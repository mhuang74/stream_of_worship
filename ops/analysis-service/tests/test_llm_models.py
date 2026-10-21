"""Tests for LLM model validation (services/llm_models.py)."""

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


def _make_openai_module(list_side_effect=None, model_ids=None):
    """Build a fake 'openai' module whose client.models.list() returns model_ids."""
    mock_model = MagicMock()
    mock_model.id = "openai/gpt-4o-mini"
    models_return = (
        [mock_model] if model_ids is None else [MagicMock(id=m) for m in model_ids]
    )

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
