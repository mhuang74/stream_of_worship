# Plan: Scale Musical Key & BPM Analysis to Hundreds of Songs (v2)

## Overview

Add a **two-tier analysis system** and **batch CLI tooling** so that musical key, BPM, loudness, and duration can be determined for hundreds of songs overnight on a single CPU box.

- **Tier 1 (fast)** — librosa-only pipeline: key (Krumhansl-Schmuckler), BPM (`librosa.beat`), loudness (RMS), duration. Runs in ~10-15s/song on CPU at 22050 Hz. Produces `analysis_status='partial'`.
- **Tier 2 (full)** — existing allin1 pipeline: beats, downbeats, sections, embeddings + key/BPM/loudness. Runs in ~30-60s/song. Produces `analysis_status='completed'`.

Phase 1 (external metadata lookup via Spotify/cyanite APIs) is **excluded** from this plan — the fast tier is quick enough to stand alone.

## Current Architecture

### BPM & Key Determination

| Concern | Implementation | Location |
|---------|---------------|----------|
| BPM | `allin1.analyze()` → `result.bpm` | `ops/analysis-service/src/sow_analysis/workers/analyzer.py:117-125` |
| Key | Custom Krumhansl-Schmuckler via `librosa.feature.chroma_cqt` + profile correlation | `ops/analysis-service/src/sow_analysis/workers/analyzer.py:27-50` |
| Loudness | RMS-based `compute_loudness()` | `ops/analysis-service/src/sow_analysis/workers/analyzer.py:53-65` |
| Orchestration | `analyze_audio()` async function, runs allin1 + librosa key + loudness in one pass | `ops/analysis-service/src/sow_analysis/workers/analyzer.py:68-182` |

### Job Pipeline

1. Admin CLI `sow-admin audio analyze <song_id>` (`ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:1474`) submits **one** job via `AnalysisClient.submit_analysis()` → `POST /api/v1/jobs/analyze`
2. Analysis service `JobQueue.submit(JobType.ANALYZE, request)` enqueues (`ops/analysis-service/src/sow_analysis/workers/queue.py:259`)
3. `_process_job_with_semaphore()` acquires `_local_model_semaphore` (default `SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS=1`) for the **entire** analysis job (`queue.py:382-385`)
4. `_process_analysis_job()` downloads audio from R2, calls `analyze_audio()`, optionally separates stems, uploads `analysis.json` to R2, builds `JobResult` (`queue.py:422-591`)
5. Admin CLI polls `GET /api/v1/jobs/{job_id}` and writes results to DB via `db_client.update_recording_analysis()` (`audio.py:1611-1626`)

### Key Bottlenecks

- **No batch analyze command** — `analyze` takes a single `song_id`, no `--stdin` support (unlike `lrc` and `align-lrc`)
- **Serial execution** — `SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS=1` default; allin1 dominates (~30-60s/song on CPU)
- **Full-song chroma at 44.1kHz** — `librosa.load(sr=None)` + `chroma_cqt(hop_length=512)` is expensive
- **allin1 always runs** — even when only key+BPM are needed, the full structure analyzer runs

### DB Schema (relevant columns)

From `ops/admin-cli/src/stream_of_worship/admin/db/schema.py:31-78`:

```sql
recordings:
  content_hash TEXT PRIMARY KEY
  hash_prefix TEXT NOT NULL UNIQUE
  analysis_status TEXT DEFAULT 'pending'  -- pending|processing|completed|failed|partial
  analysis_job_id TEXT
  duration_seconds REAL
  tempo_bpm REAL
  musical_key TEXT
  musical_mode TEXT
  key_confidence REAL
  loudness_db REAL
  beats TEXT          -- JSON array (full tier only)
  downbeats TEXT      -- JSON array (full tier only)
  sections TEXT       -- JSON array (full tier only)
  embeddings_shape TEXT  -- JSON array (full tier only)
```

### Cache Layer

`CacheManager` (`ops/analysis-service/src/sow_analysis/storage/cache.py`) caches analysis results by content hash (`{hash_prefix}.json`). Fast-tier results use a distinct `{hash_prefix}_fast.json` suffix to avoid collisions with full-tier results. Fast-tier results are **not** uploaded to R2 — they live only in the local service cache and the admin DB.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Tier naming | `fast` vs `full` | Intuitive, matches user preference |
| Default tier | `fast` | Breaking change accepted; fast tier is the common case for batch operations |
| Fast tier job type | New `FAST_ANALYZE` job type | Separate semaphore control; fast jobs don't need the local-model semaphore (no allin1/demucs) |
| Partial result status | New `analysis_status='partial'` | Distinguishes fast-tier results from full-tier; allows `--all-pending` to target unanalyzed songs without re-running completed ones |
| Fast tier audio params | Full song @ 22050 Hz, `hop_length=4096` | User preference; safest for accuracy, ~10-15s/song |
| Fast tier R2 upload | **None** | Avoids clobbering full-tier `analysis.json`; fast results are transient and live in DB + local cache only |
| Fast tier cache key | `{hash_prefix}_fast.json` | Avoids collision with full-tier `{hash_prefix}.json`; respects `force` flag |
| Fast tier stems | Never | Stems require demucs (heavy); fast tier skips entirely |
| Fast tier result fields | duration, tempo_bpm, musical_key, musical_mode, key_confidence, loudness_db | Beats/downbeats/sections/embeddings left NULL |
| Batch wait mode | `--wait` supported with progress table + state file | User preference; useful for overnight runs; state file survives CLI crashes |
| Batch manifest | JSON file with job IDs, song IDs, tier, force flag | Enables `--resume-from` to continue interrupted batches without double-submission |
| Batch size limit | `--limit N` | Prevents accidental submission of thousands of jobs; caps memory and queue pressure |
| Stuck job recovery | Reuse existing startup recovery (`get_interrupted_jobs`) | Service already recovers LRC/analysis jobs on startup; extend to FAST_ANALYZE |
| Polling strategy | Individual `GET /jobs/{id}` for ≤200 jobs; `list_jobs` client-side filter for larger batches | Simple for MVP; document scalability limit |

## Implementation Plan

### Phase A: Analysis Service — Fast Analyze Job Type

#### A1. Add `FAST_ANALYZE` job type

