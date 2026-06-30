"""Read-only catalog access for the songset constructor POC."""

from __future__ import annotations

from typing import Any

import psycopg.rows

from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.db.connection import ConnectionProvider

from .models import ConstructorConfig, SongCandidate


CATALOG_QUERY = """
SELECT
    s.id AS song_id,
    s.title,
    s.album_name,
    s.album_series,
    s.composer,
    s.lyricist,
    s.musical_key AS song_key,
    s.lyrics_raw,
    r.hash_prefix AS recording_hash_prefix,
    r.tempo_bpm,
    COALESCE(r.musical_key, s.musical_key) AS musical_key,
    r.musical_mode,
    r.key_confidence
FROM songs s
JOIN recordings r ON r.song_id = s.id
WHERE s.deleted_at IS NULL
  AND r.deleted_at IS NULL
  AND r.analysis_status = 'completed'
  AND r.visibility_status = 'published'
  AND r.tempo_bpm IS NOT NULL
  AND COALESCE(r.musical_key, s.musical_key) IS NOT NULL
"""


def load_connection_provider() -> ConnectionProvider:
    config = AdminConfig.load()
    return ConnectionProvider(config.get_connection_url())


def fetch_catalog(provider: ConnectionProvider, config: ConstructorConfig) -> list[SongCandidate]:
    query = CATALOG_QUERY
    params: list[Any] = []
    if not config.include_cpw:
        query += " AND COALESCE(s.album_series, '') NOT ILIKE %s"
        params.append("%CPW%")
    if not config.include_dev:
        query += " AND COALESCE(s.album_series, '') NOT ILIKE %s"
        params.append("%DEV%")
    if config.album_series:
        query += " AND COALESCE(s.album_series, '') ILIKE %s"
        params.append(f"%{config.album_series}%")
    if config.season:
        query += " AND (s.title ILIKE %s OR s.album_name ILIKE %s OR s.lyrics_raw ILIKE %s)"
        season = f"%{config.season}%"
        params.extend([season, season, season])
    query += " ORDER BY r.imported_at DESC LIMIT %s"
    params.append(config.pool_limit)

    conn = provider.get_connection()
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
    return [_row_to_candidate(row) for row in rows]


def _row_to_candidate(row: dict[str, Any]) -> SongCandidate:
    warnings: list[str] = []
    if row.get("key_confidence") is not None and row["key_confidence"] < 0.6:
        warnings.append("low_key_confidence")
    mode = row.get("musical_mode") or "major"
    return SongCandidate(
        song_id=row["song_id"],
        title=row["title"],
        recording_hash_prefix=row["recording_hash_prefix"],
        bpm=float(row["tempo_bpm"]),
        musical_key=row["musical_key"],
        musical_mode=mode,
        key_confidence=row.get("key_confidence"),
        album_name=row.get("album_name"),
        album_series=row.get("album_series"),
        composer=row.get("composer"),
        lyricist=row.get("lyricist"),
        lyrics_raw=row.get("lyrics_raw"),
        source_warnings=warnings,
    )
