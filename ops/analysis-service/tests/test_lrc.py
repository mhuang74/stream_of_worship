"""Tests for LRC generation worker — _llm_align integration with call_llm_with_retry."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from sow_analysis.workers.lrc import WhisperPhrase, _llm_align


class TestLlmAlign524Retry:
    """Tests for _llm_align integration with call_llm_with_retry 5xx retry."""

    @pytest.mark.asyncio
    async def test_524_on_first_attempt_success_on_second(self):
        """A 524 on first LLM attempt, success on second, returns aligned lines."""
        from sow_analysis.workers.lrc import _build_alignment_prompt

        class Fake524Error(Exception):
            def __init__(self):
                self.status_code = 524
                super().__init__("Cloudflare timeout")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(
            [
                {"time_seconds": 0.0, "text": "我要看見"},
                {"time_seconds": 5.0, "text": "祢的榮耀"},
            ]
        )

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

        phrases = [
            WhisperPhrase(text="test", start=0.0, end=3.0),
            WhisperPhrase(text="test2", start=5.0, end=8.0),
        ]

        with (
            patch("sow_analysis.workers.lrc.settings") as mock_settings,
            patch("sow_analysis.workers.llm_rate_limit.settings") as mock_rl_settings,
            patch.dict("sys.modules", {"openai": MagicMock(OpenAI=mock_openai_class)}),
            patch("sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep),
        ):
            mock_settings.SOW_LLM_API_KEY = "test-key"
            mock_settings.SOW_LLM_BASE_URL = "https://api.test.com"
            mock_settings.SOW_LLM_MODEL = "test-model"
            mock_rl_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_rl_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0
            mock_rl_settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES = 8
            mock_rl_settings.SOW_LLM_RATE_LIMIT_BASE_DELAY = 0.01
            mock_rl_settings.SOW_LLM_RATE_LIMIT_MAX_DELAY = 0.1
            mock_rl_settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 300

            lines = await _llm_align(
                "我要看見\n祢的榮耀",
                phrases,
                llm_model="test-model",
                prompt_builder=_build_alignment_prompt,
            )

        assert len(lines) == 2
        assert lines[0].text == "我要看見"
        assert lines[1].text == "祢的榮耀"
        assert call_count == 2
