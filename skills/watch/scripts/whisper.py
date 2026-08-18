#!/usr/bin/env python3
"""Transcribe a video via Groq or OpenAI Whisper API.

Strategy: extract audio (mono 16kHz mp3, tiny payload), upload to whichever
API has a key. Returns segments in the same shape as transcribe.parse_vtt so
the rest of the pipeline (filter_range, format_transcript) doesn't care where
the transcript came from.

Pure stdlib — no `pip install groq` or `pip install openai` needed.
"""
from __future__ import annotations

import io
import json
import math
import mimetypes
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import uuid
from pathlib import Path
from urllib.request import Request, urlopen


GROQ_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"

OPENAI_ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_MODEL = "whisper-1"

# Both Groq's free tier and OpenAI whisper-1 cap uploads at 25 MB. We target a
# margin under that so multipart framing overhead never pushes a chunk over.
MAX_UPLOAD_BYTES = 24 * 1024 * 1024

# HOW MUCH AUDIO IS HANDED TO ONE LOCAL DECODE, in seconds, including the
# context on either side. This dial names the DECODED span rather than the kept
# span because the decoded span is what degenerates.
#
# A long decode collapses. On one two-hour recording a single whole-file pass
# collapsed at 21:49 and repeated one sentence 6,434 times to the end: 83% of
# the file lost, and the last line before the collapse was already mis-heard.
# Fifteen-minute windows over the same audio bounded the damage without removing
# it -- seven of nine clean, one looped, one lost thirteen minutes -- and an
# offset re-run over the lost stretch collapsed again at a different second.
# Three four-minute windows over that same stretch came back clean. So the
# failure is a property of the decode, not of the audio, and its probability
# scales with the window.
#
# Four minutes is not the longest span ever measured clean -- most of the
# fifteen-minute windows were fine. It is the longest span that has not yet been
# measured degenerating, which is a weaker and more honest claim, and the reason
# the number is a dial rather than a constant.
#
# Set WATCH_DECODE_WINDOW_SECONDS to 0 to decode in one pass again. That is the
# configuration the paragraph above describes losing 83% of a file.
DECODE_WINDOW_SECONDS = 240.0
# Context decoded on EACH side of a window and then discarded. A sentence
# straddling a boundary is decoded whole by the window that keeps it, at a cost
# of 2 x overlap of extra decoding per window. It is not free of edge cases:
# see `drop_seam_repeats` for the sentence two windows render on opposite sides
# of a boundary.
DECODE_OVERLAP_SECONDS = 30.0
# How far either side of a boundary two independent decodes of the same sentence
# may be assumed to land. Used only at the join; see `drop_seam_repeats`.
SEAM_SECONDS = 2.0


