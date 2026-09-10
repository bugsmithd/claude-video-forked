# Mutation check — word timestamps, 2026-09-07

Every change in `fix/openrouter-word-timestamps` was reverted one at a time and
the cited tests re-run. A row with no red test is a test that would not notice
the fix breaking.

Each mutation was applied alone to a clean tree, the cited node ids were run
with `python3 -m pytest -q --no-header -p no:cacheprovider <node ids>`, and the
file was restored with `git checkout -- <path>` before the next row. The driver
asserts, after every revert, that the file is byte-identical to what it was
before the mutation; all twelve reverts passed that check, and
`git status --porcelain` afterwards reports only the untracked `.lane-briefs/`.

| # | mutation | tests that went red | verdict |
|---|---|---|---|
| 1 | `WORD_SEGMENT_MAX_SECONDS = 400.0` | `test_words_group_into_segments_a_run_can_anchor`, `test_the_near_edge_response_groups_the_same_way` | PINNED |
| 2 | drop `"timestamp_granularities"` from the `_post_openrouter` payload | `test_the_request_asks_for_word_timestamps` | PINNED |
| 3 | fallback condition `median >` becomes `median <` | `test_a_coarse_response_is_rebuilt_from_its_words`, `test_a_fine_response_is_left_exactly_as_it_came` | PINNED |
| 4 | fallback made unconditional (`if data.get("words"):`) | `test_a_fine_response_is_left_exactly_as_it_came`, `test_a_near_edge_response_is_left_alone_too` | PINNED |
| 5 | `continue` instead of appending the word that split the group | `test_every_word_survives_the_grouping_in_order` | PINNED |
| 6 | `transcribe_chunks` does not append the failed chunk to `dropped` | `test_a_failed_chunk_records_the_span_it_lost`, `test_a_run_that_lost_a_chunk_is_refused_by_name` | PINNED |
| 7 | `transcribe_video` prints the gap instead of raising | `test_a_run_that_lost_a_chunk_is_refused_by_name` | PINNED |
| 8 | `watch.py` returns 0 at both return sites | `test_a_failed_transcription_exits_non_zero` | PINNED |
| 9 | `watch.py` gates on `transcript_segments` instead of `whisper_returned_segments` | `test_a_focused_run_over_silence_is_not_a_failed_transcription` | PINNED |
| 10 | `watch.py` failure block returns 1 early, before `build_run`/`write_run` | `test_a_failed_transcription_still_writes_its_run_record` | PINNED |
| 11 | delete the `if not chunk_segments:` branch in `transcribe_chunks` | `test_a_chunk_that_returns_nothing_is_recorded_as_empty` | PINNED |
| 12 | `transcribe_video` refuses on `"empty"` as well as `"failed"` | `test_a_silent_chunk_is_named_but_does_not_refuse` | PINNED |

Twelve rows, twelve PINNED, and in every row the set that went red is exactly
the set the plan named in advance — no cited test survived its own mutation and
no mutation killed a test it was not predicted to kill.

Two notes on how the rows were applied, because the wording of a mutation and
the edit that realises it are not always the same thing:

- Row 8's anchor `return 1 if transcription_failed else 0` occurs **twice** in
  `watch.py` (the `--make-note` path and the plain path) and both were reverted
  to `return 0`, which is what "returns 0 unconditionally" has to mean for the
  mutation to be a real one.
- Row 11 was applied as `if False:` rather than by deleting the branch body,
  so the recorded span is unreachable while the surrounding line numbers stay
  put. The effect on the test is identical: nothing is appended with reason
  `"empty"`.

Rows 9 and 10 are the two defects the design refutation found in this plan's
first draft. Both go red under mutation, so the repair is pinned rather than
merely written.

## Full-suite runs

```
$ python3 -m pytest -q                     # before the mutations, at 38dc8f2
1996 passed, 6 skipped in 629.00s (0:10:28)                exit 0

$ python3 -m pytest -q                     # after reverting every mutation
1996 passed, 6 skipped in 628.57s (0:10:28)                exit 0

$ git status --porcelain                   # after reverting every mutation
?? .lane-briefs/
```

