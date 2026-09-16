# MINORS

Below-the-bar findings from reviews of this repository. Each row is not a blocker and did not earn a fix round. Fix one when you are already in that code, or promote it to a bead. A row tagged CLOSED stays for the record; its tag says why no fix is owed. Untagged rows are still open.

## whisper word count over the claim (commit `c797b4a`, 2026-09-15, bead `yt_notes-3y4c`)

- **CLOSED 2026-09-15, style only.** `skills/watch/scripts/whisper.py:988`, `:1079`: the word list is filtered twice per coarse chunk, once inside `segments_from_words` and once for the count; a duplicated pass, not duplicated logic.
- **CLOSED 2026-09-15, no wrong result.** `tests/test_whisper.py:1834`: the precondition measures array entries, not rebuilt words, while its message says "the whole rebuild must read full"; equal here because every fixture word is usable.
- **CLOSED 2026-09-15, fixed.** `tests/test_whisper.py:1822`: the new test does not pin the inside count (1,002); a wider count that still refuses would still pass. The test now asserts `would write 1002 words inside` and `holds 1260 words`.
- **CLOSED 2026-09-15, by design.** `skills/watch/scripts/whisper.py:1211`: any number of entries stamped within 0.5s of a claim edge are counted, and a word inside the claim with a span running far outside counts too; the brief's Boundary names this.
- **CLOSED 2026-09-15, no wrong result.** `skills/watch/scripts/whisper.py:1210`: one entry whose text holds many tokens counts every token; the old count split rebuilt text the same way.

## E-WIN-OVERFULL by segment span (commit `8d7ef2f`, 2026-09-16)

- **CLOSED 2026-09-16, fixed at `e135dda`.** `spec/evidence-witness.toml:453`: the `WIN-OVERFULL` `refuses` text still says "more than half the segments". It was already stale at `ad0573b`, which ran twice an even split.
- **CLOSED 2026-09-16, fixed.** The test is renamed `..._is_under_the_ceiling` and the spec row names the 90% ceiling. `spec/evidence-witness.toml:466` and `tests/test_spec_evidence_witness.py:1294`: the neighbour case and test name say exactly half is "on the ceiling". The ceiling was not one half at `ad0573b` either, and now it is 0.9.
- **CLOSED 2026-09-16, fixed.** The comment and the docstring now each name their baseline: the rest of the recording, or its whole average. `watch-quality/src/watchquality/note_windows.py:84`: the comment gives the corpus course 2.2 times the density of the rest. The test docstring at `tests/test_spec_evidence_witness.py:1532` gives 2.1 times the average. Both can be right, because they measure against different things, but a reader will see a contradiction.
- **CLOSED 2026-09-16, fixed.** Both docstrings now say "the removed even-split share rule". `tests/test_spec_evidence_witness.py:1533` and `:1564`: both docstrings name `overfull_share`, which no longer exists. They describe past behaviour, so nothing breaks, but a search for the name now finds only these two.

## E-WIN-OVERFULL stacked-stamp arm (commit `e135dda`, 2026-09-16)

- **CLOSED 2026-09-16, fixed.** A plan refused only by the stacked-stamp arm now prints the seconds its stamps claim against the seconds they reach and says they are stacked; the set of refused plans is unchanged. `watch-quality/src/watchquality/note_windows.py`: a plan refused by the stacked-stamp arm (for example three or more segments stamped across a whole recording that did split) prints "the plan says it split the video and the numbers say it did not", which is the wrong diagnosis; the refusal itself is right. Tests pin the message prefix, so a reword must update them.

## Thinned OpenRouter request spans (commit `a754d66`, review 2026-09-16, bead `yt_notes-sfrs`)

