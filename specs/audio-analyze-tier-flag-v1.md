# Plan: Add `--analysis-tier` to `audio analyze` (default `fast`)

> Closes the gap left by `specs/scale-key-bpm-analysis-batch.md:980`, which planned
> `analyze --tier fast <song_id>` but only shipped `--analysis-tier` on `audio batch`.
> See `docs/analyze-job-flow.md` for the full end-to-end flow of both tiers.

## Delta from Current State

The `audio analyze` command (`ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:1514`)
currently:

- Has no `--analysis-tier` flag.
- Always calls `client.submit_analysis(...)` (full tier, allin1).
- Hardcodes `analysis_status="completed"` on `--wait` writeback.
- Skip logic only checks `"completed"` and `"processing"`.

The older spec `specs/scale-key-bpm-analysis-batch.md:980` planned
`analyze --tier fast <song_id>` but it was never implemented — `--analysis-tier`
only shipped on `audio batch`.

This plan closes that gap by adding `--analysis-tier fast|full` (default `fast`) to
`audio analyze`, mirroring the tier logic already proven in `audio batch`
(audio.py:5282-5294, 5839-5889).

## Locked Decisions

1. **Flag name:** `--analysis-tier fast|full`, default `fast`. Consistent with `audio batch`.
2. **Default is `fast`:** A bare `sow-admin audio analyze <song_id>` now submits fast
   analysis. This is an intentional behavior change — fast is the cheaper, more common
   need, and `--analysis-tier full` is the explicit opt-in for structural detail.
3. **`--no-stems` with fast tier:** Warn and ignore. Stems are never generated for fast
   tier regardless.
4. **Skip logic mirrors `audio batch`:**
   - Fast: skip if `analysis_status in ("partial", "completed")` unless `--force`.
   - Full: skip if `analysis_status == "completed"` unless `--force`.
5. **In-flight job reuse:** If `analysis_status == "processing"` and the existing job's
   type matches the requested tier, reuse it (like audio.py:5254). If the job type
   doesn't match the requested tier, submit a new job.
6. **DB writeback on `--wait`:**
   - Fast: `analysis_status="partial"` (or stay `"completed"` if already completed).
   - Fast: do NOT write `beats`/`downbeats`/`sections`/`embeddings_shape`/`r2_stems_url`
     — the DB layer already preserves these when `analysis_status="partial"`
     (db/client.py:994-1030).
   - Full: `analysis_status="completed"`, write all fields.
7. **`_submit_analysis_job` helper (audio.py:578):** Out of scope for this spec. It's
   used by `audio download --analyze` and `audio download --all`, which always submit
   full analysis. A follow-up spec can add tier support there if needed.

## Command Surface

```bash
sow-admin audio analyze <song_id> [options]
```

New/changed options:

| Option | Type | Default | Description |
|---|---|---|---|
| `--analysis-tier` | `fast\|full` | `fast` | Analysis tier. `fast` = librosa-only (BPM, key, loudness). `full` = allin1 + librosa + optional Demucs stems. |
| `--no-stems` | flag | `False` | (Existing) Skip stem separation. Only meaningful with `--analysis-tier full`. Warned-and-ignored with `fast`. |
| `--force` | flag | `False` | (Existing) Force re-analysis, bypassing skip logic and cache. |
| `--wait` | flag | `False` | (Existing) Block until job completes, then writeback to DB. |
| `--config` | path | None | (Existing) Path to config file. |

### Examples

```bash
# Default: fast analysis (librosa-only, quick)
sow-admin audio analyze song_001

# Explicit fast
sow-admin audio analyze song_001 --analysis-tier fast

# Full analysis (allin1, structural detail)
sow-admin audio analyze song_001 --analysis-tier full

# Full analysis with stems, block until done
sow-admin audio analyze song_001 --analysis-tier full --wait

# Force re-run of fast analysis (bypass cache + skip logic)
sow-admin audio analyze song_001 --analysis-tier fast --force --wait
```

## Implementation Steps

