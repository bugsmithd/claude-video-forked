"""The review layer, held to what its prose claims.

Every case here is a counter-example one of three adversarial review lanes
produced against this package. Each was
run red against the build those reviews attacked before the fix landed, so a
case that goes green here is a door that is now shut rather than a door nobody
tried.

The lane ids are named by the finding they close so a future failure says which
attack came back.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from watchquality import (anchor_manifest as am, audit, demote_note as dn,
                          note_gates as ng, resolve_note as rn, wq_policy)

from conftest import RENDERING


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

BODY = "\n# t\n\nA body the lanes read.\n"



def _fm(video_id: str = "VID", reviews: str | None = "[facts, quality, coverage]") -> str:
    # The rendering the note declares. `check_oracle` opens it, and the corpus
    # fixture puts a run under `runs/<video_id>/` for the value to name.
    lines = [f"video_id: {video_id}", 'duration: "10:00"', "status: distilled",
             f"oracle: runs/{video_id}/run.json"]
    if reviews is not None:
        lines.append(f"reviews: {reviews}")
    return "\n".join(lines) + "\n"


def _header(note_sha: str, lane: str, verdict: str = "SHIP-WITH-FIXES",
            claims: str = "12", oracle: str = "runs/VID/run.json") -> str:
    return (f"---\nnote_sha256: {note_sha}\noracle: {oracle}\nlane: {lane}\n"
            f"verdict: {verdict}\nclaims_enumerated: {claims}\n---\n\n"
            f"# {lane} lane\n\nWhat the lane found.\n")


@pytest.fixture
def corpus(tmp_path: Path):
    """A root with one note and a review directory, and lanes required."""
    root = tmp_path.resolve()
    (root / "notes" / "reviews" / "VID").mkdir(parents=True)
    # The oracle a header names is opened now, so a corpus fixture has to hold
    # the run its reports cite. Both spellings, because the id is upper-cased in
    # some cases and lower in others and a case-sensitive filesystem tells them
    # apart where this machine does not.
    for vid in ("vid", "VID"):
        (root / "runs" / vid).mkdir(parents=True, exist_ok=True)
        (root / "runs" / vid / "run.json").write_text(RENDERING, encoding="utf-8")
    ambient = list(rn.REQUIRED_LANES)
    rn.REQUIRED_LANES[:] = ["facts", "quality", "coverage"]
    try:
        yield root
    finally:
        rn.REQUIRED_LANES[:] = ambient


def _reports(corpus: Path) -> Path:
    return corpus / "notes" / "reviews" / "VID"


def _sha(body: str = BODY) -> str:
    return rn.note_body_sha256(body)


# --------------------------------------------------------------------------
# mechanism F1 / premortem F1 — a report has to be a review
# --------------------------------------------------------------------------

def test_three_zero_byte_reports_are_not_a_review_layer(corpus):
    """mechanism F1: `touch facts.md quality.md coverage.md` bought a stamp."""
    for lane in ("facts", "quality", "coverage"):
        (_reports(corpus) / f"{lane}.md").write_text("", encoding="utf-8")
    got = rn.check_lanes(corpus, _fm(), "n.md", BODY)
    assert len(got) == 3, got
    assert all("E-LANE-UNPARSED" in g for g in got), got


def test_a_directory_named_like_a_report_does_not_satisfy_a_lane(corpus):
    """mechanism F2: `rglob('*.md')` yielded directories."""
    (_reports(corpus) / "quality.md").mkdir()
    got = rn.check_lanes(corpus, _fm("VID", "[quality]"), "n.md", BODY)
    assert any("E-LANE-MISSING" in g for g in got), got
    assert not any("E-LANE-UNPARSED" in g and "quality" in g for g in got), got


def test_a_symlink_to_dev_null_does_not_satisfy_a_lane(corpus):
    """mechanism F2b."""
    os.symlink("/dev/null", _reports(corpus) / "coverage.md")
    got = rn.check_lanes(corpus, _fm("VID", "[coverage]"), "n.md", BODY)
    assert any("E-LANE-MISSING" in g for g in got), got


def test_a_report_with_a_full_header_passes(corpus):
    for lane in ("facts", "quality", "coverage"):
        (_reports(corpus) / f"{lane}.md").write_text(
            _header(_sha(), lane), encoding="utf-8")
    assert rn.check_lanes(corpus, _fm(), "n.md", BODY) == []


def test_a_header_naming_another_lane_is_a_defect(corpus):
    (_reports(corpus) / "facts.md").write_text(
        _header(_sha(), "quality"), encoding="utf-8")
    got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
    assert any("E-LANE-MISLABELLED" in g for g in got), got


def test_a_header_naming_another_note_is_a_defect(corpus):
    """premortem's E-LANE-STALE: the report read a different body."""
    (_reports(corpus) / "facts.md").write_text(
        _header("0" * 64, "facts"), encoding="utf-8")
    got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
    assert any("E-LANE-STALE" in g for g in got), got


def test_an_unstated_claim_count_is_allowed_and_an_invented_one_is_not(corpus):
    path = _reports(corpus) / "facts.md"
    path.write_text(_header(_sha(), "facts", claims="unstated"), encoding="utf-8")
    assert rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY) == []
    path.write_text(_header(_sha(), "facts", claims="lots"), encoding="utf-8")
    got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
    assert any("E-LANE-UNPARSED" in g for g in got), got


def test_an_unknown_verdict_is_a_defect(corpus):
    (_reports(corpus) / "facts.md").write_text(
        _header(_sha(), "facts", verdict="LGTM"), encoding="utf-8")
    got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
    assert any("E-LANE-UNPARSED" in g for g in got), got


def test_a_header_missing_one_field_is_a_defect_and_names_the_field(corpus):
    """verification G2 — `LANE_HEADER_FIELDS` could drop `oracle` unnoticed.

    Every other field is pinned by a case that reads it: `note_sha256` by
    STALE, `lane` by MISLABELLED, `verdict` and `claims_enumerated` by their
    vocabularies. `oracle` is read by nothing downstream, so the only thing
    that can hold it in the header is a case on the field list itself.
    Deleting `"oracle"` from `LANE_HEADER_FIELDS` left all 277 cases and every
    module selftest green while the five-field header quietly became four.
    Asserted for EVERY field so the next one dropped is caught the same way.
    """
    rows = {"note_sha256": _sha(), "oracle": "runs/VID/run.json",
            "lane": "facts", "verdict": "SHIP", "claims_enumerated": "12"}
    assert set(rows) == set(rn.LANE_HEADER_FIELDS), rn.LANE_HEADER_FIELDS
    path = _reports(corpus) / "facts.md"
    for dropped in rows:
        kept = "".join(f"{k}: {v}\n" for k, v in rows.items() if k != dropped)
        path.write_text(f"---\n{kept}---\n\n# facts lane\n", encoding="utf-8")
        got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
        assert any("E-LANE-UNPARSED" in g and dropped in g for g in got), (
            dropped, got)


