# Fix extraction + redesign `canonical_line_snap` in gen_lrc_qwen3_asr_local.py

## Context

Two related problems surfaced running the POC on `wo_yao_yi_xin_cheng_xie_mi_247`:

### 1. Extraction bug (already fixed in working copy)

`mlx_qwen3_asr.Session.transcribe()` returns a `TranscriptionResult` dataclass with `.segments: list[dict]`. The previous `extract_segments()` used `getattr(seg, "text", "")` which returns `""` for dicts, marking every segment empty → "No valid text segments extracted". The current `extract_segments` in the working copy (lines 101–225) is correct: it reconstructs phrase-level segments from the top-level `result.text` by splitting on Chinese/ASCII punctuation and consuming per-character segments in parallel. That fix stays.

### 2. `canonical_line_snap` quality problems (the focus of this plan)

`tmp_output/diagnostic.md` shows 15 of 42 ASR phrases kept their raw (uncorrected) text, and several others got incorrect snaps. The reviewable patterns:

- **Fragmented ASR phrases** (ASR emits two phrases where canonical has one):
  - `在主身边` (0.17) + `谦恭颂你` (0.17) → should both snap to `在諸神面前歌頌祢`
  - `我不求是你必应允我` + `不离我` (0.29) → should snap to `我呼求時祢必應允我`; `不离我` is leftover noise
  - `鼓励我` (0.46) + `使我心里有能力` (0.82) → both fragments of `鼓勵我使我心裡有能力`
  - `我要歌颂` + `也把我作为` → the second fragment is garbled ASR of the same canonical line
- **Opening drift**: first content line `我要一心奉献你` scored 0.57 (< 0.60 threshold) against `我要一心稱謝祢`; not replaced. Song openings do not repeat, so this must be forced.
- **Short-fragment score collapse**: short fragments like `鼓励我`, `你凝视`, `不离我`, `你将我浇灌` receive very low `token_set_ratio` because the metric normalizes against both sides. They should be scored with a substring-aware metric or merged with a neighbor first.
- **Over-replacement by repeated canonical**: `鼓励我` matches canonical `鼓勵我使我心裡有能力` twice in sequence; the downstream merge-and-dedup should recognize consecutive fragments of the same canonical line and emit only one LRC row with the earlier timestamp.

### Decisions from user clarification

- Fragment handling: **Both** — merge adjacent ASR phrases first and re-score; after final scoring, dedup consecutive ASR segments that snapped to the same canonical line (keep the earlier timestamp).
- Canonical order: **Sequential greedy walk** with a forward window (next 3–5 canonical lines) and allow return-to-start (chorus repeat) only if the local score is very high (≥ 0.85).
- Opening: **Force-snap first 1–2 content ASR segments** to canonical lines [0] and [1] unconditionally (after skipping pure-filler segments like `嗯`).
- Short fragments: segments with ≤ 3 Chinese chars score with `rapidfuzz.fuzz.partial_ratio` (substring match) against the candidate canonical window instead of `token_set_ratio`.

## Recommended change

All changes are inside `poc/gen_lrc_qwen3_asr_local.py`. No other files, no tests (POC has none).

### New/changed functions

1. **New `_is_filler(text: str) -> bool`** — returns True for interjection-only segments (contains only chars from `嗯啊呃哦唉`). Used to skip leading/trailing filler from the opening-anchor logic.

2. **New `_score(asr_text, canonical_line, target_script) -> float`** — wraps the script-normalization + metric selection. Uses `partial_ratio` if the normalized ASR text has ≤ 3 Chinese chars; `token_set_ratio` otherwise. All other call sites (including `write_diagnostic`) route through this helper so diagnostic scores match the snap scores.

3. **Rewrite `canonical_line_snap(segments, lyrics, threshold=0.60)`** to:

   **Step A — preprocess:**
   - Detect target script (existing logic).
   - Build normalized canonical lines (existing logic).

   **Step B — merge pass:**
   - Walk `segments` left-to-right producing `merged_segments`. For each position `i`, compute:
     - `score_i` = best score for `segments[i]` alone against any canonical line.
     - `score_merge` = best score for `segments[i].text + segments[i+1].text` (concatenated, no separator) against any canonical line.
   - If `score_i < threshold` AND `score_merge > score_i + 0.10` AND `score_merge >= threshold`, merge: emit `{"start": segments[i]["start"], "end": segments[i+1]["end"], "text": merged_text}` and advance by 2.
   - Otherwise keep `segments[i]` as-is and advance by 1.
   - Short-fragment rule: if `segments[i]` has ≤ 3 Chinese chars AND it's not already the last segment, *always* attempt the merge evaluation; prefer merge when `score_merge >= score_i`.

   **Step C — sequential greedy snap:**
   - Maintain `cursor` = index into canonical lines, starting at 0. Window = `canonical_lines[cursor : cursor+5]`.
   - For each merged segment, compute scores over the window **and** against all canonical lines globally.
   - Decision:
     - Pick best-in-window if its score ≥ `threshold`. Advance `cursor` to its canonical index + 1.
     - Else pick best global match if its score ≥ `0.85` (chorus-repeat threshold). Set `cursor` = its canonical index + 1 (allows wrapping back to earlier lines).
     - Else keep raw ASR text (not replaced).
   - Window size 5 and chorus threshold 0.85 are constants at the top of the function (named, not inlined).

   **Step D — opening anchor:**
   - Before Step C, identify the first 1–2 *content* segments (skip `_is_filler`).
   - Force-snap segment 1 → `canonical_lines[0]`, segment 2 → `canonical_lines[1]` (if canonical has ≥ 2 lines), mark as `replaced=True`, and pre-advance `cursor` to 2.
   - This overrides the threshold check for those two segments only.

   **Step E — consecutive-duplicate dedup:**
   - After Step C produces `(start, final_text, replaced)` tuples, walk the list and collapse runs where the same `final_text` appears in consecutive `replaced=True` entries — keep only the first (earliest start). Do *not* dedup `replaced=False` entries (raw ASR text that happens to repeat is usually legitimate repetition).

