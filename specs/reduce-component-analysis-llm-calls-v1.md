# Implementation Plan: Reduce Component Analysis LLM Calls (v1)

> **Date:** 2026-08-12
> **Branch:** TBD
> **Spec ID:** `reduce-component-analysis-llm-calls-v1`

---

## Problem

A component analysis job for a song with many detected chorus components
spends excessive time on LLM theme/posture classification and per-component
audio feature extraction. Transitions only need the first chorus (in-bound),
last chorus (outbound), and first verse (replay song). All other detected
components' audio-metadata and LLM-based theme tags are unnecessary by
default.

### Evidence (from production logs)

```
LLM classification: 17 components to classify
LLM classification: starting component 1/17 (occurrence=1, type=chorus)
LLM classification: starting component 2/17 (occurrence=2, type=chorus)
...
LLM classification: starting component 17/17 (occurrence=1, type=verse)
```

For a typical song, 16 chorus repetitions are classified when only the first
(entry) and last (exit) chorus — plus the first verse (loop_target) — are
needed for transitions. Each LLM call incurs network + model latency.

### Additional waste: identical-lyric chorus repetitions

Chorus occurrences within a song typically share **identical lyrics**. Yet each
occurrence triggers a separate LLM call returning identical theme/posture.
There is no deduplication.

---

## Goal

Reduce component-analysis job LLM cost by:

1. **Selective population (default):** Skip audio-metadata + LLM classification
   for non-essential components (those whose `role == "none"`). Essential
   components — `entry`, `exit`, `loop_target`, `entry_exit` — always get
   populated.
2. **LLM lyric-hash deduplication:** Classify each unique lyric content once;
   copy the result to duplicate occurrences (applies to all component types).
3. **`--all-components` flag:** Opt-in to populate all components (current
   behavior) for backfill / debugging.

---

## Design Decisions (from clarifying questions)

| Decision | Choice |
|---|---|
| Essential components | roles ∈ {`entry`, `exit`, `loop_target`, `entry_exit`}. `entry_exit` (single-chorus song) is essential since it represents both first AND last chorus. |
| Skipped component rows | **Kept** in the result set with detection-only fields (`component_type`, `occurrence_index`, `role`, `start_time`, `end_time`, `source`, `confidence`) populated; audio-metadata + LLM fields stay `None`. No `metadata_skipped` flag field. |
| Flag name | API field `all_components: bool = False`; CLI flag `--all-components`. |
| LLM dedup strategy | **Skip non-essential + LLM lyric dedup** (both on by default). |
| Dedup scope | All component types (not just chorus). |
| Cache schema | Keep cache compatible (no `COMPONENT_SCHEMA_VERSION` bump). Existing cached `components.json` rows remain valid. The flag only affects fresh jobs. Users can pass `--force` to re-run. |

### Essential-component definition

```python
ESSENTIAL_ROLES = frozenset({"entry", "exit", "loop_target", "entry_exit"})

def _is_essential(component: ComponentInstance) -> bool:
    return component.role in ESSENTIAL_ROLES
```

Rationale: the transition engine anchors on the first chorus (in-bound
`entry`), last chorus (outbound `exit`), and first verse before the chorus
(`loop_target`). `entry_exit` is the single-chorus-song special case where one
row serves as both first AND last chorus — skipping it would leave a
single-chorus song with zero populated components.

---

## Phase 1: Add `all_components` option across the stack

**Complexity:** M

### 1.1 Analysis Service — `ComponentAnalysisOptions`

**File:** `ops/analysis-service/src/sow_analysis/models.py:74-87`

Add new field to `ComponentAnalysisOptions`:

```python
class ComponentAnalysisOptions(BaseModel):
    """Options for component analysis jobs."""

    force: bool = False
    use_stems: bool = False
    snap_to_downbeat: bool = False
    energy_aware_roles: bool = False
    classify_theme: bool = False
    classify_vocal_posture: bool = False
    # v6: populate audio-metadata + LLM fields for ALL components.
    # Default False: only essential roles (entry/exit/loop_target/entry_exit)
    # get audio + LLM; non-essential rows are kept but with NULL fields.
    all_components: bool = False
```

