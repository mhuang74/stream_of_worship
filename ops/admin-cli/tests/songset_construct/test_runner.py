"""Tests for runner and RunConfig validation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from stream_of_worship.admin.songset_constructor.config import RunConfig

# The runner subpackage requires the `constructor` extra (langgraph, rapidfuzz,
# pydantic). Skip these tests when it isn't installed so the default
# `--extra admin --extra test` command doesn't report them as failures —
# mirroring how integration tests skip when Docker is unavailable.
try:
    import stream_of_worship.admin.songset_constructor.runner  # noqa: F401

    _RUNNER_AVAILABLE = True
except Exception:
    _RUNNER_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _RUNNER_AVAILABLE,
    reason="songset constructor extra not installed (add --extra constructor)",
)


def _verify_thread_id_in_kwargs(mock_graph, config) -> None:
    """Assert invoke was called with config containing thread_id matching RunConfig."""
    args, kwargs = mock_graph.invoke.call_args
    assert "config" in kwargs
    assert kwargs["config"]["configurable"]["thread_id"] == config.thread_id


def _make_config(**overrides) -> RunConfig:
    kwargs = dict(count=3, proposals=3, pool=200)
    kwargs.update(overrides)
    return RunConfig(**kwargs)


def test_run_extracts_final_state():
    """runner.run() extracts final_proposals, pool, trace, enrichment_metrics."""
    config = _make_config(use_cache=False)
    mock_read_client = MagicMock()
    mock_pool = MagicMock()
    mock_pool.__len__.return_value = 5
    mock_pool.__iter__.return_value = iter([])

    with (
        patch("stream_of_worship.admin.songset_constructor.runner.cache.try_load_pool", return_value=None),
        patch("stream_of_worship.admin.songset_constructor.runner.fetch_catalog_pool", return_value=mock_pool),
        patch("stream_of_worship.admin.songset_constructor.runner.cache.save_pool"),
        patch("stream_of_worship.admin.songset_constructor.runner.build_graph") as mock_build_graph,
    ):
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "final_proposals": ["p1", "p2"],
            "pool": mock_pool,
            "trace": [{"node": "test", "event": "exit"}],
            "enrichment_metrics": {"pool_size": 5},
        }
        mock_build_graph.return_value = mock_graph

        from stream_of_worship.admin.songset_constructor.runner import run

        result = run(config, mock_read_client)

    assert result["final_proposals"] == ["p1", "p2"]
    assert result["pool"] == mock_pool
    assert result["trace"] == [{"node": "test", "event": "exit"}]
    assert result["enrichment_metrics"] == {"pool_size": 5}
    mock_graph.invoke.assert_called_once()
    _verify_thread_id_in_kwargs(mock_graph, config)


def test_run_cache_hit():
    """runner.run() uses cached pool when cache hit."""
    config = _make_config(use_cache=True, cache_ttl=24.0)
    mock_read_client = MagicMock()
    mock_pool = MagicMock()
    mock_pool.__len__.return_value = 5
    mock_pool.__iter__.return_value = iter([])

    with (
        patch("stream_of_worship.admin.songset_constructor.runner.cache.try_load_pool", return_value=mock_pool),
        patch("stream_of_worship.admin.songset_constructor.runner.cache.cache_path") as mock_cache_path,
        patch("stream_of_worship.admin.songset_constructor.runner.build_graph") as mock_build_graph,
        patch("stream_of_worship.admin.songset_constructor.runner.time.time", return_value=1000),
    ):
        mock_path = MagicMock()
        mock_path.stat().st_mtime = 500
        mock_cache_path.return_value = mock_path
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {"final_proposals": [], "trace": []}
        mock_build_graph.return_value = mock_graph

        from stream_of_worship.admin.songset_constructor.runner import run

        result = run(config, mock_read_client)

    assert "final_proposals" in result
    mock_graph.invoke.assert_called_once()


def test_run_cache_miss():
    """runner.run() fetches from DB when cache miss."""
    config = _make_config(use_cache=True, cache_ttl=24.0)
    mock_read_client = MagicMock()
    mock_pool = MagicMock()
    mock_pool.__len__.return_value = 5
    mock_pool.__iter__.return_value = iter([])

    with (
        patch("stream_of_worship.admin.songset_constructor.runner.cache.try_load_pool", return_value=None),
        patch("stream_of_worship.admin.songset_constructor.runner.fetch_catalog_pool", return_value=mock_pool),
        patch("stream_of_worship.admin.songset_constructor.runner.cache.save_pool") as mock_save,
        patch("stream_of_worship.admin.songset_constructor.runner.build_graph") as mock_build_graph,
    ):
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {"final_proposals": [], "trace": []}
        mock_build_graph.return_value = mock_graph

        from stream_of_worship.admin.songset_constructor.runner import run

        result = run(config, mock_read_client)

    assert "final_proposals" in result
    mock_save.assert_called_once_with(config, mock_pool)


def test_run_config_defaults():
    config = RunConfig(count=3, proposals=3, pool=200)
    assert config.count == 3
    assert config.proposals == 3
    assert config.pool == 200
    assert config.llm_enabled is False
    assert config.use_cache is True


def test_run_config_count_validation():
    for invalid in (1, 6):
        with pytest.raises(ValueError):
            RunConfig(count=invalid, proposals=3, pool=200)


def test_run_config_proposals_validation():
    with pytest.raises(ValueError):
        RunConfig(count=3, proposals=0, pool=200)


def test_run_config_pool_validation():
    with pytest.raises(ValueError):
        RunConfig(count=5, proposals=3, pool=3)


def test_run_config_season_validation():
    with pytest.raises(ValueError):
        RunConfig(count=3, proposals=3, pool=200, season="invalid")
    config = RunConfig(count=3, proposals=3, pool=200, season="advent")
    assert config.season == "advent"


def test_run_config_llm_judge_without_llm():
    config = RunConfig(
        count=3, proposals=3, pool=200,
        llm_enabled=False, llm_judge=True,
    )
    with pytest.raises(RuntimeError):
        config.validate_environment()


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
    assert config.h4_limit == 55
    assert config.h5_limit == 3


def test_run_config_to_dict():
    config = RunConfig(count=3, proposals=3, pool=200)
    d = config.to_dict()
    assert d["count"] == 3
    assert d["proposals"] == 3
    assert d["pool"] == 200
    assert d["llm_enabled"] is False


def test_run_config_include_cpw_without_album_series():
    """include_cpw should add CPW to album_series even when album_series is empty."""
    config = RunConfig(count=3, proposals=3, pool=200, include_cpw=True)
    assert "CPW" in config.album_series


def test_run_config_hymnal_mode_without_album_series():
    """hymnal_mode should add HYMN to album_series even when album_series is empty."""
    config = RunConfig(count=3, proposals=3, pool=200, hymnal_mode=True)
    assert "HYMN" in config.album_series


def test_run_config_include_cpw_with_album_series():
    """include_cpw should add CPW alongside existing album_series."""
    config = RunConfig(count=3, proposals=3, pool=200, album_series=["SOP"], include_cpw=True)
    assert "CPW" in config.album_series
    assert "SOP" in config.album_series


def test_run_config_include_cpw_no_duplicate():
    """include_cpw should not add CPW if already present."""
    config = RunConfig(count=3, proposals=3, pool=200, album_series=["CPW"], include_cpw=True)
    assert config.album_series.count("CPW") == 1


# ---------------------------------------------------------------------------
# CliRunner mapping tests for `sow-admin songset construct`
# ---------------------------------------------------------------------------

def test_construct_missing_user_exits_2():
    """--user is required; omitting it should exit with code 2 (Typer)."""
    from typer.testing import CliRunner

    from stream_of_worship.admin.commands.songset import app

    runner = CliRunner()
    result = runner.invoke(app, ["construct", "--dry-run"])
    assert result.exit_code == 2


def test_construct_runconfig_mapping_from_typer_options():
    """Verify RunConfig maps correctly from Typer options with mocked internals."""
    from typer.testing import CliRunner

    from stream_of_worship.admin.commands.songset import app

    runner = CliRunner()

    mock_run_config = MagicMock()

    with (
        patch("stream_of_worship.admin.commands.songset._import_constructor"),
        patch("stream_of_worship.admin.commands.songset.AdminConfig") as mock_admin_cfg,
        patch("stream_of_worship.admin.commands.songset.ConnectionProvider"),
        patch("stream_of_worship.admin.commands.songset.UserClient") as mock_user_cls,
        patch("stream_of_worship.admin.commands.songset.SongsetClient"),
        patch(
            "stream_of_worship.admin.songset_constructor.config.RunConfig"
        ) as mock_rc_cls,
        patch(
            "stream_of_worship.admin.songset_constructor.db.check_theme_anchors",
            return_value=12,
        ),
        patch(
            "stream_of_worship.admin.songset_constructor.runner.run",
            return_value={"final_proposals": [], "pool": [], "trace": [], "enrichment_metrics": {}},
        ),
        patch(
            "stream_of_worship.admin.songset_constructor.persist.persist_proposals",
            return_value=[],
        ),
    ):
        mock_admin_cfg.load.return_value = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user_cls.return_value.get_user_by_email.return_value = mock_user
        mock_rc_cls.return_value = mock_run_config
        mock_run_config.validate_environment = MagicMock()

        result = runner.invoke(app, [
            "construct",
            "--user", "test@example.com",
            "--count", "4",
            "--proposals", "5",
            "--pool", "100",
            "--no-llm",
            "--no-cache",
            "--dry-run",
        ])

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"
    mock_rc_cls.assert_called_once()
    call_kwargs = mock_rc_cls.call_args
    assert call_kwargs.kwargs["count"] == 4
    assert call_kwargs.kwargs["proposals"] == 5
    assert call_kwargs.kwargs["pool"] == 100
    assert call_kwargs.kwargs["llm_enabled"] is False
    assert call_kwargs.kwargs["use_cache"] is False
    assert call_kwargs.kwargs["output_dir"] is None
