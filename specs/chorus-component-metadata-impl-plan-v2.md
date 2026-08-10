# Implementation Plan: Chorus-Based Song Component Metadata (v2)

> **Revision Notes:** This supersedes `specs/chorus-component-metadata-impl-plan.md` (v1).
> Key changes:
>
> 1. **Removed allin1 fallback path.** The hybrid strategy is now strictly 2-tier:
>    cached allin1 sections (Priority 1, 0 s) → lyrics-repetition from LRC (Priority 2, <1 s) → skip gracefully (`([], 'none')`, 0 s).
>    There is no "download audio and run allin1.analyze()" fallback step.
> 2. **No auto-queued LRC sub-job.** When neither cached sections nor LRC exists, the
>    component analysis job returns empty components with `source='none'`. LRC generation
>    is the caller's responsibility (`sow-admin audio lrc`, download pipeline, or render path).
> 3. **Explicit component result caching contract.** Added `CacheManager.get_component_result()` /
>    `save_component_result()`, plus R2 `upload_component_result()` / `download_component_result()`
>    methods. `extract_components()` checks local cache first, then R2, unless `force=True`.
>    Admin CLI similarly reads cached `components.json` from R2 to avoid re-submitting jobs.
>    See the JSON schema documented in Phase 2.4 below.

---

## Hybrid Chorus Identification Strategy

### Problem

The `allin1` library runs Demucs source separation + a structural segmentation neural network.
On a laptop CPU this costs **30–60 minutes per song**. The prompt observes that "chorus can be
easily identified via lyrics repetition," suggesting we may not need allin1 at all.

### Findings

1. **allin1 sections are already cached** for every song with `analysis_status='completed'`.
   The `recordings.sections` column holds the JSON list
   `[{"label","start","end"}]`, and the full `analysis.json` (including `beats`, `downbeats`,
   `sections`) is mirrored to R2 at `{hash_prefix}/analysis.json`. **Extracting components from
   already-analyzed songs costs nothing** — the expensive Demucs run already happened.

2. **LRC is optional** (`lrc_status` tracks `pending`/`processing`/`completed`/`failed`), but
   when present it is a timestamped line list (`LRCLine{time_seconds, text}`). Choruses repeat
   verbatim or near-verbatim across occurrences, making them detectable via repeated-line-group
   clustering in **<1 second** per song.

3. **Per-component audio features** (groove_density, backbeat_strength, energy_level,
   per-segment BPM/key) require librosa on the audio slice regardless of identification method.
   This is fast (~5–15 s/song) and does NOT need allin1 or Demucs.

4. **No existing repetition/chorus detection** exists — only a stub `_detect_sections()`
   returning `unknown`.

### Recommendation: Hybrid (2-tier fallback)

| Priority | Source | Cost | Coverage |
|---|---|---|---|
| 1 | Cached allin1 sections (`recordings.sections` / `analysis.json`) | **0 s** (free) | All `analysis_status='completed'` songs |
| 2 | Lyrics-repetition from LRC lines | **<1 s** | Songs with `lrc_status='completed'` but no full analysis |
| — | Skip gracefully | **0 s** | Songs where none of the above succeed |

**Why not lyrics-only?** It discards reliable, already-computed allin1 structural labels for the
completed-analysis catalog, and fails entirely for the (common) case where LRC is missing. The
hybrid approach maximizes coverage at minimum cost.

**Benefit of hybrid:** Backfill of the entire completed-analysis catalog is instant (tier 1).
New songs with LRC get components in seconds (tier 2). Songs with neither sections nor LRC are
skipped gracefully — the caller is responsible for generating LRC if needed.

### Where Component Extraction Runs

Per-component feature computation (librosa) requires the **audio slice**. Per the architectural
separation rule (Admin CLI never imports ML libraries), all identification AND feature computation
happens in the **Analysis Service** via a new `COMPONENT_ANALYSIS` job type. The Admin CLI
submits the job (passing cached `sections`/`lrc_content` to avoid re-computation), polls for
completion, and persists the returned component rows to `song_components`.

---

## Phase 0: Schema & Models

**Goal:** Create the `song_components` table, `SongComponent` dataclass, column constants,
indexes, trigger, and DB client methods.

**Complexity:** M

**Files to create/modify:**

### 0.1 `ops/admin-cli/src/stream_of_worship/admin/db/schema.py`

Add the following constants:

```python
# song_components table (normalized long-format: one row per component instance)
CREATE_SONG_COMPONENTS_TABLE = """
CREATE TABLE IF NOT EXISTS song_components (
    id SERIAL PRIMARY KEY,
    song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL REFERENCES recordings(content_hash) ON DELETE CASCADE,
    component_type TEXT NOT NULL CHECK (component_type IN
        ('chorus','verse','prechorus','bridge','intro','outro','instrumental')),
    occurrence_index INTEGER NOT NULL DEFAULT 1,
    role TEXT NOT NULL DEFAULT 'none' CHECK (role IN
        ('entry','exit','loop_target','none')),
    start_time REAL,
    end_time REAL,
    bpm REAL,
    key TEXT,
    groove_density REAL,
    backbeat_strength REAL,
    energy_level REAL,
    confidence REAL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""

CREATE_SONG_COMPONENTS_INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS idx_song_components_song_id
    ON song_components(song_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_song_components_content_hash
    ON song_components(content_hash);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_song_components_type_role
    ON song_components(component_type, role);
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_song_components_unique
    ON song_components(song_id, component_type, occurrence_index);
    """,
]

CREATE_SONG_COMPONENTS_UPDATE_TRIGGER = """
DROP TRIGGER IF EXISTS trg_song_components_updated_at ON song_components;
CREATE TRIGGER trg_song_components_updated_at
    BEFORE UPDATE ON song_components
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
"""
```

