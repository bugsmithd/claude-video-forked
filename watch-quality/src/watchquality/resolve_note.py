#!/usr/bin/env python3
"""Density counter and citation resolver for watch-quality notes.

Slices 1 and 2 of docs/note-quality-plan-round-2.md ("No model-authored
literals"). Two jobs, both narrow:

  slice 1 -- count words and words-per-video-minute, compare against whatever
             the note declares (kills R2, the density stated three times and
             wrong three times).
  slice 2 -- resolve `{{CITE:path#"quote"}}` tokens against the cited file with
             whitespace-normalised matching (kills R3, a claim attributed to
             docs/personal-brand-strategy.md that the file never made).

No git hook and no {{SET}} yet -- those are later slices.

THIS FILE IS THE ORACLE. Before it existed there was no true density for a
note, only competing estimates, which is how 85 / 116 / 131 shipped against a
true 134 on the densest note. The definitions below are therefore normative,
not incidental:

  words   = whitespace-split tokens of the note body, where the body is
            everything after the closing `---` of the YAML frontmatter, with
            any {{CITE}} tokens rendered first so the count does not change
            when --write runs.
            Nothing else is stripped: headings, anchors, code fences and tables
            all count. This reproduces the method the notes themselves declare
            ("stripping frontmatter and splitting the body on whitespace").
  minutes = the frontmatter `duration:` field, parsed as [[H:]M:]S.
  wpm     = words / minutes.

The declared-density sentence lives inside the counted body, but rewriting its
numerals does not change the token count (`~2,150` and `2,162` are both one
token), so the check has a fixed point.

A citation resolves when its quote, with whitespace collapsed and `*` and
backticks dropped, occurs in the cited file under the same normalisation and
inside ONE markdown block. Notes and docs are hard-wrapped near 80 columns, so a
correct quote routinely spans a newline; a naive substring search would
red-light it, and one --no-verify ends the experiment. Underscores are NOT
dropped, because that let `videoid` match the real `video_id`.

Three checks beyond density and citations, each with an on-disk oracle:

  E-ANCHOR-RANGE  an anchor past the frontmatter runtime. Anchors beside a
                  reference to another note are exempt; notes legitimately cite
                  a second in a different video.
  E-BAND          a declared "N-M peer band" that is not the band the corpus
                  actually spans, recomputed from every note on every run.
  E-BARE-PATH     an intra-repo path asserted in prose instead of through a
                  token. ON by default (--no-floor disables); exempt under
                  `## Action`, and exempt once the sidecar records it. Its
                  pattern set is a judgement call, never provably complete.

--write renders each resolved token down to `path` and appends the audit --
resolved line, and the cited file's sha256 -- to notes/.resolved/<video_id>.tsv,
so a citation that goes stale after rendering is still detectable. No Build-order
row asks for a writer; the Sketch does ("promoted from checker to resolver,
because a checker argues with the note while a resolver owns the byte",
docs/note-quality-plan-round-2.md), and without the sidecar the render would
destroy the quote it resolved rather than move it.

Usage:
    scripts/resolve_note.py                 # --check over notes/
    scripts/resolve_note.py --check PATH... # files or directories
    scripts/resolve_note.py --check --no-floor  # skip the untokenised-path floor
    scripts/resolve_note.py --report        # TSV of counted values + defects
    scripts/resolve_note.py --write PATH... # render resolved {{CITE}} tokens
    scripts/resolve_note.py --selftest      # known-answer cases

Exit: 0 clean, 1 defects found, 2 usage or IO error.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tempfile
from pathlib import Path


from .wq_policy import load as load_policy  # noqa: E402

POLICY = load_policy()

# Tolerances. Words are exact on purpose: a hedge ("~2,150") is precisely the
# model-authored estimate this script exists to kill. The rounded quantities
# only have to be rounded correctly.
WORDS_TOL = 0
MINUTES_TOL = 0.5
WPM_TOL = 0.5

DENSITY_WINDOW = 300  # chars of normalised text scanned after the word "density"

RE_DURATION = re.compile(r"^duration:[ \t]*[\"']?([0-9:]+)[\"']?[ \t]*$",
                         re.MULTILINE)
RE_VIDEO_ID = re.compile(r"^video_id:[ \t]*[\"']?(\S+?)[\"']?[ \t]*$",
                         re.MULTILINE)
# The token may itself be hard-wrapped, so newlines are allowed inside it. The
# quote may not contain `{{`, or an unclosed token scans forward and swallows the
# next well-formed citation into its quote -- two defects reported as one.
RE_CITE = re.compile(r"\{\{CITE:([^#]*?)#\"((?:(?!\{\{)[\s\S])*?)\"\}\}")
RE_CITE_START = re.compile(r"\{\{\s*CITE", re.IGNORECASE)
RE_LIST_ITEM = re.compile(r"^([-*+]\s|\d+[.)]\s)")

# --- slice: cardinality before content (R5) -------------------------------
# `{{SET:label}}` ... `{{/SET}}` wraps an enumeration. The count is derived
# from the member lines and written by the renderer, so the model cannot state
# one, and a partial enumeration degrades into a visibly short list instead of
# a false "all four".
RE_SET_OPEN = re.compile(r"\{\{SET:([^}]*)\}\}")
RE_SET_CLOSE = re.compile(r"\{\{/SET\}\}")
RE_SET_START = re.compile(r"\{\{\s*SET", re.IGNORECASE)
RE_ITEM_ANY = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10, "both": 2}
# A stated cardinality that introduces an enumeration: "four surfaces:".
RE_ENUM = re.compile(r"\b(" + "|".join(NUMBER_WORDS) +
                     r")\s+(?:of\s+the\s+)?([a-z][a-z-]+s)\b\s*:", re.IGNORECASE)
RE_COUNT_IN_LABEL = re.compile(r"\b(?:\d+|" + "|".join(NUMBER_WORDS) +
                               r"|all|every)\b", re.IGNORECASE)

# Emitted between markdown blocks so a quote cannot span two of them.
BOUNDARY = "\x00"

# Marks the demotion renderer (demote_note.py) writes into a note. They live
# here because the density counter has to know what it did NOT write.
ORPHAN_MARK = "`ORPHAN`"
DEMOTED_MARK = "<!-- auto-demoted -->"
INTEGRITY_PREFIX = "> integrity:"
SPECULATION_HEADING = "## Speculation"
RE_DENSITY = re.compile(r"density", re.IGNORECASE)
RE_WORDS = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s+words\b", re.IGNORECASE)
RE_SENTENCE_END = re.compile(r"[.!?](?=\s)")
# README filename rule: YYYY-MM-DD--<slug>--<video-id>.md. Used when walking a
# directory so review reports filed under notes/ are not mistaken for notes.
RE_NOTE_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}--.*\.md$")
RE_HEADING = re.compile(r"^##\s+(.*?)\s*$")
ACTION_SECTION = "Action"
RE_BAND = re.compile(r"([0-9]+)\s*-\s*([0-9]+)\s+(?:wpm\s+)?peer\s+band", re.IGNORECASE)
RE_ANCHOR = re.compile(r"`\[(\d{1,2}:\d{2}(?::\d{2})?)\]`")
# An intra-repo path asserted in prose instead of through a {{CITE}} token.
RE_BARE_PATH = re.compile(
    r"(?<![A-Za-z0-9._/-])((?:docs|notes|scripts)/[A-Za-z0-9._/-]+"
    r"\.(?:md|py|tsv|json))")
RE_MINUTES = re.compile(r"([0-9][0-9,.]*)\s+video-minutes?\b", re.IGNORECASE)
RE_WPM = re.compile(r"([0-9][0-9,.]*)\s+words\s+per\s+video-minute\b", re.IGNORECASE)

# --- slice 13: lane roll-call by declared id (R10) -------------------------
# R10 is "review lanes died silently". A dead lane renders exactly like a lane
# that found nothing: the note simply never mentions it. Checking the report
# files a note happens to CITE cannot see that, because a lane that died is a
# lane nobody cited. The roll-call inverts it -- the note declares which lanes
# were dispatched, in frontmatter, and every declared id must have exactly one
# report on disk under notes/reviews/<video_id>/.
#
#   reviews: [facts, quality, coverage-spoken, coverage-frames]
#
# A file matches id `x` when it is named `x.md` or ends `-x.md`. The leading
# dash is load-bearing: without it `facts` would also claim `no-such-facts.md`.
# `[ \t]*`, never `\s*`, in EVERY frontmatter pattern in this file: `\s` matches
# a newline, so `applied:\s*(.*)$` read the NEXT line's value and reported every
# note as applying `rating: 4`. Slice 13 fixed that one and shipped the same bug
# six lines down in `RE_REVIEWS`, where an empty `reviews:` adopted the next
# line as its lane list; the slice-13 coverage lane found it by re-enumerating
# the bug class instead of trusting the fix. `RE_DURATION` and `RE_VIDEO_ID`
# were converted at the same time, on the same argument.
RE_STATUS = re.compile(r"^status:[ \t]*(\S*)[ \t]*$", re.MULTILINE)
RE_APPLIED = re.compile(r"^applied:[ \t]*(.*)$", re.MULTILINE)
# README: "a note with no outbound edge is a bookmark with extra steps", and
# `applied` is the status that says the edge exists. The two fields can drift
# apart in both directions and each direction lies in a different way.
STATUSES = ("capture", "distilled", "applied", "discarded")

RE_REVIEWS = re.compile(r"^reviews:[ \t]*(.*)$", re.MULTILINE)
RE_LANE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
REVIEW_DIR = POLICY.reviews_dir()
# Lanes that really ran and whose report is gone. Same shape and same promise as
# anchor_manifest.UNRESOLVABLE_RUNS: dated, reasoned, printed, may only shrink.
# Never add a row for a lane that was never dispatched -- that is the silent
# death this check exists to make loud. The rows themselves name this corpus's
# runs, so they live in `watch-quality.toml` under `[lost_reviews]`; with no
# policy file this is EMPTY, because a missing policy may not invent an
# exemption.
LOST_REVIEWS: dict[str, dict[str, str]] = POLICY.lost_reviews()


def split_frontmatter(text: str) -> tuple[str, str] | None:
    """Return (frontmatter, body) or None when there is no frontmatter block."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return None


