"""Shared pytest fixtures: ffmpeg-synthesized clips and scripts/ on sys.path."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# NO AMBIENT CORPUS. Both variables are read at IMPORT time by the package, so
# they are cleared here -- in the rootdir conftest, before any test module
# imports `watchquality` -- rather than in a fixture that would run too late.
#
# Measured 2026-08-22: with `$WATCH_QUALITY_POLICY` pointing at a real corpus,
# six cases in `test_spec_note_contract.py` fail. They are not wrong; the corpus
# policy adds required lanes and oracle rules, so a fixture note carrying one
# defect carries four, and a case asserting "1 defect(s) outstanding" reads a 4.
# The suite grades the PACKAGE, and a suite whose answer depends on which shell
# started it cannot be a gate -- which is exactly how it was found: the commit
# hook exports the policy for the leak scan, and the suite inherited it.
for _ambient in ("WATCH_QUALITY_POLICY", "WATCH_QUALITY_ROOT"):
    os.environ.pop(_ambient, None)

# Make the bundled scripts importable (mirrors watch.py's sys.path insert).
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "watch" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def pytest_configure(config: pytest.Config) -> None:
    """Register `slow`, which today means the rule-mutation run and nothing else.

    That run copies the tree and starts a fresh interpreter per mutant, so it
    takes about five minutes against fifteen seconds for everything else. It
    stays ON by default: it is the only check that can tell a rule the table
    enforces from a rule the table merely describes, and a check nobody runs
    answers nothing. `-m "not slow"` is there for the edit-run loop.
    """
    config.addinivalue_line(
        "markers", "slow: minutes, not seconds; deselect with -m 'not slow'")

# The smallest thing that is a rendering: one segment with a start, an end and
# words. Fixtures across this suite used to write `{}` wherever a transcript was
# meant -- a file that opens, parses, and carries nothing. That stood in for a
# rendering everywhere until the oracle check started asking whether a gate can
# actually READ what a note names, at which point ten fixtures were revealed to
# be asserting against a file no gate could read.
RENDERING = '{"segments": [{"start": 0.0, "end": 2.0, "text": "hello there"}]}\n'

# 14 visually distinct fills → 14 abrupt cuts → x264 emits a keyframe per cut.
COLORS = [
    "red", "green", "blue", "white", "black", "yellow", "cyan",
    "magenta", "gray", "orange", "purple", "brown", "navy", "olive",
]


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd)}\n{result.stderr}")


def build_cut_clip(
    path: Path,
    n: int = 14,
    seg: float = 0.4,
    size: str = "320x240",
    fps: int = 10,
) -> None:
    """Concatenate ``n`` solid-color segments into one clip with ``n`` cuts.

    Each color change is a hard scene cut, so the scene selector finds ~n-1
    changes. x264's own scenecut detection is unreliable on flat fills, so we
    force a keyframe at every ``seg`` boundary — giving ~n real keyframes for
    the keyframe engine to find.
    """
    inputs: list[str] = []
    for i in range(n):
        color = COLORS[i % len(COLORS)]
        inputs += ["-f", "lavfi", "-t", str(seg), "-i", f"color=c={color}:s={size}:r={fps}"]
    streams = "".join(f"[{i}:v]" for i in range(n))
    filt = f"{streams}concat=n={n}:v=1:a=0[out]"
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        *inputs,
        "-filter_complex", filt, "-map", "[out]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-force_key_frames", f"expr:gte(t,n_forced*{seg})",
        str(path),
    ])


def build_static_clip(
    path: Path,
    duration: float = 3.0,
    size: str = "320x240",
    fps: int = 10,
) -> None:
    """One solid color: 1 keyframe, no scene changes → triggers both fallbacks."""
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-t", str(duration), "-i", f"color=c=blue:s={size}:r={fps}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "600",
        str(path),
    ])


# 5 fills × 4s = a 20s clip whose color names its own timestamp: bucket = t // 4.
TIMED_COLORS = ["red", "lime", "blue", "yellow", "magenta"]
TIMED_RGB = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (255, 0, 255)]
TIMED_SEG = 4.0


def build_timed_clip(
    path: Path,
    seg: float = TIMED_SEG,
    size: str = "320x240",
    fps: int = 10,
) -> None:
    """Color-coded clock with ONE keyframe, at t=0.

    Scenecut detection off and a GOP longer than the clip, so every seek target
    except 0 sits mid-GOP. A frame that reports a timestamp its color disagrees
    with means the extractor labelled an intent instead of a measurement.
    """
    inputs: list[str] = []
    for color in TIMED_COLORS:
        inputs += ["-f", "lavfi", "-t", str(seg), "-i", f"color=c={color}:s={size}:r={fps}"]
    streams = "".join(f"[{i}:v]" for i in range(len(TIMED_COLORS)))
    filt = f"{streams}concat=n={len(TIMED_COLORS)}:v=1:a=0[out]"
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        *inputs,
        "-filter_complex", filt, "-map", "[out]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-g", "1000", "-sc_threshold", "0",
        str(path),
    ])


@pytest.fixture(scope="session")
def timed_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("clips") / "timed.mp4"
    build_timed_clip(path)
    return path


@pytest.fixture(scope="session")
def cut_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("clips") / "cuts.mp4"
    build_cut_clip(path)
    return path


@pytest.fixture(scope="session")
def static_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("clips") / "static.mp4"
    build_static_clip(path)
    return path