### 1.2 Analysis Service worker — thread `all_components` to `extract_components`

**File:** `ops/analysis-service/src/sow_analysis/workers/components.py:1264-1277`

Add `all_components: bool = False` parameter to `extract_components()`.

Define a module-level constant and helper near the other module constants
(e.g. after `_CHORUS_KEYWORDS` at `components.py:150-163`):

```python
# v6: components whose roles are transition-relevant. Only these get
# audio-metadata + LLM fields populated by default. The --all-components
# flag overrides this.
ESSENTIAL_ROLES = frozenset({"entry", "exit", "loop_target", "entry_exit"})


def _is_essential(component: "ComponentInstance") -> bool:
    return component.role in ESSENTIAL_ROLES
```

At the feature-loop site (`components.py:1398-1416`), gate per-component
feature computation on essential-ness when `all_components` is False:

```python
features_start = time.time()
last_heartbeat = time.time()
computed_count = 0
skipped_count = 0
for i, component in enumerate(components, 1):
    if not all_components and not _is_essential(component):
        skipped_count += 1
        continue
    compute_component_features(gf, component, beats=beats, downbeats=downbeats)
    computed_count += 1
    now = time.time()
    if now - last_heartbeat >= settings.SOW_STEP_HEARTBEAT_INTERVAL_SECONDS:
        elapsed = now - features_start
        logger.info(
            f"Feature computation heartbeat: {i}/{len(components)} "
            f"({computed_count} computed, {skipped_count} skipped, "
            f"{elapsed:.1f}s elapsed)"
        )
        last_heartbeat = now
logger.info(
    f"Per-component feature computation completed in "
    f"{time.time() - features_start:.2f}s "
    f"({computed_count} computed, {skipped_count} skipped, "
    f"{len(components)} total)"
)
```

Notes:
- Role assignment (`_assign_roles_by_energy`) runs **before** this loop
  (`components.py:1391-1394`) and uses `gf.y` directly, NOT per-component
  features. So skipping non-essential per-component features does not affect
  role assignment.
- Even when role assignment runs without `energy_aware_roles`, roles are
  already set during identification (`identify_from_allin1_sections` /
  `identify_from_lyrics_repetition`), so `_is_essential()` is safe to call
  here.
- Rows are still created (the `ComponentInstance` already exists from
  identification). Their audio fields stay `None` by default-attribute init
  on the `@dataclass`.

### 1.3 Analysis Service worker — queue.py orchestration

**File:** `ops/analysis-service/src/sow_analysis/workers/queue.py`

In `_process_component_analysis_job`:

- Pass `all_components=request.options.all_components` to the
  `extract_components(...)` call (`queue.py:987-1000`).
- Pass `all_components=request.options.all_components` to
  `classifier.classify_components(...)` (`queue.py:1012-1015`).

### 1.4 Analysis Service worker — `classify_components()` selective + dedup

**File:** `ops/analysis-service/src/sow_analysis/workers/classifier.py:170-206`

Rewrite `classify_components()` to:

1. Build the candidate list:
   - If `all_components=True`: candidates = all components.
   - Else: candidates = components whose `role in ESSENTIAL_ROLES`.
2. Pre-extract per-candidate lyrics via the existing
   `_extract_lyrics_for_component()` (`classifier.py:104-132`).
3. Group candidates by lyric-content hash (normalized: lowercased + whitespace
   collapsed + stripped). Each group has one **representative** that gets an
   LLM call; duplicates copy from the representative's result.
4. Run `asyncio.gather` over representative classifications using the existing
   `_classify_component_with_logging()` wrapper.
5. After gather, copy fields (`theme`, `vocal_posture`, `theme_confidence`,
   `vocal_posture_confidence`, `theme_reasoning`, `posture_reasoning`) from
   each representative to its duplicates.
6. Log skips (`"skipped non-essential component X/Y (role=none)"`) and dedup
   hits (`"dedup hit: component X copied from component Y (lyric_hash=...)"`).

Signature:

```python
async def classify_components(
    self,
    components: list[ComponentInstance],
    lrc_content: Optional[str] = None,
    all_components: bool = False,
) -> list[ComponentInstance]:
```

Dedup hash function (module-level helper near `_extract_lyrics_for_component`):

```python
import hashlib


def _lyric_hash(lyrics_lines: Optional[list[str]]) -> str:
    """Normalized content hash for lyric deduplication.

    Lowercases, collapses whitespace, and strips each line before hashing.
    Returns a stable hex digest; empty/None input returns a fixed sentinel
    so all empty-lyric components collapse to one representative LLM call.
    """
    if not lyrics_lines:
        return "EMPTY"
    normalized = " ".join(
        " ".join(line.lower().split()) for line in lyrics_lines if line.strip()
    )
    if not normalized:
        return "EMPTY"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
```

Sketch of the rewritten `classify_components`:

```python
async def classify_components(self, components, lrc_content=None, all_components=False):
    total = len(components)

    # 1. Select candidates.
    candidates = []
    skipped = []
    for i, comp in enumerate(components, 1):
        if all_components or _is_essential(comp):
            candidates.append((i, comp))
        else:
            skipped.append((i, comp))

    logger.info(
        f"LLM classification: {len(candidates)} to classify, "
        f"{len(skipped)} skipped (essential-only), {total} total"
    )
    for i, comp in skipped:
        logger.info(
            f"LLM classification: skipped component {i}/{total} "
            f"(occurrence={comp.occurrence_index}, type={comp.component_type}, "
            f"role={comp.role})"
        )

    # 2. Pre-extract lyrics + group by lyric hash.
    groups: dict[str, list[tuple[int, ComponentInstance, Optional[list[str]]]]] = {}
    for i, comp in candidates:
        lyrics_lines = None
        if lrc_content and comp.start_time is not None and comp.end_time is not None:
            lyrics_lines = _extract_lyrics_for_component(
                lrc_content, comp.start_time, comp.end_time
            )
        h = _lyric_hash(lyrics_lines)
        groups.setdefault(h, []).append((i, comp, lyrics_lines))

    # 3. Classify one representative per group.
    rep_tasks = []
    rep_index: dict[str, tuple[int, ComponentInstance]] = {}
    for h, members in groups.items():
        rep_i, rep_comp, rep_lyrics = members[0]
        rep_index[h] = (rep_i, rep_comp)
        rep_tasks.append(
            self._classify_component_with_logging(rep_i, total, rep_comp, rep_lyrics)
        )

    logger.info(
        f"LLM classification: {len(rep_tasks)} unique lyric groups "
        f"(deduped from {len(candidates)} candidates)"
    )

    results = await asyncio.gather(*rep_tasks, return_exceptions=True)
    for i, result in enumerate(results, 1):
        if isinstance(result, Exception):
            logger.warning(f"LLM classification: representative {i} failed: {result}")

    # 4. Copy representative result to duplicates.
    for h, members in groups.items():
        if len(members) <= 1:
            continue
        _, rep_comp, _ = members[0]
        for j, dup_comp, _ in members[1:]:
            dup_comp.theme = rep_comp.theme
            dup_comp.vocal_posture = rep_comp.vocal_posture
            dup_comp.theme_confidence = rep_comp.theme_confidence
            dup_comp.vocal_posture_confidence = rep_comp.vocal_posture_confidence
            dup_comp.theme_reasoning = rep_comp.theme_reasoning
            dup_comp.posture_reasoning = rep_comp.posture_reasoning
            logger.info(
                f"LLM classification: dedup hit — component {j}/{total} "
                f"copied from component {rep_index[h][0]}/{total} "
                f"(lyric_hash={h})"
            )

    return components
```

Why all component types (not just chorus): verses and bridges may also repeat
identically. The user explicitly chose all-types dedup. The dedup is safe —
identical lyric content → identical theme/posture by definition of the
classification task (theme and posture are lyric-driven properties).

`_is_essential` can be imported from `components.py` (defined in 1.2) or
re-defined locally in `classifier.py`. Importing from `components.py` is
preferred to avoid duplication — but `classifier.py` currently does not import
from `components.py`. To keep the dependency direction clean (workers may
cross-import), import `ESSENTIAL_ROLES` from `components`:

