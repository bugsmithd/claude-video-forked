"""Layer 2, the witnesses: the cases `spec/evidence-witness.toml` names.

Every rule in that table names the neighbouring wrong answers it must also
refuse, and a neighbour with no case is a hole the table is required to make
red. This file is the other half of that bargain for the witness layer: one
test per neighbour that nothing else in the suite pinned, each driving the real
function rather than restating the rule beside it.

Three habits are deliberate, and each of them is a defect this project already
shipped:

  * The DEFECT CODE is asserted, plus something a wrong implementation would
    get wrong -- a second, a count, a window number, a percentage. `assert
    defects` passes for the wrong reason, and did.
  * The neighbours that must NOT fire are here too. A rule with only positive
    cases is half specified, and the false-positive direction is where a gate
    stops being read.
  * Where a rule is known to refuse correct work, the case says so and pins the
    behaviour anyway. A false positive written down is a decision; a false
    positive nobody wrote down is a surprise.

Every fixture is invented. No video id, corpus path or person appears here.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from watchquality import demote_note as dn
from watchquality import note_coverage as nc
from watchquality import note_windows as nw
from watchquality import ocr_vote as ov
from watchquality import say_captions as sc
from watchquality import spoken_vote as sv
from watchquality import transcript_align as ta


# --------------------------------------------------------------------------
# shared fixtures. Coined vocabulary rather than repeated filler: the first
# healthy-transcript fixture in this package repeated a handful of words and
# tripped the vocabulary floor, which is the check working and the fixture
# lying. Real speech is wide, so a fixture that stands in for it has to be.
# --------------------------------------------------------------------------

def coined(n: int) -> str:
    letters, out = "abcdefghijklmnopqrstuvwxyz", ""
    n += 1
    while n:
        n, remainder = divmod(n - 1, 26)
        out = letters[remainder] + out
    return out


def wide_text(i: int) -> str:
    return (f"{coined(4 * i)} {coined(4 * i + 1)} {coined(4 * i + 2)} "
            f"{coined(4 * i + 3)} and the rest")


def rendering(count: int, step: float = 3.0, length: float | None = None,
              text=wide_text) -> list[dict]:
    return [{"start": i * step, "end": i * step + (length if length is not None else step),
             "text": text(i) if callable(text) else text}
            for i in range(count)]


def align_defects(segments: list[dict], duration: float | None = None,
                  name: str = "v") -> list[str]:
    return ta.check_rendering(name, segments, duration, ta.MAX_RUN, ta.MAX_GAP,
                              ta.MIN_DISTINCT)[0]


def codes_in(lines) -> set[str]:
    out = set()
    for line in lines:
        for word in line.split():
            if word.startswith("E-"):
                out.add(word)
    return out


def named(lines, code: str) -> list[str]:
    return [line for line in lines if code in line]


def run_align(paths: list[Path], *flags: str) -> tuple[int, list[str]]:
    """`wq-transcript-align`, returning (exit code, the defect lines it printed)."""
    import contextlib
    import io
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = ta.main([str(p) for p in paths] + list(flags))
    return code, [line for line in out.getvalue().splitlines() if " E-" in line]


def run_windows(tmp_path: Path, segments: list[dict], *flags: str,
                chapters: dict | None = None) -> tuple[int, list[str]]:
    import contextlib
    import io
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps(segments), encoding="utf-8")
    argv = [str(path), *flags]
    if chapters is not None:
        info = tmp_path / "video.info.json"
        info.write_text(json.dumps(chapters), encoding="utf-8")
        argv += ["--chapters", str(info)]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = nw.main(argv)
    return code, [line for line in out.getvalue().splitlines() if " E-" in line]


# A short transcript with one rare term per segment, so the salient sets are
# large enough that E-COV-THIN is not firing underneath every other case.
def cov_transcript(count: int = 40) -> list[dict]:
    return [{"start": i * 10.0, "end": i * 10.0 + 10.0,
             "text": f"The speaker mentions {coined(i)}zone and moves on to the next thing."}
            for i in range(count)]


def cov_measure(tmp_path: Path, note_text: str, segments: list[dict],
                span: tuple[float, float] | None = None) -> dict:
    note = tmp_path / "note.md"
    note.write_text("---\ntitle: t\n---\n\n" + note_text, encoding="utf-8")
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps(segments), encoding="utf-8")
    return nc.measure(note, transcript, span)


def cov_defects(tmp_path: Path, note_text: str, segments: list[dict],
                span: tuple[float, float] | None = None) -> list[str]:
    return nc.defects(cov_measure(tmp_path, note_text, segments, span))


# Ten sentence shapes rather than one template. A fixture built by repeating one
# sentence with the minute swapped trips E-COV-REPEAT underneath whatever rule
# it was written for, and a case that is red for a second reason cannot report
# on the first: that is the exact defect this round was convened to close.
COV_SHAPES = (
    "the speaker opens on {w} and gives a reason nobody had offered before",
    "{w} turns out to be the whole argument, and the numbers behind it follow",
    "a caller asks about {w}, and the answer runs against the earlier claim",
    "on {w} the talk slows down and works through one example at length",
    "the second half returns to {w} with a different measurement entirely",
    "{w} is set beside a competitor and the comparison decides the section",
    "a long aside about {w} explains why the first attempt was abandoned",
    "the closing summary puts {w} first among everything discussed today",
    "an objection to {w} is raised and answered inside the same minute",
    "{w} finally gets its cost written down, which changes the conclusion",
)


def cov_row(minute: int, second: int = 5) -> str:
    """One claim row anchored in `minute`, worded unlike its neighbours."""
    shape = COV_SHAPES[minute % len(COV_SHAPES)].format(w=f"{coined(minute)}zone")
    return f"- `[{minute:02d}:{second:02d}]` `SPOKEN` — {shape}.\n"


def cov_spoken(count: int = 60) -> list[dict]:
    """A recording whose every minute names its own rare term."""
    return [{"start": i * 10.0, "end": i * 10.0 + 10.0,
             "text": f"The speaker discusses {coined(i)}zone at some length here today."}
            for i in range(count)]


OCR_HEAD = ("# video_id\t{vid}\n# psm\t6\n# frames\t{n}\n# missing\t0\n"
            "# textful\t{n}\n" + ov.OCR_HEADER + "\n")


def ocr_row(frame: str, seconds: int, body: str, words: int | None = None) -> str:
    return (f"{frame}\t{seconds}\t-\t1280\t720\t"
            f"{len(body.split()) if words is None else words}\t{body}\n")


def ocr_index(rows: list[str], vid: str = "TESTID") -> str:
    return OCR_HEAD.format(vid=vid, n=len(rows)) + "".join(rows)


def ocr_note(body: str, vid: str = "TESTID") -> str:
    return (f'---\nvideo_id: {vid}\nduration: "40:00"\n---\n\n# t\n\n{body}\n')


# ==========================================================================
# ocr_vote.read_index -- E-OCR-ROW, E-OCR-UNREADABLE
# ==========================================================================

def test_a_word_count_written_as_a_decimal_is_a_malformed_row(tmp_path):
    """`4.0` is a number to a reader and not one to `int()`.

    The row shape check is two `int()` calls, and a column that looks numeric
    and is not is the neighbour of a column that plainly is not.
    """
    path = tmp_path / "TESTID.tsv"
    path.write_text(ocr_index([ocr_row("a.jpg", 30, "a slide with plenty of words on it")])
                    + "b.jpg\t60\t-\t1280\t720\t4.0\tfour words on screen\n",
                    encoding="utf-8")

    index = ov.read_index(path)

    assert len(index.defects) == 1, index.defects
    assert "E-OCR-ROW" in index.defects[0]
    assert "non-numeric column" in index.defects[0]
    # The LINE, so a hand can find it: the header block is six lines and the
    # good row is the seventh.
    assert index.defects[0].startswith("notes/.ocr/TESTID.tsv:8 "), index.defects[0]
    assert len(index.rows) == 1, "the good row before it must still be read"


def test_six_columns_and_no_text_is_a_readable_frame_with_nothing_on_it(tmp_path):
    """A frame that OCRs to nothing is evidence, not a malformed row.

    Refusing it would turn every blank slide in a recording into a defect, and
    the pixel channel's own answer for such a frame is zero words.
    """
    path = tmp_path / "TESTID.tsv"
    path.write_text(ocr_index([ocr_row("a.jpg", 30, "a slide with plenty of words on it")])
                    + "b.jpg\t60\t-\t1280\t720\t0\n", encoding="utf-8")

    index = ov.read_index(path)

    assert index.defects == []
    assert len(index.rows) == 2
    assert index.rows[1] == {"frame": "b.jpg", "seconds": 60, "sha": "-",
                             "words": 0, "text": "", "tokens": []}
    assert index.textful == index.rows[:1], "a blank frame witnesses nothing"


def test_a_declared_word_count_is_believed_over_the_text_beside_it(tmp_path):
    """The escape: the textful share is computed from the NUMBER, never the text.

    Five forged counts turn a run the pixels never read into one the vote will
    act on. Pinned as the behaviour it is, so that changing it is a decision
    somebody makes rather than a fix that quietly lands.
    """
    path = tmp_path / "TESTID.tsv"
    path.write_text(ocr_index([ocr_row("a.jpg", 30, "two words", words=99)]),
                    encoding="utf-8")

    index = ov.read_index(path)

    assert index.defects == []
    assert index.rows[0]["words"] == 99
    assert len(index.rows[0]["text"].split()) == 2
    assert index.textful == index.rows, "declared 99, and two words are on screen"
    assert index.fraction == 1.0


def test_int_takes_more_than_a_whole_number_in_the_count_column(tmp_path):
    """"A whole number" is what a reader hears; `int()` is what the row gets.

    Four counts nobody would type on purpose are read without a word: a
    negative, one padded with spaces, one written with an underscore separator,
    and one in non-ASCII digits. The negative is the interesting half -- it is
    accepted and then can never be textful, so the frame is silently dropped
    from the vote rather than reported.
    """
    path = tmp_path / "TESTID.tsv"
    path.write_text(
        ocr_index([ocr_row("a.jpg", 30, "a slide with plenty of words on it")])
        + "b.jpg\t60\t-\t1280\t720\t-4\tnegative words\n"
        + "c.jpg\t70\t-\t1280\t720\t 8 \tpadded columns here\n"
        + "d.jpg\t80\t-\t1280\t720\t1_0\tunderscored count\n"
        + "e.jpg\t90\t-\t1280\t720\t١٢\tarabic indic digits\n",
        encoding="utf-8")

    index = ov.read_index(path)

    assert index.defects == [], "none of the four is a malformed row"
    assert [r["words"] for r in index.rows] == [8, -4, 8, 10, 12]
    assert [r["frame"] for r in index.textful] == ["a.jpg", "c.jpg", "d.jpg", "e.jpg"]
    assert index.fraction == 0.8, "the negative row witnesses nothing and says nothing"


def test_a_row_of_eight_columns_is_accepted_and_its_eighth_is_discarded(tmp_path):
    """Six columns is a floor and not a shape: an extra column is dropped.

    The text is read from the seventh column alone, so a writer that appended
    one more field would lose it without a defect. Pinned as the behaviour it
    is, because the row shape check reads as a shape check and is a floor.
    """
    path = tmp_path / "TESTID.tsv"
    path.write_text(
        ocr_index([ocr_row("a.jpg", 30, "a slide with plenty of words on it")])
        + "b.jpg\t60\t-\t1280\t720\t6\tan eighth column follows\tdiscarded\n",
        encoding="utf-8")

    index = ov.read_index(path)

    assert index.defects == []
    assert len(index.rows) == 2
    assert index.rows[1]["text"] == "an eighth column follows"
    assert "discarded" not in index.rows[1]["tokens"]


def test_a_directory_where_the_index_should_be_is_unreadable(tmp_path):
    """Not a crash and not silence: a named defect with the reason attached."""
    path = tmp_path / "TESTID.tsv"
    path.mkdir()

    index = ov.read_index(path)

    assert len(index.defects) == 1
    assert "E-OCR-UNREADABLE" in index.defects[0]
    assert "Is a directory" in index.defects[0]
    assert index.rows == []


def test_bytes_that_are_not_text_are_unreadable(tmp_path):
    """One undecodable byte reports itself, and names where it is.

    A checker that dies on bad input is worse than one that reports it, and the
    byte offset is what makes the report actionable.
    """
    path = tmp_path / "TESTID.tsv"
    path.write_bytes(b"frame\tseconds\n\xff\xfe\x00bad bytes here\n")

    index = ov.read_index(path)

    assert len(index.defects) == 1
    assert "E-OCR-UNREADABLE" in index.defects[0]
    assert "not valid UTF-8 at byte 14" in index.defects[0]


def test_an_empty_index_is_readable_and_declares_the_channel_unavailable(tmp_path):
    """An index written and never filled READ BACK. It is empty, not unreadable.

    The distinction is the whole rule: unreadable is a defect a hand must
    repair, and an empty file is a channel that was not there. Refusing it
    would turn every run whose OCR pass wrote a file and found nothing into a
    hard defect, and the run's own answer for that -- one declaration in
    frontmatter terms -- is what must come out instead.
    """
    index_path = ov.ocr_path(tmp_path, "TESTID")
    index_path.parent.mkdir(parents=True)
    index_path.write_text("", encoding="utf-8")

    index = ov.read_index(index_path)

    assert index.defects == [], "an empty file read back; nothing failed to read"
    assert (index.rows, index.fraction) == ([], 0.0)

    note = tmp_path / "notes" / "2026-01-01--a--TESTID.md"
    note.write_text(ocr_note("- `ON-SCREEN` the Kajabi dashboard is open "
                             "`[27:56]`"), encoding="utf-8")

    defects, advisory, row = ov.vote_note(note, tmp_path, require_witness=True)

    assert defects == [], "even with --require-witness, an absent channel is not a defect"
    assert len(advisory) == 1, advisory
    assert ("pixel channel unavailable: 0 of 0 frames are textful"
            in advisory[0]), advisory[0]
    assert (row["index"], row["frames"], row["claims"]) == ("present", 0, 0)


# ==========================================================================
# ocr_vote.vote_note -- E-NO-PIXEL-WITNESS
# ==========================================================================

def test_a_claim_tagged_twice_leaves_the_pixel_vote_entirely(tmp_path):
    """The escape: a block tagged both on-screen and spoken is skipped whole.

    `vote_note` examines a block only when exactly one evidence class is on it,
    so a second tag removes the claim from the vote -- and with it every chance
    of the unwitnessed-proper-noun refusal firing. The single-class control
    below is what proves the fixture is otherwise on point.
    """
    index = ov.ocr_path(tmp_path, "TESTID")
    index.parent.mkdir(parents=True)
    index.write_text(ocr_index([
        ocr_row("full_27m56s.jpg", 1676, ov.WITNESS_FRAME),
        ocr_row("full_10m00s.jpg", 600,
                "a different slide about writing systems and leverage entirely"),
    ]), encoding="utf-8")
    note = tmp_path / "notes" / "2026-01-01--a--TESTID.md"

    note.write_text(ocr_note("- `ON-SCREEN` `SPOKEN` the Kajabi dashboard is "
                             "open `[27:56]`"), encoding="utf-8")
    defects, _advisory, row = ov.vote_note(note, tmp_path, require_witness=True)
    assert defects == []
    assert row["claims"] == 0, "two tags, so the block is never examined"
    assert row["flag"] == 0

    note.write_text(ocr_note("- `ON-SCREEN` the Kajabi dashboard is open "
                             "`[27:56]`"), encoding="utf-8")
    defects, _advisory, row = ov.vote_note(note, tmp_path, require_witness=True)
    assert row["claims"] == 1 and row["flag"] == 1
    assert "E-NO-PIXEL-WITNESS" in defects[0]
    assert "Kajabi" in defects[0]


def pixel_corpus(tmp_path: Path, rows: list[str], body: str, extra: str = "") -> Path:
    """A corpus root holding one note and the OCR index its frontmatter names."""
    index = ov.ocr_path(tmp_path, "TESTID")
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(ocr_index(rows) + extra, encoding="utf-8")
    note = tmp_path / "notes" / "2026-01-01--a--TESTID.md"
    note.write_text(ocr_note(body), encoding="utf-8")
    return note


# A frame that reads back with no words on it: a blank slide, a hard cut, a
# camera shot. Legible frames beside it are what a witness needs.
BLANK_FRAMES = [ocr_row(f"full_0{i}m00s.jpg", 60 * i, "", words=0) for i in range(1, 6)]
LEGIBLE_FRAMES = [
    ocr_row(f"full_2{i}m56s.jpg", 1256 + 60 * i,
            f"a slide about writing systems and leverage number {i} entirely")
    for i in range(5)]


def test_a_run_under_the_textful_floor_never_reaches_the_pixel_vote(tmp_path):
    """The first of two silencing conditions the `refuses` line has to state.

    Under a quarter textful the pixel channel is declared unavailable and
    `vote_note` returns before the vote loop, so `--require-witness` on a run of
    blank frames refuses nothing at all. The legible index beside it is the
    control: same note, same absent name, and the refusal fires.
    """
    claim = "- `ON-SCREEN` the Kajabi dashboard is open `[27:56]`"

    blank = pixel_corpus(tmp_path / "blank", BLANK_FRAMES, claim)
    defects, advisory, row = ov.vote_note(blank, tmp_path / "blank",
                                          require_witness=True)

    assert defects == [], "asked for a witness, and never asked the pixels"
    assert "0 of 5 frames are textful (0%, under 25%)" in advisory[0]
    assert (row["claims"], row["flag"]) == (0, 0)

    legible = pixel_corpus(tmp_path / "legible", LEGIBLE_FRAMES, claim)
    refused, _advisory, control = ov.vote_note(legible, tmp_path / "legible",
                                               require_witness=True)

    assert len(refused) == 1, refused
    assert "E-NO-PIXEL-WITNESS" in refused[0] and "Kajabi" in refused[0]
    assert (control["claims"], control["flag"]) == (1, 1)


def test_one_malformed_index_row_stops_the_vote_before_any_name(tmp_path):
    """The second silencing condition: any index defect returns before the vote.

    One truncated row among five good ones is reported as a row defect, and the
    unwitnessed proper noun beside it is never examined. A reader who takes a
    clean `--require-witness` run as proof the pixels were asked is wrong in
    both of these directions.
    """
    note = pixel_corpus(tmp_path, LEGIBLE_FRAMES,
                        "- `ON-SCREEN` the Kajabi dashboard is open `[27:56]`",
                        extra="truncated\trow\n")

    defects, advisory, row = ov.vote_note(note, tmp_path, require_witness=True)

    assert len(defects) == 1, defects
    assert "E-OCR-ROW" in defects[0] and "malformed row" in defects[0]
    assert "E-NO-PIXEL-WITNESS" not in " ".join(defects)
    assert (advisory, row["claims"]) == ([], 0)


def test_a_claim_whose_only_name_is_all_capitals_yields_no_candidate(tmp_path):
    """An all-capitals token is an acronym, not a proper noun worth a witness.

    Driven through `vote_note` rather than through the tokeniser alone, because
    the neighbour's claim is that such a block is NEVER REFUSED -- and that half
    lives in the vote, not in the token list. The mixed-case name beside it is
    the control: the same index, the same window, and a refusal.
    """
    caps = pixel_corpus(tmp_path / "caps", LEGIBLE_FRAMES,
                        "- `ON-SCREEN` the OKAPILANE and OBS panels are open `[27:56]`")

    defects, advisory, row = ov.vote_note(caps, tmp_path / "caps",
                                          require_witness=True)

    assert ov.candidate_tokens("the OKAPILANE and OBS panels are open") == []
    assert (defects, advisory) == ([], [])
    assert (row["claims"], row["tokens"], row["flag"]) == (0, 0, 0)

    mixed = pixel_corpus(tmp_path / "mixed", LEGIBLE_FRAMES,
                         "- `ON-SCREEN` the Okapilane panel is open `[27:56]`")

    refused, _advisory, control = ov.vote_note(mixed, tmp_path / "mixed",
                                               require_witness=True)

    assert len(refused) == 1, refused
    assert "E-NO-PIXEL-WITNESS" in refused[0] and "Okapilane" in refused[0]
    assert (control["claims"], control["tokens"]) == (1, 1), "a name once it is not shouted"


# ==========================================================================
# spoken_vote.score_note -- E-CAPTION-UNINDEXED
# ==========================================================================

CAPTION_HEAD = ('---\ntitle: t\nvideo_id: {vid}\nduration: "10:00"\n---\n\n'
                "# t\n\n## Claims\n\n")
CAPTION_ITEM = ("- `[00:12]` `SPOKEN` the cohort retention curve is the chart "
                "that matters\n")
CUES = [(10.0, 13.0, "cohort retention curve is the chart"),
        (13.0, 16.0, "that matters most for product market fit")]


def caption_corpus(tmp_path: Path, monkeypatch, track_for: str | None) -> Path:
    """A corpus root, with a caption TRACK on disk for `track_for` and no index.

    `find_tracks` is redirected at its roots rather than replaced: the search is
    the real one, over a directory this test owns.
    """
    (tmp_path / "notes").mkdir()
    tracks = tmp_path / "tracks"
    tracks.mkdir()
    if track_for is not None:
        run = tracks / track_for / "download"
        run.mkdir(parents=True)
        (run / "video.en-orig.vtt").write_text(
            "WEBVTT\n\n00:10.000 --> 00:13.000\ncohort retention curve is the chart\n",
            encoding="utf-8")
    monkeypatch.setattr(sv, "find_tracks",
                        lambda vid: sc.find_tracks(vid, roots=(tracks,)))
    return tmp_path


def test_a_caption_track_with_no_index_beside_it_is_refused(tmp_path, monkeypatch):
    """A clearable defect: the track is there and one command would index it."""
    root = caption_corpus(tmp_path, monkeypatch, track_for="TESTID")
    note = root / "notes" / "2026-01-01--a--TESTID.md"
    note.write_text(CAPTION_HEAD.format(vid="TESTID") + CAPTION_ITEM,
                    encoding="utf-8")

    defects, row = sv.score_note(note, root)

    assert len(defects) == 1, defects
    assert "E-CAPTION-UNINDEXED" in defects[0]
    assert "run --index=TESTID" in defects[0], "the refusal must name the repair"
    assert row["index"] == "missing"


def test_an_index_holding_no_cue_is_a_different_refusal(tmp_path, monkeypatch):
    """An index that exists and is empty is E-CAPTION-EMPTY, not this rule.

    The two are one keystroke apart in effect and a whole repair apart in
    meaning: one says build the index, the other says the index you built is
    wrong.
    """
    root = caption_corpus(tmp_path, monkeypatch, track_for="TESTID")
    sv.write_index(root, "TESTID", [])
    note = root / "notes" / "2026-01-01--a--TESTID.md"
    note.write_text(CAPTION_HEAD.format(vid="TESTID") + CAPTION_ITEM,
                    encoding="utf-8")

    defects, _row = sv.score_note(note, root)

    assert len(defects) == 1, defects
    assert "E-CAPTION-EMPTY" in defects[0]
    assert "E-CAPTION-UNINDEXED" not in defects[0]


def test_a_track_for_a_neighbouring_video_witnesses_nothing_here(tmp_path, monkeypatch):
    """A track on disk for another id is not this note's missing index.

    The search is per video id, and a corpus holds many; matching on "some
    track exists" would refuse every note in it.
    """
    root = caption_corpus(tmp_path, monkeypatch, track_for="NEIGHBOUR")
    note = root / "notes" / "2026-01-01--a--TESTID.md"
    note.write_text(CAPTION_HEAD.format(vid="TESTID") + CAPTION_ITEM,
                    encoding="utf-8")

    defects, row = sv.score_note(note, root)

    assert defects == []
    assert row["index"] == "missing"
    assert row["video_id"] == "TESTID"


# ==========================================================================
# note_coverage.defects -- E-COV-DEAD
# ==========================================================================

def test_a_stretch_nobody_wrote_up_is_refused_whatever_the_reason(tmp_path):
    """The known false positive, written down rather than papered over.

    A sponsor read, a long silence and a stretch of banter are all legitimately
    unwritten, and this rule cannot tell them from a stretch that was skipped.
    It refuses anyway, and its own docstring says it is a question rather than a
    verdict -- so what the case pins is that the question still gets asked.
    """
    note = "".join(cov_row(m) for m in (0, 1, 2, 6, 7, 8, 9))

    found = cov_defects(tmp_path, note, cov_spoken())

    assert codes_in(found) == {"E-COV-DEAD"}, found
    dead = named(found, "E-COV-DEAD")
    assert dead[0].startswith("[03:00] "), "the run begins at minute three"
    assert "3 consecutive minute(s)" in dead[0]


def test_a_rail_of_bare_anchors_does_not_fill_the_holes_it_covers(tmp_path):
    """The escape an adversarial lane already won with, driven through `measure`.

    One anchor per unwritten minute, on a line carrying nothing else, renders as
    a rail of timestamps and says not one thing about those minutes. If a bare
    anchor counted, three consecutive holes would close and the note would
    report a span it never wrote from -- which is how a twenty-nine-minute gap
    was made to disappear, by hiding the rail inside an HTML comment.

    The same note without the rail is the control: the run is three either way,
    so what the rail changes must be nothing.
    """
    written = "".join(cov_row(m) for m in (0, 1, 2, 6, 7, 8, 9))
    rail = "`[03:10]` `[04:10]` `[05:10]`\n"
    segments = cov_spoken()

    railed = cov_measure(tmp_path, written + rail, segments)
    bare = cov_measure(tmp_path, written, segments)
    found = nc.defects(railed)

    assert railed["anchors"] == bare["anchors"] == 7, "the rail's three do not count"
    assert railed["dead"]["longest_run"] == 3
    assert railed["dead"]["longest_run_at"] == 180.0
    assert codes_in(found) == {"E-COV-DEAD"}, found
    assert "3 consecutive minute(s)" in named(found, "E-COV-DEAD")[0]


# ==========================================================================
# note_coverage.defects -- E-COV-DUMP
# ==========================================================================

def test_a_paste_that_stamps_every_line_is_still_a_paste(tmp_path):
    """The digits of `[MM:SS]` used to break the very runs this looks for.

    A pasted transcript writes an anchor before every line, and those anchors
    tokenise -- the paste measured 0% verbatim until they were taken out first.
    """
    segments = cov_transcript(30)
    said = " ".join(s["text"] for s in segments)
    paste = "".join(f"[{i:02d}:00] {said}\n" for i in range(4))

    result = cov_measure(tmp_path, paste, segments)
    found = nc.defects(result)

    assert result["verbatim_share"] == 1.0
    dump = named(found, "E-COV-DUMP")
    assert len(dump) == 1, found
    assert "100% of this note is a verbatim run" in dump[0]


def test_a_paste_with_one_word_changed_every_ten_escapes_the_verbatim_run(tmp_path):
    """The escape an adversary can type today.

    The share looks for runs of twelve tokens, and a word changed every tenth
    breaks every one of them while a reader still sees the recording's text.
    The clean paste beside it is what proves the fixture is otherwise a paste.
    """
    said = " ".join(cov_transcript(30)[i]["text"] for i in range(30)).split()
    segments = [{"start": i * 10.0, "end": i * 10.0 + 10.0,
                 "text": " ".join(said[i:i + 12])}
                for i in range(0, len(said), 12)]
    mangled = " ".join("QQQ" if i % 10 == 9 else w for i, w in enumerate(said))

    escaped = cov_measure(tmp_path, mangled, segments)
    clean = cov_measure(tmp_path, " ".join(said), segments)

    assert escaped["verbatim_share"] == 0.0
    assert named(nc.defects(escaped), "E-COV-DUMP") == []
    assert clean["verbatim_share"] == 1.0
    assert len(named(nc.defects(clean), "E-COV-DUMP")) == 1


def test_a_note_shorter_than_one_verbatim_run_scores_zero(tmp_path):
    """Under twelve words there is no twelve-word run to find, so the share is 0.

    Not a defect and not a pass: the number simply cannot mean anything, which
    is why it is stated rather than inferred.
    """
    result = cov_measure(tmp_path,
                         "- `[00:01]` `SPOKEN` — the price was 500 dollars.\n",
                         cov_transcript(30))

    assert result["note_words"] < nc.VERBATIM_RUN
    assert result["verbatim_share"] == 0.0
    assert named(nc.defects(result), "E-COV-DUMP") == []


# ==========================================================================
# note_coverage.defects -- E-COV-LIST
# ==========================================================================

def test_an_alphabetised_dump_of_the_vocabulary_is_a_word_list(tmp_path):
    """Perfect recall, no claims: the shape recall alone cannot tell from work."""
    segments = cov_transcript()
    vocabulary = set().union(*nc.salient(segments, None).values())
    note = " ".join(sorted(vocabulary))

    result = cov_measure(tmp_path, note, segments)
    found = nc.defects(result)

    assert result["recall"]["all"]["share"] == 1.0, "it carries everything"
    listed = named(found, "E-COV-LIST")
    assert len(listed) == 1, found
    assert f"{len(vocabulary)} carried token(s)" in listed[0]


def test_one_claim_and_a_vocabulary_appendix_is_still_a_word_list(tmp_path):
    """A claim in front of a dump does not stop it being a dump.

    Measured across the corpus a real note runs 0.016 to 0.115 carried tokens
    per word; one claim plus an appendix measures 0.61, which is why the
    ceiling is where it is.
    """
    segments = cov_transcript()
    vocabulary = set().union(*nc.salient(segments, None).values())
    note = ("- `[00:01]` `SPOKEN` — the speaker opens with a single argument "
            "that runs for several sentences and makes one point.\n\n"
            + " ".join(sorted(vocabulary)))

    result = cov_measure(tmp_path, note, segments)

    assert result["list_density"] > nc.LIST_DENSITY
    assert len(named(nc.defects(result), "E-COV-LIST")) == 1


def test_padding_a_dump_with_filler_drops_it_under_the_density_ceiling(tmp_path):
    """The escape: the ratio is per WORD OF NOTE, so words are what buys it off.

    The same vocabulary, carried by the same dump, with enough filler around it
    to drop the density under the ceiling. Nothing else here notices.
    """
    segments = cov_transcript()
    vocabulary = set().union(*nc.salient(segments, None).values())
    dump = " ".join(sorted(vocabulary))
    filler = ("the speaker then goes on at some length about this and returns "
              "to it again later in a way that adds little but words ") * 12

    padded = cov_measure(tmp_path, dump + "\n\n" + filler, segments)
    bare = cov_measure(tmp_path, dump, segments)

    assert bare["recall"]["all"]["carried"] == padded["recall"]["all"]["carried"]
    assert padded["list_density"] < nc.LIST_DENSITY < bare["list_density"]
    assert named(nc.defects(padded), "E-COV-LIST") == []


def test_a_thorough_note_that_spends_real_words_is_not_a_word_list(tmp_path):
    """The direction that matters most: a good note must not be marked down.

    It carries the whole vocabulary and spends twenty-odd words per token doing
    it, which is what the ceiling is set to allow.
    """
    segments = cov_transcript()
    note = "".join(
        f"- `[{i // 6:02d}:{(i % 6) * 10:02d}]` `SPOKEN` — {coined(i)}zone comes "
        f"up at this point, and the speaker gives {coined(200 + i)} as the "
        f"reason for it, which the note records in full.\n"
        for i in range(40))

    result = cov_measure(tmp_path, note, segments)

    assert result["recall"]["all"]["share"] == 1.0
    assert result["list_density"] < nc.LIST_DENSITY
    assert named(nc.defects(result), "E-COV-LIST") == []


# ==========================================================================
# note_coverage.defects -- E-COV-REPEAT
# ==========================================================================

def test_a_claim_restated_six_rows_later_is_outside_the_window(tmp_path):
    """Deliberate restatement in a later section is not padding.

    The sweep reaches five rows back on purpose: padding and an overlapped seam
    are both local, and a full pairwise sweep would refuse a note that returns
    to its own thesis.
    """
    rows = [
        "the price of the licence was raised in the spring",
        "a second entirely different observation follows here",
        "a third observation about something else again now",
        "a fourth observation on a different subject entirely",
        "a fifth observation that shares nothing with them",
        "a sixth observation unrelated to all of the above",
        "the price of the licence was raised in the spring",
    ]
    note = "".join(f"- `[{i:02d}:01]` `SPOKEN` — {t}.\n"
                   for i, t in enumerate(rows))

    result = cov_measure(tmp_path, note, cov_transcript())

    assert result["rows"] == 7
    assert result["near_duplicate_rows"] == 0, "six rows apart is out of reach"
    assert named(nc.defects(result), "E-COV-REPEAT") == []


def test_exactly_one_row_in_ten_repeating_sits_on_the_ceiling(tmp_path):
    """0.10 is the ceiling and not a breach: the test is strictly greater.

    The repeat sits four rows after the row it repeats, well inside the reach
    of five, so it is the SHARE and not the distance that decides this one.
    """
    rows = [
        "the licence price was raised in the spring of that year",
        "the second observation concerns an entirely separate matter",
        "a third point is made about shipping times in the north",
        "the fourth remark covers how the warehouse was rebuilt",
        "the licence price was raised in the spring of that year",
        "the sixth item is about staffing over the winter months",
        "a seventh remark on why the catalogue was reprinted",
        "an eighth point about the cost of moving the offices",
        "a ninth observation regarding the new supplier contract",
        "a tenth note on the rebuilding of the eastern depot",
    ]
    note = "".join(f"- `[00:{i * 5 + 1:02d}]` `SPOKEN` — {t}.\n"
                   for i, t in enumerate(rows))

    result = cov_measure(tmp_path, note, cov_transcript())

    assert (result["rows"], result["near_duplicate_rows"]) == (10, 1)
    assert result["near_duplicate_share"] == nc.MAX_NEAR_SHARE
    assert named(nc.defects(result), "E-COV-REPEAT") == []


def test_a_seam_reconciled_twice_is_refused_as_padding(tmp_path):
    """A known false positive: two windows saw one claim and both wrote it.

    Note windows overlap on purpose so a claim made across a boundary is seen
    twice and reconciled once. Until it is reconciled the two rows are minutes
    apart, say the same thing, and are refused here as padding.
    """
    note = ("- `[00:01]` `SPOKEN` — the licence price was raised in the spring "
            "of that year.\n"
            "- `[05:31]` `SPOKEN` — the licence price was raised in the spring "
            "of that year.\n")

    result = cov_measure(tmp_path, note, cov_transcript())
    found = nc.defects(result)

    assert result["rows"] == 2 and result["near_duplicate_rows"] == 1
    repeat = named(found, "E-COV-REPEAT")
    assert len(repeat) == 1, found
    assert "1 of 2 rows repeat a row beside them" in repeat[0]


# ==========================================================================
# note_coverage -- E-COV-ROWSHAPE
# ==========================================================================

def test_a_sixth_misshapen_line_is_counted_rather_than_dropped(tmp_path):
    """round-14 F5 — the cap said nothing about itself, and the sibling does.

    `note_windows` prints a further-orphans line for exactly this reason, and
    its own comment says a cap that hides what mattered is the same failure as
    not printing it. Here the sixth misshapen line was measured, counted, and
    then dropped without a word -- so a reader shown 32 lines over a corpus
    carrying 105 had no way to tell the cap from the count.

    Would fail if: the remainder line goes away, or the cap changes without it.
    """
    note = "".join(f"- `[00:{i:02d}]` `SPOKEN` a claim with no dash at all "
                   f"number {i}.\n" for i in range(6))

    result = cov_measure(tmp_path, note, cov_transcript())
    found = nc.defects(result)

    assert len(result["misshapen_rows"]) == 6, "all six are seen"
    shapes = named(found, "E-COV-ROWSHAPE")
    assert len(shapes) == nc.ROWSHAPE_SHOWN + 1, shapes
    assert "6 in total" in shapes[-1], shapes[-1]


def test_a_row_written_with_an_en_dash_is_a_row(tmp_path):
    """Three dashes are typed in this corpus and all three are rows.

    An earlier pattern accepted only the em dash, which meant the padding check
    silently had nothing to look at on a note written with either of the others.
    """
    path = tmp_path / "note.md"
    for dash in ("—", "–", "-"):
        path.write_text(f"- `[00:01]` `SPOKEN` {dash} a claim.\n", encoding="utf-8")

        _body, rows, impossible, misshapen = nc.read_note(path)

        assert len(rows) == 1, f"{dash!r} did not read as a row"
        assert rows[0] == (1.0, "SPOKEN", "a claim.", 1.0)
        assert (impossible, misshapen) == ([], [])


def test_a_row_spanning_two_stamps_is_a_row(tmp_path):
    """A claim that took two minutes to make is written across two anchors.

    This form is in use and neither the contract nor the corpus README ever
    forbade it, so a note that used it was convicted of a misshapen row for
    writing down where a claim ended. The row is anchored at where it STARTS,
    which is what every other row means by its stamp.
    """
    path = tmp_path / "note.md"
    path.write_text(
        "- `[01:57]` `SPOKEN` to `[04:23]` `SPOKEN` — a claim that ran on.\n",
        encoding="utf-8")

    _body, rows, impossible, misshapen = nc.read_note(path)

    assert len(rows) == 1, "the range form did not read as a row"
    assert rows[0] == (1 * 60 + 57.0, "SPOKEN", "a claim that ran on.",
                       4 * 60 + 23.0)
    assert (impossible, misshapen) == ([], [])


def test_a_row_that_says_to_and_names_no_second_stamp_is_still_misshapen(tmp_path):
    """The neighbour that keeps the widening honest.

    Accepting the range form must not accept a line that merely contains the
    word. Without this case the pattern could be widened to swallow anything
    between the class and the dash, and the check would stop reporting the
    shape it exists to report.
    """
    path = tmp_path / "note.md"
    path.write_text(
        "- `[01:57]` `SPOKEN` to the end of the section — a claim.\n",
        encoding="utf-8")

    _body, rows, impossible, misshapen = nc.read_note(path)

    assert rows == [], "a line with no second anchor is not a range row"
    assert len(misshapen) == 1, "and it is reported rather than dropped"


def test_a_range_whose_end_is_not_a_time_is_reported(tmp_path):
    """The second anchor is held to the clock the first one is held to.

    The range form was added by widening the pattern, and the widening validated
    only what it captured -- the START. So `[00:99]` was named in a row that
    ended there and silent in a row that ran to there, which is the same typed
    anchor nobody checked, reported or not depending on which end it sat at.
    """
    path = tmp_path / "note.md"
    path.write_text(
        "- `[00:00]` `SPOKEN` to `[00:99]` `SPOKEN` — a claim that ran on.\n",
        encoding="utf-8")

    _body, rows, impossible, misshapen = nc.read_note(path)

    assert rows == [], "a range ending at an unsayable stamp is not a row"
    assert impossible == ["00:99"], "and the stamp is named"
    assert misshapen == []


def test_a_range_that_ends_before_it_starts_is_not_a_row(tmp_path):
    """A claim cannot finish fifty seconds before it begins.

    Both stamps are sayable, so neither is an impossible stamp; what is wrong is
    the pair. Before the range form existed this line did not parse at all and
    was reported as a shape nothing counted, and it must not become a row now
    that the shape is understood.
    """
    path = tmp_path / "note.md"
    path.write_text(
        "- `[01:00]` `SPOKEN` to `[00:10]` `SPOKEN` — a claim that went back.\n",
        encoding="utf-8")

    _body, rows, impossible, misshapen = nc.read_note(path)

    assert rows == [], "a backwards range is not a row"
    assert impossible == [], "and neither stamp is unsayable on its own"
    assert len(misshapen) == 1, "the line is reported rather than dropped"


def test_a_row_the_parser_refused_carries_no_anchors(tmp_path):
    """V5 gap a — the half-credit a refused row kept.

    `measure` builds its anchor list from the note BODY, so a line the row
    parser threw out still filled the dead-stretch buckets its two stamps sat
    in. Measured on a thirty-minute transcript: the forwards row gave 1 row and
    2 anchors, the backwards row gave 0 rows and the SAME 2 anchors, and both
    reported the same dead minutes -- a note scoring identical coverage whether
    its row parsed or not.

    A line that was never row-shaped is prose and keeps its anchors; that is
    the neighbour below. This is about a line that TRIED to be a row and was
    refused.

    Would fail if: the anchor walk stops asking `_row_of` and goes back to
    reading every line of the body alike.
    """
    segments = cov_transcript(180)  # thirty minutes
    forwards = ("- `[05:00]` `SPOKEN` to `[06:00]` `SPOKEN` — "
                "the speaker explains the zone at some length.\n")
    backwards = ("- `[06:00]` `SPOKEN` to `[05:00]` `SPOKEN` — "
                 "the speaker explains the zone at some length.\n")

    other = tmp_path / "b"
    other.mkdir()
    good = cov_measure(tmp_path, forwards, segments)
    bad = cov_measure(other, backwards, segments)

    assert good["rows"] == 1 and good["anchors"] == 2, good
    assert bad["rows"] == 0, bad
    assert bad["misshapen_rows"], "the line is still reported as misshapen"
    assert bad["anchors"] == 0, "a refused row buys no coverage"
    assert bad["anchors_on_refused_rows"] == 2, bad
    # And the consequence a reader acts on moves with it.
    assert bad["dead"]["minutes"] > good["dead"]["minutes"], (good, bad)


def test_a_shape_the_parser_never_learned_keeps_its_anchors(tmp_path):
    """round-13 F2 — the refusal was superstitious about shape.

    A claim written across two stamps with no joining word is a form `RE_ROW`
    has never read. It was already reported as a row shape nothing counts; what
    the first version of this change added was forfeiting its anchors as well,
    and the dead-minute count a reader acts on moved because of it. Measured
    over the frozen corpus: 234 anchors and 8 dead minutes lost across 8 notes,
    on lines each carrying seven to sixteen words of written claim, and 95 of
    the 105 refused lines were this one shape.

    So the two refusals are told apart. A line the parser READ and refused --
    a range that ends before it starts, a stamp no clock can say -- buys
    nothing. A line the parser could not read at all is reported as before and
    keeps its anchors, because the note did write from that stretch and the
    parser not knowing the form is not evidence that it did not.

    Would fail if: `_row_of` stops distinguishing UNREAD from MISSHAPEN.
    """
    segments = cov_transcript(180)
    unlearned = ("- `[05:00]` `SPOKEN` `[06:00]` `SPOKEN` — "
                 "the speaker explains the zone at some length.\n")

    got = cov_measure(tmp_path, unlearned, segments)

    assert got["rows"] == 0, "the parser still cannot read this form"
    assert got["misshapen_rows"], "and it is still reported as a shape"
    assert got["anchors"] == 2, "but the stretch was written from"
    assert got["anchors_on_refused_rows"] == 0, got


@pytest.mark.parametrize("line", [
    "| `[05:00]` `SPOKEN` | the speaker explains the zone at some length. |",
    "At `[05:00]` the speaker explains the zone at some length here.",
    "- `[05:00]` `SPOKEN` `[06:00]` `SPOKEN` — the speaker explains the zone.",
])
def test_one_claim_written_three_ways_keeps_its_anchors_all_three(tmp_path, line):
    """round-13 F8 — the refusal was launderable by changing the bullet.

    `RE_ROWISH` anchors on `^\\s*[-*]`, so identical content kept its anchors as
    a table row or a sentence and forfeited them as a bullet. A note that
    wanted its coverage back would have changed `- ` to `| `. Whatever else is
    true of these three lines, they say the same thing about the same second
    and they may not be scored differently for their punctuation.
    """
    got = cov_measure(tmp_path, line + "\n", cov_transcript(180))
    assert got["anchors"] >= 1, got
    assert got["anchors_on_refused_rows"] == 0, got


def test_the_cost_of_a_refusal_is_counted_in_anchors_a_bucket_would_have_seen(
        tmp_path):
    """round-13 F6 — the number overstated what the refusal cost.

    Two ways. A refused line too thin to have been read at all was counted,
    though `MIN_LINE_WORDS` would have dropped every one of its stamps anyway;
    and one second written twice on one line was counted twice, though it is
    one bucket. Neither occurs in the frozen corpus, so this is a number
    printed for a human that would have misled one.
    """
    segments = cov_transcript(180)

    thin = "- `[05:00]` `X` to `[04:00]` `X` —\n"
    got = cov_measure(tmp_path, thin, segments)
    assert got["rows"] == 0 and got["anchors"] == 0, got
    assert got["anchors_on_refused_rows"] == 0, "too thin to have counted"

    twice = tmp_path / "b"
    twice.mkdir()
    repeated = ("- `[05:00]` `SPOKEN` to `[04:00]` `SPOKEN` — "
                "at `[05:00]` alpha bravo charlie delta echo\n")
    got = cov_measure(twice, repeated, segments)
    assert got["rows"] == 0 and got["anchors"] == 0, got
    # Three stamps on the line, two seconds, two buckets. The second written
    # twice is one bucket and is counted once.
    assert got["anchors_on_refused_rows"] == 2, got


def test_prose_that_was_never_a_row_keeps_its_anchors(tmp_path):
    """The neighbour that keeps the change above from being too wide.

    Notes in this corpus cite seconds inside prose and inside tables as well as
    in rows, and a dead-stretch count drawn from rows alone once reported 29
    unwritten minutes in a note that had written from all of them. Only a line
    the ROW PARSER refused loses its anchors.
    """
    segments = cov_transcript(180)
    prose = ("At `[05:00]` the speaker explains the zone at some length "
             "and returns to it at `[06:00]` before moving on.\n")

    got = cov_measure(tmp_path, prose, segments)

    assert got["rows"] == 0, "prose is not a row"
    assert got["misshapen_rows"] == [], "and it is not a refused one either"
    assert got["anchors"] == 2, got
    assert got["anchors_on_refused_rows"] == 0, got


def test_a_range_ending_on_an_unsayable_stamp_carries_no_anchors_either(tmp_path):
    """The other refusal, which the same walk has to reach.

    `impossible` and `misshapen` are two names for one thing here: the parser
    would not make a row of the line. A fix written against the backwards range
    alone would leave the unsayable end still buying its start's bucket.
    """
    segments = cov_transcript(180)
    note = ("- `[05:00]` `SPOKEN` to `[00:99]` `SPOKEN` — "
            "the speaker explains the zone at some length.\n")

    got = cov_measure(tmp_path, note, segments)

    assert got["rows"] == 0 and got["impossible_stamps"] == ["00:99"], got
    assert got["anchors"] == 0, got
    assert got["anchors_on_refused_rows"] == 1, "only the sayable one is an anchor"


def test_a_backwards_range_reaches_the_reader_as_a_defect(tmp_path):
    """The pair above is only a finding if it survives to `defects`.

    `read_note` collecting a line into `misshapen_rows` proves the parser
    refused it; this proves the refusal is printed, which is the half the
    widening actually cost.
    """
    note = "- `[01:00]` `SPOKEN` to `[00:10]` `SPOKEN` — a claim that went back.\n"

    result = cov_measure(tmp_path, note, cov_transcript())
    found = nc.defects(result)

    assert result["misshapen_rows"], "the row was refused"
    assert len(named(found, "E-COV-ROWSHAPE")) == 1, found


# ==========================================================================
# note_coverage -- E-COV-STAMP
# ==========================================================================

def test_an_impossible_stamp_in_prose_is_never_reported(tmp_path):
    """Only stamps on matched claim rows are collected.

    Every other anchor path swallows the same error and moves on, so the same
    unsayable stamp is named in a row and silent in a sentence. The row form
    beside it is what shows the difference is the shape and not the stamp.
    """
    prose = ("The speaker returns to the subject at `[59:99]` and says a good "
             "deal more about it.\n")
    row = "- `[59:99]` `SPOKEN` — a stamp no clock can say.\n"

    in_prose = cov_measure(tmp_path, prose, cov_transcript())
    in_row = cov_measure(tmp_path, row, cov_transcript())

    assert in_prose["impossible_stamps"] == []
    assert named(nc.defects(in_prose), "E-COV-STAMP") == []
    assert in_row["impossible_stamps"] == ["59:99"]
    assert "`[59:99]` is not a time" in named(nc.defects(in_row), "E-COV-STAMP")[0]


def test_an_impossible_stamp_does_not_stop_the_rows_after_it_being_read(tmp_path):
    """One bad row must not cost the note every row written below it.

    `read_note` collects the stamp and carries on. The difference between
    collecting and stopping is invisible on a note whose only row is the bad one
    -- which is why this fixture puts nine good rows AFTER it and counts them.
    A reader who stopped would see one row, no dead stretch, and a note that
    looks like it wrote nothing.
    """
    note = (cov_row(0)
            + "- `[59:99]` `SPOKEN` — a stamp that no clock anywhere can ever say.\n"
            + "".join(cov_row(m) for m in range(1, 10)))

    result = cov_measure(tmp_path, note, cov_spoken())
    found = nc.defects(result)

    assert result["impossible_stamps"] == ["59:99"]
    assert result["rows_in_note"] == 10, "the nine rows below the bad one still read"
    assert result["rows"] == 10
    assert result["recall"]["all"]["carried"] > 0, "and they were measured"
    assert codes_in(found) == {"E-COV-STAMP"}, found


# ==========================================================================
# note_coverage -- E-COV-THIN
# ==========================================================================

def test_exactly_the_salient_floor_is_not_refused_as_thin(tmp_path):
    """25 salient tokens is the floor, and a floor is not a breach.

    One token below it the recall figure is called noise; on it, it stands.
    """
    def span_of(count: int) -> list[dict]:
        return [{"start": 0.0, "end": 10.0,
                 "text": "The speaker names "
                         + " ".join(f"{coined(i)}zone" for i in range(count))
                         + " here."}]

    note = "- `[00:01]` `SPOKEN` — a claim about the subject at hand here.\n"
    on_the_floor = cov_measure(tmp_path, note, span_of(22))
    below_it = cov_measure(tmp_path, note, span_of(21))

    assert on_the_floor["recall"]["all"]["of"] == nc.MIN_SALIENT
    assert named(nc.defects(on_the_floor), "E-COV-THIN") == []
    assert below_it["recall"]["all"]["of"] == nc.MIN_SALIENT - 1
    assert len(named(nc.defects(below_it), "E-COV-THIN")) == 1


def test_a_span_flag_makes_the_callers_window_thin_not_the_note(tmp_path):
    """The thinness belongs to the window the caller asked about.

    The same recording and the same note: over the whole file there is plenty
    to carry, and over twenty seconds of it there is not. The refusal is about
    the comparison being quotable, so it fires on the narrow window and says
    how few tokens were in it.
    """
    segments = cov_transcript()
    note = "- `[00:05]` `SPOKEN` — a claim about the opening minute of the talk.\n"

    narrow = cov_measure(tmp_path, note, segments, span=(0.0, 20.0))
    whole = cov_measure(tmp_path, note, segments)

    thin = named(nc.defects(narrow), "E-COV-THIN")
    assert len(thin) == 1
    assert f"only {narrow['recall']['all']['of']} salient token(s)" in thin[0]
    assert narrow["recall"]["all"]["of"] < nc.MIN_SALIENT
    assert named(nc.defects(whole), "E-COV-THIN") == []


# ==========================================================================
# note_windows.main -- E-WIN-EMPTY
# ==========================================================================

def test_a_window_landing_in_a_long_hole_holds_no_segment(tmp_path):
    """A boundary computed from the clock can land where nothing was said."""
    segments = [{"start": 0.0, "end": 10.0, "text": "first thing said"},
                {"start": 2400.0, "end": 2410.0, "text": "much later"},
                {"start": 4000.0, "end": 4010.0, "text": "later still"}]

    code, defects = run_windows(tmp_path, segments)

    empty = named(defects, "E-WIN-EMPTY")
    assert code == 1
    assert len(empty) == 5, defects
    assert "window 2 holds no segment" in empty[0]
    assert empty[0].startswith("[08:30] "), "and says where the boundary fell"


def test_an_uploader_chapter_the_transcript_never_witnessed_is_empty(tmp_path):
    """Chapters beat arithmetic, and a chapter can still cover unheard audio.

    The uploader marked a subject that the decode has nothing for, so the
    window that would be written from it holds no segment at all.
    """
    segments = [{"start": i * 10.0, "end": i * 10.0 + 10.0,
                 "text": f"segment number {i} said a few words"} for i in range(60)]
    chapters = {"chapters": [
        {"start_time": 0.0, "end_time": 600.0, "title": "The setup"},
        {"start_time": 1800.0, "end_time": 2400.0, "title": "The unheard part"}]}

    code, defects = run_windows(tmp_path, segments, chapters=chapters)

    empty = named(defects, "E-WIN-EMPTY")
    assert code == 1
    assert len(empty) == 1, defects
    assert "window 2 holds no segment" in empty[0]
    assert empty[0].startswith("[30:00] ")


# ==========================================================================
# note_windows.main -- E-WIN-ORPHAN
# ==========================================================================

def test_an_eleventh_orphan_is_counted_rather_than_named(tmp_path):
    """A printed list that stops at ten and does not say so reads as ten.

    Twelve segments a decoder stamped before the file began land in no window;
    ten are named and the other two are counted, out loud.
    """
    segments = [{"start": -100.0 - i, "end": -50.0 - i,
                 "text": f"stamped before the file began {i}"} for i in range(12)]
    segments += [{"start": i * 10.0, "end": i * 10.0 + 10.0,
                  "text": f"segment number {i} said a few words"} for i in range(60)]

    code, defects = run_windows(tmp_path, segments)

    orphans = named(defects, "E-WIN-ORPHAN")
    assert code == 1
    assert len(orphans) == nw.ORPHANS_SHOWN + 1, defects
    assert sum("lies in no window" in line for line in orphans) == nw.ORPHANS_SHOWN
    assert "2 further orphaned segment(s) are not listed above" in orphans[-1]


# ==========================================================================
# note_windows.main -- E-WIN-OVERFULL
# ==========================================================================

def test_a_window_holding_exactly_half_the_segments_is_under_the_ceiling(tmp_path):
    """Half the segments in one window of a real split is not a refusal.

    Two clusters either side of one boundary, thirty segments each: the split
    happened, and a rule that fired here would refuse a plan that worked.
    """
    segments = [{"start": i * 9.0, "end": i * 9.0 + 8.0,
                 "text": f"an early remark number {i}"} for i in range(30)]
    segments += [{"start": 330.0 + j * 9.0, "end": 338.0 + j * 9.0,
                  "text": f"a later remark number {j}"} for j in range(30)]

    plan = nw.plan(segments, 300.0, 0.0)
    code, defects = run_windows(tmp_path, segments, "--window", "300",
                                "--overlap", "0")

    assert [w["segments"] for w in plan] == [30, 30]
    # Half of the segments, against the one share the rule still reads, and no
    # segment stamped across more than a window.
    assert max(w["segments"] for w in plan) < len(segments) * nw.OVERFULL_CEILING
    assert max(s["end"] - s["start"] for s in segments) < 300.0
    assert (code, defects) == (0, [])


def test_a_front_loaded_recording_is_refused_as_an_unsplit_plan(tmp_path):
    """A known false positive: some recordings really are front-loaded.

    Five hundred remarks in the first ten minutes and forty asides over the
    hour after is a real shape, and the rule cannot tell it from a decoder that
    stamped everything at once. It refuses, and the count it prints is what
    lets a reader see which of the two they have.
    """
    segments = [{"start": i * 1.0, "end": i * 1.0 + 1.0,
                 "text": f"a dense opening remark number {i}"} for i in range(500)]
    segments += [{"start": 700.0 + i * 60.0, "end": 705.0 + i * 60.0,
                  "text": f"a later aside number {i}"} for i in range(40)]

    code, defects = run_windows(tmp_path, segments)

    overfull = named(defects, "E-WIN-OVERFULL")
    assert code == 1
    assert len(overfull) == 1, defects
    assert "one window holds 500 of 540 segments" in overfull[0]
    assert "the numbers say it did not" in overfull[0]


# ==========================================================================
# note_windows.main -- E-WIN-TIMELESS
# ==========================================================================

def test_three_segments_at_one_second_are_judged_on_the_floor(tmp_path):
    """Under two hundred segments the ratio is meaningless, so a floor of two runs.

    One distinct start across three segments is a broken timeline whatever a
    hundredth of three works out to.
    """
    segments = [{"start": 5.0, "end": 5.0, "text": f"all at one second {i}"}
                for i in range(3)]

    code, defects = run_windows(tmp_path, segments)

    timeless = named(defects, "E-WIN-TIMELESS")
    assert code == 1
    assert len(timeless) == 1, defects
    assert "1 distinct start time(s) across 3 segments" in timeless[0]


def test_distinct_starts_and_identical_ends_still_have_a_timeline(tmp_path):
    """The rule counts STARTS, and starts are what a window plan is cut on.

    A decoder that gets every end wrong and every start right still leaves a
    usable timeline, so this must not be refused.
    """
    segments = [{"start": i * 10.0, "end": 600.0,
                 "text": f"segment number {i} said a few words"} for i in range(60)]

    code, defects = run_windows(tmp_path, segments)

    assert len({s["start"] for s in segments}) == 60
    assert named(defects, "E-WIN-TIMELESS") == []
    assert (code, defects) == (0, [])


# ==========================================================================
# transcript_align.check_rendering -- E-TS-EMPTY
# ==========================================================================

def test_a_rendering_of_only_empty_texts_reads_back_as_no_segments(tmp_path):
    """The reader drops textless segments, so a file of them arrives here empty.

    Which is the point: a decode that emitted timings and no words is not a
    thin rendering, it is no rendering.
    """
    path = tmp_path / "r.json"
    path.write_text(json.dumps([{"start": 0.0, "end": 2.0, "text": "   "},
                                {"start": 2.0, "end": 4.0, "text": ""}]),
                    encoding="utf-8")

    segments = ta.load_segments(path)
    found = align_defects(segments, name="r.json")

    assert segments == []
    assert len(found) == 1, found
    assert "E-TS-EMPTY" in found[0]
    assert found[0].startswith("r.json[00:00] ")


def test_a_tsv_with_no_usable_rows_is_refused_as_unreadable_input(tmp_path):
    """One layer earlier, and with a different exit code.

    Exit 2 is unreadable input and exit 1 is a defect found, and a caller that
    reads the code has to be able to tell "this file is not a transcript" from
    "this transcript collapsed".
    """
    import contextlib
    import io
    path = tmp_path / "r.tsv"
    path.write_text("# a header and nothing else\n\n", encoding="utf-8")

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = ta.main([str(path)])

    assert code == 2
    assert "no start/end/text rows in this .tsv" in err.getvalue()
    assert "E-TS-EMPTY" not in out.getvalue()


def test_a_denser_middle_stretch_is_not_an_unsplit_video(tmp_path):
    """V2 finding V2-3a — a flat half, over shares that do not sum to one.

    `OVERFULL_SHARE` was compared against each window's segment count including
    the 90-second overlap, so the shares of a three-window plan sum to about
    115% rather than 100%. With three windows the arithmetic floor for the
    largest share is 33%, and a recording whose middle simply talks faster
    clears 50% without anything having gone wrong: a 22-minute talk split into
    three windows, none empty, nothing orphaned, shares 32/52/31, reported as a
    plan that did not split.

    The threshold's own comment describes the shape it was written for -- ONE
    window holding every segment -- and that is a question about what a window
    uniquely owns, which is immune both to the overlap and to the window count.

    Would fail if: the check goes back to counting a window's members.
    """
    # A middle stretch with three times the segment density of its neighbours,
    # which is a talk that got specific, not a plan that failed.
    segments = []
    for i in range(120):
        at = i * 11.0
        if 440.0 <= at <= 880.0:
            at = 440.0 + (i - 40) * 5.5
        segments.append({"start": at, "end": at + 5.0,
                         "text": f"segment {i} says a thing about widgets"})
    segments.sort(key=lambda s: s["start"])

    code, found = run_windows(tmp_path, segments)

    assert not [f for f in found if "E-WIN-OVERFULL" in f], found


def test_a_quiet_middle_is_not_an_unsplit_video(tmp_path):
    """round-14 F6 — the replacement threshold moved the false positive.

    "Some window uniquely owns nothing" fires whenever a window's exclusive
    stretch happens to hold no segments, and a silence is an ordinary property
    of a real recording. A 25-minute talk with a seven-minute gap in the middle
    -- three windows, none empty, nothing orphaned, a real split -- was
    reported as a plan that did not split, which is the same class of error as
    the flat share it replaced, on the adjacent input.
    """
    segments = [{"start": float(t), "end": float(t) + 4.0,
                 "text": f"a remark at {t} about widgets and gears"}
                for t in range(0, 590, 5)]
    segments += [{"start": float(t), "end": float(t) + 4.0,
                  "text": f"a later remark at {t} about levers"}
                 for t in range(1030, 1530, 5)]

    code, found = run_windows(tmp_path, segments)

    assert not [f for f in found if "E-WIN-OVERFULL" in f], found


def test_a_window_holding_almost_the_whole_recording_is_refused(tmp_path):
    """round-14 F3 — what costs an agent its context is what a window HOLDS.

    Both conditions measured uniquely-owned segments, so a plan where every
    window owns a small equal slice satisfied neither however much each window
    actually held: 300 segments stamped `0 -> 3600` plus two ordinary markers
    per window gave seven windows each holding 96% of a one-hour recording,
    14,770 words including overlap, reported as a clean plan of seven windows.
    That is the single overloaded context the module exists to prevent.

    Would fail if: the check goes back to counting what a window owns alone.
    """
    segments = [{"start": 0.0, "end": 3600.0,
                 "text": f"a segment {i} stamped across the whole hour"}
                for i in range(300)]
    for i in range(7):
        at = 60.0 + i * 500.0
        segments += [{"start": at, "end": at + 5.0,
                      "text": f"an ordinary marker {i} inside one window"},
                     {"start": at + 6.0, "end": at + 11.0,
                      "text": f"a second marker {i} inside the same window"}]
    segments.sort(key=lambda s: s["start"])

    code, found = run_windows(tmp_path, segments)

    assert [f for f in found if "E-WIN-OVERFULL" in f], found


def test_one_window_holding_the_whole_recording_is_still_refused(tmp_path):
    """The shape the threshold was written for, kept.

    An adversarial lane produced it by setting every segment's start to zero:
    window 1 took all 360 segments over a full hour -- the single overloaded
    context the windows exist to prevent -- reported as a clean plan of seven
    windows.
    """
    segments = [{"start": 0.0, "end": 3600.0,
                 "text": f"segment {i} says a thing about widgets"}
                for i in range(360)]

    code, found = run_windows(tmp_path, segments)

    assert [f for f in found if "E-WIN-OVERFULL" in f], found


def gapless(start: float, end: float, count: int, label: str) -> list[dict]:
    """`count` back-to-back segments tiling [start, end], the shape a rebuilt decode has."""
    step = (end - start) / count
    return [{"start": start + i * step, "end": start + (i + 1) * step,
             "text": f"{label} remark number {i}"} for i in range(count)]


def test_a_long_recording_with_one_denser_stretch_is_not_an_unsplit_video(tmp_path):
    """A course from the rung-4 corpus -- twice an even split is still density.

    A 6.6-hour recording, 4,399 segments, 47 windows, none empty, nothing
    orphaned, every segment under 31 seconds. For about 27 minutes the speaker
    talks in shorter segments, so three windows hold 228 to 236 against a
    median of 88 -- 2.1 times the whole recording's average density, which is
    2.2 times the density of the rest of it. The removed even-split share rule
    put the ceiling for a 47-window plan at 220 segments and refused the note:
    "one window holds 236 of 4399 segments". The plan split the video in time;
    one stretch simply carries more segments per second.

    Same segment count, duration, window count and biggest window as that
    rendering, built here rather than read; the rendering has a second dense
    stretch, which this fixture leaves out because one is enough to refuse.

    Would fail if: the check goes back to comparing a window's share of the
    segments against what an even split of the plan would give.
    """
    segments = (gapless(0.0, 17340.0, 2955, "a steady")
                + gapless(17340.0, 18960.0, 632, "a denser")
                + gapless(18960.0, 23727.0, 812, "a later steady"))

    plan = nw.plan(segments, 600.0, 90.0)
    code, defects = run_windows(tmp_path, segments)

    assert (len(segments), len(plan)) == (4399, 47)
    assert max(w["segments"] for w in plan) == 236
    assert max(s["end"] - s["start"] for s in segments) < 600.0
    assert named(defects, "E-WIN-OVERFULL") == []
    assert (code, defects) == (0, [])


def test_a_short_recording_with_a_dense_cold_open_is_not_an_unsplit_video(tmp_path):
    """A talk from the rung-4 corpus -- a cold open that talks fast.

    A 38-minute recording, 345 segments, five windows, none empty, nothing
    orphaned. The first eight and a half minutes carry 153 segments, so window
    1 holds 175 and the other four hold 69, 62, 57 and 30. The removed
    even-split share rule put the ceiling for five windows at 162 and refused
    the note: "one window
    holds 175 of 345 segments". The note itself says the cold open is dense;
    the plan split the video in time.

    Same segment count, duration, window count and first window as that
    rendering, built here rather than read.

    Would fail if: the check goes back to comparing a window's share of the
    segments against what an even split of the plan would give.
    """
    segments = (gapless(0.0, 510.0, 165, "a cold open")
                + gapless(510.0, 2290.0, 180, "a steady"))

    plan = nw.plan(segments, 600.0, 90.0)
    code, defects = run_windows(tmp_path, segments)

    assert len(segments) == 345
    assert [w["segments"] for w in plan] == [175, 62, 62, 61, 26]
    assert max(s["end"] - s["start"] for s in segments) < 600.0
    assert named(defects, "E-WIN-OVERFULL") == []
    assert (code, defects) == (0, [])


def spread(segments: list[dict], window: dict, seconds: float = 600.0) -> int:
    """How many of a window's members are stamped longer than a window."""
    return sum(1 for i in window["members"]
               if segments[i]["end"] - segments[i]["start"] > seconds)


