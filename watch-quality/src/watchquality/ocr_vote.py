#!/usr/bin/env python3
"""One-sided fuzzy pixel vote for watch-quality notes.

Slice 5 of docs/note-quality-plan-round-2.md, branch 3 ("Witness
independence"). Captions and whisper are one witness class, AUDIO, because both
are text-from-audio; a proper noun asserted from audio alone has one witness
counted twice. This tool asks the only independent channel available -- the
pixels -- whether it agrees, and it is allowed to answer in one direction only.

THE VOTE IS ONE-SIDED, AND THAT IS THE WHOLE DESIGN.

  PROMOTE  a `SPOKEN` or `INFERRED` claim whose proper noun fuzzy-matches text
           OCR'd from a frame near its anchor. The pixels corroborate; the note
           may be upgraded by hand.
  FLAG     an `ON-SCREEN` claim whose proper noun matches nothing OCR'd near
           its anchor. Reported, never demoted.

It may never DEMOTE a tag and never CORRECT a spelling. Measured reason, on the
full-resolution re-read (22 frames at 1280x720): `full_27m56s.jpg` OCRs
BOTH `Whitfield` and `Witfeld`, and other frames in the same run produce
`Whitfeild` and `Vhitfeeld`. A channel that emits the right token and three
wrong ones for the same word can corroborate a spelling and can never correct
one. Round 2 recorded that exact match was disproved; that was measured on
512x288 frames -- see the resolution census below, which revises it.

RESOLUTION DECIDES VIABILITY, so the tool measures it rather than assuming:

  1280x720 (focus re-read)   22 frames, 19 textful (86%), median 204 words
  1024x576 (--detail high)   15 frames, 12 textful (80%), median  53 words
   512x288 (watch default)   50 frames, 11 textful (22%), median   2 words
   512x288 (low-res run)      19 frames,  1 textful ( 5%), median   0 words

At the pipeline's default thumbnail size the pixel channel is effectively
absent: `Whitfield` degrades to `hitf`. So a run whose textful fraction is
under --min-textful declares `pixel channel unavailable` once, rather than
flagging forty claims for a channel that was never there. That is the plan's
own child item, and it is the difference between a work queue and forty
apologies.

Exit is 0 unless --require-witness is passed or the tool itself fails: a
one-sided vote that blocks is no longer one-sided. Silence is still not
rendered as agreement -- every run prints how many frames were readable, how
many claims were examined, and how many went unwitnessed.

Usage:
    scripts/ocr_vote.py --index=VIDEO_ID      # OCR the frames a manifest names
    scripts/ocr_vote.py                       # --check over notes/
    scripts/ocr_vote.py --check PATH...
    scripts/ocr_vote.py --check --window 45   # seconds either side of an anchor
    scripts/ocr_vote.py --check --require-witness
    scripts/ocr_vote.py --selftest

Needs `tesseract` on PATH for --index only; --check reads the cached index at
notes/.ocr/<video_id>.tsv. Frames are fed on stdin, because a sandboxed run may
be denied a direct read of the run directory while the same bytes pipe fine.

Exit: 0 clean, 1 defects found (--require-witness), 2 usage or IO error.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


from .anchor_manifest import (EVIDENCE_CLASSES, RE_FRAME_STAMP,  # noqa: E402
                             collect_notes, hms, manifest_path, note_blocks,
                             read_manifest)
from .resolve_note import (RE_VIDEO_ID, anchor_seconds, safe_read,  # noqa: E402
                          split_frontmatter)
from .wq_policy import load as load_policy  # noqa: E402

PROG = "ocr_vote.py"
# Read as a gate by `watch-audit`, with these flags (see `resolve_note`).
GATE_FLAGS: tuple[str, ...] = ("--check",)
PIXEL_CLASS = "ON-SCREEN"
AUDIO_CLASSES = ("SPOKEN", "INFERRED")

# A frame is textful when OCR returns at least this many words. Below it the
# read is noise, not a witness.
TEXTFUL_WORDS = 5
# Fraction of a run's frames that must be textful before the vote runs at all.
MIN_TEXTFUL = 0.25
# Seconds either side of an anchor whose frames may witness it. A slide stays
# on screen, so the window is generous; it only ever ADDS witnesses.
WINDOW = 30
# Edit distance allowed by length. `Witfeld` -> `Whitfield` is 2.
def tolerance(token: str) -> int:
    return 2 if len(token) >= 6 else 1


OCR_DIR = load_policy().ocr_subdir()
OCR_HEADER = "frame\tseconds\tsha256\twidth\theight\twords\ttext"

RE_TOKEN = re.compile(r"\b[A-Z][A-Za-z][A-Za-z-]{2,}\b")
RE_OCR_TOKEN = re.compile(r"[A-Za-z][A-Za-z-]{2,}")
# Capitalised words that carry no identity. Anything here is not a proper noun
# worth a pixel witness, and flagging them would bury the ones that are.
STOPLIST = {
    "the", "this", "that", "these", "those", "there", "then", "than", "when",
    "what", "where", "which", "while", "why", "how", "and", "but", "for",
    "with", "without", "from", "into", "onto", "over", "under", "after",
    "before", "because", "since", "you", "your", "yours", "they", "them",
    "their", "his", "her", "hers", "its", "one", "two", "three", "four",
    "five", "six", "seven", "eight", "nine", "ten", "not", "nothing", "none",
    "every", "each", "any", "all", "both", "same", "other", "another", "such",
    "only", "just", "still", "even", "also", "here", "now", "once", "again",
    "most", "more", "less", "least", "much", "many", "few", "own", "out",
    "off", "up", "down", "left", "right", "first", "second", "third", "last",
    "next", "new", "old", "good", "bad", "big", "small", "long", "short",
    "claims", "falsifier", "usable", "speculation", "run", "notes", "action",
    "inferred", "spoken", "screen", "orphan", "video", "note", "frame",
    # Spelled-out numbers open sentences and are not identities. The vote may
    # not correct a number anyway, so flagging one can only ever be noise.
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty",
    "sixty", "seventy", "eighty", "ninety", "hundred", "thousand", "million",
}


def ocr_path(root: Path, video_id: str) -> Path:
    return root / load_policy().notes_dir() / OCR_DIR / f"{video_id}.tsv"


def levenshtein(a: str, b: str, cap: int = 3) -> int:
    """Edit distance, short-circuited above `cap` because that is all we ask."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