```python
from .components import ESSENTIAL_ROLES

def _is_essential(component: ComponentInstance) -> bool:
    return component.role in ESSENTIAL_ROLES
```

### 1.5 Admin CLI client — `submit_component_analysis()`

**File:** `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py:566-631`

Add `all_components: bool = False` parameter; include in payload `options`
dict:

```python
"options": {
    "force": force,
    "snap_to_downbeat": snap_to_downbeat,
    "energy_aware_roles": energy_aware_roles,
    "use_stems": use_stems,
    "classify_theme": classify_theme,
    "classify_vocal_posture": classify_vocal_posture,
    "all_components": all_components,
},
```

### 1.6 Admin CLI command — `_submit_component_analysis_job()` helper

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:1942-1996`

Add `all_components: bool = False` parameter; forward to
`AnalysisClient.submit_component_analysis(..., all_components=all_components)`.

### 1.7 Admin CLI command — `components_recording` Typer flag

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:2153-2209`

Add Typer option:

```python
all_components: bool = typer.Option(
    False,
    "--all-components",
    help="Populate audio + LLM metadata for ALL detected components "
    "(default: only essential roles entry/exit/loop_target/entry_exit).",
)
```

Update the `--compute-all-fields` shortcut block (`audio.py:2204-2209`) to
**also** set `all_components = True` when `--compute-all-fields` is used,
since `--compute-all-fields` is the explicit "expensive full backfill"
shortcut:

```python
if compute_all_fields:
    snap_to_downbeat = True
    energy_roles = True
    classify_theme = True
    classify_posture = True
    all_components = True
```

Document this in the command docstring (`audio.py:2195-2197`): "`--compute-all-fields`
also implies `--all-components`."

Forward `all_components=all_components` to `_submit_component_analysis_job(...)`.

---

## Phase 2: Tests

**Complexity:** M

### 2.1 Analysis Service — `tests/test_classifier.py`

Add tests (mock `call_llm_with_retry` per existing test patterns in the file):

- **`test_classify_components_skips_non_essential`**: 5 components (2 with
  `role='none'`, 3 essential). With `all_components=False`, only 3 LLM calls
  fire; the 2 `role='none'` rows retain `theme=None`.
- **`test_classify_components_all_components_flag`**: Same fixture; with
  `all_components=True`, all 5 get LLM calls.
- **`test_classify_components_lyric_dedup`**: 3 essential chorus components
  with identical lyrics. Only 1 LLM call fires; the other 2 inherit the same
  `theme`/`vocal_posture`/confidences/reasoning via copy.
- **`test_classify_components_dedup_different_lyrics`**: 2 essential
  components with different lyrics → 2 distinct LLM calls.
- **`test_lyric_hash_normalizes_whitespace_and_case`**: Unit test on the
  helper — `"  Hello  World "` and `"hello world"` produce the same hash;
  `None`/`[]` → `"EMPTY"`.

### 2.2 Analysis Service — `tests/test_components.py`

Add tests:

- **`test_extract_components_skips_non_essential_features`**: Patch
  `compute_component_features` with a spy; call `extract_components(...,
  all_components=False)`. Assert:
  - Only essential-role components had `compute_component_features` invoked.
  - Non-essential rows still appear in the returned list with `bpm=None`,
    `key=None`, etc.
  - The returned `source` is unchanged.
- **`test_extract_components_all_components_populates_all`**: Same fixture;
  `all_components=True` → all components get `compute_component_features`.

### 2.3 Analysis Service — integration smoke

`tests/integration/test_api.py`: add an HTTP submission with
`"all_components": true` and confirm the request is accepted (200) and
the option round-trips into the dispatched job's options.

### 2.4 Admin CLI

Smoke-test the new `--all-components` flag via the existing `components`
command test harness (if present); otherwise verify the Typer option parses
via a `CliRunner` invocation. Add an assertion that `--compute-all-fields`
implies `all_components=True` in the forwarded options payload.

---

