# Spec: Cache madmom downbeat output for reuse across jobs

> **Date:** 2026-08-12
> **Branch:** TBD
> **Spec ID:** `cache-madmom-downbeats-for-reuse`

---

## Summary

The Analysis Service currently runs madmom downbeat detection inside the
Component Analysis job but discards the result. The computed `downbeats`
never get persisted — they live in a local variable in
`_process_component_analysis_job` (`queue.py:964-981`) and are not written to
the cached `components.json` payload (`_serialize_components` at
`components.py:1349-1391`).

This means:

1. Every `--force` re-run of Component Analysis re-runs the (slow, CPU-heavy)
   madmom RNN + DBN pipeline even though the audio content hasn't changed.
2. Other consumers that could benefit from a beat grid (transition planning
   between chorus components, render-worker audio splice preparation) have no
   way to fetch cached madmom output — they'd have to re-run detection.
3. The tier-2 lyrics-repetition path inside `extract_components` calls
   `analyze_audio_fast()` inline to populate beats, but that function does NOT
   return beats/downbeats (confirmed at `analyzer.py:544-550`). So the
   lyrics path effectively runs with no beat snapping unless madmom is
   triggered separately.

This spec adds a dedicated cache artifact — `madmom_beats.json` — keyed by
`content_hash`, storing the full madmom beat grid (not just downbeats). The
cache is consulted **even when `--force` is set** (the force flag only
invalidates the component-result cache, not the beat cache). A new
`--skip-beat-cache` Admin CLI flag bypasses beat-cache reads for genuine
re-detection scenarios.

---

## Decisions (from clarifying questions)

| Question | Decision |
|---|---|
| Cache location | R2 + local CacheManager (mirrors existing `components.json` flow) |
| Cache scope | Store the full beat grid `[[time, beat_in_bar], ...]` plus extracted downbeats. Retaining `beat_in_bar` enables musical reasoning (backbeats = beats 2 & 4). |
| Cache key | Dedicated key per `content_hash`. Decoupled from `components.json` and `analysis.json`. No parameter hashing — `beats_per_bar=[3,4]` and `fps=100` are fixed madmom constants. |
| `--skip-beat-cache` semantics | Orthogonal to `--force`. `--skip-beat-cache` only skips **reading** the beat cache; fresh detection still **writes** to cache. `--force` still independently controls component-cache invalidation. The two flags can combine. |
| Tier-2 reuse | Yes — `extract_components`'s tier-2 lyrics-repetition path reuses cached madmom downbeats instead of relying on the (beats-less) inline `analyze_audio_fast`. |

---

## Affected Components

