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
import importlib
import tempfile
import unicodedata
from importlib import metadata
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
# A citation of INSTALLED code rather than of a file in this repo:
# `{{CITE:watch-quality@0.1.0:ocr_vote.py#"TEXTFUL_WORDS = 5"}}`.
#
# A repo-relative path cannot express "the check that graded this note", because
# the checks are a versioned package that lives somewhere else now. Pointing the
# citation at a path was silently wrong the moment the gates moved: the sha
# would drift with every unrelated release, and a re-grade months later could
# not say which version it was comparing against. The version is part of the
# address, so a citation resolved against 0.1.0 REFUSES to resolve against 0.2.0
# rather than quietly resolving to different code.
RE_PKG_SPEC = re.compile(r"^([A-Za-z0-9._-]+)@([A-Za-z0-9._+!-]+):(\S+)$")
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
# The same date, captured: a dated exemption may only excuse the notes that
# already existed when it was written.
RE_NOTE_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})--")
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
# An indented `- item` under a key: block-style YAML, which this parser does
# not read and therefore has to REFUSE rather than mistake for an empty value.
RE_BLOCK_ITEM = re.compile(r"^[ \t]+-[ \t]+\S")
# Which build of the checks last passed this note.
#
# "0 defects" is a claim about a moment, and without this it is a claim about an
# unknown moment. A check can be tightened, a threshold moved, a bug fixed --
# and every note keeps its old clean bill of health, because nothing in the note
# says which version issued it. The stamp is written only by `--stamp`, only on
# a note that is clean at the time, so it means "these checks, at this version,
# passed" and cannot be back-dated by editing prose.
DIST_NAME = "watch-quality"
# Named here rather than spelled out at each site. It was already referenced by
# the `--stamp` branch and never defined, which nothing noticed because that
# branch only runs on a machine where the package is NOT installed -- an error
# path with no case, raising NameError instead of saying what was wrong.
PROG = "resolve_note.py"
# `watch-audit` runs this module as a gate, with these flags. It is declared
# here, beside the flag's own handling, rather than only in `audit.GATES`: a
# roster the suite reads off one list is a roster one edit can shorten, which
# is how a whole gate was deleted with both suites green. The order the audit
# runs them in is not declared anywhere -- it is derived from which gate reads
# which, so a gate cannot be moved above the one it imports.
GATE_FLAGS: tuple[str, ...] = ("--check",)
RE_GRADED = re.compile(r"^graded_with:[ \t]*(\S*)[ \t]*$", re.MULTILINE)
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
# The lanes this corpus makes mandatory, and the notes excused from one. A list,
# not a tuple, because the selftest has to be able to set it: a check whose only
# configuration is a file nobody edits during a test run is a check with no
# failing case, and this one exists precisely to have one.
REQUIRED_LANES: list[str] = list(POLICY.required_lanes())
UNREVIEWED_NOTES: dict[str, dict[str, str]] = POLICY.unreviewed_notes()
# Runs this corpus has written off, keyed by video id. `anchor_manifest` already
# owns this table and already ages it; the oracle check reads the SAME rows
# rather than opening a fourth ledger, because "the run behind this video is
# gone" is one fact and two tables recording it would drift apart and then cover
# for each other.
UNRESOLVABLE_RUNS: dict[str, str] = POLICY.unresolvable_runs()
# Notes whose `oracle:` names no rendering a machine can open, keyed by note
# filename and dated. This is the grandfather list for a field that was prose
# until 2026-08-21; `excused()` will not let a row reach a note filed after the
# row's own date, so the list can only shrink.
UNFILLED_ORACLES: dict[str, str] = POLICY.unfilled_oracles()
# Notes the per-note checks may not grade, keyed by corpus-relative path and
# dated. `note_gates` owns the checking; the copy here exists so this module's
# stale census can say which of these rows has stopped excusing anything.
UNGRADED_NOTES: dict[str, str] = POLICY.ungraded_notes()
# The note's own declaration of which rendering it was written against, as
# opposed to `RE_ORACLE` above, which is the character class a LANE REPORT's
# oracle field has to satisfy. Two different fields, two different owners.
RE_NOTE_ORACLE = re.compile(r"^oracle:[ \t]*(.*)$", re.MULTILINE)

# --- the header a lane report has to carry --------------------------------
# A FILE NAMED FOR A LANE IS NOT A REVIEW. Two independent review lanes found
# the same door on 2026-08-20: three zero-byte files bought a stamped note and
# `# all 5 gate(s) passed`, and a directory called `quality.md`, a symlink to
# /dev/null and a lane declared `facts-deferred` did the same. The roll-call
# graded a filename because nothing in this package ever opened a report.
#
# The repair is the mechanism this repository already trusts. The `.resolved`
# sidecar binds every citation to a path, a line and a sha256, and re-checks it
# on every run; this points the same idea at reviews. A report opens with a
# small block naming the note BODY it read, the rendering it re-derived
# against, the lane it answers for, its verdict, and how many claims it
# enumerated. `unstated` is legal for both of the last two, because a lane that
# did not count, or that reported findings without ruling on the artifact, must
# be able to SAY so -- being made to supply a number or a verdict it never
# reached is the model-authored literal this whole package exists to kill. What
# is not legal is a value outside the vocabulary, which is neither a claim nor
# an admission, only an unreadable string in the field a reader acts on.
LANE_HEADER_FIELDS = ("note_sha256", "oracle", "lane", "verdict",
                      "claims_enumerated")
UNSTATED = "unstated"
# The one verdict that is a refusal. Named rather than spelled out at the two
# places that read it, so the vocabulary and the consequence cannot drift.
BLOCK = "BLOCK"
LANE_VERDICTS = ("SHIP", "SHIP-WITH-FIXES", BLOCK, UNSTATED)
RE_HEADER_ROW = re.compile(r"^([a-z0-9_]+):[ \t]*(.*)$")
RE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# A path names a file. The class is POSITIVE -- these characters and no others
# -- because the alternative is a list of the characters somebody thought to
# refuse, and that list has no last entry: `""`, then `" "`, then U+200B, then
# the next one. `.strip()` is a proxy for "has a visible glyph" and U+2800
# (braille blank, category So) is exactly where the proxy and the property come
# apart; a positive class does not care which invisible character was invented.
RE_ORACLE = re.compile(r"^[A-Za-z0-9._~@:+/-]+$")
# NOT `str.isdigit()`: that is true of `²` and of `١٢`, both of which `int()`
# refuses. A claim count is a number a reader compares against a list.
RE_COUNT = re.compile(r"^[0-9]+$")
# Video ids whose reports were filed before the header existed. Same shape and
# same promise as LOST_REVIEWS: dated, reasoned, printed, may only shrink. It
# excuses the header and nothing else -- the report still has to exist, still
# has to be a file, and still has to be the only one answering to its lane.
UNHEADERED_REVIEWS: dict[str, str] = POLICY.unheadered_reviews()


# How a value says "this field has no value". `null`, `~` and empty are YAML's
# own, in both 1.1 and 1.2; `none` and `nil` are NOT in either spec and are here
# because a human who writes one means the same thing and no reader would call
# the field filled in.
#
# `~` was already refused for carrying no letter or digit, which read as a fix
# for the class and was a fix for one spelling of it: the rest are WORDS, and a
# word satisfies any character class (closeout). Matched whole and
# case-insensitively, so `runs/nullify/run.json` is still a path.
#
# THIS IS A FLOOR, NOT THE FIX. `n/a`, `TODO`, `unknown`, `x` and `0` are all
# still path-shaped and all still pass, because no string rule can make a value
# MEAN something. The only thing that can is giving the field a consequence --
# resolving the oracle, and letting a lane's verdict decide an exit code.
YAML_NULLS = frozenset({"null", "none", "nil", "~", ""})


def _path_shaped(value: str) -> bool:
    """A path names a file, so it needs at least one letter or digit.

    `-`, `|`, `>` and `/` are YAML punctuation rather than names, and a value
    that is only YAML's word for nothing names nothing either. A field whose
    value says the field has no value is a field that is not there, and it
    reaches a reader as a report DECLARING an oracle it does not have.
    """
    if value.casefold() in YAML_NULLS:
        return False
    return bool(RE_ORACLE.match(value)) and any(
        c.isascii() and c.isalnum() for c in value)


# WHAT EACH FIELD MUST BE, one rule per field, and the rule is a TYPE.
#
# Four rounds of review each closed the bad values the previous reviewer had
# named, and each produced four or more new survivors, because "reject the
# values somebody thought of" is a blacklist and a blacklist has no fixed
# point. Round 4's rule refused blank and whitespace; U+200B walked through it
# and rendered on screen as `oracle: ""`. A type does have a fixed point: the
# fourteenth spelling nobody has written down is refused unread, because it is
# still not 64 hex characters, still not a path, still not a lane id, still not
# a member of the vocabulary and still not a number.
#
# `unstated` is legal for the last two and is part of the type, not an escape
# from it: a lane that did not count, or that reported findings without ruling
# on the artifact, must be able to SAY so -- being made to supply a number it
# never reached is the model-authored literal this package exists to kill.
LANE_HEADER_TYPES: dict[str, tuple[object, str]] = {
    "note_sha256": (RE_SHA256.match, "64 lowercase hex characters"),
    "oracle": (_path_shaped, "a path"),
    "lane": (RE_LANE_ID.match, "a lane id, the same [a-z0-9][a-z0-9-]* a note "
                               "declares in reviews:"),
    "verdict": (lambda v: v in LANE_VERDICTS,
                "one of " + ", ".join(LANE_VERDICTS)),
    "claims_enumerated": (lambda v: v == UNSTATED or RE_COUNT.match(v),
                          f"a count of claims or {UNSTATED!r}"),
}


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


def package_target(spec: str) -> tuple[Path | None, str]:
    """Resolve `name@version:relpath` against the INSTALLED distribution.

    Returns (path, "") or (None, error). Every failure is a distinct code
    because they mean different things to whoever reads the defect:

      E-CITE-NOPKG      the package is not installed, so the claim is
                        uncheckable here -- not false.
      E-CITE-VERSION    a DIFFERENT version is installed. This is the whole
                        point of putting the version in the address: the quote
                        might still be present in the new one, at a new line,
                        meaning something else. Refusing is the honest answer.
      E-CITE-NOFILE     that version does not ship that file.
    """
    m = RE_PKG_SPEC.match(spec)
    if not m:
        return None, f"E-CITE-PKGSPEC not name@version:path: {spec}"
    name, want, rel = m.group(1), m.group(2), m.group(3)
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return None, f"E-CITE-NOPKG {name} is not installed"
    if dist.version != want:
        return None, (f"E-CITE-VERSION {name} {dist.version} is installed, the "
                      f"citation was resolved against {want}")
    for entry in dist.files or ():
        if str(entry) == rel or str(entry).endswith("/" + rel):
            path = Path(dist.locate_file(entry))
            if path.is_file():
                return path, ""
    # An EDITABLE install records a loader shim in its file list, not the
    # modules, so the loop above finds nothing for exactly the case a developer
    # is in most often. Ask the import system where the package actually is.
    for top in _top_levels(dist, name):
        try:
            mod = importlib.import_module(top)
        except ImportError:
            continue
        for base in getattr(mod, "__path__", ()) or ():
            path = Path(base) / rel
            if path.is_file():
                return path, ""
    return None, f"E-CITE-NOFILE {rel} is not in {name} {dist.version}"


