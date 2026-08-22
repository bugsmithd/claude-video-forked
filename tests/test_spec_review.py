"""The cases layer 4 named as neighbours and had nowhere to point at.

`spec/review.toml` is the roll-call and the publication gate written down as a
table: one row per rule, and every row bounded by the wrong answers it must
also refuse. A row may only join that table once every neighbour on it has a
case, so this file is the other half of the promotion -- the neighbours whose
only previous coverage was a module selftest, and the neighbours that had no
coverage at all.

Two rules govern what is written here.

FIRST, every case drives the real function. `check_lanes`, `check_required_lanes`,
`scan_text` and `wq_policy.Policy` are called with a fixture and asked what they
say; nothing here re-implements a rule and compares it against itself, because
two tests that share a regex are one test.

SECOND, every case asserts the DEFECT CODE and something a wrong implementation
would get wrong -- the lane named, the count printed, the path a reader has to
open. `assert defects` passes for the wrong reason, and the wrong reason is how
a message that names neither of two files survived four review rounds.

The neighbours that must NOT fire are here too, beside the ones that must. A
rule with only positive cases is half specified, and the false-positive
direction is where a gate stops being read.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from watchquality import audit, resolve_note as rn, wq_corpus_scan as cs, wq_policy

from conftest import RENDERING


BODY = "\n# t\n\nA body the lanes read.\n"

# Built from pieces on purpose. This file is published, and the publication
# gate refuses an eleven-character mixed-case token carrying a digit wherever
# it finds one -- including in the case that proves the gate refuses it. The
# pieces are four, four and three characters, so no literal here is the shape.
ID_SHAPED = "Spec" + "Fix7" + "aB1"

# Same reason. The dated-exemption rule refuses a calendar date beside a word
# of excuse on one line, so the word is assembled rather than typed, and no
# line of this file carries both.
EXCUSE = "ex" + "empt"


def _fm(video_id: str | None = "VID", reviews: str | None = "[facts]") -> str:
    """A note's frontmatter. `oracle:` names a run the corpus fixture writes."""
    lines: list[str] = []
    if video_id is not None:
        lines.append(f"video_id: {video_id}")
    lines += ['duration: "10:00"', "status: distilled"]
    if video_id is not None:
        lines.append(f"oracle: runs/{video_id}/run.json")
    if reviews is not None:
        lines.append(f"reviews: {reviews}")
    return "\n".join(lines) + "\n"


def _header(note_sha: str, lane: str = "facts", verdict: str = "SHIP-WITH-FIXES",
            claims: str = "12", oracle: str = "runs/VID/run.json") -> str:
    return (f"---\nnote_sha256: {note_sha}\noracle: {oracle}\nlane: {lane}\n"
            f"verdict: {verdict}\nclaims_enumerated: {claims}\n---\n\n"
            f"# {lane} lane\n\nWhat the lane found.\n")


def _sha(body: str = BODY) -> str:
    return rn.note_body_sha256(body)


@pytest.fixture
def corpus(tmp_path: Path):
    """A root with a review directory, a run to name, and no ledger in force.

    Every dated table is emptied and restored around the case, so a row this
    file writes cannot leak into the next one and a row the ambient corpus
    carries cannot quietly excuse a defect a case here is asserting.
    """
    root = tmp_path.resolve()
    (root / "notes" / "reviews" / "VID").mkdir(parents=True)
    (root / "runs" / "VID").mkdir(parents=True)
    (root / "runs" / "VID" / "run.json").write_text(RENDERING, encoding="utf-8")
    ambient_required = list(rn.REQUIRED_LANES)
    ambient = {name: dict(getattr(rn, name))
               for name in ("UNRESOLVABLE_RUNS", "UNHEADERED_REVIEWS",
                            "LOST_REVIEWS", "UNREVIEWED_NOTES")}
    rn.REQUIRED_LANES[:] = []
    for name in ambient:
        getattr(rn, name).clear()
    try:
        yield root
    finally:
        rn.REQUIRED_LANES[:] = ambient_required
        for name, rows in ambient.items():
            getattr(rn, name).clear()
            getattr(rn, name).update(rows)


@pytest.fixture
def note_corpus(tmp_path: Path):
    """A corpus for the NOTE's own `oracle:` field, which layer 3 specifies.

    Separate from `corpus` above because `check_oracle` resolves a relative
    value against `POLICY.runs_root()/<video id>` before it tries the corpus
    root, and a case about that field has to own both. `VIDeoteca` is here for
    one reason: its name carries the video id as a SUBSTRING and not as a
    component, which is the wrong answer the whole-component rule exists to
    refuse and the one no fixture in this repository held.
    """
    root = tmp_path.resolve()
    (root / "notes").mkdir()
    runs = root / "runs"
    (runs / "VID").mkdir(parents=True)
    (runs / "VID" / "run.json").write_text(RENDERING, encoding="utf-8")
    (runs / "VIDeoteca").mkdir()
    (runs / "VIDeoteca" / "run.json").write_text(RENDERING, encoding="utf-8")
    ambient_runs_root = rn.POLICY._raw.get("runs_root")
    ambient_ledger = dict(rn.UNFILLED_ORACLES)
    rn.POLICY._raw["runs_root"] = str(runs)
    rn.UNFILLED_ORACLES.clear()
    try:
        yield root
    finally:
        if ambient_runs_root is None:
            rn.POLICY._raw.pop("runs_root", None)
        else:
            rn.POLICY._raw["runs_root"] = ambient_runs_root
        rn.UNFILLED_ORACLES.clear()
        rn.UNFILLED_ORACLES.update(ambient_ledger)


def _note_fm(oracle: str | None = "run.json", video_id: str | None = "VID") -> str:
    """A note's frontmatter for the layer-3 `oracle:` cases.

    Every other rule on that field is satisfied here -- one `video_id:` row,
    one `oracle:` row, a value that opens -- so the only thing that can redden
    a case below is the boundary it names.
    """
    lines = ['duration: "10:00"', "status: distilled"]
    if video_id is not None:
        lines.append(f"video_id: {video_id}")
    if oracle is not None:
        lines.append(f"oracle: {oracle}")
    return "\n".join(lines) + "\n"


def _reports(corpus: Path, video_id: str = "VID") -> Path:
    d = corpus / "notes" / "reviews" / video_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _codes(lines: list[str]) -> list[str]:
    return [w for line in lines for w in line.split() if w.startswith("E-")]