| Component | Path | Change |
|---|---|---|
| Analysis Service cache | `ops/analysis-service/src/sow_analysis/storage/cache.py` | New `get_madmom_beats` / `save_madmom_beats` methods + `MADMOM_BEATS_SCHEMA_VERSION` |
| Analysis Service R2 | `ops/analysis-service/src/sow_analysis/storage/r2.py` | New `upload_madmom_beats` / `download_madmom_beats` methods |
| Analysis Service models | `ops/analysis-service/src/sow_analysis/models.py` | New `skip_beat_cache: bool` field on `ComponentAnalysisOptions` |
| Component worker | `ops/analysis-service/src/sow_analysis/workers/components.py` | Refactor `_detect_downbeats_madmom` to return full grid; consult cache in `extract_components` |
| Job queue | `ops/analysis-service/src/sow_analysis/workers/queue.py` | Replace inline madmom call with cached helper; thread `skip_beat_cache` through |
| Admin CLI service | `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py` | Pass `skip_beat_cache` in `submit_component_analysis` payload |
| Admin CLI command | `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | New `--skip-beat-cache` typer option on `components` command |
| Analysis Service tests | `ops/analysis-service/tests/test_cache.py` | New tests for madmom-beats cache get/save/schema-version miss |
| Analysis Service tests | `ops/analysis-service/tests/test_components.py` | Test that `extract_components` reuses cached beats and skips detection |
| Admin CLI tests | (wherever `components` command is tested) | Test `--skip-beat-cache` flag plumbing |

---

## Phase 1: New cache artifact — `madmom_beats.json`

**Complexity:** M

### 1.1 Schema

File: `ops/analysis-service/src/sow_analysis/storage/cache.py`

Add a new schema version constant alongside `COMPONENT_SCHEMA_VERSION`:

```python
# Bump when the madmom_beats.json payload shape changes. Cached payloads with
# a mismatched version are treated as cache misses and re-detected.
MADMOM_BEATS_SCHEMA_VERSION = 1
```

Payload shape:

```json
{
  "schema_version": 1,
  "content_hash": "<full sha-256>",
  "hash_prefix": "<first 12 chars>",
  "beats": [[0.464, 1], [0.928, 2], [1.392, 3], [1.856, 4], [2.320, 1], ...],
  "downbeats": [0.464, 2.320, 4.176, ...],
  "detected_at": "2026-08-12T12:34:56.789000+00:00",
  "madmom_params": {
    "beats_per_bar": [3, 4],
    "fps": 100
  }
}
```

- `beats`: full grid as `[[time, beat_in_bar], ...]` (the raw DBN output).
  `beat_in_bar == 1` marks downbeats; `2`/`3`/`4` mark intra-bar beats —
  useful for backbeat (beats 2 & 4) reasoning in transition planning.
- `downbeats`: convenience extraction (rows where `beat_in_bar == 1`),
  flattened to `[time, ...]`. This matches the current
  `_detect_downbeats_madmom` return type so existing callers are unaffected.
- `madmom_params`: records the fixed detection parameters. Since they are
  constants, no parameter-hashing of the cache key is needed.

### 1.2 CacheManager methods

File: `ops/analysis-service/src/sow_analysis/storage/cache.py`

Add two methods mirroring the existing `get_component_result` /
`save_component_result` pair (cache.py:308-349):

```python
def get_madmom_beats(self, content_hash: str) -> Optional[dict]:
    """Check if madmom beat grid exists in local cache.

    Returns None if not found, the file is corrupt, OR the cached
    ``schema_version`` does not match MADMOM_BEATS_SCHEMA_VERSION.
    """
    hash_prefix = self._get_hash_prefix(content_hash)
    cache_file = self.cache_dir / f"{hash_prefix}_madmom_beats.json"
    if cache_file.exists():
        try:
            payload = json.loads(cache_file.read_text())
        except (json.JSONDecodeError, IOError):
            return None
        if payload.get("schema_version") != MADMOM_BEATS_SCHEMA_VERSION:
            return None
        return payload
    return None

def save_madmom_beats(self, content_hash: str, payload: dict) -> Path:
    """Save madmom beat grid to local cache atomically.

    The ``payload`` dict MUST include
    ``schema_version=MADMOM_BEATS_SCHEMA_VERSION``.
    """
    hash_prefix = self._get_hash_prefix(content_hash)
    cache_file = self.cache_dir / f"{hash_prefix}_madmom_beats.json"
    # Atomic write (same pattern as save_fast_analyze_result, cache.py:163-175)
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=str(cache_file.parent),
        prefix=f".{cache_file.stem}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(json.dumps(payload, indent=2))
        tmp_path = Path(tmp.name)
    os.replace(str(tmp_path), str(cache_file))
    return cache_file
```

Local filename: `{hash32}_madmom_beats.json` (sibling to
`{hash32}_components.json`).

### 1.3 R2 methods

File: `ops/analysis-service/src/sow_analysis/storage/r2.py`

Add upload/download methods mirroring the existing
`upload_component_result` / `download_component_result` pair (r2.py:447-512):

```python
async def upload_madmom_beats(self, hash_prefix: str, payload: dict) -> str:
    """Upload madmom_beats.json to R2 at {hash_prefix}/madmom_beats.json."""
    # Same temp-file + executor pattern as upload_component_result (r2.py:459-471)

async def download_madmom_beats(self, hash_prefix: str) -> Optional[dict]:
    """Download madmom_beats.json from R2.

    Returns None if not found OR if schema_version mismatches
    MADMOM_BEATS_SCHEMA_VERSION.
    """
    # Same download + schema check pattern as download_component_result
    # (r2.py:473-512)
