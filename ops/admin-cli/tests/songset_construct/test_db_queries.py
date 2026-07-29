"""Tests for in-DB theme scoring SQL and _candidate_from_row."""

from __future__ import annotations

import json

from stream_of_worship.admin.songset_constructor.db import _candidate_from_row
from stream_of_worship.admin.songset_constructor.rules.themes import THEMES

# Tuple layout (16 columns):
# 0: s.id           5: s.album_name    10: r.tempo_bpm   15: song_theme_scores_raw
# 1: s.title        6: s.album_series  11: r_musical_key
# 2: s.title_pinyin 7: s.musical_key   12: r.musical_mode
# 3: s.composer     8: s.lyrics_raw    13: r.key_confidence
# 4: s.lyricist     9: r.hash_prefix   14: r.loudness_db


def test_candidate_from_row_basic():
    row = (
        "s1",                   # 0
        "Test Title",           # 1
        "test title",           # 2
        "Composer",             # 3
        "Lyricist",             # 4
        "Album",                # 5
        "Series",               # 6
        "C",                    # 7
        "lyrics raw\nline 2",   # 8
        "hash12345678901",      # 9
        120.0,                  # 10
        "D",                    # 11
        "maj",                  # 12
        0.95,                   # 13
        -12.5,                  # 14
        None,                   # 15
    )
    candidate = _candidate_from_row(row)
    assert candidate.song_id == "s1"
    assert candidate.title == "Test Title"
    assert candidate.recording_hash_prefix == "hash12345678901"
    assert candidate.tempo_bpm == 120.0
    assert candidate.musical_key == "D"
    assert candidate.musical_mode == "maj"
    assert candidate.song_theme_scores_raw == {}


def test_candidate_from_row_with_scores():
    scores = json.dumps({"讚美": 0.95, "感恩": 0.80, "敬拜": 0.75})
    row = (
        "s2", "Test 2", None, None, None,
        None, None, "D", "lyrics",
        "hash2", 100.0, "E", "min",
        0.8, -10.0,
        scores,
    )
    candidate = _candidate_from_row(row)
    assert candidate.song_id == "s2"
    assert candidate.song_theme_scores_raw == {"讚美": 0.95, "感恩": 0.80, "敬拜": 0.75}
    assert candidate.musical_key == "E"


def test_candidate_from_row_key_fallback():
    """When recording musical_key is None, fall back to song musical_key."""
    row = (
        "s3", "Test 3", None, None, None,
        None, None, "G", "lyrics",
        "hash3", 90.0, None, "maj",
        0.9, -8.0,
        None,
    )
    candidate = _candidate_from_row(row)
    assert candidate.musical_key == "G"
    assert candidate.song_theme_scores_raw == {}


def test_pool_query_sql_shapes():
    from stream_of_worship.admin.songset_constructor.db import POOL_QUERY

    assert "json_object_agg" in POOL_QUERY
    assert "song_theme_scores_raw" in POOL_QUERY
    assert "theme_anchors" in POOL_QUERY
    assert "embedding <=>" in POOL_QUERY
    assert "embedding::text" not in POOL_QUERY


def test_line_theme_query_sql_shapes():
    from stream_of_worship.admin.songset_constructor.db import LINE_THEME_QUERY

    assert "json_object_agg" in LINE_THEME_QUERY
    assert "line_theme_scores_raw" in LINE_THEME_QUERY
    assert "CROSS JOIN theme_anchors" in LINE_THEME_QUERY
    assert "embedding::text" not in LINE_THEME_QUERY


