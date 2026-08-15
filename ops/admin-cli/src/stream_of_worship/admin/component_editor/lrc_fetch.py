"""LRC fetch service for the Component Metadata editor (v5).

Downloads LRC content from R2 for songs in the songset, caches locally,
and provides parallel pre-fetch + on-demand fallback.
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
        song_id: Song ID
        content: LRC file content as UTF-8 string, or None if no LRC exists in R2
        cached_path: Local cache path written, if any
        error: Error message if fetch failed
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
    """Download LRC for a single song from R2, cache locally.

    - Download content via r2.download_lrc_content(hash_prefix)
    - If no LRC exists in R2 -> return LRCFetch(content=None)
    - Write to {cache_dir}/{hash_prefix}/lrc/lyrics.lrc
    - Return LRCFetch with parsed content
    """
    try:
        content = r2_client.download_lrc_content(hash_prefix)
    except Exception as e:  # noqa: BLE001
        logger.warning("LRC download failed for %s: %s", hash_prefix, e)
        return LRCFetch(song_id=song_id, content=None, cached_path=None, error=str(e))

    if content is None:
        return LRCFetch(song_id=song_id, content=None, cached_path=None, error=None)

    cached_path = cache_dir / hash_prefix / "lrc" / "lyrics.lrc"
    try:
        cached_path.parent.mkdir(parents=True, exist_ok=True)
        cached_path.write_text(content, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning("LRC cache write failed for %s: %s", hash_prefix, e)
        cached_path = None

    return LRCFetch(
        song_id=song_id,
        content=content,
        cached_path=cached_path,
        error=None,
    )


async def prefetch_all_lrc(
    sessions: list["SongSession"],
    r2_client: R2Client,
    cache_dir: Path,
) -> dict[str, LRCFetch]:
    """Parallel prefetch of LRC for all songs in the songset.

    Uses asyncio.gather to fetch all in parallel.
    Returns song_id -> LRCFetch map.
    Individual fetch failures do not abort the batch — each song's
    error is captured in its own LRCFetch.error.
    """
    tasks = [
        fetch_lrc_for_song(session.song_id, session.hash_prefix, r2_client, cache_dir)
        for session in sessions
    ]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    return {fetch.song_id: fetch for fetch in results}
