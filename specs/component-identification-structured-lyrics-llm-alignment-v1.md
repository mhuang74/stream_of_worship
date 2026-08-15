# Implementation Plan: LLM-Based Structured Lyrics-to-LRC Alignment (v1)

> **Goal:** Replace the deterministic heuristic `identify_from_structured_lyrics()` with an LLM-based alignment that maps authoritative section labels (from YouTube description structured lyrics) to LRC line ranges. The LLM handles line-segmentation mismatches (merged/split lines, empty lines, whitespace differences) that the heuristic cannot.
>
> **Decision:** LLM-only (no heuristic fallback). The heuristic `identify_from_structured_lyrics()` is kept in the codebase for reference/testing but is no longer called in the production path. Chorus repetition validation runs as defensive post-processing. Three few-shot examples.

---

## Motivation

### The Problem

The current `identify_from_structured_lyrics()` in `components.py:1198-1463` uses a **fixed-size sliding window** algorithm that requires 1:1 line correspondence between structured lyrics sections (from YouTube description) and LRC lines. When the two sources have different line segmentation — lines merged, split, or empty lines inserted — the window can never align, producing **0 components**.

#### Worked example: `zhu_a__wo_yao_gen_sui_mi_83163301`

**Structured lyrics** (Verse, 6 lines):
```
祢的話在我心           ← line 0
使我腳步不偏離         ← line 1
領我走這人生的路       ← line 2
祢的愛在我心           ← line 3
祢必與我同行           ← line 4
牽我的手走下去         ← line 5
```

**LRC** (4 lines — pairs merged):
```
[00:29.63] 祢的話在我心  使我腳步不偏離   ← LRC line 0 (= structured 0+1)
[00:36.35] 領我走這人生的路              ← LRC line 1 (= structured 2)
[00:42.99] 祢的愛在我心  祢必與我同行    ← LRC line 2 (= structured 3+4)
[00:49.53] 牽我的手走下去                ← LRC line 3 (= structured 5)
```

The window of size 6 slides over 4 LRC lines. Best match: **1/6** (far below the threshold of 5/6 for partial match). The Chorus section has the same issue (9 structured lines vs. 8 LRC lines, plus an empty line at index 4 that breaks alignment). Best chorus match: **4/9** (below the threshold of 7/9).

This is not an edge case — **line segmentation mismatch is the norm** because:
- YouTube descriptions and LRC are produced by different processes
- LRC may merge short lines or split long ones
- Structured lyrics may include empty lines for visual separation
- Both may have inconsistent whitespace

### Why LLM-Only (No Heuristic Fallback)

An LLM handles all of these naturally via semantic understanding. Keeping the heuristic as a first attempt adds complexity for marginal benefit — the heuristic only succeeds when line breaks happen to align 1:1, which is the exception rather than the rule. Going LLM-only:

- Simplifies the code path (one algorithm, not two)
- Increases accuracy (the LLM handles all mismatch cases)
- Cost is capped: component analysis runs once per song, cached in R2 + DB
- Rerunning `sow-admin audio components --force` clears previous components and stores a new set

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
| `_parse_segmenter_json()` | `section_segmenter.py:152-194` | **Relaxed variant** — alignment doesn't require strict contiguity (sections may skip LRC lines, e.g. interludes not in structured lyrics) |
| `_map_sections_to_components()` | `section_segmenter.py:197-317` | Direct reuse — converts `Section` → `ComponentInstance` with roles, timestamps, snapping |
| `_validate_chorus_repetition()` | `section_segmenter.py:354-457` | Direct reuse — defensive cross-check on chorus boundaries |
| LLM settings | `config.py:112-190` | Reuse `SOW_LLM_API_KEY`, `SOW_LLM_BASE_URL`, `SOW_LLM_MODEL`; add new timeout setting |
| Few-shot JSON pattern | `segmentation_few_shot.json` | New file for alignment few-shot examples |

### New Module: `structured_lyrics_aligner.py`

**File:** `ops/analysis-service/src/sow_analysis/workers/structured_lyrics_aligner.py`

This module mirrors `section_segmenter.py`'s structure but solves a **different task**: instead of segmenting LRC from scratch (where the LLM has to guess labels), the LLM is given **authoritative section labels and content** from the YouTube description and only needs to **align** them to LRC line indices.

**Key difference from `section_segmenter.py`:**
- Input: numbered LRC **+** structured lyrics sections (labels + lines)
- The LLM knows the section labels already; it just maps them to LRC ranges
- Output: same `Section` format, so `_map_sections_to_components` works unchanged
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
   - **System prompt**: "You are a Chinese worship-music structure analyst. Given a numbered LRC lyric file AND structured lyrics sections (from the YouTube video description), map each section to a range of LRC line numbers. Return JSON with a single key 'sections'. Each section has: label (one of intro, verse, prechorus, chorus, bridge, outro, instrumental), line_start (1-based, inclusive), line_end (1-based, inclusive), confidence (0.0-1.0), and a short rationale. Sections may repeat (e.g. a Chorus appearing 3 times → 3 sections with different line ranges). Sections must be non-overlapping. It is OK to skip LRC lines that belong to interludes or sections not in the structured lyrics. Respond with JSON only."
   - **User message**: few-shot examples + numbered LRC + structured sections + "Output JSON only:"