The two suite runs bracket the twelve mutations: the same 1,996 tests pass
before and after, so nothing in the mutation pass leaked into the tree.

## Round 2, 2026-09-08 — the repairs the three review lanes asked for

Six more mutations, one per repair, applied the same way: alone, to an otherwise
clean tree, with the observing node set re-run and the file restored before the
next row. The restore is from the bytes read immediately before the mutation
rather than from `git checkout --`, because these repairs were not committed yet
when the rows were run and a checkout would have reverted the fix along with the
mutation; every row's restore was checked by sha256 and all six matched.

The node set is wider than the row's own prediction on purpose: every test the
round-2 repairs added, plus `TestDroppedChunks`, `TestGappedTranscriptIsRefused`,
`TestWordGrouping` and the five `test_watch.py` exit-status tests. Running the
whole set per row is what makes "no mutation killed a test it was not predicted
to kill" checkable rather than assumed.

| # | mutation | tests that went red | verdict |
|---|---|---|---|
| 13 | `WORD_COVERAGE_SLACK_SECONDS = 100000.0` | `test_a_word_array_that_stops_early_refuses_the_chunk` | PINNED |
| 14 | record from `chunk_segments` instead of `shifted` | `test_a_window_that_keeps_nothing_is_recorded_as_lost` | PINNED |
| 15 | `_chunk_span` ignores `keeps` | `test_the_recorded_span_is_the_kept_span_not_the_decode_offset`, `test_a_window_that_keeps_nothing_is_recorded_as_lost`, `test_a_window_that_decoded_nothing_is_still_only_silence` | PINNED |
| 16 | `if alternative is not None:` restored | `test_an_empty_retry_never_replaces_a_looping_window` | PINNED |
| 17 | drop `or whisper_unavailable` | `test_a_missing_credential_is_a_failed_run` | PINNED |
| 18 | record every empty window as `failed` | `test_a_window_that_decoded_nothing_is_still_only_silence`, `test_a_chunk_that_returns_nothing_is_recorded_as_empty`, `test_a_silent_chunk_is_named_but_does_not_refuse` | PINNED |

Six rows, six PINNED, and every row's predicted test is in its own red set. Two
rows went red wider than predicted, and both widenings are the same fact seen
twice rather than a surprise:

- Row 15 also kills the two `dropped` tests on the windowed path, because a
  `_chunk_span` that ignores `keeps` reports the decode offset for every window,
  which is exactly the span those two assert on.
- Row 18 also kills the Task 4 empty-chunk test and the silent-chunk test in
  `transcribe_video`, because calling silence `failed` is what makes a healthy
  video with a quiet stretch refuse the run — the defect the `empty`/`failed`
  split exists to prevent.

How the rows were applied, where the wording and the edit differ:

- Row 14 is `if not shifted:` changed to `if not chunk_segments:`, which is what
  "record from `chunk_segments`" means at the only line where the two differ.
- Row 15 is `if keeps:` changed to `if False:` inside `_chunk_span`, so the
  `keeps` branch is unreachable while the surrounding lines stay put.
- Row 18 replaces the whole conditional `"empty" if not chunk_segments else
  "failed"` with the literal `"failed"`.

```
$ python3 -m pytest tests/test_whisper.py tests/test_watch.py -q   # before row 13
128 passed in 3.30s                                        exit 0

$ python3 -m pytest tests/test_whisper.py tests/test_watch.py -q   # after row 18
128 passed in 3.25s                                        exit 0

$ git status --porcelain                   # after reverting every mutation
 M skills/watch/scripts/watch.py
 M skills/watch/scripts/whisper.py
 M tests/test_watch.py
 M tests/test_whisper.py
?? .lane-briefs/
```

The four modified files are the round-2 repairs themselves, uncommitted at the
time the rows ran and committed in the same commit as this section.

**The full suite was NOT re-run for this round.** `python3 -m pytest -q` was
started and killed by the operator at 25% after 23 minutes: this machine was at
a load average above 16 from unrelated processes, which projects the 11-minute
suite past three hours. That is starvation rather than a failure, and it is
deferred as `DEFER:yt_notes-bnag`. The evidence above is the two touched test
files, which is narrower than the previous section's evidence.

