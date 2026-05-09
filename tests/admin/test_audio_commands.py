"""Tests for audio CLI commands."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import psycopg
import pytest
from typer.testing import CliRunner

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.main import app
from stream_of_worship.admin.services.analysis import AnalysisServiceError, JobInfo
from stream_of_worship.db.connection import ConnectionProvider

runner = CliRunner()


WIDE_ENV = {"COLUMNS": "200"}


@pytest.fixture
def provider(postgres_url):
    """Yield a ConnectionProvider and close it after the test."""
    provider = ConnectionProvider(postgres_url)
    yield provider
    provider.close()


@pytest.fixture
def client(provider):
    """Return an initialized DatabaseClient with schema ready.

    Tables are truncated after each test for isolation.
    """
    db = DatabaseClient(provider)
    db.initialize_schema()
    yield db
    conn = provider.get_connection()
    conn.rollback()
    cursor = conn.cursor()
    for table in ["songs", "recordings", "songsets", "songset_items"]:
        try:
            cursor.execute(f"TRUNCATE TABLE {table} CASCADE")
            conn.commit()
        except psycopg.errors.UndefinedTable:
            conn.rollback()
        except Exception:
            conn.rollback()
    conn.commit()


@pytest.fixture
def config_with_db(tmp_path, postgres_url):
    """Create a config file pointing to the test database."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[database]\nurl = "{postgres_url}"\n')
    return config_path


def _setup_db(client):
    """Seed database with one song and return the song."""
    song = Song(
        id="song_001",
        title="測試歌曲",
        source_url="https://example.com/1",
        scraped_at=datetime.now().isoformat(),
        composer="測試作曲家",
        album_name="測試專輯",
        musical_key="G",
    )
    client.insert_song(song)
    return song


