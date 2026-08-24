#!/usr/bin/env python3
"""File a lane's review report, and declare it on the note it reviewed.

TWO TRANSITIONS IN THIS PROJECT'S REVIEW STATE MACHINE HAD NO MACHINE AT ALL.
Filing a report was a human choosing a directory and a filename; declaring the
lane was a human typing into `reviews:`. Both are checked at audit time, which
is minutes or days later, and both are checked against rules that live in the
audit rather than beside the choice -- so the naming rule that decides whether a
report counts was enforced after the filing choice had already been made. The
corpus's own disposition records falling into exactly that trap.

What this writer owns, so that nobody has to remember it:

  WHERE A REPORT GOES     `notes/reviews/<video_id>/<lane>.md`, which is what
                          `resolve_note.lane_matches` looks for. One rule, one
                          place, applied when the file is written rather than
                          when it is graded.
  WHAT COUNTS AS A REPORT the header has to parse, name this lane, and name the
                          note body it read. A report that fails any of those
                          never reaches the directory -- three zero-byte files
                          bought a stamped, gated note once, and the audit-time
                          header requirement closed that door from the inside.
                          This closes it from the outside.
  WHO DECLARES THE LANE   the writer. A hand-typed `reviews:` is the note
                          asserting its own review status, and the roll-call it
                          feeds only checks lanes the note DECLARED -- so
                          declaring none passed it.

WHAT IT DELIBERATELY DOES NOT DO is decide whether the report is any good. The
verdict is the lane's, the findings are the lane's, and a writer that graded
them would be a second opinion nobody asked for. It refuses three things, all
mechanical, and files everything else.

Usage:
    wq-file-review NOTE LANE REPORT
    wq-file-review --selftest

Exit: 0 filed, 1 refused, 2 usage or unreadable input.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import resolve_note
from .resolve_note import (LANE_HEADER_FIELDS, RE_LANE_ID, brief_path,
                           brief_stamp, note_body_sha256, lane_header,
                           split_frontmatter)

PROG = "file_review.py"

# The `reviews:` row, as the note carries it. Matched rather than parsed as
# YAML for the same reason every other reader here does: a note is edited by
# hand and a strict parser turns a trailing space into a missing declaration.
RE_REVIEWS = re.compile(r"^reviews:[ \t]*\[(.*?)\][ \t]*$", re.MULTILINE)
RE_VIDEO_ID = re.compile(r"^video_id:[ \t]*(\S+)[ \t]*$", re.MULTILINE)


def declared_lanes(frontmatter: str) -> list[str]:
    """The lanes a note says it ran, in the order it says them."""
    m = RE_REVIEWS.search(frontmatter)
    if not m:
        return []
    return [x.strip() for x in m.group(1).split(",") if x.strip()]


def with_lane(frontmatter: str, lane: str) -> str:
    """The frontmatter with `lane` declared, once, in filing order.

    Once, because re-watching a video already reviewed is the most likely
    reason a second report exists, and a declaration list that grows a
    duplicate is a roll-call counting one lane twice.
    """
    lanes = declared_lanes(frontmatter)
    if lane not in lanes:
        lanes.append(lane)
    row = f"reviews: [{', '.join(lanes)}]"
    if RE_REVIEWS.search(frontmatter):
        return RE_REVIEWS.sub(row, frontmatter, count=1)
    return frontmatter.rstrip("\n") + "\n" + row


def refusals(report: Path, note_body: str, lane: str,
             brief: Path | None = None) -> list[str]:
    """Why this report may not be filed, or an empty list.

    Four questions, and none of them is about whether the review is any good.
    `lane_header` is the audit's own reader, called rather than reimplemented,
    so a report this writer accepts is one that reader accepts by construction.

    `brief` is the brief this lane was given, when one was filed. The echo it
    asks for is keyed to that artifact and to nothing else: no brief on disk,
    nothing to echo, no finding. That is what lets every report written before
    any of this existed go on filing without a dated exemption row somebody
    has to remember to shrink -- the rule turns on the evidence, not on a
    calendar.
    """
    out: list[str] = []
    fields, why = lane_header(report)
    if fields is None:
        return [f"E-FILE-UNPARSED this is not a review: {why}; every report "
                f"carries {', '.join(LANE_HEADER_FIELDS)}"]
    if brief is not None and brief.exists():
        # `exists()`, not `is_file()`. A DIRECTORY at the brief's path made the
        # whole requirement vanish on the first build -- and the roll-call
        # cannot see it either, because it is named to be skipped. That is the
        # `mkdir quality.md` mechanism this module's own header says was
        # closed for reports, still open one filename to the left.
        #
        # `safe_read` and `brief_stamp`, not a read and a body hash: a brief
        # that does not describe itself is refused by NAME rather than turned
        # into a hash of the empty string, and the message blames the brief
        # rather than the lane that quoted it honestly.
        want = brief_stamp(resolve_note.safe_read(brief)[0])
        said = fields.get("brief_sha256", "")
        if want is None:
            out.append(f"E-FILE-BADBRIEF {brief.name} does not describe "
                       f"itself: a brief carries a brief_sha256 row that is "
                       f"the hash of the body under it, and until it does "
                       f"there is nothing here for a report to echo")
        elif not said:
            out.append(f"E-FILE-UNBRIEFED lane {lane} was given a brief and "
                       f"this report does not echo its hash; without that, "
                       f"whether the lane read {brief.name} at all is a claim "
                       f"nobody downstream can refuse")
        elif said != want:
            out.append(f"E-FILE-WRONGBRIEF the report echoes brief "
                       f"{said[:12]}, the brief on disk is {want[:12]}; either "
                       f"the lane read other instructions or the brief was "
                       f"rewritten after it was dispatched")
    if fields["lane"] != lane:
        out.append(f"E-FILE-MISLABELLED the header answers for lane "
                   f"{fields['lane']}, and it is being filed as {lane}")
    want = note_body_sha256(note_body)
    if fields["note_sha256"] != want:
        out.append(f"E-FILE-STALE the header read note body "
                   f"{fields['note_sha256'][:12]}, the note on disk is "
                   f"{want[:12]}; the note changed after the lane read it, so "
                   f"this report is about a version nobody can see")
    return out


def file_review(note: Path, lane: str, report: Path,
                root: Path) -> tuple[int, list[str]]:
    """(exit code, what to print). Writes nothing when it refuses."""
    # CHECKED HERE, not borrowed. `brief_path` is string joining and
    # `Path.is_relative_to` is lexical, so a lane id holding `..` builds a path
    # out of the review directory. Nothing escaped on the first build only
    # because the report's header has to carry a matching `lane:` and THAT
    # field's type happens to be this pattern -- protection from a check
    # written in another module for another purpose, which the next caller
    # would not inherit.
    if not RE_LANE_ID.match(lane):
        return 2, [f"{PROG}: {lane!r} is not a lane id, which is the same "
                   f"[a-z0-9][a-z0-9-]* a note declares in reviews:"]
    text, why = resolve_note.safe_read(note)
    if text is None:
        return 2, [f"{PROG}: {note}: {why}"]
    split = split_frontmatter(text)
    if split is None:
        return 2, [f"{PROG}: {note}: no frontmatter, so no note to declare on"]
    frontmatter, body = split
    vid = RE_VIDEO_ID.search(frontmatter)
    if not vid:
        return 2, [f"{PROG}: {note}: declares no video_id"]

    said, why = resolve_note.safe_read(report)
    if said is None:
        return 2, [f"{PROG}: {report}: {why}"]

    refused = refusals(report, body, lane,
                       brief_path(root, vid.group(1), lane))
    if refused:
        return 1, [f"{report}:1 {line}" for line in refused]

    # WRITTEN LAST, and only once nothing refused. A writer that creates the
    # directory before it has decided leaves a review tree that grows on every
    # rejected attempt, and an empty lane directory reads to the audit exactly
    # like a lane whose report was lost.
    landing = root / resolve_note.REVIEW_DIR / vid.group(1) / f"{lane}.md"
    landing.parent.mkdir(parents=True, exist_ok=True)
    resolve_note.atomic_write(landing, said)
    # REBUILT WITH BOTH DELIMITERS. `split_frontmatter` hands back the block
    # between them and the body after them, so a writer that joins those two
    # and forgets the opening `---` produces a note with no frontmatter at all
    # -- which every reader in this package treats as a note it cannot grade.
    resolve_note.atomic_write(
        note,
        "---\n" + with_lane(frontmatter, lane).rstrip("\n") + "\n---\n" + body)
    where = landing.relative_to(root) if landing.is_relative_to(root) else landing
    return 0, [f"# filed {where}, and {note.name} now declares {lane}"]


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="wq-file-review",
        description="File a lane's report where the audit reads it, and "
                    "declare the lane on the note.")
    ap.add_argument("note", type=Path, nargs="?")
    ap.add_argument("lane", nargs="?")
    ap.add_argument("report", type=Path, nargs="?")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if args.selftest:
        return selftest()
    if not (args.note and args.lane and args.report):
        ap.error("NOTE, LANE and REPORT are all required")

    root = (root or resolve_note.POLICY.root()).resolve()
    code, said = file_review(args.note.resolve(), args.lane,
                             args.report.resolve(), root)
    for line in said:
        print(line, file=sys.stderr if code == 0 else sys.stdout)
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

    check("no reviews row is no lanes", declared_lanes("video_id: VID\n"), [])
    check("an empty row is no lanes", declared_lanes("reviews: []\n"), [])
    check("two lanes, in order",
          declared_lanes("reviews: [facts, quality]\n"), ["facts", "quality"])
    check("a lane is declared once",
          declared_lanes(with_lane("reviews: [facts]\n", "facts")), ["facts"])
    check("...and a new one is appended",
          declared_lanes(with_lane("reviews: [facts]\n", "quality")),
          ["facts", "quality"])
    check("a note with no row gets one",
          declared_lanes(with_lane("video_id: VID\n", "facts")), ["facts"])

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        (root / "notes").mkdir()
        body = "\n# t\n\nA body.\n"
        note = root / "notes" / "2026-08-20--n--VID.md"
        note.write_text(f"---\nvideo_id: VID\nreviews: []\n---\n{body}",
                        encoding="utf-8")

        def report(lane="facts", sha=None):
            return (f"---\nnote_sha256: {sha or note_body_sha256(body)}\n"
                    f"oracle: runs/VID/run.json\nlane: {lane}\n"
                    f"verdict: SHIP\nclaims_enumerated: 3\n---\n\n# r\n\nx.\n")

        src = root / "r.md"
        src.write_text(report(), encoding="utf-8")
        code, said = file_review(note, "facts", src, root)
        check("a good report files", (code, len(said)), (0, 1))
        landed = root / "notes" / "reviews" / "VID" / "facts.md"
        check("...where the audit looks", landed.is_file(), True)
        check("...and the note declares it",
              declared_lanes(note.read_text(encoding="utf-8")), ["facts"])

        src.write_text("not a review\n", encoding="utf-8")
        code, said = file_review(note, "quality", src, root)
        check("an unreadable report is refused", code, 1)
        check("...by name", "E-FILE-UNPARSED" in said[0], True)
        check("...and nothing is declared",
              declared_lanes(note.read_text(encoding="utf-8")), ["facts"])
        check("...and no directory is left behind",
              (root / "notes" / "reviews" / "VID" / "quality.md").exists(),
              False)

        src.write_text(report(sha="0" * 64), encoding="utf-8")
        code, said = file_review(note, "facts", src, root)
        check("a stale report is refused", code, 1)
        check("...by name", "E-FILE-STALE" in said[0], True)

        src.write_text(report(lane="quality"), encoding="utf-8")
        code, said = file_review(note, "facts", src, root)
        check("a mislabelled report is refused", code, 1)
        check("...by name", "E-FILE-MISLABELLED" in said[0], True)

        # Imported here rather than at module scope: the brief writer imports
        # this module, and a cycle at import time would cost both of them.
        from watchquality.file_brief import file_brief as _file_brief
        instructions = root / "brief.md"
        instructions.write_text("Read it against the oracle.\n",
                                encoding="utf-8")
        _file_brief(note, "facts", instructions, root)

        def echoing(sha):
            return report().replace(
                "---\nnote_sha256:",
                f"---\nbrief_sha256: {sha}\nnote_sha256:", 1)

        src.write_text(report(), encoding="utf-8")
        code, said = file_review(note, "facts", src, root)
        check("a report that does not echo its brief is refused", code, 1)
        check("...by name", "E-FILE-UNBRIEFED" in said[0], True)

        src.write_text(echoing("1" * 64), encoding="utf-8")
        code, said = file_review(note, "facts", src, root)
        check("a wrong echo is refused", code, 1)
        check("...by name", "E-FILE-WRONGBRIEF" in said[0], True)

        src.write_text(echoing(resolve_note.brief_sha256(
            instructions.read_text(encoding="utf-8"))), encoding="utf-8")
        check("...and the right one files",
              file_review(note, "facts", src, root)[0], 0)

        landed = resolve_note.brief_path(root, "VID", "facts")
        landed.write_text("Read it, unstamped.\n", encoding="utf-8")
        code, said = file_review(note, "facts", src, root)
        check("a brief that does not describe itself is refused", code, 1)
        check("...by name", "E-FILE-BADBRIEF" in said[0], True)

    proof.done()
    print(f"# selftest OK ({cases} cases)")
    return 0


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())
