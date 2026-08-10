"""Tests for song_components schema, model, and DB client methods.

These tests verify the Phase 0 schema definitions, the SongComponent dataclass,
and the DatabaseClient methods for upserting/querying song components.

Integration tests requiring testcontainers (PostgreSQL) are in the
integration-excluded set (run with --extra test).
"""

from stream_of_worship.admin.db.models import SongComponent
from stream_of_worship.admin.db.schema import (
    ALL_SCHEMA_STATEMENTS,
    CREATE_SONG_COMPONENTS_INDEXES,
    CREATE_SONG_COMPONENTS_TABLE,
    CREATE_SONG_COMPONENTS_UPDATE_TRIGGER,
    SONG_COMPONENT_COLUMN_COUNT,
    SONG_COMPONENT_COLUMNS_SELECT,
)


class TestSongComponentsSchema:
    """Tests for song_components schema definitions."""

    def test_table_in_all_schema_statements(self):
        """CREATE_SONG_COMPONENTS_TABLE is in the list, positioned after recordings."""
        assert CREATE_SONG_COMPONENTS_TABLE in ALL_SCHEMA_STATEMENTS
        # Verify it comes after CREATE_RECORDINGS_UPDATE_TRIGGER.
        recordings_trigger_idx = ALL_SCHEMA_STATEMENTS.index(
            next(s for s in ALL_SCHEMA_STATEMENTS if "trg_recordings_updated_at" in s)
        )
        components_table_idx = ALL_SCHEMA_STATEMENTS.index(CREATE_SONG_COMPONENTS_TABLE)
        assert components_table_idx > recordings_trigger_idx

    def test_ddl_uses_if_not_exists(self):
        """DDL uses CREATE TABLE IF NOT EXISTS (idempotent)."""
        assert "CREATE TABLE IF NOT EXISTS" in CREATE_SONG_COMPONENTS_TABLE

    def test_indexes_present(self):
        """All 4 indexes in CREATE_SONG_COMPONENTS_INDEXES."""
        assert len(CREATE_SONG_COMPONENTS_INDEXES) == 4
        # Check for the key indexes.
        all_index_sql = " ".join(CREATE_SONG_COMPONENTS_INDEXES)
        assert "idx_song_components_song_id" in all_index_sql
        assert "idx_song_components_content_hash" in all_index_sql
        assert "idx_song_components_type_role" in all_index_sql
        assert "idx_song_components_unique" in all_index_sql

    def test_unique_index_includes_role(self):
        """v3 — unique index is (song_id, component_type, occurrence_index, role)."""
        unique_index = next(
            s for s in CREATE_SONG_COMPONENTS_INDEXES if "idx_song_components_unique" in s
        )
        assert "song_id" in unique_index
        assert "component_type" in unique_index
        assert "occurrence_index" in unique_index
        assert "role" in unique_index

    def test_role_check_includes_entry_exit_value(self):
        """role CHECK constraint allows 'entry_exit' value."""
        assert "entry_exit" in CREATE_SONG_COMPONENTS_TABLE

    def test_trigger_present(self):
        """Update trigger created."""
        assert "trg_song_components_updated_at" in CREATE_SONG_COMPONENTS_UPDATE_TRIGGER

    def test_column_count(self):
        """SONG_COMPONENT_COLUMN_COUNT is 27 (v5: 16 original + 11 new)."""
        assert SONG_COMPONENT_COLUMN_COUNT == 27

    def test_columns_select_includes_all_fields(self):
        """SONG_COMPONENT_COLUMNS_SELECT includes all expected fields."""
        for field in (
            "id",
            "song_id",
            "content_hash",
            "component_type",
            "occurrence_index",
            "role",
            "start_time",
            "end_time",
            "bpm",
            "key",
            "groove_density",
            "backbeat_strength",
            "energy_level",
            "confidence",
            # v5: per-field confidence
            "bpm_confidence",
            "key_confidence",
            "groove_confidence",
            "backbeat_confidence",
            "energy_confidence",
            # v5: LLM theme/posture
            "theme",
            "vocal_posture",
            "theme_confidence",
            "vocal_posture_confidence",
            # v5: reasoning
            "theme_reasoning",
            "posture_reasoning",
            "created_at",
            "updated_at",
        ):
            assert field in SONG_COMPONENT_COLUMNS_SELECT


