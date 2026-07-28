"""Tests for runner and RunConfig validation."""

from __future__ import annotations

from stream_of_worship.admin.songset_constructor.config import RunConfig


def test_run_config_defaults():
    config = RunConfig(count=3, proposals=3, pool=200)
    assert config.count == 3
    assert config.proposals == 3
    assert config.pool == 200
    assert config.llm_enabled is False
    assert config.use_cache is True


def test_run_config_count_validation():
    for invalid in (1, 6):
        try:
            RunConfig(count=invalid, proposals=3, pool=200)
            assert False, f"Expected ValueError for count={invalid}"
        except ValueError:
            pass


def test_run_config_proposals_validation():
    try:
        RunConfig(count=3, proposals=0, pool=200)
        assert False, "Expected ValueError for proposals=0"
    except ValueError:
        pass


def test_run_config_pool_validation():
    try:
        RunConfig(count=5, proposals=3, pool=3)
        assert False, "Expected ValueError for pool < count"
    except ValueError:
        pass


def test_run_config_season_validation():
    try:
        RunConfig(count=3, proposals=3, pool=200, season="invalid")
        assert False, "Expected ValueError for invalid season"
    except ValueError:
        pass
    config = RunConfig(count=3, proposals=3, pool=200, season="advent")
    assert config.season == "advent"


def test_run_config_llm_judge_without_llm():
    config = RunConfig(
        count=3, proposals=3, pool=200,
        llm_enabled=False, llm_judge=True,
    )
    try:
        config.validate_environment()
        assert False, "Expected RuntimeError for llm_judge without llm"
    except RuntimeError:
        pass


def test_run_config_llm_enabled_ok():
    import os
    os.environ["SOW_LLM_API_KEY"] = "test-key"
    os.environ["SOW_LLM_MODEL"] = "gpt-4"
    config = RunConfig(
        count=3, proposals=3, pool=200,
        llm_enabled=True, llm_model="gpt-4",
    )
    config.validate_environment()
    del os.environ["SOW_LLM_API_KEY"]
    del os.environ["SOW_LLM_MODEL"]


def test_run_config_relax_overrides():
    config = RunConfig(
        count=3, proposals=3, pool=200,
        relax_h1=True, relax_h2_bpm=80, relax_h3_bpm=100,
        relax_h4=True, relax_h5=True, relax_h5_cfd=3,
    )
    assert config.opening_floor == 80
    assert config.closing_limit == 100
    assert config.h4_limit == 40
    assert config.h5_limit == 3


def test_run_config_to_dict():
    config = RunConfig(count=3, proposals=3, pool=200)
    d = config.to_dict()
    assert d["count"] == 3
    assert d["proposals"] == 3
    assert d["pool"] == 200
    assert d["llm_enabled"] is False
