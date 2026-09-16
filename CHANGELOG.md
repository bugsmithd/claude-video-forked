# Changelog

All notable changes to `/watch` are documented here.

Entries below 0.5.0 are upstream's. This fork's 0.3.0 and 0.4.0 were released
without changelog entries and are described only in git history; 0.5.0 restores
the habit rather than back-filling from memory.

## [0.7.4] — 2026-09-16

### Fixed
- **An OpenRouter request whose first decode lost words was written as whole.**
  One request kept 1,070 words where the second decode kept 2,005, and the run
  exited 0: the response was fine-grained, so no word-array guard ran. With
  `WATCH_OPENROUTER_MODEL_2` set, each request span now counts its words against
  the second decode. A span under 0.75 of them, where the second decode kept at
  least 100, is cut again up to twice, each cut wider than the span, because
  re-sending the same bytes returns the same answer. A cut widens on both sides
  where it can; the first span can widen only at its end. A cut that clears the
  ratio replaces that span, `transcript-1.json` is rewritten and the alignment
  runs on it. If none clears it the run is refused, naming the span's clock range
  and how many new cuts were made, unless `WATCH_ALLOW_TRANSCRIPT_GAPS=1`. A
  single-request video (600 s or less) is refused without a retry: its one span
  is the whole audio, so no different cut exists. The second decode never supplies a word or a
  second. Without a second decode, thinning cannot be seen, and the run says so.

## [0.7.3] — 2026-09-16

watch-quality 0.5.1.

### Fixed
- **`E-WIN-OVERFULL` refused honest recordings for talking faster.** The check
  compared the biggest window's segment count against twice an even split, so a
  6.6-hour course with one 2.1x-dense stretch and a 38-minute talk with a dense
  cold open were both reported as unsplit plans although both split in time. It
  now refuses by what density cannot move: more than half of a window's members
  spanning longer than a window, one window holding over 90% of segments within
  half the recording, or members whose stamps claim more than three times the
  seconds they reach. A replay of 998 corrupted transcripts the old rule refused
  accepts none of them, and 400 honest plans are refused by none. Below six
  stacked segments in a window the third arm sees nothing; that floor is
  declared, not a defect.
- **Stacked-stamp refusals now say what is wrong** — the stamps overlap —
  instead of claiming the plan did not split.
- **Two real video ids in tracked prose** (comments, docstrings, fixture
  provenance, one doc) failed the repository's own corpus scan; they now read
  "video A" and "video B".

## [0.7.2] — 2026-09-16

### Fixed
- **The description named the flags but not the rule that governs them.** A
  reader deciding whether to run `--make-note` saw the durable run and the
  `run.json` and nothing saying the note is unfinished until `watch-audit`
  passes and the three lanes of `REVIEW.md` have each run at `xhigh`. That rule
  is already in `NOTE.md:99`, `SKILL.md` and `REVIEW.md`; it now also survives
  the one line most readers stop at, which is where a cheap reading of the cost
  used to win.

## [0.7.1] — 2026-09-16

### Fixed
- **The skill's own frontmatter was three releases stale**, so the one line a
  reader sees before invoking `/watch` still described captions and a Whisper
  fallback and nothing else: no note mode, no transcript-only detail, no way to
  name the backend. `version:` also read `0.2.0` while the plugin read `0.7.0`,
  and `homepage`, `repository` and `author` still pointed at upstream rather
  than this fork. The description now names `--detail transcript`,
  `--no-captions`, `--whisper openrouter` and `--make-note`, which is where a
  reader looks for them.

## [0.7.0] — 2026-09-16

### Added
- **`--make-note` keeps the run instead of throwing it away.** The working
  directory becomes durable under `WATCH_NOTE_DIR` (default `~/watch-runs`) as
  `<video_id>/run-NN`, numbered so nothing overwrites an earlier run, and
  `run.json` records a sha256 per frame and for the source, the transcript
  backend, and the segment starts a claim may be anchored to.
