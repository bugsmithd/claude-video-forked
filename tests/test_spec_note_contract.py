"""The cases layer 1 of the spec names, one per neighbour.

`spec/note-contract.toml` is a table of what each note-contract rule REFUSES,
and every row names the neighbouring wrong answers it must refuse as well. A
neighbour with no case is a hole, so this file is where the holes were closed:
each rule below is exercised through the function that owns it, with the
must-fire and the must-NOT-fire directions written down side by side.

Two habits the reviews asked for, kept here on purpose:

  - drive the real function. A case that rebuilds the rule and compares against
    itself is two copies of one belief.
  - assert the defect CODE and something in the message a wrong implementation
    would get wrong -- a path, a count, a line number. `assert defects` passes
    for the wrong reason.

Nothing here writes to the corpus: every fixture is a temporary directory, and
every example -- VID, docs/x.md, fakepkg@1.0.0 -- is invented.
"""
from __future__ import annotations

import importlib
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

from watchquality import anchor_manifest as am
from watchquality import ocr_vote as ov
from watchquality import resolve_note as rn
from watchquality import spoken_vote as sv
from watchquality import wq_corpus_scan as cs

from conftest import RENDERING


REL = "notes/2026-08-20--n--VID.md"
NOTE_NAME = "2026-08-20--n--VID.md"

# 89 filler words + an 11-token declaration = 100 words over a 10:00 runtime,
# so the true rate is exactly 10.0 words per video-minute. Every density case
# below moves one number off that fixed point and leaves the rest alone.
FILLER = " ".join(["word"] * 89) + "\n"
TRUE_WORDS = 100
TRUE_MINUTES = "10.00"
TRUE_WPM = "10.0"

FM_CLEAN = ('duration: "10:00"\n'
            "status: distilled\n"
            "video_id: VID\n"
            "oracle: run.json\n")

DOC = ("The first pillar is trust.\n"
       "\n"
       "| Pillar | Note |\n"
       "| --- | --- |\n"
       "\n"
       "A hard-wrapped sentence that\n"
       "continues on the next line.\n"
       "\n"
       "the video_id field is read here.\n")

RE_ANY_CODE = re.compile(r"\bE-[A-Z0-9]+(?:-[A-Z0-9]+)*")


def declaration(words=TRUE_WORDS, minutes=TRUE_MINUTES, wpm=TRUE_WPM) -> str:
    return (f"density: {words} words over {minutes} video-minutes = "
            f"{wpm} words per video-minute.\n")


def codes(defects: list[str]) -> list[str]:
    return [m.group(0) for m in (RE_ANY_CODE.search(d) for d in defects) if m]


def cite_errors(body: str, root: Path, note: Path | None = None) -> list[str]:
    return [c["error"] for c in rn.resolve_citations(body, 1, root, note=note)
            if "error" in c]


def set_errors(body: str) -> list[str]:
    return [s["error"] for s in rn.find_sets(body, 1) if "error" in s]


@pytest.fixture
def corpus(tmp_path: Path):
    """A corpus root, one citable document, and a run directory per video id.

    `runs_root` is pointed at the temporary tree so `oracle: run.json` resolves
    and a note can be CLEAN -- which is what the stamp cases need. It is put
    back afterwards, because the policy object is module state.
    """
    root = (tmp_path / "corpus").resolve()
    (root / "notes" / ".resolved").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "docs" / "x.md").write_text(DOC, encoding="utf-8")

    runs = (tmp_path / "runs").resolve()
    for vid in ("VID", "AAA", "BBB"):
        (runs / vid).mkdir(parents=True)
        (runs / vid / "run.json").write_text(RENDERING, encoding="utf-8")

    ambient = rn.POLICY._raw.get("runs_root")
    rn.POLICY._raw["runs_root"] = str(runs)
    try:
        yield root
    finally:
        if ambient is None:
            rn.POLICY._raw.pop("runs_root", None)
        else:
            rn.POLICY._raw["runs_root"] = ambient


@pytest.fixture
def fake_dist(tmp_path: Path):
    """An installed distribution with one file that is not text.

    A real dependency shipping an undecodable module is not something a test
    can arrange, and writing one into this package would be a source change.
    A distribution on `sys.path` with its own `.dist-info` is the same thing
    to `importlib.metadata`, which is what the resolver asks.
    """
    site = (tmp_path / "site").resolve()
    pkg = site / "fakepkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "bad.py").write_bytes(b"x = 1  # \xff\n")
    info = site / "fakepkg-1.0.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: fakepkg\nVersion: 1.0.0\n", encoding="utf-8")
    (info / "RECORD").write_text(
        "fakepkg/__init__.py,,\nfakepkg/bad.py,,\n", encoding="utf-8")
    (info / "top_level.txt").write_text("fakepkg\n", encoding="utf-8")
    sys.path.insert(0, str(site))
    importlib.invalidate_caches()
    try:
        yield "fakepkg@1.0.0"
    finally:
        sys.path.remove(str(site))
        sys.modules.pop("fakepkg", None)
        importlib.invalidate_caches()


def write_note(root: Path, frontmatter: str, body: str,
               name: str = NOTE_NAME) -> Path:
    path = root / "notes" / name
    path.write_text(f"---\n{frontmatter}---\n{body}", encoding="utf-8")
    return path


def check(root: Path, frontmatter: str, body: str, **kw) -> list[str]:
    require_density = kw.pop("require_density", False)
    note = write_note(root, frontmatter, body, kw.pop("name", NOTE_NAME))
    return rn.check_note(note, root, require_density, **kw)[0]


def density_defects(root: Path, body: str, require_density: bool = True,
                    frontmatter: str = FM_CLEAN) -> list[str]:
    return [d for d in check(root, frontmatter, body,
                             require_density=require_density)
            if "E-DENSITY" in d]


def sidecar(root: Path, rows: str, video_id: str = "VID") -> Path:
    path = root / "notes" / ".resolved" / f"{video_id}.tsv"
    path.write_text(rows, encoding="utf-8")
    return path


def installed_spec(relpath: str = "resolve_note.py") -> str:
    """`watch-quality@<the version installed right now>:<relpath>`.

    Spelling the version out would pin these cases to one release and make
    them fail on the next one for a reason that has nothing to do with the
    rule.
    """
    name, _, version = (rn.current_stamp() or "").partition("@")
    return f"{name}@{version}:{relpath}"


not_as_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="a permission bit does not stop root, so the case cannot fire here")


# ---------------------------------------------------------------------------
# READ -- reading the file at all
# ---------------------------------------------------------------------------

def test_a_note_with_one_undecodable_byte_is_named_rather_than_raised(corpus):
    note = corpus / "notes" / NOTE_NAME
    note.write_bytes(b"---\nduration: \"10:00\"\n---\n\xff\n")
    defects, stats = rn.check_note(note, corpus, False)
    assert codes(defects) == ["E-READ"], defects
    assert "byte 26" in defects[0], defects
    assert stats is None


def test_a_directory_where_a_note_was_expected_reads_as_an_os_error(corpus):
    """A different failure from an undecodable byte, and it says so.

    `safe_read` catches OSError as well as UnicodeDecodeError; without the
    second clause a directory handed to a gate aborts the run instead of
    naming the path.
    """
    path = corpus / "notes" / "2026-08-20--d--DIR.md"
    path.mkdir()
    defects, _ = rn.check_note(path, corpus, False)
    assert codes(defects) == ["E-READ"], defects
    assert "Is a directory" in defects[0], defects


def test_a_byte_order_mark_decodes_and_is_not_a_read_defect(corpus):
    """The mark survives the decode; it is the frontmatter that then fails."""
    note = corpus / "notes" / NOTE_NAME
    note.write_bytes(b"\xef\xbb\xbf---\nduration: \"10:00\"\n---\nbody\n")
    text, why = rn.safe_read(note)
    assert text is not None and why == ""
    assert text.startswith("﻿---")
    assert "E-READ" not in " ".join(rn.check_note(note, corpus, False)[0])


def test_the_three_sibling_gates_print_this_code_from_their_own_copy_of_the_guard(
        corpus):
    """anchor_manifest, ocr_vote and spoken_vote call `safe_read` themselves and
    print the reason it hands back, so one undecodable note is refused by four
    gates in the same words -- including the byte offset, which is the part a
    reimplementation would get wrong.
    """
    note = corpus / "notes" / NOTE_NAME
    note.write_bytes(b"---\nvideo_id: VID\n---\n\xff bad\n")
    want = [f"notes/{NOTE_NAME}:1 E-READ not valid UTF-8 at byte 22"]
    assert rn.check_note(note, corpus, False)[0] == want
    assert am.check_note(note, corpus)[0] == want
    assert ov.vote_note(note, corpus)[0] == want
    assert sv.score_note(note, corpus)[0] == want


def test_the_corpus_scanner_does_not_share_the_guard_and_reads_bad_bytes_lossily(
        corpus):
    """The fourth sibling is not a fourth copy of the guard.

    `wq_corpus_scan` catches the decode error and re-reads with replacement
    characters, because one latin-1 subtitle used to abort the whole walk part
    way through. It prints this code only when the file cannot be opened at
    all, and with the exception's own text rather than the guard's.
    """
    note = corpus / "notes" / NOTE_NAME
    note.write_bytes(b"---\nvideo_id: VID\n---\n\xff bad\n")
    assert cs.scan([note], corpus) == []

    path = corpus / "notes" / "2026-08-20--d--DIR.md"
    path.mkdir()
    defects = cs.scan([path], corpus)
    assert codes(defects) == ["E-READ"], defects
    assert defects[0].startswith(f"notes/{path.name}:1 E-READ"), defects
    assert "Is a directory" in defects[0], defects


# ---------------------------------------------------------------------------
# FRONTMATTER
# ---------------------------------------------------------------------------

def test_a_blank_line_before_the_opening_marker_is_no_frontmatter(corpus):
    note = corpus / "notes" / NOTE_NAME
    note.write_text("\n---\nduration: \"10:00\"\n---\nbody\n", encoding="utf-8")
    defects, _ = rn.check_note(note, corpus, False)
    assert codes(defects) == ["E-FRONTMATTER"], defects
    assert rn.split_frontmatter(note.read_text(encoding="utf-8")) is None


def test_four_dashes_are_not_the_opening_marker(corpus):
    note = corpus / "notes" / NOTE_NAME
    note.write_text("----\nduration: \"10:00\"\n----\nbody\n", encoding="utf-8")
    defects, _ = rn.check_note(note, corpus, False)
    assert codes(defects) == ["E-FRONTMATTER"], defects


def test_trailing_spaces_after_the_marker_still_open_the_block(corpus):
    """The marker is the stripped line, so a trailing space is not a defect."""
    note = corpus / "notes" / NOTE_NAME
    note.write_text("---   \nduration: \"10:00\"\n---\t\nbody\n", encoding="utf-8")
    split = rn.split_frontmatter(note.read_text(encoding="utf-8"))
    assert split is not None and split[0] == 'duration: "10:00"'
    assert "E-FRONTMATTER" not in " ".join(rn.check_note(note, corpus, False)[0])


