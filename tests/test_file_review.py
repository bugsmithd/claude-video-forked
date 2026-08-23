"""Filing a review report, and what the writer refuses.

Every transition in this project's review state machine that a machine does not
carry is a transition a human carries in their memory. Filing a report is one
of them: the naming rule that decides whether a report counts at all lives in
the audit, which runs long after the filing choice was made by hand -- which is
exactly the trap the corpus's own disposition records falling into.

So the writer owns the naming rule, refuses a report that is not a review, and
writes the note's `reviews:` declaration rather than leaving it to be typed.
A hand-typed declaration is the note asserting its own review status.
"""
from __future__ import annotations

import hashlib
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from watchquality import file_brief as fb
from watchquality import file_review as fr
from watchquality import resolve_note as rn


BODY = "\n# t\n\nA body the lanes read.\n"


@pytest.fixture
def corpus(tmp_path: Path):
    root = tmp_path.resolve()
    (root / "notes" / "reviews").mkdir(parents=True)
    (root / "runs" / "VID").mkdir(parents=True)
    (root / "runs" / "VID" / "run.json").write_text(
        '{"segments": [{"start": 0.0, "end": 5.0, "text": "hello there"}]}\n',
        encoding="utf-8")
    return root


def _note(root: Path, reviews: str = "[]") -> Path:
    note = root / "notes" / "2026-08-20--n--VID.md"
    note.write_text(
        f'---\nvideo_id: VID\nduration: "10:00"\nstatus: distilled\n'
        f"oracle: runs/VID/run.json\nreviews: {reviews}\n---\n{BODY}",
        encoding="utf-8")
    return note


def _report(note: Path, lane: str = "facts", verdict: str = "SHIP-WITH-FIXES",
            oracle: str = "runs/VID/run.json", sha: str | None = None) -> str:
    text = note.read_text(encoding="utf-8")
    body = rn.split_frontmatter(text)[1]
    return (f"---\nnote_sha256: {sha or rn.note_body_sha256(body)}\n"
            f"oracle: {oracle}\nlane: {lane}\nverdict: {verdict}\n"
            f"claims_enumerated: 12\n---\n\n# {lane} lane\n\nWhat it found.\n")


