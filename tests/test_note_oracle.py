"""`oracle:` is the note's own declaration, and it had no consequence at all.

Eighteen of twenty-five notes left the field empty and no `RE_ORACLE` existed
for it anywhere in the package, while every gate that reads a transcript --
coverage, alignment, windows -- grades a note AGAINST its declared rendering. A
note that named none could not be graded by any of them, and nothing said so.

Two things are pinned here. The check refuses a field that is absent, empty,
unresolvable, or resolvable to something that is not this video's. And the
grandfather ledger that keeps it from reddening a frozen corpus CANNOT GROW: a
row does not reach a note filed after the row's own date, which is what stops
"the note that made us write the row" from becoming "every note from now on".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from watchquality import resolve_note as rn


BODY = "\n# t\n\nA body.\n"
NOTE = "notes/2026-08-20--n--VID.md"


def _fm(oracle: str | None = "run.json", video_id: str | None = "VID") -> str:
    lines = ['duration: "10:00"', "status: distilled"]
    if video_id is not None:
        lines.append(f"video_id: {video_id}")
    if oracle is not None:
        lines.append(f"oracle: {oracle}")
    return "\n".join(lines) + "\n"


# One segment is a transcript. `{}` is not, and every rendering in this fixture
# used to be `{}` -- a file that opens, carries nothing, and stood in for the
# thing the check is about. That fixture could not tell a rendering from an
# empty brace, which is the same blindness the check had.
RENDERING = '{"segments": [{"start": 0.0, "end": 2.0, "text": "hello there"}]}\n'


@pytest.fixture
def corpus(tmp_path: Path):
    """A corpus, and a run directory for VID holding one rendering."""
    root = tmp_path.resolve()
    (root / "notes").mkdir()
    runs = root / "runs"
    (runs / "VID").mkdir(parents=True)
    (runs / "VID" / "run.json").write_text(RENDERING, encoding="utf-8")
    (runs / "VID" / "chunks").mkdir()
    for i in (1, 2):
        (runs / "VID" / "chunks" / f"{i}.whisper.json").write_text(
            RENDERING, encoding="utf-8")
    (runs / "OTHER").mkdir()
    (runs / "OTHER" / "run.json").write_text(RENDERING, encoding="utf-8")
    (root / "README.md").write_text("a real file, and not a rendering\n",
                                    encoding="utf-8")

    ambient_runs_root = rn.POLICY._raw["runs_root"]
    ambient_ledger = dict(rn.UNFILLED_ORACLES)
    rn.POLICY._raw["runs_root"] = str(runs)
    rn.UNFILLED_ORACLES.clear()
    try:
        yield root
    finally:
        rn.POLICY._raw["runs_root"] = ambient_runs_root
        rn.UNFILLED_ORACLES.clear()
        rn.UNFILLED_ORACLES.update(ambient_ledger)


def _check(corpus: Path, rel: str = NOTE, **kw) -> list[str]:
    return rn.check_oracle(_fm(**kw), rel, corpus)


def test_a_rendering_under_this_videos_run_directory_is_clean(corpus):
    assert _check(corpus) == []


def test_the_value_may_carry_the_prose_a_reader_needs(corpus):
    """Every filled value in this corpus is `<path> (which model, how many)`.

    The parenthetical is the half a human reads. Treating the whole value as a
    path would report the only way anyone actually writes the field as broken.
    """
    assert _check(corpus, oracle="run.json (turbo, 2129 segments)") == []


def test_a_glob_is_how_a_chunked_run_is_named(corpus):
    """`chunks/*.whisper.json` names a real set of files, not a missing one."""
    assert _check(corpus, oracle="chunks/*.whisper.json") == []
    got = _check(corpus, oracle="chunks/*.none.json")
    assert any("E-ORACLE-UNRESOLVED" in g for g in got), got


def test_an_absolute_path_is_taken_as_written(corpus):
    assert _check(corpus, oracle=str(corpus / "runs" / "VID" / "run.json")) == []


@pytest.mark.parametrize("value,code", [
    (None, "E-ORACLE-MISSING"),
    ("", "E-ORACLE-EMPTY"),
    ("   ", "E-ORACLE-EMPTY"),
    ("no-such-run.json", "E-ORACLE-UNRESOLVED"),
    ("uploaded caption track", "E-ORACLE-UNRESOLVED"),
])
def test_a_field_that_names_no_rendering_is_a_defect(corpus, value, code):
    got = _check(corpus, oracle=value)
    assert any(code in g for g in got), (value, got)


def test_a_file_that_opens_and_carries_no_transcript_is_a_defect(corpus):
    """The seam a ledger row was covering.

    A run manifest sits beside the rendering in the same directory, under this
    video's id, and opens. The resolver accepted it, the oracle gate reported
    the field as fine, and the per-note gates then could not read what it had
    approved -- so the failure surfaced one layer down, as a note that "cannot
    be graded", and was written off in a ledger.

    The question the field is asking is whether a gate can read this. So the
    answer comes from the reader every gate uses, not from whether the path
    exists.
    """
    (corpus / "runs" / "VID" / "manifest.json").write_text(
        '{"video_id": "VID", "duration_seconds": 610, "frames": 12}\n',
        encoding="utf-8")

    got = _check(corpus, oracle="manifest.json")

    assert any("E-ORACLE-UNRESOLVED" in g for g in got), got
    assert any("read" in g for g in got), ("and it says what is wrong", got)


@pytest.mark.parametrize("name,body", [
    ("verbose.json", RENDERING),
    ("cpp.json", '{"transcription": [{"offsets": {"from": 0, "to": 2000}, '
                 '"text": "hello there"}]}\n'),
    ("bare.json", '[{"start": 0.0, "end": 2.0, "text": "hello there"}]\n'),
    ("captions.vtt", "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhello there\n"),
])
def test_every_shape_the_reader_accepts_is_a_rendering(corpus, name, body):
    """The neighbours that keep the refusal from eating the corpus.

    Four shapes arrive in practice and the reader takes all four. A check that
    demanded one of them would refuse most of the renderings this project
    actually writes, which is a worse defect than the one being fixed.
    """
    (corpus / "runs" / "VID" / name).write_text(body, encoding="utf-8")

    assert _check(corpus, oracle=name) == []


def test_a_rendering_that_parses_and_holds_no_segments_is_a_defect(corpus):
    """An empty transcript is not a transcript.

    `{"segments": []}` is the shape the reader accepts carrying nothing at all.
    It opens, it parses, it names the right key, and no gate downstream can say
    a single thing about a note graded against it.
    """
    (corpus / "runs" / "VID" / "empty.json").write_text(
        '{"segments": []}\n', encoding="utf-8")

    got = _check(corpus, oracle="empty.json")

    assert any("E-ORACLE-UNRESOLVED" in g for g in got), got


@pytest.mark.parametrize("value", ["README.md", "../OTHER/run.json"])
def test_a_file_that_is_not_this_videos_rendering_is_a_defect(corpus, value):
    """The placeholder that survives every string rule: a path that opens.

    `oracle: README.md` is a path, resolves, and is not a transcript of
    anything. The lane-report check learned this the hard way; the note field
    inherits the lesson rather than repeating the round.
    """
    got = _check(corpus, oracle=value)
    assert any("E-ORACLE-UNRELATED" in g for g in got), (value, got)


def test_the_message_says_which_video_it_wanted(corpus):
    got = _check(corpus, oracle="README.md")
    assert "VID" in got[0], got


def test_a_note_with_no_video_id_cannot_be_passed_on_its_oracle(corpus):
    """Deleting one line must not turn half the check off.

    The relatedness half needs a video id to compare against, and skipping it
    when there is none left `oracle: /etc/hosts` clean: with nothing to compare
    against, every file that opens is somebody's rendering (round-5 refutation
    F3). The roll-call learned the same lesson as E-LANE-NOVIDEO.
    """
    for value in ("README.md", "/etc/hosts", "run.json"):
        got = _check(corpus, oracle=value, video_id=None)
        assert any("E-ORACLE-NOVIDEO" in g for g in got), (value, got)


def test_a_note_that_declares_its_video_id_is_not_refused_for_lacking_one(corpus):
    """The direction that keeps the rule above from eating the corpus.

    A refusal aimed at "no video id" that also fires when there IS one would
    red every note in the corpus, and its sibling case cannot see that: the
    sibling only ever asks about notes with the line deleted. This rule shipped
    with both its neighbours citing that one function, which is a two-neighbour
    floor met by one case.
    """
    got = _check(corpus, oracle="run.json", video_id="VID")

    assert got == [], got
    assert not any("E-ORACLE-NOVIDEO" in g for g in got), got


def test_two_oracle_rows_naming_two_renderings_are_refused(corpus):
    """One note is written against one rendering.

    `search` takes the first row, so a second is invisible to this check and
    visible to whichever reader searches differently -- the argument the
    roll-call already makes about duplicate video_id rows, unguarded here until
    an independent pass asked for it (round-5 refutation F6).
    """
    fm = _fm(oracle="run.json") + "oracle: README.md\n"
    got = rn.check_oracle(fm, NOTE, corpus)
    assert any("E-ORACLE-TWOROWS" in g for g in got), got


def test_two_identical_oracle_rows_are_not_a_second_answer(corpus):
    """Duplicated, not contradictory. Refusing this would be a typo rule."""
    fm = _fm(oracle="run.json") + "oracle: run.json\n"
    assert rn.check_oracle(fm, NOTE, corpus) == []


# --------------------------------------------------------------------------
# the ledger, which is the half that can rot
# --------------------------------------------------------------------------

def test_a_dated_row_excuses_the_note_it_was_written_for(corpus):
    rn.UNFILLED_ORACLES["2026-08-20--n--VID.md"] = "2026-08-21 note frozen"
    assert _check(corpus, oracle="") == []


def test_a_row_does_not_reach_a_note_filed_after_it(corpus):
    """The whole point of dating the row.

    Keyed by note filename, a row could only ever cover one note -- but the
    same key can be typed again for a note written later, and that is how a
    grandfather list becomes a permanent exemption. The row's own date refuses
    it.
    """
    rn.UNFILLED_ORACLES["2026-08-22--later--VID.md"] = "2026-08-21 note frozen"
    got = _check(corpus, rel="notes/2026-08-22--later--VID.md", oracle="")
    assert any("E-ORACLE-EMPTY" in g for g in got), got


def test_a_row_for_another_note_excuses_nothing(corpus):
    rn.UNFILLED_ORACLES["2026-08-20--somebody-else--VID.md"] = "2026-08-21 frozen"
    got = _check(corpus, oracle="")
    assert any("E-ORACLE-EMPTY" in g for g in got), got


def test_a_row_dated_in_the_future_is_refused_by_the_policy():
    """The other door to permanence, and the calendar check let it through.

    Every row here is aged against something written later, so a row dated 2099
    excuses everything filed between now and then. `9999-99-99` was already
    refused as not a date; `2099-01-01` is a real day and was accepted
    (round-5 refutation F4).
    """
    from watchquality import wq_policy
    with pytest.raises(wq_policy.PolicyError):
        wq_policy.Policy({"unfilled_oracles": {"n.md": "2099-01-01 later"}}, None)
    # And the rule is general: the same date is refused in every dated table.
    for table in wq_policy.TABLES:
        with pytest.raises(wq_policy.PolicyError):
            wq_policy.Policy({table: {"k": "2099-01-01 later"}}, None)


def test_a_row_is_reported_stale_once_the_note_names_a_rendering(corpus):
    """A ledger that never says which row can go does not shrink.

    Read through the ordinary path this question answers itself: the row is
    what makes the note clean. The stale census asks whether the note would be
    clean WITHOUT it (round-5 refutation F5).
    """
    note = corpus / "notes" / "2026-08-20--n--VID.md"
    note.write_text(f"---\n{_fm(oracle='')}---\n{BODY}", encoding="utf-8")
    rn.UNFILLED_ORACLES["2026-08-20--n--VID.md"] = "2026-08-21 frozen"
    assert not [s for s in rn.stale_exemptions(corpus) if "unfilled_oracles" in s]
    note.write_text(f"---\n{_fm(oracle='run.json')}---\n{BODY}", encoding="utf-8")
    stale = [s for s in rn.stale_exemptions(corpus) if "unfilled_oracles" in s]
    assert stale and "paid" in stale[0], stale


def test_a_row_for_a_note_that_is_gone_is_reported_stale(corpus):
    rn.UNFILLED_ORACLES["2026-08-20--vanished--VID.md"] = "2026-08-21 frozen"
    stale = [s for s in rn.stale_exemptions(corpus) if "unfilled_oracles" in s]
    assert stale and "no note by that name" in stale[0], stale


def test_the_ledger_is_empty_without_a_policy():
    """A missing policy may not invent an exemption.

    This is the shape that makes a debt table dangerous: read from a file that
    may not resolve, an absent file would excuse everything rather than
    nothing.
    """
    from watchquality import wq_policy
    assert wq_policy.Policy({}, None).unfilled_oracles() == {}


def test_every_row_in_this_corpus_is_dated_and_reasoned():
    """The validator, exercised on the shape this table actually holds."""
    from watchquality import wq_policy
    with pytest.raises(wq_policy.PolicyError):
        wq_policy.Policy({"unfilled_oracles": {"n.md": "no date here"}}, None)
    with pytest.raises(wq_policy.PolicyError):
        wq_policy.Policy({"unfilled_oracles": {"n.md": "9999-99-99 impossible"}},
                         None)
    ok = wq_policy.Policy({"unfilled_oracles": {"n.md": "2026-08-21 frozen"}},
                          None)
    assert ok.unfilled_oracles() == {"n.md": "2026-08-21 frozen"}


def test_the_check_is_wired_into_check_note(corpus):
    """A check nothing calls is a check nobody has.

    Two lines wire a check into `check_note`, and an independent round found
    six checks pinned only by their own module selftest -- deleting a wiring
    left the whole pytest run green.
    """
    note = corpus / "notes" / "2026-08-20--n--VID.md"
    note.write_text(f"---\n{_fm(oracle='')}---\n{BODY}\n"
                    f"density: 6 words over 10.00 minutes = 0.6 wpm\n",
                    encoding="utf-8")
    defects, _ = rn.check_note(note, corpus, require_density=False)
    assert any("E-ORACLE-EMPTY" in d for d in defects), defects
