"""`watch-audit`: five gates, one exit code, and the distinction that matters.

A gate that COULD NOT RUN must not report as a gate that passed. That is the
whole reason this wrapper returns 2 as well as 0 and 1, and it is what these
tests hold it to.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "watch-quality" / "src"


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


def test_notes_are_passed_through_to_each_gate(monkeypatch):
    seen = []
    monkeypatch.setattr(audit, "GATES", [("a", ["--check"])])
    monkeypatch.setattr(audit, "_load",
                        lambda n: (lambda argv: seen.append(argv) or 0))
    audit.main(["--quiet", "one.md", "two.md"])
    assert seen == [["--check", "one.md", "two.md"]]
