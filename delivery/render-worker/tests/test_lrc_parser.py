from sow_render_worker.lrc_parser import (
    GlobalLRCLine,
    LRCLine,
    convert_to_global_timeline,
    estimate_last_lyric_duration,
    find_current_lyric_index,
    get_lyrics_time_range,
    group_lyrics_by_song,
    is_valid_lrc,
    parse_lrc,
)


class TestParseLRC:
    def test_basic_parse(self):
        content = "[00:01.50]Hello\n[00:05.00]World"
        result = parse_lrc(content)
        assert len(result) == 2
        assert result[0] == LRCLine(time_seconds=1.5, text="Hello")
        assert result[1] == LRCLine(time_seconds=5.0, text="World")

    def test_three_digit_milliseconds(self):
        content = "[00:01.500]Hello"
        result = parse_lrc(content)
        assert result[0].time_seconds == 1.5

    def test_two_digit_milliseconds_padded(self):
        content = "[00:01.50]Hello"
        result = parse_lrc(content)
        assert result[0].time_seconds == 1.5

    def test_sort_by_timestamp(self):
        content = "[00:10.00]Second\n[00:05.00]First\n[00:15.00]Third"
        result = parse_lrc(content)
        assert result[0].text == "First"
        assert result[1].text == "Second"
        assert result[2].text == "Third"

    def test_empty_lines_ignored(self):
        content = "[00:01.00]Hello\n\n[00:05.00]World\n"
        result = parse_lrc(content)
        assert len(result) == 2

    def test_lines_without_timestamp_ignored(self):
        content = "No timestamp here\n[00:01.00]Hello\nJust text"
        result = parse_lrc(content)
        assert len(result) == 1
        assert result[0].text == "Hello"

    def test_empty_text_lines_preserved(self):
        content = "[00:01.00]\n[00:05.00]Hello"
        result = parse_lrc(content)
        assert result == [
            LRCLine(time_seconds=1.0, text=""),
            LRCLine(time_seconds=5.0, text="Hello"),
        ]

    def test_whitespace_only_text_lines_preserved_as_blank(self):
        content = "[00:01.00]   \n[00:05.00]Hello"
        result = parse_lrc(content)
        assert result[0] == LRCLine(time_seconds=1.0, text="")

    def test_blank_lines_sort_by_timestamp(self):
        content = "[00:10.00]Second\n[00:01.00]\n[00:05.00]First"
        result = parse_lrc(content)
        assert [line.text for line in result] == ["", "First", "Second"]

    def test_empty_content(self):
        result = parse_lrc("")
        assert result == []

    def test_no_valid_lines(self):
        content = "Just some text\nNo timestamps here\nAnother line"
        result = parse_lrc(content)
        assert result == []

    def test_minutes_and_seconds(self):
        content = "[02:30.00]Chorus"
        result = parse_lrc(content)
        assert result[0].time_seconds == 150.0

    def test_chinese_text(self):
        content = "[00:05.00]讚美之泉\n[00:10.00]哈利路亞"
        result = parse_lrc(content)
        assert len(result) == 2
        assert result[0].text == "讚美之泉"
        assert result[1].text == "哈利路亞"

    def test_trailing_whitespace_stripped(self):
        content = "[00:01.00]  Hello  "
        result = parse_lrc(content)
        assert result[0].text == "Hello"

    def test_single_line(self):
        content = "[00:01.00]Only line"
        result = parse_lrc(content)
        assert len(result) == 1
        assert result[0].text == "Only line"


