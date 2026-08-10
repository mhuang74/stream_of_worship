---

# Implementation Plan: Chorus-Based Song Component Metadata (v4)

> **Revision Notes:** This supersedes `specs/chorus-component-metadata-impl-plan-v3.md` (v3).
> Key changes from v3:
>
> 1. **Energy/instrumentation-aware entry/exit selection.** v3 used positional
>    selection (first chorus = entry, last chorus = exit). v4 adds an optional
>    post-processing pass that scores each chorus occurrence by energy/instrumentation
>    cues and reassigns entry/exit roles based on musical dynamics rather than position.
>    This is critical for songs where the first chorus is sparse (quiet) and a later
>    chorus is full-band (energetic) — the energetic one is the true "exit."
> 2. **`madmom` for downbeat-aware beat tracking.** v3 used `librosa` for beat tracking.
>    v4 adds `madmom` (git URL in `ops/analysis-service/pyproject.toml`:
>    `madmom @ git+https://github.com/CPJKU/madmom.git`) for downbeat detection,
>    which produces more accurate downbeat timestamps. These are used for first-class
>    edit-point alignment (snapping to downbeats/phrase boundaries).
> 3. **Cached Demucs stems for DSP feature extraction.** v3 computed all features from
>    the full mix. v4 optionally uses cached Demucs stems (drums, vocals, bass, other)
>    for more accurate per-component feature extraction — e.g., groove_density from the
>    drums stem, backbeat_strength from the drums stem, energy_level from the full mix
>    or vocals stem.
> 4. **First-class edit-point alignment.** v3 snapped to nearest beat. v4 snaps to
>    nearest **downbeat** (from madmom) or to phrase boundaries (detected via onset
>    strength envelope zero-crossings). This produces cleaner transition points.
> 5. **Per-field confidence scoring.** v3 used a single confidence field (0.9 for allin1,
>    0.7 for lyrics). v4 computes per-field confidence (bpm_confidence, key_confidence,
>    groove_confidence, etc.) and a composite `confidence` column that is the weighted
>    mean of all per-field confidences.
> 6. **LLM stage for theme and vocal posture classification.** v4 adds a new stage
>    (after component extraction) that classifies each component's lyrical theme (8
>    categories) and vocal posture (3 categories) using an OpenAI-compatible LLM API.
>    A Chinese pronoun pre-pass heuristic runs before the LLM call to cross-check and
>    adjust confidence scores (not replace the LLM).
> 7. **Schema updates.** `components.json` `schema_version` bumps to 2. New columns
>    added to `song_components`: `theme`, `vocal_posture`, `theme_confidence`,
>    `vocal_posture_confidence`, `theme_reasoning`, `posture_reasoning`.
> 8. **Backwards-compatible component extraction.** The hybrid extraction strategy
>    (allin1 sections → lyrics repetition) is preserved from v3. The v4 additions
>    (energy-aware role assignment, madmom downbeats, stem-based features, LLM
>    classification) are **opt-in** via new options. Existing v3 callers continue to
>    work unchanged.

---

## Phase 0: Schema & Models

**Goal:** Add new columns to `song_components` for theme/posture metadata, bump
`components.json` schema_version to 2, and update models.

**Complexity:** M

### 0.1 `ops/admin-cli/src/stream_of_worship/admin/db/schema.py`

Add new columns to `CREATE_SONG_COMPONENTS_TABLE` (after `confidence`):

```python
CREATE_SONG_COMPONENTS_TABLE = """
CREATE TABLE IF NOT EXISTS song_components (
    id SERIAL PRIMARY KEY,
    song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL REFERENCES recordings(content_hash) ON DELETE CASCADE,
    component_type TEXT NOT NULL CHECK (component_type IN
        ('chorus','verse','prechorus','bridge','intro','outro','instrumental')),
    occurrence_index INTEGER NOT NULL DEFAULT 1,
    role TEXT NOT NULL DEFAULT 'none' CHECK (role IN
        ('entry','exit','loop_target','entry_exit','none')),
    start_time REAL,
    end_time REAL,
    bpm REAL,
    key TEXT,
    groove_density REAL,
    backbeat_strength REAL,
    energy_level REAL,
    confidence REAL,
    -- v4: per-field confidence scores
    bpm_confidence REAL,
    key_confidence REAL,
    groove_confidence REAL,
    backbeat_confidence REAL,
    energy_confidence REAL,
    -- v4: LLM-derived theme and vocal posture
    theme TEXT,
    vocal_posture TEXT,
    theme_confidence REAL,
    vocal_posture_confidence REAL,
    -- v4: LLM reasoning fields (for debugging/audit)
    theme_reasoning TEXT,
    posture_reasoning TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""
```

Update `SONG_COMPONENT_COLUMNS_SELECT` to include new columns:

```python
SONG_COMPONENT_COLUMNS_SELECT = """
    id, song_id, content_hash, component_type, occurrence_index, role,
    start_time, end_time, bpm, key, groove_density, backbeat_strength,
    energy_level, confidence,
    bpm_confidence, key_confidence, groove_confidence, backbeat_confidence,
    energy_confidence, theme, vocal_posture, theme_confidence,
    vocal_posture_confidence, theme_reasoning, posture_reasoning,
    created_at, updated_at
"""

# v4: column count increased from 16 to 28 (added 12: 5 per-field confidence,
# 2 theme/posture, 2 confidence, 2 reasoning)
SONG_COMPONENT_COLUMN_COUNT = 28
```

Update `ALL_SCHEMA_STATEMENTS` — no change needed (the table is already in the list;
new columns are additive via `ALTER TABLE` or re-run of `CREATE ... IF NOT EXISTS`).

### 0.2 `ops/admin-cli/src/stream_of_worship/admin/db/models.py`

Update `SongComponent` dataclass with new fields:

```python
@dataclass
class SongComponent:
    # ... existing fields ...
    bpm_confidence: Optional[float] = None
    key_confidence: Optional[float] = None
    groove_confidence: Optional[float] = None
    backbeat_confidence: Optional[float] = None
    energy_confidence: Optional[float] = None
    theme: Optional[str] = None
    vocal_posture: Optional[str] = None
    theme_confidence: Optional[float] = None
    vocal_posture_confidence: Optional[float] = None
    # v4: LLM reasoning fields
    theme_reasoning: Optional[str] = None
    posture_reasoning: Optional[str] = None

    @classmethod
    def from_row(cls, row: tuple) -> "SongComponent":
        return cls(
            # ... existing fields ...
            bpm_confidence=row[14],
            key_confidence=row[15],
            groove_confidence=row[16],
            backbeat_confidence=row[17],
            energy_confidence=row[18],
            theme=row[19],
            vocal_posture=row[20],
            theme_confidence=row[21],
            vocal_posture_confidence=row[22],
            theme_reasoning=row[23],
            posture_reasoning=row[24],
            created_at=_to_str(row[25]),
            updated_at=_to_str(row[26]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            # ... existing fields ...
            "bpm_confidence": self.bpm_confidence,
            "key_confidence": self.key_confidence,
            "groove_confidence": self.groove_confidence,
            "backbeat_confidence": self.backbeat_confidence,
            "energy_confidence": self.energy_confidence,
            "theme": self.theme,
            "vocal_posture": self.vocal_posture,
            "theme_confidence": self.theme_confidence,
            "vocal_posture_confidence": self.vocal_posture_confidence,
            "theme_reasoning": self.theme_reasoning,
            "posture_reasoning": self.posture_reasoning,
        }
```

### 0.3 `ops/admin-cli/src/stream_of_worship/admin/db/client.py`

Update `upsert_song_components` INSERT statement to include new columns:

