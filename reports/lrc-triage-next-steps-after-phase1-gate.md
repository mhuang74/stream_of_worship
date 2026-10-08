# LRC Review Triage — Recommended Next Steps After the Phase 1 Gate Failure

Date: 2026-10-09
Inputs: `reports/phase1-onset-lead-gate.md` (Phase 1 result),
`specs/lrc-review-triage-cascade-design-v2.md` (design under amendment),
`lab/poc-scripts/eval/lrc_truth/phase1/analysis.json` (artifacts).
Issue: #245

## Where the design stands

The spec's Phase 1 fallback clause is now operative:

> **Fallback if the gate fails**: drop signal 1; the checker becomes pinyin
> CER + VAD pre-filter only. The PASS bucket is then not automatable … the
> report degenerates to a REGEN-probe/MANUAL split with no auto-PASS, and the
> Success Criteria are restated accordingly (precision/recall apply to FAIL
> detection only).

Both gate criteria failed on the 10-song set:

- **Separation FAIL**: timing-negative per-line lead median +0.10 s inside the
  positive band [−1.07, +1.76] s; IQRs overlap.
- **Recoverability FAIL**: +1.0 s injection recovered within ±0.25 s on ≥90%
  of lines for only 3/10 songs (4/10 permissive denominator; gate needs 8/10).

Two supporting observations sharpen the decision:

1. **The positives do not match the spec's prior.** Seed-positive lead
   medians span −0.32 … +0.68 s (≈ 0 to −1 beat at 60–90 BPM), not the
   [1, 3]-beat early-lead prior. Either good LRCs in this catalog are
   timestamped at/near vocal onset, or ASR onsets lag true onsets. The
   [1, 3]-beat PASS criterion would fail most *good* songs.
2. **Dispersion is also non-discriminative.** Per-song lead stdev overlaps
   completely (positives 0.69–1.92 s; timing negatives 1.18–2.73 s, but the
   negative `wo_neng_gei_ni_shen_me` sits at 1.18 s — inside the positive
   range). Even a per-song spread signal would misclassify.

One spec allowance was consciously not exercised: the single retry with the
Phase 2 bake-off winner. Rationale: the failure mode is line-level alignment
brittleness (first-word matcher flips under a global shift) plus zero
separation on the base measurement — an engine swap addresses neither, and
the ≈0 s lead finding is an engine-independent measurement conflict between
ASR word timestamps and editor-made LRC timestamps.

## Recommended next steps, in order

### 1. Amend the spec (v2.1) to codify the fallback — small, do first

Record in `specs/lrc-review-triage-cascade-design-v2.md`:

- Phase 1 outcome: gate FAIL, fallback adopted, pointer to the gate report.
- Signal list change: signal 1 (onset-lead) dropped; checker = pinyin CER +
  VAD pre-filter. The per-line pinyin CER spec (signal 2) is unchanged and
  unaffected by the gate failure.
- Success-criteria rewrite per the fallback clause: no auto-PASS bucket;
  precision/recall apply to FAIL detection; the report buckets become
  **FAIL** (checker-FAIL, then probe-split into REGEN vs MANUAL) and
  **UNVERIFIED** (checker-ambiguous songs that would previously have been
  PASS candidates — they now need a human listen to be published).
- The Phase 3 items that exist only to serve signal 1 (onset-lead
  distribution measurement, PASS-window confirmation) are dropped; the
  synthetic corruption sweep keeps only content corruptions (dropped lines,
  duplicated lines, mismatched lines) — timing corruptions (uniform offsets,
  drift, jitter) are meaningless for a content-only checker.

Why first: every later phase's cost estimate and success criterion depends on
which signals exist. This is a documentation change, zero runtime.

### 2. Re-scope Phase 2 to a content-only engine bake-off, keep it small

Phase 2 survives with amended purpose: pick the ASR engine that maximizes
pinyin-CER separation on the same 10-song set (5 positives + 5 negatives).
Whisper large-v3 vs SenseVoice remains the right pair; the whisper_asr
independence caveat still matters only for future sweeps, not today's queue
(0 whisper_asr songs in review). Expected effort is small — the Phase 1
harness already caches per-song ASR output; SenseVoice is the only new
integration.

Note for the bake-off: report CER on the **line spans the LRC claims**
(lrc-mode spans) vs the **VAD-detected vocal spans** separately. A drifted
LRC paired against its own claimed span is exactly the case the checker must
catch, and the April experiment's circularity caveat (fact 7) applies.

