"""Atomic persistence of constructed songsets."""

from __future__ import annotations

from stream_of_worship.admin.songset_constructor.config import RunConfig
from stream_of_worship.admin.songset_constructor.models import SongsetProposal
from stream_of_worship.db.app.songset_client import MissingReferenceError, SongsetClient


def create_songset_with_items(
    client: SongsetClient,
    name: str,
    description: str,
    items: list[dict],
) -> str:
    songset = client.create_songset(name=name, description=description)
    for item in items:
        client.add_item(
            songset_id=songset.id,
            song_id=item["song_id"],
            recording_hash_prefix=item.get("recording_hash_prefix"),
            position=item.get("position", 0),
            gap_beats=item.get("gap_beats", 2.0),
            crossfade_enabled=item.get("crossfade_enabled", False),
            crossfade_duration_seconds=item.get("crossfade_duration_seconds"),
            key_shift_semitones=item.get("key_shift_semitones", 0),
            tempo_ratio=item.get("tempo_ratio", 1.0),
        )
    return songset.id


def persist_proposals(
    config: RunConfig,
    proposals: list[SongsetProposal],
    songset_client: SongsetClient,
) -> list[str]:
    created_ids: list[str] = []
    total = len(proposals)
    from rich.progress import BarColumn, Progress, TextColumn

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
    ) as progress:
        task = progress.add_task(f"Saving {total} songset(s)...", total=total)
        for proposal in proposals:
            name = f"Constructed rank {proposal.rank}/{total} ({len(proposal.items)}-song)"
            description = (proposal.rationale or "")[:200] or f"Songset constructed via beam search (rank {proposal.rank})"
            items = []
            for item in proposal.items:
                items.append({
                    "song_id": item.song_id,
                    "recording_hash_prefix": item.recording_hash_prefix,
                    "position": item.position,
                    "gap_beats": item.gap_beats,
                    "crossfade_enabled": item.crossfade_enabled,
                    "crossfade_duration_seconds": item.crossfade_duration_seconds,
                    "key_shift_semitones": item.key_shift_semitones,
                    "tempo_ratio": item.tempo_ratio,
                })
            try:
                songset_id = create_songset_with_items(
                    client=songset_client,
                    name=name,
                    description=description,
                    items=items,
                )
                created_ids.append(songset_id)
                from rich.console import Console
                Console().print(f"[green]Created songset {songset_id} (rank {proposal.rank})[/green]")
            except MissingReferenceError as e:
                from rich.console import Console
                Console().print(f"[red]Missing reference for rank {proposal.rank}: {e}[/red]")
            except Exception as e:
                from rich.console import Console
                Console().print(f"[red]Failed to save rank {proposal.rank}: {e}[/red]")
            progress.advance(task)
    return created_ids