def _top_levels(dist, name: str) -> list[str]:
    """Import names this distribution provides, best effort.

    `top_level.txt` when the build backend wrote one, else the two obvious
    manglings of the distribution name -- `watch-quality` installs as
    `watchquality`, and plenty of others as `watch_quality`.
    """
    text = None
    try:
        text = dist.read_text("top_level.txt")
    except (OSError, AttributeError):
        pass
    names = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for guess in (name.replace("-", ""), name.replace("-", "_")):
        if guess not in names:
            names.append(guess)
    return names


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
        if RE_PKG_SPEC.match(rec["path"]):
            target, why = package_target(rec["path"])
            if target is None:
                rec["error"] = why
                continue
        else:
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

    A CARDINALITY INSIDE A LIST ITEM ENUMERATES ONLY A NESTED LIST. This check
    shipped counting every item that followed, at any indent, and on a note whose
    body IS one long list that is every remaining row:

        n.md:69  declared two examples but 189 listed
        n.md:124 declared two mindsets but 134 listed
        n.md:149 declared both ways but 5 listed
        (and, on an earlier note, "three wishes:" -> 5, "two words:" -> 403)

    Five false positives across three recordings, every one a claim row of the
    shape "the seller holds two mindsets: knowing the thing, and forgetting he
    knows it", with the note's next rows read as its members. Siblings are not
    its enumeration; only items indented UNDER it are. Prose keeps the old
    behaviour, because a paragraph ending in "three filters:" really is
    introducing the list at the left margin below it.

    The rule the corpus learned the hard way, twice: a gate that fires on an
    idiom teaches its reader to reword notes, and a note reworded to satisfy a
    gate is no longer evidence of anything.
    """
    out = []
    lines = body.split("\n")
    for i, line in enumerate(lines):
        inside_item = RE_ITEM_ANY.match(line)
        indent = len(line) - len(line.lstrip()) if inside_item else -1
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
                if RE_ITEM_ANY.match(lines[j]):
                    deeper = len(lines[j]) - len(lines[j].lstrip()) > indent
                    if inside_item and not deeper:
                        break            # a sibling row, not a member
                    items += 1
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
        if RE_PKG_SPEC.match(cited):
            # An installed dependency is deliberately OUTSIDE the repo, so the
            # escaping-path rule below must not fire on it. The version in the
            # address is what keeps that safe: a package citation can only
            # resolve against the exact version it was resolved against.
            target, why = package_target(cited)
            if target is None:
                defects.append(f"{rel}:1 {why}")
                continue
        else:
            target = (root / cited).resolve()
            if not target.is_relative_to(root):
                defects.append(f"{rel}:1 E-SIDECAR-ROW {side.name}:{n} points "
                               f"outside the repo: {cited}")
                continue
            if not target.is_file():
                defects.append(f"{rel}:1 E-CITE-STALE cited file gone: {cited}")
                continue
        if sha256(target) != sha:
            defects.append(f"{rel}:1 E-CITE-STALE {cited} changed since "
                           f"resolution at line {line}")
    return defects


def refresh_sidecar(root: Path, note: Path,
                    only: set[str] | None = None) -> tuple[list[str], int]:
    """Re-resolve a sidecar's recorded quotes and rewrite line and sha256.

    E-CITE-STALE conflates two very different states: the cited file changed
    and still contains the quote, or the quote is gone. Once a token has been
    rendered there is nothing left to re-run, so a legitimate edit to a cited
    note used to leave a permanent red light -- which is how a check gets
    switched off. This separates them: a quote that still resolves refreshes,
    a quote that does not stays a defect and nothing is written.

    `only` restricts the refresh to rows citing those paths. `--stamp` uses it
    to repair the rows its own writes invalidated, without also papering over
    staleness that has nothing to do with this run.
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
        if only is not None and cited not in only:
            # Refreshing a row nobody asked about would rewrite a sha that went
            # stale for a reason the caller has not seen yet, turning a defect
            # into a silent update. `only` is how --stamp repairs exactly the
            # rows IT invalidated and nothing else.
            rows.append(row)
            continue
        if RE_PKG_SPEC.match(cited):
            target, why = package_target(cited)
            if target is None:
                # A version mismatch is NOT refreshable. Re-resolving the quote
                # against a different release would silently move the citation
                # to code the note was never graded by, which is the one thing
                # putting the version in the address is meant to prevent.
                defects.append(f"{rel}:1 {why}")
                rows.append(row)
                continue
        else:
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


def current_stamp() -> str | None:
    """`watch-quality@0.1.0`, or None when the package is not installed."""
    try:
        return f"{DIST_NAME}@{metadata.version(DIST_NAME)}"
    except metadata.PackageNotFoundError:
        return None


def check_grade(frontmatter: str, rel) -> list[str]:
    """Is this note's clean bill of health the CURRENT one?

    Three states, and they are not the same defect:

      unstamped   nothing says which build passed this note. Reported, because
                  an unattributed pass is what this whole field exists to end.
      stale       a different version is installed than the one that passed it.
                  The note is not wrong; its verdict is simply out of date, and
                  saying so is the difference between comparable and quietly
                  incomparable.
      uninstallable  the package is not installed, so nothing can be compared.
                  Silent -- every other check is already failing loudly by then.
    """
    now = current_stamp()
    if now is None:
        return []
    m = RE_GRADED.search(frontmatter)
    if not m or not m.group(1):
        return [f"{rel}:1 E-GRADE-UNSTAMPED no graded_with:, so nothing says "
                f"which build passed this note (--stamp records {now})"]
    if m.group(1) != now:
        return [f"{rel}:1 E-GRADE-STALE passed by {m.group(1)}, but {now} is "
                f"installed; re-check and --stamp to make it comparable"]
    return []


def stamp_note(path: Path, root: Path, stamp: str,
               require_density: bool = True,
               floor: bool = True) -> tuple[list[str], bool]:
    """Record `graded_with:` on a note that is CLEAN right now.

    Refusing to stamp a note with defects is the whole integrity of the field.
    A stamp on a red note would read, months later, as "this version passed it",
    which is the exact false light every gate here exists to remove.

    THE STAMP HAS TO GRADE WITH THE CHECKER `--check` GRADES WITH. It called
    `check_note(path, root, False)` -- density off, floor off -- so `--stamp`
    wrote a clean bill onto a note that the same build called defective one
    command later (mechanism F16). The two flags are parameters rather than
    constants so `--stamp --no-require-density` still means what it says, and
    so a weaker stamp has to be asked for by name instead of being what you
    get by default.
    """
    rel = path.relative_to(root) if path.is_relative_to(root) else path
    defects, _ = check_note(path, root, require_density, floor=floor)
    defects = [d for d in defects if "E-GRADE-" not in d]
    if defects:
        return [f"{rel}:1 E-STAMP-REFUSED {len(defects)} defect(s) outstanding; "
                f"a stamp on a red note is a false clean bill"], False
    text, why = safe_read(path)
    if text is None:
        return [f"{rel}:1 E-READ {why}"], False
    split = split_frontmatter(text)
    if split is None:
        return [f"{rel}:1 E-FRONTMATTER no YAML frontmatter block"], False
    frontmatter, body = split
    if RE_GRADED.search(frontmatter):
        if RE_GRADED.search(frontmatter).group(1) == stamp:
            return [], False
        new_fm = RE_GRADED.sub(f"graded_with: {stamp}", frontmatter, count=1)
    else:
        # Last field in the block, so an existing note's frontmatter keeps the
        # order a reader already knows.
        new_fm = frontmatter.rstrip("\n") + f"\ngraded_with: {stamp}"
    path.write_text(f"---\n{new_fm}\n---\n{body}", encoding="utf-8")
    return [], True


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
    cite_defects += check_required_lanes(frontmatter, rel)
    cite_defects += check_lanes(root, frontmatter, rel, body)
    cite_defects += check_status(frontmatter, rel, root)
    cite_defects += check_oracle(frontmatter, rel, root)
    cite_defects += check_grade(frontmatter, rel)
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

    Four bases, because a corpus tends to grow conventions and write none of
    them down: an absolute path with a tilde (`~/dev/.../file.md`), one that
    starts with the repository's own directory name, which resolves relative to
    the repo's PARENT because the note names the repo, and repo-root-relative as
    the form a reader would guess.

    THE FOURTH IS THE ONE THAT SURVIVES A MOVE. A value naming the repository
    resolved only while the checkout's basename was still that word: the base is
    the parent, and the parent plus that name is the repo again. Copy the corpus
    to any other directory and the same note becomes a phantom -- two of them
    did, and every exit-0 line in a long review chain had been measured in the
    one directory where the coincidence holds. So a leading component is dropped
    and the rest tried under the root: the note names the repository, which is
    not the same fact as what the directory is called today.

    Dropping it is not a licence. The remainder still has to exist under the
    root, so a value under no base at all is a phantom whatever it is prefixed
    with.
    """
    p = Path(value).expanduser()
    bases = [p, root / p, root.parent / p]
    if len(p.parts) > 1 and not p.is_absolute():
        bases.append(root.joinpath(*p.parts[1:]))
    for cand in bases:
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
    status = header_value(m.group(1))
    if status not in STATUSES:
        return [f"{rel}:1 E-STATUS-UNKNOWN status {status!r} is not one of "
                f"{', '.join(STATUSES)}"]
    a = RE_APPLIED.search(frontmatter)
    applied = (header_value(a.group(1)) if a else "")
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
    # A trailing YAML comment is legal on a flow sequence, and rejecting it
    # called a correct declaration malformed (properties I2). Safe to cut
    # before parsing because a quote inside the brackets is refused below, so
    # no `#` can be inside a value.
    if "#" in raw:
        raw = raw.split("#", 1)[0].strip()
    if not raw:
        # The key with no value declares no lanes, like `[]` -- UNLESS the
        # value is on the following lines. Block style is legal YAML declaring
        # real lanes, and it parsed as zero lanes under a comment saying the
        # key with no value declares none: present, unread, and silent, which
        # is the one thing the docstring above promises cannot happen
        # (properties I1).
        rest = frontmatter[m.end():].split("\n")
        if len(rest) > 1 and RE_BLOCK_ITEM.match(rest[1]):
            return None, ("reviews: block-style list is not read; write it as "
                          "a flow sequence, reviews: [facts, quality]")
        return [], None
    if not raw.startswith("[") or not raw.endswith("]"):
        return None, f"reviews: expected a bracketed list, got {raw!r}"
    inner = raw[1:-1].strip()
    if not inner:
        return [], None
    # A quote inside the brackets let one quoted item split on its own comma
    # and manufacture two lane ids from one (properties I1). A lane id is
    # `[a-z0-9-]+` and never needs quoting, so a quote here is always either a
    # mistake or a trap.
    if '"' in inner or "'" in inner:
        return None, f"reviews: a lane id is never quoted, got {inner!r}"
    ids = [p.strip() for p in inner.split(",")]
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
    #
    # `is_file()`, not truth: rglob yields directories too, so `mkdir
    # quality.md` satisfied a lane, and a symlink to /dev/null satisfied one
    # because it is not a regular file either. Both are one `mkdir` and one
    # `ln -s` from any tired agent (mechanism F2, F2b).
    d = root / REVIEW_DIR / video_id
    return sorted(p for p in d.rglob("*.md") if p.is_file()) if d.is_dir() else []


def note_body_sha256(body: str) -> str:
    """The hash a lane report pins itself to.

    Machine marks come off first, for the reason the word count takes them off:
    a demotion render is not new prose, and letting it move the hash would turn
    every report on a note red for bytes no author wrote. What is left is what
    a reader read.
    """
    return hashlib.sha256(
        strip_machine_marks(body).strip().encode("utf-8")).hexdigest()


def _invisible(ch: str) -> bool:
    """Does this character leave no mark on the page?

    `str.strip()` knows about whitespace and nothing else, so U+200B (zero
    width space), U+FEFF (a BOM an editor left behind) and U+00AD (soft hyphen)
    survived it and made `oracle: ""` into a field with a value. They are all
    category Cf; the control and separator categories join them here so the
    question asked is "is anything visible" rather than "is it one of the
    characters somebody listed".
    """
    return ch.isspace() or unicodedata.category(ch) in (
        "Cc", "Cf", "Zs", "Zl", "Zp")


def header_value(raw: str) -> str:
    """NORMALISE a header row's value. Once, in one place, before any type.

    This does not decide validity -- `LANE_HEADER_TYPES` does. It removes the
    three things that change how a value LOOKS without changing what it says,
    so that the type rule sees the same string a reader sees:

      1. compatibility forms, via NFKC, so a full-width digit is a digit;
      2. invisible characters at either end, whitespace or not;
      3. surrounding quotes, repeatedly, inside out.

    The steps run to a fixed point because each can expose work for the others:
    `"<U+200B>"` is quotes around an invisible, `"" ""` is quotes around
    whitespace around quotes.

    A value that is nothing but quote characters is nothing: an odd number of
    them peels down to a lone `"`, which is a quotation mark a reader cannot
    act on, not a value. That case is here rather than in five type rules
    because it is a property of quoting, not of any field.

    The other direction is load-bearing: a value that survives is returned
    unquoted, so `verdict: "SHIP"` is still `SHIP` and a CRLF report still
    parses. Refusing anything with a quote in it would close the class by
    breaking every legally quoted report.
    """
    value = unicodedata.normalize("NFKC", raw)
    while True:
        before = value
        while value and _invisible(value[0]):
            value = value[1:]
        while value and _invisible(value[-1]):
            value = value[:-1]
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value == before:
            break
    return "" if all(c in "\"'" for c in value) else value


def lane_header(path: Path) -> tuple[dict[str, str] | None, str | None]:
    """Read a report's machine-checked header. Returns (fields, error).

    Every rejection here is a report that would otherwise have been counted as
    a review on the strength of its filename. The parse is deliberately strict
    about the two fields a reader would act on -- the verdict and the claim
    count -- because a value outside the vocabulary is not a smaller claim, it
    is an unreadable one.
    """
    text, why = safe_read(path)
    if text is None:
        return None, why
    split = split_frontmatter(text)
    if split is None:
        return None, ("no header block; a report opens with --- and the five "
                      f"fields {', '.join(LANE_HEADER_FIELDS)}")
    fields: dict[str, str] = {}
    for line in split[0].split("\n"):
        m = RE_HEADER_ROW.match(line)
        if m:
            # EVERY row is recorded, blank included, so that YAML's last-wins
            # holds. Skipping blank rows let a duplicate `oracle:` whose second
            # copy is empty keep the first copy's value: the effective value of
            # that field is blank and the parser read the stale one.
            fields[m.group(1)] = header_value(m.group(2))
    for field in LANE_HEADER_FIELDS:
        value = fields.get(field, "")
        is_a, must_be = LANE_HEADER_TYPES[field]
        if not is_a(value):
            # A field that normalises to nothing fails here, with every other
            # bad value, because it cannot BE its type -- not under a separate
            # blank rule. The message just reads better when there is nothing
            # to quote back.
            return None, (f"header has no {field}" if not value else
                          f"{field} is not {must_be}: {value!r}")
    return fields, None


def lane_matches(reports: list[Path], lane: str) -> list[Path]:
    return [p for p in reports
            if p.name == f"{lane}.md" or p.name.endswith(f"-{lane}.md")]


def satisfies(declared: list[str], required: str) -> bool:
    """Does any declared lane cover this required one, family included?

    `coverage-spoken` and `coverage-frames` are how one note ran the coverage
    lane after splitting it, and demanding the bare id would call that note
    unreviewed. The dash is load-bearing exactly as it is in `lane_matches`:
    `coverageless` is a different lane, not a longer spelling of this one.
    """
    return any(d == required or d.startswith(f"{required}-") for d in declared)


def covered_lanes(declared: list[str], required: list[str]) -> set[str]:
    """Which required lanes these declarations actually cover.

    ONE DECLARATION MAY NOT CLEAR TWO REQUIRED LANES. The family rule stays --
    `coverage-spoken` is how one note ran the coverage lane after splitting it,
    and demanding the bare id would call that note unreviewed. But when a corpus
    requires both `quality` and `quality-deep`, `satisfies` said yes to both for
    a single declared `quality-deep`, so one id cleared a two-lane floor and
    nothing said the `quality` lane never ran (properties I6). Each declaration
    is spent on the LONGEST required lane it covers, which is the one it names
    most precisely; the shorter lane is then short a declaration, out loud.
    """
    out: set[str] = set()
    for d in declared:
        candidates = [r for r in required if satisfies([d], r)]
        if candidates:
            out.add(max(candidates, key=len))
    return out


def note_date(rel) -> str | None:
    """The ISO date in a note's filename, or None when it has none."""
    m = RE_NOTE_DATE.match(Path(rel).name)
    return m.group(1) if m else None