# --------------------------------------------------------------------------
# E-LANE-MALFORMED -- the lane list itself
# --------------------------------------------------------------------------

def test_an_unreadable_lane_list_stops_the_roll_call_before_it_starts(corpus):
    """The line that turns `lane_ids`' error string into a defect.

    Every other case on this rule stops at `lane_ids`, which returns an error
    and prints nothing. Nothing held `check_lanes` to printing it, and nothing
    held it to returning EARLY: the report on disk below would be reported
    undeclared by any implementation that carried on past the bad list, and a
    roll-call run against lanes it could not read is the one answer this rule
    exists to refuse.
    """
    (_reports(corpus) / "facts.md").write_text("", encoding="utf-8")
    fm = "video_id: VID\nreviews:\n  - facts\n  - quality\n"
    got = rn.check_lanes(corpus, fm, "n.md", BODY)
    assert _codes(got) == ["E-LANE-MALFORMED"], got
    assert "flow sequence" in got[0], got[0]


def test_one_lane_declared_twice_is_unreadable_not_a_shorter_list(corpus):
    """`reviews: [facts, facts]` is a contradiction, not a smaller claim.

    De-duplicating instead would leave the note declaring one lane while its
    author wrote two, and the second one would never be looked for.
    """
    got = rn.check_lanes(corpus, _fm("VID", "[facts, facts]"), "n.md", BODY)
    assert _codes(got) == ["E-LANE-MALFORMED"], got
    assert "declared twice" in got[0] and "facts" in got[0], got[0]


def test_a_note_that_never_mentions_reviews_is_not_a_malformed_one(corpus):
    """The absence that must NOT be refused.

    Most notes were never reviewed by a lane. A rule that read an absent key
    as an unreadable one would red every note in the corpus at once, which is
    the failure mode that gets a gate switched off rather than fixed.
    """
    assert rn.lane_ids("title: t\n") == (None, None)
    assert rn.check_lanes(corpus, "title: t\n", "n.md", BODY) == []


# --------------------------------------------------------------------------
# E-LANE-TWOVIDEOS -- which video the roll-call is addressed to
# --------------------------------------------------------------------------

def test_two_video_ids_in_one_frontmatter_are_refused_before_any_lane(corpus):
    """`search` takes the FIRST row, so the second one sent the roll-call to
    another video's reviews while the note read as if it named one."""
    fm = "video_id: VID\nvideo_id: OTHERVID\nreviews: [facts]\n"
    got = rn.check_lanes(corpus, fm, "n.md", BODY)
    assert _codes(got) == ["E-LANE-TWOVIDEOS"], got
    assert "2 video_id: rows naming 2 different videos" in got[0], got[0]


def test_the_same_video_id_written_twice_is_a_repetition_not_a_contradiction(corpus):
    """The false-positive direction: counting rows rather than answers.

    Two identical rows say one thing twice. Refusing them would convict a
    corpus for a duplicated line that changes no answer, and the rule would be
    about tidiness instead of about which video owns the reviews.
    """
    fm = ("video_id: VID\nvideo_id: VID\n"
          'duration: "10:00"\noracle: runs/VID/run.json\nreviews: []\n')
    assert rn.check_lanes(corpus, fm, "n.md", BODY) == []


def test_two_ids_differing_only_in_letter_case_are_two_videos_here(corpus):
    """`VID` and `vid` are two directories on a case-sensitive filesystem.

    The matcher is exact, so folding case in this comparison would accept a
    frontmatter whose reviews live under a directory the roll-call never
    reads.
    """
    fm = "video_id: VID\nvideo_id: vid\nreviews: [facts]\n"
    got = rn.check_lanes(corpus, fm, "n.md", BODY)
    assert _codes(got) == ["E-LANE-TWOVIDEOS"], got
    assert "2 video_id: rows naming 2 different videos" in got[0], got[0]


def test_a_third_row_agreeing_is_counted_apart_from_the_distinct_answers(corpus):
    """Three rows, two answers, and the message has to say both numbers.

    A line reporting `3 rows naming 3 videos` would send a reader looking for
    a third directory that does not exist, and a line reporting `2 rows` would
    hide the one that is merely repeated.
    """
    fm = "video_id: VID\nvideo_id: OTHERVID\nvideo_id: VID\nreviews: [facts]\n"
    got = rn.check_lanes(corpus, fm, "n.md", BODY)
    assert _codes(got) == ["E-LANE-TWOVIDEOS"], got
    assert "carries 3 video_id: rows naming 2 different videos" in got[0], got[0]


# --------------------------------------------------------------------------
# E-LANE-NOVIDEO -- a declaration nothing can answer
# --------------------------------------------------------------------------

def test_an_empty_list_without_a_video_id_declares_nothing_and_stays_clean(corpus):
    """Declaring no lanes is not declaring lanes nobody can look for.

    A rule keyed on the PRESENCE of `reviews:` rather than on its contents
    would red every note that carries the key with an empty value, which is
    how a note says the review layer did not run.
    """
    assert rn.check_lanes(corpus, "reviews: []\n", "n.md", BODY) == []


def test_an_unreadable_list_without_a_video_id_is_reported_as_unreadable(corpus):
    """Ordering, asserted: the list is read before the video id is missed.

    Both rules can fire on this frontmatter and only one of them is
    actionable. Telling the author there is no video id, when the thing that
    is wrong is the lane list, sends the repair to the wrong line.
    """
    got = rn.check_lanes(corpus, 'reviews: ["facts, quality"]\n', "n.md", BODY)
    assert _codes(got) == ["E-LANE-MALFORMED"], got
    assert "never quoted" in got[0], got[0]


# --------------------------------------------------------------------------
# E-LANE-AMBIGUOUS -- two files answering to one lane
# --------------------------------------------------------------------------

def test_a_lane_ending_in_another_lanes_id_does_not_make_the_report_ambiguous(corpus):
    """`x-facts.md` matches `facts` too, and the longer lane is the one that
    named it.

    A matcher that spent the report on both declared lanes printed a defect
    about a report doing double duty, when the truth is that the shorter lane
    has nothing at all -- which is what the line below has to say instead.
    """
    (_reports(corpus) / "x-facts.md").write_text(
        _header(_sha(), "x-facts"), encoding="utf-8")
    got = rn.check_lanes(corpus, _fm("VID", "[facts, x-facts]"), "n.md", BODY)
    assert _codes(got) == ["E-LANE-MISSING"], got
    assert "lane facts declared" in got[0], got[0]