Append to `ALL_SCHEMA_STATEMENTS` (after `CREATE_RECORDINGS_UPDATE_TRIGGER`, before auth section):

```python
ALL_SCHEMA_STATEMENTS = [
    CREATE_EXTENSION_VECTOR,
    CREATE_SONGS_TABLE,
    CREATE_RECORDINGS_TABLE,
    *CREATE_INDEXES,
    CREATE_SONG_EMBEDDING_TABLE,
    CREATE_SONG_LINE_EMBEDDING_TABLE,
    *CREATE_EMBEDDING_INDEXES,
    CREATE_THEME_ANCHORS_TABLE,
    CREATE_THEME_ANCHORS_INDEX,
    CREATE_UPDATE_TIMESTAMP_FUNCTION,
    CREATE_SONGS_UPDATE_TRIGGER,
    CREATE_RECORDINGS_UPDATE_TRIGGER,
    CREATE_SONG_COMPONENTS_TABLE,                    # NEW
    *CREATE_SONG_COMPONENTS_INDEXES,                 # NEW
    CREATE_SONG_COMPONENTS_UPDATE_TRIGGER,           # NEW
]
```

Add column constants:

```python
SONG_COMPONENT_COLUMNS_SELECT = """
    id, song_id, content_hash, component_type, occurrence_index, role,
    start_time, end_time, bpm, key, groove_density, backbeat_strength,
    energy_level, confidence, created_at, updated_at
"""

SONG_COMPONENT_COLUMN_COUNT = 16
```

### 0.2 `ops/admin-cli/src/stream_of_worship/db/postgres_schema.py`

Re-export the new constants and insert them into `ALL_SCHEMA_STATEMENTS` in the catalog section
(after `CREATE_RECORDINGS_UPDATE_TRIGGER`):

```python
from stream_of_worship.admin.db.schema import (
    ...,
    CREATE_SONG_COMPONENTS_TABLE,
    CREATE_SONG_COMPONENTS_INDEXES,
    CREATE_SONG_COMPONENTS_UPDATE_TRIGGER,
    SONG_COMPONENT_COLUMNS_SELECT,
    SONG_COMPONENT_COLUMN_COUNT,
)

ALL_SCHEMA_STATEMENTS = [
    CREATE_EXTENSION_VECTOR,
    # --- 1. admin / catalog ---
    CREATE_SONGS_TABLE,
    CREATE_RECORDINGS_TABLE,
    *CREATE_INDEXES,
    CREATE_SONG_EMBEDDING_TABLE,
    CREATE_SONG_LINE_EMBEDDING_TABLE,
    *CREATE_EMBEDDING_INDEXES,
    CREATE_THEME_ANCHORS_TABLE,
    CREATE_THEME_ANCHORS_INDEX,
    CREATE_UPDATE_TIMESTAMP_FUNCTION,
    CREATE_SONGS_UPDATE_TRIGGER,
    CREATE_RECORDINGS_UPDATE_TRIGGER,
    CREATE_SONG_COMPONENTS_TABLE,             # NEW
    *CREATE_SONG_COMPONENTS_INDEXES,          # NEW
    CREATE_SONG_COMPONENTS_UPDATE_TRIGGER,    # NEW
    # --- 2. auth ...
```

Add new names to `__all__`.

### 0.3 `ops/admin-cli/src/stream_of_worship/admin/db/models.py`

Add a new `SongComponent` dataclass following the `Recording` pattern:

```python
@dataclass
class SongComponent:
    """One row per detected/tagged component instance.

    Attributes:
        id: Auto-increment PK (None for not-yet-persisted).
        song_id: FK to songs.id.
        content_hash: FK to recordings.content_hash.
        component_type: 'chorus' | 'verse' | 'prechorus' | 'bridge' | ...
        occurrence_index: 1st chorus, 2nd chorus, etc.
        role: 'entry' | 'exit' | 'loop_target' | 'none'.
        start_time: Start time in seconds.
        end_time: End time in seconds.
        bpm: Per-component tempo (may differ from global).
        key: Per-component detected key (e.g., "G").
        groove_density: Onset/note density metric for this segment.
        backbeat_strength: Backbeat (beats 2&4) accent strength.
        energy_level: RMS/energy for this segment.
        confidence: Detection confidence (0.0–1.0).
        created_at: Row creation timestamp.
        updated_at: Row update timestamp.
    """

    id: Optional[int] = None
    song_id: str = ""
    content_hash: str = ""
    component_type: str = ""
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
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

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
            created_at=_to_str(row[14]),
            updated_at=_to_str(row[15]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "song_id": self.song_id,
            "content_hash": self.content_hash,
            "component_type": self.component_type,
            "occurrence_index": self.occurrence_index,
            "role": self.role,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "bpm": self.bpm,
            "key": self.key,
            "groove_density": self.groove_density,
            "backbeat_strength": self.backbeat_strength,
            "energy_level": self.energy_level,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
```

### 0.4 `ops/admin-cli/src/stream_of_worship/admin/db/client.py`

Add the following methods to `DatabaseClient`:

```python
def upsert_song_components(
    self,
    song_id: str,
    content_hash: str,
    components: list[SongComponent],
) -> int:
    """Bulk upsert component rows for a recording.

    Deletes existing rows for (song_id, content_hash) then inserts the
    new set. Uses a single transaction. Returns the number of rows inserted.
    """

def get_song_components(self, song_id: str) -> list[SongComponent]:
    """Return all component rows for a song, ordered by start_time."""

def get_song_components_by_role(
    self, song_id: str, role: str
) -> list[SongComponent]:
    """Return component rows matching a role (e.g., 'entry', 'exit')."""

def get_song_components_by_type(
    self, song_id: str, component_type: str
) -> list[SongComponent]:
    """Return component rows of a given type (e.g., 'chorus')."""
```

