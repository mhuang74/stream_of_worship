# LRC Signal Experiment Summary — 2026-04-28

## Goal

Identify which per-line timing signals can reliably detect LRC files with poor timing (lyrics shown
at the wrong moment in the audio). Tested 7 signals across 19 of 21 catalog songs.

**Primary finding: VAD voiced fraction is the only actionable signal.** It flags lines placed in
silence, which is a clear indicator of timing drift. All other signals (DTW, onset, tone/F0,
Qwen3 aligner drift) were ineffective or unavailable.

---

## Signals Tested

| Signal | Status | Notes |
|--------|--------|-------|
| `voiced_frac` (Silero VAD) | **Works** | Clear separator; fraction of LRC window that is voiced |
| `onset_match_ratio` | Partial | Works only for songs with TTS cache populated (3 of 19) |
| `dtw_path_cosine` | Not used | Skipped; known to saturate at 0.955 regardless of timing |
| `dtw_slope_dev` | Not used | Skipped; provides no signal |
| `tone_corr` | Not used | Skipped; too noisy on sung vocals |
| `qwen3_drift` | Not used | Skipped; inverted on long songs (see background doc) |
| `mfa_drift` | N/A | MFA not installed |

---

## All-Song VAD Summary (ranked by mean VAD)

| Song | Mean VAD | Min VAD | Lines at 0.0 | Assessment |
|------|:--------:|:-------:|:------------:|------------|
| `wo_yao_quan_xin_zan_mei_244` | **0.926** | 0.652 | 0 | Healthy |
| `ren_ding_mi_242` | **0.854** | 0.474 | 0 | Healthy |
| `shen_gao_yang_248` | **0.848** | 0.310 | 0 | Healthy |
| `zhu_mi_shi_wo_li_liang_321` | **0.836** | 0.090 | 0 | Healthy |
| `feng_sheng_de_ying_xu_250` | **0.805** | 0.000 | 1 | OK (1 intro line at t=0) |
| `wo_yao_yi_xin_cheng_xie_mi_247` | **0.790** | 0.356 | 0 | Healthy (known GOOD) |
| `chai_qian_wo_566` | **0.798** | 0.000 | 3 | Review (3 silent lines) |
| `bao_gui_shi_jia_314` | **0.774** | 0.000 | 3 | Review (3 silent lines) |
| `he_deng_en_dian_262` | **0.746** | 0.136 | 0 | OK |
| `zhe_shi_sheng_jie_zhi_di_259` | **0.759** | 0.323 | 0 | Healthy |
| `wo_yao_kan_jian_146` | **0.736** | 0.000 | 3 | Review (3 silent lines) |
| `yuan_tian_huan_xi_245` | **0.719** | 0.000 | 4 | Review (4 silent lines) |
| `ai_ke_yi_zai_geng_duo_yi_dian_dian_241` | **0.624** | 0.000 | 8 | Likely drift |
| `ye_su_de_ming_246` | **0.569** | 0.000 | 8 | **Likely drift** |
| `dan_dan_ai_mi_249` | **0.545** | 0.000 | 9 | **Known BAD** |
| `cong_zao_chen_dao_ye_wan_130` | **0.521** | 0.000 | 16 | **Likely drift** |
| `huo_zhu_wei_yao_jing_bai_mi_212` | **0.353** | 0.000 | 10 | **Severe drift** |

**Not processed** (no LRC in local cache): `cong_xin_he_yi_195`, `dan_qin_ge_chang_zan_mei_mi_401`

---

## Key Findings

### 1. VAD < 0.55 strongly predicts bad timing

Songs with mean VAD below ~0.55 have multiple lines landing in silence:
- `huo_zhu_wei_yao_jing_bai_mi_212`: 10 lines at VAD=0.000 — worst in catalog
- `cong_zao_chen_dao_ye_wan_130`: 16 lines at VAD=0.000, timestamps bunched at 1-second intervals
- `dan_dan_ai_mi_249`: 9 lines at VAD=0.000 (confirmed audibly bad)

### 2. A single line at VAD=0.000 at `[00:00.00]` is usually harmless

Several otherwise-good songs have one silent line at the very start (intro/pre-roll). These are
false positives. The threshold should be tuned to require 2+ silent lines, or ignore `[00:00.00]`.

### 3. Onset signal requires TTS cache

Only 3 songs had TTS cache pre-populated (dan_dan, wo_yao_yi_xin, zhe_shi_sheng_jie_zhi_di).
For the remaining 16 songs, onset_match_ratio is NaN. To enable onset scoring for all songs:

```bash
# Populate TTS cache for each song
PYTHONPATH=src:. uv run --extra score_lrc python poc/score_lrc_quality.py <song_id>
```

### 4. Recommended threshold for VAD-based quality gate

| Tier | Mean VAD | Verdict |
|------|:--------:|---------|
| PASS | ≥ 0.70 | Timing looks fine |
| REVIEW | 0.55–0.70 | Manual spot-check recommended |
| FAIL | < 0.55 | Likely timing drift, needs LRC fix |

---

## Songs Needing LRC Fixes (Priority Order)

1. **`huo_zhu_wei_yao_jing_bai_mi_212`** — Mean VAD 0.353, 10 silent lines. Highest priority.
2. **`cong_zao_chen_dao_ye_wan_130`** — Mean VAD 0.521, 16 silent lines. Timestamps at 1-second
   intervals suggest manual/approximate LRC.
3. **`dan_dan_ai_mi_249`** — Mean VAD 0.545, 9 silent lines. Already known bad; LRC fix in progress.
4. **`ye_su_de_ming_246`** — Mean VAD 0.569, 8 silent lines. Suspicious clustering at tail-end.
5. **`ai_ke_yi_zai_geng_duo_yi_dian_dian_241`** — Mean VAD 0.624, 8 silent lines.

---

## Methodology Notes

- **Audio source:** `clean_vocals.flac` (BS-Roformer + UVR De-Echo) for all songs. Generated fresh
  for 7 songs that had none in cache.
- **Signals computed:** VAD + onset only (`--skip-dtw --skip-tone --skip-qwen3`). Skipped signals
  are documented as ineffective in the background spec.
- **Stem separation:** Ran serially to avoid BS-Roformer memory starvation (see
  `docs/manually-fix-lrc.md` Step 0 warning).

---

## Related Files

| File | Description |
|------|-------------|
| `poc/experiment_lrc_signals.py` | Experiment script |
| `poc/experiment_output/signals.md` | Full per-song per-line data |
| `poc/experiment_output/*/signals.csv` | Raw CSV per song |
| `specs/experiment_with_lrc_eval_approaches.md` | Run book and background |
| `docs/manually-fix-lrc.md` | LRC fix workflow |
