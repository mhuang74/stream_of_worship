# Canonical-Guided Lyrics Reconstruction from Word-Level ASR (v2)

## Problem

The multi-chunk LRC pipeline (`gen_lrc_qwen3_asr_mvsep_force_align_v2.py`) collapses word-level ASR output into a single space-joined string per chunk, destroying line structure. This produces 2 LRC lines from 17 ASR segments (see `specs/fix_multi_chunk_lrc_line_collapse.md`).

The prior fix spec proposes using **ASR sentence boundaries** to preserve line structure. But ASR sentence boundaries are unreliable — they may split mid-lyric-line, merge lines, or create boundaries at different points than the songwriter intended. Since canonical lyrics are always available, we can do better: **use canonical lyrics as a template to reconstruct line structure from the word stream**.

This approach also applies to the **single-chunk path**: instead of passing ASR sentence text to the forced aligner (which may have transcription errors), we pass canonical text with correct line structure, yielding better alignment accuracy.

## Core Idea

Given:
- `asr_words`: ordered list of `(start, end, text)` tuples from ASR (word-level, each CJK char is a separate "word")
- `canonical_lines`: ordered list of canonical lyric lines (e.g., 11 lines for "I Will Sing Hallelujah")

Produce:
- `reconstructed_lines`: list of `(canonical_line_idx, word_indices)` tuples, where each tuple maps a canonical line to a contiguous range of ASR word indices that correspond to it
- `canonical_line_idx` of `-1` indicates an unmatched segment (words with no canonical counterpart)
- The concatenation of `asr_words[i][2]` for each group, after CJK space stripping, should closely match the canonical line text

This reconstruction happens **before** forced alignment. The canonical text (not ASR text) is then passed to the forced aligner, which produces per-line timestamps.

## Algorithm: Canonical-Guided Word Grouping

### Step 1: Normalize both sides to a common representation

```python
def _normalize_for_matching(text: str) -> str:
    """Strip all punctuation, whitespace, and normalize CJK variants for matching."""
    import re
    from zhconv import convert

    text = convert(text, "zh-hans")
    text = re.sub(r'[^\u4e00-\u9fff\u3400-\u4dbf a-zA-Z]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text
```

- Normalize each canonical line → `canonical_norm[i]`
- Normalize the concatenation of all ASR word texts (after CJK space stripping) → `asr_norm` (a single string)
- Also build `asr_word_norms`: list of normalized individual word texts, preserving the same index mapping

### Step 2: Sequential alignment with limited backtracking

Walk through `asr_word_norms` sequentially, consuming words that match each canonical line in order. When canonical lines repeat (chorus/verse), use smart wrap-around (Step 2c).

```python
def reconstruct_lines_from_words(
    asr_words: list[tuple[float, float, str]],
    canonical_lines: list[str],
    start_canonical_idx: int = 0,
) -> tuple[list[tuple[int, list[int]]], int]:
    """Group ASR words into canonical line assignments.

    Returns:
        Tuple of:
        - List of (canonical_line_idx, word_indices) tuples.
          canonical_line_idx may repeat when the song repeats verses/choruses.
          canonical_line_idx of -1 indicates unmatched words.
          word_indices are always contiguous ranges into asr_words.
        - The canonical line index to resume from (for chaining across chunks).
    """
```

#### Step 2a: Build the ASR norm stream

Build `asr_norm_stream`: concatenate all normalized word texts into one string, tracking the character offset range `[char_start, char_end)` that each word occupies in this stream.

Build `canonical_norm_lines`: normalize each canonical line.

#### Step 2b: Greedy match with limited backtracking

Maintain a `word_cursor` (index into `asr_words`) and a `canonical_cursor` (starting at `start_canonical_idx`).

For each canonical line `i` (starting from `canonical_cursor`), find the next contiguous span of words that best matches it:

