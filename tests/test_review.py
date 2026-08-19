"""The review-lane briefs: bounded batches, lane isolation, refute-by-default.

The template's whole claim is that three readers who cannot see each other beat
one reader who can. These tests hold the two mechanical halves of that — the
briefs really are per-lane, and the frames really are batched — plus the rule
that a repair may not author.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "watch" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import review  # noqa: E402


def _run(n_frames: int = 45) -> dict:
    return {
        "duration_seconds": 600.0,
        "frames": [{"seconds": float(i * 10), "path": f"/f/{i:03d}.jpg",
                    "reason": "scene-change"} for i in range(n_frames)],
        "transcript": {"source": "captions", "segment_starts": [0.0, 5.0, 9.5]},
        "deduped_seconds": [12.0, 13.0],
    }


def _write_run(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "run.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# --- batching ----------------------------------------------------------------

def test_batches_are_capped_and_lose_nothing():
    frames = _run(103)["frames"]
    groups = review.batches(frames)
    assert all(len(g) <= review.BATCH for g in groups)
    assert sum(len(g) for g in groups) == 103
    assert [f for g in groups for f in g] == frames


def test_a_single_short_batch_is_still_a_batch():
    assert len(review.batches(_run(3)["frames"])) == 1


def test_no_frames_yields_no_batches():
    assert review.batches([]) == []


def test_every_frame_path_reaches_exactly_one_batch():
    run = _run(45)
    text = review.frame_section(run["frames"])
    for frame in run["frames"]:
        assert text.count(frame["path"]) == 1


def test_a_run_with_no_frames_says_so_rather_than_printing_nothing():
    text = review.frame_section([])
    assert "transcript alone" in text


# --- lane isolation ----------------------------------------------------------

def test_each_lane_asks_its_own_question():
    run = _run()
    questions = {lane: review.brief(lane, run, Path("n.md"), Path("r.json"))
                 for lane in review.LANES}
    assert "TRUE against the recording" in questions["facts"]
    assert "carried HONESTLY" in questions["quality"]
    assert "LEAVE OUT" in questions["coverage"]


def test_a_note_only_lane_is_not_handed_the_frame_list():
    """quality reads the note against itself; frames would only invite it to
    re-do the facts lane's job badly."""
    text = review.brief("quality", _run(), Path("n.md"), Path("r.json"))
    assert "/f/000.jpg" not in text
    assert "## Frames" not in text


def test_the_frame_lanes_do_get_the_frames():
    for lane in ("facts", "coverage"):
        text = review.brief(lane, _run(), Path("n.md"), Path("r.json"))
        assert "/f/000.jpg" in text and "## Frames" in text


def test_every_brief_carries_the_refute_stance():
    for lane in review.LANES:
        text = review.brief(lane, _run(), Path("n.md"), Path("r.json"))
        assert "REFUTE BY DEFAULT" in text
        assert "Do not open the other lanes' output." in text


def test_the_note_is_named_as_the_thing_judged_not_the_evidence():
    text = review.brief("facts", _run(), Path("mynote.md"), Path("r.json"))
    assert "mynote.md" in text
    assert "never the evidence for a judgement" in text


# --- what the run tells a lane ----------------------------------------------

def test_legal_anchor_seconds_are_stated():
    text = review.brief("facts", _run(), Path("n.md"), Path("r.json"))
    assert "3 segment start(s)" in text
    assert "only seconds a claim may be anchored to" in text


def test_collapsed_seconds_are_explained_when_there_are_any():
    with_drops = review.brief("facts", _run(), Path("n.md"), Path("r.json"))
    assert "2 second(s) were collapsed" in with_drops

    run = _run()
    run["deduped_seconds"] = []
    assert "were collapsed" not in review.brief("facts", run, Path("n.md"),
                                                Path("r.json"))


# --- files on disk -----------------------------------------------------------

def test_write_all_produces_one_brief_per_lane_plus_a_disposition(tmp_path: Path):
    run_path = _write_run(tmp_path, _run())
    written = review.write_all(run_path, tmp_path / "n.md", tmp_path / "review")
    names = sorted(p.name for p in written)
    assert names == ["DISPOSITION.md", "lane-coverage.md", "lane-facts.md",
                     "lane-quality.md"]
    assert all(p.is_file() for p in written)


def test_the_repair_policy_forbids_authoring(tmp_path: Path):
    """The one rule that keeps a review from smuggling in unchecked claims."""
    run_path = _write_run(tmp_path, _run())
    review.write_all(run_path, tmp_path / "n.md", tmp_path / "review")
    text = (tmp_path / "review" / "DISPOSITION.md").read_text(encoding="utf-8")
    flat = " ".join(text.split())  # the prose is hard-wrapped; the rule is not
    assert "may not write a claim the rows do not carry" in flat
    assert "authoring, not repairing" in flat


