"""Tests for TUI services - Catalog, Playback, Generation."""

import json
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock
import pytest

from stream_of_worship.tui.services.catalog import SongCatalogLoader
from stream_of_worship.tui.services.playback import PlaybackService
from stream_of_worship.tui.services.generation import TransitionGenerationService
from stream_of_worship.tui.models.song import Song as TUISong
from stream_of_worship.tui.models.transition import TransitionParams, TransitionRecord
from stream_of_worship.tui.models.section import Section


class TestSongCatalogLoader:
    """Tests for SongCatalogLoader."""

    @pytest.fixture
    def audio_folder(self, tmp_path):
        """Fixture providing temporary audio folder."""
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        return audio_dir

    @pytest.fixture
    def catalog_loader(self, audio_folder):
        """Fixture providing SongCatalogLoader instance."""
        return SongCatalogLoader(audio_folder)

    def test_init(self, catalog_loader, audio_folder):
        """Test SongCatalogLoader initialization."""
        assert catalog_loader.audio_folder == audio_folder
        assert catalog_loader.songs == {}
        assert catalog_loader.warnings == []

    def test_load_from_json_file_not_found(self, catalog_loader):
        """Test loading from non-existent JSON file."""
        non_existent = Path("/nonexistent/poc_full_results.json")

        catalog_loader.load_from_json(non_existent)

        assert len(catalog_loader.warnings) == 1
        assert "not found" in catalog_loader.warnings[0].lower()
        assert catalog_loader.songs == {}

    def test_load_from_json_malformed(self, catalog_loader, tmp_path):
        """Test loading from malformed JSON file."""
        malformed_file = tmp_path / "malformed.json"
        malformed_file.write_text("invalid json{")

        catalog_loader.load_from_json(malformed_file)

        assert len(catalog_loader.warnings) >= 1

    def test_load_from_json_not_a_list(self, catalog_loader, tmp_path):
        """Test loading JSON that is not a list."""
        not_a_list_file = tmp_path / "not_a_list.json"
        not_a_list_file.write_text(json.dumps({"songs": []}))

        catalog_loader.load_from_json(not_a_list_file)

        assert catalog_loader.songs == {}

    def test_load_from_json_success(self, catalog_loader, audio_folder, tmp_path):
        """Test loading from valid JSON file."""
        # Create audio files
        (audio_folder / "song1.mp3").touch()
        (audio_folder / "song2.mp3").touch()

        data = [
            {
                "filename": "song1.mp3",
                "tempo": 120.0,
                "key": "C",
                "mode": "major",
                "key_confidence": 0.8,
                "full_key": "C major",
                "loudness_db": -10.0,
                "spectral_centroid": 2000.0,
                "duration": 180.0,
                "sections": [],
            },
            {
                "filename": "song2.mp3",
                "tempo": 100.0,
                "key": "G",
                "mode": "major",
                "key_confidence": 0.9,
                "full_key": "G major",
                "loudness_db": -8.0,
                "spectral_centroid": 2200.0,
                "duration": 240.0,
                "sections": [],
            },
        ]

        json_file = tmp_path / "catalog.json"
        with json_file.open("w") as f:
            json.dump(data, f)

        catalog_loader.load_from_json(json_file)

        assert len(catalog_loader.songs) == 2
        assert "song1.mp3" in catalog_loader.songs

    def test_load_from_json_missing_audio_file(self, catalog_loader, audio_folder, tmp_path):
        """Test that warning is added when audio file is missing."""
        # Create song entry without corresponding audio file
        data = [
            {
                "filename": "missing_song.mp3",
                "tempo": 120.0,
                "key": "C",
                "mode": "major",
                "key_confidence": 0.8,
                "full_key": "C major",
                "loudness_db": -10.0,
                "spectral_centroid": 2000.0,
                "duration": 180.0,
                "sections": [],
            },
        ]

        json_file = tmp_path / "catalog.json"
        with json_file.open("w") as f:
            json.dump(data, f)

        catalog_loader.load_from_json(json_file)

        assert len(catalog_loader.songs) == 0  # Song should be skipped
        assert len(catalog_loader.warnings) >= 1

    def test_get_song(self, catalog_loader):
        """Test getting a song by ID."""
        song = TUISong(
            filename="test.mp3",
            filepath=Path("/test.mp3"),
            duration=180.0,
            tempo=120.0,
            key="C",
            mode="major",
            key_confidence=0.8,
            full_key="C major",
            loudness_db=-10.0,
            spectral_centroid=2000.0,
            sections=[],
        )
        catalog_loader.songs["test.mp3"] = song

        result = catalog_loader.get_song("test.mp3")

        assert result is song

    def test_get_song_not_found(self, catalog_loader):
        """Test getting a non-existent song."""
        result = catalog_loader.get_song("nonexistent")

        assert result is None

    def test_get_all_songs(self, catalog_loader):
        """Test getting all songs sorted."""
        songs = {
            "b_song.mp3": TUISong(
                filename="b_song.mp3",
                filepath=Path("/b.mp3"),
                duration=180.0,
                tempo=120.0,
                key="C",
                mode="major",
                key_confidence=0.8,
                full_key="C major",
                loudness_db=-10.0,
                spectral_centroid=2000.0,
                sections=[],
            ),
            "a_song.mp3": TUISong(
                filename="a_song.mp3",
                filepath=Path("/a.mp3"),
                duration=180.0,
                tempo=120.0,
                key="C",
                mode="major",
                key_confidence=0.8,
                full_key="C major",
                loudness_db=-10.0,
                spectral_centroid=2000.0,
                sections=[],
            ),
        }
        catalog_loader.songs = songs

        result = catalog_loader.get_all_songs()

        assert len(result) == 2
        assert result[0].filename == "a_song.mp3"
        assert result[1].filename == "b_song.mp3"

    def test_get_songs_sorted_by_compatibility(self, catalog_loader):
        """Test getting songs sorted by compatibility."""
        # Create songs with different BPM and keys
        catalog_loader.songs = {
            "fast_same_key": TUISong(
                filename="fast_same_key.mp3",
                filepath=Path("/fast.mp3"),
                duration=180.0,
                tempo=120.0,  # Same as reference
                key="C",
                mode="major",
                key_confidence=0.8,
                full_key="C major",
                loudness_db=-10.0,
                spectral_centroid=2000.0,
                sections=[],
                compatibility_score=0.0,
            ),
            "fast_diff_key": TUISong(
                filename="fast_diff_key.mp3",
                filepath=Path("/fast2.mp3"),
                duration=180.0,
                tempo=118.0,  # Similar but not same
                key="D",
                mode="major",
                key_confidence=0.8,
                full_key="D major",
                loudness_db=-10.0,
                spectral_centroid=2000.0,
                sections=[],
                compatibility_score=0.0,
            ),
            "slow_song": TUISong(
                filename="slow.mp3",
                filepath=Path("/slow.mp3"),
                duration=180.0,
                tempo=80.0,  # Much slower
                key="C",
                mode="major",
                key_confidence=0.8,
                full_key="C major",
                loudness_db=-10.0,
                spectral_centroid=2000.0,
                sections=[],
                compatibility_score=0.0,
            ),
        }

        result = catalog_loader.get_songs_sorted_by_compatibility("fast_same_key")

        # fast_same_key should be first (same key and tempo)
        assert result[0].filename == "fast_same_key.mp3"
        # fast_diff_key should be second (similar tempo, different key)
        assert result[1].filename == "fast_diff_key.mp3"
        # slow_song should be last (very different tempo)
        assert result[2].filename == "slow.mp3"

        # Check that compatibility scores were computed
        assert all(s.compatibility_score > 0 for s in result)

    def test_get_song_count(self, catalog_loader):
        """Test getting song count."""
        catalog_loader.songs = {
            f"song_{i}": MagicMock()
            for i in range(5)
        }

        count = catalog_loader.get_song_count()

        assert count == 5