1. For each candidate end position `j` (from `word_cursor` to `word_cursor + MAX_LOOKAHEAD_WORDS`):
   - Concatenate `asr_word_norms[word_cursor:j+1]` → `candidate_text`
   - Compute `fuzz.token_set_ratio(candidate_text, canonical_norm_lines[i]) / 100.0` → `score`
   - Also compute `fuzz.partial_ratio(candidate_text, canonical_norm_lines[i]) / 100.0` → `partial_score`
   - Use `partial_score` if the canonical line has ≤ `SHORT_FRAG_CHARS` CJK chars (short-fragment rule)
2. Pick the `j` that maximizes the score, subject to `score >= MIN_LINE_SCORE`.
3. If score < `MIN_LINE_SCORE`, this canonical line has no good match. Skip it (emit no words) and advance to the next canonical line.
4. Otherwise, emit `(i, list(range(word_cursor, j+1)))` and advance `word_cursor` to `j+1`.

**Limited backtracking:** After successfully matching canonical line `i`, check the next `BACKTRACK_WINDOW` canonical lines (i+1, i+2, ..., i+BACKTRACK_WINDOW). If all of them score below `MIN_LINE_SCORE` against the remaining ASR words starting at the current `word_cursor`:

1. For each alternative end position `j_alt` that scored ≥ `MIN_LINE_SCORE` for line `i` (sorted by score descending, up to `MAX_BACKTRACK_ALT` alternatives):
   - Temporarily set `word_cursor = j_alt + 1`
   - Score canonical line `i+1` against words starting at this new cursor
   - If the score for line `i+1` improves by ≥ `BACKTRACK_GAIN_THRESHOLD` over the original, accept this alternative end position
2. If no alternative improves the next line's score, keep the original match.

This prevents cascading drift from a single bad boundary without the full complexity of dynamic programming.

#### Step 2c: Smart wrap-around for repeats

When `canonical_cursor` reaches the end of `canonical_lines` and `word_cursor < len(asr_words)`:

1. Compute the normalized text of the remaining unmatched words (from `word_cursor` onward).
2. Score this remaining text against each canonical line using `fuzz.partial_ratio`.
3. Pick the canonical line with the highest score as the restart point, provided the score ≥ `WRAP_MIN_SCORE` (default: 0.50).
4. If no canonical line scores ≥ `WRAP_MIN_SCORE`, group remaining words as unmatched segments using the time-gap fallback heuristic.
5. Set `canonical_cursor` to the restart point and continue matching.

This handles song structures like V1-C-V2-C-B-C where the second verse starts at a different canonical offset than line 0.

#### Step 2d: Unmatched trailing words

If words remain after all canonical lines are exhausted (even after wrap-around), group them into "unmatched" segments using the word-fallback time-gap heuristic (existing `_word_fallback_for_chunk` logic with `GAP_THRESHOLD` seconds). Emit each as `(-1, word_indices)`.

### Step 3: Handle edge cases

**Short canonical lines (≤ SHORT_FRAG_CHARS CJK chars):** Use `partial_ratio` instead of `token_set_ratio` to avoid score collapse (same rule as existing `_score` in `gen_lrc_qwen3_asr_local.py`).

**ASR words with no canonical match:** If a span of words at the beginning or between canonical lines doesn't match any canonical line well, these are likely instrumental/filler/ad-lib segments. Emit them as separate "unmatched" lines `(-1, word_indices)` with their raw ASR text (after CJK space stripping).

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
            raw = " ".join(asr_words[i][2] for i in word_indices)
            lines.append(_strip_cjk_spaces(raw))
    return lines
```

**Key insight:** We pass **canonical text** to the forced aligner, not ASR text. This means:
- The forced aligner aligns correct, well-structured text against audio
- No post-hoc snap step is needed (lines are already canonical)
- Alignment quality improves because the text is clean and correctly line-broken

### Step 5: Post-hoc quality check and fallback

After `reconstruct_lines_from_words` completes, measure reconstruction quality:

```python
def _reconstruction_quality(
    line_assignments: list[tuple[int, list[int]]],
    total_words: int,
    total_canonical_lines: int,
) -> float:
    """Compute a 0-1 quality score for the reconstruction.

    Factors:
    - Fraction of words assigned to canonical lines (vs unmatched)
    - Fraction of canonical lines that were matched (vs skipped)
    """
    matched_words = sum(
        len(indices) for idx, indices in line_assignments if idx >= 0
    )
    matched_canonical = len({idx for idx, _ in line_assignments if idx >= 0})
    word_fraction = matched_words / total_words if total_words > 0 else 0
    canonical_fraction = matched_canonical / total_canonical_lines if total_canonical_lines > 0 else 0
    return (word_fraction + canonical_fraction) / 2
