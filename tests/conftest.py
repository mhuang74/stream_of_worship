"""Pytest configuration for stream-of-worship tests."""

import sys
from pathlib import Path

# Add src directory to path
src_dir = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_dir))

import pytest


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "integration: requires Docker (testcontainers)")


@pytest.fixture(scope="session")
def postgres_url():
    """Start a Postgres container and yield its connection URL.

    This fixture is session-scoped so that all integration tests share a
    single container, keeping test runtime reasonable.

    The URL returned by testcontainers uses the ``postgresql+psycopg2://``
    dialect prefix. We rewrite it to plain ``postgresql://`` so that
    ``psycopg.connect()`` accepts it directly.
    """
    pytest.importorskip("testcontainers", reason="testcontainers not installed")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as postgres:
        url = postgres.get_connection_url()
        # psycopg doesn't understand the +psycopg2 dialect prefix
        url = url.replace("postgresql+psycopg2://", "postgresql://")
        yield url