class TestAudioDownloadCommand:
    """Tests for 'audio download' command."""

    @pytest.fixture
    def setup(self, client, config_with_db):
        return {"client": client, "config_path": config_with_db, "song": _setup_db(client)}

    def test_download_without_config(self):
        """Fails cleanly when no config file exists."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["audio", "download", "song_001"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_download_connection_error(self, tmp_path):
        """Fails when database connection fails."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid:5432/nodb"\n')

        result = runner.invoke(
            app, ["audio", "download", "song_001", "--config", str(config_path)]
        )

        assert result.exit_code == 1

    def test_download_song_not_found(self, setup):
        """Fails when the song ID does not exist in the catalog."""
        result = runner.invoke(
            app,
            ["audio", "download", "nonexistent", "--config", str(setup["config_path"])],
        )

        assert result.exit_code == 1
        assert "Song not found" in result.output

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    def test_download_existing_recording(self, mock_r2_cls, setup):
        """Exits 0 with an informational message when a recording already exists."""
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="existing.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
        )
        setup["client"].insert_recording(recording)

        mock_r2 = MagicMock()
        mock_r2_cls.return_value = mock_r2

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup["config_path"])],
        )

        assert result.exit_code == 0
        assert "Recording already exists" in result.output
        assert "aaaaaaaaaaaa" in result.output
        assert "--force" in result.output

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    def test_download_dry_run_shows_metadata(self, mock_r2_cls, setup):
        """Dry run displays song metadata and search query without downloading."""
        mock_r2 = MagicMock()
        mock_r2_cls.return_value = mock_r2

        result = runner.invoke(
            app,
            [
                "audio", "download", "song_001",
                "--config", str(setup["config_path"]),
                "--dry-run",
            ],
        )

        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert "測試歌曲" in result.output
        assert "測試作曲家" in result.output
        assert "測試專輯" in result.output

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.compute_file_hash")
    @patch("stream_of_worship.admin.commands.audio.get_hash_prefix")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    def test_download_success(
        self,
        mock_downloader_cls,
        mock_get_prefix,
        mock_compute_hash,
        mock_r2_cls,
        setup,
        tmp_path,
    ):
        """Full download flow creates a recording in the database."""
        fake_audio = tmp_path / "downloaded.mp3"
        fake_audio.write_bytes(b"fake audio content")

        mock_downloader = MagicMock()
        mock_downloader.build_search_query.return_value = "測試歌曲 測試作曲家 測試專輯"
        mock_downloader.preview_video.return_value = {
            "id": "test123",
            "title": "Test Video",
            "duration": 245,
            "webpage_url": "https://youtube.com/watch?v=test123",
        }
        mock_downloader.download.return_value = fake_audio
        mock_downloader_cls.return_value = mock_downloader

        mock_compute_hash.return_value = "b" * 64
        mock_get_prefix.return_value = "bbbbbbbbbbbb"

        mock_r2 = MagicMock()
        mock_r2.upload_audio.return_value = "s3://sow-audio/bbbbbbbbbbbb/audio.mp3"
        mock_r2_cls.return_value = mock_r2

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup["config_path"]), "--yes"],
        )

        assert result.exit_code == 0
        assert "Video Preview" in result.output
        assert "Downloaded: downloaded.mp3" in result.output
        assert "bbbbbbbbbbbb" in result.output
        assert "Uploaded" in result.output
        assert "Recording saved" in result.output

        mock_downloader.preview_video.assert_called_once()

        recording = setup["client"].get_recording_by_song_id("song_001")
        assert recording is not None
        assert recording.hash_prefix == "bbbbbbbbbbbb"
        assert recording.content_hash == "b" * 64
        assert recording.song_id == "song_001"
        assert recording.original_filename == "downloaded.mp3"
        assert recording.file_size_bytes == len(b"fake audio content")
        assert recording.r2_audio_url == "s3://sow-audio/bbbbbbbbbbbb/audio.mp3"

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.compute_file_hash")
    @patch("stream_of_worship.admin.commands.audio.get_hash_prefix")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    def test_download_youtube_failure(
        self,
        mock_downloader_cls,
        mock_get_prefix,
        mock_compute_hash,
        mock_r2_cls,
        setup,
    ):
        """YouTube download errors are reported cleanly."""
        mock_downloader = MagicMock()
        mock_downloader.build_search_query.return_value = "query"
        mock_downloader.preview_video.return_value = {
            "id": "test123",
            "title": "Test Video",
            "duration": 245,
            "webpage_url": "https://youtube.com/watch?v=test123",
        }
        mock_downloader.download.side_effect = RuntimeError("Network error")
        mock_downloader_cls.return_value = mock_downloader

        mock_r2 = MagicMock()
        mock_r2_cls.return_value = mock_r2

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup["config_path"]), "--yes"],
        )

        assert result.exit_code == 1
        assert "Download failed" in result.output

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.compute_file_hash")
    @patch("stream_of_worship.admin.commands.audio.get_hash_prefix")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    def test_download_r2_credentials_missing(
        self,
        mock_downloader_cls,
        mock_get_prefix,
        mock_compute_hash,
        mock_r2_cls,
        setup,
        tmp_path,
    ):
        """Missing R2 credentials are reported as a configuration error."""
        fake_audio = tmp_path / "audio.mp3"
        fake_audio.write_bytes(b"data")

        mock_downloader = MagicMock()
        mock_downloader.build_search_query.return_value = "query"
        mock_downloader.download.return_value = fake_audio
        mock_downloader_cls.return_value = mock_downloader

        mock_compute_hash.return_value = "c" * 64
        mock_get_prefix.return_value = "cccccccccccc"

        mock_r2_cls.side_effect = ValueError("R2 credentials not set")

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup["config_path"])],
        )

        assert result.exit_code == 1
        assert "R2 configuration error" in result.output

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.compute_file_hash")
    @patch("stream_of_worship.admin.commands.audio.get_hash_prefix")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    def test_download_r2_upload_failure(
        self,
        mock_downloader_cls,
        mock_get_prefix,
        mock_compute_hash,
        mock_r2_cls,
        setup,
        tmp_path,
    ):
        """R2 upload errors (non-ValueError) are reported cleanly."""
        fake_audio = tmp_path / "audio.mp3"
        fake_audio.write_bytes(b"data")

        mock_downloader = MagicMock()
        mock_downloader.build_search_query.return_value = "query"
        mock_downloader.preview_video.return_value = {
            "id": "test123",
            "title": "Test Video",
            "duration": 245,
            "webpage_url": "https://youtube.com/watch?v=test123",
        }
        mock_downloader.download.return_value = fake_audio
        mock_downloader_cls.return_value = mock_downloader

        mock_compute_hash.return_value = "d" * 64
        mock_get_prefix.return_value = "dddddddddddd"

        mock_r2 = MagicMock()
        mock_r2.upload_audio.side_effect = Exception("connection timeout")
        mock_r2_cls.return_value = mock_r2

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup["config_path"]), "--yes"],
        )

        assert result.exit_code == 1
        assert "Upload failed" in result.output


