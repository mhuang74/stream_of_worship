"""Unit tests for LRC serialization, parsing, and formatting."""

import pytest

from stream_of_worship.admin.services.lrc_parser import (
    LRCLine,
    LRCFile,
    LRCParsedContent,
    LRCPreservedLine,
    build_draft_from_catalog,
    compute_lrc_hash,
    format_centiseconds,
    format_duration,
    parse_lrc,
    parse_lrc_full,
    serialize_lrc,
)


class TestFormatCentiseconds:
    def test_basic_formatting(self):
        assert format_centiseconds(0.0) == "[00:00.00]"

    def test_seconds_with_centiseconds(self):
        assert format_centiseconds(17.20) == "[00:17.20]"

    def test_minutes_and_seconds(self):
        assert format_centiseconds(83.45) == "[01:23.45]"

    def test_rounding_carry_5995(self):
        assert format_centiseconds(59.995) == "[01:00.00]"

    def test_rounding_carry_59990(self):
        assert format_centiseconds(59.990) == "[00:59.99]"

    def test_clamp_negative(self):
        assert format_centiseconds(-1.0) == "[00:00.00]"

    def test_large_timestamp(self):
        assert format_centiseconds(3600.0) == "[60:00.00]"

    def test_exact_centisecond(self):
        assert format_centiseconds(1.23) == "[00:01.23]"

    def test_millisecond_rounding_up(self):
        assert format_centiseconds(1.999) == "[00:02.00]"

    def test_millisecond_rounding_down(self):
        assert format_centiseconds(1.994) == "[00:01.99]"

    def test_zero(self):
        assert format_centiseconds(0.0) == "[00:00.00]"

    def test_59_seconds_99_centiseconds(self):
        assert format_centiseconds(59.99) == "[00:59.99]"

    def test_59_seconds_995_centiseconds_rolls_minute(self):
        assert format_centiseconds(59.995) == "[01:00.00]"


class TestParseLrcFull:
    def test_basic_timed_lines(self):
        content = "[00:10.50]First line\n[00:20.75]Second line\n"
        parsed = parse_lrc_full(content)
        assert len(parsed.timed_lines) == 2
        assert parsed.timed_lines[0].time_seconds == 10.5
        assert parsed.timed_lines[0].text == "First line"
        assert parsed.timed_lines[1].time_seconds == 20.75
        assert parsed.timed_lines[1].text == "Second line"

    def test_preserves_metadata_tags(self):
        content = "[ti:Song Title]\n[ar:Artist]\n[00:10.00]Lyric\n"
        parsed = parse_lrc_full(content)
        assert len(parsed.timed_lines) == 1
        assert len(parsed.preserved_lines) == 2
        assert parsed.preserved_lines[0].tag == "ti"
        assert parsed.preserved_lines[0].value == "Song Title"
        assert parsed.preserved_lines[1].tag == "ar"
        assert parsed.preserved_lines[1].value == "Artist"

    def test_preserves_unknown_lines(self):
        content = "[00:10.00]Lyric\nsome unknown line\n"
        parsed = parse_lrc_full(content)
        assert len(parsed.timed_lines) == 1
        unknown = [p for p in parsed.preserved_lines if p.tag is None and p.raw.strip()]
        assert len(unknown) == 1
        assert unknown[0].raw == "some unknown line"

    def test_preserves_blank_lines(self):
        content = "[00:10.00]First\n\n[00:20.00]Second\n"
        parsed = parse_lrc_full(content)
        blanks = [p for p in parsed.preserved_lines if not p.raw.strip()]
        assert len(blanks) == 1

    def test_empty_content(self):
        parsed = parse_lrc_full("")
        assert len(parsed.timed_lines) == 0
        assert len(parsed.preserved_lines) == 0

    def test_millisecond_format(self):
        content = "[00:10.500]Lyric\n"
        parsed = parse_lrc_full(content)
        assert parsed.timed_lines[0].time_seconds == 10.5

    def test_centisecond_format(self):
        content = "[00:10.50]Lyric\n"
        parsed = parse_lrc_full(content)
        assert parsed.timed_lines[0].time_seconds == 10.5