```python
cursor.executemany(
    """
    INSERT INTO song_components (
        song_id, content_hash, component_type, occurrence_index,
        role, start_time, end_time, bpm, key, groove_density,
        backbeat_strength, energy_level, confidence,
        bpm_confidence, key_confidence, groove_confidence, backbeat_confidence,
        energy_confidence, theme, vocal_posture, theme_confidence,
        vocal_posture_confidence, theme_reasoning, posture_reasoning
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """,
    values,
)
```

Update `values` list comprehension to include new fields.

### 0.4 `ops/analysis-service/src/sow_analysis/storage/cache.py`

Bump `COMPONENT_SCHEMA_VERSION` to 2:

```python
# v4: bumped from 1 to 2. New schema: per-field confidence scores,
# theme, vocal_posture columns.
COMPONENT_SCHEMA_VERSION = 2
```

### 0.5 `ops/analysis-service/src/sow_analysis/models.py`

Update `ComponentResult` model:

```python
class ComponentResult(BaseModel):
    """A single identified song component with computed features."""

    component_type: str
    occurrence_index: int = 1
    role: str = "none"
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    bpm: Optional[float] = None
    key: Optional[str] = None
    groove_density: Optional[float] = None
    backbeat_strength: Optional[float] = None
    energy_level: Optional[float] = None
    confidence: Optional[float] = None
    # v4: per-field confidence scores
    bpm_confidence: Optional[float] = None
    key_confidence: Optional[float] = None
    groove_confidence: Optional[float] = None
    backbeat_confidence: Optional[float] = None
    energy_confidence: Optional[float] = None
    # v4: LLM-derived theme and vocal posture
    theme: Optional[str] = None
    vocal_posture: Optional[str] = None
    theme_confidence: Optional[float] = None
    vocal_posture_confidence: Optional[float] = None
    # v4: LLM reasoning fields (for debugging and audit)
    theme_reasoning: Optional[str] = None
    posture_reasoning: Optional[str] = None
    source: str = ""
```

Update `ComponentAnalysisOptions`:

```python
class ComponentAnalysisOptions(BaseModel):
    """Options for component analysis jobs."""

    force: bool = False
    use_stems: bool = False  # If True, prefer stems audio for feature extraction
    # v4: LLM theme/posture classification
    classify_theme: bool = False  # If True, run LLM theme classification
    classify_vocal_posture: bool = False  # If True, run LLM vocal posture classification
```

Update `ComponentAnalysisJobRequest` docstring to document new options.

### Verification (Phase 0)

```bash
uv run --project ops/admin-cli --extra admin sow-admin db init
psql "$DATABASE_URL" -c "\d song_components"
# Verify new columns exist
psql "$DATABASE_URL" -c "
  SELECT column_name, data_type
  FROM information_schema.columns
  WHERE table_name = 'song_components'
  AND column_name LIKE '%confidence%' OR column_name LIKE '%theme%' OR column_name LIKE '%posture%';
"
```

**Dependencies:** None (first phase).

---

## Phase 1: Analysis Service — Enhanced Component Extraction Module

**Goal:** Enhance `workers/components.py` with energy-aware role assignment, madmom
downbeat detection, stem-based feature extraction, and per-field confidence scoring.

**Complexity:** L

### 1.1 `ops/analysis-service/src/sow_analysis/workers/components.py`

#### Chorus Detection Assumption (v4)

This plan assumes that **repeated lyric line groups identify chorus sections**.
The energy scoring differentiates entry vs. exit occurrences but does NOT
reclassify non-chorus sections as choruses. In other words:

- If allin1 sections label a section as "chorus", it is treated as a chorus.
- If lyrics repetition clustering identifies a repeated line group, it is treated
  as a chorus.
- Energy scoring only determines which chorus occurrence is "entry" and which
  is "exit" — it never changes a component's `component_type`.

This assumption is explicit: v4 does not add a separate "chorus detection" stage.
The chorus identification logic from v3 is preserved unchanged; only the role
assignment (entry/exit) is enhanced.

#### New data structures

Add per-field confidence to `ComponentInstance`:

```python
@dataclass
class ComponentInstance:
    # ... existing fields ...
    # v4: per-field confidence scores
    bpm_confidence: Optional[float] = None
    key_confidence: Optional[float] = None
    groove_confidence: Optional[float] = None
    backbeat_confidence: Optional[float] = None
    energy_confidence: Optional[float] = None
    # v4: LLM-derived theme and vocal posture
    theme: Optional[str] = None
    vocal_posture: Optional[str] = None
    theme_confidence: Optional[float] = None
    vocal_posture_confidence: Optional[float] = None
    # v4: LLM reasoning fields (for debugging and audit)
    theme_reasoning: Optional[str] = None
    posture_reasoning: Optional[str] = None
```

#### New: `_assign_roles_by_energy` (energy-aware entry/exit)

```python
def _assign_roles_by_energy(
    components: list[ComponentInstance],
    y: np.ndarray,
    sr: int,
    beats: Optional[list[float]] = None,
    stems_dir: Optional[Path] = None,
) -> list[ComponentInstance]:
    """Reassign entry/exit roles based on energy/instrumentation cues.

    v4 replaces v3's positional selection (first=entry, last=exit) with
    energy-aware selection. For each chorus occurrence, compute an energy
    score from:
      - RMS energy of the audio slice (full mix or vocals stem)
      - Drum stem onset density (if stems available)
      - Backbeat strength (if stems available)

    The chorus with the LOWEST energy score → role='entry'
    The chorus with the HIGHEST energy score → role='exit'
    Others → role='none'

    If only 1 chorus, it gets both 'entry' and 'exit' roles (same as v3).

    Args:
        components: List of ComponentInstance objects (chorus entries only).
        y: Full audio time series.
        sr: Sample rate.
        beats: Optional beat timestamps.
        stems_dir: Optional path to cached stems directory.

    Returns:
        The same list with roles reassigned.
    """
```

**Energy scoring formula:**

```
energy_score = (
    0.4 * normalized_rms +
    0.3 * normalized_drums_onset_density +
    0.3 * normalized_backbeat_strength
)
```

Where each component is normalized to [0, 1] across all chorus occurrences.

If stems are not available, fall back to RMS-only scoring:

```
energy_score = normalized_rms
```

#### New: `_snap_to_downbeat` (madmom-based)

```python
def _snap_to_downbeat(time_seconds: float, downbeats: list[float]) -> float:
    """Snap a timestamp to the nearest downbeat (from madmom).

    Uses madmom's downbeat detection if available; falls back to
    _snap_to_beat if downbeats is empty or madmom is not installed.

    Args:
        time_seconds: Timestamp in seconds.
        downbeats: Sorted list of downbeat timestamps (from madmom).

    Returns:
        Nearest downbeat timestamp.
    """
    if not downbeats:
        return time_seconds
    downbeats_arr = np.asarray(downbeats, dtype=float)
    idx = int(np.argmin(np.abs(downbeats_arr - time_seconds)))
    return float(downbeats_arr[idx])
```

#### New: `_detect_phrases_via_onset` (phrase boundary detection)

```python
def _detect_phrases_via_onset(
    y: np.ndarray,
    sr: int,
    segment_start: float,
    segment_end: float,
    hop_length: int = 512,
) -> list[float]:
    """Detect phrase boundaries within a segment using onset strength zero-crossings.

    Computes the onset strength envelope, then finds zero-crossings of the
    derivative (peaks = phrase starts, valleys = phrase ends). Returns a list
    of timestamp offsets within [segment_start, segment_end].

    This is used for first-class edit-point alignment: instead of snapping
    to the nearest beat, we snap to the nearest phrase boundary.

    Args:
        y: Full audio time series.
        sr: Sample rate.
        segment_start: Start time of the segment.
        segment_end: End time of the segment.
        hop_length: Hop length for onset strength computation.

    Returns:
        List of phrase boundary timestamps (absolute, not relative).
    """
```