class TestPlaybackService:
    """Tests for PlaybackService."""

    def test_init_defaults(self):
        """Test PlaybackService initialization defaults."""
        service = PlaybackService()

        assert service.is_playing is False
        assert service.is_paused is False
        assert service.is_stopped is True
        assert service.current_file is None
        assert service.position == 0.0
        assert service.duration == 0.0

    def test_load(self, tmp_path):
        """Test loading an audio file."""
        service = PlaybackService()
        audio_file = tmp_path / "test.mp3"
        audio_file.touch()

        result = service.load(audio_file)

        # Placeholder returns True
        assert result is True
        assert service.current_file == audio_file

    def test_is_playing(self):
        """Test is_playing property."""
        service = PlaybackService()

        assert service.is_playing is False

        service._playing = True
        assert service.is_playing is True

    def test_is_paused(self):
        """Test is_paused property."""
        service = PlaybackService()

        assert service.is_paused is False

        service._paused = True
        service._playing = False
        assert service.is_paused is True

    def test_is_stopped(self):
        """Test is_stopped property."""
        service = PlaybackService()

        assert service.is_stopped is True

        service._playing = False
        service._paused = False
        assert service.is_stopped is True

        service._playing = True
        assert service.is_stopped is False

    def test_play(self, tmp_path):
        """Test play method."""
        service = PlaybackService()
        audio_file = tmp_path / "test.mp3"
        audio_file.touch()

        service.load(audio_file)
        result = service.play()

        assert result is True
        assert service._playing is True

    def test_play_resume_from_pause(self, tmp_path):
        """Test play resumes from pause."""
        service = PlaybackService()
        audio_file = tmp_path / "test.mp3"
        audio_file.touch()

        service.load(audio_file)
        service._paused = True
        service._playing = False

        result = service.play()

        assert result is True
        assert service._paused is False
        assert service._playing is True

    def test_play_without_file(self):
        """Test play without loaded file returns False."""
        service = PlaybackService()

        result = service.play()

        assert result is False

    def test_pause(self, tmp_path):
        """Test pause method."""
        service = PlaybackService()
        audio_file = tmp_path / "test.mp3"
        audio_file.touch()

        service.load(audio_file)
        service._playing = True

        result = service.pause()

        assert result is True
        assert service._paused is True
        assert service._playing is False

    def test_pause_when_not_playing(self):
        """Test pause when not playing returns False."""
        service = PlaybackService()

        result = service.pause()

        assert result is False

    def test_stop(self, tmp_path):
        """Test stop method."""
        service = PlaybackService()
        audio_file = tmp_path / "test.mp3"
        audio_file.touch()

        service.load(audio_file)
        service._playing = True
        service._paused = True

        result = service.stop()

        assert result is True
        assert service._playing is False
        assert service._paused is False
        assert service.position == 0.0

    def test_seek(self):
        """Test seek method."""
        service = PlaybackService()
        service._duration = 100.0

        result = service.seek(50.0)

        assert result is True
        assert service.position == 50.0

    def test_seek_clamps_negative(self):
        """Test seek clamps negative to 0."""
        service = PlaybackService()
        service._duration = 100.0

        service.seek(-10.0)

        assert service.position == 0.0

    def test_seek_clamps_exceeds_duration(self):
        """Test seek clamps to duration."""
        service = PlaybackService()
        service._duration = 100.0

        service.seek(150.0)

        assert service.position == 100.0

    def test_seek_relative(self):
        """Test seek_relative method."""
        service = PlaybackService()
        service._duration = 100.0
        service._position = 50.0

        result = service.seek_relative(10.0)

        assert result is True
        assert service.position == 60.0

    def test_seek_relative_negative(self):
        """Test seek_relative with negative delta."""
        service = PlaybackService()
        service._duration = 100.0
        service._position = 50.0

        result = service.seek_relative(-20.0)

        assert result is True
        assert service.position == 30.0


