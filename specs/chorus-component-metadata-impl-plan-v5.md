---

# Implementation Plan: Chorus-Based Song Component Metadata (v5)

> **Revision Notes:** This supersedes `specs/chorus-component-metadata-impl-plan-v4.md` (v4).
>
> v5 fixes critical issues found during code review of v4 against the actual codebase:
>
> 1. **Schema migration via ALTER TABLE.** v4 only updated the `CREATE TABLE IF NOT EXISTS` statement, which is a no-op for existing tables. v5 adds explicit `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements to `ALL_SCHEMA_STATEMENTS` so existing databases get the new columns on `sow-admin db init`.
> 2. **Column count fixed.** v4 claimed 28 columns but only had 27 (16 + 11 new, not 12). v5 uses the correct count of 27.
> 3. **INSERT placeholder count fixed.** v4 had 24 column names but 25 `%s` placeholders. v5 ensures they match (24 each).
> 4. **Serialization updated.** v4 omitted `_serialize_components`/`_deserialize_components` updates — cached payloads would silently drop theme/posture fields. v5 includes them.
> 5. **Existing 12-theme system used.** v4 invented an 8-category English theme system with a broken mapping to the existing 12-theme system (`THEMES = ("讚美","感恩","敬拜","奉獻","認罪","差遣","信心","祈禱","復興","聖靈","十字架","跟隨")`). v5 uses the actual 12 Chinese themes from `songset_constructor/rules/themes.py`.
> 6. **madmom API corrected.** v4 used a non-existent `DBPDownbeatProcessor` class and wrong calling conventions. v5 uses the correct two-stage pipeline: `RNNDownBeatProcessor` (file path → activations) → `DBNDownBeatTrackingProcessor` (activations → beats with bar positions).
> 7. **LLM classifier reuses existing rate-limiting.** v4 created a standalone OpenAI client (max_retries=2, 30s timeout) bypassing the existing `SOW_LLM_MAX_CONCURRENT` semaphore, 16-retry backoff, and min_interval throttle. v5 reuses the same LLM helper pattern as `workers/lrc.py`.
> 8. **Per-component lyrics retrieval.** v4 referenced undefined `lyrics_lines` in the classifier call. v5 adds LRC time-range filtering to extract per-component lyrics.
> 9. **Verse assignment unchanged from v3.** v4 silently changed loop_target from "verse before first chorus" to "first verse in song". v5 keeps v3 behavior.
> 10. **Decisiveness penalty fixed.** v4 matched `"or "` substring (false positives: "for", "Lord") and double-applied across both confidence fields. v5 uses `\bor\b` regex and applies per-field only.
> 11. **Retry re-runs cross-check.** v4's `_retry_llm_call` only parsed theme/vocal_posture, skipping confidence/reasoning/heuristic cross-check. v5 fixes this.
> 12. **DB CHECK constraints.** v4 had no DB-level validation for theme/posture values. v5 adds CHECK constraints enforcing the 12-theme and 3-posture vocabularies.
> 13. **Single-chorus two-row pattern handled.** v4's energy scorer didn't handle the v3 pattern of two rows (entry+exit) with identical time ranges. v5 deduplicates by unique time range before scoring.
> 14. **Parallel LLM calls.** v4's Section 10a promised `asyncio.gather` but Phase 3.2 used a sequential loop. v5 uses `asyncio.gather`.
> 15. **Conversion updates.** v4 didn't update `ComponentInstance → ComponentResult` conversion in queue.py or `_parse_component_results` in audio.py. v5 includes both.

---

## Phase 0: Schema & Models

**Goal:** Add new columns to `song_components` for theme/posture metadata, bump
`components.json` schema_version to 2, and update models.

**Complexity:** M

### 0.1 `ops/admin-cli/src/stream_of_worship/admin/db/schema.py`

**Step 1:** Add `ALTER TABLE` statements for each new column. These are idempotent
(PostgreSQL 9.6+) and safe to re-run on `sow-admin db init`.

```python
# v5: ALTER TABLE statements for new columns (idempotent, safe for existing tables).
ALTER_SONG_COMPONENTS_V5_COLUMNS = """
ALTER TABLE song_components ADD COLUMN IF NOT EXISTS bpm_confidence REAL;
ALTER TABLE song_components ADD COLUMN IF NOT EXISTS key_confidence REAL;
ALTER TABLE song_components ADD COLUMN IF NOT EXISTS groove_confidence REAL;
ALTER TABLE song_components ADD COLUMN IF NOT EXISTS backbeat_confidence REAL;
ALTER TABLE song_components ADD COLUMN IF NOT EXISTS energy_confidence REAL;
ALTER TABLE song_components ADD COLUMN IF NOT EXISTS theme TEXT
    CHECK (theme IN ('讚美','感恩','敬拜','奉獻','認罪','差遣','信心','祈禱','復興','聖靈','十字架','跟隨') OR theme IS NULL);
ALTER TABLE song_components ADD COLUMN IF NOT EXISTS vocal_posture TEXT
    CHECK (vocal_posture IN ('To God','About God','To Congregation') OR vocal_posture IS NULL);
ALTER TABLE song_components ADD COLUMN IF NOT EXISTS theme_confidence REAL;
ALTER TABLE song_components ADD COLUMN IF NOT EXISTS vocal_posture_confidence REAL;
ALTER TABLE song_components ADD COLUMN IF NOT EXISTS theme_reasoning TEXT;
ALTER TABLE song_components ADD COLUMN IF NOT EXISTS posture_reasoning TEXT;
"""
```

**Step 2:** Add `ALTER_SONG_COMPONENTS_V5_COLUMNS` to `ALL_SCHEMA_STATEMENTS`
(after `CREATE_SONG_COMPONENTS_TABLE` and its indexes).

**Step 3:** Update `SONG_COMPONENT_COLUMNS_SELECT` to include new columns:

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

# v5: 16 original + 11 new = 27 columns.
# New columns (11): 5 per-field confidence, 2 theme/posture,
# 2 confidence, 2 reasoning.
SONG_COMPONENT_COLUMN_COUNT = 27
```

Column index mapping (0-based):
```
0: id, 1: song_id, 2: content_hash, 3: component_type, 4: occurrence_index,
5: role, 6: start_time, 7: end_time, 8: bpm, 9: key,
10: groove_density, 11: backbeat_strength, 12: energy_level, 13: confidence,
14: bpm_confidence, 15: key_confidence, 16: groove_confidence,
17: backbeat_confidence, 18: energy_confidence,
19: theme, 20: vocal_posture, 21: theme_confidence,
22: vocal_posture_confidence, 23: theme_reasoning, 24: posture_reasoning,
25: created_at, 26: updated_at
```

### 0.2 `ops/admin-cli/src/stream_of_worship/admin/db/models.py`

Update `SongComponent` dataclass with 11 new fields:

```python
@dataclass
class SongComponent:
    # ... existing fields (id through confidence, created_at, updated_at) ...
    # v5: per-field confidence scores
    bpm_confidence: Optional[float] = None
    key_confidence: Optional[float] = None
    groove_confidence: Optional[float] = None
    backbeat_confidence: Optional[float] = None
    energy_confidence: Optional[float] = None
    # v5: LLM-derived theme and vocal posture
    theme: Optional[str] = None
    vocal_posture: Optional[str] = None
    theme_confidence: Optional[float] = None
    vocal_posture_confidence: Optional[float] = None
    # v5: LLM reasoning fields (for debugging/audit)
    theme_reasoning: Optional[str] = None
    posture_reasoning: Optional[str] = None

    @classmethod
    def from_row(cls, row: tuple) -> "SongComponent":
        return cls(
            id=row[0],
            song_id=row[1],
            content_hash=row[2],
            component_type=row[3],
            occurrence_index=row[4],
            role=row[5],
            start_time=row[6],
            end_time=row[7],
            bpm=row[8],
            key=row[9],
            groove_density=row[10],
            backbeat_strength=row[11],
            energy_level=row[12],
            confidence=row[13],
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

Update `upsert_song_components` INSERT statement. 24 columns (excluding id, created_at,
updated_at which are DB-managed), 24 `%s` placeholders:

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
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """,
    values,
)
```

Update `values` list comprehension to include the 11 new fields:
```python
values = [
    (
        c.song_id or song_id,
        c.content_hash or content_hash,
        c.component_type,
        c.occurrence_index,
        c.role,
        c.start_time,
        c.end_time,
        c.bpm,
        c.key,
        c.groove_density,
        c.backbeat_strength,
        c.energy_level,
        c.confidence,
        c.bpm_confidence,
        c.key_confidence,
        c.groove_confidence,
        c.backbeat_confidence,
        c.energy_confidence,
        c.theme,
        c.vocal_posture,
        c.theme_confidence,
        c.vocal_posture_confidence,
        c.theme_reasoning,
        c.posture_reasoning,
    )
    for c in components
]
```

### 0.4 `ops/analysis-service/src/sow_analysis/storage/cache.py`

Bump `COMPONENT_SCHEMA_VERSION` to 2:

```python
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
    # v5: per-field confidence scores
    bpm_confidence: Optional[float] = None
    key_confidence: Optional[float] = None
    groove_confidence: Optional[float] = None
    backbeat_confidence: Optional[float] = None
    energy_confidence: Optional[float] = None
    # v5: LLM-derived theme and vocal posture
    theme: Optional[str] = None
    vocal_posture: Optional[str] = None
    theme_confidence: Optional[float] = None
    vocal_posture_confidence: Optional[float] = None
    # v5: LLM reasoning fields (for debugging and audit)
    theme_reasoning: Optional[str] = None
    posture_reasoning: Optional[str] = None
    source: str = ""
```

Update `ComponentAnalysisOptions`:

```python
class ComponentAnalysisOptions(BaseModel):
    """Options for component analysis jobs."""

    force: bool = False
    use_stems: bool = False
    # v5: madmom downbeat snapping (uses existing downbeats if provided,
    # otherwise runs madmom detection on the audio file)
    snap_to_downbeat: bool = False
    # v5: energy-aware entry/exit role assignment
    energy_aware_roles: bool = False
    # v5: LLM theme classification (12 Chinese themes)
    classify_theme: bool = False
    # v5: LLM vocal posture classification (3 categories)
    classify_vocal_posture: bool = False
```

### Verification (Phase 0)

```bash
uv run --project ops/admin-cli --extra admin sow-admin db init
psql "$DATABASE_URL" -c "\d song_components"
# Verify new columns exist with CHECK constraints
psql "$DATABASE_URL" -c "
  SELECT column_name, data_type
  FROM information_schema.columns
  WHERE table_name = 'song_components'
  ORDER BY ordinal_position;
"
```

**Dependencies:** None (first phase).

---

## Phase 1: Analysis Service — Enhanced Component Extraction Module

**Goal:** Enhance `workers/components.py` with energy-aware role assignment, stem-based
feature extraction, per-field confidence scoring, and serialization updates.

**Complexity:** L

### 1.1 `ops/analysis-service/src/sow_analysis/workers/components.py`

#### New fields on `ComponentInstance`

Add per-field confidence and LLM fields to the dataclass:

```python
@dataclass
class ComponentInstance:
    # ... existing fields ...
    # v5: per-field confidence scores
    bpm_confidence: Optional[float] = None
    key_confidence: Optional[float] = None
    groove_confidence: Optional[float] = None
    backbeat_confidence: Optional[float] = None
    energy_confidence: Optional[float] = None
    # v5: LLM-derived theme and vocal posture
    theme: Optional[str] = None
    vocal_posture: Optional[str] = None
    theme_confidence: Optional[float] = None
    vocal_posture_confidence: Optional[float] = None
    # v5: LLM reasoning fields
    theme_reasoning: Optional[str] = None
    posture_reasoning: Optional[str] = None
```

#### New: `_assign_roles_by_energy` (energy-aware entry/exit)

```python
def _assign_roles_by_energy(
    components: list[ComponentInstance],
    y: np.ndarray,
    sr: int,
    stems_dir: Optional[Path] = None,
) -> list[ComponentInstance]:
    """Reassign entry/exit roles based on energy/instrumentation cues.

    Only operates on chorus components (component_type='chorus'). Verse and
    other component roles (e.g., loop_target) are preserved unchanged.

    For each unique chorus occurrence (identified by unique start_time/end_time
    pairs — handles the v3 single-chorus two-row pattern), compute an energy
    score from:
      - RMS energy of the audio slice (full mix or vocals stem)
      - Drum stem onset density (if stems available)
      - Backbeat strength (if stems available)

    The unique chorus with the LOWEST energy score → role='entry'
    The unique chorus with the HIGHEST energy score → role='exit'
    Others → role='none'

    If only 1 unique chorus, keep both 'entry' and 'exit' roles (v3 behavior).
    If energy scores are identical, fall back to positional: first=entry, last=exit.

    Args:
        components: List of ALL ComponentInstance objects (function filters
            to chorus-only internally).
        y: Full audio time series.
        sr: Sample rate.
        stems_dir: Optional path to cached Demucs stems directory.

    Returns:
        The same list with chorus roles reassigned.
    """
```

**Energy scoring formula (with stems):**
```
energy_score = 0.4 * normalized_rms + 0.3 * normalized_drums_onset_density + 0.3 * normalized_backbeat_strength
```

**Energy scoring formula (without stems):**
```
energy_score = normalized_rms
```

**Single-chorus handling:** Deduplicate chorus entries by (start_time, end_time).
If only one unique pair exists, keep the existing entry/exit roles unchanged.

#### New: `_snap_to_downbeat`