def test_yaml_spelling_out_no_value_is_not_a_value(corpus):
    """closeout: `oracle: null` passed the retyped rule.

    `~` was refused because it is punctuation and the rule needs one
    alphanumeric character, which reads as a fix for the class and is a fix for
    one of its four spellings. YAML says "this field has no value" in five
    ways, four of which are words, and a word satisfies a character class.
    Asserted for every spelling and every case, since a rule that reads `null`
    and not `NULL` is the same defect one letter along.
    """
    for spelling in ("null", "Null", "NULL", "~", "None", "none", "nUlL"):
        (_reports(corpus) / "facts.md").write_text(
            _header(_sha(), "facts", oracle=spelling), encoding="utf-8")
        got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
        assert any("E-LANE-UNPARSED" in g for g in got), (spelling, got)
    # ...and a real path that merely CONTAINS one of those words still passes,
    # which is what stops the rule becoming a substring blacklist. It is a real
    # file because the oracle is opened now, and the point here is the spelling.
    (corpus / "runs" / "VID" / "nullify").mkdir(parents=True)
    (corpus / "runs" / "VID" / "nullify" / "run.json").write_text(
        RENDERING, encoding="utf-8")
    (_reports(corpus) / "facts.md").write_text(
        _header(_sha(), "facts", oracle="runs/VID/nullify/run.json"),
        encoding="utf-8")
    assert rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY) == []


def test_a_header_field_present_but_empty_is_not_a_field(corpus):
    """verification-2 S3: `oracle: ""` passed, and both suites stayed green.

    A field present and empty is not a field. Stripping the surrounding quotes
    is the whole of what makes an empty quoted value empty: with
    `.strip("'\\"")` dropped, `.strip()` leaves the two quote characters,
    `fields.get("oracle")` is truthy, and a report DECLARES an oracle it does
    not have -- the false-clean-bill direction. The case above covers a line
    that is absent, which is a different mutation, and nothing covered this
    one: `296 passed`, EXIT=0, twelve module selftests green.

    The positive control at the end is the other half. The same one-token
    mutation also REJECTS `verdict: "SHIP"`, a legally quoted YAML value, so a
    fix reading "refuse anything with a quote in it" would satisfy the refusal
    half and break real reports. Both directions are asserted here, and both
    quote styles, for every field rather than for `oracle` alone.
    """
    rows = {"note_sha256": _sha(), "oracle": "runs/VID/run.json",
            "lane": "facts", "verdict": "SHIP", "claims_enumerated": "12"}
    assert set(rows) == set(rn.LANE_HEADER_FIELDS), rn.LANE_HEADER_FIELDS
    path = _reports(corpus) / "facts.md"
    for emptied in rows:
        for empty in ('""', "''"):
            kept = "".join(f"{k}: {empty if k == emptied else v}\n"
                           for k, v in rows.items())
            path.write_text(f"---\n{kept}---\n\n# facts lane\n", encoding="utf-8")
            got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
            assert any("E-LANE-UNPARSED" in g and emptied in g for g in got), (
                emptied, empty, got)
    kept = "".join(f'{k}: "{v}"\n' for k, v in rows.items())
    path.write_text(f"---\n{kept}---\n\n# facts lane\n", encoding="utf-8")
    assert rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY) == []


BLANK_INSIDES = ("", " ", "   ", "\t", " \t ")


def _blank_spellings() -> list[str]:
    """Every way to write a value that says nothing, generated not listed.

    Round 3 held `""` and `''` because those are the two strings the round-2
    report printed. `" "` was never printed, so it was never held, and it
    passed on the unmutated tree. A list of spellings is a list of the
    mutants somebody already thought of; this builds the class instead --
    blank inside, quoted, doubled, nested -- so the next spelling invented is
    already in the set.
    """
    out: list[str] = []
    for inner in BLANK_INSIDES:
        out.append(inner)
        for q in ('"', "'"):
            out.append(f"{q}{inner}{q}")
            out.append(f"{q}{q}{inner}{q}{q}")
        out.append(f'"{inner}\'{inner}\'{inner}"')
    return out


def test_header_value_calls_every_blank_spelling_nothing():
    """The property, asserted on the parser rule itself.

    `header_value` is the one rule the five fields share, so the class lives
    or dies here rather than in a fixture loop. Quoting a value must not
    change it (the metamorphic half round 3 wrote) and emptying it must.
    """
    for raw in _blank_spellings():
        assert rn.header_value(raw) == "", repr(raw)
    for raw, want in (('"SHIP"', "SHIP"), ("'SHIP'", "SHIP"), ("SHIP", "SHIP"),
                      ('  "runs/v/run.json"  ', "runs/v/run.json"),
                      ("runs/v/run.json\r", "runs/v/run.json"),
                      ('"a" and "b"', 'a" and "b')):
        assert rn.header_value(raw) == want, (raw, rn.header_value(raw))


def test_a_header_field_that_says_nothing_is_not_a_field(corpus):
    """verification-3 Claim 3: `oracle: " "` was ACCEPTED on the clean tree.

    Not a mutant -- a live defect inside the class the round-3 diff named and
    claimed to have closed. `.strip()` ran before `.strip("'\\"")`, so it
    could not see whitespace INSIDE the quotes, `fields.get("oracle")` was
    `' '`, `check_lanes` returned clean and `watch-audit` exited 0 while a
    report declared an oracle it does not have.

    Asserted over generated spellings and all five fields, because the defect
    was not that two strings were unheld, it was that spellings were held one
    at a time.
    """
    rows = {"note_sha256": _sha(), "oracle": "runs/VID/run.json",
            "lane": "facts", "verdict": "SHIP", "claims_enumerated": "12"}
    assert set(rows) == set(rn.LANE_HEADER_FIELDS), rn.LANE_HEADER_FIELDS
    path = _reports(corpus) / "facts.md"
    for emptied in rows:
        for blank in _blank_spellings():
            kept = "".join(f"{k}: {blank if k == emptied else v}\n"
                           for k, v in rows.items())
            path.write_text(f"---\n{kept}---\n\n# facts lane\n", encoding="utf-8")
            got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
            assert any("E-LANE-UNPARSED" in g and emptied in g for g in got), (
                emptied, repr(blank), got)


def test_a_header_whose_rows_carry_trailing_whitespace_still_parses(corpus):
    """The positive control for the fix, and the H2 mutant's grave.

    Dropping the leading `.strip()` closes the blank class too -- by rejecting
    every CRLF report and every row typed with a trailing space. A fix that
    only refuses is not the fix; both directions are asserted.
    """
    rows = {"note_sha256": _sha(), "oracle": "runs/VID/run.json",
            "lane": "facts", "verdict": "SHIP", "claims_enumerated": "12"}
    kept = "".join(f'{k}: "{v}"  \r\n' for k, v in rows.items())
    path = _reports(corpus) / "facts.md"
    path.write_text(f"---\n{kept}---\n\n# facts lane\n", encoding="utf-8")
    assert rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY) == []


# --------------------------------------------------------------------------
# the class, as a TYPE rather than as a list of refused spellings
# --------------------------------------------------------------------------

