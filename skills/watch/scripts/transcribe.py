#!/usr/bin/env python3
"""Parse a WebVTT subtitle file into a clean, timestamped transcript.

YouTube auto-subs emit rolling-duplicate cues (each line appears 2-3 times as it
scrolls). We dedupe consecutive identical cues and merge their time ranges.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path


TS_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _clean(text: str) -> str:
    """Strip cue tags, decode HTML entities, and normalize whitespace.

    Many YouTube caption tracks pad wrapped lines with `&nbsp;`, which otherwise
    survives into the transcript as literal `&nbsp;` (or a U+00A0 once decoded)
    in the middle of sentences. Decode entities first, then fold every kind of
    space — including U+00A0 — into a single ASCII space.
    """
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    text = text.replace(" ", " ").replace("​", "")
    return WS_RE.sub(" ", text).strip()


def parse_vtt(path: str) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()

    segments: list[dict] = []
    prev_lines: list[str] = []
    i = 0
    while i < len(lines):
        match = TS_RE.match(lines[i])
        if not match:
            i += 1
            continue

        start = _to_seconds(*match.groups()[:4])
        end = _to_seconds(*match.groups()[4:])
        i += 1

        cue_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            cleaned = _clean(lines[i])
            if cleaned:
                cue_lines.append(cleaned)
            i += 1

        # YouTube auto-subs scroll: each cue is [tail of previous cue] + [new
        # line]. Drop the leading lines that merely repeat the previous cue's
        # tail so every spoken line lands in the transcript exactly once.
        stripped = _strip_rollover(prev_lines, cue_lines)
        if stripped:
            prev_lines = stripped

        # A cue that was ENTIRELY a repeat still means the line was on screen
        # until this cue ends -- YouTube's 10ms hold cues are exactly that. Carry
        # the end time onto the previous segment instead of dropping it, or the
        # transcript understates how long every line lasted, which is the number
        # any witness window is measured against.
        if not stripped and cue_lines and segments:
            segments[-1]["end"] = max(segments[-1]["end"], round(end, 2))
            i += 1
            continue

        # _clean again on the joined text: joining can reintroduce the runs of
        # whitespace each line had trimmed away.
        cue_text = _clean(" ".join(stripped))
        if cue_text:
            segments.append({"start": round(start, 2), "end": round(end, 2), "text": cue_text})
        i += 1

    return _dedupe(segments)


def _strip_rollover(prev_lines: list[str], cue_lines: list[str]) -> list[str]:
    """Drop the longest prefix of `cue_lines` that equals a suffix of `prev_lines`."""
    for n in range(min(len(prev_lines), len(cue_lines)), 0, -1):
        if prev_lines[-n:] == cue_lines[:n]:
            return cue_lines[n:]
    return cue_lines


def _dedupe(segments: list[dict]) -> list[dict]:
    """Collapse rolling duplicates common in YouTube auto-subs."""
    out: list[dict] = []
    for seg in segments:
        if out and seg["text"] == out[-1]["text"]:
            out[-1]["end"] = seg["end"]
            continue
        if out and seg["text"].startswith(out[-1]["text"] + " "):
            out[-1]["text"] = seg["text"]
            out[-1]["end"] = seg["end"]
            continue
        out.append(seg)
    return out


def filter_range(
    segments: list[dict],
    start_seconds: float | None,
    end_seconds: float | None,
) -> list[dict]:
    """Return segments whose time range overlaps [start, end]."""
    if start_seconds is None and end_seconds is None:
        return segments
    lo = start_seconds if start_seconds is not None else float("-inf")
    hi = end_seconds if end_seconds is not None else float("inf")
    return [seg for seg in segments if seg["end"] >= lo and seg["start"] <= hi]


# WHERE THE INFORMATION IS, ACCORDING TO THE PERSON WHO PUT IT THERE.
# Scene-change and keyframe selection both sample by how much the picture
# CHANGED, and pointing at a slide changes almost nothing: a review lane found a
# screen recording under-read, with 169 of 182 extracted frames cited nowhere
# while the moments the presenter flagged had no frame at all.
#
# The speaker says when to look. These are the phrases that say it, kept to ones
# that are about the screen rather than about the argument -- "look at this"
# earns a frame, "look, the point is" does not, so the pattern requires the
# pointing word to be followed by something being shown.
#
# THE FIRST VERSION OF THIS PATTERN FOUND NOTHING ON A REAL TUTORIAL. It
# demanded "look at this/that/the", and a person actually says "let's take a
# look at my inbox", "let's look at another way", "on this message, I see…".
# Tuned against a 9-minute UI walkthrough it now finds six moments in 149
# segments, which is the right order: enough to pin, few enough to afford.
_DET = (r"(?:this|that|these|those|the|my|our|your|his|her|its|their|another"
        r"|a|an|it|here)")
DEICTIC = re.compile(
    r"\b(?:"
    rf"(?:let'?s |now |so |if you )?(?:take a |have a )?look at {_DET}\b"
    r"|look (?:here|at the screen)\b"
    r"|as you can see|you(?:'ll| will| can)? see (?:here|this|that|the)\b"
    r"|(?:we|i) (?:can )?see (?:here|this|that|the)\b"
    rf"|notice {_DET}\b|notice how\b"
    r"|watch (?:what happens|this|closely)\b"
    r"|(?:right |over )?here (?:you|we|is|are|i)\b"
    r"|(?:shown|show(?:n|ing)?) (?:here|on (?:the )?(?:screen|slide))\b"
    r"|(?:this|the) (?:slide|chart|graph|diagram|screenshot|table|screen)\b"
    r"|on (?:this|the) (?:screen|slide|message|page|tab)\b"
    r"|(?:if you )?zoom in\b"
    r")",
    re.IGNORECASE,
)
# Two cues a few seconds apart are one moment described twice, and each one
# costs a frame out of the same budget the detail engine is spending.
CUE_MIN_GAP = 20.0
# A hard ceiling so a presenter who says "look at this" every thirty seconds
# cannot spend the whole frame budget on cues; what is dropped is reported.
CUE_LIMIT = 12


def deictic_cues(segments: list[dict], min_gap: float = CUE_MIN_GAP,
                 limit: int = CUE_LIMIT) -> list[float]:
    """Seconds where the speaker points at the screen, earliest first.

    Returns starts, not midpoints: the frame wanted is the one being pointed at,
    and it is on screen before the sentence describing it finishes.
    """
    out: list[float] = []
    for seg in segments:
        if not DEICTIC.search(seg.get("text") or ""):
            continue
        start = float(seg["start"])
        if out and start - out[-1] < min_gap:
            continue
        out.append(start)
        if len(out) >= limit:
            break
    return out


def _format_stamp(seconds: float) -> str:
    """Render a transcript timestamp as [MM:SS], rolling over to [H:MM:SS] once
    past an hour. Mirrors frames.format_time so transcript stamps stay aligned
    with the frame ``t=`` markers instead of overflowing the minutes field
    (e.g. [1:01:01] rather than [61:01]) on videos longer than an hour."""
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"[{hours}:{minutes:02d}:{secs:02d}]"
    return f"[{minutes:02d}:{secs:02d}]"


def format_transcript(segments: list[dict]) -> str:
    lines = []
    for seg in segments:
        lines.append(f"{_format_stamp(seg['start'])} {seg['text']}")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: transcribe.py <vtt-path>", file=sys.stderr)
        raise SystemExit(2)
    print(format_transcript(parse_vtt(sys.argv[1])))
