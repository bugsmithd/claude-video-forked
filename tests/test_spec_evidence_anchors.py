"""Layer 2, anchors and manifests: the neighbours nothing was pinning.

`spec/evidence-anchors.toml` says what each `anchor_manifest` rule REFUSES and
names the wrong answers next door to it. A neighbour with no case is a hole, so
this file is where the holes got filled: every case here is one row of that
table, and `tests/test_spec_conformance.py` fails if a row names a function
that is not in this file.

Two habits the reviews asked for, kept literally:

* drive the real function. Nothing here re-derives a rule and compares it with
  itself -- `check_note`, `read_manifest`, `sweep_manifests`, `verify_copy`,
  `verify_artifacts`, `adopt_run` and `main` are called as a caller calls them.
* assert the code AND something a wrong implementation would get wrong: the
  second in the message, the count, the file it names. `assert defects` passes
  for the wrong reason.

Roughly half of these cases are the direction that gets forgotten -- the
neighbour that must NOT fire. A rule with only positive cases is half
specified, and the false-positive direction is where a gate stops being read.

The fixtures are invented. No corpus path, video id or note is named here.
"""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest

from watchquality import anchor_manifest as am
from watchquality.resolve_note import sha256


VIDEO_ID = "SPECL2VID"
OTHER_ID = "SPECL2ALT"


# --------------------------------------------------------------------------
# fixture builders
# --------------------------------------------------------------------------

def _note_text(body: str, video_id: str, duration: str | None) -> str:
    dur = f'duration: "{duration}"\n' if duration is not None else ""
    return (f"---\nvideo_id: {video_id}\n{dur}---\n\n"
            f"# a fixture note\n\n{body}\n")


def _write_note(root: Path, body: str, video_id: str = VIDEO_ID,
                duration: str | None = "10:00") -> Path:
    notes = root / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    path = notes / f"2026-01-01--fixture--{video_id}.md"
    path.write_text(_note_text(body, video_id, duration), encoding="utf-8")
    return path


def _manifest_text(rows, declared: str | None = VIDEO_ID,
                   duration: str | None = "600", namespaces: str = "frames",
                   sources=()) -> str:
    head = [] if declared is None else [f"# video_id\t{declared}"]
    if duration is not None:
        head.append(f"# duration\t{duration}")
    head.append(f"# namespaces\t{namespaces}")
    head += [f"# source\t{s}" for s in sources]
    return "\n".join(head + [am.MANIFEST_HEADER] + list(rows)) + "\n"


def _write_manifest(root: Path, rows, video_id: str = VIDEO_ID, **kw) -> Path:
    anchors = root / "notes" / "anchors"
    anchors.mkdir(parents=True, exist_ok=True)
    path = anchors / f"{video_id}.tsv"
    path.write_text(_manifest_text(rows, **kw), encoding="utf-8")
    return path


def _write_caption(root: Path, text: str, video_id: str = VIDEO_ID) -> Path:
    captions = root / am.CAPTION_DIR
    captions.mkdir(parents=True, exist_ok=True)
    path = captions / f"{video_id}.tsv"
    path.write_text(text, encoding="utf-8")
    return path


def _frame(second: int, name: str | None = None, sha: str | None = None) -> str:
    name = name or f"frame_{second:04d}.jpg"
    return f"frame\t{second}\t{second}\t{name}\t{sha or f'sha-{second}'}"


def _cue(start: float, end: float, index: int = 0) -> str:
    return f"cue\t{start:.3f}\t{end:.3f}\tcue_{index:04d}\t-"


def _named(defects, code: str) -> list[str]:
    """Every defect line carrying this code. The count is part of the claim."""
    return [d for d in defects if code in d]


def _one(defects, code: str) -> str:
    hits = _named(defects, code)
    assert len(hits) == 1, (code, defects)
    return hits[0]


def _run_dir(base: Path, files: dict[str, bytes]) -> Path:
    for rel, blob in files.items():
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    return base


# --------------------------------------------------------------------------
# E-ANCHOR-UNRESOLVED
# --------------------------------------------------------------------------

def test_an_inferred_second_outside_every_caption_span_is_unresolved(tmp_path):
    """The SPOKEN branch has a case; INFERRED is a separate line of code.

    The namespace has to be able to fail for either to be checked at all, so
    the cues here cover a tenth of the runtime rather than the whole of it.
    """
    note = _write_note(tmp_path, "- `[02:00]` `INFERRED` the deck moves on.")
    _write_manifest(tmp_path, [_frame(10), _cue(0.0, 60.0)],
                    namespaces="frames,cues")

    defects, row = am.check_note(note, tmp_path)

    line = _one(defects, "E-ANCHOR-UNRESOLVED")
    assert "`[02:00]` INFERRED falls in no caption span" in line
    assert VIDEO_ID in line
    assert (row["unresolved"], row["unbound-inferred"]) == (1, 0)


def test_the_unresolvable_runs_ledger_does_not_excuse_an_unresolved_anchor(
        tmp_path, monkeypatch):
    """That ledger downgrades the missing-manifest complaint and nothing else.

    A manifest that exists and does not hold the cited second is a defect
    whatever the ledger says, or the ledger becomes a blanket switch.
    """
    monkeypatch.setitem(am.UNRESOLVABLE_RUNS, VIDEO_ID,
                        "the run directory is gone and cannot be rebuilt")
    note = _write_note(tmp_path, "- `[00:20]` `ON-SCREEN` the second slide.")
    _write_manifest(tmp_path, [_frame(10)])

    defects, _ = am.check_note(note, tmp_path)

    line = _one(defects, "E-ANCHOR-UNRESOLVED")
    assert "nearest frame 00:10, 10s away" in line
    assert not _named(defects, "E-MANIFEST-MISSING")


def test_a_caption_span_over_an_on_screen_second_does_not_resolve_it(tmp_path):
    """The refusal is decided one class at a time, not from both halves at once.

    Read as a conjunction -- no frame AND no caption span -- the rule would let
    a caption span excuse an on-screen citation, and a note could claim to have
    seen a slide because somebody was talking over it. The cue namespace here
    is real and can fail, so the span is a live answer being ignored on
    purpose, and every other rule in the fixture is satisfied: the manifest
    declares this video and this runtime, both namespaces are declared and
    populated, and the anchor carries a class of its own.
    """
    note = _write_note(tmp_path, "- `[00:20]` `ON-SCREEN` the second slide.")
    _write_manifest(tmp_path, [_frame(10), _cue(0.0, 60.0)],
                    namespaces="frames,cues")

    defects, row = am.check_note(note, tmp_path)

    assert am.in_cue(20, am.read_manifest(
        tmp_path / "notes" / "anchors" / f"{VIDEO_ID}.tsv").cues)
    line = _one(defects, "E-ANCHOR-UNRESOLVED")
    assert "`[00:20]` ON-SCREEN is not an extracted frame" in line
    assert "nearest frame 00:10, 10s away" in line
    assert (row["resolved"], row["unresolved"]) == (0, 1)
    assert len(defects) == 1


