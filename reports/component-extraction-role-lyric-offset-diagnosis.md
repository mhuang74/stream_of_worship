# Component Extraction: Exit Chorus Role Loss & Lyric Off-by-One — Diagnosis Report

**Date:** 2026-08-18  
**Commit:** `2e5d7981`  
**Branch:** `sow_chorus_transition_analysis_pipeline`  
**Component:** `ops/analysis-service/src/sow_analysis/workers/`

---

## Problem Statement

A COMPONENT ANALYSIS job (`job_d068e32398a9`) run with `sow-admin audio components <song> --compute-all-fields` exhibited two bugs:

### Q1: Last chorus not persisted as Exit Chorus
The LLM-based structured-lyrics alignment correctly identified 3 chorus sections (lines 7–14, 16–25, 26–33). The positional role assignment in `_map_sections_to_components` initially set chorus occurrence 3 (the last) to `role="exit"`. However, the persisted result showed occurrence 3 with `role="none"` and occurrence 2 with `role="exit"` — the last chorus was demoted.

### Q2: Classifier lyric lines offset by 1 from LLM component extraction
The LLM-aligned section boundaries (e.g., chorus 2 = LRC lines 16–25) did not match the lyric lines used by the downstream theme/posture classifier. The classifier received lyrics for lines 16–**26** (one extra line — the first line of chorus 3), not 16–25.

A later COMPONENT ANALYSIS job (`job_fc97c93557a9`, same song pipeline) exposed a third, independent bug:

### Q3: Final Exit Chorus persisted one line short of the LLM response
The LLM structured-lyrics alignment returned the last chorus as LRC lines **26–33**, but the persisted component showed **26–32** — line 33 (the outro variation line `祢同在裡　我心永遠屬祢`) was dropped. Unlike Q1/Q2, this is a **line-boundary** loss on the final chorus, independent of role assignment.

---

## Investigation Trace

### Step 1: Read the job logs (`~/tmp/docker3.logs`)

The logs revealed the full pipeline state:

```
LLM alignment returned 5 sections (3 chorus): lines 7-14, 16-25, 26-33
  → all confidence 0.95, all parsed successfully (0 rejected)

Structured lyrics LLM alignment completed in 4.36s (4 components)
Per-component feature computation completed in 0.47s (3 computed, 1 skipped, 4 total)

LLM classification: 3 to classify, 1 skipped (essential-only), 4 total
LLM classification: skipped component 3/4 (occurrence=3, type=chorus, role=none)
```

**Key evidence:** occurrence 3 had `role=none`, meaning it was skipped from LLM theme classification (only essential roles entry/exit/loop_target get classified). Occurrence 2 had the `exit` role (it WAS classified).

### Step 2: Trace the CLI flag → option mapping

`--compute-all-fields` in `ops/admin-cli/.../commands/audio.py:3000-3001`:

```python
if compute_all_fields:
    snap_to_downbeat = True
    energy_aware_roles = True  # ← THIS IS THE KEY
    classify_theme = True
    classify_vocal_posture = True
```

So despite `energy_aware_roles` defaulting to `False` (`models.py:83`), this run had it **enabled**. The user confirmed they ran `--compute-all-fields` without explicitly enabling energy-aware roles).

### Step 3: Trace the energy-aware role reassignment path

`extract_components` (`components.py:2025-2028`, pre-fix):

```python
if energy_aware_roles:
    components = _assign_roles_by_energy(
        components, gf.y, gf.sr, stems_dir=stems_dir
    )
```

`_assign_roles_by_energy` (`components.py:577-735`):
1. Collects all chorus components, deduplicates by `(start_time, end_time)` pairs.
2. If ≥2 unique pairs: computes an RMS-energy score for each pair's audio slice.
3. **Lowest energy → `role="entry"`, highest → `role="exit"`, all others → `role="none"`.**
4. Stems were NOT loaded (`--use-stems` is not part of `--compute-all-fields`), so it used **full-mix RMS only** (`components.py:705-711`).

**Root cause for Q1:** `_assign_roles_by_energy` overwrote the positional `exit` role on chorus occurrence 3 because occurrence 2 scored higher RMS energy. The LLM-aligned boundaries and positional roles from `_map_sections_to_components` were silently discarded. This is by design for the allin1/lyrics-repetition paths (where energy provides a useful signal), but wrong for the structured_lyrics_llm path where the LLM intentionally chose section boundaries and roles.

### Step 4: Trace the lyric extraction off-by-one

`_map_sections_to_components._sec_time` (`section_segmenter.py:249-251`):

