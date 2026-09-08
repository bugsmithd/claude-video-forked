"""Whisper auto-chunking: plan, split, and timestamp stitching."""
from __future__ import annotations

import base64
import json
import math
import subprocess
from pathlib import Path

import pytest

import whisper


MB = 1024 * 1024


class TestPlanChunks:
    def test_under_limit_is_single_chunk(self):
        plan = whisper.plan_chunks(total_seconds=600.0, total_bytes=5 * MB, max_bytes=24 * MB)
        assert plan == [(0.0, 600.0)]

    def test_at_limit_is_single_chunk(self):
        plan = whisper.plan_chunks(total_seconds=600.0, total_bytes=24 * MB, max_bytes=24 * MB)
        assert plan == [(0.0, 600.0)]

    def test_over_limit_splits_into_enough_chunks(self):
        # 71 MB against a 24 MB cap → ceil(71/24) = 3 chunks.
        plan = whisper.plan_chunks(total_seconds=3600.0, total_bytes=71 * MB, max_bytes=24 * MB)
        assert len(plan) == 3

    def test_chunks_are_contiguous_and_cover_full_duration(self):
        # 7742s over 71 MB: a duration that does not divide evenly, which is
        # where independently rounded offsets and durations used to leave a
        # millisecond hole at every seam.
        total = 7742.0
        plan = whisper.plan_chunks(total_seconds=total, total_bytes=71 * MB, max_bytes=24 * MB)
        # Offsets start at 0 and each picks up where the previous ended.
        assert plan[0][0] == 0.0
        for (off, dur), (next_off, _) in zip(plan, plan[1:]):
            assert math.isclose(off + dur, next_off)
        last_off, last_dur = plan[-1]
        assert math.isclose(last_off + last_dur, total)

    def test_each_chunk_estimated_under_limit(self):
        total_seconds, total_bytes, cap = 3600.0, 71 * MB, 24 * MB
        plan = whisper.plan_chunks(total_seconds, total_bytes, cap)
        bytes_per_second = total_bytes / total_seconds
        for _off, dur in plan:
            assert dur * bytes_per_second <= cap

    def test_zero_duration_is_single_chunk(self):
        plan = whisper.plan_chunks(total_seconds=0.0, total_bytes=0, max_bytes=24 * MB)
        assert plan == [(0.0, 0.0)]


class TestShiftSegments:
    def test_adds_offset_to_start_and_end(self):
        segs = [{"start": 0.0, "end": 2.5, "text": "hi"}, {"start": 2.5, "end": 4.0, "text": "there"}]
        shifted = whisper.shift_segments(segs, 1800.0)
        assert shifted == [
            {"start": 1800.0, "end": 1802.5, "text": "hi"},
            {"start": 1802.5, "end": 1804.0, "text": "there"},
        ]

    def test_zero_offset_is_identity(self):
        segs = [{"start": 1.0, "end": 2.0, "text": "x"}]
        assert whisper.shift_segments(segs, 0.0) == segs

    def test_does_not_mutate_input(self):
        segs = [{"start": 0.0, "end": 1.0, "text": "x"}]
        whisper.shift_segments(segs, 10.0)
        assert segs[0]["start"] == 0.0


def _make_mp3(path: Path, seconds: float) -> None:
    """Synthesize a mono 16k 64k mp3 of a sine tone — mirrors extract_audio's format."""
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-t", str(seconds), "-i", "sine=frequency=440:sample_rate=16000",
            "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "64k",
            str(path),
        ],
        check=True,
    )


class TestSplitAudio:
    def test_creates_one_file_per_plan_entry(self, tmp_path: Path):
        full = tmp_path / "audio.mp3"
        _make_mp3(full, 6.0)
        plan = [(0.0, 3.0), (3.0, 3.0)]

        chunks = whisper.split_audio(full, tmp_path, plan)

        assert len(chunks) == 2
        for chunk_path, _offset in chunks:
            assert chunk_path.exists() and chunk_path.stat().st_size > 0

    def test_returns_plan_offsets(self, tmp_path: Path):
        full = tmp_path / "audio.mp3"
        _make_mp3(full, 6.0)
        plan = [(0.0, 3.0), (3.0, 3.0)]

        chunks = whisper.split_audio(full, tmp_path, plan)

        assert [offset for _path, offset in chunks] == [0.0, 3.0]

    def test_chunks_are_smaller_than_full(self, tmp_path: Path):
        full = tmp_path / "audio.mp3"
        _make_mp3(full, 6.0)
        plan = [(0.0, 3.0), (3.0, 3.0)]

        chunks = whisper.split_audio(full, tmp_path, plan)

        full_size = full.stat().st_size
        for chunk_path, _offset in chunks:
            assert chunk_path.stat().st_size < full_size


class TestAudioDuration:
    def test_reads_duration_of_synthesized_clip(self, tmp_path: Path):
        audio = tmp_path / "audio.mp3"
        _make_mp3(audio, 5.0)
        assert whisper.audio_duration(audio) == pytest.approx(5.0, abs=0.5)


class TestTranscribeChunks:
    def test_shifts_and_concatenates_each_chunk(self):
        chunks = [(Path("a.mp3"), 0.0), (Path("b.mp3"), 100.0)]

        def fake_transcribe(path: Path) -> list[dict]:
            return [{"start": 0.0, "end": 2.0, "text": path.stem}]

        out = whisper.transcribe_chunks(chunks, fake_transcribe)

        assert out == [
            {"start": 0.0, "end": 2.0, "text": "a"},
            {"start": 100.0, "end": 102.0, "text": "b"},
        ]

    def test_keeps_successful_chunks_when_one_fails(self):
        chunks = [(Path("a.mp3"), 0.0), (Path("b.mp3"), 100.0)]

        def flaky(path: Path) -> list[dict]:
            if path.stem == "b":
                raise SystemExit("chunk b failed")
            return [{"start": 1.0, "end": 2.0, "text": "a"}]

        out = whisper.transcribe_chunks(chunks, flaky)

        assert out == [{"start": 1.0, "end": 2.0, "text": "a"}]

    def test_raises_when_every_chunk_fails(self):
        chunks = [(Path("a.mp3"), 0.0), (Path("b.mp3"), 100.0)]

        def always_fail(path: Path) -> list[dict]:
            raise SystemExit("boom")

        with pytest.raises(SystemExit):
            whisper.transcribe_chunks(chunks, always_fail)


