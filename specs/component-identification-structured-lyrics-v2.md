# Implementation Plan: Structured Lyrics-Based Component Identification (v2)

> **Goal:** Add a new deterministic component identification source that leverages structured (section-tagged) lyrics from YouTube descriptions in conjunction with timestamped LRC lyrics to precisely identify chorus/verse/bridge component start and end times.
>
> This is a zero-ML, zero-LLM approach: structured lyrics provide ground-truth section labels and their lyric lines; searching for those lyric line blocks in the LRC yields exact timestamps. It becomes the highest-priority identification source when structured lyrics are available.
>
> Also adds `--segmentation-mode structured_lyrics` to the Admin CLI `audio components` command to force using this algorithm.
>
> **v2 revisions (from v1 review):** Fixes a critical bug where the allin1 block in `extract_components()` has no `if not components` guard (would silently overwrite structured_lyrics results). Extends the first-bridge-essential change to ALL identification sources (not just structured_lyrics) and synchronizes `_is_essential()` across `components.py`, `classifier.py`, and admin-cli `analysis.py`. Makes `zhconv` a hard import-time requirement. Clamps last-section `end_time` to song duration. Hard-requires `rapidfuzz` for the structured_lyrics path with a degraded-path test. Adds a cost-projection note for the lazy v3→v4 re-compute.

---

## Motivation

### Current identification sources (priority order in `extract_components`)

1. **allin1 sections** — ML audio segmentation (allin1 library). Requires Docker + heavy deps. Labels sections as intro/verse/chorus/bridge/outro/instrumental, but boundaries are audio-feature-based and can drift.
2. **LLM whole-song segmentation** (Design C) — one LLM call segments numbered LRC into labeled sections. Requires `SOW_LLM_API_KEY` + network call. Accuracy depends on LLM quality.
3. **Lyrics repetition** — deterministic clustering of repeated LRC line groups. Plateaus at IoU ≈ 0.286 on the fixture set: over-merges verse+chorus, misidentifies repeated verses as choruses, produces zero verse/loop_target components.

### Why structured lyrics is better

Structured lyrics (parsed from YouTube descriptions via `parse_structured_lyrics()`) provide **authoritative section labels** (`[Verse 1]`, `[Chorus]`, `[Bridge]`) with their **exact lyric lines**. By searching for these lyric line blocks in the timestamped LRC, we get:

- **Exact section labels** — no guessing whether a repeated block is verse or chorus.
- **Precise timestamps** — start/end times derived directly from LRC line timestamps.
- **All section types** — verse, chorus, bridge, pre-chorus, etc., not just chorus.
- **Multiple occurrences** — a Chorus block appearing 4 times in the LRC yields 4 component instances.
- **Zero ML/LLM cost** — pure text matching, instant, no Docker or API calls.

### Worked example: `cong_zao_chen_dao_ye_wan_b035044f`

**Structured lyrics** (4 sections):
```
[Verse 1]: 早晨我睜開眼睛 / 渴望聆聽祢聲音 / 心中思想祢的好 / 更多與祢來親近
[Verse 2]: 夜晚我仍要歌唱 / 向祢闡明我心意 / 敬拜化成一首歌 / 單單要唱給祢聽
[Chorus]:  從早晨到夜晚 從曠野到高山 / 親愛主 我要稱頌祢美名 / 從早晨到夜晚 祢愛永不止息 / 親愛主 一生緊緊跟隨祢
[Bridge]:  我的主 我愛祢 / 我要誇祢的愛無止盡 / 我的主 我愛祢 / 我要誇祢的愛無止盡
```

**LRC** (36 timed lines). Searching for each structured section's lyric block in the LRC:

| Section | Occurrence | LRC Lines | Start Time | End Time |
|---------|-----------|-----------|------------|----------|
| Verse 1 | 1 | 1–4 | 00:14.14 | 00:53.13 |
| Verse 2 | 1 | 5–8 | 00:53.13 | 01:23.17 |
| Chorus | 1 | 9–12 | 01:23.17 | 02:15.45 |
| Chorus | 2 | 13–16 | 02:15.45 | 02:44.01 |
| Bridge | 1 | 17–20 | 02:44.01 | 03:12.07 |
| Bridge | 2 | 21–24 | 03:12.07 | 04:07.68 |
| Chorus | 3 | 25–28 | 04:07.68 | 04:35.07 |
| Chorus | 4 | 29–32 | 04:35.07 | 05:02.90 |
| Chorus | 5 | 33–36 | 05:02.90 | ~05:28 (est, clamped to song duration) |

This yields 9 component instances with exact timestamps — far more accurate than repetition clustering (which would find the Chorus but miss Verse 1, Verse 2, and both Bridge occurrences, and might mislabel the repeated Bridge as a second Chorus).

---

## Clarification decisions

| Question | Decision |
|----------|----------|
| Traditional/Simplified Chinese mismatch | **Convert all text to traditional Chinese** using `zhconv.convert(text, 'zh-hant')` before normalization. Add `zhconv` as a **hard dependency** (import-time fail if missing). |
| Partial match threshold | **Accept at ≥75%** of section lines matching consecutively. Full match → confidence 0.95, partial match → confidence 0.80. |
| Non-essential components | **Include all matched sections** in output. Expand essential to include first bridge occurrence. |
| Essential role definition | **New definition (all sources)**: role in {entry, exit, loop_target, entry_exit} OR (component_type='bridge' AND occurrence_index=1). First bridge gets full audio/LLM metadata. Applies globally, not just structured_lyrics. |
| Verse loop_target selection | **Last verse before first chorus** (matches `section_segmenter._map_sections_to_components` behavior). |
| Cache invalidation | **Bump COMPONENT_SCHEMA_VERSION to 4**. Lazy re-compute on next access (no backfill script). |
| Preflight validation | **Hard fail** with guidance message when structured_lyrics or LRC is missing. |
| zhconv enforcement | **Hard fail at import time** — `import zhconv` at module top; service won't start without it. |
| rapidfuzz enforcement | **Hard require for structured_lyrics path** — raise if missing when `identify_from_structured_lyrics()` is called. Add degraded-path test. |
| End-time clamping | **Clamp last-section end_time to song_total_duration** when duration is known. Also apply to the repetition path. |
| Rollout strategy | **Lazy re-compute on next access.** No backfill script, no feature flag. Songs re-compute when next `audio analyze components` is run. Includes cost-projection note. |
| Consumer compatibility | **Note only.** Downstream consumers (songset_constructor, render-worker, webapp, Android) read `components.json` defensively. No explicit validation steps in this plan. |

