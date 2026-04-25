"""Songset export/import service for sow-app.

Provides JSON serialization for songsets, including export to files
and import with validation against the catalog.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from stream_of_worship.app.db.models import Songset, SongsetItem
from stream_of_worship.app.db.songset_client import MissingReferenceError, SongsetClient


@dataclass
class ImportResult:
    """Result of a songset import operation.

    Attributes:
        success: Whether import succeeded
        songset_id: ID of the imported songset (if successful)
        imported_items: Number of items imported
        orphaned_items: Number of items with missing references
        warnings: List of warning messages
        error: Error message if import failed
    """

    success: bool
    songset_id: Optional[str] = None
    imported_items: int = 0
    orphaned_items: int = 0
    warnings: list[str] = None
    error: Optional[str] = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


class SongsetIOService:
    """Service for exporting and importing songsets.

    Provides JSON serialization with catalog validation on import.
    """

    def __init__(
        self,
        songset_client: SongsetClient,
        get_recording: Optional[Callable[[str], Optional]] = None,
    ):
        """Initialize the songset IO service.

        Args:
            songset_client: SongsetClient for database operations
            get_recording: Optional callable to validate recording existence
        """
        self.songset_client = songset_client
        self.get_recording = get_recording

    def export_songset(self, songset_id: str, output_path: Path) -> Path:
        """Export a songset to JSON file.

        Args:
            songset_id: ID of the songset to export
            output_path: Path to write the JSON file

        Returns:
            Path to the exported file

        Raises:
            ValueError: If songset not found
        """
        songset = self.songset_client.get_songset(songset_id)
        if not songset:
            raise ValueError(f"Songset not found: {songset_id}")

        items = self.songset_client.get_items_raw(songset_id)

        data = {
            "songset": songset.to_dict(),
            "items": [item.to_dict() for item in items],
        }

        # Ensure parent directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return output_path

    def export_all(self, output_dir: Path) -> list[Path]:
        """Export all songsets to JSON files.

        Args:
            output_dir: Directory to write the JSON files

        Returns:
            List of paths to exported files
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        songsets = self.songset_client.list_songsets()
        exported = []

        for songset in songsets:
            safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in songset.name)
            filename = f"{safe_name}_{songset.id}.json"
            output_path = output_dir / filename

            self.export_songset(songset.id, output_path)
            exported.append(output_path)

        return exported

    def import_songset(
        self,
        input_path: Path,
        on_conflict: str = "rename",
    ) -> ImportResult:
        """Import a songset from JSON file.

        Args:
            input_path: Path to the JSON file
            on_conflict: How to handle conflicts ("rename", "replace", "skip")

        Returns:
            ImportResult with operation outcome

        Raises:
            ValueError: If on_conflict is invalid
        """
        if on_conflict not in ("rename", "replace", "skip"):
            raise ValueError(f"Invalid on_conflict: {on_conflict}")

        # Load JSON file
        try:
            with open(input_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            return ImportResult(success=False, error=f"Invalid JSON: {e}")
        except FileNotFoundError:
            return ImportResult(success=False, error=f"File not found: {input_path}")

        # Validate structure
        if "songset" not in data or "items" not in data:
            return ImportResult(success=False, error="Invalid songset file format")

        songset_data = data["songset"]
        items_data = data["items"]

        # Check for existing songset with same ID or name
        existing = self.songset_client.get_songset(songset_data["id"])
        if existing:
            if on_conflict == "skip":
                return ImportResult(
                    success=False,
                    error=f"Songset already exists: {existing.id}",
                )
            elif on_conflict == "rename":
                # Generate new ID
                from datetime import datetime

                timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
                songset_data["id"] = f"songset_{timestamp}"
            elif on_conflict == "replace":
                # Delete existing
                self.songset_client.delete_songset(existing.id)

        # Create songset
        songset = Songset(
            id=songset_data["id"],
            name=songset_data["name"],
            description=songset_data.get("description"),
            created_at=songset_data.get("created_at"),
            updated_at=songset_data.get("updated_at"),
        )

        # Insert songset directly (bypass create_songset to preserve ID)
        import sqlite3

        conn = self.songset_client.connection
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO songsets (id, name, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (songset.id, songset.name, songset.description, songset.created_at, songset.updated_at),
        )

        # Import items
        imported_count = 0
        orphaned_count = 0
        warnings = []

        for item_data in items_data:
            recording_hash = item_data.get("recording_hash_prefix")

            # Validate recording exists
            if recording_hash and self.get_recording:
                recording = self.get_recording(recording_hash)
                if not recording:
                    warnings.append(f"Recording not found: {recording_hash}, importing as orphan")
                    orphaned_count += 1

            item = SongsetItem(
                id=item_data["id"],
                songset_id=songset.id,
                song_id=item_data["song_id"],
                recording_hash_prefix=recording_hash,
                position=item_data["position"],
                gap_beats=item_data.get("gap_beats", 2.0),
                crossfade_enabled=item_data.get("crossfade_enabled", False),
                crossfade_duration_seconds=item_data.get("crossfade_duration_seconds"),
                key_shift_semitones=item_data.get("key_shift_semitones", 0),
                tempo_ratio=item_data.get("tempo_ratio", 1.0),
                created_at=item_data.get("created_at"),
            )

            cursor.execute(
                """
                INSERT INTO songset_items
                (id, songset_id, song_id, recording_hash_prefix, position, gap_beats,
                 crossfade_enabled, crossfade_duration_seconds, key_shift_semitones,
                 tempo_ratio, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.songset_id,
                    item.song_id,
                    item.recording_hash_prefix,
                    item.position,
                    item.gap_beats,
                    1 if item.crossfade_enabled else 0,
                    item.crossfade_duration_seconds,
                    item.key_shift_semitones,
                    item.tempo_ratio,
                    item.created_at,
                ),
            )
            imported_count += 1

        conn.commit()

        return ImportResult(
            success=True,
            songset_id=songset.id,
            imported_items=imported_count,
            orphaned_items=orphaned_count,
            warnings=warnings,
        )
