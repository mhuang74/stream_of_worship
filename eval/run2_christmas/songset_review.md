# Songset Constructor Review

## Key Findings

- 5 proposals were generated.
- Final proposals came from deterministic beam ranking.
- Relaxation or constraint warnings: none reported.
- Phase flow available in the pool: phase 1: 106, phase 2: 33, phase 3: 228, phase 4: 54, phase 5: 17.
- Tempo coverage: 438 known BPM values and 0 missing.

## Run Summary

- Run ID: songset-20260726T070948Z-3s-top5
- Generated: 2026-07-26T07:10:36.650771+00:00
- Requested song count: 3
- Top-k: 5
- Pool size: 438
- Relevant flags: no_llm=True, auto_relax=True, relax_h1=True, season=christmas

## What Was Done

- Loaded the catalog pool from the configured read-only source.
- Enriched candidates with phase and theme metadata, dropping songs without enough tempo/key data.
- Built compatible transition candidates and seeded ranked sequences.
- Finalized ranked proposals from beam candidates and validation state.
- Wrote proposal, report, pool, trace, and review artifacts.

## How Filters Were Applied

Enrichment output contains 438 candidates; dropped=0. Validation events recorded: 0. Final relaxed warning flags: no_llm=True, auto_relax=True, relax_h1=True, season=christmas.

## Proposal 1

Score: 0.8551
Score components: theme 1.000, tempo 0.591, harmony 0.890, diversity 1.000.
Origin: deterministic beam ranking.
Warnings: none.
Rationale: Deterministic beam seed.

| # | Title | Phase | BPM | Key | Themes | Transition |
|---|---|---:|---:|---|---|---|
| 1 | 萬國得知祢的救恩 | 1 | 92.3 | G major | 赞美, 差遣 | shift 0, gap 2 beats |
| 2 | 喜樂泉源 | 3 | 95.7 | G major | 敬拜, 赞美 | shift 0, gap 2 beats |
| 3 | 主啊，我要跟隨祢 | 5 | 71.8 | F major | 跟随, 奉献 | shift 0, gap 2 beats |

## Proposal 2

Score: 0.7565
Score components: theme 0.917, tempo 0.526, harmony 0.660, diversity 1.000.
Origin: deterministic beam ranking.
Warnings: none.
Rationale: Deterministic beam seed.

| # | Title | Phase | BPM | Key | Themes | Transition |
|---|---|---:|---:|---|---|---|
| 1 | 祢的器皿 | 1 | 103.4 | F major | 赞美, 感恩 | shift 0, gap 2 beats |
| 2 | 我們愛戴的王 | 3 | 103.4 | F major | 敬拜, 赞美 | shift 0, gap 2 beats |
| 3 | 我的救贖者活著 | 4 | 71.8 | A major | 十字架, 赞美 | shift 1, gap 4 beats |

## Proposal 3

Score: 0.6985
Score components: theme 1.000, tempo 0.641, harmony 0.780, diversity 1.000.
Origin: deterministic beam ranking.
Warnings: none.
Rationale: Deterministic beam seed.

| # | Title | Phase | BPM | Key | Themes | Transition |
|---|---|---:|---:|---|---|---|
| 1 | 披上讚美衣 | 1 | 95.7 | A major | 赞美, 感恩 | shift 0, gap 2 beats |
| 2 | 喜樂泉源 | 3 | 95.7 | G major | 敬拜, 赞美 | shift 0, gap 2 beats |
| 3 | 主啊，我要跟隨祢 | 5 | 71.8 | F major | 跟随, 奉献 | shift 0, gap 2 beats |

## Proposal 4

Score: 0.6831
Score components: theme 1.000, tempo 0.591, harmony 0.780, diversity 1.000.
Origin: deterministic beam ranking.
Warnings: none.
Rationale: Deterministic beam seed.

| # | Title | Phase | BPM | Key | Themes | Transition |
|---|---|---:|---:|---|---|---|
| 1 | 彈琴歌唱讚美祢 | 1 | 92.3 | A major | 赞美, 感恩 | shift 0, gap 2 beats |
| 2 | 喜樂泉源 | 3 | 95.7 | G major | 敬拜, 赞美 | shift 0, gap 2 beats |
| 3 | 主啊，我要跟隨祢 | 5 | 71.8 | F major | 跟随, 奉献 | shift 0, gap 2 beats |

## Proposal 5

Score: 0.6755
Score components: theme 1.000, tempo 0.641, harmony 0.665, diversity 1.000.
Origin: deterministic beam ranking.
Warnings: none.
Rationale: Deterministic beam seed.

| # | Title | Phase | BPM | Key | Themes | Transition |
|---|---|---:|---:|---|---|---|
| 1 | 主的喜樂是我力量 | 1 | 95.7 | E major | 赞美, 信心 | shift 0, gap 2 beats |
| 2 | 喜樂泉源 | 3 | 95.7 | G major | 敬拜, 赞美 | shift 2, gap 4 beats |
| 3 | 主啊，我要跟隨祢 | 5 | 71.8 | F major | 跟随, 奉献 | shift 0, gap 2 beats |
