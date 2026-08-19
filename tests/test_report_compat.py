"""The old report, byte for byte, on a build that can also make notes.

`--make-note` was added to a script whose stdout other people already read.
The mode's whole promise is that it is off unless asked for, and the tests in
test_make_note.py check that promise one observable at a time: no `## Note
mode` heading, no run.json, the ordinary frame width. Those are assertions
about the lines somebody thought to name. A report is not a set of named
lines; it is a byte string, and a stray space or a reordered clause is a
behaviour change to whatever parses it.

So this file asserts the whole string instead. It does not compare against a
committed golden, on purpose. A golden generated on this machine bakes in this
machine's ffmpeg, and the first version bump would fail the test for a reason
that has nothing to do with the change under test -- which is how a compat
test gets deleted. Instead it extracts the PRE-CHANGE scripts out of git,
runs both builds against the same clip in the same session, and diffs. Any
ffmpeg quirk lands in both sides and cancels.

BASE is the commit before note mode. Both sides of every comparison here run
the same ffmpeg, so a difference is a difference in the Python.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "watch" / "scripts"
# The parent of 294e4af "Keep the run when the output is a note, and give the
# gates one door" -- the commit that added --make-note.
BASE = "2f7923f"

RE_WORK_DIR = re.compile(r"_Work dir: `([^`]+)`")


def _archive_base(dest: Path) -> Path:
    """Extract `skills/watch/scripts` at BASE. Returns the scripts dir.

    `git archive` and not `git worktree`: reading an old tree must not leave
    bookkeeping behind in the repository the tests are running against.
    """
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH, so the pre-change build cannot be read")
    proc = subprocess.run(
        ["git", "archive", BASE, "skills/watch/scripts"],
        cwd=REPO, capture_output=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"{BASE} is not reachable from this checkout "
                    f"({proc.stderr.decode('utf-8', 'replace').strip()})")
    dest.mkdir(parents=True, exist_ok=True)
    untar = subprocess.run(["tar", "-x", "-C", str(dest)], input=proc.stdout,
                           capture_output=True)
    assert untar.returncode == 0, untar.stderr
    return dest / "skills" / "watch" / "scripts"


@pytest.fixture(scope="session")
def base_scripts(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _archive_base(tmp_path_factory.mktemp("base-build"))


def _report(script_dir: Path, clip: Path, *args: str) -> str:
    env = dict(os.environ)
    env.pop("WATCH_DETAIL", None)
    proc = subprocess.run(
        [sys.executable, str(script_dir / "watch.py"), str(clip), "--no-whisper", *args],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _normalise(report: str, script_dir: Path) -> str:
    """Blank the three paths that MUST differ between two builds.

    The work dir is a fresh temp dir per run. The scripts dir differs because
    one build was unpacked out of git. Nothing else in the report is allowed
    to move, and in particular every frame filename, every timestamp and every
    count survives this untouched.
    """
    work = RE_WORK_DIR.search(report)
    assert work, "every report ends with its work dir; this one did not"
    return (report
            .replace(work.group(1), "<WORK>")
            .replace(str(script_dir), "<SCRIPTS>"))


def _compare(base_scripts: Path, clip: Path, *args: str) -> None:
    old = _normalise(_report(base_scripts, clip, *args), base_scripts)
    new = _normalise(_report(SCRIPTS, clip, *args), SCRIPTS)
    assert new == old


# --- the report itself -------------------------------------------------------

def test_the_default_report_did_not_move(base_scripts: Path, cut_clip: Path):
    _compare(base_scripts, cut_clip)


def test_the_dedup_report_did_not_move(base_scripts: Path, static_clip: Path):
    """The one that could plausibly have broken.

    Note mode needed the SECONDS the dedup pass collapsed, not just how many,
    so `dedupe_perceptual` grew an out-parameter and all three engines started
    putting `deduped_seconds` in their meta. A static clip is where dedup
    actually fires, so this is the case where a signature change would show up
    in the old report's `N near-duplicates dropped` clause.
    """
    _compare(base_scripts, static_clip)


def test_the_transcript_detail_report_did_not_move(base_scripts: Path, cut_clip: Path):
    """`--detail transcript` skips frames, so it takes the other branch of
    every conditional in the report block."""
    _compare(base_scripts, cut_clip, "--detail", "transcript")


def test_the_cue_frame_report_did_not_move(base_scripts: Path, cut_clip: Path):
    _compare(base_scripts, cut_clip, "--timestamps", "1,3")


# --- the normaliser is not allowed to hide a real difference -----------------

def test_the_comparison_can_actually_fail(base_scripts: Path, cut_clip: Path):
    """A compat test that cannot fail is decoration.

    Perturb one line of the OLD build and confirm the comparison catches it.
    Without this, a normaliser that flattened too much -- or a `_report` that
    silently returned "" -- would pass every test above forever.
    """
    watch = base_scripts / "watch.py"
    original = watch.read_text(encoding="utf-8")
    try:
        watch.write_text(original.replace('"- **Detail:** ', '"- **detail:** ', 1),
                         encoding="utf-8")
        old = _normalise(_report(base_scripts, cut_clip), base_scripts)
        new = _normalise(_report(SCRIPTS, cut_clip), SCRIPTS)
        assert new != old
    finally:
        watch.write_text(original, encoding="utf-8")


def test_the_resolution_line_is_the_old_one(base_scripts: Path, cut_clip: Path):
    """`--resolution` lost its argparse default to a None sentinel so that
    note mode could raise the floor without overriding an explicit width. The
    flag's OWN default has to come out the other side unchanged, and it is a
    printed number, so a regression is visible in the report."""
    new = _report(SCRIPTS, cut_clip)
    assert "max 768px wide" in new
    assert "max 768px wide" in _report(base_scripts, cut_clip)