#### Enhanced: `_snap_to_edit_point`

```python
def _snap_to_edit_point(
    time_seconds: float,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    y: Optional[np.ndarray] = None,
    sr: Optional[int] = None,
    segment_start: Optional[float] = None,
    segment_end: Optional[float] = None,
    hop_length: int = 512,
) -> float:
    """Snap a timestamp to the best available edit point.

    Priority order:
      1. Nearest downbeat (from madmom) — most musically meaningful
      2. Nearest phrase boundary (from onset zero-crossings)
      3. Nearest beat (from librosa) — fallback

    Args:
        time_seconds: Timestamp to snap.
        beats: Optional beat timestamps (librosa).
        downbeats: Optional downbeat timestamps (madmom).
        y: Optional audio time series (for phrase detection).
        sr: Optional sample rate (for phrase detection).
        segment_start: Optional segment start (for phrase detection context).
        segment_end: Optional segment end (for phrase detection context).
        hop_length: Hop length for onset strength computation.

    Returns:
        Snapped timestamp.
    """
```

#### Enhanced: `compute_component_features` (stem-aware, per-field confidence)

```python
def compute_component_features(
    y: np.ndarray,
    sr: int,
    component: ComponentInstance,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    stems_dir: Optional[Path] = None,
    hop_length: int = 512,
) -> ComponentInstance:
    """Compute per-component features with stem-aware extraction and per-field confidence.

    v4 changes:
      - Uses cached Demucs stems (drums, vocals, bass, other) when available
        for more accurate feature extraction.
      - Computes per-field confidence scores (bpm_confidence, key_confidence, etc.)
      - Computes a composite `confidence` as the weighted mean of per-field scores.

    Feature extraction with stems:
      - groove_density: from drums stem onset strength (if stems available),
        otherwise from full mix.
      - backbeat_strength: from drums stem RMS at beat positions (if stems available),
        otherwise from full mix.
      - energy_level: from full mix RMS (always) + vocals stem RMS (if stems available,
        weighted average).
      - bpm: from full mix onset strength (stems don't help much here).
      - key: from full mix (stems can introduce artifacts).

    Per-field confidence:
      - bpm_confidence: 0.9 if segment_duration >= 16s, 0.7 if >= 8s, 0.4 if < 8s.
        Adjusted by correlation between onset-based tempo and beat-interval tempo.
      - key_confidence: from detect_key_segment_vote's key_score_margin,
        mapped to [0, 1] via sigmoid.
      - groove_confidence: 0.9 if stems available, 0.7 if from full mix.
      - backbeat_confidence: 0.9 if stems available, 0.7 if from full mix.
      - energy_confidence: 0.9 if stems available (vocals stem for vocal energy),
        0.7 if from full mix only.

    Composite confidence:
      confidence = mean([bpm_confidence, key_confidence, groove_confidence,
                         backbeat_confidence, energy_confidence])

    Args:
        y: Full audio time series.
        sr: Sample rate.
        component: ComponentInstance to compute features for.
        beats: Optional global beat timestamps.
        downbeats: Optional global downbeat timestamps.
        stems_dir: Optional path to cached stems directory.
        hop_length: Hop length for onset strength computation.

    Returns:
        The same ComponentInstance with features and per-field confidences populated.
    """
```

#### Enhanced: `identify_from_allin1_sections` (v4)

```python
def identify_from_allin1_sections(
    sections: list[dict],
    snap_to_downbeat: bool = True,
    downbeats: Optional[list[float]] = None,
) -> list[ComponentInstance]:
    """Identify chorus/verse components from allin1 section labels.

    v4: When snap_to_downbeat=True and downbeats are provided, start_time/end_time
    are snapped to nearest downbeat instead of nearest beat.

    Verse assignment: The FIRST verse section in the song is assigned role='loop_target'
    (not the last verse before chorus, which was v3 behavior). This ensures the
    loopable verse used for transitions is the song's opening verse.

    Args:
        sections: List of section dicts with 'label', 'start', 'end' keys.
        snap_to_downbeat: If True, snap to downbeats (requires downbeats param).
        downbeats: Optional downbeat timestamps for snapping.

    Returns:
        List of ComponentInstance objects.
    """
```

#### Enhanced: `identify_from_lyrics_repetition` (v4)

```python
def identify_from_lyrics_repetition(
    lrc_content: str,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    song_total_duration: Optional[float] = None,
    snap_to_downbeat: bool = True,
) -> list[ComponentInstance]:
    """Identify chorus via repeated-line-group clustering on LRC lines.

    v4: When snap_to_downbeat=True and downbeats are provided, start_time/end_time
    are snapped to nearest downbeat instead of nearest beat.

    Args:
        lrc_content: Raw LRC file content.
        beats: Optional list of beat timestamps for snapping.
        downbeats: Optional list of downbeat timestamps for snapping (preferred).
        song_total_duration: Optional total song duration for position weighting.
        snap_to_downbeat: If True, snap to downbeats (requires downbeats param).

    Returns:
        List of ComponentInstance objects.
    """
```

#### Enhanced: `extract_components` (v4)

```python
async def extract_components(
    audio_path: Path,
    content_hash: str,
    cache_manager: CacheManager,
    r2_client: Optional[R2Client],
    sections: Optional[list[dict]] = None,
    lrc_content: Optional[str] = None,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    force: bool = False,
    use_stems: bool = False,
    snap_to_downbeat: bool = True,
    energy_aware_roles: bool = False,
) -> tuple[list[ComponentInstance], str]:
    """Extract song components using hybrid strategy (v4 enhancements).

    v4 additions:
      - `use_stems`: If True, load cached Demucs stems and use them for
        per-component feature extraction.
      - `snap_to_downbeat`: If True, use madmom downbeats for edit-point
        snapping (requires downbeats from madmom).
      - `energy_aware_roles`: If True, run _assign_roles_by_energy after
        identification to reassign entry/exit roles based on energy cues.

    Returns (components, source) where source is one of:
    'allin1_sections', 'lyrics_repetition', 'none'.
    """
```

### 1.2 `ops/analysis-service/src/sow_analysis/workers/__init__.py`

No change needed — the module is imported lazily in `queue.py`.

### Verification (Phase 1)

```bash
cd ops/analysis-service && uv run --extra dev pytest tests/test_components.py -v
```

**Dependencies:** None (can be developed/tested standalone with fixture audio + mock sections/LRC).

---

## Phase 2: Analysis Service — madmom Downbeat Detection

**Goal:** Add madmom-based downbeat detection as an option in component analysis.

**Complexity:** M

### 2.1 `ops/analysis-service/src/sow_analysis/workers/components.py`

Add madmom downbeat detection:

```python
def _detect_downbeats_madmom(
    audio_path: Path,
    sr: int = 22050,
) -> Optional[list[float]]:
    """Detect downbeats using madmom.

    madmom's `onset.downbeat` model produces more accurate downbeat timestamps
    than librosa's beat tracker. This is used for first-class edit-point alignment
    in v4.

    Args:
        audio_path: Path to audio file.
        sr: Sample rate for loading audio.

    Returns:
        Sorted list of downbeat timestamps, or None if madmom is not available
        or detection fails.
    """
    try:
        import madmom
        from madmom.features.downbeats import DBPDownbeatProcessor

        y, sr = librosa.load(str(audio_path), sr=sr, mono=True)
        processor = DBPDownbeatProcessor(num_threads=1)
        downbeat_frames = processor(y)
        # Convert frames to timestamps
        hop_length = 512  # madmom default
        downbeat_times = madmom.signal.frames_to_time(
            downbeat_frames[:, 0],
            fps=processor.fps,
            hop_length=hop_length,
            audio_signal=y,
            sample_rate=sr,
        )
        return sorted(downbeat_times.tolist())
    except ImportError:
        logger.warning("madmom not installed, skipping downbeat detection")
        return None
    except Exception as e:
        logger.warning(f"madmom downbeat detection failed: {e}")
        return None
```

