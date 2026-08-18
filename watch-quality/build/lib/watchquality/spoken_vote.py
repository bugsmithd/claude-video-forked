#!/usr/bin/env python3
"""A textual witness for SPOKEN anchors, and the promotion it licenses.

WHY THIS EXISTS. `anchor_manifest.py` resolves an `ON-SCREEN` anchor against a
decoded frame second, and it announces the caption namespace VACUOUS because
YouTube auto-captions roll continuously: coalesced spans cover 98-100% of
runtime, so "is this second inside a cue" cannot fail. 260 corpus anchors sat in
one unchecked bucket when this was opened, 270 now, verified by nothing, and
`SPOKEN` was therefore the cheapest label that clears
`--require-evidence-class`. That is the gap this closes. Slice 11 moved the
witness primitives into `anchor_manifest`, which now splits that bucket into
`witnessed-spoken` and `unchecked-spoken` using them.

WHAT THE WITNESS IS. Not containment -- CONTENT. Take the content words of the
anchor's own markdown item, take the words actually said within a window of the
cited second, and intersect. Median fraction matched AT the cited second, by
the class the note gave the anchor:

    SPOKEN       0.333  (n=250)
    ON-SCREEN    0.280  (n=123)
    unattributed 0.075  (n=143)

Those n's are the slice-9 corpus state. The live counts move with every note
edit; `--check` and `--control` print the current ones and are the oracle.

HOW WELL IT LOCALISES, AND WHERE THAT STOPS. `--control` prints the whole
curve rather than one convenient point, because the first version shifted only
by two minutes and further, reported 115x, and read as if the witness pinned
the cited SECOND:

    shifted  15s  44.4% pass   1.4x
    shifted  30s  17.3% pass   3.5x
    shifted  60s   4.1% pass  14.7x   <- the gate is read here
    shifted 120s   1.5% pass  40.1x
    shifted 600s   0.0% pass    inf

So the honest claim is that a witness places a claim within about a minute. It
cannot tell 12:00 from 12:15, and any use that depends on the exact second is
not supported by this evidence.

WHAT IT CANNOT DO, MEASURED, NOT ASSUMED. `ON-SCREEN` scores 0.280 against
`SPOKEN`'s 0.333, while an unattributed item scores 0.075. The two classes sit
together and far from the floor: people say what is on their slides, so a
witness confirms that words were spoken near a second and comes nowhere near
separating the two classes. It licenses `SPOKEN` because `SPOKEN` is then true,
not because the alternative is excluded.

TWO BLOCKING USES WERE BUILT AND BOTH ARE REJECTED ON MEASUREMENT:

  * An absence floor -- fail a `SPOKEN` anchor with no overlap at all. Every
    firing is a paraphrase or a window-edge miss, and none is a wrong citation.
    Two adjudicated firings carry the argument. In the first the anchor is 13
    seconds outside a 12-second window while the claim is right, so the check
    would red-light correct work. The second is worse: the words ARE spoken at
    the cited second and the filter could not see them, because it drops digits
    and three-letter tokens. A checker that red-lights its own evidence is the
    false light the plan says kills a check. Count is window-dependent, which is
    itself the argument: 7 firings at a 6-word item floor, 10 at the shipped
    `MIN_ITEM_WORDS`. Both firings are quoted in full, with their note and line,
    in docs/reviews/gate-source-corpus-references.md.
  * A mis-anchor scan -- fail when the item matches some other second far
    better. Fires 6 times, false 6 of 6: three are items citing two moments at
    once, and three are the video restating itself later. A talk that repeats
    its own thesis is not a mis-citation.

The witness denominator is the WHOLE SPOKEN population: `--check` prints
`270 SPOKEN = witnessed + scored without a witness + too short + beyond the
runtime`. It used to be counted after two `continue`s, so 19 short-item
anchors fell out of it silently. `--control` scores only the SCORABLE ones and
says so, because it cannot score an item too short to score.

AUTOMATIC PROMOTION WAS BUILT AND IS ALSO REJECTED. The rule fired on 42
unattributed anchors before this slice's ten conversions (34 remain), and
hand-adjudicating them against the caption text killed it: the drifting note says
**"Never said aloud"** in the same sentence as three of the candidates, and
three more are table rows transcribing an on-screen table whose vocabulary the
speaker naturally shares while describing the exercise. That is 6 of the first
12 adjudicated, contradicting the note's own words. It is the measurement above
restated in the corpus: a witness cannot separate `SPOKEN` from `ON-SCREEN`, so
it cannot pick a class.

So the tool REPORTS and never rewrites. Candidates are printed as
`CANDIDATE` lines for a human to accept or refuse one at a time, and 10 were
accepted on this corpus. `--check` exits non-zero only on a real error;
`--control` exits non-zero when the witness stops discriminating, which is the
one thing here that can actually break.

Usage:
    scripts/spoken_vote.py --index=VIDEO_ID          # write notes/.captions/
    scripts/spoken_vote.py --check                   # the default
    scripts/spoken_vote.py --check --candidates      # list what a human could label
    scripts/spoken_vote.py --check --window 20
    scripts/spoken_vote.py --report
    scripts/spoken_vote.py --control                 # the shift control
    scripts/spoken_vote.py --control --window 4      # 4s fails, 30s fails, 12 is measured
    scripts/spoken_vote.py --selftest

`--index=<id>`, with the equals sign: one real video id starts with a hyphen.

Exit: 0 clean, 1 a defect or a failed control, 2 usage or IO error. A CANDIDATE
is NOT a defect and does not affect the exit code -- a witness is not a class.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


# The witness primitives live in `anchor_manifest` because the cues namespace
# is that module's and this is the resolver it never had; slice 11 moved them
# down rather than keeping a second copy, which is what let `note_items` drift
# from `note_anchors` in the first place.
from .anchor_manifest import (CAPTION_DIR, CAPTION_HEADER,  # noqa: E402,F401
                             MIN_FRACTION, MIN_HITS, MIN_ITEM_WORDS, RE_ANCHOR,
                             RE_VIDEO_ID, STOPWORDS, WINDOW, bind_classes,
                             caption_path, collect_notes, content_words, hms,
                             note_anchors, note_blocks, promotable, read_index,
                             safe_read, split_frontmatter, spoken_at, witness)
from .resolve_note import parse_duration  # noqa: E402
from .say_captions import cues, find_tracks, pick_track  # noqa: E402
from .wq_policy import load as load_policy  # noqa: E402

POLICY = load_policy()

PROG = "spoken_vote.py"

# The control's shifts, and the separation it must show for the check to mean
# anything. Below this the namespace is as vacuous as containment was.
CONTROL_SHIFTS = (15, 30, 60, 120, 300, 600)
# The shift the gate is read at, and the floor it must clear. Both are stated
# because the first version shifted only by 120s and further, scored 115x, and
# read as if the witness pinned the cited SECOND. It does not: the selectivity
# curve is 1.4x at 15s, 3.3x at 30s, 13.0x at 60s. The honest claim is that a
# witness localises a claim to about a minute, and the gate is read there.
CONTROL_GATE_SHIFT = 60
MIN_SELECTIVITY = 5.0
# Selectivity alone passes a world where NOTHING matches. Rotate every caption
# index onto the wrong video and true and shifted both go to zero, which reads
# as infinite selectivity -- the slice-9 coverage lane's blocker, reproduced on
# the real corpus before it was believed. So the control also demands that the
# witness still work at the cited second. Measured 61.0%; the floor sits far
# below that and far above a wrong-index zero.
MIN_TRUE_PASS = 0.25

UNATTRIBUTED = ("NONE", "MULTI", "CONTESTED")


def write_index(root: Path, video_id: str, rows) -> Path:
    out = caption_path(root, video_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# video_id\t{video_id}", f"# cues\t{len(rows)}", CAPTION_HEADER]
    for start, end, said in rows:
        lines.append(f"{start:.3f}\t{end:.3f}\t" + said.replace("\t", " "))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def note_items(body: str, start_line: int, video_id: str = ""):
    """(anchor, seconds, bound class, line number, item content words).

    A thin view over `anchor_manifest.note_anchors`, not a second walker. The
    first version re-implemented it and drifted: the slice-9 conformance lane
    filed the duplication, and slice 11 found it costing 19 anchors out of a
    published denominator. Fenced text is quoted, not claimed, so `VERBATIM`
    anchors stay out here as they always did.
    """
    for a in note_anchors(body, start_line, video_id):
        if a["class"] == "VERBATIM":
            continue
        yield (a["anchor"], a["seconds"], a["class"], a["line"], a["item"])


def score_note(path: Path, root: Path, window: int = WINDOW):
    """(defect lines, row). A note with no caption index is reported, never
    failed -- the index is recoverable, and failing on it would punish the
    note for an artefact the note does not control."""
    rel = str(path.relative_to(root) if path.is_relative_to(root) else path)
    row = {"file": rel, "video_id": "", "index": "missing", "anchors": 0,
           "spoken": 0, "spoken_witnessed": 0, "spoken_unscorable": 0,
           "spoken_over_duration": 0, "unattributed": 0, "promotable": 0,
           "unscorable": 0, "candidates": []}
    text, why = safe_read(path)
    if text is None:
        return [f"{rel}:1 E-READ {why}"], row
    split = split_frontmatter(text)
    if split is None:
        return [f"{rel}:1 E-FRONTMATTER no YAML frontmatter block"], row
    frontmatter, body = split
    m = RE_VIDEO_ID.search(frontmatter)
    if not m:
        return [f"{rel}:1 E-NO-VIDEO-ID cannot address a caption index"], row
    video_id = m.group(1)
    row["video_id"] = video_id
    cpath = caption_path(root, video_id)
    if not cpath.is_file():
        # A track on disk that was never indexed IS a defect, and a clearable
        # one: `--index=<id>` fixes it in a second. A video with no track
        # anywhere is not the note's fault and only gets a printed line.
        if find_tracks(video_id):
            return [f"{rel}:1 E-CAPTION-UNINDEXED a caption track exists for "
                    f"{video_id} but {CAPTION_DIR}/{video_id}.tsv does not; "
                    f"run --index={video_id}"], row
        return [], row
    rows = read_index(cpath)
    if not rows:
        return [f"{cpath.name}:1 E-CAPTION-EMPTY index holds no cues"], row
    row["index"] = "present"

    duration = parse_duration(frontmatter) or 0
    out: list[str] = []
    for anchor, seconds, cls, ln, item in note_items(body, len(
            frontmatter.split("\n")) + 3):
        row["anchors"] += 1
        # `spoken` is the WHOLE SPOKEN population, counted before anything can
        # skip it. It used to be incremented after these two `continue`s, so 19
        # corpus anchors with short items fell out of the published "153 of
        # 251" denominator without appearing in any SPOKEN-specific counter,
        # and an anchor past the note's runtime fell out of every counter at
        # all. A denominator that quietly shrinks is how a witness rate reads
        # better than it is.
        if cls == "SPOKEN":
            row["spoken"] += 1
        if duration and seconds > duration:
            if cls == "SPOKEN":
                row["spoken_over_duration"] += 1
            continue
        if len(item) < MIN_ITEM_WORDS:
            row["unscorable"] += 1
            if cls == "SPOKEN":
                row["spoken_unscorable"] += 1
            continue
        hits, fraction = witness(item, rows, seconds, window)
        if cls == "SPOKEN":
            row["spoken_witnessed"] += promotable(hits, fraction)
        elif cls in UNATTRIBUTED:
            row["unattributed"] += 1
            if promotable(hits, fraction):
                row["promotable"] += 1
                row["candidates"].append(
                    f"{rel}:{ln} CANDIDATE `[{anchor}]` has no evidence class, "
                    f"and {hits} of {len(item)} of its words are spoken within "
                    f"{window}s of {hms(seconds)} ({fraction:.0%}) -- a human "
                    f"decides whether that is how the note knows it")
    return out, row


def control(files: list[Path], root: Path,
            window: int = WINDOW) -> tuple[list[str], bool]:
    """Score every anchor at its own second and at shifted ones.

    A rule that scores a moment the claim was never made just as highly is
    measuring vocabulary, not evidence, and would be the caption namespace's
    vacuity wearing a new hat.

    The whole curve is printed, not one convenient point. Shifting only by
    minutes and reporting the result reads as if the witness pinned the cited
    second; the near shifts say otherwise and are the honest limit of the tool.
    """
    import collections as _c
    per = _c.defaultdict(lambda: [0, 0])
    true_pass = true_n = 0
    for path in files:
        text, _ = safe_read(path)
        split = split_frontmatter(text or "")
        if split is None:
            continue
        frontmatter, body = split
        m = RE_VIDEO_ID.search(frontmatter)
        if not m:
            continue
        cpath = caption_path(root, m.group(1))
        if not cpath.is_file():
            continue
        rows = read_index(cpath)
        duration = parse_duration(frontmatter) or 0
        for _, seconds, cls, _, item in note_items(body, len(
                frontmatter.split("\n")) + 3):
            if cls != "SPOKEN" or len(item) < MIN_ITEM_WORDS:
                continue
            if duration and seconds > duration:
                continue
            true_n += 1
            true_pass += promotable(*witness(item, rows, seconds, window))
            for shift in CONTROL_SHIFTS:
                for at in (seconds - shift, seconds + shift):
                    if not 0 <= at <= duration:
                        continue
                    per[shift][1] += 1
                    per[shift][0] += promotable(*witness(item, rows, at, window))
    if not true_n or not per:
        return ["# control: nothing to score"], False
    t = true_pass / true_n
    # SCORABLE, not all: the control can only score an item long enough to
    # score, so it must not reuse the word the headline uses for the whole
    # SPOKEN population -- that is the denominator slice 11 just unshrank.
    out = [f"# control: {true_n} scorable SPOKEN anchor(s) at their own "
           f"second, {t:.1%} pass, window {window}s"]
    gate = None
    for shift in sorted(per):
        hits, n = per[shift]
        rate = hits / n
        sel = t / rate if rate else float("inf")
        mark = "  <- gate" if shift == CONTROL_GATE_SHIFT else ""
        out.append(f"# shifted {shift:>4}s: {n:>4} scored, {rate:>5.1%} pass, "
                   f"selectivity {sel:>5.1f}x{mark}")
        if shift == CONTROL_GATE_SHIFT:
            gate = sel
    live = t >= MIN_TRUE_PASS
    ok = live and gate is not None and gate >= MIN_SELECTIVITY
    out.append(f"# a witness localises a claim to about {CONTROL_GATE_SHIFT}s, "
               f"not to the cited second -- read the near rows")
    if not live:
        out.append(f"# only {t:.1%} pass at the cited second, under the "
                   f"{MIN_TRUE_PASS:.0%} floor -- the indexes are wrong or the "
                   f"scoring is broken; selectivity means nothing when both "
                   f"sides are zero")
    out.append(f"# control {'HOLDS' if ok else 'FAILED'} at "
               f"{CONTROL_GATE_SHIFT}s (floor {MIN_SELECTIVITY:.0f}x, "
               f"cited-second floor {MIN_TRUE_PASS:.0%})")
    return out, ok


def report(rows: list[dict]) -> str:
    # new columns are APPENDED so a positional consumer keeps reading the same
    # ones, matching `anchor_manifest.report`
    cols = ("file", "video_id", "index", "anchors", "spoken",
            "spoken_witnessed", "unattributed", "promotable", "unscorable",
            "spoken_unscorable", "spoken_over_duration")
    out = ["\t".join(cols)]
    for r in rows:
        out.append("\t".join(str(r[c]) for c in cols))
    return "\n".join(out)


def selftest() -> int:
    import tempfile
    fails = cases = 0

    def check(name, got, want):
        nonlocal fails, cases
        cases += 1
        if got != want:
            fails += 1
            print(f"FAIL {name}: got {got!r} want {want!r}")

    check("machine marks are not evidence",
          content_words("`[01:00]` `SPOKEN` `ORPHAN` retention curve"),
          {"retention", "curve"})
    check("stopwords drop", content_words("this is about the thing"), {"thing"})
    check("short words drop", content_words("a b cd efg hijk"), {"hijk"})
    check("apostrophes survive", content_words("founder's problem"),
          {"founder's", "problem"})

    rows = [(10.0, 13.0, "cohort retention curve is the chart"),
            (13.0, 16.0, "that matters most for product market fit")]
    item = content_words("- the cohort retention curve is offered as the chart "
                         "that matters most")
    check("witness at the second", witness(item, rows, 12)[0] >= 4, True)
    check("witness far away", witness(item, rows, 900), (0, 0.0))
    check("promotion rule", promotable(4, 0.25), True)
    check("four hits but a long item fails", promotable(4, 0.10), False)
    check("high fraction but three hits fails", promotable(3, 0.9), False)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        (root / "notes").mkdir()
        write_index(root, "TESTID", rows)
        check("index round-trips", read_index(caption_path(root, "TESTID")),
              rows)
        check("a hyphen-leading id gets its own file",
              write_index(root, "-TESTID", rows).name, "-TESTID.tsv")

        body = ("---\ntitle: t\nvideo_id: TESTID\nduration: \"10:00\"\n---\n\n"
                "# t\n\n## Claims\n\n")
        note = root / "notes" / "2026-01-01--probe--TESTID.md"

        note.write_text(body + "- `[00:12]` the cohort retention curve is "
                        "offered as the chart that matters most\n", encoding="utf-8")
        d, row = score_note(note, root)
        check("an unattributed anchor with a witness is a candidate",
              (len(d), row["promotable"]), (0, 1))
        check("a candidate is never a defect, and says so",
              "a human decides" in row["candidates"][0], True)

        note.write_text(body + "- `[09:30]` the cohort retention curve is "
                        "offered as the chart that matters most\n", encoding="utf-8")
        check("the same item elsewhere is not promotable",
              score_note(note, root)[1]["promotable"], 0)

        note.write_text(body + "- `[00:12]` `SPOKEN` the cohort retention "
                        "curve is offered as the chart that matters most\n",
                        encoding="utf-8")
        d, row = score_note(note, root)
        check("an already-labelled anchor is never a defect", d, [])
        check("but it is counted as witnessed",
              (row["spoken"], row["spoken_witnessed"]), (1, 1))

        note.write_text(body + "- `[00:12]` `SPOKEN` nothing said here at all "
                        "about wombats aardvarks pangolins okapis\n",
                        encoding="utf-8")
        d, row = score_note(note, root)
        check("an unwitnessed SPOKEN anchor is NOT a defect", d, [])
        check("it is only counted", row["spoken_witnessed"], 0)

        note.write_text(body + "- `[00:12]` too short\n", encoding="utf-8")
        check("a tiny item is unscorable, not promotable",
              score_note(note, root)[1]["unscorable"], 1)

        # ---- the SPOKEN denominator is the whole population. Before slice 11
        # these two shapes were counted after `spoken` was incremented, so a
        # short item vanished from "153 of 251" and an over-runtime anchor
        # vanished from every counter there is.
        note.write_text(body + "- `[00:12]` `SPOKEN` too short\n",
                        encoding="utf-8")
        d, row = score_note(note, root)
        check("a short SPOKEN item still counts in the denominator",
              (row["spoken"], row["spoken_witnessed"],
               row["spoken_unscorable"]), (1, 0, 1))
        note.write_text(body + "- `[10:30]` `SPOKEN` the cohort retention "
                        "curve is offered as the chart that matters most\n",
                        encoding="utf-8")
        d, row = score_note(note, root)
        check("an over-runtime SPOKEN anchor is counted, not dropped",
              (row["spoken"], row["spoken_over_duration"],
               row["spoken_witnessed"]), (1, 1, 0))
        check("and the SPOKEN row partitions",
              row["spoken"], row["spoken_witnessed"] + row["spoken_unscorable"]
              + row["spoken_over_duration"])

        note.write_text(body + "```text\n- `[00:12]` the cohort retention "
                        "curve is the chart that matters most\n```\n",
                        encoding="utf-8")
        check("fenced text is quoted, not claimed",
              score_note(note, root)[1]["anchors"], 0)

        # `--control --window N` used to accept N and score at 12s anyway, so
        # WINDOW had no evidence artifact behind it at all. It does now: on the
        # real corpus 4s fails the cited-second floor and 30s fails the
        # selectivity gate, which is what makes 12 a measurement.
        note.write_text(body + "- `[00:12]` `SPOKEN` the cohort retention "
                        "curve is offered as the chart that matters most\n",
                        encoding="utf-8")
        wide = control([note], root, window=12)[0][0]
        narrow = control([note], root, window=1)[0][0]
        check("--control reports the window it used",
              ("window 12s" in wide, "window 1s" in narrow), (True, True))
        check("and a narrower window really scores differently",
              wide != narrow, True)

        note.write_text(body + "- `[20:00]` the cohort retention curve is "
                        "offered as the chart that matters most\n", encoding="utf-8")
        check("an anchor past the runtime is skipped, not scored",
              score_note(note, root)[1]["promotable"], 0)

        # The control must fail when every index names the wrong video. Both
        # sides then score zero, which reads as infinite selectivity -- so the
        # cited-second floor, not the ratio, is what catches it.
        note.write_text(body + "- `[00:12]` `SPOKEN` the cohort retention "
                        "curve is offered as the chart that matters most\n",
                        encoding="utf-8")
        check("the control holds on a real index",
              control([note], root)[1], True)
        write_index(root, "TESTID", [(10.0, 13.0, "wombats aardvarks pangolins"),
                                     (13.0, 16.0, "okapis quokkas narwhals")])
        lines, ok = control([note], root)
        check("a wrong index fails the control", ok, False)
        check("and says why", any("indexes are wrong" in x for x in lines), True)
        write_index(root, "TESTID", rows)

        bare = root / "notes" / "2026-01-01--none--NOINDEX.md"
        bare.write_text(body.replace("TESTID", "NOINDEX")
                        + "- `[00:12]` a claim with plenty of content words\n",
                        encoding="utf-8")
        d, row = score_note(bare, root)
        check("no caption index is reported, not failed", (d, row["index"]),
              ([], "missing"))

    if fails:
        print(f"selftest FAILED ({fails})")
        return 1
    print(f"selftest OK ({cases} cases)")
    return 0


def main(argv: list[str] | None = None) -> int:
    root = POLICY.root(fallback=Path(__file__).resolve().parent.parent)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*", type=Path,
                    help="notes or directories; defaults to notes/")
    ap.add_argument("--check", action="store_true",
                    help="score anchors against the caption index (the default)")
    ap.add_argument("--report", action="store_true",
                    help="TSV on stdout, one row per note")
    ap.add_argument("--index", metavar="VIDEO_ID",
                    help="write notes/.captions/<id>.tsv; use --index=<id>")
    ap.add_argument("--track", type=Path,
                    help="a .vtt to index instead of searching for one")
    ap.add_argument("--candidates", action="store_true",
                    help="list unattributed anchors that carry a witness")
    ap.add_argument("--window", type=int, default=WINDOW, metavar="SECONDS",
                    help="how far either side of a cited second to look")
    ap.add_argument("--control", action="store_true",
                    help="score every SPOKEN anchor at shifted seconds too")
    ap.add_argument("--selftest", action="store_true",
                    help="known-answer cases")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.index:
        if args.check or args.report or args.control or args.paths:
            print(f"{PROG}: --index does not combine with "
                  f"--check/--report/--control/paths", file=sys.stderr)
            return 2
        track = args.track
        if track is None:
            found = find_tracks(args.index)
            if not found:
                print(f"{PROG}: no caption track for {args.index}",
                      file=sys.stderr)
                return 2
            track = pick_track(found)
        if not track.is_file():
            print(f"{PROG}: {track} is not a file", file=sys.stderr)
            return 2
        rows = cues(track)
        if not rows:
            print(f"{PROG}: {track} holds no cues", file=sys.stderr)
            return 2
        out = write_index(root, args.index, rows)
        print(f"# {out.relative_to(root)}\t{len(rows)} cue(s)\tfrom {track}")
        return 0

    files = collect_notes([p.resolve() for p in args.paths]
                          or [root / POLICY.notes_dir()])
    if not files:
        print(f"{PROG}: no notes found", file=sys.stderr)
        return 2

    if args.control:
        lines, ok = control(files, root, args.window)
        for line in lines:
            print(line, file=sys.stderr)
        return 0 if ok else 1

    defects: list[str] = []
    rows: list[dict] = []
    for f in files:
        d, row = score_note(f, root, args.window)
        defects.extend(d)
        rows.append(row)

    if args.report:
        print(report(rows))
    for line in defects:
        print(line, file=sys.stderr)
    if args.candidates:
        for r in rows:
            for line in r["candidates"]:
                print(line, file=sys.stderr)
    indexed = [r for r in rows if r["index"] == "present"]
    spoken = sum(r["spoken"] for r in indexed)
    seen = sum(r["spoken_witnessed"] for r in indexed)
    cand = sum(r["promotable"] for r in indexed)
    short = sum(r["spoken_unscorable"] for r in indexed)
    over = sum(r["spoken_over_duration"] for r in indexed)
    print(f"# {len(indexed)} of {len(rows)} note(s) have a caption index; "
          f"{seen} of {spoken} SPOKEN anchor(s) carry a textual witness",
          file=sys.stderr)
    # a partition, not a headline: every SPOKEN anchor lands in exactly one of
    # these, so the denominator cannot quietly shrink again
    print(f"# {spoken} SPOKEN = {seen} witnessed + "
          f"{spoken - seen - short - over} scored without a witness + "
          f"{short} item too short to score + {over} beyond the runtime; "
          f"absence is NOT evidence, see docs/reviews/slice9-spoken-witness.md",
          file=sys.stderr)
    for r in rows:
        if r["index"] == "missing":
            print(f"# no caption index\t{r['file']}\t{r['video_id']}",
                  file=sys.stderr)
    print(f"# {cand} unattributed anchor(s) have a witness "
          f"(--candidates lists them); a witness is not a class",
          file=sys.stderr)
    print(f"# {len(defects)} defect(s)", file=sys.stderr)
    return 1 if defects else 0


if __name__ == "__main__":
    raise SystemExit(main())
