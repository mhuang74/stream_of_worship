# Status Reconciliation via R2 Scan (v2)

## Problem Summary

When the Admin submits jobs via `sow-admin audio lrc` or `sow-admin audio analyze`, the local DB is set to `processing`. The Analysis Service completes the job and uploads results to R2, but it **never writes to Turso**. The Admin CLI only discovers completion if:

- `--wait` was used (single-song only, not batch)
- `audio status --sync` is run (polls Analysis Service, but unreliable if the service restarts and loses job state)
- `db sync` is run (only syncs local↔Turso, but nobody wrote the completion to Turso either)

### Current Latency

Admins see status updates only when they manually run sync commands, which can be hours or days after job completion.

### Desired Behavior

Admins should see status updates within a few minutes (at most 5 minutes) of job completion, even if the Analysis Service restarts.

## Solution: Add `--reconcile` to `audio status`

Instead of polling the Analysis Service (fragile), scan R2 directly for file presence. R2 is the durable source of truth:

- `{hash_prefix}/lyrics.lrc` exists → LRC job completed successfully
- `{hash_prefix}/analysis.json` exists → analysis job completed successfully

This approach is robust against Analysis Service restarts since R2 is persistent storage.

### Key Design Decisions

1. **R2 as source of truth**: Completion is determined by file presence in R2, not Analysis Service job state
2. **DB-driven scan**: Iterate over DB recordings with incomplete status and check R2 for each (vs. listing all R2 files)
3. **Distinguish 404 from other R2 errors**: Non-404 errors (permission, credential, network) are logged and skipped — not silently treated as "file not found"
4. **Reconcile both LRC and analysis**: Single `--reconcile` flag covers both job types
5. **Analysis reconcile downloads + parses `analysis.json`**: Populates all structured fields (tempo, key, beats, etc.), not just status
6. **No auto-fail detection**: Leave `processing` entries alone if no file found (job may still be running)
7. **Never auto-sync**: Admin runs `db sync` separately after reconcile
8. **No schema changes**: Existing DB methods and columns are sufficient
9. **No new containers**: Leverage existing Admin CLI infrastructure

## File Changes

| File | Change |
|------|--------|
| `src/stream_of_worship/admin/services/r2.py` | Add `lrc_exists()`, `analysis_exists()`, `download_analysis_json()` |
| `src/stream_of_worship/admin/commands/audio.py` | Add `--reconcile` flag to `check_status()` (LRC + analysis), narrow `--sync` deprecation warning |
| `src/stream_of_worship/admin/db/client.py` | No changes needed |

## Detailed Changes

### 1. `src/stream_of_worship/admin/services/r2.py` — Add R2 check methods

#### `lrc_exists(hash_prefix) -> Optional[str]`

```python
def lrc_exists(self, hash_prefix: str) -> Optional[str]:
    """Check whether an LRC file exists in R2.

    Args:
        hash_prefix: 12-character hash prefix

    Returns:
        S3 URL of the LRC file if it exists, None if not found.

    Raises:
        ClientError: On non-404 errors (permission, credential, network).
    """
    s3_key = f"{hash_prefix}/lyrics.lrc"
    try:
        self._client.head_object(Bucket=self.bucket, Key=s3_key)
        return f"s3://{self.bucket}/{s3_key}"
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey"):
            return None
        raise
```

**Critical difference from v1**: Only 404/NoSuchKey returns `None`. All other `ClientError` (permission denied, credential expired, network timeout) re-raise so the caller knows something went wrong.

**Location**: Insert after `audio_exists()` method.

#### `analysis_exists(hash_prefix) -> Optional[str]`

```python
def analysis_exists(self, hash_prefix: str) -> Optional[str]:
    """Check whether an analysis.json file exists in R2.

    Args:
        hash_prefix: 12-character hash prefix

    Returns:
        S3 URL of the analysis.json if it exists, None if not found.

    Raises:
        ClientError: On non-404 errors (permission, credential, network).
    """
    s3_key = f"{hash_prefix}/analysis.json"
    try:
        self._client.head_object(Bucket=self.bucket, Key=s3_key)
        return f"s3://{self.bucket}/{s3_key}"
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey"):
            return None
        raise
```

**Location**: Insert after `lrc_exists()` method.

#### `download_analysis_json(hash_prefix) -> dict`

