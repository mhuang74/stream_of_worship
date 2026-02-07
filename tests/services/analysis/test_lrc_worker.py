"""Tests for LRC generation worker."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock whisper and openai before importing lrc module
sys.modules["whisper"] = MagicMock()
sys.modules["openai"] = MagicMock()

from sow_analysis.models import JobStatus, JobType, LrcJobRequest, LrcOptions
from sow_analysis.workers.lrc import (
    LLMAlignmentError,
    LLMConfigError,
    LRCLine,
    LRCWorkerError,
    WhisperTranscriptionError,
    WhisperWord,
    _build_alignment_prompt,
    _parse_llm_response,
    _run_whisper_transcription,
    _write_lrc,
    generate_lrc,
)
from sow_analysis.workers.queue import Job, JobQueue


class TestLRCLine:
    """Test LRCLine formatting."""

    def test_format_basic(self):
        """Test basic LRC line formatting."""
        line = LRCLine(time_seconds=5.5, text="Hello world")
        assert line.format() == "[00:05.50] Hello world"

    def test_format_minutes(self):
        """Test formatting with minutes."""
        line = LRCLine(time_seconds=125.75, text="Two minutes in")
        assert line.format() == "[02:05.75] Two minutes in"

    def test_format_zero(self):
        """Test formatting at time zero."""
        line = LRCLine(time_seconds=0.0, text="Start")
        assert line.format() == "[00:00.00] Start"

    def test_format_chinese(self):
        """Test formatting Chinese text."""
        line = LRCLine(time_seconds=10.25, text="這是中文歌詞")
        assert line.format() == "[00:10.25] 這是中文歌詞"

    def test_format_long_song(self):
        """Test formatting for long songs (>10 minutes)."""
        line = LRCLine(time_seconds=615.0, text="Ten minutes")
        assert line.format() == "[10:15.00] Ten minutes"


class TestWhisperWord:
    """Test WhisperWord dataclass."""

    def test_create_word(self):
        """Test creating a WhisperWord."""
        word = WhisperWord(word="hello", start=1.5, end=2.0)
        assert word.word == "hello"
        assert word.start == 1.5
        assert word.end == 2.0


class TestBuildAlignmentPrompt:
    """Test prompt building for LLM alignment."""

    def test_builds_prompt_with_lyrics(self):
        """Test prompt includes lyrics."""
        words = [WhisperWord("hello", 0.0, 0.5)]
        prompt = _build_alignment_prompt("Line 1\nLine 2", words)
        assert "Line 1" in prompt
        assert "Line 2" in prompt

    def test_builds_prompt_with_timestamps(self):
        """Test prompt includes word timestamps."""
        words = [
            WhisperWord("hello", 0.0, 0.5),
            WhisperWord("world", 0.6, 1.0),
        ]
        prompt = _build_alignment_prompt("Hello world", words)
        assert '"start": 0.0' in prompt
        assert '"end": 0.5' in prompt


class TestParseLLMResponse:
    """Test LLM response parsing."""

    def test_parse_valid_json(self):
        """Test parsing valid JSON response."""
        response = '[{"time_seconds": 0.5, "text": "Hello"}]'
        lines = _parse_llm_response(response)
        assert len(lines) == 1
        assert lines[0].time_seconds == 0.5
        assert lines[0].text == "Hello"

    def test_parse_with_code_block(self):
        """Test parsing response wrapped in code block."""
        response = '```json\n[{"time_seconds": 1.0, "text": "Test"}]\n```'
        lines = _parse_llm_response(response)
        assert len(lines) == 1
        assert lines[0].text == "Test"

    def test_parse_with_code_block_no_lang(self):
        """Test parsing response wrapped in code block without language."""
        response = '```\n[{"time_seconds": 1.0, "text": "Test"}]\n```'
        lines = _parse_llm_response(response)
        assert len(lines) == 1

    def test_parse_multiple_lines(self):
        """Test parsing multiple lyric lines."""
        response = """[
            {"time_seconds": 0.0, "text": "First"},
            {"time_seconds": 2.5, "text": "Second"},
            {"time_seconds": 5.0, "text": "Third"}
        ]"""
        lines = _parse_llm_response(response)
        assert len(lines) == 3

    def test_parse_invalid_json_raises(self):
        """Test that invalid JSON raises ValueError."""
        with pytest.raises(ValueError):
            _parse_llm_response("not json")

    def test_parse_missing_fields_raises(self):
        """Test that missing fields raises ValueError."""
        with pytest.raises(ValueError):
            _parse_llm_response('[{"time_seconds": 0.5}]')

    def test_parse_not_array_raises(self):
        """Test that non-array JSON raises ValueError."""
        with pytest.raises(ValueError):
            _parse_llm_response('{"time_seconds": 0.5, "text": "Hi"}')


class TestWriteLRC:
    """Test LRC file writing."""

    def test_write_basic(self):
        """Test writing LRC lines to file."""
        lines = [
            LRCLine(0.0, "First line"),
            LRCLine(2.5, "Second line"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.lrc"
            count = _write_lrc(lines, path)

            assert count == 2
            assert path.exists()
            content = path.read_text()
            assert "[00:00.00] First line" in content
            assert "[00:02.50] Second line" in content

    def test_write_creates_parent_dirs(self):
        """Test that writing creates parent directories."""
        lines = [LRCLine(0.0, "Test")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "subdir" / "nested" / "test.lrc"
            _write_lrc(lines, path)
            assert path.exists()

    def test_write_sorts_by_time(self):
        """Test that lines are sorted by time."""
        lines = [
            LRCLine(5.0, "Third"),
            LRCLine(0.0, "First"),
            LRCLine(2.5, "Second"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.lrc"
            _write_lrc(lines, path)

            content = path.read_text().strip().split("\n")
            assert "First" in content[0]
            assert "Second" in content[1]
            assert "Third" in content[2]


class TestRunWhisperTranscription:
    """Test Whisper transcription function."""

    @pytest.mark.asyncio
    async def test_transcription_returns_words(self):
        """Test that transcription returns WhisperWord list."""
        mock_result = {
            "segments": [
                {
                    "words": [
                        {"word": "hello", "start": 0.0, "end": 0.5},
                        {"word": "world", "start": 0.6, "end": 1.0},
                    ]
                }
            ]
        }

        mock_model = MagicMock()
        mock_model.transcribe.return_value = mock_result

        with patch("sow_analysis.workers.lrc.settings") as mock_settings:
            mock_settings.SOW_WHISPER_CACHE_DIR = Path("/tmp/whisper")
            with patch("whisper.load_model", return_value=mock_model):
                with tempfile.NamedTemporaryFile(suffix=".mp3") as tmp:
                    words = await _run_whisper_transcription(
                        Path(tmp.name), "large-v3", "zh", "cpu"
                    )

        assert len(words) == 2
        assert words[0].word == "hello"
        assert words[1].word == "world"

    @pytest.mark.asyncio
    async def test_transcription_no_words_raises(self):
        """Test that no words raises WhisperTranscriptionError."""
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"segments": []}

        with patch("sow_analysis.workers.lrc.settings") as mock_settings:
            mock_settings.SOW_WHISPER_CACHE_DIR = Path("/tmp/whisper")
            with patch("whisper.load_model", return_value=mock_model):
                with tempfile.NamedTemporaryFile(suffix=".mp3") as tmp:
                    with pytest.raises(WhisperTranscriptionError):
                        await _run_whisper_transcription(
                            Path(tmp.name), "large-v3", "zh", "cpu"
                        )


class TestLLMAlign:
    """Test LLM alignment function."""

    @pytest.mark.asyncio
    async def test_missing_api_key_raises(self):
        """Test that missing API key raises LLMConfigError."""
        from sow_analysis.workers.lrc import _llm_align

        words = [WhisperWord("hello", 0.0, 0.5)]

        with patch("sow_analysis.workers.lrc.settings") as mock_settings:
            mock_settings.SOW_LLM_API_KEY = ""
            with pytest.raises(LLMConfigError):
                await _llm_align("Hello", words, "gpt-4o-mini")

    @pytest.mark.asyncio
    async def test_successful_alignment(self):
        """Test successful LLM alignment."""
        from sow_analysis.workers.lrc import _llm_align

        words = [WhisperWord("hello", 0.0, 0.5)]
        response_json = '[{"time_seconds": 0.0, "text": "Hello"}]'

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = response_json

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        mock_openai_class = MagicMock(return_value=mock_client)

        with patch("sow_analysis.workers.lrc.settings") as mock_settings:
            mock_settings.SOW_LLM_API_KEY = "test-key"
            mock_settings.SOW_LLM_BASE_URL = "https://api.test.com"
            with patch.dict("sys.modules", {"openai": MagicMock(OpenAI=mock_openai_class)}):
                lines = await _llm_align("Hello", words, "gpt-4o-mini")

        assert len(lines) == 1
        assert lines[0].text == "Hello"

    @pytest.mark.asyncio
    async def test_alignment_retries_on_parse_error(self):
        """Test that alignment retries on parse error."""
        from sow_analysis.workers.lrc import _llm_align

        words = [WhisperWord("hello", 0.0, 0.5)]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        # First two calls return invalid JSON, third returns valid
        mock_response.choices[0].message.content = "invalid"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        mock_openai_class = MagicMock(return_value=mock_client)

        with patch("sow_analysis.workers.lrc.settings") as mock_settings:
            mock_settings.SOW_LLM_API_KEY = "test-key"
            mock_settings.SOW_LLM_BASE_URL = "https://api.test.com"
            with patch.dict("sys.modules", {"openai": MagicMock(OpenAI=mock_openai_class)}):
                with pytest.raises(LLMAlignmentError):
                    await _llm_align("Hello", words, "gpt-4o-mini", max_retries=2)

        # Should have tried twice
        assert mock_client.chat.completions.create.call_count == 2


class TestGenerateLRC:
    """Test full LRC generation pipeline."""

    @pytest.mark.asyncio
    async def test_generate_lrc_success(self):
        """Test successful LRC generation."""
        mock_whisper_result = {
            "segments": [
                {
                    "words": [
                        {"word": "測", "start": 0.0, "end": 0.2},
                        {"word": "試", "start": 0.3, "end": 0.5},
                    ]
                }
            ]
        }
        mock_model = MagicMock()
        mock_model.transcribe.return_value = mock_whisper_result

        llm_response = '[{"time_seconds": 0.0, "text": "測試歌詞"}]'
        mock_llm_response = MagicMock()
        mock_llm_response.choices = [MagicMock()]
        mock_llm_response.choices[0].message.content = llm_response

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_llm_response

        mock_openai_class = MagicMock(return_value=mock_client)

        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "audio.mp3"
            audio_path.write_bytes(b"fake audio")
            output_path = Path(tmp) / "output.lrc"

            with patch("sow_analysis.workers.lrc.settings") as mock_settings:
                mock_settings.SOW_WHISPER_CACHE_DIR = Path(tmp) / "whisper"
                mock_settings.SOW_WHISPER_DEVICE = "cpu"
                mock_settings.SOW_LLM_API_KEY = "test-key"
                mock_settings.SOW_LLM_BASE_URL = "https://api.test.com"

                with patch("whisper.load_model", return_value=mock_model):
                    with patch.dict(
                        "sys.modules", {"openai": MagicMock(OpenAI=mock_openai_class)}
                    ):
                        options = LrcOptions()
                        lrc_path, count = await generate_lrc(
                            audio_path, "測試歌詞", options, output_path
                        )

            assert lrc_path == output_path
            assert count == 1
            assert output_path.exists()
            content = output_path.read_text()
            assert "測試歌詞" in content


class TestLRCJobQueueProcessing:
    """Test LRC job processing in queue."""

    @pytest.fixture
    async def queue(self):
        """Create a test job queue."""
        with tempfile.TemporaryDirectory() as tmp:
            q = JobQueue(max_concurrent=1, cache_dir=Path(tmp))
            yield q
            q.stop()

    @pytest.mark.asyncio
    async def test_lrc_job_with_valid_request(self, queue):
        """Test LRC job processing with valid request."""
        request = LrcJobRequest(
            audio_url="s3://bucket/hash/audio.mp3",
            content_hash="abc123def456",
            lyrics_text="測試歌詞\n第二行",
        )

        job = await queue.submit(JobType.LRC, request)
        assert job.type == JobType.LRC

    @pytest.mark.asyncio
    async def test_lrc_job_uses_cache(self, queue):
        """Test LRC job returns cached result."""
        request = LrcJobRequest(
            audio_url="s3://bucket/hash/audio.mp3",
            content_hash="abc123def456",
            lyrics_text="測試歌詞",
        )

        # Pre-populate cache
        queue.cache_manager.save_lrc_result(
            request.content_hash,
            {"lrc_url": "s3://bucket/abc123def456/lyrics.lrc", "line_count": 5},
        )

        job = Job(
            id="job_test123",
            type=JobType.LRC,
            status=JobStatus.QUEUED,
            request=request,
        )

        await queue._process_lrc_job(job)

        assert job.status == JobStatus.COMPLETED
        assert job.stage == "cached"
        assert job.result.line_count == 5

    @pytest.mark.asyncio
    async def test_lrc_job_force_bypasses_cache(self, queue):
        """Test force option bypasses cache."""
        request = LrcJobRequest(
            audio_url="s3://bucket/hash/audio.mp3",
            content_hash="abc123def456",
            lyrics_text="測試歌詞",
            options=LrcOptions(force=True),
        )

        # Pre-populate cache
        queue.cache_manager.save_lrc_result(
            request.content_hash,
            {"lrc_url": "s3://bucket/abc123def456/lyrics.lrc", "line_count": 5},
        )

        job = Job(
            id="job_test123",
            type=JobType.LRC,
            status=JobStatus.QUEUED,
            request=request,
        )

        # This will fail because R2 is not configured, but it should NOT use cache
        await queue._process_lrc_job(job)

        # Should fail (no R2), but stage should not be "cached"
        assert job.stage != "cached"

    @pytest.mark.asyncio
    async def test_lrc_job_invalid_request_type(self, queue):
        """Test LRC job with invalid request type fails."""
        from sow_analysis.models import AnalyzeJobRequest

        request = AnalyzeJobRequest(
            audio_url="s3://bucket/hash/audio.mp3",
            content_hash="abc123",
        )

        job = Job(
            id="job_test123",
            type=JobType.LRC,
            status=JobStatus.QUEUED,
            request=request,
        )

        await queue._process_lrc_job(job)

        assert job.status == JobStatus.FAILED
        assert "Invalid request type" in job.error_message

    @pytest.mark.asyncio
    async def test_lrc_job_missing_llm_key_fails(self, queue):
        """Test LRC job fails gracefully when LLM key missing."""
        request = LrcJobRequest(
            audio_url="s3://bucket/hash/audio.mp3",
            content_hash="abc123def456",
            lyrics_text="測試歌詞",
        )

        # Mock R2 download
        mock_r2 = AsyncMock()
        mock_r2.download_audio = AsyncMock()
        mock_r2.check_exists = AsyncMock(return_value=False)
        queue.r2_client = mock_r2

        # Mock Whisper
        mock_whisper_result = {
            "segments": [{"words": [{"word": "test", "start": 0.0, "end": 0.5}]}]
        }
        mock_model = MagicMock()
        mock_model.transcribe.return_value = mock_whisper_result

        job = Job(
            id="job_test123",
            type=JobType.LRC,
            status=JobStatus.QUEUED,
            request=request,
        )

        with tempfile.TemporaryDirectory() as tmp:
            with patch("sow_analysis.workers.lrc.settings") as mock_settings:
                mock_settings.SOW_WHISPER_CACHE_DIR = Path(tmp)
                mock_settings.SOW_WHISPER_DEVICE = "cpu"
                mock_settings.SOW_LLM_API_KEY = ""  # Missing key
                mock_settings.SOW_R2_BUCKET = "test-bucket"

                with patch("whisper.load_model", return_value=mock_model):
                    await queue._process_lrc_job(job)

        assert job.status == JobStatus.FAILED
        assert "SOW_LLM_API_KEY" in job.error_message


class TestLRCOptions:
    """Test LrcOptions model."""

    def test_default_values(self):
        """Test default option values."""
        options = LrcOptions()
        assert options.whisper_model == "large-v3"
        assert options.llm_model == ""  # Empty by default, falls back to SOW_LLM_MODEL env var
        assert options.use_vocals_stem is True
        assert options.language == "zh"
        assert options.force is False

    def test_custom_values(self):
        """Test custom option values."""
        options = LrcOptions(
            whisper_model="medium",
            llm_model="anthropic/claude-3-haiku",
            use_vocals_stem=False,
            language="en",
            force=True,
        )
        assert options.whisper_model == "medium"
        assert options.llm_model == "anthropic/claude-3-haiku"
        assert options.use_vocals_stem is False
        assert options.language == "en"
        assert options.force is True
