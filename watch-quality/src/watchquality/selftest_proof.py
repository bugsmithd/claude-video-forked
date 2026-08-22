"""What a selftest must EMIT before the harness will call it a selftest.

Four review rounds found the same shape -- a check reporting success without
having checked -- and each fix graded something the check SAID: a success
string, then a case count, then a roster list, then a refusal the module was
asked to produce. Every one of those is a thing a module can be mutated into
saying, and the fourth round's own entry point proved it: `negative_control()`
instead of `negative_control(check)` is one deleted token, and a module that
compares nothing then passes both the count floor and the control.

So this module stops asking a selftest to report anything. It records EVIDENCE,
and `tests/test_selftests.py` scores the evidence:

    the cases are the statements of `selftest()` that TYPECHECK as a case --
    the harness derives them from the module's own source, and a statement that
    compares an expression with itself, or asserts a constant, is not one;

    the evidence that they ran is the set of line numbers `Proof` watched
    execute inside that one function, written where the harness asked for it;

    the verdict is the harness intersecting the two and comparing the size
    against a floor it recorded. No number the module prints is read.

"A check that reports success without having checked" is not refused here, it
is unrepresentable: a module with nothing to compare has no cases for the
harness to find, and a module that skips its cases leaves no lines for it to
count. Both halves are computed OUTSIDE the module, from the module's source
and from the interpreter, so neither is a number a mutant can arrange to say.

The comparator is load-bearing twice over. `begin(check)` hands the harness the
name whose calls ARE this module's cases, so deleting that argument does not
skip a control -- it erases the module's evidence, and the floor is then unmet.
That is the fixed point the argument-shaped hole needed.

`done()` keeps the negative control the fourth round added, and moves it to the
END of the selftest, after the cases rather than before them: a module that
returns early now fails both ways round, having neither refused nor run.

Two boundaries, named rather than left for the next round to find:

- The trace is only installed when the harness asks for it by setting
  `$WQ_SELFTEST_EVIDENCE`, so an ordinary `--selftest` run is byte-identical to
  what it was, and costs what it did. An unasked-for run proves nothing, which
  is why the harness always asks.
- The evidence says a case RAN, not that its expectation still discriminates.
  A case rewritten to compare the code under test with a value the same code
  computed is refused at the source level (it does not typecheck as a case);
  one rewritten to compare against a different-but-equally-wrong constant is
  not, and cannot be by any static rule. That is mutation testing over the
  code under test, and it is the residue this design leaves.
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

NEGATIVE_CONTROL_ENV = "WQ_SELFTEST_NEGATIVE_CONTROL"
EVIDENCE_ENV = "WQ_SELFTEST_EVIDENCE"

# The label the checker is handed. It reaches the output through the failure
# path every checker in this package already has -- `raise AssertionError(
# f"{label}: ...")`, or `print(f"FAIL {name}: ...")` -- so the harness can tell
# a refusal from a crash for some unrelated reason.
MARKER = "negative control: a wrong answer must be refused"

# Wrong on purpose, and wrong in a way no real expectation is: two objects that
# no checker in this package ever compares, so a control can never collide with
# a case.
_GOT = "negative-control-got"
_WANT = "negative-control-want"


class Proof:
    """The lines of one `selftest()` that this process watched execute."""

    def __init__(self, check, frame) -> None:
        self._check = check
        self._code = frame.f_code
        self._seen: set[int] = set()
        self._path = os.environ.get(EVIDENCE_ENV)
        if self._path:
            frame.f_trace_lines = True
            frame.f_trace = self._line
            sys.settrace(self._elsewhere)

    def _line(self, frame, event, arg):
        if event == "line":
            self._seen.add(frame.f_lineno)
        return self._line

    def _elsewhere(self, frame, event, arg):
        """Everything that is not the selftest itself is traced by nothing."""
        return self._line if frame.f_code is self._code else None

    def done(self) -> None:
        """Hand over the evidence, then refuse a wrong answer when asked to.

        Called LAST, immediately before the selftest's own return: a module
        that never reaches it never refused anything, and a module that reaches
        it early has no lines to show for the cases it skipped.
        """
        if self._path:
            sys.settrace(None)
            Path(self._path).write_text(
                json.dumps({"file": self._code.co_filename,
                            "lines": sorted(self._seen)}), encoding="utf-8")
        if os.environ.get(NEGATIVE_CONTROL_ENV):
            if self._check is None:
                # The inline-assert modules have no helper to hand a wrong
                # answer to. What is left to prove is that `assert` still bites
                # here; under `python -O` this line is gone, the selftest exits
                # 0, and the harness reads that as the failure it is.
                assert _GOT == _WANT, MARKER
            else:
                self._check(MARKER, _GOT, _WANT)


def begin(check: Callable[[str, object, object], object] | None = None) -> Proof:
    """Open a selftest. Pass the comparator whose calls are this module's cases.

    Pass nothing only where the module asserts inline and has no comparator:
    the harness then reads its `assert` statements as the cases instead. Where
    there IS one, the argument is what makes those calls count, so dropping it
    costs the module every case it has.
    """
    return Proof(check, sys._getframe(1))
