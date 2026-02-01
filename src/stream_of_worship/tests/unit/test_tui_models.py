"""Tests for TUI models - Section, Song (TUI version), Transition, Playlist."""

from pathlib import Path
from datetime import datetime
import pytest

from stream_of_worship.tui.models.section import Section
from stream_of_worship.tui.models.song import Song as TUISong
from stream_of_worship.tui.models.transition import TransitionParams, TransitionRecord
from stream_of_worship.tui.models.playlist import (
    Playlist,
    PlaylistItem,
    PlaylistMetadata,
)


class TestSection:
    """Tests for Section dataclass."""

    def test_section_creation(self):
        """Test creating a Section."""
        section = Section(
            label="Chorus",
            start=45.0,
            end=75.0,
            duration=30.0,
        )

        assert section.label == "Chorus"
        assert section.start == 45.0
        assert section.end == 75.0
        assert section.duration == 30.0

    def test_format_time(self):
        """Test format_time method."""
        section = Section(
            label="Verse",
            start=65.0,
            end=125.0,
            duration=60.0,
        )

        assert section.format_time(65.0) == "1:05"
        assert section.format_time(125.0) == "2:05"
        assert section.format_time(180.0) == "3:00"

    def test_format_display(self):
        """Test format_display method."""
        section = Section(
            label="Bridge",
            start=100.0,
            end=145.0,
            duration=45.0,
        )

        result = section.format_display()
        assert "Bridge" in result
        assert "1:40" in result
        assert "2:25" in result
        assert "45s" in result


class TestTUISong:
    """Tests for TUI Song model."""

    def test_song_creation_minimal(self):
        """Test creating a Song with minimal data."""
        song = TUISong(
            filename="test_song.mp3",
            filepath=Path("/path/to/test_song.mp3"),
            duration=180.0,
            tempo=120.0,
            key="C",
            mode="major",
            key_confidence=0.8,
            full_key="C major",
            loudness_db=-12.0,
            spectral_centroid=2500.0,
            sections=[],
        )

        assert song.filename == "test_song.mp3"
        assert song.tempo == 120.0
        assert song.key == "C"
        assert song.mode == "major"

    def test_song_post_init_defaults(self):
        """Test that __post_init__ sets default list values."""
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
            beats=None,  # Will be defaulted
            downbeats=None,  # Will be defaulted
            embeddings_shape=None,  # Will be defaulted
        )

        assert song.beats == []
        assert song.downbeats == []
        assert song.embeddings_shape == []

    def test_id_property(self):
        """Test id property."""
        song = TUISong(
            filename="awesome_song.mp3",
            filepath=Path("/path/awesome_song.mp3"),
            duration=180.0,
            tempo=100.0,
            key="G",
            mode="major",
            key_confidence=0.9,
            full_key="G major",
            loudness_db=-8.0,
            spectral_centroid=2200.0,
            sections=[],
        )

        assert song.id == "awesome_song.mp3"

    def test_display_name_property(self):
        """Test display_name property."""
        song = TUISong(
            filename="praise_song.mp3",
            filepath=Path("/path/praise_song.mp3"),
            duration=180.0,
            tempo=128.5,
            key="D",
            mode="major",
            key_confidence=0.85,
            full_key="D major",
            loudness_db=-10.0,
            spectral_centroid=2300.0,
            sections=[],
        )

        # BPM should be rounded
        assert "128 BPM" in song.display_name
        assert "D major" in song.display_name
        assert "praise_song.mp3" in song.display_name

    def test_format_duration(self):
        """Test format_duration method."""
        song = TUISong(
            filename="test.mp3",
            filepath=Path("/test.mp3"),
            duration=125.5,
            tempo=100.0,
            key="C",
            mode="major",
            key_confidence=0.8,
            full_key="C major",
            loudness_db=-10.0,
            spectral_centroid=2000.0,
            sections=[],
        )

        # Duration is truncated to seconds
        assert song.format_duration() == "2:05"

    def test_from_dict_with_sections(self):
        """Test creating Song from dictionary with sections."""
        data = {
            "filename": "test.mp3",
            "filepath": "/path/test.mp3",
            "duration": 180.0,
            "tempo": 120.0,
            "key": "C",
            "mode": "major",
            "key_confidence": 0.8,
            "full_key": "C major",
            "loudness_db": -10.0,
            "spectral_centroid": 2000.0,
            "sections": [
                {"label": "Verse 1", "start": 0.0, "end": 45.0, "duration": 45.0},
                {"label": "Chorus 1", "start": 45.0, "end": 75.0, "duration": 30.0},
            ],
        }

        song = TUISong.from_dict(data)

        assert len(song.sections) == 2
        assert song.sections[0].label == "Verse 1"
        assert song.sections[1].label == "Chorus 1"

    def test_from_dict_with_defaults(self):
        """Test creating Song from dictionary with minimal data."""
        data = {
            "filename": "test.mp3",
            "filepath": "/test.mp3",
            "duration": 180.0,
            "tempo": 120.0,
            "key": "C",
            "mode": "major",
            "key_confidence": 0.8,
            "full_key": "C major",
            "loudness_db": -10.0,
            "spectral_centroid": 2000.0,
        }

        song = TUISong.from_dict(data)

        # Should have default section list
        assert song.sections == []


