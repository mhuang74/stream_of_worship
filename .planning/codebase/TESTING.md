# Testing Patterns

**Analysis Date:** 2026-02-13

## Test Framework

**Runner:**
- pytest >= 7.4.0
- Config: `pyproject.toml` [tool.pytest.ini_options]
- Async support: pytest-asyncio >= 0.23.0

**Assertion Library:**
- pytest's built-in assertions: `assert`, `assert x == y`
- No explicit assertion library; standard Python assertions

**Run Commands:**
```bash
# Run all tests (set PYTHONPATH first)
PYTHONPATH=src uv run pytest

# Run specific test file
PYTHONPATH=src uv run pytest tests/unit/test_catalog.py -v

# Run specific test class/function
PYTHONPATH=src uv run pytest tests/unit/test_catalog.py::TestSong::test_song_creation -v

# Run with coverage
PYTHONPATH=src uv run pytest --cov=src/stream_of_worship tests/

# Watch mode not configured; use pytest-watch if needed
```

**Configuration:**
- Pytest config in `pyproject.toml`
- Test discovery: `tests/**/*.py`
- Excluded directories: `scripts/`, `poc/`, `.*/`, `build/`, `dist/`, `*.egg-info`
- Asyncio mode: auto

## Test File Organization

**Location:**
- Co-located in `tests/` parallel to `src/stream_of_worship/`
- Unit tests: `src/stream_of_worship/tests/unit/`
- Integration tests: `src/stream_of_worship/tests/integration/`
- Conftest: `src/stream_of_worship/tests/conftest.py`

**Naming:**
- Test files: `test_<module>.py`
- Test classes: `Test<Component>` (PascalCase)
- Test methods: `test_<scenario>` (snake_case, descriptive)
- Examples:
  - `tests/unit/test_catalog.py` → `TestSong`, `TestCatalogIndex`
  - `tests/unit/test_config.py` → `TestConfig`
  - `tests/integration/test_lrc_pipeline.py` → `TestLRCLineFormatting`, `TestBeatSnapping`

**Structure:**
```
src/stream_of_worship/
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # Shared fixtures
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── test_catalog.py
│   │   ├── test_config.py
│   │   ├── test_cli.py
│   │   ├── test_lrc_generator.py
│   │   ├── test_metadata_generator.py
│   │   ├── test_migration.py
│   │   ├── test_paths.py
│   │   ├── test_tui_models.py
│   │   ├── test_tui_services.py
│   │   └── test_tui_state.py
│   └── integration/
│       ├── __init__.py
│       └── test_lrc_pipeline.py
```

## Test Structure

**Suite Organization:**

From `src/stream_of_worship/tests/unit/test_catalog.py`:
```python
"""Tests for song catalog management."""

import json
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
import pytest

from stream_of_worship.core.catalog import Song, CatalogIndex


class TestSong:
    """Tests for Song dataclass."""

    def test_song_creation(self):
        """Test creating a Song with all fields."""
        song = Song(
            id="test_song_1",
            title="Test Song",
            artist="Test Artist",
            bpm=120.0,
            key="C",
            duration=180.0,
            tempo_category="medium",
            vocalist="mixed",
            themes=["Praise", "Worship"],
            bible_verses=["Psalm 23:1"],
            ai_summary="A test song about praising God.",
            has_stems=True,
            has_lrc=True,
        )

        assert song.id == "test_song_1"
        assert song.title == "Test Song"
        assert song.bpm == 120.0
        assert song.tempo_category == "medium"
        assert song.has_stems is True
        assert song.has_lrc is True

    def test_display_name_property(self):
        """Test display_name property."""
        song = Song(
            id="test_song",
            title="Awesome Song",
            artist="Great Artist",
            bpm=100.0,
            key="G",
            duration=240.0,
        )

        assert song.display_name == "Awesome Song - Great Artist"
```

**Patterns:**

1. **One test class per class/component:**
   - `TestSong` for Song dataclass
   - `TestCatalogIndex` for CatalogIndex dataclass
   - Clear separation of concerns

2. **Descriptive test method names:**
   - Describe what is being tested: `test_song_creation`, `test_display_name_property`
   - Include scenario/edge case: `test_from_dict_with_all_fields`, `test_from_dict_with_defaults`
   - Test failure cases: `test_load_raises_file_not_found`

3. **Docstrings for test methods:**
   - One-line description of what test verifies
   - Example: `"""Test creating a Song with all fields."""`

4. **Arrange-Act-Assert pattern:**
   - Setup data (Arrange)
   - Call function (Act)
   - Verify results (Assert)

## Mocking

**Framework:**
- `unittest.mock` from standard library
- MagicMock, patch decorator, mock_open
- Imported as: `from unittest.mock import patch, MagicMock, mock_open`

**Patterns:**

From `src/stream_of_worship/tests/integration/test_lrc_pipeline.py`:
```python
@patch("stream_of_worship.ingestion.lrc_generator.openai")
def test_llm_align_success(self, mock_openai):
    """Test successful LLM alignment."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps([
        {"time_seconds": 10.5, "text": "First line"},
        {"time_seconds": 15.2, "text": "Second line"},
    ])
    mock_client.chat.completions.create.return_value = mock_response
    mock_openai.OpenAI.return_value = mock_client

    generator = LRCGenerator(api_key="test-key")
    whisper_words = [
        WhisperWord(word="First", start=10.5, end=10.8),
        WhisperWord(word="line", start=10.9, end=11.2),
        WhisperWord(word="Second", start=15.2, end=15.5),
        WhisperWord(word="line", start=15.6, end=15.9),
    ]

    result = generator._llm_align("First line\nSecond line", whisper_words)

    assert len(result) == 2
    assert result[0].time_seconds == 10.5
    assert result[0].text == "First line"
```