# --------------------------------------------------------------------------
# E-ANCHOR-UNQUOTED
# --------------------------------------------------------------------------

def test_a_timestamp_backticked_on_one_side_only_is_still_invisible(tmp_path):
    """Half-quoted is not quoted: the anchor pattern needs both backticks.

    The pair is what the scan skips, so one of them has to leave the stamp
    counted nowhere -- which is the whole reason this wider scan exists.
    """
    note = _write_note(tmp_path, "- half quoted `[04:06] in ordinary prose.")
    _write_manifest(tmp_path, [_frame(246)])

    defects, row = am.check_note(note, tmp_path)

    line = _one(defects, "E-ANCHOR-UNQUOTED")
    assert "[04:06] is timestamp-shaped but carries no backticks" in line
    assert row["anchors"] == 0


def test_a_three_digit_minute_is_caught_by_the_wider_scan(tmp_path):
    """`RE_ANCHOR` caps minutes at two digits, so `[105:18]` is invisible to it.

    Invisible is worse than wrong: nothing counts the stamp and the note reads
    clean. This scan is deliberately wider on exactly that axis.
    """
    note = _write_note(tmp_path, "- a long talk, [105:18] in ordinary prose.")
    _write_manifest(tmp_path, [_frame(10)])

    defects, row = am.check_note(note, tmp_path)

    assert "[105:18] is timestamp-shaped" in _one(defects, "E-ANCHOR-UNQUOTED")
    assert row["anchors"] == 0


def test_the_second_of_two_adjacent_bare_stamps_is_offered_to_nothing(tmp_path):
    """The boundary of the wider scan, written down because it points the wrong
    way: a reader who trusts "every bare stamp is caught" writes a range with
    no separator and reads the silence as a pass.

    `RE_LOOSE_STAMP` ends in an optional trailing character, so each match eats
    one character past its closing bracket and the scan resumes after it. Write
    three stamps hard against each other and the first and third are refused
    while the middle one is offered to nothing -- and it is not an anchor
    either, because that pattern needs backticks. This case pins the hole as it
    stands, so closing it in the source has to come past a red line here.
    """
    note = _write_note(tmp_path, "- the range [00:10][00:20][00:30] in prose.")
    _write_manifest(tmp_path, [_frame(10)])

    defects, row = am.check_note(note, tmp_path)

    lines = _named(defects, "E-ANCHOR-UNQUOTED")
    assert len(lines) == 2, lines
    assert "[00:10] is timestamp-shaped" in lines[0]
    assert "[00:30] is timestamp-shaped" in lines[1]
    assert not [d for d in defects if "00:20" in d]
    assert row["anchors"] == 0


# --------------------------------------------------------------------------
# E-ANCHOR-UNATTRIBUTED
# --------------------------------------------------------------------------

def test_two_labels_touching_one_anchor_are_counted_as_contested(tmp_path):
    """CONTESTED and MULTI roll up to one disposition and are different work.

    "nobody labelled this" and "the tool could not choose" are separate piles,
    and the printed line has to say which one this note is in.
    """
    note = _write_note(tmp_path, "- `ON-SCREEN` `[00:10]` `SPOKEN`")
    _write_manifest(tmp_path, [_frame(10)])

    defects, row = am.check_note(note, tmp_path)

    line = _one(defects, "E-ANCHOR-UNATTRIBUTED")
    assert "(0 unlabelled, 0 labelled but untouched, 1 contested)" in line
    assert row["unattributable"] == 1


def test_a_contested_anchor_with_a_donor_elsewhere_is_cross_referenced(tmp_path):
    """Which of the two codes a contested anchor earns is decided by another
    line entirely, and neither rule said so.

    The item is the same one the case above uses -- two labels, both touching
    the anchor -- with one earlier line labelling that second once. That single
    label sends the anchor to the cross-reference rule, so the unattributed
    complaint never fires and the contested counter it prints reads zero for
    this note. The cross-reference line then calls an anchor with two labels
    unlabelled, which is the wrong repair to hand a reader; it is asserted
    verbatim here so that changing it has to come past a red case.
    """
    note = _write_note(
        tmp_path,
        "- `[00:10]` `ON-SCREEN` the opening slide.\n\n"
        "- `ON-SCREEN` `[00:10]` `SPOKEN`")
    _write_manifest(tmp_path, [_frame(10)])

    defects, row = am.check_note(note, tmp_path)

    assert (row["cross-referenced"], row["unattributable"]) == (1, 0)
    assert not _named(defects, "E-ANCHOR-UNATTRIBUTED")
    line = _one(defects, "E-ANCHOR-CROSSREF")
    assert "1 anchor(s) carry no class of their own and lean on another "\
           "line's label for the same second" in line
    assert len(defects) == 1


def test_an_item_labelled_twice_and_touching_neither_label_is_cross_referenced(
        tmp_path):
    """The third shape through the same branch, and a separate word in it.

    Here the two labels are in the item and neither is next to the anchor, so
    the tool has two candidates and no way to pick. With a donor elsewhere it
    lands in the cross-referenced pile exactly as the contested shape does --
    which is why the trigger cannot say "carries no class of its own".
    """
    note = _write_note(
        tmp_path,
        "- `[00:10]` `ON-SCREEN` the opening slide.\n\n"
        "- `ON-SCREEN` and `SPOKEN` in turn, and a while later "
        "`[00:10]` lands.")
    _write_manifest(tmp_path, [_frame(10)])

    defects, row = am.check_note(note, tmp_path)

    assert (row["cross-referenced"], row["unattributable"]) == (1, 0)
    assert not _named(defects, "E-ANCHOR-UNATTRIBUTED")
    assert "1 anchor(s) carry no class of their own" in _one(
        defects, "E-ANCHOR-CROSSREF")
    assert len(defects) == 1


# --------------------------------------------------------------------------
# E-ANCHOR-CROSSREF
# --------------------------------------------------------------------------

def test_the_unresolvable_runs_ledger_does_not_excuse_a_leaned_on_anchor(
        tmp_path, monkeypatch):
    """A re-citation is a defect about the NOTE, not about the run directory."""
    monkeypatch.setitem(am.UNRESOLVABLE_RUNS, VIDEO_ID,
                        "the run directory is gone and cannot be rebuilt")
    note = _write_note(
        tmp_path,
        "- `[00:10]` `ON-SCREEN` the opening slide.\n\n"
        "- and back at `[00:10]` the point lands.")
    _write_manifest(tmp_path, [_frame(10)])

    defects, row = am.check_note(note, tmp_path)

    line = _one(defects, "E-ANCHOR-CROSSREF")
    assert "1 anchor(s) carry no class of their own" in line
    assert row["cross-referenced"] == 1


