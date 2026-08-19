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
OPENROUTER_PROVIDER = "groq"
# The providers OpenRouter documents as returning `verbose_json`. Kept as the
# preference and as the text of the error message, not as a belief: the
# measurement above found a provider outside this list returning timestamps
# anyway. Asking for one of these is the cheap precaution; the refusal in
# `_segments_from_response` is the check.
OPENROUTER_TIMESTAMPED_PROVIDERS = ("openai", "groq", "together")
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


def load_api_key(preferred: str | None = None) -> tuple[str, str] | tuple[None, None]:
    """Return (backend, api_key). OpenRouter, then Groq, then OpenAI, then local.

    If `preferred` is "openrouter", "groq", "openai", or "local", only that
    backend is considered. For the "local" backend the second tuple element is
    the path to the whisper.cpp binary (WHISPER_CPP_BIN) rather than an API key.

    OpenRouter sits first because setting its key is already a decision to pay
    for transcription, and because a hosted decode has not been measured
    collapsing the way a long local one does. Having no key at all remains the
    normal state, and the local backend remains the one that needs nothing.
    """
    candidates = (
        ("OPENROUTER_API_KEY", "openrouter"),
        ("GROQ_API_KEY", "groq"),
        ("OPENAI_API_KEY", "openai"),
        ("WHISPER_CPP_BIN", "local"),
    )
    if preferred is not None:
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
                     provider: str | None = None) -> dict:
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
    if provider not in OPENROUTER_TIMESTAMPED_PROVIDERS:
        print(
            f"[watch] WATCH_OPENROUTER_PROVIDER={provider!r} is outside the "
            f"providers OpenRouter documents as returning segment timestamps "
            f"({', '.join(OPENROUTER_TIMESTAMPED_PROVIDERS)}). One outside it "
            f"returned them anyway when this was measured, so this is a "
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
        "temperature": 0,
        "provider": {"only": [provider], "order": [provider],
                     "allow_fallbacks": False},
    }
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


def _segments_from_response(data: dict, allow_untimed: bool = True) -> list[dict]:
    """Convert Whisper verbose_json into our {start, end, text} segment format.

    `allow_untimed` decides what happens when the response carries text but no
    segments. The old behaviour -- one segment spanning 0.0 to 0.0 holding the
    whole transcript -- keeps a caller working, and it also produces a file that
    every downstream check will grade as if it had times. On a route where an
    untimed response means the request was routed to the wrong provider, that is
    a silent wrong answer, so that route asks for it to be refused instead.
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


def transcribe_chunks(
    chunks: list[tuple[Path, float]],
    transcribe_one,
    keeps: list[tuple[float, float]] | None = None,
    retry_window=None,
) -> list[dict]:
    """Transcribe each chunk, shift its segments by the chunk offset, concatenate.

    A chunk that fails after its own retries is logged and skipped so one bad
    slice doesn't discard the whole transcript. Raises only if every chunk fails.

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
            chunk_segments = transcribe_one(path)
        except SystemExit as exc:
            failures += 1
            print(
                f"[watch] chunk {index + 1}/{len(chunks)} failed — skipping ({exc})",
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
            if alternative is not None:
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
    # English, which silently mangles non-English audio.
    language = _read_config_value("WHISPER_CPP_LANG") or "auto"
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
    """
    if not segments:
        return 0.0, 0.0, 0.0
    spans = sorted(s["end"] - s["start"] for s in segments)
    return (statistics.median(spans), spans[-1],
            segments[-1]["end"] - segments[0]["start"])


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
                     model_override: str | None = None) -> list[dict]:
    """Transcribe one audio file and return its 0-based segments.

    For cloud backends `api_key` is the API key; for "local" it is the
    whisper.cpp binary path. `model_override` set means this is the SECOND
    decode, which is held to a looser bar -- see `check_granularity`.
    """
    if backend == "groq":
        model = GROQ_MODEL
        segments = _segments_from_response(
            _post_whisper(GROQ_ENDPOINT, api_key, GROQ_MODEL, audio_path))
    elif backend == "openai":
        model = OPENAI_MODEL
        segments = _segments_from_response(
            _post_whisper(OPENAI_ENDPOINT, api_key, OPENAI_MODEL, audio_path))
    elif backend == "openrouter":
        model = (model_override or _read_config_value("WATCH_OPENROUTER_MODEL")
                 or OPENROUTER_MODEL)
        segments = _segments_from_response(
            _post_openrouter(api_key, model, audio_path,
                             _read_config_value("WATCH_OPENROUTER_PROVIDER")),
            allow_untimed=False)
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


def transcribe_video(
    video_path: str,
    audio_out: Path,
    backend: str | None = None,
    api_key: str | None = None,
) -> tuple[list[dict], str]:
    """Run the full flow: extract audio → upload → parse segments.

    Returns (segments, backend_used). Raises SystemExit on any failure.
    """
    if backend is None or api_key is None:
        detected_backend, detected_key = load_api_key()
        backend = backend or detected_backend
        api_key = api_key or detected_key

    if not backend or not api_key:
        setup_py = Path(__file__).resolve().parent / "setup.py"
        raise SystemExit(
            "No Whisper backend available. Set OPENROUTER_API_KEY (preferred), "
            "GROQ_API_KEY or OPENAI_API_KEY, "
            "or WHISPER_CPP_BIN + WHISPER_CPP_MODEL for offline whisper.cpp, "
            "in the environment or in ~/.config/watch/.env. "
            f"Run `python3 {setup_py}` to configure."
        )

    print(f"[watch] extracting audio for Whisper ({backend})…", file=sys.stderr)
    audio_path = extract_audio(video_path, audio_out)
    audio_bytes = audio_path.stat().st_size

    def decode(model_override: str | None, work_name: str) -> list[dict]:
        def transcribe_one(path: Path) -> list[dict]:
            return _transcribe_file(backend, api_key, path, model_override)

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
                return shift_segments(transcribe_one(again[0][0]), start - offset)

            return transcribe_chunks(
                chunks, transcribe_one,
                keeps=[(keep_from, keep_to) for _, _, keep_from, keep_to in windows],
                retry_window=retry_window)

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
                transcribe_one)

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
        return transcribe_chunks(chunks, transcribe_one)

    segments = decode(None, "chunks")

    if not segments:
        raise SystemExit("Whisper returned no transcript segments")

    print(f"[watch] transcribed {len(segments)} segments via {backend}", file=sys.stderr)

    second = second_model(backend)
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
            print(f"[watch] second decode failed, continuing with one: {exc}",
                  file=sys.stderr)
        else:
            second_path = write_rendering(
                audio_out.parent / "transcript-2.json", backend, second, other)
            print(
                f"[watch] second decode: {len(other)} segments — NOTHING HERE IS "
                f"QUOTABLE until the two agree:\n"
                f"[watch]   wq-transcript-align {first_path} {second_path}",
                file=sys.stderr,
            )

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