def strip_machine_marks(body: str) -> str:
    """Remove everything a script wrote into the note.

    Density measures what the author wrote. The demotion renderer adds an
    integrity blockquote and ORPHAN / auto-demoted markers, and counting those
    would make every render move the number the note declares -- the tool would
    keep failing the note for the tool's own bytes.
    """
    body = body.replace(" " + ORPHAN_MARK, "").replace(" " + DEMOTED_MARK, "")
    return "\n".join(l for l in body.split("\n")
                     if not l.startswith(INTEGRITY_PREFIX)
                     and l.strip() != SPECULATION_HEADING)


def count_words(body: str) -> int:
    return len(strip_machine_marks(body).split())


def parse_duration(frontmatter: str) -> int | None:
    """`duration: "1:26:56"` -> 5216 seconds."""
    m = RE_DURATION.search(frontmatter)
    if not m:
        return None
    parts = m.group(1).split(":")
    if not 1 <= len(parts) <= 3 or any(p == "" for p in parts):
        return None
    # Only the leading group may exceed 59; "1:2:3:4" used to parse as 223384s
    # and became the oracle every density check was then measured against.
    if any(int(p) > 59 for p in parts[1:]):
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def is_block_start(stripped: str) -> bool:
    """True where a new markdown block begins: heading, table row, fence,
    rule, blockquote or list item."""
    return bool(stripped.startswith(("#", "|", "```", "---", ">"))
                or RE_LIST_ITEM.match(stripped))


def normalise(body: str, body_start_line: int,
              boundaries: bool = False) -> tuple[str, list[int]]:
    """Collapse whitespace and drop markdown emphasis, keeping a line map.

    Notes are hard-wrapped near 80 columns, so a declared density phrase can
    span a newline and `**134 words per video-minute**` carries emphasis
    markers. Matching happens on the normalised string; the parallel line map
    turns any match offset back into a real line number for grep-shaped output.

    With `boundaries=True` a sentinel is emitted wherever one markdown block
    ends and the next begins. Quotes are normalised WITHOUT the sentinel, so a
    quote can never span a block: without this, "Three pillars, and nothing
    else: | Pillar |" resolved against a file where those words sit either side
    of a blank line and a table header -- a fabricated citation passing as real.
    """
    out: list[str] = []
    lines: list[int] = []
    pending_space = False
    for idx, raw in enumerate(body.split("\n")):
        line = body_start_line + idx
        stripped = raw.strip()
        if boundaries and out and out[-1] != BOUNDARY and (
                not stripped or is_block_start(stripped)):
            out.append(BOUNDARY)
            lines.append(line)
        for ch in stripped:
            if ch.isspace():
                pending_space = True
                continue
            if ch in "*`":
                # `_` is NOT stripped: dropping it made `videoid` match the real
                # `video_id`, so a quote could resolve against text that does not
                # exist. Italic underscores are rarer than identifiers here.
                continue
            if pending_space and out:
                out.append(" ")
                lines.append(line)
            pending_space = False
            out.append(ch)
            lines.append(line)
        pending_space = True  # the newline itself acts as a separator
    return "".join(out), lines


def _num(raw: str) -> float:
    return float(raw.replace(",", ""))


def sentence_around(norm: str, at: int) -> str:
    """The single sentence containing offset `at`, capped either side.

    A forward-only window did two things wrong: it reached past the sentence
    and pulled an unrelated "12,000 words" into the declaration, and it never
    looked back, so "...9 words over 10 video-minutes -- that is its density"
    was invisible. A sentence is the unit a human writes a declaration in.
    """
    start = 0
    for m in RE_SENTENCE_END.finditer(norm, 0, at):
        start = m.end()
    end = len(norm)
    m = RE_SENTENCE_END.search(norm, at)
    if m:
        end = m.start()
    return norm[max(start, at - DENSITY_WINDOW):min(end, at + DENSITY_WINDOW)]


def find_declarations(norm: str, linemap: list[int]) -> list[dict]:
    """Every `density...` sentence that actually declares at least one number."""
    found = []
    for m in RE_DENSITY.finditer(norm):
        window = sentence_around(norm, m.end())
        decl = {"line": linemap[m.start()]}
        for key, rx in (("words", RE_WORDS), ("minutes", RE_MINUTES), ("wpm", RE_WPM)):
            hit = rx.search(window)
            if hit:
                decl[key] = _num(hit.group(1))
        if len(decl) > 1:
            found.append(decl)
    return found


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_read(path: Path) -> tuple[str | None, str]:
    """Text, or (None, reason). One undecodable byte anywhere in notes/ used to
    abort the whole run with a traceback and zero defect lines -- a checker that
    dies silently on bad input is worse than one that reports the bad input."""
    try:
        return path.read_text(encoding="utf-8"), ""
    except UnicodeDecodeError as exc:
        return None, f"not valid UTF-8 at byte {exc.start}"
    except OSError as exc:
        return None, f"unreadable: {exc.strerror or exc}"


def resolve_citations(body: str, body_start_line: int, root: Path,
                      note: Path | None = None) -> list[dict]:
    """One record per {{CITE}} token: resolved line, or an `error` code.

    Records are also emitted for text that opens a token but never forms one.
    A typo'd `{{CITE` used to parse as zero tokens and vanish, which turns a
    broken citation into a silent pass -- the exact failure mode the resolver
    exists to remove.
    """
    out = []
    matched_starts = set()
    for m in RE_CITE.finditer(body):
        rec = {
            "span": m.span(),
            "line": body_start_line + body.count("\n", 0, m.start()),
            "path": " ".join(m.group(1).split()),
            "quote": normalise(m.group(2), 1)[0],
            "raw_quote": " ".join(m.group(2).split()),
        }
        matched_starts.add(m.start())
        out.append(rec)
        if not rec["quote"]:
            rec["error"] = "E-CITE-EMPTY empty quote"
            continue
        target = (root / rec["path"]).resolve()
        if not target.is_relative_to(root):
            rec["error"] = f"E-CITE-PATH outside repo: {rec['path']}"
            continue
        if not target.is_file():
            rec["error"] = f"E-CITE-NOFILE {rec['path']}"
            continue
        if note is not None and target == note.resolve():
            rec["error"] = "E-CITE-SELF a note is not evidence for itself"
            continue
        text, why = safe_read(target)
        if text is None:
            rec["error"] = f"E-CITE-UNREADABLE {rec['path']} {why}"
            continue
        norm, linemap = normalise(text, 1, boundaries=True)
        at = norm.find(rec["quote"])
        if at < 0:
            rec["error"] = (f"E-CITE-NOMATCH {rec['path']} does not contain "
                            f"\"{rec['raw_quote']}\"")
            continue
        rec["target"] = target
        rec["target_line"] = linemap[at]
        rec["sha"] = sha256(target)

    for m in RE_CITE_START.finditer(body):
        if m.start() in matched_starts:
            continue
        # Inside a well-formed token? Then a greedy match swallowed it.
        out.append({
            "span": (m.start(), m.start()),
            "line": body_start_line + body.count("\n", 0, m.start()),
            "path": "", "quote": "", "raw_quote": "",
            "error": "E-CITE-MALFORMED token does not close as "
                     "{{CITE:path#\"quote\"}}",
        })
    return out


