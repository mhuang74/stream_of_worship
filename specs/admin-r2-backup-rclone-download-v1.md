# Admin R2 Backup rclone Download Path v1

**Service:** Admin CLI (`src/stream_of_worship/admin/`)
**Status:** Draft (plan only — no implementation)
**Created:** 2026-06-20
**Predecessor specs:**
- `specs/admin-r2-backup-throughput-remediation-v1.md` (32-worker bump, abandoned)
- `specs/admin-r2-backup-throughput-remediation-v2.md` (revert + investigation closure)
**CLI command:** `sow-admin maintenance backup-r2`

## Summary

Re-open the R2 backup throughput investigation that was closed as "not feasible
within the local Python CLI architecture" in
`admin-r2-backup-throughput-remediation-v2.md`. That closure was correct **for
the boto3-with-ThreadPoolExecutor architecture** — it does not generalize to
the rclone option the user has now authorized.

This spec introduces rclone as an external binary dependency for the
**download phase only**. The inventory, metadata-HEAD, manifest-build, tar
archive, verify, and restore paths remain unchanged. The Python codebase keeps
`boto3` for everything except the bulk download loop.

The success metric for this spec is **parity with `rclone copy`'s default
behavior** on the `stream-of-worship` R2 bucket, measured by end-to-end backup
runtime (no fixed MiB/s target). The closure spec's `<10 min` goal is **not**
re-opened by this spec; it is left in the "Out of Scope" bucket per the user's
direction.

**Mandatory benchmark step:** the implementation phase must run the existing
`--diag-range-key` diagnostic against both the current boto3 path and the new
rclone path, plus a baseline `rclone copy` reference benchmark, before any
production code lands. This confirms (or refutes) that the ~7 MiB/s ceiling is
an R2 account-level cap rather than a boto3-specific artifact.

## Background

### Critical fact: the code is NOT using the r2.dev endpoint

Two of the four candidate options the user originally listed are non-issues:

| User's option | Resolution |
|---|---|
| "confirmation that r2.dev endpoint is bandwidth-limited" | **N/A.** Zero occurrences of `r2.dev` in the codebase. The backup has always used the S3-compatible endpoint `https://<account-id>.r2.cloudflarestorage.com`. See `src/stream_of_worship/admin/services/r2.py:95-111` and `examples/sow-admin-config.toml:27-28`. |
| "use S3-compatible API instead" | **Already doing this.** `R2Client` constructs a `boto3.client("s3", endpoint_url=...)`. No change needed. |
| "use Cloudflare REST API instead" | **Not viable.** The Cloudflare REST API (`api.cloudflare.com`) only manages bucket/object **metadata**; it cannot stream object content for bulk download. Rejected. |
| "use rclone" | **Pursued.** This spec. rclone uses the same S3-compatible API as boto3 but has a Go-implemented, heavily-tuned multipart downloader (parallel ranges per object, global transfer pool, automatic ETag verification). |

### What the boto3 backup path does today

The current `backup-r2` command (`maintenance.py:642-784`) calls
`write_backup` (`r2_backup.py:884-1203`), which:

1. Builds an inventory via boto3 paginator (`build_inventory`).
2. Submits each object as a `ThreadPoolExecutor` future.
3. Each worker calls `R2Client.get_object_stream` (raw `boto3 get_object`).
4. Streams body through `HashingReader` (SHA-256 + MD5) into a tempfile using a
   1 MiB `COPY_BUFFER_SIZE`.
5. Validates ContentLength, ETag, and (for non-multipart ETags) MD5 body checksum.
6. The main thread writes each completed tempfile into the current chunked tar
   archive with a sanitized member name `objects/{idx:012d}.bin`.
7. Optionally spot-checks 5% of objects via `head_object`.
8. Writes a `manifest.json` v4 with per-object: `key`, `size`, `sha256`, `etag`,
   `content_type`, `cache_control`, `content_disposition`, `content_encoding`,
   `metadata`.
9. Renames `<output>.part/` → `<output>/` atomically.

### Why parallelism didn't help (recap)