# The thirteen in-class variants the 2026-08-20 final review measured as
# ACCEPTED on the unmutated tree, numbered exactly as that report numbers them.
# Each entry is the raw text written to the right of a header key. Four of them
# put no glyph on the page at all, so the report renders as `oracle: ""` -- the
# shape four rounds of blank-value rules were convened to refuse and did not.
#
# THEY ARE NOT THE RULE. Four rounds of "reject the bad values somebody thought
# of" each produced four or more new survivors, because a blacklist has no fixed
# point: a reviewer can always invent one more spelling. These thirteen are the
# ACCEPTANCE SET for a rule written the other way round -- each field parses as
# its own type, and a value is valid when it IS that type. A fourteenth spelling
# nobody has written down is refused by the same rule, unread.
IN_CLASS_VARIANTS = (
    (1, "\"​\"", "zero-width space, quoted -- renders as empty"),
    (2, "​", "zero-width space, bare -- renders as empty"),
    (3, "\"﻿\"", "byte-order mark, what an editor leaves behind"),
    (4, "\"­\"", "soft hyphen: no glyph"),
    (5, "\"⠀\"", "braille blank: no glyph, and not whitespace"),
    (6, '"', "a value that is only a quote character"),
    (7, '"""', "odd quote count: the unquote loop lands on one quote"),
    (8, '"""""', "same, five deep"),
    (9, "'", "same, single quote"),
    (10, "\"'", "unbalanced pair: the unquote loop never enters"),
    (11, "\"\"''", "doubled-unbalanced: the loop never enters"),
    (12, "\"'\"", "nested, resolves to one quote"),
    (13, "'\"'", "nested, resolves to one quote"),
)


def test_every_in_class_header_variant_is_refused_in_every_field(corpus):
    """The acceptance set of the type rule, asserted by number and by field.

    Run red against the parser these thirteen were measured on: all thirteen
    were ACCEPTED in `oracle`, `check_lanes` returned clean and the corpus
    audit exited 0 while a report declared an oracle it does not have.

    Asserted for all five fields rather than for `oracle` alone, because the
    defect was never that thirteen strings were unheld -- it was that strings
    were held one at a time. The failure message names the variant number so a
    regression says which one came back.
    """
    rows = {"note_sha256": _sha(), "oracle": "runs/VID/run.json",
            "lane": "facts", "verdict": "SHIP", "claims_enumerated": "12"}
    assert set(rows) == set(rn.LANE_HEADER_FIELDS), rn.LANE_HEADER_FIELDS
    path = _reports(corpus) / "facts.md"
    accepted = []
    for field in rows:
        for num, raw, why in IN_CLASS_VARIANTS:
            kept = "".join(f"{k}: {raw if k == field else v}\n"
                           for k, v in rows.items())
            path.write_text(f"---\n{kept}---\n\n# facts lane\n",
                            encoding="utf-8")
            got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
            if not any("E-LANE-UNPARSED" in g and field in g for g in got):
                accepted.append((num, field, why, got))
    assert accepted == [], accepted


def test_every_header_field_has_exactly_one_type(corpus):
    """The guard on the guard: a field with no type is a field with no check.

    `LANE_HEADER_FIELDS` and the type table are two lists that have to agree.
    Deleting a row from the table would leave its field parsed by nothing and
    every case above still green, which is the mutation that took `oracle` out
    of the field list one round earlier.
    """
    assert set(rn.LANE_HEADER_TYPES) == set(rn.LANE_HEADER_FIELDS), (
        sorted(rn.LANE_HEADER_TYPES), sorted(rn.LANE_HEADER_FIELDS))


def test_the_lane_field_reuses_the_id_rule_a_note_declares_with(corpus):
    """One lane-id rule, both ends of the roll-call.

    A note declares `reviews: [facts]` against `RE_LANE_ID`; a report answers
    for `lane: facts`. A second, looser pattern on the report side would let a
    report claim a lane no note could ever declare, and the mismatch would read
    as a missing review rather than as a malformed report.
    """
    rows = {"note_sha256": _sha(), "oracle": "runs/VID/run.json",
            "lane": "Facts", "verdict": "SHIP", "claims_enumerated": "12"}
    kept = "".join(f"{k}: {v}\n" for k, v in rows.items())
    path = _reports(corpus) / "facts.md"
    path.write_text(f"---\n{kept}---\n\n# facts lane\n", encoding="utf-8")
    got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
    assert any("E-LANE-UNPARSED" in g and "lane" in g for g in got), got
    assert rn.RE_LANE_ID.match("facts") and not rn.RE_LANE_ID.match("Facts")


def test_a_count_is_ascii_digits_not_anything_python_calls_a_digit(corpus):
    """`str.isdigit()` is true of `١٢` and of `²`, and `int()` refuses both.

    A claim count is a number a reader compares against a list of claims.
    `claims_enumerated: ١٢` is not a smaller count, it is an unreadable one,
    and the field's type is what says so.

    `²` is the one the normaliser answers instead: NFKC folds a compatibility
    digit to its digit, so the type sees `2` and the field carries a number.
    That is the declared consequence of normalising before typing, asserted
    here so it is a decision rather than a surprise -- and it is the safe
    direction, a value a reader can see becoming the number it depicts, not an
    invisible one becoming a value.
    """
    assert rn.header_value("²") == "2"
    rows = {"note_sha256": _sha(), "oracle": "runs/VID/run.json",
            "lane": "facts", "verdict": "SHIP", "claims_enumerated": "12"}
    path = _reports(corpus) / "facts.md"
    for count in ("١٢", "1_2", "-1", "12.0", "1 2"):
        kept = "".join(f"{k}: {count if k == 'claims_enumerated' else v}\n"
                       for k, v in rows.items())
        path.write_text(f"---\n{kept}---\n\n# facts lane\n", encoding="utf-8")
        got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
        assert any("E-LANE-UNPARSED" in g and "claims_enumerated" in g
                   for g in got), (repr(count), got)
    for count in ("0", "12", "unstated"):
        kept = "".join(f"{k}: {count if k == 'claims_enumerated' else v}\n"
                       for k, v in rows.items())
        path.write_text(f"---\n{kept}---\n\n# facts lane\n", encoding="utf-8")
        assert rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md",
                             BODY) == [], count


