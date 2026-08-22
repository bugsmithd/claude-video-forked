"""A header field has to MEAN something, which only a consequence can make true.

Retyping the five header fields killed 65 known-bad values at once, and an
independent reviewer then showed the class was still open at a level no string
rule reaches. `oracle: TODO` is a path. So are `n/a`, `unknown`, `x` and `0`.
Every one of them satisfies the type and names nothing, and `verdict: BLOCK`
satisfied its vocabulary while exiting 0, because no code downstream ever read
the field. A value can be well-typed and still be a placeholder.

Two consequences close that, and both are about what the value CAUSES:

  E-LANE-ORACLE-MISSING  the oracle is opened. A path that resolves to no file
                         is not an oracle, whatever it is spelled. A run that
                         legitimately moved or was reaped is excused by the
                         corpus's own dated `unresolvable_runs` row -- the same
                         table `anchor_manifest` already ages -- and by nothing
                         else.
  E-LANE-BLOCKED         a lane that says BLOCK reds the note. It had no effect
                         at all before: three lanes could rule BLOCK and the
                         audit still printed all gates passed.

No DEBT LEDGER excuses a BLOCK. Those three tables are for work that was done
and lost, or never dispatched, and a BLOCK is neither: it is a live finding
whose exits are to fix the note or to have the lane rule again on what changed.

`unheadered_reviews` is not a debt ledger and it DOES excuse one, along with
everything else in the header -- a video id on that table has no header to
read, so there is no verdict and no oracle either. The last case in this file
asserts exactly that. Saying otherwise here would repeat the overstatement an
independent review already found in the module's own docstring, in the one
direction that matters.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from watchquality import resolve_note as rn

from conftest import RENDERING


BODY = "\n# t\n\nA body the lanes read.\n"


def _fm(video_id: str = "VID", reviews: str | None = "[facts]") -> str:
    # `oracle:` names the rendering the NOTE was written against, and is opened
    # by its own check. The corpus fixture puts a run there; a fixture without
    # one would be red for a reason no case in this file is about.
    lines = [f"video_id: {video_id}", 'duration: "10:00"', "status: distilled",
             f"oracle: runs/{video_id}/run.json"]
    if reviews is not None:
        lines.append(f"reviews: {reviews}")
    return "\n".join(lines) + "\n"


def _header(note_sha: str, lane: str = "facts", verdict: str = "SHIP-WITH-FIXES",
            claims: str = "12", oracle: str = "runs/VID/run.json") -> str:
    return (f"---\nnote_sha256: {note_sha}\noracle: {oracle}\nlane: {lane}\n"
            f"verdict: {verdict}\nclaims_enumerated: {claims}\n---\n\n"
            f"# {lane} lane\n\nWhat the lane found.\n")


@pytest.fixture
def corpus(tmp_path: Path):
    """One note, one review directory, and a run the oracle can actually name."""
    root = tmp_path.resolve()
    (root / "notes" / "reviews" / "VID").mkdir(parents=True)
    (root / "runs" / "VID").mkdir(parents=True)
    (root / "runs" / "VID" / "run.json").write_text(RENDERING, encoding="utf-8")
    (root / "README.md").write_text("a real file, and not an oracle\n",
                                    encoding="utf-8")
    ambient_required = list(rn.REQUIRED_LANES)
    ambient_runs = dict(rn.UNRESOLVABLE_RUNS)
    rn.REQUIRED_LANES[:] = []
    rn.UNRESOLVABLE_RUNS.clear()
    try:
        yield root
    finally:
        rn.REQUIRED_LANES[:] = ambient_required
        rn.UNRESOLVABLE_RUNS.clear()
        rn.UNRESOLVABLE_RUNS.update(ambient_runs)


def _write(corpus: Path, note_sha: str | None = None, **kw) -> None:
    (corpus / "notes" / "reviews" / "VID" / "facts.md").write_text(
        _header(note_sha or rn.note_body_sha256(BODY), **kw), encoding="utf-8")


def _check(corpus: Path, rel: str = "notes/2026-08-20--n--VID.md") -> list[str]:
    return rn.check_lanes(corpus, _fm(), rel, BODY)


# --------------------------------------------------------------------------
# the oracle is opened
# --------------------------------------------------------------------------

def test_an_oracle_that_resolves_is_clean(corpus):
    _write(corpus)
    assert _check(corpus) == []


def test_an_oracle_naming_no_file_is_a_defect(corpus):
    _write(corpus, oracle="runs/VID/no-such-run.json")
    got = _check(corpus)
    assert any("E-LANE-ORACLE-MISSING" in g for g in got), got


def test_a_real_file_that_is_not_this_run_is_a_defect(corpus, tmp_path: Path):
    """The placeholder that survived the first pass: any file that exists.

    An independent review swept the values that RESOLVE and found an unrelated
    README, a system file and the report naming itself all clean. Opening
    something is not the same as opening the run, and the issue asked for the
    latter.
    """
    outside = tmp_path / "unrelated.json"
    outside.write_text(RENDERING, encoding="utf-8")
    for value in ("README.md", "notes/reviews/VID/facts.md", str(outside)):
        _write(corpus, oracle=value)
        got = _check(corpus)
        assert any("E-LANE-ORACLE-UNRELATED" in g for g in got), (value, got)


def test_walking_out_of_the_run_directory_does_not_launder_an_oracle(corpus):
    """The falsifier the first version failed: `runs/<id>/../../README.md`.

    The id half read the raw string and the corpus half read the resolved path,
    so a prefix satisfied one and was arithmetic away from the other. One
    keystroke from the fixtures, which is the same objection that retired
    `is_file()` a round earlier.
    """
    _write(corpus, oracle="runs/VID/../../README.md")
    got = _check(corpus)
    assert any("E-LANE-ORACLE-UNRELATED" in g for g in got), got


def test_the_id_has_to_be_a_whole_name_on_the_path(corpus):
    """A substring test called a lot of things a run that were not one.

    `docs/VID.md` and `VIDeoteca/x.json` both carry the id and neither is a
    rendering of it. The id is a directory or file NAME on the path.
    """
    for value in ("docs/VID.md", "VIDeoteca_archive/run.json"):
        path = corpus / value
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(RENDERING, encoding="utf-8")
        _write(corpus, oracle=value)
        got = _check(corpus)
        assert any("E-LANE-ORACLE-UNRELATED" in g for g in got), (value, got)


def test_a_value_that_is_not_even_a_path_reds_the_note_instead_of_crashing(corpus):
    """`~nosuchuser/x.json` raised out of the gate. A gate may red, not abort.

    The whole premise of this header block is that the field carries whatever
    somebody typed, so a value Python refuses to expand has to be answered,
    not thrown.
    """
    _write(corpus, oracle="~nosuchuser42/x.json")
    got = _check(corpus)
    assert any("E-LANE-ORACLE" in g for g in got), got


def test_a_symlink_cannot_launder_an_oracle_either(corpus):
    """Same hole, a different spelling: the link's own path carries the id."""
    link = corpus / "runs" / "VID" / "looks-like-a-run.json"
    link.symlink_to(corpus / "README.md")
    _write(corpus, oracle="runs/VID/looks-like-a-run.json")
    got = _check(corpus)
    assert any("E-LANE-ORACLE-UNRELATED" in g for g in got), got


