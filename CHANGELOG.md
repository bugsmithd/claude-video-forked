# Changelog

All notable changes to `/watch` are documented here.

Entries below 0.5.0 are upstream's. This fork's 0.3.0 and 0.4.0 were released
without changelog entries and are described only in git history; 0.5.0 restores
the habit rather than back-filling from memory.

## [0.6.1] — 2026-08-19

### Added
- **A second way to lose timestamps, and a gate for it.** A response can carry
  segment times and still be unanchorable. `Qwen/Qwen3-ASR-1.7B` returns a
  typical segment of 15-27 seconds where `openai/whisper-large-v3` returns 2-3;
  both pass the has-timestamps refusal, and only one can be cited to a second.
  The primary decode now refuses a rendering whose median segment exceeds
  10 seconds, and the second decode only warns — a second witness is read for
  the words it disagrees about, and every stamp comes from the first decode.
- `Qwen/Qwen3-ASR-1.7B` is documented as the second witness for
  `WATCH_OPENROUTER_MODEL_2`, measured live through the same key at the same
  price. `qwen/qwen3-asr` and `qwen/qwen3-asr-flash` are live too and reject
  `verbose_json` outright, so they are refused before anything is written.

### Notes
- The first version of this gate judged the SHARE of a request covered by its
  longest segment. It separated the two models on a 30-second clip (27% against
  93%) and stopped separating them on a 90-second one (31% against 31%), because
  the coarse model's segments cap out near 27s while the request keeps growing —
  a gate that fires on short files and sleeps on long ones. The median does not
  move with length and replaced it before either shipped.

## [watch-quality 0.4.1] — 2026-08-19

The plugin's own version moves separately; see 0.6.1 above.

### Fixed
- `wq-corpus-scan` called `Qwen/Qwen3-ASR-1.7B` a video id. The eleven
  characters between the slash and the dot have exactly the shape the gate
  refuses, and this is the first false positive that could not be reworded away
  — a model's published name is the one string that has to appear verbatim. It
  is allowed by name, with its reason, and has a selftest case. A floor that is
  permanently red is a floor everyone learns to step over.

## [watch-quality 0.4.0] — 2026-08-19

The plugin stays at 0.5.0: nothing under `skills/watch/` changed.

### Added
- **`wq-note-coverage`** — the first check here that asks what a note LEFT OUT.
  Everything else grades what a note says; nothing could see a stretch of the
  recording nobody wrote from. It counts what a note carried, once, over three
  sets a note cannot pad its way into — **figures** (tokens with a digit),
  **names** (tokens capitalised mid-sentence) and **terms** (tokens the recording
  uses rarely) — so splitting one claim into six rows does not move the number.
  `E-COV-DEAD` names three consecutive unwritten minutes, `E-COV-DUMP` a note
  that reproduces the transcript, `E-COV-LIST` a vocabulary dump wearing a
  note's clothes, `E-COV-REPEAT` padding, `E-COV-STAMP` an anchor no clock can
  say, and `E-COV-ROWSHAPE` a line that meant to be a claim row and did not
  parse as one.
- Every constant is measured over 24 real notes, and the measurements are in the
  source beside them. Recall runs 0.189 to 0.928 across that corpus, so the
  figure separates thin notes from thorough ones rather than scoring all alike.
- `wq-transcript-align` and everything built on it now read a caption-index
  `.tsv`, so a note can be measured against the same index it was graded on.

### Notes
- **Recall is comparative, not a grade.** Its denominator includes the
  recogniser's own mis-hearings, which no note can carry. It is worth reading
  between two notes over one span, not on its own.
- An adversarial lane whose only instruction was to build inputs that score well
  defeated nine of this command's promises on its first attempt — six by scoring
  garbage clean, three by marking down notes that were correct. All nine are
  closed and each has a selftest case; the cases outnumber the checks.

## [watch-quality 0.3.1] — 2026-08-19

The plugin stays at 0.5.0: nothing under `skills/watch/` changed, only the
packaged gates.

### Fixed
- **`wq-note-windows` crashed on every real transcript.** Its summary line
  counted `orphans`, which the previous release turned from a list into a
  function, so the command printed its window table and then raised `TypeError`.
  It survived review because the selftest never called `main()` and the attack
  replay asserted exit status alone — and a crash exits non-zero, which reads as
  a defect caught. Nine selftest cases now run the command end-to-end and assert
  the codes it prints, not only what it returns.

### Changed
- An orphan list that stops at ten now says how many it did not print.

## [0.5.0] — 2026-08-19

### Added
- **Windowed local decode.** whisper.cpp now transcribes 240 seconds at a time,
  keeping 180 and taking 30 seconds of context on each side, because a long
  decode collapses: a single whole-file pass over a two-hour recording repeated
  one sentence 6,434 times from 21:49 and lost 83% of the file. Dials are
  `WATCH_DECODE_WINDOW_SECONDS` and `WATCH_DECODE_OVERLAP_SECONDS`; 0 restores
  the single pass. Cloud backends are unchanged — they have not been measured
  failing this way, which is an absence of evidence rather than a clean bill of
  health.
- **A window that loops is re-decoded from a different offset**, and the better
  of the two is kept. It is a second roll of the dice, not a cure: on one
  measured run a looping window went from 6 repeats to 1, and another went from
  6 to 30 and kept its first decode.
- **`WHISPER_CPP_MODEL_2`** runs a second decode with a second model and writes
  both renderings to the run directory. Neither is merged and neither is chosen
  automatically — the model trusted on one recording was the one that collapsed
  on the next.
