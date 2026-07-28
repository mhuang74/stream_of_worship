# Database Bandwidth Analysis: Songset Constructor Contribution

**Date:** 2026-07-28
**Status:** Investigation Complete
**Context:** The reduce-database-network-transfer spec (`specs/reduce-database-network-transfer-v1.md`) identifies ~5.5 GB network transfer against 0.17 GB stored data (32× amplification). This report investigates whether the POC songset constructor (`lab/poc-scripts/poc/songset_constructor/`) is a significant contributor.

## Key Finding

**Yes, with a single user running 80–100 constructions in 3 weeks, the songset constructor is the dominant contributor to the 5.5 GB problem.**

The root cause is **serializing 1536-dim pgvector embeddings as text** (`vector::text` cast) and transferring them over the wire — both song-level embeddings and (especially) every line-level embedding.

## Data Transfer Per Run

The constructor makes exactly **2 database queries** per run, both always executed regardless of mode (`--no-llm` vs `--llm`, or `--only-evaluate-pool-enrichment`).

### Query 1: Pool Query

Fetches `JOIN` of `songs` (24 columns) + `recordings` (34 columns) + `song_embedding` (embedding::text, model_version):

```
SELECT s.id, s.title, s.title_pinyin, s.composer, s.lyricist,
       s.album_name, s.album_series, s.musical_key, ...
       r.content_hash, r.hash_prefix, r.song_id, ...
       se.embedding::text AS song_embedding_text,
       se.model_version AS song_embedding_model
FROM songs s
JOIN recordings r ON s.id = r.song_id
LEFT JOIN song_embedding se ON se.song_id = s.id
WHERE r.visibility_status IN ('published', 'review')
  AND ...
ORDER BY s.title
LIMIT %s
```

| Component | Per Row | ×200 rows (default) | ×500 rows |
|-----------|---------|--------------------:|----------:|
| 24 song columns (incl. `lyrics_raw`, `lyrics_lines`, `sections` — large TEXT) | ~3 KB | | |
| 34 recording columns (incl. `beats`, `downbeats`, `key_candidates`, 4 URL columns) | ~2 KB | | |
| `song_embedding::text` — 1536-dim float32 vector → ASCII text | **~16.5 KB** | | |
| **Total pool row** | **~22 KB** | **~4.3 MB** | **~10.7 MB** |

### Query 2: Line Embedding Query

Fetches ALL line embeddings for every song in the pool, each as `embedding::text` (~16.5 KB):

```sql
SELECT song_id, line_index, embedding::text
FROM song_line_embedding
WHERE song_id = ANY(%s)
ORDER BY song_id, line_index
```

This is the **dominant cost** by a wide margin:

| Lines per song | ×200 songs | ×500 songs |
|---------------|-----------:|-----------:|
| 15 (conservative) | **48.2 MB** | **120.6 MB** |
| 20 (typical: 2–3 verses + chorus) | **64.5 MB** | **161.3 MB** |
| 30 (3 verses + chorus + bridge) | **96.7 MB** | **241.9 MB** |

### Cost Breakdown Per Run (at 20 lines/song)

| Pool limit | Pool query | Line embeddings | **Total per run** |
|-----------|-----------:|----------------:|------------------:|
| 200 (default) | 4.3 MB | 64.5 MB | **68.8 MB** |
| 500 (common recipe flag) | 10.7 MB | 161.3 MB | **172.0 MB** |

## Projected Over 80–100 Runs

| Pool limit | Per run | **80 runs** | **100 runs** |
|-----------|-------:|-----------:|------------:|
| 200 | 68.8 MB | **5.4 GB** | **6.7 GB** |
| 500 | 172.0 MB | **13.4 GB** | **16.8 GB** |

With the default `--pool-limit 200`, **80–100 runs alone account for 5.4–6.7 GB** — essentially the entire 5.5 GB problem reported in the spec.

## Why the Vector Serialization Is So Expensive

A 1536-dim `float32` pgvector, when cast to text via `::text`, produces:

```
[0.12345678,0.23456789,0.34567890,...,0.98765432]
```

- Each float: ~10 characters + comma = ~11 chars
- Total: 2 (brackets) + 1536 × 11 = **~16,898 characters = ~16.5 KB**

Compare to reasonable alternatives:

| Format | Size per vector | Savings vs text |
|--------|---------------:|----------------:|
| ASCII text (`::text` cast) | **~16.5 KB** | — |
| Binary float32 (1536 × 4 bytes) | **6.0 KB** | 64% |
| Half-precision float16 (1536 × 2 bytes) | **3.0 KB** | 82% |
| 12 theme scores (12 × 4 bytes) | **48 bytes** | 99.7% |

