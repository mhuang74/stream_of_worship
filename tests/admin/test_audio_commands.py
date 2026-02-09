"""Tests for audio CLI commands."""

import sqlite3
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.main import app
from stream_of_worship.admin.services.analysis import AnalysisServiceError, JobInfo

runner = CliRunner()


def _setup_db(tmp_path):
    """Create a temp database seeded with one song and return paths."""
    db_path = tmp_path / "test.db"
    client = DatabaseClient(db_path)
    client.initialize_schema()

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

    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[database]\npath = "{db_path}"\n')

    return {"db_path": db_path, "config_path": config_path, "song": song}


class TestAudioDownloadCommand:
    """Tests for 'audio download' command."""

    @pytest.fixture
    def setup(self, tmp_path):
        return _setup_db(tmp_path)

    def test_download_without_config(self):
        """Fails cleanly when no config file exists."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["audio", "download", "song_001"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_download_without_database(self, tmp_path):
        """Fails when the database path does not exist."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\npath = "/nonexistent/db.sqlite"\n')

        result = runner.invoke(
            app, ["audio", "download", "song_001", "--config", str(config_path)]
        )

        assert result.exit_code == 1
        assert "Database not found" in result.output

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
        db_client = DatabaseClient(setup["db_path"])
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="existing.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
        )
        db_client.insert_recording(recording)

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
        # Real file so that stat().st_size works
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

        # Verify preview_video was called
        mock_downloader.preview_video.assert_called_once()

        # Verify the recording was persisted
        db_client = DatabaseClient(setup["db_path"])
        recording = db_client.get_recording_by_song_id("song_001")
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
    def setup_with_recordings(self, tmp_path):
        """Database with two songs and two recordings at distinct times."""
        db_path = tmp_path / "test.db"
        client = DatabaseClient(db_path)
        client.initialize_schema()

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

        config_path = tmp_path / "config.toml"
        config_path.write_text(f'[database]\npath = "{db_path}"\n')

        return {"db_path": db_path, "config_path": config_path}

    def test_list_without_config(self):
        """Fails cleanly when no config file exists."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["audio", "list"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_list_without_database(self, tmp_path):
        """Fails when database path does not exist."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\npath = "/nonexistent/db.sqlite"\n')

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path)]
        )

        assert result.exit_code == 1
        assert "Database not found" in result.output

    def test_list_empty_database(self, tmp_path):
        """Shows a message when no recordings exist."""
        db_path = tmp_path / "test.db"
        client = DatabaseClient(db_path)
        client.initialize_schema()

        config_path = tmp_path / "config.toml"
        config_path.write_text(f'[database]\npath = "{db_path}"\n')

        result = runner.invoke(
            app, ["audio", "list", "--config", str(config_path)]
        )

        assert result.exit_code == 0
        assert "No recordings found" in result.output

    def test_list_all_recordings(self, setup_with_recordings):
        """Table format shows all recordings."""
        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(setup_with_recordings["config_path"])],
        )

        assert result.exit_code == 0
        assert "aaaaaaaaaaaa" in result.output
        assert "bbbbbbbbbbbb" in result.output
        assert "song1.mp3" in result.output
        assert "song2.mp3" in result.output
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
        )

        assert result.exit_code == 0
        assert "1 total" in result.output

    def test_list_shows_song_titles(self, setup_with_recordings):
        """Song titles are resolved and displayed in the table."""
        result = runner.invoke(
            app,
            ["audio", "list", "--config", str(setup_with_recordings["config_path"])],
        )

        assert result.exit_code == 0
        assert "第一首歌" in result.output
        assert "第二首歌" in result.output