def test_segments_stamped_to_the_last_second_are_an_unsplit_video(tmp_path):
    """The loosening the span rule shipped with, kept refused.

    Seventy remarks ten seconds apart, every one stamped to end on the
    recording's last second. Only the first ten are longer than a window, so
    counting segments longer than a window finds ten of the seventy the second
    window holds, and passed the plan. The density rule refused it. Window 2
    holds all seventy, and their stamps claim 24,850 seconds inside the 700
    seconds they reach: they are stacked on top of each other.

    Would fail if: the check reads only how many members are longer than a
    window.
    """
    segments = [{"start": 10.0 * i, "end": 700.0,
                 "text": f"a remark number {i} stamped to the end"} for i in range(70)]

    plan = nw.plan(segments, 600.0, 90.0)
    code, defects = run_windows(tmp_path, segments)

    assert [w["segments"] for w in plan] == [61, 70]
    assert spread(segments, plan[1]) == 10
    overfull = named(defects, "E-WIN-OVERFULL")
    assert code == 1
    assert len(overfull) == 1, defects
    assert "one window holds 70 of 70 segments" in overfull[0]
    # The diagnosis names stacked stamps, not a split that never happened.
    assert "claim 24850 seconds inside the 700 they reach" in overfull[0]
    assert "numbers say it did not" not in overfull[0]