# ---------------------------------------------------------------------------
# NO-VIDEO-ID
# ---------------------------------------------------------------------------

def test_a_note_with_sets_and_no_citations_is_written_without_a_video_id(corpus):
    """The sidecar only exists to hold quotes, so no quotes means no need."""
    note = write_note(corpus, 'duration: "10:00"\n',
                      "{{SET:things}}\n- a\n- b\n{{/SET}}\n")
    defects, rendered = rn.write_note(note, corpus)
    assert defects == [] and rendered == 0
    assert "(2 listed):" in note.read_text(encoding="utf-8")


def test_the_field_spelled_with_a_capital_v_is_not_the_field(corpus):
    note = write_note(corpus, "Video_id: VID\n",
                      '{{CITE:docs/x.md#"The first pillar"}}\n')
    defects, rendered = rn.write_note(note, corpus)
    assert codes(defects) == ["E-NO-VIDEO-ID"], defects
    assert rendered == 0
    assert "{{CITE:" in note.read_text(encoding="utf-8"), "the note was rewritten"


def test_a_video_id_whose_value_sits_on_the_next_line_reads_as_absent(corpus):
    """`[ \\t]*`, never `\\s*`: a newline-blind pattern adopts the line below."""
    note = write_note(corpus, "video_id:\nVID\n",
                      '{{CITE:docs/x.md#"The first pillar"}}\n')
    defects, _ = rn.write_note(note, corpus)
    assert codes(defects) == ["E-NO-VIDEO-ID"], defects


def test_a_video_id_of_todo_is_present_and_meaningless_and_accepted(corpus):
    """A string rule cannot make a value MEAN something. This is the floor."""
    note = write_note(corpus, "video_id: TODO\n",
                      '{{CITE:docs/x.md#"The first pillar"}}\n')
    defects, rendered = rn.write_note(note, corpus)
    assert defects == [] and rendered == 1
    assert (corpus / "notes" / ".resolved" / "TODO.tsv").is_file()


# ---------------------------------------------------------------------------
# DURATION
# ---------------------------------------------------------------------------

def test_four_colon_separated_groups_are_not_a_runtime(corpus):
    """`1:2:3:4` used to parse as 223384s and became the density oracle."""
    assert rn.parse_duration('duration: "1:2:3:4"\n') is None
    defects = check(corpus, 'duration: "1:2:3:4"\nstatus: distilled\n', "body\n")
    assert "E-DURATION" in codes(defects), defects


def test_a_minutes_group_of_seventy_five_is_not_a_clock_reading(corpus):
    assert rn.parse_duration('duration: "1:75:00"\n') is None
    assert "E-DURATION" in codes(
        check(corpus, 'duration: "1:75:00"\nstatus: distilled\n', "body\n"))


def test_a_duration_whose_value_sits_on_the_next_line_is_not_read(corpus):
    assert rn.parse_duration("duration:\n90:00\n") is None
    assert "E-DURATION" in codes(
        check(corpus, "duration:\n90:00\nstatus: distilled\n", "body\n"))


def test_a_leading_group_of_ninety_is_ninety_minutes(corpus):
    """Only the groups AFTER the first are clock digits."""
    assert rn.parse_duration('duration: "90:00"\n') == 5400
    assert "E-DURATION" not in codes(
        check(corpus, 'duration: "90:00"\nstatus: distilled\n', "body\n"))


def test_a_duration_of_n_slash_a_holds_no_digits_for_the_pattern(corpus):
    """Refused because nothing matched, not because anything read the value."""
    assert rn.parse_duration("duration: n/a\n") is None
    assert "E-DURATION" in codes(
        check(corpus, "duration: n/a\nstatus: distilled\n", "body\n"))


# ---------------------------------------------------------------------------
# DURATION-ZERO
# ---------------------------------------------------------------------------

def test_a_two_group_zero_runtime_leaves_density_no_denominator(corpus):
    defects = check(corpus, 'duration: "0:00"\nstatus: distilled\n', "body\n")
    assert "E-DURATION-ZERO" in codes(defects), defects
    assert "0:00" in [d for d in defects if "E-DURATION-ZERO" in d][0]


def test_a_three_group_zero_runtime_is_the_same_zero(corpus):
    assert rn.parse_duration('duration: "0:00:00"\n') == 0
    assert "E-DURATION-ZERO" in codes(
        check(corpus, 'duration: "0:00:00"\nstatus: distilled\n', "body\n"))


def test_a_runtime_of_one_second_still_has_a_denominator(corpus):
    assert rn.parse_duration('duration: "0:01"\n') == 1
    assert "E-DURATION-ZERO" not in codes(
        check(corpus, 'duration: "0:01"\nstatus: distilled\n', "body\n"))


def test_a_zero_runtime_returns_before_any_anchor_is_looked_at(corpus):
    """One defect, and it is the one that makes the other unanswerable."""
    defects = check(corpus, 'duration: "0:00"\nstatus: distilled\n',
                    "see `[45:00]` here\n")
    assert "E-DURATION-ZERO" in codes(defects)
    assert "E-ANCHOR-RANGE" not in codes(defects), defects


# ---------------------------------------------------------------------------
# DENSITY-MISSING
# ---------------------------------------------------------------------------

def test_the_word_density_with_no_number_declares_nothing(corpus):
    defects = density_defects(corpus, FILLER + "the density of this note is fine.\n")
    assert codes(defects) == ["E-DENSITY-MISSING"], defects


def test_a_density_sentence_stating_one_number_is_a_declaration(corpus):
    """89 filler words plus a three-token sentence is 92, and 92 is stated."""
    assert density_defects(corpus, FILLER + "density: 92 words.\n") == []


def test_a_run_that_did_not_ask_for_density_accepts_a_note_without_one(corpus):
    assert density_defects(corpus, FILLER, require_density=True) != []
    assert density_defects(corpus, FILLER, require_density=False) == []


def test_a_density_line_inside_a_fenced_block_still_counts_as_declared(corpus):
    """Nothing strips fences, so the fence markers are two of the 94 words."""
    body = FILLER + "```\ndensity: 94 words.\n```\n"
    assert density_defects(corpus, body) == []


def test_a_density_sentence_whose_number_is_in_no_declaring_shape_declares_nothing(
        corpus):
    """"At least one number" is not the rule. Three phrasings are.

    `N words`, `N video-minute(s)` and `N words per video-minute` are the whole
    vocabulary, so a sentence about density carrying a perfectly good number in
    any other shape is refused exactly as a numberless one is.
    """
    defects = density_defects(corpus, FILLER + "density: 92 things.\n")
    assert codes(defects) == ["E-DENSITY-MISSING"], defects
    assert "no declared density line" in defects[0], defects
    assert density_defects(corpus, FILLER + "density: 92 words.\n") == []


# ---------------------------------------------------------------------------
# DENSITY-WORDS
# ---------------------------------------------------------------------------

def test_the_marks_the_demotion_renderer_wrote_are_not_the_authors_words(corpus):
    """The denominator is the body AFTER the tool's own bytes come out.

    `strip_machine_marks` removes the integrity blockquote the demotion
    renderer writes, along with the ORPHAN and auto-demoted markers and the
    Speculation heading. Counting them would make every render move the number
    the note declares -- the tool failing the note for the tool's own words.
    """
    integrity = "> integrity: one two three four five\n"
    assert density_defects(corpus, FILLER + declaration() + integrity) == []

    defects = density_defects(corpus, FILLER + declaration(words=106) + integrity)
    assert codes(defects) == ["E-DENSITY-WORDS"], defects
    assert "declared 106 counted 100" in defects[0], defects


def test_a_hedged_word_count_is_read_as_the_exact_number_it_states(corpus):
    """`~2,150` is the model-authored estimate the whole check exists to kill."""
    body = FILLER + "density: ~2,150 words over 10.00 video-minutes = 10.0 words per video-minute.\n"
    defects = density_defects(corpus, body)
    assert codes(defects) == ["E-DENSITY-WORDS"], defects
    assert "declared 2150 counted 100" in defects[0], defects


def test_a_word_count_off_by_one_is_refused_by_the_zero_tolerance(corpus):
    defects = density_defects(corpus, FILLER + declaration(words=101))
    assert codes(defects) == ["E-DENSITY-WORDS"], defects
    assert "declared 101 counted 100" in defects[0], defects


def test_a_decimal_word_count_parses_as_itself(corpus):
    """Not as its last digit, which is what a digit-at-a-time reader gives."""
    defects = density_defects(corpus, FILLER + declaration(words="100.5"))
    assert "declared 100.5 counted 100" in defects[0], defects


def test_a_later_unrelated_word_count_is_outside_the_declaring_sentence(corpus):
    """The window is a sentence. A forward-only one pulled in `12000 words`."""
    body = (FILLER + "density: 107 words over 10.00 video-minutes = 10.7 words "
            "per video-minute. The transcript holds 12000 words in all.\n")
    assert density_defects(corpus, body) == []


# ---------------------------------------------------------------------------
# DENSITY-MINUTES
# ---------------------------------------------------------------------------

def test_a_minute_count_wrong_by_two_minutes_is_refused(corpus):
    defects = density_defects(corpus, FILLER + declaration(minutes="12.00"))
    assert codes(defects) == ["E-DENSITY-MINUTES"], defects
    assert "declared 12 counted 10.00" in defects[0], defects


def test_a_minute_count_wrong_by_exactly_half_a_minute_is_the_boundary(corpus):
    """Strictly greater than the tolerance, so half a minute passes."""
    assert density_defects(corpus, FILLER + declaration(minutes="10.50")) == []


def test_a_declaration_written_before_the_word_density_is_still_read(corpus):
    """"...over 12 video-minutes -- that is its density" reads backwards."""
    body = FILLER + "Ninety-five words over 12.00 video-minutes -- that is its density.\n"
    defects = density_defects(corpus, body)
    assert codes(defects) == ["E-DENSITY-MINUTES"], defects
    assert "declared 12" in defects[0], defects


def test_video_minute_in_the_singular_is_also_a_declaration(corpus):
    body = (FILLER + "density: 100 words over 12.00 video-minute = 10.0 words "
            "per video-minute.\n")
    defects = density_defects(corpus, body)
    assert "E-DENSITY-MINUTES" in codes(defects), defects


# ---------------------------------------------------------------------------
# DENSITY-WPM
# ---------------------------------------------------------------------------

def test_emphasis_markers_do_not_hide_a_wrong_rate(corpus):
    body = (FILLER + "density: 100 words over 10.00 video-minutes = "
            "**12.0 words per video-minute**.\n")
    defects = density_defects(corpus, body)
    assert codes(defects) == ["E-DENSITY-WPM"], defects
    assert "declared 12 counted 10.0" in defects[0], defects


def test_a_rate_phrase_that_wraps_across_a_line_is_still_read(corpus):
    """Notes are hard-wrapped near 80 columns, so the phrase spans a newline."""
    body = (FILLER + "density: 100 words over 10.00 video-minutes = 12.0 words per\n"
            "video-minute.\n")
    defects = density_defects(corpus, body)
    assert codes(defects) == ["E-DENSITY-WPM"], defects


