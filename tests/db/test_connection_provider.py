"""Unit tests for ConnectionProvider retry logic."""

from unittest.mock import MagicMock, patch

import pytest

from stream_of_worship.db.connection import ConnectionProvider


class TestConnectionProvider:
    def test_get_connection_creates_new_connection(self):
        """First call to get_connection should call psycopg.connect."""
        mock_conn = MagicMock()
        mock_conn.closed = False

        with patch("stream_of_worship.db.connection.psycopg.connect", return_value=mock_conn) as mock_connect:
            provider = ConnectionProvider("postgresql://user:pass@localhost/db")
            conn = provider.get_connection()

            assert conn is mock_conn
            mock_connect.assert_called_once()
            assert mock_connect.call_args.kwargs.get("connect_timeout") == 10

    def test_get_connection_reuses_open_connection(self):
        """Subsequent calls should reuse the same connection."""
        mock_conn = MagicMock()
        mock_conn.closed = False

        with patch("stream_of_worship.db.connection.psycopg.connect", return_value=mock_conn):
            provider = ConnectionProvider("postgresql://user:pass@localhost/db")
            conn1 = provider.get_connection()
            conn2 = provider.get_connection()

            assert conn1 is conn2

    def test_get_connection_reconnects_when_closed(self):
        """If connection is closed, a new one should be created."""
        mock_conn1 = MagicMock()
        mock_conn1.closed = True
        mock_conn2 = MagicMock()
        mock_conn2.closed = False

        with patch(
            "stream_of_worship.db.connection.psycopg.connect",
            side_effect=[mock_conn1, mock_conn2],
        ) as mock_connect:
            provider = ConnectionProvider("postgresql://user:pass@localhost/db")
            provider.get_connection()
            conn = provider.get_connection()

            assert conn is mock_conn2
            assert mock_connect.call_count == 2

    def test_retry_on_initial_failure(self):
        """Transient failure on first attempt should be retried."""
        mock_conn = MagicMock()
        mock_conn.closed = False

        with patch(
            "stream_of_worship.db.connection.psycopg.connect",
            side_effect=[Exception("cold start"), mock_conn],
        ) as mock_connect:
            with patch("stream_of_worship.db.connection.time.sleep") as mock_sleep:
                provider = ConnectionProvider("postgresql://user:pass@localhost/db")
                conn = provider.get_connection()

                assert conn is mock_conn
                assert mock_connect.call_count == 2
                mock_sleep.assert_called_once()

    def test_max_retries_exceeded_raises(self):
        """If all retries fail, the last exception should be re-raised."""
        with patch(
            "stream_of_worship.db.connection.psycopg.connect",
            side_effect=[Exception("fail1"), Exception("fail2"), Exception("fail3")],
        ):
            with patch("stream_of_worship.db.connection.time.sleep"):
                provider = ConnectionProvider("postgresql://user:pass@localhost/db")
                with pytest.raises(Exception, match="fail3"):
                    provider.get_connection()

    def test_close_sets_connection_to_none(self):
        """close() should close and clear the connection."""
        mock_conn = MagicMock()
        mock_conn.closed = False

        with patch("stream_of_worship.db.connection.psycopg.connect", return_value=mock_conn):
            provider = ConnectionProvider("postgresql://user:pass@localhost/db")
            provider.get_connection()
            provider.close()

            mock_conn.close.assert_called_once()
            assert provider._connection is None

    def test_context_manager(self):
        """ConnectionProvider should work as a context manager."""
        mock_conn = MagicMock()
        mock_conn.closed = False

        with patch("stream_of_worship.db.connection.psycopg.connect", return_value=mock_conn):
            with ConnectionProvider("postgresql://user:pass@localhost/db") as provider:
                _ = provider.get_connection()

            mock_conn.close.assert_called_once()
