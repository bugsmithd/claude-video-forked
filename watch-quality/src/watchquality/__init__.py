"""watch-quality -- evidence gates for notes made from video.

Twelve commands that refuse to let a note claim more than its evidence supports.
They grade what a `/watch` run produced: that the transcript underneath it did
not degenerate mid-decode, that every timestamp a note cites exists in the run,
that a claim tagged ON-SCREEN was actually on screen, that a declared review
lane really emitted a report, and that a citation still says what it said when
it was resolved. One of them asks the opposite question -- what the recording
said that the note never carried -- because a note is also wrong by omission.

THE PACKAGE CARRIES NO CORPUS. Every path, every exemption and every root
arrives from `watch-quality.toml` beside the notes being graded, read only by
`wq_policy`. With no policy file the exemption tables are EMPTY, because a
missing policy must never be able to invent an exemption. `wq_corpus_scan`
enforces that rule mechanically over this source tree.

Each module is also a command; see `[project.scripts]` in pyproject.toml.
"""

__version__ = "0.5.0"

__all__ = ["__version__"]
