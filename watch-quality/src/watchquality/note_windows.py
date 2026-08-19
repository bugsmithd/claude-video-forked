#!/usr/bin/env python3
"""The window boundaries a long video is written from, printed before anyone writes.

One context holding a two-hour transcript and a hundred frames compresses the
middle of the video hardest, and the compression is invisible: partial lists get
presented as whole ones, and a stretch that carried three anecdotes comes back as
one sentence. The fix is to write a long video in windows. The boundaries of
those windows decide what gets dropped, so THIS IS A SCRIPT AND NOT A MODEL.

If a model picks the boundaries, the outline it produces is an unaudited
compression that has already decided what will be lost, and nothing downstream
can see what it decided. Here the boundaries come from the transcript's own
segments and two numbers. They are deterministic, printable, and re-derivable by
anyone who doubts a window -- which makes an outline something that can be
checked against them rather than taken on trust. The labelling of a window --
what it is about, how many claims it is worth -- remains a judgement; the cutting
does not.

WINDOWS HERE OVERLAP ON PURPOSE, and that is the opposite of the decode windows
the transcript itself was produced with. A decode window discards its overlap,
because two copies of a sentence in one transcript is a defect. A note window
KEEPS its overlap, because a claim made across a boundary should be seen twice
and reconciled once -- deduped at merge, by anchor, keeping the fuller
rendering. Two windows that never see the same words have a seam nobody read.

Usage:
    wq-note-windows TRANSCRIPT [--window 600] [--overlap 90] [--json]
    wq-note-windows TRANSCRIPT --chapters video.info.json
    wq-note-windows --selftest

TRANSCRIPT may be WebVTT, whisper.cpp `-oj` JSON, Whisper `verbose_json`, or a
bare list of {start, end, text}.

Exit: 0 windows printed, 1 a window came out empty, 2 usage or unreadable input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .transcript_align import RE_WORD, hms, load_segments

PROG = "note_windows.py"

# Eight to twelve minutes is the span a single pass reads without visibly
# thinning; ten is the middle of it. The dial exists because the right answer
# differs between a lecture and an interview, not because the default is a guess.
WINDOW_SECONDS = 600.0
# Shared between neighbours, so a claim made across a seam is written by both
# windows and reconciled at merge. Below about a minute a sentence can begin in
# one window and land its point in the next with neither having both halves.
OVERLAP_SECONDS = 90.0
# One window holding more than this share of a multi-window plan means the split
# did not happen. An adversarial lane produced it by setting every segment's
# start to zero: window 1 took all 360 segments over a full hour, which is
# exactly the single overloaded context the windows exist to prevent, reported
# as a clean plan of seven windows.
OVERFULL_SHARE = 0.5
# Orphans printed one by one before the rest are counted. Ten names the problem;
# eight hundred would bury the window table that explains it.
ORPHANS_SHOWN = 10


def plan(segments: list[dict], window_seconds: float,
         overlap_seconds: float, duration: float | None = None) -> list[dict]:
    """Overlapping windows over a transcript, snapped to segment boundaries.

    Nominal boundaries come from the clock; the emitted ones come from the
    segments, because a window that starts mid-sentence is a window whose first
    claim has no beginning. Every window is a contiguous run of segments, and
    consecutive windows share the segments inside the overlap.

    WHAT IS COVERED IS THE TRANSCRIPT, NOT THE CLOCK. The nominal windows tile
    [0, end] with no hole, so every SEGMENT lands in at least one window; the
    emitted start and end are a segment's, so a stretch the transcript never
    witnessed is inside no window's printed span, and silence between segments
    is not covered because there is nothing there to cover. `duration` extends
    the last window to a declared length, for a recording that runs on past its
    final segment -- without it the tail after the last word is unwritten, and
    nothing else would say so.

    A final window that would add less than `overlap_seconds` nobody else has is
    absorbed into its predecessor rather than dispatched: a 40-second window
    costs a whole agent and returns almost nothing. Absorbing extends its
    predecessor, which can therefore run up to window + overlap long.
    """
    if window_seconds <= 0 or overlap_seconds < 0 or window_seconds - overlap_seconds <= 0:
        raise ValueError(
            f"an overlap of {overlap_seconds}s in a {window_seconds}s window "
            f"leaves no stride; windows would not advance")
    if not segments:
        return []
    stride = window_seconds - overlap_seconds

    # A malformed segment whose end precedes its start must still be placed, so
    # the file's length is the largest number in it either way.
    total = max(max(s["end"] for s in segments),
                max(s["start"] for s in segments))
    if duration is not None:
        total = max(total, duration)
    starts: list[float] = []
    at = 0.0
    while at < total:
        starts.append(at)
        at += stride
    # The previous window already reaches starts[-1] + overlap, so the last
    # window's own contribution is what lies beyond that.
    if len(starts) > 1 and total - starts[-1] - overlap_seconds < overlap_seconds:
        starts.pop()

    out: list[dict] = []
    for index, start in enumerate(starts):
        end = total if index == len(starts) - 1 else min(total, start + window_seconds)
        # Inclusive at both ends. A zero-length segment, and one sitting exactly
        # on a boundary, belongs to a window rather than to nothing; these
        # windows overlap by design, so claiming a segment twice at a seam is
        # the intended behaviour and losing it is not.
        members = [i for i, s in enumerate(segments)
                   if s["start"] <= end and s["end"] >= start]
        row = _row(index, [segments[i] for i in members], start, end)
        row["members"] = members
        out.append(row)
    return out


def orphans(segments: list[dict], windows: list[dict]) -> list[int]:
    """Indices of segments that no window claimed.

    MEMBERSHIP, not geometry. An earlier version asked whether each segment's
    span fell inside some window's printed span, which a segment whose end
    precedes its start satisfies vacuously -- so a segment in none of the seven
    windows was reported as an orphan count of zero.
    """
    claimed: set[int] = set()
    for window in windows:
        claimed.update(window.get("members", ()))
    return [i for i in range(len(segments)) if i not in claimed]


def _row(index: int, rows: list[dict], start: float, end: float) -> dict:
    """One window's printed shape.

    min and max rather than rows[0] and rows[-1]: a caller may hand over
    segments in any order, and a window reporting the first-listed segment's
    start as its own would be quietly wrong rather than loudly so.
    """
    return {
        "window": index + 1,
        "start": round(min((s["start"] for s in rows), default=start), 2),
        "end": round(max((s["end"] for s in rows), default=end), 2),
        "segments": len(rows),
        "words": sum(len(RE_WORD.findall(s["text"].lower())) for s in rows),
        "first": min(rows, key=lambda s: s["start"])["text"] if rows else "",
        "last": max(rows, key=lambda s: s["start"])["text"] if rows else "",
    }


def plan_from_chapters(segments: list[dict], chapters: list[dict]) -> list[dict]:
    """Windows from the uploader's own chapter marks.

    Where they exist these beat any arithmetic: the uploader has already marked
    where a subject ends, and a window that ends where the subject ends is one
    whose claims do not straddle. They are used as given, with no overlap added
    -- a chapter boundary is a real boundary, not an artefact of a stride, so
    there is no seam to cover.
    """
    out: list[dict] = []
    for index, chapter in enumerate(chapters):
        start = float(chapter.get("start_time") or 0.0)
        end = float(chapter.get("end_time") or 0.0)
        members = [i for i, s in enumerate(segments)
                   if s["start"] <= end and s["end"] >= start]
        row = _row(index, [segments[i] for i in members], start, end)
        row["members"] = members
        row["title"] = (chapter.get("title") or "").strip()
        out.append(row)
    return out


def read_chapters(path: Path) -> list[dict]:
    """Chapters out of a yt-dlp `.info.json`, or an empty list if it has none."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [c for c in (raw.get("chapters") or []) if isinstance(c, dict)]