```python
def _sec_time(s: Section) -> tuple[float, float]:
    start = lines[s.line_start - 1].time_seconds
    if s.line_end < n:
        end = lines[s.line_end].time_seconds   # ← next section's first line
    else:
        # estimate from avg line duration
```

This implements a **half-open `[start, next_line_time)`** convention — `end_time` is the timestamp of the line *after* the section's last line. This is standard for LRC (a line's duration extends until the next line's timestamp).

But `_extract_lyrics_for_component` (`classifier.py:128-133`, pre-fix):

```python
return [
    ln.text
    for ln in lrc_file.lines
    if ln.text and ln.text.strip()
    and start_time <= ln.time_seconds <= end_time   # ← CLOSED interval
]
```

Used a **closed `<=`** comparison. A line whose timestamp exactly equals `end_time` (i.e., the first line of the *next* section) was swept into the current component's lyric set.

**Verified with the actual LRC timestamps:**

| Chorus | LLM range | start_time | end_time | Extra line swept in by `<=` |
|--------|-----------|------------|----------|-----------------------------|
| 1 (occ=1) | L7–L14 | 61.80 | 124.17 (L15) | L15 (blank, silently dropped by `text.strip()` filter) |
| 2 (occ=2) | L16–L25 | 134.22 | 211.68 (L26) | **L26** (first line of chorus 3) |
| 3 (occ=3) | L26–L33 | 211.68 | ~268.97 (estimated) | None (no L34 exists) |

The log preview confirmed this: `docker3.logs:109` shows chorus 2's classifier lyrics starting with L17 and its preview runs through L26's content.

**Impact:** Chorus 2 and chorus 3 hashed to different lyric groups despite near-identical canonical lyrics, defeating the classifier's dedup and wasting an extra LLM call (3 unique groups instead of 2).

### Step 5: Trace the final-chorus line loss (Q3)

The second job (`docker_logs_chorus_missing_line.logs`) showed the LLM returning the last chorus as lines 26–33, but the persisted component showed 26–32. Q1/Q2 fixes were already applied (role was correctly `exit`, classifier interval was half-open), so this was a separate path.

**Key evidence — the `Section content alignment` diagnostics:**
```
Section content alignment: section 'chorus': no matching structured section
Section content alignment: section 'chorus': no matching structured section
```

Two of the three aligned choruses had no matching structured section. The structured lyrics contain only a single `[chorus]` block, but the LLM aligned 3 chorus occurrences; `_match_structured_sections` pairs 1:1, so the 2nd/3rd choruses correctly fell back to `None` (line 400-402 `continue`s, **no boundary change**). So `_validate_section_content_alignment` is NOT the cause — it leaves unmatched sections untouched.

**Root cause — `_validate_chorus_repetition` trims the last chorus.**

`align_structured_lyrics` calls `_validate_chorus_repetition(sections, ...)` as a defensive post-processing step (`structured_lyrics_aligner.py:675`). That validator (`section_segmenter.py:427-530`) scans each chorus backward from its last line looking for the first line whose normalized text appears **elsewhere** in the song, then sets `line_end` to that line (trim). Walk for chorus 3 (lines 26–33):

- L33 `祢同在裡　我心永遠屬祢` — does **not** repeat elsewhere (L14/L25 are `祢同在裡　我願永遠棲息`; `_normalize_line` keeps the difference) → skipped.
- L32 `亙古不變的約定　飛到地極尋見祢` — repeats at L13/L24 → `last_repeating_line_0 = 31` → since `31 < 32`, the **trim branch** fires, setting `line_end = 32` (33 → 32).

Verified by reproducing the normalization: `L33 == L14 ? False`, `L32 == L24 ? True`.

The trim heuristic assumes a trailing non-repeating line was erroneously swept in from the next section. That is correct for `segment_song` (LLM segments from scratch and can over-merge), but **wrong for `structured_lyrics_llm`**: the LLM aligned authoritative section labels and intentionally kept the outro variation `我心永遠屬祢` (which even appears in the structured lyrics as `我願永遠棲息 (我心永遠屬祢)`).

---

## Fix Summary

### Q1: Skip energy reassignment for `structured_lyrics_llm` source

**File:** `ops/analysis-service/src/sow_analysis/workers/components.py:2025-2033`

```python
# Skip energy reassignment for structured_lyrics_llm: the LLM
# already chose section boundaries and positional entry/exit roles
# intentionally; RMS-only energy scoring can demote the
# structurally-last chorus to role='none'.
if energy_aware_roles and source != "structured_lyrics_llm":
    components = _assign_roles_by_energy(
        components, gf.y, gf.sr, stems_dir=stems_dir
    )
```