## Scope this record does not cover

The live verification — two runs of `FIhj0yb9KPI` against the real router — is
deliberately absent. It is environment-sensitive and is run by the operator, not
by the lane that wrote this file. Nothing here is evidence about the live route;
every run above is offline.

## Round 3, 2026-09-08 — the coverage check was comparing the wrong thing

Found by running the fix, not by reading it. The corpus build of 2026-09-08 put
30 videos through this path. 29 came back clean. `YPKV-UCLLd0` was refused four
times running with identical numbers — words covering 526s of the 541s its own
segments cover, at a typical 25s — and `"temperature": 0` in the request is why
re-drawing changed nothing.

Probing the refused chunk settled it. The 15s sits entirely at the START:

| measure | value |
|---|---|
| first segment | `0.00 -> 14.88`, text `'🎵'`, **0 words inside it** |
| first word | starts `14.86` |
| last segment end | `540.57` |
| last word end | `540.49` |
| tail difference | **0.08s** — on the LIVE chunk only; see the correction below |
| loudness of `0 -> 14.86` | `mean_volume: -11.1 dB` — the show's music bed |

`segment_shape` measures coverage as `last_end - first_start`, so a music intro
that produces a segment and no words reads as 15s of dropped speech. A truncated
word array stops EARLY; it does not start late. The guard now compares the two
renderings at the tail.

Raw responses kept outside this repo at
`~/rung4-corpus/_driver/diagnose/{chunk_000-full,head-span,disputed-span}.json`;
the trimmed capture is committed as
`tests/fixtures/openrouter/music-intro.json`.

**CORRECTED 2026-09-10, three numbers in this section that a reader cannot
reproduce from the repository.** The refutation of `15393c8` measured all three.

1. **The 0.08s tail difference belongs to the live chunk, which is not
   committed.** On `tests/fixtures/openrouter/music-intro.json` — the artifact a
   reader can open — the tail difference is **1.06s**: `seg_last_end` 114.12
   against `word_last_end` 113.06. Margin against the 5.0s slack in force at the
   time was 3.94s on the fixture, not 4.92s.
2. **The fixture is trimmed AND added to, not "trimmed, nothing edited".** It
   carries two top-level blocks the response never had, `_derived` and
   `_provenance`, and `_derived["first_word_start"]` is read by
   `test_a_music_intro_is_not_a_shortfall`, so the block is load-bearing rather
   than decoration. The trim also cut the word array short of the last segment's
   text — last word `" phrase,"` at 113.06, while that segment's text runs on to
   `"computerization."` at 114.12 — which is what produces the 1.06s above.
3. **One test file was touched, not two.** `tests/test_whisper.py` alone; 108
   test definitions at `15393c8` and `python3 -m pytest -q tests/test_whisper.py`
   → `109 passed`, exit 0. The 129 in the verification table below is
   `test_whisper.py` + `test_watch.py`, which is a real number attributed to the
   wrong file set.

**SUPERSEDED 2026-09-10.** "The guard now compares the two renderings at the
tail" describes the guard as `15393c8` left it. A tail comparison accepts a word
array missing its HEAD and, like the span form before it, cannot
see a hole in the MIDDLE. The guard reads interior coverage now; the record of
that round is `.lane-briefs/2026-09-10-fix-15393c8.md`.

| # | mutation | tests that went red | verdict |
|---|---|---|---|
| 19 | restore the span comparison (`covered - rebuilt_covered`) | `test_a_music_intro_is_not_a_shortfall` | PINNED |

Run by hand with `~/rung4-corpus/_driver/mutate19.py`: restore byte-identical by
sha256, pytest exit 1, `1 failed, 104 passed`.

### Verification

