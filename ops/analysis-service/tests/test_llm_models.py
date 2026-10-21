"""Tests for LLM model validation (services/llm_models.py)."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from sow_analysis.services.llm_models import (
    LLMModelValidationError,
    validate_llm_model,
)


def _patched_settings():
    """Patch llm_models.settings with non-empty SOW_LLM_API_KEY/BASE_URL."""
    return patch(
        "sow_analysis.services.llm_models.settings",
        MagicMock(
            SOW_LLM_API_KEY="test-key",
            SOW_LLM_BASE_URL="https://openrouter.ai/api/v1",
            SOW_LLM_MODEL_VALIDATION_TIMEOUT_SECONDS=30.0,
        ),
    )


def _make_openai_module(list_side_effect=None, model_ids=None, model_metadata=None):
    """Build a fake 'openai' module whose client.models.list() returns model_ids.

    ``model_metadata`` maps model id -> reasoning-meta dict; listed models get
    ``metadata={"reasoning": meta}``. Models without an entry get
    ``metadata=None`` (no reasoning metadata reported).
    """
    ids = model_ids if model_ids is not None else ["openai/gpt-4o-mini"]
    reasoning_meta = model_metadata or {}
    models_return = []
    for model_id in ids:
        mock_model = MagicMock(id=model_id)
        if model_id in reasoning_meta:
            mock_model.metadata = {"reasoning": reasoning_meta[model_id]}
        else:
            mock_model.metadata = None
        models_return.append(mock_model)

    mock_client = MagicMock()
    mock_client.models.list.side_effect = list_side_effect
    mock_client.models.list.return_value = models_return

    mock_openai_class = MagicMock(return_value=mock_client)
    return MagicMock(OpenAI=mock_openai_class), mock_openai_class


class TestValidateLLMModel:
    """Test validate_llm_model fail-closed behavior."""

    @pytest.mark.asyncio
    async def test_success_returns_model_ids(self):
        """Model listed by the provider -> validation passes, returns the id set."""
        openai_module, mock_openai_class = _make_openai_module(
            model_ids=["openai/gpt-4o-mini", "anthropic/claude-3-haiku"]
        )

        with _patched_settings(), patch.dict(
            "sys.modules", {"openai": openai_module}
        ):
            result = await validate_llm_model("openai/gpt-4o-mini")

        assert result == {"openai/gpt-4o-mini", "anthropic/claude-3-haiku"}
        kwargs = mock_openai_class.call_args.kwargs
        assert kwargs["max_retries"] == 0
        assert kwargs["timeout"] == 30.0

    @pytest.mark.asyncio
    async def test_model_not_listed_raises(self):
        """Explicit model id missing from the provider list -> fail closed."""
        openai_module, _ = _make_openai_module(model_ids=["openai/gpt-4o-mini"])

        with _patched_settings(), patch.dict("sys.modules", {"openai": openai_module}):
            with pytest.raises(
                LLMModelValidationError, match="not in the provider's model list"
            ):
                await validate_llm_model("openai/bogus-model")

    @pytest.mark.asyncio
    async def test_invalid_model_logs_validation_info(self, caplog):
        """Invalid model id leaves an INFO log naming the model and /models."""
        openai_module, _ = _make_openai_module(model_ids=["openai/gpt-4o-mini"])

        with (
            caplog.at_level(logging.INFO, logger="sow_analysis.services.llm_models"),
            _patched_settings(),
            patch.dict("sys.modules", {"openai": openai_module}),
            pytest.raises(LLMModelValidationError),
        ):
            await validate_llm_model("openai/bogus-model")

        assert any(
            r.levelname == "INFO"
            and "openai/bogus-model" in r.getMessage()
            and "/models" in r.getMessage()
            for r in caplog.records
        ), "expected the fail-path INFO log naming the model and /models"

    @pytest.mark.asyncio
    async def test_provider_error_raises_validation_error(self):
        """Any provider/network error surfaces as LLMModelValidationError."""
        openai_module, _ = _make_openai_module(list_side_effect=ConnectionError("boom"))

        with _patched_settings(), patch.dict("sys.modules", {"openai": openai_module}):
            with pytest.raises(
                LLMModelValidationError, match="LLM model validation failed for"
            ):
                await validate_llm_model("openai/gpt-4o-mini")

    @pytest.mark.asyncio
    async def test_openai_missing_raises_validation_error(self):
        """ImportError (openai not installed) is caught by the same guard."""
        with _patched_settings(), patch.dict("sys.modules", {"openai": None}):
            with pytest.raises(
                LLMModelValidationError, match="LLM model validation failed for"
            ):
                await validate_llm_model("openai/gpt-4o-mini")

    @pytest.mark.asyncio
    async def test_missing_api_key_raises_before_provider_call(self):
        """Empty SOW_LLM_API_KEY fails closed before reaching the provider."""
        with patch("sow_analysis.services.llm_models.settings") as mock_settings:
            mock_settings.SOW_LLM_API_KEY = ""
            mock_settings.SOW_LLM_BASE_URL = "https://openrouter.ai/api/v1"

            with pytest.raises(LLMModelValidationError, match="SOW_LLM_API_KEY not set"):
                await validate_llm_model("openai/gpt-4o-mini")

    @pytest.mark.asyncio
    async def test_missing_base_url_raises_before_provider_call(self):
        """Empty SOW_LLM_BASE_URL fails closed before reaching the provider."""
        with patch("sow_analysis.services.llm_models.settings") as mock_settings:
            mock_settings.SOW_LLM_API_KEY = "test-key"
            mock_settings.SOW_LLM_BASE_URL = ""

            with pytest.raises(
                LLMModelValidationError, match="SOW_LLM_BASE_URL not set"
            ):
                await validate_llm_model("openai/gpt-4o-mini")


class TestSplitLLMModelEffort:
    """Test split_llm_model_effort parsing."""

    def test_effort_suffix_parsed(self):
        from sow_analysis.services.llm_models import split_llm_model_effort

        assert split_llm_model_effort("deepseek-v4.1-flash:low") == (
            "deepseek-v4.1-flash",
            "low",
        )

    def test_non_effort_suffix_untouched(self):
        from sow_analysis.services.llm_models import split_llm_model_effort

        assert split_llm_model_effort("m:free") == ("m:free", None)

    def test_no_colon_untouched(self):
        from sow_analysis.services.llm_models import split_llm_model_effort

        assert split_llm_model_effort("m") == ("m", None)

    def test_effort_case_sensitive(self):
        from sow_analysis.services.llm_models import split_llm_model_effort

        assert split_llm_model_effort("m:LOW") == ("m:LOW", None)

    def test_none_effort_parsed(self):
        from sow_analysis.services.llm_models import split_llm_model_effort

        assert split_llm_model_effort("m:none") == ("m", "none")


class TestValidateLLMModelRequest:
    """Test validate_llm_model_request (model:effort syntax)."""

    @pytest.mark.asyncio
    async def test_valid_model_and_effort(self):
        """Valid model:effort -> (model_id, effort), exactly one provider fetch."""
        from sow_analysis.services.llm_models import validate_llm_model_request

        openai_module, mock_openai_class = _make_openai_module(
            model_ids=["m", "glm-5.3-flash"],
            model_metadata={"m": {"accepted_efforts": ["low", "high"]}},
        )

        with _patched_settings(), patch.dict("sys.modules", {"openai": openai_module}):
            result = await validate_llm_model_request("m:low")

        assert result == ("m", "low")
        assert mock_openai_class.return_value.models.list.call_count == 1

    @pytest.mark.asyncio
    async def test_effort_not_accepted_raises_with_sorted_list(self):
        from sow_analysis.services.llm_models import validate_llm_model_request

        openai_module, _ = _make_openai_module(
            model_ids=["m"],
            model_metadata={"m": {"accepted_efforts": ["high", "low", "medium"]}},
        )

        with _patched_settings(), patch.dict("sys.modules", {"openai": openai_module}):
            with pytest.raises(LLMModelValidationError) as exc_info:
                await validate_llm_model_request("m:xhigh")

        assert "['high', 'low', 'medium']" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_model_without_reasoning_metadata_raises(self):
        from sow_analysis.services.llm_models import validate_llm_model_request

        openai_module, _ = _make_openai_module(model_ids=["m"])

        with _patched_settings(), patch.dict("sys.modules", {"openai": openai_module}):
            with pytest.raises(
                LLMModelValidationError, match="does not report reasoning metadata"
            ):
                await validate_llm_model_request("m:low")

    @pytest.mark.asyncio
    async def test_non_effort_suffix_fails_membership_check(self):
        """'m:free' is not an effort suffix -> checked as a model id and fails."""
        from sow_analysis.services.llm_models import validate_llm_model_request

        openai_module, _ = _make_openai_module(model_ids=["openai/gpt-4o-mini"])

        with _patched_settings(), patch.dict("sys.modules", {"openai": openai_module}):
            with pytest.raises(
                LLMModelValidationError,
                match="LLM model 'm:free' is not in the provider's model list",
            ):
                await validate_llm_model_request("m:free")

    @pytest.mark.asyncio
    async def test_plain_model_passes_without_effort_validation(self):
        """No suffix -> (model, None); no reasoning metadata needed."""
        from sow_analysis.services.llm_models import validate_llm_model_request

        openai_module, _ = _make_openai_module(model_ids=["m"])

        with _patched_settings(), patch.dict("sys.modules", {"openai": openai_module}):
            result = await validate_llm_model_request("m")

        assert result == ("m", None)
