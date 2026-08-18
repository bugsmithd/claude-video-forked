#!/usr/bin/env python3
"""Whether a transcript decode survived, and where a second decode disagrees.

A note made from video quotes a transcript. If the decode that produced that
transcript degenerated, every quote downstream of the collapse is invented text
with a timestamp on it -- and no other gate in this package can see it. The
anchors resolve, the quoted words match the file, and the file is wrong.

That is not hypothetical. On one two-hour recording a single-pass decode
collapsed at 21:49 and repeated one sentence to the end of the file: 6,434
emissions, 362 distinct texts out of 6,809, 83% of the recording lost, half an
hour of compute spent producing it. Its last line before the collapse was
already mis-heard, and the first draft of the note quoted the mis-hearing.
Splitting the same audio into nine fifteen-minute windows bounded the damage
without removing it -- seven clean, one looped 21 times, one lost thirteen
minutes -- and an offset re-run over the lost stretch collapsed again at a
different second. Three four-minute windows over that same stretch came back
clean. So degeneration is a property of the decode, not of the audio, and its
probability scales with the length of the decode window.

Two things follow, and this command supplies both:

  A DEGENERATION CHECK, per rendering. Loops, coverage gaps and truncation are
  mechanical to detect, and every one of them above was found by hand first.

  A SECOND DECODE, aligned over the whole file. Two models are NOT two witness
  classes: both are speech-to-text, and they have agreed with each other and
  been wrong together. What an alignment measures is STABILITY, and instability
  is a reason to distrust a passage -- agreement is never on its own a reason to
  trust one. On the file after the one it was trusted on, the better model was
  the one that collapsed, so the oracle is chosen per file, from the alignment,
  and never inherited. That is one reversal, not a rate; it is enough to stop
  inheriting, and not enough to predict which model will fail next.

Absence is never evidence. A divergent region is reported as a passage one
decode did not witness, never as a passage that is false.

Usage:
    wq-transcript-align RENDERING [RENDERING ...] [--duration SECONDS]
    wq-transcript-align --selftest

A rendering may be WebVTT, whisper.cpp `-oj` JSON, Whisper `verbose_json`, or a
bare list of {start, end, text}. The first one named is the baseline that every
other is aligned against.

Exit: 0 clean, 1 defects, 2 usage or unreadable input.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

PROG = "transcript_align.py"

# A run of identical consecutive texts. The healthy decode of the two-hour file
# had a longest run of 3, and the composite rebuilt from windows was accepted on
# "no run of four"; the collapsed decode ran to 6,434. Four separates them by
# three orders of magnitude, so the exact value is not delicate.
MAX_RUN = 4
# Silence longer than this reads as lost audio rather than a pause. Also the
# acceptance criterion the windowed composite was checked against.
MAX_GAP = 20.0
# Distinct texts as a share of all texts. Healthy: 2,009 of 2,129 = 0.94.
# Collapsed: 362 of 6,809 = 0.05. Anything under this is a stuck decoder.
MIN_DISTINCT = 0.5
# Whole-file agreement between two renderings. Measured pairs, and none of them
# is a pair of unimpeached decodes, because on these recordings there was no
# such pair: 0.9496 between a clean decode and a composite rebuilt window by
# window after a collapse; 0.9682 on a file where the other model looped three
# times; 0.6963 where the collapse took the last seven minutes; 0.0771 against
# the decode that lost 83% of a file. Below this floor two decodes disagree so
# widely that neither can be quoted without opening the audio.
MIN_RATIO = 0.85
# A divergent region counts as substantial at this many tokens on its LONGER
# side -- max, not min, because a region with nothing on one side is exactly the
# case that matters most: a clause only one decode heard at all. Three of those
# carried real content on the file measured above, including a price and a
# contractual sum that existed in one rendering only.
MIN_REGION = 3
REGIONS_SHOWN = 10

# EVERY CONSTANT BELOW EXISTS BECAUSE AN ATTACK GOT PAST THE ONES ABOVE. A lane
# whose job was to build inputs that PASS defeated ten of this file's promises on
# its first attempt, and each of these closes one of them. The numbers are
# measured against the corpus's real decodes, clean and collapsed, and the
# separations are stated beside them so a later reader can re-derive rather than
# trust.

# A whisper segment is seconds long. One that claims minutes is malformed -- and
# a single segment claiming to run to the end of the file used to blind BOTH the
# gap check and the truncation check, because each folded it into a running
# maximum. Thirty-one segments could hide 7,100 seconds of missing audio.
MAX_SEGMENT_SECONDS = 120.0
# Share of the running time that has a segment over it. Real decodes measure
# 0.98-1.00. One second of speech every twenty passes every per-hole gap check
# ever written, and measures 0.05 here.
MIN_COVERAGE = 0.5
# Share of all segments that are the SAME text. Real decodes: 0.011 to 0.032.
# Collapsed decodes: 0.45 and 0.945. An interleaved loop -- one stuck line
# alternating with real speech -- holds a longest run of 1 and a distinct ratio
# of 0.5006, clearing the floor above by six ten-thousandths, and lands at 0.5.
MAX_TOP_SHARE = 0.10
# Consecutive segments that are near-identical rather than identical. Appending
# a counter to each repeat makes 2,000 copies of one sentence look like 2,000
# distinct texts. Measured longest near-run: 2-4 on real decodes, 409 and 6,434
# on collapsed ones, so 8 sits between them with room on both sides.
NEAR_RUN = 8
NEAR_SIMILARITY = 0.8
# Distinct words per minute. Real: 26.9 to 38.0. A file of permuted filler is
# vocabulary-poor however lively its segment texts look. This floor is far below
# any real speech, so it fires only on catastrophe and never on a quiet talk.
MIN_WORDS_PER_MINUTE = 5.0
# Below this the per-file statistics above are noise rather than evidence.
STATS_MIN_SECONDS = 300.0
STATS_MIN_SEGMENTS = 20

RE_WORD = re.compile(r"[a-z0-9']+")
# Words that carry a claim's meaning rather than its wording. A divergence of one
# token is invisible to any size ranking -- two decodes differing only in whether
# the speaker said "not" scored 0.9999905 over 52,788 tokens and printed nothing
# -- so a region touching one of these is surfaced whatever its size. Names are
# NOT in here: they are lost to lowercasing, and are left to the size ranking.
SALIENT = frozenset("""
no not never none nor neither cannot can't don't doesn't didn't won't wouldn't
shouldn't isn't aren't wasn't weren't without unless except rarely hardly
zero one two three four five six seven eight nine ten eleven twelve twenty
thirty forty fifty sixty seventy eighty ninety hundred thousand million billion
half double triple percent
""".split())


def hms(seconds: float) -> str:
    total = int(seconds)
    if total >= 3600:
        return f"{total // 3600}:{total // 60 % 60:02d}:{total % 60:02d}"
    return f"{total // 60:02d}:{total % 60:02d}"


def normalise(text: str) -> str:
    return " ".join(RE_WORD.findall(text.lower()))


def load_segments(path: Path) -> list[dict]:
    """Read one rendering into [{start, end, text}], sorted by start.

    Four shapes arrive in practice and all four are the same three fields under
    different names, so the reader accepts all of them rather than making the
    operator convert -- a conversion step is a place for a transcript to be
    silently truncated.
    """
    if path.suffix.lower() == ".vtt":
        # say_captions already parses WebVTT, including the rolling auto-caption
        # shape. A second parser here is a second thing to be wrong: a reviewer
        # once had to reimplement VTT parsing to check a claim, and that is the
        # cost this import avoids. Imported inside the function so a malformed
        # watch-quality.toml (which say_captions reads at import) cannot stop
        # this command from checking a pair of JSON renderings.
        from .say_captions import cues
        return [{"start": s, "end": e, "text": t} for s, e, t in cues(path)]

    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "transcription" in raw:      # whisper.cpp -oj
        out = []
        for entry in raw.get("transcription") or []:
            offsets = entry.get("offsets") or {}
            out.append({"start": float(offsets.get("from") or 0) / 1000.0,
                        "end": float(offsets.get("to") or 0) / 1000.0,
                        "text": (entry.get("text") or "").strip()})
    elif isinstance(raw, dict) and "segments" in raw:         # verbose_json
        out = [{"start": float(s.get("start") or 0.0),
                "end": float(s.get("end") or 0.0),
                "text": (s.get("text") or "").strip()}
               for s in raw.get("segments") or []]
    elif isinstance(raw, list):                               # bare segments
        out = [{"start": float(s.get("start") or 0.0),
                "end": float(s.get("end") or 0.0),
                "text": (s.get("text") or "").strip()}
               for s in raw]
    else:
        raise ValueError("not a transcript: expected .vtt, or JSON with "
                         "'transcription', 'segments', or a bare segment list")
    out = [s for s in out if s["text"]]
    return sorted(out, key=lambda s: s["start"])


def tokens_with_times(segments: list[dict]) -> tuple[list[str], list[float]]:
    """Flat token stream plus, per token, the second its segment starts at.

    Alignment happens over tokens because segment boundaries differ between
    models -- the same sentence is one segment to one decoder and three to
    another, so aligning segments would report every such split as a divergence.
    """
    words: list[str] = []
    times: list[float] = []
    for seg in segments:
        for word in RE_WORD.findall(seg["text"].lower()):
            words.append(word)
            times.append(seg["start"])
    return words, times


def longest_run(segments: list[dict]) -> tuple[int, float, str]:
    """Longest run of consecutive identical texts: (length, start, text)."""
    best_len, best_start, best_text = 0, 0.0, ""
    run_len, run_start, previous = 0, 0.0, None
    for seg in segments:
        key = normalise(seg["text"])
        if key and key == previous:
            run_len += 1
        else:
            run_len, run_start, previous = 1, seg["start"], key
        if run_len > best_len:
            best_len, best_start, best_text = run_len, run_start, seg["text"]
    return best_len, best_start, best_text


def segment_end(seg: dict, cap: float = MAX_SEGMENT_SECONDS) -> float:
    """A segment's end, refusing to believe an absurd one.

    The uncapped version let one malformed segment claiming to run to the end of
    the file blind every later check: the running maximum jumped to the end, so
    no hole after it could be seen and the rendering could never look short.
    """
    return min(seg["end"], seg["start"] + cap)


def gaps(segments: list[dict], max_gap: float) -> list[tuple[float, float]]:
    """Stretches longer than max_gap with no segment in them: (start, end)."""
    out: list[tuple[float, float]] = []
    previous_end = 0.0
    for seg in segments:
        if seg["start"] - previous_end > max_gap:
            out.append((previous_end, seg["start"]))
        previous_end = max(previous_end, segment_end(seg), seg["start"])
    return out


def content_tokens(text: str) -> list[str]:
    """Tokens with the numbers taken out.

    A counter is not vocabulary and not content. Numbering each copy of one
    sentence makes 2,000 repeats look like 2,000 distinct texts AND inflates the
    distinct-word count that is supposed to notice an empty transcript, so both
    of those measures count words the speaker chose rather than a loop's index.
    Salience, which is the one place a number is the point, uses the raw tokens.
    """
    return [w for w in RE_WORD.findall(text.lower())
            if not any(c.isdigit() for c in w)]


def near_run(segments: list[dict], similarity: float = NEAR_SIMILARITY) -> int:
    """Longest run of consecutive segments that are NEARLY the same.

    Identical-text detection is defeated by appending anything that changes --
    a counter, a timestamp, one word -- so 2,000 copies of a sentence arrive as
    2,000 distinct texts. Overlap of the word sets survives that.
    """
    best = run = 0
    previous: set[str] = set()
    for seg in segments:
        words = set(content_tokens(seg["text"]))
        union = words | previous
        run = run + 1 if union and len(words & previous) / len(union) >= similarity else 1
        previous = words
        best = max(best, run)
    return best


def top_share(segments: list[dict]) -> tuple[float, str]:
    """Share of segments carrying the single most common text, and that text.

    Adjacency-free on purpose. A loop interleaved with real speech has a longest
    run of 1 and a distinct ratio just over any floor worth setting, and half of
    every segment in the file is still one sentence.
    """
    if not segments:
        return 0.0, ""
    counts = Counter(normalise(s["text"]) for s in segments)
    text, count = counts.most_common(1)[0]
    return count / len(segments), text


def coverage(segments: list[dict], span: float) -> float:
    """Share of `span` that some segment sits over, counting overlaps once."""
    if span <= 0:
        return 0.0
    covered = 0.0
    reached = 0.0
    for seg in sorted(segments, key=lambda s: s["start"]):
        start = max(seg["start"], reached)
        end = segment_end(seg)
        if end > start:
            covered += end - start
            reached = end
    return min(covered / span, 1.0)


def check_rendering(name: str, segments: list[dict], duration: float | None,
                    max_run: int, max_gap: float,
                    min_distinct: float) -> tuple[list[str], dict]:
    """Degeneration defects for one rendering, plus its census numbers."""
    defects: list[str] = []
    texts = [normalise(s["text"]) for s in segments]
    distinct = len(set(texts))
    words = sum(len(RE_WORD.findall(s["text"].lower())) for s in segments)
    run_len, run_start, run_text = longest_run(segments)
    holes = gaps(segments, max_gap)
    last_end = max((segment_end(s) for s in segments), default=0.0)
    span = duration if duration is not None else last_end
    near = near_run(segments)
    share, share_text = top_share(segments)
    covered = coverage(segments, span)
    minutes = span / 60.0
    vocabulary = {w for s in segments for w in content_tokens(s["text"])}
    per_minute = len(vocabulary) / minutes if minutes else 0.0
    # Below these the statistics are noise, and a check that fires on noise is a
    # check that gets ignored. Said out loud in the census rather than assumed.
    enough = span >= STATS_MIN_SECONDS and len(segments) >= STATS_MIN_SEGMENTS

    for seg in segments:
        if seg["end"] - seg["start"] > MAX_SEGMENT_SECONDS:
            defects.append(
                f"{name}[{hms(seg['start'])}] E-TS-SEGMENT one segment claims "
                f"{seg['end'] - seg['start']:.0f}s; a segment that long is "
                f"malformed, and it hides every hole and every truncation after it")
            break

    if near >= NEAR_RUN and run_len < max_run:
        defects.append(
            f"{name}[00:00] E-TS-NEARLOOP {near} consecutive segments are "
            f"near-identical (limit {NEAR_RUN}) without being identical; a "
            f"repeat with a counter on it is still a repeat")
    if enough and share > MAX_TOP_SHARE:
        defects.append(
            f"{name}[00:00] E-TS-DOMINANT {share:.0%} of segments are one text "
            f"(limit {MAX_TOP_SHARE:.0%}), interleaved or not: {share_text[:50]!r}")
    if enough and covered < MIN_COVERAGE:
        defects.append(
            f"{name}[00:00] E-TS-SPARSE segments cover {covered:.0%} of "
            f"{hms(span)} (floor {MIN_COVERAGE:.0%}); the rest is unwitnessed, "
            f"and no single hole in it need exceed the gap limit")
    if enough and per_minute < MIN_WORDS_PER_MINUTE:
        defects.append(
            f"{name}[00:00] E-TS-VOCABULARY {per_minute:.1f} distinct words per "
            f"minute (floor {MIN_WORDS_PER_MINUTE}); real speech measures 27 to "
            f"38, so this text is lively-looking and empty")

    if not segments:
        defects.append(f"{name}[00:00] E-TS-EMPTY no segments in this rendering")
    if run_len >= max_run:
        defects.append(
            f"{name}[{hms(run_start)}] E-TS-LOOP {run_len} identical segments in "
            f"a row (limit {max_run}); the decoder is repeating itself, and "
            f"nothing after this second can be quoted: {run_text[:60]!r}")
    if segments and distinct / len(segments) < min_distinct:
        defects.append(
            f"{name}[00:00] E-TS-REPEAT {distinct} distinct of {len(segments)} "
            f"segments ({distinct / len(segments):.2f}, floor {min_distinct}); "
            f"a decode this repetitive has stalled somewhere")
    for start, end in holes:
        defects.append(
            f"{name}[{hms(start)}] E-TS-GAP no segment for {end - start:.0f}s, "
            f"to {hms(end)}; the audio there is unwitnessed, not silent")
    if duration is not None and duration - last_end > max_gap:
        defects.append(
            f"{name}[{hms(last_end)}] E-TS-SHORT rendering ends {duration - last_end:.0f}s "
            f"before the declared {hms(duration)}; the tail was never decoded")

    census = {"name": name, "segments": len(segments), "distinct": distinct,
              "words": words, "run": run_len, "gaps": len(holes),
              "last_end": last_end, "near": near, "top_share": share,
              "coverage": covered, "per_minute": per_minute, "enough": enough}
    return defects, census


def is_salient(words: list[str]) -> bool:
    """Does this side of a divergence carry meaning rather than wording?"""
    return any(w in SALIENT or any(c.isdigit() for c in w) for w in words)


def align(base: list[dict], other: list[dict],
          min_region: int) -> tuple[float, list[dict], dict]:
    """Whole-file token alignment: (ratio, regions, census).

    `autojunk` is off. It treats a token appearing in over 1% of a long stream
    as noise -- which, on a transcript, is every common word, and it inflates
    the ratio of two decodes that share nothing but articles.
    """
    a_words, a_times = tokens_with_times(base)
    b_words, b_times = tokens_with_times(other)
    matcher = difflib.SequenceMatcher(None, a_words, b_words, autojunk=False)
    ratio = matcher.ratio()

    regions: list[dict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        base = a_words[i1:i2]
        other = b_words[j1:j2]
        regions.append({
            "tag": tag,
            "size": max(i2 - i1, j2 - j1),
            "at": a_times[i1] if i1 < len(a_times) else (
                a_times[-1] if a_times else 0.0),
            "base": " ".join(base),
            "other": " ".join(other),
            "salient": is_salient(base) or is_salient(other),
        })

    shown = [r for r in regions if r["size"] >= min_region or r["salient"]]
    census = {"ratio": ratio, "regions": len(regions),
              "substantial": sum(1 for r in regions if r["size"] >= min_region),
              "salient": sum(1 for r in regions if r["salient"]),
              "base_tokens": len(a_words), "other_tokens": len(b_words),
              "only_base": sum(len(r["base"].split()) for r in regions),
              "only_other": sum(len(r["other"].split()) for r in regions)}
    # Salient first, then by size. A one-token divergence is bottom of any size
    # ranking and top of the list of things that change what a note says: two
    # decodes differing only in whether the speaker said "not" scored 0.9999905,
    # produced a single region of size 1, and printed nothing at all.
    return ratio, sorted(shown, key=lambda r: (not r["salient"], -r["size"])), census


def fingerprint(segments: list[dict]) -> str:
    """What two renderings must NOT share if they are to be two witnesses."""
    joined = "\n".join(f"{s['start']:.2f}\t{normalise(s['text'])}"
                       for s in segments)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def selftest() -> int:
    cases = 0

    def check(label: str, got, want) -> None:
        nonlocal cases
        cases += 1
        if got != want:
            raise AssertionError(f"{label}: got {got!r}, want {want!r}")

    def segs(*rows) -> list[dict]:
        return [{"start": float(s), "end": float(s) + 2.0, "text": t}
                for s, t in rows]

    check("MM:SS", hms(65), "01:05")
    check("past the hour", hms(3918), "1:05:18")
    check("punctuation and case fall out", normalise("Well, THAT'S it."),
          "well that's it")

    clean = segs((0, "one thing happened"), (3, "then another"),
                 (6, "and a third"), (9, "and a fourth"))
    check("a clean run has no repeats", longest_run(clean)[0], 1)
    check("a clean run has no gaps", gaps(clean, MAX_GAP), [])

    # The shape of the collapse: one text, over and over, to the end.
    stuck = segs((0, "a real sentence")) + segs(*[(3 + i * 3, "the same line")
                                                 for i in range(9)])
    length, start, _ = longest_run(stuck)
    check("a loop is counted", length, 9)
    check("...and anchored at its first second", start, 3.0)
    defects, census = check_rendering("v", stuck, None, MAX_RUN, MAX_GAP,
                                      MIN_DISTINCT)
    check("a loop is a defect", sum("E-TS-LOOP" in d for d in defects), 1)
    check("...and so is the distinct-text share",
          sum("E-TS-REPEAT" in d for d in defects), 1)
    check("the loop defect names the second", "[00:03]" in defects[0], True)
    check("census counts distinct texts", census["distinct"], 2)

    # A window that decoded nothing leaves a hole, not silence.
    holed = segs((0, "before the hole"), (90, "after the hole"))
    check("a gap is found", gaps(holed, MAX_GAP), [(2.0, 90.0)])
    defects, _ = check_rendering("v", holed, None, MAX_RUN, MAX_GAP,
                                 MIN_DISTINCT)
    check("a gap is a defect", sum("E-TS-GAP" in d for d in defects), 1)
    check("a late start is a gap too",
          gaps(segs((60, "the file starts here")), MAX_GAP), [(0.0, 60.0)])

    # Truncation is only visible against a declared duration.
    defects, _ = check_rendering("v", clean, 600.0, MAX_RUN, MAX_GAP,
                                 MIN_DISTINCT)
    check("a short rendering is a defect",
          sum("E-TS-SHORT" in d for d in defects), 1)
    defects, _ = check_rendering("v", clean, None, MAX_RUN, MAX_GAP,
                                 MIN_DISTINCT)
    check("...and is not guessed at without one",
          sum("E-TS-SHORT" in d for d in defects), 0)
    check("an empty rendering says so",
          sum("E-TS-EMPTY" in d for d in check_rendering(
              "v", [], None, MAX_RUN, MAX_GAP, MIN_DISTINCT)[0]), 1)

    # Alignment. Identical content, different segment boundaries, must not read
    # as divergence -- that is why the alignment is over tokens.
    split = segs((0, "one thing"), (1, "happened then"), (3, "another and a"),
                 (6, "third and a fourth"))
    ratio, regions, _ = align(clean, split, MIN_REGION)
    check("re-segmented identical text aligns exactly", ratio, 1.0)
    check("...with no divergent regions", regions, [])

    swapped = segs((0, "one thing happened"), (3, "then another"),
                   (6, "and a third"), (9, "and a fifth"))
    ratio, regions, census = align(clean, swapped, 1)
    check("one changed word is one region", len(regions), 1)
    check("...anchored where it occurs", regions[0]["at"], 9.0)
    check("...and shows both readings",
          (regions[0]["base"], regions[0]["other"]), ("fourth", "fifth"))
    check("a one-token region is not substantial by default",
          align(clean, swapped, MIN_REGION)[1], [])
    check("agreement is still near total", round(ratio, 2), 0.91)

    # A clause only one decode heard: nothing on one side, which is the case
    # MIN_REGION must NOT filter out.
    fuller = segs((0, "one thing happened"), (3, "then another"),
                  (6, "and a third for two hundred pounds"), (9, "and a fourth"))
    _, regions, census = align(clean, fuller, MIN_REGION)
    check("a clause only one decode heard is substantial", len(regions), 1)
    check("...and is counted as tokens the other lacks", census["only_other"], 4)
    check("...and the baseline side is empty", regions[0]["base"], "")

    # Two decodes that share only common words must not score as agreement.
    unrelated = segs((0, "a completely different set of words"),
                     (3, "with a shared article or two"),
                     (6, "but not the same content"), (9, "at all here"))
    ratio, _, _ = align(clean, unrelated, MIN_REGION)
    check("unrelated renderings fall under the floor", ratio < MIN_RATIO, True)

    # EVERY CASE BELOW IS AN ATTACK THAT PASSED. The fixtures that found them
    # were built in a scratch directory that no longer exists, so the shapes live
    # here, where they travel with the package.
    def many(count: int, text, step: float = 2.0) -> list[dict]:
        return [{"start": i * step, "end": i * step + step,
                 "text": text(i) if callable(text) else text}
                for i in range(count)]

    # Interleaved: one stuck line every other segment. Longest run 1, distinct
    # ratio 0.5006 -- over any distinct-ratio floor worth setting.
    interleaved = [s for i in range(400)
                   for s in ({"start": i * 4.0, "end": i * 4.0 + 2.0,
                              "text": "thanks for watching and please subscribe"},
                             {"start": i * 4.0 + 2.0, "end": i * 4.0 + 4.0,
                              "text": f"a real sentence number {i} with words in it"})]
    check("an interleaved loop has no run at all", longest_run(interleaved)[0], 1)
    check("...and is caught by the share of one text",
          round(top_share(interleaved)[0], 2), 0.5)
    defects, _ = check_rendering("v", interleaved, 1600.0, MAX_RUN, MAX_GAP,
                                 MIN_DISTINCT)
    check("...as a defect", sum("E-TS-DOMINANT" in d for d in defects), 1)

    # A counter on each repeat defeats exact equality entirely.
    counted = many(600, lambda i: f"and we can get another two percent of gdp {i}")
    check("a counter makes every repeat distinct",
          longest_run(counted)[0], 1)
    check("...and near-identity still sees it", near_run(counted), 600)
    defects, _ = check_rendering("v", counted, 1200.0, MAX_RUN, MAX_GAP,
                                 MIN_DISTINCT)
    check("...as a defect", sum("E-TS-NEARLOOP" in d for d in defects), 1)

    # One second of speech every twenty: no hole exceeds the gap limit.
    sawtooth = [{"start": i * 20.0, "end": i * 20.0 + 1.0,
                 "text": f"a fragment number {i} of what was said"}
                for i in range(60)]
    check("a sawtooth transcript trips no gap", gaps(sawtooth, MAX_GAP), [])
    check("...and covers almost nothing", round(coverage(sawtooth, 1200.0), 2), 0.05)
    defects, _ = check_rendering("v", sawtooth, 1200.0, MAX_RUN, MAX_GAP,
                                 MIN_DISTINCT)
    check("...as a defect", sum("E-TS-SPARSE" in d for d in defects), 1)

    # One absurd segment used to blind every later check by raising a maximum.
    blinded = ([{"start": 0.0, "end": 7200.0, "text": "a segment claiming the file"}]
               + [{"start": 3000.0 + i * 3.0, "end": 3003.0 + i * 3.0,
                   "text": f"words number {i} spoken here"} for i in range(30)])
    check("an absurd segment is not believed", segment_end(blinded[0]), 120.0)
    check("...so the holes after it are visible again",
          len(gaps(blinded, MAX_GAP)) > 0, True)
    defects, _ = check_rendering("v", blinded, 7200.0, MAX_RUN, MAX_GAP,
                                 MIN_DISTINCT)
    check("...and it is named", sum("E-TS-SEGMENT" in d for d in defects), 1)

    # Fluent, lively, and empty: permuted filler with a counter on it.
    filler = many(400, lambda i: f"yeah right you know {i}", step=3.0)
    check("permuted filler looks entirely distinct",
          len({normalise(s['text']) for s in filler}), 400)
    defects, _ = check_rendering("v", filler, 1200.0, MAX_RUN, MAX_GAP,
                                 MIN_DISTINCT)
    check("...and is caught on vocabulary",
          sum("E-TS-VOCABULARY" in d for d in defects), 1)
    check("a number is not vocabulary", content_tokens("yeah right 417"),
          ["yeah", "right"])

    # A one-token divergence is the bottom of any size ranking.
    said = many(40, lambda i: f"sentence number {i} about the business")
    flipped = [dict(s) for s in said]
    flipped[20] = dict(flipped[20], text="sentence number 20 about not the business")
    ratio, regions, census = align(said, flipped, MIN_REGION)
    check("one flipped word barely moves the ratio", ratio > 0.99, True)
    check("...and is under the size floor", census["substantial"], 0)
    check("...but is shown anyway", len(regions), 1)
    check("...marked as meaning", regions[0]["salient"], True)
    check("a negation is salient", is_salient(["not"]), True)
    check("a number is salient", is_salient(["1994"]), True)
    check("ordinary words are not", is_salient(["the", "business"]), False)

    # Two names for one decode is one witness.
    check("the same rendering fingerprints the same",
          fingerprint(said), fingerprint([dict(s) for s in said]))
    check("a different one does not", fingerprint(said) == fingerprint(flipped),
          False)

    # ...and none of the above may fire on a healthy transcript. The first
    # attempt at this fixture repeated a handful of words 400 times and tripped
    # the vocabulary floor, which is the check working and the fixture lying;
    # real speech is wide, so the fixture has to be.
    def coined(n: int) -> str:
        letters, out = "abcdefghijklmnopqrstuvwxyz", ""
        n += 1
        while n:
            n, remainder = divmod(n - 1, 26)
            out = letters[remainder] + out
        return out

    healthy = many(400, lambda i: f"{coined(3 * i)} {coined(3 * i + 1)} "
                                  f"{coined(3 * i + 2)} and the rest of it",
                   step=3.0)
    defects, _ = check_rendering("v", healthy, 1200.0, MAX_RUN, MAX_GAP,
                                 MIN_DISTINCT)
    check("a healthy transcript trips none of them", defects, [])

    check("a single rendering has no witness",
          witness_defect(1), [f"[00:00] E-TS-SINGLE-WITNESS one decode was "
                              f"supplied; a lone decode has nothing to be "
                              f"checked against, and both times a decode "
                              f"collapsed the collapse was found by a second "
                              f"one"])
    check("two renderings do not raise it", witness_defect(2), [])

    print(f"# selftest OK ({cases} cases)")
    return 0


def witness_defect(count: int) -> list[str]:
    """Kept out of main() so the selftest can assert on the exact wording."""
    if count >= 2:
        return []
    return ["[00:00] E-TS-SINGLE-WITNESS one decode was supplied; a lone decode "
            "has nothing to be checked against, and both times a decode "
            "collapsed the collapse was found by a second one"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("renderings", nargs="*", type=Path,
                    help="transcripts of the SAME audio; the first is baseline")
    ap.add_argument("--duration", type=float,
                    help="seconds of audio, to catch a truncated tail")
    ap.add_argument("--max-run", type=int, default=MAX_RUN)
    ap.add_argument("--max-gap", type=float, default=MAX_GAP)
    ap.add_argument("--min-distinct", type=float, default=MIN_DISTINCT)
    ap.add_argument("--min-ratio", type=float, default=MIN_RATIO)
    ap.add_argument("--min-region", type=int, default=MIN_REGION)
    ap.add_argument("--regions", type=int, default=REGIONS_SHOWN,
                    help="divergent regions to print, largest first")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if args.selftest:
        return selftest()
    if not args.renderings:
        ap.print_usage(sys.stderr)
        return 2

    loaded: list[tuple[str, list[dict]]] = []
    for path in args.renderings:
        try:
            loaded.append((path.name, load_segments(path)))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"{PROG}: {path}: {exc}", file=sys.stderr)
            return 2

    defects = witness_defect(len(loaded))
    # A witness that is the same witness twice is one witness wearing two names.
    # The same file passed as both renderings scored 1.0000 and cleared the
    # single-witness check, which is a fabricated second opinion.
    seen: dict[str, str] = {}
    for name, segments in loaded:
        mark = fingerprint(segments)
        if mark in seen:
            defects.append(
                f"{name}[00:00] E-TS-SAME-WITNESS identical to {seen[mark]}; "
                f"one decode named twice is not two decodes, and the agreement "
                f"between them measures nothing")
        seen.setdefault(mark, name)

    for name, segments in loaded:
        found, census = check_rendering(name, segments, args.duration,
                                        args.max_run, args.max_gap,
                                        args.min_distinct)
        defects.extend(found)
        print(f"# {census['name']}: {census['segments']} segments, "
              f"{census['distinct']} distinct, {census['words']} words, "
              f"longest run {census['run']}, near-run {census['near']}, "
              f"top text {census['top_share']:.1%}, "
              f"coverage {census['coverage']:.0%}, "
              f"{census['per_minute']:.1f} distinct words/min, "
              f"{census['gaps']} gap(s), ends {hms(census['last_end'])}"
              f"{'' if census['enough'] else ' [too short for the shares above]'}",
              file=sys.stderr)

    base_name, base = loaded[0]
    for name, segments in loaded[1:]:
        ratio, regions, census = align(base, segments, args.min_region)
        print(f"# {base_name} vs {name}: ratio {ratio:.4f}, "
              f"{census['regions']} divergent region(s), "
              f"{census['substantial']} at {args.min_region}+ tokens, "
              f"{census['salient']} touching a negation or a number, "
              f"{census['base_tokens']} vs {census['other_tokens']} tokens, "
              f"{census['only_base']} only in the baseline, "
              f"{census['only_other']} only in {name}", file=sys.stderr)
        if ratio < args.min_ratio:
            defects.append(
                f"{name}[00:00] E-TS-DIVERGENT whole-file agreement with "
                f"{base_name} is {ratio:.4f}, under {args.min_ratio}; these two "
                f"decodes are not renderings of the same words, so neither can "
                f"be quoted until the audio settles it")
        for region in regions[:args.regions]:
            print(f"[{hms(region['at'])}] {'MEANING' if region['salient'] else region['tag']:<7} "
                  f"{base_name}: {region['base'][:70] or '(nothing)'} "
                  f"|| {name}: {region['other'][:70] or '(nothing)'}")
        # NAMED, not silently dropped. A cap that hides the region that mattered
        # is the same failure as not printing it: the price that existed in one
        # decode only was region 23 of 23 by size, under a cap of 10.
        if len(regions) > args.regions:
            print(f"# {len(regions) - args.regions} more region(s) not shown; "
                  f"raise --regions to see them", file=sys.stderr)

    for defect in defects:
        print(defect)
    print(f"# {len(loaded)} rendering(s), {len(defects)} defect(s)",
          file=sys.stderr)
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
