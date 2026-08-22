"""The spec is a table, and this is what makes it one.

Six review rounds found the same shape: a finding names one wrong answer, the
repair refuses exactly that answer, and the neighbouring wrong answers stay
accepted until somebody types one. Nothing in the package ever said what a rule
REFUSES, so "is this rule complete?" had no answer except another review.

`spec/*.toml` answers it. This file makes the answer binding: a rule with no
code, a code with no rule, an owner that was renamed, a neighbour with no case
— each of those is a failing test rather than a finding four rounds later.

What it does NOT check is whether a rule is CORRECT. A wrong rule with four
honest neighbours passes here and fails a review. That is the division of
labour: the table makes review cheap, it does not replace it.
"""
from __future__ import annotations

import ast
import importlib
import re
import tomllib
from pathlib import Path

import pytest

import watchquality
from watchquality import wq_policy


PACKAGE = Path(watchquality.__file__).parent
REPO = PACKAGE.resolve().parents[2]
SPEC_DIR = REPO / "spec"
TESTS_DIR = REPO / "tests"

RE_CODE = re.compile(r"\b(E-[A-Z0-9]+(?:-[A-Z0-9]+)*)\b")


def _rules() -> list[dict]:
    out: list[dict] = []
    for path in sorted(SPEC_DIR.glob("*.toml")):
        # `unspecified.toml` is the debt list, not a rule table. `proposed-*`
        # are drafts: extracted from the source but not yet promoted, because a
        # rule may only join the real table once every neighbour it names has a
        # case. Reading them here would turn 256 missing cases into 256 red
        # rows on day one, which is a way of teaching everyone to ignore the
        # colour.
        # `unpinned.toml` is a ledger ABOUT these rules -- one dated row per
        # rule the mutation run could not pin -- and its rows carry an id and a
        # date and nothing else. Read as rules they became five owner-less
        # entries, which is how they WERE read for one run: 129 rules that had
        # just passed came out failing, because the loader had begun handing
        # the harness rows that name no owner to break.
        if (path.name in ("unspecified.toml", "unpinned.toml")
                or path.name.startswith("proposed-")):
            continue
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
        for rule in doc.get("rule", []):
            rule["_file"] = path.name
            out.append(rule)
    return out


def _unspecified() -> dict[str, str]:
    path = SPEC_DIR / "unspecified.toml"
    if not path.is_file():
        return {}
    return dict(tomllib.loads(path.read_text(encoding="utf-8")).get("codes", {}))


# The size of the debt list, committed. `spec/unspecified.toml` says it may
# only shrink and nothing read its length: a code with no rule row is legal so
# long as it is written down as owed, which makes this list the escape hatch
# for the conformance case below. Lower this in the same commit as the row you
# struck off. Raising it is the edit the file's first sentence forbids.
UNSPECIFIED_CAP = 0


