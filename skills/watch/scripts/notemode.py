#!/usr/bin/env python3
"""The durable-run half of `--make-note`.

`/watch` was built to answer a question and throw the evidence away: a temp
working directory, a "delete when done" line, near-duplicate frames collapsed
silently, and a report that is prose rather than an artifact. Every one of
those is correct for the four uses the skill was designed for and wrong for the
fifth, where the output is a note whose claims have to resolve against pixels
and seconds months later.

This module is the preset that inverts the trade for one mode. It writes
nothing but `run.json`, and `run.json` is the whole point: the seconds that
survived, the seconds the dedup pass collapsed, a sha256 per frame and per
source, and which backend produced the transcript. A note built on top of it
can be checked; a note built on a report that has been deleted cannot.

It deliberately holds no opinion about what a note SAYS. That contract lives in
`NOTE.md` beside the skill, because it is a prompt and not a file format.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

RUN_JSON = "run.json"
# Enough to identify a file, short enough to read in a diff. Collisions are not
# the threat here; a frame silently swapped for another is.
DIGEST_CHARS = 16


def sha256(path: Path | str | None) -> str | None:
    """A digest of the bytes, or None when there is nothing to digest."""
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:DIGEST_CHARS]


def video_id_of(source: str, info: dict) -> str:
    """The platform's id where there is one, else a digest of the source string.

    A local file has no id and still needs a stable directory name, and using
    the file's stem would collide the moment two videos are both called
    `recording.mp4`.
    """
    for key in ("id", "display_id"):
        value = (info or {}).get(key)
        if value:
            return str(value)
    return "local-" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:10]


def rehome(work: Path, target: Path, dl: dict) -> Path:
    """Move a run directory once the video's real id is known, fixing `dl` paths.

    A URL's platform id only arrives with the yt-dlp metadata call, which needs
    somewhere to write first. Naming the directory after a digest of the URL
    would work and would make every run directory unreadable to a human, so the
    run starts under a pending name and moves exactly once, before anything but
    the download exists. Returns the directory actually in use: on any failure
    the original is kept rather than the run being lost to a tidy-up.
    """
    if target == work:
        return work
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        work.rename(target)
    except OSError:
        return work
    for key in ("subtitle_path", "video_path", "audio_path"):
        value = dl.get(key)
        if isinstance(value, str) and value.startswith(str(work)):
            dl[key] = str(target) + value[len(str(work)):]
    # The staging parent has served its purpose. rmdir and not rmtree, because
    # it must refuse when another run is staging under it right now; a run in
    # flight is exactly what the tidy-up may not touch.
    try:
        work.parent.rmdir()
    except OSError:
        pass
    return target


def build_run(*, source: str, video_id: str, work: Path, info: dict,
              duration: float, resolution: int, detail: str,
              video_path: str | None, frames: list[dict],
              dropped_seconds: list[float], transcript_source: str | None,
              transcript_segments: list[dict],
              subtitle_path: str | None) -> dict:
    """The record a note is checked against, as plain data."""
    return {
        "schema": 1,
        "source": source,
        "video_id": video_id,
        "title": (info or {}).get("title"),
        "uploader": (info or {}).get("uploader"),
        "duration_seconds": round(float(duration or 0.0), 3),
        "detail": detail,
        "resolution": resolution,
        "work_dir": str(work),
        "video_sha256": sha256(video_path),
        "transcript": {
            "source": transcript_source,
            "segments": len(transcript_segments),
            "subtitle_path": subtitle_path,
            # The seconds a claim may be anchored to. A stamp that is not one
            # of these resolves to nothing, which is the single most common
            # defect a note gate catches.
            "segment_starts": [round(float(s["start"]), 2)
                               for s in transcript_segments],
        },
        "frames": [
            {
                "seconds": round(float(f["timestamp_seconds"]), 2),
                "path": str(f["path"]),
                "reason": f.get("reason", "selected"),
                "sha256": sha256(f.get("path")),
            }
            for f in frames
        ],
        # NOT A COUNT. These seconds existed and were collapsed into a
        # neighbour because they looked the same; a held slide is the usual
        # case. A note may legitimately say the slide was up across them, and
        # without this list it cannot know they were ever there.
        "deduped_seconds": [round(float(s), 2) for s in sorted(dropped_seconds)],
    }


def write_run(work: Path, run: dict) -> Path:
    path = work / RUN_JSON
    path.write_text(json.dumps(run, indent=2, sort_keys=False) + "\n",
                    encoding="utf-8")
    return path
