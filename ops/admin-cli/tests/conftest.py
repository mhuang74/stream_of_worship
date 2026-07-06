"""Pytest configuration for stream-of-worship tests."""

import sys
from pathlib import Path

# Add src directory to path
src_dir = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_dir))

import pytest


@pytest.fixture(scope="session")
def postgres_url():
    """Start a Postgres container and yield its connection URL.

    This fixture is session-scoped so that all integration tests share a
    single container, keeping test runtime reasonable.

    The URL returned by testcontainers uses the ``postgresql+psycopg2://``
    dialect prefix. We rewrite it to plain ``postgresql://`` so that
    ``psycopg.connect()`` accepts it directly.

    If Docker is not available, the fixture skips instead of erroring.
    """
    pytest.importorskip("testcontainers", reason="testcontainers not installed")
    from testcontainers.postgres import PostgresContainer

    try:
        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception:
        pytest.skip("Docker not available; skipping integration test")

    try:
        url = container.get_connection_url()
        # psycopg doesn't understand the +psycopg2 dialect prefix
        url = url.replace("postgresql+psycopg2://", "postgresql://")
        yield url
    finally:
        container.stop()


@pytest.fixture
def make_test_provider(postgres_url):
    """Return a factory that builds a ConnectionProvider for the test DB.

    Usage::

        def test_something(make_test_provider):
            provider = make_test_provider()
            client = DatabaseClient(provider)
            ...
    """
    from stream_of_worship.db.connection import ConnectionProvider

    def _make():
        return ConnectionProvider(postgres_url, sslmode="disable")

    return _make


@pytest.fixture
def seed_user():
    """Return a callable that inserts a user into the ``"user"`` table.

    Usage::

        def test_something(postgres_url, seed_user):
            from stream_of_worship.db.connection import ConnectionProvider
            provider = ConnectionProvider(postgres_url)
            user_id = seed_user(provider, email="alice@example.com", name="Alice")
            # ... use user_id to scope SongsetClient, etc.

    Assumes the schema has already been initialized for ``provider``.
    """

    def _seed(provider, email: str = "test@example.com", name: str = "Test User") -> int:
        from stream_of_worship.db.user_client import UserClient

        with UserClient(provider) as client:
            user = client.create_user(email=email, name=name)
            return user.id

    return _seed