class TestTransitionParams:
    """Tests for TransitionParams dataclass."""

    def test_default_values(self):
        """Test default transition parameters."""
        params = TransitionParams()

        assert params.transition_type == "gap"
        assert params.gap_beats == 1.0
        assert params.overlap == 4.0
        assert params.fade_window == 8.0
        assert params.fade_bottom == 0.33
        assert params.stems_to_fade == ["bass", "drums", "other"]
        assert params.from_section_start_adjust == 0
        assert params.from_section_end_adjust == 0
        assert params.to_section_start_adjust == 0
        assert params.to_section_end_adjust == 0

    def test_is_gap_property(self):
        """Test is_gap property."""
        params = TransitionParams(transition_type="gap")
        assert params.is_gap is True

        params.transition_type = "crossfade"
        assert params.is_gap is False

    def test_is_crossfade_property(self):
        """Test is_crossfade property."""
        params = TransitionParams(transition_type="crossfade")
        assert params.is_crossfade is True

        params.transition_type = "gap"
        assert params.is_crossfade is False

    def test_to_dict(self):
        """Test converting to dictionary."""
        params = TransitionParams(
            transition_type="gap",
            gap_beats=2.0,
            overlap=4.0,
            fade_window=12.0,
            fade_bottom=0.25,
            stems_to_fade=["bass", "drums"],
            from_section_start_adjust=-2,
            from_section_end_adjust=1,
            to_section_start_adjust=-1,
            to_section_end_adjust=0,
        )

        result = params.to_dict()

        assert result["type"] == "gap"
        assert result["gap_beats"] == 2.0
        assert result["overlap"] == 4.0
        assert result["fade_window"] == 12.0
        assert result["fade_bottom"] == 0.25
        assert result["stems_to_fade"] == ["bass", "drums"]
        assert result["from_section_start_adjust"] == -2

    def test_from_dict(self):
        """Test creating from dictionary."""
        data = {
            "type": "crossfade",
            "gap_beats": 1.5,
            "overlap": 6.0,
            "fade_window": 10.0,
            "fade_bottom": 0.5,
            "stems_to_fade": ["bass"],
            "from_section_start_adjust": -1,
            "to_section_end_adjust": 2,
        }

        params = TransitionParams.from_dict(data)

        assert params.transition_type == "crossfade"
        assert params.gap_beats == 1.5
        assert params.overlap == 6.0
        assert params.fade_window == 10.0
        assert params.fade_bottom == 0.5
        assert params.stems_to_fade == ["bass"]
        assert params.from_section_start_adjust == -1

    def test_from_dict_with_defaults(self):
        """Test creating from minimal dictionary."""
        data = {"type": "gap"}

        params = TransitionParams.from_dict(data)

        assert params.transition_type == "gap"
        assert params.fade_window == 8.0  # Default
        assert params.stems_to_fade == ["bass", "drums", "other"]  # Default


