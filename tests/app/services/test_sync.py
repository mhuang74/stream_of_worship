"""Tests for the app connection health checker.

The old AppSyncService/Turso infrastructure has been removed; this module now
only re-exports ``check_database_connection()`` from the shared db module.
"""

from unittest.mock import MagicMock, patch

from stream_of_worship.app.services.sync import check_database_connection


class TestCheckDatabaseConnection:
    """Tests for check_database_connection."""

    def test_returns_true_on_success(self):
        """Test successful connectivity check."""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=None)

        with patch("stream_of_worship.db.connection.psycopg.connect", return_value=mock_conn):
            assert check_database_connection("postgresql://user:pass@localhost/db") is True

    def test_returns_false_on_connect_failure(self):
        """Test failed connectivity check."""
        with patch(
            "stream_of_worship.db.connection.psycopg.connect",
            side_effect=Exception("Connection refused"),
        ):
            assert check_database_connection("postgresql://bad:5432/db") is False

    def test_returns_false_on_execute_failure(self):
        """Test when connection succeeds but SELECT 1 fails."""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=None)
        mock_conn.execute = MagicMock(side_effect=Exception("some error"))

        with patch("stream_of_worship.db.connection.psycopg.connect", return_value=mock_conn):
            assert check_database_connection("postgresql://user@localhost/db") is False

    def test_uses_custom_timeout(self):
        """Test that the timeout parameter is passed through."""
        with patch("stream_of_worship.db.connection.psycopg.connect") as mock_connect:
            mock_connect.return_value.__enter__ = MagicMock(return_value=MagicMock())
            check_database_connection("postgresql://user@localhost/db", timeout=5)
            _, kwargs = mock_connect.call_args
            assert kwargs["connect_timeout"] == 5
