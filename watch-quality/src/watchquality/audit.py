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
import os
import sys
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

# Absolute, not relative: `tests/test_audit.py` loads this file standalone by
# path so it tests the source tree rather than the installed wheel, and a
# relative import has no package to be relative to there. `_load` below already
# reaches for its gates the same way.
from watchquality import wq_policy

# ORDER IS THE POINT, not an accident of the import list. Structure first: a
# note whose anchors do not resolve will produce nonsense from every checker
# after it, and reading a witness failure caused by a broken anchor wastes the
# reader's attention on a symptom.
GATES: list[tuple[str, list[str]]] = [
    ("resolve_note", ["--check"]),
    ("anchor_manifest", ["--check"]),
    ("spoken_vote", ["--check"]),
    ("ocr_vote", ["--check"]),
    # Last of the checkers, because it reads a note's declared rendering and
    # that declaration is what `resolve_note` checks. Three per-note checks ran
    # in no runner at all until this one existed: the shape of the arguments
    # they take -- a note AND a transcript -- had nowhere to come from until
    # `oracle:` became a field a gate could open.
    ("note_gates", ["--check"]),
    ("demote_note", ["--diff"]),
]


def resolved_policy() -> tuple[Path | None, tuple[str, ...] | None]:
    """(the policy file this process resolved, the lanes it requires).

    Re-resolved rather than read off the cached import, because the whole point
    is to report what THIS invocation, from THIS directory, is about to
    enforce.
    """
    pol = wq_policy.load(refresh=True)
    return pol.source, pol.required_lanes()


def _load(name: str) -> Callable[[list[str]], int]:
    module = __import__(f"watchquality.{name}", fromlist=["main"])
    return module.main


def run_gate(name: str, flags: list[str], notes: list[str]) -> tuple[int, str, str]:
    """(exit code, captured stdout, captured stderr). A gate that raises is a 2.

    STDERR IS CAPTURED TOO, and it is not decoration. `note_gates` prints its
    census -- "4 of 25 note(s) graded against their own rendering" -- and one
    line per note it skipped to stderr, on the argument that a skip is not a
    pass and the reader has to see which it was. This function captured stdout
    only, so that gate's whole section of the report was the word `EXIT=0`, sat
    under `# all 6 gate(s) passed`, over a corpus where 21 of 25 notes were
    never opened. The gate was right, the audit dropped it, and the audit is
    the artifact people read and paste.
    """
    try:
        main = _load(name)
    except Exception as exc:  # noqa: BLE001 -- the message IS the handling
        return 2, f"# {name}: could not be loaded ({exc})\n", ""
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = main([*flags, *notes])
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
    except Exception as exc:  # noqa: BLE001
        return (2, out.getvalue() + f"# {name}: raised {type(exc).__name__}: {exc}\n",
                err.getvalue())
    return int(code or 0), out.getvalue(), err.getvalue()


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

    # SAY WHAT IS BEING ENFORCED, BEFORE ENFORCING IT. `required_lanes` is
    # resolved by walking up from the working directory, so the same note under
    # the same command was red inside the corpus and green from anywhere else --
    # and a full five-gate run never named the policy it used, so an armed run
    # and a disarmed run were byte-identical apart from the defect lines that
    # did not appear (mechanism F3-F7, and F4 which is why the others are
    # invisible).
    try:
        source, lanes = resolved_policy()
    except wq_policy.PolicyError as exc:
        print(f"# policy: UNUSABLE -- {exc}")
        return 2
    if source is None and not os.environ.get(wq_policy.ENV_NO_POLICY):
        # GRADING A CORPUS THIS PROCESS NEVER FOUND IS NOT A PASS. Without
        # this, `cd /tmp && watch-audit path/to/notes` required no lanes and
        # printed `# all 5 gate(s) passed`.
        print(f"# policy: none -- no watch-quality.toml above {Path.cwd()} or "
              f"above the installed package, so every corpus rule this audit "
              f"exists to enforce is empty. Point ${wq_policy.ENV_VAR} at the "
              f"corpus policy, run from inside the corpus, or ask for the "
              f"neutral-defaults run by name with {wq_policy.ENV_NO_POLICY}=1.")
        print("# 0 gate(s) run: the audit could not run")
        return 2
    where = source if source is not None else f"none ({wq_policy.ENV_NO_POLICY})"
    print(f"# policy: {where}")
    print(f"# required_lanes: {', '.join(lanes or ()) or '(none)'}")
    # The other lever that moves what gets graded without moving the output.
    # `$WATCH_QUALITY_ROOT` points the gates at a DIFFERENT corpus while the
    # policy still comes from here, and the census line then reads "N notes
    # checked" about notes nobody asked for (mechanism F7).
    if os.environ.get(wq_policy.ENV_ROOT):
        print(f"# corpus root: ${wq_policy.ENV_ROOT}="
              f"{os.environ[wq_policy.ENV_ROOT]}")

    worst = 0
    # Kept APART, because they mean different things and the summary has to name
    # each gate by what it actually did. Collapsed into one list, a run where
    # one gate found defects and another could not start printed "2 gate(s) did
    # not complete" naming the gate that completed perfectly well -- sending a
    # reader after a broken install that is not there, and burying the defects
    # that are.
    defective: list[str] = []
    broken: list[str] = []
    for name, flags in GATES:
        code, out, err = run_gate(name, flags, args.notes)
        # A NEGATIVE CODE IS A GATE THAT DIED, not a gate that passed. It went
        # on `broken` already, but `max(worst, -9)` left `worst` at 0, so the
        # run printed "all N gate(s) passed", exited 0, and never printed the
        # "did not complete" line it had just built -- the exact sentence this
        # split exists to enforce, through the one door the split did not close.
        worst = max(worst, code if code > 0 else 2 if code else 0)
        if code == 1:
            defective.append(name)
        elif code:
            broken.append(name)
        if args.quiet:
            verdict = "ok" if code == 0 else ("DEFECTS" if code == 1 else "COULD NOT RUN")
            print(f"{name}: {verdict}")
            if code:
                print(out, end="")
                print(err, end="")
            else:
                # QUIET DROPS THE DETAIL, NOT THE CENSUS. Printing a gate's
                # output only when it found something is the right rule for
                # defect lines and the wrong one for a gate reporting how much
                # of the corpus it managed to grade at all. Quiet was the worse
                # of the two readings: `note_gates: ok`, and nothing else, over
                # 21 notes nobody opened.
                for line in (out + err).splitlines():
                    if line.startswith("# "):
                        print(line)
        else:
            print(f"### {name} {' '.join(flags)}")
            print(out, end="" if out.endswith("\n") else "\n")
            if err:
                print(err, end="" if err.endswith("\n") else "\n")
            print(f"EXIT={code}")
            print()

    if worst == 0:
        print(f"# all {len(GATES)} gate(s) passed")
    else:
        # A GATE THAT COULD NOT RUN IS NOT A GATE THAT PASSED, and collapsing
        # the two is how a broken install reads as a clean corpus. Both lines
        # print when both happened, so neither hides inside the other.
        if defective:
            print(f"# {len(defective)} gate(s) found defects: "
                  f"{', '.join(defective)}")
        if broken:
            print(f"# {len(broken)} gate(s) did not complete: "
                  f"{', '.join(broken)}")
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
