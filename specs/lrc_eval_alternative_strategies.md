# Plan: Better LRC↔Vocal-Stem Validation Strategy (No ASR Re-run)

## Context

The current `poc/score_lrc_quality.py` reported 0.950 / 1.0 for `dan_dan_ai_mi_249`
but user-audible timing drift is clearly present. Investigation confirmed the
scorer has a structural blind spot:

- It synthesizes TTS for each LRC line, then pulls a stem window from
  `[line.timestamp, next_line.timestamp]` (up to 15 s) and scores via
  **mean-pooled wav2vec2 cosine similarity**.
- Mean-pooling over a 3–6 s window destroys temporal information, so as long
  as *any* matching vocal exists somewhere in the window, the score is high.
- Because this worship song recycles 5–6 short phrases (`我愛祢 我的主`,
  `單單愛祢`, `祢是唯一`, …), *any* vocal window contains near-matches for
  any line. The score is near-guaranteed to be high regardless of timing.
- `peak_offset` is computed but not used in PASS/REVIEW decision. Multiple
  lines show scores ≥0.97 with offsets of 2–5 s — audible drift that the
  reported score hides.

Goal: design a stronger validation strategy that operates **only on the final
LRC + vocal stem** (no ASR re-run, no reuse of raw ASR output — the final LRC
has been canonical-snapped and possibly forced-aligned, so pre-snap ASR
timestamps are no longer tied 1:1 to final LRC lines), optionally using TTS.
Goal: come up with a few options and recommend one to try.

## Hard constraint

**Do not re-run ASR.** Do not use `asr_raw.json` either — its text and timings
belong to the pre-snap transcription and do not correspond to the final LRC's
lines. Anything we compute must derive from:

1. the final LRC (timestamps + text per line), and
2. the vocal stem (`stems/clean_vocals.flac`),
3. optionally, TTS synthesis of each LRC line (cached already).

## Why current approach fails

Two independent failure modes:

- **Temporal pooling kills alignment signal.** `embedder.embed()` returns
  `last_hidden_state.mean(dim=1)` — a single vector per window. Moving the
  stem window ±3 s barely changes the mean.
- **Phrase recycling inflates similarity.** Self-cosine between different
  lines of this song (e.g., `我愛祢 我的主` vs `單單愛祢`) is already high in
  wav2vec2 space because the phonetic inventory overlaps heavily. So "0.97"
  is not evidence of correctness; it's the noise floor for this song.

## Candidate strategies (TTS-based, no ASR)

### Option A — Per-line peak-offset gating

Keep the current TTS pipeline but *ignore* the cosine score and make
`peak_offset` the primary signal. The scorer already computes it via sliding
mean-pooled cosine over the window.

- PASS rule: `median(|peak_offset|) < 0.5 s` and `P90(|peak_offset|) < 1.5 s`.
- Per-line flag: `|peak_offset| > 1.0 s` → REVIEW.
- **Pros:** zero new dependencies, 1-file edit, re-uses cached TTS/embeddings.
- **Cons:** peak-offset is still computed from mean-pooled sliding window, so
  it's noisy on short lines (`全心全意` is 4 chars ≈ 1 s of TTS) and can lock
  onto a repeat of the same phrase elsewhere in the window. The repeat
  problem is the killer: the song repeats `我愛祢 我的主` ~20 times, so peak
  often locks onto the wrong instance and reports a small offset by
  coincidence.

### Option B — TTS-vs-stem framewise DTW, scored by path cost and slope

Replace cosine-of-means with **DTW over framewise wav2vec2 embeddings**,
restricted to a tight window around the LRC timestamp (e.g.,
`[t − 0.5, t + tts_duration + 1.0]`).

- Score each line on two DTW-derived quantities:
  - **Path cost** (average cosine distance along the warping path) —
    measures phonetic match.
  - **Path slope deviation** from 1.0 — measures timing stretch/compression.
    An aligned line produces a path with slope ≈ 1; a misaligned one
    produces a near-horizontal or near-vertical path with high slope
    deviation.
- PASS rule: require both low path cost *and* slope near 1.
- **Pros:** directly measures whether TTS phonemes line up in time with stem
  phonemes. Tight window prevents far-away repeats from dominating. DTW is
  already imported in `score_lrc_quality.py:506`.
- **Cons:** DTW over framewise embeddings is ~20–50× more compute than the
  current cosine-of-means. For 63 lines with ~2 s TTS + ~3 s stem windows at
  50 Hz (wav2vec2 hop ≈ 20 ms), each line is ~100 × 150 = 15k cells —
  tractable, a few minutes total. Slope measurement adds implementation
  work.

