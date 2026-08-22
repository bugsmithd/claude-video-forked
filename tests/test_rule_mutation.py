"""Break every rule's owner and check that a case the rule names notices.

Six review rounds ran the same loop: build a gate, dispatch reviewers, they
find real defects, fix, repeat. The loop never converged, because the oracle
for "is this rule real?" was a fresh reader, and a fresh reader always finds
something.

The spec table already claims, for every rule, which cases pin it. That claim
is mechanically checkable. Break the rule's owner; a case the rule itself cites
must go red. Run it over every rule and the output is the list of rules that
nothing pins. The list is countable, it only shrinks, and when it is empty the
table proves itself.

A rule is PINNED when at least one mutant of its owner makes at least one of
its own cited cases fail. Otherwise it is UNPINNED, and that is a finding about
the rule, not an opinion about it.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import tomllib
from pathlib import Path

import pytest

from test_spec_conformance import PACKAGE, REPO, RULES, SPEC_DIR

# Bounds. A mutant can loop forever, and an owner with two hundred branches
# would cost more than the answer is worth, so both are capped -- and every
# drop is counted into the run report rather than passed off as coverage.
MUTANT_CAP = 12
PYTEST_TIMEOUT = 300
SELFTEST_TIMEOUT = 120

# What the run dropped, and why. Written out when WQ_MUTATION_REPORT names a
# path, so the counts in a ledger row come from a measurement and not a memory.
REPORT: dict[str, object] = {
    "rules": {},
    "mutants_dropped": {},
    "cases_red_at_baseline": {},
    "timeouts": [],
    "loaded_from": "",
    "started": time.time(),
}


def _ledgered() -> dict[str, str]:
    """Rules already written down as unpinned, with the date they were found."""
    path = SPEC_DIR / "unpinned.toml"
    if not path.is_file():
        return {}
    doc = tomllib.loads(path.read_text(encoding="utf-8"))
    return {row["id"]: f"{row.get('found', '?')} {row.get('why', '')}".strip()
            for row in doc.get("rule", [])}


UNPINNED = _ledgered()


def _rows_naming_no_rule(ledger: dict[str, str], rules: list[dict]) -> list[str]:
    """Ledger ids that answer to no rule in the table.

    `_ledgered` reads an id and a date and never asks whether the id names
    anything. A typo, a renamed rule or a deleted one leaves a row that skips
    no case while still counting against the cap, which is a ledger that cannot
    reach zero because part of it stopped meaning anything.
    """
    return sorted(set(ledger) - {r["id"] for r in rules})

# The size of the ledger, committed. `spec/unpinned.toml` says it may only
# shrink, and nothing enforced that: adding a row turns the pin test into a skip
# for that rule, silently, with the suite green. Lower this in the same commit
# as the row you delete. Raising it is the edit the sentence forbids.
LEDGER_CAP = 2


class _Mutator(ast.NodeTransformer):
    """One mutation per instance, selected by index.

    Four shapes, chosen because they are what this package's guards are made
    of: a comparison, a branch, a conjunction, and a returned answer.
    """

    def __init__(self, target: int) -> None:
        self.target, self.seen, self.label = target, 0, None
        # The statements the mutation lands in, as source, innermost first. A
        # function that owns three defect codes gets one mutation per branch,
        # and the branch tells you which code the mutation was aimed at -- see
        # `_is_aimed`. Innermost FIRST and not innermost ONLY, because the
        # statement a mutation lands in often prints nothing: a guard whose body
        # is `continue` decides whether a code is emitted and holds none of it.
        self.regions: tuple[str, ...] = ()
        self._stack: list[ast.stmt] = []

    def visit(self, node):
        if not isinstance(node, ast.stmt):
            return super().visit(node)
        self._stack.append(node)
        try:
            return super().visit(node)
        finally:
            self._stack.pop()

    def _fire(self, label: str) -> bool:
        hit = self.seen == self.target
        if hit:
            self.label = label
            # Unparsed HERE, before the mutation is applied: one mutation fires
            # per run, so these are the statements as they were written.
            self.regions = tuple(ast.unparse(s) for s in reversed(self._stack))
        self.seen += 1
        return hit

    def visit_Compare(self, node):                     # x <= y  ->  x > y
        self.generic_visit(node)
        flip = {ast.Lt: ast.GtE, ast.LtE: ast.Gt, ast.Gt: ast.LtE,
                ast.GtE: ast.Lt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
                ast.In: ast.NotIn, ast.NotIn: ast.In,
                ast.Is: ast.IsNot, ast.IsNot: ast.Is}
        op = type(node.ops[0])
        if op in flip and self._fire(f"comparison {op.__name__}"):
            node.ops = [flip[op]()]
        return node

    def visit_If(self, node):                          # if COND -> if False
        self.generic_visit(node)
        if self._fire("branch never taken"):
            node.test = ast.Constant(value=False)
        return node

    def visit_BoolOp(self, node):                      # a and b -> a
        self.generic_visit(node)
        if len(node.values) > 1 and self._fire("second operand dropped"):
            return node.values[0]
        return node

    def visit_Return(self, node):                      # return X -> return None
        self.generic_visit(node)
        if node.value is not None and self._fire("return value dropped"):
            node.value = ast.Constant(value=None)
        return node


def mutants_for(source: str) -> list[tuple[str, str, tuple[str, ...]]]:
    """(label, mutated source, the statements it landed in) per mutation."""
    count = _Mutator(-1)
    count.visit(ast.parse(source))
    out: list[tuple[str, str, tuple[str, ...]]] = []
    for i in range(count.seen):
        m = _Mutator(i)
        mutated = ast.fix_missing_locations(m.visit(ast.parse(source)))
        if m.label:
            out.append((m.label, ast.unparse(mutated), m.regions))
    return out


class _Sandbox:
    """A copy of the tree, with the package the cases import pinned INSIDE it.

    The package is installed editable against the real checkout, so a copy that
    is not on the path silently tests the original: every mutant then leaves
    every case green and the run reports total coverage it never had. The
    constructor asserts the loaded module resolves inside the copy, and every
    later result depends on that assertion having passed.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        shutil.copytree(
            REPO, root,
            ignore=shutil.ignore_patterns(
                ".git", "__pycache__", ".pytest_cache", ".venv", "dist", "*.pyc"),
            dirs_exist_ok=True,
        )
        self.src = root / "watch-quality" / "src"
        self.package = self.src / "watchquality"
        self.original: dict[str, str] = {}
        proof = self.run(["-c", "import watchquality; print(watchquality.__file__)"], 120)
        self.loaded = proof.stdout.strip()
        if not self.loaded.startswith(str(self.src)):
            raise RuntimeError(
                "the copy is not what the cases import; every result would be "
                f"a result about the original: {self.loaded!r}")
        REPORT["loaded_from"] = self.loaded

    def run(self, args: list[str], timeout: int) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(self.src)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        for leak in ("PYTEST_CURRENT_TEST", "PYTEST_ADDOPTS", "WQ_MUTATION_REPORT"):
            env.pop(leak, None)
        return subprocess.run(
            [sys.executable, "-B", *args], cwd=self.root, env=env,
            capture_output=True, text=True, timeout=timeout)

    def install(self, module: str, text: str) -> None:
        """Write a module body into the copy, and make sure it is what runs.

        Measured in this repo: restoring a file of the same size inside one
        mtime tick served a stale `.pyc`, the mutation did not run, and the
        same case came out red on one run and green on the next from identical
        inputs. A result that cannot be reproduced twice is not a result.
        """
        path = self.package / f"{module}.py"
        self.original.setdefault(module, path.read_text(encoding="utf-8"))
        path.write_text(text, encoding="utf-8")
        os.utime(path, None)
        for cache in self.src.rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)

    def restore(self, module: str) -> None:
        if module not in self.original:
            return
        path = self.package / f"{module}.py"
        path.write_text(self.original.pop(module), encoding="utf-8")
        os.utime(path, None)
        for cache in self.src.rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)


