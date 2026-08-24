#!/usr/bin/env python3
"""What the note MISSED — the question every other check here declines to ask.

The rest of this package asks whether what a note SAYS is supported. Nothing in
it asks what the recording said that the note never carried, and that is the
defect map-reduce exists to fix: the middle of a long video gets compressed
hardest, and a note thinned in its middle third looks exactly like a note that
was thorough about a quiet stretch.

The obvious measure -- claims per minute -- cannot answer it. One argument
returned as six rows scores the same as six claims, and a note padded with
"the speaker offers an example here" scores higher than one that reports the
example. So this file counts what was CARRIED, once, and never how much was
written:

    figures   tokens with a digit in them: prices, counts, percentages, years.
              The least paraphrasable thing a recording contains.
    names     tokens capitalised mid-sentence: people, companies, places.
    terms     tokens the recording uses rarely, which is what makes them
              specific to the stretch they appear in.

Recall over those three sets does not move when a claim is split in two, and it
does not move when a row says nothing. It moves when the note carries something
the recording said, which is the only thing worth measuring here.

TWO GUARDS SIT BESIDE IT, because recall alone can be won by pasting the
transcript into the note:

    dead stretches   minutes of the span with no anchor at all. Absolute:
                     a run of them IS the compression defect, whatever the
                     recall figure says.
    near-duplicate   rows that repeat a row before them. Fragmentation and
    rows             padding both show up here and nowhere else.

WHAT THIS IS NOT. It is not a quality score, and a high recall is not a good
note: a note can carry every number in the file and still misread the argument
those numbers were part of. Recall is COMPARATIVE -- two notes over the same
span, against the same rendering -- because the denominator includes the
recogniser's own mis-hearings, which no note can carry and which therefore
depress every note equally. The dead-stretch count is the only absolute here.

A spoken "five grand" is a term and not a figure, because the recogniser writes
it in words. That is a known floor, stated rather than papered over.

Usage:
    wq-note-coverage NOTE TRANSCRIPT [--span MM:SS-MM:SS] [--json]
    wq-note-coverage --selftest

TRANSCRIPT is anything `wq-transcript-align` reads: WebVTT, whisper.cpp JSON,
Whisper verbose_json, a caption-index `.tsv`, or a bare segment list.

Exit: 0 clean, 1 a defect was reported, 2 usage or unreadable input.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

from .transcript_align import RE_WORD, hms, load_segments

PROG = "note_coverage.py"

# A token in this many segments or fewer is specific to them. Measured over 24
# notes and their caption indexes: the recall it produces spreads 0.189 to 0.928,
# median 0.642 -- a metric that separates the thin notes from the thorough ones
# instead of scoring everything alike.
RARE_DF = 3
# Shorter rare tokens are overwhelmingly recogniser noise -- clipped words and
# half-heard syllables -- rather than terms a note could carry.
MIN_TERM_LEN = 4
# One minute is the granularity a reader would notice a hole at. Shorter buckets
# report gaps in ordinary turn-taking; longer ones hide a stretch nobody wrote
# from inside a bucket that has one anchor at its edge.
BUCKET_SECONDS = 60.0
# Consecutive dead minutes that mean a stretch went unwritten rather than a quiet
# passage went unremarked. Measured across the corpus: the longest dead run is
# two minutes or less in 20 of 24 notes, and the four outliers are 3, 4, 12 and
# 36. Three sits above ordinary turn-taking and at the foot of the outliers.
#
# THIS IS A QUESTION, NOT A VERDICT. A sponsor read, a long silence and a
# stretch of banter are all legitimately unwritten, and this check cannot tell
# them from a stretch that was skipped. It says where to look.
DEAD_RUN = 3
# Two rows this similar say the same thing twice.
NEAR_ROW = 0.85
# Share of rows that may repeat a neighbour before the note is padding. The
# corpus's worst note measures 0.004, so this ceiling is twenty-five times the
# highest real value: it is a padding tripwire, not a style preference.
MAX_NEAR_SHARE = 0.10
# Below this many salient tokens the recall figure is noise, not evidence. The
# smallest real span in the corpus offers 115.
MIN_SALIENT = 25
# Consecutive tokens that, appearing in both note and recording, mean the note
# is reproducing rather than reporting. Measured: at twelve tokens, the corpus's
# heaviest-quoting note is 37% verbatim and the median is 5%, so a note more than
# sixty percent verbatim runs is not quoting, it is pasting.
VERBATIM_RUN = 12
DUMP_SHARE = 0.60
# Carried salient tokens per word of note. A note that carries the recording's
# whole vocabulary in three thousand words is a word list, not a note; measured
# across the corpus the ratio runs 0.016 to 0.115, an alphabetised vocabulary
# dump measures 0.75, and one claim padded with a vocabulary appendix 0.61.
LIST_DENSITY = 0.30
# Words a line must carry, besides its anchors, before its anchor counts as a
# stretch someone wrote from. An adversarial lane filled every hole in a note by
# hiding one bare anchor per minute inside an HTML comment.
MIN_LINE_WORDS = 4
# How far into a file a frontmatter block may close. Beyond this the opening
# `---` was a horizontal rule, and treating it as frontmatter deletes the note.
FRONTMATTER_LINES = 80
# Misshapen rows quoted one by one before the rest are counted. Five names the
# problem; a note carrying thirty-six would bury everything else this check
# says. The remainder is COUNTED on a line of its own -- it used to be dropped,
# and a reader shown 32 lines over a corpus carrying 105 could not tell the cap
# from the count (round-14 F5).
ROWSHAPE_SHOWN = 5

RE_RAW = re.compile(r"[A-Za-z0-9'’]+")
# THREE digits in the leading field, and every dash. A two-hour recording is
# legitimately stamped `[105:20]`, and a note written with an en dash is still a
# note: both shapes were invisible to an earlier version of this pattern, which
# meant the padding check silently had nothing to look at.
_STAMP = r"\d{1,3}(?::\d{2}){1,2}"
# A claim that took two minutes to make is written across two anchors:
# `- [01:57] SPOKEN to [04:23] SPOKEN — …`. The row is anchored where it
# STARTS, which is what every other row's stamp means. Three notes in this
# corpus use the form and nothing ever wrote it down, so a note was convicted
# of a misshapen row for recording where a claim ended. The tail is spelled out
# -- the word, then a second anchor, then a second class -- rather than
# permitted as "anything before the dash", because the loose version would stop
# this check reporting the shape it exists to report.
# THE SECOND ANCHOR IS CAPTURED, because for one day it was not. The widening
# validated only what it captured, which was the start, so `[00:99]` was named
# in a row that ended there and silent in a row that ran to there, and a claim
# that finished fifty seconds before it began read as a row. Both shapes were
# reported before this form was understood; capturing the end is what keeps
# understanding the form from costing the check that read it.
_RANGE_TAIL = rf"(?:\s*to\s+`\[({_STAMP})\]`\s+`[A-Z-]+`)?"
RE_ROW = re.compile(
    rf"^\s*[-*]\s+`\[({_STAMP})\]`\s+`([A-Z-]+)`{_RANGE_TAIL}"
    rf"\s*(?:\([^)]*\)\s*)?[—–-]+\s*(.*)$")
# A line that was trying to be a row. Reported rather than dropped, so the next
# unforeseen shape is loud instead of silent.
# A SIGN IS PART OF WHAT THE LINE WAS TRYING TO SAY, so this pattern reads one
# and the row pattern does not. `- `[-00:10]` `SPOKEN` — …` is a row to every
# reader who is not this parser: it was not a row, and it was not row-shaped
# either, so it fell through as prose and the whole claim vanished with no row,
# no refusal and no count. A stamp before the recording started is a finding,
# and a finding has to be said out loud.
RE_ROWISH = re.compile(rf"^\s*[-*]\s+`?\[[-+]?{_STAMP}\]`?\s+`?[A-Z-]{{4,}}`?")
RE_ANCHOR = re.compile(rf"`?\[({_STAMP})\]`?")
# An HTML comment is not the note. It renders as nothing, and an adversarial
# lane used one to hide an anchor for every minute of a two-hour recording.
RE_COMMENT = re.compile(r"<!--.*?-->", re.S)
# Bookkeeping, not content. Counting the density paragraph as carried text would
# let a note improve its own score by describing itself.
RE_RUN_NOTES = re.compile(r"^##\s+Run notes\s*$", re.MULTILINE)
RE_KEY = re.compile(r"^[A-Za-z_][\w-]*\s*:")
# A code fence, in markdown's own vocabulary rather than this file's guess at
# it: three or more backticks, or three or more tildes.
RE_FENCE = re.compile(r"^(`{3,}|~{3,})")


def seconds_of(stamp: str) -> float:
    """`MM:SS` or `H:MM:SS` to seconds, refusing what a clock cannot say.

    `[59:99]` is not a late minute, it is a typed anchor nobody checked. Only
    the leading field may run past 59, because a recording may be 130 minutes
    long and be written `130:12`.
    """
    parts = [int(p) for p in stamp.split(":")]
    if any(p > 59 for p in parts[1:]) or any(p < 0 for p in parts):
        raise ValueError(f"{stamp} is not a time")
    while len(parts) < 3:
        parts.insert(0, 0)
    return parts[0] * 3600.0 + parts[1] * 60.0 + parts[2]


def stem(word: str) -> str:
    """Enough to match plurals and tenses, and no more.

    A note writes "companies" where the transcript said "company". A full
    stemmer would also collapse words that mean different things, and this
    metric is a comparison between two notes -- an over-eager stem inflates both
    and tells the reader less.
    """
    for suffix, replacement in (("ies", "y"), ("ing", ""), ("ed", ""),
                                ("es", ""), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)] + replacement
    return word


# What one line is to the row parser. Named rather than spelled out, because
# two readers ask this question -- `read_note`, which collects the rows, and
# the anchor walk in `measure`, which has to know which lines were REFUSED --
# and a line that is a thrown-out row to one and ordinary prose to the other is
# exactly the half-credit this vocabulary exists to close (V5 section 1 gap a).
ROW, IMPOSSIBLE, MISSHAPEN, UNREAD, PROSE = (
    "row", "impossible", "misshapen", "unread", "prose")
# The two refusals are NOT the same finding, and treating them alike cost the
# corpus 234 anchors across 8 notes on its first day (round-13 F2).
#
#   MISSHAPEN / IMPOSSIBLE  the parser READ the line and refused what it said:
#                           a range ending before it starts, a stamp no clock
#                           can say. The note's own claim about the clock is
#                           wrong, so it buys nothing.
#   UNREAD                  the parser could not read the line at all. In this
#                           corpus that is overwhelmingly one form -- a claim
#                           written across two stamps with no joining word,
#                           95 of 105 refused lines -- carrying seven to
#                           sixteen words of written claim each. The form not
#                           being in `RE_ROW` is a fact about `RE_ROW`, and it
#                           is not evidence that nobody wrote from that minute.
#
# Both are reported, exactly as before. Only the first forfeits anchors.
REFUSED = (IMPOSSIBLE, MISSHAPEN)


def _row_of(line: str) -> tuple[str, object]:
    """Read one line as a claim row, a refusal, or neither.

    `(ROW, (seconds, class, text))` when it parses. `(IMPOSSIBLE, stamp)` when
    a stamp is not a time a clock can say. `(MISSHAPEN, line)` when the line
    parsed and what it said about the clock is wrong. `(UNREAD, line)` when it
    was trying to be a row in a shape this pattern has never read. `(PROSE,
    line)` when it never was one.

    UNREAD and PROSE keep their anchors; the other two refusals do not.
    """
    match = RE_ROW.match(line)
    if not match:
        return (UNREAD, line) if RE_ROWISH.match(line) else (PROSE, line)
    try:
        at = seconds_of(match.group(1))
    except ValueError:
        return (IMPOSSIBLE, match.group(1))
    # Where a row names where it ENDED, that stamp answers to the same clock.
    # An unsayable end is the same finding as an unsayable start, so it gets
    # the same name; an end BEFORE the start is neither stamp's fault and is
    # reported as the shape it is, which is what this line was reported as
    # before the range form was read at all.
    end = match.group(3)
    if end is not None:
        try:
            until = seconds_of(end)
        except ValueError:
            return (IMPOSSIBLE, end)
        if until < at:
            return (MISSHAPEN, line)
    else:
        until = at
    # A ROW WITH NOTHING AFTER THE DASH IS THE PADDING THESE GUARDS EXIST TO
    # CATCH. It entered the row list and the `carried_per_row` denominator while
    # carrying no words at all, so a note could improve the look of its own
    # density by adding rows that say nothing.
    body = match.group(4).strip()
    if not body:
        return (MISSHAPEN, line)
    # THE ROW KEEPS ITS END. Where it names one, that is where the claim closed;
    # where it does not, a claim occupies the instant it is anchored at. A
    # window asks which rows TOUCH it, and it could not ask that while the end
    # was parsed, validated and then dropped.
    return (ROW, (at, match.group(2), body, until))


def _in_span(stamp: str, span: tuple[float, float]) -> bool:
    """Is this written stamp a second inside the span being measured?"""
    try:
        at = seconds_of(stamp)
    except ValueError:
        return False
    return span[0] <= at <= span[1]


def read_note(path: Path) -> tuple[str, list[tuple[float, str, str, float]],
                                   list[str], list[str]]:
    """The note's carried text, its claim rows, unsayable stamps, and misshapen lines.

    Frontmatter is dropped because it is metadata, and `## Run notes` because it
    is the note talking about itself. An impossible stamp is collected rather
    than raised on: one bad row must not stop the other four hundred being
    measured, and it must not pass unmentioned either.
    """
    text = RE_COMMENT.sub(" ", path.read_text(encoding="utf-8"))
    text = strip_frontmatter(text)
    # A TRAILING heading, not the first one that matches. `## Run notes` is a
    # bookkeeping section at the foot of a note, and cutting at the first match
    # threw away four fifths of a note whose METHOD section carried the same
    # name -- which then measured as a note that had written nothing at all.
    # The test is what the cut would REMOVE: bookkeeping is a paragraph, so a
    # heading whose tail is more than half the note is a section that shares the
    # name, and leaving it in costs a paragraph while cutting it costs the note.
    cuts = [c for c in RE_RUN_NOTES.finditer(text)
            if (len(text) - c.start()) * 2 <= len(text)]
    if cuts:
        text = text[: cuts[-1].start()]

    rows: list[tuple[float, str, str, float]] = []
    impossible: list[str] = []
    misshapen: list[str] = []
    lines = text.splitlines()
    inside, unclosed = fenced_lines(lines)
    if unclosed is not None:
        misshapen.append(lines[unclosed].strip()[:60])
    for n, line in enumerate(lines):
        if n in inside:
            continue
        kind, value = _row_of(line)
        if kind == ROW:
            rows.append(value)
        elif kind == IMPOSSIBLE:
            impossible.append(value)
        elif kind in (MISSHAPEN, UNREAD):
            misshapen.append(value.strip()[:60])
    return text, rows, impossible, misshapen


def fenced_lines(lines: list[str]) -> tuple[frozenset[int], int | None]:
    """Which lines sit inside a code fence, and where an unclosed one opened.

    A FENCE IS HOW A NOTE SAYS "THIS IS A SHAPE, NOT A CLAIM". Writing the row
    format into a note charged that note with the minute in its own example --
    an anchor it never claimed, in a row nobody wrote.

    AN UNCLOSED FENCE IS NOT A FENCE, AND QUOTES NOTHING. A toggle read the rest
    of the file as quoted text, so a note that opened a block and forgot to
    close it measured as a note that made one claim -- every row after that line
    gone, and gone SILENTLY. Reporting the stray line fixed the silence and left
    the loss: measured again, an unclosed opener still cost two real anchors and
    moved a note's unwritten minutes from five to seven. So an opener with no
    closer quotes NOTHING; its lines stay claims, and the line that opened is
    reported like any other misshapen one. Losing a claim a person wrote is the
    worse of the two errors, and this reader is not rendering the page.

    THE MARKER IS MARKDOWN'S, not this file's guess at it: three or more
    backticks or three or more tildes, and a closer that is the same character,
    at least as long, and carries nothing else. `~~~` was a fence to every
    renderer and not to an earlier version of this reader, and four backticks
    closed a three-backtick block that should have swallowed it.

    THE INDENT IS NOT. Markdown allows three spaces at the margin and measures
    from the content column inside a list item; this reader has no list context,
    so an example written under a nested bullet indents four spaces, no fence
    was seen, and the example counted as a claim -- the rule's own subject,
    reached by a different number of spaces. A marker is a marker at any indent
    here. The cost is the other side of that trade: a closer indented four
    spaces closes rather than reading as code, and a marker inside an indented
    code block opens. Both mistakes end a fence EARLY, which keeps a person's
    rows as claims, and that is the direction this reader is willing to be wrong
    in.
    """
    inside: set[int] = set()
    opener: tuple[int, str, int] | None = None
    # Held rather than added, because whether these are quoted is not known
    # until a closer arrives -- and if none does, they were never quoted at all.
    pending: set[int] = set()
    for n, line in enumerate(lines):
        stripped = line.lstrip(" \t")
        mark = RE_FENCE.match(stripped)
        if opener is None:
            if mark:
                opener = (n, mark.group(1)[0], len(mark.group(1)))
                pending = set()
            continue
        at, char, width = opener
        if (mark and mark.group(1)[0] == char and len(mark.group(1)) >= width
                and not stripped[len(mark.group(1)):].strip()):
            inside |= pending
            opener = None
            pending = set()
            continue
        pending.add(n)
    return frozenset(inside), opener[0] if opener else None


def strip_frontmatter(text: str) -> str:
    """Drop a YAML block, and nothing that merely starts with a rule.

    `---` opens frontmatter and also draws a horizontal rule, and the two are
    told apart by what follows: a closing `---` nearby, with `key: value` lines
    between. Reading a rule as frontmatter deleted an entire note here and
    reported it as carrying 0.1% of the recording.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for index in range(1, min(len(lines), FRONTMATTER_LINES)):
        if lines[index].strip() != "---":
            continue
        block = [line for line in lines[1:index] if line.strip()]
        keyed = sum(1 for line in block
                    if RE_KEY.match(line) or line.lstrip().startswith("#"))
        if block and keyed * 2 >= len(block):
            return "\n".join(lines[index + 1:])
        return text
    return text


