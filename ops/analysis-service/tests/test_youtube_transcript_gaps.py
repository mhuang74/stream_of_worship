"""Unit tests for gap placeholder insertion in YouTube transcript LRC pipeline."""

import pytest

from sow_analysis.config import settings
from sow_analysis.workers.lrc import LRCLine
from sow_analysis.workers.youtube_transcript import (
    TranscriptCue,
    _extract_cue_timings,
    insert_gap_placeholder_lines,
)


def _fmt(t: float) -> str:
    minutes = int(t // 60)
    seconds = t % 60
    return f"{minutes:02d}:{seconds:05.2f}"


class TestInsertGapPlaceholderLines:
    """Tests for insert_gap_placeholder_lines()."""

    def test_worked_example_shi_jia_de_ai(self):
        # Worked example from 十架的愛 @ 68 BPM:
        # cue1 139.93 + 6.09s duration; next line 179.90.
        # placeholder = 139.93 + 6.09 + 4*60/68 = 149.55 ("02:29.55")
        cues = [
            TranscriptCue(start=139.93, duration=6.09),
            TranscriptCue(start=179.90, duration=13.6),
        ]
        lines = [
            LRCLine(time_seconds=139.93, text="十架的愛"),
            LRCLine(time_seconds=179.90, text="十架的愛 何等奇妙 超乎我所求所想"),
        ]
        out = insert_gap_placeholder_lines(lines, cues, 68.0)
        assert out[1].time_seconds == 139.93 + 6.09 + 4 * 60 / 68.0
        assert out[1].text == ""

    def test_no_insertion_below_threshold(self):
        # Gap 5s < 12 beats @ 68 BPM (10.59s) -> no placeholder.
        cues = [
            TranscriptCue(start=10.0, duration=4.0),
            TranscriptCue(start=19.0, duration=4.0),
        ]
        lines = [
            LRCLine(time_seconds=10.0, text="one"),
            LRCLine(time_seconds=19.0, text="two"),
        ]
        out = insert_gap_placeholder_lines(lines, cues, 68.0)
        assert len(out) == 2

    def test_gap_measured_from_line_end_not_start(self):
        # Line ends at 14.0 (start 10 + 4s duration); next line 30.0.
        # Gap = 16s > 12 beats @ 68 (10.59s); placeholder = 14 + 3.53 = 17.53.
        cues = [
            TranscriptCue(start=10.0, duration=4.0),
            TranscriptCue(start=30.0, duration=4.0),
        ]
        lines = [
            LRCLine(time_seconds=10.0, text="one"),
            LRCLine(time_seconds=30.0, text="two"),
        ]
        out = insert_gap_placeholder_lines(lines, cues, 68.0)
        assert len(out) == 3
        assert out[1].time_seconds == pytest.approx(10.0 + 4.0 + 4 * 60 / 68.0)

    def test_span_semantics_covers_merged_and_dropped_cues(self):
        # LLM merged cues at 10 and 20 into one line; cue 15 was dropped.
        # Line end must be 20 + 5 = 25, not 10's duration.
        cues = [
            TranscriptCue(start=10.0, duration=4.0),
            TranscriptCue(start=15.0, duration=4.0),
            TranscriptCue(start=20.0, duration=5.0),
            TranscriptCue(start=60.0, duration=4.0),
        ]
        lines = [
            LRCLine(time_seconds=10.0, text="merged"),
            LRCLine(time_seconds=60.0, text="next"),
        ]
        out = insert_gap_placeholder_lines(lines, cues, 68.0)
        # placeholder = 25 + 4 beats @68
        assert out[1].time_seconds == pytest.approx(25.0 + 4 * 60 / 68.0)

    def test_no_tempo_skips_insertion(self):
        cues = [
            TranscriptCue(start=10.0, duration=4.0),
            TranscriptCue(start=90.0, duration=4.0),
        ]
        lines = [
            LRCLine(time_seconds=10.0, text="one"),
            LRCLine(time_seconds=90.0, text="two"),
        ]
        assert insert_gap_placeholder_lines(lines, cues, None) == lines
        assert insert_gap_placeholder_lines(lines, cues, 0.0) == lines

    def test_no_cues_skips_insertion(self):
        lines = [
            LRCLine(time_seconds=10.0, text="one"),
            LRCLine(time_seconds=90.0, text="two"),
        ]
        assert insert_gap_placeholder_lines(lines, [], 68.0) == lines

    def test_fewer_than_two_lines_skips_insertion(self):
        lines = [LRCLine(time_seconds=10.0, text="one")]
        cues = [TranscriptCue(start=10.0, duration=4.0)]
        assert insert_gap_placeholder_lines(lines, cues, 68.0) == lines

    def test_unmatched_timestamp_skips_gap(self):
        # LLM emitted a timestamp that matches no cue -> no insertion for it.
        cues = [
            TranscriptCue(start=10.0, duration=4.0),
            TranscriptCue(start=90.0, duration=4.0),
        ]
        lines = [
            LRCLine(time_seconds=10.0, text="one"),
            LRCLine(time_seconds=40.0, text="unmatched"),
            LRCLine(time_seconds=90.0, text="two"),
        ]
        out = insert_gap_placeholder_lines(lines, cues, 68.0)
        # Gap 1 (10→40, line end 14) inserts at 17.53; the 40 line matches no
        # cue so its gap to 90 is skipped; 40→90 gap belongs to the unmatched
        # line, not line 10.
        assert len(out) == 4
        assert out[1].text == ""
        assert out[2].text == "unmatched"

    def test_placeholder_lands_between_lines(self):
        # placeholder = line_end + 4 beats stays strictly between the lines.
        bpm = 60.0
        # line end 18.0, next 40.0 -> gap 22s > 12s threshold.
        # placeholder = 18 + 4 = 22.0 < 40.
        cues = [
            TranscriptCue(start=10.0, duration=8.0),
            TranscriptCue(start=40.0, duration=4.0),
        ]
        lines = [
            LRCLine(time_seconds=10.0, text="one"),
            LRCLine(time_seconds=40.0, text="two"),
        ]
        out = insert_gap_placeholder_lines(lines, cues, bpm)
        assert out[1].time_seconds == pytest.approx(22.0)

    def test_zero_threshold_disables_insertion(self):
        cues = [
            TranscriptCue(start=10.0, duration=4.0),
            TranscriptCue(start=90.0, duration=4.0),
        ]
        lines = [
            LRCLine(time_seconds=10.0, text="one"),
            LRCLine(time_seconds=90.0, text="two"),
        ]
        original = settings.SOW_LRC_GAP_THRESHOLD_BEATS
        try:
            settings.SOW_LRC_GAP_THRESHOLD_BEATS = 0.0
            out = insert_gap_placeholder_lines(lines, cues, 68.0)
            assert out == lines
        finally:
            settings.SOW_LRC_GAP_THRESHOLD_BEATS = original

    def test_interior_gaps_only(self):
        # Intro (before first line) and outro (after last) get no placeholders;
        # only the interior gap between the two lines does.
        cues = [
            TranscriptCue(start=100.0, duration=4.0),
            TranscriptCue(start=200.0, duration=4.0),
        ]
        lines = [
            LRCLine(time_seconds=100.0, text="one"),
            LRCLine(time_seconds=200.0, text="two"),
        ]
        out = insert_gap_placeholder_lines(lines, cues, 68.0)
        assert len(out) == 3
        assert out[0].time_seconds == 100.0
        assert out[2].time_seconds == 200.0
        assert out[1].time_seconds == pytest.approx(104.0 + 4 * 60 / 68.0)

    def test_multiple_gaps_each_get_one_placeholder(self):
        cues = [
            TranscriptCue(start=10.0, duration=4.0),
            TranscriptCue(start=90.0, duration=4.0),
            TranscriptCue(start=170.0, duration=4.0),
        ]
        lines = [
            LRCLine(time_seconds=10.0, text="one"),
            LRCLine(time_seconds=90.0, text="two"),
            LRCLine(time_seconds=170.0, text="three"),
        ]
        out = insert_gap_placeholder_lines(lines, cues, 60.0)
        assert len(out) == 5
        assert out[1].text == "" and out[3].text == ""
        assert out[1].time_seconds == 18.0  # 14 + 4 beats @60
        assert out[3].time_seconds == 98.0  # 94 + 4 beats @60


class TestExtractCueTimings:
    """Tests for _extract_cue_timings()."""

    def test_extracts_from_snippet_objects(self):
        class Snip:
            def __init__(self, start, duration, text):
                self.start = start
                self.duration = duration
                self.text = text

        class Transcript:
            snippets = [Snip(3.44, 8.76, "a"), Snip(13.96, 13.68, "b")]

        cues = _extract_cue_timings(Transcript())
        assert [(c.start, c.duration) for c in cues] == [(3.44, 8.76), (13.96, 13.68)]

    def test_clamps_negative_duration(self):
        class Snip:
            start = 5.0
            duration = -2.0

        class Transcript:
            snippets = [Snip()]

        cues = _extract_cue_timings(Transcript())
        assert cues[0].end == 5.0

    def test_cue_end_property(self):
        cue = TranscriptCue(start=139.93, duration=6.09)
        assert cue.end == 146.02