```python
def download_analysis_json(self, hash_prefix: str) -> dict:
    """Download and parse analysis.json from R2.

    Args:
        hash_prefix: 12-character hash prefix

    Returns:
        Parsed analysis result dictionary with keys:
        duration_seconds, tempo_bpm, musical_key, musical_mode,
        key_confidence, loudness_db, beats, downbeats, sections,
        embeddings_shape, stems_url

    Raises:
        ClientError: On any R2 error (including 404).
        json.JSONDecodeError: If the file is not valid JSON.
    """
    s3_key = f"{hash_prefix}/analysis.json"
    response = self._client.get_object(Bucket=self.bucket, Key=s3_key)
    body = response["Body"].read().decode("utf-8")
    return json.loads(body)
```

**Location**: Insert after `analysis_exists()` method.

**Note**: This method does NOT catch errors — the caller handles them. If `analysis_exists()` returned a URL, then `download_analysis_json()` should succeed unless there's a race condition (file deleted between check and download).

### 2. `src/stream_of_worship/admin/commands/audio.py` — Add `--reconcile` to `check_status()`

**New parameter** (add after `force_url` parameter):

```python
reconcile: bool = typer.Option(
    False, "--reconcile", "-r",
    help="Reconcile LRC and analysis status by scanning R2 (robust against service restarts)"
)
```

**Reconcile logic** (insert after `--force-status` handler and before "Mode A: Query specific job"):

```python
# Handle --reconcile mode
if reconcile:
    try:
        r2_client = R2Client(config.r2_bucket, config.r2_endpoint_url, config.r2_region)
    except ValueError as e:
        console.print(f"[red]R2 not configured: {e}[/red]")
        raise typer.Exit(1)

    incomplete_lrc = db_client.list_recordings(lrc_status="incomplete")
    incomplete_analysis = db_client.list_recordings(status="incomplete")

    # Deduplicate: a recording may be incomplete on both
    all_hashes = set()
    reconcile_queue = []
    for rec in incomplete_lrc:
        if rec.hash_prefix not in all_hashes:
            all_hashes.add(rec.hash_prefix)
            reconcile_queue.append(rec)
    for rec in incomplete_analysis:
        if rec.hash_prefix not in all_hashes:
            all_hashes.add(rec.hash_prefix)
            reconcile_queue.append(rec)

    if not reconcile_queue:
        console.print("[green]No recordings with incomplete LRC or analysis status.[/green]")
    else:
        console.print(f"[cyan]Scanning R2 across {len(reconcile_queue)} recording(s)...[/cyan]")
        reconciled_lrc = 0
        reconciled_analysis = 0
        error_count = 0

        for rec in reconcile_queue:
            # LRC reconciliation
            if rec.lrc_status in ("pending", "processing", "failed"):
                try:
                    lrc_url = r2_client.lrc_exists(rec.hash_prefix)
                    if lrc_url:
                        db_client.update_recording_lrc(
                            hash_prefix=rec.hash_prefix,
                            r2_lrc_url=lrc_url,
                        )
                        reconciled_lrc += 1
                        console.print(
                            f"  [green]✓[/green] {rec.song_id or rec.hash_prefix}: "
                            f"LRC {rec.lrc_status} → completed"
                        )
                except ClientError as e:
                    error_count += 1
                    console.print(
                        f"  [red]✗[/red] {rec.hash_prefix}: R2 error checking LRC: {e}"
                    )

            # Analysis reconciliation
            if rec.analysis_status in ("pending", "processing", "failed"):
                try:
                    analysis_url = r2_client.analysis_exists(rec.hash_prefix)
                    if analysis_url:
                        analysis_data = r2_client.download_analysis_json(rec.hash_prefix)
                        db_client.update_recording_analysis(
                            hash_prefix=rec.hash_prefix,
                            duration_seconds=analysis_data.get("duration_seconds"),
                            tempo_bpm=analysis_data.get("tempo_bpm"),
                            musical_key=analysis_data.get("musical_key"),
                            musical_mode=analysis_data.get("musical_mode"),
                            key_confidence=analysis_data.get("key_confidence"),
                            loudness_db=analysis_data.get("loudness_db"),
                            beats=json.dumps(analysis_data["beats"]) if "beats" in analysis_data else None,
                            downbeats=json.dumps(analysis_data["downbeats"]) if "downbeats" in analysis_data else None,
                            sections=json.dumps(analysis_data["sections"]) if "sections" in analysis_data else None,
                            embeddings_shape=json.dumps(analysis_data["embeddings_shape"]) if "embeddings_shape" in analysis_data else None,
                            r2_stems_url=analysis_data.get("stems_url"),
                        )
                        reconciled_analysis += 1
                        console.print(
                            f"  [green]✓[/green] {rec.song_id or rec.hash_prefix}: "
                            f"analysis {rec.analysis_status} → completed"
                        )
                except ClientError as e:
                    error_count += 1
                    console.print(
                        f"  [red]✗[/red] {rec.hash_prefix}: R2 error checking analysis: {e}"
                    )
                except (json.JSONDecodeError, KeyError) as e:
                    error_count += 1
                    console.print(
                        f"  [red]✗[/red] {rec.hash_prefix}: error parsing analysis.json: {e}"
                    )

        parts = []
        if reconciled_lrc > 0:
            parts.append(f"{reconciled_lrc} LRC")
        if reconciled_analysis > 0:
            parts.append(f"{reconciled_analysis} analysis")
        if parts:
            console.print(f"[green]Reconciled {' and '.join(parts)} status(es) from R2.[/green]")
        else:
            console.print("[dim]No completed files found in R2 for pending recordings.[/dim]")
        if error_count > 0:
            console.print(f"[yellow]{error_count} R2 error(s) encountered (see above).[/yellow]")
        console.print("")
    # Fall through to list pending recordings table
```

