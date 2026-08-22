#!/usr/bin/env python3
"""Refuse to let corpus data reach the public package.

`docs/watch-quality-package-plan.md` §5 makes this a publication gate, not a
nicety: the gates are going into a PUBLIC fork, so their source may not name a
video id, a corpus path, or a dated exemption. That rule was prose, and prose
does not fail a build. Here it is an exit code.

Three things are refused, each for its own reason:

  VIDEO-ID    an 11-character `[A-Za-z0-9_-]` token that mixes case and digits.
              That shape is a YouTube id and nothing else in this code is
              shaped like one. Checked in comments, docstrings and fixtures
              alike, because a fixture is published too.
  DATED       a bare ISO date next to an exemption word. Exemptions belong in
              `watch-quality.toml`, where they are printed on every run and may
              only shrink; one buried in a comment is invisible and permanent.
  CORPUS-PATH the private repository's own name, which leaks a directory
              layout and, through it, what is being graded.

False positives are expected and cheap: add the token to ALLOW below with a
reason. A false NEGATIVE is what this exists to prevent, so the shape test is
deliberately broad.

EVERY PUBLISHED TEXT FILE, not just the code. This scanner read `*.py` only for
its first two releases, so the README, the packaging metadata and the tests were
structurally invisible to it -- and that is exactly where the leaks were found,
by a reviewer reading rather than by this gate running: a real exemption reason
with its real date, a sentence quoted verbatim from a private document, and a
private recording's exact duration, all in README prose that had passed a clean
scan. A gate that only reads the files least likely to leak is a rubber stamp.

EVERY PUBLISHED DIRECTORY, for the same reason and a release later. Widening the
suffix list fixed the file TYPES and left the WALK where it was, on the package
directory alone, so a run still reported `15 file(s) scanned ... 0 corpus
reference(s)` and exited 0 on a day two files under `tests/` named the private
repository. The matching was never wrong; those files were simply never handed
to it. The published unit is the CHECKOUT -- README, changelog, skills, hooks,
tests -- so that is what is walked, and when there is no checkout to find the
run says how far it could see instead of reporting a small number as if it were
the whole.

Usage:
    scripts/wq_corpus_scan.py            # scan everything the checkout publishes
    scripts/wq_corpus_scan.py <path>...  # scan what you name, files or trees
    scripts/wq_corpus_scan.py --require-literals   # refuse a wordless run
    scripts/wq_corpus_scan.py --selftest

Exit: 0 clean, 1 something corpus-shaped is in the source, 2 usage error.

`--require-literals` is for a caller that cannot read the output, a push hook
being the case it was written for. The word list lives in the PRIVATE policy and
cannot be in the published checkout, so a run launched from the wrong directory,
or with the private checkout absent, finds no policy, scans every file against an
empty list, and exits 0. That run is indistinguishable from a clean one in its
exit code, and a hook reads nothing else. With the flag, no words in force is a 2.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROG = "wq_corpus_scan.py"
REQUIRE_FLAG = "--require-literals"

# An id is 11 chars of [A-Za-z0-9_-]. Bounded on both sides so a longer token
# (a sha, a base64 blob) does not match a window inside itself.
RE_ID = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{11}(?![A-Za-z0-9_-])")
# EITHER ORDER. The first version required the date to come first, so
# `frames reaped 2026-08-05` -- the way a person actually writes it -- walked
# straight through a rule whose whole subject is that sentence.
RE_DATED_EXEMPTION = re.compile(
    r"\d{4}-\d{2}-\d{2}.{0,80}?\b(exempt|exemption|reaped|waiv)"
    r"|\b(exempt|exemption|reaped|waiv)\w*.{0,80}?\d{4}-\d{2}-\d{2}",
    re.IGNORECASE)

# Tokens that pass the shape test and are not ids. Each needs a reason; an
# unexplained entry here is how this gate would rot into a rubber stamp.
ALLOW: dict[str, str] = {
    "-Xk4Rm2Qp7Z": "synthetic dash-id fixture in say_captions selftest",
    "Bt7Wn3Kd9Qy": "synthetic plain-id fixture in say_captions selftest",
    "A-Za-z0-9_-": "the character class itself, written out in a comment",
    "dQw4w9WgXcQ": "the internet's best-known public video, used as a README "
                   "example precisely because it is in no private corpus",
    "rlOpbu3Enkw": "the URL in the upstream project's own download test, "
                   "inherited with the fork and present in no private corpus",
    "colorE5E5E5": "a WebVTT cue-tag colour class, not an id -- the shape test "
                   "cannot tell hex from base64 and is not asked to",
    "Qwen3-ASR-1": "the first eleven characters of the Qwen/Qwen3-ASR-1.7B "
                   "model slug, a published name on a public model router",
}

# What counts as a published file. Everything in a public repository is
# published, so the list is about what can CARRY a leak in text, not about what
# a reader would call source. Binary fixtures are not read; a leak in one is a
# real risk this gate does not cover, and saying so is better than implying it.
#
# A file with NO suffix is read too. The suffix list is an allowlist, and an
# allowlist keyed on something five published files do not have is a hole rather
# than a rule: `LICENSE`, `.gitignore`, `.gitattributes` and `.skillignore` are
# all tracked, all human-written, and `.gitignore`'s whole job is to name
# directories -- which is the CORPUS-PATH class this gate refuses.
TEXT_SUFFIXES = frozenset({
    ".py", ".md", ".toml", ".txt", ".tsv", ".csv", ".json",
    ".cfg", ".ini", ".yaml", ".yml", ".sh", ".vtt", ".srt",
})

# On disk and published by nobody: version-control internals and tool caches.
# Everything else under the checkout is walked, INCLUDING files git does not
# track -- `git ls-files` would have been the tidier rule and the wrong one,
# because a leak that has not been committed yet is exactly the one worth
# catching, and it is untracked right up until the commit this gate exists to
# stop. The cost of the looser rule is noise; the cost of the tighter one is a
# miss.
#
# Matched against the path RELATIVE TO THE TARGET, never the absolute path. An
# independent review planted a checkout under a directory called `venv` and the
# run reported `0 file(s) scanned ... 0 corpus reference(s)` and exit 0 -- the
# silent clean report this module was rewritten to end, re-created one directory
# above the repository, by a rule that could see names nobody in the repository
# chose.
# Version control's own storage. Skipped in SILENCE, because it is not a
# directory anyone could have put published prose in and naming its four
# thousand object shards would bury the line that matters.
VCS_DIRS = frozenset({".git", ".hg", ".svn"})
# Generated, vendored or ephemeral. Skipped and REPORTED, because a human could
# reasonably have put a published page in one -- `docs/venv/guide.md` is a
# plausible tutorial -- and `watch-quality/build/` really does hold a tracked,
# stale copy of this package. Dropping those in silence under a line reading
# `0 corpus reference(s)` is the rubber stamp this module keeps being rewritten
# to stop being.
SKIP_DIRS = frozenset({
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    ".venv", "venv", "node_modules", "build", "dist",
}) | VCS_DIRS


def published_root(start: Path) -> tuple[Path, str | None]:
    """The checkout `start` sits in, or its own directory and why that is all.

    Returned as a pair rather than a path, because the two answers mean
    different things and the caller has to say which one it got. A package
    installed into site-packages has no repository above it, and reporting the
    package's file count as though it were the publication is the precise shape
    of the failure this function was added for.

    `pkg` itself is tested before its parents: a module sitting AT a checkout
    root is standing in one, and walking only upwards told it there was none.
    """
    start = start.resolve()
    pkg = start.parent if start.is_file() else start
    for directory in (pkg, *pkg.parents):
        if (directory / ".git").exists():
            return directory, None
    return pkg, ("no checkout above this package, so the scan reached the "
                 "package directory only")


def _by_suffix(paths: list[Path]) -> dict[str, int]:
    """How many files of each suffix, for a census line nobody has to count."""
    out: dict[str, int] = {}
    for p in paths:
        key = p.suffix.lower() or "(no suffix)"
        out[key] = out.get(key, 0) + 1
    return out


def publishable(path: Path) -> bool:
    """Can this file carry a leak in text? Suffix known, or no suffix at all."""
    return path.suffix.lower() in TEXT_SUFFIXES or not path.suffix


def collect(targets: list[Path],
            skipped: list[Path] | None = None,
            unread: list[Path] | None = None) -> list[Path]:
    """Every publishable text file under `targets`, sorted, deduplicated.

    A named directory is walked as named and never widened to its repository:
    `wq_corpus_scan.py some/dir` has to mean that directory, or nobody can scan
    a subtree without the answer quietly becoming the whole tree.

    TWO KINDS OF NOT-READ, and both are handed back rather than swallowed.
    Directories dropped by `SKIP_DIRS` go to `skipped`; files whose suffix is
    not on the allowlist go to `unread`. `docs/venv/guide.md` is a plausible
    published page and so is `NOTES.rst`, and dropping either in silence under a
    line reading `0 corpus reference(s)` is the part that would make this gate a
    rubber stamp again. The suffix list is an ALLOWLIST, so every format nobody
    thought of -- `.rst`, `.org`, `.adoc`, `.html` -- lands here.

    A symlinked DIRECTORY is not descended -- `rglob` does not follow one -- so
    a tree reachable only through a link is not scanned. A symlinked file is.
    """
    files: list[Path] = []
    for target in targets:
        if not target.is_dir():
            files.append(target)
            continue
        for p in target.rglob("*"):
            if not p.is_file():
                continue
            # DIRECTORY components only. Matching the whole path meant a
            # published file NAMED `build` was dropped, and reported as a
            # directory nobody could go and look at.
            parts = p.relative_to(target).parts[:-1]
            if SKIP_DIRS.intersection(parts):
                if skipped is not None and not VCS_DIRS.intersection(parts):
                    # The TOP-MOST skipped directory, not the file. Naming
                    # every file under one would be four thousand lines, and
                    # the caller acts on the directory.
                    cut = next(i for i, part in enumerate(parts)
                               if part in SKIP_DIRS)
                    skipped.append(target.joinpath(*parts[:cut + 1]))
                continue
            if not publishable(p):
                if unread is not None:
                    unread.append(p)
                continue
            files.append(p)
    # This scanner quotes every shape it refuses, so it would refuse itself --
    # ITSELF, resolved, and not every file that shares its name. A stale build
    # copy of this module is tracked, published, and was permanently unscanned
    # under the name comparison this replaces.
    me = Path(__file__).resolve()
    return sorted({f for f in files if f.resolve() != me})


# A moment, however it is written. NOT bracket-scoped: the first version matched
# `[07:04]` only, so the same second published as `[7:04]`, `[0:07:04]`,
# `(07:04)`, `[ 07:04 ]`, `[07:04-08:04]` or bare `07:04` was a different STRING
# and walked straight through -- six renderings, one keystroke apart from the
# one that was caught. Bounded on both sides so a longer number does not match a
# window inside itself, and minutes and seconds are 00-59 so a version or a
# ratio is not read as a time.
# A FRACTIONAL SECOND IS NOT AN ANCHOR. `00:00:02.000` is a subtitle cue, and a
# suite full of two-second synthetic cues has every small even second in it --
# which one note line or another also carries, so twenty-one fixtures that
# reproduce nothing were refused. Cue timing published verbatim is a real leak
# class and it is a different one: it needs the caption tables, not the notes.
RE_STAMP = re.compile(
    r"(?<![\d:])(\d{1,3}):([0-5]\d)(?::([0-5]\d))?(?![\d:])(?!\.\d)")


def _seconds(match: re.Match) -> int:
    """A matched stamp as the second it names, so renderings compare equal."""
    a, b, c = match.group(1), match.group(2), match.group(3)
    if c is None:
        return int(a) * 60 + int(b)
    return int(a) * 3600 + int(b) * 60 + int(c)


def stamps_in(line: str) -> frozenset[int]:
    """Every moment named on one line, in seconds."""
    return frozenset(_seconds(m) for m in RE_STAMP.finditer(line))


def anchor_sets_from_lines(lines) -> tuple[frozenset[int], ...]:
    """The lines that carry two or more moments, as sets of seconds."""
    return tuple(s for s in (stamps_in(line) for line in lines) if len(s) >= 2)


def anchor_sets(notes: Path) -> tuple[frozenset[int], ...]:
    """Every corpus note line carrying two or more anchors, as a set of stamps.

    The refused list can only refuse what somebody thought to write down, and
    nobody can write down the thing that actually identifies a recording: WHERE
    its moments are. Thirteen tracked lines in this fork reproduced a private
    note by its stamps alone and every one of them exited 0, correctly, because
    none of them held a refusable word.

    TWO OR MORE, and from ONE line. A single stamp is not a fingerprint -- this
    suite and the README are full of `[00:00]` -- and a corpus flattened into
    one bag of stamps would refuse any two round numbers. What reproduces a
    recording is stamps that stood together on one line of one note.
    """
    out: list[frozenset[int]] = []
    if not notes.is_dir():
        return ()
    for p in sorted(notes.rglob("*.md")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out.extend(anchor_sets_from_lines(text.splitlines()))
    return tuple(out)


def _round_pair(stamps: frozenset[int]) -> bool:
    """Two whole minutes, which is a coincidence rather than a fingerprint.

    `[00:00]` and `[02:00]` is the likeliest pair anybody writes in a README, a
    chapter list or a fixture, and one note line or another carries both. The
    report may not quote the stamps, so that author sees an exit 1 nothing can
    explain -- which is the shape of a gate somebody removes. Three round marks
    standing together is the note's shape rather than a coincidence, so the
    excuse stops at two.
    """
    return len(stamps) == 2 and all(s % 60 == 0 for s in stamps)


# Characters that are between letters without being anything. A refused literal
# is a NAME, and a name survives having its space written as a hyphen, an
# underscore, nothing at all, two spaces, a line break, or a zero-width space
# dropped in the middle of it. Every one of those evaded a `word in line` test
# while naming exactly the thing the policy refuses.
RE_INVISIBLE = re.compile(r"[­​-‏⁠﻿]")
RE_SEPARATOR = re.compile(r"[\s\-_]")


def _squash(text: str) -> tuple[str, list[int]]:
    """The text with separators and casing taken out, and a line per character.

    The line map is the half that makes a finding actionable: the squashed text
    has no line breaks left in it, so the offset of a match has to be carried
    back to the line the match STARTS on, or every report points at line 1.
    """
    kept: list[str] = []
    lines: list[int] = []
    line = 1
    for ch in text:
        if ch == "\n":
            line += 1
            continue
        if RE_INVISIBLE.match(ch) or RE_SEPARATOR.match(ch):
            continue
        # Folding can change length -- one character in, two out -- so the map
        # is extended per emitted character rather than per source character.
        folded = ch.casefold()
        kept.append(folded)
        lines.extend([line] * len(folded))
    return "".join(kept), lines


def read_scannable(path: Path) -> str | None:
    """The file as text, or None when it is not text this reader can make.

    UTF-16 IS THE ONE THAT GOT THROUGH. The old read fell back to a lossy decode
    only on `UnicodeDecodeError`, and UTF-16 does not raise one: decoded as
    UTF-8 it comes back as every letter separated by a replacement character, so
    the file counted toward the scanned total, matched nothing, and produced no
    `# not scanned:` line. A miss that reports itself is a limit; a miss that
    reports a clean count is the failure this module keeps being rewritten for.

    None means REPORT IT, and the caller puts it with the files it could not
    read. Latin-1 is last and cannot fail, so a subtitle file in an eight-bit
    encoding is still scanned rather than dropped.
    """
    data = path.read_bytes()          # OSError is the caller's to report
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # A NUL in the first pages is what wide text looks like without a mark.
    if b"\x00" in data[:4096]:
        for encoding in ("utf-16-le", "utf-16-be"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return None
    return data.decode("latin-1")


def _is_id_shaped(tok: str) -> bool:
    """Mixed case AND a digit. A real id almost always has both; an English
    word, a snake_case name and a CONSTANT almost never do."""
    return (any(c.isdigit() for c in tok)
            and any(c.isupper() for c in tok)
            and any(c.islower() for c in tok))


def scan_text(text: str, rel: str, refused: tuple[str, ...] = (),
              anchors: tuple[frozenset[str], ...] = ()) -> list[str]:
    """`refused` is the caller's own list of words that must not be published --
    a private repository's name, an employer, a client. It cannot be a constant
    here: writing the name down in this file would publish the very string the
    check exists to keep private, which is exactly what the first version of
    this scanner did.
    """
    out: list[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        for tok in RE_ID.findall(line):
            if _is_id_shaped(tok) and tok not in ALLOW:
                out.append(f"{rel}:{n} E-CORPUS-VIDEO-ID {tok} "
                           f"is video-id-shaped; move it out of the package")
        if RE_DATED_EXEMPTION.search(line):
            out.append(f"{rel}:{n} E-CORPUS-DATED-EXEMPTION dated exemption in "
                       f"source; it belongs in watch-quality.toml")
        stamps = stamps_in(line)
        if (len(stamps) >= 2 and not _round_pair(stamps)
                and any(stamps <= a for a in anchors)):
            # The stamps are NOT echoed, for the same reason the refused word
            # is not: the pair IS the leak, and a report quoting it publishes
            # the pair into whatever reads the report.
            out.append(f"{rel}:{n} E-CORPUS-ANCHOR-SET every timestamp on this "
                       f"line stands on one line of one corpus note; invent "
                       f"the anchors or drop them")
    # THE REFUSED WORDS, ONCE OVER THE WHOLE FILE. Line by line, a literal broken
    # across a line was two halves of nothing, and a literal whose space was
    # written as a hyphen or an underscore or not at all was a different string.
    # Case folding closed casing and closed none of those. Squashed, they are one
    # word again -- and so is anything else somebody puts between the letters.
    squashed, line_of = _squash(text)
    for word in refused:
        needle, _ = _squash(word)
        if not needle:
            # A hole in the list, not a rule. `"" in anything` is True, so one
            # empty entry would refuse every line of every file.
            continue
        at, reported = squashed.find(needle), set()
        while at >= 0:
            n = line_of[at]
            if n not in reported:
                reported.add(n)
                # The refused word is NOT echoed. A defect report that quotes it
                # ends up in a log, a CI page or a commit message, and the leak
                # happens there instead.
                out.append(f"{rel}:{n} E-CORPUS-REFUSED-WORD line contains a "
                           f"refused literal (see refused_literals in "
                           f"watch-quality.toml)")
            at = squashed.find(needle, at + 1)
    return out


def scan(paths: list[Path], root: Path,
         refused: tuple[str, ...] = (),
         anchors: tuple[frozenset[str], ...] = (),
         unread: list[Path] | None = None) -> list[str]:
    out: list[str] = []
    for p in sorted(paths):
        rel = str(p.relative_to(root) if p.is_relative_to(root) else p)
        try:
            text = read_scannable(p)
        except OSError as exc:
            # A NAMED target that is not there is LOUD. It is the misspelled
            # path, and reporting it as a file that could not be decoded would
            # turn an exit 1 into a line in the not-scanned census.
            out.append(f"{rel}:1 E-READ {exc}")
            continue
        if text is None:
            # Handed back rather than swallowed, and rather than reported as a
            # defect: this file was not read, which is a different sentence from
            # "this file is clean" and from "this file leaks".
            if unread is not None:
                unread.append(p)
            continue
        out.extend(scan_text(text, rel, refused, anchors))
    return out


def selftest() -> int:
    import tempfile
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

    def n_hits(text: str, refused: tuple[str, ...] = ()) -> int:
        return len(scan_text(text, "x.py", refused))

    # Every id below is INVENTED. This file is excluded from its own scan -- it
    # has to be, since it quotes the shapes it refuses -- so a real id written
    # here would be published by the very check meant to prevent that. The tests
    # are about shape, and a made-up id has the same shape as a real one.
    check("an id is caught", n_hits('vid = "Qm4Zt8Xv2Ly"'), 1)
    check("a dash id is caught", n_hits('"-Pk9Nb3Wc6R": {}'), 1)
    check("an id in a comment is caught too",
          n_hits("# measured on Hd5Jq7Vt1Nz"), 1)
    check("an allowed fixture passes", n_hits('"-Xk4Rm2Qp7Z"'), 0)
    # A PUBLISHED MODEL SLUG has the shape and none of the risk, and this is the
    # first false positive that could not be reworded away: the eleven
    # characters between the slash and the dot are the model's name.
    check("a model slug passes",
          n_hits('MODEL_2 = "Qwen/Qwen3-ASR-1.7B"'), 0)
    # Shape, not vocabulary: these are the near-misses that would make the gate
    # unusable if it fired on them.
    check("an all-lower word passes", n_hits("resolve_note"), 0)
    check("a CONSTANT passes", n_hits("MIN_ITEM_WORDS"), 0)
    check("ten chars is not an id", n_hits('"Qm4Zt8Xv2L"'), 0)
    check("twelve chars is not an id", n_hits('"Qm4Zt8Xv2Ly1"'), 0)
    check("no digit is not an id", n_hits('"QmwZtoXvyLy"'), 0)
    check("no uppercase is not an id", n_hits('"qm4zt8xv2ly"'), 0)
    check("a longer token is not scanned inside",
          n_hits("sha=9f2c1ab8e7d4c3b2a1908f7e6d5c4b3a2919e8d7"), 0)
    # INVENTED, like every id above, and for the same reason. The first version
    # of this line reused a real corpus row's date AND its verb, inside the one
    # file the scan can never read -- so "the whole fork scans clean" was
    # unfalsifiable about it. A fixture in the scanner's own source has to be
    # further from the truth than anything else, not closer.
    check("a dated exemption is caught",
          n_hits('# 1999-01-01 exempt: placeholder'), 1)
    check("a plain date passes", n_hits("# written 1999-01-01"), 0)
    # EITHER ORDER. The rule required the date first, so the way a person
    # actually writes the sentence walked straight through it.
    check("...and the reason may come first",
          n_hits('# frames reaped 1999-01-01'), 1)
    check("a reason with no date still passes", n_hits("# frames reaped"), 0)
    check("a clean line passes", n_hits("def scan(paths, root):"), 0)
    # The refused list is the caller's, so with none supplied nothing is refused
    # -- and the word itself never appears in the report it produces.
    check("a refused word is caught",
          n_hits("path = secretco/docs", ("secretco",)), 1)
    check("...and the report does not quote it",
          "secretco" in " ".join(scan_text("path = secretco/docs", "x.py",
                                           ("secretco",))), False)
    check("nothing is refused by default", n_hits("path = secretco/docs"), 0)
    # A refused literal is a NAME, and prose capitalises a name. Matching one
    # casing published the other.
    check("a refused word is caught whatever its case",
          n_hits("Secretco ships this", ("secretco",)), 1)
    check("...and the list's own casing does not matter either",
          n_hits("path = secretco/docs", ("SecretCo",)), 1)
    check("an empty entry refuses nothing",
          n_hits("anything at all", ("",)), 0)
    # The gap that let three leaks through: prose, packaging and fixtures are
    # published too, so the file list must reach past *.py.
    for suffix in (".md", ".toml", ".txt", ".json", ".tsv", ".vtt"):
        check(f"{suffix} is scanned", suffix in TEXT_SUFFIXES, True)
    check("a compiled artefact is not", ".pyc" in TEXT_SUFFIXES, False)

    # And the gap a release later: the right file types, in one directory. The
    # walk has to reach the checkout, or the suffix list above is decoration.
    root, limited = published_root(Path(__file__))
    check("this package is inside a checkout", limited, None)
    check("the tests directory is reached",
          (root / "tests").is_dir() and any(
              p.parts[-2] == "tests" for p in collect([root / "tests"])), True)
    check("the readme is reached",
          root / "README.md" in collect([root]), True)
    check("history is not walked",
          any(".git" in p.parts for p in collect([root])), False)
    check("a cache is not walked",
          any("__pycache__" in p.parts for p in collect([root])), False)
    check("the scanner excuses itself",
          any(p.resolve() == Path(__file__).resolve() for p in collect([root])),
          False)
    # What was dropped is handed back, as the DIRECTORY the caller can act on.
    dropped: list[Path] = []
    collect([root], dropped)
    check("a skipped directory is reported, not its files",
          all(p.is_dir() for p in dropped) and bool(dropped), True)
    check("and version control's own storage is not reported at all",
          any(VCS_DIRS.intersection(p.parts) for p in dropped), False)
    # The skip list names DIRECTORIES. Matching the whole path dropped a
    # published file that merely shares one of those names, and then reported
    # it as a directory nobody could go and look at.
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        (t / "build").write_text("a file, not a directory\n", encoding="utf-8")
        (t / "dist").mkdir()
        (t / "dist" / "out.md").write_text("generated\n", encoding="utf-8")
        (t / "notes.rst").write_text("a format nobody listed\n", encoding="utf-8")
        dropped2: list[Path] = []
        unread2: list[Path] = []
        got = collect([t], dropped2, unread2)
        check("a file named like a skipped directory is read",
              t / "build" in got, True)
        check("...and the directory beside it is still skipped",
              dropped2, [t / "dist"])
        # An allowlist silently drops every format nobody listed, so the file
        # is handed back rather than vanishing under a clean count.
        check("an unlisted suffix is reported, not swallowed",
              unread2, [t / "notes.rst"])

    proof.done()
    print(f"# selftest OK ({cases} cases)")
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--selftest":
        return selftest()
    require = REQUIRE_FLAG in argv
    argv = [a for a in argv if a != REQUIRE_FLAG]
    # The checkout is the default target, because the checkout is what gets
    # published. The package alone was the old default and it is the reason a
    # clean exit meant nothing: the files most likely to carry prose about a
    # private corpus -- README, changelog, tests -- all sit outside it.
    root, limited = published_root(Path(__file__))
    targets = [Path(a).resolve() for a in argv] if argv else [root]
    skipped: list[Path] = []
    unread: list[Path] = []
    files = collect(targets, skipped, unread)
    # Shown relative to the checkout when it is inside one, absolute when it is
    # not. The first version filtered on `is_relative_to(root)` and printed
    # NOTHING for a named target outside this package's own checkout -- so
    # `wq_corpus_scan.py some/other/tree` could scan zero files, drop a
    # publishable one, print `0 corpus reference(s)` and exit 0, which is the
    # silent clean report this module exists to refuse.
    for d in sorted(set(skipped)):
        shown = d.relative_to(root) if d.is_relative_to(root) else d
        print(f"# not scanned: {shown}/ is generated or vendored; a published "
              f"page in there is not read", file=sys.stderr)
    by_suffix = _by_suffix(unread)
    # The refused words are the CALLER's, read from their policy. With no policy
    # the video-id and dated-exemption rules still run; only the word list is
    # empty, and the run says so rather than implying a clean bill of health.
    from .wq_policy import ENV_VAR, PolicyError, load
    try:
        policy = load()
        refused = policy.refused_literals()
    except PolicyError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2
    # And the caller's CORPUS, for the class no word list can hold. Absent
    # policy this is empty and the summary line says so, which is the whole
    # difference between a run that compared and a run that never looked.
    try:
        anchors = anchor_sets(policy.root() / policy.notes_dir())
    except OSError:
        anchors = ()
    # A WORDLESS RUN IS NOT A CLEAN RUN, and only the caller knows which of the
    # two it asked for. Scanning is skipped rather than reported, because the
    # answer would be `0 corpus reference(s)` either way and printing it is how
    # this ends up quoted as proof.
    if require and not refused:
        print(f"{PROG}: {REQUIRE_FLAG} was asked for and the policy in force "
              f"lists no refused literal, so this run could not have found one. "
              f"The word list is private and does not travel with this package: "
              f"run from inside the corpus, or point ${ENV_VAR} at its policy "
              f"file.", file=sys.stderr)
        return 2
    # AND THE OTHER EMPTY SET. A run with words in force and no FILES is just as
    # incapable of a finding, and it is the likelier accident: a named target
    # that was moved or misspelled walks nothing and prints a clean summary
    # (round-5 refutation F7). Same flag, because a caller reading only the exit
    # code cannot tell these two apart either.
    if require and not files:
        print(f"{PROG}: {REQUIRE_FLAG} was asked for and this run walked no "
              f"file at all under {', '.join(str(t) for t in targets)}, so it "
              f"could not have found anything.", file=sys.stderr)
        return 2
    # AND THE THIRD EMPTY SET. No ANCHORS in force is the same incapacity as no
    # words and no files: the run prints `0 corpus reference(s)` and exits 0
    # while the class it exists to catch could not have been looked for. It is
    # as easy to reach as the other two -- a corpus root pointed elsewhere, a
    # notes directory not yet cloned, a sparse checkout -- and a push hook
    # reading the exit code cannot tell any of the three from a clean run.
    if require and not anchors:
        print(f"{PROG}: {REQUIRE_FLAG} was asked for and the corpus in force "
              f"holds no line with two or more timestamps, so this run could "
              f"not have found one reproduced. The corpus is private and does "
              f"not travel with this package: run from inside it, or point "
              f"${ENV_VAR} at its policy file.", file=sys.stderr)
        return 2
    # THE THIRD KIND OF NOT-READ, and it is found by reading. A file whose bytes
    # are not text this reader can make is added here by `scan`, so the census
    # below counts it -- counted as scanned it would read exactly like a clean
    # one, which is how a UTF-16 file carrying a refused word passed.
    undecodable: list[Path] = []
    hits = scan(files, root, refused, anchors, undecodable)
    for suffix, count in sorted(_by_suffix(undecodable).items()):
        print(f"# not scanned: {count} {suffix} file(s); the bytes are not text "
              f"this reader can make, so a leak in one is not read",
              file=sys.stderr)
    # And the other half of not-read, by SUFFIX rather than by directory.
    # Grouped, because a tree of images would otherwise bury the count -- but
    # named, because an allowlist silently drops every format nobody listed.
    for suffix, count in sorted(by_suffix.items()):
        print(f"# not scanned: {count} {suffix} file(s); that suffix is not on "
              f"the text list, so a leak in one is not read", file=sys.stderr)
    for h in hits:
        print(h)
    # Named after what was WALKED, not after where this file happens to live.
    # `root` was printed here whatever the target was, so a run over a
    # temporary tree announced `scanned under <this checkout>` -- and that
    # line is the artifact a reader pastes as proof of coverage.
    reach = f"; {limited}" if limited else ""
    walked = ", ".join(str(t) for t in targets)
    print(f"# {len(files)} file(s) scanned under {walked}, {len(refused)} "
          f"refused literal(s) and {len(anchors)} anchor set(s) in force, "
          f"{len(hits)} corpus reference(s){reach}", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