class TestAudioListCommand:
    """Tests for 'audio list' command."""

    @pytest.fixture
    def setup_with_recordings(self, client, config_with_db):
        """Database with two songs and two recordings at distinct times."""
        songs = [
            Song(
                id="song_001",
                title="第一首歌",
                source_url="https://example.com/1",
                scraped_at=datetime.now().isoformat(),
            ),
            Song(
                id="song_002",
                title="第二首歌",
                source_url="https://example.com/2",
                scraped_at=datetime.now().isoformat(),
            ),
        ]
        for song in songs:
            client.insert_song(song)

        recordings = [
            Recording(
                content_hash="a" * 64,
                hash_prefix="aaaaaaaaaaaa",
                song_id="song_001",
                original_filename="song1.mp3",
                file_size_bytes=1024000,
                imported_at="2024-01-15T10:30:00",
                analysis_status="completed",
            ),
            Recording(
                content_hash="b" * 64,
                hash_prefix="bbbbbbbbbbbb",
                song_id="song_002",
                original_filename="song2.mp3",
                file_size_bytes=2048000,
                imported_at="2024-01-16T10:30:00",
                analysis_status="pending",
            ),
        ]
        for rec in recordings:
            client.insert_recording(rec)

        return {"client": client, "config_path": config_with_db}

    def test_list_without_config(self):
        """Fails cleanly when no config file exists."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["audio", "list"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_list_connection_error(self, tmp_path):
        """Fails when database connection fails."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid:5432/nodb"\n')

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path)]
        )

        assert result.exit_code == 1

    def test_list_empty_database(self, client, config_with_db):
        """Shows a message when no recordings exist."""
        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_with_db)]
        )

        assert result.exit_code == 0
        assert "No recordings found" in result.output

    def test_list_all_recordings(self, setup_with_recordings):
        """Table format shows all recordings."""
        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(setup_with_recordings["config_path"])],
            env=WIDE_ENV,
        )

        assert result.exit_code == 0
        assert "aaaaaaaaaaaa" in result.output
        assert "bbbbbbbbbbbb" in result.output
        assert "song_001" in result.output
        assert "song_002" in result.output
        assert "2 total" in result.output

    def test_list_with_status_filter(self, setup_with_recordings):
        """Status filter returns only matching recordings."""
        result = runner.invoke(
            app,
            [
                "audio", "list",
                "--config", str(setup_with_recordings["config_path"]),
                "--status", "completed",
            ],
            env=WIDE_ENV,
        )

        assert result.exit_code == 0
        assert "aaaaaaaaaaaa" in result.output
        assert "bbbbbbbbbbbb" not in result.output

    def test_list_ids_format(self, setup_with_recordings):
        """ids format outputs one song_id per line."""
        result = runner.invoke(
            app,
            [
                "audio", "list",
                "--config", str(setup_with_recordings["config_path"]),
                "--format", "ids",
            ],
        )

        assert result.exit_code == 0
        assert "song_001" in result.output
        assert "song_002" in result.output

    def test_list_with_limit(self, setup_with_recordings):
        """Limit parameter restricts number of returned recordings."""
        result = runner.invoke(
            app,
            [
                "audio", "list",
                "--config", str(setup_with_recordings["config_path"]),
                "--limit", "1",
            ],
            env=WIDE_ENV,
        )

        assert result.exit_code == 0
        assert "1 total" in result.output

    def test_list_shows_song_titles(self, setup_with_recordings):
        """Song titles are resolved and displayed in the table."""
        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(setup_with_recordings["config_path"])],
            env=WIDE_ENV,
        )

        assert result.exit_code == 0
        assert "第一首歌" in result.output
        assert "第二首歌" in result.output

    def test_list_shows_album_column(self, setup_with_recordings):
        """Album column is present in the table."""
        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(setup_with_recordings["config_path"])],
            env=WIDE_ENV,
        )

        assert result.exit_code == 0
        assert "Album" in result.output

    def test_list_invalid_sort(self, setup_with_recordings):
        """Invalid sort option shows error."""
        result = runner.invoke(
            app,
            [
                "audio",
                "list",
                "--config",
                str(setup_with_recordings["config_path"]),
                "--sort",
                "invalid",
            ],
        )

        assert result.exit_code == 1
        assert "Invalid sort option" in result.output

    def test_list_album_filter(self, client, config_with_db):
        """Album filter returns only matching recordings."""
        songs = [
            Song(
                id="song_001",
                title="Song A",
                source_url="https://example.com/1",
                scraped_at=datetime.now().isoformat(),
                album_name="Album Alpha",
            ),
            Song(
                id="song_002",
                title="Song B",
                source_url="https://example.com/2",
                scraped_at=datetime.now().isoformat(),
                album_name="Album Beta",
            ),
        ]
        for song in songs:
            client.insert_song(song)

        recordings = [
            Recording(
                content_hash="a" * 64,
                hash_prefix="aaaaaaaaaaaa",
                song_id="song_001",
                original_filename="a.mp3",
                file_size_bytes=1024,
                imported_at="2024-01-15T10:30:00",
            ),
            Recording(
                content_hash="b" * 64,
                hash_prefix="bbbbbbbbbbbb",
                song_id="song_002",
                original_filename="b.mp3",
                file_size_bytes=2048,
                imported_at="2024-01-16T10:30:00",
            ),
        ]
        for rec in recordings:
            client.insert_recording(rec)

        result = runner.invoke(
            app,
            [
                "audio",
                "list",
                "--config",
                str(config_with_db),
                "--album",
                "Alpha",
            ],
        )

        assert result.exit_code == 0
        assert "song_001" in result.output
        assert "song_002" not in result.output

    def test_list_sort_by_title(self, client, config_with_db):
        """Sort by title orders recordings by song title."""
        songs = [
            Song(
                id="song_z",
                title="Zebra Song",
                source_url="https://example.com/z",
                scraped_at=datetime.now().isoformat(),
                album_name="Album Z",
            ),
            Song(
                id="song_a",
                title="Apple Song",
                source_url="https://example.com/a",
                scraped_at=datetime.now().isoformat(),
                album_name="Album A",
            ),
        ]
        for song in songs:
            client.insert_song(song)

        recordings = [
            Recording(
                content_hash="z" * 64,
                hash_prefix="zzzzzzzzzzzz",
                song_id="song_z",
                original_filename="z.mp3",
                file_size_bytes=1024,
                imported_at="2024-01-15T10:30:00",
            ),
            Recording(
                content_hash="a" * 64,
                hash_prefix="aaaaaaaaaaaa",
                song_id="song_a",
                original_filename="a.mp3",
                file_size_bytes=2048,
                imported_at="2024-01-16T10:30:00",
            ),
        ]
        for rec in recordings:
            client.insert_recording(rec)

        result = runner.invoke(
            app,
            [
                "audio",
                "list",
                "--config",
                str(config_with_db),
                "--sort",
                "title",
                "--format",
                "ids",
            ],
        )

        assert result.exit_code == 0
        ids = result.output.strip().split("\n")
        assert ids == ["song_a", "song_z"]

    def test_list_sort_by_imported(self, setup_with_recordings):
        """Sort by imported uses DB default order (imported_at DESC)."""
        result = runner.invoke(
            app,
            [
                "audio",
                "list",
                "--config",
                str(setup_with_recordings["config_path"]),
                "--sort",
                "imported",
                "--format",
                "ids",
            ],
        )

        assert result.exit_code == 0
        ids = result.output.strip().split("\n")
        assert ids[0] == "song_002"
        assert ids[1] == "song_001"


