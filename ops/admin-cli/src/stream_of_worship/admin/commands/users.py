"""User management commands for sow-admin.

Seed and inspect rows in the Better Auth ``"user"`` table. IDs are short
sequential integers assigned by the DB.
"""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.db.connection import ConnectionProvider
from stream_of_worship.db.user_client import DuplicateEmailError, UserClient

console = Console()
app = typer.Typer(help="User management operations")


def _get_user_client(config: AdminConfig) -> UserClient:
    provider = ConnectionProvider(config.get_connection_url())
    return UserClient(provider)


def _load_config(config_path: Optional[Path]) -> AdminConfig:
    try:
        return AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)


def _truncate(value, width: int = 40) -> str:
    """Render a value for table display, truncating long strings.

    ``None`` renders as an empty string; non-string values (ints, floats,
    booleans) are stringified first so numeric columns display.
    """
    if value is None:
        return ""
    text = str(value)
    if len(text) > width:
        return text[:width] + "…"
    return text


def _print_cascade_preview(preview: dict[str, list[dict]]) -> None:
    """Print a per-table breakdown of rows a delete would cascade away.

    Empty tables are skipped; when every table is empty a single dim line is
    printed; a total row count is appended after the tables.
    """
    table_specs = [
        (
            "songsets",
            "Songsets",
            [
                ("ID", "id"),
                ("Name", "name"),
                ("Description", "description"),
                ("Created", "created_at"),
            ],
        ),
        (
            "songset_items",
            "Songset Items",
            [
                ("ID", "id"),
                ("Songset ID", "songset_id"),
                ("Song ID", "song_id"),
                ("Pos", "position"),
                ("Created", "created_at"),
            ],
        ),
        (
            "user_settings",
            "User Settings",
            [
                ("User ID", "user_id"),
                ("Offline Auto-Cache", "offline_auto_cache"),
                ("Created", "created_at"),
                ("Updated", "updated_at"),
            ],
        ),
        (
            "user_lrc_override",
            "User LRC Overrides",
            [
                ("ID", "id"),
                ("Recording Hash", "recording_content_hash"),
                ("Created", "created_at"),
                ("Updated", "updated_at"),
            ],
        ),
        (
            "lyric_mark",
            "Lyric Marks",
            [
                ("ID", "id"),
                ("Recording Hash", "recording_content_hash"),
                ("Timestamp (s)", "timestamp_seconds"),
                ("Created", "created_at"),
            ],
        ),
        (
            "songset_share",
            "Songset Shares",
            [
                ("Token", "token"),
                ("Songset ID", "songset_id"),
                ("Render Job ID", "render_job_id"),
                ("Created", "created_at"),
            ],
        ),
        (
            "account",
            "Accounts",
            [
                ("ID", "id"),
                ("Provider", "providerId"),
                ("Account ID", "accountId"),
                ("Created", "createdAt"),
            ],
        ),
        (
            "session",
            "Sessions",
            [
                ("ID", "id"),
                ("Token", "token"),
                ("Expires", "expiresAt"),
                ("Created", "createdAt"),
            ],
        ),
    ]
    total = 0
    printed_any = False
    for key, title, columns in table_specs:
        rows = preview.get(key, [])
        if not rows:
            continue
        total += len(rows)
        printed_any = True
        table = Table(title=f"{title} ({len(rows)} row(s))")
        for header, _ in columns:
            table.add_column(header)
        sample = rows[:10]
        for row in sample:
            table.add_row(*[_truncate(row.get(col)) for _, col in columns])
        omitted = len(rows) - len(sample)
        if omitted > 0:
            table.add_row(f"[dim]… {omitted} more row(s) omitted[/dim]")
        console.print(table)

    if not printed_any:
        console.print("[dim]No cascade data to delete.[/dim]")
    else:
        console.print(f"[yellow]Total: {total} row(s) will be cascade-deleted.[/yellow]")


@app.command("add")
def add_user(
    email: str = typer.Argument(..., help="User email (must be unique)"),
    display_name: Optional[str] = typer.Option(
        None,
        "--display-name",
        "-n",
        help="Display name (defaults to email local-part)",
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Create a new user."""
    config = _load_config(config_path)
    try:
        client = _get_user_client(config)
        with client:
            user = client.create_user(email=email, name=display_name)
    except DuplicateEmailError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]Failed to create user: {exc}[/red]")
        raise typer.Exit(1)

    table = Table(title="User created")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Email", style="green")
    table.add_row(str(user.id), user.name, user.email)
    console.print(table)


@app.command("list")
def list_users(
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """List all users."""
    config = _load_config(config_path)
    try:
        client = _get_user_client(config)
        with client:
            users = client.list_users()
    except Exception as exc:
        console.print(f"[red]Failed to list users: {exc}[/red]")
        raise typer.Exit(1)

    if not users:
        console.print(
            "[yellow]No users yet.[/yellow] "
            "Run [cyan]sow-admin users add <email>[/cyan] to create one."
        )
        return

    table = Table(title=f"Users ({len(users)})")
    table.add_column("ID", style="cyan", justify="right")
    table.add_column("Name", style="green")
    table.add_column("Email", style="green")
    table.add_column("Created", style="dim")
    for user in users:
        table.add_row(str(user.id), user.name, user.email, user.created_at or "")
    console.print(table)


@app.command("delete")
def delete_user(
    user_id: int = typer.Argument(..., help="User ID to delete"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Delete a user (CASCADE deletes their songsets, settings, etc.)."""
    config = _load_config(config_path)
    try:
        client = _get_user_client(config)
        with client:
            user = client.get_user(user_id)
            if user is None:
                console.print(f"[red]No user with id {user_id}[/red]")
                raise typer.Exit(1)

            if not yes:
                console.print(
                    f"About to delete [bold]{user.name}[/bold] "
                    f"({user.email}, id={user.id}).\n"
                    "[yellow]This will CASCADE delete their songsets, "
                    "songset_items, user_settings, user_lrc_override, "
                    "lyric_mark, songset_share rows, and Better Auth account/"
                    "session rows.[/yellow]"
                )
                _print_cascade_preview(client.preview_cascade_delete(user_id))
                confirm = typer.confirm("Continue?", default=False)
                if not confirm:
                    console.print("[dim]Cancelled.[/dim]")
                    raise typer.Exit(0)

            deleted = client.delete_user(user_id)
    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[red]Failed to delete user: {exc}[/red]")
        raise typer.Exit(1)

    if deleted:
        console.print(f"[green]Deleted user {user_id}[/green]")
    else:
        console.print(f"[red]No user with id {user_id}[/red]")
        raise typer.Exit(1)