class TestPlanWindows:
    """Decode windows. A whole-file decode collapsed and took 83% of a two-hour
    recording with it, so the property under test is that no window ever hands
    the decoder more audio than the span that was measured clean."""

    def test_short_audio_is_one_window(self):
        assert whisper.plan_windows(200.0, 240.0, 30.0) == [(0.0, 200.0, 0.0, 200.0)]

    def test_zero_window_disables_windowing(self):
        assert whisper.plan_windows(7200.0, 0.0, 30.0) == [(0.0, 7200.0, 0.0, 7200.0)]

    def test_kept_spans_are_contiguous_and_cover_the_file_once(self):
        total = 7200.0
        windows = whisper.plan_windows(total, 240.0, 30.0)
        assert windows[0][2] == 0.0
        for (_o, _d, _kf, keep_to), (_o2, _d2, next_from, _kt) in zip(windows, windows[1:]):
            assert math.isclose(keep_to, next_from)
        assert math.isclose(windows[-1][3], total)

    def test_no_window_decodes_more_than_the_window_length(self):
        for _offset, duration, _keep_from, _keep_to in whisper.plan_windows(7200.0, 240.0, 30.0):
            assert duration <= 240.0

    def test_every_kept_span_is_decoded_with_context_on_both_sides(self):
        windows = whisper.plan_windows(7200.0, 240.0, 30.0)
        for offset, duration, keep_from, keep_to in windows[1:-1]:
            assert offset == pytest.approx(keep_from - 30.0)
            assert offset + duration == pytest.approx(keep_to + 30.0)

    def test_the_first_and_last_windows_do_not_run_off_the_file(self):
        windows = whisper.plan_windows(7200.0, 240.0, 30.0)
        assert windows[0][0] == 0.0
        last_offset, last_duration, _kf, _kt = windows[-1]
        assert last_offset + last_duration <= 7200.0

    def test_overlap_that_leaves_nothing_to_keep_is_refused(self):
        # Silently falling back to a whole-file decode is the failure this whole
        # mechanism exists to remove, so a bad dial stops the run.
        with pytest.raises(ValueError):
            whisper.plan_windows(7200.0, 60.0, 30.0)

    def test_a_negative_overlap_is_refused(self):
        # It used to plan happily: every window decoded LESS than the span it
        # claimed to keep, so a slice of every window's audio was never decoded
        # and nothing said so.
        with pytest.raises(ValueError):
            whisper.plan_windows(1000.0, 240.0, -30.0)

    def test_a_negative_window_is_refused_even_on_short_audio(self):
        with pytest.raises(ValueError):
            whisper.plan_windows(200.0, -240.0, 30.0)


class TestDropSeamRepeats:
    """Each window is an independent decode, so one sentence gets two
    timestamps. Both can land on the same side of a boundary, or on opposite
    sides. A strict span test loses it in one of those cases."""

    def test_a_sentence_both_windows_kept_is_kept_once(self):
        kept = [{"start": 179.996, "end": 181.0, "text": "The point, restated."}]
        incoming = [{"start": 180.0, "end": 181.2, "text": "the point restated"},
                    {"start": 200.0, "end": 202.0, "text": "something new"}]
        out = whisper.drop_seam_repeats(kept, incoming)
        assert [s["text"] for s in out] == ["something new"]

    def test_a_sentence_neither_window_would_keep_survives(self):
        # The previous window rendered it at 180.2 and dropped it (past its
        # boundary); this window rendered it at 179.0. Without the slack it is
        # lost from both, silently.
        kept = [{"start": 100.0, "end": 101.0, "text": "earlier"}]
        incoming = whisper.trim_to_keep(
            [{"start": 179.0, "end": 181.0, "text": "the sentence at the seam"}],
            180.0 - whisper.SEAM_SECONDS, 360.0, last=False)
        out = whisper.drop_seam_repeats(kept, incoming)
        assert [s["text"] for s in out] == ["the sentence at the seam"]

    def test_a_repetition_far_from_the_seam_is_not_deleted(self):
        kept = [{"start": 10.0, "end": 11.0, "text": "I think that is right"},
                {"start": 178.0, "end": 179.0, "text": "and so we carried on"}]
        incoming = [{"start": 300.0, "end": 301.0, "text": "I think that is right"}]
        assert whisper.drop_seam_repeats(kept, incoming) == incoming

    def test_the_first_window_passes_through(self):
        incoming = [{"start": 0.0, "end": 1.0, "text": "opening line"}]
        assert whisper.drop_seam_repeats([], incoming) == incoming


class TestLongestIdenticalRun:
    def test_a_clean_decode_scores_one(self):
        segs = [{"start": float(i), "end": i + 1.0, "text": f"line {i}"}
                for i in range(5)]
        assert whisper.longest_identical_run(segs) == 1

    def test_punctuation_and_case_do_not_hide_a_loop(self):
        segs = [{"start": 0.0, "end": 1.0, "text": "The same line."},
                {"start": 1.0, "end": 2.0, "text": "the same line"},
                {"start": 2.0, "end": 3.0, "text": "THE SAME LINE!"}]
        assert whisper.longest_identical_run(segs) == 3

    def test_an_empty_decode_scores_zero(self):
        assert whisper.longest_identical_run([]) == 0


class TestLoopRetry:
    """A window that loops is re-decoded from a different second. The
    degeneration is a property of the decode, not of the audio."""

    def _looping(self, n: int) -> list[dict]:
        return [{"start": float(i), "end": i + 1.0, "text": "stuck"}
                for i in range(n)]

    def test_a_looping_window_is_retried_and_the_better_decode_wins(self):
        clean = [{"start": 0.0, "end": 1.0, "text": "what was actually said"}]
        calls = []

        def transcribe(path: Path) -> list[dict]:
            return self._looping(9)

        def retry(index: int) -> list[dict]:
            calls.append(index)
            return clean

        out = whisper.transcribe_chunks([(Path("w0.mp3"), 0.0)], transcribe,
                                        retry_window=retry)

        assert calls == [0]
        assert out == clean

    def test_a_worse_retry_is_discarded(self):
        first = self._looping(5)

        def transcribe(path: Path) -> list[dict]:
            return first

        out = whisper.transcribe_chunks(
            [(Path("w0.mp3"), 0.0)], transcribe,
            retry_window=lambda index: self._looping(20))

        assert len(out) == len(first)

    def test_a_clean_window_is_never_retried(self):
        calls = []

        def transcribe(path: Path) -> list[dict]:
            return [{"start": 0.0, "end": 1.0, "text": "fine"}]

        whisper.transcribe_chunks([(Path("w0.mp3"), 0.0)], transcribe,
                                  retry_window=lambda index: calls.append(index))

        assert calls == []

    def test_a_failing_retry_leaves_the_first_decode_in_place(self):
        first = self._looping(6)

        def failing_retry(index: int):
            raise SystemExit("the retry died")

        out = whisper.transcribe_chunks(
            [(Path("w0.mp3"), 0.0)], lambda path: first,
            retry_window=failing_retry)

        assert len(out) == len(first)