def test_a_row_admitting_a_lost_report_does_not_reach_a_lane_with_two(corpus):
    """The ledger excuses an ABSENCE, and this lane has an abundance.

    A widening that let the row stand down the whole lane would take a note
    with two conflicting verdicts and print nothing at all, which is the
    silent-clean direction every ledger has to be held away from.
    """
    for sub in ("a", "b"):
        d = _reports(corpus) / sub
        d.mkdir()
        (d / "facts.md").write_text(_header(_sha(), "facts"), encoding="utf-8")
    rn.LOST_REVIEWS["VID"] = {"facts": "2026-08-20 the report was lost"}
    got = rn.check_lanes(corpus, _fm("VID", "[facts]"),
                         "notes/2026-08-20--n--VID.md", BODY)
    assert _codes(got) == ["E-LANE-AMBIGUOUS"], got
    assert "matches 2 reports" in got[0], got[0]


# --------------------------------------------------------------------------
# E-LANE-MISSING -- a lane with nothing answering for it
# --------------------------------------------------------------------------

def test_a_report_name_without_the_dash_answers_for_no_lane(corpus):
    """`artifacts.md` is a different report, not a longer spelling of `facts`.

    Dropping the dash from the suffix match would let any filename ENDING in
    the lane id answer for it, so a report about something else would satisfy
    the roll-call and the real lane would look reviewed. The name is chosen so
    that the two spellings genuinely disagree: `artifacts.md` ends in
    `facts.md` and does not end in `-facts.md`.
    """
    (_reports(corpus) / "artifacts.md").write_text(
        _header(_sha(), "artifacts"), encoding="utf-8")
    got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
    assert sorted(_codes(got)) == ["E-LANE-MISSING", "E-LANE-UNDECLARED"], got
    assert any("lane facts declared" in g for g in got), got
    assert any("artifacts.md" in g for g in got), got


def test_a_lost_report_row_filed_under_another_video_excuses_nothing_here(corpus):
    """The ledger is keyed by video id, and this note is not that video.

    A lookup that read the table rather than the row -- `bool(LOST_REVIEWS)`
    is one keystroke from `LOST_REVIEWS.get(video_id)` -- would let one
    admission anywhere in the corpus stand down every dead lane in it.
    """
    rn.LOST_REVIEWS["OTHERVID"] = {"facts": "2026-08-20 that video lost its report"}
    got = rn.check_lanes(corpus, _fm("VID", "[facts]"),
                         "notes/2026-08-20--n--VID.md", BODY)
    assert _codes(got) == ["E-LANE-MISSING"], got
    assert "lane facts declared" in got[0], got[0]


def test_a_row_excusing_a_missing_header_does_not_excuse_a_missing_report(corpus):
    """`unheadered_reviews` buys a header bypass, and nothing else.

    The row says a report was filed before the header existed. A lane with no
    report at all has no header to bypass, and letting the row reach here
    would turn "we cannot read it" into "it need not exist".
    """
    rn.UNHEADERED_REVIEWS["VID"] = "2026-08-20 filed before the header existed"
    got = rn.check_lanes(corpus, _fm("VID", "[facts]"),
                         "notes/2026-08-20--n--VID.md", BODY)
    assert _codes(got) == ["E-LANE-MISSING"], got
    assert "lane facts declared" in got[0], got[0]


def test_a_row_admitting_a_lane_never_ran_does_not_excuse_one_that_died(corpus):
    """`unreviewed_notes` excuses the DECLARATION floor, one layer up.

    Collapsing the two would let "we never dispatched it" answer for a lane
    the note did declare -- and the policy refuses to hold both admissions for
    one lane precisely so each cannot cover for the other.
    """
    rn.UNREVIEWED_NOTES["VID"] = {"facts": "2026-08-20 never dispatched"}
    got = rn.check_lanes(corpus, _fm("VID", "[facts]"),
                         "notes/2026-08-20--n--VID.md", BODY)
    assert _codes(got) == ["E-LANE-MISSING"], got
    assert "lane facts declared" in got[0], got[0]


# --------------------------------------------------------------------------
# E-LANE-UNDECLARED -- a report the note does not own up to
# --------------------------------------------------------------------------

def test_a_report_one_directory_deeper_is_still_seen_by_the_roll_call(corpus):
    """One `mkdir` made a report invisible, and filing by date is the obvious
    thing a future run does.

    A non-recursive walk would report nothing here, and reporting nothing is
    indistinguishable from a clean corpus.
    """
    sub = _reports(corpus) / "2026-08-20"
    sub.mkdir()
    (sub / "facts.md").write_text(_header(_sha(), "facts"), encoding="utf-8")
    got = rn.check_lanes(corpus, _fm("VID", "[]"), "n.md", BODY)
    assert _codes(got) == ["E-LANE-UNDECLARED"], got
    assert str(Path("2026-08-20") / "facts.md") in got[0], got[0]


def test_a_report_for_a_lane_nobody_declared_is_named_in_the_line(corpus):
    """The report that exists and answers for nothing the note claims to have
    run. Naming it is the whole content of the defect: a reader who is not
    told which file cannot tell an extra report from a mis-declared one."""
    (_reports(corpus) / "facts.md").write_text(
        _header(_sha(), "facts"), encoding="utf-8")
    (_reports(corpus) / "quality.md").write_text(
        _header(_sha(), "quality"), encoding="utf-8")
    got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
    assert _codes(got) == ["E-LANE-UNDECLARED"], got
    assert "quality.md" in got[0] and "facts.md" not in got[0], got[0]


def test_the_same_buried_report_is_clean_once_its_lane_is_declared(corpus):
    """The false-positive direction of the recursive walk.

    A rule that refused any report below the video directory would convict the
    corpus for its own filing convention, so the deeper path has to be the
    thing that is FOUND rather than the thing that is wrong.
    """
    sub = _reports(corpus) / "2026-08-20"
    sub.mkdir()
    (sub / "buried.md").write_text(_header(_sha(), "buried"), encoding="utf-8")
    assert rn.check_lanes(corpus, _fm("VID", "[buried]"), "n.md", BODY) == []


