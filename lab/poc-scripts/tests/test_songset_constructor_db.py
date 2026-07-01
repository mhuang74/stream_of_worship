from poc.songset_constructor.db import POOL_QUERY


def test_pool_query_includes_published_and_review_lrc_recordings():
    assert "r.visibility_status IN ('published', 'review')" in POOL_QUERY
    assert "(r.lrc_status = 'completed' OR r.r2_lrc_url IS NOT NULL)" in POOL_QUERY
    assert "AND r.analysis_status" not in POOL_QUERY
    assert "cardinality(%s::text[]) = 0 OR s.album_series = ANY(%s)" in POOL_QUERY
