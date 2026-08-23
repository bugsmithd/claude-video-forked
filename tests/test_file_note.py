"""Filing a note from the run it was written against.

A note's dated filename and its `oracle:` row are both derivable from the run,
and both were typed. Typing the filename is how a note ends up undated, which
costs it every exemption row that keys on a date. Typing the oracle is worse:
the corpus carries notes naming a rendering no gate can open, each one excused
by a dated row somebody has to remember, and every one of them was a path that
looked right to the person who wrote it.

So the writer picks both. It refuses to name an oracle it cannot itself read,
which is the finding those rows record -- caught at the moment of writing
rather than at the next audit.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from watchquality import file_note as fn
from watchquality import resolve_note as rn
from watchquality import transcript_align as ta


VTT = ("WEBVTT\n\n"
       "00:00:00.000 --> 00:00:05.000\nhello there\n\n"
       "00:00:05.000 --> 00:00:12.000\nand a second thing said out loud\n")


@pytest.fixture
def corpus(tmp_path: Path):
    root = tmp_path.resolve()
    (root / "notes").mkdir()
    return root


def _run(root: Path, title: str = "How Product Teams Ship", segments: str | None = VTT,
         video_id: str = "VID", seconds: float = 612.0) -> Path:
    work = root / "runs" / video_id / "run-01"
    work.mkdir(parents=True)
    run = {"schema": 1, "source": "url", "video_id": video_id, "title": title,
           "uploader": "A Channel", "duration_seconds": seconds,
           "work_dir": str(work),
           "transcript": {"source": "captions", "segments": 2,
                          "subtitle_path": str(work / "captions.vtt")}}
    if segments is not None:
        (work / "captions.vtt").write_text(segments, encoding="utf-8")
    (work / "run.json").write_text(json.dumps(run, indent=2) + "\n",
                                   encoding="utf-8")
    return work / "run.json"


def _file(root: Path, run: Path, *extra: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fn.main([str(run), *extra], root=root)
    return code, out.getvalue() + err.getvalue()


def test_the_filename_is_derived_from_the_run_and_not_typed(corpus):
    """T3 — the date, the slug and the id all come off the run.

    A note whose filename carries no date is a note every dated exemption row
    in this package cannot see: `note_date` returns None and the debt tables
    have to decide what an absent date buys. Deriving it removes the decision.
    """
    run = _run(corpus)

    code, said = _file(corpus, run, "--date", "2026-08-23")

    assert code == 0, said
    landed = corpus / "notes" / "2026-08-23--how-product-teams-ship--VID.md"
    assert landed.is_file(), said
    assert rn.note_date(landed.name) == "2026-08-23"


def test_the_oracle_names_a_rendering_a_gate_can_actually_open(corpus):
    """The debt this writer exists to stop growing.

    Six notes in the corpus name a rendering no gate can read, and the run
    manifest is one of the shapes that fails: `load_segments` refuses it,
    because a manifest carries a segment COUNT and not the segments.
    """
    run = _run(corpus)

    code, said = _file(corpus, run, "--date", "2026-08-23")

    assert code == 0, said
    landed = corpus / "notes" / "2026-08-23--how-product-teams-ship--VID.md"
    frontmatter, _ = rn.split_frontmatter(landed.read_text(encoding="utf-8"))
    oracle = rn.RE_NOTE_ORACLE.search(frontmatter).group(1).strip()
    assert not oracle.endswith("run.json"), oracle
    # Read against the corpus root, never the cwd -- which is the rule the
    # gates already follow for a relative oracle row.
    assert len(ta.load_segments(corpus / oracle)) == 2


def test_a_run_whose_renderings_no_gate_can_read_is_refused(corpus):
    """Refused at the door, rather than excused by a dated row later.

    Every one of those six rows records a path that looked right to whoever
    typed it. The writer is the only place that can tell, because it is the
    only place that can try.
    """
    run = _run(corpus, segments=None)

    code, said = _file(corpus, run, "--date", "2026-08-23")

    assert code == 1, said
    assert "E-NOTE-BLINDORACLE" in said, said
    assert list((corpus / "notes").iterdir()) == [], said


def test_a_note_for_this_video_is_never_overwritten(corpus):
    """The notes are frozen, and a writer that clobbers one is a data loss.

    Keyed on the video id rather than the whole filename: a second run of the
    same video on a later date would otherwise land beside the first as a
    second note about one video, which is the ambiguity the roll-call spent
    two rounds learning to refuse.
    """
    run = _run(corpus)
    _file(corpus, run, "--date", "2026-08-23")

    code, said = _file(corpus, run, "--date", "2026-08-24")

    assert code == 1, said
    assert "E-NOTE-EXISTS" in said, said
    assert len(list((corpus / "notes").iterdir())) == 1, said


def test_a_title_that_yields_no_slug_is_refused(corpus):
    """A filename is a name, and `2026-08-23----VID.md` is not one.

    Refused rather than filled with a placeholder: a machine-chosen `untitled`
    reads, a year later, exactly like a title that was genuinely "untitled".
    """
    run = _run(corpus, title="!!! ??? ...")

    code, said = _file(corpus, run, "--date", "2026-08-23")

    assert code == 1, said
    assert "E-NOTE-UNNAMEABLE" in said, said


def test_the_duration_comes_off_the_run_rather_than_a_reading_of_the_clock(corpus):
    """Density has this number as its denominator, so a typo silently moves it.

    `E-DENSITY-WPM` compares a declared rate against one computed from this
    field; a duration typed one digit short makes every density claim on the
    note wrong in the same direction, and nothing else would notice.
    """
    run = _run(corpus, seconds=612.0)

    _file(corpus, run, "--date", "2026-08-23")

    landed = corpus / "notes" / "2026-08-23--how-product-teams-ship--VID.md"
    frontmatter, _ = rn.split_frontmatter(landed.read_text(encoding="utf-8"))
    assert rn.parse_duration(frontmatter) == 612


def test_what_it_wrote_carries_no_structural_defect(corpus):
    """The whole point, asserted by the reader rather than field by field.

    A new note IS unreviewed and IS unstamped, and both are true findings that
    this writer must not paper over. What it must not produce is a note the
    audit calls malformed -- no frontmatter, no duration, an unreadable status
    or an oracle that resolves to nothing.
    """
    run = _run(corpus)
    _file(corpus, run, "--date", "2026-08-23")
    landed = corpus / "notes" / "2026-08-23--how-product-teams-ship--VID.md"

    got, stats = rn.check_note(landed, corpus, require_density=False)

    structural = [d for d in got if any(
        code in d for code in ("E-FRONTMATTER", "E-DURATION", "E-STATUS",
                               "E-ORACLE", "E-READ", "E-LANE-MALFORMED",
                               "E-LANE-NOVIDEO"))]
    assert structural == [], structural
    assert stats["seconds"] == 612
