"""Constants for the Component Metadata editor TUI (v4).

v4 changes from v3:
- ``DATA_TABLE_COLUMNS`` reordered so transition-critical + editable fields
  come first (transition-cluster), then audit-context (confidences + reasoning),
  then timestamps (meta-cluster). Each column carries a 4th tuple element
  tagging its visual cluster.
- New ``HERO_PRIMARY_FIELDS`` / ``HERO_REASONING_FIELDS`` drive the
  ``ComponentHeroPanel`` widget layout.
- New ``REASONING_TABLE_TRUNC`` / ``REASONING_CELL_WIDTH`` control the
  dimmed, truncated rendering of reasoning text inside the DataTable (the
  authoritative full-text rendering lives in the Hero panel).
"""

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
COMPONENT_SCHEMA_VERSION = 4

# =============================================================================
# v4: Column order for the DataTable (left -> right). Three visual clusters:
#   (1) transition-cluster -- role, identification, time, audio-derived
#       metrics, the 4 editable fields, backbeat. These are the values the
#       songset constructor consumes; they must be visible without scrolling.
#   (2) audit-cluster -- confidence values + LLM reasoning (truncated in the
#       table; full text in the Hero panel).
#   (3) meta-cluster -- created_at / updated_at.
# Contrast with v3 which had the 4 editable fields at positions 21-24
# AFTER every confidence column. v4 prioritises them.
# =============================================================================
DATA_TABLE_COLUMNS: tuple[tuple[str, str, bool, str], ...] = (
    # (key, header_label, editable, cluster)
    # -- transition-cluster (cluster 1) --------------------------------------
    ("role", "Role", False, "transition-cluster"),
    ("component_type", "Type", False, "transition-cluster"),
    ("occurrence_index", "Occ", False, "transition-cluster"),
    ("start_time", "Start", False, "transition-cluster"),
    ("end_time", "End", False, "transition-cluster"),
    ("bpm", "BPM", False, "transition-cluster"),
    ("key", "Key", False, "transition-cluster"),
    # Editable fields, grouped together right after bpm/key for prominence.
    ("theme", "*Theme", True, "transition-cluster"),
    ("vocal_posture", "*Posture", True, "transition-cluster"),
    ("energy_level", "*Energy", True, "transition-cluster"),
    ("groove_density", "*Groove", True, "transition-cluster"),
    ("backbeat_strength", "Backbeat", False, "transition-cluster"),
    # -- audit-cluster (cluster 2) -------------------------------------------
    ("confidence", "Conf", False, "audit-cluster"),
    ("bpm_confidence", "BPMc", False, "audit-cluster"),
    ("key_confidence", "KEYc", False, "audit-cluster"),
    ("groove_confidence", "GRVc", False, "audit-cluster"),
    ("backbeat_confidence", "BBc", False, "audit-cluster"),
    ("energy_confidence", "ENGc", False, "audit-cluster"),
    ("theme_confidence", "THMc", False, "audit-cluster"),
    ("vocal_posture_confidence", "PSTc", False, "audit-cluster"),
    # Reasoning rendered DIMMED + TRUNCATED here; full text lives in Hero panel.
    ("theme_reasoning", "ThemeReason\u2042", False, "audit-cluster"),
    ("posture_reasoning", "PostureReason\u2042", False, "audit-cluster"),
    # -- meta-cluster (cluster 3) --------------------------------------------
    ("created_at", "Created", False, "meta-cluster"),
    ("updated_at", "Updated", False, "meta-cluster"),
)

# The \u2042 (asterism) suffix signals to the operator: "the full text is in
# the Hero panel."
REASONING_TABLE_TRUNC = 40  # chars; v3 had no truncation policy (used cell width).

# =============================================================================
# v4 NEW: Hero panel layout. The Hero panel renders five rows:
#   row 1 (Header line, bold, large):
#       "> ENTRY CHORUS  --  Occurrence 1  --  [00:23 -> 02:15]"
#   row 2 (Primary line, normal):
#       "BPM 96    Key G    Energy -12.0 dB    Groove 0.80    Backbeat 0.42"
#   row 3 (Editable / theme line, accent color):
#       "Theme: 敬拜    Vocal posture: To God"
#   row 4 (Theme reasoning, italic, dimmed):
#       "Theme reasoning: <full text>"
#   row 5 (Posture reasoning, italic, dimmed):
#       "Posture reasoning: <full text>"
# Field order in row 2:
# =============================================================================
HERO_PRIMARY_FIELDS: tuple[tuple[str, str, str], ...] = (
    # (field_name, display_label, format_spec_or_None)
    ("bpm", "BPM", "{:.0f}"),
    ("key", "Key", "{}"),
    ("energy_level", "Energy", "{:.1f} dB"),
    ("groove_density", "Groove", "{:.2f}"),
    ("backbeat_strength", "Backbeat", "{:.2f}"),
)

HERO_REASONING_FIELDS: tuple[tuple[str, str], ...] = (
    # (field_name, display_label)
    ("theme_reasoning", "Theme reasoning"),
    ("posture_reasoning", "Posture reasoning"),
)

# Float editor input attributes (unchanged from v3).
GROOVE_DENSITY_MIN = 0.0
GROOVE_DENSITY_MAX = 2.0  # no DB CHECK; admin guard only
ENERGY_LEVEL_MIN = -60.0  # dB; admin guard only
ENERGY_LEVEL_MAX = 0.0

# Cell-width hint for the truncated reason cells in the DataTable.
REASONING_CELL_WIDTH = REASONING_TABLE_TRUNC + 1  # +1 for the ellipsis char.
