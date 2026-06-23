"""Login (pick-a-user) screen.

Shows the list of users from the ``"user"`` table and lets the operator
choose one. There's no password — the TUI runs on a trusted machine and
identity is just a name attached to a user_id for data scoping.

If no users exist, the screen instead shows a hint to run
``sow-admin users add <email>`` first.
"""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label

from sow_lab_app.logging_config import get_logger
from sow_lab_app.state import AppState
from stream_of_worship.db.auth_models import User
from stream_of_worship.db.user_client import UserClient

logger = get_logger(__name__)


class LoginScreen(Screen):
    """Pick-a-user screen, shown at TUI startup."""

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def __init__(self, state: AppState, user_client: UserClient):
        """Initialize the login screen.

        Args:
            state: Application state.
            user_client: Client for the Better Auth ``"user"`` table.
        """
        super().__init__()
        self.state = state
        self.user_client = user_client
        self.users: list[User] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Label("[bold]Pick a user[/bold]", id="title")
            yield Label(
                "Select the account to continue (Enter to confirm, q to quit)",
                id="subtitle",
            )
            table = DataTable(id="user_table")
            table.add_columns("ID", "Name", "Email")
            yield table
            yield Label("", id="empty_message")
        yield Footer()

    def on_mount(self) -> None:
        logger.info("LoginScreen mounted")
        self.users = self.user_client.list_users()

        table = self.query_one("#user_table", DataTable)
        empty_label = self.query_one("#empty_message", Label)

        if not self.users:
            logger.warning("No users found — TUI cannot proceed past login")
            empty_label.update(
                "[red]No users found.[/red]\n"
                "Run [cyan]sow-admin users add <email>[/cyan] then relaunch."
            )
            return

        for user in self.users:
            table.add_row(str(user.id), user.name, user.email, key=str(user.id))

        if table.cursor_row is None and len(self.users) > 0:
            table.cursor_row = 0
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        user_id_str = event.row_key.value
        user = next((u for u in self.users if str(u.id) == user_id_str), None)
        if user is None:
            logger.warning(f"Unknown user row selected: {user_id_str}")
            return
        logger.info(f"User selected: id={user.id} email={user.email}")
        self.state.set_current_user(user)
        # Delegate to the app to wire up the user-scoped SongsetClient and
        # navigate to the songset list.
        self.app.on_user_selected(user)

    def action_quit(self) -> None:
        logger.info("Action: quit (from login)")
        self.app.action_quit()