# --------------------------------------------------------------------------
# E-COVERAGE-GAP
# --------------------------------------------------------------------------

def test_a_span_exactly_as_long_as_the_ceiling_is_not_a_coverage_defect(tmp_path):
    """The comparison is strict, and a ceiling nobody can sit exactly on is a
    ceiling that means one second less than it says."""
    note = _write_note(tmp_path, "- `[00:10]` `ON-SCREEN` the opening slide.")
    _write_manifest(tmp_path, [_frame(10), _frame(70)])

    _, row = am.check_note(note, tmp_path)
    assert row["longest_gap"] == 590

    at_ceiling, _ = am.check_note(note, tmp_path, max_gap=590)
    over_ceiling, _ = am.check_note(note, tmp_path, max_gap=589)

    assert not _named(at_ceiling, "E-COVERAGE-GAP")
    assert "590s (00:10-10:00)" in _one(over_ceiling, "E-COVERAGE-GAP")


def test_a_spoken_anchor_on_an_uncited_frames_second_does_not_clear_the_gap(
        tmp_path):
    """Only FRAME citations count. A note may cover the runtime in SPOKEN
    anchors and still never have looked at the screen, which is the defect.

    The spoken anchor here is fully resolved -- it sits inside a caption span
    of a namespace that can fail -- and it sits on the second of the frame
    nobody cited. Everything about it says covered except the one thing this
    ledger measures.
    """
    note = _write_note(
        tmp_path,
        "- `[00:10]` `ON-SCREEN` the opening slide.\n\n"
        "- `[01:10]` `SPOKEN` and the speaker says so.")
    _write_manifest(tmp_path, [_frame(10), _frame(70), _cue(0.0, 120.0)],
                    namespaces="frames,cues")

    defects, row = am.check_note(note, tmp_path, max_gap=1)

    assert row["resolved"] == 2
    assert (row["cited_frames"], row["uncited_frames"]) == (1, 1)
    assert "1 of 2 frames are cited nowhere" in _one(defects, "E-COVERAGE-GAP")


def test_six_anchors_on_one_second_count_as_one_cited_frame(tmp_path):
    """Citations are a set of seconds. Counting them as a list would let a note
    buy coverage by repeating itself."""
    body = "\n\n".join(f"- `[00:10]` `ON-SCREEN` reading {i} of the slide."
                       for i in range(6))
    note = _write_note(tmp_path, body)
    _write_manifest(tmp_path, [_frame(10), _frame(70)])

    defects, row = am.check_note(note, tmp_path, max_gap=1)

    assert (row["anchors"], row["resolved"]) == (6, 6)
    assert (row["cited_frames"], row["uncited_frames"]) == (1, 1)
    assert "1 of 2 frames are cited nowhere" in _one(defects, "E-COVERAGE-GAP")


# --------------------------------------------------------------------------
# E-PARTITION
# --------------------------------------------------------------------------

def test_a_note_with_no_anchors_still_partitions(tmp_path):
    """Zero equals zero. An empty sum that reports a defect turns the check off
    for every note that happens to cite nothing."""
    note = _write_note(tmp_path, "Prose that cites no second at all.")
    _write_manifest(tmp_path, [_frame(10)])

    defects, row = am.check_note(note, tmp_path)

    assert row["anchors"] == 0
    assert not _named(defects, "E-PARTITION")


def test_a_pile_added_and_never_assigned_is_not_a_partition_defect(
        tmp_path, monkeypatch):
    """The draft expected this to fire. It does not, and the reason is worth
    pinning: an unassigned member contributes zero to the sum, so a disposition
    added to the list and wired to nothing is invisible here."""
    monkeypatch.setattr(am, "DISPOSITIONS", am.DISPOSITIONS + ("nowhere",))
    note = _write_note(tmp_path, "- `[00:10]` `ON-SCREEN` the opening slide.")
    _write_manifest(tmp_path, [_frame(10)])

    defects, row = am.check_note(note, tmp_path)

    assert row["anchors"] == 1
    assert not _named(defects, "E-PARTITION")


def test_a_pile_spliced_in_over_a_report_column_breaks_the_partition(
        tmp_path, monkeypatch):
    """The edit this rule does catch: a member spliced into the disposition list
    that names a column already counting something else.

    `blanket` is a report column, not a disposition, and it counts anchors that
    already landed in a pile -- so summing it in double-counts them and the
    anchor total stops matching.
    """
    monkeypatch.setattr(am, "DISPOSITIONS", am.DISPOSITIONS + ("blanket",))
    note = _write_note(
        tmp_path, "- Continuity, all `ON-SCREEN`: `[00:10]` and `[00:20]`.")
    _write_manifest(tmp_path, [_frame(10), _frame(20)])

    defects, row = am.check_note(note, tmp_path)

    assert (row["anchors"], row["blanket"]) == (2, 2)
    assert "2 anchors do not sum to their dispositions" in _one(
        defects, "E-PARTITION")


# --------------------------------------------------------------------------
# E-MANIFEST-MISSING
# --------------------------------------------------------------------------

def test_a_run_in_the_unresolvable_runs_ledger_is_printed_rather_than_refused(
        tmp_path, monkeypatch):
    """The one rule that ledger excuses -- and the downgrade is to a PRINTED
    line carrying the dated reason, not to silence."""
    reason = "the run directory went before the manifest was built"
    monkeypatch.setitem(am.UNRESOLVABLE_RUNS, VIDEO_ID, reason)
    note = _write_note(tmp_path, "- `[00:20]` `ON-SCREEN` the second slide.")

    defects, row = am.check_note(note, tmp_path)
    printed = am.summarise([row], tmp_path)

    assert not _named(defects, "E-MANIFEST-MISSING")
    assert (row["unchecked"], row["manifest"]) == (1, "missing")
    assert any(f"[exempt: {reason}]" in line for line in printed), printed


def test_a_note_with_no_on_screen_anchors_needs_no_manifest(tmp_path):
    """Nothing went unchecked, so nothing is owed. This is the false-positive
    direction: demanding a manifest from a note that cites no frame is how the
    requirement gets switched off wholesale."""
    note = _write_note(tmp_path, "- `[00:20]` `SPOKEN` the speaker says so.")

    defects, row = am.check_note(note, tmp_path)

    assert row["unchecked"] == 0
    assert not _named(defects, "E-MANIFEST-MISSING")


# --------------------------------------------------------------------------
# E-MANIFEST-UNREADABLE
# --------------------------------------------------------------------------

