"""Tests for diagnose/report sections."""

from __future__ import annotations

from stream_of_worship.admin.songset_constructor.config import RunConfig
from stream_of_worship.admin.songset_constructor.diagnose import assemble_report_sections


def test_diagnose_empty_result():
    config = RunConfig(count=3, proposals=3, pool=200)
    result = {
        "final_proposals": [],
        "pool": [],
        "trace": [],
        "enrichment_metrics": {},
    }
    sections = assemble_report_sections(config, result)
    joined = "\n".join(sections)
    assert "Pool Enrichment Metrics" in joined
    assert "No valid proposals" in joined or "No Results" in joined


def test_diagnose_with_pool():
    from stream_of_worship.admin.songset_constructor.models import SongCandidate

    config = RunConfig(count=3, proposals=3, pool=200)
    pool = [
        SongCandidate(song_id="s1", title="A", recording_hash_prefix="h1", phase=1, tempo_bpm=100),
        SongCandidate(song_id="s2", title="B", recording_hash_prefix="h2", phase=3, tempo_bpm=80),
        SongCandidate(song_id="s3", title="C", recording_hash_prefix="h3", phase=5, tempo_bpm=60),
    ]
    result = {
        "final_proposals": [],
        "pool": pool,
        "trace": [
            {"node": "load_catalog", "event": "exit", "data": {"pool_size": len(pool)}},
            {"node": "enrich_pool", "event": "exit", "data": {"pool_size": len(pool), "dropped": 0}},
            {"node": "beam_seed_candidates", "event": "exit", "data": {
                "role_eligibility": {
                    "valid_openers_h2": 1,
                    "valid_closers_h3": 1,
                    "phase_1_candidates_h1": 1,
                },
            }},
        ],
        "enrichment_metrics": {},
    }
    sections = assemble_report_sections(config, result)
    joined = "\n".join(sections)
    assert "Pool Enrichment Metrics" in joined
    assert "Phase distribution" in joined