def plan_chunks(
    total_seconds: float,
    total_bytes: int,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> list[tuple[float, float]]:
    """Split a duration into contiguous (offset, duration) chunks under max_bytes.

    Size scales linearly with duration (constant-bitrate mono mp3), so an even
    time split yields evenly-sized chunks. Returns a single full-length chunk
    when the audio already fits.
    """
    if total_bytes <= max_bytes or total_seconds <= 0:
        return [(0.0, total_seconds)]

    n = math.ceil(total_bytes / max_bytes)
    chunk = total_seconds / n
    plan: list[tuple[float, float]] = []
    for i in range(n):
        offset = i * chunk
        # The last chunk absorbs any rounding remainder so durations sum exactly.
        duration = (total_seconds - offset) if i == n - 1 else chunk
        plan.append((round(offset, 3), round(duration, 3)))
    return plan


def plan_windows(
    total_seconds: float,
    window_seconds: float = DECODE_WINDOW_SECONDS,
    overlap_seconds: float = DECODE_OVERLAP_SECONDS,
) -> list[tuple[float, float, float, float]]:
    """Split a duration into overlapping decode windows.

    Returns (offset, duration, keep_from, keep_to) per window. The first two are
    the audio handed to the decoder; the last two are the span of SOURCE time
    whose segments are kept. The kept SPANS are contiguous and tile the file
    exactly once, so no stretch of audio is claimed by two windows.

    The overlap is symmetric: a window is decoded with `overlap_seconds` of
    context on each side of the span it keeps, EXCEPT where that would run off
    the start or end of the file, where it is clamped. So a sentence crossing a
    boundary is heard whole by the window that keeps it rather than cut in half
    by both.

    A tiling of spans is not by itself a guarantee about sentences; see
    `drop_seam_repeats` for what happens to a sentence rendered on both sides of
    a boundary by two independent decodes.

    `window_seconds <= 0` disables windowing and returns one whole-file window.
    A negative window or overlap, and an overlap of half the window or more,
    are misconfigurations rather than preferences, and raise: silently reverting
    to a whole-file decode is the failure this exists to remove.
    """
    if window_seconds < 0 or overlap_seconds < 0:
        raise ValueError(
            f"window {window_seconds}s and overlap {overlap_seconds}s must not "
            f"be negative")
    if window_seconds <= 0 or total_seconds <= window_seconds:
        return [(0.0, total_seconds, 0.0, total_seconds)]

    kept = window_seconds - 2 * overlap_seconds
    if kept <= 0:
        raise ValueError(
            f"overlap {overlap_seconds}s x2 leaves nothing of a {window_seconds}s "
            f"window to keep")

    out: list[tuple[float, float, float, float]] = []
    keep_from = 0.0
    while keep_from < total_seconds:
        keep_to = min(keep_from + kept, total_seconds)
        offset = max(0.0, keep_from - overlap_seconds)
        end = min(total_seconds, keep_to + overlap_seconds)
        out.append((round(offset, 3), round(end - offset, 3),
                    round(keep_from, 3), round(keep_to, 3)))
        keep_from = keep_to
    return out


def trim_to_keep(
    segments: list[dict],
    keep_from: float,
    keep_to: float,
    last: bool,
) -> list[dict]:
    """Drop the segments a window decoded only as context.

    Kept by segment START, so a SPAN of source time is claimed by exactly one
    window even when a segment runs past the boundary. The final window keeps
    everything after its start: a decoder may place a segment a little past the
    duration ffprobe reported, and the tail of a file is not a thing to discard
    on a rounding.

    A span tiling is not a sentence tiling. Each window is an INDEPENDENT decode,
    so one sentence gets its own timestamp from each of the two windows that
    heard it, and those two timestamps can straddle the boundary in opposite
    directions -- in which case a strict span test drops it from both. That is
    why the caller passes a lower bound slack of `SEAM_SECONDS` and then removes
    the duplicates by text; see `drop_seam_repeats`. A duplicated sentence is
    visible in the transcript. A dropped one is not.
    """
    return [s for s in segments
            if s["start"] >= keep_from and (last or s["start"] < keep_to)]


# Deliberately a second copy of what watch-quality's transcript_align does with
# the same words. This script ships inside the skill and runs on the standard
# library alone, so it cannot import the grading package -- a plugin that needed
# a pip install to transcribe a video would not be a plugin.
RE_SEAM_WORD = re.compile(r"[a-z0-9']+")


def _seam_key(text: str) -> str:
    return " ".join(RE_SEAM_WORD.findall(text.lower()))


def longest_identical_run(segments: list[dict]) -> int:
    """The longest run of consecutive segments saying the same thing.

    A decode that has stopped listening emits one line over and over. On a clean
    decode of a two-hour recording the longest such run was 3; on the pass that
    collapsed it was 6,434.
    """
    best = run = 0
    previous = None
    for seg in segments:
        key = _seam_key(seg["text"])
        run = run + 1 if key and key == previous else 1
        previous = key
        best = max(best, run)
    return best


# A run this long is a loop, not a speaker repeating themselves. Windowing turned
# a 6,434-run that ate 83% of a file into an 8-run inside one 4-minute window --
# bounded, but still there, and still unquotable.
LOOP_RUN = 4


def drop_seam_repeats(kept: list[dict], incoming: list[dict],
                      seam: float = SEAM_SECONDS) -> list[dict]:
    """Remove the sentences at a window's head that the previous window has.

    The window before this one kept everything up to the boundary; this one is
    allowed to reach back `seam` seconds past it so a sentence cannot fall
    between the two. Anything it reaches back and finds already there is
    dropped, matched on the words rather than the clock, because the two
    renderings of one sentence rarely carry the same timestamp -- that mismatch
    is the whole reason the slack exists.

    Only the tail of `kept` is compared: a sentence legitimately repeated an
    hour earlier is not a seam artefact, and matching against the whole
    transcript would silently delete real repetition.
    """
    if not kept or not incoming:
        return incoming
    edge = max(s["end"] for s in kept)
    recent = {_seam_key(s["text"]) for s in kept if s["end"] >= edge - 4 * seam}
    return [s for s in incoming
            if not (s["start"] <= edge + seam and _seam_key(s["text"]) in recent)]


def _read_config_value(name: str) -> str | None:
    """Read a config value from the environment, then the watch dotenv files."""
    def _from_env(env_name: str) -> str | None:
        value = os.environ.get(env_name)
        return value.strip() if value else None

    def _from_dotenv(path: Path, env_name: str) -> str | None:
        if not path.exists():
            return None
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() != env_name:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                    value = value[1:-1]
                return value or None
        except OSError:
            return None
        return None

    dotenv_paths = [
        Path.home() / ".config" / "watch" / ".env",
        Path.cwd() / ".env",
    ]

    value = _from_env(name)
    if value:
        return value
    for candidate in dotenv_paths:
        value = _from_dotenv(candidate, name)
        if value:
            return value
    return None


def load_api_key(preferred: str | None = None) -> tuple[str, str] | tuple[None, None]:
    """Return (backend, api_key). Prefers Groq, then OpenAI, then local whisper.cpp.

    If `preferred` is "groq", "openai", or "local", only that backend is
    considered. For the "local" backend the second tuple element is the path to
    the whisper.cpp binary (WHISPER_CPP_BIN) rather than an API key.
    """
    candidates = (
        ("GROQ_API_KEY", "groq"),
        ("OPENAI_API_KEY", "openai"),
        ("WHISPER_CPP_BIN", "local"),
    )
    if preferred is not None:
        candidates = tuple(c for c in candidates if c[1] == preferred)

    for key_name, backend in candidates:
        value = _read_config_value(key_name)
        if value:
            return backend, value

    return None, None


def extract_audio(video_path: str, out_path: Path) -> Path:
    """Extract mono 16kHz 64kbps mp3 — ~480 kB/min, fits any Whisper limit."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(Path(video_path).resolve()),
        "-vn",
        "-acodec", "libmp3lame",
        "-ar", "16000",
        "-ac", "1",
        "-b:a", "64k",
        str(out_path.resolve()),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg audio extraction failed: {result.stderr.strip()}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise SystemExit("ffmpeg produced no audio — video may have no audio track")
    return out_path


def audio_duration(audio_path: Path) -> float:
    """Return the duration of an audio file in seconds via ffprobe."""
    if shutil.which("ffprobe") is None:
        raise SystemExit("ffprobe is not installed. Install with: brew install ffmpeg")

    result = subprocess.run(
        [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            str(audio_path.resolve()),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffprobe failed: {result.stderr.strip()}")
    fmt = json.loads(result.stdout or "{}").get("format", {})
    return float(fmt.get("duration") or 0.0)


def split_audio(
    full_audio: Path,
    work_dir: Path,
    plan: list[tuple[float, float]],
) -> list[tuple[Path, float]]:
    """Slice full_audio into per-plan chunk files, returning (path, offset) pairs.

    Uses stream copy (`-c copy`) so there is no re-encode and no quality loss;
    mp3 frame boundaries are close enough for transcription's purposes.
    """
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    work_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[tuple[Path, float]] = []
    for index, (offset, duration) in enumerate(plan):
        out_path = work_dir / f"chunk_{index:03d}.mp3"
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-ss", f"{offset:.3f}",
            "-i", str(full_audio.resolve()),
            "-t", f"{duration:.3f}",
            "-c", "copy",
            str(out_path.resolve()),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            raise SystemExit(
                f"ffmpeg failed to split audio chunk {index + 1}: {result.stderr.strip()}"
            )
        chunks.append((out_path, offset))
    return chunks


def _build_multipart(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    """Assemble a multipart/form-data body the Whisper APIs accept.

    Whisper's multipart upload is small and predictable — doing it by hand
    keeps us on pure stdlib instead of pulling requests/groq/openai SDKs.
    """
    boundary = f"----WatchBoundary{uuid.uuid4().hex}"
    eol = b"\r\n"
    buf = io.BytesIO()

    for name, value in fields.items():
        buf.write(f"--{boundary}".encode()); buf.write(eol)
        buf.write(f'Content-Disposition: form-data; name="{name}"'.encode()); buf.write(eol)
        buf.write(eol)
        buf.write(str(value).encode()); buf.write(eol)

    mimetype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    buf.write(f"--{boundary}".encode()); buf.write(eol)
    buf.write(
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"'.encode()
    )
    buf.write(eol)
    buf.write(f"Content-Type: {mimetype}".encode()); buf.write(eol)
    buf.write(eol)
    buf.write(file_path.read_bytes())
    buf.write(eol)
    buf.write(f"--{boundary}--".encode()); buf.write(eol)

    return buf.getvalue(), boundary


MAX_ATTEMPTS = 4       # initial + 3 retries
MAX_429_RETRIES = 2
RETRY_BASE_DELAY = 2.0


def _post_whisper(endpoint: str, api_key: str, model: str, audio_path: Path) -> dict:
    fields = {
        "model": model,
        "response_format": "verbose_json",
        "temperature": "0",
    }
    body, boundary = _build_multipart(fields, audio_path)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        # Groq sits behind Cloudflare — the default `Python-urllib/3.x` UA
        # trips WAF rule 1010 (403) before auth even runs. Any non-default
        # UA clears it; we identify honestly.
        "User-Agent": "watch-skill/1.0 (+claude-code; python-urllib)",
    }

    context = ssl.create_default_context()
    rate_limit_hits = 0
    last_exc: Exception | None = None
    last_detail = ""

    for attempt in range(MAX_ATTEMPTS):
        request = Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=300, context=context) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = _read_error_body(exc)
            last_exc, last_detail = exc, detail

            # 4xx other than 429 are client errors — no retry will fix them.
            if 400 <= exc.code < 500 and exc.code != 429:
                raise SystemExit(f"Whisper request failed: {exc}{detail}")

            if exc.code == 429:
                rate_limit_hits += 1
                if rate_limit_hits >= MAX_429_RETRIES:
                    raise SystemExit(f"Whisper request failed: {exc}{detail}")
                delay = _retry_after(exc) or RETRY_BASE_DELAY * (2 ** attempt) + 1
            else:
                delay = RETRY_BASE_DELAY * (2 ** attempt)

            if attempt < MAX_ATTEMPTS - 1:
                print(
                    f"[watch] whisper HTTP {exc.code} — retrying in {delay:.1f}s "
                    f"(attempt {attempt + 2}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            continue
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError) as exc:
            last_exc, last_detail = exc, ""
            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY * (attempt + 1)
                print(
                    f"[watch] whisper network error ({type(exc).__name__}: {exc}) — "
                    f"retrying in {delay:.1f}s (attempt {attempt + 2}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            continue

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Whisper returned non-JSON response: {exc}: {payload[:200]}")

    raise SystemExit(
        f"Whisper request failed after {MAX_ATTEMPTS} attempts: {last_exc}{last_detail}"
    )


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read()
    except Exception:
        return ""
    if not body:
        return ""
    try:
        return f" — {body.decode('utf-8', errors='replace')[:400]}"
    except Exception:
        return ""


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    header = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else None
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def shift_segments(segments: list[dict], offset_seconds: float) -> list[dict]:
    """Return a copy of segments with start/end shifted by offset_seconds.

    Each chunk is transcribed in isolation, so Whisper returns 0-based timestamps
    per chunk; shifting by the chunk's offset stitches them into source time.
    """
    if offset_seconds == 0:
        return segments
    return [
        {
            "start": round(seg["start"] + offset_seconds, 2),
            "end": round(seg["end"] + offset_seconds, 2),
            "text": seg["text"],
        }
        for seg in segments
    ]


def _segments_from_response(data: dict) -> list[dict]:
    """Convert Whisper verbose_json into our {start, end, text} segment format."""
    out: list[dict] = []
    for seg in data.get("segments") or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "start": round(float(seg.get("start") or 0.0), 2),
            "end": round(float(seg.get("end") or 0.0), 2),
            "text": text,
        })

    if not out:
        full = (data.get("text") or "").strip()
        if full:
            out.append({"start": 0.0, "end": 0.0, "text": full})

    return out


def transcribe_chunks(
    chunks: list[tuple[Path, float]],
    transcribe_one,
    keeps: list[tuple[float, float]] | None = None,
    retry_window=None,
) -> list[dict]:
    """Transcribe each chunk, shift its segments by the chunk offset, concatenate.

    A chunk that fails after its own retries is logged and skipped so one bad
    slice doesn't discard the whole transcript. Raises only if every chunk fails.

    `keeps` is the per-chunk (keep_from, keep_to) from `plan_windows`. With it,
    each chunk contributes only the segments inside its own span and the
    overlaps are dropped; without it every segment is kept, which is right for
    the byte-split path where chunks do not overlap.

    `retry_window(index)` re-decodes one window from a different offset, and is
    called only when a window comes back looping. The degeneration is a property
    of the decode rather than of the audio -- the same stretch that killed a
    decode twice came back clean from a third start -- so a second attempt from
    a different second is the cheapest repair there is. It is used only if it
    loops less than the first attempt did; a retry is allowed to fail.
    """
    segments: list[dict] = []
    failures = 0
    for index, (path, offset) in enumerate(chunks):
        try:
            chunk_segments = transcribe_one(path)
        except SystemExit as exc:
            failures += 1
            print(
                f"[watch] chunk {index + 1}/{len(chunks)} failed — skipping ({exc})",
                file=sys.stderr,
            )
            continue

        run = longest_identical_run(chunk_segments)
        if retry_window and run >= LOOP_RUN:
            print(f"[watch] chunk {index + 1}/{len(chunks)} looped {run}x — "
                  f"re-decoding it from a different offset", file=sys.stderr)
            try:
                alternative = retry_window(index)
            except SystemExit as exc:
                print(f"[watch] the retry failed too ({exc})", file=sys.stderr)
                alternative = None
            if alternative is not None:
                alternative_run = longest_identical_run(alternative)
                print(f"[watch] retry looped {alternative_run}x vs {run}x — "
                      f"{'keeping the retry' if alternative_run < run else 'keeping the first'}",
                      file=sys.stderr)
                if alternative_run < run:
                    chunk_segments = alternative

        shifted = shift_segments(chunk_segments, offset)
        if keeps:
            keep_from, keep_to = keeps[index]
            # Every window after the first reaches SEAM_SECONDS back past its own
            # boundary, and drop_seam_repeats then removes whatever the previous
            # window already kept. Reaching back risks a duplicate; not reaching
            # back risks a silent loss, and only one of those is visible.
            lower = keep_from - SEAM_SECONDS if index else keep_from
            shifted = trim_to_keep(shifted, lower, keep_to,
                                   index == len(chunks) - 1)
            shifted = drop_seam_repeats(segments, shifted)
        segments.extend(shifted)
        # Both numbers: decoded, then kept. A window that decoded 90 and kept 60
        # is working as intended; one that decoded 90 and kept 0 is a boundary
        # bug, and printing only the decoded count would hide it.
        print(
            f"[watch] chunk {index + 1}/{len(chunks)} → {len(chunk_segments)} "
            f"segments, {len(shifted)} kept",
            file=sys.stderr,
        )

    if failures == len(chunks):
        raise SystemExit("Whisper failed on every audio chunk")
    return segments


def _run_whisper_cpp(bin_path: str, audio_path: Path,
                     model_override: str | None = None) -> list[dict]:
    """Transcribe one audio file with a local whisper.cpp binary.

    Requires WHISPER_CPP_BIN (the whisper-cli executable) and WHISPER_CPP_MODEL
    (a ggml model file) in the environment or ~/.config/watch/.env. Nothing
    leaves the machine.

    `model_override` is the second decode's model; see `second_model_path`.
    """
    # WHISPER_CPP_BIN may be a path or a bare name already on PATH.
    binary = Path(bin_path).expanduser()
    if not binary.exists():
        resolved = shutil.which(bin_path)
        if not resolved:
            raise SystemExit(f"WHISPER_CPP_BIN is not an executable path or on PATH: {bin_path}")
        binary = Path(resolved)

    model = model_override or _read_config_value("WHISPER_CPP_MODEL")
    if not model:
        raise SystemExit(
            "WHISPER_CPP_MODEL is not set. Point it at a ggml model file "
            "(e.g. ggml-small.en.bin) in ~/.config/watch/.env."
        )
    model_path = Path(model).expanduser()
    if not model_path.exists():
        raise SystemExit(f"WHISPER_CPP_MODEL does not exist: {model_path}")

    # whisper.cpp wants 16kHz WAV input; the pipeline's audio is mp3.
    wav_path = audio_path.with_suffix(".whisper.wav")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(audio_path.resolve()),
        "-ar", "16000", "-ac", "1",
        str(wav_path.resolve()),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not wav_path.exists():
        raise SystemExit(f"ffmpeg wav conversion failed: {result.stderr.strip()}")

    # "auto" lets whisper.cpp detect the spoken language; its own default is
    # English, which silently mangles non-English audio.
    language = _read_config_value("WHISPER_CPP_LANG") or "auto"
    threads = _read_config_value("WHISPER_CPP_THREADS") or str(os.cpu_count() or 4)

    # A distinct prefix per model, so a failed second decode can never be read
    # from the first decode's leftover JSON and reported as the second witness.
    out_prefix = audio_path.with_suffix(".whisper2" if model_override else ".whisper")
    cmd = [
        str(binary.resolve()),
        "-m", str(model_path.resolve()),
        "-f", str(wav_path.resolve()),
        "-l", language,
        "-oj",
        "-of", str(out_prefix.resolve()),
        "-t", threads,
        "-pp",
    ]
    # Local runs take minutes on long videos, so stream progress instead of
    # going silent; keep the tail of stderr for the failure message.
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    tail: list[str] = []
    last_pct = -10
    for line in proc.stderr or []:
        line = line.strip()
        if not line:
            continue
        tail.append(line)
        del tail[:-20]
        if "progress =" in line:
            try:
                pct = int(line.split("progress =")[-1].strip().rstrip("%"))
            except ValueError:
                continue
            if pct >= last_pct + 10:
                last_pct = pct
                print(f"[watch] whisper.cpp {pct}%…", file=sys.stderr)
    returncode = proc.wait()

    json_path = Path(str(out_prefix) + ".json")
    if returncode != 0 or not json_path.exists():
        detail = "\n".join(tail[-6:])
        raise SystemExit(f"whisper.cpp failed (exit {returncode}): {detail[-400:]}")

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"whisper.cpp produced unreadable JSON: {exc}")

    segments: list[dict] = []
    for entry in data.get("transcription") or []:
        text = (entry.get("text") or "").strip()
        if not text:
            continue
        offsets = entry.get("offsets") or {}
        segments.append({
            "start": round(float(offsets.get("from") or 0) / 1000.0, 2),
            "end": round(float(offsets.get("to") or 0) / 1000.0, 2),
            "text": text,
        })
    return segments


def _transcribe_file(backend: str, api_key: str, audio_path: Path,
                     model_override: str | None = None) -> list[dict]:
    """Transcribe one audio file and return its 0-based segments.

    For cloud backends `api_key` is the API key; for "local" it is the
    whisper.cpp binary path.
    """
    if backend == "groq":
        response = _post_whisper(GROQ_ENDPOINT, api_key, GROQ_MODEL, audio_path)
    elif backend == "openai":
        response = _post_whisper(OPENAI_ENDPOINT, api_key, OPENAI_MODEL, audio_path)
    elif backend == "local":
        return _run_whisper_cpp(api_key, audio_path, model_override)
    else:
        raise SystemExit(f"Unknown whisper backend: {backend}")
    return _segments_from_response(response)


def _config_float(name: str, default: float) -> float:
    """A numeric dial from the environment or the dotenv, or its default.

    A value that is not a number is a typo in a config file, and a typo that
    silently reverts to the default would put a whole-file decode back without
    saying so. It stops the run instead.
    """
    raw = _read_config_value(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise SystemExit(f"{name} is not a number: {raw!r}")


def second_model_path() -> str | None:
    """The model for the second decode, from WHISPER_CPP_MODEL_2, or None.

    TWO MODELS ARE NOT TWO WITNESS CLASSES. Both are speech-to-text; they have
    agreed with each other and been wrong together. What a second decode buys is
    a way to SEE an unstable passage: on one file the two models disagreed on the
    episode's most-quoted sentence, one hearing the negation of the other, and a
    single-model note would have published the inverse of the claim.

    So the second rendering is written to disk beside the first and neither is
    merged into the other. Picking the better one is a per-file judgement made
    from the alignment, and inheriting last file's choice is what this exists to
    prevent -- the model trusted on one recording was the one that collapsed on
    the next. That is one reversal, not a rate: enough to stop inheriting a
    choice, and not enough to predict which model fails next.
    """
    return _read_config_value("WHISPER_CPP_MODEL_2")


def write_rendering(path: Path, backend: str, model: str | None,
                    segments: list[dict]) -> Path:
    """Write one decode where `wq-transcript-align` can read it."""
    path.write_text(json.dumps(
        {"backend": backend, "model": model, "segments": segments}, indent=2),
        encoding="utf-8")
    return path


def transcribe_video(
    video_path: str,
    audio_out: Path,
    backend: str | None = None,
    api_key: str | None = None,
) -> tuple[list[dict], str]:
    """Run the full flow: extract audio → upload → parse segments.

    Returns (segments, backend_used). Raises SystemExit on any failure.
    """
    if backend is None or api_key is None:
        detected_backend, detected_key = load_api_key()
        backend = backend or detected_backend
        api_key = api_key or detected_key

    if not backend or not api_key:
        setup_py = Path(__file__).resolve().parent / "setup.py"
        raise SystemExit(
            "No Whisper backend available. Set GROQ_API_KEY (preferred) or OPENAI_API_KEY, "
            "or WHISPER_CPP_BIN + WHISPER_CPP_MODEL for offline whisper.cpp, "
            "in the environment or in ~/.config/watch/.env. "
            f"Run `python3 {setup_py}` to configure."
        )

    print(f"[watch] extracting audio for Whisper ({backend})…", file=sys.stderr)
    audio_path = extract_audio(video_path, audio_out)
    audio_bytes = audio_path.stat().st_size

    def decode(model_override: str | None, work_name: str) -> list[dict]:
        def transcribe_one(path: Path) -> list[dict]:
            return _transcribe_file(backend, api_key, path, model_override)

        # WINDOWED, for the local backend only. The cloud APIs window internally
        # and have not been measured degenerating this way; that is a gap in the
        # evidence, not a clean bill of health, so their path is left as it was
        # rather than changed on a guess. Windowing them would also multiply
        # requests, rate-limit exposure and cost per video.
        window_seconds = _config_float("WATCH_DECODE_WINDOW_SECONDS",
                                       DECODE_WINDOW_SECONDS)
        overlap_seconds = _config_float("WATCH_DECODE_OVERLAP_SECONDS",
                                        DECODE_OVERLAP_SECONDS)
        if backend == "local" and window_seconds > 0:
            duration = audio_duration(audio_path)
            try:
                windows = plan_windows(duration, window_seconds, overlap_seconds)
            except ValueError as exc:
                raise SystemExit(f"decode window misconfigured: {exc}")
            if len(windows) == 1:
                print(f"[watch] audio: {duration:.0f}s — one decode window",
                      file=sys.stderr)
                return transcribe_one(audio_path)
            print(
                f"[watch] audio: {duration:.0f}s — {len(windows)} decode windows "
                f"of {window_seconds:.0f}s (+{overlap_seconds:.0f}s context each "
                f"side); a longer decode is where transcripts collapse",
                file=sys.stderr,
            )
            chunks = split_audio(audio_path, audio_out.parent / work_name,
                                 [(offset, length) for offset, length, _, _ in windows])

            def retry_window(index: int) -> list[dict] | None:
                """The same window, decoded from a different second.

                Reaching further back is preferred; at the very start of the
                file there is nothing to reach back into, so it reaches forward
                instead. Either way the window still covers everything it keeps.
                """
                offset, length, _keep_from, _keep_to = windows[index]
                start = max(0.0, offset - overlap_seconds)
                end = min(duration, offset + length + overlap_seconds)
                if start == offset and end == offset + length:
                    return None
                again = split_audio(audio_path,
                                    audio_out.parent / f"{work_name}-retry-{index}",
                                    [(start, end - start)])
                # Returned in the ORIGINAL window's frame, because the caller
                # shifts by that window's offset and knows nothing of this one.
                return shift_segments(transcribe_one(again[0][0]), start - offset)

            return transcribe_chunks(
                chunks, transcribe_one,
                keeps=[(keep_from, keep_to) for _, _, keep_from, keep_to in windows],
                retry_window=retry_window)

        if audio_bytes <= MAX_UPLOAD_BYTES:
            verb = "transcribing locally with" if backend == "local" else "uploading to"
            print(
                f"[watch] audio: {audio_bytes / 1024:.0f} kB — {verb} {backend} Whisper…",
                file=sys.stderr,
            )
            return transcribe_one(audio_path)

        duration = audio_duration(audio_path)
        plan = plan_chunks(duration, audio_bytes, MAX_UPLOAD_BYTES)
        print(
            f"[watch] audio: {audio_bytes / (1024 * 1024):.0f} MB exceeds "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB — splitting into {len(plan)} chunks…",
            file=sys.stderr,
        )
        chunks = split_audio(audio_path, audio_out.parent / work_name, plan)
        return transcribe_chunks(chunks, transcribe_one)

    segments = decode(None, "chunks")

    if not segments:
        raise SystemExit("Whisper returned no transcript segments")

    print(f"[watch] transcribed {len(segments)} segments via {backend}", file=sys.stderr)

    second = second_model_path() if backend == "local" else None
    if second:
        first_path = write_rendering(
            audio_out.parent / "transcript-1.json", backend,
            _read_config_value("WHISPER_CPP_MODEL"), segments)
        # A second decode that dies must not take the first one's transcript
        # with it. A run with one rendering is worse than a run with two and
        # says so; a run with none is a wasted download.
        try:
            other = decode(second, "chunks-2")
        except SystemExit as exc:
            print(f"[watch] second decode failed, continuing with one: {exc}",
                  file=sys.stderr)
        else:
            second_path = write_rendering(
                audio_out.parent / "transcript-2.json", backend, second, other)
            print(
                f"[watch] second decode: {len(other)} segments — NOTHING HERE IS "
                f"QUOTABLE until the two agree:\n"
                f"[watch]   wq-transcript-align {first_path} {second_path}",
                file=sys.stderr,
            )

    return segments, backend


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: whisper.py <video-path> [<audio-out.mp3>] [--backend groq|openai]", file=sys.stderr)
        raise SystemExit(2)

    video = sys.argv[1]
    audio_out = Path(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else Path("audio.mp3")
    backend_override = None
    if "--backend" in sys.argv:
        backend_override = sys.argv[sys.argv.index("--backend") + 1]

    segments, backend = transcribe_video(video, audio_out, backend=backend_override)
    print(json.dumps({"backend": backend, "segments": segments}, indent=2))
