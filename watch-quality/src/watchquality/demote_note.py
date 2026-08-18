#!/usr/bin/env python3
"""Demotion counter and renderer for watch-quality notes -- slice 3, and since.

docs/note-quality-plan-round-2.md, branch "2. Inverted defaults, rendered not
blocked". The eventual tool is a RENDERER, not a gate: it downgrades a weak
claim in place instead of refusing the commit, because a blocking hook does not
survive a one-person repo with no CI. This file is the first step the plan asks
for. `--dry-run` still prints per-note counts and nothing else, and that
counting mode never edits a note and always exits 0. `--apply` DOES rewrite the
notes in place -- it is the step `README.md` puts in the note-writing routine --
and `--diff` shows what it would do. The docstring claimed the counting-only
contract for the whole file three slices after `--apply` shipped.

What it counts, and what the renderer would later do with each:

  untagged     Claims bullet with no `ON-SCREEN` / `SPOKEN` / `INFERRED` class.
               Renderer injects `INFERRED` plus an auto-demoted comment.
  speculation  Objection block in "Where it breaks" with no falsifier line.
               Renderer moves it verbatim under a new `## Speculation` heading.
  orphans      A timestamp used outside Claims that appears nowhere in Claims.
               Renderer marks it `ORPHAN`. This is R4, unfixed since round 1.
  lanes        Review lanes the note DECLARES in frontmatter `reviews:` that
               have no report on disk under notes/reviews/<video_id>/.

The plan also wants "a review lane declared in the run header with no emitted
verdict" to render FAIL. Slice 13 gave that a convention -- `reviews: [id, ...]`
-- and the gate lives in resolve_note.check_lanes as E-LANE-MISSING and its
three siblings. This file only COUNTS, and it counts through the same two
primitives (`lane_ids`, `lane_matches`) so the integrity line and the gate
cannot report different numbers for the same note. Before that convention only
the weaker cited-file check existed, and it could not see a lane that died,
because a lane that died is a lane nobody cited.

Usage:
    scripts/demote_note.py --dry-run [PATH ...]   # default: notes/
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .resolve_note import (DEMOTED_MARK, INTEGRITY_PREFIX,  # noqa: E402
                          LOST_REVIEWS, ORPHAN_MARK, RE_VIDEO_ID,
                          SPECULATION_HEADING, collect, lane_ids, lane_matches,
                          lane_reports, split_frontmatter)
from .wq_policy import load as load_policy  # noqa: E402

POLICY = load_policy()

CLAIMS_SECTION = "Claims"
OBJECTION_SECTION = "Where it breaks"
EVIDENCE_CLASSES = ("ON-SCREEN", "SPOKEN", "INFERRED")
# R4 is about anchors carrying an ARGUMENT that Claims never made. Provenance
# sections legitimately cite frames that were read but never claimed, so they are
# out of scope by default; `--all-sections` puts them back. Validated against the
# densest note, independently verified on 2026-08-05 as having zero orphans in
# "Where it breaks" and "What is usable" -- all nine of its raw hits are Run notes.
META_SECTIONS = ("", "Run notes", "Action")

RE_HEADING = re.compile(r"^##\s+(.*?)\s*$")
RE_BULLET = re.compile(r"^\s*-\s+")
RE_ANCHOR = re.compile(r"`\[(\d{1,2}:\d{2}(?::\d{2})?)\]`")
# The repo writes *Falsifier:*; the plan's sketch says `refuted by:`. Accept both.
RE_FALSIFIER = re.compile(r"(\*?Falsifier:\*?|refuted by:)", re.IGNORECASE)
RE_REPORT = re.compile(r"`([\w./-]+\.md)`")

SPECULATION_SECTION = SPECULATION_HEADING.removeprefix("## ")
RE_ORPHANED = re.compile(RE_ANCHOR.pattern + r"\s+" + re.escape(ORPHAN_MARK))


def sections(body: str, start_line: int) -> dict[str, list[tuple[int, str]]]:
    """Split a note body into `## ` sections of (line number, text)."""
    out: dict[str, list[tuple[int, str]]] = {}
    current = ""
    for idx, line in enumerate(body.split("\n")):
        m = RE_HEADING.match(line)
        if m:
            current = m.group(1)
            out.setdefault(current, [])
            continue
        out.setdefault(current, []).append((start_line + idx, line))
    return out


def claim_bullets(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Bullets, with their continuation lines folded in."""
    bullets: list[tuple[int, str]] = []
    for ln, text in lines:
        if RE_BULLET.match(text):
            bullets.append((ln, text))
        elif bullets and text.strip():
            ln0, prev = bullets[-1]
            bullets[-1] = (ln0, prev + " " + text.strip())
    return bullets


