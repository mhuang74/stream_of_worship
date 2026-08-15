# Implementation Plan: LLM-Based Structured Lyrics-to-LRC Alignment (v2)

> **Goal:** Replace the deterministic heuristic `identify_from_structured_lyrics()` with an LLM-based alignment that maps authoritative section labels (from YouTube description structured lyrics) to LRC line ranges. The LLM handles line-segmentation mismatches (merged/split lines, empty lines, whitespace differences) that the heuristic cannot.
>
> **Decision:** LLM-only (no heuristic fallback). The heuristic `identify_from_structured_lyrics()` is kept in the codebase for reference/testing but is no longer called in the production path. Chorus repetition validation runs as defensive post-processing. Three few-shot examples.
>
> **v2 changes:** Fixes 1 CRITICAL and 5 HIGH issues found in v1 review (see "v1 Review Findings" below). The core architecture is unchanged; v2 corrects the `source` field propagation, restores non-chorus section support, adds duration clamping, hardens the few-shot loader and parser, and clarifies label normalization.

---

## v1 Review Findings

### CRITICAL

#### C1. `_map_sections_to_components` hardcodes `source="llm_segmentation"`

**Location:** `section_segmenter.py:256, 287, 311`

The v1 spec (line 67) claims "Direct reuse" of `_map_sections_to_components`, but that function hardcodes `source="llm_segmentation"` on every emitted `ComponentInstance`. The v1 spec wants `source="structured_lyrics_llm"`.

**Impact:** The `extract_components()` outer `source` variable is set correctly (v1 Change 4, line 225), and `_serialize_components` writes `component_source` from that variable — so the cached `components.json` payload is correct. However, the individual `ComponentInstance.source` field is wrong on the first return (before caching). After a cache round-trip, `_deserialize_components` (line 2050) overwrites `source` from `component_source`, masking the bug. Any downstream code reading `component.source` before serialization sees `"llm_segmentation"` instead of `"structured_lyrics_llm"`.

**Fix:** Add a `source` parameter to `_map_sections_to_components` (default `"llm_segmentation"` for backward compat with `segment_song`). The alignment path passes `source="structured_lyrics_llm"`.

### HIGH

#### H1. Songs without chorus produce zero components

**Location:** `section_segmenter.py:207-209`

`_map_sections_to_components` returns `[]` when there are no chorus sections:

```python
chorus_sections = [s for s in sections if s.label == "chorus"]
if not chorus_sections:
    return []
```

The old heuristic `identify_from_structured_lyrics` (lines 1198-1463) handles ALL section types and returns components for any matched section. Furthermore, `_map_sections_to_components` only emits chorus + verse-before-chorus, dropping bridge/prechorus/intro/outro/instrumental entirely.

**Impact:** A song whose structured lyrics have no `[Chorus]` label (e.g. only `[Verse]` + `[Bridge]`) will silently produce 0 components under the new path, even though the LLM correctly aligned all sections. This is a regression from the heuristic, which would have returned verse/bridge components.

**Fix:** Generalize `_map_sections_to_components` to emit components for all section labels, not just chorus + verse. When chorus sections exist, preserve the existing role-assignment logic (entry/exit/none, single-chorus duplication). When no chorus exists, fall back to role assignment by section order (first section → entry, last → exit, middle → none) so downstream transition logic still has entry/exit anchors.

#### H2. No `song_total_duration` clamping

**Location:** v1 spec Change 1, `align_structured_lyrics()` signature (lines 96-102)

The old heuristic receives `song_total_duration=gf.duration` and clamps the last section's `end_time` (`components.py:1351`). The v1 `align_structured_lyrics()` function signature does not accept this parameter. `_map_sections_to_components` estimates end_time via average line duration (`section_segmenter.py:218-223`), which can overshoot the actual song duration.

**Impact:** The last component's `end_time` may exceed the song's actual length, producing invalid timestamps in `components.json` and downstream transition planning.

**Fix:** Add `song_total_duration: Optional[float] = None` to `align_structured_lyrics()` and thread it through to `_map_sections_to_components`, which clamps `end_time` after estimation.

#### H3. `_load_alignment_few_shot_examples()` lacks held-out fixture assertion

**Location:** v1 spec Change 1, internal function 5 (line 138)

