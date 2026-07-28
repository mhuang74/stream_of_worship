# Spec: `sow-admin songset construct` v3 — Bandwidth-Optimized

**Supersedes:** `specs/songset_construct_command_v2.md`
**Related:** `specs/reduce-database-network-transfer-v3.md` (Phases 1–2), `reports/database-bandwidth-analysis-song-constructor-ds4.md`
**Status:** Implementation Plan (Not Yet Implemented)

## What Changed From v2

v2 was drafted before the bandwidth analysis report (`reports/database-bandwidth-analysis-song-constructor-ds4.md`) identified the songset constructor as the **dominant contributor** to the 5.5 GB database network transfer problem (~95%+). v2's `db.py` adaptation was a thin import-swap of the POC's `fetch_catalog_pool`, which would have carried the exact same bandwidth pathology into production.

v3 incorporates the bandwidth optimizations **directly into the production subpackage**, rather than porting the POC's problematic query patterns and fixing them later. Since v3 is a production refactor (not a relocation), the SQL queries, data model, and enrichment flow are restructured at the source.

### Delta Summary

| v2 | v3 |
|---|---|
| `POOL_QUERY` fetches `se.embedding::text` (~16.5 KB/row) | `POOL_QUERY` computes cosine similarity in SQL via `theme_anchors` table; returns 12 scalar scores (~200 bytes/row as JSON) |
| `LINE_EMBEDDING_QUERY` fetches all line `embedding::text` (~64.5 MB/run) | `LINE_THEME_QUERY` computes `MAX(cosine)` per theme per song in SQL; returns 12 scores/song as JSON (~200 bytes/song) |
| Pool query uses `SONG_COLUMNS_FOR_JOIN` (24 cols) + `RECORDING_COLUMNS_FOR_JOIN` (34 cols) | Constructor-specific column projection: 9 song + 6 recording columns (only fields the constructor uses) |
| No pool caching | Local cache layer in `cache.py`; subsequent runs with same `(pool_limit, album_series)` skip DB entirely |
| `SongCandidate` carries `song_embedding: list[float]` + `line_embeddings: list[list[float]]` | `SongCandidate` carries `song_theme_scores_raw: dict[str,float]` + `line_theme_scores_raw: dict[str,float]` (pre-normalization cosine scores from SQL) |
| `enrich_pool` calls `classify_embedding_themes` (numpy cosine in Python) | `enrich_pool` calls `normalise_cosine_scores` directly on pre-computed SQL scores; `classify_embedding_themes` and `load_theme_anchors` no longer called |
| CLI: `[-p, --pool]` only | CLI adds `--no-cache`, `--cache-dir`, `--cache-ttl` |
| No `theme_anchors` table | New `theme_anchors` table in schema + `sow-admin theme-anchors sync` command |
| Per-run transfer: ~68.8 MB | Per-run transfer: ~340 KB (cache miss) / ~0 KB (cache hit) |

### Bandwidth Projections

| Scenario | v2 (per run) | v3 (cache miss) | v3 (cache hit) |
|---|---:|---:|---:|
| Pool query (200 songs) | 4.3 MB | ~300 KB | 0 KB |
| Line embedding query (200 songs × 20 lines) | 64.5 MB | ~42 KB | 0 KB |
| **Total per run** | **68.8 MB** | **~342 KB** | **0 KB** |
| 80 runs | 5.4 GB | 27 MB | 27 MB (first run only) |
| 100 runs | 6.7 GB | 34 MB | 34 MB (first run only) |

**99.5% reduction on cache miss; 100% reduction on cache hit.**

## Goal

Integrate the songset-constructor POC (`lab/poc-scripts/poc/songset_constructor/**`) into the Admin CLI as a lazily-loaded subpackage (`stream_of_worship.admin.songset_constructor`), exposed via `sow-admin songset construct`. The command persists constructed songsets to a designated user, gated by `--dry-run`, `--yes`, and an optional `--report` flag.

**Critically, this version eliminates the POC's database bandwidth pathology by computing theme classification in PostgreSQL (via pgvector's `<=>` operator) rather than transferring raw 1536-dim embedding vectors over the wire.**

This is a **production refactor**, not a pure relocation. The CLI surface, default behavior, DB queries, data model, and enrichment flow are intentionally changed.

## Architecture Decisions

### 1. Admin-CLI Subpackage (Same as v2)

```
ops/admin-cli/src/stream_of_worship/admin/songset_constructor/
  __init__.py
  config.py
  models.py
  db.py
  cache.py              # NEW: pool cache layer
  graph/
  rules/
  artifacts/
  data/
    theme_anchors.json  # shipped for initial DB population
  runner.py
  persist.py
  diagnose.py
  report_writer.py
```

**Why a subpackage?** Same rationale as v2: eliminates circular dependency, no file move from `lab/`, lazy imports.

### 2. In-DB Theme Classification (NEW in v3)

Instead of fetching 1536-dim embedding vectors as `::text` (~16.5 KB each) and computing cosine similarity in Python with numpy, the v3 pool query uses pgvector's `<=>` (cosine distance) operator to compute cosine similarity **directly in SQL** against 12 theme anchors stored in a `theme_anchors` table.