4. **`write_diagnostic`**: route its per-row scoring through the shared `_score` helper so the diagnostic reflects what snap actually used. Add a `Merged` column indicating whether the ASR row came from a Step-B merge, and an extra summary line `Merged segments: N` and `Deduped rows: M`.

### Tunable constants (top of the snap function)

```python
WINDOW_SIZE = 5                   # Step C forward window
CHORUS_REPEAT_THRESHOLD = 0.85    # Step C global match threshold
MERGE_GAIN = 0.10                 # Step B: merge must beat solo by this
SHORT_FRAG_CHARS = 3              # Step B/Score: <= this uses partial_ratio
OPENING_ANCHOR_COUNT = 2          # Step D: first N content segments forced
```

### Minimal scope

- Do not change `extract_segments` (already correct).
- Do not touch unrelated bugs: unreachable `typer.echo` after `raise typer.Exit(1)` at line 582–586, `valid_segments == 0` comparison on a list at line 279. Flag them if the user wants a separate cleanup pass.

## Critical files

- `poc/gen_lrc_qwen3_asr_local.py` — edits to `canonical_line_snap` (lines 350–400), `write_diagnostic` (lines 419–513), and new helpers (`_is_filler`, `_score`).
- `tmp_output/diagnostic.md` — reproduction artifact; verify against this.
- `tmp_output/out.txt` — current (bad) LRC output; regenerate and spot-check.

## Verification

1. Re-run:
   ```
   uv run --extra transcription python poc/gen_lrc_qwen3_asr_local.py \
     wo_yao_yi_xin_cheng_xie_mi_247 \
     --save-raw ./tmp_output -o ./tmp_output/out.txt \
     --no-lyrics-context --force-rerun
   ```
   (`--force-rerun` isn't strictly needed once `extract_segments` is already correct and cached, but it re-exercises the full path.)

2. In `tmp_output/diagnostic.md` expect:
   - **Opening**: `我要一心奉献你` (17.17s) snaps to `我要一心稱謝祢`, `Replaced = Yes` (force-anchored even below threshold).
   - **Fragment merge**: `在主身边` + `谦恭颂你` become one merged row at 50.94s that snaps to `在諸神面前歌頌祢` with a high score; two separate low-scored rows disappear.
   - Similar merges for `鼓励我 + 使我心里有能力` (→ `鼓勵我使我心裡有能力`) and the `我不求是你必应允我 + 不离我` pair.
   - **Sequential walk**: the verse-vs-chorus matches align with song structure; no more spurious `我要一心稱謝祢` on `你凝视`.
   - **Dedup**: no two consecutive LRC rows with identical canonical text.

3. `tmp_output/out.txt` should have roughly 30–35 lines (down from 42), no obvious duplicates, and every non-filler line timestamp increases monotonically.

4. Sanity-run one more song to check the changes don't regress a well-behaved case — pick any other song with a cached transcription or a fresh run.

## Risks / edge cases to review

- **Two-line song openings where canonical[0] is a title line**: force-anchor to canonical[0] may attach a chorus line to a title. If this shows up in another song, consider making `OPENING_ANCHOR_COUNT` configurable via CLI.
- **Partial_ratio false positives**: a short fragment `我` would partial-match almost any canonical line. Guard: require `score_merge > score_partial + 0.05` before a short-frag merge wins; otherwise keep partial-ratio replacement.
- **Canonical chorus appearing 3+ times**: sequential walk with window 5 is fine for adjacent repeats; the chorus-repeat threshold 0.85 handles jumps. If a song has a bridge between choruses, window 5 may be too small — revisit if a real song fails.
- **Dedup on legitimately repeated canonical lines**: intentional repeats (some worship songs repeat the same line twice for emphasis) would collapse into one. Since we only dedup *consecutive* identical snaps and only when both are `replaced=True`, this should mostly match singer's actual repeat behavior; flag if a test song loses a real repeat.