The v1 spec says `source_song_id` MUST NOT be any held-out fixture ID (Change 2, line 161), but the loader function is not described as having the runtime assertion that `_load_few_shot_examples()` has (`section_segmenter.py:343-350`).

**Impact:** A few-shot example accidentally sourced from a held-out fixture song would leak test data into the prompt, invalidating A/B evaluation.

**Fix:** The alignment few-shot loader must assert `source_song_id` is not in `_EXPECTED_HELD_OUT_IDS` (reuse the same set from `section_segmenter.py:30-34`).

#### H4. Relaxed parser underspecified

**Location:** v1 spec Change 1, internal function 4 (line 136)

The v1 spec describes a "relaxed variant" of `_parse_segmenter_json` but underspecifies three behaviors:

1. **Sorting:** The LLM may return sections out of order. The parser must sort by `line_start` before returning.
2. **Overlap detection:** The existing linear `line_start <= prev_end` check (line 179) only works with strict contiguity. With relaxed contiguity (gaps allowed), overlap detection must compare each new range against ALL accepted ranges, not just the previous one.
3. **Repeated labels:** Three chorus sections with different line ranges are valid. The parser must not reject them as duplicates (the `seen_ranges` set tracks `(line_start, line_end)` tuples, which handles this correctly, but the spec should be explicit).

**Fix:** The relaxed parser sorts sections by `line_start`, then iterates and rejects only true overlaps (new `line_start <= existing.line_end` AND new `line_end >= existing.line_start`), allowing gaps.

#### H5. Label normalization gap

**Location:** v1 spec Change 1, system prompt (line 133)

Structured lyrics labels (from `parse_structured_lyrics`) include forms like `"verse 1"`, `"pre-chorus"`, `"chorus 2"`, `"hook"`, `"refrain"`, `"tag"` (see `_LABEL_TO_COMPONENT_TYPE` at `components.py:1146-1166`). The LLM must output labels in `_VALID_LABELS` (`"verse"`, `"prechorus"`, `"chorus"`, `"bridge"`, `"intro"`, `"outro"`, `"instrumental"` — no numbers, no hyphens, no synonyms). The v1 system prompt does not explicitly state this normalization requirement.

**Impact:** The LLM may echo back `"verse 1"` or `"pre-chorus"`, which the parser rejects (label not in `_VALID_LABELS`), producing 0 components.

**Fix:** The system prompt must explicitly instruct: "Normalize all labels to one of: intro, verse, prechorus, chorus, bridge, outro, instrumental. Drop numbers (Verse 1 → verse), convert hyphens (pre-chorus → prechorus), map synonyms (hook/refrain/tag → chorus)." Additionally, the parser should apply a normalization map as a fallback before rejecting.

---

## Architecture

### Existing Infrastructure (reused)

The existing LLM segmentation module `section_segmenter.py` has the exact infrastructure needed:

| Component | Location | Reuse |
|-----------|----------|-------|
| OpenAI client setup | `section_segmenter.py:80-90` | Copy pattern (new client with own timeout) |
| `call_llm_with_retry()` | `llm_rate_limit.py:474-505` | Direct reuse (shared semaphore/throttle) |
| `_render_numbered_lrc()` | `section_segmenter.py:100-111` | Direct reuse — renders LRC as `"<n>  [<ss.xx>] <text>"` |
| `Section` dataclass | `section_segmenter.py:71-76` | Direct reuse — `{label, line_start, line_end, confidence, rationale}` |
| `_parse_segmenter_json()` | `section_segmenter.py:152-194` | **New relaxed variant** — alignment doesn't require strict contiguity (sections may skip LRC lines, e.g. interludes not in structured lyrics). See H4 for hardening. |
| `_map_sections_to_components()` | `section_segmenter.py:197-317` | **Modified** — add `source` param (C1), generalize to non-chorus sections (H1), add duration clamping (H2) |
| `_validate_chorus_repetition()` | `section_segmenter.py:354-457` | Direct reuse — defensive cross-check on chorus boundaries |
| LLM settings | `config.py:112-190` | Reuse `SOW_LLM_API_KEY`, `SOW_LLM_BASE_URL`, `SOW_LLM_MODEL`; add new timeout setting |
| Few-shot JSON pattern | `segmentation_few_shot.json` | New file for alignment few-shot examples |