Import `SongComponent` from `.models` at the top of the file.

The `upsert_song_components` implementation:

```python
def upsert_song_components(self, song_id, content_hash, components):
    with self.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM song_components WHERE song_id = %s AND content_hash = %s",
            (song_id, content_hash),
        )
        if not components:
            return 0
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
            )
            for c in components
        ]
        cursor.executemany(
            """
            INSERT INTO song_components (
                song_id, content_hash, component_type, occurrence_index,
                role, start_time, end_time, bpm, key, groove_density,
                backbeat_strength, energy_level, confidence
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            values,
        )
        return len(values)
```

### Verification (Phase 0)

```bash
# Idempotent schema creation adds the new table
uv run --project ops/admin-cli --extra admin sow-admin db init

# Confirm table exists
psql "$DATABASE_URL" -c "\d song_components"
```

**Dependencies:** None (first phase).

---

## Phase 1: Analysis Service — Component Extraction Module

**Goal:** Create `workers/components.py` with chorus identification (two strategies:
allin1-section labels and lyrics-repetition clustering) and per-component audio feature
computation.

**Complexity:** L

**Files to create/modify:**

### 1.1 `ops/analysis-service/src/sow_analysis/workers/components.py` (NEW)

This module is the heart of component extraction. It contains:

#### Data structures

```python
@dataclass
class ComponentInstance:
    """An identified song component with computed features."""
    component_type: str          # 'chorus' | 'verse' | ...
    occurrence_index: int       # 1-based
    role: str                   # 'entry' | 'exit' | 'loop_target' | 'none'
    start_time: float
    end_time: float
    bpm: Optional[float] = None
    key: Optional[str] = None
    groove_density: Optional[float] = None
    backbeat_strength: Optional[float] = None
    energy_level: Optional[float] = None
    confidence: Optional[float] = None
    source: str = ""            # 'allin1_sections' | 'lyrics_repetition' | 'none'
```

#### Identification Strategy 1: `identify_from_allin1_sections`

```python
def identify_from_allin1_sections(
    sections: list[dict],
) -> list[ComponentInstance]:
    """Identify chorus/verse components from allin1 section labels.

    allin1 labels: 'intro', 'verse', 'chorus', 'bridge', 'outro', 'instrumental'.

    Rules:
    - All sections labeled 'chorus' → list with occurrence_index 1..N.
    - occurrence_index=1 → role='entry'
    - occurrence_index=N (last) → role='exit'
    - If only 1 chorus → that single instance serves BOTH 'entry' and 'exit'
      (handled at persistence: two rows, same start/end, roles differ).
    - The verse section immediately preceding the first chorus → role='loop_target',
      occurrence_index=1.
    - If no verse before first chorus → skip loop_target.
    """
```

**Edge cases:**
- No 'chorus' label at all → return `[]` (caller falls through or skips).
- Multiple consecutive 'verse' sections before the first chorus → take the last verse section
  before the chorus (closest to it).
- 'prechorus' labels → ignored for this milestone (future rows, no schema migration needed).

#### Identification Strategy 2: `identify_from_lyrics_repetition`

```python
def identify_from_lyrics_repetition(
    lrc_content: str,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
) -> list[ComponentInstance]:
    """Identify chorus via repeated-line-group clustering on LRC lines.

    Algorithm (Repeated-line-group clustering):
    1. Parse LRC into list[LRCLine] (time_seconds, text) using the shared
       LRC parser (reuse admin-cli's parse_lrc logic — port a lightweight
       copy into the analysis service since it cannot import admin-cli).
    2. Normalize each line: strip, lowercase, remove punctuation/whitespace.
       Skip empty lines.
    3. For window sizes w = 2..min(12, N//2):
       a. Slide a window of w consecutive lines across the list.
       b. For each window start i, compute a signature =
          tuple(normalized_texts[i:i+w]).
       c. Group window-start indices by exact-signature match; record groups
          with >= 2 occurrences as candidates.
       d. Also try fuzzy match (rapidfuzz.fuzz.ratio > 85 on joined text) to
          catch minor lyric variations — merge near-duplicate signatures.
    4. Score candidates: score = repeat_count * w (favor many repeats of long blocks).
       Tiebreak: earliest start_time.
    5. Best candidate = chorus. Its occurrence positions give N chorus instances:
       - occurrence 1 → role='entry'
       - occurrence N → role='exit'
       - if N==1 → single instance serves both roles.
       start_time = first_line.time_seconds of each occurrence.
       end_time = next line's time_seconds after the block (or last_line.time_seconds
       + estimated duration).
    6. Verse (loop_target): the lines immediately before the first chorus occurrence.
       Walk backward from the chorus start until a time-gap > threshold (e.g., 3s)
       or the song start. start_time = first verse line time, end_time = chorus start.
       occurrence_index=1.
    7. Snap start_time/end_time to nearest beat if beats provided.
    """
```

**Fuzzy matching note:** `rapidfuzz` is already a dependency (used in `services/canonical_snap.py`).
Import `from rapidfuzz import fuzz`.

**LRC parser note:** The analysis service does not currently have an LRC parser. Add a minimal
`parse_lrc_lines(content: str) -> list[tuple[float, str]]` in this module (regex-based, same
pattern as admin-cli's `parse_lrc`). Keep it minimal — no metadata tag preservation needed for
component detection.

#### Strategy selector

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
) -> tuple[list[ComponentInstance], str]:
    """Extract song components using hybrid strategy.

    Returns (components, source) where source is one of:
    'allin1_sections', 'lyrics_repetition', 'none'.

    Strategy order:
    1. If sections provided & non-empty → identify_from_allin1_sections()
    2. Elif lrc_content provided & non-empty → identify_from_lyrics_repetition()
    3. If all fail → ([], 'none').

    After identification, ALWAYS download audio (if not already) and compute
    per-component features via compute_component_features().
    """