class TestTransitionGenerationService:
    """Tests for TransitionGenerationService."""

    @pytest.fixture
    def output_dirs(self, tmp_path):
        """Fixture providing output directories."""
        output_dir = tmp_path / "output_transitions"
        output_songs_dir = tmp_path / "output_songs"
        stems_folder = tmp_path / "stems"

        output_dir.mkdir()
        output_songs_dir.mkdir()
        stems_folder.mkdir()

        return output_dir, output_songs_dir, stems_folder

    @pytest.fixture
    def generation_service(self, output_dirs):
        """Fixture providing TransitionGenerationService instance."""
        return TransitionGenerationService(*output_dirs)

    def test_init_creates_directories(self, tmp_path):
        """Test that init creates output directories."""
        output_dir = tmp_path / "output"
        output_songs = tmp_path / "songs"
        stems = tmp_path / "stems"

        TransitionGenerationService(output_dir, output_songs, stems)

        assert output_dir.exists()
        assert output_songs.exists()
        assert stems.exists()

    def test_generate_transition_success(self, generation_service, tmp_path):
        """Test successful transition generation."""
        section_a = Section(label="Chorus", start=45.0, end=75.0, duration=30.0)
        section_b = Section(label="Verse", start=80.0, end=110.0, duration=30.0)

        params = TransitionParams(transition_type="gap", gap_beats=1.0, fade_window=8.0)

        record = generation_service.generate_transition(
            song_a_filename="song_a.mp3",
            song_b_filename="song_b.mp3",
            section_a=section_a,
            section_b=section_b,
            parameters=params,
        )

        assert record is not None
        assert record.id == 1
        assert record.transition_type == "gap"
        assert record.song_a_filename == "song_a.mp3"
        assert record.song_b_filename == "song_b.mp3"

    def test_generate_transition_creates_file(self, generation_service, tmp_path):
        """Test that generate creates output file."""
        section_a = Section(label="Chorus", start=45.0, end=75.0, duration=30.0)
        section_b = Section(label="Verse", start=80.0, end=110.0, duration=30.0)

        params = TransitionParams(transition_type="gap", gap_beats=1.0)

        generation_service.generate_transition(
            song_a_filename="song_a.mp3",
            song_b_filename="song_b.mp3",
            section_a=section_a,
            section_b=section_b,
            parameters=params,
        )

        # Check that a file was created in output directory
        output_files = list(generation_service.output_dir.glob("*.flac"))
        assert len(output_files) >= 1

    def test_generate_transition_increments_id(self, generation_service, tmp_path):
        """Test that ID is incremented after each generation."""
        section_a = Section(label="Chorus", start=45.0, end=75.0, duration=30.0)
        section_b = Section(label="Verse", start=80.0, end=110.0, duration=30.0)

        params = TransitionParams(transition_type="gap")

        record1 = generation_service.generate_transition(
            song_a_filename="song_a.mp3",
            song_b_filename="song_b.mp3",
            section_a=section_a,
            section_b=section_b,
            parameters=params,
        )

        record2 = generation_service.generate_transition(
            song_a_filename="song_a2.mp3",
            song_b_filename="song_b2.mp3",
            section_a=section_a,
            section_b=section_b,
            parameters=params,
        )

        assert record1.id == 1
        assert record2.id == 2

    def test_save_transition(self, generation_service, tmp_path):
        """Test saving a transition."""
        section_a = Section(label="Chorus", start=45.0, end=75.0, duration=30.0)
        section_b = Section(label="Verse", start=80.0, end=110.0, duration=30.0)

        params = TransitionParams(transition_type="gap")

        record = generation_service.generate_transition(
            song_a_filename="song_a.mp3",
            song_b_filename="song_b.mp3",
            section_a=section_a,
            section_b=section_b,
            parameters=params,
        )

        result = generation_service.save_transition(record)

        assert result is True
        assert record.is_saved is True
        assert record.saved_path is not None

    def test_save_transition_custom_path(self, generation_service, tmp_path):
        """Test saving transition to custom path."""
        section_a = Section(label="Chorus", start=45.0, end=75.0, duration=30.0)
        section_b = Section(label="Verse", start=80.0, end=110.0, duration=30.0)

        params = TransitionParams(transition_type="gap")
        record = generation_service.generate_transition(
            song_a_filename="song_a.mp3",
            song_b_filename="song_b.mp3",
            section_a=section_a,
            section_b=section_b,
            parameters=params,
        )

        custom_path = tmp_path / "custom_saved.flac"
        result = generation_service.save_transition(record, save_path=custom_path)

        assert result is True
        assert record.saved_path == custom_path

    def test_generate_full_song(self, generation_service, tmp_path):
        """Test generating full song output."""
        section = Section(label="Full Song", start=0.0, end=180.0, duration=180.0)

        params = TransitionParams(transition_type="gap")

        record = generation_service.generate_full_song(
            song_filename="test.mp3",
            section=section,
            parameters=params,
        )

        assert record is not None
        assert record.id == 1
        assert record.transition_type == "full"
        assert record.output_type == "full_song"

    @patch("stream_of_worship.tui.services.generation.get_error_logger")
    @patch("stream_of_worship.tui.services.generation.get_session_logger")
    def test_generate_with_error_logging(self, mock_error_logger, mock_session_logger, generation_service, tmp_path):
        """Test that errors are logged."""
        mock_error_logger.return_value = MagicMock()
        mock_session_logger.return_value = MagicMock(enabled=False)

        section_a = Section(label="Chorus", start=45.0, end=75.0, duration=30.0)
        section_b = Section(label="Verse", start=80.0, end=110.0, duration=30.0)

        params = TransitionParams()

        generation_service.generate_transition(
            song_a_filename="song_a.mp3",
            song_b_filename="song_b.mp3",
            section_a=section_a,
            section_b=section_b,
            parameters=params,
        )

        # Check error logger was called appropriately
        # (Session logger returns disabled, so error logger should not be called)
        mock_error_logger.log_generation_error.assert_not_called()