### New Module: `structured_lyrics_aligner.py`

**File:** `ops/analysis-service/src/sow_analysis/workers/structured_lyrics_aligner.py`

This module mirrors `section_segmenter.py`'s structure but solves a **different task**: instead of segmenting LRC from scratch (where the LLM has to guess labels), the LLM is given **authoritative section labels and content** from the YouTube description and only needs to **align** them to LRC line indices.

**Key difference from `section_segmenter.py`:**
- Input: numbered LRC **+** structured lyrics sections (labels + lines)
- The LLM knows the section labels already; it just maps them to LRC ranges
- Output: same `Section` format, so `_map_sections_to_components` works (with modifications per C1/H1/H2)
- No strict contiguity requirement (structured lyrics may omit interludes)
- Sections may repeat (chorus appears 3 times → 3 Section objects with different line ranges)

---

## Changes

### Change 1 — New file: `structured_lyrics_aligner.py`

**File:** `ops/analysis-service/src/sow_analysis/workers/structured_lyrics_aligner.py`

#### Public function:

```python
async def align_structured_lyrics(
    structured_lyrics_json: str,
    lrc_content: str,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    snap_to_downbeat: bool = False,
    song_total_duration: Optional[float] = None,  # [H2] NEW
) -> list[ComponentInstance]:
    """Identify components by LLM-aligning structured lyrics sections to LRC lines.

    Unlike section_segmenter.py (which segments LRC from scratch), this function
    receives authoritative section labels from the YouTube description and only
    asks the LLM to map each section to a range of LRC line indices. This handles
    line-segmentation mismatches (merged/split lines, empty lines, whitespace
    differences) that the deterministic identify_from_structured_lyrics() cannot.

    Returns ComponentInstance list with source='structured_lyrics_llm'.
    """
```

#### Internal functions (mirror section_segmenter.py pattern):

1. **`_build_client()`** — OpenAI client with `SOW_LLM_STRUCTURED_LYRICS_TIMEOUT_SECONDS` (new setting). Falls back to `SOW_LLM_SEGMENTATION_TIMEOUT_SECONDS`.

2. **`_render_structured_sections(structured_lyrics_json)`** — renders the structured lyrics JSON into a readable text block for the prompt:
   ```
   [Verse]
   祢的話在我心 
   使我腳步不偏離
   ...

   [Chorus]
   主啊 我要跟隨祢
   將我一生獻給祢
   ...
   ```

3. **`_build_alignment_prompt(lrc_content, structured_lyrics_json, few_shot_examples)`** — constructs the system + user messages:
   - **System prompt** (with H5 label normalization instructions):
     "You are a Chinese worship-music structure analyst. Given a numbered LRC lyric file AND structured lyrics sections (from the YouTube video description), map each section to a range of LRC line numbers. Return JSON with a single key 'sections'. Each section has: label (one of intro, verse, prechorus, chorus, bridge, outro, instrumental), line_start (1-based, inclusive), line_end (1-based, inclusive), confidence (0.0-1.0), and a short rationale. **Normalize all labels to exactly one of: intro, verse, prechorus, chorus, bridge, outro, instrumental.** Drop numbers (Verse 1 → verse), convert hyphens (pre-chorus → prechorus), map synonyms (hook/refrain/tag → chorus). Sections may repeat (e.g. a Chorus appearing 3 times → 3 sections with different line ranges). Sections must be non-overlapping. It is OK to skip LRC lines that belong to interludes or sections not in the structured lyrics. Respond with JSON only."
   - **User message**: few-shot examples + numbered LRC + structured sections + "Output JSON only:"

4. **`_parse_alignment_json(response_text, n_lines)`** — relaxed parser (H4 hardening):
   - Parses JSON, extracts `sections` list.
   - For each section: validates label (with normalization fallback — see H5), validates `1 <= line_start <= line_end <= n_lines`.
   - **Sorts sections by `line_start`** before overlap checking.
   - **Overlap detection against ALL accepted sections** (not just previous): rejects a new section if its `[line_start, line_end]` range overlaps any already-accepted section's range. Gaps are allowed.
   - Applies `_LABEL_NORMALIZATION_MAP` as fallback before rejecting unknown labels.
   - Returns `Optional[list[Section]]`.