4. **`_parse_alignment_json(response_text, n_lines)`** — relaxed parser (no strict contiguity, but still validates: labels in `_VALID_LABELS`, `1 <= line_start <= line_end <= n_lines`, no overlapping ranges). Returns `Optional[list[Section]]`.

5. **`_load_alignment_few_shot_examples()`** — loads from `structured_lyrics_alignment_few_shot.json` (new file).

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
9. _map_sections_to_components(sections, lines, beats, downbeats, snap_to_downbeat)
   → ComponentInstance list with source="structured_lyrics_llm"
10. return components
```

### Change 2 — New few-shot examples file

**File:** `ops/analysis-service/src/sow_analysis/workers/structured_lyrics_alignment_few_shot.json`

JSON array of 3 example objects (same schema as `segmentation_few_shot.json`):
- `source_song_id` — MUST NOT be any held-out fixture ID (`jun_wang_jiu_zai_zhe_li_1c32724c`, `yi_sheng_jing_bai_mi_da2173d0`, `zhu_a__wo_yao_gen_sui_mi_83163301`)
- `input` — text containing both numbered LRC AND structured sections
- `sections` — expected output: `[{label, line_start, line_end, confidence, rationale}]`

**Three examples chosen to demonstrate different mismatch patterns:**
1. A song where structured lyrics split lines that LRC merges (line-segmentation mismatch)
2. A song with empty lines in structured lyrics not present in LRC
3. A song with repeated chorus sections (multiple occurrences mapped to different LRC ranges)

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

Add alongside `SOW_LLM_SEGMENTATION_TIMEOUT_SECONDS` (line ~164):

```python
SOW_LLM_STRUCTURED_LYRICS_TIMEOUT_SECONDS: int = 60
```

This gives the structured lyrics alignment its own timeout, separate from the segmentation LLM and theme classifier.

### Change 4 — Integration in `extract_components()`

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

### Change 5 — `source` value: `"structured_lyrics_llm"`

The new source value `"structured_lyrics_llm"` is used when LLM alignment produced the components. This is stored in `components.json` as `component_source` and in the `source` field of `ComponentInstance`.

**No schema version bump needed** — `source` is a free-form string, not an enum in the JSON schema. Existing consumers (`_serialize_components`, `_deserialize_components`, admin CLI parsers) handle arbitrary source strings.

**Traceability:** Distinguishing `"structured_lyrics_llm"` (LLM-aligned) from `"structured_lyrics"` (old heuristic, no longer used in production) helps debugging and comparison.

### Change 6 — Tests

**File:** `ops/analysis-service/tests/test_structured_lyrics_aligner.py`

Test cases:
1. **Unit tests for `_render_structured_sections()`** — renders JSON to text, verifies formatting.
2. **Unit tests for `_parse_alignment_json()`** — valid JSON, invalid JSON, overlapping ranges, out-of-range line indices, unknown labels, relaxed contiguity (gaps allowed).
3. **Unit tests for `align_structured_lyrics()`** — mock the LLM call, verify Section→ComponentInstance mapping, role assignment, timestamps, snapping.
4. **Unit tests for chorus repetition validation** — verify `_validate_chorus_repetition` is applied as defensive post-processing.
5. **Integration test for `extract_components()`** — mock `align_structured_lyrics` to return components, verify `source="structured_lyrics_llm"`.
6. **Live LLM test** (gated by `SOW_LLM_LIVE_TESTS=1`): run alignment on `zhu_a__wo_yao_gen_sui_mi_83163301` (the failing song) and verify non-zero components with correct section labels and approximate timestamps.

### Change 7 — `.env.example` update

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
   - _parse_alignment_json → Section list
   - _validate_chorus_repetition → tightened Section list (defensive)
   - _map_sections_to_components → ComponentInstance list (source="structured_lyrics_llm")
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
| LLM hallucinates labels not in structured lyrics | Parser validates labels against `_VALID_LABELS`; prompt instructs to use only labels from structured lyrics |
| Different line counts between structured lyrics and LRC | This is the expected scenario — the LLM handles it via semantic matching, not line-by-line comparison |

---

## Open Questions

1. **Should `_validate_chorus_repetition()` use the same `ValidatorWeights` defaults as `section_segmenter.py`?** Recommendation: yes, reuse `DEFAULT_VALIDATOR_WEIGHTS` for consistency. Can be tuned later.

2. **Few-shot example selection:** Start with 3 examples. The tuning harness in `test_components_tuning_llm.py` can be extended later to A/B test different example sets.