def excused(reason: str | None, rel) -> bool:
    """Does this dated exemption row reach the note in front of it?

    Every debt row is keyed by video id, and two notes about one video share
    it, so a note written after the debt was recorded was BORN EXCUSED --
    re-watching a video already on the ledger being the single most likely
    reason a second note exists. `watch-quality.toml` says the opposite in as
    many words: "A new note does not belong in this list -- the gate firing on
    it is the gate working" (mechanism F8, premortem F8).

    The row's own date is the boundary that makes that sentence true. It
    excuses the notes that existed when it was written and nothing filed after.
    A note whose filename carries no date cannot be placed either side of the
    line, and is left excused rather than convicted on an absence.
    """
    if reason is None:
        return False
    when = note_date(rel)
    return when is None or when <= reason[:10]


def oracle_token(value: str) -> str:
    """The path out of an `oracle:` value, dropping the prose beside it.

    Every filled value in this corpus is written `<path> (what it is)`, and the
    parenthetical is the useful half for a reader: which model, how many
    segments. It is not a path and is not treated as one.
    """
    return value.strip().split()[0] if value.strip() else ""


def _first_match(pattern: Path) -> Path | None:
    """The file a candidate names, resolving a glob rather than refusing one.

    `watch-whisper/chunks/*.whisper.json` is how a chunked run is written down,
    and it names a real set of files. Treating the literal string as a filename
    would report the only honest way to name that rendering as unresolvable.
    """
    parts = pattern.parts
    globbed = next((i for i, part in enumerate(parts)
                    if any(c in part for c in "*?[")), None)
    if globbed is None:
        return pattern if pattern.is_file() else None
    anchor = Path(*parts[:globbed]) if globbed else Path(".")
    matches = sorted(p for p in anchor.glob(str(Path(*parts[globbed:])))
                     if p.is_file())
    return matches[0] if matches else None


def note_oracle_target(value: str, rel, root: Path, video_id: str) -> Path | None:
    """The rendering a note's `oracle:` names, or None when nothing opens.

    Three places are tried and no others: an absolute path, a path under this
    VIDEO's run directory, and a path under the corpus. The run directory is
    the one that matters -- every filled value in this corpus is relative to it
    -- and it is also what makes the answer specific to this video rather than
    to any file that happens to exist somewhere.
    """
    token = oracle_token(value)
    if not token:
        return None
    p = Path(token).expanduser()
    if p.is_absolute():
        return _first_match(p)
    bases = [POLICY.runs_root().expanduser() / video_id] if video_id else []
    bases.append(root)
    for base in bases:
        hit = _first_match(base / p)
        if hit is not None:
            return hit
    return None


def check_oracle(frontmatter: str, rel, root: Path,
                 honour_ledger: bool = True) -> list[str]:
    """`oracle:` names a rendering that opens, for notes written from now on.

    The field was prose. Eighteen of twenty-five notes left it empty and the
    values in the rest resolved to no file, while every gate downstream --
    coverage, alignment, windows -- is a check AGAINST the declared rendering.
    A note that names no oracle cannot be checked by any of them, and nothing
    said so.

    Turning it on today would redden the frozen corpus, so it is turned on for
    what comes next: `[unfilled_oracles]` carries a dated row per note that
    existed when the rule landed, and `excused()` refuses to let a row reach a
    note filed after its date. The ledger cannot grow -- a new note has no row,
    and adding one dated in the past does not cover it either.

    `honour_ledger=False` asks the other question: would this note be clean
    WITHOUT its row? That is what `stale_exemptions` needs, and reading it
    through the ordinary path would answer "yes" for every row on the table,
    because the row is what makes the answer yes.
    """
    if honour_ledger:
        excuse = (UNFILLED_ORACLES.get(str(rel))
                  or UNFILLED_ORACLES.get(Path(rel).name))
        if excused(excuse, rel):
            return []
    rows = RE_NOTE_ORACLE.findall(frontmatter)
    if not rows:
        return [f"{rel}:1 E-ORACLE-MISSING no oracle: field, so no gate can say "
                f"which rendering this note was written against"]
    # TWO ANSWERS IN ONE FIELD IS NOT A SMALLER CLAIM, IT IS AN UNREADABLE ONE.
    # `search` takes the first row, so a second one is invisible here and picked
    # up by whichever downstream reader searches differently -- the argument
    # `check_lanes` already makes about duplicate `video_id:` rows, which was
    # unguarded here until an independent pass asked for it by name.
    if len({r.strip() for r in rows}) > 1:
        return [f"{rel}:1 E-ORACLE-TWOROWS {len(rows)} oracle: rows naming "
                f"different renderings; one note is written against one"]
    value = rows[0].strip()
    if not value:
        return [f"{rel}:1 E-ORACLE-EMPTY oracle: is empty; name the rendering "
                f"this note was written against"]
    vid = RE_VIDEO_ID.search(frontmatter)
    video_id = vid.group(1) if vid else ""
    # NO VIDEO ID MEANS THE RELATEDNESS HALF CANNOT RUN AT ALL, and passing the
    # note anyway is how `oracle: /etc/hosts` came out clean: with nothing to
    # compare against, every file that opens is somebody's rendering. Deleting
    # one line -- the video id -- must not turn a check off.
    if not video_id:
        return [f"{rel}:1 E-ORACLE-NOVIDEO oracle: names {oracle_token(value)} "
                f"and the note declares no video_id:, so nothing can say the "
                f"two belong to each other"]
    hit = note_oracle_target(value, rel, root, video_id)
    if hit is None:
        return [f"{rel}:1 E-ORACLE-UNRESOLVED oracle: names {oracle_token(value)}, "
                f"which opens as no file under this video's run directory or "
                f"the corpus"]
    # AND IT IS THIS VIDEO'S. A path that opens somewhere else is a rendering of
    # something, and every gate downstream would grade this note against it.
    if video_id not in hit.resolve().parts:
        return [f"{rel}:1 E-ORACLE-UNRELATED oracle: opens as {hit}, which is "
                f"not a rendering of {video_id}"]
    # AND A GATE CAN READ IT. A run manifest sits in the same directory as the
    # rendering, under the same video id, and opens -- so "the path exists" said
    # yes to a file carrying no transcript at all. The failure then surfaced one
    # layer down, as a note that could not be graded, and was written off in a
    # ledger. The question this field asks is whether a gate can read what it
    # names, so the answer comes from the reader every gate uses.
    #
    # Imported here rather than at module scope: `transcript_align` pulls in a
    # caption parser that reads the policy file, and a malformed policy must not
    # stop this module from loading.
    from .transcript_align import load_segments
    try:
        segments = load_segments(hit)
    except Exception as exc:  # noqa: BLE001 -- any reason it will not read
        return [f"{rel}:1 E-ORACLE-UNRESOLVED oracle: opens as {hit}, which no "
                f"gate can read as a transcript: {exc}"]
    if not segments:
        return [f"{rel}:1 E-ORACLE-UNRESOLVED oracle: opens as {hit}, which "
                f"reads as a transcript carrying no segments, so no gate can "
                f"say anything about a note graded against it"]
    return []