class TestTransitionRecord:
    """Tests for TransitionRecord dataclass."""

    def test_creation_minimal(self):
        """Test creating a minimal TransitionRecord."""
        now = datetime.now()
        record = TransitionRecord(
            id=1,
            transition_type="gap",
            song_a_filename="song_a.mp3",
            song_b_filename="song_b.mp3",
            section_a_label="Chorus",
            section_b_label="Verse",
            compatibility_score=85.5,
            generated_at=now,
            audio_path=Path("/output/transition.flac"),
        )

        assert record.id == 1
        assert record.transition_type == "gap"
        assert record.is_saved is False
        assert record.saved_path is None

    def test_format_list_display(self):
        """Test format_list_display method."""
        record = TransitionRecord(
            id=123,
            transition_type="crossfade",
            song_a_filename="song_a",
            song_b_filename="song_b",
            section_a_label="Chorus",
            section_b_label="Verse",
            compatibility_score=87.5,
            generated_at=datetime.now(),
            audio_path=Path("/output/test.flac"),
        )

        result = record.format_list_display()
        assert "#123" in result
        assert "Crossfade" in result
        assert "song_a" in result
        assert "song_b" in result
        assert "87%" in result

    def test_format_time(self):
        """Test format_time method."""
        record = TransitionRecord(
            id=1,
            transition_type="gap",
            song_a_filename="song_a",
            song_b_filename="song_b",
            section_a_label="Chorus",
            section_b_label="Verse",
            compatibility_score=85.0,
            generated_at=datetime(2026, 2, 1, 12, 30, 45),
            audio_path=Path("/output/test.flac"),
        )

        result = record.format_time()
        assert "12:30:45" in result

    def test_status_display(self):
        """Test status_display property."""
        record = TransitionRecord(
            id=1,
            transition_type="gap",
            song_a_filename="song_a",
            song_b_filename="song_b",
            section_a_label="Chorus",
            section_b_label="Verse",
            compatibility_score=85.0,
            generated_at=datetime.now(),
            audio_path=Path("/output/test.flac"),
            is_saved=False,
        )

        assert record.status_display == "○ Temporary"

        record.is_saved = True
        record.saved_path = Path("/saved/path.flac")

        assert record.status_display == "● Saved"


class TestPlaylistItem:
    """Tests for PlaylistItem dataclass."""

    def test_creation_minimal(self):
        """Test creating a minimal PlaylistItem."""
        item = PlaylistItem(
            song_id="song_1",
            song_filename="test_song.mp3",
        )

        assert item.song_id == "song_1"
        assert item.song_filename == "test_song.mp3"
        assert item.start_section == 0  # Default
        assert item.end_section is None  # Default
        assert item.transition_to_next is None  # Default

    def test_creation_with_transition(self):
        """Test creating a PlaylistItem with transition."""
        transition = TransitionParams(transition_type="gap")
        item = PlaylistItem(
            song_id="song_1",
            song_filename="test.mp3",
            start_section=1,
            end_section=3,
            transition_to_next=transition,
        )

        assert item.transition_to_next is not None
        assert item.transition_to_next.transition_type == "gap"

    def test_to_dict(self):
        """Test converting to dictionary."""
        transition = TransitionParams(transition_type="crossfade")
        item = PlaylistItem(
            song_id="song_1",
            song_filename="test.mp3",
            start_section=0,
            end_section=2,
            transition_to_next=transition,
        )

        result = item.to_dict()

        assert result["song_id"] == "song_1"
        assert result["song_filename"] == "test.mp3"
        assert result["start_section"] == 0
        assert result["end_section"] == 2
        assert "transition_to_next" in result

    def test_from_dict_without_transition(self):
        """Test creating from dictionary without transition."""
        data = {
            "song_id": "song_1",
            "song_filename": "test.mp3",
            "start_section": 0,
        }

        item = PlaylistItem.from_dict(data)

        assert item.song_id == "song_1"
        assert item.transition_to_next is None

    def test_from_dict_with_transition(self):
        """Test creating from dictionary with transition."""
        data = {
            "song_id": "song_1",
            "song_filename": "test.mp3",
            "transition_to_next": {
                "type": "gap",
                "gap_beats": 1.0,
                "overlap": 4.0,
                "fade_window": 8.0,
                "fade_bottom": 0.33,
                "stems_to_fade": ["bass", "drums", "other"],
            },
        }

        item = PlaylistItem.from_dict(data)

        assert item.transition_to_next is not None
        assert item.transition_to_next.transition_type == "gap"


