"""`--make-note`: the mode that keeps the evidence instead of deleting it.

Every behaviour here asserts TWICE — present with the flag, absent without it.
The whole claim of the mode is that the original path is untouched when nobody
asks for it, and a default pinned one layer below the CLI is not pinned.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "watch" / "scripts"
WATCH = SCRIPTS / "watch.py"
sys.path.insert(0, str(SCRIPTS))

import config  # noqa: E402
import notemode  # noqa: E402


def _run(clip: Path, *args: str, note_dir: Path | None = None) -> str:
    env = dict(os.environ)
    env.pop("WATCH_DETAIL", None)
    if note_dir is not None:
        env["WATCH_NOTE_DIR"] = str(note_dir)
    proc = subprocess.run(
        [sys.executable, str(WATCH), str(clip), "--no-whisper", *args],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _run_json(root: Path) -> dict:
    hits = sorted(root.rglob("run.json"))
    assert len(hits) == 1, f"expected one run.json, found {hits}"
    return json.loads(hits[0].read_text(encoding="utf-8"))


# --- the mode is off unless asked for ---------------------------------------

def test_without_the_flag_the_run_is_still_disposable(cut_clip: Path, tmp_path: Path):
    out = _run(cut_clip, note_dir=tmp_path)
    assert "delete when done" in out
    assert "## Note mode" not in out
    assert not list(tmp_path.rglob("run.json"))


def test_with_the_flag_the_run_is_kept(cut_clip: Path, tmp_path: Path):
    out = _run(cut_clip, "--make-note", note_dir=tmp_path)
    assert "delete when done" not in out
    assert "## Note mode" in out
    assert "**kept**" in out
    assert _run_json(tmp_path)["schema"] == 1


# --- the resolution floor ----------------------------------------------------

def test_note_mode_raises_the_resolution_floor(cut_clip: Path, tmp_path: Path):
    _run(cut_clip, "--make-note", note_dir=tmp_path)
    assert _run_json(tmp_path)["resolution"] == config.NOTE_RESOLUTION == 1024


def test_ordinary_runs_keep_the_ordinary_width(cut_clip: Path, tmp_path: Path):
    out = _run(cut_clip, note_dir=tmp_path)
    assert f"max {config.DEFAULT_RESOLUTION}px wide" in out
    assert config.DEFAULT_RESOLUTION == 768


def test_an_explicit_width_beats_the_floor(cut_clip: Path, tmp_path: Path):
    """The floor is a default, not an override: asking for 512 gets 512."""
    _run(cut_clip, "--make-note", "--resolution", "512", note_dir=tmp_path)
    assert _run_json(tmp_path)["resolution"] == 512


# --- what run.json has to carry ---------------------------------------------

def test_every_frame_carries_its_second_and_a_digest(cut_clip: Path, tmp_path: Path):
    _run(cut_clip, "--make-note", note_dir=tmp_path)
    run = _run_json(tmp_path)
    assert run["frames"], "a clip with cuts must yield frames"
    for frame in run["frames"]:
        assert frame["sha256"] and len(frame["sha256"]) == notemode.DIGEST_CHARS
        assert isinstance(frame["seconds"], float)
        assert Path(frame["path"]).is_file()


def test_the_collapsed_seconds_are_listed_not_just_counted(static_clip: Path,
                                                           tmp_path: Path):
    """A held slide is the case this exists for: one frame survives, and the
    seconds it stands in for are still on the record."""
    _run(static_clip, "--make-note", note_dir=tmp_path)
    run = _run_json(tmp_path)
    assert len(run["frames"]) == 1
    assert len(run["deduped_seconds"]) > 0
    assert run["deduped_seconds"] == sorted(run["deduped_seconds"])


# --- the run record must not name the wrong transcript ----------------------
# `--no-captions` downloads a caption track and then decodes the audio instead.
# Recording that unused track as the transcript's own file handed a later reader
# the wrong artifact to re-check a quote against.

def _with_captions(clip: Path, tmp_path: Path) -> Path:
    work = tmp_path / "src"
    work.mkdir(parents=True, exist_ok=True)
    target = work / "video.mp4"
    target.write_bytes(clip.read_bytes())
    (work / "video.en.vtt").write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nthe caption track\n",
        encoding="utf-8")
    return target


def test_the_transcript_names_the_file_it_came_from(cut_clip: Path, tmp_path: Path):
    _run(_with_captions(cut_clip, tmp_path), "--make-note",
         note_dir=tmp_path / "runs")
    transcript = _run_json(tmp_path / "runs")["transcript"]
    assert transcript["source"] == "captions"
    assert transcript["subtitle_path"].endswith("video.en.vtt")
    assert transcript["unused_subtitle_path"] is None


def test_an_unused_caption_track_is_not_the_transcript(cut_clip: Path, tmp_path: Path):
    _run(_with_captions(cut_clip, tmp_path), "--make-note", "--no-captions",
         note_dir=tmp_path / "runs")
    transcript = _run_json(tmp_path / "runs")["transcript"]
    assert transcript["source"] != "captions"
    assert transcript["subtitle_path"] is None
    assert transcript["unused_subtitle_path"].endswith("video.en.vtt")


def _run_record(source: str | None, parsed: str | None) -> dict:
    return notemode.build_run(
        source="url", video_id="VID", work=Path("/tmp/w"), info={},
        duration=1.0, resolution=360, detail="low", video_path=None,
        frames=[], dropped_seconds=[], transcript_source=source,
        transcript_segments=[{"start": 0.0}], subtitle_path="/tmp/w/v.en.vtt",
        parsed_from=parsed)["transcript"]


def test_a_run_that_says_captions_names_the_file_it_parsed(tmp_path: Path):
    """properties I26(b), the worst finding in that review.

    `watch.py` parses the track off the EARLY `dl`, rebinds `dl` from the
    second yt-dlp pass, then hands `build_run` the late one. When the late one
    carries no subtitle the run says `source: captions` and names no file at
    all -- neither oracle nor witness -- so nothing downstream can re-check a
    quote.
    """
    got = _run_record("captions", None)
    assert got["subtitle_path"] is None or got["source"] != "captions", got
    assert _run_record("captions", "/tmp/w/v.en.vtt")["subtitle_path"] == \
        "/tmp/w/v.en.vtt"


def test_a_second_pass_that_repicks_the_track_does_not_rewrite_history(tmp_path):
    """properties I28: the two yt-dlp passes both call `_pick_subtitle`.

    The realistic form of I26(b) is not a null path, it is a path to a
    DIFFERENT transcript than the one the segments were parsed from.
    """
    got = _run_record("captions", "/tmp/w/v.en.vtt")
    assert got["subtitle_path"] == "/tmp/w/v.en.vtt"
    assert got["unused_subtitle_path"] is None
    notemode_record = notemode.build_run(
        source="url", video_id="VID", work=Path("/tmp/w"), info={},
        duration=1.0, resolution=360, detail="low", video_path=None,
        frames=[], dropped_seconds=[], transcript_source="captions",
        transcript_segments=[{"start": 0.0}],
        subtitle_path="/tmp/w/v.de-orig.vtt",
        parsed_from="/tmp/w/v.en.vtt")["transcript"]
    assert notemode_record["subtitle_path"] == "/tmp/w/v.en.vtt"
    assert notemode_record["unused_subtitle_path"] == "/tmp/w/v.de-orig.vtt"


def test_a_source_label_that_merely_begins_with_captions_is_not_captions():
    """properties I26(a): `captionsless` was treated as captions."""
    assert _run_record("captionsless", None)["subtitle_path"] is None


def test_rehome_leaves_a_sibling_directory_alone(tmp_path: Path):
    """properties I27: `startswith` with no separator boundary.

    A sibling whose name merely BEGINS with the run's name had its recorded
    paths rewritten to a location that does not exist.
    """
    work = tmp_path / "pending" / "run"
    work.mkdir(parents=True)
    sibling = tmp_path / "pending" / "run-old"
    sibling.mkdir()
    stray = sibling / "video.en.vtt"
    stray.write_text("WEBVTT\n", encoding="utf-8")
    dl = {"subtitle_path": str(stray), "video_path": None}

    notemode.rehome(work, tmp_path / "abc123" / "run-01", dl)

    assert dl["subtitle_path"] == str(stray)
    assert Path(dl["subtitle_path"]).is_file()


def test_the_source_digest_is_recorded(cut_clip: Path, tmp_path: Path):
    _run(cut_clip, "--make-note", note_dir=tmp_path)
    assert _run_json(tmp_path)["video_sha256"] == notemode.sha256(cut_clip)


# --- runs never overwrite each other ----------------------------------------

def test_a_second_run_lands_beside_the_first(cut_clip: Path, tmp_path: Path):
    _run(cut_clip, "--make-note", note_dir=tmp_path)
    _run(cut_clip, "--make-note", note_dir=tmp_path)
    runs = sorted(p.parent.name for p in tmp_path.rglob("run.json"))
    assert runs == ["run-01", "run-02"]


# --- units -------------------------------------------------------------------

def test_note_run_dir_counts_up(tmp_path: Path):
    first = config.note_run_dir(tmp_path, "vid")
    assert first.name == "run-01"
    first.mkdir(parents=True)
    assert config.note_run_dir(tmp_path, "vid").name == "run-02"


def test_video_id_prefers_the_platform_id():
    assert notemode.video_id_of("https://example/x", {"id": "abc123"}) == "abc123"


def test_a_local_file_gets_a_stable_id_from_its_path():
    a = notemode.video_id_of("/tmp/recording.mp4", {})
    b = notemode.video_id_of("/tmp/recording.mp4", {})
    c = notemode.video_id_of("/other/recording.mp4", {})
    assert a == b and a != c and a.startswith("local-")


def test_rehome_moves_the_directory_and_fixes_the_paths(tmp_path: Path):
    work = tmp_path / "pending" / "run-01"
    (work / "download").mkdir(parents=True)
    subs = work / "download" / "v.en.vtt"
    subs.write_text("WEBVTT\n", encoding="utf-8")
    dl = {"subtitle_path": str(subs), "video_path": None}

    target = tmp_path / "abc123" / "run-01"
    got = notemode.rehome(work, target, dl)

    assert got == target
    assert not work.exists()
    assert Path(dl["subtitle_path"]).is_file()
    assert dl["subtitle_path"].startswith(str(target))


def test_rehome_takes_the_staging_dir_with_it(tmp_path: Path):
    """`pending/` is scaffolding, and scaffolding left standing after every URL
    run reads like a second run that failed."""
    work = tmp_path / "pending" / "run-01"
    work.mkdir(parents=True)
    notemode.rehome(work, tmp_path / "abc123" / "run-01", {})
    assert not (tmp_path / "pending").exists()


def test_rehome_leaves_a_staging_dir_another_run_is_using(tmp_path: Path):
    work = tmp_path / "pending" / "run-01"
    work.mkdir(parents=True)
    inflight = tmp_path / "pending" / "run-02"
    inflight.mkdir()
    notemode.rehome(work, tmp_path / "abc123" / "run-01", {})
    assert inflight.is_dir()


def test_rehome_keeps_the_run_when_the_move_fails(tmp_path: Path):
    """Losing a run to a tidy-up is the one failure this mode cannot have."""
    work = tmp_path / "pending" / "run-01"
    work.mkdir(parents=True)
    blocked = tmp_path / "taken" / "run-01"
    blocked.mkdir(parents=True)
    (blocked / "occupied").write_text("x", encoding="utf-8")

    assert notemode.rehome(work, blocked, {}) == work
    assert work.exists()


def test_sha256_of_a_missing_file_is_none(tmp_path: Path):
    assert notemode.sha256(tmp_path / "nope") is None
    assert notemode.sha256(None) is None
