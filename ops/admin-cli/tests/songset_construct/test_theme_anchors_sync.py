"""Tests for theme-anchors sync command."""

from __future__ import annotations

import json

from stream_of_worship.admin.songset_constructor.rules.embeddings import load_theme_anchors


def test_load_theme_anchors():
    anchors = load_theme_anchors()
    assert len(anchors) == 12
    expected_themes = {"讚美", "感恩", "敬拜", "奉獻", "認罪", "差遣", "信心", "祈禱", "復興", "聖靈", "十字架", "跟隨"}
    assert set(anchors.keys()) == expected_themes


def test_theme_anchors_json_has_correct_structure():
    from pathlib import Path
    path = Path(__file__).resolve().parents[4] / "ops/admin-cli/src/stream_of_worship/admin/songset_constructor/data/theme_anchors.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
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