class TestPlaylistMetadata:
    """Tests for PlaylistMetadata dataclass."""

    def test_creation(self):
        """Test creating PlaylistMetadata."""
        now = datetime.now()
        metadata = PlaylistMetadata(
            name="Sunday Service",
            created_at=now,
            updated_at=now,
            total_duration=720.0,
            total_songs=6,
        )

        assert metadata.name == "Sunday Service"
        assert metadata.total_duration == 720.0
        assert metadata.total_songs == 6

    def test_formatted_duration_minutes_only(self):
        """Test formatted_duration for < 1 hour."""
        metadata = PlaylistMetadata(
            name="Test",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            total_duration=720.0,  # 12 minutes
            total_songs=1,
        )

        assert metadata.formatted_duration == "12:00"

    def test_formatted_duration_with_hours(self):
        """Test formatted_duration for > 1 hour."""
        metadata = PlaylistMetadata(
            name="Test",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            total_duration=3600.0,  # 1 hour
            total_songs=1,
        )

        assert metadata.formatted_duration == "1:00:00"

    def test_formatted_duration_long(self):
        """Test formatted_duration for long duration."""
        metadata = PlaylistMetadata(
            name="Test",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            total_duration=5400.0,  # 1.5 hours
            total_songs=1,
        )

        assert metadata.formatted_duration == "1:30:00"

    def test_song_count_display_singular(self):
        """Test song_count_display for single song."""
        metadata = PlaylistMetadata(
            name="Test",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            total_duration=180.0,
            total_songs=1,
        )

        assert metadata.song_count_display == "1 song"

    def test_song_count_display_plural(self):
        """Test song_count_display for multiple songs."""
        metadata = PlaylistMetadata(
            name="Test",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            total_duration=180.0,
            total_songs=3,
        )

        assert metadata.song_count_display == "3 songs"

    def test_to_dict(self):
        """Test converting to dictionary."""
        now = datetime.now()
        metadata = PlaylistMetadata(
            name="Test Playlist",
            created_at=now,
            updated_at=now,
            total_duration=360.0,
            total_songs=2,
        )

        result = metadata.to_dict()

        assert result["name"] == "Test Playlist"
        assert result["total_duration"] == 360.0
        assert result["total_songs"] == 2
        assert "created_at" in result
        assert "updated_at" in result

    def test_from_dict(self):
        """Test creating from dictionary."""
        now = datetime.now().isoformat()
        data = {
            "name": "Test",
            "created_at": now,
            "updated_at": now,
            "total_duration": 300.0,
            "total_songs": 1,
        }

        metadata = PlaylistMetadata.from_dict(data)

        assert metadata.name == "Test"
        assert metadata.total_duration == 300.0
        assert metadata.total_songs == 1