5. **`_load_alignment_few_shot_examples()`** — loads from `structured_lyrics_alignment_few_shot.json` (new file). **[H3]** Asserts `source_song_id` is not in `_EXPECTED_HELD_OUT_IDS` (reused from `section_segmenter.py:30-34`).

#### Label normalization map [H5]

```python
_LABEL_NORMALIZATION_MAP: dict[str, str] = {
    "verse 1": "verse",
    "verse 2": "verse",
    "verse 3": "verse",
    "verse 4": "verse",
    "verse 5": "verse",
    "pre-chorus": "prechorus",
    "prechorus": "prechorus",
    "chorus 1": "chorus",
    "chorus 2": "chorus",
    "chorus 3": "chorus",
    "hook": "chorus",
    "refrain": "chorus",
    "tag": "chorus",
    "bridge": "bridge",
    "intro": "intro",
    "outro": "outro",
    "instrumental": "instrumental",
}
```

Applied in `_parse_alignment_json` as a fallback before rejecting a label not in `_VALID_LABELS`.

#### Flow inside `align_structured_lyrics()`:

```
 1. _build_client() + _segmentation_model()
 2. _load_alignment_few_shot_examples()
 3. _build_alignment_prompt(lrc_content, structured_lyrics_json, few_shot)
 4. call_llm_with_retry(_call) → response text
 5. _render_numbered_lrc(lrc_content) → n_lines
 6. _parse_alignment_json(text, n_lines) → Section list (or None → return [])
 7. _validate_chorus_repetition(sections, lrc_content) → tightened Section list (defensive)
 8. lines = parse_lrc(lrc_content).lines
 9. _map_sections_to_components(
        sections, lines, beats, downbeats, snap_to_downbeat,
        source="structured_lyrics_llm",           # [C1]
        song_total_duration=song_total_duration,   # [H2]
    )
    → ComponentInstance list
10. return components
```

### Change 2 — New few-shot examples file

**File:** `ops/analysis-service/src/sow_analysis/workers/structured_lyrics_alignment_few_shot.json`

JSON array of 3 example objects (same schema as `segmentation_few_shot.json`):
- `source_song_id` — MUST NOT be any held-out fixture ID (`jun_wang_jiu_zai_zhe_li_1c32724c`, `yi_sheng_jing_bai_mi_da2173d0`, `zhu_a__wo_yao_gen_sui_mi_83163301`). **[H3]** Enforced at load time by `_load_alignment_few_shot_examples()`.
- `input` — text containing both numbered LRC AND structured sections
- `sections` — expected output: `[{label, line_start, line_end, confidence, rationale}]`

**Three examples chosen to demonstrate different mismatch patterns:**
1. A song where structured lyrics split lines that LRC merges (line-segmentation mismatch)
2. A song with empty lines in structured lyrics not present in LRC
3. A song with repeated chorus sections (multiple occurrences mapped to different LRC ranges)

**All `sections[].label` values in the examples MUST use normalized forms** (verse, prechorus, chorus — not "Verse 1", "pre-chorus") to reinforce the normalization instruction in the system prompt. [H5]

**Package data:** Update `pyproject.toml`:
```toml
[tool.setuptools.package-data]
sow_analysis = [
    "workers/segmentation_few_shot.json",
    "workers/structured_lyrics_alignment_few_shot.json",
]
```

### Change 3 — New config setting

**File:** `ops/analysis-service/src/sow_analysis/config.py`

Add alongside `SOW_LLM_SEGMENTATION_TIMEOUT_SECONDS` (line ~170):

```python
SOW_LLM_STRUCTURED_LYRICS_TIMEOUT_SECONDS: float = 60.0
# Per-request SDK-level HTTP timeout for the structured lyrics alignment
# OpenAI client (mirrors SOW_LLM_SEGMENTATION_TIMEOUT_SECONDS).
# call_llm_with_retry's budget is the overall wall-clock ceiling.
```

This gives the structured lyrics alignment its own timeout, separate from the segmentation LLM and theme classifier.

### Change 4 — Modify `_map_sections_to_components()` in `section_segmenter.py`

**File:** `ops/analysis-service/src/sow_analysis/workers/section_segmenter.py`

**Location:** Lines 197-317.

#### 4a. Add `source` parameter [C1]