---

## Critical files

### New files
- `ops/analysis-service/tests/test_structured_lyrics_identification.py` — unit tests for `identify_from_structured_lyrics()`.

### Modified files (analysis-service)
- `ops/analysis-service/src/sow_analysis/workers/components.py` — new `identify_from_structured_lyrics()` function; expand `_is_essential()` for first bridge (global); **fix allin1 block guard** (`if not components and ...`); wire into `extract_components()` priority chain; clamp end_time in both structured_lyrics and repetition paths.
- `ops/analysis-service/src/sow_analysis/workers/classifier.py` — **expand `_is_essential()` for first bridge** (mirror of components.py change; keeps classifier decoupled but consistent).
- `ops/analysis-service/src/sow_analysis/models.py` — add `structured_lyrics` field to `ComponentAnalysisJobRequest`; add `"structured_lyrics"` to `segmentation_mode` Literal.
- `ops/analysis-service/src/sow_analysis/workers/queue.py` — thread `structured_lyrics` from request to `extract_components()`.
- `ops/analysis-service/src/sow_analysis/storage/cache.py` — bump `COMPONENT_SCHEMA_VERSION` 3 → 4.
- `ops/analysis-service/pyproject.toml` — add `zhconv` dependency (move from optional to required if currently optional).

### Modified files (admin-cli)
- `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py` — add `structured_lyrics` param to `submit_component_analysis()`; bump `COMPONENT_SCHEMA_VERSION` 3 → 4; **expand `_cached_components_have_llm_fields()` essential check to include bridge occ=1**.
- `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` — pass `recording.structured_lyrics` to `submit_component_analysis()`; add `"structured_lyrics"` to `SEGMENTATION_MODE_VALUES`; add preflight check.
- `ops/admin-cli/src/stream_of_worship/admin/component_editor/constants.py` — bump `COMPONENT_SCHEMA_VERSION` 3 → 4.

### Unchanged files
- `ops/analysis-service/src/sow_analysis/workers/lrc_parser.py` — `parse_lrc` reused as-is.
- `ops/analysis-service/src/sow_analysis/workers/section_segmenter.py` — no changes; LLM segmentation is a separate fallback path.
- `ops/admin-cli/src/stream_of_worship/admin/services/structured_lyrics.py` — `parse_structured_lyrics()` / `flatten_structured_lyrics()` reused as-is.

---

## Implementation changes

### Change 0 — Bump `COMPONENT_SCHEMA_VERSION`

**Files:**
- `ops/analysis-service/src/sow_analysis/storage/cache.py:15` — `COMPONENT_SCHEMA_VERSION = 4`.
- `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py:22` — `COMPONENT_SCHEMA_VERSION = 4`.
- `ops/admin-cli/src/stream_of_worship/admin/component_editor/constants.py:49` — `COMPONENT_SCHEMA_VERSION = 4`.

Forces re-computation of cached `components.json` payloads. The new `source="structured_lyrics"` value populates only on re-compute (or `--force`). Admin-cli's R2 reads will treat any v3 payloads as cache misses and trigger re-compute.

**Rollout strategy: lazy re-compute on next access.** No backfill script. Songs re-compute when an operator next runs `sow-admin audio analyze components <song_id>` (or with `--force`). Existing v3 cached payloads remain valid until individually superseded — admin-cli and the worker both treat v3 as a cache miss (re-compute), but they do NOT proactively scan the catalog.

**Cost projection note:** Each re-computed song incurs: (a) R2 audio download + librosa precompute + per-component feature compute (~30–90s depending on song length), (b) one additional LLM call for bridge occ=1 theme/posture classification (when `--classify-theme`/`--classify-vocal-posture` is set and the song has a bridge), (c) possibly more components than the previous allin1/repetition identification (structured_lyrics yields N chorus occurrences + verses + bridges). Operators should re-compute in small batches during off-peak hours. The `--segmentation-mode structured_lyrics` flag can be used to A/B test individual songs before committing to the default-mode re-compute.

---

### Change 1 — Add `structured_lyrics` field to `ComponentAnalysisJobRequest`

**File:** `ops/analysis-service/src/sow_analysis/models.py`

**Location:** `ComponentAnalysisJobRequest` (line 207), after `lrc_content` (line 228).

```python
class ComponentAnalysisJobRequest(BaseModel):
    # ... existing fields ...
    lrc_content: Optional[str] = None  # Cached LRC text
    structured_lyrics: Optional[str] = None  # v8: parsed structured lyrics JSON (from recordings.structured_lyrics)
    options: ComponentAnalysisOptions = Field(default_factory=ComponentAnalysisOptions)
```

The field accepts the raw JSON string stored in `recordings.structured_lyrics` (output of `parse_structured_lyrics()` — a JSON dict with `{"sections": [{"label", "raw_label", "lines"}], "preamble_lines": [...]}`). Passing as JSON string avoids introducing a pydantic model dependency on the analysis-service side.