class TestAudioShowCommand:
    """Tests for 'audio show' command."""

    @pytest.fixture
    def setup_with_recording(self, tmp_path):
        """Database with a song linked to a fully-populated recording."""
        db_path = tmp_path / "test.db"
        client = DatabaseClient(db_path)
        client.initialize_schema()

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

        config_path = tmp_path / "config.toml"
        config_path.write_text(f'[database]\npath = "{db_path}"\n')

        return {"db_path": db_path, "config_path": config_path}

    def test_show_without_config(self):
        """Fails cleanly when no config file exists."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["audio", "show", "abc123def456"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_show_without_database(self, tmp_path):
        """Fails when the database does not exist."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\npath = "/nonexistent/db.sqlite"\n')

        result = runner.invoke(
            app, ["audio", "show", "abc123", "--config", str(config_path)]
        )

        assert result.exit_code == 1
        assert "Database not found" in result.output

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
        assert "dddddddddddd" in result.output  # hash prefix shown for reference
        assert "d" * 64 in result.output  # full hash
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
        assert "128.5" in result.output  # tempo
        assert "major" in result.output  # mode
        assert "0.87" in result.output  # key confidence
        assert "-8.2" in result.output  # loudness

    def test_show_pending_recording_no_analysis_section(self, tmp_path):
        """Analysis Results section is absent for pending recordings."""
        db_path = tmp_path / "test.db"
        client = DatabaseClient(db_path)
        client.initialize_schema()

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

        config_path = tmp_path / "config.toml"
        config_path.write_text(f'[database]\npath = "{db_path}"\n')

        result = runner.invoke(
            app, ["audio", "show", "song_pending", "--config", str(config_path)]
        )

        assert result.exit_code == 0
        assert "song_pending" in result.output
        assert "eeeeeeeeeeee" in result.output
        assert "pending" in result.output
        assert "Analysis Results" not in result.output

    def test_show_recording_without_linked_song(self, tmp_path):
        """Recording with no song_id cannot be looked up by song_id."""
        db_path = tmp_path / "test.db"
        client = DatabaseClient(db_path)
        client.initialize_schema()

        # Create an orphan recording (no song_id) - this shouldn't happen
        # in normal usage since we now require song_id for all recordings
        recording = Recording(
            content_hash="f" * 64,
            hash_prefix="ffffffffffff",
            original_filename="orphan.mp3",
            file_size_bytes=500,
            imported_at="2024-02-01T12:00:00",
            analysis_status="pending",
        )
        client.insert_recording(recording)

        config_path = tmp_path / "config.toml"
        config_path.write_text(f'[database]\npath = "{db_path}"\n')

        # Trying to look up by non-existent song_id should fail
        result = runner.invoke(
            app, ["audio", "show", "nonexistent_song", "--config", str(config_path)]
        )

        assert result.exit_code == 1
        assert "No recording found" in result.output


