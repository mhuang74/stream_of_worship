"""Checkpointer selection — always InMemorySaver for v3."""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from stream_of_worship.admin.songset_constructor.config import RunConfig


def choose_checkpointer(config: RunConfig):
    return InMemorySaver()