def render_citations(body: str, cites: list[dict]) -> str:
    """Replace each token with `path`.

    There is exactly one render, and the density count is taken from its
    output, so --write cannot change the number the note declares. An earlier
    line-preserving variant appended the token's newlines after the path, which
    split `foo{{CITE..}}bar` into two words before the write and one after.
    Malformed markers (zero-length span) are reported, never rendered.
    """
    for rec in reversed(cites):
        start, end = rec["span"]
        if start == end:
            continue
        body = body[:start] + f"`{rec['path']}`" + body[end:]
    return body


def find_sets(body: str, body_start_line: int) -> list[dict]:
    """One record per `{{SET:label}} ... {{/SET}}` block.

    The members are the top-level list items inside it, with continuation lines
    folded in. The label may not contain a number: the count is the renderer's
    to write, which is the whole point of the token.
    """
    out: list[dict] = []
    pos = 0
    while True:
        opened = RE_SET_OPEN.search(body, pos)
        if opened is None:
            break
        line = body_start_line + body.count("\n", 0, opened.start())
        closed = RE_SET_CLOSE.search(body, opened.end())
        nested = RE_SET_OPEN.search(body, opened.end())
        rec = {"line": line, "label": " ".join(opened.group(1).split()),
               "members": [], "span": (opened.start(), opened.end())}
        out.append(rec)
        if closed is None:
            rec["error"] = "E-SET-UNCLOSED block never closes with {{/SET}}"
            break
        if nested is not None and nested.start() < closed.start():
            rec["error"] = "E-SET-NESTED a set may not contain a set"
            pos = nested.start()
            continue
        rec["span"] = (opened.start(), closed.end())
        inner = body[opened.end():closed.start()]
        for raw in inner.split("\n"):
            if RE_ITEM_ANY.match(raw):
                rec["members"].append(raw.rstrip())
            elif rec["members"] and raw.strip():
                rec["members"][-1] += " " + raw.strip()
        if not rec["members"]:
            rec["error"] = "E-SET-EMPTY block lists no members"
        # Backticked spans are stripped first: a label naming its own anchor,
        # `[02:56]`, is not a cardinality claim, and reading its digits as one
        # is the false red light this whole family of checks is warned about.
        elif RE_COUNT_IN_LABEL.search(re.sub(r"`[^`]*`", " ", rec["label"])):
            rec["error"] = (f"E-SET-COUNT the label states a count "
                            f"(\"{rec['label']}\"); the renderer derives it "
                            f"from the {len(rec['members'])} member line(s)")
        pos = closed.end()

    for m in RE_SET_START.finditer(body):
        if any(r["span"][0] == m.start() for r in out):
            continue
        out.append({"line": body_start_line + body.count("\n", 0, m.start()),
                    "label": "", "members": [], "span": (m.start(), m.start()),
                    "error": "E-SET-MALFORMED token does not open as "
                             "{{SET:label}}"})
    return out


def render_sets(body: str, sets: list[dict]) -> str:
    """Replace each block with its members under a machine-written count."""
    for rec in reversed(sets):
        start, end = rec["span"]
        if start == end or "error" in rec:
            continue
        head = f"{rec['label']} ({len(rec['members'])} listed):"
        block = "\n".join([head.lstrip(), ""] + rec["members"])
        # A rendered set must not run into the bullet after it, or its members
        # join that list and the enumeration silently gains neighbours.
        tail = body[end:]
        if not tail.startswith("\n\n"):
            block += "\n"
        body = body[:start] + block + tail
    return body


def check_enumerations(body: str, body_start_line: int, rel) -> list[str]:
    """A stated count that introduces a MARKDOWN LIST must match its items.

    Deliberately narrow, and the narrowness is measured twice. Across the eight
    notes 117 cardinality words appear; exactly 6 introduce something
    countable. Widening this to every "three minutes" would fire 111 times on
    prose that enumerates nothing.

    Counting an INLINE comma list was built, run against the corpus, and cut:
    it fired 4 times and all 4 were false. A comma inside a number splits
    `1,300-token` in two; a comma before "and" splits one clause into two
    members ("the toy stays on trend through the holiday season, **and** the
    parent scales supply" reads as three); a sentence that keeps going past the
    list keeps adding commas; and "first of four figures:" is a forward
    reference to four figures cited later, not a list of what follows. Prose
    list boundaries are not an oracle, which is exactly why the plan put the
    countable case behind a {{SET}} token instead.
    """
    out = []
    lines = body.split("\n")
    for i, line in enumerate(lines):
        for m in RE_ENUM.finditer(line):
            declared = NUMBER_WORDS[m.group(1).lower()]
            # a markdown list below?
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            items = 0
            while j < len(lines) and (RE_ITEM_ANY.match(lines[j])
                                      or (items and lines[j].startswith("  ")
                                          and lines[j].strip())):
                items += RE_ITEM_ANY.match(lines[j]) is not None
                j += 1
            if items and items != declared:
                out.append(f"{rel}:{body_start_line + i} E-SET-COUNT declared "
                           f"{m.group(1)} {m.group(2)} but {items} listed")
    return out


def sidecar_path(root: Path, video_id: str) -> Path:
    return root / POLICY.resolved_dir() / f"{video_id}.tsv"


def check_sidecar(root: Path, frontmatter: str, rel) -> list[str]:
    """A rendered citation is no longer greppable, so the sidecar sha is the
    only thing that can still notice the cited file drifting."""
    m = RE_VIDEO_ID.search(frontmatter)
    if not m:
        return []
    side = sidecar_path(root, m.group(1))
    if not side.is_file():
        return []
    text, why = safe_read(side)
    if text is None:
        return [f"{rel}:1 E-SIDECAR-UNREADABLE {side.name} {why}"]
    defects = []
    for n, row in enumerate(text.splitlines(), 1):
        if not row or row.startswith("#"):
            continue
        fields = row.split("\t")
        if len(fields) < 3:
            # A short row used to pad with "" and report a false STALE, blaming
            # the cited document for the sidecar's own corruption.
            defects.append(f"{rel}:1 E-SIDECAR-ROW {side.name}:{n} has "
                           f"{len(fields)} of 4 columns")
            continue
        cited, line, sha = fields[0], fields[1], fields[2]
        target = (root / cited).resolve()
        if not target.is_relative_to(root):
            defects.append(f"{rel}:1 E-SIDECAR-ROW {side.name}:{n} points "
                           f"outside the repo: {cited}")
        elif not target.is_file():
            defects.append(f"{rel}:1 E-CITE-STALE cited file gone: {cited}")
        elif sha256(target) != sha:
            defects.append(f"{rel}:1 E-CITE-STALE {cited} changed since "
                           f"resolution at line {line}")
    return defects


def refresh_sidecar(root: Path, note: Path) -> tuple[list[str], int]:
    """Re-resolve a sidecar's recorded quotes and rewrite line and sha256.

    E-CITE-STALE conflates two very different states: the cited file changed
    and still contains the quote, or the quote is gone. Once a token has been
    rendered there is nothing left to re-run, so a legitimate edit to a cited
    note used to leave a permanent red light -- which is how a check gets
    switched off. This separates them: a quote that still resolves refreshes,
    a quote that does not stays a defect and nothing is written.
    """
    rel = note.relative_to(root) if note.is_relative_to(root) else note
    text, why = safe_read(note)
    split = split_frontmatter(text) if text else None
    if split is None:
        return [f"{rel}:1 E-FRONTMATTER no YAML frontmatter block"], 0
    m = RE_VIDEO_ID.search(split[0])
    if not m:
        return [], 0
    side = sidecar_path(root, m.group(1))
    body, _why = safe_read(side)
    if body is None:
        return [], 0

    defects, rows, refreshed = [], [], 0
    for n, row in enumerate(body.splitlines(), 1):
        if not row or row.startswith("#"):
            rows.append(row)
            continue
        fields = row.split("\t")
        if len(fields) < 4:
            defects.append(f"{rel}:1 E-SIDECAR-ROW {side.name}:{n} has "
                           f"{len(fields)} of 4 columns")
            rows.append(row)
            continue
        cited, _line, sha, quote = fields[0], fields[1], fields[2], fields[3]
        target = (root / cited).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            defects.append(f"{rel}:1 E-CITE-STALE cited file gone: {cited}")
            rows.append(row)
            continue
        current = sha256(target)
        if current == sha:
            rows.append(row)
            continue
        cited_text, _ = safe_read(target)
        norm, linemap = normalise(cited_text or "", 1, boundaries=True)
        at = norm.find(normalise(quote, 1)[0])
        if at < 0:
            defects.append(f"{rel}:1 E-CITE-NOMATCH {cited} no longer contains "
                           f"\"{quote}\"")
            rows.append(row)
            continue
        rows.append(f"{cited}\t{linemap[at]}\t{current}\t{quote}")
        refreshed += 1
    if refreshed and not defects:
        side.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return defects, refreshed


