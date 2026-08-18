"""Console entry points.

Every gate already exposes `main(argv)` and returns an exit code, because that
is what makes them testable from their own selftests. A console script is
called with no arguments, so each name here is the two-line adapter between the
two -- kept in one file so a reader can see all nine commands at once, and so
adding a gate is one line rather than a new `if __name__` block.
"""

from __future__ import annotations

import sys
from collections.abc import Callable


def _run(main: Callable[..., int]) -> int:
    return main(sys.argv[1:])


def resolve_note() -> int:
    from .resolve_note import main
    return _run(main)


def anchor_manifest() -> int:
    from .anchor_manifest import main
    return _run(main)


def spoken_vote() -> int:
    from .spoken_vote import main
    return _run(main)


def ocr_vote() -> int:
    from .ocr_vote import main
    return _run(main)


def demote_note() -> int:
    from .demote_note import main
    return _run(main)


def say_captions() -> int:
    from .say_captions import main
    return _run(main)


def frame_fixture() -> int:
    from .frame_fixture import main
    return _run(main)


def policy() -> int:
    from .wq_policy import main
    return _run(main)


def corpus_scan() -> int:
    from .wq_corpus_scan import main
    return _run(main)