def test_ten_remarks_ending_on_the_last_second_are_an_unsplit_video(tmp_path):
    """What wrong ends mean when no segment is longer than a window.

    Ten remarks a minute apart from 100 seconds on, every one stamped to end
    at 690. The longest is 590 seconds, so none is longer than a window, and
    both windows hold every remark. Their stamps claim 3,200 seconds inside
    the 590 they reach.
    """
    segments = [{"start": 100.0 + 60.0 * j, "end": 690.0,
                 "text": f"a remark number {j} stamped to the end"} for j in range(10)]

    plan = nw.plan(segments, 600.0, 90.0)
    code, defects = run_windows(tmp_path, segments)

    assert [w["segments"] for w in plan] == [9, 10]
    assert max(s["end"] - s["start"] for s in segments) < 600.0
    overfull = named(defects, "E-WIN-OVERFULL")
    assert code == 1
    assert len(overfull) == 1, defects
    assert "one window holds 10 of 10 segments" in overfull[0]
    assert "stamps are stacked" in overfull[0]


def ends_pushed_to_the_last_second() -> list[dict]:
    """Every other end moved to the recording's last second, the rest 400 seconds late."""
    return [{"start": i * 5.4,
             "end": 1241.0 if i % 2 == 0 else i * 5.4 + 400.0,
             "text": f"a remark number {i}"} for i in range(230)]