```

#### Per-component feature computation

```python
def compute_component_features(
    y: np.ndarray,
    sr: int,
    component: ComponentInstance,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
) -> ComponentInstance:
    """Compute per-component BPM, key, groove_density, backbeat_strength, energy_level.

    - bpm: re-estimate from beat intervals within [start_time, end_time] using
      librosa.onset.onset_strength + librosa.beat.tempo on the sliced audio.
      Fallback: global BPM if the segment is too short (<8s).
    - key: extract the audio slice y[start:end], call detect_key_segment_vote()
      with the single segment as the window. Reuse analyzer.detect_key_segment_vote.
    - groove_density: mean onset strength envelope within the segment
      (librosa.onset.onset_strength → mean). Normalize to onsets/sec.
    - backbeat_strength: using beats within the segment, compute mean RMS of
      frames at beat positions 2&4 vs 1&3 (within each 4-beat group). Ratio >1
      means beats 2&4 are stronger. Use librosa.feature.rms at beat frames.
    - energy_level: mean RMS (librosa.feature.rms) within the segment.
    - confidence: heuristic — 0.9 if from allin1 labels, 0.7 if from lyrics.
      Adjust per feature extraction success.
    """
```

**Snap-to-beat alignment helper:**

```python
def _snap_to_beat(time_seconds: float, beats: list[float]) -> float:
    """Snap a timestamp to the nearest beat."""
```

### 1.2 `ops/analysis-service/src/sow_analysis/workers/__init__.py`

No change needed — the module is imported lazily in queue.py (like analyzer, lrc).

### Verification (Phase 1)

```bash
cd ops/analysis-service && uv run --extra dev pytest tests/test_components.py -v
```

**Dependencies:** None (can be developed/tested standalone with fixture audio + mock sections/LRC).

---

## Phase 2: Analysis Service — Job Integration

**Goal:** Add the `COMPONENT_ANALYSIS` job type, request/result models, route endpoint,
queue processing, and cache strategy.

**Complexity:** M

**Files to create/modify:**

### 2.1 `ops/analysis-service/src/sow_analysis/models.py`

Add to `JobType` enum:

```python
class JobType(str, Enum):
    ANALYZE = "analyze"
    LRC = "lrc"
    STEM_SEPARATION = "stem_separation"
    EMBEDDING = "embedding"
    FORCED_ALIGNMENT = "forced_alignment"
    FAST_ANALYZE = "fast_analyze"
    COMPONENT_ANALYSIS = "component_analysis"   # NEW
```

Add request models:

```python
class ComponentAnalysisOptions(BaseModel):
    """Options for component analysis jobs."""
    force: bool = False
    use_stems: bool = False  # If True, prefer stems audio (drums/vocals) for feature extraction


class ComponentAnalysisJobRequest(BaseModel):
    """Request to submit a component analysis job.

    The hybrid extraction strategy prefers cached allin1 sections first,
    then lyrics-repetition from LRC.
    Callers SHOULD pass cached `sections`, `beats`, `downbeats`, and
    `lrc_content` from the DB/R2 to avoid re-computation.
    """
    audio_url: str
    content_hash: str
    song_id: str = ""
    sections: Optional[List[Section]] = None      # Cached allin1 sections
    beats: Optional[List[float]] = None            # Cached beat timestamps
    downbeats: Optional[List[float]] = None        # Cached downbeat timestamps
    lrc_content: Optional[str] = None              # Cached LRC text
    options: ComponentAnalysisOptions = Field(default_factory=ComponentAnalysisOptions)
```

Add result model:

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
    source: str = ""   # 'allin1_sections' | 'lyrics_repetition' | 'none'
```

Add to `JobResult`:

```python
class JobResult(BaseModel):
    # ... existing fields ...
    components: Optional[List[ComponentResult]] = None     # NEW
    component_source: Optional[str] = None                  # NEW: identification source
```

Update the `Job` dataclass `request` Union to include `ComponentAnalysisJobRequest`.

### 2.2 `ops/analysis-service/src/sow_analysis/routes/jobs.py`

Add endpoint:

```python
@router.post("/jobs/component-analysis", response_model=JobResponse)
async def submit_component_analysis_job(
    request: ComponentAnalysisJobRequest,
    api_key: str = Depends(verify_api_key),
) -> JobResponse:
    """Submit a component analysis job.

    Identifies chorus/verse components via hybrid strategy (cached allin1
    sections → lyrics repetition) and computes per-component
    audio features (BPM, key, groove density, backbeat strength, energy).
    """
    if job_queue is None:
        raise HTTPException(500, "Job queue not initialized")
    job = await job_queue.submit(JobType.COMPONENT_ANALYSIS, request)
    return job_to_response(job)
```

Update `job_to_response()` to pass the new `components` and `component_source` fields into
`JobResult`:

```python
result = JobResult(
    # ... existing fields ...
    components=job.result.components if job.type == JobType.COMPONENT_ANALYSIS else None,
    component_source=(job.result.component_source
                      if job.type == JobType.COMPONENT_ANALYSIS else None),
)
```

Import the new models at the top.

### 2.3 `ops/analysis-service/src/sow_analysis/workers/queue.py`

Add optional import (alongside existing analyzer import):

```python
try:
    from .components import extract_components, ComponentInstance
except ImportError:
    extract_components = None
    ComponentInstance = None
```

