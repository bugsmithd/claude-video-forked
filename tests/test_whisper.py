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
    def test_the_key_selects_the_backend(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        assert whisper.load_api_key()[0] == "openrouter"

    def test_it_is_preferred_over_groq(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
        assert whisper.load_api_key()[0] == "openrouter"

    def test_a_preference_still_wins(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
        assert whisper.load_api_key("groq")[0] == "groq"

    def test_the_second_model_is_opt_in(self, monkeypatch):
        monkeypatch.delenv("WATCH_OPENROUTER_MODEL_2", raising=False)
        assert whisper.second_model("openrouter") is None
        monkeypatch.setenv("WATCH_OPENROUTER_MODEL_2", "openai/gpt-4o-transcribe")
        assert whisper.second_model("openrouter") == "openai/gpt-4o-transcribe"

    def test_no_second_model_on_a_backend_that_has_none(self):
        assert whisper.second_model("groq") is None

    def test_the_preferred_provider_is_one_of_the_documented_ones(self):
        # OpenRouter documents verbose_json as available on these three only.
        # Measured 2026-08-19: the pin has no effect on this endpoint and a
        # provider outside the list returned timestamps anyway, so this asserts
        # the preference is coherent, NOT that routing is guaranteed.
        assert whisper.OPENROUTER_TIMESTAMPED_PROVIDERS == (
            "openai", "groq", "together")
        assert whisper.OPENROUTER_PROVIDER in whisper.OPENROUTER_TIMESTAMPED_PROVIDERS

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
        assert sent["body"]["provider"]["only"] == ["groq"]
        assert sent["body"]["provider"]["allow_fallbacks"] is False
        assert sent["body"]["input_audio"]["format"] == "mp3"
        assert base64.b64decode(sent["body"]["input_audio"]["data"]) == b"\x00\x01\x02"

    def test_a_provider_outside_the_documented_list_is_warned_about(
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
        whisper._post_openrouter("sk", "m", audio, provider="deepinfra")
        assert "deepinfra" in capsys.readouterr().err


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