**Song-level scores:** A correlated subquery returns `json_object_agg(theme, 1 - (se.embedding <=> ta.embedding))` — 12 float scores as a JSON object (~200 bytes), replacing the 16.5 KB `::text` vector.

**Line-level scores:** A separate query computes `MAX(1 - (sle.embedding <=> ta.embedding))` grouped by `(song_id, theme)`, returning 12 aggregated scores per song as JSON (~200 bytes per song), replacing the 64.5 MB raw line embedding transfer.

### 3. Reduced Column Projection (NEW in v3)

The POC's `POOL_QUERY` uses `SONG_COLUMNS_FOR_JOIN` (24 columns) and `RECORDING_COLUMNS_FOR_JOIN` (34 columns). The constructor only uses 9 song columns and 6 recording columns. v3 defines constructor-specific column lists.

| Source | POC columns | v3 columns | Fields used |
|---|---:|---:|---|
| Songs | 24 | 9 | `id, title, title_pinyin, composer, lyricist, album_name, album_series, musical_key, lyrics_raw` |
| Recordings | 34 | 6 | `hash_prefix, tempo_bpm, musical_key, musical_mode, key_confidence, loudness_db` |

### 4. Pool Caching (NEW in v3)

A local cache layer (`cache.py`) stores the raw pool (post-`fetch_catalog_pool`, pre-`enrich_pool`) to a JSON file. Subsequent runs with the same `(pool_limit, album_series)` filter skip both DB queries entirely.

**Cache key:** SHA-256 hash of `f"{pool_limit}:{sorted(album_series or ['*'])}"`.

**Cache file:** `{cache_dir}/pool_{hash}.json` (default: `~/.cache/sow/songset_constructor/`).

**TTL:** Default 24 hours. Configurable via `--cache-ttl HOURS`. Use `--no-cache` to bypass.

**Why cache the raw pool (pre-enrichment)?** The enrichment step depends on `config.season` (seasonal bias). If someone runs with `--season advent` and then `--season lent` with the same pool filter, the cache should return the raw pool so enrichment re-runs with the different season. Enrichment is pure CPU (keyword matching + normalization + fusion + phase inference), so re-running it is cheap.

## Non-Goals

- Modifying the core rules/graph scoring logic — beam search, fitness, harmony, transitions remain identical.
- ML/heavy analysis (Demucs, allin1) — Admin CLI still refuses to import these.
- Video/audio rendering of constructed songsets.
- Surfaces other than Typer CLI.
- Preserving the `lab/poc-scripts/construct_songset_agent.py` entrypoint or the POC CLI surface.
- Fetching raw embedding vectors at any point (the `::text` cast pattern is permanently eliminated).

## DB Schema Changes

### New Table: `theme_anchors`

```sql
CREATE TABLE IF NOT EXISTS theme_anchors (
    theme         TEXT PRIMARY KEY,
    embedding     vector(1536) NOT NULL,
    model_version TEXT NOT NULL DEFAULT 'text-embedding-3-small',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
```

**Location in schema:** Add `CREATE_THEME_ANCHORS_TABLE` to `ALL_SCHEMA_STATEMENTS` in `ops/admin-cli/src/stream_of_worship/admin/db/schema.py`.

**Drizzle migration:** `delivery/webapp/drizzle/0018_theme_anchors.sql` (for webapp schema tracking).

### Populate: `sow-admin theme-anchors sync`

New Typer command that reads `songset_constructor/data/theme_anchors.json` and upserts all 12 anchor vectors into the `theme_anchors` table.

```
sow-admin theme-anchors sync [--force]
```

- `--force`: re-inserts even if row count is already 12 (useful when anchors are updated).
- Without `--force`: skips if 12 rows already exist with matching `model_version`.
- Reads from the bundled `data/theme_anchors.json` in the subpackage.

### Startup Validation

The `construct` command verifies `theme_anchors` has 12 rows before proceeding. If not, prints:

```
[red]theme_anchors table has N rows (expected 12). Run: sow-admin theme-anchors sync[/red]
```

and exits 1.

## CLI Surface (Revised)

```
sow-admin songset construct \
  --user alice@example.com \
  [-n, --count 2..5]                (default 3)
  [-k, --proposals 1..20]           (default 3)
  [-p, --pool >=4]                  (default 200)
  [--album-series "敬拜讚美 (1)" ...]
  [--include-cpw / --no-include-cpw]    (default False)
  [--intimate / --no-intimate]          (default False)
  [--hymnal-mode / --no-hymnal-mode]    (default False)
  [--season advent|christmas|lent|easter|pentecost]
  [--llm / --no-llm]                   (default --no-llm)
  [--llm-judge / --no-llm-judge]       (default --no-llm-judge)
  [--llm-model NAME]
  [--relax h2:90,h3:80,h4,h5:3]        (optional; see Relax Syntax)
  [--constraints-file PATH]            (YAML/JSON override for --relax)
  [--report]                           (default False; writes diagnose_report.md)
  [--report-dir PATH]                  (default ./output/songset_constructor/<UTC stamp>/)
  [--dry-run]                          (default False; skips DB writes, still writes report if --report)
  [--yes]                              (default False; auto-saves without prompting)
  [--no-cache]                         (default False; bypasses pool cache)
  [--cache-dir PATH]                   (default ~/.cache/sow/songset_constructor/)
  [--cache-ttl HOURS]                  (default 24; pool cache TTL in hours)
  [-c, --config PATH]                  (existing; for AdminConfig)
```

