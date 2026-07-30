"""Songset commands for sow-admin.

Provides CLI commands for listing songsets with their items, songs, and recordings.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

import typer
from rich.console import Console
from rich.table import Table

from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.db.app.songset_client import SongsetClient
from stream_of_worship.db.connection import ConnectionProvider
from stream_of_worship.db.user_client import UserClient

console = Console()
app = typer.Typer(help="Songset operations")


def _get_connection_provider(config: AdminConfig) -> ConnectionProvider:
    """Get a ConnectionProvider from config.

    Args:
        config: Admin configuration.

    Returns:
        ConnectionProvider instance.
    """
    return ConnectionProvider(config.get_connection_url())


def _resolve_owner_emails(
    connection_provider: ConnectionProvider, songsets: list
) -> dict[int, str]:
    """Resolve user emails for a list of songsets in one round-trip.

    Args:
        connection_provider: DB connection provider.
        songsets: List of Songset objects.

    Returns:
        Dict mapping user_id to email string.
    """
    user_ids = list({s.user_id for s in songsets})
    if not user_ids:
        return {}

    result: dict[int, str] = {}
    with connection_provider.get_connection().cursor() as cursor:
        cursor.execute(
            'SELECT "id", "email" FROM "user" WHERE "id" = ANY(%s)',
            (user_ids,),
        )
        for row in cursor.fetchall():
            result[row[0]] = row[1]
    return result


@app.command("list")
def list_songsets(
    user: str | None = typer.Option(
        None,
        "--user",
        "-u",
        help="Filter songsets to one user, resolved by email",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        "-l",
        help="Cap the number of songsets returned",
    ),
    format: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format (table|ids)",
    ),
    config_path: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """List songsets with their items.

    Display songsets and their constituent songs with key/BPM/duration info.
    With no flags, lists every songset across all users. Use --user to filter
    by owner email. Orphaned items (no matching songs row) render dashes.
    """
    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    connection_provider = _get_connection_provider(config)
    songset_client = SongsetClient(connection_provider, user_id=0)

    try:
        # Resolve user filter if provided
        if user is not None:
            user_client = UserClient(connection_provider)
            resolved_user = user_client.get_user_by_email(user)
            if resolved_user is None:
                console.print(f"[red]User not found: {user}[/red]")
                raise typer.Exit(1)
            songsets = songset_client.list_songsets_for_user_id(resolved_user.id, limit=limit)
        else:
            songsets = songset_client.list_all_songsets(limit=limit)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error listing songsets: {e}[/red]")
        raise typer.Exit(1)

    if not songsets:
        console.print("[yellow]No songsets found.[/yellow]")
        return

    # Short-circuit ids format before the owner/items round-trips
    if format == "ids":
        for songset in songsets:
            console.print(songset.id)
        return

    # Resolve owner emails in one round-trip
    owner_emails = _resolve_owner_emails(connection_provider, songsets)

    # Batch-fetch items with song/recording data
    songset_ids = [s.id for s in songsets]
    items_by_songset = songset_client.list_songset_items_with_song_recording(songset_ids)

    # Table format: one row per songset item
    total_items = sum(len(items) for items in items_by_songset.values())
    table = Table(title=f"Songsets ({len(songsets)} total, {total_items} items)")
    table.add_column("Songset", style="green")
    table.add_column("Owner", style="cyan")
    table.add_column("#", style="dim", justify="right")
    table.add_column("Title", style="cyan")
    table.add_column("Duration", style="yellow")
    table.add_column("Key", style="magenta")
    table.add_column("BPM", style="white")
    table.add_column("Song ID", style="dim")

    current_songset: str | None = None

    for songset in songsets:
        items = items_by_songset.get(songset.id, [])

        # Print section separator when songset changes
        if current_songset is not None:
            table.add_section()

        current_songset = songset.id
        owner_email = owner_emails.get(songset.user_id, "?")

        if not items:
            # Empty songset: one row with dashes
            table.add_row(
                songset.name,
                owner_email,
                "-",
                "(no songs)",
                "--:--",
                "-",
                "--",
                "-",
            )
        else:
            for item in items:
                position_str = str(item.position + 1)
                title = item.song_title or "(missing)"
                duration = item.formatted_duration if item.song_title else "--:--"
                key = item.display_key if item.song_title else "-"
                bpm = str(round(item.tempo_bpm)) if item.tempo_bpm is not None else "--"
                song_id = item.song_id

                table.add_row(
                    songset.name,
                    owner_email,
                    position_str,
                    title,
                    duration,
                    key,
                    bpm,
                    song_id,
                )

    console.print(table)


# ---------------------------------------------------------------------------
# construct subcommand — lazily loaded songset_constructor subpackage
# ---------------------------------------------------------------------------


def _import_constructor():
    try:
        from stream_of_worship.admin.songset_constructor.cache import try_load_pool  # noqa: F401
        from stream_of_worship.admin.songset_constructor.config import RunConfig  # noqa: F401
        from stream_of_worship.admin.songset_constructor.db import (  # noqa: F401
            check_theme_anchors,
            fetch_catalog_pool,
        )
        from stream_of_worship.admin.songset_constructor.diagnose import (
            assemble_report_sections,  # noqa: F401
        )
        from stream_of_worship.admin.songset_constructor.graph.builder import (
            build_graph,  # noqa: F401
        )
        from stream_of_worship.admin.songset_constructor.persist import (
            persist_proposals,  # noqa: F401
        )
        from stream_of_worship.admin.songset_constructor.runner import run  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "constructor extra not installed. Run: "
            "`uv sync --project ops/admin-cli --extra admin --extra constructor`"
        ) from exc


def _parse_relax(value: str) -> dict:
    """Parse --relax token string into a dict of relax_* overrides."""
    result: dict = {}
    tokens = [t.strip() for t in value.split(",") if t.strip()]
    for token in tokens:
        if ":" in token:
            key, val = token.split(":", 1)
            key = key.strip()
            val = val.strip()
        else:
            key = token
            val = None
        if key == "h1":
            result["relax_h1"] = True
        elif key == "h2":
            try:
                result["relax_h2_bpm"] = int(val) if val else None
            except ValueError:
                console.print(f"[red]Invalid relax value for h2: '{val}' (expected integer)[/red]")
                raise typer.Exit(1)
        elif key == "h3":
            try:
                result["relax_h3_bpm"] = int(val) if val else None
            except ValueError:
                console.print(f"[red]Invalid relax value for h3: '{val}' (expected integer)[/red]")
                raise typer.Exit(1)
        elif key == "h4":
            if val:
                result["relax_h4"] = True
                try:
                    result["relax_h4_bpm"] = int(val)
                except ValueError:
                    console.print(f"[red]Invalid relax value for h4: '{val}' (expected integer)[/red]")
                    raise typer.Exit(1)
            else:
                result["relax_h4"] = True
        elif key == "h5":
            if val:
                result["relax_h5"] = True
                try:
                    result["relax_h5_cfd"] = int(val)
                except ValueError:
                    console.print(f"[red]Invalid relax value for h5: '{val}' (expected integer)[/red]")
                    raise typer.Exit(1)
            else:
                result["relax_h5"] = True
        else:
            console.print(f"[yellow]Unknown relax token: {token}[/yellow]")
    return result


def _load_constraints_file(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
            return yaml.safe_load(raw) or {}
        except ImportError:
            console.print("[red]PyYAML not installed. Install with `uv add pyyaml`.[/red]")
            raise typer.Exit(1)
    return json.loads(raw)


@app.command("construct")
def construct_songset(
    user: str = typer.Option(
        ...,
        "--user",
        help="Email of the user to own the constructed songsets",
    ),
    count: int = typer.Option(
        3,
        "--count",
        "-n",
        help="Number of songs per songset (2-5)",
        min=2,
        max=5,
    ),
    proposals: int = typer.Option(
        3,
        "--proposals",
        "-k",
        help="Number of proposals to generate (1-20)",
        min=1,
        max=20,
    ),
    pool: int = typer.Option(
        200,
        "--pool",
        "-p",
        help="Maximum pool size",
        min=4,
    ),
    album_series: list[str] | None = typer.Option(
        None,
        "--album-series",
        help="Filter by album series (can be specified multiple times)",
    ),
    include_cpw: bool = typer.Option(
        False,
        "--include-cpw/--no-include-cpw",
        help="Include CPW album series",
    ),
    intimate: bool = typer.Option(
        False,
        "--intimate/--no-intimate",
        help="Intimate mode (lower closing BPM)",
    ),
    hymnal_mode: bool = typer.Option(
        False,
        "--hymnal-mode/--no-hymnal-mode",
        help="Hymnal mode",
    ),
    season: str | None = typer.Option(
        None,
        "--season",
        help="Seasonal bias (advent, christmas, lent, easter, pentecost)",
    ),
    llm: bool = typer.Option(
        False,
        "--llm/--no-llm",
        help="Enable LLM-based planning",
    ),
    llm_judge: bool = typer.Option(
        False,
        "--llm-judge/--no-llm-judge",
        help="Enable LLM judge",
    ),
    llm_model: str | None = typer.Option(
        None,
        "--llm-model",
        help="LLM model name",
    ),
    relax: str | None = typer.Option(
        None,
        "--relax",
        help="Relax syntax: comma-separated tokens like h2:90,h3:80,h4,h5:3",
    ),
    constraints_file: Path | None = typer.Option(
        None,
        "--constraints-file",
        help="YAML/JSON file with relax overrides",
        exists=True,
    ),
    report: bool = typer.Option(
        False,
        "--report",
        help="Write diagnose_report.md",
    ),
    report_dir: Path | None = typer.Option(
        None,
        "--report-dir",
        help="Report output directory",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Skip DB writes",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Auto-save without prompting",
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Bypass pool cache",
    ),
    cache_dir: Path | None = typer.Option(
        None,
        "--cache-dir",
        help="Cache directory",
    ),
    cache_ttl: float = typer.Option(
        24.0,
        "--cache-ttl",
        help="Cache TTL in hours (0 disables cache)",
    ),
    config_path: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Construct songsets using the songset-constructor graph.

    Runs beam search + optional LLM planning to generate songset proposals.
    Requires the ``constructor`` extra and the ``theme_anchors`` table
    to be populated (run ``sow-admin theme-anchors sync`` first).
    """
    load_dotenv("/opt/sow/.env", override=False)

    _import_constructor()

    from stream_of_worship.admin.songset_constructor.config import RunConfig
    from stream_of_worship.admin.songset_constructor.db import check_theme_anchors
    from stream_of_worship.admin.songset_constructor.persist import persist_proposals
    from stream_of_worship.admin.songset_constructor.report_writer import write_report
    from stream_of_worship.admin.songset_constructor.runner import run

    # Resolve relax overrides
    relax_overrides: dict = {}
    if constraints_file:
        relax_overrides.update(_load_constraints_file(constraints_file))
    if relax:
        relax_overrides.update(_parse_relax(relax))

    # Filter unknown keys from constraints-file against RunConfig fields
    if relax_overrides:
        from stream_of_worship.admin.songset_constructor.config import RunConfig as _RC
        valid_keys = set(_RC.__dataclass_fields__.keys())
        unknown = {k: v for k, v in relax_overrides.items() if k not in valid_keys}
        for k in unknown:
            console.print(f"[yellow]Unknown constraints key '{k}' — ignored.[/yellow]")
            relax_overrides.pop(k)

    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    connection_provider = ConnectionProvider(config.get_connection_url())

    # Step 1 — Resolve user
    console.print(f"Resolving user [cyan]{user}[/cyan] ... ", end="")
    user_client = UserClient(connection_provider)
    resolved_user = user_client.get_user_by_email(user)
    if resolved_user is None:
        console.print("[red]not found[/red]")
        console.print(f"[red]User not found: {user}[/red]")
        raise typer.Exit(1)
    console.print("[green]done[/green]")

    # Step 2 — Build ReadOnlyClient
    from stream_of_worship.db.app.read_client import ReadOnlyClient

    read_client = ReadOnlyClient(connection_provider)

    # Step 3 — Validate theme_anchors table
    from stream_of_worship.admin.songset_constructor.db import ThemeAnchorsTableMissing

    console.print("Checking theme_anchors ... ", end="")
    try:
        anchor_count = check_theme_anchors(read_client)
    except ThemeAnchorsTableMissing:
        console.print("[red]missing[/red]")
        console.print(
            "[red]theme_anchors table does not exist. "
            "Run: sow-admin db init && sow-admin theme-anchors sync[/red]"
        )
        raise typer.Exit(1)
    if anchor_count != 12:
        console.print(f"[red]{anchor_count}/12[/red]")
        console.print(
            f"[red]theme_anchors table has {anchor_count} rows (expected 12). "
            "Run: sow-admin theme-anchors sync[/red]"
        )
        raise typer.Exit(1)
    console.print(f"[green]{anchor_count}/12[/green]")

    # Step 4 — Build RunConfig
    try:
        run_config = RunConfig(
            count=count,
            proposals=proposals,
            pool=pool,
            album_series=album_series or [],
            include_cpw=include_cpw,
            intimate=intimate,
            hymnal_mode=hymnal_mode,
            season=season,
            llm_enabled=llm,
            llm_judge=llm_judge,
            llm_model=llm_model,
            use_cache=not no_cache and cache_ttl > 0,
            cache_dir=cache_dir or Path.home() / ".cache" / "sow" / "songset_constructor",
            cache_ttl=cache_ttl,
            output_dir=report_dir if report else None,
            interactive_review=False,
            only_evaluate_pool_enrichment=False,
            **relax_overrides,
        )

        run_config.validate_environment()
    except (ValueError, RuntimeError, TypeError) as e:
        console.print(f"[red]Configuration error: {e}[/red]")
        raise typer.Exit(1)

    # Step 5 — Run graph
    console.print("Constructing songsets ...")
    result = run(run_config, read_client)

    proposals = result.get("final_proposals", [])

    # Step 6 — Print summary
    console.print(f"[dim]Generated {len(proposals)} proposals[/dim]")
    if proposals:
        table = Table(title=f"Proposals ({len(proposals)} total)")
        table.add_column("Rank", style="green", justify="right")
        table.add_column("Score", style="cyan")
        table.add_column("Sequence")
        table.add_column("BPM/Key Arc")
        table.add_column("Warnings", style="yellow")
        for p in proposals:
            seq = " → ".join(f"{i.title}(P{i.phase})" for i in p.items)
            bpms = " → ".join(str(round(i.bpm)) if i.bpm else "?" for i in p.items)
            keys = " → ".join(f"{i.key or '?'}" for i in p.items)
            warnings = ", ".join(p.hard_constraint_warnings) or "—"
            table.add_row(
                str(p.rank),
                f"{p.score.total:.4f}",
                seq,
                f"{keys} | {bpms} BPM",
                warnings,
            )
        console.print(table)
    else:
        console.print("[yellow]No valid proposals generated.[/yellow]")

    # Step 7 — Report
    if report:
        from stream_of_worship.admin.songset_constructor.config import DEFAULT_REPORT_DIR

        report_path = write_report(
            run_config,
            result,
            report_dir or DEFAULT_REPORT_DIR / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        )
        console.print(f"[dim]Report written to: {report_path}[/dim]")

    # Step 8 — Save flow
    if dry_run:
        console.print("[yellow]Dry run: skipping DB writes.[/yellow]")
        return

    if not yes:
        confirmed = typer.confirm(
            f"Save {len(proposals)} songset(s) to user {user}?",
            default=False,
        )
        if not confirmed:
            console.print("[yellow]Aborted.[/yellow]")
            return

    if proposals:
        songset_client = SongsetClient(connection_provider, user_id=resolved_user.id)
        created = persist_proposals(run_config, proposals, songset_client)
        if created:
            console.print(f"\n[green]Created {len(created)} songset(s).[/green]")
        if len(created) < len(proposals):
            failed = len(proposals) - len(created)
            console.print(f"[red]{failed} proposal(s) failed to save.[/red]")
            raise typer.Exit(1)
