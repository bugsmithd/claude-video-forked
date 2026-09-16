---
name: watch
version: "0.7.1"
description: Watch a video (URL or local path) and answer questions about it, or write a keepable note from it. Downloads with yt-dlp, samples second-stamped frames with ffmpeg, and transcribes from captions or Whisper (OpenRouter, Groq, OpenAI, local whisper.cpp). Use `--detail transcript` for no frames, `--no-captions` to decode the audio instead of trusting a caption track, `--whisper openrouter` to pick the backend by name, and `--make-note` for a durable run with a `run.json` a note's claims can be checked against. Fork of bradautomates/claude-video.
argument-hint: "<video-url-or-path> [question]"
allowed-tools: Bash, Read, AskUserQuestion
homepage: https://github.com/bugsmithd/claude-video-forked
repository: https://github.com/bugsmithd/claude-video-forked
author: Divyanshu Rathore
license: MIT
user-invocable: true
---

# /watch

You don't have a video input; this skill gives you one. A Python script gets captions first, optionally downloads the video, extracts frames as JPEGs (scene-aware, or fast keyframes at `efficient` detail), gets a timestamped transcript (native captions first, then Whisper API as fallback), and prints frame paths. You then `Read` each frame path to see the images and combine them with the transcript to answer the user.

## Resolve `SKILL_DIR` (do this before any command)

Every `python3 ...` command below runs a bundled script under `SKILL_DIR/scripts/`. Set `SKILL_DIR` to the **absolute path of the directory containing THIS SKILL.md you just Read** — your harness told you that path in the Read result. The scripts are always a direct sibling of this file (`SKILL_DIR/scripts/watch.py`), in every install layout:

```
Read ~/.claude/plugins/cache/claude-video/watch/<ver>/skills/watch/SKILL.md → SKILL_DIR=…/skills/watch
Read ~/.codex/skills/watch/SKILL.md                                          → SKILL_DIR=~/.codex/skills/watch
Read ~/.agents/skills/watch/SKILL.md                                         → SKILL_DIR=~/.agents/skills/watch
```

Substitute that literal path for `${SKILL_DIR}` in every command. This works on every harness (Claude Code, Codex, Cursor, Gemini CLI, …) without relying on any harness-specific environment variable. Guard once at the start of a run:

```bash
SKILL_DIR="<absolute path of the directory containing the SKILL.md you Read>"
if [ ! -f "$SKILL_DIR/scripts/watch.py" ]; then
  echo "ERROR: scripts/watch.py not found under SKILL_DIR=$SKILL_DIR" >&2
  echo "Re-check the directory of the SKILL.md you Read and substitute it as SKILL_DIR." >&2
  exit 1
fi
```

## Step 0 — Setup preflight (runs every `/watch` invocation, silent on success)

**Python interpreter:** every `python3 ...` command in this skill is for macOS/Linux. On **Windows**, substitute `python` — the `python3` command on Windows is the Microsoft Store stub and will not run the script.

On the first `/watch` invocation in a session, use structured preflight so you can detect first-run setup:

```bash
python3 "${SKILL_DIR}/scripts/setup.py" --json
```

Branch on two fields:

