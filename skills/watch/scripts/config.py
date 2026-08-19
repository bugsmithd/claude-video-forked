#!/usr/bin/env python3
"""Shared /watch configuration helpers."""
from __future__ import annotations

import os
from pathlib import Path


CONFIG_DIR = Path.home() / ".config" / "watch"
CONFIG_FILE = CONFIG_DIR / ".env"

DEFAULT_DETAIL = "balanced"

DETAILS = {"transcript", "efficient", "balanced", "token-burner"}

# WHERE A DURABLE RUN LANDS. The default path is deliberately generic and
# configurable: a run kept for a note belongs wherever that person keeps them,
# and hardcoding one installation's directory into a shared script is how a
# private path ends up in a public repository.
DEFAULT_NOTE_DIR = Path.home() / "watch-runs"
# The resolution floor for a durable run, measured rather than chosen. Twelve
# frames of a UI tutorial OCR'd at four widths gave 17 readable words at 512px,
# 402 at 768, 637 at 1024 and 891 at 1536. A note anchors claims to pixels, so
# it buys the text the ordinary token budget declines to pay for.
NOTE_RESOLUTION = 1024
DEFAULT_RESOLUTION = 768


def read_env_file(path: Path | None = None) -> dict[str, str]:
    if path is None:
        path = CONFIG_FILE
    values: dict[str, str] = {}
    if not path.exists():
        return values
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, _, value = raw.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
            value = value[1:-1]
        else:
            # Strip an inline comment (a '#' preceded by whitespace) from an
            # unquoted value. Without this, `WATCH_DETAIL=balanced  # note`
            # parses as "balanced  # note", fails validation, and silently
            # falls back to the default. Keeps '#' inside quotes / API keys.
            for i, ch in enumerate(value):
                if ch == "#" and i > 0 and value[i - 1] in " \t":
                    value = value[:i].rstrip()
                    break
        values[key.strip()] = value
    return values


def get_config() -> dict[str, object]:
    file_values = read_env_file()

    detail = (
        os.environ.get("WATCH_DETAIL")
        or file_values.get("WATCH_DETAIL")
        or DEFAULT_DETAIL
    )
    if detail not in DETAILS:
        detail = DEFAULT_DETAIL

    return {
        "detail": detail,
        "config_file": str(CONFIG_FILE),
    }


def note_dir() -> Path:
    """The root a durable run is kept under, from `WATCH_NOTE_DIR` or the default."""
    raw = (os.environ.get("WATCH_NOTE_DIR")
           or read_env_file().get("WATCH_NOTE_DIR") or "")
    return Path(raw).expanduser() if raw.strip() else DEFAULT_NOTE_DIR


def note_run_dir(root: Path, video_id: str) -> Path:
    """`<root>/<video_id>/run-NN`, the next free NN.

    Numbered and not timestamped so a second run of the same video is
    obviously a second run, and so the same inputs give the same name in a
    test. Nothing here ever overwrites an earlier run: the evidence a note
    resolves against is the one thing this mode exists to stop destroying.
    """
    base = root.expanduser() / (video_id or "unknown")
    for n in range(1, 1000):
        candidate = base / f"run-{n:02d}"
        if not candidate.exists():
            return candidate
    raise SystemExit(f"{base} already holds 999 runs; move some aside")


def frame_cap(detail: str) -> int | None:
    if detail == "efficient":
        return 50
    if detail == "balanced":
        return 100
    if detail == "token-burner":
        return None
    if detail == "transcript":
        return None
    return 100