@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root can read a mode-000 file")
def test_a_manifest_the_process_cannot_open_is_reported_not_raised(tmp_path):
    """A permission error used to abort the run. A checker that dies on bad
    input is worse than one that names the bad input."""
    path = tmp_path / f"{VIDEO_ID}.tsv"
    path.write_text(_manifest_text([_frame(10)]), encoding="utf-8")
    path.chmod(0o000)
    try:
        man = am.read_manifest(path)
    finally:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert man.fatal is True
    line = _one(man.defects, "E-MANIFEST-UNREADABLE")
    assert line.startswith(f"notes/anchors/{VIDEO_ID}.tsv:1 ")
    assert "Permission denied" in line


def test_a_manifest_holding_bytes_that_are_not_utf8_names_the_byte(tmp_path):
    """The offset is the actionable part: "unreadable" alone sends the reader
    looking at the whole file."""
    path = tmp_path / f"{VIDEO_ID}.tsv"
    path.write_bytes(b"# video_id\t" + VIDEO_ID.encode() + b"\n\xff\xfe rows\n")

    man = am.read_manifest(path)

    assert man.fatal is True
    assert "not valid UTF-8 at byte 21" in _one(man.defects,
                                                "E-MANIFEST-UNREADABLE")


def test_a_manifest_path_that_is_a_directory_is_unreadable(tmp_path):
    """A directory where a file was expected reads back as an OS error, and the
    reader has to survive it the same way."""
    path = tmp_path / f"{VIDEO_ID}.tsv"
    path.mkdir()

    man = am.read_manifest(path)

    assert man.fatal is True
    assert "Is a directory" in _one(man.defects, "E-MANIFEST-UNREADABLE")


def test_an_unreadable_manifest_stops_the_video_and_duration_checks(tmp_path):
    """Reading fatally is meant to STOP the comparisons downstream of it.

    Nothing was decoded, so `video_id` is None and `duration` is zero -- and
    reporting those as a wrong video and a missing runtime would bury the one
    complaint that is true under two that are artefacts of it.
    """
    note = _write_note(tmp_path, "- `[00:10]` `ON-SCREEN` the opening slide.")
    manifest = _write_manifest(tmp_path, [_frame(10)])
    manifest.write_bytes(b"\xff\xfe\n")

    defects, row = am.check_note(note, tmp_path)

    assert _named(defects, "E-MANIFEST-UNREADABLE")
    assert not _named(defects, "E-MANIFEST-VIDEO")
    assert not _named(defects, "E-MANIFEST-DURATION")
    assert not _named(defects, "E-MANIFEST-NO-DURATION")
    assert (row["manifest"], row["unchecked"]) == ("present", 1)


# --------------------------------------------------------------------------
# E-MANIFEST-ROW
# --------------------------------------------------------------------------

def test_a_row_of_an_unknown_kind_is_malformed(tmp_path):
    """Five columns is not enough: the kind has to be one this reader resolves
    against, or the row is checked by nothing."""
    path = tmp_path / f"{VIDEO_ID}.tsv"
    path.write_text(_manifest_text(["ocr\t1\t2\tword\tsha-1", _frame(10)]),
                    encoding="utf-8")

    man = am.read_manifest(path)

    line = _one(man.defects, "E-MANIFEST-ROW")
    assert line.startswith(f"notes/anchors/{VIDEO_ID}.tsv:5 ")
    assert "malformed row" in line
    assert man.frames == {10: "frame_0010.jpg"}


def test_a_row_whose_start_is_a_word_is_a_non_numeric_span(tmp_path):
    """The second of the three refusals under this code, and a different branch
    from the column count."""
    path = tmp_path / f"{VIDEO_ID}.tsv"
    path.write_text(
        _manifest_text(["frame\tstart\t2\tframe_x.jpg\tsha-x", _frame(10)]),
        encoding="utf-8")

    man = am.read_manifest(path)

    assert "non-numeric span" in _one(man.defects, "E-MANIFEST-ROW")
    assert sorted(man.frames) == [10]


def test_two_caption_spans_may_share_a_start(tmp_path):
    """Only frames are one-per-second. Cues overlap by design -- auto-captions
    roll -- so refusing a shared start here would red-light every real run."""
    path = tmp_path / f"{VIDEO_ID}.tsv"
    path.write_text(
        _manifest_text([_cue(5.0, 9.0, 0), _cue(5.0, 11.0, 1), _frame(10)],
                       namespaces="frames,cues"),
        encoding="utf-8")

    man = am.read_manifest(path)

    assert not _named(man.defects, "E-MANIFEST-ROW")
    assert man.cues == [(5.0, 9.0), (5.0, 11.0)]


# --------------------------------------------------------------------------
# E-MANIFEST-RANGE
# --------------------------------------------------------------------------

def test_a_frame_stamped_exactly_at_the_runtime_is_in_range(tmp_path):
    """The comparison is strict. The last second of a video is a second of it."""
    path = tmp_path / f"{VIDEO_ID}.tsv"
    path.write_text(_manifest_text([_frame(600)]), encoding="utf-8")

    man = am.read_manifest(path)

    assert not _named(man.defects, "E-MANIFEST-RANGE")
    assert man.duration == 600


def test_a_manifest_with_no_runtime_cannot_run_the_range_check(tmp_path):
    """Nothing dates the manifest, so there is no end to be past.

    The frame here is far outside any plausible runtime, which is what makes
    the silence a claim rather than an accident of the fixture.
    """
    path = tmp_path / f"{VIDEO_ID}.tsv"
    path.write_text(_manifest_text([_frame(9999)], duration=None),
                    encoding="utf-8")

    man = am.read_manifest(path)

    assert _named(man.defects, "E-MANIFEST-NO-DURATION")
    assert not _named(man.defects, "E-MANIFEST-RANGE")


def test_a_caption_span_past_the_runtime_is_not_a_range_defect(tmp_path):
    """This rule looks at frames only. A cue span is coalesced from a caption
    track whose last cue routinely runs past the stated duration."""
    path = tmp_path / f"{VIDEO_ID}.tsv"
    path.write_text(
        _manifest_text([_frame(10), _cue(590.0, 900.0)],
                       namespaces="frames,cues"),
        encoding="utf-8")

    man = am.read_manifest(path)

    assert not _named(man.defects, "E-MANIFEST-RANGE")
    assert man.cues == [(590.0, 900.0)]


# --------------------------------------------------------------------------
# E-MANIFEST-DUPLICATE
# --------------------------------------------------------------------------