- **`can_proceed: true` and `first_run: false`** → setup is already done (the user may have deliberately skipped a Whisper key — that's allowed). Proceed to Step 1 without comment.
- **`first_run: true`** → genuine first-time setup. Do these in order:
  1. If `missing_binaries` is non-empty, run the installer first (it auto-installs on macOS / prints commands elsewhere — see below) and confirm the binaries land. **Do not skip this and jump to preferences.**
  2. Run the installer once more if needed so it scaffolds `~/.config/watch/.env` (it only writes the template when the file is absent, so let it create the file *before* you write any values into it).
  3. Encourage a Whisper API key and ask the watch-preference questions below, then write the selected values into `~/.config/watch/.env` and set `SETUP_COMPLETE=true`.
- **`can_proceed: false` and `first_run: false`** → setup was finished before but the environment regressed (e.g. `missing_binaries` after an OS change). Run the installer to remediate, then proceed. Don't re-ask preferences.

A missing Whisper key is *encouraged to fix, not required*: on a genuine first run `status` will read `needs_key` even when binaries are present — that's your cue to encourage a key, not a blocker.

On follow-up `/watch` calls in the same session, use the silent check:

```bash
python3 "${SKILL_DIR}/scripts/setup.py" --check
```

This is a <100ms lookup. Exit 0 means /watch can run — this **includes a user who finished setup without a Whisper key** (keyless is allowed). On exit 0 the script emits **nothing** — proceed to Step 1 without comment. **Do NOT announce "setup is complete" to the user** — they don't need a status message on every turn. The only acceptable user-visible output from Step 0 is when remediation is required.

On non-zero exit, follow the table:

| Exit | Meaning | Action |
|------|---------|--------|
| `2` | Missing binaries (`ffmpeg` / `ffprobe` / `yt-dlp`) | Run installer |
| `3` | Genuine first run with no Whisper API key | Run installer to scaffold `.env`, then encourage a key (the user may decline — proceed with `--no-whisper`) |
| `4` | Both missing | Run installer, then encourage a key |

Exit `3` only fires before the user has completed setup. Once `SETUP_COMPLETE=true` is written, a keyless install returns exit 0 and is never nagged again.

The installer is idempotent — safe to re-run:

```bash
python3 "${SKILL_DIR}/scripts/setup.py"
```

On macOS with Homebrew, it auto-installs `ffmpeg` and `yt-dlp`. On Linux/Windows, it prints the exact install commands for the user to run. It scaffolds `~/.config/watch/.env` with commented placeholders and default watch settings at `0600` perms.

**If an API key is still missing after install:** use `AskUserQuestion` to ask the user whether they have a Groq API key (preferred — cheaper, faster) or an OpenAI key. Then write it into `~/.config/watch/.env` — set the matching `GROQ_API_KEY=...` or `OPENAI_API_KEY=...` line. If they don't want to set up Whisper, proceed with `--no-whisper` and tell them videos without native captions will come back frames-only.

**First-run watch preference:** after the installer has scaffolded `~/.config/watch/.env`, use `AskUserQuestion` to ask one question:

- Default detail (one dial). Present these as `AskUserQuestion` options in this exact order — lightest to heaviest — and keep `(recommended)` on `balanced` even though it is not first (do **not** reorder to put the recommended option first):
  - `transcript` — no frames at all, transcript only (skips video download when captions exist).
  - `efficient` — fast keyframe pass (cap 50).
  - `balanced` (recommended) — scene-aware frames (cap 100, default).
  - `token-burner` — scene-aware, uncapped (maximum fidelity; high token cost).

Write the answer directly into `~/.config/watch/.env` by setting the bare key on its own line — **no trailing inline comment** (a `# note` after the value can break parsing):

```bash
WATCH_DETAIL=balanced
```

Use the user's selected value. If they skip the question, keep the recommended default. Once dependencies, the API-key choice, and this preference are handled, write or update `SETUP_COMPLETE=true` in the same file. Do not ask this preference question again when `SETUP_COMPLETE=true`.

**Structured mode (optional):** `python3 "${SKILL_DIR}/scripts/setup.py" --json` emits `{status, can_proceed, first_run, setup_complete, missing_binaries, whisper_backend, has_api_key, config_file, watch_detail, platform}` where `status` is one of `ready | needs_install | needs_key | needs_install_and_key`. `status` describes the *ideal* state (a key is encouraged, so a keyless first run reads `needs_key`); `can_proceed` is the operational gate (binaries present AND a key is set OR setup was already completed). Branch on `can_proceed`/`first_run` to decide whether to run; use `status` to decide what to encourage.

Within a single session, you can skip Step 0 on follow-up `/watch` calls — once `--check` returned 0, nothing about the environment changes between turns.

## When to use

- User pastes a video URL (YouTube, Vimeo, X, TikTok, Twitch clip, most yt-dlp-supported sites) and asks about it.
- User points at a local video file (`.mp4`, `.mov`, `.mkv`, `.webm`, etc.) and asks about it.
- User types `/watch <url-or-path> [question]`.

## Recommended limits

- **Best accuracy: videos under 10 minutes.** Frame coverage scales inversely with duration.
- **Universal rate cap: 2 fps.** The script never samples faster than 2 fps, even when a budget or `--fps` would imply more.
- **The frame ceiling is set by the detail mode** (`WATCH_DETAIL` in `~/.config/watch/.env`, or `--detail`), not a single global cap:
  - `transcript` → no frames
  - `efficient` → up to **50** (keyframes)
  - `balanced` (default) → up to **100** (scene-aware)
  - `token-burner` → **uncapped** (scene-aware; a soft warning prints past 250 frames)
  - `--max-frames N` overrides whichever cap the mode would otherwise use.
- **Full-video frame budget by duration.** Token cost grows with frame count, so the script targets a budget by duration. This budget sets the fps and the uniform-sampling fallback; scene-aware selection can fill up to the detail cap above, whichever is lower:
  - ≤30s → ~12-30 frames
  - 30s-1min → ~40 frames
  - 1-3min → ~60 frames
  - 3-10min → ~80 frames
  - \>10min → up to the detail cap, sparsely spaced (warning printed)
- If the user hands you a long video, consider asking whether they want a specific section before burning tokens on a sparse scan.

## How to invoke

**Step 1 — parse the user input.** Separate the video source (URL or path) from any question the user asked. Example: `/watch https://youtu.be/abc what language is this in?` → source = `https://youtu.be/abc`, question = `what language is this in?`.

**Step 2 — run the watch script.** Pass the source verbatim. Do not shell-escape it yourself beyond normal quoting:

```bash
python3 "${SKILL_DIR}/scripts/watch.py" "<source>"
```

Optional flags:
- `--detail transcript|efficient|balanced|token-burner` — fidelity/speed dial. `transcript` = no frames (transcript only, skips video download when captions exist); `efficient` = fast keyframes (cap 50); `balanced` = scene-aware frames (cap 100); `token-burner` = scene-aware, uncapped.
- `--start T` / `--end T` — focus on a section. Accepts `SS`, `MM:SS`, or `HH:MM:SS`. When either is set, fps auto-scales denser (see "Focusing on a section" below).
- `--timestamps T1,T2,…` — grab a frame at each of these absolute timestamps (`SS`, `MM:SS`, or `HH:MM:SS`). Use this after reading the transcript to capture deictic moments the presenter flags ("look here", "as you can see", "notice this") that visual selection alone may miss. See "Transcript-cue frames" below.
- `--max-frames N` — override the preset cap for tighter token budget (e.g. `--max-frames 40`)
- `--resolution W` — change frame width in px (default **768**). Measured on twelve frames of a UI tutorial, OCR'd at four widths: 512px yields 17 readable words, 768px yields 402, 1024px yields 637, 1536px yields 891. Use 1024+ for dense UI, code or small type; 512 only when the video is a talking head and tokens matter more than text.
- `--fps F` — override auto-fps (clamped to 2 fps max)
- `--out-dir DIR` — keep working files somewhere specific (default: an auto-generated tmp dir)
- `--whisper openrouter|groq|openai|local` — force a specific Whisper backend (default: prefer Groq, then OpenAI, then local whisper.cpp). `openrouter` is never chosen automatically, however many keys are set: that endpoint ignores the provider pin and routes to whichever provider is cheapest, so the model that actually decoded a run can change between runs. Ask for it by name.
- `--no-captions` — ignore the video's own subtitles and transcribe the audio instead. Captions win by default because they are free and exact when a human wrote them; an auto-generated track is neither, and for a note whose claims are quoted from the transcript, choosing the decoder is part of the method. Applies to both parse sites — a URL's downloaded track and a local file's sidecar.
- `--no-whisper` — disable the Whisper fallback entirely (frames-only if no captions)
- `--no-dedup` — keep near-duplicate frames. By default a frame-delta pass drops frames that are visually near-identical to the previous kept one (held slides, static screen recordings, paused video) so the frame budget goes to distinct content; the report's **Frames** line notes how many were dropped. Pass this only if the user needs every sampled frame (e.g. judging subtle frame-to-frame motion).
- `--make-note` — keep the run instead of throwing it away. See "Note mode" below. Costs more tokens and more disk; changes nothing when absent.

### Note mode (`--make-note`)

Use it when the output is a **file the user keeps**, not an answer in chat — a research note whose claims have to still be checkable months from now. Do not use it for ordinary questions: it is more expensive on every axis.

Ordinary `/watch` is built to throw the evidence away — a temp directory, a "delete when done" line, collapsed near-duplicate frames, a report that is prose. Every one of those is right for answering a question and wrong for a note whose claims resolve against pixels and seconds. `--make-note` inverts that trade for one run:

- The working directory is **durable**, under `WATCH_NOTE_DIR` (default `~/watch-runs`), as `<video_id>/run-NN`. Nothing overwrites an earlier run and nothing is deleted for the user.
- `--resolution` floors at **1024** instead of 768, because a note anchors claims to on-screen text. An explicit `--resolution` still wins.
- The seconds the dedup pass collapsed are **recorded, not just counted**, so a held slide can be described across the whole span it was up.
- A `run.json` is written: a sha256 per frame and for the source, the transcript backend, and every second the transcript starts on — which are the only seconds a claim may be anchored to.

**Then read `NOTE.md` beside this file and follow it instead of Step 4.** It carries the note contract: evidence classes, the verbatim-quote rule, hedges, the do-not-name-the-speaker rule, and how to read frames in batches. The report prints the `run.json` path and points at it.

Check the finished note with one command:

```bash
watch-audit <note.md>
```

Exit 0 = every gate passed. Exit 1 = defects, printed above. **Exit 2 = a gate could not run, which is not a pass.** Fix what it finds before calling the note done.

The gates prove a note is well-formed, never that it is right — a wrong attribution, a dropped hedge and a confidently stated inference pass all of them. So read `REVIEW.md` beside this file and run it: three fresh readers, refute-by-default, frames in bounded batches, one agent per lane in parallel, **every lane at `xhigh` reasoning effort** — measured 2026-09-09 over 30 unreviewed notes, `xhigh` finds about four more real defects per note than `low` in every lane at the same accuracy, so `low` is the quiet setting and not the sloppy one. **On a note built with `--make-note` this is part of building the note, not a favour asked afterwards** — generate the briefs with `review.py <run.json> <note.md>`, dispatch the lanes, file the reports, then list them in the note's `reviews:` field. This line used to say the review was opt-in and never automatic, and the cost argument quietly won every time: a note reached its corpus stamped and gated with the review layer skipped and nothing recorded that it had been. A corpus can now name the lanes it requires, and `watch-audit` fails a note that is short one (`E-LANE-UNREVIEWED`).

### Focusing on a section (higher frame rate)

When the user asks about a specific moment — "what happens at the 2 minute mark?", "zoom into 0:45 to 1:00", "the first 10 seconds" — pass `--start` and/or `--end`. The script switches to focused-mode budgets, which are denser than full-video budgets (still capped at 2 fps, and still bounded by the detail-mode cap — the counts below assume the default `balanced` cap of 100; `efficient` tops out at 50):

- ≤5s → 2 fps (up to 10 frames)
- 5-15s → 2 fps (up to 30 frames)
- 15-30s → ~2 fps (up to 60 frames)
- 30-60s → ~1.3 fps (up to 80 frames)
- 60-180s → ~0.6 fps (100 frames, capped)

Focused mode is the right call for:
- Any moment/range the user names explicitly ("around 2:30", "the intro", "the last 30 seconds").
- Any video longer than ~10 minutes where the user's question is about a specific part — running focused on the relevant section is far more useful than a sparse scan of the whole thing.
- Re-runs after a full scan didn't have enough detail in some region.

Transcript is auto-filtered to the same range. Frame timestamps are absolute (real video timeline, not offset-from-start).

Examples:
```bash
# Last 10 seconds of a 1 minute video
python3 "${SKILL_DIR}/scripts/watch.py" video.mp4 --start 50 --end 60

# Zoom into 2:15 → 2:45 at 2 fps (60 frames)
python3 "${SKILL_DIR}/scripts/watch.py" "$URL" --start 2:15 --end 2:45 --fps 2

# From 1h12m to the end of the video
python3 "${SKILL_DIR}/scripts/watch.py" "$URL" --start 1:12:00
```

**Step 3 — Read every frame path the script lists, in batches of at most 20.** The Read tool renders JPEGs directly as images for you. Each batch is one message whose file names sit beside its own images; do not open a hundred frames across one message and pair them with a separate list afterwards, which is how a whole batch shifts by one and nobody notices. Every frame also carries its second **in its own pixels**, drawn top-left, so a mis-pairing is visible in the image rather than inferred from list order — if a frame's burned-in `t=` disagrees with the path you think you are reading, trust the pixels and say so.

**Step 4 — answer the user.** You now have two streams of evidence:
- **Frames** — what's on screen at each timestamp
- **Transcript** — what's said at each timestamp. The report's header shows the source (`captions` = yt-dlp pulled native subs; `whisper (groq)` or `whisper (openai)` = transcribed by API).

If the user asked a specific question, answer it directly citing timestamps. If they didn't ask anything, summarize what happens in the video — structure, key moments, notable visuals, spoken content.

This holds for `transcript` detail too: even with no frames, produce a **summary** like the other modes — do not paste the full transcript into chat. Synthesize structure, key moments, and spoken content with timestamps; quote only the lines that matter. Offer the raw transcript only if the user explicitly asks for it.

**Step 5 — clean up.** The script prints a working directory at the end. If the user isn't going to ask follow-ups about this video, delete it with `rm -rf <dir>`. If they might, leave it in place.

## Detail and frames

Default behavior comes from `~/.config/watch/.env`:

- `WATCH_DETAIL=transcript|efficient|balanced|token-burner` (default: `balanced`)

At `transcript` detail, captions are enough to return a report without downloading video. If captions are missing, the script downloads audio only and tries Whisper. If no transcript can be produced, it reports the limitation clearly; re-run with `--detail balanced` for frames.

At `efficient` detail, the script downloads the video and extracts **keyframes only** (`ffmpeg -skip_frame nokey`) — a near-instant pass that lands frames on scene cuts. If a clip has fewer than 4 keyframes it falls back to uniform sampling.

At `balanced` / `token-burner` detail, the script extracts **scene-aware** frames: ffmpeg scene-change selection first, falling back to uniform sampling only when the video is effectively static. `balanced` caps at 100 frames; `token-burner` is uncapped. Frame report lines include both timestamp and selection reason. Extracted images are clamped to a maximum 1998px height for Claude Read compatibility.

## Transcript-cue frames

Visual frame selection (scene/keyframe) can miss the moments a presenter explicitly flags — "look here", "as you can see", "notice this", "watch what happens" — because pointing at a slide is often a *low* visual change.

**This now runs by itself.** When captions exist, the script scans the transcript for those phrases before it spends the frame budget and pins a frame at each, up to 12, at least 20s apart. The report marks them `reason=transcript-cue`. Pass `--no-auto-cues` to turn it off.

The pattern is deliberately narrow — it wants a pointing word followed by something being shown ("look at this chart"), not rhetorical use ("look, the point is"). It will still miss cues phrased unusually, so the manual route remains and **overrides** the automatic one:

1. Run once at `--detail transcript` (or any detail) to get the timestamped transcript.
2. Scan it for deictic cues the regex would not catch.
3. Re-run with `--timestamps 4:32,7:10,9:55` (absolute source times). For a URL, point the second run at the **downloaded local file** in the work dir so it doesn't re-download.

Behavior:
- **Additive by default.** Cue frames (`reason=transcript-cue`) are merged into whatever `--detail` already selected, in chronological order.
- **Pinned and counted first.** Cue frames are reserved against the frame cap before the detail engine runs, so they're never evicted by even-sampling.
- **Honors focus mode.** With `--start/--end`, any cue timestamp outside the window is dropped (reported in the summary). Coordinates are always absolute source time.
- **Cue-only frames.** `--detail transcript --timestamps …` skips scene/keyframe sampling and returns *only* the cue frames (it will download the video to do so, since frames need pixels).

## Transcription

The script gets a timestamped transcript in one of two ways:

1. **Native captions (free, preferred).** yt-dlp pulls manual or auto-generated subtitles from the source platform if available. For a **local file**, a `.vtt` sitting beside the video (same stem) is the same thing and is used the same way — handing `/watch` a recording and its own transcript used to transcribe the audio from scratch anyway. `--no-captions` skips both.
2. **Whisper fallback.** If no captions came back (or the source is a local file), the script extracts audio (`ffmpeg -vn -ac 1 -ar 16000 -b:a 64k`, ~0.5 MB/min) and transcribes it with whichever Whisper backend is configured:
   - **OpenRouter** — `openai/whisper-large-v3` through one key and one balance. **Opt-in only:** set `OPENROUTER_API_KEY` and pass `--whisper openrouter`. A key alone selects nothing, because the routing below means the provider that answers is not the one asked for. **What the run depends on is segment timestamps, and there are two ways to lose them.** A response with no seconds at all is refused rather than written. So is one whose seconds are too coarse to point at anything: a model returning a single segment that covers most of a request places every claim inside a stretch minutes long, and nothing downstream can anchor, quote or grade that. A provider preference is sent (`WATCH_OPENROUTER_PROVIDER`, default `deepinfra`) but was measured having no effect on this endpoint: pinned to `groq`, `deepinfra` and `together` in turn, the same clip came back timestamped every time and billed at DeepInfra's rate. The default is `deepinfra` and not `groq` because Groq is a first-class backend here (`--whisper groq`) — routing to it through OpenRouter pays the router to reach something already reachable directly, and DeepInfra is the one this pipeline has no other way to. Override the model with `WATCH_OPENROUTER_MODEL`. A long recording is split into `WATCH_OPENROUTER_MAX_SECONDS` (default 600) per request, because providers behind the router time out after about 60 seconds of processing. `WATCH_OPENROUTER_MODEL_2` adds an opt-in second decode written beside the first and never merged into it; prefer a different model family there — `Qwen/Qwen3-ASR-1.7B` is the one measured working through the same key, and its coarse timestamps are harmless in that slot because a second witness is read for the words it disagrees about while every stamp comes from the first decode.
   - **Groq** — `whisper-large-v3`. Cheap and fast. Get a key at console.groq.com/keys.
   - **OpenAI** — `whisper-1`. Fallback. Get a key at platform.openai.com/api-keys.
   - **Local whisper.cpp** — fully offline, no API key, audio never leaves the machine. Set `WHISPER_CPP_BIN` (a whisper-cli executable: a path, or a name on `PATH`) and `WHISPER_CPP_MODEL` (a ggml model file) in `~/.config/watch/.env`; optional `WHISPER_CPP_LANG` (default `auto` detects the spoken language) and `WHISPER_CPP_THREADS`. No 25 MB upload cap applies, so long videos go through in one pass; progress prints to stderr as it runs.

All settings live in `~/.config/watch/.env`. The script prefers Groq, then OpenAI, then local whisper.cpp; OpenRouter is reached only through `--whisper openrouter`. Override with `--whisper openrouter|groq|openai|local` to force one. When a cloud backend fails mid-run (rate limit, network) and a local install is configured, the script automatically retries locally. Use `--no-whisper` to skip the fallback entirely.

## Failure modes and handling

- **Setup preflight failed** → run `python3 "${SKILL_DIR}/scripts/setup.py"` (auto-installs ffmpeg/yt-dlp via brew on macOS, scaffolds the `.env`). For API key, ask the user via `AskUserQuestion` and write it to `~/.config/watch/.env`.
- **No transcript available** → captions missing AND (no Whisper key OR Whisper API failed). Script prints a hint pointing to setup. Proceed frames-only and tell the user.
- **Long video warning printed** → acknowledge it in your answer. Offer to re-run focused on a specific section via `--start`/`--end` rather than a sparse full-video scan.
- **Download fails** → yt-dlp's error goes to stderr. If it's a login-required or region-locked video, tell the user plainly; do not keep retrying.
- **Whisper request fails** → the error is printed to stderr (likely: invalid key or rate limit). Audio over the API's 25 MB upload cap is split into chunks and transcribed automatically, so length alone won't fail it; if some chunks fail the transcript is partial and the dropped chunks are noted on stderr. The report will say "none available" only if every chunk fails. You can retry with `--whisper openai` if Groq failed (or vice versa).

## Token efficiency

This skill burns tokens primarily on frames. Order of magnitude:
- 80 frames at 768px wide is roughly 110-180k image tokens depending on aspect ratio; the same 80 at 512px is roughly 50-80k and reads almost no on-screen text.
- The transcript is cheap (a few thousand tokens at most for a 10-minute video).
- Bumping `--resolution` to 1024 roughly quadruples the image tokens per frame. Only do it when necessary.

If you already watched a video this session and the user asks a follow-up, do **not** re-run the script — you already have the frames and transcript in context. Just answer from what you have.

## Security & Permissions

**What this skill does:**
- Runs `yt-dlp` locally to download the video and pull native captions when the source supports them (public data; the request goes directly to whatever host the URL points at)
- Runs `ffmpeg` / `ffprobe` locally to extract frames as JPEGs and, when Whisper is needed, a mono 16 kHz audio clip
- Sends the extracted audio clip to OpenRouter (`openrouter.ai/api/v1/audio/transcriptions`) only when `--whisper openrouter` is passed and `OPENROUTER_API_KEY` is set, base64-encoded in a JSON body, routed to a pinned provider with fallbacks off
- Sends the extracted audio clip to Groq's Whisper API (`api.groq.com/openai/v1/audio/transcriptions`) when `GROQ_API_KEY` is set
- Sends the extracted audio clip to OpenAI's audio transcription API (`api.openai.com/v1/audio/transcriptions`) when `OPENAI_API_KEY` is set and Groq is not, or when `--whisper openai` is forced
- Writes the downloaded video, frames, audio, and an intermediate transcript to a working directory under the system temp dir (or `--out-dir` if specified) so Claude can `Read` them
- Reads / creates `~/.config/watch/.env` (mode `0600`) to store the Whisper API key(s) and a `SETUP_COMPLETE` marker. As a fallback, also reads `.env` in the current working directory

**What this skill does NOT do:**
- Does not upload the video itself to any API — only the extracted audio goes out, and only when native captions are missing AND Whisper is not disabled with `--no-whisper`
- Does not access any platform account (no login, no session cookies, no posting) — yt-dlp only ever requests public data
- Does not share API keys between providers (Groq key only goes to `api.groq.com`, OpenAI key only goes to `api.openai.com`)
- Does not log, cache, or write API keys to stdout, stderr, or output files
- Does not persist anything outside the working directory and `~/.config/watch/.env` — clean up the working directory when you're done (Step 5)

**Bundled scripts:** `scripts/watch.py` (entry point), `scripts/download.py` (yt-dlp wrapper), `scripts/frames.py` (ffmpeg frame extraction), `scripts/transcribe.py` (caption selection + Whisper orchestration), `scripts/whisper.py` (Groq / OpenAI clients), `scripts/setup.py` (preflight + installer)

Review scripts before first use to verify behavior.
