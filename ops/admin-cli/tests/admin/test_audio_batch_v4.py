"""Tests for the v4 audio batch command (step flags, force scoping, resume).

These tests exercise the CLI argument-validation paths that run before any
database or R2 access. They use the Typer CliRunner against the real app.
"""

from unittest.mock import MagicMock, patch

from rich.console import Console
from typer.testing import CliRunner

from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.main import app

runner = CliRunner()

WIDE_ENV = {"COLUMNS": "200"}


class TestBatchStepFlags:
    """Step flag selection and validation."""

    def test_no_step_flags_exits_1(self, tmp_path):
        """No step flags and no --all-steps exits 1 with usage."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid/invalid"\n')
        result = runner.invoke(
            app,
            ["audio", "batch", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "No step flags selected" in result.output

    def test_invalid_format_exits_1(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid/invalid"\n')
        result = runner.invoke(
            app,
            ["audio", "batch", "--analyze", "--format", "xml", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "Invalid format" in result.output

    def test_invalid_analysis_tier_exits_1(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid/invalid"\n')
        result = runner.invoke(
            app,
            [
                "audio",
                "batch",
                "--analyze",
                "--analysis-tier",
                "medium",
                "--config",
                str(config_path),
            ],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "Invalid analysis tier" in result.output

    def test_invalid_analysis_status_exits_1(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid/invalid"\n')
        result = runner.invoke(
            app,
            [
                "audio",
                "batch",
                "--analyze",
                "--analysis-status",
                "bogus",
                "--config",
                str(config_path),
            ],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "Invalid analysis status" in result.output

    def test_partial_is_valid_analysis_status(self, tmp_path):
        """'partial' is accepted as a valid analysis status filter."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid/invalid"\n')
        result = runner.invoke(
            app,
            [
                "audio",
                "batch",
                "--analyze",
                "--analysis-status",
                "partial",
                "--config",
                str(config_path),
            ],
            env=WIDE_ENV,
        )
        # Should NOT fail on validation; it will fail later on DB load.
        assert "Invalid analysis status" not in result.output


