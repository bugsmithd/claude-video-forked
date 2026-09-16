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