**What to Mock:**
- External API calls: `requests.get()`, OpenAI API, S3/R2
- File I/O: use temporary directories via pytest fixtures
- Database connections: create in-memory or test databases
- Long-running operations: mock for speed
- Randomness: seed or mock random generators

**What NOT to Mock:**
- Core business logic (unless testing integration with mocks)
- Dataclass construction and properties
- Standard library functions (Path, json, etc.)
- Internal utility functions you're testing
- Ensure mocking doesn't hide real bugs

## Fixtures and Factories

**Test Data:**

From `src/stream_of_worship/tests/unit/test_catalog.py`:
```python
@pytest.fixture
def catalog_file(self, tmp_path):
    """Fixture providing a temporary catalog file."""
    return tmp_path / "catalog.json"

def test_load_with_songs(self, catalog_file):
    """Test loading catalog from JSON file."""
    data = {
        "last_updated": "2026-01-30T10:00:00Z",
        "version": "1.0",
        "songs": [
            {
                "id": "song_1",
                "title": "Song One",
                "artist": "Artist A",
                "bpm": 120.0,
                "key": "C",
                "duration": 180.0,
            },
            {
                "id": "song_2",
                "title": "Song Two",
                "artist": "Artist B",
                "bpm": 100.0,
                "key": "G",
                "duration": 240.0,
            },
        ],
    }

    with catalog_file.open("w") as f:
        json.dump(data, f)

    catalog = CatalogIndex.load(catalog_file)

    assert len(catalog.songs) == 2
    assert catalog.songs[0].title == "Song One"
    assert catalog.songs[1].title == "Song Two"
```

**Location:**
- Fixtures defined in test methods as parameters: `def test_method(self, tmp_path):`
- Pytest provides built-in fixtures: `tmp_path`, `tmp_path_factory`, `monkeypatch`
- Shared fixtures in `src/stream_of_worship/tests/conftest.py`
- Fixture scope controlled by `@pytest.fixture(scope=...)`

**Patterns:**
- Use `tmp_path` for temporary file testing
- Create test objects inline in test methods
- Dataclass construction for test data setup
- Dictionary creation for JSON parsing tests

## Coverage

**Requirements:**
- No enforcement configured; optional measurement
- View coverage: `PYTHONPATH=src uv run pytest --cov=src/stream_of_worship`

**Target:**
- Unit tests aim for high coverage of core modules
- Integration tests ensure key workflows function
- Exclude: scripts, POC code, UI rendering

## Test Types

**Unit Tests:**
- Location: `tests/unit/`
- Scope: Test individual functions, classes, dataclass behavior
- Isolation: Use mocks for external dependencies
- Examples:
  - `test_catalog.py`: Tests Song and CatalogIndex classes in isolation
  - `test_config.py`: Tests Config dataclass and methods
  - `test_paths.py`: Tests path resolution functions
- Speed: Fast, <1ms each typically

**Integration Tests:**
- Location: `tests/integration/`
- Scope: Test complete workflows across multiple modules
- Isolation: Mock external APIs but test internal flow
- Example: `test_lrc_pipeline.py`
  - Tests LRC line formatting
  - Tests beat snapping with beat lists
  - Tests LLM alignment with retries
  - Tests end-to-end generation
- Speed: Slower, may take seconds due to workflow complexity

**E2E Tests:**
- Framework: Not used currently
- Future: Could use Textual testing framework for TUI
- Would test full user workflows from UI to output

## Common Patterns

**Async Testing:**

From `pyproject.toml`:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

Not yet heavily used; structure when needed:
```python
@pytest.mark.asyncio
async def test_async_function():
    """Test async function."""
    result = await async_function()
    assert result is not None
```

**Error Testing:**

From `src/stream_of_worship/tests/unit/test_catalog.py`:
```python
def test_load_raises_file_not_found(self):
    """Test that load raises FileNotFoundError for non-existent file."""
    with pytest.raises(FileNotFoundError):
        CatalogIndex.load(Path("/nonexistent/catalog.json"))

def test_remove_song_not_found(self):
    """Test removing a non-existent song."""
    catalog = CatalogIndex()

    result = catalog.remove_song("nonexistent")

    assert result is False
```

**Parametrized Tests:**

Not yet used; could apply for testing multiple scenarios:
```python
@pytest.mark.parametrize("input,expected", [
    ("input1", "expected1"),
    ("input2", "expected2"),
])
def test_scenarios(input, expected):
    assert function(input) == expected
```

**Fixtures with Scope:**

```python
@pytest.fixture(scope="function")
def fresh_catalog():
    """Fresh catalog for each test."""
    return CatalogIndex()

@pytest.fixture(scope="session")
def large_dataset():
    """Load large dataset once per test session."""
    return load_test_data()
```

## Test Dependencies

**From `pyproject.toml`:**
```toml
test = [
    "pytest>=7.4.0",
    "pytest-mock>=3.12.0",
    "pytest-asyncio>=0.23.0",
    "fastapi>=0.109.0",
    "httpx>=0.26.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
]
```

- `pytest`: Test runner
- `pytest-mock`: Enhanced mocking (though `unittest.mock` used directly)
- `pytest-asyncio`: Async test support with auto mode
- FastAPI, httpx, pydantic: For testing API-related code

---

*Testing analysis: 2026-02-13*
