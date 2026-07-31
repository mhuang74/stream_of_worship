#!/usr/bin/env python3
"""Retrieve the song catalog pool from PostgreSQL with in-DB pgvector theme scoring.

Wraps ``fetch_catalog_pool`` from the songset_constructor package.

Usage:
    python fetch_pool.py [--pool-limit 500] [--album-series "敬拜讚美 (1)"] [--no-cache]

Output: JSON array of raw SongCandidate objects (pre-enrichment) to stdout.
Diagnostics (pool size, cache hit/miss) to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
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

    cached = try_load_pool(run_config)
    if cached is not None:
        print(f"Pool cache hit: {len(cached)} songs", file=sys.stderr)
        cpath = cache_path(run_config)
        print(f"Cache file: {cpath}", file=sys.stderr)
        json.dump([c.model_dump(mode="json") for c in cached], sys.stdout, ensure_ascii=False)
        provider.close()
        return

    print(f"Pool cache miss, fetching from database (limit={args.pool_limit})...", file=sys.stderr)
    pool = fetch_catalog_pool(run_config, client=read_client)
    save_pool(run_config, pool)
    cpath = cache_path(run_config)
    print(f"Pool size: {len(pool)} songs", file=sys.stderr)
    print(f"Cache saved to: {cpath}", file=sys.stderr)

    json.dump([c.model_dump(mode="json") for c in pool], sys.stdout, ensure_ascii=False)
    provider.close()


if __name__ == "__main__":
    main()