```

R2 key: `{hash_prefix}/madmom_beats.json` (sibling to `{hash_prefix}/components.json`).

Import `MADMOM_BEATS_SCHEMA_VERSION` from `.cache` (already imports
`COMPONENT_SCHEMA_VERSION` at r2.py:14).

---

## Phase 2: Refactor `_detect_downbeats_madmom` to return full grid

**Complexity:** S

File: `ops/analysis-service/src/sow_analysis/workers/components.py`

### 2.1 Change return type

Current `_detect_downbeats_madmom` (components.py:33-74) returns
`Optional[list[float]]` (downbeats only). Change it to return
`Optional[dict]` matching the payload shape from §1.1:

```python
def _detect_downbeats_madmom(audio_path: Path) -> Optional[dict]:
    """Detect beats + downbeats using madmom's two-stage pipeline.

    Returns:
        Dict with keys: beats ([[time, beat_in_bar], ...]),
        downbeats ([time, ...]), detected_at, madmom_params.
        None if detection fails.
    """
    try:
        from madmom.features.downbeats import (
            RNNDownBeatProcessor,
            DBNDownBeatTrackingProcessor,
        )

        rnn = RNNDownBeatProcessor()
        activations = rnn(str(audio_path))
        dbn = DBNDownBeatTrackingProcessor(beats_per_bar=[3, 4], fps=100)
        beats_grid = dbn(activations)  # (num_beats, 2): [time, beat_in_bar]

        downbeat_times = beats_grid[beats_grid[:, 1] == 1][:, 0].tolist()
        beats_list = beats_grid.tolist()
        return {
            "beats": beats_list,
            "downbeats": sorted(downbeat_times),
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "madmom_params": {"beats_per_bar": [3, 4], "fps": 100},
        }
    except Exception as e:
        logger.warning(f"madmom downbeat detection failed: {e}")
        return None
```

Need to add `from datetime import datetime, timezone` import (currently
components.py imports `time`, not `datetime`).

### 2.2 New cached helper: `get_or_detect_madmom_beats`

Add a new async helper that wraps detection with cache lookups (local → R2 →
detect → save local → upload R2). This is the single entry point all callers
use:

```python
async def get_or_detect_madmom_beats(
    audio_path: Path,
    content_hash: str,
    cache_manager: CacheManager,
    r2_client: Optional[R2Client],
    skip_beat_cache: bool = False,
) -> Optional[dict]:
    """Return cached madmom beat grid, detecting + caching if absent.

    Cache layers (checked in order, unless ``skip_beat_cache``):
      1. Local CacheManager ({hash32}_madmom_beats.json)
      2. R2 ({hash_prefix}/madmom_beats.json) — downloaded + saved to local

    On miss: run ``_detect_downbeats_madmom`` in an executor, then persist
    to local cache and R2 (best-effort). Always writes fresh output to cache
    (even when ``skip_beat_cache`` skipped the read) — the only way to
    suppress writes is to not call this function.

    Returns the payload dict (see §1.1) or None if detection fails.
    """
    hash_prefix = content_hash[:12]

    if not skip_beat_cache:
        cached = cache_manager.get_madmom_beats(content_hash)
        if cached is not None:
            logger.info(f"Madmom beats cache hit (local): {content_hash[:16]}...")
            return cached

        if r2_client is not None:
            r2_cached = await r2_client.download_madmom_beats(hash_prefix)
            if r2_cached is not None:
                logger.info(f"Madmom beats cache hit (R2): {content_hash[:16]}...")
                cache_manager.save_madmom_beats(content_hash, r2_cached)
                return r2_cached

    loop = asyncio.get_event_loop()
    detected = await loop.run_in_executor(None, _detect_downbeats_madmom, audio_path)
    if detected is None:
        return None

    # Stamp identity fields before persisting (detection fn doesn't know hash).
    detected["content_hash"] = content_hash
    detected["hash_prefix"] = hash_prefix
    detected["schema_version"] = MADMOM_BEATS_SCHEMA_VERSION

    cache_manager.save_madmom_beats(content_hash, detected)

    if r2_client is not None:
        try:
            await r2_client.upload_madmom_beats(hash_prefix, detected)
        except Exception as e:
            logger.warning(f"Failed to upload madmom_beats.json to R2: {e}")

    return detected
```

Import `MADMOM_BEATS_SCHEMA_VERSION` from `..storage.cache` (components.py:24
already imports `COMPONENT_SCHEMA_VERSION` from the same module).

---

## Phase 3: Wire the cache into the Component Analysis job

**Complexity:** M

### 3.1 New model field

File: `ops/analysis-service/src/sow_analysis/models.py`

Add to `ComponentAnalysisOptions` (models.py:74-87):

```python
class ComponentAnalysisOptions(BaseModel):
    """Options for component analysis jobs."""

    force: bool = False
    use_stems: bool = False
    snap_to_downbeat: bool = False
    energy_aware_roles: bool = False
    classify_theme: bool = False
    classify_vocal_posture: bool = False
    # v6: bypass reading the cached madmom beat grid (re-detect + overwrite).
    # Orthogonal to ``force``: ``force`` invalidates components.json;
    # ``skip_beat_cache`` invalidates madmom_beats.json reads only.
    # Fresh detection still WRITES to the cache.
    skip_beat_cache: bool = False