def check_required_lanes(frontmatter: str, rel) -> list[str]:
    """Every lane this corpus requires was at least DECLARED by this note.

    `check_lanes` below is a roll-call of what the note declared, so a note that
    declared nothing passes it clean -- which is how a note reached the corpus
    stamped and gated with the whole review layer skipped. A roll-call of
    declarations is structurally unable to see a note that declared none, and
    "we always run the lanes" was prose enforced by memory.

    That reports the ABSENCE only. Whether each declared lane produced a report
    stays `check_lanes`' job, so a note short of the floor and a note whose lane
    died read as two different defects rather than one blurred one.
    """
    if not REQUIRED_LANES:
        return []
    ids, err = lane_ids(frontmatter)
    if err:
        return []  # check_lanes already reports this, and once is enough
    declared = ids or []
    m = RE_VIDEO_ID.search(frontmatter)
    debt = UNREVIEWED_NOTES.get(m.group(1), {}) if m else {}
    covered = covered_lanes(declared, list(REQUIRED_LANES))
    return [f"{rel}:1 E-LANE-UNREVIEWED lane {lane} is required and not "
            f"declared in reviews:"
            for lane in REQUIRED_LANES
            if lane not in covered and not excused(debt.get(lane), rel)]


def report_name(path: Path, root: Path) -> str:
    """How a report is named in a defect line: its path under the review dir.

    The bare filename was ambiguous exactly where the message mattered most --
    two files in two subdirectories printed as `facts.md, facts.md`, which
    names neither (properties I17).
    """
    base = root / REVIEW_DIR
    return str(path.relative_to(base) if path.is_relative_to(base) else path)


def oracle_path(value: str, root: Path) -> Path:
    """Where a header's `oracle:` actually points.

    Relative values resolve against the CORPUS ROOT and never against
    `Path.cwd()`: a gate whose answer changes with the directory it was run
    from is not a gate, and the policy loader was hardened against exactly that
    reading of "relative" already.

    NORMALISED, once, here. The first version handed the raw string to one
    check and the normalised path to the other, and `runs/<id>/../../README.md`
    walked between them: the id was in the string, the file was outside the
    notes directory, and an unrelated README was clean again one prefix away
    from the spelling that had just been refused. A symlink laundered its
    target the same way. One path, both questions.

    A header field carries whatever somebody typed, so `~nosuchuser/x.json`
    arrives here too, and `expanduser` RAISES `RuntimeError` on it. A gate may
    red a note; it may not abort on one. A value Python cannot even turn into a
    path becomes the corpus root, which is a directory and therefore not a
    file, so it reds the note exactly as any other unopenable value does.
    """
    try:
        p = Path(value).expanduser()
        return (p if p.is_absolute() else root / p).resolve()
    except (RuntimeError, ValueError):
        # `.resolve()` is INSIDE the try. It was outside, so the clause named
        # `ValueError` and could not catch the one value that raises it: a NUL
        # byte, which `lstat` refuses. The header parser happens to reject that
        # value first today, which made the gap unreachable rather than absent.
        return root.resolve()


def oracle_names_run(resolved: Path, video_id: str, root: Path) -> bool:
    """Is this file plausibly a rendering of THIS video, or just a file?

    Two questions, and both were needed.

    One: the video id is a DIRECTORY OR FILE NAME on the path -- a whole
    component, not a substring. That is how every real run in this corpus is
    laid out, and it refuses `README.md` and `/etc/hosts`. A substring test
    came first and was looser than this sentence: `docs/<id>.md` and
    `<id>eoteca_archive/` both satisfied it while being nothing of the kind.

    Two: the path may not sit inside the NOTES DIRECTORY -- notes, sidecars,
    and the review reports themselves. That is what refuses a report naming
    ITSELF: the id is in that path too, and a review is the artifact under
    judgement rather than the source of truth it was measured against. It is
    the notes directory and not the whole corpus, deliberately, because a
    corpus may keep its runs beside its notes and this one does not.

    Both asked of the NORMALISED path, which is what `oracle_path` returns and
    what `..` and a symlink used to slip between.

    Deliberately a shape and not a registry: a run that moved to another disk
    is still that video's run, and the corpus already has one dated table for a
    run it can no longer find at all.

    WHAT THIS RULES OUT, decided rather than discovered. `<id>.vtt`, `<id>.srt`
    and `<id>.whisper.json` are the default output names of the captions and
    ASR tools this project uses, and a run directory that suffixes or dates the
    id (`<id>-run-03`, `2026-08-20--<id>`) is an obvious future layout. All of
    them are REFUSED here, because the id has to be a whole name on the path
    and a filename stem is not one. That is a deliberate cost, not an
    oversight: every run this corpus has ever written is addressed
    `<id>/run-NN/...`, so 104 of 104 artifacts and 4 of 4 headered reports pass
    unchanged. A lane that wants to cite a bare `<id>.vtt` should file it under
    the run directory it came from, or this rule should be widened on purpose
    and this paragraph rewritten with it.
    """
    if video_id not in resolved.parts:
        return False
    return not resolved.is_relative_to((root / POLICY.notes_dir()).resolve())


def check_consequences(fields: dict[str, str], root: Path, video_id: str,
                       name: str, rel) -> list[str]:
    """The two header fields that now cause something, and what they cause.

    Retyping the five fields killed 65 known-bad values and could not reach
    these two, because the defect is not the spelling. `oracle: TODO` is a
    path. So are `n/a`, `unknown`, `x` and `0`. Each satisfies the type, each
    names nothing, and the next placeholder nobody has written down satisfies
    it too. `verdict: BLOCK` was in the vocabulary and exited 0, because no
    line downstream ever read the field: three lanes could refuse a note and
    the audit still printed that every gate passed.

    So the fields are given a consequence instead of a longer rule.

      ORACLE-MISSING    the path is opened. A value that resolves to no file
                        is not an oracle whatever it is spelled, and a
                        placeholder is refused for naming nothing rather than
                        for being on a list.
      ORACLE-UNRELATED  ...and opening SOMETHING is not enough either. An
                        independent review swept the values that resolve and
                        found `oracle: README.md`, `/etc/hosts`, `~/.zshrc` and
                        the report naming ITSELF all clean -- so the new
                        placeholder was not `TODO`, it was any real file, one
                        keystroke from the fixtures. The issue asked for the
                        path to resolve AGAINST THE RUN IT NAMES, so the video
                        id has to be a whole component of it and it may not
                        point back inside the notes directory it grades.
      BLOCKED           a lane that says BLOCK reds the note. None of the three
                        debt ledgers reaches it: they record work lost or never
                        dispatched, and a BLOCK is a live finding whose exits
                        are to fix the note or to have the lane rule again.

    `unheadered_reviews` DOES reach all three, and saying otherwise here was
    the review's finding F3. A video id on that table has its whole header
    skipped -- there is no header to read -- so an unheadered report cannot be
    refused for its verdict or its oracle either. That is the cost of the row,
    and it is why the row may only shrink.

    The two dated exits, and nothing else: `unresolvable_runs` for a run that
    has genuinely gone, and `unheadered_reviews` for a report filed before any
    of this existed.

    The cost is worth stating plainly: making BLOCK expensive gives a lane an
    incentive to write SHIP-WITH-FIXES instead. That trade is accepted because
    the alternative is the measured status quo, where the strongest thing a
    reviewer can say costs nothing at all.
    """
    out: list[str] = []
    resolved = oracle_path(fields["oracle"], root)
    # The row excuses the file being GONE and nothing else. It let any value at
    # all through while it also gated the relatedness branch -- a corpus with a
    # dated row accepted `oracle: README.md`, `/etc/hosts` and the report citing
    # itself -- and "the run behind this video is gone" cannot be the reason a
    # resolving file is accepted.
    forgiven = excused(UNRESOLVABLE_RUNS.get(video_id), rel)
    if not resolved.is_file() and not forgiven:
        out.append(f"{rel}:1 E-LANE-ORACLE-MISSING {name} names oracle "
                   f"{fields['oracle']}, which is no file under {root}; a path "
                   f"that opens nothing is not an oracle")
    elif not oracle_names_run(resolved, video_id, root):
        # "a real file" only when it IS one. Under a dated row the branch above
        # is skipped, so this one is reached for values that open nothing, and
        # it told the reader the opposite of what it had just measured.
        what = "a real file" if resolved.is_file() else "a value"
        out.append(f"{rel}:1 E-LANE-ORACLE-UNRELATED {name} names oracle "
                   f"{fields['oracle']}, {what} that is not a rendering of "
                   f"{video_id}; an oracle carries the video id as a whole "
                   f"path name and sits outside {POLICY.notes_dir()}/")
    if fields["verdict"] == BLOCK:
        out.append(f"{rel}:1 E-LANE-BLOCKED {name} ruled {BLOCK} on lane "
                   f"{fields['lane']}; a refused note may not sit under a "
                   f"clean audit, and no debt ledger excuses this one")
    return out


def check_lanes(root: Path, frontmatter: str, rel, body: str) -> list[str]:
    """Roll-call: every declared lane emitted a REVIEW, and every review was declared.

    Nine ways this goes wrong and all nine are defects, because each one
    reads as a clean review from the outside:

      MALFORMED    the lane list itself cannot be read, so nothing below it can
                   be trusted to be about the lanes the note declared.
      MISSING      the lane died, or was never dispatched after being declared.
      UNDECLARED   a report exists that the note does not own up to running.
      AMBIGUOUS    two files answer to one id, so which one is the verdict?
      TWOVIDEOS    the frontmatter names two videos, so the roll-call would be
                   answered by another video's reviews.
      NOVIDEO      lanes declared by a note with no video id. The roll-call is
                   addressed by video id, so deleting one line used to disarm
                   it entirely and the declaration was then believed on its own
                   word (mechanism F12).
      UNPARSED     the file exists and is not a review: no header, or a header
                   whose verdict or claim count cannot be read.
      MISLABELLED  the header answers for a different lane than the filename
                   promised.
      STALE        the header pins a different note body than the one on disk,
                   so the note was repaired or extended after the lane read it.

    The last three are the ones that make this a check on a review rather than
    on a filename. `body` is the note body the header is measured against; it
    is not optional, because a caller that could omit it could disarm STALE.

    SHARED used to sit in this list and is gone. It only ever fired when one
    report matched two declared ids, which was the loose suffix matcher failing
    to tell `facts` from `review-facts` rather than a report doing double duty;
    the lane left without one is still convicted, by MISSING, which was always
    the finding meant. The argument is repeated twelve lines below, where the
    matcher lives -- this half is here because a docstring that still lists a
    removed rule is how the next reader learns a rule that does not exist.
    """
    ids, err = lane_ids(frontmatter)
    found = RE_VIDEO_ID.findall(frontmatter)
    m = RE_VIDEO_ID.search(frontmatter)
    if err:
        return [f"{rel}:1 E-LANE-MALFORMED {err}"]
    if len(found) > 1 and len(set(found)) > 1:
        # `search` takes the FIRST row, so a second `video_id:` sent the whole
        # roll-call to another video's reviews while the note read as if it
        # named one. Two different answers in one field is not a smaller claim,
        # it is an unreadable one -- the argument `lane_header` already makes
        # about its own duplicate rows.
        return [f"{rel}:1 E-LANE-TWOVIDEOS frontmatter carries "
                f"{len(found)} video_id: rows naming {len(set(found))} "
                f"different videos; which one the reviews belong to is unread"]
    if not m:
        if ids:
            return [f"{rel}:1 E-LANE-NOVIDEO reviews: declares "
                    f"{', '.join(ids)} but there is no video_id:, so no report "
                    f"can be looked for and the declaration is its own witness"]
        return []
    video_id = m.group(1)
    reports = lane_reports(root, video_id)
    if ids is None:
        ids = []
    lost = LOST_REVIEWS.get(video_id, {})
    # AGED, like every other debt row. Membership alone made this the one table
    # that covered work nobody had done yet: a report filed today, under a video
    # id grandfathered in weeks ago, bought the whole header bypass -- oracle,
    # verdict and all -- for a note written after the row.
    unheadered = excused(UNHEADERED_REVIEWS.get(video_id), rel)
    want = note_body_sha256(body)
    out: list[str] = []
    # Keyed by PATH, not by name. `rglob` spans subdirectories, so name-keying
    # turned two files in two directories into one file answering to a lane
    # twice, and printed a fabricated E-LANE-SHARED naming one declared lane
    # twice over (properties I17, I18).
    #
    # E-LANE-SHARED lived here and is gone with the same change. It fired only
    # when one report answered two declared lanes, which the suffix matcher made
    # possible for a lane id ending in another one -- and that was never a
    # shared report, only a matcher too loose to tell `facts` from
    # `review-facts`. The lane left without a report is still convicted, by
    # E-LANE-MISSING, which is the finding that was always meant.
    claimed: dict[Path, list[str]] = {}
    for lane in ids:
        # Each report is spent on the LONGEST declared lane it matches, which is
        # the one that named it. `x-facts.md` matches both `facts` and
        # `x-facts`, so declaring the pair produced a fabricated E-LANE-SHARED
        # and left the shorter lane looking answered by a file about the other.
        hits = [h for h in lane_matches(reports, lane)
                if max((d for d in ids if lane_matches([h], d)), key=len) == lane]
        for h in hits:
            claimed.setdefault(h, []).append(lane)
        if len(hits) > 1:
            out.append(f"{rel}:1 E-LANE-AMBIGUOUS lane {lane} matches "
                       f"{len(hits)} reports: "
                       f"{', '.join(report_name(h, root) for h in hits)}")
        elif not hits and not excused(lost.get(lane), rel):
            out.append(f"{rel}:1 E-LANE-MISSING lane {lane} declared, no report "
                       f"under {REVIEW_DIR}/{video_id}/")
    for path, lanes in sorted(claimed.items()):
        name = report_name(path, root)
        if unheadered:
            continue
        fields, why = lane_header(path)
        if fields is None:
            out.append(f"{rel}:1 E-LANE-UNPARSED {name} is not a review: {why}")
            continue
        # Outside the chain below, deliberately. MISLABELLED and STALE are
        # alternatives -- one report cannot be both -- but a report can be
        # stale AND name a dead oracle, and folding these two into that chain
        # would report one defect per round and send the repair back twice.
        out.extend(check_consequences(fields, root, video_id, name, rel))
        if fields["lane"] not in lanes:
            out.append(f"{rel}:1 E-LANE-MISLABELLED {name} answers for lane "
                       f"{fields['lane']}, but it was read as "
                       f"{', '.join(sorted(lanes))}")
        elif fields["note_sha256"] != want:
            out.append(f"{rel}:1 E-LANE-STALE {name} read note body "
                       f"{fields['note_sha256'][:12]}, the note on disk is "
                       f"{want[:12]}; the note changed after the lane read it")
    for p in reports:
        if p not in claimed:
            out.append(f"{rel}:1 E-LANE-UNDECLARED {report_name(p, root)} is on "
                       f"disk but no reviews: id claims it")
    return out


