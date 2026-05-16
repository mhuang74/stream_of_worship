# Fix: Multi-Chunk LRC Line Collapse (2-line output from 17 ASR segments)

## Problem

When running `gen_lrc_qwen3_asr_mvsep_force_align_v2.py` on a song longer than 5 minutes (requiring multi-chunk alignment), the output LRC collapses to only 2 lines with timecodes — one per chunk — instead of one line per ASR sentence. The canonical lyrics for the test song has 11 lines, and the ASR produced 17 sentence segments, yet the final LRC has only 2.

### Observed Output

```
[00:21.92] 我 敬 拜 你， 荣 耀 的 天 父， 无 人 能 与 你 相 比。 我 敬 拜 你， 圣 洁 的 天 父， 你 是 我 的 一 切。 啊。 ...
[04:00.00] 全 心 来 赞 美 你， I will sing Hallelujah。 你 与 我 同 在， I will sing hallelujah。 爱 你， I will sing. 同 在， 你 与 我 同 在， 你 与 我 同 在。
```

### Expected Output

17 LRC lines (one per ASR sentence) with `--no-snap`, or 11 canonical lines with `--snap`. Each line should have properly formatted Chinese text without spaces between characters.

### Reproduction

```bash
uv run --extra poc_qwen3_asr poc/gen_lrc_qwen3_asr_mvsep_force_align_v2.py \
  iwillsinghallelujah_wo_yao_chang_ha_li_lu_ya__f5b0bc26 \
  --model qwen3-asr-flash-filetrans \
  --lyrics-context --save-raw ~/tmp --output ~/tmp --no-snap
```

Key log lines confirming the bug:

```
Processing chunk 0: 0.0-300.0s (643 chars)
Mapping 242 segments to 1 lines        ← should be ~13 lines
Processing chunk 1: 240.0-323.5s (116 chars)
Mapping 36 segments to 1 lines         ← should be ~4 lines
Merged into 2 segments                 ← should be ~17 segments
```

---

## Root Cause Analysis

### Bug 1: `assign_text_to_chunks` destroys sentence structure

**Location**: `poc/gen_lrc_qwen3_asr_mvsep_force_align_v2.py:986-1015`

The multi-chunk pipeline uses `assign_text_to_chunks()` to determine which ASR text belongs to each chunk. This function takes word-level timestamps and joins all words with **spaces** into a single string per chunk:

```python
def assign_text_to_chunks(
    asr_words: list[tuple[float, float, str]],
    chunks: list[tuple[float, float]],
) -> dict[int, str]:
    chunk_texts: dict[int, list[str]] = {i: [] for i in range(len(chunks))}

    for word_start, word_end, word_text in asr_words:
        for i, (chunk_start, chunk_end) in enumerate(chunks):
            # ... check if word falls in chunk ...
            if in_chunk:
                chunk_texts[i].append(word_text)

    return {i: " ".join(texts) for i, texts in chunk_texts.items()}
    #              ^^^^^^^^^^^^
    #  All words space-joined into ONE string per chunk.
    #  No sentence boundaries preserved.
```

For the test song (323.5s → 2 chunks), this produces:

| Chunk | Time Range | Output |
|-------|-----------|--------|
| 0 | 0.0–300.0s | `"我 敬 拜 你 ， 荣 耀 的 天 父 ， 无 人 能 与 你 相 比 。 我 敬 拜 你 ， ..."` (643 chars, single string) |
| 1 | 240.0–323.5s | `"全 心 来 赞 美 你 ， I will sing Hallelujah 。 ..."` (116 chars, single string) |

Then in `align_chunk()` (line 1049):

```python
chunk_lines = [line for line in chunk_text.split("\n") if line.strip()]
if not chunk_lines:
    chunk_lines = [chunk_text] if chunk_text.strip() else []
```

Since `chunk_text` has no newlines (words were space-joined), `split("\n")` produces one element. After filtering, `chunk_lines` is a **list with 1 element** — the entire chunk text as a single line.

This single-element list is passed to `align_lyrics()`:

```python
aligned = align_lyrics(
    audio_path=segment_path,
    lyrics_lines=chunk_lines,   # ← 1 line!
    ...
)
```

Inside `align_lyrics()` (line 735):

```python
lyrics_text = "\n".join(lyrics_lines)  # Just the single line
results = model.align(audio=str(audio_path), text=lyrics_text, language=language)
```

The forced aligner aligns the entire chunk text as a single unit. Then `map_segments_to_lines()` maps character-level alignment back to the original lines — which is just **1 line**. So each chunk produces exactly 1 output segment.

**2 chunks × 1 segment = 2 LRC lines.**

### Contrast with the single-chunk path (which works correctly)

The single-chunk path (audio ≤ 300s) uses `asr_text` from `extract_asr_text()`, which properly joins sentences with newlines:

```python
# Single-chunk path (line 1565-1611)
if len(chunks) == 1:
    chunk_text = asr_text  # ← from extract_asr_text(), sentences joined by \n
    chunk_lines = [line for line in chunk_text.split("\n") if line.strip()]
    # chunk_lines has N lines (one per ASR sentence) ✓
```

The bug only manifests in the multi-chunk path because `assign_text_to_chunks` was designed to use word-level data (for precise time-range assignment) but discards sentence structure in the process.

### Bug 2: Chinese characters spaced out in output

**Location**: `poc/gen_lrc_qwen3_asr_mvsep_force_align_v2.py:1015` and `1130-1138`

The Qwen3-ASR word-level output treats each Chinese character as a separate "word":

```json
{"begin_time": 21900, "end_time": 22460, "text": "我", "punctuation": ""},
{"begin_time": 22460, "end_time": 22700, "text": "敬", "punctuation": ""},
{"begin_time": 24460, "end_time": 24780, "text": "拜", "punctuation": ""},
```

When `assign_text_to_chunks` joins these with spaces:

```
"我 敬 拜 你 ， 荣 耀 的 天 父 ， 无 人 能 与 你 相 比 。"
```

Instead of the correct:

```
"我敬拜你，荣耀的天父，无人能与你相比。"
```

This affects:
1. **LRC output quality** — the spaced-out text is ugly and hard to read
2. **Forced alignment quality** — the aligner sees spaced text which may affect character-level alignment accuracy
3. **Canonical snap quality** — fuzzy matching against canonical lyrics is degraded because the token structure differs

The same spacing issue exists in `_word_fallback_for_chunk()` (line 1130-1138), which also space-joins words.

### Bug 3: Diagnostic table zips mismatched lists

**Location**: `poc/gen_lrc_qwen3_asr_mvsep_force_align_v2.py:381`

```python
for (_, end, asr_text), (start, _, replaced) in zip(segments, results):
```

`segments` has 17 entries (ASR sentences), `results` has 2 entries (one per chunk). `zip()` truncates to the shorter list, so the diagnostic only shows 2 rows. The end times from `segments` are paired with start times from `results` that correspond to different segments, producing misleading data like `Start=240.00, End=56.81`.

---

## Detailed Pipeline Trace (Test Song)

### Input Data

- **Song**: I Will Sing Hallelujah [我要唱哈利路亞]
- **Audio duration**: 323.5s
- **ASR model**: qwen3-asr-flash-filetrans
- **ASR output**: 17 sentence segments, 242 word-level timestamps
- **Canonical lyrics**: 11 lines (Traditional Chinese)

### Step-by-step trace

| Step | What happens | Result |
|------|-------------|--------|
| ASR | 17 sentences extracted from filetrans response | ✓ Correct |
| Word timestamps | 242 words extracted | ✓ Correct |
| Chunk planning | 323.5s > 300s → 2 chunks: [0-300s], [240-323.5s] | ✓ Correct |
| `assign_text_to_chunks` | Words mapped to chunks, space-joined | ✗ 1 string per chunk, no sentence boundaries |
| `align_chunk` (chunk 0) | `chunk_text.split("\n")` → 1 line | ✗ Forced aligner gets 1 line |
| `align_lyrics` (chunk 0) | Aligns 1 line → `map_segments_to_lines` maps to 1 line | ✗ 1 output segment |
| `align_chunk` (chunk 1) | Same as above | ✗ 1 output segment |
| `merge_chunks` | 2 segments total | ✗ Should be ~17 |
| `results_to_lrc` | 2 LRC lines with spaced-out Chinese text | ✗ Both bugs manifest |

