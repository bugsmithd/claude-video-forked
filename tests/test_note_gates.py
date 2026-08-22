"""The runner that finally reaches the per-note checks.

Three checks sat unreachable because the five gates take `[--flags] [notes...]`
and these take positional paths — a note AND its transcript. There was nowhere
to get the transcript from until `oracle:` became a field a gate could open.

What is pinned here is the half that decides whether this gate is worth having:
a note it cannot read is SKIPPED and named, never counted as passing, and the
ledger that excuses a frozen note ages like every other ledger in the package.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from watchquality import note_gates as ng
from watchquality import note_windows
from watchquality import resolve_note
from watchquality import wq_policy


BODY = "\n# t\n\nA body.\n"


@pytest.fixture
def corpus(tmp_path: Path):
    root = tmp_path.resolve()
    (root / "notes").mkdir()
    (root / "runs" / "VID").mkdir(parents=True)
    (root / "runs" / "VID" / "run.json").write_text(
        '{"segments": [{"start": 0.0, "end": 5.0, "text": "hello there"}]}\n',
        encoding="utf-8")
    ambient = dict(ng.UNGRADED_NOTES)
    ng.UNGRADED_NOTES.clear()
    try:
        yield root
    finally:
        ng.UNGRADED_NOTES.clear()
        ng.UNGRADED_NOTES.update(ambient)


def _note(root: Path, name: str, oracle: str, video: str = "VID") -> Path:
    p = root / "notes" / name
    p.write_text(f'---\nvideo_id: {video}\nduration: "10:00"\n'
                 f"status: distilled\noracle: {oracle}\n---\n{BODY}",
                 encoding="utf-8")
    return p


def _run(root: Path, *paths: Path) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ng.main(["--check", *[str(p) for p in paths]], root=root)
    return code, out.getvalue(), err.getvalue()


def test_a_note_it_cannot_read_is_named_and_not_counted_as_graded(corpus):
    """A skip is not a pass, and the census has to show which it was.

    Nineteen of twenty-five notes in this corpus name no rendering. A run that
    reported `0 defects` over them would be a clean bill of health for notes
    the gate never opened — the exact false report every gate here exists to
    refuse.
    """
    note = _note(corpus, "2026-08-20--empty--VID.md", "")
    code, _, err = _run(corpus, note)
    assert code == 0
    assert "not graded" in err and str(note.name) in err
    assert "0 of 1 note(s) graded" in err


def test_a_rendering_that_opens_is_graded_and_says_so(corpus):
    note = _note(corpus, "2026-08-20--good--VID.md", "runs/VID/run.json")
    code, _, err = _run(corpus, note)
    assert "1 of 1 note(s) graded" in err, err
    assert "not graded" not in err, err
    assert code in (0, 1)          # the verdict is the checks' business


def test_a_check_that_cannot_read_the_rendering_is_a_defect_not_a_pass(corpus,
                                                                       monkeypatch):
    """Exit 2 from a per-note check means nobody opened the file.

    Forwarding only what it printed would report the note as clean on a
    rendering that could not be parsed, which is `watch-audit`'s own rule one
    level down.

    Driven through a check that exits 2 in prose rather than through an
    unreadable file, because an unreadable file no longer reaches this far:
    the note is skipped before the checks run and the oracle gate reports it.
    The guard is still what is under test, and it is still reached through
    `main`.
    """
    def cannot_read(argv):
        print("could not open the rendering, and says so in prose")
        return 2

    monkeypatch.setattr(ng.note_coverage, "main", cannot_read)
    monkeypatch.setattr(ng.note_windows, "main", cannot_read)
    note = _note(corpus, "2026-08-20--good--VID.md", "runs/VID/run.json")
    code, out, err = _run(corpus, note)
    assert code == 1, (code, out, err)
    assert "exited 2" in out, out


def test_a_note_whose_oracle_is_not_a_transcript_is_not_graded(corpus):
    """The seam a ledger row was covering, at the layer that owns it.

    A file that opens is not a rendering. A run manifest sits beside the real
    transcript, under the same video id, and opens -- so the note gate used to
    be handed it, fail to read it, and report a note that "cannot be graded",
    while the oracle gate had already called the field fine.

    One finding, one owner: the oracle gate refuses a value no gate can read,
    and this gate skips the note and says which one it skipped. A skip is still
    not a pass, which is what the census line is for.
    """
    (corpus / "runs" / "VID" / "manifest.json").write_text(
        '{"video_id": "VID", "duration_seconds": 610}\n', encoding="utf-8")
    note = _note(corpus, "2026-08-20--manifest--VID.md", "runs/VID/manifest.json")

    code, out, err = _run(corpus, note)

    assert code == 0, (code, out, err)
    assert "0 of 1 note(s) graded" in err, err
    assert "not graded" in err and note.name in err, err
    assert out == "", out


def test_the_report_beside_a_defect_is_not_a_defect(corpus):
    """The window plan and the coverage table are output, not findings.

    A first version forwarded every line the checks printed, so a note with a
    clean five-window plan was reported as carrying five defects.
    """
    note = _note(corpus, "2026-08-20--good--VID.md", "runs/VID/run.json")
    _, out, _ = _run(corpus, note)
    for line in out.splitlines():
        body = line.split(": ", 1)[1] if ": " in line else line
        assert ng.RE_DEFECT_LINE.match(body), line


def test_a_check_that_fails_without_a_code_is_still_a_defect(corpus, monkeypatch):
    """The false clean bill of health, which is worse than not running at all.

    Three independent refuters found this the same way. A check that exits
    non-zero and says what it found in prose carries no defect code, the
    filter dropped every line it could not classify, and the note was then
    reported as graded and clean over a check that failed.
    """
    def codeless(argv):
        print("the rendering disagrees with the note, and says so in prose")
        return 1

    monkeypatch.setattr(ng.note_coverage, "main", codeless)
    monkeypatch.setattr(ng.note_windows, "main", codeless)
    note = _note(corpus, "2026-08-20--good--VID.md", "runs/VID/run.json")
    code, out, err = _run(corpus, note)
    assert code == 1, (code, out, err)
    assert "0 defect(s)" not in err, err
    assert "exited 1" in out, out


def test_the_gates_own_finding_is_shaped_like_every_other_finding(corpus, monkeypatch):
    """A gate printing a defect its own classifier rejects has two rules.

    The rescue line invented for a check that failed without naming a code is
    itself a finding, and a reader greps this package's output for the one
    shape everything else in it carries.
    """
    monkeypatch.setattr(ng.note_coverage, "main", lambda argv: 1)
    monkeypatch.setattr(ng.note_windows, "main", lambda argv: 1)
    note = _note(corpus, "2026-08-20--good--VID.md", "runs/VID/run.json")
    _, out, _ = _run(corpus, note)
    bodies = [ln.split(": ", 1)[1] for ln in out.splitlines() if ": " in ln]
    assert bodies, out
    for body in bodies:
        assert ng.RE_DEFECT_LINE.match(body), body


def test_transcript_text_that_looks_like_a_code_is_not_a_defect(corpus, monkeypatch):
    """The other direction of the same guard, and why one regex cannot do both.

    `note_windows` ends each row of its window plan with the first sixty
    characters of that window's opening transcript text. A talk about
    e-commerce spells the shape of a defect code exactly, and a pattern that
    matches anywhere in the line counted the report row as a finding.
    """
    ecom = corpus / "runs" / "VID" / "ecom.json"
    ecom.write_text(json.dumps({"segments": [
        {"start": float(i * 20), "end": float(i * 20 + 19),
         "text": ("E-COMMERCE growth and E-MAIL funnels" if i == 0
                  else f"segment number {i} says something ordinary")}
        for i in range(5)]}) + "\n", encoding="utf-8")

    # The fixture only means anything if the report row really carries the
    # word, and carries it as a report row rather than as a finding.
    buf = io.StringIO()
    with redirect_stdout(buf):
        note_windows.main([str(ecom)])
    rows = [ln for ln in buf.getvalue().splitlines() if "E-COMMERCE" in ln]
    assert rows, buf.getvalue()
    assert not rows[0].startswith("["), rows[0]

    monkeypatch.setattr(ng.note_coverage, "main", lambda argv: 0)
    note = _note(corpus, "2026-08-20--ecom--VID.md", "runs/VID/ecom.json")
    code, out, err = _run(corpus, note)
    assert "E-COMMERCE" not in out, out
    # The row's declared exit. A note whose only output is a report is clean.
    assert code == 0, (code, out, err)
    assert "0 defect(s)" in err, err


def test_a_dated_row_excuses_a_frozen_note_and_cannot_reach_a_newer_one(corpus):
    """The ledger, and the half of it that keeps it shrinking.

    A frozen note cannot be repaired, so a real finding in one is permanent and
    reporting it every run teaches the reader to skip the output. A row dated
    today may not excuse a note filed tomorrow.
    """
    old = _note(corpus, "2026-08-20--old--VID.md", "")
    ng.UNGRADED_NOTES["notes/2026-08-20--old--VID.md"] = "2026-08-21 note frozen"
    _, _, err = _run(corpus, old)
    assert "excused by the ledger" in err, err

    later = _note(corpus, "2026-08-22--later--VID.md", "")
    ng.UNGRADED_NOTES["notes/2026-08-22--later--VID.md"] = "2026-08-21 note frozen"
    _, _, err = _run(corpus, later)
    assert "excused by the ledger" not in err, err
    assert "names no rendering" in err, err

    # Refusing the row is not a formality: it exposes the note to the grading
    # the row was suppressing, and the finding then gets reported. The note
    # above is skipped for an unrelated reason, so it cannot show that.
    # The rendering is READABLE and long, and the note carries none of it. An
    # unreadable one would be skipped before the checks run, which would prove
    # the row was refused and nothing about what refusing it exposed.
    graded = _note(corpus, "2026-08-22--graded--VID.md", "runs/VID/long.json")
    (corpus / "runs" / "VID" / "long.json").write_text(json.dumps({"segments": [
        {"start": i * 30.0, "end": i * 30.0 + 29.0,
         "text": f"segment {i} said something about margins and hiring nobody "
                 f"wrote down anywhere in the note at all"}
        for i in range(20)]}), encoding="utf-8")
    ng.UNGRADED_NOTES["notes/2026-08-22--graded--VID.md"] = "2026-08-21 frozen"
    code, out, err = _run(corpus, graded)
    assert "excused by the ledger" not in err, err
    assert code == 1, (code, out, err)


def test_a_row_excuses_the_note_it_names_and_not_its_namesake(corpus):
    """A bare filename is not an identity; two directories can hold one name.

    The fallback lookup let one dated row excuse every note sharing a
    filename, which turns a one-note exemption into a pattern nobody wrote
    and nobody dated.
    """
    (corpus / "notes" / "sub").mkdir()
    named = _note(corpus, "2026-08-20--twin--VID.md", "")
    twin = corpus / "notes" / "sub" / "2026-08-20--twin--VID.md"
    twin.write_text(named.read_text(encoding="utf-8"), encoding="utf-8")

    # A row keyed by bare name reached both, so an exemption written for one
    # note silently covered a note in another directory that happened to
    # share its filename.
    ng.UNGRADED_NOTES["2026-08-20--twin--VID.md"] = "2026-08-21 note frozen"
    _, _, err = _run(corpus, named)
    assert "excused by the ledger" not in err, err
    _, _, err = _run(corpus, twin)
    assert "excused by the ledger" not in err, err

    ng.UNGRADED_NOTES.clear()
    ng.UNGRADED_NOTES["notes/2026-08-20--twin--VID.md"] = "2026-08-21 note frozen"
    _, _, err = _run(corpus, named)
    assert "excused by the ledger" in err, err
    _, _, err = _run(corpus, twin)
    assert "excused by the ledger" not in err, err


def test_a_ledger_row_that_names_no_note_is_reported_stale(corpus, monkeypatch):
    """A dead row nobody hears about is a bypass with a date on it.

    Every table in this policy promises to shrink, and a row can only shrink
    if somebody learns it is dead. The other four tables are censused; this
    one was not, so a row whose note had been renamed or removed sat there
    excusing nothing, permanently and invisibly.
    """
    _note(corpus, "2026-08-20--real--VID.md", "")
    monkeypatch.setattr(resolve_note, "UNGRADED_NOTES",
                        {"notes/2026-01-01--ghost--GONE.md": "2026-08-21 gone"})
    stale = resolve_note.stale_exemptions(corpus)
    assert any("ghost" in row for row in stale), stale
    assert any("ungraded_notes" in row for row in stale), stale


def test_the_ledger_is_empty_without_a_policy():
    """A missing policy may not invent an exemption."""
    assert wq_policy.Policy({}, None).ungraded_notes() == {}


def test_a_finding_that_reads_only_the_note_survives_a_missing_rendering(corpus):
    """V2 finding V2-6 — for these two, a skip was exactly a pass.

    This gate skips a note whose oracle it cannot open, and `note_coverage` is
    the only owner of `E-COV-ROWSHAPE` and `E-COV-STAMP`. Both read the NOTE
    and never the rendering, so they were unreachable for every skipped note:
    the corpus reported zero misshapen rows while 21 skipped notes carried 105
    of them, and the worst of those notes exits 1 the moment a rendering is
    handed to it by hand.

    The census does name the skipped notes, so this was never silent. But
    "skipping is not passing" is only true of a finding that needs the thing
    that is missing.

    Would fail if: `check_note` returns on the skip path without asking the
    note-only checks.
    """
    note = corpus / "notes" / "2026-08-20--m--VID.md"
    note.write_text(
        '---\nvideo_id: VID\nduration: "22:00"\nstatus: distilled\n'
        "oracle: runs/VID/gone.json\n---\n\n# t\n\n"
        "- `[59:99]` `SPOKEN` — a stamp no clock can say.\n", encoding="utf-8")

    lines, why = ng.check_note(note, corpus)

    assert why and "opens nothing" in why, why
    assert any("E-COV-STAMP" in ln for ln in lines), lines

    # ...and it reaches a reader, as a named line under the skip rather than as
    # a defect. A skipped note is one this gate could not grade; its rows are a
    # real and permanent finding, and reddening a frozen corpus over one nobody
    # can repair teaches its reader to stop reading the output.
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        code = ng.main([str(note)], root=corpus)
    printed = err.getvalue()
    assert code == 0, printed
    assert "E-COV-STAMP" in printed, printed
    assert "1 finding(s) in notes this gate could not grade" in printed, printed


def test_a_note_the_gate_cannot_read_at_all_reports_nothing_about_its_rows(corpus):
    """The neighbour: the note-only checks need the NOTE.

    A note that cannot be read, or carries no frontmatter, is skipped whole.
    Reporting a row shape from a file nobody could parse would be inventing a
    finding, which is the opposite failure to the one above.
    """
    note = corpus / "notes" / "2026-08-20--unreadable--VID.md"
    note.write_bytes(b"\xff\xfe\x00not a note at all")

    lines, why = ng.check_note(note, corpus)

    assert why, why
    assert lines == [], lines


def test_a_note_is_not_graded_against_another_videos_transcript(tmp_path):
    """V2 finding V2-10 — two gates, two different questions about one field.

    `check_oracle` asks whether a value is THIS video's rendering and whether a
    gate can read it. `rendering_for` asked only the second. So a note whose
    oracle is an absolute path to another video's transcript was graded,
    counted in the census as graded against its own rendering, and scored
    against the wrong recording -- producing arithmetic defects about a talk it
    was not written from. `E-ORACLE-UNRELATED` still fired from the other gate,
    so nothing was silent; the note was simply graded anyway.

    Would fail if: `rendering_for` stops asking whether the file it found
    belongs to the note in front of it.
    """
    root = tmp_path.resolve()
    (root / "notes").mkdir()
    segments = json.dumps({"segments": [
        {"start": float(i) * 30, "end": float(i) * 30 + 30,
         "text": f"sentence {i} about widgets"} for i in range(44)]})
    for vid in ("VID", "OTHER"):
        (root / "runs" / vid).mkdir(parents=True)
        (root / "runs" / vid / "run.json").write_text(segments, encoding="utf-8")

    def note_naming(oracle: str) -> Path:
        note = root / "notes" / "2026-08-20--n--VID.md"
        note.write_text(
            f'---\nvideo_id: VID\nduration: "22:00"\nstatus: distilled\n'
            f"oracle: {oracle}\n---\n\n# t\n\nA body.\n", encoding="utf-8")
        return note

    # Its own run, named absolutely: graded, which is the behaviour to keep.
    mine = ng.rendering_for(note_naming(str(root / "runs/VID/run.json")), root)
    assert mine[0] is not None and "VID" in str(mine[0]), mine

    # Another video's, equally readable: not graded, and the census says why.
    theirs = ng.rendering_for(
        note_naming(str(root / "runs/OTHER/run.json")), root)
    assert theirs[0] is None, theirs
    assert "VID" in theirs[1], theirs[1]

    # round-14 F2 — and the SAME file reached through `..`. The relatedness
    # test asks whether the video id is a whole component of the path, and its
    # docstring says it is asked of the normalised path. It was not: the value
    # arrived straight from the resolver, so `runs/VID/../OTHER/run.json`
    # carried `VID` as a component and was graded as this note's own.
    dotted = ng.rendering_for(
        note_naming(str(root / "runs/VID/../OTHER/run.json")), root)
    assert dotted[0] is None, dotted
    assert "VID" in dotted[1], dotted[1]


def test_a_finding_reaches_main_with_the_exit_code_and_shape_it_declares(corpus):
    """V1 Q2 and Q3 — the rules declared `exit = 1` and a greppable shape.

    Every case they cited called `_run` with a SUBSTITUTED function, asserted
    the returned tuple, and never reached `main`. So the exit code the table
    declares was pinned by nothing, and the docstring's claim about a single
    shape a reader can grep for was checked by cases that stripped the note
    path off the front of the line before matching -- which proves the line
    matches its siblings, not that it matches what `watch-audit` prints. A
    reader grepping that output for the declared shape matched zero of this
    gate's lines.

    Nothing is substituted here: a real note, a real rendering on disk, the
    entry point a caller uses, and the shape asserted whole.

    Would fail if: `main` returns 0 having printed a defect, or the note path
    stops prefixing the line.
    """
    (corpus / "runs" / "VID" / "real.json").write_text(json.dumps(
        {"segments": [{"start": i * 30.0, "end": i * 30.0 + 30.0,
                       "text": f"segment {i} about widgets and gears"}
                      for i in range(40)]}), encoding="utf-8")
    note = corpus / "notes" / "2026-08-20--real--VID.md"
    note.write_text(
        '---\nvideo_id: VID\nduration: "20:00"\nstatus: distilled\n'
        "oracle: runs/VID/real.json\n---\n" + BODY, encoding="utf-8")

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ng.main([str(note)], root=corpus)

    printed = out.getvalue() + err.getvalue()
    said = [ln for ln in printed.splitlines() if " E-" in ln]
    assert code == 1, printed
    assert said, printed
    # And every line carries the shape the package demands of a finding, from
    # the entry point rather than from a helper: `<path>: [MM:SS] E-CODE ...`.
    # The cases this row cited stripped the note-path prefix before matching,
    # so they proved the line matched its siblings rather than the shape a
    # reader greps `watch-audit` output for -- and that grep matched zero of
    # this gate's lines (V1 Q3).
    import re
    shape = re.compile(r"^\S+\.md: \[\d{2}:\d{2}\] E-[A-Z0-9-]+ \S")
    assert all(shape.match(ln) for ln in said), said


def test_the_gate_declares_itself_and_runs_after_the_note_gate():
    """The roster is derived from `GATE_FLAGS`, and order from the imports.

    This gate reads a note's declared rendering, and that declaration is what
    `resolve_note` checks, so it may not run before it.
    """
    from watchquality import audit
    names = [name for name, _ in audit.GATES]
    assert "note_gates" in names, names
    assert names.index("note_gates") > names.index("resolve_note"), names
    assert dict(audit.GATES)["note_gates"] == list(ng.GATE_FLAGS)