```

If the quality score is below `RECONSTRUCTION_FALLBACK_THRESHOLD` (default: 0.40):

1. Log a warning: `"Reconstruction quality {score:.2f} below threshold; falling back to sentence-based assignment"`
2. Fall back to the sentence-based approach (Fix 1 from `specs/fix_multi_chunk_lrc_line_collapse.md`): use `assign_sentences_to_chunks` with ASR sentence boundaries
3. The snap step is re-enabled for the fallback path

This handles cases where ASR quality is very low and the canonical-guided matching produces poor assignments.

## Integration into the Pipeline

### Changes to `gen_lrc_qwen3_asr_mvsep_force_align_v2.py`

#### 1. New function: `reconstruct_lines_from_words` (new, ~120 lines)

As described in Step 2 above. This is the core algorithm. Returns `(line_assignments, next_canonical_idx)` for chunk chaining.

#### 2. New function: `build_aligned_text` (new, ~20 lines)

As described in Step 4 above. Produces the line list for the forced aligner.

#### 3. New function: `_strip_cjk_spaces` (new, ~10 lines)

Removes spaces between CJK characters:

```python
def _strip_cjk_spaces(text: str) -> str:
    return re.sub(r'([\u4e00-\u9fff\u3400-\u4dbf])\s+(?=[\u4e00-\u9fff\u3400-\u4dbf])', r'\1', text)
```

#### 4. New function: `_normalize_for_matching` (new, ~10 lines)

As described in Step 1 above.

#### 5. New function: `_reconstruction_quality` (new, ~20 lines)

As described in Step 5 above.

#### 6. New function: `_partition_words_to_chunks` (new, ~20 lines)

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

#### 7. Modify single-chunk path (lines 1565-1611)

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
    line_assignments, _ = reconstruct_lines_from_words(asr_words, lyrics)
    quality = _reconstruction_quality(line_assignments, len(asr_words), len(lyrics))

    if quality >= RECONSTRUCTION_FALLBACK_THRESHOLD:
        chunk_lines = build_aligned_text(asr_words, line_assignments, lyrics)
        typer.echo(
            f"Reconstructed {len(chunk_lines)} lines from {len(asr_words)} words "
            f"using {len(lyrics)} canonical lines (quality={quality:.2f})",
            err=True,
        )
    else:
        typer.echo(
            f"Reconstruction quality {quality:.2f} below threshold; "
            f"falling back to ASR sentence text",
            err=True,
        )
        chunk_lines = [line for line in asr_text.split("\n") if line.strip()]
        if not chunk_lines:
            chunk_lines = [asr_text] if asr_text.strip() else []

    ...
    aligned = align_lyrics(audio_path=audio_path, lyrics_lines=chunk_lines, ...)
```

#### 8. Modify multi-chunk path (lines 1612-1677)

Replace `assign_text_to_chunks` with canonical-guided reconstruction per chunk, chaining canonical line progress across chunks.

**Before:**
```python
chunk_texts = assign_text_to_chunks(asr_words_fallback, chunks)
for i, (chunk_start, chunk_end) in enumerate(chunks):
    chunk_text = chunk_texts.get(i, "")
    result = align_chunk(audio_path, chunk_text=chunk_text, ...)
```