---

### Change 2 — Add `"structured_lyrics"` to `segmentation_mode` Literal

**File:** `ops/analysis-service/src/sow_analysis/models.py`

**Location:** `ComponentAnalysisOptions.segmentation_mode` (line 109).

```python
segmentation_mode: Optional[Literal["llm", "repetition", "allin1", "structured_lyrics"]] = None
```

When `segmentation_mode="structured_lyrics"`, the worker runs ONLY the structured-lyrics identification path and returns `[]` if structured lyrics or LRC are unavailable — no fallback chain. Enables A/B testing against other sources.

---

### Change 3 — Add `zhconv` as a hard dependency to analysis-service

**File:** `ops/analysis-service/pyproject.toml`

Add `zhconv` to the **required** dependencies (not optional). This is a pure-Python package (no native deps) already used by admin-cli's `canonical_snap.py`. It provides `zhconv.convert(text, 'zh-hant')` for simplified→traditional Chinese conversion.

```bash
uv add zhconv --project ops/analysis-service
```

**Runtime enforcement:** `import zhconv` at the top of `components.py` (module-level), NOT inside a try/except. If the Docker image is missing the dep, the analysis-service will fail to import `components.py` and the worker will refuse to start — making the regression immediately visible rather than silently degrading matching accuracy.

```python
# At the top of components.py, alongside existing imports:
import zhconv
```

---

### Change 4 — New function: `identify_from_structured_lyrics()`

**File:** `ops/analysis-service/src/sow_analysis/workers/components.py`

**Location:** After `identify_from_lyrics_repetition()` (ends at line 1125), before `compute_component_features()` (line 1128).

#### Function signature:

```python
def identify_from_structured_lyrics(
    structured_lyrics_json: str,
    lrc_content: str,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    snap_to_downbeat: bool = False,
    song_total_duration: Optional[float] = None,
) -> list[ComponentInstance]:
    """Identify song components by matching structured lyrics sections to LRC lines.

    Structured lyrics provide authoritative section labels (Verse, Chorus, Bridge,
    etc.) with their exact lyric lines. This function searches for each section's
    lyric block in the timestamped LRC to find precise start/end times.

    Algorithm:
      1. Parse structured lyrics JSON → sections with label + lines.
      2. Parse LRC → timed lines.
      3. Normalize all lines: convert to traditional Chinese via zhconv, then
         apply _normalize_line() (strips punctuation, lowercases, removes whitespace).
      4. For each structured section:
         a. If section has no lyric lines (e.g. [Intro], [Instrumental]), skip.
         b. Build the section's normalized line signature.
         c. Slide through LRC lines, finding all positions where consecutive
            LRC lines fuzzy-match the section's lines (rapidfuzz.fuzz.ratio > 85
            per line, or exact normalized match).
         d. Accept partial matches if ≥75% of section lines match consecutively
            (confidence 0.80 instead of 0.95).
         e. Each match position gives a time range:
            - start_time = LRC line at match position
            - end_time = LRC line immediately after the matched block, or
              estimated from average line duration if at song end.
            - end_time is clamped to song_total_duration if provided.
         f. Create a ComponentInstance per match (occurrence_index = 1..N).
      5. Assign roles:
         - Chorus: first occurrence → 'entry', last → 'exit', middle → 'none'.
           Single chorus → two rows (entry + exit), matching v3 pattern.
         - Verse: last verse section before first chorus → 'loop_target';
           other verses → 'none'.
         - Bridge/other: → 'none' (kept for metadata).
      6. Snap start_time/end_time to beats/downbeats if provided.

    Args:
        structured_lyrics_json: JSON string from recordings.structured_lyrics.
        lrc_content: Raw LRC file content.
        beats: Optional beat timestamps for snapping.
        downbeats: Optional downbeat timestamps for snapping (preferred).
        snap_to_downbeat: If True, snap to downbeats (requires downbeats param).
        song_total_duration: Optional total song duration in seconds. Used to
            clamp the end_time of the last LRC section so it does not exceed
            the actual song length.

    Returns:
        List of ComponentInstance objects. Empty if structured lyrics or LRC
        are unparseable, or if no section blocks can be matched.
    """
```

#### Key implementation details:

**Text normalization (traditional Chinese first, hard import):**

```python
# Module-level import (NOT try/except — hard fail if missing):
import zhconv

def _to_traditional(text: str) -> str:
    return zhconv.convert(text, 'zh-hant')

def _normalize_for_matching(text: str) -> str:
    return _normalize_line(_to_traditional(text))
```

This converts simplified→traditional before applying the existing `_normalize_line()` (strips punctuation, lowercases, removes whitespace, handles CJK punctuation). Both structured lyrics lines and LRC lines are normalized this way, ensuring consistent comparison regardless of source character set.

**rapidfuzz — hard require for this function:**

```python
def _lines_match(a: str, b: str) -> bool:
    """Exact normalized match OR rapidfuzz ratio > 85."""
    if a == b:
        return True
    from rapidfuzz import fuzz as rf_fuzz
    return rf_fuzz.ratio(a, b) > 85
```