### What the correct flow should produce

| Step | What should happen | Result |
|------|-------------------|--------|
| Chunk text assignment | Sentences mapped to chunks, newline-joined | 2 strings with ~13 and ~4 sentences respectively |
| `align_chunk` (chunk 0) | `chunk_text.split("\n")` → ~13 lines | ✓ Forced aligner gets multiple lines |
| `align_lyrics` (chunk 0) | Aligns ~13 lines → ~13 output segments | ✓ |
| `align_lyrics` (chunk 1) | Aligns ~4 lines → ~4 output segments | ✓ |
| `merge_chunks` | ~17 segments after dedup | ✓ |
| `results_to_lrc` | ~17 LRC lines with proper Chinese text | ✓ |

---

## Recommended Fixes

### Fix 1: Replace `assign_text_to_chunks` with sentence-based assignment

Create a new function `assign_sentences_to_chunks` that uses **sentence-level segments** (not word-level) to assign text to chunks, preserving sentence boundaries with newlines:

```python
def assign_sentences_to_chunks(
    segments: list[tuple[float, float, str]],
    chunks: list[tuple[float, float]],
) -> dict[int, str]:
    """Assign sentence-level ASR segments to chunks, preserving sentence structure.

    Unlike assign_text_to_chunks (which uses word-level data and joins with spaces),
    this function uses sentence-level segments and joins with newlines, preserving
    the line structure that the forced aligner needs to produce per-line timestamps.

    A sentence is assigned to a chunk if its start time falls within the chunk's
    time range (inclusive of start, exclusive of end, except the last chunk
    which is inclusive of both).

    Args:
        segments: List of (start, end, text) tuples at sentence granularity
        chunks: List of (chunk_start, chunk_end) tuples

    Returns:
        Dict mapping chunk index to newline-joined sentence texts
    """
    chunk_sentences: dict[int, list[str]] = {i: [] for i in range(len(chunks))}

    for seg_start, seg_end, seg_text in segments:
        for i, (chunk_start, chunk_end) in enumerate(chunks):
            is_last = i == len(chunks) - 1
            in_chunk = (chunk_start <= seg_start <= chunk_end) if is_last else (chunk_start <= seg_start < chunk_end)
            if in_chunk:
                chunk_sentences[i].append(seg_text)
                break

    return {i: "\n".join(sents) for i, sents in chunk_sentences.items()}
```

**Why sentence-level instead of word-level for text assignment:**

- Sentence segments already have proper text formatting (no spacing issues)
- Sentence segments already have accurate timestamps for chunk assignment
- The forced aligner needs line-structured text (newlines between sentences) to produce per-line timestamps
- Word-level timestamps are still needed for the **fallback path** (when forced alignment fails per chunk)

**Changes to main flow** (line ~1625):

```python
# Before:
chunk_texts = assign_text_to_chunks(asr_words_fallback, chunks)

# After:
chunk_texts = assign_sentences_to_chunks(segments, chunks)
```

Keep `asr_words_fallback` for the `align_chunk()` fallback parameter only.

### Fix 2: Fix Chinese character spacing in word-level fallback

When forced alignment fails on a chunk and we fall back to word-level ASR timestamps, `_word_fallback_for_chunk()` joins words with spaces, producing the same spacing issue. Fix by stripping spaces between CJK characters:

```python
import re

def _strip_cjk_spaces(text: str) -> str:
    """Remove spaces between CJK characters (Chinese/Japanese/Korean).

    Word-level ASR treats each CJK character as a separate 'word'.
    Space-joining produces '我 敬 拜 你' instead of '我敬拜你'.
    This function removes inter-CJK spaces while preserving spaces
    around non-CJK text (e.g., 'I will sing Hallelujah').
    """
    return re.sub(r'([\u4e00-\u9fff\u3400-\u4dbf])\s+(?=[\u4e00-\u9fff\u3400-\u4dbf])', r'\1', text)
```

