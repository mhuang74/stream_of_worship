# Canonical-Guided Lyrics Reconstruction from Word-Level ASR

## Problem

The multi-chunk LRC pipeline (`gen_lrc_qwen3_asr_mvsep_force_align_v2.py`) collapses word-level ASR output into a single space-joined string per chunk, destroying line structure. This produces 2 LRC lines from 17 ASR segments (see `specs/fix_multi_chunk_lrc_line_collapse.md`).

The prior fix spec proposes using **ASR sentence boundaries** to preserve line structure. But ASR sentence boundaries are unreliable — they may split mid-lyric-line, merge lines, or create boundaries at different points than the songwriter intended. Since canonical lyrics are always available, we can do better: **use canonical lyrics as a template to reconstruct line structure from the word stream**.

This approach also applies to the **single-chunk path**: instead of passing ASR sentence text to the forced aligner (which may have transcription errors), we pass canonical text with correct line structure, yielding better alignment accuracy.

## Core Idea

Given:
- `asr_words`: ordered list of `(start, end, text)` tuples from ASR (word-level, each CJK char is a separate "word")
- `canonical_lines`: ordered list of canonical lyric lines (e.g., 11 lines for "I Will Sing Hallelujah")

Produce:
- `reconstructed_lines`: list of `(canonical_line_idx, word_indices)` tuples, where each tuple maps a canonical line to the contiguous range of ASR words that correspond to it
- The concatenation of `asr_words[i][2]` for each group, after CJK space stripping, should closely match the canonical line text

This reconstruction happens **before** forced alignment. The canonical text (not ASR text) is then passed to the forced aligner, which produces per-line timestamps.

## Algorithm: Canonical-Guided Word Grouping

### Step 1: Normalize both sides to a common representation

```python
def _normalize_for_matching(text: str) -> str:
    """Strip all punctuation, whitespace, and normalize CJK variants for matching."""
    import re
    from zhconv import convert

    # Convert to simplified for matching (canonical may be traditional)
    text = convert(text, "zh-hans")
    # Remove all punctuation and whitespace
    text = re.sub(r'[^\u4e00-\u9fff\u3400-\u4dbf a-zA-Z]', '', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text
```

- Normalize each canonical line → `canonical_norm[i]`
- Normalize the concatenation of all ASR word texts (after CJK space stripping) → `asr_norm` (a single string)
- Also build `asr_word_norms`: list of normalized individual word texts, preserving the same index mapping

### Step 2: Greedy sequential alignment of ASR words to canonical lines

Walk through `asr_word_norms` sequentially, consuming words that match each canonical line in order. When canonical lines repeat (chorus/verse), wrap around.

```python
def reconstruct_lines_from_words(
    asr_words: list[tuple[float, float, str]],
    canonical_lines: list[str],
) -> list[tuple[int, list[int]]]:
    """Group ASR words into canonical line assignments.

    Returns:
        List of (canonical_line_idx, word_indices) tuples.
        canonical_line_idx may repeat when the song repeats verses/choruses.
        word_indices are contiguous ranges into asr_words.
    """
```

**Algorithm detail:**

1. Build `asr_norm_stream`: concatenate all normalized word texts into one string, tracking the character offset range `[char_start, char_end)` that each word occupies in this stream.

2. Build `canonical_norm_lines`: normalize each canonical line.

3. For each canonical line in sequence (with wrap-around), find the next contiguous span of words in the ASR stream that best matches it:

   - Maintain a `word_cursor` (index into `asr_words`).
   - For canonical line `i`, compute a **match window**: try matching `canonical_norm_lines[i]` against the ASR stream starting at `word_cursor`, with a lookahead of up to `MAX_LOOKAHEAD_WORDS` (default: 30) words.
   - For each candidate end position `j` (from `word_cursor` to `word_cursor + MAX_LOOKAHEAD_WORDS`):
     - Concatenate `asr_word_norms[word_cursor:j+1]` → `candidate_text`
     - Compute `fuzz.token_set_ratio(candidate_text, canonical_norm_lines[i]) / 100.0` → `score`
     - Also compute `fuzz.partial_ratio(candidate_text, canonical_norm_lines[i]) / 100.0` → `partial_score`
     - Use `partial_score` if the canonical line has ≤ 3 CJK chars (short-fragment rule from existing code)
   - Pick the `j` that maximizes the score, subject to `score >= MIN_LINE_SCORE` (default: 0.40).
   - If score < `MIN_LINE_SCORE`, this canonical line has no good match in the current window. Skip it (emit no words for this line) and advance to the next canonical line.
   - Otherwise, emit `(i, list(range(word_cursor, j+1)))` and advance `word_cursor` to `j+1`.