```

No change to `ComponentAnalysisJobRequest` itself — the request already
carries `options: ComponentAnalysisOptions`, so the new field rides along
automatically (pydantic will accept it in the POST payload).

### 3.2 Queue: replace inline madmom call with cached helper

File: `ops/analysis-service/src/sow_analysis/workers/queue.py`

Current code at queue.py:964-981 runs madmom inline only when
`snap_to_downbeat` is requested AND `request.downbeats` is missing.

Replace it with a call to the new cached helper that runs **whenever
downbeats are needed** (broader than just `snap_to_downbeat`, because the
tier-2 lyrics-repetition path also benefits — see §3.3):

```python
# v6: Populate downbeats from cache (or detect + cache on miss).
# This runs even on --force: the beat grid is a pure function of audio
# content and is intentionally cached separately from components.json.
downbeats = request.downbeats
beats_grid: Optional[list[list[float]]] = None

if not downbeats:
    from .components import get_or_detect_madmom_beats

    madmom_result = await get_or_detect_madmom_beats(
        audio_path=audio_path,
        content_hash=request.content_hash,
        cache_manager=self.cache_manager,
        r2_client=self.r2_client,
        skip_beat_cache=request.options.skip_beat_cache,
    )
    if madmom_result is not None:
        downbeats = madmom_result.get("downbeats")
        beats_grid = madmom_result.get("beats")
        if not downbeats:
            logger.warning(
                "madmom detection returned no downbeats; using beat snapping only"
            )
```

Keep the existing `step_timer("Madmom downbeat detection", logger)` wrapper
around the helper call — it now measures cache-hit-or-detect end-to-end.

Pass `downbeats=downbeats` to `extract_components` as before (queue.py:992).
`beats_grid` is logged but not currently consumed by `extract_components`
(future transition-planning consumers will read it from the cache directly).

**Behavior matrix:**

| `request.downbeats` | `skip_beat_cache` | Behavior |
|---|---|---|
| provided | any | Skip madmom entirely (caller supplied cached downbeats from DB, as today) |
| absent | False | `get_or_detect_madmom_beats` → local cache → R2 → detect+cache |
| absent | True | `get_or_detect_madmom_beats` skips reads, runs detect, writes cache |

`--force` (request.options.force) has **no effect** on this branch — it only
governs the `extract_components` cache check at components.py:1226. This is
the key behavioral change requested: madmom output survives `--force`.

### 3.3 Tier-2 lyrics-repetition path: reuse cached downbeats

File: `ops/analysis-service/src/sow_analysis/workers/components.py`

Current tier-2 fallback in `extract_components` (components.py:1276-1289)
calls `analyze_audio_fast()` inline hoping to populate `beats`/`downbeats`,
but that function returns neither (it only emits duration/bpm/key/loudness —
analyzer.py:544-550). So the call wastes a librosa pass and leaves both
variables None.

Change: if `downbeats` is still None at the tier-2 entry (i.e. the queue
didn't supply them AND the queue's madmom helper didn't populate them — the
latter shouldn't happen post-§3.2, but defense-in-depth), consult the beat
cache directly:

```python
if not beats and not downbeats:
    # v6: Prefer cached madmom beats over a wasteful inline fast_analyze
    # (which doesn't return beats/downbeats anyway — see analyzer.py:544).
    cached_beats = cache_manager.get_madmom_beats(content_hash)
    if cached_beats is not None:
        downbeats = cached_beats.get("downbeats")
        # Note: full grid (beats with beat_in_bar) is in cached_beats["beats"]
        # but identify_from_lyrics_repetition consumes flat beat timestamps,
        # not the grid. Leave beats=None; downbeats suffice for snapping.
    # Drop the inline analyze_audio_fast call entirely — it never returned
    # beats and only wasted ~5s of librosa.load + chroma_cqt.