def test_a_rate_wrong_by_exactly_half_a_word_per_minute_is_the_boundary(corpus):
    assert "E-DENSITY-WPM" not in codes(
        density_defects(corpus, FILLER + declaration(wpm="10.5")))


def test_every_wrong_rate_statement_is_reported_not_only_the_first(corpus):
    body = (FILLER
            + "density: 100 words over 10.00 video-minutes = 1.0 words per video-minute.\n"
            + "The density again: 2.0 words per video-minute.\n"
            + "And density once more: 3.0 words per video-minute.\n")
    defects = density_defects(corpus, body)
    assert codes(defects).count("E-DENSITY-WPM") == 3, defects


# ---------------------------------------------------------------------------
# CITE-EMPTY
# ---------------------------------------------------------------------------

def test_a_citation_quoting_nothing_at_all_is_refused(corpus):
    errors = cite_errors('{{CITE:docs/x.md#""}}', corpus)
    assert errors == ["E-CITE-EMPTY empty quote"], errors


def test_a_quote_of_one_space_normalises_to_nothing(corpus):
    assert cite_errors('{{CITE:docs/x.md#" "}}', corpus) == ["E-CITE-EMPTY empty quote"]


def test_a_quote_of_only_emphasis_characters_normalises_to_nothing(corpus):
    """`*` and backticks come off before the quote is weighed."""
    assert cite_errors('{{CITE:docs/x.md#"**``"}}', corpus) == [
        "E-CITE-EMPTY empty quote"]


def test_a_quote_of_a_single_visible_character_is_not_empty(corpus):
    """Meaningless, and this rule is not the one that says so."""
    assert cite_errors('{{CITE:docs/x.md#"T"}}', corpus) == []


# ---------------------------------------------------------------------------
# CITE-MALFORMED
# ---------------------------------------------------------------------------

def test_a_citation_whose_closing_quote_is_missing_is_reported(corpus):
    errors = cite_errors('{{CITE:docs/x.md#"trust}}', corpus)
    assert errors == ['E-CITE-MALFORMED token does not close as '
                      '{{CITE:path#"quote"}}'], errors


def test_a_space_instead_of_a_colon_after_the_keyword_is_reported(corpus):
    assert codes(cite_errors('{{CITE docs/x.md#"trust"}}', corpus)) == [
        "E-CITE-MALFORMED"]


def test_an_unclosed_token_does_not_swallow_the_citation_after_it(corpus):
    """Two defects, not one.

    The quote may not contain `{{`, or the unclosed token scans forward and
    eats the next well-formed citation -- which is how two defects were
    reported as one and the second was never resolved at all.
    """
    body = ('{{CITE:docs/x.md#"trust and then '
            '{{CITE:docs/x.md#"nowhere in the file"}}')
    errors = cite_errors(body, corpus)
    assert sorted(codes(errors)) == ["E-CITE-MALFORMED", "E-CITE-NOMATCH"], errors


def test_a_lower_case_keyword_is_read_only_by_the_loose_sweep(corpus):
    """The strict pattern will not read it, so the sweep has to."""
    assert codes(cite_errors('{{cite:docs/x.md#"The first pillar"}}', corpus)) == [
        "E-CITE-MALFORMED"]


def test_a_stray_closing_marker_is_swept_for_by_nothing(corpus):
    """Named as a neighbour because it PASSES: nothing looks for a lone `}}`."""
    assert cite_errors("a sentence ending in }} and carrying on\n", corpus) == []


# ---------------------------------------------------------------------------
# CITE-PATH
# ---------------------------------------------------------------------------

def test_a_cited_path_climbing_out_with_parent_segments_is_outside(corpus):
    errors = cite_errors('{{CITE:../secrets.md#"anything"}}', corpus)
    assert errors == ["E-CITE-PATH outside repo: ../secrets.md"], errors


def test_an_absolute_cited_path_replaces_the_root_entirely(corpus):
    errors = cite_errors('{{CITE:/etc/hosts#"localhost"}}', corpus)
    assert codes(errors) == ["E-CITE-PATH"] and "/etc/hosts" in errors[0], errors


def test_a_link_inside_the_repository_pointing_out_of_it_is_outside(corpus, tmp_path):
    """Resolution happens before the containment test, so the link is followed."""
    outside = (tmp_path / "outside.md").resolve()
    outside.write_text("The first pillar is trust.\n", encoding="utf-8")
    (corpus / "docs" / "link.md").symlink_to(outside)
    errors = cite_errors('{{CITE:docs/link.md#"The first pillar"}}', corpus)
    assert errors == ["E-CITE-PATH outside repo: docs/link.md"], errors


def test_a_package_address_is_outside_the_repository_on_purpose(corpus):
    """The version in the address is what makes leaving the repo safe.

    Asserting the ABSENCE of this code would prove nothing: a package address
    joined to the corpus root still lands inside the root, so `E-CITE-PATH` is
    unreachable for this input whichever way the citation is routed. The claim
    is the routing itself -- the address goes to the installed distribution and
    resolves to a file that is not in the corpus at all.
    """
    body = '{{CITE:' + installed_spec() + '#"' + rn.PROG + '"}}'
    records = rn.resolve_citations(body, 1, corpus)
    assert [r.get("error") for r in records] == [None], records
    target = records[0]["target"]
    assert target.name == rn.PROG and target.is_file(), target
    assert not target.is_relative_to(corpus), target
    assert records[0]["target_line"] > 0, records


# ---------------------------------------------------------------------------
# CITE-NOFILE, resolved against the repository
# ---------------------------------------------------------------------------

def test_a_cited_path_that_never_existed_has_no_document_behind_it(corpus):
    errors = cite_errors('{{CITE:docs/never.md#"anything"}}', corpus)
    assert errors == ["E-CITE-NOFILE docs/never.md"], errors


def test_a_cited_path_naming_a_directory_exists_and_is_not_a_file(corpus):
    assert cite_errors('{{CITE:docs#"anything"}}', corpus) == ["E-CITE-NOFILE docs"]


def test_a_token_wrapped_before_its_hash_still_names_the_real_path(corpus):
    """The token may be hard-wrapped; the PATH is whitespace-collapsed."""
    assert cite_errors('{{CITE:docs/x.md\n#"The first pillar"}}', corpus) == []


def test_an_empty_cited_path_joins_to_the_root_and_is_not_a_file(corpus):
    errors = cite_errors('{{CITE:#"The first pillar"}}', corpus)
    assert errors == ["E-CITE-NOFILE "], errors


# ---------------------------------------------------------------------------
# CITE-NOMATCH
# ---------------------------------------------------------------------------

def test_a_plausible_quote_absent_from_the_cited_file_is_refused(corpus):
    errors = cite_errors('{{CITE:docs/x.md#"the second pillar is trust"}}', corpus)
    assert codes(errors) == ["E-CITE-NOMATCH"], errors
    assert 'does not contain "the second pillar is trust"' in errors[0], errors


def test_a_quote_spanning_a_blank_line_and_a_table_header_is_refused(corpus):
    """Every word is in the file, in that order, across three blocks.

    Without a block sentinel this resolved -- a fabricated citation passing as
    real, which is the failure the boundary marker exists to stop.
    """
    errors = cite_errors('{{CITE:docs/x.md#"is trust. | Pillar |"}}', corpus)
    assert codes(errors) == ["E-CITE-NOMATCH"], errors
    # ...and the words really are contiguous once the sentinel is taken away.
    assert "is trust. | Pillar |" in rn.normalise(DOC, 1, boundaries=False)[0]


def test_a_quote_missing_an_underscore_no_longer_matches_the_identifier(corpus):
    """`_` is NOT stripped: dropping it let `videoid` match the real name."""
    errors = cite_errors('{{CITE:docs/x.md#"the videoid field"}}', corpus)
    assert codes(errors) == ["E-CITE-NOMATCH"], errors


def test_a_correct_quote_that_wraps_across_a_newline_still_resolves(corpus):
    """The false-positive direction: one red light here ends the experiment."""
    assert cite_errors('{{CITE:docs/x.md#"sentence that continues"}}', corpus) == []


# ---------------------------------------------------------------------------
# CITE-SELF
# ---------------------------------------------------------------------------

def test_a_note_citing_its_own_filename_is_not_evidence_for_itself(corpus):
    note = write_note(corpus, FM_CLEAN, "alpha beta gamma\n")
    errors = cite_errors(f'{{{{CITE:notes/{NOTE_NAME}#"alpha beta"}}}}', corpus,
                         note=note)
    assert errors == ["E-CITE-SELF a note is not evidence for itself"], errors


def test_a_note_citing_itself_through_dot_segments_is_the_same_file(corpus):
    """Both sides are resolved, so a detour through `..` does not disguise it."""
    note = write_note(corpus, FM_CLEAN, "alpha beta gamma\n")
    body = f'{{{{CITE:notes/../notes/{NOTE_NAME}#"alpha beta"}}}}'
    assert codes(cite_errors(body, corpus, note=note)) == ["E-CITE-SELF"]


def test_two_notes_citing_each_other_is_not_self_citation(corpus):
    note = write_note(corpus, FM_CLEAN, "alpha beta gamma\n")
    other = write_note(corpus, FM_CLEAN, "alpha beta gamma\n",
                       name="2026-08-20--m--VID.md")
    body = f'{{{{CITE:notes/{other.name}#"alpha beta"}}}}'
    assert cite_errors(body, corpus, note=note) == []


def test_a_check_with_no_note_supplied_cannot_see_self_citation(corpus):
    """The rule needs to know which note is asking; without it, it is off."""
    write_note(corpus, FM_CLEAN, "alpha beta gamma\n")
    body = f'{{{{CITE:notes/{NOTE_NAME}#"alpha beta"}}}}'
    assert cite_errors(body, corpus, note=None) == []


# ---------------------------------------------------------------------------
# CITE-UNREADABLE
# ---------------------------------------------------------------------------

def test_a_cited_file_with_an_undecodable_byte_is_a_different_failure(corpus):
    (corpus / "docs" / "bad.md").write_bytes(b"The first \xff pillar\n")
    errors = cite_errors('{{CITE:docs/bad.md#"The first"}}', corpus)
    assert codes(errors) == ["E-CITE-UNREADABLE"], errors
    assert "not valid UTF-8 at byte 10" in errors[0], errors


@not_as_root
def test_a_cited_file_the_process_may_not_open_is_reported_as_unreadable(corpus):
    shut = corpus / "docs" / "shut.md"
    shut.write_text("The first pillar is trust.\n", encoding="utf-8")
    os.chmod(shut, 0o000)
    try:
        errors = cite_errors('{{CITE:docs/shut.md#"The first"}}', corpus)
    finally:
        os.chmod(shut, 0o644)
    assert codes(errors) == ["E-CITE-UNREADABLE"], errors
    assert "Permission denied" in errors[0], errors


