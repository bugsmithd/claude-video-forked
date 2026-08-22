#!/usr/bin/env python3
"""Label-vs-pixels fixture for R1 -- slice 4 of docs/note-quality-plan-round-2.md.

R1 is image-to-timestamp mis-pairing: frames read in one message get matched to
timestamps by list position, and round 1's fix (stamping the second into every
filename) did not stop it, because the filenames arrive as a separate list from
the images. The plan's remedy is "one image per message, decoded second in the
same message envelope", and its proof column asks for a fixture the current
batch read fails.

This builds that fixture. Each frame carries a short random code in its pixels
and its second in its filename, and nothing else. The pairing is therefore only
recoverable by actually looking at each image -- position carries no signal, and
the code cannot be guessed from the filename or inferred from neighbours.

    scripts/frame_fixture.py --build --out DIR [--count 12] [--seed 7]
    # read the frames, write "<filename>\t<code>" lines to claims.tsv
    scripts/frame_fixture.py --score DIR claims.tsv

Truth lives in DIR/.answers.tsv. A reader that opens it has not run the
experiment. Exit: 0 all pairs correct, 1 any mismatch, 2 usage error.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ALPHABET = "ACDEFHJKLMNPRTUVWXY34679"  # no look-alikes
CODE_LEN = 4
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
ANSWERS = ".answers.tsv"


def load_font(size: int):
    from PIL import ImageFont
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


TITLES = ["The Writing System", "The Writing Loop", "The Reading System",
          "The Thinking Loop", "The Writing Habit", "The Reading Habit"]
BULLETS = ["capture the idea before it decays", "one input, one output, daily",
           "the outline is the argument", "publish the smallest complete thing",
           "compression is the whole skill", "revisit only what earned it"]


def build(out: Path, count: int, seed: int, start: int, step: int,
          style: str = "plain") -> int:
    """Render `count` frames whose pixels carry a code the filename does not.

    `plain` is the easy condition: one huge code on white. `slide` is the
    faithful one -- the code is small and in a corner of a text slide whose
    titles and bullets repeat across frames, which is what a real screen
    recording looks like and what the reader actually had to bind in the run
    where R1 happened.
    """
    from PIL import Image, ImageDraw
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    big, mid, small = load_font(180), load_font(44), load_font(26)
    rows = []
    used: set[str] = set()
    for i in range(count):
        second = start + i * step
        while True:
            code = "".join(rng.choice(ALPHABET) for _ in range(CODE_LEN))
            if code not in used:
                used.add(code)
                break
        name = f"frame_{second:05d}s_{second // 60:02d}m{second % 60:02d}s.jpg"
        img = Image.new("RGB", (960, 540), "white")
        draw = ImageDraw.Draw(img)
        if style == "slide":
            draw.text((60, 50), rng.choice(TITLES), fill="black", font=mid)
            picks = rng.sample(BULLETS, 4)
            for n, b in enumerate(picks):
                draw.text((80, 150 + n * 60), f"- {b}", fill="#222", font=small)
            draw.text((790, 495), code, fill="#444", font=small)
        else:
            box = draw.textbbox((0, 0), code, font=big)
            draw.text(((960 - box[2] + box[0]) / 2, (540 - box[3] + box[1]) / 2),
                      code, fill="black", font=big)
        img.save(out / name, quality=92)
        rows.append(f"{name}\t{code}")
    (out / ANSWERS).write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"{count} frames in {out}")
    print(f"truth withheld in {out / ANSWERS}")
    return 0


def read_pairs(path: Path) -> dict[str, str]:
    pairs = {}
    for row in path.read_text(encoding="utf-8").splitlines():
        if not row.strip() or row.startswith("#"):
            continue
        fields = row.split("\t") if "\t" in row else row.split()
        if len(fields) < 2:
            continue
        pairs[Path(fields[0]).name] = fields[1].strip().upper()
    return pairs


def score(out: Path, claims: Path) -> int:
    truth = read_pairs(out / ANSWERS)
    claimed = read_pairs(claims)
    wrong, missing = [], []
    for name, code in truth.items():
        if name not in claimed:
            missing.append(name)
        elif claimed[name] != code:
            wrong.append((name, claimed[name], code))
    for name, got, want in wrong:
        print(f"{name} MISPAIRED claimed {got} pixels {want}")
    for name in missing:
        print(f"{name} UNREAD no claim")
    ok = len(truth) - len(wrong) - len(missing)
    extra = set(claimed) - set(truth)
    if extra:
        print(f"# {len(extra)} claims name no such frame: {sorted(extra)[:3]}")
    print(f"# {ok}/{len(truth)} correct, {len(wrong)} mispaired, "
          f"{len(missing)} unread", file=sys.stderr)
    return 1 if (wrong or missing or extra) else 0


def selftest() -> int:
    # This selftest asserts inline, so there is no comparator to name: the
    # harness reads its `assert` statements as the cases instead, and what
    # `done()` can still prove is that `assert` bites in this interpreter.
    from watchquality import selftest_proof
    proof = selftest_proof.begin()

    import tempfile
    # COUNTED, not typed. `print("selftest OK (5 cases)")` was a literal, and
    # a literal is the same defect the case-count floor exists to catch one
    # level down: a number that says what the selftest did without being
    # computed from what it did. It read 5 while four `assert` statements ran.
    cases = 0
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        build(d, 4, seed=7, start=60, step=30)
        truth = read_pairs(d / ANSWERS)
        assert len(truth) == 4, truth; cases += 1
        assert len(set(truth.values())) == 4, truth; cases += 1
        names = sorted(truth)
        # An exact transcription scores clean.
        good = d / "good.tsv"
        good.write_text("".join(f"{n}\t{truth[n]}\n" for n in names))
        assert score(d, good) == 0; cases += 1
        # Two labels swapped -- the exact shape of R1 -- must not.
        bad = d / "bad.tsv"
        swapped = dict(truth)
        swapped[names[0]], swapped[names[1]] = truth[names[1]], truth[names[0]]
        bad.write_text("".join(f"{n}\t{swapped[n]}\n" for n in names))
        assert score(d, bad) == 1; cases += 1
        # Determinism: same seed, same codes.
        d2 = Path(td) / "again"
        build(d2, 4, seed=7, start=60, step=30)
        assert read_pairs(d2 / ANSWERS) == truth; cases += 1
    proof.done()
    print(f"selftest OK ({cases} cases)")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="label-vs-pixels frame fixture")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--score", nargs=2, metavar=("DIR", "CLAIMS"))
    ap.add_argument("--out", type=Path)
    ap.add_argument("--count", type=int, default=12)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--start", type=int, default=90, help="first second")
    ap.add_argument("--step", type=int, default=137, help="seconds between frames")
    ap.add_argument("--style", choices=("plain", "slide"), default="plain")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.build:
        if not args.out:
            print("resolve: --build needs --out", file=sys.stderr)
            return 2
        return build(args.out, args.count, args.seed, args.start, args.step,
                     args.style)
    if args.score:
        return score(Path(args.score[0]), Path(args.score[1]))
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
