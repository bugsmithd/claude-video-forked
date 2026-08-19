#!/usr/bin/env python3
"""One command that runs every gate over a note and returns one exit code.

The gates were built one slice at a time and each got its own console script,
which is right for developing them and wrong for using them. Nobody remembers
five invocations and their flags, so in practice the gates were run when
somebody remembered, which is the same as not being a gate.

`watch-audit <note.md>` is the whole block. Exit 0 means every gate passed;
exit 1 means at least one found a defect and its output is above; exit 2 means
a gate could not run at all, which is not the same thing and must not read like
a pass.

    watch-audit notes/some-note.md
    watch-audit notes/*.md --quiet      # one line per gate, defects only
    watch-audit --list                  # what would run, in order
"""
from __future__ import annotations

import argparse
import io
import sys
from collections.abc import Callable
from contextlib import redirect_stdout

# ORDER IS THE POINT, not an accident of the import list. Structure first: a
# note whose anchors do not resolve will produce nonsense from every checker
# after it, and reading a witness failure caused by a broken anchor wastes the
# reader's attention on a symptom.
GATES: list[tuple[str, list[str]]] = [
    ("resolve_note", ["--check"]),
    ("anchor_manifest", ["--check"]),
    ("spoken_vote", ["--check"]),
    ("ocr_vote", ["--check"]),
    ("demote_note", ["--diff"]),
]


def _load(name: str) -> Callable[[list[str]], int]:
    module = __import__(f"watchquality.{name}", fromlist=["main"])
    return module.main


def run_gate(name: str, flags: list[str], notes: list[str]) -> tuple[int, str]:
    """(exit code, captured stdout). A gate that raises is a 2, never a 0."""
    try:
        main = _load(name)
    except Exception as exc:  # noqa: BLE001 -- the message IS the handling
        return 2, f"# {name}: could not be loaded ({exc})\n"
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            code = main([*flags, *notes])
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
    except Exception as exc:  # noqa: BLE001
        return 2, buf.getvalue() + f"# {name}: raised {type(exc).__name__}: {exc}\n"
    return int(code or 0), buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="watch-audit",
        description="Run every note gate in order and return one exit code.")
    ap.add_argument("notes", nargs="*", help="note paths; default is the corpus")
    ap.add_argument("--quiet", action="store_true",
                    help="one line per gate; print a gate's output only when "
                         "it found something")
    ap.add_argument("--list", action="store_true",
                    help="print the gates in the order they would run")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if args.list:
        for i, (name, flags) in enumerate(GATES, start=1):
            print(f"{i}. {name} {' '.join(flags)}")
        return 0

    worst = 0
    failed: list[str] = []
    for name, flags in GATES:
        code, out = run_gate(name, flags, args.notes)
        worst = max(worst, code)
        if code:
            failed.append(name)
        if args.quiet:
            verdict = "ok" if code == 0 else ("DEFECTS" if code == 1 else "COULD NOT RUN")
            print(f"{name}: {verdict}")
            if code:
                print(out, end="")
        else:
            print(f"### {name} {' '.join(flags)}")
            print(out, end="" if out.endswith("\n") else "\n")
            print(f"EXIT={code}")
            print()

    if worst == 0:
        print(f"# all {len(GATES)} gate(s) passed")
    elif worst == 1:
        print(f"# {len(failed)} gate(s) found defects: {', '.join(failed)}")
    else:
        # A GATE THAT COULD NOT RUN IS NOT A GATE THAT PASSED, and collapsing
        # the two is how a broken install reads as a clean corpus.
        print(f"# {len(failed)} gate(s) did not complete: {', '.join(failed)}")
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