def check_note(path: Path, root: Path, require_density: bool,
               band: tuple[float, float] | None = None,
               floor: bool = False) -> tuple[list[str], dict | None]:
    """Return (defect lines, counted stats)."""
    rel = path.relative_to(root) if path.is_relative_to(root) else path
    text, why = safe_read(path)
    if text is None:
        return [f"{rel}:1 E-READ {why}"], None
    split = split_frontmatter(text)
    if split is None:
        return [f"{rel}:1 E-FRONTMATTER no YAML frontmatter block"], None
    frontmatter, body = split
    body_start_line = len(frontmatter.split("\n")) + 3  # ---, frontmatter, ---

    cites = resolve_citations(body, body_start_line, root, note=path)
    cite_defects = [f"{rel}:{c['line']} {c['error']}" for c in cites if "error" in c]
    cite_defects += check_sidecar(root, frontmatter, rel)
    cite_defects += check_lanes(root, frontmatter, rel)
    cite_defects += check_status(frontmatter, rel, root)
    cite_defects += check_band(body, band, rel)
    sets = find_sets(body, body_start_line)
    cite_defects += [f"{rel}:{s['line']} {s['error']}" for s in sets if "error" in s]
    cite_defects += check_enumerations(body, body_start_line, rel)
    if floor:
        known = sidecar_paths(root, frontmatter) | {c["path"] for c in cites}
        cite_defects += check_floor(body, body_start_line, rel, known)

    # Citations are inline and sets are line blocks, so citations render first
    # and the set spans are re-found against the rendered text. One render, so
    # --write cannot move the number the note declares.
    rendered = render_citations(body, cites) if cites else body
    if sets:
        rendered = render_sets(rendered, find_sets(rendered, body_start_line))
    words = count_words(rendered)
    seconds = parse_duration(frontmatter)
    # A note with a broken duration still belongs in --report; dropping its row
    # made the corpus view silently smaller than the corpus.
    stats = {"file": str(rel), "words": words, "seconds": seconds or 0,
             "minutes": None, "wpm": None}
    if seconds is None:
        return cite_defects + [f"{rel}:1 E-DURATION frontmatter duration "
                               f"missing or unparseable"], stats
    if seconds == 0:
        return cite_defects + [f"{rel}:1 E-DURATION-ZERO duration is 0:00, so "
                               f"density has no denominator"], stats
    minutes = seconds / 60.0
    wpm = words / minutes
    stats.update(minutes=minutes, wpm=wpm)
    cite_defects += check_anchors(body, seconds, rel)

    # Declarations are found in the ORIGINAL body so reported line numbers match
    # the file on disk; only the word count comes from the rendered form.
    norm, linemap = normalise(body, body_start_line)
    decls = find_declarations(norm, linemap)
    if not decls:
        if require_density:
            return cite_defects + [f"{rel}:1 E-DENSITY-MISSING no declared density line"], stats
        return cite_defects, stats

    defects = list(cite_defects)
    for d in decls:
        ln = d["line"]
        if "words" in d and abs(d["words"] - words) > WORDS_TOL:
            defects.append(
                f"{rel}:{ln} E-DENSITY-WORDS declared {d['words']:g} counted {words}")
        if "minutes" in d and abs(d["minutes"] - minutes) > MINUTES_TOL:
            defects.append(
                f"{rel}:{ln} E-DENSITY-MINUTES declared {d['minutes']:g} "
                f"counted {minutes:.2f}")
        if "wpm" in d and abs(d["wpm"] - wpm) > WPM_TOL:
            defects.append(
                f"{rel}:{ln} E-DENSITY-WPM declared {d['wpm']:g} counted {wpm:.1f}")
    return defects, stats


def anchor_seconds(text: str) -> list[tuple[str, int]]:
    out = []
    for raw in RE_ANCHOR.findall(text):
        parts = [int(p) for p in raw.split(":")]
        seconds = 0
        for p in parts:
            seconds = seconds * 60 + p
        out.append((raw, seconds))
    return out


def check_anchors(body: str, seconds: int, rel) -> list[str]:
    """An anchor past the runtime cites a moment the video does not have.

    `duration:` is the only oracle the density check has, and nothing else
    validates it. This is the cross-check: if anchors run past it, one of the
    two is wrong and the note says which.

    Anchors near a reference to another note are exempt -- notes legitimately
    cite a second in a DIFFERENT video, and flagging those would be the false
    red light that gets a checker switched off.
    """
    lines = body.split("\n")
    foreign = set()
    for i, line in enumerate(lines):
        if re.search(r"notes/\S+\.md", line):
            foreign.update({i, i + 1, i + 2})
    over = {a for i, line in enumerate(lines) if i not in foreign
            for a, s in anchor_seconds(line) if s > seconds}
    over = sorted(over)
    if not over:
        return []
    return [f"{rel}:1 E-ANCHOR-RANGE {len(over)} anchor(s) past the "
            f"{seconds}s runtime: {', '.join(over[:4])}"]


def check_band(body: str, band: tuple[float, float] | None, rel) -> list[str]:
    """A declared peer band must be the band the corpus actually has."""
    if band is None:
        return []
    defects = []
    for m in RE_BAND.finditer(" ".join(body.split())):
        lo, hi = float(m.group(1)), float(m.group(2))
        if abs(lo - band[0]) > 0.5 or abs(hi - band[1]) > 0.5:
            defects.append(f"{rel}:1 E-BAND declared {m.group(1)}-{m.group(2)} "
                           f"computed {band[0]:.0f}-{band[1]:.0f}")
    return defects


def check_floor(body: str, body_start_line: int, rel,
                resolved: set[str] | None = None) -> list[str]:
    """Intra-repo paths asserted in prose rather than through a {{CITE}} token.

    The resolver only governs literals that opted in, and nothing forces opt-in.
    This is the floor that forces it. Two exemptions, both mechanical:

      - a path already resolved, whether it is still a token in the body or has
        been rendered and recorded in the sidecar. Rendering must not turn a
        checked citation back into an unchecked one.
      - a path under `## Action`. Those name a file the note intends to CHANGE,
        so there is nothing in it yet to quote; demanding a citation there would
        be the false red light that gets the check switched off.

    Its pattern set is a judgement call and its coverage is never provably
    complete: it names what it finds, and cannot claim what it missed.
    """
    out = []
    resolved = resolved or set()
    section = ""
    for i, line in enumerate(RE_CITE.sub("", body).split("\n")):
        head = RE_HEADING.match(line)
        if head:
            section = head.group(1)
        if section == ACTION_SECTION:
            continue
        for m in RE_BARE_PATH.finditer(line):
            if m.group(1) in resolved:
                continue
            out.append(f"{rel}:{body_start_line + i} E-BARE-PATH untokenised "
                       f"{m.group(1)}")
    return out


def sidecar_paths(root: Path, frontmatter: str) -> set[str]:
    """Paths this note has already resolved and recorded."""
    m = RE_VIDEO_ID.search(frontmatter)
    if not m:
        return set()
    side = sidecar_path(root, m.group(1))
    if not side.is_file():
        return set()
    text, _ = safe_read(side)
    return {row.split("\t")[0] for row in (text or "").splitlines()
            if row and not row.startswith("#")}


def applied_target(root: Path, value: str) -> Path | None:
    """Resolve an `applied:` value, or None when nothing it could mean exists.

    Three bases, because a corpus tends to grow two conventions and write
    neither down: an absolute path with a tilde (`~/dev/.../file.md`), and one
    that starts with the repository's own directory name, which resolves
    relative to the repo's PARENT because the note names the repo. Repo-root-
    relative is accepted too, as the form a reader would guess.
    """
    p = Path(value).expanduser()
    for cand in (p, root / p, root.parent / p):
        if cand.exists():
            return cand
    return None


def check_status(frontmatter: str, rel, root: Path | None = None) -> list[str]:
    """`status:` and `applied:` have to agree, in both directions.

    A note with an `applied:` path and `status: distilled` under-reports work
    that was really done, so the corpus reads as more inert than it is. A note
    with `status: applied` and an empty `applied:` claims an outbound edge that
    does not exist, which is the more expensive lie: it is the one the README
    calls the whole point. Neither direction is a warning.
    """
    m = RE_STATUS.search(frontmatter)
    if not m:
        return [f"{rel}:1 E-STATUS-MISSING no status: field"]
    status = m.group(1).strip().strip("'\"")
    if status not in STATUSES:
        return [f"{rel}:1 E-STATUS-UNKNOWN status {status!r} is not one of "
                f"{', '.join(STATUSES)}"]
    a = RE_APPLIED.search(frontmatter)
    applied = (a.group(1).strip().strip("'\"") if a else "")
    if applied and status != "applied":
        return [f"{rel}:1 E-STATUS-APPLIED applied: names {applied} but status "
                f"is {status}"]
    if status == "applied" and not applied:
        return [f"{rel}:1 E-STATUS-NOEDGE status is applied but applied: is empty"]
    # A PHANTOM PATH CLAIMS THE SAME NONEXISTENT EDGE AS AN EMPTY FIELD, and
    # until slice 13's coverage lane nothing looked: `E-STATUS-NOEDGE` tested
    # emptiness while calling itself a check on the edge. The roll-call resolves
    # its lane ids against the filesystem; this had to as well.
    if applied and root is not None and applied_target(root, applied) is None:
        return [f"{rel}:1 E-STATUS-EDGE applied: names {applied}, which resolves "
                f"to no file from the note, the repo or its parent"]
    return []


