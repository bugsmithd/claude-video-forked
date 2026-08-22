#!/usr/bin/env python3
"""Run the per-note checks that no runner ever ran.

Three checks were built and then sat unreachable: how much of the recording a
note carried, whether a window plan over its rendering loses segments, and
whether two renderings of one recording agree. `watch-audit` could not reach any
of them, and the reason was mechanical rather than an oversight -- the five
gates it runs take `[--flags] [notes...]`, and these three take POSITIONAL
paths: a note AND its transcript, or a rendering, or two renderings. There was
nowhere to get the transcript from.

`oracle:` is where. It became a checked field on 2026-08-21, so a note now names
the rendering it was written against and the gate can open it. That is the whole
reason this module can exist now and could not before.

WHAT THIS DOES NOT DO. A note whose oracle names no rendering is SKIPPED, by
name, and skipping is not passing: the count is printed and the notes are
listed. Most notes predate the rule, so this gate reaches only a few of them,
and the census prints the two numbers rather than leaving a reader to guess.
No count is written here, because a number in a docstring goes stale the day
a note is added and the run's own census cannot. Reporting `0 defects` over a
corpus it could mostly not read would be the false clean bill every gate here
exists to refuse.

`transcript_align` is deliberately NOT run. It compares two renderings, and a
note declares one; running it against a single file would red every note in the
corpus for a condition none of them claims to meet. It belongs where the second
rendering is, which is the run directory, and that is a different gate.

Usage:
    watch-audit                       # runs this with the others
    python3 -m watchquality.note_gates --check
    python3 -m watchquality.note_gates --check notes/some-note.md

Exit: 0 clean, 1 a note carried a defect, 2 the gate could not run.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

from watchquality import note_coverage, note_windows, resolve_note, wq_policy

PROG = "note_gates.py"
GATE_FLAGS: tuple[str, ...] = ("--check",)
# The shape every defect line in this package carries: a timestamp, then the
# code. `spec/` is written against it, and it is what tells a defect apart
# from the report printed beside it.
#
# The POSITION is load-bearing. Matching a code anywhere in the line counted
# a report row as a finding, because `note_windows` ends each row of its plan
# with the opening transcript text of that window, and a talk about
# e-commerce spells the shape exactly.
RE_DEFECT_LINE = re.compile(
    r"^\[\d{1,2}:\d{2}(?::\d{2})?\]\s+E-[A-Z0-9]+(?:-[A-Z0-9]+)*(?:\s|$)")

POLICY = wq_policy.load()
# Notes these checks may not grade, keyed by note filename and dated. Empty
# with no policy, because a missing policy may not invent an exemption.
UNGRADED_NOTES: dict[str, str] = POLICY.ungraded_notes()


def rendering_for(note: Path, root: Path) -> tuple[Path | None, str]:
    """(the rendering this note declares, why not when there is none).

    The oracle is re-resolved here rather than trusted from the note gate's
    result, because a checker that inherits another checker's answer inherits
    its bugs as well -- and the two run in the same process, minutes apart, on
    a corpus that a `--write` run may have moved underneath them.
    """
    text, why = resolve_note.safe_read(note)
    if text is None:
        return None, f"unreadable: {why}"
    split = resolve_note.split_frontmatter(text)
    if split is None:
        return None, "no frontmatter, so no oracle field"
    frontmatter = split[0]
    rows = resolve_note.RE_NOTE_ORACLE.findall(frontmatter)
    if not rows or not rows[0].strip():
        return None, "names no rendering"
    vid = resolve_note.RE_VIDEO_ID.search(frontmatter)
    if not vid:
        return None, "declares no video id"
    hit = resolve_note.note_oracle_target(rows[0].strip(), note, root,
                                          vid.group(1))
    if hit is None:
        return None, f"names {resolve_note.oracle_token(rows[0])}, which opens nothing"
    # AND IT HAS TO BE THIS VIDEO'S. `check_oracle` asks two questions of the
    # field -- is this the note's own rendering, and can a gate read it -- and
    # this function asked only the second. So a note whose oracle was an
    # absolute path to ANOTHER video's transcript was graded against it, and
    # counted in the census as graded against its own: arithmetic defects about
    # a recording the note was never written from. The relatedness test is
    # `resolve_note`'s, called rather than restated, because two copies of one
    # question are two answers waiting to differ (V2 finding V2-10).
    if not resolve_note.oracle_names_run(hit, vid.group(1), root):
        return None, (f"names {resolve_note.oracle_token(rows[0])}, which is "
                      f"not a rendering of {vid.group(1)}")
    # A FILE THAT OPENS IS NOT A RENDERING. A run manifest sits beside the real
    # transcript, under the same video id, and opens -- so this gate was handed
    # one, could not read it, and reported a note that "cannot be graded", while
    # the oracle gate had already called the field fine. Two layers reporting
    # one seam, and the row that silenced them sat in a ledger for frozen notes.
    #
    # One finding, one owner: `check_oracle` refuses a value no gate can read,
    # and this gate skips the note. The skip is still not a pass -- the census
    # names every note it did not grade.
    from .transcript_align import load_segments
    try:
        if not load_segments(hit):
            return None, (f"names {resolve_note.oracle_token(rows[0])}, which "
                          f"reads as a transcript carrying no segments")
    except Exception as exc:  # noqa: BLE001 -- any reason it will not read
        return None, (f"names {resolve_note.oracle_token(rows[0])}, which no "
                      f"gate can read as a transcript: {exc}")
    return hit, ""


def _run(fn, argv: list[str]) -> tuple[int, list[str], list[str]]:
    """(exit code, the defect lines it printed, everything it printed).

    Driven in-process and captured, so a check that dies takes its own note
    down and not the run. `run_gate` in `audit` makes the same argument one
    level up; this is the same shape because the failure is the same shape.

    The third value is the EVIDENCE. A check can fail without spelling a
    code, and a caller that keeps only what it managed to classify reports
    the note as clean over a check that failed.
    """
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            code = fn(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
    except Exception as exc:  # noqa: BLE001 -- the message IS the handling
        return 2, [f"[00:00] E-NOTE-CHECK-RAISED {type(exc).__name__}: {exc}"], []
    # ONLY the defect lines. Both checks print a report as well -- a coverage
    # table, a window plan -- and a first version forwarded every row of it,
    # so a note with a clean plan and five windows was reported as carrying
    # five defects. A defect line is the one that OPENS with a code.
    printed = buf.getvalue().splitlines()
    lines = [ln for ln in printed if RE_DEFECT_LINE.match(ln)]
    return int(code or 0), lines, printed


def check_note(note: Path, root: Path) -> tuple[list[str], str | None]:
    """(defect lines, the reason this note was skipped)."""
    rel = note.relative_to(root) if note.is_relative_to(root) else note
    # A FROZEN NOTE CANNOT BE REPAIRED, so a real finding in one is permanent
    # and already known, and reporting it on every run teaches the reader to
    # skip the output. Dated, printed, ageing like every other ledger here: a
    # row cannot reach a note filed after the row's own date.
    # KEYED BY PATH, never by bare name. Two directories can hold one
    # filename, and a row that excuses "whatever is called this" is a pattern
    # nobody wrote and nobody dated.
    # AND NOT ON AN ABSENCE. This row returns before the whole per-note grading
    # layer -- every coverage code and every window code at once -- which makes
    # it the widest excuse in the policy. A filename with no date is the
    # weakest evidence in it, and the two do not go together (round-13 F3).
    excuse = UNGRADED_NOTES.get(str(rel))
    if resolve_note.excused(excuse, rel, undated=False):
        return [], f"excused by the ledger [{excuse}]"
    rendering, why = rendering_for(note, root)
    if rendering is None:
        # A SKIP IS NOT A PASS, and for two of these findings it was exactly
        # one. `E-COV-STAMP` and `E-COV-ROWSHAPE` read the note and never the
        # rendering, so a note skipped for an unreadable oracle was never asked
        # about the shape of its own rows: the corpus reported zero misshapen
        # rows while 21 skipped notes carried 105 (V2 finding V2-6). The note
        # is still reported as ungraded -- the census is unchanged -- and now
        # the checks that did not need a rendering have run.
        return ([f"{rel}: {line}"
                 for line in note_coverage.note_only_defects(note)], why)
    out: list[str] = []
    for fn, argv in ((note_coverage.main, [str(note), str(rendering)]),
                     (note_windows.main, [str(rendering)])):
        code, lines, printed = _run(fn, argv)
        # A CHECK THAT FAILED IS NOT A CHECK THAT PASSED, whatever it managed
        # to spell. Exit 2 means nobody opened the rendering; exit 1 means it
        # found something and said so in its own words. Keeping only what the
        # classifier recognised drops the second kind entirely and reports the
        # note as clean, which is the one report this gate exists to refuse.
        if code != 0 and not lines:
            tail = next((ln for ln in reversed(printed) if ln.strip()), "")
            said = f"; last said {tail!r}" if tail else ""
            where = (rendering.relative_to(root)
                     if rendering.is_relative_to(root) else rendering)
            # SHAPED LIKE EVERY OTHER FINDING. A reader greps this package's
            # output for one shape, and a gate whose own defect line fails its
            # own classifier is a gate keeping two rules.
            lines = [f"[00:00] E-NOTE-CHECK-FAILED {fn.__module__} exited "
                     f"{code} on {where} without naming a defect{said}"]
        out.extend(f"{rel}: {line}" for line in lines)
    return out, None


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="wq-note-gates",
        description="Run the per-note checks against each note's own rendering.")
    ap.add_argument("paths", nargs="*", type=Path,
                    help="notes to check; default is the corpus")
    ap.add_argument("--check", action="store_true",
                    help="accepted for symmetry with the other gates")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if args.selftest:
        return selftest()

    pol = wq_policy.load(refresh=True)
    # GRADING A CORPUS THIS PROCESS NEVER FOUND IS NOT A PASS -- the argument
    # `audit` already makes, repeated here because this gate can be run alone.
    # A caller that means it asks for the neutral run BY NAME, and a caller
    # that knows the root passes it.
    if root is None and pol.source is None and not _neutral_by_name():
        print(f"{PROG}: no policy resolved, so the corpus root is a guess",
              file=sys.stderr)
        return 2
    root = (root or pol.root()).resolve()
    notes = [p.resolve() for p in args.paths] or resolve_note.corpus_notes(root)

    defects: list[str] = []
    skipped: list[tuple[Path, str, list[str]]] = []
    for note in notes:
        found, why = check_note(note, root)
        if why is not None:
            rel = note.relative_to(root) if note.is_relative_to(root) else note
            skipped.append((rel, why, found))
            continue
        defects.extend(found)

    for line in defects:
        print(line)
    # NAMED, not counted. "19 skipped" under a line reading "0 defects" is the
    # shape of a clean bill of health for a corpus this gate never opened.
    #
    # AND WHAT THE SKIP DID NOT EXCUSE IS NAMED TOO. Two of these findings read
    # the note and never the rendering, so for them a skip was exactly a pass
    # and the corpus reported zero misshapen rows while 21 skipped notes
    # carried 105 (V2 finding V2-6). They are printed here rather than counted
    # as defects: a skipped note is one this gate could not grade, its rows are
    # a real and permanent finding, and reddening a frozen corpus over a
    # finding nobody can repair teaches its reader to stop reading the output.
    unreached = 0
    for rel, why, found in skipped:
        print(f"# not graded: {rel} {why}", file=sys.stderr)
        unreached += len(found)
        for line in found:
            print(f"# even so: {line}", file=sys.stderr)
    print(f"# {len(notes) - len(skipped)} of {len(notes)} note(s) graded "
          f"against their own rendering, {len(defects)} defect(s), "
          f"{unreached} finding(s) in notes this gate could not grade",
          file=sys.stderr)
    return 1 if defects else 0


def _neutral_by_name() -> bool:
    import os
    return bool(os.environ.get(wq_policy.ENV_NO_POLICY))


def selftest() -> int:
    import tempfile
    from watchquality import selftest_proof

    cases = 0

    def check(label: str, got, want) -> None:
        nonlocal cases
        cases += 1
        if got != want:
            raise AssertionError(f"{label}: {got!r} != {want!r}")

    proof = selftest_proof.begin(check)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        (root / "notes").mkdir()
        (root / "runs" / "VID").mkdir(parents=True)
        rendering = root / "runs" / "VID" / "run.json"
        # ONE SEGMENT, not `{"segments": []}`. The empty version opens, parses,
        # names the right key and is not a transcript -- and it stood in for a
        # rendering here until this gate started asking whether a check could
        # read what a note declares.
        rendering.write_text(
            '{"segments": [{"start": 0.0, "end": 2.0, "text": "hello there"}]}\n',
            encoding="utf-8")
        manifest = root / "runs" / "VID" / "manifest.json"
        manifest.write_text('{"video_id": "VID", "frames": 12}\n',
                            encoding="utf-8")

        def note(name: str, oracle: str, video: str = "VID") -> Path:
            p = root / "notes" / name
            p.write_text(f"---\nvideo_id: {video}\nduration: \"10:00\"\n"
                         f"status: distilled\noracle: {oracle}\n---\n\n# t\n\n"
                         f"A body.\n", encoding="utf-8")
            return p

        good = note("2026-08-20--good--VID.md", "runs/VID/run.json")
        found, why = rendering_for(good, root)
        check("a declared rendering resolves", found, rendering)
        check("and is not called a skip", why, "")

        empty = note("2026-08-20--empty--VID.md", "")
        check("an empty oracle is a skip", rendering_for(empty, root)[0], None)
        check("and says why", "names no rendering",
              rendering_for(empty, root)[1])

        gone = note("2026-08-20--gone--VID.md", "runs/VID/absent.json")
        check("a rendering that opens nothing is a skip",
              rendering_for(gone, root)[0], None)
        check("and names what it tried",
              "absent.json" in rendering_for(gone, root)[1], True)

        # A FILE THAT OPENS IS NOT A RENDERING. The manifest sits beside the
        # transcript, under the same video id, and this gate used to be handed
        # it, fail to read it, and report a note that could not be graded --
        # while the oracle gate had already called the field fine.
        mani = note("2026-08-20--manifest--VID.md", "runs/VID/manifest.json")
        check("a file that opens and carries no transcript is a skip",
              rendering_for(mani, root)[0], None)
        check("and says it could not be read",
              "read" in rendering_for(mani, root)[1], True)

        # A SKIP IS NOT A PASS, and the count has to show it.
        buf = io.StringIO()
        import contextlib
        err = io.StringIO()
        with redirect_stdout(buf), contextlib.redirect_stderr(err):
            code = main(["--check", str(empty)], root=root)
        check("a skipped note is not a defect", code, 0)
        check("and the run names it", "not graded" in err.getvalue(), True)
        check("and says how many it graded", "0 of 1 note(s) graded"
              in err.getvalue(), True)

        # A check that raises takes its note down, not the run.
        def boom(argv):
            raise RuntimeError("no such thing")

        code, lines, _printed = _run(boom, [])
        check("a raising check is a 2", code, 2)
        check("and reports what raised", "RuntimeError" in lines[0], True)

        # A CHECK THAT FAILED IN PROSE STILL FAILED. The classifier keeps
        # only what opens with a code, and a check that exits 1 saying so in
        # words carries none -- so keeping only the classified lines reported
        # the note as clean over a check that had just failed.
        def prose(argv):
            print("the rendering disagrees with the note")
            return 1

        code, lines, printed = _run(prose, [])
        check("a prose failure is still a 1", code, 1)
        check("and nothing it printed is classified as a defect", lines, [])
        check("and what it said is kept as evidence",
              "disagrees" in printed[0], True)

        # A report row is not a finding. `note_windows` ends each row of its
        # plan with the opening transcript text of that window, so a talk
        # about e-commerce spells the shape of a code in a column of prose.
        check("a code in the middle of a row is not a defect line",
              bool(RE_DEFECT_LINE.match(
                  "1\t00:00\t01:39\t5\t31\tE-COMMERCE growth")), False)
        check("a code after the timestamp is",
              bool(RE_DEFECT_LINE.match("[00:00] E-WIN-EMPTY window 3")), True)

    proof.done()
    print(f"# selftest OK ({cases} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
