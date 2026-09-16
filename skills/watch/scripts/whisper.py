#!/usr/bin/env python3
"""Transcribe a video via OpenRouter, Groq, OpenAI, or local whisper.cpp.

Strategy: extract audio (mono 16kHz mp3, tiny payload), send it to whichever
backend has a key, and fall back to a local decode when none does. Returns
segments in the same shape as transcribe.parse_vtt so the rest of the pipeline
(filter_range, format_transcript) doesn't care where the transcript came from.

WHAT EVERY BACKEND HERE MUST RETURN IS TIMES, not words. A transcript with no
seconds in it cannot be anchored, quoted or graded, so a route that can silently
produce one is refused rather than written -- see `_post_openrouter`.

Pure stdlib — no `pip install groq` or `pip install openai` needed.
"""
from __future__ import annotations

import io
import json
import math
import mimetypes
import os
import re
import shutil
import ssl
import statistics
import subprocess
import sys
import time
import urllib.error
import uuid
from pathlib import Path
from urllib.request import Request, urlopen


GROQ_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"

OPENAI_ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_MODEL = "whisper-1"

# OpenRouter fronts several transcription providers behind one key and one
# balance. It is OpenAI-shaped, so almost nothing below changes -- except the
# one thing this pipeline cannot do without.
#
# TIMESTAMPS ARE THE WHOLE CATCH, and the documentation and the wire disagree
# about them. OpenRouter's speech-to-text guide says `verbose_json` -- the
# response format carrying segment timestamps -- "is only available on
# OpenAI-compatible providers (OpenAI, Groq, Together). Other providers reject
# it with a 400."
#
# MEASURED 2026-08-19, and it did not hold. Four requests for the same 4.891s
# clip, pinned in turn to groq, deepinfra and together, all returned two
# timestamped segments and all billed 3.66825e-05 -- which is 4.891 x
# 0.0000075, DeepInfra's per-second rate, and 1/200th of Together's. So the
# `provider` block below is IGNORED on this endpoint, every request went to the
# cheapest provider, and that provider returned timestamps the documentation
# says it refuses.
#
# Two consequences, and the second is the one that matters:
#   1. Pinning a provider here is a request, not a guarantee. It is still sent,
#      because it costs nothing and will start working if routing arrives.
#   2. The only real guard is checking the RESPONSE for timestamps and refusing
#      it when they are missing -- see `_segments_from_response`. A transcript
#      with no seconds in it cannot be anchored, cited or graded, and every
#      check downstream of this file is about a second in a recording.
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/audio/transcriptions"
OPENROUTER_MODEL = "openai/whisper-large-v3"
# THE ROUTER IS FOR PROVIDERS A DIRECT BACKEND CANNOT REACH. This defaulted to
# `groq`, which is also a backend of its own here (`--whisper groq`,
# `GROQ_API_KEY`), so the default asked OpenRouter -- and OpenRouter's margin --
# to reach a provider already reachable directly. DeepInfra is not reachable
# any other way from this pipeline, and it is the provider the measurement
# above was actually billed at. Override with `WATCH_OPENROUTER_PROVIDER`.
OPENROUTER_PROVIDER = "deepinfra"
# The providers OpenRouter documents as returning `verbose_json`. Kept as the
# text of the error message and as a doc fact, not as a belief: the measurement
# above found a provider outside this list returning timestamps anyway.
OPENROUTER_TIMESTAMPED_PROVIDERS = ("openai", "groq", "together")
# The providers this repo has SEEN return them, 2026-08-19, on the clip above.
# The pin is chosen from here rather than from the doc list, and a pin outside
# BOTH is what the heads-up in `_post_openrouter` is for. Neither list is the
# check; the refusal in `_segments_from_response` is.
OPENROUTER_MEASURED_TIMESTAMPED = ("groq", "deepinfra", "together")
# Seconds of audio per request. OpenRouter's guide: "Recordings longer than
# about a minute of processing time should be split anyway, since upstream
# providers time out after 60 seconds per request." Ten minutes of audio is
# seconds of work for a batched provider and leaves a wide margin; the byte cap
# alone would allow ~50 minutes of this pipeline's 64 kbps mono mp3 in one
# request, which is exactly the request that times out.
OPENROUTER_MAX_SECONDS = 600.0

# HOW COARSE A RENDERING IS ALLOWED TO BE, which is a different question from
# whether it has timestamps at all and was learned the hard way.
#
# MEASURED 2026-08-19 through OpenRouter, same script read twice at two lengths:
#                             29.6s clip                 89.5s clip
#   openai/whisper-large-v3   8 segments, median 2.26s   17 segments, median 3.06s
#   Qwen/Qwen3-ASR-1.7B       2 segments, median 14.81s   4 segments, median 27.30s
# Both pass the refusal in `_segments_from_response`, because both carry
# timestamps. Only one of them can be anchored. A rendering whose typical
# segment is half a minute says a thing was said somewhere in a paragraph, and
# every check downstream of this file is about a second.
#
# THE DISCRIMINATOR IS THE MEDIAN SEGMENT, and the first attempt at this rule
# got it wrong. Measuring the share of the request its LONGEST segment covers
# separated the two models at 29.6s (27% against 93%) and stopped separating
# them at 89.5s (31% against 31%), because the coarse model's segments cap out
# around 27s while the request keeps growing. A share that shrinks as the file
# grows is a gate that fires on short files and sleeps on long ones -- exactly
# backwards. The median does not move with length: 2-3s against 15-27s at both.
#
# Below `COARSE_MIN_SECONDS` the rule is off. On a tail chunk that short, one
# blob costs at most half a minute of anchor precision.
COARSE_MEDIAN_SECONDS = 10.0
COARSE_MIN_SECONDS = 30.0

# HOW WORD TIMESTAMPS ARE GROUPED BACK INTO SEGMENTS, and why these three
# numbers rather than any others.
#
# MEASURED 2026-09-07, one chunk of one video, ten direct probes and two real
# runs: the SEGMENTS came back at a 6.76-9.02s median on the probes and a 30s
# median inside the runs, same payload and same headers both times, because the
# `provider` block is ignored and the request goes wherever is cheapest that
# second. The WORDS came back on every single request, 883-1,086 of them at a
# 0.20-0.24s median. One of those two arrays can be relied on.
#
# Grouped at the numbers below, the two captured fixtures in
# `tests/fixtures/openrouter/` come back as 80 segments each, median 3.76s and
# 3.65s, longest 4.00s. That is the shape whisper-large-v3 produces when the
# router happens to route well -- 2.26s and 3.06s, measured 2026-08-19 and
# recorded beside COARSE_MEDIAN_SECONDS -- and it leaves 6.3s of margin under
# the 10.0s guard.
#
# THE CAP IS THE RULE THAT ACTUALLY FIRES: of the 79 splits in the first
# fixture, 62 came from the cap, 17 from a silence and none from punctuation,
# because a redacted fixture carries no punctuation. On real text the sentence
# rule fires too, and it can only ever split a group the cap would have split
# later. A cap of 8.0s also passes the guard, at a median of 6.75s and 7.51s on
# the two captures -- 2.49s of margin on the tighter one, under a threshold that
# one rounding change would move across. 4.0s leaves 6.24s and 6.36s.
WORD_SEGMENT_MAX_SECONDS = 4.0
# A silence at least this long ends a segment. The typical word on this route
# spans 0.20s, so half a second of nothing is a clause boundary rather than a
# pause inside a phrase.
WORD_SEGMENT_GAP_SECONDS = 0.5
# A sentence end does not split a segment shorter than this. Without it "Yes."
# becomes its own segment and puts a citable anchor on an interjection.
WORD_SEGMENT_MIN_SECONDS = 1.0

# HOW MUCH OF THE SERVED RENDERING THE WORDS MUST COVER before a rebuild may
# replace it. TWO numbers, because one statistic cannot express both failures.
#
# RE-DERIVED 2026-09-10, second time in a day. The number this replaces summed
# every word-free second in the chunk and compared the total to one slack. A sum
# cannot separate the two populations it is asked to separate, measured rather
# than argued: `tests/fixtures/openrouter/coarse-reconstructed.json` with 0.80s
# of every 1.60s of its words deleted loses 562 of 1,085 words and sums to
# 14.13s -- comfortably under the 20.0s slack, so the chunk was rebuilt and
# written. Meanwhile a show whose theme runs a few seconds longer than the one
# in the fixtures summed past the same slack and failed the whole run.
#
# THE TWO SHAPES ARE DIFFERENT SHAPES, so they get different terms:
#
#   a music bed or a silent intro  ONE long word-free run and nothing else
#   a thinned or truncated decode  many runs, or a big one, against the claim
#
# THE SHARE IS TAKEN OUTSIDE THE LONGEST RUN, and that is forced rather than
# tidy. Read as a plain fraction of the claim, the healthy responses are the
# WORST ones on the page -- `music-intro.json` leaves 14.62% of its claim
# word-free and `head-span.json` 90.31%, both legitimately, both the same music
# glyph -- while the thinned capture above leaves 1.11%. The ordering inverts.
# The longest run is what the first term already judges, so the second term
# reads what is left after it.
#
# MEASURED over every captured response on this machine holding BOTH arrays --
# the four in `tests/fixtures/openrouter/` and the three the 2026-09-08 corpus
# build kept in `~/rung4-corpus/_driver/diagnose/` -- and over the defect shapes
# built by deleting words from those same captures:
#
#                                          longest run   share outside it
#   speech throughout (4 responses)        0.02-0.22s    0.00%
#   the video B music glyph (3)            14.36s        0.00%, 0.12%, 0.55%
#   ---------------------------------------------------------------------
#   words start a minute late              59.50s        0.00%
#   words cover only 200-300s of 300s     199.50s        0.00%
#   one word at the tail of a 300s chunk  287.60s        3.70%
#   `fine.json` thinned 0.8s of 1.6s        1.70s        4.12%
#   `coarse-reconstructed` thinned so       1.70s        4.38%
#   15 constructed holes of 2.0s            1.00s        4.67%
#
# Each threshold is the GEOMETRIC midpoint of its band -- both statistics are
# ratios of durations, so equal multiplicative margin is the honest middle --
# rounded DOWN, because the healthy side of each band rests on far fewer
# artifacts than the defect side. Run: midpoint of 14.36 and 59.50 is 29.23s,
# down to 25.0s, which is 1.74x the largest healthy run and 2.38x under the
# smallest defect. Share: midpoint of 0.55% and 4.12% is 1.51%, down to 1.5%,
# 2.7x either way.
#
# WHAT MOVES: a single word-free run between 20s and 25s used to fail a whole
# video and now passes, which is the false refusal the sum created. A capture
# with half its words thinned away now refuses.
#
# WHAT STILL PASSES AND SHOULD NOT: word loss that INTERLEAVES rather than
# cuts. Deleting every second word of a real capture leaves the survivors spread
# across the whole chunk, 0.66-1.24% outside the longest run, inside the healthy
# range. No statistic over time coverage can see it, because the time is still
# covered; catching it needs a density rule this file does not have.
#
# NOT DERIVED FROM THE FULL 2026-09-08 CORPUS: that build kept renderings, not
# raw responses, so 27 of its 30 videos retain no word array to measure. The
# healthy side of the run band is ONE audio source seen three times. Widening
# the derivation needs a capture pass that keeps raw responses AND defective
# word arrays, not only healthy ones (DEFER:yt_notes-66wk).
#
# THE SHARE IS SCALE-RELATIVE AND STAYS THAT WAY, ruled 2026-09-10 on the
# measurement rather than on the shape of the rule. `rest / claimed` forgives
# 4.5s in a five-minute chunk and 9.0s in a ten-minute one, and
# OPENROUTER_MAX_SECONDS is 600.0, so the shipped route runs at the looser end.
# Capping the tolerance in seconds as well was measured and dropped: over 522
# defect rows built by thinning and punching the two real captures that reach
# this branch, the worst shape escaping all three terms loses 5.61% of its words
# (82 of 1,462) with 1.09s outside its longest run, so a cap has to fall UNDER
# 1.09s to move the worst escape at all -- and those same captures measure 0.56s
# and 0.61s there when healthy. A cap tight enough to bite would sit 1.2x above
# a real capture, on a healthy side that is one audio source. The word-count
# term below already bounds that escape at its declared floor. What changed
# instead is the tests: both pins now run at 600s, the production chunk length,
# where they used to run at half of it.
WORD_COVERAGE_RUN_SECONDS = 25.0
WORD_COVERAGE_HOLE_SHARE = 0.015

