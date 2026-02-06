"""Tests for audio CLI commands."""

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

    def test_download_existing_recording(self, setup):
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

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup["config_path"])],
        )

        assert result.exit_code == 0
        assert "Recording already exists" in result.output
        assert "aaaaaaaaaaaa" in result.output

    def test_download_dry_run_shows_metadata(self, setup):
        """Dry run displays song metadata and search query without downloading."""
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
        mock_downloader.download.return_value = fake_audio
        mock_downloader_cls.return_value = mock_downloader

        mock_compute_hash.return_value = "b" * 64
        mock_get_prefix.return_value = "bbbbbbbbbbbb"

        mock_r2 = MagicMock()
        mock_r2.upload_audio.return_value = "s3://sow-audio/bbbbbbbbbbbb/audio.mp3"
        mock_r2_cls.return_value = mock_r2

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup["config_path"])],
        )

        assert result.exit_code == 0
        assert "Downloaded: downloaded.mp3" in result.output
        assert "bbbbbbbbbbbb" in result.output
        assert "Uploaded" in result.output
        assert "Recording saved" in result.output

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
        mock_downloader.download.side_effect = RuntimeError("Network error")
        mock_downloader_cls.return_value = mock_downloader

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup["config_path"])],
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
        mock_downloader.download.return_value = fake_audio
        mock_downloader_cls.return_value = mock_downloader

        mock_compute_hash.return_value = "d" * 64
        mock_get_prefix.return_value = "dddddddddddd"

        mock_r2 = MagicMock()
        mock_r2.upload_audio.side_effect = Exception("connection timeout")
        mock_r2_cls.return_value = mock_r2

        result = runner.invoke(
            app,
            ["audio", "download", "song_001", "--config", str(setup["config_path"])],
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
        """ids format outputs one hash prefix per line."""
        result = runner.invoke(
            app,
            [
                "audio", "list",
                "--config", str(setup_with_recordings["config_path"]),
                "--format", "ids",
            ],
        )

        assert result.exit_code == 0
        assert "aaaaaaaaaaaa" in result.output
        assert "bbbbbbbbbbbb" in result.output

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

    def test_show_nonexistent_recording(self, setup_with_recording):
        """Reports an error for a hash prefix that does not exist."""
        result = runner.invoke(
            app,
            [
                "audio", "show", "nonexistent",
                "--config", str(setup_with_recording["config_path"]),
            ],
        )

        assert result.exit_code == 1
        assert "Recording not found" in result.output

    def test_show_displays_basic_fields(self, setup_with_recording):
        """All basic metadata fields are rendered."""
        result = runner.invoke(
            app,
            [
                "audio", "show", "dddddddddddd",
                "--config", str(setup_with_recording["config_path"]),
            ],
        )

        assert result.exit_code == 0
        assert "dddddddddddd" in result.output
        assert "d" * 64 in result.output  # full hash
        assert "test_song.mp3" in result.output
        assert "測試歌曲" in result.output
        assert "s3://sow-audio/dddddddddddd/audio.mp3" in result.output

    def test_show_displays_analysis_results(self, setup_with_recording):
        """Analysis section is shown when status is completed."""
        result = runner.invoke(
            app,
            [
                "audio", "show", "dddddddddddd",
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

        recording = Recording(
            content_hash="e" * 64,
            hash_prefix="eeeeeeeeeeee",
            original_filename="pending.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-15T10:30:00",
            analysis_status="pending",
        )
        client.insert_recording(recording)

        config_path = tmp_path / "config.toml"
        config_path.write_text(f'[database]\npath = "{db_path}"\n')

        result = runner.invoke(
            app, ["audio", "show", "eeeeeeeeeeee", "--config", str(config_path)]
        )

        assert result.exit_code == 0
        assert "eeeeeeeeeeee" in result.output
        assert "pending" in result.output
        assert "Analysis Results" not in result.output

    def test_show_recording_without_linked_song(self, tmp_path):
        """Recording with no song_id renders without song info."""
        db_path = tmp_path / "test.db"
        client = DatabaseClient(db_path)
        client.initialize_schema()

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

        result = runner.invoke(
            app, ["audio", "show", "ffffffffffff", "--config", str(config_path)]
        )

        assert result.exit_code == 0
        assert "ffffffffffff" in result.output
        assert "orphan.mp3" in result.output


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

    def test_analyze_recording_not_found_by_hash(self, setup):
        """Error for nonexistent hash prefix."""
        result = runner.invoke(
            app,
            [
                "audio", "analyze", "nonexistent123",
                "--config", str(setup["config_path"]),
            ],
        )

        assert result.exit_code == 1
        assert "Recording not found" in result.output

    def test_analyze_recording_not_found_by_song_id(self, setup):
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
        assert "has no recording" in result.output

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
                "audio", "analyze", "aaaaaaaaaaaa",
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
                "audio", "analyze", "aaaaaaaaaaaa",
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
                "audio", "analyze", "aaaaaaaaaaaa",
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
                "audio", "analyze", "aaaaaaaaaaaa",
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
                "audio", "analyze", "aaaaaaaaaaaa",
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
                "audio", "analyze", "aaaaaaaaaaaa",
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
                "audio", "analyze", "aaaaaaaaaaaa",
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
                "audio", "analyze", "aaaaaaaaaaaa",
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
    def test_analyze_by_hash_prefix(self, mock_client_cls, setup, monkeypatch):
        """Resolves by hash prefix."""
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
                "audio", "analyze", "aaaaaaaaaaaa",
                "--config", str(setup["config_path"]),
            ],
        )

        assert result.exit_code == 0
        mock_client.submit_analysis.assert_called_once()

    @patch("stream_of_worship.admin.commands.audio.AnalysisClient")
    def test_analyze_by_song_id(self, mock_client_cls, setup, monkeypatch):
        """Resolves by song_id."""
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
                "audio", "analyze", "aaaaaaaaaaaa",
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
                "audio", "analyze", "aaaaaaaaaaaa",
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
                "audio", "analyze", "aaaaaaaaaaaa",
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
                "audio", "analyze", "aaaaaaaaaaaa",
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
        assert "aaaaaaaaaaaa" in result.output

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