### 2.2 `ops/analysis-service/src/sow_analysis/workers/queue.py`

When `use_stems` or `snap_to_downbeat` is True in the job request, run madmom
downbeat detection before component extraction:

```python
async def _process_component_analysis_job(self, job: Job) -> None:
    """Process a component analysis job (v4).

    v4 flow additions:
    - If snap_to_downbeat=True and downbeats not in request, run
      _detect_downbeats_madmom() to obtain downbeat timestamps.
    - Pass downbeats to extract_components() for edit-point snapping.
    - If use_stems=True and stems_dir exists in cache, pass stems_dir
      to compute_component_features() for stem-aware feature extraction.
    - If energy_aware_roles=True, run _assign_roles_by_energy() after
      identification to reassign entry/exit roles.
    """
```

### Verification (Phase 2)

```bash
cd ops/analysis-service && uv run --extra dev pytest tests/test_components.py -v -k downbeat
```

**Dependencies:** Phase 1 (components module).

---

## Phase 3: Analysis Service — LLM Theme & Vocal Posture Classification

**Goal:** Add a new LLM stage that classifies each component's lyrical theme
(8 categories) and vocal posture using an OpenAI-compatible API.

**Complexity:** L

### 3.1 `ops/analysis-service/src/sow_analysis/workers/classifier.py` (NEW)

This module handles theme and vocal posture classification via LLM.