@pytest.fixture(scope="session")
def sandbox(tmp_path_factory: pytest.TempPathFactory) -> _Sandbox:
    box = _Sandbox(tmp_path_factory.mktemp("mutation") / "tree")
    yield box
    path = os.environ.get("WQ_MUTATION_REPORT")
    if path:
        REPORT["seconds"] = round(time.time() - float(REPORT["started"]), 1)
        Path(path).write_text(json.dumps(REPORT, indent=1), encoding="utf-8")


def _owner_source(owner: str) -> tuple[str, list[str], int, int]:
    """(module, its lines, first line, last line) of the function a rule owns.

    A module-level constant the rule also leans on is NOT in here: the
    `RE_DEFECT_LINE` half of a guard lives beside the function, not inside it,
    and nothing below can break it. A rule pinned here is pinned against its
    owner's body and no further.
    """
    module, _, symbol = owner.partition(".")
    lines = (PACKAGE / f"{module}.py").read_text(encoding="utf-8").splitlines(keepends=True)
    body = ast.parse("".join(lines)).body
    node = None
    for part in symbol.split("."):
        node = next((n for n in body
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef)) and n.name == part), None)
        if node is None:
            raise LookupError(f"{owner}: no {part} to break")
        body = node.body
    return module, lines, node.lineno - 1, node.end_lineno


_MUTANTS: dict[str, tuple[str, list[tuple[str, str]]]] = {}


def _owner_mutants(owner: str) -> tuple[str, list[tuple[str, str]]]:
    """Capped, evenly spread mutants of one owner, as whole module bodies."""
    if owner not in _MUTANTS:
        module, lines, start, end = _owner_source(owner)
        segment = "".join(lines[start:end])
        indent = " " * (len(segment) - len(segment.lstrip(" ")))
        raw = mutants_for(textwrap.dedent(segment))
        if len(raw) > MUTANT_CAP:
            step = len(raw) / MUTANT_CAP
            keep = [raw[int(i * step)] for i in range(MUTANT_CAP)]
            REPORT["mutants_dropped"][owner] = len(raw) - len(keep)
            raw = keep
        built = []
        for i, (label, mutated, region) in enumerate(raw):
            spliced = (lines[:start]
                       + [textwrap.indent(mutated + "\n", indent)]
                       + lines[end:])
            built.append((f"{label} #{i}", "".join(spliced), region))
        _MUTANTS[owner] = (module, built)
    return _MUTANTS[owner]