The v2 remediation spec confirmed via `--diag-range-key` that:

- 4 parallel Range-GETs to **one** object yield `ratio=2.41` — partial scaling,
  not linear. Each connection's throughput *dropped* from 0.34 → 0.21 MiB/s.
- 32 workers gave 5.0 MiB/s aggregate vs 7.3 MiB/s at 8 workers — worker count
  past ~8 divides a fixed aggregate cap thinner.
- Signatures match an **R2 account/bucket-level throughput cap** (~5–7 MiB/s).

**Hypothesis we are now testing:** boto3 opens one HTTP/2 stream per object
(per-worker). rclone opens N parallel Range-GETs per object across a global
worker pool, pipelines more aggressively, and may more efficiently saturate an
aggregate cap. **Either rclone breaks the ceiling or we confirm it is
architecturally R2-account-level** — both outcomes are valuable.

### What rclone gives us that boto3 doesn't

Per the rclone S3 backend documentation (`https://rclone.org/s3/#cloudflare-r2`):

| Capability | boto3 (current) | rclone |
|---|---|---|
| Native `provider=Cloudflare` config | n/a (manual endpoint) | First-class (`--s3-provider Cloudflare`) |
| Env-var-based config (no `rclone.conf` file) | n/a | `RCLONE_CONFIG_<REMOTE>_<KEY>` |
| Parallel Range-GETs per object | manual in diagnostic only | automatic per `--s3-download-concurrency` + `--s3-chunk-size` |
| Global transfer pool | one `ThreadPoolExecutor` | `--transfers N`, `--checkers M` |
| `--fast-list` (LIST one call per 1000 objects) | uses paginator (equivalent) | equivalent, but flag is explicit |
| Automatic ETag verification on download | manual in `HashingReader` | automatic (`rclone check` and per-object) |
| Tunable `--bwlimit` | none | for throttling if needed |
| Resumable transfers (`--inplace`, delta checks) | none | automatic by size+mtime |

## Decision & Rationale

**Approach: rclone-subprocess download phase, keeping the boto3 manifest
pipeline unchanged.**

### Pipeline comparison

```
CURRENT (boto3):
  inventory → [boto3 get_object × N workers] → tempfile → tar + manifest → atomic rename

PROPOSED (rclone):
  inventory → [rclone copy → staging dir] → walk staging → tar + manifest → atomic rename
```

### Why split phases (rather than full rclone replacement)

- Manifest format is `MANIFEST_VERSION = 4` and includes R2-side metadata
  (`content_type`, `cache_control`, `cache_disposition`, `content_encoding`,
  per-object `metadata`). rclone's local-side download does not capture these
  headers; we still need a `head_object` per object.
- Existing `verify_archive`, `restore_from_archive`, and `plan_restore` logic
  is unchanged — patches localized to `write_backup`.
- Single subprocess invocation gives us rclone's whole transfer pool tuning
  for free; no need to reimplement its connection manager in Python.

### Why env-var config (no `rclone.conf` file)

- Credentials already live in `SOW_R2_ACCESS_KEY_ID` /
  `SOW_R2_SECRET_ACCESS_KEY` env vars per `r2.py:86-93`. Reuse them.
- Avoids writing a config file with secrets to disk.
- Per rclone S3 docs, env vars of the form
  `RCLONE_CONFIG_<REMOTE>_<KEY>` are loaded as a remote named `<REMOTE>`
  (lowercased). No file required.

## Implementation Plan

### Fix 1: New `RcloneClient` service module (P0)

**Goal:** Encapsulate rclone binary detection, env-based config, and the
`copy` command builder. Keep the rest of the codebase oblivious to rclone.

**File (new):** `src/stream_of_worship/admin/services/rclone.py`

Skeleton:

```python
"""rclone subprocess wrapper for R2 bulk download.

rclone is faster than boto3+ThreadPoolExecutor for bulk S3-compatible
download because it pipelines parallel Range-GETs per object across a
global transfer pool. This module wraps the subprocess invocation; the
boto3 path remains the default until --backend=rclone is explicitly used.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from stream_of_worship.admin.services.r2 import R2Client

logger = logging.getLogger(__name__)

REMOTE_NAME = "sow_r2"


@dataclass
class RcloneResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float


class RcloneNotAvailableError(Exception):
    """Raised when rclone is not on PATH."""


class RcloneClient:
    """Builds and runs `rclone copy` against an R2 bucket via env-var config."""

    def __init__(
        self,
        r2_client: R2Client,
        transfers: int = 8,
        checkers: int = 16,
        fast_list: bool = True,
        progress: bool = True,
    ) -> None:
        self._r2 = r2_client
        self._transfers = transfers
        self._checkers = checkers
        self._fast_list = fast_list
        self._progress = progress

        binary = shutil.which("rclone")
        if binary is None:
            raise RcloneNotAvailableError(
                "rclone binary not found in PATH. "
                "Install via `brew install rclone` (macOS) or https://rclone.org/install/. "
                "Alternatively, run with the default boto3 backend."
            )
        self._binary = binary

    @property
    def remote_alias(self) -> str:
        return f"{REMOTE_NAME}:{self._r2.bucket}"

    def env(self) -> dict[str, str]:
        """Env vars configuring the rclone remote without an rclone.conf file."""
        env = os.environ.copy()
        access_key = os.environ.get("SOW_R2_ACCESS_KEY_ID")
        secret_key = os.environ.get("SOW_R2_SECRET_ACCESS_KEY")
        if not access_key or not secret_key:
            raise ValueError("SOW_R2_ACCESS_KEY_ID / SOW_R2_SECRET_ACCESS_KEY not set")
        prefix = f"RCLONE_CONFIG_{REMOTE_NAME.upper()}"
        env[f"{prefix}_TYPE"] = "s3"
        env[f"{prefix}_PROVIDER"] = "Cloudflare"
        env[f"{prefix}_ACCESS_KEY_ID"] = access_key
        env[f"{prefix}_SECRET_ACCESS_KEY"] = secret_key
        env[f"{prefix}_REGION"] = self._r2.region
        env[f"{prefix}_ENDPOINT"] = self._r2.endpoint_url
        env[f"{prefix}_NO_CHECK_BUCKET"] = "true"
        return env

    def copy_bucket_to_dir(self, dest: Path) -> RcloneResult:
        """Invoke `rclone copy r2:bucket dest/ --fast-list --transfers=N ...`"""
        cmd = [
            self._binary,
            "copy",
            self.remote_alias,
            str(dest),
            "--transfers", str(self._transfers),
            "--checkers", str(self._checkers),
            "--retries", "3",
            "--low-level-retries", "10",
        ]
        if self._fast_list:
            cmd.append("--fast-list")
        if self._progress:
            cmd += ["--stats", "5s", "--stats-log-level", "NOTICE", "--progress"]
        # rclone verifies MD5/ETag on download by default; we keep this on
        # rather than re-validating in Python (HashingReader still computes
        # SHA-256 for the manifest).
        t0 = time.monotonic()
        proc = subprocess.run(
            cmd, env=self.env(), capture_output=not self._progress, text=True
        )
        return RcloneResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            elapsed_seconds=time.monotonic() - t0,
        )
```

Public accessors needed in `R2Client` (currently the endpoint/region are
stored as instance attrs but the names are not yet documented as public):
confirm/extract `bucket`, `region`, `endpoint_url` as public attributes on
`R2Client` (read paths; no setter behavior changes). Adjust `r2.py:95-111`
constructor only if these are currently underscore-prefixed.

---

### Fix 2: Refactor `write_backup` to support a backend selector (P0)

**Goal:** Add a `--backend={boto3,rclone}` option. Default stays `boto3` until
the benchmark proves rclone's advantage and the user opts in to flipping.

**File:** `src/stream_of_worship/admin/services/r2_backup.py`

Refactor `write_backup` to delegate the download phase to one of two
strategies. Both strategies must produce identical `DownloadResult` payloads
(temp_path, sha256, bytes_read, etag_match_ok) so the manifest-build loop is
unchanged.

