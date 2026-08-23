#!/usr/bin/env python3
"""Open a note from the run it will be written against.

TWO FIELDS EVERY NOTE CARRIES ARE DERIVABLE FROM ITS RUN, AND BOTH WERE TYPED.

  THE DATED FILENAME  is not decoration. `note_date` reads it, and every dated
                      exemption row in this package is compared against what it
                      returns -- so an undated note is one the debt tables
                      cannot age, and each of them then has to decide what an
                      absence buys. Two rounds of refutation were spent on that
                      decision. Deriving the date removes it.
  THE ORACLE          is the rendering a gate re-derives the note against. Six
                      notes in the corpus name one no gate can open, each one
                      excused by a dated row somebody has to remember to shrink,
                      and every one of those paths looked right to whoever typed
                      it. The run manifest is the commonest way to get it wrong:
                      it carries a segment COUNT, not the segments.

So this writer picks both, and it refuses to name an oracle it cannot read
ITSELF. That is the whole of its claim to be worth having -- it is the only
place in the chain that can try the file before the path is committed to, and
the finding it prevents is one that currently arrives days later with a note
already frozen around it.

WHAT IT DELIBERATELY DOES NOT WRITE is the note. Topics, rating, the body and
the judgement of what the video was about are the author's; a machine-written
`untitled` or an invented topic list reads, a year later, exactly like a human
who meant it. It writes the frame and leaves the note empty.

Usage:
    wq-file-note RUN [--date YYYY-MM-DD] [--slug SLUG]
    wq-file-note --selftest

Exit: 0 filed, 1 refused, 2 usage or unreadable input.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

from . import resolve_note
from .resolve_note import REVIEW_DIR  # noqa: F401  (kept for the reader's map)

PROG = "file_note.py"

# The renderings a gate knows how to open. The manifest itself is a `.json` in
# the same directory and `load_segments` refuses it outright, so the ordering
# here carries no safety -- it is read order and nothing more.
RENDERING_GLOBS = ("*.vtt", "*.tsv", "*.json")
SLUG_WORDS = 8
RE_UNSLUGGABLE = re.compile(r"[^a-z0-9]+")
# The same shape a note declares in `reviews:`, reused: a name that goes into a
# path has to BE a name. `--slug a/b` went in verbatim on the first build, and
# `mkdir(parents=True)` then made the directory it implied -- after which the
# non-recursive existing-note glob could not see the note the writer had just
# filed, and the next call opened a second note about one video.
RE_STEM = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# A video id is a filename component, a glob argument and a path component. On
# the first build it was none of those things: `?` and `*` matched another
# video's note, 300 characters raised ENAMETOOLONG, and a null byte raised out
# of the pathlib call. One rule answers all three.
RE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def slug(title: str, words: int = SLUG_WORDS) -> str:
    """A filename-safe stem, or the empty string when there is nothing to say.

    ASCII-folded rather than escaped: a filename is typed by hand at a shell
    prompt often enough that a non-ASCII one is a filename somebody cannot
    reach. Bounded at `words`, because the corpus's own names are read in a
    directory listing and a full title is not.
    """
    folded = unicodedata.normalize("NFKD", title)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    parts = [p for p in RE_UNSLUGGABLE.split(folded.lower()) if p]
    return "-".join(parts[:words])


def hhmmss(seconds: float) -> str:
    """`M:SS`, or `H:MM:SS` past the hour -- the shape the duration row takes."""
    whole = int(round(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def one_line(value: object) -> str:
    """A frontmatter value that cannot become a second row.

    The block is built by concatenation, so a newline anywhere in a value
    writes arbitrary rows into it -- and the rows worth injecting are exactly
    the ones a note is graded on: `status`, `oracle`, `video_id`.
    """
    return " ".join(str(value or "").split())


def rendering(run: Path, manifest: dict) -> Path | None:
    """The rendering a gate can actually open, or None if there is not one.

    The manifest's own `subtitle_path` is tried first and is not trusted: it
    records the file the run PARSED, which on a captions run can be a track
    that was never written to disk. Everything beside the manifest is tried
    after it, and each candidate is opened rather than pattern-matched --
    `load_segments` is the reader the gates use, so a file this accepts is one
    they accept by construction.
    """
    # Imported here: transcript_align pulls in say_captions, which reads policy
    # at import time, and a writer that cannot run on a malformed config is a
    # writer that cannot fix one.
    from .transcript_align import load_segments

    def reads(path: Path) -> bool:
        try:
            return bool(load_segments(path))
        except Exception:
            return False

    said = (manifest.get("transcript") or {}).get("subtitle_path")
    candidates: list[Path] = []
    if isinstance(said, str) and said:
        # ANCHORED TO THE RUN, not to the caller. A bare `Path(said)` resolves
        # a relative value against whatever directory the operator happened to
        # be in, so one manifest gave two operators two different oracles --
        # and one of them got whatever file shared that name.
        candidates.append(run.parent / said)
    for pattern in RENDERING_GLOBS:
        candidates.extend(sorted(run.parent.glob(pattern)))
    vid = str(manifest.get("video_id") or "")
    for candidate in candidates:
        if not candidate.is_file() or not reads(candidate):
            continue
        # THE AUDIT'S OWN RULE, applied before the path is written rather than
        # after. `check_oracle` requires the video id to be a component of the
        # resolved oracle path and prints E-ORACLE-UNRELATED when it is not.
        # The first build passed its own test only because the fixture happened
        # to put the run under a directory named for the video; a run directory
        # named any other way produced a note the audit convicts on the day it
        # is written.
        if vid in candidate.resolve().parts:
            return candidate
    return None


def frontmatter(manifest: dict, when: str, oracle: Path, root: Path) -> str:
    """The frame, in the order the corpus's own notes carry it."""
    where = oracle.resolve()
    shown = where.relative_to(root) if where.is_relative_to(root) else where
    rows = [
        f"title: {one_line(manifest.get('title'))}",
        f"video_id: {manifest['video_id']}",
        f"channel: {one_line(manifest.get('uploader'))}",
        f"watched: {when}",
        f'duration: "{hhmmss(float(manifest["duration_seconds"]))}"',
        "topics: []",
        "status: capture",
        # NO `applied:` ROW. `check_status` reads the row as a value rather
        # than as YAML, so the empty list writes as the two-character string
        # `[]` and convicts the note of naming an edge it does not name. A
        # writer whose output its own audit refuses is a writer that moved the
        # problem, and the row means nothing on a note nothing has applied yet.
        "reviews: []",
        f"oracle: {shown}",
    ]
    return "\n".join(rows)


