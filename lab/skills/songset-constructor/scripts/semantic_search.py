#!/usr/bin/env python3
"""Search for songs by theme, lyrics content, or natural language query.

Uses pgvector semantic similarity (if embedding API available) or keyword ILIKE search.

Usage:
    uv run --project ops/admin-cli --extra admin --extra constructor python semantic_search.py --query "感恩" --limit 20
    uv run --project ops/admin-cli --extra admin --extra constructor python semantic_search.py --query "cross" --field title --mode keyword
    uv run --project ops/admin-cli --extra admin --extra constructor python semantic_search.py --query "holy spirit" --mode auto
    uv run --project ops/admin-cli --extra admin --extra constructor python semantic_search.py --query "感恩" --album-series "敬拜讚美 (1)" --limit 5

Output: JSON array of matching songs to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ADMIN_CLI_SRC = PROJECT_ROOT / "ops" / "admin-cli" / "src"
if str(ADMIN_CLI_SRC) not in sys.path:
    sys.path.insert(0, str(ADMIN_CLI_SRC))


def main() -> None:
    parser = argparse.ArgumentParser(description="Semantic or keyword search for songs")
    parser.add_argument("--query", type=str, required=True, help="Search text")
    parser.add_argument("--limit", type=int, default=20, help="Maximum results")
    parser.add_argument(
        "--field",
        type=str,
        default=None,
        choices=["title", "lyrics", "composer", "all"],
        help="Field for keyword search (if omitted, uses semantic search)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="auto",
        choices=["semantic", "keyword", "auto"],
        help="Search mode: semantic (pgvector), keyword (ILIKE), or auto",
    )
    parser.add_argument(
        "--album-series",
        action="append",
        default=None,
        help='Filter by album series (e.g., "敬拜讚美 (1)"). Repeatable.',
    )
    args = parser.parse_args()
    album_series = args.album_series or []

    from stream_of_worship.admin.config import AdminConfig
    from stream_of_worship.admin.songset_constructor.rules.themes import THEME_VOCAB
    from stream_of_worship.db.app.read_client import ReadOnlyClient
    from stream_of_worship.db.connection import ConnectionProvider

    config = AdminConfig.load()
    connection_url = config.get_connection_url()
    provider = ConnectionProvider(connection_url)
    read_client = ReadOnlyClient(provider)

    try:
        if args.mode == "keyword" or (args.mode == "auto" and args.field):
            results = _keyword_search(read_client, args.query, args.field or "all", args.limit, album_series)
        elif args.mode == "semantic":
            results = _semantic_search(read_client, args.query, args.limit, album_series)
        else:  # auto
            results = _semantic_search(read_client, args.query, args.limit, album_series)
            if not results:
                results = _keyword_search(read_client, args.query, "all", args.limit, album_series)

        json.dump(results, sys.stdout, ensure_ascii=False, indent=2)
    finally:
        provider.close()


def _keyword_search(
    read_client: ReadOnlyClient,
    query: str,
    field: str,
    limit: int,
    album_series: list[str],
) -> list[dict]:
    """Keyword search using ReadOnlyClient.search_songs (ILIKE)."""
    songs = read_client.search_songs(query, field=field, limit=limit)
    results = []
    for song in songs:
        if album_series and song.album_series not in album_series:
            continue
        recording = read_client.get_recording_by_song_id(song.id)
        results.append(
            {
                "song_id": song.id,
                "title": song.title,
                "title_pinyin": song.title_pinyin,
                "recording_hash_prefix": recording.hash_prefix if recording else None,
                "tempo_bpm": recording.tempo_bpm if recording else None,
                "musical_key": recording.musical_key if recording else song.musical_key,
                "musical_mode": recording.musical_mode if recording else None,
                "album_name": song.album_name,
                "album_series": song.album_series,
                "score": 1.0,
                "match_type": "keyword",
            }
        )
    return results


def _semantic_search(
    read_client: ReadOnlyClient,
    query: str,
    limit: int,
    album_series: list[str],
) -> list[dict]:
    """Semantic search using pgvector or theme-vocab fallback."""
    import os

    api_key = os.environ.get("SOW_EMBEDDING_API_KEY")
    base_url = os.environ.get("SOW_EMBEDDING_BASE_URL")

    if api_key and base_url:
        results = _pgvector_search(read_client, query, limit, api_key, base_url, album_series)
        if results:
            return results

    # Fallback: match query against THEME_VOCAB keywords, then query songs with high theme scores
    return _theme_vocab_search(read_client, query, limit, album_series)


def _pgvector_search(
    read_client: ReadOnlyClient,
    query: str,
    limit: int,
    api_key: str,
    base_url: str,
    album_series: list[str],
) -> list[dict]:
    """Generate embedding for query and search via pgvector cosine distance."""
    try:
        import urllib.request
        import json as _json

        data = _json.dumps({"model": "text-embedding-3-small", "input": query}).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/embeddings",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_data = _json.loads(resp.read().decode("utf-8"))
        embedding = resp_data["data"][0]["embedding"]
    except Exception as e:
        print(f"Warning: Embedding generation failed: {e}", file=sys.stderr)
        return []

    conn = read_client.connection
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT s.id, s.title, s.title_pinyin, s.album_name, s.album_series,
               s.musical_key, r.hash_prefix, r.tempo_bpm, r.musical_mode,
               1 - (se.embedding <=> %s::vector) AS score
        FROM songs s
        JOIN recordings r ON s.id = r.song_id
        LEFT JOIN song_embedding se ON se.song_id = s.id
        WHERE se.embedding IS NOT NULL
          AND r.visibility_status IN ('published', 'review')
          AND r.deleted_at IS NULL
          AND s.deleted_at IS NULL
          AND (cardinality(%s::text[]) = 0 OR s.album_series = ANY(%s))
        ORDER BY se.embedding <=> %s::vector
        LIMIT %s
        """,
        (str(embedding), album_series, album_series, str(embedding), limit),
    )
    rows = cursor.fetchall()

    results = []
    for row in rows:
        results.append(
            {
                "song_id": row[0],
                "title": row[1],
                "title_pinyin": row[2],
                "recording_hash_prefix": row[6],
                "tempo_bpm": row[7],
                "musical_key": row[5],
                "musical_mode": row[8],
                "album_name": row[3],
                "album_series": row[4],
                "score": round(float(row[9]), 4) if row[9] is not None else 0.0,
                "match_type": "semantic",
            }
        )
    return results


