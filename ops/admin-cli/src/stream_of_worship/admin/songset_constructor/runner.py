"""Run the songset constructor graph."""

from __future__ import annotations

import time

from rich.console import Console

from stream_of_worship.admin.songset_constructor import cache
from stream_of_worship.admin.songset_constructor.artifacts.trace import event
from stream_of_worship.admin.songset_constructor.config import RunConfig
from stream_of_worship.admin.songset_constructor.db import fetch_catalog_pool
from stream_of_worship.admin.songset_constructor.graph.builder import build_graph
from stream_of_worship.admin.songset_constructor.graph.state import ConstructorState
from stream_of_worship.db.app.read_client import ReadOnlyClient

console = Console()


def run(config: RunConfig, read_client: ReadOnlyClient) -> dict:
    cached_pool = cache.try_load_pool(config)
    if cached_pool is not None:
        pool = cached_pool
        try:
            age_h = (time.time() - cache.cache_path(config).stat().st_mtime) / 3600
            console.print(f"[dim]Pool loaded from cache (age: {age_h:.0f}h)[/dim]")
        except (FileNotFoundError, OSError):
            console.print("[dim]Pool loaded from cache[/dim]")
    else:
        pool = fetch_catalog_pool(config, client=read_client)
        cache.save_pool(config, pool)
        console.print(f"[dim]Pool fetched from DB ({len(pool)} songs)[/dim]")

    graph = build_graph(config)

    initial_state: ConstructorState = {
        "config": config,
        "pool": pool,
        "trace": [event("load_catalog", "exit", {"pool_size": len(pool)})],
        "iterations": 0,
        "beam_candidates": [],
        "llm_drafts": [],
        "final_proposals": [],
    }

    result = graph.invoke(
        initial_state,
        config={"configurable": {"thread_id": config.thread_id}},
    )

    return {
        "final_proposals": result.get("final_proposals", []),
        "pool": result.get("pool", pool),
        "trace": result.get("trace", []),
        "enrichment_metrics": result.get("enrichment_metrics", {}),
    }