def selftest() -> int:
    cases = 0

    def check(label: str, got, want) -> None:
        nonlocal cases
        cases += 1
        if got != want:
            raise AssertionError(f"{label}: got {got!r}, want {want!r}")

    def segs(count: int, step: float = 10.0) -> list[dict]:
        return [{"start": i * step, "end": i * step + step,
                 "text": f"segment number {i} said a few words"}
                for i in range(count)]

    short = segs(20)                      # 200 seconds
    windows = plan(short, 600.0, 90.0)
    check("a short file is one window", len(windows), 1)
    check("...covering all of it", (windows[0]["start"], windows[0]["end"]),
          (0.0, 200.0))
    check("...and every segment", windows[0]["segments"], 20)

    long = segs(360)                      # 3600 seconds
    windows = plan(long, 600.0, 90.0)
    check("an hour at a ten-minute stride", len(windows), 7)
    check("the first window starts at zero", windows[0]["start"], 0.0)
    check("the last window ends at the end", windows[-1]["end"], 3600.0)

    # No hole between neighbours, and a real shared seam.
    for before, after in zip(windows, windows[1:]):
        check(f"window {after['window']} starts before {before['window']} ends",
              after["start"] < before["end"], True)
    # At least the overlap, and a little more: the boundary test is inclusive,
    # so the segments sitting on a nominal edge are shared by both neighbours
    # rather than assigned to one of them. Extra shared context at a seam is the
    # safe direction; a seam nobody read is the unsafe one.
    check("the seam is at least the overlap",
          windows[0]["end"] - windows[1]["start"] >= 90, True)

    # THE CLAIM IS ABOUT SEGMENTS, NOT SECONDS. An earlier version of this case
    # walked the clock over a gapless fixture, so it could only ever pass: a
    # transcript with a hole in it has seconds no window covers, and there is
    # nothing there to cover. What must hold is that no segment falls out.
    check("no segment falls outside every window", orphans(long, windows), [])
    gappy = [{"start": 0.0, "end": 10.0, "text": "first thing said"},
             {"start": 2400.0, "end": 2410.0, "text": "much later"},
             {"start": 4000.0, "end": 4010.0, "text": "later still"}]
    check("a transcript full of holes still loses no segment",
          orphans(gappy, plan(gappy, 600.0, 90.0)), [])

    check("the words are counted, not estimated", windows[0]["words"],
          sum(len(RE_WORD.findall(s["text"].lower()))
              for s in long if s["start"] <= 600.0))

    # A stub tail is absorbed rather than dispatched.
    stubby = segs(104)                    # 1040s: a 20s tail after two strides
    check("a thin tail does not become its own window",
          len(plan(stubby, 600.0, 90.0)), 2)
    check("...and is still covered", plan(stubby, 600.0, 90.0)[-1]["end"], 1040.0)
    # 1110s: the tail window would start at 1020 and add nothing at all, because
    # the window before it already reaches 1110.
    check("a tail with no new content at all is absorbed",
          len(plan(segs(111), 600.0, 90.0)), 2)
    # 1290s: 270 past the last start, of which 90 is shared -- 180 of new
    # content, which is worth an agent.
    check("a tail that does add content is kept",
          len(plan(segs(129), 600.0, 90.0)), 3)

    # A recording that runs on past its last word.
    check("without a duration the tail after the last word is not planned",
          plan(short, 600.0, 90.0)[-1]["end"], 200.0)
    check("with one, the last window reaches it",
          plan(short, 600.0, 90.0, duration=900.0)[-1]["end"], 900.0)

    # Malformed and degenerate inputs must land somewhere or be reported, never
    # silently vanish.
    zero_length = [{"start": 0.0, "end": 0.0, "text": "a stamp with no span"},
                   {"start": 0.0, "end": 10.0, "text": "an ordinary segment"},
                   {"start": 20.0, "end": 20.0, "text": "another bare stamp"}]
    check("zero-length segments are placed",
          plan(zero_length, 600.0, 90.0)[0]["segments"], 3)
    # THE WHOLE SEGMENT LIST, not a slice of it. This case previously excluded
    # the malformed segment from its own assertion, so it passed while that
    # segment sat in none of the windows -- the exact loss the check exists for.
    reversed_seg = [{"start": 0.0, "end": 10.0, "text": "ordinary"},
                    {"start": 500.0, "end": 400.0, "text": "ends before it starts"}]
    check("a segment whose end precedes its start is still placed",
          orphans(reversed_seg, plan(reversed_seg, 600.0, 90.0)), [])
    check("...and is counted", plan(reversed_seg, 600.0, 90.0)[0]["segments"], 2)
    # Membership, not geometry: a window's printed span cannot answer this.
    faked = [dict(w, members=[]) for w in plan(reversed_seg, 600.0, 90.0)]
    check("a window that claims nothing orphans everything",
          len(orphans(reversed_seg, faked)), 2)

    check("an empty transcript plans nothing", plan([], 600.0, 90.0), [])
    for bad in ((90.0, 90.0), (0.0, 10.0), (-5.0, 999.0), (600.0, -30.0)):
        try:
            plan(long, bad[0], bad[1])
            raise AssertionError(f"{bad} must not plan windows")
        except ValueError:
            cases += 1

    # Determinism is the whole claim of this file: same input, same boundaries.
    check("the same transcript plans the same windows",
          plan(long, 600.0, 90.0), plan(long, 600.0, 90.0))

    # Every segment at second zero, which is what a decoder with broken offsets
    # emits. The plan looked like seven windows and window 1 held all of them --
    # the one overloaded context these windows exist to break up, wearing a
    # table of boundaries.
    timeless = [{"start": 0.0, "end": 3600.0, "text": f"segment number {i}"}
                for i in range(360)]
    stacked = plan(timeless, 600.0, 90.0)
    check("a timeless transcript still plans windows", len(stacked) > 1, True)
    check("...and one of them holds everything",
          stacked[0]["segments"], 360)
    check("...which is a distinct-start-count of one",
          len({s["start"] for s in timeless}), 1)

    chapters = [{"start_time": 0.0, "end_time": 1200.0, "title": "The setup"},
                {"start_time": 1200.0, "end_time": 3600.0, "title": "The argument"}]
    from_chapters = plan_from_chapters(long, chapters)
    check("a chapter is a window", len(from_chapters), 2)
    check("...titled by the uploader", from_chapters[1]["title"], "The argument")
    # The emitted end is a SEGMENT's, not the chapter mark itself: a sentence
    # running across the mark is written by the chapter it began in.
    check("...and ends on the segment that crosses the mark",
          from_chapters[0]["end"], 1210.0)
    check("...which is where the next chapter's first segment already began",
          from_chapters[1]["start"] <= 1200.0, True)

    # THE COMMAND ITSELF, not only the arithmetic under it. Every case above
    # calls plan() or orphans() directly, and a version of this file shipped
    # with main() raising TypeError on its own summary line: it printed the
    # window table, then died before its exit code meant anything. A replay
    # that only asserts a non-zero exit reads that crash as a defect found.
    import contextlib
    import io
    import tempfile

    def run(rows: list[dict], *flags: str) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "transcript.json"
            path.write_text(json.dumps(rows), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main([str(path), *flags])
        return code, out.getvalue(), err.getvalue()

    code, out, err = run(long)
    check("a healthy transcript exits clean", code, 0)
    check("...naming no defect", "E-WIN-" in out, False)
    check("...and counting its orphans on stderr",
          "0 of 360 segments in no window" in err, True)
    check("...having printed a window per planned row",
          out.count("\n") - 2, len(plan(long, 600.0, 90.0)))

    code, out, _ = run(timeless)
    check("a timeless transcript exits 1", code, 1)
    check("...by name", "E-WIN-TIMELESS" in out, True)
    check("...and says the split did not happen", "E-WIN-OVERFULL" in out, True)

    code, out, _ = run(long, "--json")
    check("--json exits clean", code, 0)
    check("...and is parseable", len(json.loads(out)), len(plan(long, 600.0, 90.0)))

    print(f"# selftest OK ({cases} cases)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("transcript", nargs="?", type=Path)
    ap.add_argument("--window", type=float, default=WINDOW_SECONDS,
                    help="seconds per window (default %(default)s)")
    ap.add_argument("--overlap", type=float, default=OVERLAP_SECONDS,
                    help="seconds shared with each neighbour (default %(default)s)")
    ap.add_argument("--duration", type=float,
                    help="seconds of recording, if it runs past its last word")
    ap.add_argument("--chapters", type=Path, metavar="INFO_JSON",
                    help="use the uploader's chapters from a yt-dlp .info.json")
    ap.add_argument("--json", action="store_true", help="machine-readable plan")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if args.selftest:
        return selftest()
    if not args.transcript:
        ap.print_usage(sys.stderr)
        return 2

    try:
        segments = load_segments(args.transcript)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"{PROG}: {args.transcript}: {exc}", file=sys.stderr)
        return 2

    source = f"{args.window:.0f}s windows, {args.overlap:.0f}s overlap"
    if args.chapters:
        try:
            chapters = read_chapters(args.chapters)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"{PROG}: {args.chapters}: {exc}", file=sys.stderr)
            return 2
        if chapters:
            windows = plan_from_chapters(segments, chapters)
            source = f"{len(chapters)} uploader chapters"
        else:
            # Said out loud. A silent fall-through to arithmetic would leave the
            # operator believing the boundaries came from the uploader.
            print(f"{PROG}: {args.chapters} declares no chapters — "
                  f"falling back to {source}", file=sys.stderr)
            windows = plan(segments, args.window, args.overlap, args.duration)
    else:
        try:
            windows = plan(segments, args.window, args.overlap, args.duration)
        except ValueError as exc:
            print(f"{PROG}: {exc}", file=sys.stderr)
            return 2

    if args.json:
        print(json.dumps(windows, indent=2))
    else:
        print(f"# {args.transcript.name}: {len(segments)} segments, {source}")
        print("window\tstart\tend\tsegments\twords\topens with")
        for w in windows:
            title = w.get("title")
            opening = f"{title} :: " if title else ""
            print(f"{w['window']}\t{hms(w['start'])}\t{hms(w['end'])}\t"
                  f"{w['segments']}\t{w['words']}\t{opening}{w['first'][:60]}")

    defects = [f"[{hms(w['start'])}] E-WIN-EMPTY window {w['window']} holds no "
               f"segment; a boundary landed in a stretch the transcript never "
               f"witnessed"
               for w in windows if not w["segments"]]
    # COUNTED, not assumed. A window plan that quietly loses a segment loses
    # whatever that segment said, and the loss is invisible in a table of
    # boundaries that all look reasonable.
    lost = orphans(segments, windows)
    defects.extend(
        f"[{hms(segments[i]['start'])}] E-WIN-ORPHAN a segment lies in no "
        f"window; nothing would be written from it"
        for i in lost[:ORPHANS_SHOWN])
    # Said out loud. A printed list that stops at ten and does not say so reads
    # as ten orphans when it is the first ten of hundreds.
    if len(lost) > ORPHANS_SHOWN:
        defects.append(
            f"[00:00] E-WIN-ORPHAN {len(lost) - ORPHANS_SHOWN} further orphaned "
            f"segment(s) are not listed above")
    # A plan whose windows all collapse onto one is the single overloaded
    # context this file exists to break up, wearing a table of boundaries. It
    # happens when a decoder emits every segment at the same second.
    distinct_starts = len({s["start"] for s in segments})
    if segments and distinct_starts < max(2, len(segments) // 100):
        defects.append(
            f"[00:00] E-WIN-TIMELESS {distinct_starts} distinct start time(s) "
            f"across {len(segments)} segments; this transcript has no usable "
            f"timeline, so any window plan over it is arithmetic on one number")
    biggest = max((w["segments"] for w in windows), default=0)
    if len(windows) > 1 and biggest > len(segments) * OVERFULL_SHARE:
        defects.append(
            f"[00:00] E-WIN-OVERFULL one window holds {biggest} of "
            f"{len(segments)} segments; the plan says it split the video and "
            f"the numbers say it did not")
    for defect in defects:
        print(defect)
    print(f"# {len(windows)} window(s), "
          f"{sum(w['words'] for w in windows)} words including overlap, "
          f"{sum(1 for w in windows if not w['segments'])} empty, "
          f"{len(lost)} of {len(segments)} segments in no window",
          file=sys.stderr)
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