```python
def _map_sections_to_components(
    sections: list[Section],
    lines: list,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    snap_to_downbeat: bool = False,
    weights: ValidatorWeights = DEFAULT_VALIDATOR_WEIGHTS,
    source: str = "llm_segmentation",  # [C1] NEW
    song_total_duration: Optional[float] = None,  # [H2] NEW
) -> list[ComponentInstance]:
```

All `ComponentInstance(...)` constructions use `source=source` instead of the hardcoded `"llm_segmentation"`.

`segment_song()` (line 629) calls with `source="llm_segmentation"` (default) — no change needed.

`align_structured_lyrics()` calls with `source="structured_lyrics_llm"`.

#### 4b. Generalize to non-chorus sections [H1]

Current behavior: returns `[]` if no chorus sections; only emits chorus + verse-before-chorus.

New behavior:

```python
chorus_sections = [s for s in sections if s.label == "chorus"]

if chorus_sections:
    # Existing chorus + verse-before-chorus logic (unchanged).
    # All emitted ComponentInstances use source=source. [C1]
    ...
else:
    # [H1] No chorus: emit ALL sections with role assignment by order.
    # first section → role="entry", last → role="exit", middle → role="none".
    # This ensures downstream transition logic still has entry/exit anchors.
    n = len(lines)
    for i, sec in enumerate(sections):
        start, end = _sec_time(sec)
        # [H2] Clamp end_time to song duration.
        if song_total_duration is not None and end > song_total_duration:
            end = song_total_duration
        role = "entry" if i == 0 else ("exit" if i == len(sections) - 1 else "none")
        components.append(
            ComponentInstance(
                component_type=sec.label,
                occurrence_index=1,
                role=role,
                start_time=start,
                end_time=end,
                confidence=sec.confidence * weights.mapping_confidence_multiplier,
                source=source,
                section_label=sec.label,
                lyrics_excerpt=_lyrics_excerpt(sec),
                llm_rationale=sec.rationale,
            )
        )
    return components
```

#### 4c. Add duration clamping to chorus path [H2]

In `_sec_time()`, after computing `end`, add:

```python
if song_total_duration is not None and end > song_total_duration:
    end = song_total_duration
```

This applies to both the chorus and non-chorus paths.

### Change 5 — Integration in `extract_components()`

**File:** `ops/analysis-service/src/sow_analysis/workers/components.py`

**Location:** The structured lyrics block at lines 1783-1810.

Current behavior: runs heuristic `identify_from_structured_lyrics()`, returns empty if 0 components and `segmentation_mode=='structured_lyrics'` (no fallback).

New behavior: **replaces** the heuristic call with the LLM alignment call:

```python
# v9: Structured lyrics identification via LLM alignment.
if (
    not components
    and structured_lyrics
    and lrc_content
    and segmentation_mode in (None, "structured_lyrics")
):
    sl_start = time.time()
    if settings.SOW_LLM_API_KEY:
        try:
            from .structured_lyrics_aligner import align_structured_lyrics
            components = await align_structured_lyrics(
                structured_lyrics,
                lrc_content,
                beats=beats,
                downbeats=downbeats,
                snap_to_downbeat=snap_to_downbeat,
                song_total_duration=gf.duration if gf is not None else None,  # [H2]
            )
            logger.info(
                f"Structured lyrics LLM alignment completed in "
                f"{time.time() - sl_start:.2f}s ({len(components)} components)"
            )
            if components:
                source = "structured_lyrics_llm"
        except Exception as e:
            logger.warning(f"Structured lyrics LLM alignment failed: {e}")
            components = []
    else:
        logger.warning(
            "Structured lyrics available but SOW_LLM_API_KEY is unset; "
            "skipping LLM alignment"
        )
    if not components and segmentation_mode == "structured_lyrics":
        logger.warning(
            "segmentation_mode='structured_lyrics' requested but no components "
            "found; returning empty"
        )
```

**Key behaviors:**
- When `segmentation_mode=None` (auto): runs LLM alignment → falls through to allin1/LLM-segmentation/repetition if it fails (unchained fallback preserved)
- When `segmentation_mode="structured_lyrics"`: runs LLM alignment only → returns empty if it fails (no fallback to allin1/LLM/repetition, preserving the A/B test contract)
- When LLM API key is unset: logs warning, skips alignment, falls through