def _file(root: Path, note: Path, lane: str, report: str) -> tuple[int, str]:
    src = root / f"{lane}-report.md"
    src.write_text(report, encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fr.main([str(note), lane, str(src)], root=root)
    return code, out.getvalue() + err.getvalue()


def test_a_report_lands_where_the_audit_looks_for_it(corpus):
    """The naming rule is the writer's, not the filer's memory.

    `lane_matches` decides whether a report counts, and it is enforced at audit
    time -- after somebody has already chosen a directory and a filename. The
    writer applies the same rule at the moment of filing, so the choice cannot
    be made wrongly and discovered later.
    """
    note = _note(corpus)

    code, said = _file(corpus, note, "facts", _report(note))

    assert code == 0, said
    landed = corpus / "notes" / "reviews" / "VID" / "facts.md"
    assert landed.is_file(), said
    assert rn.lane_matches([landed], "facts") == [landed]


def test_the_note_declares_the_lane_the_writer_filed(corpus):
    """T9 — `reviews:` written by the tool, never typed.

    A hand-typed declaration is the note asserting its own review status, and
    the roll-call it feeds only checks lanes the note DECLARED: declaring none
    passed it.
    """
    note = _note(corpus)

    _file(corpus, note, "facts", _report(note))

    assert "reviews: [facts]" in note.read_text(encoding="utf-8")

    _file(corpus, note, "quality", _report(note, lane="quality"))

    got = note.read_text(encoding="utf-8")
    assert "reviews: [facts, quality]" in got, got


def test_a_report_that_is_not_a_review_is_refused_before_it_is_filed(corpus):
    """Three zero-byte files bought a stamped, gated note once.

    The header requirement closed that at audit time. Refusing at filing time
    is the other half: a report that cannot be read as a review never reaches
    the directory the audit reads.
    """
    note = _note(corpus)

    code, said = _file(corpus, note, "facts", "not a review at all\n")

    assert code == 1, said
    assert "E-FILE-UNPARSED" in said, said
    assert not (corpus / "notes" / "reviews" / "VID").exists(), said
    assert "reviews: []" in note.read_text(encoding="utf-8")


def test_a_report_about_another_version_of_the_note_is_refused(corpus):
    """X3 — nothing bound a report to the version of the note it read.

    The corpus already owns this mechanism: a sidecar binds every citation to a
    hash. The header carries one too, and the audit compares it -- but by then
    the report is filed and the reader has to work out which of the two moved.
    Refused at the door instead.
    """
    note = _note(corpus)

    code, said = _file(corpus, note, "facts", _report(note, sha="0" * 64))

    assert code == 1, said
    assert "E-FILE-STALE" in said, said
    assert not (corpus / "notes" / "reviews" / "VID").exists(), said


def test_a_report_answering_for_a_different_lane_is_refused(corpus):
    """The header names its lane, and the filing names its lane.

    Two answers to one question is not a smaller claim; it is an unreadable
    one, and `E-LANE-MISLABELLED` is the audit-time finding this prevents.
    """
    note = _note(corpus)

    code, said = _file(corpus, note, "facts", _report(note, lane="quality"))

    assert code == 1, said
    assert "E-FILE-MISLABELLED" in said, said


def test_filing_the_same_lane_twice_does_not_declare_it_twice(corpus):
    """A re-review overwrites its report and leaves one declaration.

    Re-watching a video already reviewed is the single most likely reason a
    second report exists, and a declaration list that grows a duplicate is a
    roll-call that counts one lane twice.
    """
    note = _note(corpus)

    _file(corpus, note, "facts", _report(note))
    code, said = _file(corpus, note, "facts", _report(note, verdict="SHIP"))

    assert code == 0, said
    got = note.read_text(encoding="utf-8")
    assert got.count("facts") == 1, got
    landed = corpus / "notes" / "reviews" / "VID" / "facts.md"
    assert "verdict: SHIP\n" in landed.read_text(encoding="utf-8")


def test_what_it_wrote_survives_the_audit_that_reads_it(corpus):
    """The whole point, asserted end to end rather than per field.

    A writer whose output its own audit refuses is a writer that moved the
    problem. `check_lanes` is the reader; it gets the last word here.
    """
    note = _note(corpus)
    for lane in ("facts", "quality", "coverage"):
        code, said = _file(corpus, note, lane, _report(note, lane=lane))
        assert code == 0, said

    text = note.read_text(encoding="utf-8")
    frontmatter, body = rn.split_frontmatter(text)
    ambient = list(rn.REQUIRED_LANES)
    rn.REQUIRED_LANES[:] = ["facts", "quality", "coverage"]
    try:
        got = rn.check_lanes(corpus, frontmatter, note.name, body)
    finally:
        rn.REQUIRED_LANES[:] = ambient

    assert got == [], got


def _brief(root: Path, note: Path, lane: str,
           text: str = "Read it against the oracle.\n") -> str:
    """File a brief for `lane`, and hand back the hash a report must echo."""
    src = root / f"{lane}-brief-src.md"
    src.write_text(text, encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fb.main([str(note), lane, str(src)], root=root)
    assert code == 0, out.getvalue() + err.getvalue()
    return rn.brief_sha256(text)


def _echoing(note: Path, lane: str, sha: str | None) -> str:
    report = _report(note, lane=lane)
    if sha is None:
        return report
    return report.replace("---\nnote_sha256:",
                          f"---\nbrief_sha256: {sha}\nnote_sha256:", 1)


def test_a_report_that_does_not_echo_its_brief_is_refused(corpus):
    """T5/T6/T7 — "the lane read the brief" stops being unfalsifiable.

    Nothing on disk told a lane that read six hundred words from one handed a
    one-line prompt. The brief carries a hash; a report that cannot quote it
    back is a report by somebody who did not have the brief open.
    """
    note = _note(corpus)
    _brief(corpus, note, "facts")

    code, said = _file(corpus, note, "facts", _echoing(note, "facts", None))

    assert code == 1, said
    assert "E-FILE-UNBRIEFED" in said, said
    assert not (corpus / "notes" / "reviews" / "VID" / "facts.md").exists(), said


def test_a_report_echoing_the_wrong_brief_is_refused(corpus):
    """A hash that is not this brief's is a report about other instructions.

    It is also what a brief edited after dispatch produces, which is the
    reading that matters: the report answers prose that has been replaced, and
    nobody reading it later could tell.
    """
    note = _note(corpus)
    _brief(corpus, note, "facts")

    code, said = _file(corpus, note, "facts", _echoing(note, "facts", "1" * 64))

    assert code == 1, said
    assert "E-FILE-WRONGBRIEF" in said, said


def test_a_report_that_echoes_its_brief_is_filed(corpus):
    """The positive case, and the brief survives the filing.

    A writer that consumed the brief would make the check unrepeatable: the
    audit could never re-derive the hash the report was accepted against.
    """
    note = _note(corpus)
    sha = _brief(corpus, note, "facts")

    code, said = _file(corpus, note, "facts", _echoing(note, "facts", sha))

    assert code == 0, said
    assert (corpus / "notes" / "reviews" / "VID" / "facts.md").is_file(), said
    assert rn.brief_path(corpus, "VID", "facts").is_file(), said


def test_a_report_files_when_no_brief_was_ever_written(corpus):
    """The requirement is conditional on the artifact, never on a date.

    The corpus holds reports written before any of this existed. Making the
    echo unconditional would fail every one of them and buy another dated
    exemption ledger; keying it to a brief actually on disk needs no table at
    all, and no row anybody has to remember to shrink.
    """
    note = _note(corpus)

    code, said = _file(corpus, note, "facts", _echoing(note, "facts", None))

    assert code == 0, said


def test_a_brief_hash_that_is_not_a_hash_is_not_read_as_one(corpus):
    """A field present and malformed is the one a reader acts on wrongly.

    The header parser is strict about the two fields a reader acts on for the
    same reason: a value outside the vocabulary is not a smaller claim, it is
    an unreadable one.
    """
    note = _note(corpus)
    _brief(corpus, note, "facts")

    code, said = _file(corpus, note, "facts",
                       _echoing(note, "facts", "not-a-hash"))

    assert code == 1, said
    assert "E-FILE-UNPARSED" in said, said
