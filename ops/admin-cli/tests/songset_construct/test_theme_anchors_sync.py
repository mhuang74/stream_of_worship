"""Tests for theme-anchors sync command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from stream_of_worship.admin.commands.theme_anchors import THEME_ANCHORS_PATH
from stream_of_worship.admin.songset_constructor.rules.embeddings import load_theme_anchors


def test_load_theme_anchors():
    anchors = load_theme_anchors()
    assert len(anchors) == 12
    expected_themes = {"讚美", "感恩", "敬拜", "奉獻", "認罪", "差遣", "信心", "祈禱", "復興", "聖靈", "十字架", "跟隨"}
    assert set(anchors.keys()) == expected_themes


def test_theme_anchors_json_has_correct_structure():
    payload = json.loads(THEME_ANCHORS_PATH.read_text(encoding="utf-8"))
    assert "model_version" in payload
    assert payload["model_version"] == "text-embedding-3-small"
    assert "dim" in payload
    assert payload["dim"] == 1536
    assert "anchors" in payload
    assert len(payload["anchors"]) == 12


def test_theme_anchor_dimensions():
    import numpy as np
    anchors = load_theme_anchors()
    for theme, vector in anchors.items():
        assert isinstance(vector, np.ndarray)
        assert vector.shape == (1536,)
        assert vector.dtype == np.float32


# ---------------------------------------------------------------------------
# CliRunner-based tests for the sync command
# ---------------------------------------------------------------------------

def _make_mock_cursor():
    """Create a mock cursor that tracks execute calls and returns configurable results."""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = [0]
    mock_cursor.fetchall.return_value = []
    return mock_cursor


def _make_mock_conn(mock_cursor):
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.commit = MagicMock()
    return mock_conn


def test_sync_skips_when_12_rows_exist():
    """Without --force, sync should skip if 12 matching rows already exist."""
    from stream_of_worship.admin.commands.theme_anchors import app

    runner = CliRunner()
    mock_cursor = _make_mock_cursor()
    mock_cursor.fetchone.return_value = [12]
    mock_conn = _make_mock_conn(mock_cursor)

    with (
        patch("stream_of_worship.admin.commands.theme_anchors.AdminConfig") as mock_config_cls,
        patch("stream_of_worship.admin.commands.theme_anchors.ConnectionProvider") as mock_cp_cls,
    ):
        mock_config = MagicMock()
        mock_config_cls.load.return_value = mock_config
        mock_cp = MagicMock()
        mock_cp.get_connection.return_value = mock_conn
        mock_cp_cls.return_value = mock_cp

        result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "already has 12" in result.stdout or "already has 12" in (result.output or "")
    # Should NOT have executed any INSERT
    insert_calls = [
        call for call in mock_cursor.execute.call_args_list
        if "INSERT" in str(call).upper()
    ]
    assert len(insert_calls) == 0


def test_sync_upserts_when_fewer_than_12_rows():
    """With <12 rows, sync should upsert all 12 anchors."""
    from stream_of_worship.admin.commands.theme_anchors import app

    runner = CliRunner()
    mock_cursor = _make_mock_cursor()
    mock_cursor.fetchone.return_value = [5]
    mock_conn = _make_mock_conn(mock_cursor)

    with (
        patch("stream_of_worship.admin.commands.theme_anchors.AdminConfig") as mock_config_cls,
        patch("stream_of_worship.admin.commands.theme_anchors.ConnectionProvider") as mock_cp_cls,
    ):
        mock_config = MagicMock()
        mock_config_cls.load.return_value = mock_config
        mock_cp = MagicMock()
        mock_cp.get_connection.return_value = mock_conn
        mock_cp_cls.return_value = mock_cp

        result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "Synced 12 theme anchors" in result.stdout or "Synced 12" in (result.output or "")
    # Should have executed 12 INSERTs
    insert_calls = [
        call for call in mock_cursor.execute.call_args_list
        if "INSERT" in str(call).upper()
    ]
    assert len(insert_calls) == 12
    mock_conn.commit.assert_called()


def test_sync_force_re_inserts_even_with_12_rows():
    """With --force, sync should re-insert even if 12 rows exist."""
    from stream_of_worship.admin.commands.theme_anchors import app

    runner = CliRunner()
    mock_cursor = _make_mock_cursor()
    mock_conn = _make_mock_conn(mock_cursor)

    with (
        patch("stream_of_worship.admin.commands.theme_anchors.AdminConfig") as mock_config_cls,
        patch("stream_of_worship.admin.commands.theme_anchors.ConnectionProvider") as mock_cp_cls,
    ):
        mock_config = MagicMock()
        mock_config_cls.load.return_value = mock_config
        mock_cp = MagicMock()
        mock_cp.get_connection.return_value = mock_conn
        mock_cp_cls.return_value = mock_cp

        result = runner.invoke(app, ["--force"])

    assert result.exit_code == 0
    assert "Synced 12 theme anchors" in result.stdout or "Synced 12" in (result.output or "")
    # Should NOT have run the COUNT query (force skips the check)
    count_calls = [
        call for call in mock_cursor.execute.call_args_list
        if "COUNT" in str(call).upper()
    ]
    assert len(count_calls) == 0
    # Should have executed 12 INSERTs
    insert_calls = [
        call for call in mock_cursor.execute.call_args_list
        if "INSERT" in str(call).upper()
    ]
    assert len(insert_calls) == 12