```python
"""LLM-based theme and vocal posture classification for song components."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI

from ..config import settings
from .components import ComponentInstance

logger = logging.getLogger(__name__)

# 8 theme categories — the ONLY valid values for theme classification.
# These are the exact strings the LLM must output.
THEME_CATEGORIES = (
    "Call to Worship",
    "Adoration/Praise",
    "Thanksgiving/Testimony",
    "Declaration/Faith",
    "Confession/Repentance",
    "Surrender/Consecration",
    "Contemplation/Intimacy",
    "Benediction/Sending",
)

# Vocal posture categories — the ONLY valid values for vocal posture classification.
# Describes the singer's relational stance in the lyrics.
VOCAL_POSTURE_CATEGORIES = (
    "To God",
    "About God",
    "To Congregation",
)

# Mapping from the 8-category system to the existing 12-theme admin-cli system.
# (Used for reference only; v4 output uses only the 8 categories above.)
THEME_TO_OLD_12 = {
    "Call to Worship":          {"敬拜高潮", "启示与异象"},
    "Adoration/Praise":         {"赞美与敬拜", "喜乐与欢呼"},
    "Thanksgiving/Testimony":   {"感恩与感谢"},
    "Declaration/Faith":        {"宣告与争战", "盼望与信靠"},
    "Confession/Repentance":    {"悔改与洁净", "忧伤与哀恸"},
    "Surrender/Consecration":   {"奉献与委身"},
    "Contemplation/Intimacy":   {"默想与敬虔"},
    "Benediction/Sending":      {"合一与团契"},
}

# Mapping from the 8-category system to the 5-phase worship arc.
THEME_TO_5PHASE = {
    "Call to Worship":          "Phase 1: Call to Worship",
    "Adoration/Praise":         "Phase 2: Thanksgiving & Praise",
    "Thanksgiving/Testimony":   "Phase 2: Thanksgiving & Praise",
    "Declaration/Faith":        "Phase 3: Worship (mid-set)",
    "Confession/Repentance":    "Phase 4: Response",
    "Surrender/Consecration":   "Phase 3/4: Worship/Response",
    "Contemplation/Intimacy":   "Phase 3: Worship",
    "Benediction/Sending":      "Phase 5: Commission",
}

# CJK character range for Chinese text detection.
_CJK_RANGE_START = 0x4E00
_CJK_RANGE_END = 0x9FFF

# First-person pronouns (I, we).
_FIRST_PERSON_PRONOUNS = ("我", "我們", "咱", "俺")

# Second-person pronouns — casual (you).
_SECOND_PERSON_CASUAL = ("你", "您", "你們", "爾")

# Third-person pronouns — casual (he, she, it, they).
_THIRD_PERSON_CASUAL = ("他", "她", "它", "他們", "她們", "它們")

# Religious pronouns — used specifically for God in Chinese Christian context.
# 祢 (U+794E): "You" directed to God (second person, reverent).
# 祂 (U+7956): "He/Him" referring to God (third person, reverent).
_RELIGIOUS_SECOND_PERSON = ("\u794e")   # 祢 — you-to-God
_RELIGIOUS_THIRD_PERSON = ("\u7956")   # 祂 — he-to-God

# Imperative / exhortation markers for "To Congregation" detection.
_CONGREGATION_MARKERS = ("我們", "讓我們", "當", "應當", "要", "彼此", "眾", "凡")


def _count_cjk_chars(text: str) -> int:
    """Count CJK characters in text."""
    return sum(1 for ch in text if _CJK_RANGE_START <= ord(ch) <= _CJK_RANGE_END)


def _has_first_person_pronoun(text: str) -> bool:
    """Check if text contains first-person pronouns (I/we)."""
    return any(p in text for p in _FIRST_PERSON_PRONOUNS)


def _has_second_person_casual(text: str) -> bool:
    """Check if text contains casual second-person pronouns (you)."""
    return any(p in text for p in _SECOND_PERSON_CASUAL)


def _has_third_person_casual(text: str) -> bool:
    """Check if text contains casual third-person pronouns (he/she/it/they)."""
    return any(p in text for p in _THIRD_PERSON_CASUAL)


def _has_religious_pronoun(text: str) -> tuple[bool, bool]:
    """Check for religious pronouns (祢/祂) in text.

    Returns:
        (has_religious_you, has_religious_he) — whether 祢 and/or 祂 appear.
    """
    has_you = _RELIGIOUS_SECOND_PERSON in text
    has_he = _RELIGIOUS_THIRD_PERSON in text
    return (has_you, has_he)


def _has_congregation_marker(text: str) -> bool:
    """Check if text contains imperative/exhortation markers for congregation address.

    Markers like 讓我們 (let us), 當 (should), 彼此 (one another) indicate
    the singer is addressing the congregation, not God.
    """
    return any(m in text for m in _CONGREGATION_MARKERS)


def _classify_posture_heuristic(lyrics: str) -> Optional[str]:
    """Chinese pronoun pre-pass heuristic for vocal posture.

    This heuristic runs BEFORE the LLM call to provide a fast-path signal
    that cross-checks and adjusts the LLM output. It does NOT replace the LLM.

    The religious-pronoun distinction (祢/祂 vs 你/他) is the core design principle:
    only religious pronouns confirm direct address to God. Casual pronouns without
    religious markers cannot confirm "To God" — they are classified conservatively
    as "About God" because the pre-pass cannot distinguish between a singer
    addressing God (using simplified 你) and describing God to others (using 你/他
    in narrative lyrics). The −0.1 disagreement path handles the legitimate case
    where a modern simplified-Chinese worship song uses casual 你 to address God
    directly — the LLM is allowed to override, with a mild penalty.

    Heuristic rules (in priority order):
      1. Religious pronoun (祢/祂) present → "To God" (strong signal)
      2. Imperative plural / congregation markers (讓我們, 當, 彼此, 你们/你們)
         → "To Congregation" (exhortation wins over casual pronoun ambiguity)
      3. Casual 你 OR 他 present AND 祢/祂 ABSENT → "About God" (conservative;
         third-person-leaning — without 祢/祂 we cannot confirm God-directed address)
      4. No pronouns / no markers → None (let LLM decide)

    Returns the heuristic classification, or None if the heuristic is
    inconclusive (LLM should be used as primary classifier).
    """
    if not lyrics:
        return None

    # Rule 1: Religious pronouns are the strongest signal for "To God".
    has_religious_you, has_religious_he = _has_religious_pronoun(lyrics)
    if has_religious_you or has_religious_he:
        return "To God"

    # Rule 2: Imperative / exhortation markers → "To Congregation".
    #    This is checked BEFORE casual-pronoun rules so exhortation wins
    #    over ambiguous casual pronoun patterns.
    if _has_congregation_marker(lyrics):
        return "To Congregation"

    # Rule 3: Casual 你 OR 他 present, but NO religious pronouns → "About God".
    #    Conservative: without 祢/祂 we cannot confirm God-directed address.
    has_second = _has_second_person_casual(lyrics)
    has_third = _has_third_person_casual(lyrics)

    if has_second or has_third:
        return "About God"

    # Rule 4: No clear pattern — let LLM decide.
    return None


class ThemeClassifier:
    """Classifies song components using LLM theme and vocal posture detection."""

    def __init__(self):
        if not settings.SOW_LLM_API_KEY:
            raise ValueError(
                "SOW_LLM_API_KEY environment variable not set. "
                "Set this to your LLM API key for theme/posture classification."
            )
        if not settings.SOW_LLM_BASE_URL:
            raise ValueError(
                "SOW_LLM_BASE_URL environment variable not set. "
                "Set this to your LLM API base URL."
            )
        if not settings.SOW_LLM_MODEL:
            raise ValueError(
                "SOW_LLM_MODEL environment variable not set. "
                "Set this to your LLM model ID."
            )
        self._client = OpenAI(
            api_key=settings.SOW_LLM_API_KEY,
            base_url=settings.SOW_LLM_BASE_URL,
            timeout=30.0,
            max_retries=2,
        )

    async def classify_component(
        self,
        component: ComponentInstance,
        lyrics_lines: Optional[list[str]] = None,
    ) -> ComponentInstance:
        """Classify a single component's theme and vocal posture.

        v4: The heuristic pre-pass does NOT replace the LLM. Instead, it runs
        first and its result is used to cross-check and adjust the LLM output.
        This ensures LLM provides the primary classification while the heuristic
        provides a confidence signal.

        Args:
            component: ComponentInstance with lyrics context.
            lyrics_lines: Optional list of lyric lines for the component
                (used as context for the LLM prompt).

        Returns:
            The same ComponentInstance with theme/vocal_posture populated.
        """
        # Combine lyrics lines into a single text for heuristic + LLM.
        lyrics_text = " ".join(lyrics_lines or []) if lyrics_lines else ""

        # Step 1: Heuristic pre-pass — runs first but does NOT replace LLM.
        #    The pre-pass classifies posture only (theme is orthogonal to pronouns).
        heuristic_posture = _classify_posture_heuristic(lyrics_text)
        heuristic_theme = None  # pre-pass does not classify theme authoritatively

        # Step 2: Always call LLM for primary classification.
        await self._classify_component_llm(component, lyrics_text, heuristic_posture)
        return component

    async def _classify_component_llm(
        self,
        component: ComponentInstance,
        lyrics_text: str,
        heuristic_posture: Optional[str] = None,
    ) -> None:
        """Classify via LLM API call, with heuristic cross-check.

        v4: The LLM always runs. The heuristic result (if available) is used
        to adjust confidence scores after the LLM returns. The pre-pass is
        fundamentally about POSTURE, not theme — theme_confidence is governed
        solely by the LLM's self-reported value and the decisiveness penalty.

        Posture adjustment scheme:
          - Heuristic agrees with LLM → +0.05 (capped at 0.95)
          - Heuristic="To God" AND LLM="To Congregation" → −0.2; if result < 0.6
            → flag for review (genuine conflict: religious pronouns present but
            LLM interprets as congregational exhortation)
          - Heuristic="About God" AND LLM="To God" → −0.1 (modern simplified-Chinese
            song using casual 你 to address God; LLM likely correct, no auto-flag)
          - All other disagreements → −0.2 (genuine conflict; flag if < 0.6)
          - Heuristic is None (inconclusive) → no posture adjustment

        Decisiveness penalty (applied to both theme and posture):
          - LLM reasoning mentions multiple candidates ("or"/"either"/"possibly"/"maybe")
            → −0.1 on both confidence fields
        """
        prompt = self._build_prompt(component, lyrics_text)
        try:
            response = await asyncio.to_thread(
                self._client.chat.completions.create,
                model=settings.SOW_LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                max_tokens=300,
            )
            result = response.choices[0].message.content
            parsed = self._parse_llm_json(result)

            # Populate from LLM.
            component.theme = parsed.get("theme")
            component.theme_confidence = parsed.get("theme_confidence", 0.7)
            component.theme_reasoning = parsed.get("theme_reasoning", "")
            component.vocal_posture = parsed.get("vocal_posture")
            component.vocal_posture_confidence = parsed.get("vocal_posture_confidence", 0.7)
            component.posture_reasoning = parsed.get("posture_reasoning", "")

            # Heuristic cross-check adjustments (posture only).
            self._apply_heuristic_adjustment(component, heuristic_posture)

            # Retry once on JSON parse failure.
            if component.theme is None or component.vocal_posture is None:
                await self._retry_llm_call(component, lyrics_text)

        except Exception as e:
            logger.warning(f"LLM classification failed for component: {e}")
            # Leave theme/posture as None — components are still valid.

    def _parse_llm_json(self, text: str) -> dict:
        """Parse LLM JSON response with basic error handling."""
        import json
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {}

    def _apply_heuristic_adjustment(
        self,
        component: ComponentInstance,
        heuristic_posture: Optional[str],
    ) -> None:
        """Adjust LLM confidence based on heuristic agreement.

        v4: Nuanced penalty scheme for vocal posture. The pre-pass is fundamentally
        about POSTURE, not theme — theme_confidence is governed solely by the LLM's
        self-reported value and the decisiveness penalty.

        VOCAL POSTURE adjustments:
          - Heuristic is None (inconclusive) → no posture adjustment.
          - Heuristic agrees with LLM posture → +0.05 (capped at 0.95).
          - Heuristic="To God" (祢/祂 present) AND LLM="To Congregation" →
            −0.2 on posture_confidence. If result < 0.6 → flag for review
            (genuine conflict: religious pronouns present but LLM interprets
            as congregational exhortation).
          - Heuristic="About God" (casual 你/他, no 祢/祂) AND LLM="To God" →
            −0.1 (modern simplified-Chinese song using casual 你 to address God;
            LLM likely correct — do NOT auto-flag for review).
          - All other disagreements → −0.2 (genuine conflict; flag for review
            if resulting posture_confidence < 0.6).

        THEME: no heuristic adjustment. The pronoun pre-pass does not classify
        theme authoritatively. theme_confidence is governed by the LLM's
        self-reported value and the decisiveness penalty only.

        DECISIVENESS PENALTY (applies to both fields):
          - LLM reasoning mentions multiple candidates ("or"/"either"/"possibly"/"maybe")
            → −0.1 on both theme_confidence and vocal_posture_confidence.

        Args:
            component: ComponentInstance with LLM-populated fields.
            heuristic_posture: Optional heuristic posture result.
        """
        if not heuristic_posture or not component.vocal_posture:
            # No adjustment if heuristic is inconclusive or LLM failed.
            pass
        elif heuristic_posture == component.vocal_posture:
            # Agreement: mild boost.
            component.vocal_posture_confidence = min(
                0.95,
                (component.vocal_posture_confidence or 0.7) + 0.05,
            )
        else:
            # Disagreement — two distinct branches.
            if heuristic_posture == "To God" and component.vocal_posture == "To Congregation":
                # Religious pronouns present but LLM says congregational.
                # Strong signal of genuine conflict.
                component.vocal_posture_confidence = max(
                    0.0,
                    (component.vocal_posture_confidence or 0.7) - 0.2,
                )
                if component.vocal_posture_confidence < 0.6:
                    # Flag for review: religious pronouns + LLM disagrees.
                    # The review flag is surfaced by low confidence routing
                    # (no separate field needed — the < 0.6 threshold is the flag).
                    logger.warning(
                        f"Review flagged: heuristic='To God' (祢/祂 present) "
                        f"but LLM='To Congregation' for component "
                        f"occurrence={component.occurrence_index}"
                    )
            elif heuristic_posture == "About God" and component.vocal_posture == "To God":
                # Casual 你/他 but LLM says To God — modern simplified-Chinese
                # worship song using casual pronoun to address God directly.
                # LLM likely correct; mild penalty, no auto-flag.
                component.vocal_posture_confidence = max(
                    0.0,
                    (component.vocal_posture_confidence or 0.7) - 0.1,
                )
            else:
                # All other disagreements — genuine conflict.
                component.vocal_posture_confidence = max(
                    0.0,
                    (component.vocal_posture_confidence or 0.7) - 0.2,
                )
                if component.vocal_posture_confidence < 0.6:
                    logger.warning(
                        f"Review flagged: heuristic='{heuristic_posture}' "
                        f"but LLM='{component.vocal_posture}' for component "
                        f"occurrence={component.occurrence_index}"
                    )

        # Decisiveness penalty: check if reasoning mentions multiple candidates.
        for reasoning_field in [component.theme_reasoning or "", component.posture_reasoning or ""]:
            if any(word in reasoning_field.lower() for word in ["or ", "either", "possibly", "maybe"]):
                if component.theme_confidence:
                    component.theme_confidence = max(0.0, component.theme_confidence - 0.1)
                if component.vocal_posture_confidence:
                    component.vocal_posture_confidence = max(
                        0.0, component.vocal_posture_confidence - 0.1
                    )

    async def _retry_llm_call(
        self,
        component: ComponentInstance,
        lyrics_text: str,
    ) -> None:
        """Retry LLM call once on JSON parse failure."""
        try:
            prompt = self._build_prompt(component, lyrics_text)
            response = await asyncio.to_thread(
                self._client.chat.completions.create,
                model=settings.SOW_LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                max_tokens=300,
            )
            result = response.choices[0].message.content
            parsed = self._parse_llm_json(result)
            component.theme = parsed.get("theme")
            component.vocal_posture = parsed.get("vocal_posture")
        except Exception as e:
            logger.warning(f"LLM retry failed for component: {e}")

    def _build_prompt(
        self,
        component: ComponentInstance,
        lyrics_text: str,
    ) -> str:
        """Build the LLM prompt for theme + vocal posture classification.

        v4: Uses a 6-field JSON schema with few-shot examples.
        """
        themes_str = ", ".join(f'"{t}"' for t in THEME_CATEGORIES)
        postures_str = ", ".join(f'"{p}"' for p in VOCAL_POSTURE_CATEGORIES)

        return f"""Classify the following song component's lyrical theme and vocal posture.

Component type: {component.component_type}
Occurrence: {component.occurrence_index}
Role: {component.role}

Lyrics:
{lyrics_text[:2000]}

## Theme Categories (choose exactly ONE):
{themes_str}

## Vocal Posture Categories (choose exactly ONE):
{postures_str}

## JSON Response Schema:
{{
  "theme": "one of the theme categories above",
  "theme_confidence": 0.0-1.0,
  "theme_reasoning": "brief explanation",
  "vocal_posture": "one of the posture categories above",
  "posture_confidence": 0.0-1.0,
  "posture_reasoning": "brief explanation"
}}

## Examples:

Example 1 — Direct address to God with religious pronoun:
Lyrics: "祢是聖潔的，祢配得一切讚美" (You are holy, You deserve all praise)
Response: {{"theme": "Adoration/Praise", "theme_confidence": 0.95, "theme_reasoning": "Religious pronoun 祢 + praise language", "vocal_posture": "To God", "posture_confidence": 0.98, "posture_reasoning": "Direct address to God using 祢"}}

Example 2 — Third-person description of God:
Lyrics: "神愛世人，賜下獨生子" (God loved the world, gave His only Son)
Response: {{"theme": "Declaration/Faith", "theme_confidence": 0.85, "theme_reasoning": "Describes God's character and works", "vocal_posture": "About God", "posture_confidence": 0.95, "posture_reasoning": "Third-person reference to God (祂/他)"}}

Example 3 — Congregational exhortation:
Lyrics: "讓我們歡喜快樂，歸榮耀給神" (Let us rejoice and give glory to God)
Response: {{"theme": "Adoration/Praise", "theme_confidence": 0.80, "theme_reasoning": "Call to praise together", "vocal_posture": "To Congregation", "posture_confidence": 0.90, "posture_reasoning": "Imperative plural 讓我們 (let us)"}}

Return ONLY valid JSON matching the schema above. No markdown, no explanation.
"""
```