**File**: `ops/analysis-service/src/sow_analysis/models.py`

```python
class JobType(str, Enum):
    ANALYZE = "analyze"
    FAST_ANALYZE = "fast_analyze"  # NEW
    LRC = "lrc"
    STEM_SEPARATION = "stem_separation"
    EMBEDDING = "embedding"
    FORCED_ALIGNMENT = "forced_alignment"
```

Add new request/options models:

```python
class FastAnalyzeOptions(BaseModel):
    """Options for fast analysis jobs (librosa-only, no allin1)."""
    force: bool = False
    sample_rate: int = 22050
    hop_length: int = 4096

class FastAnalyzeJobRequest(BaseModel):
    """Request to submit a fast analysis job."""
    audio_url: str
    content_hash: str
    options: FastAnalyzeOptions = Field(default_factory=FastAnalyzeOptions)
```

Update `Job.request` union type to include `FastAnalyzeJobRequest`.

The existing `JobResult` model already has all the fields needed (`duration_seconds`, `tempo_bpm`, `musical_key`, `musical_mode`, `key_confidence`, `loudness_db`). The fast tier simply leaves `beats`, `downbeats`, `sections`, `embeddings_shape`, `stems_url` as `None`.

#### A2. Implement fast analysis function

**File**: `ops/analysis-service/src/sow_analysis/workers/analyzer.py`

Add a new function `analyze_audio_fast()` alongside the existing `analyze_audio()`:

```python
def detect_key_fast(y: np.ndarray, sr: int, hop_length: int = 4096) -> tuple[str, str, float]:
    """Detect musical key using Krumhansl-Schmuckler with optimized chroma.

    Same algorithm as detect_key() but with larger hop_length for speed.
    """
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)
    chroma_avg = np.mean(chroma, axis=1)
    # ... (same correlation logic as existing detect_key)

def estimate_tempo(y: np.ndarray, sr: int) -> float:
    """Estimate tempo using librosa.beat.beat_track (lightweight, no allin1)."""
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    # librosa may return array; extract scalar
    if isinstance(tempo, np.ndarray):
        tempo = float(tempo.item())
    return float(tempo)

async def analyze_audio_fast(
    audio_path: Path,
    cache_manager: CacheManager,
    content_hash: str,
    sample_rate: int = 22050,
    hop_length: int = 4096,
    force: bool = False,
) -> dict:
    """Analyze audio using librosa only (no allin1, no stems).

    Returns: dict with duration_seconds, tempo_bpm, musical_key, musical_mode,
             key_confidence, loudness_db.
    """
    # Check fast-tier cache (distinct from full-tier cache)
    if not force:
        cached = cache_manager.get_fast_analysis_result(content_hash)
        if cached:
            return cached

    # Load audio at reduced sample rate
    y, sr = librosa.load(str(audio_path), sr=sample_rate, mono=True)
    duration = librosa.get_duration(y=y, sr=sr)

    # Tempo (librosa beat tracking)
    tempo_bpm = estimate_tempo(y, sr)

    # Key (Krumhansl-Schmuckler with optimized hop)
    mode, key, key_confidence = detect_key_fast(y, sr, hop_length=hop_length)

    # Loudness (RMS)
    loudness_db = compute_loudness(y)

    result = {
        "duration_seconds": duration,
        "tempo_bpm": tempo_bpm,
        "musical_key": key,
        "musical_mode": mode,
        "key_confidence": key_confidence,
        "loudness_db": loudness_db,
    }

    cache_manager.save_fast_analysis_result(content_hash, result)
    return result
```

**Cache methods** (Option A from v1, required):

```python
# ops/analysis-service/src/sow_analysis/storage/cache.py

def get_fast_analysis_result(self, content_hash: str) -> Optional[dict]:
    """Check if fast analysis result exists in cache."""
    hash_prefix = self._get_hash_prefix(content_hash)
    cache_file = self.cache_dir / f"{hash_prefix}_fast.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except (json.JSONDecodeError, IOError):
            return None
    return None

def save_fast_analysis_result(self, content_hash: str, result: dict) -> Path:
    """Save fast analysis result to cache."""
    hash_prefix = self._get_hash_prefix(content_hash)
    cache_file = self.cache_dir / f"{hash_prefix}_fast.json"
    cache_file.write_text(json.dumps(result, indent=2))
    return cache_file
```

#### A3. Add API route

**File**: `ops/analysis-service/src/sow_analysis/routes/jobs.py`

```python
@router.post("/jobs/fast-analyze", response_model=JobResponse)
async def submit_fast_analysis_job(
    request: FastAnalyzeJobRequest,
    api_key: str = Depends(verify_api_key),
) -> JobResponse:
    """Submit audio for fast analysis (librosa-only, no allin1)."""
    if job_queue is None:
        raise HTTPException(500, "Job queue not initialized")
    job = await job_queue.submit(JobType.FAST_ANALYZE, request)
    return job_to_response(job)
```

#### A4. Add job processing in queue

**File**: `ops/analysis-service/src/sow_analysis/workers/queue.py`

Add import:
```python
try:
    from .analyzer import analyze_audio, analyze_audio_fast
    from .separator import separate_stems
except ImportError:
    analyze_audio = None
    analyze_audio_fast = None
    separate_stems = None
```

Wire into `_process_job_with_semaphore()`:

```python
elif job.type == JobType.FAST_ANALYZE:
    # Fast analysis uses only librosa (CPU-bound but lightweight)
    # Does NOT acquire _local_model_semaphore — allin1/demucs not involved
    async with self._fast_analyze_semaphore:
        await self._process_fast_analyze_job(job)
```

**Key design decision**: Fast analyze jobs acquire `_fast_analyze_semaphore` (not `_local_model_semaphore`). This bounds concurrency to a configurable limit to avoid CPU oversubscription, while still allowing parallelism with full analysis jobs.

Add `_process_fast_analyze_job()` method:

```python
async def _process_fast_analyze_job(self, job: Job) -> None:
    """Process a fast analysis job (librosa-only)."""
    set_job_id(job.id)
    job_start_time = time.time()
    logger.info(f"Starting fast analysis job for audio: {job.request.audio_url}")

    job.status = JobStatus.PROCESSING
    job.stage = "downloading"
    job.progress = 0.1
    await self.job_store.update_job(job.id, status="processing", stage="downloading", progress=0.1)

    request = job.request
    if not isinstance(request, FastAnalyzeJobRequest):
        job.status = JobStatus.FAILED
        job.error_message = "Invalid request type for fast analysis job"
        await self.job_store.update_job(job.id, status="failed", error_message=job.error_message)
        return

    if analyze_audio_fast is None:
        job.status = JobStatus.FAILED
        job.error_message = "Fast analysis dependencies not available (librosa)"
        job.stage = "missing_dependencies"
        await self.job_store.update_job(
            job.id, status="failed", stage="missing_dependencies", error_message=job.error_message
        )
        return

    try:
        if not self.r2_client and settings.SOW_R2_ENDPOINT_URL:
            self.initialize_r2(settings.SOW_R2_BUCKET, settings.SOW_R2_ENDPOINT_URL)

        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            audio_path = temp_path / "audio.mp3"

            if self.r2_client:
                await self.r2_client.download_audio(request.audio_url, audio_path)

            job.stage = "analyzing"
            job.progress = 0.3
            await self.job_store.update_job(job.id, stage="analyzing", progress=0.3)

            # Run fast analysis in thread pool (librosa is blocking)
            loop = asyncio.get_event_loop()
            analysis_result = await loop.run_in_executor(
                None,
                lambda: analyze_audio_fast(
                    audio_path,
                    self.cache_manager,
                    request.content_hash,
                    sample_rate=request.options.sample_rate,
                    hop_length=request.options.hop_length,
                    force=request.options.force,
                ),
            )

            job.progress = 0.8

            # Fast tier does NOT upload to R2 — results go to cache + job result only
            # This avoids clobbering full-tier analysis.json

            # Build job result (beats/downbeats/sections/embeddings stay None)
            job.result = JobResult(
                duration_seconds=analysis_result.get("duration_seconds"),
                tempo_bpm=analysis_result.get("tempo_bpm"),
                musical_key=analysis_result.get("musical_key"),
                musical_mode=analysis_result.get("musical_mode"),
                key_confidence=analysis_result.get("key_confidence"),
                loudness_db=analysis_result.get("loudness_db"),
            )

            job.status = JobStatus.COMPLETED
            job.progress = 1.0
            job.stage = "complete"
            total_elapsed = time.time() - job_start_time
            logger.info(f"Fast analysis job completed in {total_elapsed:.2f}s")

            try:
                await self.job_store.update_job(
                    job.id,
                    status="completed",
                    progress=1.0,
                    stage="complete",
                    result_json=job.result.model_dump_json() if job.result else None,
                )
            except Exception as e:
                logger.error(f"Failed to update job {job.id} in database: {e}")

    except Exception as e:
        job.status = JobStatus.FAILED
        job.error_message = str(e)
        job.stage = "error"
        logger.error(f"Fast analysis job failed: {e}")
        try:
            await self.job_store.update_job(
                job.id, status="failed", stage="error", error_message=str(e)
            )
        except Exception as db_err:
            logger.error(f"Failed to update job {job.id} in database: {db_err}")
    finally:
        job.updated_at = datetime.now(timezone.utc)
```

#### A5. Config: fast analyze concurrency

**File**: `ops/analysis-service/src/sow_analysis/config.py`

```python
SOW_FAST_ANALYZE_MAX_CONCURRENT: int = 4
```

**File**: `ops/analysis-service/src/sow_analysis/workers/queue.py` — in `__init__()`:

```python
self._fast_analyze_semaphore = asyncio.Semaphore(settings.SOW_FAST_ANALYZE_MAX_CONCURRENT)
```

**Operational note**: `SOW_FAST_ANALYZE_MAX_CONCURRENT` should be tuned to `os.cpu_count()` or slightly less. Each fast job is CPU-bound (librosa CQT + beat tracking). Setting this to 4 on a 4-core box means each job takes ~4x longer but throughput stays roughly constant. For true throughput gains on single-CPU boxes, consider `max(1, os.cpu_count() // 2)`.

#### A6. Stuck job recovery on startup

**File**: `ops/analysis-service/src/sow_analysis/workers/queue.py`

The existing `initialize()` method (line 211-258) already recovers interrupted jobs via `get_interrupted_jobs()`. This is generic — it recovers **all** jobs with `status='processing'` that were in-flight when the service died. No code change is required for FAST_ANALYZE jobs to be recovered, but we must verify that `JobStore.get_interrupted_jobs()` does not filter by `JobType`.

Verify `ops/analysis-service/src/sow_analysis/storage/db.py`:

```python
# get_interrupted_jobs should query WHERE status = 'processing' without type filter
```

If it does filter by type, remove the filter. Document this behavior in the plan.

### Phase B: Admin CLI — Batch Analyze Command

#### B1. Add `AnalysisClient.submit_fast_analysis()` method

**File**: `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`

```python
def submit_fast_analysis(
    self,
    audio_url: str,
    content_hash: str,
    force: bool = False,
) -> JobInfo:
    """Submit audio for fast analysis (librosa-only)."""
    payload = {
        "audio_url": audio_url,
        "content_hash": content_hash,
        "options": {
            "force": force,
        },
    }
    try:
        response = requests.post(
            f"{self.base_url}/api/v1/jobs/fast-analyze",
            json=payload,
            headers=self._auth_headers(),
            timeout=self.timeout,
        )
        if response.status_code == 401:
            raise AnalysisServiceError(
                "Authentication failed: Invalid API key", status_code=401
            )
        response.raise_for_status()
        data = response.json()
        return self._parse_job_response(data)
    except requests.exceptions.ConnectionError as e:
        raise AnalysisServiceError(
            f"Cannot connect to analysis service at {self.base_url}: {e}"
        )
    except requests.exceptions.RequestException as e:
        raise AnalysisServiceError(f"Failed to submit fast analysis: {e}")
```

#### B2. Refactor `analyze` command to support `--stdin`, `--tier`, `--wait`, `--limit`, `--manifest`, `--state-file`, `--dry-run`

**File**: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

Replace the existing `analyze_recording` command (line 1474) with an extended version:

```python
@app.command("analyze")
def analyze_recording(
    song_id: Optional[str] = typer.Argument(None, help="Song ID to analyze"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-analysis"),
    no_stems: bool = typer.Option(False, "--no-stems", help="Skip stem separation (full tier only)"),
    tier: str = typer.Option(
        "fast",
        "--tier",
        "-t",
        help="Analysis tier: 'fast' (librosa-only, key+BPM, default) or 'full' (allin1, complete)",
    ),
    stdin: bool = typer.Option(False, "--stdin", help="Read song IDs from stdin (one per line)"),
    all_pending: bool = typer.Option(
        False, "--all-pending", help="Analyze all recordings with pending/failed analysis status"
    ),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for analysis to complete"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Maximum songs to process in this batch"),
    manifest: Optional[Path] = typer.Option(None, "--manifest", "-m", help="Path to batch manifest JSON (for resume)"),
    resume_from: Optional[Path] = typer.Option(None, "--resume-from", help="Resume a previous batch from its manifest file"),
    state_file: Optional[Path] = typer.Option(None, "--state-file", "-s", help="Path to write incremental batch state (defaults to manifest path + .state.json)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be submitted without submitting"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Submit a recording for analysis.

    Defaults to --tier fast (librosa-only, key+BPM/loudness, ~10-15s/song).
    Use --tier full for complete analysis with beats/sections/embeddings (allin1, ~30-60s/song).

    For batch processing, pipe song IDs via stdin:
        sow-admin audio list --status incomplete --format ids | sow-admin audio analyze --stdin --tier fast

    Or analyze all pending recordings:
        sow-admin audio analyze --all-pending --tier fast --wait

    Resume an interrupted batch:
        sow-admin audio analyze --resume-from /tmp/batch_20240101_120000.json --wait
    """
    # Validate tier
    if tier not in ("fast", "full"):
        console.print(f"[red]Invalid tier: {tier}. Must be 'fast' or 'full'.[/red]")
        raise typer.Exit(1)

    # Validate mutually exclusive inputs
    input_modes = [bool(song_id), stdin, all_pending, bool(resume_from)]
    if sum(input_modes) == 0:
        console.print("[red]Error: Provide a song_id, --stdin, --all-pending, or --resume-from[/red]")
        raise typer.Exit(1)
    if sum(input_modes) > 1:
        console.print("[red]Error: Use only one of: song_id, --stdin, --all-pending, --resume-from[/red]")
        raise typer.Exit(1)

    # ... load config, db_client, analysis_client (same boilerplate)

    # Resolve song IDs and manifest
    if resume_from:
        song_ids, manifest_data = _load_batch_manifest(resume_from)
        tier = manifest_data.get("tier", tier)
        force = manifest_data.get("force", force)
        no_stems = manifest_data.get("no_stems", no_stems)
        if not manifest:
            manifest = resume_from
    elif all_pending:
        # Query recordings with analysis_status in ('pending', 'failed') or 'partial' when tier=full
        if tier == "fast":
            recordings = db_client.list_recordings(status="incomplete")
        else:  # full tier — also re-analyze 'partial' recordings
            recordings = db_client.list_recordings(status="incomplete")
            partial = db_client.list_recordings(status="partial")
            recordings.extend(partial)
        # Deterministic ordering: by imported_at ASC for reproducible batches
        recordings.sort(key=lambda r: r.imported_at or "")
        song_ids = [r.song_id for r in recordings if r.song_id]
        if limit:
            song_ids = song_ids[:limit]
        if not song_ids:
            console.print("[green]No pending recordings to analyze.[/green]")
            return
        console.print(f"[cyan]Found {len(song_ids)} recording(s) to analyze.[/cyan]")
    elif stdin:
        song_ids = _read_song_ids_from_stdin()
        if limit:
            song_ids = song_ids[:limit]
        if not song_ids:
            console.print("[yellow]No song IDs provided via stdin[/yellow]")
            raise typer.Exit(0)
    else:
        song_ids = [song_id]

    # Dry run: print and exit
    if dry_run:
        console.print(f"[cyan]Dry run ({tier} tier): would process {len(song_ids)} song(s)[/cyan]")
        for sid in song_ids:
            console.print(f"  {sid}")
        return

    # Dispatch to single or batch handler
    if len(song_ids) == 1:
        _submit_analysis_single(
            song_id=song_ids[0],
            tier=tier,
            db_client=db_client,
            analysis_client=analysis_client,
            force=force,
            no_stems=no_stems,
            wait=wait,
            console=console,
        )
    else:
        _submit_analysis_batch(
            song_ids=song_ids,
            tier=tier,
            db_client=db_client,
            analysis_client=analysis_client,
            force=force,
            no_stems=no_stems,
            wait=wait,
            manifest_path=manifest,
            state_file_path=state_file,
            console=console,
        )
```

#### B3. Implement `_submit_analysis_single()`

**File**: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

Refactor the existing single-song logic (currently inline in `analyze_recording`) into a helper:

```python
def _submit_analysis_single(
    song_id: str,
    tier: str,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    force: bool,
    no_stems: bool,
    wait: bool,
    console: Console,
) -> None:
    """Submit analysis for a single recording."""
    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        console.print(f"[red]No recording found for {song_id}.[/red]")
        raise typer.Exit(1)

    if not recording.r2_audio_url:
        console.print(f"[red]Recording {recording.hash_prefix} has no audio URL.[/red]")
        raise typer.Exit(1)

    # Check existing status
    if tier == "fast":
        if recording.analysis_status in ("completed", "partial") and not force:
            console.print(
                f"[yellow]Recording {recording.hash_prefix} already analyzed "
                f"(status: {recording.analysis_status}). Use --force to re-analyze.[/yellow]"
            )
            raise typer.Exit(0)
    else:  # full tier
        if recording.analysis_status == "completed" and not force:
            console.print(
                f"[yellow]Recording {recording.hash_prefix} already fully analyzed. "
                f"Use --force to re-analyze.[/yellow]"
            )
            raise typer.Exit(0)

    # If already processing and --wait, poll existing job instead of re-submitting
    if recording.analysis_status == "processing" and recording.analysis_job_id and not force:
        if wait:
            console.print(f"[cyan]Polling existing job: {recording.analysis_job_id}[/cyan]")
            _wait_for_analysis_completion(
                job_id=recording.analysis_job_id,
                recording=recording,
                tier=tier,
                analysis_client=analysis_client,
                db_client=db_client,
                console=console,
            )
            return
        else:
            console.print(
                f"[yellow]Analysis already in progress for "
                f"{recording.hash_prefix} (job: {recording.analysis_job_id})[/yellow]"
            )
            raise typer.Exit(0)

    # Submit job
    if tier == "fast":
        job = analysis_client.submit_fast_analysis(
            audio_url=recording.r2_audio_url,
            content_hash=recording.content_hash,
            force=force,
        )
    else:
        job = analysis_client.submit_analysis(
            audio_url=recording.r2_audio_url,
            content_hash=recording.content_hash,
            generate_stems=not no_stems,
            force=force,
        )

    job_id = job.job_id
    db_client.update_recording_status(
        hash_prefix=recording.hash_prefix,
        analysis_status="processing",
        analysis_job_id=job_id,
    )
    console.print(f"[green]Analysis submitted (job: {job_id}, tier: {tier})[/green]")

    if wait:
        _wait_for_analysis_completion(
            job_id=job_id,
            recording=recording,
            tier=tier,
            analysis_client=analysis_client,
            db_client=db_client,
            console=console,
        )
```

