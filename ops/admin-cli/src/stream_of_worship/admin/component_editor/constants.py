"""Constants for the Component Metadata editor TUI."""

# 4 user-editable columns (subset of song_components). Order matters:
# theme / vocal_posture are enums (cycle with [ / ]).
# groove_density / energy_level are floats (numeric input overlay).
EDITABLE_FIELDS: tuple[str, ...] = (
    "theme",
    "vocal_posture",
    "groove_density",
    "energy_level",
)

# The 12-theme vocabulary (must match db/schema.py CHECK constraint).
THEME_VALUES: tuple[str, ...] = (
    "讚美",
    "感恩",
    "敬拜",
    "奉獻",
    "認罪",
    "差遣",
    "信心",
    "祈禱",
    "復興",
    "聖靈",
    "十字架",
    "跟隨",
)

# The 3-posture vocabulary (must match db/schema.py CHECK constraint).
VOCAL_POSTURE_VALUES: tuple[str, ...] = (
    "To God",
    "About God",
    "To Congregation",
)

# Mirror of sow_analysis.storage.cache.COMPONENT_SCHEMA_VERSION
COMPONENT_SCHEMA_VERSION = 2

# Column order for the DataTable (left -> right). Read-only columns marked RO;
# editable marked RW (*). 27 columns from SONG_COMPONENT_COLUMNS_SELECT.
DATA_TABLE_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    # (key, header_label, editable)
    ("role", "Role", False),
    ("component_type", "Type", False),
    ("occurrence_index", "Occ", False),
    ("start_time", "Start", False),
    ("end_time", "End", False),
    ("bpm", "BPM", False),
    ("key", "Key", False),
    ("backbeat_strength", "Backbeat", False),
    ("confidence", "Conf", False),
    ("bpm_confidence", "BPMc", False),
    ("key_confidence", "KEYc", False),
    ("groove_confidence", "GRVc", False),
    ("backbeat_confidence", "BBc", False),
    ("energy_confidence", "ENGc", False),
    ("theme_confidence", "THMc", False),
    ("vocal_posture_confidence", "PSTc", False),
    ("theme_reasoning", "ThemeReason", False),
    ("posture_reasoning", "PostureReason", False),
    ("created_at", "Created", False),
    ("updated_at", "Updated", False),
    # Editable (4)
    ("theme", "*Theme", True),
    ("vocal_posture", "*Posture", True),
    ("groove_density", "*Groove", True),
    ("energy_level", "*Energy", True),
)

# Compact column set for the v2 top-panel read-only DataTable.
# Numerical columns only (no dates, no long text, no enum fields).
# (field_key, header_label) — drives the top panel's table setup + refresh.
COMPACT_TABLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("occurrence_index", "Occ"),
    ("bpm", "BPM"),
    ("key", "Key"),
    ("start_time", "Start"),
    ("end_time", "End"),
    ("confidence", "Conf"),
    ("backbeat_strength", "Backbeat"),
    ("groove_density", "Groove"),
    ("energy_level", "Energy"),
)

# Float editor input attributes
GROOVE_DENSITY_MIN = 0.0
GROOVE_DENSITY_MAX = 2.0  # no DB CHECK; admin guard only
ENERGY_LEVEL_MIN = -60.0  # dB; admin guard only
ENERGY_LEVEL_MAX = 0.0