def test_three_manifests_for_one_video_print_one_line_naming_the_second_file(
        tmp_path):
    """One line per colliding id, and it addresses the SECOND file: the first
    is the one that would be kept, so naming it sends the reader to the wrong
    place. The count and the full list are in the same line."""
    anchors = tmp_path / "notes" / "anchors"
    anchors.mkdir(parents=True)
    for name in ("first.tsv", "second.tsv", "third.tsv"):
        (anchors / name).write_text(f"# video_id\t{VIDEO_ID}\n", encoding="utf-8")

    defects = am.sweep_manifests(tmp_path, {VIDEO_ID})

    line = _one(defects, "E-MANIFEST-DUPLICATE")
    assert line.startswith("notes/anchors/second.tsv:1 ")
    assert f"3 manifests declare {VIDEO_ID}: first.tsv, second.tsv, third.tsv" in line


def test_two_manifests_declaring_no_video_fall_back_to_their_filenames(tmp_path):
    """With no `# video_id` the file is addressed by its stem, so two of them
    collide only when the filenames do -- which they cannot, on one filesystem."""
    anchors = tmp_path / "notes" / "anchors"
    anchors.mkdir(parents=True)
    for name in ("alpha.tsv", "beta.tsv"):
        (anchors / name).write_text("# duration\t600\n", encoding="utf-8")

    defects = am.sweep_manifests(tmp_path, {"alpha", "beta"})

    assert defects == []


# --------------------------------------------------------------------------
# E-MANIFEST-ORPHAN
# --------------------------------------------------------------------------

def test_a_manifest_declaring_no_video_is_addressed_by_its_filename(tmp_path):
    """The fallback is what keeps an undeclared manifest visible at all. It
    also means the id in the complaint is a filename stem, and saying so is the
    difference between a reader finding the file and hunting for a video."""
    anchors = tmp_path / "notes" / "anchors"
    anchors.mkdir(parents=True)
    (anchors / "unnamed.tsv").write_text("# duration\t600\n", encoding="utf-8")

    defects = am.sweep_manifests(tmp_path, {VIDEO_ID})

    line = _one(defects, "E-MANIFEST-ORPHAN")
    assert line.startswith("notes/anchors/unnamed.tsv:1 ")
    assert "no note declares video_id unnamed" in line


def test_a_manifest_renamed_away_from_its_filename_is_detached(tmp_path):
    """The manifest is addressed only by the id it declares, so editing that
    field silently detaches the file -- the shape this sweep mirrors from the
    sidecar sweep. The filename still looks right, which is why it needs a
    line of its own."""
    anchors = tmp_path / "notes" / "anchors"
    anchors.mkdir(parents=True)
    (anchors / f"{VIDEO_ID}.tsv").write_text(f"# video_id\t{OTHER_ID}\n",
                                             encoding="utf-8")

    defects = am.sweep_manifests(tmp_path, {VIDEO_ID})

    line = _one(defects, "E-MANIFEST-ORPHAN")
    assert line.startswith(f"notes/anchors/{VIDEO_ID}.tsv:1 ")
    assert f"no note declares video_id {OTHER_ID}" in line


# --------------------------------------------------------------------------
# E-MANIFEST-ARTIFACT
# --------------------------------------------------------------------------

def test_a_frame_still_on_disk_with_changed_bytes_is_caught_by_rehashing(
        tmp_path):
    """The second of the two refusals, and the one that separates re-hashing
    from merely re-finding. The file is where it was and is not what it was."""
    run = _run_dir(tmp_path / "run", {"frames/frame_0010.jpg": b"the original"})
    frame_path = run / "frames" / "frame_0010.jpg"
    path = tmp_path / f"{VIDEO_ID}.tsv"
    path.write_text(
        _manifest_text([_frame(10, sha=sha256(frame_path))], sources=[str(run)]),
        encoding="utf-8")
    man = am.read_manifest(path)
    assert am.verify_artifacts(man, tmp_path) == []

    frame_path.write_bytes(b"a different picture entirely")
    defects = am.verify_artifacts(man, tmp_path)

    line = _one(defects, "E-MANIFEST-ARTIFACT")
    assert "1 frame(s) no longer hash to the recorded sha256" in line


def test_a_manifest_naming_no_source_directories_finds_its_frames_nowhere(
        tmp_path):
    """An empty search set makes every frame read as gone, and the line has to
    say the set was empty -- otherwise it reads as evidence of deletion.

    The frame is written to disk UNDER the root the caller passes, and named
    with the recorded sha256, so a search that fell back to that root would
    find it and report nothing. Without the file on disk the case cannot tell
    an empty search set from a search that ran and found nothing, and a
    fallback to the root left the whole suite green.
    """
    run = _run_dir(tmp_path / "run", {"frames/frame_0010.jpg": b"the original"})
    frame_path = run / "frames" / "frame_0010.jpg"
    path = tmp_path / f"{VIDEO_ID}.tsv"
    path.write_text(_manifest_text([_frame(10, sha=sha256(frame_path))]),
                    encoding="utf-8")
    man = am.read_manifest(path)
    assert man.sources == []
    assert frame_path.is_file() and frame_path.is_relative_to(tmp_path)

    defects = am.verify_artifacts(man, tmp_path)

    line = _one(defects, "E-MANIFEST-ARTIFACT")
    assert "1 of 1 frame(s) named by this manifest are gone from nowhere" in line


def test_the_gone_from_line_names_the_source_directories_the_manifest_declared(
        tmp_path):
    """"nowhere" is the fallback for an empty list, not the message.

    Two directories are declared and neither holds the frame, so the line has
    to name both of them in the order the manifest gave: that is the difference
    between a reader who knows where to look and one who is told the search set
    was empty when it was not.
    """
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    path = tmp_path / f"{VIDEO_ID}.tsv"
    path.write_text(
        _manifest_text([_frame(10)], sources=[str(first), str(second)]),
        encoding="utf-8")

    defects = am.verify_artifacts(am.read_manifest(path), tmp_path)

    line = _one(defects, "E-MANIFEST-ARTIFACT")
    assert f"are gone from {first}, {second}" in line
    assert "nowhere" not in line


def test_the_unresolvable_runs_ledger_does_not_excuse_a_missing_artifact(
        tmp_path, monkeypatch):
    """That ledger excuses a missing manifest. A manifest that exists and names
    frames nobody can find is a different claim and stays a defect."""
    monkeypatch.setitem(am.UNRESOLVABLE_RUNS, VIDEO_ID,
                        "the run directory is gone and cannot be rebuilt")
    note = _write_note(tmp_path, "- `[00:10]` `ON-SCREEN` the opening slide.")
    _write_manifest(tmp_path, [_frame(10)])

    defects, _ = am.check_note(note, tmp_path, artifacts=True)

    assert "1 of 1 frame(s)" in _one(defects, "E-MANIFEST-ARTIFACT")


# --------------------------------------------------------------------------
# E-MANIFEST-VIDEO
# --------------------------------------------------------------------------

