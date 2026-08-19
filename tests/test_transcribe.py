"""Transcript parsing and formatting.

Merged from upstream PRs #88 (hour-mark stamps), #151 (rolling-caption
dedup at parse time) and #124 (HTML entities and whitespace).
"""
from __future__ import annotations

from pathlib import Path

import transcribe


# --- from upstream PR #88 --------------------------------------------

def test_format_transcript_under_an_hour_uses_mm_ss():
    segs = [{"start": 5.0, "end": 7.0, "text": "hi"}, {"start": 65.0, "end": 68.0, "text": "later"}]
    assert transcribe.format_transcript(segs) == "[00:05] hi\n[01:05] later"


def test_format_transcript_rolls_over_to_hours():
    """Past an hour the stamp must carry an hours field, not overflow minutes."""
    segs = [{"start": 3661.0, "end": 3665.0, "text": "past one hour"}]
    assert transcribe.format_transcript(segs) == "[1:01:01] past one hour"


def test_format_stamp_matches_frame_marker_shape():
    """A transcript stamp lines up with the frames.format_time `t=` marker so the
    two evidence streams reference the same clock on long videos."""
    from frames import format_time

    for seconds in (0.0, 59.0, 60.0, 3599.0, 3600.0, 7325.0):
        assert transcribe._format_stamp(seconds) == f"[{format_time(int(seconds))}]"


# --- from upstream PR #151 -------------------------------------------

YT_ROLLOVER = """WEBVTT
Kind: captions
Language: en

00:00:00.240 --> 00:00:01.910 align:start position:0%

There<00:00:00.480><c> are</c><00:00:00.640><c> people</c>

00:00:01.910 --> 00:00:01.920 align:start position:0%
There are people


00:00:01.920 --> 00:00:04.309 align:start position:0%
There are people
agent<00:00:02.399><c> workforces</c>

00:00:04.309 --> 00:00:04.319 align:start position:0%
agent workforces


00:00:04.319 --> 00:00:06.550 align:start position:0%
agent workforces
and<00:00:04.560><c> sub</c><00:00:04.799><c> agents</c>

00:00:06.550 --> 00:00:06.560 align:start position:0%
and sub agents

"""


def test_youtube_rollover_lands_each_line_once(tmp_path: Path):
    p = tmp_path / "video.en.vtt"
    p.write_text(YT_ROLLOVER, encoding="utf-8")
    segs = transcribe.parse_vtt(str(p))
    texts = [s["text"] for s in segs]
    assert texts == ["There are people", "agent workforces", "and sub agents"]
    # every word appears exactly once across the transcript
    joined = " ".join(texts).split()
    assert len(joined) == len(set(joined))


def test_strip_rollover_partial_and_none():
    assert transcribe._strip_rollover(["a", "b"], ["b", "c"]) == ["c"]
    assert transcribe._strip_rollover(["a", "b"], ["a", "b", "c"]) == ["c"]
    assert transcribe._strip_rollover(["a", "b"], ["x", "y"]) == ["x", "y"]
    assert transcribe._strip_rollover([], ["x"]) == ["x"]


# --- from upstream PR #124 -------------------------------------------

def _write(tmp_path: Path, body: str) -> str:
    path = tmp_path / "subs.vtt"
    path.write_text("WEBVTT\nKind: captions\nLanguage: en\n\n" + body, encoding="utf-8")
    return str(path)


# --- _clean ------------------------------------------------------------------

def test_clean_strips_cue_tags():
    assert transcribe._clean("<c.colorE5E5E5>hello</c> world") == "hello world"


def test_clean_decodes_html_entities():
    assert transcribe._clean("dogmas &amp; sacred texts") == "dogmas & sacred texts"
    assert transcribe._clean("5 &lt; 6") == "5 < 6"


def test_clean_folds_nbsp_into_a_single_space():
    # YouTube pads wrapped caption lines with &nbsp;, which must not survive
    # into the transcript as a literal entity or a U+00A0.
    cleaned = transcribe._clean("is not&nbsp; a religion in the conventional&nbsp;&nbsp;")
    assert cleaned == "is not a religion in the conventional"
    assert "&nbsp;" not in cleaned
    assert " " not in cleaned


def test_clean_drops_zero_width_spaces():
    assert transcribe._clean("a​b") == "ab"


def test_clean_collapses_runs_of_whitespace():
    assert transcribe._clean("a   \t b\n") == "a b"


# --- deictic_cues ------------------------------------------------------------