Unlike the repetition path (which treats rapidfuzz as optional), the structured_lyrics path **requires** rapidfuzz for fuzzy matching. If rapidfuzz is not installed, the `from rapidfuzz import fuzz` inside `_lines_match` raises `ImportError` on the first non-exact comparison. This makes the dependency visible immediately. (rapidfuzz is already a dependency of the analysis-service via the repetition path's optional import; this change just makes it non-optional for the new function.)

**Parsing structured lyrics JSON:**
```python
import json

try:
    structured = json.loads(structured_lyrics_json)
except (json.JSONDecodeError, TypeError):
    return []
sections = structured.get("sections", [])
if not sections:
    return []
```

**Parsing LRC:**
```python
try:
    lrc_file = parse_lrc(lrc_content)
except (ValueError, Exception):
    return []
lrc_lines = [ln for ln in lrc_file.lines if ln.text and ln.text.strip()]
if len(lrc_lines) < 2:
    return []
```

**Block matching algorithm:**
For a structured section with `k` lines, slide a window of size `k` through the LRC:
```python
section_norm = [_normalize_for_matching(line) for line in section_lines]
k = len(section_norm)
matches = []
for i in range(len(lrc_norm) - k + 1):
    window = lrc_norm[i:i+k]
    match_count = sum(_lines_match(a, b) for a, b in zip(window, section_norm))
    if match_count == k:
        matches.append((i, i + k, 0.95))  # full match
    elif match_count >= math.ceil(k * 0.75):
        matches.append((i, i + k, 0.80))  # partial match
```

**Deduplication:** Overlapping matches for the same section are deduplicated (keep earliest start, highest confidence).

**End time calculation (with clamping):**
```python
start_time = lrc_lines[start_idx].time_seconds
if end_idx < len(lrc_lines):
    end_time = lrc_lines[end_idx].time_seconds
else:
    block_durations = [
        lrc_lines[j+1].time_seconds - lrc_lines[j].time_seconds
        for j in range(start_idx, min(end_idx - 1, len(lrc_lines) - 1))
    ]
    avg_dur = sum(block_durations) / len(block_durations) if block_durations else 4.0
    end_time = lrc_lines[min(end_idx - 1, len(lrc_lines) - 1)].time_seconds + avg_dur

# Clamp to song duration if known (prevents end_time > actual song length).
if song_total_duration is not None and end_time > song_total_duration:
    end_time = song_total_duration
```

**Label normalization:** Map structured lyrics labels to component_type:
```python
_LABEL_TO_COMPONENT_TYPE = {
    "verse": "verse", "verse 1": "verse", "verse 2": "verse",
    "verse 3": "verse", "verse 4": "verse", "verse 5": "verse",
    "pre-chorus": "prechorus", "prechorus": "prechorus",
    "chorus": "chorus", "chorus 1": "chorus", "chorus 2": "chorus", "chorus 3": "chorus",
    "bridge": "bridge",
    "intro": "intro", "outro": "outro", "instrumental": "instrumental",
    "hook": "chorus", "refrain": "chorus", "tag": "chorus",
}
```

Mirrors `_RECOGNISED_LABELS` in `structured_lyrics.py:25-47`.

**Role assignment:**
- Chorus: first occurrence → `entry`, last → `exit`, middle → `none`. Single chorus → two rows (entry + exit), matching v3 pattern.
- Verse: walk all verse sections before the first chorus occurrence; the last verse before first chorus → `loop_target`; other verses → `none`. Mirrors `section_segmenter._map_sections_to_components` (line 293-316).
- Bridge, pre-chorus, intro, outro, instrumental → `none`.

**Confidence:** `0.95` for full matches, `0.80` for partial matches (≥75% lines matched).

**Source field:** `source="structured_lyrics"` on all ComponentInstance objects.

**lyrics_excerpt:** Populate from the structured section's lines (joined by newline).

**section_label:** Set to the normalized label (e.g., "chorus", "verse", "bridge").

---

### Change 5 — Expand `_is_essential()` to include first bridge (ALL sources, global)

This change applies to **three** locations that each have their own copy of the essential-role check. All three must be updated consistently.

#### 5a. `components.py:_is_essential()`

**File:** `ops/analysis-service/src/sow_analysis/workers/components.py`

**Location:** `_is_essential()` function (line 213).

Current:
```python
def _is_essential(component: "ComponentInstance") -> bool:
    return component.role in ESSENTIAL_ROLES
```

New:
```python
def _is_essential(component: "ComponentInstance") -> bool:
    """Return True if the component's role is transition-essential.

    Essential roles: entry, exit, loop_target, entry_exit.
    v8: Also includes the first bridge occurrence (component_type='bridge'
    AND occurrence_index=1) — bridges are useful for transition planning.
    This applies to ALL identification sources, not just structured_lyrics.
    """
    if component.role in ESSENTIAL_ROLES:
        return True
    if component.component_type == "bridge" and component.occurrence_index == 1:
        return True
    return False
```

This means the first bridge gets full audio-metadata (BPM, key, groove, energy) and LLM fields (theme, vocal_posture) populated by default, regardless of which identification source produced it. Other bridges and non-essential components keep NULL fields (unless `--all-components`).

#### 5b. `classifier.py:_is_essential()`

**File:** `ops/analysis-service/src/sow_analysis/workers/classifier.py`

**Location:** `_is_essential()` function (line 169).

The classifier module has its own **duplicated** copy of `_is_essential()` (its docstring says "Mirrors components._is_essential; defined locally to keep the classifier module decoupled from the components module's internals"). This copy must be updated in lockstep, otherwise:

- `ThemeClassifier.classify_components()` (classifier.py:272) gates LLM classification on `classifier._is_essential` → bridge occ=1 would NOT be classified → `theme`/`vocal_posture` remain NULL.
- `has_cached_llm_fields()` (classifier.py:178-198, called from `queue.py:1024`) uses `classifier._is_essential` to decide candidate set → would return True (cache satisfied) even when bridge has no LLM fields → LLM classification silently skipped.

Current:
```python
def _is_essential(component: ComponentInstance) -> bool:
    """Return True if the component's role is transition-essential.

    Mirrors components._is_essential; defined locally to keep the
    classifier module decoupled from the components module's internals.
    """
    return component.role in ESSENTIAL_ROLES
```

New:
```python
def _is_essential(component: ComponentInstance) -> bool:
    """Return True if the component's role is transition-essential.

    Mirrors components._is_essential; defined locally to keep the
    classifier module decoupled from the components module's internals.
    v8: Also includes the first bridge occurrence (component_type='bridge'
    AND occurrence_index=1). MUST stay in sync with components._is_essential.
    """
    if component.role in ESSENTIAL_ROLES:
        return True
    if component.component_type == "bridge" and component.occurrence_index == 1:
        return True
    return False
```

#### 5c. admin-cli `analysis.py:_cached_components_have_llm_fields()`

**File:** `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`

**Location:** `_cached_components_have_llm_fields()` (line 141).

This function operates on raw dicts from R2 `components.json` (not `ComponentInstance` objects) and has its own `essential_roles` set. It must be updated to include bridge occ=1, otherwise a v4 cached payload where bridge occ=1 has NULL theme/posture will be treated as cache-valid and the admin-cli will skip resubmission.

Current (line 164):
```python
essential_roles = {"entry", "exit", "loop_target", "entry_exit"}
for comp in components:
    is_candidate = all_components or comp.get("role", "none") in essential_roles
    if not is_candidate:
        continue
```

New:
```python
essential_roles = {"entry", "exit", "loop_target", "entry_exit"}
for comp in components:
    is_essential_bridge = (
        comp.get("component_type") == "bridge"
        and comp.get("occurrence_index") == 1
    )
    is_candidate = (
        all_components
        or comp.get("role", "none") in essential_roles
        or is_essential_bridge
    )
    if not is_candidate:
        continue
```

**Trade-off (all sources):** ~1-2 extra LLM calls (theme/posture classification) per song that has a bridge, across ALL identification sources. The songset_constructor transition planner gains bridge as a transition option. This is a deliberate global semantics change — bridge occ=1 is now essential everywhere, not just in the structured_lyrics path.

---

### Change 6 — Wire into `extract_components()` priority chain

**File:** `ops/analysis-service/src/sow_analysis/workers/components.py`

**Location:** `extract_components()` (line 1343).

#### New priority order (when `segmentation_mode` is `None`):

1. **structured_lyrics** (NEW — highest priority, deterministic ground-truth)
2. **allin1_sections** (existing — ML audio segmentation)
3. **llm_segmentation** (existing — LLM whole-song segmentation)
4. **lyrics_repetition** (existing — deterministic fallback)

When `segmentation_mode` is set to a specific value, only that source runs (no fallback — existing behavior).

#### Function signature update:

Add `structured_lyrics` and `song_total_duration` parameters:

```python
async def extract_components(
    audio_path: Path,
    content_hash: str,
    cache_manager: CacheManager,
    r2_client: Optional[R2Client],
    sections: Optional[list[dict]] = None,
    lrc_content: Optional[str] = None,
    structured_lyrics: Optional[str] = None,  # v8: structured lyrics JSON
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    force: bool = False,
    use_stems: bool = False,
    snap_to_downbeat: bool = False,
    energy_aware_roles: bool = False,
    all_components: bool = False,
    use_llm_segmentation: bool = False,
    segmentation_mode: Optional[str] = None,
) -> tuple[list[ComponentInstance], str]:
```

Returns `(components, source)` where source is one of:
`'structured_lyrics'`, `'allin1_sections'`, `'lyrics_repetition'`, `'llm_segmentation'`, `'none'`.

#### CRITICAL FIX: Add `if not components` guard to the allin1 block

**Location:** `components.py:1438` (current line number).

The current allin1 block has **no `if not components` guard** — it unconditionally overwrites `components`:

```python
# CURRENT (BUGGY — would clobber structured_lyrics results):
if segmentation_mode in (None, "allin1") and sections:
    components = identify_from_allin1_sections(...)
```

This must be changed to:

```python
# FIXED — only runs if structured_lyrics didn't produce components:
if not components and segmentation_mode in (None, "allin1") and sections:
    components = identify_from_allin1_sections(...)
```

Without this fix, the structured_lyrics block's results would be silently overwritten by allin1 whenever `sections` is non-empty (which it will be for any song that has had a full-tier analysis run). This is the most critical fix in v2.

#### New identification block (insert BEFORE the fixed allin1 check):

```python
# v8: Structured lyrics identification (highest priority, deterministic).
if (
    not components
    and structured_lyrics
    and lrc_content
    and segmentation_mode in (None, "structured_lyrics")
):
    sl_start = time.time()
    # Pass song_total_duration for end_time clamping.
    song_dur = gf.duration if gf is not None else None
    components = identify_from_structured_lyrics(
        structured_lyrics,
        lrc_content,
        beats=beats,
        downbeats=downbeats,
        snap_to_downbeat=snap_to_downbeat,
        song_total_duration=song_dur,
    )
    logger.info(
        f"Structured lyrics identification completed in "
        f"{time.time() - sl_start:.2f}s ({len(components)} components)"
    )
    if components:
        source = "structured_lyrics"
    elif segmentation_mode == "structured_lyrics":
        logger.warning(
            "segmentation_mode='structured_lyrics' requested but no components found; "
            "returning empty"
        )
```

The existing allin1/llm/repetition blocks follow, now correctly guarded by `if not components and ...`.

---

### Change 7 — Thread `structured_lyrics` through `queue.py`

**File:** `ops/analysis-service/src/sow_analysis/workers/queue.py`

**Location:** `_process_component_analysis_job()` (line 869), the `extract_components()` call (line 998).

Add `structured_lyrics=request.structured_lyrics` to the call:

```python
components, source = await extract_components(
    audio_path=audio_path,
    content_hash=request.content_hash,
    cache_manager=self.cache_manager,
    r2_client=self.r2_client,
    sections=sections_dicts,
    lrc_content=request.lrc_content,
    structured_lyrics=request.structured_lyrics,  # v8
    beats=request.beats,
    downbeats=downbeats,
    force=request.options.force,
    use_stems=request.options.use_stems,
    snap_to_downbeat=request.options.snap_to_downbeat,
    energy_aware_roles=request.options.energy_aware_roles,
    all_components=request.options.all_components,
    use_llm_segmentation=request.options.use_llm_segmentation,
    segmentation_mode=request.options.segmentation_mode,
)
```

No other changes needed in `queue.py` — `ComponentResult` conversion (line 1070) is unchanged since `ComponentInstance` already has `section_label`, `lyrics_excerpt`, and `source` fields.

---

### Change 8 — Admin CLI: pass `structured_lyrics` in job submission

#### 8a. `AnalysisClient.submit_component_analysis()`

**File:** `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`

**Location:** `submit_component_analysis()` (line 612).

Add `structured_lyrics` parameter and include in payload:

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
    structured_lyrics: Optional[str] = None,  # v8
    force: bool = False,
    # ... existing options ...
    segmentation_mode: Optional[str] = None,
) -> JobInfo:
```

Payload dict (line 680):
```python
payload = {
    "audio_url": audio_url,
    "content_hash": content_hash,
    "song_id": song_id,
    "sections": sections,
    "beats": beats,
    "downbeats": downbeats,
    "lrc_content": lrc_content,
    "structured_lyrics": structured_lyrics,  # v8
    "options": { ... },
}
```

#### 8b. `_submit_component_analysis_job()`

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

**Location:** `_submit_component_analysis_job()` (line 2475).

After fetching LRC content (line 2558), add structured lyrics extraction:

```python
# Fetch structured lyrics from the recording row.
structured_lyrics: Optional[str] = None
if recording.structured_lyrics:
    structured_lyrics = recording.structured_lyrics