class TestTrimToKeep:
    def test_keeps_by_start_inside_the_span(self):
        segs = [{"start": 5.0, "end": 6.0, "text": "before"},
                {"start": 15.0, "end": 16.0, "text": "inside"},
                {"start": 25.0, "end": 26.0, "text": "after"}]
        kept = whisper.trim_to_keep(segs, 10.0, 20.0, last=False)
        assert [s["text"] for s in kept] == ["inside"]

    def test_a_segment_crossing_the_end_belongs_to_the_window_that_starts_it(self):
        segs = [{"start": 19.0, "end": 23.0, "text": "straddles"}]
        assert whisper.trim_to_keep(segs, 10.0, 20.0, last=False) == segs
        assert whisper.trim_to_keep(segs, 20.0, 30.0, last=False) == []

    def test_the_last_window_keeps_its_tail(self):
        segs = [{"start": 25.0, "end": 26.0, "text": "past the declared end"}]
        assert whisper.trim_to_keep(segs, 10.0, 20.0, last=True) == segs


class TestTranscribeChunksWithKeeps:
    def test_overlapping_windows_do_not_duplicate_a_sentence(self):
        chunks = [(Path("w0.mp3"), 0.0), (Path("w1.mp3"), 150.0)]

        def fake_transcribe(path: Path) -> list[dict]:
            if path.stem == "w0":
                return [{"start": 100.0, "end": 101.0, "text": "once"}]
            # w1 starts at 150 and re-decodes that same sentence as context.
            return [{"start": 0.0, "end": 1.0, "text": "once"},
                    {"start": 60.0, "end": 61.0, "text": "twice"}]

        out = whisper.transcribe_chunks(chunks, fake_transcribe,
                                        keeps=[(0.0, 180.0), (180.0, 360.0)])

        assert [s["text"] for s in out] == ["once", "twice"]

    def test_without_keeps_every_segment_survives(self):
        chunks = [(Path("a.mp3"), 0.0)]

        def fake_transcribe(path: Path) -> list[dict]:
            return [{"start": 0.0, "end": 1.0, "text": "kept"}]

        assert whisper.transcribe_chunks(chunks, fake_transcribe) == [
            {"start": 0.0, "end": 1.0, "text": "kept"}]


class TestPlanBySeconds:
    def test_short_audio_is_one_request(self):
        assert whisper.plan_by_seconds(300.0, 600.0) == [(0.0, 300.0)]

    def test_long_audio_splits_below_the_cap(self):
        plan = whisper.plan_by_seconds(3600.0, 600.0)
        assert len(plan) == 6
        assert all(dur <= 600.0 for _off, dur in plan)

    def test_chunks_are_contiguous_and_cover_the_duration(self):
        total = 7742.0
        plan = whisper.plan_by_seconds(total, 600.0)
        assert plan[0][0] == 0.0
        for (off, dur), (next_off, _) in zip(plan, plan[1:]):
            assert math.isclose(off + dur, next_off)
        assert math.isclose(plan[-1][0] + plan[-1][1], total)

    def test_chunks_are_even_rather_than_max_sized(self):
        # 3601s at a 600s cap is seven chunks of ~514s, not six of 600 plus
        # one of 1: a one-second tail chunk is a fragment of a sentence.
        plan = whisper.plan_by_seconds(3601.0, 600.0)
        assert min(dur for _off, dur in plan) > 500.0

    def test_a_cap_of_zero_is_refused(self):
        with pytest.raises(ValueError):
            whisper.plan_by_seconds(600.0, 0.0)


class TestOpenRouterSegments:
    """A transcript with no times must never be written, only refused."""

    def test_timestamped_segments_are_read(self):
        data = {"segments": [{"start": 1.0, "end": 2.0, "text": " hello "}]}
        assert whisper._segments_from_response(data, allow_untimed=False) == [
            {"start": 1.0, "end": 2.0, "text": "hello"}]

    def test_text_without_segments_is_refused(self):
        data = {"text": "a wall of words with no seconds in it"}
        with pytest.raises(SystemExit) as caught:
            whisper._segments_from_response(data, allow_untimed=False)
        assert "WATCH_OPENROUTER_PROVIDER" in str(caught.value)

    def test_the_old_behaviour_survives_where_it_is_allowed(self):
        data = {"text": "a wall of words"}
        assert whisper._segments_from_response(data) == [
            {"start": 0.0, "end": 0.0, "text": "a wall of words"}]

    def test_an_empty_response_is_empty_either_way(self):
        assert whisper._segments_from_response({}, allow_untimed=False) == []