class TestConvertToGlobalTimeline:
    def test_basic_conversion(self):
        local_lines = [
            LRCLine(time_seconds=5.0, text="Hello"),
            LRCLine(time_seconds=10.0, text="World"),
        ]
        result = convert_to_global_timeline(local_lines, 30.0, "Test Song")
        assert len(result) == 2
        assert result[0] == GlobalLRCLine(
            text="Hello",
            local_time_seconds=5.0,
            global_time_seconds=35.0,
            title="Test Song",
        )
        assert result[1] == GlobalLRCLine(
            text="World",
            local_time_seconds=10.0,
            global_time_seconds=40.0,
            title="Test Song",
        )

    def test_zero_offset(self):
        local_lines = [LRCLine(time_seconds=3.0, text="Start")]
        result = convert_to_global_timeline(local_lines, 0.0, "Song A")
        assert result[0].global_time_seconds == 3.0
        assert result[0].local_time_seconds == 3.0

    def test_empty_lines(self):
        result = convert_to_global_timeline([], 10.0, "Empty")
        assert result == []

    def test_title_preserved(self):
        local_lines = [LRCLine(time_seconds=1.0, text="Hi")]
        result = convert_to_global_timeline(local_lines, 0.0, "My Song Title")
        assert result[0].title == "My Song Title"


class TestEstimateLastLyricDuration:
    def _make_lyrics(self, times_and_texts):
        return [
            GlobalLRCLine(text=t, local_time_seconds=ts, global_time_seconds=ts, title="Song")
            for ts, t in times_and_texts
        ]

    def test_empty_lyrics(self):
        assert estimate_last_lyric_duration([]) == 5.0

    def test_matching_previous_text(self):
        lyrics = self._make_lyrics(
            [
                (0.0, "Chorus"),
                (5.0, "Verse"),
                (10.0, "Chorus"),
            ]
        )
        result = estimate_last_lyric_duration(lyrics)
        assert result == 5.0

    def test_matching_previous_text_minimum_3(self):
        lyrics = self._make_lyrics(
            [
                (0.0, "Chorus"),
                (1.0, "Chorus"),
            ]
        )
        result = estimate_last_lyric_duration(lyrics)
        assert result == 3.0

    def test_fallback_char_count_chinese(self):
        lyrics = self._make_lyrics([(0.0, "讚美之泉")])
        result = estimate_last_lyric_duration(lyrics)
        assert result >= 3.0

    def test_fallback_char_count_ascii(self):
        lyrics = self._make_lyrics([(0.0, "Hello")])
        result = estimate_last_lyric_duration(lyrics)
        assert result >= 3.0

    def test_fallback_with_bpm(self):
        lyrics = self._make_lyrics([(0.0, "Hello")])
        result_fast = estimate_last_lyric_duration(lyrics, tempo_bpm=140.0)
        result_slow = estimate_last_lyric_duration(lyrics, tempo_bpm=70.0)
        assert result_fast < result_slow

    def test_fallback_zero_bpm_uses_default(self):
        lyrics = self._make_lyrics([(0.0, "Hello")])
        result = estimate_last_lyric_duration(lyrics, tempo_bpm=0.0)
        assert result >= 3.0

    def test_fallback_none_bpm_uses_default(self):
        lyrics = self._make_lyrics([(0.0, "Hello")])
        result = estimate_last_lyric_duration(lyrics, tempo_bpm=None)
        assert result >= 3.0

    def test_single_line_fallback(self):
        lyrics = self._make_lyrics([(0.0, "Only line")])
        result = estimate_last_lyric_duration(lyrics)
        assert result >= 3.0

    def test_skips_trailing_blank_lines(self):
        lyrics = self._make_lyrics(
            [
                (0.0, "Chorus"),
                (5.0, "Verse"),
                (10.0, "Chorus"),
                (14.0, ""),
            ]
        )
        result = estimate_last_lyric_duration(lyrics)
        assert result == 5.0

    def test_all_blank_lines_return_default(self):
        lyrics = self._make_lyrics([(0.0, ""), (5.0, "")])
        assert estimate_last_lyric_duration(lyrics) == 5.0