### New CLI Command: `sow-admin theme-anchors sync`

```
sow-admin theme-anchors sync [--force]
```

Reads the bundled `theme_anchors.json` and upserts into the `theme_anchors` table. Required once before the first `construct` run.

### Mutually Exclusive / Validation Rules

- `--dry-run` + `--yes`: allowed; `--yes` is simply moot.
- `--no-llm` + `--llm-judge`: error at `RunConfig.validate_environment()`.
- `--report` without `--report-dir`: uses cwd-relative default.
- `--report-dir` without `--report`: allowed but has no effect.
- `--cache-ttl 0`: disables cache (same as `--no-cache`).
- `--no-cache` + `--cache-dir`: allowed; `--cache-dir` has no effect when cache is disabled.

### Removed from POC (Same as v2)

- `--songs` → `--count` (`-n`)
- `--top-k` → `--proposals` (`-k`)
- `--pool-limit` → `--pool` (`-p`)
- `--output-dir` → `--report-dir` (only active with `--report`)
- `--diagnose-report` → `--report`
- `--interactive-review`, `--resume-thread-id`, `--only-evaluate-pool-enrichment`
- `--env-file` (admin-cli uses `AdminConfig` / shell env)
- All individual `--relax-hN*` flags → `--relax`

### Relax Syntax (Same as v2)

`--relax` accepts a comma-separated list of `key[:value]` tokens:

| Token | Maps to |
|-------|---------|
| `h1` | `relax_h1 = True` |
| `h2:90` | `relax_h2_bpm = 90` (also implies `relax_h1` if auto-relax logic requires) |
| `h3` | `relax_h3_bpm = <default>` |
| `h3:85` | `relax_h3_bpm = 85` |
| `h4` | `relax_h4 = True` |
| `h4:40` | `relax_h4_bpm = 40` |
| `h5` | `relax_h5 = True` |
| `h5:3` | `relax_h5_cfd = 3` |

`--constraints-file` is a YAML/JSON dict with the same keys. It is merged with `--relax`; explicit flags win.

Required: `--user <email>`. Missing → error exit 2.

## Dependency Changes

### Admin CLI: new `constructor` extra

`ops/admin-cli/pyproject.toml`:

```toml
[project.optional-dependencies]
admin = [
    # ... existing deps ...
    "numpy>=1.24.0",
]
constructor = [
    "langgraph>=0.2.50",
    "langchain-core>=0.3.0",
    "langchain-openai>=0.2.0",
    "numpy>=1.26.0",
    "pydantic>=2.7.0",
    "python-dotenv>=1.2.2",
    "rapidfuzz>=3.0.0",
]
test = [
    # ... existing test deps ...
]
```

Notes:
- `langgraph-checkpoint-sqlite` is **omitted** — `InMemorySaver` is used exclusively.
- `typer` and `rich` are already in the `admin` extra.
- `psycopg` comes via `stream-of-worship[postgres]`.
- No circular dependencies.
- `numpy` is still needed in `constructor` for `normalise_cosine_scores` and phase inference, but NOT for vector cosine (moved to SQL).

### Lazy-import guard (Same as v2)

In `commands/songset.py`:

```python
def _import_constructor():
    try:
        from stream_of_worship.admin.songset_constructor.config import RunConfig
        from stream_of_worship.admin.songset_constructor.graph.builder import build_graph
        from stream_of_worship.admin.songset_constructor.db import fetch_catalog_pool
        # ... other imports ...
    except ImportError as exc:
        raise RuntimeError(
            "constructor extra not installed. Run: "
            "`uv sync --extra admin --extra constructor`"
        ) from exc
```

## Module Layout

```
ops/admin-cli/src/stream_of_worship/admin/
  commands/
    songset.py                 # extended with 'construct' subcommand (thin wrapper)
    theme_anchors.py           # NEW: 'theme-anchors sync' subcommand
  songset_constructor/         # NEW subpackage
    __init__.py
    config.py                  # RunConfig with revised defaults and no __file__ paths
    models.py                  # SongCandidate updated (see Model Changes)
    db.py                      # fetch_catalog_pool with in-DB scoring queries
    cache.py                   # NEW: pool cache layer
    graph/
      builder.py
      nodes.py                 # enrich_pool updated to use pre-computed scores
      state.py
      llm.py
      checkpointer.py          # always InMemorySaver
    rules/
      beam.py
      diagnostics.py
      embeddings.py            # parse_pgvector_text removed; load_theme_anchors kept for sync
      fitness.py
      hard_constraints.py
      harmony.py
      phases.py                # _normalise_cosine_scores made public
      proposals.py
      themes.py                # classify_embedding_themes deprecated
      transitions.py
    artifacts/
      writer.py
      enrichment_report.py
      trace.py
    data/
      theme_anchors.json       # shipped with subpackage for DB population
    runner.py                  # RunConfig from CLI args -> graph.run() -> result dict
    persist.py                 # atomic save: create_songset + add_items in one tx
    diagnose.py                # assemble markdown sections from result
    report_writer.py           # write diagnose_report.md if --report

ops/admin-cli/tests/songset_construct/
  __init__.py
  test_runner.py
  test_persist.py
  test_diagnose.py
  test_relax_parser.py
  test_db_queries.py           # NEW: verify in-DB theme scoring SQL
  test_cache.py                # NEW: verify cache hit/miss/TTL/invalidation
  test_theme_anchors_sync.py   # NEW: verify sync command
```

