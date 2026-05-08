"""Pytest configuration for stream-of-worship tests."""

import sys
from pathlib import Path

# Add src directory to path
src_dir = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_dir))


# ---------------------------------------------------------------------------
# Pytest markers
# ---------------------------------------------------------------------------
def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires Docker (testcontainers)")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
import pytest  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402


@pytest.fixture(scope="session")
def postgres_url():
    """Start a Postgres container for the session.

    Tests marked with ``@pytest.mark.integration`` require Docker.
    Unit tests can use this fixture directly---it is a session-scoped
    singleton so the container is only started once even with many
    integration tests.
    """
    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url()
        # Ensure the protocol is ``postgresql://`` (psycopg3 expects it).
        url = url.replace("postgresql+psycopg2://", "postgresql://")
        yield url
