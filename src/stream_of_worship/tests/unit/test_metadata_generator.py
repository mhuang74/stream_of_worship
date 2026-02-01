"""Tests for metadata generation."""

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

try:
    from stream_of_worship.ingestion.metadata_generator import (
        SongMetadata,
        MetadataGenerator,
    )
    HAS_IMPORT = True
except ImportError:
    HAS_IMPORT = False
    SongMetadata = None
    MetadataGenerator = None


if not HAS_IMPORT:
    pytest.skip("metadata_generation dependencies not installed", allow_module_level=True)


class TestSongMetadata:
    """Tests for SongMetadata dataclass."""

    def test_creation_with_all_fields(self):
        """Test creating SongMetadata with all fields."""
        metadata = SongMetadata(
            ai_summary="A worship song about God's love.",
            themes=["Love", "Praise"],
            bible_verses=["John 3:16", "Romans 5:8"],
            vocalist="mixed",
        )

        assert metadata.ai_summary == "A worship song about God's love."
        assert metadata.themes == ["Love", "Praise"]
        assert len(metadata.bible_verses) == 2
        assert metadata.vocalist == "mixed"

    def test_to_dict(self):
        """Test converting SongMetadata to dictionary."""
        metadata = SongMetadata(
            ai_summary="Test summary",
            themes=["Worship"],
            bible_verses=["Psalm 23:1"],
            vocalist="female",
        )

        result = metadata.to_dict()

        assert result["ai_summary"] == "Test summary"
        assert result["themes"] == ["Worship"]
        assert result["bible_verses"] == ["Psalm 23:1"]
        assert result["vocalist"] == "female"

    def test_from_dict(self):
        """Test creating SongMetadata from dictionary."""
        data = {
            "ai_summary": "A test song.",
            "themes": ["Praise", "Worship"],
            "bible_verses": ["Psalm 23:1"],
            "vocalist": "male",
        }

        metadata = SongMetadata.from_dict(data)

        assert metadata.ai_summary == "A test song."
        assert metadata.themes == ["Praise", "Worship"]
        assert metadata.bible_verses == ["Psalm 23:1"]
        assert metadata.vocalist == "male"

    def test_from_dict_with_defaults(self):
        """Test creating SongMetadata with minimal data."""
        data = {
            "ai_summary": "Minimal song.",
        }

        metadata = SongMetadata.from_dict(data)

        assert metadata.ai_summary == "Minimal song."
        assert metadata.themes == []
        assert metadata.bible_verses == []
        assert metadata.vocalist == "mixed"


