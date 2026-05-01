# LRC Status Reconciliation via R2 Scan

## Problem Summary

When the Admin submits LRC jobs via `sow-admin audio lrc`, the local DB is set to `lrc_status="processing"`. The Analysis Service completes the job and uploads the LRC file to R2, but it **never writes to Turso**. The Admin CLI only discovers completion if:

- `--wait` was used (single-song only, not batch)
- `audio status --sync` is run (polls Analysis Service, but unreliable if the service restarts and loses job state)
- `db sync` is run (only syncs local↔Turso, but nobody wrote the completion to Turso either)

### Current Latency

Admins see LRC status updates only when they manually run sync commands, which can be hours or days after job completion.

### Desired Behavior

Admins should see LRC status updates within a few minutes (at most 5 minutes) of job completion, even if the Analysis Service restarts.

## Solution: Add `--reconcile` to `audio status`

Instead of polling the Analysis Service (fragile), scan R2 directly for LRC file presence. R2 is the durable source of truth — if `{hash_prefix}/lyrics.lrc` exists, the job completed successfully. This approach is robust against Analysis Service restarts since R2 is persistent storage.

### Key Design Decisions

1. **R2 as source of truth**: LRC completion is determined by file presence in R2, not Analysis Service job state
2. **DB-driven scan**: Iterate over DB recordings with incomplete status and check R2 for each (vs. listing all R2 files)
3. **No auto-fail detection**: Leave `processing` entries alone if no LRC file found (job may still be running)
4. **Never auto-sync**: Admin runs `db sync` separately after reconcile
5. **No new containers**: Leverage existing Admin CLI infrastructure

## File Changes

| File | Change |
|------|--------|
| `src/stream_of_worship/admin/services/r2.py` | Add `lrc_exists(hash_prefix) -> Optional[str]` |
| `src/stream_of_worship/admin/commands/audio.py` | Add `--reconcile` flag to `check_status()`, deprecate `--sync` |
| `src/stream_of_worship/admin/db/client.py` | No changes needed (existing methods sufficient) |

## Detailed Changes

### 1. `src/stream_of_worship/admin/services/r2.py` — Add `lrc_exists()`

Add a method following the existing pattern of `audio_exists()`:

```python
def lrc_exists(self, hash_prefix: str) -> Optional[str]:
    """Check whether an LRC file exists in R2.

    Args:
        hash_prefix: 12-character hash prefix

    Returns:
        S3 URL of the LRC file if it exists, None otherwise
    """
    try:
        s3_key = f"{hash_prefix}/lyrics.lrc"
        self._client.head_object(Bucket=self.bucket, Key=s3_key)
        return f"s3://{self.bucket}/{s3_key}"
    except ClientError:
        return None
```

**Location**: Insert after `audio_exists()` method (around line 137)

### 2. `src/stream_of_worship/admin/commands/audio.py` — Add `--reconcile` to `check_status()`

**New parameter** (add after `force_url` parameter, around line 1801):

```python
reconcile: bool = typer.Option(
    False, "--reconcile", "-r",
    help="Reconcile LRC status by scanning R2 for completed LRC files (robust against service restarts)"
)
```

**Reconcile logic** (insert after `--force-status` handler and before "Mode A: Query specific job", around line 1850):

```python
# Handle --reconcile mode
if reconcile:
    # Initialize R2 client
    try:
        r2_client = R2Client(config.r2_bucket, config.r2_endpoint_url, config.r2_region)
    except ValueError as e:
        console.print(f"[red]R2 not configured: {e}[/red]")
        raise typer.Exit(1)

    # DB-driven: get all recordings with lrc_status != 'completed'
    incomplete = db_client.list_recordings(lrc_status="incomplete")

    if not incomplete:
        console.print("[green]No recordings with incomplete LRC status.[/green]")
        # Fall through to list pending recordings table
    else:
        console.print(f"[cyan]Scanning R2 for LRC files across {len(incomplete)} recording(s)...[/cyan]")
        reconciled = 0
        for rec in incomplete:
            lrc_url = r2_client.lrc_exists(rec.hash_prefix)
            if lrc_url:
                db_client.update_recording_lrc(
                    hash_prefix=rec.hash_prefix,
                    r2_lrc_url=lrc_url,
                )
                reconciled += 1
                console.print(f"  [green]✓[/green] {rec.song_id or rec.hash_prefix}: pending/processing → completed")

        if reconciled > 0:
            console.print(f"[green]Reconciled {reconciled} LRC status(es) from R2.[/green]")
        else:
            console.print("[dim]No completed LRC files found in R2 for pending recordings.[/dim]")
        console.print("")
    # Fall through to list pending recordings table
```

**Deprecate `--sync`**: Add warning at the start of `--sync` handling (around line 1920):

```python
# Mode B: Sync and list pending recordings
# If --sync, query analysis service for all pending jobs and update local DB
if sync:
    console.print(
        "[yellow]Warning: --sync is unreliable if the Analysis Service has restarted. "
        "Consider using --reconcile instead, which scans R2 directly.[/yellow]"
    )
    # ... rest of --sync logic ...
```