def test_a_manifest_declaring_another_video_is_refused(tmp_path):
    """Resolving against another video's frames is the failure this rule exists
    for, so the line names both ids and neither is guessable from the other."""
    note = _write_note(tmp_path, "- `[00:10]` `ON-SCREEN` the opening slide.")
    _write_manifest(tmp_path, [_frame(10)], declared=OTHER_ID)

    defects, _ = am.check_note(note, tmp_path)

    line = _one(defects, "E-MANIFEST-VIDEO")
    assert f"manifest declares {OTHER_ID}, note declares {VIDEO_ID}" in line


def test_a_manifest_declaring_no_video_is_refused_against_a_note_that_does(
        tmp_path):
    """Nothing recorded is not the same as agreeing. A manifest with no id
    belongs to no video and cannot be shown to belong to this one."""
    note = _write_note(tmp_path, "- `[00:10]` `ON-SCREEN` the opening slide.")
    _write_manifest(tmp_path, [_frame(10)], declared=None)

    defects, _ = am.check_note(note, tmp_path)

    line = _one(defects, "E-MANIFEST-VIDEO")
    assert f"manifest declares None, note declares {VIDEO_ID}" in line


# --------------------------------------------------------------------------
# E-MANIFEST-DURATION
# --------------------------------------------------------------------------

def test_a_difference_of_exactly_the_tolerance_is_not_drift(tmp_path):
    """Strict again: two seconds apart is inside the tolerance, three is not,
    and a fixture at each side of the line is what pins the operator."""
    _write_manifest(tmp_path, [_frame(10)])
    inside = _write_note(tmp_path, "- `[00:10]` `ON-SCREEN` a slide.",
                         duration="10:02")

    defects, _ = am.check_note(inside, tmp_path)
    assert not _named(defects, "E-MANIFEST-DURATION")

    outside = _write_note(tmp_path, "- `[00:10]` `ON-SCREEN` a slide.",
                          duration="10:03")
    defects, _ = am.check_note(outside, tmp_path)
    assert "manifest 600s, note 603s" in _one(defects, "E-MANIFEST-DURATION")


def test_a_note_that_states_no_runtime_cannot_drift(tmp_path):
    """Both sides have to state a runtime for them to disagree. A note with no
    duration is a different defect and belongs to a different layer."""
    note = _write_note(tmp_path, "- `[00:10]` `ON-SCREEN` a slide.",
                       duration=None)
    _write_manifest(tmp_path, [_frame(10)])

    defects, _ = am.check_note(note, tmp_path)

    assert not _named(defects, "E-MANIFEST-DURATION")


# --------------------------------------------------------------------------
# E-MANIFEST-NAMESPACE
# --------------------------------------------------------------------------

def test_caption_rows_with_no_cues_namespace_declared_are_refused(tmp_path):
    """The other half of the rule: rows nothing declared would be checked by
    nothing, which is the silent direction of the same mismatch."""
    path = tmp_path / f"{VIDEO_ID}.tsv"
    path.write_text(
        _manifest_text([_frame(10), _cue(0.0, 60.0)], namespaces="frames"),
        encoding="utf-8")

    man = am.read_manifest(path)

    line = _one(man.defects, "E-MANIFEST-NAMESPACE")
    assert "cues declared=False rows=1" in line


def test_a_namespace_the_reader_knows_nothing_about_is_passed_over(tmp_path):
    """Neither declared-empty nor undeclared-full. The reader resolves two
    namespaces and says nothing about a third, which is worth writing down: a
    manifest can declare a namespace this gate never checks."""
    path = tmp_path / f"{VIDEO_ID}.tsv"
    path.write_text(_manifest_text([_frame(10)], namespaces="frames,ocr"),
                    encoding="utf-8")

    man = am.read_manifest(path)

    assert not _named(man.defects, "E-MANIFEST-NAMESPACE")
    assert "ocr" in man.namespaces


# --------------------------------------------------------------------------
# E-MANIFEST-NO-DURATION
# --------------------------------------------------------------------------

@pytest.mark.parametrize("written", ["600.0", "-600"])
def test_a_runtime_that_is_not_plain_digits_reads_as_no_runtime(tmp_path, written):
    """A decimal point or a minus sign fails the digit test and is dropped in
    silence, so the manifest is undated by a value that looks like a runtime."""
    path = tmp_path / f"{VIDEO_ID}.tsv"
    path.write_text(_manifest_text([_frame(10)], duration=written),
                    encoding="utf-8")

    man = am.read_manifest(path)

    assert man.duration == 0
    assert "nothing dates this manifest" in _one(man.defects,
                                                 "E-MANIFEST-NO-DURATION")


def test_a_runtime_of_zero_cannot_be_told_from_unknown(tmp_path):
    """The ambiguity this rule was written to name: zero doubles as "unknown"
    and "zero-length", and both switch three checks off."""
    path = tmp_path / f"{VIDEO_ID}.tsv"
    path.write_text(_manifest_text([_frame(10)], duration="0"), encoding="utf-8")

    man = am.read_manifest(path)

    assert man.duration == 0
    assert "so three checks cannot run" in _one(man.defects,
                                                "E-MANIFEST-NO-DURATION")


# --------------------------------------------------------------------------
# E-ADOPT-MISSING
# --------------------------------------------------------------------------

def test_a_destination_that_is_a_directory_was_not_written(tmp_path):
    """A directory is not the file the copy claims to be, and reporting it as
    absent is right: nothing readable landed at that path."""
    source = tmp_path / "source.jpg"
    source.write_bytes(b"a frame")
    destination = tmp_path / "dest.jpg"
    destination.mkdir()

    complaints = am.verify_copy(source, destination)

    assert complaints == [f"E-ADOPT-MISSING {destination} was not written"]


# --------------------------------------------------------------------------
# E-ADOPT-CORRUPT
# --------------------------------------------------------------------------

def test_two_empty_files_hash_alike_and_are_not_corrupt(tmp_path):
    """A zero-length source copies to a zero-length destination, and a hash
    comparison that special-cased emptiness would call the honest copy broken."""
    source = tmp_path / "source.jpg"
    destination = tmp_path / "dest.jpg"
    source.write_bytes(b"")
    destination.write_bytes(b"")

    assert am.verify_copy(source, destination) == []


# --------------------------------------------------------------------------
# E-ADOPT-EXTRA
# --------------------------------------------------------------------------

def test_a_stray_empty_directory_in_the_destination_is_not_a_stranger(tmp_path):
    """The reconcile walks files. An empty directory records no run's frames,
    so calling it extra would fail an adoption over a leftover folder."""
    source = _run_dir(tmp_path / "run", {"frames/a.jpg": b"a"})
    dest_root = tmp_path / "dest"
    (dest_root / VIDEO_ID / "run" / "leftovers").mkdir(parents=True)

    _, complaints = am.adopt_run(VIDEO_ID, [source], dest_root, allow_temp=True)

    assert complaints == []


