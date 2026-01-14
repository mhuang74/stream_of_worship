"""Song catalog loading and management."""
import json
from pathlib import Path

from app.models.song import Song


class SongCatalogLoader:
    """Loads and manages the song catalog from JSON files."""

    def __init__(self, audio_folder: Path):
        """Initialize the catalog loader.

        Args:
            audio_folder: Path to the folder containing audio files
        """
        self.audio_folder = Path(audio_folder)
        self.songs: dict[str, Song] = {}
        self.warnings: list[str] = []

    def load_from_json(self, json_path: Path) -> dict[str, Song]:
        """Load songs from a JSON file (output from poc_analysis_allinone.py).

        Args:
            json_path: Path to the JSON file containing song metadata

        Returns:
            Dictionary mapping song IDs to Song objects
        """
        self.songs = {}
        self.warnings = []

        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
        except FileNotFoundError:
            self.warnings.append(f"JSON file not found: {json_path}")
            return self.songs
        except json.JSONDecodeError as e:
            self.warnings.append(f"Malformed JSON: {json_path} - {e}")
            return self.songs

        if not isinstance(data, list):
            self.warnings.append(f"Invalid JSON format: expected list, got {type(data)}")
            return self.songs

        for song_data in data:
            try:
                song = Song.from_dict(song_data)

                # Validate that audio file exists
                audio_path = self.audio_folder / song.filename
                if not audio_path.exists():
                    self.warnings.append(f"Audio file not found: {audio_path}")
                    continue

                # Update filepath to be absolute
                song.filepath = audio_path

                self.songs[song.id] = song

            except (KeyError, ValueError, TypeError) as e:
                filename = song_data.get("filename", "unknown")
                self.warnings.append(f"Skipping song {filename}: {e}")
                continue

        return self.songs

    def get_song(self, song_id: str) -> Song | None:
        """Get a song by ID."""
        return self.songs.get(song_id)

    def get_all_songs(self) -> list[Song]:
        """Get all songs sorted alphabetically by filename."""
        return sorted(self.songs.values(), key=lambda s: s.filename.lower())

    def get_songs_sorted_by_compatibility(self, reference_song_id: str) -> list[Song]:
        """Get all songs sorted by compatibility with a reference song.

        This is a placeholder - compatibility scores would be computed
        or loaded from a separate compatibility matrix file.

        Args:
            reference_song_id: The song to compare against

        Returns:
            List of songs sorted by compatibility (descending)
        """
        reference_song = self.get_song(reference_song_id)
        if not reference_song:
            return self.get_all_songs()

        # Exclude the reference song itself
        other_songs = [s for s in self.songs.values() if s.id != reference_song_id]

        # Compute simple compatibility based on tempo and key similarity
        for song in other_songs:
            song.compatibility_score = self._compute_compatibility(reference_song, song)

        # Sort by compatibility (descending), then alphabetically
        return sorted(
            other_songs,
            key=lambda s: (-s.compatibility_score, s.filename.lower())
        )

    def _compute_compatibility(self, song_a: Song, song_b: Song) -> float:
        """Compute a simple compatibility score between two songs.

        This is a placeholder implementation. In production, this would
        use more sophisticated analysis or load pre-computed scores.

        Returns:
            Compatibility score from 0-100
        """
        # Tempo similarity (within 10 BPM is 100%, linear decay beyond)
        tempo_diff = abs(song_a.tempo - song_b.tempo)
        tempo_score = max(0, 100 - (tempo_diff * 5))

        # Key similarity (same key = 100%, related keys = 80%, others = 50%)
        if song_a.key == song_b.key and song_a.mode == song_b.mode:
            key_score = 100
        elif song_a.key == song_b.key:  # Same root, different mode
            key_score = 80
        else:
            # Check for related keys (dominant, subdominant, relative)
            key_score = 60  # Default for unrelated keys

        # Overall score (weighted average)
        overall = (tempo_score * 0.6) + (key_score * 0.4)
        return round(overall, 1)

    def get_song_count(self) -> int:
        """Return the number of loaded songs."""
        return len(self.songs)