def four_in_nine_later_starts_zeroed() -> list[dict]:
    """Sixty opening remarks, then 130 later ones of which four in nine start at zero."""
    segments = [{"start": i * 10.0, "end": i * 10.0 + 9.0,
                 "text": f"an opening remark number {i}"} for i in range(60)]
    for j in range(130):
        at = 600.0 + j * 11.2
        segments.append({"start": 0.0 if j % 9 in (0, 2, 4, 6) else at,
                         "end": at + 9.0, "text": f"a later remark number {j}"})
    return segments


def ends_pushed_up_to_twenty_minutes_late() -> list[dict]:
    """One end in four pushed 1,200 seconds late, the rest 599, none past the recording."""
    return [{"start": i * 11.15,
             "end": min(i * 11.15 + (1200.0 if i % 4 == 0 else 599.0), 3925.0),
             "text": f"a remark number {i}"} for i in range(352)]


@pytest.mark.parametrize("build, windows, held, spread_members", [
    (ends_pushed_to_the_last_second, 3, 196, 60),
    (four_in_nine_later_starts_zeroed, 4, 118, 58),
    (ends_pushed_up_to_twenty_minutes_late, 8, 122, 33),
])
def test_a_plan_whose_stamps_stack_up_is_refused_when_few_are_longer_than_a_window(
        tmp_path, build, windows, held, spread_members):
    """Code review, 2026-09-16: three corruptions the span rule passed.

    A replay of corrupted transcripts found plans the density rule refused and
    the span rule passed, at three, four and eight windows. In each, at most
    half of the biggest window's members are longer than a window, so that
    count never fires. These fixtures have the same shape and window count,
    built here rather than read.

    Would fail if: the check reads only how many members are longer than a
    window.
    """
    segments = build()

    plan = nw.plan(segments, 600.0, 90.0)
    biggest = max(plan, key=lambda w: w["segments"])
    code, defects = run_windows(tmp_path, segments)

    assert (len(plan), biggest["segments"]) == (windows, held)
    assert spread(segments, biggest) == spread_members
    assert spread_members * 2 <= held
    overfull = named(defects, "E-WIN-OVERFULL")
    assert code == 1
    assert len(overfull) == 1, defects
    assert f"one window holds {held} of {len(segments)} segments" in overfull[0]
    assert "stamps are stacked" in overfull[0]
    assert "numbers say it did not" not in overfull[0]


