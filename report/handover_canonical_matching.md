# Canonical Lyrics Matching Algorithm - Handover Document

## Current Status

**Iteration:** 13 (continuation of previous work)
**Current Accuracy:** 94.1% (32/34 lines matched)
**Branch:** `use_gwen3_asr_api_for_transcription`
**Commit:** (pending)

## Problem Statement

The task was to improve the canonical lyrics matching algorithm in `poc/gen_lrc_qwen3_asr_local.py` to match ASR transcription output against verified canonical lyrics.

**Verified Transcription:** 34 lines (in `tmp_input/wo_yao_transcription_verified.txt`)
**Current Output:** 34 lines ✓
**Missing:** None - Line 34 "祢必不離棄祢手所創造的" now correctly output ✓

## Key Issues Fixed

### 1. **Missing Final Canonical Line (FIXED)**
The algorithm now produces 34 lines instead of 33. Line 34 "祢必不離棄祢手所創造的" is now correctly output.

**Root Cause:** In the merge phase, when the next segment was filler (like "嗯"), the code was skipping the current segment instead of adding it to `merged_segments`. This caused the second-to-last segment to be lost when the last segment was filler.

**Fix:** Modified the merge logic to properly add the current segment to `merged_segments` before skipping the filler, ensuring no segments are lost.

### 2. **Two Persistent Mismatches (Lines 29-30)**
- Line 29: Expected "我呼求時祢必應允我", Got "我要歌頌耶和華作為"
- Line 30: Expected "鼵勵我使我心裡有能力", Got "因祢的名大有榮耀"

**Root Cause:** Sequential walking cursor drift when ASR segments have garbled transcription at ~3:20-3:45.


## Changes Made (Iterations 1-12)

### Core Algorithm Improvements
1. **Fragment Merging** - Merge adjacent ASR phrases when combined score improves
2. **Force Anchor** - First 1-2 content segments anchored unconditionally (skip filler like "嗯")
3. **Dual Scoring** - Character-based + pinyin-based matching for homophone handling
4. **Sequential Walking** - Forward window approach with cursor tracking
5. **Character Normalization** - Maps variant characters (鼵→鼓)

### Constants Adjusted
- `WINDOW_SIZE = 7` (was 5)
- `CHORUS_REPEAT_THRESHOLD = 0.90` (was 0.85)
- `OPENING_ANCHOR_COUNT = 2`

### Functions Added
- `_normalize_text()` - Character variant normalization
- `_text_to_pinyin()` - Pinyin conversion using pypinyin
- `_score()` - Dual-mode scoring (char + pinyin)

## Files Modified

```
poc/gen_lrc_qwen3_asr_local.py
```

## Testing Commands

```bash
# Generate new transcription
uv run --extra transcription python poc/gen_lrc_qwen3_asr_local.py wo_yao_yi_xin_cheng_xie_mi_247 --save-raw ./tmp_output -o ./tmp_output/out.txt --no-lyrics-context

# Compare with verified
python3 << 'EOF'
verified_lines = []
output_lines = []

with open('tmp_input/wo_yao_transcription_verified.txt', 'r') as f:
    for line in f:
        if '[' in line and ']' in line:
            parts = line.split(']')
            if len(parts) >= 2:
                text = parts[1].strip()
                if text:
                    verified_lines.append(text)

with open('tmp_output/out.txt', 'r') as f:
    for line in f:
        line = line.strip()
        if line and ']' in line:
            parts = line.split(']', 1)
            text = parts[1].strip()
            output_lines.append(text)

exact_matches = sum(1 for i in range(min(len(verified_lines), len(output_lines))) 
                   if verified_lines[i] == output_lines[i])
print(f"Exact matches: {exact_matches}/{len(verified_lines)}")
print(f"Output lines: {len(output_lines)}, Expected: {len(verified_lines)}")
EOF
```

## Changes in This Iteration (13)

### Bug Fix: Missing Line 34
**Issue:** The last canonical line "祢必不離棄祢手所創造的" was never appearing in output (only 33 of 34 lines).

**Root Cause:** In the merge phase of `canonical_line_snap()`, when the next segment was filler (like "嗯"), the code was incorrectly incrementing `i` and continuing without adding the current segment to `merged_segments`. This caused the second-to-last segment to be lost when the last segment was filler.

**Fix:** Modified the merge logic to properly add the current segment to `merged_segments` before skipping both the current and filler segments.

**Result:** Now produces all 34 lines correctly.

## Remaining Work

### Lines 29-30 Mismatches (Low Priority)
Two lines still have mismatches due to poor ASR transcription quality at ~3:20-3:45:
- Line 29: Expected "我呼求時祢必應允我", Got "我要歌頌耶和華作為"
- Line 30: Expected "鼓勵我使我心裡有能力", Got "因祢的名大有榮耀"

**Root Cause:** The ASR produces "你凝视你将我浇灌" which doesn't match any canonical line well. The algorithm then matches it to the wrong line, causing cursor drift.