#### B4. Implement `_submit_analysis_batch()`

**File**: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

```python
def _submit_analysis_batch(
    song_ids: list[str],
    tier: str,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    force: bool,
    no_stems: bool,
    wait: bool,
    manifest_path: Optional[Path],
    state_file_path: Optional[Path],
    console: Console,
) -> None:
    """Submit analysis for multiple recordings (batch mode)."""
    submitted = 0
    skipped = 0
    errors = 0
    job_entries: list[dict] = []  # {job_id, hash_prefix, song_id, status}

    # Resolve default manifest/state paths
    if manifest_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        manifest_path = Path(f"/tmp/sow_analyze_batch_{timestamp}.json")
    if state_file_path is None:
        state_file_path = manifest_path.with_suffix(".state.jsonl")

    for i, song_id in enumerate(song_ids, 1):
        console.print(f"[{i}/{len(song_ids)}] Processing {song_id}...")

        recording = db_client.get_recording_by_song_id(song_id)
        if not recording:
            console.print("  [red]No recording found[/red]")
            errors += 1
            job_entries.append({"song_id": song_id, "status": "error", "error": "No recording found"})
            continue

        if not recording.r2_audio_url:
            console.print("  [red]No audio URL[/red]")
            errors += 1
            job_entries.append({"song_id": song_id, "hash_prefix": recording.hash_prefix, "status": "error", "error": "No audio URL"})
            continue

        # Skip logic based on tier
        if tier == "fast":
            if recording.analysis_status in ("completed", "partial") and not force:
                console.print(f"  [yellow]Already analyzed (status: {recording.analysis_status})[/yellow]")
                skipped += 1
                job_entries.append({"song_id": song_id, "hash_prefix": recording.hash_prefix, "status": "skipped", "reason": f"status={recording.analysis_status}"})
                continue
        else:  # full
            if recording.analysis_status == "completed" and not force:
                console.print("  [yellow]Already fully analyzed[/yellow]")
                skipped += 1
                job_entries.append({"song_id": song_id, "hash_prefix": recording.hash_prefix, "status": "skipped", "reason": "status=completed"})
                continue

        # If already processing and not forced, skip re-submission
        if recording.analysis_status == "processing" and recording.analysis_job_id and not force:
            console.print(f"  [yellow]Already processing (job: {recording.analysis_job_id})[/yellow]")
            skipped += 1
            job_entries.append({"song_id": song_id, "hash_prefix": recording.hash_prefix, "job_id": recording.analysis_job_id, "status": "processing"})
            continue

        try:
            if tier == "fast":
                job = analysis_client.submit_fast_analysis(
                    audio_url=recording.r2_audio_url,
                    content_hash=recording.content_hash,
                    force=force,
                )
            else:
                job = analysis_client.submit_analysis(
                    audio_url=recording.r2_audio_url,
                    content_hash=recording.content_hash,
                    generate_stems=not no_stems,
                    force=force,
                )

            db_client.update_recording_status(
                hash_prefix=recording.hash_prefix,
                analysis_status="processing",
                analysis_job_id=job.job_id,
            )
            console.print(f"  [green]Submitted (job: {job.job_id})[/green]")
            job_entries.append({"song_id": song_id, "hash_prefix": recording.hash_prefix, "job_id": job.job_id, "status": "submitted"})
            submitted += 1

        except AnalysisServiceError as e:
            console.print(f"  [red]Failed to submit: {e}[/red]")
            errors += 1
            job_entries.append({"song_id": song_id, "hash_prefix": recording.hash_prefix, "status": "error", "error": str(e)})
        except Exception as e:
            console.print(f"  [red]Unexpected error: {e}[/red]")
            errors += 1
            job_entries.append({"song_id": song_id, "hash_prefix": recording.hash_prefix, "status": "error", "error": str(e)})

    # Write manifest
    manifest_data = {
        "version": 1,
        "tier": tier,
        "force": force,
        "no_stems": no_stems,
        "created_at": datetime.now().isoformat(),
        "total": len(song_ids),
        "submitted": submitted,
        "skipped": skipped,
        "errors": errors,
        "jobs": job_entries,
    }
    manifest_path.write_text(json.dumps(manifest_data, indent=2))
    console.print(f"\n[cyan]Manifest written to {manifest_path}[/cyan]")

    # Summary
    console.print("")
    console.print("[cyan]Batch Summary:[/cyan]")
    console.print(f"  Submitted: {submitted}")
    console.print(f"  Skipped: {skipped}")
    console.print(f"  Errors: {errors}")
    console.print(f"  Total: {len(song_ids)}")

    # Wait mode: poll all jobs
    if wait and submitted > 0:
        _wait_for_batch_completion(
            manifest_data=manifest_data,
            tier=tier,
            analysis_client=analysis_client,
            db_client=db_client,
            state_file_path=state_file_path,
            console=console,
        )
```

#### B5. Implement `_wait_for_batch_completion()`

**File**: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