def test_one_segment_stamped_across_the_whole_recording_is_not_an_unsplit_video(tmp_path):
    """One bad stamp is not a plan that failed to split.

    Seventy back-to-back remarks over 700 seconds, plus one segment stamped
    across all of it. Window 1 holds 62 segments whose stamps claim 1,310
    seconds inside the 700 they reach: under twice, where the check fires only
    above three times.
    """
    segments = gapless(0.0, 700.0, 70, "a steady") + [
        {"start": 0.0, "end": 700.0, "text": "one segment stamped across everything"}]

    plan = nw.plan(segments, 600.0, 90.0)
    code, defects = run_windows(tmp_path, segments)

    assert [w["segments"] for w in plan] == [62, 21]
    assert named(defects, "E-WIN-OVERFULL") == []
    assert (code, defects) == (0, [])


@pytest.mark.parametrize("bad", ["Infinity", "-Infinity", "NaN"])
@pytest.mark.parametrize("field", ["start", "end"])
def test_a_stamp_that_is_not_a_number_of_seconds_is_refused_at_the_reader(
        tmp_path, field, bad):
    """V1 section D — `json.loads` accepts these and every reader inherits them.

    `Infinity` is legal JSON to Python and nothing downstream expects it. The
    window planner walks `at += stride` while `at < total`, so with an infinite
    total it appends to a list for ever: killed under `timeout 20`, rc 124,
    reproduced twice. Nothing rescues it -- `_run` catches a check that fails
    and one that raises, and there is no catch for one that never returns, so
    the whole audit stalls with no exit code at all.

    The refusal belongs HERE rather than at the planner. The planner is one
    consumer of these numbers; the recall counter, the aligner, the coverage
    walk and the caption index are others, and each would need its own guard.
    A start or an end is a number of seconds into a recording, and none of
    these three is one.

    Would fail if: `load_segments` stops testing what it parsed.
    """
    other = "end" if field == "start" else "start"
    path = tmp_path / "r.json"
    path.write_text(
        '{"segments": [{"%s": %s, "%s": 1.0, "text": "a thing"}]}'
        % (field, bad, other), encoding="utf-8")

    with pytest.raises(ValueError) as caught:
        ta.load_segments(path)
    assert "seconds" in str(caught.value), caught.value


def test_every_reader_branch_refuses_a_stamp_that_is_not_seconds(tmp_path):
    """round-14 F4 — the guard sat past two of the four return paths.

    `load_segments` has four branches and the `.vtt` and `.tsv` ones return
    before the check. The comment justifying the guard named "the caption
    index" among the consumers it protects, and the caption index IS the `.tsv`
    branch: the one reader named in the argument was one of the two the guard
    could not see. A `.tsv` whose first stamp is the literal `inf` was accepted
    and then killed the aligner in `hms`, on `int(seconds)`.

    Would fail if: the guard stops being applied to every branch.
    """
    tsv = tmp_path / "idx.tsv"
    tsv.write_text("# start\tend\ttext\ninf\t10\thello world one\n"
                   "20\t30\tsecond row here\n", encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        ta.load_segments(tsv)
    assert "seconds" in str(caught.value), caught.value

    vtt = tmp_path / "r.vtt"
    vtt.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nhello there\n",
                   encoding="utf-8")
    assert ta.load_segments(vtt), "an ordinary caption file still reads"


