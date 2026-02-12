# Coding Conventions

**Analysis Date:** 2026-02-13

## Naming Patterns

**Files:**
- Snake case for all Python files: `test_catalog.py`, `lrc_generator.py`, `export_service.py`
- Test files prefix with `test_`: `test_catalog.py`, `test_config.py`, `test_lrc_pipeline.py`
- Integration tests in `tests/integration/`, unit tests in `tests/unit/`
- Module names are descriptive and lowercase: `catalog.py`, `scraper.py`, `metadata_generator.py`

**Functions:**
- Snake case: `get_user_data_dir()`, `format_time()`, `add_song()`
- Private functions prefixed with underscore: `_beat_snap()`, `_llm_align()`, `_auth_headers()`
- Methods prefixed with `get_` for retrievers: `get_song()`, `get_logger()`
- Methods prefixed with `find_` or `filter_` for searching: `find_by_theme()`, `filter_by_bpm_range()`

**Variables:**
- Snake case: `last_updated`, `api_key`, `song_id`, `output_dir`
- Constants in UPPERCASE: None currently used
- Boolean variables clear intent: `has_stems`, `has_lrc`, `force`, `incremental`
- Parameters descriptive and typed: `audio_url: str`, `generate_stems: bool = True`

**Types:**
- Type hints on all function parameters and return values
- Use of `Optional[T]`, `List[T]`, `Dict[str, Any]` from typing module
- Dataclass usage for data containers: `Song`, `CatalogIndex`, `AnalysisResult`, `JobInfo`
- Enum for state/status values: `ExportState`, `AppScreen`

## Code Style

**Formatting:**
- Black formatter with line length: 100 characters (configured in `pyproject.toml`)
- Target Python version: 3.11
- Consistent indentation: 4 spaces

**Linting:**
- Ruff for linting (configured in `pyproject.toml`)
- Line length: 100 characters
- Target version: py311
- Rule configuration in `pyproject.toml` [tool.ruff]

**Module Docstrings:**
- All modules have docstring at top describing purpose
- Format: triple double quotes `"""..."""`
- Example from `src/stream_of_worship/core/catalog.py`:
```python
"""Song catalog management for Stream of Worship.

This module handles loading, indexing, and managing the song library
from the catalog_index.json file.
"""
```

## Import Organization

**Order:**
1. Standard library imports (json, logging, pathlib, typing, datetime, etc.)
2. Third-party library imports (requests, BeautifulSoup, dataclasses, etc.)
3. Local application imports (stream_of_worship.*)

**Path Aliases:**
- No path aliases configured; all imports use full module paths
- Relative imports avoided; absolute imports preferred: `from stream_of_worship.core.catalog import Song`
- Clean separation by package: admin, app, core, ingestion, tui, cli

**Example from `src/stream_of_worship/admin/services/scraper.py`:**
```python
import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from pypinyin import lazy_pinyin

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Song
```

## Error Handling

**Patterns:**
- Custom exceptions with descriptive names: `AnalysisServiceError`, `ValueError`, `FileNotFoundError`
- Exception classes inherit from base Exception types
- Exceptions provide context: status codes, error messages with details
- Example from `src/stream_of_worship/admin/services/analysis.py`:
```python
class AnalysisServiceError(Exception):
    """Error communicating with the analysis service."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code
```

- Try/except blocks catch specific exceptions, not bare except
- Exceptions re-raised with context: `raise AnalysisServiceError(...)`
- HTTP errors handled explicitly: `requests.exceptions.ConnectionError`, `requests.exceptions.RequestException`
- File operations check existence before access: `if not path.exists():` raises `FileNotFoundError(f"...{path}")`

## Logging

**Framework:**
- Standard Python `logging` module
- Logger naming: `logging.getLogger(__name__)` per module
- Loggers prefixed with module name for tracking

**Patterns:**
- Logger initialized at module level: `logger = logging.getLogger(__name__)`
- App-specific logger via `get_logger(name)` function: `logger = get_logger(__name__)`
- Logging levels: DEBUG, INFO, ERROR used appropriately
- Informational messages for operations: `logger.info(f"Fetching lyrics table from {self.url}")`
- Error messages with context: `logger.error(f"Failed to fetch page: {e}")`
- Debug for internals: `logger.debug(...)`
- Special module: `src/stream_of_worship/app/logging_config.py` handles app logging setup
  - Provides `setup_logging(log_dir)` and `get_logger(name)`
  - Rotates logs when >10MB
  - Detailed format: timestamp, level, module name, message

## Comments

**When to Comment:**
- Docstrings for all public functions and classes (required)
- Complex logic explained inline for clarity
- Workarounds or non-obvious solutions documented
- Example from `src/stream_of_worship/core/catalog.py`:
```python
# Check if song already exists by ID
existing_index = next(
    (i for i, s in enumerate(self.songs) if s.id == song_id), None
)
```

**JSDoc/TSDoc:**
- Not used; Python docstrings in Google/NumPy style
- Args, Returns, Raises documented in docstrings
- Example:
```python
def load(cls, path: Optional[Path] = None) -> "CatalogIndex":
    """Load catalog index from JSON file.

    Args:
        path: Path to catalog_index.json (uses default if None)

    Returns:
        CatalogIndex instance

    Raises:
        FileNotFoundError: If catalog file doesn't exist
        ValueError: If catalog file contains invalid data
    """
```

## Function Design

**Size:**
- Functions focused on single responsibility
- Typically 5-30 lines for utility functions
- Longer functions for orchestration (e.g., export pipeline): 50-100 lines
- Private methods for internal helpers

**Parameters:**
- Typed parameters with type hints
- Optional parameters with defaults: `include_video: bool = True`
- Avoid too many parameters; use dataclass for complex config
- Example: `submit_analysis(audio_url: str, content_hash: str, generate_stems: bool = True) -> JobInfo`

**Return Values:**
- Clear return types: `-> Optional[Song]`, `-> List[Song]`, `-> bool`, `-> None`
- Dataclasses for complex returns: `-> AnalysisResult`, `-> JobInfo`
- Empty collections returned, not None: `return []`, `return {}`
- Functions indicate success/failure via return type or exception

## Module Design

**Exports:**
- All public classes and functions exported naturally
- No explicit `__all__` lists (convention: if not prefixed with `_`, it's public)
- Example module structure:
  - `src/stream_of_worship/core/catalog.py` exports `Song`, `CatalogIndex`
  - `src/stream_of_worship/admin/services/analysis.py` exports `AnalysisClient`, `AnalysisResult`, `JobInfo`, `AnalysisServiceError`

**Barrel Files:**
- Used in `__init__.py` for convenience imports
- Example from `src/stream_of_worship/core/__init__.py`:
```python
from stream_of_worship.core.config import Config
from stream_of_worship.core.paths import (
    get_user_data_dir,
    get_cache_dir,
    get_song_library_path,
    get_output_path,
)
```

**Dataclasses:**
- Heavy use of `@dataclass` for data containers
- Fields use `field(default_factory=list)` for mutable defaults
- Post-init processing via `__post_init__` when needed
- Class methods for construction: `@classmethod from_dict(cls, data)`, `to_dict()`
- Properties for computed values: `@property display_name(self)`

**Path Handling:**
- ALWAYS use `pathlib.Path`, never string concatenation
- All path functions in `src/stream_of_worship/core/paths.py`
- Functions handle platform-specific paths (macOS, Linux, Windows)
- Example: `path = get_user_data_dir() / "output" / "audio"`

---

*Convention analysis: 2026-02-13*