def test_a_report_filed_under_another_videos_directory_is_not_this_notes(corpus):
    """The roll-call is addressed by video id, in both directions.

    Widening the walk to the whole review tree would make every note answer
    for every other video's reports, and the first symptom would be a corpus
    where no note can ever be clean.
    """
    (_reports(corpus, "OTHERVID") / "facts.md").write_text(
        _header(_sha(), "facts"), encoding="utf-8")
    assert rn.check_lanes(corpus, _fm("VID", "[]"), "n.md", BODY) == []


# --------------------------------------------------------------------------
# E-LANE-UNPARSED -- a report that is not a review
# --------------------------------------------------------------------------

def test_the_header_bypass_stops_at_a_note_filed_after_the_row(corpus):
    """Membership alone made this the one table that covered future work.

    Two notes about one video share the row, and re-watching a video already
    on the ledger is the single most likely reason a second note exists. The
    row's own date is what stops it reaching that note; without the date
    comparison the same zero-byte report buys a clean bill for a note written
    a year later.
    """
    (_reports(corpus) / "facts.md").write_text("", encoding="utf-8")
    rn.UNHEADERED_REVIEWS["VID"] = "2026-08-20 filed before the header existed"
    fm = _fm("VID", "[facts]")
    assert rn.check_lanes(corpus, fm, "notes/2026-08-19--n--VID.md", BODY) == []
    got = rn.check_lanes(corpus, fm, "notes/2026-08-21--n--VID.md", BODY)
    assert _codes(got) == ["E-LANE-UNPARSED"], got
    assert "no header block" in got[0], got[0]


# --------------------------------------------------------------------------
# E-LANE-MISLABELLED -- the header answers for the wrong lane
# --------------------------------------------------------------------------

def test_a_header_answering_for_the_lane_that_claimed_it_is_clean(corpus):
    """The false-positive direction, and the only case that proves the
    comparison is a comparison rather than a refusal."""
    (_reports(corpus) / "facts.md").write_text(
        _header(_sha(), "facts"), encoding="utf-8")
    assert rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY) == []


def test_a_header_naming_a_lane_no_note_declares_names_both_sides(corpus):
    """Not another declared lane -- a lane that exists nowhere.

    The message has to carry the lane the header claims AND the lane the
    filename was read as, because those are the two lines a reader has to
    reconcile and either one alone reads as somebody else's mistake.
    """
    (_reports(corpus) / "facts.md").write_text(
        _header(_sha(), "ghostlane"), encoding="utf-8")
    got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
    assert _codes(got) == ["E-LANE-MISLABELLED"], got
    assert "answers for lane ghostlane" in got[0], got[0]
    assert "read as facts" in got[0], got[0]


def test_a_lane_field_differing_only_in_case_is_refused_as_unreadable(corpus):
    """`lane: Facts` is not a lane id, so it never reaches the comparison.

    Folding case in the header type would let a report claim a lane no note
    could ever declare, and the mismatch would then read as a missing review
    rather than as a malformed report. The defect is asserted here so the
    ORDER is a decision rather than an accident.
    """
    (_reports(corpus) / "facts.md").write_text(
        _header(_sha(), "Facts"), encoding="utf-8")
    got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
    assert _codes(got) == ["E-LANE-UNPARSED"], got
    assert "lane is not" in got[0] and "Facts" in got[0], got[0]


# --------------------------------------------------------------------------
# E-LANE-STALE -- the header pins a body that is no longer there
# --------------------------------------------------------------------------

def test_one_edit_to_the_note_turns_every_report_on_it_red_at_once(corpus):
    """Every lane read the body, so every lane's verdict is about the old one.

    Reporting only the first would send the repair back three times, and a
    reader would believe two of the three reviews still stood.
    """
    for lane in ("facts", "quality", "coverage"):
        (_reports(corpus) / f"{lane}.md").write_text(
            _header(_sha(), lane), encoding="utf-8")
    fm = _fm("VID", "[facts, quality, coverage]")
    assert rn.check_lanes(corpus, fm, "n.md", BODY) == []
    got = rn.check_lanes(corpus, fm, "n.md", BODY + "\nA sentence added later.\n")
    assert _codes(got) == ["E-LANE-STALE"] * 3, got
    assert all(_sha()[:12] in g for g in got), got


def test_a_mark_a_script_wrote_is_not_an_edit_a_lane_has_to_read_again(corpus):
    """The false-positive direction, and it is not a small one.

    The demotion renderer writes into the note. Letting those bytes move the
    hash would turn every report on every rendered note red for prose no
    author wrote, and the rule would be measuring the tool instead of the
    text.
    """
    marked = f"\n# t\n\nA body the lanes read. {rn.ORPHAN_MARK}\n"
    assert marked != BODY
    assert rn.note_body_sha256(marked) == _sha()
    (_reports(corpus) / "facts.md").write_text(
        _header(_sha(), "facts"), encoding="utf-8")
    assert rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", marked) == []


def test_a_report_that_is_mislabelled_and_stale_is_reported_as_mislabelled(corpus):
    """One report cannot be both, and the chain says which one wins.

    The two are alternatives on purpose: a header answering for another lane
    was never measured against this note, so the pinned body is not evidence
    of anything. Asserted so the ordering is written down rather than
    inherited from the shape of an `elif`.
    """
    (_reports(corpus) / "facts.md").write_text(
        _header("0" * 64, "quality"), encoding="utf-8")
    got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
    assert _codes(got) == ["E-LANE-MISLABELLED"], got
    assert "quality" in got[0], got[0]


# --------------------------------------------------------------------------
# E-LANE-UNREVIEWED -- the floor a roll-call of declarations cannot see
# --------------------------------------------------------------------------

def test_a_note_that_declared_nothing_is_short_of_every_required_lane(corpus):
    """The whole defect: a roll-call of declarations cannot see a note that
    declared none, so the note reached the corpus stamped and gated with the
    review layer skipped and no rule fired."""
    rn.REQUIRED_LANES[:] = ["facts", "quality"]
    got = rn.check_required_lanes("video_id: VID\nreviews: []\n",
                                  "2026-08-20--n--VID.md")
    assert _codes(got) == ["E-LANE-UNREVIEWED"] * 2, got
    assert any("lane facts is required" in g for g in got), got
    assert any("lane quality is required" in g for g in got), got