class TestSerializeLrc:
    def test_basic_serialization(self):
        lines = [
            LRCLine(time_seconds=10.5, text="First", raw_timestamp="[00:10.50]"),
            LRCLine(time_seconds=20.75, text="Second", raw_timestamp="[00:20.75]"),
        ]
        result = serialize_lrc(lines)
        assert "[00:10.50]First" in result
        assert "[00:20.75]Second" in result

    def test_serialization_with_preserved_metadata(self):
        lines = [
            LRCLine(time_seconds=10.0, text="Lyric", raw_timestamp="[00:10.00]"),
        ]
        preserved = [
            LRCPreservedLine(raw="[ti:Title]", tag="ti", value="Title"),
            LRCPreservedLine(raw="[ar:Artist]", tag="ar", value="Artist"),
        ]
        result = serialize_lrc(lines, preserved)
        assert "[ti:Title]" in result
        assert "[ar:Artist]" in result
        assert "[00:10.00]Lyric" in result

    def test_serialization_no_preserved(self):
        lines = [
            LRCLine(time_seconds=0.0, text="Hello", raw_timestamp="[00:00.00]"),
        ]
        result = serialize_lrc(lines)
        assert "[00:00.00]Hello" in result

    def test_round_trip(self):
        original = "[ti:Title]\n[ar:Artist]\n\n[00:10.50]First line\n[00:20.75]Second line\n"
        parsed = parse_lrc_full(original)
        serialized = serialize_lrc(parsed.timed_lines, parsed.preserved_lines)
        reparsed = parse_lrc_full(serialized)
        assert len(reparsed.timed_lines) == len(parsed.timed_lines)
        for orig, new in zip(parsed.timed_lines, reparsed.timed_lines):
            assert abs(orig.time_seconds - new.time_seconds) < 0.01
            assert orig.text == new.text


class TestParseLrcBackwardCompat:
    def test_basic_parse(self):
        content = "[00:10.50]First line\n[00:20.75]Second line\n"
        result = parse_lrc(content)
        assert result.line_count == 2
        assert result.duration_seconds == 20.75

    def test_raises_on_empty(self):
        with pytest.raises(ValueError, match="No valid LRC lines"):
            parse_lrc("")


class TestBuildDraftFromCatalog:
    def test_from_lyrics_lines_json(self):
        import json
        lines_json = json.dumps(["Line 1", "Line 2", "Line 3"])
        result = build_draft_from_catalog(lyrics_lines=lines_json)
        assert len(result) == 3
        assert result[0].text == "Line 1"
        assert result[0].time_seconds == 0.0

    def test_from_lyrics_raw(self):
        result = build_draft_from_catalog(lyrics_raw="Line 1\nLine 2\n")
        assert len(result) == 2
        assert result[0].text == "Line 1"

    def test_prefers_lyrics_lines_over_raw(self):
        import json
        lines_json = json.dumps(["From JSON"])
        result = build_draft_from_catalog(lyrics_lines=lines_json, lyrics_raw="From raw")
        assert len(result) == 1
        assert result[0].text == "From JSON"

    def test_empty_inputs(self):
        result = build_draft_from_catalog()
        assert len(result) == 0

    def test_strips_blank_lines(self):
        result = build_draft_from_catalog(lyrics_raw="Line 1\n\nLine 2\n")
        assert len(result) == 2

    def test_invalid_json_falls_back(self):
        result = build_draft_from_catalog(lyrics_lines="not json", lyrics_raw="Fallback")
        assert len(result) == 1
        assert result[0].text == "Fallback"


class TestComputeLrcHash:
    def test_deterministic(self):
        content = "[00:10.00]Hello"
        h1 = compute_lrc_hash(content)
        h2 = compute_lrc_hash(content)
        assert h1 == h2

    def test_different_content_different_hash(self):
        h1 = compute_lrc_hash("[00:10.00]Hello")
        h2 = compute_lrc_hash("[00:10.00]World")
        assert h1 != h2


class TestFormatDuration:
    def test_basic(self):
        assert format_duration(259) == "4:19"

    def test_with_leading_zero_seconds(self):
        assert format_duration(65) == "1:05"

    def test_zero(self):
        assert format_duration(0) == "0:00"
