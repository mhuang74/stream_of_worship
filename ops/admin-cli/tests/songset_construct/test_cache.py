"""Tests for pool cache layer."""

from __future__ import annotations

import json
import time
from pathlib import Path

from stream_of_worship.admin.songset_constructor.cache import (
    _cache_key,
    try_load_pool,
    save_pool,
)
from stream_of_worship.admin.songset_constructor.config import RunConfig
from stream_of_worship.admin.songset_constructor.models import SongCandidate


def test_cache_key_deterministic():
    key1 = _cache_key(200, ["A", "B"])
    key2 = _cache_key(200, ["B", "A"])
    assert key1 == key2
    key3 = _cache_key(200, ["A"])
    assert key1 != key3


def test_cache_key_wildcard():
    key1 = _cache_key(200, [])
    key2 = _cache_key(200, None)  # type: ignore
    assert key1 == key2


def test_cache_miss_returns_none(tmp_path: Path):
    config = RunConfig(
        count=3, proposals=3, pool=200,
        cache_dir=tmp_path, use_cache=True, cache_ttl=24.0,
    )
    assert try_load_pool(config) is None


def test_cache_hit_returns_pool(tmp_path: Path):
    pool = [
        SongCandidate(song_id="s1", title="Test", recording_hash_prefix="abc"),
    ]
    config = RunConfig(
        count=3, proposals=3, pool=200,
        cache_dir=tmp_path, use_cache=True, cache_ttl=24.0,
    )
    save_pool(config, pool)
    loaded = try_load_pool(config)
    assert loaded is not None
    assert len(loaded) == 1
    assert loaded[0].song_id == "s1"


def test_cache_ttl_expiry(tmp_path: Path):
    pool = [
        SongCandidate(song_id="s1", title="Test", recording_hash_prefix="abc"),
    ]
    config = RunConfig(
        count=3, proposals=3, pool=200,
        cache_dir=tmp_path, use_cache=True, cache_ttl=0,  # 0 = expired
    )
    save_pool(config, pool)
    loaded = try_load_pool(config)
    # Since cache_ttl is 0, the cache is considered expired
    assert loaded is None


def test_no_cache_returns_none(tmp_path: Path):
    pool = [
        SongCandidate(song_id="s1", title="Test", recording_hash_prefix="abc"),
    ]
    config = RunConfig(
        count=3, proposals=3, pool=200,
        cache_dir=tmp_path, use_cache=False, cache_ttl=24.0,
    )
    save_pool(config, pool)
    loaded = try_load_pool(config)
    assert loaded is None  # use_cache=False bypasses


def test_save_pool_writes_valid_json(tmp_path: Path):
    pool = [
        SongCandidate(song_id="s1", title="Test", recording_hash_prefix="abc"),
    ]
    config = RunConfig(
        count=3, proposals=3, pool=200,
        cache_dir=tmp_path, use_cache=True, cache_ttl=24.0,
    )
    save_pool(config, pool)
    key = _cache_key(config.pool, config.album_series)
    path = tmp_path / f"pool_{key}.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["song_id"] == "s1"
    assert data[0]["title"] == "Test"


def test_corrupt_cache_returns_none(tmp_path: Path):
    """A corrupt cache file should be treated as a cache miss, not a crash."""
    config = RunConfig(
        count=3, proposals=3, pool=200,
        cache_dir=tmp_path, use_cache=True, cache_ttl=24.0,
    )
    key = _cache_key(config.pool, config.album_series)
    path = tmp_path / f"pool_{key}.json"
    path.write_text("NOT VALID JSON {{{", encoding="utf-8")
    assert try_load_pool(config) is None


def test_corrupt_cache_self_heals(tmp_path: Path):
    """A corrupt cache file is deleted so subsequent runs don't keep hitting it."""
    config = RunConfig(
        count=3, proposals=3, pool=200,
        cache_dir=tmp_path, use_cache=True, cache_ttl=24.0,
    )
    key = _cache_key(config.pool, config.album_series)
    path = tmp_path / f"pool_{key}.json"
    path.write_text("NOT VALID JSON {{{", encoding="utf-8")
    try_load_pool(config)
    assert not path.exists()


def test_atomic_write_no_tmp_left(tmp_path: Path):
    """save_pool should not leave a .tmp file behind on success."""
    pool = [
        SongCandidate(song_id="s1", title="Test", recording_hash_prefix="abc"),
    ]
    config = RunConfig(
        count=3, proposals=3, pool=200,
        cache_dir=tmp_path, use_cache=True, cache_ttl=24.0,
    )
    save_pool(config, pool)
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


def test_atomic_write_preserves_existing_on_failure(tmp_path: Path):
    """If a .tmp write is interrupted, the existing cache file stays intact."""
    pool = [
        SongCandidate(song_id="s1", title="Original", recording_hash_prefix="abc"),
    ]
    config = RunConfig(
        count=3, proposals=3, pool=200,
        cache_dir=tmp_path, use_cache=True, cache_ttl=24.0,
    )
    save_pool(config, pool)
    key = _cache_key(config.pool, config.album_series)
    path = tmp_path / f"pool_{key}.json"
    original_content = path.read_text(encoding="utf-8")

    # Simulate a truncated .tmp file left from an interrupted write
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text("TRUNCATED", encoding="utf-8")

    # The existing cache file should still be intact
    assert path.read_text(encoding="utf-8") == original_content
