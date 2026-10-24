"""The startup configuration table must cover every env-derived setting."""

import pytest

from sow_analysis import main
from sow_analysis.config import Settings


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


@pytest.mark.parametrize("name", sorted(main.SECRET_FIELDS))
def test_secret_fields_render_presence_only(monkeypatch, name):
    monkeypatch.setenv(name, "sk-test-secret-value")
    monkeypatch.setattr(main, "settings", Settings())

    row = _rows_by_field(main._startup_config_rows())[name]
    assert row[2] == "(set)"
    assert "sk-test-secret-value" not in main._render_startup_config_table(
        main._startup_config_rows()
    )


def test_secret_value_absent_from_table(monkeypatch):
    monkeypatch.setenv("SOW_LLM_API_KEY", "sk-live-should-never-be-logged")
    monkeypatch.setattr(main, "settings", Settings())

    rows = main._startup_config_rows()
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


def test_value_formatting_conventions(monkeypatch):
    monkeypatch.setenv("SOW_YOUTUBE_PROXY", "http://user:pass@proxy.local:8080")
    monkeypatch.setenv("SOW_LLM_BASE_URL", "")
    monkeypatch.setenv("SOW_MVSEP_STAGE1_ADD_OPT2", "")

    monkeypatch.setattr(main, "settings", Settings())
    rows = _rows_by_field(main._startup_config_rows())

    assert rows["SOW_YOUTUBE_PROXY"][2] == "http://***@proxy.local:8080"
    assert rows["SOW_LLM_BASE_URL"][2] == "(not set)"
    assert rows["SOW_MVSEP_STAGE1_ADD_OPT2"][2] == "(not set)"
    assert rows["CACHE_DIR"][2] == str(main.settings.CACHE_DIR)
    assert rows["SOW_ANALYZE_MAX_CONCURRENT"][2] == str(main.settings.SOW_ANALYZE_MAX_CONCURRENT)
