# MINORS

Below-the-bar findings from reviews of this repository. Each row is not a blocker and did not earn a fix round. Fix one when you are already in that code, or promote it to a bead.

## whisper word count over the claim (commit `c797b4a`, 2026-09-15, bead `yt_notes-3y4c`)

- `skills/watch/scripts/whisper.py:988`, `:1079`: the word list is filtered twice per coarse chunk, once inside `segments_from_words` and once for the count; a duplicated pass, not duplicated logic.
- `tests/test_whisper.py:1834`: the precondition measures array entries, not rebuilt words, while its message says "the whole rebuild must read full"; equal here because every fixture word is usable.
- `tests/test_whisper.py:1822`: the new test does not pin the inside count (1,002); a wider count that still refuses would still pass.
- `skills/watch/scripts/whisper.py:1211`: any number of entries stamped within 0.5s of a claim edge are counted, and a word inside the claim with a span running far outside counts too; the brief's Boundary names this.
- `skills/watch/scripts/whisper.py:1210`: one entry whose text holds many tokens counts every token; the old count split rebuilt text the same way.
