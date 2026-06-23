"""Unit tests for map_segments_to_lines() function.

Migrated from services/qwen3/tests/test_map_segments_to_lines.py.
Tests character-level alignment segment mapping to original lyric lines,
especially focusing on repeated choruses common in worship songs.
"""

import pytest

from sow_analysis.workers.forced_alignment import map_segments_to_lines, normalize_text


class TestNormalizeText:
    """Tests for normalize_text() helper function."""

    def test_removes_spaces(self):
        result = normalize_text("ABC DEF")
        assert result == "ABCDEF"

    def test_removes_leading_trailing_spaces(self):
        result = normalize_text("  ABC  ")
        assert result == "ABC"

    def test_removes_multiple_spaces(self):
        result = normalize_text("A  B   C")
        assert result == "ABC"

    def test_removes_chinese_period_comma(self):
        result = normalize_text("ABC。DEF，GHI")
        assert result == "ABCDEFGHI"

    def test_removes_chinese_exclamation_question(self):
        result = normalize_text("ABC！DEF？GHI")
        assert result == "ABCDEFGHI"

    def test_removes_chinese_punctuation(self):
        result = normalize_text("ABC、DEF；GHI：JKL""MNO")
        assert result == "ABCDEFGHIJKLMNO"

    def test_removes_chinese_quotes(self):
        result = normalize_text("'ABC''DEF'""GHI'jkl'""mno")
        assert result == "ABCDEFGHIjklmno"

    def test_removes_chinese_parentheses(self):
        result = normalize_text("（ABC）【DEF】「GHI」『JKL』")
        assert result == "ABCDEFGHIJKL"

    def test_empty_string(self):
        result = normalize_text("")
        assert result == ""

    def test_only_punctuation(self):
        result = normalize_text(" 。，！？")
        assert result == ""

    def test_mixed_content(self):
        result = normalize_text(" 我们 仰望你，你圣洁 的光")
        assert result == "我们仰望你你圣洁的光"