**After:**
```python
chunk_word_map = _partition_words_to_chunks(asr_words_fallback, chunks)
next_canonical_idx = 0

for i, (chunk_start, chunk_end) in enumerate(chunks):
    chunk_words = chunk_word_map.get(i, [])

    if chunk_words:
        line_assignments, next_canonical_idx = reconstruct_lines_from_words(
            chunk_words, lyrics, start_canonical_idx=next_canonical_idx,
        )
        quality = _reconstruction_quality(
            line_assignments, len(chunk_words), len(lyrics),
        )

        if quality >= RECONSTRUCTION_FALLBACK_THRESHOLD:
            chunk_lines = build_aligned_text(chunk_words, line_assignments, lyrics)
            chunk_text = "\n".join(chunk_lines)
        else:
            typer.echo(
                f"Chunk {i}: reconstruction quality {quality:.2f} below threshold; "
                f"falling back to sentence-based assignment",
                err=True,
            )
            chunk_text = _sentence_fallback_for_chunk(segments, chunk_start, chunk_end)
    else:
        chunk_text = ""

    typer.echo(
        f"Processing chunk {i}: {chunk_start:.1f}-{chunk_end:.1f}s "
        f"({len(chunk_words)} words -> {len(chunk_lines) if chunk_words else 0} lines, "
        f"canonical_start={next_canonical_idx})",
        err=True,
    )

    result = align_chunk(
        audio_path=audio_path,
        chunk_start=chunk_start,
        chunk_end=chunk_end,
        chunk_text=chunk_text,
        asr_words=chunk_words if chunk_words else asr_words_fallback,
        language=language,
        device=device,
        dtype=dtype,
        model_cache_dir=model_cache_dir,
    )
```

#### 9. New helper: `_sentence_fallback_for_chunk` (new, ~15 lines)

For the post-hoc fallback path in multi-chunk: extract sentence-level segments belonging to a chunk and join with newlines.

```python
def _sentence_fallback_for_chunk(
    segments: list[tuple[float, float, str]],
    chunk_start: float,
    chunk_end: float,
) -> str:
    """Fall back to sentence-level ASR text for a chunk (when reconstruction fails)."""
    chunk_sents = [
        text for start, end, text in segments
        if chunk_start <= start <= chunk_end
    ]
    return "\n".join(chunk_sents)
```

#### 10. Eliminate or simplify the snap step (lines 1695-1706)

Since canonical text is already used for alignment, the snap step becomes a no-op when reconstruction succeeded. When the fallback path was used, the snap step should still run.

**Recommended approach:** Track whether each result line came from canonical text or ASR text. If all lines are canonical, skip snap. If any lines are from the fallback path, run snap on those lines only.

In practice, the simplest implementation:

```python
if snap and lyrics:
    if used_canonical_reconstruction:
        typer.echo(
            "Canonical text used for alignment; snap step skipped (already canonical)",
            err=True,
        )
        results = [(start, text, True) for start, _end, text in merged]
    else:
        results = sequential_canonical_snap(merged, lyrics, threshold=snap_threshold)
        replaced_count = sum(1 for _, _, replaced in results if replaced)
        typer.echo(
            f"Sequential canonical snap: {replaced_count}/{len(results)} segments replaced",
            err=True,
        )
else:
    results = [(start, text, False) for start, _end, text in merged]
```

#### 11. Fix `_word_fallback_for_chunk` (lines 1098-1140)

Apply `_strip_cjk_spaces` to the fallback path:

```python
# Before:
seg_text = " ".join(w[2] for w in current_words)

# After:
seg_text = _strip_cjk_spaces(" ".join(w[2] for w in current_words))
```

#### 12. Fix diagnostic table zip mismatch (line 381)

As described in the original fix spec (Fix 3, Option A): iterate over `results` only, not `zip(segments, results)`.

```python
# Before:
for (_, end, asr_text), (start, final_text, replaced) in zip(segments, results):

# After:
for start, final_text, replaced in results:
    end = None  # Not available from results tuple; omit from table or compute from next entry
```

Or use Option B (timestamp proximity matching) if the ASR text column is important for diagnostics.

## Tunable Constants

