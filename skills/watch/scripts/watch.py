#!/usr/bin/env python3
"""/watch entry point: download video, extract frames, parse transcript.

Prints a markdown report to stdout listing frame paths + transcript. Claude
then Reads each frame path to see the video.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from config import DEFAULT_RESOLUTION, NOTE_RESOLUTION, frame_cap, get_config, note_dir, note_run_dir  # noqa: E402
from download import download, fetch_captions, is_url  # noqa: E402
from notemode import build_run, rehome, video_id_of, write_run  # noqa: E402
from frames import MAX_FPS, auto_fps, auto_fps_focus, burn_stamps, extract_at_timestamps, extract_keyframes, extract_scene_or_uniform, format_time, get_metadata, merge_frames, parse_time, parse_timestamps, stamp_paths  # noqa: E402
from transcribe import deictic_cues, filter_range, format_transcript, parse_vtt  # noqa: E402
from whisper import load_api_key, transcribe_video  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="watch",
        description="Download a video, extract auto-scaled frames, and surface the transcript.",
    )
    ap.add_argument("source", help="Video URL or local file path")
    ap.add_argument("--max-frames", type=int, default=None, help="Override frame cap")
    # 512 WAS BELOW THE FLOOR FOR READING A SCREEN, and it is now measured.
    # Twelve frames of a 10-minute UI tutorial, same source, four widths, OCR'd
    # with tesseract --psm 6: 512px yields 17 readable words, 768px yields 402,
    # 1024px yields 637, 1536px yields 891. The step from 512 to 768 is 24x the
    # text for 2x the bytes; every step after it costs more and returns less.
    # A review lane had already reported screen recordings under-read and
    # 169 of 182 frames cited nowhere; this is one reason why.
    # Left as None so `--make-note` can raise the floor without overriding a
    # width the user asked for. A default resolved at parse time cannot tell
    # "the user typed 768" from "nobody said".
    ap.add_argument("--resolution", type=int, default=None,
                    help=f"Frame width in pixels (default {DEFAULT_RESOLUTION}, "
                         f"or {NOTE_RESOLUTION} under --make-note; 512 loses "
                         f"nearly all on-screen text, 1024+ for dense UI or code)")
    ap.add_argument("--fps", type=float, default=None, help="Override auto-fps")
    ap.add_argument(
        "--detail",
        choices=["transcript", "efficient", "balanced", "token-burner"],
        default=None,
        help="Fidelity/speed dial: transcript (no frames), efficient (fast keyframes, cap 50), "
             "balanced (scene, cap 100), token-burner (scene, uncapped).",
    )
    ap.add_argument(
        "--timestamps",
        type=str,
        default=None,
        help="Comma-separated absolute timestamps (SS, MM:SS, HH:MM:SS) to grab a frame at, "
             "e.g. transcript-flagged 'look here' moments. Added on top of the detail frames "
             "(reserved against the cap); with --detail transcript these become the only frames.",
    )
    ap.add_argument("--start", type=str, default=None, help="Range start (SS, MM:SS, or HH:MM:SS)")
    ap.add_argument("--end", type=str, default=None, help="Range end (SS, MM:SS, or HH:MM:SS)")
    ap.add_argument("--out-dir", type=str, default=None, help="Working directory (default: tmp)")
    ap.add_argument(
        "--no-whisper",
        action="store_true",
        help="Disable Whisper fallback. Report frames-only if no captions available.",
    )
    ap.add_argument(
        "--whisper",
        choices=["groq", "openai", "local"],
        default=None,
        help="Force a specific Whisper backend. Default: prefer Groq, then OpenAI, then local whisper.cpp.",
    )
    ap.add_argument(
        "--no-dedup",
        action="store_true",
        help="Disable near-duplicate frame removal. Keeps visually identical "
             "frames (static screen recordings, held slides) instead of collapsing them.",
    )
    ap.add_argument(
        "--no-auto-cues",
        action="store_true",
        help="Do not read the transcript for moments the speaker points at the "
             "screen ('look at this', 'as you can see'). Those moments are "
             "pinned by default because visual selection samples by change and "
             "pointing at a slide barely changes the picture.",
    )
    ap.add_argument(
        "--make-note",
        action="store_true",
        help="Keep the run instead of throwing it away: a durable working "
             "directory under WATCH_NOTE_DIR, a higher resolution floor, the "
             "seconds the dedup pass collapsed recorded rather than discarded, "
             "and a run.json a note's claims can later be checked against. "
             "Costs more tokens and more disk; changes nothing when absent.",
    )
    args = ap.parse_args()

    make_note = args.make_note
    resolution = args.resolution
    if resolution is None:
        resolution = NOTE_RESOLUTION if make_note else DEFAULT_RESOLUTION
    args.resolution = resolution

    config = get_config()
    detail = args.detail or str(config["detail"])
    configured_cap = frame_cap(detail)
    if args.max_frames is not None:
        max_frames = args.max_frames
    else:
        max_frames = configured_cap
    if max_frames is not None and max_frames < 1:
        raise SystemExit("--max-frames must be greater than zero")
    budget_cap = max_frames if max_frames is not None else 100
    cue_timestamps = parse_timestamps(args.timestamps)

    url_source = is_url(args.source)
    note_root = note_dir() if make_note else None
    if args.out_dir:
        work = Path(args.out_dir).expanduser().resolve()
    elif make_note and not url_source:
        # A local file's id is a digest of its path, so it is known now and the
        # directory never has to move.
        work = note_run_dir(note_root, video_id_of(args.source, {}))
    elif make_note:
        work = note_run_dir(note_root, "pending")
    else:
        work = Path(tempfile.mkdtemp(prefix="watch-"))
    work.mkdir(parents=True, exist_ok=True)
    print(f"[watch] working dir: {work}", file=sys.stderr)

    dl: dict = {"subtitle_path": None, "info": {}, "downloaded": False}
    transcript_segments: list[dict] = []
    transcript_text: str | None = None
    transcript_source: str | None = None
    video_path: str | None = None

    if url_source:
        print("[watch] checking metadata/captions via yt-dlp…", file=sys.stderr)
        dl = fetch_captions(args.source, work / "download")
        if make_note and not args.out_dir:
            work = rehome(work, note_run_dir(
                note_root, video_id_of(args.source, dl.get("info") or {})), dl)
            print(f"[watch] durable run: {work}", file=sys.stderr)
        if dl.get("subtitle_path"):
            try:
                transcript_segments = parse_vtt(dl["subtitle_path"])
                transcript_text = format_transcript(transcript_segments)
                transcript_source = "captions"
            except Exception as exc:
                print(f"[watch] subtitle parse failed: {exc}", file=sys.stderr)
                transcript_segments = []

    # THE SPEAKER KNOWS WHERE TO LOOK AND THE SAMPLER DOES NOT. Scene and
    # keyframe selection both sample by visual change, and pointing at a slide
    # is a low-change moment, so a screen recording gets frames of the cuts and
    # none of the content. Where captions exist, the moments the speaker flags
    # are pinned before the detail engine spends its budget. `--timestamps`
    # still wins outright: an explicit ask is never overridden by a guess.
    if transcript_segments and not cue_timestamps and not args.no_auto_cues:
        cue_timestamps = deictic_cues(transcript_segments)
        if cue_timestamps:
            print(f"[watch] {len(cue_timestamps)} transcript cue(s) found "
                  f"where the speaker points at the screen; pinning a frame at "
                  f"each", file=sys.stderr)

    # --timestamps needs the video for frame grabs, so it overrides the
    # transcript-mode download skip (and forces a full, not audio-only, fetch).
    audio_only = detail == "transcript" and not cue_timestamps
    if detail == "transcript" and transcript_segments and not cue_timestamps:
        video_path = None
    else:
        if url_source:
            print(
                "[watch] downloading audio via yt-dlp…" if audio_only
                else "[watch] downloading video via yt-dlp…",
                file=sys.stderr,
            )
            dl = download(
                args.source,
                work / "download",
                audio_only=audio_only,
            )
        else:
            print("[watch] using local file…", file=sys.stderr)
            dl = download(args.source, work / "download")
        video_path = dl["video_path"]

    meta = get_metadata(video_path) if video_path else {
        "duration_seconds": float((dl.get("info") or {}).get("duration") or 0),
        "width": None,
        "height": None,
        "codec": None,
        "has_audio": False,
    }
    full_duration = meta["duration_seconds"]

    start_sec = parse_time(args.start)
    end_sec = parse_time(args.end)

    if start_sec is not None and start_sec < 0:
        raise SystemExit("--start must be non-negative")
    if end_sec is not None and start_sec is not None and end_sec <= start_sec:
        raise SystemExit("--end must be greater than --start")
    if full_duration > 0 and start_sec is not None and start_sec >= full_duration:
        raise SystemExit(f"--start {start_sec:.1f}s is past end of video ({full_duration:.1f}s)")

    effective_start = start_sec if start_sec is not None else 0.0
    effective_end = end_sec if end_sec is not None else full_duration
    effective_duration = max(0.0, effective_end - effective_start)
    focused = start_sec is not None or end_sec is not None

    if focused:
        fps, target = auto_fps_focus(effective_duration, max_frames=budget_cap)
    else:
        fps, target = auto_fps(effective_duration, max_frames=budget_cap)
    if args.fps is not None:
        fps = min(args.fps, MAX_FPS)
        target = max(1, int(round(fps * effective_duration)))

    if transcript_segments and focused:
        transcript_segments = filter_range(transcript_segments, start_sec, end_sec)
        transcript_text = format_transcript(transcript_segments)

    scope = (
        f"{format_time(effective_start)}-{format_time(effective_end)} ({effective_duration:.1f}s)"
        if focused else f"full {effective_duration:.1f}s"
    )
    frames: list[dict] = []
    frame_meta: dict = {"engine": "none", "candidate_count": 0, "selected_count": 0, "fallback": False}
    cue_frames: list[dict] = []
    cue_meta: dict = {}

    # Transcript cues are pinned: extracted first and counted against the cap so
    # the detail engine never evicts the moments the user explicitly asked for.
    if cue_timestamps and video_path:
        cue_frames, cue_meta = extract_at_timestamps(
            video_path,
            work / "frames",
            cue_timestamps,
            resolution=args.resolution,
            max_frames=max_frames,
            start_seconds=start_sec,
            end_seconds=end_sec,
        )
        if cue_meta.get("dropped_out_of_window"):
            print(
                f"[watch] {cue_meta['dropped_out_of_window']} cue timestamp(s) outside the "
                "focus range — dropped",
                file=sys.stderr,
            )

    detail_budget = max_frames if max_frames is None else max(0, max_frames - len(cue_frames))
    if detail != "transcript" and video_path and detail_budget != 0:
        cap_label = "unlimited" if detail_budget is None else str(detail_budget)
        engine_label = "keyframes" if detail == "efficient" else "scene-aware frames"
        print(
            f"[watch] extracting {engine_label} over {scope} "
            f"(target {target}, cap {cap_label})…",
            file=sys.stderr,
        )
        if detail == "efficient":
            frames, frame_meta = extract_keyframes(
                video_path,
                work / "frames",
                resolution=args.resolution,
                max_frames=detail_budget,
                start_seconds=start_sec,
                end_seconds=end_sec,
                dedup=not args.no_dedup,
            )
        else:  # balanced, token-burner
            frames, frame_meta = extract_scene_or_uniform(
                video_path,
                work / "frames",
                fps=fps,
                target_frames=target,
                resolution=args.resolution,
                max_frames=detail_budget,
                start_seconds=start_sec,
                end_seconds=end_sec,
                dedup=not args.no_dedup,
            )

    if cue_frames:
        frames = merge_frames(frames, cue_frames)

    # Last step before reporting: every surviving frame states its own second
    # twice — in its file name, and in its own pixels. The name alone was
    # already shipped when a whole batch shifted by one anyway, because names
    # arrive as a separate list from the images and position does the pairing.
    frames = burn_stamps(stamp_paths(frames))

    if not transcript_segments and dl.get("subtitle_path"):
        try:
            all_segments = parse_vtt(dl["subtitle_path"])
            transcript_segments = filter_range(all_segments, start_sec, end_sec) if focused else all_segments
            transcript_text = format_transcript(transcript_segments)
            transcript_source = "captions"
        except Exception as exc:
            print(f"[watch] subtitle parse failed: {exc}", file=sys.stderr)

    if not transcript_segments and not args.no_whisper and video_path and meta.get("has_audio"):
        backend, api_key = load_api_key(args.whisper)
        if backend and api_key:
            # Cloud backend first; if it fails and a local whisper.cpp install
            # is configured, retry locally before giving up on a transcript.
            attempts = [(backend, api_key)]
            if backend != "local" and args.whisper is None:
                local_backend, local_bin = load_api_key("local")
                if local_backend and local_bin:
                    attempts.append((local_backend, local_bin))
            for attempt_backend, attempt_key in attempts:
                try:
                    all_segments, used_backend = transcribe_video(
                        video_path,
                        work / "audio.mp3",
                        backend=attempt_backend,
                        api_key=attempt_key,
                    )
                    transcript_segments = filter_range(all_segments, start_sec, end_sec) if focused else all_segments
                    transcript_text = format_transcript(transcript_segments)
                    transcript_source = f"whisper ({used_backend})"
                    break
                except SystemExit as exc:
                    print(f"[watch] whisper ({attempt_backend}) failed: {exc}", file=sys.stderr)
        else:
            hint = (
                f"--whisper {args.whisper} was set but the matching API key is missing"
                if args.whisper else
                "no subtitles and no Whisper API key found"
            )
            setup_py = SCRIPT_DIR / "setup.py"
            print(
                f"[watch] {hint} — run `python3 {setup_py}` to enable the Whisper fallback",
                file=sys.stderr,
            )
    elif not transcript_segments and video_path and not meta.get("has_audio"):
        print("[watch] no audio stream found — proceeding without transcription", file=sys.stderr)

    # A SECOND CUE PASS, FOR THE VIDEOS THAT HAD NO CAPTIONS TO READ FIRST.
    # The transcript that arrives from Whisper arrives after the frames are
    # already chosen, so the pass above had nothing to scan — which is exactly
    # the case where visual selection is on its own and most likely to miss the
    # moments the speaker points at. Grabbing them now costs one ffmpeg seek per
    # cue and nothing else; the detail frames already taken are kept.
    if (transcript_segments and not cue_frames and not args.no_auto_cues
            and not cue_timestamps and video_path and detail != "transcript"):
        late_cues = deictic_cues(transcript_segments)
        if late_cues:
            late_frames, _ = extract_at_timestamps(
                video_path,
                work / "frames",
                late_cues,
                resolution=args.resolution,
                max_frames=None,
                start_seconds=start_sec,
                end_seconds=end_sec,
            )
            if late_frames:
                print(f"[watch] {len(late_frames)} transcript cue(s) found in the "
                      f"Whisper transcript; frames grabbed after the fact",
                      file=sys.stderr)
                frames = merge_frames(frames, burn_stamps(stamp_paths(late_frames)))

    info = dl.get("info") or {}

    print()
    print("# watch: video report")
    print()
    print(f"- **Source:** {args.source}")
    if info.get("title"):
        print(f"- **Title:** {info['title']}")
    if info.get("uploader"):
        print(f"- **Uploader:** {info['uploader']}")
    print(f"- **Duration:** {format_time(full_duration)} ({full_duration:.1f}s)")
    if focused:
        print(
            f"- **Focus range:** {format_time(effective_start)} → {format_time(effective_end)} "
            f"({effective_duration:.1f}s)"
        )
    if meta.get("width") and meta.get("height"):
        print(f"- **Resolution:** {meta['width']}x{meta['height']} ({meta.get('codec') or 'unknown codec'})")
    range_mode = "focused" if focused else "full"
    print(f"- **Detail:** {detail}")
    detail_count = frame_meta.get("selected_count", 0)
    if detail != "transcript":
        cap_label = "unlimited" if detail_budget is None else str(detail_budget)
        engine = frame_meta.get("engine", "scene")
        fallback = " with uniform fallback" if frame_meta.get("fallback") else ""
        deduped = frame_meta.get("deduped_count", 0)
        dedup_note = f", {deduped} near-duplicate{'s' if deduped != 1 else ''} dropped" if deduped else ""
        print(
            f"- **Frames:** {detail_count} selected from {frame_meta.get('candidate_count', detail_count)} "
            f"candidates ({engine}{fallback}{dedup_note}, {range_mode} range, budget {target}, cap {cap_label})"
        )
    elif not cue_frames:
        print("- **Frames:** skipped (transcript detail)")
    if cue_frames:
        dropped = cue_meta.get("dropped_out_of_window", 0)
        drop_note = f", {dropped} dropped outside range" if dropped else ""
        print(
            f"- **Cue frames:** {len(cue_frames)} at transcript-flagged timestamps "
            f"(transcript-cue{drop_note})"
        )
    if frames:
        print(f"- **Frame size:** max {args.resolution}px wide, max 1998px tall")
    if transcript_segments:
        in_range = " in range" if focused else ""
        print(
            f"- **Transcript:** {len(transcript_segments)} segments{in_range} "
            f"(via {transcript_source or 'captions'})"
        )
    else:
        print("- **Transcript:** none available")

    if detail == "token-burner" and len(frames) > 250:
        print()
        print(
            f"> **Warning:** token-burner detail selected {len(frames)} frames. "
            "This may use a large number of image tokens."
        )

    if not focused and full_duration > 600 and detail not in ("transcript", "token-burner"):
        mins = int(full_duration // 60)
        print()
        print(
            f"> **Warning:** This is a {mins}-minute video. Frame coverage is sparse at this length "
            f"under `{detail}` detail — its cap spreads thin across the full clip. For better results, "
            "re-run with `--start HH:MM:SS --end HH:MM:SS` to zoom into a section, or use "
            "`--detail token-burner` to keep every scene-change frame across the whole video."
        )

    print()
    print("## Frames")
    print()
    if frames:
        print(f"Frames live at: `{work / 'frames'}`")
        print()
        print(
            "**Read each frame path below with the Read tool to view the image.** "
            "Frames are in chronological order; `t=MM:SS` is the absolute timestamp in the source video."
        )
        print()
        for frame in frames:
            print(
                f"- `{frame['path']}` "
                f"(t={format_time(frame['timestamp_seconds'])}, reason={frame.get('reason', 'selected')})"
            )
    else:
        print("_No frames extracted._")

    print()
    print("## Transcript")
    print()
    if transcript_text:
        label = transcript_source or "captions"
        if focused:
            print(f"_Source: {label}. Filtered to {format_time(effective_start)} → {format_time(effective_end)}:_")
        else:
            print(f"_Source: {label}._")
        print()
        print("```")
        print(transcript_text)
        print("```")
    elif detail == "transcript":
        print(
            "_No transcript available at transcript detail. Captions were missing and Whisper was "
            "unavailable or failed, so there is no visual fallback here. Re-run with "
            "`--detail balanced` for frames._"
        )
    elif focused and dl.get("subtitle_path"):
        print(f"_No transcript lines fell inside {format_time(effective_start)} → {format_time(effective_end)}._")
    else:
        setup_py = SCRIPT_DIR / "setup.py"
        print(
            "_No transcript available — proceed with frames only. "
            "Captions were missing and the Whisper fallback was unavailable "
            "(no API key set, or `--no-whisper` was used). "
            f"Run `python3 {setup_py}` to enable Whisper, then re-run._"
        )

    print()
    print("---")
    if not make_note:
        print(f"_Work dir: `{work}` — delete when done._")
        return 0

    run = build_run(
        source=args.source,
        video_id=video_id_of(args.source, info),
        work=work,
        info=info,
        duration=full_duration,
        resolution=args.resolution,
        detail=detail,
        video_path=video_path,
        frames=frames,
        dropped_seconds=frame_meta.get("deduped_seconds") or [],
        transcript_source=transcript_source,
        transcript_segments=transcript_segments,
        subtitle_path=dl.get("subtitle_path"),
    )
    run_path = write_run(work, run)
    dropped = run["deduped_seconds"]
    print(f"_Work dir: `{work}` — **kept**, not a temp dir. Nothing here is "
          f"deleted for you: a note's claims resolve against these files._")
    print()
    print("## Note mode")
    print()
    print(f"- **Run record:** `{run_path}` — {len(run['frames'])} frame(s) with "
          f"a sha256 each, {run['transcript']['segments']} transcript segment "
          f"start(s), source digest `{run['video_sha256'] or 'n/a'}`.")
    if dropped:
        print(f"- **Collapsed seconds:** {len(dropped)} second(s) existed and "
              f"were merged into a neighbouring frame as near-identical. They "
              f"are listed in `run.json` under `deduped_seconds`; a held slide "
              f"was on screen across them even though only one frame survives.")
    print(f"- **Anchors:** a claim may only be stamped at a second the "
          f"transcript actually starts on. Those seconds are in `run.json` "
          f"under `transcript.segment_starts`.")
    print(f"- **The note contract is not in this report.** Read `NOTE.md` "
          f"beside this skill and follow it instead of Step 4.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
