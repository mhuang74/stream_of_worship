"""Trace event helpers for graph nodes."""

from __future__ import annotations

from datetime import UTC, datetime


def event(node: str, event_name: str, data: dict | None = None, iteration: int = 0) -> dict:
    return {
        "ts": datetime.now(UTC).isoformat(),
        "node": node,
        "event": event_name,
        "iteration": iteration,
        "data": data or {},
    }