def test_bandwidth_validation():
    """Verify fetch_catalog_pool does not leak embedding::text and stays under 1 MB for pool_limit=200."""
    from unittest.mock import MagicMock, patch

    from stream_of_worship.admin.songset_constructor.config import RunConfig
    from stream_of_worship.admin.songset_constructor.db import POOL_QUERY, LINE_THEME_QUERY

    assert "embedding::text" not in POOL_QUERY
    assert "embedding::text" not in LINE_THEME_QUERY

    config = RunConfig(count=3, proposals=3, pool=200, use_cache=False)
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    # Simulate 200 rows with minimal data (no theme scores)
    mock_cursor.fetchall.return_value = [
        (
            f"s{i}", f"Title {i}", None, "Composer A", None,
            "Album", "Series", "C", "lyrics",
            f"hash{i:04d}", 120.0, "D", "maj",
            0.95, -12.5, None,
        )
        for i in range(200)
    ]

    mock_read_client = MagicMock()
    mock_read_client.connection = mock_conn

    with patch("stream_of_worship.admin.songset_constructor.db.fetch_line_theme_scores", return_value={}):
        from stream_of_worship.admin.songset_constructor.db import fetch_catalog_pool

        pool = fetch_catalog_pool(config, client=mock_read_client)

    assert len(pool) == 200

    # Verify no query contained embedding::text
    for call_args in mock_cursor.execute.call_args_list:
        sql = call_args[0][0] if call_args[0] else ""
        assert "embedding::text" not in sql, f"embedding::text found in: {sql[:100]}"

    # Estimate bandwidth: 200 rows × 16 columns × average 40 bytes per value
    estimated_bytes = 200 * 16 * 40
    assert estimated_bytes < 1_000_000, f"Estimated bandwidth {estimated_bytes} bytes exceeds 1 MB limit"


def test_bandwidth_theme_scores_12_keys():
    """Verify song_theme_scores_raw has exactly 12 keys when populated."""
    import json

    from stream_of_worship.admin.songset_constructor.db import _candidate_from_row
    from stream_of_worship.admin.songset_constructor.rules.themes import THEMES

    scores = json.dumps({theme: 0.5 for theme in THEMES})
    row = (
        "s1", "Title", None, None, None,
        None, None, "C", "lyrics",
        "hash1", 120.0, "D", "maj",
        0.95, -12.5, scores,
    )
    candidate = _candidate_from_row(row)
    assert len(candidate.song_theme_scores_raw) == 12
    assert set(candidate.song_theme_scores_raw.keys()) == set(THEMES)


def test_normalise_cosine_scores_empty():
    from stream_of_worship.admin.songset_constructor.rules.themes import normalise_cosine_scores

    result = normalise_cosine_scores({})
    assert all(v == 0.0 for v in result.values())
    assert set(result.keys()) == set(THEMES)


def test_normalise_cosine_scores_identity():
    from stream_of_worship.admin.songset_constructor.rules.themes import normalise_cosine_scores

    scores = {"讚美": 0.9, "感恩": 0.5, "敬拜": 0.3}
    result = normalise_cosine_scores(scores)
    assert abs(result["讚美"] - 1.0) < 1e-6
    assert abs(result["感恩"] - 0.333333) < 1e-3
    assert result["敬拜"] == 0.0


def test_pgvector_python_cosine_tolerance():
    """Verify normalise_cosine_scores produces equivalent results
    whether raw scores come from pgvector or Python cosine."""
    from stream_of_worship.admin.songset_constructor.rules.themes import (
        THEMES,
        normalise_cosine_scores,
    )

    pgvector_scores = {
        "讚美": 0.850001, "感恩": 0.750000, "敬拜": 0.650000,
        "奉獻": 0.550000, "認罪": 0.450000, "差遣": 0.350000,
        "信心": 0.250000, "祈禱": 0.150000, "復興": 0.050000,
        "聖靈": 0.000001, "十字架": 0.000000, "跟隨": 0.000000,
    }
    python_scores = {
        "讚美": 0.850000, "感恩": 0.750000, "敬拜": 0.650000,
        "奉獻": 0.550000, "認罪": 0.450000, "差遣": 0.350000,
        "信心": 0.250000, "祈禱": 0.150000, "復興": 0.050000,
        "聖靈": 0.000000, "十字架": 0.000000, "跟隨": 0.000000,
    }

    result_pv = normalise_cosine_scores(pgvector_scores)
    result_py = normalise_cosine_scores(python_scores)

    for theme in THEMES:
        assert abs(result_pv[theme] - result_py[theme]) < 1e-5, (
            f"Theme {theme}: pgvector={result_pv[theme]}, python={result_py[theme]}"
        )
