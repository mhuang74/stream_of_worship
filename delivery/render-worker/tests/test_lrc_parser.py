import pytest

from sow_render_worker.audio_engine import AudioClipInfo
from sow_render_worker.lrc_parser import (
    GlobalLRCLine,
    LRCLine,
    estimate_last_lyric_duration,
    find_current_lyric_index,
    get_lyrics_time_range,
    group_lyrics_by_song,
    is_valid_lrc,
    lyric_boundary_tolerance,
    parse_lrc,
    remap_lrc_to_clips,
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


def _clip(
    source_start: float,
    source_end: float,
    target_start: float,
) -> AudioClipInfo:
    return AudioClipInfo(
        audio_path="/tmp/song.mp3",
        source_start_seconds=source_start,
        source_end_seconds=source_end,
        start_time_seconds=target_start,
    )


class TestLyricBoundaryTolerance:
    def test_tolerance_follows_tempo(self):
        assert lyric_boundary_tolerance(86.13281) == pytest.approx(1.3931973193490377)
        assert lyric_boundary_tolerance(60) == 2.0
        assert lyric_boundary_tolerance(None) == 0.5
        assert lyric_boundary_tolerance(0) == 0.5
        assert lyric_boundary_tolerance(float("nan")) == 0.5


class TestRemapLrcToClips:
    def test_full_file_clip_maps_every_line(self):
        local_lines = [
            LRCLine(time_seconds=5.0, text="Hello"),
            LRCLine(time_seconds=10.0, text="World"),
        ]
        result = remap_lrc_to_clips(local_lines, (_clip(0.0, 30.0, 0.0),), "Test Song")
        assert result == [
            GlobalLRCLine(
                text="Hello",
                local_time_seconds=5.0,
                global_time_seconds=5.0,
                title="Test Song",
            ),
            GlobalLRCLine(
                text="World",
                local_time_seconds=10.0,
                global_time_seconds=10.0,
                title="Test Song",
            ),
        ]

    def test_reordered_and_repeated_clips_remap_each_range(self):
        local_lines = [
            LRCLine(time_seconds=1.0, text="line at 1"),
            LRCLine(time_seconds=11.0, text="line at 11"),
            LRCLine(time_seconds=21.0, text="line at 21"),
        ]
        clips = (
            _clip(10.0, 20.0, 0.0),
            _clip(0.0, 10.0, 10.0),
            _clip(10.0, 20.0, 20.0),
        )
        result = remap_lrc_to_clips(local_lines, clips, "Song")
        assert [(line.text, line.global_time_seconds) for line in result] == [
            ("line at 11", 1.0),
            ("line at 1", 11.0),
            ("line at 11", 21.0),
        ]

    def test_omitted_range_contributes_no_lines(self):
        local_lines = [
            LRCLine(time_seconds=1.0, text="kept"),
            LRCLine(time_seconds=15.0, text="omitted"),
        ]
        result = remap_lrc_to_clips(local_lines, (_clip(0.0, 10.0, 0.0),), "Song")
        assert [line.text for line in result] == ["kept"]

    def test_boundary_at_end_belongs_to_next_clip(self):
        local_lines = [LRCLine(time_seconds=5.0, text="boundary")]
        clips = (_clip(0.0, 5.0, 0.0), _clip(5.0, 10.0, 5.0))
        result = remap_lrc_to_clips(local_lines, clips, "Song")
        assert len(result) == 1
        assert result[0].global_time_seconds == 5.0

    def test_target_start_offsets_line(self):
        local_lines = [LRCLine(time_seconds=7.0, text="mid")]
        result = remap_lrc_to_clips(local_lines, (_clip(5.0, 10.0, 30.0),), "Song")
        assert result[0].global_time_seconds == 32.0
        assert result[0].local_time_seconds == 7.0

    def test_blank_gap_placeholder_retained(self):
        local_lines = [LRCLine(time_seconds=2.0, text=""), LRCLine(time_seconds=3.0, text="text")]
        result = remap_lrc_to_clips(local_lines, (_clip(0.0, 5.0, 10.0),), "Song")
        assert [(line.text, line.global_time_seconds) for line in result] == [
            ("", 12.0),
            ("text", 13.0),
        ]

    def test_pickup_before_source_start_belongs_to_clip(self):
        # Forced-alignment onset 30 ms before the beat-snapped section bound:
        # the line is the pickup into the next clip, emitted exactly once, at
        # that clip's first frame.
        local_lines = [LRCLine(time_seconds=53.30, text="chorus")]
        clips = (_clip(27.37, 53.33, 0.0), _clip(53.33, 80.80, 25.96))
        result = remap_lrc_to_clips(local_lines, clips, "Song")
        assert [(line.text, line.global_time_seconds) for line in result] == [("chorus", 25.96)]

    def test_unarranged_full_file_clip_keeps_its_tail(self):
        # An unarranged render is one clip covering the whole recording, so
        # nothing can inherit a line in its final half second.
        local_lines = [LRCLine(time_seconds=29.8, text="last")]
        result = remap_lrc_to_clips(local_lines, (_clip(0.0, 30.0, 0.0),), "Song")
        assert [(line.text, line.global_time_seconds) for line in result] == [("last", 29.8)]

    def test_last_clip_keeps_its_tail(self):
        local_lines = [LRCLine(time_seconds=79.9, text="final")]
        clips = (_clip(27.37, 53.33, 0.0), _clip(53.33, 80.80, 25.96))
        result = remap_lrc_to_clips(local_lines, clips, "Song")
        assert len(result) == 1
        assert result[0].global_time_seconds == pytest.approx(52.53)

    def test_adjacent_short_clip_does_not_duplicate_lines(self):
        # Real shape: a 0–0.21 s section followed by one starting at 0.21 s.
        local_lines = [
            LRCLine(time_seconds=0.05, text="opening"),
            LRCLine(time_seconds=0.4, text="intro"),
        ]
        clips = (_clip(0.0, 0.21, 0.0), _clip(0.21, 15.91, 0.21))
        result = remap_lrc_to_clips(local_lines, clips, "Song")
        assert [line.text for line in result] == ["opening", "intro"]

    def test_boundary_line_appears_once_across_tiled_clips(self):
        local_lines = [LRCLine(time_seconds=53.30, text="chorus")]
        clips = (_clip(27.37, 53.33, 0.0), _clip(53.33, 80.80, 25.96))
        assert len(remap_lrc_to_clips(local_lines, clips, "Song")) == 1

    def test_short_clip_keeps_interior_line(self):
        # A 0.3 s clip is shorter than the tolerance; it keeps its own interior.
        local_lines = [LRCLine(time_seconds=10.2, text="short")]
        result = remap_lrc_to_clips(local_lines, (_clip(10.0, 10.3, 0.0),), "Song")
        assert len(result) == 1
        assert result[0].global_time_seconds == pytest.approx(0.2)

    def test_no_clips_returns_empty(self):
        assert remap_lrc_to_clips([LRCLine(time_seconds=1.0, text="x")], (), "Song") == []

    def test_empty_lines_returns_empty(self):
        assert remap_lrc_to_clips([], (_clip(0.0, 10.0, 0.0),), "Song") == []

    def test_title_preserved(self):
        local_lines = [LRCLine(time_seconds=1.0, text="Hi")]
        result = remap_lrc_to_clips(local_lines, (_clip(0.0, 10.0, 0.0),), "My Song Title")
        assert result[0].title == "My Song Title"

    def test_pickup_kept_when_preceding_section_is_omitted(self):
        # The 10–20 s section was dropped, so no clip ends at the 20 s boundary
        # the pickup is sung into; ownership tolerates 0.5 s and the clip that
        # starts there keeps the line, at its first frame.
        local_lines = [LRCLine(time_seconds=19.8, text="pickup")]
        result = remap_lrc_to_clips(local_lines, (_clip(20.0, 30.0, 0.0),), "Song")
        assert [(line.text, line.global_time_seconds) for line in result] == [("pickup", 0.0)]

    def test_late_line_stays_with_its_clip_inside_its_target_window(self):
        # 10.3 s is 0.3 s after the first clip's end and nowhere near the second
        # clip's start: the line belongs to the first clip, clamped just inside
        # its target window so the highlight never leaks into the next Item.
        local_lines = [LRCLine(time_seconds=10.3, text="late")]
        clips = (_clip(0.0, 10.0, 0.0), _clip(20.0, 30.0, 10.0))
        result = remap_lrc_to_clips(local_lines, clips, "Song")
        assert [line.text for line in result] == ["late"]
        assert result[0].global_time_seconds == pytest.approx(9.999)

    def test_line_on_shared_boundary_belongs_to_the_later_clip(self):
        local_lines = [LRCLine(time_seconds=10.0, text="boundary")]
        clips = (_clip(0.0, 10.0, 0.0), _clip(10.0, 20.0, 10.0))
        result = remap_lrc_to_clips(local_lines, clips, "Song")
        assert [(line.text, line.global_time_seconds) for line in result] == [
            ("boundary", 10.0)
        ]

    def test_line_more_than_tolerance_outside_every_clip_is_dropped(self):
        local_lines = [LRCLine(time_seconds=10.6, text="omitted")]
        clips = (_clip(0.0, 10.0, 0.0), _clip(20.0, 30.0, 10.0))
        assert remap_lrc_to_clips(local_lines, clips, "Song") == []

    def test_stable_sort_by_global_time(self):
        local_lines = [LRCLine(time_seconds=2.0, text="a")]
        clips = (_clip(0.0, 5.0, 20.0), _clip(0.0, 5.0, 0.0))
        result = remap_lrc_to_clips(local_lines, clips, "Song")
        assert [line.global_time_seconds for line in result] == [2.0, 22.0]

    def test_one_beat_pickup_owned_by_first_clip(self):
        # Reported song (hereforyou at 86.13281 BPM): the first chorus clip
        # starts at 0.70 s while its first line is sung at 0.01 s — one beat
        # earlier — so the fixed 0.5 s bound dropped it from the frames while
        # the second chorus kept the identical line.
        local_lines = [
            LRCLine(time_seconds=0.01, text="To You our hearts are open"),
            LRCLine(time_seconds=2.82, text="Nothing here is hidden"),
        ]
        clips = (_clip(0.7, 22.04, 0.0), _clip(22.04, 69.33, 21.34))
        result = remap_lrc_to_clips(local_lines, clips, "Song", tempo_bpm=86.13281)
        assert [(line.text, line.global_time_seconds) for line in result] == [
            ("To You our hearts are open", 0.0),
            ("Nothing here is hidden", pytest.approx(2.12)),
        ]
        # The pre-tempo fallback still refuses the one-beat pickup.
        assert [line.text for line in remap_lrc_to_clips(local_lines, clips, "Song")] == [
            "Nothing here is hidden"
        ]

    def test_two_beat_pickup_belongs_to_next_clip(self):
        local_lines = [LRCLine(time_seconds=19.0, text="pickup")]
        clips = (_clip(0.0, 10.0, 0.0), _clip(20.0, 30.0, 20.0))
        result = remap_lrc_to_clips(local_lines, clips, "Song", tempo_bpm=60)
        assert [(line.text, line.global_time_seconds) for line in result] == [("pickup", 20.0)]
        # 19.0 s is exactly two beats before 20.0 s; the fallback's 0.5 s drops it.
        assert remap_lrc_to_clips(local_lines, clips, "Song") == []


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
