# Phase 1 — Onset-Lead Timecode Feasibility Gate

Issue: #245
Spec: `specs/lrc-review-triage-cascade-design-v2.md` (Phase 1)
Tool: `lab/poc-scripts/phase1_onset_lead.py` (`measure`, `recover`, `analyze`)
Artifacts: `lab/poc-scripts/eval/lrc_truth/phase1/` (per-song JSONs, word caches, `analysis.json`)

## Gate decision: **FAIL** — adopt the Phase 1 fallback

Both criteria fail:

- **(a) Separation: FAIL.** Timing-attributed negative per-line lead median
  (+0.095 s) sits inside the positive p10–p90 band [−1.07 s, +1.76 s]; IQRs
  overlap (`analysis.json → gate.separation`).
- **(b) Recoverability: FAIL.** At the +1.0 s injection the best case is 4/10
  songs (8/10 required) under the permissive denominator; 3/10 under the
  strict one (`analysis.json → gate.recoverability_per_offset`).

Per the spec's fallback: drop signal 1 (onset-lead); the checker becomes
pinyin CER + VAD pre-filter only. The PASS bucket is not automatable; the
report degenerates to a REGEN-probe/MANUAL split with no auto-PASS.

## Method

For each of the 10 set songs (5 seed positives + 5 seed negatives):

1. Transcribe `clean_vocals.flac` (cached stems) with faster-whisper large-v3,
   CPU, in a single whole-file pass with faster-whisper's internal Silero VAD
   (`vad_filter=True`), **auto language detection**
   (`language=None` — one song's LRC is English; the zh-forced default
   hallucinated Chinese on it and produced a 0% match rate).
2. Parse LRC lines (ADR-0008 gap placeholders excluded from scoring).
3. Per-line onset-lead: `lead_s = (onset of the line's first matched ASR word)
   − (LRC line timestamp)`; matched via homophone-tolerant pinyin sequence
   alignment (`eval_lrc.align_sequences_per_line`), with a raw-token fallback
   for non-CJK lines.
4. Beats: `lead_beats = lead_s * BPM / 60` (BPM from the production
   `recordings.tempo_bpm`, allin1 analysis).

The word stream is transcribed once per song and cached
(`<song_id>.words.json`); recoverability injections re-align shifted LRC
timestamps against the same cached stream (audio unchanged → re-transcription
would add no signal).

## Base measurement — the headline finding

On the five seed positives, the measured per-line lead distribution is
centered at ≈ 0 s, not the spec's [1, 3]-beat prior (~0.9–2.7 s at these
tempos):

| song | truth | matched sung lines | lead median (s) | p10–p90 (s) |
|---|---|---|---|---|
| cong_zao_chen_dao_ye_wan | positive | 36/36 | +0.31 | −1.09 … +1.65 |
| hereforyou (English) | positive | 58/90 | +0.68 | −0.55 … +3.93 |
| mei_hao_de_chuang_zao | positive | 53/56 | −0.26 | −1.53 … +0.48 |
| xin_kao_mei_yi_ju_ying_xu | positive | 31/31 | −0.32 | −1.15 … +0.31 |
| zhu_a__wo_yao_gen_sui_mi | positive | 34/34 | +0.03 | −0.75 … +1.01 |

Either (i) good LRCs in this catalog are timestamped at/onset-of-vocal rather
than 1–3 beats early, or (ii) the ASR onsets are later than true vocal onsets
(Whisper word timestamps lag, VAD segment edges are late). The data cannot
distinguish these without hand-labeled onsets, but either way the [1, 3]-beat
prior expectation does not hold on real positives — which independently
undermines the auto-PASS criterion, not just the gate arithmetic.

## Test (a) — Separation: FAIL

**Attribution.** Only the `youtube_transcript` negatives carry timing failure
ground truth (their failure mode is gross LRC drift). The `qwen3_asr`
negative is a content failure (missing/duplicated lines) and the
`manual_upload` negative was flagged `anomaly` in Phase 0 — both are excluded
from the timing-separation test and reported separately.

**Criterion** (per-line distributions, not per-song medians): the pooled
timing-negative per-line lead distribution separates from the pooled positive
per-line lead distribution when its median lies outside the positive
p10–p90 spread AND its IQR is disjoint from the positive p10–p90 range.

**Result** (n = 212 positive lines vs 104 timing-negative lines, in seconds):

| | p10 | p50 | p90 |
|---|---|---|---|
| positives | −1.07 | −0.01 | +1.76 |
| timing negatives | −0.93 | +0.10 | +2.29 |

