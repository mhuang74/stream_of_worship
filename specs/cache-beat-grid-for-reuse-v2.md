# Spec v2: Cache beat grid (`beat_grid.json`) for reuse across jobs

> **Date:** 2026-08-12
> **Branch:** TBD
> **Spec ID:** `cache-beat-grid-for-reuse`
> **Supersedes:** `specs/cache-madmom-downbeats-for-reuse.md` (v1 — retained unmodified for history)

---

## Deltas vs v1 spec (design review, 2026-08-12)

This v2 follows a full code review of v1 against the current codebase. Three design decisions were confirmed with the user; several factual errors in v1's references and test-plan claims were corrected.

| # | Change | Rationale |
|---|--------|-----------|
| 1 | **Detection stays gated on `snap_to_downbeat`** (v1 §3.2 proposed running madmom whenever `request.downbeats` is absent) | The ungated call would add full madmom RNN+DBN cost (~30–90 s/song) to every components job lacking cached downbeats — including default runs without `--snap-to-downbeat`, and every LRC-only song in batch backfills. v2 preserves today's zero default cost; the cache fills only where beats are actually requested. |
| 2 | **Artifact renamed `madmom_beats.json` → `beat_grid.json`**; payload gains `source: "madmom"`; `beats` becomes nullable | allin1 full analysis already produces beats/downbeats (`analyzer.py:294–333` → `analysis.json` → DB → `request.beats`/`request.downbeats`); madmom is only the fallback. A source-agnostic artifact keeps the contract usable if an allin1-derived grid (flat timestamps, no `beat_in_bar`) is ever written — in that case `beats` is `null` and `downbeats` is populated. |
| 3 | **Lazy population only** — no backfill flag, no beats-only mode | The cache fills on natural `--snap-to-downbeat` runs. Songs never re-analyzed simply have no entry; consumers fall back to allin1 flat lists (DB / `analysis.json`). |
| 4 | **Test plan corrected** | v1 §5.3 assumed existing components-command tests ("wherever the components command is tested") — none exist: `ops/admin-cli/tests/admin/test_audio_commands.py` and `test_analysis_client.py` have zero component coverage; only DB-layer tests exist (`test_song_components.py`). v1 §5.4 cited an "existing component-result round-trip test" in `tests/integration/test_r2.py` — none exists (no match for `download_component_result` there). v1 also missed the *third* `_submit_component_analysis_job` invocation (batch `--no-wait`, audio.py:2256–2266) and specified no queue-wiring tests. All fixed in Phase 5. |
| 5 | **Stale docstrings explicitly updated** | `ComponentAnalysisJobRequest` docstring (models.py:194–196) still claims the worker "will run analyze_audio_fast() inline to obtain them" — false today (`analyze_audio_fast` returns no beats, analyzer.py:545–553) and removed by this change. Also the v3 note in `submit_component_analysis` (analysis.py:587–589). |
| 6 | **Confidence-fallback semantics documented** as a deliberate (failure-mode-only) behavior change | See §3.4. |
| 7 | **`beats_grid` dead variable removed** from the queue snippet | v1 §3.2 assigned the grid and claimed it "is logged"; the code logged nothing. v2 simply doesn't retain the grid in queue scope. |
| 8 | **Cross-package consumer contract clarified** | v1's usage example called `cache_manager.get_madmom_beats(...)` from the render-worker — a separate package that must not import `sow_analysis`. §6 defines the R2 key + payload contract and source precedence instead. |
| 9 | **Line-reference corrections** | `_serialize_components` is at components.py:1362–1404 (v1 said 1349–1391); the cache-module import is components.py:25 (v1 said :24). |

### Verified facts (audit basis)

- `queue.py:963–984` — inline madmom call, gated by `request.options.snap_to_downbeat and not downbeats and _detect_downbeats_madmom is not None`.
- `components.py:34–75` — `_detect_downbeats_madmom` returns `Optional[list[float]]` (flat downbeats; the full DBN grid is discarded).
- `components.py:1362–1404` — `_serialize_components` does not persist downbeats.
- `analyzer.py:545–553` — `analyze_audio_fast` returns `{duration_seconds, tempo_bpm, **key_fields, loudness_db}` only; no beats/downbeats. The inline call at components.py:1277–1290 therefore always yields `beats=None, downbeats=None`.
- `components.py:1277, 1288, 1309–1312` — `inline_fast_ran` flag and confidence fallback.
- `cache.py:15, 150–175, 308–349` — `COMPONENT_SCHEMA_VERSION`, atomic-write pattern (`save_fast_analyze_result`), `get/save_component_result`.
- `r2.py:14, 447–512` — schema import; upload/download component result.
- `models.py:74–87, 185–206` — `ComponentAnalysisOptions`, `ComponentAnalysisJobRequest`.
- `analysis.py:566–631` — `submit_component_analysis` (options payload at 623–630).
- `audio.py` — helper signature 1942–1956; submit call 2043–2057; command 2153–2183; docstring 2185–2203; invocations at 2256–2266 (batch `--no-wait`), 2270–2279 (batch, wait), 2330–2338 (single).
- `ops/analysis-service/pyproject.toml:11` — madmom is a **hard** dependency (`git+https://github.com/CPJKU/madmom.git`), so import-failure fallback is a minor concern, not a design driver.
- `_snap_to_edit_point` (components.py:380–428) is dead code — no callers anywhere under `ops/`. All live snap sites (components.py:647–648, 897–903, 957–962) are gated on `snap_to_downbeat`. (v2 keeps the queue gate regardless — Decision 1.)