def lane_ids(frontmatter: str) -> tuple[list[str] | None, str | None]:
    """Declared review lane ids, or (None, None) when the key is absent.

    Returns (ids, error). An absent key is not a defect: most notes were never
    reviewed by a lane. A key that is present and unreadable IS one, because the
    alternative is a typo silently disarming the roll-call.
    """
    m = RE_REVIEWS.search(frontmatter)
    if not m:
        return None, None
    raw = m.group(1).strip()
    if not raw:
        return [], None  # the key with no value declares no lanes, like `[]`
    if not raw.startswith("[") or not raw.endswith("]"):
        return None, f"reviews: expected a bracketed list, got {raw!r}"
    inner = raw[1:-1].strip()
    if not inner:
        return [], None
    ids = [p.strip().strip("'\"") for p in inner.split(",")]
    bad = [i for i in ids if not RE_LANE_ID.match(i)]
    if bad:
        return None, f"reviews: not a lane id: {', '.join(bad) or '(empty)'}"
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        return None, f"reviews: declared twice: {', '.join(dupes)}"
    return ids, None


def lane_reports(root: Path, video_id: str) -> list[Path]:
    # rglob, not glob: one `mkdir` made an undeclared report invisible to the
    # roll-call, and filing by date under the video id is the obvious thing a
    # future run does (slice-13 coverage lane).
    d = root / REVIEW_DIR / video_id
    return sorted(p for p in d.rglob("*.md")) if d.is_dir() else []


def lane_matches(reports: list[Path], lane: str) -> list[Path]:
    return [p for p in reports
            if p.name == f"{lane}.md" or p.name.endswith(f"-{lane}.md")]


def check_lanes(root: Path, frontmatter: str, rel) -> list[str]:
    """Roll-call: every declared lane emitted a report, every report was declared.

    Four ways this goes wrong and all four are defects, because each one reads
    as a clean review from the outside:

      MISSING     the lane died, or was never dispatched after being declared.
      UNDECLARED  a report exists that the note does not own up to running.
      AMBIGUOUS   two files answer to one id, so which one is the verdict?
      SHARED      one file answers to two ids, so one lane is silently absent.
    """
    ids, err = lane_ids(frontmatter)
    m = RE_VIDEO_ID.search(frontmatter)
    if err:
        return [f"{rel}:1 E-LANE-MALFORMED {err}"]
    if not m:
        return []
    video_id = m.group(1)
    reports = lane_reports(root, video_id)
    if ids is None:
        ids = []
    lost = LOST_REVIEWS.get(video_id, {})
    out: list[str] = []
    claimed: dict[str, list[str]] = {}
    for lane in ids:
        hits = lane_matches(reports, lane)
        for h in hits:
            claimed.setdefault(h.name, []).append(lane)
        if len(hits) > 1:
            out.append(f"{rel}:1 E-LANE-AMBIGUOUS lane {lane} matches "
                       f"{len(hits)} reports: {', '.join(h.name for h in hits)}")
        elif not hits and lane not in lost:
            out.append(f"{rel}:1 E-LANE-MISSING lane {lane} declared, no report "
                       f"under {REVIEW_DIR}/{video_id}/")
    for name, lanes in sorted(claimed.items()):
        if len(lanes) > 1:
            out.append(f"{rel}:1 E-LANE-SHARED {name} answers to "
                       f"{len(lanes)} declared lanes: {', '.join(sorted(lanes))}")
    for p in reports:
        if p.name not in claimed:
            out.append(f"{rel}:1 E-LANE-UNDECLARED {p.name} is on disk but no "
                       f"reviews: id claims it")
    return out


def orphan_sidecars(root: Path, files: list[Path]) -> list[str]:
    """Sidecars whose video_id matches no note in the corpus.

    The sidecar is addressed only by video_id, so renaming that field silently
    detaches the audit: the note stops being staleness-checked and nothing says
    so. This sweep is the only thing that notices.
    """
    live = set()
    for f in files:
        text, _ = safe_read(f)
        if text is None:
            continue
        split = split_frontmatter(text)
        if split:
            m = RE_VIDEO_ID.search(split[0])
            if m:
                live.add(m.group(1))
    out = []
    for side in sorted((root / POLICY.resolved_dir()).glob("*.tsv")):
        if side.stem not in live:
            out.append(f"notes/.resolved/{side.name}:1 E-SIDECAR-ORPHAN "
                       f"no note declares video_id {side.stem}")
    return out


