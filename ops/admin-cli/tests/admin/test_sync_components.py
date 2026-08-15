"""Tests for get_cached_component_result and the sync-components command."""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from stream_of_worship.admin.main import app

runner = CliRunner()

WIDE_ENV = {"COLUMNS": "200"}


@pytest.fixture
def api_key_env(monkeypatch):
    monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-api-key")


@pytest.fixture
def r2_creds_env(monkeypatch):
    monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-access")
    monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")


class TestGetCachedComponentResult:
    def _client(self, api_key_env):
        from stream_of_worship.admin.services.analysis import AnalysisClient

        return AnalysisClient("http://localhost:8000")

    def test_get_cached_component_result_accepts_schema_v2(self, api_key_env):
        client = self._client(api_key_env)
        r2_client = MagicMock()
        r2_client.download_component_result.return_value = {
            "schema_version": 4,
            "components": [],
        }
        result = client.get_cached_component_result("a" * 12, r2_client=r2_client)
        assert result is not None
        assert result["schema_version"] == 4

    def test_get_cached_component_result_rejects_schema_v1(self, api_key_env):
        client = self._client(api_key_env)
        r2_client = MagicMock()
        r2_client.download_component_result.return_value = {
            "schema_version": 1,
            "components": [],
        }
        result = client.get_cached_component_result("a" * 12, r2_client=r2_client)
        assert result is None

    def test_get_cached_component_result_rejects_missing_schema(self, api_key_env):
        client = self._client(api_key_env)
        r2_client = MagicMock()
        r2_client.download_component_result.return_value = {"components": []}
        result = client.get_cached_component_result("a" * 12, r2_client=r2_client)
        assert result is None

    def test_get_cached_component_result_requires_r2_client(self, api_key_env):
        client = self._client(api_key_env)
        with pytest.raises(ValueError, match="requires an admin R2Client"):
            client.get_cached_component_result("a" * 12)


class TestSyncComponentsCommand:
    def _mock_deps(self, cached_payload, existing_rows=0):
        mock_config = MagicMock()
        mock_config.r2_bucket = "bucket"
        mock_config.r2_endpoint_url = "https://r2.example.com"
        mock_config.r2_region = "auto"
        mock_config.analysis_url = "http://localhost:8000"

        mock_r2 = MagicMock()
        mock_r2.download_component_result.return_value = cached_payload

        mock_analysis = MagicMock()
        mock_analysis.get_cached_component_result.return_value = cached_payload

        mock_db = MagicMock()
        mock_db.get_recording_by_song_id.return_value = MagicMock(
            hash_prefix="a" * 12,
            content_hash="b" * 64,
        )
        mock_db.get_song_components.return_value = [MagicMock() for _ in range(existing_rows)]

        return mock_config, mock_r2, mock_analysis, mock_db

    def _invoke(self, args, config, r2, analysis, db):
        with patch("stream_of_worship.admin.commands.audio.AdminConfig.load", return_value=config), \
             patch("stream_of_worship.admin.commands.audio.get_db_client", return_value=db), \
             patch("stream_of_worship.admin.commands.audio.R2Client", return_value=r2), \
             patch("stream_of_worship.admin.commands.audio.AnalysisClient", return_value=analysis):
            return runner.invoke(app, ["audio", "sync-components"] + args, env=WIDE_ENV)

    def _payload(self, components):
        return {"schema_version": 2, "components": components}

    def test_sync_components_dry_run_writes_nothing(self, r2_creds_env):
        payload = self._payload([
            {"component_type": "chorus", "occurrence_index": 1, "role": "entry",
             "start_time": 0.0, "end_time": 10.0, "theme": "感恩", "vocal_posture": "站立"},
        ])
        config, r2, analysis, db = self._mock_deps(payload, existing_rows=1)
        result = self._invoke(["song_001", "--dry-run"], config, r2, analysis, db)
        assert result.exit_code == 0
        db.upsert_song_components.assert_not_called()
        assert "DRY-RUN" in result.output

    def test_sync_components_shrink_refused_without_yes(self, r2_creds_env):
        payload = self._payload([
            {"component_type": "chorus", "occurrence_index": 1, "role": "entry",
             "start_time": 0.0, "end_time": 10.0, "theme": "感恩", "vocal_posture": "站立"},
        ])
        config, r2, analysis, db = self._mock_deps(payload, existing_rows=3)
        result = self._invoke(["song_001"], config, r2, analysis, db)
        assert result.exit_code == 1
        db.upsert_song_components.assert_not_called()
        assert "Refusing to shrink" in result.output

    def test_sync_components_shrink_allowed_with_yes(self, r2_creds_env):
        payload = self._payload([
            {"component_type": "chorus", "occurrence_index": 1, "role": "entry",
             "start_time": 0.0, "end_time": 10.0, "theme": "感恩", "vocal_posture": "站立"},
        ])
        config, r2, analysis, db = self._mock_deps(payload, existing_rows=3)
        result = self._invoke(["song_001", "--yes"], config, r2, analysis, db)
        assert result.exit_code == 0
        db.upsert_song_components.assert_called_once()

    def test_sync_components_empty_components_no_upsert(self, r2_creds_env):
        payload = self._payload([])
        config, r2, analysis, db = self._mock_deps(payload, existing_rows=2)
        result = self._invoke(["song_001"], config, r2, analysis, db)
        assert result.exit_code == 0
        db.upsert_song_components.assert_not_called()

    def test_sync_components_missing_r2_creds_loud(self, monkeypatch):
        config = MagicMock()
        config.r2_bucket = "bucket"
        config.r2_endpoint_url = "https://r2.example.com"
        config.r2_region = "auto"
        config.analysis_url = "http://localhost:8000"

        db = MagicMock()
        db.get_recording_by_song_id.return_value = MagicMock(
            hash_prefix="a" * 12, content_hash="b" * 64
        )

        def _raise(*args, **kwargs):
            raise ValueError("R2 credentials not set")

        with patch("stream_of_worship.admin.commands.audio.AdminConfig.load", return_value=config), \
             patch("stream_of_worship.admin.commands.audio.get_db_client", return_value=db), \
             patch("stream_of_worship.admin.commands.audio.R2Client", side_effect=_raise):
            result = runner.invoke(
                app, ["audio", "sync-components", "song_001"], env=WIDE_ENV
            )
        assert result.exit_code == 1
        assert "R2 credentials not set" in result.output
