#!/usr/bin/env python3
"""Anchor manifest and reconciliation for watch-quality notes.

Slice 6 of docs/note-quality-plan-round-2.md, which is round 1's slice 3 still
unbuilt: "Manifest artifact + linter resolves anchors against it", killing R4
hard. Nothing in the repo reconciled the timestamps a note cites against the
artifacts a run produced, so "this second exists" was an assumption everywhere.

Three jobs:

  --adopt   copy one or more `watch.py` run directories somewhere durable,
            re-hash every file written, and only then --build from the copy.
            This is the entry point a new note uses, and the ordering is the
            point: a manifest built straight from a run directory names files
            under a path the OS owns, so --verify-artifacts is a bluff that
            expires at the next reboot. `--runs-root` overrides the
            destination, `--with-video` keeps the video file, which is left
            behind by default because it is large and re-downloadable while the
            keyframes are neither.
  --build   read one or more run directories and write the manifest to
            notes/anchors/<video_id>.tsv. Durable only if the directories are;
            a run directory is a temp dir by default and gets reaped by the OS,
            which is why four of the eight notes here can never be reconciled.
  --check   resolve every anchor a note cites against that manifest.

THE NAMESPACE IS A UNION OF TWO, AND THEY DO NOT DISCRIMINATE EQUALLY.

  frames    decoded seconds, taken from the frame filename that `stamp_paths`
            already writes (`frame_0252_t19m20s.jpg`). Measured before this
            file existed: across the two notes whose frames survive, 13 of 13
            `ON-SCREEN` anchors equal an extracted frame second EXACTLY, so the
            band is zero. Selective: on the densest surviving run, 137 of 1833
            seconds are frames. (That sample is notes written AFTER stamping
            landed, so exactness there is the transcription being right, not the
            decode being accurate -- the one note with known 8-14s drift has
            unstamped frames and is excluded by construction. Zero band is still
            the right call, because a band would mask R1's swap. The runs behind
            these counts are named in
            docs/reviews/gate-source-corpus-references.md.)
  cues      caption spans, coalesced. `SPOKEN` and `INFERRED` anchors resolve
            against them -- but ONLY when the namespace discriminates. Measured
            on all four surviving runs: YouTube auto-captions roll
            continuously, so coalesced spans collapse to ONE span covering
            98.2-100% of runtime. Containment is then a tautology, and a
            namespace admitting >= 95% of the runtime is announced VACUOUS and
            its resolver is switched off rather than reported as a check that
            passed. A gate that cannot fail is not a gate, and rendering one as
            a pass is R10's defect wearing a different hat.

WHAT THIS DOES NOT CATCH. Resolution closes the namespace, so an anchor naming
a second the run never produced is an exit code. It cannot catch a *swap*
between two seconds that were both extracted -- R1's mis-pairing survives this
and stays open (docs/reviews/slice4-frame-pairing.md).

ATTRIBUTION IS PER ITEM, AND NEVER INHERITED. An anchor takes the evidence
class of its OWN markdown item -- one bullet, one table row, one blockquote
line, one heading, one paragraph -- with wrapped continuation lines folded in
because notes wrap near 80 columns. A sub-bullet does NOT inherit its parent's
class, and one tagged row in a table does NOT tag the rest of the table. The
first version bled a single `ON-SCREEN` tag across a whole table and mis-bound
three correct anchors in the marketing-agents note; under-checking is counted
loudly, over-checking is the false red light the plan says kills a checker.
Anchors inside a fenced block are VERBATIM: quoted text is not a claim.

AN ITEM WITH TWO CLASSES IS SPLIT, NOT DISCARDED. Notes label per anchor --
``[07:04]` `SPOKEN` `ON-SCREEN` `[08:04]`` is one bullet saying two things --
and calling the whole item unattributable threw away 97 corpus anchors the
note had already labelled. `bind_classes` gives each label the anchor it
TOUCHES: anchors joined to each other by glue alone are one group and take one
class, and a label reaches the nearest group through glue alone. One word of
prose ends the reach. Measured on the corpus: 313 -> 216 unattributable with
no note edited, no anchor newly unresolved.

THE CONTROL IS `--control`, AND IT IS THE SECOND ONE WRITTEN. The first
reversed the order of the class tokens and counted how many anchors moved.
Three review lanes refuted it independently: the nearest-token rule that was
thrown out for printing false defects scored BETTER on it, 110 of 128 against
99, and the figures moved whenever the corpus moved. `--control` instead puts
a word of prose in every label/anchor gap -- a touch rule must lose EVERY
binding, a distance rule loses none. Measured 105 of 105 against 0 of 121. A
control that ranks a known-wrong rule above the shipped one measures nothing,
which is the same argument this file makes about the VACUOUS cues namespace.

Every anchor lands in exactly one disposition and the summary must balance:
resolved, unresolved, unchecked, witnessed-spoken, unchecked-spoken,
unbound-inferred, unattributable, verbatim, foreign, cross-referenced. If it
ever fails to balance, say so.

`witnessed-spoken` and `unchecked-spoken` replaced `unbound-spoken` in slice 11.
That is a WIRE BREAK: `--report` column positions after `unchecked` all shift,
and a positional consumer of an older report reads the wrong column. A partition
member cannot be renamed without one, so it is stated rather than hidden.
Slice 12's `cross-referenced` is NOT a wire break: it is last in DISPOSITIONS
and `report()` writes it as the LAST column of all, after `blanket`, so every
column an older consumer reads keeps its index. A selftest pins those indices,
because the first attempt spliced it into the disposition block and silently
moved seven columns -- the conformance lane caught it.

AN ANCHOR WITH NO EVIDENCE CLASS IS ALSO A DEFECT BY DEFAULT, on the same
terms, and since slice 12 there is nothing left to exempt: UNATTRIBUTED_NOTES
is EMPTY, so the defect fires for every note, and the line says which pile each
anchor is in -- unlabelled, labelled but untouched, or contested.
`--no-require-evidence-class` is the escape hatch, not the default, because a
switch that is off exempts every note written from here to spare four old ones.

SINCE SLICE 13 THE STRICTER READING IS ALSO THE DEFAULT. A `cross-referenced`
anchor -- one carrying no class of its own that repeats a second the note
labels once elsewhere -- is a defect, `E-ANCHOR-CROSSREF`. Slice 12 shipped
that as an opt-in flag failing at 106 anchors because the corpus was not
converted; slice 13 converted all 106 and flipped the default, so the
disposition still exists to name the shape and the count is expected to be 0.
`--no-require-touched-class` disables it and there is no dated exemption list.

A note whose manifest is missing or unusable is a DEFECT by default. The four
runs the OS reaped, plus the drifting run's pre-stamping frames, are named in
UNRESOLVABLE_RUNS with a date and a reason and downgrade to a printed WARN --
a bounded, shrinking list, not a switch. Every note written from here has its
run directory on disk at writing time, so the light is clearable; a blanket
default-off would have exempted every future note to spare four old ones.

Usage:
    scripts/anchor_manifest.py                        # --check over notes/
    scripts/anchor_manifest.py --check PATH...
    scripts/anchor_manifest.py --check --max-gap 300  # uncited-frame span fails
    scripts/anchor_manifest.py --check --no-require-manifest
    scripts/anchor_manifest.py --check --no-require-evidence-class
    scripts/anchor_manifest.py --control             # the binding rule's control
    scripts/anchor_manifest.py --check --verify-artifacts
    scripts/anchor_manifest.py --report               # TSV on stdout only
    scripts/anchor_manifest.py --adopt=VIDEO_ID --run-dir DIR [--run-dir DIR]
    scripts/anchor_manifest.py --adopt=VIDEO_ID --run-dir DIR --with-video
    scripts/anchor_manifest.py --build=VIDEO_ID --run-dir DIR [--run-dir DIR]
    scripts/anchor_manifest.py --check --no-require-touched-class
    scripts/anchor_manifest.py --selftest

`--build=<id>`, with the equals sign: one real video id starts with a hyphen.

Exit: 0 clean, 1 defects found, 2 usage or IO error.
"""

from __future__ import annotations

import argparse
import bisect
import collections
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path


from .resolve_note import (DEMOTED_MARK, ORPHAN_MARK, RE_ANCHOR,  # noqa: E402
                          RE_NOTE_NAME, RE_VIDEO_ID, anchor_seconds,
                          parse_duration, safe_read, sha256, split_frontmatter)
from .wq_policy import load as load_policy  # noqa: E402

POLICY = load_policy()

PROG = "anchor_manifest.py"

EVIDENCE_CLASSES = ("ON-SCREEN", "SPOKEN", "INFERRED")
PIXEL_CLASS = "ON-SCREEN"
AUDIO_CLASSES = ("SPOKEN", "INFERRED")
# The class as the note writes it, so a bare word in prose never counts.
RE_CLASS_TOKEN = re.compile(r"`(%s)`" % "|".join(EVIDENCE_CLASSES))
# What may sit between an anchor and its label without breaking the pair.
RE_GLUE = re.compile(r"^[\s`+,;/&|*~-]*$")
# Timestamp-shaped, whatever the punctuation around it. RE_ANCHOR requires
# backticks AND caps minutes at two digits, so `[105:18]` and a bare [46:30]
# are both invisible to it -- and invisible is worse than wrong, because the
# note then reads clean. Deliberately wider than RE_ANCHOR on both counts.
RE_LOOSE_STAMP = re.compile(r"(.?)\[(\d{1,3}:\d{1,2}(?::\d{1,2})?)\](.?)")

# Runs whose evidence cannot be reconstructed, and notes whose anchors pre-date
# the evidence-class requirement. Both lists may only shrink and both are
# printed on every run. Their CONTENTS are facts about one corpus, so they live
# in watch-quality.toml (see scripts/wq_policy.py); an entry without an ISO date
# is rejected there rather than merely discouraged here. With no policy file
# both lists are EMPTY -- a missing policy can never invent an exemption.
UNRESOLVABLE_RUNS = POLICY.unresolvable_runs()
UNATTRIBUTED_NOTES: dict[str, str] = POLICY.unattributed_notes()

RE_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
RE_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
RE_TABLE_ROW = re.compile(r"^\s*\|")
RE_QUOTE = re.compile(r"^\s*>")
RE_FENCE = re.compile(r"^\s*(?:```|~~~)")
# A reference to another note. Only exempts when that note is a DIFFERENT video.
RE_NOTE_REF = POLICY.note_ref_re()
# stamp_paths writes frame_0252_t19m20s.jpg / cue_0043_t09m05s.jpg, and the
# focus sweeps write full_19m20s.jpg. An hour group appears past 60 minutes.
RE_FRAME_STAMP = re.compile(r"_t?(?:(\d+)h)?(\d{1,2})m(\d{1,2})s\.(?:jpg|jpeg|png)$",
                            re.IGNORECASE)
# Both legal WebVTT timestamp forms: HH:MM:SS.mmm and MM:SS.mmm.
RE_VTT_SPAN = re.compile(
    r"(?:(\d+):)?(\d{2}):(\d{2})\.(\d{3})\s+-->\s+(?:(\d+):)?(\d{2}):(\d{2})\.(\d{3})")

# Two caption cues closer than this are one span. Auto-captions overlap by
# design (a rolling window), so anything above 0 gives the same answer here.
CUE_GAP = 1.0
# The manifest describes one run of one video. More than this much disagreement
# with the note's own duration means they are not the same video.
DURATION_TOL = 2.0
# A namespace admitting this fraction of the runtime cannot fail, so its
# resolver is switched off and the fact is announced.
VACUOUS_COVERAGE = 0.95

MANIFEST_HEADER = "kind\tstart\tend\tid\tsha256"
KINDS = ("frame", "cue")

# `witnessed-spoken` and `unchecked-spoken` replace the single `unbound-spoken`
# bucket. The old name read as a benign parking space for 270 of 592 anchors
# while the cues namespace was VACUOUS, so `SPOKEN` was the cheapest label that
# cleared every gate -- the slice-10 coverage lane called that a scoring rule
# that rewards the unverifiable answer. Splitting it says out loud which
# `SPOKEN` anchors an actual caption witness supports and which are simply not
# checked. A witness localises to about a minute, so it earns its own row and
# NOT `resolved`, which means an exact second.
# `cross-referenced` is APPENDED, never inserted, for the reason slice 11
# wrote down: renaming or inserting a partition member shifts every --report
# column after it, and a positional consumer reads the wrong number silently.
DISPOSITIONS = ("resolved", "unresolved", "unchecked", "witnessed-spoken",
                "unchecked-spoken", "unbound-inferred", "unattributable",
                "verbatim", "foreign", "cross-referenced")

# An unlabelled anchor whose SECOND is already labelled elsewhere in the SAME
# note is a re-citation, not a missing decision: the note said how it knows
# that moment, once, where it made the claim, and later prose points back at
# it. 129 of the 186 corpus anchors with no class are this shape. All 129 were
# read by hand against the line that labels them, and 127 cite the same
# channel -- but TWO do not (the satori note quotes spoken words at `[04:06]`
# and `[05:42]`, both of which it labels ON-SCREEN elsewhere), so this
# disposition deliberately does NOT assign the class. It says only that the
# note classes this second somewhere else. Both mismatches were repaired from
# the captions rather than laundered by the rule.
#
# Guards, each one a selftest: the second must carry EXACTLY ONE class
# elsewhere (two channels stay unattributable, because that is the question
# DEFER:YTN-BOTH-CLASSES-ONE-ANCHOR is about); the match is on the exact
# second, never a neighbour; and a fenced (VERBATIM) or foreign anchor donates
# nothing, since neither carries this note's evidence.


def manifest_path(root: Path, video_id: str) -> Path:
    return root / POLICY.anchors_dir() / f"{video_id}.tsv"


