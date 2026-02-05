"""Pytest configuration and fixtures."""
import sys
from pathlib import Path

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def pytest_configure(config):
    """Configure pytest."""
    config.addinivalue_line(
        "markers", "asyncio: mark test as an asyncio test"
    )


@pytest.fixture
def config_path():
    """Get the config path."""
    return Path(__file__).parent.parent / "config.json"


@pytest.fixture
def app(config_path):
    """Create app instance for testing."""
    from app.main import TransitionBuilderApp
    return TransitionBuilderApp(config_path)