**Deprecate `--sync`**: Add warning at the start of `--sync` handling:

```python
if sync:
    console.print(
        "[yellow]Warning: --sync is unreliable if the Analysis Service has restarted. "
        "For LRC and analysis status, consider using --reconcile instead, "
        "which scans R2 directly.[/yellow]"
    )
```

**Update docstring**:

```python
"""Check analysis status.

With JOB_ID: query the service for that job's status.
Without: list all recordings with pending/processing/failed status.
Use --reconcile to update LRC and analysis status by scanning R2 (robust against service restarts).
Use --sync to poll the analysis service (unreliable if service restarted).
Use --force-status when you need to manually override status.
"""
```

### 3. No changes needed to `db/client.py`

Existing methods are sufficient:
- `list_recordings(lrc_status="incomplete")` → recordings where `lrc_status IN ('pending', 'processing', 'failed')`
- `list_recordings(status="incomplete")` → recordings where `analysis_status IN ('pending', 'processing', 'failed')`
- `update_recording_lrc(hash_prefix, r2_lrc_url)` → sets `lrc_status='completed'`, auto-publishes if `visibility_status IS NULL`
- `update_recording_analysis(hash_prefix, ...)` → sets `analysis_status='completed'` + all structured fields

## Automation / Cron Integration

```cron
# Reconcile status every 3 minutes, then sync to Turso
# Use ';' (not '&&') so db sync always runs even if reconcile has partial R2 errors
*/3 * * * * sow-admin audio status --reconcile > /tmp/sow-reconcile.log 2>&1 ; sow-admin db sync >> /tmp/sow-reconcile.log 2>&1
```

This delivers **≤3 min latency** for status visibility:

1. Job completes and files uploaded to R2
2. cron runs `audio status --reconcile` at T+0-3m, finds R2 files, updates local DB
3. cron runs `db sync`, pushes updates to Turso cloud
4. Other Admin replicas sync from Turso and see completed status

