# watch-quality

Eleven commands that refuse to let a note claim more than its evidence supports.

They are built for notes made from video — a `/watch` run, or anything that
produces a transcript, a set of extracted frames, and a note that cites seconds
in them. The checks ask whether the note's claims survive contact with what the
run actually captured.

## What each gate answers

| Command | The question it answers |
|---|---|
| `wq-resolve-note` | Does every quoted citation still say what it said when it was resolved, and is the declared density the real one? |
| `wq-anchor-manifest` | Does every second a note cites exist in the run, and is it an extracted frame or a caption span? |
| `wq-spoken-vote` | Do the captions carry a textual witness for the anchors tagged `SPOKEN`? |
| `wq-ocr-vote` | Do the pixels corroborate a proper noun the audio asserted, and is an `ON-SCREEN` claim witnessed on screen? |
| `wq-demote-note` | Which claims have lost their evidence and should be marked as such? |
| `wq-say-captions` | What was actually said around this second? (the tool you reach for when a gate disagrees with you) |
| `wq-frame-fixture` | Build frame fixtures for the manifest tests. |
| `wq-policy` | Which policy file is in force, and what does it say? |
| `wq-transcript-align` | Did this transcript decode survive, and where does a second decode of the same audio disagree with it? |
| `wq-note-windows` | Which stretches of a long video is a note written from, and where do they overlap? |
| `wq-corpus-scan` | Has any corpus data leaked into this package's own source? |

Three principles the checks are built on, because they explain the refusals:

- **Absence is never evidence.** A check that cannot find a witness reports that
  it could not, and never reports that the claim is false.
- **A witness class votes once.** Captions and speech-to-text are both
  text-from-audio, so they are one witness, not two. The pixels are the second.
- **An exemption is dated, printed and may only shrink.** Nothing is quietly
  excused; every run prints what it skipped and why.

## Install

```
pip install watch-quality
```

Python 3.11 or newer. No dependencies — the gates run on the standard library,
so a corpus can be graded on a machine with nothing else installed.
`wq-ocr-vote --index` additionally wants `tesseract` on `PATH`, and reads image
sizes through Pillow when it is present (`pip install 'watch-quality[ocr]'`);
both are optional, and their absence is reported rather than assumed away.

## Point it at your notes

The package holds no facts about any corpus. Paths, exemptions and the corpus
root all come from a `watch-quality.toml` beside the notes being graded:

```toml
notes_dir = "notes"
runs_root = "~/watch-runs"
search_roots = ["~/watch-runs", "/tmp"]

# Runs whose evidence cannot be reconstructed. Every entry must start with an
# ISO date and a reason; the list is printed on every run and may only shrink.
[unresolvable_runs]
"dQw4w9WgXcQ" = "2024-01-31 the frames for this run no longer exist; captions do"
```

Resolution order, first hit wins: `$WATCH_QUALITY_POLICY`, then
`watch-quality.toml` in the working directory or any parent. The corpus root is
`$WATCH_QUALITY_ROOT` if set, otherwise the directory holding the policy file.

**With no policy file at all the exemption tables are EMPTY.** A missing policy
can never invent an exemption, and an entry that does not start with an ISO date
is rejected rather than ignored.

## Run the checks

```
wq-resolve-note --check
wq-anchor-manifest --check
wq-spoken-vote --check
wq-ocr-vote --check
wq-demote-note --diff
```

Exit 0 is clean, 1 is defects found, 2 is a usage or IO error. Each gate prints
its defects on stdout and its census — counts, denominators, exemptions — on
stderr, so `2>/dev/null` gives you a work queue and `>/dev/null` gives you the
summary. The census is deliberately loud: "0 defects" over 24 notes means
nothing without knowing how many of them were testable.

## Before any of that: did the transcript survive?

Every check above grades a note against a transcript. None of them can tell you
the transcript is wrong. If the decode collapsed, the anchors still resolve, the
quoted words still match the file, and the file is fiction.

```
wq-transcript-align turbo.json large-v3.json --duration 3600
```

It answers two questions. **Did either decode degenerate?** — a run of identical
segments (`E-TS-LOOP`), a stretch with no segments in it (`E-TS-GAP`), a
rendering that stops early (`E-TS-SHORT`), a stuck decoder repeating a handful of
texts (`E-TS-REPEAT`). **Where do two decodes of the same audio disagree?** —
whole-file token alignment, the ratio, and the divergent passages largest first,
each anchored to a second.

