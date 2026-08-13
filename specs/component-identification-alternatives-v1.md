# Spec v1: Alternative Designs for Chorus/Verse Identification from LRC

> Goal: propose alternative designs for identifying chorus & verse components from LRC lyrics, since the v1 weight-tuning loop (`specs/component-identification-tuning-loop-v1.md`) plateaued at **grand-total IoU ≈ 0.286** on the 3-song fixture set and the grid search found **no meaningful win**. The deficit is structural, not weight-tunable.
>
> This document is a **design comparison only** — no implementation. The user will pick one design to detail in a follow-up implementation plan.

## Why the tuning loop stalled (root causes)

The v1 weight knobs only moved the score 0.120 → 0.286. The remaining error is dominated by three structural defects of `identify_from_lyrics_repetition` (`ops/analysis-service/src/sow_analysis/workers/components.py:854`), none of which any combination of the four weights can fix:

1. **Zero `verse`/`loop_target` components on all 3 songs (verse IoU = 0.0).** The verse is synthesized by walking backward from the *first chorus occurrence* (`components.py:1089-1119`), but the winning chorus candidate's first occurrence always starts at **line 1 / index 0** (the merged block absorbs the verse), so `first_chorus_start_idx > 0` is `False` and no verse is emitted. This single defect costs ≈ a third of achievable IoU per song.

2. **Over-merged chorus windows.** The repeated-sequence signature greedily joins verse + chorus into one giant block (e.g. `jun_wang` entry = 13 lines beginning with the *verse* 聖潔耶穌/哈利路亞; `zhu_a` entry = 13 lines beginning with the *verse* 祢的話在我心). The predicted time range only partially covers the true chorus, capping IoU near 0.25–0.5 regardless of scoring.

3. **Entry/exit role misalignment.** With only two essential chorus rows derived by pure occurrence order, the algorithm labels the ground-truth *entry* (e.g. `yi_sheng` lines 18–21) as `exit` → −0.10 role-mismatch penalty.

**They can't be fixed by**: window-size tuning (explicitly frozen in v1 out-of-scope), keyword list tweaks (frozen), or the repeat-count cap (already swept). A different identification strategy is required.

## Locked decisions (user-confirmed constraints)

1. **LLM stack** — Reuse the existing OpenAI-compatible stack already wired into the analysis-service: `openai` SDK, `SOW_LLM_API_KEY` / `SOW_LLM_BASE_URL` / `SOW_LLM_MODEL` env vars, `call_llm_with_retry` rate-limit wrapper (`llm_rate_limit.py`), `response_format={"type":"json_object"}`. **No** new dependencies (`anthropic`, `litellm`, `instructor`). Provider-agnostic via `base_url`.
2. **LLM cost/latency** — No hard constraint; prioritize accuracy. But each design states its LLM-call count per song so cost is visible.
3. **allin1 sections** — Treat as **unreliable** (user says labels are often wrong). A new design may consume allin1 sections as a *weak prior* but must not depend on their correctness. The allin1 path is treated as one optional input, not ground truth.
4. **Output contract** — Open to **schema evolution**. The designs below may extend `ComponentInstance` (`components.py:223`) with new optional fields (e.g. `section_label`, `lyrics_excerpt`, `llm_rationale`). New fields default to `None` so existing consumers and the persisted `components.json` stay backward-compatible. `COMPONENT_SCHEMA_VERSION` bump is in scope for the chosen design.
5. **Deterministic fallback** — Each design retains a no-LLM path (the existing `identify_from_lyrics_repetition` promoted to fallback) so deployments without `SOW_LLM_API_KEY` keep working at the current 0.286 baseline.

## Shared building blocks (used by all designs)

All three designs reuse the same scaffolding, so implementation cost is mostly paid once:

### S1. LLM client & call helper
A new module `ops/analysis-service/src/sow_analysis/workers/section_segmenter.py` (or extend `components.py`) hosting the LLM segmentation logic. It constructs an `OpenAI(api_key=settings.SOW_LLM_API_KEY, base_url=settings.SOW_LLM_BASE_URL, ...)` client **identical in shape to `ThemeClassifier._client`** (`classifier.py:214-234`) and wraps synchronous calls through `call_llm_with_retry`. A new setting `SOW_LLM_SEGMENTATION_TIMEOUT_SECONDS` (default `60.0`) and a feature flag `SOW_COMPONENTS_USE_LLM_SEGMENTATION` (default off) gate the path.

### S2. Numbered-LRC prompt input
The LRC is rendered into a numbered text block so the model returns **line ranges** instead of timestamps (timestamps are easy to mis-transcribe; line numbers are integers the post-processor maps to `LRCLine.time_seconds`):

