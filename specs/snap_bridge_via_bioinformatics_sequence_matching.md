# Plan: Sequence-Alignment Snap Algorithm (Needleman-Wunsch over Canonical Repeats)

## Context

`poc/gen_lrc_qwen3_asr_local.py::canonical_line_snap()` currently achieves **32/34 lines (94.1%)** on `wo_yao_yi_xin_cheng_xie_mi_247`. The single failure is verified line 29 at the bridge section, where the song skips canonical[8] and [9] and jumps from [7] directly to [10].

The existing algorithm is a **greedy left-to-right walk** with a monotonically advancing cursor. When ASR text is garbled (`best_score_all < 0.40`), it falls back to sequential cursor + duration-based `n_lines_est`. This cannot represent structural jumps: no sequential emit from `cursor=8` can produce `[10, 11]`.

Constraint (hard, from user): snap target is only the 14-line canonical set. The 34-line verified file is evaluation ground truth and must not leak into the algorithm.

Goal: fix verified line 29 (bridge) while preserving the 32 currently correct lines. Target 34/34 on `wo_yao`.

---

## Algorithm Evaluation

Framing: we have a **query sequence** (merged ASR segments, ~30 items) and a **reference sequence** (canonical lines, 14 items that may be reused in any order — verse/chorus/bridge). We want the best monotonic alignment allowing gaps in the reference (bridge skip) and allowing the reference to be repeated (chorus returns).

### Needleman-Wunsch (global alignment) — **chosen**
Classic global DP over `(query_i, reference_j)` with match/mismatch/gap operations. Adapts cleanly to this problem with one twist: the reference isn't 14 symbols — it's **an unrolled tape of canonical lines across K layers** (`K_MAX = 4` for verse + 2 choruses + bridge+outro cushion), total length `14 * K_MAX ≈ 56`. An ASR segment can "match" any position on the tape; a gap in the reference tape corresponds to a skipped canonical line (bridge); advancing across a layer boundary corresponds to a chorus repeat (no penalty at the boundary).

- **State**: `dp[i][t]` = best cumulative score aligning first `i` merged segments ending at reference-tape position `t`.
- **Transitions**:
  - Match: `dp[i][t] = dp[i-1][t'] + score(seg_i, canonical[t mod C])` for all `t' < t`, with a per-gap penalty on `(t - t' - 1)` only *within* a layer (free across layer boundaries).
  - Multi-emit match (long garbled segment covers `m` consecutive canonical lines): `dp[i][t] = dp[i-1][t-m] + sum(scores)`, bounded by `n_lines_est ± 1`.
  - Segment deletion (skip ASR seg): only allowed for filler-like residuals already handled in Phase 2 merge; not used here.
- **Complexity**: `O(|M| * T * (T + L_MAX))` where `T = C * K_MAX ≈ 56`. ≈ 30 × 56 × 60 ≈ 100k ops. Trivial.
- **Why it wins**: Directly models both the bridge (gap in reference with penalty) and chorus repeats (layer boundary, free). Classic, well-understood, easy to audit.

### Smith-Waterman (local alignment) — rejected
Smith-Waterman finds the best *local* substring alignment and resets the DP to 0 on poor regions. That is the wrong shape for this problem: every merged ASR segment must be emitted (per user requirement: 100% replacement), and we want a single alignment covering the whole song, not the best local stretch. Smith-Waterman's reset behavior would drop the low-confidence bridge segments from the alignment entirely — the exact opposite of what we need. Good fit for finding *if* a chorus phrase occurs *somewhere*, bad fit for whole-song alignment.

### Burrows-Wheeler Transform / FM-index — rejected
BWT indexes a reference to make exact/near-exact substring queries in `O(m)` (where `m` = query length). Its strengths:
- Short reads → large reference (millions of bases). Here the reference is 14 lines; no index speedup is meaningful.
- Exact or low-edit-distance matching. Here the ASR is 0.22–0.89 fuzzy with Chinese/pinyin variance — the matching quality is already handled by `_combined_score` using `rapidfuzz` + `pypinyin`.
- Seed-and-extend for long reads. Our "reads" are single ASR phrases of 3–22 chars.
BWT solves a retrieval problem we don't have (fast candidate lookup). It does not solve the alignment-under-structure problem we do have. Using it would still require a downstream DP to chain seed matches — i.e., Needleman-Wunsch under the hood — with no benefit from the BWT layer at this scale.

### Other considered and rejected
- **Hirschberg's algorithm** (Needleman-Wunsch in linear space): premature optimization. `T ≈ 56`, `|M| ≈ 30`; full DP table is 1.7k cells.
- **Affine gap penalties (Gotoh)**: would allow "gap open" vs "gap extend" to discourage fragmented skips. Overkill for a 14-line reference with at most 2 consecutive skipped lines in the bridge. Can be added later if a second failure case demands it.
- **Profile HMM / pair HMM**: would let us learn emission/transition probabilities from a corpus of songs. No training corpus exists; not worth the scaffolding for one song.

---

## Approach: Needleman-Wunsch with Layered Reference Tape

### Model