# --------------------------------------------------------------------------
# indexing
# --------------------------------------------------------------------------

def run_tesseract(path: Path, psm: int = 6) -> str:
    with path.open("rb") as fh:
        proc = subprocess.run(["tesseract", "stdin", "-", "--psm", str(psm)],
                              stdin=fh, capture_output=True)
    return " ".join(proc.stdout.decode("utf-8", "replace").split())


def image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image
    except ImportError:
        return (0, 0)
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (0, 0)


def best_pixels(name: str, seconds: int, dirs: list[Path]) -> Path | None:
    """The largest image available for that second, which may not be the file
    the manifest names.

    The manifest keeps the FIRST file it saw for a second, so on the densest run
    a 512x288 thumbnail wins over the 1280x720 focus re-read of the same moment
    -- and at 512x288 `Whitfield` OCRs as `hitf`. Identity is the manifest's
    job; reading pixels is this tool's, and it should read the best ones there
    are. The row records which file was actually read.
    """
    best, area = None, -1
    for d in dirs:
        for p in d.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            m = RE_FRAME_STAMP.search(p.name)
            if p.name != name and not m:
                continue
            if p.name != name and m:
                hours = int(m.group(1)) if m.group(1) else 0
                if hours * 3600 + int(m.group(2)) * 60 + int(m.group(3)) != seconds:
                    continue
            w, h = image_size(p)
            size = w * h or p.stat().st_size
            if size > area:
                best, area = p, size
    return best


