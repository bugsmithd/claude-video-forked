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

Usage:
    scripts/wq_corpus_scan.py            # scan the package's own source
    scripts/wq_corpus_scan.py <path>...  # scan what you name, files or trees
    scripts/wq_corpus_scan.py --selftest

Exit: 0 clean, 1 something corpus-shaped is in the source, 2 usage error.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROG = "wq_corpus_scan.py"

# An id is 11 chars of [A-Za-z0-9_-]. Bounded on both sides so a longer token
# (a sha, a base64 blob) does not match a window inside itself.
RE_ID = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{11}(?![A-Za-z0-9_-])")
RE_DATED_EXEMPTION = re.compile(
    r"\d{4}-\d{2}-\d{2}.{0,80}?\b(exempt|exemption|reaped|waiv)", re.IGNORECASE)

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
TEXT_SUFFIXES = frozenset({
    ".py", ".md", ".toml", ".txt", ".tsv", ".csv", ".json",
    ".cfg", ".ini", ".yaml", ".yml", ".sh", ".vtt", ".srt",
})


def _is_id_shaped(tok: str) -> bool:
    """Mixed case AND a digit. A real id almost always has both; an English
    word, a snake_case name and a CONSTANT almost never do."""
    return (any(c.isdigit() for c in tok)
            and any(c.isupper() for c in tok)
            and any(c.islower() for c in tok))


def scan_text(text: str, rel: str, refused: tuple[str, ...] = ()) -> list[str]:
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
        for word in refused:
            if word and word in line:
                # The refused word is NOT echoed. A defect report that quotes it
                # ends up in a log, a CI page or a commit message, and the leak
                # happens there instead.
                out.append(f"{rel}:{n} E-CORPUS-REFUSED-WORD line contains a "
                           f"refused literal (see refused_literals in "
                           f"watch-quality.toml)")
                break
    return out


def scan(paths: list[Path], root: Path,
         refused: tuple[str, ...] = ()) -> list[str]:
    out: list[str] = []
    for p in sorted(paths):
        rel = str(p.relative_to(root) if p.is_relative_to(root) else p)
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            out.append(f"{rel}:1 E-READ {exc}")
            continue
        out.extend(scan_text(text, rel, refused))
    return out


def selftest() -> int:
    cases = 0

    def check(label: str, got, want) -> None:
        nonlocal cases
        cases += 1
        if got != want:
            raise AssertionError(f"{label}: got {got!r}, want {want!r}")

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
    check("a dated exemption is caught",
          n_hits('# 2026-08-05 exempt: frames reaped'), 1)
    check("a plain date passes", n_hits("# written 2026-08-05"), 0)
    check("a clean line passes", n_hits("def scan(paths, root):"), 0)
    # The refused list is the caller's, so with none supplied nothing is refused
    # -- and the word itself never appears in the report it produces.
    check("a refused word is caught",
          n_hits("path = secretco/docs", ("secretco",)), 1)
    check("...and the report does not quote it",
          "secretco" in " ".join(scan_text("path = secretco/docs", "x.py",
                                           ("secretco",))), False)
    check("nothing is refused by default", n_hits("path = secretco/docs"), 0)
    check("an empty entry refuses nothing",
          n_hits("anything at all", ("",)), 0)
    # The gap that let three leaks through: prose, packaging and fixtures are
    # published too, so the file list must reach past *.py.
    for suffix in (".md", ".toml", ".txt", ".json", ".tsv", ".vtt"):
        check(f"{suffix} is scanned", suffix in TEXT_SUFFIXES, True)
    check("a compiled artefact is not", ".pyc" in TEXT_SUFFIXES, False)

    print(f"# selftest OK ({cases} cases)")
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--selftest":
        return selftest()
    # The package's own directory is the default target, because the package is
    # what gets published. Scanning the caller's repo instead would pass on the
    # day the gates moved and no longer sat where the scanner looked.
    pkg = Path(__file__).resolve().parent
    root = pkg.parent
    targets = [Path(a).resolve() for a in argv] if argv else [pkg]
    files: list[Path] = []
    for t in targets:
        if t.is_dir():
            files.extend(sorted(
                p for p in t.rglob("*")
                if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES
                # A repository's own history is not published prose, and it is
                # large enough to turn this gate into a minute-long wait.
                and ".git" not in p.parts))
        else:
            files.append(t)
    # This scanner quotes every shape it refuses, so it would refuse itself.
    files = [f for f in files if f.name != Path(__file__).name]
    # The refused words are the CALLER's, read from their policy. With no policy
    # the video-id and dated-exemption rules still run; only the word list is
    # empty, and the run says so rather than implying a clean bill of health.
    from .wq_policy import PolicyError, load
    try:
        refused = load().refused_literals()
    except PolicyError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2
    hits = scan(files, root, refused)
    for h in hits:
        print(h)
    print(f"# {len(files)} file(s) scanned, {len(refused)} refused literal(s) "
          f"in force, {len(hits)} corpus reference(s)", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