def _cases(rule: dict) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The cases this rule cites: pytest node ids, and selftest modules.

    34 neighbours in the table carry no `kind`. They are read as `pytest`,
    which is the same default `test_spec_conformance` already applies to them,
    and every one of them names a pytest node id.
    """
    node_ids: set[str] = set()
    modules: set[str] = set()
    for n in rule.get("neighbour", []):
        test = n.get("test", "")
        if n.get("kind", "pytest") == "selftest":
            modules.add(test.split("::")[0])
        elif test:
            node_ids.add(test)
    return tuple(sorted(node_ids)), tuple(sorted(modules))


_GREEN: dict[tuple, bool] = {}


def _passes(sandbox: _Sandbox, key: tuple, args: list[str], timeout: int) -> bool:
    """Did this command pass? A timeout counts as noticed, and is counted."""
    if key in _GREEN:
        return _GREEN[key]
    try:
        done = sandbox.run(args, timeout)
        ok = done.returncode == 0
    except subprocess.TimeoutExpired:
        REPORT["timeouts"].append(list(key))
        ok = False
    _GREEN[key] = ok
    return ok


def _pytest_args(node_ids: tuple[str, ...]) -> list[str]:
    return ["-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *node_ids]


def _selftest_args(module: str) -> list[str]:
    return ["-m", f"watchquality.{module}", "--selftest"]


def _green_cases(sandbox: _Sandbox, rule: dict) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The cited cases that pass with nothing broken.

    A case that is already red would look killed by every mutant and would
    report the rule pinned by a failure that has nothing to do with it.
    """
    node_ids, modules = _cases(rule)
    if node_ids and not _passes(sandbox, ("pytest",) + node_ids,
                                _pytest_args(node_ids), PYTEST_TIMEOUT):
        good = tuple(n for n in node_ids
                     if _passes(sandbox, ("pytest", n), _pytest_args((n,)), PYTEST_TIMEOUT))
        for bad in set(node_ids) - set(good):
            REPORT["cases_red_at_baseline"].setdefault(bad, []).append(rule["id"])
        node_ids = good
    good_modules = []
    for module in modules:
        if _passes(sandbox, ("selftest", module), _selftest_args(module), SELFTEST_TIMEOUT):
            good_modules.append(module)
        else:
            REPORT["cases_red_at_baseline"].setdefault(f"{module}::selftest",
                                                       []).append(rule["id"])
    return node_ids, tuple(good_modules)


