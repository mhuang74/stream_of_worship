"""Checkpointer selection — always InMemorySaver for v3."""

from __future__ import annotations

from stream_of_worship.admin.songset_constructor.config import RunConfig
from langgraph.checkpoint.memory import InMemorySaver


def choose_checkpointer(config: RunConfig):
    return InMemorySaver()