```

Remove the `from .analyzer import analyze_audio_fast` inline call and the
`inline_fast_ran` tracking flag (components.py:1276-1311). The
confidence-lowering fallback at components.py:1309-1311 ("if not beats and
not inline_fast_ran: confidence = 0.5") stays but its condition simplifies
to `if not downbeats`.

This is a behavior change documented in the docstring at components.py:1215-
1218 — update that note to reflect that the queue now populates downbeats
via the cached madmom helper, and the tier-2 path no longer calls
fast_analyze inline.

---

## Phase 4: Admin CLI `--skip-beat-cache` flag

**Complexity:** S

### 4.1 Service client: pass the flag through

File: `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`

Add `skip_beat_cache: bool = False` parameter to
`submit_component_analysis` (analysis.py:566-631) and include it in the
`options` dict of the POST payload (analysis.py:623-630):

```python
"options": {
    "force": force,
    "snap_to_downbeat": snap_to_downbeat,
    "energy_aware_roles": energy_aware_roles,
    "use_stems": use_stems,
    "classify_theme": classify_theme,
    "classify_vocal_posture": classify_vocal_posture,
    "skip_beat_cache": skip_beat_cache,
},
```

Update the docstring to describe the new flag.

### 4.2 Command: new typer option

File: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

Add a new option to the `components` command (audio.py:2153-2183), placed
near `--force` for discoverability:

```python
skip_beat_cache: bool = typer.Option(
    False,
    "--skip-beat-cache",
    help=(
        "Bypass cached madmom beat grid; re-run detection (still writes "
        "fresh result to cache). Orthogonal to --force."
    ),
),
```

Thread it through both call sites:

- `_submit_component_analysis_job(...)` helper (audio.py:1942-1956 signature
  + audio.py:2043-2057 `client.submit_component_analysis(...)` call) — add
  `skip_beat_cache: bool = False` parameter and pass it through.
- Both invocations of the helper (single-song at audio.py:2330-2338 and
  batch stdin at audio.py:2258-2279) — pass `skip_beat_cache=skip_beat_cache`.

Update the command docstring (audio.py:2185-2203) to mention the flag and
its orthogonality with `--force`:

```
--skip-beat-cache bypasses the cached madmom beat grid and re-runs
detection. Unlike --force (which re-runs component extraction), this only
affects beat detection. Freshly detected beats are still written to cache.
Combine with --force to re-run everything from scratch.
```

### 4.3 Command help output

The `--help` text should make the orthogonality clear. Example after
implementation:

```
--force / -f              Force re-extraction of components
--skip-beat-cache         Bypass cached madmom beat grid; re-run detection
                          (still writes fresh result to cache). Orthogonal
                          to --force.