class TestMetadataGenerator:
    """Tests for MetadataGenerator class."""

    @patch("stream_of_worship.ingestion.metadata_generator.openai")
    def test_init_with_defaults(self, mock_openai):
        """Test generator initialization with defaults."""
        generator = MetadataGenerator()
        assert generator.model == "openai/gpt-4o-mini"
        assert generator.api_base == "https://openrouter.ai/api/v1"

    @patch("stream_of_worship.ingestion.metadata_generator.openai")
    def test_init_creates_client_lazily(self, mock_openai):
        """Test that LLM client is created lazily."""
        mock_openai.OpenAI.return_value = MagicMock()
        generator = MetadataGenerator()
        assert generator._client is None
        # Access property should create client
        _ = generator.client
        mock_openai.OpenAI.assert_called_once()

    @patch("stream_of_worship.ingestion.metadata_generator.openai")
    def test_client_raises_without_api_key(self, mock_openai):
        """Test that accessing LLM client raises ValueError without API key."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError):
                generator = MetadataGenerator()
                _ = generator.client

    @patch("stream_of_worship.ingestion.metadata_generator.openai")
    def test_generate_with_full_params(self, mock_openai):
        """Test generate with all parameters."""
        # Mock response with metadata
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "ai_summary": "Test summary",
            "themes": ["Praise"],
            "bible_verses": ["Psalm 23:1"],
            "vocalist": "mixed",
        })

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.OpenAI.return_value = mock_client

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            generator = MetadataGenerator()
            result = generator.generate(
                title="Test Song",
                artist="Test Artist",
                lyrics_text="Test lyrics",
                key="C",
                bpm=120.0,
            )

        assert result.ai_summary == "Test summary"
        assert "Praise" in result.themes
        assert "Psalm 23:1" in result.bible_verses
        assert result.vocalist == "mixed"

    @patch("stream_of_worship.ingestion.metadata_generator.openai")
    def test_generate_filters_invalid_themes(self, mock_openai):
        """Test that generate filters out invalid theme names."""
        # Mock response with some valid and some invalid themes
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "ai_summary": "Test song.",
            "themes": ["Praise", "InvalidTheme", "Worship", "AnotherBadTheme"],
            "bible_verses": [],
            "vocalist": "mixed",
        })

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.OpenAI.return_value = mock_client

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            generator = MetadataGenerator()
            result = generator.generate(
                title="Test Song",
                artist="Test Artist",
                lyrics_text="Test lyrics.",
                key="C",
                bpm=100.0,
            )

        # Only valid themes should be kept
        assert "Praise" in result.themes
        assert "Worship" in result.themes
        assert "InvalidTheme" not in result.themes
        assert "AnotherBadTheme" not in result.themes

    @patch("stream_of_worship.ingestion.metadata_generator.openai")
    def test_generate_validates_vocalist(self, mock_openai):
        """Test that generate validates vocalist type."""
        # Mock response with invalid vocalist
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "ai_summary": "Test song.",
            "themes": ["Praise"],
            "bible_verses": [],
            "vocalist": "invalid",
        })

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.OpenAI.return_value = mock_client

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            generator = MetadataGenerator()
            result = generator.generate(
                title="Test Song",
                artist="Test Artist",
                lyrics_text="Test lyrics.",
                key="C",
                bpm=100.0,
            )

        # Invalid vocalist should default to "mixed"
        assert result.vocalist == "mixed"

    @patch("stream_of_worship.ingestion.metadata_generator.openai")
    def test_generate_handles_json_decode_error(self, mock_openai):
        """Test that generate raises RuntimeError for invalid JSON."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "invalid json{{{"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.OpenAI.return_value = mock_client

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            generator = MetadataGenerator()
            with pytest.raises(RuntimeError) as exc_info:
                generator.generate(
                    title="Test Song",
                    artist="Test Artist",
                    lyrics_text="Test lyrics.",
                )

            assert "invalid JSON" in str(exc_info.value)

    @patch("stream_of_worship.ingestion.metadata_generator.openai")
    def test_get_tempo_category(self, mock_openai):
        """Test _get_tempo_category method."""
        generator = MetadataGenerator()

        assert generator._get_tempo_category(85.0) == "slow"
        assert generator._get_tempo_category(89.9) == "slow"
        assert generator._get_tempo_category(90.0) == "medium"
        assert generator._get_tempo_category(100.0) == "medium"
        assert generator._get_tempo_category(129.9) == "medium"
        assert generator._get_tempo_category(130.0) == "fast"

    @patch("stream_of_worship.ingestion.metadata_generator.openai")
    def test_filter_valid_themes(self, mock_openai):
        """Test _filter_valid_themes method."""
        generator = MetadataGenerator()

        # Mix of valid and invalid themes
        input_themes = [
            "Praise",  # Valid
            "praise",  # Valid (case variant - converted to canonical form)
            "InvalidTheme",  # Invalid
            "WORSHIP",  # Valid (uppercase - converted to canonical form)
            "BadTheme",  # Invalid
            "Love",  # Valid
        ]

        result = generator._filter_valid_themes(input_themes)

        # The function returns canonical capitalization for valid themes
        assert "Praise" in result
        assert "Worship" in result
        assert "Love" in result
        assert "InvalidTheme" not in result
        assert "BadTheme" not in result
