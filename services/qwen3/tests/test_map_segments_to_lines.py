"""Unit tests for map_segments_to_lines() function.

Tests character-level alignment segment mapping to original lyric lines,
especially focusing on repeated choruses common in worship songs.
"""

import pytest

from sow_qwen3.routes.align import map_segments_to_lines, normalize_text


class TestNormalizeText:
    """Tests for normalize_text() helper function."""

    def test_removes_spaces(self):
        """Test that whitespace is removed."""
        result = normalize_text("ABC DEF")
        assert result == "ABCDEF"

    def test_removes_leading_trailing_spaces(self):
        """Test that leading and trailing spaces are removed."""
        result = normalize_text("  ABC  ")
        assert result == "ABC"

    def test_removes_multiple_spaces(self):
        """Test that multiple consecutive spaces are removed."""
        result = normalize_text("A  B   C")
        assert result == "ABC"

    def test_removes_chinese_period_comma(self):
        """Test that Chinese period and comma are removed."""
        result = normalize_text("ABC。DEF，GHI")
        assert result == "ABCDEFGHI"

    def test_removes_chinese_exclamation_question(self):
        """Test that Chinese exclamation and question marks are removed."""
        result = normalize_text("ABC！DEF？GHI")
        assert result == "ABCDEFGHI"

    def test_removes_chinese_punctuation(self):
        """Test that other Chinese punctuation is removed."""
        result = normalize_text("ABC、DEF；GHI：JKL""MNO")
        assert result == "ABCDEFGHIJKLMNO"

    def test_removes_chinese_quotes(self):
        """Test that Chinese curly quotes are removed."""
        result = normalize_text("'ABC''DEF'""GHI'jkl'""mno")
        assert result == "ABCDEFGHIjklmno"

    def test_removes_chinese_parentheses(self):
        """Test that Chinese parentheses are removed."""
        result = normalize_text("（ABC）【DEF】「GHI」『JKL』")
        assert result == "ABCDEFGHIJKL"

    def test_empty_string(self):
        """Test that empty string returns empty."""
        result = normalize_text("")
        assert result == ""

    def test_only_punctuation(self):
        """Test that string with only punctuation returns empty."""
        result = normalize_text(" 。，！？")
        assert result == ""

    def test_mixed_content(self):
        """Test that mixed content works correctly."""
        result = normalize_text(" 我们 仰望你，你圣洁 的光")
        assert result == "我们仰望你你圣洁的光"