Timing-negative median +0.10 s is inside the positive band →
`median_outside_positive_band: false`; IQRs overlap → `iqr_disjoint: false`.
The two distributions are statistically indistinguishable at this sample
size. A drifted youtube_transcript LRC does not produce measurably larger
|lead| than a good LRC — drift and "good" both land in the same ±2 s band.

## Test (b) — Recoverability: FAIL

Injected uniform offsets +0.5 s and +1.0 s into every set song's LRC line
timestamps; re-measured against the same cached word stream.

**Criterion** (issue #245, literal): recovered offset within ±0.25 s of the
injected value on ≥90% of sung lines for ≥8/10 songs.

**Scope note**: injections run on all 10 songs, not only positives — the
estimator is under test, not LRC correctness; a drifted negative whose lead
distribution still tracks the injection is evidence the estimator recovers
uniform components from drift.

**Result** (`analysis.json → gate.recoverability_per_offset`):

| offset | strict denominator (all sung lines) | permissive (lines measured in both runs) |
|---|---|---|
| +0.5 s | 6/10 | 8/10 |
| +1.0 s | 3/10 | 4/10 |

The verdict does not depend on the denominator reading: both fail at +1.0 s.

**Failure anatomy.** Per-line recovered deltas are overwhelmingly exact
(mode = injected value on every song; e.g. zhu_a: 33/34 lines at exactly
+1.0 s; cong_zao: 32/36; mei_hao: 45/53). The recovered **median** equals the
injected offset exactly on all 20 song×offset runs. Songs fail the per-line
≥90% criterion because of a small number (1–4) of alignment-flip outliers per
song whose per-line delta is large negative (−0.8 to −6.6 s): under the
injected shift, the first-word matcher flips to a different word for a few
lines, and those outliers drag the per-line fraction below 90% and the
recovered mean below the injected value. Two concrete contributors:

- `hereforyou_62e79ae9` (English): 32 of 90 sung lines have no pinyin and can
  never match — the strict denominator counts them as recovery failures. Its
  permissive fraction is 58/58 = 1.00 at +0.5 s and 54/56 = 0.96 at +1.0 s.
- Per-song means at +1.0 s (e.g. 0.642 for cong_zao, 0.677 for mei_hao) are
  3–4 outliers away from the injected 1.0; without those lines every song
  would pass. This is line-level alignment brittleness under a global shift,
  not estimator bias.

**Implication for the fallback.** The estimator's central tendency recovers
injected offsets perfectly (median exact on 20/20 runs), but per-line
recoverability is brittle exactly where the checker needs it (per-line
classification). Combined with the failed separation, per-line onset-lead is
not a reliable basis for the PASS decision at Phase 1 scale.

## Known limitations

- `hereforyou_62e79ae9` (positive) has English lyrics. The initial zh-forced
  transcription produced hallucinated Chinese filler (0% match); fixed by
  passing `language=None` (faster-whisper auto-detect, verified `en` at
  p=0.94 on this song's vocals) and adding a raw-token per-line alignment
  path (`align_sequences_per_line_raw`) for all-non-CJK LRCs. Its per-song
  numbers are reported as-is; its 32 no-pinyin lines are the entire
  strict-vs-permissive denominator gap, so the verdict is unaffected.
- Transcription is a single whole-file pass with faster-whisper's internal
  Silero VAD (`vad_filter=True`); its VAD is tuned for speech, so on sung
  vocals it can hold segments through instrumental gaps. This affects
  segmentation granularity, not transcription language or alignment
  correctness.
- The spec allows one retry with the Phase 2 bake-off winner if the gate
  fails with Whisper. We did not exercise it: the failure mode is
  line-level alignment brittleness (matcher flips to a different word under
  a global shift) plus zero separation on the base measurement — an
  engine swap does not address either, and the base-measurement finding
  (positives center at ≈ 0 s lead, not [1, 3] beats) would need
  hand-labeled ground truth to disprove, which no alternative engine
  provides.
- `_song_source` reads LRC provenance from the Phase 0 snapshot
  (`eval/lrc_truth/latest.json` → `seed_subsets.negative.lrc_source_provenance`),
  falling back to the spec's Appendix A mapping; per-song `source`
  fields in `analysis.json` reflect that resolution.

## Consequences (per spec fallback)

- Signal 1 (onset-lead timing) is dropped; the checker becomes
  **pinyin CER + VAD pre-filter** only (signals 2 + 3).
- The PASS bucket is not automatable (it requires the ~2-beat lead); the
  Phase 3 report degenerates to a **REGEN-probe / MANUAL** split with no
  auto-PASS, and the Success Criteria restated accordingly: precision/recall
  apply to FAIL detection only.
- Phase 2 (engine bake-off) as originally scoped is moot for signal 1; any
  remaining engine evaluation serves the pinyin-CER checker only.