def collect(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            # rglob, not glob: a note filed one directory down used to be
            # invisible, and an empty result used to read as a clean pass.
            found = sorted(f for f in p.rglob("*.md") if RE_NOTE_NAME.match(f.name))
            if not found:
                print(f"resolve_note.py: no notes under {p}", file=sys.stderr)
                raise SystemExit(2)
            files.extend(found)
        elif p.is_file():
            files.append(p)
        else:
            print(f"resolve_note.py: no such path: {p}", file=sys.stderr)
            raise SystemExit(2)
    return [f for f in files if f.name != "_template.md"]


def write_note(path: Path, root: Path) -> tuple[list[str], int]:
    """Render resolved {{CITE}} tokens in place and record the audit sidecar.

    Refuses to touch a file with any unresolved token: a half-rendered note
    hides the defect it was supposed to surface.
    """
    rel = path.relative_to(root) if path.is_relative_to(root) else path
    text, why = safe_read(path)
    if text is None:
        return [f"{rel}:1 E-READ {why}"], 0
    split = split_frontmatter(text)
    if split is None:
        return [f"{rel}:1 E-FRONTMATTER no YAML frontmatter block"], 0
    frontmatter, body = split
    body_start_line = len(frontmatter.split("\n")) + 3

    cites = resolve_citations(body, body_start_line, root, note=path)
    sets = find_sets(body, body_start_line)
    if not cites and not sets:
        return [], 0
    bad = [f"{rel}:{c['line']} {c['error']}" for c in cites if "error" in c]
    bad += [f"{rel}:{s['line']} {s['error']}" for s in sets if "error" in s]
    if bad:
        return bad + [f"{rel}:1 E-CITE-UNRESOLVED not written"], 0

    m = RE_VIDEO_ID.search(frontmatter)
    if not m and cites:
        # Rendering discards the quote, so without a sidecar to write it to the
        # evidence is destroyed rather than moved. Refuse instead.
        return [f"{rel}:1 E-NO-VIDEO-ID cannot record the audit, not written"], 0

    head = text[:len(text) - len(body)]
    rendered = render_citations(body, cites) if cites else body
    if sets:
        rendered = render_sets(rendered, find_sets(rendered, body_start_line))
    path.write_text(head + rendered, encoding="utf-8")
    if not cites:
        return [], 0

    side = sidecar_path(root, m.group(1))
    side.parent.mkdir(parents=True, exist_ok=True)
    rows = [f"# note\t{rel}", "# path\tline\tsha256\tquote"]
    rows += [f"{c['path']}\t{c['target_line']}\t{c['sha']}\t{c['raw_quote']}"
             for c in cites]
    side.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return [], len(cites)


def selftest() -> int:
    """Known-answer cases for the things that are easy to get wrong."""
    cases = 0
    assert count_words("one two  three\nfour\n") == 4; cases += 1
    assert parse_duration('duration: "1:26:56"') == 5216; cases += 1
    assert parse_duration("duration: 4:59") == 299; cases += 1
    body = ("Note density: ~2,150 words over 87 video-minutes, about 25 words\n"
            "per video-minute.\n")
    norm, linemap = normalise(body, 1)
    d = find_declarations(norm, linemap)[0]
    assert d == {"line": 1, "words": 2150.0, "minutes": 87.0, "wpm": 25.0}, d
    cases += 1
    bold = "Density: 4,085 words over 30.5 video-minutes, **134 words per\nvideo-minute**,\n"
    d = find_declarations(*normalise(bold, 1))[0]
    assert d["wpm"] == 134.0 and d["words"] == 4085.0, d
    cases += 1
    assert find_declarations(*normalise("a density it had estimated, not counted.", 1)) == []
    cases += 1

    # Citations. This used to read a real corpus document and assert on the
    # line number a phrase sat at inside it, which made a package selftest fail
    # the moment the corpus was not beside the code -- and made an ordinary edit
    # to that document a test failure. The fixture below reproduces the only
    # property the assertion ever needed: a quote that WRAPS across a newline in
    # the source still resolves, which is the R3 defect (a citation was recorded
    # for words the document does not contain in that order).
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        (root / "docs").mkdir()
        anchor = "docs/anchor.md"
        (root / anchor).write_text(
            "# Anchor\n"          # 1
            "\n"                  # 2
            "filler paragraph.\n"  # 3
            "\n"                  # 4
            "A reader will not\n"  # 5
            "write the sentence already\n"   # 6  <- the quote starts here
            "running in their head for you.\n"  # 7  <- and ends here
            "\n"                   # 8
            "Three pillars, and nothing else:\n"  # 9
            "\n"                   # 10
            "| Pillar | Why |\n"   # 11
            "| --- | --- |\n"      # 12
            "| One | because |\n",  # 13
            encoding="utf-8")
        wrapped = ('a {{CITE:' + anchor + '#"write the sentence already\n'
                   'running in their head"}} b\n')
        rec = resolve_citations(wrapped, 10, root)[0]
        assert "error" not in rec, rec
        # target_line is where the quote STARTS in the cited file; line is where
        # the citation sits in the citing note. They are different numbers and
        # were once confused.
        assert rec["target_line"] == 6 and rec["line"] == 10, rec
        cases += 1
        # The R3 defect itself: words that are not in the document, in an order
        # that reads plausibly. This must be an error, not a resolution.
        bad = resolve_citations(
            '{{CITE:' + anchor + '#"posts never ship because they are '
            'half-written essays"}}', 1, root)[0]
        assert bad["error"].startswith("E-CITE-NOMATCH"), bad
        cases += 1
        assert resolve_citations('{{CITE:docs/does-not-exist.md#"x"}}', 1,
                                 root)[0]["error"].startswith("E-CITE-NOFILE")
        cases += 1
        assert resolve_citations('{{CITE:' + anchor + '#""}}', 1, root)[0][
            "error"].startswith("E-CITE-EMPTY")
        cases += 1

        # --- regressions for the four blockers found in review, 2026-08-05 ---
        # A quote may not span a markdown block: these words sit either side of
        # a blank line and a table header, and used to resolve.
        spanning = ('{{CITE:' + anchor + '#"Three pillars, and nothing else: '
                    '| Pillar |"}}')
        assert resolve_citations(spanning, 1, root)[0]["error"].startswith(
            "E-CITE-NOMATCH"), "block boundary not enforced"
        cases += 1
        # ...but a quote inside one block still resolves.
        inside = '{{CITE:' + anchor + '#"Three pillars, and nothing else"}}'
        assert "error" not in resolve_citations(inside, 1, root)[0]
        cases += 1
        # A note is not evidence for itself.
        me = root / POLICY.notes_dir() / "x.md"
        assert resolve_citations('{{CITE:notes/x.md#"q"}}', 1, root, note=me)[0][
            "error"].startswith(("E-CITE-SELF", "E-CITE-NOFILE"))
        cases += 1
        # A token that never closes must be loud, not invisible.
        for broken in ('{{CITE:docs/x.md#"unclosed}}', '{{CITE docs/x.md#"q"}}'):
            recs = resolve_citations(broken, 1, root)
            assert recs and recs[-1]["error"].startswith("E-CITE-MALFORMED"), \
                broken
        cases += 1
        # Rendering must not move the word count, even flush against text.
        flush = ('foo{{CITE:' + anchor + '#"write the sentence already\n'
                 'running in their head"}}bar')
        assert count_words(
            render_citations(flush, resolve_citations(flush, 1, root))) == 1
        cases += 1
        # An unclosed token no longer eats the next valid one.
        two = ('{{CITE:' + anchor + '#"unclosed '
               '{{CITE:' + anchor + '#"Three pillars"}}')
        errs = [r.get("error", "ok") for r in resolve_citations(two, 1, root)]
        assert "ok" in errs and any(
            e.startswith("E-CITE-MALFORMED") for e in errs), errs
        cases += 1

    # --- the pre-known-answer fixture, preserved after the notes were repaired ---
    # Both numerals below shipped in real notes and both were wrong. Repairing
    # the notes consumed the fixture, so it lives here now: the counter must
    # still fail on the text as it was written, end to end.
    head = ('---\ntitle: fixture\nvideo_id: FIXTURE\nduration: "1:00"\n'
            'status: capture\n---\n')
    with tempfile.TemporaryDirectory() as td:
        for prose, code in (
                ("Note density: ~2,150 words over 87 video-minutes.", "E-DENSITY-WORDS"),
                ("Density: 4,085 words, **131 words per video-minute**.", "E-DENSITY-WPM")):
            f = Path(td) / "fixture.md"
            f.write_text(head + prose + "\n", encoding="utf-8")
            defects, _ = check_note(f, root, False)
            assert any(code in d for d in defects), (code, defects)
            cases += 1

    # --- regressions for the MAJOR/MINOR findings, same review ---
    # A declaration is one sentence: no reaching past it, and no blind spot behind it.
    far = "Density here. Unrelated later: 12,000 words of transcript."
    assert find_declarations(*normalise(far, 1)) == [], far
    cases += 1
    back = "This note runs 9 words over 10 video-minutes -- that is its density."
    d = find_declarations(*normalise(back, 1))
    assert d and d[0]["words"] == 9.0 and d[0]["minutes"] == 10.0, d
    cases += 1
    # A decimal word count must parse as itself, not as its last digit.
    d = find_declarations(*normalise("Density: 1,234.5 words.", 1))[0]
    assert d["words"] == 1234.5, d
    cases += 1
    # Durations: three groups at most, and no 60+ in minutes or seconds.
    assert parse_duration('duration: "1:2:3:4"') is None
    assert parse_duration('duration: "1:75"') is None
    assert parse_duration('duration: "0:00"') == 0
    cases += 1
    # A bad byte is a defect line, not a traceback.
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "bad.md"
        f.write_bytes(b"---\nduration: \"1:00\"\n---\n\xff\xfe not utf-8\n")
        defects, _ = check_note(f, root, False)
        assert defects and "E-READ" in defects[0], defects
    cases += 1

    # --- regressions for the conformance and coverage lanes ---
    # `_` must survive normalisation, or `videoid` matches the real `video_id`.
    assert normalise("video_id", 1)[0] == "video_id"
    with tempfile.TemporaryDirectory() as td:
        r = Path(td).resolve()
        (r / "README.md").write_text("video_id: the field\n", encoding="utf-8")
        assert resolve_citations('{{CITE:README.md#"videoid:"}}', 1, r)[0][
            "error"].startswith("E-CITE-NOMATCH")
        # ...and the real spelling still resolves, or the case above would pass
        # for the wrong reason -- a file the resolver simply cannot read.
        assert "error" not in resolve_citations(
            '{{CITE:README.md#"video_id: the field"}}', 1, r)[0]
    cases += 1
    # An anchor past the runtime is a defect, unless it cites another note.
    assert check_anchors("see `[45:00]` here", 600, "n.md")
    assert not check_anchors("compare notes/x.md\n`[45:00]` there", 600, "n.md")
    cases += 1
    # A declared peer band must be the band the corpus actually spans.
    assert check_band("above the 42-121 peer band", (25.0, 413.0), "n.md")
    assert not check_band("inside the 25-413 wpm peer band", (24.9, 412.9), "n.md")
    cases += 1
    # The floor names paths the resolver is not checking, and ignores tokens.
    assert len(check_floor("see docs/some-strategy.md now", 1, "n.md")) == 1
    assert check_floor('{{CITE:docs/some-strategy.md#"q"}}', 1, "n.md") == []
    cases += 1
    # ...and stays quiet once the sidecar records the path, or under ## Action.
    assert check_floor("see docs/x.md", 1, "n.md", {"docs/x.md"}) == []
    assert check_floor("## Action\n- [ ] edit docs/x.md", 1, "n.md") == []
    assert len(check_floor("## Action\n- x\n## Claims\ndocs/x.md", 1, "n.md")) == 1
    cases += 1

    # --- {{SET}}: the count is derived, never typed (R5) ---
    block = "{{SET:the tab bar}}\n- Home\n- Search\n- Library\n{{/SET}}\n"
    rec = find_sets(block, 1)[0]
    assert "error" not in rec and len(rec["members"]) == 3, rec
    assert render_sets(block, [rec]).startswith(
        "the tab bar (3 listed):\n\n- Home"), render_sets(block, [rec])
    cases += 1
    # A rendered block never runs into the bullet that follows it.
    run_on = "{{SET:x}}\n- a\n{{/SET}}\n- next claim\n"
    out = render_sets(run_on, find_sets(run_on, 1))
    assert "- a\n\n- next claim" in out, out
    cases += 1
    # A member that wraps is one member, not two.
    wrap = "{{SET:x}}\n- a long member that\n  wraps here\n- second\n{{/SET}}"
    assert len(find_sets(wrap, 1)[0]["members"]) == 2
    cases += 1
    # The label may not state the count -- that is the defect this token kills.
    assert find_sets("{{SET:all four filters}}\n- a\n{{/SET}}", 1)[0][
        "error"].startswith("E-SET-COUNT")
    cases += 1
    for broken, code in ((f"{{{{SET:x}}}}\n- a\n", "E-SET-UNCLOSED"),
                         ("{{SET:x}}\n\n{{/SET}}", "E-SET-EMPTY"),
                         ("{{SET:x}}\n- a\n{{SET:y}}\n- b\n{{/SET}}", "E-SET-NESTED"),
                         ("{{SET x}}\n- a\n", "E-SET-MALFORMED")):
        got = [s for s in find_sets(broken, 1) if "error" in s]
        assert got and got[0]["error"].startswith(code), (code, got)
    cases += 1
    # Rendering is a fixed point: a rendered block carries no token to re-render.
    once = render_sets(block, find_sets(block, 1))
    assert render_sets(once, find_sets(once, 1)) == once
    cases += 1
    # A stated count that introduces a real enumeration must match it.
    assert check_enumerations("three filters:\n- a\n- b\n- c\n", 1, "n.md") == []
    assert len(check_enumerations("four filters:\n- a\n- b\n- c\n", 1, "n.md")) == 1
    cases += 1
    # An inline comma list is NOT counted. Built, measured, cut: it fired 4
    # times on the corpus and all 4 were false -- these are the real lines.
    assert check_enumerations(
        "- two conditions: the toy stays on trend through\n"
        "  the holiday season, **and** the parent scales supply.\n", 1, "n.md") == []
    assert check_enumerations(
        "- same agent, two configs: 1,300-token system prompt vanilla vs\n"
        "  7,900 with web search and sub-agents added.\n", 1, "n.md") == []
    assert check_enumerations(
        "- the stated cost, first of four figures: the whole business\n", 1,
        "n.md") == []
    cases += 1
    # --- --refresh-sidecar separates "changed" from "gone" ---
    with tempfile.TemporaryDirectory() as td:
        # .resolve(): on macOS the temp dir is a symlink, and the sidecar's
        # inside-the-repo test compares resolved paths.
        r = Path(td).resolve()
        (r / "docs").mkdir()
        (r / "notes" / ".resolved").mkdir(parents=True)
        cited = r / "docs" / "x.md"
        cited.write_text("alpha\nthe quoted phrase\nbeta\n", encoding="utf-8")
        note = r / "notes" / "2026-01-01--n--VID.md"
        note.write_text('---\nvideo_id: VID\nduration: "1:00"\n---\n\nbody\n',
                        encoding="utf-8")
        side = r / "notes" / ".resolved" / "VID.tsv"
        side.write_text("# note\tn.md\n# path\tline\tsha256\tquote\n"
                        f"docs/x.md\t2\t{sha256(cited)}\tthe quoted phrase\n",
                        encoding="utf-8")
        assert check_sidecar(r, "video_id: VID", "n.md") == []
        # the cited file changes but still contains the quote -> refresh
        cited.write_text("new first line\nalpha\nthe quoted phrase\nbeta\n",
                         encoding="utf-8")
        assert check_sidecar(r, "video_id: VID", "n.md")[0].count("E-CITE-STALE")
        d, n = refresh_sidecar(r, note)
        assert (d, n) == ([], 1), (d, n)
        assert check_sidecar(r, "video_id: VID", "n.md") == []
        assert side.read_text(encoding="utf-8").split("\n")[2].split("\t")[1] == "3"
        # the quote is gone -> a defect, and the sidecar is left alone
        before = side.read_text(encoding="utf-8")
        cited.write_text("nothing like it here\n", encoding="utf-8")
        d, n = refresh_sidecar(r, note)
        assert n == 0 and d and "E-CITE-NOMATCH" in d[0], (d, n)
        assert side.read_text(encoding="utf-8") == before
    cases += 1

    # ...and a cardinality that enumerates nothing is not a set claim.
    assert check_enumerations("produced in under three minutes flat", 1, "n.md") == []
    assert check_enumerations("roughly every five years he does this", 1, "n.md") == []
    cases += 1

    # --- status and applied: agree, in both directions ---------------------
    assert check_status("status: distilled\napplied:\n", "n.md") == []; cases += 1
    assert check_status("status: applied\napplied: docs/x.md\n", "n.md") == []
    cases += 1
    got = check_status("status: distilled\napplied: docs/x.md\n", "n.md")
    assert len(got) == 1 and "E-STATUS-APPLIED" in got[0], got
    cases += 1
    got = check_status("status: applied\napplied:\n", "n.md")
    assert len(got) == 1 and "E-STATUS-NOEDGE" in got[0], got
    cases += 1
    assert "E-STATUS-UNKNOWN" in check_status("status: done\n", "n.md")[0]; cases += 1
    assert "E-STATUS-MISSING" in check_status("title: t\n", "n.md")[0]; cases += 1
    # `discarded` keeps the file on purpose and has no outbound edge to name.
    assert check_status("status: discarded\napplied:\n", "n.md") == []; cases += 1
    # An empty `applied:` must not read the NEXT line as its value. This fired on
    # 6 of 8 real notes before it was a case: every one "applied" `rating: 4`.
    assert check_status("status: distilled\napplied:\nrating: 4\n", "n.md") == []
    cases += 1
    assert check_status("applied:\nstatus: distilled\n", "n.md") == []; cases += 1
    # ...and neither may `reviews:`, which shipped with the same bug six lines
    # from the fix and was caught by re-enumerating the class, not the fix.
    assert lane_ids("reviews:\nstatus: applied\n") == ([], None); cases += 1
    assert lane_ids("reviews:\n[facts]\n") == ([], None); cases += 1
    assert parse_duration('duration:\n"1:00"\n') is None; cases += 1
    assert RE_VIDEO_ID.search("video_id:\nWRONG\n") is None; cases += 1
    # A phantom `applied:` path claims the same edge an empty one does.
    with tempfile.TemporaryDirectory() as td:
        r = Path(td).resolve()
        (r / "docs").mkdir()
        (r / "docs" / "real.md").write_text("x", encoding="utf-8")
        fm = "status: applied\napplied: docs/real.md\n"
        assert check_status(fm, "n.md", r) == []; cases += 1
        gone = check_status("status: applied\napplied: docs/ghost.md\n", "n.md", r)
        assert len(gone) == 1 and "E-STATUS-EDGE" in gone[0], gone
        cases += 1
        # ...and the repo's second convention, a path naming the repo itself,
        # resolves from the parent.
        (r.parent / "sibling_probe.md").write_text("x", encoding="utf-8")
        try:
            assert check_status(
                "status: applied\napplied: sibling_probe.md\n", "n.md", r) == []
            cases += 1
        finally:
            (r.parent / "sibling_probe.md").unlink()
        # No root, no resolution: the caller opted out, so it must not fire.
        assert check_status("status: applied\napplied: docs/ghost.md\n",
                            "n.md") == []; cases += 1

    # --- lane roll-call (R10) ---------------------------------------------
    assert lane_ids("title: t\n") == (None, None); cases += 1
    assert lane_ids("reviews: []\n") == ([], None); cases += 1
    assert lane_ids("reviews: [facts, quality]\n") == (["facts", "quality"], None)
    cases += 1
    # A key present and unreadable is a defect, not a silent skip -- a typo must
    # not disarm the check it was typed into.
    assert lane_ids("reviews: facts, quality\n")[1], "bare list accepted"
    cases += 1
    assert lane_ids("reviews: [Facts]\n")[1], "upper-case id accepted"; cases += 1
    assert lane_ids("reviews: [facts, facts]\n")[1], "duplicate id accepted"
    cases += 1
    with tempfile.TemporaryDirectory() as td:
        r = Path(td).resolve()
        d = r / "notes" / "reviews" / "VID"
        d.mkdir(parents=True)
        (d / "n-note-review-facts.md").write_text("x", encoding="utf-8")
        fm = "video_id: VID\nreviews: [facts]\n"
        assert check_lanes(r, fm, "n.md") == [], check_lanes(r, fm, "n.md")
        cases += 1
        # exact name, not just the -suffix form
        (d / "n-note-review-facts.md").rename(d / "facts.md")
        assert check_lanes(r, fm, "n.md") == []; cases += 1
        # ...and the dash is load-bearing: `nonfacts.md` is not lane `facts`.
        (d / "facts.md").rename(d / "nonfacts.md")
        got = check_lanes(r, fm, "n.md")
        assert any("E-LANE-MISSING" in g for g in got), got
        assert any("E-LANE-UNDECLARED" in g for g in got), got
        cases += 1
        (d / "nonfacts.md").rename(d / "facts.md")
        # a report nobody declared
        (d / "n-review-quality.md").write_text("x", encoding="utf-8")
        got = check_lanes(r, fm, "n.md")
        assert len(got) == 1 and "E-LANE-UNDECLARED" in got[0], got
        cases += 1
        # two files for one id: which one is the verdict?
        fm2 = "video_id: VID\nreviews: [facts, quality]\n"
        assert check_lanes(r, fm2, "n.md") == []; cases += 1
        (d / "b-review-quality.md").write_text("x", encoding="utf-8")
        got = check_lanes(r, fm2, "n.md")
        assert len(got) == 1 and "E-LANE-AMBIGUOUS" in got[0], got
        cases += 1
        (d / "b-review-quality.md").unlink()
        # one file answering to two ids hides a lane that never emitted
        fm3 = "video_id: VID\nreviews: [facts, quality, review-quality]\n"
        got = check_lanes(r, fm3, "n.md")
        assert any("E-LANE-SHARED" in g for g in got), got
        cases += 1
        # a declared lane with no report at all
        fm4 = "video_id: VID\nreviews: [facts, quality, coverage]\n"
        got = check_lanes(r, fm4, "n.md")
        assert len(got) == 1 and "E-LANE-MISSING" in got[0] and "coverage" in got[0]
        cases += 1
        # ...unless it is dated in LOST_REVIEWS, and only for ITS video id.
        # The second note is stocked identically, so the ONLY thing that can
        # separate the two verdicts is the video id the exemption is filed under.
        other = r / "notes" / "reviews" / "OTHER"
        other.mkdir(parents=True)
        for name in ("facts.md", "n-review-quality.md"):
            (other / name).write_text("x", encoding="utf-8")
        LOST_REVIEWS["VID"] = {"coverage": "2026-01-01 test"}
        try:
            assert check_lanes(r, fm4, "n.md") == []
            cases += 1
            got = check_lanes(r, fm4.replace("VID", "OTHER"), "n.md")
            assert len(got) == 1 and "E-LANE-MISSING" in got[0], got
            cases += 1
        finally:
            del LOST_REVIEWS["VID"]
        # no declaration and no reports on disk is the ordinary case
        assert check_lanes(r, "video_id: NONE\n", "n.md") == []; cases += 1
        # A report one directory deeper was invisible to the roll-call, and
        # filing by date under the video id is the obvious thing a future run
        # does (slice-13 coverage lane): `mkdir` was the whole dodge.
        (d / "2026-08-06").mkdir()
        (d / "2026-08-06" / "n-review-buried.md").write_text("x", encoding="utf-8")
        got = check_lanes(r, fm, "n.md")
        assert any("E-LANE-UNDECLARED" in g and "buried" in g for g in got), got
        cases += 1
        assert check_lanes(r, "video_id: VID\nreviews: [facts, quality, buried]\n",
                           "n.md") == []
        cases += 1

    # --- the CLI actually reaches both new checks --------------------------
    # Every case above calls the predicate directly. Deleting the ONE line that
    # wires it into check_note left the whole suite green, in both files, which
    # two independent review lanes filed against slice 13. These cases drive
    # argparse end to end against a fixture root instead.
    with tempfile.TemporaryDirectory() as td:
        r = Path(td).resolve()
        (r / "notes" / "reviews" / "WIRED").mkdir(parents=True)
        note = r / "notes" / "2026-01-01--wired--WIRED.md"
        clean = ('---\nvideo_id: WIRED\nduration: "1:00"\nstatus: distilled\n'
                 'applied:\n---\n\n# t\n\nA body with no declaration in it.\n')
        note.write_text(clean, encoding="utf-8")
        assert main(["--check", "--no-require-density", str(note)], root=r) == 0
        cases += 1
        # a declared lane with no report must reach the exit code through main()
        note.write_text(clean.replace("applied:\n",
                                      "applied:\nreviews: [ghost]\n"),
                        encoding="utf-8")
        assert main(["--check", "--no-require-density", str(note)], root=r) == 1
        cases += 1
        # ...and so must a status/applied mismatch
        note.write_text(clean.replace("applied:\n", "applied: docs/x.md\n"),
                        encoding="utf-8")
        assert main(["--check", "--no-require-density", str(note)], root=r) == 1
        cases += 1
    print(f"selftest OK ({cases} cases)")
    return 0


def main(argv: list[str], root: Path | None = None) -> int:
    # `root` is a parameter so the selftest can drive the real CLI against a
    # fixture tree. The slice-13 conformance lane deleted the `check_lanes` and
    # `check_status` calls out of `check_note` one at a time and all 64 cases
    # stayed green: every case called the predicates directly, so the suite
    # proved the predicates and proved nothing about them being reached.
    root = root or POLICY.root(fallback=Path(__file__).resolve().parent.parent)
    ap = argparse.ArgumentParser(description="count and check note density")
    ap.add_argument("paths", nargs="*", type=Path)
    ap.add_argument("--check", action="store_true", help="report defects (default)")
    ap.add_argument("--report", action="store_true", help="TSV of counted values")
    ap.add_argument("--write", action="store_true",
                    help="render resolved {{CITE}} tokens and write the sidecar")
    ap.add_argument("--no-require-density", dest="require_density",
                    action="store_false",
                    help="stop treating a note with no declared density as a "
                         "defect (on by default; all eight notes declare one)")
    ap.add_argument("--refresh-sidecar", action="store_true",
                    help="re-resolve recorded quotes and rewrite their sha256")
    ap.add_argument("--no-floor", dest="floor", action="store_false",
                    help="skip the untokenised-path floor (on by default)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    files = collect([p.resolve() for p in args.paths]
                    or [root / POLICY.notes_dir()])

    if args.refresh_sidecar:
        defects, refreshed = [], 0
        for f in files:
            d, n = refresh_sidecar(root, f)
            defects.extend(d)
            refreshed += n
        for line in defects:
            print(line)
        print(f"# {len(files)} notes, {refreshed} sidecar row(s) refreshed, "
              f"{len(defects)} defects", file=sys.stderr)
        return 1 if defects else 0

    if args.write:
        defects, written = [], 0
        for f in files:
            d, n = write_note(f, root)
            defects.extend(d)
            written += n
        for line in defects:
            print(line)
        print(f"# {len(files)} notes, {written} citations rendered, "
              f"{len(defects)} defects", file=sys.stderr)
        return 1 if defects else 0

    defects: list[str] = []
    rows: list[dict] = []
    for f in files:
        d, stats = check_note(f, root, args.require_density, floor=args.floor)
        defects.extend(d)
        if stats:
            rows.append(stats)
    defects += orphan_sidecars(root, files)

    # The peer band is a property of the corpus, so it can only be checked once
    # every note has been counted.
    wpms = sorted(r["wpm"] for r in rows if r["wpm"] is not None)
    band = (wpms[0], wpms[-1]) if wpms else None
    declared = 0
    for f in files:
        text, _why = safe_read(f)
        split = split_frontmatter(text) if text else None
        if not split:
            continue
        rel = f.relative_to(root) if f.is_relative_to(root) else f
        defects += check_band(split[1], band, rel)
        if find_declarations(*normalise(split[1], 1)):
            declared += 1

    if args.report:
        print("file\twords\tseconds\tminutes\twpm")
        for r in rows:
            mins = "-" if r["minutes"] is None else f"{r['minutes']:.2f}"
            wpm = "-" if r["wpm"] is None else f"{r['wpm']:.1f}"
            print(f"{r['file']}\t{r['words']}\t{r['seconds']}\t{mins}\t{wpm}")
        band = sorted(r["wpm"] for r in rows if r["wpm"] is not None)
        if band:
            print(f"# {len(rows)} notes, wpm band {band[0]:.0f}-{band[-1]:.0f}")
        # --report used to compute every defect and throw it away, so the same
        # corpus read as clean here and dirty under --check.
        for line in defects:
            print(line, file=sys.stderr)
        print(f"# {len(defects)} defects", file=sys.stderr)
        return 1 if defects else 0

    for line in defects:
        print(line)
    # An exemption nobody sees is an exemption nobody removes. Same promise as
    # anchor_manifest's UNRESOLVABLE_RUNS: printed on every ordinary run.
    for vid, lanes in sorted(LOST_REVIEWS.items()):
        for lane, why in sorted(lanes.items()):
            print(f"# lane {vid}/{lane} exempt from the roll-call [{why}]",
                  file=sys.stderr)
    # "0 defects" over 8 notes used to read as "8 notes verified" when only the
    # 2 carrying a declaration were ever tested. Say how many were testable.
    print(f"# {len(files)} notes checked, {declared} with a declared density, "
          f"{len(defects)} defects", file=sys.stderr)
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