```

Pass to submit call (line 2634):
```python
job = client.submit_component_analysis(
    audio_url=recording.r2_audio_url,
    content_hash=recording.content_hash,
    song_id=song_id,
    sections=sections,
    beats=beats,
    downbeats=downbeats,
    lrc_content=lrc_content,
    structured_lyrics=structured_lyrics,  # v8
    force=force,
    # ... existing options ...
    segmentation_mode=segmentation_mode,
)
```

#### 8c. Preflight check for `--segmentation-mode structured_lyrics`

Add after existing preflight checks (lines 2562-2582):

```python
if segmentation_mode == "structured_lyrics":
    if not structured_lyrics:
        console.print(
            f"[red]Cannot use --segmentation-mode structured_lyrics: recording {song_id} "
            f"has no structured_lyrics. Run "
            f"`sow-admin audio download --youtube ...` or "
            f"`sow-admin catalog insert --youtube ...` first.[/red]"
        )
        return None
    if not lrc_content:
        console.print(
            f"[red]Cannot use --segmentation-mode structured_lyrics: recording {song_id} "
            f"has no LRC (lrc_status != 'completed'). Run "
            f"`sow-admin audio analyze lrc {song_id}` first.[/red]"
        )
        return None
```

#### 8d. Add `"structured_lyrics"` to `SEGMENTATION_MODE_VALUES`

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:82`

