"""The startup configuration table must cover every env-derived setting."""

import re

import pytest

from sow_analysis import main
from sow_analysis.config import Settings

# Any Settings field naming a credential must be masked in the startup log. Catches a
# new secret field added without a matching SECRET_FIELDS entry. Anchored at the end so
# names like SOW_LLM_SEGMENTATION_MAX_TOKENS are not treated as credentials.
_SECRET_NAME_RE = re.compile(
    r"(?:API_KEY|SECRET_ACCESS_KEY|ACCESS_KEY(?:_ID)?|TOKEN(?:_ID|_SECRET)?|PASSWORD)$"
)


def _rows_by_field(rows: list[tuple[str, str, str]]) -> dict[str, tuple[str, str, str]]:
    return {row[1]: row for row in rows}


def test_every_settings_field_has_a_row():
    """A new Settings field must appear in the startup log (not silently vanish)."""
    missing = sorted(set(Settings.model_fields) - set(_rows_by_field(main._startup_config_rows())))
    assert missing == [], f"startup table omits: {missing}"


def test_category_map_covers_every_field_without_duplicates():
    total_grouped = sum(len(fields) for fields in main._CATEGORY_FIELDS.values())
    assert total_grouped == len(main._CATEGORY_BY_FIELD), "a field is listed in two categories"
    assert set(main._CATEGORY_BY_FIELD) == set(Settings.model_fields)


def test_rows_use_every_category_and_never_fall_back_to_other():
    """An unmapped field renders under _UNCATEGORIZED instead of crashing; catch it here."""
    categories = {row[0] for row in main._startup_config_rows() if row[0] != "Modal"}
    assert categories == set(main._CATEGORY_FIELDS)
    assert main._UNCATEGORIZED not in categories


def test_every_credential_named_field_is_masked():
    unmasked = sorted(
        name
        for name in Settings.model_fields
        if _SECRET_NAME_RE.search(name) and name not in main.SECRET_FIELDS
    )
    assert unmasked == [], f"credential-looking fields missing from SECRET_FIELDS: {unmasked}"


@pytest.mark.parametrize("name", sorted(main.SECRET_FIELDS))
def test_secret_fields_render_presence_only(name):
    cfg = Settings(**{name: "sk-test-secret-value"})

    rows = main._startup_config_rows(cfg)
    assert _rows_by_field(rows)[name][2] == "(set)"
    assert "sk-test-secret-value" not in main._render_startup_config_table(rows)


def test_secret_value_absent_from_table():
    rows = main._startup_config_rows(Settings(SOW_LLM_API_KEY="sk-live-should-never-be-logged"))

    assert _rows_by_field(rows)["SOW_LLM_API_KEY"] == ("LLM", "SOW_LLM_API_KEY", "(set)")
    assert "sk-live-should-never-be-logged" not in main._render_startup_config_table(rows)


def test_modal_token_rows_reflect_env(monkeypatch):
    monkeypatch.setenv("MODAL_TOKEN_ID", "ta-test-token-id")
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)

    rows = _rows_by_field(main._startup_config_rows())
    assert rows["MODAL_TOKEN_ID"] == ("Modal", "MODAL_TOKEN_ID", "(set)")
    assert rows["MODAL_TOKEN_SECRET"] == ("Modal", "MODAL_TOKEN_SECRET", "(not set)")
    assert rows["modal_token_configured"][2] == "False"
    # Both halves are required before Modal is usable; the token itself is never logged.
    assert "ta-test-token-id" not in main._render_startup_config_table(main._startup_config_rows())


def test_value_formatting_conventions():
    cfg = Settings(
        SOW_YOUTUBE_PROXY="http://user:pass@proxy.local:8080",
        SOW_LLM_BASE_URL="",
        SOW_MVSEP_STAGE1_ADD_OPT2="",
    )

    rows = _rows_by_field(main._startup_config_rows(cfg))

    assert rows["SOW_YOUTUBE_PROXY"][2] == "http://***@proxy.local:8080"
    assert rows["SOW_LLM_BASE_URL"][2] == "(not set)"
    assert rows["SOW_MVSEP_STAGE1_ADD_OPT2"][2] == "(not set)"
    assert rows["CACHE_DIR"][2] == str(cfg.CACHE_DIR)
    assert rows["SOW_ANALYZE_MAX_CONCURRENT"][2] == str(cfg.SOW_ANALYZE_MAX_CONCURRENT)
