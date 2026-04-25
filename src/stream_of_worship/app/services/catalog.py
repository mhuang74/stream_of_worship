"""Catalog browsing service for sow-app.

Provides high-level catalog operations combining songs and recordings data
for display in the TUI. Acts as a facade over the read-only database client.
"""

from dataclasses import dataclass
from typing import Optional

from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.db.schema import (
    RECORDING_COLUMNS_FOR_JOIN,
    SONG_COLUMNS_FOR_JOIN,
    SONG_COLUMN_COUNT,
)
from stream_of_worship.app.db.models import SongsetItem
from stream_of_worship.app.db.read_client import ReadOnlyClient
from stream_of_worship.app.db.songset_client import SongsetClient


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


@dataclass
class SongsetItemWithDetails:
    """Songset item with resolved song/recording details.

    Attributes:
        item: The songset item
        song: Resolved song (may be None if soft-deleted)
        recording: Resolved recording (may be None)
        is_orphan: True if song or recording is missing/soft-deleted
        display_title: Title to display (from song or "Unknown")
    """

    item: SongsetItem
    song: Optional[Song] = None
    recording: Optional[Recording] = None

    @property
    def is_orphan(self) -> bool:
        """Check if this item is orphaned (missing or soft-deleted reference)."""
        if self.song is None or self.recording is None:
            return True
        if self.song.deleted_at is not None:
            return True
        if self.recording.deleted_at is not None:
            return True
        return False

    @property
    def display_title(self) -> str:
        """Get the title to display."""
        if self.song:
            if self.song.deleted_at is not None:
                return f"Removed: {self.song.title}"
            return self.song.title
        return "Unknown"

    @property
    def display_key(self) -> str:
        """Get the key to display."""
        if self.recording and self.recording.musical_key:
            return self.recording.musical_key
        if self.song and self.song.musical_key:
            return self.song.musical_key
        return "?"


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
        # When filtering by LRC status, query via recordings first
        if only_with_lrc:
            return self._list_lrc_songs(album=album, key=key, limit=limit, offset=offset)

        # When filtering by analysis status, query via recordings first
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
        """List songs with analyzed recordings."""
        cursor = self.db_client.connection.cursor()

        query = f"""
            SELECT {SONG_COLUMNS_FOR_JOIN},
                   {RECORDING_COLUMNS_FOR_JOIN}
            FROM songs s
            JOIN recordings r ON s.id = r.song_id
            WHERE r.analysis_status = 'completed' AND r.deleted_at IS NULL
            AND s.deleted_at IS NULL
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
            row_tuple = tuple(row)
            song = Song.from_row(row_tuple[0:SONG_COLUMN_COUNT])
            recording = Recording.from_row(row_tuple[SONG_COLUMN_COUNT:])
            result.append(SongWithRecording(song=song, recording=recording))

        return result

    def _list_lrc_songs(
        self,
        album: Optional[str] = None,
        key: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[SongWithRecording]:
        """List songs with LRC lyrics."""
        cursor = self.db_client.connection.cursor()

        query = f"""
            SELECT {SONG_COLUMNS_FOR_JOIN},
                   {RECORDING_COLUMNS_FOR_JOIN}
            FROM songs s
            JOIN recordings r ON s.id = r.song_id
            WHERE r.lrc_status = 'completed' AND r.visibility_status = 'published'
            AND r.deleted_at IS NULL AND s.deleted_at IS NULL
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
            row_tuple = tuple(row)
            song = Song.from_row(row_tuple[0:SONG_COLUMN_COUNT])
            recording = Recording.from_row(row_tuple[SONG_COLUMN_COUNT:])
            result.append(SongWithRecording(song=song, recording=recording))

        return result

    def search_songs_with_recordings(
        self, query: str, field: str = "all", limit: int = 20, only_with_lrc: bool = True
    ) -> list[SongWithRecording]:
        """Search songs with their recordings."""
        if not only_with_lrc:
            songs = self.db_client.search_songs(query, field=field, limit=limit)
            result = []
            for song in songs:
                recording = self.db_client.get_recording_by_song_id(song.id)
                result.append(SongWithRecording(song=song, recording=recording))
            return result

        return self._search_lrc_songs(query, field=field, limit=limit)

    def _search_lrc_songs(
        self, query: str, field: str = "all", limit: int = 20
    ) -> list[SongWithRecording]:
        """Search songs with LRC lyrics."""
        cursor = self.db_client.connection.cursor()

        search_pattern = f"%{query}%"

        base_sql = f"""
            SELECT {SONG_COLUMNS_FOR_JOIN},
                   {RECORDING_COLUMNS_FOR_JOIN}
            FROM songs s
            JOIN recordings r ON s.id = r.song_id
            WHERE r.lrc_status = 'completed' AND r.visibility_status = 'published'
            AND r.deleted_at IS NULL AND s.deleted_at IS NULL
            AND (
        """

        if field == "title":
            where_clause = "s.title LIKE ? OR s.title_pinyin LIKE ?"
            params = [search_pattern, search_pattern]
        elif field == "lyrics":
            where_clause = "s.lyrics_raw LIKE ?"
            params = [search_pattern]
        elif field == "composer":
            where_clause = "s.composer LIKE ? OR s.lyricist LIKE ?"
            params = [search_pattern, search_pattern]
        else:  # all
            where_clause = """
                s.title LIKE ? OR s.title_pinyin LIKE ? OR
                s.lyrics_raw LIKE ? OR s.composer LIKE ? OR s.lyricist LIKE ?
            """
            params = [search_pattern] * 5

        sql = base_sql + where_clause + ") ORDER BY s.title LIMIT ?"
        params.append(limit)

        cursor.execute(sql, params)

        result = []
        for row in cursor.fetchall():
            row_tuple = tuple(row)
            song = Song.from_row(row_tuple[0:SONG_COLUMN_COUNT])
            recording = Recording.from_row(row_tuple[SONG_COLUMN_COUNT:])
            result.append(SongWithRecording(song=song, recording=recording))

        return result

    def list_available_albums(self) -> list[str]:
        """List all albums that have at least one recording."""
        all_albums = self.db_client.list_albums()

        result = []
        for album in all_albums:
            songs = self.db_client.list_songs(album=album, limit=1)
            if songs:
                recording = self.db_client.get_recording_by_song_id(songs[0].id)
                if recording:
                    result.append(album)

        return result

    def list_available_keys(self) -> list[str]:
        """List all keys that have at least one recording."""
        cursor = self.db_client.connection.cursor()
        cursor.execute(
            """
            SELECT DISTINCT s.musical_key
            FROM songs s
            JOIN recordings r ON s.id = r.song_id
            WHERE s.musical_key IS NOT NULL
            AND r.deleted_at IS NULL AND s.deleted_at IS NULL
            ORDER BY s.musical_key
            """
        )
        return [row[0] for row in cursor.fetchall() if row[0]]

    def get_stats(self) -> dict:
        """Get catalog statistics."""
        total_songs = self.db_client.get_song_count()
        total_recordings = self.db_client.get_recording_count()
        analyzed_recordings = self.db_client.get_analyzed_recording_count()

        return {
            "total_songs": total_songs,
            "total_recordings": total_recordings,
            "analyzed_recordings": analyzed_recordings,
            "analysis_coverage": (
                f"{analyzed_recordings / total_recordings * 100:.1f}%"
                if total_recordings > 0
                else "N/A"
            ),
        }

    def get_recording_for_song(self, song_id: str) -> Optional[Recording]:
        """Get the best recording for a song."""
        return self.db_client.get_recording_by_song_id(song_id)

    def get_catalog_health(self) -> dict:
        """Get catalog health status with actionable guidance."""
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

    def get_songset_with_items(
        self, songset_id: str, songset_client: SongsetClient
    ) -> tuple[list[SongsetItemWithDetails], int]:
        """Get a songset with resolved items (two-step cross-DB lookup).

        This replaces the cross-DB JOIN with a Python-side lookup:
        1. Get items from songset_client
        2. For each item, fetch recording and song from catalog
        3. Mark orphans for deleted/missing references

        Args:
            songset_id: The songset ID
            songset_client: SongsetClient for fetching items

        Returns:
            Tuple of (list of SongsetItemWithDetails, orphan count)
        """
        items = songset_client.get_items_raw(songset_id)

        result = []
        orphan_count = 0

        for item in items:
            # Fetch recording (include deleted to check for soft-deleted)
            recording = None
            if item.recording_hash_prefix:
                recording = self.db_client.get_recording_by_hash(
                    item.recording_hash_prefix, include_deleted=True
                )

            # Fetch song (prefer recording's song_id, fall back to item's song_id)
            song = None
            if recording and recording.song_id:
                song = self.db_client.get_song_including_deleted(recording.song_id)
            if not song and item.song_id:
                song = self.db_client.get_song_including_deleted(item.song_id)

            # Check if orphan
            is_orphan = song is None or recording is None
            if is_orphan:
                orphan_count += 1

            result.append(
                SongsetItemWithDetails(
                    item=item,
                    song=song,
                    recording=recording,
                )
            )

        return result, orphan_count