def _codes_in_source() -> dict[str, set[str]]:
    """Every defect code the package can PRINT, and which module prints it.

    Read from the SOURCE rather than from a list, for the reason every roster
    in this suite is derived: a list is one edit shorter than the thing it
    claims to cover.

    Read from the STRINGS rather than from the text, because a plain text scan
    counts three things that are not codes and cannot be specified:

      a comment  `E-LANE-SHARED lived here and is gone` -- the removal argument,
                 which the table would then demand a rule for
      a prefix   `assert "E-LANE-ORACLE" in got[0]` -- matching two real codes
                 that both begin that way, and naming neither
      a negative `check(..., "E-WIN-" in out, False)` -- a selftest proving a
                 code does NOT appear

    All three were on the debt list for a day, which is a debt list teaching
    its reader to ignore it. Codes are collected from string constants only,
    and never from inside a module's own `selftest`: what a selftest asserts
    about a code is not the same as the module emitting one.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        selftests = {node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == "selftest"}
        inside_selftest = {id(n) for fn in selftests for n in ast.walk(fn)}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in inside_selftest:
                continue
            for match in RE_CODE.finditer(node.value):
                found.setdefault(match.group(1), set()).add(path.stem)
    return found


def _test_names() -> set[str]:
    """`<file>::<function>` for every test in the suite, by AST.

    Not by running pytest: collection would make this test depend on the whole
    suite being importable, and the question here is only whether the case was
    written.
    """
    names: set[str] = set()
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(f"tests/{path.name}::{node.name}")
    return names


def _selftest_labels() -> set[str]:
    """`<module>::<label>` for every labelled check inside a module selftest.

    A module's own selftest is not a lesser suite: `test_selftests.py` runs all
    twelve of them inside this pytest run, derives their cases from source and
    proves the comparator still reacts. So a neighbour pinned there is pinned,
    and the table is allowed to say so.

    What is NOT allowed is the vaguer thing the old format forced: writing
    `kind = "none"` for a case that exists, because the only spelling on offer
    was a pytest node id. That understates the coverage and hides the real
    gaps underneath it.

    The label is the first string argument of a `check(...)` call, which is the
    shape every comparator in this package takes: `check("what this proves",
    got, want)`.

    NOT the first string argument of ANY call. That version put a note's
    filename, a `RuntimeError` message and a blob of fixture JSON into the pool
    -- `note_gates` alone contributed five entries that were not labels -- so a
    rule could cite a fixture as the case that pins it and pass. It also
    inflated the pool by 280 entries, which is most of the floor below.
    """
    labels: set[str] = set()
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not (isinstance(node, ast.FunctionDef) and node.name == "selftest"):
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call) or not call.args:
                    continue
                if not (isinstance(call.func, ast.Name) and call.func.id == "check"):
                    continue
                first = call.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    labels.add(f"{path.stem}::{first.value}")
    return labels


RULES = _rules()
CODES = _codes_in_source()
TEST_NAMES = _test_names()
SELFTEST_LABELS = _selftest_labels()
POLICY_TABLES = set(wq_policy.TABLES) | set(wq_policy.NESTED_TABLES)


def test_the_spec_directory_is_actually_read():
    """An empty parametrisation is a silent skip, not a pass.

    Every other check in this file is parametrised over `RULES`, so an empty
    spec directory, a renamed folder or a TOML file that stopped parsing would
    leave the whole file green while checking nothing.
    """
    assert SPEC_DIR.is_dir(), SPEC_DIR
    assert len(RULES) >= 12, len(RULES)
    assert TEST_NAMES, TESTS_DIR
    # 400, not the 500 this floor carried until 2026-08-21. The pool held 694
    # entries then and 414 now, and the 280 that left were never labels: fixture
    # filenames, an exception message, a blob of JSON. A floor met by junk
    # measures the junk.
    assert len(SELFTEST_LABELS) >= 400, len(SELFTEST_LABELS)


@pytest.mark.parametrize("rule", RULES, ids=lambda r: r["id"])
def test_every_rule_names_a_code_that_exists(rule):
    """A row for a rule the package does not implement is prose.

    `code = ""` is legal and means the rule has no defect string of its own --
    an exit code, a printed line, a refusal at load time. Those still need
    every other field.
    """
    code = rule.get("code", "")
    if not code:
        return
    assert code in CODES, (rule["id"], code, "names no code in the package")


@pytest.mark.parametrize("rule", RULES, ids=lambda r: r["id"])
def test_every_rule_names_an_owner_that_exists(rule):
    """Module and symbol, resolved by import, and it has to be a FUNCTION.

    A row pointing at a function that was renamed is a row nobody reads, and
    it is the first thing to rot: the rule survives, the name moves.

    Resolving by `hasattr` alone accepted `note_gates.re` and `note_gates.sys`
    -- stdlib modules the gate imports -- and `note_gates.PROG`, a string
    constant, as the owners of a refusal. A refusal is made of code, so its
    owner is a function; and the function has to be defined in the module the
    row names, or the row sends its reader to a re-export.
    """
    module_name, _, symbol = rule["owner"].partition(".")
    module = importlib.import_module(f"watchquality.{module_name}")
    obj = module
    for part in symbol.split("."):
        assert hasattr(obj, part), (rule["id"], rule["owner"], part)
        obj = getattr(obj, part)
    assert callable(obj), (rule["id"], rule["owner"],
                           f"is a {type(obj).__name__}, which refuses nothing")
    assert getattr(obj, "__module__", "") == f"watchquality.{module_name}", (
        rule["id"], rule["owner"], "is defined somewhere else and re-exported")


@pytest.mark.parametrize("rule", RULES, ids=lambda r: r["id"])
def test_every_rule_is_bounded_by_at_least_two_neighbours(rule):
    """One neighbour is the case the reviewer reported. The second is the one
    that keeps being missed.

    This is pattern C-7 written down: a fix that recognises exactly the one
    reported wrong answer is the defect this project produced four rounds in a
    row.
    """
    neighbours = rule.get("neighbour", [])
    assert len(neighbours) >= 2, (rule["id"], len(neighbours))
    for n in neighbours:
        assert n.get("case", "").strip(), (rule["id"], "a neighbour with no case text")
    # TWO ENTRIES IS NOT TWO CASES. One rule met this floor by citing a single
    # test function twice under two different case descriptions, so the second
    # neighbour was the first one retold. Citing one test for two neighbours is
    # legitimate when the test really covers both directions -- a parametrised
    # case often does -- but not when it is the ONLY test the rule names.
    distinct = {n.get("test") for n in neighbours}
    assert len(distinct) >= 2, (
        rule["id"], sorted(distinct),
        "two neighbours, one case: the floor is met by retelling one test")


@pytest.mark.parametrize("rule", RULES, ids=lambda r: r["id"])
def test_every_neighbour_names_a_test_that_exists(rule):
    """The load-bearing check.

    Adding a neighbour to the table without writing its case turns the suite
    red. That is what makes an unlisted neighbour a failing row rather than a
    review finding four rounds later -- and it is the only line here that
    costs anything to satisfy.
    """
    for n in rule.get("neighbour", []):
        kind = n.get("kind", "pytest")
        assert kind in ("pytest", "selftest"), (rule["id"], kind, "an uncovered "
                                                "neighbour cannot be promoted")
        pool = TEST_NAMES if kind == "pytest" else SELFTEST_LABELS
        assert n.get("test") in pool, (rule["id"], kind, n.get("test"), "no such case")


@pytest.mark.parametrize("rule", RULES, ids=lambda r: r["id"])
def test_every_excuse_names_a_policy_table_that_exists(rule):
    """A rule cannot claim a debt ledger nothing reads."""
    for table in rule.get("excused_by", []):
        assert table in POLICY_TABLES, (rule["id"], table, sorted(POLICY_TABLES))


def test_every_code_in_the_package_is_either_specified_or_owed():
    """The direction that finds the holes.

    Checking the table against the code only proves the table is honest about
    what it claims. This checks the code against the table, which is where an
    unspecified rule shows up -- and unspecified is where every regression in
    this project has come from.

    `spec/unspecified.toml` is the debt list. It may only shrink, and this
    case is what makes that true: a code added without a rule has to be
    written down as owed, in the same commit.
    """
    specified = {r["code"] for r in RULES if r.get("code")}
    owed = set(_unspecified())
    missing = set(CODES) - specified - owed
    assert not missing, sorted(missing)


def test_the_debt_list_holds_no_code_that_is_already_specified():
    """A row in both lists is a debt that was paid and never struck off."""
    specified = {r["code"] for r in RULES if r.get("code")}
    both = specified & set(_unspecified())
    assert not both, sorted(both)


def test_the_debt_list_may_only_shrink():
    """The sentence at the head of the file, given a number a test can read.

    `spec/unspecified.toml` says it may only shrink and nothing read its size.
    A code added with no rule row is legal as long as it is ALSO written down
    as owed -- so the debt list is the escape hatch for the case above, and an
    unbounded escape hatch is not one. The list has been empty since
    2026-08-21; committing that zero is what stops the next new code being
    parked here instead of specified (V3 Q5, V4 item 9).

    Would fail if: a row is added to the debt list without lowering the cap,
    which is the edit the file's own first sentence forbids.
    """
    rows = sorted(_unspecified())
    assert len(rows) <= UNSPECIFIED_CAP, (
        f"the debt list holds {len(rows)} row(s) and was capped at "
        f"{UNSPECIFIED_CAP}: {rows}. This list may only shrink -- write the "
        f"rule, or lower the cap in the same commit as the row you struck off.")
    # Grown by one, the same comparison is red. A cap resting on the current
    # count proves nothing on its own about what it would refuse.
    assert len({**_unspecified(), "E-GROWN-IN-A-SANDBOX": "x"}) > UNSPECIFIED_CAP


def test_the_debt_list_holds_no_code_the_package_no_longer_prints():
    """And the other way a debt list rots: the code is gone, the row remains."""
    stale = set(_unspecified()) - set(CODES)
    assert not stale, sorted(stale)


def test_the_enumerator_counts_what_is_printed_and_not_what_is_mentioned():
    """The narrowing that emptied the debt list, pinned so it cannot widen back.

    A plain text scan counted a code named in a comment, a prefix inside an
    assertion, and a selftest proving a code does NOT appear. All three sat on
    the debt list for a day and none could ever be paid off, which is a debt
    list teaching its reader to ignore it.

    The counter-example is built here rather than asserted about the package,
    so it keeps working when those particular lines move.
    """
    import tempfile

    src = (
        '"""E-DOC-ONLY in a docstring counts, because a docstring is a string."""\n'
        "# E-COMMENT-ONLY is a comment and is not printed by anything\n"
        "def main():\n"
        '    print("E-REAL-CODE something went wrong")\n'
        "def selftest():\n"
        '    check("E-SELFTEST-ONLY must not appear", "E-REAL-CODE" in out, False)\n'
    )
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp)
        (pkg / "fake.py").write_text(src, encoding="utf-8")
        tree = ast.parse(src)
        selftests = {n for n in tree.body
                     if isinstance(n, ast.FunctionDef) and n.name == "selftest"}
        inside = {id(n) for fn in selftests for n in ast.walk(fn)}
        got = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in inside:
                    continue
                got |= {m.group(1) for m in RE_CODE.finditer(node.value)}
    assert got == {"E-REAL-CODE", "E-DOC-ONLY"}, got


def test_no_defect_code_is_built_across_an_interpolation():
    """A code the enumerator cannot see is a code the table cannot cover.

    `_codes_in_source` reads string constants, and an f-string's literal
    fragments are constants -- so `f"{rel}:1 E-ORACLE-EMPTY ..."` is seen. What
    is NOT seen is a code SPLIT by the interpolation itself:
    `f"E-NOTE-{kind}-MISSING"` records the fragment `E-NOTE` and never sees the
    code that gets printed. Both directions of the debt list break at once: an
    unspecified code escapes the census, and a phantom prefix demands a rule for
    a code nothing prints.

    The package has none today. This keeps it that way, because the failure is
    silent and the fix -- write the code out -- costs a line.
    """
    def split_codes(tree: ast.AST, where: str) -> list[tuple[str, int, str]]:
        found: list[tuple[str, int, str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            for piece, following in zip(node.values, node.values[1:]):
                if not (isinstance(piece, ast.Constant)
                        and isinstance(piece.value, str)):
                    continue
                tail = piece.value.rsplit(" ", 1)[-1]
                if tail.startswith("E-") and not isinstance(following, ast.Constant):
                    found.append((where, node.lineno, tail))
        return found

    # The scan reacts. Asserted on a counter-example built here, so the proof
    # keeps working when the package has no violation to point at -- which is
    # the state this case exists to hold.
    caught = split_codes(ast.parse(
        'print(f"{rel}:1 E-NOTE-{kind}-MISSING nothing names this")\n'), "made up")
    assert [c[2] for c in caught] == ["E-NOTE-"], caught
    whole = split_codes(ast.parse(
        'print(f"{rel}:1 E-NOTE-MISSING {count} of them")\n'), "made up")
    assert whole == [], ("a code written out whole is not split", whole)

    split: list[tuple[str, int, str]] = []
    for path in sorted(PACKAGE.glob("*.py")):
        split += split_codes(ast.parse(path.read_text(encoding="utf-8")), path.stem)
    assert not split, split


def test_the_label_pool_holds_labels_and_not_fixtures():
    """The counter-example, built here rather than asserted about the package.

    `_selftest_labels` used to take the first string argument of ANY call inside
    a selftest, which put a note's filename, an exception message and a blob of
    fixture JSON into the pool a rule may cite as its pinning case.
    """
    src = (
        "def selftest():\n"
        '    note("2026-08-20--a-fixture--VID.md", "run.json")\n'
        '    path.write_text("{\\"segments\\": []}")\n'
        '    raise RuntimeError("no such thing")\n'
        '    check("this one is a label", got, want)\n'
    )
    tree = ast.parse(src)
    got = set()
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef) and node.name == "selftest"):
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call) or not call.args:
                continue
            if not (isinstance(call.func, ast.Name) and call.func.id == "check"):
                continue
            first = call.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                got.add(first.value)
    assert got == {"this one is a label"}, got


def test_no_rule_id_is_used_twice():
    ids = [r["id"] for r in RULES]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})


# The size of the table on the day it was closed. Raising this number is
# allowed and is the whole point of having it: it costs one line, and that line
# has to say which milestone reopened the table.
#
# 134 -> 135 on 2026-08-22, for AUDIT-FORWARDS-WHAT-A-GATE-SAID. A round of
# independent verification found the aggregator printing `# all 6 gate(s)
# passed` with an empty body for the gate that had just reported grading 4 of
# 25 notes -- correct exit codes, and a report that said the opposite of what
# the run found. No existing row could see it: every audit row is about a code.
#
# 135 -> 136 on 2026-08-22, for CORPUS-ANCHOR-SET. The same round found the
# leak gate exiting 0 over thirteen tracked lines that reproduced a private
# recording by its timestamps alone. Every exit code was right: none of the
# thirteen holds a refusable word, and a word list can only refuse what somebody
# thought to write down. No existing row could see it, because every corpus row
# compares against a list.
FROZEN_RULE_COUNT = 136


def test_the_table_is_closed():
    """A frozen surface is a claim a test can hold.

    The loop this project kept running was: add a gate, review it, find defects
    in it, add a guard to catch those, review the guard. Every round ended with
    more surface than it started with, so the work left could never shrink.

    Freezing the count is what makes the remaining work finite. It does not
    forbid a new rule -- it makes adding one a decision somebody signs, in a
    commit message, rather than a thing that happens while fixing something
    else.
    """
    assert len(RULES) == FROZEN_RULE_COUNT, (
        f"the table holds {len(RULES)} rules and was frozen at "
        f"{FROZEN_RULE_COUNT}. The spec is closed; a new rule belongs to a new "
        "milestone. If this is deliberate, raise FROZEN_RULE_COUNT in the same "
        "commit and say in the message which milestone reopened the table.")
