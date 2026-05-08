"""Unit tests for ConnectionProvider.

No Docker required; the retry logic is exercised with mocked psycopg.connect.
"""

from unittest.mock import MagicMock, patch

import pytest

from stream_of_worship.db.connection import ConnectionProvider


class TestConnectionProvider:
    """Tests for the ConnectionProvider class."""

    @patch("stream_of_worship.db.connection.psycopg")
    def test_get_connection_creates_connection(self, mock_psycopg):
        """Verify get_connection creates a new connection."""
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_psycopg.connect.return_value = mock_conn

        provider = ConnectionProvider("postgresql://user@localhost/db")
        conn = provider.get_connection()

        assert conn is mock_conn
        mock_psycopg.connect.assert_called_once_with(
            "postgresql://user@localhost/db",
            connect_timeout=10,
        )

    @patch("stream_of_worship.db.connection.psycopg")
    def test_get_connection_reuses_existing(self, mock_psycopg):
        """Verify get_connection reuses an open connection."""
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_psycopg.connect.return_value = mock_conn

        provider = ConnectionProvider("postgresql://user@localhost/db")
        conn1 = provider.get_connection()
        conn2 = provider.get_connection()

        assert conn1 is conn2
        assert mock_psycopg.connect.call_count == 1  # Only created once

    @patch("stream_of_worship.db.connection.psycopg")
    def test_get_connection_reconnects_when_closed(self, mock_psycopg):
        """Verify get_connection reconnects when connection is closed."""
        mock_conn1 = MagicMock()
        mock_conn1.closed = True
        mock_conn2 = MagicMock()
        mock_conn2.closed = False
        mock_psycopg.connect.side_effect = [mock_conn1, mock_conn2]

        provider = ConnectionProvider("postgresql://user@localhost/db")
        conn1 = provider.get_connection()
        conn2 = provider.get_connection()

        assert conn1 is mock_conn1
        assert conn2 is mock_conn2
        assert mock_psycopg.connect.call_count == 2

    @patch("stream_of_worship.db.connection.psycopg")
    def test_close_closes_connection(self, mock_psycopg):
        """Verify close closes the connection."""
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_psycopg.connect.return_value = mock_conn

        provider = ConnectionProvider("postgresql://user@localhost/db")
        provider.get_connection()
        provider.close()

        mock_conn.close.assert_called_once()
        assert provider._connection is None

    @patch("stream_of_worship.db.connection.psycopg")
    def test_context_manager(self, mock_psycopg):
        """Verify context manager closes connection on exit."""
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_psycopg.connect.return_value = mock_conn

        with ConnectionProvider("postgresql://user@localhost/db") as provider:
            _ = provider.get_connection()

        mock_conn.close.assert_called_once()

    @patch("stream_of_worship.db.connection.time.sleep")
    @patch("stream_of_worship.db.connection.psycopg")
    def test_retry_on_failure(self, mock_psycopg, mock_sleep):
        """Verify retry logic on connection failure."""
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_psycopg.connect.side_effect = [
            Exception("Connection refused"),
            Exception("Connection refused"),
            mock_conn,
        ]

        provider = ConnectionProvider("postgresql://user@localhost/db")
        conn = provider.get_connection()

        assert conn is mock_conn
        assert mock_psycopg.connect.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("stream_of_worship.db.connection.time.sleep")
    @patch("stream_of_worship.db.connection.psycopg")
    def test_retry_exhaustion_raises(self, mock_psycopg, mock_sleep):
        """Verify last exception is raised after retries are exhausted."""
        mock_psycopg.connect.side_effect = [
            Exception("Failure 1"),
            Exception("Failure 2"),
            Exception("Failure 3"),
        ]

        provider = ConnectionProvider("postgresql://user@localhost/db")
        with pytest.raises(Exception, match="Failure 3"):
            provider.get_connection()

        assert mock_psycopg.connect.call_count == 3


class TestConnectionProviderIntegration:
    """Integration tests that require a real Postgres (testcontainers)."""

    @pytest.mark.integration
    def test_real_postgres_connection(self, postgres_url):
        """Verify ConnectionProvider works with a real Postgres instance."""
        from stream_of_worship.db.connection import ConnectionProvider

        provider = ConnectionProvider(postgres_url)
        conn = provider.get_connection()

        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        assert result[0] == 1

        provider.close()