## Bandwidth Optimization Strategy

### A. In-DB Theme Classification

#### `theme_anchors` table

Stores the 12 theme anchor vectors (讚美, 感恩, 敬拜, 奉獻, 認罪, 差遣, 信心, 祈禱, 復興, 聖靈, 十字架, 跟隨) as `vector(1536)` rows. Populated from `data/theme_anchors.json` via `sow-admin theme-anchors sync`.

#### Revised Pool Query (`POOL_QUERY`)

```sql
SELECT s.id, s.title, s.title_pinyin, s.composer, s.lyricist,
       s.album_name, s.album_series, s.musical_key, s.lyrics_raw,
       r.hash_prefix, r.tempo_bpm, r.musical_key AS r_musical_key,
       r.musical_mode, r.key_confidence, r.loudness_db,
       (
           SELECT json_object_agg(ta.theme, 1 - (se.embedding <=> ta.embedding))
           FROM theme_anchors ta
       ) AS song_theme_scores_raw
FROM songs s
JOIN recordings r ON s.id = r.song_id
LEFT JOIN song_embedding se ON se.song_id = s.id
WHERE r.visibility_status IN ('published', 'review')
  AND (r.lrc_status = 'completed' OR r.r2_lrc_url IS NOT NULL)
  AND r.deleted_at IS NULL
  AND s.deleted_at IS NULL
  AND (cardinality(%s::text[]) = 0 OR s.album_series = ANY(%s))
ORDER BY s.title
LIMIT %s
```

**Per-row transfer:** 15 small columns (~1.3 KB, dominated by `lyrics_raw`) + 1 JSON object (~200 bytes for 12 float scores) = ~1.5 KB.

