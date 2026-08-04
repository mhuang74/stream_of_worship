#!/usr/bin/env python3
"""Retrieve the song catalog pool from PostgreSQL with in-DB pgvector theme scoring.

Wraps ``fetch_catalog_pool`` from the songset_constructor package.

Usage:
    uv run --project ops/admin-cli --extra admin --extra constructor python fetch_pool.py [--pool-limit 500] [--album-series "敬拜讚美 (1)"] [--no-cache]

Output: JSON array of raw SongCandidate objects (pre-enrichment) to stdout.
Diagnostics (pool size, cache hit/miss) to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ADMIN_CLI_SRC = PROJECT_ROOT / "ops" / "admin-cli" / "src"
if str(ADMIN_CLI_SRC) not in sys.path:
    sys.path.insert(0, str(ADMIN_CLI_SRC))


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch catalog pool for songset construction")
    parser.add_argument("--pool-limit", type=int, default=500, help="Maximum songs to load")
    parser.add_argument(
        "--album-series",
        action="append",
        default=None,
        help='Filter by album series (e.g., "敬拜讚美 (1)"). Repeatable.',
    )
    parser.add_argument("--use-cache", action="store_true", default=True, help="Use pool cache (default)")
    parser.add_argument("--no-cache", dest="use_cache", action="store_false", help="Bypass pool cache")
    parser.add_argument(
        "--prefer-fresh",
        action="store_true",
        default=False,
        help="Attempt DB first; fall back to cache only on DB error",
    )
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        default=True,
        help="Serve stale cache when DB read fails (default: True)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path.home() / ".cache" / "sow" / "songset_constructor",
        help="Cache directory",
    )
    args = parser.parse_args()

    from stream_of_worship.admin.config import AdminConfig
    from stream_of_worship.admin.songset_constructor.cache import cache_path, try_load_pool, save_pool
    from stream_of_worship.admin.songset_constructor.config import RunConfig
    from stream_of_worship.admin.songset_constructor.db import fetch_catalog_pool
    from stream_of_worship.db.app.read_client import ReadOnlyClient
    from stream_of_worship.db.connection import ConnectionProvider

    config = AdminConfig.load()
    connection_url = config.get_connection_url()
    provider = ConnectionProvider(connection_url)
    read_client = ReadOnlyClient(provider)

    run_config = RunConfig(
        pool=args.pool_limit,
        album_series=args.album_series or [],
        use_cache=args.use_cache,
        cache_dir=args.cache_dir,
    )

    def _emit_cached(cached, *, source: str) -> None:
        print(f"Pool cache {source}: {len(cached)} songs", file=sys.stderr)
        cpath = cache_path(run_config)
        print(f"Cache file: {cpath}", file=sys.stderr)
        json.dump([c.model_dump(mode="json") for c in cached], sys.stdout, ensure_ascii=False)
        provider.close()

    def _emit_fresh(pool) -> None:
        save_pool(run_config, pool)
        cpath = cache_path(run_config)
        print(f"Pool size: {len(pool)} songs", file=sys.stderr)
        print(f"Cache saved to: {cpath}", file=sys.stderr)
        json.dump([c.model_dump(mode="json") for c in pool], sys.stdout, ensure_ascii=False)
        provider.close()

    # --- Cache-first flow -------------------------------------------------
    # 1. Always probe cache (regardless of TTL) unless --prefer-fresh.
    if args.use_cache and not args.prefer_fresh:
        cached = try_load_pool(run_config)
        if cached is not None:
            _emit_cached(cached, source="hit")
            return
        # Cache file exists but stale? Try to serve it as a fallback.
        stale = _try_load_stale(run_config)
        if stale is not None:
            print(
                f"Pool cache stale (age > {run_config.cache_ttl:.0f}h); attempting DB refresh ...",
                file=sys.stderr,
            )
        # Fall through to DB attempt.
    elif args.use_cache and args.prefer_fresh:
        # --prefer-fresh: attempt DB first, fall back to cache on error.
        try:
            print(f"Prefer-fresh: fetching from database (limit={args.pool_limit})...", file=sys.stderr)
            pool = fetch_catalog_pool(run_config, client=read_client)
            _emit_fresh(pool)
            return
        except Exception as exc:
            cached = try_load_pool(run_config)
            if cached is None:
                cached = _try_load_stale(run_config)
            if cached is not None:
                age_h = _cache_age_hours(run_config)
                print(
                    f"[WARN] DB fetch failed ({exc}); serving cached pool (age: {age_h:.0f}h, "
                    f"{len(cached)} songs)",
                    file=sys.stderr,
                )
                _emit_cached(cached, source="fallback")
                return
            print(f"No pool available: DB unreachable and no cache file found. ({exc})", file=sys.stderr)
            provider.close()
            sys.exit(1)

    # 2. Attempt DB fetch; on failure, fall back to stale cache if allowed.
    try:
        print(f"Pool cache miss, fetching from database (limit={args.pool_limit})...", file=sys.stderr)
        pool = fetch_catalog_pool(run_config, client=read_client)
        _emit_fresh(pool)
    except Exception as exc:
        if args.use_cache and args.allow_stale:
            stale = _try_load_stale(run_config)
            if stale is not None:
                age_h = _cache_age_hours(run_config)
                print(
                    f"[WARN] Serving stale pool cache (age: {age_h:.0f}h, DB unreachable: {exc})",
                    file=sys.stderr,
                )
                _emit_cached(stale, source="stale-fallback")
                return
        print(f"No pool available: DB unreachable and no cache file found. ({exc})", file=sys.stderr)
        provider.close()
        sys.exit(1)


def _try_load_stale(run_config):
    """Load a cache file ignoring TTL (for stale fallback)."""
    from stream_of_worship.admin.songset_constructor.cache import _cache_path, _cache_key
    from stream_of_worship.admin.songset_constructor.models import SongCandidate
    import json as _json
    from pydantic import ValidationError

    path = _cache_path(run_config.cache_dir, _cache_key(run_config.pool, run_config.album_series))
    if not path.exists():
        return None
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
        return [SongCandidate.model_validate(item) for item in data]
    except (OSError, _json.JSONDecodeError, ValidationError):
        return None


def _cache_age_hours(run_config) -> float:
    from stream_of_worship.admin.songset_constructor.cache import _cache_path, _cache_key

    path = _cache_path(run_config.cache_dir, _cache_key(run_config.pool, run_config.album_series))
    if not path.exists():
        return 0.0
    return (time.time() - path.stat().st_mtime) / 3600


if __name__ == "__main__":
    main()