def test_a_report_naming_itself_is_not_its_own_oracle(corpus):
    """The id IS in that path, which is why the second half of the rule exists.

    A review is the thing under judgement. Letting one cite itself as the
    source of truth it was measured against is the circularity the whole
    header block was built to break.
    """
    _write(corpus, oracle="notes/reviews/VID/facts.md")
    got = _check(corpus)
    assert any("E-LANE-ORACLE-UNRELATED" in g for g in got), got


@pytest.mark.parametrize("placeholder", ["TODO", "n/a", "unknown", "x", "0",
                                         "tbd", "pending", "-1"])
def test_a_well_typed_placeholder_no_longer_passes(corpus, placeholder):
    """The reviewer's list, and the two nobody has written down yet.

    None of these is refused for being on a list. They are refused for naming
    no file, which is why a spelling nobody has thought of is refused too.
    """
    _write(corpus, oracle=placeholder)
    got = _check(corpus)
    assert any("E-LANE-ORACLE-MISSING" in g for g in got), (placeholder, got)


def test_a_relative_oracle_is_read_against_the_corpus_not_the_cwd(corpus):
    """`cd` may not change a verdict.

    A path resolved against `Path.cwd()` makes the gate's answer depend on
    where it was run from, which is the same defect the policy loader was
    already hardened against.
    """
    _write(corpus, oracle="runs/VID/run.json")
    assert _check(corpus) == []
    _write(corpus, oracle="./runs/VID/run.json")
    assert _check(corpus) == []


