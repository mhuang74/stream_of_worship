# Handover: Canonical-Line Snap — Bridge Section Alignment Problem

## Current State

`poc/gen_lrc_qwen3_asr_local.py` achieves **32/34 lines matched (94.1%)** for song `wo_yao_yi_xin_cheng_xie_mi_247`. One line is mismatched (line 29). No lines are missing.

Command to reproduce:
```
uv run --extra transcription python poc/gen_lrc_qwen3_asr_local.py \
  --save-raw ./tmp_output -o ./tmp_output/out.txt \
  --no-lyrics-context wo_yao_yi_xin_cheng_xie_mi_247 \
  --vocal-stem ./tmp_input/wo_yao_clean_vocals.flac \
  --verified-lyrics ./tmp_input/wo_yao_transcription_verified.txt \
  --comparison-output ./tmp_output/wo_yao_comparison.txt
```

---

## The Core Problem

### Song Structure

The canonical lyrics set has **14 unique lines** (indices 0–13). The verified performance uses these in a non-linear order — specifically, the **bridge section** (around 3:20–3:47 in the recording) jumps from canonical[7] directly to canonical[10], **skipping canonical[8] and canonical[9]**:

```
canonical[7]  = 祢應許必將我救活
canonical[8]  = 我要歌頌耶和華作為    ← SKIPPED in bridge
canonical[9]  = 因祢的名大有榮耀      ← SKIPPED in bridge
canonical[10] = 我呼求時祢必應允我
canonical[11] = 鼓勵我使我心裡有能力
```

The verified file around the bridge section:
```
[03:20.10] 我雖行在困苦患難中   ← canonical[5]
[03:20.10] 祢應許必將我救活     ← canonical[7]  (cursor reaches ~8 after this)
[03:33.23] 我呼求時祢必應允我   ← canonical[10] ← PROBLEM LINE (verified[29])
[03:33.23] 鼓勵我使我心裡有能力  ← canonical[11]
[03:46.50] 我呼求時祢必應允我   ← canonical[10] (repeated!)
[03:46.50] 鼓勵我使我心裡有能力  ← canonical[11]
[04:02.27] 祢必成全關乎我的事   ← canonical[12]
```

### What ASR Produces at the Bridge

The ASR model outputs garbled text for the bridge section. From `tmp_output/diagnostic.md`:

| Start  | End    | ASR Text (simplified)               | Best Match            | Score |
|--------|--------|--------------------------------------|-----------------------|-------|
| 194.44 | 206.92 | 因你慈爱永远长存我虽行在困苦患难中          | 我雖行在困苦患難中 (canon[5])  | 0.69  |
| 207.40 | 226.36 | 你殷实地将我浇灌我要歌颂野花花丛里因你滋密的有荣耀 | 因祢的名大有榮耀 (canon[9])    | 0.30  |
| 226.82 | 232.82 | 我不强势你别疑虑我                      | 我呼求時祢必應允我 (canon[10]) | 0.22  |
| 233.46 | 240.02 | 不理我是我心里有能力                    | 鼓勵我使我心裡有能力 (canon[11])| 0.70  |
| 243.07 | 250.43 | 你必成全关乎我的事                      | 祢必成全關乎我的事 (canon[12]) | 0.89  |

Both 207-226s and 226-232s segments score **below 0.40** → they enter the **low-confidence sequential path** in `canonical_line_snap()`.

### How the Low-Confidence Path Works (current code)

In `canonical_line_snap()` (around line 678):
```python
LOW_CONFIDENCE_THRESHOLD = 0.40
AVG_LINE_DURATION = 8.0  # seconds per canonical line

seq_cursor = cursor % n_canonical
low_confidence = best_score_all < LOW_CONFIDENCE_THRESHOLD

if low_confidence:
    seg_duration_s = seg["end"] - seg["start"]
    n_lines_est = max(1, round(seg_duration_s / AVG_LINE_DURATION))
    selected_idx = seq_cursor
    selected_line = canonical_lines[seq_cursor]
    used_window = True
```

For the 207-226s segment (18.9s duration):
- `cursor = 8` after 194-206s emitted canonical[5]+[7] (merged gap fill)

  > Wait — the 194-206s merged segment covers `我雖行在困苦患難中` (canon[5]) + gap-fill for canon[6] or [7]? Let me trace carefully.