def block_spans(lines: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """One objection per block, keeping the raw lines so a block can be moved
    verbatim. Paragraphs split on blank lines, and a top-level bullet always
    starts its own block -- notes write objections both ways."""
    out: list[list[tuple[int, str]]] = []
    buf: list[tuple[int, str]] = []
    for ln, text in lines:
        stripped = text.strip()
        if stripped and RE_BULLET.match(text) and not text.startswith((" ", "\t")) and buf:
            out.append(buf)
            buf = []
        if stripped:
            buf.append((ln, text))
        elif buf:
            out.append(buf)
            buf = []
    if buf:
        out.append(buf)
    return out


def blocks(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """block_spans folded down to (start line, joined text) for counting."""
    return [(span[0][0], " ".join(t.strip() for _, t in span))
            for span in block_spans(lines)]


def anchors(text: str) -> set[str]:
    return set(RE_ANCHOR.findall(text))


def count_note(path: Path, root: Path, all_sections: bool = False) -> dict:
    rel = path.relative_to(root) if path.is_relative_to(root) else path
    text = path.read_text(encoding="utf-8")
    split = split_frontmatter(text)
    if split is None:
        return {"file": str(rel), "error": "no frontmatter"}
    frontmatter, body = split
    secs = sections(body, len(frontmatter.split("\n")) + 3)

    bullets = claim_bullets(secs.get(CLAIMS_SECTION, []))
    untagged = [ln for ln, t in bullets
                if not any(f"`{c}`" in t for c in EVIDENCE_CLASSES)]

    objections = blocks(secs.get(OBJECTION_SECTION, []))
    speculation = [ln for ln, t in objections if not RE_FALSIFIER.search(t)]

    claim_anchors = anchors(" ".join(t for _, t in bullets))
    orphans: set[str] = set()
    for name, lines in secs.items():
        if name == CLAIMS_SECTION or (not all_sections and name in META_SECTIONS):
            continue
        orphans |= anchors(" ".join(t for _, t in lines)) - claim_anchors

    # ONE definition of "did lane X emit a report", not three. This counter used
    # to run its own cited-filename scan while `integrity_line` used the
    # declared-lane rule and `resolve_note.check_lanes` used a third; one note
    # answered 0, 2 and clean at the same time (slice-13 correctness lane).
    _, lanes_missing = review_reports(body, frontmatter, root)

    return {
        "file": str(rel),
        "claims": len(bullets),
        "untagged": len(untagged),
        "objections": len(objections),
        "speculation": len(speculation),
        "orphans": len(orphans),
        "lanes_missing": len(lanes_missing),
        "demotions": len(untagged) + len(speculation) + len(orphans)
                     + len(lanes_missing),
        "orphan_list": sorted(orphans),
    }


def ordered_sections(body: str) -> list[tuple[str, str | None, list[str]]]:
    """(name, heading line, body lines) in document order; the preamble first."""
    out: list[tuple[str, str | None, list[str]]] = [("", None, [])]
    for line in body.split("\n"):
        m = RE_HEADING.match(line)
        if m:
            out.append((m.group(1), line, []))
        else:
            out[-1][2].append(line)
    return out


def mark_orphans(lines: list[str], orphans: set[str]) -> tuple[list[str], int]:
    """Append `ORPHAN` after each unclaimed timestamp, and only once."""
    marked = 0
    out = []
    for line in lines:
        def sub(m, line=line):
            nonlocal marked
            already = line[m.end():].startswith(" " + ORPHAN_MARK)
            if m.group(1) not in orphans or already:
                return m.group(0)
            marked += 1
            return m.group(0) + " " + ORPHAN_MARK
        out.append(RE_ANCHOR.sub(sub, line))
    return out, marked


def render(text: str, root: Path) -> str:
    """Rewrite a note in place: demote, never refuse.

    Idempotent by construction -- every action is skipped when its marker is
    already present, so running twice is a no-op. That matters more than it
    sounds: a renderer that re-marks on every run makes its own output unusable
    as input, and this one runs after every write.
    """
    split = split_frontmatter(text)
    if split is None:
        return text
    frontmatter, body = split
    head = text[:len(text) - len(body)]

    secs = sections(body, 1)
    bullets = claim_bullets(secs.get(CLAIMS_SECTION, []))
    claim_anchors = anchors(" ".join(t for _, t in bullets))

    rendered: list[tuple[str, str | None, list[str]]] = []
    for name, heading, lines in ordered_sections(body):
        if name == CLAIMS_SECTION:
            lines = [tag_claim(l) for l in lines]
        elif name not in META_SECTIONS:
            orphans = anchors("\n".join(lines)) - claim_anchors
            lines, _ = mark_orphans(lines, orphans)
        rendered.append((name, heading, lines))
        if name == OBJECTION_SECTION:
            keep, moved = partition_objections(lines)
            rendered[-1] = (name, heading, keep)
            if moved:
                rendered.append((SPECULATION_SECTION,
                                 f"## {SPECULATION_SECTION}", moved))

    out_body = "\n".join(
        "\n".join(([heading] if heading else []) + lines)
        for _, heading, lines in rendered)
    return head + integrity_line(out_body, frontmatter, root)


def tag_claim(line: str) -> str:
    if not RE_BULLET.match(line) or any(f"`{c}`" in line for c in EVIDENCE_CLASSES):
        return line
    if not RE_ANCHOR.search(line):
        return line
    last = list(RE_ANCHOR.finditer(line))[-1]
    return (line[:last.end()] + " `INFERRED`" + line[last.end():]
            + " " + DEMOTED_MARK)


def tidy(lines: list[str]) -> list[str]:
    """A section body with one blank line between blocks and none at the edges.

    Removing a block leaves its blank-line neighbours behind, so without this
    every render adds vertical space and the output stops being a fixed point.
    """
    out: list[str] = []
    for text in lines:
        if text.strip() or (out and out[-1].strip()):
            out.append(text)
    while out and not out[-1].strip():
        out.pop()
    return ([""] + out + [""]) if out else [""]


def partition_objections(lines: list[str]) -> tuple[list[str], list[str]]:
    """Objections with no falsifier move out, verbatim."""
    numbered = list(enumerate(lines))
    keep_idx: set[int] = set(range(len(lines)))
    moved: list[str] = []
    for span in block_spans(numbered):
        text = " ".join(t for _, t in span)
        if RE_FALSIFIER.search(text):
            continue
        for i, t in span:
            keep_idx.discard(i)
            moved.append(t)
        moved.append("")
    keep = [t for i, t in numbered if i in keep_idx]
    return tidy(keep), (tidy(moved) if moved else [])


def integrity_line(body: str, frontmatter: str, root: Path) -> str:
    """One blockquote above the thesis, counted from the RENDERED bytes.

    Deriving it from the output rather than from the input is what keeps it
    true after a second run: the demotions are visible as markers, so they
    cannot be silently un-counted.
    """
    secs = sections(body, 1)
    claims = len(claim_bullets(secs.get(CLAIMS_SECTION, [])))
    demoted = body.count(DEMOTED_MARK)
    objections = len(blocks(secs.get(OBJECTION_SECTION, [])))
    spec = len(blocks(secs.get(SPECULATION_SECTION, [])))
    orphans = len(set(RE_ORPHANED.findall(body)))
    cited, missing = review_reports(body, frontmatter, root)
    # `0/2 on disk` reads as two lanes that produced nothing, which is the exact
    # thing the roll-call exists to distinguish from a lane that died. Say which
    # of the missing ones are dated in LOST_REVIEWS, so the note's own face
    # carries the same fact the gate does.
    m = RE_VIDEO_ID.search(frontmatter)
    lost = sum(1 for lane in missing
               if lane in LOST_REVIEWS.get(m.group(1) if m else "", {}))
    line = (f"> integrity: {claims} claims · {demoted} auto-demoted · "
            f"{objections} falsifiable / {spec} speculation · "
            f"{orphans} orphan anchors · "
            f"reviews {cited - len(missing)}/{cited} on disk"
            + (f", {lost} lost" if lost else ""))
    lines = [t for t in body.split("\n") if not t.startswith(INTEGRITY_PREFIX)]
    out: list[str] = []
    i, placed = 0, False
    while i < len(lines):
        out.append(lines[i])
        if not placed and lines[i].startswith("# "):
            placed = True
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1  # blanks are re-emitted below, never accumulated
            out.extend(["", line, ""])
            continue
        i += 1
    if placed:
        return "\n".join(out)
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join([line, ""] + lines)


def review_reports(body: str, frontmatter: str, root: Path) -> tuple[int, list[str]]:
    """(declared lanes, the declared lanes with no report on disk).

    Declared, not cited. A note that cites four reports it never ran would
    otherwise print `reviews 4/4`, and a lane that died would print nothing at
    all. Notes written before the `reviews:` convention fall back to the cited
    filenames so their integrity lines keep meaning what they said.
    """
    m = RE_VIDEO_ID.search(frontmatter)
    ids, err = lane_ids(frontmatter)
    if ids is not None and not err and m:
        reports = lane_reports(root, m.group(1))
        missing = [i for i in ids if not lane_matches(reports, i)]
        return len(ids), sorted(missing)
    names = {Path(c).name for c in RE_REPORT.findall(body) if "review" in Path(c).name}
    if not m:
        return len(names), sorted(names)
    reviews = root / POLICY.reviews_dir() / m.group(1)
    return len(names), sorted(n for n in names if not (reviews / n).is_file())


def selftest(root: Path) -> int:
    """A counter nobody has seen fire is not evidence of a clean repo."""
    import tempfile
    head = ('---\ntitle: t\nvideo_id: FIXTURE\nduration: "1:00"\n---\n\n'
            "## Claims\n\n"
            "- `[00:10]` `SPOKEN` — tagged claim.\n"
            "- `[00:20]` — untagged claim, would be demoted to INFERRED.\n\n"
            "## Where it breaks\n\n"
            "**Falsifiable objection.** Body at `[00:10]`. *Falsifier:* a thing.\n\n"
            "**Bare assertion.** No falsifier, and it anchors `[09:99]`.\n\n"
            "- A bulleted objection with no falsifier either.\n")
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "fixture.md"
        f.write_text(head, encoding="utf-8")
        r = count_note(f, root)
    assert r["claims"] == 2, r
    assert r["untagged"] == 1, r
    assert r["objections"] == 3, r
    assert r["speculation"] == 2, r
    assert r["orphans"] == 1 and r["orphan_list"] == ["09:99"], r
    cases = 5

    once = render(head, root)
    assert "`[00:20]` `INFERRED`" in once and DEMOTED_MARK in once, once
    cases += 1
    assert f"## {SPECULATION_SECTION}" in once, once
    cases += 1
    assert "Bare assertion" in once.split(f"## {SPECULATION_SECTION}")[1], once
    cases += 1
    assert "Falsifiable objection" in once.split(f"## {SPECULATION_SECTION}")[0], once
    cases += 1
    assert f"`[09:99]` {ORPHAN_MARK}" in once, once
    cases += 1
    assert once.count(INTEGRITY_PREFIX) == 1, once
    cases += 1
    # Idempotent: the renderer runs after every write, so its own output must be
    # a fixed point or the markers compound.
    assert render(once, root) == once, "render is not idempotent"
    cases += 1
    # A note with nothing to demote must come back byte-identical apart from the
    # integrity line, which is the only thing the renderer ever adds unasked.
    clean = ('---\ntitle: t\nvideo_id: F\nduration: "1:00"\n---\n\n# T\n\n'
             "## Claims\n\n- `[00:10]` `SPOKEN` — c.\n\n"
             "## Where it breaks\n\n**O.** *Falsifier:* x `[00:10]`.\n")
    got = render(clean, root)
    assert got.replace("\n\n> integrity:", "\n> integrity:").count("integrity") == 1
    assert render(got, root) == got
    cases += 1

    # The integrity line counts DECLARED lanes, so a note that cites reports it
    # never declared cannot inflate it -- that citation is prose, not a roll-call.
    with tempfile.TemporaryDirectory() as td:
        r2 = Path(td).resolve()
        d = r2 / "notes" / "reviews" / "VID"
        d.mkdir(parents=True)
        (d / "x-review-facts.md").write_text("x", encoding="utf-8")
        body = "cites `notes/reviews/VID/x-review-facts.md` and a review-ghost.md\n"
        assert review_reports(body, "video_id: VID\nreviews: [facts]\n", r2) == (1, [])
        cases += 1
        assert review_reports(body, "video_id: VID\nreviews: [facts, dead]\n",
                              r2) == (2, ["dead"])
        cases += 1
        # ...and a note written before the convention still counts what it cites.
        assert review_reports(body, "video_id: VID\n", r2)[0] == 1
        cases += 1
    print(f"selftest OK ({cases} cases)")
    return 0


def main(argv: list[str]) -> int:
    root = POLICY.root(fallback=Path(__file__).resolve().parent.parent)
    ap = argparse.ArgumentParser(description="count would-be demotions per note")
    ap.add_argument("paths", nargs="*", type=Path)
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="print counts only; --diff and --apply do the rest")
    ap.add_argument("--show-orphans", action="store_true",
                    help="list the orphan timestamps per note")
    ap.add_argument("--all-sections", action="store_true",
                    help="count orphans in provenance sections too")
    ap.add_argument("--diff", action="store_true",
                    help="print the unified diff the renderer would apply")
    ap.add_argument("--apply", action="store_true",
                    help="rewrite the notes in place")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest(root)

    if args.diff or args.apply:
        import difflib
        changed = 0
        for f in collect([p.resolve() for p in args.paths]
                     or [root / POLICY.notes_dir()]):
            before = f.read_text(encoding="utf-8")
            after = render(before, root)
            if after == before:
                continue
            changed += 1
            if args.apply:
                f.write_text(after, encoding="utf-8")
            else:
                sys.stdout.writelines(difflib.unified_diff(
                    before.splitlines(True), after.splitlines(True),
                    f"a/{f.name}", f"b/{f.name}", n=1))
        verb = "rewritten" if args.apply else "would change"
        print(f"# {changed} notes {verb}", file=sys.stderr)
        return 0

    rows = [count_note(f, root, args.all_sections) for f in
            collect([p.resolve() for p in args.paths]
                        or [root / POLICY.notes_dir()])]

    cols = ("claims", "untagged", "objections", "speculation", "orphans",
            "lanes_missing", "demotions")
    print("note\t" + "\t".join(cols))
    for r in rows:
        if "error" in r:
            print(f"{r['file']}\t{r['error']}")
            continue
        print(Path(r["file"]).name + "\t" + "\t".join(str(r[c]) for c in cols))
        if args.show_orphans and r["orphan_list"]:
            print("  orphans: " + ", ".join(r["orphan_list"]))

    good = [r for r in rows if "error" not in r]
    if good:
        d = sorted(r["demotions"] for r in good)
        median = d[len(d) // 2] if len(d) % 2 else (d[len(d) // 2 - 1] + d[len(d) // 2]) / 2
        print(f"# {len(good)} notes, {sum(d)} would-be demotions, "
              f"median {median:g} per note", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