```python
def _snap_to_downbeat(time_seconds: float, downbeats: list[float]) -> float:
    """Snap a timestamp to the nearest downbeat.

    Args:
        time_seconds: Timestamp in seconds.
        downbeats: Sorted list of downbeat timestamps.

    Returns:
        Nearest downbeat timestamp, or the input if downbeats is empty.
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
    of absolute timestamp offsets within [segment_start, segment_end].

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

    v5 changes:
      - Uses cached Demucs stems (drums, vocals, bass, other) when available.
      - Computes per-field confidence scores.
      - Computes composite `confidence` as weighted mean of per-field scores.

    Feature extraction with stems:
      - groove_density: from drums stem onset strength (if stems available).
      - backbeat_strength: from drums stem RMS at beat positions (if stems available).
      - energy_level: from full mix RMS (always) + vocals stem RMS (weighted if available).
      - bpm: from full mix onset strength (stems don't help much here).
      - key: from full mix (stems can introduce artifacts).

    Per-field confidence:
      - bpm_confidence: 0.9 if segment >= 16s, 0.7 if >= 8s, 0.4 if < 8s.
      - key_confidence: from detect_key_segment_vote's key_score_margin, sigmoid-mapped.
      - groove_confidence: 0.9 if stems available, 0.7 if full mix only.
      - backbeat_confidence: 0.9 if stems available, 0.7 if full mix only.
      - energy_confidence: 0.9 if stems available, 0.7 if full mix only.

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

#### Enhanced: `identify_from_allin1_sections` (v5)

Preserves v3 behavior including:
- positional role assignment (first=entry, last=exit)
- single chorus → two rows (entry + exit, same occurrence_index=1)
- loop_target = verse immediately before first chorus (UNCHANGED from v3)

New v5 parameter:
```python
def identify_from_allin1_sections(
    sections: list[dict],
    snap_to_downbeat: bool = False,
    downbeats: Optional[list[float]] = None,
) -> list[ComponentInstance]:
    """Identify chorus/verse components from allin1 section labels.

    v5: When snap_to_downbeat=True and downbeats are provided, start_time/end_time
    are snapped to nearest downbeat instead of nearest beat.

    Verse assignment (UNCHANGED from v3): The verse section immediately preceding
    the first chorus is assigned role='loop_target'.

    Args:
        sections: List of section dicts with 'label', 'start', 'end' keys.
        snap_to_downbeat: If True, snap to downbeats (requires downbeats param).
        downbeats: Optional downbeat timestamps for snapping.

    Returns:
        List of ComponentInstance objects.
    """