| command | result | exit |
|---|---|---|
| `python3 -m pytest tests/test_whisper.py -k music_intro -q` (before the code) | `1 failed` — refused at `whisper.py:870` for the predicted reason | 1 |
| `python3 -m pytest tests/test_whisper.py tests/test_watch.py -q` (after) | `129 passed in 3.32s` | 0 |
| `python3 ~/rung4-corpus/_driver/mutate19.py` | row 19 PINNED, restore byte-identical | 0 |

### Live, and it is two runs this time

Both against the real router, `--no-captions`, on this change:

| measure | run 1 | run 2 |
|---|---|---|
| exit | 0 | 0 |
| chunks kept | 9 of 9 | 9 of 9 |
| `ABSENT` lines | 0 | 0 |
| segments | 1317 | 1689 |

The segment counts differ and that is the granularity lottery doing what it
does: each draw is served a different rendering, so the rebuild produces a
different number of anchors from the same audio. The measures that decide
whether audio was lost — exit code, chunks kept, ABSENT lines — match.

### What this round does NOT prove

No test covers the case the old check was built for arriving at the same time as
a music intro, because no such response has been captured. A word array that
both starts late AND stops early would now be caught only on its tail.

**This change has had no independent review.** It was written, tested and run by
the same session that found the defect, which is the thing every other round
here was careful not to do.

## Round 5, 2026-09-10 — one statistic could not express two failures

Answering `.lane-briefs/2026-09-10-refute-42a8053.md`, which refuted the 20.0s
slack `42a8053` shipped. The ledger below jumps from row 19 to row 20 with
nothing in between on purpose: the round that produced `42a8053` recorded its
five rows in `.lane-briefs/`, which is untracked, so this file has no entry for
them. That gap is the refutation's F8 and it is not repaired here.

### What was wrong, and it was not the number

`42a8053` summed every word-free second in a chunk and compared the total to one
slack. The refutation built a defect at ordinary scale out of the same artifact
that anchors the healthy side, and it landed inside the gap the derivation
claimed was empty. Reproduced here on the committed fixture, at a slightly
different deletion phase:

```
tests/fixtures/openrouter/coarse-reconstructed.json
  0.80s of every 1.60s of its words deleted -> 562 of 1,085 words gone (51.8%)
  -> 14.13s uncovered, under the 20.0s slack -> ACCEPTED, chunk rebuilt, exit 0
```

Half a real capture's words, and the guard reported a number smaller than the
slack. Meanwhile a theme song five seconds longer than the one in the fixtures
summed past the same slack and failed the whole video. A threshold that refuses
the second case while writing the first is not separating two populations.

### The statistic that replaced the sum

Two terms, because the two failures have different SHAPES:

- **the longest single word-free run** — what a music bed or a silent intro
  produces: one long hole and nothing else;
- **the share of the claimed speech left word-free by every OTHER run** — what a
  thinned or truncated word array produces: many holes against the whole chunk.

Either term over its threshold refuses. The share is taken outside the longest
run because a plain fraction inverts the ordering: measured over the seven
captured responses holding both arrays, the healthy `music-intro.json` leaves
**14.62%** of its claim word-free and `head-span.json` **90.31%** — both
legitimately, both the same music glyph — while a capture with half its words
thinned away leaves **1.11%**. The longest run is what the first term already
judges, so the second term reads what is left after it.

### Both thresholds, and what each rests on

Measured over every captured response on this machine holding both a segment
array and a word array — the four in `tests/fixtures/openrouter/` and the three
in `~/rung4-corpus/_driver/diagnose/` — and over defect shapes built by deleting
words from those same captures:

| population | longest run | share outside it |
|---|---|---|
| speech throughout (4 responses) | 0.02–0.22s | 0.00% |
| the `YPKV-UCLLd0` music glyph (3 responses) | 14.36s | 0.00%, 0.12%, **0.55%** |
| words start a minute late | **59.50s** | 0.00% |
| words cover only 200–300s of a 300s chunk | 199.50s | 0.00% |
| one word at the tail of a 300s chunk | 287.60s | 3.70% |
| `fine.json` thinned 0.80s of every 1.60s | 1.70s | **4.12%** |
| `coarse-reconstructed.json` thinned the same way | 1.70s | 4.38% |
| 15 constructed holes of 2.0s | 1.00s | 4.67% |

