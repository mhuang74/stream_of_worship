# Plan: BPM Agreement Report — v5 Lineage + Consensus + CPS×BPM Crosstab

## Overview

Rewrite `lab/poc-scripts/bpm_agreement_report.py` to replace the pairwise
method-agreement matrix (which becomes noisy and redundant at 5 methods → 10
pairs) with three focused analysis sections that answer sharper questions:

- **Section B — v5 Lineage Delta**: Did the CPS-derived prior (prod_v5)
  improve accuracy over the octave-guard heuristic (prod_v4)?
- **Section C — Consensus & Disagreement**: Across all 5 methods, which songs
  are unambiguous vs. contested?
- **Section D — CPS ↔ BPM Match**: Does lyrical pacing (Characters-Per-Second)
  predict musical tempo, per method? Crosstab in the text-table format
  already produced by `compare_bpm_libraries.py:print_crosstab`.

The existing pairwise functions (`compute_pairwise_metrics`,
`chart_scatter_matrix`, `chart_bland_altman`, `chart_box_octave`,
`chart_tolerance_bars`, `chart_diff_histograms`) are removed.

## Target Data

- **CSV**: `lab/poc-scripts/output/bpm_comparison_20260708_114300.csv`
  - 98 data rows, 5 methods, all songs have LRC.
  - Columns of interest: `librosa_bpm`, `madmom_bpm`, `beatnet_bpm`,
    `prod_v4_bpm`, `prod_v5_bpm`, `stored_bpm`, `cps`, `cps_bucket`,
    `prod_v5_prior`.
- **Output**: `lab/poc-scripts/output/bpm_agreement_report.html` (path unchanged).

## Key Data Findings (verified)

- `stored_bpm` is independent ground truth (5-decimal precision, from the
  analysis-service pipeline). No method matches it exactly.
- `librosa_raw == prod_v4` on **all 98 songs** — the v4 octave guard never
  fires in this dataset. They are numerically identical.
- `prod_v5` diverges from v4 on **65/98 songs** (where the CPS prior is
  active; the remaining 33 fall back to `start_bpm=80` and match v4).
- Only `madmom` and `beatnet` are truly independent algorithm families.
- All 98 songs have `lrc_available=true`.
- CPS bucket distribution (nominal cuts 1.5 / 2.8): **74 slow / 23 moderate /
  1 fast**. The "fast" row is sparse — interpret with caution.
- K-means empirical CPS buckets produce a more balanced **53 / 44 / 1** split.

## Design Decisions (confirmed with user)

| Decision | Choice |
|---|---|
| Method set | **All 5 methods** (librosa, madmom, beatnet, prod_v4, prod_v5) — keep librosa_raw even though ≡ prod_v4 |
| Sections | **B (v5 lineage) + C (consensus) + D (CPS×BPM crosstab)** |
| Octave handling (B, C) | **Both raw + octave-corrected metrics** side-by-side |
| Octave anchor (C consensus) | **[60, 160)** fixed window |
| Section D — CPS bucket scheme | **Both nominal (1.5/2.8) and k-means empirical** side-by-side |
| Section D — BPM bucket scheme | **Global k-means** on combined 490-point set (5 methods × 98 songs), single set of cutoffs applied to all method tables |
| Section D — BPM values | **Raw BPM** (no octave normalization in the crosstab) |
| Section D — table format | Match `compare_bpm_libraries.py:print_crosstab` text-table layout exactly |

## Config & Docstring (`bpm_agreement_report.py:1-31`)

- `CSV_PATH` → `bpm_comparison_20260708_114300.csv`
- `METHOD_COLS`: add `"prod_v4": "prod_v4_bpm"` and `"prod_v5": "prod_v5_bpm"`
  (5 entries total).
- New constants:
  - `STORED_COL = "stored_bpm"`
  - `CPS_COL = "cps"`
  - `CPS_BUCKET_COL = "cps_bucket"`
  - `OCTAVE_ANCHOR = (60, 160)`
  - `CPS_NOMINAL_CUTS = (1.5, 2.8)`
  - `BPM_NOMINAL_CUTS = (90.0, 120.0)`
  - `RANDOM_STATE = 42`
- Docstring: describe the three sections; note `librosa_raw ≡ prod_v4` on this
  dataset (octave guard never fired); note v5's high CPS-diagonal is *partly
  by construction* since it uses the CPS prior.

## New Helpers (replace `compute_pairwise_metrics`)

- `octave_normalize(bpm, lo, hi) -> tuple[float, int]` — shift by 2^k into
  `[lo, hi)`; returns `(normalized_value, k)`. For values already in range,
  returns `(bpm, 0)`.
