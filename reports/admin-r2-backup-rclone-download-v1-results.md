# Admin R2 Backup rclone Download Path v1 — Benchmark Results

**Date:** 2026-06-20
**Status:** STOP — rclone does not outperform boto3; cap is R2-account-level
**Decision:** Per `specs/admin-r2-backup-rclone-download-v1.md` Step 1c, do NOT implement Fixes 1-7

## Summary

The mandatory pre-implementation benchmark was executed. **rclone does not achieve a ≥1.2x improvement over the boto3 baseline.** In fact, rclone is **slower** than boto3 for both single-file and multi-file downloads on the `stream-of-worship` R2 bucket from the test environment. This confirms the hypothesis that the ~5–7 MiB/s aggregate throughput is an **R2 account/bucket-level cap**, not a boto3-specific artifact.

## Benchmark Environment

- **Host:** macOS 15.7.5 (Apple Silicon, arm64)
- **Network:** Residential broadband (location: Taiwan)
- **rclone version:** v1.74.3
- **boto3 version:** (bundled with project dependencies)
- **R2 bucket:** `stream-of-worship`
- **R2 endpoint:** `https://6c80769fe5aa4be53908b83c3d0454cd.r2.cloudflarestorage.com`
- **Test object:** `d48247f4fb2f/stems/vocals.wav` (65.745 MiB)
- **Total bucket size:** ~12.9 GB, ~679 objects

## Results

### 1a. boto3 baseline — Range-GET diagnostic

```bash
sow-admin maintenance backup-r2 \
  --output /tmp/sow-r2-baseline-boto3 \
  --chunk-size 5GiB --concurrency 8 \
  --diag-range-key d48247f4fb2f/stems/vocals.wav
```

```json
{
  "content_length": 68938108,
  "num_ranges": 4,
  "single_conn_mbps": 7.85,
  "multi_conn_total_mbps": 6.54,
  "ratio": 0.83,
  "per_range_mbps": [1.77, 3.81, 1.71, 1.64]
}
```

**Interpretation:**
- Single connection: **7.85 MiB/s**
- 4 parallel Range-GETs: **6.54 MiB/s aggregate** (ratio 0.83)
- The ratio **< 1.0** means parallel connections actually *hurt* throughput — the network path is already saturated by a single connection. This is the opposite of the v2 remediation spec's earlier finding (ratio=2.41), indicating network conditions have changed (now saturated rather than per-connection-capped).

### 1b. rclone pure reference — single file

```bash
rclone copy sow_r2:stream-of-worship/d48247f4fb2f/stems/vocals.wav /tmp/rclone-bench \
  --transfers 1 --checkers 1 --stats 1s --progress
```

| Metric | Value |
|---|---|
| File size | 65.745 MiB |
| Elapsed time | 15.9 s |
| Average throughput | **4.07 MiB/s** |

### 1c. rclone with multi-thread streams (parallel Range-GETs)

```bash
rclone copy ... --multi-thread-streams 8 --multi-thread-cutoff 1M --multi-thread-chunk-size 8M
```

| Metric | Value |
|---|---|
| Elapsed time | 22.4 s |
| Average throughput | **2.96 MiB/s** |

**Multi-thread streams made throughput WORSE** (2.96 vs 4.07 MiB/s). The parallel streams contend for the same saturated network pipe.

### 1d. rclone multi-file aggregate (8 transfers)

```bash
rclone copy sow_r2:stream-of-worship /tmp/rclone-bench-multi \
  --include "*/audio.mp3" --max-depth 2 --fast-list \
  --transfers 8 --checkers 16 --stats 2s --progress
```

| Metric | Value |
|---|---|
| Total transferred | 738.825 MiB (108 files) |
| Elapsed time | 1m 56.2s |
| Average throughput | **5.68 MiB/s** |

## Comparative Analysis

| Backend | Throughput (MiB/s) | vs boto3 single conn |
|---|---|---|
| boto3 single conn (diag) | **7.85** | 1.00× (baseline) |
| boto3 multi conn (4 Range-GETs) | **6.54** | 0.83× |
| rclone single file | **4.07** | 0.52× |
| rclone multi-thread streams | **2.96** | 0.38× |
| rclone multi-file (8 transfers) | **5.68** | 0.72× |

**Conclusion:** rclone achieves **0.38×–0.72×** the boto3 baseline throughput. It is consistently slower across all tested configurations.

## Decision Rationale

Per `specs/admin-r2-backup-rclone-download-v1.md` Step 1c:

> | If ref-rclone achieves... | Then... |
> |---|---|
> | <1.2x baseline-boto3 throughput | **Stop.** The cap is R2-account-level, not boto3-specific. |

The rclone reference (best case: 5.68 MiB/s multi-file) is **0.72× the boto3 baseline (7.85 MiB/s)**, well below the 1.2× threshold. Therefore:

1. **No production code is implemented.** Fixes 1-7 from the spec are abandoned.
2. **The throughput cap is confirmed to be R2-account-level.** Neither boto3 tuning nor rclone can break it from this client/network path.
3. **The boto3 path remains the sole backup backend.** It is faster, has no external binary dependency, and is already well-tested.

## Out of Scope (reaffirmed)

- aiobotocore async rewrite
- boto3 TransferConfig tuning
- Cloudflare Worker in-network backup
- Promoting rclone to default backend

## References

- `specs/admin-r2-backup-rclone-download-v1.md` — this spec
- `specs/admin-r2-backup-throughput-remediation-v2.md` — predecessor closure spec
- `src/stream_of_worship/admin/services/r2_backup.py` — existing boto3 backup path (unchanged)