```python
MAX_LOOKAHEAD_WORDS = 30       # How far ahead to search for a canonical line match
MIN_LINE_SCORE = 0.40          # Minimum fuzzy score to assign words to a canonical line
GAP_THRESHOLD = 1.0            # Seconds; gap between words to infer a line break (fallback)
SHORT_FRAG_CHARS = 3           # CJK char count below which partial_ratio is used
BACKTRACK_WINDOW = 3           # How many next canonical lines to check before backtracking
MAX_BACKTRACK_ALT = 3          # Max alternative end positions to try when backtracking
BACKTRACK_GAIN_THRESHOLD = 0.10  # Min score improvement to accept a backtrack alternative
WRAP_MIN_SCORE = 0.50          # Min score to accept a smart wrap-around restart point
RECONSTRUCTION_FALLBACK_THRESHOLD = 0.40  # Quality below which we fall back to sentence-based
```

These should be defined as module-level constants near the top of the file, not inlined.

## Dependencies

The following dependencies are already used in the file (imported lazily inside function bodies) and do not need to be added to `pyproject.toml`:

- `rapidfuzz` (used for `fuzz.token_set_ratio` and `fuzz.partial_ratio`) — already in `gen_lrc_qwen3_asr_mvsep_force_align_v2.py` via lazy import at line 1253
- `zhconv` (used for `convert(text, "zh-hans")`) — already in the file via lazy import at line 1254

No new dependencies are required.

## Why This Is Better Than Sentence-Based Assignment

| Aspect | Sentence-based (Fix 1 from original spec) | Canonical-guided (this spec) |
|--------|------------------------------------------|------------------------------|
| Line structure source | ASR sentence boundaries | Canonical lyrics template |
| Text passed to aligner | ASR text (may have errors) | Canonical text (correct) |
| Post-alignment snap | Required (ASR text → canonical) | Not needed (already canonical) |
| Handles ASR splitting mid-line | No (ASR sentences may not match lyric lines) | Yes (canonical lines define boundaries) |
| Handles ASR merging lines | Partially (merged sentence = one LRC line) | Yes (canonical line boundaries split merged text) |
| CJK spacing | Separate fix needed | Handled by reconstruction |
| Chorus/verse repeats | Handled by snap wrap-around | Handled by smart wrap-around in reconstruction |
| Alignment quality | Good (correct structure) | Better (correct structure + correct text) |
| Multi-chunk canonical scoping | N/A (sentence-based per chunk) | Chained progress across chunks |
| Drift recovery | None (greedy snap) | Limited backtracking |
| Low-ASR robustness | No fallback | Post-hoc quality check with sentence-based fallback |

## Edge Cases and Risks

### 1. ASR words that don't match any canonical line

Instrumental sections, ad-libs, or spoken interludes may produce ASR words with no canonical counterpart. These are emitted as "unmatched" lines `(-1, word_indices)` with raw ASR text (CJK-stripped). The forced aligner still aligns them, and they appear in the LRC as non-canonical lines.

### 2. Canonical lines that don't appear in the audio