- `octave_corrected_diff(a, b)` — keep existing (used in B).
- `cps_value(row) -> Optional[float]` — parse `cps` column (`"" → None`).
- `cps_bucket_nominal(cps) -> Optional[str]` — "slow"/"moderate"/"fast" via
  `CPS_NOMINAL_CUTS`; None if missing.
- `bpm_bucket_nominal(bpm) -> Optional[str]` — similar via
  `BPM_NOMINAL_CUTS`.
- `kmeans_3(arr_1d, random_state=RANDOM_STATE) -> tuple[np.ndarray, np.ndarray, list[float]]`
  — returns labels, sorted cluster centers, and the 2 derived cutoffs (midpoints
  between adjacent sorted centers). sklearn is already in the dep tree of
  `compare_bpm_libraries.py`.
- `cps_bucket_kmeans(cps, cutoffs) -> str` and
  `bpm_bucket_kmeans(bpm, cutoffs) -> str` — bucket using supplied empirical
  cutoffs.
- `error_vs_stored(vals, stored) -> dict` — returns `raw_diffs`,
  `octave_diffs`, and aggregate stats (MAE/RMSE/median/min/max) for both
  families.
- `per_song_consensus(data, methods) -> dict` — for each song, octave-normalize
  each method's BPM to [60,160), compute `median`, `min`, `max`,
  `radius = max - min`, and `outlier_method` (method furthest from median).

## Section B — v5 Lineage Delta

**Research question**: Does the CPS-derived prior (v5) improve accuracy over
the octave-guard heuristic (v4), measured against `stored_bpm`?

### Charts

1. `chart_v5_delta_histogram(data)` — overlay histograms of
   `err_v5_raw − err_v4_raw` and `err_v5_oct − err_v4_oct`. Vertical line at 0.
   Negative = v5 better.
2. `chart_v5_error_scatter(data)` — x = v4 octave-corrected error,
   y = v5 octave-corrected error, per-song points colored by CPS bucket
   (k-means). y=x reference line. Quadrants labelled "v5 wins" (below diagonal)
   / "v5 loses" (above).
3. `chart_v5_cps_bar(data)` — grouped bars: per CPS bucket (k-means), mean
   Δerror (octave-corrected) and v5 win-rate %.

### Tables

- **Win/Loss/Tie summary**: columns = version (raw / octave-corrected),
  v5 wins, v5 loses, ties, mean Δerror.
- **CPS-bucket stratified** (k-means buckets): per bucket — n songs,
  v4 octave-MAE, v5 octave-MAE, mean Δerror, win-rate.
- **Top octave-flip cases** (≤10): songs where `v5_bpm / v4_bpm ≈ 2 or 0.5`
  (v5 picked a different octave than v4). Columns: song, CPS bucket (k-means),
  v4_bpm, v5_bpm, stored_bpm, v4_error (oct-corrected), v5_error (oct-corrected),
  v5_prior.

## Section C — Consensus & Disagreement

**Research question**: Across all 5 methods, which songs are unambiguous vs.
contested?

### Charts

1. `chart_consensus_radius_hist(data)` — histogram of per-song `radius`
   (max − min of octave-normalized BPMs). Vertical line at median radius.
2. `chart_method_deviation_box(data)` — box plot: per-method distribution of
   `|normalized_bpm − consensus_median|`. One box per method.
3. `chart_disputed_strip(data)` — strip plot for top-10 most-disputed songs:
   horizontal lines per song; per-method normalized BPM points layered;
   stored_bpm marker as red X.

### Tables

- **Per-method deviation summary**: method, mean abs deviation (raw + octave),
  % songs within ±5 BPM of consensus.
- **Top 10 most-disputed songs**: song, all 5 method BPMs (normalized + raw),
  stored, consensus median, radius, identified outlier method.
- **Top 10 most-agreed songs**: same columns, sorted ascending by radius
  (sanity reference — confirms the field agrees on easy songs).

## Section D — CPS ↔ BPM Match

**Research question**: Does lyrical pacing (CPS) predict musical tempo, per
method? Crosstab validates the CPS prior hypothesis.

### Bucketing

- **CPS buckets — two schemes shown side-by-side:**
  1. **Nominal**: cuts at 1.5 and 2.8 (as defined in
     `compare_bpm_libraries.py:cps_bucket_label`).
  2. **K-means empirical**: `KMeans(n_clusters=3, random_state=42)` on the
     98 per-song CPS values; cutoffs = midpoints between sorted centers.
