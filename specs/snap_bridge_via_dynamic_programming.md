# Plan: Dynamic Programming Snap Algorithm (Cross-Segment Consensus)

## Context

`poc/gen_lrc_qwen3_asr_local.py::canonical_line_snap()` currently achieves **32/34 lines (94.1%)** on `wo_yao_yi_xin_cheng_xie_mi_247`. The single failure is verified line 29 at the bridge section, where the song skips canonical[8] and [9] and jumps from [7] directly to [10].

The existing algorithm is a **greedy left-to-right walk** with a monotonically advancing cursor. When ASR text is garbled (`best_score_all < 0.40`), it falls back to sequential cursor + duration-based `n_lines_est`. This cannot represent structural jumps: no sequential emit from `cursor=8` can produce `[10, 11]`.

Per `report/handover_snap_bridge_problem.md` §Option B, replacing the greedy walk with a **Dynamic Programming consensus** solves this without requiring manual song-structure annotation. The DP jointly scores *all* segments against *all* canonical indices under monotonic assignment, so a low-confidence segment's index is pinned by its high-confidence neighbors' anchors rather than by its own weak score.

Constraint (hard, from user): snap target is only the 14-line canonical set. The 34-line verified file is evaluation ground truth and must not leak into the algorithm.

Goal: fix verified line 29 (bridge) while preserving the 32 currently correct lines. Target 34/34 on `wo_yao`.

---

## Approach: Monotonic-with-Repeats DP over Merged Segments

### Model

Let `M` = merged segments after Phase 2 (currently ~30 for `wo_yao`), `C` = canonical lines (14 for `wo_yao`). The existing merge pass, filler skip, and opening anchor stay unchanged. The DP replaces only the **sequential walk core** (lines 631–754).

State: `dp[i][j][k]` = best cumulative score using the first `i` merged segments, where segment `i-1` was assigned the contiguous canonical run ending at index `j` with multiplicity-layer `k` (how many times we have wrapped through C so far).

For `wo_yao`, two full chorus repeats + a bridge ≈ 3 layers. Set `K_MAX = 4` (configurable).

### Transitions

From `dp[i-1][j_prev][k_prev]` to `dp[i][j][k]`:

1. **Single-line assignment**: segment `i` emits canonical line `(j, k)`. Allowed if `(k, j) >= (k_prev, j_prev)` in lex order, and either:
   - `(k, j) == (k_prev, j_prev + 1)` (sequential continue within a layer),
   - `k == k_prev and j > j_prev + 1` (forward skip within a layer — the bridge case, penalized by `SKIP_PENALTY * (j - j_prev - 1)`),
   - `k == k_prev + 1 and j <= j_prev` (layer wrap: new chorus/verse repetition, penalized by `WRAP_PENALTY` if `j > 0`; free if `j == 0`).
2. **Multi-line assignment** (long garbled segment covers several canonical lines): segment `i` emits canonical lines `(j_start..j, k)` contiguous. Cost = sum of per-line scores. Allowed count bounded by `n_lines_est = max(1, round(seg_duration / AVG_LINE_DURATION))` with ±1 slack.

Transition cost = sum of `_combined_score(seg_text, canonical_lines[j'])` over emitted indices + any penalty.

### Penalties (tunable via CLI, defaults chosen conservatively)

- `SKIP_PENALTY = 0.15` — cost of skipping a canonical index within a layer (discourages gratuitous jumps, but small enough that a bridge skip of 2 indices costs 0.30, which is less than emitting the wrong line at score 0.22).
- `WRAP_PENALTY = 0.05` — small cost for starting a new layer mid-sequence (allow chorus repeats cheaply).
- `MISSING_PENALTY = 0.0` — it is acceptable to leave trailing canonical indices unused.

### Opening anchor

The existing Phase 3 (force canonical[0] and canonical[1] for the first two content segments) becomes a **boundary condition** for the DP: `dp[2][1][0]` is seeded with the score of that assignment and all other entries at that boundary are `-inf`. This preserves the anchor invariant and keeps behavior identical for the opening.

### Output

Backtrack the optimal `dp[|M|][*][*]` to recover per-segment canonical-index lists. For each merged segment emitting `m` lines, distribute timestamps evenly across `[seg.start, seg.end]` as today (existing code at lines 734–744 is reused verbatim).

### Complexity

`O(|M| * C * K_MAX * (C + L_MAX))` where `L_MAX` = max multi-line emit (≈ 3). For `wo_yao`: 30 × 14 × 4 × (14+3) ≈ 28k cell updates. Trivial runtime.

---

## Why this fixes the bridge

At the 207–226s garbled segment (score 0.30 against `canonical[9]`):

