"""Tests for songset CLI commands."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from stream_of_worship.admin.main import app
from stream_of_worship.db.app.models import Songset, SongsetItem

runner = CliRunner()


def _make_songset(songset_id: str, user_id: int, name: str) -> Songset:
    """Helper to create a Songset for tests."""
    now = datetime.now().isoformat()
    return Songset(
        id=songset_id,
        user_id=user_id,
        name=name,
        description=None,
        created_at=now,
        updated_at=now,
    )


def _make_item(
    item_id: str,
    songset_id: str,
    song_id: str,
    position: int,
    song_title: str = "Test Song",
    duration: float = 240.0,
    tempo_bpm: float = 120.0,
    key: str = "G",
) -> SongsetItem:
    """Helper to create a SongsetItem for tests."""
    return SongsetItem(
        id=item_id,
        songset_id=songset_id,
        song_id=song_id,
        position=position,
        song_title=song_title,
        song_key=key,
        duration_seconds=duration,
        tempo_bpm=tempo_bpm,
        recording_key=key,
    )


class TestSongsetListCommand:
    """Tests for 'songset list' command."""

    def test_list_without_config(self):
        """Test list fails without config."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["songset", "list"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_list_all_songsets(self):
        """Test listing all songsets produces a table."""
        songset1 = _make_songset("ss1", 1, "Worship Set 1")
        songset2 = _make_songset("ss2", 2, "Worship Set 2")

        item1 = _make_item("item1", "ss1", "song1", 0)
        item2 = _make_item("item2", "ss1", "song2", 1)

        with patch("stream_of_worship.admin.commands.songset.AdminConfig.load") as mock_config, \
             patch("stream_of_worship.admin.commands.songset.ConnectionProvider") as mock_conn_prov_cls, \
             patch("stream_of_worship.admin.commands.songset.SongsetClient") as mock_client_cls, \
             patch("stream_of_worship.admin.commands.songset.UserClient") as mock_user_cls:
            mock_config.return_value.get_connection_url.return_value = "postgresql://test"
            mock_conn_prov_cls.return_value = MagicMock()

            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_client.list_all_songsets.return_value = [songset1, songset2]
            mock_client.list_songset_items_with_song_recording.return_value = {
                "ss1": [item1, item2],
                "ss2": [],
            }

            result = runner.invoke(app, ["songset", "list"])

        assert result.exit_code == 0
        assert "Worship Set 1" in result.output
        assert "Worship Set 2" in result.output
        assert "Test Song" in result.output
        assert "(no songs)" in result.output

    def test_list_with_user_filter(self):
        """Test listing with --user flag filters to that user."""
        songset1 = _make_songset("ss1", 1, "Alice's Set")

        item1 = _make_item("item1", "ss1", "song1", 0)

        with patch("stream_of_worship.admin.commands.songset.AdminConfig.load") as mock_config, \
             patch("stream_of_worship.admin.commands.songset.ConnectionProvider") as mock_conn_prov_cls, \
             patch("stream_of_worship.admin.commands.songset.SongsetClient") as mock_client_cls, \
             patch("stream_of_worship.admin.commands.songset.UserClient") as mock_user_cls:
            mock_config.return_value.get_connection_url.return_value = "postgresql://test"
            mock_conn_prov_cls.return_value = MagicMock()

            mock_user = MagicMock()
            mock_user.id = 1
            mock_user.email = "alice@example.com"
            mock_user_cls.return_value.get_user_by_email.return_value = mock_user

            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_client.list_songsets_for_user_id.return_value = [songset1]
            mock_client.list_songset_items_with_song_recording.return_value = {
                "ss1": [item1],
            }

            result = runner.invoke(
                app, ["songset", "list", "--user", "alice@example.com"]
            )

        assert result.exit_code == 0
        assert "Alice's Set" in result.output
        mock_user_cls.return_value.get_user_by_email.assert_called_once_with("alice@example.com")
        mock_client.list_songsets_for_user_id.assert_called_once_with(1, limit=None)

    def test_list_user_not_found(self):
        """Test listing with unknown user email exits 1."""
        with patch("stream_of_worship.admin.commands.songset.AdminConfig.load") as mock_config, \
             patch("stream_of_worship.admin.commands.songset.ConnectionProvider") as mock_conn_prov_cls, \
             patch("stream_of_worship.admin.commands.songset.SongsetClient") as mock_client_cls, \
             patch("stream_of_worship.admin.commands.songset.UserClient") as mock_user_cls:
            mock_config.return_value.get_connection_url.return_value = "postgresql://test"
            mock_conn_prov_cls.return_value = MagicMock()
            mock_user_cls.return_value.get_user_by_email.return_value = None

            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            result = runner.invoke(
                app, ["songset", "list", "--user", "unknown@x.com"]
            )

        assert result.exit_code == 1
        assert "User not found" in result.output

    def test_list_empty_result(self):
        """Test listing with no songsets shows message."""
        with patch("stream_of_worship.admin.commands.songset.AdminConfig.load") as mock_config, \
             patch("stream_of_worship.admin.commands.songset.ConnectionProvider") as mock_conn_prov_cls, \
             patch("stream_of_worship.admin.commands.songset.SongsetClient") as mock_client_cls:
            mock_config.return_value.get_connection_url.return_value = "postgresql://test"
            mock_conn_prov_cls.return_value = MagicMock()

            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_client.list_all_songsets.return_value = []

            result = runner.invoke(app, ["songset", "list"])

        assert result.exit_code == 0
        assert "No songsets found" in result.output

    def test_list_format_ids(self):
        """Test listing with --format ids outputs IDs only."""
        songset1 = _make_songset("ss1", 1, "Worship Set 1")
        songset2 = _make_songset("ss2", 2, "Worship Set 2")

        with patch("stream_of_worship.admin.commands.songset.AdminConfig.load") as mock_config, \
             patch("stream_of_worship.admin.commands.songset.ConnectionProvider") as mock_conn_prov_cls, \
             patch("stream_of_worship.admin.commands.songset.SongsetClient") as mock_client_cls:
            mock_config.return_value.get_connection_url.return_value = "postgresql://test"
            mock_conn_prov_cls.return_value = MagicMock()

            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_client.list_all_songsets.return_value = [songset1, songset2]

            result = runner.invoke(app, ["songset", "list", "--format", "ids"])

        assert result.exit_code == 0
        assert "ss1" in result.output
        assert "ss2" in result.output
        assert "Worship Set" not in result.output
        # ids format must not trigger the items batch fetch
        mock_client.list_songset_items_with_song_recording.assert_not_called()

    def test_list_orphaned_items(self):
        """Test orphaned items render dashes."""
        songset1 = _make_songset("ss1", 1, "Set with Orphan")

        # Orphaned item: no song_title, no duration, no key
        orphan_item = SongsetItem(
            id="item1",
            songset_id="ss1",
            song_id="missing_song",
            position=0,
            song_title=None,
            song_key=None,
            duration_seconds=None,
            tempo_bpm=None,
            recording_key=None,
        )

        with patch("stream_of_worship.admin.commands.songset.AdminConfig.load") as mock_config, \
             patch("stream_of_worship.admin.commands.songset.ConnectionProvider") as mock_conn_prov_cls, \
             patch("stream_of_worship.admin.commands.songset.SongsetClient") as mock_client_cls:
            mock_config.return_value.get_connection_url.return_value = "postgresql://test"
            mock_conn_prov_cls.return_value = MagicMock()

            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_client.list_all_songsets.return_value = [songset1]
            mock_client.list_songset_items_with_song_recording.return_value = {
                "ss1": [orphan_item],
            }

            result = runner.invoke(app, ["songset", "list"])

        assert result.exit_code == 0
        assert "(missing)" in result.output
        assert "--:--" in result.output

    def test_list_empty_songset(self):
        """Test empty songset is rendered, not skipped."""
        songset1 = _make_songset("ss1", 1, "Empty Set")

        with patch("stream_of_worship.admin.commands.songset.AdminConfig.load") as mock_config, \
             patch("stream_of_worship.admin.commands.songset.ConnectionProvider") as mock_conn_prov_cls, \
             patch("stream_of_worship.admin.commands.songset.SongsetClient") as mock_client_cls:
            mock_config.return_value.get_connection_url.return_value = "postgresql://test"
            mock_conn_prov_cls.return_value = MagicMock()

            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_client.list_all_songsets.return_value = [songset1]
            mock_client.list_songset_items_with_song_recording.return_value = {
                "ss1": [],
            }

            result = runner.invoke(app, ["songset", "list"])

        assert result.exit_code == 0
        assert "Empty Set" in result.output
        assert "(no songs)" in result.output
        # Empty songset row must render dashes in the Song ID column,
        # not the songset id
        assert "ss1" not in result.output

    def test_list_with_limit(self):
        """Test --limit propagates to client."""
        songset1 = _make_songset("ss1", 1, "Worship Set 1")

        with patch("stream_of_worship.admin.commands.songset.AdminConfig.load") as mock_config, \
             patch("stream_of_worship.admin.commands.songset.ConnectionProvider") as mock_conn_prov_cls, \
             patch("stream_of_worship.admin.commands.songset.SongsetClient") as mock_client_cls:
            mock_config.return_value.get_connection_url.return_value = "postgresql://test"
            mock_conn_prov_cls.return_value = MagicMock()

            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_client.list_all_songsets.return_value = [songset1]
            mock_client.list_songset_items_with_song_recording.return_value = {
                "ss1": [],
            }

            result = runner.invoke(app, ["songset", "list", "--limit", "5"])

        assert result.exit_code == 0
        mock_client.list_all_songsets.assert_called_once_with(limit=5)

    def test_list_format_json(self):
        """Test listing with --format json outputs nested JSON."""
        import json

        songset1 = _make_songset("ss1", 1, "Worship Set 1")
        item1 = _make_item("item1", "ss1", "song1", 0, song_title="Test Song")

        with patch("stream_of_worship.admin.commands.songset.AdminConfig.load") as mock_config, \
             patch("stream_of_worship.admin.commands.songset.ConnectionProvider") as mock_conn_prov_cls, \
             patch("stream_of_worship.admin.commands.songset.SongsetClient") as mock_client_cls:
            mock_config.return_value.get_connection_url.return_value = "postgresql://test"
            mock_conn_prov_cls.return_value = MagicMock()

            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_client.list_all_songsets.return_value = [songset1]
            mock_client.list_songset_items_with_song_recording.return_value = {
                "ss1": [item1],
            }

            result = runner.invoke(app, ["songset", "list", "--format", "json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        songset = data[0]
        assert songset["id"] == "ss1"
        assert songset["name"] == "Worship Set 1"
        assert songset["user_id"] == 1
        assert songset["owner_email"] == "?"
        assert songset["description"] is None
        assert "created_at" in songset
        assert "updated_at" in songset
        assert len(songset["items"]) == 1
        item = songset["items"][0]
        assert item["position"] == 1
        assert item["song_id"] == "song1"
        assert item["song_title"] == "Test Song"
        assert item["display_key"] == "G"
        assert item["tempo_bpm"] == 120
        assert item["duration"] == "4:00"
        assert item["duration_seconds"] == 240.0

    def test_list_format_json_empty_songset(self):
        """Test empty songset renders as empty items array in JSON."""
        import json

        songset1 = _make_songset("ss1", 1, "Empty Set")

        with patch("stream_of_worship.admin.commands.songset.AdminConfig.load") as mock_config, \
             patch("stream_of_worship.admin.commands.songset.ConnectionProvider") as mock_conn_prov_cls, \
             patch("stream_of_worship.admin.commands.songset.SongsetClient") as mock_client_cls:
            mock_config.return_value.get_connection_url.return_value = "postgresql://test"
            mock_conn_prov_cls.return_value = MagicMock()

            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_client.list_all_songsets.return_value = [songset1]
            mock_client.list_songset_items_with_song_recording.return_value = {
                "ss1": [],
            }

            result = runner.invoke(app, ["songset", "list", "--format", "json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["items"] == []

    def test_list_format_json_orphaned_item(self):
        """Test orphaned item renders nulls in JSON."""
        import json

        songset1 = _make_songset("ss1", 1, "Set with Orphan")
        orphan_item = SongsetItem(
            id="item1",
            songset_id="ss1",
            song_id="missing_song",
            position=0,
            song_title=None,
            song_key=None,
            duration_seconds=None,
            tempo_bpm=None,
            recording_key=None,
        )

        with patch("stream_of_worship.admin.commands.songset.AdminConfig.load") as mock_config, \
             patch("stream_of_worship.admin.commands.songset.ConnectionProvider") as mock_conn_prov_cls, \
             patch("stream_of_worship.admin.commands.songset.SongsetClient") as mock_client_cls:
            mock_config.return_value.get_connection_url.return_value = "postgresql://test"
            mock_conn_prov_cls.return_value = MagicMock()

            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_client.list_all_songsets.return_value = [songset1]
            mock_client.list_songset_items_with_song_recording.return_value = {
                "ss1": [orphan_item],
            }

            result = runner.invoke(app, ["songset", "list", "--format", "json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        item = data[0]["items"][0]
        assert item["song_title"] is None
        assert item["display_key"] is None
        assert item["tempo_bpm"] is None
        assert item["duration"] == "--:--"
        assert item["duration_seconds"] is None

    def test_list_invalid_format(self):
        """Test invalid --format value exits 1 with an error."""
        with patch("stream_of_worship.admin.commands.songset.AdminConfig.load") as mock_config, \
             patch("stream_of_worship.admin.commands.songset.ConnectionProvider") as mock_conn_prov_cls:
            mock_config.return_value.get_connection_url.return_value = "postgresql://test"
            mock_conn_prov_cls.return_value = MagicMock()

            result = runner.invoke(app, ["songset", "list", "--format", "yaml"])

        assert result.exit_code == 1
        assert "--format must be one of" in result.output
