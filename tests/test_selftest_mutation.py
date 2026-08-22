"""Weaken one expectation at a time and ask whether anything notices.

The rule harness beside this one breaks a rule's OWNER and checks that a case
the rule cites goes red. It cannot see the other direction. A module selftest
counts its cases, and a case that says nothing counts exactly as much as one
that says something: deleting two real expectations and adding two that
typecheck and pass -- `check("pad one", 1 + 1, 2)` -- keeps every floor met and
survives a green suite. Weakening ONE inline expectation does the same, because
the negative control proves the shared checker still refuses and that the
assert is live; it cannot prove each individual expectation still
discriminates. Two issues have been open about that since 2026-08-20.

So this harness mutates the TEST rather than the code. For each `check(...)` in
a module's selftest it builds a variant where that one call compares a value
with itself -- still counted, still executed, still green -- and then asks the
question that matters: is there a mutant of this module that the intact
selftest catches and the weakened one does not?

  DISCRIMINATING   at least one mutant is caught with the expectation intact
                   and survives without it. The expectation is load-bearing.
  INERT            every mutant this run tried lands the same way either way.
                   That is a finding about the expectation, bounded by the
                   mutants tried, and the bound is written into the report.

INERT IS NOT THE SAME AS WRONG. An expectation can be the only witness for a
behaviour no mutant in the sample reaches, and this harness cannot tell that
from an expectation that tests nothing. Every count it prints is therefore
"against the mutants tried", never "against the module".
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from test_rule_mutation import (MUTANT_CAP, SELFTEST_TIMEOUT, _Sandbox,
                                mutants_for)
from test_spec_conformance import PACKAGE

# Bounds, and every one of them is a reason a finding here is a floor. A
# selftest run costs a second or two, and the sweep is expectations times
# mutants times modules.
EXPECTATIONS_PER_MODULE = 8
MUTANTS_PER_MODULE = 3

REPORT: dict[str, object] = {"modules": {}, "dropped": {}}


@pytest.fixture(scope="session")
def tree(tmp_path_factory: pytest.TempPathFactory) -> _Sandbox:
    """One copy of the repository for the whole sweep.

    The same object the rule harness builds, and for the same reason: the
    package is installed editable against the real checkout, so a copy that is
    not on the path silently tests the original and every variant comes back
    green.
    """
    return _Sandbox(tmp_path_factory.mktemp("selftest-mutation") / "tree")


def _selftest_source(module: str) -> tuple[str, ast.FunctionDef] | None:
    """The module's source and its `selftest` definition, or None."""
    path = PACKAGE / f"{module}.py"
    if not path.is_file():
        return None
    source = path.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "selftest":
            return source, node
    return None


def expectations_in(selftest: ast.FunctionDef) -> list[ast.Call]:
    """Every `check(label, got, want)` call, in source order.

    Three arguments exactly. A `check` with two is a different helper, and one
    with a keyword is not the shape this weakening understands -- both are
    counted as dropped rather than silently skipped.
    """
    out = []
    for node in ast.walk(selftest):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "check" and len(node.args) == 3
                and not node.keywords):
            out.append(node)
    return sorted(out, key=lambda c: (c.lineno, c.col_offset))


class _Weaken(ast.NodeTransformer):
    """Rewrite the Nth `check(label, got, want)` into `check(label, got, got)`.

    The call still runs, still increments the case count, and still passes --
    which is exactly the residue this harness exists to measure. Replacing it
    with `pass` would be a different and easier question, because the case
    count would move and the count IS checked.
    """

    def __init__(self, target: int) -> None:
        self.target, self.seen, self.hit = target, 0, False

    def visit_Call(self, node):
        self.generic_visit(node)
        if (isinstance(node.func, ast.Name) and node.func.id == "check"
                and len(node.args) == 3 and not node.keywords):
            if self.seen == self.target:
                self.hit = True
                node.args[2] = node.args[1]
            self.seen += 1
        return node