class TestDeicticCues:
    """Uniform sampling is blind to where the information is; the speaker says."""

    def _seg(self, start, text):
        return {"start": start, "end": start + 2.0, "text": text}

    def test_it_finds_the_moments_the_speaker_points_at(self):
        segments = [
            self._seg(0.0, "Welcome everyone, thanks for coming along today."),
            self._seg(30.0, "So if you look at this chart, the trend is obvious."),
            self._seg(60.0, "As you can see, the number doubles every quarter."),
            self._seg(90.0, "Anyway, that is roughly the state of the market."),
            self._seg(120.0, "Watch what happens when I click run."),
        ]
        assert transcribe.deictic_cues(segments) == [30.0, 60.0, 120.0]

    def test_cues_closer_than_the_gap_collapse_to_the_first(self):
        segments = [
            self._seg(30.0, "Look at this."),
            self._seg(33.0, "And notice this bit here too."),
            self._seg(95.0, "Now look at this other one."),
        ]
        assert transcribe.deictic_cues(segments, min_gap=20.0) == [30.0, 95.0]

    def test_it_is_capped(self):
        many = [self._seg(float(i * 60), "look at this") for i in range(40)]
        assert len(transcribe.deictic_cues(many, limit=8)) == 8

    def test_it_says_nothing_when_nobody_points(self):
        assert transcribe.deictic_cues([self._seg(0.0, "hello there")]) == []

    def test_the_phrasings_people_actually_use(self):
        """Taken verbatim from a 9-minute UI walkthrough the first pattern missed."""
        spoken = [
            "Let's take a look at my inbox. Now, this is my inbox.",
            "On this message, I see the AI has already composed an auto-draft.",
            "Let's look at another way to reply and schedule a meeting.",
            "we see that booking page has been saved.",
        ]
        segments = [self._seg(i * 60.0, text) for i, text in enumerate(spoken)]
        assert transcribe.deictic_cues(segments) == [0.0, 60.0, 120.0, 180.0]

    def test_it_does_not_fire_on_the_rhetorical_look(self):
        segments = [
            self._seg(0.0, "Look, the point is that nobody reads the manual."),
            self._seg(60.0, "I see what you mean, and I agree with him."),
        ]
        assert transcribe.deictic_cues(segments) == []


# --- parse_vtt ---------------------------------------------------------------

def test_parse_vtt_yields_clean_timestamped_segments(tmp_path):
    path = _write(
        tmp_path,
        "00:00:09.000 --> 00:00:12.000\n"
        "The religion of the Buddha is not&nbsp; a religion&nbsp;&nbsp;\n"
        "\n"
        "00:00:12.000 --> 00:00:18.000\n"
        "<c>because it lacks dogmas &amp; sacred texts.</c>&nbsp;\n",
    )
    segments = transcribe.parse_vtt(path)

    assert [s["text"] for s in segments] == [
        "The religion of the Buddha is not a religion",
        "because it lacks dogmas & sacred texts.",
    ]
    assert segments[0]["start"] == 9.0
    assert segments[0]["end"] == 12.0


def test_parse_vtt_joins_wrapped_lines_without_double_spaces(tmp_path):
    path = _write(
        tmp_path,
        "00:00:00.000 --> 00:00:04.000\n"
        "first line&nbsp;\n"
        "&nbsp;second line\n",
    )
    assert transcribe.parse_vtt(path)[0]["text"] == "first line second line"


def test_parse_vtt_skips_cue_lines_that_are_only_entities(tmp_path):
    path = _write(
        tmp_path,
        "00:00:00.000 --> 00:00:04.000\n"
        "&nbsp;\n"
        "real text\n",
    )
    assert transcribe.parse_vtt(path)[0]["text"] == "real text"


def test_parse_vtt_dedupes_rolling_duplicates(tmp_path):
    path = _write(
        tmp_path,
        "00:00:00.000 --> 00:00:02.000\nhello\n"
        "\n"
        "00:00:02.000 --> 00:00:04.000\nhello\n",
    )
    segments = transcribe.parse_vtt(path)
    assert len(segments) == 1
    assert segments[0]["end"] == 4.0


def test_parse_vtt_merges_rolling_prefix_growth(tmp_path):
    path = _write(
        tmp_path,
        "00:00:00.000 --> 00:00:02.000\nhello\n"
        "\n"
        "00:00:02.000 --> 00:00:04.000\nhello world\n",
    )
    segments = transcribe.parse_vtt(path)
    assert len(segments) == 1
    assert segments[0]["text"] == "hello world"


def test_parse_vtt_dedupe_matches_across_nbsp_padding(tmp_path):
    # Same line, one padded with &nbsp;: normalization must let dedup catch it.
    path = _write(
        tmp_path,
        "00:00:00.000 --> 00:00:02.000\nhello world\n"
        "\n"
        "00:00:02.000 --> 00:00:04.000\nhello&nbsp; world\n",
    )
    assert len(transcribe.parse_vtt(path)) == 1