# THE THIRD TERM READS WORDS, NOT SECONDS, and it exists because both terms
# above read TIME. A decode that THINS rather than truncates keeps the time
# covered while the words go: `coarse-reconstructed.json` with 0.60s deleted
# out of every 1.60s loses 423 of its 1,085 words and leaves a longest run of
# 1.45s and 1.25% of the claim outside it -- inside both thresholds, written
# without a word of complaint. Moving that window one step (0.80s of 1.60s) is
# the case the pair DOES catch, which is how three rounds of tuning each
# shipped a defect one window away from its own regression test.
#
# A rendering too coarse to anchor still says how many words it heard, in its
# own text. So the words are graded against that count rather than against the
# clock: fewer than 0.95 word stamps per word of served text and the rebuild is
# refused.
#
# SAY PLAINLY WHAT THIS IS. It is a FILTER, chosen for what it costs an
# attacker, and it is NOT a separator. Measured 2026-09-10 over 23 candidate
# statistics and 828 constructed defect rows, NO statistic over these two
# arrays separates a healthy capture from a thinned one on the artifacts this
# machine holds -- every candidate's band touches, and three of the seven
# captures share one audio source, so the healthy side was never a population
# (`.lane-briefs/2026-09-10-design-coverage-statistic.md`). A later session
# must not read 0.95 as a proven boundary.
#
# THE DECLARED FLOOR IS PART OF THE DESIGN, not a defect awaiting another
# round. The ratio of a thinned array is `baseline x (1 - loss)`, so this term
# is blind to every loss under `1 - 0.95/baseline`. On the real capture that
# reaches this branch (1,462 words of served text, 502.0s claimed, baseline
# 1.0083) that is 5.78% OF A CHUNK'S WORDS -- 85 words and 29.0s of claimed
# speech in a ten-minute chunk. Underneath that, this term sees nothing. What
# ships without it has a floor of 32.1%, 469 words.
#
# 0.95 IS THE LAST SETTING WHOSE FALSE-POSITIVE MARGIN STILL EXCEEDS ITS OWN
# FLOOR: `music-intro.json`, a real capture, clears it by 5.72% against a floor
# of 5.78%. 0.90 buys an 11.60% margin for a 10.7% floor; past 1.004 the music
# intro is refused outright. Every capture on this machine is accepted, so the
# false-positive cost measured here is zero chunks.
#
# VALID ONLY WHILE THE SERVED TEXT IS COMPLETE. It assumes a provider whose
# segment TIMES are too coarse to anchor still returns whole text. Four real
# captures support that (1.0000-1.0091); `coarse-reconstructed.json` is a
# RECONSTRUCTION whose placeholder text runs 1.5069, and on that baseline the
# floor is 37% -- 401 words. That is the reason this is a filter and not a
# separator, and closing it needs a capture pass that saves the served TEXT and
# not only the word array (DEFER:yt_notes-66wk).
WORD_COVERAGE_MIN_WORD_RATIO = 0.95

# Both Groq's free tier and OpenAI whisper-1 cap uploads at 25 MB. We target a
# margin under that so multipart framing overhead never pushes a chunk over.
MAX_UPLOAD_BYTES = 24 * 1024 * 1024

# HOW MUCH AUDIO IS HANDED TO ONE LOCAL DECODE, in seconds, including the
# context on either side. This dial names the DECODED span rather than the kept
# span because the decoded span is what degenerates.
#
# A long decode collapses. On one two-hour recording a single whole-file pass
# collapsed at 21:49 and repeated one sentence 6,434 times to the end: 83% of
# the file lost, and the last line before the collapse was already mis-heard.
# Fifteen-minute windows over the same audio bounded the damage without removing
# it -- seven of nine clean, one looped, one lost thirteen minutes -- and an
# offset re-run over the lost stretch collapsed again at a different second.
# Three four-minute windows over that same stretch came back clean. So the
# failure is a property of the decode, not of the audio, and its probability
# scales with the window.
#
# Four minutes is not the longest span ever measured clean -- most of the
# fifteen-minute windows were fine. It is the longest span that has not yet been
# measured degenerating, which is a weaker and more honest claim, and the reason
# the number is a dial rather than a constant.
#
# Set WATCH_DECODE_WINDOW_SECONDS to 0 to decode in one pass again. That is the
# configuration the paragraph above describes losing 83% of a file.
DECODE_WINDOW_SECONDS = 240.0
# Context decoded on EACH side of a window and then discarded. A sentence
# straddling a boundary is decoded whole by the window that keeps it, at a cost
# of 2 x overlap of extra decoding per window. It is not free of edge cases:
# see `drop_seam_repeats` for the sentence two windows render on opposite sides
# of a boundary.
DECODE_OVERLAP_SECONDS = 30.0
# How far either side of a boundary two independent decodes of the same sentence
# may be assumed to land. Used only at the join; see `drop_seam_repeats`.
SEAM_SECONDS = 2.0


