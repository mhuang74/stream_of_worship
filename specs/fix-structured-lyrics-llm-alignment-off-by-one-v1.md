# Implementation Plan: Fix Structured Lyrics LLM Alignment Off-By-One (v1)

> **Goal:** Eliminate the off-by-one line-number drift in `align_structured_lyrics()` that causes Chorus components to start at LRC line 2 of the Chorus instead of line 1, producing misaligned `lyrics_excerpt`, misaligned theme classification lyrics, and missing the first Chorus line from the entry component.
>
> **Root cause:** The LLM, when faced with a structured Verse section that has 5 lines but an LRC where Verse lines 4+5 are merged into a single LRC line (4 LRC lines total), extends the Verse section by one LRC line to preserve "5 lines = 5 LRC lines" count equality. This swallows structured-Chorus-line-1 into the Verse. The off-by-one then cascades to every subsequent section until a structural reset (blank LRC line, or a long repeated-Chorus block) lets the LLM re-anchor.
>
> **Strategy:** Three-layer defense — (1) prompt hardening so the LLM doesn't make the mistake, (2) deterministic post-alignment content validation that catches and repairs the mistake if it slips through, (3) LLM retry loop with targeted feedback when validation fails and repair is not possible.

---

## Problem Analysis

### Observed Behavior

For a song with structured lyrics:

```
[Verse]
主 袢使卑微轉為尊貴
使傷心流淚轉為笑顏
患難生忍耐 忍耐生老練
老練生盼望 盼望不至羞愧
就沒有失望

[Chorus]
心中充滿盼望 盼望使眼睛明亮
道路雖崎嶇 袢與我同行
心中充滿盼望 盼望使信心剛強
信靠每一句應許 生命充滿亮光
```

And LRC where Verse lines 4+5 are merged:

```
1  [00:10.05] 主袢使卑微 轉為尊貴
2  [00:15.15] 使傷心流淚 轉為笑顏
3  [00:20.42] 患難生忍耐 忍耐生老練
4  [00:25.84] 老練生盼望 盼望不至羞愧 就沒有失望    ← merged
5  [00:33.75] 心中充滿盼望 盼望使眼睛明亮             ← Chorus line 1
6  [00:40.00] 道路雖崎嶇 袢與我同行
7  [00:44.53] 心中充滿盼望 盼望使信心剛強
8  [00:50.55] 信靠每一句應許 生命充滿亮光
9  [00:58.46] 生命充滿亮光
...
```

The LLM returned:

```json
{"sections": [
  {"label": "verse",  "line_start": 1,  "line_end": 5},   ← WRONG (should be 1-4)
  {"label": "chorus", "line_start": 6,  "line_end": 9},   ← WRONG (should be 5-8 or 5-9)
  {"label": "verse",  "line_start": 11, "line_end": 15},  ← WRONG (cascaded)
  {"label": "chorus", "line_start": 16, "line_end": 18},  ← WRONG (cascaded)
  {"label": "chorus", "line_start": 20, "line_end": 33}  ← CORRECT (re-anchored)
]}
```

### Why Theme Step Appeared "Correct"

The theme classification step (`classifier.py:297-312`) extracts lyrics by **time-window filtering** the LRC (`_extract_lyrics_for_component`), NOT by reading `component.lyrics_excerpt`. For Component 3 (chorus occ 3, exit), the LLM aligned lines 20-33 correctly, so the time window `[lrc_lines[19].time_seconds, lrc_lines[32].time_seconds]` captures the correct Chorus lyrics starting with "心中充滿盼望 盼望使眼睛明亮".

For Component 1 (chorus occ 1, entry), the LLM aligned lines 6-9, so the time window starts at LRC line 6 ("道路雖崎嶇") — missing both "心中充滿盼望 盼望使眼睛明亮" (line 5, misclassified as verse) and the first half of the Chorus. The theme log truncates at 60 chars, so the missing lines aren't visually obvious.

### Root Cause: Line-Count Anchoring Bias