- **Frames carry their own timestamp, and get wider.** `frames.py` draws
  `t=MM:SS` into every frame with a truetype face, which tesseract reads 6 of 6
  back off a real run. The default width moves 512 to 768 on a measurement —
  twelve frames of one tutorial OCR'd at four widths give 17 readable words at
  512, 402 at 768 and 637 at 1024 — so note mode floors at 1024.
- **Three review lanes that cannot see each other.** `review.py` writes one
  brief per lane into `<run-dir>/review/` — facts, quality, coverage — each with
  a refute-by-default stance and an instruction not to read another lane,
  because three agreeing lanes look like corroboration when they are an echo.
  Every brief first counts how much of the note this run can reach: anchors with
  a frame, anchors on a segment start, longest runtime with no frame.
- **Review lanes run at xhigh reasoning effort.** Measured 2026-09-09 over 30
  unreviewed notes read at low and at xhigh across all three lanes: 180 reviews,
  1,218 findings, 610 one-sided after pairing on a shared five-word quote. xhigh
  finds about four more real defects per note per lane, at roughly 49k output
  tokens and 11 minutes against 9k and under 3 at low.
- **OpenRouter word timestamps group into anchorable segments** at a 4.0s cap, a
  0.5s silence and a sentence end past 1.0s, reproducing the 3.7s median
  `whisper-large-v3` gives when the routing is good. A transcript with a
  chunk-sized hole is refused, naming the missing clock range;
  `WATCH_ALLOW_TRANSCRIPT_GAPS=1` keeps the old behaviour.

### Fixed
- **A URL's run was named after a digest of the URL, not the video.**
  `_read_info` narrowed yt-dlp's `info.json` to the fields the report reads and
  `id` was not among them, so every note-mode run fell through to the fallback
  for local files — silently, because the report printed the right title out of
  the same dict. (`skills/watch/scripts/download.py`)
- **A run that lost every audio chunk exited 0**, printing
  `Transcript: none available`, which a batch driven off exit codes reads as
  success. Gated on Whisper having run, so `--no-whisper` on an uncaptioned clip
  still exits 0; a run with no credential now exits 1. `--backend local` also
  decoded with the OpenRouter key, and `-l auto` re-detects per file, so
  `large-v3` called English Welsh — the language is pinned once per run.
- **The word-coverage guard refused real renderings and passed broken ones over
  five rounds.** Comparing spans charged a rebuild for a music intro's silent
  seconds; comparing endpoints then passed words covering 0-30 and 270-300 of a
  300s chunk, the shape that once destroyed 83% of a two-hour file. Three terms
  now, each derived from the captured responses: longest word-free run inside a
  claim of speech (25.0s), share outside it (1.5%), word stamps per served word
  (0.95). Refusals name video time, not chunk-local seconds.
- **The scaffolded `.env` and `SKILL.md` offered
  `WATCH_OPENROUTER_PROVIDER=groq`** while the module pins `deepinfra`; both are
  now pinned to `whisper.OPENROUTER_PROVIDER` by a case.

### Notes
- watch-quality 0.5.0; see below.
- `spec/` publishes 136 rules across five files, each naming what it refuses,
  which function owns it and which cases pin it. A mutation harness breaks a
  rule's owner and requires a cited case to go red: 134 measured, 134 pinned.

## [watch-quality 0.5.0] — 2026-09-16

The plugin's own version moves separately; see 0.7.0 above.

### Added
- **`watch-audit` gives the gates one door.** Gates over notes, windows,
  anchors, citations, transcript alignment and review lanes, an aggregator
  reporting what each gate said rather than only its exit code, and a leak
  scanner refusing a published line that reproduces a private recording.
- **`wq-file-note` derives the two fields a note was given by hand.** The dated
  filename is what `note_date` reads and every dated exemption row is compared
  against, so an undated note cannot be aged. Six notes in the corpus name a
  rendering no gate can open, so the filer refuses an oracle it cannot read
  itself, and distrusts the manifest's `subtitle_path`.
