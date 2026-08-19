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