def test_an_empty_cited_file_reads_fine_and_fails_as_a_missing_quote(corpus):
    (corpus / "docs" / "empty.md").write_text("", encoding="utf-8")
    errors = cite_errors('{{CITE:docs/empty.md#"The first"}}', corpus)
    assert codes(errors) == ["E-CITE-NOMATCH"], errors


def test_an_undecodable_file_inside_an_installed_package_reaches_the_same_reader(
        corpus, fake_dist):
    """The package path and the repository path share one reader, so they
    share one code -- and the address in the message says which path it came
    down."""
    errors = cite_errors('{{CITE:' + fake_dist + ':bad.py#"x = 1"}}', corpus)
    assert codes(errors) == ["E-CITE-UNREADABLE"], errors
    assert "fakepkg@1.0.0:bad.py" in errors[0], errors


# ---------------------------------------------------------------------------
# CITE-PKGSPEC
# ---------------------------------------------------------------------------

def test_a_package_address_with_no_at_sign_is_not_an_address(corpus):
    _, why = rn.package_target("watchquality/resolve_note.py")
    assert why.startswith("E-CITE-PKGSPEC"), why
    assert "watchquality/resolve_note.py" in why


def test_a_package_address_with_no_path_after_the_colon_is_not_an_address():
    _, why = rn.package_target(installed_spec(""))
    assert why.startswith("E-CITE-PKGSPEC"), why


def test_a_package_address_with_an_empty_version_is_not_an_address():
    _, why = rn.package_target("watch-quality@:resolve_note.py")
    assert why.startswith("E-CITE-PKGSPEC"), why


def test_no_ordinary_run_can_reach_the_package_address_complaint(corpus):
    """The docstring presents this as a failure a reader will meet. It is not.

    Every production caller applies the same `name@version:path` pattern
    BEFORE calling, so a value that would fail it is sent to the repository
    path instead and comes back as a missing file.
    """
    errors = cite_errors('{{CITE:a@b#"anything"}}', corpus)
    assert codes(errors) == ["E-CITE-NOFILE"], errors
    assert "E-CITE-PKGSPEC" not in " ".join(errors)


# ---------------------------------------------------------------------------
# CITE-NOPKG
# ---------------------------------------------------------------------------

def test_a_distribution_nobody_has_installed_is_uncheckable_here(corpus):
    _, why = rn.package_target("no-such-distribution-here@1.0.0:a.py")
    assert why == "E-CITE-NOPKG no-such-distribution-here is not installed", why


def test_an_underscore_spelling_names_the_same_installed_distribution():
    """The draft row expected a refusal here and the code does not refuse.

    `importlib.metadata` normalises a distribution name, so `watch_quality`
    and `watch-quality` are the same distribution and the citation resolves.
    The neighbour is a must-NOT-fire, not a must-fire.
    """
    target, why = rn.package_target(installed_spec().replace("-", "_", 1))
    assert why == "", why
    assert target is not None and target.name == "resolve_note.py"


def test_a_repository_file_named_with_an_at_sign_is_sent_down_the_package_path(
        corpus):
    """One test decides the path, and an ambiguous value goes to the package.

    A real repository file called `a@1.0:b.md` is never opened; the reader is
    told a distribution called `a` is not installed.
    """
    ambiguous = corpus / "a@1.0:b.md"
    try:
        ambiguous.write_text("The first pillar is trust.\n", encoding="utf-8")
    except OSError:  # pragma: no cover - filesystem refuses the name
        pytest.skip("this filesystem will not take a colon in a filename")
    errors = cite_errors('{{CITE:a@1.0:b.md#"The first pillar"}}', corpus)
    assert errors == ["E-CITE-NOPKG a is not installed"], errors


def test_an_editable_install_still_resolves_through_the_import_system():
    """An editable install lists a loader shim, not the modules.

    The file list therefore finds nothing for exactly the case a developer is
    in most often, and the import system is asked instead.
    """
    target, why = rn.package_target(installed_spec())
    assert why == "" and target is not None
    assert target.is_file() and target.name == "resolve_note.py"


# ---------------------------------------------------------------------------
# CITE-VERSION
# ---------------------------------------------------------------------------

def test_a_version_that_was_never_released_is_refused():
    _, why = rn.package_target("watch-quality@0.0.1-never:resolve_note.py")
    assert why.startswith("E-CITE-VERSION"), why
    assert "resolved against 0.0.1-never" in why, why


def test_a_version_with_a_trailing_zero_added_is_refused_by_string_equality():
    """Packaging calls `X.Y.Z` and `X.Y.Z.0` one release. This comparison
    does not, and the row says so rather than pretending otherwise."""
    _, why = rn.package_target(installed_spec().replace(":", ".0:", 1))
    assert why.startswith("E-CITE-VERSION"), why


def test_the_exact_installed_version_resolves():
    target, why = rn.package_target(installed_spec())
    assert (why, target is None) == ("", False)


def test_a_sidecar_row_citing_a_moved_version_cannot_be_refreshed(corpus):
    """Re-resolving against another release would move the citation to code
    the note was never graded by, so the row stays a defect."""
    note = write_note(corpus, "video_id: VID\n", "body\n")
    sidecar(corpus, "watch-quality@0.0.1-never:resolve_note.py\t1\tdead\tPROG\n")
    defects, refreshed = rn.refresh_sidecar(corpus, note)
    assert refreshed == 0
    assert codes(defects) == ["E-CITE-VERSION"], defects


# ---------------------------------------------------------------------------
# CITE-NOFILE, resolved against an installed distribution
# ---------------------------------------------------------------------------

def test_a_module_that_release_does_not_ship_is_refused():
    _, why = rn.package_target(installed_spec("no_such_module.py"))
    assert why.startswith("E-CITE-NOFILE"), why
    assert "no_such_module.py" in why, why


def test_a_path_naming_a_directory_inside_the_package_is_not_a_file():
    _, why = rn.package_target(installed_spec("__pycache__"))
    assert why.startswith("E-CITE-NOFILE"), why


def test_a_path_that_is_only_a_suffix_of_a_shipped_path_is_refused():
    """The file list is matched on a whole path or a whole trailing segment,
    so `note.py` may not claim `resolve_note.py`."""
    _, why = rn.package_target(installed_spec("note.py"))
    assert why.startswith("E-CITE-NOFILE"), why


def test_a_shipped_module_is_found_even_when_the_file_list_is_a_shim():
    target, why = rn.package_target(installed_spec("wq_policy.py"))
    assert why == "" and target is not None and target.is_file()


# ---------------------------------------------------------------------------
# CITE-STALE
# ---------------------------------------------------------------------------

def test_a_cited_file_edited_after_resolution_no_longer_matches_its_hash(corpus):
    cited = corpus / "docs" / "x.md"
    sidecar(corpus, f"docs/x.md\t1\t{rn.sha256(cited)}\tThe first pillar\n")
    cited.write_text(DOC + "\nan added paragraph.\n", encoding="utf-8")
    defects = rn.check_sidecar(corpus, "video_id: VID\n", REL)
    assert codes(defects) == ["E-CITE-STALE"], defects
    assert "docs/x.md changed since resolution at line 1" in defects[0], defects


def test_a_cited_file_that_has_gone_is_reported_as_stale(corpus):
    sidecar(corpus, "docs/gone.md\t1\t" + "0" * 64 + "\tThe first pillar\n")
    defects = rn.check_sidecar(corpus, "video_id: VID\n", REL)
    assert defects == [f"{REL}:1 E-CITE-STALE cited file gone: docs/gone.md"], defects


def test_an_edit_above_the_quote_is_repaired_by_a_refresh(corpus):
    """A legitimate edit to a cited file used to leave a permanent red light,
    which is how a check gets switched off."""
    note = write_note(corpus, "video_id: VID\n", "body\n")
    cited = corpus / "docs" / "x.md"
    side = sidecar(corpus, f"docs/x.md\t1\t{rn.sha256(cited)}\tThe first pillar\n")
    cited.write_text("a new opening line.\n" + DOC, encoding="utf-8")
    defects, refreshed = rn.refresh_sidecar(corpus, note)
    assert (defects, refreshed) == ([], 1)
    assert side.read_text(encoding="utf-8").split("\t")[1] == "2"


def test_an_edit_that_leaves_the_length_unchanged_is_caught_by_the_hash(corpus):
    """Only the sha can notice this one; a size or a line count cannot."""
    cited = corpus / "docs" / "x.md"
    before = rn.sha256(cited)
    cited.write_text(DOC.replace("trust.", "truth."), encoding="utf-8")
    assert len(cited.read_text(encoding="utf-8")) == len(DOC)
    sidecar(corpus, f"docs/x.md\t1\t{before}\tThe first pillar\n")
    assert codes(rn.check_sidecar(corpus, "video_id: VID\n", REL)) == ["E-CITE-STALE"]


def test_a_note_with_no_sidecar_has_nothing_to_re_check(corpus):
    assert rn.check_sidecar(corpus, "video_id: NOSUCH\n", REL) == []


# ---------------------------------------------------------------------------
# CITE-UNRESOLVED
# ---------------------------------------------------------------------------

def test_one_broken_citation_stops_the_good_one_being_rendered(corpus):
    """A half-rendered note hides the defect it was meant to surface."""
    body = ('{{CITE:docs/x.md#"The first pillar"}} and '
            '{{CITE:docs/gone.md#"anything"}}\n')
    note = write_note(corpus, "video_id: VID\n", body)
    defects, rendered = rn.write_note(note, corpus)
    assert codes(defects) == ["E-CITE-NOFILE", "E-CITE-UNRESOLVED"], defects
    assert rendered == 0
    assert note.read_text(encoding="utf-8").endswith(body)


def test_a_malformed_set_blocks_a_write_just_as_a_citation_does(corpus):
    note = write_note(corpus, "video_id: VID\n", "{{SET:things}}\n- a\n")
    defects, rendered = rn.write_note(note, corpus)
    assert codes(defects) == ["E-SET-UNCLOSED", "E-CITE-UNRESOLVED"], defects
    assert rendered == 0


def test_a_note_with_no_citations_and_no_sets_is_left_untouched(corpus):
    note = write_note(corpus, "video_id: VID\n", "plain prose, nothing to render.\n")
    before = note.read_text(encoding="utf-8")
    assert rn.write_note(note, corpus) == ([], 0)
    assert note.read_text(encoding="utf-8") == before


def test_a_note_red_for_status_is_still_written(corpus):
    """The write reads two families only, so a note red elsewhere renders."""
    note = write_note(corpus, "video_id: VID\nstatus: nonsense\n",
                      '{{CITE:docs/x.md#"The first pillar"}}\n')
    defects, rendered = rn.write_note(note, corpus)
    assert (defects, rendered) == ([], 1)
    assert "`docs/x.md`" in note.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# SIDECAR-ROW, on the ordinary check
# ---------------------------------------------------------------------------