The line embedding query for 200 songs × 20 lines = 4,000 rows, each carrying a full 16.5 KB of text = **64.5 MB** of text that is immediately parsed back into numpy arrays on the Python side (`parse_pgvector_text`), only to compute cosine similarity against 12 theme anchors.

## Other Production Sources (With 1 User)

For comparison, with only 1 user (no Android app polling, limited webapp page loads):

| Source | Monthly estimate | Notes |
|--------|----------------:|-------|
| Songset constructor (80–100 runs) | **5–7 GB** | Dominant |
| Render worker jobs | ~50–200 MB | A few render jobs, each ~15 KB × 15 queries |
| Webapp page loads | ~50–100 MB | Song listing, songset detail pages |
| **Total** | **~5.5 GB** | Matches spec |

## Recommendations for `specs/reduce-database-network-transfer-v1.md`

### 1. Add Songset Constructor to Phase 1 (High Impact)

The spec's Phase 1 currently covers only render worker changes. The songset constructor should be added as a Phase 1 item since it's the dominant source.

**Option A — Move theme classification into PostgreSQL (Highest Impact)**

Compute cosine similarity against the 12 theme anchors using pgvector's `<#>` operator directly in SQL. Return only the 12 theme scores (48 bytes × 12 = 576 bytes) instead of transferring all 1536-dim vectors (~16.5 KB each). This eliminates both the `song_embedding::text` and line embedding serialization costs.

```python
# Instead of:
#   fetching all vectors to Python, then compute cosine in numpy
# Do in SQL:
SELECT s.id,
       (1 - (se.embedding <=> anchor_讚美::vector)) AS theme_讚美,
       (1 - (se.embedding <=> anchor_感恩::vector)) AS theme_感恩,
       ...
FROM songs s
JOIN song_embedding se ON se.song_id = s.id
```

Per-run transfer drops from **~69 MB to ~1 MB** (just song + recording columns).

**Option B — Skip line embeddings (Medium Impact, Low Effort)**

Line embeddings contribute only 15% weight to the fused theme score. Dropping them cuts per-run transfer by **~64.5 MB (94% of data)** while only reducing embedding-based theme accuracy by the line-embedding fraction (15% of the 40% embedding weight = 6% of the total fused score).

**Option C — Cache the enriched pool (Medium Impact)**

Once `enrich_pool` completes, the pool (carrying `themes`, `phase`, etc. as small data) could be cached to a local file. Subsequent runs with the same `--pool-limit` / `--album-series` filter would skip the DB entirely.

### 2. Update Measurements in the Spec

The spec's "Before Implementation" section should note that the songset constructor is measurable via:
- Number of `song_line_embedding` queries (easily visible in Neon query log)
- Pool `LIMIT` values used across runs
- Average lines per song enrolled

### 3. Cost-Benefit of Each Option

| Option | Effort | Bandwidth Reduction | Complexity |
|--------|--------|-------------------:|-----------|
| In-DB theme classification | High | ~99% per run | Medium (requires pgvector anchor table or CTE) |
| Skip line embeddings | Low | ~94% per run | Low (conditional fetch in `fetch_catalog_pool`) |
| Cache enriched pool | Low | ~94% per run (subsequent runs) | Low |
| In-DB line theme scoring | Medium | ~99.7% per run | Medium |

## Appendix: Full Column Lists

### Songs (24 columns)

`id, title, title_pinyin, composer, lyricist, album_name, album_series, musical_key, musical_key_root, musical_key_mode, musical_key_start_root, musical_key_end_root, musical_key_start_pitch_class, musical_key_end_pitch_class, musical_key_parse_status, **lyrics_raw**, **lyrics_lines**, **sections**, source_url, table_row_number, scraped_at, created_at, updated_at, deleted_at`

Large TEXT columns bolded.

### Recordings (34 columns)

`content_hash, hash_prefix, song_id, **original_filename**, file_size_bytes, imported_at, **r2_audio_url**, **r2_stems_url**, **r2_lrc_url**, duration_seconds, tempo_bpm, musical_key, musical_mode, key_confidence, key_algorithm_version, key_score_margin, key_window_agreement, **key_candidates**, key_detected_at, loudness_db, **beats**, **downbeats**, sections, embeddings_shape, analysis_status, analysis_job_id, lrc_status, lrc_job_id, created_at, updated_at, **youtube_url**, visibility_status, download_status, deleted_at`

Large TEXT/URL columns bolded.