**Update docstring** (around line 1804):

```python
"""Check analysis status.

With JOB_ID: query the service for that job's status.
Without: list all recordings with pending/processing/failed status.
Use --reconcile to update LRC status by scanning R2 (robust against service restarts).
Use --sync to poll the analysis service (unreliable if service restarted).
Use --force-status when you need to manually override status.
"""
```

### 3. No changes needed to `db/client.py`

The existing methods are sufficient:
- `list_recordings(lrc_status="incomplete")` returns recordings where `lrc_status IN ('pending', 'processing', 'failed')`
- `update_recording_lrc(hash_prefix, r2_lrc_url)` sets `lrc_status='completed'` and auto-publishes

## Automation / Cron Integration

The `--reconcile` flag works seamlessly with cron:

```cron
# Reconcile LRC status every 3 minutes, then sync to Turso
*/3 * * * * sow-admin audio status --reconcile > /tmp/sow-reconcile.log 2>&1 && sow-admin db sync >> /tmp/sow-reconcile.log 2>&1
```

This delivers **≤3 min latency** for LRC status visibility:

1. Job completes and LRC uploaded to R2
2. cron runs `audio status --reconcile at T+0-3m`, finds R2 files, updates local DB
3. cron runs `db sync`, pushes updates to Turso cloud
4. Other Admin replicas sync from Turso and see completed status

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| LRC exists in R2, DB says `pending` | Updated to `completed` with correct `r2_lrc_url` |
| LRC exists in R2, DB says `processing` | Updated to `completed` with correct `r2_lrc_url` |
| LRC exists in R2, DB says `failed` | Updated to `completed` with correct `r2_lrc_url` |
| No LRC in R2, DB says `processing` | Left unchanged (job may still be running) |
| No LRC in R2, DB says `failed` | Left unchanged |
| `r2_lrc_url` already set but `lrc_status != 'completed'` | Fixed to `completed` |
| Recording not in DB but LRC in R2 | Ignored (DB-driven scan only checks known recordings) |

## Performance Considerations

- **R2 API calls**: Each incomplete recording triggers one `head_object` call
- **Current use cases**: Admins typically have <100 recordings with incomplete LRC status
- **Duration**: ~100ms per R2 API call on average (Cloudflare R2 has low latency)
- **Total time for 100 recordings**: ~10 seconds (acceptable for cron)

### Future Optimization (Out of Scope)

For larger catalogs, use `concurrent.futures.ThreadPoolExecutor` to parallelize R2 checks:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

with ThreadPoolExecutor(max_workers=10) as executor:
    futures = {
        executor.submit(r2_client.lrc_exists, rec.hash_prefix): rec
        for rec in incomplete
    }
    for future in as_completed(futures):
        rec = futures[future]
        try:
            lrc_url = future.result()
            if lrc_url:
                db_client.update_recording_lrc(rec.hash_prefix, lrc_url)
                reconciled += 1
                console.print(f"  [green]✓[/green] {rec.song_id or rec.hash_prefix}")
        except Exception as e:
            console.print(f"  [red]✗[/red] Failed to check {rec.hash_prefix}: {e}")
```

This would reduce ~100 recordings from 10s to 1-2s.

## Testing

### Manual Testing Steps

1. Submit LRC job: `sow-admin audio lrc <song_id>`
2. Verify DB shows `lrc_status="processing"`
3. Wait for Analysis Service to complete (or manually upload LRC to R2 for testing)
4. Run: `sow-admin audio status --reconcile`
5. Verify DB shows `lrc_status="completed"` and `r2_lrc_url` set
6. Run second reconciliation: should report "No completed LRC files found"

### Unit Test Coverage

Add tests for new `R2Client.lrc_exists()` method:

```python
def test_lrc_exists(monkeypatch):
    client = R2Client("test-bucket", "https://r2.example.com")
    
    # Mock head_object to return successful response
    def mock_head_object(Bucket, Key):
        return True
    monkeypatch.setattr(client._client, "head_object", mock_head_object)
    
    result = client.lrc_exists("abc123456789")
    assert result == "s3://test-bucket/abc123456789/lyrics.lrc"
```

## Future Enhancements (Out of Scope)

- **Analysis status reconciliation**: Could similarly check R2 for `analysis.json` / `stems/` to reconcile `analysis_status`
- **`--reconcile-all`**: Scan all recordings (including completed) to backfill any missing LRC URLs
- **`--reconcile` on `audio list`**: Auto-reconcile before listing could reduce surprise, but adds latency to every list command

## References

- Admin CLI LRC workflow: `src/stream_of_worship/admin/commands/audio.py` (lines 1456-1514)
- Admin CLI status command: `src/stream_of_worship/admin/commands/audio.py` (lines 1786-2068)
- R2Client implementation: `src/stream_of_worship/admin/services/r2.py`
- Database client: `src/stream_of_worship/admin/db/client.py`
- R2 file naming pattern: `{hash_prefix}/lyrics.lrc` (see `upload_lrc()` in R2Client)
