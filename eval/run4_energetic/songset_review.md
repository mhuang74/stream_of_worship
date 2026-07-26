# Songset Constructor Review

## Key Findings

- 5 proposals were generated.
- Final proposals came from deterministic beam ranking.
- Relaxation or constraint warnings: none reported.
- Phase flow available in the pool: phase 1: 78, phase 2: 38, phase 3: 243, phase 4: 60, phase 5: 19.
- Tempo coverage: 438 known BPM values and 0 missing.

## Run Summary

- Run ID: songset-20260726T071123Z-3s-top5
- Generated: 2026-07-26T07:12:05.393275+00:00
- Requested song count: 3
- Top-k: 5
- Pool size: 438
- Relevant flags: no_llm=True, auto_relax=True, relax_h1=True, relax_h2_bpm=110

## What Was Done

- Loaded the catalog pool from the configured read-only source.
- Enriched candidates with phase and theme metadata, dropping songs without enough tempo/key data.
- Built compatible transition candidates and seeded ranked sequences.
- Finalized ranked proposals from beam candidates and validation state.
- Wrote proposal, report, pool, trace, and review artifacts.

## How Filters Were Applied

Enrichment output contains 438 candidates; dropped=0. Validation events recorded: 0. Final relaxed warning flags: no_llm=True, auto_relax=True, relax_h1=True, relax_h2_bpm=110.

## Proposal 1

Score: 0.7314
Score components: theme 0.917, tempo 0.393, harmony 0.735, diversity 1.000.
Origin: deterministic beam ranking.
Warnings: none.
Rationale: Deterministic beam seed.

| # | Title | Phase | BPM | Key | Themes | Transition |
|---|---|---:|---:|---|---|---|
| 1 | 極大的聲音 | 1 | 112.3 | G major | 赞美, 祈祷 | shift 0, gap 2 beats |
| 2 | 主賜福如春雨 | 3 | 103.4 | C major | 敬拜, 赞美 | shift 0, gap 2 beats |
| 3 | 我的救贖者活著 | 4 | 71.8 | A major | 十字架, 赞美 | shift -2, gap 4 beats |

## Proposal 2

Score: 0.7084
Score components: theme 0.917, tempo 0.362, harmony 0.665, diversity 1.000.
Origin: deterministic beam ranking.
Warnings: none.
Rationale: Deterministic beam seed.

| # | Title | Phase | BPM | Key | Themes | Transition |
|---|---|---:|---:|---|---|---|
| 1 | 極大的聲音 | 1 | 112.3 | G major | 赞美, 祈祷 | shift 0, gap 2 beats |
| 2 | 我們愛戴的王 | 3 | 103.4 | F major | 敬拜, 赞美 | shift 0, gap 2 beats |
| 3 | 最好的朋友 | 4 | 69.8 | D major | 十字架, 感恩 | shift -2, gap 4 beats |

## Proposal 3

Score: 0.7084
Score components: theme 0.917, tempo 0.362, harmony 0.665, diversity 1.000.
Origin: deterministic beam ranking.
Warnings: none.
Rationale: Deterministic beam seed.

| # | Title | Phase | BPM | Key | Themes | Transition |
|---|---|---:|---:|---|---|---|
| 1 | 極大的聲音 | 1 | 112.3 | G major | 赞美, 祈祷 | shift 0, gap 2 beats |
| 2 | 禱告的大軍 | 3 | 103.4 | A major | 祈祷, 圣灵 | shift 0, gap 2 beats |
| 3 | 榮耀羔羊 | 4 | 69.8 | C major | 十字架, 敬拜 | shift 2, gap 4 beats |

## Proposal 4

Score: 0.5584
Score components: theme 0.917, tempo 0.362, harmony 0.665, diversity 1.000.
Origin: deterministic beam ranking.
Warnings: none.
Rationale: Deterministic beam seed.

| # | Title | Phase | BPM | Key | Themes | Transition |
|---|---|---:|---:|---|---|---|
| 1 | 極大的聲音 | 1 | 112.3 | G major | 赞美, 祈祷 | shift 0, gap 2 beats |
| 2 | 我們愛戴的王 | 3 | 103.4 | F major | 敬拜, 赞美 | shift 0, gap 2 beats |
| 3 | 專愛 | 4 | 69.8 | D major | 奉献, 敬拜 | shift -2, gap 4 beats |

## Proposal 5

Score: 0.5584
Score components: theme 0.917, tempo 0.362, harmony 0.665, diversity 1.000.
Origin: deterministic beam ranking.
Warnings: none.
Rationale: Deterministic beam seed.

| # | Title | Phase | BPM | Key | Themes | Transition |
|---|---|---:|---:|---|---|---|
| 1 | 極大的聲音 | 1 | 112.3 | G major | 赞美, 祈祷 | shift 0, gap 2 beats |
| 2 | 我們愛戴的王 | 3 | 103.4 | F major | 敬拜, 赞美 | shift 0, gap 2 beats |
| 3 | 更深渴慕祢 | 4 | 69.8 | D major | 奉献, 祈祷 | shift -2, gap 4 beats |
