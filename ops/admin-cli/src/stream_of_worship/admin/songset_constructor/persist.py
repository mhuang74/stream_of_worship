"""Atomic persistence of constructed songsets."""

from __future__ import annotations

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn

from stream_of_worship.admin.songset_constructor.config import RunConfig
from stream_of_worship.admin.songset_constructor.models import SongsetProposal
from stream_of_worship.db.app.songset_client import SongsetClient


def persist_proposals(
    config: RunConfig,
    proposals: list[SongsetProposal],
    songset_client: SongsetClient,
) -> list[str]:
    created_ids: list[str] = []
    total = len(proposals)

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
                songset = songset_client.create_songset_with_items(
                    name=name,
                    description=description,
                    items=items,
                )
                created_ids.append(songset.id)
                Console().print(f"[green]Created songset {songset.id} (rank {proposal.rank})[/green]")
            except Exception as e:
                Console().print(f"[red]Failed to save rank {proposal.rank}: {e}[/red]")
            progress.advance(task)
    return created_ids
