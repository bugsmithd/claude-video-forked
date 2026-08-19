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