### 3. Build the pinyin-CER checker against the Phase 1 word caches

The per-song word streams (`eval/lrc_truth/phase1/*.words.json`, 10 songs)
are already the substrate signal 2 needs. Next step is a small script
(`phase2_cer.py` or an `eval_lrc.py` extension) that:

- aligns LRC lines to the ASR word stream (the existing homophone-tolerant
  aligner is prior art and already debugged in Phase 1);
- computes per-line and per-song CER, homophone-normalized;
- tolerates ADR-0008 placeholder lines and line splits/merges;
- reports per-line attribution (missing / duplicated / mismatched) to support
  the "failure-mode matches known defect" success criterion.

Validation target on the existing artifacts: the qwen3_asr negative
(`ai_shi_wo_men_yong_gan`, missing/duplicated lines) and the three
youtube_transcript negatives must CER-FAIL; the five positives must
CER-PASS. If positives and negatives don't separate on CER either, that is
the earliest possible signal to stop before Phase 4 spend.

### 4. Re-verify the two excluded negatives before Phase 3

The Phase 1 separation test only ever had three timing negatives. The other
two negatives (`bu_ting_zan_mei_mi` manual_upload, `ai_shi_wo_men_yong_gan`
qwen3_asr) were excluded from the timing test but are in scope for the
content checker. Before Phase 3 calibration, confirm from the maintainer's
feedback threads what makes each a negative (the manual_upload one was
flagged `anomaly` in Phase 0 — its failure mode may not be machine-checkable
at all). If its failure mode is not content-checkable, it must be excluded
from the content checker's must-FAIL set too, with a note — otherwise it
will permanently depress measured recall.

### 5. Phase 3+4 as amended: calibration on content signals, then the 399 run

- Re-query the negative list (`lyrics feedback list --rating poor`) before
  any measurement — the spec requires this and new feedback joins must-FAIL.
- Synthetic corruption sweep: content corruptions only (see step 1).
- Freeze thresholds → run all 399 review songs with the transcript probe on
  every checker-FAIL song (paced ≥2 s, one retry, cached per video_id).
- Deliverable: per-song JSON reports + console summary; buckets
  REGEN / MANUAL / UNVERIFIED. No auto-PASS, no writes.

### 6. Phase 5 costing with the degraded report shape

The regen batch (FAIL ∩ transcript-retrievable) and manual queue (FAIL ∩
no-transcript + regen-recheck failures) are computed as the spec says, but
the UNVERIFIED bucket is new: songs the checker could not fail but which no
longer have an automated PASS path. Estimate the human listen cost for that
bucket explicitly — it is the price of the Phase 1 failure and belongs in
the Phase 5 memo so the maintainer can weigh "run the content-only cascade"
vs "keep hand-triaging".

### 7. Park, don't delete, the timing signal

The onset-lead estimator is not worthless: recovered medians were exact on
20/20 song×offset runs — as a *global* offset estimator it works. What
failed is per-line classification. Two cheap future probes, explicitly not
recommended now:

- Hand-label first-word onsets for ~3 songs (Audacity/WaveSurfer) to settle
  whether ASR onsets or LRC timestamps are biased. If editor timestamps are
  systematically ≈0-lead by design, the [1,3]-beat prior was wrong, not the
  signal — but per-line brittleness still blocks automated use, so this
  changes documentation, not the pipeline.
- A song-level (not line-level) uniform-offset probe could still serve as a
  gross-drift flag (report-only, no PASS). Only worth revisiting if the
  content-only cascade leaves too large an UNVERIFIED bucket.

## What NOT to do

- **Don't build a smarter per-line timing matcher.** The recoverability
  failures were alignment flips on repeated phrases — the same saturation the
  April experiment hit with DTW. More matcher sophistication is the most
  expensive possible path to maybe-fixing a signal that also failed
  separation.
- **Don't re-run the gate with SenseVoice before amending the spec.** The
  bake-off belongs to Phase 2's content signal; spending Phase 1 again
  delays the 399-song run without a hypothesis for why a different engine
  fixes either failure mode.
- **Don't soften the report to keep an auto-PASS bucket.** Publishing a
  wrong LRC is the dangerous direction; the fallback exists precisely to
  avoid it.