Add semaphore consideration: Component analysis is librosa-only (like fast_analyze). Reuse
`_fast_analyze_semaphore` or add a dedicated `_component_semaphore`.
**Recommendation: reuse `_fast_analyze_semaphore`** since both are librosa-CPU-bound and should
coordinate.

Add dispatch in `_process_job_with_semaphore`:

```python
elif job.type == JobType.COMPONENT_ANALYSIS:
    async with self._fast_analyze_semaphore:
        latest = self._jobs.get(job.id, job)
        if latest.status == JobStatus.CANCELLED:
            return
        await self._process_component_analysis_job(job)
```

Add the processor method:

```python
async def _process_component_analysis_job(self, job: Job) -> None:
    """Process a component analysis job.

    Downloads audio from R2, runs hybrid component extraction, uploads
    results to R2, builds JobResult with components list.
    """
    # Similar structure to _process_fast_analyze_job:
    # 1. Set status PROCESSING, stage 'downloading'
    # 2. Download audio from R2 to temp dir
    # 3. Call extract_components() with cached sections/lrc_content from request
    # 4. Convert list[ComponentInstance] → list[ComponentResult]
    # 5. Upload component results to R2 as {hash_prefix}/components.json
    # 6. Build JobResult(components=..., component_source=...)
    # 7. Set status COMPLETED, stage 'complete'
    # 8. Persist to JobStore
```

### 2.4 Cache strategy

Add to `CacheManager` (`ops/analysis-service/src/sow_analysis/storage/cache.py`):

```python
def get_component_result(self, content_hash: str) -> Optional[dict]:
    """Check if component analysis result exists in local cache.

    Checks for `{hash_prefix}_components.json` in the local cache directory.
    Returns None if not found or if the file is corrupt.
    """
    hash_prefix = self._get_hash_prefix(content_hash)
    cache_file = self.cache_dir / f"{hash_prefix}_components.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except (json.JSONDecodeError, IOError):
            return None
    return None


def save_component_result(self, content_hash: str, result: dict) -> Path:
    """Save component analysis result to local cache.

    Stores as `{hash_prefix}_components.json` in the local cache directory.
    Returns the path to the written file.
    """
    hash_prefix = self._get_hash_prefix(content_hash)
    cache_file = self.cache_dir / f"{hash_prefix}_components.json"
    cache_file.write_text(json.dumps(result, indent=2))
    return cache_file
```

The `extract_components()` function checks cache first (unless `force=True`), mirroring
`analyze_audio`'s pattern.

**JSON schema for `components.json`:**

```json
{
  "content_hash": "abc123...",
  "hash_prefix": "abc",
  "component_source": "allin1_sections",
  "components": [
    {
      "component_type": "chorus",
      "occurrence_index": 1,
      "role": "entry",
      "start_time": 45.2,
      "end_time": 72.1,
      "bpm": 80.0,
      "key": "G",
      "groove_density": 0.45,
      "backbeat_strength": 1.12,
      "energy_level": -18.3,
      "confidence": 0.9
    },
    {
      "component_type": "chorus",
      "occurrence_index": 2,
      "role": "exit",
      "start_time": 140.3,
      "end_time": 167.0,
      "bpm": 80.0,
      "key": "G",
      "groove_density": 0.43,
      "backbeat_strength": 1.08,
      "energy_level": -17.9,
      "confidence": 0.9
    },
    {
      "component_type": "verse",
      "occurrence_index": 1,
      "role": "loop_target",
      "start_time": 30.0,
      "end_time": 45.2,
      "bpm": 80.0,
      "key": "G",
      "groove_density": 0.38,
      "backbeat_strength": 0.95,
      "energy_level": -19.1,
      "confidence": 0.9
    }
  ]
}
```

### 2.5 `ops/analysis-service/src/sow_analysis/storage/r2.py`

Add upload and download methods for component results:

```python
async def upload_component_result(self, hash_prefix: str, result: dict) -> str:
    """Upload components.json to R2 at {hash_prefix}/components.json.

    Returns the R2 object key.
    """
    # Same pattern as upload_analysis_result


async def download_component_result(self, hash_prefix: str) -> Optional[dict]:
    """Download components.json from R2 and return as dict (None if not found).

    Checks existence first, downloads to a temp file, parses JSON, cleans up.
    """
    # Uses check_exists + download_to_temp + json.loads
```

Also add:

```python
async def download_lrc_text(self, hash_prefix: str) -> Optional[str]:
    """Download lyrics.lrc from R2 and return as text (None if not found)."""
    # Uses check_exists + download_to_temp + read_text
```

### Verification (Phase 2)

```bash
# Unit/integration test the new endpoint
cd ops/analysis-service && uv run --extra dev pytest tests/test_queue.py -v -k component
cd ops/analysis-service && uv run --extra dev pytest tests/integration/test_api.py -v -k component
```

**Dependencies:** Phase 1 (components module).

---

## Phase 3: Admin CLI — Persistence & Display

**Goal:** Submit component analysis jobs, persist results to `song_components`, display component
metadata in the CLI.

**Complexity:** M

**Files to create/modify:**

### 3.1 `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`

Add to `AnalysisClient`:

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
) -> JobInfo:
    """Submit a component analysis job to the analysis service.

    Passes cached sections/beats/lrc_content so the service can use the
    hybrid strategy without re-computation.

    If a cached components.json exists in R2 at {hash_prefix}/components.json,
    this method returns the cached result directly (unless force=True).
    """
```

Admin CLI should check R2 for cached `components.json` before submitting:

```python
def get_cached_component_result(self, hash_prefix: str) -> Optional[dict]:
    """Check if a component result is already cached in R2.

    Returns the parsed components.json from {hash_prefix}/components.json,
    or None if not found. Used to avoid re-submitting jobs.
    """
