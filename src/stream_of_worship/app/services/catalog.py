"""Catalog browsing service for sow-app.

Provides high-level catalog operations combining songs and recordings data
for display in the TUI. Acts as a facade over the read-only database client.
"""

from dataclasses import dataclass
from typing import Optional

from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.app.db.read_client import ReadOnlyClient


@dataclass
class SongWithRecording:
    """Combined song and recording information for display.

    Attributes:
        song: The song metadata
        recording: Associated recording (may be None)
        has_analysis: Whether recording has analysis data
        has_lrc: Whether recording has LRC lyrics
    """

    song: Song
    recording: Optional[Recording] = None

    @property
    def has_analysis(self) -> bool:
        """Check if the recording has analysis data."""
        return self.recording is not None and self.recording.has_analysis

    @property
    def has_lrc(self) -> bool:
        """Check if the recording has LRC lyrics."""
        return self.recording is not None and self.recording.has_lrc

    @property
    def duration_seconds(self) -> Optional[float]:
        """Get duration from recording if available."""
        return self.recording.duration_seconds if self.recording else None

    @property
    def tempo_bpm(self) -> Optional[float]:
        """Get tempo from recording if available."""
        return self.recording.tempo_bpm if self.recording else None

    @property
    def display_key(self) -> str:
        """Get the key to display."""
        if self.recording and self.recording.musical_key:
            mode = self.recording.musical_mode or ""
            return f"{self.recording.musical_key} {mode}".strip()
        return self.song.musical_key or "?"

    @property
    def formatted_duration(self) -> str:
        """Get formatted duration."""
        if self.recording and self.recording.duration_seconds:
            return self.recording.formatted_duration
        return "--:--"


class CatalogService:
    """Service for browsing the song catalog.

    Provides high-level operations for listing, searching, and filtering
    songs with their associated recording information.

    Attributes:
        db_client: Read-only database client
    """

    def __init__(self, db_client: ReadOnlyClient):
        """Initialize the catalog service.

        Args:
            db_client: Read-only database client
        """
        self.db_client = db_client

    def get_song_with_recording(self, song_id: str) -> Optional[SongWithRecording]:
        """Get a song with its associated recording.

        Args:
            song_id: The song ID

        Returns:
            SongWithRecording or None if song not found
        """
        song = self.db_client.get_song(song_id)
        if not song:
            return None

        recording = self.db_client.get_recording_by_song_id(song_id)
        return SongWithRecording(song=song, recording=recording)

    def list_songs_with_recordings(
        self,
        album: Optional[str] = None,
        key: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        only_with_recordings: bool = False,
        only_analyzed: bool = False,
    ) -> list[SongWithRecording]:
        """List songs with their recordings.

        Args:
            album: Filter by album name
            key: Filter by musical key
            limit: Maximum number of results
            offset: Number of results to skip
            only_with_recordings: Only return songs with recordings
            only_analyzed: Only return songs with analyzed recordings

        Returns:
            List of SongWithRecording
        """
        songs = self.db_client.list_songs(album=album, key=key, limit=limit, offset=offset)

        result = []
        for song in songs:
            recording = self.db_client.get_recording_by_song_id(song.id)

            if only_with_recordings and not recording:
                continue

            if only_analyzed and (not recording or not recording.has_analysis):
                continue

            result.append(SongWithRecording(song=song, recording=recording))

        return result

    def search_songs_with_recordings(
        self, query: str, field: str = "all", limit: int = 20
    ) -> list[SongWithRecording]:
        """Search songs with their recordings.

        Args:
            query: Search query string
            field: Field to search (title, lyrics, composer, all)
            limit: Maximum number of results

        Returns:
            List of SongWithRecording
        """
        songs = self.db_client.search_songs(query, field=field, limit=limit)

        result = []
        for song in songs:
            recording = self.db_client.get_recording_by_song_id(song.id)
            result.append(SongWithRecording(song=song, recording=recording))

        return result

    def list_available_albums(self) -> list[str]:
        """List all albums that have at least one recording.

        Returns:
            List of album names
        """
        # Get all albums
        all_albums = self.db_client.list_albums()

        # Filter to those with recordings
        result = []
        for album in all_albums:
            songs = self.db_client.list_songs(album=album, limit=1)
            if songs:
                recording = self.db_client.get_recording_by_song_id(songs[0].id)
                if recording:
                    result.append(album)

        return result

    def list_available_keys(self) -> list[str]:
        """List all keys that have at least one recording.

        Returns:
            List of key names
        """
        # Get all keys from songs with recordings
        cursor = self.db_client.connection.cursor()
        cursor.execute(
            """
            SELECT DISTINCT s.musical_key
            FROM songs s
            JOIN recordings r ON s.id = r.song_id
            WHERE s.musical_key IS NOT NULL
            ORDER BY s.musical_key
            """
        )
        return [row[0] for row in cursor.fetchall() if row[0]]

    def get_stats(self) -> dict:
        """Get catalog statistics.

        Returns:
            Dictionary with catalog stats
        """
        total_songs = self.db_client.get_song_count()
        total_recordings = self.db_client.get_recording_count()
        analyzed_recordings = self.db_client.get_analyzed_recording_count()

        return {
            "total_songs": total_songs,
            "total_recordings": total_recordings,
            "analyzed_recordings": analyzed_recordings,
            "analysis_coverage": (
                f"{analyzed_recordings / total_recordings * 100:.1f}%"
                if total_recordings > 0 else "N/A"
            ),
        }

    def get_recording_for_song(self, song_id: str) -> Optional[Recording]:
        """Get the best recording for a song.

        Prefers recordings with analysis, then by most recent.

        Args:
            song_id: The song ID

        Returns:
            Best recording or None
        """
        return self.db_client.get_recording_by_song_id(song_id)