Run it on two renderings, from two models. One is `E-TS-SINGLE-WITNESS`: a lone
decode has nothing to be checked against. But two models are **not** two witness
classes — both are speech-to-text, and they have agreed with each other and been
wrong together. The alignment measures *stability*; instability is a reason to
distrust a passage, and agreement is never on its own a reason to trust one.
Which model is the better one is a per-file question, decided from the alignment
and never inherited from the last file: the model trusted on one recording was
the one that collapsed on the next. That is one reversal, not a rate — enough to
stop inheriting a choice, not enough to predict which model fails next.

Accepts WebVTT, whisper.cpp `-oj` JSON, Whisper `verbose_json`, or a bare list of
`{start, end, text}`, so the renderings need no conversion step to compare.

## Writing a long video in windows

A single pass over a two-hour transcript compresses the middle hardest, and does
it invisibly: partial lists arrive presented as whole ones, and a stretch that
carried three anecdotes comes back as a sentence. Writing it in windows fixes
that only if the boundaries are not themselves a compression nobody can see —
so they come from a script and two numbers, never from a model.

```
wq-note-windows turbo.json                       # 10-minute windows, 90s overlap
wq-note-windows turbo.json --chapters video.info.json
```

Windows here **overlap on purpose**, which is the opposite of the decode windows
above. A decode discards its overlap: two copies of a sentence in one transcript
is a defect. A note window keeps it, so a claim made across a seam is written
twice and reconciled once at merge — two windows that never see the same words
have a seam nobody read.

`--chapters` prefers the uploader's own marks where they exist, and says so on
stderr when the file declares none rather than quietly reverting to arithmetic.
Boundaries snap to the transcript's segments, so `--json` gives a plan anyone can
re-derive and check an outline against.

## Which build passed this note

"0 defects" is a claim about a moment. Without a record of *which* moment, it is
a claim about an unknown one: tighten a check, move a threshold, fix a bug, and
every note keeps its old clean bill of health with nothing saying so.

```
wq-resolve-note --stamp
```

writes `graded_with: watch-quality@0.3.0` into the frontmatter of every note
that is clean **at that moment**. A note with outstanding defects is refused
(`E-STAMP-REFUSED`), never stamped — a stamp on a red note would read months
later as "this version passed it", which is the exact false light the gates
exist to remove.

Thereafter `--check` reports `E-GRADE-UNSTAMPED` (nothing says which build
passed this) and `E-GRADE-STALE` (a different version is installed than the one
that passed it). Stale is not wrong; it is out of date, and saying so is the
difference between comparable and quietly incomparable.

Stamping writes to notes, and notes cite each other by content hash, so a stamp
invalidates the recorded sha of any note citing the stamped one. `--stamp`
repairs exactly those rows between rounds and nothing else — refreshing
everything would repair staleness you have not been shown yet.

## Citing a source, and citing a version of this package

A note cites evidence with a token that names the file and quotes the words:

```
{{CITE:docs/strategy.md#"we sell to people who have already decided"}}
```

`wq-resolve-note --write` resolves the quote, renders it, and records the file,
the line, a sha256 and the quote in a sidecar. A later run re-checks the sha, so
an edit to the cited document is reported rather than silently tolerated.

Code can be cited the same way, but a **path** is the wrong address for it:

```
{{CITE:watch-quality@0.1.0:ocr_vote.py#"TEXTFUL_WORDS = 5"}}
```

The version is part of the address on purpose. A citation resolved against
0.1.0 **refuses** to resolve against 0.2.0 (`E-CITE-VERSION`) rather than
quietly resolving to different code — the quote may well still be present in the
new release, at a new line, meaning something else. Not installed is
`E-CITE-NOPKG`, which says the claim cannot be checked here, not that it is
false. The distinction is the whole point: a re-grade months later is either
comparable, or loudly not comparable.

## Tests

Every module carries its own:

```
wq-resolve-note --selftest      # ...and the same for each command
wq-transcript-align --selftest
wq-note-windows --selftest
wq-corpus-scan                  # no corpus data in this source tree
wq-corpus-scan ..               # ...nor anywhere else being published
```

`wq-corpus-scan` reads every published text file — prose, packaging and fixtures,
not only `*.py`. It read only the code until 0.3.0, and three leaks reached the
README under a clean scan: an exemption reason with its real date, a sentence
quoted from a private document, and a private recording's exact duration. Point
it at the repository root, not at the package.

They pass with no corpus, no policy file and no network. That is enforced, not
hoped for: fixtures that read a real document were the reason this package could
not previously be tested away from the notes it was written against.

## Licence

MIT.
