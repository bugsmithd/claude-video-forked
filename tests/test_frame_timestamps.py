"""A frame's reported timestamp must describe the frame that was decoded.

Both extractors previously reported an intent — the requested seek target for
cue frames, and `start + index/fps` arithmetic for focus-window sweeps — rather
than a measurement. On a clip whose color names its own second, that shows up as
a frame whose color disagrees with its own label.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import frames
from conftest import TIMED_RGB, TIMED_SEG


def _rgb(path: Path) -> tuple[int, int, int]:
    """Average the JPEG down to one pixel and read it back."""
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(path),
            "-vf", "scale=1:1",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()
    return tuple(result.stdout[:3])


def _color_bucket(path: Path) -> int:
    """Which of the fixture's five fills this frame actually shows."""
    r, g, b = _rgb(path)
    dists = [(r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2 for cr, cg, cb in TIMED_RGB]
    return dists.index(min(dists))


def _assert_label_matches_pixels(out: list[dict]) -> None:
    mismatches = [
        (fr["timestamp_seconds"], int(fr["timestamp_seconds"] // TIMED_SEG), _color_bucket(Path(fr["path"])))
        for fr in out
        if int(fr["timestamp_seconds"] // TIMED_SEG) != _color_bucket(Path(fr["path"]))
    ]
    assert not mismatches, f"label/content mismatch (ts, expected_bucket, actual_bucket): {mismatches}"


def test_cue_frames_report_the_second_they_show(timed_clip: Path, tmp_path: Path):
    """`--timestamps` frames must show the moment they claim to show."""
    requested = [1.0, 7.0, 13.0, 18.0]
    out, meta = frames.extract_at_timestamps(str(timed_clip), tmp_path / "f", requested)

    assert meta["engine"] == "timestamps"
    assert len(out) == len(requested)
    _assert_label_matches_pixels(out)


def test_cue_frame_timestamps_stay_within_tolerance(timed_clip: Path, tmp_path: Path):
    """A reported time may be measured rather than requested, but not both wrong
    and silent: whatever is reported must sit within a frame of what was asked."""
    requested = [7.0, 13.0]
    out, _ = frames.extract_at_timestamps(str(timed_clip), tmp_path / "f", requested)

    drift = [abs(fr["timestamp_seconds"] - want) for fr, want in zip(out, requested)]
    assert max(drift) <= 0.2, f"drift beyond one frame at 10fps: {drift}"


def test_stamped_paths_carry_their_own_second(timed_clip: Path, tmp_path: Path):
    """A frame read out of a batch must be identifiable from its path alone."""
    out, _ = frames.extract_at_timestamps(str(timed_clip), tmp_path / "f", [7.0, 13.0])
    stamped = frames.stamp_paths(out)

    assert [Path(fr["path"]).name for fr in stamped] == [
        "cue_0000_t00m07s.jpg",
        "cue_0001_t00m13s.jpg",
    ]
    assert all(Path(fr["path"]).exists() for fr in stamped)
    _assert_label_matches_pixels(stamped)


def test_stamping_is_idempotent(timed_clip: Path, tmp_path: Path):
    """Re-running must not append a second stamp or lose the file."""
    out, _ = frames.extract_at_timestamps(str(timed_clip), tmp_path / "f", [7.0])
    once = [Path(fr["path"]).name for fr in frames.stamp_paths(out)]
    twice = [Path(fr["path"]).name for fr in frames.stamp_paths(out)]

    assert once == twice
    assert all(Path(fr["path"]).exists() for fr in out)


def test_stamp_time_formats_past_an_hour():
    assert frames._stamp_time(7.0) == "00m07s"
    assert frames._stamp_time(135.4) == "02m15s"
    assert frames._stamp_time(3735.0) == "1h02m15s"


def test_focus_window_frames_report_the_second_they_show(timed_clip: Path, tmp_path: Path):
    """`--start/--end` sweeps label frames by arithmetic; the pixels must agree."""
    out = frames.extract(
        str(timed_clip), tmp_path / "f", fps=1.0, start_seconds=7.0, end_seconds=11.0,
    )

    assert len(out) >= 3
    assert all(7.0 <= fr["timestamp_seconds"] <= 11.0 for fr in out)
    _assert_label_matches_pixels(out)
