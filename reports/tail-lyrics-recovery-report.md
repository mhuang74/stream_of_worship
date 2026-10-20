# Tail-lyrics recovery: is local Whisper worth its CPU?

**Data cut:** 2026-10-20 01:25:48Z (container death, SIGKILL exit 137, OOM not indicated) ·
**Scope:** full run of container instance `analysis-service-analysis-dev-1` on remote host
`unoccluded.tailscale` (driver `sow_dev` / `sow_dev_2`) · **Source:** `docker logs` grep + parse;
no code/DB/R2 changes.

## Headline

| Metric | Value |
|---|---|
| LRC jobs completed in run (`LRC result cached`) | 62 |
| Tail-fill triggered | 36 |
| Recovered ≥1 lyric line | 22 / 36 (61%) |
| Total lines appended | 43 |
| Whisper-path (Qwen3 unusable) | 26 jobs → 35 lines |
| Qwen3-succeeded (completed jobs) | 9 jobs → 8 lines |

**Whisper (faster-whisper large-v3, cpu/int8):**
- 26 fresh loads, 245.6s total (5 cold ≈ 19.6–30.7s; warm ≈ 5.0–6.2s)
- Transcription wall (segment-emission → LLM alignment) = 1491.6s on the 17 runs that recovered lines, 554.0s wasted on the 9 zero-line runs → 42.6s transcribe wall per recovered line on successful-only runs (58.4s if the zero-run waste is spread over all 43 lines)
- Total Whisper CPU (model load 245.6s + transcribe wall 2045.6s) ≈ **0.64 CPU-h over 26 runs**

**Qwen3 (DashScope remote ASR):**
- Succeeds only 9/36 (25%) tail attempts among completed jobs; 26 attempts unusable → Whisper fallback (1 more Qwen3 attempt was in flight at kill: 祢使我雙腳跳舞)
- Fallback reasons (26): 16 × "returned too-few usable phrases" (1–2 phrases on sung tails), 9 × `SUCCESS_WITH_NO_VALID_FRAGMENT` after retries, 1 × filetrans polling timeout
- Wall per attempt: 22.6–31.8s when it succeeds; too-few detection is cheap (one 0.1s cache hit, 15 misses 16.5–40.2s); the 9 failed-after-retries paths await 36.3–57.8s each and 1 polling-timeout path awaited 1814.4s (給夢想一雙翅膀) — the 10 hard failures cost ~2229.7s combined wall drag, **zero CPU, but serious pipeline latency**

## Verdict

- **Local Whisper IS earning its CPU.** 17 of 26 fallback runs recovered lines (65%); ~48.4s whisper wall per recovered line, ~42.6s transcribe wall per line — modest on a dev box. Without it, 35 of the 43 recovered lines would be missing.
- **The Qwen3 tail path is the weak link, not Whisper.** 72% of attempts (26/36) return junk. A tail is inherently a repeat-heavy, phrase-sparse segment — this is structural, not a transient outage.
- Keep both. No code change in this task.

## Per-song detail (36 trigger jobs, chronological)