Apply in `_word_fallback_for_chunk()` (line ~1130-1138):

```python
# Before:
seg_text = " ".join(w[2] for w in current_words)

# After:
seg_text = _strip_cjk_spaces(" ".join(w[2] for w in current_words))
```

Also apply in `assign_text_to_chunks()` (line ~1015) if it's kept for any purpose:

```python
# Before:
return {i: " ".join(texts) for i, texts in chunk_texts.items()}

# After:
return {i: _strip_cjk_spaces(" ".join(texts)) for i, texts in chunk_texts.items()}
```

### Fix 3: Fix diagnostic table zip mismatch

The diagnostic `zip(segments, results)` pairs ASR segments with output results by position, but these lists have different lengths (17 vs 2). The diagnostic should either:

**Option A**: Only show results (not try to pair with ASR segments):

```python
for start, end, text in results:
    # ... show start, text, replaced ...
```

**Option B**: Match results to their corresponding ASR segment by timestamp proximity:

```python
for start, final_text, replaced in results:
    # Find the ASR segment closest to this result's start time
    best_seg = min(segments, key=lambda s: abs(s[0] - start))
    # ... show best_seg text vs final_text ...
```

Option A is simpler and sufficient for diagnostic purposes.

### Fix 4 (optional): Handle sentence overlap at chunk boundaries

With sentence-based assignment, a sentence whose start time falls in the overlap region will be assigned to only one chunk (the first one whose range includes the start time). This is correct for the forced aligner (which needs non-overlapping text), but means the overlap region may have slightly different alignment quality.

The existing `merge_chunks()` deduplication logic already handles this by preferring alignment from the chunk interior. No change needed unless testing reveals quality issues at boundaries.

---

## Expected Outcome After Fixes

### With `--no-snap`

17 LRC lines, one per ASR sentence, with accurate timestamps and properly formatted Chinese text:

```
[00:21.90] 我敬拜你，荣耀的天父，无人能与你相比。
[00:41.45] 我敬拜你，圣洁的天父，你是我的一切。
[01:04.97] 啊。
[01:14.86] 我敬拜你，荣耀的天父，无人能与你相比。
[01:40.40] 我敬拜你，圣洁的天父，你是我的一切。
...
```

### With `--snap`

17 lines snapped to 11 canonical lines. Repeated verses/choruses map to the same canonical line via sequential snap with wrap-around:

```
[00:21.90] 我敬拜祢　榮耀的天父
[00:41.45] 無人能與祢相比
[00:41.45] 我敬拜祢　聖潔的天父
[00:41.45] 祢是我的一切
...
```

(The exact timestamps depend on forced alignment accuracy.)

---

## Additional Observations

### `--lyrics-context` is a no-op for filetrans model

The log output confirms:

```
Note: filetrans model does not support system-message context biasing;
context will be used for vocabulary hint only if vocabulary_id is set
```

The `qwen3-asr-flash-filetrans` model does not support system-message context biasing. The `--lyrics-context` flag has no effect when using this model. This is not a bug (the code warns about it), but users should be aware that ASR quality may be lower without context biasing. The non-filetrans `qwen3-asr-flash` model does support context biasing but may have audio length limitations.

### ASR verification score is low (0.34)

The low score is expected because:
1. The ASR text is simplified Chinese, canonical lyrics are traditional Chinese (the `verify_asr_quality` function does not normalize script before scoring)
2. The filetrans model had no context biasing
3. The ASR text includes repeated sections (performance structure) while canonical lyrics have unique lines only

The `verify_asr_quality` function should also normalize script (like `sequential_canonical_snap` does) for a more accurate diagnostic score. This is a separate minor issue.

### Sentence-level timestamps from filetrans may have zero-duration entries

Several word entries in the raw ASR response have `begin_time == end_time` (e.g., sentence 3, word "啊" at 64972/64972ms). This suggests the filetrans model has lower timestamp precision for some segments. The forced aligner should correct these timestamps, but the fallback path (word-level ASR) would inherit them.