**For 200 rows:** ~300 KB (vs v2's 4.3 MB).

When `song_embedding` is NULL (no embedding), the subquery returns NULL. The Python `_candidate_from_row` handles this as an empty dict.

#### New Line Theme Query (`LINE_THEME_QUERY`)

Replaces the POC's `LINE_EMBEDDING_QUERY`:

```sql
SELECT song_id,
       json_object_agg(theme, max_cosine) AS line_theme_scores_raw
FROM (
    SELECT sle.song_id,
           ta.theme,
           MAX(1 - (sle.embedding <=> ta.embedding)) AS max_cosine
    FROM song_line_embedding sle
    CROSS JOIN theme_anchors ta
    WHERE sle.song_id = ANY(%s)
    GROUP BY sle.song_id, ta.theme
) sub
GROUP BY song_id
```

**Per-row transfer:** `song_id` (8 bytes) + JSON object (~200 bytes) = ~210 bytes.

**For 200 songs:** ~42 KB (vs v2's 64.5 MB).

Songs with no line embeddings are simply absent from the result set; the Python code defaults to an empty dict.

#### Performance Note

Both queries use pgvector's HNSW index for cosine distance (`vector_cosine_ops`). The `CROSS JOIN theme_anchors` (12 rows) against `song_line_embedding` is a nested loop over 12 anchor rows — trivial cost. For 200 songs × 20 lines × 12 anchors = 48,000 exact cosine distance computations, all in compiled C with SIMD optimization. This is significantly faster than transferring 64.5 MB of text and parsing it in Python.

### B. Reduced Column Projection

The constructor-specific column lists replace `SONG_COLUMNS_FOR_JOIN` / `RECORDING_COLUMNS_FOR_JOIN`:

```python
CONSTRUCTOR_SONG_COLUMNS = (
    "s.id, s.title, s.title_pinyin, s.composer, s.lyricist, "
    "s.album_name, s.album_series, s.musical_key, s.lyrics_raw"
)
CONSTRUCTOR_RECORDING_COLUMNS = (
    "r.hash_prefix, r.tempo_bpm, r.musical_key AS r_musical_key, "
    "r.musical_mode, r.key_confidence, r.loudness_db"
)
```

**Skipped song columns** (15): `musical_key_root`, `musical_key_mode`, `musical_key_start_root`, `musical_key_end_root`, `musical_key_start_pitch_class`, `musical_key_end_pitch_class`, `musical_key_parse_status`, `lyrics_lines`, `sections`, `source_url`, `table_row_number`, `scraped_at`, `created_at`, `updated_at`, `deleted_at`.

**Skipped recording columns** (28): `content_hash`, `song_id`, `original_filename`, `file_size_bytes`, `imported_at`, `r2_audio_url`, `r2_stems_url`, `r2_lrc_url`, `duration_seconds`, `key_algorithm_version`, `key_score_margin`, `key_window_agreement`, `key_candidates`, `key_detected_at`, `beats`, `downbeats`, `sections`, `embeddings_shape`, `analysis_status`, `analysis_job_id`, `lrc_status`, `lrc_job_id`, `created_at`, `updated_at`, `youtube_url`, `visibility_status`, `download_status`, `deleted_at`.

**Note:** `_candidate_from_row` no longer uses `Song.from_row` / `Recording.from_row` (which expect all 24/34 columns in canonical order). It directly indexes the 15-column tuple by position.

### C. Pool Caching (`cache.py`)

```python
def _cache_key(pool_limit: int, album_series: list[str]) -> str:
    """SHA-256 hash of filter parameters."""
    raw = f"{pool_limit}:{sorted(album_series or ['*'])}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"pool_{key}.json"

def try_load_pool(config: RunConfig) -> list[SongCandidate] | None:
    """Return cached pool if valid, None otherwise."""
    if not config.use_cache:
        return None
    path = _cache_path(config.cache_dir, _cache_key(config.pool, config.album_series))
    if not path.exists():
        return None
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    if age_hours > config.cache_ttl:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return [SongCandidate.model_validate(item) for item in data]

def save_pool(config: RunConfig, pool: list[SongCandidate]) -> None:
    """Write pool to cache."""
    if not config.use_cache:
        return
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(config.cache_dir, _cache_key(config.pool, config.album_series))
    path.write_text(
        json.dumps([c.model_dump(mode="json") for c in pool], ensure_ascii=False),
        encoding="utf-8",
    )
```

**Cache invalidation:**
- **TTL:** 24 hours (configurable). Songs/recordings/embeddings rarely change.
- **Manual:** `--no-cache` flag bypasses cache entirely (always queries DB).
- **`--cache-ttl 0`:** Equivalent to `--no-cache`.
- **Stale data risk:** If a new song is added to the DB between cache writes, it won't appear in the pool until the cache expires. This is acceptable — the constructor is an offline tool, not a real-time system.

## Model Changes

### `SongCandidate` (Updated)

```python
class SongCandidate(BaseModel):
    song_id: str
    title: str
    title_pinyin: str | None = None
    composer: str | None = None
    lyricist: str | None = None
    album_name: str | None = None
    album_series: str | None = None
    recording_hash_prefix: str
    tempo_bpm: float | None = None
    musical_key: str | None = None
    musical_mode: str | None = None
    key_confidence: float | None = None
    loudness_db: float | None = None
    lyrics_raw: str | None = None
    # REMOVED: song_embedding: list[float] | None
    # REMOVED: line_embeddings: list[list[float]]
    song_theme_scores_raw: dict[str, float] = Field(default_factory=dict)   # NEW: raw cosine from SQL
    line_theme_scores_raw: dict[str, float] = Field(default_factory=dict)   # NEW: max cosine from SQL
    themes: dict[str, float] = Field(default_factory=dict)
    phase: int = 0
    secondary_phases: list[int] = Field(default_factory=list)
    fan_out: int = 0
    is_dead_end: bool = False
    is_hymn: bool = False
```

### `RunConfig` (Updated)

New fields:
```python
    use_cache: bool = True
    cache_dir: Path = field(default_factory=lambda: Path.home() / ".cache" / "sow" / "songset_constructor")
    cache_ttl: float = 24.0  # hours
```

Removed (same as v2):
- `env_file: Path | None`
- `load_runtime_env()`, `DEFAULT_ENV_FILE`

Renamed (same as v2):
- `no_llm` → `llm_enabled` (default `False`)

### `db.py` — `_candidate_from_row` (Rewritten)

```python
def _candidate_from_row(row: tuple) -> SongCandidate:
    # Tuple layout (15 columns):
    # 0: s.id          5: s.album_name    10: r.tempo_bpm
    # 1: s.title       6: s.album_series  11: r.musical_mode
    # 2: s.title_pinyin 7: s.musical_key  12: r.key_confidence
    # 3: s.composer    8: s.lyrics_raw    13: r.loudness_db
    # 4: s.lyricist    9: r.hash_prefix   14: song_theme_scores_raw (JSON or None)
    raw_scores = row[14]
    song_theme_scores_raw = json.loads(raw_scores) if raw_scores else {}
    return SongCandidate(
        song_id=row[0],
        title=row[1],
        title_pinyin=row[2],
        composer=row[3],
        lyricist=row[4],
        album_name=row[5],
        album_series=row[6],
        musical_key=row[10] or row[7],  # recording.musical_key or song.musical_key
        recording_hash_prefix=row[9],
        tempo_bpm=row[10],
        musical_mode=row[11],
        key_confidence=row[12],
        loudness_db=row[13],
        lyrics_raw=row[8],
        song_theme_scores_raw=song_theme_scores_raw,
        is_hymn=row[6] == "HYMN",
    )
```

### `db.py` — `fetch_catalog_pool` (Rewritten)

```python
def fetch_catalog_pool(config: RunConfig, *, client: ReadOnlyClient) -> list[SongCandidate]:
    cursor = client.connection.cursor()
    cursor.execute(POOL_QUERY, (config.album_series, config.album_series, config.pool_limit))
    pool = [_candidate_from_row(tuple(row)) for row in cursor.fetchall()]
    # Fetch line theme scores (in-DB MAX cosine per theme)
    song_ids = [c.song_id for c in pool]
    line_scores = fetch_line_theme_scores(song_ids, client=client)
    return [
        candidate.model_copy(update={"line_theme_scores_raw": line_scores.get(candidate.song_id, {})})
        for candidate in pool
    ]
```

### `db.py` — `fetch_line_theme_scores` (New, replaces `fetch_line_embeddings`)

```python
def fetch_line_theme_scores(song_ids: list[str], *, client: ReadOnlyClient) -> dict[str, dict[str, float]]:
    if not song_ids:
        return {}
    cursor = client.connection.cursor()
    cursor.execute(LINE_THEME_QUERY, (song_ids,))
    result: dict[str, dict[str, float]] = {}
    for song_id, scores_json in cursor.fetchall():
        result[song_id] = json.loads(scores_json) if scores_json else {}
    return result
```

### `graph/nodes.py` — `enrich_pool` (Updated)

```python
def enrich_pool(state: ConstructorState) -> dict:
    config = state["config"]
    enriched = []
    dropped = 0
    drop_diagnostics = enrichment_drop_diagnostics(state.get("pool", []))
    for candidate in state.get("pool", []):
        if candidate.tempo_bpm is None and candidate.musical_key is None:
            dropped += 1
            continue
        title = classify_title_themes(candidate.title, candidate.title_pinyin)
        lyrics = classify_lyrics_themes(candidate.lyrics_raw)
        # In-DB pre-computed scores → normalize in Python
        song_emb = normalise_cosine_scores(candidate.song_theme_scores_raw)
        line_emb = normalise_cosine_scores(candidate.line_theme_scores_raw)
        fused = apply_seasonal_bias(fuse_themes(title, lyrics, song_emb, line_emb), config.season)
        primary_phase = infer_phase(fused, candidate.tempo_bpm)
        secondary = infer_secondary_phases(fused, primary_phase, candidate.tempo_bpm)
        enriched.append(
            candidate.model_copy(
                update={
                    "themes": fused,
                    "phase": primary_phase,
                    "secondary_phases": secondary,
                    "is_hymn": candidate.album_series == "HYMN",
                }
            )
        )
    return {
        "pool": enriched,
        "trace": _trace(state, "enrich_pool", "exit", {
            "pool_size": len(enriched),
            "dropped": dropped,
            **drop_diagnostics,
        }),
    }
```

Key changes:
- `load_theme_anchors()` is **no longer called** in `enrich_pool` (anchors are in the DB).
- `classify_embedding_themes()` is **no longer called** (cosine computed in SQL).
- `_normalise_cosine_scores` is renamed to `normalise_cosine_scores` (made public in `themes.py`).
- The normalization and fusion logic is unchanged — only the source of raw cosine scores changes.

### `rules/themes.py` — Changes

- `_normalise_cosine_scores` → renamed to `normalise_cosine_scores` (public).
- `classify_embedding_themes` → deprecated (kept for reference/testing, but not called in production path).
- `THEMES`, `THEME_VOCAB`, `classify_title_themes`, `classify_lyrics_themes` → unchanged.

### `rules/embeddings.py` — Changes

- `parse_pgvector_text` → **removed** (no longer needed; no raw vectors are fetched).
- `cosine` → **removed** (no longer needed; cosine computed in SQL).
- `load_theme_anchors` → **kept** (used by `theme-anchors sync` command to read the JSON file for DB population).

## Behaviour

### Step 0 — Parse `--relax` and `--constraints-file`
If provided, merge into a `dict[str, Any]` passed to `RunConfig` construction.

### Step 1 — Resolve user
Same as `songset list`: `UserClient.get_user_by_email(email)`. Missing → `[red]User not found[/red]`, exit 1.

### Step 2 — Build AdminConfig + ReadOnlyClient
`AdminConfig.load(config_path)` → `ConnectionProvider` → `ReadOnlyClient`.

### Step 3 — Validate theme_anchors table
Query: `SELECT COUNT(*) FROM theme_anchors`. If count ≠ 12, print error mentioning `sow-admin theme-anchors sync` and exit 1.

### Step 4 — Build RunConfig
Map Typer options to `RunConfig` fields:
- `llm_enabled` (from `--llm / --no-llm`, default `False`)
- `proposals` (from `--proposals`)
- `count` (from `--count`)
- `pool` (from `--pool`)
- `relax_*` (from parsed `--relax` / `--constraints-file`)
- `output_dir` (from `--report-dir` if `--report` else `None`)
- `use_cache` (from `--no-cache`, default `True`)
- `cache_dir` (from `--cache-dir`, default `~/.cache/sow/songset_constructor/`)
- `cache_ttl` (from `--cache-ttl`, default `24.0`)

Force:
- `interactive_review = False`
- `resume_thread_id = None`
- `only_evaluate_pool_enrichment = False`

### Step 5 — Run graph
Call `runner.run(config, read_client)` which:

1. **Try cache:** `cache.try_load_pool(config)` → if hit, skip to step 4.
2. **Fetch pool:** `fetch_catalog_pool(config, client=read_client)` — executes `POOL_QUERY` + `LINE_THEME_QUERY`.
3. **Save to cache:** `cache.save_pool(config, pool)`.
4. **Build graph:** `build_graph(config)` with `InMemorySaver`.
5. **Stream:** `stream_mode="debug"` (for console progress only).
6. **Return:** `{"final_proposals": ..., "pool": ..., "trace": ..., "enrichment_metrics": ...}`.

Print `[dim]Pool loaded from cache (age: Nh)[/dim]` or `[dim]Pool fetched from DB (N songs)[/dim]` accordingly.

### Step 6 — Print summary
Always print a Rich table of proposals (rank, score, sequence, BPM/key arcs, warnings). If `proposals == []`, print the deterministic no-results summary.

### Step 7 — Report (if `--report`)
Write `<report_dir>/diagnose_report.md` with:
1. Header + timestamp + RunConfig dump (including cache status).
2. Pool enrichment metrics (fenced block).
3. Pool overview table.
4. Phase distribution & role-eligibility counts.
5. Rule-drop diagnostics bullets.
6. Per-proposal sections (summary + details + score components).
7. Diversity matrix.
8. Condensed graph trace bullets.
9. No-results fallback if applicable.

No other artifacts are written.

### Step 8 — Save flow

#### `--dry-run`
Print `[yellow]Dry run: skipping DB writes.[/yellow]` and exit 0.

#### Default (no `--yes`, no `--dry-run`)
Prompt: `Save N songset(s) to user <email> (y/N)?` via `typer.confirm(default=False)`.
- `N`/Ctrl-C → exit 0 cleanly.
- `y` → proceed to Step 9.

#### `--yes`
Skip prompt; proceed to Step 9.

### Step 9 — Persist songsets (`persist.py`)
**Atomic save per proposal.** For each `SongsetProposal` in `final_proposals`:

1. Build a list of `SongsetItem`-data dicts (in `position` order).
2. Call a new atomic helper on `SongsetClient`:
   ```
   create_songset_with_items(
       name="Constructed rank {rank}/{proposals} ({count}-song)",
       description=first_200_chars(rationale) or fallback,
       items=[{song_id, recording_hash_prefix, position, gap_beats, ...}],
   )
   ```
3. If a `MissingReferenceError` occurs, **rollback**, print `[red]` message, continue to next proposal.
4. Wrap in Rich progress bar. Print `Created songset <id> (rank N)` on success.
5. Exit 1 if any proposal failed; exit 0 if all succeeded.

## Refactors to POC Code

### `config.py`
- Remove `default_output_dir()` factory that uses `Path(__file__)`.
- Remove `load_runtime_env()` and `DEFAULT_ENV_FILE`.
- Remove `env_file` field.
- Rename `no_llm` → `llm_enabled` (default `False`).
- Add `use_cache: bool = True`, `cache_dir: Path`, `cache_ttl: float = 24.0`.
- `output_dir: Path | None = None`.

### `db.py`
- Remove `get_connection_url()` and `build_read_client()`.
- Remove `from sow_lab_app.config import AppConfig`.
- Remove `from ...embeddings import parse_pgvector_text` (no longer needed).
- Remove `SONG_COLUMNS_FOR_JOIN` / `RECORDING_COLUMNS_FOR_JOIN` imports.
- Define `CONSTRUCTOR_SONG_COLUMNS` and `CONSTRUCTOR_RECORDING_COLUMNS` (constructor-specific).
- `POOL_QUERY` uses in-DB theme scoring (correlated subquery with `theme_anchors`).
- `LINE_EMBEDDING_QUERY` → replaced by `LINE_THEME_QUERY` (in-DB MAX cosine per theme).
- `fetch_catalog_pool(config, *, client: ReadOnlyClient)` — `client` is **required**.
- `fetch_line_embeddings` → replaced by `fetch_line_theme_scores`.
- `_candidate_from_row` rewritten (15-column tuple, no `Song.from_row` / `Recording.from_row`).

### `graph/checkpointer.py`
- Remove `SqliteSaver` path and `_stable_checkpoint_dir()`.
- Always return `InMemorySaver()`.

### `graph/nodes.py`
- `enrich_pool`: Remove `load_theme_anchors()` and `classify_embedding_themes()` calls.
- `enrich_pool`: Call `normalise_cosine_scores()` directly on `candidate.song_theme_scores_raw` and `candidate.line_theme_scores_raw`.

### `rules/embeddings.py`
- Remove `parse_pgvector_text` and `cosine`.
- Keep `load_theme_anchors` (used by `theme-anchors sync`).

### `rules/themes.py`
- Rename `_normalise_cosine_scores` → `normalise_cosine_scores` (public).
- Deprecate `classify_embedding_themes` (keep for reference/tests, do not call in production).

## Tests

### New subpackage tests: `ops/admin-cli/tests/songset_construct/`

| File | Scope |
|---|---|
| `test_runner.py` | Mock `build_graph` and `fetch_catalog_pool`; verify RunConfig maps from Typer options; verify `--user` missing exits 2; verify `--no-llm --llm-judge` errors; verify cache integration in runner. |
| `test_persist.py` | Mock `SongsetClient`; verify each proposal → one atomic create-with-items call; verify `MissingReferenceError` rollback. |
| `test_diagnose.py` | Synthesize proposals + pool + trace; assert report sections. |
| `test_relax_parser.py` | Valid/invalid `--relax` strings and `--constraints-file` merging. |
| `test_db_queries.py` | **NEW:** Verify `POOL_QUERY` returns song_theme_scores_raw JSON; verify `LINE_THEME_QUERY` returns max cosine per theme; verify `_candidate_from_row` parses 15-column tuple correctly; verify NULL embedding → empty dict. |
| `test_cache.py` | **NEW:** Verify cache hit returns pool; cache miss returns None; TTL expiry returns None; `--no-cache` always returns None; `save_pool` writes valid JSON; cache key is deterministic for same params, different for different params. |
| `test_theme_anchors_sync.py` | **NEW:** Verify sync reads JSON and upserts 12 rows; `--force` re-inserts; without `--force` skips if 12 rows exist. |

Run with:
```bash
uv run --project ops/admin-cli --python 3.11 --extra admin --extra constructor --extra test pytest tests/songset_construct/ -v
```

### Bandwidth Validation Test

A test that runs `fetch_catalog_pool` against a test database with pgvector, and asserts:
- No `embedding::text` appears in any executed query (can be verified by intercepting cursor.execute).
- The `song_theme_scores_raw` JSON has exactly 12 keys.
- The `line_theme_scores_raw` JSON has exactly 12 keys per song (or is empty).
- Total bytes transferred (estimated from row sizes) is < 1 MB for pool_limit=200.

## Documentation Updates

- `AGENTS.md` — remove `ops/songset-constructor` component. Note `constructor` extra under Admin CLI. Add `sow-admin theme-anchors sync` to commands list.
- `ops/admin-cli/README.md` — add `sow-admin songset construct` and `sow-admin theme-anchors sync` examples. Note the `theme-anchors sync` prerequisite.
- `docs/agent_guide_songset_constructor.md` — update Quick Start to use `sow-admin songset construct` as the production path; deprecate `construct_songset_agent.py`.
- `specs/reduce-database-network-transfer-v3.md` — update Phase 2 to note that the v3 songset construct spec supersedes the POC-level fix; the in-DB scoring is implemented in the production admin CLI subpackage directly.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| `theme_anchors` table not populated | Startup validation (Step 3) checks row count; clear error message with sync command. |
| pgvector `<=>` performance with `CROSS JOIN` | 12-row theme_anchors table is trivial; HNSW index on `song_line_embedding.embedding` accelerates individual distance computation; 48K computations is fast in C/SIMD. |
| `json_object_agg` returns NULL when `song_embedding` is NULL | `_candidate_from_row` handles NULL → empty dict; `normalise_cosine_scores` returns zeros for empty dict (existing behavior). |
| `theme_anchors` drift (anchors updated) | `sow-admin theme-anchors sync --force` re-populates; `model_version` column tracks provenance; cache TTL ensures stale pools expire. |
| Pool cache returns stale data (new songs added) | TTL (24h default) bounds staleness; `--no-cache` bypasses; constructor is offline tool — staleness is acceptable. |
| `SongsetClient` does not support atomic create+items | Add `create_songset_with_items` or use raw SQL transaction in `persist.py`. |
| LangGraph debug streaming is slow / noisy | Acceptable for CLI; keep `stream_mode="debug"` for user feedback. |
| Stale recording_hash_prefix between construct and save | Validate all recordings inside the atomic transaction before inserting. |
| `--user` not-yet-existing | Early `UserClient.get_user_by_email` check before running graph. |
| Heavy LLM deps under default install | Kept in `constructor` extra only; lazy-import guard. |
| `Path(__file__)` default output dir in POC | Removed; `output_dir` is `None` unless `--report-dir` provided. |
| POC code drift between `lab/` and admin-cli subpackage | Lab code is frozen (not deleted). Subpackage is the production source of truth. |
| Normalization results differ between Python `cosine()` and pgvector `<=>` | Floating-point differences are negligible (<1e-6); normalization (min-max shift) is robust to tiny perturbations. Add a tolerance test in `test_db_queries.py`. |

## Acceptance

- `theme_anchors` table exists with 12 rows. `sow-admin theme-anchors sync` succeeds.
- `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/` exists with all modules listed above.
- `uv sync --project ops/admin-cli --extra admin --extra constructor` succeeds.
- `sow-admin songset construct --user me@x --count 3 --proposals 3 --dry-run` prints proposals, writes no DB rows, writes no files.
- Same with `--report` writes `./output/songset_constructor/<ts>/diagnose_report.md`, no other artifacts.
- Same with `--llm` and `--report` runs graph with LLM nodes and writes report.
- Same without `--dry-run` prompts `y/N`; selecting `N` leaves DB clean.
- Same with `--yes` persists N songsets atomically under `me@x`.
- Running without `constructor` extra fails with clear `RuntimeError` and install hint.
- Running without `theme-anchors sync` first exits 1 with clear error.
- **Bandwidth:** A single `--dry-run` construct with `--pool 200` transfers < 1 MB from the DB (measured by instrumenting the cursor). No `embedding::text` cast appears in any query.
- **Cache:** A second run with the same `--pool` and `--album-series` prints "Pool loaded from cache" and makes zero DB queries during pool fetch.
- **Cache bypass:** Running with `--no-cache` always fetches from DB.
- `test_db_queries.py`, `test_cache.py`, `test_theme_anchors_sync.py` all pass.