- **BPM buckets — global k-means**: `KMeans(n_clusters=3, random_state=42)`
  on the combined 490-point set (5 methods × 98 songs) → single set of 3
  cutoffs applied to all method tables. Headers show empirical cutoffs
  (e.g., `BPM<72 / 72–118 / >118`).
- **BPM values**: raw (no octave normalization in the crosstab).

### Tables (10 total = 5 methods × 2 CPS schemes)

For each (method, CPS scheme) combination, produce a crosstab matching the
sample format exactly:

```
=== CPS bucket × BPM bucket (<method>, <CPS scheme>) ===
                  BPM<72  72–118    >118
  Slow              ...
  Moderate          ...
  Fast               ...
  Diagonal mass: X/98 = Y%  (CPS-BPM agreement)
```

A summary row at the bottom of each scheme shows the BPM cutoffs used and the
CPS cutoffs for that scheme.

### Charts

1. `chart_cps_bpm_diagonal_bars(data)` — grouped bar chart, one cluster per
   method, showing diagonal agreement % under each CPS scheme (nominal vs.
   k-means). Highlights that v5 reaches high agreement by construction.
2. `chart_cps_vs_bpm_scatter(data)` — 5 panels (one per method). X = CPS
   continuous, Y = raw BPM. Vertical dashed lines at nominal CPS cuts
   (1.5, 2.8). Horizontal dashed lines at empirical BPM cuts. Points colored
   by CPS bucket (k-means).

### Caveats (amber callout at section top)

- v5 uses the CPS prior *by construction*, so its high diagonal agreement is
  **not independent evidence** of the CPS-tempo correlation. The other 4
  methods provide the independent test.
- Distribution is skewed: only 1 fast-CPS song — interpret the "fast" row
  cautiously.
- `librosa_raw` and `prod_v4` produce identical BPMs on this dataset, so
  their crosstabs are byte-identical.

## HTML Scaffolding

- Intro paragraph: 5 methods × 98 songs, three analysis sections.
- Three H2 sections (B, C, D) with charts above tables.
- Existing `_html_table`, `_make_fig`, `_save_base64`, CSS, footer retained.
- Existing `.note` CSS class (line 389) reused for section-level caveats.
- The old amber "no ground truth" note is removed (we now treat `stored_bpm`
  as reference for Section B; Section D uses lyrical pacing as an independent
  signal).

## `main()` Refactor

1. Load CSV → 98 rows.
2. Compute global k-means BPM cutoffs once (490-point input).
3. Compute k-means CPS cutoffs once (98-point input).
4. Compute Section B artifacts (charts + tables).
5. Compute Section C artifacts (charts + tables).
6. Compute Section D artifacts (10 crosstabs + 2 charts).
7. Assemble `charts` and `tables` dicts (keyed by section), pass to
   `build_html_report`.
8. Write HTML.

## Removed

- `compute_pairwise_metrics`
- `chart_scatter_matrix`
- `chart_bland_altman`
- `chart_box_octave`
- `chart_tolerance_bars`
- `chart_diff_histograms`
- Their HTML assembly in `build_html_report`.

## Verification

- Run: `uv run --project ops/admin-cli --python 3.11 --extra admin python
  lab/poc-scripts/bpm_agreement_report.py` (admin extra pulls matplotlib +
  numpy + scipy + sklearn already in dep tree).
- Stdout headline stats: v5 win-rate, median consensus radius, per-method
  diagonal % under nominal vs. k-means CPS.
- HTML file size > 500 KB.
- Spot-check crosstabs:
  - `prod_v5` under **nominal** CPS should hit ~100% diagonal (all 74 slow
    land in BPM<90, all 23 moderate land in 90–120, 1 fast lands >120) — *by
    construction*.
  - `prod_v5` under **k-means** CPS will be lower because k-means relabels
    some nominally-moderate songs as "slow" — produces off-diagonal cells
    (matches user's sample: 53/44/1 split, ~77–79% diagonal).
  - `librosa` and `prod_v4` crosstabs are byte-identical (validate).
- Spot-check the octave-flip table (Section B): should include `Here I Bow`
  (v4=92.3, v5=68.0, stored=92.3) — v5 picked the half-time octave.

## Out of Scope

- No changes to `compare_bpm_libraries.py` (the CSV generator).
- No adoption of prod_v5 into the production analyzer (research POC only).
- No ground-truth accuracy leaderboard section (user explicitly excluded).
- No pairwise method-agreement matrix (replaced by the three sections above).
