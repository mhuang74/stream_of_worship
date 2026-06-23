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
