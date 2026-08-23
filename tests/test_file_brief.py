"""Writing a lane's brief, and the hash that makes reading it checkable.

"The lane read its brief" is today a sentence in a dispatch log. Nothing on
disk tells a lane that read six hundred words from one that was handed a
one-line prompt and invented the rest, so the claim cannot be refused by
anybody who was not in the room.

A hash fixes that. The brief carries one, the lane's report echoes it back, and
the filer recomputes it from the brief on disk -- so the echo is evidence
rather than a promise, and a brief edited after dispatch stops matching every
report written against it.
"""
from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from watchquality import file_brief as fb
from watchquality import resolve_note as rn


BODY = "\n# t\n\nA body the lanes read.\n"
BRIEF = "Read the note against its oracle. Enumerate every claim.\n"


@pytest.fixture
def corpus(tmp_path: Path):
    root = tmp_path.resolve()
    (root / "notes" / "reviews").mkdir(parents=True)
    return root


def _note(root: Path, reviews: str = "[]") -> Path:
    note = root / "notes" / "2026-08-20--n--VID.md"
    note.write_text(
        f'---\nvideo_id: VID\nduration: "10:00"\nstatus: distilled\n'
        f"oracle: runs/VID/run.json\nreviews: {reviews}\n---\n{BODY}",
        encoding="utf-8")
    return note


def _file(root: Path, note: Path, lane: str, brief: str) -> tuple[int, str]:
    src = root / f"{lane}-brief-src.md"
    src.write_text(brief, encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fb.main([str(note), lane, str(src)], root=root)
    return code, out.getvalue() + err.getvalue()


def test_a_brief_lands_where_the_filer_will_look_for_it(corpus):
    """One directory, one naming rule, applied when the file is written.

    A brief filed anywhere else is a brief the echo check cannot find, and a
    requirement that silently does not apply is worse than no requirement --
    it reads, from the outside, exactly like a requirement that was met.
    """
    note = _note(corpus)

    code, said = _file(corpus, note, "facts", BRIEF)

    assert code == 0, said
    assert rn.brief_path(corpus, "VID", "facts").is_file(), said


def test_the_stamped_hash_is_the_hash_of_the_brief_body(corpus):
    """The lane reads the hash off the brief it was given.

    Self-reference is why the body is hashed and not the file: a hash that
    covered its own header could not be written down inside that header.
    """
    note = _note(corpus)

    _file(corpus, note, "facts", BRIEF)

    landed = rn.brief_path(corpus, "VID", "facts")
    frontmatter, body = rn.split_frontmatter(landed.read_text(encoding="utf-8"))
    assert body.strip() == BRIEF.strip()
    assert f"brief_sha256: {rn.brief_sha256(body)}" in frontmatter


def test_an_empty_brief_is_refused(corpus):
    """Three zero-byte files bought a stamped, gated note once.

    A brief with nothing in it satisfies an echo check perfectly: it hashes,
    it is stamped, and a lane can quote the hash back having read nothing.
    """
    note = _note(corpus)

    code, said = _file(corpus, note, "facts", "   \n\n")

    assert code == 1, said
    assert "E-BRIEF-EMPTY" in said, said
    assert not rn.brief_path(corpus, "VID", "facts").exists(), said


def test_a_rewritten_brief_replaces_the_one_before_it(corpus):
    """A re-dispatch is the likeliest reason a second brief exists.

    The new hash is what matters: every report written against the old brief
    stops matching, which is the correct reading -- those lanes read prose
    that is no longer the instruction.
    """
    note = _note(corpus)
    _file(corpus, note, "facts", BRIEF)
    first = rn.brief_path(corpus, "VID", "facts").read_text(encoding="utf-8")

    code, said = _file(corpus, note, "facts", BRIEF + "Also check the stamps.\n")

    assert code == 0, said
    got = rn.brief_path(corpus, "VID", "facts").read_text(encoding="utf-8")
    assert got != first
    assert "Also check the stamps." in got


def test_a_brief_is_not_counted_as_a_review(corpus):
    """The trap this slice walks into if nobody looks.

    `lane_reports` globs `*.md` under the video's review directory and every
    file it returns that no declared lane claims is an `E-LANE-UNDECLARED`.
    A brief lives in that directory by design, so filing one would manufacture
    a defect on every note that got a brief.
    """
    note = _note(corpus, reviews="[]")
    _file(corpus, note, "facts", BRIEF)

    text = note.read_text(encoding="utf-8")
    frontmatter, body = rn.split_frontmatter(text)

    assert rn.check_lanes(corpus, frontmatter, note.name, body) == []
    assert rn.lane_reports(corpus, "VID") == []


def test_a_brief_does_not_answer_the_roll_call_for_its_lane(corpus):
    """The mirror of the case above, and the more dangerous half.

    If a brief satisfied `lane_matches`, filing one would mark the lane
    reviewed -- a note could pass its roll-call on the strength of the
    instructions nobody carried out.
    """
    note = _note(corpus, reviews="[facts]")
    _file(corpus, note, "facts", BRIEF)

    text = note.read_text(encoding="utf-8")
    frontmatter, body = rn.split_frontmatter(text)
    got = rn.check_lanes(corpus, frontmatter, note.name, body)

    assert len(got) == 1, got
    assert "E-LANE-MISSING" in got[0], got
