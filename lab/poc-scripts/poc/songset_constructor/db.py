"""Read-only catalog access for the songset constructor."""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Iterable

from sow_lab_app.config import AppConfig
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.db.schema import (
    RECORDING_COLUMN_COUNT,
    RECORDING_COLUMNS_FOR_JOIN,
    SONG_COLUMN_COUNT,
    SONG_COLUMNS_FOR_JOIN,
)
from stream_of_worship.db.app.read_client import ReadOnlyClient
from stream_of_worship.db.connection import ConnectionProvider

from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.models import SongCandidate
from poc.songset_constructor.rules.embeddings import parse_pgvector_text

POOL_QUERY = f"""
SELECT {SONG_COLUMNS_FOR_JOIN},
       {RECORDING_COLUMNS_FOR_JOIN},
       se.embedding::text AS song_embedding_text,
       se.model_version AS song_embedding_model
FROM songs s
JOIN recordings r ON s.id = r.song_id
LEFT JOIN song_embedding se ON se.song_id = s.id
WHERE r.visibility_status = 'published'
  AND r.analysis_status = 'completed'
  AND r.lrc_status = 'completed'
  AND r.deleted_at IS NULL
  AND s.deleted_at IS NULL
  AND s.album_series = ANY(%s)
ORDER BY s.title
LIMIT %s
"""

LINE_EMBEDDING_QUERY = """
SELECT song_id, line_index, embedding::text
FROM song_line_embedding
WHERE song_id = ANY(%s)
ORDER BY song_id, line_index
"""


def get_connection_url() -> str:
    if os.environ.get("SOW_DATABASE_URL"):
        return os.environ["SOW_DATABASE_URL"]
    return AppConfig.load().get_connection_url()


def build_read_client(connection_url: str | None = None) -> ReadOnlyClient:
    return ReadOnlyClient(ConnectionProvider(connection_url or get_connection_url()))


def _candidate_from_row(row: tuple) -> SongCandidate:
    song = Song.from_row(row[:SONG_COLUMN_COUNT])
    recording = Recording.from_row(row[SONG_COLUMN_COUNT : SONG_COLUMN_COUNT + RECORDING_COLUMN_COUNT])
    embedding_text = row[SONG_COLUMN_COUNT + RECORDING_COLUMN_COUNT]
    embedding = parse_pgvector_text(embedding_text)
    return SongCandidate(
        song_id=song.id,
        title=song.title,
        title_pinyin=song.title_pinyin,
        composer=song.composer,
        lyricist=song.lyricist,
        album_name=song.album_name,
        album_series=song.album_series,
        recording_hash_prefix=recording.hash_prefix,
        tempo_bpm=recording.tempo_bpm,
        musical_key=recording.musical_key or song.musical_key,
        musical_mode=recording.musical_mode,
        key_confidence=recording.key_confidence,
        loudness_db=recording.loudness_db,
        lyrics_raw=song.lyrics_raw,
        song_embedding=embedding.tolist() if embedding is not None else None,
        is_hymn=song.album_series == "HYMN",
    )


def fetch_catalog_pool(
    config: RunConfig,
    *,
    client: ReadOnlyClient | None = None,
) -> list[SongCandidate]:
    owns_client = client is None
    db_client = client or build_read_client()
    try:
        cursor = db_client.connection.cursor()
        cursor.execute(POOL_QUERY, (config.album_series, config.pool_limit))
        pool = [_candidate_from_row(tuple(row)) for row in cursor.fetchall()]
        line_embeddings = fetch_line_embeddings([candidate.song_id for candidate in pool], client=db_client)
        return [
            candidate.model_copy(update={"line_embeddings": line_embeddings.get(candidate.song_id, [])})
            for candidate in pool
        ]
    finally:
        if owns_client:
            db_client.close()


def fetch_line_embeddings(
    song_ids: Iterable[str],
    *,
    client: ReadOnlyClient,
) -> dict[str, list[list[float]]]:
    ids = list(dict.fromkeys(song_ids))
    if not ids:
        return {}
    cursor = client.connection.cursor()
    cursor.execute(LINE_EMBEDDING_QUERY, (ids,))
    grouped: dict[str, list[list[float]]] = defaultdict(list)
    for song_id, _line_index, embedding_text in cursor.fetchall():
        embedding = parse_pgvector_text(embedding_text)
        if embedding is not None:
            grouped[song_id].append(embedding.tolist())
    return dict(grouped)