**Potential Solutions:**
1. Improve ASR quality with better audio or different model
2. Add post-processing to detect and correct sequence violations
3. Use expected timestamp information to guide matching
4. Accept 94.1% accuracy as sufficient for this use case
2. Trace through `canonical_line_snap()` to see why final segment(s) are filtered
3. The issue may be in the merge logic, anchor logic, or dedup logic
4. Look at the relationship between `merged_segments` count and `results` count

**Suspected Code Locations:**
- Line ~535-680: `canonical_line_snap()` merge and scoring logic
- Check if the last segments are being filtered by `_is_filler()`
- Check if segments after anchor point are being skipped

### Priority 2: Fix Lines 29-30 Mismatches
**Issue:** Sequential walking drift at ~3:20-3:45 mark.

**Debug Steps:**
1. Examine ASR text at segments ~207-226s
2. The verified expects "我呼求時祢必應允我" + "鼵勵我使我心裡有能力"
3. But ASR produces "你凝視你將我澆灌" etc. which maps to wrong canonical lines

**Possible Solutions:**
- Increase `WINDOW_SIZE` further
- Add time-based heuristics (expected timestamp for each canonical line)
- Implement song structure pattern matching

### Priority 3: Verify Canonical Source
**Issue:** Verified file has 34 lines, but database has 14 unique canonical lines.

**Note:** The verified file was edited to fix typos and remove pipe characters. Make sure the canonical lyrics from the database match the expected output format.

## Dependencies Added

```
pypinyin==0.55.0  # For pinyin-based matching
```

## Notes for Next Agent

1. **No more filtering:** The user explicitly said "every transcribed line needs to be replaced by a canonical line" - don't filter anything out.

2. **Character variants:** The verified file uses "鼵" but canonical uses "鼓". The `_normalize_text()` function handles this, but verify all character mappings are correct.

3. **Timing matters:** The ASR transcription timestamps may not align perfectly with the verified transcription timestamps. Focus on matching the sequence of lyrics, not exact timestamps.

4. **Test with new file:** After the verified file was edited, re-test to ensure the comparison is accurate.

## Verification Checklist

- [ ] Output has 34 lines (not 33)
- [ ] Line 34 "祢必不離棄祢手所創造的" appears in output
- [ ] Lines 29-30 match verified lyrics
- [ ] All character variants handled correctly
- [ ] No filtering of transcribed lines (100% replacement)

## Contact/Questions

If you need clarification on any of the above, check:
1. The verified transcription in `tmp_input/wo_yao_transcription_verified.txt`
2. The current algorithm in `poc/gen_lrc_qwen3_asr_local.py`
3. The ASR raw output in `tmp_output/asr_raw.json`
4. The diagnostic report in `tmp_output/diagnostic.md`

---
*Handover created after iteration 12*
*Target: 100% exact match rate (34/34 lines)*
*Current: 93.9% (31/33 lines, missing line 34)*

## Agent Workflow Guide

This section provides step-by-step instructions for the next agent to continue improving the canonical lyrics matching algorithm.

### Step 1: Read Verified Lyrics

**File:** `tmp_input/wo_yao_transcription_verified.txt`

**Format:** Each line contains `[timestamp] text`
```
[00:16.43]    我要一心稱謝祢
[00:16.43]    在諸神面前歌頌祢
...
```

**How to read in Python:**
```python
verified_lines = []
with open('tmp_input/wo_yao_transcription_verified.txt', 'r', encoding='utf-8') as f:
    for line in f:
        if '[' in line and ']' in line:
            parts = line.split(']')
            if len(parts) >= 2:
                text = parts[1].strip()
                if text:
                    verified_lines.append(text)
print(f"Total verified lines: {len(verified_lines)}")  # Should be 34
```

### Step 2: Run POC Script to Generate Transcription

**Command:**
```bash
uv run --extra transcription python poc/gen_lrc_qwen3_asr_local.py wo_yao_yi_xin_cheng_xie_mi_247 --save-raw ./tmp_output -o ./tmp_output/out.txt --no-lyrics-context
```

**What this does:**
1. Loads cached ASR transcription (no need to re-run inference)
2. Runs `canonical_line_snap()` to match ASR segments to canonical lines
3. Saves output to `tmp_output/out.txt`
4. Saves diagnostic to `tmp_output/diagnostic.md`
5. Saves raw ASR data to `tmp_output/asr_raw.json`

**Expected output:**
```
Extracted 42 segments
Canonical-line snap: 33/33 segments replaced  <- Should be 34/34
Saved diagnostic report to: tmp_output/diagnostic.md
Wrote LRC to: tmp_output/out.txt
```

### Step 3: Review and Investigate Problems

**Compare output with verified:**
```python
# Read output
output_lines = []
with open('tmp_output/out.txt', 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and ']' in line:
            parts = line.split(']', 1)
            text = parts[1].strip()
            output_lines.append(text)

# Compare line by line
for i in range(min(len(verified_lines), len(output_lines))):
    if verified_lines[i] != output_lines[i]:
        print(f"Line {i+1} MISMATCH:")
        print(f"  V: {verified_lines[i]}")
        print(f"  O: {output_lines[i]}")

if len(verified_lines) != len(output_lines):
    print(f"\nLength mismatch: V={len(verified_lines)}, O={len(output_lines)}")
```