```python
SEGMENTATION_MODE_VALUES = {"llm", "repetition", "allin1", "structured_lyrics"}
```

#### 8e. Update `--segmentation-mode` help text

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:2820`

```python
    segmentation_mode: Optional[str] = typer.Option(
        None,
        "--segmentation-mode",
        help=(
            "Force a mutually-exclusive component identification source: "
            "structured_lyrics | llm | repetition | allin1. Default (omitted) uses "
            "current best-available priority (structured_lyrics > allin1 > llm > "
            "repetition). REQUIRES --force (validated). "
            "structured_lyrics/llm/repetition require LRC; structured_lyrics also "
            "requires structured_lyrics on the recording; allin1 requires cached "
            "sections from a prior `audio analyze --analysis-tier full` run."
        ),
    ),
```

---

### Change 9 — Clamp end_time in the repetition path

**File:** `ops/analysis-service/src/sow_analysis/workers/components.py`

**Location:** `identify_from_lyrics_repetition()` (line 1036-1045).

The repetition path has the same latent end-time-over-extension issue as the structured_lyrics path. For consistency, clamp the estimated end_time to `song_total_duration` when provided.

Current (line 1039-1045):
```python
else:
    # Estimate: average line duration in the block.
    block_durations = []
    for k in range(start_idx, min(start_idx + w, n - 1)):
        block_durations.append(lines[k + 1].time_seconds - lines[k].time_seconds)
    avg_dur = sum(block_durations) / len(block_durations) if block_durations else 4.0
    end_time = lines[min(start_idx + w - 1, n - 1)].time_seconds + avg_dur
```

New:
```python
else:
    # Estimate: average line duration in the block.
    block_durations = []
    for k in range(start_idx, min(start_idx + w, n - 1)):
        block_durations.append(lines[k + 1].time_seconds - lines[k].time_seconds)
    avg_dur = sum(block_durations) / len(block_durations) if block_durations else 4.0
    end_time = lines[min(start_idx + w - 1, n - 1)].time_seconds + avg_dur
    # Clamp to song duration if known.
    if song_total_duration is not None and end_time > song_total_duration:
        end_time = song_total_duration