def test_an_absent_reviews_key_is_the_same_absence_as_an_empty_list(corpus):
    """`reviews: []` and no `reviews:` at all say the same thing.

    Reading only the key would let the whole floor be disarmed by deleting one
    line, which is the same one-line disarming the video id rule already had
    to close.
    """
    rn.REQUIRED_LANES[:] = ["facts"]
    absent = rn.check_required_lanes("video_id: VID\n", "2026-08-20--n--VID.md")
    empty = rn.check_required_lanes("video_id: VID\nreviews: []\n",
                                    "2026-08-20--n--VID.md")
    assert absent == empty, (absent, empty)
    assert _codes(absent) == ["E-LANE-UNREVIEWED"], absent
    assert "lane facts is required" in absent[0], absent[0]


def test_a_row_admitting_a_lost_report_does_not_excuse_never_declaring_it(corpus):
    """The other half of the pair the policy refuses to hold together.

    `lost_reviews` says a lane RAN and its report is gone. It cannot answer
    for a note that never declared the lane, or the corpus could clear its
    floor by admitting to work it never did.
    """
    rn.REQUIRED_LANES[:] = ["facts"]
    rn.LOST_REVIEWS["VID"] = {"facts": "2026-08-20 the report was lost"}
    got = rn.check_required_lanes("video_id: VID\nreviews: []\n",
                                  "2026-08-20--n--VID.md")
    assert _codes(got) == ["E-LANE-UNREVIEWED"], got
    assert "lane facts is required" in got[0], got[0]


# --------------------------------------------------------------------------
# E-CORPUS-VIDEO-ID and E-CORPUS-DATED-EXEMPTION -- the publication gate
# --------------------------------------------------------------------------

def test_an_id_shaped_token_is_refused_and_the_line_is_named():
    """The first pytest on this rule; until now only the module's own selftest
    asserted it, and a selftest that stops running stops failing.

    The line number is asserted because the defect is an instruction to go and
    delete something: a report that names the file and not the line makes the
    reader search a corpus for a token they are not allowed to paste.
    """
    text = f"a first line\nsome prose about {ID_SHAPED} here\n"
    got = cs.scan_text(text, "docs/page.md")
    assert _codes(got) == ["E-CORPUS-VIDEO-ID"], got
    assert got[0].startswith("docs/page.md:2 "), got[0]
    assert ID_SHAPED in got[0], got[0]


def test_a_dated_excuse_written_into_source_is_refused_and_the_line_is_named():
    """Also the first pytest on this rule.

    A dated excuse belongs in the policy file, where it ages and where the
    corpus can see it. Written into source it is permanent and invisible, and
    the reader who has to move it needs the line.
    """
    text = f"a first line\na row {EXCUSE}ed on 2026-08-20 because of a rename\n"
    got = cs.scan_text(text, "docs/page.md")
    assert _codes(got) == ["E-CORPUS-DATED-EXEMPTION"], got
    assert got[0].startswith("docs/page.md:2 "), got[0]
    assert "watch-quality.toml" in got[0], got[0]


def test_a_date_and_an_excuse_too_far_apart_are_not_one_sentence():
    """The window is the whole rule, and it has to have an edge.

    Without one, any file carrying a date anywhere and the word anywhere would
    be refused, and the gate would be answered by deleting the word rather
    than by moving the row. The pair is asserted in BOTH orders, because the
    pattern that reads a date first and the pattern that reads the word first
    are two branches and only one of them was ever exercised.

    The gap is measured AT THE EDGE, and that is the repair. This case used to
    put 120 characters between the two, which measures "further than 119" and
    left the window free to be widened to 118 with nothing going red. Eighty
    characters apart is one sentence and eighty-one is not, so both numbers
    are written here and a window moved either way fails.
    """
    edge, over = " " * 80, " " * 81
    for gap, wanted in ((edge, ["E-CORPUS-DATED-EXEMPTION"]), (over, [])):
        first = cs.scan_text(f"2026-08-20{gap}{EXCUSE}ed\n", "docs/page.md")
        second = cs.scan_text(f"{EXCUSE}ed{gap}2026-08-20\n", "docs/page.md")
        assert _codes(first) == wanted, (len(gap), first)
        assert _codes(second) == wanted, (len(gap), second)


def test_a_date_and_an_excuse_on_two_lines_are_not_one_row():
    """This rule reads one line at a time, and that is a decision.

    A whole-file search would refuse a changelog that dates its entries and a
    glossary that defines the word, which is most of the prose in a
    repository. The cost is that a row split across two lines is not seen, and
    it is written down here rather than discovered later.
    """
    assert cs.scan_text(f"2026-08-20\n{EXCUSE}ed for a rename\n",
                        "docs/page.md") == []


# --------------------------------------------------------------------------
# the policy, which raises rather than prints
# --------------------------------------------------------------------------

def test_a_required_lane_that_is_not_a_string_is_refused_at_load_time():
    """A number cannot be declared by any note, so nothing could satisfy it.

    The type check has to come first: `RE_LANE_ID.match(7)` raises `TypeError`
    and a policy loader that aborts with a traceback is a corpus nobody can
    check at all.
    """
    with pytest.raises(wq_policy.PolicyError) as exc:
        wq_policy.Policy({"required_lanes": [7]}, None)
    assert "required_lanes" in str(exc.value), str(exc.value)
    assert "7" in str(exc.value), str(exc.value)


def test_a_required_lane_starting_with_a_dash_is_refused_at_load_time():
    """The note side refuses it too, and that is the point.

    One lane-id rule, both ends: a floor requiring `-facts` could only ever be
    cleared by a debt row for a typo, and the corpus would carry a permanent
    exception for a character.
    """
    assert not wq_policy.RE_LANE_ID.match("-facts")
    with pytest.raises(wq_policy.PolicyError) as exc:
        wq_policy.Policy({"required_lanes": ["-facts"]}, None)
    assert "-facts" in str(exc.value), str(exc.value)


def test_one_video_may_lose_one_lane_and_never_dispatch_another():
    """The overlap rule is about a PAIR, not about a video.

    Keying it on the video id alone would refuse an honest policy -- a corpus
    that lost the facts report and never ran the quality lane is describing
    two different things -- and a rule that refuses honest rows gets deleted
    rather than narrowed.
    """
    wq_policy.Policy(
        {"lost_reviews": {"VID": {"facts": "2026-08-20 the report was lost"}},
         "unreviewed_notes": {"VID": {"quality": "2026-08-20 never dispatched"}}},
        None)