def _controls(rule: dict) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Cases in the SAME module that an aimed mutation should leave alone.

    A mutation that kills the cited case has only pinned the rule if it was
    AIMED. Flipping the first `is None` in a long function often stops the
    module importing, and then every case in the suite goes red -- the cited
    one included, for a reason that has nothing to do with the rule. Without a
    control, that wrecking ball is indistinguishable from a guard doing its
    job, and the run reports every rule pinned.

    TWO SOURCES, BOTH ALWAYS. A case pinning a different FUNCTION in the module
    is one. A case pinning a sibling RULE that shares this owner is the other,
    and it is the sharper of the two: a mutation aimed at this rule's branch
    should leave the branch beside it alone, while one that breaks the whole
    function reddens both.

    The sibling rule used to be reached only when the different-function search
    came back empty, so 11 rules got the sharp control and 123 kept the blunt
    one -- and a mutation confined to one function cannot redden a case about
    another unless the module stops importing, which is checked one line
    earlier. That made the blunt control close to a constant. Flipping the first
    guard of one note reader makes it answer a read error for every note and
    produce none of the twelve defects it owns: twelve cited cases die, twelve
    controls stay green, and one mutation is recorded as the pin for all twelve.
    Taking from both sources is what refuses that.

    A FEW FROM EACH, not the first few of the union. Sorting the union by node
    id and cutting to three drops whichever source sorts late, which on the
    twelve-rule owner above is the source that matters.

    AND SELFTESTS, because a kill can be scored by one. A control of pytest node
    ids only cannot notice a mutation that reddens the module's selftest and
    nothing else, so that mutation is recorded as aimed by a control incapable
    of failing. Three window rules were pinned by a mutant that broke JSON
    rendering, which is not any of their branches.

    A CASE THIS RULE ITSELF CITES IS NEVER A CONTROL, from either source. Two
    rules routinely cite one case, and a control drawn from this rule's own
    citations is green exactly when the mutant failed to kill -- which makes the
    aim test a second copy of the kill test that precedes it.
    """
    module, _, symbol = rule["owner"].partition(".")
    mine, mine_modules = _cases(rule)
    siblings: set[str] = set()
    family: set[str] = set()
    modules: set[str] = set()
    for other in RULES:
        if other["id"] == rule["id"]:
            continue
        other_module, _, other_symbol = other["owner"].partition(".")
        if other_module != module:
            continue
        other_ids, other_modules = _cases(other)
        (family if other_symbol == symbol else siblings).update(other_ids)
        modules.update(other_modules)
    # A CODE-LESS ROW GETS NO FAMILY CONTROL. Without a code the own-branch
    # route can never fire, so the control is the only witness there is -- and
    # two code-less rows sharing an owner describe one guard between them, which
    # no mutation of that guard can tell apart. Measured: `LEDGER-MAY-ONLY-
    # SHRINK` and `UNGRADED-LEDGER-AGES` both own `resolve_note.excused` and both
    # refuse "a row reaching a note filed after the row's own date", so every
    # mutation reddened both and the first read as unpinned. That is a finding
    # about the TABLE -- one guard written down twice -- and until somebody
    # decides which row goes, it is recorded here rather than turned into a
    # ledger row the file says may not be added.
    if not rule.get("code"):
        family = set()
    return (tuple(sorted(family - set(mine)))[:2]
            + tuple(sorted(siblings - set(mine)))[:2],
            tuple(sorted(modules - set(mine_modules))))


def _still_imports(sandbox: _Sandbox, module: str) -> bool:
    done = sandbox.run(["-c", f"import watchquality.{module}"], SELFTEST_TIMEOUT)
    return done.returncode == 0


# Codes as the source writes them, so a region can be asked WHICH codes it
# prints rather than whether one string sits inside another.
RE_CODE = re.compile(r"E-[A-Z0-9]+(?:-[A-Z0-9]+)*")


def _region_is_this_rules_branch(rule: dict, regions: tuple[str, ...]) -> bool:
    """Does this statement print THIS rule's code, and no other rule's?

    Both halves were missing and both were exploitable.

    WHOLE CODES, NOT SUBSTRINGS. `E-GRADE` is a rule's entire code and five
    other codes begin with it, so `code in region` hands one rule its
    neighbour's kill. None of the five pairs fires today only because in every
    pair the two rules have different owners.

    AND NO OTHER RULE'S CODE. The region is the innermost enclosing STATEMENT,
    and nothing bounds what a statement encloses. Wrap a nine-rule roll-call in
    one guard and flip the guard: the function answers nothing for every note in
    the corpus, the module still imports, and that one `if` prints all nine
    codes -- so all nine rules read their own code in it and all nine were
    recorded pinned, by one mutation, with the control short-circuited. Four of
    the pins in a real run were won this way before this test existed.

    A statement that prints two rules' codes is one rule's branch for neither.

    OUTWARD UNTIL SOMETHING IS PRINTED. The statement a mutation lands in often
    prints nothing: `if m.start() in matched_starts: continue` decides whether
    a code is emitted and holds none of it. Asking only the innermost statement
    made those mutations abstain, and the pin fell to the control -- which a
    sibling rule sharing the owner then correctly refused, leaving the rule
    reading unpinned with its aimed mutation sitting in the list. The walk stops
    at the FIRST enclosing statement that prints any code at all, so a roll-call
    further out can neither lend its codes nor take this one's away.
    """
    code = rule.get("code", "")
    if not code:
        return False
    for region in regions:
        printed = set(RE_CODE.findall(region))
        if printed:
            return printed == {code}
    return False


def _is_aimed(sandbox: _Sandbox, rule: dict, module: str, key: tuple,
              regions: tuple[str, ...] = (), by_selftest: bool = False) -> bool:
    """Did this mutant kill the cited case WITHOUT flattening everything else?

    Two ways to answer, and the first is the sharper one.

    THE MUTATION LANDED IN THIS RULE'S OWN CODE. `wq_corpus_scan.scan_text`
    owns three defect codes in three branches, and a mutation inside the branch
    that prints THIS rule's code is aimed at this rule by construction -- no
    control can say it better. That case was being thrown away: every sibling
    case in the module runs through the same function, so an aimed mutation
    reddened them too and looked like a wrecking ball. The leak gate sat in the
    unpinned ledger for a day because of it.

    Otherwise, the control: cases pinning the rest of this module -- a sibling
    rule sharing the owner, a sibling function, and the module's selftests --
    all have to stay green. That is what tells a guard doing its job from a
    mutation that flattened the function or stopped the module importing.
    """
    if not _still_imports(sandbox, module):
        return False
    if _region_is_this_rules_branch(rule, regions):
        REPORT["rules"].setdefault(rule["id"], {})["aim"] = "the rule's own branch"
        return True
    controls, control_modules = _controls(rule)
    if not controls and not control_modules:
        # Nothing else in this module is pinned by the table, so the import
        # check above is the only control there is. Recorded, not hidden.
        REPORT["rules"].setdefault(rule["id"], {})["control"] = "import only"
        return True
    if controls and not _passes(sandbox, key + ("control",) + controls,
                                _pytest_args(controls), PYTEST_TIMEOUT):
        return False
    if not by_selftest:
        return True
    # A SELFTEST-SCORED KILL IS JUDGED BY SELFTESTS. Asked of every kill this
    # would be a control the aimed mutation itself fails: a selftest is
    # all-or-nothing over its whole module, so an aimed mutation reddens the
    # sibling's selftest exactly as it reddens the rule's own. Asked only where
    # the kill came from one, it is the control that was missing -- four rules
    # lost their pin to the strict version, all four of them killed through
    # pytest, and all four measured.
    return all(_passes(sandbox, key + ("control-selftest", m),
                       _selftest_args(m), SELFTEST_TIMEOUT)
               for m in control_modules)


def _best_kill(kills: list[tuple[str, bool]]) -> str | None:
    """Of the aimed kills found, the one with the strongest witness.

    Mutants are tried in syntax order, so returning at the first aimed kill
    records the EARLIEST evidence rather than the best. Two window rules have a
    later mutant that lands in the statement printing their own code and reddens
    their cited cases; neither was recorded, because an unrelated earlier mutant
    scraped past the control first, and the report reads the same either way.
    """
    for label, own_branch in kills:
        if own_branch:
            return label
    return kills[0][0] if kills else None


def _mutants_that_survive(rule: dict, sandbox: _Sandbox) -> list[str]:
    """Every capped mutant of the owner that left every cited case green."""
    module, mutants = _owner_mutants(rule["owner"])
    node_ids, modules = _green_cases(sandbox, rule)
    record = REPORT["rules"].setdefault(rule["id"], {})
    record.update(owner=rule["owner"], mutants=len(mutants),
                  pytest_cases=list(node_ids), selftest_modules=list(modules))
    if not mutants:
        record["verdict"] = "owner has no mutation this mutator can make"
        return ["the owner carries no comparison, branch, conjunction or "
                "returned value, so nothing here was ever put to the question"]
    if not node_ids and not modules:
        record["verdict"] = "no case that passes with nothing broken"
        return ["no cited case runs green here, so nothing here can pin anything"]
    survived: list[str] = []
    indiscriminate: list[str] = []
    kills: list[tuple[str, bool]] = []
    try:
        for index, (label, text, regions) in enumerate(mutants):
            sandbox.install(module, text)
            key = (rule["owner"], index)
            dead = (node_ids and not _passes(sandbox, key + ("pytest",) + node_ids,
                                             _pytest_args(node_ids), PYTEST_TIMEOUT))
            by_selftest = False
            if not dead:
                by_selftest = dead = any(
                    not _passes(sandbox, key + ("selftest", m),
                                _selftest_args(m), SELFTEST_TIMEOUT)
                    for m in modules)
            if not dead:
                survived.append(label)
                continue
            # A KILL IS NOT A PIN UNTIL IT IS AIMED. See `_is_aimed`.
            if not _is_aimed(sandbox, rule, module, key, regions, by_selftest):
                indiscriminate.append(label)
                continue
            own_branch = _region_is_this_rules_branch(rule, regions)
            kills.append((label, own_branch))
            if own_branch:
                break              # nothing later can witness this better
    finally:
        sandbox.restore(module)
    winner = _best_kill(kills)
    if winner:
        record["verdict"] = f"pinned by {winner}"
        return []
    record["verdict"] = "unpinned"
    record["survived"] = survived
    record["indiscriminate"] = indiscriminate
    return survived + [f"{label} (killed everything, so it aimed at nothing)"
                       for label in indiscriminate] or ["no mutant reached it"]


def test_the_mutator_changes_something_it_can_name():
    src = "def f(x):\n    if x > 3:\n        return 'big'\n    return 'small'\n"
    muts = mutants_for(src)
    assert muts, "a function with a branch has at least one mutant"
    labels = [label for label, _, _ in muts]
    assert any("comparison" in label for label in labels), labels
    for _, mutated, _ in muts:
        assert mutated != src
        compile(mutated, "<mutant>", "exec")     # a mutant must still parse


def test_a_mutation_reports_the_statement_it_landed_in():
    """The aim test's input, pinned separately from the aim test.

    `_is_aimed` reads this string to decide whether a mutation landed in the
    branch that prints the rule's own code. If the region came back empty, or
    came back as the whole function, every mutation would look aimed at every
    rule the function owns -- which is the wrecking-ball reading, arriving by
    the other door.
    """
    src = ("def f(x, y):\n"
           "    if x > 3:\n"
           "        print('E-FIRST loud')\n"
           "    if y > 3:\n"
           "        print('E-SECOND quiet')\n")
    # A LIST, not a dict keyed by label: two branches produce two mutations
    # with the same label, and keying by it hides one whole branch.
    regions = [(label, chain) for label, _, chain in mutants_for(src)]

    assert len(regions) == 4, regions
    for label, chain in regions:
        assert chain, (label, "no statement recorded")
        assert chain[0].count("if ") == 1, (label, chain, "the region is one branch")
        # Innermost FIRST, and the function itself is the last thing out.
        assert chain[-1].startswith("def f("), (label, chain)
    # Each innermost region names its own code and not its neighbour's.
    inner = [c[0] for _, c in regions]
    assert sum("E-FIRST" in r and "E-SECOND" not in r for r in inner) == 2
    assert sum("E-SECOND" in r and "E-FIRST" not in r for r in inner) == 2


def test_a_region_naming_two_rules_codes_is_nobodys_branch():
    """The wrecking ball that walked in through the aim test's front door.

    The region is the innermost enclosing STATEMENT, and nothing bounds how much
    a statement encloses. Wrap a roll-call function's whole body in one guard
    and flip the guard: the function returns nothing for every note in the
    corpus, the module still imports, and the region -- that one `if` -- prints
    all nine of the codes the function owns. Under a substring test each of the
    nine rules read its own code in that region and was recorded pinned, by one
    mutation, with the control short-circuited and never run.

    A statement that prints two rules' codes is one rule's branch for neither of
    them.
    """
    region = ("if REVIEW_DIR is not None:\n"
              "    out.append('E-LANE-AMBIGUOUS two lanes')\n"
              "    out.append('E-LANE-MISSING no lane')")

    assert not _region_is_this_rules_branch({"code": "E-LANE-AMBIGUOUS"}, (region,))
    assert not _region_is_this_rules_branch({"code": "E-LANE-MISSING"}, (region,))


def test_a_region_naming_one_rules_code_is_that_rules_branch():
    """The control, without which the case above is satisfied by returning False.

    The region test exists because in a module where every rule runs through one
    scanner an aimed mutation reddens the siblings too, so the control alone
    calls a real guard a wrecking ball. Refusing every region would put the leak
    gate back in the unpinned ledger, which is where it sat for a day.
    """
    region = "if word in line:\n    out.append('E-CORPUS-REFUSED-WORD here')"

    assert _region_is_this_rules_branch({"code": "E-CORPUS-REFUSED-WORD"}, (region,))


def test_a_code_is_matched_whole_and_never_as_a_prefix():
    """`E-GRADE` is a real rule's whole code, and five others start with it.

    A substring test hands one rule its neighbour's kill whenever one code is a
    prefix of another. Five such pairs are in the table today and none fires,
    because in every pair the two rules have different owners -- so this is one
    refactor away from live and nothing else refuses it.
    """
    region = "if stamp is None:\n    out.append('E-GRADE-UNSTAMPED no stamp')"

    assert not _region_is_this_rules_branch({"code": "E-GRADE"}, (region,))
    assert _region_is_this_rules_branch({"code": "E-GRADE-UNSTAMPED"}, (region,))


def test_a_rule_with_no_code_never_claims_a_region():
    """Eleven rows carry `code = ""`, and a region test cannot speak for them.

    For those the control is the only witness there is, and saying so is the
    difference between a measurement and a rule that cannot fail.
    """
    region = "if x:\n    out.append('E-ANYTHING at all')"

    assert not _region_is_this_rules_branch({"code": ""}, (region,))
    assert not _region_is_this_rules_branch({}, (region,))


def test_a_rule_that_shares_its_owner_with_siblings_still_gets_a_control():
    """The gap that left four window rules pinned by argument parsing.

    A control was "a case pinning a DIFFERENT function in the SAME module", and
    where every rule in a module names one owner there is none -- which is
    exactly the module where telling an aimed mutation from a wrecking ball
    matters most. Four rules about window shape and two about row shape had the
    import check as their only control, so any mutant that killed anything was
    called aimed. The winning mutant for all four window rules was one flip in
    `ap.parse_args`.

    A sibling rule sharing the owner is a SHARPER control than a sibling
    function, not a weaker one: a mutation aimed at this rule's branch should
    leave the branch beside it alone, and one that breaks the whole function
    reddens both.
    """
    family = [r for r in RULES if r["owner"] == "note_windows.main"]
    assert len(family) >= 2, "this case needs an owner several rules share"

    node_ids, _ = _controls(family[0])

    assert node_ids, family[0]["id"]


def test_a_control_is_never_a_case_the_rule_itself_cites():
    """A rule cannot be its own control.

    Sharing an owner often means sharing a case, and a control drawn from this
    rule's own citations is green exactly when the mutant failed to kill --
    which turns the aim test into a second copy of the kill test.
    """
    for rule in RULES:
        own, own_modules = _cases(rule)
        node_ids, modules = _controls(rule)
        assert not set(node_ids) & set(own), rule["id"]
        assert not set(modules) & set(own_modules), rule["id"]


def test_a_control_draws_on_the_sibling_rule_as_well_as_the_sibling_function():
    """Both sources, not one or the other.

    The sibling RULE sharing the owner was reached only when the sibling
    FUNCTION search came back empty, so eleven rules got the sharper control and
    a hundred and twenty-three kept the weaker one. That is what let one
    mutation be recorded as the pin for twelve rules at once: flip the first
    guard of a note reader and it answers one read error for every note,
    producing none of the twelve defects it owns -- every cited case dies, and
    every control pins a DIFFERENT function, so every control stays green.

    A mutation confined to one function cannot redden a case about another
    unless the module stops importing, and that is checked one line earlier. For
    those rules the control was close to a constant.
    """
    twelve = [r for r in RULES if r["owner"] == "anchor_manifest.check_note"]
    assert len(twelve) >= 2, "this case needs an owner several rules share"
    rule = twelve[0]
    family_cases: set[str] = set()
    for other in twelve[1:]:
        family_cases.update(_cases(other)[0])
    family_cases -= set(_cases(rule)[0])

    node_ids, _ = _controls(rule)

    assert family_cases & set(node_ids), (rule["id"], sorted(family_cases)[:3])


def test_a_control_carries_the_selftests_its_siblings_cite():
    """A kill can be scored by a selftest, so a control has to be able to fail.

    `_mutants_that_survive` counts a module selftest going red as a kill. The
    control was pytest node ids only, so a mutation that reddens the selftest
    and touches no sibling pytest case was recorded as AIMED, by a control
    structurally incapable of noticing. Three window rules were pinned that way
    by a mutant that broke JSON rendering and nothing else.
    """
    with_selftests = [r for r in RULES
                      if any(_cases(o)[1]
                             for o in RULES
                             if o["id"] != r["id"]
                             and o["owner"].split(".")[0] == r["owner"].split(".")[0])]
    assert with_selftests, "no module in the table pairs a rule with a selftest"

    assert any(_controls(r)[1] for r in with_selftests)


def test_the_selftest_control_is_asked_only_of_a_selftest_scored_kill():
    """Asked of every kill, it is a control the aimed mutation itself fails.

    A selftest is all-or-nothing over its whole module, so a mutation aimed at
    this rule's branch reddens a sibling's selftest exactly as it reddens the
    rule's own case. Requiring it green for every kill cost four rules their
    pin on a measured run, and all four had been killed through pytest -- which
    is the direction the selftest control has nothing to say about.
    """
    rule = next(r for r in RULES if _controls(r)[1])
    module = rule["owner"].partition(".")[0]

    class _Stub:
        """Every pytest control green, every selftest red."""

        def run(self, args, timeout):
            return subprocess.CompletedProcess(
                args, 1 if "--selftest" in args else 0, "", "")

    stub = _Stub()
    # A region printing nothing, so the own-branch route abstains and the
    # control is what answers.
    assert _is_aimed(stub, rule, module, ("scored-by-pytest",), ())
    assert not _is_aimed(stub, rule, module, ("scored-by-selftest",), (),
                         by_selftest=True)


def test_an_own_branch_kill_is_preferred_over_a_control_route_kill():
    """Mutants are tried in syntax order, so the first aimed one is the earliest.

    It is not the best. Two window rules have a later mutant that lands in the
    statement printing their own code and reddens their cited cases, and neither
    was recorded, because an unrelated earlier mutant scraped past the control
    first. The report then carries the weaker evidence and reads the same.
    """
    kills = [("branch never taken #6", False), ("comparison Gt #10", True)]

    assert _best_kill(kills) == "comparison Gt #10"
    # And with nothing own-branch to prefer, the first survivor still wins.
    assert _best_kill([("a", False), ("b", False)]) == "a"
    assert _best_kill([]) is None


def test_a_guard_whose_block_prints_one_code_is_that_rules_branch():
    """The statement the mutation lands in often prints nothing at all.

    `if m.start() in matched_starts: continue` decides whether this rule's code
    is emitted, and the statement itself holds no code -- so the own-branch
    route abstained and the pin fell to the control, which a sibling rule
    sharing the owner correctly refuses. The rule then reads as unpinned while
    the mutation that was aimed at it sat in the list.

    So the walk goes outward from the mutation to the first enclosing statement
    that prints ANY code, and asks that one. The wrecking ball is refused by the
    same sentence it always was: the roll-call's guard reaches a block printing
    nine codes, and a block printing two rules' codes is one rule's branch for
    neither.
    """
    rule = {"id": "R", "code": "E-CITE-MALFORMED"}
    inner = "if m.start() in matched_starts:\n    continue"
    outer = ('for m in RE_CITE_START.finditer(body):\n'
             '    if m.start() in matched_starts:\n        continue\n'
             '    out.append({"error": "E-CITE-MALFORMED token does not close"})')

    assert _region_is_this_rules_branch(rule, (inner, outer))
    # And the walk stops at the FIRST block that prints anything: a roll-call
    # above it cannot lend its codes, nor take this one's away.
    roll = outer + '\n    out.append({"error": "E-CITE-STALE stale"})'
    assert not _region_is_this_rules_branch(rule, (inner, roll))


def test_two_code_less_rows_on_one_owner_are_not_each_others_control():
    """Two rows describing one guard cannot separate each other.

    `LEDGER-MAY-ONLY-SHRINK` and `UNGRADED-LEDGER-AGES` both own
    `wq_policy.excused` and both refuse a row reaching a note filed after the
    row's own date. Neither carries a code, so neither can ever show its own
    branch, and every mutation of that guard reddens both -- which under a
    family control makes both unpinnable by construction.

    That is a finding about the table, not the harness: one guard is written
    down twice. Until a row goes, a code-less rule keeps the sibling-function
    control and does not take a sibling rule as one.
    """
    pair = [r for r in RULES if r["owner"] == "wq_policy.excused"]
    assert len(pair) >= 2 and not any(r.get("code") for r in pair), pair

    for rule in pair:
        node_ids, _ = _controls(rule)
        for other in pair:
            if other["id"] != rule["id"]:
                assert not set(_cases(other)[0]) & set(node_ids), rule["id"]
    # And a rule that DOES carry a code still takes its family as a control --
    # that is what refuses the wrecking ball.
    windows = [r for r in RULES if r["owner"] == "note_windows.main"]
    assert all(r.get("code") for r in windows), windows
    family_cases = {c for r in windows[1:] for c in _cases(r)[0]}
    assert family_cases & set(_controls(windows[0])[0])


def test_the_unpinned_ledger_may_only_shrink():
    """The file says so and nothing enforced it.

    Adding a row turns the pin test into `pytest.skip` for that rule,
    permanently and silently: no baseline, no comparison against history, and a
    green suite. The escape hatch was one TOML block wide. A committed number is
    three lines and makes the sentence mechanical.
    """
    assert len(UNPINNED) <= LEDGER_CAP, (
        f"the ledger holds {len(UNPINNED)} rows and was capped at {LEDGER_CAP}. "
        "This table may only shrink: pin the rule, or lower the cap in the same "
        "commit as the row you deleted.")
    # And the comparison DISCRIMINATES, which a cap sitting exactly on the row
    # count does not prove by itself. Grow the ledger by one and the same test
    # is red -- so this case cannot go quietly inert the day somebody raises
    # the cap and the row count together.
    grown = {**UNPINNED, "wq-grown-in-a-sandbox": "1999-01-01 appended"}
    assert len(grown) > LEDGER_CAP


def test_the_ledger_states_its_own_size_and_the_number_is_read():
    """The prose in that file was wrong for a day and nothing could notice.

    `spec/unpinned.toml` carries several paragraphs of run history, and one of
    them stated "130 of 134 pinned, 4 unpinned" and then, one sentence later,
    that three rows had left a table of five -- wrong against the run report,
    against the row count two screens down, and against its own arithmetic. The
    conformance suite reads the rows and never the comments, so prose and table
    could disagree indefinitely (V4 item 6, V3 Q0).

    A declared `rows` key is the smallest thing a test can read. It does not
    make the paragraphs true; it makes the one number they all turn on
    checkable, and it has to be edited in the same commit as a row.

    Would fail if: a row is added or deleted without touching the count.
    """
    doc = tomllib.loads((SPEC_DIR / "unpinned.toml").read_text(encoding="utf-8"))
    assert "rows" in doc, (
        "spec/unpinned.toml must declare `rows = <n>` beside its table, so the "
        "count its prose turns on is a number rather than a sentence")
    assert doc["rows"] == len(doc.get("rule", [])), (
        f"the file declares {doc['rows']} row(s) and holds "
        f"{len(doc.get('rule', []))}")
    assert doc["rows"] == len(UNPINNED), (
        "the declared count and the ledger the harness actually reads have "
        "come apart, which means a row has a duplicate id")


def test_a_ledger_row_naming_no_rule_is_reported_rather_than_silent():
    """A rotten row skips nothing, and nothing ever said so.

    `_ledgered` reads an id and a date and never asks whether the id names a
    rule that exists. So a row whose id is a typo -- or whose rule was renamed,
    or deleted -- sits on the ledger looking like a paid-for exemption while
    skipping no case at all, and the cap above counts it as one of the two rows
    the table is allowed. The ledger cannot shrink toward zero if part of it is
    already naming nothing (V3 Q5, V4 item 9).

    Would fail if: `_rows_naming_no_rule` stops comparing against the real ids.
    """
    # The ledger that ships names only rules that exist.
    assert _rows_naming_no_rule(UNPINNED, RULES) == []

    # And the check is the reason that sentence is worth reading: a row whose
    # id nothing answers to is named, not counted as coverage.
    rotten = {**UNPINNED, "no-such-rule-id": "2026-08-21 typo"}
    assert _rows_naming_no_rule(rotten, RULES) == ["no-such-rule-id"]


def test_the_constructed_wrecking_ball_is_refused_over_the_real_roll_call():
    """The attack that produced this fix, rebuilt from the shipped source.

    `resolve_note.check_lanes` is the roll-call for nine lane rules. Wrap its
    body in a guard the package already uses elsewhere and let the mutator flip
    the guard: the function reports nothing for any note, the module still
    imports, and one mutation was recorded as an aimed pin for all nine rules at
    once -- the ledgered one among them.

    Built here from the real function rather than from a fixture, so a refactor
    that moves the codes around cannot quietly retire the case.
    """
    owner = [r for r in RULES if r["owner"] == "resolve_note.check_lanes"]
    codes = {r["code"] for r in owner if r.get("code")}
    assert len(codes) >= 2, "this case needs a function that owns several codes"

    _module, lines, start, end = _owner_source("resolve_note.check_lanes")
    real = textwrap.dedent("".join(lines[start:end]))
    wrapped = ("def check_lanes(note, root):\n    if root is not None:\n"
               + textwrap.indent(real, "        "))
    guard_regions = [chain for _, _, chain in mutants_for(wrapped)
                     if chain and chain[0].startswith("if root is not None:")]
    assert guard_regions, "the mutator never landed in the guard"

    for chain in guard_regions:
        for code in codes:
            assert not _region_is_this_rules_branch({"code": code}, chain), code


def test_every_prefix_pair_in_the_real_table_is_told_apart():
    """Asked of the table that ships, not of a fixture.

    A case built from two invented codes proves the matcher; this proves it
    against the pairs that actually exist, so a new rule whose code extends an
    old one inherits the guarantee instead of an exemption.
    """
    codes = sorted({r["code"] for r in RULES if r.get("code")})
    pairs = [(a, b) for a in codes for b in codes if a != b and b.startswith(a)]
    assert pairs, "the table has at least one code that is a prefix of another"

    for shorter, longer in pairs:
        region = f"if x:\n    out.append('{longer} something')"
        assert not _region_is_this_rules_branch({"code": shorter}, (region,)), \
            (shorter, longer)
        assert _region_is_this_rules_branch({"code": longer}, (region,))


@pytest.mark.slow
@pytest.mark.parametrize("rule", RULES, ids=lambda r: r["id"])
def test_every_rule_is_pinned_by_a_case_it_names(rule, sandbox):
    """Break the owner; a case the rule cites must notice.

    This is the whole termination condition. A rule whose owner can be broken
    every way the mutator knows, with every cited case still green, is pinned
    by nothing -- and no reviewer had to form an opinion for that to be true.
    """
    if rule["id"] in UNPINNED:
        pytest.skip(f"ledgered: {UNPINNED[rule['id']]}")
    survived = _mutants_that_survive(rule, sandbox)
    assert not survived, (
        f"{rule['id']}: owner {rule['owner']} survives "
        f"{len(survived)} mutation(s) with every cited case green: "
        f"{survived[:3]}")