Let `M` = merged segments after Phase 2 (~30 for `wo_yao`), `C` = canonical lines (14 for `wo_yao`), `K_MAX = 4` (layers = max times the canonical set can be reused across the song). The **reference tape** is the concatenation of `K_MAX` copies of the canonical list: positions `t ∈ [0, T)` where `T = C * K_MAX` and `canon(t) = canonical_lines[t mod C]`. Layer boundaries sit at `t mod C == 0`.

The existing merge pass, filler skip, and opening anchor stay unchanged. Needleman-Wunsch replaces only the **sequential walk core** (lines 631–754).

State: `dp[i][t]` = best cumulative score aligning the first `i` merged segments with segment `i-1` ending at reference-tape position `t`. Backpointer `bp[i][t]` records the prior `(t', m)` (where `m` = number of reference lines covered by segment `i-1`).

### Transitions

From `dp[i-1][t']` to `dp[i][t]`:

1. **Single-line match** (`m = 1`): emit `canon(t)` for segment `i`.
   - Score: `score(seg_i, canon(t)) - gap_penalty(t', t)`.
   - `gap_penalty(t', t)`:
     - `0` if `t = t' + 1` (sequential).
     - `SKIP_PENALTY * (t - t' - 1)` if `t > t' + 1` and `⌊t/C⌋ == ⌊t'/C⌋` (within-layer skip — the bridge case).
     - `WRAP_PENALTY` if `⌊t/C⌋ > ⌊t'/C⌋` (crossing into a new canonical repeat; cost independent of where in the new layer we land).
2. **Multi-line match** (`m > 1`, long garbled segment covering `m` consecutive canonical lines): emit `canon(t-m+1..t)`.
   - Score: `sum(score(seg_i, canon(t'')) for t'' in [t-m+1..t]) - gap_penalty(t', t-m+1)`.
   - `m` bounded by `n_lines_est = max(1, round(seg_duration / AVG_LINE_DURATION))` with ±1 slack (so `m ∈ {max(1, n-1), n, n+1}`).
   - All `m` positions must lie within a single layer (no crossing a boundary mid-emit).

Scoring uses `_combined_score` unchanged — same pinyin-gated rule as today.

### Penalties (tunable via CLI; defaults chosen so the bridge path beats the greedy path)

- `SKIP_PENALTY = 0.15` — within-layer gap. Two-line bridge skip costs 0.30, less than the ≈0.40 score lost by emitting the wrong canonical line at the bridge segments.
- `WRAP_PENALTY = 0.05` — starting a new layer. Small enough that chorus repeats are essentially free, large enough to prevent spurious wraps when a within-layer match exists.
- End-of-reference (remaining tape after final segment): free.
- Start-of-reference: `dp[0][-1] = 0` (sentinel); all other `dp[0][*] = -inf`.

### Opening anchor

Phase 3 (force canonical[0], canonical[1] for the first two content segments) becomes the DP **initial condition**: seed `dp[2][1] = score(seg_0, canon(0)) + score(seg_1, canon(1))` and `dp[2][t] = -inf` for `t ≠ 1`. Backpointers record the fixed assignment. This keeps opening behavior identical and gives the DP a reliable left edge.

### Output

Backtrack from `argmax_t dp[|M|][t]` to recover per-segment canonical-index lists (each a contiguous run of 1..L_MAX indices on the tape; project to `canonical_lines` via `t mod C`). Feed into the existing timestamp-interpolation block (lines 734–744, reused verbatim): for a segment emitting `m` lines, distribute timestamps evenly across `[seg.start, seg.end]`.

### Complexity