class TestForceScoping:
    """--force validation per the v4 spec."""

    def test_force_all_steps_exits_1(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid/invalid"\n')
        result = runner.invoke(
            app,
            ["audio", "batch", "--all-steps", "--force", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "--force with --all-steps" in result.output

    def test_force_no_step_flags_exits_1(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid/invalid"\n')
        result = runner.invoke(
            app,
            ["audio", "batch", "--force", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1

    def test_force_download_exits_1_with_hint(self, tmp_path):
        """--force --download is rejected with the two-step purge hint."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid/invalid"\n')
        result = runner.invoke(
            app,
            ["audio", "batch", "--download", "--force", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "--force --download is not supported" in result.output
        assert "purge-soft-deletes" in result.output

    def test_force_multiple_steps_exits_1(self, tmp_path):
        """--force with two step flags exits 1."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid/invalid"\n')
        result = runner.invoke(
            app,
            ["audio", "batch", "--lrc", "--analyze", "--force", "--config", str(config_path)],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "exactly one step flag" in result.output


class TestResumeMutualExclusivity:
    """--resume is mutually exclusive with all selection flags and --force."""

    def test_resume_with_force_exits_1(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid/invalid"\n')
        result = runner.invoke(
            app,
            [
                "audio",
                "batch",
                "--resume",
                "/tmp/manifest.json",
                "--force",
                "--config",
                str(config_path),
            ],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "--resume is mutually exclusive" in result.output

    def test_resume_with_analyze_exits_1(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid/invalid"\n')
        result = runner.invoke(
            app,
            [
                "audio",
                "batch",
                "--resume",
                "/tmp/manifest.json",
                "--analyze",
                "--config",
                str(config_path),
            ],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "--resume is mutually exclusive" in result.output

    def test_resume_with_album_exits_1(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid/invalid"\n')
        result = runner.invoke(
            app,
            [
                "audio",
                "batch",
                "--resume",
                "/tmp/manifest.json",
                "--album",
                "foo",
                "--config",
                str(config_path),
            ],
            env=WIDE_ENV,
        )
        assert result.exit_code == 1
        assert "--resume is mutually exclusive" in result.output


class TestRecordingModelAnalysis:
    """Tests for the Recording model analysis properties."""

    def test_has_analysis_partial_is_true(self):
        from stream_of_worship.admin.db.models import Recording

        recording = Recording(
            content_hash="abc",
            hash_prefix="abc",
            original_filename="test.mp3",
            file_size_bytes=100,
            imported_at="2024-01-01T00:00:00",
            analysis_status="partial",
        )
        assert recording.has_analysis is True
        assert recording.has_fast_analysis is True
        assert recording.has_full_analysis is False

    def test_has_analysis_completed_is_true(self):
        from stream_of_worship.admin.db.models import Recording

        recording = Recording(
            content_hash="abc",
            hash_prefix="abc",
            original_filename="test.mp3",
            file_size_bytes=100,
            imported_at="2024-01-01T00:00:00",
            analysis_status="completed",
        )
        assert recording.has_analysis is True
        assert recording.has_fast_analysis is True
        assert recording.has_full_analysis is True

    def test_has_analysis_pending_is_false(self):
        from stream_of_worship.admin.db.models import Recording

        recording = Recording(
            content_hash="abc",
            hash_prefix="abc",
            original_filename="test.mp3",
            file_size_bytes=100,
            imported_at="2024-01-01T00:00:00",
            analysis_status="pending",
        )
        assert recording.has_analysis is False
        assert recording.has_fast_analysis is False
        assert recording.has_full_analysis is False


class TestManifestHelpers:
    """Tests for manifest writer/loader helpers."""

    def test_write_and_load_manifest(self, tmp_path):
        from stream_of_worship.admin.commands.audio import _write_manifest, _load_manifest

        batch_id = "2026-06-30T0215_batch"
        results = {"song_1": {"analyze": "completed"}}
        entries = [
            {
                "song_id": "song_1",
                "hash_prefix": "feedface",
                "step": "analyze",
                "tier": "fast",
                "job_id": "job_a1b2",
                "status": "completed",
                "attempts": 1,
                "previous_job_id": None,
                "error_class": None,
                "error_message": None,
                "submitted_at": "2026-06-30T02:15:04Z",
                "completed_at": "2026-06-30T02:20:00Z",
            }
        ]
        path = _write_manifest(
            batch_id,
            results,
            tmp_path,
            ["analyze"],
            "fast",
            120,
            "2026-06-30T02:15:00Z",
            entries,
        )
        assert path is not None
        assert path.exists()

        loaded = _load_manifest(path)
        assert loaded is not None
        assert loaded["batch_id"] == batch_id
        assert loaded["selected_steps"] == ["analyze"]
        assert loaded["analysis_tier"] == "fast"
        assert len(loaded["songs"]) == 1
        assert loaded["songs"][0]["song_id"] == "song_1"
        assert loaded["songs"][0]["status"] == "completed"

    def test_manifest_dir_env_override(self, tmp_path, monkeypatch):
        from stream_of_worship.admin.commands.audio import _get_manifest_dir

        custom = tmp_path / "custom_manifests"
        monkeypatch.setenv("SOW_BATCH_MANIFEST_DIR", str(custom))
        assert _get_manifest_dir() == custom

    def test_manifest_dir_default(self, monkeypatch):
        from stream_of_worship.admin.commands.audio import _get_manifest_dir

        monkeypatch.delenv("SOW_BATCH_MANIFEST_DIR", raising=False)
        result = _get_manifest_dir()
        assert "sow-admin" in str(result)
        assert "batch" in str(result)


# ---------------------------------------------------------------------------
# Helpers for album-filter unit tests
# ---------------------------------------------------------------------------

ALBUM_FULL = "聽見這世代的呼喚"
ALBUM_PARTIAL = "聽見這世代的"


def _make_recording(
    song_id: str,
    hash_prefix: str = "abc123def456",
    download_status: str = "completed",
    analysis_status: str = "completed",
    lrc_status: str = "completed",
) -> Recording:
    return Recording(
        content_hash=hash_prefix + "0" * 52,
        hash_prefix=hash_prefix,
        original_filename=f"{song_id}.mp3",
        file_size_bytes=1000,
        imported_at="2024-01-01T00:00:00",
        song_id=song_id,
        download_status=download_status,
        analysis_status=analysis_status,
        lrc_status=lrc_status,
    )


def _make_song(
    song_id: str,
    title: str = "恩典之路",
    album_name: str | None = ALBUM_FULL,
    album_series: str | None = None,
) -> Song:
    return Song(
        id=song_id,
        title=title,
        source_url="https://example.com",
        scraped_at="2024-01-01T00:00:00",
        album_name=album_name,
        album_series=album_series,
    )


class TestResolveSongIdsAlbumFilter:
    """Unit tests for _resolve_song_ids album substring matching (R1, R2, R3)."""

    def test_resolve_album_substring_includes_recorded_and_unrecorded(self):
        from stream_of_worship.admin.commands.audio import _resolve_song_ids

        db = MagicMock()
        recorded_song_id = "song_001"
        unrecorded_song_id = "song_002"
        recording = _make_recording(recorded_song_id)
        # Phase 1 returns the recorded song
        db.list_recordings_with_songs.return_value = [
            (recording, "恩典之路", ALBUM_FULL, None),
        ]
        # Phase 2 returns the unrecorded song
        db.list_songs.return_value = [_make_song(unrecorded_song_id, "蒙恩")]
        # get_recording_by_song_id returns None for unrecorded
        db.get_recording_by_song_id.return_value = None

        result = _resolve_song_ids(
            db,
            album=ALBUM_PARTIAL,
            song=None,
            lrc_status=None,
            download_status=None,
            analysis_status=None,
            stdin=False,
            limit=None,
        )

        assert recorded_song_id in result
        assert unrecorded_song_id in result
        # Verify album was pushed to SQL layer
        db.list_recordings_with_songs.assert_called_once()
        call_kwargs = db.list_recordings_with_songs.call_args
        assert call_kwargs.kwargs.get("album") == ALBUM_PARTIAL

    def test_resolve_album_exact_match_still_works(self):
        from stream_of_worship.admin.commands.audio import _resolve_song_ids

        db = MagicMock()
        recorded_song_id = "song_001"
        unrecorded_song_id = "song_002"
        recording = _make_recording(recorded_song_id)
        db.list_recordings_with_songs.return_value = [
            (recording, "恩典之路", ALBUM_FULL, None),
        ]
        db.list_songs.return_value = [_make_song(unrecorded_song_id, "蒙恩")]
        db.get_recording_by_song_id.return_value = None

        result = _resolve_song_ids(
            db,
            album=ALBUM_FULL,
            song=None,
            lrc_status=None,
            download_status=None,
            analysis_status=None,
            stdin=False,
            limit=None,
        )

        assert recorded_song_id in result
        assert unrecorded_song_id in result

    def test_resolve_album_matches_album_series(self):
        """Album substring should match via album_series field too (R1)."""
        from stream_of_worship.admin.commands.audio import _resolve_song_ids

        db = MagicMock()
        song_id = "song_010"
        recording = _make_recording(song_id)
        # The recording's song has album_name="Soaking Album" but
        # album_series="敬拜讚美15". The SQL ILIKE matches on album_series.
        db.list_recordings_with_songs.return_value = [
            (recording, "一些歌", "Soaking Album", "敬拜讚美15"),
        ]
        db.list_songs.return_value = []

        result = _resolve_song_ids(
            db,
            album="敬拜讚美",
            song=None,
            lrc_status=None,
            download_status=None,
            analysis_status=None,
            stdin=False,
            limit=None,
        )

        assert song_id in result

    def test_resolve_album_with_status_filter_excludes_unrecorded(self):
        """When a status filter is present, Phase 2 (unrecorded) is skipped (R3)."""
        from stream_of_worship.admin.commands.audio import _resolve_song_ids

        db = MagicMock()
        recorded_song_id = "song_001"
        unrecorded_song_id = "song_002"
        recording = _make_recording(recorded_song_id, analysis_status="pending")
        db.list_recordings_with_songs.return_value = [
            (recording, "恩典之路", ALBUM_FULL, None),
        ]
        db.list_songs.return_value = [_make_song(unrecorded_song_id, "蒙恩")]

        result = _resolve_song_ids(
            db,
            album=ALBUM_PARTIAL,
            song=None,
            lrc_status=None,
            download_status=None,
            analysis_status="incomplete",
            stdin=False,
            limit=None,
        )

        assert recorded_song_id in result
        assert unrecorded_song_id not in result
        # Phase 2 should not have been called
        db.list_songs.assert_not_called()

    def test_resolve_album_no_match_empty(self):
        from stream_of_worship.admin.commands.audio import _resolve_song_ids

        db = MagicMock()
        db.list_recordings_with_songs.return_value = []
        db.list_songs.return_value = []

        result = _resolve_song_ids(
            db,
            album="不存在的專輯",
            song=None,
            lrc_status=None,
            download_status=None,
            analysis_status=None,
            stdin=False,
            limit=None,
        )

        assert result == []

    def test_resolve_song_filter_still_works(self):
        from stream_of_worship.admin.commands.audio import _resolve_song_ids

        db = MagicMock()
        song_id = "song_001"
        recording = _make_recording(song_id)
        db.list_recordings_with_songs.return_value = [
            (recording, "恩典之路", ALBUM_FULL, None),
        ]
        db.list_songs.return_value = []

        result = _resolve_song_ids(
            db,
            album=None,
            song="恩典",
            lrc_status=None,
            download_status=None,
            analysis_status=None,
            stdin=False,
            limit=None,
        )

        assert song_id in result


class TestDryRunGroupedOutput:
    """Tests for _print_dry_run_v4 grouped output (R4)."""

    def test_dry_run_grouped_output(self):
        from stream_of_worship.admin.commands.audio import _print_dry_run_v4

        db = MagicMock()
        # 3 with recording, 2 missing, 1 not found
        with_recs = [
            (_make_song("song_001", "恩典之路"), _make_recording("song_001", "hash001")),
            (_make_song("song_002", "讚美詩"), _make_recording("song_002", "hash002")),
            (_make_song("song_003", "哈利路亞"), _make_recording("song_003", "hash003")),
        ]
        missing = [
            _make_song("song_004", "蒙恩"),
            _make_song("song_005", "讓我尋見祢"),
        ]
        orphan_id = "song_999"

        song_ids = [s.id for s, _ in with_recs] + [s.id for s in missing] + [orphan_id]

        # get_song returns the song; get_recording_by_song_id returns recording or None
        song_map = {}
        for song, rec in with_recs:
            song_map[song.id] = song
        for song in missing:
            song_map[song.id] = song

        rec_map = {s.id: r for s, r in with_recs}

        def mock_get_song(sid):
            return song_map.get(sid)

        def mock_get_recording(sid):
            return rec_map.get(sid)

        db.get_song.side_effect = mock_get_song
        db.get_recording_by_song_id.side_effect = mock_get_recording

        test_console = Console(record=True, width=200)
        with patch("stream_of_worship.admin.commands.audio.console", test_console):
            _print_dry_run_v4(db, song_ids, ["analyze", "embedding"], False, "fast", 120)

        output = test_console.export_text()

        assert "With recording (3)" in output
        assert "Missing recording — will download (2)" in output
        assert "Song not found (1)" in output
        assert "恩典之路" in output
        assert "蒙恩" in output
        assert "讓我尋見祢" in output
        assert orphan_id in output

    def test_dry_run_count_line(self):
        from stream_of_worship.admin.commands.audio import _print_dry_run_v4

        db = MagicMock()
        with_recs = [
            (_make_song("song_001", "恩典之路"), _make_recording("song_001", "hash001")),
            (_make_song("song_002", "讚美詩"), _make_recording("song_002", "hash002")),
            (_make_song("song_003", "哈利路亞"), _make_recording("song_003", "hash003")),
        ]
        missing = [
            _make_song("song_004", "蒙恩"),
            _make_song("song_005", "讓我尋見祢"),
        ]

        song_ids = [s.id for s, _ in with_recs] + [s.id for s in missing]

        song_map = {s.id: s for s, _ in with_recs}
        song_map.update({s.id: s for s in missing})
        rec_map = {s.id: r for s, r in with_recs}

        db.get_song.side_effect = lambda sid: song_map.get(sid)
        db.get_recording_by_song_id.side_effect = lambda sid: rec_map.get(sid)

        test_console = Console(record=True, width=200)
        with patch("stream_of_worship.admin.commands.audio.console", test_console):
            _print_dry_run_v4(db, song_ids, ["analyze"], False, "fast", 120)

        output = test_console.export_text()

        assert "5 song(s)" in output
        assert "3 with recording" in output
        assert "2 missing" in output


class TestProbeBatchAlbumFilter:
    """Regression test for probe-batch --album matching album_series (R6)."""

    def test_probe_batch_album_matches_album_series(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[database]\nurl = "postgresql://invalid/invalid"\n')

        # Recording whose song has album_series="敬拜讚美15"
        recording = _make_recording("song_010", hash_prefix="feedface")
        song = _make_song(
            "song_010",
            title="一些歌",
            album_name="Soaking Album",
            album_series="敬拜讚美15",
        )

        mock_db = MagicMock()
        mock_db.get_recordings_without_duration.return_value = [recording]
        mock_db.list_recordings.return_value = [recording]
        # list_songs is the new batched lookup — returns the matching song
        mock_db.list_songs.return_value = [song]
        # get_song is called in the dry-run table rendering
        mock_db.get_song.return_value = song

        with (
            patch(
                "stream_of_worship.admin.commands.audio.is_ffprobe_available",
                return_value=True,
            ),
            patch(
                "stream_of_worship.admin.commands.audio.AdminConfig.load",
            ),
            patch(
                "stream_of_worship.admin.commands.audio.get_db_client",
                return_value=mock_db,
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "audio",
                    "probe-batch",
                    "--album",
                    "敬拜讚美",
                    "--dry-run",
                    "--config",
                    str(config_path),
                ],
                env=WIDE_ENV,
            )

        assert result.exit_code == 0
        assert "No recordings to probe" not in result.output
        # The recording should appear in the dry-run output
        assert "feedface" in result.output or "一些歌" in result.output
        # Verify the batched lookup was used (not N+1 get_song)
        mock_db.list_songs.assert_called_once()
        call_kwargs = mock_db.list_songs.call_args
        assert call_kwargs.kwargs.get("album") == "敬拜讚美"