def corpus_notes(root: Path) -> list[Path]:
    """Every note in the corpus, whatever this run was pointed at."""
    base = root / POLICY.notes_dir()
    return sorted(f for f in base.rglob("*.md")
                  if is_note(f, root)) if base.is_dir() else []


def declared_by_corpus(root: Path) -> dict[str, list[str]]:
    """{video_id: the lanes that note declares}, over the whole corpus."""
    out: dict[str, list[str]] = {}
    for f in corpus_notes(root):
        text, _ = safe_read(f)
        split = split_frontmatter(text) if text else None
        if not split:
            continue
        m = RE_VIDEO_ID.search(split[0])
        if m:
            ids, err = lane_ids(split[0])
            out[m.group(1)] = [] if err or ids is None else ids
    return out


def corpus_video_ids(root: Path) -> set[str]:
    """Every video_id the corpus declares, whatever this run was pointed at."""
    return set(declared_by_corpus(root))


def stale_exemptions(root: Path) -> list[str]:
    """Exemption rows that no longer excuse anything.

    Every table in the policy promises to shrink, and nothing ever told anyone
    WHICH row could go. `anchor_manifest` prints a stale census for
    `unresolvable_runs`; the three review tables had none, so a lane renamed, a
    video removed or a debt actually paid left a row excusing something that no
    longer exists -- permanently, and invisibly (properties I12, I13, I16;
    mechanism F10, which asked for exactly this to make "may only shrink"
    measurable).

    Reported, never a defect. A dead row is bookkeeping, and turning a corpus
    red over one teaches a reader to reach for the exemption table rather than
    for the review.
    """
    declared = declared_by_corpus(root)
    out: list[str] = []

    def say(table: str, vid: str, lane: str | None, why: str) -> None:
        where = f"{vid}/{lane}" if lane else vid
        out.append(f"# STALE EXEMPTION [{table}] {where}: {why}")

    for vid, lanes in sorted(UNREVIEWED_NOTES.items()):
        for lane in sorted(lanes):
            if vid not in declared:
                say("unreviewed_notes", vid, lane, "no note declares this video_id")
            elif lane not in REQUIRED_LANES:
                say("unreviewed_notes", vid, lane,
                    "this corpus does not require that lane")
            elif satisfies(declared[vid], lane):
                say("unreviewed_notes", vid, lane,
                    "the note now declares it, so the debt is paid")
    for vid, lanes in sorted(LOST_REVIEWS.items()):
        for lane in sorted(lanes):
            if vid not in declared:
                say("lost_reviews", vid, lane, "no note declares this video_id")
            elif lane not in declared[vid]:
                say("lost_reviews", vid, lane,
                    "the note does not declare that lane, so nothing looks for it")
            elif lane_matches(lane_reports(root, vid), lane):
                say("lost_reviews", vid, lane, "a report for it is on disk")
    for vid in sorted(UNHEADERED_REVIEWS):
        if not lane_reports(root, vid):
            say("unheadered_reviews", vid, None,
                "no reports on disk, so no header is being excused")
    # A row here dies in two ways: the note is gone, or somebody filled the
    # field. The second is the whole reason this ledger is keyed by note --
    # it is the only table in the policy that a note can pay off by itself,
    # and until this loop existed nothing would ever have said so.
    notes_dir = (root / POLICY.notes_dir())
    for name in sorted(UNFILLED_ORACLES):
        note = notes_dir / Path(name).name
        if not note.is_file():
            say("unfilled_oracles", name, None, "no note by that name")
            continue
        text, _ = safe_read(note)
        split = split_frontmatter(text) if text else None
        if split and not check_oracle(split[0], Path(name).name, root,
                                      honour_ledger=False):
            say("unfilled_oracles", name, None,
                "the note names a rendering that opens, so the row is paid")
    # A row here dies ONE way that can be checked from here: the note is gone.
    # The other death -- the finding stopped happening -- would mean running
    # both per-note checks against every excused rendering, which is the work
    # the row exists to skip. Said out loud so nobody reads an empty census
    # as proof that every remaining row is still earning its place.
    for name in sorted(UNGRADED_NOTES):
        if not (root / name).is_file():
            say("ungraded_notes", name, None, "no note at that path")
    return out


def orphan_sidecars(root: Path, files: list[Path]) -> list[str]:
    """Sidecars whose video_id matches no note in the corpus.

    The sidecar is addressed only by video_id, so renaming that field silently
    detaches the audit: the note stops being staleness-checked and nothing says
    so. This sweep is the only thing that notices.

    MEMBERSHIP IS A FACT ABOUT THE CORPUS, so it is read from the corpus. The
    live set used to be built from the files this run happened to be handed, so
    `watch-audit <one-note.md>` -- the invocation both documents prescribe after
    a repair pass -- declared every OTHER note's sidecar orphaned: 18 false
    defects and exit 1 on a note that was clean (premortem F3). A check that
    cries wolf on its own documented happy path is a check whose exit code
    stops being read, and after that nothing else here means anything.
    """
    live = set()
    for f in {*files, *corpus_notes(root)}:
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


def in_review_dir(path: Path, root: Path) -> bool:
    """Is this file inside THE review directory of THIS corpus?

    `notes/reviews`, the configured prefix, not the bare segment `reviews`
    matched anywhere in the path. The bare segment made a correctly named note
    under `drafts/reviews/` invisible to every gate, with no error and exit 0
    (properties I23).
    """
    rel = path.relative_to(root) if path.is_relative_to(root) else path
    return Path(REVIEW_DIR) in rel.parents


def is_note(path: Path, root: Path) -> bool:
    """A note, and not a document that merely lives under the notes directory.

    The name rule alone is not enough. `rglob` descends into the review
    directory, and a lane that filed its report under a dated name -- the same
    convention the notes themselves use -- was collected as a note and failed
    every gate with `E-FRONTMATTER`, on three gates at once. A review of a note
    is not a note, and the directory it sits in is the fact that says so.

    Which directory it sits in is measured from the CORPUS ROOT, and callers
    used to pass the path the operator typed instead. `wq-resolve-note
    notes/reviews/VID` therefore inverted the guard: relative to that argument
    no report is under a review directory, so every report in it became a note
    (properties I22).
    """
    if not RE_NOTE_NAME.match(path.name):
        return False
    return not in_review_dir(path, root)


def refuses_empty(files: list[Path], paths: list[Path], prog: str) -> bool:
    """True, and says so, when a corpus yielded no notes at all.

    The refusal used to live inside `collect` and moved out so that pointing a
    gate at a directory of REVIEWS could return nothing instead of raising.
    That was right and it was applied one caller at a time, which gave the
    guarantee back to two gates and silently dropped it for the third: on an
    empty corpus `demote_note` printed `# 0 notes would change`, exited 0, and
    `watch-audit` printed `demote_note: ok` over it (verification G1). A rule
    every caller has to remember is a rule two callers out of three keep, so it
    lives in one function that every entry point asks.
    """
    if files:
        return False
    named = ", ".join(str(p) for p in paths) or POLICY.notes_dir()
    print(f"{prog}: no notes under {named}", file=sys.stderr)
    return True