### 3.2 `ops/analysis-service/src/sow_analysis/workers/queue.py`

After component extraction, if `classify_theme` or `classify_vocal_posture` is True
in the job request options, run the LLM classifier:

```python
# After extract_components() returns:
if job.request.options.classify_theme or job.request.options.classify_vocal_posture:
    try:
        from .classifier import ThemeClassifier
        classifier = ThemeClassifier()
        for component in components:
            # Fetch lyrics lines for this component from LRC or DB.
            component = await classifier.classify_component(component, lyrics_lines)
    except Exception as e:
        logger.warning(f"LLM classification failed: {e}")
        # Don't fail the job — components are still valid without theme/posture.
```

### 3.3 Theme/Posture Placement Decision (Songs vs. Components)

v4 places `theme` and `vocal_posture` on **`song_components` chorus rows**, not on
the `songs` table. This honors the song vs. component distinction:

- **Song-level** metadata (key, BPM) is relatively stable across recordings of the
  same song.
- **Component-level** metadata (theme, posture) is specific to each component instance.

**Shared values for verbatim choruses:** If a song has multiple chorus occurrences
that are lyrically identical, they will receive the same theme and vocal_posture
values (since the LLM receives the same lyrics). The confidence scores may differ
slightly if the heuristic cross-check produces different results for different
occurrences (e.g., if context lines differ).

**Implication for songset constructor:** The songset constructor should query
`song_components` for chorus rows to get theme/posture metadata. If a song has
no chorus component with theme/posture populated, the song-level theme (from the
existing 12-theme system) can be used as a fallback.

### Verification (Phase 3)

```bash
cd ops/analysis-service && uv run --extra dev pytest tests/test_classifier.py -v
```

**Dependencies:** Phase 1 (components module), LLM API key configured.

---

## Phase 4: Analysis Service — Job Integration & Cache

**Goal:** Update job models, queue processing, and cache strategy for v4.

**Complexity:** M

### 4.1 `ops/analysis-service/src/sow_analysis/routes/jobs.py`

Update the component analysis endpoint to accept new options:

```python
@router.post("/jobs/component-analysis", response_model=JobResponse)
async def submit_component_analysis_job(
    request: ComponentAnalysisJobRequest,
    api_key: str = Depends(verify_api_key),
) -> JobResponse:
    """Submit a component analysis job.

    v4: Additional options for energy-aware role assignment, madmom
    downbeat snapping, stem-based feature extraction, and LLM theme/posture
    classification.
    """
```

### 4.2 `ops/analysis-service/src/sow_analysis/workers/queue.py`

