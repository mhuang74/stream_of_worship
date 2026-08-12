"""LRC fetch service for the Component Metadata editor.

Downloads LRC (synchronized lyrics) files from R2 for the songs in the
editor's songset, caches them locally, and exposes the parsed content.

The cache layout mirrors the audio cache layout:
    {cache_dir}/{hash_prefix}/audio/lyrics.lrc

Pre-fetch (parallel) and on-demand (single song) fetch paths are both
supported. Individual fetch failures do not abort the batch — each song's
error is captured in its own ``LRCFetch.error``.
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from stream_of_worship.admin.services.r2 import R2Client

if TYPE_CHECKING:
    from stream_of_worship.admin.component_editor.state import SongSession

logger = logging.getLogger(__name__)


@dataclass
class LRCFetch:
    """Result of an LRC fetch for a single song.

    Attributes:
        song_id: Song ID the fetch was for.
        content: Parsed LRC content string, or None if no LRC exists in R2
            (or the fetch failed).
        cached_path: Local cache path written, if any.
        error: Error message if the fetch failed, None otherwise.
    """

    song_id: str
    content: str | None
    cached_path: Path | None
    error: str | None


async def fetch_lrc_for_song(
    song_id: str,
    hash_prefix: str,
    r2_client: R2Client,
    cache_dir: Path,
) -> LRCFetch:
    """Download LRC for a single song from R2 and cache locally.

    - Resolve LRC identity via ``r2_client.get_lrc_identity(hash_prefix)``.
    - If no LRC exists in R2 → return ``LRCFetch(content=None)``.
    - Download content via ``r2_client.download_lrc_content()``.
    - Write to ``{cache_dir}/{hash_prefix}/audio/lyrics.lrc``
      (same directory as ``audio.mp3``).
    - Return ``LRCFetch`` with parsed content.

    Args:
        song_id: Song ID (used for the result record / map key).
        hash_prefix: Recording hash prefix (R2 key namespace).
        r2_client: R2 client.
        cache_dir: Local cache root directory.

    Returns:
        ``LRCFetch`` with content / cached_path / error populated.
    """
    try:
        identity = await asyncio.to_thread(r2_client.get_lrc_identity, hash_prefix)
    except Exception as exc:
        logger.warning("LRC identity lookup failed for %s: %s", song_id, exc)
        return LRCFetch(song_id=song_id, content=None, cached_path=None, error=str(exc))

    if not identity.exists:
        return LRCFetch(song_id=song_id, content=None, cached_path=None, error=None)

    try:
        content = await asyncio.to_thread(r2_client.download_lrc_content, hash_prefix)
    except Exception as exc:
        logger.warning("LRC content download failed for %s: %s", song_id, exc)
        return LRCFetch(song_id=song_id, content=None, cached_path=None, error=str(exc))

    if content is None:
        return LRCFetch(song_id=song_id, content=None, cached_path=None, error=None)

    cache_path = cache_dir / hash_prefix / "audio" / "lyrics.lrc"
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(content, encoding="utf-8")
    except Exception as exc:
        logger.warning("LRC cache write failed for %s: %s", song_id, exc)
        return LRCFetch(
            song_id=song_id,
            content=content,
            cached_path=None,
            error=str(exc),
        )

    return LRCFetch(
        song_id=song_id,
        content=content,
        cached_path=cache_path,
        error=None,
    )


async def prefetch_all_lrc(
    song_sessions: list["SongSession"],
    r2_client: R2Client,
    cache_dir: Path,
) -> dict[str, LRCFetch]:
    """Parallel prefetch of LRC for all songs in the songset.

    Uses ``asyncio.gather`` to fetch all in parallel. Returns a
    ``song_id -> LRCFetch`` map. Individual fetch failures do not abort
    the batch — each song's error is captured in its own ``LRCFetch.error``.

    Args:
        song_sessions: List of song sessions to prefetch LRC for.
        r2_client: R2 client.
        cache_dir: Local cache root directory.

    Returns:
        Mapping of ``song_id`` to ``LRCFetch`` result.
    """
    tasks = [
        fetch_lrc_for_song(session.song_id, session.hash_prefix, r2_client, cache_dir)
        for session in song_sessions
    ]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    return {fetch.song_id: fetch for fetch in results}