---

## Summary

The Analysis Service runs madmom downbeat detection inside the Component Analysis job but discards the result: `_detect_downbeats_madmom` (components.py:34–75) returns flat downbeats into a local variable in `_process_component_analysis_job` (queue.py:963–984), and they are never written to the cached `components.json` payload (`_serialize_components`, components.py:1362–1404).

Consequences:

1. Every `--force` re-run of Component Analysis with `--snap-to-downbeat` (and missing `request.downbeats`) re-runs the slow, CPU-heavy madmom RNN + DBN pipeline even though the audio hasn't changed.
2. Consumers that could use a beat grid (transition planning between chorus components, render-worker audio splice preparation) have no cached madmom output to fetch — they'd have to re-run detection.
3. The tier-2 lyrics-repetition path in `extract_components` calls `analyze_audio_fast()` inline hoping to populate beats (components.py:1277–1290), but that function returns neither beats nor downbeats (analyzer.py:545–553). The call's only real effect is warming the fast-analyze cache as a side effect.

This spec adds a dedicated cache artifact — `beat_grid.json` — keyed by `content_hash`, storing `source`, the full beat grid `[[time, beat_in_bar], ...]` when produced by madmom, plus the flattened downbeats list. The cache is consulted **even when `--force` is set** (the force flag invalidates only the component-result cache). A new `--skip-beat-cache` Admin CLI flag bypasses beat-cache reads for genuine re-detection (e.g. madmom upgrade).

Detection scope and population are deliberate non-changes: madmom runs only when `snap_to_downbeat` is requested and no downbeats were supplied; no backfill machinery is added (Decision 1, Decision 3).

---

## Decisions

| Question | Decision |
|---|---|
| Cache location | R2 + local CacheManager (mirrors existing `components.json` flow). |
| Artifact identity | Source-agnostic `beat_grid.json` with `source: "madmom"`; `beats` grid nullable for future non-madmom sources. Constant `BEAT_GRID_SCHEMA_VERSION = 1`. |
| Cache scope | Full grid `[[time, beat_in_bar], ...]` plus extracted flat `downbeats`. `beat_in_bar` enables backbeat (beats 2 & 4) reasoning. |
| Cache key | `content_hash` only — no parameter hashing; `beats_per_bar=[3,4]`, `fps=100` are pinned madmom constants, recorded in `madmom_params` for audit. If they ever change, bump `BEAT_GRID_SCHEMA_VERSION`. |
| Detection scope | **Unchanged**: madmom runs only when `options.snap_to_downbeat` is set AND `request.downbeats` is absent. No detection on default jobs. |
| `force` vs beat cache | `--force` invalidates `components.json` only. The beat cache is consulted regardless (the core win: no madmom re-run on forced component re-extraction). |
| `--skip-beat-cache` semantics | Orthogonal to `--force`: skips beat-cache **reads** only; fresh detection still **writes** the cache. Combinable. |
| Backfill | None — lazy population only. |
| Tier-2 reuse | `extract_components`'s tier-2 lyrics path reads the beat-grid cache instead of calling `analyze_audio_fast()` inline (which never returned beats). |

---

## Affected Components

