#!/usr/bin/env python3
"""What was SPOKEN at a second, from the recovered caption tracks.

This is the evidence tool behind every `SPOKEN` label slice 8 wrote into the
corpus. It lived in /tmp for one session, which meant the citations in the
write-up could not be re-derived from the repo -- a reviewer had to reimplement
WebVTT parsing to check them. Two lines above, that same write-up rejects /tmp
for run artifacts. So it lives here now.

WHAT IT CAN AND CANNOT WITNESS. A caption track can confirm that words were
said near a second, so it can witness `SPOKEN`. It can never witness
`ON-SCREEN`: silence in the audio is not evidence that something was on the
screen. Absence here is a reason to go and look at a frame, never a class.

Tracks are the ones `watch.py --detail transcript --out-dir` leaves behind,
searched under the roots `search_roots` names in watch-quality.toml (print them
with `scripts/wq_policy.py`). The `en-orig` track is preferred: it is the one
YouTube generated, not a translation.

Usage:
    scripts/say_captions.py VIDEO_ID MM:SS [MM:SS ...] [--window N]
    scripts/say_captions.py VIDEO_ID --find "a phrase"
    scripts/say_captions.py --selftest

Exit: 0 found, 1 nothing matched, 2 usage or no track on disk.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path


from .wq_policy import load as load_policy  # noqa: E402

PROG = "say_captions.py"

RE_SPAN = re.compile(
    r"(?:(\d+):)?(\d{2}):(\d{2})\.(\d{3})\s+-->\s+(?:(\d+):)?(\d{2}):(\d{2})\.(\d{3})")
RE_TAG = re.compile(r"<[^>]+>")
# A YouTube id is 11 characters of [A-Za-z0-9_-]; one that starts with a dash
# looks exactly like a flag bundle to argparse. See `dash_safe`.
RE_DASH_ID = re.compile(r"^-[A-Za-z0-9_-]{10}$")
# Where a run's caption track may be found, in order. Corpus-specific, so it
# comes from watch-quality.toml (scripts/wq_policy.py).
SEARCH_ROOTS = load_policy().search_roots()
WINDOW = 6


def clock(hours: str | None, mins: str, secs: str, ms: str) -> float:
    return int(hours or 0) * 3600 + int(mins) * 60 + int(secs) + int(ms) / 1000


def seconds_of(raw: str) -> int:
    out = 0
    for part in raw.strip("[]`").split(":"):
        out = out * 60 + int(part)
    return out


def hms(seconds: float) -> str:
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def normalise(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())


def find_tracks(video_id: str, roots=SEARCH_ROOTS) -> list[Path]:
    """Every .vtt under a directory named for this video."""
    out: set[Path] = set()
    for root in roots:
        if root.is_dir():
            out.update(root.glob(f"**/{video_id}/**/*.vtt"))
    return sorted(out)


def pick_track(paths: list[Path]) -> Path:
    # `en-orig` is YouTube's own; anything else may be a machine translation.
    return sorted(paths, key=lambda p: (".en-orig." not in p.name, len(p.name)))[0]


def cues(path: Path) -> list[tuple[float, float, str]]:
    """(start, end, text). Auto-captions roll, so consecutive cues repeat most
    of their words; callers dedupe rather than this parser, which stays literal
    about what the file says."""
    out: list[tuple[float, float, str]] = []
    lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
    for i, line in enumerate(lines):
        m = RE_SPAN.search(line)
        if not m:
            continue
        body = []
        for nxt in lines[i + 1:]:
            if not nxt.strip() or RE_SPAN.search(nxt):
                break
            body.append(RE_TAG.sub("", nxt).strip())
        said = " ".join(x for x in body if x)
        if said:
            out.append((clock(*m.groups()[:4]), clock(*m.groups()[4:]), said))
    return out


def around(rows, seconds: int, window: int = WINDOW) -> str:
    seen: list[str] = []
    for start, end, said in rows:
        if end >= seconds - window and start <= seconds + window and said not in seen:
            seen.append(said)
    return re.sub(r"\s+", " ", " ".join(seen))


def where(rows, phrase: str) -> list[str]:
    want = normalise(phrase)
    return [hms(start) for start, _, said in rows if want in normalise(said)]


def selftest() -> int:
    fails = 0
    cases = 0

    def check(name, got, want):
        nonlocal fails, cases
        cases += 1
        if got != want:
            fails += 1
            print(f"FAIL {name}: got {got!r} want {want!r}")

    # The harness decides what ran. `check`'s calls ARE this module's cases,
    # which is why its name is handed over here rather than kept private. This
    # checker records rather than raises, so the refusal `done()` provokes
    # lands in `fails` and the return below is what carries it out.
    from watchquality import selftest_proof
    proof = selftest_proof.begin(check)

    check("MM:SS.mmm parses", clock(None, "01", "05", "500"), 65.5)
    check("HH:MM:SS.mmm parses", clock("1", "01", "05", "000"), 3665.0)
    check("anchor seconds past the hour", seconds_of("`[1:05:18]`"), 3918)
    check("bare MM:SS", seconds_of("25:41"), 1541)
    # A leading-dash id must not read as a flag bundle. The shape is the whole
    # test, so these two ids are synthetic; a real one exited 2 here until
    # `dash_safe` landed (slice 12), and that measurement is recorded in
    # docs/reviews/gate-source-corpus-references.md rather than in this fixture.
    check("leading-dash id survives argparse",
          dash_safe(["-Xk4Rm2Qp7Z", "22:18"]), (["22:18"], "-Xk4Rm2Qp7Z"))
    check("a flag before the id is still a flag",
          dash_safe(["--window", "9", "-Xk4Rm2Qp7Z"]),
          (["--window", "9"], "-Xk4Rm2Qp7Z"))
    check("an explicit -- is left alone",
          dash_safe(["--", "-Xk4Rm2Qp7Z"]), (["--", "-Xk4Rm2Qp7Z"], None))
    check("an ordinary id is untouched",
          dash_safe(["Bt7Wn3Kd9Qy", "16:07"]), (["Bt7Wn3Kd9Qy", "16:07"], None))
    # THE OPTION AFTER THE ID MUST SURVIVE (slice-13 coverage lane, BLOCKER).
    # Fencing the id in place made `--find` a positional, so it was parsed as a
    # timestamp and crashed with exit 1 -- the same code `--find` uses for NOT
    # FOUND, so the crash read as evidence that a phrase is never spoken.
    check("an option AFTER a dash id stays an option",
          dash_safe(["-Xk4Rm2Qp7Z", "--find", "chart"]),
          (["--find", "chart"], "-Xk4Rm2Qp7Z"))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        d = root / "TESTVID" / "download"
        d.mkdir(parents=True)
        (d / "video.en-orig.vtt").write_text(
            "WEBVTT\n\n00:10.000 --> 00:13.000\nwe built a <c>redaction</c> system\n\n"
            "00:13.000 --> 00:16.000\nso that data does not leave\n",
            encoding="utf-8")
        (d / "video.en.vtt").write_text("WEBVTT\n\n00:10.000 --> 00:13.000\nx\n",
                                        encoding="utf-8")
        found = find_tracks("TESTVID", roots=(root,))
        check("both tracks found", len(found), 2)
        check("en-orig wins", pick_track(found).name, "video.en-orig.vtt")
        rows = cues(pick_track(found))
        check("cue count", len(rows), 2)
        check("markup stripped", rows[0][2], "we built a redaction system")
        check("window catches the neighbour cue", "does not leave" in around(rows, 12),
              True)
        check("phrase located", where(rows, "Redaction, system!"), ["00:10"])
        check("absent phrase is empty", where(rows, "tldraw"), [])
        check("no track is not a crash", find_tracks("NOSUCH", roots=(root,)), [])

    proof.done()
    if fails:
        print(f"selftest FAILED ({fails})")
        return 1
    # COUNTED, not typed: the literal said 12 while 16 cases ran, which is the
    # same class of stale number the review lanes keep finding in prose.
    print(f"selftest OK ({cases} cases)")
    return 0


def dash_safe(argv: list[str]) -> tuple[list[str], str | None]:
    """Let a YouTube id that STARTS with a dash be typed like any other.

    Some ids start with a dash, and argparse read one as a bundle of short
    flags: `say_captions.py <dash-id> 22:18` exited 2 with "unrecognized
    arguments" — the one video whose evidence was hardest to reach was the one
    the evidence tool refused (slice-12 coverage lane). `--` always worked;
    nobody knew to type it. An id is 11 characters of [A-Za-z0-9_-], so a
    leading-dash token of exactly that shape is an id, not a flag.

    The id is LIFTED OUT of argv and returned beside it, not fenced in place
    (slice-13 coverage lane, BLOCKER). Inserting `--` before the id makes every
    LATER token positional too, so `say_captions.py <dash-id> --find "chart"`
    parsed `--find` as a timestamp and died in `seconds_of` — and it died with
    exit 1, which `--find` also uses for NOT FOUND. The crash was
    indistinguishable from "this phrase is never spoken", on the one note whose
    evidence is hardest to reach. A fabricated negative is worse than no
    evidence, so argparse never sees the id at all.

    The runs this was measured on are named in
    docs/reviews/gate-source-corpus-references.md, which is corpus, not engine.

    Returns (argv without the id, the id or None).
    """
    if "--" in argv:
        return argv, None
    for i, tok in enumerate(argv):
        if RE_DASH_ID.match(tok):
            return argv[:i] + argv[i + 1:], tok
    return argv, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video_id", nargs="?", help="youtube id, may start '-'")
    ap.add_argument("stamps", nargs="*", help="MM:SS or H:MM:SS, backticks ok")
    ap.add_argument("--find", metavar="PHRASE", help="print every second it is said")
    ap.add_argument("--window", type=int, default=WINDOW,
                    help="seconds either side of a stamp")
    ap.add_argument("--selftest", action="store_true", help="known-answer cases")
    rest, dash_id = dash_safe(sys.argv[1:] if argv is None else argv)
    args = ap.parse_args(rest)
    if dash_id:
        # The id was pulled out before parsing, so whatever landed in video_id
        # is really the first stamp.
        if args.video_id:
            args.stamps = [args.video_id] + args.stamps
        args.video_id = dash_id

    if args.selftest:
        return selftest()
    if not args.video_id:
        ap.print_usage(sys.stderr)
        return 2

    tracks = find_tracks(args.video_id)
    if not tracks:
        print(f"{PROG}: no caption track for {args.video_id} under "
              f"{' or '.join(str(r) for r in SEARCH_ROOTS)}", file=sys.stderr)
        return 2
    path = pick_track(tracks)
    rows = cues(path)
    print(f"# {path}  ({len(rows)} cues)")

    if args.find:
        hits = where(rows, args.find)
        print(f"{args.find!r} -> {', '.join(hits) if hits else 'NOT FOUND'}")
        return 0 if hits else 1

    if not args.stamps:
        ap.print_usage(sys.stderr)
        return 2
    empty = 0
    for raw in args.stamps:
        # A usage error must NOT share exit 1 with NOT FOUND: an unparseable
        # stamp used to raise, and a caller reading exit codes recorded that as
        # evidence the phrase is never spoken.
        try:
            sec = seconds_of(raw)
        except ValueError:
            print(f"{PROG}: not a timestamp: {raw!r}", file=sys.stderr)
            return 2
        said = around(rows, sec, args.window)
        empty += not said
        print(f"[{raw.strip('[]`')}] {sec}s :: {said or '(no caption)'}")
    return 1 if empty == len(args.stamps) else 0


if __name__ == "__main__":
    raise SystemExit(main())