def test_adopting_a_source_onto_itself_calls_nothing_a_stranger(tmp_path):
    """The copy is skipped entirely when source and destination resolve to one
    path, so the reconcile must not then read the source's own files as another
    run's leftovers."""
    source = _run_dir(tmp_path / VIDEO_ID / "run", {"frames/a.jpg": b"a"})

    durable, complaints = am.adopt_run(VIDEO_ID, [source], tmp_path,
                                       allow_temp=True)

    assert complaints == []
    assert [p.resolve() for p in durable] == [source.resolve()]


# --------------------------------------------------------------------------
# E-ADOPT-ID
# --------------------------------------------------------------------------

def test_an_id_that_starts_with_a_hyphen_is_a_legal_directory_name(tmp_path):
    """The package states elsewhere that a real id may start with a hyphen --
    it is why the build flag is spelled with an equals sign. Refusing it here
    would lock that video out of adoption entirely."""
    source = _run_dir(tmp_path / "run", {"frames/a.jpg": b"a"})

    durable, complaints = am.adopt_run("-leadinghyphen", [source],
                                       tmp_path / "dest", allow_temp=True)

    assert complaints == []
    assert durable == [tmp_path / "dest" / "-leadinghyphen" / "run"]


def test_an_empty_id_names_the_root_itself_and_is_refused(tmp_path):
    """`dest_root / ""` is `dest_root`, so an empty id adopts over the declared
    root rather than into a directory under it."""
    source = _run_dir(tmp_path / "run", {"frames/a.jpg": b"a"})

    durable, complaints = am.adopt_run("", [source], tmp_path / "dest",
                                       allow_temp=True)

    assert durable == []
    assert complaints == ["E-ADOPT-ID '' is not usable as a directory name"]


def test_an_id_carrying_a_backslash_is_refused(tmp_path):
    """A separator on another platform is still a separator: refusing only `/`
    leaves the traversal open to anything that reads the id elsewhere."""
    source = _run_dir(tmp_path / "run", {"frames/a.jpg": b"a"})
    traversing = "up\\out"

    durable, complaints = am.adopt_run(traversing, [source], tmp_path / "dest",
                                       allow_temp=True)

    assert durable == []
    assert len(complaints) == 1
    assert complaints[0].startswith("E-ADOPT-ID ")
    assert repr(traversing) in complaints[0]
    assert "is not usable as a directory name" in complaints[0]


# --------------------------------------------------------------------------
# adopting into a path the OS owns -- a refusal with no code of its own
# --------------------------------------------------------------------------

def test_an_adoption_into_a_path_the_os_owns_copies_nothing(tmp_path):
    """The refusal the whole adoption path exists for, and nothing pinned it.

    A manifest built from a run under a directory the OS clears certifies
    frames that stop existing at the next reboot, and every check downstream
    goes on confirming their sha256 in the meantime. Three cases in this file
    know the refusal is there -- two pass the escape to get past it and one
    stubs it out -- and none of them watches it fire.

    `tmp_path` is under the area the OS clears, which is what makes the fixture
    honest rather than contrived: this is the state a real caller reaches by
    pointing an adoption at a scratch directory.
    """
    source = _run_dir(tmp_path / "run", {"frames/a.jpg": b"a"})
    dest_root = tmp_path / "dest"
    assert am.temp_owned(dest_root / VIDEO_ID)

    durable, complaints = am.adopt_run(VIDEO_ID, [source], dest_root)

    assert durable == []
    assert complaints == [f"refusing to adopt into {dest_root / VIDEO_ID}: "
                          f"the OS owns that path"]
    assert not dest_root.exists()


def test_the_same_destination_with_the_escape_given_is_adopted(tmp_path):
    """The must-NOT direction, and the reason the escape exists at all: the
    module's own selftest adopts into a temp directory on purpose.

    Same source and same destination as the case above, so the escape is the
    only difference between a refusal and a copy that lands.
    """
    source = _run_dir(tmp_path / "run", {"frames/a.jpg": b"a"})
    dest_root = tmp_path / "dest"

    durable, complaints = am.adopt_run(VIDEO_ID, [source], dest_root,
                                       allow_temp=True)

    assert complaints == []
    assert durable == [dest_root / VIDEO_ID / "run"]
    assert (dest_root / VIDEO_ID / "run" / "frames" / "a.jpg").read_bytes() == b"a"


def test_an_adoption_into_a_path_the_os_owns_exits_one_and_writes_no_manifest(
        tmp_path):
    """The surface a caller reads. The complaint line proves nothing about the
    exit code, and this refusal happens before the manifest is built -- so a
    run that ignored it would leave a manifest naming a directory that expires.
    """
    source = _run_dir(tmp_path / "run", {"frames/a.jpg": b"a"})
    dest_root = tmp_path / "dest"

    code = am.main([f"--adopt={VIDEO_ID}", "--run-dir", str(source),
                    "--runs-root", str(dest_root)], root=tmp_path)

    assert code == 1
    assert not (tmp_path / "notes" / "anchors" / f"{VIDEO_ID}.tsv").exists()


def test_an_unusable_id_is_refused_before_the_owned_path_is_looked_at(tmp_path):
    """Order matters, because the two refusals ask for different repairs.

    The destination here is one the OS owns AND the id walks out of the root.
    The id check runs first, so the caller is told to fix the id; reporting the
    path instead would send them to change a directory that is not the problem.
    """
    source = _run_dir(tmp_path / "run", {"frames/a.jpg": b"a"})
    dest_root = tmp_path / "dest"
    assert am.temp_owned(dest_root / "up")

    durable, complaints = am.adopt_run("../up", [source], dest_root)

    assert durable == []
    assert complaints == ["E-ADOPT-ID '../up' is not usable as a directory name"]
    assert not [c for c in complaints if "the OS owns that path" in c]


# --------------------------------------------------------------------------
# E-ADOPT-IO
# --------------------------------------------------------------------------

def test_a_failure_creating_the_destinations_parent_is_reported_not_raised(
        tmp_path):
    """The guarded step is the pair -- make the folder, then write the file --
    and the folder is the half no case reached. A file sitting where the folder
    has to go is the ordinary way it happens after a half-run adoption."""
    source = _run_dir(tmp_path / "run", {"sub/a.jpg": b"a"})
    dest_root = tmp_path / "dest"
    (dest_root / VIDEO_ID / "run").mkdir(parents=True)
    (dest_root / VIDEO_ID / "run" / "sub").write_bytes(b"not a directory")

    _, complaints = am.adopt_run(VIDEO_ID, [source], dest_root, allow_temp=True)

    line = _one(complaints, "E-ADOPT-IO")
    assert str(source / "sub" / "a.jpg") in line
    assert "File exists" in line