class TestAnalyzeCommand:
    """Tests for 'audio analyze' command."""

    @pytest.fixture
    def setup(self, tmp_path):
        """Create a temp database seeded with one song and recording."""
        db_path = tmp_path / "test.db"
        client = DatabaseClient(db_path)
        client.initialize_schema()

        song = Song(
            id="song_001",
            title="測試歌曲",
            source_url="https://example.com/1",
            scraped_at=datetime.now().isoformat(),
            composer="測試作曲家",
        )
        client.insert_song(song)

        config_path = tmp_path / "config.toml"
        config_path.write_text(f'[database]\npath = "{db_path}"\n')

        return {"db_path": db_path, "config_path": config_path, "song": song}

    def test_analyze_without_config(self):
        """Fails cleanly when no config file exists."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["audio", "analyze", "abc123"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_analyze_without_database(self, tmp_path):
        """Fails when the database path does not exist."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\npath = "/nonexistent/db.sqlite"\n')

        result = runner.invoke(
            app, ["audio", "analyze", "abc123", "--config", str(config_path)]
        )

        assert result.exit_code == 1
        assert "Database not found" in result.output

    def test_analyze_no_recording_for_song(self, setup):
        """Error when song has no recording."""
        # Song exists but has no recording
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
        db_client = DatabaseClient(setup["db_path"])
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url=None,
        )
        db_client.insert_recording(recording)

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
        db_client = DatabaseClient(setup["db_path"])
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
        db_client.insert_recording(recording)

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

        db_client = DatabaseClient(setup["db_path"])
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
        db_client.insert_recording(recording)

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

        db_client = DatabaseClient(setup["db_path"])
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
        db_client.insert_recording(recording)

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

        db_client = DatabaseClient(setup["db_path"])
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
        db_client.insert_recording(recording)

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
        db_client = DatabaseClient(setup["db_path"])
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

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

        db_client = DatabaseClient(setup["db_path"])
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

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

        db_client = DatabaseClient(setup["db_path"])
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

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

        # Verify DB updated
        updated = db_client.get_recording_by_hash("aaaaaaaaaaaa")
        assert updated.analysis_status == "processing"
        assert updated.analysis_job_id == "job-abc-123"

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_by_song_id(self, mock_client_cls, setup, monkeypatch):
        """Analyzes using song_id."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = DatabaseClient(setup["db_path"])
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

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

        db_client = DatabaseClient(setup["db_path"])
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

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

        # Verify DB updated with results
        updated = db_client.get_recording_by_hash("aaaaaaaaaaaa")
        assert updated.analysis_status == "completed"
        assert updated.duration_seconds == 245.5
        assert updated.tempo_bpm == 128.0
        assert updated.musical_key == "G"

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_wait_mode_failed(self, mock_client_cls, setup, monkeypatch):
        """Updates DB to 'failed' on failure."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = DatabaseClient(setup["db_path"])
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

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

        # Verify DB updated to failed
        updated = db_client.get_recording_by_hash("aaaaaaaaaaaa")
        assert updated.analysis_status == "failed"

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_wait_mode_timeout(self, mock_client_cls, setup, monkeypatch):
        """Error on poll timeout."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        db_client = DatabaseClient(setup["db_path"])
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

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

        db_client = DatabaseClient(setup["db_path"])
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
        )
        db_client.insert_recording(recording)

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
        # Verify generate_stems=False was passed
        call_kwargs = mock_client.submit_analysis.call_args[1]
        assert call_kwargs["generate_stems"] is False


class TestStatusCommand:
    """Tests for 'audio status' command."""

    @pytest.fixture
    def setup(self, tmp_path):
        """Create a temp database seeded with one song and recording."""
        db_path = tmp_path / "test.db"
        client = DatabaseClient(db_path)
        client.initialize_schema()

        song = Song(
            id="song_001",
            title="測試歌曲",
            source_url="https://example.com/1",
            scraped_at=datetime.now().isoformat(),
        )
        client.insert_song(song)

        config_path = tmp_path / "config.toml"
        config_path.write_text(f'[database]\npath = "{db_path}"\n')

        return {"db_path": db_path, "config_path": config_path, "song": song}

    def test_status_without_config(self):
        """Fails cleanly when no config file exists."""
        with patch("stream_of_worship.admin.config.get_config_path") as mock_path:
            mock_path.side_effect = FileNotFoundError("No config")
            result = runner.invoke(app, ["audio", "status"])

        assert result.exit_code == 1
        assert "Config file not found" in result.output

    def test_status_without_database(self, tmp_path):
        """Fails when the database path does not exist."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\npath = "/nonexistent/db.sqlite"\n')

        result = runner.invoke(
            app, ["audio", "status", "--config", str(config_path)]
        )

        assert result.exit_code == 1
        assert "Database not found" in result.output

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
    def test_status_with_job_id_missing_api_key(self, mock_client_cls, setup, monkeypatch):
        """Error 401 handling."""
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")

        mock_client = MagicMock()
        mock_client.get_job.side_effect = AnalysisServiceError(
            "Authentication failed", status_code=401
        )
        mock_client_cls.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "audio", "status", "some-job",
                "--config", str(setup["config_path"]),
            ],
        )

        assert result.exit_code == 1
        assert "Authentication failed" in result.output

    def test_status_no_args_all_completed(self, setup):
        """'All recordings processed' message."""
        db_client = DatabaseClient(setup["db_path"])
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="completed",
            lrc_status="completed",
        )
        db_client.insert_recording(recording)

        result = runner.invoke(
            app,
            ["audio", "status", "--config", str(setup["config_path"])],
        )

        assert result.exit_code == 0
        assert "All recordings are fully processed" in result.output

    def test_status_no_args_pending(self, setup):
        """Shows pending recordings table."""
        db_client = DatabaseClient(setup["db_path"])
        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://sow-audio/test/audio.mp3",
            analysis_status="pending",
            lrc_status="pending",
        )
        db_client.insert_recording(recording)

        result = runner.invoke(
            app,
            ["audio", "status", "--config", str(setup["config_path"])],
        )

        assert result.exit_code == 0
        assert "Pending Recordings" in result.output
        assert "song_001" in result.output

    def test_status_empty_database(self, tmp_path):
        """Empty DB handling."""
        db_path = tmp_path / "test.db"
        client = DatabaseClient(db_path)
        client.initialize_schema()

        config_path = tmp_path / "config.toml"
        config_path.write_text(f'[database]\npath = "{db_path}"\n')

        result = runner.invoke(
            app,
            ["audio", "status", "--config", str(config_path)],
        )

        assert result.exit_code == 0
        assert "All recordings are fully processed" in result.output