def build_index(root: Path, video_id: str, psm: int = 6) -> tuple[str, dict]:
    """OCR every frame the manifest names, once, keyed by its sha256.

    Raises ValueError when the manifest is missing or names no frames.
    """
    mpath = manifest_path(root, video_id)
    if not mpath.is_file():
        raise ValueError(f"no manifest at notes/anchors/{video_id}.tsv")
    man = read_manifest(mpath)
    if not man.frames:
        raise ValueError("manifest carries no frames namespace, so there are "
                         "no pixels to read")
    text, _ = safe_read(mpath)
    shas = {}
    for line in (text or "").splitlines():
        cols = line.split("\t")
        if len(cols) == 5 and cols[0] == "frame":
            shas[cols[3]] = cols[4]
    dirs = [Path(s) for s in man.sources]

    rows, missing, textful, upgraded = [], 0, 0, 0
    for sec in sorted(man.frames):
        name = man.frames[sec]
        hit = best_pixels(name, sec, dirs)
        if hit is None:
            missing += 1
            continue
        upgraded += hit.name != name
        w, h = image_size(hit)
        body = run_tesseract(hit, psm)
        words = len(body.split())
        textful += words >= TEXTFUL_WORDS
        rows.append(f"{hit.name}\t{sec}\t{shas.get(name, '-')}\t{w}\t{h}\t"
                    f"{words}\t{body}")
    if not rows:
        raise ValueError(f"none of the {len(man.frames)} frames named by the "
                         f"manifest could be found under "
                         f"{', '.join(man.sources) or 'nowhere'}")
    head = [f"# video_id\t{video_id}", f"# psm\t{psm}",
            f"# frames\t{len(rows)}", f"# missing\t{missing}",
            f"# textful\t{textful}", f"# upgraded\t{upgraded}"]
    stats = {"frames": len(rows), "missing": missing, "textful": textful,
             "upgraded": upgraded, "fraction": textful / len(rows)}
    return "\n".join(head + [OCR_HEADER] + rows) + "\n", stats


class Index:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.defects: list[str] = []

    @property
    def textful(self) -> list[dict]:
        return [r for r in self.rows if r["words"] >= TEXTFUL_WORDS]

    @property
    def fraction(self) -> float:
        return len(self.textful) / len(self.rows) if self.rows else 0.0

    def near(self, seconds: int, window: int) -> list[dict]:
        return [r for r in self.textful
                if abs(r["seconds"] - seconds) <= window]


def read_index(path: Path) -> Index:
    idx = Index()
    rel = f"notes/{OCR_DIR}/{path.name}"
    text, why = safe_read(path)
    if text is None:
        idx.defects.append(f"{rel}:1 E-OCR-UNREADABLE {why}")
        return idx
    for i, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.startswith("#") or line == OCR_HEADER:
            continue
        cols = line.split("\t")
        if len(cols) < 6:
            idx.defects.append(f"{rel}:{i} E-OCR-ROW malformed row")
            continue
        try:
            seconds, words = int(cols[1]), int(cols[5])
        except ValueError:
            idx.defects.append(f"{rel}:{i} E-OCR-ROW non-numeric column")
            continue
        body = cols[6] if len(cols) > 6 else ""
        idx.rows.append({"frame": cols[0], "seconds": seconds, "sha": cols[2],
                         "words": words, "text": body,
                         # Sorted, not a set. A frame that OCRs both `Dropbox`
                         # and `dropbox` has two zero-distance candidates, and
                         # best_match returns whichever it reaches first; over a
                         # set that is Python's per-process string hash, so the
                         # same corpus printed a different PROMOTE line run to
                         # run. Measured: two runs of the same gate over the same
                         # notes differed on 4 lines, which quietly made every
                         # "byte-identical to the baseline" check a coin flip.
                         "tokens": sorted(set(RE_OCR_TOKEN.findall(body)))})
    return idx


# --------------------------------------------------------------------------
# the vote
# --------------------------------------------------------------------------