def test_the_shapes_real_reports_use_still_parse(corpus):
    """The positive control, taken from the four headered reports on disk.

    An absolute oracle path, a lane id with no dash and one with a dash, an
    `unstated` verdict, an `unstated` count and a counted one. A type rule that
    refuses any of these is wrong about the type, not about the report.
    """
    # The absolute oracle is this fixture's own run rather than a literal from
    # a real report: the path is opened now, so a shape that names nothing on
    # this machine would be testing the consequence and not the type.
    rows = {"note_sha256": _sha(), "oracle": str(corpus / "runs/VID/run.json"),
            "lane": "facts", "verdict": "unstated",
            "claims_enumerated": "85"}
    path = _reports(corpus) / "facts.md"
    kept = "".join(f"{k}: {v}\n" for k, v in rows.items())
    path.write_text(f"---\n{kept}---\n\n# facts lane\n", encoding="utf-8")
    assert rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY) == []
    rows["lane"] = "coverage-spoken"
    rows["verdict"] = "SHIP-WITH-FIXES"
    rows["claims_enumerated"] = "unstated"
    # A quoted relative oracle. It stays a RUN path: an oracle inside the
    # corpus is refused now, because a report naming a note or another report
    # is naming the thing under judgement rather than the source of truth.
    rows["oracle"] = "runs/VID/run.json"
    kept = "".join(f'{k}: "{v}"\n' for k, v in rows.items())
    (path.parent / "coverage-spoken.md").write_text(
        f"---\n{kept}---\n\n# coverage lane\n", encoding="utf-8")
    path.unlink()
    assert rn.check_lanes(corpus, _fm("VID", "[coverage-spoken]"), "n.md",
                          BODY) == []


def test_an_applied_field_that_says_nothing_is_not_an_edge(corpus):
    """The same class, one parser over: `status: applied` + `applied: " "`.

    `check_status`'s own docstring calls an `applied:` field that names no
    file "the more expensive lie". It carried the identical two-strip form, so
    a whitespace-only path satisfied `if status == "applied" and not applied`
    and E-STATUS-NOEDGE never fired. One rule, every site that reads a header
    value -- otherwise the next round finds this class again in this file.
    """
    for blank in _blank_spellings():
        fm = f"video_id: VID\nstatus: applied\napplied: {blank}\n"
        got = rn.check_status(fm, "n.md", corpus)
        assert any("E-STATUS-NOEDGE" in g for g in got), (repr(blank), got)


def test_a_grandfathered_video_id_keeps_its_headerless_reports(corpus):
    """The 17 directories that pre-date the header, exempted by dated row.

    The note is named with a date because this row no longer forgives an
    undated one: it skips four checks at once, and an absence may not buy that
    (round-4 refutation F-7). Every note in the corpus is filed with a date, so
    the fixture was the only thing relying on the old width.
    """
    for lane in ("facts", "quality", "coverage"):
        (_reports(corpus) / f"{lane}.md").write_text("old report\n", encoding="utf-8")
    rn.UNHEADERED_REVIEWS["VID"] = "2026-08-20 filed before the header existed"
    try:
        assert rn.check_lanes(corpus, _fm(), "2026-08-19--n--VID.md", BODY) == []
    finally:
        del rn.UNHEADERED_REVIEWS["VID"]


def test_the_exemption_is_read_per_video_id_not_as_a_table_that_exists(corpus):
    """verification G2 — the case above cannot see which id was excused.

    Mutating the lookup to `bool(UNHEADERED_REVIEWS)` left all 274 cases green
    and turned the header requirement off for the WHOLE corpus, one row being
    enough to excuse every note in it. So the exemption has to be asserted from
    the other side: a table with somebody else's row in it excuses nobody.
    """
    for lane in ("facts", "quality", "coverage"):
        (_reports(corpus) / f"{lane}.md").write_text("old report\n",
                                                     encoding="utf-8")
    rn.UNHEADERED_REVIEWS["SOMEONE-ELSE"] = "2026-08-20 a different note"
    try:
        got = rn.check_lanes(corpus, _fm(), "n.md", BODY)
        assert len(got) == 3, got
        assert all("E-LANE-UNPARSED" in g for g in got), got
    finally:
        del rn.UNHEADERED_REVIEWS["SOMEONE-ELSE"]


# --------------------------------------------------------------------------
# mechanism F12 — deleting video_id disarmed the roll-call
# --------------------------------------------------------------------------

def test_declaring_lanes_without_a_video_id_is_a_defect(corpus):
    got = rn.check_lanes(corpus, _fm("VID", "[facts, quality, coverage]"
                                     ).replace("video_id: VID\n", ""),
                         "n.md", BODY)
    assert len(got) == 1 and "E-LANE-NOVIDEO" in got[0], got


def test_a_note_with_neither_a_video_id_nor_lanes_is_still_quiet(corpus):
    assert rn.check_lanes(corpus, "title: t\n", "n.md", BODY) == []


# --------------------------------------------------------------------------
# properties I6 — one declaration cleared a two-lane floor
# --------------------------------------------------------------------------

def test_one_declaration_cannot_satisfy_two_required_lanes(corpus):
    rn.REQUIRED_LANES[:] = ["quality", "quality-deep"]
    got = rn.check_required_lanes(
        "video_id: VID\nreviews: [quality-deep]\n", "2026-08-20--n--VID.md")
    assert len(got) == 1 and "quality " in got[0], got


def test_a_family_declaration_still_covers_its_own_lane(corpus):
    assert rn.check_required_lanes(
        "video_id: VID\nreviews: [facts, quality, coverage-spoken]\n",
        "2026-08-20--n--VID.md") == []


# --------------------------------------------------------------------------
# properties I17 / I18 — two reports, one filename
# --------------------------------------------------------------------------

def test_two_reports_with_one_filename_are_ambiguous_and_not_shared(corpus):
    for sub in ("a", "b"):
        d = _reports(corpus) / sub
        d.mkdir()
        (d / "facts.md").write_text(_header(_sha(), "facts"), encoding="utf-8")
    got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
    assert len(got) == 1, got
    assert "E-LANE-AMBIGUOUS" in got[0], got


def test_two_reports_with_one_filename_are_named_apart_in_the_message(corpus):
    """verification G2 — `report_name` could revert to `path.name` unnoticed.

    The case above asserts the DEFECT; nothing asserted the message, so
    `return path.name` left all 277 cases and every module selftest green
    while the line read `facts.md, facts.md` and named neither file. The
    naming is the whole content of properties I17: a defect a reader cannot
    act on is a defect that gets closed by deleting a file at random.
    """
    for sub in ("a", "b"):
        d = _reports(corpus) / sub
        d.mkdir()
        (d / "facts.md").write_text(_header(_sha(), "facts"), encoding="utf-8")
    a, b = str(Path("a") / "facts.md"), str(Path("b") / "facts.md")

    got = rn.check_lanes(corpus, _fm("VID", "[facts]"), "n.md", BODY)
    assert len(got) == 1 and "E-LANE-AMBIGUOUS" in got[0], got
    assert a in got[0] and b in got[0], got[0]

    # Same naming where the report is claimed by nothing at all.
    undeclared = [g for g in rn.check_lanes(corpus, _fm("VID", "[quality]"),
                                            "n.md", BODY)
                  if "E-LANE-UNDECLARED" in g]
    assert len(undeclared) == 2, undeclared
    assert any(a in g for g in undeclared), undeclared
    assert any(b in g for g in undeclared), undeclared


# --------------------------------------------------------------------------
# mechanism F8 — a new note was born excused
# --------------------------------------------------------------------------

