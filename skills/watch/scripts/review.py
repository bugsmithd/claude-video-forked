#!/usr/bin/env python3
"""Build the review lane briefs for a finished note, from its run.json.

THE GATES CANNOT TELL WHETHER A NOTE IS RIGHT. They check that anchors resolve,
that quotes are verbatim, that classes are present and that counts match their
lists. A wrong attribution, a dropped hedge and a confidently stated inference
pass every one of them. The only thing that catches those is a second reader who
did not write the note, and more than one of them, because a single reader
agrees with whatever they read first.

So this writes the briefs. It does not dispatch anything: a skill that spawns
agents because it felt like it is a skill people uninstall, and the caller
decides whether three fresh readers are worth their cost on this particular
note. `REVIEW.md` is the human half.

WHY THE FRAMES ARE SPLIT INTO BATCHES HERE rather than left to the reader. A
lane that opens a hundred frames in one message and then works from a separate
list of file names is pairing images with names by position, and a single
dropped frame shifts the whole tail by one with nothing to notice it. Each batch
below is small enough that its names sit beside its own images.

    review.py <run.json> <note.md>          # writes <run-dir>/review/
    review.py <run.json> <note.md> --print  # brief to stdout, writes nothing
    review.py --selftest
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Twenty is the batch the note contract already asks a writer for, and a
# reviewer reading in a different rhythm than the writer wrote in finds
# differences that are artefacts of the rhythm.
BATCH = 20

LANES: dict[str, dict] = {
    "facts": {
        "question": "Is each claim TRUE against the recording?",
        "frames": True,
        "brief": """Re-open the evidence behind every claim and try to break it.

For each row, go back to the second it names and read what is actually there.
Check three things and report only what fails:

1. The quote. A `SPOKEN` row's quoted string must be word for word in the
   transcript, in reach of its anchor. A near-quote is a finding.
2. The attribution. Where a row names who said something, does the recording
   settle it? Two voices and no labels means most attributions cannot be
   settled from the transcript at all; say so rather than agreeing.
3. The claim itself. Does the passage at that second support what the row says,
   or something weaker, or something else?

An `ON-SCREEN` row is checked against the frame at its second, not against the
transcript and not against your memory of a neighbouring frame.""",
    },
    "quality": {
        "question": "Is each claim carried HONESTLY?",
        "frames": False,
        "brief": """Read the note against itself. You are looking for claims
that are true in outline and overstated in delivery.

1. Dropped hedges. "Maybe part of it is" becoming "the real formula is". A
   number offered as acceptable becoming the number complained about. This is
   the most common defect there is and no gate can see it.
2. Upgraded inferences. An `INFERRED` row sitting beside a `SPOKEN` one and
   quietly restating it as fact. Read every `INFERRED` row asking what the
   recording would have to contain for it to be `SPOKEN`, and whether the note
   is behaving as if it does.
3. Recogniser artefacts presented as speech. A transcript's punctuation is a
   guess and its rare words are often wrong; a note that quotes a mangled term
   as if it were the speaker's own word is carrying an artefact as evidence.
4. Prose that outruns its rows. A thesis or a summary asserting something no
   row underneath it carries.""",
    },
    "coverage": {
        "question": "What did the note LEAVE OUT?",
        "frames": True,
        "brief": """The other lanes grade what is on the page. You grade what
is not. Work from the recording forward, not from the note outward, or you will
only confirm the note's own shape.

1. Walk the whole runtime. Name every argument, example, figure and named
   entity in it, then check each against the note. Report the ones missing.
2. Look for long stretches with no anchor at all. A gap is not proof of an
   omission, but it is where omissions live.
3. Check the frames the note never cites. A frame extracted and referenced
   nowhere is either a redundant frame or a missed one, and which it is can
   only be settled by looking.
4. Judge the note's own closing list of what it deliberately left out. Is it
   credible for the material, or is it short?""",
    },
}

REFUTE = """STANCE: REFUTE BY DEFAULT. Your job is not to produce findings. It
is to try to break the note and report only what actually broke. Before you
write a finding, argue the other side of it and see whether it survives. If you
are unsure, the finding does not go in the list — it goes in a short "could not
settle" section at the end, with what you would need in order to settle it.

A lane that returns twenty plausible findings is worth less than one that
returns three certain ones, because every finding costs a human a re-read and
a wrong one costs them their trust in the rest."""

DISPOSITION = """# Disposition

One row per finding. The verdict is the author's, not the lane's.

| id | lane | finding | verdict | what changed |
|---|---|---|---|---|

## The repair policy

A repair may strike a false statement, restore a dropped hedge, re-point a
wrong anchor, flag an unflagged recogniser artefact, or delete a row that is
not a claim.

**It may not write a claim the rows do not carry.** A lane reporting that
something is missing is reporting a gap, and filling that gap means going back
to the recording and writing a new row with its own anchor — which is authoring,
not repairing, and belongs in its own pass where it can be checked like any
other claim.

## Where lanes disagree

