"""Tests for DatabaseClient.transaction() reconnect-on-BEGIN resilience.

A connection that idles longer than the server tolerates (Neon suspend, NAT
idle timeout) keeps a half-dead socket that ``ConnectionProvider`` still
reports as open, so the next ``BEGIN`` raises ``OperationalError``. These
tests pin down the one-shot reconnect retry inside ``transaction()``: it must
happen before the caller's body runs, and must never replay work after entry.
"""

from unittest.mock import MagicMock, patch

import psycopg
import pytest

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import SongComponent
from stream_of_worship.db.connection import ConnectionProvider

STALE_ERROR = "SSL SYSCALL error: Operation timed out"


def _transaction_mock(enter_error: Exception | None = None) -> MagicMock:
    """Build a Transaction stand-in; ``enter_error`` makes BEGIN fail."""
    txn = MagicMock()
    if enter_error is None:
        txn.__enter__.return_value = txn
    else:
        txn.__enter__.side_effect = enter_error
    return txn


def _connection_mock(txn: MagicMock) -> MagicMock:
    """Build a connection stand-in that is never reported as ``closed``."""
    conn = MagicMock()
    conn.closed = False
    conn.transaction.return_value = txn
    return conn


class TestTransactionReconnect:
    def test_reconnects_and_retries_once_when_begin_fails(self):
        """BEGIN on a stale socket reconnects, then runs the body on the new connection."""
        stale_txn = _transaction_mock(psycopg.OperationalError(STALE_ERROR))
        stale_conn = _connection_mock(stale_txn)
        fresh_txn = _transaction_mock()
        fresh_conn = _connection_mock(fresh_txn)

        with patch(
            "stream_of_worship.db.connection.psycopg.connect",
            side_effect=[stale_conn, fresh_conn],
        ) as mock_connect:
            client = DatabaseClient(ConnectionProvider("postgresql://user:pass@localhost/db"))
            bodies: list[psycopg.Connection] = []

            with client.transaction() as conn:
                bodies.append(conn)

        assert mock_connect.call_count == 2
        assert bodies == [fresh_conn]
        assert bodies[0] is fresh_conn
        stale_txn.__exit__.assert_not_called()
        stale_conn.commit.assert_not_called()
        fresh_txn.__exit__.assert_called_once_with(None, None, None)
        fresh_conn.commit.assert_called_once()

    def test_upsert_song_components_survives_stale_connection(self):
        """The observed crash path (post-poll component write) now succeeds."""
        stale_txn = _transaction_mock(psycopg.OperationalError(STALE_ERROR))
        stale_conn = _connection_mock(stale_txn)
        fresh_txn = _transaction_mock()
        fresh_conn = _connection_mock(fresh_txn)

        with patch(
            "stream_of_worship.db.connection.psycopg.connect",
            side_effect=[stale_conn, fresh_conn],
        ) as mock_connect:
            client = DatabaseClient(ConnectionProvider("postgresql://user:pass@localhost/db"))
            inserted = client.upsert_song_components(
                "song_1",
                "abc123",
                [SongComponent(song_id="song_1", content_hash="abc123", component_type="chorus")],
            )

        assert inserted == 1
        assert mock_connect.call_count == 2
        cursor = fresh_conn.cursor.return_value
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        assert any("DELETE FROM song_components" in sql for sql in statements)
        cursor.executemany.assert_called_once()
        fresh_conn.commit.assert_called_once()
        stale_conn.cursor.assert_not_called()

    def test_healthy_connection_does_not_reconnect(self):
        """A healthy BEGIN leaves the provider untouched."""
        txn = _transaction_mock()
        conn = _connection_mock(txn)

        with patch(
            "stream_of_worship.db.connection.psycopg.connect", return_value=conn
        ) as mock_connect:
            provider = ConnectionProvider("postgresql://user:pass@localhost/db")
            client = DatabaseClient(provider)
            with (
                patch.object(provider, "invalidate", wraps=provider.invalidate) as invalidate,
                client.transaction() as yielded,
            ):
                assert yielded is conn

        assert mock_connect.call_count == 1
        invalidate.assert_not_called()
        txn.__exit__.assert_called_once_with(None, None, None)
        conn.commit.assert_called_once()

    def test_body_exception_rolls_back_without_retry(self):
        """A workload error after entry rolls back and propagates untouched."""
        txn = _transaction_mock()
        conn = _connection_mock(txn)

        with patch(
            "stream_of_worship.db.connection.psycopg.connect", return_value=conn
        ) as mock_connect:
            client = DatabaseClient(ConnectionProvider("postgresql://user:pass@localhost/db"))
            with (
                pytest.raises(RuntimeError, match="simulated caller error"),
                client.transaction(),
            ):
                raise RuntimeError("simulated caller error")

        assert mock_connect.call_count == 1
        exit_args = txn.__exit__.call_args.args
        assert exit_args[0] is RuntimeError
        assert isinstance(exit_args[1], RuntimeError)
        conn.commit.assert_not_called()

    def test_body_operational_error_is_not_retried(self):
        """OperationalError after entry must not replay partially executed work."""
        txn = _transaction_mock()
        conn = _connection_mock(txn)

        with patch(
            "stream_of_worship.db.connection.psycopg.connect", return_value=conn
        ) as mock_connect:
            client = DatabaseClient(ConnectionProvider("postgresql://user:pass@localhost/db"))
            with (
                pytest.raises(psycopg.OperationalError, match=STALE_ERROR),
                client.transaction(),
            ):
                raise psycopg.OperationalError(STALE_ERROR)

        assert mock_connect.call_count == 1
        assert txn.__exit__.call_args.args[0] is psycopg.OperationalError
        conn.commit.assert_not_called()

    def test_retry_failure_propagates(self):
        """If the reconnected BEGIN also fails, the error propagates (no third attempt)."""
        stale_txn = _transaction_mock(psycopg.OperationalError(STALE_ERROR))
        stale_conn = _connection_mock(stale_txn)
        also_stale_txn = _transaction_mock(psycopg.OperationalError(STALE_ERROR))
        also_stale_conn = _connection_mock(also_stale_txn)

        with patch(
            "stream_of_worship.db.connection.psycopg.connect",
            side_effect=[stale_conn, also_stale_conn],
        ) as mock_connect:
            client = DatabaseClient(ConnectionProvider("postgresql://user:pass@localhost/db"))
            with (
                pytest.raises(psycopg.OperationalError, match=STALE_ERROR),
                client.transaction(),
            ):
                pytest.fail("body must not run when BEGIN never succeeds")

        assert mock_connect.call_count == 2

    def test_nested_transaction_entry_does_not_reconnect(self):
        """Entering a savepoint inside an existing transaction keeps the connection."""
        outer_txn = _transaction_mock()
        inner_txn = _transaction_mock()
        conn = _connection_mock(outer_txn)
        conn.transaction.side_effect = [outer_txn, inner_txn]

        with patch(
            "stream_of_worship.db.connection.psycopg.connect", return_value=conn
        ) as mock_connect:
            provider = ConnectionProvider("postgresql://user:pass@localhost/db")
            client = DatabaseClient(provider)
            with (
                patch.object(provider, "invalidate", wraps=provider.invalidate) as invalidate,
                client.transaction() as outer,
                client.transaction() as inner,
            ):
                assert inner is outer

        assert mock_connect.call_count == 1
        invalidate.assert_not_called()
        assert conn.transaction.call_count == 2
        outer_txn.__exit__.assert_called_once_with(None, None, None)
        inner_txn.__exit__.assert_called_once_with(None, None, None)
        # Only the outermost transaction commits on the connection.
        assert conn.commit.call_count == 2