def test_an_audit_row_holding_only_a_path_is_too_short_to_read(corpus):
    """A short row used to pad with "" and report a false staleness, blaming
    the cited document for the sidecar's own corruption."""
    sidecar(corpus, "docs/x.md\n")
    defects = rn.check_sidecar(corpus, "video_id: VID\n", REL)
    assert defects == [f"{REL}:1 E-SIDECAR-ROW VID.tsv:1 has 1 of 4 columns"], defects


def test_an_audit_row_of_exactly_three_columns_passes_the_ordinary_check(corpus):
    """The message says four and the check demands three. Both are here."""
    cited = corpus / "docs" / "x.md"
    sidecar(corpus, f"docs/x.md\t1\t{rn.sha256(cited)}\n")
    assert rn.check_sidecar(corpus, "video_id: VID\n", REL) == []


def test_audit_columns_separated_by_spaces_read_as_one_column(corpus):
    cited = corpus / "docs" / "x.md"
    sidecar(corpus, f"docs/x.md 1 {rn.sha256(cited)}\n")
    defects = rn.check_sidecar(corpus, "video_id: VID\n", REL)
    assert "has 1 of 4 columns" in defects[0], defects


def test_a_comment_or_blank_audit_row_is_skipped(corpus):
    cited = corpus / "docs" / "x.md"
    sidecar(corpus, f"# note\t{REL}\n\ndocs/x.md\t1\t{rn.sha256(cited)}\tq\n")
    assert rn.check_sidecar(corpus, "video_id: VID\n", REL) == []


def test_a_recorded_path_climbing_out_with_parent_segments_is_refused(corpus):
    sidecar(corpus, "../elsewhere.md\t1\t" + "0" * 64 + "\tq\n")
    defects = rn.check_sidecar(corpus, "video_id: VID\n", REL)
    assert defects == [f"{REL}:1 E-SIDECAR-ROW VID.tsv:1 points outside the "
                       f"repo: ../elsewhere.md"], defects


def test_a_recorded_absolute_path_is_refused(corpus):
    sidecar(corpus, "/etc/hosts\t1\t" + "0" * 64 + "\tq\n")
    defects = rn.check_sidecar(corpus, "video_id: VID\n", REL)
    assert codes(defects) == ["E-SIDECAR-ROW"] and "/etc/hosts" in defects[0]


def test_a_recorded_package_address_is_outside_the_repository_on_purpose(corpus):
    """The escaping-path rule must not fire on an installed dependency."""
    spec = installed_spec()
    target, why = rn.package_target(spec)
    assert why == ""
    sidecar(corpus, f"{spec}\t1\t{rn.sha256(target)}\tPROG\n")
    assert rn.check_sidecar(corpus, "video_id: VID\n", REL) == []


def test_a_recorded_path_inside_the_repository_and_gone_is_staleness(corpus):
    """Inside and absent is a different answer from outside, and says so."""
    sidecar(corpus, "docs/vanished.md\t1\t" + "0" * 64 + "\tq\n")
    defects = rn.check_sidecar(corpus, "video_id: VID\n", REL)
    assert codes(defects) == ["E-CITE-STALE"], defects


# ---------------------------------------------------------------------------
# SIDECAR-ROW, on the refresh path
# ---------------------------------------------------------------------------

def test_a_three_column_row_has_no_quote_for_a_refresh_to_re_resolve(corpus):
    note = write_note(corpus, "video_id: VID\n", "body\n")
    cited = corpus / "docs" / "x.md"
    sidecar(corpus, f"docs/x.md\t1\t{rn.sha256(cited)}\n")
    defects, refreshed = rn.refresh_sidecar(corpus, note)
    assert refreshed == 0
    assert defects == [f"{REL}:1 E-SIDECAR-ROW VID.tsv:1 has 3 of 4 columns"], defects


def test_a_row_with_an_empty_quote_column_has_four_columns(corpus):
    """Four columns, so this rule does not fire -- and the empty quote then
    matches at offset zero, so the row refreshes to line 1 unchallenged."""
    note = write_note(corpus, "video_id: VID\n", "body\n")
    cited = corpus / "docs" / "x.md"
    before = rn.sha256(cited)
    cited.write_text("a new opening line.\n" + DOC, encoding="utf-8")
    side = sidecar(corpus, f"docs/x.md\t9\t{before}\t\n")
    defects, refreshed = rn.refresh_sidecar(corpus, note)
    assert defects == [] and refreshed == 1
    assert side.read_text(encoding="utf-8").split("\t")[1] == "1"


def test_a_row_outside_the_requested_set_is_left_alone(corpus):
    """`only` is how a stamp repairs exactly the rows IT invalidated."""
    note = write_note(corpus, "video_id: VID\n", "body\n")
    cited = corpus / "docs" / "x.md"
    stale = rn.sha256(cited)
    cited.write_text(DOC + "an edit.\n", encoding="utf-8")
    side = sidecar(corpus, f"docs/x.md\t1\t{stale}\tThe first pillar\n")
    before = side.read_text(encoding="utf-8")
    defects, refreshed = rn.refresh_sidecar(corpus, note, only={"docs/other.md"})
    assert (defects, refreshed) == ([], 0)
    assert side.read_text(encoding="utf-8") == before


def test_a_row_whose_file_changed_and_whose_quote_survived_is_rewritten(corpus):
    note = write_note(corpus, "video_id: VID\n", "body\n")
    cited = corpus / "docs" / "x.md"
    stale = rn.sha256(cited)
    cited.write_text("a new opening line.\n" + DOC, encoding="utf-8")
    side = sidecar(corpus, f"docs/x.md\t1\t{stale}\tThe first pillar\n")
    defects, refreshed = rn.refresh_sidecar(corpus, note)
    assert (defects, refreshed) == ([], 1)
    assert rn.sha256(cited) in side.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# SIDECAR-UNREADABLE
# ---------------------------------------------------------------------------

def test_an_audit_file_with_an_undecodable_byte_is_not_no_audit(corpus):
    (corpus / "notes" / ".resolved" / "VID.tsv").write_bytes(
        b"docs/x.md\t1\tabc\t\xff\n")
    defects = rn.check_sidecar(corpus, "video_id: VID\n", REL)
    assert codes(defects) == ["E-SIDECAR-UNREADABLE"], defects
    assert "VID.tsv not valid UTF-8" in defects[0], defects


@not_as_root
def test_an_audit_file_the_process_may_not_open_is_reported(corpus):
    side = sidecar(corpus, "docs/x.md\t1\tabc\tq\n")
    os.chmod(side, 0o000)
    try:
        defects = rn.check_sidecar(corpus, "video_id: VID\n", REL)
    finally:
        os.chmod(side, 0o644)
    assert codes(defects) == ["E-SIDECAR-UNREADABLE"], defects
    assert "Permission denied" in defects[0], defects


def test_the_refresh_path_returns_quietly_on_an_unreadable_audit(corpus):
    """A neighbour named because the two paths disagree: the check refuses and
    the refresh says nothing at all."""
    note = write_note(corpus, "video_id: VID\n", "body\n")
    (corpus / "notes" / ".resolved" / "VID.tsv").write_bytes(b"\xff\n")
    assert rn.refresh_sidecar(corpus, note) == ([], 0)
    assert codes(rn.check_sidecar(corpus, "video_id: VID\n", REL)) == [
        "E-SIDECAR-UNREADABLE"]


def test_an_empty_audit_file_reads_fine(corpus):
    sidecar(corpus, "")
    assert rn.check_sidecar(corpus, "video_id: VID\n", REL) == []


# ---------------------------------------------------------------------------
# SIDECAR-ORPHAN
# ---------------------------------------------------------------------------

def _corpus_with_two_notes(root: Path) -> tuple[Path, Path]:
    a = write_note(root, "video_id: AAA\n", "body\n", name="2026-08-20--a--AAA.md")
    b = write_note(root, "video_id: BBB\n", "body\n", name="2026-08-20--b--BBB.md")
    for vid in ("AAA", "BBB"):
        sidecar(root, "# note\n", video_id=vid)
    return a, b


def test_an_audit_left_behind_after_its_video_id_changed_is_orphaned(corpus):
    """Renaming the field silently detaches the audit; this is what notices."""
    a, b = _corpus_with_two_notes(corpus)
    sidecar(corpus, "# note\n", video_id="WASBBB")
    defects = rn.orphan_sidecars(corpus, [a, b])
    assert defects == ["notes/.resolved/WASBBB.tsv:1 E-SIDECAR-ORPHAN no note "
                       "declares video_id WASBBB"], defects


def test_a_run_handed_one_note_still_reads_membership_from_the_corpus(corpus):
    """Membership is a fact about the corpus, so it is read from the corpus.

    Built from the handed-in files instead, `watch-audit <one-note.md>` --
    the invocation both documents prescribe -- declared every OTHER note's
    audit orphaned and exited 1 on a note that was clean.
    """
    a, _ = _corpus_with_two_notes(corpus)
    assert rn.orphan_sidecars(corpus, [a]) == []


def test_a_note_that_cannot_be_decoded_leaves_its_audit_reading_as_orphaned(corpus):
    """A skipped note is not a note that declared nothing, and the sweep
    cannot tell the two apart."""
    a, b = _corpus_with_two_notes(corpus)
    bad = corpus / "notes" / "2026-08-20--c--CCC.md"
    bad.write_bytes(b"---\nvideo_id: CCC\n---\n\xff\n")
    sidecar(corpus, "# note\n", video_id="CCC")
    defects = rn.orphan_sidecars(corpus, [a, b, bad])
    assert [d for d in defects if "CCC" in d], defects


def test_a_file_in_the_audit_directory_that_is_not_a_tsv_is_never_looked_at(corpus):
    a, b = _corpus_with_two_notes(corpus)
    (corpus / "notes" / ".resolved" / "notes.txt").write_text("x\n", encoding="utf-8")
    assert rn.orphan_sidecars(corpus, [a, b]) == []


# ---------------------------------------------------------------------------
# SET-UNCLOSED
# ---------------------------------------------------------------------------

def test_an_opened_block_with_members_and_no_close_is_refused(corpus):
    errors = set_errors("{{SET:things}}\n- a\n- b\n")
    assert errors[0] == "E-SET-UNCLOSED block never closes with {{/SET}}", errors


def test_a_close_written_in_lower_case_is_not_the_close(corpus):
    errors = set_errors("{{SET:things}}\n- a\n{{/set}}\n")
    assert "E-SET-UNCLOSED" in errors[0], errors


def test_the_scan_stops_at_an_unclosed_block_and_the_next_open_is_only_swept(corpus):
    """The strict scan breaks, so the second block is never examined as a set.

    The loose sweep still names it -- as malformed, which is not what it is.
    """
    errors = set_errors("{{SET:a}}\n- x\n\n{{SET:b}}\n- y\n")
    assert codes(errors) == ["E-SET-UNCLOSED", "E-SET-MALFORMED"], errors


def test_a_block_closed_immediately_is_empty_rather_than_unclosed(corpus):
    errors = set_errors("{{SET:things}}\n{{/SET}}\n")
    assert codes(errors) == ["E-SET-EMPTY"], errors


# ---------------------------------------------------------------------------
# SET-NESTED
# ---------------------------------------------------------------------------