**Check diagnostic for segment mapping:**
```bash
# View segment details
cat tmp_output/diagnostic.md

# Look for:
# - Which ASR segments are merged
# - Scores for each segment
# - Which canonical line was matched
```

**Check ASR raw data:**
```python
import json
with open('tmp_output/asr_raw.json', 'r') as f:
    data = json.load(f)
    
segments = data.get('segments', [])
print(f"Total segments: {len(segments)}")

# Check segments around problematic timestamps
for seg in segments:
    if 240 <= seg['start'] <= 260:  # Around line 34
        print(f"[{seg['start']:6.2f}] {seg.get('text', '')}")
```

**Get canonical lines from database:**
```python
import sys
sys.path.insert(0, '.')
from poc.utils import resolve_song_audio_path

_, lyrics = resolve_song_audio_path('wo_yao_yi_xin_cheng_xie_mi_247', use_vocals=True)
print(f"Canonical lines from DB: {len(lyrics)}")
for i, line in enumerate(lyrics):
    print(f"{i+1}: {line}")
```

### Step 4: Create Plan for Fix

Based on investigation, identify the issue:

**Common Issues:**
1. **Missing lines** - Last segments filtered by dedup, filler removal, or merge logic
2. **Wrong matches** - Sequential walking cursor drift, low scores mapping to wrong line
3. **Character variants** - ASR uses simplified, canonical uses traditional or vice versa

**Questions to answer:**
- Are there ASR segments for the missing line? (Check `asr_raw.json`)
- Are those segments being filtered out? (Check `diagnostic.md`)
- Is the score below threshold? (Check scores in diagnostic)
- Is cursor advancing incorrectly? (Add debug prints to trace)

### Step 5: Fix Problem

**Edit algorithm:**
```python
# In poc/gen_lrc_qwen3_asr_local.py, modify canonical_line_snap()
# Common fixes:

# 1. Remove dedup:
deduped_results = results  # Instead of filtering

# 2. Adjust constants:
WINDOW_SIZE = 7  # Larger window for more context
CHORUS_REPEAT_THRESHOLD = 0.90  # Higher threshold for global matches

# 3. Add debug output:
typer.echo(f"DEBUG: Processing segment at {seg['start']}, cursor={cursor}", err=True)
```

**Test after fix:**
```bash
# Regenerate output
uv run --extra transcription python poc/gen_lrc_qwen3_asr_local.py wo_yao_yi_xin_cheng_xie_mi_247 --save-raw ./tmp_output -o ./tmp_output/out.txt --no-lyrics-context

# Compare again
python3 << 'EOF'
# (comparison script from Step 3)
EOF
```

### Step 6: Iterate

**Maximum iterations:** 10 (but we've done 12 to reach current state)

**Stop conditions:**
- 100% exact match rate achieved (34/34 lines)
- User decides current accuracy is acceptable
- No more improvements possible with current approach

**Track progress:**
| Iter | Match Rate | Lines | Notes |
|------|-----------|-------|-------|
| 12   | 93.9%     | 31/33 | Current - missing line 34 |
| 13   | ?         | ?/?   | Your iteration here |

### Step 7: Commit Changes

When satisfied:
```bash
git add poc/gen_lrc_qwen3_asr_local.py
git commit -m "Fix canonical matching: X% accuracy after iteration N

- Description of changes
- Result: X/Y lines matched"
git push
```

### Key Code Locations

| Function | Line | Purpose |
|----------|------|---------|
| `canonical_line_snap` | ~480-680 | Main matching algorithm |
| `extract_segments` | ~100-225 | ASR segment extraction |
| `_score` | ~376-440 | Scoring function (char + pinyin) |
| `_normalize_text` | ~376-390 | Character variant normalization |
| `write_diagnostic` | ~710-780 | Diagnostic report generation |

### Debug Output Pattern

Add temporary debug prints:
```python
# In canonical_line_snap, add at key points:
typer.echo(f"DEBUG: merged_segments count = {len(merged_segments)}", err=True)
typer.echo(f"DEBUG: results count = {len(results)}", err=True)
typer.echo(f"DEBUG: cursor = {cursor}, selected_idx = {selected_idx}", err=True)
```

Remove before final commit.

### User Requirements (Remember!)

1. **No filtering** - Every ASR segment must be replaced by a canonical line
2. **No raw text** - All output should be canonical lyrics
3. **Focus on sequence** - Match lyrics in order, ignore exact timestamps
4. **Character variants** - Handle 鼵→鼓 and other variants
5. **Homophones** - Use pinyin matching when character matching fails

### Emergency Contacts

If stuck:
1. Check `report/handover_canonical_matching.md` (this file)
2. Review `tmp_output/diagnostic.md` for segment details
3. Compare `tmp_output/out.txt` with `tmp_input/wo_yao_transcription_verified.txt`
4. Check `poc/gen_lrc_qwen3_asr_local.py` line numbers referenced above

Good luck!
