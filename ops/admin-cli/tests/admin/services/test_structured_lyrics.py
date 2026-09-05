"""Unit tests for structured-lyrics parser and flattener."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stream_of_worship.admin.services.structured_lyrics import (
    StructuredLyricsResult,
    StructuredLyricsSection,
    extract_structured_lyrics_with_llm,
    flatten_structured_lyrics,
    parse_structured_lyrics,
    parse_structured_lyrics_smart,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

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


class TestStructuredLyricsModels:
    """Tests for Pydantic models and .to_dict() round-trip."""

    def test_section_to_dict(self):
        """StructuredLyricsSection.to_dict() matches the existing dict shape."""
        section = StructuredLyricsSection(
            label="verse",
            raw_label="Verse",
            lines=["Line 1", "Line 2"],
        )
        d = section.to_dict()
        assert d == {"label": "verse", "raw_label": "Verse", "lines": ["Line 1", "Line 2"]}

    def test_result_to_dict(self):
        """StructuredLyricsResult.to_dict() matches the existing dict shape."""
        result = StructuredLyricsResult(
            sections=[
                StructuredLyricsSection(label="chorus", raw_label="Chorus", lines=["Line A"]),
            ],
            preamble_lines=["Intro line"],
        )
        d = result.to_dict()
        assert d == {
            "sections": [
                {"label": "chorus", "raw_label": "Chorus", "lines": ["Line A"]},
            ],
            "preamble_lines": ["Intro line"],
        }

    def test_to_dict_matches_parse_structured_lyrics_shape(self):
        """to_dict() output is structurally identical to parse_structured_lyrics output."""
        parsed = parse_structured_lyrics(WORKED_EXAMPLE)
        assert parsed is not None

        result = StructuredLyricsResult(
            sections=[
                StructuredLyricsSection(
                    label=s["label"], raw_label=s["raw_label"], lines=s["lines"]
                )
                for s in parsed["sections"]
            ],
            preamble_lines=parsed["preamble_lines"],
        )
        assert result.to_dict() == parsed

    def test_empty_result_to_dict(self):
        """Empty StructuredLyricsResult.to_dict() produces empty lists."""
        result = StructuredLyricsResult()
        assert result.to_dict() == {"sections": [], "preamble_lines": []}


class TestExtractStructuredLyricsWithLLM:
    """Tests for extract_structured_lyrics_with_llm with mocked LLM."""

    def test_empty_description_returns_none(self):
        """Empty/None description returns None without calling LLM."""
        assert extract_structured_lyrics_with_llm("") is None
        assert extract_structured_lyrics_with_llm(None) is None

    def test_llm_drops_mid_section_promo(self):
        """LLM cleanup drops a promo line that appears mid-section."""
        description = "[Verse]\n" "親愛耶穌　祢真愛我\n" "訂閱我們的頻道！\n" "毫無保留　我敬拜祢\n"
        expected = StructuredLyricsResult(
            sections=[
                StructuredLyricsSection(
                    label="verse",
                    raw_label="Verse",
                    lines=["親愛耶穌　祢真愛我", "毫無保留　我敬拜祢"],
                ),
            ],
            preamble_lines=[],
        )

        fake_chat = MagicMock()
        fake_structured = MagicMock()
        fake_structured.invoke.return_value = expected
        fake_chat.with_structured_output.return_value = fake_structured

        with patch(
            "stream_of_worship.admin.services.structured_lyrics.build_chat_model_for_lyrics",
            return_value=fake_chat,
        ):
            result = extract_structured_lyrics_with_llm(description)

        assert result is not None
        assert len(result.sections) == 1
        assert result.sections[0].lines == ["親愛耶穌　祢真愛我", "毫無保留　我敬拜祢"]
        assert "訂閱我們的頻道！" not in result.sections[0].lines

    def test_llm_failure_propagates(self):
        """LLM call failure (e.g. ValueError) propagates to caller."""
        description = "[Verse]\nLine 1"

        fake_chat = MagicMock()
        fake_structured = MagicMock()
        fake_structured.invoke.side_effect = ValueError("malformed JSON")
        fake_chat.with_structured_output.return_value = fake_structured

        with (
            patch(
                "stream_of_worship.admin.services.structured_lyrics.build_chat_model_for_lyrics",
                return_value=fake_chat,
            ),
            pytest.raises(ValueError, match="malformed JSON"),
        ):
            extract_structured_lyrics_with_llm(description)

    def test_llm_receives_heuristic_hint(self):
        """The heuristic parse result is passed to the LLM as a hint."""
        description = "[Verse]\nLine 1\n[Chorus]\nLine 2"

        fake_chat = MagicMock()
        fake_structured = MagicMock()
        fake_structured.invoke.return_value = StructuredLyricsResult()
        fake_chat.with_structured_output.return_value = fake_structured

        with patch(
            "stream_of_worship.admin.services.structured_lyrics.build_chat_model_for_lyrics",
            return_value=fake_chat,
        ):
            extract_structured_lyrics_with_llm(description)

        fake_structured.invoke.assert_called_once()
        prompt = fake_structured.invoke.call_args[0][0]
        assert "candidate parse" in prompt
        assert "verse" in prompt


class TestParseStructuredLyricsSmart:
    """Tests for the smart orchestration entrypoint."""

    def test_use_llm_false_returns_heuristic_dict(self):
        """use_llm=False returns the heuristic dict unchanged."""
        result = parse_structured_lyrics_smart(WORKED_EXAMPLE, use_llm=False)
        expected = parse_structured_lyrics(WORKED_EXAMPLE)
        assert result == expected

    def test_use_llm_false_empty_returns_none(self):
        """use_llm=False with empty description returns None."""
        assert parse_structured_lyrics_smart("", use_llm=False) is None
        assert parse_structured_lyrics_smart(None, use_llm=False) is None

    def test_use_llm_true_with_env_unset_raises_runtime_error(self, monkeypatch):
        """use_llm=True with SOW_LLM_API_KEY/SOW_LLM_MODEL unset raises RuntimeError."""
        monkeypatch.delenv("SOW_LLM_API_KEY", raising=False)
        monkeypatch.delenv("SOW_LLM_MODEL", raising=False)
        with pytest.raises(RuntimeError, match="SOW_LLM_API_KEY"):
            parse_structured_lyrics_smart(WORKED_EXAMPLE, use_llm=True)

    def test_use_llm_true_empty_returns_none(self, monkeypatch):
        """use_llm=True with empty description returns None without calling LLM."""
        assert parse_structured_lyrics_smart("", use_llm=True) is None
        assert parse_structured_lyrics_smart(None, use_llm=True) is None

    def test_use_llm_true_returns_llm_dict(self):
        """use_llm=True returns the LLM-cleaned dict via to_dict()."""
        expected = StructuredLyricsResult(
            sections=[
                StructuredLyricsSection(label="verse", raw_label="Verse", lines=["Line 1"]),
            ],
            preamble_lines=[],
        )
        fake_chat = MagicMock()
        fake_structured = MagicMock()
        fake_structured.invoke.return_value = expected
        fake_chat.with_structured_output.return_value = fake_structured

        with patch(
            "stream_of_worship.admin.services.structured_lyrics.build_chat_model_for_lyrics",
            return_value=fake_chat,
        ):
            result = parse_structured_lyrics_smart(WORKED_EXAMPLE, use_llm=True)

        assert result is not None
        assert result == expected.to_dict()

    def test_use_llm_true_empty_sections_returns_none(self):
        """use_llm=True with an LLM result of zero sections returns None (not {})."""
        fake_chat = MagicMock()
        fake_structured = MagicMock()
        fake_structured.invoke.return_value = StructuredLyricsResult()
        fake_chat.with_structured_output.return_value = fake_structured

        with patch(
            "stream_of_worship.admin.services.structured_lyrics.build_chat_model_for_lyrics",
            return_value=fake_chat,
        ):
            result = parse_structured_lyrics_smart(WORKED_EXAMPLE, use_llm=True)

        assert result is None


class TestFixtureFiles:
    """Tests that verify committed fixture files are valid."""

    def test_description_fixture_exists(self):
        """The description fixture file exists and is non-empty."""
        path = FIXTURES_DIR / "_XgP0p-S4S8_description.txt"
        assert path.exists()
        assert path.read_text(encoding="utf-8").strip() != ""

    def test_expected_json_fixture_valid(self):
        """The expected JSON fixture is valid and has the right shape."""
        path = FIXTURES_DIR / "_XgP0p-S4S8_expected.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "sections" in data
        assert "preamble_lines" in data
        assert len(data["sections"]) == 3
        assert data["sections"][0]["label"] == "verse"
        assert data["sections"][1]["label"] == "pre-chorus"
        assert data["sections"][2]["label"] == "chorus"