| # | Title | Job id | Gap s | Clip start / len s | Qwen3 attempt | Appended | Transcribe s | Whisper wall s | Qwen3 wall s |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 禱告的大軍 | ac611ee8 | 67.1 | 237.97 / 82.09 | too-few (2), cache hit | 2 | 87.7 | 144.4 | 0.1 |
| 2 | 禱告的力量 | dc8999c1 | 40.5 | 233.93 / 55.54 | no-valid-fragment | 1 | 75.1 | 106.1 | 43.2 |
| 3 | 得勝的歌 | f63ffe03 | 34.1 | 269.35 / 49.14 | no-valid-fragment | 2 | 69.5 | 89.3 | 51.0 |
| 4 | 恩典之路 | 0baeb226 | 32.3 | 218.67 / 47.30 | ok | 0 | — | — | 23.3 |
| 5 | 復興聖潔 | 27f04561 | 33.8 | 321.63 / 48.78 | too-few (2) | 1 | 56.1 | 61.7 | 16.5 |
| 6 | 給夢想一雙翅膀 | c8a27fe5 | 33.6 | 238.04 / 48.59 | filetrans timeout | 0 | 51.3 | 57.1 | 1814.4 |
| 7 | 更深渴慕祢 | e73caf6d | 38.5 | 247.53 / 53.48 | no-valid-fragment | 0 | 53.1 | 58.3 | 39.3 |
| 8 | 和散那，歡迎君王 | eb3c8019 | 56.4 | 249.90 / 71.37 | ok | 0 | — | — | 22.6 |
| 9 | 黑暗中的光芒 | 006da2a8 | 30.7 | 433.87 / 45.75 | no-valid-fragment | 1 | 55.5 | 61.4 | 36.3 |
| 10 | 厚恩待我 | ec077cea | 65.8 | 262.34 / 80.80 | no-valid-fragment | 4 | 88.8 | 94.5 | 44.8 |
| 11 | 回家 | 8e6bea7f | 42.7 | 217.30 / 57.73 | ok | 1 | — | — | 23.1 |
| 12 | I Believe［我相信］ | 74bd4d0c | 37.6 | 240.97 / 52.58 | no-valid-fragment | 0 | 56.7 | 62.9 | 48.3 |
| 13 | 簡單的歌 | f9239d82 | 35.3 | 169.07 / 50.34 | ok | 1 | — | — | 26.1 |
| 14 | 將天敞開 | 1e065d7e | 32.5 | 204.80 / 47.46 | too-few (2) | 2 | 58.7 | 64.5 | 21.1 |
| 15 | 降下祢大能 | 0597db48 | 37.3 | 1150.50 / 52.29 | too-few (1) | 2 | 80.9 | 86.9 | 40.2 |
| 16 | 近前來 | ca089bfc | 55.7 | 325.84 / 70.72 | ok | 2 | — | — | 29.8 |
| 17 | 盡情地微笑 | a061bf0c | 34.7 | 324.34 / 49.68 | ok | 1 | — | — | 23.6 |
| 18 | 進入豐盛 | 02a4a797 | 34.5 | 176.83 / 49.46 | too-few (1) | 4 | 72.5 | 78.4 | 24.8 |
| 19 | 進入祢的同在 | 07700c9a | 37.0 | 324.27 / 52.01 | too-few (1) | 2 | 55.3 | 61.3 | 27.1 |
| 20 | 敬拜祢 | 824060ac | 64.4 | 228.53 / 79.40 | too-few (1) | 3 | 99.6 | 105.3 | 24.6 |
| 21 | 救贖的恩典 | 81bf9f76 | 36.9 | 232.20 / 51.88 | too-few (2) | 0 | 59.4 | 65.4 | 26.4 |
| 22 | 看見復興 | 9a756740 | 47.1 | 196.87 / 62.14 | too-few (2) | 3 | 92.2 | 98.5 | 25.0 |
| 23 | 滿有能力 | 543779a1 | 31.9 | 187.33 / 46.89 | no-valid-fragment | 0 | 59.1 | 65.6 | 39.8 |
| 24 | 祢必成就美好事 | ff0a0186 | 55.2 | 220.10 / 70.20 | too-few (2) | 0 | 82.6 | 88.6 | 37.9 |
| 25 | 祢的愛 | 2dde3593 | 30.4 | 235.90 / 45.42 | ok | 0 | — | — | 31.8 |
| 26 | 祢的愛長闊高深 | d5e68030 | 40.5 | 170.43 / 55.53 | too-few (1) | 0 | 88.2 | 94.1 | 26.6 |
| 27 | 祢的慈愛 | 214e918d | 32.3 | 184.40 / 47.30 | too-few (2) | 0 | 53.8 | 59.6 | 31.8 |
| 28 | 祢的恩典夠我用 | 8bf090a5 | 43.7 | 215.77 / 58.75 | too-few (1) | 1 | 85.1 | 90.7 | 30.5 |
| 29 | 祢的器皿 | b9cf32b0 | 30.1 | 220.56 / 45.15 | no-valid-fragment | 2 | 152.9 | 159.0 | 57.8 |
| 30 | 祢的最愛 | 7b10ebca | 34.0 | 234.17 / 48.99 | ok | 0 | — | — | 31.5 |
| 31 | 祢是何等榮美 | 1a8a0c6c | 39.8 | 227.73 / 54.82 | no-valid-fragment | 0 | 49.8 | 76.1 | 54.8 |
| 32 | 祢是配得讚美 | 6fbda766 | 33.5 | 328.63 / 48.52 | ok | 3 | — | — | 25.9 |
| 33 | 祢是聖潔 | 648ad811 | 46.1 | 299.07 / 61.14 | too-few (1) | 2 | 135.6 | 140.9 | 28.1 |
| 34 | 祢是我的一切 | 82cdf596 | 61.7 | 228.23 / 76.71 | too-few (1) | 2 | 99.8 | 119.7 | 31.0 |
| 35 | 祢使我生命美麗 | dffdeebd | 50.7 | 213.73 / 65.72 | too-few (1) | 1 | 126.3 | 132.4 | 32.6 |
| 36 | 祢使我雙腳跳舞 | f237842c | 37.4 | 202.57 / 52.36 | in flight at kill | ? | — | — | — |