### Option C — TTS onset sequence ↔ stem onset sequence alignment

Reduce the problem to **onset timing**. Voice onsets are the sharpest
timing landmark in singing.

- For each LRC line:
  1. Synthesize TTS (already cached) and extract TTS onsets via
     `librosa.onset.onset_detect` on the TTS audio — gives an expected
     pattern of N onsets (one per character, roughly).
  2. Extract stem onsets in a tight window `[t − 0.3, t + tts_duration + 0.3]`.
  3. Score the match between the two onset sequences (e.g., count of matched
     onsets within ±150 ms, or DTW over the 1-D onset-time sequences).
- PASS rule: require ≥ 80 % of TTS onsets to have a stem onset within
  ±150 ms.
- **Pros:** directly measures timing (not phonetic similarity); cheap;
  librosa is already a project dep. Less sensitive to phrase-recycling
  because the match is local.
- **Cons:** onset detection on sung vocals with vibrato/sustained notes is
  noisy; may mis-count onsets on long held syllables. Pure-timing — doesn't
  validate that the *right* text is there. Needs the phonetic check
  alongside.

### Option D — Forced-alignment-at-line-level via TTS anchors

Treat the LRC as a known text and do a **constrained forced alignment** of
the whole LRC against the whole stem, using each line's TTS as an acoustic
anchor.

- Algorithm sketch:
  1. Synthesize TTS for every line (already cached).
  2. Concatenate per-line TTS framewise embeddings into one long sequence
     (with small gaps).
  3. Compute stem framewise embeddings for the full song (once).
  4. Run a constrained DTW/Viterbi that must visit line anchors **in
     order**, emitting a predicted start time per line.
  5. Score each line by `|predicted_start − lrc_timestamp|`.
- PASS rule: `median(|drift|) < 0.3 s`, `P90 < 1.0 s`, no line flipped past
  its neighbor.