**Change A** — add a new module-level function `write_backup_rclone`:

```python
def write_backup_rclone(
    r2_client: R2Client,
    inventory: Inventory,
    output_dir: Path,
    chunk_size_bytes: int = DEFAULT_CHUNK_SIZE_BYTES,
    spot_check_ratio: float = SPOT_CHECK_HEAD_RATIO,
    progress: Optional[BackupProgress] = None,
    tracer: Optional[BackupTracer] = None,
    rclone_transfers: int = 8,
    rclone_checkers: int = 16,
) -> None:
    """rclone-backed implementation of write_backup.

    Pipeline:
      1. rclone copy r2:bucket -> output_dir.part/rclone_staging/
      2. Walk staging directory in inventory order; for each expected key:
         - Open staged file as stream
         - Stream through HashingReader (SHA-256 + size check)
         - HEAD r2 to fetch content_type/cache_control/.../metadata
         - Verify etag vs inventory.etag
         - Write to current chunked tar with the existing member-name scheme
      3. Spot-check 5% via head_object (same as boto3 path)
      4. Write manifest.json v4 (identical schema)
      5. rmtree(rclone_staging); rename .part/ -> output_dir/ (atomic)
    """
```

The key invariants compared to `write_backup` (the boto3 path):

| Aspect | boto3 path | rclone path |
|---|---|---|
| Download temp storage | one tempfile per object, deleted after tar | one shared staging dir, deleted after archive-build loop |
| SHA-256 computed | during download stream | during post-download staging-walk stream |
| ETag verification | during download via ContentLength match | rclone already verifies on download; reverify in staging-walk via HEAD |
| Per-object metadata | captured via `head_object` if spot-checked (5%) | **always** captured via `head_object` (per-object) — see Fix 3 |
| Tar member name | `objects/{idx:012d}.bin` | identical |
| Manifest schema | `MANIFEST_VERSION = 4` | identical |
| Partial-tracking dir name | `<output>.part/` | identical, plus `<output>.part/rclone_staging/` |

**Change B** — add a backend dispatcher:

```python
def write_backup(
    r2_client: R2Client,
    inventory: Inventory,
    output_dir: Path,
    *,
    backend: str = "boto3",       # new kwarg
    concurrency: int = DEFAULT_CONCURRENCY,
    chunk_size: str = "10GiB",
    ...
) -> None:
    if backend not in ("boto3", "rclone"):
        raise BackupError(f"unknown backend {backend!r}; expected boto3 or rclone")
    if backend == "rclone":
        write_backup_rclone(r2_client, inventory, ...)
    else:
        _write_backup_boto3(r2_client, inventory, ...)  # renamed from current impl
```

Rename the existing `write_backup` body to `_write_backup_boto3` and keep
its behavior byte-identical. The public `write_backup` becomes the dispatcher.

---

### Fix 3: Always `head_object` in the rclone path for full manifest metadata (P1)

**Why:** The existing manifest schema (v4) includes `content_type`,
`cache_control`, `content_disposition`, `content_encoding`, and per-object
`metadata`. The boto3 path only spot-checks 5% via `head_object` because
the download `get_object` response already carries these headers. rclone
copy discards them (only writes the bytes to disk).

**Decision:** For the rclone backend, do one `head_object` per object during
the manifest-build walk. This adds N boto3 HEAD calls per backup. At a 12.9 GB
bucket of 679 objects this is 679 HEAD calls — negligible compared to the
download phase.

**Alternative considered:** Use `rclone lsf r2:bucket --json --header`
to extract metadata in one bulk call, but rclone does not return R2 custom
metadata via `lsf`. Per-object HEAD is simpler and correct.

---

### Fix 4: Surface `bucket`, `region`, `endpoint_url` as public attrs on `R2Client` (P1)

**File:** `src/stream_of_worship/admin/services/r2.py`

Currently the R2Client attributes (lines 95-111) are private (`self._bucket`,
`self._endpoint_url`, etc., prefixed underscore). The new `RcloneClient`
needs read-only access to construct its env-var config. Add `@property`
readers; do **not** add setters.