def test_the_window_planner_terminates_on_every_rendering_it_is_handed():
    """The other half, stated as the property rather than as one input.

    `load_segments` is the door and it is shut. This says what the planner
    itself promises: handed segments, it returns. It is asserted directly
    because `plan` is a public function and a caller can build a segment list
    without going through the reader -- which is how the corpus reaches it in
    one place already.
    """
    for end in (float("inf"), float("nan")):
        with pytest.raises(ValueError) as caught:
            nw.plan([{"start": 0.0, "end": end, "text": "one"}], 600.0, 90.0)
        assert "seconds" in str(caught.value), (end, caught.value)

    # A finite length can still be absurd, and `at += stride` over 1e18 seconds
    # is a list nothing can hold. The refusal names the ceiling rather than
    # running until the machine says no.
    with pytest.raises(ValueError) as caught:
        nw.plan([{"start": 0.0, "end": 1e18, "text": "one"}], 600.0, 90.0)
    assert "window" in str(caught.value).lower(), caught.value

    # ...and an ordinary rendering still plans, so the guard is not a refusal
    # of everything.
    got = nw.plan([{"start": float(i) * 30, "end": float(i) * 30 + 30,
                    "text": f"segment {i}"} for i in range(60)], 600.0, 90.0)
    assert len(got) >= 2, got


def test_a_rendering_of_one_segment_is_thin_and_not_empty():
    """One segment is a rendering. Thinness is somebody else's rule."""
    found = align_defects([{"start": 0.0, "end": 2.0, "text": "one thing happened"}])

    assert found == []


# ==========================================================================
# transcript_align.check_rendering -- E-TS-SEGMENT
# ==========================================================================

def test_only_the_first_absurd_segment_in_a_rendering_is_named():
    """The scan breaks at the first, so a second one is never printed.

    Pinned because the count reads as "one malformed segment" when it is the
    first of however many, and a repair that raised the cap would look like it
    had cleared the file.
    """
    segments = [{"start": 10.0, "end": 400.0, "text": "first absurd"},
                {"start": 500.0, "end": 900.0, "text": "second absurd"}]

    found = named(align_defects(segments), "E-TS-SEGMENT")

    assert len(found) == 1
    assert found[0].startswith("v[00:10] "), "the first one, by its own second"
    assert "one segment claims 390s" in found[0]


def test_a_segment_claiming_exactly_two_minutes_sits_on_the_cap():
    """The cap is a cap: 120 seconds is allowed and 120.5 is not."""
    on_it = [{"start": 0.0, "end": ta.MAX_SEGMENT_SECONDS, "text": "exactly the cap"}]
    over_it = [{"start": 0.0, "end": ta.MAX_SEGMENT_SECONDS + 0.5, "text": "over it"}]

    assert named(align_defects(on_it), "E-TS-SEGMENT") == []
    assert len(named(align_defects(over_it), "E-TS-SEGMENT")) == 1


def test_a_segment_whose_end_precedes_its_start_is_not_refused_as_long():
    """A negative length is malformed and is not this rule's malformed.

    Nothing here refuses it, and the gap walk downstream is what notices the
    hole it leaves -- so the case pins both halves rather than only the silence.
    """
    segments = [{"start": 300.0, "end": 100.0, "text": "ends before it starts"}]

    found = align_defects(segments)

    assert named(found, "E-TS-SEGMENT") == []
    assert len(named(found, "E-TS-GAP")) == 1


# ==========================================================================
# transcript_align.check_rendering -- E-TS-GAP
# ==========================================================================

def test_a_hole_of_exactly_the_gap_limit_is_not_refused():
    """Twenty seconds is silence; twenty and a half is lost audio."""
    on_it = [{"start": 0.0, "end": 10.0, "text": "before"},
             {"start": 30.0, "end": 40.0, "text": "after"}]
    over_it = [{"start": 0.0, "end": 10.0, "text": "before"},
               {"start": 30.5, "end": 40.0, "text": "after"}]

    assert ta.gaps(on_it, ta.MAX_GAP) == []
    assert named(align_defects(on_it), "E-TS-GAP") == []
    assert ta.gaps(over_it, ta.MAX_GAP) == [(10.0, 30.5)]
    assert len(named(align_defects(over_it), "E-TS-GAP")) == 1


# ==========================================================================
# transcript_align.check_rendering -- E-TS-SHORT
# ==========================================================================

def test_a_rendering_ending_exactly_the_gap_limit_early_is_not_refused():
    """The tail check uses the same limit as the holes, and the same strictness."""
    segments = [{"start": 0.0, "end": 5.0, "text": "one thing"},
                {"start": 3.0, "end": 5.0, "text": "another thing"}]

    assert named(align_defects(segments, duration=25.0), "E-TS-SHORT") == []
    assert len(named(align_defects(segments, duration=25.5), "E-TS-SHORT")) == 1


def test_a_rendering_running_past_the_declared_length_is_not_refused_here():
    """A rendering longer than the audio is a disagreement about the audio.

    It is not truncation, which is the only thing this rule can see, and
    refusing it here would put the wrong name on the problem. Ten minutes of
    rendering against a declared one hundred seconds is as wrong as a decode
    gets, and it is still not a tail that was never decoded.
    """
    segments = rendering(200, step=3.0)

    found = align_defects(segments, duration=100.0)

    assert max(ta.segment_end(s) for s in segments) == 600.0
    assert named(found, "E-TS-SHORT") == []


# ==========================================================================
# transcript_align.check_rendering -- E-TS-SPARSE
# ==========================================================================

def test_a_sawtooth_transcript_covers_too_little_of_its_own_running_time():
    """One second of speech every twenty passes every per-hole gap check.

    No single hole reaches the gap limit, every text is distinct, and the file
    is nineteen unwitnessed minutes. This is the rule's own case, written here
    rather than cited into the module's selftest, where three different checks
    share the label `...as a defect` and a citation resolves to one of three.
    """
    saw = [{"start": i * 20.0, "end": i * 20.0 + 1.0, "text": wide_text(i)}
           for i in range(60)]

    found = align_defects(saw)

    assert ta.gaps(saw, ta.MAX_GAP) == [], "no hole reaches twenty seconds"
    assert round(ta.coverage(saw, 1181.0), 2) == 0.05
    assert codes_in(found) == {"E-TS-SPARSE"}, found
    assert "segments cover 5% of 19:41 (floor 50%)" in found[0]


def test_a_sawtooth_under_the_statistics_floor_is_not_refused_as_sparse():
    """Below five minutes a share is noise, and a check that fires on noise
    is a check that gets ignored.

    The same shape over ten minutes is refused, which is what shows the floor
    is doing the work rather than the fixture.
    """
    short = [{"start": i * 12.0, "end": i * 12.0 + 1.0, "text": wide_text(i)}
             for i in range(20)]
    long = [{"start": i * 20.0, "end": i * 20.0 + 1.0, "text": wide_text(i)}
            for i in range(30)]

    assert round(ta.coverage(short, 240.0), 2) == 0.08
    assert named(align_defects(short, duration=240.0), "E-TS-SPARSE") == []
    assert len(named(align_defects(long, duration=600.0), "E-TS-SPARSE")) == 1


def test_overlapping_segments_are_counted_once_when_the_share_is_taken():
    """Twenty-five segments claiming 2,500 seconds of a 600-second file.

    Counted naively the coverage is over four hundred percent and nothing is
    ever sparse again; counted as a union it is the 24% it really is.
    """
    segments = [{"start": i * 1.0, "end": i * 1.0 + 100.0, "text": wide_text(i)}
                for i in range(25)]
    segments += [{"start": 580.0 + i, "end": 581.0 + i, "text": wide_text(50 + i)}
                 for i in range(20)]

    assert sum(s["end"] - s["start"] for s in segments) == 2520.0
    assert ta.coverage(segments, 600.0) == 0.24

    sparse = named(align_defects(segments, duration=600.0), "E-TS-SPARSE")
    assert len(sparse) == 1
    assert "segments cover 24% of 10:00" in sparse[0]


# ==========================================================================
# transcript_align.check_rendering -- E-TS-REPEAT
# ==========================================================================

def test_two_segments_of_which_one_repeats_sit_on_the_distinct_floor():
    """Half distinct is the floor, in a file far too short to mean it.

    One repeat out of two is the most repetitive a two-segment rendering can
    be, and it scores exactly the number a collapsed two-hour decode has to get
    under. The floor is a floor, so it stands.
    """
    segments = [{"start": 0.0, "end": 2.0, "text": "the same line"},
                {"start": 3.0, "end": 5.0, "text": "the same line"}]

    found = align_defects(segments)

    assert len({ta.normalise(s["text"]) for s in segments}) / len(segments) == ta.MIN_DISTINCT
    assert named(found, "E-TS-REPEAT") == []


# ==========================================================================
# transcript_align.check_rendering -- E-TS-LOOP
# ==========================================================================

def test_identical_texts_differing_only_in_punctuation_are_one_text():
    """A decoder that varies its full stops has not stopped repeating itself.

    Normalising before the comparison is what makes the run visible, and the
    reported second must still be where the run began.
    """
    segments = [{"start": 0.0, "end": 2.0, "text": "The same line."},
                {"start": 3.0, "end": 5.0, "text": "the same line"},
                {"start": 6.0, "end": 8.0, "text": "THE SAME LINE!"},
                {"start": 9.0, "end": 11.0, "text": "the same line,"}]

    loop = named(align_defects(segments), "E-TS-LOOP")

    assert ta.longest_run(segments)[0] == 4
    assert len(loop) == 1
    assert loop[0].startswith("v[00:00] ")
    assert "4 identical segments in a row" in loop[0]


def test_three_identical_segments_in_a_row_sit_under_the_loop_limit():
    """Where the limit SITS, which is the whole content of the neighbour.

    The healthy decode of the two-hour recording this package was built on holds
    a longest run of three. Tightening the limit to three would report that
    decode as a collapse, so the case is written from below: three in a row, in
    a long rendering that trips nothing else, and no defect at all. The run of
    four beside it is what shows the check is present rather than absent.
    """
    healthy = rendering(200)
    for i in (50, 51, 52):
        healthy[i]["text"] = wide_text(50)
    collapsed = rendering(200)
    for i in (50, 51, 52, 53):
        collapsed[i]["text"] = wide_text(50)

    assert ta.longest_run(healthy)[0] == 3
    assert codes_in(align_defects(healthy)) == set(), align_defects(healthy)

    loop = named(align_defects(collapsed), "E-TS-LOOP")
    assert ta.longest_run(collapsed)[0] == ta.MAX_RUN
    assert len(loop) == 1
    assert "4 identical segments in a row (limit 4)" in loop[0]


# ==========================================================================
# transcript_align.check_rendering -- E-TS-NEARLOOP
# ==========================================================================

def test_six_hundred_numbered_copies_of_one_sentence_are_a_near_loop():
    """Identical-text detection is defeated by a counter; word overlap is not.

    Six hundred copies of one sentence, each numbered, arrive as six hundred
    distinct texts: the distinct-share floor sees nothing, the dominant-text
    share sees nothing, and the run of identical texts is one. The whole
    collapse is packed into two minutes, which is under the statistics floor, so
    the per-file shares are switched off and near-identity is the only rule left
    that can colour this fixture.
    """
    counted = [{"start": i * 0.2, "end": i * 0.2 + 0.2,
                "text": f"the speaker repeats this one sentence again number {i}"}
               for i in range(600)]

    found = align_defects(counted)

    assert len({ta.normalise(s["text"]) for s in counted}) == 600, "all distinct"
    assert ta.longest_run(counted)[0] == 1, "and not one of them is a repeat"
    assert ta.near_run(counted) == 600
    assert codes_in(found) == {"E-TS-NEARLOOP"}, found
    assert "600 consecutive segments are near-identical (limit 8)" in found[0]


def test_an_identical_run_is_refused_once_and_not_again_as_near_identity():
    """Identical is a special case of near-identical, and is named once.

    Ten copies of one line are a near-run of ten as well as a run of ten, and
    without the guard the same collapse would print two defects with two names
    and two repairs. The guard is `run_len < max_run`, so what pins it is a
    fixture that clears the identical-run limit and reports only that.
    """
    segments = rendering(200)
    for i in range(50, 60):
        segments[i]["text"] = wide_text(50)

    found = align_defects(segments)

    assert (ta.longest_run(segments)[0], ta.near_run(segments)) == (10, 10)
    assert codes_in(found) == {"E-TS-LOOP"}, found
    assert found[0].startswith("v[02:30] "), "the second the run began"
    assert "10 identical segments in a row" in found[0]

def test_two_ordinary_words_changed_each_time_escapes_near_identity():
    """The escape: word-set overlap at four fifths, and two in ten breaks it.

    A reader sees one sentence twelve times over. The counter version beside it
    is the shape near-identity was built for, and it is still caught -- so what
    this pins is the width of the hole, not the absence of the check.
    """
    words = ("market price value margin revenue profit demand supply budget "
             "ledger balance forecast quarter target segment channel partner "
             "vendor contract renewal rebate discount premium surplus").split()
    escaping = [{"start": i * 3.0, "end": i * 3.0 + 3.0,
                 "text": f"and we can get another two percent of gdp "
                         f"{words[2 * i]} {words[2 * i + 1]}"}
                for i in range(12)]
    counted = [{"start": i * 3.0, "end": i * 3.0 + 3.0,
                "text": f"and we can get another two percent of gdp {i}"}
               for i in range(12)]

    assert ta.near_run(escaping) == 1
    assert named(align_defects(escaping), "E-TS-NEARLOOP") == []
    assert ta.near_run(counted) == 12
    assert len(named(align_defects(counted), "E-TS-NEARLOOP")) == 1


def test_eight_honest_sentences_on_one_subject_are_refused_as_a_near_loop():
    """A known false positive: a speaker enumerating is not a decoder looping.

    Eight sentences that differ by one word each -- a quarter at a time through
    a year -- overlap by more than four fifths, and this rule cannot tell that
    from a repeat with a counter on it.
    """
    quarters = "first second third fourth fifth sixth seventh eighth".split()
    segments = [{"start": i * 4.0, "end": i * 4.0 + 4.0,
                 "text": f"in the {q} quarter revenue grew by roughly the same "
                         f"modest amount as before"}
                for i, q in enumerate(quarters)]

    found = named(align_defects(segments), "E-TS-NEARLOOP")

    assert ta.longest_run(segments)[0] == 1, "not one of them is a repeat"
    assert ta.near_run(segments) == ta.NEAR_RUN
    assert len(found) == 1
    assert "8 consecutive segments are near-identical" in found[0]


