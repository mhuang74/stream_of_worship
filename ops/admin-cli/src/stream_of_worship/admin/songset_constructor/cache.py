"""Pool cache layer for the songset constructor."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from pydantic import ValidationError

from stream_of_worship.admin.songset_constructor.config import RunConfig
from stream_of_worship.admin.songset_constructor.models import SongCandidate


def _cache_key(pool_limit: int, album_series: list[str]) -> str:
    raw = f"{pool_limit}:{sorted(album_series or ['*'])}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"pool_{key}.json"


def try_load_pool(config: RunConfig) -> list[SongCandidate] | None:
    if not config.use_cache:
        return None
    path = _cache_path(config.cache_dir, _cache_key(config.pool, config.album_series))
    if not path.exists():
        return None
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    if age_hours > config.cache_ttl:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [SongCandidate.model_validate(item) for item in data]
    except (OSError, json.JSONDecodeError, ValidationError):
        path.unlink(missing_ok=True)
        return None


def cache_path(config: RunConfig) -> Path:
    return _cache_path(config.cache_dir, _cache_key(config.pool, config.album_series))


def save_pool(config: RunConfig, pool: list[SongCandidate]) -> None:
    if not config.use_cache:
        return
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(config.cache_dir, _cache_key(config.pool, config.album_series))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps([c.model_dump(mode="json") for c in pool], ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)