```python
def _wait_for_batch_completion(
    manifest_data: dict,
    tier: str,
    analysis_client: AnalysisClient,
    db_client: DatabaseClient,
    state_file_path: Path,
    console: Console,
) -> None:
    """Poll all submitted jobs until they complete, then write results to DB."""
    jobs = manifest_data["jobs"]
    submitted_jobs = [j for j in jobs if j.get("status") == "submitted" or j.get("status") == "processing"]

    console.print(f"\n[cyan]Waiting for {len(submitted_jobs)} job(s) to complete...[/cyan]")

    completed = 0
    failed = 0
    pending = list(submitted_jobs)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task(
            f"Analyzing ({tier})...",
            total=len(submitted_jobs),
            completed=0,
        )

        while pending:
            still_pending = []
            for entry in pending:
                job_id = entry.get("job_id")
                hash_prefix = entry.get("hash_prefix")
                song_id = entry.get("song_id")
                if not job_id:
                    continue

                try:
                    job_info = analysis_client.get_job(job_id)
                    if job_info.status == "completed":
                        _store_analysis_result(
                            hash_prefix=hash_prefix,
                            tier=tier,
                            job_info=job_info,
                            db_client=db_client,
                        )
                        entry["status"] = "completed"
                        entry["completed_at"] = datetime.now().isoformat()
                        completed += 1
                        progress.update(task, completed=completed + failed)
                    elif job_info.status == "failed":
                        console.print(f"  [red]Failed: {song_id} ({job_id})[/red]")
                        db_client.update_recording_status(
                            hash_prefix=hash_prefix,
                            analysis_status="failed",
                        )
                        entry["status"] = "failed"
                        entry["error"] = job_info.error_message or "Unknown error"
                        failed += 1
                        progress.update(task, completed=completed + failed)
                    else:
                        still_pending.append(entry)
                except AnalysisServiceError as e:
                    console.print(f"  [red]Error polling {song_id}: {e}[/red]")
                    still_pending.append(entry)

            # Write incremental state
            _write_batch_state(state_file_path, manifest_data)

            pending = still_pending
            if pending:
                time.sleep(5.0)  # Poll interval

    console.print(f"\n[bold]Summary:[/bold] {completed} completed, {failed} failed")
```

#### B6. Implement `_store_analysis_result()` helper

**File**: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

This helper centralizes result storage and sets the correct status (`partial` for fast tier, `completed` for full tier):

```python
def _store_analysis_result(
    hash_prefix: str,
    tier: str,
    job_info: JobInfo,
    db_client: DatabaseClient,
) -> None:
    """Store analysis result in DB with appropriate status."""
    if not job_info.result:
        return

    result = job_info.result
    status = "partial" if tier == "fast" else "completed"

    db_client.update_recording_analysis(
        hash_prefix=hash_prefix,
        duration_seconds=result.duration_seconds,
        tempo_bpm=result.tempo_bpm,
        musical_key=result.musical_key,
        musical_mode=result.musical_mode,
        key_confidence=result.key_confidence,
        loudness_db=result.loudness_db,
        beats=json.dumps(result.beats) if result.beats else None,
        downbeats=json.dumps(result.downbeats) if result.downbeats else None,
        sections=json.dumps(result.sections) if result.sections else None,
        embeddings_shape=(
            json.dumps(result.embeddings_shape) if result.embeddings_shape else None
        ),
        r2_stems_url=result.stems_url,
        analysis_status=status,  # NEW parameter
    )
```

**Required DB change**: `update_recording_analysis()` must accept an optional `analysis_status` parameter:

```python
def update_recording_analysis(
    self,
    hash_prefix: str,
    ...,
    analysis_status: str = "completed",  # NEW parameter
) -> None:
    # ... use analysis_status instead of hardcoded 'completed'
```

#### B7. Manifest and state file helpers

**File**: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

```python
def _load_batch_manifest(manifest_path: Path) -> tuple[list[str], dict]:
    """Load a batch manifest and return song IDs + manifest data."""
    data = json.loads(manifest_path.read_text())
    song_ids = [j["song_id"] for j in data.get("jobs", []) if j.get("song_id")]
    return song_ids, data

def _write_batch_state(state_file_path: Path, manifest_data: dict) -> None:
    """Write incremental batch state as JSON lines (append-friendly)."""
    # Overwrite with current manifest data for simplicity
    state_file_path.write_text(json.dumps(manifest_data, indent=2))
```

### Phase C: DB Schema — Add `partial` Status Support

#### C1. Update `list_recordings()` to support `partial` status

**File**: `ops/admin-cli/src/stream_of_worship/admin/db/client.py`

The existing `list_recordings()` method (line 664) already handles `status="incomplete"` as `IN ('pending', 'processing', 'failed')`. `partial` is **not** included in `incomplete`.

Update the `incomplete` filter (no change needed — it already excludes `partial`):

```python
if status == "incomplete":
    query += " AND analysis_status IN ('pending', 'processing', 'failed')"
```

Add explicit `partial` filter support:

```python
elif status == "partial":
    query += " AND analysis_status = 'partial'"
```

The CLI handles `--all-pending --tier full` by calling `list_recordings(status="incomplete")` and `list_recordings(status="partial")` separately and merging (as shown in B2).

#### C2. Update `Recording.has_analysis` property

**File**: `ops/admin-cli/src/stream_of_worship/admin/db/models.py`

```python
@property
def has_analysis(self) -> bool:
    """Check if analysis is complete (full or partial).

    Returns:
        True if analysis_status is 'completed' or 'partial'.
    """
    return self.analysis_status in ("completed", "partial")

@property
def has_full_analysis(self) -> bool:
    """Check if recording has full analysis (beats/sections/embeddings).

    Returns:
        True if analysis_status is 'completed'.
    """
    return self.analysis_status == "completed"
```

**Breaking change**: `has_analysis` now returns `True` for `partial`. Callers that need full analysis should use `has_full_analysis`. Audit all usages:
- `audio show` — should show partial results (see C3)
- `audio list` — status display is fine
- Any other commands filtering on `has_analysis` — verify behavior is acceptable with partial data

#### C3. Update `audio show` command to display partial status

**File**: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

In `show_recording()` (line 1316), update the analysis results display:

```python
# Show analysis tier indicator
if recording.analysis_status == "partial":
    info_lines.append("[cyan]Analysis:[/cyan] [yellow]partial (key+BPM only)[/yellow]")
elif recording.analysis_status == "completed":
    info_lines.append("[cyan]Analysis:[/cyan] [green]complete[/green]")

# Analysis results (shown when analysis is complete OR partial)
if recording.has_analysis:
    info_lines.append("")
    info_lines.append("[bold]Analysis Results:[/bold]")
    if recording.duration_seconds is not None:
        info_lines.append(f"[cyan]Duration:[/cyan] {recording.formatted_duration}")
    if recording.tempo_bpm is not None:
        info_lines.append(f"[cyan]Tempo:[/cyan] {recording.tempo_bpm} BPM")
    if recording.musical_key:
        info_lines.append(f"[cyan]Key:[/cyan] {recording.musical_key}")
    if recording.musical_mode:
        info_lines.append(f"[cyan]Mode:[/cyan] {recording.musical_mode}")
    if recording.key_confidence is not None:
        info_lines.append(f"[cyan]Key Confidence:[/cyan] {recording.key_confidence:.2f}")
    if recording.loudness_db is not None:
        info_lines.append(f"[cyan]Loudness:[/cyan] {recording.loudness_db:.1f} dB")
    if recording.embeddings_shape:
        info_lines.append(f"[cyan]Embeddings:[/cyan] {recording.embeddings_shape}")
```

#### C4. Update `audio list` command to support `--status partial`

**File**: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

In `list_recordings()` command (line 1161), update the `--status` help text:

```python
status: Optional[str] = typer.Option(
    None,
    "--status",
    help="Filter by analysis status (pending|processing|completed|failed|partial|incomplete)",
),
```

Add `partial` colorization in `_colorize_status()`:

```python
elif status == "partial":
    return f"[yellow]{status}[/yellow]"
```

### Phase D: Algorithmic Speedups (Fast Tier)

These are baked into the `analyze_audio_fast()` implementation (Phase A2):

| Optimization | Value | Impact |
|-------------|-------|--------|
| Sample rate | 22050 Hz (down from 44100) | ~2x faster chroma/beat tracking |
| Hop length | 4096 (up from 512) | ~8x fewer chroma frames |
| No allin1 | Skip entirely | Saves ~30-50s/song |
| No stem separation | Skip demucs entirely | Saves ~20-40s/song |
| No embeddings | Skip entirely | Saves model inference time |
| Full song duration | No truncation | Maintains accuracy per user preference |

**Expected throughput**: ~10-15s/song on CPU → **~100-150 songs/hour** → hundreds of songs overnight.

**CPU oversubscription warning**: `SOW_FAST_ANALYZE_MAX_CONCURRENT=4` on a 4-core box means each job contends for CPU. Actual per-job latency increases ~linearly with concurrency, so throughput may plateau. Recommend tuning to `max(1, os.cpu_count() // 2)` for single-CPU boxes and monitoring actual wall-clock time.

### Phase E: Reconciliation & Status Sync

#### E1. Update `--reconcile` to handle `partial` status

**File**: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

In the `status --reconcile` command (line 2547), the reconciliation logic scans R2 for `analysis.json` files. When a recording has `analysis_status` in `('pending', 'processing', 'failed')` but an `analysis.json` exists in R2, it reconciles.

Update to also reconcile `partial` recordings — if the R2 `analysis.json` contains beats/sections, upgrade to `completed`; otherwise set to `partial`:

```python
if rec.analysis_status in ("pending", "processing", "failed", "partial"):
    try:
        analysis_url = r2_client.analysis_exists(rec.hash_prefix)
        if analysis_url:
            analysis_data = r2_client.download_analysis_json(rec.hash_prefix)
            # Determine tier based on presence of beats/sections
            has_full_data = "beats" in analysis_data and analysis_data.get("beats") is not None
            tier_status = "completed" if has_full_data else "partial"
            db_client.update_recording_analysis(
                hash_prefix=rec.hash_prefix,
                ...,
                analysis_status=tier_status,
            )
```

## File Change Summary

| File | Change |
|------|--------|
| `ops/analysis-service/src/sow_analysis/models.py` | Add `FAST_ANALYZE` job type, `FastAnalyzeOptions`, `FastAnalyzeJobRequest`; update `Job.request` union |
| `ops/analysis-service/src/sow_analysis/workers/analyzer.py` | Add `detect_key_fast()`, `estimate_tempo()`, `analyze_audio_fast()` with `force` support |
| `ops/analysis-service/src/sow_analysis/storage/cache.py` | Add `get_fast_analysis_result()`, `save_fast_analysis_result()` |
| `ops/analysis-service/src/sow_analysis/routes/jobs.py` | Add `POST /jobs/fast-analyze` endpoint |
| `ops/analysis-service/src/sow_analysis/workers/queue.py` | Add `_fast_analyze_semaphore`, `_process_fast_analyze_job()`, wire into `_process_job_with_semaphore()`; verify stuck-job recovery is type-agnostic |
| `ops/analysis-service/src/sow_analysis/config.py` | Add `SOW_FAST_ANALYZE_MAX_CONCURRENT` setting |
| `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py` | Add `submit_fast_analysis()` method |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | Refactor `analyze` command with `--stdin`, `--tier`, `--all-pending`, `--wait`, `--limit`, `--manifest`, `--resume-from`, `--state-file`, `--dry-run`; add `_submit_analysis_single()`, `_submit_analysis_batch()`, `_wait_for_batch_completion()`, `_store_analysis_result()`, `_load_batch_manifest()`, `_write_batch_state()`; update `show` and `list` for `partial` status; update `--reconcile` |
| `ops/admin-cli/src/stream_of_worship/admin/db/client.py` | Add `analysis_status` parameter to `update_recording_analysis()`; add `partial` branch in `list_recordings()` |
| `ops/admin-cli/src/stream_of_worship/admin/db/models.py` | Update `has_analysis` to include `partial`; add `has_full_analysis` property |

## Request Flow

### Single Song (Fast Tier)