class TestOpenRouterBackend:
    def test_asking_for_it_by_name_selects_it(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        assert whisper.load_api_key("openrouter")[0] == "openrouter"

    def test_the_key_alone_does_not_select_it(self, monkeypatch):
        """It is a flag, not a default.

        The endpoint ignores the provider pin and routes to whichever provider
        is cheapest that minute, so which model actually decoded a run can
        change between runs. A backend like that must be asked for, because the
        transcript is the oracle every downstream check is measured against.
        """
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("WHISPER_CPP_BIN", "/usr/bin/whisper")
        assert whisper.load_api_key()[0] == "local"

    def test_a_key_that_only_openrouter_has_leaves_no_backend(
            self, monkeypatch, tmp_path):
        """HOME is redirected because `_read_config_value` falls through to the
        dotenv, and this developer's real one has a whisper.cpp path in it —
        the test would otherwise pass on the machine rather than on the code."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        for name in ("GROQ_API_KEY", "OPENAI_API_KEY", "WHISPER_CPP_BIN"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        assert whisper.load_api_key() == (None, None)
        assert whisper.load_api_key("openrouter")[0] == "openrouter"

    def test_groq_still_beats_it_without_a_flag(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
        assert whisper.load_api_key()[0] == "groq"

    def test_a_preference_still_wins(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
        assert whisper.load_api_key("groq")[0] == "groq"

    def test_the_flag_offers_it(self):
        """A backend `load_api_key` honours and the CLI cannot name is a
        backend nobody can reach, and that was the state for months."""
        import subprocess
        import sys
        watch_py = (Path(__file__).resolve().parent.parent / "skills" / "watch"
                    / "scripts" / "watch.py")
        out = subprocess.run([sys.executable, str(watch_py), "--help"],
                             capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        assert "openrouter" in out.stdout

    def test_the_second_model_is_opt_in(self, monkeypatch):
        monkeypatch.delenv("WATCH_OPENROUTER_MODEL_2", raising=False)
        assert whisper.second_model("openrouter") is None
        monkeypatch.setenv("WATCH_OPENROUTER_MODEL_2", "openai/gpt-4o-transcribe")
        assert whisper.second_model("openrouter") == "openai/gpt-4o-transcribe"

    def test_no_second_model_on_a_backend_that_has_none(self):
        assert whisper.second_model("groq") is None


class TestDetectedLanguageIsPinned:
    """One recording is one language; `auto` let the second model pick another."""

    def test_the_first_decode_pins_the_language_for_the_rest_of_the_run(self):
        whisper.reset_detected_language()
        assert whisper.decode_language() == "auto"
        whisper.remember_detected_language("en")
        assert whisper.decode_language() == "en"
        # A later window detecting something else does not move the pin.
        whisper.remember_detected_language("cy")
        assert whisper.decode_language() == "en"

    def test_a_configured_language_always_wins(self, monkeypatch):
        whisper.reset_detected_language()
        whisper.remember_detected_language("cy")
        monkeypatch.setenv("WHISPER_CPP_LANG", "en")
        assert whisper.decode_language() == "en"

    def test_auto_is_never_remembered_as_a_language(self):
        whisper.reset_detected_language()
        whisper.remember_detected_language("auto")
        whisper.remember_detected_language("")
        assert whisper.decode_language() == "auto"


class TestBackendOverrideCarriesItsOwnKey:
    """`--backend local` with a cloud key set decoded with the cloud key."""

    def test_the_named_backend_gets_its_own_credential(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("WHISPER_CPP_BIN", "/usr/bin/whisper-cli")
        seen = {}

        monkeypatch.setattr(whisper, "extract_audio", lambda v, o: Path(o))
        monkeypatch.setattr(whisper, "audio_duration", lambda p: 10.0)

        def fake_transcribe(backend, api_key, path, model_override=None):
            seen["backend"], seen["key"] = backend, api_key
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]

        monkeypatch.setattr(whisper, "_transcribe_file", fake_transcribe)
        monkeypatch.setattr(whisper, "second_model", lambda b: None)
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"x" * 32)
        whisper.transcribe_video("clip.wav", audio, backend="local")
        assert seen["backend"] == "local"
        assert seen["key"] == "/usr/bin/whisper-cli"


class TestAlignRenderings:
    """A second decode nobody compares is a doubled bill for one witness."""

    def _renderings(self, tmp_path):
        first = tmp_path / "transcript-1.json"
        second = tmp_path / "transcript-2.json"
        for path in (first, second):
            path.write_text(json.dumps({"segments": []}), encoding="utf-8")
        return first, second

    def test_it_runs_the_aligner_and_keeps_the_report(self, tmp_path, monkeypatch):
        first, second = self._renderings(tmp_path)
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "regions\n", "# ratio 0.99\n")

        monkeypatch.setattr(whisper.shutil, "which",
                            lambda _: "/usr/bin/wq-transcript-align")
        monkeypatch.setattr(whisper.subprocess, "run", fake_run)
        report = whisper.align_renderings(first, second, 61.0)
        assert report == tmp_path / "transcript-align.txt"
        assert "ratio 0.99" in report.read_text(encoding="utf-8")
        assert str(first) in seen["cmd"] and str(second) in seen["cmd"]
        assert "--duration" in seen["cmd"]

    def test_a_missing_aligner_never_fails_the_transcription(self, tmp_path,
                                                             monkeypatch):
        first, second = self._renderings(tmp_path)
        monkeypatch.setattr(whisper.shutil, "which", lambda _: None)
        assert whisper.align_renderings(first, second, 61.0) is None

    def test_a_reported_defect_still_keeps_the_report(self, tmp_path, monkeypatch):
        first, second = self._renderings(tmp_path)

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd, 1, "E-TS-DIVERGENT\n", "# 2 defect(s)\n")

        monkeypatch.setattr(whisper.shutil, "which",
                            lambda _: "/usr/bin/wq-transcript-align")
        monkeypatch.setattr(whisper.subprocess, "run", fake_run)
        report = whisper.align_renderings(first, second, None)
        assert report is not None
        assert "E-TS-DIVERGENT" in report.read_text(encoding="utf-8")

    def test_the_pin_names_a_provider_only_the_router_can_reach(self):
        """The router exists for providers a direct backend cannot reach.

        The pin defaulted to `groq`, which is also a backend of its own
        (`--whisper groq`, `GROQ_API_KEY`), so the default spent the router's
        margin reaching a provider already reachable directly. The requirement
        is deepinfra VIA openrouter; groq direct.

        `deepinfra` is outside the three OpenRouter DOCUMENTS as returning
        `verbose_json` and inside the three this repo MEASURED returning it
        (2026-08-19, see the note above `OPENROUTER_ENDPOINT`) -- which is why
        the documented list is kept as a doc fact and is not the test.
        """
        # The doc claim, unchanged and still only a doc claim.
        assert whisper.OPENROUTER_TIMESTAMPED_PROVIDERS == (
            "openai", "groq", "together")
        # The measurement, which is what the pin is chosen from.
        assert whisper.OPENROUTER_MEASURED_TIMESTAMPED == (
            "groq", "deepinfra", "together")
        assert whisper.OPENROUTER_PROVIDER in whisper.OPENROUTER_MEASURED_TIMESTAMPED
        # And not a provider that is its own backend.
        assert whisper.OPENROUTER_PROVIDER == "deepinfra"
        assert whisper.OPENROUTER_PROVIDER not in {"groq", "openai", "local"}

    def test_the_default_pin_does_not_warn_about_itself(
            self, monkeypatch, tmp_path, capsys):
        """The warning is for a pin with no evidence, not for the default."""
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"\x00")

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{"segments": []}'

        monkeypatch.setattr(whisper, "urlopen", lambda *a, **k: FakeResponse())
        whisper._post_openrouter("sk", "m", audio)
        assert "is outside" not in capsys.readouterr().err

    def test_the_request_asks_for_timestamps_and_pins_the_provider(
            self, monkeypatch, tmp_path):
        """The request body is the whole contract with OpenRouter."""
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"\x00\x01\x02")
        sent: dict = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{"segments": [{"start": 0, "end": 1, "text": "hi"}]}'

        def fake_urlopen(request, timeout=None, context=None):
            sent["url"] = request.full_url
            sent["body"] = json.loads(request.data)
            sent["headers"] = request.headers
            return FakeResponse()

        monkeypatch.setattr(whisper, "urlopen", fake_urlopen)
        out = whisper._post_openrouter("sk-or-test", "openai/whisper-large-v3",
                                       audio)

        assert out["segments"][0]["text"] == "hi"
        assert sent["url"] == whisper.OPENROUTER_ENDPOINT
        assert sent["body"]["response_format"] == "verbose_json"
        assert sent["body"]["provider"]["only"] == ["deepinfra"]
        assert sent["body"]["provider"]["allow_fallbacks"] is False
        assert sent["body"]["input_audio"]["format"] == "mp3"
        assert base64.b64decode(sent["body"]["input_audio"]["data"]) == b"\x00\x01\x02"

    def test_a_provider_with_neither_doc_nor_measurement_is_warned_about(
            self, monkeypatch, tmp_path, capsys):
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"\x00")

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{"segments": []}'

        monkeypatch.setattr(whisper, "urlopen",
                            lambda *a, **k: FakeResponse())
        # `deepinfra` used to be the example here. It is the default now, and
        # the same file records it returning timestamps, so warning about it
        # would be warning about the measurement. A provider with neither the
        # doc nor the measurement behind it is what the line is for.
        whisper._post_openrouter("sk", "m", audio, provider="fireworks")
        assert "fireworks" in capsys.readouterr().err


def _segments(spans):
    return [{"start": a, "end": b, "text": "x"} for a, b in spans]


class TestGranularity:
    """Timestamps a run cannot point with are the second way to lose them.

    Measured 2026-08-19 through OpenRouter, one script read at two lengths:
    whisper-large-v3 gave a median segment of 2.26s and 3.06s, Qwen3-ASR-1.7B
    14.81s and 27.30s. Both carry timestamps. Only one can be anchored.
    """

    def test_a_normal_rendering_passes(self):
        # Whisper's shape: many short segments over a long file.
        spans = [(i * 5.0, i * 5.0 + 4.5) for i in range(40)]
        whisper.check_granularity(_segments(spans), "whisper", refuse=True)

    def test_a_coarse_rendering_is_refused(self):
        with pytest.raises(SystemExit) as caught:
            whisper.check_granularity(
                _segments([(0.0, 27.6), (27.6, 54.9), (54.9, 82.2),
                           (82.2, 89.46)]), "coarse/model", refuse=True)
        assert "too coarse to anchor" in str(caught.value)
        assert "coarse/model" in str(caught.value)

    def test_the_same_rendering_only_warns_on_a_second_decode(self, capsys):
        whisper.check_granularity(
            _segments([(0.0, 27.6), (27.6, 54.9), (54.9, 82.2),
                       (82.2, 89.46)]), "coarse/model", refuse=False)
        assert "coarse" in capsys.readouterr().err

    def test_a_short_clip_is_left_alone(self):
        # One segment covering all of it, under the floor: a tail chunk that
        # short costs at most half a minute, so the rule is off.
        whisper.check_granularity(_segments([(0.0, 29.6)]), "m", refuse=True)

    def test_an_empty_rendering_is_not_a_granularity_problem(self):
        whisper.check_granularity([], "m", refuse=True)

    def test_the_median_separates_the_two_at_both_lengths(self):
        """The rule this replaced did not, which is why it is the median.

        Judging the SHARE of the request covered by the longest segment
        separated these two at 29.6s and stopped separating them at 89.5s --
        31% against 31% -- because the coarse model's segments cap out near 27s
        while the request keeps growing. These are the measured shapes.
        """
        short_whisper = _segments([(0.0, 1.56), (1.89, 7.97), (8.28, 11.24),
                                   (11.52, 13.02), (13.3, 17.0), (17.2, 21.0),
                                   (21.2, 25.0), (25.2, 29.61)])
        short_qwen = _segments([(0.0, 27.6), (27.6, 29.63)])
        long_whisper = _segments([(i * 5.26, i * 5.26 + 3.06)
                                  for i in range(17)])
        long_qwen = _segments([(0.0, 27.6), (27.6, 54.9), (54.9, 82.2),
                               (82.2, 89.46)])

        for fine, coarse in ((short_whisper, short_qwen),
                             (long_whisper, long_qwen)):
            assert whisper.segment_shape(fine)[0] < whisper.COARSE_MEDIAN_SECONDS
            assert whisper.segment_shape(coarse)[0] > whisper.COARSE_MEDIAN_SECONDS

    def test_the_second_decode_is_the_one_that_may_be_coarse(self, monkeypatch,
                                                             tmp_path, capsys):
        """`model_override` set means second decode, and only it survives."""
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"\x00")
        blob = {"segments": [{"start": 0.0, "end": 600.0, "text": "everything"}]}
        monkeypatch.setattr(whisper, "_post_openrouter", lambda *a, **k: blob)
        monkeypatch.setattr(whisper, "_read_config_value", lambda name: None)

        assert whisper._transcribe_file("openrouter", "sk", audio,
                                        "Qwen/Qwen3-ASR-1.7B")
        assert "coarse" in capsys.readouterr().err
        with pytest.raises(SystemExit):
            whisper._transcribe_file("openrouter", "sk", audio)


def test_an_unknown_backend_name_names_the_mistake(monkeypatch):
    """properties I33: a typo was reported as a missing credential.

    `--backend Local` on a fully configured machine answered "No Whisper
    backend available. Set GROQ_API_KEY…", sending the user to fix credentials
    they already have. `watch.py` constrains its own flag with argparse
    choices; this module's own CLI does not, and this is the shared door.
    """
    monkeypatch.setattr(whisper, "_read_config_value", lambda name: "sk-x")
    assert whisper.load_api_key("groq") == ("groq", "sk-x")
    with pytest.raises(SystemExit) as caught:
        whisper.load_api_key("Local")
    assert "Local" in str(caught.value), caught.value


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "openrouter"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class TestWordGrouping:
    """Segments are a lottery on this route; words are not.

    Measured 2026-09-07 on one chunk of FIhj0yb9KPI: ten direct probes came
    back at 23-31 segments and a 6.76-9.02s median, and two `watch` runs over
    the same audio and the same payload came back at 11-13 segments and a 30s
    median, which `check_granularity` refuses. Every one of those responses
    carried a word array, at a 0.20s median. So the words are the rendering
    that does not depend on which provider the router picked.

    The three numbers were set by measurement, not by taste. Grouped at
    max 4.0s / gap 0.5s, the two captured fixtures come back as 80 segments
    with a median of 3.76s and 3.65s -- the shape whisper-large-v3 produces on
    its own (2.26s and 3.06s, measured 2026-08-19 beside COARSE_MEDIAN_SECONDS)
    and comfortably under the 10.0s guard. A cap of 8.0s also passes, at a
    median of 7.8s, which is 2.2s of margin under a threshold one rounding
    change away from firing.
    """

    def test_words_group_into_segments_a_run_can_anchor(self):
        """Fails if the grouping returns one blob: median would be 284s, not 3.76s."""
        words = _fixture("fine")["words"]
        segments = whisper.segments_from_words(words)
        median, longest, covered = whisper.segment_shape(segments)
        assert median < whisper.COARSE_MEDIAN_SECONDS, median
        assert longest <= whisper.WORD_SEGMENT_MAX_SECONDS + 0.01, longest
        assert 3.0 < median < 4.5, median
        assert covered > 280.0, covered

    def test_the_near_edge_response_groups_the_same_way(self):
        """Fails if the cap is applied per word instead of per group."""
        segments = whisper.segments_from_words(_fixture("near-edge")["words"])
        median, longest, _covered = whisper.segment_shape(segments)
        assert median < whisper.COARSE_MEDIAN_SECONDS, median
        assert longest <= whisper.WORD_SEGMENT_MAX_SECONDS + 0.01, longest

    def test_every_word_survives_the_grouping_in_order(self):
        """Fails if a group boundary drops the word it splits on.

        Asserting only the median passes on a grouping that silently loses
        half the words, which is the defect this fix exists to remove.
        """
        words = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4}
                 for i, w in enumerate("alpha bravo charlie delta echo "
                                       "foxtrot golf hotel india".split())]
        segments = whisper.segments_from_words(words)
        assert len(segments) > 1, segments
        joined = " ".join(s["text"] for s in segments)
        assert joined == "alpha bravo charlie delta echo foxtrot golf hotel india"

    def test_a_silence_ends_a_segment(self):
        """Fails if the gap rule is dropped: the two halves would be one segment."""
        words = ([{"word": "before", "start": 0.0, "end": 0.3}]
                 + [{"word": "after", "start": 9.0, "end": 9.3}])
        segments = whisper.segments_from_words(words)
        assert len(segments) == 2, segments
        assert segments[0] == {"start": 0.0, "end": 0.3, "text": "before"}
        assert segments[1] == {"start": 9.0, "end": 9.3, "text": "after"}

    def test_a_sentence_end_only_splits_once_the_segment_has_length(self):
        """Fails if the minimum is dropped: 'Yes.' becomes its own segment.

        A one-word segment out of an interjection is not wrong, it is noise --
        it puts a citable anchor on a word nobody would cite.
        """
        words = [{"word": "Yes.", "start": 0.0, "end": 0.3},
                 {"word": "So", "start": 0.4, "end": 0.6},
                 {"word": "anyway", "start": 0.7, "end": 1.1}]
        segments = whisper.segments_from_words(words)
        assert len(segments) == 1, segments
        assert segments[0]["text"] == "Yes. So anyway"

    def test_a_sentence_end_splits_a_segment_that_has_run_long_enough(self):
        """Fails if the sentence rule never fires: this would be one segment."""
        words = [{"word": "one", "start": 0.0, "end": 0.4},
                 {"word": "two", "start": 0.5, "end": 0.9},
                 {"word": "three.", "start": 1.0, "end": 1.4},
                 {"word": "four", "start": 1.5, "end": 1.9}]
        segments = whisper.segments_from_words(words)
        assert len(segments) == 2, segments
        assert segments[0]["text"] == "one two three."
        assert segments[1]["text"] == "four"

    def test_no_words_is_no_segments(self):
        """Fails if the function invents a 0.0-0.0 segment out of an empty list."""
        assert whisper.segments_from_words([]) == []

    def test_a_malformed_word_is_skipped_rather_than_crashing(self):
        """Fails if a missing key raises: one bad word would lose a whole chunk."""
        words = [{"word": "kept", "start": 0.0, "end": 0.4},
                 {"word": "no-times"},
                 {"word": "", "start": 1.0, "end": 1.2},
                 {"word": "also-kept", "start": 1.3, "end": 1.6}]
        segments = whisper.segments_from_words(words)
        assert " ".join(s["text"] for s in segments) == "kept also-kept"

    def test_the_request_asks_for_word_timestamps(self, monkeypatch, tmp_path):
        """Fails if the key is dropped from the payload.

        This is a CONTRACT test and it proves nothing works -- it proves the
        request asks. Measured 2026-09-07: without the key the response carries
        zero words (`tests/fixtures/openrouter/no-granularity.json`), so the
        fallback below would have nothing to rebuild from.
        """
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"\x00")
        sent = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{"segments": []}'

        def fake_urlopen(request, *a, **k):
            sent["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        monkeypatch.setattr(whisper, "urlopen", fake_urlopen)
        whisper._post_openrouter("sk", "m", audio, provider="deepinfra")
        assert sent["body"]["timestamp_granularities"] == ["segment", "word"]

    def test_a_coarse_response_is_rebuilt_from_its_words(self, capsys):
        """Fails if the words path is skipped: the chunk is refused instead.

        This is the run that lost 9:28-14:12. The segments are the shape that
        run reported; the words are the captured ones.
        """
        data = _fixture("coarse-reconstructed")
        assert whisper.segment_shape(
            whisper._segments_from_response({"segments": data["segments"]})
        )[0] > whisper.COARSE_MEDIAN_SECONDS

        segments = whisper._segments_from_response(data, allow_untimed=False)
        median, _longest, _covered = whisper.segment_shape(segments)
        assert median < whisper.COARSE_MEDIAN_SECONDS, median
        assert len(segments) > 12, len(segments)
        assert "word timestamps" in capsys.readouterr().err
        whisper.check_granularity(segments, "openai/whisper-large-v3", refuse=True)

    def test_a_response_with_words_and_no_segments_is_rebuilt(self):
        """Fails if the reader still requires a `segments` key to be present."""
        segments = whisper._segments_from_response(_fixture("words-only"),
                                                   allow_untimed=False)
        assert whisper.segment_shape(segments)[0] < whisper.COARSE_MEDIAN_SECONDS
        whisper.check_granularity(segments, "openai/whisper-large-v3", refuse=True)

    def test_a_fine_response_is_left_exactly_as_it_came(self):
        """Fails if the grouping runs unconditionally and replaces good segments.

        31 segments at a 6.76s median already anchor. Regrouping them would
        throw away the provider's own sentence boundaries for no gain.
        """
        data = _fixture("fine")
        segments = whisper._segments_from_response(data, allow_untimed=False)
        assert len(segments) == len(data["segments"])
        assert segments[0]["start"] == round(float(data["segments"][0]["start"]), 2)

    def test_a_near_edge_response_is_left_alone_too(self):
        """Fails if the guard's comparison flips to `<`.

        9.02s sits 0.98s under the 10.0s threshold. A mean-instead-of-median
        edit, or a `>=` where a `>` belongs, moves this fixture across and
        starts regrouping renderings that were fine.
        """
        data = _fixture("near-edge")
        segments = whisper._segments_from_response(data, allow_untimed=False)
        assert len(segments) == len(data["segments"])

    def test_no_segments_and_no_words_still_refuses(self):
        """Fails if the untimed refusal is lost behind the new branch."""
        with pytest.raises(SystemExit) as caught:
            whisper._segments_from_response({"text": "everything, untimed"},
                                            allow_untimed=False)
        assert "no segment timestamps" in str(caught.value)

    def test_no_segments_and_no_words_still_allows_untimed_when_asked(self):
        """Fails if the new branch changes the groq and openai paths."""
        segments = whisper._segments_from_response({"text": "untimed"})
        assert segments == [{"start": 0.0, "end": 0.0, "text": "untimed"}]


class TestDroppedChunks:
    """A skipped chunk is a hole in the evidence, and it used to be invisible.

    Run 2 of FIhj0yb9KPI on 2026-09-07 kept chunks 1, 2, 4, 5 and 6, lost
    chunk 3, and reported `Transcript: 141 segments (via whisper (openrouter))`
    with no mention that roughly 9:28-14:12 was absent. Every gate downstream
    read that transcript as whole.
    """

    def test_a_failed_chunk_records_the_span_it_lost(self, tmp_path, capsys):
        """Fails if the drop is only printed: `dropped` stays empty."""
        chunks = [(tmp_path / "c0.mp3", 0.0), (tmp_path / "c1.mp3", 568.0),
                  (tmp_path / "c2.mp3", 1136.0)]

        def transcribe_one(path):
            if path.name == "c1.mp3":
                raise SystemExit("too coarse to anchor")
            return [{"start": 0.0, "end": 2.0, "text": "kept"}]

        dropped = []
        segments = whisper.transcribe_chunks(chunks, transcribe_one,
                                             dropped=dropped)
        assert len(segments) == 2
        assert dropped == [(568.0, 1136.0, "failed")]
        assert "9:28" in capsys.readouterr().err

    def test_a_failed_last_chunk_records_an_open_span(self, tmp_path):
        """Fails if the code indexes chunks[index + 1] unguarded: IndexError."""
        chunks = [(tmp_path / "c0.mp3", 0.0), (tmp_path / "c1.mp3", 600.0)]

        def transcribe_one(path):
            if path.name == "c1.mp3":
                raise SystemExit("nope")
            return [{"start": 0.0, "end": 2.0, "text": "kept"}]

        dropped = []
        whisper.transcribe_chunks(chunks, transcribe_one, dropped=dropped)
        assert dropped == [(600.0, None, "failed")]

    def test_a_chunk_that_returns_nothing_is_recorded_as_empty(self, tmp_path):
        """Fails if only the raising path is recorded.

        The refutation measured this door: a chunk that RETURNS `[]` takes the
        success path, never increments `failures`, and used to vanish. It is
        recorded and named `empty` rather than `failed`, because silence is a
        legitimate reason a chunk carries no speech.
        """
        chunks = [(tmp_path / "c0.mp3", 0.0), (tmp_path / "c1.mp3", 568.0),
                  (tmp_path / "c2.mp3", 1136.0)]
        dropped = []
        whisper.transcribe_chunks(
            chunks,
            lambda p: [] if p.name == "c1.mp3"
            else [{"start": 0.0, "end": 2.0, "text": "kept"}],
            dropped=dropped)
        assert dropped == [(568.0, 1136.0, "empty")]

    def test_a_clean_run_records_nothing(self, tmp_path):
        """Fails if the list is appended to unconditionally."""
        chunks = [(tmp_path / "c0.mp3", 0.0)]
        dropped = []
        whisper.transcribe_chunks(
            chunks, lambda p: [{"start": 0.0, "end": 1.0, "text": "x"}],
            dropped=dropped)
        assert dropped == []

    def test_the_span_reads_as_a_clock(self):
        """Fails if the formatter prints raw seconds a reader has to convert."""
        assert whisper._format_span(568.0, 852.0) == "9:28–14:12"
        assert whisper._format_span(600.0, None) == "10:00 to the end"

    def test_omitting_the_list_keeps_the_old_signature_working(self, tmp_path):
        """Fails if `dropped` becomes required: every existing caller breaks."""
        chunks = [(tmp_path / "c0.mp3", 0.0), (tmp_path / "c1.mp3", 10.0)]

        def transcribe_one(path):
            if path.name == "c1.mp3":
                raise SystemExit("nope")
            return [{"start": 0.0, "end": 2.0, "text": "kept"}]

        assert whisper.transcribe_chunks(chunks, transcribe_one) == [
            {"start": 0.0, "end": 2.0, "text": "kept"}]


class TestGappedTranscriptIsRefused:
    def test_a_run_that_lost_a_chunk_is_refused_by_name(self, monkeypatch,
                                                        tmp_path):
        """Fails if the gap only warns: a 30-video batch skips warnings."""
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"\x00")
        monkeypatch.setattr(whisper, "extract_audio", lambda *a, **k: audio)
        monkeypatch.setattr(whisper, "audio_duration", lambda *a, **k: 1704.0)
        monkeypatch.setattr(whisper, "split_audio",
                            lambda a, d, plan: [(tmp_path / f"c{i}.mp3", off)
                                                for i, (off, _len)
                                                in enumerate(plan)])
        monkeypatch.setattr(whisper, "second_model", lambda backend: None)
        monkeypatch.setattr(whisper, "_read_config_value", lambda name: None)

        def transcribe_one(path):
            if path.name == "c1.mp3":
                raise SystemExit("too coarse to anchor")
            return [{"start": 0.0, "end": 2.0, "text": "kept"}]

        monkeypatch.setattr(whisper, "_transcribe_file",
                            lambda backend, key, path, override=None:
                            transcribe_one(path))

        with pytest.raises(SystemExit) as caught:
            whisper.transcribe_video("v.mp4", tmp_path / "audio.mp3",
                                     backend="openrouter", api_key="sk")
        message = str(caught.value)
        assert "missing from the transcript" in message
        assert "9:28" in message
        assert "WATCH_ALLOW_TRANSCRIPT_GAPS" in message

    def test_a_silent_chunk_is_named_but_does_not_refuse(self, monkeypatch,
                                                         tmp_path, capsys):
        """Fails if `empty` refuses too: a musical intro would fail the run.

        A chunk that returns no segments may be silence. Refusing on it would
        report a healthy video as a failure, which is the defect the exit-status
        task was amended for. It is named in stderr and carried, not refused.
        """
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"\x00")
        monkeypatch.setattr(whisper, "extract_audio", lambda *a, **k: audio)
        monkeypatch.setattr(whisper, "audio_duration", lambda *a, **k: 1704.0)
        monkeypatch.setattr(whisper, "split_audio",
                            lambda a, d, plan: [(tmp_path / f"c{i}.mp3", off)
                                                for i, (off, _len)
                                                in enumerate(plan)])
        monkeypatch.setattr(whisper, "second_model", lambda backend: None)
        monkeypatch.setattr(whisper, "_read_config_value", lambda name: None)
        monkeypatch.setattr(
            whisper, "_transcribe_file",
            lambda backend, key, path, override=None:
            [] if path.name == "c1.mp3"
            else [{"start": 0.0, "end": 2.0, "text": "kept"}])

        segments, _backend = whisper.transcribe_video(
            "v.mp4", tmp_path / "audio.mp3", backend="openrouter", api_key="sk")
        assert segments
        err = capsys.readouterr().err
        assert "9:28" in err
        assert "no speech" in err

    def test_the_escape_hatch_keeps_the_partial_transcript(self, monkeypatch,
                                                           tmp_path):
        """Fails if the refusal has no override: exploratory runs lose the flag."""
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"\x00")
        monkeypatch.setattr(whisper, "extract_audio", lambda *a, **k: audio)
        monkeypatch.setattr(whisper, "audio_duration", lambda *a, **k: 1704.0)
        monkeypatch.setattr(whisper, "split_audio",
                            lambda a, d, plan: [(tmp_path / f"c{i}.mp3", off)
                                                for i, (off, _len)
                                                in enumerate(plan)])
        monkeypatch.setattr(whisper, "second_model", lambda backend: None)
        monkeypatch.setattr(
            whisper, "_read_config_value",
            lambda name: "1" if name == "WATCH_ALLOW_TRANSCRIPT_GAPS" else None)
        def refuse_one(backend, key, path, override=None):
            # The SAME failure as the test above -- the flag decides what the
            # run does about it, not whether it happened.
            if path.name == "c1.mp3":
                raise SystemExit("too coarse to anchor")
            return [{"start": 0.0, "end": 2.0, "text": "kept"}]

        monkeypatch.setattr(whisper, "_transcribe_file", refuse_one)

        segments, backend = whisper.transcribe_video(
            "v.mp4", tmp_path / "audio.mp3", backend="openrouter", api_key="sk")
        assert backend == "openrouter"
        assert segments


class TestWordCoverage:
    """A rebuild that covers less audio than the rendering it replaces is a loss.

    Found by the grouping review, 2026-09-08: cutting a captured response's word
    array to the words ending by 30.0s made `_segments_from_response` return 9
    segments covering 29.65s in place of a rendering covering 284.00s, print
    "rebuilding 9 segments from 119 word timestamps", pass `check_granularity`
    at a 3.64s median, and record nothing anywhere. That is the failure this
    whole change exists to remove, moved from between chunks to inside one.
    """

    def test_a_word_array_that_stops_early_refuses_the_chunk(self):
        """Fails if the rebuild is accepted: 254s of speech vanish silently."""
        data = _fixture("coarse-reconstructed")
        short = dict(data, words=[w for w in data["words"]
                                  if float(w["end"]) <= 30.0])
        assert 100 < len(short["words"]) < len(data["words"]), len(short["words"])
        with pytest.raises(SystemExit) as caught:
            whisper._segments_from_response(short, allow_untimed=False)
        message = str(caught.value)
        assert "word timestamps cover only" in message
        assert "refused" in message

    def test_a_word_array_reaching_the_end_is_still_rebuilt(self):
        """Fails if the slack is too tight: every captured response is refused."""
        segments = whisper._segments_from_response(
            _fixture("coarse-reconstructed"), allow_untimed=False)
        assert whisper.segment_shape(segments)[0] < whisper.COARSE_MEDIAN_SECONDS

    def test_a_response_with_no_segments_has_nothing_to_fall_short_of(self):
        """Fails if the check runs when there is no served rendering to compare."""
        segments = whisper._segments_from_response(_fixture("words-only"),
                                                   allow_untimed=False)
        assert segments


class TestWindowContributesNothing:
    """What a window CONTRIBUTES, not what its decoder returned.

    Found by the failure-paths review, 2026-09-08. `whisper.py` has carried the
    comment "one that decoded 90 and kept 0 is a boundary bug" since before this
    fix; the fix printed that number and acted on the other one.
    """

    def test_a_window_that_keeps_nothing_is_recorded_as_lost(self, tmp_path):
        """Fails if the record reads chunk_segments: three minutes vanish, exit 0.

        The window decodes 90 segments, every one of them inside its leading
        context, so `trim_to_keep` keeps none.
        """
        chunks = [(tmp_path / "c0.mp3", 0.0), (tmp_path / "c1.mp3", 150.0)]
        keeps = [(0.0, 180.0), (180.0, 360.0)]

        def transcribe_one(path):
            if path.name == "c1.mp3":
                return [{"start": i * 0.3, "end": i * 0.3 + 0.2, "text": f"w{i}"}
                        for i in range(90)]
            return [{"start": 0.0, "end": 2.0, "text": "kept"}]

        dropped = []
        whisper.transcribe_chunks(chunks, transcribe_one, keeps=keeps,
                                  dropped=dropped)
        assert dropped == [(180.0, 360.0, "failed")]

    def test_a_window_that_decoded_nothing_is_still_only_silence(self, tmp_path):
        """Fails if every empty window becomes `failed`: silence refuses runs."""
        chunks = [(tmp_path / "c0.mp3", 0.0), (tmp_path / "c1.mp3", 150.0)]
        keeps = [(0.0, 180.0), (180.0, 360.0)]
        dropped = []
        whisper.transcribe_chunks(
            chunks,
            lambda p: [] if p.name == "c1.mp3"
            else [{"start": 0.0, "end": 2.0, "text": "kept"}],
            keeps=keeps, dropped=dropped)
        assert dropped == [(180.0, 360.0, "empty")]

    def test_the_recorded_span_is_the_kept_span_not_the_decode_offset(self):
        """Fails if the span reads the decode offset: it names audio that is present.

        `plan_windows` decodes from `keep_from - overlap`, so on this path the
        decode offset sits 30s before the audio the window is responsible for.
        A reader who checks the named stretch finds it in the transcript and
        concludes the warning is wrong, while the missing stretch is unnamed.
        """
        chunks = [(object(), 0.0), (object(), 150.0), (object(), 390.0)]
        keeps = [(0.0, 180.0), (180.0, 420.0), (420.0, 600.0)]
        assert whisper._chunk_span(chunks, 1, keeps) == (180.0, 420.0)
        assert whisper._chunk_span(chunks, 1, None) == (150.0, 390.0)
        assert whisper._chunk_span(chunks, 2, None) == (390.0, None)


class TestRetryMustCarrySomething:
    def test_an_empty_retry_never_replaces_a_looping_window(self, tmp_path,
                                                            capsys):
        """Fails if `alternative is not None` gates it: empty wins by construction.

        `longest_identical_run([])` is 0, which is less than any looping run, so
        an empty re-decode is always "better". A looping window is at least
        visible in the transcript; silence is not.
        """
        chunks = [(tmp_path / "c0.mp3", 0.0)]
        looping = [{"start": float(i), "end": i + 1.0, "text": "same"}
                   for i in range(whisper.LOOP_RUN + 2)]
        dropped = []
        segments = whisper.transcribe_chunks(
            chunks, lambda p: looping, retry_window=lambda index: [],
            dropped=dropped)
        assert segments == looping
        assert dropped == []