4. **Wrap-around for repeats**: When `i` reaches the end of `canonical_lines`, reset `i = 0` and continue as long as `word_cursor < len(asr_words)`. This handles verse/chorus repeats.

5. **Unmatched trailing words**: If words remain after all canonical lines are exhausted (even after wrap-around), group them into a final "unmatched" segment using the word-fallback time-gap heuristic (existing `_word_fallback_for_chunk` logic with 1.0s gap threshold).

### Step 3: Handle edge cases

**Short canonical lines (≤ 3 CJK chars):** Use `partial_ratio` instead of `token_set_ratio` to avoid score collapse (same rule as existing `_score` in `gen_lrc_qwen3_asr_local.py`).

**ASR words with no canonical match:** If a span of words at the beginning or between canonical lines doesn't match any canonical line well, these are likely instrumental/filler/ad-lib segments. Emit them as separate "unmatched" lines with their raw ASR text (after CJK space stripping).

**Canonical lines with no ASR match:** If a canonical line scores below `MIN_LINE_SCORE` against all candidate word spans, skip it. The forced aligner would produce poor results for a line with no audio evidence. Log a warning.

**Mixed Chinese/English lines:** The normalization step preserves ASCII letters, so English portions (e.g., "I will sing Hallelujah") are matched character-by-character alongside CJK characters.

### Step 4: Produce line-structured text for forced alignment

From the `(canonical_line_idx, word_indices)` assignments:

```python
def build_aligned_text(
    asr_words: list[tuple[float, float, str]],
    line_assignments: list[tuple[int, list[int]]],
    canonical_lines: list[str],
) -> list[str]:
    """Build the text lines to pass to the forced aligner.

    Uses canonical text (not ASR text) for each matched line.
    Unmatched words produce lines with CJK-stripped ASR text.
    """
    lines = []
    for canonical_idx, word_indices in line_assignments:
        if canonical_idx >= 0:
            lines.append(canonical_lines[canonical_idx])
        else:
            # Unmatched segment: use ASR text with CJK spaces stripped
            raw = " ".join(asr_words[i][2] for i in word_indices)
            lines.append(_strip_cjk_spaces(raw))
    return lines
```

**Key insight:** We pass **canonical text** to the forced aligner, not ASR text. This means:
- The forced aligner aligns correct, well-structured text against audio
- No post-hoc snap step is needed (lines are already canonical)
- Alignment quality improves because the text is clean and correctly line-broken

## Integration into the Pipeline

### Changes to `gen_lrc_qwen3_asr_mvsep_force_align_v2.py`

#### 1. New function: `reconstruct_lines_from_words` (new, ~80 lines)

As described in Step 2 above. This is the core algorithm.

#### 2. New function: `build_aligned_text` (new, ~20 lines)

As described in Step 4 above. Produces the line list for the forced aligner.

#### 3. New function: `_strip_cjk_spaces` (new, ~10 lines)

From the existing fix spec (Fix 2). Removes spaces between CJK characters.

```python
def _strip_cjk_spaces(text: str) -> str:
    return re.sub(r'([\u4e00-\u9fff\u3400-\u4dbf])\s+(?=[\u4e00-\u9fff\u3400-\u4dbf])', r'\1', text)
```

#### 4. New function: `_normalize_for_matching` (new, ~10 lines)

As described in Step 1 above.

#### 5. Modify single-chunk path (lines 1565-1611)

**Before:**
```python
if len(chunks) == 1:
    chunk_text = asr_text  # ASR sentence text
    chunk_lines = [line for line in chunk_text.split("\n") if line.strip()]
    ...
    aligned = align_lyrics(audio_path=audio_path, lyrics_lines=chunk_lines, ...)
```

**After:**
```python
if len(chunks) == 1:
    # Reconstruct line structure from words using canonical lyrics
    line_assignments = reconstruct_lines_from_words(asr_words, lyrics)
    chunk_lines = build_aligned_text(asr_words, line_assignments, lyrics)
    typer.echo(
        f"Reconstructed {len(chunk_lines)} lines from {len(asr_words)} words "
        f"using {len(lyrics)} canonical lines",
        err=True,
    )
    ...
    aligned = align_lyrics(audio_path=audio_path, lyrics_lines=chunk_lines, ...)
```

#### 6. Modify multi-chunk path (lines 1612-1677)

Replace `assign_text_to_chunks` with canonical-guided reconstruction per chunk.

**Before:**
```python
chunk_texts = assign_text_to_chunks(asr_words_fallback, chunks)
for i, (chunk_start, chunk_end) in enumerate(chunks):
    chunk_text = chunk_texts.get(i, "")
    result = align_chunk(audio_path, chunk_text=chunk_text, ...)
```

