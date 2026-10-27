"""Tests for the LLM health check's model:effort handling."""

from unittest.mock import MagicMock, patch

from sow_analysis.config import settings
from sow_analysis.routes.health import check_llm_connection


def test_check_llm_connection_parses_model_effort(monkeypatch):
    monkeypatch.setattr(settings, "SOW_LLM_API_KEY", "k")
    monkeypatch.setattr(settings, "SOW_LLM_BASE_URL", "https://fake.example/v1")
    monkeypatch.setattr(settings, "SOW_LLM_MODEL", "deepseek-v4.1-flash:low")

    resp = MagicMock()
    resp.choices[0].message.content = "quote"
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = resp

    with patch.dict(
        "sys.modules", {"openai": MagicMock(OpenAI=MagicMock(return_value=mock_client))}
    ):
        result = check_llm_connection()

    kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "deepseek-v4.1-flash"
    assert kwargs["reasoning_effort"] == "low"
    assert result["status"] == "healthy"
    assert result["model"] == "deepseek-v4.1-flash:low"