def collect(paths: list[Path], root: Path, skipped: list[str]) -> list[Path]:
    """The notes under `paths`, with every dropped `.md` file NAMED.

    A file that looks like it belongs to the corpus and is not collected used
    to vanish. An empty result was loud -- `SystemExit(2)`, so an empty corpus
    never read as a clean pass -- and a PARTIAL result was silent, so a
    hand-written note, a rename or a `-v2` copy left the audited set without a
    word (mechanism F15). The rule is unchanged; what changes is that the
    caller can now say how many files it turned away.

    Reports under the review directory are not counted as skips. They are the
    other clause of `is_note` and they are meant to be there; naming 59 of them
    on every run is how a reader learns to stop reading the line.
    """
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            # rglob, not glob: a note filed one directory down used to be
            # invisible.
            for f in sorted(p.rglob("*.md")):
                if is_note(f, root):
                    files.append(f)
                elif f.name == "_template.md":
                    continue
                # A note-named file inside the review tree is the one surprise
                # worth printing: `is_note` excludes it correctly as a report
                # and that exclusion is also a hiding place, so a whole note
                # copied under another video's review directory passed every
                # gate by never being read (mechanism F14).
                elif not in_review_dir(f, root) or RE_NOTE_NAME.match(f.name):
                    skipped.append(str(f.relative_to(root)
                                       if f.is_relative_to(root) else f))
        elif p.is_file():
            files.append(p)
        else:
            print(f"{PROG}: no such path: {p}", file=sys.stderr)
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
    # This selftest asserts inline, so there is no comparator to name: the
    # harness reads its `assert` statements as the cases instead, and what
    # `done()` can still prove is that `assert` bites in this interpreter.
    from watchquality import selftest_proof
    proof = selftest_proof.begin()

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

            # --- citing installed code by name@version:path ---
        # Asserted against THIS package, which is always installed wherever the
        # selftest runs -- as a distribution or from the source tree.
        try:
            me, my_version = "watch-quality", metadata.version("watch-quality")
        except metadata.PackageNotFoundError:
            me, my_version = "", ""
        if my_version:
            good = f'{{{{CITE:{me}@{my_version}:resolve_note.py#"E-CITE-VERSION"}}}}'
            rec = resolve_citations(good, 1, root)[0]
            assert "error" not in rec, rec
            assert rec["target_line"] > 0 and rec["sha"], rec
            cases += 1
            # The version is part of the address, so a different one refuses.
            wrong = f'{{{{CITE:{me}@0.0.0-not-a-release:resolve_note.py#"x"}}}}'
            assert resolve_citations(wrong, 1, root)[0]["error"].startswith(
                "E-CITE-VERSION"), "a version mismatch must not resolve"
            cases += 1
            # A file that version does not ship.
            gone = f'{{{{CITE:{me}@{my_version}:no_such_module.py#"x"}}}}'
            assert resolve_citations(gone, 1, root)[0]["error"].startswith(
                "E-CITE-NOFILE")
            cases += 1
        # Not installed is "cannot check", never "false".
        absent = '{{CITE:no-such-package@1.0.0:x.py#"q"}}'
        assert resolve_citations(absent, 1, root)[0]["error"].startswith(
            "E-CITE-NOPKG")
        cases += 1
        # A package spec is never resolved against the repo, so the
        # outside-the-repo rule cannot fire on one and hide the real reason.
        assert "E-CITE-PATH" not in resolve_citations(absent, 1, root)[0]["error"]
        cases += 1
        assert package_target("not-a-spec")[1].startswith("E-CITE-PKGSPEC")
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
    # A ROW'S SIBLINGS ARE NOT ITS ENUMERATION. Same shape as the rows that
    # tripped this in the field, where the check read 189, 134 and 5 members
    # off the rest of the note.
    assert check_enumerations(
        "- `[34:29]` `SPOKEN` - the seller holds two mindsets: knowing the\n"
        "  thing, and forgetting he knows it.\n"
        "- `[34:59]` `SPOKEN` - a second claim at a later second.\n"
        "- `[35:29]` `INFERRED` - and a third, reading the second.\n",
        1, "n.md") == []
    assert check_enumerations(
        "- they want it both ways: the free tier is the advertisement.\n"
        "- and the paid tier is the same data.\n", 1, "n.md") == []
    # ...but a list nested UNDER the row still counts, and still has to match.
    assert check_enumerations(
        "- the seller holds two mindsets:\n  - knows everything\n"
        "  - knows nothing\n", 1, "n.md") == []
    assert len(check_enumerations(
        "- the seller holds two mindsets:\n  - knows everything\n"
        "  - knows nothing\n  - knows too much\n", 1, "n.md")) == 1
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

    # --- what counts as a note ---------------------------------------------
    # A lane report filed under a dated name is not a note, and collecting it as
    # one failed three gates at once with E-FRONTMATTER on a report that has no
    # frontmatter because reports do not have any.
    with tempfile.TemporaryDirectory() as td:
        r = Path(td).resolve()
        reviews = r / POLICY.reviews_dir() / "VID"
        reviews.mkdir(parents=True)
        note = r / POLICY.notes_dir() / "2026-08-20--a-note--VID.md"
        report = reviews / "2026-08-20--review-facts.md"
        for f in (note, report):
            f.write_text("x", encoding="utf-8")
        assert is_note(note, r), "a note in the notes dir is a note"; cases += 1
        assert not is_note(report, r), "a dated report was taken for a note"
        cases += 1
        # The undated names the corpus actually uses were already skipped by the
        # name rule, and must stay skipped for the reason that now applies.
        plain = reviews / "facts.md"
        plain.write_text("x", encoding="utf-8")
        assert not is_note(plain, r); cases += 1
        # A directory merely CALLED reviews below the note is not the shield;
        # the shield is the review directory itself, so a note deeper in the
        # tree still counts.
        deep = r / POLICY.notes_dir() / "2026" / "2026-08-20--b-note--VID.md"
        deep.parent.mkdir()
        deep.write_text("x", encoding="utf-8")
        assert is_note(deep, r); cases += 1
        # ...and so does a note under a directory that merely SHARES the name.
        # The test used to be the bare segment `reviews` matched anywhere, so a
        # note under `drafts/reviews/` left the corpus with no error and exit 0
        # (properties I23).
        elsewhere = r / "drafts" / "reviews" / "2026-08-20--c-note--VID.md"
        elsewhere.parent.mkdir(parents=True)
        elsewhere.write_text("x", encoding="utf-8")
        assert is_note(elsewhere, r); cases += 1
        # The shield is measured from the CORPUS ROOT, never from whatever the
        # operator typed. Pointing the gate at the review tree used to invert
        # it and grade every report in it as a note (properties I22).
        assert not is_note(report, r); cases += 1
        assert collect([reviews], r, []) == []; cases += 1
        # A file the collector turns away is NAMED. An empty corpus was loud
        # and a partial one was silent, so a renamed note left the audited set
        # without a word (mechanism F15).
        stray = r / POLICY.notes_dir() / "draft-second-note.md"
        stray.write_text("x", encoding="utf-8")
        dropped: list[str] = []
        got_notes = collect([r / POLICY.notes_dir()], r, dropped)
        assert len(got_notes) == 2, got_notes
        assert "notes/draft-second-note.md" in dropped, dropped
        cases += 1
        # An ORDINARY report under the review directory is not a "skip". It is
        # the other clause of the rule, and naming 55 of them every run is how
        # a reader learns to stop reading the line.
        assert f"{POLICY.reviews_dir()}/VID/facts.md" not in dropped, dropped
        cases += 1
        # A NOTE-NAMED file under it is, because that exclusion is also a
        # hiding place: a whole note copied under another video's review
        # directory passed every gate by never being read (mechanism F14).
        assert any("2026-08-20--review-facts.md" in d for d in dropped), dropped
        cases += 1
        stray.unlink()
        # A sidecar is an orphan against the CORPUS, not against the argument
        # list, or auditing one note declares every other note's sidecar dead
        # (premortem F3).
        side = sidecar_path(r, "VID")
        side.parent.mkdir(parents=True, exist_ok=True)
        side.write_text("docs/x.md\t1\tdead\tq\n", encoding="utf-8")
        note.write_text("---\nvideo_id: VID\n---\n\nbody\n", encoding="utf-8")
        assert orphan_sidecars(r, [deep]) == [], orphan_sidecars(r, [deep])
        cases += 1
        side.unlink()

    # --- the lanes a corpus makes mandatory --------------------------------
    # The roll-call below checks that DECLARED lanes ran. It says nothing about
    # a note that declared none, so `reviews: []` was a clean corpus with the
    # review layer skipped entirely -- the one failure mode a roll-call of
    # declarations structurally cannot see. This is the other half.
    REQUIRED_LANES.clear()
    REQUIRED_LANES.extend(["facts", "quality", "coverage"])
    try:
        # Nothing declared is the whole defect, once lanes are required.
        got = check_required_lanes("video_id: VID\nreviews: []\n", "n.md")
        assert len(got) == 3 and all("E-LANE-UNREVIEWED" in g for g in got), got
        cases += 1
        # An ABSENT key is the same absence as an empty one. Reading it as
        # "opted out" would let deleting a line disarm the requirement.
        got = check_required_lanes("video_id: VID\n", "n.md")
        assert len(got) == 3, got; cases += 1
        assert check_required_lanes(
            "video_id: VID\nreviews: [facts, quality, coverage]\n", "n.md") == []
        cases += 1
        # A lane split in two still covers its family: `coverage-spoken` and
        # `coverage-frames` are how one corpus note ran the coverage lane, and
        # demanding the bare id would call that note unreviewed.
        assert check_required_lanes(
            "video_id: VID\nreviews: [facts, quality, coverage-spoken]\n",
            "n.md") == []; cases += 1
        # ...but the dash is load-bearing here too, exactly as it is in
        # lane_matches: `coverageless` is not the coverage lane.
        got = check_required_lanes(
            "video_id: VID\nreviews: [facts, quality, coverageless]\n", "n.md")
        assert len(got) == 1 and "coverage" in got[0], got; cases += 1
        # Extra lanes are somebody doing more than the floor, not a defect.
        assert check_required_lanes(
            "video_id: VID\nreviews: [facts, quality, coverage, verify]\n",
            "n.md") == []; cases += 1
        # A dated exemption silences one lane of one note, and only that one.
        UNREVIEWED_NOTES["VID"] = {"coverage": "2026-01-01 test"}
        try:
            got = check_required_lanes(
                "video_id: VID\nreviews: [facts, quality]\n", "n.md")
            assert got == [], got; cases += 1
            got = check_required_lanes(
                "video_id: OTHER\nreviews: [facts, quality]\n", "n.md")
            assert len(got) == 1 and "coverage" in got[0], got; cases += 1
            # ...one LANE, not the note: the exemption names coverage, so a
            # note that also dropped quality is still short a lane.
            got = check_required_lanes(
                "video_id: VID\nreviews: [facts]\n", "n.md")
            assert len(got) == 1 and "quality" in got[0], got; cases += 1
        finally:
            del UNREVIEWED_NOTES["VID"]
        # A note with no video_id cannot be exempted by id, and its lanes are
        # still required -- the missing id is its own defect elsewhere.
        assert len(check_required_lanes("title: t\n", "n.md")) == 3; cases += 1
        # A malformed key is check_lanes' defect to report, not this one's;
        # reporting it twice teaches a reader the note has two problems.
        assert check_required_lanes("video_id: VID\nreviews: facts\n",
                                    "n.md") == []; cases += 1
    finally:
        REQUIRED_LANES.clear()
        REQUIRED_LANES.extend(POLICY.required_lanes())
    # With no corpus policy asking for lanes, this check is silent: the package
    # cannot know what a corpus considers mandatory.
    assert check_required_lanes("video_id: VID\nreviews: []\n", "n.md") == [] \
        or POLICY.required_lanes(); cases += 1

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
        # The body every report below claims to have read, and a report that IS
        # a review of it. Writing "x" was enough before the header existed,
        # which is precisely the finding these cases now carry.
        note_body = "\n# t\n\nA body the lanes read.\n"
        sha = note_body_sha256(note_body)
        # The oracle every report below names. It is a real file because the
        # header check opens it: a fixture whose oracle resolves to nothing is
        # a fixture with a defect in it, and every case here would then be
        # measured against a corpus that is already red.
        (r / "runs" / "VID").mkdir(parents=True)
        (r / "runs" / "VID" / "run.json").write_text("{}\n", encoding="utf-8")

        def report(lane: str, note_sha: str = sha, verdict: str = "SHIP",
                   claims: str = UNSTATED,
                   oracle: str = "runs/VID/run.json") -> str:
            return (f"---\nnote_sha256: {note_sha}\noracle: {oracle}\n"
                    f"lane: {lane}\nverdict: {verdict}\n"
                    f"claims_enumerated: {claims}\n---\n\n# {lane}\n\nfound.\n")

        (d / "n-note-review-facts.md").write_text(report("facts"),
                                                  encoding="utf-8")
        fm = "video_id: VID\nreviews: [facts]\n"
        assert check_lanes(r, fm, "n.md", note_body) == [], \
            check_lanes(r, fm, "n.md", note_body)
        cases += 1
        # exact name, not just the -suffix form
        (d / "n-note-review-facts.md").rename(d / "facts.md")
        assert check_lanes(r, fm, "n.md", note_body) == []; cases += 1
        # ...and the dash is load-bearing: `nonfacts.md` is not lane `facts`.
        (d / "facts.md").rename(d / "nonfacts.md")
        got = check_lanes(r, fm, "n.md", note_body)
        assert any("E-LANE-MISSING" in g for g in got), got
        assert any("E-LANE-UNDECLARED" in g for g in got), got
        cases += 1
        (d / "nonfacts.md").rename(d / "facts.md")
        # a report nobody declared
        (d / "n-review-quality.md").write_text(report("quality"),
                                               encoding="utf-8")
        got = check_lanes(r, fm, "n.md", note_body)
        assert len(got) == 1 and "E-LANE-UNDECLARED" in got[0], got
        cases += 1
        # two files for one id: which one is the verdict?
        fm2 = "video_id: VID\nreviews: [facts, quality]\n"
        assert check_lanes(r, fm2, "n.md", note_body) == []; cases += 1
        (d / "b-review-quality.md").write_text(report("quality"),
                                               encoding="utf-8")
        got = check_lanes(r, fm2, "n.md", note_body)
        assert len(got) == 1 and "E-LANE-AMBIGUOUS" in got[0], got
        cases += 1
        (d / "b-review-quality.md").unlink()
        # A lane id ending in another one. `n-review-quality.md` matches the
        # suffix rule for `quality` AND names `review-quality` exactly, and the
        # longer id is the one that named it -- so `quality` is left with no
        # report and is convicted, rather than both lanes reading as answered.
        fm3 = "video_id: VID\nreviews: [facts, quality, review-quality]\n"
        got = check_lanes(r, fm3, "n.md", note_body)
        assert any("E-LANE-MISSING" in g and " quality " in g for g in got), got
        cases += 1
        assert not any("E-LANE-AMBIGUOUS" in g for g in got), got
        cases += 1
        # a declared lane with no report at all
        fm4 = "video_id: VID\nreviews: [facts, quality, coverage]\n"
        got = check_lanes(r, fm4, "n.md", note_body)
        assert len(got) == 1 and "E-LANE-MISSING" in got[0] and "coverage" in got[0]
        cases += 1
        # ...unless it is dated in LOST_REVIEWS, and only for ITS video id.
        # The second note is stocked identically, so the ONLY thing that can
        # separate the two verdicts is the video id the exemption is filed under.
        other = r / "notes" / "reviews" / "OTHER"
        other.mkdir(parents=True)
        (r / "runs" / "OTHER").mkdir(parents=True)
        (r / "runs" / "OTHER" / "run.json").write_text("{}\n", encoding="utf-8")
        for name, lane in (("facts.md", "facts"),
                           ("n-review-quality.md", "quality")):
            (other / name).write_text(report(lane, oracle="runs/OTHER/run.json"),
                                      encoding="utf-8")
        LOST_REVIEWS["VID"] = {"coverage": "2026-01-01 test"}
        try:
            assert check_lanes(r, fm4, "n.md", note_body) == []
            cases += 1
            got = check_lanes(r, fm4.replace("VID", "OTHER"), "n.md", note_body)
            assert len(got) == 1 and "E-LANE-MISSING" in got[0], got
            cases += 1
            # ...and only for a note that already existed when the row was
            # written. A newer note inherits the video id and must not inherit
            # the excuse with it (mechanism F8).
            got = check_lanes(r, fm4, "2026-06-01--later--VID.md", note_body)
            assert len(got) == 1 and "E-LANE-MISSING" in got[0], got
            cases += 1
        finally:
            del LOST_REVIEWS["VID"]
        # no declaration and no reports on disk is the ordinary case
        assert check_lanes(r, "video_id: NONE\n", "n.md", note_body) == []
        cases += 1
        # Two DIFFERENT video ids in one frontmatter. `search` took the first,
        # so the whole roll-call went to another video's reviews while the note
        # read as if it named one.
        got = check_lanes(r, "video_id: VID\nvideo_id: OTHER\nreviews: [facts]\n",
                          "n.md", note_body)
        assert len(got) == 1 and "E-LANE-TWOVIDEOS" in got[0], got
        cases += 1
        # ...and the same id written twice is a repetition, not a contradiction.
        got = check_lanes(r, "video_id: VID\nvideo_id: VID\nreviews: [facts]\n",
                          "n.md", note_body)
        assert not any("E-LANE-TWOVIDEOS" in g for g in got), got
        cases += 1
        # ...but declaring lanes with no video id is not: the roll-call is
        # addressed by video id, so deleting that one line used to leave the
        # declaration believed on its own word (mechanism F12).
        got = check_lanes(r, "reviews: [facts, quality]\n", "n.md", note_body)
        assert len(got) == 1 and "E-LANE-NOVIDEO" in got[0], got
        cases += 1
        # A report one directory deeper was invisible to the roll-call, and
        # filing by date under the video id is the obvious thing a future run
        # does (slice-13 coverage lane): `mkdir` was the whole dodge.
        (d / "2026-08-06").mkdir()
        (d / "2026-08-06" / "n-review-buried.md").write_text(report("buried"),
                                                             encoding="utf-8")
        got = check_lanes(r, fm, "n.md", note_body)
        assert any("E-LANE-UNDECLARED" in g and "buried" in g for g in got), got
        cases += 1
        assert check_lanes(r, "video_id: VID\nreviews: [facts, quality, buried]\n",
                           "n.md", note_body) == []
        cases += 1

        # --- a report has to BE a review --------------------------------
        # Everything above is the roll-call over files that parse. These are
        # the door two independent lanes walked through on 2026-08-20: three
        # zero-byte files bought a stamped, gated, clean note.
        blank = d / "2026-08-06" / "n-review-buried.md"
        deep_fm = "video_id: VID\nreviews: [facts, quality, buried]\n"
        blank.write_text("", encoding="utf-8")
        got = check_lanes(r, deep_fm, "n.md", note_body)
        assert len(got) == 1 and "E-LANE-UNPARSED" in got[0], got
        cases += 1
        # A directory named like a report, and a symlink to /dev/null, are the
        # same attack one layer down: `rglob` yields both and neither is a file.
        blank.unlink()
        blank.mkdir()
        got = check_lanes(r, deep_fm, "n.md", note_body)
        assert len(got) == 1 and "E-LANE-MISSING" in got[0], got
        cases += 1
        blank.rmdir()
        blank.symlink_to("/dev/null")
        got = check_lanes(r, deep_fm, "n.md", note_body)
        assert len(got) == 1 and "E-LANE-MISSING" in got[0], got
        cases += 1
        blank.unlink()
        # A header that answers for another lane, and a verdict or a claim
        # count outside the vocabulary.
        blank.write_text(report("facts"), encoding="utf-8")
        got = check_lanes(r, deep_fm, "n.md", note_body)
        assert len(got) == 1 and "E-LANE-MISLABELLED" in got[0], got
        cases += 1
        for bad in (report("buried", verdict="LGTM"),
                    report("buried", claims="lots"),
                    report("buried", note_sha="not-a-hash")):
            blank.write_text(bad, encoding="utf-8")
            got = check_lanes(r, deep_fm, "n.md", note_body)
            assert len(got) == 1 and "E-LANE-UNPARSED" in got[0], got
            cases += 1
        # A stated count IS allowed; `unstated` is the escape for a lane that
        # did not count, not a way to avoid saying anything.
        blank.write_text(report("buried", claims="12"), encoding="utf-8")
        assert check_lanes(r, deep_fm, "n.md", note_body) == []; cases += 1
        # ...and the same escape exists for a lane that reported findings and
        # never ruled on the artifact. Inventing a verdict for it would be the
        # model-authored literal, one field over.
        blank.write_text(report("buried", verdict=UNSTATED), encoding="utf-8")
        assert check_lanes(r, deep_fm, "n.md", note_body) == []; cases += 1
        # A header pinned to a different note body: the note was repaired or
        # extended after the lane read it, which is the authoring pass this
        # gate had no state for (premortem F2, X3).
        blank.write_text(report("buried", note_sha="0" * 64), encoding="utf-8")
        got = check_lanes(r, deep_fm, "n.md", note_body)
        assert len(got) == 1 and "E-LANE-STALE" in got[0], got
        cases += 1
        # ...and editing the note after the lanes read it turns EVERY report on
        # it red at once, which is the authoring pass finally having a state the
        # gate can see rather than a sentence in a brief.
        blank.write_text(report("buried"), encoding="utf-8")
        got = check_lanes(r, deep_fm, "n.md", note_body + "one more claim.\n")
        assert len(got) == 3 and all("E-LANE-STALE" in g for g in got), got
        cases += 1
        # ...and a machine mark is not an edit: the demotion renderer's own
        # bytes must not turn every report on the note red.
        marked = f"{note_body}\n{INTEGRITY_PREFIX} 3 claims\n"
        assert check_lanes(r, deep_fm, "n.md", marked) == []; cases += 1
        # A dated row excuses the header for the reports filed before it, and
        # nothing else about them.
        blank.write_text("old report\n", encoding="utf-8")
        got = check_lanes(r, deep_fm, "n.md", note_body)
        assert len(got) == 1 and "E-LANE-UNPARSED" in got[0], got
        cases += 1
        UNHEADERED_REVIEWS["VID"] = "2026-01-01 filed before the header existed"
        try:
            assert check_lanes(r, deep_fm, "n.md", note_body) == []; cases += 1
            got = check_lanes(r, fm4, "n.md", note_body)
            assert any("E-LANE-MISSING" in g for g in got), got
            cases += 1
            # ...and it AGES, like every other debt row. Membership alone made
            # this the one table that covered work nobody had done yet: a note
            # filed after the row bought the whole header bypass with it.
            got = check_lanes(r, deep_fm, "2026-06-01--later--VID.md", note_body)
            assert len(got) == 1 and "E-LANE-UNPARSED" in got[0], got
            cases += 1
        finally:
            del UNHEADERED_REVIEWS["VID"]
        blank.write_text(report("buried"), encoding="utf-8")

        # --- a header field with a consequence --------------------------
        # Every value below is WELL TYPED and means nothing, which is why the
        # type check that killed 65 bad values could not reach one of them.
        for empty in ("TODO", "n/a", "unknown", "x", "0", "runs/VID/gone.json"):
            blank.write_text(report("buried", oracle=empty), encoding="utf-8")
            got = check_lanes(r, deep_fm, "n.md", note_body)
            assert len(got) == 1 and "E-LANE-ORACLE-MISSING" in got[0], (empty, got)
            cases += 1
        # An absolute path is taken as written; a relative one is read against
        # the corpus root, never against the directory the gate was run from.
        blank.write_text(report("buried", oracle=str(r / "runs/VID/run.json")),
                         encoding="utf-8")
        assert check_lanes(r, deep_fm, "n.md", note_body) == []; cases += 1
        # A file that EXISTS and is not this run. The first pass asked only
        # whether the path opened, so any real file was a legal oracle.
        (r / "README.md").write_text("not an oracle\n", encoding="utf-8")
        for unrelated in ("README.md", "notes/reviews/VID/facts.md"):
            blank.write_text(report("buried", oracle=unrelated),
                             encoding="utf-8")
            got = check_lanes(r, deep_fm, "n.md", note_body)
            assert len(got) == 1 and "E-LANE-ORACLE-UNRELATED" in got[0], got
            cases += 1
        # Two defects at once, which is what the placement outside the
        # mislabelled/stale chain buys and what nothing used to check.
        blank.write_text(report("buried", note_sha="0" * 64,
                                oracle="runs/VID/gone.json"), encoding="utf-8")
        got = check_lanes(r, deep_fm, "n.md", note_body)
        assert any("E-LANE-STALE" in g for g in got), got; cases += 1
        assert any("E-LANE-ORACLE-MISSING" in g for g in got), got; cases += 1
        # A directory resolves and is not the artifact a lane read.
        blank.write_text(report("buried", oracle="runs/VID"), encoding="utf-8")
        got = check_lanes(r, deep_fm, "n.md", note_body)
        assert len(got) == 1 and "E-LANE-ORACLE-MISSING" in got[0], got
        cases += 1
        # The one exit: the corpus has written the run off, dated, in the table
        # anchor_manifest already ages. And it excuses only what pre-dates it.
        blank.write_text(report("buried", oracle="runs/VID/gone.json"),
                         encoding="utf-8")
        UNRESOLVABLE_RUNS["VID"] = "2026-01-01 the run behind this video is gone"
        try:
            assert check_lanes(r, deep_fm, "n.md", note_body) == []; cases += 1
            got = check_lanes(r, deep_fm, "2026-06-01--later--VID.md", note_body)
            assert len(got) == 1 and "E-LANE-ORACLE-MISSING" in got[0], got
            cases += 1
            # ...and it excuses the run being GONE, not any value at all. It
            # gated both branches once, so a corpus with a dated row accepted
            # an unrelated file that opens perfectly well.
            blank.write_text(report("buried", oracle="README.md"),
                             encoding="utf-8")
            got = check_lanes(r, deep_fm, "n.md", note_body)
            assert len(got) == 1 and "E-LANE-ORACLE-UNRELATED" in got[0], got
            cases += 1
        finally:
            del UNRESOLVABLE_RUNS["VID"]
        # Walking back out of the run directory used to launder any file: the
        # id was read from the raw string and the location from the resolved
        # path, and `..` sat between the two.
        blank.write_text(report("buried", oracle="runs/VID/../../README.md"),
                         encoding="utf-8")
        got = check_lanes(r, deep_fm, "n.md", note_body)
        assert len(got) == 1 and "E-LANE-ORACLE-UNRELATED" in got[0], got
        cases += 1
        # The id is a whole NAME on the path. A substring test called
        # `docs/VID.md` a rendering of VID, which it is not.
        (r / "docs").mkdir()
        (r / "docs" / "VID.md").write_text("x\n", encoding="utf-8")
        blank.write_text(report("buried", oracle="docs/VID.md"),
                         encoding="utf-8")
        got = check_lanes(r, deep_fm, "n.md", note_body)
        assert len(got) == 1 and "E-LANE-ORACLE-UNRELATED" in got[0], got
        cases += 1
        # And a value Python cannot expand reds the note instead of raising
        # out of the gate entirely.
        blank.write_text(report("buried", oracle="~nosuchuser42/x.json"),
                         encoding="utf-8")
        got = check_lanes(r, deep_fm, "n.md", note_body)
        assert len(got) == 1 and "E-LANE-ORACLE" in got[0], got
        cases += 1
        # A lane that refuses the note reds it, and no table excuses that one.
        blank.write_text(report("buried", verdict=BLOCK), encoding="utf-8")
        got = check_lanes(r, deep_fm, "n.md", note_body)
        assert len(got) == 1 and "E-LANE-BLOCKED" in got[0], got
        cases += 1
        UNRESOLVABLE_RUNS["VID"] = "2026-01-01 the run behind this video is gone"
        LOST_REVIEWS["VID"] = {"buried": "2026-01-01 test"}
        try:
            got = check_lanes(r, deep_fm, "n.md", note_body)
            assert any("E-LANE-BLOCKED" in g for g in got), got
            cases += 1
        finally:
            del UNRESOLVABLE_RUNS["VID"]
            del LOST_REVIEWS["VID"]
        blank.write_text(report("buried"), encoding="utf-8")

    # --- the CLI actually reaches both new checks --------------------------
    # Every case above calls the predicate directly. Deleting the ONE line that
    # wires it into check_note left the whole suite green, in both files, which
    # two independent review lanes filed against slice 13. These cases drive
    # argparse end to end against a fixture root instead.
    with tempfile.TemporaryDirectory() as td:
        r = Path(td).resolve()
        (r / "notes" / "reviews" / "WIRED").mkdir(parents=True)
        # A rendering for the note to declare. `check_oracle` opens the value
        # AND reads it, so a fixture without one is red for a reason no case
        # here is about. It used to be `{}` -- a file that opens and carries no
        # transcript, which is exactly what the oracle check now refuses.
        (r / "runs" / "WIRED").mkdir(parents=True)
        (r / "runs" / "WIRED" / "run.json").write_text(
            '{"segments": [{"start": 0.0, "end": 2.0, "text": "hello there"}]}\n',
            encoding="utf-8")
        note = r / "notes" / "2026-01-01--wired--WIRED.md"
        # The fixture is stamped, because these cases are about lanes and status
        # reaching the exit code. An unstamped note is its own defect, tested
        # below; leaving it unstamped here would make every case pass for the
        # wrong reason.
        graded = f"graded_with: {current_stamp()}\n" if current_stamp() else ""
        clean = ('---\nvideo_id: WIRED\nduration: "1:00"\nstatus: distilled\n'
                 'oracle: runs/WIRED/run.json\n'
                 f'applied:\n{graded}---\n\n# t\n\n'
                 'A body with no declaration in it.\n')
        note.write_text(clean, encoding="utf-8")
        # This fixture declares no lanes, so a corpus policy that requires some
        # would make every case below fail for a reason none of them is about.
        # The requirement gets its own case at the end of the block instead.
        ambient, REQUIRED_LANES[:] = list(REQUIRED_LANES), []
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
        # ...and so must a note that skipped a lane the corpus requires. Wiring
        # is the whole risk here: the predicate had cases before the one line
        # that calls it existed.
        note.write_text(clean, encoding="utf-8")
        REQUIRED_LANES[:] = ["facts"]
        try:
            assert main(["--check", "--no-require-density", str(note)],
                        root=r) == 1
            cases += 1
        finally:
            # Back to none for the rest of this fixture, which is about the
            # stamp; `ambient` is restored when the block ends.
            REQUIRED_LANES[:] = []

        # --- the grade stamp ---
        now = current_stamp()
        if now:
            # A red note is REFUSED and left exactly as it was. This is the case
            # that matters: a stamp is a claim that these checks passed.
            red = clean.replace("applied:\n", "applied: docs/x.md\n").replace(
                f"graded_with: {now}\n", "")
            note.write_text(red, encoding="utf-8")
            errs, wrote = stamp_note(note, r, now)
            assert not wrote and errs and "E-STAMP-REFUSED" in errs[0], errs
            assert note.read_text(encoding="utf-8") == red, "a refusal wrote"
            cases += 1
            # The stamp now grades with the checker `--check` grades with, so
            # this density-free fixture is refused by DEFAULT -- which is the
            # finding: `--stamp` used to write a clean bill onto a note the
            # same build called defective one command later (mechanism F16).
            note.write_text(clean.replace(f"graded_with: {now}\n", ""),
                            encoding="utf-8")
            errs, wrote = stamp_note(note, r, now)
            assert not wrote and errs and "E-STAMP-REFUSED" in errs[0], errs
            cases += 1
            # ...and the weaker stamp is still reachable, by name, exactly as
            # `--check --no-require-density` is.
            errs, wrote = stamp_note(note, r, now, require_density=False)
            assert wrote and not errs, errs
            assert f"graded_with: {now}" in note.read_text(encoding="utf-8")
            cases += 1
            # Stamping twice writes nothing the second time.
            assert stamp_note(note, r, now, require_density=False) == ([], False)
            cases += 1
            # A stamp from another version is stale, not wrong.
            stale = note.read_text(encoding="utf-8").replace(
                now, f"{DIST_NAME}@0.0.0")
            note.write_text(stale, encoding="utf-8")
            d, _ = check_note(note, r, False)
            assert any("E-GRADE-STALE" in x for x in d), d
            cases += 1
            # An unstamped note says so rather than passing quietly.
            note.write_text(clean.replace(f"graded_with: {now}\n", ""),
                            encoding="utf-8")
            d, _ = check_note(note, r, False)
            assert any("E-GRADE-UNSTAMPED" in x for x in d), d
            cases += 1
            # The frontmatter stays parseable after stamping, which a naive
            # append past the closing --- would break.
            stamp_note(note, r, now, require_density=False)
            fm, _body = split_frontmatter(note.read_text(encoding="utf-8"))
            assert RE_GRADED.search(fm), fm
            cases += 1
            # `only` leaves an unrelated stale row alone. Without this, --stamp
            # would quietly repair staleness the caller has not been shown.
            cited = r / "docs" / "cited.md"
            cited.parent.mkdir(exist_ok=True)
            cited.write_text("the quoted words are here\n", encoding="utf-8")
            side = sidecar_path(r, "WIRED")
            side.parent.mkdir(parents=True, exist_ok=True)
            side.write_text("docs/cited.md\t1\tdeadbeef\tthe quoted words\n",
                            encoding="utf-8")
            assert refresh_sidecar(r, note, only={"docs/other.md"}) == ([], 0)
            assert "deadbeef" in side.read_text(encoding="utf-8")
            cases += 1
            # ...and refreshes it when it IS in scope.
            _d, n_refreshed = refresh_sidecar(r, note, only={"docs/cited.md"})
            assert n_refreshed == 1, n_refreshed
            assert "deadbeef" not in side.read_text(encoding="utf-8")
            cases += 1
        REQUIRED_LANES[:] = ambient
    proof.done()
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
    ap.add_argument("--stamp", action="store_true",
                    help="record graded_with: on every note that is clean now; "
                         "a note with defects is refused, not stamped")
    ap.add_argument("--no-floor", dest="floor", action="store_false",
                    help="skip the untokenised-path floor (on by default)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    skipped: list[str] = []
    files = collect([p.resolve() for p in args.paths]
                    or [root / POLICY.notes_dir()], root, skipped)
    for s in skipped:
        print(f"# not collected: {s} is under the corpus and is not named "
              f"YYYY-MM-DD--slug--<video-id>.md", file=sys.stderr)
    if refuses_empty(files, args.paths, PROG):
        return 2

    if args.stamp:
        stamp = current_stamp()
        if stamp is None:
            print(f"{PROG}: {DIST_NAME} is not installed, so there is no "
                  f"version to stamp with", file=sys.stderr)
            return 2
        # Stamping WRITES to notes, and notes cite each other, so stamping note
        # A invalidates the recorded sha of any note citing A -- which makes the
        # citer red and unstampable, purely as a side effect of this run. One
        # pass therefore cannot finish the job.
        #
        # Between rounds only the rows citing notes THIS RUN stamped are
        # refreshed. Refreshing everything would repair staleness the caller has
        # not been shown yet, which turns a defect into a silent update -- the
        # failure mode every check here exists to remove. Three rounds is a
        # runaway guard, not a limit anyone should reach.
        defects, stamped, rounds = [], 0, 0
        for rounds in range(1, 4):
            defects, wrote = [], set()
            for f in files:
                d, did = stamp_note(f, root, stamp, args.require_density,
                                    args.floor)
                defects.extend(d)
                if did:
                    wrote.add(str(f.relative_to(root)
                                  if f.is_relative_to(root) else f))
            stamped += len(wrote)
            if not wrote:
                break
            for f in files:
                refresh_sidecar(root, f, only=wrote)
        for line in defects:
            print(line)
        print(f"# {len(files)} notes, {stamped} stamped {stamp} over {rounds} "
              f"round(s), {len(defects)} refused", file=sys.stderr)
        return 1 if defects else 0

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
    #
    # That doctrine was written for LOST_REVIEWS and stopped two lines short of
    # the table beside it. UNREVIEWED_NOTES excuses the ENTIRE review layer for
    # 19 lanes of this corpus and was the one exemption table no ordinary run
    # ever mentioned (mechanism F9); UNHEADERED_REVIEWS is new and would have
    # started life with the same silence. Both print here now.
    for vid, lanes in sorted(LOST_REVIEWS.items()):
        for lane, why in sorted(lanes.items()):
            print(f"# lane {vid}/{lane} exempt from the roll-call [{why}]",
                  file=sys.stderr)
    for vid, lanes in sorted(UNREVIEWED_NOTES.items()):
        for lane, why in sorted(lanes.items()):
            print(f"# lane {vid}/{lane} never ran, debt recorded [{why}]",
                  file=sys.stderr)
    for vid, why in sorted(UNHEADERED_REVIEWS.items()):
        print(f"# reviews for {vid} are read on their filenames alone [{why}]",
              file=sys.stderr)
    # The doctrine stopping one table short, a third time. This one is the
    # largest ledger in the corpus and was born silent (round-5 refutation F5).
    for name, why in sorted(UNFILLED_ORACLES.items()):
        print(f"# {name} names no rendering a gate can open [{why}]",
              file=sys.stderr)
    for line in stale_exemptions(root):
        print(line, file=sys.stderr)
    # "0 defects" over 8 notes used to read as "8 notes verified" when only the
    # 2 carrying a declaration were ever tested. Say how many were testable.
    print(f"# {len(files)} notes checked, {declared} with a declared density, "
          f"{len(defects)} defects", file=sys.stderr)
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