Some canonical lyrics may include lines not sung in a particular performance (e.g., a bridge that's skipped). The reconstruction will simply not assign any words to these lines, and they won't appear in the LRC output. This is correct behavior.

### 3. Heavily garbled ASR

If ASR quality is very low, the word-to-canonical matching may produce poor assignments. The post-hoc quality check (Step 5) detects this: if the reconstruction quality score is below `RECONSTRUCTION_FALLBACK_THRESHOLD`, the pipeline falls back to the sentence-based approach and re-enables the snap step. A warning is logged.

### 4. Songs with very short lines

Lines like "啊" or "Amen" (1-2 chars) may match many positions in the ASR stream. The sequential cursor constraint prevents false matches: short lines are matched only at the current cursor position, not anywhere in the stream. The `partial_ratio` scoring for short fragments further reduces false positives.

### 5. Overlap region in multi-chunk

Words in the chunk overlap region (e.g., 240-300s for chunks [0-300s] and [240-323.5s]) are assigned to only one chunk by `_partition_words_to_chunks`. The canonical reconstruction within each chunk is independent. The existing `merge_chunks` deduplication handles any overlap artifacts.

### 6. Canonical lyrics with English + Chinese mixed lines

Lines like "你與我同在，I will sing Hallelujah" contain both CJK and ASCII. The normalization preserves both, and `token_set_ratio` handles mixed-script matching well. No special handling needed.

### 7. Backtracking interaction with smart wrap-around

If backtracking adjusts a boundary near the end of the canonical line list, the smart wrap-around may choose a different restart point than it would have without backtracking. This is desirable — the backtracking correction should propagate into the wrap-around decision.

### 8. Chunk chaining with backtracking

When a chunk's reconstruction uses backtracking, the returned `next_canonical_idx` reflects the post-backtracking state. The next chunk starts from this corrected position. This is correct — the backtracking may shift the canonical cursor forward or keep it the same, but never backward (since we only try alternative end positions that consume a different number of words for the same canonical line).

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
- The reconstruction quality check triggers the fallback
- The fallback produces reasonable output (sentence-based + snap)
- A warning is logged about low reconstruction quality

### Test 4: Song with verse/chorus repeats

Run a song with structure V1-C-V2-C-B-C. Verify that:
- The smart wrap-around correctly identifies the restart point for each repeat
- The chained chunk progress correctly carries the canonical line index across chunks
- No duplicate lines from incorrect wrap-around

### Test 5: Diagnostic output

Check `diagnostic.md` for:
- "Reconstructed N lines from M words using K canonical lines (quality=X.XX)" log
- Per-line mapping showing which canonical line each output line corresponds to
- Any "unmatched" or "skipped canonical line" warnings
- Any "backtracking" or "smart wrap-around" log entries

## Files to Modify

| File | Change |
|------|--------|
| `poc/gen_lrc_qwen3_asr_mvsep_force_align_v2.py` | All changes listed above. No other files. |

New functions: `reconstruct_lines_from_words`, `build_aligned_text`, `_strip_cjk_spaces`, `_normalize_for_matching`, `_partition_words_to_chunks`, `_reconstruction_quality`, `_sentence_fallback_for_chunk`.

Modified functions: single-chunk path (lines 1565-1611), multi-chunk path (lines 1612-1677), `_word_fallback_for_chunk` (lines 1098-1140), snap step (lines 1695-1706), diagnostic table (line 381).

Removed/replaced: `assign_text_to_chunks` (replaced by `_partition_words_to_chunks` + `reconstruct_lines_from_words`).

## Relationship to Existing Specs

- **`specs/fix_multi_chunk_lrc_line_collapse.md`**: This spec supersedes Fixes 1 and 2 from that spec. Fix 3 (diagnostic zip) and Fix 4 (overlap handling) are still valid and should be implemented alongside this spec.
- **`specs/improve_canonical_lyrics_matching_algo.md`**: The `_score`, `_combined_score`, and `_is_filler` functions from that spec are relevant but operate at the segment level (post-alignment). This spec operates at the word level (pre-alignment). The scoring logic (`partial_ratio` for short fragments, `token_set_ratio` otherwise) should be reused. Note: those functions exist in `gen_lrc_qwen3_asr_local.py`, not in the target file for this spec.
- **`specs/snap_bridge_via_dynamic_programming.md`**: The DP-based snap is a post-alignment technique. With canonical-guided reconstruction, the snap step is eliminated (when reconstruction succeeds). The DP approach could be adapted for word-level reconstruction if the greedy+backtracking approach proves insufficient, but this is not needed initially.
- **`specs/canonical_guided_lyrics_reconstruction.md` (v1)**: This v2 spec supersedes v1. Key differences: chained chunk progress (v1 started each chunk from canonical line 0), limited backtracking (v1 was purely greedy), smart wrap-around (v1 used blind reset to 0), post-hoc fallback (v1 had a pre-check only), and dependency clarification (v1 referenced `fuzzywuzzy` but the codebase uses `rapidfuzz`).