def name_candidates(text: str) -> set[str]:
    """Tokens capitalised where a sentence did not just begin.

    The recogniser capitalises the first word of every sentence, so that
    position carries no information. Everything after it does.
    """
    out: set[str] = set()
    for sentence in re.split(r"[.!?]+", text):
        tokens = RE_RAW.findall(sentence)
        for token in tokens[1:]:
            if len(token) > 1 and token[0].isupper() and token.lower() != "i":
                out.add(token.lower())
    return out


def salient(segments: list[dict], span: tuple[float, float] | None
            ) -> dict[str, set[str]]:
    """The three carryable sets, over the span, ranked by the whole file.

    Document frequency is computed over the WHOLE file and the sets are then
    restricted to the span, because a token is rare in a recording, not in ten
    minutes of one -- inside a short span almost everything looks rare.
    """
    frequency: dict[str, int] = {}
    for seg in segments:
        for token in set(RE_WORD.findall(seg["text"].lower())):
            frequency[token] = frequency.get(token, 0) + 1
    # RARE_DF is a count, and a count means different things at different
    # lengths: three segments out of two thousand is 0.14% of the recording,
    # three out of three is all of it. So the cutoff scales down on short input
    # and stops at RARE_DF, which is where it stops meaning "rare" at all.
    cutoff = max(1, min(RARE_DF, len(segments) // 20))

    inside = segments if span is None else [
        s for s in segments if s["start"] <= span[1] and s["end"] >= span[0]]

    figures: set[str] = set()
    names: set[str] = set()
    terms: set[str] = set()
    for seg in inside:
        capitalised = name_candidates(seg["text"])
        for token in RE_WORD.findall(seg["text"].lower()):
            if any(c.isdigit() for c in token):
                figures.add(token)
            elif token in capitalised:
                names.add(token)
            elif (len(token) >= MIN_TERM_LEN
                  and frequency.get(token, 0) <= cutoff):
                terms.add(token)
    # DISJOINT, so the three sets sum to the total rather than over-counting it.
    # One token can qualify twice -- a name the recogniser also wrote lowercase
    # somewhere is both a name and a rare term -- and counting it in both makes
    # the denominator larger than the number of distinct things there are to
    # carry, which is a lie in whichever direction the note happens to fall.
    names -= figures
    terms -= figures | names
    return {"figures": figures, "names": names, "terms": terms}


def carried(text: str) -> set[str]:
    """Every token the note holds, stemmed, as a set. Repetition cannot help."""
    tokens = RE_WORD.findall(text.lower())
    return {stem(t) for t in tokens} | set(tokens)


def recall(wanted: set[str], held: set[str]) -> tuple[int, int]:
    hit = sum(1 for token in wanted if token in held or stem(token) in held)
    return hit, len(wanted)


def dead_buckets(anchors: list[float], span: tuple[float, float]
                 ) -> tuple[int, int, int, float]:
    """Minutes of the span with no anchor: (dead, total, longest run, run start)."""
    start, end = span
    total = max(1, int((end - start) // BUCKET_SECONDS) + 1)
    live = {int((a - start) // BUCKET_SECONDS) for a in anchors
            if start <= a <= end}
    dead = run = best = 0
    run_start = best_start = 0
    for index in range(total):
        if index in live:
            run = 0
            continue
        dead += 1
        if run == 0:
            run_start = index
        run += 1
        if run > best:
            best, best_start = run, run_start
    return dead, total, best, start + best_start * BUCKET_SECONDS


def near_duplicates(rows: list[tuple[float, str, str]], reach: int = 5
                    ) -> list[tuple[float, str]]:
    """Rows that repeat one of the rows just before them.

    A window of five rather than a full pairwise sweep: a note repeats itself
    where it is padding or where two windows overlapped, and both of those are
    local. A full sweep would also flag a claim deliberately restated in a later
    section, which is not the defect being looked for.
    """
    out: list[tuple[float, str]] = []
    for index, (at, _cls, text, _until) in enumerate(rows):
        key = " ".join(RE_WORD.findall(text.lower()))
        if not key:
            continue
        for previous in rows[max(0, index - reach):index]:
            other = " ".join(RE_WORD.findall(previous[2].lower()))
            if not other:
                continue
            if SequenceMatcher(None, key, other, autojunk=False).ratio() >= NEAR_ROW:
                out.append((at, text))
                break
    return out


def span_text(body: str, span: tuple[float, float]) -> str:
    """The lines of a note that anchor into a span.

    Rows and prose sentences both count: what is being asked is "what did this
    note write about these minutes", and this corpus answers that in both
    shapes. A line with no anchor belongs to no stretch and is left out.
    """
    kept: list[str] = []
    for line in body.splitlines():
        for stamp in RE_ANCHOR.findall(line):
            try:
                at = seconds_of(stamp)
            except ValueError:
                continue
            if span[0] <= at <= span[1]:
                kept.append(line)
                break
    return "\n".join(kept)


def verbatim_share(body: str, segments: list[dict], run: int = VERBATIM_RUN
                   ) -> float:
    """Share of the note's words sitting inside a long verbatim run of the recording.

    Quoting is how this corpus works, so this cannot be a ban. It is a ceiling:
    a note that reproduces the transcript scores a perfect recall while carrying
    no judgement at all, and nothing else here can tell that from thoroughness.
    """
    said = RE_WORD.findall(
        " ".join(s["text"] for s in segments).lower())
    # ANCHORS OUT FIRST. A pasted transcript writes `[MM:SS]` before every line,
    # and those digits become tokens that break the very runs this looks for:
    # the paste measured 0% verbatim until they were removed.
    wrote = RE_WORD.findall(RE_ANCHOR.sub(" ", body).lower())
    if len(wrote) < run or len(said) < run:
        return 0.0
    grams = {tuple(said[i:i + run]) for i in range(len(said) - run + 1)}
    inside = [False] * len(wrote)
    for i in range(len(wrote) - run + 1):
        if tuple(wrote[i:i + run]) in grams:
            for j in range(i, i + run):
                inside[j] = True
    return sum(inside) / len(wrote)


def measure(note: Path, transcript: Path, span: tuple[float, float] | None
            ) -> dict:
    body, rows, impossible, misshapen = read_note(note)
    segments = load_segments(transcript)
    windowed = span is not None
    if span is None:
        lo = min((s["start"] for s in segments), default=0.0)
        hi = max((s["end"] for s in segments), default=0.0)
        span = (lo, hi)

    # ASKED ABOUT A SPAN, ANSWER ABOUT THE SPAN. An earlier version counted the
    # whole note's text against a ten-minute window's vocabulary and called the
    # generosity "the safe direction"; an adversarial lane then scored 89.4% on
    # a stretch the note had written nothing about, because a two-hour talk
    # reuses its own words. So with `--span` the note's text is the lines that
    # anchor INTO that span -- rows and prose sentences alike -- and without one
    # it is the whole note, which is the same rule at full width.
    # A ROW TOUCHES A WINDOW, it does not have to start in one. Filtering on the
    # start alone put a claim that opened at 04:50 and closed at 05:30 in NONE
    # of this window's row numbers -- not `rows`, not `carried_per_row`, not
    # `row_words` -- while its words were inside the window's scope text, so
    # the note carried the claim and every number about that minute said it did
    # not. A row with no end of its own still ends where it begins.
    in_span = [r for r in rows if r[3] >= span[0] and r[0] <= span[1]]
    # AND THE NOTE'S TEXT IS NOT ITS EXAMPLES EITHER. The row parse and the
    # anchor walk both stopped at a fence and the vocabulary did not, so a note
    # that documented the row format carried the words of its own illustration
    # against the transcript and bought recall share for them. Blanked rather
    # than removed: `span_text` cuts by line, and dropping lines would move
    # every line after the fence.
    body_lines = body.splitlines()
    quoted, _ = fenced_lines(body_lines)
    spoken = "\n".join("" if n in quoted else line
                       for n, line in enumerate(body_lines))
    scope = span_text(spoken, span) if windowed else spoken
    wanted = salient(segments, span)
    body_tokens = carried(scope)
    row_tokens = carried(" ".join(r[2] for r in in_span))

    result: dict = {
        "note": note.name,
        "transcript": transcript.name,
        "span": [span[0], span[1]],
        "rows": len(in_span),
        "rows_in_note": len(rows),
        "impossible_stamps": impossible,
        "misshapen_rows": misshapen,
        "recall": {},
    }
    total_hit = total_want = 0
    for key, tokens in wanted.items():
        hit, want = recall(tokens, body_tokens)
        row_hit, _ = recall(tokens, row_tokens)
        total_hit += hit
        total_want += want
        result["recall"][key] = {"carried": hit, "of": want,
                                 "carried_by_rows": row_hit}
    result["recall"]["all"] = {"carried": total_hit, "of": total_want,
                               "share": round(total_hit / total_want, 4)
                               if total_want else 0.0}

    # EVERY anchor in the note, not only the ones on claim rows. Notes in this
    # corpus cite seconds inside prose and inside tables as well as in rows, and
    # a dead-stretch count drawn from rows alone reported 29 unwritten minutes
    # in a note that had in fact written from all of them.
    # AN ANCHOR ONLY COUNTS WHERE SOMETHING WAS WRITTEN. A line of bare
    # timestamps fills every hole in this check while saying nothing, which is
    # how an adversarial lane made a note with a twenty-nine-minute gap report
    # none: it hid one anchor per minute in an HTML comment.
    # AND A ROW THE PARSER REFUSED IS NOT ONE OF THEM. This walk read every
    # line of the body alike, so a backwards range gave 0 rows and the same two
    # anchors as the row that parsed, and both reported the same dead minutes:
    # a note scored identical coverage whether its row was legal or not. Prose
    # keeps its anchors -- it never claimed to be a row -- but a line that
    # tried and failed buys nothing (V5 section 1 gap a).
    anchors: list[float] = []
    lost: set[int] = set()
    # THE FENCE IS SKIPPED HERE TOO, and stopping only the row parse at it was
    # half a fix: the example's anchor still filled a dead-minute bucket, so a
    # note that documented the row format bought itself a written minute on a
    # row nobody wrote. An illustration is not a claim in ANY count -- the
    # vocabulary above is fenced from the same set.
    for n, line in enumerate(body_lines):
        if n in quoted:
            continue
        stamps = RE_ANCHOR.findall(line)
        if not stamps:
            continue
        said = len(RE_WORD.findall(RE_ANCHOR.sub(" ", line).lower()))
        if _row_of(line)[0] in REFUSED:
            # WHAT THE REFUSAL COST, and not a stamp more. Every filter the
            # anchors themselves pass is applied first: a line too thin to have
            # been read at all cost nothing, a stamp no clock can say was never
            # going to fill a bucket, and one second written twice on one line
            # is one bucket. The number is read by a person deciding whether a
            # dead stretch is real (round-13 F6).
            if said >= MIN_LINE_WORDS:
                lost |= {int(seconds_of(s)) for s in stamps
                         if _in_span(s, span)}
            continue
        if said < MIN_LINE_WORDS:
            continue
        for stamp in stamps:
            try:
                at = seconds_of(stamp)
            except ValueError:
                continue
            if span[0] <= at <= span[1]:
                anchors.append(at)
    dead, buckets, run, run_at = dead_buckets(anchors, span)
    result["dead"] = {"minutes": dead, "of": buckets, "longest_run": run,
                      "longest_run_at": run_at}
    result["anchors"] = len(anchors)
    # Printed rather than silently dropped: a note whose coverage fell when its
    # rows stopped parsing should be able to see why in the same object.
    result["anchors_on_refused_rows"] = len(lost)
    result["anchored_seconds"] = len({int(a) for a in anchors})
    # THE FRAGMENTATION NUMBER. Rows per second turned out not to be one: the
    # corpus sits at 1.0-1.5 whatever the note's quality, because a fragmented
    # note spreads across more seconds rather than stacking on one. What moves
    # is how much each row carries. A note that says one thing in six rows has
    # the same numerator and six times the denominator.
    row_hit_total = sum(v["carried_by_rows"] for v in result["recall"].values()
                        if "carried_by_rows" in v)
    result["carried_per_row"] = round(row_hit_total / len(in_span), 2) if in_span else 0.0
    repeats = near_duplicates(in_span)
    result["near_duplicate_rows"] = len(repeats)
    result["near_duplicate_share"] = round(len(repeats) / len(in_span), 4) if in_span else 0.0
    result["row_words"] = len(" ".join(r[2] for r in in_span).split())
    result["note_words"] = len(scope.split())
    result["words_per_carried_token"] = round(
        result["row_words"] / row_hit_total, 1) if row_hit_total else 0.0
    result["verbatim_share"] = round(verbatim_share(scope, segments), 4)
    # A note carrying the recording's whole vocabulary in a few thousand words
    # is a word list. Real notes spend fifteen to sixty words per token carried.
    result["list_density"] = round(
        total_hit / result["note_words"], 4) if result["note_words"] else 0.0
    return result


def note_only_defects(note: Path) -> list[str]:
    """The findings that read the NOTE and never the rendering.

    `E-COV-STAMP` and `E-COV-ROWSHAPE` are both about how a line is written,
    and neither has ever needed a transcript. They were unreachable anyway,
    because the only route to this module is `note_gates`, which skips a note
    whose oracle it cannot open -- so for these two a skip was exactly a pass.
    The corpus reported zero misshapen rows while 21 skipped notes carried 105
    of them (V2 finding V2-6).

    Reads the note itself and reports nothing when it cannot: inventing a row
    shape for a file nobody could parse is the opposite mistake.
    """
    try:
        result = {"impossible_stamps": [], "misshapen_rows": []}
        _body, _rows, impossible, misshapen = read_note(note)
    except (OSError, UnicodeDecodeError):
        return []
    result["impossible_stamps"] = impossible
    result["misshapen_rows"] = misshapen
    return [line for line in defects({**_EMPTY_RESULT, **result})
            if "E-COV-STAMP" in line or "E-COV-ROWSHAPE" in line]


# The shape `defects` reads, with every threshold answered in the direction
# that reports nothing. `note_only_defects` fills in the two fields it owns and
# takes the two lines they produce, so the wording of a finding lives once.
_EMPTY_RESULT: dict = {
    "impossible_stamps": [], "misshapen_rows": [], "rows": 0,
    "dead": {"minutes": 0, "of": 0, "longest_run": 0, "longest_run_at": 0.0},
    "near_duplicate_rows": 0, "near_duplicate_share": 0.0,
    "verbatim_share": 0.0, "list_density": 0.0, "note_words": 0,
    "recall": {"all": {"carried": 0, "of": MIN_SALIENT, "share": 0.0}},
}


def defects(result: dict) -> list[str]:
    out: list[str] = []
    for stamp in result.get("impossible_stamps", []):
        out.append(
            f"[00:00] E-COV-STAMP `[{stamp}]` is not a time; no recording has a "
            f"minute or second past 59, so nothing can ever resolve it")
    dead = result["dead"]
    if dead["longest_run"] >= DEAD_RUN:
        out.append(
            f"[{hms(dead['longest_run_at'])}] E-COV-DEAD {dead['longest_run']} "
            f"consecutive minute(s) of the span carry no anchor at all; nothing "
            f"in the note was written from that stretch")
    if result["near_duplicate_share"] > MAX_NEAR_SHARE:
        out.append(
            f"[00:00] E-COV-REPEAT {result['near_duplicate_rows']} of "
            f"{result['rows']} rows repeat a row beside them "
            f"({result['near_duplicate_share']:.0%}); a claim said twice is not "
            f"two claims")
    if result.get("verbatim_share", 0.0) >= DUMP_SHARE:
        out.append(
            f"[00:00] E-COV-DUMP {result['verbatim_share']:.0%} of this note is "
            f"a verbatim run of the recording; reproducing a transcript carries "
            f"everything and reports nothing")
    if result.get("list_density", 0.0) >= LIST_DENSITY:
        out.append(
            f"[00:00] E-COV-LIST {result['recall']['all']['carried']} carried "
            f"token(s) in {result['note_words']} word(s) of note; at that "
            f"density this is a word list rather than a set of claims")
    shapes = result.get("misshapen_rows", [])
    for line in shapes[:ROWSHAPE_SHOWN]:
        out.append(
            f"[00:00] E-COV-ROWSHAPE a line looks like a claim row and does not "
            f"parse as one, so nothing counted it: {line}")
    # THE REST ARE COUNTED, not dropped. Five per note was a silent truncation:
    # a reader shown 32 lines over a corpus carrying 105 has no way to tell the
    # cap from the count, and "32 findings" reads as the number there are
    # (round-14 F5).
    if len(shapes) > ROWSHAPE_SHOWN:
        out.append(
            f"[00:00] E-COV-ROWSHAPE {len(shapes) - ROWSHAPE_SHOWN} more "
            f"line(s) in this note look like claim rows and do not parse as "
            f"one; {len(shapes)} in total, {ROWSHAPE_SHOWN} shown")
    if result["recall"]["all"]["of"] < MIN_SALIENT:
        out.append(
            f"[00:00] E-COV-THIN only {result['recall']['all']['of']} salient "
            f"token(s) in this span; the recall figure above is noise and must "
            f"not be quoted as a comparison")
    return out


def selftest() -> int:
    cases = 0

    def check(label: str, got, want) -> None:
        nonlocal cases
        cases += 1
        if got != want:
            raise AssertionError(f"{label}: got {got!r}, want {want!r}")

    # The harness decides what ran. `check`'s calls ARE this module's cases,
    # which is why its name is handed over here rather than kept private, and
    # `done()` below is where the evidence goes and a wrong answer is refused.
    from watchquality import selftest_proof
    proof = selftest_proof.begin(check)

    import contextlib
    import io
    import tempfile

    check("a plural matches its singular", stem("companies"), "company")
    check("a short word is left alone", stem("its"), "its")
    check("a tense is stripped", stem("dragging"), "dragg")
    check("an hour-long stamp is read", seconds_of("1:03:12"), 3792.0)
    check("...and a short one", seconds_of("59:25"), 3565.0)

    check("a name mid-sentence is a name",
          "trellick" in name_candidates("He said Trellick Partners pays more."), True)
    check("...and the first word of a sentence is not",
          "he" in name_candidates("He said Trellick Partners pays more."), False)
    check("...nor is a lone I", name_candidates("Well I went."), set())

    segments = [
        {"start": 0.0, "end": 10.0, "text": "The price was 500 dollars in Lisbon."},
        {"start": 10.0, "end": 20.0, "text": "The price was ordinary and the price was fine."},
        {"start": 20.0, "end": 30.0, "text": "A quinquireme carried the price."},
    ]
    sets = salient(segments, None)
    check("a digit is a figure", sets["figures"], {"500"})
    check("a mid-sentence capital is a name", sets["names"], {"lisbon"})
    check("a rare long word is a term", "quinquireme" in sets["terms"], True)
    check("...and a repeated one is not", "price" in sets["terms"], False)
    check("...nor is a short one", "was" in sets["terms"], False)

    # THE WHOLE POINT, stated as a case: splitting one claim into many rows must
    # not move recall, and neither must saying nothing at length.
    one_row = "- `[00:01]` `SPOKEN` — the price was 500 dollars in Lisbon.\n"
    six_rows = ("- `[00:01]` `SPOKEN` — the price was 500 dollars in Lisbon.\n"
                "- `[00:02]` `SPOKEN` — a price is mentioned here.\n"
                "- `[00:03]` `SPOKEN` — the speaker returns to the topic.\n"
                "- `[00:04]` `SPOKEN` — an example is offered at this point.\n"
                "- `[00:05]` `SPOKEN` — the point is developed further.\n"
                "- `[00:06]` `SPOKEN` — the discussion continues from there.\n")
    lean = carried(one_row)
    padded = carried(six_rows)
    for key, tokens in sets.items():
        check(f"padding does not raise {key} recall",
              recall(tokens, padded)[0], recall(tokens, lean)[0])

    check("a hole in the middle is counted",
          dead_buckets([0.0, 300.0], (0.0, 300.0))[0], 4)
    check("...and its longest run named",
          dead_buckets([0.0, 300.0], (0.0, 300.0))[2], 4)
    check("a well-spread note has no hole",
          dead_buckets([0.0, 60.0, 120.0], (0.0, 179.0))[0], 0)

    repeated = [(1.0, "SPOKEN", "the price was five hundred dollars", 1.0),
                (2.0, "SPOKEN", "the price was five hundred dollars", 2.0),
                (3.0, "SPOKEN", "an entirely different observation follows", 3.0)]
    check("a row repeating its neighbour is caught",
          len(near_duplicates(repeated)), 1)

    # THE COMMAND, end to end. A version of a sibling module shipped with main()
    # raising on its own summary line: it printed its table and then died, and a
    # check that reads only the exit code cannot tell that from a defect found.
    def run(note_text: str, *flags: str) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.md"
            note.write_text("---\ntitle: t\n---\n\n" + note_text, encoding="utf-8")
            transcript = Path(tmp) / "t.json"
            transcript.write_text(json.dumps(segments), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main([str(note), str(transcript), *flags])
        return code, out.getvalue(), err.getvalue()

    code, out, err = run(one_row)
    check("a thin span refuses to be quoted as a comparison", code, 1)
    check("...by name", "E-COV-THIN" in out, True)
    check("...and the summary still prints", "salient token(s) carried" in err, True)

    code, out, _ = run(six_rows, "--json")
    check("--json exits the same way", code, 1)
    check("...and parses", json.loads(out)["rows"], 6)

    # EVERY REPAIR BELOW HAS AN ATTACK BEHIND IT. A lane whose only instruction
    # was to build inputs that score well defeated nine of this file's promises
    # on its first attempt -- six by scoring garbage clean, three by marking
    # down notes that were correct.
    def parsed(note_text: str) -> list:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "n.md"
            path.write_text(note_text, encoding="utf-8")
            return read_note(path)

    for dash, name in (("—", "em dash"), ("–", "en dash"), ("-", "hyphen")):
        rows_seen = parsed(f"- `[00:01]` `SPOKEN` {dash} a claim.\n")[1]
        check(f"a row written with an {name} is a row", len(rows_seen), 1)
    check("a speaker mark before the dash does not hide a row",
          len(parsed("- `[00:01]` `SPOKEN` (host) — a claim.\n")[1]), 1)
    check("a stamp past ninety-nine minutes is a stamp",
          parsed("- `[105:20]` `SPOKEN` — late in a long recording.\n")[1][0][0],
          6320.0)
    check("a line that meant to be a row and is not gets reported",
          len(parsed("- `[00:01]` `SPOKEN` a claim with no dash at all.\n")[3]), 1)

    hr = "---\n\n# Title\n\n- `[00:01]` `SPOKEN` — a claim.\n"
    check("an opening horizontal rule is not frontmatter",
          len(parsed(hr)[1]), 1)
    fm = "---\ntitle: t\nvideo_id: x\n---\n\n- `[00:02]` `SPOKEN` — a claim.\n"
    check("...and real frontmatter still goes", "title: t" in parsed(fm)[0], False)
    check("...leaving the note", len(parsed(fm)[1]), 1)

    early = ("## Run notes\n\nmethod described up front\n\n"
             "- `[00:01]` `SPOKEN` — a claim.\n")
    check("a section named Run notes early does not delete the note",
          len(parsed(early)[1]), 1)
    both = early + "\n## Run notes\n\nNote density: 1 word over 1 minute.\n"
    check("...and the last one still cuts", "Note density" in parsed(both)[0], False)

    check("an HTML comment is not the note",
          "hidden" in parsed("<!-- hidden -->\n\ntext\n")[0], False)

    # A bare rail of anchors fills every hole while saying nothing.
    rail = " ".join(f"[{m:02d}:00]" for m in range(0, 6))
    body, _r, _i, _m = parsed(f"# t\n\n{rail}\n")
    live = [line for line in body.splitlines()
            if RE_ANCHOR.findall(line)
            and len(RE_WORD.findall(RE_ANCHOR.sub(" ", line))) >= MIN_LINE_WORDS]
    check("a line of bare anchors is not a stretch anyone wrote from", live, [])

    check("a paste of the recording is a paste",
          verbatim_share(" ".join(s["text"] for s in segments) * 3, segments,
                         run=6) > DUMP_SHARE, True)
    check("...and a note that quotes one line of it is not",
          verbatim_share("The price was 500 dollars in Lisbon, which tells you "
                         "the market was thinner than anyone admitted at the "
                         "time and stayed that way for years afterwards.",
                         segments) > DUMP_SHARE, False)

    code, out, _ = run("- `[00:01]` `SPOKEN` — a claim.\n", "--span", "0:00-9:99")
    check("a span that is not a time is refused", code, 2)
    code, out, _ = run("- `[59:99]` `SPOKEN` — a stamp no clock can say.\n")
    check("an impossible stamp is named", "E-COV-STAMP" in out, True)
    check("...and does not stop the run", code, 1)

    proof.done()
    print(f"# selftest OK ({cases} cases)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("note", nargs="?", type=Path)
    ap.add_argument("transcript", nargs="?", type=Path)
    ap.add_argument("--span", help="MM:SS-MM:SS, default the whole recording")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if args.selftest:
        return selftest()
    if not args.note or not args.transcript:
        ap.print_usage(sys.stderr)
        return 2

    span = None
    if args.span:
        try:
            lo, hi = args.span.split("-", 1)
            span = (seconds_of(lo.strip()), seconds_of(hi.strip()))
        except ValueError:
            print(f"{PROG}: --span wants MM:SS-MM:SS, got {args.span!r}",
                  file=sys.stderr)
            return 2
        if span[1] <= span[0]:
            print(f"{PROG}: --span ends before it starts", file=sys.stderr)
            return 2

    try:
        result = measure(args.note, args.transcript, span)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2

    found = defects(result)
    if args.json:
        # Defects go INSIDE the document rather than after it. Printing them
        # below the JSON would make `--json` unparseable exactly when there is
        # something to report, which is when a caller most needs to read it.
        result["defects"] = found
        print(json.dumps(result, indent=2))
    else:
        r = result["recall"]
        print(f"# {result['note']} vs {result['transcript']}, "
              f"{hms(result['span'][0])}-{hms(result['span'][1])}")
        print(f"carried\t{r['all']['carried']}\tof\t{r['all']['of']}"
              f"\t{r['all']['share']:.1%}")
        for key in ("figures", "names", "terms"):
            print(f"  {key}\t{r[key]['carried']}\tof\t{r[key]['of']}"
                  f"\t(rows alone {r[key]['carried_by_rows']})")
        print(f"dead minutes\t{result['dead']['minutes']}\tof\t"
              f"{result['dead']['of']}\tlongest run {result['dead']['longest_run']}")
        print(f"rows\t{result['rows']}\tover\t{result['anchored_seconds']}"
              f"\tanchored second(s), {result['carried_per_row']} carried per row")
        print(f"near-duplicate rows\t{result['near_duplicate_rows']}"
              f"\t{result['near_duplicate_share']:.1%}")
        print(f"row words\t{result['row_words']}\t"
              f"{result['words_per_carried_token']} per token the rows carried"
              f"\t(whole note {result['note_words']} words)")
        print(f"verbatim\t{result['verbatim_share']:.1%}\tof the note is a "
              f"{VERBATIM_RUN}-word run of the recording; carried per note word "
              f"{result['list_density']:.3f}")

    for defect in found:
        print(defect, file=sys.stderr if args.json else sys.stdout)
    print(f"# {result['recall']['all']['share']:.1%} of "
          f"{result['recall']['all']['of']} salient token(s) carried, "
          f"{len(found)} defect(s)", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