- **`wq-file-review` and `wq-file-brief` own what a person used to remember.**
  The first decides where a report goes and what counts as one — the header must
  parse, name its lane and name the note — where both were checked at audit
  time, days later. The second stamps a brief with the hash of its own body,
  which the lane must quote back or its report is refused.
- **One note per video is a rule now**, because reviews and the audit sidecar
  are addressed by video id alone, so one set of reports answered for both.
- **Scans and refreshes say what they could not do.** Where a class of thing was
  not in force the summary names it; a sidecar refresh prints each repaired
  row's old and new line; the header check prints its reach, 55 of 59 excused.

### Fixed
- **The leak gate read UTF-16 as clean**, because the lossy fallback fired only
  on `UnicodeDecodeError`, which UTF-16 does not raise: a file carrying a live
  refused literal counted as scanned and printed no `# not scanned:` line.
- **The gate excused itself by file, then by resolved path.** Excusing the file
  meant a refused word in the module that quotes every refused shape could never
  be found; excusing by resolved path meant a copy of the package elsewhere —
  tarball, CI checkout, second worktree — exited 1 on its own fixtures.
- **Two gates never returned.** `Infinity` is legal JSON, so the window planner
  appended until the machine gave out — `timeout 20`, rc 124, twice, through a
  note's own oracle field. `is_relative_to` is lexical, so `<runs>/../../..`
  read as inside the corpus and the walk started wherever `..` landed.
- **Exemption rows excused notes forever, and unpinned rows never aged.**
  `excused` compared `when <= reason[:10]` as strings and every ISO date sorts
  below every letter, so `permanent` and `9999-99-99` each excused every note
  they named for good. Unpinned rows are re-measured on every run now.
- **A refusal named no literal and could not name one.** Over the fork's own
  tracked text a five-character needle has 1,253 dictionary words that match
  only after the squash removes every separator — `spec/README.md:91` was
  refused for `adjust`, a word not in the file. It names which literal it hit
  now, without printing it, on a line number `_decodings` no longer shifts.
- **Six spellings of a name were encodings, not separators.** After `%65`,
  `&#101;` or base64 the letter is not in the file, so no separator list reaches
  one. Ten codepoints had walked past that list, three of them not adversarial:
  an en dash, a full stop and a slash are how a name gets written down.
- **The anchor rule argued a moment has six renderings, then matched on the
  colon.** Eight renderings of the same two seconds exited 0: `1m13s`, `73s`,
  `1 min 13 sec`, `PT1M13S`, `?t=73`, a `U+2236` ratio colon, a right-to-left
  mark inside the stamp, and `[٠١:١٣]`. The rule reads the number now.
- **Three measurements were right only by coincidence.** A note naming
  `<repo>/docs/x.md` resolved only while the checkout's basename was that word.
  A row starting before a window and ending inside it was counted by no window.
  And an unstamped brief made the required hash echo a published constant, so a
  lane that never opened its brief could quote it.

## [0.6.2] — 2026-08-19

### Fixed
- **`E-SET-COUNT` fired on idioms and counted the rest of the note as their
  members.** A cardinality inside a claim row now enumerates only a list nested
  UNDER that row; a row's siblings are not its enumeration. Prose keeps the old
  behaviour, because a paragraph ending "three filters:" really does introduce
  the list at the left margin below it. The five false positives that argued for
  this, across three recordings: `two examples:` read as 189 members,
  `two mindsets:`
  as 134, `both ways:` as 5, `three wishes:` as 5, `two words:` as 403. Each was
  an idiom read as a declared count.
- The reason this waited two versions and then did not: the corpus reworded two
  rows to satisfy the gate the first time. A note reworded to satisfy a broken
  gate has stopped being evidence, so the third, fourth and fifth instance were
  left standing in the notes and the gate was fixed instead.

### Notes
- watch-quality 0.4.2. `resolve_note --selftest` covers the three real rows that
  triggered it plus the nested-list case that must still be counted.

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
