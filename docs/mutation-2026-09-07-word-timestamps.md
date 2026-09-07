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

## Scope this record does not cover

The live verification — two runs of `FIhj0yb9KPI` against the real router — is
deliberately absent. It is environment-sensitive and is run by the operator, not
by the lane that wrote this file. Nothing here is evidence about the live route;
every run above is offline.
