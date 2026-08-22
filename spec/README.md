# The spec, and why it is a table rather than a document

Six review rounds found the same defect shape every time: **the fix lands one
layer below the bug.** A finding names one wrong answer, the repair refuses
exactly that answer, and the neighbouring wrong answers — the ones nobody
typed — stay accepted until the next round types one.

That is not a discipline problem. It is a missing artifact. Nothing in this
package ever said what a rule REFUSES, only what it happened to catch, so
"is this rule complete?" had no answer except another review.

These files are that artifact. One row per rule, and the row is executable:
`tests/test_spec_conformance.py` reads them and fails when the table and the
code disagree.

## What a row looks like

```toml
[[rule]]
id       = "ORACLE-UNRELATED"
code     = "E-ORACLE-UNRELATED"
layer    = "gate"
owner    = "resolve_note.check_oracle"
trigger  = "the note's oracle: value opens as a file"
refuses  = "a path that does not carry the video id as a whole component"
excused_by = ["unfilled_oracles"]
exit     = 1

[[rule.neighbour]]
case = "an absolute path carrying the id somewhere harmless"
test = "tests/test_note_oracle.py::test_an_absolute_path_still_has_to_be_this_videos"
```

## What the conformance test enforces

1. **Every code in the package has a rule.** A defect code the table does not
   describe is an unspecified rule, and unspecified is where every regression
   has come from. Codes not yet written up live in `unspecified.toml`, which
   is a debt list and may only shrink.
2. **Every rule names a code that exists.** A row for a rule the code does not
   implement is prose.
3. **Every rule names an owner that exists** — module and symbol, resolved by
   import. A row pointing at a function that was renamed is a row nobody reads.
4. **Every rule names at least two neighbours.** One neighbour is the case the
   reviewer reported; the second is the one that keeps being missed.
5. **Every neighbour names a test that exists.** This is the load-bearing
   check: adding a neighbour to the table without writing its case turns the
   suite red. It is what makes "an unlisted neighbour" a failing row rather
   than a review finding four rounds later.
6. **Every `excused_by` names a policy table that exists**, so a rule cannot
   claim a debt ledger nothing reads.

## What it deliberately does NOT do

It does not check that a rule is CORRECT — only that it is described, owned,
bounded by named neighbours, and that each neighbour has a case. A wrong rule
with four honest neighbours will pass here and fail a review, which is the
right division of labour: the table makes review cheap, it does not replace it.

It does not generate the tests. A generated case asserts what the generator
believed; these cases are written by hand against the module, and the table
checks only that the case is there. Two tests that share a generator are one
test.

## Layers

| file | layer | rows | what it covers |
|---|---|---|---|
| `note-contract.toml` | 1 | 45 | what a note must carry: frontmatter, density, citations, sets |
| `evidence-anchors.toml` | 2 | 26 | anchors, manifests, transcripts, windows |
| `evidence-witness.toml` | 2 | 29 | what a witness has to show before a claim counts |
| `gate.toml` | 3 | 18 | the aggregator, the leak scan, the policy and its ledgers |
| `review.toml` | 4 | 17 | the lane roll-call and the report header |
| `unspecified.toml` | — | — | codes with no row yet; a debt list, may only shrink |
| `unpinned.toml` | — | — | rows nothing kills; a ledger, may only shrink |

## The table is closed at 136 rows

This is a decision, not a discovery, and it is the one that ends the loop.

Six rounds ran the same shape: add a gate, review it, find defects in it, add a
guard for those, review the guard. Every round ended with more surface than it
started with. The remaining work was never going to shrink, because the thing
being reviewed grew as fast as the review.

So the surface is frozen at the rows that exist today.
`tests/test_spec_conformance.py::test_the_table_is_closed` holds the count.

It has moved twice, both on 2026-08-22, and both reasons are written beside the
constant. 134 to 135: a round of independent verification found the aggregator
printing a six-gate pass with an empty body for the gate that had just reported
grading 4 of 25 notes. Every exit code was right; the report said the opposite
of what the run found. No existing row could see it, because every audit row is
about a code and this is about what gets printed — which is the artifact people
actually read.

135 to 136: the same round found the leak gate exiting 0 over thirteen tracked
lines that reproduced a private recording by its timestamps alone. Every exit
code was right there too — none of the thirteen holds a refusable word, and a
word list can only refuse what somebody thought to write down. No existing row
could see it, because every corpus row compares against a list, and what
identifies a recording is where its moments are.

It does not forbid a new rule. It costs one line —
raise `FROZEN_RULE_COUNT` — and that line makes adding a rule a decision
somebody signs in a commit message, naming the milestone that reopened the
table, rather than something that happens quietly while fixing something else.

What is NOT frozen, and never will be:

- **Cases.** Writing another case for an existing rule is always in scope, and
  is what the unpinned ledger asks for.
- **The two ledgers.** `unspecified.toml` and `unpinned.toml` may only shrink,
  and shrinking either is the work this freeze makes finite.
- **Correcting a row.** A wrong `refuses`, a renamed owner, a missing
  neighbour — those are repairs to a row that already exists, not new surface.

## What a row does not prove

A row says a rule is described, owned, and bounded by named neighbours that
have cases. It does not say a case would notice if the rule stopped working.

`tests/test_rule_mutation.py` asks that second question by rewriting a rule's
owner and re-running the cases the row itself cites. A row nothing kills is
decoration, and it gets a dated entry in `unpinned.toml` rather than a quiet
pass. Read that ledger's count, not the row count here, when asking how much of
the surface is actually held.