def test_one_block_opened_inside_another_is_refused(corpus):
    errors = set_errors("{{SET:a}}\n{{SET:b}}\n- y\n{{/SET}}\n{{/SET}}\n")
    assert errors[0] == "E-SET-NESTED a set may not contain a set", errors


def test_two_blocks_side_by_side_do_not_nest(corpus):
    assert set_errors("{{SET:a}}\n- x\n{{/SET}}\n\n{{SET:b}}\n- y\n{{/SET}}\n") == []


def test_a_single_close_for_two_opens_is_handed_to_the_inner_block(corpus):
    """The scan restarts AT the inner open, so the one close closes the inner
    one and the outer block is reported nested rather than unclosed."""
    records = rn.find_sets("{{SET:a}}\n{{SET:b}}\n- y\n{{/SET}}\n", 1)
    assert [r.get("error", "") for r in records] == [
        "E-SET-NESTED a set may not contain a set", ""]
    assert records[1]["members"] == ["- y"]


def test_the_inner_block_is_examined_again_and_can_carry_its_own_defect(corpus):
    errors = set_errors("{{SET:a}}\n{{SET:3 things}}\n- y\n{{/SET}}\n{{/SET}}\n")
    assert codes(errors) == ["E-SET-NESTED", "E-SET-COUNT"], errors


# ---------------------------------------------------------------------------
# SET-EMPTY
# ---------------------------------------------------------------------------

def test_a_block_holding_only_a_blank_line_lists_no_members(corpus):
    errors = set_errors("{{SET:things}}\n\n{{/SET}}\n")
    assert errors == ["E-SET-EMPTY block lists no members"], errors


def test_a_block_holding_a_paragraph_and_no_list_items_lists_no_members(corpus):
    assert set_errors("{{SET:things}}\nsome prose about it\n{{/SET}}\n") == [
        "E-SET-EMPTY block lists no members"]


def test_a_block_holding_one_indented_list_item_has_a_member(corpus):
    assert set_errors("{{SET:things}}\n  - a\n{{/SET}}\n") == []


def test_a_block_whose_only_item_is_numbered_has_a_member(corpus):
    assert set_errors("{{SET:things}}\n1. a\n{{/SET}}\n") == []


# ---------------------------------------------------------------------------
# SET-MALFORMED
# ---------------------------------------------------------------------------

def test_the_set_keyword_followed_by_a_space_does_not_open_a_block(corpus):
    errors = set_errors("{{SET things}}\n- a\n{{/SET}}\n")
    assert errors == ['E-SET-MALFORMED token does not open as {{SET:label}}'], errors


def test_a_lower_case_set_keyword_is_read_only_by_the_loose_sweep(corpus):
    assert codes(set_errors("{{set:things}}\n- a\n{{/SET}}\n")) == ["E-SET-MALFORMED"]


def test_a_space_between_the_braces_and_the_set_keyword_is_malformed(corpus):
    assert codes(set_errors("{{ SET:things}}\n- a\n{{/SET}}\n")) == ["E-SET-MALFORMED"]


def test_a_stray_set_close_is_swept_for_by_neither_pass(corpus):
    """Named as a neighbour because it PASSES: nothing looks for a lone close."""
    assert set_errors("a sentence with {{/SET}} in it and nothing else\n") == []


# ---------------------------------------------------------------------------
# SET-COUNT, from the label
# ---------------------------------------------------------------------------

def test_a_label_containing_a_number_word_states_its_own_count(corpus):
    errors = set_errors("{{SET:three things}}\n- a\n{{/SET}}\n")
    assert 'the label states a count ("three things")' in errors[0], errors
    assert "from the 1 member line(s)" in errors[0], errors


def test_a_label_containing_a_digit_states_its_own_count(corpus):
    errors = set_errors("{{SET:3 things}}\n- a\n- b\n{{/SET}}\n")
    assert codes(errors) == ["E-SET-COUNT"], errors
    assert "from the 2 member line(s)" in errors[0], errors


def test_a_label_saying_every_claims_completeness_without_a_number(corpus):
    assert codes(set_errors("{{SET:every surface}}\n- a\n{{/SET}}\n")) == [
        "E-SET-COUNT"]


def test_a_label_whose_only_digits_sit_in_a_backticked_timestamp_is_not_a_count(
        corpus):
    """Reading an anchor's digits as a cardinality is the false red light this
    family is warned about."""
    assert set_errors("{{SET:moments at `[02:56]`}}\n- a\n{{/SET}}\n") == []


def test_a_label_saying_several_claims_a_size_and_is_not_refused(corpus):
    assert set_errors("{{SET:several things}}\n- a\n{{/SET}}\n") == []


def test_a_label_whose_only_number_is_a_year_is_refused_as_a_count(corpus):
    """The test is any digit outside backticks, not any cardinality.

    A year enumerates nothing, and the backticked-timestamp escape hatch is the
    only one on offer, so a bare number that is not a count is refused with a
    message that names a count.
    """
    errors = set_errors("{{SET:moments in 2026}}\n- a\n{{/SET}}\n")
    assert 'the label states a count ("moments in 2026")' in errors[0], errors
    assert "from the 1 member line(s)" in errors[0], errors


def test_a_label_saying_one_states_its_own_count_and_is_outside_the_vocabulary(
        corpus):
    """`one` is not in NUMBER_WORDS, so the narrowest count word of all passes.

    The block is well formed and has a member, so the label check is reached
    and declines -- this is the vocabulary's edge, not a block that never got
    that far.
    """
    sets = rn.find_sets("{{SET:one thing}}\n- a\n{{/SET}}\n", 1)
    assert [s["label"] for s in sets] == ["one thing"], sets
    assert [s["members"] for s in sets] == [["- a"]], sets
    assert [s.get("error") for s in sets] == [None], sets


# ---------------------------------------------------------------------------
# SET-COUNT, from prose
# ---------------------------------------------------------------------------

def test_a_paragraph_claiming_four_items_above_a_list_of_three(corpus):
    defects = rn.check_enumerations("There are four surfaces:\n\n- a\n- b\n- c\n",
                                    1, REL)
    assert defects == [f"{REL}:1 E-SET-COUNT declared four surfaces but 3 listed"], defects


def test_sibling_rows_are_not_the_members_of_a_claim_inside_a_list_row(corpus):
    """Five false positives across three recordings were all this shape: a
    claim row whose next rows were read as its members."""
    body = ("- the seller holds two mindsets: knowing and forgetting\n"
            "- another row\n- a third row\n- a fourth row\n")
    assert rn.check_enumerations(body, 1, REL) == []


def test_a_list_indented_under_a_claim_row_is_its_enumeration(corpus):
    body = ("- the seller holds two mindsets: knowing and forgetting\n"
            "  - a\n  - b\n  - c\n")
    defects = rn.check_enumerations(body, 1, REL)
    assert defects == [f"{REL}:1 E-SET-COUNT declared two mindsets but 3 listed"], defects


def test_a_comma_list_in_prose_with_no_markdown_list_is_not_counted(corpus):
    """Counting inline comma lists was built, run against the corpus, and cut:
    it fired four times and all four were false."""
    body = "There are three filters: speed, cost and reach.\n\nA paragraph follows.\n"
    assert rn.check_enumerations(body, 1, REL) == []


def test_a_cardinality_that_enumerates_nothing_is_not_counted(corpus):
    """A span of minutes or of years is not a set claim.

    What keeps it out is the colon: without one, `RE_ENUM` does not match at
    all. With a colon and a list under it, this check does not know the
    difference -- see the gap list in `spec/note-contract.toml`.
    """
    assert rn.check_enumerations("produced in under three minutes flat", 1, REL) == []
    assert rn.check_enumerations("roughly every five years he does this", 1, REL) == []


# ---------------------------------------------------------------------------
# BARE-PATH
# ---------------------------------------------------------------------------

def test_a_path_written_in_prose_under_an_ordinary_heading_is_untokenised(corpus):
    defects = rn.check_floor("## Notes\n\nsee docs/x.md for the wording\n", 1, REL)
    assert defects == [f"{REL}:3 E-BARE-PATH untokenised docs/x.md"], defects


def test_the_same_path_inside_a_citation_token_is_not_untokenised(corpus):
    body = '## Notes\n\n{{CITE:docs/x.md#"The first pillar"}}\n'
    assert rn.check_floor(body, 1, REL) == []


def test_a_path_already_recorded_in_the_audit_is_not_untokenised(corpus):
    """Rendering must not turn a checked citation back into an unchecked one."""
    body = "## Notes\n\nsee `docs/x.md` for the wording\n"
    assert rn.check_floor(body, 1, REL) != []
    assert rn.check_floor(body, 1, REL, {"docs/x.md"}) == []


def test_a_path_under_the_action_heading_names_a_file_to_change(corpus):
    """There is nothing in it yet to quote, so demanding a citation would be
    the false red light that gets the check switched off."""
    assert rn.check_floor("## Action\n\nedit docs/x.md next\n", 1, REL) == []


def test_a_three_hash_heading_named_action_does_not_set_the_section(corpus):
    """The exemption is keyed on an exact two-hash heading. The module
    docstring says only "exempt under `## Action`"."""
    defects = rn.check_floor("### Action\n\nedit docs/x.md next\n", 1, REL)
    assert codes(defects) == ["E-BARE-PATH"], defects


def test_the_action_exemption_persists_until_the_next_two_hash_heading(corpus):
    """The section is set by a two-hash heading and never cleared.

    A deeper heading does not end it, so one `## Action` exempts every matching
    path for the rest of the body; only the next two-hash heading takes the
    exemption away. A reader drawing sections by indent level would put the
    second path outside the exemption, and it is inside.
    """
    body = "## Action\n\nedit docs/a.md\n\n### Later\n\nsee docs/x.md here\n"
    assert rn.check_floor(body, 1, REL) == []

    ended = body.replace("### Later", "## Notes")
    assert ended.split("\n")[6] == "see docs/x.md here"
    assert rn.check_floor(ended, 1, REL) == [
        f"{REL}:7 E-BARE-PATH untokenised docs/x.md"], ended


def test_a_path_in_an_unlisted_directory_is_not_matched_at_all(corpus):
    """The pattern set is a judgement call and never provably complete."""
    assert rn.check_floor("## Notes\n\nsee src/x.md and docs/x.txt\n", 1, REL) == []


def test_a_bare_path_below_a_wrapped_token_is_reported_at_a_shifted_line(corpus):
    """Tokens are removed before the scan, and a token that wrapped takes its
    newlines with it: `docs/y.md` is on line 7 and is named at line 5."""
    body = ('## Notes\n\n{{CITE:docs/x.md\n#"A hard-wrapped\nsentence"}}\n\n'
            'see docs/y.md too\n')
    assert body.split("\n")[6] == "see docs/y.md too"
    defects = rn.check_floor(body, 1, REL)
    assert defects == [f"{REL}:5 E-BARE-PATH untokenised docs/y.md"], defects


# ---------------------------------------------------------------------------
# STATUS-MISSING
# ---------------------------------------------------------------------------

