"""End-to-end routing of --detail through watch.py on a local clip."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

WATCH = Path(__file__).resolve().parent.parent / "skills" / "watch" / "scripts" / "watch.py"


def _run(clip: Path, *args: str, env_extra: dict | None = None) -> str:
    env = dict(os.environ)
    env.pop("WATCH_DETAIL", None)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(WATCH), str(clip), "--no-whisper", *args],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_efficient_uses_keyframe_engine(cut_clip: Path):
    out = _run(cut_clip, "--detail", "efficient")
    assert "(keyframe" in out
    assert "**Detail:** efficient" in out


def test_balanced_uses_scene_engine(cut_clip: Path):
    out = _run(cut_clip, "--detail", "balanced")
    assert "(scene" in out
    assert "**Detail:** balanced" in out


def test_token_burner_uses_scene_engine(cut_clip: Path):
    out = _run(cut_clip, "--detail", "token-burner")
    assert "(scene" in out


def test_transcript_skips_frames(cut_clip: Path):
    out = _run(cut_clip, "--detail", "transcript")
    assert "skipped" in out
    assert "frame_0000.jpg" not in out


def test_flag_overrides_env(cut_clip: Path):
    out = _run(cut_clip, "--detail", "efficient", env_extra={"WATCH_DETAIL": "balanced"})
    assert "(keyframe" in out


def test_default_is_balanced(cut_clip: Path):
    out = _run(cut_clip)  # no flag, WATCH_DETAIL cleared
    assert "**Detail:** balanced" in out
    assert "(scene" in out


def test_timestamps_add_cue_frames_to_detail(cut_clip: Path):
    out = _run(cut_clip, "--detail", "balanced", "--timestamps", "1,3")
    assert "reason=transcript-cue" in out
    assert "reason=scene-change" in out  # detail frames still present (additive)


def test_timestamps_with_transcript_detail_is_cue_only(cut_clip: Path):
    out = _run(cut_clip, "--detail", "transcript", "--timestamps", "1,3")
    assert "reason=transcript-cue" in out
    assert "reason=scene-change" not in out
    assert "reason=keyframe" not in out


def _frame_lines(out: str) -> int:
    return sum(1 for line in out.splitlines() if "/frames/frame_" in line and "(t=" in line)


def test_dedup_collapses_static_by_default(static_clip: Path):
    out = _run(static_clip)  # solid blue → identical frames collapse to one
    assert "near-duplicate" in out
    assert _frame_lines(out) == 1


def test_no_dedup_preserves_static_frames(static_clip: Path):
    out = _run(static_clip, "--no-dedup")
    assert "near-duplicate" not in out
    assert _frame_lines(out) > 1


# --- choosing the decoder ----------------------------------------------------
# Captions win by default and are free, and on YouTube they are very often
# machine-generated. A note quotes its transcript, so which decoder produced it
# is part of the method rather than an implementation detail.

def _subtitled(clip: Path, tmp_path: Path) -> Path:
    """The clip with a sidecar .vtt beside it, which download.py picks up."""
    work = tmp_path / "download"
    work.mkdir(parents=True, exist_ok=True)
    target = work / "video.mp4"
    target.write_bytes(clip.read_bytes())
    (work / "video.en.vtt").write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nthe caption track\n",
        encoding="utf-8")
    return target


def test_captions_are_used_by_default(cut_clip: Path, tmp_path: Path):
    out = _run(_subtitled(cut_clip, tmp_path))
    assert "via captions" in out
    assert "the caption track" in out


def test_no_captions_ignores_the_subtitle_track(cut_clip: Path, tmp_path: Path):
    out = _run(_subtitled(cut_clip, tmp_path), "--no-captions")
    assert "the caption track" not in out
    assert "via captions" not in out


# The URL path parses captions at a DIFFERENT site from the local path — early,
# so the deictic-cue pass has something to scan before frames are chosen. The
# flag was added against the late site only, and the first real run came back
# captioned with no error anywhere. These stub yt-dlp and drive the early site.

def _url_run(monkeypatch, tmp_path: Path, *args: str) -> str:
    import sys as _sys
    _sys.path.insert(0, str(WATCH.parent))
    import watch

    vtt = tmp_path / "video.en.vtt"
    vtt.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nthe caption track\n",
                   encoding="utf-8")
    fetched = {"subtitle_path": str(vtt), "video_path": None, "downloaded": True,
               "info": {"title": "A Talk", "duration": 10, "id": "vid0000000"}}
    monkeypatch.setattr(watch, "fetch_captions", lambda *a, **k: dict(fetched))
    monkeypatch.setattr(watch, "download",
                        lambda *a, **k: {**fetched, "subtitle_path": None})
    monkeypatch.setattr(_sys, "argv",
                        ["watch.py", "https://example.com/watch?v=vid0000000",
                         "--detail", "transcript", "--no-whisper", *args])
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert watch.main() == 0
    return buf.getvalue()


def test_a_url_uses_its_captions_by_default(monkeypatch, tmp_path: Path):
    out = _url_run(monkeypatch, tmp_path)
    assert "via captions" in out
    assert "the caption track" in out


def test_no_captions_reaches_the_url_path_too(monkeypatch, tmp_path: Path):
    out = _url_run(monkeypatch, tmp_path, "--no-captions")
    assert "the caption track" not in out
    assert "via captions" not in out


def test_a_url_run_records_the_caption_file_its_segments_came_from(
        monkeypatch, tmp_path: Path):
    """properties I26(b) — `--make-note` and captions, end to end.

    The stub is the shipped one: `fetch_captions` returns the track, `download`
    returns the same dict with NO subtitle. `build_run` read the late `dl`, so
    the run said `source: captions` and named no file at all -- neither the
    oracle nor the witness -- and every gate downstream of it had nothing to
    re-check a quote against. The predicate had cases; the wiring did not.
    """
    import io
    import json
    import sys as _sys
    from contextlib import redirect_stdout

    _sys.path.insert(0, str(WATCH.parent))
    import watch

    vtt = tmp_path / "video.en.vtt"
    vtt.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nthe caption track\n",
                   encoding="utf-8")
    fetched = {"subtitle_path": str(vtt), "video_path": None, "downloaded": True,
               "info": {"title": "A Talk", "duration": 10, "id": "vid0000000"}}
    monkeypatch.setattr(watch, "fetch_captions", lambda *a, **k: dict(fetched))
    # The SECOND pass, which is where the bug lived: it rebinds `dl`, and the
    # track it returns is not the one the segments were parsed from.
    monkeypatch.setattr(watch, "download",
                        lambda *a, **k: {**fetched, "subtitle_path": None})
    monkeypatch.setenv("WATCH_NOTE_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(_sys, "argv",
                        ["watch.py", "https://example.com/watch?v=vid0000000",
                         "--detail", "efficient", "--no-whisper", "--make-note"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert watch.main() == 0
    assert "via captions" in buf.getvalue()

    runs = sorted((tmp_path / "runs").rglob("run.json"))
    assert len(runs) == 1, runs
    transcript = json.loads(runs[0].read_text(encoding="utf-8"))["transcript"]
    assert transcript["source"] == "captions"
    assert transcript["subtitle_path"], transcript
    assert Path(transcript["subtitle_path"]).name == "video.en.vtt"


def _whisper_run(monkeypatch, tmp_path: Path, transcribe, *args: str,
                 credential: tuple = ("openrouter", "sk")):
    """Drive watch.main() with yt-dlp, ffprobe and Whisper all stubbed.

    `credential` is what `load_api_key` returns, and it is a PARAMETER rather
    than something a caller patches beforehand: this helper patches the same
    name, so the last write wins and a caller's stub was being overwritten.
    Guarding the default by asking whether the installed `load_api_key` is a
    lambda works only for as long as every stub happens to be written as one.
    """
    import io
    import sys as _sys
    from contextlib import redirect_stdout

    _sys.path.insert(0, str(WATCH.parent))
    import watch

    (tmp_path / "v.mp4").write_bytes(b"\x00")
    fetched = {"subtitle_path": None, "video_path": str(tmp_path / "v.mp4"),
               "downloaded": True,
               "info": {"title": "A Talk", "duration": 600, "id": "vid0000000"}}
    monkeypatch.setattr(watch, "fetch_captions", lambda *a, **k: dict(fetched))
    monkeypatch.setattr(watch, "download", lambda *a, **k: dict(fetched))
    monkeypatch.setattr(watch, "get_metadata", lambda *a, **k: {
        "duration_seconds": 600.0, "width": 640, "height": 360,
        "codec": "h264", "size_bytes": 1, "has_audio": True})
    monkeypatch.setattr(watch, "load_api_key", lambda which=None: credential)
    monkeypatch.setattr(watch, "transcribe_video", transcribe)
    monkeypatch.setattr(_sys, "argv",
                        ["watch.py", "https://example.com/watch?v=vid0000000",
                         "--detail", "transcript", *args])
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = watch.main()
    return code, buf.getvalue()


def test_a_failed_transcription_exits_non_zero(monkeypatch, tmp_path: Path):
    """Fails if the run still returns 0: a batch driven off exit codes records
    a video with no transcript as a success.

    Run 1 of FIhj0yb9KPI on 2026-09-07 did exactly that -- all six chunks
    refused as too coarse, `Transcript: none available`, exit 0.
    """
    def refuse(*a, **k):
        raise SystemExit("the transcription is too coarse to anchor")

    code, out = _whisper_run(monkeypatch, tmp_path, refuse)
    assert code == 1
    assert "Transcript:** none available" in out
    assert "Transcription failed" in out


def test_a_focused_run_over_silence_is_not_a_failed_transcription(
        monkeypatch, tmp_path: Path):
    """Fails if the flag reads `transcript_segments` instead of what Whisper returned.

    Measured by the design refutation on 2026-09-07: a complete 143-segment
    transcript spanning 0:30-10:00, asked for `--start 0:00 --end 0:20`, exited 1
    and claimed Whisper returned nothing. Whisper returned everything; the focus
    window is simply empty, which is the correct answer. The repository
    recommends exactly this input at watch.py:454-461.
    """
    def healthy(*a, **k):
        return ([{"start": 30.0 + i, "end": 31.0 + i, "text": f"line {i}"}
                 for i in range(143)], "openrouter")

    code, out = _whisper_run(monkeypatch, tmp_path, healthy,
                             "--start", "0:00", "--end", "0:20")
    assert code == 0
    assert "Transcription failed" not in out


def test_a_failed_transcription_still_writes_its_run_record(
        monkeypatch, tmp_path: Path):
    """Fails if the exit status is an early return before build_run/write_run.

    A run that failed is the run you most want a record of. The draft returned
    before watch.py:519-535 and wrote no run.json at all.
    """
    import json

    def refuse(*a, **k):
        raise SystemExit("the transcription is too coarse to anchor")

    monkeypatch.setenv("WATCH_NOTE_DIR", str(tmp_path / "runs"))
    code, out = _whisper_run(monkeypatch, tmp_path, refuse, "--make-note")
    assert code == 1
    assert "## Note mode" in out
    runs = sorted((tmp_path / "runs").rglob("run.json"))
    assert len(runs) == 1, runs
    assert json.loads(runs[0].read_text(encoding="utf-8"))["transcript"]["segments"] == 0


def test_a_missing_credential_is_a_failed_run(monkeypatch, tmp_path: Path):
    """Fails if it exits 0: a batch against an environment with no key
    records every video as a success with no transcript.

    Unlike `--no-whisper`, an absent or mistyped key is not a choice made per
    run — found by the failure-paths review, 2026-09-08.
    """
    def never_called(*a, **k):
        raise AssertionError("transcribe_video ran without a credential")

    code, out = _whisper_run(monkeypatch, tmp_path, never_called,
                             credential=(None, None))
    assert code == 1
    assert "Transcription unavailable" in out


def test_a_video_with_no_speech_is_a_failed_run(monkeypatch, tmp_path: Path):
    """Pins the operator's ruling of 2026-09-08: exit 1, deliberately.

    A silent video yields no transcript and is useless for a note, so the batch
    stops on it rather than recording it as done. Fails if someone later
    'repairs' this back to exit 0 on the grounds that nothing was lost.
    """
    def silent(*a, **k):
        raise SystemExit("Whisper returned no transcript segments")

    code, out = _whisper_run(monkeypatch, tmp_path, silent)
    assert code == 1
    assert "Transcription failed" in out
