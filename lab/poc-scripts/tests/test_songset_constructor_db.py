from poc.songset_constructor.db import POOL_QUERY


def test_pool_query_includes_fast_analyze_partial_recordings():
    assert "r.analysis_status IN ('completed', 'partial')" in POOL_QUERY