- **`watch-quality` 0.3.0** ships two new commands: `wq-transcript-align`, which
  asks whether a decode survived and where a second decode disagrees, and
  `wq-note-windows`, which prints the boundaries a long video would be written
  from.

### Fixed
- Windowed output no longer duplicates or drops a sentence at a window
  boundary. Each window reaches two seconds back past its own edge and drops
  what the previous window already kept, matched on words rather than the clock.

## [0.2.0] — 2026-06-29

### Added
- **`--detail` dial** with four modes — `transcript` (captions only, no frames), `efficient` (fast keyframe pass, cap 50), `balanced` (scene-aware, cap 100, default), and `token-burner` (scene-aware, uncapped). Set the default with `WATCH_DETAIL` in `~/.config/watch/.env`.
- **Frame deduplication** (default on; `--no-dedup` to disable). Before the budget cap, a pass downscales each frame to a 16×16 grayscale thumbnail and drops frames whose mean per-pixel difference from the last *kept* frame is within threshold — so the budget goes to distinct content instead of held slides and static recordings. The **Frames** report line shows how many near-duplicates were dropped.
- **Whisper auto-chunking.** Audio over the 25 MB upload cap is split into evenly sized chunks, transcribed per chunk, with segment timestamps shifted back into source time. Partial failures are tolerated — transcription only fails if *every* chunk fails, so length alone no longer breaks it.
- **`--timestamps T1,T2,…`** — grab a frame at each absolute timestamp; reserved against the cap, and the only frames produced under `--detail transcript`.
- **`--no-whisper`** — disable transcription entirely (frames only).
- pytest suite covering config, dedup, download, fixtures, frames, setup, timestamps, watch, and whisper (no network; ffmpeg-synthesized clips).

### Changed
- **Restructured into a self-contained `skills/watch/` package** so `SKILL.md` and its `scripts/` runtime are siblings in one folder. This fixes installs on Codex, Cursor, Copilot, and other Agent Skills hosts: `npx skills add` now copies the skill as a working unit instead of grabbing the root `SKILL.md` without its scripts.
- **Harness-agnostic path resolution** — `SKILL.md` resolves `$SKILL_DIR` from where it was Read instead of the Claude-Code-only `${CLAUDE_SKILL_DIR}`, so script calls work on every host.
- `/watch` is now derived from `SKILL.md` frontmatter; the separate `commands/watch.md` wrapper was dropped to avoid a duplicate slash command.
- `balanced` now full-decodes to detect every scene cut across the whole video. The previous early-exit was faster but kept only the first cuts and dropped the tail of long videos.
- `token-burner` is exempt from the long-video "sparse scan" warning, since it keeps every scene-change frame.
- `--max-frames` is now an override on top of each mode's default cap, rather than a fixed default of 80.

### Fixed
- Non-Claude installs (`npx skills add`) were dead on arrival — the installer copied `SKILL.md` without the `scripts/` it shells out to. The self-contained package layout resolves this.

### Removed
- `V2_PLAN.md` and `V2_CONCERNS.md` planning docs.

## [0.1.3] — 2026-05-09

### Fixed
- Windows: `video.info.json` is read as UTF-8 (#4). Previously `Path.read_text()` defaulted to cp1252 on Windows and crashed on yt-dlp's UTF-8 output, silently dropping Title/Uploader from the report. Same fix applied to `.env` reads/writes in `whisper.py` and `setup.py`.
- `download.py` now logs info.json parse failures to stderr instead of swallowing them.

### Security
- Hardened subprocess argv against option injection (#2): inserted `--` before the URL in the yt-dlp argv, and tightened `is_url` to reject `-`-prefixed sources and require a non-empty netloc. Resolved video/audio paths to absolute via `Path.resolve()` before passing to `ffmpeg`/`ffprobe`, so a relative path starting with `-` can't be misinterpreted as a flag.

## [0.1.2] — 2026-04-24

### Fixed
- Windows console crash: removed the emoji from the long-video warning in `watch.py`; cp1252 consoles couldn't encode it.
- `setup.py` now prints `winget` / `pip` install commands on Windows instead of "unsupported platform" — matches what the README already promised.

### Changed
- `SKILL.md` notes that on Windows the scripts must be invoked with `python`, not `python3` (the latter is the Microsoft Store stub on Windows).

## [0.1.1] — 2026-04-24

### Fixed
- Added `commands/watch.md` shim so `/watch` is callable when installed as a Claude Code plugin. Without it, the plugin loaded but the skill wasn't exposed as a slash command.
- `scripts/build-skill.sh` now strips `commands/` from the claude.ai `.skill` bundle alongside `hooks/` and `.claude-plugin/`.

## [0.1.0] — 2026-04-24

Initial marketplace release.

### Added
- `/watch <url-or-path> [question]` slash command.
- yt-dlp download with native caption extraction (manual + auto-subs).
- ffmpeg frame extraction with auto-scaled fps (≤2 fps, ≤100 frames, duration-aware budget).
- `--start` / `--end` focused mode with denser frame budget and transcript range filtering.
- Whisper fallback (Groq preferred, OpenAI secondary) for videos without captions.
- `setup.py` preflight: silent `--check`, structured `--json`, and installer that auto-runs `brew install` on macOS.
- Session-start hook that prints a one-line status on first run / partial config.
- `.skill` bundle packaging for claude.ai upload via `scripts/build-skill.sh`.