```python
@property
def bucket(self) -> str:
    return self._bucket

@property
def endpoint_url(self) -> str:
    return self._endpoint_url

@property
def region(self) -> str:
    return self._region
```

If the existing attrs are already public (verify by reading lines 95-111
during implementation), this fix is a no-op.

---

### Fix 5: Add `--backend` flag to the `backup-r2` command (P0)

**File:** `src/stream_of_worship/admin/commands/maintenance.py:642-670`

```python
@app.command("backup-r2")
def backup_r2(
    output: Path = typer.Option(..., "--output", help="Output directory for backup"),
    chunk_size: str = typer.Option("10GiB", "--chunk-size", ...),
    concurrency: int = typer.Option(8, "--concurrency", min=1, max=64, ...),
    backend: str = typer.Option(
        "boto3", "--backend",
        help="Download backend: 'boto3' (default, ThreadPoolExecutor) or "
             "'rclone' (requires rclone in PATH; uses parallel Range-GETs).",
    ),
    debug_traces: bool = typer.Option(False, "--debug-traces", ...),
    format_: str = typer.Option("table", "--format", help="table|json"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
    diag_range_key: Optional[str] = typer.Option(None, "--diag-range-key", ...),
) -> None:
    """Backup entire R2 bucket to a local directory with chunked tar archives."""
    ...
    write_backup(r2_client, inventory, output, backend=backend, ...)
```

Validate `backend ∈ {"boto3", "rclone"}` at command entry (raise
`typer.BadParameter` if invalid). Do NOT auto-detect rclone in PATH; let
the explicit `--backend=rclone` flag fail loudly if rclone is missing, so
the boto3 path remains a guaranteed fallback.

---

### Fix 6: Tests for `RcloneClient` (`tests/admin/test_rclone.py`, new) (P0)

Mock `subprocess.run` and `shutil.which`. Cover:

1. `RcloneNotAvailableError` when `shutil.which("rclone")` returns `None`.
2. Env-var construction: assert `RCLONE_CONFIG_SOW_R2_TYPE == "s3"`,
   `RCLONE_CONFIG_SOW_R2_PROVIDER == "Cloudflare"`,
   `RCLONE_CONFIG_SOW_R2_ENDPOINT == <expected endpoint>`.
3. `copy_bucket_to_dir` invokes `rclone copy sow_r2:<bucket> <dest>` with
   the documented flags.
4. Nonzero exit code is surfaced in `RcloneResult`.
5. Credentials missing → raises `ValueError`.

### Fix 7: Tests for `write_backup_rclone` (`tests/admin/test_r2_backup.py`) (P0)

Extend the existing mocked-R2Client fixture (`tests/admin/test_r2_backup_commands.py:20-80`).
Cover:

1. rclone backend produces identical manifest schema v4 as boto3 backend.
   Assert: same keys, same `sha256`, same `etag`, same `content_type` etc.
