# REVIEW.md — three readers who did not write the note

Optional. Read this only when the user asks for a review, or when the note is
going somewhere it will be trusted without being re-derived.

`watch-audit` proves a note is well-formed. It cannot prove the note is right: a
wrong attribution, a dropped hedge and a confidently stated inference pass every
gate there is. The only thing that catches those is a reader who did not write
the note — and more than one, because a single reader agrees with whatever they
read first.

**This is a dispatch template, not an automatic step.** Nothing here runs
without the user asking. Three fresh agents is a real cost and it is theirs to
approve.

## Build the briefs

```bash
python3 "${SKILL_DIR}/scripts/review.py" <run.json> <note.md>
```

Writes `<run-dir>/review/` — one brief per lane, plus a `DISPOSITION.md`
skeleton. The briefs are built from `run.json`, so each carries the real frame
paths, the legal anchor seconds, and the seconds that were collapsed.

## The three lanes

| lane | asks | reads |
|---|---|---|
| **facts** | Is each claim TRUE against the recording? | transcript + frames |
| **quality** | Is each claim carried HONESTLY? | the note itself |
| **coverage** | What did the note LEAVE OUT? | the recording, forward |

They are separate because they fail differently. A facts lane checks what is on
the page and will never notice what is absent from it; a coverage lane working
from the recording forward finds exactly that and has no opinion about whether a
hedge survived. Running one lane and calling it a review buys you its blind
spot.

## Four rules for dispatching them

**One agent per lane, in parallel, each with ONLY its own brief.** A lane that
can see another lane's findings agrees with them, and three agreeing lanes look
like corroboration when they are an echo.

**Fresh context each.** A reviewer who watched the note being written is not a
second reader. If the note was written in this session, the lanes have to be
separate agents, not you re-reading.

**Refute by default.** Each brief carries the stance; hold the lanes to it. The
job is not to produce findings, it is to try to break the note and report only
what broke. Twenty plausible findings are worth less than three certain ones,
because every finding costs a human a re-read and a wrong one costs them their
trust in the rest.

**Prepend whatever contract your harness needs** — output limits, tool routing,
whatever your setup requires of a subagent. This template deliberately carries
none of that, because it is not the same everywhere.

## Reading frames in bounded batches

`review.py` splits the frames into batches of at most 20 and puts them in the
brief that way. This is not tidiness. A lane that opens a hundred frames in one
message and then works from a separate list of file names is pairing images with
names **by position**, and one dropped frame shifts the whole tail by one with
nothing to notice it. Each batch is small enough that its names sit beside its
own images, and every frame states its second in its own pixels as a second
check.

## Then dispose of what comes back

Fill in `DISPOSITION.md`: one row per finding, verdict by the author. The lanes
report; they do not decide.

**The repair policy, which is the load-bearing part.** A repair may strike a
false statement, restore a dropped hedge, re-point a wrong anchor, flag an
unflagged recogniser artefact, or delete a row that is not a claim.

**It may not write a claim the rows do not carry.** A coverage lane reporting
something missing is reporting a gap. Filling that gap means going back to the
recording and writing a new row with its own anchor — which is authoring, not
repairing, and belongs in its own pass where it can be gated like anything else.
Repairing and authoring in the same pass is how an unchecked claim enters a note
wearing the credibility of a review.

**Where two lanes disagree, go to the recording.** Opposite conclusions about
one passage are information, not a tie to break by majority. Settle it at the
source and write down which lane was wrong and why; that is the only thing that
makes the next set of briefs better.

## Re-run the gates afterwards

Repairs move anchors and delete rows, so:

```bash
watch-audit <note.md>
```

A note that passed before a repair pass has not passed after one.