class TestSongComponentModel:
    """Tests for SongComponent dataclass."""

    def test_default_values(self):
        """Default SongComponent has sensible defaults."""
        c = SongComponent()
        assert c.id is None
        assert c.song_id == ""
        assert c.content_hash == ""
        assert c.component_type == ""
        assert c.occurrence_index == 1
        assert c.role == "none"
        assert c.start_time is None
        assert c.end_time is None
        assert c.bpm is None
        assert c.key is None
        assert c.groove_density is None
        assert c.backbeat_strength is None
        assert c.energy_level is None
        assert c.confidence is None
        assert c.created_at is None
        assert c.updated_at is None

    def test_from_row(self):
        """from_row parses a 27-element tuple correctly (v5 schema)."""
        row = (
            1,  # id
            "song_0001",  # song_id
            "abc123",  # content_hash
            "chorus",  # component_type
            1,  # occurrence_index
            "entry",  # role
            10.0,  # start_time
            20.0,  # end_time
            80.0,  # bpm
            "G",  # key
            0.45,  # groove_density
            1.12,  # backbeat_strength
            -18.3,  # energy_level
            0.9,  # confidence
            # v5: per-field confidence
            0.85,  # bpm_confidence
            0.80,  # key_confidence
            0.75,  # groove_confidence
            0.90,  # backbeat_confidence
            0.70,  # energy_confidence
            # v5: LLM theme/posture
            "讚美",  # theme
            "To God",  # vocal_posture
            0.92,  # theme_confidence
            0.95,  # vocal_posture_confidence
            # v5: reasoning
            "Religious pronoun 祢 + praise language",  # theme_reasoning
            "Direct address to God using 祢",  # posture_reasoning
            "2024-01-15T10:30:00",  # created_at
            "2024-01-15T10:30:00",  # updated_at
        )
        c = SongComponent.from_row(row)
        assert c.id == 1
        assert c.song_id == "song_0001"
        assert c.content_hash == "abc123"
        assert c.component_type == "chorus"
        assert c.occurrence_index == 1
        assert c.role == "entry"
        assert c.start_time == 10.0
        assert c.end_time == 20.0
        assert c.bpm == 80.0
        assert c.key == "G"
        assert c.groove_density == 0.45
        assert c.backbeat_strength == 1.12
        assert c.energy_level == -18.3
        assert c.confidence == 0.9
        # v5 fields
        assert c.bpm_confidence == 0.85
        assert c.key_confidence == 0.80
        assert c.groove_confidence == 0.75
        assert c.backbeat_confidence == 0.90
        assert c.energy_confidence == 0.70
        assert c.theme == "讚美"
        assert c.vocal_posture == "To God"
        assert c.theme_confidence == 0.92
        assert c.vocal_posture_confidence == 0.95
        assert c.theme_reasoning == "Religious pronoun 祢 + praise language"
        assert c.posture_reasoning == "Direct address to God using 祢"
        assert c.created_at == "2024-01-15T10:30:00"
        assert c.updated_at == "2024-01-15T10:30:00"

    def test_to_dict(self):
        """to_dict returns all fields."""
        c = SongComponent(
            id=1,
            song_id="song_0001",
            content_hash="abc123",
            component_type="chorus",
            occurrence_index=1,
            role="entry",
            start_time=10.0,
            end_time=20.0,
            bpm=80.0,
            key="G",
            groove_density=0.45,
            backbeat_strength=1.12,
            energy_level=-18.3,
            confidence=0.9,
            created_at="2024-01-15T10:30:00",
            updated_at="2024-01-15T10:30:00",
        )
        d = c.to_dict()
        assert d["id"] == 1
        assert d["song_id"] == "song_0001"
        assert d["component_type"] == "chorus"
        assert d["role"] == "entry"
        assert d["bpm"] == 80.0
        assert d["confidence"] == 0.9

    def test_from_row_roundtrip(self):
        """from_row → to_dict → SongComponent(**dict) roundtrips (v5 schema)."""
        row = (
            42,
            "song_0002",
            "def456",
            "verse",
            2,
            "loop_target",
            30.0,
            45.0,
            75.0,
            "D",
            0.38,
            0.95,
            -19.1,
            0.7,
            # v5: per-field confidence
            0.82,
            0.78,
            0.65,
            0.88,
            0.60,
            # v5: LLM theme/posture
            "感恩",
            "About God",
            0.80,
            0.85,
            # v5: reasoning
            "Describes God's character and works",
            "Third-person reference to God",
            None,
            None,
        )
        c = SongComponent.from_row(row)
        d = c.to_dict()
        c2 = SongComponent(**d)
        assert c2.id == c.id
        assert c2.song_id == c.song_id
        assert c2.component_type == c.component_type
        assert c2.role == c.role
        assert c2.start_time == c.start_time