**Rationale:** The `structured_lyrics_llm` source represents the highest-confidence identification path — the LLM aligns authoritative structured-lyrics section labels to LRC line ranges. Energy-based reassignment, which uses only RMS (no stems added by `--compute-all-fields`), is a blunt heuristic that can override musically meaningful positional roles. For structured-lyrics-aligned components, the positional `entry`/`exit` assignment from `_map_sections_to_components` should be respected.

### Q2: Change closed interval to half-open

**File:** `ops/analysis-service/src/sow_analysis/workers/classifier.py:132`

```python
# Before:
and start_time <= ln.time_seconds <= end_time
# After:
and start_time <= ln.time_seconds < end_time
```

**Rationale:** Every `end_time` computation in the pipeline (`_sec_time`, `identify_from_allin1_sections._lrc_line_range`, `identify_from_structured_lyrics`, `identify_from_lyrics_repetition`) uses the next section's first-line timestamp as the exclusive upper bound (half-open convention). The classifier's lyric extraction must match this convention. The single `<` ensures that a line whose timestamp exactly equals `end_time` — which belongs to the *next* section — is excluded.

### Q3: Skip chorus repetition cross-check for `structured_lyrics_llm` source

**File:** `ops/analysis-service/src/sow_analysis/workers/structured_lyrics_aligner.py:672-683`

Removed the call to `_validate_chorus_repetition(sections, lrc_content, ...)` from `align_structured_lyrics` (and its now-unused import). Before mapping sections to components, the structured-lyrics LLM path no longer runs the deterministic repetition validator.

**Rationale:** Same as Q1 — the `structured_lyrics_llm` path represents the highest-confidence identification. The LLM explicitly aligned authoritative structured-lyrics section labels to LRC ranges and intentionally kept lyrical variations (outro tags whose last line differs from earlier chorus occurrences). The repetition validator's trim heuristic (`section_segmenter.py:504-516`) drops those intentional variation lines — it trimmed the final chorus from 26–33 to 26–32. The validator remains active for `segment_song` (`llm_segmentation` source), where the LLM segments from scratch and can genuinely over-merge boundaries.

### Regression Tests

1. **`test_classifier.py::test_extract_lyrics_for_component_half_open_end`** — verifies that a line at exactly `end_time` is excluded from the lyric set.
2. **`test_components.py::TestExtractComponents::test_energy_aware_roles_skipped_for_structured_lyrics_llm`** — verifies `_assign_roles_by_energy` is not called when `source == "structured_lyrics_llm"`, and the positional `exit` role on the last chorus is preserved.
3. **`test_structured_lyrics_aligner.py::TestAlignStructuredLyrics::test_chorus_repetition_validation_skipped_for_structured_lyrics_llm`** — the LLM returns a chorus whose last line is a non-repeating variation while an interior line repeats; verifies `line_end` is preserved (not trimmed). Fails (line_end 2→1) if `_validate_chorus_repetition` runs.

---

## Diagnostic Checklist: Investigating Similar Component-Extraction Bugs

When a COMPONENT ANALYSIS job produces unexpected roles, missing components, or misaligned lyrics, follow this checklist:

### 1. Identify the identification source
Check the logs for the source string:
- `"structured_lyrics_llm"` — from `align_structured_lyrics` (highest priority)
- `"allin1_sections"` — from `identify_from_allin1_sections`
- `"lyrics_repetition"` — from `identify_from_lyrics_repetition`
- `"llm_segmentation"` — from `segment_song`
- `"none"` — no components found

Log line pattern: `Component extraction (...s)` preceded by `Structured lyrics LLM alignment completed` / `LLM segmentation completed` / `Component identification completed`.

### 2. Check which CLI flags were used
- `--compute-all-fields` implicitly enables `energy_aware_roles=True`, `snap_to_downbeat=True`, `classify_theme=True`, `classify_vocal_posture=True` (see `audio.py:3000-3001`).
- `--energy-aware-roles` alone enables only the energy reassignment.
- `--all-components` populates audio/LLM fields for ALL components (not just essential roles).
- `--use-stems` loads Demucs stems for energy scoring (drums/backbeat); without it, energy scoring is RMS-only.