```

The `song_total_duration` parameter already exists on `identify_from_lyrics_repetition()` (line 892), so no signature change is needed.

---

## Detailed algorithm walkthrough (worked example)

### Input: `cong_zao_chen_dao_ye_wan_b035044f`

**Structured lyrics sections** (after `json.loads`):
```json
{
  "sections": [
    {"label": "verse 1", "raw_label": "Verse 1", "lines": ["早晨我睜開眼睛", "渴望聆聽祢聲音", "心中思想祢的好", "更多與祢來親近"]},
    {"label": "verse 2", "raw_label": "Verse 2", "lines": ["夜晚我仍要歌唱", "向祢闡明我心意", "敬拜化成一首歌", "單單要唱給祢聽"]},
    {"label": "chorus", "raw_label": "Chorus", "lines": ["從早晨到夜晚 從曠野到高山", "親愛主 我要稱頌祢美名", "從早晨到夜晚 祢愛永不止息", "親愛主 一生緊緊跟隨祢"]},
    {"label": "bridge", "raw_label": "Bridge", "lines": ["我的主 我愛祢", "我要誇祢的愛無止盡", "我的主 我愛祢", "我要誇祢的愛無止盡"]}
  ]
}
```

**Step 1: Parse LRC** → 36 timed lines.

**Step 2: Normalize all LRC lines** using `_normalize_for_matching()` = `zhconv.convert(text, 'zh-hant')` → `_normalize_line()`.

**Step 3: For each structured section, search for block matches:**

#### Verse 1 (4 lines)
- LRC lines 1-4 match exactly → match at (idx=0, end=4), confidence 0.95
- No other matches (verse text is unique)
- → ComponentInstance(type="verse", occ=1, role=none, start=14.14, end=53.13)

#### Verse 2 (4 lines)
- LRC lines 5-8 match exactly → match at (idx=4, end=8), confidence 0.95
- → ComponentInstance(type="verse", occ=2, role=none, start=53.13, end=83.17)

#### Chorus (4 lines)
- LRC lines 9-12 → occurrence 1, start=83.17, end=135.45
- LRC lines 13-16 → occurrence 2, start=135.45, end=164.01
- LRC lines 25-28 → occurrence 3, start=247.68, end=275.07
- LRC lines 29-32 → occurrence 4, start=275.07, end=302.90
- LRC lines 33-36 → occurrence 5, start=302.90, end=~328 (estimated, **clamped to song_total_duration**)
- 5 chorus occurrences → roles: 1=entry, 5=exit, 2-4=none
- → 5 ComponentInstance objects

#### Bridge (4 lines)
- LRC lines 17-20 → occurrence 1, start=164.01, end=192.07
- LRC lines 21-24 → occurrence 2, start=192.07, end=247.68
- → 2 ComponentInstance objects (role="none")

**Step 4: Verse role assignment:**
- First chorus starts at LRC line 9 (idx=8).
- Walk all verse sections before first chorus: Verse 1 and Verse 2.
- Last verse before first chorus (Verse 2) → `loop_target`.
- Verse 1 → `none`.

**Step 5: Snap to beats/downbeats** (if provided).

**Final result:** 9 ComponentInstance objects:

| # | Type | Occ | Role | Start | End | Conf | Essential? |
|---|------|-----|------|-------|-----|------|------------|
| 1 | verse | 1 | none | 14.14 | 53.13 | 0.95 | No |
| 2 | verse | 2 | loop_target | 53.13 | 83.17 | 0.95 | Yes |
| 3 | chorus | 1 | entry | 83.17 | 135.45 | 0.95 | Yes |
| 4 | chorus | 2 | none | 135.45 | 164.01 | 0.95 | No |
| 5 | bridge | 1 | none | 164.01 | 192.07 | 0.95 | Yes (first bridge) |
| 6 | bridge | 2 | none | 192.07 | 247.68 | 0.95 | No |
| 7 | chorus | 3 | none | 247.68 | 275.07 | 0.95 | No |
| 8 | chorus | 4 | none | 275.07 | 302.90 | 0.95 | No |
| 9 | chorus | 5 | exit | 302.90 | ~328 (clamped) | 0.95 | Yes |

Components #2, #3, #5, #9 are essential → get full audio/LLM metadata. Others get NULL fields (unless `--all-components`).

---

## Edge cases and handling

### 1. Section with no lyric lines
Sections like `[Intro]` or `[Instrumental]` may have empty `lines` arrays. Skip them — no timestamp information from LRC matching.

### 2. Partial matches (≥75% threshold)
If a structured section has 4 lines but only 3 appear consecutively in the LRC, accept the match at confidence 0.80 (instead of 0.95 for full match). Below 75% threshold, reject the match.

### 3. No matches found
If no section block can be found in the LRC, return `[]` and let `extract_components()` fall through to the next identification source (allin1 → llm → repetition).

### 4. Traditional/Simplified Chinese mismatch
All text is converted to traditional Chinese via `zhconv.convert(text, 'zh-hant')` before `_normalize_line()`. This handles the common case where structured lyrics from YouTube use traditional Chinese but LRC uses simplified (or vice versa). `zhconv` is a **hard import-time dependency** — the analysis-service will not start without it, ensuring this conversion always runs.

### 5. LRC has extra lines not in structured lyrics
LRC may include spoken intro lines, ad-libs, or repeated single lines not in the structured lyrics. These are not matched by any section's block search — they fall between matched blocks and don't affect component identification.

### 6. Structured lyrics has single section but LRC has multiple occurrences
Some YouTube descriptions list `[Chorus]` once but it repeats in the song. The LRC search finds all occurrences. The section label identifies *what* the lyrics are, and the LRC search finds *all* timestamped occurrences.

### 7. Interleaved/overlapping matches
Overlapping matches for the same section are deduplicated (keep earliest start, highest confidence). Different sections should not overlap if the structured lyrics are well-formed.

### 8. Last-section end_time exceeds song duration
When the matched section is the last block in the LRC, `end_time` is extrapolated from the block's average line duration. This is **clamped to `song_total_duration`** when duration is known (passed from `gf.duration` in `extract_components()`), preventing `end_time` from exceeding the actual song length. The same clamping is applied to the repetition path (Change 9).

### 9. rapidfuzz not installed
The structured_lyrics path **requires** rapidfuzz for fuzzy matching. If rapidfuzz is not installed, `_lines_match()` raises `ImportError` on the first non-exact comparison. This makes the dependency visible immediately rather than silently degrading to exact-only matching. (rapidfuzz is already a dependency of the analysis-service via the repetition path.)

---

## Consumer compatibility note

Downstream consumers (songset_constructor transition planner, render-worker ffmpeg trim, webapp component display, Android component display) read `components.json` defensively — they use `.get()` with defaults and do not hard-fail on unknown `component_source` values or new `component_type` entries. The new `source="structured_lyrics"` value and the expanded essential set (bridge occ=1) are additive and do not break existing consumers. However, operators should be aware that re-computed songs may have a **different** component shape than the previous allin1/repetition identification (e.g., 5 chorus occurrences instead of 2, bridge now essential). No explicit validation steps are included in this plan.

---

## Testing

### Unit tests: `ops/analysis-service/tests/test_structured_lyrics_identification.py`

1. **Basic matching** — 4-section structured lyrics matched against 36-line LRC. Assert 9 components with correct types, occurrences, roles, timestamps.
2. **Single chorus → two rows** — 1 Chorus section, LRC with 1 occurrence. Assert two rows (entry + exit).
3. **Multiple chorus occurrences** — 1 Chorus section, LRC with 4 occurrences. Assert 4 components with entry/exit on first/last.
4. **No match** — structured lyrics text not in LRC. Assert empty list.
5. **Traditional/simplified mismatch** — structured lyrics in traditional, LRC in simplified. Assert matches found via zhconv.
6. **Partial match** — 4-line section, only 3 consecutive LRC lines match. Assert match accepted at 0.80 confidence.
7. **Missing structured_lyrics** — `structured_lyrics_json=None`. Assert empty list.
8. **Missing LRC** — `lrc_content=None`. Assert empty list.
9. **Verse loop_target assignment** — Verse 1 + Verse 2 before Chorus. Assert Verse 2 → loop_target.
10. **Empty lines in section** — section with `lines=[]`. Assert section skipped.
11. **Beat/downbeat snapping** — provide beats/downbeats, assert timestamps snapped.
12. **First bridge is essential** — assert `_is_essential()` returns True for bridge occ=1, False for bridge occ=2. **Test in both `components.py` and `classifier.py`** to verify the two copies are in sync.
13. **Segmentation mode integration** — `extract_components` with `segmentation_mode="structured_lyrics"`, assert `source="structured_lyrics"`.
14. **End-time clamping** — last LRC section end_time would exceed song_total_duration. Assert end_time clamped to song_total_duration.
15. **End-time clamping (repetition path)** — same test for `identify_from_lyrics_repetition()` with `song_total_duration` set.
16. **rapidfuzz degraded path** — mock `rapidfuzz` as unavailable. Assert `_lines_match()` raises `ImportError` on non-exact comparison (documents the hard-require behavior).
17. **allin1 guard fix** — `extract_components` with both `structured_lyrics` and `sections` available (default mode). Assert `source="structured_lyrics"` (not `"allin1_sections"`), proving the allin1 block does not clobber.
18. **admin-cli _cached_components_have_llm_fields bridge** — cached payload with bridge occ=1 having NULL theme. Assert function returns False (cache not satisfied), triggering resubmission.

---

## Rollout

1. Implement Changes 0–9.
2. Add zhconv: `uv add zhconv --project ops/analysis-service`.
3. Run unit tests: `uv run --project ops/analysis-service --extra dev pytest tests/test_structured_lyrics_identification.py -v`.
4. Run existing tests (no regression): `uv run --project ops/analysis-service --extra dev pytest tests/test_components.py tests/test_components_tuning.py tests/test_section_segmenter.py -v`.
5. Run admin-cli tests: `uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/admin/test_analysis_client.py tests/admin/test_audio_commands.py -v`.
6. End-to-end: `sow-admin audio analyze components cong_zao_chen_dao_ye_wan_b035044f --force --compute-all-fields`.
7. Verify `components.json` in R2 has `schema_version=4`, `component_source="structured_lyrics"`, 9 components.
8. A/B test: `--segmentation-mode structured_lyrics` vs `--segmentation-mode repetition` vs `--segmentation-mode allin1`.
9. **Lazy re-compute:** subsequent `sow-admin audio analyze components <song_id>` calls for other songs will re-compute on access (v3 cache treated as miss). Re-compute in small batches during off-peak hours.

---

## Related specs

- `specs/component-identification-llm-segmentation-v2.md` — LLM whole-song segmentation (Design C).
- `specs/component-identification-alternatives-v1.md` — design comparison of identification approaches.
- `specs/component-identification-tuning-loop-v1.md` — weight-tuning for lyrics-repetition path.
- `specs/chorus-component-metadata-impl-plan-v5.md` — v5 component metadata (ComponentAnalysisOptions, ComponentResult).
- `specs/admin-structured-lyrics-youtube.md` — structured lyrics ingestion from YouTube descriptions.
- `specs/enhance-audio-download-structured-lyrics-llm-v1.md` — LLM cleanup of structured lyrics.
- `specs/admin-cli-segmentation-mode-flag-v2.md` — original `--segmentation-mode` flag spec.
- `specs/component-identification-structured-lyrics-v1.md` — v1 of this spec (superseded).
