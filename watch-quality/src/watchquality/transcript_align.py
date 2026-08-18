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
import json
import re
import sys
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

RE_WORD = re.compile(r"[a-z0-9']+")


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


def gaps(segments: list[dict], max_gap: float) -> list[tuple[float, float]]:
    """Stretches longer than max_gap with no segment in them: (start, end)."""
    out: list[tuple[float, float]] = []
    previous_end = 0.0
    for seg in segments:
        if seg["start"] - previous_end > max_gap:
            out.append((previous_end, seg["start"]))
        previous_end = max(previous_end, seg["end"], seg["start"])
    return out


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
    last_end = max((s["end"] for s in segments), default=0.0)

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
              "last_end": last_end}
    return defects, census


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
        regions.append({
            "tag": tag,
            "size": max(i2 - i1, j2 - j1),
            "at": a_times[i1] if i1 < len(a_times) else (
                a_times[-1] if a_times else 0.0),
            "base": " ".join(a_words[i1:i2]),
            "other": " ".join(b_words[j1:j2]),
        })

    substantial = [r for r in regions if r["size"] >= min_region]
    census = {"ratio": ratio, "regions": len(regions),
              "substantial": len(substantial),
              "base_tokens": len(a_words), "other_tokens": len(b_words),
              "only_base": sum(len(r["base"].split()) for r in regions),
              "only_other": sum(len(r["other"].split()) for r in regions)}
    return ratio, sorted(substantial, key=lambda r: -r["size"]), census


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
    for name, segments in loaded:
        found, census = check_rendering(name, segments, args.duration,
                                        args.max_run, args.max_gap,
                                        args.min_distinct)
        defects.extend(found)
        print(f"# {census['name']}: {census['segments']} segments, "
              f"{census['distinct']} distinct, {census['words']} words, "
              f"longest run {census['run']}, {census['gaps']} gap(s), "
              f"ends {hms(census['last_end'])}", file=sys.stderr)

    base_name, base = loaded[0]
    for name, segments in loaded[1:]:
        ratio, regions, census = align(base, segments, args.min_region)
        print(f"# {base_name} vs {name}: ratio {ratio:.4f}, "
              f"{census['regions']} divergent region(s), "
              f"{census['substantial']} at {args.min_region}+ tokens, "
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
            print(f"[{hms(region['at'])}] {region['tag']:<7} "
                  f"{base_name}: {region['base'][:70] or '(nothing)'} "
                  f"|| {name}: {region['other'][:70] or '(nothing)'}")

    for defect in defects:
        print(defect)
    print(f"# {len(loaded)} rendering(s), {len(defects)} defect(s)",
          file=sys.stderr)
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