```
1  [00:33.32] 聖潔耶穌 祢寶座在這裡
2  [00:47.89] 哈利路亞 祢榮耀在這裡
3  [01:02.33] 聖潔耶穌 祢寶座在這裡
4  [01:16.64] 哈利路亞 祢榮耀在這裡
5  [01:29.34] 君王就在這裡 我們歡然獻祭
...
```

Line numbering follows the **1-based raw-LRC convention already used by the tuning fixtures** (`specs/component-identification-tuning-loop-v1.md` "Line indexing convention") — every physical line counts, blanks included. This lets the existing `_expected_time_range` scorer be reused unchanged.

### S3. JSON response schema (designs A & B)
```json
{
  "sections": [
    {"label": "verse",   "line_start": 1,  "line_end": 4,  "confidence": 0.9, "rationale": "..."},
    {"label": "chorus",  "line_start": 5,  "line_end": 8,  "confidence": 0.95, "rationale": "..."},
    {"label": "verse",   "line_start": 10, "line_end": 11, "confidence": 0.8,  "rationale": "..."}
  ]
}
```
`label ∈ {intro, verse, prechorus, chorus, bridge, outro, instrumental}`. Ranges must be non-overlapping, cover all non-blank lines, and be in-order. The prompt embeds 2–3 few-shot examples drawn from the existing fixture songs.

A separate `_parse_segmenter_json` helper reuses `ThemeClassifier._parse_llm_json`'s defensive parsing (`classifier.py:515`) and validates constraints (non-overlapping, in-file bounds, valid labels). On any violation → fall back to the deterministic path.

### S4. Section-to-ComponentInstance mapper (deterministic, shared by A & B)
Once sections are labelled, the mapper is pure Python (no LLM) and mirrors `identify_from_allin1_sections` (`components.py:735-851`) logic:
- Collect `chorus`-labelled sections in order → `occurrence_index = 1..N`.
- `occurrence_index == 1` → `role="entry"`; last → `role="exit"`; middle → `role="none"`.
- **Single chorus (v3):** emit two rows `(occ, "entry")` + `(occ, "exit")` with identical times — bit-compatible with the existing contract.
- **Verse / `loop_target`:** take the **last `verse`-labelled section that ends at or before the first chorus starts** → `occurrence_index=1, role="loop_target"`. This directly fixes Root Cause #1 (no verse) because the LLM labels verse independently of where the chorus candidate starts.
- Each emitted `ComponentInstance` gets a **new optional field `section_label: Optional[str]`** (one of the 7 labels above) and `lyrics_excerpt: Optional[str]` (the joined lines), both defaulting `None` so existing rows / persisted JSON are unchanged.
- `source = "llm_segmentation"` (new string); `confidence = section.confidence * 0.95`.
- Beat/downbeat snapping reused from the existing `_snap_to_beat` / `_snap_to_downbeat` helpers.

### S5. Deterministic fallback (shared)
When `SOW_COMPONENTS_USE_LLM_SEGMENTATION` is off, `SOW_LLM_API_KEY` is unset, the LLM call raises, or JSON validation fails, `extract_components` falls through to the existing `identify_from_lyrics_repetition` / `identify_from_allin1_sections` ordering (`components.py:1419-1466`). Net result for no-LLM deployments = today's 0.286 baseline.

---

## Design A — LLM whole-song segmentation (single call, full structure)

**One LLM call per song.** Feed the numbered LRC (S2) + minimal song-level metadata (title, duration) and ask for a complete structural segmentation (S3). The mapper (S4) then emits `ComponentInstance`s. No repetition clustering involved.

```
LRC → [1 LLM call: segment into labelled sections] → S4 mapper → ComponentInstance[]
                                   (on failure) → S5 fallback (existing path)
```