def test_frontmatter_with_no_status_field_at_all(corpus):
    defects = rn.check_status('duration: "10:00"\n', REL)
    assert defects == [f"{REL}:1 E-STATUS-MISSING no status: field"], defects


def test_a_two_word_status_value_reports_the_field_as_absent(corpus):
    """`(\\S*)` cannot span a space, so the row does not match and the field
    reads as missing rather than as unknown."""
    assert codes(rn.check_status("status: in progress\n", REL)) == ["E-STATUS-MISSING"]


def test_an_indented_status_field_is_not_at_the_left_margin(corpus):
    """YAML would read it; this parser refuses rather than guesses."""
    assert codes(rn.check_status("  status: applied\n", REL)) == ["E-STATUS-MISSING"]


def test_the_status_field_spelled_with_a_capital_letter(corpus):
    assert codes(rn.check_status("Status: applied\n", REL)) == ["E-STATUS-MISSING"]


def test_an_empty_status_value_is_not_reported_as_a_missing_field(corpus):
    """The direction this rule otherwise never states.

    `(\\S*)` matches nothing at all, so a field with an empty value IS read and
    the note is refused for an unknown value instead. Tightening the pattern to
    `(\\S+)` would move this note into this rule and nothing here would notice.
    """
    defects = rn.check_status("status:\n", REL)
    assert defects == [f"{REL}:1 E-STATUS-UNKNOWN status '' is not one of "
                       f"capture, distilled, applied, discarded"], defects


# ---------------------------------------------------------------------------
# STATUS-UNKNOWN
# ---------------------------------------------------------------------------

def test_a_plausible_word_outside_the_vocabulary(corpus):
    defects = rn.check_status("status: drafted\n", REL)
    assert codes(defects) == ["E-STATUS-UNKNOWN"], defects
    assert "'drafted' is not one of capture, distilled, applied, discarded" in defects[0]


def test_an_empty_status_value_reads_as_unknown_rather_than_missing(corpus):
    defects = rn.check_status("status:\n", REL)
    assert codes(defects) == ["E-STATUS-UNKNOWN"], defects
    assert "status ''" in defects[0], defects


def test_a_status_value_on_the_next_line_reads_as_empty(corpus):
    defects = rn.check_status("status:\napplied\n", REL)
    assert codes(defects) == ["E-STATUS-UNKNOWN"] and "status ''" in defects[0]


def test_a_correct_status_wrapped_in_quotation_marks_is_normalised(corpus):
    assert rn.check_status('status: "applied"\napplied: docs/x.md\n', REL,
                           corpus) == []


def test_a_correct_status_in_capitals_is_outside_a_case_sensitive_vocabulary(corpus):
    defects = rn.check_status("status: APPLIED\n", REL)
    assert codes(defects) == ["E-STATUS-UNKNOWN"] and "'APPLIED'" in defects[0]


# ---------------------------------------------------------------------------
# STATUS-APPLIED
# ---------------------------------------------------------------------------

def test_an_edge_named_while_the_status_says_distilled(corpus):
    defects = rn.check_status("status: distilled\napplied: docs/x.md\n", REL)
    assert defects == [f"{REL}:1 E-STATUS-APPLIED applied: names docs/x.md but "
                       f"status is distilled"], defects


def test_an_edge_named_while_the_status_says_discarded(corpus):
    defects = rn.check_status("status: discarded\napplied: docs/x.md\n", REL)
    assert codes(defects) == ["E-STATUS-APPLIED"] and "discarded" in defects[0]


def test_an_edge_named_while_the_status_says_capture(corpus):
    """The third of the three statuses that fire.

    The test is that the status is not `applied`, not that it is one of the two
    the row used to name, so a note that never claims to have been distilled at
    all is refused here too.
    """
    defects = rn.check_status("status: capture\napplied: docs/x.md\n", REL)
    assert defects == [f"{REL}:1 E-STATUS-APPLIED applied: names docs/x.md but "
                       f"status is capture"], defects


def test_an_edge_of_only_invisible_characters_normalises_to_empty(corpus):
    """U+200B survived `.strip()` and made an empty field into a named edge."""
    assert rn.check_status("status: distilled\napplied: ​­\n", REL) == []


def test_an_edge_value_on_the_next_line_is_not_the_next_fields_value(corpus):
    """`applied:\\s*(.*)$` read the line below and reported every note as
    applying whatever field came next."""
    defects = rn.check_status("status: applied\napplied:\nrating: 4\n", REL)
    assert codes(defects) == ["E-STATUS-NOEDGE"], defects


# ---------------------------------------------------------------------------
# STATUS-NOEDGE
# ---------------------------------------------------------------------------

def test_an_applied_note_with_a_blank_edge_field_claims_an_edge_it_lacks(corpus):
    defects = rn.check_status("status: applied\napplied:\n", REL)
    assert defects == [f"{REL}:1 E-STATUS-NOEDGE status is applied but "
                       f"applied: is empty"], defects


def test_yamls_own_word_for_nothing_is_a_value_here_not_an_empty_field(corpus):
    """The draft expected this rule to fire and it does not.

    `header_value` normalises quoting and invisibles, not YAML's nulls, so
    `applied: null` is a value -- and it is the EDGE rule that refuses it,
    for naming a path that opens as nothing.
    """
    defects = rn.check_status("status: applied\napplied: null\n", REL, corpus)
    assert codes(defects) == ["E-STATUS-EDGE"], defects
    assert "names null" in defects[0], defects


def test_an_edge_field_holding_todo_is_not_empty(corpus):
    """Not refused by THIS rule. No string rule can make a value mean
    something; only resolving it can, which is the next rule down."""
    assert "E-STATUS-NOEDGE" not in " ".join(
        rn.check_status("status: applied\napplied: TODO\n", REL, corpus))


# ---------------------------------------------------------------------------
# STATUS-EDGE
# ---------------------------------------------------------------------------

def test_an_edge_that_names_no_file_anywhere_is_a_phantom(corpus, tmp_path,
                                                          monkeypatch):
    """A phantom path claims the same nonexistent edge as a blank field.

    The chdir is load-bearing, not tidiness: the first base this rule tries is
    the process's working directory, so run from a directory that happens to
    hold `docs/nope.md` this case fails on its own name. "Anywhere" is three
    named places and one of them is ambient.
    """
    monkeypatch.chdir(tmp_path)
    defects = rn.check_status("status: applied\napplied: docs/nope.md\n", REL, corpus)
    assert codes(defects) == ["E-STATUS-EDGE"], defects
    assert "names docs/nope.md" in defects[0], defects


def test_an_edge_that_opens_only_from_the_directory_the_command_was_run_in_resolves(
        corpus, tmp_path, monkeypatch):
    """The first base is the PROCESS's working directory.

    Neither the repository nor its parent holds this file, so the only thing
    that resolves it is where the operator stood when the gate was invoked --
    and the same note is a phantom one directory up.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "planted.md").write_text("an outbound edge\n", encoding="utf-8")
    frontmatter = "status: applied\napplied: planted.md\n"

    monkeypatch.chdir(elsewhere)
    assert rn.applied_target(corpus, "planted.md") == Path("planted.md")
    assert rn.check_status(frontmatter, REL, corpus) == []

    monkeypatch.chdir(tmp_path)
    assert rn.applied_target(corpus, "planted.md") is None
    assert codes(rn.check_status(frontmatter, REL, corpus)) == ["E-STATUS-EDGE"]


def test_an_edge_that_opens_only_beside_the_note_is_still_a_phantom(
        corpus, tmp_path, monkeypatch):
    """The note's own directory is not one of the three bases.

    The message says "from the note", and a file sitting next to the note is
    refused anyway -- so the one place a reader would look first is the one
    place the resolver never tries.
    """
    monkeypatch.chdir(tmp_path)
    sibling = corpus / "notes" / "sibling.md"
    sibling.write_text("beside the note\n", encoding="utf-8")
    assert sibling.is_file()

    defects = rn.check_status("status: applied\napplied: sibling.md\n", REL, corpus)
    assert codes(defects) == ["E-STATUS-EDGE"], defects
    assert "names sibling.md" in defects[0], defects


def test_an_edge_written_from_the_repository_root_resolves(corpus):
    assert rn.check_status("status: applied\napplied: docs/x.md\n", REL, corpus) == []


def test_an_edge_resolved_from_the_repositorys_parent_resolves(corpus):
    """The corpus's second convention: the value starts with the repo's own
    directory name, so it resolves against the repo's PARENT."""
    value = f"{corpus.name}/docs/x.md"
    assert rn.applied_target(corpus, value) is not None
    assert rn.check_status(f"status: applied\napplied: {value}\n", REL, corpus) == []


def test_an_edge_naming_the_repository_resolves_after_the_repository_moves(
        corpus, tmp_path, monkeypatch):
    """The corpus does not survive being copied to another directory.

    A note names `<repo>/docs/x.md`, which resolved only because the checkout's
    basename happened to be that word: the base is the repo's PARENT, and the
    parent plus that name is the repo again. Copy the corpus anywhere else and
    the same note is a phantom -- two of them are, on the real corpus, and every
    exit-0 line in the review chain was measured in the one directory where the
    coincidence holds.

    The leading component is the repository as the NOTE names it, which is not
    the same fact as what the directory is called today. Dropping the fourth
    base makes the relocated assertion red and leaves the rest green.
    """
    # A DIFFERENT PARENT as well as a different name: with the original still
    # sitting beside it, the repo's-parent base finds that one and the case
    # passes for a reason that has nothing to do with the copy.
    far = tmp_path / "far"
    far.mkdir()
    moved = far / "somewhere-else"
    shutil.copytree(corpus, moved)
    monkeypatch.chdir(far)
    value = f"{corpus.name}/docs/x.md"

    assert rn.applied_target(moved, value) is not None
    assert rn.check_status(f"status: applied\napplied: {value}\n", REL, moved) == []
    # And the strip is not a licence: a file that is under no base at all is
    # still a phantom, whatever it is prefixed with.
    assert rn.applied_target(moved, f"{corpus.name}/docs/absent.md") is None


def test_an_edge_starting_with_a_tilde_is_expanded_before_it_is_looked_for(
        corpus, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "edge.md").write_text("an outbound edge\n", encoding="utf-8")
    assert rn.check_status("status: applied\napplied: ~/edge.md\n", REL, corpus) == []
    assert codes(rn.check_status("status: applied\napplied: ~/absent.md\n", REL,
                                 corpus)) == ["E-STATUS-EDGE"]


def test_a_phantom_edge_checked_with_no_corpus_root_resolves_nothing(corpus):
    """Without a root there is nothing to resolve against, so the rule is off."""
    assert rn.check_status("status: applied\napplied: docs/nope.md\n", REL,
                           None) == []


def test_an_edge_naming_a_directory_exists_and_is_accepted(corpus):
    """`exists()`, not `is_file()`: a directory is an edge as far as this goes."""
    assert rn.check_status("status: applied\napplied: docs\n", REL, corpus) == []