def hms(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def vtt_clock(hours: str | None, mins: str, secs: str, ms: str) -> float:
    return (int(hours or 0) * 3600 + int(mins) * 60 + int(secs) + int(ms) / 1000)


# --------------------------------------------------------------------------
# reading a run directory
# --------------------------------------------------------------------------

def frame_rows(run_dirs: list[Path]) -> tuple[dict[int, Path], list[Path],
                                              list[tuple[int, Path, Path]]]:
    """(second -> frame path, unstamped frames, collisions).

    An unstamped frame is REPORTED rather than guessed at. /tmp/dk-frames-rt
    holds `zoom_2814_draft.jpg`, where 2814 is neither a second (the video is
    1833s long) nor unambiguously 28:14; inferring one would fabricate exactly
    the kind of literal this whole round is about taking away from the writer.

    A collision -- two files stamped with the same second -- is reported for
    the same reason: 25 of the densest surviving run's 162 stamped frames share a second
    with another, and dropping them silently understates the coverage ledger's
    denominator and makes the surviving row's sha256 an arbitrary pick.
    """
    found: dict[int, Path] = {}
    unstamped: list[Path] = []
    collisions: list[tuple[int, Path, Path]] = []
    for d in run_dirs:
        for p in sorted(d.rglob("*")):
            if p.suffix.lower() not in (".jpg", ".jpeg", ".png") or not p.is_file():
                continue
            m = RE_FRAME_STAMP.search(p.name)
            if not m:
                unstamped.append(p)
                continue
            hours = int(m.group(1)) if m.group(1) else 0
            sec = hours * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if sec in found:
                collisions.append((sec, found[sec], p))
                continue
            found[sec] = p
    return found, unstamped, collisions


def vtt_spans(path: Path) -> list[tuple[float, float]]:
    text, _ = safe_read(path)
    if text is None:
        return []
    out = []
    for m in RE_VTT_SPAN.finditer(text):
        a = vtt_clock(m.group(1), m.group(2), m.group(3), m.group(4))
        b = vtt_clock(m.group(5), m.group(6), m.group(7), m.group(8))
        if b >= a:
            out.append((a, b))
    return out


def coalesce(spans: list[tuple[float, float]], gap: float = CUE_GAP) -> list[list[float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def cue_rows(run_dirs: list[Path]) -> tuple[list[list[float]], list[Path]]:
    spans: list[tuple[float, float]] = []
    sources: list[Path] = []
    for d in run_dirs:
        for p in sorted(d.rglob("*.vtt")):
            got = vtt_spans(p)
            if got:
                spans.extend(got)
                sources.append(p)
    return coalesce(spans), sources


def run_info(run_dirs: list[Path]) -> tuple[int | None, str | None]:
    """(duration, video id) as the run directory itself recorded them."""
    for d in run_dirs:
        for p in sorted(d.rglob("video.info.json")):
            text, _ = safe_read(p)
            if text is None:
                continue
            try:
                blob = json.loads(text)
            except json.JSONDecodeError:
                continue
            dur = blob.get("duration")
            return (int(dur) if isinstance(dur, (int, float)) and dur > 0 else None,
                    blob.get("id"))
    return None, None


def build_manifest(video_id: str, run_dirs: list[Path]) -> tuple[str, dict]:
    """Build the manifest text. Raises ValueError on anything unbuildable.

    Refusing beats emitting: a manifest with no duration silently disables the
    range check, both selectivity readouts and the coverage ledger, and a
    manifest built against the wrong run directory resolves a note's anchors
    against another video's frames.
    """
    frames, unstamped, collisions = frame_rows(run_dirs)
    cues, cue_sources = cue_rows(run_dirs)
    duration, source_id = run_info(run_dirs)
    if source_id and source_id != video_id:
        raise ValueError(f"run directory declares video id {source_id}, "
                         f"--build says {video_id}")
    if duration is None:
        ends = [max(frames)] if frames else []
        ends += [int(cues[-1][1])] if cues else []
        duration = max(ends) if ends else 0
    if duration <= 0:
        raise ValueError("no duration: no video.info.json, no frames and no "
                         "cues, so nothing dates this manifest")

    namespaces = [n for n, present in (("frames", frames), ("cues", cues)) if present]
    head = [
        f"# video_id\t{video_id}",
        f"# duration\t{duration}",
        f"# namespaces\t{','.join(namespaces)}",
    ]
    head += [f"# source\t{d}" for d in run_dirs]
    head += [f"# unstamped\t{p.name}" for p in unstamped]
    head += [f"# collision\t{s}\tkept {a.name}\tdropped {b.name}"
             for s, a, b in collisions]
    rows = [f"frame\t{s}\t{s}\t{frames[s].name}\t{sha256(frames[s])}"
            for s in sorted(frames)]
    rows += [f"cue\t{a:.3f}\t{b:.3f}\tcue_{i:04d}\t-"
             for i, (a, b) in enumerate(cues)]
    text = "\n".join(head + [MANIFEST_HEADER] + rows) + "\n"
    stats = {"video_id": video_id, "duration": duration, "frames": len(frames),
             "cues": len(cues), "unstamped": len(unstamped),
             "collisions": len(collisions), "cue_sources": len(cue_sources),
             "namespaces": namespaces}
    return text, stats


# --------------------------------------------------------------------------
# slice 13: adopting a run before the OS reaps it
# --------------------------------------------------------------------------
# DEFER:YTN-MANIFEST-AT-WRITE-TIME. Four of eight notes can never be reconciled
# because `watch.py` defaulted to the system temp dir and the runs were deleted.
# The loss is not that the manifest was late -- it is that the manifest names
# files under a directory the OS owns, so `--verify-artifacts` becomes a bluff
# the moment the machine reboots.
#
# Copying is not re-extracting. A fresh extraction picks DIFFERENT keyframes and
# would grade a note against a run it was never written from, which is why
# UNRESOLVABLE_RUNS is permanent. A byte copy preserves the exact frames, and
# `--adopt` proves it did by re-hashing every file it wrote.
#
# The video file is excluded by default: at 60-230 MB it dominates the copy and,
# unlike a keyframe set, it is deterministic and re-downloadable. `--with-video`
# keeps it, which is worth it when a note may later need a second re-extracted
# at a higher resolution (see docs/reviews/slice10-blanket-class-reach.md).
RUNS_ROOT = POLICY.runs_root()
VIDEO_SUFFIXES = (".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".wav")


def temp_owned(p: Path) -> bool:
    """True when the OS, not the user, decides how long this path lives."""
    roots = [Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve(),
             Path("/var/folders").resolve()]
    try:
        rp = p.resolve()
    except OSError:
        return False
    return any(rp == r or r in rp.parents for r in roots)


def verify_copy(src: Path, dst: Path) -> list[str]:
    """One complaint line when the copy is not the file it claims to be.

    Split out from `adopt_run` so it can be exercised on a pair that really
    differs: `shutil.copy2` will not corrupt a file on demand, and a test that
    can only assert the happy path pins nothing.
    """
    if not dst.is_file():
        return [f"E-ADOPT-MISSING {dst} was not written"]
    if sha256(dst) != sha256(src):
        return [f"E-ADOPT-CORRUPT {dst} does not match {src}"]
    return []


def adopt_run(video_id: str, run_dirs: list[Path], dest_root: Path,
              with_video: bool = False, allow_temp: bool = False,
              copier=None) -> tuple[list[Path], list[str]]:
    """Copy each run under dest_root/<video_id>/<name>/ and verify every byte.

    Returns (durable run dirs, complaint lines). A complaint is never silent and
    never partial-credit: a file whose copy does not re-hash to the source is
    reported and the destination is left in place for inspection, because a
    half-trusted evidence directory is worse than a named broken one.

    THE DESTINATION IS RECONCILED, NOT JUST WRITTEN (slice-13 correctness lane,
    BLOCKER). `verify_copy` proves the files this call WROTE are faithful and
    says nothing about what was already there. Adopting two different runs under
    one name -- the normal path when `watch.py` is re-run with the same
    `--out-dir` label -- left the older run's frames in place, and `--build`
    then recorded them with a valid sha256 that `--verify-artifacts` confirms
    for ever. A frame the note was never written from, certified by every check
    downstream. Every destination file the sources do not have is now
    `E-ADOPT-EXTRA` and the build does not happen.

    `copier` is an injection point for the selftest only. `shutil.copy2` cannot
    be asked to corrupt a file, so without it the one line that turns
    `verify_copy` into this function's guarantee could be deleted with all 132
    cases still green.
    """
    copy = copier or shutil.copy2
    bad: list[str] = []
    if video_id in ("", ".", "..") or "/" in video_id or "\\" in video_id:
        # Ids may start with `-` (the corpus has one) but are never path
        # components: `--adopt=../ESCAPED` wrote outside the declared root.
        return [], [f"E-ADOPT-ID {video_id!r} is not usable as a directory name"]
    dest_root = dest_root / video_id
    # `allow_temp` exists for the selftest, whose own root IS a temp dir. The
    # CLI never passes it; the refusal below is what the corpus actually runs.
    if not allow_temp and temp_owned(dest_root):
        return [], [f"refusing to adopt into {dest_root}: the OS owns that path"]
    out: list[Path] = []
    for src in run_dirs:
        dst = dest_root / src.name
        if dst.resolve() == src.resolve():
            out.append(dst)
            continue
        # `wrote` is what this call verified; `known` also holds the paths the
        # sources have but this call deliberately skipped, so a video file left
        # by an earlier `--with-video` adoption is not reported as a stranger.
        wrote: set[Path] = set()
        known: set[Path] = set()
        for f in sorted(src.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(src)
            known.add(rel)
            if not with_video and f.suffix.lower() in VIDEO_SUFFIXES:
                continue
            target = dst / rel
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                copy(f, target)
            except OSError as exc:
                # An exception here used to abort with a traceback and no
                # complaint line, leaving a half-written destination that reads
                # like an honest failure. Exit 1 must mean "adoption complained".
                bad.append(f"E-ADOPT-IO {f} -> {target}: {exc}")
                continue
            wrote.add(rel)
            bad.extend(verify_copy(f, target))
        for p in sorted(dst.rglob("*")) if dst.is_dir() else []:
            if p.is_file() and p.relative_to(dst) not in known:
                bad.append(f"E-ADOPT-EXTRA {p} is in the destination and in no "
                           f"source; adopt into an empty directory")
        out.append(dst)
    return out, bad


# --------------------------------------------------------------------------
# reading a manifest back
# --------------------------------------------------------------------------

class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self.video_id: str | None = None
        self.duration: int = 0
        self.namespaces: set[str] = set()
        self.frames: dict[int, str] = {}
        self.cues: list[tuple[float, float]] = []
        self.sources: list[str] = []
        self.defects: list[str] = []
        self.fatal = False

    @property
    def cue_coverage(self) -> float:
        if not self.duration:
            return 0.0
        return min(1.0, sum(b - a for a, b in self.cues) / self.duration)

    @property
    def frame_coverage(self) -> float:
        if not self.duration:
            return 0.0
        return min(1.0, len(self.frames) / self.duration)

    @property
    def cues_vacuous(self) -> bool:
        return self.cue_coverage >= VACUOUS_COVERAGE

    def resolves(self, namespace: str) -> bool:
        """Whether this manifest can actually decide an anchor of that class."""
        if namespace == "frames":
            return bool(self.frames)
        return bool(self.cues) and not self.cues_vacuous


def read_manifest(path: Path) -> Manifest:
    man = Manifest(path)
    rel = f"notes/anchors/{path.name}"
    text, why = safe_read(path)
    if text is None:
        man.defects.append(f"{rel}:1 E-MANIFEST-UNREADABLE {why}")
        man.fatal = True
        return man
    seen: set[int] = set()
    for i, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if line.startswith("#"):
            parts = line.lstrip("# ").split("\t")
            if len(parts) >= 2:
                key, value = parts[0].strip(), parts[1].strip()
                if key == "video_id":
                    man.video_id = value
                elif key == "duration" and value.isdigit():
                    man.duration = int(value)
                elif key == "namespaces":
                    man.namespaces = {n for n in value.split(",") if n}
                elif key == "source":
                    man.sources.append(value)
            continue
        if line == MANIFEST_HEADER:
            continue
        cols = line.split("\t")
        if len(cols) != 5 or cols[0] not in KINDS:
            man.defects.append(f"{rel}:{i} E-MANIFEST-ROW malformed row")
            continue
        try:
            start, end = float(cols[1]), float(cols[2])
        except ValueError:
            man.defects.append(f"{rel}:{i} E-MANIFEST-ROW non-numeric span")
            continue
        if cols[0] == "frame":
            sec = int(start)
            if sec in seen:
                man.defects.append(f"{rel}:{i} E-MANIFEST-ROW duplicate frame "
                                   f"second {sec}; a second names one artifact")
                continue
            seen.add(sec)
            man.frames[sec] = cols[3]
        else:
            man.cues.append((start, end))
    man.cues.sort()

    if not man.duration:
        # Zero doubles as "unknown" and "zero-length", and it silently disables
        # the range check, both selectivity readouts and the coverage ledger.
        man.defects.append(f"{rel}:1 E-MANIFEST-NO-DURATION nothing dates this "
                           f"manifest, so three checks cannot run")
    past = sorted(s for s in man.frames if man.duration and s > man.duration)
    if past:
        man.defects.append(f"{rel}:1 E-MANIFEST-RANGE {len(past)} frame(s) past "
                           f"the {man.duration}s runtime: "
                           f"{', '.join(hms(s) for s in past[:4])}")
    # A declared namespace with no rows would silently pass every anchor bound
    # to it; a namespace with rows but undeclared would silently check nothing.
    for name, rows in (("frames", man.frames), ("cues", man.cues)):
        if (name in man.namespaces) != bool(rows):
            man.defects.append(f"{rel}:1 E-MANIFEST-NAMESPACE {name} "
                               f"declared={name in man.namespaces} "
                               f"rows={len(rows)}")
    return man


def verify_artifacts(man: Manifest, root: Path) -> list[str]:
    """Re-stat and re-hash every frame the manifest names.

    Without this the manifest is self-certifying: once written, its claims
    about a run in /tmp survive the run's deletion and nothing notices.
    """
    rel = f"notes/anchors/{man.path.name}"
    text, _ = safe_read(man.path)
    recorded = {}
    for line in (text or "").splitlines():
        cols = line.split("\t")
        if len(cols) == 5 and cols[0] == "frame":
            recorded[cols[3]] = cols[4]
    dirs = [Path(s) for s in man.sources]
    out = []
    missing = changed = 0
    for name, sha in recorded.items():
        hit = next((p for d in dirs for p in d.rglob(name) if p.is_file()), None)
        if hit is None:
            missing += 1
        elif sha256(hit) != sha:
            changed += 1
    if missing:
        out.append(f"{rel}:1 E-MANIFEST-ARTIFACT {missing} of {len(recorded)} "
                   f"frame(s) named by this manifest are gone from "
                   f"{', '.join(man.sources) or 'nowhere'}")
    if changed:
        out.append(f"{rel}:1 E-MANIFEST-ARTIFACT {changed} frame(s) no longer "
                   f"hash to the recorded sha256")
    return out


def sweep_manifests(root: Path, live: set[str]) -> list[str]:
    """Manifests that no note claims, and two manifests for one video.

    Mirrors resolve_note.orphan_sidecars: the manifest is addressed only by
    video_id, so renaming that field detaches it silently.
    """
    out = []
    by_id: dict[str, list[str]] = {}
    for man in sorted((root / POLICY.anchors_dir()).glob("*.tsv")):
        text, _ = safe_read(man)
        declared = man.stem
        for line in (text or "").splitlines():
            if line.startswith("# video_id"):
                parts = line.split("\t")
                if len(parts) > 1:
                    declared = parts[1].strip()
                break
        by_id.setdefault(declared, []).append(man.name)
        if declared not in live:
            out.append(f"notes/anchors/{man.name}:1 E-MANIFEST-ORPHAN no note "
                       f"declares video_id {declared}")
    for vid, names in sorted(by_id.items()):
        if len(names) > 1:
            out.append(f"notes/anchors/{names[1]}:1 E-MANIFEST-DUPLICATE "
                       f"{len(names)} manifests declare {vid}: "
                       f"{', '.join(names)}")
    return out


# --------------------------------------------------------------------------
# reading a note
# --------------------------------------------------------------------------

def note_blocks(body: str, start_line: int) -> list[dict]:
    """Markdown items, one per block: bullet, table row, quote line, heading,
    paragraph. Wrapped continuation lines fold in; nothing else does.

    `verbatim` marks a fenced region, whose anchors are quoted text rather than
    a claim of the note's own.
    """
    out: list[dict] = []
    section = ""
    buf: list[tuple[int, str]] = []
    verbatim = False

    def flush() -> None:
        nonlocal buf
        if buf:
            out.append({"lines": list(buf), "section": section,
                        "verbatim": verbatim})
            buf = []

    for i, line in enumerate(body.split("\n")):
        ln = start_line + i
        if RE_FENCE.match(line):
            flush()
            verbatim = not verbatim
            continue
        if verbatim:
            if line.strip():
                buf.append((ln, line))
            continue
        head = RE_HEADING.match(line)
        if head:
            flush()
            if head.group(1) == "##":
                section = head.group(2)
            buf.append((ln, line))
            flush()
            continue
        if (not line.strip() or RE_LIST_ITEM.match(line)
                or RE_TABLE_ROW.match(line) or RE_QUOTE.match(line)):
            flush()
        if line.strip():
            buf.append((ln, line))
    flush()
    return out


def strip_marks(text: str) -> tuple[str, list[int]]:
    """Delete the renderer's own marks and return (clean text, index map).

    `index[i]` is where `clean[i]` sat in the original, so a caller can bind on
    the clean string and still report original offsets. Deleting rather than
    blanking matters because DISTANCE decides which anchor a label binds to:
    the slice-8 coverage lane showed ``[01:12]` `ORPHAN` `SPOKEN`` pushing its
    own label 10 characters away, far enough for the label to jump to the next
    anchor and leave three author-labelled anchors as MULTI. A mark this
    pipeline wrote must not change what the pipeline concludes.

    An anchor is PROTECTED from the deletion. `ORPHAN_MARK` starts with a
    backtick, so on ``[00:30]`ORPHAN`` the scan matched at the anchor's CLOSING
    backtick and ate it: the anchor still existed in `note_anchors`' per-line
    scan and no longer existed in `joined`, so the lookup died `KeyError` with
    no defect line, no partition and no summary -- the whole run aborted. The
    slice-10 correctness lane fuzzed 60,000 items and crashed 784 of them; the
    corpus holds none of that shape, which is exactly why it had to be a test.
    """
    protected = bytearray(len(text))
    for m in RE_ANCHOR.finditer(text):
        for j in range(m.start(), m.end()):
            protected[j] = 1
    clean: list[str] = []
    index: list[int] = []
    i = 0
    while i < len(text):
        for mark in (ORPHAN_MARK, DEMOTED_MARK):
            if text.startswith(mark, i) and not any(protected[i:i + len(mark)]):
                i += len(mark)
                break
        else:
            clean.append(text[i])
            index.append(i)
            i += 1
    return "".join(clean), index


def is_glue(gap: str) -> bool:
    """True when nothing but joining punctuation separates a label from its
    anchor, on ONE line. The renderer's marks are already gone by here.

    A BARE line break is not glue. Notes wrap near 80 columns, so a line break
    lands anywhere, and letting whitespace alone join across one was a live
    false negative: an `ON-SCREEN` anchor ending a line and a `SPOKEN` anchor
    starting the next merged into one group, took `SPOKEN`, and landed in a
    namespace measured VACUOUS on 4 of 4 manifests -- a loud MULTI turned into
    a silent pass. A deliberate joiner still survives a wrap: a dash left at
    the end of a line joins the anchor on the next one, because a range is one
    range whatever the column count says, and only whitespace-and-nothing-else
    across a newline is refused. That wrapped form is a selftest fixture, not a
    corpus quote -- the corpus writes its ranges inline.
    """
    if "\n" in gap and not gap.strip():
        return False
    return bool(RE_GLUE.match(gap))


def bind_by_touch(joined: str) -> list[tuple[list[tuple[int, int]], set[str]]]:
    """Group anchors by glue, then hand each label to the group it TOUCHES.

    Split out of `bind_classes` so the counterfactual can be measured with the
    shipped rule rather than a second copy of it: slice 9 shipped a headline
    table computed by a throwaway script's own logic and a lane killed it.
    Returns `[(members, classes-that-reached-this-group)]`, offsets into
    `joined`.
    """
    anchors = [(m.start(), m.end()) for m in RE_ANCHOR.finditer(joined)]
    tokens = [(m.start(), m.end(), m.group(1))
              for m in RE_CLASS_TOKEN.finditer(joined)]

    groups: list[list[tuple[int, int]]] = []
    for astart, aend in anchors:
        if groups and is_glue(joined[groups[-1][-1][1]:astart]):
            groups[-1].append((astart, aend))
        else:
            groups.append([(astart, aend)])

    bound: list[set[str]] = [set() for _ in groups]
    for ts, te, cls in tokens:
        best: tuple[int, int, int] | None = None
        for gi, members in enumerate(groups):
            gstart, gend = members[0][0], members[-1][1]
            if gend <= ts:
                dist, gap, side = ts - gend, joined[gend:ts], 0
            elif gstart >= te:
                dist, gap, side = gstart - te, joined[te:gstart], 1
            else:
                continue
            if not is_glue(gap):
                continue
            if best is None or (dist, side) < (best[0], best[1]):
                best = (dist, side, gi)
        if best is not None:
            bound[best[2]].add(cls)
    return list(zip(groups, bound))


def mark_free(raw: str) -> tuple[str, list[int]]:
    """`strip_marks`, but never at the cost of losing an anchor.

    Deleting a mark can FUSE the text around it into a stamp that was not an
    anchor before, or split one that was: on
    ``- `[0:30]<!-- auto-demoted -->`[0:30]` `` the opening anchor has no
    closing backtick, the comment is not protected, and deleting it leaves a
    clean string whose anchors sit at offsets the raw scan never saw --
    `note_anchors` then died `KeyError: 30`, aborting the whole run with no
    defect line, exactly like the slice-10 crash and from the opposite
    direction. The slice-12 correctness lane found it by fuzzing.

    So the deletion is CONDITIONAL: it applies only while the two scans agree
    on where the anchors are. When they disagree, binding runs on the raw text
    with an identity map, which is a worse distance estimate and a correct one.
    """
    clean, index = strip_marks(raw)
    before = [m.start() for m in RE_ANCHOR.finditer(raw)]
    after = [index[m.start()] for m in RE_ANCHOR.finditer(clean)]
    if before == after:
        return clean, index
    return raw, list(range(len(raw)))


def bind_classes(spans: list[tuple[int, str, int]]) -> dict[int, str]:
    """Evidence class per anchor POSITION: a label binds to the anchor it
    touches, and to nothing further away.

    ONE class in the item still labels every anchor in it, wherever it sits --
    the satori note writes "Continuity, all `ON-SCREEN`:" and then names three
    frames a sentence apart, and means all three. Only a TWO-class item is
    split, because only then is there a question. The first
    version called every two-class item unattributable, which buried corpus
    anchors the note had already labelled one by one: the langfuse note writes
    ``[07:04]` `SPOKEN` `ON-SCREEN` `[08:04]`` on a single bullet.

    Splitting is by touch, not by distance. Anchors joined to each other by
    glue alone -- whitespace, `/`, a range dash, `+`, a table pipe, an `ORPHAN`
    mark -- are ONE group and take one class, because ``[03:43]` / `[04:59]`
    `ON-SCREEN`` labels both cards, not the second one. Each label then reaches
    for the nearest group on either side, again through glue only, and one word
    of prose in the gap ends the reach. A nearest-token rule was tried first
    and bound the densest note's then-unlabelled ``[16:12]`` to an `ON-SCREEN`
    card two sentences upstream, printing a defect for a frame the note never
    claimed; slice 9 has since labelled that anchor `SPOKEN` from the captions. A group no label touches stays MULTI: the item said two things and
    did not say which is this one's.

    Binding runs on the text with the renderer's marks DELETED, so an `ORPHAN`
    the tool wrote cannot push a label onto a different anchor; results are
    reported at the original offsets. Keys are original offsets either way.

    `spans` is (line number, line text, offset of that line in the item).
    """
    raw = "\n".join(text for _, text, _ in spans)
    joined, index = mark_free(raw)
    tokens = [(m.start(), m.end(), m.group(1))
              for m in RE_CLASS_TOKEN.finditer(joined)]
    anchors = [(m.start(), m.end()) for m in RE_ANCHOR.finditer(joined)]
    present = {c for _, _, c in tokens}
    if len(present) < 2:
        one = next(iter(present)) if present else "NONE"
        return {index[a]: one for a, _ in anchors}

    out: dict[int, str] = {}
    for members, found in bind_by_touch(joined):
        # CONTESTED is MULTI's other half: two labels both touch this group, so
        # the note said which and said it twice. It rolls up to the same
        # disposition, but the summary counts them apart -- "nobody labelled
        # this" and "the tool could not choose" are different piles of work.
        cls = (found.pop() if len(found) == 1
               else ("CONTESTED" if found else "MULTI"))
        for astart, _ in members:
            out[index[astart]] = cls
    return out


def blanket_reach(spans: list[tuple[int, str, int]]) -> set[int]:
    """Original offsets of anchors a ONE-class item labels WITHOUT the label
    touching them. Report-only, and it will stay that way.

    The obvious fix -- run the touch rule on the one-class branch too -- was
    measured against the corpus and is refuted by it. The satori note writes
    "all `SPOKEN`: `[00:00]` … `[01:12]` …" and "Continuity, all `ON-SCREEN`:",
    the langfuse note writes ``[20:27]` `ON-SCREEN` `[20:34]``, and the vc-pmf
    note writes "Two portfolio references are `INFERRED` … at `[51:30]`, and
    Even at `[1:01:49]`." Touch strips a class those notes state in words, which
    is the false red light the plan says kills a checker.

    The whole population was read anchor by anchor on 2026-08-05 and three were
    repaired in the notes; touch would have fixed none of the three. The counts
    and the per-anchor verdicts live in
    `docs/reviews/slice10-blanket-class-reach.md`, with the evidence each
    verdict rests on, because a number quoted in a docstring cannot be
    re-derived and this one drifts every time a note is edited. What is
    derivable here is the live population: this function, and the
    `# blanket reach:` line it feeds.
    """
    raw = "\n".join(text for _, text, _ in spans)
    joined, index = mark_free(raw)
    if len({m.group(1) for m in RE_CLASS_TOKEN.finditer(joined)}) != 1:
        return set()
    return {index[astart] for members, found in bind_by_touch(joined)
            if not found for astart, _ in members}


def note_anchors(body: str, start_line: int, video_id: str = "") -> list[dict]:
    """Every anchor, with the evidence class bound to that anchor.

    Attribution never crosses an item boundary. The first version folded a
    whole table into one block, so a single `ON-SCREEN` cell bound every anchor
    in the table -- three of them in the marketing-agents note -- which is the
    fabricated red light the plan says kills a checker.
    """
    out: list[dict] = []
    for block in note_blocks(body, start_line):
        spans: list[tuple[int, str, int]] = []
        base = 0
        for ln, text in block["lines"]:
            spans.append((ln, text, base))
            base += len(text) + 1
        bound = bind_classes(spans)
        blanket = blanket_reach(spans)
        # the item's own words, carried on every anchor so the caption witness
        # has one definition of "this item" instead of two
        item_words = content_words("\n".join(t for _, t, _ in spans))
        # A reference reaches its own line and the next TWO, which is
        # `resolve_note.check_anchors`' rule and therefore the repo's oracle
        # for "foreign". Per-line alone was narrower than the oracle and the
        # satori note broke it: the reference is long enough to fill its line,
        # so the anchor it introduces wraps onto the next one and was read as
        # this video's second. Reaching the whole item would be the one-word
        # bypass the per-line rule exists to stop; two lines is the wrap.
        near: dict[int, set[str]] = {}
        for idx, (_, text, _) in enumerate(spans):
            refs = {Path(ref).stem.split("--")[-1]
                    for ref in RE_NOTE_REF.findall(text)}
            refs.discard(video_id)
            for reach in (idx, idx + 1, idx + 2):
                if refs:
                    near.setdefault(reach, set()).update(refs)
        for pos, (ln, text, off) in enumerate(spans):
            others = near.get(pos, set())
            for m in RE_ANCHOR.finditer(text):
                raw = m.group(1)
                sec = 0
                for p in (int(q) for q in raw.split(":")):
                    sec = sec * 60 + p
                cls = bound[off + m.start()]
                out.append({"anchor": raw, "seconds": sec,
                            "class": "VERBATIM" if block["verbatim"] else cls,
                            "section": block["section"], "line": ln,
                            "foreign": bool(others),
                            # a fenced anchor is VERBATIM and a foreign one
                            # belongs to another video; neither ever takes this
                            # item's class, so counting either as blanket reach
                            # inflates a population nobody can act on
                            "blanket": (not block["verbatim"] and not others
                                        and (off + m.start()) in blanket),
                            "item": item_words})
    return out


def nearest(seconds: int, ordered: list[int]) -> int | None:
    if not ordered:
        return None
    i = bisect.bisect_left(ordered, seconds)
    cand = [ordered[j] for j in (i - 1, i) if 0 <= j < len(ordered)]
    return min(cand, key=lambda c: abs(seconds - c))


# --------------------------------------------------------------------------
# the caption witness
#
# This lives HERE, not in `spoken_vote.py`, because the cues namespace is this
# module's and it never had a resolver worth the name: cue spans cover 98-100%
# of runtime, so containment cannot fail and the namespace is announced
# VACUOUS. A CONTENT witness -- do the item's own words appear in what was said
# near the cited second -- is the resolver that namespace was missing, and
# `spoken_vote.py` imports these rather than keeping a second copy. Slice 9's
# review found `note_items` re-implementing `note_anchors` and drifting; one
# implementation is the fix.
# --------------------------------------------------------------------------

CAPTION_DIR = POLICY.caption_dir()
CAPTION_HEADER = "start\tend\ttext"

# Window either side of the cited second. Auto-caption cue boundaries do not
# align with sentences, so a claim's words straddle two or three cues.
WINDOW = 12
# An item with fewer content words than this cannot be scored honestly: the
# denominator is too small for a fraction to mean anything.
MIN_ITEM_WORDS = 6
# The rule, calibrated against the shift control rather than taste. Hits alone
# lets a long item pass on coincidence; fraction alone lets a four-word item
# pass on one word.
MIN_HITS = 4
MIN_FRACTION = 0.25

# Words that carry no evidence. Deliberately small: an aggressive stoplist is
# how the rejected absence floor lost "$1 in, $5 out ATM".
STOPWORDS = set("""
the a an and or but if then than that this these those of to in on for with as at by
from into over under about after before between during is are was were be been being it
its he she they them his her their you your we our me my not no so such one two three
four five six seven eight nine ten first second third what which who whom when where why
how all any both each few more most other some only own same too very can will just
should now also has have had do does did done get got make made say says said like out
up down off again further here there once because while against above below
""".split())
RE_MARKUP = re.compile(r"`[^`]*`|<!--.*?-->|\{\{[^}]*\}\}|[*_>#|]")
RE_WORD = re.compile(r"[a-z][a-z'-]{3,}")


def content_words(text: str) -> set[str]:
    """The words that could witness anything. Backticked spans go first: they
    hold anchors and evidence-class tokens, which are the note's machinery.

    A hyphenated compound yields its parts as well as itself. Notes write
    "body-shaped" where the speaker says "body shaped", and the first version
    lost five real witnesses to that alone.
    """
    out: set[str] = set()
    for word in RE_WORD.findall(RE_MARKUP.sub(" ", text).lower()):
        if word not in STOPWORDS:
            out.add(word)
        if "-" in word:
            out.update(p for p in word.split("-")
                       if len(p) >= 4 and p not in STOPWORDS)
    return out


def caption_path(root: Path, video_id: str) -> Path:
    return root / CAPTION_DIR / f"{video_id}.tsv"


def read_index(path: Path) -> list[tuple[float, float, str]]:
    text, _ = safe_read(path)
    rows = []
    for line in (text or "").splitlines():
        if line.startswith("#") or line == CAPTION_HEADER:
            continue
        parts = line.split("\t", 2)
        if len(parts) == 3:
            try:
                rows.append((float(parts[0]), float(parts[1]), parts[2]))
            except ValueError:
                continue
    return rows


def index_video_id(path: Path) -> str:
    """The `# video_id` header `write_index` puts at the top of a caption index.

    It was written and never read, so an index rotated onto the wrong video was
    used anyway and the anchors it "witnessed" were witnessed by another talk.
    Slice 9 killed exactly this on the control side by rotating the indexes;
    slice 11's correctness lane found the same hole open on the check side.
    """
    text, _ = safe_read(path)
    for line in (text or "").splitlines():
        if not line.startswith("#"):
            break
        parts = line.split("\t", 1)
        if parts[0].strip() == "# video_id" and len(parts) == 2:
            return parts[1].strip()
    return ""


def spoken_at(rows, second: float, window: int = WINDOW) -> set[str]:
    said = " ".join(t for a, b, t in rows
                    if b >= second - window and a <= second + window)
    return content_words(said)


def witness(item: set[str], rows, second: float,
            window: int = WINDOW) -> tuple[int, float]:
    hit = item & spoken_at(rows, second, window)
    return len(hit), (len(hit) / len(item) if item else 0.0)


def promotable(hits: int, fraction: float) -> bool:
    return hits >= MIN_HITS and fraction >= MIN_FRACTION


def in_cue(seconds: int, cues: list[tuple[float, float]]) -> bool:
    starts = [a for a, _ in cues]
    i = bisect.bisect_right(starts, seconds) - 1
    return any(0 <= j < len(cues) and cues[j][0] <= seconds <= cues[j][1]
               for j in (i, i + 1))


def coverage_ledger(frames: list[int], cited: set[int],
                    duration: int) -> tuple[int, int, tuple[int, int]]:
    """(uncited frame count, longest span holding an uncited frame, its bounds).

    R6 in one number: a frame that was extracted and never cited is content the
    run looked at and the note never reported. Only FRAME citations count -- a
    note may cover that runtime with SPOKEN anchors and still never look at the
    screen, which is the defect.

    A span is only charged when it actually contains an uncited frame. Measuring
    raw runtime between citations red-lights a note that cited every frame there
    was, which is the false light that gets the whole check switched off. For
    the same reason --max-gap is opt-in: on a talking-head video with no slides
    a large gap is correct.
    """
    hit = sorted(s for s in frames if s in cited)
    miss = sorted(s for s in frames if s not in cited)
    worst, bounds = 0, (0, 0)
    for s in miss:
        i = bisect.bisect_left(hit, s)
        lo = hit[i - 1] if i > 0 else 0
        hi = hit[i] if i < len(hit) else duration
        if hi - lo > worst:
            worst, bounds = hi - lo, (lo, hi)
    return len(miss), worst, bounds


def blank_row(rel: str) -> dict:
    row = {"file": rel, "video_id": "", "manifest": "none", "anchors": 0,
           "frames": 0, "cited_frames": 0, "uncited_frames": 0,
           "longest_gap": 0, "frame_selectivity": "", "cue_selectivity": "",
           "class_exempt": "", "blanket": 0}
    row.update({d: 0 for d in DISPOSITIONS})
    return row


def binding_control(files: list[Path]) -> tuple[list[str], bool]:
    """The metamorphic control on `bind_classes`, shipped rather than described.

    Put a word of prose in every gap between a label and an anchor. A rule that
    binds by TOUCH must lose every binding; a rule that binds by DISTANCE keeps
    them all. The first control written for this slice reversed the order of the
    class tokens instead, and three review lanes independently showed it proves
    nothing: the nearest-token rule that was thrown out for printing false
    defects scored BETTER on it (110 of 128 against 99), and its published
    numbers moved whenever the corpus moved. This one separates the two rules
    completely -- 100% against 0% -- and it is a property of the shipped rule
    alone, so it can be re-run by anyone at any commit.
    """
    bound = lost = items = 0
    for path in files:
        text, _ = safe_read(path)
        split = split_frontmatter(text or "")
        if split is None:
            continue
        frontmatter, body = split
        for block in note_blocks(body, len(frontmatter.split("\n")) + 3):
            spans: list[tuple[int, str, int]] = []
            base = 0
            for ln, line in block["lines"]:
                spans.append((ln, line, base))
                base += len(line) + 1
            joined = "\n".join(t for _, t, _ in spans)
            if len({m.group(1) for m in RE_CLASS_TOKEN.finditer(joined)}) < 2:
                continue
            items += 1
            before = bind_classes(spans)
            out, last = [], 0
            for m in RE_CLASS_TOKEN.finditer(joined):
                out.append(joined[last:m.start()])
                out.append(" word " + m.group(0) + " word ")
                last = m.end()
            out.append(joined[last:])
            mutant = "".join(out)
            mspans, base = [], 0
            for i, line in enumerate(mutant.split("\n")):
                mspans.append((i, line, base))
                base += len(line) + 1
            after = bind_classes(mspans)
            keys, mkeys = sorted(before), sorted(after)
            for i, k in enumerate(keys):
                if before[k] in ("MULTI", "CONTESTED", "NONE"):
                    continue
                bound += 1
                if i < len(mkeys) and after[mkeys[i]] in ("MULTI", "CONTESTED"):
                    lost += 1
    ok = bound > 0 and lost == bound
    return ([f"# control: prose inserted in every label/anchor gap of "
             f"{items} two-class item(s)",
             f"# {lost} of {bound} binding(s) lost, "
             f"{'100%' if ok else f'{100.0 * lost / bound:.1f}%' if bound else 'n/a'}"
             f" -- a touch rule must lose every one",
             f"# control {'HOLDS' if ok else 'FAILED'}"], ok)


def check_note(path: Path, root: Path, require_manifest: bool = True,
               max_gap: int | None = None, require_class: bool = True,
               artifacts: bool = False,
               require_touched: bool = True) -> tuple[list[str], dict]:
    rel = str(path.relative_to(root) if path.is_relative_to(root) else path)
    row = blank_row(rel)

    text, why = safe_read(path)
    if text is None:
        return [f"{rel}:1 E-READ {why}"], row
    split = split_frontmatter(text)
    if split is None:
        return [f"{rel}:1 E-FRONTMATTER no YAML frontmatter block"], row
    frontmatter, body = split
    body_start_line = len(frontmatter.split("\n")) + 3

    m = RE_VIDEO_ID.search(frontmatter)
    if not m:
        return [f"{rel}:1 E-NO-VIDEO-ID cannot address a manifest"], row
    video_id = m.group(1)
    row["video_id"] = video_id
    exempt = video_id in UNRESOLVABLE_RUNS

    ancs = note_anchors(body, body_start_line, video_id)
    row["anchors"] = len(ancs)
    row["blanket"] = sum(1 for a in ancs if a["blanket"])

    mpath = manifest_path(root, video_id)
    man = read_manifest(mpath) if mpath.is_file() else None
    defects: list[str] = []
    if man is not None:
        defects += man.defects
        row["manifest"] = "present"
        row["frames"] = len(man.frames)
        if man.duration:
            row["frame_selectivity"] = f"{man.frame_coverage:.3f}"
            row["cue_selectivity"] = f"{man.cue_coverage:.3f}"
        if not man.fatal:
            if man.video_id != video_id:
                defects.append(f"{rel}:1 E-MANIFEST-VIDEO manifest declares "
                               f"{man.video_id}, note declares {video_id}")
            note_seconds = parse_duration(frontmatter)
            if (note_seconds and man.duration
                    and abs(note_seconds - man.duration) > DURATION_TOL):
                defects.append(f"{rel}:1 E-MANIFEST-DURATION manifest "
                               f"{man.duration}s, note {note_seconds}s")
        if artifacts:
            defects += verify_artifacts(man, root)
    else:
        row["manifest"] = "missing"

    # ---- one disposition per anchor, so the summary is a partition
    # An index file that exists must be usable and must belong to THIS video.
    # Both failures used to read as "no witness", which is indistinguishable
    # from an honest miss and is how a rotated index passes.
    cpath = caption_path(root, video_id)
    cue_index = read_index(cpath) if cpath.is_file() else []
    if cpath.is_file():
        if not cue_index:
            defects.append(f"{rel}:1 E-CAPTION-EMPTY {cpath.name} exists but "
                           f"holds no usable cue rows, so every SPOKEN anchor "
                           f"here reads as unwitnessed")
        declared = index_video_id(cpath)
        if declared and declared != video_id:
            defects.append(f"{rel}:1 E-CAPTION-VIDEO {cpath.name} declares "
                           f"{declared}, note declares {video_id}; a witness "
                           f"from another talk is not a witness")
    # Which seconds this note labels, and with what. Only a bound anchor of
    # this video donates: VERBATIM is quoted rather than claimed, and a foreign
    # anchor is another video's second entirely.
    classed: dict[int, set[str]] = {}
    for a in ancs:
        if (not a["foreign"] and a["class"] in EVIDENCE_CLASSES):
            classed.setdefault(a["seconds"], set()).add(a["class"])
    unresolved_lines = []
    for a in ancs:
        cls, sec = a["class"], a["seconds"]
        if a["foreign"]:
            a["disposition"] = "foreign"
        elif cls == "VERBATIM":
            a["disposition"] = "verbatim"
        elif cls in ("NONE", "MULTI", "CONTESTED"):
            a["disposition"] = ("cross-referenced"
                                if len(classed.get(sec, ())) == 1
                                else "unattributable")
        elif cls == PIXEL_CLASS:
            if man is None or man.fatal or not man.resolves("frames"):
                a["disposition"] = "unchecked"
            elif sec in man.frames:
                a["disposition"] = "resolved"
            else:
                a["disposition"] = "unresolved"
                near = nearest(sec, sorted(man.frames))
                where = (f"nearest frame {hms(near)}, {abs(sec - near)}s away"
                         if near is not None else "no frames in manifest")
                unresolved_lines.append(
                    f"{rel}:{a['line']} E-ANCHOR-UNRESOLVED `[{a['anchor']}]` "
                    f"{PIXEL_CLASS} is not an extracted frame of {video_id} "
                    f"({where})")
        elif cls == "SPOKEN":
            if man is not None and not man.fatal and man.resolves("cues"):
                # a cue namespace that can actually fail still decides, and it
                # decides harder than a witness: containment names a span
                if in_cue(sec, man.cues):
                    a["disposition"] = "resolved"
                else:
                    a["disposition"] = "unresolved"
                    unresolved_lines.append(
                        f"{rel}:{a['line']} E-ANCHOR-UNRESOLVED "
                        f"`[{a['anchor']}]` {cls} falls in no caption span of "
                        f"{video_id}")
            else:
                # every corpus manifest is here: containment is VACUOUS or the
                # namespace is absent, so the witness is the only thing that
                # can fail. An item too short to score is unchecked, not
                # witnessed, and it lands in a named counter rather than a
                # `continue` -- `spoken_vote.py --check` prints the live count
                # (27 at slice 12, 19 at slice 11), because calling them
                # anything else is how a denominator goes quiet.
                hits, fraction = (
                    (0, 0.0) if not cue_index or len(a["item"]) < MIN_ITEM_WORDS
                    else witness(a["item"], cue_index, sec))
                a["disposition"] = ("witnessed-spoken"
                                    if promotable(hits, fraction)
                                    else "unchecked-spoken")
        else:  # INFERRED
            if man is None or man.fatal or not man.resolves("cues"):
                a["disposition"] = "unbound-inferred"
            elif in_cue(sec, man.cues):
                a["disposition"] = "resolved"
            else:
                a["disposition"] = "unresolved"
                unresolved_lines.append(
                    f"{rel}:{a['line']} E-ANCHOR-UNRESOLVED `[{a['anchor']}]` "
                    f"{cls} falls in no caption span of {video_id}")
        row[a["disposition"]] += 1
    defects += unresolved_lines

    if row["anchors"] != sum(row[d] for d in DISPOSITIONS):
        defects.append(f"{rel}:1 E-PARTITION {row['anchors']} anchors do not "
                       f"sum to their dispositions")

    # ---- can this note be checked at all?
    pixel_wanted = row["resolved"] + row["unresolved"] + row["unchecked"]
    if row["unchecked"] and require_manifest and not exempt:
        reason = ("no notes/anchors/%s.tsv" % video_id if man is None
                  else "its manifest carries no usable frames namespace")
        defects.append(f"{rel}:1 E-MANIFEST-MISSING {reason}; "
                       f"{row['unchecked']} of {pixel_wanted} ON-SCREEN "
                       f"anchor(s) unchecked")
    if require_class and row["unattributable"]:
        kinds = collections.Counter(a["class"] for a in ancs
                                    if a["disposition"] == "unattributable")
        detail = (f"{row['unattributable']} anchor(s) sit in items with no "
                  f"evidence class or two ({kinds['NONE']} unlabelled, "
                  f"{kinds['MULTI']} labelled but untouched, "
                  f"{kinds['CONTESTED']} contested)")
        if video_id in UNATTRIBUTED_NOTES:
            row["class_exempt"] = detail
        else:
            defects.append(f"{rel}:1 E-ANCHOR-UNATTRIBUTED {detail}")
    # ON BY DEFAULT since slice 13. `cross-referenced` is a weaker standard than
    # a class on the anchor itself: the slice-12 coverage lane showed a fresh
    # note can dodge E-ANCHOR-UNATTRIBUTED entirely by labelling a second once
    # and re-citing it for ever. Slice 12 shipped this as an opt-in flag failing
    # at 106 anchors, because the corpus was not converted yet. Slice 13
    # converted all 106 -- each read against the citing line, the frame manifest
    # and the caption track -- so the corpus passes and the default flips.
    # `--no-require-touched-class` is the escape, and there is no dated
    # exemption list: a leaned-on anchor is now simply a defect.
    if require_touched and row["cross-referenced"]:
        defects.append(f"{rel}:1 E-ANCHOR-CROSSREF {row['cross-referenced']} "
                       f"anchor(s) carry no class of their own and lean on "
                       f"another line's label for the same second")

    # ---- a timestamp the anchor pattern cannot see is worse than an orphan:
    # nothing counts it, so the note reads clean. Fenced text is exempt, being
    # quoted rather than claimed.
    for block in note_blocks(body, body_start_line):
        if block["verbatim"]:
            continue
        for ln, text in block["lines"]:
            for m in RE_LOOSE_STAMP.finditer(text):
                if m.group(1) == "`" and m.group(3) == "`":
                    continue
                defects.append(f"{rel}:{ln} E-ANCHOR-UNQUOTED [{m.group(2)}] is "
                               f"timestamp-shaped but carries no backticks, so "
                               f"no check can see it")
    if sum(1 for line in body.split("\n") if RE_FENCE.match(line)) % 2:
        defects.append(f"{rel}:1 E-FENCE-UNBALANCED odd number of code fences; "
                       f"everything after the last one reads as verbatim and is "
                       f"exempt from every check")

    # ---- coverage ledger, from FRAME citations only
    if man is not None and not man.fatal and man.frames and man.duration:
        cited = {a["seconds"] for a in ancs if a["disposition"] == "resolved"
                 and a["class"] == PIXEL_CLASS}
        ordered = sorted(man.frames)
        uncited, worst, bounds = coverage_ledger(ordered, cited, man.duration)
        row["cited_frames"] = len(ordered) - uncited
        row["uncited_frames"] = uncited
        row["longest_gap"] = worst
        if max_gap is not None and worst > max_gap:
            defects.append(f"{rel}:1 E-COVERAGE-GAP {worst}s "
                           f"({hms(bounds[0])}-{hms(bounds[1])}) holds an "
                           f"extracted frame the note never cites, over the "
                           f"{max_gap}s ceiling; {uncited} of {len(ordered)} "
                           f"frames are cited nowhere")
    return defects, row


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def summarise(rows: list[dict], root: Path) -> list[str]:
    """The lines that stop a clean exit from being read as a verified corpus."""
    total = sum(r["anchors"] for r in rows)
    part = {d: sum(r[d] for r in rows) for d in DISPOSITIONS}
    manifested = [r for r in rows if r["manifest"] == "present"]
    missing = [r for r in rows if r["manifest"] == "missing"]

    out = [f"# {len(rows)} notes checked, {len(manifested)} manifested, "
           f"{len(missing)} unmanifested",
           "# " + str(total) + " anchors = " + " + ".join(
               f"{part[d]} {d}" for d in DISPOSITIONS)]
    if total != sum(part.values()):
        out.append("# PARTITION BROKEN: the dispositions do not sum to the "
                   "anchor count")
    for r in rows:
        if r["unchecked"]:
            why = ("no manifest" if r["manifest"] == "missing"
                   else "manifest carries no usable frames namespace")
            note = (f" [exempt: {UNRESOLVABLE_RUNS[r['video_id']]}]"
                    if r["video_id"] in UNRESOLVABLE_RUNS else "")
            out.append(f"# unchecked\t{r['file']}\t{r['unchecked']} ON-SCREEN "
                       f"anchor(s), {why}{note}")
    vacuous = [r["video_id"] for r in manifested
               if r["cue_selectivity"]
               and float(r["cue_selectivity"]) >= VACUOUS_COVERAGE]
    if vacuous:
        out.append(f"# cues namespace VACUOUS on {len(vacuous)} of "
                   f"{len(manifested)} manifest(s) (spans cover >= "
                   f"{VACUOUS_COVERAGE:.0%} of runtime, so containment cannot "
                   f"fail and its resolver is off): {', '.join(vacuous)}")
    ledger = [r for r in manifested if r["frames"]]
    if ledger:
        out.append(f"# coverage ledger: "
                   f"{sum(r['uncited_frames'] for r in ledger)} of "
                   f"{sum(r['frames'] for r in ledger)} extracted frames are "
                   f"cited nowhere; longest span holding one, "
                   f"{max(r['longest_gap'] for r in ledger)}s "
                   f"(--max-gap makes it a defect)")
    live = {r["video_id"] for r in rows if r["video_id"]}
    for r in rows:
        if r["class_exempt"]:
            out.append(f"# unattributed\t{r['file']}\t{r['class_exempt']} "
                       f"[exempt: {UNATTRIBUTED_NOTES[r['video_id']]}]")
    pending = sorted(v for v in UNATTRIBUTED_NOTES if v in live)
    if pending:
        out.append(f"# {len(pending)} note(s) exempt from "
                   f"--require-evidence-class, each dated in "
                   f"UNATTRIBUTED_NOTES: {', '.join(pending)}")
    xref = sum(r["cross-referenced"] for r in rows)
    if xref:
        out.append(f"# cross-referenced: {xref} of {total} anchor(s) carry no "
                   f"class of their own and repeat a second this note labels "
                   f"exactly once elsewhere. The class is NOT re-derived here "
                   f"and NOT asserted -- when the rule was measured on "
                   f"2026-08-05, 129 such pairs were read by hand and 2 cited "
                   f"the OTHER channel; both were repaired, not inherited. "
                   f"Since slice 13 all {xref} are ALSO defects by default "
                   f"(E-ANCHOR-CROSSREF, --no-require-touched-class disables), "
                   f"so a non-zero count here means the corpus regressed. See "
                   f"docs/reviews/slice12-cross-referenced-anchors.md")
    blanket = sum(r["blanket"] for r in rows)
    if blanket:
        worst = max(rows, key=lambda r: r["blanket"])
        out.append(f"# blanket reach: {blanket} of {total} anchor(s) take "
                   f"their item's ONE class without touching the label, most "
                   f"in {worst['file']} ({worst['blanket']}). REPORT ONLY -- "
                   f"applying touch here was measured and refuted; the "
                   f"per-anchor adjudication is "
                   f"docs/reviews/slice10-blanket-class-reach.md")
    stale = sorted(v for v in UNRESOLVABLE_RUNS if v in live)
    if stale:
        out.append(f"# {len(stale)} run(s) exempt from --require-manifest, "
                   f"each dated in UNRESOLVABLE_RUNS: {', '.join(stale)}")
    return out


def report(rows: list[dict]) -> str:
    # `blanket` is APPENDED, never inserted: a positional consumer of an
    # earlier report must keep reading the same column. `cross-referenced` is
    # appended for the same reason and goes AFTER `blanket`, not with the other
    # dispositions: splicing it into the disposition block moved seven columns
    # (`frames` 13 -> 14 through `blanket` 19 -> 20) while the docstring claimed
    # nothing moved. The order here is pinned by a selftest on the header.
    cols = (["file", "video_id", "manifest", "anchors"]
            + [d for d in DISPOSITIONS if d != "cross-referenced"]
            + ["frames", "cited_frames", "uncited_frames", "longest_gap",
               "frame_selectivity", "cue_selectivity", "blanket",
               "cross-referenced"])
    lines = ["\t".join(cols)]
    lines += ["\t".join(str(r[c]) for c in cols) for r in rows]
    return "\n".join(lines)


def collect_notes(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            found = sorted(f for f in p.rglob("*.md") if RE_NOTE_NAME.match(f.name))
            if not found:
                print(f"{PROG}: no notes under {p}", file=sys.stderr)
                raise SystemExit(2)
            files.extend(found)
        elif p.is_file():
            files.append(p)
        else:
            print(f"{PROG}: no such path: {p}", file=sys.stderr)
            raise SystemExit(2)
    return [f for f in files if f.name != "_template.md"]


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def _vtt_stamp(t: float) -> str:
    return f"{int(t)//3600:02d}:{int(t)//60%60:02d}:{t%60:06.3f}"


def _write_run(base: Path, frames: list[str], cues: list[tuple[float, float]],
               duration: int | None = None, video_id: str = "TESTID") -> Path:
    (base / "frames").mkdir(parents=True, exist_ok=True)
    for name in frames:
        (base / "frames" / name).write_bytes(name.encode())
    if cues:
        (base / "download").mkdir(parents=True, exist_ok=True)
        rows = ["WEBVTT", ""]
        for a, b in cues:
            rows += [f"{_vtt_stamp(a)} --> {_vtt_stamp(b)}", "text", ""]
        (base / "download" / "video.en.vtt").write_text("\n".join(rows),
                                                        encoding="utf-8")
    if duration is not None:
        (base / "download").mkdir(parents=True, exist_ok=True)
        (base / "download" / "video.info.json").write_text(
            json.dumps({"id": video_id, "duration": duration}), encoding="utf-8")
    return base


def _note(body: str, video_id: str = "FRAMESONLY", duration: str = "10:00") -> str:
    return (f"---\nvideo_id: {video_id}\nduration: \"{duration}\"\n---\n\n"
            f"# t\n\n{body}\n")


def _manifest(text_rows: list[str], video_id: str = "TESTID",
              duration: int = 600, namespaces: str = "frames") -> str:
    return ("\n".join([f"# video_id\t{video_id}", f"# duration\t{duration}",
                       f"# namespaces\t{namespaces}", MANIFEST_HEADER]
                      + text_rows) + "\n")


def selftest() -> int:
    cases = 0

    def check(label: str, got, want) -> None:
        nonlocal cases
        cases += 1
        if got != want:
            print(f"FAIL {label}: got {got!r} want {want!r}", file=sys.stderr)
            raise SystemExit(1)

    def classes(body: str) -> list[tuple[str, str]]:
        return [(a["anchor"], a["class"]) for a in note_anchors(body, 1, "TESTID")]

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        run = _write_run(root / "run",
                         ["frame_0001_t00m30s.jpg", "frame_0002_t01m10s.jpg",
                          "zoom_2814_draft.jpg"],
                         [(0.0, 5.0), (5.2, 9.0), (60.0, 65.0)], duration=600)

        text, stats = build_manifest("TESTID", [run])
        check("build frames", stats["frames"], 2)
        hour = _write_run(root / "hour", ["frame_0003_t1h23m07s.jpg"], [])
        check("hour form", sorted(frame_rows([hour])[0]), [4987])
        check("unstamped counted", stats["unstamped"], 1)
        check("unstamped named", "# unstamped\tzoom_2814_draft.jpg" in text, True)
        check("cues coalesced", stats["cues"], 2)
        check("duration from info.json", stats["duration"], 600)
        check("build is a pure function of its input",
              build_manifest("TESTID", [run])[0], text)

        # a collision is named, not silently dropped
        dup = _write_run(root / "dup",
                         ["frame_0001_t00m30s.jpg", "cue_0007_t00m30s.jpg"], [],
                         duration=600)
        dtext, dstats = build_manifest("TESTID", [dup])
        check("collision counted", (dstats["frames"], dstats["collisions"]), (1, 1))
        check("collision named", "# collision\t30\t" in dtext, True)

        # --build refuses what it cannot date or cannot trust
        blank = _write_run(root / "blank", [], [])
        try:
            build_manifest("TESTID", [blank])
            check("undateable manifest refused", "built", "refused")
        except ValueError:
            check("undateable manifest refused", "refused", "refused")
        wrong = _write_run(root / "wrong", ["frame_0001_t00m30s.jpg"], [],
                           duration=600, video_id="OTHERVID")
        try:
            build_manifest("TESTID", [wrong])
            check("wrong run dir refused", "built", "refused")
        except ValueError:
            check("wrong run dir refused", "refused", "refused")

        # ---- adopting a run (slice 13, DEFER:YTN-MANIFEST-AT-WRITE-TIME)
        # The point of the copy is that the manifest built from it grades the
        # SAME frames, so the proof is row equality, not a file count.
        (run / "download" / "video.mp4").parent.mkdir(parents=True, exist_ok=True)
        (run / "download" / "video.mp4").write_bytes(b"\x00" * 4096)
        durable = root / "durable"
        dirs, bad = adopt_run("TESTID", [run], durable, allow_temp=True)
        check("adoption reports no corruption", bad, [])
        check("the video file is left behind by default",
              (dirs[0] / "download" / "video.mp4").exists(), False)
        atext, astats = build_manifest("TESTID", dirs)
        check("the adopted manifest names the same frames",
              [r for r in atext.split("\n") if r.startswith("frame\t")],
              [r for r in text.split("\n") if r.startswith("frame\t")])
        check("the adopted manifest points at the durable copy",
              f"# source\t{dirs[0]}" in atext and str(durable) in atext, True)
        check("--with-video keeps it",
              (adopt_run("TESTID", [run], root / "durable2", with_video=True,
                         allow_temp=True)[0][0]
               / "download" / "video.mp4").exists(), True)
        # An adopted copy that is not byte-identical is a defect, not a warning:
        # the manifest it feeds records a sha256 nothing would ever re-check.
        tampered = root / "durable3"
        adopt_run("TESTID", [run], tampered, allow_temp=True)
        original = next(run.rglob("*.jpg"))
        victim = tampered / "TESTID" / run.name / original.relative_to(run)
        victim.write_text("tampered", encoding="utf-8")
        got = verify_copy(original, victim)
        check("a drifted copy is a defect",
              len(got) == 1 and "E-ADOPT-CORRUPT" in got[0], True)
        victim.unlink()
        check("a copy that never landed is a defect",
              "E-ADOPT-MISSING" in verify_copy(original, victim)[0], True)
        check("an honest copy is silent",
              verify_copy(original, dirs[0] / original.relative_to(run)), [])
        # ...and the destination may never be somewhere the OS reaps.
        check("adopting into the temp root is refused",
              adopt_run("TESTID", [run], Path(tempfile.gettempdir()))[1] != [], True)
        check("temp_owned knows the difference",
              (temp_owned(Path(tempfile.gettempdir()) / "x"),
               temp_owned(Path.home() / "durable-runs")), (True, False))
        # A POPULATED DESTINATION IS RECONCILED, NOT MERGED (correctness lane,
        # BLOCKER). Adopting a second run under the same name left the first
        # run's frames in place, and --build recorded them with a valid sha256
        # that --verify-artifacts confirms for ever.
        stale = root / "durable4"
        (stale / "TESTID" / run.name / "frames").mkdir(parents=True)
        (stale / "TESTID" / run.name / "frames" / "frame_9999_t99m00s.jpg"
         ).write_text("older run", encoding="utf-8")
        _, bad4 = adopt_run("TESTID", [run], stale, allow_temp=True)
        check("a leftover from an earlier run is named",
              len(bad4) == 1 and "E-ADOPT-EXTRA" in bad4[0], True)
        # ...but a file the SOURCE has and this call skipped is not a stranger.
        vid = root / "durable5"
        adopt_run("TESTID", [run], vid, with_video=True, allow_temp=True)
        check("a video kept by an earlier --with-video run is not a stranger",
              adopt_run("TESTID", [run], vid, allow_temp=True)[1], [])
        # An I/O failure is a complaint line, not a traceback: exit 1 has to
        # mean "adoption complained", or a half-written copy reads as an honest
        # failure (correctness lane).
        def _boom(src, dst):
            raise OSError(13, "Permission denied")
        check("an unreadable source is reported, not raised",
              [b.split()[0] for b in
               adopt_run("TESTID", [run], root / "durable6", allow_temp=True,
                         copier=_boom)[1]][:1], ["E-ADOPT-IO"])
        # The one line that turns verify_copy into this function's guarantee.
        # SAME LENGTH, different bytes: a mutant comparing st_size instead of
        # sha256 survived a shorter forgery, so the suite could not tell hashing
        # from length checking (correctness lane).
        def _wrong(src, dst):
            Path(dst).write_bytes(bytes(len(Path(src).read_bytes())))
        check("a copier that writes the wrong bytes is caught",
              any("E-ADOPT-CORRUPT" in b for b in
                  adopt_run("TESTID", [run], root / "durable7", allow_temp=True,
                            copier=_wrong)[1]), True)
        # An id is not a path component: `--adopt=../ESCAPED` wrote outside the
        # declared runs root.
        check("a traversing id is refused",
              "E-ADOPT-ID" in adopt_run("../ESCAPED", [run], root / "durable8",
                                        allow_temp=True)[1][0], True)

        # WebVTT MM:SS.mmm is legal and must not silently vanish
        mmss = root / "mmss.vtt"
        mmss.write_text("WEBVTT\n\n00:01.000 --> 00:04.000\nhi\n", encoding="utf-8")
        check("MM:SS.mmm cues parse", vtt_spans(mmss), [(1.0, 4.0)])

        mdir = root / "notes" / "anchors"
        mdir.mkdir(parents=True)
        (mdir / "TESTID.tsv").write_text(text, encoding="utf-8")
        # Most reconciliation fixtures use a frames-only manifest, so the cue
        # resolver is out of the way; TEETH below is where cues are exercised.
        (mdir / "FRAMESONLY.tsv").write_text(
            _manifest(["frame\t30\t30\ta.jpg\tsha", "frame\t70\t70\tb.jpg\tsha"],
                      video_id="FRAMESONLY"), encoding="utf-8")
        notes = root / "notes"

        # ---- attribution is per item and never inherited
        check("table row keeps its own class",
              classes("| a | b |\n|---|---|\n| `ON-SCREEN` slide | `[00:30]` |\n"
                      "| he says it | `[00:37]` |"),
              [("00:30", "ON-SCREEN"), ("00:37", "NONE")])
        check("numbered items keep their own class",
              classes("1. `ON-SCREEN` first at `[00:30]`\n"
                      "2. `SPOKEN` second at `[00:37]`"),
              [("00:30", "ON-SCREEN"), ("00:37", "SPOKEN")])
        check("a sub-bullet does not inherit",
              classes("- `ON-SCREEN` parent\n  - child at `[00:37]`"),
              [("00:37", "NONE")])
        check("a wrapped continuation line does inherit",
              classes("- `ON-SCREEN` a claim that runs long enough to\n"
                      "  wrap before its anchor `[00:37]`"),
              [("00:37", "ON-SCREEN")])
        check("fenced text is verbatim, not a claim",
              classes("- `ON-SCREEN` see below\n\n```text\nquote `[00:37]`\n```"),
              [("00:37", "VERBATIM")])
        check("a heading anchor is seen",
              classes("## the slide at `[00:37]`"), [("00:37", "NONE")])
        check("a blockquote is its own item",
              classes("- `ON-SCREEN` a bullet\n> a quote at `[00:37]`"),
              [("00:37", "NONE")])

        # ---- a two-class item is split by touch, and only by touch
        # The langfuse note's real shape: one bullet, one class per anchor.
        check("two classes split one per anchor",
              classes("- `[07:04]` `SPOKEN` `ON-SCREEN` `[08:04]` — both"),
              [("07:04", "SPOKEN"), ("08:04", "ON-SCREEN")])
        # The shape a nearest-token rule got wrong: the third anchor is
        # unlabelled and two sentences downstream, and inventing a class for it
        # printed E-ANCHOR-UNRESOLVED for a frame the note never claimed.
        check("an untouched anchor in a two-class item is MULTI",
              classes("- `[16:01]` `SPOKEN` + `[16:07]` `ON-SCREEN` — the one\n"
                      "  citation. `[16:12]` adds the mechanism."),
              [("16:01", "SPOKEN"), ("16:07", "ON-SCREEN"),
               ("16:12", "MULTI")])
        check("prose in the gap ends the reach",
              classes("- `[27:56]` `ON-SCREEN` — the seed. Spoken `[25:41]` it\n"
                      "  becomes 700,000. `[25:48]` `SPOKEN` — voiced together."),
              [("27:56", "ON-SCREEN"), ("25:41", "MULTI"),
               ("25:48", "SPOKEN")])
        check("an ORPHAN mark is glue, not prose",
              classes("`[02:15]` `ORPHAN` `ON-SCREEN` + `SPOKEN` — three tests"),
              [("02:15", "ON-SCREEN")])
        # One class still labels the whole item however far it sits, which is
        # the reviewed round-6 rule; splitting applies only where there are two.
        check("one class still reaches across prose",
              classes("- `ON-SCREEN` a card, described at length, then `[00:37]`"),
              [("00:37", "ON-SCREEN")])
        check("a label between two anchors ties to the left",
              classes("- `[00:30]` `SPOKEN` `[00:37]` `ON-SCREEN`"),
              [("00:30", "SPOKEN"), ("00:37", "ON-SCREEN")])
        # Anchors joined only by glue are one group and take one class: the
        # densest note labels a PAIR of cards, not the second card.
        check("a slash-joined pair takes one class",
              classes("- `[03:43]` / `[04:59]` `ON-SCREEN` — two cards, and\n"
                      "  `[05:01]` `SPOKEN` says the same"),
              [("03:43", "ON-SCREEN"), ("04:59", "ON-SCREEN"),
               ("05:01", "SPOKEN")])
        # The range form, machine marks and all.
        check("a marked range takes one class",
              classes("`[02:08]` `ORPHAN`-`[02:34]` `ORPHAN` `ON-SCREEN` — the\n"
                      "poster, and `[02:36]` `SPOKEN` says so too"),
              [("02:08", "ON-SCREEN"), ("02:34", "ON-SCREEN"),
               ("02:36", "SPOKEN")])
        check("a group with no label of its own is MULTI",
              classes("`[01:12]` `SPOKEN` + `[01:16]`-`[01:24]` `ON-SCREEN`, and\n"
                      "then `[01:28]`-`[01:31]` an orange card"),
              [("01:12", "SPOKEN"), ("01:16", "ON-SCREEN"),
               ("01:24", "ON-SCREEN"), ("01:28", "MULTI"),
               ("01:31", "MULTI")])
        # A line break is not glue. Found by the slice-8 correctness lane: the
        # `ON-SCREEN` anchor was absorbed into the next line's `SPOKEN` group
        # and stopped being checked against frames at all -- a loud MULTI
        # turned into a silent pass.
        check("a line break does not group two anchors",
              classes("- `ON-SCREEN` the card at `[02:54]`\n"
                      "  `[02:53]` `SPOKEN` he says it"),
              [("02:54", "MULTI"), ("02:53", "SPOKEN")])
        check("a label does not reach across a line break",
              classes("- `[00:30]` `SPOKEN` first\n"
                      "  `ON-SCREEN` `[00:37]` second"),
              [("00:30", "SPOKEN"), ("00:37", "ON-SCREEN")])

        # ---- every documented glue character is exercised, one case each.
        # The lane's mutation probe deleted 8 of 11 from RE_GLUE without a
        # single case noticing, so the alphabet is now pinned by tests.
        for glue in ("`", "+", ",", ";", "/", "&", "|", "*", "~", "-", " "):
            check(f"glue {glue!r} joins a label to its anchor",
                  classes(f"- `[00:30]`{glue}`ON-SCREEN` and `[00:37]` `SPOKEN`"),
                  [("00:30", "ON-SCREEN"), ("00:37", "SPOKEN")])
        check("a letter is not glue",
              classes("- `[00:30]` x `ON-SCREEN` and `[00:37]` `SPOKEN`"),
              [("00:30", "MULTI"), ("00:37", "SPOKEN")])

        # ---- the ONE-class branch: why touch is NOT applied there.
        # Slice 10 measured all 48 corpus anchors a single label reaches
        # without touching. Touch would strip a class the note states in
        # words in 46 of them. Each shape below is a live corpus line, so a
        # future "just run touch everywhere" edit fails these instead of
        # quietly emptying four notes.
        def blanket(body: str) -> list[str]:
            return [a["anchor"] for a in note_anchors(body, 1, "TESTID")
                    if a["blanket"]]

        # satori:324 -- the note says "all", then lists three frames.
        satori = ("Continuity, all `ON-SCREEN`: the wordmark at `[09:05]`\n"
                  "reads VECTRYL, the tweet at `[09:21]` is @veritas, and at\n"
                  "`[09:28]` two arrows point at Spotify.")
        check("an item that says all keeps its class",
              classes(satori),
              [("09:05", "ON-SCREEN"), ("09:21", "ON-SCREEN"),
               ("09:28", "ON-SCREEN")])
        check("and every one of them is counted as blanket reach",
              blanket(satori), ["09:05", "09:21", "09:28"])
        # langfuse:58 -- one label sandwiched between two frames labels both.
        # Touch hands it to the left only, on the (dist, side) tie-break.
        check("a sandwiched label in a one-class item labels both",
              classes("- `[20:27]` `ON-SCREEN` `[20:34]` — a score is a number"),
              [("20:27", "ON-SCREEN"), ("20:34", "ON-SCREEN")])
        # vc-pmf:225 -- the class is declared in the prose of the sentence.
        check("a class declared in prose still binds",
              classes("Two references are `INFERRED` and unverified:\n"
                      "Akshayakalpa at `[51:30]`, and Even at `[1:01:49]`."),
              [("51:30", "INFERRED"), ("1:01:49", "INFERRED")])
        # The counter measures the ONE-class branch only: a two-class item's
        # untouched anchors are already MULTI and already reported.
        check("a two-class item contributes no blanket reach",
              blanket("- `[16:01]` `SPOKEN` + `[16:07]` `ON-SCREEN` — one\n"
                      "  citation. `[16:12]` adds the mechanism."), [])
        check("an anchor that touches its label is not blanket reach",
              blanket("- `ON-SCREEN` `[00:30]` a card, and later `[00:37]`"),
              ["00:37"])
        check("an unlabelled item has no blanket reach",
              blanket("- a claim at `[00:30]`"), [])
        # The counter is per ANCHOR, not per group. Without this case a patch
        # that counts one member per untouched group passes all 90 others --
        # the slice-10 correctness lane mutated exactly that and killed nothing.
        check("every member of an untouched glue-joined group is counted",
              blanket("- all `ON-SCREEN`: the pair `[00:30]` / `[00:37]` here"),
              ["00:30", "00:37"])
        # A zero-class and a one-class item must differ, or the class-count
        # guard is a free variable.
        check("zero classes and one class differ",
              (blanket("- a claim at `[00:30]`"),
               blanket("- `SPOKEN` he says so, and then `[00:30]`")),
              ([], ["00:30"]))
        # A fenced anchor is VERBATIM and never takes the item's class, so it
        # cannot be blanket reach however the label sits.
        check("a verbatim anchor is never blanket reach",
              blanket("```text\n`ON-SCREEN` a quoted line, then `[00:37]`\n```"),
              [])
        check("a foreign anchor is never blanket reach",
              blanket("- all `ON-SCREEN`: see "
                      "notes/2026-01-01--other--OTHERID.md at `[00:37]`"), [])

        # ---- a renderer mark must never eat an anchor. `ORPHAN_MARK` opens
        # with a backtick, so before slice 10 the deletion could start at an
        # anchor's CLOSING backtick: the anchor vanished from the bound map and
        # `note_anchors` died `KeyError` mid-corpus, printing no defect line at
        # all. 784 crashes in a 60,000-item fuzz, 0 in the corpus.
        check("a mark that would eat an anchor's backtick is kept",
              strip_marks("`[00:30]`ORPHAN` and `SPOKEN` `[01:10]`")[0],
              "`[00:30]`ORPHAN` and `SPOKEN` `[01:10]`")
        check("a real mark is still deleted",
              strip_marks("`[00:30]` `ORPHAN` x")[0], "`[00:30]`  x")
        check("the eaten-backtick shape no longer raises",
              classes("- `[00:30]`ORPHAN` and `SPOKEN` `[01:10]`"),
              [("00:30", "SPOKEN"), ("01:10", "SPOKEN")])

        # an ON-SCREEN anchor on an extracted second resolves
        good = notes / "2026-01-01--good--TESTID.md"
        good.write_text(_note("- `ON-SCREEN` a thing at `[00:30]`"), encoding="utf-8")
        d, row = check_note(good, root)
        check("exact anchor clean", d, [])
        check("exact anchor counted", row["resolved"], 1)

        # ... and one seven seconds off does not
        bad = notes / "2026-01-01--bad--TESTID.md"
        bad.write_text(_note("- `ON-SCREEN` a thing at `[00:37]`"), encoding="utf-8")
        d, row = check_note(bad, root)
        check("off-by-seven fires", len(d), 1)
        check("off-by-seven code", "E-ANCHOR-UNRESOLVED" in d[0], True)
        check("names the nearest frame", "nearest frame 00:30, 7s away" in d[0], True)

        # THE BOUNDARY, made executable: an anchor moved onto a DIFFERENT
        # extracted second still resolves. Closing the namespace catches an
        # invented anchor, never a swap between two frames the run produced --
        # which is why R1 stays open. Measured on the real corpus: shifting all
        # 13 ON-SCREEN anchors by +1s, +7s and +60s fired 12 of 13 each time,
        # the survivor landing on another real frame.
        swap = notes / "2026-01-01--swap--TESTID.md"
        swap.write_text(_note("- `ON-SCREEN` a thing at `[01:10]`"), encoding="utf-8")
        check("a swap onto another extracted frame survives",
              check_note(swap, root)[0], [])

        # a SPOKEN anchor is not bound to frames, and not to a vacuous cue set
        spoken = notes / "2026-01-01--spoken--TESTID.md"
        spoken.write_text(_note("- `SPOKEN` a thing at `[00:37]`"), encoding="utf-8")
        d, row = check_note(spoken, root)
        check("spoken with no caption index is unchecked, not resolved",
              (d, row["unchecked-spoken"]), ([], 1))

        # ---- the caption witness IS the cues namespace's resolver. Before
        # slice 11 every SPOKEN anchor landed in one `unbound-spoken` bucket
        # that could not fail, so `SPOKEN` was the cheapest label that cleared
        # every gate -- a scoring rule that rewards the unverifiable answer.
        # FRAMESONLY, because `_note` defaults to it and its manifest carries
        # no cues namespace -- which is the corpus's own situation
        (root / CAPTION_DIR).mkdir(parents=True, exist_ok=True)
        (root / CAPTION_DIR / "FRAMESONLY.tsv").write_text(
            CAPTION_HEADER + "\n"
            "10.000\t13.000\tcohort retention curve is the chart\n"
            "13.000\t16.000\tthat matters most for product market fit\n",
            encoding="utf-8")
        witnessed = notes / "2026-01-01--witness--TESTID.md"
        witnessed.write_text(_note(
            "- `SPOKEN` the cohort retention curve is offered as the chart "
            "that matters most `[00:12]`"), encoding="utf-8")
        d, row = check_note(witnessed, root)
        check("a witnessed SPOKEN anchor is witnessed, not resolved",
              (d, row["witnessed-spoken"], row["resolved"]), ([], 1, 0))
        silent = notes / "2026-01-01--silent--TESTID.md"
        silent.write_text(_note(
            "- `SPOKEN` wombats aardvarks pangolins okapis quokkas narwhals "
            "`[00:12]`"), encoding="utf-8")
        d, row = check_note(silent, root)
        check("an unwitnessed SPOKEN anchor is unchecked, never a defect",
              (d, row["unchecked-spoken"]), ([], 1))
        # FOUR content words, deliberately: a one-word item is below every
        # plausible floor, so it cannot tell MIN_ITEM_WORDS from a mutation of
        # it. The slice-11 correctness lane deleted the guard and failed zero
        # of 101 cases against the old one-word fixture.
        tiny = notes / "2026-01-01--tiny--TESTID.md"
        tiny.write_text(_note("- `SPOKEN` cohort retention curve chart "
                              "`[00:12]`"), encoding="utf-8")
        d, row = check_note(tiny, root)
        check("an item too short to score is unchecked, not witnessed",
              (d, row["unchecked-spoken"]), ([], 1))

        # ---- a caption index that exists must be usable and must be THIS
        # video's. Both failures used to read as an honest miss.
        (root / CAPTION_DIR / "FRAMESONLY.tsv").write_text(
            CAPTION_HEADER + "\nGARBAGE\n", encoding="utf-8")
        d, row = check_note(witnessed, root)
        check("an unusable caption index is a defect, not silence",
              ("E-CAPTION-EMPTY" in " ".join(d), row["witnessed-spoken"]),
              (True, 0))
        (root / CAPTION_DIR / "FRAMESONLY.tsv").write_text(
            "# video_id\tOTHERVID\n" + CAPTION_HEADER + "\n"
            "10.000\t13.000\tcohort retention curve is the chart\n"
            "13.000\t16.000\tthat matters most for product market fit\n",
            encoding="utf-8")
        d, row = check_note(witnessed, root)
        check("an index rotated onto another video is a defect",
              ("E-CAPTION-VIDEO" in " ".join(d), row["witnessed-spoken"]),
              (True, 1))
        (root / CAPTION_DIR / "FRAMESONLY.tsv").write_text(
            CAPTION_HEADER + "\n"
            "10.000\t13.000\tcohort retention curve is the chart\n"
            "13.000\t16.000\tthat matters most for product market fit\n",
            encoding="utf-8")
        check("and the restored index is clean again",
              check_note(witnessed, root)[0], [])

        # ---- a reference reaches two lines, matching resolve_note's oracle.
        # The satori note wraps its cross-video anchor onto the next line.
        wrapped = notes / "2026-01-01--wrapped--TESTID.md"
        wrapped.write_text(_note(
            "4. **No iteration.** Compare\n"
            "   `notes/2026-01-01--other--OTHERVID.md`\n"
            "   `[28:51]` ten positionings, let the market choose."),
            encoding="utf-8")
        d, row = check_note(wrapped, root)
        check("a wrapped cross-video citation is foreign, not ours",
              (d, row["foreign"]), ([], 1))
        far = notes / "2026-01-01--far--TESTID.md"
        far.write_text(_note(
            "- `notes/2026-01-01--other--OTHERVID.md` is the comparison\n"
            "  and a line of prose\n"
            "  and another line of prose\n"
            "  then `ON-SCREEN` our own frame at `[00:30]`"), encoding="utf-8")
        check("but the reach stops at two lines",
              check_note(far, root)[1]["foreign"], 0)

        # ... but IS bound when the cue namespace actually discriminates
        (mdir / "TEETH.tsv").write_text(
            _manifest(["cue\t0.000\t60.000\tcue_0000\t-"], video_id="TEETH",
                      namespaces="cues"), encoding="utf-8")
        teeth = notes / "2026-01-01--teeth--TEETH.md"
        teeth.write_text(_note("- `SPOKEN` a quote at `[05:00]`", video_id="TEETH"),
                         encoding="utf-8")
        d, row = check_note(teeth, root)
        check("non-vacuous cues resolve SPOKEN",
              ("E-ANCHOR-UNRESOLVED" in " ".join(d), row["unresolved"]), (True, 1))
        teeth_ok = notes / "2026-01-01--teethok--TEETH.md"
        teeth_ok.write_text(_note("- `SPOKEN` a quote at `[00:30]`", video_id="TEETH"),
                            encoding="utf-8")
        check("an anchor inside a cue span resolves", check_note(teeth_ok, root)[0], [])

        # an anchor beside a reference to ANOTHER video's note is exempt, per line
        foreign = notes / "2026-01-01--foreign--TESTID.md"
        foreign.write_text(_note(
            "- `ON-SCREEN` see `notes/2026-01-01--x--OTHERVID.md` at `[00:37]`"),
            encoding="utf-8")
        d, row = check_note(foreign, root)
        check("foreign exempt but counted", (d, row["foreign"]), ([], 1))
        own = notes / "2026-01-01--own--TESTID.md"
        own.write_text(_note(
            "- `ON-SCREEN` see `notes/2026-01-01--own--FRAMESONLY.md` at `[00:37]`"),
            encoding="utf-8")
        check("a reference to this note's OWN file exempts nothing",
              len(check_note(own, root)[0]), 1)
        split_ref = notes / "2026-01-01--split--TESTID.md"
        split_ref.write_text(_note(
            "- `ON-SCREEN` a thing at `[00:37]`\n  see `notes/2026-01-01--x--OTHERVID.md`"),
            encoding="utf-8")
        check("the exemption does not spread to the whole item",
              len(check_note(split_ref, root)[0]), 1)

        # a missing manifest is a defect by default, exempt only by dated entry
        orphan = notes / "2026-01-01--orphan--NOMANIFEST.md"
        orphan.write_text(_note("- `ON-SCREEN` a thing at `[00:37]`",
                                video_id="NOMANIFEST"), encoding="utf-8")
        d, row = check_note(orphan, root)
        check("missing manifest fires by default",
              ("E-MANIFEST-MISSING" in " ".join(d), row["unchecked"]), (True, 1))
        check("--no-require-manifest silences it",
              check_note(orphan, root, require_manifest=False)[0], [])

        # a manifest that exists but cannot resolve is NOT "present enough"
        (mdir / "CUESONLY.tsv").write_text(
            _manifest(["cue\t0.000\t600.000\tcue_0000\t-"], video_id="CUESONLY",
                      namespaces="cues"), encoding="utf-8")
        cues_only = notes / "2026-01-01--cues--CUESONLY.md"
        cues_only.write_text(_note("- `ON-SCREEN` a thing at `[00:37]`",
                                   video_id="CUESONLY"), encoding="utf-8")
        d, row = check_note(cues_only, root)
        check("a frameless manifest does not satisfy the gate",
              ("no usable frames namespace" in " ".join(d), row["unchecked"]),
              (True, 1))
        check("vacuous cue coverage detected",
              read_manifest(mdir / "CUESONLY.tsv").cues_vacuous, True)

        # a note whose duration disagrees describes a different run
        drift = notes / "2026-01-01--drift--TESTID.md"
        drift.write_text(_note("- `ON-SCREEN` a thing at `[00:30]`",
                               duration="9:00"), encoding="utf-8")
        check("duration drift fires",
              "E-MANIFEST-DURATION" in " ".join(check_note(drift, root)[0]), True)

        # malformed, duplicate, undated and empty-namespace manifests
        (mdir / "BROKEN.tsv").write_text(
            _manifest(["frame\t30\t30\ta.jpg\tsha", "frame\tnope"],
                      video_id="BROKEN"), encoding="utf-8")
        check("malformed row reported",
              sum("E-MANIFEST-ROW" in x
                  for x in read_manifest(mdir / "BROKEN.tsv").defects), 1)
        (mdir / "DUP.tsv").write_text(
            _manifest(["frame\t30\t30\ta.jpg\tsha", "frame\t30\t30\tb.jpg\tsha"],
                      video_id="DUP"), encoding="utf-8")
        dman = read_manifest(mdir / "DUP.tsv")
        check("duplicate second reported",
              (len(dman.frames), sum("duplicate frame second" in x
                                     for x in dman.defects)), (1, 1))
        (mdir / "UNDATED.tsv").write_text(
            "# video_id\tUNDATED\n# namespaces\tframes\n" + MANIFEST_HEADER
            + "\nframe\t9999\t9999\ta.jpg\tsha\n", encoding="utf-8")
        check("undated manifest reported",
              sum("E-MANIFEST-NO-DURATION" in x
                  for x in read_manifest(mdir / "UNDATED.tsv").defects), 1)
        (mdir / "EMPTY.tsv").write_text(
            _manifest([], video_id="EMPTY"), encoding="utf-8")
        check("empty namespace reported",
              sum("E-MANIFEST-NAMESPACE" in x
                  for x in read_manifest(mdir / "EMPTY.tsv").defects), 1)
        (mdir / "PAST.tsv").write_text(
            _manifest(["frame\t30\t30\ta.jpg\tsha",
                       "frame\t4987\t4987\tb.jpg\tsha"], video_id="PAST"),
            encoding="utf-8")
        check("frame past runtime reported",
              sum("E-MANIFEST-RANGE" in x
                  for x in read_manifest(mdir / "PAST.tsv").defects), 1)

        # the manifest is not self-certifying
        (mdir / "GONE.tsv").write_text(
            "\n".join([f"# video_id\tGONE", "# duration\t600",
                       "# namespaces\tframes", f"# source\t{run}",
                       MANIFEST_HEADER,
                       "frame\t30\t30\tno_such_file.jpg\tdeadbeef"]) + "\n",
            encoding="utf-8")
        gone = notes / "2026-01-01--gone--GONE.md"
        gone.write_text(_note("- `ON-SCREEN` a thing at `[00:30]`", video_id="GONE"),
                        encoding="utf-8")
        check("a deleted frame passes without --verify-artifacts",
              check_note(gone, root)[0], [])
        check("--verify-artifacts catches it",
              "E-MANIFEST-ARTIFACT" in " ".join(
                  check_note(gone, root, artifacts=True)[0]), True)

        # orphan and duplicate manifests
        sweep = sweep_manifests(root, {"TESTID"})
        check("orphan manifest reported",
              sum("E-MANIFEST-ORPHAN" in x for x in sweep) >= 1, True)
        (mdir / "TESTID-copy.tsv").write_text(text, encoding="utf-8")
        check("duplicate manifest reported",
              sum("E-MANIFEST-DUPLICATE" in x
                  for x in sweep_manifests(root, {"TESTID"})), 1)
        (mdir / "TESTID-copy.tsv").unlink()

        # untagged anchors are a defect BY DEFAULT: a switch that is off
        # exempts every note written from here to spare four old ones, which
        # is the shape the manifest requirement already refused.
        loose = notes / "2026-01-01--loose--TESTID.md"
        loose.write_text(_note("a bare line at `[00:30]`"), encoding="utf-8")
        d, row = check_note(loose, root)
        check("an unattributed anchor fires by default",
              ("E-ANCHOR-UNATTRIBUTED" in " ".join(d), row["unattributable"]),
              (True, 1))
        check("the defect names which pile it is",
              "1 unlabelled, 0 labelled but untouched, 0 contested" in d[0], True)
        check("--no-require-evidence-class silences it",
              check_note(loose, root, require_class=False)[0], [])
        # A note named in the dated list is exempt, and says so in the summary
        # rather than falling silent.
        UNATTRIBUTED_NOTES["FRAMESONLY"] = "2026-01-01 fixture"
        try:
            d, row = check_note(loose, root)
            check("a dated exemption downgrades to a printed note",
                  (d, bool(row["class_exempt"])), ([], True))
        finally:
            del UNATTRIBUTED_NOTES["FRAMESONLY"]
        # Two labels both touching one group is CONTESTED, not silence.
        check("two labels touching one anchor is CONTESTED",
              classes("- `SPOKEN` `[00:30]` `ON-SCREEN` and `[00:37]` `SPOKEN`"),
              [("00:30", "CONTESTED"), ("00:37", "SPOKEN")])
        # A timestamp the anchor pattern cannot see is invisible to every check.
        unq = notes / "2026-01-01--unquoted--TESTID.md"
        unq.write_text(_note("a bare [46:30] with no backticks"), encoding="utf-8")
        check("an unquoted timestamp is a defect",
              "E-ANCHOR-UNQUOTED" in " ".join(check_note(unq, root)[0]), True)
        unq.write_text(_note("```text\nquoted [46:30]\n```"), encoding="utf-8")
        check("a fenced unquoted timestamp is not",
              check_note(unq, root)[0], [])
        unq.write_text(_note("```text\nan unclosed fence"), encoding="utf-8")
        check("an unbalanced fence is a defect",
              "E-FENCE-UNBALANCED" in " ".join(check_note(unq, root)[0]), True)
        unq.unlink()
        # The shipped control: prose in every gap must cost every binding.
        ctl = notes / "2026-01-01--control--TESTID.md"
        ctl.write_text(_note("- `[00:30]` `SPOKEN` `ON-SCREEN` `[00:37]` both"),
                       encoding="utf-8")
        check("the binding control holds on a two-class item",
              binding_control([ctl])[1], True)
        ctl.unlink()

        # every anchor lands in exactly one disposition
        mixed = notes / "2026-01-01--mixed--TESTID.md"
        mixed.write_text(_note(
            "- `ON-SCREEN` at `[00:30]`\n"
            "- `SPOKEN` at `[00:40]`\n"
            "- `INFERRED` at `[00:50]`\n"
            "- bare at `[01:00]`\n"
            "- `ON-SCREEN` `SPOKEN` at `[01:20]`\n"
            "- `ON-SCREEN` cf `notes/2026-01-01--x--OTHERVID.md` at `[01:30]`\n"
            "\n```text\n`[01:40]`\n```"), encoding="utf-8")
        d, row = check_note(mixed, root)
        check("the partition holds", row["anchors"],
              sum(row[k] for k in DISPOSITIONS))
        check("dispositions are the expected ones",
              {k: row[k] for k in DISPOSITIONS if row[k]},
              {"resolved": 1, "unchecked-spoken": 1, "unbound-inferred": 1,
               "unattributable": 2, "verbatim": 1, "foreign": 1})

        # ---- cross-referenced: a re-citation is not a missing decision, and
        # the rule is deliberately narrow. Each case below kills one mutant.
        xref = notes / "2026-01-01--xref--TESTID.md"
        xref.write_text(_note(
            "- `ON-SCREEN` the card at `[00:30]`\n"
            "- later prose points back at `[00:30]` without a label"),
            encoding="utf-8")
        d, row = check_note(xref, root, require_touched=False)
        check("a re-citation of a classed second is cross-referenced",
              ({k: row[k] for k in DISPOSITIONS if row[k]}, d),
              ({"resolved": 1, "cross-referenced": 1}, []))
        # NEIGHBOUR: one second away is a different moment. Kills a mutant that
        # inherits from the nearest classed second instead of the same one.
        xref.write_text(_note(
            "- `ON-SCREEN` the card at `[00:30]`\n"
            "- later prose points at `[00:31]` without a label"),
            encoding="utf-8")
        check("a neighbouring second inherits nothing",
              check_note(xref, root)[1]["unattributable"], 1)
        # TWO CHANNELS: the note labels this second both ways somewhere, so the
        # re-citation genuinely does not say which. Kills a mutant that asks
        # only whether ANY class exists elsewhere.
        xref.write_text(_note(
            "- `ON-SCREEN` the card at `[00:30]`\n"
            "- `SPOKEN` he says it at `[00:30]`\n"
            "- later prose points back at `[00:30]` without a label"),
            encoding="utf-8")
        d, row = check_note(xref, root)
        check("a second labelled both ways stays unattributable",
              (row["unattributable"], row["cross-referenced"]), (1, 0))
        # VERBATIM donates nothing: fenced text is quoted, not claimed.
        xref.write_text(_note(
            "```text\n`[00:30]` `ON-SCREEN` in a quoted block\n```\n\n"
            "- later prose points back at `[00:30]` without a label"),
            encoding="utf-8")
        check("a fenced anchor donates no class",
              check_note(xref, root)[1]["unattributable"], 1)
        # FOREIGN donates nothing: that second belongs to another video.
        xref.write_text(_note(
            "- `ON-SCREEN` per `notes/2026-01-01--x--OTHERVID.md` at `[00:30]`\n"
            "\n- later prose points back at `[00:30]` without a label"),
            encoding="utf-8")
        d, row = check_note(xref, root)
        check("a foreign anchor donates no class",
              (row["foreign"], row["unattributable"], row["cross-referenced"]),
              (1, 1, 0))
        # The gate still fires on what is genuinely undecided, and the printed
        # counts do not silently absorb the re-citations.
        xref.write_text(_note(
            "- `ON-SCREEN` the card at `[00:30]`\n"
            "- later prose points back at `[00:30]` without a label\n"
            "- and a bare `[02:00]` nothing labels"), encoding="utf-8")
        d, row = check_note(xref, root)
        # STRICT MODE, on by DEFAULT since slice 13. Without it a fresh note
        # dodges E-ANCHOR-UNATTRIBUTED by labelling a second once and re-citing
        # it for ever (coverage lane, slice 12). The default is pinned here as
        # well as the flag, because a mutant flipping it back is silent: every
        # disposition count stays identical and only the exit code moves.
        xref.write_text(_note(
            "- `ON-SCREEN` the card at `[00:30]`\n"
            "- later prose points back at `[00:30]` without a label"),
            encoding="utf-8")
        check("a leaned-on anchor is a defect by default",
              ("E-ANCHOR-CROSSREF" in " ".join(check_note(xref, root)[0]),
               check_note(xref, root, require_touched=False)[0]), (True, []))
        # ...and the same through argparse, which is the default a user gets.
        # Both review lanes found the check_note pin does NOT cover the CLI:
        # inverting `not args.no_require_touched_class` left all cases green and
        # `--check` at exit 0, because with 0 cross-referenced anchors in the
        # corpus the output is byte-identical either way.
        # The fixture root carries other fixtures' manifests, so the exit code
        # is not clean either way; the defect LINE is what the flag governs.
        import contextlib
        import io

        def _cli(*flags):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
                main(["--check", "--no-require-manifest", *flags, str(xref)],
                     root=root)
            # the DEFECT line, not the summary sentence, which names the code
            return ":1 E-ANCHOR-CROSSREF" in buf.getvalue()

        check("the CLI reaches the default", _cli(), True)
        check("...and --no-require-touched-class turns it off",
              _cli("--no-require-touched-class"), False)
        xref.write_text(_note(
            "- `ON-SCREEN` the card at `[00:30]`\n"
            "- later prose points back at `[00:30]` without a label\n"
            "- and a bare `[02:00]` nothing labels"), encoding="utf-8")
        check("the gate still fires on the undecided anchor",
              ("E-ANCHOR-UNATTRIBUTED" in " ".join(d), row["unattributable"],
               row["cross-referenced"]), (True, 1, 1))
        check("the partition holds with cross-referenced present",
              row["anchors"], sum(row[k] for k in DISPOSITIONS))
        # DONEE: a MULTI anchor -- an item that named two classes and touched
        # neither -- is a re-citation too. A mutant narrowing the donee to NONE
        # passed every earlier case and moved 10 live corpus anchors
        # (correctness lane, slice 12).
        xref.write_text(_note(
            "- `ON-SCREEN` the card at `[00:30]`\n"
            "- `SPOKEN` he says it at `[00:40]`\n"
            "- `ON-SCREEN` and `SPOKEN` both named here, `[00:30]` touched by "
            "neither"), encoding="utf-8")
        d, row = check_note(xref, root)
        check("a MULTI anchor on a classed second is cross-referenced too",
              (row["cross-referenced"], row["unattributable"]), (1, 0))
        # SECONDS, NOT SPELLING: `[1:05:18]` and `[65:18]` are the same second,
        # and the vc-pmf note writes both. A mutant keying the donor on the
        # anchor TEXT passed every earlier case.
        xref.write_text(_note(
            "- `SPOKEN` he says it at `[1:05:18]`\n"
            "- the transcript prints that as `[65:18]`", duration="1:30:00"),
            encoding="utf-8")
        check("the same second in another spelling still donates",
              check_note(xref, root)[1]["cross-referenced"], 1)
        # NEIGHBOUR, BOTH DIRECTIONS: the earlier case only put the donor one
        # second BELOW the citation, so a `sec + 1` mutant survived it.
        xref.write_text(_note(
            "- `ON-SCREEN` the card at `[00:31]`\n"
            "- prose points at `[00:30]` without a label"), encoding="utf-8")
        check("a donor one second LATER inherits nothing either",
              check_note(xref, root)[1]["unattributable"], 1)
        # ORDER: the labelling line may come AFTER the line that re-cites it.
        # A mutant demanding the donor sit earlier passed every case above,
        # because all of them put the label first (conformance lane, slice 12).
        xref.write_text(_note(
            "- prose points at `[00:30]` before anything labels it\n"
            "- `ON-SCREEN` the card at `[00:30]`"), encoding="utf-8")
        d, row = check_note(xref, root, require_touched=False)
        check("a donor further down the note still counts",
              ({k: row[k] for k in DISPOSITIONS if row[k]}, d),
              ({"resolved": 1, "cross-referenced": 1}, []))
        xref.unlink()
        # A mark deletion must never lose an anchor. This input crashed
        # `note_anchors` with `KeyError: 30` before `mark_free` (slice-12
        # correctness lane): the opening anchor has no closing backtick, so the
        # comment is unprotected and deleting it moved the anchors the raw scan
        # had already found.
        check("a mark that fuses an anchor does not crash the run",
              [(a["anchor"], a["class"]) for a in
               note_anchors("- `[0:30]<!-- auto-demoted -->`[0:30]`", 1, "T")],
              [("0:30", "NONE")])
        check("marks are still deleted when the anchors line up",
              classes("- `[00:30]`ORPHAN` and `SPOKEN` `[01:10]`"),
              [("00:30", "SPOKEN"), ("01:10", "SPOKEN")])
        # The header is the wire. Splicing the new member into the disposition
        # block moved seven columns while the docstring claimed none moved, so
        # the INDEX of every pre-slice-12 column is pinned here, not the tuple.
        header = report([blank_row("x")]).split("\n")[0].split("\t")
        check("no pre-slice-12 --report column moved",
              [header.index(c) for c in
               ("file", "video_id", "manifest", "anchors", "resolved",
                "unresolved", "unchecked", "witnessed-spoken",
                "unchecked-spoken", "unbound-inferred", "unattributable",
                "verbatim", "foreign", "frames", "cited_frames",
                "uncited_frames", "longest_gap", "frame_selectivity",
                "cue_selectivity", "blanket")],
              list(range(20)))
        check("cross-referenced is the last column of all",
              (header[-1], len(header)), ("cross-referenced", 21))

        # the coverage ledger, and --max-gap
        check("ledger", coverage_ledger([30, 70, 200], {30}, 600),
              (2, 570, (30, 600)))
        check("a fully cited run has no gap",
              coverage_ledger([10, 20, 30], {10, 20, 30}, 600), (0, 0, (0, 0)))
        full = notes / "2026-01-01--full--TESTID.md"
        full.write_text(_note("- `ON-SCREEN` both `[00:30]` `[01:10]`"),
                        encoding="utf-8")
        d, row = check_note(full, root, max_gap=60)
        check("citing every frame is never a coverage defect", d, [])
        d, row = check_note(good, root, max_gap=30)
        check("--max-gap fires on a real uncited frame",
              "E-COVERAGE-GAP" in " ".join(d), True)
        check("the gap is bounded by the runtime", row["longest_gap"], 600 - 30)

    print(f"selftest OK ({cases} cases)")
    return 0


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    # `root` is a parameter so the selftest can drive the REAL argparse wiring
    # against a fixture tree. Without it `selftest()` never called `main()`, and
    # both review lanes found the same consequence independently: inverting
    # `not args.no_require_touched_class` -- flipping the slice-13 default back
    # to slice 12's opt-in -- left all 132 cases green and `--check` at exit 0,
    # because with 0 cross-referenced anchors the corpus output is identical
    # either way. The default was pinned one layer below the surface a user
    # touches.
    root = root or POLICY.root(fallback=Path(__file__).resolve().parent.parent)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*", type=Path,
                    help="notes or directories; defaults to notes/")
    ap.add_argument("--check", action="store_true",
                    help="reconcile anchors against manifests (the default)")
    ap.add_argument("--report", action="store_true",
                    help="TSV on stdout, one row per note; defects go to stderr")
    ap.add_argument("--build", metavar="VIDEO_ID",
                    help="build a manifest; use --build=<id>, ids may start '-'")
    ap.add_argument("--adopt", metavar="VIDEO_ID",
                    help="copy the run somewhere durable, then --build from the "
                         "copy; use --adopt=<id>")
    ap.add_argument("--runs-root", type=Path, default=RUNS_ROOT,
                    help=f"where --adopt copies to (default {RUNS_ROOT})")
    ap.add_argument("--with-video", action="store_true",
                    help="--adopt keeps the video file too (large, and it is "
                         "re-downloadable; the keyframes are not)")
    ap.add_argument("--run-dir", action="append", type=Path, default=[],
                    help="a watch.py run directory; repeatable")
    ap.add_argument("--out", type=Path,
                    help="where to write the manifest (default notes/anchors/)")
    ap.add_argument("--no-require-manifest", action="store_true",
                    help="stop treating an unusable manifest as a defect")
    ap.add_argument("--no-require-evidence-class", action="store_true",
                    help="stop treating an unattributed anchor as a defect")
    ap.add_argument("--no-require-touched-class", action="store_true",
                    help="stop treating a cross-referenced anchor as a defect "
                         "(on by default since slice 13; all 106 were converted)")
    ap.add_argument("--control", action="store_true",
                    help="run the binding rule's metamorphic control on notes/")
    ap.add_argument("--verify-artifacts", action="store_true",
                    help="re-stat and re-hash every frame a manifest names")
    ap.add_argument("--max-gap", type=int, metavar="SECONDS",
                    help="fail a span this long holding an uncited frame")
    ap.add_argument("--selftest", action="store_true",
                    help="known-answer cases")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.adopt and args.build:
        print(f"{PROG}: --adopt already builds; drop --build", file=sys.stderr)
        return 2
    build_id = args.build or args.adopt
    if build_id:
        if args.check or args.report or args.paths:
            print(f"{PROG}: --build does not combine with --check/--report/paths",
                  file=sys.stderr)
            return 2
        if not args.run_dir:
            print(f"{PROG}: --build needs at least one --run-dir", file=sys.stderr)
            return 2
        missing = [d for d in args.run_dir if not d.is_dir()]
        if missing:
            print(f"{PROG}: no such run dir: {missing[0]}", file=sys.stderr)
            return 2
        run_dirs = args.run_dir
        if args.adopt:
            # Adopt BEFORE building, so the manifest's `# source` rows and every
            # frame path it names point at the durable copy. Building first and
            # copying after leaves a manifest that verifies only until reboot.
            run_dirs, bad = adopt_run(args.adopt, args.run_dir, args.runs_root,
                                      args.with_video)
            for line in bad:
                print(line, file=sys.stderr)
            if bad:
                return 1
            for d in run_dirs:
                print(f"# adopted\t{d}", file=sys.stderr)
        try:
            text, stats = build_manifest(build_id, run_dirs)
        except ValueError as exc:
            print(f"{PROG}: refusing to build {build_id}: {exc}", file=sys.stderr)
            return 2
        out = args.out or manifest_path(root, build_id)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"{PROG}: cannot write {out}: {exc}", file=sys.stderr)
            return 2
        shown = out.relative_to(root) if out.is_relative_to(root) else out
        print(f"{shown}: {stats['frames']} frame(s), {stats['cues']} cue "
              f"span(s), {stats['duration']}s, namespaces "
              f"{','.join(stats['namespaces']) or 'none'}"
              + (f", {stats['unstamped']} unstamped excluded"
                 if stats["unstamped"] else "")
              + (f", {stats['collisions']} collision(s) excluded"
                 if stats["collisions"] else ""))
        return 0

    files = collect_notes([p.resolve() for p in args.paths]
                          or [root / POLICY.notes_dir()])

    if args.control:
        lines, ok = binding_control(files)
        for line in lines:
            print(line, file=sys.stderr)
        return 0 if ok else 1

    defects: list[str] = []
    rows: list[dict] = []
    for f in files:
        d, row = check_note(f, root, not args.no_require_manifest, args.max_gap,
                            not args.no_require_evidence_class,
                            args.verify_artifacts,
                            not args.no_require_touched_class)
        defects.extend(d)
        rows.append(row)
    defects += sweep_manifests(root, {r["video_id"] for r in rows if r["video_id"]})

    if args.report:
        print(report(rows))
    # Defects and commentary go to stderr so `--report | cut -f4` works, the
    # same split resolve_note.py and demote_note.py already use.
    for line in defects:
        print(line, file=sys.stderr)
    for line in summarise(rows, root):
        print(line, file=sys.stderr)
    print(f"# {len(defects)} defect(s)", file=sys.stderr)
    return 1 if defects else 0


if __name__ == "__main__":
    raise SystemExit(main())