## Phase 3: Documentation

**Complexity:** S

- Help text in `components_recording` is covered by Typer help in 1.7.
- Add a short note to `specs/chorus-component-metadata-impl-plan-v5.md` (or a
  new `v6` addendum section) describing the essential-component default and
  the `--all-components` override.
- No `AGENTS.md` change needed (test commands unchanged).

---

## Risk & Rollback

### Risks

- **Skipped components break downstream consumers:** Any consumer iterating
  over `components` and unconditionally reading `bpm`/`theme` must tolerate
  `None`. Audit before merge:
  - The Admin CLI's `_persist_components()` path (audio.py) — should already
    tolerate None (it inserts NULL into DB columns).
  - The songset constructor — verify it filters by role before reading
    metadata (it should, since it only consumes entry/exit/loop_target).
  - The delivery render-worker — verify it does not read non-essential
    component metadata.
- **Cache rows with NULLs served to old clients:** Existing cached
  `components.json` rows (full metadata) remain valid. New fresh runs may
  produce NULL fields for skipped components — consumers must handle NULLs
  (see audit above).
- **Dedup correctness:** Identical lyrics across chorus occurrences yield the
  same LLM theme/vocal-posture classification — this is by definition (theme
  and posture are lyric-driven properties, not audio-driven). Safe to copy.

### Rollback

Disable selective behavior by setting `all_components=True` as the default in
`ComponentAnalysisOptions` (one-line revert). No DB migration or cache
invalidation required.

---

## Acceptance Criteria

- [ ] A song with 17 components (16 chorus + 1 verse) classifies via **≤ 3**
      LLM calls by default (1 entry chorus + 1 exit chorus + 1 verse, minus
      any lyric-hash collisions between entry and exit chorus if their lyrics
      are identical).
- [ ] Non-essential components appear in the result list with `bpm=None`,
      `theme=None` etc.
- [ ] `--all-components` restores the current behavior (all rows populated).
- [ ] `--compute-all-fields` implies `--all-components`.
- [ ] Existing cached `components.json` (schema v2) continue to load without
      change.
- [ ] New unit + integration tests pass.
- [ ] `uv run --project ops/admin-cli --extra admin sow-admin audio
      components <song_id> --compute-all-fields` reproduces the legacy
      fully-populated result.

---

## Out of Scope

- Persisting LLM results back to `components.json` in R2 (currently LLM fields
  are not cached in components.json — they are recomputed each job run by
  design). A future spec could add an LLM-result cache to R2 keyed on lyric
  hash + schema version.
- Audio-feature caching for skipped components on demand (e.g. a flag to
  backfill only previously-skipped components).

---

## File-by-file change summary

| File | Change |
|---|---|
| `ops/analysis-service/src/sow_analysis/models.py` | Add `all_components: bool = False` to `ComponentAnalysisOptions` (line 87) |
| `ops/analysis-service/src/sow_analysis/workers/components.py` | Add `all_components` param to `extract_components()` (1264); add `ESSENTIAL_ROLES` + `_is_essential()` (after 163); gate feature loop on essential-ness (1398) |
| `ops/analysis-service/src/sow_analysis/workers/classifier.py` | Add `all_components` param to `classify_components()` (170); add selective filtering + lyric-hash dedup; add `_lyric_hash()` helper; import `ESSENTIAL_ROLES` from `components` |
| `ops/analysis-service/src/sow_analysis/workers/queue.py` | Thread `all_components=request.options.all_components` into `extract_components` (987) and `classify_components` (1012) |
| `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py` | Add `all_components` param to `submit_component_analysis()` (566); include in payload `options` (615) |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | Add `--all-components` Typer flag (2177); include in `--compute-all-fields` shortcut (2205); thread through `_submit_component_analysis_job()` (1942) |
| `ops/analysis-service/tests/test_components.py` | Add skip-non-essential + all-components tests |
| `ops/analysis-service/tests/test_classifier.py` | Add selective + dedup tests + `_lyric_hash` unit test |
| `ops/analysis-service/tests/integration/test_api.py` | Add `all_components` HTTP submission smoke test |