Actually the cursor state at 207.4s depends on how the 194-206s segment is processed. Its matched canonical is [5] with score 0.69 (window match). The 194-206s segment is a merged segment (covers ~12s). After it, cursor = 6 or 7 depending on whether gap-fill fires.

- At 207.4s: `seq_cursor = cursor % 14`. With `n_lines_est = round(18.9 / 8.0) = 2`, emits `[seq_cursor, seq_cursor+1]`.
- **If seq_cursor = 7**: emits canonical[7]+[8] = `祢應許必將我救活` + `我要歌頌耶和華作為` → verified[28] gets `祢應許必將我救活` ✓, verified[29] gets `我要歌頌耶和華作為` ✗ (expected `我呼求時祢必應允我`)

The problem is precisely that `n_lines_est = 2` sequential emit of `[7, 8]` places canonical[8] (`我要歌頌耶和華作為`) at verified position 29, which should be canonical[10] (`我呼求時祢必應允我`).

### Why the Bridge Cannot Be Fixed by Linear Cursor Alone

The snap algorithm maintains a monotonically advancing cursor through the 14-line canonical set. The bridge section requires:

1. cursor skips from 7 to 10 (skipping [8] and [9])
2. canonical[10] appears **twice** consecutively
3. canonical[11] appears **twice** consecutively (interleaved after each [10])

No assignment of sequential cursor positions `[seq_cursor, seq_cursor+1, ...]` to the 18.9s garbled segment can produce `[10, 11]` starting from `cursor=8` — because 8 and 9 are not 10 and 11.

Additionally, the two garbled segments cover identical content (`我呼求時祢必應允我 + 鼓勵我使我心裡有能力`) but at different timestamps. The algorithm needs to emit canonical[10]+[11] twice, from two consecutive garbled segments.

---

## Approaches Attempted and Why They Failed

### 1. Enable lyrics context biasing (`--lyrics-context`)

**Hypothesis**: The root cause of garbled ASR is that the model doesn't know the song vocabulary. Passing the canonical lyrics as a context prompt to `Session.transcribe(context=...)` should improve raw transcription quality, reducing the number of segments that fall into the low-confidence path.

**What happened**: The model treated the lyrics in the system prompt as audio content to transcribe and hallucinated them back verbatim. Instead of 34 output lines, the transcription produced 105 lines — the canonical lyrics repeated 3–4 times as extra "transcribed" segments. This was true regardless of format (newline-separated, with descriptive prefix, or space-separated vocabulary terms).

**Why it failed**: Inspecting `mlx_qwen3_asr/tokenizer.py` revealed that the `context=` parameter injects text into `<|im_start|>system\n{context}<|im_end|>`. The Qwen3 model used here is designed for space-separated vocabulary terms as biasing hints, not full lyric passages. Providing full lyrics causes the model to output them as if reading from a document rather than transcribing audio.

**Status**: `--no-lyrics-context` must be used. Context biasing is currently unusable for this song.

---

### 2. Context prefix (descriptive preamble before lyrics)

**Hypothesis**: Wrapping the lyrics with a descriptive prefix (`"This is a Chinese Christian worship song. Use the following canonical lyrics as term/phrase reference..."`) — mirroring the cloud variant at `poc/gen_lrc_qwen3_asr.py:352–362` — would signal to the model that these are reference phrases, not content to transcribe.

**What happened**: Same hallucination as Approach 1. The model still output the canonical lyrics as extra segments regardless of the framing prefix.

**Why it failed**: The instruction following of the local 1.7B MLX model is weaker than the cloud model. The prefix was not sufficient to change the model's behavior — it continued treating the lyrics as transcription output.

---

### 3. Pinyin boost for all segment lengths

**Hypothesis**: `_score()` with `use_pinyin=True` can recover matches when simplified vs. traditional character differences cause character-level scoring to fail. Adding a pinyin boost (score * 0.9) for all segments would improve overall matching.

**What happened**: The 207–226s garbled segment ("你殷实地将我浇灌我要歌颂野花花丛里因你滋密的有荣耀", 22 Chinese characters) scored **0.762 pinyin** against `因祢的名大有榮耀` (canonical[9], 8 characters). This was a **false positive** — the long garbled text contains phonetic substrings that accidentally match the short canonical line.

**Why it failed**: `fuzz.token_set_ratio` on pinyin strings is not length-normalized in the way that helps here. A long garbled string that contains a few syllables matching a short canonical line scores disproportionately high. At score 0.762, this segment would bypass the low-confidence path entirely and snap to the wrong canonical line.