`O(|M| * T * (T + L_MAX))` = `O(|M| * C² * K_MAX²)`. For `wo_yao`: 30 × 56 × (56 + 3) ≈ 100k ops. Runtime well under 1s; dominated by `_combined_score` calls, which we precompute into an `|M| × C` matrix (420 calls vs. the greedy's ~900+).

---

## Why this fixes the bridge

At the 207–226s garbled segment (segment A, score 0.30 against `canonical[9]`, 0.22 against `canonical[10]`) followed by 226–232s (segment B, 0.22 against `canonical[10]`, 0.70 against `canonical[11]`):

- **Greedy today**: cursor=8, low-confidence path emits sequential `[8, 9]` → wrong.
- **Needleman-Wunsch**: two candidate paths compete ending at the high-confidence segment C (233–240s, 0.70 on `canonical[11]`):
  - Path A: `seg_A → [8, 9]` (0.22+0.30), `seg_B → [10]` (0.22), `seg_C → [11]` (0.70). Total ≈ 1.44. No skip penalty.
  - Path B: `seg_A → [10, 11]` (0.22+?), `seg_B → [10, 11]` (layer wrap, 0.22+0.70), `seg_C → [11]` (0.70, layer=1). Incurs `SKIP_PENALTY*2 = 0.30` for skipping [8,9] once + `WRAP_PENALTY = 0.05`. Bridge structure makes this the true alignment.
  - Downstream segments D (`canonical[12]`, 0.89) and E (`canonical[13]`, high) anchor the end of layer 1 firmly, giving Path B a large positive tail that Path A cannot match (Path A would need `seg_D` to score highly on `canonical[10]`, which it doesn't).

The `_detect_extra_lines_in_segment` function at lines 792-802 (currently dead code) can supply the multi-line score for segment A's `[10, 11]` emission. The NW objective propagates segment C's and D's strong matches *backward* through backpointers, pulling segment A's alignment to `[10, 11]` — exactly the cross-segment consensus the problem needs.

---

## Critical files & line ranges

| File | Change |
|------|--------|
| `poc/gen_lrc_qwen3_asr_local.py:472-760` | `canonical_line_snap()` — replace Phase 4 (lines 631-754) with Needleman-Wunsch over the layered reference tape. Keep Phase 1–3 (merge, filler skip, opening anchor) and the timestamp-interpolation block (lines 734-744) intact. |
| `poc/gen_lrc_qwen3_asr_local.py:644-655` | `_combined_score()` — extract from closure to module level so the NW helper can call it with the same pinyin-gating behavior. No semantic change. |
| `poc/gen_lrc_qwen3_asr_local.py:1106-1160` | Add CLI flags `--snap-algo {greedy,nw}` (default `nw`), `--nw-skip-penalty` (default 0.15), `--nw-wrap-penalty` (default 0.05), `--nw-k-max` (default 4) for tuning and A/B comparison. |
| `poc/gen_lrc_qwen3_asr_local.py:1009-1103` | `write_diagnostic()` — add columns for each segment's assigned tape positions `(t_start..t_end)`, layer `⌊t/C⌋`, and per-transition penalty, so regressions are debuggable. |

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
# Precompute scores: S[i][j] = _combined_score(seg_i.text, canonical_lines[j])
# Shape: (|M| - anchor_end, C). ~420 rapidfuzz calls for wo_yao.
post_anchor = merged_segments[anchor_end:]
S = [[_combined_score(seg["text"], canonical_lines[j]) for j in range(n_canonical)]
     for seg in post_anchor]

# Per-segment duration-based n_lines_est, same heuristic as today
L_est = [max(1, round((seg["end"] - seg["start"]) / AVG_LINE_DURATION))
         for seg in post_anchor]

T = n_canonical * K_MAX
NEG_INF = float("-inf")
dp = [[NEG_INF] * T for _ in range(len(post_anchor) + 1)]
bp = [[None] * T for _ in range(len(post_anchor) + 1)]

# Initial condition: opening anchor emitted canonical[0], canonical[1] => tape position 1
dp[0][1] = 0.0  # start just past the opening anchor

for i in range(1, len(post_anchor) + 1):
    n_est = L_est[i - 1]
    for m in {max(1, n_est - 1), n_est, n_est + 1}:
        for t in range(m - 1, T):
            layer = t // n_canonical
            # multi-emit must stay within one layer
            if (t - m + 1) // n_canonical != layer:
                continue
            emit_score = sum(S[i - 1][(t - m + 1 + k) % n_canonical] for k in range(m))
            for t_prev in range(-1, t - m + 1):  # -1 sentinel = before-start (only for i==1 if no anchor)
                if dp[i - 1][t_prev] == NEG_INF:
                    continue
                gap = gap_penalty(t_prev, t - m + 1, n_canonical)
                cand = dp[i - 1][t_prev] + emit_score - gap
                if cand > dp[i][t]:
                    dp[i][t] = cand
                    bp[i][t] = (t_prev, m)

# Backtrack from argmax dp[-1]
t_end = max(range(T), key=lambda t: dp[-1][t])
path = []  # list of (seg_idx, [canonical indices])
t = t_end
for i in range(len(post_anchor), 0, -1):
    t_prev, m = bp[i][t]
    tape_positions = list(range(t - m + 1, t + 1))
    canon_indices = [p % n_canonical for p in tape_positions]
    path.append((i - 1, canon_indices))
    t = t_prev
path.reverse()

# Emit via existing timestamp-interpolation block (lines 734-744), one loop iteration per
# (seg_idx, canon_indices) in path.
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

2. **A/B comparison**: run once with `--snap-algo greedy` and once with `--snap-algo nw`. Diff outputs line-by-line. The only intended change is verified line 29 (`我要歌頌耶和華作為` → `我呼求時祢必應允我`).

3. **Diagnostic inspection**: open `tmp_output/diagnostic.md` after NW run. Confirm the 207–226s and 226–232s bridge segments both show assigned tape positions mapping to `canonical[10], canonical[11]` (expected tape positions: `24, 25` at layer 1, given `C=14`).

4. **Smoke test on a non-bridge song**: pick a second song from the catalog that the greedy algorithm already handles well, run with `--snap-algo nw`, confirm no regression. Penalties should be small enough that simple songs fall into the trivial monotonic path (layer 0, no skips).

5. **Penalty sensitivity**: sweep `--nw-skip-penalty` over `{0.05, 0.10, 0.15, 0.20, 0.25}` and `--nw-wrap-penalty` over `{0.0, 0.05, 0.10}` to confirm the chosen defaults sit in a stable region (output unchanged across at least the middle three values of each sweep).

Success criterion: `wo_yao` reaches 34/34 (100%) and the second smoke-test song stays at its current score.