def existing(root: Path, video_id: str) -> Path | None:
    """A note already open on this video, whatever date it carries."""
    notes = root / resolve_note.POLICY.notes_dir()
    if not notes.is_dir():
        return None
    for path in sorted(notes.glob(f"*--{video_id}.md")):
        return path
    return None


def file_note(run: Path, root: Path, when: str | None = None,
              stem: str | None = None) -> tuple[int, list[str]]:
    """(exit code, what to print). Writes nothing when it refuses."""
    text, why = resolve_note.safe_read(run)
    if text is None:
        return 2, [f"{PROG}: {run}: {why}"]
    try:
        manifest = json.loads(text)
    except ValueError as exc:
        return 2, [f"{PROG}: {run}: not readable as a run: {exc}"]
    if not isinstance(manifest, dict) or not manifest.get("video_id"):
        return 2, [f"{PROG}: {run}: names no video_id, so there is no note to "
                   f"open and nothing to key one on"]
    video_id = str(manifest["video_id"])
    if not RE_VIDEO_ID.match(video_id):
        return 2, [f"{PROG}: {run}: video_id {video_id!r} is not one this can "
                   f"put in a filename, glob for, or compare a path component "
                   f"against"]
    try:
        float(manifest["duration_seconds"])
    except (KeyError, TypeError, ValueError):
        return 2, [f"{PROG}: {run}: duration_seconds is missing or is not a "
                   f"number, and it is the only denominator density has"]

    already = existing(root, video_id)
    if already is not None:
        return 1, [f"{run}:1 E-NOTE-EXISTS {already.name} is already open on "
                   f"this video; the notes are frozen, and a second note about "
                   f"one video is the ambiguity the roll-call refuses"]

    stem = stem or slug(str(manifest.get("title") or ""))
    if not RE_STEM.match(stem):
        return 1, [f"{run}:1 E-NOTE-UNNAMEABLE {stem!r} is no name to file "
                   f"under; a placeholder here reads a year later exactly like "
                   f"a title somebody meant, and a name holding a separator is "
                   f"a directory this writer would silently create"]

    oracle = rendering(run, manifest)
    if oracle is None:
        return 1, [f"{run}:1 E-NOTE-BLINDORACLE no rendering beside this run "
                   f"can be read as a transcript, so the note would name an "
                   f"oracle no gate can open -- which is the debt this corpus "
                   f"already carries a dated row for, six times over"]

    # LOCAL, not UTC. `watched:` is a human's day, and this machine is not on
    # UTC -- a run watched in the evening was being dated tomorrow.
    when = when or datetime.fromtimestamp(run.stat().st_mtime).date().isoformat()
    landing = root / resolve_note.POLICY.notes_dir() / f"{when}--{stem}--{video_id}.md"
    landing.parent.mkdir(parents=True, exist_ok=True)
    landing.write_text(
        "---\n" + frontmatter(manifest, when, oracle, root) + "\n---\n\n",
        encoding="utf-8")
    where = landing.relative_to(root) if landing.is_relative_to(root) else landing
    return 0, [f"# opened {where}, against {oracle.name}"]


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="wq-file-note",
        description="Open a note from its run, with the dated filename and the "
                    "oracle derived rather than typed.")
    ap.add_argument("run", type=Path, nargs="?")
    ap.add_argument("--date", help="the watched date; defaults to the run's own")
    ap.add_argument("--slug", help="override the name derived from the title")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if args.selftest:
        return selftest()
    if not args.run:
        ap.error("RUN is required")
    when = None
    if args.date:
        try:
            # NORMALISED, not just validated. `fromisoformat` accepts more
            # spellings than a filename does, and the first build validated the
            # operator's string and then interpolated THAT -- so `20260823`
            # passed and produced a name `note_date` cannot read. An undated
            # note is the one thing deriving the date exists to prevent.
            when = date.fromisoformat(args.date).isoformat()
        except ValueError:
            ap.error(f"--date {args.date!r} is not a date")

    root = (root or resolve_note.POLICY.root()).resolve()
    code, said = file_note(args.run.resolve(), root, when, args.slug)
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

    check("a title becomes a name", slug("How Product Teams Ship"),
          "how-product-teams-ship")
    check("accents fold rather than escape", slug("Café Déjà Vu"), "cafe-deja-vu")
    check("punctuation alone is no name", slug("!!! ??? ..."), "")
    check("a name is bounded", slug("a b c d e f g h i j", words=3), "a-b-c")
    check("runs of separators collapse", slug("a --  b"), "a-b")
    check("under the hour, minutes and seconds", hhmmss(612), "10:12")
    check("...and over it, hours too", hhmmss(3671), "1:01:11")
    check("...and a rounded second is still a second", hhmmss(59.6), "1:00")

    vtt = ("WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nhello there\n\n"
           "00:00:05.000 --> 00:00:12.000\nand a second thing said\n")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        (root / "notes").mkdir()
        work = root / "runs" / "VID" / "run-01"
        work.mkdir(parents=True)
        manifest = {"video_id": "VID", "title": "How Product Teams Ship",
                    "uploader": "A Channel", "duration_seconds": 612.0,
                    "transcript": {"subtitle_path": str(work / "captions.vtt")}}
        run = work / "run.json"
        run.write_text(json.dumps(manifest), encoding="utf-8")

        code, said = file_note(run, root, when="2026-08-23")
        check("a run with no readable rendering is refused", code, 1)
        check("...by name", "E-NOTE-BLINDORACLE" in said[0], True)
        check("...and no note is left behind",
              list((root / "notes").iterdir()), [])

        (work / "captions.vtt").write_text(vtt, encoding="utf-8")
        code, said = file_note(run, root, when="2026-08-23")
        check("...and with one, the note opens", code, 0)
        landed = root / "notes" / "2026-08-23--how-product-teams-ship--VID.md"
        check("...under a name derived from the run", landed.is_file(), True)
        head = landed.read_text(encoding="utf-8")
        check("...naming the rendering rather than the manifest",
              "oracle: " in head and "run.json" not in head, True)
        check("...and the duration off the run", 'duration: "10:12"' in head,
              True)

        code, said = file_note(run, root, when="2026-08-24")
        check("a second note about one video is refused", code, 1)
        check("...by name", "E-NOTE-EXISTS" in said[0], True)
        check("...and the first is untouched",
              len(list((root / "notes").iterdir())), 1)

        manifest["title"] = "!!!"
        run.write_text(json.dumps(manifest), encoding="utf-8")
        (root / "notes" / landed.name).unlink()
        code, said = file_note(run, root, when="2026-08-23")
        check("a title with no name in it is refused", code, 1)
        check("...by name", "E-NOTE-UNNAMEABLE" in said[0], True)

        run.write_text("{not json", encoding="utf-8")
        check("a run that is not a run is usage, not a defect",
              file_note(run, root)[0], 2)

    proof.done()
    print(f"# selftest OK ({cases} cases)")
    return 0


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())