```

Payload structure:

```python
payload = {
    "audio_url": audio_url,
    "content_hash": content_hash,
    "song_id": song_id,
    "sections": sections,
    "beats": beats,
    "downbeats": downbeats,
    "lrc_content": lrc_content,
    "options": {"force": force},
}
```

POST to `/api/v1/jobs/component-analysis`.

Update `_parse_job_response()` to parse the new `components` and `component_source` fields into
`AnalysisResult`. Add fields to the `AnalysisResult` dataclass:

```python
@dataclass
class AnalysisResult:
    # ... existing fields ...
    components: Optional[List[Dict[str, Any]]] = None      # NEW
    component_source: Optional[str] = None                  # NEW
```

### 3.2 `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

Add a new submission helper:

```python
def _submit_component_analysis_job(
    recording: Recording,
    song_id: str,
    analysis_url: str,
    db_client: DatabaseClient,
    console: Console,
    force: bool = False,
    wait: bool = True,
) -> Optional[list[SongComponent]]:
    """Submit component analysis, wait for completion, persist results.

    Gathers cached sections/beats/downbeats from the recording row,
    fetches LRC content from R2 (if lrc_status='completed'),
    submits the job, polls, and persists component rows via
    db_client.upsert_song_components().

    Before submitting, checks R2 for cached {hash_prefix}/components.json.
    If found and not force, returns the cached result directly.

    Returns the persisted SongComponent list, or None on failure.
    """
```

This helper:
1. Parses `recording.sections` (JSON) → list[dict] (if `recording.has_full_analysis`).
2. Parses `recording.beats` / `recording.downbeats` (JSON) → list[float].
3. Downloads LRC content from R2 via `R2Client.download_lrc_content(hash_prefix)` if
   `recording.has_lrc`.
4. Checks R2 for cached `components.json` at `{hash_prefix}/components.json` (unless `force`).
5. Calls `client.submit_component_analysis(...)` with all cached data.
6. Polls via `client.wait_for_completion(job_id)`.
7. Converts the result's `components` list to `list[SongComponent]` and calls
   `db_client.upsert_song_components(song_id, content_hash, components)`.
8. Returns the components.

### 3.3 New CLI command: `sow-admin audio components`

Add to `audio.py`:

```python
@app.command("components")
def components_recording(
    song_id: str = typer.Argument(..., help="Song ID to analyze components for"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-extraction"),
    no_wait: bool = typer.Option(False, "--no-wait", help="Submit without waiting"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Extract and display chorus/verse component metadata for a song.

    Submits a component analysis job to the analysis service (using cached
    allin1 sections or LRC lyrics repetition), then displays the results
    in a Rich table: component type, occurrence, role, start-end time,
    BPM, key, groove, backbeat, energy, confidence.

    If a cached components.json already exists in R2, returns it directly
    (unless --force is specified).
    """
```

Display a Rich table:

```
┌────────────┬──────┬─────────────┬───────────┬───────┬─────┬────────┬──────────┬────────┬────────────┐
│ Type       │ Occ. │ Role        │ Start-End │ BPM   │ Key │ Groove │ Backbeat │ Energy │ Confidence │
├────────────┼──────┼─────────────┼───────────┼───────┼─────┼────────┼──────────┼────────┼────────────┤
│ chorus     │ 1    │ entry       │ 45.2-72.1 │ 80.0  │ G   │ 0.45   │ 1.12     │ -18.3  │ 0.90       │
│ chorus     │ 2    │ exit        │ 140.3-167 │ 80.0  │ G   │ 0.43   │ 1.08     │ -17.9  │ 0.90       │
│ verse      │ 1    │ loop_target │ 30.0-45.2 │ 80.0  │ G   │ 0.38   │ 0.95     │ -19.1  │ 0.90       │
└────────────┴──────┴─────────────┴───────────┴───────┴─────┴────────┴──────────┴────────┴────────────┘
```

### 3.4 Extend `download` command

Add a `--components` flag to the `download_audio` command (alongside `--analyze` / `--lrc` / `--all`):

```python
components: bool = typer.Option(
    False, "--components", help="Submit for component analysis after download"
),
# Update --all to include components:
if all:
    analyze = True
    lrc = True
    components = True
```

### 3.5 Extend `show` command

In `show_recording()`, after the analysis status display, query and display component rows:

```python
components = db_client.get_song_components(song_id)
if components:
    console.print("")
    console.print("[cyan]Components:[/cyan]")
    # Render Rich table (same as the components command)
elif recording.has_full_analysis or recording.has_lrc:
    console.print("")
    console.print("[yellow]Components: not yet extracted (run 'sow-admin audio components {song_id}')[/yellow]")
```

### 3.6 Extend `analyze` command (optional chaining)

After a successful full analysis (`analyze` command with `--wait`), optionally trigger component
analysis since the allin1 sections are now freshly cached. Add a `--components` flag to the
`analyze` command.

### Verification (Phase 3)

```bash
# Submit and wait for a single song
uv run --project ops/admin-cli --extra admin sow-admin audio components song_0001

# Show displays components
uv run --project ops/admin-cli --extra admin sow-admin audio show song_0001
```

**Dependencies:** Phase 0 (schema/client), Phase 2 (job endpoint).

---

## Phase 4: Backfill & Migration

**Goal:** Re-run component extraction on existing analyzed songs using cached data
(no re-download, no allin1 re-run).

**Complexity:** S

### 4.1 Batch backfill command

Add a `--stdin` mode to the `components` command (following the `lrc`/`analyze` batch pattern):