2. Staging directory is removed after archive-build.
3. Missing staged file (rclone partial failure) raises `BackupError`.
4. `head_object` is invoked once per object (vs boto3 path's ~5% sample).
5. Tar member name scheme is `objects/{idx:012d}.bin` (unchanged).
6. `--backend=invalid` raises `typer.BadParameter`.
7. `--backend=rclone` with no rclone binary in PATH surfaces a friendly
   error message (not a silent boto3 fallback).

### Fix 8: pyproject.toml — declare soft rclone guidance (P1)

rclone is a system binary, not a Python package; it is not added to
`pyproject.toml` dependencies. Add a comment in the `admin` extra section
documenting the optional external dependency:

```toml
admin = [
    ...
    "boto3>=1.34.0",          # primary R2 backend; see RCLONE note below
    ...
]
# Optional: rclone (system binary, NOT pip-installable) speeds up
# `sow-admin maintenance backup-r2 --backend=rclone` via parallel
# Range-GETs. Install via `brew install rclone` (macOS) or
# https://rclone.org/install/. The boto3 backend remains the default
# and is fully functional without rclone.
```

## File Change Summary

| File | Change | Priority |
|---|---|---|
| `src/stream_of_worship/admin/services/rclone.py` (new) | New `RcloneClient` wrapping `rclone copy` via env-var config | P0 |
| `src/stream_of_worship/admin/services/r2_backup.py` | Rename `write_backup` body → `_write_backup_boto3`; add dispatcher `write_backup(backend=...)`; add `write_backup_rclone` | P0 |
| `src/stream_of_worship/admin/services/r2.py` | Add public `@property` readers for `bucket`, `region`, `endpoint_url` (if not already public) | P1 |
| `src/stream_of_worship/admin/commands/maintenance.py:642-670` | Add `--backend={boto3,rclone}` Typer option; thread through to `write_backup` | P0 |
| `tests/admin/test_rclone.py` (new) | Mocked-subprocess unit tests for `RcloneClient` | P0 |
| `tests/admin/test_r2_backup.py` | New `write_backup_rclone` tests alongside existing boto3 tests | P0 |
| `tests/admin/test_r2_backup_commands.py` | CLI tests: `--backend=rclone` happy path, `--backend=invalid` error, missing-rclone fallback behavior | P0 |
| `pyproject.toml` | Add comment in `admin` extra documenting optional rclone system binary | P1 |
| `examples/sow-admin-config.toml` | Add note in `[r2]` section: `# Optional: install rclone to enable --backend=rclone (faster bulk download)` | P2 |
| `report/current_impl_status.md` | Append entry after spec lands (per AGENTS.md convention) | P1 |
| `MEMORY.md` (if present) | Reflect that the previously "closed" throughput investigation is reopened via rclone | P1 |

## Verification Plan

### Step 0: Install rclone (prereq)

```bash
brew install rclone
rclone --version    # confirm installed, e.g. v1.67+
```

### Step 1: Mandatory pre-implementation benchmark (establishes baseline)

Run **all three** measurements before writing any production code. This is the
empirical test the v2 remediation spec skipped; we will not skip it again.

#### 1a. boto3 baseline (the "before" number)

```bash
sow-admin maintenance backup-r2 \
  --output ~/BACKUPS/sow-r2/baseline-boto3 \
  --chunk-size 5GiB \
  --concurrency 8 \
  --debug-traces \
  --diag-range-key d48247f4fb2f/stems/vocals.wav 2>&1 | tee baseline-boto3.log
```

Record in `specs/admin-r2-backup-rclone-download-v1-results.md`:
- total runtime (s), aggregate MiB/s, objects_downloaded
- `single_conn_mbps`, `multi_conn_total_mbps`, `ratio` from the diag

#### 1b. rclone pure reference (the "ceiling" number)

```bash
mkdir -p ~/BACKUPS/sow-r2/ref-rclone
export RCLONE_CONFIG_SOW_R2_TYPE=s3
export RCLONE_CONFIG_SOW_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_SOW_R2_ACCESS_KEY_ID="$SOW_R2_ACCESS_KEY_ID"
export RCLONE_CONFIG_SOW_R2_SECRET_ACCESS_KEY="$SOW_R2_SECRET_ACCESS_KEY"
export RCLONE_CONFIG_SOW_R2_REGION=auto
export RCLONE_CONFIG_SOW_R2_ENDPOINT=<from-config>
export RCLONE_CONFIG_SOW_R2_NO_CHECK_BUCKET=true

rclone copy sow_r2:stream-of-worship ~/BACKUPS/sow-r2/ref-rclone \
  --transfers 8 --checkers 16 --fast-list \
  --stats 5s --stats-log-level NOTICE --progress 2>&1 | tee ref-rclone.log
```

Record:
- total runtime (s), aggregate MiB/s, file count + bytes
- compare file count vs baseline inventory count (should match exactly)

#### 1c. Decision point

| If ref-rclone achieves... | Then... |
|---|---|
| >2x baseline-boto3 throughput | Proceed with implementation (Fixes 1-7) and the new backend becomes the default in a follow-up spec |
| 1.2x–2x baseline-boto3 throughput | Proceed with implementation; keep boto3 as default backend; document the modest gain |
| <1.2x baseline-boto3 throughput | **Stop.** The cap is R2-account-level, not boto3-specific. Update this spec with the finding, file as `admin-r2-backup-rclone-download-v1-results.md`, do not implement Fixes 1-7 |
| rclone crashes / auth failure | Diagnose env var config; if rclone backend is incompatible with R2, fall through to the next-preferred option (aiobotocore — out of scope for this spec) |

This step is the **only** step where the spec may stop without proceeding. The
rest of the verification plan assumes Step 1c returned "proceed".

### Step 2: Implement Fixes 1-7

Implement in the order: Fix 4 (R2Client properties) → Fix 1 (RcloneClient) →
Fix 2 (write_backup_rclone) → Fix 3 (head_object in rclone path) → Fix 5
(--backend flag) → Fix 6 + 7 (tests). Run:

```bash
PYTHONPATH=src uv run --python 3.11 --extra admin --extra test \
  pytest tests/admin/test_rclone.py tests/admin/test_r2_backup.py \
  tests/admin/test_r2_backup_commands.py -v
```

### Step 3: Lint and typecheck

```bash
PYTHONPATH=src uv run --python 3.11 --extra admin \
  ruff check \
  src/stream_of_worship/admin/services/rclone.py \
  src/stream_of_worship/admin/services/r2_backup.py \
  src/stream_of_worship/admin/services/r2.py \
  src/stream_of_worship/admin/commands/maintenance.py

PYTHONPATH=src uv run --python 3.11 --extra admin \
  mypy src/stream_of_worship/admin/services/rclone.py \
       src/stream_of_worship/admin/services/r2_backup.py
```

### Step 4: Post-implementation end-to-end benchmark

Run the new CLI backend:

```bash
sow-admin maintenance backup-r2 \
  --output ~/BACKUPS/sow-r2/impl-rclone \
  --backend rclone \
  --chunk-size 5GiB \
  --debug-traces \
  --diag-range-key d48247f4fb2f/stems/vocals.wav 2>&1 | tee impl-rclone.log
```

Pass criteria:
- Total runtime ≤ `ref-rclone` baseline from Step 1b ± 10% (parity reach)
- Manifest `MANIFEST_VERSION == 4` and includes all expected fields
- `verify-r2-backup` (Step 5) completes clean

If parity is reached (i.e., rclone backend matches rclone reference), the cap
diagnostic from Step 4 (`--diag-range-key`) is still useful: it tells us
whether the in-CLI implementation cached any R2 account-level override.

#### 4b. Comparative metric block (record in results spec)

| Backend | Total runtime (s) | Aggregate MiB/s | `single_conn_mbps` | `multi_conn_total_mbps` | `ratio` |
|---|---|---|---|---|---|
| boto3 (Step 1a) | TBD | TBD |  |  |  |
| rclone ref (Step 1b) | TBD | TBD | n/a (rclone-external) | n/a | n/a |
| new rclone backend (Step 4) | TBD | TBD |  |  |  |

### Step 5: Verify backup integrity

```bash
sow-admin maintenance verify-r2-backup \
  --input ~/BACKUPS/sow-r2/impl-rclone 2>&1 | tee verify-impl-rclone.log
```

Expected: every object's sha256, size, and etag match. If any mismatch,
investigate: rclone may have re-computed MD5s that don't match the etag
schema expected (multipart vs single-part); adjust manifest validation in
`write_backup_rclone` accordingly.

### Step 6: Restore round-trip smoke (optional, recommended)

```bash
sow-admin maintenance restore-r2 \
  --input ~/BACKUPS/sow-r2/impl-rclone \
  --prefix test-restore/ \
  --dry-run 2>&1 | tee restore-dry-run.log
```

Expected: plan_restore lists all objects. No actual upload in dry-run.

### Step 7: Session completion (per AGENTS.md)

```bash
git pull --rebase
git push
git status
```

`git status` MUST show "up to date with origin" before stopping.

## Out of Scope

- **aiobotocore async rewrite.** Listed in user's clarification round as an
  alternative to rclone. Not pursued in this spec; revisit if rclone path
  yields <1.2x improvement (per Step 1c decision).
- **boto3 TransferConfig tuning** (use_threads=True, multipart_chunksize).
  Listed in user's clarification as an alternative. Not pursued; if rclone
  doesn't break the ceiling, neither will boto3 tuning using a similar
  mechanism (parallel Range-GETs across a global pool).
- **Restoring the user's original `<10 min for 12.9 GB` goal.** User
  explicitly chose "match rclone default behavior" as the success metric;
  no fixed target is reintroduced here.
- **Replacing the boto3 path entirely.** boto3 path remains as default and
  fallback. A follow-up spec (`admin-r2-backup-rclone-default-v1.md`) may
  promote rclone to default after Step 5 produces a stable success run.
- **Cloudflare Worker in-network backup.** Still out of scope; revisit only
  if rclone + boto3 HEAD both hit the cap.
- **Reading R2 custom metadata via `rclone lsf`.** Rejected; rclone's `lsf`
  does not return R2 custom metadata cleanly. Per-object `head_object` is
  used in the rclone backend (Fix 3).
- **`--fast-list` memory tuning.** Default behavior is acceptable for a 679-object
  bucket. Revisit if bucket grows past ~1M objects.
- **`--debug-traces` integration of rclone subprocess output.** The
  subprocess's stdout/stderr is logged to the console during the rclone call;
  the Python BackupTracer does not parse rclone output for now. A follow-up may
  pipe `--stats` lines into BackupTracer if finer-grained accounting is needed.

## Decisions Deferred

- **Default backend**: keep `boto3` as default; do not flip to `rclone` until
  Step 1c confirms a ≥1.2x improvement and Step 4 achieves parity.
- **`--backend=auto`** (detect rclone, fall back to boto3): explicitly
  rejected in Fix 5 to avoid surprising the user; explicit flag is clearer
  and surfaces rclone-missing loudly.
- **Streaming from rclone to tar without staging directory**: would require
  per-object `rclone cat` (defeats rclone's parallelism) or running rclone
  with `rclone rcat` to a Python pipe (unreliable). Staging dir is the
  correct trade-off.
- **Promoting rclone binary to a Homebrew cask dependency in CI**: not in
  scope; the admin CLI is run locally, not in CI. CI can install rclone if
  Step 1 benchmarks need reproducible runs.
- **Switching restore (`restore_from_archive`) to rclone**: out of scope.
  The boto3 `upload_fileobj` restore path is upload-bound (different
  bottleneck profile) and is rarely called; would require a separate spec.

## References

- `specs/admin-r2-backup-throughput-remediation-v1.md` — 32-worker bump that
  regressed throughput; predecessor of this work.
- `specs/admin-r2-backup-throughput-remediation-v2.md` — closure of the v1
  investigation; established the ~7 MiB/s account-level cap and the
  `ratio=2.41` diagnostic signature.
- `specs/admin-r2-backup-concurrent-downloads-v1.md` — original boto3
  ThreadPoolExecutor design; the architecture this spec augments.
- `specs/admin-r2-backup-perf-traces.md` — BackupTracer design; the
  `--diag-range-key` flag this spec relies on for Step 1.
- `src/stream_of_worship/admin/services/r2.py:95-111` — boto3 R2Client (the
  baseline; unchanged except for Fix 4 read-only properties).
- `src/stream_of_worship/admin/services/r2_backup.py:884-1203` — existing
  boto3 `write_backup` (renamed to `_write_backup_boto3` per Fix 2).
- `src/stream_of_worship/admin/commands/maintenance.py:642-784` — existing
  `backup-r2` Typer command (augmented in Fix 5).
- `examples/sow-admin-config.toml:20-28` — R2 endpoint config docs.
- https://rclone.org/s3/#cloudflare-r2 — Cloudflare R2 backend reference.
- `MEMORY.md` line 3 — existing closure note that this spec may supersede.
