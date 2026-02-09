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
        only_with_lrc: bool = False,
    ) -> list[SongWithRecording]:
        """List songs with their recordings.

        Args:
            album: Filter by album name
            key: Filter by musical key
            limit: Maximum number of results
            offset: Number of results to skip
            only_with_recordings: Only return songs with recordings
            only_analyzed: Only return songs with analyzed recordings
            only_with_lrc: Only return songs with LRC lyrics

        Returns:
            List of SongWithRecording
        """
        # When filtering by LRC status, query via recordings first to ensure
        # we get the right songs even with a limit (LRC songs may not be
        # in the first N songs when ordered by title)
        if only_with_lrc:
            return self._list_lrc_songs(album=album, key=key, limit=limit, offset=offset)

        # When filtering by analysis status, query via recordings first to ensure
        # we get the right songs even with a limit (analyzed songs may not be
        # in the first N songs when ordered by title)
        if only_analyzed:
            return self._list_analyzed_songs(album=album, key=key, limit=limit, offset=offset)

        songs = self.db_client.list_songs(album=album, key=key, limit=limit, offset=offset)

        result = []
        for song in songs:
            recording = self.db_client.get_recording_by_song_id(song.id)

            if only_with_recordings and not recording:
                continue

            result.append(SongWithRecording(song=song, recording=recording))

        return result

    def _list_analyzed_songs(
        self,
        album: Optional[str] = None,
        key: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[SongWithRecording]:
        """List songs with analyzed recordings.

        Queries through recordings to find songs with completed analysis,
        ensuring proper pagination even when songs are spread across the catalog.

        Args:
            album: Filter by album name
            key: Filter by musical key
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of SongWithRecording with analyzed recordings
        """
        cursor = self.db_client.connection.cursor()

        # Build query joining songs and recordings
        query = """
            SELECT s.*, r.content_hash, r.hash_prefix, r.song_id, r.original_filename,
                   r.file_size_bytes, r.imported_at, r.r2_audio_url, r.r2_stems_url,
                   r.r2_lrc_url, r.duration_seconds, r.tempo_bpm, r.musical_key,
                   r.musical_mode, r.key_confidence, r.loudness_db, r.beats,
                   r.downbeats, r.sections, r.embeddings_shape, r.analysis_status,
                   r.analysis_job_id, r.lrc_status, r.lrc_job_id, r.created_at,
                   r.updated_at
            FROM songs s
            JOIN recordings r ON s.id = r.song_id
            WHERE r.analysis_status = 'completed'
        """
        params: list = []

        if album:
            query += " AND s.album_name = ?"
            params.append(album)

        if key:
            query += " AND s.musical_key = ?"
            params.append(key)

        query += " ORDER BY s.title"

        if limit:
            query += f" LIMIT {limit}"

        if offset:
            query += f" OFFSET {offset}"

        cursor.execute(query, params)

        result = []
        for row in cursor.fetchall():
            # Split row into song and recording parts
            # Song has 16 columns (0-15), Recording has 25 columns (16-40)
            # Total: 41 columns in the JOIN result
            row_tuple = tuple(row)
            song = Song.from_row(row_tuple[0:16])
            # Recording columns start at 16 (content_hash)
            recording = Recording.from_row(row_tuple[16:])
            result.append(SongWithRecording(song=song, recording=recording))

        return result

    def _list_lrc_songs(
        self,
        album: Optional[str] = None,
        key: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[SongWithRecording]:
        """List songs with LRC lyrics.

        Queries through recordings to find songs with completed LRC generation,
        ensuring proper pagination even when songs are spread across the catalog.

        Args:
            album: Filter by album name
            key: Filter by musical key
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of SongWithRecording with LRC lyrics
        """
        cursor = self.db_client.connection.cursor()

        # Build query joining songs and recordings
        query = """
            SELECT s.*, r.content_hash, r.hash_prefix, r.song_id, r.original_filename,
                   r.file_size_bytes, r.imported_at, r.r2_audio_url, r.r2_stems_url,
                   r.r2_lrc_url, r.duration_seconds, r.tempo_bpm, r.musical_key,
                   r.musical_mode, r.key_confidence, r.loudness_db, r.beats,
                   r.downbeats, r.sections, r.embeddings_shape, r.analysis_status,
                   r.analysis_job_id, r.lrc_status, r.lrc_job_id, r.created_at,
                   r.updated_at
            FROM songs s
            JOIN recordings r ON s.id = r.song_id
            WHERE r.lrc_status = 'completed'
        """
        params: list = []

        if album:
            query += " AND s.album_name = ?"
            params.append(album)

        if key:
            query += " AND s.musical_key = ?"
            params.append(key)

        query += " ORDER BY s.title"

        if limit:
            query += f" LIMIT {limit}"

        if offset:
            query += f" OFFSET {offset}"

        cursor.execute(query, params)

        result = []
        for row in cursor.fetchall():
            # Split row into song and recording parts
            # Song has 16 columns (0-15), Recording has 25 columns (16-40)
            # Total: 41 columns in the JOIN result
            row_tuple = tuple(row)
            song = Song.from_row(row_tuple[0:16])
            # Recording columns start at 16 (content_hash)
            recording = Recording.from_row(row_tuple[16:])
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

    def get_catalog_health(self) -> dict:
        """Get catalog health status with actionable guidance.

        Returns:
            Dictionary with:
            - status: 'empty'|'no_recordings'|'no_analysis'|'ready'
            - total_songs: int
            - total_recordings: int
            - analyzed_recordings: int
            - guidance: str (user-facing message with next steps)
        """
        stats = self.get_stats()
        total_songs = stats["total_songs"]
        total_recordings = stats["total_recordings"]
        analyzed = stats["analyzed_recordings"]

        if total_songs == 0:
            return {
                "status": "empty",
                "total_songs": 0,
                "total_recordings": 0,
                "analyzed_recordings": 0,
                "guidance": "No songs found. Run: sow-admin catalog scrape",
            }

        if total_recordings == 0:
            return {
                "status": "no_recordings",
                "total_songs": total_songs,
                "total_recordings": 0,
                "analyzed_recordings": 0,
                "guidance": f"Found {total_songs} songs but no audio files. Download audio: sow-admin audio download <song_id>",
            }

        if analyzed == 0:
            return {
                "status": "no_analysis",
                "total_songs": total_songs,
                "total_recordings": total_recordings,
                "analyzed_recordings": 0,
                "guidance": f"Found {total_songs} songs and {total_recordings} recording(s), but no analysis completed. Run: sow-admin audio analyze <song_id>",
            }

        return {
            "status": "ready",
            "total_songs": total_songs,
            "total_recordings": total_recordings,
            "analyzed_recordings": analyzed,
            "guidance": f"Catalog ready: {analyzed} analyzed recording(s) available",
        }