- `skills/watch/scripts/whisper.py:1995-1996`: retries widen a 600 s request to 660 s and then 720 s, past `OPENROUTER_MAX_SECONDS`; not measured against the provider's 60 s processing timeout.
- `skills/watch/scripts/whisper.py:2009-2010`: the retry trim keys on segment start only, so seam words can be duplicated after the span end or dropped before its start; `drop_seam_repeats` is not applied.
- `skills/watch/SKILL.md:1`: frontmatter version reads 0.7.2 while `plugin.json` reads 0.7.4; the drift predates this commit.
- `skills/watch/scripts/whisper.py:1978`, `:2001`: `audio_duration` and the retry `split_audio` sit outside the per-attempt catch, so an ffmpeg failure there ends the run instead of counting as a failed attempt.
- `skills/watch/scripts/whisper.py:1799-1800`: a second model that hallucinates 100 or more words over silence can refuse a healthy run; only replay evidence (0 of 43 healthy spans flagged) exists.
- `tests/test_whisper.py:1968`: no test tells retry words from second-decode words directly; filling a span from the second decode is caught only by retry counts.
- ADR mirror `claude-video-forked.md`, TRADEOFFS thinning entry: the ruling says "a shifted start", while the code and CHANGELOG widen both sides; align the wording at the next ADR touch.
- `skills/watch/scripts/whisper.py:1974`: no live run of the re-cut path yet; the route is environment-sensitive and needs two matching runs.

## One language per OpenRouter run (review of `c56ac3c..cbc6a4d`, 2026-09-16, bead `yt_notes-glea`)

Review minors 3 and 6 are closed by the language-normalising fix and are not listed.

- 2026-09-16, `yt_notes-glea`. `skills/watch/scripts/whisper.py:1726-1728`: a wrong first detection spreads to the whole run, both decodes included, and a chunk with no segments (music) still pins its language. Needs an operator ruling on whether only a chunk with segments may pin.
- 2026-09-16, `yt_notes-glea`. `skills/watch/scripts/whisper.py:848`: a configured `WATCH_OPENROUTER_LANG` is still sent as typed, with no check that it is a code the endpoint accepts. The detected pin is now normalised to a code; the configured value is not.
- 2026-09-16, `yt_notes-glea`. `skills/watch/scripts/whisper.py:1938`: no test covers the per-run reset of the pin; removing it passes every test. Older tests leave the pin set, so direct `_transcribe_file` tests can depend on order.
- 2026-09-16, `yt_notes-glea`. `skills/watch/scripts/whisper.py:2124-2126`: a thin-span retry that comes back in another language counts as a failed cut, so the refusal says "kept too few words" instead of naming the language.
- 2026-09-16, `yt_notes-glea`. `skills/watch/scripts/setup.py:81-82`: the comment says "the first request's detected language"; the code pins the first successful response that carries a readable language, which may come later.
- 2026-09-16, `yt_notes-glea`. `skills/watch/scripts/whisper.py:2019`: with a single request and a wrong `WATCH_OPENROUTER_LANG`, the refusal is the raw `LanguageMismatch` text, not the run-level message.

## Language from video metadata (review of `7d39cf4..2f4e992`, 2026-09-16)

- **CLOSED 2026-09-16, ruled.** The minor above about a wrong first detection spreading to the whole run is settled by 0.7.6: the video's declared language now comes before detection. Whether the local whisper.cpp path should use it too is `yt_notes-vhpz`.
- 2026-09-16. `skills/watch/scripts/whisper.py:1433-1434`: a macrolanguage subtag collapses to its primary subtag, so `zh-yue` normalises to `zh` for both the seed and the mismatch compare, although Whisper's table lists `yue` separately.
- 2026-09-16. `skills/watch/scripts/whisper.py:1755-1757`: when every request of the first decode returns an unrecognised label, a later recognised detection still prints "detected by the first request".
- 2026-09-16. `skills/watch/scripts/whisper.py:1499`: the config source line prints the raw `WATCH_OPENROUTER_LANG` value (`'English'`), while the metadata line prints a normalised code.
- 2026-09-16. `skills/watch/scripts/whisper.py:1502-1506`: an unreadable metadata value (for example `xx-Nowhere`) falls through to detection without stderr naming the ignored value.
- 2026-09-16. `tests/test_whisper.py`: `test_no_usable_metadata_falls_back_to_detection` and `test_the_local_path_keeps_its_configured_language_first` were not mutation-checked in review.