Each threshold is the **geometric midpoint** of its band — both statistics are
ratios of durations, so equal multiplicative margin is the honest middle —
**rounded down**, because the healthy side of each band rests on far fewer
artifacts than the defect side.

| constant | band | midpoint | shipped | margin |
|---|---|---|---|---|
| `WORD_COVERAGE_RUN_SECONDS` | 14.36s … 59.50s | 29.23s | **25.0s** | 1.74× the largest healthy run, 2.38× under the smallest defect |
| `WORD_COVERAGE_HOLE_SHARE` | 0.55% … 4.12% | 1.51% | **1.5%** | 2.7× either way |

Two things the run band does not hide: its healthy side is **one audio source
seen three times**, and a single word-free run between 20s and 25s used to fail
a whole video and now passes. That second one is the false refusal the sum
created, and removing it is a behaviour change rather than a side effect.

**What still passes and should not.** Word loss that INTERLEAVES rather than
cuts. Deleting every second word of a real capture leaves the survivors spread
across the whole chunk — 0.66–1.24% outside the longest run, inside the healthy
range — so no statistic over time COVERAGE can see it. Catching it needs a
density rule this file does not have (`DEFER:yt_notes-66wk`).

### The rows

Applied one at a time to an rsync copy of the working tree at `/tmp/fix42/tree`
with `.git` excluded; `skills/watch/scripts/whisper.py` in the repository under
test was never opened for writing. The driver asserts the copy is byte-identical
to the original before each mutation and restores it after, and the copy ends
the pass at the same `124 passed` it started with. Node set:
`python3 -m pytest -q tests/test_whisper.py`, the whole file per row, so "no
mutation killed a test it was not predicted to kill" is checkable rather than
assumed.

| # | mutation | tests that went red | verdict |
|---|---|---|---|
| 20 | `WORD_COVERAGE_RUN_SECONDS = 26.0` | `test_a_word_free_run_just_over_the_threshold_refuses` | PINNED |
| 21 | `WORD_COVERAGE_RUN_SECONDS = 24.0` | `test_a_word_free_run_just_under_the_threshold_is_rebuilt` | PINNED |
| 22 | `WORD_COVERAGE_HOLE_SHARE = 0.017` | `test_a_coverage_share_just_over_the_threshold_refuses`, `test_a_repeated_segment_does_not_claim_the_same_seconds_twice` | PINNED |
| 23 | `WORD_COVERAGE_HOLE_SHARE = 0.013` | `test_a_coverage_share_just_under_the_threshold_is_rebuilt` | PINNED |
| 24 | drop `if end <= start: continue` from `_merge_spans` | `test_a_backwards_rebuilt_segment_cannot_uncover_more_than_the_claim` | PINNED |
| 25 | the refusal names `holes[0]` instead of the longest hole | `test_the_refusal_names_the_longest_run_not_the_first` | PINNED |
| 26 | the run term made unreachable (`if False:`) | `test_a_hole_in_the_middle_refuses_the_chunk`, `test_a_word_array_missing_its_head_refuses_the_chunk`, `test_a_word_array_that_starts_a_minute_late_refuses_the_chunk`, `test_a_word_array_that_stops_early_refuses_the_chunk`, `test_a_word_free_run_just_over_the_threshold_refuses`, `test_one_word_at_the_end_does_not_pass_a_whole_chunk`, `test_the_refusal_names_the_longest_run_not_the_first` | PINNED |
| 27 | the share term made unreachable (`if False:`) | `test_a_coverage_share_just_over_the_threshold_refuses`, `test_a_repeated_segment_does_not_claim_the_same_seconds_twice`, `test_half_a_real_response_thinned_away_refuses_the_chunk` | PINNED |
| 28 | the share taken over the WHOLE uncovered total again | `test_a_music_intro_is_not_a_shortfall`, `test_a_word_free_run_just_under_the_threshold_is_rebuilt` | PINNED |
| 29 | `_claimed_seconds` sums raw spans instead of merging them | `test_a_repeated_segment_does_not_claim_the_same_seconds_twice` | PINNED |