**The old heuristic `identify_from_structured_lyrics()` is NOT deleted** — it remains in the codebase for reference and unit testing, but is no longer called in the production path.

### Change 6 — `source` value: `"structured_lyrics_llm"`

The new source value `"structured_lyrics_llm"` is used when LLM alignment produced the components. This is stored in `components.json` as `component_source` and in the `source` field of each `ComponentInstance` (via the `source` parameter to `_map_sections_to_components` — see C1/Change 4a).

**No schema version bump needed** — `source` is a free-form string, not an enum in the JSON schema. Existing consumers (`_serialize_components`, `_deserialize_components`, admin CLI parsers) handle arbitrary source strings.

**Traceability:** Distinguishing `"structured_lyrics_llm"` (LLM-aligned) from `"structured_lyrics"` (old heuristic, no longer used in production) helps debugging and comparison.

### Change 7 — Tests

**File:** `ops/analysis-service/tests/test_structured_lyrics_aligner.py`

Test cases:
1. **Unit tests for `_render_structured_sections()`** — renders JSON to text, verifies formatting.
2. **Unit tests for `_parse_alignment_json()`** — valid JSON, invalid JSON, overlapping ranges, out-of-range line indices, unknown labels (with normalization fallback), relaxed contiguity (gaps allowed), out-of-order sections (sorted), repeated labels with different ranges. [H4, H5]
3. **Unit tests for `align_structured_lyrics()`** — mock the LLM call, verify Section→ComponentInstance mapping, role assignment, timestamps, snapping, `source="structured_lyrics_llm"`. [C1]
4. **Unit tests for chorus repetition validation** — verify `_validate_chorus_repetition` is applied as defensive post-processing.
5. **Unit tests for non-chorus-only sections** — structured lyrics with no chorus label → verify components are still emitted with entry/exit roles by order. [H1]
6. **Unit tests for duration clamping** — last section's `end_time` exceeds `song_total_duration` → verify clamped. [H2]
7. **Unit tests for few-shot loader held-out assertion** — loading a few-shot file with a held-out `source_song_id` raises `ValueError`. [H3]
8. **Integration test for `extract_components()`** — mock `align_structured_lyrics` to return components, verify `source="structured_lyrics_llm"` in both the return tuple and `ComponentInstance.source`. [C1]
9. **Live LLM test** (gated by `SOW_LLM_LIVE_TESTS=1`): run alignment on `zhu_a__wo_yao_gen_sui_mi_83163301` (the failing song) and verify non-zero components with correct section labels and approximate timestamps.

### Change 8 — `.env.example` update

**File:** `ops/analysis-service/.env.example`

Document the new timeout setting:
```env
# Structured lyrics LLM alignment timeout (seconds)
SOW_LLM_STRUCTURED_LYRICS_TIMEOUT_SECONDS=60
```

---

## No Changes Required

| Area | Why |
|------|-----|
| `segmentation_mode` Literal | `"structured_lyrics"` already exists — no new mode needed |
| `ComponentAnalysisJobRequest` model | Already has `structured_lyrics` + `lrc_content` fields |
| R2 `components.json` format | Same payload, just `component_source="structured_lyrics_llm"` |
| DB schema | `song_components` table unchanged — components are already clear-and-re-stored |
| Admin CLI | No changes — `sow-admin audio components --segmentation-mode structured_lyrics` already works; just produces better results now |
| `_serialize_components` / `_deserialize_components` | `source` is already a free-form string |
| `COMPONENT_SCHEMA_VERSION` | No bump — schema shape unchanged (only a new `source` value) |
| Old `identify_from_structured_lyrics()` | Kept in codebase for reference/testing, not called in production |
| `segment_song()` in `section_segmenter.py` | Uses `source="llm_segmentation"` default — no change needed |

---

## Data Flow

