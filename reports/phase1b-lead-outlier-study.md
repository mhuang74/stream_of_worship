# Phase 1b — Lead-Time Separation Extraction: **NULL — No Complete Separation**

Date: 2026-10-09
Protocol: issue #252 (frozen pre-registration, published before measurement)
Harness: `lab/poc-scripts/phase1b_lead_outliers.py`; artifacts `lab/poc-scripts/eval/lrc_truth/phase1b/`
Inputs: Phase 1 caches (`phase1/*.words.json`, `phase1/*.base.json`, `phase1/bpm_cache.json`) consumed read-only; 4 new word caches written under `phase1b/`.
Supersedes the "drop signal 1 permanently" disposition of the Phase 1 gate failure (#245) — that disposition is now confirmed empirically.

## Verdict

**NULL.** 70 candidate cells were swept across the 7 frozen feature
families; **0 achieve complete separation** (best gap = +0.0000, and it is
degenerate: the `abs_s T=10.0s` cell where every song scores exactly 0).
Per the pre-committed null action, **signal 1 stays dropped**, the Phase 1
fallback (pinyin CER + VAD pre-filter, no auto-PASS) and the recommended
v2.1 spec amendment proceed unchanged, and this study is closed — no
further feature search.

## Population (final state)

| Song | Role | Audio | Matched/Sung |
|---|---|---|---|
| zhu_a__wo_yao_gen_sui_mi | positive | stem (cached) | 34/34 |
| hereforyou_62e79ae9 | positive | stem (cached) | 58/90 |
| xin_kao_mei_yi_ju_ying_xu | positive | stem (cached) | 31/31 |
| cong_zao_chen_dao_ye_wan | positive | stem (cached) | 36/36 |
| mei_hao_de_chuang_zao | positive | stem (cached) | 53/56 |
| shu_bu_jin_71fba0ce | timing negative (yt) | stem (cached) | 30/32 |
| wo_neng_gei_ni_shen_me | timing negative (yt) | stem (cached) | 42/42 |
| jing_bai_ye_su_b08227a2 | timing negative (yt) | stem (cached) | 32/39 |
| cang_shen_zhi_chu_39437ec0 | timing negative (yt) | stem (cached) | 29/29 |
| wo_jing_bai_mi__ye_su | expansion timing negative (yt) | **stem retry SUCCEEDED** (local run; word stream re-transcribed from clean stem) | 50/50 |
| na_me_shen_de_ke_mu | expansion timing negative (yt) | **stem retry SUCCEEDED** (local run; word stream re-transcribed from clean stem) | 34/37 |
| dan_dan_ai_mi_f6653864 | convenience (April known-bad) | stem retry FAILED again (HTTP 400); mixed audio | 0/17 — **unmeasurable** |
| ai_shi_wo_men_yong_gan | diagnostic (qwen3_asr) | stem (cached) | 44/44 |
| bu_ting_zan_mei_mi | diagnostic (manual_upload) | stem (cached) | 37/37 |

Primary fit: **5 positives vs 6 DB-truth timing negatives** — the ceiling the
protocol allowed (n=6 DB-truth + 1 convenience). The degradation ladder for
the two mvsep-400 songs resolved at rung (1): the user's local
`run_stem_cache.py --set review_queue --local` run (started 04:54, unrelated
to this study's mvsep retries) separated both songs before the study
re-measured them, so neither needed the quality-flagged mixed-audio fallback.
`dan_dan` failed separation again (HTTP 400) and, on mixed audio, ASR found
2 pinyin syllables against 17 repetitive lines → matched 0 lines → recorded
`measurable: false` and excluded (it never counted toward the fit anyway).

## Frozen-protocol compliance

- Feature list: exactly the 7 families of the issue (abs seconds, abs beats,
  relative MAD-z, song summaries, drift slope+curvature, union,
  verified-outlier); the harness raises on any undeclared family
  (`FEATURE_FAMILIES` guard, tested).
- Grids (pre-declared in the harness header): T_s ∈ {1,2,3,4,5,7,10} s,
  T_b ∈ {0.5,1,2,3,4,8} beats, k ∈ {3,4,5,6,8,10}, union T_s × k,
  verified anchor |lead|>3 s with verification cutoff ∈ {0.3,0.4,0.5}.
- Whole-line verification window (frozen before measurement):
  [line_time − 2.0 s, next_line_time + 2.0 s), whole-line token match on
  pinyin (CJK) / raw latin tokens (English), cutoff < 0.5 ⇒ verified outlier.
- Criterion: complete separation, non-zero gap — min(neg) > max(pos). The
  verdict cites the whole grid, not a best cell.
- Word caches are provenance-keyed (`<song_id>.words-<audio_stem>.json`);
  Phase 1's legacy `<song_id>.words.json` caches are trusted only from the
  Phase 1 directory (canonical clean-stem streams). A mixed-audio fallback
  stream can therefore never satisfy a stem-based re-measure — `wo_jing_bai`
  and `na_me_shen` were each re-transcribed from their clean stems after the
  separation retry succeeded, and their stale mixed-audio caches are retained
  under `words-audio.json` names for audit.
- Zero DB writes; Phase 1 directory read-only; the only writes are Phase 1b
  artifacts + 4 LRC fetches into the truth LRC cache (as the issue allows).

## Per-song values (the grid the verdict cites)

| Song | Role | \|med\| s | MAD | p10–p90 | late-frac | \|slope\| | f(\|lead\|>3s) | f(z>5) | ver-out(frac) |
|---|---|---|---|---|---|---|---|---|---|
| zhu_a | pos | 0.035 | 0.34 | 1.86 | 0.50 | 0.007 | 0.000 | 0.029 | 0.000 |
| hereforyou | pos | 0.685 | 0.47 | 4.50 | 0.16 | 0.014 | 0.103 | 0.103 | 0.056 |
| mei_hao | pos | 0.260 | 0.22 | 2.14 | 0.77 | 0.004 | 0.038 | 0.189 | 0.036 |
| xin_kao | pos | 0.320 | 0.32 | 3.60 | 0.81 | 0.012 | 0.065 | 0.097 | 0.032 |
| cong_zao | pos | 0.315 | 0.75 | 2.90 | 0.39 | 0.002 | 0.000 | 0.000 | 0.000 |
| shu_bu_jin | neg | 0.005 | 0.46 | 4.36 | 0.50 | 0.079 | 0.133 | 0.200 | 0.031 |
| wo_neng | neg | 0.325 | 0.68 | 2.77 | 0.29 | 0.004 | 0.024 | 0.024 | 0.024 |
| jing_bai | neg | 0.270 | 0.62 | 3.86 | 0.59 | 0.058 | 0.031 | 0.031 | 0.000 |
| cang_shen | neg | 0.630 | 0.30 | 1.43 | 0.79 | 0.006 | 0.000 | 0.000 | 0.000 |
| wo_jing_bai | neg | 0.585 | 0.23 | 1.33 | 0.88 | 0.008 | 0.000 | 0.000 | 0.000 |
| na_me_shen | neg | 0.285 | 0.39 | 1.59 | 0.56 | 0.014 | 0.000 | 0.029 | 0.000 |
| ai_shi (diag) | — | 0.045 | 1.67 | 5.58 | 0.52 | 0.030 | 0.182 | 0.023 | 0.091 |
| bu_ting (diag) | — | 0.470 | 0.21 | 1.51 | 0.89 | 0.019 | 0.000 | 0.054 | 0.000 |
| dan_dan (conv) | — | — | — | — | — | — | — | — | — |

Why the null is real, cell by cell:

- **Song-level summaries (3):** every summary is inside the positive range on
  at least one negative. `cang_shen` (a *bad* LRC) has the lowest MAD (0.30)
  and narrowest width (1.43) of all 11 measured songs — tighter than every
  positive — while `hereforyou` (a *good* LRC) has the widest (4.50) and
  lowest late-fraction (0.16) of all songs. |median| is dominated by
  `cang_shen` 0.630 > every positive max 0.685? No — 0.630 < 0.685, inside.
- **Line-level fractions (1, 2, 5):** the only negatives with any
  outlier lines (`shu_bu_jin` 0.133 @T=3s, `jing_bai` 0.031, `wo_neng`
  0.024) are matched or exceeded by positives (`hereforyou` 0.103,
  `mei_hao` 0.038, `xin_kao` 0.065). At the most permissive anchor (T=10 s)
  exactly one negative has any line beyond ±10 s (`jing_bai` 1/32) — but the
  gap is 0 only because every positive scores 0 and one negative scores
  0.031… which is why the cell shows gap +0.0000: `min(neg)=0.0` because the
  other 5 negatives score 0. **No negative exceeds all positives anywhere.**
- **Drift (4):** |slope| best gap −0.0106 (positives reach 0.0142 via
  `hereforyou`, a good LRC, while 4 of 6 negatives sit below it);
  |curvature| best gap −0.0038.
- **Verified outliers (6):** verification *reduced* the negative signal
  without removing positive outliers: verified-outlier fractions sweep —
  positives up to 0.044 (vt=0.3/0.4, `hereforyou`) / 0.056 (vt=0.5),
  negatives up to 0.000 (vt=0.3/0.4) / 0.031 (vt=0.5, `shu_bu_jin`). Gaps:
  vt=0.3 −0.0444, vt=0.4 −0.0444, vt=0.5 −0.0556.

## The central validity threat: matcher-flip vs bad line

Whole-line verification works as designed but does not manufacture
separation. Raw |lead|>3 s outliers per song: positives carry them too
(`hereforyou` 6 lines, `mei_hao` 2, `xin_kao` 2) — the Phase 1 finding that
good LRCs produce 1–4 large-|lead| outlier lines via matcher flips on
repeated phrases holds on the bigger positive sample. Verification
suppresses flips only partially: 5 of `hereforyou`'s 6 raw outliers
survived as verified (e.g. li=33 "We are here for You" @173.7 s — the sung
instance at 179.9–181.1 s falls inside the narrow [171.7, 181.4) window, so
the flip is invisible to window-based verification), while on positives
with clean CJK streams half the raw outliers are correctly suppressed
(`xin_kao` li=19 flipped into the next repetition while the correct words
sit in-window at 133.2–136.8 → whole-line fraction 1.0 → not verified; its
li=25 at frac 0.31 survives).

On the negative side, `jing_bai` li=35 (+13.95 s) — a genuinely misplaced
line, words sung at 311–319 s against a 297.4 s timestamp — is verified as
real, but the same feature gives `hereforyou` 5 verified outliers, so the
discriminator cannot separate. **If window-based whole-line verification
cannot tell them apart, line-level extraction is unvalidatable — that is
itself a finding, as the issue anticipated.**

## The pre-registered honesty check: the statistical twin

`wo_neng_gei_ni_shen_me` (bad) vs `cong_zao_chen_dao_ye_wan` (good) remain a
statistical twin on every frozen feature: |median| 0.325 vs 0.315, MAD 0.68
vs 0.745, p10–p90 2.771 vs 2.899, late-frac 0.286 vs 0.389, |slope| 0.0041
vs 0.0020, |curvature| 0.0018 vs 0.0039. On drift curvature the *negative*
is tighter than the positive. As recorded up front in the issue: when this
pair does not separate on the frozen features, the null is real, not a
feature-engineering shortfall.

## Diagnostics (excluded from fit, tabulated per the issue)

- `ai_shi` (qwen3_asr content negative): the highest raw outlier fraction of
  all songs (f(|lead|>3s)=0.182) — its failure is content, and its timing
  noise is the largest, but whole-line verification cuts it to 0.091, and it
  was never eligible to move thresholds. If anything, this confirms timing
  features are not specific to the timing-failure mode.
- `bu_ting` (manual_upload anomaly): flat timing (MAD 0.21, zero raw
  outliers) — invisible to any lead-time rule, as Phase 1 already showed.
- `dan_dan` (April known-bad, convenience): unmeasurable on mixed audio
  (0/17 matched); reported, not fitted.

## Phase 5 costing implication (either way recorded)

The null keeps the content-only fallback's cost shape: the UNVERIFIED bucket
(no automated PASS) is the price of Phase 1's failure, and no timing signal
shrinks it. A revived timing FAIL-signal would only have removed songs whose
failure is *timing-attributed AND line-level-outlier-shaped* — the study
shows the two seed songs with that shape (`shu_bu_jin`, `jing_bai`) are
indistinguishable from good songs on every extracted condition, so the
bucket reduction would have been marginal while the false-FAIL cost on good
songs (`hereforyou` 5 verified outliers) is real.

## Reproduction

```
uv run --project lab/poc-scripts --extra lrc_eval python lab/poc-scripts/phase1b_lead_outliers.py measure
uv run --project lab/poc-scripts --extra lrc_eval python lab/poc-scripts/phase1b_lead_outliers.py analyze
```

Second `measure` run performs zero transcriptions (word-cache hits only) and
reproduces identical record bodies — verified over the **final** 14-song
population by deleting every per-song JSON and diffing against a snapshot:
`population: 14 | re-transcriptions: 0 | record diffs: none`
(`tests/test_phase1b_lead_outliers.py`, 14 tests).

## Tracker consequences

- #245: closes as superseded — gate ran, FAIL recorded, and the signal-1
  disposition is now decided empirically by this study (null).
- #248: its "timing signal stays dropped" acceptance criterion **stands**
  (null verdict); no timing signal re-enters the bake-off.
- The v2.1 spec amendment per `reports/lrc-triage-next-steps-after-phase1-gate.md`
  proceeds unchanged (out of scope here).
