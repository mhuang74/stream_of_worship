"""Integration tests for LRC generation pipeline.

These tests verify the complete LRC generation workflow including:
- LRC line formatting and parsing
- Beat snapping to grid
- LLM alignment with retries
- End-to-end generation with mocked dependencies
- Batch processing
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

try:
    from stream_of_worship.ingestion.lrc_generator import (
        LRCLine,
        WhisperWord,
        LRCGenerator,
        parse_lrc_file,
    )
    HAS_IMPORT = True
except ImportError:
    HAS_IMPORT = False

if not HAS_IMPORT:
    pytest.skip("lrc_generation dependencies not installed", allow_module_level=True)


class TestLRCLineFormatting:
    """Tests for LRC line formatting."""

    def test_format_under_60_seconds(self):
        """Test formatting timestamps under 60 seconds."""
        line = LRCLine(time_seconds=12.5, text="Test lyrics")
        assert line.format() == "[00:12.50] Test lyrics"

    def test_format_over_60_seconds(self):
        """Test formatting timestamps over 60 seconds."""
        line = LRCLine(time_seconds=65.75, text="Line two")
        assert line.format() == "[01:05.75] Line two"

    def test_format_over_120_seconds(self):
        """Test formatting timestamps over 2 minutes."""
        line = LRCLine(time_seconds=125.125, text="Line three")
        assert line.format() == "[02:05.12] Line three"

    def test_format_exact_minute(self):
        """Test formatting exact minute boundaries."""
        line = LRCLine(time_seconds=60.0, text="Exactly one minute")
        assert line.format() == "[01:00.00] Exactly one minute"


class TestWhisperWord:
    """Tests for WhisperWord dataclass."""

    def test_whisper_word_creation(self):
        """Test creating a WhisperWord."""
        word = WhisperWord(word="test", start=1.0, end=1.5)
        assert word.word == "test"
        assert word.start == 1.0
        assert word.end == 1.5

    def test_whisper_word_multiple_words(self):
        """Test creating multiple WhisperWords."""
        words = [
            WhisperWord(word="hello", start=0.0, end=0.5),
            WhisperWord(word="world", start=0.6, end=1.1),
        ]
        assert len(words) == 2
        assert words[0].word == "hello"
        assert words[1].word == "world"


class TestBeatSnapping:
    """Tests for beat grid snapping functionality."""

    def test_snap_to_nearest_beat(self):
        """Test that timestamps snap to nearest beat."""
        generator = LRCGenerator(api_key="test-key")
        lines = [
            LRCLine(time_seconds=12.3, text="Line one"),
            LRCLine(time_seconds=18.7, text="Line two"),
        ]
        beats = [12.0, 12.5, 13.0, 18.0, 18.5, 19.0]

        snapped = generator._beat_snap(lines, beats)

        assert snapped[0].time_seconds == 12.5  # Closest to 12.3
        assert snapped[1].time_seconds == 18.5  # Closest to 18.7

    def test_snap_empty_beats_list(self):
        """Test that empty beats list returns original lines."""
        generator = LRCGenerator(api_key="test-key")
        lines = [LRCLine(time_seconds=12.5, text="Line one")]

        snapped = generator._beat_snap(lines, [])

        assert snapped[0].time_seconds == 12.5

    def test_snap_preserves_lyrics_text(self):
        """Test that beat snapping preserves lyrics text."""
        generator = LRCGenerator(api_key="test-key")
        lines = [LRCLine(time_seconds=10.1, text="Chinese lyrics here")]
        beats = [10.0, 10.2]

        snapped = generator._beat_snap(lines, beats)

        assert snapped[0].text == "Chinese lyrics here"


class TestLLMAlignment:
    """Tests for LLM alignment functionality."""

    @patch("stream_of_worship.ingestion.lrc_generator.openai")
    def test_llm_align_success(self, mock_openai):
        """Test successful LLM alignment."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps([
            {"time_seconds": 10.5, "text": "First line"},
            {"time_seconds": 15.2, "text": "Second line"},
        ])
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.OpenAI.return_value = mock_client

        generator = LRCGenerator(api_key="test-key")
        whisper_words = [
            WhisperWord(word="First", start=10.5, end=10.8),
            WhisperWord(word="line", start=10.9, end=11.2),
            WhisperWord(word="Second", start=15.2, end=15.5),
            WhisperWord(word="line", start=15.6, end=15.9),
        ]

        result = generator._llm_align("First line\nSecond line", whisper_words)

        assert len(result) == 2
        assert result[0].time_seconds == 10.5
        assert result[0].text == "First line"

    @patch("stream_of_worship.ingestion.lrc_generator.openai")
    def test_llm_align_retries_on_failure(self, mock_openai):
        """Test that LLM alignment retries on failure."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        # First two calls return invalid JSON, third succeeds
        mock_response.choices[0].message.content.side_effect = [
            "invalid json",
            "invalid json",
            json.dumps([{"time_seconds": 10.5, "text": "First line"}]),
        ]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.OpenAI.return_value = mock_client

        generator = LRCGenerator(api_key="test-key")
        whisper_words = [WhisperWord(word="test", start=10.5, end=10.8)]

        with pytest.raises(RuntimeError):
            generator._llm_align("Test lyrics", whisper_words, max_retries=3)

    @patch("stream_of_worship.ingestion.lrc_generator.openai")
    def test_llm_align_strips_markdown(self, mock_openai):
        """Test that markdown code blocks are stripped from response."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = """```json
[{"time_seconds": 10.5, "text": "First line"}]
```"""
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.OpenAI.return_value = mock_client

        generator = LRCGenerator(api_key="test-key")
        whisper_words = [WhisperWord(word="test", start=10.5, end=10.8)]

        result = generator._llm_align("Test lyrics", whisper_words)

        assert len(result) == 1
        assert result[0].text == "First line"


class TestLRCFileWriting:
    """Tests for LRC file writing."""

    def test_write_lrc_creates_directories(self, tmp_path):
        """Test that write_lrc creates parent directories."""
        generator = LRCGenerator(api_key="test-key")
        lines = [
            LRCLine(time_seconds=12.5, text="Line one"),
            LRCLine(time_seconds=18.2, text="Line two"),
        ]
        output_path = tmp_path / "nested" / "dir" / "test.lrc"

        generator._write_lrc(lines, output_path)

        assert output_path.exists()

    def test_write_lrc_content(self, tmp_path):
        """Test that LRC file content is correct."""
        generator = LRCGenerator(api_key="test-key")
        lines = [
            LRCLine(time_seconds=12.5, text="First line"),
            LRCLine(time_seconds=65.75, text="Second line"),
        ]
        output_path = tmp_path / "test.lrc"

        generator._write_lrc(lines, output_path)

        content = output_path.read_text(encoding="utf-8")
        assert "[00:12.50] First line" in content
        assert "[01:05.75] Second line" in content


class TestWhisperFormatting:
    """Tests for Whisper output formatting."""

    def test_format_whisper_groups_words(self):
        """Test that Whisper words are formatted in groups."""
        generator = LRCGenerator(api_key="test-key")
        words = [
            WhisperWord(word=f"word{i}", start=float(i), end=float(i) + 0.5)
            for i in range(25)
        ]

        result = generator._format_whisper_for_llm(words)

        # Should have empty lines between groups of 10
        lines = result.split("\n")
        assert "" in lines  # Empty line separator

    def test_format_whisper_includes_timestamps(self):
        """Test that formatted output includes timestamps."""
        generator = LRCGenerator(api_key="test-key")
        words = [WhisperWord(word="hello", start=1.5, end=2.0)]

        result = generator._format_whisper_for_llm(words)

        assert "[1.50-2.00s] hello" in result


class TestParseLRCFile:
    """Tests for LRC file parsing."""

    def test_parse_standard_lrc(self, tmp_path):
        """Test parsing a standard LRC file."""
        content = """[ti:Test Song]