# ==========================================================================
# transcript_align.check_rendering -- E-TS-DOMINANT
# ==========================================================================

def test_an_interleaved_loop_under_the_statistics_floor_is_not_refused():
    """Half of every segment is one sentence, in a file too short to say so.

    The same interleaving over a longer file is refused, which is what shows
    the floor rather than the fixture is what kept this one quiet.
    """
    def interleave(pairs: int, step: float) -> list[dict]:
        out = []
        for i in range(pairs):
            out.append({"start": i * step, "end": i * step + step / 2,
                        "text": "thanks for watching and please subscribe"})
            out.append({"start": i * step + step / 2, "end": i * step + step,
                        "text": wide_text(i)})
        return out

    short = interleave(30, 8.0)
    long = interleave(400, 4.0)

    assert round(ta.top_share(short)[0], 2) == 0.5
    assert named(align_defects(short, duration=240.0), "E-TS-DOMINANT") == []
    assert len(named(align_defects(long, duration=1600.0), "E-TS-DOMINANT")) == 1


def test_a_stuck_line_numbered_per_copy_escapes_the_dominant_share():
    """The escape: the share counts EXACT texts, and a counter splits them.

    Two hundred copies of one sentence, one text to a reader and two hundred to
    this count, so the share never rises off the floor.
    """
    segments = []
    for i in range(200):
        segments.append({"start": i * 6.0, "end": i * 6.0 + 3.0,
                         "text": f"thanks for watching and please subscribe number {i}"})
        segments.append({"start": i * 6.0 + 3.0, "end": i * 6.0 + 6.0,
                         "text": wide_text(i)})

    found = align_defects(segments, duration=1200.0)

    assert ta.top_share(segments)[0] < ta.MAX_TOP_SHARE
    assert named(found, "E-TS-DOMINANT") == []
    assert found == [], "and nothing else catches it either"


def test_a_refrain_repeated_a_dozen_times_in_an_hour_is_under_the_ceiling():
    """Speakers have refrains, and a tenth of an hour's segments is not one."""
    segments = [{"start": i * 9.0, "end": i * 9.0 + 9.0,
                 "text": "and that is the whole point of it" if i % 33 == 0
                         else wide_text(i)}
                for i in range(400)]

    found = align_defects(segments, duration=3600.0)

    assert sum(1 for s in segments if "whole point" in s["text"]) == 13
    assert ta.top_share(segments)[0] < ta.MAX_TOP_SHARE
    assert named(found, "E-TS-DOMINANT") == []


# ==========================================================================
# transcript_align.check_rendering -- E-TS-VOCABULARY
# ==========================================================================

def test_a_quiet_stretch_stays_far_above_the_vocabulary_floor():
    """The floor is set below any real speech, so it fires only on catastrophe.

    Forty slow segments over twenty minutes -- a quiet talk, not a fluent
    nothing -- measures well over the five distinct words a minute the rule
    asks for.
    """
    segments = [{"start": i * 30.0, "end": i * 30.0 + 28.0, "text": wide_text(i)}
                for i in range(40)]

    _found, census = ta.check_rendering("v", segments, 1200.0, ta.MAX_RUN,
                                        ta.MAX_GAP, ta.MIN_DISTINCT)

    assert census["enough"] is True, "the statistics floor is cleared"
    assert census["per_minute"] > ta.MIN_WORDS_PER_MINUTE
    assert named(align_defects(segments, duration=1200.0), "E-TS-VOCABULARY") == []


# ==========================================================================
# transcript_align.main -- E-TS-DIVERGENT
# ==========================================================================

def test_a_third_rendering_is_never_compared_with_the_second(tmp_path):
    """Only the first is a baseline, so later pairs are never measured.

    Two decodes that agree with the first and disagree wildly with each other
    is exactly the shape a third opinion is fetched to settle, and nothing here
    says a word about it.
    """
    base = rendering(40)
    second = [dict(s, text=wide_text(500 + i)) if i >= 20 else dict(s)
              for i, s in enumerate(base)]
    third = [dict(s, text=wide_text(900 + i)) if i < 20 else dict(s)
             for i, s in enumerate(base)]
    paths = []
    for name, rows in (("a.json", base), ("b.json", second), ("c.json", third)):
        path = tmp_path / name
        path.write_text(json.dumps(rows), encoding="utf-8")
        paths.append(path)

    code, defects = run_align(paths)

    assert ta.align(second, third, ta.MIN_REGION)[0] < ta.MIN_RATIO
    divergent = named(defects, "E-TS-DIVERGENT")
    assert code == 1
    assert len(divergent) == 2, defects
    assert all("agreement with a.json" in line for line in divergent)
    assert not any("with b.json" in line for line in divergent)


# ==========================================================================
# transcript_align -- E-TS-SINGLE-WITNESS
# ==========================================================================

def test_no_renderings_at_all_is_a_usage_error_before_the_witness_rule():
    """Exit 2, and this rule is never reached.

    Zero decodes is not a lone decode: nothing was checked, and reporting the
    single-witness defect would say something was.
    """
    import contextlib
    import io

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = ta.main([])

    assert code == 2
    assert "E-TS-SINGLE-WITNESS" not in out.getvalue()
    assert ta.witness_defect(0), "the rule itself would have fired on a count of 0"


def test_a_lone_decode_that_also_collapsed_reports_both(tmp_path):
    """Neither refusal silences the other.

    A lone decode is unchecked AND this one is looping, and a run that reported
    only the first would leave the operator fetching a second opinion on a file
    that is already known to be broken.
    """
    segments = [{"start": 0.0, "end": 3.0, "text": "a real sentence"}]
    segments += [{"start": 3.0 + i * 3.0, "end": 6.0 + i * 3.0,
                  "text": "the same line"} for i in range(9)]
    path = tmp_path / "one.json"
    path.write_text(json.dumps(segments), encoding="utf-8")

    code, defects = run_align([path])

    assert code == 1
    assert codes_in(defects) == {"E-TS-SINGLE-WITNESS", "E-TS-LOOP", "E-TS-REPEAT"}
    assert "9 identical segments in a row" in named(defects, "E-TS-LOOP")[0]


# ==========================================================================
# transcript_align.main -- E-TS-SAME-WITNESS
# ==========================================================================

def test_the_same_file_named_twice_is_one_witness(tmp_path):
    """It scores perfect agreement and clears the lone-decode rule.

    Which is the whole reason the fingerprint exists: a fabricated second
    opinion is worse than an admitted single one.
    """
    path = tmp_path / "r.json"
    path.write_text(json.dumps(rendering(40)), encoding="utf-8")

    code, defects = run_align([path, path])

    same = named(defects, "E-TS-SAME-WITNESS")
    assert code == 1
    assert len(same) == 1, defects
    assert "identical to r.json" in same[0]
    assert named(defects, "E-TS-SINGLE-WITNESS") == [], "it did clear that one"


def test_one_decode_under_two_names_or_two_exports_is_one_witness(tmp_path):
    """A copy under another name, and the same decode exported another way.

    The fingerprint is taken from the starts and the normalised texts, so it
    survives a rename and it survives a change of file format -- which is what
    it has to do, because both are how one decode arrives twice.
    """
    base = rendering(40)
    (tmp_path / "run-a.json").write_text(json.dumps(base), encoding="utf-8")
    (tmp_path / "run-b.json").write_text(json.dumps(base), encoding="utf-8")
    (tmp_path / "run-c.tsv").write_text(
        "# an exported caption index\nstart\tend\ttext\n"
        + "".join(f"{s['start']:.3f}\t{s['end']:.3f}\t{s['text']}\n" for s in base),
        encoding="utf-8")

    _code, renamed = run_align([tmp_path / "run-a.json", tmp_path / "run-b.json"])
    _code, exported = run_align([tmp_path / "run-a.json", tmp_path / "run-c.tsv"])

    assert "run-b.json[00:00] E-TS-SAME-WITNESS" in named(renamed, "E-TS-SAME-WITNESS")[0]
    assert "identical to run-a.json" in named(renamed, "E-TS-SAME-WITNESS")[0]
    assert "run-c.tsv[00:00] E-TS-SAME-WITNESS" in named(exported, "E-TS-SAME-WITNESS")[0]


def test_one_decode_re_segmented_passes_as_a_second_witness(tmp_path):
    """The escape: the fingerprint reads segment boundaries as well as words.

    Re-emitting one decode with its sentences joined in pairs produces a second
    fingerprint, a perfect alignment, and a clean run -- a second witness that
    does not exist.
    """
    base = rendering(40)
    resplit = [{"start": i * 3.0, "end": i * 3.0 + 6.0,
                "text": base[i]["text"] + " " + base[i + 1]["text"]}
               for i in range(0, 40, 2)]
    (tmp_path / "one.json").write_text(json.dumps(base), encoding="utf-8")
    (tmp_path / "one-resplit.json").write_text(json.dumps(resplit), encoding="utf-8")

    code, defects = run_align([tmp_path / "one.json", tmp_path / "one-resplit.json"])

    assert ta.fingerprint(base) != ta.fingerprint(resplit)
    assert ta.align(base, resplit, ta.MIN_REGION)[0] == 1.0
    assert (code, defects) == (0, []), "one decode, and nothing says so"


def test_two_models_agreeing_on_words_and_differing_on_timing_are_two_witnesses(tmp_path):
    """The direction the fingerprint must not break: real agreement.

    Two decoders that heard the same words and cut them half a second apart are
    two witnesses, and refusing them would make perfect agreement between
    genuine decodes impossible to report.
    """
    base = rendering(40)
    other = [dict(s, start=s["start"] + 0.5, end=s["end"] + 0.5) for s in base]
    (tmp_path / "model-one.json").write_text(json.dumps(base), encoding="utf-8")
    (tmp_path / "model-two.json").write_text(json.dumps(other), encoding="utf-8")

    code, defects = run_align([tmp_path / "model-one.json",
                               tmp_path / "model-two.json"])

    assert ta.fingerprint(base) != ta.fingerprint(other)
    assert ta.align(base, other, ta.MIN_REGION)[0] == 1.0
    assert (code, defects) == (0, [])


# ==========================================================================
# demote_note.review_reports -- E-LANE-MISSING, counted rather than refused
# ==========================================================================

def lane_corpus(tmp_path: Path, reports: tuple[str, ...] = ("x-review-facts.md",)
                ) -> Path:
    directory = tmp_path / dn.POLICY.reviews_dir() / "VID"
    directory.mkdir(parents=True)
    for name in reports:
        (directory / name).write_text("a report", encoding="utf-8")
    return tmp_path


CITES = "cites `notes/reviews/VID/x-review-facts.md` and a review-ghost.md\n"


def test_a_declared_lane_with_a_report_on_disk_counts_as_present(tmp_path):
    """One declared lane, one report, nothing missing."""
    root = lane_corpus(tmp_path)

    assert dn.review_reports(CITES, "video_id: VID\nreviews: [facts]\n", root) == (1, [])


def test_a_declared_lane_with_no_report_is_counted_and_rendered_not_refused(tmp_path):
    """The whole rule: a lane that died is COUNTED here, and never a defect.

    `resolve_note.check_lanes` owns the half that refuses. This module reads the
    same condition through the same two primitives and renders the number onto
    the note's own face, so the gate and the integrity line cannot disagree
    about one note.
    """
    root = lane_corpus(tmp_path)

    counted = dn.review_reports(CITES, "video_id: VID\nreviews: [facts, dead]\n", root)
    line = dn.integrity_line("# T\n\n" + CITES,
                             "video_id: VID\nreviews: [facts, dead]\n", root)

    assert counted == (2, ["dead"])
    assert "reviews 1/2 on disk" in line
    assert "E-LANE-MISSING" not in line, "this module counts, it does not refuse"


def test_a_note_written_before_the_convention_falls_back_to_what_it_cites(tmp_path):
    """No `reviews:` key at all is not a defect: most notes were never reviewed.

    The fallback counts the review reports the note cites, so an old note's
    integrity line keeps meaning what it said when it was written.
    """
    root = lane_corpus(tmp_path)

    assert dn.review_reports(CITES, "video_id: VID\n", root) == (1, [])


def test_reports_a_note_never_declared_do_not_inflate_the_roll_call(tmp_path):
    """Declared, not cited. A citation is prose; the roll-call is the key.

    Four reports named in the body against one declared lane still counts one,
    because otherwise a note could print `reviews 4/4` for lanes it never ran.
    """
    root = lane_corpus(tmp_path)
    body = ("cites `notes/reviews/VID/x-review-facts.md` `a-review-one.md` "
            "`b-review-two.md` `c-review-three.md` `d-review-four.md`\n")

    assert dn.review_reports(body, "video_id: VID\nreviews: [facts]\n", root) == (1, [])


def test_a_malformed_lane_id_falls_back_to_counting_cited_filenames(tmp_path):
    """A typo in the key silently changes what is being counted.

    `reviews: [Bad Id]` does not parse, so the declared-lane branch is skipped
    and the pre-convention fallback counts citations instead. The number that
    reaches the note's face is then about the prose, not about the roll-call,
    and nothing in this module says the switch happened.
    """
    root = lane_corpus(tmp_path)
    body = ("cites `notes/reviews/VID/x-review-facts.md` `a-review-one.md` "
            "`b-review-two.md` `c-review-three.md` `d-review-four.md`\n")

    declared, missing = dn.review_reports(body, "video_id: VID\nreviews: [Bad Id]\n",
                                          root)

    assert declared == 5, "five cited filenames, not one declared lane"
    assert missing == ["a-review-one.md", "b-review-two.md",
                       "c-review-three.md", "d-review-four.md"]


def test_declared_lanes_with_no_video_id_report_every_cited_filename_missing(tmp_path):
    """With no video id there is no directory to look in, so nothing is on disk.

    Every cited report comes back missing wholesale -- including the one that is
    sitting there -- because the path it would be at cannot be built.
    """
    root = lane_corpus(tmp_path)
    body = ("cites `notes/reviews/VID/x-review-facts.md` `a-review-one.md` "
            "`b-review-two.md` `c-review-three.md` `d-review-four.md`\n")

    declared, missing = dn.review_reports(body, "reviews: [facts, dead]\n", root)

    assert declared == 5
    assert missing == ["a-review-one.md", "b-review-two.md",
                       "c-review-three.md", "d-review-four.md",
                       "x-review-facts.md"]


