"""Embedding helpers without a pgvector Python dependency."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def parse_pgvector_text(value: str | None) -> np.ndarray | None:
    if not value:
        return None
    text = value.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if not text:
        return np.array([], dtype=np.float32)
    return np.array([float(part.strip()) for part in text.split(",") if part.strip()], dtype=np.float32)


def cosine(a: np.ndarray | list[float] | None, b: np.ndarray | list[float] | None) -> float:
    if a is None or b is None:
        return 0.0
    av = np.asarray(a, dtype=np.float32)
    bv = np.asarray(b, dtype=np.float32)
    if av.size == 0 or bv.size == 0 or av.shape != bv.shape:
        return 0.0
    denom = float(np.linalg.norm(av) * np.linalg.norm(bv))
    if denom <= 1e-12:
        return 0.0
    return float(np.dot(av, bv) / denom)


def load_theme_anchors(path: Path | None = None) -> dict[str, np.ndarray]:
    anchor_path = path or Path(__file__).resolve().parents[1] / "data" / "theme_anchors.json"
    payload = json.loads(anchor_path.read_text(encoding="utf-8"))
    return {
        theme: np.asarray(vector, dtype=np.float32)
        for theme, vector in payload.get("anchors", {}).items()
    }