[ar:Test Artist]
[offset:0]

[00:12.50]First line of lyrics
[00:18.20]Second line of lyrics
[01:05.75]Third line of lyrics
[01:30.00]Fourth line of lyrics
"""
        file_path = tmp_path / "test.lrc"
        file_path.write_text(content, encoding="utf-8")

        lines = parse_lrc_file(file_path)

        assert len(lines) == 4
        assert lines[0].time_seconds == 12.5
        assert lines[0].text == "First line of lyrics"
        assert lines[1].time_seconds == 18.2
        assert lines[1].text == "Second line of lyrics"

    def test_parse_skips_metadata(self, tmp_path):
        """Test that metadata lines are skipped."""
        content = """[ti:Test Song]
[ar:Test Artist]
[al:Cyber Worship]
[by:Test Composer]
"""
        file_path = tmp_path / "metadata_only.lrc"
        file_path.write_text(content, encoding="utf-8")

        lines = parse_lrc_file(file_path)

        assert len(lines) == 0

    def test_parse_raises_file_not_found(self):
        """Test that parsing raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            parse_lrc_file(Path("/nonexistent/file.lrc"))


class TestEndToEndWorkflow:
    """End-to-end integration tests with mocked dependencies."""

    @patch("stream_of_worship.ingestion.lrc_generator.whisper")
    @patch("stream_of_worship.ingestion.lrc_generator.openai")
    @patch("stream_of_worship.ingestion.lrc_generator.get_whisper_cache_path")
    def test_generate_lrc_success(self, mock_cache, mock_openai, mock_whisper, tmp_path):
        """Test successful end-to-end LRC generation."""
        mock_cache.return_value = tmp_path / "cache"

        # Mock Whisper model
        mock_model = MagicMock()
        mock_result = {
            "segments": [
                {
                    "words": [
                        {"word": "Hello", "start": 0.5, "end": 1.0},
                        {"word": "world", "start": 1.1, "end": 1.5},
                    ]
                }
            ]
        }
        mock_model.transcribe.return_value = mock_result
        mock_whisper.load_model.return_value = mock_model

        # Mock OpenAI client
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps([
            {"time_seconds": 0.5, "text": "Hello world"}
        ])
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.OpenAI.return_value = mock_client

        # Create a dummy audio file
        audio_path = tmp_path / "test.mp3"
        audio_path.write_bytes(b"dummy audio content")

        generator = LRCGenerator(api_key="test-key")
        output_path = tmp_path / "output.lrc"

        success = generator.generate(
            audio_path=audio_path,
            lyrics_text="Hello world",
            beats=[0.5, 1.0, 1.5],
            output_path=output_path,
        )

        assert success is True
        assert output_path.exists()

    @patch("stream_of_worship.ingestion.lrc_generator.whisper")
    @patch("stream_of_worship.ingestion.lrc_generator.openai")
    @patch("stream_of_worship.ingestion.lrc_generator.get_whisper_cache_path")
    def test_generate_lrc_with_progress_callback(self, mock_cache, mock_openai, mock_whisper, tmp_path):
        """Test that progress callback is called during generation."""
        mock_cache.return_value = tmp_path / "cache"

        mock_model = MagicMock()
        mock_result = {"segments": [{"words": [{"word": "test", "start": 0.5, "end": 1.0}]}]}
        mock_model.transcribe.return_value = mock_result
        mock_whisper.load_model.return_value = mock_model

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps([
            {"time_seconds": 0.5, "text": "test"}
        ])
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.OpenAI.return_value = mock_client

        audio_path = tmp_path / "test.mp3"
        audio_path.write_bytes(b"dummy audio content")

        generator = LRCGenerator(api_key="test-key")
        output_path = tmp_path / "output.lrc"

        progress_calls = []
        def progress_callback(msg, pct):
            progress_calls.append((msg, pct))

        generator.generate(
            audio_path=audio_path,
            lyrics_text="test",
            beats=[0.5],
            output_path=output_path,
            progress_callback=progress_callback,
        )

        assert len(progress_calls) > 0
        assert progress_calls[-1] == ("Done!", 1.0)

    @patch("stream_of_worship.ingestion.lrc_generator.whisper")
    @patch("stream_of_worship.ingestion.lrc_generator.openai")
    @patch("stream_of_worship.ingestion.lrc_generator.get_whisper_cache_path")
    def test_generate_lrc_handles_whisper_error(self, mock_cache, mock_openai, mock_whisper, tmp_path):
        """Test that generation handles Whisper errors gracefully."""
        mock_cache.return_value = tmp_path / "cache"

        mock_model = MagicMock()
        mock_model.transcribe.side_effect = Exception("Whisper error")
        mock_whisper.load_model.return_value = mock_model

        audio_path = tmp_path / "test.mp3"
        audio_path.write_bytes(b"dummy audio content")

        generator = LRCGenerator(api_key="test-key")
        output_path = tmp_path / "output.lrc"

        success = generator.generate(
            audio_path=audio_path,
            lyrics_text="test",
            beats=[],
            output_path=output_path,
        )

        assert success is False


