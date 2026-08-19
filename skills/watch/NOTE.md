# NOTE.md — what a note has to be, when `--make-note` was used

Read this instead of Step 4 of `SKILL.md`. Step 4 answers a question; this
writes an artifact. The difference is not length. It is that **every claim here
has to stay checkable after the conversation is gone**, and a script will check
it.

`watch.py --make-note` has already done its half: the run directory is kept, the
frames are wider, the seconds the dedup pass collapsed are recorded rather than
discarded, and `run.json` holds a digest per frame, the source digest, and the
list of seconds the transcript actually starts on. Your half is the note.

## The four rules a gate enforces

**1. An anchor is a second the transcript starts on.** Not a rounded one, not an
interpolated one, not the middle of a long segment. `run.json` lists every legal
one under `transcript.segment_starts`. A stamp that is not in that list resolves
to nothing and the claim is deleted by a script that cannot ask what you meant.

    - `[04:32]` `SPOKEN` — the claim, in your words, one sentence or two

**2. Every claim carries an evidence class, and the class is a promise.**

- `SPOKEN` — it was said. A quoted string on a `SPOKEN` row must be **verbatim**
  from the transcript, word for word.
- `ON-SCREEN` — it was visible in a frame. The anchor must be a second a frame
  exists at, which is `run.json` → `frames[].seconds`.
- `INFERRED` — your reading. The recording does not state it. This class is the
  one that goes wrong: an `INFERRED` row sitting beside a `SPOKEN` one quietly
  upgrades it. If the speaker was unsure, the row says he was unsure.

**3. A hedge is part of the claim.** "Maybe part of it is" does not become "the
real formula is". A number offered as acceptable does not become the number
complained about. This is the single most common defect found by reading notes
against their recordings, and it is invisible to every mechanical check.

**4. Do not name who is speaking unless the words settle it.** A transcript
labels no voices. Where two people are talking, a claim given to the wrong one
is the most damaging error a note can carry and no gate can see it. Write the
claim, not the speaker. Name one only where the recording itself settles it
("I said to you last year", "you made that point earlier") and say what settled
it.

## The three rules a reader enforces

**Declare a count only if you list that many.** "The four filters" over three
bullets is a partial enumeration presented as a whole set, and it reads as
complete. Prefer to list and let the count follow. If a checker is available for
your note format, let it derive the number rather than writing one.

**Say what you did not write.** Close with the things you saw and deliberately
left out, each with its timestamp and one clause of why. A note that claims to
have covered a busy ten minutes and lists nothing omitted is not credible.

**One argument is one row.** A claim, its mechanism and its example belong
together. Splitting one argument across six rows to look thorough is measurable
and is marked down — though it is a genuine trade: rules against splitting push
toward reproducing sentences instead, and buy concision and quoting at some cost
in coverage.

## Reading the frames

Read every frame path **in batches of at most 20**, each batch one message whose
file names sit beside its own images. Do not open a hundred frames in one
message and pair them with a list afterwards; that is how a whole batch shifts
by one and nobody notices. Each frame states its own second in its pixels, so a
mis-pairing is visible rather than inferred — if a burned-in `t=` disagrees with
the path you think you are reading, trust the pixels and say so.

`run.json` → `deduped_seconds` lists seconds that existed and were collapsed
into a neighbouring frame as near-identical. A held slide is the usual case: it
was on screen across all of them even though one frame survives. You may say so;
you may not anchor an `ON-SCREEN` claim at a second with no frame.

## Then check it

    watch-audit <note.md>

Exit 0 means every gate passed. Exit 1 means a gate found defects and they are
printed above it. **Exit 2 means a gate could not run at all, which is not a
pass** — fix the install before believing anything.

Fix what the gates find before you report the note as done. A defect list you
have read and not acted on is worse than no gate, because the next reader
assumes the exit code was 0.

## What none of this buys you

The gates check that anchors resolve, that quotes are verbatim, that classes are
present and that counts match their lists. **They cannot tell whether the note
is right.** A wrong attribution, a dropped hedge and a confidently stated
inference all pass every one of them.

That is what a second reader is for. `REVIEW.md` beside this file is the
template: three fresh readers who cannot see each other, refute-by-default,
frames in bounded batches, and a repair policy that stops a review from
smuggling in claims nobody checked. It is opt-in — ask the user before spending
three agents on it.