**Zero-line jobs (13 finished + 1 in flight):** 9 whisper-path (rows 6, 7, 12, 21, 23, 24, 26, 27, 31) + 4 Qwen3-ok (rows 4, 8, 25, 30); row 36 (祢使我雙腳跳舞) was killed mid-Qwen3-attempt. The 9 whisper-path zero cases wasted ≈ 625.6s (load + transcribe wall) total.

## Appendix: exact recovered lines per song

Per-song (name, real song id from Neon DB, job id, exact recovered lines) lives in the companion
results file: **[`tail-lyrics-recovered-songs.md`](tail-lyrics-recovered-songs.md)**
(22 songs / 43 lines; counts match each job's `asr_tail_line_count` exactly). HTML render:
[`tail-lyrics-recovered-songs.html`](tail-lyrics-recovered-songs.html).

## Method notes and caveats

- Numbers are **log-derived only** (INFO-level `docker logs`, 2026-10-19 23:29Z → 01:25:48Z). No DB or R2 cross-checks.
- The driver was **killed at the data cut** (2026-10-20 01:25:48Z, exit 137; `OOMKilled=false`, so an external SIGKILL) with one tail-fill job mid-Qwen3-attempt (row 36) and ~194 LRC jobs still queued — treat 62 completed LRC jobs as the final tally for this container instance, not the whole library run.
- The "1 job in-flight" note from the 01:15Z snapshot (滿有能力 `job_2f4e543779a1`) actually finished with **0 lines** — included here as a whisper-path zero case.
- 26 of 62 completed jobs never triggered tail fill: their tails were already covered by transcript LRC, or gap ≤ min(30s, 15%·duration) per `tail_fill_threshold_seconds()` in `ops/analysis-service/src/sow_analysis/workers/lrc.py:828`; the not-needed path logs at DEBUG level, which is invisible in INFO logs.
- Config constants confirmed from `config.py` (lines 144–161): tail fill on by default; lead-in 15s (acoustic context; avoids the no-usable-transcription failure of clips starting exactly at the last lyric), dup-window 3s (ASR seam-echo / repeat echo), no_speech drop 0.6, Qwen3 min usable phrases 3, tail min 1.
- "Transcribe s" = segment-emission → LLM-alignment wall (`Processing audio…` → `LLM call completed…`), not model transcribe() alone; whisper wall includes slicing, loading and alignment overhead.
- Earlier container instances (~26 further songs of the ~72-song library known before this window) are gone — logs from them were unavailable for this analysis.