```

#### Enhanced: `identify_from_lyrics_repetition` (v5)

```python
def identify_from_lyrics_repetition(
    lrc_content: str,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    song_total_duration: Optional[float] = None,
    snap_to_downbeat: bool = False,
) -> list[ComponentInstance]:
    """Identify chorus via repeated-line-group clustering on LRC lines.

    v5: When snap_to_downbeat=True and downbeats are provided, start_time/end_time
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

#### Enhanced: `extract_components` (v5)

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
    snap_to_downbeat: bool = False,
    energy_aware_roles: bool = False,
) -> tuple[list[ComponentInstance], str]:
    """Extract song components using hybrid strategy (v5 enhancements).

    v5 additions:
      - `use_stems`: If True, load cached Demucs stems for per-component
        feature extraction.
      - `snap_to_downbeat`: If True, use downbeats for edit-point snapping.
      - `energy_aware_roles`: If True, run _assign_roles_by_energy after
        identification to reassign entry/exit roles based on energy cues.

    Note: `analyze_audio_fast()` does NOT return beats/downbeats. When the
    tier-2 lyrics path runs and beats are missing, the caller (queue.py)
    should run madmom downbeat detection BEFORE calling this function to
    populate the downbeats parameter.

    Returns (components, source) where source is one of:
    'allin1_sections', 'lyrics_repetition', 'none'.
    """
```

#### CRITICAL: Update `_serialize_components` and `_deserialize_components`

These functions handle cache serialization. Without updating them, theme/posture
and per-field confidence data is silently lost on cache round-trips.

```python
def _serialize_components(
    components: list[ComponentInstance],
    content_hash: str,
    hash_prefix: str,
    source: str,
) -> dict:
    """Serialize components to the v5 components.json payload."""
    return {
        "schema_version": COMPONENT_SCHEMA_VERSION,  # now 2
        "content_hash": content_hash,
        "hash_prefix": hash_prefix,
        "component_source": source,
        "components": [
            {
                "component_type": c.component_type,
                "occurrence_index": c.occurrence_index,
                "role": c.role,
                "start_time": c.start_time,
                "end_time": c.end_time,
                "bpm": c.bpm,
                "key": c.key,
                "groove_density": c.groove_density,
                "backbeat_strength": c.backbeat_strength,
                "energy_level": c.energy_level,
                "confidence": c.confidence,
                # v5: per-field confidence
                "bpm_confidence": c.bpm_confidence,
                "key_confidence": c.key_confidence,
                "groove_confidence": c.groove_confidence,
                "backbeat_confidence": c.backbeat_confidence,
                "energy_confidence": c.energy_confidence,
                # v5: LLM theme/posture
                "theme": c.theme,
                "vocal_posture": c.vocal_posture,
                "theme_confidence": c.theme_confidence,
                "vocal_posture_confidence": c.vocal_posture_confidence,
                # v5: reasoning
                "theme_reasoning": c.theme_reasoning,
                "posture_reasoning": c.posture_reasoning,
            }
            for c in components
        ],
    }


def _deserialize_components(payload: dict) -> list[ComponentInstance]:
    """Deserialize components from a cached components.json payload."""
    components = []
    for c in payload.get("components", []):
        components.append(
            ComponentInstance(
                component_type=c.get("component_type", ""),
                occurrence_index=c.get("occurrence_index", 1),
                role=c.get("role", "none"),
                start_time=c.get("start_time", 0.0),
                end_time=c.get("end_time", 0.0),
                bpm=c.get("bpm"),
                key=c.get("key"),
                groove_density=c.get("groove_density"),
                backbeat_strength=c.get("backbeat_strength"),
                energy_level=c.get("energy_level"),
                confidence=c.get("confidence"),
                # v5: per-field confidence
                bpm_confidence=c.get("bpm_confidence"),
                key_confidence=c.get("key_confidence"),
                groove_confidence=c.get("groove_confidence"),
                backbeat_confidence=c.get("backbeat_confidence"),
                energy_confidence=c.get("energy_confidence"),
                # v5: LLM theme/posture
                theme=c.get("theme"),
                vocal_posture=c.get("vocal_posture"),
                theme_confidence=c.get("theme_confidence"),
                vocal_posture_confidence=c.get("vocal_posture_confidence"),
                # v5: reasoning
                theme_reasoning=c.get("theme_reasoning"),
                posture_reasoning=c.get("posture_reasoning"),
                source=payload.get("component_source", ""),
            )
        )
    return components
```

### 1.2 `ops/analysis-service/src/sow_analysis/workers/__init__.py`

No change needed — the module is imported lazily.

### Verification (Phase 1)

```bash
cd ops/analysis-service && uv run --extra dev pytest tests/test_components.py -v
```

**Dependencies:** Phase 0 (models).

---

## Phase 2: Analysis Service — madmom Downbeat Detection

**Goal:** Add madmom-based downbeat detection for the tier-2 path (when allin1
downbeats are not available).

**Complexity:** M

### 2.1 `ops/analysis-service/src/sow_analysis/workers/components.py`

Add madmom downbeat detection using the CORRECT API:

```python
def _detect_downbeats_madmom(
    audio_path: Path,
) -> Optional[list[float]]:
    """Detect downbeats using madmom's two-stage pipeline.

    madmom API (correct usage):
      1. RNNDownBeatProcessor() takes a FILE PATH (not numpy array),
         returns activations array at 100 fps.
      2. DBNDownBeatTrackingProcessor(beats_per_bar=[3,4], fps=100) takes
         activations, returns [[time, beat_in_bar], ...].
      3. Downbeats = rows where beat_in_bar == 1.

    Note: madmom resamples to 44100 Hz internally. fps=100 must match between
    RNNDownBeatProcessor and DBNDownBeatTrackingProcessor.

    Args:
        audio_path: Path to audio file.

    Returns:
        Sorted list of downbeat timestamps, or None if detection fails.
    """
    try:
        from madmom.features.downbeats import (
            RNNDownBeatProcessor,
            DBNDownBeatTrackingProcessor,
        )

        rnn = RNNDownBeatProcessor()
        activations = rnn(str(audio_path))

        dbn = DBNDownBeatTrackingProcessor(
            beats_per_bar=[3, 4],  # model 3/4 and 4/4 time
            fps=100,               # must match RNNDownBeatProcessor's internal fps
        )
        beats = dbn(activations)  # shape (num_beats, 2): [time, beat_in_bar]

        # Downbeats are where beat_in_bar == 1
        downbeat_times = beats[beats[:, 1] == 1][:, 0]
        return sorted(downbeat_times.tolist())
    except Exception as e:
        logger.warning(f"madmom downbeat detection failed: {e}")
        return None
```

**When to use:** Only when `downbeats` is not already provided (tier-2 lyrics
path without full allin1 analysis). The full analysis path already has downbeats
from allin1 in `recordings.downbeats`, passed via `request.downbeats`.

### 2.2 `ops/analysis-service/src/sow_analysis/workers/queue.py`

In `_process_component_analysis_job`, when `snap_to_downbeat=True` and downbeats
are not in the request, run madmom downbeat detection BEFORE calling
`extract_components`:

```python
# v5: If snap_to_downbeat requested but downbeats not provided, run madmom.
downbeats = request.downbeats
if request.options.snap_to_downbeat and not downbeats:
    madmom_downbeats = await loop.run_in_executor(
        None, _detect_downbeats_madmom, audio_path
    )
    if madmom_downbeats:
        downbeats = madmom_downbeats
    else:
        logger.warning("madmom downbeat detection returned None; using beat snapping only")
```

### Verification (Phase 2)

```bash
cd ops/analysis-service && uv run --extra dev pytest tests/test_components.py -v -k downbeat
```

**Dependencies:** Phase 1 (components module).

---

## Phase 3: Analysis Service — LLM Theme & Vocal Posture Classification

**Goal:** Add LLM classification for theme (12 Chinese categories) and vocal posture
(3 categories), reusing existing rate-limiting infrastructure.

**Complexity:** L

### 3.1 `ops/analysis-service/src/sow_analysis/workers/classifier.py` (NEW)

This module handles theme and vocal posture classification via LLM.

```python
"""LLM-based theme and vocal posture classification for song components."""

import asyncio
import json
import logging
import re
from typing import Optional

from openai import OpenAI

from ..config import settings
from .components import ComponentInstance
from .lrc_parser import parse_lrc

logger = logging.getLogger(__name__)

# IMPORT the existing 12-theme system from songset_constructor rules.
# These are the ONLY valid values for theme classification.
# Source: ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/themes.py
THEME_CATEGORIES = (
    "讚美", "感恩", "敬拜", "奉獻", "認罪", "差遣",
    "信心", "祈禱", "復興", "聖靈", "十字架", "跟隨",
)

# Vocal posture categories — the ONLY valid values for vocal posture classification.
VOCAL_POSTURE_CATEGORIES = (
    "To God",
    "About God",
    "To Congregation",
)

# CJK character range for Chinese text detection.
_CJK_RANGE_START = 0x4E00
_CJK_RANGE_END = 0x9FFF

# Religious pronouns — used specifically for God in Chinese Christian context.
# 祢 (U+794E): "You" directed to God (second person, reverent).
# 祂 (U+7956): "He/Him" referring to God (third person, reverent).
_RELIGIOUS_SECOND_PERSON = "\u794e"  # 祢 — you-to-God
_RELIGIOUS_THIRD_PERSON = "\u7956"   # 祂 — he-to-God

# First-person pronouns (I, we).
_FIRST_PERSON_PRONOUNS = ("我", "我們", "咱", "俺")

# Second-person pronouns — casual (you).
_SECOND_PERSON_CASUAL = ("你", "您", "你們", "爾")

# Third-person pronouns — casual (he, she, it, they).
_THIRD_PERSON_CASUAL = ("他", "她", "它", "他們", "她們", "它們")

# Imperative / exhortation markers for "To Congregation" detection.
_CONGREGATION_MARKERS = ("讓我們", "當", "應當", "要", "彼此", "眾", "凡")


def _has_religious_pronoun(text: str) -> tuple[bool, bool]:
    """Check for religious pronouns (祢/祂) in text.

    Returns:
        (has_religious_you, has_religious_he)
    """
    has_you = _RELIGIOUS_SECOND_PERSON in text
    has_he = _RELIGIOUS_THIRD_PERSON in text
    return (has_you, has_he)


def _classify_posture_heuristic(lyrics: str) -> Optional[str]:
    """Chinese pronoun pre-pass heuristic for vocal posture.

    Runs BEFORE the LLM call to cross-check and adjust the LLM output.
    Does NOT replace the LLM.

    Heuristic rules (in priority order):
      1. Religious pronoun (祢/祂) present → "To God" (strong signal)
      2. Imperative plural / congregation markers (讓我們, 當, 彼此) → "To Congregation"
      3. Casual 你 OR 他 present AND 祢/祂 ABSENT → "About God" (conservative)
      4. No pronouns / no markers → None (let LLM decide)

    Returns the heuristic classification, or None if inconclusive.
    """
    if not lyrics:
        return None

    # Rule 1: Religious pronouns → "To God".
    has_religious_you, has_religious_he = _has_religious_pronoun(lyrics)
    if has_religious_you or has_religious_he:
        return "To God"

    # Rule 2: Imperative / exhortation markers → "To Congregation".
    if any(m in lyrics for m in _CONGREGATION_MARKERS):
        return "To Congregation"

    # Rule 3: Casual 你 OR 他 present, no 祢/祂 → "About God".
    has_second = any(p in lyrics for p in _SECOND_PERSON_CASUAL)
    has_third = any(p in lyrics for p in _THIRD_PERSON_CASUAL)
    if has_second or has_third:
        return "About God"

    # Rule 4: No clear pattern.
    return None


def _extract_lyrics_for_component(
    lrc_content: str,
    start_time: float,
    end_time: float,
) -> list[str]:
    """Extract lyric lines within a component's time range from LRC content.

    Parses the LRC content using the existing parse_lrc() from lrc_parser.py,
    then filters lines whose timestamps fall within [start_time, end_time].

    Args:
        lrc_content: Raw LRC file content.
        start_time: Component start time in seconds.
        end_time: Component end time in seconds.

    Returns:
        List of lyric line texts within the time range.
    """
    try:
        lrc_file = parse_lrc(lrc_content)
    except (ValueError, Exception):
        return []

    return [
        ln.text
        for ln in lrc_file.lines
        if ln.text and ln.text.strip()
        and start_time <= ln.time_seconds <= end_time
    ]


# Decisiveness indicator regex: word-boundary "or", "either", "possibly", "maybe"
_DECISIVENESS_PATTERN = re.compile(
    r"\b(or|either|possibly|maybe)\b", re.IGNORECASE
)


class ThemeClassifier:
    """Classifies song components using LLM theme and vocal posture detection.

    Reuses the existing LLM rate-limiting infrastructure (SOW_LLM_MAX_CONCURRENT
    semaphore, retry/backoff) shared with LRC and embedding jobs.
    """

    def __init__(self):
        if not settings.SOW_LLM_API_KEY:
            raise ValueError(
                "SOW_LLM_API_KEY environment variable not set."
            )
        if not settings.SOW_LLM_BASE_URL:
            raise ValueError(
                "SOW_LLM_BASE_URL environment variable not set."
            )
        if not settings.SOW_LLM_MODEL:
            raise ValueError(
                "SOW_LLM_MODEL environment variable not set."
            )
        # Reuse the same OpenAI client pattern as workers/lrc.py.
        self._client = OpenAI(
            api_key=settings.SOW_LLM_API_KEY,
            base_url=settings.SOW_LLM_BASE_URL,
        )
        self._model = settings.SOW_LLM_MODEL
        # Reuse the module-level LLM semaphore if available.
        # (Imported from the same location as lrc.py imports it.)
        self._llm_semaphore = _get_llm_semaphore()
        self._llm_min_interval = settings.SOW_LLM_MIN_INTERVAL_SECONDS

    async def classify_components(
        self,
        components: list[ComponentInstance],
        lrc_content: Optional[str] = None,
    ) -> list[ComponentInstance]:
        """Classify multiple components in parallel via asyncio.gather.

        Args:
            components: List of ComponentInstance objects to classify.
            lrc_content: Optional LRC text for per-component lyrics extraction.

        Returns:
            The same list with theme/vocal_posture populated.
        """
        tasks = []
        for comp in components:
            lyrics_lines = None
            if lrc_content and comp.start_time is not None and comp.end_time is not None:
                lyrics_lines = _extract_lyrics_for_component(
                    lrc_content, comp.start_time, comp.end_time
                )
            tasks.append(self.classify_component(comp, lyrics_lines))

        await asyncio.gather(*tasks, return_exceptions=True)
        return components

    async def classify_component(
        self,
        component: ComponentInstance,
        lyrics_lines: Optional[list[str]] = None,
    ) -> ComponentInstance:
        """Classify a single component's theme and vocal posture.

        The heuristic pre-pass runs first but does NOT replace the LLM.
        It provides a confidence cross-check signal.

        Args:
            component: ComponentInstance with lyrics context.
            lyrics_lines: Optional list of lyric lines for the component.

        Returns:
            The same ComponentInstance with theme/vocal_posture populated.
        """
        lyrics_text = " ".join(lyrics_lines or []) if lyrics_lines else ""

        # Step 1: Heuristic pre-pass (posture only).
        heuristic_posture = _classify_posture_heuristic(lyrics_text)

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

        Uses the shared LLM semaphore and min_interval throttle.

        Posture adjustment scheme:
          - Heuristic agrees with LLM → +0.05 (capped at 0.95)
          - Heuristic="To God" AND LLM="To Congregation" → −0.2; flag if < 0.6
          - Heuristic="About God" AND LLM="To God" → −0.1 (no auto-flag)
          - All other disagreements → −0.2 (flag if < 0.6)
          - Heuristic is None → no posture adjustment

        Decisiveness penalty (per-field, NOT cross-applied):
          - theme_reasoning mentions decisiveness words → −0.1 on theme_confidence only
          - posture_reasoning mentions decisiveness words → −0.1 on posture_confidence only
        """
        prompt = self._build_prompt(component, lyrics_text)

        async with self._llm_semaphore:
            # Min interval throttle (same pattern as lrc.py).
            if self._llm_min_interval > 0:
                await asyncio.sleep(self._llm_min_interval)

            try:
                response = await asyncio.to_thread(
                    self._client.chat.completions.create,
                    model=self._model,
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
                component.vocal_posture_confidence = parsed.get(
                    "vocal_posture_confidence", 0.7
                )
                component.posture_reasoning = parsed.get("posture_reasoning", "")

                # Heuristic cross-check.
                self._apply_heuristic_adjustment(component, heuristic_posture)

                # Retry on parse failure.
                if component.theme is None or component.vocal_posture is None:
                    await self._retry_llm_call(component, lyrics_text, heuristic_posture)

            except Exception as e:
                logger.warning(f"LLM classification failed for component: {e}")

    def _parse_llm_json(self, text: str) -> dict:
        """Parse LLM JSON response with basic error handling."""
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

        Posture adjustments only (theme has no heuristic cross-check).

        Decisiveness penalty is PER-FIELD:
          - theme_reasoning mentions decisiveness words → theme_confidence only
          - posture_reasoning mentions decisiveness words → posture_confidence only
        """
        if not heuristic_posture or not component.vocal_posture:
            pass  # No adjustment if heuristic is inconclusive or LLM failed.
        elif heuristic_posture == component.vocal_posture:
            component.vocal_posture_confidence = min(
                0.95,
                (component.vocal_posture_confidence or 0.7) + 0.05,
            )
        else:
            if (heuristic_posture == "To God"
                    and component.vocal_posture == "To Congregation"):
                component.vocal_posture_confidence = max(
                    0.0, (component.vocal_posture_confidence or 0.7) - 0.2
                )
                if component.vocal_posture_confidence < 0.6:
                    logger.warning(
                        f"Review flagged: heuristic='To God' but LLM='To Congregation' "
                        f"for occurrence={component.occurrence_index}"
                    )
            elif (heuristic_posture == "About God"
                    and component.vocal_posture == "To God"):
                component.vocal_posture_confidence = max(
                    0.0, (component.vocal_posture_confidence or 0.7) - 0.1
                )
            else:
                component.vocal_posture_confidence = max(
                    0.0, (component.vocal_posture_confidence or 0.7) - 0.2
                )
                if component.vocal_posture_confidence < 0.6:
                    logger.warning(
                        f"Review flagged: heuristic='{heuristic_posture}' "
                        f"but LLM='{component.vocal_posture}' "
                        f"for occurrence={component.occurrence_index}"
                    )

        # Decisiveness penalty: PER-FIELD (not cross-applied).
        if component.theme_reasoning and _DECISIVENESS_PATTERN.search(component.theme_reasoning):
            if component.theme_confidence:
                component.theme_confidence = max(0.0, component.theme_confidence - 0.1)

        if (component.posture_reasoning
                and _DECISIVENESS_PATTERN.search(component.posture_reasoning)):
            if component.vocal_posture_confidence:
                component.vocal_posture_confidence = max(
                    0.0, component.vocal_posture_confidence - 0.1
                )

    async def _retry_llm_call(
        self,
        component: ComponentInstance,
        lyrics_text: str,
        heuristic_posture: Optional[str] = None,
    ) -> None:
        """Retry LLM call on JSON parse failure. Re-runs heuristic cross-check."""
        async with self._llm_semaphore:
            if self._llm_min_interval > 0:
                await asyncio.sleep(self._llm_min_interval)
            try:
                prompt = self._build_prompt(component, lyrics_text)
                response = await asyncio.to_thread(
                    self._client.chat.completions.create,
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    max_tokens=300,
                )
                result = response.choices[0].message.content
                parsed = self._parse_llm_json(result)

                # Parse ALL fields (not just theme/posture).
                component.theme = parsed.get("theme")
                component.theme_confidence = parsed.get("theme_confidence", 0.7)
                component.theme_reasoning = parsed.get("theme_reasoning", "")
                component.vocal_posture = parsed.get("vocal_posture")
                component.vocal_posture_confidence = parsed.get(
                    "vocal_posture_confidence", 0.7
                )
                component.posture_reasoning = parsed.get("posture_reasoning", "")

                # Re-run heuristic cross-check on retry.
                self._apply_heuristic_adjustment(component, heuristic_posture)
            except Exception as e:
                logger.warning(f"LLM retry failed: {e}")

    def _build_prompt(
        self,
        component: ComponentInstance,
        lyrics_text: str,
    ) -> str:
        """Build the LLM prompt for theme + vocal posture classification.

        Uses the existing 12-Chinese-theme system and 3 vocal posture categories.
        """
        themes_str = ", ".join(f'"{t}"' for t in THEME_CATEGORIES)
        postures_str = ", ".join(f'"{p}"' for p in VOCAL_POSTURE_CATEGORIES)

        return f"""Classify the following song component's lyrical theme and vocal posture.

Component type: {component.component_type}
Occurrence: {component.occurrence_index}
Role: {component.role}

Lyrics:
{lyrics_text[:2000]}

## Theme Categories (choose exactly ONE — these are Chinese theme names):
{themes_str}

## Vocal Posture Categories (choose exactly ONE):
{postures_str}

## JSON Response Schema:
{{
  "theme": "one of the theme categories above",
  "theme_confidence": 0.0-1.0,
  "theme_reasoning": "brief explanation",
  "vocal_posture": "one of the posture categories above",
  "vocal_posture_confidence": 0.0-1.0,
  "posture_reasoning": "brief explanation"
}}

## Examples:

Example 1 — Direct address to God with religious pronoun:
Lyrics: "祢是聖潔的，祢配得一切讚美" (You are holy, You deserve all praise)
Response: {{"theme": "讚美", "theme_confidence": 0.95, "theme_reasoning": "Religious pronoun 祢 + praise language", "vocal_posture": "To God", "vocal_posture_confidence": 0.98, "posture_reasoning": "Direct address to God using 祢"}}

Example 2 — Third-person description of God:
Lyrics: "神愛世人，賜下獨生子" (God loved the world, gave His only Son)
Response: {{"theme": "信心", "theme_confidence": 0.85, "theme_reasoning": "Describes God's character and works", "vocal_posture": "About God", "vocal_posture_confidence": 0.95, "posture_reasoning": "Third-person reference to God"}}

Example 3 — Congregational exhortation:
Lyrics: "讓我們歡喜快樂，歸榮耀給神" (Let us rejoice and give glory to God)
Response: {{"theme": "讚美", "theme_confidence": 0.80, "theme_reasoning": "Call to praise together", "vocal_posture": "To Congregation", "vocal_posture_confidence": 0.90, "posture_reasoning": "Imperative plural 讓我們 (let us)"}}

Return ONLY valid JSON matching the schema above. No markdown, no explanation.
"""


def _get_llm_semaphore():
    """Get the shared LLM semaphore (same instance as LRC/embedding jobs).

    Reuses the module-level semaphore from the LLM utilities, ensuring
    consistent throttling across all LLM consumers.
    """
    # Import the same semaphore used by workers/lrc.py.
    # If the module exposes a factory, use it; otherwise create from settings.
    try:
        from .llm_utils import get_llm_semaphore
        return get_llm_semaphore()
    except ImportError:
        # Fallback: create a local semaphore (less ideal — no shared throttling).
        max_concurrent = max(1, settings.SOW_LLM_MAX_CONCURRENT)
        return asyncio.Semaphore(max_concurrent)
```

**Note on `llm_util.py`:** If a shared LLM utility module does not yet exist,
extract the semaphore/retry logic from `workers/lrc.py` into a new
`workers/llm_utils.py` as a prerequisite step. This ensures `ThemeClassifier`,
LRC jobs, and embedding jobs all share the same concurrency budget.

### 3.2 `ops/analysis-service/src/sow_analysis/workers/queue.py`

After component extraction, if `classify_theme` or `classify_vocal_posture` is True,
run the LLM classifier with parallel calls via `asyncio.gather`:

```python
# After extract_components() returns:
if (job.request.options.classify_theme
        or job.request.options.classify_vocal_posture):
    try:
        from .classifier import ThemeClassifier
        classifier = ThemeClassifier()
        components = await classifier.classify_components(
            components,
            lrc_content=request.lrc_content,
        )
    except Exception as e:
        logger.warning(f"LLM classification failed: {e}")
        # Don't fail the job — components are still valid without theme/posture.
```

### 3.3 Theme/Posture Placement (Components, not Songs)

v5 places `theme` and `vocal_posture` on **`song_components` chorus rows**, not on
the `songs` table. This honors the song vs. component distinction:

- **Song-level** metadata (key, BPM) is stable across recordings.
- **Component-level** metadata (theme, posture) is specific to each component.

**Shared values for verbatim choruses:** If a song has multiple chorus occurrences
that are lyrically identical, they receive the same theme and posture values.

**Implication for songset constructor:** Query `song_components` for chorus rows
to get theme/posture metadata. If none populated, fall back to the song-level
12-theme system from the songset constructor's `rules/themes.py`.

### Verification (Phase 3)

```bash
cd ops/analysis-service && uv run --extra dev pytest tests/test_classifier.py -v
```

**Dependencies:** Phase 1 (components module), LLM API key configured.

---

## Phase 4: Analysis Service — Job Integration & Cache

**Goal:** Update job models, queue processing, and cache for v5.

**Complexity:** M

### 4.1 `ops/analysis-service/src/sow_analysis/routes/jobs.py`

The component analysis endpoint already accepts `ComponentAnalysisJobRequest`
which includes `options: ComponentAnalysisOptions`. No route changes needed —
the new options (`snap_to_downbeat`, `energy_aware_roles`, `classify_theme`,
`classify_vocal_posture`) are on `ComponentAnalysisOptions` (Phase 0.5).

### 4.2 `ops/analysis-service/src/sow_analysis/workers/queue.py`

Update `_process_component_analysis_job` to handle v5 options:

```python
async def _process_component_analysis_job(self, job: Job) -> None:
    """Process a component analysis job (v5).

    Flow:
    1. Set status PROCESSING, stage 'downloading'.
    2. Download audio from R2.
    3. extract_components() re-checks local + R2 cache (defense in depth).
    4. If snap_to_downbeat=True and downbeats not provided:
       - Run _detect_downbeats_madmom(audio_path).
    5. Call extract_components() with v5 options.
    6. If classify_theme or classify_vocal_posture:
       - Run ThemeClassifier.classify_components() (parallel via asyncio.gather).
    7. Convert list[ComponentInstance] → list[ComponentResult] (INCLUDING new fields).
    8. Upload to R2, save to local cache.
    9. Build JobResult, set COMPLETED.
    """
```

**CRITICAL: Update ComponentInstance → ComponentResult conversion:**

The current conversion (queue.py:977-991) only maps 12 fields. v5 must map all
new fields:

```python
component_results = [
    ComponentResult(
        component_type=c.component_type,
        occurrence_index=c.occurrence_index,
        role=c.role,
        start_time=c.start_time,
        end_time=c.end_time,
        bpm=c.bpm,
        key=c.key,
        groove_density=c.groove_density,
        backbeat_strength=c.backbeat_strength,
        energy_level=c.energy_level,
        confidence=c.confidence,
        # v5: per-field confidence
        bpm_confidence=c.bpm_confidence,
        key_confidence=c.key_confidence,
        groove_confidence=c.groove_confidence,
        backbeat_confidence=c.backbeat_confidence,
        energy_confidence=c.energy_confidence,
        # v5: LLM theme/posture
        theme=c.theme,
        vocal_posture=c.vocal_posture,
        theme_confidence=c.theme_confidence,
        vocal_posture_confidence=c.vocal_posture_confidence,
        # v5: reasoning
        theme_reasoning=c.theme_reasoning,
        posture_reasoning=c.posture_reasoning,
        source=c.source,
    )
    for c in components
]
```

### 4.3 `ops/analysis-service/src/sow_analysis/storage/r2.py`

No changes needed — `upload_component_result` and `download_component_result`
handle the generic dict payload. The schema_version check is in CacheManager.

### Verification (Phase 4)

```bash
cd ops/analysis-service && uv run --extra dev pytest tests/test_queue.py -v -k component
cd ops/analysis-service && uv run --extra dev pytest tests/integration/test_api.py -v -k component
```

**Dependencies:** Phase 1, Phase 2, Phase 3.

---

## Phase 5: Admin CLI — Persistence & Display (v5)

**Goal:** Update admin CLI to persist and display new v5 fields.

**Complexity:** M

### 5.1 `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

**Update `_parse_component_results`:** Must map new fields from ComponentResult
dicts to SongComponent objects:

```python
def _parse_component_results(
    raw_components: list[dict],
    song_id: str,
    content_hash: str,
) -> list[SongComponent]:
    # ... existing field mapping ...
    # v5: add new fields
    for c in raw_components:
        components.append(
            SongComponent(
                # ... existing fields ...
                bpm_confidence=c.get("bpm_confidence"),
                key_confidence=c.get("key_confidence"),
                groove_confidence=c.get("groove_confidence"),
                backbeat_confidence=c.get("backbeat_confidence"),
                energy_confidence=c.get("energy_confidence"),
                theme=c.get("theme"),
                vocal_posture=c.get("vocal_posture"),
                theme_confidence=c.get("theme_confidence"),
                vocal_posture_confidence=c.get("vocal_posture_confidence"),
                theme_reasoning=c.get("theme_reasoning"),
                posture_reasoning=c.get("posture_reasoning"),
                # ... song_id, content_hash ...
            )
        )
    return components
```

**Update `_submit_component_analysis_job`:** Pass new options:

```python
def _submit_component_analysis_job(
    recording: Recording,
    song_id: str,
    analysis_url: str,
    db_client: DatabaseClient,
    console: Console,
    force: bool = False,
    wait: bool = True,
    # v5 options
    snap_to_downbeat: bool = False,
    energy_aware_roles: bool = False,
    use_stems: bool = False,
    classify_theme: bool = False,
    classify_vocal_posture: bool = False,
) -> Optional[list[SongComponent]]:
```

**Update `_render_components_table`:** Add columns for theme and vocal posture:

```python
table.add_column("BPM_C", justify="right")  # bpm_confidence
table.add_column("Theme")
table.add_column("Posture")
```

### 5.2 Update `components_recording` CLI command

Add CLI flags for v5 options:

```python
@app.command("components")
def components_recording(
    song_id: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force"),
    snap_to_downbeat: bool = typer.Option(False, "--snap-to-downbeat"),
    energy_roles: bool = typer.Option(False, "--energy-roles"),
    use_stems: bool = typer.Option(False, "--use-stems"),
    classify_theme: bool = typer.Option(False, "--classify-theme"),
    classify_posture: bool = typer.Option(False, "--classify-posture"),
    ...
):
```

### Verification (Phase 5)

```bash
uv run --project ops/admin-cli --extra admin sow-admin audio components song_0001 \
    --classify-theme --classify-posture --snap-to-downbeat --energy-roles
uv run --project ops/admin-cli --extra admin sow-admin audio show song_0001
```

**Dependencies:** Phase 0, Phase 4.

---

## Phase 6: Backfill & Migration

**Goal:** Re-run component extraction on existing songs with v5 enhancements.

**Complexity:** S

### 6.1 Backfill strategy

| Option | Description | Cost |
|---|---|---|
| `use_stems` | Cached Demucs stems for feature extraction | +5s/song |
| `snap_to_downbeat` | madmom downbeat detection (only if downbeats missing) | +2-5s/song |
| `energy_aware_roles` | Energy-based entry/exit assignment | +1s/song |
| `classify_theme` | LLM theme classification (12 Chinese themes) | +1-3s/component |
| `classify_vocal_posture` | LLM vocal posture classification | +1-3s/component |

### 6.2 Batch backfill command

```bash
uv run --project ops/admin-cli --extra admin sow-admin audio list --analysis completed --format ids \
  | uv run --project ops/admin-cli --extra admin sow-admin audio components --stdin \
    --use-stems --snap-to-downbeat --energy-roles --classify-theme --classify-posture
```

### 6.3 Migration idempotency

`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` is idempotent (PostgreSQL 9.6+).
Re-running `sow-admin db init` is safe and will add any missing columns.

### Verification (Phase 6)

```bash
psql "$DATABASE_URL" -c "SELECT theme, vocal_posture, theme_confidence FROM song_components LIMIT 5;"
```

**Dependencies:** Phase 5.

---

## 10. Deliverables

### 10a. Processing-Stage Breakdown & Parallelization

```
Stage 0: Download audio from R2
  └── Stage 1A: allin1 section detection (cached from full analysis)
  └── Stage 1B: LRC lyrics parsing
  └── Stage 1C: beat/downbeat detection
        ├── Use existing downbeats if available (from full allin1 analysis)
        └── Else: madmom RNNDownBeatProcessor → DBNDownBeatTrackingProcessor
  └── Stage 1D: stem loading (if use_stems=True)
        │
        ▼
Stage 2: Hybrid component identification (allin1 → lyrics repetition)
        │
        ▼
Stage 3: Feature extraction (stem-aware if available) + per-field confidence
  └── Parallel per component
        │
        ▼
Stage 4: Energy-aware role assignment (if energy_aware_roles=True)
        │
        ▼
Stage 5: LLM theme/posture classification (if classify_theme/Posture=True)
  └── Parallel via asyncio.gather (one LLM call per component, shared semaphore)
        │
        ▼
Stage 6: Cache write + R2 upload (schema_version=2)
```

### 10b. Library & Algorithm Choices

| Component | Library/Algorithm | Justification |
|---|---|---|
| Beat tracking | `librosa` | Fast, reliable for worship music (60–140 BPM). Fallback. |
| Downbeat detection | `madmom` (RNN+DBN) | `RNNDownBeatProcessor` → `DBNDownBeatTrackingProcessor`. Already a core dependency. |
| Section detection | `allin1` | Existing model; provides chorus/verse/bridge labels + downbeats. |
| Lyrics clustering | Custom repetition algorithm | LRC format; repeated line groups identify choruses. |
| Stem separation | Demucs (cached) | Already used in analysis pipeline. |
| Theme/posture LLM | OpenAI-compatible API | Reuses existing SOW_LLM_* config and rate-limiting infrastructure. |
| Heuristic pre-pass | Rule-based (Chinese pronouns) | Zero-cost confidence cross-check for posture. |

### 10c. Input/Output Mapping per Stage → Schema Fields

| Stage | Input | Output | Schema Fields |
|---|---|---|---|
| 1A: allin1 | Audio file | Sections + downbeats | `component_type`, `start_time`, `end_time` |
| 1B: LRC | LRC content | Line groups + timestamps | `component_type` (chorus via repetition) |
| 1C: beats | Audio file | Beat/downbeat timestamps | Edit-point snapping (no column) |
| 1D: stems | Audio file | Stem waveforms | Feature extraction quality |
| 3: features | Component slice + stems | DSP features | `groove_density`, `backbeat_strength`, `energy_level` |
| 3: conf | Duration, stem availability | Confidence scores | 5 per-field confidence columns |
| 4: roles | Energy scores | Role assignments | `role` |
| 5: LLM | Per-component lyrics (LRC time range) | JSON classification | `theme`, `vocal_posture`, confidences, reasoning |

### 10d. Edge-Case & Failure Handling

| Edge Case | Handling |
|---|---|
| No allin1 sections, no LRC | Empty component list; source='none' |
| madmom detection fails | Fall back to beat snapping; log warning |
| Stems not cached | Full-mix features; lower per-field confidence |
| LLM API timeout/failure | Leave theme/posture None; job completes |
| LLM JSON parse fails | Retry once; re-run heuristic cross-check |
| Single chorus | Two rows (entry+exit), same occurrence_index=1 |
| Energy scores identical | Fall back to positional: first=entry, last=exit |
| Empty lyrics for component | Skip LLM call; leave theme/posture None |
| Heuristic + LLM disagree | Confidence penalty; log for review |
| Decisiveness words in reasoning | Per-field −0.1 penalty |

### 10e. Pilot & Validation

**Phase 1: Unit validation**
- Run classifier on 50 manually-labeled chorus samples.
- Target: ≥85% theme accuracy, ≥90% posture accuracy.

**Phase 2: Integration validation**
- Run full v5 pipeline on 10 songs with all options.
- Manual spot-check of theme/posture and per-field confidence.

**Phase 3: Backfill validation**
- Backfill 100 songs. Compare theme distribution against existing 12-theme system.

---

## Summary of Changes by File

| File | Change |
|---|---|
| `ops/admin-cli/.../db/schema.py` | ALTER TABLE for 11 new columns, CHECK constraints, update SELECT column list, COLUMN_COUNT=27 |
| `ops/admin-cli/.../db/models.py` | 11 new fields on SongComponent, update from_row/to_dict |
| `ops/admin-cli/.../db/client.py` | Update upsert INSERT (24 cols, 24 placeholders), update values comprehension |
| `ops/analysis-service/.../storage/cache.py` | Bump COMPONENT_SCHEMA_VERSION to 2 |
| `ops/analysis-service/.../models.py` | 11 new fields on ComponentResult, 4 new options on ComponentAnalysisOptions |
| `ops/analysis-service/.../workers/components.py` | Energy-aware roles, madmom downbeat detection (correct API), stem features, per-field confidence, serialize/deserialize updates |
| `ops/analysis-service/.../workers/classifier.py` | **NEW** — LLM classifier with 12 Chinese themes, 3 postures, reused rate-limiting, per-component lyrics extraction, parallel calls |
| `ops/analysis-service/.../workers/queue.py` | Dispatch v5 options, update ComponentInstance→ComponentResult conversion |
| `ops/admin-cli/.../commands/audio.py` | Update _parse_component_results, _render_components_table, CLI flags |

---

## Dependencies Between Phases

```
Phase 0 (schema/models)
  └── Phase 1 (components.py)
        ├── Phase 2 (madmom)
        ├── Phase 3 (classifier)
        └── Phase 4 (job integration)
              └── Phase 5 (admin CLI)
                    └── Phase 6 (backfill)
```

---

## Testing Strategy

### Unit Tests
- `test_components.py`: Energy-aware roles, stem features, serialize/deserialize round-trip.
- `test_classifier.py`: Heuristic pre-pass, LLM mock, decisiveness penalty, per-component lyrics extraction.
- `test_downbeat.py`: madmom RNNDownBeatProcessor → DBNDownBeatTrackingProcessor pipeline.

### Integration Tests
- `test_queue.py`: Full job pipeline with v5 options.
- `test_api.py`: Endpoint with new options.
- `test_admin_cli.py`: CLI commands with new flags.

### E2E Tests
- Backfill on small set with all v5 options.
- Manual spot-check of theme/posture classifications.

---

## Risk Assessment

| Risk | Mitigation |
|---|---|
| madmom detection slow on long songs | Only run when downbeats not already cached |
| LLM API failures | Retry with existing backoff; leave None on failure |
| Stem cache misses | Fall back to full-mix features |
| Performance regression | v5 options are opt-in; v3 path unchanged |
| LLM rate limit (429) | Shared SOW_LLM_MAX_CONCURRENT semaphore across all LLM consumers |
| Schema migration on existing DBs | ALTER TABLE IF NOT EXISTS (idempotent, safe) |