class TestDownloadCommandNewFeatures:
    """Tests for new download command features (--force, --url, preview)."""

    @pytest.fixture
    def setup(self, tmp_path):
        """Create a temp database seeded with one song."""
        db_path = tmp_path / "test.db"
        client = DatabaseClient(db_path)
        client.initialize_schema()

        song = Song(
            id="song_001",
            title="將天敞開",
            source_url="https://example.com/1",
            scraped_at=datetime.now().isoformat(),
            composer="游智婷",
            album_name="敬拜讚美15",
        )
        client.insert_song(song)

        config_path = tmp_path / "config.toml"
        config_path.write_text(f'''[database]
path = "{db_path}"

[r2]
bucket = "test-bucket"
endpoint_url = "https://test.r2.dev"
region = "auto"
''')

        return {
            "db_path": db_path,
            "config_path": config_path,
            "song": song,
            "tmp_path": tmp_path,
        }

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    def test_download_with_force_shows_deletion_message(
        self, mock_yt_class, mock_r2_class, setup, monkeypatch
    ):
        """--force shows deletion message for existing recording."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")

        # Create existing recording
        db_client = DatabaseClient(setup["db_path"])
        recording = Recording(
            content_hash="old" * 24,
            hash_prefix="oldoldoldold",
            song_id="song_001",
            original_filename="old.mp3",
            file_size_bytes=1000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://bucket/oldoldoldold/audio.mp3",
        )
        db_client.insert_recording(recording)

        # Mock R2 client
        mock_r2 = MagicMock()
        mock_r2.audio_exists.return_value = True
        mock_r2.upload_audio.return_value = "s3://bucket/newhash/audio.mp3"
        mock_r2_class.return_value = mock_r2

        # Mock YouTube downloader
        mock_yt = MagicMock()
        mock_yt.build_search_query.return_value = "將天敞開 游智婷 敬拜讚美15"
        mock_yt.preview_video.return_value = {
            "id": "abc123",
            "title": "Test Video",
            "duration": 245,
            "webpage_url": "https://youtube.com/watch?v=abc123",
        }
        mock_yt.download.return_value = setup["tmp_path"] / "Test Video.mp3"
        mock_yt_class.return_value = mock_yt

        # Create fake downloaded file
        mp3_path = setup["tmp_path"] / "Test Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            [
                "audio", "download", "song_001",
                "--config", str(setup["config_path"]),
                "--yes",  # Skip confirmation
                "--force",  # Delete existing
            ],
        )

        # Verify the deletion message was shown
        assert "Deleting existing recording" in result.output

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    def test_download_with_url_uses_direct_url(
        self, mock_yt_class, mock_r2_class, setup, monkeypatch
    ):
        """--url directly downloads from provided URL."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")

        # Mock R2 client
        mock_r2 = MagicMock()
        mock_r2.audio_exists.return_value = False
        mock_r2.upload_audio.return_value = "s3://bucket/hash/audio.mp3"
        mock_r2_class.return_value = mock_r2

        # Mock YouTube downloader
        mock_yt = MagicMock()
        mock_yt.preview_video.return_value = {
            "id": "custom123",
            "title": "Custom Video",
            "duration": 245,
            "webpage_url": "https://youtube.com/watch?v=custom123",
        }
        mock_yt.download_by_url.return_value = setup["tmp_path"] / "Custom Video.mp3"
        mock_yt_class.return_value = mock_yt

        # Create fake downloaded file
        mp3_path = setup["tmp_path"] / "Custom Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            [
                "audio", "download", "song_001",
                "--config", str(setup["config_path"]),
                "--yes",
                "--url", "https://youtube.com/watch?v=custom123",
            ],
        )

        assert result.exit_code == 0
        # Verify download_by_url was called, not download
        mock_yt.download_by_url.assert_called_once_with("https://youtube.com/watch?v=custom123")
        assert mock_yt.download.call_count == 0

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    def test_download_shows_duration_warning(
        self, mock_yt_class, mock_r2_class, setup, monkeypatch
    ):
        """Shows warning for videos over 7 minutes."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")

        # Mock R2 client
        mock_r2 = MagicMock()
        mock_r2.audio_exists.return_value = False
        mock_r2.upload_audio.return_value = "s3://bucket/hash/audio.mp3"
        mock_r2_class.return_value = mock_r2

        # Mock YouTube downloader with long video (500 seconds = 8:20)
        mock_yt = MagicMock()
        mock_yt.build_search_query.return_value = "將天敞開 游智婷 敬拜讚美15"
        mock_yt.preview_video.return_value = {
            "id": "long123",
            "title": "Long Video",
            "duration": 500,
            "webpage_url": "https://youtube.com/watch?v=long123",
        }
        mock_yt.download.return_value = setup["tmp_path"] / "Long Video.mp3"
        mock_yt_class.return_value = mock_yt

        # Create fake downloaded file
        mp3_path = setup["tmp_path"] / "Long Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            [
                "audio", "download", "song_001",
                "--config", str(setup["config_path"]),
                "--yes",
            ],
        )

        assert result.exit_code == 0
        # Should show formatted duration 8:20
        assert "8:20" in result.output


    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    @patch("stream_of_worship.admin.commands.audio._submit_analysis_job")
    def test_download_with_analyze_flag(
        self, mock_submit_analysis, mock_yt_class, mock_r2_class, setup, monkeypatch
    ):
        """--analyze flag submits analysis job after download."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-analysis-key")

        # Mock R2 client
        mock_r2 = MagicMock()
        mock_r2.audio_exists.return_value = False
        mock_r2.upload_audio.return_value = "s3://bucket/hash123/audio.mp3"
        mock_r2_class.return_value = mock_r2

        # Mock YouTube downloader
        mock_yt = MagicMock()
        mock_yt.build_search_query.return_value = "將天敞開 游智婷 敬拜讚美15"
        mock_yt.preview_video.return_value = {
            "id": "test123",
            "title": "Test Video",
            "duration": 300,
            "webpage_url": "https://youtube.com/watch?v=test123",
        }
        mock_yt.download.return_value = setup["tmp_path"] / "Test Video.mp3"
        mock_yt_class.return_value = mock_yt

        # Create fake downloaded file
        mp3_path = setup["tmp_path"] / "Test Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            [
                "audio", "download", "song_001",
                "--config", str(setup["config_path"]),
                "--yes",
                "--analyze",
            ],
        )

        assert result.exit_code == 0
        assert "Submitting for analysis" in result.output
        mock_submit_analysis.assert_called_once()

        # Verify it was called with recording
        call_kwargs = mock_submit_analysis.call_args[1]
        assert "recording" in call_kwargs

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    @patch("stream_of_worship.admin.commands.audio._submit_lrc_job")
    def test_download_with_lrc_flag(
        self, mock_submit_lrc, mock_yt_class, mock_r2_class, setup, monkeypatch
    ):
        """--lrc flag submits LRC job after download."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-analysis-key")

        # Add lyrics to the song
        db_client = DatabaseClient(setup["db_path"])
        song = db_client.get_song("song_001")
        song.lyrics_raw = "這是歌詞\n第二行歌詞"
        db_client.insert_song(song)

        # Mock R2 client
        mock_r2 = MagicMock()
        mock_r2.audio_exists.return_value = False
        mock_r2.upload_audio.return_value = "s3://bucket/hash456/audio.mp3"
        mock_r2_class.return_value = mock_r2

        # Mock YouTube downloader
        mock_yt = MagicMock()
        mock_yt.build_search_query.return_value = "將天敞開 游智婷 敬拜讚美15"
        mock_yt.preview_video.return_value = {
            "id": "test456",
            "title": "Test Video",
            "duration": 300,
            "webpage_url": "https://youtube.com/watch?v=test456",
        }
        mock_yt.download.return_value = setup["tmp_path"] / "Test Video.mp3"
        mock_yt_class.return_value = mock_yt

        # Create fake downloaded file
        mp3_path = setup["tmp_path"] / "Test Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            [
                "audio", "download", "song_001",
                "--config", str(setup["config_path"]),
                "--yes",
                "--lrc",
            ],
        )

        assert result.exit_code == 0
        assert "Submitting for LRC generation" in result.output
        mock_submit_lrc.assert_called_once()

        # Verify it was called with song_id and recording
        call_kwargs = mock_submit_lrc.call_args[1]
        assert "song_id" in call_kwargs
        assert call_kwargs["song_id"] == "song_001"
        assert "recording" in call_kwargs

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    @patch("stream_of_worship.admin.commands.audio._submit_analysis_job")
    @patch("stream_of_worship.admin.commands.audio._submit_lrc_job")
    def test_download_with_all_flag(
        self, mock_submit_lrc, mock_submit_analysis, mock_yt_class, mock_r2_class, setup, monkeypatch
    ):
        """--all flag triggers both analysis and LRC submission."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-analysis-key")

        # Add lyrics to the song
        db_client = DatabaseClient(setup["db_path"])
        song = db_client.get_song("song_001")
        song.lyrics_raw = "這是歌詞\n第二行歌詞"
        db_client.insert_song(song)

        # Mock R2 client
        mock_r2 = MagicMock()
        mock_r2.audio_exists.return_value = False
        mock_r2.upload_audio.return_value = "s3://bucket/hash789/audio.mp3"
        mock_r2_class.return_value = mock_r2

        # Mock YouTube downloader
        mock_yt = MagicMock()
        mock_yt.build_search_query.return_value = "將天敞開 游智婷 敬拜讚美15"
        mock_yt.preview_video.return_value = {
            "id": "test789",
            "title": "Test Video",
            "duration": 300,
            "webpage_url": "https://youtube.com/watch?v=test789",
        }
        mock_yt.download.return_value = setup["tmp_path"] / "Test Video.mp3"
        mock_yt_class.return_value = mock_yt

        # Create fake downloaded file
        mp3_path = setup["tmp_path"] / "Test Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            [
                "audio", "download", "song_001",
                "--config", str(setup["config_path"]),
                "--yes",
                "--all",
            ],
        )

        assert result.exit_code == 0
        # Both messages should be shown
        assert "Submitting for analysis" in result.output
        assert "Submitting for LRC generation" in result.output
        # Both jobs should be submitted
        mock_submit_analysis.assert_called_once()
        mock_submit_lrc.assert_called_once()

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    @patch("stream_of_worship.admin.commands.audio.YouTubeDownloader")
    def test_download_without_analysis_flags_only_downloads(
        self, mock_yt_class, mock_r2_class, setup, monkeypatch
    ):
        """Without --analyze/-lrc/--all, download does NOT submit analysis or LRC."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")
        monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-analysis-key")

        # Mock R2 client
        mock_r2 = MagicMock()
        mock_r2.audio_exists.return_value = False
        mock_r2.upload_audio.return_value = "s3://bucket/simple/audio.mp3"
        mock_r2_class.return_value = mock_r2

        # Mock YouTube downloader
        mock_yt = MagicMock()
        mock_yt.build_search_query.return_value = "將天敞開 游智婷 敬拜讚美15"
        mock_yt.preview_video.return_value = {
            "id": "simple",
            "title": "Test Video",
            "duration": 300,
            "webpage_url": "https://youtube.com/watch?v=simple",
        }
        mock_yt.download.return_value = setup["tmp_path"] / "Test Video.mp3"
        mock_yt_class.return_value = mock_yt

        # Create fake downloaded file
        mp3_path = setup["tmp_path"] / "Test Video.mp3"
        mp3_path.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            [
                "audio", "download", "song_001",
                "--config", str(setup["config_path"]),
                "--yes",
                # No --analyze, --lrc, or --all flags
            ],
        )

        assert result.exit_code == 0
        # Should NOT show submission messages
        assert "Submitting for analysis" not in result.output
        assert "Submitting for LRC" not in result.output
        # Upload success message should still appear
        assert "Recording saved" in result.output


class TestDeleteCommand:
    """Tests for 'audio delete' command."""

    @pytest.fixture
    def setup(self, tmp_path):
        """Create a temp database seeded with song and recording."""
        db_path = tmp_path / "test.db"
        client = DatabaseClient(db_path)
        client.initialize_schema()

        song = Song(
            id="song_001",
            title="測試歌曲",
            source_url="https://example.com/1",
            scraped_at=datetime.now().isoformat(),
        )
        client.insert_song(song)

        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="aaaaaaaaaaaa",
            song_id="song_001",
            original_filename="test.mp3",
            file_size_bytes=1000000,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://bucket/aaaaaaaaaaaa/audio.mp3",
        )
        client.insert_recording(recording)

        config_path = tmp_path / "config.toml"
        config_path.write_text(f'''[database]
path = "{db_path}"

[r2]
bucket = "test-bucket"
endpoint_url = "https://test.r2.dev"
region = "auto"
''')

        return {
            "db_path": db_path,
            "config_path": config_path,
            "song": song,
            "recording": recording,
        }

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    def test_delete_without_confirmation(self, mock_r2_class, setup, monkeypatch):
        """Prompts for confirmation without --yes."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")

        mock_r2 = MagicMock()
        mock_r2_class.return_value = mock_r2

        result = runner.invoke(
            app,
            ["audio", "delete", "song_001", "--config", str(setup["config_path"])],
            input="y",  # Confirm
        )

        assert result.exit_code == 0
        assert "Delete this recording" in result.output
        # After confirmation, recording should be deleted
        db_client = DatabaseClient(setup["db_path"])
        assert db_client.get_recording_by_song_id("song_001") is None

    @patch("stream_of_worship.admin.commands.audio.R2Client")
    def test_delete_with_yes_flag(self, mock_r2_class, setup, monkeypatch):
        """Skips confirmation with --yes flag."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")

        mock_r2 = MagicMock()
        mock_r2_class.return_value = mock_r2

        result = runner.invoke(
            app,
            ["audio", "delete", "song_001", "--config", str(setup["config_path"]), "--yes"],
        )

        assert result.exit_code == 0
        assert "deleted successfully" in result.output
        # Verify recording deleted
        db_client = DatabaseClient(setup["db_path"])
        assert db_client.get_recording_by_song_id("song_001") is None

    def test_delete_removes_from_database(self, setup, monkeypatch):
        """Removes recording from database."""
        monkeypatch.setenv("SOW_R2_ACCESS_KEY_ID", "test-key")
        monkeypatch.setenv("SOW_R2_SECRET_ACCESS_KEY", "test-secret")

        result = runner.invoke(
            app,
            ["audio", "delete", "song_001", "--config", str(setup["config_path"]), "--yes"],
        )

        assert result.exit_code == 0
        # Verify recording deleted from database
        db_client = DatabaseClient(setup["db_path"])
        assert db_client.get_recording_by_song_id("song_001") is None

    def test_delete_nonexistent_recording(self, tmp_path):
        """Error when recording doesn't exist."""
        db_path = tmp_path / "test.db"
        client = DatabaseClient(db_path)
        client.initialize_schema()

        song = Song(
            id="song_001",
            title="測試",
            source_url="https://example.com",
            scraped_at=datetime.now().isoformat(),
        )
        client.insert_song(song)

        config_path = tmp_path / "config.toml"
        config_path.write_text(f'''[database]
path = "{db_path}"

[r2]
bucket = "test-bucket"
endpoint_url = "https://test.r2.dev"
region = "auto"
''')

        result = runner.invoke(
            app,
            ["audio", "delete", "song_001", "--config", str(config_path), "--yes"],
        )

        assert result.exit_code == 1
        assert "No recording found" in result.output