class TestBatchGeneration:
    """Tests for batch LRC generation."""

    @patch("stream_of_worship.ingestion.lrc_generator.whisper")
    @patch("stream_of_worship.ingestion.lrc_generator.openai")
    @patch("stream_of_worship.ingestion.lrc_generator.get_whisper_cache_path")
    def test_batch_generate(self, mock_cache, mock_openai, mock_whisper, tmp_path):
        """Test batch generation for multiple songs."""
        mock_cache.return_value = tmp_path / "cache"

        mock_model = MagicMock()
        mock_result = {"segments": [{"words": [{"word": "test", "start": 0.5, "end": 1.0}]}]}
        mock_model.transcribe.return_value = mock_result
        mock_whisper.load_model.return_value = mock_model

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps([
            {"time_seconds": 0.5, "text": "test"}
        ])
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.OpenAI.return_value = mock_client

        # Create test audio files
        for i in range(3):
            audio_path = tmp_path / f"song{i}.mp3"
            audio_path.write_bytes(b"dummy audio content")

        songs = [
            (tmp_path / "song0.mp3", "lyrics 0", [0.5], tmp_path / "output0.lrc"),
            (tmp_path / "song1.mp3", "lyrics 1", [0.5], tmp_path / "output1.lrc"),
            (tmp_path / "song2.mp3", "lyrics 2", [0.5], tmp_path / "output2.lrc"),
        ]

        generator = LRCGenerator(api_key="test-key")
        success, failures, paths = generator.batch_generate(songs)

        assert success == 3
        assert failures == 0
        assert len(paths) == 3

    @patch("stream_of_worship.ingestion.lrc_generator.whisper")
    @patch("stream_of_worship.ingestion.lrc_generator.openai")
    @patch("stream_of_worship.ingestion.lrc_generator.get_whisper_cache_path")
    def test_batch_generate_stops_on_max_failures(self, mock_cache, mock_openai, mock_whisper, tmp_path):
        """Test that batch generation stops after max failures."""
        mock_cache.return_value = tmp_path / "cache"

        mock_model = MagicMock()
        mock_model.transcribe.side_effect = Exception("Whisper error")
        mock_whisper.load_model.return_value = mock_model

        songs = [
            (tmp_path / f"song{i}.mp3", f"lyrics {i}", [], tmp_path / f"output{i}.lrc")
            for i in range(10)
        ]

        # Create dummy audio files
        for audio_path, _, _, _ in songs:
            audio_path.write_bytes(b"dummy audio content")

        generator = LRCGenerator(api_key="test-key")
        success, failures, paths = generator.batch_generate(songs, max_failures=2)

        assert failures == 2
        assert len(paths) == 0