The LLM treats structured-section line count as a hard constraint. When the LRC merges two structured lines into one, the LLM extends the section boundary by one LRC line to preserve count equality, rather than accepting that the LRC has fewer lines for the same content.

### Why `_validate_chorus_repetition` Doesn't Catch It

`_validate_chorus_repetition` (`section_segmenter.py:417-520`) only trims `line_end` downward when the last line of a chorus repeats elsewhere. It never expands boundaries or checks whether `line_start` is correct. In this case:
- Chorus 1 (lines 6-9): last line "生命充滿亮光" repeats at lines 32-33 → trims to line 9 (no change, already ends on a repeating line).
- The validator confirms the boundary as-is, adding a +0.05 confidence bonus.

The validator has no signal that line 5 ("心中充滿盼望 盼望使眼睛明亮") was wrongly absorbed into the Verse section.

---

## Architecture

### Existing Infrastructure (reused)

| Component | Location | Reuse |
|-----------|----------|-------|
| `align_structured_lyrics()` | `structured_lyrics_aligner.py:314-416` | **Modified** — add validation + retry loop |
| `_build_alignment_prompt()` | `structured_lyrics_aligner.py:108-147` | **Modified** — add line-merging hint to system prompt |
| `_parse_alignment_json()` | `structured_lyrics_aligner.py:150-276` | **Modified** — return breakdown includes validation diagnostics |
| `_render_numbered_lrc()` | `section_segmenter.py:100-111` | Direct reuse |
| `_render_structured_sections()` | `structured_lyrics_aligner.py:77-105` | Direct reuse |
| `_normalize_for_matching()` | `components.py:1174-1180` | Direct reuse — zhconv + `_normalize_line` |
| `_lines_match()` | `components.py:1183-1195` | Direct reuse — exact or rapidfuzz > 85 |
| `call_llm_with_retry()` | `llm_rate_limit.py` | Direct reuse |
| `Section` dataclass | `section_segmenter.py:71-77` | Direct reuse |
| `_validate_chorus_repetition()` | `section_segmenter.py:417-520` | Direct reuse (unchanged — runs after new validator) |
| `_map_sections_to_components()` | `section_segmenter.py:233-380` | Direct reuse |

### New: `_validate_section_content_alignment()`

A deterministic post-alignment validator that checks whether each LLM-aligned section's `line_start` actually corresponds to the structured section's first lyric line. This is the key new component — it catches the off-by-one that `_validate_chorus_repetition` cannot.

---

## Changes

### Change 1 — Prompt hardening: line-merging hint

**File:** `ops/analysis-service/src/sow_analysis/workers/structured_lyrics_aligner.py`
**Location:** `_build_alignment_prompt()`, lines 117-132 (system prompt string)

Add a paragraph to the system prompt after the "Sections may repeat" sentence:

```
**Line-count mismatch is expected.** The LRC may merge two short structured
lines into one LRC line, or split one long structured line into two LRC
lines. Do NOT preserve line-count equality between structured sections and
LRC ranges. Instead, match by lyric CONTENT: a section's line_start must
point to the LRC line whose text matches (fuzzy) the section's FIRST
structured line, and line_end must point to the LRC line whose text matches
(fuzzy) the section's LAST structured line. If the structured section has 5
lines but the LRC only has 4 lines covering that content, line_end must
still equal the 4th LRC line — not the 5th.
```

**Rationale:** The current prompt says "map each section to a range of LRC line numbers" but never explicitly tells the LLM that line counts may differ. The LLM defaults to count-preserving behavior. This hint makes the expectation explicit.

**Risk:** Prompt length increase is ~400 chars. Negligible relative to the numbered LRC + few-shot examples (typically 5-15KB).

### Change 2 — New validator: `_validate_section_content_alignment()`

**File:** `ops/analysis-service/src/sow_analysis/workers/structured_lyrics_aligner.py`
**Location:** New function, inserted after `_parse_alignment_json()` (after line 276)

#### Signature

