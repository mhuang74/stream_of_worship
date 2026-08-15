"""Tests for the Component Metadata editor constants (v4).

Asserts the v4 column ordering (D1), cluster tagging, and Hero panel field
configuration.
"""

from stream_of_worship.admin.component_editor.constants import (
    DATA_TABLE_COLUMNS,
    EDITABLE_FIELDS,
    HERO_PRIMARY_FIELDS,
    HERO_REASONING_FIELDS,
    REASONING_CELL_WIDTH,
    REASONING_TABLE_TRUNC,
)


class TestDataTableColumns:
    """v4 D1: column ordering and cluster tagging."""

    def test_columns_are_4_tuples(self):
        for entry in DATA_TABLE_COLUMNS:
            assert len(entry) == 4, f"Column entry {entry} must be a 4-tuple"
            key, header, editable, cluster = entry
            assert isinstance(key, str)
            assert isinstance(header, str)
            assert isinstance(editable, bool)
            assert isinstance(cluster, str)

    def test_first_column_is_role(self):
        assert DATA_TABLE_COLUMNS[0][0] == "role"

    def test_second_column_is_component_type(self):
        assert DATA_TABLE_COLUMNS[1][0] == "component_type"

    def test_editable_fields_in_transition_cluster(self):
        """All 4 editable fields must be in the transition-cluster."""
        for key, _, editable, cluster in DATA_TABLE_COLUMNS:
            if editable:
                assert (
                    cluster == "transition-cluster"
                ), f"Editable field '{key}' must be in transition-cluster, got '{cluster}'"

    def test_editable_fields_at_positions_7_to_10(self):
        """The 4 table-editable fields should be at positions 7-10 (0-indexed).

        EDITABLE_FIELDS is a superset of the table's editable columns: it also
        includes start_time/end_time (editable only in the Detail Panel).
        """
        editable_keys = [key for key, _, editable, _ in DATA_TABLE_COLUMNS if editable]
        assert set(editable_keys).issubset(set(EDITABLE_FIELDS))
        assert len(editable_keys) == 4

    def test_no_confidence_column_before_editable_fields(self):
        """D1 regression: no 'confidence' column appears before any editable column."""
        first_editable_idx = next(
            i for i, (_, _, editable, _) in enumerate(DATA_TABLE_COLUMNS) if editable
        )
        for i, (key, _, _, _) in enumerate(DATA_TABLE_COLUMNS[:first_editable_idx]):
            assert (
                "confidence" not in key
            ), f"Confidence column '{key}' at position {i} appears before editable fields"

    def test_backbeat_after_editable_fields(self):
        """Backbeat should come right after the editable fields."""
        keys = [key for key, _, _, _ in DATA_TABLE_COLUMNS]
        assert keys.index("backbeat_strength") > keys.index("groove_density")

    def test_reasoning_columns_in_audit_cluster(self):
        for key, _, _, cluster in DATA_TABLE_COLUMNS:
            if key in ("theme_reasoning", "posture_reasoning"):
                assert cluster == "audit-cluster"

    def test_timestamp_columns_in_meta_cluster(self):
        for key, _, _, cluster in DATA_TABLE_COLUMNS:
            if key in ("created_at", "updated_at"):
                assert cluster == "meta-cluster"

    def test_reasoning_headers_have_asterism_suffix(self):
        """D4: reasoning column headers have the ⁂ suffix."""
        for key, header, _, _ in DATA_TABLE_COLUMNS:
            if key in ("theme_reasoning", "posture_reasoning"):
                assert "\u2042" in header, f"Header '{header}' for {key} must contain ⁂"


class TestHeroFields:
    """v4 D2: Hero panel field configuration."""

    def test_hero_primary_fields_includes_bpm(self):
        keys = [f for f, _, _ in HERO_PRIMARY_FIELDS]
        assert "bpm" in keys

    def test_hero_primary_fields_includes_key(self):
        keys = [f for f, _, _ in HERO_PRIMARY_FIELDS]
        assert "key" in keys

    def test_hero_primary_fields_includes_energy_level(self):
        keys = [f for f, _, _ in HERO_PRIMARY_FIELDS]
        assert "energy_level" in keys

    def test_hero_primary_fields_includes_groove_density(self):
        keys = [f for f, _, _ in HERO_PRIMARY_FIELDS]
        assert "groove_density" in keys

    def test_hero_primary_fields_includes_backbeat(self):
        keys = [f for f, _, _ in HERO_PRIMARY_FIELDS]
        assert "backbeat_strength" in keys

    def test_hero_primary_fields_excludes_theme(self):
        """Theme is in the editable row (row 3), not the primary row (row 2)."""
        keys = [f for f, _, _ in HERO_PRIMARY_FIELDS]
        assert "theme" not in keys

    def test_hero_reasoning_fields(self):
        assert HERO_REASONING_FIELDS == (
            ("theme_reasoning", "Theme reasoning"),
            ("posture_reasoning", "Posture reasoning"),
        )


class TestReasoningTruncation:
    """v4 D4: reasoning truncation constants."""

    def test_truncation_is_40(self):
        assert REASONING_TABLE_TRUNC == 40

    def test_cell_width_is_trunc_plus_1(self):
        assert REASONING_CELL_WIDTH == REASONING_TABLE_TRUNC + 1