def test_an_absolute_oracle_is_taken_as_written(corpus, tmp_path: Path):
    """A run that lives on another disk is still that video's run."""
    elsewhere = tmp_path / "outside" / "VID" / "run.json"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text(RENDERING, encoding="utf-8")
    _write(corpus, oracle=str(elsewhere))
    assert _check(corpus) == []


def test_a_directory_is_not_an_oracle(corpus):
    """`oracle: runs/VID` resolves and is not the artifact a lane read."""
    _write(corpus, oracle="runs/VID")
    got = _check(corpus)
    assert any("E-LANE-ORACLE-MISSING" in g for g in got), got


def test_a_run_the_corpus_has_written_off_is_excused(corpus):
    """The one legitimate exit: a dated row in the table that already ages.

    No new table. `unresolvable_runs` already means exactly this -- the run
    behind this video id cannot be resolved any more -- and `anchor_manifest`
    already prints when such a row has gone stale.
    """
    _write(corpus, oracle="runs/VID/gone.json")
    rn.UNRESOLVABLE_RUNS["VID"] = "2026-08-20 the run behind this video is gone"
    assert _check(corpus, "notes/2026-08-19--n--VID.md") == []


def test_the_excuse_covers_a_missing_run_and_not_any_value_at_all(corpus):
    """A dated row says the run is GONE, which cannot justify a wrong file.

    While the row gated both branches, a corpus with one accepted an unrelated
    README, a system file and the report citing itself. Five rows are live, so
    this was reachable in the corpus, not only in a fixture.
    """
    rn.UNRESOLVABLE_RUNS["VID"] = "2026-08-20 the run behind this video is gone"
    for value in ("README.md", "notes/reviews/VID/facts.md"):
        _write(corpus, oracle=value)
        got = _check(corpus, "notes/2026-08-19--n--VID.md")
        assert any("E-LANE-ORACLE-UNRELATED" in g for g in got), (value, got)


def test_the_message_does_not_call_a_missing_oracle_a_real_file(corpus):
    """The wording, pinned. Every mutation of it survived the suite.

    Under a dated row the missing-file branch is skipped, so the relatedness
    branch is reached for values that open nothing -- and it told the reader
    "a real file" about one, which is the opposite of what it had measured.
    Inverting the wording back also survived, so this asserts both directions.
    """
    rn.UNRESOLVABLE_RUNS["VID"] = "2026-08-20 the run behind this video is gone"
    _write(corpus, oracle="no-such-file.json")
    got = [g for g in _check(corpus, "notes/2026-08-19--n--VID.md")
           if "E-LANE-ORACLE-UNRELATED" in g]
    assert got and "a value that is not a rendering" in got[0], got
    _write(corpus, oracle="README.md")
    got = [g for g in _check(corpus, "notes/2026-08-19--n--VID.md")
           if "E-LANE-ORACLE-UNRELATED" in g]
    assert got and "a real file that is not a rendering" in got[0], got


def test_a_value_python_refuses_to_path_answers_with_the_corpus_root(corpus):
    """The fallback itself, at the unit. Reverting it left the suite green.

    A NUL byte is the value that raises where `expanduser` alone does not, and
    the header parser happens to refuse it first, so end to end it is
    unreachable today. Unreachable is not the same as absent, and a fix nothing
    pins is one the next round deletes as dead.
    """
    assert rn.oracle_path("a\x00b", corpus) == corpus.resolve()
    assert rn.oracle_path("~nosuchuser42/x.json", corpus) == corpus.resolve()


def test_the_excuse_does_not_reach_a_note_filed_after_it(corpus):
    """A debt row excuses what existed when it was written, and nothing later."""
    _write(corpus, oracle="runs/VID/gone.json")
    rn.UNRESOLVABLE_RUNS["VID"] = "2026-08-20 the run behind this video is gone"
    got = _check(corpus, "notes/2026-08-21--n--VID.md")
    assert any("E-LANE-ORACLE-MISSING" in g for g in got), got