def test_an_adoption_that_could_not_write_exits_one_rather_than_raising(
        tmp_path, monkeypatch, capsys):
    """The exit code is the surface a caller reads, and the complaint line
    proved nothing about it: an adoption that raises leaves a half-written
    destination and a traceback, which reads like a crash, not a refusal.

    `temp_owned` is stubbed because the real refusal for a temp destination
    fires first and would hide the path under test.
    """
    monkeypatch.setattr(am, "temp_owned", lambda path: False)
    source = _run_dir(tmp_path / "run", {"sub/a.jpg": b"a"})
    dest_root = tmp_path / "dest"
    (dest_root / VIDEO_ID / "run").mkdir(parents=True)
    (dest_root / VIDEO_ID / "run" / "sub").write_bytes(b"not a directory")

    code = am.main([f"--adopt={VIDEO_ID}", "--run-dir", str(source),
                    "--runs-root", str(dest_root)], root=tmp_path)

    assert code == 1
    assert "E-ADOPT-IO" in capsys.readouterr().err


def test_one_file_failing_does_not_stop_the_others_being_copied_and_checked(
        tmp_path):
    """A failed write is one file's complaint, not the run's. Aborting on the
    first would leave the rest unverified and the destination half-built with
    nothing said about which half."""
    source = _run_dir(tmp_path / "run", {"a.jpg": b"a", "b.jpg": b"b",
                                         "c.jpg": b"c"})

    def refuse_the_middle_file(src, dst):
        if Path(src).name == "b.jpg":
            raise OSError(28, "No space left on device")
        shutil.copy2(src, dst)

    _, complaints = am.adopt_run(VIDEO_ID, [source], tmp_path / "dest",
                                 allow_temp=True, copier=refuse_the_middle_file)

    assert len(_named(complaints, "E-ADOPT-IO")) == 1
    assert "No space left on device" in complaints[0]
    landed = sorted(p.name for p in
                    (tmp_path / "dest" / VIDEO_ID / "run").iterdir())
    assert landed == ["a.jpg", "c.jpg"]


# --------------------------------------------------------------------------
# E-CAPTION-EMPTY
# --------------------------------------------------------------------------

def test_an_index_holding_only_its_header_is_a_file_that_says_nothing(tmp_path):
    """A file that exists and holds no row reads exactly like an honest miss,
    which is how a broken witness passes for an absent one."""
    note = _write_note(tmp_path, "- `[00:10]` `SPOKEN` the speaker says so.")
    _write_manifest(tmp_path, [_frame(10)])
    _write_caption(tmp_path, am.CAPTION_HEADER + "\n")

    defects, _ = am.check_note(note, tmp_path)

    line = _one(defects, "E-CAPTION-EMPTY")
    assert f"{VIDEO_ID}.tsv exists but holds no usable cue rows" in line


def test_an_index_whose_times_are_words_holds_no_usable_rows(tmp_path):
    """Right shape, unreadable times: a different branch of the same reader,
    and one a row-count check would call full."""
    note = _write_note(tmp_path, "- `[00:10]` `SPOKEN` the speaker says so.")
    _write_manifest(tmp_path, [_frame(10)])
    _write_caption(tmp_path,
                   am.CAPTION_HEADER + "\nnine\tten\tand here is what was said\n")

    defects, _ = am.check_note(note, tmp_path)

    assert "holds no usable cue rows" in _one(defects, "E-CAPTION-EMPTY")


# --------------------------------------------------------------------------
# E-CAPTION-VIDEO
# --------------------------------------------------------------------------

def test_the_right_video_declared_with_trailing_spaces_is_trimmed(tmp_path):
    """The header value is trimmed before it is compared, so whitespace a
    writer left behind must not turn a correct index into a foreign one."""
    note = _write_note(tmp_path, "- `[00:10]` `SPOKEN` the speaker says so.")
    _write_manifest(tmp_path, [_frame(10)])
    index = _write_caption(
        tmp_path,
        f"# video_id\t  {VIDEO_ID}  \n{am.CAPTION_HEADER}\n"
        "0.0\t9.0\tand here is what was said\n")

    defects, _ = am.check_note(note, tmp_path)

    assert am.index_video_id(index) == VIDEO_ID
    assert not _named(defects, "E-CAPTION-VIDEO")
    assert not _named(defects, "E-CAPTION-EMPTY")


def test_a_wrong_index_that_is_also_empty_earns_both_complaints(tmp_path):
    """Two independent failures of one file. Reporting either one alone leaves
    the reader repairing half of it and re-running into the other."""
    note = _write_note(tmp_path, "- `[00:10]` `SPOKEN` the speaker says so.")
    _write_manifest(tmp_path, [_frame(10)])
    _write_caption(tmp_path, f"# video_id\t{OTHER_ID}\n{am.CAPTION_HEADER}\n")

    defects, _ = am.check_note(note, tmp_path)

    assert "holds no usable cue rows" in _one(defects, "E-CAPTION-EMPTY")
    assert f"declares {OTHER_ID}" in _one(defects, "E-CAPTION-VIDEO")


# --------------------------------------------------------------------------
# E-FENCE-UNBALANCED
# --------------------------------------------------------------------------

def test_a_tilde_fence_left_open_is_unbalanced(tmp_path):
    """The reader accepts two fence spellings, so counting only one of them
    leaves the whole tail of a note exempt from every check in silence."""
    note = _write_note(tmp_path, "~~~\nquoted `[00:10]` and never closed\n")
    _write_manifest(tmp_path, [_frame(10)])

    defects, _ = am.check_note(note, tmp_path)

    assert "odd number of code fences" in _one(defects, "E-FENCE-UNBALANCED")


def test_three_fences_are_odd_without_being_one(tmp_path):
    """A note that opens, closes and opens again looks deliberately fenced, and
    a check for "exactly one" would read it as balanced."""
    note = _write_note(
        tmp_path,
        "```\nquoted\n```\n\nprose in between\n\n```\nopen for ever\n")
    _write_manifest(tmp_path, [_frame(10)])

    defects, _ = am.check_note(note, tmp_path)

    assert "reads as verbatim and is exempt" in _one(defects,
                                                     "E-FENCE-UNBALANCED")


def test_a_note_whose_fences_balance_is_not_refused(tmp_path):
    """The false-positive direction, and the reason the count is a parity check
    rather than a ban on fences."""
    note = _write_note(
        tmp_path,
        "```\nquoted\n```\n\n- `[00:10]` `ON-SCREEN` the opening slide.")
    _write_manifest(tmp_path, [_frame(10)])

    defects, _ = am.check_note(note, tmp_path)

    assert not _named(defects, "E-FENCE-UNBALANCED")
    assert defects == []