```python
def _validate_section_content_alignment(
    sections: list[Section],
    structured_lyrics_json: str,
    lrc_content: str,
    fuzzy_threshold: int = 85,
) -> tuple[list[Section], list[str]]:
    """Validate that each aligned section's line_start matches its structured first line.

    For each Section, compares the normalized text of:
      - The LRC line at (line_start - 1) against the structured section's FIRST line.
      - The LRC line at (line_end - 1) against the structured section's LAST line.

    If line_start doesn't match, attempts a bounded repair: searches ±2 LRC lines
    around line_start for a match. If found, adjusts line_start (and line_end by
    the same delta, clamped to valid range). If no repair is possible, the section
    is flagged with a diagnostic but kept (downstream chorus validation may still
    catch it).

    Returns (repaired_sections, diagnostics) where diagnostics is a list of
    human-readable strings describing each repair or flag.
    """
```

#### Algorithm

```
1. Parse structured_lyrics_json → list of (label, first_line, last_line) per section.
   - Match structured sections to aligned Sections by ORDER (both are in song order).
   - If counts differ (structured has 4 sections, aligned has 5), match by best
     fuzzy alignment of first_line to the LRC line at each Section.line_start.
2. Parse LRC → list of normalized LRC lines.
3. For each (aligned_section, structured_section) pair:
   a. Normalize structured first_line via _normalize_for_matching().
   b. Get LRC line text at (line_start - 1), normalize via _normalize_for_matching().
   c. If _lines_match(structured_first, lrc_at_start): ✓ pass.
   d. Else: search ±2 lines around line_start for a match.
      - If found at offset delta: adjust line_start += delta, line_end += delta
        (clamped: 1 <= line_start, line_end <= n_lines, line_start <= line_end).
        Record diagnostic: "section '{label}': line_start {old}->{new} (repaired:
        LRC line '{actual}' didn't match structured first line '{expected}')".
      - If not found: record diagnostic flag (no adjustment):
        "section '{label}': line_start {start} may be misaligned (LRC line
        '{actual}' doesn't match structured first line '{expected}')".
4. Re-check overlap after adjustments (adjustments may cause new overlaps).
   - If an adjusted section now overlaps a neighbor, revert the adjustment and
     flag instead.
5. Return (sections, diagnostics).
```

#### Key design decisions

- **±2 line search window:** Merges/splits rarely shift by more than 1-2 LRC lines. A wider window risks false positives (matching a repeated line in a different section).
- **Adjustment preserves section length:** `line_end` shifts by the same delta as `line_start`, so the section's LRC line span stays the same length. This is correct because the merge/split affects the boundary, not the interior.
- **Overlap re-check:** Adjustments are reverted if they create overlaps. This prevents the validator from introducing a new problem while fixing another.
- **Diagnostics returned, not just logged:** The caller (`align_structured_lyrics`) uses diagnostics to decide whether to retry the LLM. If all sections pass or are repaired, no retry. If sections are flagged (unrepairable), the caller may retry with diagnostics as feedback.

#### Normalization reuse

Uses `_normalize_for_matching()` from `components.py:1174-1180` (zhconv → traditional + `_normalize_line`), ensuring the validator handles simplified/traditional Chinese mismatches the same way the deterministic `identify_from_structured_lyrics` does.

#### Matching reuse

Uses `_lines_match()` from `components.py:1183-1195` (exact normalized OR rapidfuzz > 85), ensuring fuzzy lyric variations (minor wording differences between YouTube description and LRC) are handled.

### Change 3 — Retry loop with diagnostic feedback

**File:** `ops/analysis-service/src/sow_analysis/workers/structured_lyrics_aligner.py`
**Location:** `align_structured_lyrics()`, lines 314-416

#### Current flow (single-shot)

```
1. Build prompt → messages
2. call_llm_with_retry → response text
3. _parse_alignment_json → sections (or None → return [])
4. _validate_chorus_repetition → sections
5. _map_sections_to_components → components
6. return components
```

#### New flow (retry with feedback)