**Fix applied**: Pinyin boost is now disabled for segments with more than 8 Chinese characters (`_combined_score()` at line ~644). For short segments (≤ 8 chars), pinyin boost is retained since short garbled text doesn't produce accidental high-pinyin-score matches.

---

### 4. Low-confidence sequential fallback (current baseline)

**Hypothesis**: When `best_score_all < 0.40`, the fuzzy matcher can't reliably distinguish repeated chorus lines. Trusting sequential position (`seq_cursor = cursor % n_canonical`) is safer than jumping to the global best match.

**What happened**: Matching rate improved from earlier broken states. For most garbled segments, sequential cursor emits the correct canonical line. **Current score: 32/34 (94.1%).**

**Remaining failure**: At the bridge section (207–226s segment), `cursor ≈ 8` and `seq_cursor = 8`. With `n_lines_est = round(18.9 / 8.0) = 2`, sequential emit produces canonical[8]+[9] = `我要歌頌耶和華作為` + `因祢的名大有榮耀`. But the song needs canonical[10]+[11] at that point. The cursor is 2 positions behind the correct canonical index because the bridge skips [8] and [9].

**Why it's limited**: Sequential cursor cannot skip canonical indices. It always advances linearly through [0, 1, 2, ..., 13, 0, 1, ...]. Any structural jump in the song's use of the canonical set cannot be modeled.

---

### 5. Lookahead anchor — backfill all indices from cursor to anchor

**Hypothesis**: Find the first upcoming high-confidence segment (score ≥ 0.55) and use its canonical index as an anchor. Backfill all canonical indices from `seq_cursor` to `anchor - 1` for the current garbled segment.

**Concrete trace for the bridge**:
- 207–226s segment: `seq_cursor = 8`, lookahead scans ahead
- 233–240s segment scores 0.70 against canonical[11] → `lookahead_anchor_idx = 11`
- Backfill: `range(8, 11)` = [8, 9, 10] → emits `我要歌頌耶和華作為` + `因祢的名大有榮耀` + `我呼求時祢必應允我`

**What happened**: Result was 32/34 (94.1%) but with 2 EXTRA lines. canonical[8] (`我要歌頌耶和華作為`) and canonical[9] (`因祢的名大有榮耀`) appeared as extra output lines with no corresponding verified position.

**Why it failed**: The bridge skips canonical[8] and [9]. Backfilling from `seq_cursor` to `anchor` includes indices that the song never uses at this structural position. There is no general way to know which indices in the range to skip.

---

### 6. Lookahead anchor — emit anchor-1 only (single line per garbled segment)

**Hypothesis**: Each garbled segment covers exactly one canonical line that leads into the anchor. Emit only `canonical[anchor - 1]` for the garbled segment, and let the next garbled segment do its own lookahead for the second occurrence.

**Concrete trace for the bridge**:
- 207–226s segment: anchor=11, emit `canonical[10]` = `我呼求時祢必應允我` ✓. Cursor → `seq_cursor + 1 = 9`.
- 226–232s segment: `seq_cursor = 9`, lookahead scans ahead. 233–240s scores 0.70 against canonical[11]. `ahead_best_idx = 11 >= seq_cursor = 9` → anchor = 11. Emit `canonical[10]` = `我呼求時祢必應允我` ✓. Cursor → 10.
- 233–240s segment: cursor = 10, window = [10..17]. Scores 0.70 against canonical[11] → selected_idx = 11. Gap fill emits canonical[10] before [11] → extra `我呼求時祢必應允我` output.

**What happened**: Results were worse overall: 30/34 (88.2%) with gaps at verified lines 8–9, 28, and 31. The `cursor = seq_cursor + 1` advance disrupted the earlier 64–83s segment (another multi-line garbled segment) which also entered this path and advanced cursor by only 1 instead of 2, causing a cascade of misaligned matches from line 8 onward.