Two lanes reaching opposite conclusions about the same passage is information,
not a tie to be broken by majority. Go to the recording and settle it there,
and record which lane was wrong and why — that is how the briefs get better.
"""


RE_ANCHOR = re.compile(r"`\[(\d{1,2}:\d{2}(?::\d{2})?)\]`")


def anchor_seconds(text: str) -> list[int]:
    """Every `[MM:SS]` or `[H:MM:SS]` in the note, as whole seconds."""
    out = []
    for stamp in RE_ANCHOR.findall(text):
        parts = [int(p) for p in stamp.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        out.append(parts[0] * 3600 + parts[1] * 60 + parts[2])
    return out


def reach(note: Path, run: dict) -> dict | None:
    """How much of this note the RUN can answer, before any lane starts.

    A note is only fully reviewable against the run it was written from, and
    `review.py` will take any run.json handed to it. Pointing it at a
    re-capture of the same video is the easy mistake — same title, same
    duration, different frames — and the lanes discover it one claim at a time,
    each concluding UNTESTABLE without knowing the others did too. Counting it
    once, here, costs nothing and puts the number in every brief.

    None when the note cannot be read; a run is not blocked by a missing note.
    """
    try:
        text = note.read_text(encoding="utf-8")
    except OSError:
        return None
    anchors = anchor_seconds(text)
    if not anchors:
        return None
    frames = {int(f["seconds"]) for f in run.get("frames") or []}
    starts = {int(s) for s in run.get("transcript", {}).get("segment_starts") or []}
    return {
        "anchors": len(anchors),
        "framed": sum(1 for s in anchors if s in frames),
        "spoken": sum(1 for s in anchors if s in starts),
        "gap": widest_gap(sorted(frames), float(run.get("duration_seconds") or 0)),
    }


def widest_gap(frame_seconds: list[int], duration: float) -> tuple[int, int]:
    """The longest stretch of runtime with no frame in it, as (start, end).

    `(0, 0)` means there is nothing to measure — no frames and no duration —
    and callers must print nothing rather than print a zero-width gap. A run
    with no frames and a known duration correctly reports the whole runtime.
    """
    edges = [0, *frame_seconds, int(duration or 0)]
    worst = (0, 0)
    for a, b in zip(edges, edges[1:]):
        if b - a > worst[1] - worst[0]:
            worst = (a, b)
    return worst


def reach_section(stats: dict | None) -> list[str]:
    if not stats:
        return []
    n, framed, spoken = stats["anchors"], stats["framed"], stats["spoken"]
    lo, hi = stats["gap"]
    lines = [
        "",
        "## How much of the note this run can answer",
        "",
        f"- The note carries {n} anchor(s). {framed} of them fall on a second "
        f"this run kept a frame for; {spoken} fall on a transcript segment "
        f"start.",
    ]
    if hi > lo:
        lines.append(f"- The longest stretch of runtime with no frame at all is "
                     f"{lo}s to {hi}s.")
    if framed * 2 < n:
        lines.append(
            f"- **Most of this note's anchors have no frame in this run.** That "
            f"is a fact about the run, not about the note: a claim you cannot "
            f"reach is UNTESTABLE here and must not be reported as refuted. If "
            f"the note was written from a different capture of the same video, "
            f"say so in your findings rather than grading around it.")
    return lines


def load_run(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"review.py: cannot read {path}: {exc}")


def batches(frames: list[dict], size: int = BATCH) -> list[list[dict]]:
    return [frames[i:i + size] for i in range(0, len(frames), size)]


def frame_section(frames: list[dict]) -> str:
    if not frames:
        return ("No frames were kept for this run, so every claim in it rests "
                "on the transcript alone. Say so if the note implies otherwise.")
    groups = batches(frames)
    out = [f"{len(frames)} frame(s), in {len(groups)} batch(es) of at most "
           f"{BATCH}. **Read one batch per message**, so each file name sits "
           f"beside its own image. Each frame carries its second in its own "
           f"pixels: if the burned-in `t=` disagrees with the path you think "
           f"you are reading, trust the pixels and say so.", ""]
    for n, group in enumerate(groups, start=1):
        first, last = group[0]["seconds"], group[-1]["seconds"]
        out.append(f"### Batch {n} of {len(groups)} — {first:.0f}s to {last:.0f}s")
        out.extend(f"- `{f['path']}` (t={f['seconds']:.0f}s, "
                   f"{f.get('reason', 'selected')})" for f in group)
        out.append("")
    return "\n".join(out).rstrip()


def brief(lane: str, run: dict, note: Path, run_path: Path) -> str:
    spec = LANES[lane]
    anchors = run.get("transcript", {}).get("segment_starts") or []
    dropped = run.get("deduped_seconds") or []
    parts = [
        f"# Review lane: {lane}",
        "",
        f"**{spec['question']}**",
        "",
        f"SOURCE OF TRUTH: the recording, through `{run_path}` and the files it "
        f"names. The note under review is `{note}` — it is the thing being "
        f"judged, never the evidence for a judgement.",
        "",
        REFUTE,
        "",
        "## What to do",
        "",
        spec["brief"],
        "",
        "## The run",
        "",
        f"- Duration: {run.get('duration_seconds', 0):.0f}s",
        f"- Transcript: {run.get('transcript', {}).get('source') or 'none'}, "
        f"{len(anchors)} segment start(s) — these are the only seconds a claim "
        f"may be anchored to.",
    ]
    if dropped:
        parts.append(
            f"- {len(dropped)} second(s) were collapsed as near-identical to a "
            f"neighbouring frame and have no frame of their own. A claim about "
            f"what was on screen across them is legitimate; an `ON-SCREEN` "
            f"anchor at one of them is not.")
    parts += reach_section(reach(note, run))
    if spec["frames"]:
        parts += ["", "## Frames", "", frame_section(run.get("frames") or [])]
    parts += [
        "",
        "## What to return",
        "",
        "A numbered list of findings. Each one: the anchor, what the note says, "
        "what the recording says, and why the difference matters. Then a short "
        "`## Could not settle` section for anything you could not decide, "
        "naming what would decide it.",
        "",
        "Do not edit the note. Do not open the other lanes' output.",
    ]
    return "\n".join(parts) + "\n"


def write_all(run_path: Path, note: Path, out_dir: Path) -> list[Path]:
    run = load_run(run_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for lane in LANES:
        path = out_dir / f"lane-{lane}.md"
        path.write_text(brief(lane, run, note, run_path), encoding="utf-8")
        written.append(path)
    disposition = out_dir / "DISPOSITION.md"
    disposition.write_text(DISPOSITION, encoding="utf-8")
    written.append(disposition)
    return written


def selftest() -> int:
    failures = 0

    def check(name: str, ok: bool):
        nonlocal failures
        if not ok:
            failures += 1
            print(f"review.py: selftest FAIL {name}")

    frames = [{"seconds": float(i), "path": f"/f/{i}.jpg", "reason": "scene"}
              for i in range(45)]
    groups = batches(frames)
    check("batches are capped", all(len(g) <= BATCH for g in groups))
    check("batches lose nothing", sum(len(g) for g in groups) == 45)
    check("three batches for forty-five", len(groups) == 3)
    check("no frames is not a crash", batches([]) == [])

    run = {"duration_seconds": 90.0, "frames": frames,
           "transcript": {"source": "captions", "segment_starts": [0.0, 5.0]},
           "deduped_seconds": [7.0]}
    text = brief("facts", run, Path("n.md"), Path("run.json"))
    check("the stance is in the brief", "REFUTE BY DEFAULT" in text)
    check("frames reach a frames lane", "/f/0.jpg" in text)
    check("collapsed seconds are explained", "collapsed" in text)
    quality = brief("quality", run, Path("n.md"), Path("run.json"))
    check("a note-only lane gets no frame list", "/f/0.jpg" not in quality)
    check("the repair policy forbids authoring",
          "may not write a claim the rows do not carry" in DISPOSITION)

    check("anchors are read in both shapes",
          anchor_seconds("`[02:58]` and `[1:02:58]`") == [178, 3778])
    check("the widest gap is the widest one",
          widest_gap([1, 2, 50, 51], 60.0) == (2, 50))
    # A run that cannot reach the note it was handed says so in every brief,
    # because the alternative is three lanes each discovering it alone.
    reached = reach_section({"anchors": 10, "framed": 2, "spoken": 9,
                             "gap": (100, 200)})
    check("a run that cannot reach the note says so",
          any("no frame in this run" in line for line in reached))
    check("a run that can reach the note does not nag",
          not any("no frame in this run" in line for line in
                  reach_section({"anchors": 10, "framed": 9, "spoken": 9,
                                 "gap": (1, 2)})))
    check("a note that cannot be read does not block the briefs",
          reach(Path("/no/such/note.md"), run) is None)

    print(f"# 14 case(s), {failures} failure(s)")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="review.py")
    ap.add_argument("run", nargs="?", help="path to run.json")
    ap.add_argument("note", nargs="?", help="path to the note under review")
    ap.add_argument("--out-dir", default=None,
                    help="where the briefs go (default: <run dir>/review)")
    ap.add_argument("--lane", choices=sorted(LANES), default=None,
                    help="with --print, which lane to print")
    ap.add_argument("--print", dest="to_stdout", action="store_true",
                    help="print one brief and write nothing")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.run or not args.note:
        ap.error("run.json and note.md are both required")

    run_path, note = Path(args.run), Path(args.note)
    if args.to_stdout:
        print(brief(args.lane or "facts", load_run(run_path), note, run_path),
              end="")
        return 0

    out_dir = Path(args.out_dir) if args.out_dir else run_path.parent / "review"
    written = write_all(run_path, note, out_dir)
    print(f"# {len(written)} file(s) in {out_dir}")
    for path in written:
        print(f"- {path}")
    print()
    print("Dispatch one fresh-context agent per lane, in parallel, each with "
          "ONLY its own brief. A lane that can see another lane's findings "
          "agrees with them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