```

---

## Phase 5: Tests

**Complexity:** M

### 5.1 Cache tests

File: `ops/analysis-service/tests/test_cache.py`

Add a `TestMadmomBeatsCache` class mirroring `TestWhisperTranscriptionCache`
(test_cache.py:13-60):

- `test_save_and_get_madmom_beats` — save payload, get returns it
- `test_get_returns_none_before_save` — cache miss
- `test_schema_version_mismatch_treated_as_miss` — payload with
  `schema_version=99` returns None
- `test_corrupt_json_returns_none` — invalid JSON in file returns None

### 5.2 Component worker tests

File: `ops/analysis-service/tests/test_components.py`

- `test_extract_components_uses_cached_madmom_beats` — pre-seed
  `cache_manager.save_madmom_beats(...)`, monkeypatch
  `_detect_downbeats_madmom` to assert it's NOT called, verify
  `downbeats` flow through to `identify_from_lyrics_repetition`.
- `test_extract_components_skip_beat_cache_runs_detection` — with
  `skip_beat_cache=True`, assert `_detect_downbeats_madmom` IS called even
  when cache is populated.
- `test_get_or_detect_madmom_beats_writes_cache_on_miss` — empty cache,
  monkeypatch detection, assert `save_madmom_beats` is called with the
  detected payload (including `schema_version` and identity fields).

### 5.3 Admin CLI tests

Wherever the `components` command is tested (search for existing
`components` command tests in `ops/admin-cli/tests/`):

- `test_components_skip_beat_cache_flag_plumbed` — invoke the command with
  `--skip-beat-cache`, mock `AnalysisClient.submit_component_analysis`,
  assert the payload `options.skip_beat_cache == True`.
- `test_components_default_passes_skip_beat_cache_false` — without the
  flag, assert `options.skip_beat_cache == False`.

### 5.4 R2 tests

File: `ops/analysis-service/tests/integration/test_r2.py`

Add round-trip tests for `upload_madmom_beats` / `download_madmom_beats`
mirroring `test_r2.py`'s existing component-result round-trip test (search
for `download_component_result`).

---

## Backward Compatibility

- **No migration needed.** `madmom_beats.json` is a new artifact; existing
  songs simply have a cache miss and trigger detection on first component
  analysis. No DB schema change.
- **API: no breaking change.** `skip_beat_cache` defaults to `False` in
  `ComponentAnalysisOptions`, so existing POST payloads that don't include
  it behave exactly as before (except they now benefit from the beat cache
  on `--force` re-runs — an improvement, not a regression).
- **CLI: no breaking change.** New flag is optional. Existing invocations
  are unaffected.
- **Cache schema versioning:** `MADMOM_BEATS_SCHEMA_VERSION = 1`. Future
  shape changes bump the constant; mismatched payloads are treated as cache
  misses (same pattern as `COMPONENT_SCHEMA_VERSION`, cache.py:15 + 328).

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Stale cache when audio content changes | Cache is keyed by `content_hash` (SHA-256 of audio bytes). Different audio → different hash → different cache key. Safe by construction. |
| madmom params (`beats_per_bar`, `fps`) change in future | `madmom_params` is recorded in the payload for auditability. If params change, bump `MADMOM_BEATS_SCHEMA_VERSION` to force re-detection. No parameter-hashing needed since the values are currently constants. |
| Removing the inline `analyze_audio_fast` call from tier-2 path changes behavior | The call never populated `beats`/`downbeats` anyway (analyzer.py:544-550 returns neither). Removing it is a pure perf win with no semantic change. Confidence-lowering fallback at components.py:1309-1311 is preserved. |
| `--force` no longer re-runs madmom | Explicitly requested by the user. The beat grid is a pure function of audio content; re-running on identical audio is wasteful. `--skip-beat-cache` exists for the rare genuine re-detection case (e.g. madmom library upgrade). |
| R2 storage growth (one extra ~5-50KB JSON per song) | Negligible vs. existing `components.json` and `analysis.json` per song. Same prefix bucket, same lifecycle. |
| Concurrent jobs writing the same `madmom_beats.json` | Atomic local write via `os.replace` (same pattern as `save_fast_analyze_result`, cache.py:163-175). R2 writes are idempotent last-writer-wins (same content). Safe. |

---

## Usage Examples

### Standard re-run (beats cached, madmom skipped):

```bash
uv run --project ops/admin-cli --extra admin sow-admin audio components SONG_001 --force
# → components.json re-extracted; madmom_beats.json cache HIT, detection skipped
```

### Genuine re-detection (madmom library upgraded):

```bash
uv run --project ops/admin-cli --extra admin sow-admin audio components SONG_001 --force --skip-beat-cache
# → components.json re-extracted; madmom_beats.json cache MISS (read skipped),
#   detection runs, fresh result written to cache
```

### Future transition-planning consumer (read-only):

```python
# In a transition planner or render-worker splice prep:
cached = cache_manager.get_madmom_beats(content_hash)
if cached:
    beats_grid = cached["beats"]       # [[time, beat_in_bar], ...]
    downbeats = cached["downbeats"]    # [time, ...]
    # Backbeats (beats 2 & 4) for splice alignment:
    backbeats = [t for t, bib in beats_grid if bib in (2, 4)]
```

---

## Verification Checklist

- [ ] `MADMOM_BEATS_SCHEMA_VERSION` constant added to `cache.py`
- [ ] `CacheManager.get_madmom_beats` / `save_madmom_beats` implemented
- [ ] `R2Client.upload_madmom_beats` / `download_madmom_beats` implemented
- [ ] `_detect_downbeats_madmom` returns full grid dict (not just flat downbeats)
- [ ] `get_or_detect_madmom_beats` helper added with local→R2→detect→save flow
- [ ] `ComponentAnalysisOptions.skip_beat_cache` field added (default False)
- [ ] `queue.py` calls `get_or_detect_madmom_beats`, respects `skip_beat_cache`
- [ ] `extract_components` tier-2 path reads cached beats; inline
      `analyze_audio_fast` call removed
- [ ] Admin CLI `submit_component_analysis` service method passes the flag
- [ ] Admin CLI `components` command exposes `--skip-beat-cache` typer option
- [ ] Cache tests (get/save/schema-miss/corrupt) pass
- [ ] Component worker tests (cache hit / skip-cache / write-on-miss) pass
- [ ] Admin CLI flag-plumbing tests pass
- [ ] R2 round-trip test passes
- [ ] `uv run --project ops/admin-cli --extra admin pytest -v` (or component
      test subset) green
- [ ] `cd ops/analysis-service && uv run --extra dev pytest tests/ -v` green