| Component | Path | Change |
|---|---|---|
| Analysis Service cache | `ops/analysis-service/src/sow_analysis/storage/cache.py` | New `BEAT_GRID_SCHEMA_VERSION`, `get_beat_grid` / `save_beat_grid` (atomic) |
| Analysis Service R2 | `ops/analysis-service/src/sow_analysis/storage/r2.py` | New `upload_beat_grid` / `download_beat_grid` with schema check |
| Analysis Service models | `ops/analysis-service/src/sow_analysis/models.py` | `ComponentAnalysisOptions.skip_beat_cache: bool = False`; refresh stale `ComponentAnalysisJobRequest` docstring (194–196) |
| Component worker | `ops/analysis-service/src/sow_analysis/workers/components.py` | `_detect_downbeats_madmom` returns full-grid dict; new `get_or_detect_beat_grid` helper; tier-2 inline fast_analyze removed (cache read instead); docstring note 1216–1219 refreshed |
| Job queue | `ops/analysis-service/src/sow_analysis/workers/queue.py` | Replace lines 963–984 with gated helper call; extend import try/except at 101–106 |
| Admin CLI service | `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py` | `skip_beat_cache` param on `submit_component_analysis` (566–631); wire into options payload (623–630); docstring |
| Admin CLI command | `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | `--skip-beat-cache` typer option; thread through helper signature (1942–1956), submit call (~2052), and **all three** invocations (2256–2266, 2270–2279, 2330–2338) |
| Analysis Service tests | `ops/analysis-service/tests/test_cache.py` | New `TestBeatGridCache` class |
| Analysis Service tests | `ops/analysis-service/tests/test_components.py` | Helper + tier-2 reuse tests |
| Analysis Service tests | `ops/analysis-service/tests/test_queue_beat_grid.py` (NEW) | Queue-wiring tests (flag/gate matrix) |
| Analysis Service tests | `ops/analysis-service/tests/integration/test_r2.py` | Beat-grid R2 round-trip + schema-mismatch tests |
| Admin CLI tests | `ops/admin-cli/tests/admin/test_analysis_client.py` | First payload-coverage tests for `submit_component_analysis` |
| Admin CLI tests | `ops/admin-cli/tests/admin/test_audio_commands.py` (or helper-level) | `--skip-beat-cache` threading tests |

---

## Phase 1: New cache artifact — `beat_grid.json`

**Complexity:** M

### 1.1 Schema

File: `ops/analysis-service/src/sow_analysis/storage/cache.py`

Add alongside `COMPONENT_SCHEMA_VERSION` (cache.py:13–15):

```python
# Bump when the beat_grid.json payload shape changes. Cached payloads with a
# mismatched version are treated as cache misses and re-detected.
BEAT_GRID_SCHEMA_VERSION = 1
```

Payload shape:

```json
{
  "schema_version": 1,
  "source": "madmom",
  "content_hash": "<full sha-256>",
  "hash_prefix": "<first 12 chars>",
  "beats": [[0.464, 1], [0.928, 2], [1.392, 3], [1.856, 4], [2.320, 1], "..."],
  "downbeats": [0.464, 2.320, 4.176],
  "detected_at": "2026-08-12T12:34:56.789000+00:00",
  "madmom_params": {"beats_per_bar": [3, 4], "fps": 100}
}
```

Contract notes:

- `schema_version` — equality check against `BEAT_GRID_SCHEMA_VERSION` on every read; mismatch → treated as miss.
- `source` — detector identity; `"madmom"` is the only value this spec produces. Consumers MUST NOT assume it.
- `beats` — full grid as `[[time, beat_in_bar], ...]`; **nullable** (a future allin1-sourced entry would carry `beats: null` with flat `downbeats` only, since allin1 stores timestamps without bar position). `beat_in_bar == 1` marks downbeats; 2/3/4 mark intra-bar beats.
- `downbeats` — convenience extraction (`beat_in_bar == 1` rows), flattened `[time, ...]`. Always a list (possibly empty) when the artifact exists.
- `detected_at`, `madmom_params` — audit fields; `madmom_params` present only for `source == "madmom"`.
- Additive optional fields do NOT require a version bump (readers use `.get`); any shape change to existing fields does.

### 1.2 CacheManager methods

File: `ops/analysis-service/src/sow_analysis/storage/cache.py`

Mirror `get_component_result` / `save_component_result` (cache.py:308–349), but save **atomically** following `save_fast_analyze_result` (cache.py:150–175):

```python
def get_beat_grid(self, content_hash: str) -> Optional[dict]:
    """Local beat-grid cache lookup.

    Returns None on miss, corrupt JSON, OR schema_version != BEAT_GRID_SCHEMA_VERSION.
    """
    hash_prefix = self._get_hash_prefix(content_hash)
    cache_file = self.cache_dir / f"{hash_prefix}_beat_grid.json"
    if cache_file.exists():
        try:
            payload = json.loads(cache_file.read_text())
        except (json.JSONDecodeError, IOError):
            return None
        if payload.get("schema_version") != BEAT_GRID_SCHEMA_VERSION:
            return None
        return payload
    return None