def test_an_exemption_cannot_excuse_a_note_written_after_it(corpus):
    rn.UNREVIEWED_NOTES["VID"] = {"facts": "2026-08-20 frozen, not re-reviewed",
                                  "quality": "2026-08-20 frozen",
                                  "coverage": "2026-08-20 frozen"}
    try:
        assert rn.check_required_lanes("video_id: VID\nreviews: []\n",
                                       "2026-08-05--old--VID.md") == []
        got = rn.check_required_lanes("video_id: VID\nreviews: []\n",
                                      "2026-08-21--new--VID.md")
        assert len(got) == 3, got
    finally:
        del rn.UNREVIEWED_NOTES["VID"]


# --------------------------------------------------------------------------
# mechanism F16 — --stamp graded with a weaker checker than --check
# --------------------------------------------------------------------------

def test_stamp_refuses_a_note_that_check_calls_defective(corpus):
    note = corpus / "notes" / "2026-08-20--n--VID.md"
    note.write_text(f"---\n{_fm('VID', None)}---\n{BODY}", encoding="utf-8")
    stamp = rn.current_stamp() or "watch-quality@0.0.0"
    errs, wrote = rn.stamp_note(note, corpus, stamp)
    assert not wrote and errs and "E-STAMP-REFUSED" in errs[0], (errs, wrote)


def test_the_stamp_entry_point_grades_as_hard_as_check_does(corpus):
    """verification G2 — the case above passes for the wrong reason.

    Its fixture is red from `E-LANE-UNREVIEWED`, so it survives even when
    `main` hands `stamp_note` the weaker flags: the note is refused either
    way. Mutating the call to `stamp_note(f, root, stamp, False, False)` left
    all 274 cases green while the mutant stamped a density-less note clean.
    This fixture is clean in EVERY respect except the one those flags govern,
    so the only thing that can refuse it is `main` passing them through.
    """
    for lane in ("facts", "quality", "coverage"):
        (_reports(corpus) / f"{lane}.md").write_text(
            _header(_sha(), lane), encoding="utf-8")
    note = corpus / "notes" / "2026-08-20--n--VID.md"
    note.write_text(f"---\n{_fm()}---\n{BODY}", encoding="utf-8")
    stamp = rn.current_stamp() or "watch-quality@0.0.0"

    # The entry point refuses it, and writes nothing.
    assert rn.main(["--stamp", str(note)], root=corpus) != 0
    assert "graded_with" not in note.read_text(encoding="utf-8")

    # And the flags are the ONLY reason: asked for the weaker grading by name,
    # the same note on the same build stamps clean. If that call ever stops
    # writing, this fixture has become red for some other reason and the case
    # above has gone inert the way its predecessor did.
    errs, wrote = rn.stamp_note(note, corpus, stamp, False, False)
    assert wrote and not errs, (errs, wrote)


# --------------------------------------------------------------------------
# mechanism F11 — the date rule was shape, not meaning
# --------------------------------------------------------------------------

def test_an_impossible_date_is_not_a_valid_exemption():
    with pytest.raises(wq_policy.PolicyError):
        wq_policy.Policy({"unresolvable_runs": {"abc": "9999-99-99 x"}}, None)


def test_a_real_date_is_still_a_valid_exemption():
    wq_policy.Policy({"unresolvable_runs": {"abc": "2026-08-20 a reason"}}, None)


# --------------------------------------------------------------------------
# decision 2 — no policy file is COULD NOT RUN, not a pass
# --------------------------------------------------------------------------

def test_watch_audit_exits_two_when_no_policy_resolves(monkeypatch, capsys):
    monkeypatch.delenv(wq_policy.ENV_NO_POLICY, raising=False)
    monkeypatch.setattr(audit, "GATES", [("a", ["--check"])])
    monkeypatch.setattr(audit, "_load", lambda n: (lambda argv: 0))
    monkeypatch.setattr(audit, "resolved_policy", lambda: (None, None))
    assert audit.main(["--quiet"]) == 2
    out = capsys.readouterr().out
    assert "no watch-quality.toml" in out
    assert wq_policy.ENV_NO_POLICY in out


def test_no_policy_can_be_asked_for_by_name(monkeypatch, capsys):
    monkeypatch.setenv(wq_policy.ENV_NO_POLICY, "1")
    monkeypatch.setattr(audit, "GATES", [("a", ["--check"])])
    monkeypatch.setattr(audit, "_load", lambda n: (lambda argv: 0))
    monkeypatch.setattr(audit, "resolved_policy", lambda: (None, None))
    assert audit.main(["--quiet"]) == 0


def test_the_audit_names_the_policy_it_enforced(monkeypatch, capsys):
    monkeypatch.setattr(audit, "GATES", [("a", ["--check"])])
    monkeypatch.setattr(audit, "_load", lambda n: (lambda argv: 0))
    monkeypatch.setattr(audit, "resolved_policy",
                        lambda: (Path("/corpus/watch-quality.toml"),
                                 ("facts", "quality")))
    assert audit.main(["--quiet"]) == 0
    out = capsys.readouterr().out
    assert "/corpus/watch-quality.toml" in out
    assert "facts, quality" in out


# --------------------------------------------------------------------------
# premortem F3 — the documented single-note invocation cried wolf
# --------------------------------------------------------------------------

def test_auditing_one_note_does_not_orphan_the_others(corpus):
    notes = corpus / "notes"
    for vid in ("VID", "OTHER"):
        (notes / f"2026-08-20--n-{vid}--{vid}.md").write_text(
            f"---\n{_fm(vid, None)}---\n{BODY}", encoding="utf-8")
        side = notes / ".resolved" / f"{vid}.tsv"
        side.parent.mkdir(parents=True, exist_ok=True)
        side.write_text("docs/x.md\t1\tdead\tq\n", encoding="utf-8")
    one = [notes / "2026-08-20--n-VID--VID.md"]
    assert rn.orphan_sidecars(corpus, one) == []


# --------------------------------------------------------------------------
# properties I22 / I23 + mechanism F14 / F15 — corpus membership
# --------------------------------------------------------------------------

def test_a_report_is_not_a_note_even_when_the_gate_is_pointed_at_reviews(corpus):
    report = _reports(corpus) / "2026-08-20--facts--VID.md"
    report.write_text("x", encoding="utf-8")
    assert not rn.is_note(report, corpus)
    assert rn.collect([_reports(corpus)], corpus, []) == []


def test_a_dropped_file_is_counted_rather_than_silently_skipped(corpus):
    notes = corpus / "notes"
    (notes / "2026-08-20--kept--VID.md").write_text("x", encoding="utf-8")
    (notes / "draft-second-note.md").write_text("x", encoding="utf-8")
    skipped: list[str] = []
    got = rn.collect([notes], corpus, skipped)
    assert len(got) == 1, got
    assert any("draft-second-note.md" in s for s in skipped), skipped


# ==========================================================================
# The findings the red spec above did not reach. Same three reports, same
# 2026-08-20 lanes; these were written after it, each run red first.
# ==========================================================================

# --------------------------------------------------------------------------
# mechanism F14 — a note parked in the review tree
# --------------------------------------------------------------------------