```
sow-admin audio analyze <song_id> --tier fast --wait
  │
  ├─ DB lookup: recording (r2_audio_url, content_hash)
  ├─ Check: analysis_status not in ('completed', 'partial') unless --force
  │
  ├─ AnalysisClient.submit_fast_analysis()
  │   └─ POST /api/v1/jobs/fast-analyze
  │       └─ JobQueue.submit(FAST_ANALYZE, request)
  │           └─ Job(QUEUED) → asyncio.Queue
  │
  ├─ DB: analysis_status='processing', analysis_job_id=job_id
  │
  ├─ Poll: GET /api/v1/jobs/{job_id} (every 5s)
  │
  └─ On completion:
      └─ _process_fast_analyze_job()
          ├─ Download audio from R2
          ├─ analyze_audio_fast()
          │   ├─ Check cache (skip if force)
          │   ├─ librosa.load(sr=22050)
          │   ├─ librosa.beat.beat_track() → tempo_bpm
          │   ├─ detect_key_fast(hop_length=4096) → key, mode, confidence
          │   └─ compute_loudness() → loudness_db
          │   └─ Save to {hash}_fast.json cache
          ├─ NO R2 upload (avoids clobbering full-tier analysis.json)
          └─ JobResult(duration, tempo, key, mode, confidence, loudness)
      └─ _store_analysis_result()
          └─ db_client.update_recording_analysis(analysis_status='partial')
```

### Batch (Fast Tier, Overnight)

```
sow-admin audio analyze --all-pending --tier fast --wait --limit 200
  │
  ├─ DB: list_recordings(status='incomplete') → song_ids (ordered by imported_at)
  ├─ Apply --limit 200
  │
  ├─ For each song_id:
  │   ├─ AnalysisClient.submit_fast_analysis()
  │   ├─ DB: analysis_status='processing'
  │   └─ Collect job_ids → manifest JSON
  │
  └─ _wait_for_batch_completion()
      ├─ Poll all jobs individually every 5s
      ├─ On each completion: _store_analysis_result() → status='partial'
      ├─ Write incremental state file after each poll round
      └─ Progress bar + summary table
```

### Resume Interrupted Batch

```
sow-admin audio analyze --resume-from /tmp/sow_analyze_batch_20240101_120000.json --wait
  │
  ├─ Load manifest: tier, force, no_stems, job list
  ├─ Filter to jobs with status in ('submitted', 'processing')
  │
  └─ _wait_for_batch_completion()
      ├─ Re-poll remaining jobs
      ├─ Skip already completed/failed entries
      └─ Continue writing state file
```

### Upgrade Partial → Full

```
sow-admin audio analyze --all-pending --tier full --wait
  │
  ├─ DB: list_recordings(status='incomplete') + list_recordings(status='partial')
  │
  └─ For each: submit_analysis() (existing allin1 pipeline)
      └─ On completion: update_recording_analysis(analysis_status='completed')
          └─ Upload full analysis.json to R2 (overwrites any previous full-tier result)
```

## Testing Plan

### Analysis Service Tests

**File**: `ops/analysis-service/tests/test_fast_analyze.py`

- Test `FastAnalyzeJobRequest` model validation
- Test `POST /api/v1/jobs/fast-analyze` endpoint (auth, validation)
- Test `_process_fast_analyze_job()` with mocked `analyze_audio_fast`
- Test `analyze_audio_fast()` with a short test audio file:
  - Verify returns `duration_seconds`, `tempo_bpm`, `musical_key`, `musical_mode`, `key_confidence`, `loudness_db`
  - Verify `beats`, `downbeats`, `sections`, `embeddings_shape` are NOT in result
- Test cache hit path (`get_fast_analysis_result` returns cached)
- Test cache miss path (saves to `_fast.json`)
- Test `force=True` bypasses cache
- Test concurrency: multiple fast analyze jobs run in parallel (up to `SOW_FAST_ANALYZE_MAX_CONCURRENT`)
- Test error handling: audio download failure, librosa error
- Test stuck-job recovery: simulate service restart with `PROCESSING` fast_analyze job, verify it is requeued on startup

### Admin CLI Tests

- Test `analyze --tier fast <song_id>` submits to `/jobs/fast-analyze`
- Test `analyze --tier full <song_id>` submits to `/jobs/analyze` (existing behavior)
- Test `analyze --stdin --tier fast` batch submission
- Test `analyze --all-pending --tier fast` queries incomplete recordings
- Test `analyze --all-pending --tier full` queries incomplete + partial recordings
- Test `analyze --all-pending --limit 10` caps batch size
- Test `analyze --dry-run` prints without submitting
- Test `--wait` in batch mode polls all jobs and writes results
- Test manifest is written after batch submission
- Test `--resume-from` loads manifest and continues polling
- Test state file is written incrementally during `--wait`
- Test `_store_analysis_result()` sets `partial` for fast tier, `completed` for full tier
- Test skip logic: `partial` recordings skipped with `--tier fast`, included with `--tier full`
- Test `audio show` displays `partial` status correctly and shows key/BPM
- Test `audio list --status partial` filters correctly
- Test `has_analysis` property returns `True` for `partial` status

## Operational Runbook

### Tuning Concurrency

If fast-tier jobs are slower than expected (~15s each), check CPU saturation:

```bash
# Monitor CPU while batch runs
watch -n 1 'ps aux | grep python | grep analysis-service'

# Tune down if oversubscribed
SOW_FAST_ANALYZE_MAX_CONCURRENT=2 docker compose up -d
```

### Recovering from CLI Crash

```bash
# Find latest manifest
ls -lt /tmp/sow_analyze_batch_*.json

# Resume
sow-admin audio analyze --resume-from /tmp/sow_analyze_batch_20240101_120000.json --wait
```

### Handling Stuck `processing` Jobs

If the analysis service crashes and leaves jobs in `processing`:

1. Restart the service — it automatically requeues interrupted jobs on startup.
2. If DB still shows `processing` but service has no record, use `--reconcile`:
   ```bash
   sow-admin audio status --reconcile
   ```

### Overnight Batch Best Practices

```bash
# Use tmux/screen for long-running batches
tmux new -s analyze_batch
sow-admin audio analyze --all-pending --tier fast --wait --limit 500 \
  --manifest /tmp/overnight_batch.json \
  --state-file /tmp/overnight_batch.state.jsonl

# Detach: Ctrl-B D
# Reattach: tmux attach -t analyze_batch
```

### Fast Tier → Full Tier Upgrade

After fast tier completes on all songs, upgrade partial results:

```bash
# This will process both incomplete and partial recordings
sow-admin audio analyze --all-pending --tier full --wait
```

## Open Questions

None — all resolved via user Q&A.
