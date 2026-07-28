"""Embedding helpers — load_theme_anchors kept for theme-anchors sync."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def load_theme_anchors(path: Path | None = None) -> dict[str, np.ndarray]:
    anchor_path = path or Path(__file__).resolve().parents[1] / "data" / "theme_anchors.json"
    payload = json.loads(anchor_path.read_text(encoding="utf-8"))
    return {
        theme: np.asarray(vector, dtype=np.float32)
        for theme, vector in payload.get("anchors", {}).items()
    }