def test_a_malformed_run_json_is_refused_not_guessed(tmp_path: Path):
    bad = tmp_path / "run.json"
    bad.write_text("{not json", encoding="utf-8")
    try:
        review.load_run(bad)
    except SystemExit as exc:
        assert "cannot read" in str(exc)
    else:
        raise AssertionError("a malformed run.json must not parse")


def test_selftest_passes():
    assert review.selftest() == 0


# --- whether this run can reach this note ------------------------------------
# The first real lane run was pointed at a re-capture of the video rather than
# at the run the note was written from, because that note predates run.json and
# there was no other brief to build. All three lanes worked it out separately,
# one UNTESTABLE claim at a time. The count belongs in the brief.

def _note(tmp_path: Path, seconds: list[int]) -> Path:
    rows = "\n".join(f"- a claim `[{s // 60:02d}:{s % 60:02d}]`" for s in seconds)
    path = tmp_path / "n.md"
    path.write_text(f"---\nvideo_id: x\n---\n\n{rows}\n", encoding="utf-8")
    return path


def test_anchors_are_read_in_both_shapes():
    assert review.anchor_seconds("`[02:58]` `[1:02:58]` `[00:00]`") == [178, 3778, 0]


def test_prose_that_looks_like_a_time_is_not_an_anchor():
    assert review.anchor_seconds("at 02:58 he says, and [02:58] too") == []


def test_the_widest_gap_is_the_widest_one():
    assert review.widest_gap([10, 20, 300, 310], 320.0) == (20, 300)


def test_no_frames_makes_the_whole_runtime_the_gap():
    assert review.widest_gap([], 600.0) == (0, 600)


def test_a_gap_of_nothing_is_not_printed(tmp_path: Path):
    """With no frames and no duration there is nothing to measure, and `0s to
    0s` reads as "this run covers everything" — the opposite of the truth."""
    run = _run(0)
    run["duration_seconds"] = 0.0
    assert review.widest_gap([], 0.0) == (0, 0)
    text = review.brief("facts", run, _note(tmp_path, [0, 100]), Path("r.json"))
    assert "The note carries 2 anchor(s)" in text
    assert "0s to 0s" not in text
    assert "longest stretch" not in text


def test_the_spoken_count_is_the_segment_starts(tmp_path: Path):
    """`framed` and `spoken` answer different questions, and the fixture keeps
    them unequal so that swapping the two would fail rather than pass."""
    run = _run(3)                      # frames at 0s, 10s, 20s
    run["transcript"]["segment_starts"] = [0.0, 5.0, 9.5]   # whole seconds 0, 5, 9
    note = _note(tmp_path, [0, 5, 10, 20, 100])
    stats = review.reach(note, run)
    assert stats["anchors"] == 5
    assert stats["framed"] == 3        # 0s, 10s, 20s have frames
    assert stats["spoken"] == 2        # 0s and 5s are segment starts; 10s is not
    text = review.brief("facts", run, note, Path("r.json"))
    assert "3 of them fall on a second this run kept a frame for" in text
    assert "2 fall on a transcript segment start" in text


def test_a_run_that_cannot_reach_the_note_says_so(tmp_path: Path):
    run = _run(3)  # frames at 0s, 10s, 20s only
    text = review.brief("facts", run, _note(tmp_path, [0, 100, 200, 300]),
                        Path("r.json"))
    assert "The note carries 4 anchor(s). 1 of them" in text
    assert "Most of this note's anchors have no frame in this run." in text
    assert "must not be reported as refuted" in text


def test_a_run_that_reaches_the_note_does_not_nag(tmp_path: Path):
    text = review.brief("facts", _run(), _note(tmp_path, [0, 10, 20, 30]),
                        Path("r.json"))
    assert "The note carries 4 anchor(s). 4 of them" in text
    assert "Most of this note's anchors" not in text


def test_a_missing_note_does_not_block_the_briefs(tmp_path: Path):
    text = review.brief("facts", _run(), tmp_path / "gone.md", Path("r.json"))
    assert "REFUTE BY DEFAULT" in text
    assert "How much of the note this run can answer" not in text


def test_the_reach_count_reaches_the_lane_with_no_frames(tmp_path: Path):
    """The quality lane gets no frame list and needs this number most: a note
    that is nearly all ON-SCREEN claims leaves it very little it may grade."""
    text = review.brief("quality", _run(3), _note(tmp_path, [0, 100, 200]),
                        Path("r.json"))
    assert "The note carries 3 anchor(s)" in text
    assert "/f/000.jpg" not in text