class TestAudioShowCommand:
    """Tests for 'audio show' command."""

    @pytest.fixture
    def setup_with_recording(self, client, config_with_db):
        """Database with a song linked to a fully-populated recording."""
        song = Song(
            id="song_001",
            title="測試歌曲",
            source_url="https://example.com/1",
            scraped_at=datetime.now().isoformat(),
            composer="測試作曲家",
        )
        client.insert_song(song)

        recording = Recording(
            content_hash="d" * 64,
            hash_prefix="dddddddddddd",
            song_id="song_001",
            original_filename="test_song.mp3",
            file_size_bytes=5242880,
            imported_at="2024-01-15T10:30:00",
            r2_audio_url="s3://sow-audio/dddddddddddd/audio.mp3",
            analysis_status="completed",
            duration_seconds=245.3,
            tempo_bpm=128.5,
            musical_key="G",
            musical_mode="major",
            key_confidence=0.87,
            loudness_db=-8.2,
        )
        client.insert_recording(recording)

        return {"client": client, "config_path": config_with_db}

    def test_show_without_config(self):
        """Fails cleanly when no config file exists."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["audio", "show", "abc123def456"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_show_connection_error(self, tmp_path):
        """Fails when database connection fails."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid:5432/nodb"\n')

        result = runner.invoke(
            app, ["audio", "show", "abc123", "--config", str(config_path)]
        )

        assert result.exit_code == 1

    def test_show_no_recording_for_song(self, setup_with_recording):
        """Reports an error when song has no recording."""
        result = runner.invoke(
            app,
            [
                "audio", "show", "song_without_recording",
                "--config", str(setup_with_recording["config_path"]),
            ],
        )

        assert result.exit_code == 1
        assert "No recording found" in result.output

    def test_show_displays_basic_fields(self, setup_with_recording):
        """All basic metadata fields are rendered."""
        result = runner.invoke(
            app,
            [
                "audio", "show", "song_001",
                "--config", str(setup_with_recording["config_path"]),
            ],
        )

        assert result.exit_code == 0
        assert "song_001" in result.output
        assert "dddddddddddd" in result.output
        assert "d" * 64 in result.output
        assert "test_song.mp3" in result.output
        assert "測試歌曲" in result.output
        assert "s3://sow-audio/dddddddddddd/audio.mp3" in result.output

    def test_show_displays_analysis_results(self, setup_with_recording):
        """Analysis section is shown when status is completed."""
        result = runner.invoke(
            app,
            [
                "audio", "show", "song_001",
                "--config", str(setup_with_recording["config_path"]),
            ],
        )

        assert result.exit_code == 0
        assert "Analysis Results" in result.output
        assert "128.5" in result.output
        assert "major" in result.output
        assert "0.87" in result.output
        assert "-8.2" in result.output

    def test_show_pending_recording_no_analysis_section(self, client, config_with_db):
        """Analysis Results section is absent for pending recordings."""
        song = Song(
            id="song_pending",
            title="Pending Song",
            source_url="https://example.com/pending",
            scraped_at=datetime.now().isoformat(),
        )
        client.insert_song(song)

        recording = Recording(
            content_hash="e" * 64,
            hash_prefix="eeeeeeeeeeee",
            song_id="song_pending",
            original_filename="pending.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            analysis_status="pending",
        )
        client.insert_recording(recording)

        result = runner.invoke(
            app, ["audio", "show", "song_pending", "--config", str(config_with_db)]
        )

        assert result.exit_code == 0
        assert "song_pending" in result.output
        assert "eeeeeeeeeeee" in result.output
        assert "pending" in result.output
        assert "Analysis Results" not in result.output

    def test_show_recording_without_linked_song(self, client, config_with_db):
        """Recording with no song_id cannot be looked up by song_id."""
        recording = Recording(
            content_hash="f" * 64,
            hash_prefix="ffffffffffff",
            original_filename="orphan.mp3",
            file_size_bytes=500,
            imported_at="2024-02-01T12:00:00",
            analysis_status="pending",
        )
        client.insert_recording(recording)

        result = runner.invoke(
            app, ["audio", "show", "nonexistent_song", "--config", str(config_with_db)]
        )

        assert result.exit_code == 1
        assert "No recording found" in result.output