**Why `;` instead of `&&`**: If reconcile encounters non-404 R2 errors for some recordings, it still exits 0 (errors are logged but don't halt the command). However, using `;` ensures `db sync` always runs, pushing the successfully reconciled recordings to Turso even if some R2 checks failed.

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| LRC exists in R2, DB says `pending` | Updated to `completed` with `r2_lrc_url` |
| LRC exists in R2, DB says `processing` | Updated to `completed` with `r2_lrc_url` |
| LRC exists in R2, DB says `failed` | Updated to `completed` with `r2_lrc_url` (admin may have set failed for bad content, but file exists) |
| analysis.json exists in R2, DB says `pending`/`processing`/`failed` | Downloaded, parsed, all fields populated, `analysis_status='completed'` |
| No file in R2, DB says `processing` | Left unchanged (job may still be running) |
| No file in R2, DB says `failed` | Left unchanged |
| R2 permission/credential error on one recording | Logged, that recording skipped, others continue |
| analysis.json is malformed | Logged as error, that recording skipped, others continue |
| `r2_lrc_url` already set but `lrc_status != 'completed'` | Fixed to `completed` via `update_recording_lrc()` |
| Recording not in DB but files in R2 | Ignored (DB-driven scan only checks known recordings) |
| `update_recording_lrc()` auto-publishes | If `visibility_status IS NULL`, set to `published` (existing behavior) |

## Error Handling: 404 vs Other ClientError

This is the key operational improvement over v1. The original spec caught ALL `ClientError` and returned `None`, making permission errors indistinguishable from "file not found."

| Error Type | HTTP Code | Behavior |
|------------|-----------|----------|
| NoSuchKey / Not Found | 404 | Return `None` (file genuinely missing) |
| Access Denied | 403 | Re-raise (credential issue — admin must fix) |
| Network timeout | N/A | Re-raise (transient — retry later) |
| Internal R2 error | 500 | Re-raise (Cloudflare issue — retry later) |

**In `--reconcile` mode**: Non-404 errors are caught at the command level, logged, and the loop continues. The command still exits 0 so `db sync` runs afterward.

## Performance Considerations

- **R2 API calls**: Each incomplete recording triggers 1-2 `head_object` calls (one for LRC, one for analysis) plus a `get_object` for analysis download if found
- **Current use cases**: Admins typically have <100 recordings with incomplete status
- **Duration**: ~100ms per R2 `head_object`, ~200ms for `get_object` + parse
- **Total time for 100 recordings**: ~15-20 seconds (acceptable for cron)

### Future Optimization (Out of Scope)

Use `concurrent.futures.ThreadPoolExecutor` to parallelize R2 checks (reduce 100 recordings from ~15s to ~2s).

## Testing

### Manual Testing Steps

1. Submit LRC job: `sow-admin audio lrc <song_id>`
2. Verify DB shows `lrc_status="processing"`
3. Wait for Analysis Service to complete (or manually upload LRC to R2 for testing)
4. Run: `sow-admin audio status --reconcile`
5. Verify DB shows `lrc_status="completed"` and `r2_lrc_url` set
6. Run second reconciliation: should report no new files found

### Analysis Reconcile Testing

1. Submit analysis job: `sow-admin audio analyze <song_id>`
2. Verify DB shows `analysis_status="processing"`
3. Wait for completion or manually upload `analysis.json` to R2
4. Run: `sow-admin audio status --reconcile`
5. Verify DB shows `analysis_status="completed"` and all fields populated (tempo, key, etc.)
6. Compare fields against what Analysis Service reported

### Error Handling Testing

1. Temporarily set invalid R2 credentials
2. Run: `sow-admin audio status --reconcile`
3. Should see error messages, not silent `None` returns
4. Restore credentials and verify reconcile works again

### Unit Test Coverage

```python
def test_lrc_exists_found(monkeypatch):
    client = R2Client("test-bucket", "https://r2.example.com")
    monkeypatch.setattr(client._client, "head_object", lambda **kw: {})
    assert client.lrc_exists("abc123456789") == "s3://test-bucket/abc123456789/lyrics.lrc"

def test_lrc_exists_not_found(monkeypatch):
    from botocore.exceptions import ClientError
    client = R2Client("test-bucket", "https://r2.example.com")
    error_response = {"Error": {"Code": "404"}}
    def mock_head(**kw):
        raise ClientError(error_response, "HeadObject")
    monkeypatch.setattr(client._client, "head_object", mock_head)
    assert client.lrc_exists("abc123456789") is None

def test_lrc_exists_permission_denied(monkeypatch):
    from botocore.exceptions import ClientError
    client = R2Client("test-bucket", "https://r2.example.com")
    error_response = {"Error": {"Code": "403"}}
    def mock_head(**kw):
        raise ClientError(error_response, "HeadObject")
    monkeypatch.setattr(client._client, "head_object", mock_head)
    with pytest.raises(ClientError):
        client.lrc_exists("abc123456789")

def test_analysis_exists_and_download(monkeypatch):
    client = R2Client("test-bucket", "https://r2.example.com")
    analysis = {"tempo_bpm": 120.0, "musical_key": "C", "musical_mode": "major"}
    monkeypatch.setattr(client._client, "head_object", lambda **kw: {})
    monkeypatch.setattr(
        client._client, "get_object",
        lambda **kw: {"Body": io.BytesIO(json.dumps(analysis).encode())},
    )
    assert client.analysis_exists("abc123456789") is not None
    data = client.download_analysis_json("abc123456789")
    assert data["tempo_bpm"] == 120.0
```

## Differences from v1 (`lrc-reconciliation-via-r2.md`)

| Aspect | v1 | v2 |
|--------|----|----|
| Scope | LRC only | LRC + analysis |
| `ClientError` handling | Catch all → `None` | Distinguish 404 only; re-raise others |
| Analysis reconcile | Out of scope | Download + parse `analysis.json`, populate all DB fields |
| Cron chaining | `&&` | `;` (db sync always runs) |
| `--sync` deprecation | Generic warning | Narrowed: "for LRC and analysis status, consider --reconcile" |
| Schema changes | None | None (unchanged) |

## References

- Admin CLI status command: `src/stream_of_worship/admin/commands/audio.py` (lines 1786-2068)
- R2Client implementation: `src/stream_of_worship/admin/services/r2.py`
- Database client: `src/stream_of_worship/admin/db/client.py`
- Analysis Service R2 uploads: `services/analysis/src/sow_analysis/storage/r2.py`
- R2 file naming: `{hash_prefix}/lyrics.lrc`, `{hash_prefix}/analysis.json`
