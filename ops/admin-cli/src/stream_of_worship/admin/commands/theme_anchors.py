"""Theme-anchors commands for sow-admin.

Provides ``sow-admin theme-anchors sync`` to populate the ``theme_anchors``
table from the bundled JSON file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import typer
from rich.console import Console

from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.db.connection import ConnectionProvider

console = Console()
app = typer.Typer(help="Theme anchors operations")

THEME_ANCHORS_PATH = (
    Path(__file__).resolve().parents[1] / "songset_constructor" / "data" / "theme_anchors.json"
)


def _get_connection_provider(config: AdminConfig) -> ConnectionProvider:
    return ConnectionProvider(config.get_connection_url())


@app.command("sync")
def sync_theme_anchors(
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-insert even if 12 rows already exist",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Populate the theme_anchors table from the bundled JSON file.

    Reads ``songset_constructor/data/theme_anchors.json`` and upserts
    all 12 anchor vectors into the ``theme_anchors`` table.

    Without ``--force``, skips if 12 rows already exist with matching
    ``model_version``.
    """
    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    connection_provider = _get_connection_provider(config)
    conn = connection_provider.get_connection()
    cursor = conn.cursor()

    if not force:
        cursor.execute(
            "SELECT COUNT(*) FROM theme_anchors WHERE model_version = 'text-embedding-3-small'"
        )
        count = cursor.fetchone()[0]
        if count >= 12:
            console.print(
                f"[green]theme_anchors already has {count} rows (model_version=text-embedding-3-small). "
                "Use --force to re-insert.[/green]"
            )
            return

    if not THEME_ANCHORS_PATH.exists():
        console.print(
            f"[red]Theme anchors file not found: {THEME_ANCHORS_PATH}[/red]"
        )
        raise typer.Exit(1)

    import json

    payload = json.loads(THEME_ANCHORS_PATH.read_text(encoding="utf-8"))
    anchors = payload.get("anchors", {})
    model_version = payload.get("model_version", "text-embedding-3-small")

    if len(anchors) != 12:
        console.print(f"[red]Expected 12 anchors, got {len(anchors)}[/red]")
        raise typer.Exit(1)

    inserted = 0
    for theme, vector in anchors.items():
        vector_str = "[" + ",".join(str(v) for v in vector) + "]"
        cursor.execute(
            """
            INSERT INTO theme_anchors (theme, embedding, model_version)
            VALUES (%s, %s::vector, %s)
            ON CONFLICT (theme) DO UPDATE SET
                embedding = EXCLUDED.embedding,
                model_version = EXCLUDED.model_version,
                created_at = NOW()
            """,
            (theme, vector_str, model_version),
        )
        inserted += 1

    conn.commit()
    console.print(f"[green]Synced {inserted} theme anchors to database.[/green]")