class TestAnalyzeCommand:
    """Tests for 'audio analyze' command."""

    @pytest.fixture
    def setup(self, client, config_with_db):
        """Create a database seeded with one song."""
        song = Song(
            id="song_001",
            title="測試歌曲",
            source_url="https://example.com/1",
            scraped_at=datetime.now().isoformat(),
            composer="測試作曲家",
        )
        client.insert_song(song)

        return {"client": client, "config_path": config_with_db, "song": song}

    def test_analyze_without_config(self):
        """Fails cleanly when no config file exists."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["audio", "analyze", "abc123"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_analyze_connection_error(self, tmp_path):
        """Fails when database connection fails."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid:5432/nodb"\n')

        result = runner.invoke(
            app, ["audio", "analyze", "abc123", "--config", str(config_path)]
        )

        assert result.exit_code == 1

    def test_analyze_no_recording_for_song(self, setup):
        """Error when song has no recording."""
        result = runner.invoke(
            app,
            [
                "audio", "analyze", "song_001",
                "--config", str(setup["config_path"]),
            ],
        )

        assert result.exit_code == 1
        assert "No recording found" in result.output

    def test_analyze_song_not_found(self, setup):
        """Error when song doesn't exist."""
        result = runner.invoke(
            app,
            [
                "audio", "analyze", "nonexistent_song",
                "--config", str(setup["config_path"]),
            ],
        )

        assert result.exit_code == 1
        assert "No recording found" in result.output

    def test_analyze_no_r2_audio_url(self, setup):
        """Error when recording lacks audio URL."""
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url=None,
        )
        setup["client"].insert_recording(recording)

        result = runner.invoke(
            app,
            [
                "audio", "analyze", "song_001",
                "--config", str(setup["config_path"]),
            ],
        )

        assert result.exit_code == 1
        assert "has no audio URL" in result.output

    def test_analyze_already_completed_no_force(self, setup):
        """Exit 0 with message when already done."""
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="completed",
        )
        setup["client"].insert_recording(recording)

        result = runner.invoke(
            app,
            [
                "audio", "analyze", "song_001",
                "--config", str(setup["config_path"]),
            ],
        )

        assert result.exit_code == 0
        assert "already analyzed" in result.output

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_already_completed_with_force(self, mock_client_cls, setup, monkeypatch):
        """Re-submits with --force."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="completed",
        )
        setup["client"].insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-123",
            status="queued",
            job_type="analysis",
            progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio", "analyze", "song_001",
                "--config", str(setup["config_path"]),
                "--force",
            ],
        )

        assert result.exit_code == 0
        assert "Analysis submitted" in result.output
        mock_client.submit_analysis.assert_called_once()

    def test_analyze_already_processing_no_wait(self, setup, monkeypatch):
        """Exit 0 with existing job info."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="processing",
            analysis_job_id="existing-job-123",
        )
        setup["client"].insert_recording(recording)

        result = runner.invoke(
            app,
            [
                "audio", "analyze", "song_001",
                "--config", str(setup["config_path"]),
            ],
        )

        assert result.exit_code == 0
        assert "already in progress" in result.output
        assert "existing-job-123" in result.output

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_already_processing_with_wait(self, mock_client_cls, setup, monkeypatch):
        """Polls existing job."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="processing",
            analysis_job_id="existing-job-123",
        )
        setup["client"].insert_recording(recording)

        mock_client = MagicMock()
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="existing-job-123",
            status="completed",
            job_type="analysis",
            progress=1.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio", "analyze", "song_001",
                "--config", str(setup["config_path"]),
                "--wait",
            ],
        )

        assert result.exit_code == 0
        mock_client.wait_for_completion.assert_called_once()

    def test_analyze_missing_api_key(self, setup):
        """Error when SOW_ANALYSIS_API_KEY not set."""
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        setup["client"].insert_recording(recording)

        result = runner.invoke(
            app,
            [
                "audio", "analyze", "song_001",
                "--config", str(setup["config_path"]),
            ],
        )

        assert result.exit_code == 1
        assert "not configured" in result.output

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_service_unavailable(self, mock_client_cls, setup, monkeypatch):
        """Error when service unreachable."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        setup["client"].insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_analysis.side_effect = AnalysisServiceError(
            "Cannot connect to analysis service"
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio", "analyze", "song_001",
                "--config", str(setup["config_path"]),
            ],
        )

        assert result.exit_code == 1
        assert "Failed to submit" in result.output

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_fire_and_forget_success(self, mock_client_cls, setup, monkeypatch):
        """Submits, updates DB to 'processing'."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        setup["client"].insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-abc-123",
            status="queued",
            job_type="analysis",
            progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio", "analyze", "song_001",
                "--config", str(setup["config_path"]),
            ],
        )

        assert result.exit_code == 0
        assert "Analysis submitted" in result.output
        assert "job-abc-123" in result.output

        updated = setup["client"].get_recording_by_hash("aaaaaaaaaaaa")
        assert updated.analysis_status == "processing"
        assert updated.analysis_job_id == "job-abc-123"

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_by_song_id(self, mock_client_cls, setup, monkeypatch):
        """Analyzes using song_id."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        setup["client"].insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-123",
            status="queued",
            job_type="analysis",
            progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio", "analyze", "song_001",
                "--config", str(setup["config_path"]),
            ],
        )

        assert result.exit_code == 0
        mock_client.submit_analysis.assert_called_once()

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_wait_mode_completed(self, mock_client_cls, setup, monkeypatch):
        """Polls, stores results to DB."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        setup["client"].insert_recording(recording)

        mock_client = MagicMock()
        from stream_of_worship.admin.services.analysis import AnalysisResult
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-123",
            status="queued",
            job_type="analysis",
            progress=0.0,
        )
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="job-123",
            status="completed",
            job_type="analysis",
            progress=1.0,
            result=AnalysisResult(
                duration_seconds=245.5,
                tempo_bpm=128.0,
                musical_key="G",
                musical_mode="major",
                key_confidence=0.95,
                loudness_db=-8.5,
            ),
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio", "analyze", "song_001",
                "--config", str(setup["config_path"]),
                "--wait",
            ],
        )

        assert result.exit_code == 0
        assert "Analysis completed" in result.output

        updated = setup["client"].get_recording_by_hash("aaaaaaaaaaaa")
        assert updated.analysis_status == "completed"
        assert updated.duration_seconds == 245.5
        assert updated.tempo_bpm == 128.0
        assert updated.musical_key == "G"

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_wait_mode_failed(self, mock_client_cls, setup, monkeypatch):
        """Updates DB to 'failed' on failure."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        setup["client"].insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-123",
            status="queued",
            job_type="analysis",
            progress=0.0,
        )
        mock_client.wait_for_completion.return_value = JobInfo(
            job_id="job-123",
            status="failed",
            job_type="analysis",
            progress=0.0,
            error_message="Analysis pipeline error",
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio", "analyze", "song_001",
                "--config", str(setup["config_path"]),
                "--wait",
            ],
        )

        assert result.exit_code == 1
        assert "Analysis failed" in result.output

        updated = setup["client"].get_recording_by_hash("aaaaaaaaaaaa")
        assert updated.analysis_status == "failed"

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_wait_mode_timeout(self, mock_client_cls, setup, monkeypatch):
        """Error on poll timeout."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        setup["client"].insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-123",
            status="queued",
            job_type="analysis",
            progress=0.0,
        )
        mock_client.wait_for_completion.side_effect = AnalysisServiceError(
            "Timed out waiting for job"
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio", "analyze", "song_001",
                "--config", str(setup["config_path"]),
                "--wait",
            ],
        )

        assert result.exit_code == 1
        assert "Timed out" in result.output

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_no_stems_flag(self, mock_client_cls, setup, monkeypatch):
        """Passes generate_stems=False."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        setup["client"].insert_recording(recording)

        mock_client = MagicMock()
        mock_client.submit_analysis.return_value = JobInfo(
            job_id="job-123",
            status="queued",
            job_type="analysis",
            progress=0.0,
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio", "analyze", "song_001",
                "--config", str(setup["config_path"]),
                "--no-stems",
            ],
        )

        assert result.exit_code == 0
        call_kwargs = mock_client.submit_analysis.call_args[1]
        assert call_kwargs["generate_stems"] is False