**After:**
```python
# Partition words into chunks first (using timestamps)
chunk_word_map = _partition_words_to_chunks(asr_words_fallback, chunks)

for i, (chunk_start, chunk_end) in enumerate(chunks):
    chunk_words = chunk_word_map.get(i, [])
    if chunk_words:
        # Reconstruct line structure within this chunk
        line_assignments = reconstruct_lines_from_words(chunk_words, lyrics)
        chunk_lines = build_aligned_text(chunk_words, line_assignments, lyrics)
        chunk_text = "\n".join(chunk_lines)
    else:
        chunk_text = ""

    typer.echo(
        f"Processing chunk {i}: {chunk_start:.1f}-{chunk_end:.1f}s "
        f"({len(chunk_words)} words → {len(chunk_lines)} lines)",
        err=True,
    )

    result = align_chunk(
        audio_path=audio_path,
        chunk_start=chunk_start,
        chunk_end=chunk_end,
        chunk_text=chunk_text,
        asr_words=chunk_words,  # Pass chunk-scoped words for fallback
        ...
    )
```

#### 7. New helper: `_partition_words_to_chunks` (new, ~20 lines)

Replaces `assign_text_to_chunks`. Returns word tuples (not space-joined strings), preserving all metadata.

```python
def _partition_words_to_chunks(
    asr_words: list[tuple[float, float, str]],
    chunks: list[tuple[float, float]],
) -> dict[int, list[tuple[float, float, str]]]:
    """Partition word-level ASR data into chunks by timestamp.

    Unlike assign_text_to_chunks (which space-joins words into strings),
    this returns the original word tuples, preserving timestamps and text.
    """
    chunk_words: dict[int, list[tuple[float, float, str]]] = {
        i: [] for i in range(len(chunks))
    }

    for word in asr_words:
        word_start = word[0]
        for i, (chunk_start, chunk_end) in enumerate(chunks):
            is_last = i == len(chunks) - 1
            in_chunk = (chunk_start <= word_start <= chunk_end) if is_last else (chunk_start <= word_start < chunk_end)
            if in_chunk:
                chunk_words[i].append(word)
                break

    return chunk_words
```

#### 8. Eliminate or simplify the snap step (lines 1695-1706)

Since canonical text is already used for alignment, the snap step becomes a no-op. Options:

**Option A (recommended): Remove the snap step entirely.** The `--snap` / `--no-snap` flag becomes meaningless since lines are already canonical. Emit a log message: `"Canonical text used for alignment; snap step skipped (already canonical)"`.

**Option B: Keep snap as a diagnostic comparison.** Run snap but don't use its output — just log how many lines the snap would have changed vs. the canonical-aligned output. Useful for debugging the reconstruction quality.

#### 9. Fix `_word_fallback_for_chunk` (lines 1098-1140)

Apply `_strip_cjk_spaces` to the fallback path:

```python
# Before:
seg_text = " ".join(w[2] for w in current_words)

# After:
seg_text = _strip_cjk_spaces(" ".join(w[2] for w in current_words))
```

#### 10. Fix diagnostic table zip mismatch (line 381)

As described in the original fix spec (Fix 3, Option A): iterate over `results` only, not `zip(segments, results)`.

## Tunable Constants

```python
MAX_LOOKAHEAD_WORDS = 30    # How far ahead to search for a canonical line match
MIN_LINE_SCORE = 0.40       # Minimum fuzzy score to assign words to a canonical line
GAP_THRESHOLD = 1.0         # Seconds; gap between words to infer a line break (fallback)
SHORT_FRAG_CHARS = 3        # CJK char count below which partial_ratio is used
```

These should be defined as module-level constants near the top of the file, not inlined.

## Why This Is Better Than Sentence-Based Assignment

| Aspect | Sentence-based (Fix 1 from original spec) | Canonical-guided (this spec) |
|--------|------------------------------------------|------------------------------|
| Line structure source | ASR sentence boundaries | Canonical lyrics template |
| Text passed to aligner | ASR text (may have errors) | Canonical text (correct) |
| Post-alignment snap | Required (ASR text → canonical) | Not needed (already canonical) |
| Handles ASR splitting mid-line | No (ASR sentences may not match lyric lines) | Yes (canonical lines define boundaries) |
| Handles ASR merging lines | Partially (merged sentence = one LRC line) | Yes (canonical line boundaries split merged text) |
| CJK spacing | Separate fix needed | Handled by reconstruction |
| Chorus/verse repeats | Handled by snap wrap-around | Handled by wrap-around in reconstruction |
| Alignment quality | Good (correct structure) | Better (correct structure + correct text) |