def test_a_claim_that_straddles_the_window_is_counted_by_the_window(tmp_path):
    """A row anchored outside a span still carries words inside it.

    The filter asked where a row STARTED, so a claim opening at 04:50 and
    closing at 05:30, measured against 05:00-06:00, was in no row-based number
    for that window -- not `rows`, not `carried_per_row`, not `row_words` --
    while its words were inside the window's scope text. The note carried the
    claim and every number about that window said it did not.
    """
    segments = cov_spoken(60)
    note = ("- `[04:50]` `SPOKEN` to `[05:30]` `SPOKEN` — "
            + COV_SHAPES[0].format(w=f"{coined(30)}zone") + ".\n")

    inside = cov_measure(tmp_path, note, segments, (300.0, 360.0))
    before = cov_measure(tmp_path, note, segments, (0.0, 120.0))

    assert inside["rows"] == 1, "the row overlaps 05:00-06:00"
    assert inside["row_words"] > 0
    assert before["rows"] == 0, "and does not reach a window it never touches"


def test_a_row_written_inside_a_code_fence_is_not_a_row(tmp_path):
    """Documenting the row format cost a note an anchor it never wrote.

    `read_note` strips HTML comments and frontmatter and read straight through
    a fenced block, so a note explaining its own format claimed the minute in
    its example. The fence is how a note says "this is a shape, not a claim".
    """
    segments = cov_spoken(60)
    note = (cov_row(2) + "\n```\n" + cov_row(9) + "```\n")

    result = cov_measure(tmp_path, note, segments, None)

    assert result["rows_in_note"] == 1, "the fenced row is an illustration"


def test_a_row_with_nothing_after_the_dash_is_not_a_row(tmp_path):
    """The padding these guards exist to catch, counted as content.

    A row carrying no text entered the row list and the `carried_per_row`
    denominator, so a note could improve the look of its own density by adding
    rows that say nothing at all.
    """
    segments = cov_spoken(60)
    note = cov_row(2) + "- `[03:05]` `SPOKEN` —\n"

    result = cov_measure(tmp_path, note, segments, None)

    assert result["rows_in_note"] == 1, "one row said something"
    assert len(result["misshapen_rows"]) >= 1, "and the empty one is reported"


def test_a_row_anchored_before_the_recording_started_is_reported(tmp_path):
    """A negative stamp made a whole row disappear, silently.

    `[-00:10]` is not a stamp this parser reads, and the line was not row-shaped
    enough to be reported as one either, so it fell out as prose: no row, no
    refusal, no count. The note then measured as though the claim had never been
    written, which is the one outcome a gate may not produce.
    """
    segments = cov_spoken(60)
    note = cov_row(2) + "- `[-00:10]` `SPOKEN` — a claim before the recording.\n"

    result = cov_measure(tmp_path, note, segments, None)

    assert result["rows_in_note"] == 1
    assert len(result["misshapen_rows"]) >= 1, "the negative stamp is named"


def test_a_window_plan_measured_in_nan_is_refused(tmp_path):
    """Every comparison against nan is False, so every guard passed.

    `plan` refuses a window that does not advance by comparing the geometry --
    and `nan <= 0` is False, `nan - nan <= 0` is False, so a plan measured in
    nothing sailed through the guard that exists to stop exactly that and
    returned one window over the whole recording.
    """
    segments = [{"start": 0.0, "end": 30.0, "text": "a"},
                {"start": 30.0, "end": 60.0, "text": "b"}]
    nan = float("nan")

    for window, overlap in ((nan, 10.0), (600.0, nan), (nan, nan)):
        with pytest.raises(ValueError, match="seconds"):
            nw.plan(segments, window, overlap)


def scan_corpus(tmp_path: Path, monkeypatch) -> None:
    """A policy with one refused literal and one note line carrying two stamps.

    Both are required before `--require-literals` will scan anything at all,
    and a case that skips them passes on the wordless refusal instead of the
    one it was written for.
    """
    corpus = tmp_path / "corpus"
    (corpus / "notes").mkdir(parents=True)
    (corpus / "notes" / "n.md").write_text(
        "- `[01:13]` to `[02:41]` COV -- a note line\n", encoding="utf-8")
    (corpus / "watch-quality.toml").write_text(
        'notes_dir = "notes"\nrefused_literals = ["inventedholdingco"]\n',
        encoding="utf-8")
    monkeypatch.setenv("WATCH_QUALITY_POLICY",
                       str(corpus / "watch-quality.toml"))
    monkeypatch.setenv("WATCH_QUALITY_ROOT", str(corpus))
    # `load` caches the first policy the process saw, so setting the variables
    # alone leaves an earlier test's empty policy in force and this case would
    # pass on the wordless refusal instead of the one it is about.
    from watchquality import wq_policy
    monkeypatch.setattr(wq_policy, "_CACHED", None)


def test_a_scan_with_nothing_in_force_cannot_be_quoted_as_clean(
        tmp_path, monkeypatch, capsys):
    """A zero that means "nothing was looked for" is not the zero a reader quotes.

    Run the scanner without the corpus and it prints `0 refused literal(s) and
    0 anchor set(s) ... in force, 0 corpus reference(s)` and exits 0. Pointed at
    the corpus it loads a thousand anchor sets and prints the same trailing
    zero, and only that run carries evidence. The line does not tell the two
    apart, so the shorter command produces a clean-looking artifact from a run
    that compared nothing.
    """
    from watchquality import wq_corpus_scan as wcs
    from watchquality import wq_policy

    monkeypatch.delenv("WATCH_QUALITY_POLICY", raising=False)
    monkeypatch.delenv("WATCH_QUALITY_ROOT", raising=False)
    monkeypatch.setenv("WATCH_QUALITY_NO_POLICY", "1")
    monkeypatch.setattr(wq_policy, "_CACHED", None)
    tree = tmp_path / "published"
    tree.mkdir()
    (tree / "a.md").write_text("# an ordinary page\n", encoding="utf-8")

    code = wcs.main([str(tree)])

    summary = [line for line in capsys.readouterr().err.splitlines()
               if "corpus reference(s)" in line]
    assert code == 0
    assert len(summary) == 1, summary
    assert "COULD NOT HAVE FOUND that class" in summary[0], summary[0]


def test_a_scan_with_words_in_force_is_not_marked_as_blind(tmp_path,
                                                           monkeypatch,
                                                           capsys):
    """The control: a run that really compared must read as a result.

    A warning printed on every run is a warning nobody reads, and it would make
    the honest artifact unquotable along with the empty one.
    """
    from watchquality import wq_corpus_scan as wcs

    scan_corpus(tmp_path, monkeypatch)
    tree = tmp_path / "published"
    tree.mkdir()
    (tree / "a.md").write_text("# an ordinary page\n", encoding="utf-8")

    code = wcs.main(["--require-literals", str(tree)])

    summary = [line for line in capsys.readouterr().err.splitlines()
               if "corpus reference(s)" in line]
    assert code == 0
    assert "COULD NOT HAVE FOUND" not in summary[0], summary[0]


def test_a_scan_that_could_not_read_anything_is_refused(tmp_path, monkeypatch):
    """The fourth incapacity, in the flag that names three.

    `--require-literals` refuses a run with no words, no files and no anchor
    sets in force, because each is a run that could not have found anything.
    A tree whose every file is bytes this reader cannot make text of is the
    same state: it exited 0 with a summary counting the files it WALKED as the
    files it scanned, and a push hook reads only that exit code.
    """
    from watchquality import wq_corpus_scan as wcs

    scan_corpus(tmp_path, monkeypatch)
    tree = tmp_path / "published"
    tree.mkdir()
    (tree / "a.md").write_bytes(bytes(range(256)) * 4)
    (tree / "b.md").write_bytes(bytes(range(255, -1, -1)) * 4)

    assert wcs.main(["--require-literals", str(tree)]) == 2


def test_an_example_indented_under_a_bullet_is_still_an_example(tmp_path):
    """A fence measures its indent from the bullet it sits under, not the margin.

    The reader dropped any line indented more than three spaces before it looked
    for a marker, which is a document-level rule. A note that writes its row
    format under a nested bullet indents the fence four spaces, no fence was
    seen, and the example counted as a claim -- the rule's own subject, reached
    by a different number of spaces.
    """
    fence = "`" * 3
    note = "\n".join([
        "- how a row is written",
        f"    {fence}",
        "    - `[00:30]` `SPOKEN` -- the shape, not something anybody said.",
        f"    {fence}",
        "",
        "- `[00:10]` `SPOKEN` -- a claim the writer actually made.",
        "",
    ])

    got = cov_measure(tmp_path, note, cov_transcript())

    assert got["rows_in_note"] == 1
    assert got["misshapen_rows"] == []


def test_a_fence_nobody_closed_does_not_eat_the_rest_of_the_note(tmp_path):
    """An unclosed fence is not a fence, and the rows after it are still claims.

    Reporting the stray line fixed the silence and left the loss: every row
    after the opener was still dropped, so a note that opened a block and forgot
    to close it was charged with minutes it had written from. Losing a person's
    claims is the worse error of the two, so the opener is reported and nothing
    is quoted.
    """
    fence = "`" * 3
    note = "\n".join([
        "- `[00:10]` `SPOKEN` -- a claim before the stray line.",
        fence,
        "- `[00:30]` `SPOKEN` -- a claim after it, which the writer did write.",
        "- `[00:50]` `SPOKEN` -- and one more after that.",
        "",
    ])

    got = cov_measure(tmp_path, note, cov_transcript())

    assert got["rows_in_note"] == 3
    assert got["misshapen_rows"] == [fence]


def test_an_illustration_buys_no_recall(tmp_path):
    """"Not a claim" has to mean every count, and recall is one of them.

    The row parse and the anchor walk both skip a fence; the note's text did
    not, so a note that documented the row format carried the words in its own
    example against the transcript and bought itself recall share for them.
    """
    segments = cov_transcript()
    fence = "`" * 3
    bare = "- `[00:10]` `SPOKEN` -- the speaker opens the topic.\n"
    with_example = "\n".join([
        bare.rstrip(),
        fence,
        f"- `[01:00]` `SPOKEN` -- {coined(6)}zone and {coined(7)}zone go here.",
        fence,
        "",
    ])

    plain = cov_measure(tmp_path, bare, segments)
    illustrated = cov_measure(tmp_path, with_example, segments)

    assert illustrated["recall"]["all"] == plain["recall"]["all"]


def test_a_path_that_would_not_open_is_named_however_it_fails(tmp_path,
                                                              monkeypatch,
                                                              capsys):
    """The census is only honest if the walk hands it everything it could not read.

    A link pointing at nothing and a directory nobody may list were both dropped
    before the reader ever saw them, so a whole subtree left the count in
    silence under a line a reader pastes as proof of coverage.
    """
    from watchquality import wq_corpus_scan as wcs

    if os.geteuid() == 0:
        pytest.skip("root lists a mode-000 directory, so half of this case "
                    "would not be unlistable")

    scan_corpus(tmp_path, monkeypatch)
    tree = tmp_path / "published"
    tree.mkdir()
    (tree / "a.md").write_text("# an ordinary page\n", encoding="utf-8")
    (tree / "b.md").symlink_to(tree / "never-written.md")
    shut = tree / "locked"
    shut.mkdir()
    (shut / "c.md").write_text("# a page inside it\n", encoding="utf-8")
    shut.chmod(0o000)
    try:
        code = wcs.main(["--require-literals", str(tree)])
    finally:
        shut.chmod(0o700)

    seen = capsys.readouterr()
    named = [line for line in seen.out.splitlines() if " E-READ " in line]
    assert code == 1
    assert any("b.md" in line for line in named), named
    assert any("locked" in line for line in named), named
    summary = [line for line in seen.err.splitlines()
               if "file(s) scanned" in line]
    assert summary[0].startswith("# 1 file(s) scanned "), summary[0]
    assert "2 that would not open" in summary[0], summary[0]


def test_a_link_to_nothing_inside_a_vendored_tree_is_still_just_vendored(
        tmp_path, monkeypatch):
    """Naming what could not be read must not out-rank the skip list.

    A dependency directory is full of links to things nobody installed, and
    reporting each one turns a clean tree's exit 0 into exit 1 -- a wrong
    verdict on a tree with nothing published wrong in it. The census answers for
    what this gate reads, and it does not read a vendored tree at all.
    """
    from watchquality import wq_corpus_scan as wcs

    if os.geteuid() == 0:
        pytest.skip("root lists a mode-000 directory, so half of this case "
                    "would not be unlistable")

    scan_corpus(tmp_path, monkeypatch)
    tree = tmp_path / "published"
    tree.mkdir()
    (tree / "a.md").write_text("# an ordinary page\n", encoding="utf-8")
    vendored = tree / "node_modules" / ".bin"
    vendored.mkdir(parents=True)
    (vendored / "tool").symlink_to(vendored / "never-installed.js")
    shut = tree / "node_modules" / "locked"
    shut.mkdir()
    (shut / "c.md").write_text("# a page inside it\n", encoding="utf-8")
    shut.chmod(0o000)
    try:
        code = wcs.main(["--require-literals", str(tree)])
    finally:
        shut.chmod(0o700)

    assert code == 0


def test_the_summary_counts_the_files_it_read(tmp_path, monkeypatch, capsys):
    """"N file(s) scanned" is the artifact a reader pastes as proof of coverage.

    It counted what was WALKED, so a tree half of which never became text read
    as a tree fully scanned -- and the two files that were not read fail in
    DIFFERENT ways: one is bytes no encoding accepts, the other never opened.
    Subtracting one kind and not the other still overstates the number, so both
    are named here and the count has to answer to both.
    """
    from watchquality import wq_corpus_scan as wcs

    if os.geteuid() == 0:
        pytest.skip("root opens a mode-000 file, so the unopenable half of "
                    "this case would not be unopenable")

    scan_corpus(tmp_path, monkeypatch)
    tree = tmp_path / "published"
    tree.mkdir()
    (tree / "a.md").write_text("# an ordinary page\n", encoding="utf-8")
    (tree / "b.md").write_bytes(bytes(range(256)) * 4)
    shut = tree / "c.md"
    shut.write_text("# a page nobody may open\n", encoding="utf-8")
    shut.chmod(0o000)
    try:
        wcs.main(["--require-literals", str(tree)])
    finally:
        # Left at 0 it is a directory pytest cannot tidy on some filesystems,
        # and this case is about a full disk.
        shut.chmod(0o600)

    summary = [line for line in capsys.readouterr().err.splitlines()
               if "file(s) scanned" in line]
    assert len(summary) == 1, summary
    assert summary[0].startswith("# 1 file(s) scanned "), summary[0]
    assert "1 not text this reader can make" in summary[0]
    assert "1 that would not open" in summary[0]


def test_a_scan_that_read_something_is_not_refused(tmp_path, monkeypatch):
    """The control: one readable file among the unreadable ones is enough.

    The flag answers "could this run have found anything", not "was every file
    perfect", so a tree that is mostly unreadable still scans on the strength
    of what it could read.
    """
    from watchquality import wq_corpus_scan as wcs

    scan_corpus(tmp_path, monkeypatch)
    tree = tmp_path / "published"
    tree.mkdir()
    (tree / "a.md").write_bytes(bytes(range(256)) * 4)
    (tree / "b.md").write_text("# an ordinary page\n", encoding="utf-8")

    assert wcs.main(["--require-literals", str(tree)]) == 0