# ---------------------------------------------------------------------------
# GRADE-UNSTAMPED
# ---------------------------------------------------------------------------

def test_a_note_with_no_graded_with_field_attributes_its_pass_to_nothing(corpus):
    defects = rn.check_grade('duration: "10:00"\n', REL)
    assert codes(defects) == ["E-GRADE-UNSTAMPED"], defects
    assert rn.current_stamp() in defects[0], defects


def test_a_graded_with_field_with_nothing_after_the_colon(corpus):
    assert codes(rn.check_grade("graded_with:\n", REL)) == ["E-GRADE-UNSTAMPED"]


def test_a_graded_with_record_written_with_a_space_is_reported_as_absent(corpus):
    """`(\\S*)` cannot span a space, so a record that IS there reads as absent.

    The message then denies a line the file contains. Widening the pattern
    would move this note to the stale rule, which is where a reader would put
    it, and no other case here would notice.
    """
    defects = rn.check_grade("graded_with: watch quality@1\n", REL)
    assert codes(defects) == ["E-GRADE-UNSTAMPED"], defects
    assert "no graded_with:" in defects[0], defects


def test_a_graded_with_field_of_two_quotation_marks_is_treated_as_a_value(corpus):
    """This reader does NOT normalise quoting, so `""` is a build name and the
    note is reported stale rather than unstamped."""
    defects = rn.check_grade('graded_with: ""\n', REL)
    assert codes(defects) == ["E-GRADE-STALE"], defects
    assert 'passed by ""' in defects[0], defects


def test_a_run_where_the_distribution_is_not_installed_is_silent(monkeypatch):
    """Silent by design -- and that means the whole grade family is off
    wherever the package is not installed, including a source-tree CI image."""
    monkeypatch.setattr(rn, "DIST_NAME", "no-such-distribution-here")
    assert rn.current_stamp() is None
    assert rn.check_grade('duration: "10:00"\n', REL) == []


# ---------------------------------------------------------------------------
# GRADE-STALE
# ---------------------------------------------------------------------------

def test_a_record_naming_an_older_release_is_no_longer_comparable(corpus):
    defects = rn.check_grade("graded_with: watch-quality@0.0.1\n", REL)
    assert codes(defects) == ["E-GRADE-STALE"], defects
    assert "passed by watch-quality@0.0.1" in defects[0], defects


def test_a_record_with_a_trailing_zero_added_is_a_different_string(corpus):
    """Packaging calls the two the same release; this comparison does not."""
    stale = (rn.current_stamp() or "") + ".0"
    defects = rn.check_grade(f"graded_with: {stale}\n", REL)
    assert codes(defects) == ["E-GRADE-STALE"] and stale in defects[0]


def test_a_record_naming_a_different_distribution_at_the_right_version(corpus):
    _, _, version = (rn.current_stamp() or "").partition("@")
    defects = rn.check_grade(f"graded_with: some-other-dist@{version}\n", REL)
    assert codes(defects) == ["E-GRADE-STALE"], defects


def test_a_record_naming_exactly_the_installed_build(corpus):
    assert rn.check_grade(f"graded_with: {rn.current_stamp()}\n", REL) == []


# ---------------------------------------------------------------------------
# GRADE-EXEMPT -- the grade family may not withhold its own repair
# ---------------------------------------------------------------------------

def test_a_note_whose_only_defect_is_an_older_record_is_still_stamped(corpus):
    stamp = rn.current_stamp()
    note = write_note(corpus, FM_CLEAN + "graded_with: watch-quality@0.0.1\n",
                      "one two three\n")
    defects, written = rn.stamp_note(note, corpus, stamp, require_density=False,
                                     floor=False)
    assert (defects, written) == ([], True)
    assert f"graded_with: {stamp}" in note.read_text(encoding="utf-8")


def test_a_note_whose_only_defect_is_no_record_at_all_is_still_stamped(corpus):
    stamp = rn.current_stamp()
    note = write_note(corpus, FM_CLEAN, "one two three\n")
    assert codes(rn.check_note(note, corpus, False)[0]) == ["E-GRADE-UNSTAMPED"]
    assert rn.stamp_note(note, corpus, stamp, require_density=False,
                         floor=False) == ([], True)


def test_a_note_with_a_real_defect_and_a_stale_record_is_not_stamped(corpus):
    note = write_note(corpus,
                      FM_CLEAN.replace("distilled", "nonsense")
                      + "graded_with: watch-quality@0.0.1\n",
                      "one two three\n")
    before = note.read_text(encoding="utf-8")
    defects, written = rn.stamp_note(note, corpus, rn.current_stamp(),
                                     require_density=False, floor=False)
    assert written is False
    assert codes(defects) == ["E-STAMP-REFUSED"], defects
    assert note.read_text(encoding="utf-8") == before


def test_a_defect_line_that_merely_mentions_the_grade_prefix_is_dropped(corpus):
    """The filter is a substring match on the MESSAGE, not on the code.

    A bare path whose own name carries the prefix therefore disappears, and a
    note with an outstanding defect is stamped clean.
    """
    note = write_note(corpus, FM_CLEAN,
                      "## Notes\n\nsee docs/E-GRADE-note.md here\n")
    defects, _ = rn.check_note(note, corpus, False, floor=True)
    assert "E-BARE-PATH" in codes(defects), defects
    stamped = rn.stamp_note(note, corpus, rn.current_stamp(),
                            require_density=False, floor=True)
    assert stamped == ([], True)
    assert "graded_with:" in note.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# STAMP-REFUSED
# ---------------------------------------------------------------------------

def test_a_note_carrying_an_ordinary_defect_is_refused_and_left_untouched(corpus):
    note = write_note(corpus, FM_CLEAN.replace("distilled", "nonsense"),
                      "one two three\n")
    before = note.read_text(encoding="utf-8")
    defects, written = rn.stamp_note(note, corpus, rn.current_stamp(),
                                     require_density=False, floor=False)
    assert written is False
    assert "1 defect(s) outstanding" in defects[0], defects
    assert note.read_text(encoding="utf-8") == before


def test_a_note_declaring_no_density_is_graded_by_the_default_stamp(corpus):
    """The stamp has to grade with the checker `--check` grades with; it used
    to call for a weaker grade and write a clean bill onto a red note."""
    note = write_note(corpus, FM_CLEAN, "one two three\n")
    defects, written = rn.stamp_note(note, corpus, rn.current_stamp())
    assert written is False
    assert codes(defects) == ["E-STAMP-REFUSED"], defects


def test_a_note_already_carrying_this_exact_record_is_left_alone(corpus):
    stamp = rn.current_stamp()
    note = write_note(corpus, FM_CLEAN + f"graded_with: {stamp}\n",
                      "one two three\n")
    before = note.read_text(encoding="utf-8")
    assert rn.stamp_note(note, corpus, stamp, require_density=False,
                         floor=False) == ([], False)
    assert note.read_text(encoding="utf-8") == before


def test_stamping_one_note_makes_its_neighbour_stale_for_a_second_round(corpus):
    """One pass cannot finish the corpus.

    A note that cites another has that other note's sha256 in its audit, and
    a stamp rewrites the cited note's bytes -- so a note that was clean before
    the pass is red after it, and only a second round can reach it.
    """
    stamp = rn.current_stamp()
    fm_a = FM_CLEAN.replace("VID", "AAA")
    fm_b = FM_CLEAN.replace("VID", "BBB")
    b = write_note(corpus, fm_b, "beta body text\n", name="2026-08-20--b--BBB.md")
    a = write_note(corpus, fm_a,
                   'see {{CITE:notes/2026-08-20--b--BBB.md#"beta body"}}\n',
                   name="2026-08-20--a--AAA.md")
    assert rn.write_note(a, corpus) == ([], 1)
    assert codes(rn.check_note(a, corpus, False)[0]) == ["E-GRADE-UNSTAMPED"]

    assert rn.stamp_note(b, corpus, stamp, require_density=False,
                         floor=False) == ([], True)
    after = codes(rn.check_note(a, corpus, False)[0])
    assert "E-CITE-STALE" in after, after


# ---------------------------------------------------------------------------
# ANCHOR-RANGE
# ---------------------------------------------------------------------------

def test_a_timestamp_well_past_the_end_of_the_recording(corpus):
    defects = rn.check_anchors("see `[45:00]` here\n", 600, REL)
    assert defects == [f"{REL}:1 E-ANCHOR-RANGE 1 anchor(s) past the 600s "
                       f"runtime: 45:00"], defects


def test_a_timestamp_beside_a_reference_to_another_note_is_exempt(corpus):
    """Notes legitimately cite a second in a DIFFERENT recording."""
    assert rn.check_anchors("notes/other.md says\nsee `[45:00]`\n", 600, REL) == []


def test_a_timestamp_exactly_equal_to_the_runtime_is_the_boundary(corpus):
    assert rn.check_anchors("the last moment is `[10:00]`\n", 600, REL) == []


def test_a_timestamp_on_the_line_above_a_reference_is_not_exempt(corpus):
    """The window is forward-only -- the reference's own line and the two
    below it -- although the docstring says "near", which reads symmetric."""
    defects = rn.check_anchors("see `[45:00]`\nnotes/other.md says so\n", 600, REL)
    assert codes(defects) == ["E-ANCHOR-RANGE"], defects


def test_an_unparseable_runtime_returns_before_any_timestamp_is_read(corpus):
    defects = check(corpus, 'duration: "1:75:00"\nstatus: distilled\n',
                    "see `[45:00]` here\n")
    assert "E-DURATION" in codes(defects)
    assert "E-ANCHOR-RANGE" not in codes(defects), defects


# ---------------------------------------------------------------------------
# BAND
# ---------------------------------------------------------------------------

def test_a_band_declared_far_from_the_computed_one(corpus):
    defects = rn.check_band("inside the 42-121 wpm peer band\n", (10.0, 20.0), REL)
    assert defects == [f"{REL}:1 E-BAND declared 42-121 computed 10-20"], defects


def test_a_band_within_a_rounding_step_of_the_computed_one(corpus):
    assert rn.check_band("inside the 42-121 wpm peer band\n", (42.4, 120.6), REL) == []


def test_a_corpus_with_no_countable_rate_has_no_band_to_check(corpus):
    """No note has a rate, so there is no band, and nothing is compared."""
    assert rn.check_band("inside the 42-121 wpm peer band\n", None, REL) == []


def test_a_band_declared_with_its_ends_the_wrong_way_round(corpus):
    """Compared end for end, so it is refused -- for the wrong reason."""
    defects = rn.check_band("inside the 121-42 wpm peer band\n", (42.0, 121.0), REL)
    assert defects == [f"{REL}:1 E-BAND declared 121-42 computed 42-121"], defects


def test_a_band_inside_a_quotation_is_still_read_as_this_notes_claim(corpus):
    """The body is one flattened string, so a blockquote is not a shield."""
    body = "> the other note says the 42-121 wpm peer band\n"
    assert codes(rn.check_band(body, (10.0, 20.0), REL)) == ["E-BAND"]