Ten rows, ten PINNED. Rows 20–23 are the answer to the refutation's F11, which
found that any slack in `[14.92, 59.49]` passed the whole file: each threshold is
now pinned from BOTH sides, so a later session moving either number by one step
in either direction turns a test red. Rows 24 and 25 are F9 and F10, two clauses
`42a8053` added that nothing killed.

Row 28 is the one worth reading twice. Taking the share over the whole uncovered
total rather than outside the longest run turns `test_a_music_intro_is_not_a_shortfall`
red — that is the ordering inversion above, reproduced as a mutation.

### Verification

| command | result | exit |
|---|---|---|
| `python3 -m pytest -q tests/test_whisper.py -k TestWordCoverageIsTwoTerms` (before the code) | `1 failed` — `DID NOT RAISE SystemExit`, the predicted reason | 1 |
| `python3 -m pytest -q tests/test_whisper.py` (GATE, run 1) | `124 passed in 0.71s` | 0 |
| `python3 -m pytest -q tests/test_whisper.py` (GATE, run 2) | `124 passed in 0.67s` | 0 |
| `python3 -m pytest -q` (full suite, once) | `2021 passed, 6 skipped in 656.78s` | 0 |
| `python3 /tmp/fix42/mutate.py` | ten rows, all PINNED, copy restored and re-verified `124 passed` | 0 |
| `python3 /tmp/fix42/derive.py` | the two population tables above | 0 |

### What this round does NOT prove

No live transcription ran; this route needs network and a key, and the
two-matching-runs bar for an environment-sensitive route is the operator's.
Every defect shape above is constructed or built by deleting words from a real
capture — a truncated or thinned word array has still never been captured from
this endpoint, in either direction. The healthy side of the run threshold is
still one audio source.

## Round 6, 2026-09-10 — both terms read time, so a third reads words

Implementing the design frozen at user FINAL SAY on 2026-09-10 and recorded in
the project ADR under `DECISION 2026-09-10`. The measurements behind it are
`.lane-briefs/2026-09-10-design-coverage-statistic.md`, sections
`WHAT I RECOMMEND` and `DETECTION FLOOR`. This round adds one term; it replaces
nothing.

### What was wrong, and again it was not the number

Round 5 split one summed slack into a longest word-free run and a share of the
claim outside it. Both terms read TIME. A decode that THINS rather than
truncates keeps the time covered while the words go, so moving round 5's own
deletion window one step walks straight past both:

```
tests/fixtures/openrouter/coarse-reconstructed.json
  0.80s of every 1.60s deleted -> 562 of 1,085 words gone
     -> run 1.70s, share 4.38%  -> REFUSED   (round 5's regression test)
  0.60s of every 1.60s deleted -> 423 of 1,085 words gone
     -> run 1.45s, share 1.25%  -> ACCEPTED, 142 segments written
```

Three rounds in a row now, the shipped guard has refused the case its own test
names and accepted the same shape one window away from it.

### The term this round adds

A rendering too coarse to ANCHOR still says how many words it HEARD, in its own
text. So the word array is graded against that count and not against the clock:

> Refuse the rebuild when `len(data["words"]) < 0.95 × sum(len(seg["text"].split())
> for seg in out)`.

Both inputs were already in hand at the call site. The run term and the share
term are kept, because the run term catches the late-start shape the ratio
cannot see — `coarse-reconstructed.json` with its first minute of words removed
leaves 844 words at a ratio of 1.1722, which the ratio accepts and the run term
refuses. Verified by hand this round, not inferred from a test name.

**It is a filter, not a separator, and that is written beside the constant.** A
design lane measured 23 candidate statistics over 828 constructed defect rows
and found every band touching; three of the seven captures holding both arrays
share one audio source, so the healthy side was never a population. 0.95 is the
last setting whose false-positive margin (5.72% on the real `music-intro.json`
capture) still exceeds its own detection floor (5.78%).

### The declared floor