def _theme_vocab_search(
    read_client: ReadOnlyClient,
    query: str,
    limit: int,
    album_series: list[str],
) -> list[dict]:
    """Match query against THEME_VOCAB to identify themes, then find songs with high theme scores."""
    from stream_of_worship.admin.songset_constructor.rules.themes import THEME_VOCAB

    query_lower = query.lower()
    matched_themes: list[str] = []
    for theme, terms in THEME_VOCAB.items():
        for term in terms:
            if term.lower() in query_lower or query_lower in term.lower():
                matched_themes.append(theme)
                break

    if not matched_themes:
        # Fall back to keyword search
        return _keyword_search(read_client, query, "all", limit, album_series)

    conn = read_client.connection
    cursor = conn.cursor()

    # Query songs with high theme scores for matched themes via pgvector
    cursor.execute(
        """
        SELECT s.id, s.title, s.title_pinyin, s.album_name, s.album_series,
               s.musical_key, r.hash_prefix, r.tempo_bpm, r.musical_mode,
               MAX(1 - (se.embedding <=> ta.embedding)) AS score
        FROM songs s
        JOIN recordings r ON s.id = r.song_id
        LEFT JOIN song_embedding se ON se.song_id = s.id
        CROSS JOIN theme_anchors ta
        WHERE ta.theme = ANY(%s)
          AND se.embedding IS NOT NULL
          AND r.visibility_status IN ('published', 'review')
          AND r.deleted_at IS NULL
          AND s.deleted_at IS NULL
          AND (cardinality(%s::text[]) = 0 OR s.album_series = ANY(%s))
        GROUP BY s.id, s.title, s.title_pinyin, s.album_name, s.album_series,
                 s.musical_key, r.hash_prefix, r.tempo_bpm, r.musical_mode
        ORDER BY score DESC
        LIMIT %s
        """,
        (matched_themes, album_series, album_series, limit),
    )
    rows = cursor.fetchall()

    results = []
    for row in rows:
        results.append(
            {
                "song_id": row[0],
                "title": row[1],
                "title_pinyin": row[2],
                "recording_hash_prefix": row[6],
                "tempo_bpm": row[7],
                "musical_key": row[5],
                "musical_mode": row[8],
                "album_name": row[3],
                "album_series": row[4],
                "score": round(float(row[9]), 4) if row[9] is not None else 0.0,
                "match_type": "theme_vocab",
                "matched_themes": matched_themes,
            }
        )
    return results


if __name__ == "__main__":
    main()