def save_beat_grid(self, content_hash: str, payload: dict) -> Path:
    """Save beat grid atomically (temp file + os.replace).

    ``payload`` MUST include ``schema_version=BEAT_GRID_SCHEMA_VERSION``.
    """
    hash_prefix = self._get_hash_prefix(content_hash)
    cache_file = self.cache_dir / f"{hash_prefix}_beat_grid.json"
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

Local filename: `{hash32}_beat_grid.json` (sibling to `{hash32}_components.json`; local prefix is 32 chars via `_get_hash_prefix`, cache.py:31–33).

### 1.3 R2 methods

File: `ops/analysis-service/src/sow_analysis/storage/r2.py`

Mirror `upload_component_result` / `download_component_result` (r2.py:447–512):

```python
async def upload_beat_grid(self, hash_prefix: str, payload: dict) -> str:
    """Upload beat_grid.json to R2 at {hash_prefix}/beat_grid.json. Returns s3 URL."""
    # Same temp-file + run_in_executor pattern as upload_component_result.

async def download_beat_grid(self, hash_prefix: str) -> Optional[dict]:
    """Download beat_grid.json from R2.

    Returns None if not found, corrupt, OR schema_version != BEAT_GRID_SCHEMA_VERSION.
    """
    # Same download + schema check pattern as download_component_result.
```

R2 key: `{hash_prefix}/beat_grid.json` (sibling to `{hash_prefix}/components.json`; R2 prefix is 12 chars — `content_hash[:12]`, per components.py:1224).

Extend the r2.py:14 import to also pull `BEAT_GRID_SCHEMA_VERSION` from `.cache`.

---

## Phase 2: Full-grid detection + cached helper

**Complexity:** S

File: `ops/analysis-service/src/sow_analysis/workers/components.py`

### 2.1 `_detect_downbeats_madmom` returns the grid dict

Keep the function name (minimal churn) but change the return type from `Optional[list[float]]` to `Optional[dict]` (components.py:34–75):

```python
def _detect_downbeats_madmom(audio_path: Path) -> Optional[dict]:
    """Detect beats + downbeats via madmom's two-stage pipeline.

    Returns a partial beat-grid payload: source/beats/downbeats/detected_at/
    madmom_params (identity fields are stamped by get_or_detect_beat_grid,
    which knows the content hash). None if detection fails.
    """
    try:
        from madmom.features.downbeats import (
            RNNDownBeatProcessor,
            DBNDownBeatTrackingProcessor,
        )

        rnn = RNNDownBeatProcessor()
        activations = rnn(str(audio_path))
        dbn = DBNDownBeatTrackingProcessor(beats_per_bar=[3, 4], fps=100)
        grid = dbn(activations)  # (num_beats, 2): [time, beat_in_bar]

        downbeat_times = grid[grid[:, 1] == 1][:, 0].tolist()
        return {
            "source": "madmom",
            "beats": grid.tolist(),
            "downbeats": sorted(downbeat_times),
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "madmom_params": {"beats_per_bar": [3, 4], "fps": 100},
        }
    except Exception as e:
        logger.warning(f"madmom downbeat detection failed: {e}")
        return None
```

Add `from datetime import datetime, timezone` (components.py currently imports `time` only, line 16).

### 2.2 New cached helper: `get_or_detect_beat_grid`

Single entry point for all callers:

```python
async def get_or_detect_beat_grid(
    audio_path: Path,
    content_hash: str,
    cache_manager: CacheManager,
    r2_client: Optional[R2Client],
    skip_beat_cache: bool = False,
) -> Optional[dict]:
    """Return the cached beat grid, detecting + caching on miss.

    Read order (skipped entirely when skip_beat_cache=True):
      1. Local cache ({hash32}_beat_grid.json)
      2. R2 ({hash12}/beat_grid.json) — on hit, backfill local cache

    On miss: run _detect_downbeats_madmom in an executor, stamp identity
    fields, persist local (atomic) + R2 (best-effort; failure logs a warning).
    Returns the payload dict or None if detection fails.
    """
    hash_prefix = content_hash[:12]

    if not skip_beat_cache:
        cached = cache_manager.get_beat_grid(content_hash)
        if cached is not None:
            logger.info(f"Beat grid cache hit (local): {content_hash[:16]}...")
            return cached
        if r2_client is not None:
            r2_cached = await r2_client.download_beat_grid(hash_prefix)
            if r2_cached is not None:
                logger.info(f"Beat grid cache hit (R2): {content_hash[:16]}...")
                cache_manager.save_beat_grid(content_hash, r2_cached)
                return r2_cached

    loop = asyncio.get_event_loop()
    detected = await loop.run_in_executor(None, _detect_downbeats_madmom, audio_path)
    if detected is None:
        return None

    detected["content_hash"] = content_hash
    detected["hash_prefix"] = hash_prefix
    detected["schema_version"] = BEAT_GRID_SCHEMA_VERSION

    cache_manager.save_beat_grid(content_hash, detected)

    if r2_client is not None:
        try:
            await r2_client.upload_beat_grid(hash_prefix, detected)
        except Exception as e:
            logger.warning(f"Failed to upload beat_grid.json to R2: {e}")

    return detected
```