```bash
# Backfill all songs with completed analysis (instant — uses cached sections)
uv run --project ops/admin-cli --extra admin sow-admin audio list --analysis completed --format ids \
  | uv run --project ops/admin-cli --extra admin sow-admin audio components --stdin
```

The `--stdin` mode:
1. Reads song IDs from stdin (one per line).
2. For each: looks up the recording, gathers cached sections/beats/lrc_content.
3. Submits a component analysis job.
4. Polls for completion (with batch concurrency control — submit N jobs, then poll).
5. Persists results to `song_components`.

### 4.2 Backfill strategy tiers

| Song state | Backfill path | Cost |
|---|---|---|
| `analysis_status='completed'` | Pass cached `sections`/`beats`/`downbeats` in the job request | **0 s** identification + ~10 s features |
| `lrc_status='completed'` only | Pass `lrc_content` in the job request | **<1 s** identification + ~10 s features |
| Neither sections nor LRC | **Skip / return empty; rely on caller to generate LRC separately** | **0 s** |

**Recommendation:** For batch backfill, filter to `analysis_status='completed'` first (fastest,
highest coverage). Then process `lrc_status='completed'` songs. Skip songs with neither —
left without components (transition logic falls back to global features). The caller
(e.g., `sow-admin audio lrc`, download pipeline, or render path) is responsible for generating
LRC when needed.

### 4.3 Migration idempotency

All DDL uses `CREATE ... IF NOT EXISTS`, so running `sow-admin db init` adds the
`song_components` table to existing databases without data loss. The `upsert_song_components`
method uses DELETE+INSERT (not ON CONFLICT) so re-running backfill replaces stale component
rows cleanly.

### Verification (Phase 4)

```bash
# Backfill completed-analysis songs
uv run --project ops/admin-cli --extra admin sow-admin audio list --analysis completed --format ids \
  | uv run --project ops/admin-cli --extra admin sow-admin audio components --stdin

# Verify rows in DB
psql "$DATABASE_URL" -c "SELECT song_id, component_type, role, start_time, key FROM song_components ORDER BY song_id, start_time LIMIT 20;"
```

**Dependencies:** Phase 3 (CLI + persistence).

---

## Phase 5: Testing Strategy

**Goal:** Comprehensive unit, integration, and end-to-end tests.

**Complexity:** M

### 5.1 Analysis service tests

**`ops/analysis-service/tests/test_components.py` (NEW)**

| Test | Description |
|---|---|
| `test_identify_from_allin1_sections_basic` | 2 choruses + 1 verse before → entry, exit, loop_target |
| `test_identify_from_allin1_single_chorus` | 1 chorus → serves both entry and exit roles |
| `test_identify_from_allin1_no_chorus` | Sections with no 'chorus' label → returns `[]` |
| `test_identify_from_allin1_no_verse_before_chorus` | Chorus at index 0 → no loop_target |
| `test_identify_from_lyrics_repetition_basic` | LRC with 2 identical chorus blocks → entry + exit |
| `test_identify_from_lyrics_repetition_fuzzy` | Near-verbatim chorus (1 char diff) → still detected via rapidfuzz |
| `test_identify_from_lyrics_repetition_no_repeat` | No repeated blocks → returns `[]` |
| `test_identify_from_lyrics_repetition_verse_before_chorus` | Verse lines before first chorus → loop_target with correct boundaries |
| `test_compute_component_features` | Slice audio fixture → BPM, key, groove, backbeat, energy computed |
| `test_snap_to_beat` | Timestamp snaps to nearest beat in list |
| `test_extract_components_hybrid_priority` | sections provided → uses allin1 path (not lyrics) |
| `test_extract_components_lyrics_fallback` | No sections, lrc_content provided → lyrics path |
| `test_extract_components_skip_no_data` | No sections, no lrc → returns `([], 'none')` |
| `test_extract_components_cache_hit` | Cached components.json in local cache → returns cached result immediately |
| `test_extract_components_cache_miss` | No local cache, cached components.json in R2 → downloads and returns |
| `test_extract_components_force_bypasses_cache` | force=True → skips cache, recomputes |
| `test_extract_components_cache_save` | After extraction, result saved to local cache and R2 |

Use `numpy` synthetic audio (sine waves) for feature computation tests. Mock R2 client.

### 5.2 Schema tests

**`ops/admin-cli/tests/admin/test_schema.py` (extend or new)**

| Test | Description |
|---|---|
| `test_song_components_table_in_all_schema_statements` | `CREATE_SONG_COMPONENTS_TABLE` is in the list, positioned after recordings |
| `test_song_components_ddl_idempotent` | Running DDL twice doesn't error (IF NOT EXISTS) |
| `test_song_components_indexes_present` | All 4 indexes in `CREATE_SONG_COMPONENTS_INDEXES` |
| `test_song_components_trigger_present` | Update trigger created |

### 5.3 DB client tests

**`ops/admin-cli/tests/admin/test_client_components.py` (NEW)**

| Test | Description |
|---|---|
| `test_upsert_song_components_insert` | Insert 3 rows → 3 rows in DB |
| `test_upsert_song_components_replace` | Insert, then upsert with 2 rows → old rows deleted, 2 new |
| `test_get_song_components` | Round-trip: insert → query → verify from_row |
| `test_get_song_components_by_role` | Filter by role='entry' |
| `test_get_song_components_by_type` | Filter by component_type='chorus' |
| `test_upsert_cascade_delete` | Delete recording → song_components rows cascade-deleted |

Uses testcontainers (PostgreSQL) following existing test patterns. These tests are in the
integration-excluded set (run with `--extra test`).

### 5.4 CLI tests

**`ops/admin-cli/tests/admin/test_audio_components.py` (NEW)**