def test_one_lane_may_be_lost_for_one_video_and_never_dispatched_for_another():
    """The same rule from the other side: keying on the lane id alone.

    Two videos are two histories. Refusing this would make a corpus unable to
    admit that the same lane failed in two different ways across its notes.
    """
    wq_policy.Policy(
        {"lost_reviews": {"VID": {"facts": "2026-08-20 the report was lost"}},
         "unreviewed_notes": {"OTHERVID": {"facts": "2026-08-20 never dispatched"}}},
        None)


def test_a_refused_policy_is_an_exit_code_the_corpus_gate_returns(
        tmp_path: Path, monkeypatch, capsys):
    """The half of `exit = 2` that is true, and nothing asserted it.

    Eleven neighbours across the three policy rows call `Policy(...)` and
    assert a raise. A raise is not an exit code, and the number in the table is
    the thing a gate runner reads, so the two were never connected. This
    connects them for the entry point that CATCHES the refusal.
    """
    def refuse(*_a, **_kw):
        raise wq_policy.PolicyError(
            "required_lanes: 'Facts' is not a lane id a note could declare")

    monkeypatch.setattr(wq_policy, "load", refuse)
    (tmp_path / "page.md").write_text("nothing to find here\n", encoding="utf-8")
    assert cs.main([str(tmp_path)]) == 2
    assert "is not a lane id" in capsys.readouterr().err


def test_the_note_gate_never_turns_a_refused_policy_into_an_exit_code(tmp_path: Path):
    """And the half that is not, which is why the row now says so.

    `resolve_note` loads the policy at IMPORT, and no line in it mentions
    `PolicyError`. So a corpus whose policy is refused does not get the 2 the
    table promised from the gate that grades its notes: the exception escapes,
    Python prints a traceback, and the process exits 1 -- the code that means
    "defects found", which is the collapse the aggregator rule one file over
    exists to refuse.
    """
    bad = tmp_path / "watch-quality.toml"
    bad.write_text('required_lanes = ["Facts"]\n', encoding="utf-8")
    env = dict(os.environ, WATCH_QUALITY_POLICY=str(bad))
    proc = subprocess.run(
        [sys.executable, "-c", "import watchquality.resolve_note"],
        env=env, capture_output=True, text=True)
    assert proc.returncode == 1, (proc.returncode, proc.stderr)
    assert "PolicyError" in proc.stderr, proc.stderr
    assert "Traceback" in proc.stderr, proc.stderr


def test_a_gate_that_found_defects_is_not_lifted_to_the_could_not_run_code(
        monkeypatch, capsys):
    """`exit = 2` is for a gate that could not ANSWER, not for a red corpus.

    The trigger used to read "any gate returns a non-zero code", which is the
    opposite of the distinction the rule is for: a gate returning 1 has run
    perfectly well and found defects, and the run has to carry that 1 outward.
    Lifting it to 2 would tell a reader to go and fix an install when what is
    broken is the corpus.
    """
    # This repository carries no `watch-quality.toml`, and a run that found no
    # policy exits 2 before any gate runs -- which would pass this case for the
    # one reason it is not about. The neutral-defaults run is asked for by name.
    monkeypatch.setenv(wq_policy.ENV_NO_POLICY, "1")
    monkeypatch.setattr(audit, "GATES", [("a", [])])
    monkeypatch.setattr(audit, "_load", lambda n: (lambda argv: 1))
    assert audit.main(["--quiet"]) == 1
    out = capsys.readouterr().out
    assert "# 1 gate(s) found defects: a" in out
    assert "did not complete" not in out


def test_an_impossible_date_beside_an_excuse_is_still_refused_here():
    """The publication gate reads a date SHAPE, and that is deliberate.

    The policy loader refuses `9999-99-99` because a row that cannot age is
    permanent. This gate is asking a different question -- is a dated excuse
    written into published source -- and the answer does not depend on the day
    being real. The row said "calendar date" and meant the policy loader's
    word, so the two files disagreed about the same sentence.
    """
    got = cs.scan_text(f"a row 9999-99-99 {EXCUSE}ed for a rename\n", "docs/page.md")
    assert _codes(got) == ["E-CORPUS-DATED-EXEMPTION"], got
    assert got[0].startswith("docs/page.md:1 "), got[0]


# --------------------------------------------------------------------------
# layer 3, the note's own `oracle:` field -- `spec/gate.toml`
# --------------------------------------------------------------------------

def test_the_note_oracle_id_has_to_be_a_whole_name_on_the_path(note_corpus):
    """The load-bearing case the row named and no fixture held.

    Every existing neighbour on this rule uses a path with no video id in it
    at all -- `README.md`, `../OTHER/run.json` -- so a substring test and a
    whole-component test give the same answer on all of them, and replacing
    `video_id not in hit.resolve().parts` with `video_id not in
    str(hit.resolve())` left the whole suite green. Under that reading a note
    may name a file whose directory merely STARTS with the id and be graded
    clean by every gate downstream.

    Both directions are here: the run directory named exactly, which must stay
    clean, and the neighbouring directory whose name only contains the id.
    """
    assert rn.check_oracle(_note_fm(), "notes/n.md", note_corpus) == []
    got = rn.check_oracle(_note_fm(oracle="../VIDeoteca/run.json"),
                          "notes/n.md", note_corpus)
    assert _codes(got) == ["E-ORACLE-UNRELATED"], got
    assert "VIDeoteca" in got[0] and "not a rendering of VID" in got[0], got[0]


def test_a_capitalised_oracle_key_is_not_the_field(note_corpus):
    """`Oracle:` is not `oracle:`, and the row says so.

    The field pattern is anchored and case-sensitive. Both boundaries this row
    names were pinned to a parametrised case whose five values are all VALUES
    rather than keys, so folding case into the pattern was suite-green -- and
    a corpus in which two spellings of one key both work has no key at all.
    """
    fm = 'duration: "10:00"\nstatus: distilled\nvideo_id: VID\nOracle: run.json\n'
    got = rn.check_oracle(fm, "notes/n.md", note_corpus)
    assert _codes(got) == ["E-ORACLE-MISSING"], got
    assert "no oracle: field" in got[0], got[0]


def test_an_indented_oracle_row_is_not_the_field(note_corpus):
    """The other boundary: YAML would read this row and this parser does not.

    Indenting the key under something else is how a field silently stops being
    read, and the note would then be graded as though it had never declared a
    rendering -- which is exactly what this code says, and what nothing held
    it to.
    """
    fm = ('duration: "10:00"\nstatus: distilled\nvideo_id: VID\n'
          "meta:\n  oracle: run.json\n")
    got = rn.check_oracle(fm, "notes/n.md", note_corpus)
    assert _codes(got) == ["E-ORACLE-MISSING"], got
    assert "no oracle: field" in got[0], got[0]


