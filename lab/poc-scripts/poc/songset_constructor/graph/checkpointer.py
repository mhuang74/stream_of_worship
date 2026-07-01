"""Checkpointer selection."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from poc.songset_constructor.config import RunConfig


def _stable_checkpoint_dir() -> Path:
    """Return a stable checkpoint directory that survives across runs."""
    env_dir = os.environ.get("SOW_CHECKPOINT_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".local" / "share" / "sow-songset" / "checkpoints"


def choose_checkpointer(config: RunConfig):
    if config.interactive_review or config.resume_thread_id:
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver as SqliteSaver  # type: ignore

        # Use a stable directory so --resume-thread-id works even if
        # --output-dir differs from the original run.
        checkpoint_dir = _stable_checkpoint_dir()
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        db_path = checkpoint_dir / f"checkpoint_{config.thread_id}.db"
        return SqliteSaver(sqlite3.connect(str(db_path), check_same_thread=False))
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()