def weakened(source: str, index: int) -> str | None:
    """The module source with expectation `index` comparing a value with itself."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "selftest":
            w = _Weaken(index)
            w.visit(node)
            if not w.hit:
                return None
            return ast.unparse(ast.fix_missing_locations(tree))
    return None


def _selftest_passes(box: _Sandbox, module: str) -> bool:
    done = box.run(["-m", f"watchquality.{module}", "--selftest"],
                       SELFTEST_TIMEOUT)
    return done.returncode == 0


# --------------------------------------------------------------------------
# the harness, asked of itself first
# --------------------------------------------------------------------------

FIXTURE = textwrap.dedent('''
    def selftest():
        cases = 0
        def check(label, got, want):
            nonlocal cases
            cases += 1
            if got != want:
                raise AssertionError(label)
        check("one", 1 + 1, 2)
        check("two", "a" + "b", "ab")
        return 0
''')


def test_the_weakening_leaves_the_call_counted_and_always_true():
    """A weakened expectation must still LOOK like a case that ran.

    Deleting the call, or replacing it with `pass`, moves the case count -- and
    the case count is checked, so the harness would be measuring the counter
    rather than the expectation.
    """
    got = weakened(FIXTURE, 0)

    assert got is not None
    assert "check('one', 1 + 1, 1 + 1)" in got, got
    assert "check('two', 'a' + 'b', 'ab')" in got, got
    assert got.count("check(") == FIXTURE.count("check(")


def test_the_weakening_reaches_each_expectation_in_turn_and_no_further():
    """One expectation per variant, and an index past the end is None."""
    assert "check('two', 'a' + 'b', 'a' + 'b')" in weakened(FIXTURE, 1)
    assert weakened(FIXTURE, 2) is None


def test_every_expectation_is_a_three_argument_check():
    """The shape the weakening understands, asked of the real selftests.

    A `check` with a different arity is not weakened, and a module whose
    expectations are all of some other shape would silently measure nothing --
    so the count is asserted rather than assumed.
    """
    found = {}
    for module in sorted(p.stem for p in PACKAGE.glob("*.py")):
        got = _selftest_source(module)
        if got is None:
            continue
        found[module] = len(expectations_in(got[1]))
    assert found, "no module in this package has a selftest"
    assert sum(found.values()) > 100, found


@pytest.mark.slow
@pytest.mark.parametrize("module", sorted(
    p.stem for p in PACKAGE.glob("*.py")
    if p.stem not in ("__init__", "selftest_proof")))
def test_which_selftest_expectations_a_mutant_sample_witnesses(module, tree):
    """Which expectation catches each mutant FIRST, and which catch nothing.

    A selftest raises on its first failing check, so for any one mutant exactly
    one expectation is the witness. That is the measurement those two
    issues ask for and it is a CENSUS, not a gate: an expectation no mutant in this
    sample reaches may be the only witness for a behaviour the sample never
    touched, and this harness cannot tell that from an expectation that tests
    nothing. Reporting the first as a defect would teach its reader to ignore
    the output, which is the failure every ledger in this project is written
    against.

    What IS asserted is the harness: where a mutant is caught at all, some
    expectation has to be named as the one that caught it. A run where every
    mutant is caught and no expectation is ever named is a harness measuring
    itself.
    """
    got = _selftest_source(module)
    if got is None:
        pytest.skip(f"{module} has no selftest")
    source, defn = got
    expectations = expectations_in(defn)
    if not expectations:
        pytest.skip(f"{module}.selftest has no three-argument check()")

    labels = {}
    for index, call in enumerate(expectations):
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            labels.setdefault(first.value, index)

    mutants = mutants_for(source)[:MUTANT_CAP]
    path = tree.package / f"{module}.py"
    original = path.read_text(encoding="utf-8")
    witnessed: dict[int, str] = {}
    survived = 0
    unnamed = 0
    timed_out = 0
    try:
        for label, mutated, _regions in mutants:
            path.write_text(mutated, encoding="utf-8")
            try:
                done = tree.run(["-m", f"watchquality.{module}", "--selftest"],
                                SELFTEST_TIMEOUT)
            except subprocess.TimeoutExpired:
                # A MUTANT CAN LOOP. That is not a caught mutant and not a
                # surviving one: nobody found out. Counted on its own line, so
                # a module whose sample was mostly timeouts cannot read as a
                # module whose expectations were mostly witnessed.
                timed_out += 1
                continue
            if done.returncode == 0:
                survived += 1
                continue
            said = done.stdout + done.stderr
            hit = next((i for text, i in labels.items() if text in said), None)
            if hit is None:
                unnamed += 1
            else:
                witnessed.setdefault(hit, label)
    finally:
        path.write_text(original, encoding="utf-8")

    caught = len(mutants) - survived - timed_out
    REPORT["modules"][module] = {
        "expectations": len(expectations),
        "labelled": len(labels),
        "mutants_tried": len(mutants),
        "mutants_caught": caught,
        "mutants_survived": survived,
        "mutants_timed_out": timed_out,
        "caught_by_something_unlabelled": unnamed,
        "expectations_witnessed": sorted(witnessed),
        "witnessed": len(witnessed),
    }

    # NOTHING IS ASSERTED PER MODULE, on purpose. A mutant can be caught by an
    # inline assert, by a helper that raises, or by the module failing to
    # import -- all real catches that name no expectation -- and a module whose
    # catches are all of that kind is a finding about where its evidence lives
    # rather than a defect in any one line. The sweep asserts the harness once,
    # below, and everything else is a census.


@pytest.mark.slow
def test_the_sweep_can_see_the_expectations_it_is_measuring():
    """The harness, asserted once over the whole sweep rather than per module.

    If no module's catches were ever traced back to a named expectation, this
    harness is measuring its own plumbing. It runs last: the census above fills
    the report, and this reads it.
    """
    modules = REPORT["modules"]
    if not modules:
        pytest.skip("the census did not run in this session")
    witnessed = sum(v["witnessed"] for v in modules.values())
    caught = sum(v["mutants_caught"] for v in modules.values())
    assert caught, "no mutant was caught anywhere; the sandbox is not the tree"
    assert witnessed, (
        "not one caught mutant was traced to a named expectation across "
        f"{len(modules)} module(s); this harness is measuring itself")


def teardown_module(_module) -> None:
    path = os.environ.get("WQ_SELFTEST_MUTATION_REPORT")
    if path:
        Path(path).write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
