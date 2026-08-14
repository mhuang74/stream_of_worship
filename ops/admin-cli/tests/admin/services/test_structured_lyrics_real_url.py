"""Integration test for LLM-based structured lyrics extraction.

Requires a real SOW_LLM_API_KEY and SOW_LLM_MODEL env var.
Excluded by default (addopts = "-m 'not integration'" in pyproject.toml).

Run manually with:
    uv run --project ops/admin-cli --extra admin --extra test pytest \
        tests/admin/services/test_structured_lyrics_real_url.py -v -m integration
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from stream_of_worship.admin.services.structured_lyrics import (
    extract_structured_lyrics_with_llm,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.integration
def test_real_llm_extraction_matches_expected():
    """Calls the real LLM and asserts the result matches the expected fixture.

    Requires SOW_LLM_API_KEY and SOW_LLM_MODEL to be set.
    """
    if not os.environ.get("SOW_LLM_API_KEY") or not os.environ.get("SOW_LLM_MODEL"):
        pytest.skip("SOW_LLM_API_KEY / SOW_LLM_MODEL not set")

    description = (FIXTURES_DIR / "_XgP0p-S4S8_description.txt").read_text(encoding="utf-8")
    expected = json.loads((FIXTURES_DIR / "_XgP0p-S4S8_expected.json").read_text(encoding="utf-8"))

    result = extract_structured_lyrics_with_llm(description)
    assert result is not None

    result_dict = result.to_dict()
    assert len(result_dict["sections"]) == len(expected["sections"])
    for actual_section, expected_section in zip(result_dict["sections"], expected["sections"]):
        assert actual_section["label"] == expected_section["label"]
        assert actual_section["raw_label"] == expected_section["raw_label"]
        assert actual_section["lines"] == expected_section["lines"]