def candidate_tokens(text: str) -> list[str]:
    """Proper nouns worth a pixel witness: capitalised, not in the stoplist.

    Deliberately narrow. The plan's wider rule (digits, currency, dates) is not
    here, because a number that OCR mangles is exactly what this vote may not
    correct, and flagging every figure would bury the names.
    """
    out = []
    for tok in RE_TOKEN.findall(re.sub(r"`[^`]*`", " ", text)):
        head = tok.lower().split("-")[0]
        if head in STOPLIST or tok.lower() in STOPLIST or tok.isupper():
            continue
        if tok not in out:
            out.append(tok)
    return out


def best_match(token: str, rows: list[dict]) -> tuple[str, int, dict | None]:
    """(nearest OCR token, its distance, the frame it came from)."""
    best, dist, where = "", tolerance(token) + 1, None
    for row in rows:
        for cand in row["tokens"]:
            if abs(len(cand) - len(token)) > tolerance(token):
                continue
            d = levenshtein(token.lower(), cand.lower(), tolerance(token))
            if d < dist:
                best, dist, where = cand, d, row
                if d == 0:
                    return best, dist, where
    return best, dist, where


def vote_note(path: Path, root: Path, window: int = WINDOW,
              require_witness: bool = False) -> tuple[list[str], list[str], dict]:
    """(defects, advisory lines, row)."""
    rel = str(path.relative_to(root) if path.is_relative_to(root) else path)
    row = {"file": rel, "video_id": "", "index": "none", "frames": 0,
           "textful": 0, "claims": 0, "tokens": 0, "promote": 0, "flag": 0,
           "witnessed": 0}

    text, why = safe_read(path)
    if text is None:
        return [f"{rel}:1 E-READ {why}"], [], row
    split = split_frontmatter(text)
    if split is None:
        return [f"{rel}:1 E-FRONTMATTER no YAML frontmatter block"], [], row
    frontmatter, body = split
    m = RE_VIDEO_ID.search(frontmatter)
    if not m:
        return [f"{rel}:1 E-NO-VIDEO-ID cannot address an OCR index"], [], row
    video_id = m.group(1)
    row["video_id"] = video_id

    ipath = ocr_path(root, video_id)
    if not ipath.is_file():
        return [], [], row
    idx = read_index(ipath)
    row["index"] = "present"
    row["frames"] = len(idx.rows)
    row["textful"] = len(idx.textful)
    if idx.defects:
        return idx.defects, [], row
    if idx.fraction < MIN_TEXTFUL:
        # Declared once, in frontmatter terms, instead of forty inline
        # apologies for a channel that was never present.
        return [], [f"# {rel}\tpixel channel unavailable: {len(idx.textful)} of "
                    f"{len(idx.rows)} frames are textful ({idx.fraction:.0%}, "
                    f"under {MIN_TEXTFUL:.0%})"], row

    defects: list[str] = []
    advisory: list[str] = []
    start_line = len(frontmatter.split("\n")) + 3
    for block in note_blocks(body, start_line):
        if block["verbatim"]:
            continue
        joined = " ".join(t for _, t in block["lines"])
        tagged = [c for c in EVIDENCE_CLASSES if f"`{c}`" in joined]
        if len(tagged) != 1:
            continue
        seconds = [s for _, t in block["lines"] for _, s in anchor_seconds(t)]
        if not seconds:
            continue
        tokens = candidate_tokens(joined)
        if not tokens:
            continue
        row["claims"] += 1
        row["tokens"] += len(tokens)
        line = block["lines"][0][0]
        rows_near = [r for s in seconds for r in idx.near(s, window)]
        for token in tokens:
            match, dist, where = best_match(token, rows_near)
            witnessed = where is not None and dist <= tolerance(token)
            row["witnessed"] += witnessed
            if witnessed and tagged[0] in AUDIO_CLASSES:
                row["promote"] += 1
                advisory.append(
                    f"{rel}:{line} PROMOTE {token} is {tagged[0]} but "
                    f"{where['frame']} at {hms(where['seconds'])} OCRs "
                    f"`{match}` (distance {dist})")
            elif not witnessed and tagged[0] == PIXEL_CLASS:
                row["flag"] += 1
                near = (f"nearest `{match}` distance {dist}" if match
                        else "nothing similar in any frame")
                msg = (f"{rel}:{line} FLAG {token} is {PIXEL_CLASS} but no "
                       f"frame within {window}s of "
                       f"{', '.join(hms(s) for s in seconds[:2])} OCRs it "
                       f"({near})")
                advisory.append(msg)
                if require_witness:
                    defects.append(msg.replace(" FLAG ", " E-NO-PIXEL-WITNESS "))
    return defects, advisory, row