def test_a_note_parked_in_the_review_tree_is_named_rather_than_hidden(corpus):
    """A note-named file under reviews/ is the one surprise worth printing."""
    (corpus / "notes" / "2026-08-20--kept--VID.md").write_text("x",
                                                               encoding="utf-8")
    (_reports(corpus) / "2026-08-20--hidden--VID.md").write_text(
        "x", encoding="utf-8")
    (_reports(corpus) / "facts.md").write_text("x", encoding="utf-8")
    skipped: list[str] = []
    got = rn.collect([corpus / "notes"], corpus, skipped)
    assert len(got) == 1, got
    assert any("hidden" in s for s in skipped), skipped
    assert not any("facts.md" in s for s in skipped), skipped


# --------------------------------------------------------------------------
# properties I1 / I2 — a declaration read as a different declaration
# --------------------------------------------------------------------------

def test_a_block_style_review_list_is_not_read_as_no_lanes():
    """I1: legal YAML declaring two lanes parsed as zero, silently."""
    ids, err = rn.lane_ids("video_id: VID\nreviews:\n  - facts\n  - quality\n")
    assert ids is None and err, (ids, err)


def test_a_quoted_comma_does_not_manufacture_two_lanes():
    """I1: `["a, b"]` split inside the quotes and invented a second lane."""
    ids, err = rn.lane_ids('reviews: ["facts, quality"]\n')
    assert ids is None and err, (ids, err)


def test_a_trailing_comment_is_not_a_malformed_list():
    """I2: a legal flow sequence with a YAML comment was called malformed."""
    assert rn.lane_ids("reviews: [facts]  # ran 2026-08-20\n") == (["facts"],
                                                                   None)


# --------------------------------------------------------------------------
# properties I8 / I14 — a policy that cannot be satisfied, or that lies twice
# --------------------------------------------------------------------------

def test_a_policy_cannot_require_a_lane_no_note_can_declare():
    """I8: `required_lanes = ["Facts"]` is unsatisfiable in both directions."""
    with pytest.raises(wq_policy.PolicyError):
        wq_policy.Policy({"required_lanes": ["Facts"]}, None)
    wq_policy.Policy({"required_lanes": ["facts", "coverage-spoken"]}, None)


def test_a_lane_cannot_be_both_lost_and_never_dispatched():
    """I14: both rows present, and each admission covers for the other."""
    with pytest.raises(wq_policy.PolicyError):
        wq_policy.Policy(
            {"lost_reviews": {"V": {"facts": "2026-08-20 lost"}},
             "unreviewed_notes": {"V": {"facts": "2026-08-20 never ran"}}},
            None)


# --------------------------------------------------------------------------
# properties I12 / I13 / I16 + mechanism F10 — rows that excuse nothing
# --------------------------------------------------------------------------

def test_an_exemption_that_excuses_nothing_is_reported(corpus):
    note = corpus / "notes" / "2026-08-20--n--VID.md"
    note.write_text(f"---\n{_fm('VID')}---\n{BODY}", encoding="utf-8")
    rn.UNREVIEWED_NOTES["GHOSTVID"] = {"facts": "2026-08-20 no such note"}
    rn.UNREVIEWED_NOTES["VID"] = {"nosuchlane": "2026-08-20 no such lane",
                                  "facts": "2026-08-20 the note declares it"}
    try:
        got = rn.stale_exemptions(corpus)
    finally:
        del rn.UNREVIEWED_NOTES["GHOSTVID"]
        del rn.UNREVIEWED_NOTES["VID"]
    assert any("GHOSTVID" in g for g in got), got
    assert any("nosuchlane" in g for g in got), got
    assert any("VID/facts" in g for g in got), got


# --------------------------------------------------------------------------
# properties I22 / I24 + premortem F3 — the same two bugs, three gates over
# --------------------------------------------------------------------------

def _corpus_with_two_notes(corpus: Path) -> None:
    for vid in ("VID", "OTHER"):
        (corpus / "notes" / f"2026-08-20--n-{vid}--{vid}.md").write_text(
            f"---\n{_fm(vid, None)}---\n{BODY}", encoding="utf-8")


def test_auditing_one_note_does_not_orphan_the_other_manifests(corpus):
    """premortem F3, the half that lives in anchor_manifest."""
    _corpus_with_two_notes(corpus)
    anchors = corpus / "notes" / "anchors"
    anchors.mkdir(parents=True)
    for vid in ("VID", "OTHER"):
        (anchors / f"{vid}.tsv").write_text(f"# video_id\t{vid}\n",
                                            encoding="utf-8")
    assert am.sweep_manifests(corpus, {"VID"}) == []


def test_an_empty_corpus_still_never_reads_as_a_clean_pass(corpus):
    """mechanism R9 / claim C11 — the attack the gate already survived.

    Moving the empty-result decision out of `collect` so a directory of
    reviews could return nothing broke it: `main` raised `NameError` instead
    of exiting 2. Caught by re-running the review's own R9 fixture against the
    new build, which is the point of re-running the refuted half.
    """
    (corpus / "notes" / "draft.md").write_text("x", encoding="utf-8")
    assert rn.main(["--check", str(corpus / "notes")], root=corpus) == 2


def test_every_gate_refuses_an_empty_corpus_not_just_the_first(corpus):
    """verification G1 — the regression that escaped the fix pass.

    The empty-result decision moved out of `collect` and into `main`, which
    gave the guarantee back to `resolve_note` and to `anchor_manifest` and
    silently dropped it for `demote_note`, whose two call sites reach `collect`
    directly. On an empty corpus it printed `# 0 notes would change` and exited
    0, and `watch-audit` printed `demote_note: ok` over it. One gate reading an
    empty corpus as clean is the whole failure mode the guarantee exists for,
    so this asserts it of EVERY entry point rather than of the one that
    happened to be checked.
    """
    (corpus / "notes" / "draft.md").write_text("x", encoding="utf-8")
    notes = str(corpus / "notes")
    assert rn.main(["--check", notes], root=corpus) == 2
    for argv in (["--diff", notes], [notes]):
        assert dn.main(argv, root=corpus) == 2, argv
    # `anchor_manifest` refuses by raising rather than returning, which the
    # audit runner turns into the same exit 2. Asserting a return value here
    # would fail against correct behaviour.
    with pytest.raises(SystemExit) as exit_info:
        am.main(["--check", notes], root=corpus)
    assert exit_info.value.code == 2


# --------------------------------------------------------------------------
# verification G3 — the caller wiring, which only the selftest held
# --------------------------------------------------------------------------

def test_check_note_wires_both_lane_checks_into_its_own_result(corpus):
    """Deleting either `cite_defects +=` line left all 277 cases green.

    Every case above calls `check_lanes` or `check_required_lanes` DIRECTLY,
    so all of them survive the one edit that matters: `check_note` no longer
    asking. That is this project's own named failure class -- a suite stays
    green while the line wiring a check into the caller is deleted -- and it
    was live in `pytest -q` while `resolve_note --selftest` caught it. The
    selftests now run in the same command (`tests/test_selftests.py`); this
    case additionally names the wiring, so the failure says which line went.
    """
    note = corpus / "notes" / "2026-08-20--n--VID.md"
    note.write_text(f"---\n{_fm('VID', '[facts]')}---\n{BODY}", encoding="utf-8")
    got, _ = rn.check_note(note, corpus, require_density=False)

    # check_required_lanes: two required lanes were never declared.
    unreviewed = [g for g in got if "E-LANE-UNREVIEWED" in g]
    assert len(unreviewed) == 2, got
    # check_lanes: the one lane that WAS declared produced no report.
    missing = [g for g in got if "E-LANE-MISSING" in g and "facts" in g]
    assert len(missing) == 1, got


