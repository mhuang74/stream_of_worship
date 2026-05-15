"""Tests for LoginScreen (pick-a-user) using Textual's pilot API."""

from unittest.mock import MagicMock

import pytest
from textual.app import App
from textual.widgets import DataTable, Label

from stream_of_worship.app.screens.login import LoginScreen
from stream_of_worship.app.state import AppState
from stream_of_worship.db.auth_models import User


class _HarnessApp(App):
    """Minimal Textual App that hosts the LoginScreen for testing.

    Captures ``on_user_selected`` invocations so tests can assert that the
    screen correctly delegates after a row is chosen.
    """

    def __init__(self, state: AppState, user_client):
        super().__init__()
        self._state = state
        self._user_client = user_client
        self.selected_user: User | None = None

    def on_mount(self) -> None:
        self.push_screen(LoginScreen(self._state, self._user_client))

    def on_user_selected(self, user: User) -> None:
        self.selected_user = user


def _user(uid: int, name: str, email: str) -> User:
    return User(id=uid, name=name, email=email)


@pytest.mark.asyncio
async def test_login_lists_users_and_focuses_table():
    state = AppState()
    user_client = MagicMock()
    user_client.list_users.return_value = [
        _user(1, "Alice", "alice@example.com"),
        _user(2, "Bob", "bob@example.com"),
    ]

    app = _HarnessApp(state, user_client)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#user_table", DataTable)
        # Two rows, one per user
        assert len(list(table.rows.keys())) == 2
        # Verifies on_mount called list_users()
        user_client.list_users.assert_called_once()


@pytest.mark.asyncio
async def test_login_empty_state_when_no_users():
    state = AppState()
    user_client = MagicMock()
    user_client.list_users.return_value = []

    app = _HarnessApp(state, user_client)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#user_table", DataTable)
        assert len(list(table.rows.keys())) == 0
        empty = app.screen.query_one("#empty_message", Label)
        # ``Static`` widgets store their renderable on a name-mangled private,
        # but the rendered content is accessible via the visual representation.
        # The empty-state message must mention the admin CLI command so the
        # operator knows how to recover.
        assert "sow-admin users add" in str(empty.visual)


@pytest.mark.asyncio
async def test_login_row_selection_sets_user_and_delegates():
    state = AppState()
    alice = _user(1, "Alice", "alice@example.com")
    bob = _user(2, "Bob", "bob@example.com")
    user_client = MagicMock()
    user_client.list_users.return_value = [alice, bob]

    app = _HarnessApp(state, user_client)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#user_table", DataTable)
        # Focus the table, move cursor to row 1 (Bob), and post a RowSelected
        # event the same way Textual would after Enter on a focused DataTable.
        table.focus()
        table.move_cursor(row=1)
        await pilot.pause()
        # Fire the row-selected message directly (avoids relying on the
        # specific key binding for Enter in this Textual version).
        from textual.widgets.data_table import RowKey
        message = DataTable.RowSelected(
            table,
            cursor_row=1,
            row_key=RowKey(str(bob.id)),
        )
        app.screen.post_message(message)
        await pilot.pause()

    assert state.current_user is not None
    assert state.current_user.id == bob.id
    assert app.selected_user is not None
    assert app.selected_user.id == bob.id