```
1. Build prompt → messages
2. For attempt in 1..max_attempts (default 2):
   a. call_llm_with_retry → response text
   b. _parse_alignment_json → sections (or None → return [] on attempt 1,
      return [] on attempt 2 if also None)
   c. _validate_section_content_alignment(sections, structured_lyrics_json,
      lrc_content) → (repaired_sections, diagnostics)
   d. If no "flagged" diagnostics (all passed or repaired):
      sections = repaired_sections
      break
   e. If attempt < max_attempts AND any flagged diagnostics:
      Append a corrective user message to messages:
        "Your previous alignment had issues:
         - section 'verse': line_start 1 may be misaligned (LRC line '心中充滿盼望...'
           doesn't match structured first line '主袢使卑微 轉為尊貴').
         - section 'chorus': line_start 6 may be misaligned (LRC line '道路雖崎嶇...'
           doesn't match structured first line '心中充滿盼望 盼望使眼睛明亮').
         Re-align with correct line_start values."
      Continue to attempt 2.
   f. If attempt == max_attempts:
      Use repaired_sections (with whatever repairs were possible).
      Log warnings for all flagged diagnostics.
3. _validate_chorus_repetition(sections, lrc_content) → sections
4. _map_sections_to_components(sections, ...) → components
5. return components
```

#### New config setting

```python
# In config.py, alongside SOW_LLM_STRUCTURED_LYRICS_TIMEOUT_SECONDS:
SOW_LLM_STRUCTURED_LYRICS_MAX_ATTEMPTS: int = 2
# Maximum number of LLM calls for structured lyrics alignment with diagnostic
# feedback. 1 = single-shot (no retry). 2 = one retry if validation flags
# misaligned sections. Each attempt is a full LLM call (costs tokens).
```

Default 2: one retry is enough to correct the off-by-one in the observed case, while bounding cost. Set to 1 to disable retries (useful for A/B testing the prompt-only fix).

#### Corrective message format

The corrective message is appended to the `messages` list as a new user message (not replacing the original). This preserves the full context (numbered LRC + structured sections) while adding the failure feedback. The LLM sees:

```
[user]: Here are a few reference examples...
[user]: Now align the structured lyrics sections to this numbered LRC:
        1  [00:10.05] 主袢使卑微 轉為尊貴
        ...
[user]: Output JSON only:
[assistant]: {"sections": [...]}  ← previous (wrong) response
[user]: Your previous alignment had issues:
        - section 'verse': line_start 1, but LRC line 1 '主袢使卑微 轉為尊貴'
          doesn't match the structured verse's first line '主 袢使卑微轉為尊貴'.
          Wait — actually it DOES match (fuzzy). The issue is line_end=5: LRC
          line 5 '心中充滿盼望 盼望使眼睛明亮' is the Chorus's first line, not
          the Verse's last line '就沒有失望'. Reduce line_end to 4.
        - section 'chorus': line_start 6, but LRC line 6 '道路雖崎嶇 袢與我同行'
          is the Chorus's SECOND line. The Chorus's first line is '心中充滿盼望
          盼望使眼睛明亮' at LRC line 5. Set line_start to 5.
        Re-align with correct line_start and line_end values.
```

**Note:** The diagnostic format from `_validate_section_content_alignment` focuses on `line_start` mismatches. For the observed case, the Verse's `line_start=1` is correct (matches "主袢使卑微"), but `line_end=5` is wrong. The validator should also check `line_end` against the structured section's LAST line. See Change 2 algorithm step 3 — the validator checks both `line_start` (against structured first) and `line_end` (against structured last). The diagnostic message for `line_end` mismatch is analogous.

### Change 4 — `_parse_alignment_json` breakdown includes validation diagnostics

**File:** `ops/analysis-service/src/sow_analysis/workers/structured_lyrics_aligner.py`
**Location:** `_parse_alignment_json()`, return value at line 270-276

No change to the function signature or return type. The validation diagnostics from `_validate_section_content_alignment` are logged separately by `align_structured_lyrics` at DEBUG level, and the corrective feedback (if retrying) is constructed from the diagnostics list.