def test_a_report_can_be_stale_and_name_a_dead_oracle_at_once(corpus):
    """Both defects in one round, which is why the call sits outside the chain.

    MISLABELLED and STALE are alternatives -- one report cannot be both -- so
    they are an `elif` chain. These two are not alternatives to either, and a
    review folded the call back INTO that chain and watched the suite stay
    green: the placement was argued in a comment and pinned by nothing. This
    is the case that pins it. A repair round that fixed the sha and then
    discovered the oracle is the round this saves.
    """
    _write(corpus, note_sha="0" * 64, oracle="runs/VID/gone.json")
    got = _check(corpus)
    assert any("E-LANE-STALE" in g for g in got), got
    assert any("E-LANE-ORACLE-MISSING" in g for g in got), got


# --------------------------------------------------------------------------
# a verdict decides an exit code
# --------------------------------------------------------------------------

@pytest.mark.parametrize("verdict", ["SHIP", "SHIP-WITH-FIXES", "unstated"])
def test_the_three_verdicts_that_are_not_a_refusal_stay_clean(corpus, verdict):
    _write(corpus, verdict=verdict)
    assert _check(corpus) == []


def test_a_lane_that_says_block_reds_the_note(corpus):
    _write(corpus, verdict="BLOCK")
    got = _check(corpus)
    assert any("E-LANE-BLOCKED" in g for g in got), got


def test_a_blocked_lane_is_named_so_a_reader_knows_which_one(corpus):
    _write(corpus, lane="facts", verdict="BLOCK")
    got = [g for g in _check(corpus) if "E-LANE-BLOCKED" in g]
    assert got and "facts" in got[0], got


def test_no_debt_ledger_can_excuse_a_block(corpus):
    """A BLOCK is a finding, not a debt, so the debt ledgers must not reach it.

    A DEBT LEDGER. `unheadered_reviews` is not one and it does excuse a BLOCK,
    which the last case in this file asserts; a name that said "exemption
    table" here repeated the sentence two reviews had already refuted, one
    layer below the docstring where they caught it.

    Every table is loaded at once here on purpose: the point is not that one of
    them fails to excuse it, but that none of them does.
    """
    _write(corpus, verdict="BLOCK")
    rn.UNRESOLVABLE_RUNS["VID"] = "2026-08-20 run gone"
    rn.LOST_REVIEWS["VID"] = {"facts": "2026-08-20 report gone"}
    rn.UNREVIEWED_NOTES["VID"] = {"facts": "2026-08-20 never dispatched"}
    try:
        got = _check(corpus, "notes/2026-08-19--n--VID.md")
        assert any("E-LANE-BLOCKED" in g for g in got), got
    finally:
        rn.LOST_REVIEWS.pop("VID", None)
        rn.UNREVIEWED_NOTES.pop("VID", None)


def test_a_block_reaches_the_exit_code_a_gate_reads(corpus, capsys):
    """The whole point of the field: the CLI a gate runner calls returns 1.

    `check_lanes` returning a string is not a consequence. A gate runner reads
    an exit code, and this ties one to the other through the real entry point.

    The SHIP run is here so the case cannot pass on any other defect in the
    fixture: the same note, the same flags, one field changed, and the exit
    code has to move with it.
    """
    stamp = rn.current_stamp()
    if stamp is None:
        pytest.skip("the package is not installed, so no note can be stamped")
    note = corpus / "notes" / "2026-08-20--n--VID.md"
    note.write_text(f"---\n{_fm()}graded_with: {stamp}\n---\n{BODY}",
                    encoding="utf-8")
    argv = ["--check", str(note), "--no-require-density"]

    _write(corpus, verdict="SHIP")
    assert rn.main(argv, root=corpus) == 0
    capsys.readouterr()

    _write(corpus, verdict="BLOCK")
    assert rn.main(argv, root=corpus) == 1
    assert "E-LANE-BLOCKED" in capsys.readouterr().out


# --------------------------------------------------------------------------
# what these two checks deliberately do NOT reach
# --------------------------------------------------------------------------

def test_a_corpus_row_excusing_the_whole_header_still_excuses_both(corpus):
    """`unheadered_reviews` means the gate cannot see inside those reports.

    It is stated here rather than left implicit: a video id on that table gets
    no oracle check and no verdict check either, because there is no header to
    read. That is the cost of the row, and the reason it may only shrink.
    """
    _write(corpus, oracle="TODO", verdict="BLOCK")
    ambient = dict(rn.UNHEADERED_REVIEWS)
    rn.UNHEADERED_REVIEWS["VID"] = "2026-08-20 filed before the header existed"
    try:
        assert _check(corpus) == []
    finally:
        rn.UNHEADERED_REVIEWS.clear()
        rn.UNHEADERED_REVIEWS.update(ambient)