def test_the_manifest_gate_pointed_at_reviews_grades_no_reports(corpus):
    """properties I22 through the collector three gates share."""
    (_reports(corpus) / "2026-08-20--facts--VID.md").write_text(
        "x", encoding="utf-8")
    with pytest.raises(SystemExit):
        am.collect_notes([_reports(corpus)], corpus)


# --------------------------------------------------------------------------
# V1-4 — the date rule lived in the loader and nowhere else
# --------------------------------------------------------------------------

# The four rows V1 measured. Row one is the only legal shape; the other three
# are the three ways a reason can carry no date the ageing comparison reads.
V1_PROBE = ("2026-08-21 note frozen",
            "frozen, recorded 2026-08-21",
            "permanent",
            "9999-99-99 impossible")


@pytest.mark.parametrize("reason", V1_PROBE[1:])
def test_a_reason_that_is_not_dated_first_excuses_nothing(reason):
    """Two lanes read this code and disagreed; the probe decided.

    `excused` compares `when <= reason[:10]` as STRINGS, so every ISO date
    sorts below every letter and a reason beginning with a word excused every
    note it named, forever. `wq_policy._validate` refuses those rows at load,
    which is why the corpus was never actually holed -- but the function makes
    no such assumption in its own body, and `UNHEADERED_REVIEWS` and its
    neighbours are plain dicts any caller can write a row into without passing
    a `Policy` at all. The guard belongs where the comparison is.

    Would fail if: the ISO-date test is removed from `excused`.
    """
    assert rn.excused(reason, "2026-08-22--new--VID.md") is False


def test_a_reason_dated_first_still_ages_the_note_in_front_of_it():
    """The other half of the same guard: row one of V1's probe still works."""
    assert rn.excused(V1_PROBE[0], "2026-08-20--old--VID.md") is True
    assert rn.excused(V1_PROBE[0], "2026-08-22--new--VID.md") is False


@pytest.mark.parametrize("reason", V1_PROBE[1:])
def test_the_loader_refuses_the_same_three_rows(reason):
    """Both layers, so neither one is the only thing holding the door."""
    with pytest.raises(wq_policy.PolicyError):
        wq_policy.Policy({"unheadered_reviews": {"VID": reason}}, None)


# --------------------------------------------------------------------------
# verification-3 T3 — the widest bypass, and whether it is a boundary
# --------------------------------------------------------------------------

OLD_ROW = "2020-01-01 filed before the header existed"


def test_the_unheadered_row_is_a_boundary_and_not_a_membership_card(corpus):
    """The whole header bypass, aged like every other debt row.

    A row here skips the header entirely -- oracle, verdict, lane label and
    body hash together -- so it is the widest excuse in the policy. Read as
    bare membership it covered work nobody had done yet: a report filed today,
    under a video id grandfathered in years ago, bought all four.

    This lived only in the module selftest. The pytest layer is where a
    refactor gets noticed, so it is asserted here too.

    Would fail if: `check_lanes` goes back to `video_id in UNHEADERED_REVIEWS`.
    """
    (_reports(corpus) / "facts.md").write_text("not a review\n", encoding="utf-8")
    fm = _fm("VID", "[facts]")
    rn.UNHEADERED_REVIEWS["VID"] = OLD_ROW
    try:
        # A note that existed when the row was written: the header is skipped.
        assert rn.check_lanes(corpus, fm, "2020-01-01--old--VID.md", BODY) == []

        # A note filed after it: the report is read, and it is not a review.
        got = rn.check_lanes(corpus, fm, "2026-06-01--later--VID.md", BODY)
        assert len(got) == 1 and "E-LANE-UNPARSED" in got[0], got

        # What the case discriminates, said out loud: membership is true for
        # BOTH notes, so a bare `in` test cannot tell them apart and the date
        # comparison is the only thing that does.
        assert "VID" in rn.UNHEADERED_REVIEWS
        assert rn.excused(OLD_ROW, "2026-06-01--later--VID.md") is False
    finally:
        del rn.UNHEADERED_REVIEWS["VID"]


@pytest.mark.parametrize("table", ["UNRESOLVABLE_RUNS", "UNHEADERED_REVIEWS",
                                   "UNFILLED_ORACLES", "UNGRADED_NOTES"])
def test_every_dated_table_ages_the_same_way(table):
    """One rule, four tables. Two of them read it differently for a while.

    Each of these promises the same thing at the head of the policy file --
    dated, reasoned, printed, may only shrink -- and the ageing is what makes
    "dated" mean anything. A table that holds rows of the same shape and reads
    them by a different rule is the defect this parametrisation exists to stop
    coming back one table at a time.
    """
    assert isinstance(getattr(rn, table), dict)
    assert rn.excused(OLD_ROW, "2019-12-31--before--VID.md") is True
    assert rn.excused(OLD_ROW, "2020-01-01--same-day--VID.md") is True
    assert rn.excused(OLD_ROW, "2020-01-02--after--VID.md") is False


# --------------------------------------------------------------------------
# round-4 F-7 — an undated note bought the whole header bypass
# --------------------------------------------------------------------------

def test_an_undated_note_still_buys_one_rows_worth_of_forgiveness():
    """The default is unchanged: absence is not a conviction.

    A note whose filename carries no date cannot be placed either side of the
    row's line. For `lost_reviews` and `unresolvable_runs` that forgives ONE
    thing -- a lane's missing report, a run that has gone -- so leaving it
    excused costs a row and no more.
    """
    assert rn.excused(OLD_ROW, "no-date-here.md") is True


def test_an_undated_note_does_not_buy_the_whole_header_bypass(corpus):
    """...and where the row forgives four things at once, it does not.

    The unheadered row skips oracle, verdict, lane label and body hash
    together. An undated note filed under an old row bought all four on an
    absence, which is the widest excuse in the policy resting on the weakest
    evidence in it -- the one place where "left excused rather than convicted"
    costs more than the finding it was protecting.

    Would fail if: `check_lanes` stops passing `undated=False`, or `excused`
    stops reading it.
    """
    assert rn.excused(OLD_ROW, "no-date-here.md", undated=False) is False

    (_reports(corpus) / "facts.md").write_text("not a review\n", encoding="utf-8")
    fm = _fm("VID", "[facts]")
    rn.UNHEADERED_REVIEWS["VID"] = OLD_ROW
    try:
        got = rn.check_lanes(corpus, fm, "no-date-here.md", BODY)
        assert len(got) == 1 and "E-LANE-UNPARSED" in got[0], got
    finally:
        del rn.UNHEADERED_REVIEWS["VID"]


