# Enrichment Eval: Baseline (Pre-Traditional) vs Post-Traditional Comparison

## Overview

Two eval runs of `--only-evaluate-pool-enrichment --pool-limit 500` against the
real SOP.org catalog (438 songs), comparing the mixed Simplified/Traditional
`THEME_VOCAB` (baseline) against the Traditional-only conversion
(post-enhancement).

- Baseline report: `reports/enrichment_eval_baseline_pre_traditional.md`
- Post-enhancement report: `reports/enrichment_eval_post_traditional.md`

## Key Metric Comparison

### Pool Overview

| Metric | Baseline | Post | Δ |
|---|---:|---:|---:|
| Loaded | 438 | 438 | 0 |
| Enriched | 438 | 438 | 0 |
| Dropped | 0 | 0 | 0 |

### Phase Distribution

| Phase | Baseline | Post | Δ |
|---|---:|---:|---:|
| Phase 1 (讚美) | 78 (17.8%) | 70 (16.0%) | −8 |
| Phase 2 (感恩) | 38 (8.7%) | 30 (6.8%) | −8 |
| Phase 3 (敬拜) | 243 (55.5%) | 249 (56.8%) | +6 |
| Phase 4 (奉獻) | 60 (13.7%) | 68 (15.5%) | +8 |
| Phase 5 (差遣) | 19 (4.3%) | 21 (4.8%) | +2 |

### Theme Dominance

| Theme | Baseline | Post | Δ |
|---|---:|---:|---:|
| 讚美 | 78 | 70 | −8 |
| 感恩 | 38 | 30 | −8 |
| 敬拜 | 94 | 106 | +12 |
| 奉獻 | 23 | 28 | +5 |
| 認罪 | 5 | 3 | −2 |
| 差遣 | 2 | 3 | +1 |
| 信心 | 94 | 84 | −10 |
| 祈禱 | 42 | 46 | +4 |
| 復興 | 3 | 1 | −2 |
| 聖靈 | 30 | 34 | +4 |
| 十字架 | 15 | 16 | +1 |
| 跟隨 | 14 | 17 | +3 |

### Signal Coverage

| Signal | Baseline | Post | Δ |
|---|---:|---:|---:|
| Title hits | 76/438 | 106/438 | **+30** |
| Lyrics hits | 342/438 | 372/438 | **+30** |
| Song embedding | 438/438 | 438/438 | 0 |
| Line embeddings | 436/438 | 436/438 | 0 |

### Diversity

| Metric | Baseline | Post | Δ |
|---|---:|---:|---:|
| Theme entropy | 2.999 bits | 2.996 bits | −0.003 |
| Phase entropy | 1.810 bits | 1.778 bits | −0.032 |

## Analysis

### What improved

- **Signal coverage jumped significantly.** Title hits +30 and lyrics hits +30.
  The Traditional-only `THEME_VOCAB` now correctly matches the Traditional
  Chinese lyrics in the catalog. 30 songs that previously had zero title/lyrics
  theme hits now have at least one hit — these were songs whose keywords
  existed only in Simplified form (e.g., `宝血`→`寶血`, `传扬`→`傳揚`).
- **Zero-theme songs remained at 0.** All 438 songs still get a theme
  assignment (via title, lyrics, or embeddings).
- **Phase 4 (奉獻) grew** from 60→68, moving out of "underrepresented" status.

### What did not improve (or worsened slightly)

- **Phase 3 (敬拜) dominance increased** from 55.5%→56.8%. The 敬拜 theme gained
  12 songs. This is because the Traditional keyword `榮耀` (previously `荣耀`)
  now matches properly, and 敬拜 is already the most keyword-heavy theme in
  worship music.
- **Phase entropy decreased** slightly (1.810→1.778), indicating slightly worse
  phase balance.
- **讚美 and 信心 lost songs** (−8 and −10 respectively). Some songs previously
  classified as 讚美 or 信心 are now classified as 敬拜 — the Traditional
  keywords for 敬拜 match more strongly now.
- **Underrepresented themes worsened**: 認罪 5→3, 復興 3→1. These themes have
  very few matching songs in the catalog regardless of Simplified/Traditional.

### Root cause

The Traditional conversion fixed the **signal coverage** problem (more songs
now have theme hits), but it did not fix the **diversity** problem. In fact, it
slightly worsened diversity because 敬拜 is naturally dominant in Chinese
worship music, and proper keyword matching amplifies that dominance. The
embedding anchors were not regenerated (keys renamed only, vectors unchanged),
so embedding classification behavior is identical between the two runs.

## Recommendations (Part 3 evaluation)

Based on these results, the most impactful Part 3 recommendations are:

### 1. Recommendation A: Expand THEME_VOCAB (HIGH PRIORITY)

The underrepresented themes (認罪 3, 差遣 3, 復興 1) have very few keyword
matches. Adding more Traditional Chinese keywords per theme (e.g., `稱頌`,
`頌讚` for 讚美; `罪孽`, `洗淨` for 認罪; `覺醒`, `澆灌` for 復興) would
help more songs match these themes instead of defaulting to 敬拜.

### 2. Recommendation C: Lower Embedding Influence When Title/Lyrics Agree (MEDIUM PRIORITY)

敬拜 gained 12 songs partly because the embedding anchors (unchanged) pull
toward 敬拜. When title and lyrics both agree on a non-敬拜 theme, the
embedding contribution should be reduced to prevent overriding the keyword
signal.

### 3. Recommendation B: Multi-Phase Tagging (MEDIUM PRIORITY)

With Phase 3 at 56.8%, allowing borderline songs to appear in multiple phase
slots would help the beam search find candidates for underrepresented phases
(2 and 5).

### 4. Recommendation D: Worship Fallback (LOW PRIORITY)

Zero-theme songs are already 0, so the worship fallback would have minimal
impact on this catalog. It may help for future songs with unusual vocabulary.

### 5. Recommendation E: Normalize Embedding Anchor Texts (MEDIUM PRIORITY)

The embedding anchors were not regenerated (only key-renamed). Regenerating
with longer, richer Traditional Chinese descriptions would improve embedding
classification quality. This requires `SOW_EMBEDDING_API_KEY` /
`SOW_EMBEDDING_BASE_URL` to be available.

## Next Steps

1. Implement Recommendation A (expand THEME_VOCAB) — lowest risk, highest
   expected impact on underrepresented themes.
2. Implement Recommendation C (dynamic fusion weights) — medium complexity,
   targets the 敬拜 dominance directly.
3. Re-run `--only-evaluate-pool-enrichment` to measure improvement.
4. Regenerate `theme_anchors.json` with longer anchor texts (Recommendation E)
   when the embedding endpoint is available.
