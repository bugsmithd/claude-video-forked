"""yt-dlp argv construction for download.py.

Regression guard: ``--sub-langs all`` makes yt-dlp fetch YouTube's hundreds of
auto-translated caption tracks, which can take minutes and stalls before the
video download even starts. The request must therefore stay bounded — but
bounded is not the same as English-only: asking for ``en.*`` on a Turkish video
returns a machine translation and silently discards the spoken words. So we ask
for the original track plus English as a fallback, which resolves to two tracks
(measured: ``Downloading subtitles: tr-orig, en``), not hundreds.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "watch" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import download  # noqa: E402
import notemode  # noqa: E402

URL = "https://www.youtube.com/watch?v=rlOpbu3Enkw"


def _capture_argv(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Stub subprocess.run inside download.py and record every argv."""
    calls: list[list[str]] = []

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return _Result()

    monkeypatch.setattr(download.subprocess, "run", fake_run)
    return calls


def _sub_langs(argv: list[str]) -> str:
    idx = argv.index("--sub-langs")
    return argv[idx + 1]


def _assert_bounded(langs: str) -> None:
    tokens = langs.split(",")
    assert "all" not in tokens, f"sub-langs must not request all languages, got {langs!r}"
    assert len(tokens) <= 5, f"sub-langs must stay bounded, got {langs!r}"
    assert any("-orig" in t for t in tokens), f"sub-langs must ask for the original track, got {langs!r}"
    # "en.*" also matches YouTube's ~30 auto-translated tracks (en-ar, en-zh,
    # ...), which _pick_subtitle never selects and which trigger HTTP 429 on the
    # way, so the English variants are spelled out instead of wildcarded.
    assert "en.*" not in tokens, f"sub-langs must not wildcard English, got {langs!r}"


def test_fetch_captions_requests_bounded_langs(monkeypatch, tmp_path):
    calls = _capture_argv(monkeypatch)
    download.fetch_captions(URL, tmp_path / "download")
    _assert_bounded(_sub_langs(calls[0]))


def test_download_url_requests_bounded_langs(monkeypatch, tmp_path):
    calls = _capture_argv(monkeypatch)
    # _pick_video returns None with no real file, which raises SystemExit after
    # the yt-dlp argv is already built — that's all we need to inspect.
    with pytest.raises(SystemExit):
        download.download_url(URL, tmp_path / "download")
    _assert_bounded(_sub_langs(calls[0]))


def test_pick_subtitle_prefers_original_over_translation(tmp_path):
    for name in ("video.en.vtt", "video.tr-orig.vtt"):
        (tmp_path / name).write_text("WEBVTT\n", encoding="utf-8")
    assert download._pick_subtitle(tmp_path).name == "video.tr-orig.vtt"


# --- what the projection is allowed to drop ----------------------------------
# `_read_info` narrows yt-dlp's info.json down to the handful of fields the
# report and the run record read. Narrowing is right, and it is also silent: a
# key dropped here fails somewhere that never mentions download.py. `id` was
# dropped, so `--make-note` on a YouTube link filed its run under
# `local-<digest>` and reported no error at all. `video_id_of`'s own unit test
# passed the whole time, because it was handed a dict this function never
# produces. These go through the real json instead.

def _info_json(tmp_path: Path, **fields) -> Path:
    path = tmp_path / "video.info.json"
    path.write_text(json.dumps(fields), encoding="utf-8")
    return path


def test_the_platform_id_survives_the_projection(tmp_path: Path):
    info = download._read_info(
        _info_json(tmp_path, id="dQw4w9WgXcQ", display_id="dQw4w9WgXcQ",
                   title="A Tutorial", uploader="Someone", duration=562.9,
                   webpage_url=URL),
        URL)
    assert info["id"] == "dQw4w9WgXcQ"


def test_a_url_run_is_named_after_the_video_not_a_digest(tmp_path: Path):
    """The composition, which is where the defect lived: each half was right
    and the pair was not."""
    info = download._read_info(
        _info_json(tmp_path, id="dQw4w9WgXcQ", title="A Tutorial",
                   webpage_url=URL),
        URL)
    assert notemode.video_id_of(URL, info) == "dQw4w9WgXcQ"


def test_display_id_alone_still_names_the_run(tmp_path: Path):
    """Both keys are carried, and each has to earn its line: some extractors
    give `display_id` and no `id` at all."""
    info = download._read_info(
        _info_json(tmp_path, display_id="some-slug", title="A Tutorial"), URL)
    assert notemode.video_id_of(URL, info) == "some-slug"


def test_a_source_with_no_id_still_falls_back(tmp_path: Path):
    info = download._read_info(_info_json(tmp_path, title="A Tutorial"), URL)
    assert notemode.video_id_of(URL, info).startswith("local-")