| Test | Description |
|---|---|
| `test_components_command_displays_table` | Mock analysis client → command renders Rich table |
| `test_components_command_no_data` | Song with no analysis/LRC → graceful message |
| `test_show_command_displays_components` | `show` renders component table when rows exist |
| `test_download_with_components_flag` | `download --components` chains after download |
| `test_components_command_uses_cached_r2_result` | Cached components.json in R2 → skips job submission |
| `test_components_command_force_re_submits` | --force flag → re-submits even when cache exists |

### 5.5 Integration test

**`ops/analysis-service/tests/integration/test_component_job.py` (NEW)**

End-to-end: submit component analysis job → poll → verify result has components list. Uses
mocked R2 (audio fixture), cached sections in request.

### Verification (Phase 5)

```bash
cd ops/analysis-service && uv run --extra dev pytest tests/test_components.py -v
cd ops/analysis-service && uv run --extra dev pytest tests/integration/test_component_job.py -v
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest -v -k "components or song_components"
```

**Dependencies:** Phases 0–3.

---

## Cross-Cutting Concerns

### Backward compatibility

Existing recordings with analysis results but no component data work fine —
`get_song_components()` returns `[]`, and transition logic falls back to global (whole-song)
features. Component extraction is entirely optional and deferred.

### Performance estimates

| Operation | Time |
|---|---|
| Component identification from cached sections | <0.1 s |
| Component identification from LRC lyrics repetition | <1 s |
| Per-component feature computation (librosa, 3 components) | ~5–15 s |
| Audio download from R2 | ~2–5 s |
| Component result cache hit (local) | <0.01 s |
| Component result cache hit (R2 download) | ~0.5–1 s |

Total for a typical backfilled song (tier 1): **~10–20 s** (mostly feature extraction + optional
audio download). For LRC-only songs (tier 2): **~10–20 s**. Songs with neither sections nor LRC
are skipped.

### Source confidence heuristic

| Source | Confidence |
|---|---|
| `allin1_sections` | 0.9 |
| `lyrics_repetition` | 0.7 |
| `none` | 0.0 |

### allin1 label reliability

allin1 section labels are ML-predicted and may mislabel a chorus as 'verse'. Fallback strategy:
1. If allin1 sections exist but contain NO 'chorus' label → fall through to lyrics-repetition
   (if LRC available).
2. If lyrics-repetition also fails (no repeated blocks) → return empty components
   (`source='none'`). The song uses global features.

### Stems usage (optional future)

The `ComponentAnalysisOptions.use_stems` flag (default False) allows the feature extractor to
use the drums stem for backbeat_strength and the vocals stem for groove_density — yielding more
accurate per-component metrics. This is deferred for the first milestone; the plan's
`compute_component_features` works on the full mix by default.

---

## Appendix: File-by-File Change List

### Created files

| File | Description |
|---|---|
| `ops/analysis-service/src/sow_analysis/workers/components.py` | Component extraction: allin1-section identification, lyrics-repetition clustering, per-component feature computation |
| `ops/analysis-service/tests/test_components.py` | Unit tests for identification algorithms, feature computation, and cache behavior |
| `ops/analysis-service/tests/integration/test_component_job.py` | Integration test for the component analysis job pipeline |
| `ops/admin-cli/tests/admin/test_client_components.py` | DB client tests for upsert/get song_components |
| `ops/admin-cli/tests/admin/test_audio_components.py` | CLI tests for `components` command, `show` enhancement, and cache behavior |

### Modified files

| File | Description |
|---|---|
| `ops/admin-cli/src/stream_of_worship/admin/db/schema.py` | Add `CREATE_SONG_COMPONENTS_TABLE`, indexes, trigger, column constants; append to `ALL_SCHEMA_STATEMENTS` |
| `ops/admin-cli/src/stream_of_worship/db/postgres_schema.py` | Re-export new schema constants; insert into unified `ALL_SCHEMA_STATEMENTS`; add to `__all__` |
| `ops/admin-cli/src/stream_of_worship/admin/db/models.py` | Add `SongComponent` dataclass with `from_row()` / `to_dict()` |
| `ops/admin-cli/src/stream_of_worship/admin/db/client.py` | Add `upsert_song_components()`, `get_song_components()`, `get_song_components_by_role()`, `get_song_components_by_type()` |
| `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py` | Add `submit_component_analysis()` to `AnalysisClient`; add `components`/`component_source` to `AnalysisResult`; update `_parse_job_response()`; add `get_cached_component_result()` for R2 cache check |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | Add `components` command; add `_submit_component_analysis_job()` helper; extend `show` with component table; extend `download` with `--components` flag; extend `analyze` with `--components` chaining |
| `ops/analysis-service/src/sow_analysis/models.py` | Add `COMPONENT_ANALYSIS` to `JobType`; add `ComponentAnalysisOptions`, `ComponentAnalysisJobRequest`, `ComponentResult` models; add `components`/`component_source` to `JobResult`; update `Job.request` Union |
| `ops/analysis-service/src/sow_analysis/routes/jobs.py` | Add `POST /jobs/component-analysis` endpoint; update `job_to_response()` for new result fields |
| `ops/analysis-service/src/sow_analysis/workers/queue.py` | Add `COMPONENT_ANALYSIS` dispatch in semaphore block; add `_process_component_analysis_job()`; add optional import of `components` module |
| `ops/analysis-service/src/sow_analysis/storage/cache.py` | Add `get_component_result()` / `save_component_result()` methods |
| `ops/analysis-service/src/sow_analysis/storage/r2.py` | Add `upload_component_result()` and `download_component_result()` methods; add `download_lrc_text()` method |

(End of file)