```
1. Admin CLI: sow-admin audio components --segmentation-mode structured_lyrics <song_id>
   ↓
2. _submit_component_analysis_job()
   - Fetch structured_lyrics from recordings.structured_lyrics (DB)
   - Fetch lrc_content from R2 ({hash_prefix}/lyrics.lrc)
   - POST /api/v1/jobs/component-analysis with both payload fields
   ↓
3. Analysis service worker (queue.py)
   - Downloads audio from R2
   - Calls extract_components(..., structured_lyrics=..., lrc_content=..., segmentation_mode="structured_lyrics")
   ↓
4. extract_components() (components.py:1783)
   - Call LLM align_structured_lyrics() [one LLM call]
   - If 0 components: return empty (no fallback for structured_lyrics mode)
   ↓
5. align_structured_lyrics() (structured_lyrics_aligner.py)
   - Render numbered LRC + structured sections as prompt
   - call_llm_with_retry → LLM returns {sections: [{label, line_start, line_end, ...}]}
   - _parse_alignment_json → Section list (sorted, overlap-checked, label-normalized) [H4, H5]
   - _validate_chorus_repetition → tightened Section list (defensive)
   - _map_sections_to_components(source="structured_lyrics_llm", song_total_duration=...) → ComponentInstance list [C1, H1, H2]
   ↓
6. Back in extract_components: source = "structured_lyrics_llm"
   - Optional: ThemeClassifier runs on components
   ↓
7. _serialize_components → components.json saved to R2
   - Job result returned to admin CLI
   ↓
8. Admin CLI: upsert_song_components()
   - DELETE FROM song_components WHERE song_id=... AND content_hash=...
   - INSERT new components (clear-and-re-store)
```

---

## Migration Path

For existing songs with stale/empty component results from the old heuristic:

```bash
# Re-run component analysis for a specific song
sow-admin audio components <song_id> --segmentation-mode structured_lyrics --force

# Or batch re-process (if needed — component analysis is cached, --force overrides cache)
for song in $(sow-admin song list --has-structured-lyrics --format csv | tail -n +2); do
  sow-admin audio components "$song" --segmentation-mode structured_lyrics --force
done
```

The `--force` flag bypasses the R2 cache, and `upsert_song_components()` does DELETE-then-INSERT, so previous components are cleared and replaced.

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| LLM returns wrong line ranges | Few-shot examples with correct alignments; `_validate_chorus_repetition` defensive cross-check; confidence < 0.95 to indicate LLM-derived |
| LLM API failure | Graceful fallback: empty result. Error logged. No crash. Falls through to allin1/LLM/repetition when `segmentation_mode=None`. |
| Cost per song | One LLM call per song. Cached in R2 + DB — never re-run unless `--force`. |
| LLM hallucinates labels not in structured lyrics | Parser validates labels against `_VALID_LABELS`; `_LABEL_NORMALIZATION_MAP` fallback normalizes synonyms before rejection [H5]; prompt instructs to use only labels from structured lyrics |
| Different line counts between structured lyrics and LRC | This is the expected scenario — the LLM handles it via semantic matching, not line-by-line comparison |
| Songs without chorus label produce 0 components | `_map_sections_to_components` generalized to emit all section types with order-based roles [H1] |
| Last section end_time exceeds song duration | `song_total_duration` clamping in `_map_sections_to_components` [H2] |
| Few-shot example leaks from held-out fixture | `_load_alignment_few_shot_examples()` asserts `source_song_id` not in `_EXPECTED_HELD_OUT_IDS` [H3] |
| LLM returns out-of-order or overlapping sections | Parser sorts by `line_start`, then checks overlap against all accepted sections [H4] |

---

## Open Questions for Clarification

1. **Should `_validate_chorus_repetition()` use the same `ValidatorWeights` defaults as `section_segmenter.py`?** Recommendation: yes, reuse `DEFAULT_VALIDATOR_WEIGHTS` for consistency. Can be tuned later.

2. **Few-shot example selection:** Start with 3 examples. The tuning harness in `test_components_tuning_llm.py` can be extended later to A/B test different example sets.

3. **Non-chorus role assignment [H1]:** When no chorus exists, should the first section always be `role="entry"` and last `role="exit"`, or should we skip sections that are purely instrumental/intro/outro? Recommendation: assign entry to the first verse or prechorus (skip intro/instrumental), and exit to the last verse or bridge (skip outro/instrumental). This better matches the transition-planning semantics. Needs confirmation.

4. **Should `align_structured_lyrics()` pass `song_title` and `duration` to the prompt (like `segment_song` does)?** The structured lyrics already provide section labels, so the LLM has more context than raw segmentation. But title/duration could still help disambiguate. Recommendation: pass `duration` (already available as `gf.duration`), skip `song_title` (not available in `extract_components` without a DB lookup).