Update `_process_component_analysis_job` to handle v4 options:

```python
async def _process_component_analysis_job(self, job: Job) -> None:
    """Process a component analysis job (v4).

    v4 flow:
    1. Set status PROCESSING, stage 'downloading'
    2. Download audio from R2 to temp dir
    3. Check CacheManager.get_component_result(content_hash) — if cached AND
       schema_version == COMPONENT_SCHEMA_VERSION (v4) AND not force, short-circuit.
    4. Check R2 for {hash_prefix}/components.json — same schema_version check.
    5. If snap_to_downbeat=True and downbeats not in request:
       - Run _detect_downbeats_madmom(audio_path) to obtain downbeat timestamps.
    6. If use_stems=True: check CacheManager.get_stems_dir(content_hash) for
       cached Demucs stems.
    7. Call extract_components() with:
       - use_stems=True/False (from options)
       - snap_to_downbeat=True/False (from options)
       - energy_aware_roles=True/False (from options)
       - downbeats (from madmom or request)
       - stems_dir (from cache, if use_stems=True)
    8. If classify_theme or classify_vocal_posture:
       - Run ThemeClassifier for each component.
    9. Convert list[ComponentInstance] → list[ComponentResult].
    10. Upload component results to R2 as {hash_prefix}/components.json
        (with schema_version=2).
    11. Save to local cache via cache_manager.save_component_result().
    12. Build JobResult(components=..., component_source=...).
    13. Set status COMPLETED, stage 'complete'.
    14. Persist to JobStore.
    """
```

### 4.3 `ops/analysis-service/src/sow_analysis/storage/r2.py`

No changes needed — `upload_component_result` and `download_component_result`
already handle the generic dict payload (schema_version check is in CacheManager).

### Verification (Phase 4)

```bash
cd ops/analysis-service && uv run --extra dev pytest tests/test_queue.py -v -k component
cd ops/analysis-service && uv run --extra dev pytest tests/integration/test_api.py -v -k component
```

**Dependencies:** Phase 1 (components module), Phase 2 (madmom), Phase 3 (classifier).

---

## Phase 5: Admin CLI — Persistence & Display (v4)

**Goal:** Update admin CLI to persist and display new v4 fields (per-field confidence,
theme, vocal posture).

**Complexity:** M

### 5.1 `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`

Update `submit_component_analysis` to pass new options:

```python
def submit_component_analysis(
    self,
    audio_url: str,
    content_hash: str,
    song_id: str = "",
    sections: Optional[list[dict]] = None,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    lrc_content: Optional[str] = None,
    force: bool = False,
    # v4 options
    use_stems: bool = False,
    snap_to_downbeat: bool = True,
    energy_aware_roles: bool = False,
    classify_theme: bool = False,
    classify_vocal_posture: bool = False,
) -> JobInfo:
    """Submit a component analysis job to the analysis service (v4).

    v4: Passes new options for energy-aware role assignment, madmom downbeat
    snapping, stem-based feature extraction, and LLM theme/posture classification.
    """
```

### 5.2 `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

Update the `components` command display to include new columns:

```
┌────────────┬──────┬─────────────┬───────────┬───────┬─────┬────────┬──────────┬────────┬────────────┬────────┬─────────────┬────────┬──────────────┐
│ Type       │ Occ. │ Role        │ Start-End │ BPM   │ Key │ Groove │ Backbeat │ Energy │ Confidence │ BPM_C  │ Key_C       │ Theme  │ Vocal Posture│
├────────────┼──────┼─────────────┼───────────┼───────┼─────┼────────┼──────────┼────────┼────────────┼────────┼─────────────┼────────┼──────────────┤
│ chorus     │ 1    │ entry       │ 45.2-72.1 │ 80.0  │ G   │ 0.45   │ 1.12     │ -18.3  │ 0.90       │ 0.85   │ 0.92        │ praise │ adoration    │
│ chorus     │ 2    │ exit        │ 140.3-167 │ 80.0  │ G   │ 0.43   │ 1.08     │ -17.9  │ 0.90       │ 0.85   │ 0.92        │ praise │ celebration  │
│ verse      │ 1    │ loop_target │ 30.0-45.2 │ 80.0  │ G   │ 0.38   │ 0.95     │ -19.1  │ 0.90       │ 0.80   │ 0.88        │ faith  │ declaration  │
└────────────┴──────┴─────────────┴───────────┴───────┴─────┴────────┴──────────┴────────┴────────────┴────────┴─────────────┴────────┴──────────────┘
```

Where `BPM_C` = bpm_confidence, `Key_C` = key_confidence.

### 5.3 Extend `show` command

In `show_recording()`, display theme and vocal_posture columns alongside existing
component fields.

### Verification (Phase 5)

```bash
uv run --project ops/admin-cli --extra admin sow-admin audio components song_0001
uv run --project ops/admin-cli --extra admin sow-admin audio show song_0001
```

**Dependencies:** Phase 0 (schema/client), Phase 4 (job integration).

---

## Phase 6: Backfill & Migration

**Goal:** Re-run component extraction on existing songs with v4 enhancements.

**Complexity:** S

### 6.1 Backfill strategy

| Option | Description | Cost |
|---|---|---|
| `use_stems` | Uses cached Demucs stems for feature extraction | +5s/song (stem load) |
| `snap_to_downbeat` | Runs madmom downbeat detection | +2-5s/song |
| `energy_aware_roles` | Post-processes chorus roles by energy | +1s/song |
| `classify_theme` | LLM theme classification | +1-3s/component |
| `classify_vocal_posture` | LLM vocal posture classification | +1-3s/component |

### 6.2 Batch backfill command

```bash
# Backfill with all v4 enhancements (slower but richer metadata)
uv run --project ops/admin-cli --extra admin sow-admin audio list --analysis completed --format ids \
  | uv run --project ops/admin-cli --extra admin sow-admin audio components --stdin \
    --use-stems --snap-to-downbeat --energy-roles --classify-theme --classify-posture
```

### 6.3 Migration idempotency

New columns use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` pattern. The `CREATE ... IF NOT EXISTS`
on the table remains; new columns are additive. Re-running `sow-admin db init` is safe.

### Verification (Phase 6)

```bash
# Verify new columns
psql "$DATABASE_URL" -c "SELECT theme, vocal_posture, theme_confidence FROM song_components LIMIT 5;"
```

**Dependencies:** Phase 5 (admin CLI persistence).

---

## 10. Deliverables

### 10a. Processing-Stage Breakdown & Parallelization

```
Stage 0: Download audio from R2
  └── Stage 1: Parallel branch A — allin1 section detection
  └── Stage 1: Parallel branch B — LRC lyrics parsing
  └── Stage 1: Parallel branch C — beat/downbeat detection (librosa + madmom)
  └── Stage 1: Parallel branch D — stem extraction (if use_stems=True)
        │
        ▼
Stage 2: Hybrid component identification
  (allin1 sections → lyrics repetition fallback)
        │
        ▼
Stage 3: Feature extraction (stem-aware if available)
  └── Stage 3: Parallel — groove_density, backbeat_strength, energy_level
        │
        ▼
Stage 4: Energy-aware role assignment (if energy_aware_roles=True)
        │
        ▼
Stage 5: LLM theme/posture classification (if classify_theme/Posture=True)
  └── Stage 5: Parallel — one LLM call per component (asyncio.gather)
        │
        ▼