class TestPlaylist:
    """Tests for Playlist dataclass."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        """Fixture providing temp directory for playlist file."""
        return tmp_path

    def test_creation_with_defaults(self):
        """Test creating Playlist with defaults."""
        playlist = Playlist(id="test-playlist")

        assert playlist.id == "test-playlist"
        # Metadata should be auto-created
        assert playlist.metadata is not None
        assert playlist.metadata.name == "Untitled Playlist"
        assert playlist.metadata.total_songs == 0

    def test_name_property_getter(self):
        """Test name property getter."""
        playlist = Playlist(id="test")
        assert playlist.name == "Untitled Playlist"

    def test_name_property_setter(self):
        """Test name property setter updates metadata."""
        playlist = Playlist(id="test")
        playlist.name = "My Custom Playlist"

        assert playlist.metadata.name == "My Custom Playlist"
        # Should update timestamp
        assert playlist.metadata.updated_at > datetime.now().replace(microsecond=0)

    def test_song_count_property(self):
        """Test song_count property."""
        playlist = Playlist(id="test")
        playlist.items = [
            PlaylistItem(song_id=f"song_{i}", song_filename=f"song{i}.mp3")
            for i in range(3)
        ]

        assert playlist.song_count == 3

    def test_duration_property(self):
        """Test duration property."""
        playlist = Playlist(id="test")
        playlist.metadata = PlaylistMetadata(
            name="Test",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            total_duration=450.0,
            total_songs=3,
        )

        assert playlist.duration == 450.0

    def test_add_song_appends_by_default(self):
        """Test that add_song appends by default."""
        playlist = Playlist(id="test")
        playlist.add_song("song_1", "song1.mp3")

        assert len(playlist.items) == 1
        assert playlist.items[0].song_id == "song_1"

    def test_add_song_at_index(self):
        """Test that add_song can insert at index."""
        playlist = Playlist(id="test")
        playlist.add_song("song_1", "song1.mp3")
        playlist.add_song("song_2", "song2.mp3")

        # Insert at beginning
        playlist.add_song("song_3", "song3.mp3", index=0)

        assert len(playlist.items) == 3
        assert playlist.items[0].song_id == "song_3"
        assert playlist.items[1].song_id == "song_1"

    def test_remove_song_success(self):
        """Test removing a song."""
        playlist = Playlist(id="test")
        playlist.add_song("song_1", "song1.mp3")
        playlist.add_song("song_2", "song2.mp3")

        removed = playlist.remove_song(0)

        assert removed is not None
        assert removed.song_id == "song_1"
        assert len(playlist.items) == 1
        assert playlist.items[0].song_id == "song_2"

    def test_remove_song_invalid_index(self):
        """Test removing with invalid index."""
        playlist = Playlist(id="test")
        playlist.add_song("song_1", "song1.mp3")

        removed = playlist.remove_song(10)

        assert removed is None

    def test_move_song_success(self):
        """Test moving a song."""
        playlist = Playlist(id="test")
        playlist.add_song("song_1", "song1.mp3")
        playlist.add_song("song_2", "song2.mp3")
        playlist.add_song("song_3", "song3.mp3")

        result = playlist.move_song(0, 2)

        assert result is True
        # Order should be: 2, 1, 3
        assert playlist.items[0].song_id == "song_2"
        assert playlist.items[1].song_id == "song_1"
        assert playlist.items[2].song_id == "song_3"

    def test_move_song_same_index(self):
        """Test moving song to same position."""
        playlist = Playlist(id="test")
        playlist.add_song("song_1", "song1.mp3")

        result = playlist.move_song(0, 0)

        assert result is True  # Should succeed

    def test_update_transition(self):
        """Test updating transition for a song."""
        playlist = Playlist(id="test")
        playlist.add_song("song_1", "song1.mp3")
        playlist.add_song("song_2", "song2.mp3")

        transition = TransitionParams(transition_type="crossfade")
        result = playlist.update_transition(0, transition)

        assert result is True
        assert playlist.items[0].transition_to_next is not None
        assert playlist.items[0].transition_to_next.transition_type == "crossfade"

    def test_update_transition_invalid_index(self):
        """Test updating transition with invalid index."""
        playlist = Playlist(id="test")
        transition = TransitionParams()
        result = playlist.update_transition(10, transition)

        assert result is False

    def test_clear(self):
        """Test clearing playlist."""
        playlist = Playlist(id="test")
        playlist.add_song("song_1", "song1.mp3")
        playlist.add_song("song_2", "song2.mp3")

        playlist.clear()

        assert len(playlist.items) == 0
        assert playlist.metadata.total_songs == 0

    def test_get_transition(self):
        """Test getting transition for a song."""
        playlist = Playlist(id="test")
        playlist.add_song("song_1", "song1.mp3", transition=TransitionParams(transition_type="gap"))
        playlist.add_song("song_2", "song2.mp3")

        transition = playlist.get_transition(0)

        assert transition is not None
        assert transition.transition_type == "gap"

    def test_get_transition_no_transition(self):
        """Test getting transition when none exists."""
        playlist = Playlist(id="test")
        playlist.add_song("song_1", "song1.mp3")

        transition = playlist.get_transition(0)

        assert transition is None

    def test_to_dict(self):
        """Test converting to dictionary."""
        playlist = Playlist(id="test")
        playlist.add_song("song_1", "song1.mp3")

        result = playlist.to_dict()

        assert result["id"] == "test"
        assert "metadata" in result
        assert "items" in result

    def test_from_dict(self):
        """Test creating from dictionary."""
        data = {
            "id": "test-playlist",
            "metadata": {
                "name": "Test Playlist",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "total_duration": 360.0,
                "total_songs": 2,
            },
            "items": [
                {
                    "song_id": "song_1",
                    "song_filename": "song1.mp3",
                    "start_section": 0,
                }
            ],
        }

        playlist = Playlist.from_dict(data)

        assert playlist.id == "test-playlist"
        assert playlist.metadata.name == "Test Playlist"
        assert len(playlist.items) == 1

    @pytest.fixture
    def playlist_file(self, tmp_dir):
        """Fixture providing a temporary playlist file."""
        return tmp_dir / "test_playlist.json"

    def test_save_creates_file(self, playlist_file):
        """Test that save creates file."""
        playlist = Playlist(id="test")
        playlist.add_song("song_1", "song1.mp3")

        playlist.save(playlist_file)

        assert playlist_file.exists()

    def test_load_creates_playlist(self, playlist_file):
        """Test that load creates playlist from file."""
        # Create file first
        playlist = Playlist(id="test")
        playlist.add_song("song_1", "song1.mp3")
        playlist.save(playlist_file)

        # Load it back
        loaded = Playlist.load(playlist_file)

        assert loaded.id == "test"
        assert len(loaded.items) == 1

    def test_load_raises_file_not_found(self, tmp_dir):
        """Test that load raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            Playlist.load(tmp_dir / "nonexistent.json")

    def test_save_load_round_trip(self, playlist_file):
        """Test that save/load round-trip preserves data."""
        original = Playlist(id="test-roundtrip")
        original.add_song("song_1", "song1.mp3")
        original.add_song("song_2", "song2.mp3")

        original.save(playlist_file)
        loaded = Playlist.load(playlist_file)

        assert len(loaded.items) == len(original.items)
        assert loaded.items[0].song_id == original.items[0].song_id
        assert loaded.items[1].song_id == original.items[1].song_id