- Greedy: cursor=8, sequential emit [8, 9] → wrong.
- DP: considers all `(j, k)` endings. The path `…→(seg=A, j=11, k=0, m=2 lines [10, 11])→(seg=B, j=11, k=1, m=2 lines [10, 11])→(seg=C, j=11, k=1)→(seg=D, j=12, k=1)` costs `SKIP_PENALTY * 2 ≈ 0.30` to skip [8,9] but earns high scores from segments C (0.70 on canonical[11]) and D (0.89 on canonical[12]) downstream. The bridge-skipping path beats the sequential [8,9] path because even though segment A scores only ~0.22 on canonical[10], segment B also scores ~0.22 on canonical[10] *and* segment C strongly confirms canonical[11] — the consensus locks in [10, 11] at both segments.

---

## Critical files & line ranges

| File | Change |
|------|--------|
| `poc/gen_lrc_qwen3_asr_local.py:472-760` | `canonical_line_snap()` — replace Phase 4 (lines 631-754) with DP. Keep Phase 1-3 (merge, filler skip, opening anchor) and the output-emission block (lines 734-744) intact. |
| `poc/gen_lrc_qwen3_asr_local.py:644-655` | `_combined_score()` — keep as-is; extract from closure to module level so the DP helper can call it with the same pinyin-gating behavior. |
| `poc/gen_lrc_qwen3_asr_local.py:1106-1160` | Add CLI flags `--snap-algo {greedy,dp}` (default `dp`), `--dp-skip-penalty`, `--dp-wrap-penalty`, `--dp-k-max` for tuning and A/B comparison. |
| `poc/gen_lrc_qwen3_asr_local.py:1009-1103` | `write_diagnostic()` — extend diagnostic columns to show each segment's assigned `(canonical_idx, layer_k, is_multi_emit)` from DP backtrack, so regressions are debuggable. |

No changes to `extract_segments`, catalog loading, `resolve_song_audio_path`, `generate_comparison_report`, or any file outside `poc/gen_lrc_qwen3_asr_local.py`.

## Functions / utilities to reuse

- `_score()` (line 409) — char + pinyin fuzzy. Unchanged.
- `_combined_score()` (line 644) — extract to module scope; unchanged semantics (pinyin gate at 8 chars).
- `_normalize_text()`, `_is_filler()`, `detect_chinese_script()` — unchanged.
- Phase 2 merge pass (lines 524-604) — unchanged (the DP consumes `merged_segments`).
- Opening anchor (lines 606-629) — unchanged, feeds DP initial state.
- Timestamp interpolation block (lines 734-744) — unchanged, runs once per segment on DP-backtracked lines.

## Implementation sketch (inside `canonical_line_snap`, replacing lines 631-754)

```python
# Precompute scores: S[i][j] = _combined_score(merged_segments[i].text, canonical_lines[j])
# Shape (|M|, C).
S = [[_combined_score(seg["text"], canonical_lines[j]) for j in range(n_canonical)]
     for seg in merged_segments[anchor_end:]]

# n_lines_est per segment, same heuristic as today
L = [max(1, round((seg["end"] - seg["start"]) / AVG_LINE_DURATION))
     for seg in merged_segments[anchor_end:]]

# DP: dp[i][j][k] = (best_score, back_ptr). Initial state seeded from opening anchor end.
# Sentinel score -inf. Run transitions described above.

# Backtrack to get per-segment list[list[int]] canonical-index assignments.
# Emit via existing timestamp-interpolation block.
```

## Verification

1. **Regression on `wo_yao`**: run the reproduce command from the handover doc. Target: `Matching rate: 34/34` in `tmp_output/wo_yao_comparison.txt`. Must not regress lines 0–28 or 30–33.
   ```
   uv run --extra transcription python poc/gen_lrc_qwen3_asr_local.py \
     --save-raw ./tmp_output -o ./tmp_output/out.txt \
     --no-lyrics-context wo_yao_yi_xin_cheng_xie_mi_247 \
     --vocal-stem ./tmp_input/wo_yao_clean_vocals.flac \
     --verified-lyrics ./tmp_input/wo_yao_transcription_verified.txt \
     --comparison-output ./tmp_output/wo_yao_comparison.txt
   ```
   With `--reuse-transcription` on (default), the cached ASR at `~/.cache/qwen3_asr/...json` skips the model — iteration is fast (~1s per run).

2. **A/B comparison**: run once with `--snap-algo greedy` and once with `--snap-algo dp`. Diff outputs line-by-line. The only intended change is verified line 29 (`我要歌頌耶和華作為` → `我呼求時祢必應允我`).

3. **Diagnostic inspection**: open `tmp_output/diagnostic.md` after DP run. Confirm the 207–226s and 226–232s bridge segments both show assigned canonical indices `[10, 11]` and a `layer_k` bump consistent with a chorus repeat.

4. **Smoke test on a non-bridge song**: pick a second song from the catalog that the greedy algorithm already handles well, run with `--snap-algo dp`, confirm no regression. (Tuning goal: penalties should be small enough that simple songs fall into the trivial monotonic path.)

5. **Penalty sensitivity**: sweep `--dp-skip-penalty` over `{0.05, 0.10, 0.15, 0.20, 0.25}` to confirm the chosen default is in a stable region (result unchanged across at least the middle three values).

Success criterion: `wo_yao` reaches 34/34 (100%) and the second smoke-test song stays at its current score.
