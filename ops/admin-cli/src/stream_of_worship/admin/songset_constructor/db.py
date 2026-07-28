"""Read-only catalog access with in-DB theme scoring."""

from __future__ import annotations

import json

from stream_of_worship.admin.songset_constructor.config import RunConfig
from stream_of_worship.admin.songset_constructor.models import SongCandidate
from stream_of_worship.db.app.read_client import ReadOnlyClient

CONSTRUCTOR_SONG_COLUMNS = (
    "s.id, s.title, s.title_pinyin, s.composer, s.lyricist, "
    "s.album_name, s.album_series, s.musical_key, s.lyrics_raw"
)
CONSTRUCTOR_RECORDING_COLUMNS = (
    "r.hash_prefix, r.tempo_bpm, r.musical_key AS r_musical_key, "
    "r.musical_mode, r.key_confidence, r.loudness_db"
)

POOL_QUERY = f"""
SELECT {CONSTRUCTOR_SONG_COLUMNS},
       {CONSTRUCTOR_RECORDING_COLUMNS},
       (
           SELECT json_object_agg(ta.theme, 1 - (se.embedding <=> ta.embedding))
           FROM theme_anchors ta
       ) AS song_theme_scores_raw
FROM songs s
JOIN recordings r ON s.id = r.song_id
LEFT JOIN song_embedding se ON se.song_id = s.id
WHERE r.visibility_status IN ('published', 'review')
  AND (r.lrc_status = 'completed' OR r.r2_lrc_url IS NOT NULL)
  AND r.deleted_at IS NULL
  AND s.deleted_at IS NULL
  AND (cardinality(%s::text[]) = 0 OR s.album_series = ANY(%s))
ORDER BY s.title
LIMIT %s
"""

LINE_THEME_QUERY = """
SELECT song_id,
       json_object_agg(theme, max_cosine) AS line_theme_scores_raw
FROM (
    SELECT sle.song_id,
           ta.theme,
           MAX(1 - (sle.embedding <=> ta.embedding)) AS max_cosine
    FROM song_line_embedding sle
    CROSS JOIN theme_anchors ta
    WHERE sle.song_id = ANY(%s)
    GROUP BY sle.song_id, ta.theme
) sub
GROUP BY song_id
"""

THEME_ANCHORS_COUNT_QUERY = "SELECT COUNT(*) FROM theme_anchors"


def _candidate_from_row(row: tuple) -> SongCandidate:
    raw_scores = row[15]
    song_theme_scores_raw = json.loads(raw_scores) if raw_scores else {}
    return SongCandidate(
        song_id=row[0],
        title=row[1],
        title_pinyin=row[2],
        composer=row[3],
        lyricist=row[4],
        album_name=row[5],
        album_series=row[6],
        musical_key=row[11] or row[7],
        recording_hash_prefix=row[9],
        tempo_bpm=row[10],
        musical_mode=row[12],
        key_confidence=row[13],
        loudness_db=row[14],
        lyrics_raw=row[8],
        song_theme_scores_raw=song_theme_scores_raw,
        is_hymn=row[6] == "HYMN",
    )


def fetch_catalog_pool(config: RunConfig, *, client: ReadOnlyClient) -> list[SongCandidate]:
    cursor = client.connection.cursor()
    cursor.execute(POOL_QUERY, (config.album_series, config.album_series, config.pool))
    pool = [_candidate_from_row(tuple(row)) for row in cursor.fetchall()]
    song_ids = [c.song_id for c in pool]
    line_scores = fetch_line_theme_scores(song_ids, client=client)
    return [
        candidate.model_copy(update={"line_theme_scores_raw": line_scores.get(candidate.song_id, {})})
        for candidate in pool
    ]


def fetch_line_theme_scores(song_ids: list[str], *, client: ReadOnlyClient) -> dict[str, dict[str, float]]:
    if not song_ids:
        return {}
    cursor = client.connection.cursor()
    cursor.execute(LINE_THEME_QUERY, (song_ids,))
    result: dict[str, dict[str, float]] = {}
    for song_id, scores_json in cursor.fetchall():
        result[song_id] = json.loads(scores_json) if scores_json else {}
    return result


def check_theme_anchors(client: ReadOnlyClient) -> int:
    cursor = client.connection.cursor()
    cursor.execute(THEME_ANCHORS_COUNT_QUERY)
    row = cursor.fetchone()
    return row[0] if row else 0