**Why it failed**: `cursor = seq_cursor + 1` is too conservative — it advances by 1 regardless of how many canonical lines the garbled segment actually covers. This breaks all other multi-line low-confidence segments, not just the bridge. And the 233–240s spurious segment (which doesn't correspond to any verified line) subsequently matched canonical[11] a third time, producing an extra output line.

---

### 7. Lookahead anchor — duration-constrained backfill

**Hypothesis**: Instead of emitting all indices from cursor to anchor, use `n_lines_est` to determine how many lines to emit, ending at the anchor: `range(anchor - n_lines_est, anchor)`.

**Concrete trace for the bridge**:
- 207–226s segment: anchor=11, n_lines_est=2. Emit `range(11-2, 11)` = [9, 10] → `因祢的名大有榮耀` + `我呼求時祢必應允我`

**What happened**: canonical[9] (`因祢的名大有榮耀`) is still wrong. 91.2% (31/34) with regressions at lines 8–9, 28, 31.

**Why it failed**: The bridge jumps from [7] to [10], so both [8] and [9] should be skipped. `anchor - n_lines_est = 9` still includes canonical[9], which is not in the bridge. Duration-based estimation cannot determine which specific canonical indices to include — it only knows how many.

---

## Exact Failure State (94.1% baseline, current code)

From `tmp_output/wo_yao_comparison.txt`:
```
28  | 28 | 祢應許必將我救活         ← correct
29  | 29 | 我呼求時祢必應允我 [DIFFERS]
           expected: 我呼求時祢必應允我
           got:      我要歌頌耶和華作為  ← canonical[8], should be canonical[10]
30  | 30 | 鼓勵我使我心裡有能力      ← correct  
31  | 31 | 我呼求時祢必應允我        ← correct (second occurrence)
32  | 32 | 鼓勵我使我心裡有能力      ← correct
```

Only line 29 is wrong. Lines 30-32 are correct.

---

## What a Working Solution Needs

The snap algorithm receives these inputs at the bridge:

1. **Segment A** (207-226s, score 0.30): garbled, `n_lines_est=2` → must emit canonical[10]+[11]
2. **Segment B** (226-232s, score 0.22): garbled, `n_lines_est=1` (6s) → must emit canonical[10]+[11]

The cursor before Segment A is `~8` (after canonical[7] was emitted by the prior merged segment).

For Segment A to emit [10, 11] instead of [8, 9] or [7, 8], the algorithm must:
- **Identify that canonical[8] and [9] should be skipped** — the bridge jumps directly to [10].

There are two viable approaches for a future implementer:

### Option A: Song Structure Metadata
Extend the canonical lyrics input to include structural annotations — e.g., a second file specifying that the bridge jumps from [7] to [10]. The snap algorithm consults this when low-confidence and advances cursor to the annotated jump target.

**Pros**: Correct by construction, generalizes to any song structure.
**Cons**: Requires manual annotation per song; adds schema complexity.

### Option B: Cross-Segment Consensus
When a segment is low-confidence, score ALL segments in a local window (±3 segments) against ALL canonical lines simultaneously. Find the assignment that maximizes total score while respecting temporal order. This is a dynamic programming assignment problem.

**Pros**: Fully automatic, no manual annotation needed.
**Cons**: Significantly more complex to implement; may be slow for large segment counts.

### Option C: Accept 94.1% as "Good Enough"
The single error (line 29) is at a bridge section where the raw ASR is deeply garbled (score 0.30). The resulting LRC file is still usable — the wrong canonical line at that position (`我要歌頌耶和華作為`) is a valid song lyric, just from the wrong moment. For practical worship video use, this may be acceptable.

**Cons**: Line 29 shows the wrong lyric for ~19 seconds during the bridge.

---

## Files Relevant to This Problem

| File | Relevance |
|------|-----------|
| `poc/gen_lrc_qwen3_asr_local.py:678-797` | `canonical_line_snap()` — the snap algorithm, low-confidence path |
| `poc/gen_lrc_qwen3_asr_local.py:642-656` | `_combined_score()` — score function with pinyin boost disable for long segments |
| `tmp_input/wo_yao_transcription_verified.txt` | 34-line ground truth with timestamps |
| `tmp_output/diagnostic.md` | Per-segment ASR scores and matched canonical lines |
| `tmp_output/wo_yao_comparison.txt` | Alignment comparison output |
| `tmp_output/asr_raw.json` | Raw ASR output before snap |

---

## Key Constraint (from user)

> **"can ONLY use canonical lyrics as target for matching"**

The verified lyrics file (`tmp_input/wo_yao_transcription_verified.txt`) must NOT be used as the snap target — only the 14-line canonical set. This rules out the straightforward solution of tracking absolute position in the 34-line verified sequence.