The existing `breakdown` string from `_parse_alignment_json` continues to describe parse-level issues (rejected sections, overlaps). The new diagnostics describe content-alignment issues. Both are logged:

```python
logger.debug("Structured lyrics alignment parse: %s", parse_breakdown)
if diagnostics:
    logger.debug("Section content alignment diagnostics: %s", "; ".join(diagnostics))
    for d in diagnostics:
        if "may be misaligned" in d:
            logger.warning("Section content alignment issue: %s", d)
```

### Change 5 — Tests

**File:** `ops/analysis-service/tests/test_structured_lyrics_aligner.py`

#### New test class: `TestValidateSectionContentAlignment`

1. **`test_all_sections_aligned_correctly`** — all `line_start` values match structured first lines → no repairs, no flags, empty diagnostics.

2. **`test_line_start_off_by_one_repaired`** — Verse `line_end=5` (should be 4), Chorus `line_start=6` (should be 5). Validator repairs Chorus `line_start` to 5 (finds "心中充滿盼望..." at LRC line 5, which matches structured Chorus first line). Verse `line_end` is NOT repaired by this validator (it only checks `line_start` against structured first line, and Verse `line_start=1` is correct). The Verse `line_end` issue is caught by the overlap check after Chorus repair: Chorus now starts at 5, Verse ends at 5 → overlap → Verse `line_end` is trimmed to 4.

3. **`test_line_start_mismatch_unrepairable_flagged`** — `line_start` points to a line that doesn't match within ±2 lines → section is flagged, not repaired. Diagnostics contain "may be misaligned".

4. **`test_repair_reverted_on_new_overlap`** — repairing a section's `line_start` would cause it to overlap a neighbor → repair is reverted, section is flagged.

5. **`test_simplified_traditional_mismatch_handled`** — structured lyrics in traditional, LRC in simplified → `_normalize_for_matching` (zhconv) handles it, no false flag.

6. **`test_fuzzy_match_minor_wording_variation`** — structured line "主 袢使卑微轉為尊貴" vs LRC line "主袢使卑微 轉為尊貴" (space difference) → `_lines_match` (rapidfuzz > 85) handles it, no false flag.

7. **`test_structured_section_count_mismatch`** — structured lyrics have 4 sections, LLM returned 5 sections → validator matches by best fuzzy alignment of first_line, doesn't crash.

#### New test class: `TestAlignStructuredLyricsRetry`

8. **`test_retry_on_validation_flag`** — mock LLM to return wrong alignment on attempt 1, correct alignment on attempt 2. Verify the corrective message was appended to messages, and the final components use the attempt-2 alignment.

9. **`test_no_retry_when_all_repaired`** — mock LLM to return alignment with repairable off-by-one. Validator repairs it. Verify only 1 LLM call (no retry), and the repaired alignment is used.

10. **`test_max_attempts_exhausted_uses_best_effort`** — mock LLM to always return wrong alignment. Verify 2 LLM calls (max_attempts=2), final components use the repaired (but still flagged) alignment, warnings logged.

11. **`test_retry_disabled_when_max_attempts_1`** — set `SOW_LLM_STRUCTURED_LYRICS_MAX_ATTEMPTS=1`. Mock LLM returns wrong alignment. Verify 1 LLM call, no retry, validator still runs and repairs what it can.

#### Updates to existing tests

12. **`TestBuildAlignmentPrompt.test_system_prompt_contains_line_merging_hint`** — verify the new prompt paragraph ("Line-count mismatch is expected") is present in the system message.

13. **`TestAlignStructuredLyrics.test_returns_components_with_correct_source`** — no change needed (existing test still passes; the validator runs but finds no issues on the simple 2-section fixture).

#### Live LLM test (gated)

