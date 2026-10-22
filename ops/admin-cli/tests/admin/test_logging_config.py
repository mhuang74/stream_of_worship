"""Unit tests for sow-admin file logging configuration."""

import logging
from pathlib import Path

from stream_of_worship.admin.logging_config import (
    DEFAULT_LOG_FILENAME,
    get_log_path,
    setup_logging,
)


def test_get_log_path_uses_cache_dir():
    path = get_log_path()
    assert path.name == DEFAULT_LOG_FILENAME
    assert path.parent.name == "logs"


def test_setup_logging_writes_to_file(tmp_path):
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    try:
        setup_logging(tmp_path, "INFO")
        logging.getLogger("stream_of_worship.admin.test").info("hello from test")
        for handler in root.handlers:
            handler.flush()

        log_file = Path(tmp_path) / DEFAULT_LOG_FILENAME
        assert log_file.exists()
        assert "hello from test" in log_file.read_text(encoding="utf-8")
    finally:
        root.handlers.clear()
        root.handlers.extend(original_handlers)
        root.setLevel(original_level)


def test_setup_logging_reads_env_level(tmp_path, monkeypatch):
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    try:
        monkeypatch.setenv("SOW_ADMIN_LOG_LEVEL", "DEBUG")
        setup_logging(tmp_path)
        assert logging.getLogger().level == logging.DEBUG

        monkeypatch.setenv("SOW_ADMIN_LOG_LEVEL", "not-a-level")
        setup_logging(tmp_path)
        assert logging.getLogger().level == logging.INFO
    finally:
        root.handlers.clear()
        root.handlers.extend(original_handlers)
        root.setLevel(original_level)