| capture reaching the branch | baseline | blind below | in words | in seconds |
|---|---|---|---|---|
| `diagnose/chunk_000-full` (1,462 words of served text, 502.0s claimed) | 1.0083 | 5.78% of words | 85 | 29.0s |
| `fixtures/music-intro` (229 words of served text, 102.1s claimed) | 1.0044 | 5.41% of words | 12 | 5.5s |

Below that, the term sees nothing, and a defect constructed underneath it is the
design working as written rather than a refutation of it. What round 5 shipped
has a floor of 32.1% — 469 words.

### The rows

Applied one at a time to an rsync copy of the working tree at `/tmp/fix95/tree`
with `.git` excluded; `skills/watch/scripts/whisper.py` in the repository under
test was never opened for writing. The copy is asserted byte-identical to the
original before each row and restored after it, and the pass ends at the same
`129 passed` it started with. Node set: `python3 -m pytest -q tests/test_whisper.py`.

| # | mutation | tests that went red | verdict |
|---|---|---|---|
| 30 | `WORD_COVERAGE_MIN_WORD_RATIO = 0.97` | `test_a_word_count_just_over_the_line_is_rebuilt` | PINNED |
| 31 | `WORD_COVERAGE_MIN_WORD_RATIO = 0.93` | `test_a_word_count_just_under_the_line_refuses` | PINNED |
| 32 | the word-count term made unreachable (`if False and …`) | `test_a_capture_thinned_one_window_further_refuses_the_chunk`, `test_a_single_stamp_spanning_the_chunk_refuses_it`, `test_a_word_count_just_under_the_line_refuses` | PINNED |
| 33 | the whole clause deleted, comment and all | the same three | PINNED |

Four rows, four PINNED. Rows 30 and 31 are the both-sides pin the GATE asks for:
a later session moving the constant one step in either direction turns a test
red. Row 33 is the GATE's own check — a clause whose deletion leaves the suite
green is not done, and three such clauses shipped in this file a week ago.

**A mutation driver that reuses `.pyc` files reports the previous row's
verdict.** Two settings of the same constant differ by neither file size nor,
inside one second, mtime, so CPython served row 31 the module it compiled for
row 30 and the pass printed row 30's red test twice. The driver now runs with
`PYTHONDONTWRITEBYTECODE=1`. Any future pass over a numeric constant needs the
same.

### Verification

| command | result | exit |
|---|---|---|
| `python3 -m pytest -q tests/test_whisper.py` (before the code) | `124 passed` | 0 |
| `python3 -m pytest -q tests/test_whisper.py::TestWordCountAgainstServedText` (tests written, code not) | `5 failed` — three `DID NOT RAISE SystemExit`, two `AttributeError: module 'whisper' has no attribute 'WORD_COVERAGE_MIN_WORD_RATIO'` | 1 |
| `python3 -m pytest -q tests/test_whisper.py` (GATE, run 1) | `129 passed in 0.59s` | 0 |
| `python3 -m pytest -q tests/test_whisper.py` (GATE, run 2) | `129 passed in 0.57s` | 0 |
| `python3 -m pytest -q` (full suite, once) | `2026 passed, 6 skipped in 565.14s` | 0 |
| `python3 /tmp/fix95/mutate.py` | four rows, all PINNED, copy restored and re-verified `129 passed` | 0 |

### What this round does NOT prove

No live transcription ran. Every defect shape is still constructed or built by
deleting words from a real capture, so the defect population remains
hypothetical exactly as it was in rounds 3 and 5.

**One test the frozen design asked for is not here, and the reason is
arithmetic.** The brief named `coarse-reconstructed.json` thinned at 0.45s of
every 1.60s — 321 of 1,085 words gone — as a case that must REFUSE. It does not,
and cannot at 0.95. That fixture is a RECONSTRUCTION whose placeholder segment
text holds 720 words against 1,085 stamps, a baseline of 1.5069, so the term is
blind there to any loss under 37% — 401 words. 321 is under it; 423 (the case
above) is over it, which is why the test that ships is the one that ships. The
design report states this blind spot itself. The threshold was not moved.