## Edge Cases and Risks

### 1. ASR words that don't match any canonical line

Instrumental sections, ad-libs, or spoken interludes may produce ASR words with no canonical counterpart. These are emitted as "unmatched" lines with raw ASR text (CJK-stripped). The forced aligner still aligns them, and they appear in the LRC as non-canonical lines.

### 2. Canonical lines that don't appear in the audio

Some canonical lyrics may include lines not sung in a particular performance (e.g., a bridge that's skipped). The reconstruction will simply not assign any words to these lines, and they won't appear in the LRC output. This is correct behavior.

### 3. Heavily garbled ASR

If ASR quality is very low (e.g., `verify_asr_quality` score < 0.30), the word-to-canonical matching may produce poor assignments. Mitigation: if the overall ASR quality score is below a threshold (0.30), fall back to the sentence-based approach (original Fix 1) and log a warning.

### 4. Songs with very short lines

Lines like "啊" or "Amen" (1-2 chars) may match many positions in the ASR stream. The sequential cursor constraint prevents false matches: short lines are matched only at the current cursor position, not anywhere in the stream.

### 5. Overlap region in multi-chunk

Words in the chunk overlap region (e.g., 240-300s for chunks [0-300s] and [240-323.5s]) are assigned to only one chunk by `_partition_words_to_chunks`. The canonical reconstruction within each chunk is independent. The existing `merge_chunks` deduplication handles any overlap artifacts.

### 6. Canonical lyrics with English + Chinese mixed lines

Lines like "你與我同在，I will sing Hallelujah" contain both CJK and ASCII. The normalization preserves both, and `token_set_ratio` handles mixed-script matching well. No special handling needed.

## Verification

### Test 1: Multi-chunk song (the original bug)

```bash
uv run --extra poc_qwen3_asr poc/gen_lrc_qwen3_asr_mvsep_force_align_v2.py \
  iwillsinghallelujah_wo_yao_chang_ha_li_lu_ya__f5b0bc26 \
  --model qwen3-asr-flash-filetrans \
  --lyrics-context --save-raw ~/tmp --output ~/tmp
```

**Expected:** ~17 LRC lines (one per sung canonical line occurrence), with proper Chinese text (no inter-character spaces). Each line uses canonical text. Timestamps are accurate from forced alignment.

### Test 2: Single-chunk song

Run any song ≤ 5 minutes. Verify that:
- The reconstruction produces the same number of lines as the number of canonical line occurrences in the performance
- Timestamps are at least as accurate as the previous ASR-text-based alignment
- No CJK spacing artifacts in output

### Test 3: Song with garbled ASR

Run a song with known low ASR quality. Verify that:
- The reconstruction degrades gracefully (unmatched words appear as raw lines)
- No crash or empty output
- A warning is logged about low ASR quality

### Test 4: Diagnostic output

Check `diagnostic.md` for:
- "Reconstructed N lines from M words using K canonical lines" log
- Per-line mapping showing which canonical line each output line corresponds to
- Any "unmatched" or "skipped canonical line" warnings

## Files to Modify

| File | Change |
|------|--------|
| `poc/gen_lrc_qwen3_asr_mvsep_force_align_v2.py` | All changes listed above. No other files. |

New functions: `reconstruct_lines_from_words`, `build_aligned_text`, `_strip_cjk_spaces`, `_normalize_for_matching`, `_partition_words_to_chunks`.

Modified functions: single-chunk path (lines 1565-1611), multi-chunk path (lines 1612-1677), `_word_fallback_for_chunk` (lines 1098-1140), snap step (lines 1695-1706), diagnostic table (line 381).

Removed/replaced: `assign_text_to_chunks` (replaced by `_partition_words_to_chunks` + `reconstruct_lines_from_words`).

## Relationship to Existing Specs

- **`specs/fix_multi_chunk_lrc_line_collapse.md`**: This spec supersedes Fixes 1 and 2 from that spec. Fix 3 (diagnostic zip) and Fix 4 (overlap handling) are still valid and should be implemented alongside this spec.
- **`specs/improve_canonical_lyrics_matching_algo.md`**: The `_score`, `_combined_score`, and `_is_filler` functions from that spec are relevant but operate at the segment level (post-alignment). This spec operates at the word level (pre-alignment). The scoring logic (`partial_ratio` for short fragments, `token_set_ratio` otherwise) should be reused.
- **`specs/snap_bridge_via_dynamic_programming.md`**: The DP-based snap is a post-alignment technique. With canonical-guided reconstruction, the snap step is eliminated. The DP approach could be adapted for word-level reconstruction if the greedy sequential approach proves insufficient, but this is not needed initially.