**Why it fixes the root causes:**
- *Zero verse (#1):* the LLM labels the 聖潔耶穌/哈利路亞 block as `verse` independent of where any repeated block starts; S4 picks it as the pre-chorus verse → `loop_target`. Verse IoU stops being 0.
- *Over-merged windows (#2):* the LLM semantically separates verse from chorus (they're different lyric content) rather than joining them into one repeated signature block → chorus boundaries align with the true chorus lines (5–8, 45–48, …).
- *Role misalignment (#3):* roles are derived in S4 from **ordered chorus occurrences**, which matches the ground-truth definition the scorer uses, so entry/exit line up.

**Pros**
- Simplest mental model; one call, one JSON, one mapper.
- Best expected IoU because the LLM uses lyric *semantics* + song-structure priors that the repetition heuristic lacks.
- Generalizes to songs with non-repeated choruses (a known failure mode of repetition-based detection).

**Cons / risks**
- **Hallucinated line ranges.** Mitigated by: (a) numbered input so answers are integers, not timestamps; (b) bounds + non-overlap validation in S3; (c) few-shot examples; (d) fallback to S5. A row whose range is OOB or overlaps is dropped; if >30% of lines are uncovered the whole result is rejected → fallback.
- **Single point of failure / cost variance.** One call ≈ 1.5–4k prompt tokens + 0.5–2k completion tokens per song. Acceptable per stated "no strong constraint".
- **No cross-check.** The LLM's segmentation is trusted unless it fails JSON validation. Design C adds a cross-check if A's accuracy is insufficient.
- **Token growth on very long LRC.** Cap at ~150 lines (rare for worship songs); truncate middle with a marker if exceeded and validate coverage.

**LLM calls per song:** 1.
**Estimated new code:** `section_segmenter.py` (~250 lines) + S4 mapper (~80 lines) + prompt + tests. Existing `identify_from_lyrics_repetition` kept as fallback (unchanged).

---

## Design B — LLM as chorus/verse selector over repetition candidates

**Keep the repetition clustering; replace the single-best scoring pick with an LLM judge.** Run the existing candidate discovery (`components.py:894-987`) but instead of `scored_candidates.sort(...)` and taking `[0]`, emit the **top-K candidate blocks** (e.g. K=5, ranked by current `final_score`), render each candidate's joined lyrics as a numbered/multiple-choice list, and ask one LLM call: *"which candidate is the chorus? which preceding block is the verse?"*

```
LRC → repetition clustering → top-K candidate blocks
                                  → [1 LLM call: pick chorus + verse from candidates] → build ComponentInstance[]
                                   (on failure / <2 candidates) → S5 fallback
```

**Why it could help — and why it's the weakest of the three:**
- Helps with **role misalignment (#3)** only if the LLM also re-orders occurrences. Marginal.
- Does **not** reliably fix **over-merged windows (#2)**: if the chorus candidate is itself the over-merged 13-line block, the LLM is picking *among* over-wide options — there may be no clean chorus candidate in the top-K. (The clustering window size sweep could produce a tighter candidate, but window range is frozen in v1.)
- Does **not** reliably fix **zero verse (#1)**: the verse-walk-back bug in `components.py:1089` still fires when the chosen chorus candidate starts at line 1. Would need an additional change to also ask the LLM for the verse line range — at which point this collapses toward Design A.

**Pros**
- Cheapest conceptual change: reuses the clustering pipeline; only the *picker* changes.
- More bounded LLM risk (candidates are real repeated blocks, not free-form).

**Cons**
- Inherited structural defects: still bounded by what repetition clustering discovers.
- Adds complexity without clearly fixing the root causes.
- Barely cheaper than Design A (still 1 LLM call/song) for less expected accuracy.

**LLM calls per song:** 1.
**Recommendation:** consider only if the user wants to preserve the existing pipeline's character; otherwise Design A dominates it.

---

## Design C — Two-pass LLM with repetition cross-check (highest-accuracy / most tokens)

**Design A + a deterministic validation/cross-check pass using the existing repetition signal.** First LLM call segments the whole song (identical to Design A). Then a **non-LLM validator** runs the current repetition clustering over the LRC *within each LLM-labelled `chorus` section* to (a) confirm the section's lyrics actually repeat elsewhere in the song, and (b) tighten the `line_end` of each chorus section to the last line whose text repeats (fixing the over-merge from the *chorus side*). Sections that fail repetition validation are flagged with reduced `confidence` but **kept** (a non-repeated chorus is musically valid, e.g. an outro chorus).

```
LRC → [LLM call 1: segment (Design A)] → S4 mapper → raw ComponentInstance[]
       + repetition validation pass (no LLM, tightens chorus boundaries, sets confidence)
       (on LLM failure) → S5 fallback
```

Optionally a **second LLM call** presents the *validated* section list back to the LLM for a yes/no sanity check ("is this segmentation correct?"); on "no" with rationale, run a corrective third call. This is opt-in and off by default — only enable if Design A's measured IoU is still below target.

**Why it exists:** it addresses Design A's "no cross-check" risk explicitly. The repetition signal, while insufficient on its own, is a strong *validator* of chorus identity (choruses repeat by definition).

**Pros**
- Highest expected accuracy: LLM semantics + repetition validation synergize.
- Over-merge fixed from both sides (LLM doesn't merge; validator trims trailing verse lines if the LLM's `line_end` overshoots).
- Graceful degradation: validator only adjusts confidence/boundaries, never removes data.

**Cons / costs**
- Up to 2–3× tokens of Design A if the optional sanity-check call is enabled.
- More moving parts (validator logic, confidence-blending formula) → more to tune/overfit on 3 fixtures.
- The validator must guard against the *original* over-merge (don't let it re-introduce the 13-line block when trimming).

**LLM calls per song:** 1 (default), up to 2–3 (opt-in sanity check).
**Recommendation:** choose this if Design A's measured IoU on the 3 fixtures is below ~0.75.

---

## Comparison matrix

| Dimension | Design A (single LLM) | Design B (LLM picks among candidates) | Design C (LLM + repetition cross-check) |
|---|---|---|---|
| Fixes zero-verse (#1) | ✅ yes (separate verse label) | ❌ no (walk-back bug remains) | ✅ yes |
| Fixes over-merge (#2) | ✅ likely | ⚠️ partial (bounded by candidates) | ✅ yes (validator trims) |
| Fixes role misalignment (#3) | ✅ yes (ordered occurrences) | ⚠️ partial | ✅ yes |
| LLM calls / song | 1 | 1 | 1 (default), ≤3 (opt-in) |
| Tokens / song (est.) | 2–6k | 2–5k | 4–12k |
| New code | ~330 lines | ~150 lines (smallest) | ~450 lines |
| Keeps existing pipeline | as fallback only | as the candidate source | as validator + fallback |
| Handles non-repeated chorus | ✅ yes | ❌ no | ✅ yes |
| Overfit risk on 3 fixtures | medium | low | high (more knobs) |
| Expected IoU (qualitative) | high | low–medium | highest |

## Recommended default

**Design A**, unless its measured IoU falls short: it directly addresses all three root causes with one LLM call, reuses the existing stack, and keeps a clean deterministic fallback. Design C is the escalation path (add the repetition validator) if A underperforms. Design B is documented for completeness but is not recommended.

## Evaluation (reuses the v1 tuning harness — no new eval infra)

 whichever design is chosen is scored with the **existing** harness at `ops/analysis-service/tests/test_components_tuning.py` + `eval/components_tuning/`, with one extension: the scorer must accept the new `source="llm_segmentation"` value (it currently partitions only by `component_type`, so this is a no-op for IoU; only the `n_predicted_choruses` count and role bonus logic need to tolerate the new source string).

Success criteria (gating, decided before implementation):
- **Pass bar:** grand-total IoU ≥ **0.70** on the 3 fixtures (vs current 0.286).
- **Stretch:** ≥ **0.85** per-song mean on all 3.
- **No regression:** no-LLM fallback still produces the existing 0.286 baseline (asserted by a new test that runs with `SOW_COMPONENTS_USE_LLM_SEGMENTATION=false`).
- The 3 fixtures are too few to claim generalization; a follow-up eval on 10 additional songs (ground-truth labelled the same way) should be planned but is **out of scope** for this spec.

Add a `--use-llm-segmentation` flag to the COMPONENT ANALYSIS job options so the operator can A/B the old vs new path per song.

## Open questions (resolve before detailing the chosen design)

1. **Schema version bump.** Adding `section_label` + `lyrics_excerpt` (and the new `source` string) requires bumping `COMPONENT_SCHEMA_VERSION` (`storage/cache.py`). Confirm consumer compatibility (admin-cli, webapp, render-worker) — these all read `components.json` defensively, but a bump forces a re-compute. Decide: bump now vs keep fields un-versioned (risk: stale cached components won't have the new fields until `--force`).
2. **Model choice.** `SOW_LLM_MODEL` is shared with the theme classifier (cheap models like `gpt-4o-mini` / qwen). Segmentation may need a stronger model (e.g. `gpt-4o`) for accurate Chinese lyric structure. Add a separate `SOW_LLM_SEGMENTATION_MODEL` override (default falls back to `SOW_LLM_MODEL`)?
3. **Few-shot source.** Few-shot examples drawn from the 3 fixture songs risk leaking the test set. Option: hand-write 2 examples from **other** worship songs (manually segmented) to keep fixtures as held-out eval.
4. **Sampling temperature.** Segmentation wants deterministic output → `temperature=0`. Confirm the provider honours it (OpenRouter routes vary).

## Out of scope

- Implementing any design (this doc is design-only).
- Re-tuning the theme/vocal-posture `ThemeClassifier` (`classifier.py`) — orthogonal; runs downstream of identification.
- The allin1-sections path beyond treating it as a weak optional prior.
- Expanding the 3-song fixture set (planned as separate follow-up eval).
- Persisting LLM rationale to R2/DB beyond the existing `components.json` fields.