**`coarse-reconstructed.json` should not be the fixture pinning a term that
reads segment TEXT.** Its text is twelve identical 60-token placeholder blocks
and its words belong to a different chunk. A real coarse response with its text
intact is worth more than another threshold: `DEFER:yt_notes-66wk`, widened to
save the served text and not only the word array.

## Round 7, 2026-09-10 — the term counted entries the rebuild throws away

A refutation of round 6 confirmed the finding it was pointed at, on a real
capture rather than a construction. Report:
`.lane-briefs/2026-09-10-refute-32fd8ae.md`, finding A.

### What was wrong

The new term's numerator was `len(data["words"])` — the length of the array the
response carried. `segments_from_words` drops an entry with no text or no usable
times, deliberately and by its own docstring, so one malformed word never costs
the chunk it sits in. The term never saw that drop.

So an array padded with droppable entries read full length at the line while the
transcript came out short by every one of them, and the two time terms saw
nothing either, because each dropped word's second is still covered by its
neighbours once spans are widened. On `~/rung4-corpus/_driver/diagnose/chunk_000-full.json`
— a real capture, 1,462 entries against 1,450 words of served text — 85 blanked
entries, 5.81% and above that capture's own 5.78% declared floor, were accepted
and 1,377 words written. It was not bounded by the floor: 292 entries (19.97%)
and 731 entries (50.00%) were accepted too.

### The repair

Count what the rebuild writes rather than what the array carries:
`rebuilt_words = sum(len(seg["text"].split()) for seg in regrouped)`. `regrouped`
is computed ten lines above the guard and is what the function returns, so the
count now measures the transcript that will be written. The threshold did not
move and the design was not reopened.

Verified against the same real capture, read-only: 85, 292 and 731 blanked
entries all REFUSE, each naming the word count it would have written.

### What this round does NOT repair, and it is filed rather than fixed

**A looping decode.** Every entry valid, the array length unchanged, the times
still covering every second the segments claim, and real speech replaced by
repeats of its neighbours: 731 of 1,462 words replaced is accepted and 1,462
words are written. All three terms measure DELIVERY, not distinctness, so none of
them can see substitution. Detecting it needs a different signal. Filed as
`yt_notes-o182`.

### The rows

Same driver and same discipline as round 6, on an rsync copy at `/tmp/r4/tree7`
with `.git` excluded, `PYTHONDONTWRITEBYTECODE=1`, the copy asserted
byte-identical before each row and restored after it. Node set:
`python3 -m pytest -q tests/test_whisper.py`.

| # | mutation | tests that went red | verdict |
|---|---|---|---|
| 34 | numerator back to `len(data["words"])` | `test_entries_the_rebuild_discards_do_not_count_toward_the_line` | PINNED |
| 35 | the whole word-count clause deleted | that one plus `test_a_capture_thinned_one_window_further_refuses_the_chunk`, `test_a_single_stamp_spanning_the_chunk_refuses_it`, `test_a_word_count_just_under_the_line_refuses` | PINNED |

Row 34 is the one that matters: it is the defect this round repairs, and before
the repair the suite was green with it in place.

### Verification

| command | result | exit |
|---|---|---|
| `python3 -m pytest -q tests/test_whisper.py -k entries_the_rebuild_discards` (test written, code not) | `1 failed` — `DID NOT RAISE SystemExit` | 1 |
| `python3 -m pytest -q tests/test_whisper.py` (GATE, run 1) | `130 passed in 0.66s` | 0 |
| `python3 -m pytest -q tests/test_whisper.py` (GATE, run 2) | `130 passed in 0.65s` | 0 |
| `python3 -m pytest -q` (full suite, once) | `2027 passed, 6 skipped in 642.01s` | 0 |
| `python3 /tmp/r4/mutate_round7.py` | two rows, both PINNED, copy restored and re-verified `130 passed` | 0 |

### What this round does NOT prove

No live transcription ran. The three shapes finding A used — text blanked, start
nulled, entries repeated — are all constructed; no capture on this machine
carries a text-less or time-less entry, measured 0 of 5,843 entries across nine
artifacts. The rebuilder's own docstring says the shape is expected in the wild,
which is why it is repaired rather than argued with.