def test_an_empty_second_oracle_row_is_a_second_answer(note_corpus):
    """One row empty and one filled, which the row named and its case did not.

    The neighbour pointed at a fixture writing two FILLED rows, so dropping
    empty rows before counting the distinct answers was suite-green. Under
    that reading `oracle: run.json` followed by a bare `oracle:` is silently
    one answer, and the blank row -- which is what a half-finished edit leaves
    behind -- is never seen by anything.
    """
    fm = _note_fm(oracle="run.json") + "oracle:\n"
    got = rn.check_oracle(fm, "notes/n.md", note_corpus)
    assert _codes(got) == ["E-ORACLE-TWOROWS"], got
    assert "2 oracle: rows" in got[0], got[0]


def test_a_block_list_under_the_oracle_key_reads_as_an_empty_value(note_corpus):
    """The neighbour that had no parameter, written as its own case.

    A block list is how somebody would name two renderings if the field took
    a list, and it does not: the key's own row is empty and the item below it
    is never read. Refused as EMPTY rather than as unresolvable, because the
    value really is absent and telling the author their path did not open
    would send the repair to a line that has no path on it.
    """
    fm = ('duration: "10:00"\nstatus: distilled\nvideo_id: VID\n'
          "oracle:\n  - run.json\n")
    got = rn.check_oracle(fm, "notes/n.md", note_corpus)
    assert _codes(got) == ["E-ORACLE-EMPTY"], got
    assert "oracle: is empty" in got[0], got[0]


@pytest.mark.parametrize("value", ["/**/*.json", "/*/*/*/*.json", "~/**/*.json"])
def test_a_globbed_oracle_is_bounded_to_the_places_a_rendering_can_be(value):
    """V2 finding V2-5 — the anchor of an absolute glob is `/`.

    `_first_match` splits a pattern at its first wildcard and globs from the
    part before it. For a relative value that part is inside the corpus and the
    walk is bounded; for an absolute one it is the filesystem root, so a note
    carrying `oracle: /**/*.json` sends both the resolver and the note gate
    across the whole disk. Killed under `timeout 20`, rc 124, twice.

    A rendering lives under the corpus or under the runs root. A pattern whose
    anchor is neither names no rendering, whatever it matches, so it is refused
    without walking anything.

    Would fail if: `_first_match` stops checking where its anchor is.
    """
    import time
    start = time.monotonic()
    got = rn._first_match(Path(value).expanduser(), (Path("/no/such/corpus"),))
    took = time.monotonic() - start

    assert got is None, got
    assert took < 2.0, f"answered in {took:.1f}s, which is a walk not a lookup"


def test_a_globbed_oracle_inside_the_corpus_still_resolves(tmp_path):
    """The neighbour that keeps the bound from being a refusal of everything.

    `watch-whisper/chunks/*.whisper.json` is how a chunked run is written down
    and it names a real set of files. That is why the glob is read at all.
    """
    root = tmp_path.resolve()
    chunks = root / "runs" / "VID" / "chunks"
    chunks.mkdir(parents=True)
    (chunks / "a.whisper.json").write_text("{}", encoding="utf-8")

    got = rn._first_match(root / "runs/VID/chunks/*.whisper.json", (root,))

    assert got is not None and got.name == "a.whisper.json", got

    # ...and the same pattern, with the corpus somewhere else, resolves to
    # nothing. The bound is where the anchor sits, not what it matches.
    assert rn._first_match(root / "runs/VID/chunks/*.whisper.json",
                           (Path("/no/such/corpus"),)) is None


def test_what_a_row_costs_a_note_whose_filename_carries_no_date(note_corpus):
    """The unlisted half of "a row may only shrink", stated out loud.

    The row is aged against the note's FILENAME date, and a note that has none
    cannot be placed either side of the line. What happens then is a real
    decision the table did not mention, and it is now TWO decisions, split by
    how much the row forgives.

    The narrow tables forgive one thing -- a lane's lost report, a run that has
    gone -- and there an absence is still left excused rather than convicted.
    The three wide ones forgive the whole grading layer, the whole report
    header, or the field that decides which rendering a note is graded against,
    and there it is not: the widest excuse in the policy may not rest on the
    weakest evidence in it (round-4 F-7, round-13 F3).
    """
    ROW = "2026-08-21 note frozen"
    assert rn.excused(ROW, "n.md") is True
    assert rn.excused(ROW, "n.md", undated=False) is False

    # `unfilled_oracles` is one of the three, so the undated note is graded.
    rn.UNFILLED_ORACLES["n.md"] = ROW
    got = rn.check_oracle(_note_fm(oracle=""), "n.md", note_corpus)
    assert _codes(got) == ["E-ORACLE-EMPTY"], got
    # And a note that IS dated, and pre-dates the row, is still excused -- so
    # this case cannot pass by the ledger having been switched off altogether.
    rn.UNFILLED_ORACLES["2026-08-20--earlier--VID.md"] = ROW
    assert rn.check_oracle(_note_fm(oracle=""),
                           "notes/2026-08-20--earlier--VID.md",
                           note_corpus) == []
    # ...and one filed after it is not.
    rn.UNFILLED_ORACLES["2026-08-22--later--VID.md"] = ROW
    got = rn.check_oracle(_note_fm(oracle=""),
                          "notes/2026-08-22--later--VID.md", note_corpus)
    assert _codes(got) == ["E-ORACLE-EMPTY"], got


# --------------------------------------------------------------------------
# the neighbours layer 4 named and counted twice, or did not name at all
# --------------------------------------------------------------------------

def test_a_lane_list_that_is_not_bracketed_is_refused_as_unbracketed(corpus):
    """`reviews: facts` is one lane to a reader and no list to this parser.

    One of the two refusals on this row that had no neighbour at all. Deleting
    the bracket check does not make the value unreadable: `raw[1:-1]` turns
    `facts` into `act`, which IS a lane id, so the note would declare a lane
    nobody wrote and the roll-call would go looking for `act.md`.
    """
    got = rn.check_lanes(corpus, _fm("VID", "facts"), "n.md", BODY)
    assert _codes(got) == ["E-LANE-MALFORMED"], got
    assert "expected a bracketed list" in got[0] and "facts" in got[0], got[0]


