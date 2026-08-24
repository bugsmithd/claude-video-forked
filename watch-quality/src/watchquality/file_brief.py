#!/usr/bin/env python3
"""File a lane's brief, and stamp the hash its report has to echo back.

"THE LANE READ ITS BRIEF" IS TODAY A SENTENCE IN A DISPATCH LOG. Nothing on
disk tells a lane that read six hundred words of instruction from a lane that
was handed a one-line prompt and invented the rest, so the claim cannot be
refused by anybody who was not in the room -- and a claim nobody can refuse is
not evidence, it is a habit.

This writer makes it checkable. The brief is filed beside the report it asks
for, carrying the hash of its own body; the lane reads that hash off the brief
it was given; the filer recomputes it from the brief on disk and refuses a
report that cannot quote it back. Three consequences follow, and all three are
the point:

  A LANE THAT NEVER OPENED THE BRIEF has nothing to quote. It can invent a
  verdict, but not a 64-character hash of prose it did not read.
  A BRIEF EDITED AFTER DISPATCH stops matching every report written against
  it, which is the correct reading: those lanes answered instructions that no
  longer exist, and without the hash nobody downstream could tell.
  A BRIEF FILED SOMEWHERE ELSE cannot happen, because the naming rule lives
  here rather than in whoever is dispatching. That is the same argument the
  review filer beside this one makes about report names, and it is the trap
  this project's own disposition records falling into.

WHAT IT REFUSES is one thing, and it is not about whether the brief is any
good. An EMPTY brief satisfies an echo check perfectly -- it hashes, it is
stamped, and a lane can quote the hash back having read nothing. Three
zero-byte files bought a stamped, gated note once; a zero-byte brief would buy
the same and print a hash while doing it.

Usage:
    wq-file-brief NOTE LANE BRIEF
    wq-file-brief --selftest

Exit: 0 filed, 1 refused, 2 usage or unreadable input.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import resolve_note
from .file_review import RE_VIDEO_ID
from .resolve_note import brief_path, brief_sha256, split_frontmatter

PROG = "file_brief.py"


def brief_text(lane: str, body: str) -> str:
    """The brief as it lands: a header the lane reads, then the instruction.

    The hash is stamped rather than left to be looked up because the lane's
    only job is to copy it into its report -- a hash somebody has to run a
    second command to obtain is a hash that gets skipped under load, and a
    check everybody skips is a check nobody has.
    """
    return (f"---\nbrief_sha256: {brief_sha256(body)}\nlane: {lane}\n---\n"
            f"{body.strip()}\n")


def refusals(body: str) -> list[str]:
    """Why this brief may not be filed, or an empty list."""
    if not body.strip():
        return ["E-BRIEF-EMPTY there is nothing in this brief to have read; "
                "an empty brief hashes and stamps like any other, so a lane "
                "could quote it back having read nothing"]
    return []


def file_brief(note: Path, lane: str, brief: Path,
               root: Path) -> tuple[int, list[str]]:
    """(exit code, what to print). Writes nothing when it refuses."""
    text, why = resolve_note.safe_read(note)
    if text is None:
        return 2, [f"{PROG}: {note}: {why}"]
    split = split_frontmatter(text)
    if split is None:
        return 2, [f"{PROG}: {note}: no frontmatter, so no video to brief for"]
    vid = RE_VIDEO_ID.search(split[0])
    if not vid:
        return 2, [f"{PROG}: {note}: declares no video_id"]
    if not resolve_note.RE_LANE_ID.match(lane):
        return 2, [f"{PROG}: {lane!r} is not a lane id, which is the same "
                   f"[a-z0-9][a-z0-9-]* a note declares in reviews:"]

    body, why = resolve_note.safe_read(brief)
    if body is None:
        return 2, [f"{PROG}: {brief}: {why}"]

    refused = refusals(body)
    if refused:
        return 1, [f"{brief}:1 {line}" for line in refused]

    # WRITTEN LAST, for the reason the review filer writes last: a writer that
    # creates the directory before it has decided leaves an empty lane
    # directory behind on every rejected attempt, and to the audit that reads
    # exactly like a lane whose report was lost.
    landing = brief_path(root, vid.group(1), lane)
    landing.parent.mkdir(parents=True, exist_ok=True)
    resolve_note.atomic_write(landing, brief_text(lane, body))
    where = landing.relative_to(root) if landing.is_relative_to(root) else landing
    return 0, [f"# filed {where}", brief_sha256(body)]


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="wq-file-brief",
        description="File a lane's brief where the review filer reads it, and "
                    "stamp the hash the lane's report has to echo back.")
    ap.add_argument("note", type=Path, nargs="?")
    ap.add_argument("lane", nargs="?")
    ap.add_argument("brief", type=Path, nargs="?")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if args.selftest:
        return selftest()
    if not (args.note and args.lane and args.brief):
        ap.error("NOTE, LANE and BRIEF are all required")

    root = (root or resolve_note.POLICY.root()).resolve()
    code, said = file_brief(args.note.resolve(), args.lane,
                            args.brief.resolve(), root)
    # The hash goes to STDOUT on success and the prose to stderr, so that a
    # dispatcher can read the hash out of a pipe without parsing a sentence.
    for line in said[:1] if code == 0 else said:
        print(line, file=sys.stderr if code == 0 else sys.stdout)
    if code == 0:
        print(said[1])
    return code


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

    check("an empty brief is refused", bool(refusals("  \n\n")), True)
    check("...and a brief with words in it is not", refusals("Read it."), [])
    check("the stamp is the hash of the body",
          f"brief_sha256: {brief_sha256('Read it.')}" in brief_text("f", "Read it."),
          True)
    check("...and the body survives verbatim",
          brief_text("f", "Read it.\n").endswith("---\nRead it.\n"), True)
    check("whitespace is not instruction",
          brief_sha256("Read it."), brief_sha256("\n  Read it.  \n"))
    check("different instructions hash differently",
          brief_sha256("Read it.") == brief_sha256("Read it too."), False)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        (root / "notes").mkdir()
        note = root / "notes" / "2026-08-20--n--VID.md"
        note.write_text("---\nvideo_id: VID\nreviews: []\n---\n\n# t\n\nA body.\n",
                        encoding="utf-8")
        src = root / "brief.md"
        src.write_text("Read the note against its oracle.\n", encoding="utf-8")

        code, said = file_brief(note, "facts", src, root)
        check("a brief files", code, 0)
        landed = brief_path(root, "VID", "facts")
        check("...where the filer looks", landed.is_file(), True)
        check("...and the hash is handed back", said[-1],
              brief_sha256(src.read_text(encoding="utf-8")))
        check("...and the roll-call does not see a report",
              resolve_note.lane_reports(root, "VID"), [])

        src.write_text("   \n", encoding="utf-8")
        code, said = file_brief(note, "quality", src, root)
        check("an empty brief is refused at the door", code, 1)
        check("...by name", "E-BRIEF-EMPTY" in said[0], True)
        check("...and nothing is left behind",
              brief_path(root, "VID", "quality").exists(), False)

        src.write_text("Read it.\n", encoding="utf-8")
        check("a lane id is a lane id",
              file_brief(note, "Facts", src, root)[0], 2)

    proof.done()
    print(f"# selftest OK ({cases} cases)")
    return 0


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())