def summarise(rows: list[dict]) -> list[str]:
    indexed = [r for r in rows if r["index"] == "present"]
    out = [f"# {len(rows)} notes checked, {len(indexed)} with an OCR index, "
           f"{len(rows) - len(indexed)} without",
           f"# {sum(r['textful'] for r in indexed)} of "
           f"{sum(r['frames'] for r in indexed)} indexed frames are textful; "
           f"{sum(r['claims'] for r in indexed)} claims carried "
           f"{sum(r['tokens'] for r in indexed)} proper noun(s), "
           f"{sum(r['witnessed'] for r in indexed)} witnessed",
           f"# {sum(r['promote'] for r in indexed)} promote(s), "
           f"{sum(r['flag'] for r in indexed)} flag(s); this vote never demotes "
           f"a tag and never corrects a spelling"]
    for r in rows:
        if r["index"] == "none" and r["video_id"]:
            out.append(f"# no OCR index\t{r['file']}\t{r['video_id']}")
    return out


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

# Verbatim OCR output of /tmp/dk-frames-rt/full_27m56s.jpg, tesseract 5.5.3
# --psm 6, 2026-08-05. The frame yields the right spelling AND a mangled one,
# which is the whole argument for a one-sided vote.
WITNESS_FRAME = ("Marion Whitfield the collected field notes author "
                   "Witfeld a field guide to influence")


def _index_text(rows: list[str], video_id: str = "TESTID") -> str:
    return ("\n".join([f"# video_id\t{video_id}", "# psm\t6",
                       f"# frames\t{len(rows)}", "# missing\t0",
                       f"# textful\t{len(rows)}", OCR_HEADER] + rows) + "\n")


def _row(frame: str, seconds: int, body: str) -> str:
    return f"{frame}\t{seconds}\t-\t1280\t720\t{len(body.split())}\t{body}"


def _note(body: str, video_id: str = "TESTID") -> str:
    return (f"---\nvideo_id: {video_id}\nduration: \"10:00\"\n---\n\n"
            f"# t\n\n{body}\n")