14. **`TestAlignStructuredLyricsLive.test_align_off_by_one_song`** — run alignment on a song with known line-merging (the user's reported song, or a synthetic fixture with the same pattern). Verify Chorus 1 `line_start` points to the LRC line containing "心中充滿盼望 盼望使眼睛明亮" (structured Chorus line 1), not "道路雖崎嶇" (structured Chorus line 2).

### Change 6 — Config setting

**File:** `ops/analysis-service/src/sow_analysis/config.py`
**Location:** After `SOW_LLM_STRUCTURED_LYRICS_TIMEOUT_SECONDS`

```python
SOW_LLM_STRUCTURED_LYRICS_MAX_ATTEMPTS: int = 2
# Maximum LLM calls for structured lyrics alignment with diagnostic feedback.
# 1 = single-shot (no retry). 2 = one retry if _validate_section_content_alignment
# flags unrepairable misalignments. Each attempt is a full LLM call.
```

**File:** `ops/analysis-service/.env.example`

```env
# Structured lyrics LLM alignment max attempts (1 = no retry, 2 = one retry)
SOW_LLM_STRUCTURED_LYRICS_MAX_ATTEMPTS=2
```

---

## No Changes Required

| Area | Why |
|------|-----|
| `_validate_chorus_repetition()` | Unchanged — runs after the new validator, handles chorus `line_end` trimming independently. The new validator handles `line_start` and `line_end` against structured content; chorus repetition handles `line_end` against repetition in the LRC. They're complementary. |
| `_map_sections_to_components()` | Unchanged — receives validated/repaired Sections, maps to components as before. |
| `identify_from_structured_lyrics()` (deterministic) | Unchanged — still dead code in production path, still tested by `test_structured_lyrics_identification.py`. Not wired into `extract_components`. |
| `extract_components()` in `components.py` | Unchanged — already calls `align_structured_lyrics()` correctly. The retry loop is internal to `align_structured_lyrics`. |
| `classifier.py` (theme step) | Unchanged — benefits automatically from correct `lyrics_excerpt` and correct time-window filtering once alignment is fixed. |
| `components.json` schema | Unchanged — same payload shape. The `source` field remains `"structured_lyrics_llm"`. |
| DB schema | Unchanged. |
| Admin CLI | Unchanged — `sow-admin audio components --segmentation-mode structured_lyrics` works as before, just produces correct results. |
| Few-shot examples file | Unchanged — the 3 existing examples already demonstrate correct alignment. The prompt hint (Change 1) is the primary prompt-level fix; adding a 4th example specifically for line-merging is optional (see Open Questions). |

---

## Data Flow (with changes)

```
1. extract_components() calls align_structured_lyrics(structured_lyrics, lrc_content, ...)
   ↓
2. align_structured_lyrics():
   a. _build_alignment_prompt(lrc_content, structured_lyrics_json, few_shot)
      - System prompt now includes line-merging hint [Change 1]
   b. For attempt in 1..max_attempts [Change 3]:
      i.   call_llm_with_retry(messages) → response text
      ii.  _parse_alignment_json(text, n_lines) → sections (or None → return [])
      iii. _validate_section_content_alignment(sections, structured_lyrics_json,
           lrc_content) → (repaired_sections, diagnostics) [Change 2]
      iv.  If no flagged diagnostics: sections = repaired_sections; break
      v.   If flagged and attempt < max_attempts: append corrective message
           to messages; continue
      vi.  If flagged and attempt == max_attempts: use repaired_sections;
           log warnings
   c. _validate_chorus_repetition(sections, lrc_content) → sections (unchanged)
   d. _map_sections_to_components(sections, ...) → components (unchanged)
   e. return components
   ↓
3. extract_components() sets source = "structured_lyrics_llm"
   ↓
4. ThemeClassifier.classify_components(components, lrc_content)
   - _extract_lyrics_for_component uses [start_time, end_time] from now-correct
     component boundaries → captures correct Chorus lyrics
   ↓
5. _serialize_components → components.json saved to R2
```

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Validator's ±2 line search matches a repeated line in a different section (false positive repair) | Overlap re-check after repair: if the repair causes an overlap with a neighbor, revert and flag instead. Also, the search is bounded to ±2, and repeated lines within ±2 of the expected position are rare (they'd have to be in the immediately adjacent section). |
| Retry loop doubles LLM cost for songs that need retry | Default `max_attempts=2` — only retries when validation flags unrepairable issues. Songs with correct alignment (the majority) make 1 call. Set `max_attempts=1` to disable retries entirely. |
| Corrective message confuses the LLM (it "fixes" the wrong thing) | The corrective message is specific: it names the section label, the current line_start, the actual LRC line text, and the expected structured line text. The LLM has all the information to make a targeted correction. If attempt 2 is also wrong, the best-effort repaired result is used (not worse than the original single-shot behavior). |
| Validator is too strict (flags correct alignments due to fuzzy match threshold) | `_lines_match` uses rapidfuzz > 85, which is lenient enough for minor wording variations but strict enough to catch the off-by-one (where the LRC line is from a completely different section). The ±2 search window also helps: if the first line is at line_start+1 (merge case), the validator finds it and repairs. |
| Structured section count ≠ aligned section count (LLM merged/split sections) | Validator matches by best fuzzy alignment of first_line, not by index. If counts differ significantly (>1), the validator flags all sections and lets the retry loop handle it. |
| Prompt hint makes the prompt too long | The hint is ~400 chars. The numbered LRC + few-shot examples are typically 5-15KB. The hint is <5% of total prompt length. No concern. |
| New config setting `SOW_LLM_STRUCTURED_LYRICS_MAX_ATTEMPTS` not respected | Tested explicitly in `test_retry_disabled_when_max_attempts_1`. |

