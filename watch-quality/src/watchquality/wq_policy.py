#!/usr/bin/env python3
"""Corpus policy for the watch-quality gates.

The gates are being extracted into a package that will live in a PUBLIC repo
(`docs/watch-quality-package-plan.md`). Engine code may therefore not name a
video id, a corpus path, or a dated exemption -- those are facts about one
private corpus, not about the checks. They live in `watch-quality.toml`
alongside the notes being graded, and this module is the only thing that reads
it.

Resolution order for the file, first hit wins:

1. `$WATCH_QUALITY_POLICY` -- an explicit path. Naming a file that does not
   exist is an error, never a silent fallback.
2. `watch-quality.toml` in the current directory or any parent.
3. `watch-quality.toml` in this file's directory or any parent, so a gate run
   from elsewhere still finds the corpus it ships with.
4. No file: the neutral defaults below, with EMPTY exemption lists. A missing
   policy can never invent an exemption.

Every exemption value must start with an ISO date. The dated-exemption rule was
prose that nothing enforced; here it is an exit code.

Usage:
    scripts/wq_policy.py             # print the resolved policy and its source
    scripts/wq_policy.py --selftest

Exit: 0 clean, 1 invalid policy, 2 usage or IO error.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path

PROG = "wq_policy.py"
ENV_VAR = "WATCH_QUALITY_POLICY"
ENV_ROOT = "WATCH_QUALITY_ROOT"
FILENAME = "watch-quality.toml"

# Neutral, corpus-free. These are what the package does with no policy present.
DEFAULTS: dict[str, object] = {
    "notes_dir": "notes",
    "caption_subdir": ".captions",
    "resolved_subdir": ".resolved",
    "ocr_subdir": ".ocr",
    "anchors_subdir": "anchors",
    "reviews_subdir": "reviews",
    "runs_root": "~/.watch-quality/runs",
    "search_roots": ["~/.watch-quality/runs"],
    # Words that must never appear in package source: a private repository's
    # name, an employer, a client. Empty by default, because the package cannot
    # know them -- and naming one in package source would publish the very
    # string the check exists to keep private.
    "refused_literals": [],
}
TABLES = ("unresolvable_runs", "unattributed_notes")
# Same promise as TABLES -- dated, reasoned, may only shrink -- but keyed twice,
# by video id and then by lane, because a review can be lost for one lane of a
# note and present for another.
NESTED_TABLES = ("lost_reviews",)
RE_DATED = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\S")


class PolicyError(Exception):
    """The policy file exists and is wrong. Never fall back on this."""


def find_policy(start: Path | None = None) -> Path | None:
    env = os.environ.get(ENV_VAR)
    if env:
        path = Path(env).expanduser()
        if not path.is_file():
            raise PolicyError(f"{ENV_VAR} names no file: {path}")
        return path
    seen: list[Path] = []
    for base in (Path(start) if start else Path.cwd(), Path(__file__).resolve().parent):
        base = base.resolve()
        for directory in (base, *base.parents):
            if directory in seen:
                continue
            seen.append(directory)
            candidate = directory / FILENAME
            if candidate.is_file():
                return candidate
    return None


def _check_dated(table: str, key: str, reason: object) -> None:
    if not isinstance(reason, str) or not RE_DATED.match(reason):
        raise PolicyError(
            f"[{table}] {key}: exemption must start with an ISO date "
            f"and a reason, got {reason!r}")


def _validate(raw: dict) -> None:
    unknown = set(raw) - set(DEFAULTS) - set(TABLES) - set(NESTED_TABLES)
    if unknown:
        raise PolicyError(f"unknown key(s): {', '.join(sorted(unknown))}")
    for table in TABLES:
        entries = raw.get(table, {})
        if not isinstance(entries, dict):
            raise PolicyError(f"[{table}] must be a table")
        for key, reason in entries.items():
            _check_dated(table, key, reason)
    for table in NESTED_TABLES:
        entries = raw.get(table, {})
        if not isinstance(entries, dict):
            raise PolicyError(f"[{table}] must be a table")
        for key, lanes in entries.items():
            if not isinstance(lanes, dict):
                raise PolicyError(f"[{table}.{key}] must be a table of lanes")
            for lane, reason in lanes.items():
                _check_dated(f"{table}.{key}", lane, reason)


def resolve_root(explicit: Path | str | None = None,
                 fallback: Path | None = None,
                 source: Path | None = None) -> Path:
    """The corpus root, in decreasing order of authority.

    `--root`, then `$WATCH_QUALITY_ROOT`, then the directory holding the policy
    file, then `fallback`. The fallback is what every gate used to do alone --
    derive the root from where the SCRIPT lives -- which silently assumes the
    corpus sits beside the gate. That assumption is false the moment the gates
    are installed as a package, and it failed exactly that way when the seam was
    first tested (docs/watch-quality-package-plan.md 3b).
    """
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get(ENV_ROOT)
    if env:
        return Path(env).expanduser().resolve()
    if source is not None:
        return Path(source).expanduser().resolve().parent
    if fallback is not None:
        return Path(fallback).resolve()
    return Path.cwd()


class Policy:
    def __init__(self, raw: dict, source: Path | None):
        _validate(raw)
        self.source = source
        self._raw = {**DEFAULTS, **raw}

    def _str(self, key: str) -> str:
        return str(self._raw[key])

    def notes_dir(self) -> str:
        return self._str("notes_dir")

    def caption_dir(self) -> str:
        return f"{self.notes_dir()}/{self._str('caption_subdir')}"

    def resolved_dir(self) -> str:
        return f"{self.notes_dir()}/{self._str('resolved_subdir')}"

    def ocr_subdir(self) -> str:
        return self._str("ocr_subdir")

    def anchors_dir(self) -> str:
        return f"{self.notes_dir()}/{self._str('anchors_subdir')}"

    def reviews_dir(self) -> str:
        return f"{self.notes_dir()}/{self._str('reviews_subdir')}"

    def runs_root(self) -> Path:
        return Path(self._str("runs_root")).expanduser()

    def search_roots(self) -> tuple[Path, ...]:
        return tuple(Path(str(p)).expanduser() for p in self._raw["search_roots"])

    def refused_literals(self) -> tuple[str, ...]:
        return tuple(str(w) for w in self._raw["refused_literals"] if str(w))

    def note_ref_re(self) -> re.Pattern[str]:
        return re.compile(r"%s/(\S+\.md)" % re.escape(self.notes_dir()))

    def unresolvable_runs(self) -> dict[str, str]:
        return dict(self._raw.get("unresolvable_runs", {}))

    def unattributed_notes(self) -> dict[str, str]:
        return dict(self._raw.get("unattributed_notes", {}))

    def lost_reviews(self) -> dict[str, dict[str, str]]:
        return {vid: dict(lanes)
                for vid, lanes in self._raw.get("lost_reviews", {}).items()}

    def root(self, explicit: Path | str | None = None,
             fallback: Path | None = None) -> Path:
        return resolve_root(explicit, fallback, self.source)


_CACHED: Policy | None = None


def load(start: Path | None = None, refresh: bool = False) -> Policy:
    global _CACHED
    if _CACHED is not None and not refresh:
        return _CACHED
    source = find_policy(start)
    raw = tomllib.loads(source.read_text(encoding="utf-8")) if source else {}
    _CACHED = Policy(raw, source)
    return _CACHED


def selftest() -> int:
    import tempfile
    cases = 0

    def check(label: str, got, want) -> None:
        nonlocal cases
        cases += 1
        if got != want:
            raise AssertionError(f"{label}: got {got!r}, want {want!r}")

    # No file anywhere: neutral defaults, and both exemption lists EMPTY.
    with tempfile.TemporaryDirectory() as tmp:
        os.environ.pop(ENV_VAR, None)
        empty = Policy({}, None)
        check("default notes_dir", empty.notes_dir(), "notes")
        check("default caption_dir", empty.caption_dir(), "notes/.captions")
        check("default ocr_subdir", empty.ocr_subdir(), ".ocr")
        check("default anchors_dir", empty.anchors_dir(), "notes/anchors")
        check("default reviews_dir", empty.reviews_dir(), "notes/reviews")
        check("default resolved_dir", empty.resolved_dir(), "notes/.resolved")
        check("no exemptions", empty.unresolvable_runs(), {})
        check("no unattributed", empty.unattributed_notes(), {})
        check("no lost reviews", empty.lost_reviews(), {})
        check("nothing refused by default", empty.refused_literals(), ())
        check("no source", empty.source, None)
        # The walk-up, tested against a tree this test builds. It used to assert
        # that SOME policy is always found, which only held because the module
        # sat beside a corpus that had one -- so the package alone failed a test
        # about the package. What the walk-up actually promises is: a policy in
        # an ancestor directory is found from below.
        # It is asserted against `start`, not against ambient state: the search
        # also walks up from this module's own directory, and in a checkout that
        # ships a corpus that second chain finds one no matter what the first
        # does. Only the first chain is this test's business.
        nest = Path(tmp) / "a" / "b" / "c"
        nest.mkdir(parents=True)
        (Path(tmp) / "a" / FILENAME).write_text('notes_dir = "n"\n',
                                                encoding="utf-8")
        check("found from a descendant", find_policy(nest),
              Path(tmp).resolve() / "a" / FILENAME)
        check("and the nearest one wins", find_policy(nest).parent.name, "a")

    # A real policy round-trips, including the derived paths.
    raw = {"notes_dir": "corpus", "caption_subdir": ".caps", "ocr_subdir": ".pix",
           "runs_root": "/tmp/runs", "search_roots": ["/tmp/runs", "/tmp"],
           "unresolvable_runs": {"abc": "2026-08-05 fixture, not a real run"},
           "lost_reviews": {"abc": {"facts": "2026-08-05 fixture, not a real lane"}}}
    pol = Policy(raw, Path("/dev/null"))
    check("notes_dir", pol.notes_dir(), "corpus")
    check("caption_dir", pol.caption_dir(), "corpus/.caps")
    check("anchors_dir follows notes_dir", pol.anchors_dir(), "corpus/anchors")
    check("reviews_dir follows notes_dir", pol.reviews_dir(), "corpus/reviews")
    check("ocr_subdir", pol.ocr_subdir(), ".pix")
    check("runs_root", pol.runs_root(), Path("/tmp/runs"))
    check("search_roots", pol.search_roots(), (Path("/tmp/runs"), Path("/tmp")))
    check("exemption", pol.unresolvable_runs(), {"abc": "2026-08-05 fixture, not a real run"})
    check("refused literals, blanks dropped",
          Policy({"refused_literals": ["acme", "", "beta"]}, None
                 ).refused_literals(), ("acme", "beta"))
    check("lost review", pol.lost_reviews(),
          {"abc": {"facts": "2026-08-05 fixture, not a real lane"}})
    check("lost review copy is not the policy's own dict",
          (pol.lost_reviews()["abc"].pop("facts"), pol.lost_reviews())[1],
          {"abc": {"facts": "2026-08-05 fixture, not a real lane"}})
    check("note ref matches", bool(pol.note_ref_re().search("see corpus/x.md")), True)
    check("note ref is scoped", bool(pol.note_ref_re().search("see notes/x.md")), False)

    # Root resolution, in order. The script-relative fallback is LAST because it
    # is the assumption that broke when the seam was first tested.
    os.environ.pop(ENV_ROOT, None)
    check("policy dir beats fallback",
          Policy({}, Path("/opt/corpus/watch-quality.toml")).root(fallback=Path("/opt/pkg")),
          Path("/opt/corpus"))
    check("fallback used with no policy file",
          Policy({}, None).root(fallback=Path("/opt/pkg")), Path("/opt/pkg"))
    check("explicit beats everything",
          Policy({}, Path("/opt/corpus/watch-quality.toml")).root("/srv/notes",
                                                                 Path("/opt/pkg")),
          Path("/srv/notes"))
    os.environ[ENV_ROOT] = "/srv/env-notes"
    check("env beats policy dir",
          Policy({}, Path("/opt/corpus/watch-quality.toml")).root(fallback=Path("/opt/pkg")),
          Path("/srv/env-notes"))
    check("explicit still beats env",
          Policy({}, None).root("/srv/flag", Path("/opt/pkg")), Path("/srv/flag"))
    os.environ.pop(ENV_ROOT, None)

    # An undated exemption is a defect, not a warning.
    for bad in ("no date here", "2026-08-05", "05-08-2026 wrong order", 7):
        try:
            Policy({"unresolvable_runs": {"abc": bad}}, None)
        except PolicyError:
            cases += 1
        else:
            raise AssertionError(f"undated exemption accepted: {bad!r}")

    # The nested table gets the same date rule, one level down, and a lane table
    # that is a bare string is a shape error rather than a silently ignored row.
    for bad in ({"vid": {"facts": "report reaped"}},
                {"vid": "2026-08-05 fixture, not a real lane"}):
        try:
            Policy({"lost_reviews": bad}, None)
        except PolicyError:
            cases += 1
        else:
            raise AssertionError(f"bad lost_reviews accepted: {bad!r}")

    # An unknown key is a typo that would otherwise be silently ignored.
    try:
        Policy({"notes_directory": "notes"}, None)
    except PolicyError:
        cases += 1
    else:
        raise AssertionError("unknown key accepted")

    # A named-but-missing policy file never falls back.
    os.environ[ENV_VAR] = "/nonexistent/watch-quality.toml"
    try:
        find_policy()
    except PolicyError:
        cases += 1
    else:
        raise AssertionError("missing $%s file accepted" % ENV_VAR)
    finally:
        os.environ.pop(ENV_VAR, None)

    print(f"# selftest OK ({cases} cases)")
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--selftest":
        return selftest()
    if argv:
        print(f"{PROG}: unknown argument {argv[0]}", file=sys.stderr)
        return 2
    try:
        pol = load(refresh=True)
    except PolicyError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1
    print(f"# policy source: {pol.source or '(none -- neutral defaults)'}")
    print(f"# notes_dir     {pol.notes_dir()}")
    print(f"# caption_dir   {pol.caption_dir()}")
    print(f"# resolved_dir  {pol.resolved_dir()}")
    print(f"# ocr_subdir    {pol.ocr_subdir()}")
    print(f"# anchors_dir   {pol.anchors_dir()}")
    print(f"# reviews_dir   {pol.reviews_dir()}")
    print(f"# runs_root     {pol.runs_root()}")
    print(f"# search_roots  {', '.join(str(p) for p in pol.search_roots())}")
    for table, entries in (("unresolvable_runs", pol.unresolvable_runs()),
                           ("unattributed_notes", pol.unattributed_notes())):
        print(f"# {table}: {len(entries)} entry(ies)")
        for key, reason in entries.items():
            print(f"#   {key}\t{reason}")
    lost = pol.lost_reviews()
    print(f"# lost_reviews: {sum(len(v) for v in lost.values())} lane(s) "
          f"across {len(lost)} run(s)")
    for key, lanes in lost.items():
        for lane, reason in lanes.items():
            print(f"#   {key}\t{lane}\t{reason}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except PolicyError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        sys.exit(1)