# --------------------------------------------------------------------------
# verification-3 T1/T2/T4 — how much of the review layer is graded at all
# --------------------------------------------------------------------------

def test_the_run_says_how_many_reports_it_graded_and_how_many_it_excused(corpus):
    """A gate that grades 4 of 59 files and prints "passed" is not reporting.

    Measured on the corpus: 17 of 18 review directories carry an
    `unheadered_reviews` row, so 55 of 59 report files are exempt from the
    header check and 4 are graded -- and the audit printed "all gates passed"
    with nothing saying which of those two numbers it was talking about. Every
    one of the 55 would be a defect without its row, so the table is not a
    formality covering a legacy tail; it is load-bearing for all of what it
    covers (verification-3 T1, T2).

    Would fail if: the reach line goes away, or stops counting the reports the
    exemption reaches.
    """
    for lane in ("facts", "quality", "coverage"):
        (_reports(corpus) / f"{lane}.md").write_text(
            _header(_sha(), lane), encoding="utf-8")
    note = corpus / "notes" / "2026-08-20--n--VID.md"
    note.write_text(f"---\n{_fm()}---\n{BODY}", encoding="utf-8")

    import io
    from contextlib import redirect_stderr, redirect_stdout
    err = io.StringIO()
    rn.UNHEADERED_REVIEWS["VID"] = OLD_ROW
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            rn.main(["--check", str(note)], root=corpus)
    finally:
        del rn.UNHEADERED_REVIEWS["VID"]
    printed = err.getvalue()

    assert "review report(s)" in printed, printed
    assert "3 excused by 1 row" in printed, printed


def test_a_ledger_may_not_grow_past_the_cap_its_own_policy_declares():
    """"May only shrink" was a sentence in five files and a number in none.

    Every debt table in the policy says it, and nothing read any of their
    sizes. `unheadered_reviews` is the one that matters most -- it decides
    whether the header check applies at all, and in the corpus this suite ships
    beside it reaches 55 of 59 report files, every one of which would be a
    defect without its row (verification-3 T4).

    So a corpus declares the size of each ledger it keeps, and the loader
    refuses a table that has outgrown its own declaration. The cap lives with
    the table rather than in this file, because the rows are a fact about one
    corpus and the rule is a fact about the package.

    Would fail if: `_validate` stops reading `[ledger_caps]`.
    """
    rows = {f"VID{i}": "2020-01-01 filed before the header existed"
            for i in range(3)}

    wq_policy.Policy({"unheadered_reviews": rows,
                      "ledger_caps": {"unheadered_reviews": 3}}, None)

    with pytest.raises(wq_policy.PolicyError) as caught:
        wq_policy.Policy({"unheadered_reviews": rows,
                          "ledger_caps": {"unheadered_reviews": 2}}, None)
    assert "may only shrink" in str(caught.value), caught.value

    # A cap naming a table nobody keeps is a cap that enforces nothing, and it
    # is the way this mechanism goes quiet: a rename, and the number is inert.
    with pytest.raises(wq_policy.PolicyError):
        wq_policy.Policy({"ledger_caps": {"no_such_table": 0}}, None)


# --------------------------------------------------------------------------
# round-13 F1 — the third layer, which read no date at all
# --------------------------------------------------------------------------

def test_the_manifest_gate_ages_the_two_tables_it_shares(corpus):
    """A bare membership test on the SAME rows the oracle check ages.

    `resolve_note` says out loud that it reads these rows rather than opening a
    fourth ledger because "`anchor_manifest` already owns this table and
    already ages it". It did not. `exempt = video_id in UNRESOLVABLE_RUNS` and
    `if video_id in UNATTRIBUTED_NOTES` read no reason, compared no date, and
    excused every note about that video for ever -- including notes filed years
    after the row. The comment that justified not opening a fourth ledger was
    the false one (round-13 F1).

    Would fail if: either lookup goes back to `in`.
    """
    late = "2026-08-22--new--VID.md"
    early = "2019-12-31--old--VID.md"
    for table in (am.UNRESOLVABLE_RUNS, am.UNATTRIBUTED_NOTES):
        table["VID"] = OLD_ROW
    try:
        assert am.exempt_run("VID", early) is True
        assert am.exempt_run("VID", late) is False
        assert am.exempt_class("VID", early) is True
        assert am.exempt_class("VID", late) is False
        # A reason that is not a date excuses nothing here either, which is
        # the guard the other two layers already carry.
        for table in (am.UNRESOLVABLE_RUNS, am.UNATTRIBUTED_NOTES):
            table["VID"] = "permanent"
        assert am.exempt_run("VID", early) is False
        assert am.exempt_class("VID", early) is False
    finally:
        for table in (am.UNRESOLVABLE_RUNS, am.UNATTRIBUTED_NOTES):
            del table["VID"]


# --------------------------------------------------------------------------
# round-13 F3 — the two tables that forgive more and still took an absence
# --------------------------------------------------------------------------

def test_the_oracle_ledger_does_not_take_an_absence_as_payment(corpus):
    """`unheadered_reviews` was narrowed and it is the THIRD-widest table.

    A row in `unfilled_oracles` forgives the field every downstream gate reads
    to decide WHICH rendering a note is graded against. So a row written to
    forgive an empty field also forgave a note declaring two different ones,
    and it took a missing filename date as payment (round-13 F3).

    Would fail if: `check_oracle` stops passing `undated=False`.
    """
    fm = "video_id: VID\noracle: a.json\noracle: b.json\n"
    dated, undated = "2019-12-31--old--VID.md", "no-date-here.md"
    for rel in (dated, undated):
        rn.UNFILLED_ORACLES[rel] = OLD_ROW
    try:
        assert rn.check_oracle(fm, dated, corpus) == []
        got = rn.check_oracle(fm, undated, corpus)
        assert got and "E-ORACLE" in got[0], got
    finally:
        for rel in (dated, undated):
            del rn.UNFILLED_ORACLES[rel]


def test_the_ungraded_ledger_does_not_take_an_absence_as_payment(corpus, tmp_path):
    """The WIDEST table of the five, and it forgave on an absence too.

    A row here returns before the whole per-note grading layer -- every
    coverage code and every window code at once. That is strictly wider than
    the header bypass this slice narrowed first (round-13 F3).

    Would fail if: `note_gates.check_note` stops passing `undated=False`.
    """
    notes = corpus / "notes"
    dated = notes / "2019-12-31--old--VID.md"
    undated = notes / "no-date-here.md"
    for note in (dated, undated):
        note.write_text(f"---\n{_fm()}---\n{BODY}", encoding="utf-8")
        ng.UNGRADED_NOTES[str(note.relative_to(corpus))] = OLD_ROW
    try:
        _, why = ng.check_note(dated, corpus)
        assert why and "excused by the ledger" in why, why
        _, why = ng.check_note(undated, corpus)
        assert not (why and "excused by the ledger" in why), why
    finally:
        for note in (dated, undated):
            del ng.UNGRADED_NOTES[str(note.relative_to(corpus))]