Extend the components.py:25 import to also pull `BEAT_GRID_SCHEMA_VERSION` from `..storage.cache`.

---

## Phase 3: Wire into the Component Analysis job (gate preserved)

**Complexity:** M

### 3.1 Model changes

File: `ops/analysis-service/src/sow_analysis/models.py`

1. Add to `ComponentAnalysisOptions` (models.py:74–87), after `snap_to_downbeat`:

```python
    # v6: bypass READING the cached beat grid (re-detect + overwrite).
    # Orthogonal to `force`: `force` invalidates components.json;
    # `skip_beat_cache` invalidates beat_grid.json reads only.
    # Fresh detection still WRITES the beat cache.
    skip_beat_cache: bool = False
```

2. Refresh the stale `ComponentAnalysisJobRequest` docstring (models.py:194–196). Replace the v3 claim:

> v3: If ``beats``/``downbeats`` are absent (tier-2 only), the worker will run analyze_audio_fast() inline to obtain them.

with:

> v6: If ``downbeats`` are absent and ``options.snap_to_downbeat`` is set, the
> worker resolves them via the beat-grid cache (``{hash12}/beat_grid.json``),
> running madmom detection only on cache miss. ``analyze_audio_fast`` is never
> run inline for beats — it does not produce them.

No change to the request schema itself — the new option rides inside `options`.

### 3.2 Queue: gated helper call

File: `ops/analysis-service/src/sow_analysis/workers/queue.py`

Extend the component import try/except (queue.py:101–106):

```python
try:
    from .components import (
        ComponentInstance,
        extract_components,
        _detect_downbeats_madmom,
        get_or_detect_beat_grid,
    )
except ImportError:
    extract_components = None
    ComponentInstance = None
    _detect_downbeats_madmom = None
    get_or_detect_beat_grid = None
```

