"""Tests for YouTube transcript-based LRC generation."""

import pytest

from sow_analysis.workers.youtube_transcript import (
    build_correction_prompt,
    extract_video_id,
    parse_lrc_response,
)


class TestExtractVideoId:
    """Tests for extract_video_id()."""

    def test_standard_url(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_short_url(self):
        url = "https://youtu.be/dQw4w9WgXcQ"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_url_with_extra_params(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=120&list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_short_url_with_params(self):
        url = "https://youtu.be/dQw4w9WgXcQ?t=30"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_invalid_url(self):
        assert extract_video_id("https://example.com") is None

    def test_empty_string(self):
        assert extract_video_id("") is None

    def test_no_v_param(self):
        url = "https://www.youtube.com/watch?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
        assert extract_video_id(url) is None


class TestBuildCorrectionPrompt:
    """Tests for build_correction_prompt()."""

    def test_produces_valid_prompt(self):
        transcript_text = "00:15.00\nI want to see\n\n00:20.00\nYour glory\n"
        lyrics = ["我要看見", "祢的榮耀"]

        prompt = build_correction_prompt(transcript_text, lyrics)

        assert "我要看見" in prompt
        assert "祢的榮耀" in prompt
        assert "I want to see" in prompt
        assert "Your glory" in prompt
        assert "[mm:ss.xx]" in prompt

    def test_empty_lyrics(self):
        prompt = build_correction_prompt("00:00.00\nHello\n", [])
        assert "Hello" in prompt

    def test_contains_rules(self):
        prompt = build_correction_prompt("00:00.00\ntest\n", ["測試"])
        assert "Rules" in prompt
        assert "timecodes" in prompt


class TestParseLrcResponse:
    """Tests for parse_lrc_response()."""

    def test_valid_lrc_lines(self):
        response = "[00:15.00] 我要看見\n[00:20.50] 祢的榮耀\n"
        lines = parse_lrc_response(response)
        assert len(lines) == 2
        assert lines[0].time_seconds == 15.0
        assert lines[0].text == "我要看見"
        assert lines[1].time_seconds == 20.5
        assert lines[1].text == "祢的榮耀"

    def test_filters_non_lrc_lines(self):
        response = "Here is the corrected LRC:\n[00:15.00] 我要看見\nSome commentary\n[00:20.50] 祢的榮耀\n"
        lines = parse_lrc_response(response)
        assert len(lines) == 2

    def test_empty_response_raises(self):
        with pytest.raises(ValueError, match="No valid LRC lines"):
            parse_lrc_response("No LRC content here")

    def test_time_calculation(self):
        response = "[02:30.00] 測試"
        lines = parse_lrc_response(response)
        assert lines[0].time_seconds == 150.0


class TestGenerateLrcFallback:
    """Tests for generate_lrc() YouTube-first with Whisper fallback."""

    @pytest.mark.asyncio
    async def test_no_youtube_url_skips_youtube_path(self, tmp_path):
        """When youtube_url is None, YouTube path is skipped entirely."""
        from unittest.mock import AsyncMock, patch

        from sow_analysis.models import LrcOptions
        from sow_analysis.workers.lrc import LRCLine, generate_lrc

        audio_path = tmp_path / "test.mp3"
        audio_path.write_bytes(b"fake audio data")

        mock_phrases = [
            type("WhisperPhrase", (), {"text": "測試", "start": 0.0, "end": 1.0})()
        ]

        mock_lrc_lines = [LRCLine(time_seconds=0.0, text="測試")]

        with (
            patch(
                "sow_analysis.workers.lrc._run_whisper_transcription",
                new_callable=AsyncMock,
                return_value=mock_phrases,
            ) as mock_whisper,
            patch(
                "sow_analysis.workers.lrc._llm_align",
                new_callable=AsyncMock,
                return_value=mock_lrc_lines,
            ),
        ):
            path, count, phrases = await generate_lrc(
                audio_path,
                "測試",
                LrcOptions(),
                youtube_url=None,
            )

            mock_whisper.assert_called_once()
            assert count == 1

    @pytest.mark.asyncio
    async def test_youtube_failure_falls_back_to_whisper(self, tmp_path):
        """When YouTube transcript fails, falls back to Whisper path."""
        from unittest.mock import AsyncMock, patch

        from sow_analysis.models import LrcOptions
        from sow_analysis.workers.lrc import LRCLine, generate_lrc
        from sow_analysis.workers.youtube_transcript import YouTubeTranscriptError

        audio_path = tmp_path / "test.mp3"
        audio_path.write_bytes(b"fake audio data")

        mock_phrases = [
            type("WhisperPhrase", (), {"text": "測試", "start": 0.0, "end": 1.0})()
        ]
        mock_lrc_lines = [LRCLine(time_seconds=0.0, text="測試")]

        with (
            patch(
                "sow_analysis.workers.youtube_transcript.youtube_transcript_to_lrc",
                new_callable=AsyncMock,
                side_effect=YouTubeTranscriptError("No transcript available"),
            ),
            patch(
                "sow_analysis.workers.lrc._run_whisper_transcription",
                new_callable=AsyncMock,
                return_value=mock_phrases,
            ) as mock_whisper,
            patch(
                "sow_analysis.workers.lrc._llm_align",
                new_callable=AsyncMock,
                return_value=mock_lrc_lines,
            ),
        ):
            path, count, phrases = await generate_lrc(
                audio_path,
                "測試",
                LrcOptions(),
                youtube_url="https://www.youtube.com/watch?v=test123",
            )

            # Whisper fallback should have been called
            mock_whisper.assert_called_once()
            assert count == 1

    @pytest.mark.asyncio
    async def test_youtube_success_skips_whisper(self, tmp_path):
        """When YouTube transcript succeeds, Whisper is not called."""
        from unittest.mock import AsyncMock, patch

        from sow_analysis.models import LrcOptions
        from sow_analysis.workers.lrc import LRCLine, generate_lrc

        audio_path = tmp_path / "test.mp3"
        audio_path.write_bytes(b"fake audio data")

        mock_lrc_lines = [
            LRCLine(time_seconds=15.0, text="我要看見"),
            LRCLine(time_seconds=20.0, text="祢的榮耀"),
        ]

        with (
            patch(
                "sow_analysis.workers.youtube_transcript.youtube_transcript_to_lrc",
                new_callable=AsyncMock,
                return_value=mock_lrc_lines,
            ),
            patch(
                "sow_analysis.workers.lrc._run_whisper_transcription",
                new_callable=AsyncMock,
            ) as mock_whisper,
        ):
            path, count, phrases = await generate_lrc(
                audio_path,
                "我要看見\n祢的榮耀",
                LrcOptions(),
                youtube_url="https://www.youtube.com/watch?v=test123",
            )

            # Whisper should NOT have been called
            mock_whisper.assert_not_called()
            assert count == 2
            # YouTube path returns empty phrases list
            assert phrases == []
