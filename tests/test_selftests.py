"""One command runs both suites, and the harness scores what it watched.

`watchquality` carries a second, larger suite than pytest: every module's own
`--selftest`. An independent verification round found six checks pinned ONLY
there -- among them both lines wiring a check into `check_note` -- so deleting a
wiring left `pytest -q` fully green while the module selftest went red. Nothing
ran the selftests automatically (mechanism F18), and the number quoted as
evidence was pytest's.

This file removes the gap by running every module selftest from inside the
pytest run. It is deliberately a SUBPROCESS per module: that is the entry point
the reviews cite, it keeps each module's import-time policy binding intact, and
it keeps the selftest state out of the pytest process.

The module list is DISCOVERED, not typed, because a hardcoded list is the same
defect one level up: a new module with a selftest nobody added to the list is a
check nothing runs. `cli.py` is why the predicate is `def selftest` and not the
word: `python3 -m watchquality.cli --selftest` exits 0 while running no test at
all, because it ignores the flag.

WHAT THIS FILE READS, AND WHAT IT NO LONGER DOES. Four rounds graded a number
the module reported about itself -- a success string, then `selftest OK (N
cases)`, then a roster, then a refusal the module was asked to produce -- and
each was walked through by a mutant that arranged to say it. So nothing the
module prints is read here any more. A case is derived from the module's OWN
SOURCE by `cases()` below, `selftest_proof.Proof` records which of those lines
the interpreter executed, and the floor is checked against the intersection.
Both halves are computed outside the module. A selftest that compares nothing
has no cases to find, and one that skips them has no lines to show.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import watchquality
from watchquality import selftest_proof


PACKAGE = Path(watchquality.__file__).parent

# The nine the 2026-08-20 verification ran by hand. Discovery must find at
# least these; finding MORE is the point of discovering rather than typing.
VERIFIED_2026_08_20 = {"resolve_note", "wq_policy", "anchor_manifest",
                       "spoken_vote", "ocr_vote", "demote_note",
                       "note_coverage", "transcript_align", "note_windows"}

# Ratcheted, deliberately: an independent review deleted six freshly added
# `wq_corpus_scan` cases and the run stayed green, because the floor still held
# the count from before they existed. Slack in a floor is exactly as much
# regression protection as no floor, for exactly the newest cases.
#
# What the HARNESS watched each module run on 2026-08-20: 803 of its own cases
# across twelve modules. Not the count the modules print -- these are derived
# from each selftest's source and confirmed executed, so they move when the
# work moves and not when a number does. Held as a FLOOR, so adding cases needs
# no edit here and losing them cannot pass. A module absent from this table --
# a new one -- still has to run at least one case.
CASE_FLOOR = {"anchor_manifest": 256, "demote_note": 17, "frame_fixture": 5,
              "note_coverage": 41, "note_windows": 39, "ocr_vote": 38,
              "resolve_note": 180, "say_captions": 25, "spoken_vote": 51,
              "transcript_align": 55, "wq_corpus_scan": 40, "wq_policy": 56}
DEFAULT_FLOOR = 1
TOTAL_CASES_2026_08_20 = 803


def _selftest_modules() -> list[str]:
    out = []
    for path in sorted(PACKAGE.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        if "\ndef selftest(" in src and '"--selftest"' in src:
            out.append(path.stem)
    return out


MODULES = _selftest_modules()


def _selftest(module: str) -> ast.FunctionDef:
    src = (PACKAGE / f"{module}.py").read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == "selftest":
            return node
    raise AssertionError(f"{module}: no top-level selftest")


def _comparator(fn: ast.FunctionDef) -> str | None:
    """The name whose calls this module told the harness are its cases.

    It is the argument to `selftest_proof.begin`, so a module cannot keep its
    comparator private and still have the calls counted -- which is what makes
    deleting that argument cost it every case rather than one control.
    """
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("begin"):
            return ast.unparse(node.args[0]) if node.args else None
    return None


def _statements(fn: ast.FunctionDef) -> list[ast.stmt]:
    """Every statement of the selftest itself, nested definitions excluded.

    The comparator's own body is not a case: `if got != want: raise` runs once
    per case and would otherwise be counted as one.
    """
    out: list[ast.stmt] = []

    def walk(stmts) -> None:
        for stmt in stmts or ():
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            out.append(stmt)
            for field in ("body", "orelse", "finalbody"):
                walk(getattr(stmt, field, None))
            for handler in getattr(stmt, "handlers", ()) or ():
                walk(handler.body)

    walk(fn.body)
    return out


def _says_nothing(test: ast.expr) -> bool:
    """A constant, or an expression compared with itself. Neither is a case."""
    if isinstance(test, ast.Constant):
        return True
    return (isinstance(test, ast.Compare) and len(test.comparators) == 1
            and ast.unparse(test.left) == ast.unparse(test.comparators[0]))


def cases(module: str) -> list[tuple[int, int]]:
    """The line spans of every statement that TYPECHECKS as a case.

    Three shapes, and nothing else counts: an `assert` whose test says
    something, a call to the comparator whose two answers are written
    differently, and a `try` whose `else` raises -- the "this must have been
    refused" idiom, anchored on its body because that is the line that runs
    when the case passes.
    """
    fn = _selftest(module)
    comparator = _comparator(fn)
    found: list[tuple[int, int]] = []
    for stmt in _statements(fn):
        if isinstance(stmt, ast.Assert):
            if not _says_nothing(stmt.test):
                found.append((stmt.lineno, stmt.end_lineno))
            continue
        if (isinstance(stmt, ast.Try) and stmt.handlers and stmt.orelse
                and any(isinstance(x, ast.Raise) for x in stmt.orelse)):
            found.append((stmt.body[0].lineno, stmt.body[-1].end_lineno))
        if comparator is None:
            continue
        for node in ast.walk(stmt):
            if (isinstance(node, ast.Call)
                    and ast.unparse(node.func) == comparator
                    and len(node.args) >= 3
                    and ast.unparse(node.args[1]) != ast.unparse(node.args[2])):
                found.append((node.lineno, node.end_lineno))
    return found


def test_every_module_that_has_a_selftest_is_in_this_run():
    """An empty or shrunken parametrisation is a silent skip, not a pass.

    Three assertions, because `VERIFIED_2026_08_20` freezes nine names and the
    other three were protected by nothing: rewriting `say_captions`'s argv
    check to single quotes -- behaviour-identical Python -- dropped it from
    discovery and the run read `296 passed`, EXIT=0 (verification-2 hole 7b,
    re-confirmed live in verification-3 Claim 8). `CASE_FLOOR` already names
    all twelve, so the containment check closes it with data this file has.

    The sum is the guard on the guard. `CASE_FLOOR = {k: 1 for k in
    CASE_FLOOR}` -- one line, in this file -- left `297 passed`, EXIT=0
    (verification-3 V2): a floor table nothing asserts is a floor of nothing.
    """
    missing = VERIFIED_2026_08_20 - set(MODULES)
    assert not missing, missing
    unrun = set(CASE_FLOOR) - set(MODULES)
    assert not unrun, unrun
    assert sum(CASE_FLOOR.values()) == TOTAL_CASES_2026_08_20, CASE_FLOOR


@pytest.mark.parametrize("module", MODULES)
def test_the_harness_watched_the_cases_run(module: str, tmp_path: Path):
    """The evidence, and the only thing here that decides a selftest ran.

    What this replaces: reading `selftest OK (N cases)`. Injecting `print(
    "selftest OK (0 cases)"); return 0` at the top of `resolve_note`'s selftest
    kept the whole run at `296 passed`, EXIT=0 (verification-2 hole 7a); the
    floor that answered it then read a number the module computed about itself,
    and a checker rewritten to `if False: raise` still produced it
    (verification-3 S-B), as did a module that dropped the control's argument
    and compared nothing at all.

    So the number is taken away from the module entirely. `cases()` derives the
    cases from the module's own source and `Proof` reports which lines ran; a
    case only counts when both agree. A selftest that returns early, that
    stops calling the comparator it declared, that asserts a constant, or that
    compares an expression with itself, loses cases it cannot get back by
    printing anything.
    """
    evidence = tmp_path / "evidence.json"
    proc = subprocess.run(
        [sys.executable, "-m", f"watchquality.{module}", "--selftest"],
        cwd=tmp_path, capture_output=True, text=True,
        env={**os.environ, selftest_proof.EVIDENCE_ENV: str(evidence)})
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    # Exit 0 alone is what `cli.py` returns for a flag it never read, and a
    # selftest that never reached `done()` leaves nothing here to read.
    assert evidence.exists(), (f"{module}: ran to exit 0 without handing over "
                               f"any evidence", out)
    lines = set(json.loads(evidence.read_text(encoding="utf-8"))["lines"])
    ran = [span for span in cases(module)
           if lines & set(range(span[0], span[1] + 1))]
    floor = CASE_FLOOR.get(module, DEFAULT_FLOOR)
    assert len(ran) >= floor, (module, len(ran), floor, out)


@pytest.mark.parametrize("module", MODULES)
def test_the_module_selftest_refuses_a_wrong_answer(module: str, tmp_path: Path):
    """The other half: the cases ran, and the comparator still reacts.

    Counting cases cannot tell 49 assertions from 49 increments. A failure can:
    under `$WQ_SELFTEST_NEGATIVE_CONTROL` every selftest hands its own
    comparator an answer that is wrong, and only one that still compares, and
    still reacts, turns that into a non-zero exit. A disarmed one accepts it
    and exits 0, which is what this case reads.

    It fires from `done()`, at the END of the selftest rather than the top,
    which is what closes the door the fourth round left open: the control run
    and the ordinary run used to share no code past the first line, so a module
    that returned immediately after the control satisfied both.
    """
    proc = subprocess.run(
        [sys.executable, "-m", f"watchquality.{module}", "--selftest"],
        cwd=tmp_path, capture_output=True, text=True,
        env={**os.environ, selftest_proof.NEGATIVE_CONTROL_ENV: "1"})
    assert proc.returncode != 0, (
        f"{module}: its checker accepted a wrong answer", proc.stdout + proc.stderr)
    assert selftest_proof.MARKER in (proc.stdout + proc.stderr), (
        f"{module}: exited non-zero for some reason other than the control",
        proc.stdout + proc.stderr)