def plan_chunks(
    total_seconds: float,
    total_bytes: int,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> list[tuple[float, float]]:
    """Split a duration into contiguous (offset, duration) chunks under max_bytes.

    Size scales linearly with duration (constant-bitrate mono mp3), so an even
    time split yields evenly-sized chunks. Returns a single full-length chunk
    when the audio already fits.
    """
    if total_bytes <= max_bytes or total_seconds <= 0:
        return [(0.0, total_seconds)]

    n = math.ceil(total_bytes / max_bytes)
    chunk = total_seconds / n
    # The same seam rule as `plan_by_seconds`: a duration is the distance to the
    # next rounded offset, so consecutive chunks meet exactly. Rounding the two
    # numbers independently left a sub-second hole at every join.
    offsets = [round(i * chunk, 3) for i in range(n)]
    return [
        (offsets[i],
         round((offsets[i + 1] if i + 1 < n else total_seconds) - offsets[i], 3))
        for i in range(n)
    ]


def plan_by_seconds(total_seconds: float,
                    max_seconds: float) -> list[tuple[float, float]]:
    """Split a duration into contiguous (offset, duration) chunks of at most
    `max_seconds`, evenly sized.

    Separate from `plan_chunks` because the two limits are different animals: a
    byte cap is about what an upload accepts, a second cap is about how long a
    provider will work before it gives up. This pipeline's audio is small enough
    that the byte cap alone would hand a provider fifty minutes in one request.

    Evenly sized rather than max-sized: eight chunks of nine minutes retry
    better than seven of ten plus one of two, and the tail chunk is the one most
    likely to be a fragment of a sentence.
    """
    if max_seconds <= 0:
        raise ValueError(f"a chunk of {max_seconds}s holds no audio")
    if total_seconds <= max_seconds or total_seconds <= 0:
        return [(0.0, total_seconds)]

    n = math.ceil(total_seconds / max_seconds)
    chunk = total_seconds / n
    # EACH DURATION IS THE GAP TO THE NEXT ROUNDED OFFSET, not the unrounded
    # chunk length. Rounding offset and duration independently leaves a
    # millisecond gap or overlap at every seam -- a few words a file, silently.
    offsets = [round(i * chunk, 3) for i in range(n)]
    return [
        (offsets[i],
         round((offsets[i + 1] if i + 1 < n else total_seconds) - offsets[i], 3))
        for i in range(n)
    ]


def plan_windows(
    total_seconds: float,
    window_seconds: float = DECODE_WINDOW_SECONDS,
    overlap_seconds: float = DECODE_OVERLAP_SECONDS,
) -> list[tuple[float, float, float, float]]:
    """Split a duration into overlapping decode windows.

    Returns (offset, duration, keep_from, keep_to) per window. The first two are
    the audio handed to the decoder; the last two are the span of SOURCE time
    whose segments are kept. The kept SPANS are contiguous and tile the file
    exactly once, so no stretch of audio is claimed by two windows.

    The overlap is symmetric: a window is decoded with `overlap_seconds` of
    context on each side of the span it keeps, EXCEPT where that would run off
    the start or end of the file, where it is clamped. So a sentence crossing a
    boundary is heard whole by the window that keeps it rather than cut in half
    by both.

    A tiling of spans is not by itself a guarantee about sentences; see
    `drop_seam_repeats` for what happens to a sentence rendered on both sides of
    a boundary by two independent decodes.

    `window_seconds <= 0` disables windowing and returns one whole-file window.
    A negative window or overlap, and an overlap of half the window or more,
    are misconfigurations rather than preferences, and raise: silently reverting
    to a whole-file decode is the failure this exists to remove.
    """
    if window_seconds < 0 or overlap_seconds < 0:
        raise ValueError(
            f"window {window_seconds}s and overlap {overlap_seconds}s must not "
            f"be negative")
    if window_seconds <= 0 or total_seconds <= window_seconds:
        return [(0.0, total_seconds, 0.0, total_seconds)]

    kept = window_seconds - 2 * overlap_seconds
    if kept <= 0:
        raise ValueError(
            f"overlap {overlap_seconds}s x2 leaves nothing of a {window_seconds}s "
            f"window to keep")

    out: list[tuple[float, float, float, float]] = []
    keep_from = 0.0
    while keep_from < total_seconds:
        keep_to = min(keep_from + kept, total_seconds)
        offset = max(0.0, keep_from - overlap_seconds)
        end = min(total_seconds, keep_to + overlap_seconds)
        out.append((round(offset, 3), round(end - offset, 3),
                    round(keep_from, 3), round(keep_to, 3)))
        keep_from = keep_to
    return out


def trim_to_keep(
    segments: list[dict],
    keep_from: float,
    keep_to: float,
    last: bool,
) -> list[dict]:
    """Drop the segments a window decoded only as context.

    Kept by segment START, so a SPAN of source time is claimed by exactly one
    window even when a segment runs past the boundary. The final window keeps
    everything after its start: a decoder may place a segment a little past the
    duration ffprobe reported, and the tail of a file is not a thing to discard
    on a rounding.

    A span tiling is not a sentence tiling. Each window is an INDEPENDENT decode,
    so one sentence gets its own timestamp from each of the two windows that
    heard it, and those two timestamps can straddle the boundary in opposite
    directions -- in which case a strict span test drops it from both. That is
    why the caller passes a lower bound slack of `SEAM_SECONDS` and then removes
    the duplicates by text; see `drop_seam_repeats`. A duplicated sentence is
    visible in the transcript. A dropped one is not.
    """
    return [s for s in segments
            if s["start"] >= keep_from and (last or s["start"] < keep_to)]


# Deliberately a second copy of what watch-quality's transcript_align does with
# the same words. This script ships inside the skill and runs on the standard
# library alone, so it cannot import the grading package -- a plugin that needed
# a pip install to transcribe a video would not be a plugin.
RE_SEAM_WORD = re.compile(r"[a-z0-9']+")


def _seam_key(text: str) -> str:
    return " ".join(RE_SEAM_WORD.findall(text.lower()))


def longest_identical_run(segments: list[dict]) -> int:
    """The longest run of consecutive segments saying the same thing.

    A decode that has stopped listening emits one line over and over. On a clean
    decode of a two-hour recording the longest such run was 3; on the pass that
    collapsed it was 6,434.
    """
    best = run = 0
    previous = None
    for seg in segments:
        key = _seam_key(seg["text"])
        run = run + 1 if key and key == previous else 1
        previous = key
        best = max(best, run)
    return best


# A run this long is a loop, not a speaker repeating themselves. Windowing turned
# a 6,434-run that ate 83% of a file into an 8-run inside one 4-minute window --
# bounded, but still there, and still unquotable.
LOOP_RUN = 4


def drop_seam_repeats(kept: list[dict], incoming: list[dict],
                      seam: float = SEAM_SECONDS) -> list[dict]:
    """Remove the sentences at a window's head that the previous window has.

    The window before this one kept everything up to the boundary; this one is
    allowed to reach back `seam` seconds past it so a sentence cannot fall
    between the two. Anything it reaches back and finds already there is
    dropped, matched on the words rather than the clock, because the two
    renderings of one sentence rarely carry the same timestamp -- that mismatch
    is the whole reason the slack exists.

    Only the tail of `kept` is compared: a sentence legitimately repeated an
    hour earlier is not a seam artefact, and matching against the whole
    transcript would silently delete real repetition.
    """
    if not kept or not incoming:
        return incoming
    edge = max(s["end"] for s in kept)
    recent = {_seam_key(s["text"]) for s in kept if s["end"] >= edge - 4 * seam}
    return [s for s in incoming
            if not (s["start"] <= edge + seam and _seam_key(s["text"]) in recent)]


def _read_config_value(name: str) -> str | None:
    """Read a config value from the environment, then the watch dotenv files."""
    def _from_env(env_name: str) -> str | None:
        value = os.environ.get(env_name)
        return value.strip() if value else None

    def _from_dotenv(path: Path, env_name: str) -> str | None:
        if not path.exists():
            return None
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() != env_name:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                    value = value[1:-1]
                return value or None
        except OSError:
            return None
        return None

    dotenv_paths = [
        Path.home() / ".config" / "watch" / ".env",
        Path.cwd() / ".env",
    ]

    value = _from_env(name)
    if value:
        return value
    for candidate in dotenv_paths:
        value = _from_dotenv(candidate, name)
        if value:
            return value
    return None


# Backends that are only used when SOMEBODY ASKS FOR THEM BY NAME.
#
# OpenRouter used to sit first in the automatic order, on the argument that
# setting its key is already a decision to pay. That argument is wrong in one
# direction that matters: this endpoint ignores the provider pin and routes to
# whichever provider is cheapest that minute (measured 2026-08-19, see the note
# above OPENROUTER_ENDPOINT). A backend whose actual provider changes between
# runs must not be the one a run picks up on its own, because the transcript is
# the oracle every downstream check is measured against.
#
# So it is a flag now, not a default. `--whisper openrouter` gets it; nothing
# else does, however many keys are lying around in the config.
BY_REQUEST_ONLY = ("openrouter",)


def load_api_key(preferred: str | None = None) -> tuple[str, str] | tuple[None, None]:
    """Return (backend, api_key). Groq, then OpenAI, then local whisper.cpp.

    If `preferred` is "openrouter", "groq", "openai", or "local", only that
    backend is considered. For the "local" backend the second tuple element is
    the path to the whisper.cpp binary (WHISPER_CPP_BIN) rather than an API key.

    OpenRouter is reachable ONLY by asking for it: see BY_REQUEST_ONLY. Having
    no key at all remains the normal state, and the local backend remains the
    one that needs nothing.
    """
    candidates = (
        ("OPENROUTER_API_KEY", "openrouter"),
        ("GROQ_API_KEY", "groq"),
        ("OPENAI_API_KEY", "openai"),
        ("WHISPER_CPP_BIN", "local"),
    )
    # A NAME OUTSIDE THE FOUR IS A TYPO, NOT AN ABSENT CREDENTIAL. Filtering an
    # unknown name left an empty candidate list, so `--backend Local` on a fully
    # configured machine reported "No Whisper backend available. Set
    # GROQ_API_KEY…" and sent the user to fix credentials they already have
    # (properties I33). `watch.py` constrains its own flag with argparse
    # choices; this module's CLI does not, and this is the shared door.
    if preferred is not None and preferred not in [c[1] for c in candidates]:
        raise SystemExit(
            f"Unknown whisper backend: {preferred!r}. Choose one of "
            f"{', '.join(c[1] for c in candidates)}.")
    if preferred is None:
        candidates = tuple(c for c in candidates if c[1] not in BY_REQUEST_ONLY)
    else:
        candidates = tuple(c for c in candidates if c[1] == preferred)

    for key_name, backend in candidates:
        value = _read_config_value(key_name)
        if value:
            return backend, value

    return None, None


def extract_audio(video_path: str, out_path: Path) -> Path:
    """Extract mono 16kHz 64kbps mp3 — ~480 kB/min, fits any Whisper limit."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(Path(video_path).resolve()),
        "-vn",
        "-acodec", "libmp3lame",
        "-ar", "16000",
        "-ac", "1",
        "-b:a", "64k",
        str(out_path.resolve()),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg audio extraction failed: {result.stderr.strip()}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise SystemExit("ffmpeg produced no audio — video may have no audio track")
    return out_path


def audio_duration(audio_path: Path) -> float:
    """Return the duration of an audio file in seconds via ffprobe."""
    if shutil.which("ffprobe") is None:
        raise SystemExit("ffprobe is not installed. Install with: brew install ffmpeg")

    result = subprocess.run(
        [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            str(audio_path.resolve()),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffprobe failed: {result.stderr.strip()}")
    fmt = json.loads(result.stdout or "{}").get("format", {})
    return float(fmt.get("duration") or 0.0)


def split_audio(
    full_audio: Path,
    work_dir: Path,
    plan: list[tuple[float, float]],
) -> list[tuple[Path, float]]:
    """Slice full_audio into per-plan chunk files, returning (path, offset) pairs.

    Uses stream copy (`-c copy`) so there is no re-encode and no quality loss;
    mp3 frame boundaries are close enough for transcription's purposes.
    """
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    work_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[tuple[Path, float]] = []
    for index, (offset, duration) in enumerate(plan):
        out_path = work_dir / f"chunk_{index:03d}.mp3"
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-ss", f"{offset:.3f}",
            "-i", str(full_audio.resolve()),
            "-t", f"{duration:.3f}",
            "-c", "copy",
            str(out_path.resolve()),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            raise SystemExit(
                f"ffmpeg failed to split audio chunk {index + 1}: {result.stderr.strip()}"
            )
        chunks.append((out_path, offset))
    return chunks


def _build_multipart(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    """Assemble a multipart/form-data body the Whisper APIs accept.

    Whisper's multipart upload is small and predictable — doing it by hand
    keeps us on pure stdlib instead of pulling requests/groq/openai SDKs.
    """
    boundary = f"----WatchBoundary{uuid.uuid4().hex}"
    eol = b"\r\n"
    buf = io.BytesIO()

    for name, value in fields.items():
        buf.write(f"--{boundary}".encode()); buf.write(eol)
        buf.write(f'Content-Disposition: form-data; name="{name}"'.encode()); buf.write(eol)
        buf.write(eol)
        buf.write(str(value).encode()); buf.write(eol)

    mimetype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    buf.write(f"--{boundary}".encode()); buf.write(eol)
    buf.write(
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"'.encode()
    )
    buf.write(eol)
    buf.write(f"Content-Type: {mimetype}".encode()); buf.write(eol)
    buf.write(eol)
    buf.write(file_path.read_bytes())
    buf.write(eol)
    buf.write(f"--{boundary}--".encode()); buf.write(eol)

    return buf.getvalue(), boundary


MAX_ATTEMPTS = 4       # initial + 3 retries
MAX_429_RETRIES = 2
RETRY_BASE_DELAY = 2.0


def _post_whisper(endpoint: str, api_key: str, model: str, audio_path: Path) -> dict:
    fields = {
        "model": model,
        "response_format": "verbose_json",
        "temperature": "0",
    }
    body, boundary = _build_multipart(fields, audio_path)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        # Groq sits behind Cloudflare — the default `Python-urllib/3.x` UA
        # trips WAF rule 1010 (403) before auth even runs. Any non-default
        # UA clears it; we identify honestly.
        "User-Agent": "watch-skill/1.0 (+claude-code; python-urllib)",
    }

    context = ssl.create_default_context()
    rate_limit_hits = 0
    last_exc: Exception | None = None
    last_detail = ""

    for attempt in range(MAX_ATTEMPTS):
        request = Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=300, context=context) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = _read_error_body(exc)
            last_exc, last_detail = exc, detail

            # 4xx other than 429 are client errors — no retry will fix them.
            if 400 <= exc.code < 500 and exc.code != 429:
                raise SystemExit(f"Whisper request failed: {exc}{detail}")

            if exc.code == 429:
                rate_limit_hits += 1
                if rate_limit_hits >= MAX_429_RETRIES:
                    raise SystemExit(f"Whisper request failed: {exc}{detail}")
                delay = _retry_after(exc) or RETRY_BASE_DELAY * (2 ** attempt) + 1
            else:
                delay = RETRY_BASE_DELAY * (2 ** attempt)

            if attempt < MAX_ATTEMPTS - 1:
                print(
                    f"[watch] whisper HTTP {exc.code} — retrying in {delay:.1f}s "
                    f"(attempt {attempt + 2}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            continue
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError) as exc:
            last_exc, last_detail = exc, ""
            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY * (attempt + 1)
                print(
                    f"[watch] whisper network error ({type(exc).__name__}: {exc}) — "
                    f"retrying in {delay:.1f}s (attempt {attempt + 2}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            continue

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Whisper returned non-JSON response: {exc}: {payload[:200]}")

    raise SystemExit(
        f"Whisper request failed after {MAX_ATTEMPTS} attempts: {last_exc}{last_detail}"
    )


def _post_openrouter(api_key: str, model: str, audio_path: Path,
                     provider: str | None = None,
                     language: str | None = None) -> dict:
    """One transcription request to OpenRouter, on the base64 JSON path.

    THE JSON PATH RATHER THAN MULTIPART, because only the JSON body documents a
    `provider` object. Base64 costs a third more bytes on the wire -- about
    1.5 MB extra on a ten-minute chunk of this pipeline's 64 kbps mono mp3.

    The `provider` block was MEASURED HAVING NO EFFECT on this endpoint (see the
    note beside OPENROUTER_PROVIDER: three different pins, one provider's
    price). It is sent anyway because it costs nothing and documents the intent,
    and because routing arriving later should not need a code change. What
    actually protects the run is the caller asking `_segments_from_response` to
    refuse a response with no timestamps in it.
    """
    import base64

    provider = provider or OPENROUTER_PROVIDER
    if (provider not in OPENROUTER_TIMESTAMPED_PROVIDERS
            and provider not in OPENROUTER_MEASURED_TIMESTAMPED):
        print(
            f"[watch] WATCH_OPENROUTER_PROVIDER={provider!r} is outside both "
            f"the providers OpenRouter documents as returning segment "
            f"timestamps ({', '.join(OPENROUTER_TIMESTAMPED_PROVIDERS)}) and "
            f"the ones measured returning them "
            f"({', '.join(OPENROUTER_MEASURED_TIMESTAMPED)}). This is a "
            f"heads-up and not a refusal; a response without timestamps is "
            f"what gets refused.",
            file=sys.stderr,
        )

    payload = {
        "model": model,
        "input_audio": {
            "data": base64.b64encode(audio_path.read_bytes()).decode("ascii"),
            "format": audio_path.suffix.lstrip(".").lower() or "mp3",
        },
        "response_format": "verbose_json",
        # ASK FOR WORDS AS WELL AS SEGMENTS. Measured 2026-09-07: without this
        # key the response carries zero words, so `_segments_from_response` has
        # nothing to fall back to when the provider that served the request
        # returns 30-second segments. With it, every response measured carried
        # one word array at a 0.20s median, regardless of provider.
        "timestamp_granularities": ["segment", "word"],
        "temperature": 0,
        "provider": {"only": [provider], "order": [provider],
                     "allow_fallbacks": False},
    }
    if language:
        payload["language"] = language
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "watch-skill/1.0 (+claude-code; python-urllib)",
        # Identifies the caller on OpenRouter's dashboards. Optional there,
        # useful when a bill needs explaining.
        "X-Title": "watch-skill",
    }

    context = ssl.create_default_context()
    last_exc: Exception | None = None
    last_detail = ""
    for attempt in range(MAX_ATTEMPTS):
        request = Request(OPENROUTER_ENDPOINT, data=body, headers=headers,
                          method="POST")
        try:
            with urlopen(request, timeout=300, context=context) as response:
                payload_text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = _read_error_body(exc)
            last_exc, last_detail = exc, detail
            if exc.code == 400:
                raise SystemExit(
                    f"OpenRouter rejected the request: {exc}{detail}\n"
                    f"If it names response_format, the provider {provider!r} "
                    f"does not return segment timestamps. Set "
                    f"WATCH_OPENROUTER_PROVIDER to one of "
                    f"{', '.join(OPENROUTER_TIMESTAMPED_PROVIDERS)}."
                )
            if 400 <= exc.code < 500 and exc.code != 429:
                raise SystemExit(f"OpenRouter request failed: {exc}{detail}")
            delay = (_retry_after(exc) or RETRY_BASE_DELAY * (2 ** attempt))
        except (urllib.error.URLError, TimeoutError, ConnectionResetError,
                OSError) as exc:
            last_exc, last_detail = exc, ""
            delay = RETRY_BASE_DELAY * (attempt + 1)
        else:
            try:
                return json.loads(payload_text)
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    f"OpenRouter returned non-JSON: {exc}: {payload_text[:200]}")

        if attempt < MAX_ATTEMPTS - 1:
            print(f"[watch] openrouter error ({last_exc}) — retrying in "
                  f"{delay:.1f}s (attempt {attempt + 2}/{MAX_ATTEMPTS})",
                  file=sys.stderr)
            time.sleep(delay)

    raise SystemExit(
        f"OpenRouter request failed after {MAX_ATTEMPTS} attempts: "
        f"{last_exc}{last_detail}")


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read()
    except Exception:
        return ""
    if not body:
        return ""
    try:
        return f" — {body.decode('utf-8', errors='replace')[:400]}"
    except Exception:
        return ""


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    header = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else None
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def shift_segments(segments: list[dict], offset_seconds: float) -> list[dict]:
    """Return a copy of segments with start/end shifted by offset_seconds.

    Each chunk is transcribed in isolation, so Whisper returns 0-based timestamps
    per chunk; shifting by the chunk's offset stitches them into source time.
    """
    if offset_seconds == 0:
        return segments
    return [
        {
            "start": round(seg["start"] + offset_seconds, 2),
            "end": round(seg["end"] + offset_seconds, 2),
            "text": seg["text"],
        }
        for seg in segments
    ]


def _segments_from_response(data: dict, allow_untimed: bool = True,
                            offset_seconds: float = 0.0) -> list[dict]:
    """Convert Whisper verbose_json into our {start, end, text} segment format.

    `allow_untimed` decides what happens when the response carries text but no
    segments. The old behaviour -- one segment spanning 0.0 to 0.0 holding the
    whole transcript -- keeps a caller working, and it also produces a file that
    every downstream check will grade as if it had times. On a route where an
    untimed response means the request was routed to the wrong provider, that is
    a silent wrong answer, so that route asks for it to be refused instead.

    `offset_seconds` is where this chunk's audio starts in the video, and it is
    used for one thing: the clock ranges a coverage refusal names. Every time in
    a response is 0-based inside the chunk that was uploaded, so a hole at 1:40
    of the second ten-minute request printed bare reads as 1:40 of the video and
    sends a reader 600 seconds away from the audio that went. `transcribe_chunks`
    already holds the number -- it is what it shifts the chunk's own segments by
    -- so it is handed down rather than guessed at. It stays 0.0 for a request
    that carries the whole file, where the chunk IS the video.
    """
    out: list[dict] = []
    for seg in data.get("segments") or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "start": round(float(seg.get("start") or 0.0), 2),
            "end": round(float(seg.get("end") or 0.0), 2),
            "text": text,
        })

    # A COARSE RENDERING AND A MISSING ONE ARE THE SAME PROBLEM HERE, and the
    # words answer both. `check_granularity` refuses a coarse rendering a few
    # frames up the stack, and on the chunked path that refusal drops the whole
    # chunk: one real run kept five chunks of six and wrote a transcript with
    # 9:28-14:12 missing from it while reporting a segment total as if nothing
    # had gone. Rebuilding from the words keeps the audio.
    #
    # The condition is the granularity guard's own, so a rendering that would
    # PASS that guard is never regrouped -- a provider's own sentence
    # boundaries are better than any rule here, and 31 segments at a 6.76s
    # median need no help.
    median, _longest, covered = segment_shape(out)
    if (data.get("words")
            and (not out or (covered > COARSE_MIN_SECONDS
                             and median > COARSE_MEDIAN_SECONDS))):
        regrouped = segments_from_words(data["words"])
        if regrouped:
            # INSIDE THE RENDERING, NOT AT ITS ENDS. Both endpoint forms this
            # guard has worn read two numbers off each rendering, and two
            # numbers cannot say whether the middle is there: a word array
            # covering 0-30s and 270-300s of a 300s chunk agrees with the
            # segments at both ends while 240s of it hold no word, which is the
            # shape that cost 83% of a two-hour file on 2026-08-18. Comparing
            # the tail alone additionally accepted a word array missing its
            # HEAD -- one word at 299.6s replacing a 300s chunk, measured
            # 2026-09-10. What the guard wants is interior coverage: every
            # stretch the served rendering CLAIMS as speech holds a word.
            #
            # Stated that way it also keeps the case the tail form was written
            # for. video B chunk 1 opens on 14.88s of the show's music
            # glyph and its first word lands at 14.86: 14.92s uncovered in all,
            # 14.36s of it that one intro, inside a slack derived from that
            # response and its two siblings
            # (`tests/fixtures/openrouter/music-intro.json`).
            #
            # `out` may be empty here: a response can carry words and no
            # segments at all, and then there is nothing claiming speech to
            # fall short of. All three terms below are then structurally unable
            # to fire -- no claim, so no holes and no denominator -- and that is
            # the reading, not an oversight. `words-only.json` is a real capture
            # taking this door; the inertness is pinned term by term by
            # `TestTheNoSegmentEntryIsInertOnPurpose` so a later session cannot
            # arrive at it by accident. A segment with blank text claims no
            # speech either, so it never reaches this check -- which is the one
            # way a response can still hide audio from it, pinned by
            # `test_a_blank_trailing_segment_claims_no_speech`.
            holes = word_coverage_holes(out, regrouped)
            # NEITHER RENDERING IS USABLE when either term fires, so this
            # refuses instead of choosing. The segments are too coarse to
            # anchor and the words do not reach audio the segments say is
            # speech; silently taking the shorter one is the exact failure this
            # file exists to prevent, moved inside a chunk where no gate can
            # see it.
            worst = max(holes, key=lambda hole: hole[1] - hole[0],
                        default=(0.0, 0.0))
            # IN VIDEO TIME, because that is the only clock a reader can check.
            # The holes are measured in the chunk's own frame; `offset_seconds`
            # is where that frame starts.
            where = _format_span(worst[0] + offset_seconds,
                                 worst[1] + offset_seconds)
            run = worst[1] - worst[0]
            claimed = _claimed_seconds(out)
            rest = sum(end - start for start, end in holes) - run
            share = (rest / claimed) if claimed else 0.0
            if run > WORD_COVERAGE_RUN_SECONDS:
                raise SystemExit(
                    f"the response's word timestamps leave {run:.0f}s of "
                    f"its own segments with no word in them, the longest run "
                    f"{where}, so rebuilding from them would "
                    f"drop that speech while still reporting a segment count. "
                    f"The segments are too coarse to anchor (a typical "
                    f"{median:.0f}s), so this chunk is refused rather than "
                    f"written.")
            if share > WORD_COVERAGE_HOLE_SHARE:
                raise SystemExit(
                    f"the response's word timestamps leave {share:.1%} of the "
                    f"seconds its own segments call speech with no word in "
                    f"them, {len(holes) - 1} runs beside the longest at "
                    f"{where}, so rebuilding from them would "
                    f"drop that speech while still reporting a segment count. "
                    f"The segments are too coarse to anchor (a typical "
                    f"{median:.0f}s), so this chunk is refused rather than "
                    f"written.")
            # AND THE SAME QUESTION ASKED IN WORDS. Both terms above read
            # time, and a decode that thins rather than truncates keeps the
            # time covered while the words go. A filter with a declared floor,
            # not a separator -- the reasoning is beside
            # WORD_COVERAGE_MIN_WORD_RATIO. `served_words` is 0 when the
            # response carried no segments at all, and then there is no count
            # to grade against.
            #
            # COUNT WHAT THE REBUILD WRITES, NOT WHAT THE ARRAY CARRIES.
            # `segments_from_words` drops an entry with no text or no usable
            # times on purpose, so one malformed word never costs the chunk it
            # sits in. Counting `data["words"]` here read those dropped entries
            # as delivered speech: 1,200 entries against 1,260 words of served
            # text clears this line while 150 of them carry no text and only
            # 1,050 words reach the transcript, each dropped word's second
            # still covered by its neighbours so no time term fires either.
            #
            # AND COUNT IT OVER THE SAME SECONDS THE DENOMINATOR DESCRIBES.
            # The served text speaks for the stretches its segments claim and
            # nothing else, so a word stamped outside them answers for nothing
            # here: 260 words after a 300s claim lifted 1,000 real ones to
            # 1,260 against 1,260 (yt_notes-3y4c).
            served_words = sum(len(seg["text"].split()) for seg in out)
            rebuilt_words = words_inside_claim(out, usable_words(data["words"]))
            if served_words and rebuilt_words < (
                    WORD_COVERAGE_MIN_WORD_RATIO * served_words):
                raise SystemExit(
                    f"rebuilding the response's word timestamps would write "
                    f"{rebuilt_words} words inside the seconds its segments "
                    f"call speech, for a rendering whose own text "
                    f"holds {served_words} words, so it would drop speech the "
                    f"response itself transcribed while still reporting a "
                    f"segment count. The segments are too coarse to anchor (a "
                    f"typical {median:.0f}s), so this chunk is refused rather "
                    f"than written.")
            print(f"[watch] the response carried {len(out)} segment(s) at a "
                  f"typical {median:.0f}s, which cannot be anchored — "
                  f"rebuilding {len(regrouped)} segments from "
                  f"{len(data['words'])} word timestamps", file=sys.stderr)
            return regrouped

    if not out:
        full = (data.get("text") or "").strip()
        if full and not allow_untimed:
            raise SystemExit(
                f"the transcription came back with text and no segment "
                f"timestamps ({len(full)} characters). Nothing downstream can "
                f"anchor, cite or grade a transcript with no seconds in it, so "
                f"this is refused rather than written. Set "
                f"WATCH_OPENROUTER_PROVIDER to one of "
                f"{', '.join(OPENROUTER_TIMESTAMPED_PROVIDERS)}."
            )
        if full:
            out.append({"start": 0.0, "end": 0.0, "text": full})

    return out


def _merge_spans(spans) -> list[tuple[float, float]]:
    """Sorted, non-overlapping `(start, end)` pairs. Empty spans are dropped."""
    merged: list[list[float]] = []
    for start, end in sorted(spans):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _claimed_seconds(served: list[dict]) -> float:
    """Seconds the served rendering calls speech, an overlap counted once.

    The denominator of the coverage share. Merged rather than summed because a
    provider that overlaps two segments has not claimed those seconds twice.
    """
    return sum(end - start for start, end in
               _merge_spans((seg["start"], seg["end"]) for seg in served))


def word_coverage_holes(served: list[dict],
                        rebuilt: list[dict]) -> list[tuple[float, float]]:
    """Stretches the served rendering calls speech that the rebuild has no word for.

    A word is a point in time and speech is continuous, so a word only ever
    covers the silence around itself. Each rebuilt segment is therefore widened
    by WORD_SEGMENT_GAP_SECONDS at each end -- the same silence that ends a
    segment on the way in, so a gap this rule treats as inside a phrase is not
    read as a hole on the way back out. That number is what makes the two
    populations separate: measured 2026-09-10 over the seven captured responses
    holding both arrays, widening by 0.0s leaves 12.64-23.12s uncovered on
    responses that are speech throughout -- indistinguishable from the 14.36s
    music glyph -- and widening by 0.5s leaves 0.02-0.22s.

    Reported as intervals rather than a total because the refusal has to name
    WHERE the audio went; a reader checks a clock range against the video.
    """
    claimed = _merge_spans((seg["start"], seg["end"]) for seg in served)
    covered = _merge_spans((seg["start"] - WORD_SEGMENT_GAP_SECONDS,
                            seg["end"] + WORD_SEGMENT_GAP_SECONDS)
                           for seg in rebuilt)
    holes: list[tuple[float, float]] = []
    for start, end in claimed:
        cursor = start
        for cover_start, cover_end in covered:
            if cover_end <= cursor:
                continue
            if cover_start >= end:
                break
            if cover_start > cursor:
                holes.append((cursor, cover_start))
            cursor = cover_end
            if cursor >= end:
                break
        if cursor < end:
            holes.append((cursor, end))
    return holes


def usable_words(words: list[dict]) -> list[dict]:
    """The word entries a rebuild keeps, as `{start, end, text}`, in order.

    One home for the rule `segments_from_words` applies: an entry with no
    text or no usable times is dropped, so one malformed word never costs
    the chunk it sits in. The word-count guard reads the same list, so the
    two can never disagree about which entries are speech.
    """
    kept: list[dict] = []
    for word in words:
        text = (word.get("word") or word.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(word["start"])
            end = float(word["end"])
        except (KeyError, TypeError, ValueError):
            continue
        kept.append({"start": start, "end": end, "text": text})
    return kept


def words_inside_claim(served: list[dict], words: list[dict]) -> int:
    """Words the rebuild keeps that fall inside the stretches `served` calls speech.

    The word-count line grades against the served rendering's own text, and
    that text describes only the seconds its segments claim. Counting every
    rebuilt word read words from outside the claim as delivered speech: 1,000
    words over a 300s claim plus 260 stamped after it reached 1,260 against
    1,260 and cleared the line. A word counts when its stamp overlaps a
    claimed stretch widened by WORD_SEGMENT_GAP_SECONDS, the same silence
    `word_coverage_holes` grants a rebuilt segment at each end.
    """
    claimed = _merge_spans((seg["start"], seg["end"]) for seg in served)
    return sum(len(word["text"].split()) for word in words
               if any(word["start"] < end + WORD_SEGMENT_GAP_SECONDS
                      and word["end"] > start - WORD_SEGMENT_GAP_SECONDS
                      for start, end in claimed))


def segments_from_words(words: list[dict]) -> list[dict]:
    """Group word-level timestamps into segments this pipeline can anchor.

    A group ends when adding the next word would carry it past
    WORD_SEGMENT_MAX_SECONDS, when the silence before that word reaches
    WORD_SEGMENT_GAP_SECONDS, or when the previous word ended a sentence and
    the group is already WORD_SEGMENT_MIN_SECONDS long. Order is preserved and
    no word is dropped except one carrying no text or no usable times -- a
    single malformed word must not cost the chunk it sits in.
    """
    groups: list[list[dict]] = []
    current: list[dict] = []
    for word in usable_words(words):
        start, end = word["start"], word["end"]
        if current:
            opened = current[0]["start"]
            previous = current[-1]
            if (end - opened > WORD_SEGMENT_MAX_SECONDS
                    or start - previous["end"] >= WORD_SEGMENT_GAP_SECONDS
                    or (previous["text"].endswith((".", "?", "!"))
                        and previous["end"] - opened >= WORD_SEGMENT_MIN_SECONDS)):
                groups.append(current)
                current = []
        current.append(word)
    if current:
        groups.append(current)

    return [
        {"start": round(group[0]["start"], 2),
         "end": round(group[-1]["end"], 2),
         "text": " ".join(word["text"] for word in group)}
        for group in groups
    ]


def transcribe_chunks(
    chunks: list[tuple[Path, float]],
    transcribe_one,
    keeps: list[tuple[float, float]] | None = None,
    retry_window=None,
    dropped: list[tuple[float, float | None, str]] | None = None,
) -> list[dict]:
    """Transcribe each chunk, shift its segments by the chunk offset, concatenate.

    A chunk that fails after its own retries is logged and skipped so one bad
    slice doesn't discard the whole transcript. Raises only if every chunk fails.

    `dropped` is an out-parameter. A chunk that contributes no segments appends
    `(start, end, reason)` in seconds, with `end` None for the last chunk whose
    end this function cannot know, and `reason` either "failed" (the chunk
    raised) or "empty" (it came back with nothing). Losing a chunk quietly is
    how a run wrote a transcript with five minutes missing and reported a
    segment total as though nothing had happened. This function only records;
    `transcribe_video` decides, and it treats the two reasons differently
    because silence is a legitimate reason for an empty chunk and a refusal is
    not a legitimate answer to silence.

    `keeps` is the per-chunk (keep_from, keep_to) from `plan_windows`. With it,
    each chunk contributes only the segments inside its own span and the
    overlaps are dropped; without it every segment is kept, which is right for
    the byte-split path where chunks do not overlap.

    `retry_window(index)` re-decodes one window from a different offset, and is
    called only when a window comes back looping. The degeneration is a property
    of the decode rather than of the audio -- the same stretch that killed a
    decode twice came back clean from a third start -- so a second attempt from
    a different second is the cheapest repair there is. It is used only if it
    loops less than the first attempt did; a retry is allowed to fail.
    """
    segments: list[dict] = []
    failures = 0
    for index, (path, offset) in enumerate(chunks):
        try:
            chunk_segments = transcribe_one(path, offset)
        except SystemExit as exc:
            failures += 1
            lost_from, lost_to = _chunk_span(chunks, index, keeps)
            if dropped is not None:
                dropped.append((lost_from, lost_to,
                                "language" if isinstance(exc, LanguageMismatch)
                                else "failed"))
            print(
                f"[watch] chunk {index + 1}/{len(chunks)} failed — skipping "
                f"({exc}); {_format_span(lost_from, lost_to)} of audio is now "
                f"ABSENT from the transcript",
                file=sys.stderr,
            )
            continue

        run = longest_identical_run(chunk_segments)
        if retry_window and run >= LOOP_RUN:
            print(f"[watch] chunk {index + 1}/{len(chunks)} looped {run}x — "
                  f"re-decoding it from a different offset", file=sys.stderr)
            try:
                alternative = retry_window(index)
            except SystemExit as exc:
                print(f"[watch] the retry failed too ({exc})", file=sys.stderr)
                alternative = None
            if alternative:
                # NOT `is not None`. `longest_identical_run([])` is 0, which is
                # smaller than any looping run, so an empty re-decode would win
                # by construction — the metric's own floor. A looping window is
                # at least visible in the transcript as repetition; replacing it
                # with silence hides the same audio behind a clean-looking run.
                alternative_run = longest_identical_run(alternative)
                print(f"[watch] retry looped {alternative_run}x vs {run}x — "
                      f"{'keeping the retry' if alternative_run < run else 'keeping the first'}",
                      file=sys.stderr)
                if alternative_run < run:
                    chunk_segments = alternative

        shifted = shift_segments(chunk_segments, offset)
        if keeps:
            keep_from, keep_to = keeps[index]
            # Every window after the first reaches SEAM_SECONDS back past its own
            # boundary, and drop_seam_repeats then removes whatever the previous
            # window already kept. Reaching back risks a duplicate; not reaching
            # back risks a silent loss, and only one of those is visible.
            lower = keep_from - SEAM_SECONDS if index else keep_from
            shifted = trim_to_keep(shifted, lower, keep_to,
                                   index == len(chunks) - 1)
            shifted = drop_seam_repeats(segments, shifted)
        if not shifted:
            # WHAT THE WINDOW CONTRIBUTES, not what its decoder returned. A
            # window can decode ninety segments and keep none of them — the
            # comment below has named that boundary bug for longer than this
            # fix has existed, and printing the number is not the same as
            # acting on it. Decoded nothing: silence, which is legitimate.
            # Decoded something and kept none of it: audio that went missing.
            lost_from, lost_to = _chunk_span(chunks, index, keeps)
            if dropped is not None:
                dropped.append((lost_from, lost_to,
                                "empty" if not chunk_segments else "failed"))
        segments.extend(shifted)
        # Both numbers: decoded, then kept. A window that decoded 90 and kept 60
        # is working as intended; one that decoded 90 and kept 0 is a boundary
        # bug, and printing only the decoded count would hide it.
        print(
            f"[watch] chunk {index + 1}/{len(chunks)} → {len(chunk_segments)} "
            f"segments, {len(shifted)} kept",
            file=sys.stderr,
        )

    if failures == len(chunks):
        raise SystemExit("Whisper failed on every audio chunk")
    return segments


# THE LANGUAGE IS DETECTED ONCE PER RUN, NOT ONCE PER DECODE. `-l auto` asks
# whisper.cpp to detect the language of every file it is handed, and a run now
# hands it many: two windows per four minutes, twice over when a second model is
# configured. On a six-minute clip of an English talk that mentions Wales and
# the Tudors, `large-v3` detected Welsh on BOTH of its windows and returned 1,192
# words of fluent Welsh, while `turbo` returned English. The alignment caught it
# -- 0.2127 agreement, refused as E-TS-DIVERGENT -- which is the gate working,
# and it is still a wasted decode and a false divergence report.
#
# So the first successful decode's detected language is remembered and passed
# explicitly to every decode after it. An explicit `WHISPER_CPP_LANG` still wins
# over both.
_DETECTED_LANGUAGE: str | None = None
_UNKNOWN_LANGUAGES_NAMED: set[str] = set()

# A LANGUAGE IS COMPARED AND PINNED AS ITS CODE, NEVER AS RETURNED. Measured
# 2026-09-16: through OpenRouter, `Qwen/Qwen3-ASR-1.7B` answers
# `language: 'english'` on English speech, with or without `language: "en"` in
# the request, while `openai/whisper-large-v3` answers `'en'`. Compared raw,
# every second-decode chunk was refused as a mismatch and the second decode was
# lost. Whisper's own table (`whisper/tokenizer.py`, `LANGUAGES`) is the
# reference for the names.
WHISPER_LANGUAGES = {
    "en": "english", "zh": "chinese", "de": "german", "es": "spanish",
    "ru": "russian", "ko": "korean", "fr": "french", "ja": "japanese",
    "pt": "portuguese", "tr": "turkish", "pl": "polish", "ca": "catalan",
    "nl": "dutch", "ar": "arabic", "sv": "swedish", "it": "italian",
    "id": "indonesian", "hi": "hindi", "fi": "finnish", "vi": "vietnamese",
    "he": "hebrew", "uk": "ukrainian", "el": "greek", "ms": "malay",
    "cs": "czech", "ro": "romanian", "da": "danish", "hu": "hungarian",
    "ta": "tamil", "no": "norwegian", "th": "thai", "ur": "urdu",
    "hr": "croatian", "bg": "bulgarian", "lt": "lithuanian", "la": "latin",
    "mi": "maori", "ml": "malayalam", "cy": "welsh", "sk": "slovak",
    "te": "telugu", "fa": "persian", "lv": "latvian", "bn": "bengali",
    "sr": "serbian", "az": "azerbaijani", "sl": "slovenian", "kn": "kannada",
    "et": "estonian", "mk": "macedonian", "br": "breton", "eu": "basque",
    "is": "icelandic", "hy": "armenian", "ne": "nepali", "mn": "mongolian",
    "bs": "bosnian", "kk": "kazakh", "sq": "albanian", "sw": "swahili",
    "gl": "galician", "mr": "marathi", "pa": "punjabi", "si": "sinhala",
    "km": "khmer", "sn": "shona", "yo": "yoruba", "so": "somali",
    "af": "afrikaans", "oc": "occitan", "ka": "georgian", "be": "belarusian",
    "tg": "tajik", "sd": "sindhi", "gu": "gujarati", "am": "amharic",
    "yi": "yiddish", "lo": "lao", "uz": "uzbek", "fo": "faroese",
    "ht": "haitian creole", "ps": "pashto", "tk": "turkmen", "nn": "nynorsk",
    "mt": "maltese", "sa": "sanskrit", "lb": "luxembourgish", "my": "myanmar",
    "bo": "tibetan", "tl": "tagalog", "mg": "malagasy", "as": "assamese",
    "tt": "tatar", "haw": "hawaiian", "ln": "lingala", "ha": "hausa",
    "ba": "bashkir", "jw": "javanese", "su": "sundanese", "yue": "cantonese",
}
_LANGUAGE_CODES = {name: code for code, name in WHISPER_LANGUAGES.items()}


def normalise_language(value: object) -> str | None:
    """The code for a language given as a code or a name, else None.

    None covers `auto`, an empty value, a value that is not a string, and a
    name the table does not hold. A tag such as yt-dlp's `en-US` is read by its
    primary subtag.
    """
    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    if key in WHISPER_LANGUAGES:
        return key
    if key in _LANGUAGE_CODES:
        return _LANGUAGE_CODES[key]
    primary, dash, _region = key.replace("_", "-").partition("-")
    return primary if dash and primary in WHISPER_LANGUAGES else None


def reset_detected_language() -> None:
    """Forget the pin. One process, one recording; a second video re-detects."""
    global _DETECTED_LANGUAGE
    _DETECTED_LANGUAGE = None
    _UNKNOWN_LANGUAGES_NAMED.clear()


def remember_detected_language(language: object) -> None:
    """Pin the FIRST real detection, as its code. Later windows do not move it."""
    global _DETECTED_LANGUAGE
    code = normalise_language(language)
    if _DETECTED_LANGUAGE is None and code:
        _DETECTED_LANGUAGE = code


def decode_language() -> str:
    """Configured language, else the one this run already detected, else auto.

    `WHISPER_CPP_LANG=auto` is the shipped default and is a request to detect,
    not a language — reading it as one would out-rank the detection it asked
    for and leave every decode detecting independently.
    """
    configured = _read_config_value("WHISPER_CPP_LANG")
    if configured and configured != "auto":
        return configured
    return _DETECTED_LANGUAGE or "auto"


class LanguageMismatch(SystemExit):
    """A chunk came back in a language other than the run's.

    Raised by the chunk, recorded by `transcribe_chunks` as reason "language",
    and refused by `transcribe_video` -- the same chunk-level/run-level split
    as a failed chunk. Measured 2026-09-16: a chunk of English speech came back
    as Welsh with segment starts that still lined up, so its timings prove
    nothing about its words.
    """


def openrouter_language() -> str | None:
    """`WATCH_OPENROUTER_LANG`, else this run's detected language, else None.

    None sends no `language`, which asks the router to detect. `auto` is a
    request to detect for the same reason as `WHISPER_CPP_LANG=auto`.
    """
    configured = _read_config_value("WATCH_OPENROUTER_LANG")
    if configured and configured != "auto":
        return configured
    return _DETECTED_LANGUAGE


def seed_openrouter_language(declared: object) -> None:
    """Pin the run's language before the first request, and say where it came from.

    Precedence, ruled 2026-09-16 for 0.7.6: `WATCH_OPENROUTER_LANG` (not
    `auto`), then the language the video's metadata declares, then the first
    request's detection, which `_transcribe_file` announces. A music intro
    detected as another language used to pin the whole run.
    """
    global _DETECTED_LANGUAGE
    configured = _read_config_value("WATCH_OPENROUTER_LANG")
    if configured and configured != "auto":
        print(f"[watch] language {configured!r} set by WATCH_OPENROUTER_LANG",
              file=sys.stderr)
        return
    code = normalise_language(declared)
    if code:
        _DETECTED_LANGUAGE = code
        print(f"[watch] language {code!r} set by the video metadata "
              f"({declared!r})", file=sys.stderr)


def _run_whisper_cpp(bin_path: str, audio_path: Path,
                     model_override: str | None = None) -> list[dict]:
    """Transcribe one audio file with a local whisper.cpp binary.

    Requires WHISPER_CPP_BIN (the whisper-cli executable) and WHISPER_CPP_MODEL
    (a ggml model file) in the environment or ~/.config/watch/.env. Nothing
    leaves the machine.

    `model_override` is the second decode's model; see `second_model_path`.
    """
    # WHISPER_CPP_BIN may be a path or a bare name already on PATH.
    binary = Path(bin_path).expanduser()
    if not binary.exists():
        resolved = shutil.which(bin_path)
        if not resolved:
            raise SystemExit(f"WHISPER_CPP_BIN is not an executable path or on PATH: {bin_path}")
        binary = Path(resolved)

    model = model_override or _read_config_value("WHISPER_CPP_MODEL")
    if not model:
        raise SystemExit(
            "WHISPER_CPP_MODEL is not set. Point it at a ggml model file "
            "(e.g. ggml-small.en.bin) in ~/.config/watch/.env."
        )
    model_path = Path(model).expanduser()
    if not model_path.exists():
        raise SystemExit(f"WHISPER_CPP_MODEL does not exist: {model_path}")

    # whisper.cpp wants 16kHz WAV input; the pipeline's audio is mp3.
    wav_path = audio_path.with_suffix(".whisper.wav")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(audio_path.resolve()),
        "-ar", "16000", "-ac", "1",
        str(wav_path.resolve()),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not wav_path.exists():
        raise SystemExit(f"ffmpeg wav conversion failed: {result.stderr.strip()}")

    # "auto" lets whisper.cpp detect the spoken language; its own default is
    # English, which silently mangles non-English audio. After the first decode
    # of a run the detection is already made and is reused -- see
    # `_DETECTED_LANGUAGE`.
    language = decode_language()
    threads = _read_config_value("WHISPER_CPP_THREADS") or str(os.cpu_count() or 4)

    # A distinct prefix per model, so a failed second decode can never be read
    # from the first decode's leftover JSON and reported as the second witness.
    out_prefix = audio_path.with_suffix(".whisper2" if model_override else ".whisper")
    cmd = [
        str(binary.resolve()),
        "-m", str(model_path.resolve()),
        "-f", str(wav_path.resolve()),
        "-l", language,
        "-oj",
        "-of", str(out_prefix.resolve()),
        "-t", threads,
        "-pp",
    ]
    # Local runs take minutes on long videos, so stream progress instead of
    # going silent; keep the tail of stderr for the failure message.
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    tail: list[str] = []
    last_pct = -10
    for line in proc.stderr or []:
        line = line.strip()
        if not line:
            continue
        tail.append(line)
        del tail[:-20]
        if "progress =" in line:
            try:
                pct = int(line.split("progress =")[-1].strip().rstrip("%"))
            except ValueError:
                continue
            if pct >= last_pct + 10:
                last_pct = pct
                print(f"[watch] whisper.cpp {pct}%…", file=sys.stderr)
    returncode = proc.wait()

    json_path = Path(str(out_prefix) + ".json")
    if returncode != 0 or not json_path.exists():
        detail = "\n".join(tail[-6:])
        raise SystemExit(f"whisper.cpp failed (exit {returncode}): {detail[-400:]}")

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"whisper.cpp produced unreadable JSON: {exc}")

    remember_detected_language((data.get("result") or {}).get("language"))

    segments: list[dict] = []
    for entry in data.get("transcription") or []:
        text = (entry.get("text") or "").strip()
        if not text:
            continue
        offsets = entry.get("offsets") or {}
        segments.append({
            "start": round(float(offsets.get("from") or 0) / 1000.0, 2),
            "end": round(float(offsets.get("to") or 0) / 1000.0, 2),
            "text": text,
        })
    return segments


def segment_shape(segments: list[dict]) -> tuple[float, float, float]:
    """(median segment seconds, longest segment seconds, span covered).

    The span is measured from the segments themselves rather than from the
    audio, so this needs nothing but what came back.

    FROM THE TIMES, NOT FROM THE LIST ORDER. Reading `segments[0]` and
    `segments[-1]` assumed a response lists its segments in clock order, and
    nothing in the API promises that: a response listing a 300s segment before
    a 20s one measured its own span as 20s, dropped under COARSE_MIN_SECONDS
    and skipped the coarseness gate entirely -- found 2026-09-10.
    """
    if not segments:
        return 0.0, 0.0, 0.0
    spans = sorted(s["end"] - s["start"] for s in segments)
    return (statistics.median(spans), spans[-1],
            max(s["end"] for s in segments)
            - min(s["start"] for s in segments))


def _format_span(start: float, end: float | None) -> str:
    """`9:28–14:12`, or `10:00 to the end` when the span runs off the last chunk.

    A missing stretch of audio is reported the way a reader would cite it. The
    seconds are what the code has; minutes and seconds are what a person can
    check against the video.
    """
    def clock(seconds: float) -> str:
        return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"

    if end is None:
        return f"{clock(start)} to the end"
    return f"{clock(start)}–{clock(end)}"


def _chunk_span(chunks: list, index: int,
                keeps: list[tuple[float, float]] | None = None
                ) -> tuple[float, float | None]:
    """The span of audio a chunk was RESPONSIBLE for, not the span it decoded.

    On the byte-split and seconds-split paths those are the same: a chunk's
    offset is where its audio starts and the next chunk's offset is where it
    ends. On the windowed local path they are not. `plan_windows` decodes from
    `keep_from - overlap`, so a window's decode offset sits up to
    DECODE_OVERLAP_SECONDS before the audio it keeps, and reporting the decode
    offset names a stretch that IS in the transcript while leaving the stretch
    that is missing unnamed. A reader who checks the named seconds finds them
    present and concludes the warning is wrong.
    """
    if keeps:
        return keeps[index]
    following = chunks[index + 1][1] if index + 1 < len(chunks) else None
    return chunks[index][1], following


def check_granularity(segments: list[dict], model: str | None,
                      *, refuse: bool) -> None:
    """Stop a rendering too coarse to anchor -- or, on a second decode, say so.

    THE SECOND DECODE IS ALLOWED TO BE COARSE and the first is not, because the
    two are read for different things. `wq-transcript-align` stamps every
    divergence from the BASE rendering's clock; the other decode contributes
    words, and its own timestamps are never used for a stamp. So a coarse model
    is a usable second witness and an unusable primary.
    """
    median, longest, covered = segment_shape(segments)
    if covered <= COARSE_MIN_SECONDS or median <= COARSE_MEDIAN_SECONDS:
        return

    detail = (f"{model or 'the model'} returned {len(segments)} segment(s) for "
              f"{covered:.0f}s of audio; the typical one spans {median:.0f}s "
              f"and the longest {longest:.0f}s")
    if refuse:
        raise SystemExit(
            f"the transcription is too coarse to anchor: {detail}. A claim can "
            f"only be cited to a second, and this one places everything inside "
            f"a stretch tens of seconds long. Use a model that segments -- "
            f"{OPENROUTER_MODEL} does, at a typical 2-3s -- or keep this one as "
            f"WATCH_OPENROUTER_MODEL_2, where coarse timestamps do no harm.")
    print(f"[watch] second decode is coarse: {detail}. Fine for cross-checking "
          f"words, which is all it is read for; its own stamps are not used.",
          file=sys.stderr)


def _transcribe_file(backend: str, api_key: str, audio_path: Path,
                     model_override: str | None = None,
                     offset_seconds: float = 0.0) -> list[dict]:
    """Transcribe one audio file and return its 0-based segments.

    For cloud backends `api_key` is the API key; for "local" it is the
    whisper.cpp binary path. `model_override` set means this is the SECOND
    decode, which is held to a looser bar -- see `check_granularity`.

    The segments come back 0-based and the caller shifts them; `offset_seconds`
    says where this file sits in the video so that a refusal, which never
    reaches the caller's shift, can still name a clock range a reader can check.
    """
    if backend == "groq":
        model = GROQ_MODEL
        segments = _segments_from_response(
            _post_whisper(GROQ_ENDPOINT, api_key, GROQ_MODEL, audio_path),
            offset_seconds=offset_seconds)
    elif backend == "openai":
        model = OPENAI_MODEL
        segments = _segments_from_response(
            _post_whisper(OPENAI_ENDPOINT, api_key, OPENAI_MODEL, audio_path),
            offset_seconds=offset_seconds)
    elif backend == "openrouter":
        model = (model_override or _read_config_value("WATCH_OPENROUTER_MODEL")
                 or OPENROUTER_MODEL)
        # ONE LANGUAGE PER RUN. The first successful chunk's detection is sent
        # on every later request, and a response in another language is refused
        # rather than kept, because the router may not honour the field.
        language = openrouter_language()
        data = _post_openrouter(api_key, model, audio_path,
                                _read_config_value("WATCH_OPENROUTER_PROVIDER"),
                                language)
        returned = data.get("language")
        code = normalise_language(returned)
        # A value that cannot be read as a language is neither pinned nor
        # refused: refusing would fail a healthy run on a label, and pinning
        # would send the label on every later request.
        if code is None and returned not in (None, "", "auto"):
            if repr(returned) not in _UNKNOWN_LANGUAGES_NAMED:
                _UNKNOWN_LANGUAGES_NAMED.add(repr(returned))
                print(f"[watch] {model} returned language {returned!r}, which "
                      f"is not a known language code or name — not pinned and "
                      f"not compared", file=sys.stderr)
        elif language and code and code != (normalise_language(language)
                                             or language.strip().lower()):
            duration = data.get("duration")
            raise LanguageMismatch(
                f"asked for language {language!r} and got {returned!r} back "
                f"for "
                f"{_format_span(offset_seconds, offset_seconds + duration if duration else None)}"
                f"; its timings may still line up, but its words are not "
                f"the run's speech")
        segments = _segments_from_response(
            data, allow_untimed=False, offset_seconds=offset_seconds)
        if language is None and code:
            print(f"[watch] language {code!r} detected by the first request",
                  file=sys.stderr)
        remember_detected_language(code)
    elif backend == "local":
        model = model_override or _read_config_value("WHISPER_CPP_MODEL")
        segments = _run_whisper_cpp(api_key, audio_path, model_override)
    else:
        raise SystemExit(f"Unknown whisper backend: {backend}")

    check_granularity(segments, model, refuse=model_override is None)
    return segments


def _config_float(name: str, default: float) -> float:
    """A numeric dial from the environment or the dotenv, or its default.

    A value that is not a number is a typo in a config file, and a typo that
    silently reverts to the default would put a whole-file decode back without
    saying so. It stops the run instead.
    """
    raw = _read_config_value(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise SystemExit(f"{name} is not a number: {raw!r}")


def second_model_path() -> str | None:
    """The model for the second decode, from WHISPER_CPP_MODEL_2, or None.

    TWO MODELS ARE NOT TWO WITNESS CLASSES. Both are speech-to-text; they have
    agreed with each other and been wrong together. What a second decode buys is
    a way to SEE an unstable passage: on one file the two models disagreed on the
    episode's most-quoted sentence, one hearing the negation of the other, and a
    single-model note would have published the inverse of the claim.

    So the second rendering is written to disk beside the first and neither is
    merged into the other. Picking the better one is a per-file judgement made
    from the alignment, and inheriting last file's choice is what this exists to
    prevent -- the model trusted on one recording was the one that collapsed on
    the next. That is one reversal, not a rate: enough to stop inheriting a
    choice, and not enough to predict which model fails next.
    """
    return _read_config_value("WHISPER_CPP_MODEL_2")


def second_model(backend: str) -> str | None:
    """The model for the second decode on this backend, or None.

    Opt-in on every backend, because a second decode doubles both the wall clock
    and, on a paid route, the bill.

    ON THE PAID ROUTE THE SECOND MODEL CAN BE A DIFFERENT FAMILY, and that is
    worth more than a second whisper. Two whisper variants are one witness class
    counted twice -- the thing this repo keeps re-learning.

    `Qwen/Qwen3-ASR-1.7B` is the one measured here: reachable through the same
    key, a different architecture rather than another size of the same one, and
    the same 3.7e-05 for a five-second clip. Its timestamps are COARSE -- one
    segment covering 93% of a half-minute clip where whisper-large-v3 returned
    eight -- which is why it belongs in this slot and not the other one. A
    second witness is read for the words it disagrees about; the stamps come
    from the base rendering. `qwen/qwen3-asr` and `qwen/qwen3-asr-flash` are
    also live but reject `verbose_json` outright, so they are refused before
    anything is written.

    Set WATCH_OPENROUTER_MODEL_2 to whichever you want; nothing is chosen for
    you, and neither rendering is merged into the other.
    """
    if backend == "local":
        return second_model_path()
    if backend == "openrouter":
        return _read_config_value("WATCH_OPENROUTER_MODEL_2")
    return None


def write_rendering(path: Path, backend: str, model: str | None,
                    segments: list[dict]) -> Path:
    """Write one decode where `wq-transcript-align` can read it."""
    path.write_text(json.dumps(
        {"backend": backend, "model": model, "segments": segments}, indent=2),
        encoding="utf-8")
    return path


ALIGN_CLI = "wq-transcript-align"


def align_renderings(first: Path, second: Path,
                     duration: float | None) -> Path | None:
    """Compare the two decodes and keep the report, instead of suggesting it.

    A PRINTED COMMAND DOES NOT RUN. Until now a two-model run ended by printing
    `wq-transcript-align <a> <b>` and stopping, which makes the comparison an
    optional step at the exact moment the operator has what they came for. A
    second rendering nobody aligns is a doubled wall clock buying one witness,
    and the whole reason for the second decode is that two models disagreed on
    one recording's most-quoted sentence, one hearing the negation of the other.

    `--duration` is passed when known: a decode that stops early ends cleanly
    and looks fine on its own, and only the audio's length catches it.

    NOTHING HERE CAN FAIL THE TRANSCRIPTION. A missing CLI, a crash, or a report
    full of defects all leave both renderings on disk, and a run with two
    transcripts and no report is worth more than a run with none. Defects are
    printed loudly; they are not raised.
    """
    if shutil.which(ALIGN_CLI) is None:
        print(f"[watch] {ALIGN_CLI} is not installed; the two renderings are "
              f"on disk and NOTHING IS QUOTABLE until they are compared:\n"
              f"[watch]   {ALIGN_CLI} {first} {second}", file=sys.stderr)
        return None

    cmd = [ALIGN_CLI, str(first), str(second)]
    if duration and duration > 0:
        cmd += ["--duration", f"{duration:.3f}"]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        print(f"[watch] alignment could not run: {exc}", file=sys.stderr)
        return None

    report = first.parent / "transcript-align.txt"
    report.write_text((done.stderr or "") + (done.stdout or ""), encoding="utf-8")
    for line in (done.stderr or "").splitlines():
        if line.startswith("#"):
            print(f"[watch] {line}", file=sys.stderr)
    if done.returncode:
        print(f"[watch] THE TWO DECODES DISAGREE — read {report} before quoting "
              f"anything from either", file=sys.stderr)
    else:
        print(f"[watch] alignment clean; regions listed in {report}",
              file=sys.stderr)
    return report


# A REQUEST WHOSE DECODE THINNED IS CUT AGAIN, and these two numbers say when.
#
# MEASURED 2026-09-16 on plugin 0.7.3: one request's first decode kept 1,070
# words where the second decode kept 2,005 (0.53), another kept 1,095 of 1,888
# (0.58). Both responses were fine-grained, so no word-array guard in
# `_segments_from_response` ran, and both runs exited 0. Across the 45
# OpenRouter request spans on disk that day, every other span with a second
# decode read 0.84 or higher. 0.75 sits between the two groups.
#
# A span where the second decode kept under 100 words is not graded: one
# span read 1,385 against 3, which is a second decode that heard nothing, not
# a first decode that lost speech.
THIN_WORD_RATIO = 0.75
THIN_MIN_WORDS = 100


def span_words(segments: list[dict], start: float, end: float | None) -> int:
    """Words in the segments that start inside [start, end); `end` None runs on."""
    return sum(len(seg["text"].split()) for seg in segments
               if seg["start"] >= start and (end is None or seg["start"] < end))


def thin_spans(first: list[dict], second: list[dict],
               plan: list[tuple[float, float]]
               ) -> list[tuple[int, float, float | None, int, int]]:
    """Request spans whose first decode kept too few of the second decode's words.

    Returns `(index, start, end, first_words, second_words)`, `end` None for
    the last request. THE SECOND DECODE ONLY SAYS A SPAN IS SHORT. It never
    supplies a word or a second to the transcript.
    """
    found = []
    for index, (start, _length) in enumerate(plan):
        end = plan[index + 1][0] if index + 1 < len(plan) else None
        heard = span_words(second, start, end)
        kept = span_words(first, start, end)
        if heard >= THIN_MIN_WORDS and kept < THIN_WORD_RATIO * heard:
            found.append((index, start, end, kept, heard))
    return found


def transcribe_video(
    video_path: str,
    audio_out: Path,
    backend: str | None = None,
    api_key: str | None = None,
    language_hint: str | None = None,
) -> tuple[list[dict], str]:
    """Run the full flow: extract audio → upload → parse segments.

    `language_hint` is the language the video's metadata declares, such as
    yt-dlp's `en-US`; only the OpenRouter path reads it.

    Returns (segments, backend_used). Raises SystemExit on any failure.
    """
    # THE PREFERENCE HAS TO REACH THE LOOKUP. This asked `load_api_key()` with
    # no argument and then kept the caller's backend beside whatever credential
    # the unfiltered search happened to return first, so `--backend local` on a
    # machine with an OpenRouter key ran whisper.cpp with the OpenRouter key as
    # its binary path and failed every window: "WHISPER_CPP_BIN is not an
    # executable path or on PATH: sk-or-v1-...". The backend and the credential
    # are one decision and are now made in one call.
    if api_key is None:
        detected_backend, detected_key = load_api_key(backend)
        backend = backend or detected_backend
        api_key = detected_key

    if not backend or not api_key:
        setup_py = Path(__file__).resolve().parent / "setup.py"
        raise SystemExit(
            "No Whisper backend available. Set GROQ_API_KEY or OPENAI_API_KEY, "
            "or OPENROUTER_API_KEY with `--whisper openrouter`, "
            "or WHISPER_CPP_BIN + WHISPER_CPP_MODEL for offline whisper.cpp, "
            "in the environment or in ~/.config/watch/.env. "
            f"Run `python3 {setup_py}` to configure."
        )

    print(f"[watch] extracting audio for Whisper ({backend})…", file=sys.stderr)
    reset_detected_language()
    if backend == "openrouter":
        seed_openrouter_language(language_hint)
    audio_path = extract_audio(video_path, audio_out)
    audio_bytes = audio_path.stat().st_size

    def decode(model_override: str | None, work_name: str,
               dropped: list[tuple[float, float | None, str]] | None = None
               ) -> list[dict]:
        def transcribe_one(path: Path, offset: float = 0.0) -> list[dict]:
            return _transcribe_file(backend, api_key, path, model_override,
                                    offset)

        # WINDOWED, for the local backend only. The cloud APIs window internally
        # and have not been measured degenerating this way; that is a gap in the
        # evidence, not a clean bill of health, so their path is left as it was
        # rather than changed on a guess. Windowing them would also multiply
        # requests, rate-limit exposure and cost per video.
        window_seconds = _config_float("WATCH_DECODE_WINDOW_SECONDS",
                                       DECODE_WINDOW_SECONDS)
        overlap_seconds = _config_float("WATCH_DECODE_OVERLAP_SECONDS",
                                        DECODE_OVERLAP_SECONDS)
        if backend == "local" and window_seconds > 0:
            duration = audio_duration(audio_path)
            try:
                windows = plan_windows(duration, window_seconds, overlap_seconds)
            except ValueError as exc:
                raise SystemExit(f"decode window misconfigured: {exc}")
            if len(windows) == 1:
                print(f"[watch] audio: {duration:.0f}s — one decode window",
                      file=sys.stderr)
                return transcribe_one(audio_path)
            print(
                f"[watch] audio: {duration:.0f}s — {len(windows)} decode windows "
                f"of {window_seconds:.0f}s (+{overlap_seconds:.0f}s context each "
                f"side); a longer decode is where transcripts collapse",
                file=sys.stderr,
            )
            chunks = split_audio(audio_path, audio_out.parent / work_name,
                                 [(offset, length) for offset, length, _, _ in windows])

            def retry_window(index: int) -> list[dict] | None:
                """The same window, decoded from a different second.

                Reaching further back is preferred; at the very start of the
                file there is nothing to reach back into, so it reaches forward
                instead. Either way the window still covers everything it keeps.
                """
                offset, length, _keep_from, _keep_to = windows[index]
                start = max(0.0, offset - overlap_seconds)
                end = min(duration, offset + length + overlap_seconds)
                if start == offset and end == offset + length:
                    return None
                again = split_audio(audio_path,
                                    audio_out.parent / f"{work_name}-retry-{index}",
                                    [(start, end - start)])
                # Returned in the ORIGINAL window's frame, because the caller
                # shifts by that window's offset and knows nothing of this one.
                # The retry's OWN offset is `start`, not the window's, and that
                # is what a refusal inside it has to name.
                return shift_segments(transcribe_one(again[0][0], start),
                                      start - offset)

            return transcribe_chunks(
                chunks, transcribe_one,
                keeps=[(keep_from, keep_to) for _, _, keep_from, keep_to in windows],
                retry_window=retry_window, dropped=dropped)

        # SPLIT BY TIME, not only by size, on the routed cloud path. The
        # provider behind OpenRouter times out after 60 seconds of processing,
        # and this pipeline's audio is small enough that the byte cap would let
        # a single request carry the better part of an hour.
        if backend == "openrouter":
            max_seconds = _config_float("WATCH_OPENROUTER_MAX_SECONDS",
                                        OPENROUTER_MAX_SECONDS)
            duration = audio_duration(audio_path)
            try:
                plan = plan_by_seconds(duration, max_seconds)
            except ValueError as exc:
                raise SystemExit(f"WATCH_OPENROUTER_MAX_SECONDS: {exc}")
            if len(plan) == 1:
                print(f"[watch] audio: {duration:.0f}s — one request to "
                      f"openrouter…", file=sys.stderr)
                return transcribe_one(audio_path)
            print(
                f"[watch] audio: {duration:.0f}s — {len(plan)} requests of "
                f"about {plan[0][1]:.0f}s each; one long request is what the "
                f"provider times out on",
                file=sys.stderr,
            )
            return transcribe_chunks(
                split_audio(audio_path, audio_out.parent / work_name, plan),
                transcribe_one, dropped=dropped)

        if audio_bytes <= MAX_UPLOAD_BYTES:
            verb = "transcribing locally with" if backend == "local" else "uploading to"
            print(
                f"[watch] audio: {audio_bytes / 1024:.0f} kB — {verb} {backend} Whisper…",
                file=sys.stderr,
            )
            return transcribe_one(audio_path)

        duration = audio_duration(audio_path)
        plan = plan_chunks(duration, audio_bytes, MAX_UPLOAD_BYTES)
        print(
            f"[watch] audio: {audio_bytes / (1024 * 1024):.0f} MB exceeds "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB — splitting into {len(plan)} chunks…",
            file=sys.stderr,
        )
        chunks = split_audio(audio_path, audio_out.parent / work_name, plan)
        return transcribe_chunks(chunks, transcribe_one, dropped=dropped)

    # ONLY THE FIRST DECODE'S GAPS DECIDE THE RUN. The second decode is a
    # cross-check whose own timestamps are never used for a stamp, and it is
    # already allowed to fail outright a few lines below.
    gaps: list[tuple[float, float | None, str]] = []
    segments = decode(None, "chunks", dropped=gaps)

    # SILENCE IS NOT A HOLE. A chunk that came back empty is named so a reader
    # can check it against the video, and then the run continues; refusing here
    # would fail a healthy recording over a musical intro.
    for start, end, reason in gaps:
        if reason == "empty":
            print(f"[watch] {_format_span(start, end)} decoded to no speech — "
                  f"kept as silence, not counted as lost audio", file=sys.stderr)

    allowed = (_read_config_value("WATCH_ALLOW_TRANSCRIPT_GAPS") or "").lower()
    foreign = [(start, end) for start, end, reason in gaps
               if reason == "language"]
    if foreign and allowed not in ("1", "true", "yes", "on"):
        spans = ", ".join(_format_span(start, end) for start, end in foreign)
        raise SystemExit(
            f"{len(foreign)} chunk(s) came back in a language other than the "
            f"run's and were dropped: {spans}. Timings that line up do not make "
            f"the words the speech, so the run is refused rather than written. "
            f"Set WATCH_OPENROUTER_LANG to the spoken language, or "
            f"WATCH_ALLOW_TRANSCRIPT_GAPS=1 to keep the partial transcript "
            f"anyway.")

    lost = [(start, end) for start, end, reason in gaps if reason == "failed"]
    if lost and allowed not in ("1", "true", "yes", "on"):
        spans = ", ".join(_format_span(start, end) for start, end in lost)
        raise SystemExit(
            f"{len(lost)} chunk(s) failed and their audio is missing from the "
            f"transcript: {spans}. A note built on this file would cite "
            f"evidence with a hole in it, and nothing downstream can see the "
            f"hole — the report counts the segments that arrived. So the run "
            f"is refused rather than written. Set "
            f"WATCH_ALLOW_TRANSCRIPT_GAPS=1 to keep the partial transcript "
            f"anyway.")

    if not segments:
        raise SystemExit("Whisper returned no transcript segments")

    print(f"[watch] transcribed {len(segments)} segments via {backend}", file=sys.stderr)

    def recut_thin_spans(first: list[dict], other: list[dict]) -> list[dict]:
        # A NEW CUT, NOT A PLAIN RETRY. Re-sending the same chunk file came back
        # byte-identical, so each attempt widens the cut by another overlap on
        # both sides, then trims the result back to the span it answers for.
        duration = audio_duration(audio_path)
        plan = plan_by_seconds(duration, _config_float(
            "WATCH_OPENROUTER_MAX_SECONDS", OPENROUTER_MAX_SECONDS))
        thin = thin_spans(first, other, plan)
        if not thin:
            print(f"[watch] thinning check: {len(plan)} request(s), none thinned",
                  file=sys.stderr)
            return first
        still_thin = []
        for index, start, end, kept, heard in thin:
            span_end = duration if end is None else end
            print(f"[watch] {_format_span(start, end)} kept {kept} words where "
                  f"the second decode kept {heard} — cutting it again",
                  file=sys.stderr)
            replaced = False
            cuts = 0
            for attempt in (1, 2):
                cut_from = max(0.0, start - attempt * DECODE_OVERLAP_SECONDS)
                cut_to = min(duration, span_end + attempt * DECODE_OVERLAP_SECONDS)
                if (cut_from, cut_to) == (start, span_end):
                    break
                cuts += 1
                cut = split_audio(audio_path,
                                  audio_out.parent / f"chunks-thin-{index}-{attempt}",
                                  [(cut_from, cut_to - cut_from)])
                try:
                    retry = shift_segments(_transcribe_file(
                        backend, api_key, cut[0][0], None, cut_from), cut_from)
                except SystemExit as exc:
                    print(f"[watch] cut {attempt} failed ({exc})", file=sys.stderr)
                    continue
                retry = [seg for seg in retry if seg["start"] >= start
                         and (end is None or seg["start"] < end)]
                words = span_words(retry, start, end)
                print(f"[watch] cut {attempt} kept {words} words", file=sys.stderr)
                if words >= THIN_WORD_RATIO * heard:
                    first = sorted(
                        [seg for seg in first if seg["start"] < start
                         or (end is not None and seg["start"] >= end)] + retry,
                        key=lambda seg: seg["start"])
                    replaced = True
                    break
            if not replaced:
                still_thin.append((start, end, cuts))
        if still_thin:
            spans = ", ".join(_format_span(start, end) for start, end, _ in still_thin)
            # Only as many cuts as were made: a span that is the whole audio
            # cannot widen on either side, so it gets none.
            detail = ", ".join(
                f"{_format_span(start, end)} after {cuts} new cut{'' if cuts == 1 else 's'}"
                + (" (one request covers the whole audio, so no different cut "
                   "exists)" if cuts == 0 else "")
                for start, end, cuts in still_thin)
            if allowed not in ("1", "true", "yes", "on"):
                raise SystemExit(
                    f"{len(still_thin)} request(s) kept too few words: "
                    f"{detail}. The second decode heard speech there "
                    f"that the transcript does not carry, so the run is refused "
                    f"rather than written. Set WATCH_ALLOW_TRANSCRIPT_GAPS=1 to "
                    f"keep the thin transcript anyway.")
            print(f"[watch] keeping thin audio at {spans} because "
                  f"WATCH_ALLOW_TRANSCRIPT_GAPS is set", file=sys.stderr)
        return first

    second = second_model(backend)
    other = None
    if second:
        first_model = (_read_config_value("WHISPER_CPP_MODEL")
                       if backend == "local"
                       else _read_config_value("WATCH_OPENROUTER_MODEL")
                       or OPENROUTER_MODEL)
        first_path = write_rendering(
            audio_out.parent / "transcript-1.json", backend, first_model, segments)
        # A second decode that dies must not take the first one's transcript
        # with it. A run with one rendering is worse than a run with two and
        # says so; a run with none is a wasted download.
        try:
            other = decode(second, "chunks-2")
        except SystemExit as exc:
            other = None
            print(f"[watch] second decode failed, continuing with one: {exc}",
                  file=sys.stderr)
        else:
            second_path = write_rendering(
                audio_out.parent / "transcript-2.json", backend, second, other)
            print(f"[watch] second decode: {len(other)} segments — NOTHING HERE "
                  f"IS QUOTABLE until the two agree", file=sys.stderr)
            if backend == "openrouter":
                recut = recut_thin_spans(segments, other)
                if recut is not segments:
                    segments = recut
                    write_rendering(first_path, backend, first_model, segments)
            try:
                duration = audio_duration(audio_path)
            except SystemExit:
                duration = None
            align_renderings(first_path, second_path, duration)

    if backend == "openrouter" and other is None:
        print("[watch] no second decode, so thinning could not be checked: a "
              "request that lost words passes unseen", file=sys.stderr)

    return segments, backend


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: whisper.py <video-path> [<audio-out.mp3>] "
              "[--backend openrouter|groq|openai|local]", file=sys.stderr)
        raise SystemExit(2)

    video = sys.argv[1]
    audio_out = Path(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else Path("audio.mp3")
    backend_override = None
    if "--backend" in sys.argv:
        backend_override = sys.argv[sys.argv.index("--backend") + 1]

    segments, backend = transcribe_video(video, audio_out, backend=backend_override)
    print(json.dumps({"backend": backend, "segments": segments}, indent=2))