class TestFindCurrentLyricIndex:
    def _make_lyrics(self, times):
        return [
            GlobalLRCLine(
                text=f"Line {i}",
                local_time_seconds=ts,
                global_time_seconds=ts,
                title="Song",
            )
            for i, ts in enumerate(times)
        ]

    def test_before_first_lyric(self):
        lyrics = self._make_lyrics([5.0, 10.0, 15.0])
        assert find_current_lyric_index(lyrics, 2.0) == -1

    def test_at_first_lyric(self):
        lyrics = self._make_lyrics([5.0, 10.0, 15.0])
        assert find_current_lyric_index(lyrics, 5.0) == 0

    def test_between_lyrics(self):
        lyrics = self._make_lyrics([5.0, 10.0, 15.0])
        assert find_current_lyric_index(lyrics, 7.0) == 0

    def test_at_last_lyric(self):
        lyrics = self._make_lyrics([5.0, 10.0, 15.0])
        assert find_current_lyric_index(lyrics, 15.0) == 2

    def test_after_last_lyric(self):
        lyrics = self._make_lyrics([5.0, 10.0, 15.0])
        assert find_current_lyric_index(lyrics, 20.0) == 2

    def test_empty_lyrics(self):
        assert find_current_lyric_index([], 5.0) == -1

    def test_single_lyric_before(self):
        lyrics = self._make_lyrics([10.0])
        assert find_current_lyric_index(lyrics, 5.0) == -1

    def test_single_lyric_at(self):
        lyrics = self._make_lyrics([10.0])
        assert find_current_lyric_index(lyrics, 10.0) == 0


class TestGroupLyricsBySong:
    def test_basic_grouping(self):
        lyrics = [
            GlobalLRCLine("A", 1.0, 1.0, "Song 1"),
            GlobalLRCLine("B", 2.0, 2.0, "Song 2"),
            GlobalLRCLine("C", 3.0, 3.0, "Song 1"),
        ]
        grouped = group_lyrics_by_song(lyrics)
        assert len(grouped) == 2
        assert len(grouped["Song 1"]) == 2
        assert len(grouped["Song 2"]) == 1

    def test_empty_lyrics(self):
        grouped = group_lyrics_by_song([])
        assert grouped == {}

    def test_single_song(self):
        lyrics = [
            GlobalLRCLine("A", 1.0, 1.0, "Only Song"),
            GlobalLRCLine("B", 2.0, 2.0, "Only Song"),
        ]
        grouped = group_lyrics_by_song(lyrics)
        assert len(grouped) == 1
        assert len(grouped["Only Song"]) == 2

    def test_preserves_order(self):
        lyrics = [
            GlobalLRCLine("A", 1.0, 1.0, "Song 1"),
            GlobalLRCLine("B", 2.0, 2.0, "Song 2"),
            GlobalLRCLine("C", 3.0, 3.0, "Song 1"),
        ]
        grouped = group_lyrics_by_song(lyrics)
        assert grouped["Song 1"][0].text == "A"
        assert grouped["Song 1"][1].text == "C"


class TestIsValidLRC:
    def test_valid_lrc(self):
        assert is_valid_lrc("[00:01.00]Hello") is True

    def test_valid_lrc_three_digit_ms(self):
        assert is_valid_lrc("[00:01.500]Hello") is True

    def test_invalid_no_timestamp(self):
        assert is_valid_lrc("Just some text") is False

    def test_empty_string(self):
        assert is_valid_lrc("") is False

    def test_multiple_lines_valid(self):
        content = "[00:01.00]Hello\n[00:05.00]World"
        assert is_valid_lrc(content) is True

    def test_partial_timestamp_invalid(self):
        assert is_valid_lrc("[00:01]Hello") is False


class TestGetLyricsTimeRange:
    def test_basic_range(self):
        lyrics = [
            LRCLine(time_seconds=5.0, text="First"),
            LRCLine(time_seconds=10.0, text="Last"),
        ]
        result = get_lyrics_time_range(lyrics)
        assert result is not None
        assert result["first_time"] == 5.0
        assert result["last_time"] == 10.0

    def test_empty_lyrics(self):
        assert get_lyrics_time_range([]) is None

    def test_single_line(self):
        lyrics = [LRCLine(time_seconds=7.5, text="Only")]
        result = get_lyrics_time_range(lyrics)
        assert result is not None
        assert result["first_time"] == 7.5
        assert result["last_time"] == 7.5