def test_an_entry_that_is_not_a_lane_id_is_refused_by_name(corpus):
    """The other refusal with no neighbour, and the id rule is shared.

    A capital is not a lane id at either end: the policy refuses to REQUIRE
    one and the note is refused for DECLARING one. Without this check the
    declaration is believed, the roll-call looks under a name no report can
    carry, and the answer arrives as a missing lane rather than as a typo.
    """
    got = rn.check_lanes(corpus, _fm("VID", "[FACTS]"), "n.md", BODY)
    assert _codes(got) == ["E-LANE-MALFORMED"], got
    assert "not a lane id: FACTS" in got[0], got[0]


def test_a_quoted_item_is_refused_as_quoted_and_not_as_a_bad_lane_id(corpus):
    """The neighbour that was passing on the wrong check.

    Its case asserted only that `lane_ids` returned an error, and with the
    quote check removed it still did -- the halves `"facts` and `quality"`
    fail the id pattern, so the SECOND check convicted and the case never
    noticed. The message is asserted here, so the two checks are told apart.
    """
    ids, err = rn.lane_ids('reviews: ["facts, quality"]\n')
    assert ids is None and "never quoted" in err, (ids, err)
    got = rn.check_lanes(corpus, _fm("VID", '["facts, quality"]'), "n.md", BODY)
    assert _codes(got) == ["E-LANE-MALFORMED"], got
    assert "never quoted" in got[0], got[0]


def test_a_report_that_is_not_markdown_answers_for_no_lane(corpus):
    """A report is a `.md` file, and the row never said so.

    `lane_reports` yields `*.md` only, so a `facts.txt` under this video's
    review directory is not a report: it answers for no lane, and it is not
    reported as undeclared either. Both halves are here, because the second is
    the surprising one -- a file sitting in the review directory that the
    roll-call is silent about.
    """
    (_reports(corpus) / "facts.txt").write_text(
        _header(_sha(), "facts"), encoding="utf-8")
    assert rn.check_lanes(corpus, _fm("VID", "[]"), "n.md", BODY) == []
    got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
    assert _codes(got) == ["E-LANE-MISSING"], got
    assert "lane facts declared" in got[0], got[0]


def test_a_header_naming_the_shorter_lane_the_file_was_not_spent_on(corpus):
    """Which lane a report was spent on, and it is exactly one.

    `x-facts.md` matches both `facts` and `x-facts`, and the file goes to the
    LONGEST declared lane that matches it -- the one that named it most
    precisely. So a header reading `lane: facts` on that file answers for a
    lane the file was not spent on, and the shorter lane is separately short a
    report. Both lines are asserted, because a matcher that spent the file the
    other way would print neither.
    """
    (_reports(corpus) / "x-facts.md").write_text(
        _header(_sha(), "facts"), encoding="utf-8")
    got = rn.check_lanes(corpus, _fm("VID", "[facts, x-facts]"), "n.md", BODY)
    assert sorted(_codes(got)) == ["E-LANE-MISLABELLED", "E-LANE-MISSING"], got
    assert any("answers for lane facts" in g and "read as x-facts" in g
               for g in got), got
    assert any("lane facts declared" in g for g in got), got


def test_a_dated_row_admitting_a_lane_never_ran_clears_the_floor(corpus):
    """The only exit this rule has, and six neighbours never showed it working.

    Five proved the refusal and the sixth proved a `lost_reviews` row does NOT
    excuse it, so a lookup that never found its row would have looked exactly
    like a rule with no exit -- and the corpus would have no way to admit a
    lane it never dispatched.
    """
    rn.REQUIRED_LANES[:] = ["facts", "quality"]
    rn.UNREVIEWED_NOTES["VID"] = {"facts": "2026-08-20 never dispatched"}
    got = rn.check_required_lanes("video_id: VID\nreviews: []\n",
                                  "2026-08-20--n--VID.md")
    assert _codes(got) == ["E-LANE-UNREVIEWED"], got
    assert "lane quality is required" in got[0], got[0]


def test_the_row_admitting_a_lane_never_ran_stops_at_a_later_note(corpus):
    """And it ages, like every other dated row.

    Two notes about one video share the key, so membership alone would let one
    admission clear the floor for every note written after it -- which is the
    permanence the date exists to refuse, in the one table that governs
    whether the review layer ran at all.
    """
    rn.REQUIRED_LANES[:] = ["facts"]
    rn.UNREVIEWED_NOTES["VID"] = {"facts": "2026-08-20 never dispatched"}
    assert rn.check_required_lanes("video_id: VID\nreviews: []\n",
                                   "2026-08-19--n--VID.md") == []
    got = rn.check_required_lanes("video_id: VID\nreviews: []\n",
                                  "2026-08-21--n--VID.md")
    assert _codes(got) == ["E-LANE-UNREVIEWED"], got
    assert "lane facts is required" in got[0], got[0]


def test_the_floor_reaches_the_stamp_entry_point_and_stops_the_write(corpus):
    """The stamp grades as hard as a check run, on THIS rule.

    The neighbour used to name a stamp case whose fixture declares all three
    required lanes and files a report for each, so this rule never fired in
    it. The fixture here is clean in every other respect -- it stamps when the
    corpus requires nothing -- and the floor is the only thing that can refuse
    it, which is what makes the refusal evidence about this rule.
    """
    note = corpus / "notes" / "2026-08-20--n--VID.md"
    note.write_text(f"---\n{_fm('VID', '[]')}---\n{BODY}", encoding="utf-8")
    stamp = rn.current_stamp() or "watch-quality@0.0.0"

    rn.REQUIRED_LANES[:] = []
    errs, wrote = rn.stamp_note(note, corpus, stamp, require_density=False)
    assert wrote and not errs, (errs, wrote)

    note.write_text(f"---\n{_fm('VID', '[]')}---\n{BODY}", encoding="utf-8")
    rn.REQUIRED_LANES[:] = ["facts"]
    defects, _ = rn.check_note(note, corpus, require_density=False)
    assert any("E-LANE-UNREVIEWED" in d for d in defects), defects
    errs, wrote = rn.stamp_note(note, corpus, stamp, require_density=False)
    assert not wrote and errs and "E-STAMP-REFUSED" in errs[0], (errs, wrote)
    assert "graded_with" not in note.read_text(encoding="utf-8")