class TestStatusCommand:
    """Tests for 'audio status' command."""

    @pytest.fixture
    def setup(self, client, config_with_db):
        """Create a database seeded with one song."""
        song = Song(
            id="song_001",
            title="測試歌曲",
            source_url="https://example.com/1",
            scraped_at=datetime.now().isoformat(),
        )
        client.insert_song(song)

        return {"client": client, "config_path": config_with_db, "song": song}

    def test_status_without_config(self):
        """Fails cleanly when no config file exists."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["audio", "status"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_status_connection_error(self, tmp_path):
        """Fails when database connection fails."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid:5432/nodb"\n')

        result = runner.invoke(
            app, ["audio", "status", "--config", str(config_path)]
        )

        assert result.exit_code == 1

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_status_with_job_id_success(self, mock_client_cls, setup, monkeypatch):
        """Displays job in Rich Panel."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        mock_client = MagicMock()
        mock_client.get_job.return_value = JobInfo(
            job_id="job-abc-123",
            status="completed",
            job_type="analysis",
            progress=1.0,
            stage="complete",
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio", "status", "job-abc-123",
                "--config", str(setup["config_path"]),
            ],
        )

        assert result.exit_code == 0
        assert "job-abc-123" in result.output
        assert "completed" in result.output

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_status_with_job_id_not_found(self, mock_client_cls, setup, monkeypatch):
        """Error 404 handling."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        mock_client = MagicMock()
        mock_client.get_job.side_effect = AnalysisServiceError(
            "Job not found", status_code=404
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio", "status", "nonexistent-job",
                "--config", str(setup["config_path"]),
            ],
        )

        assert result.exit_code == 1
        assert "Job not found" in result.output

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_status_no_job_id_shows_all_jobs(self, mock_client_cls, setup, monkeypatch):
        """Shows all recent jobs when no job ID provided."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        mock_client = MagicMock()
        mock_client.list_jobs.return_value = [
            JobInfo(
                job_id="job-1",
                status="completed",
                job_type="analysis",
                progress=1.0,
            ),
            JobInfo(
                job_id="job-2",
                status="processing",
                job_type="analysis",
                progress=0.5,
            ),
        ]
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio", "status",
                "--config", str(setup["config_path"]),
            ],
        )

        assert result.exit_code == 0
        assert "job-1" in result.output
        assert "job-2" in result.output

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_status_service_unavailable(self, mock_client_cls, setup, monkeypatch):
        """Error when service unreachable."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        mock_client = MagicMock()
        mock_client.list_jobs.side_effect = AnalysisServiceError("Service unavailable")
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio", "status",
                "--config", str(setup["config_path"]),
            ],
        )

        assert result.exit_code == 1
        assert "Service unavailable" in result.output