class TestSimpleMapping:
    """Tests for basic mapping scenarios."""

    def test_simple_mapping(self):
        segments = [(0.0, 2.0, "ABC"), (2.0, 4.0, "DEF")]
        original_lines = ["ABC", "DEF"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 2
        assert result[0] == (0.0, 2.0, "ABC")
        assert result[1] == (2.0, 4.0, "DEF")

    def test_simple_mapping_with_spaces(self):
        segments = [(0.0, 2.0, "A B C"), (2.0, 4.0, "D E F")]
        original_lines = ["A B C", "D E F"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 2
        assert result[0][2] == "A B C"
        assert result[1][2] == "D E F"

    def test_three_segment_mapping(self):
        segments = [(0.0, 1.5, "ABC"), (1.5, 3.0, "DEF"), (3.0, 4.5, "GHI")]
        original_lines = ["ABC", "DEF", "GHI"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 3
        assert result[0] == (0.0, 1.5, "ABC")
        assert result[1] == (1.5, 3.0, "DEF")
        assert result[2] == (3.0, 4.5, "GHI")


class TestRepeatedChorus:
    """Tests for repeated chorus scenarios (critical for worship songs)."""

    def test_repeated_chorus_same_text_different_times(self):
        segments = [
            (0.0, 2.0, "Chorus"),
            (2.0, 4.0, "Chorus"),
            (4.0, 6.0, "Chorus"),
        ]
        original_lines = ["Chorus", "Chorus", "Chorus"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 3
        assert result[0][2] == "Chorus"
        assert result[1][2] == "Chorus"
        assert result[2][2] == "Chorus"
        assert result[0][0] <= result[1][0] <= result[2][0]
        assert result[0][1] <= result[1][1] <= result[2][1]

    def test_repeated_chorus_character_overlap(self):
        segments = [
            (0.0, 2.0, "我爱"),
            (2.0, 4.0, "你"),
            (4.0, 6.0, "我爱"),
            (6.0, 8.0, "你"),
        ]
        original_lines = ["我爱你", "我爱你"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 2
        assert result[0][2] == "我爱你"
        assert result[1][2] == "我爱你"
        assert result[0][0] < result[1][0]
        assert result[0][1] <= result[1][0] or result[0][1] <= result[1][1]

    def test_chorus_repeated_with_verse(self):
        segments = [
            (0.0, 2.0, "Chorus"),
            (2.0, 4.0, "Verse"),
            (4.0, 6.0, "Chorus"),
        ]
        original_lines = ["Chorus", "Verse", "Chorus"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 3
        assert result[0][2] == "Chorus"
        assert result[1][2] == "Verse"
        assert result[2][2] == "Chorus"
        assert result[0][0] < result[2][0]


class TestEmptyLines:
    """Tests for handling empty lines in original lyrics."""

    def test_empty_lines_use_previous_time(self):
        segments = [(0.0, 2.0, "ABC"), (2.0, 4.0, "DEF")]
        original_lines = ["", "ABC", "", "DEF", ""]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 5
        assert result[0] == (0.0, 0.0, "")
        assert result[1] == (0.0, 2.0, "ABC")
        assert result[2] == (2.0, 2.0, "")
        assert result[3] == (2.0, 4.0, "DEF")
        assert result[4] == (4.0, 4.0, "")

    def test_all_empty_lines(self):
        segments = [(0.0, 2.0, "ABC")]
        original_lines = ["", "", ""]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 3
        for line in result:
            assert line[2] == ""
            assert line[0] == line[1]

    def test_leading_empty_lines(self):
        segments = [(0.0, 2.0, "ABC"), (2.0, 4.0, "DEF")]
        original_lines = ["", "", "ABC", "DEF"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 4
        assert result[0] == (0.0, 0.0, "")
        assert result[1] == (0.0, 0.0, "")
        assert result[2] == (0.0, 2.0, "ABC")
        assert result[3] == (2.0, 4.0, "DEF")

    def test_trailing_empty_lines(self):
        segments = [(0.0, 2.0, "ABC"), (2.0, 4.0, "DEF")]
        original_lines = ["ABC", "DEF", "", ""]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 4
        assert result[0] == (0.0, 2.0, "ABC")
        assert result[1] == (2.0, 4.0, "DEF")
        assert result[2] == (4.0, 4.0, "")
        assert result[3] == (4.0, 4.0, "")


class TestLineNotFound:
    """Tests for handling lines not found in aligned text."""

    def test_line_not_found_interpolation(self):
        segments = [(0.0, 2.0, "ABCDEF")]
        original_lines = ["XYZ"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 1
        assert result[0][2] == "XYZ"
        assert result[0][0] == 0.0
        assert result[0][1] == 0.0

    def test_multiple_lines_not_found(self):
        segments = [(0.0, 2.0, "ABC")]
        original_lines = ["XYZ", "MNO", "PQR"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 3
        for i, line in enumerate(result):
            assert line[2] == original_lines[i]

    def test_some_lines_found_some_not(self):
        segments = [(0.0, 1.0, "ABC"), (1.0, 2.0, "DEF")]
        original_lines = ["ABC", "XYZ", "DEF"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 3
        assert result[0][2] == "ABC"
        assert result[1][2] == "XYZ"
        assert result[2][2] == "DEF"
        assert result[0][0] == 0.0
        assert result[0][1] == 1.0
        assert result[2][0] == 1.0
        assert result[2][1] == 2.0

    def test_line_not_found_past_end(self):
        segments = [(0.0, 2.0, "ABC")]
        original_lines = ["ABC", "XYZ"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 2
        assert result[0][2] == "ABC"
        assert result[1][2] == "XYZ"
        assert result[1][0] == 2.0
        assert result[1][1] == 2.0


class TestNoOverlappingSegments:
    """Tests for cases where no segments overlap with line."""

    def test_no_overlap_interpolation(self):
        segments = [(1.0, 2.0, "ABC")]
        original_lines = ["XYZ"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 1
        assert result[0][2] == "XYZ"
        assert isinstance(result[0][0], float)


class TestEmptyInput:
    """Tests for empty input scenarios."""

    def test_empty_segments(self):
        segments = []
        original_lines = ["ABC", "DEF"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 2
        assert result[0][2] == "ABC"
        assert result[1][2] == "DEF"

    def test_empty_lines_input(self):
        segments = [(0.0, 2.0, "ABC")]
        original_lines = []

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 0

    def test_both_empty(self):
        segments = []
        original_lines = []

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 0


class TestChineseWorshipLyrics:
    """Integration tests with realistic Chinese worship song scenarios."""

    def test_realistic_chinese_lyrics(self):
        segments = [
            (0.0, 1.0, "我仰望你"),
            (1.0, 2.0, "你圣洁的光"),
            (2.0, 3.0, "在黑暗中照耀"),
            (3.0, 4.0, "我仰望你"),
            (4.0, 5.0, "你圣洁的光"),
        ]
        original_lines = [
            "我仰望你",
            "你圣洁的光",
            "在黑暗中照耀",
            "我仰望你",
            "你圣洁的光",
        ]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 5
        for i, line in enumerate(result):
            assert line[2] == original_lines[i]
        for i in range(1, len(result)):
            assert result[i][0] >= result[i - 1][0], f"Line {i} timestamp out of order"

    def test_chinese_with_punctuation(self):
        segments = [
            (0.0, 1.0, "我仰望你"),
            (1.0, 2.0, "我仰望你"),
        ]
        original_lines = [
            "我仰望你。",
            "我仰望你，",
        ]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 2

    def test_complex_repetition_pattern(self):
        segments = [
            (0.0, 1.5, "你是我力量"),
            (1.5, 3.0, "你是我盾牌"),
            (3.0, 5.0, "我全心依靠你"),
            (5.0, 6.5, "你是我力量"),
            (6.5, 8.0, "你是我盾牌"),
            (8.0, 10.0, "我全心依靠你"),
        ]
        original_lines = [
            "你是我力量",
            "你是我盾牌",
            "我全心依靠你",
            "你是我力量",
            "你是我盾牌",
            "我全心依靠你",
        ]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 6
        for i in range(1, 6):
            assert result[i][0] >= result[i - 1][0], f"Line {i + 1} start before line {i}"
        assert result[0][0] == 0.0
        assert result[0][1] <= 2.0
        assert result[3][0] >= 4.0


class TestSegmentTimingPrecision:
    """Tests for handling segment time precision."""

    def test_fractional_timestamps(self):
        segments = [(0.15, 2.35, "ABC"), (2.35, 4.75, "DEF")]
        original_lines = ["ABC", "DEF"]

        result = map_segments_to_lines(segments, original_lines)

        assert result[0][0] == 0.15
        assert result[0][1] == 2.35
        assert result[1][0] == 2.35
        assert result[1][1] == 4.75

    def test_very_short_durations(self):
        segments = [(0.0, 0.1, "A"), (0.1, 0.2, "B"), (0.2, 0.3, "C")]
        original_lines = ["A", "B", "C"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 3
        for i, line in enumerate(result):
            assert line[0] == segments[i][0]
            assert line[1] == segments[i][1]
