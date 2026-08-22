"""`watch-audit`: five gates, one exit code, and the distinction that matters.

A gate that COULD NOT RUN must not report as a gate that passed. That is the
whole reason this wrapper returns 2 as well as 0 and 1, and it is what these
tests hold it to.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

import watchquality
from watchquality import wq_policy

SRC = Path(__file__).resolve().parent.parent / "watch-quality" / "src"
# The installed package, which the editable install points back at this tree.
# The roster is derived from what is in here rather than typed below.
PACKAGE = Path(watchquality.__file__).parent


@pytest.fixture(autouse=True)
def _no_policy_by_name(monkeypatch):
    """These cases are about the aggregator, not about a corpus.

    `watch-audit` exits 2 when no policy file resolves, because grading a
    corpus it never found is not a pass. This repository has no
    `watch-quality.toml`, so every case below has to ask for the
    neutral-defaults run BY NAME -- which is the escape hatch existing for
    exactly this, and asking for it here is the point of it having a name.
    """
    monkeypatch.setenv(wq_policy.ENV_NO_POLICY, "1")


def _load_audit():
    """Load the source tree's audit module by path.

    The installed wheel is what the gates themselves run from; this module is
    new, so importing `watchquality.audit` normally would find whatever is
    installed rather than what is under test.
    """
    spec = importlib.util.spec_from_file_location(
        "wq_audit_under_test", SRC / "watchquality" / "audit.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit = _load_audit()


def _stub(monkeypatch, name: str, code: int, text: str = ""):
    def main(argv):
        if text:
            print(text)
        return code
    monkeypatch.setattr(audit, "_load", lambda n: main if n == name else main)


def test_all_clean_is_exit_zero(monkeypatch, capsys):
    monkeypatch.setattr(audit, "GATES", [("a", ["--check"]), ("b", ["--check"])])
    monkeypatch.setattr(audit, "_load", lambda n: (lambda argv: 0))
    assert audit.main([]) == 0
    assert "all 2 gate(s) passed" in capsys.readouterr().out


CENSUS = "# 4 of 25 note(s) graded against their own rendering, 0 defect(s)"


def _census_gate(argv):
    """A gate that passes and says, on stderr, that it graded almost nothing."""
    print("# not graded: notes/a.md names no rendering", file=sys.stderr)
    print(CENSUS, file=sys.stderr)
    return 0


def test_a_gate_that_says_it_graded_nothing_is_not_hidden_by_a_pass(monkeypatch,
                                                                    capsys):
    """The false clean bill this whole file exists to refuse, one channel over.

    `note_gates` prints its census and its per-note skips to STDERR, on the
    argument that a skip is not a pass and the reader must see which it was.
    `run_gate` captured stdout only, so the audit's report body for that gate
    was the word EXIT=0 and nothing else, under `# all 6 gate(s) passed` -- over
    a real corpus where 21 of 25 notes were never opened. Anyone who reads,
    redirects, pastes or CI-captures this command saw a six-gate pass.
    """
    monkeypatch.setattr(audit, "GATES", [("a", ["--check"])])
    monkeypatch.setattr(audit, "_load", lambda n: _census_gate)

    assert audit.main([]) == 0

    out = capsys.readouterr().out
    assert CENSUS in out, out
    assert "not graded" in out, out
    assert "all 1 gate(s) passed" in out, out


def test_quiet_drops_the_detail_and_keeps_the_census(monkeypatch, capsys):
    """`--quiet` is for reading, not for hiding.

    It prints a gate's output only when the gate found something, which is the
    right rule for defect lines and the wrong one for a gate saying how much of
    the corpus it managed to grade. Quiet was the worse of the two: it printed
    `a: ok` and stopped.
    """
    monkeypatch.setattr(audit, "GATES", [("a", ["--check"])])
    monkeypatch.setattr(audit, "_load", lambda n: _census_gate)

    assert audit.main(["--quiet"]) == 0

    out = capsys.readouterr().out
    assert "a: ok" in out
    assert CENSUS in out, out


def test_what_a_failing_gate_wrote_to_stderr_reaches_the_report(monkeypatch,
                                                                capsys):
    """The same channel, on the path where it carries the reason.

    A gate that exits 2 having explained why on stderr is the case a reader
    most needs the explanation for, and it was dropped exactly as the census
    was.
    """
    def explainer(argv):
        print("# a: tesseract is not installed", file=sys.stderr)
        return 2

    monkeypatch.setattr(audit, "GATES", [("a", [])])
    monkeypatch.setattr(audit, "_load", lambda n: explainer)

    assert audit.main([]) == 2

    out = capsys.readouterr().out
    assert "tesseract is not installed" in out, out


def test_a_gate_that_cannot_be_loaded_says_so_in_the_report(monkeypatch, capsys):
    """The path where the audit speaks INSTEAD of the gate.

    There is no gate output to forward here, so the reason `run_gate` builds is
    the only thing standing between a reader and an empty section. A version
    that returns the code and drops the message leaves the same silent block
    this rule exists to refuse -- and the exit code, which the sibling rule
    already holds, is still a correct 2.
    """
    def boom(name):
        raise ImportError("no such gate")

    monkeypatch.setattr(audit, "GATES", [("a", [])])
    monkeypatch.setattr(audit, "_load", boom)

    assert audit.main([]) == 2

    out = capsys.readouterr().out
    assert "could not be loaded" in out, out
    assert "no such gate" in out, out


def test_a_gate_that_raises_after_speaking_keeps_both_halves(monkeypatch, capsys):
    """A gate that dies mid-run has usually already said the useful part.

    It printed findings on stdout and an explanation on stderr, and then hit
    the thing that killed it. All three belong in the report: the two halves it
    wrote, and the exception that stopped it. Dropping either captured half
    here is invisible to every exit-code case in this file, because the code is
    2 in all of them.
    """
    def half_way(argv):
        print("notes/a.md:1 E-SOMETHING found before the fall")
        print("# a: 1 of 9 note(s) graded", file=sys.stderr)
        raise RuntimeError("tesseract went missing")

    monkeypatch.setattr(audit, "GATES", [("a", [])])
    monkeypatch.setattr(audit, "_load", lambda n: half_way)

    assert audit.main([]) == 2

    out = capsys.readouterr().out
    assert "found before the fall" in out, out
    assert "1 of 9 note(s) graded" in out, out
    assert "tesseract went missing" in out, out


def test_a_silent_gate_adds_no_empty_section(monkeypatch, capsys):
    """The control: forwarding stderr must not print a blank line per gate.

    Without this, "the report carries stderr" is satisfied by a version that
    prints an empty stderr block for all six gates, and the noise is what makes
    a reader stop reading the block that matters.
    """
    monkeypatch.setattr(audit, "GATES", [("a", [])])
    monkeypatch.setattr(audit, "_load", lambda n: (lambda argv: 0))

    assert audit.main([]) == 0

    out = capsys.readouterr().out
    assert "stderr" not in out.lower(), out
    assert "\n\n\n" not in out, out


def test_one_defect_is_exit_one_and_names_the_gate(monkeypatch, capsys):
    monkeypatch.setattr(audit, "GATES", [("a", []), ("b", [])])
    monkeypatch.setattr(audit, "_load",
                        lambda n: (lambda argv: 1 if n == "b" else 0))
    assert audit.main(["--quiet"]) == 1
    out = capsys.readouterr().out
    assert "b: DEFECTS" in out and "a: ok" in out
    assert "found defects: b" in out


def test_a_gate_that_cannot_load_is_two_not_zero(monkeypatch, capsys):
    def boom(name):
        raise ImportError("no such gate")
    monkeypatch.setattr(audit, "GATES", [("a", [])])
    monkeypatch.setattr(audit, "_load", boom)
    assert audit.main(["--quiet"]) == 2
    out = capsys.readouterr().out
    assert "COULD NOT RUN" in out
    assert "did not complete" in out


def test_a_gate_that_raises_is_two_not_zero(monkeypatch):
    def raiser(argv):
        raise RuntimeError("tesseract went missing")
    monkeypatch.setattr(audit, "GATES", [("a", [])])
    monkeypatch.setattr(audit, "_load", lambda n: raiser)
    assert audit.main(["--quiet"]) == 2


def test_a_defect_outranks_nothing_but_a_failure_outranks_a_defect(monkeypatch):
    """The exit code is the worst outcome, so a broken gate is never hidden by
    a merely-failing one."""
    codes = {"a": 1, "b": 2, "c": 0}
    monkeypatch.setattr(audit, "GATES", [(k, []) for k in codes])
    monkeypatch.setattr(audit, "_load", lambda n: (lambda argv: codes[n]))
    assert audit.main(["--quiet"]) == 2


def test_a_defective_gate_and_a_broken_one_get_a_line_each(monkeypatch, capsys):
    """The split itself, which nothing read until an independent pass ran.

    The case above sets exactly this shape -- one gate at 1, one at 2 -- and
    asserts only the exit code, so restoring the pre-fix single-`failed` list
    verbatim left `398 passed`, EXIT=0 (round-4 refutation F-1). That code
    printed `# 2 gate(s) did not complete: a, b`, naming a gate that completed
    perfectly well and burying the defects it found.

    So the LINES are read here, and each is pinned to the gate that earned it.
    """
    codes = {"a": 1, "b": 2, "c": 0}
    monkeypatch.setattr(audit, "GATES", [(k, []) for k in codes])
    monkeypatch.setattr(audit, "_load", lambda n: (lambda argv: codes[n]))
    assert audit.main(["--quiet"]) == 2
    out = capsys.readouterr().out
    assert "# 1 gate(s) found defects: a" in out
    assert "# 1 gate(s) did not complete: b" in out
    # Neither line may claim a gate it did not earn, and the clean gate is in
    # neither -- read off the names each line lists, not off the whole output.
    named = {line.split(": ", 1)[0].split(" gate(s) ", 1)[1]:
             line.split(": ", 1)[1].split(", ")
             for line in out.splitlines() if line.startswith("# 1 gate(s) ")}
    assert named == {"found defects": ["a"], "did not complete": ["b"]}


def test_a_gate_killed_by_a_signal_is_not_a_gate_that_passed(monkeypatch, capsys):
    """A negative code is the third way a gate fails to answer.

    `run_gate` maps a subprocess killed by a signal to its negative code. That
    reached `broken`, but `max(worst, -9)` is 0, so the run printed `# all 1
    gate(s) passed`, exited 0, and threw away the `did not complete` line it
    had already built (round-4 refutation F-2).
    """
    monkeypatch.setattr(audit, "GATES", [("a", [])])
    monkeypatch.setattr(audit, "_load", lambda n: (lambda argv: -9))
    assert audit.main(["--quiet"]) == 2
    out = capsys.readouterr().out
    assert "did not complete: a" in out
    assert "passed" not in out


def test_systemexit_from_a_gate_is_honoured(monkeypatch):
    def exiter(argv):
        raise SystemExit(1)
    monkeypatch.setattr(audit, "GATES", [("a", [])])
    monkeypatch.setattr(audit, "_load", lambda n: exiter)
    assert audit.main(["--quiet"]) == 1


def test_list_prints_the_real_order_and_runs_nothing(capsys):
    assert audit.main(["--list"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [line.split(". ", 1)[1] for line in out] == [
        f"{name} {' '.join(flags)}" for name, flags in audit.GATES]
    # Structure before witnesses: an unresolved anchor makes every later gate
    # report symptoms of it.
    assert out[0].endswith("resolve_note --check")


def test_the_roster_is_every_module_that_declares_itself_a_gate():
    """verification-2 S2: a gate can be deleted and both suites stay green.

    Deleting `("demote_note", ["--diff"])` left `295 passed` EXIT=0, every
    module selftest green, and `watch-audit` printing `# all 4 gate(s) passed`
    over a corpus the deleted gate would have failed -- round 1's §G1 false
    clean bill through a different door. The answer then was to spell the five
    rows out here, which pinned it against ONE edit; a copy of a list is still
    a list, and the reviewer's mutant that reordered both files was green
    (verification-final N7).

    So the expectation is not written here at all. It is DERIVED from the
    modules: a gate is a module that declares `GATE_FLAGS`, and the flags the
    audit runs it with are the ones it declared. Dropping a gate now has to
    drop the declaration too, adding one has to add it, and neither file alone
    can make this green.
    """
    declared = {}
    for path in sorted(PACKAGE.glob("*.py")):
        module = importlib.import_module(f"watchquality.{path.stem}")
        flags = getattr(module, "GATE_FLAGS", None)
        if flags is not None:
            declared[path.stem] = list(flags)
    assert dict(audit.GATES) == declared


def test_a_gate_runs_after_every_gate_it_reads():
    """Order is derived from the imports, which is where the reason lives.

    `audit`'s docstring says structure first: a note whose anchors do not
    resolve produces nonsense from every checker after it, so reading a witness
    failure caused by a broken anchor spends the reader's attention on a
    symptom. That is not a preference -- it is `anchor_manifest` importing
    `resolve_note`, and both witnesses importing `anchor_manifest`. So the
    order is checked against the import graph rather than against a copy of
    the roster that a second edit can move.

    The second half comes from the declared flags: a gate that PROPOSES a
    change runs after every gate that only checks, because a demotion proposed
    from a note whose anchors do not resolve is the same symptom-before-cause
    the order exists to prevent.

    What this does NOT pin: two checking gates that read nothing of each
    other's are unordered by the code, and swapping them is an equivalent
    mutation. The check says what the code says and no more.
    """
    names = [name for name, _ in audit.GATES]
    seen: list[str] = []
    for name in names:
        src = (PACKAGE / f"{name}.py").read_text(encoding="utf-8")
        for other in names:
            if other != name and f"from .{other} import" in src:
                assert other in seen, (name, "reads", other, "but runs first")
        seen.append(name)
    proposing = [i for i, (_, flags) in enumerate(audit.GATES) if "--diff" in flags]
    checking = [i for i, (_, flags) in enumerate(audit.GATES) if "--diff" not in flags]
    assert not proposing or min(proposing) > max(checking), audit.GATES


@pytest.mark.parametrize("gate", [name for name, _ in audit.GATES])
@pytest.mark.parametrize("code", [1, 2])
def test_main_honours_every_gate_over_the_real_roster(monkeypatch, gate, code):
    """One level up from `run_gate`, and the same false clean bill.

    verification-final N6: `if name == "demote_note": code = 0` inside `main`'s
    own loop left `328 passed` EXIT=0 and `# all 5 gate(s) passed` over a
    corpus that gate rejected. `run_gate` is exercised gate by gate one screen
    down, and `main` only ever against a monkeypatched two-entry fake roster --
    so the aggregator that actually runs was never asked what it does with an
    answer it does not like.

    The harness hands it one, at every position of the REAL roster in turn.
    """
    monkeypatch.setattr(audit, "_load",
                        lambda name: (lambda argv: code if name == gate else 0))
    assert audit.main(["--quiet"]) == code


@pytest.mark.parametrize("gate", [name for name, _ in audit.GATES])
@pytest.mark.parametrize("code", [1, 2])
def test_every_gate_in_the_roster_has_its_verdict_honoured(monkeypatch, gate, code):
    """The roster says a gate RAN; this says its answer was read.

    verification-3 R6, a live survivor: `run_gate` returning 0 for
    `demote_note` whatever the gate really exited left `297 passed`, EXIT=0
    and `watch-audit` printing `# all 5 gate(s) passed`. The roster case one
    screen up passes under it -- the list is untouched, the gate is invoked,
    its verdict is discarded -- which is the same false clean bill as §G1 and
    as a selftest printing OK over zero cases: a check reporting success
    without having checked.

    The untargeted form (every gate's 1 mapped to 0) already died; a
    per-gate special case did not, because nothing exercised the roster's
    members one at a time. Parametrised over the REAL roster, so a new gate
    inherits the guarantee instead of an exemption.
    """
    monkeypatch.setattr(audit, "_load", lambda n: (lambda argv: code))
    assert audit.run_gate(gate, [], [])[0] == code


@pytest.mark.parametrize("gate", [name for name, _ in audit.GATES])
def test_every_gate_in_the_roster_is_a_module_that_loads(gate):
    """A roster of names nobody imports is a roster of nothing.

    The literal-roster case compares strings; strings are green for a gate
    whose module was renamed or deleted. `run_gate` maps that to 2 at runtime,
    which is correct and late -- it is discovered by running the corpus, not
    by the suite that claims to hold the roster.
    """
    assert callable(audit._load(gate))


def test_notes_are_passed_through_to_each_gate(monkeypatch):
    seen = []
    monkeypatch.setattr(audit, "GATES", [("a", ["--check"])])
    monkeypatch.setattr(audit, "_load",
                        lambda n: (lambda argv: seen.append(argv) or 0))
    audit.main(["--quiet", "one.md", "two.md"])
    assert seen == [["--check", "one.md", "two.md"]]


@pytest.mark.slow
def test_the_suite_answers_the_same_with_a_corpus_policy_in_the_environment(tmp_path):
    """A suite whose answer depends on which shell started it cannot be a gate.

    Found by running the commit hook: it exports `$WATCH_QUALITY_POLICY` for the
    leak scan, the suite inherited it, and six cases in
    `test_spec_note_contract.py` turned red. They were not wrong -- a real
    corpus policy adds required lanes and oracle rules, so a fixture note
    carrying one defect carries four, and a case reading "1 defect(s)
    outstanding" sees a 4.

    `tests/conftest.py` clears both ambient variables at import time. This is
    what holds that: a subprocess run of the affected file WITH the variable
    exported, which was red before the clear and is green after it. Marked slow
    because it starts a second interpreter and collects a file.
    """
    import os
    import subprocess
    import sys

    policy = tmp_path / "watch-quality.toml"
    policy.write_text('required_lanes = ["facts", "quality"]\n', encoding="utf-8")
    repo = Path(__file__).resolve().parent.parent

    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/test_spec_note_contract.py"],
        cwd=repo, capture_output=True, text=True,
        env=dict(os.environ, WATCH_QUALITY_POLICY=str(policy),
                 PYTHONDONTWRITEBYTECODE="1"))

    assert done.returncode == 0, done.stdout[-2000:]