(`_detect_downbeats_madmom` remains imported because nothing else needs removing, and the existing `is not None` guard pattern is preserved. It may alternatively be dropped from this import block once the helper supersedes it — implementer's choice; keep the diff minimal.)

Replace queue.py:963–984 with:

```python
# v6: Downbeats come from the beat-grid cache (or madmom detection on miss).
# The beat grid is a pure function of audio bytes, cached separately from
# components.json, so this consults the cache even when options.force is set.
# Detection scope is unchanged: only when snap_to_downbeat is requested.
downbeats = request.downbeats
if (
    request.options.snap_to_downbeat
    and not downbeats
    and get_or_detect_beat_grid is not None
):
    with step_timer("Beat grid lookup / madmom detection", logger):
        audio_size_mb = audio_path.stat().st_size / (1024 * 1024)
        logger.info(f"Beat grid: processing {audio_size_mb:.1f}MB audio file")
        beat_grid = await get_or_detect_beat_grid(
            audio_path=audio_path,
            content_hash=request.content_hash,
            cache_manager=self.cache_manager,
            r2_client=self.r2_client,
            skip_beat_cache=request.options.skip_beat_cache,
        )
    if beat_grid and beat_grid.get("downbeats"):
        downbeats = beat_grid["downbeats"]
    else:
        logger.warning(
            "No downbeats available (cache miss + detection failed); "
            "using beat snapping only"
        )
```

`downbeats` is passed to `extract_components` at queue.py:995 exactly as before. The full grid is intentionally NOT kept in queue scope; future consumers read it from the cache.

**Behavior matrix:**

| `options.snap_to_downbeat` | `request.downbeats` | `options.skip_beat_cache` | `options.force` | Behavior |
|---|---|---|---|---|
| False | any | any | any | Helper not called. **Identical to today** — no madmom cost, no beat-cache I/O. |
| True | provided | any | any | Madmom/cache skipped entirely (caller supplied downbeats from DB). Same as today. |
| True | absent | False | False | local cache → R2 → detect+cache. Components served from component cache unless .../force applies. |
| True | absent | False | True | Beat grid from cache (or detect on miss) — **madmom NOT re-run just because --force was passed** (the core win). Components re-extracted. |
| True | absent | True | any | Beat-cache reads skipped; detection runs; fresh result written to cache. |

### 3.3 Tier-2 lyrics-repetition path: read cache, drop inline fast_analyze

File: `ops/analysis-service/src/sow_analysis/workers/components.py`

Replace the inline block (components.py:1277–1290):

```python
if not beats and not downbeats:
    # v6: prefer the beat-grid cache. The old inline analyze_audio_fast call
    # never returned beats/downbeats (analyzer.py:545–553); dropping it only
    # forfeits its fast-cache warm-up side effect (accepted — see Risks).
    cached_grid = cache_manager.get_beat_grid(content_hash)
    if cached_grid is not None:
        downbeats = cached_grid.get("downbeats")
        # The full grid (cached_grid["beats"]) is not consumed by
        # identify_from_lyrics_repetition, which takes flat timestamps.
```

Remove the `inline_fast_ran` variable entirely.

Update the docstring note at components.py:1216–1219 to:

> Note: `analyze_audio_fast()` does NOT return beats/downbeats and is never
> called from this function. Downbeats are expected from the caller (queue
> populates them via the beat-grid cache when snap_to_downbeat is set); the
> tier-2 lyrics path additionally reads the beat-grid cache directly as
> defense-in-depth.

### 3.4 Confidence fallback — documented behavior change

components.py:1309–1312 currently lowers `c.confidence = 0.5` when `not beats and not inline_fast_ran`. Because `analyze_audio_fast` never returns beats, the old condition reduced to: "lower only if beats were never supplied AND the inline call raised". Since `inline_fast_ran` is gone, the condition becomes:

```python
if not downbeats:
    for c in components:
        c.confidence = 0.5
```

Deliberate deltas, both confined to the tier-2 (lyrics_repetition) source:

- **Fix:** today, lyrics-tier songs WITH downbeats but WITHOUT beats get 0.5 (inline_fast_ran stays False when downbeats were supplied). After: not lowered.
- **Accepted regression (failure mode only):** songs WITH request beats (`request.beats` from allin1) but NO downbeats where madmom also failed — today not lowered (inline ran without raising); after: lowered to 0.5. Requires madmom runtime failure (madmom is a hard dependency, pyproject.toml:11) plus missing allin1 downbeats; rare, and the 0.5 signal is arguably more honest there.

---

## Phase 4: Admin CLI `--skip-beat-cache`

**Complexity:** S

### 4.1 Service client

File: `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`

Add `skip_beat_cache: bool = False` to `submit_component_analysis` (signature at 566–581; place it after `snap_to_downbeat` for readability), include in the options payload (623–630):

```python
"skip_beat_cache": skip_beat_cache,
```

Docstring: add the arg description, and refresh the v3 note (587–589) so it no longer implies the worker re-computes beats inline.

### 4.2 Command

File: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

Add near `--force` (2156):

```python
skip_beat_cache: bool = typer.Option(
    False,
    "--skip-beat-cache",
    help=(
        "Bypass cached beat grid; re-run madmom detection (still writes the "
        "fresh result to cache). Orthogonal to --force."
    ),
),
```

Thread through:

- `_submit_component_analysis_job` signature (1942–1956) — add `skip_beat_cache: bool = False`.
- The `client.submit_component_analysis(...)` call (~2043–2057) — pass it.
- **All three** invocations of the helper: batch `--no-wait` (2256–2266), batch wait (2270–2279), single-song (2330–2338). (v1 missed the first.)

Explicitly **NOT** included in `--compute-all-fields` (2204–2209): that shortcut enables extraction features, not cache-control overrides.

Update the command docstring (2185–2203):

> --skip-beat-cache bypasses the cached beat grid and re-runs madmom detection
> (the fresh result is still written to cache). Unlike --force (which re-runs
> component extraction), this affects only beat detection. Combine both to
> re-run everything from scratch. Neither flag is implied by
> --compute-all-fields.

---

## Phase 5: Tests

**Complexity:** M

### 5.1 Cache tests

File: `ops/analysis-service/tests/test_cache.py`

New `TestBeatGridCache` (mirror the style of `TestWhisperTranscriptionCache`, test_cache.py:13+):

- `test_save_and_get_beat_grid` — round trip.
- `test_get_returns_none_before_save` — miss.
- `test_schema_version_mismatch_treated_as_miss` — payload with `schema_version=99`.
- `test_corrupt_json_returns_none` — invalid JSON on disk.

### 5.2 Component worker tests

File: `ops/analysis-service/tests/test_components.py`

- `test_helper_local_hit_short_circuits` — pre-seed `save_beat_grid`; monkeypatch `_detect_downbeats_madmom` to raise if called; assert payload returned.
- `test_helper_r2_hit_backfills_local` — empty local; stub `r2_client.download_beat_grid`; assert local file exists afterward.
- `test_helper_miss_detects_and_persists` — monkeypatch detection; assert `save_beat_grid` write (with `schema_version`, `content_hash`, `hash_prefix`, `source`) and R2 upload attempted.
- `test_helper_skip_beat_cache_runs_detection_and_overwrites` — cache seeded, `skip_beat_cache=True`; detection called; cache overwritten with new content.
- `test_helper_detection_failure_returns_none_no_write` — detection returns None; helper returns None; no local file created.
- `test_tier2_uses_cached_beat_grid` — drive `extract_components` into the lyrics path with a seeded grid; assert downbeats reach `identify_from_lyrics_repetition`; assert `analyze_audio_fast` is never imported (monkeypatch guard).
- `test_tier2_confidence_zero_without_downbeats` / `..._unchanged_with_downbeats` — §3.4 semantics.

At implementation time, grep test_components.py for existing `inline_fast_ran` / `analyze_audio_fast` mocks and update them accordingly.

### 5.3 Queue-wiring tests (NEW file)

File: `ops/analysis-service/tests/test_queue_beat_grid.py` (follow the mocking style of `tests/test_queue_persistence.py`)

Exercise `_process_component_analysis_job` with `get_or_detect_beat_grid` and `extract_components` monkeypatched:

- Helper called when `snap_to_downbeat=True` and no `request.downbeats`.
- Helper NOT called when `snap_to_downbeat=False` (regression guard for Decision 1).
- Helper NOT called when `request.downbeats` provided.
- `skip_beat_cache` forwarded from options.
- `force=True` still consults the beat cache (assert helper called with `skip_beat_cache=False`).
- Helper returning None → `extract_components` receives `downbeats=None`, job still completes.

### 5.4 Admin CLI tests

These create NEW coverage where none exists today (see Deltas #4):

- `ops/admin-cli/tests/admin/test_analysis_client.py` — mock `requests.post`; assert `submit_component_analysis(..., skip_beat_cache=True)` sends `options.skip_beat_cache == True`, and default sends `False`.
- `ops/admin-cli/tests/admin/test_audio_commands.py` (or a focused helper-level test if command-level fixtures prove heavy) — assert `_submit_component_analysis_job(..., skip_beat_cache=True)` forwards the flag to `client.submit_component_analysis`, and that both batch-mode invocation sites pass the command's flag value.

### 5.5 R2 integration tests

File: `ops/analysis-service/tests/integration/test_r2.py`

There is NO pre-existing component-result round-trip to mirror (v1 §5.4 was inaccurate). Add, following this file's existing R2 mocking pattern:

- `test_upload_download_beat_grid_round_trip`
- `test_download_beat_grid_schema_mismatch_returns_none`
- `test_download_beat_grid_missing_returns_none`

---

## Backward Compatibility

- **No migration.** `beat_grid.json` is a new artifact; existing songs miss and detect on their next `--snap-to-downbeat` run. No DB schema change.
- **API: no breaking change.** `skip_beat_cache` defaults to `False`; existing POST payloads behave as before — except `--force --snap-to-downbeat` runs stop re-running madmom when a cached grid exists (an improvement, not a regression).
- **CLI: no breaking change.** New flag is optional; not part of `--compute-all-fields`.
- **Default job cost: unchanged.** Detection remains gated on `snap_to_downbeat` (Decision 1).
- **Schema versioning:** `BEAT_GRID_SCHEMA_VERSION = 1`; mismatched payloads are misses (same pattern as `COMPONENT_SCHEMA_VERSION`, cache.py:15 + 328).

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Stale cache when audio content changes | Keyed by `content_hash` (SHA-256 of audio bytes). Different audio → different key. Safe by construction. |
| madmom params change in future | Params recorded in `madmom_params` for audit; bump `BEAT_GRID_SCHEMA_VERSION` to force global re-detection. |
| Dropping inline `analyze_audio_fast` forfeits its fast-cache warm-up side effect | Accepted: the call never returned beats; the fast tier seeds itself on demand via `sow-admin audio analyze --analysis-tier fast`. No consumer depends on components jobs warming the fast cache. |
| §3.4 confidence-fallback edge (madmom failure + allin1 beats present, no downbeats) now lowers to 0.5 | Rare failure mode only (madmom is a hard dep); surfaced via existing warning logs; arguably the correct signal. Documented deliberately. |
| `--force` no longer re-runs madmom | Intended. `--skip-beat-cache` is the escape hatch (madmom library upgrade, corrupted detector output). |
| Concurrent jobs writing the same grid | Atomic local write (`os.replace`); R2 last-writer-wins with byte-identical content for identical audio. Safe. |
| Consumer deserializes an unexpected `source` or `beats: null` | Contract (§1.1, §6) requires consumers to check `source` and tolerate null `beats`. Version bump if shape changes. |
| R2 storage growth (~15–30 KB JSON per snapped song) | Negligible vs `components.json`; same-prefix lifecycle. |
| Detection unavailable at runtime (bad audio, madmom error) | Helper returns None; job continues with beat snapping only (identical to today's failure path). |

---

## Usage Examples

### Standard snap-to-downbeat run (beat cache filled/used):

```bash
uv run --project ops/admin-cli --extra admin sow-admin audio components SONG_001 --snap-to-downbeat
# → beat_grid.json: local → R2 → detect+cache; components.json as today
```

### Forced re-extraction (madmom skipped via beat cache):

```bash
uv run --project ops/admin-cli --extra admin sow-admin audio components SONG_001 --force --snap-to-downbeat
# → components.json re-extracted; beat_grid.json cache HIT, detection skipped
```

### Genuine re-detection (madmom upgraded):

```bash
uv run --project ops/admin-cli --extra admin sow-admin audio components SONG_001 --force --skip-beat-cache --snap-to-downbeat
# → beat-cache read skipped; detection runs; fresh grid written to cache
```

### Future transition-planning / render-worker consumer (contract-level)

Consumers outside `sow_analysis` (e.g. `delivery/render-worker`) must NOT import `sow_analysis`; they read R2 directly using the published contract:

```python
# Artifact contract:
#   R2 key:  {hash_prefix_12}/beat_grid.json   (local mirror: {hash32}_beat_grid.json)
#   payload: schema_version, source, content_hash, hash_prefix,
#            beats|null (grid with beat_in_bar), downbeats (flat),
#            detected_at, madmom_params (madmom sources only)
# Suggested precedence when any beat grid will do:
#   1. beat_grid.json in R2 (beat_in_bar available when source == "madmom")
#   2. recordings.beats / recordings.downbeats (DB, allin1 flat lists)
import json
import boto3  # render-worker already vendors AWS SDK family

s3 = boto3.client("s3", endpoint_url=R2_ENDPOINT)
try:
    obj = s3.get_object(Bucket=R2_BUCKET, Key=f"{hash_prefix}/beat_grid.json")
except s3.exceptions.NoSuchKey:
    grid = None  # fall back to DB allin1 downbeats
else:
    grid = json.loads(obj["Body"].read())
    assert grid["schema_version"] == 1
    downbeats = grid["downbeats"]
    if grid["beats"]:  # null for non-madmom sources
        backbeats = [t for t, bib in grid["beats"] if bib in (2, 4)]
```

---

## Verification Checklist

- [ ] `BEAT_GRID_SCHEMA_VERSION` added to cache.py
- [ ] `CacheManager.get_beat_grid` / `save_beat_grid` implemented (atomic save)
- [ ] `R2Client.upload_beat_grid` / `download_beat_grid` implemented (schema check on download)
- [ ] `_detect_downbeats_madmom` returns full-grid dict with `source="madmom"`
- [ ] `get_or_detect_beat_grid` helper (local → R2 → detect → save local → upload R2)
- [ ] `ComponentAnalysisOptions.skip_beat_cache` added (default False)
- [ ] `ComponentAnalysisJobRequest` docstring refreshed (models.py:194–196)
- [ ] queue.py: helper imported in try/except; call gated on `snap_to_downbeat and not downbeats`; `skip_beat_cache` forwarded; `force` has no effect on beat-cache reads
- [ ] components.py tier-2: inline `analyze_audio_fast` removed; beat-grid cache read added; `inline_fast_ran` removed; confidence condition `if not downbeats`; docstring note 1216–1219 updated
- [ ] Admin CLI `submit_component_analysis` passes the flag; docstring refreshed
- [ ] Admin CLI `components` command: `--skip-beat-cache` option; threaded through helper + **all three** call sites; NOT in `--compute-all-fields`; docstring updated
- [ ] Cache tests (round trip / miss / schema mismatch / corrupt) pass
- [ ] Component worker tests (hit / R2 backfill / miss-persist / skip / failure / tier-2 reuse / confidence) pass
- [ ] Queue-wiring tests (gate matrix incl. snap=False regression guard, force interplay) pass
- [ ] Admin CLI payload + flag-threading tests pass
- [ ] R2 integration tests (round trip / schema mismatch / missing) pass
- [ ] `cd ops/analysis-service && uv run --extra dev pytest tests/ -v` green
- [ ] `uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest -v` green
