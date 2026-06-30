"""Checkpointer selection."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from poc.songset_constructor.config import RunConfig


def choose_checkpointer(config: RunConfig):
    if config.interactive_review or config.resume_thread_id:
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver as SqliteSaver  # type: ignore

        db_path = Path(config.output_dir) / "checkpoint.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return SqliteSaver(sqlite3.connect(str(db_path), check_same_thread=False))
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()
