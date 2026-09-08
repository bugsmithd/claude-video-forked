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