### Step 1: Add `--analysis-tier` parameter to command signature

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:1514`

Add `analysis_tier` parameter to `analyze_recording`:

```python
@app.command("analyze")
def analyze_recording(
    song_id: str = typer.Argument(..., help="Song ID to analyze"),
    analysis_tier: str = typer.Option(
        "fast", "--analysis-tier", help="Analysis tier: fast (default) or full"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-analysis"),
    no_stems: bool = typer.Option(False, "--no-stems", help="Skip stem separation (full tier only)"),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for analysis to complete"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
```

### Step 2: Validate `analysis_tier` value

**File:** same, insert after config load (~line 1529)

```python
if analysis_tier not in ("fast", "full"):
    console.print(
        f"[red]Invalid analysis tier: {analysis_tier}. Must be 'fast' or 'full'[/red]"
    )
    raise typer.Exit(1)
```

### Step 3: Warn on `--no-stems` with fast tier

**File:** same, insert after validation

```python
if no_stems and analysis_tier == "fast":
    console.print(
        "[yellow]--no-stems is ignored with --analysis-tier fast "
        "(stems are never generated for fast analysis)[/yellow]"
    )
    no_stems = False
```

### Step 4: Update skip logic for tier-awareness

**File:** same, replace existing skip logic at audio.py:1551-1570

Current logic only skips on `"completed"` and `"processing"`. New logic mirrors
`audio batch` (audio.py:5228-5242):

```python
# Check if already analyzed (tier-aware)
if not force:
    if analysis_tier == "fast" and recording.analysis_status in ("partial", "completed"):
        console.print(
            f"[yellow]Recording {recording.hash_prefix} already analyzed "
            f"(status: {recording.analysis_status}). Use --force to re-analyze.[/yellow]"
        )
        raise typer.Exit(0)
    if analysis_tier == "full" and recording.analysis_status == "completed":
        console.print(
            f"[yellow]Recording {recording.hash_prefix} already fully analyzed. "
            f"Use --force to re-analyze.[/yellow]"
        )
        raise typer.Exit(0)
```

### Step 5: Tier-aware in-flight job reuse

**File:** same, extend the existing `"processing"` check at audio.py:1559-1570

```python
# Check if already processing
if recording.analysis_status == "processing" and recording.analysis_job_id and not force:
    # Verify the existing job's tier matches the requested tier
    try:
        existing_job = client.get_job(recording.analysis_job_id)
        job_is_fast = existing_job.job_type == "fast_analyze"
        tier_is_fast = analysis_tier == "fast"
        if job_is_fast != tier_is_fast:
            # Tier mismatch — submit a new job instead of reusing
            console.print(
                f"[yellow]Existing job {recording.analysis_job_id} is "
                f"{'fast' if job_is_fast else 'full'} tier; "
                f"requested {'fast' if tier_is_fast else 'full'}. "
                f"Submitting new job.[/yellow]"
            )
            skip_submission = False
        elif not wait:
            console.print(
                f"[yellow]Analysis already in progress for "
                f"{recording.hash_prefix} (job: {recording.analysis_job_id})[/yellow]"
            )
            raise typer.Exit(0)
        else:
            job_id = recording.analysis_job_id
            skip_submission = True
    except Exception:
        # Can't reach service or job not found — submit new
        skip_submission = False
```

### Step 6: Branch submission by tier

**File:** same, replace the `client.submit_analysis(...)` call at audio.py:1582-1587

```python
if analysis_tier == "fast":
    job = client.submit_fast_analysis(
        audio_url=recording.r2_audio_url,
        content_hash=recording.content_hash,
        force=force,
    )
else:
    job = client.submit_analysis(
        audio_url=recording.r2_audio_url,
        content_hash=recording.content_hash,
        generate_stems=not no_stems,
        force=force,
    )
```

### Step 7: Tier-aware DB writeback on `--wait`

**File:** same, replace the writeback block at audio.py:1649-1675

```python
if final_job.result:
    result = final_job.result
    # Determine effective tier from job type (in case of job reuse)
    effective_tier = "fast" if final_job.job_type == "fast_analyze" else "full"
    if effective_tier != analysis_tier:
        console.print(
            f"[yellow]Job type '{effective_tier}' differs from requested "
            f"'{analysis_tier}', treating as '{effective_tier}'[/yellow]"
        )

    # Status: partial for fast, completed for full
    status_to_set = "partial" if effective_tier == "fast" else "completed"
    # Don't downgrade an already-completed recording
    if effective_tier == "fast" and recording.analysis_status == "completed":
        status_to_set = "completed"

    db_client.update_recording_analysis(
        hash_prefix=recording.hash_prefix,
        duration_seconds=result.duration_seconds,
        tempo_bpm=result.tempo_bpm,
        musical_key=result.musical_key,
        musical_mode=result.musical_mode,
        key_confidence=result.key_confidence,
        key_algorithm_version=result.key_algorithm_version,
        key_score_margin=result.key_score_margin,
        key_window_agreement=result.key_window_agreement,
        key_candidates=(
            json.dumps(result.key_candidates)
            if isinstance(result.key_candidates, list)
            else result.key_candidates
        ),
        key_detected_at=result.key_detected_at,
        loudness_db=result.loudness_db,
        # Full-tier-only fields: only write for full tier
        beats=(
            json.dumps(result.beats)
            if effective_tier == "full" and result.beats
            else None
        ),
        downbeats=(
            json.dumps(result.downbeats)
            if effective_tier == "full" and result.downbeats
            else None
        ),
        sections=(
            json.dumps(result.sections)
            if effective_tier == "full" and result.sections
            else None
        ),
        embeddings_shape=(
            json.dumps(result.embeddings_shape)
            if effective_tier == "full" and result.embeddings_shape
            else None
        ),
        r2_stems_url=result.stems_url,
        analysis_status=status_to_set,
    )
```

Note: The DB layer already handles `analysis_status="partial"` correctly — it omits
full-only columns from the UPDATE entirely (db/client.py:994-1030), so passing `None`
for them is safe; they won't be written.

### Step 8: Update docstring

**File:** same, update the docstring at audio.py:1522-1526

```python
"""Submit a recording for analysis.

By default, submits fast analysis (librosa-only: tempo, key, loudness).
Use --analysis-tier full for structural analysis (beats, sections,
embeddings) via allin1, with optional stem separation.

Looks up the recording by song_id and submits it to the analysis service.
"""
```

## Tests

**File:** `ops/admin-cli/tests/admin/test_audio_commands.py`

### New tests to add (in `TestAnalyzeCommand` class)

1. **`test_analyze_default_tier_is_fast`** — Invoke `audio analyze song_001` without
   `--analysis-tier`. Assert `submit_fast_analysis` is called (NOT `submit_analysis`).

2. **`test_analyze_explicit_fast_tier`** — Invoke with `--analysis-tier fast`. Assert
   `submit_fast_analysis` called.

3. **`test_analyze_full_tier`** — Invoke with `--analysis-tier full`. Assert
   `submit_analysis` called with `generate_stems=True`.

4. **`test_analyze_full_tier_no_stems`** — Invoke with `--analysis-tier full
   --no-stems`. Assert `submit_analysis` called with `generate_stems=False`.

5. **`test_analyze_fast_tier_no_stems_warned`** — Invoke with `--analysis-tier fast
   --no-stems`. Assert exit 0, warning printed, `submit_fast_analysis` called.

6. **`test_analyze_invalid_tier`** — Invoke with `--analysis-tier bogus`. Assert exit 1,
   error message printed.

7. **`test_analyze_fast_skips_partial`** — Recording with
   `analysis_status="partial"`. Invoke `audio analyze song_001` (default fast). Assert
   exit 0, skipped, no submission.

8. **`test_analyze_full_does_not_skip_partial`** — Recording with
   `analysis_status="partial"`. Invoke `audio analyze song_001 --analysis-tier full
   --wait`. Assert `submit_analysis` called, `analysis_status` updated to `"completed"`.

9. **`test_analyze_fast_force_overrides_partial`** — Recording with
   `analysis_status="partial"`. Invoke `audio analyze song_001 --analysis-tier fast
   --force`. Assert `submit_fast_analysis` called.

10. **`test_analyze_fast_wait_sets_partial`** — Invoke `audio analyze song_001 --wait`
    (default fast) with mocked `wait_for_completion` returning a completed fast job.
    Assert DB `analysis_status="partial"`, and `beats`/`sections`/etc. NOT written.

11. **`test_analyze_fast_wait_preserves_completed`** — Recording already
    `analysis_status="completed"`. Invoke `audio analyze song_001 --force --wait`
    (default fast). Assert DB `analysis_status` stays `"completed"` (not downgraded to
    `"partial"`).

12. **`test_analyze_full_wait_sets_completed`** — Invoke `audio analyze song_001
    --analysis-tier full --wait`. Assert DB `analysis_status="completed"`, all fields
    written.

13. **`test_analyze_tier_mismatch_in_flight_job`** — Recording with
    `analysis_status="processing"`, existing job is `fast_analyze`. Invoke `audio analyze
    song_001 --analysis-tier full --wait`. Assert new `submit_analysis` job submitted
    (not reusing the fast job).

### Existing tests to update

14. **`test_analyze_already_completed_no_force`** (line 934) — Currently expects skip on
    `"completed"`. With default now `fast`, `"completed"` still skips for fast tier. But
    the recording in the test should be checked — if it's `"completed"`, fast tier
    should still skip. **No change needed** — behavior is correct.

15. **`test_analyze_already_completed_with_force`** (line 954) — Currently mocks
    `submit_analysis`. With default now `fast` and `--force`, should mock
    `submit_fast_analysis` instead. **Update mock** from
    `submit_analysis.return_value` to `submit_fast_analysis.return_value`.

16. **`test_analyze_fire_and_forget_success`** (line 1080) — Same: default is now fast.
    **Update mock** to `submit_fast_analysis`.

17. **`test_analyze_by_song_id`** (line 1113) — Same. **Update mock** to
    `submit_fast_analysis`, or explicitly pass `--analysis-tier full` to preserve the
    existing test's intent.

18. **`test_analyze_wait_mode_completed`** (line 1141) — Currently asserts
    `analysis_status == "completed"`. With default fast, the status would be
    `"partial"`. **Either:** (a) update to `--analysis-tier full` to preserve the
    full-tier writeback test, or (b) add a separate fast-tier test. **Recommend (a)** —
    change invocation to `--analysis-tier full --wait` to keep testing the full path,
    and add test #10 above for the fast path.

19. **`test_analyze_wait_mode_failed`** (line 1183) — Update mock to
    `submit_fast_analysis` (default tier).

20. **`test_analyze_wait_mode_timeout`** (line 1218) — Same.

21. **`test_analyze_no_stems_flag`** (line 1247) — Currently tests `--no-stems` with
    default (full) tier. **Update** to explicitly pass `--analysis-tier full` since
    default is now `fast`.

## Files Changed

| File | Change |
|---|---|
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | Add `--analysis-tier` param, validation, skip logic, submission branch, writeback logic, docstring |
| `ops/admin-cli/tests/admin/test_audio_commands.py` | 13 new tests, 8 existing tests updated |

**No service-side changes needed** — the Analysis Service already has
`POST /api/v1/jobs/fast-analyze`, `JobType.FAST_ANALYZE`, `analyze_audio_fast`, and
`SOW_FAST_ANALYZE_MAX_CONCURRENT`. **No DB migration needed** —
`analysis_status="partial"` is already supported by `db/client.py:994-1030`.

## Verification

```bash
# Run the updated test suite
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest ops/admin-cli/tests/admin/test_audio_commands.py::TestAnalyzeCommand -v

# Manual smoke test
export SOW_ANALYSIS_API_KEY=<key>
cd ops/analysis-service && docker compose up -d
sow-admin audio analyze <song_id> --analysis-tier fast --wait
sow-admin audio analyze <song_id> --analysis-tier full --wait
```

## Open Questions

1. **Should `_submit_analysis_job` helper (audio.py:578) also gain tier support?** It's
   used by `audio download --analyze --all`. Currently always submits full. A follow-up
   spec could add `--analysis-tier` to `audio download` as well. **Recommendation:** Out
   of scope for this spec; file a follow-up if needed.

2. **Should the `audio download --analyze` flag default to fast tier too?** If yes,
   that's a separate spec. **Recommendation:** Same as above — out of scope.
