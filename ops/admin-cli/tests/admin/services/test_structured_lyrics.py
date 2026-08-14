"""Unit tests for structured-lyrics parser and flattener."""

from stream_of_worship.admin.services.structured_lyrics import (
    flatten_structured_lyrics,
    parse_structured_lyrics,
)

WORKED_EXAMPLE = """[Verse]
親愛耶穌　祢真愛我
毫無保留　我敬拜祢
因祢捨命　我回到父神面前
在恩典和應許中敬拜

[Pre-Chorus]
喔耶穌　喔耶穌
祢喜悅我向祢歌頌
喔耶穌　喔耶穌
高舉雙手　全心敬拜祢

[Chorus]
我要一生　一生敬拜祢
在祢殿中　瞻仰祢榮美
在祢豐盛恩典中　我歡欣歌頌
在祢救恩盼望中　我靈不住快樂"""

WORKED_EXAMPLE_FLATTENED = """[Verse]
親愛耶穌　祢真愛我
毫無保留　我敬拜祢
因祢捨命　我回到父神面前
在恩典和應許中敬拜
[Pre-Chorus]
喔耶穌　喔耶穌
祢喜悅我向祢歌頌
喔耶穌　喔耶穌
高舉雙手　全心敬拜祢
[Chorus]
我要一生　一生敬拜祢
在祢殿中　瞻仰祢榮美
在祢豐盛恩典中　我歡欣歌頌
在祢救恩盼望中　我靈不住快樂"""


class TestParseStructuredLyrics:
    """Tests for parse_structured_lyrics."""

    def test_worked_example(self):
        """The worked example parses to 3 sections with exact line lists."""
        result = parse_structured_lyrics(WORKED_EXAMPLE)

        assert result is not None
        sections = result["sections"]
        assert len(sections) == 3
        assert result["preamble_lines"] == []

        assert sections[0]["raw_label"] == "Verse"
        assert sections[0]["label"] == "verse"
        assert len(sections[0]["lines"]) == 4
        assert sections[0]["lines"][0] == "親愛耶穌　祢真愛我"

        assert sections[1]["raw_label"] == "Pre-Chorus"
        assert sections[1]["label"] == "pre-chorus"
        assert len(sections[1]["lines"]) == 4

        assert sections[2]["raw_label"] == "Chorus"
        assert sections[2]["label"] == "chorus"
        assert len(sections[2]["lines"]) == 4

    def test_no_section_tags_returns_none(self):
        """Description with only preamble (no [...] tags) returns None."""
        description = "This is a channel promo.\nSubscribe for more!"
        result = parse_structured_lyrics(description)

        assert result is None

    def test_empty_description_returns_none(self):
        """Empty description returns None."""
        assert parse_structured_lyrics("") is None
        assert parse_structured_lyrics(None) is None

    def test_preamble_lines(self):
        """Lines before the first section tag go into preamble_lines."""
        description = "Channel promo line\nSubscribe!\n\n[Verse]\nLine 1\nLine 2"
        result = parse_structured_lyrics(description)

        assert result is not None
        assert result["preamble_lines"] == ["Channel promo line", "Subscribe!"]
        assert len(result["sections"]) == 1
        assert result["sections"][0]["lines"] == ["Line 1", "Line 2"]

    def test_unrecognised_bracket_label_parsed_generically(self):
        """Unrecognised bracket-only lines are still treated as section headers."""
        description = "[Custom Section]\nLine 1\nLine 2"
        result = parse_structured_lyrics(description)

        assert result is not None
        assert len(result["sections"]) == 1
        assert result["sections"][0]["raw_label"] == "Custom Section"
        assert result["sections"][0]["label"] == "custom section"

    def test_numbered_sections(self):
        """Numbered section labels are preserved in raw_label, normalised in label."""
        description = "[Verse 1]\nLine 1\n[Chorus 2]\nLine 2\n[Bridge]\nLine 3\n[Intro]\nLine 4"
        result = parse_structured_lyrics(description)

        assert result is not None
        sections = result["sections"]
        assert len(sections) == 4
        assert sections[0]["raw_label"] == "Verse 1"
        assert sections[0]["label"] == "verse 1"
        assert sections[1]["raw_label"] == "Chorus 2"
        assert sections[1]["label"] == "chorus 2"
        assert sections[2]["raw_label"] == "Bridge"
        assert sections[2]["label"] == "bridge"
        assert sections[3]["raw_label"] == "Intro"
        assert sections[3]["label"] == "intro"

    def test_trailing_non_lyric_lines_discarded(self):
        """Trailing promo/URL lines after a blank-line gap are excluded."""
        description = (
            "[Chorus]\n"
            "我要一生敬拜祢\n"
            "\n"
            "訂閱我們的頻道\n"
            "https://example.com\n"
            "@channel_name\n"
        )
        result = parse_structured_lyrics(description)

        assert result is not None
        assert len(result["sections"]) == 1
        assert result["sections"][0]["lines"] == ["我要一生敬拜祢"]

    def test_blank_lines_between_sections_dropped(self):
        """Blank lines between sections are visual separators, not lyric content."""
        description = "[Verse]\nLine 1\n\n[Chorus]\nLine 2"
        result = parse_structured_lyrics(description)

        assert result is not None
        assert len(result["sections"]) == 2
        assert result["sections"][0]["lines"] == ["Line 1"]
        assert result["sections"][1]["lines"] == ["Line 2"]

    def test_carriage_returns_stripped(self):
        """Trailing \\r characters are stripped from each line."""
        description = "[Verse]\r\nLine 1\r\nLine 2\r\n"
        result = parse_structured_lyrics(description)

        assert result is not None
        assert result["sections"][0]["lines"] == ["Line 1", "Line 2"]


class TestFlattenStructuredLyrics:
    """Tests for flatten_structured_lyrics."""

    def test_round_trip_worked_example(self):
        """flatten(parse(ex)) matches the input tagged block, ignoring inter-section blanks."""
        parsed = parse_structured_lyrics(WORKED_EXAMPLE)
        assert parsed is not None

        flattened = flatten_structured_lyrics(parsed)
        assert flattened == WORKED_EXAMPLE_FLATTENED

    def test_preamble_excluded(self):
        """Preamble lines are NOT included in the flattened output."""
        description = "Promo line\n[Verse]\nLine 1"
        parsed = parse_structured_lyrics(description)
        assert parsed is not None

        flattened = flatten_structured_lyrics(parsed)
        assert "Promo line" not in flattened
        assert "[Verse]" in flattened
        assert "Line 1" in flattened

    def test_empty_sections(self):
        """Empty sections list produces empty string."""
        assert flatten_structured_lyrics({"sections": [], "preamble_lines": []}) == ""

    def test_no_trailing_blank_line(self):
        """Flattened output has no trailing blank line."""
        parsed = parse_structured_lyrics("[Verse]\nLine 1")
        assert parsed is not None

        flattened = flatten_structured_lyrics(parsed)
        assert not flattened.endswith("\n")