def selftest() -> int:
    cases = 0

    def check(label: str, got, want) -> None:
        nonlocal cases
        cases += 1
        if got != want:
            print(f"FAIL {label}: got {got!r} want {want!r}", file=sys.stderr)
            raise SystemExit(1)

    # The harness decides what ran. `check`'s calls ARE this module's cases,
    # which is why its name is handed over here rather than kept private, and
    # `done()` below is where the evidence goes and a wrong answer is refused.
    from watchquality import selftest_proof
    proof = selftest_proof.begin(check)

    # An INVENTED surname and an invented misreading of it. The pair here was
    # once a real one taken from a private corpus, and it sat in a public fork
    # for as long as the refused-literal match was case-sensitive.
    check("levenshtein", levenshtein("vandell", "vandellis"), 2)
    check("levenshtein caps", levenshtein("a", "abcdefgh"), 4)
    check("tolerance by length", (tolerance("Ivy"), tolerance("Whitfield")), (1, 2))
    check("stoplist drops sentence openers",
          candidate_tokens("The Whitfield model"), ["Whitfield"])
    check("code spans are not claims",
          candidate_tokens("`ON-SCREEN` the `Whitfield` model"), [])
    check("all-caps tags are not proper nouns",
          candidate_tokens("OKAPILANE and Trellick"), ["Trellick"])

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        idir = root / "notes" / OCR_DIR
        idir.mkdir(parents=True)
        notes = root / "notes"

        # FIXTURE 1 -- the plan's first: a known token matches at distance <= 2.
        (idir / "TESTID.tsv").write_text(_index_text([
            _row("full_27m56s.jpg", 1676, WITNESS_FRAME),
            _row("full_10m00s.jpg", 600, "a different slide about writing "
                                         "systems and leverage entirely"),
        ]), encoding="utf-8")

        # FIXTURE 2 -- the plan's second, and the assertion that IS the design:
        # `Witfeld` fuzzy-resolves to `Whitfield` while an exact matcher fails.
        idx = read_index(idir / "TESTID.tsv")
        rows_near = idx.near(1676, WINDOW)
        exact = any("Witfeld" == t for r in rows_near for t in r["tokens"])
        match, dist, where = best_match("Witfeld", rows_near)
        check("the mangled token is present verbatim", exact, True)
        check("an exact matcher would not resolve it to the truth",
              any(t == "Whitfield" and t == "Witfeld" for r in rows_near
                  for t in r["tokens"]), False)
        check("fuzzy resolves Witfeld to Whitfield within tolerance",
              (match in ("Whitfield", "Witfeld"), dist <= tolerance("Witfeld")),
              (True, True))
        check("and it names the frame it came from",
              where["frame"], "full_27m56s.jpg")

        # a SPOKEN claim the pixels corroborate is PROMOTED, never rewritten
        spoken = notes / "2026-01-01--a--TESTID.md"
        spoken.write_text(_note(
            "- `SPOKEN` Marion Whitfield wrote the book `[27:56]`"),
            encoding="utf-8")
        d, adv, row = vote_note(spoken, root)
        # Two proper nouns in one claim, `Marion` and `Whitfield`, so the vote
        # is per token rather than per claim.
        check("a corroborated SPOKEN claim promotes",
              (d, row["promote"], row["flag"]), ([], 2, 0))
        check("the promote line names the frame",
              "full_27m56s.jpg" in adv[0], True)
        check("the note is not rewritten",
              spoken.read_text(encoding="utf-8").count("Whitfield"), 1)

        # an ON-SCREEN claim with no pixel witness is FLAGGED, never demoted
        onscreen = notes / "2026-01-01--b--TESTID.md"
        onscreen.write_text(_note(
            "- `ON-SCREEN` the Kajabi dashboard is open `[27:56]`"),
            encoding="utf-8")
        d, adv, row = vote_note(onscreen, root)
        check("an unwitnessed ON-SCREEN claim flags",
              (d, row["flag"]), ([], 1))
        check("flagging is advisory by default", d, [])
        d, adv, row = vote_note(onscreen, root, require_witness=True)
        check("--require-witness makes it a defect",
              "E-NO-PIXEL-WITNESS" in d[0], True)

        # the window is what keeps a far-away frame from witnessing
        far = notes / "2026-01-01--c--TESTID.md"
        far.write_text(_note("- `ON-SCREEN` Whitfield on screen `[00:10]`"),
                       encoding="utf-8")
        check("a frame outside the window does not witness",
              vote_note(far, root, window=5)[2]["flag"], 1)
        check("a wide enough window does", vote_note(far, root, window=2000)[2]["flag"], 0)

        # a thumbnail-resolution run declares the channel absent, once
        (idir / "THUMBS.tsv").write_text(_index_text(
            [_row("frame_0001_t00m30s.jpg", 30, "reste"),
             _row("frame_0002_t01m10s.jpg", 70, ""),
             _row("frame_0003_t01m50s.jpg", 110, "a b"),
             _row("frame_0004_t02m30s.jpg", 150, "textful enough to count as a "
                                                 "real read here"),
             _row("frame_0005_t03m10s.jpg", 190, "xx")],
            video_id="THUMBS"), encoding="utf-8")
        thumbs = notes / "2026-01-01--d--THUMBS.md"
        thumbs.write_text(_note(
            "- `ON-SCREEN` Whitfield on screen `[00:30]`", video_id="THUMBS"),
            encoding="utf-8")
        d, adv, row = vote_note(thumbs, root)
        check("an unreadable run declares itself instead of flagging",
              (d, row["flag"], "pixel channel unavailable" in adv[0]),
              ([], 0, True))

        # no index at all is silent per note but counted in the summary
        none = notes / "2026-01-01--e--NOINDEX.md"
        none.write_text(_note("- `ON-SCREEN` Whitfield `[00:30]`",
                              video_id="NOINDEX"), encoding="utf-8")
        d, adv, row = vote_note(none, root)
        check("no index is not a defect", (d, adv, row["index"]), ([], [], "none"))
        check("but the summary names it",
              any("no OCR index" in x for x in summarise([row])), True)

        # a malformed index row is reported, not skipped
        (idir / "BROKEN.tsv").write_text(
            _index_text([_row("a.jpg", 30, "fine")], video_id="BROKEN")
            + "a.jpg\tnope\n", encoding="utf-8")
        check("malformed index row reported",
              sum("E-OCR-ROW" in x
                  for x in read_index(idir / "BROKEN.tsv").defects), 1)

    proof.done()
    print(f"selftest OK ({cases} cases)")
    return 0


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    root = load_policy().root(fallback=Path(__file__).resolve().parent.parent)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*", type=Path,
                    help="notes or directories; defaults to notes/")
    ap.add_argument("--check", action="store_true",
                    help="run the vote (the default)")
    ap.add_argument("--index", metavar="VIDEO_ID",
                    help="OCR the frames a manifest names; use --index=<id>")
    ap.add_argument("--psm", type=int, default=6,
                    help="tesseract page segmentation mode for --index")
    ap.add_argument("--window", type=int, default=WINDOW, metavar="SECONDS",
                    help="how far from an anchor a frame may witness it")
    ap.add_argument("--require-witness", action="store_true",
                    help="make an unwitnessed ON-SCREEN proper noun a defect")
    ap.add_argument("--selftest", action="store_true", help="known-answer cases")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.index:
        if args.check or args.paths:
            print(f"{PROG}: --index does not combine with --check/paths",
                  file=sys.stderr)
            return 2
        if shutil.which("tesseract") is None:
            print(f"{PROG}: tesseract is not on PATH", file=sys.stderr)
            return 2
        try:
            text, stats = build_index(root, args.index, args.psm)
        except ValueError as exc:
            print(f"{PROG}: cannot index {args.index}: {exc}", file=sys.stderr)
            return 2
        out = ocr_path(root, args.index)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"{PROG}: cannot write {out}: {exc}", file=sys.stderr)
            return 2
        print(f"notes/{OCR_DIR}/{args.index}.tsv: {stats['frames']} frame(s), "
              f"{stats['textful']} textful ({stats['fraction']:.0%})"
              + (f", {stats['upgraded']} read at a higher resolution than the "
                 f"manifest names" if stats["upgraded"] else "")
              + (f", {stats['missing']} named frame(s) not found"
                 if stats["missing"] else "")
              + ("" if stats["fraction"] >= MIN_TEXTFUL
                 else f" -- under {MIN_TEXTFUL:.0%}, so the vote will declare "
                      f"the pixel channel unavailable"))
        return 0

    files = collect_notes([p.resolve() for p in args.paths]
                          or [root / load_policy().notes_dir()], root)
    defects: list[str] = []
    advisory: list[str] = []
    rows: list[dict] = []
    for f in files:
        d, adv, row = vote_note(f, root, args.window, args.require_witness)
        defects.extend(d)
        advisory.extend(adv)
        rows.append(row)

    for line in advisory:
        print(line)
    for line in defects:
        print(line, file=sys.stderr)
    for line in summarise(rows):
        print(line, file=sys.stderr)
    print(f"# {len(defects)} defect(s)", file=sys.stderr)
    return 1 if defects else 0


if __name__ == "__main__":
    raise SystemExit(main())