- **Pros:** monotonicity constraint eliminates the phrase-recycling problem
  (each line can only match *its* slot in the song's timeline).
  End-to-end — gives you a corrected timestamp for free, so the same pass
  can suggest fixes.
- **Cons:** significant implementation work (constrained DTW / monotone
  alignment over embedding sequences). Compute is meaningful but
  one-shot — ~5k × 10k cell DTW for a 4–5 min song, doable in a few
  minutes with band-limited DTW. Needs careful handling of instrumental
  gaps between lines.

### Option E — Voice activity consistency check

Pure timing, no TTS needed.

- Run Silero VAD (already a project dep — used by `gen_lrc_whisperx.py`) on
  the stem.
- For each LRC line, check: is there voiced audio at `[t, t + min_duration]`?
- Detect inversions: does line N end before line N+1 starts?
- **Pros:** trivial; no TTS; catches gross timing errors (line placed in
  silence).
- **Cons:** says nothing about *what* is being sung — a line placed during
  any vocal passage passes. Useful as a cheap pre-filter, not as the main
  signal.

### Option F — Montreal Forced Aligner (MFA) on stem + LRC text

Treat the LRC text as a known transcription and run MFA to align phones
against the stem. MFA is purpose-built for this problem on read speech.

- Run `mfa align` with the official `mandarin_mfa` acoustic model and
  pinyin-based dictionary over `(stem, LRC text)`.
- Per-line signals: `mfa_line_start` (aligner's word-level start of the
  line's first character) and `mean_phone_log_likelihood`.
- PASS rule candidates:
  - `|mfa_line_start − lrc_timestamp| < 0.3 s`, and
  - `mean_phone_log_likelihood > threshold` (threshold calibrated per
    song).
- **Pros:** purpose-built, gives both timing drift *and* acoustic
  confidence. No TTS needed. Monotone by construction, so no
  phrase-recycling problem. Output timestamps double as corrected LRC
  candidates.
- **Cons:** MFA is trained on read speech, not sung Mandarin — alignment
  quality on melismas / sustained notes is an open question. New
  dependency (conda-only). Official Mandarin dict assumes pinyin input;
  Traditional-char LRCs need conversion.

### Option G — qwen3-forcedaligner (neural aligner)

Same shape as Option F but with a neural aligner already declared in this
project (`poc_qwen3_align` extra, `pyproject.toml:81`).

- Feed stem + LRC text; get per-line start + per-line confidence.
- Same PASS/drift logic as Option F.
- **Pros:** already installed. Neural aligners tend to be more robust to
  non-read speech than HMM-GMM aligners like MFA. No new dep.
- **Cons:** unknown performance on sung Mandarin worship vocals; need to
  empirically validate.

### Option H — Rhythmic density via onset detection

Cheap, pure-timing signal distinct from Option C: aggregate, not
per-character.

- Count stem onsets in `[t_i, t_{i+1}]` (librosa onsets on stem).
- Compare to expected character count of the LRC line (one onset per
  character, roughly, for Mandarin).
- Line is suspicious if observed onset count deviates >30 % from expected.
- **Pros:** trivial; no TTS; complementary to phonetic checks.
- **Cons:** onset counting on sustained notes is unreliable; probably
  only useful as a secondary signal.

### Option I — Tone/F0 correlation via CREPE/pYIN

Mandarin is tonal. Each character has an expected tone contour (1=flat,
2=rising, 3=dip, 4=falling). In *spoken* Mandarin the F0 contour follows
the tone. In *sung* Mandarin the melody dominates, so tone information is
partially masked — but not entirely.

- Extract F0 on the stem window via `librosa.pyin` (cheap) or CREPE
  (higher quality, heavier).
- For each character, compare observed F0 slope sign vs expected tone
  slope sign.
- Per-line score = mean agreement across characters.
- **Pros:** orthogonal signal to everything above. If it works, it's a
  strong sanity check that can't be fooled by phrase recycling.
- **Cons:** unknown whether tone survives sung-melody contamination
  enough to be useful. Treat as a validity probe in the experiment
  phase, not a committed signal.

## Recommendation: **experiment first, then pick**

The user has expanded the option set (Options F–I above) and asked to
gather per-line signal data across a few songs before committing to a
final scorer design. Concretely:

- Write an experiment driver that computes Options B, E, F, G, H, I as
  per-line signals on at least one known-bad (`dan_dan_ai_mi_249`) and
  one known-good (`wo_yao_yi_xin_cheng_xie_mi_247`) song.
- Emit a per-line CSV + a short markdown summary showing which signals
  cleanly separate the two songs at line level.
- Pick the final scorer (single signal or weighted combo) from that
  evidence, then replace `score_lrc_quality.py` outright (no legacy
  flag — user prefers a clean cutover).

### Why this beats committing to Option B up front

## Prior recommendation (superseded): **Option B (DTW with slope), augmented by Option E as a pre-filter**

Rationale:

- **Option D is the ideal** but is a multi-day implementation and risks
  getting stuck on constrained-DTW edge cases (instrumental gaps, tempo
  breaks). Save for a later iteration.
- **Option A** is the cheapest fix but the phrase-recycling problem means
  peak-offset locks onto wrong repeats. Not worth the false confidence.
- **Option C** alone doesn't validate text.
- **Option B** directly attacks both failure modes of the current scorer:
  - DTW over framewise embeddings preserves the temporal signal that
    mean-pooling destroys.
  - A tight ±0.5 s anchor window around the LRC timestamp keeps the match
    local, so distant repeats of the same phrase can't win.
  - Slope-near-1 requirement means a line that "matches" only by stretching
    time 3× is correctly rejected.
- **Option E** as a sanity pre-filter is nearly free and catches the dumb
  errors (line placed in silence, line past end-of-song) without loading
  the embedder.

Expected outcome on `dan_dan_ai_mi_249`:
the current scorer reports 0.950; a correctly-implemented Option B should
plausibly land in the 0.55–0.75 range and flag the 15+ lines with
`peak_offset > 1.0 s` as the suspect set. That matches the user's audible
observation.

## Implementation plan (superseded — see experiment-first plan below)

New CLI flag on `poc/score_lrc_quality.py`, e.g., `--strategy dtw` (default
stays as legacy for now, to preserve comparability).

Concrete changes to `poc/score_lrc_quality.py`:

1. **Tight window.** Change `window_start/window_end` (lines 644–651) to
   `[max(0, t − 0.5), t + tts_duration + 1.0]` instead of the
   next-line-bounded window.
2. **Framewise DTW as primary score.** Rework `score_line` (lines 586–609)
   to:
   - Always compute `tts_frames` and `stem_frames`.
   - Run the existing `dtw_distance` (line 506) over them, but *also*
     recover the warping path (change `dtw_distance` to return `(score,
     path)`), compute `path_slope` as `Δstem / Δtts` along the path and its
     std-dev from 1.0.
   - Score the line as a weighted combo, e.g.,
     `0.7 · path_cosine + 0.3 · (1 − clamp(|slope − 1|, 0, 1))`.
3. **PASS/REVIEW rule.** In `score_lrc` (line 710), require `median(score)
   ≥ 0.75` *and* `≤ 15 %` lines below 0.6 *and* per-line-slope-deviation
   stats within bounds. Emit the per-line slope in the markdown and JSON
   reports.
4. **Silero VAD pre-filter.** Add a quick pass using `silero-vad` (already
   indirectly in the stack via whisperx) that marks each line with
   `voiced_fraction_in_window`. Lines with `voiced_fraction < 0.3` get a
   REVIEW flag regardless of DTW.
5. **Reports.** Add new columns: `path_cost`, `slope`, `voiced_fraction`.
   Keep legacy `score` and `peak_offset` for comparison during rollout.

Critical files to modify:

- `poc/score_lrc_quality.py` — all scoring logic (the only file that needs
  to change).

Dependencies (likely already present via `score_lrc_base` extra):

- `scipy` (already imported for DTW).
- `librosa` (already imported).
- `silero-vad` — confirm before coding; if absent, `torchaudio` VAD or
  energy-threshold VAD is a fallback.

## Verification

On `dan_dan_ai_mi_249` (ground-truth: user says timing is off):

- Run both the legacy scorer and the new `--strategy dtw` scorer.
- Expect new scorer to flag ≥10 lines as REVIEW (matching the audible
  drift), overall score to fall below 0.80.
- Manually spot-check 3 flagged lines against audio to confirm they really
  are mis-timed.
- Run on a known-good song (any song with a hand-verified LRC) to confirm
  the new scorer *doesn't* spuriously REVIEW good LRCs. If no such song
  exists in the catalog, note this as a follow-up.

## Out of scope

- Re-running ASR (but external forced alignment via MFA / qwen3-forcedaligner
  is explicitly in scope per Options F–G above).
- Reusing `asr_raw.json` — its text/timings are pre-canonical-snap and
  don't map 1:1 to final LRC lines.
- Fixing the LRC itself. This plan only concerns *detecting* bad LRCs;
  correcting them is a separate task (likely Option D in a later
  iteration).

---

## Experiment-first plan (current direction after interview)

### Decisions captured

- **Legacy handling:** replace `score_lrc_quality.py` outright once the
  final scorer is chosen. No `--strategy` flag, no dual-maintenance.
- **Reference songs:**
  - Bad: `dan_dan_ai_mi_249` (audible drift; current scorer returns 0.950).
    *Locate stems + final LRC before the experiment starts* — not present
    under `vocal_extraction_output/`.
  - Good: `wo_yao_yi_xin_cheng_xie_mi_247`. Canonical LRC via
    `sow_admin audio view-lrc wo_yao_yi_xin_cheng_xie_mi_247` (not the
    raw `…sensevoice.lrc` file).
- **VAD:** Silero.
- **Forced aligner:** try both MFA and `qwen3-forcedaligner` in the
  experiment. `qwen3-forcedaligner` is already in
  `pyproject.toml:81` under the `poc_qwen3_align` extra. MFA is **not**
  installed; add it only if the experiment supports it.
- **MFA model (if used):** official `mandarin_mfa` via `mfa model download`;
  training on worship data is a follow-up only if sung-vocal alignment is
  poor.
- **Tone/F0 and onset-density signals:** include as exploratory validity
  probes; drop before the final scorer if they're too noisy on sung vocals.

### Experiment deliverable

A new script `poc/experiment_lrc_signals.py` that, per song, produces
`poc/experiment_output/<song>/signals.csv` with per-line columns:

`line_idx, t_lrc, text, voiced_frac, dtw_path_cosine, dtw_slope_dev,
onset_match_ratio, tone_corr, mfa_drift, mfa_logprob, qwen3_drift,
qwen3_conf`

Plus a short `signals.md` summary: histograms per signal, top-10
worst-per-signal lines for manual spot-check, and correlations between
signals (do DTW slope and qwen3 drift agree on which lines are bad?).

**No PASS/REVIEW thresholds in this phase.** Thresholds get picked after
the data comes back.

### Post-experiment decision

With both reference songs scored, pick the final scorer by:

- Which signals cleanly separate the two songs at the *per-line* level?
- Which are cheap and robust enough for every-run use?
- Single signal, or a small weighted combo?

That decision, and the outright replacement of `score_lrc_quality.py`,
happen in a follow-up PR.
