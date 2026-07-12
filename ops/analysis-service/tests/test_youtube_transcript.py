"""Tests for YouTube transcript-based LRC generation."""

import asyncio
import time

import pytest

from sow_analysis.workers.youtube_transcript import (
    DEFAULT_LANGUAGES,
    EN_LANG_CODES,
    ZH_LANG_CODES,
    _build_proxy_config,
    _find_best_transcript,
    _is_rate_limited_error,
    _is_transient_connection_error,
    _rate_limiter,
    _YouTubeRateLimiter,
    build_correction_prompt,
    extract_video_id,
    fetch_youtube_transcript,
    language_preference_codes,
    parse_lrc_response,
    RotatingProxyConfig,
    YouTubeRateLimitedError,
    YouTubeTranscriptError,
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

    def test_english_prompt_preserves_official_casing(self):
        prompt = build_correction_prompt("00:00.00\nill sing\n", ["I'll Sing"], language="en")
        assert "English worship songs" in prompt
        assert "Preserve casing" in prompt
        assert "I'll Sing" in prompt


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

        mock_phrases = [type("WhisperPhrase", (), {"text": "測試", "start": 0.0, "end": 1.0})()]

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

        mock_phrases = [type("WhisperPhrase", (), {"text": "測試", "start": 0.0, "end": 1.0})()]
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

            mock_whisper.assert_not_called()
            assert count == 2
            assert phrases == []


class TestDefaultLanguages:
    """Tests for the expanded default language code list."""

    def test_includes_zh_tw(self):
        assert "zh-TW" in DEFAULT_LANGUAGES

    def test_includes_zh_cn(self):
        assert "zh-CN" in DEFAULT_LANGUAGES

    def test_includes_zh_hant(self):
        assert "zh-Hant" in DEFAULT_LANGUAGES

    def test_includes_zh_hans(self):
        assert "zh-Hans" in DEFAULT_LANGUAGES

    def test_zh_codes_before_en(self):
        zh_indices = [DEFAULT_LANGUAGES.index(c) for c in ZH_LANG_CODES if c in DEFAULT_LANGUAGES]
        en_indices = [DEFAULT_LANGUAGES.index(c) for c in DEFAULT_LANGUAGES if c.startswith("en")]
        assert max(zh_indices) < min(en_indices)

    def test_en_preference_codes_put_en_first(self):
        languages = language_preference_codes("en")
        assert languages[: len(EN_LANG_CODES)] == EN_LANG_CODES
        assert languages[-len(ZH_LANG_CODES) :] == ZH_LANG_CODES

    def test_zh_preference_codes_put_zh_first(self):
        languages = language_preference_codes("zh")
        assert languages[: len(ZH_LANG_CODES)] == ZH_LANG_CODES
        assert languages[-len(EN_LANG_CODES) :] == EN_LANG_CODES


class TestFindBestTranscript:
    """Tests for _find_best_transcript()."""

    @staticmethod
    def _make_transcript(language_code, language="", is_generated=False):
        return type(
            "Transcript",
            (),
            {
                "language_code": language_code,
                "language": language,
                "is_generated": is_generated,
            },
        )()

    def test_prefers_manual_zh_over_generated_zh(self):
        manual = self._make_transcript("zh-TW", "Chinese (Taiwan)", is_generated=False)
        generated = self._make_transcript("zh-TW", "Chinese (Taiwan)", is_generated=True)
        result = _find_best_transcript([generated, manual])
        assert result is manual

    def test_english_language_prefers_english_over_chinese(self):
        zh_manual = self._make_transcript("zh-TW", "Chinese (Taiwan)", is_generated=False)
        en_manual = self._make_transcript("en", "English", is_generated=False)
        result = _find_best_transcript([zh_manual, en_manual], language="en")
        assert result is en_manual

    def test_english_language_falls_back_to_chinese(self):
        zh_manual = self._make_transcript("zh-TW", "Chinese (Taiwan)", is_generated=False)
        result = _find_best_transcript([zh_manual], language="en")
        assert result is zh_manual

    def test_prefers_zh_over_en(self):
        zh_gen = self._make_transcript("zh-CN", "Chinese (China)", is_generated=True)
        en_manual = self._make_transcript("en", "English", is_generated=False)
        result = _find_best_transcript([en_manual, zh_gen])
        assert result is zh_gen

    def test_prefers_manual_en_over_generated_en(self):
        manual = self._make_transcript("en", "English", is_generated=False)
        generated = self._make_transcript("en", "English", is_generated=True)
        result = _find_best_transcript([generated, manual])
        assert result is manual

    def test_returns_generated_zh_when_no_manual_zh(self):
        generated = self._make_transcript("zh-HK", "Chinese (Hong Kong)", is_generated=True)
        en_manual = self._make_transcript("en", "English", is_generated=False)
        result = _find_best_transcript([en_manual, generated])
        assert result is generated

    def test_returns_generated_en_when_only_en_available(self):
        generated = self._make_transcript("en", "English", is_generated=True)
        result = _find_best_transcript([generated])
        assert result is generated

    def test_returns_none_when_no_transcripts(self):
        result = _find_best_transcript([])
        assert result is None

    def test_returns_none_for_unsupported_language(self):
        ja = self._make_transcript("ja", "Japanese", is_generated=False)
        result = _find_best_transcript([ja])
        assert result is None

    def test_handles_zh_prefix_codes(self):
        zh_unknown = self._make_transcript("zh-SG", "Chinese (Singapore)", is_generated=False)
        result = _find_best_transcript([zh_unknown])
        assert result is zh_unknown


class TestFetchYoutubeTranscript:
    """Tests for fetch_youtube_transcript() with two-phase fallback."""

    @pytest.mark.asyncio
    async def test_direct_fetch_succeeds(self):
        from unittest.mock import patch

        mock_snippet = type("Snippet", (), {"text": "測試", "start": 0.0})()
        mock_transcript = [mock_snippet]

        with patch("youtube_transcript_api.YouTubeTranscriptApi") as MockApi:
            mock_api = MockApi.return_value
            mock_api.fetch.return_value = mock_transcript
            result = await fetch_youtube_transcript("testVideoId")

        assert result == mock_transcript
        mock_api.fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_to_list_when_direct_fails(self):
        from unittest.mock import patch

        mock_snippet = type("Snippet", (), {"text": "我要看見", "start": 15.0})()
        mock_fetched = [mock_snippet]

        mock_transcript_obj = type(
            "Transcript",
            (),
            {
                "language_code": "zh-TW",
                "language": "Chinese (Taiwan)",
                "is_generated": False,
                "fetch": lambda s: mock_fetched,
            },
        )()

        mock_transcript_list = [mock_transcript_obj]

        with patch("youtube_transcript_api.YouTubeTranscriptApi") as MockApi:
            mock_api = MockApi.return_value
            mock_api.fetch.side_effect = Exception("No transcripts found")
            mock_api.list.return_value = mock_transcript_list

            result = await fetch_youtube_transcript("testVideoId")

        assert len(result) == 1
        mock_api.list.assert_called_once_with("testVideoId")

    @pytest.mark.asyncio
    async def test_raises_when_no_transcript_available(self):
        from unittest.mock import patch

        from sow_analysis.workers.youtube_transcript import YouTubeTranscriptError

        with patch("youtube_transcript_api.YouTubeTranscriptApi") as MockApi:
            mock_api = MockApi.return_value
            mock_api.fetch.side_effect = Exception("No transcripts found")
            mock_api.list.return_value = []

            with pytest.raises(YouTubeTranscriptError, match="No suitable transcript"):
                await fetch_youtube_transcript("testVideoId")

    @pytest.mark.asyncio
    async def test_prefers_manual_zh_tw_via_list_fallback(self):
        from unittest.mock import patch

        mock_snippet = type("Snippet", (), {"text": "測試", "start": 0.0})()

        zh_tw_manual = type(
            "Transcript",
            (),
            {
                "language_code": "zh-TW",
                "language": "Chinese (Taiwan)",
                "is_generated": False,
                "fetch": lambda s: [mock_snippet],
            },
        )()
        en_manual = type(
            "Transcript",
            (),
            {
                "language_code": "en",
                "language": "English",
                "is_generated": False,
                "fetch": lambda s: [mock_snippet],
            },
        )()

        with patch("youtube_transcript_api.YouTubeTranscriptApi") as MockApi:
            mock_api = MockApi.return_value
            mock_api.fetch.side_effect = Exception("No transcripts found")
            mock_api.list.return_value = [en_manual, zh_tw_manual]

            result = await fetch_youtube_transcript("testVideoId")

        assert len(result) == 1


class TestRotatingProxyConfig:
    """Tests for RotatingProxyConfig."""

    def test_prevent_keeping_connections_alive_is_true(self):
        """RotatingProxyConfig always returns True for prevent_keeping_connections_alive."""
        config = RotatingProxyConfig(
            http_url="http://proxy:8080",
            https_url="http://proxy:8080",
            retries_when_blocked=3,
        )
        assert config.prevent_keeping_connections_alive is True

    def test_retries_when_blocked_returns_configured_value(self):
        """RotatingProxyConfig returns the configured retries_when_blocked value."""
        config = RotatingProxyConfig(
            http_url="http://proxy:8080",
            https_url="http://proxy:8080",
            retries_when_blocked=5,
        )
        assert config.retries_when_blocked == 5

    def test_to_requests_dict_returns_proxy_urls(self):
        """RotatingProxyConfig.to_requests_dict() returns the proxy URLs."""
        config = RotatingProxyConfig(
            http_url="http://proxy:8080",
            https_url="https://proxy:8443",
            retries_when_blocked=3,
        )
        result = config.to_requests_dict()
        assert result["http"] == "http://proxy:8080"
        assert result["https"] == "https://proxy:8443"

    def test_uses_https_url_for_http_when_only_https_provided(self):
        """When only https_url is provided, it's used for both http and https."""
        config = RotatingProxyConfig(
            http_url=None,
            https_url="https://proxy:8443",
            retries_when_blocked=3,
        )
        result = config.to_requests_dict()
        assert result["http"] == "https://proxy:8443"
        assert result["https"] == "https://proxy:8443"


class TestBuildProxyConfig:
    """Tests for _build_proxy_config()."""

    @pytest.mark.asyncio
    async def test_returns_none_when_proxy_not_configured(self):
        """When SOW_YOUTUBE_PROXY is empty, returns None."""
        from unittest.mock import patch

        with patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings:
            mock_settings.SOW_YOUTUBE_PROXY = ""
            mock_settings.SOW_YOUTUBE_PROXY_RETRIES = 3

            result = _build_proxy_config()

            assert result is None

    @pytest.mark.asyncio
    async def test_returns_rotating_proxy_config_when_proxy_configured(self):
        """When SOW_YOUTUBE_PROXY is set, returns RotatingProxyConfig."""
        from unittest.mock import patch

        with patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings:
            mock_settings.SOW_YOUTUBE_PROXY = "http://proxy:8080"
            mock_settings.SOW_YOUTUBE_PROXY_RETRIES = 5

            result = _build_proxy_config()

            assert result is not None
            assert isinstance(result, RotatingProxyConfig)
            assert result.retries_when_blocked == 5

    @pytest.mark.asyncio
    async def test_uses_default_retries_when_not_configured(self):
        """Uses default retries when SOW_YOUTUBE_PROXY_RETRIES is not set."""
        from unittest.mock import patch

        with patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings:
            mock_settings.SOW_YOUTUBE_PROXY = "http://proxy:8080"
            mock_settings.SOW_YOUTUBE_PROXY_RETRIES = 3

            result = _build_proxy_config()

            assert result.retries_when_blocked == 3


class TestFetchYoutubeTranscriptWithProxy:
    """Tests for fetch_youtube_transcript() with proxy configuration."""

    @pytest.mark.asyncio
    async def test_passes_proxy_config_to_api_when_set(self):
        """When proxy is configured, passes proxy_config to YouTubeTranscriptApi."""
        from unittest.mock import patch

        mock_snippet = type("Snippet", (), {"text": "測試", "start": 0.0})()
        mock_transcript = [mock_snippet]

        _rate_limiter._last_request_time = 0.0
        _rate_limiter._consecutive_429_count = 0
        _rate_limiter._circuit_open_until = 0.0

        with (
            patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings,
            patch("youtube_transcript_api.YouTubeTranscriptApi") as MockApi,
        ):
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_PROXY = "http://proxy:8080"
            mock_settings.SOW_YOUTUBE_PROXY_RETRIES = 3
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT = 1
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.1
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            mock_api = MockApi.return_value
            mock_api.fetch.return_value = mock_transcript

            result = await fetch_youtube_transcript("testVideoId")

            assert result == mock_transcript
            MockApi.assert_called_once()
            call_kwargs = MockApi.call_args[1]
            assert "proxy_config" in call_kwargs
            assert call_kwargs["proxy_config"] is not None
            assert isinstance(call_kwargs["proxy_config"], RotatingProxyConfig)

    @pytest.mark.asyncio
    async def test_passes_none_to_api_when_proxy_not_set(self):
        """When proxy is not configured, passes None as proxy_config to YouTubeTranscriptApi."""
        from unittest.mock import patch

        mock_snippet = type("Snippet", (), {"text": "測試", "start": 0.0})()
        mock_transcript = [mock_snippet]

        _rate_limiter._last_request_time = 0.0
        _rate_limiter._consecutive_429_count = 0
        _rate_limiter._circuit_open_until = 0.0

        with (
            patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings,
            patch("youtube_transcript_api.YouTubeTranscriptApi") as MockApi,
        ):
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_PROXY = ""
            mock_settings.SOW_YOUTUBE_PROXY_RETRIES = 3
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT = 1
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.1
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            mock_api = MockApi.return_value
            mock_api.fetch.return_value = mock_transcript

            result = await fetch_youtube_transcript("testVideoId")

            assert result == mock_transcript
            MockApi.assert_called_once()
            call_kwargs = MockApi.call_args[1]
            assert "proxy_config" in call_kwargs
            assert call_kwargs["proxy_config"] is None


class TestYouTubeRateLimiter:
    """Tests for _YouTubeRateLimiter."""

    def _fresh_rate_limiter(self):
        """Create a fresh _YouTubeRateLimiter instance for test isolation."""
        rl = _YouTubeRateLimiter()
        rl._semaphore = None
        rl._interval_lock = None
        rl._state_lock = None
        rl._last_request_time = 0.0
        rl._consecutive_429_count = 0
        rl._circuit_open_until = 0.0
        return rl

    @pytest.mark.asyncio
    async def test_min_interval_enforced(self):
        """Two rapid calls → second call waits the min interval."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        with patch(
            "sow_analysis.workers.youtube_transcript.settings"
        ) as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 3.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.1
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            sleep_calls = []

            original_sleep = asyncio.sleep

            async def mock_sleep(duration):
                sleep_calls.append(duration)
                await original_sleep(0)

            with patch(
                "sow_analysis.workers.youtube_transcript.asyncio.sleep",
                side_effect=mock_sleep,
            ):
                await rl.call(lambda: "result1", description="call1")
                await rl.call(lambda: "result2", description="call2")

            # Second call should have slept for ~min_interval
            assert len(sleep_calls) > 0
            assert sleep_calls[0] >= 2.9

    @pytest.mark.asyncio
    async def test_retries_on_429(self):
        """fn raises 429 on first 2 attempts, succeeds on 3rd → retried, result returned."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("too many 429 error responses")
            return "success"

        with patch(
            "sow_analysis.workers.youtube_transcript.settings"
        ) as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 3
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.01
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            result = await rl.call(fn, description="retry test")

        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_threshold(self):
        """N consecutive 429s (N=threshold) → circuit opens."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        def fn():
            raise Exception("too many 429 error responses")

        with patch(
            "sow_analysis.workers.youtube_transcript.settings"
        ) as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.01
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 3
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            for _ in range(3):
                with pytest.raises(YouTubeRateLimitedError):
                    await rl.call(fn, description="circuit test")

            assert rl._consecutive_429_count >= 3
            assert await rl._is_circuit_open()

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_calls_when_open(self):
        """When circuit is open, call() raises YouTubeTranscriptError without calling fn."""
        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()
        rl._circuit_open_until = time.monotonic() + 9999

        fn_called = False

        def fn():
            nonlocal fn_called
            fn_called = True
            return "should not reach"

        with pytest.raises(YouTubeRateLimitedError, match="circuit breaker is open"):
            await rl.call(fn, description="blocked test")

        assert fn_called is False

    @pytest.mark.asyncio
    async def test_circuit_breaker_closes_after_cooldown(self):
        """After cooldown period elapses, calls proceed again."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        # Set circuit open in the past
        rl._circuit_open_until = time.monotonic() - 1

        with patch(
            "sow_analysis.workers.youtube_transcript.settings"
        ) as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.01
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            result = await rl.call(lambda: "recovered", description="recovery test")

        assert result == "recovered"
        assert not await rl._is_circuit_open()

    @pytest.mark.asyncio
    async def test_success_resets_429_count(self):
        """After some 429s, a successful call resets _consecutive_429_count to 0."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()
        rl._consecutive_429_count = 3

        with patch(
            "sow_analysis.workers.youtube_transcript.settings"
        ) as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.01
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            result = await rl.call(lambda: "ok", description="reset test")

        assert result == "ok"
        assert rl._consecutive_429_count == 0

    @pytest.mark.asyncio
    async def test_non_429_error_not_retried(self):
        """fn raises non-429 error → no retry, exception propagates immediately."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            raise Exception("Subtitles are disabled for this video")

        with patch(
            "sow_analysis.workers.youtube_transcript.settings"
        ) as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 3
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.01
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            with pytest.raises(Exception, match="Subtitles are disabled"):
                await rl.call(fn, description="non-429 test")

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_concurrency_semaphore_limits_parallel(self):
        """Multiple concurrent call() invocations → only max_concurrent run at once."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        current_concurrent = 0
        max_observed = 0

        def fn():
            nonlocal current_concurrent, max_observed
            current_concurrent += 1
            max_observed = max(max_observed, current_concurrent)
            import time as _time

            _time.sleep(0.05)
            current_concurrent -= 1
            return "done"

        with patch(
            "sow_analysis.workers.youtube_transcript.settings"
        ) as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.01
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            # Create a semaphore with max_concurrent=1
            rl._semaphore = asyncio.Semaphore(1)

            tasks = [rl.call(fn, description=f"concurrent-{i}") for i in range(5)]
            results = await asyncio.gather(*tasks)

        assert all(r == "done" for r in results)
        assert max_observed == 1

    @pytest.mark.asyncio
    async def test_max_concurrent_zero_disables_semaphore(self):
        """With MAX_CONCURRENT=0, calls proceed without semaphore but min-interval still enforced."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()

        with patch(
            "sow_analysis.workers.youtube_transcript.settings"
        ) as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT = 0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.01
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            rl._ensure_initialized()

            assert rl._semaphore is None

            result = await rl.call(lambda: "no semaphore", description="zero test")

        assert result == "no semaphore"

    def test_status_code_429_detected(self):
        """Exception with .status_code = 429 is detected as rate-limited."""

        class FakeResponseError(Exception):
            def __init__(self):
                self.status_code = 429
                super().__init__("Too Many Requests")

        assert _is_rate_limited_error(FakeResponseError()) is True

    def test_string_429_detected(self):
        """Exception with '429' in message is detected as rate-limited."""
        assert _is_rate_limited_error(Exception("too many 429 error responses")) is True

    def test_non_429_not_detected(self):
        """Non-429 exception is not detected as rate-limited."""
        assert _is_rate_limited_error(Exception("Subtitles are disabled")) is False


class TestLLMCorrectRateLimitRetry:
    """Tests for _llm_correct() 429 and 5xx retry behavior."""

    @pytest.mark.asyncio
    async def test_llm_correct_retries_on_429(self):
        """Mock OpenAI client raises 429 twice, succeeds third → _llm_correct returns result."""
        from unittest.mock import MagicMock, patch

        from sow_analysis.workers.youtube_transcript import _llm_correct

        call_count = 0

        class FakeRateLimitError(Exception):
            def __init__(self):
                self.status_code = 429
                super().__init__("Rate limit exceeded")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "[00:15.00] 我要看見"

        mock_client = MagicMock()

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise FakeRateLimitError()
            return mock_response

        mock_client.chat.completions.create.side_effect = side_effect
        mock_openai_class = MagicMock(return_value=mock_client)

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with (
            patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings,
            patch("sow_analysis.workers.llm_rate_limit.settings") as mock_rl_settings,
            patch.dict("sys.modules", {"openai": MagicMock(OpenAI=mock_openai_class)}),
            patch("sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep),
        ):
            mock_settings.SOW_LLM_API_KEY = "test-key"
            mock_settings.SOW_LLM_BASE_URL = "https://api.test.com"
            mock_settings.SOW_LLM_MODEL = "test-model"
            mock_settings.SOW_LLM_MAX_CONCURRENT = 0  # disable semaphore
            mock_rl_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_rl_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0
            mock_rl_settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES = 8
            mock_rl_settings.SOW_LLM_RATE_LIMIT_BASE_DELAY = 0.01
            mock_rl_settings.SOW_LLM_RATE_LIMIT_MAX_DELAY = 0.1
            mock_rl_settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 300

            result = await _llm_correct("test prompt", "test-model")

        assert result == "[00:15.00] 我要看見"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_llm_correct_retries_on_524_then_succeeds(self):
        """Mock OpenAI client raises 524 on first call, succeeds on second."""
        from unittest.mock import MagicMock, patch

        from sow_analysis.workers.youtube_transcript import _llm_correct

        class Fake524Error(Exception):
            def __init__(self):
                self.status_code = 524
                super().__init__("Cloudflare timeout")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "[00:15.00] 我要看見"

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Fake524Error()
            return mock_response

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = side_effect
        mock_openai_class = MagicMock(return_value=mock_client)

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with (
            patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings,
            patch("sow_analysis.workers.llm_rate_limit.settings") as mock_rl_settings,
            patch.dict("sys.modules", {"openai": MagicMock(OpenAI=mock_openai_class)}),
            patch("sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep),
        ):
            mock_settings.SOW_LLM_API_KEY = "test-key"
            mock_settings.SOW_LLM_BASE_URL = "https://api.test.com"
            mock_settings.SOW_LLM_MODEL = "test-model"
            mock_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_rl_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_rl_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0
            mock_rl_settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES = 8
            mock_rl_settings.SOW_LLM_RATE_LIMIT_BASE_DELAY = 0.01
            mock_rl_settings.SOW_LLM_RATE_LIMIT_MAX_DELAY = 0.1
            mock_rl_settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 300

            result = await _llm_correct("test prompt", "test-model")

        assert result == "[00:15.00] 我要看見"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_llm_correct_429_exhausts_retries(self):
        """Mock OpenAI client always raises 429 → YouTubeTranscriptError raised."""
        from unittest.mock import MagicMock, patch

        from sow_analysis.workers.youtube_transcript import _llm_correct

        class FakeRateLimitError(Exception):
            def __init__(self):
                self.status_code = 429
                super().__init__("Rate limit exceeded")

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = FakeRateLimitError()
        mock_openai_class = MagicMock(return_value=mock_client)

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with (
            patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings,
            patch("sow_analysis.workers.llm_rate_limit.settings") as mock_rl_settings,
            patch.dict("sys.modules", {"openai": MagicMock(OpenAI=mock_openai_class)}),
            patch("sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep),
        ):
            mock_settings.SOW_LLM_API_KEY = "test-key"
            mock_settings.SOW_LLM_BASE_URL = "https://api.test.com"
            mock_settings.SOW_LLM_MODEL = "test-model"
            mock_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_rl_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_rl_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0
            mock_rl_settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES = 3
            mock_rl_settings.SOW_LLM_RATE_LIMIT_BASE_DELAY = 0.01
            mock_rl_settings.SOW_LLM_RATE_LIMIT_MAX_DELAY = 0.1
            mock_rl_settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 300

            with pytest.raises(YouTubeTranscriptError, match="rate-limit retries"):
                await _llm_correct("test prompt", "test-model")

        assert mock_client.chat.completions.create.call_count == 3

    @pytest.mark.asyncio
    async def test_llm_correct_524_exhausts_retries(self):
        """Mock OpenAI client always raises 524 → YouTubeTranscriptError with 'transient-error retries'."""
        from unittest.mock import MagicMock, patch

        from sow_analysis.workers.youtube_transcript import _llm_correct

        class Fake524Error(Exception):
            def __init__(self):
                self.status_code = 524
                super().__init__("Cloudflare timeout")

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Fake524Error()
        mock_openai_class = MagicMock(return_value=mock_client)

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with (
            patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings,
            patch("sow_analysis.workers.llm_rate_limit.settings") as mock_rl_settings,
            patch.dict("sys.modules", {"openai": MagicMock(OpenAI=mock_openai_class)}),
            patch("sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep),
        ):
            mock_settings.SOW_LLM_API_KEY = "test-key"
            mock_settings.SOW_LLM_BASE_URL = "https://api.test.com"
            mock_settings.SOW_LLM_MODEL = "test-model"
            mock_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_rl_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_rl_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0
            mock_rl_settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES = 3
            mock_rl_settings.SOW_LLM_RATE_LIMIT_BASE_DELAY = 0.01
            mock_rl_settings.SOW_LLM_RATE_LIMIT_MAX_DELAY = 0.1
            mock_rl_settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 300

            with pytest.raises(YouTubeTranscriptError, match="transient-error retries"):
                await _llm_correct("test prompt", "test-model")

        assert mock_client.chat.completions.create.call_count == 3

    @pytest.mark.asyncio
    async def test_llm_correct_non_429_no_retry(self):
        """Mock OpenAI client raises non-429 → YouTubeTranscriptError raised immediately."""
        from unittest.mock import MagicMock, patch

        from sow_analysis.workers.youtube_transcript import _llm_correct

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = ValueError("Auth failed")
        mock_openai_class = MagicMock(return_value=mock_client)

        with (
            patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings,
            patch("sow_analysis.workers.llm_rate_limit.settings") as mock_rl_settings,
            patch.dict("sys.modules", {"openai": MagicMock(OpenAI=mock_openai_class)}),
        ):
            mock_settings.SOW_LLM_API_KEY = "test-key"
            mock_settings.SOW_LLM_BASE_URL = "https://api.test.com"
            mock_settings.SOW_LLM_MODEL = "test-model"
            mock_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_rl_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_rl_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0
            mock_rl_settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES = 8
            mock_rl_settings.SOW_LLM_RATE_LIMIT_BASE_DELAY = 0.01
            mock_rl_settings.SOW_LLM_RATE_LIMIT_MAX_DELAY = 0.1
            mock_rl_settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 300

            with pytest.raises(YouTubeTranscriptError, match="LLM correction failed"):
                await _llm_correct("test prompt", "test-model")

        # Should have tried only once (non-429 propagates immediately)
        assert mock_client.chat.completions.create.call_count == 1

    @pytest.mark.asyncio
    async def test_llm_correct_respects_retry_after(self):
        """Verify backoff sleep respects retry_after from response body."""
        from unittest.mock import MagicMock, patch

        from sow_analysis.workers.youtube_transcript import _llm_correct

        class FakeRateLimitError(Exception):
            def __init__(self):
                self.status_code = 429
                self.response = MagicMock()
                self.response.text = '{"error": {"retry_after": 5.0}}'
                super().__init__("Rate limit exceeded")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "result"

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise FakeRateLimitError()
            return mock_response

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = side_effect
        mock_openai_class = MagicMock(return_value=mock_client)

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with (
            patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings,
            patch("sow_analysis.workers.llm_rate_limit.settings") as mock_rl_settings,
            patch.dict("sys.modules", {"openai": MagicMock(OpenAI=mock_openai_class)}),
            patch("sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep),
        ):
            mock_settings.SOW_LLM_API_KEY = "test-key"
            mock_settings.SOW_LLM_BASE_URL = "https://api.test.com"
            mock_settings.SOW_LLM_MODEL = "test-model"
            mock_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_rl_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_rl_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0
            mock_rl_settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES = 8
            mock_rl_settings.SOW_LLM_RATE_LIMIT_BASE_DELAY = 0.01
            mock_rl_settings.SOW_LLM_RATE_LIMIT_MAX_DELAY = 30.0
            mock_rl_settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 300

            result = await _llm_correct("test prompt", "test-model")

        assert result == "result"
        assert len(sleep_calls) == 1
        # Delay should be >= retry_after (5.0)
        assert sleep_calls[0] >= 5.0


class TestBreakerRecoveryOnCooldownExpiry:
    """Tests for circuit-breaker recovery on cooldown expiry (bug fix)."""

    def _fresh_rate_limiter(self):
        rl = _YouTubeRateLimiter()
        rl._semaphore = None
        rl._interval_lock = None
        rl._state_lock = None
        rl._last_request_time = 0.0
        rl._consecutive_429_count = 0
        rl._circuit_open_until = 0.0
        return rl

    @pytest.mark.asyncio
    async def test_cooldown_expiry_resets_429_count(self):
        """After cooldown expires, _is_circuit_open() resets the 429 count."""
        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        # Simulate breaker tripped: 5 consecutive 429s, cooldown in the past
        rl._consecutive_429_count = 5
        rl._circuit_open_until = time.monotonic() - 1

        is_open = await rl._is_circuit_open()

        assert is_open is False
        assert rl._consecutive_429_count == 0
        assert rl._circuit_open_until == 0.0

    @pytest.mark.asyncio
    async def test_cooldown_not_yet_expired_keeps_breaker_open(self):
        """If cooldown hasn't expired, breaker stays open and count is NOT reset."""
        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        rl._consecutive_429_count = 5
        rl._circuit_open_until = time.monotonic() + 999

        is_open = await rl._is_circuit_open()

        assert is_open is True
        assert rl._consecutive_429_count == 5

    @pytest.mark.asyncio
    async def test_breaker_never_open_returns_false(self):
        """If breaker was never opened, _is_circuit_open() returns False."""
        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        assert await rl._is_circuit_open() is False
        assert rl._consecutive_429_count == 0


class TestYouTubeRateLimitedErrorRaised:
    """Tests that YouTubeRateLimitedError is raised from the right sites."""

    def _fresh_rate_limiter(self):
        rl = _YouTubeRateLimiter()
        rl._semaphore = None
        rl._interval_lock = None
        rl._state_lock = None
        rl._last_request_time = 0.0
        rl._consecutive_429_count = 0
        rl._circuit_open_until = 0.0
        return rl

    @pytest.mark.asyncio
    async def test_breaker_open_at_entry_raises_rate_limited(self):
        """When breaker is open at entry to call(), raises YouTubeRateLimitedError."""
        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()
        rl._circuit_open_until = time.monotonic() + 9999

        with pytest.raises(YouTubeRateLimitedError, match="circuit breaker is open"):
            await rl.call(lambda: "nope", description="blocked")

    @pytest.mark.asyncio
    async def test_breaker_just_opened_raises_rate_limited(self):
        """When count >= threshold, breaker opens and raises YouTubeRateLimitedError."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        def fn():
            raise Exception("too many 429 error responses")

        with patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.01
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 3
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            for _ in range(3):
                with pytest.raises(YouTubeRateLimitedError):
                    await rl.call(fn, description="breaker test")

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_raises_rate_limited(self):
        """When all retries are exhausted on 429 (below threshold), raises YouTubeRateLimitedError."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        def fn():
            raise Exception("too many 429 error responses")

        with patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 1
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.01
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            with pytest.raises(YouTubeRateLimitedError, match="rate limited after"):
                await rl.call(fn, description="exhausted test")

    @pytest.mark.asyncio
    async def test_non_429_error_raises_transcript_error_not_rate_limited(self):
        """Non-429 errors raise YouTubeTranscriptError (or propagate), not YouTubeRateLimitedError."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        def fn():
            raise Exception("Subtitles are disabled for this video")

        with patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 3
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.01
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            with pytest.raises(Exception, match="Subtitles are disabled") as exc_info:
                await rl.call(fn, description="non-429 test")

            assert not isinstance(exc_info.value, YouTubeRateLimitedError)


class TestFreeModeConfigSelection:
    """Tests that free-mode threshold/cooldown/retry/min-interval values are used."""

    def _fresh_rate_limiter(self):
        rl = _YouTubeRateLimiter()
        rl._semaphore = None
        rl._interval_lock = None
        rl._state_lock = None
        rl._last_request_time = 0.0
        rl._consecutive_429_count = 0
        rl._circuit_open_until = 0.0
        return rl

    @pytest.mark.asyncio
    async def test_free_mode_uses_free_threshold(self):
        """In free mode, breaker opens at the *_FREE threshold (10), not default (5)."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            raise Exception("too many 429 error responses")

        with patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = True
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS_FREE = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES_FREE = 0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY_FREE = 0.01
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD_FREE = 10
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN_FREE = 300

            # With threshold=10 and max_retries=0, each call() does 1 attempt and
            # increments count by 1. After 10 calls, breaker opens.
            for i in range(10):
                with pytest.raises(YouTubeRateLimitedError):
                    await rl.call(fn, description=f"free-threshold-{i}")

            assert await rl._is_circuit_open()

    @pytest.mark.asyncio
    async def test_free_mode_uses_free_cooldown(self):
        """In free mode, _open_circuit uses the *_FREE cooldown value."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        with patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = True
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN_FREE = 300
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 120

            await rl._open_circuit()

            # Cooldown should be ~300s from now
            remaining = rl.remaining_cooldown()
            assert 290 < remaining <= 300

    @pytest.mark.asyncio
    async def test_non_free_mode_uses_default_cooldown(self):
        """In non-free mode, _open_circuit uses the default cooldown value."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        with patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 120
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN_FREE = 300

            await rl._open_circuit()

            remaining = rl.remaining_cooldown()
            assert 110 < remaining <= 120


class TestTryYoutubeTranscriptLrcReRaises:
    """Tests that try_youtube_transcript_lrc re-raises YouTubeRateLimitedError."""

    @pytest.mark.asyncio
    async def test_rate_limited_error_propagates(self, tmp_path):
        """YouTubeRateLimitedError is re-raised, not swallowed."""
        from unittest.mock import AsyncMock, patch

        from sow_analysis.models import LrcOptions
        from sow_analysis.workers.lrc import try_youtube_transcript_lrc

        output_path = tmp_path / "lyrics.lrc"

        with patch(
            "sow_analysis.workers.youtube_transcript.youtube_transcript_to_lrc",
            new_callable=AsyncMock,
            side_effect=YouTubeRateLimitedError("breaker open"),
        ):
            with pytest.raises(YouTubeRateLimitedError, match="breaker open"):
                await try_youtube_transcript_lrc(
                    "https://www.youtube.com/watch?v=test123",
                    "測試",
                    LrcOptions(),
                    output_path,
                )

    @pytest.mark.asyncio
    async def test_permanent_failure_returns_none(self, tmp_path):
        """YouTubeTranscriptError (non-rate-limited) returns None (falls back)."""
        from unittest.mock import AsyncMock, patch

        from sow_analysis.models import LrcOptions
        from sow_analysis.workers.lrc import try_youtube_transcript_lrc

        output_path = tmp_path / "lyrics.lrc"

        with patch(
            "sow_analysis.workers.youtube_transcript.youtube_transcript_to_lrc",
            new_callable=AsyncMock,
            side_effect=YouTubeTranscriptError("No suitable transcript found"),
        ):
            result = await try_youtube_transcript_lrc(
                "https://www.youtube.com/watch?v=test123",
                "測試",
                LrcOptions(),
                output_path,
            )

        assert result is None


class TestWaitForYoutubeCooldownIfOpen:
    """Tests for the wait_for_youtube_cooldown_if_open helper."""

    @pytest.mark.asyncio
    async def test_returns_immediately_when_breaker_closed(self):
        """When breaker is not open, returns True immediately."""
        from sow_analysis.workers.youtube_transcript import wait_for_youtube_cooldown_if_open

        _rate_limiter._consecutive_429_count = 0
        _rate_limiter._circuit_open_until = 0.0

        result = await wait_for_youtube_cooldown_if_open(max_heartbeat_seconds=0.1)

        assert result is True

    @pytest.mark.asyncio
    async def test_waits_then_returns_when_cooldown_expires(self):
        """When breaker is open, waits until cooldown expires, then returns True."""
        from unittest.mock import patch

        from sow_analysis.workers.youtube_transcript import wait_for_youtube_cooldown_if_open

        _rate_limiter._ensure_initialized()
        _rate_limiter._consecutive_429_count = 5
        _rate_limiter._circuit_open_until = time.monotonic() + 0.2

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with patch(
            "sow_analysis.workers.youtube_transcript.asyncio.sleep",
            side_effect=mock_sleep,
        ):
            result = await wait_for_youtube_cooldown_if_open(max_heartbeat_seconds=0.1)

        assert result is True
        assert len(sleep_calls) > 0
        # After cooldown expiry, count should be reset
        assert _rate_limiter._consecutive_429_count == 0

    @pytest.mark.asyncio
    async def test_cancellation_returns_early(self):
        """When is_cancelled returns True, returns early."""
        from unittest.mock import patch

        from sow_analysis.workers.youtube_transcript import wait_for_youtube_cooldown_if_open

        _rate_limiter._ensure_initialized()
        _rate_limiter._consecutive_429_count = 5
        _rate_limiter._circuit_open_until = time.monotonic() + 9999

        cancelled = False

        def is_cancelled():
            return cancelled

        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            nonlocal cancelled
            cancelled = True
            await original_sleep(0)

        with patch(
            "sow_analysis.workers.youtube_transcript.asyncio.sleep",
            side_effect=mock_sleep,
        ):
            result = await wait_for_youtube_cooldown_if_open(
                max_heartbeat_seconds=0.1,
                is_cancelled=is_cancelled,
            )

        # Breaker is still open (cooldown hasn't expired), so returns False
        assert result is False


class TestFreeModeJitter:
    """Tests for ±25% jitter in free-mode min-interval."""

    def _fresh_rate_limiter(self):
        rl = _YouTubeRateLimiter()
        rl._semaphore = None
        rl._interval_lock = None
        rl._state_lock = None
        rl._last_request_time = 0.0
        rl._consecutive_429_count = 0
        rl._circuit_open_until = 0.0
        return rl

    @pytest.mark.asyncio
    async def test_jitter_varies_interval_in_free_mode(self):
        """In free mode, min-interval sleep varies within ±25% of 30.0s."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        sleep_durations = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_durations.append(duration)
            await original_sleep(0)

        with (
            patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings,
            patch(
                "sow_analysis.workers.youtube_transcript.asyncio.sleep",
                side_effect=mock_sleep,
            ),
        ):
            mock_settings.SOW_FREE_ONLY_MODE = True
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS_FREE = 30.0

            # Call _enforce_min_interval many times with _last_request_time set to now
            # so elapsed is ~0 and the min-interval sleep triggers
            for _ in range(20):
                rl._last_request_time = time.monotonic()
                await rl._enforce_min_interval()

        # All sleeps should be within [22.5, 37.5] (30 ± 25%)
        assert len(sleep_durations) == 20
        for d in sleep_durations:
            assert 22.5 <= d <= 37.5, f"Sleep {d} outside jitter range [22.5, 37.5]"

        # And they should vary (not all the same)
        assert len(set(sleep_durations)) > 1


class TestConcurrentBreakerStateSafety:
    """Tests that concurrent tasks don't corrupt breaker state."""

    def _fresh_rate_limiter(self):
        rl = _YouTubeRateLimiter()
        rl._semaphore = None
        rl._interval_lock = None
        rl._state_lock = None
        rl._last_request_time = 0.0
        rl._consecutive_429_count = 0
        rl._circuit_open_until = 0.0
        return rl

    @pytest.mark.asyncio
    async def test_concurrent_429_increments_are_atomic(self):
        """Two concurrent tasks hitting 429s increment count exactly twice."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        def fn():
            # Yield control to allow interleaving
            raise Exception("too many 429 error responses")

        with patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.01
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            tasks = [
                asyncio.create_task(rl.call(fn, description=f"concurrent-429-{i}"))
                for i in range(3)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # All should raise YouTubeRateLimitedError or YouTubeTranscriptError
            for r in results:
                assert isinstance(r, Exception)

            # Count should be exactly 3 (one per task)
            assert rl._consecutive_429_count == 3

    @pytest.mark.asyncio
    async def test_concurrent_is_circuit_open_reset_is_atomic(self):
        """Concurrent is_circuit_open() calls during cooldown expiry don't corrupt state."""
        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        # Set breaker open with cooldown about to expire
        rl._consecutive_429_count = 5
        rl._circuit_open_until = time.monotonic() + 0.05

        # Wait for cooldown to expire
        await asyncio.sleep(0.1)

        # Multiple concurrent is_circuit_open() calls
        results = await asyncio.gather(
            *[rl.is_circuit_open() for _ in range(10)]
        )

        # All should return False
        assert all(r is False for r in results)
        # Count should be reset exactly once
        assert rl._consecutive_429_count == 0
        assert rl._circuit_open_until == 0.0


class TestRemainingCooldown:
    """Tests for the remaining_cooldown() accessor."""

    def _fresh_rate_limiter(self):
        rl = _YouTubeRateLimiter()
        rl._semaphore = None
        rl._interval_lock = None
        rl._state_lock = None
        rl._last_request_time = 0.0
        rl._consecutive_429_count = 0
        rl._circuit_open_until = 0.0
        return rl

    def test_returns_zero_when_breaker_never_open(self):
        rl = self._fresh_rate_limiter()
        assert rl.remaining_cooldown() == 0.0

    def test_returns_positive_when_breaker_open(self):
        rl = self._fresh_rate_limiter()
        rl._circuit_open_until = time.monotonic() + 100
        assert 90 < rl.remaining_cooldown() <= 100

    def test_returns_zero_when_cooldown_expired(self):
        rl = self._fresh_rate_limiter()
        rl._circuit_open_until = time.monotonic() - 10
        assert rl.remaining_cooldown() == 0.0


class TestIsTransientConnectionError:
    """Tests for _is_transient_connection_error()."""

    def test_ssl_error_by_type_name(self):
        """Exception with 'ssl' in type name is detected."""

        class SSLError(Exception):
            pass

        assert _is_transient_connection_error(SSLError("handshake failed")) is True

    def test_connection_error_by_type_name(self):
        """Exception with 'connectionerror' in type name is detected."""

        class ConnectionError(Exception):
            pass

        assert _is_transient_connection_error(ConnectionError("reset")) is True

    def test_ssl_marker_in_message(self):
        """Exception message containing SSL markers is detected."""
        assert _is_transient_connection_error(
            Exception("[SSL: BAD_EXTENSION] bad extension (_ssl.c:1016)")
        ) is True

    def test_wrong_version_number_marker(self):
        """Exception message with wrong_version_number is detected."""
        assert _is_transient_connection_error(
            Exception("[SSL: WRONG_VERSION_NUMBER] wrong version number")
        ) is True

    def test_record_layer_failure_marker(self):
        """Exception message with record layer failure is detected."""
        assert _is_transient_connection_error(
            Exception("[SSL] record layer failure (_ssl.c:2590)")
        ) is True

    def test_tls_marker_in_message(self):
        """Exception message containing 'tls' is detected."""
        assert _is_transient_connection_error(
            Exception("tls handshake failed")
        ) is True

    def test_connection_reset_marker(self):
        """Exception message with 'connection reset' is detected."""
        assert _is_transient_connection_error(
            Exception("Connection reset by peer")
        ) is True

    def test_connection_aborted_marker(self):
        """Exception message with 'connection aborted' is detected."""
        assert _is_transient_connection_error(
            Exception("Connection aborted")
        ) is True

    def test_broken_pipe_marker(self):
        """Exception message with 'broken pipe' is detected."""
        assert _is_transient_connection_error(
            Exception("broken pipe")
        ) is True

    def test_read_timed_out_marker(self):
        """Exception message with 'read timed out' is detected."""
        assert _is_transient_connection_error(
            Exception("Read timed out")
        ) is True

    def test_max_retries_exceeded_with_ssl_cause(self):
        """Max retries exceeded wrapping an SSL error is detected via __cause__."""

        class SSLError(Exception):
            pass

        ssl_err = SSLError("[SSL: BAD_EXTENSION] bad extension")
        try:
            raise ssl_err
        except SSLError as cause:
            try:
                raise Exception("Max retries exceeded with url") from cause
            except Exception as wrapper:
                assert _is_transient_connection_error(wrapper) is True

    def test_max_retries_exceeded_with_ssl_context(self):
        """Max retries exceeded wrapping an SSL error via __context__ is detected."""

        class SSLError(Exception):
            pass

        try:
            raise SSLError("[SSL: WRONG_VERSION_NUMBER]")
        except SSLError:
            try:
                raise Exception("Max retries exceeded with url")
            except Exception as wrapper:
                assert _is_transient_connection_error(wrapper) is True

    def test_non_transient_error_not_detected(self):
        """Non-SSL, non-connection error is not detected."""
        assert _is_transient_connection_error(
            Exception("Subtitles are disabled for this video")
        ) is False

    def test_429_error_not_detected_as_transient(self):
        """429 errors are not classified as transient connection errors."""
        assert _is_transient_connection_error(
            Exception("too many 429 error responses")
        ) is False

    def test_none_exception_returns_false(self):
        """None exception returns False (no crash)."""
        assert _is_transient_connection_error(None) is False

    def test_circular_reference_does_not_loop(self):
        """Circular __cause__ chain doesn't cause infinite loop."""

        class CustomError(Exception):
            pass

        err = CustomError("ssl error")
        err.__cause__ = err  # self-reference
        assert _is_transient_connection_error(err) is True


class TestSSLRetryBehavior:
    """Tests for SSL/connection error retry in _YouTubeRateLimiter.call()."""

    def _fresh_rate_limiter(self):
        rl = _YouTubeRateLimiter()
        rl._semaphore = None
        rl._interval_lock = None
        rl._state_lock = None
        rl._last_request_time = 0.0
        rl._consecutive_429_count = 0
        rl._circuit_open_until = 0.0
        return rl

    @pytest.mark.asyncio
    async def test_ssl_error_retried_then_succeeds(self):
        """fn raises SSL error twice, succeeds on 3rd → retried, result returned."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("[SSL: BAD_EXTENSION] bad extension (_ssl.c:1016)")
            return "success"

        with patch(
            "sow_analysis.workers.youtube_transcript.settings"
        ) as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 3
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.01
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            result = await rl.call(fn, description="ssl retry test")

        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_ssl_error_does_not_trip_circuit_breaker(self):
        """SSL errors do NOT increment _consecutive_429_count or open the breaker."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        def fn():
            raise Exception("[SSL: WRONG_VERSION_NUMBER] wrong version number")

        with patch(
            "sow_analysis.workers.youtube_transcript.settings"
        ) as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 2
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.01
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 3
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            with pytest.raises(Exception, match="WRONG_VERSION_NUMBER"):
                await rl.call(fn, description="ssl no-breaker test")

        # 429 count should remain 0 — SSL errors don't trip the breaker
        assert rl._consecutive_429_count == 0
        assert not await rl._is_circuit_open()

    @pytest.mark.asyncio
    async def test_ssl_error_exhausts_retries_then_raises(self):
        """SSL error on all attempts → original exception propagates."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            raise Exception("[SSL] record layer failure (_ssl.c:2590)")

        with patch(
            "sow_analysis.workers.youtube_transcript.settings"
        ) as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 2
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.01
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            with pytest.raises(Exception, match="record layer failure"):
                await rl.call(fn, description="ssl exhausted test")

        # Should have tried max_retries + 1 = 3 times
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_ssl_error_retry_uses_backoff_sleep(self):
        """SSL error retry sleeps with exponential backoff (capped at 30s)."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("[SSL: BAD_EXTENSION] bad extension")
            return "ok"

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with (
            patch(
                "sow_analysis.workers.youtube_transcript.settings"
            ) as mock_settings,
            patch(
                "sow_analysis.workers.youtube_transcript.asyncio.sleep",
                side_effect=mock_sleep,
            ),
        ):
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 3
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 5.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            result = await rl.call(fn, description="ssl backoff test")

        assert result == "ok"
        # At least one backoff sleep should have occurred
        assert len(sleep_calls) >= 1
        # First backoff: base_delay * 2^0 = 5.0, capped at 30, +jitter (up to 25%)
        # So sleep should be in [5.0, 6.25]
        assert 5.0 <= sleep_calls[0] <= 6.25

    @pytest.mark.asyncio
    async def test_connection_reset_error_retried(self):
        """Connection reset error is retried like SSL errors."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Connection reset by peer")
            return "recovered"

        with patch(
            "sow_analysis.workers.youtube_transcript.settings"
        ) as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 3
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.01
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            result = await rl.call(fn, description="conn-reset test")

        assert result == "recovered"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_non_transient_non_429_error_not_retried(self):
        """Non-SSL, non-429 error is not retried (propagates immediately)."""
        from unittest.mock import patch

        rl = self._fresh_rate_limiter()
        rl._ensure_initialized()

        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            raise Exception("Subtitles are disabled for this video")

        with patch(
            "sow_analysis.workers.youtube_transcript.settings"
        ) as mock_settings:
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 3
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.01
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            with pytest.raises(Exception, match="Subtitles are disabled"):
                await rl.call(fn, description="non-transient test")

        assert call_count == 1


class TestListFallbackDelay:
    """Tests for the 2s delay before list fallback in fetch_youtube_transcript()."""

    @pytest.mark.asyncio
    async def test_delay_before_list_fallback(self):
        """When direct fetch fails, a ~2s delay occurs before list fallback."""
        from unittest.mock import patch

        mock_snippet = type("Snippet", (), {"text": "測試", "start": 0.0})()
        mock_fetched = [mock_snippet]

        mock_transcript_obj = type(
            "Transcript",
            (),
            {
                "language_code": "zh-TW",
                "language": "Chinese (Taiwan)",
                "is_generated": False,
                "fetch": lambda s: mock_fetched,
            },
        )()

        mock_transcript_list = [mock_transcript_obj]

        _rate_limiter._last_request_time = 0.0
        _rate_limiter._consecutive_429_count = 0
        _rate_limiter._circuit_open_until = 0.0

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with (
            patch("sow_analysis.workers.youtube_transcript.settings") as mock_settings,
            patch("youtube_transcript_api.YouTubeTranscriptApi") as MockApi,
            patch(
                "sow_analysis.workers.youtube_transcript.asyncio.sleep",
                side_effect=mock_sleep,
            ),
        ):
            mock_settings.SOW_FREE_ONLY_MODE = False
            mock_settings.SOW_YOUTUBE_PROXY = ""
            mock_settings.SOW_YOUTUBE_PROXY_RETRIES = 3
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT = 1
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 0
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.1
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
            mock_settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

            mock_api = MockApi.return_value
            mock_api.fetch.side_effect = Exception("No transcripts found")
            mock_api.list.return_value = mock_transcript_list

            result = await fetch_youtube_transcript("testVideoId")

        assert len(result) == 1
        # The 2.0s delay should be among the sleep calls
        assert 2.0 in sleep_calls