Stage 6: Cache write + R2 upload
```

**Parallelization strategy:**
- Stage 1 branches A-D run in parallel (independent analyses of same audio).
- Stage 3 feature extraction runs in parallel per component (each chorus/verse is independent).
- Stage 5 LLM calls run in parallel via `asyncio.gather` (one call per component).
- Total parallelism: O(num_components) LLM calls, O(num_components) feature extractions.

### 10b. Library & Algorithm Choices Justification

| Component | Library/Algorithm | Justification |
|---|---|---|
| Beat tracking | `librosa` | Fast, reliable for tempo range of worship music (60–140 BPM). Used as fallback. |
| Downbeat detection | `madmom` (RNN) | Superior accuracy for worship music tempos; already in analysis-service deps. |
| Section detection | `allin1` | Existing model; provides chorus/verse/bridge labels. |
| Lyrics clustering | Custom repetition algorithm | LRC format is simple; repeated line groups reliably identify choruses. |
| Stem separation | Demucs (cached) | Already used in analysis pipeline; stems improve feature accuracy. |
| Theme/posture LLM | OpenAI-compatible API | Flexible, supports few-shot prompting; model can be swapped via config. |
| Heuristic pre-pass | Rule-based (Chinese pronouns) | Zero-cost, provides confidence signal for LLM cross-check. |

### 10c. Input/Output Mapping per Stage → Schema Fields

| Stage | Input | Output | Maps to Schema Fields |
|---|---|---|---|
| 1A: allin1 | Audio file | Section labels + timestamps | `component_type`, `start_time`, `end_time` |
| 1B: LRC | LRC content | Line groups + timestamps | `component_type` (chorus via repetition) |
| 1C: beats | Audio file | Beat/downbeat timestamps | Edit-point snapping (no schema column) |
| 1D: stems | Audio file | Stem waveforms (if use_stems) | Feature extraction quality |
| 3: features | Component slice + stems | DSP features | `groove_density`, `backbeat_strength`, `energy_level` |
| 3: per-field conf | Segment duration, stem availability | Confidence scores | `bpm_confidence`, `key_confidence`, `groove_confidence`, `backbeat_confidence`, `energy_confidence` |
| 4: energy roles | Energy scores | Role assignments | `role` (entry/exit/loop_target/none) |
| 5: LLM | Lyrics text | JSON classification | `theme`, `vocal_posture`, `theme_confidence`, `vocal_posture_confidence`, `theme_reasoning`, `posture_reasoning` |

### 10d. Edge-Case & Failure Handling

| Edge Case | Handling Strategy |
|---|---|
| No allin1 sections, no LRC | Return empty component list; job completes with source='none' |
| madmom import fails | Fall back to librosa beats; log warning; continue |
| Demucs stems not cached | Fall back to full-mix feature extraction; lower per-field confidence |
| LLM API timeout | Retry once; if still fails, leave theme/posture as None; job completes |
| LLM JSON parse fails | Retry once with same prompt; if still fails, leave theme/posture as None |
| Single chorus occurrence | Assign both 'entry' and 'exit' roles (same as v3) |
| Chorus with identical energy scores | Fall back to positional: first=entry, last=exit |
| Empty lyrics for LLM | Skip LLM call; leave theme/posture as None |
| Heuristic + LLM disagree | Apply −0.2 posture confidence penalty; log disagreement for audit |
| Religious pronoun ambiguity (祢 used for non-God "you") | LLM reasoning field captures context; low confidence flags for manual review |

### 10e. Pilot & Validation Approach

**Phase 1: Unit validation (automated)**
- Run classifier on 50 manually-labeled chorus samples (theme + posture).
- Measure accuracy against ground truth.
- Target: ≥85% theme accuracy, ≥90% posture accuracy.

**Phase 2: Integration validation (manual spot-check)**
- Run full v4 pipeline on 10 songs with all options enabled.
- Manually verify theme classifications make sense in context.
- Verify vocal posture aligns with pronoun patterns.
- Check per-field confidence scores are reasonable.

**Phase 3: Backfill validation**
- Backfill 100 songs with v4 enhancements.
- Compare theme distribution against existing 12-theme system.
- Verify no regressions in component extraction quality.

**Validation metrics:**
- Theme accuracy: % of LLM themes matching manual review
- Posture accuracy: % of LLM postures matching manual review
- Heuristic agreement rate: % of cases where heuristic agrees with LLM
- LLM success rate: % of components with non-null theme/posture
- Average confidence: mean(theme_confidence, vocal_posture_confidence)

---

## Summary of Changes by File

| File | Change |
|---|---|
| `ops/admin-cli/src/stream_of_worship/admin/db/schema.py` | Add 12 new columns to `song_components` table (5 per-field confidence, 2 theme/posture, 2 confidence, 2 reasoning) |
| `ops/admin-cli/src/stream_of_worship/admin/db/models.py` | Add 12 new fields to `SongComponent` dataclass |
| `ops/admin-cli/src/stream_of_worship/admin/db/client.py` | Update INSERT/SELECT for new columns |
| `ops/analysis-service/src/sow_analysis/storage/cache.py` | Bump `COMPONENT_SCHEMA_VERSION` to 2 |
| `ops/analysis-service/src/sow_analysis/models.py` | Add per-field confidence + theme/posture + reasoning fields to `ComponentResult`, new options to `ComponentAnalysisOptions` |
| `ops/analysis-service/src/sow_analysis/workers/components.py` | Energy-aware role assignment, madmom downbeat snapping, stem-based features, per-field confidence, chorus detection assumption |
| `ops/analysis-service/src/sow_analysis/workers/classifier.py` | **NEW** — LLM theme & vocal posture classifier with Chinese pronoun heuristic cross-check, few-shot prompt, decisiveness signal, retry logic |
| `ops/analysis-service/src/sow_analysis/workers/queue.py` | Dispatch v4 options (madmom, stems, classifier) |
| `ops/analysis-service/src/sow_analysis/routes/jobs.py` | Accept new options in endpoint |
| `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py` | Pass new options to analysis service |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | Display new columns, pass new CLI flags |

---

## Dependencies Between Phases

```
Phase 0 (schema/models)
  └── Phase 1 (components.py enhancements)
        ├── Phase 2 (madmom downbeat detection)
        ├── Phase 3 (LLM classifier)
        └── Phase 4 (job integration)
              └── Phase 5 (admin CLI persistence/display)
                    └── Phase 6 (backfill/migration)
```

All phases can be developed incrementally. Phase 1-4 can be tested in isolation with
fixture audio and mock sections/LRC. Phase 5-6 require a running database and analysis
service.

---

## Testing Strategy

### Unit Tests
- `test_components.py`: Test each function independently with synthetic audio.
- `test_classifier.py`: Test heuristic pre-pass with known Chinese pronoun patterns.
- `test_downbeat.py`: Test madmom downbeat detection against known audio.

### Integration Tests
- `test_queue.py`: Test full job pipeline with v4 options.
- `test_api.py`: Test endpoint with new options.
- `test_admin_cli.py`: Test CLI commands with new flags.

### E2E Tests
- Run backfill on a small set of songs with all v4 options enabled.
- Verify theme/posture classifications make sense (manual spot-check).
- Verify per-field confidence scores are reasonable.

---

## Risk Assessment

| Risk | Mitigation |
|---|---|
| madmom not available on all platforms | Graceful fallback to librosa beats |
| LLM API failures | Retry once; if still fails, leave theme/posture as None; job completes |
| Stem cache misses | Fall back to full-mix feature extraction |
| Performance regression | v4 options are opt-in; v3 path unchanged |
| Heuristic + LLM disagree | −0.2 confidence penalty; reasoning field captures context for audit |
| Token cost for LLM | Heuristic does NOT reduce LLM calls — LLM always runs for primary classification |

---

## Future Extensions (out of scope for v4)

1. **Pre-chorus detection** — currently ignored in v3/v4.
2. **Crossfade point detection** — identify smooth transition points between components.
3. **Key change detection** — detect modulations within songs.
4. **Structural similarity clustering** — group songs by component structure patterns.
5. **Automated songset generation** — use component metadata to auto-generate worship sets.