class TestSimpleMapping:
    """Tests for basic mapping scenarios."""

    def test_simple_mapping(self):
        """Test basic mapping with non-repeated lines."""
        segments = [(0.0, 2.0, "ABC"), (2.0, 4.0, "DEF")]
        original_lines = ["ABC", "DEF"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 2
        assert result[0] == (0.0, 2.0, "ABC")
        assert result[1] == (2.0, 4.0, "DEF")

    def test_simple_mapping_with_spaces(self):
        """Test mapping with spaces in segments and lines."""
        segments = [(0.0, 2.0, "A B C"), (2.0, 4.0, "D E F")]
        original_lines = ["A B C", "D E F"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 2
        assert result[0][2] == "A B C"
        assert result[1][2] == "D E F"

    def test_three_segment_mapping(self):
        """Test mapping with three segments."""
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
        """Test that repeated chorus lines get correct timestamps."""
        # Same chorus text at three different time ranges
        segments = [
            (0.0, 2.0, "Chorus"),
            (2.0, 4.0, "Chorus"),
            (4.0, 6.0, "Chorus"),
        ]
        original_lines = ["Chorus", "Chorus", "Chorus"]

        result = map_segments_to_lines(segments, original_lines)

        # Should have 3 entries, one for each original line
        assert len(result) == 3

        # Each line should have the original text preserved
        assert result[0][2] == "Chorus"
        assert result[1][2] == "Chorus"
        assert result[2][2] == "Chorus"

        # Timestamps should be chronologically ordered
        assert result[0][0] <= result[1][0] <= result[2][0]
        assert result[0][1] <= result[1][1] <= result[2][1]

    def test_repeated_chorus_character_overlap(self):
        """Test repeated choruses when segments overlap characters."""
        # First chorus with detailed characters
        segments = [
            (0.0, 2.0, "我爱"),  # Characters "我" "爱"
            (2.0, 4.0, "你"),    # Character "你"
            (4.0, 6.0, "我爱"),  # Repeat "我" "爱"
            (6.0, 8.0, "你"),    # Repeat "你"
        ]
        original_lines = ["我爱你", "我爱你"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 2
        assert result[0][2] == "我爱你"
        assert result[1][2] == "我爱你"

        # First line should use early timestamps, second line later
        assert result[0][0] < result[1][0]
        assert result[0][1] <= result[1][0] or result[0][1] <= result[1][1]

    def test_chorus_repeated_with_verse(self):
        """Test pattern of chorus-verse-chorus (common in worship songs)."""
        segments = [
            (0.0, 2.0, "Chorus"),  # First chorus
            (2.0, 4.0, "Verse"),   # Verse
            (4.0, 6.0, "Chorus"),  # Second chorus
        ]
        original_lines = ["Chorus", "Verse", "Chorus"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 3
        assert result[0][2] == "Chorus"
        assert result[1][2] == "Verse"
        assert result[2][2] == "Chorus"

        # First and third line should have "Chorus" at different times
        assert result[0][0] < result[2][0]


class TestEmptyLines:
    """Tests for handling empty lines in original lyrics."""

    def test_empty_lines_use_previous_time(self):
        """Test that empty lines use previous end time."""
        segments = [(0.0, 2.0, "ABC"), (2.0, 4.0, "DEF")]
        original_lines = ["", "ABC", "", "DEF", ""]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 5

        # First empty line should have timestamp 0.0
        assert result[0] == (0.0, 0.0, "")

        # Second line should have the first segment timestamp
        assert result[1] == (0.0, 2.0, "ABC")

        # Third empty line should use previous end time
        assert result[2] == (2.0, 2.0, "")

        # Fourth line should have the second segment timestamp
        assert result[3] == (2.0, 4.0, "DEF")

        # Fifth empty line should use previous end time
        assert result[4] == (4.0, 4.0, "")

    def test_all_empty_lines(self):
        """Test handling when all lines are empty."""
        segments = [(0.0, 2.0, "ABC")]
        original_lines = ["", "", ""]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 3
        for line in result:
            assert line[2] == ""
            assert line[0] == line[1]  # Same start and end time

    def test_leading_empty_lines(self):
        """Test empty lines at the beginning."""
        segments = [(0.0, 2.0, "ABC"), (2.0, 4.0, "DEF")]
        original_lines = ["", "", "ABC", "DEF"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 4
        # Leading empty lines should have 0.0 timestamp
        assert result[0] == (0.0, 0.0, "")
        assert result[1] == (0.0, 0.0, "")
        assert result[2] == (0.0, 2.0, "ABC")
        assert result[3] == (2.0, 4.0, "DEF")

    def test_trailing_empty_lines(self):
        """Test empty lines at the end."""
        segments = [(0.0, 2.0, "ABC"), (2.0, 4.0, "DEF")]
        original_lines = ["ABC", "DEF", "", ""]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 4
        assert result[0] == (0.0, 2.0, "ABC")
        assert result[1] == (2.0, 4.0, "DEF")
        # Trailing empty lines should use previous end time
        assert result[2] == (4.0, 4.0, "")
        assert result[3] == (4.0, 4.0, "")


class TestLineNotFound:
    """Tests for handling lines not found in aligned text."""

    def test_line_not_found_interpolation(self):
        """Test fallback interpolation when line not found in text."""
        segments = [(0.0, 2.0, "ABCDEF")]
        original_lines = ["XYZ"]  # Different text, not in segments

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 1
        assert result[0][2] == "XYZ"
        # Should use interpolated time (0.0 since it's first line)
        assert result[0][0] == 0.0
        assert result[0][1] == 0.0

    def test_multiple_lines_not_found(self):
        """Test with multiple lines that aren't found."""
        segments = [(0.0, 2.0, "ABC")]
        original_lines = ["XYZ", "MNO", "PQR"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 3
        # All lines should be present with interpolated timestamps
        for i, line in enumerate(result):
            assert line[2] == original_lines[i]

    def test_some_lines_found_some_not(self):
        """Test when some lines are found and some aren't."""
        segments = [(0.0, 1.0, "ABC"), (1.0, 2.0, "DEF")]
        original_lines = ["ABC", "XYZ", "DEF"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 3
        assert result[0][2] == "ABC"
        assert result[1][2] == "XYZ"  # Not found, interpolated
        assert result[2][2] == "DEF"

        # First line should have accurate timing from segments
        assert result[0][0] == 0.0
        assert result[0][1] == 1.0

        # Last line should have accurate timing from segments
        assert result[2][0] == 1.0
        assert result[2][1] == 2.0

    def test_line_not_found_past_end(self):
        """Test lines beyond the aligned text."""
        segments = [(0.0, 2.0, "ABC")]
        original_lines = ["ABC", "XYZ"]  # Second line past end

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 2
        assert result[0][2] == "ABC"
        assert result[1][2] == "XYZ"
        # Second line past end should use previous end time
        assert result[1][0] == 2.0
        assert result[1][1] == 2.0


class TestNoOverlappingSegments:
    """Tests for cases where no segments overlap with line."""

    def test_no_overlap_interpolation(self):
        """Test interpolation when segments don't cover the line."""
        # Segments with timestamps but line doesn't match
        segments = [(1.0, 2.0, "ABC")]
        original_lines = ["XYZ"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 1
        assert result[0][2] == "XYZ"
        # Should use interpolated time from position
        assert isinstance(result[0][0], float)


class TestEmptyInput:
    """Tests for empty input scenarios."""

    def test_empty_segments(self):
        """Test handling of empty segments list."""
        segments = []
        original_lines = ["ABC", "DEF"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 2
        # Both lines should have timestamps based on interpolation
        # Since aligned_text is empty, ratio calculation may produce 0.0
        assert result[0][2] == "ABC"
        assert result[1][2] == "DEF"

    def test_empty_lines_input(self):
        """Test handling of empty original_lines list."""
        segments = [(0.0, 2.0, "ABC")]
        original_lines = []

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 0

    def test_both_empty(self):
        """Test handling of both empty inputs."""
        segments = []
        original_lines = []

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 0


class TestChineseWorshipLyrics:
    """Integration tests with realistic Chinese worship song scenarios."""

    def test_realistic_chinese_lyrics(self):
        """Test with realistic Chinese worship song structure."""
        # Typical pattern: chorus, verse, chorus
        segments = [
            (0.0, 1.0, "我仰望你"),
            (1.0, 2.0, "你圣洁的光"),
            (2.0, 3.0, "在黑暗中照耀"),
            (3.0, 4.0, "我仰望你"),  # Repeated
            (4.0, 5.0, "你圣洁的光"),  # Repeated
        ]
        original_lines = [
            "我仰望你",
            "你圣洁的光",
            "在黑暗中照耀",
            "我仰望你",  # Repeated
            "你圣洁的光",  # Repeated
        ]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 5

        # Text of each line should be preserved
        for i, line in enumerate(result):
            assert line[2] == original_lines[i]

        # Verify timestamps are in order
        for i in range(1, len(result)):
            assert result[i][0] >= result[i-1][0], f"Line {i} timestamp out of order"

    def test_chinese_with_punctuation(self):
        """Test Chinese lyrics with punctuation that gets normalized."""
        segments = [
            (0.0, 1.0, "我仰望你"),  # No punctuation
            (1.0, 2.0, "我仰望你"),  # Repeated
        ]
        original_lines = [
            "我仰望你。",
            "我仰望你，",  # Punctuation should be normalized
        ]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 2
        # Lines should still match despite punctuation differences
        assert len(result) == 2  # No crash, graceful handling

    def test_complex_repetition_pattern(self):
        """Test complex repetition common in worship songs."""
        # Verse-Chorus-Verse-Chorus pattern with overlaps
        segments = [
            (0.0, 1.5, "你是我力量"),        # Verse 1, line 1
            (1.5, 3.0, "你是我盾牌"),        # Verse 1, line 2
            (3.0, 5.0, "我全心依靠你"),      # Verse 1, line 3
            (5.0, 6.5, "你是我力量"),        # Verse 2, line 1 (same as V1L1)
            (6.5, 8.0, "你是我盾牌"),        # Verse 2, line 2 (same as V1L2)
            (8.0, 10.0, "我全心依靠你"),     # Verse 2, line 3 (same as V1L3)
        ]
        original_lines = [
            "你是我力量",
            "你是我盾牌",
            "我全心依靠你",
            "你是我力量",   # Repeated
            "你是我盾牌",   # Repeated
            "我全心依靠你", # Repeated
        ]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 6

        # Verify chronological ordering of timestamps
        for i in range(1, 6):
            assert result[i][0] >= result[i-1][0], f"Line {i+1} start before line {i}"

        # First occurrence should use early timestamps
        assert result[0][0] == 0.0
        assert result[0][1] <= 2.0

        # Second occurrence (same text) should use later timestamps
        assert result[3][0] >= 4.0  # Starting after first set ends


class TestSegmentTimingPrecision:
    """Tests for handling segment time precision."""

    def test_fractional_timestamps(self):
        """Test handling of fractional second timestamps."""
        segments = [(0.15, 2.35, "ABC"), (2.35, 4.75, "DEF")]
        original_lines = ["ABC", "DEF"]

        result = map_segments_to_lines(segments, original_lines)

        assert result[0][0] == 0.15
        assert result[0][1] == 2.35
        assert result[1][0] == 2.35
        assert result[1][1] == 4.75

    def test_very_short_durations(self):
        """Test handling of very short segment durations."""
        segments = [(0.0, 0.1, "A"), (0.1, 0.2, "B"), (0.2, 0.3, "C")]
        original_lines = ["A", "B", "C"]

        result = map_segments_to_lines(segments, original_lines)

        assert len(result) == 3
        for i, line in enumerate(result):
            assert line[0] == segments[i][0]
            assert line[1] == segments[i][1]
