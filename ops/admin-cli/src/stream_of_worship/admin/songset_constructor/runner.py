"""Run the songset constructor graph."""

from __future__ import annotations

from stream_of_worship.admin.songset_constructor import cache
from stream_of_worship.admin.songset_constructor.config import RunConfig
from stream_of_worship.admin.songset_constructor.db import fetch_catalog_pool
from stream_of_worship.admin.songset_constructor.graph.builder import build_graph
from stream_of_worship.admin.songset_constructor.graph.state import ConstructorState
from stream_of_worship.db.app.read_client import ReadOnlyClient


def run(config: RunConfig, read_client: ReadOnlyClient) -> dict:
    cached_pool = cache.try_load_pool(config)
    if cached_pool is not None:
        pool = cached_pool
        from rich.console import Console
        Console().print("[dim]Pool loaded from cache[/dim]")
    else:
        pool = fetch_catalog_pool(config, client=read_client)
        cache.save_pool(config, pool)
        from rich.console import Console
        Console().print(f"[dim]Pool fetched from DB ({len(pool)} songs)[/dim]")

    graph = build_graph(config)

    initial_state: ConstructorState = {
        "config": config,
        "pool": pool,
        "_read_client": read_client,
        "trace": [],
        "iterations": 0,
        "beam_candidates": [],
        "llm_drafts": [],
        "final_proposals": [],
    }

    events = list(graph.stream(initial_state, stream_mode="debug"))
    final_state = events[-1][1] if len(events) == 1 else events[-1]

    result = {
        "final_proposals": final_state.get("final_proposals", []),
        "pool": final_state.get("pool", pool),
        "trace": final_state.get("trace", []),
        "enrichment_metrics": final_state.get("enrichment_metrics", {}),
    }
    return result