### 3. For role misassignment bugs: trace through `_assign_roles_by_energy`
- If `energy_aware_roles=True` (from `--compute-all-fields` or explicit), check whether `_assign_roles_by_energy` ran (`components.py:2025`).
- It only operates on `component_type="chorus"` components (`components.py:612`).
- It deduplicates by `(start_time, end_time)` pairs — if only 1 unique pair, it returns unchanged (`components.py:622`).
- Without stems, it uses RMS-only scoring (`components.py:705-711`): `(rms - min) / (max - min)`.
- **Lowest energy → `entry`, highest → `exit`** (`components.py:722-733`). Verify the actual RMS values by slicing the audio at the component's `start_time`/`end_time`.
- Check if `source == "structured_lyrics_llm"` — if so, energy reassignment should now be skipped (post-fix).

### 4. For lyric misalignment bugs: compare `line_start`/`line_end` vs classifier lyrics
- The LLM alignment returns `line_start`/`line_end` (1-based, inclusive) — these are the "correct" LRC line indices.
- The classifier uses `_extract_lyrics_for_component(lrc_content, start_time, end_time)` — it filters by **timestamp range**, not by line indices.
- The `end_time` for non-final components is `lines[line_end].time_seconds` — i.e., the timestamp of the line *after* the section's last line (half-open convention).
- Verify: for each component, list LRC lines where `start_time <= ln.time_seconds < end_time`. This should match `line_start` to `line_end` (inclusive). If there's an off-by-one, check the `<=` vs `<` boundary.
- For the last component (`line_end == n`), `end_time` is estimated from average line duration — the off-by-one does not affect it (no next line exists).

### 5. Check the structured-lyrics alignment LLM response
- Logs contain `LLM response [LLM structured lyrics alignment] content:` followed by the JSON.
- Each section has `line_start`, `line_end` (1-based, inclusive), `confidence`, `rationale`.
- The parser (`_parse_alignment_json`) rejects sections that overlap, are out of range, or have invalid labels.
- `_validate_section_content_alignment` may repair `line_start` by ±2 lines if the LRC line at the original `line_start` doesn't fuzzy-match the structured section's first line. Check for `Section content alignment:` debug lines.

### 6. Check the LRC parse
- `parse_lrc` (`lrc_parser.py`) returns `LRCLine(time_seconds, text, raw_timestamp)`.
- Blank lines (empty `text`) are included in `parse_lrc().lines` but filtered out by `_extract_lyrics_for_component` (`if ln.text and ln.text.strip()`).
- `identify_from_structured_lyrics` and `identify_from_lyrics_repetition` filter blank lines into a `filtered_map` for 1-based indexing.
- Verify LRC line numbering: the numbered LRC in the logs is 1-based and matches `parse_lrc().lines` indices (after filtering).

### 7. Reproduce with a smoke test
- Re-run the job with `--force` to bypass cache.
- If the bug is role-related, add `--segmentation-mode structured_lyrics` to isolate the structured-lyrics path.
- If the bug is lyric-related, log the `_extract_lyrics_for_component` output for each component and compare to `line_start`/`line_end`.
- Use `eval` to replay the LRC parsing and timestamp arithmetic outside the service.

### 8. Check for content-alignment validator repairs
- `_validate_section_content_alignment` (`structured_lyrics_aligner.py:345-445`) may shift `line_start` by ±2 if the LLM's alignment doesn't match the structured lyrics' first line.
- Log pattern: `Section content alignment: section '<label>': line_start X->Y (repaired: ...)`.
### 9. For a missing final chorus line: check `_validate_chorus_repetition`
- If a chorus's `line_end` is one shorter than the LLM's response (e.g. 26–33 → 26–32), suspect the deterministic repetition validator, not the content-alignment validator.
- `_validate_chorus_repetition` scans the chorus backward from its last line for the first line that repeats elsewhere in the song, and trims `line_end` to it (`section_segmenter.py:504-516`). A final-chorus **outro variation** (last line differs from earlier occurrences) is intentionally kept by the LLM but dropped by the trim.
- The validator is now skipped for the `structured_lyrics_llm` source but still runs for `llm_segmentation` (`segment_song`). If you observe this under `llm_segmentation`, the trim is expected behavior for over-merged boundaries.
- Verify by reproducing: normalize LRC lines and check whether the dropped last line's text appears elsewhere; if not, the trim is what shortened it.

### 10. When is a "no matching structured section" diagnostic benign?
- `_validate_section_content_alignment` pairs aligned sections to structured sections 1:1. When the LLM aligns more occurrences of a label than exist structurally (e.g. 3 choruses vs 1 `[chorus]` block), the extra aligned sections get `ssec=None` → "no matching structured section", and are passed through **unchanged** (line 400-402). Those diagnostics do NOT cause line loss.