---

## Migration Path

No migration needed — the fix is purely algorithmic. Existing cached `components.json` files in R2 are not invalidated; they'll be replaced on the next `--force` re-run. Songs that were previously misaligned will produce correct alignments on the next component analysis run.

For bulk re-processing of songs known to have the off-by-one issue:

```bash
sow-admin audio components <song_id> --segmentation-mode structured_lyrics --force
```

---

## Open Questions for Clarification

1. **Should the validator also check `line_end` against the structured section's LAST line?** The observed bug has both `line_start` and `line_end` wrong for the Verse (line_end=5 should be 4) and Chorus (line_start=6 should be 5, line_end=9 should be 8 or 9). The plan above checks both. However, `line_end` is also checked by `_validate_chorus_repetition` (for chorus sections only). Recommendation: validator checks both `line_start` and `line_end` for ALL sections; `_validate_chorus_repetition` continues to handle chorus-specific `line_end` trimming independently. They don't conflict.

2. **Should a 4th few-shot example be added specifically demonstrating line-merging?** The prompt hint (Change 1) is the primary fix. A few-shot example would reinforce it. Recommendation: add a 4th example to `structured_lyrics_alignment_few_shot.json` showing a Verse with 5 structured lines mapping to 4 LRC lines (merged). This is optional but recommended for robustness. The example must NOT come from a held-out fixture song.

3. **Should the corrective message include the full numbered LRC again?** The original user message already contains it. Appending a corrective user message (not replacing) preserves context. The LLM sees the original LRC + the corrective feedback. Recommendation: do not repeat the LRC in the corrective message — it's already in context and repeating it wastes tokens.

4. **Should `max_attempts` default be 2 or 3?** The observed off-by-one is a systematic LLM bias that a single retry with targeted feedback should fix. If the retry also fails, a 3rd attempt is unlikely to help (the LLM doesn't understand the feedback). Recommendation: default 2. Can be tuned via config if production logs show retry-attempt-2 still frequently wrong.

5. **Should the validator's ±2 search window be configurable?** Hardcoding 2 is simpler. If production logs show repairs failing because the offset is 3+, make it configurable. Recommendation: hardcode 2 for now, add config if needed later.

6. **Should the validator handle the case where the LLM returns fewer sections than the structured lyrics (e.g., merged a Verse + Chorus into one section)?** The current plan matches by best fuzzy alignment and flags mismatches. A merged-sections scenario would be flagged and retried. Recommendation: handle in the retry loop (the corrective message would say "you returned 4 sections but the structured lyrics have 5 sections"). This is a future enhancement; the current plan focuses on the off-by-one within correctly-counted sections.
