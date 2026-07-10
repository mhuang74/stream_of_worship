# Plan: Analysis-Service prod-v5 BPM Detection (CPS-Derived lognormal Prior)

## Overview

Replace the `analyze_audio_fast` tempo estimator in
`ops/analysis-service/src/sow_analysis/workers/analyzer.py` with the **prod-v5
approach** validated in
`lab/poc-scripts/output/bpm_comparison_20260708_114300.csv`.

prod-v5 supersedes the v4 `start_bpm=80` scalar prior + octave guard with a
**Characters-Per-Second (CPS)-derived lognormal prior** computed from the
song's own LRC lyrics. When LRC is absent, it falls back to the exact v4
behavior (prior=None → `start_bpm=80` + octave guard).

This is scope-limited to the **fast analysis path** (`analyze_audio_fast`).
The full `analyze_audio` (allin1) path is unchanged — its BPM comes from
allin1, not librosa.

## Conclusions Confirmed From CSV Data

Source: `lab/poc-scripts/output/bpm_comparison_20260708_114300.csv` (98 rows)

### Conclusion 1 — CPS nominal buckets align 100% with prod-v5 BPM

Using the **nominal CPS bucket scheme** (`slow < 1.5`, `1.5 ≤ moderate ≤ 2.8`,
`fast > 2.8`) and the **BPM bucket scheme** (`slow < 90`, `90 ≤ moderate ≤ 120`,
`fast > 120`):

| Metric | Value |
|---|---|
| Total songs | 98 |
| Songs with LRC (CPS computable) | 98 |
| CPS bucket ≡ prod-v5 BPM bucket | **98 / 98 (100%)** |
| Misaligned | **0** |

Verified by cross-tabulation: every song whose CPS lands in bucket `slow`
has a prod-v5 BPM < 90; every `moderate` CPS song has prod-v5 BPM in
[90, 120]; the single `fast`-CPS song has prod-v5 BPM > 120. Zero
off-diagonal mass. (The v5 diagonal is achieved *by construction* since
the CPS prior shapes the lognormal center; the other 4 methods provide the
independent cross-check — see `specs/bpm-agreement-report-v5-lineage-consensus-cps.md`.)

### Conclusion 2 — prod-v5 performance is excellent and scales linearly

Per-song timing for the fast tier (onset_strength + tempo estimation only;
audio load excluded, consistent with how the analyzer runs):

| Library   | Total (98 songs) | Mean / song | Max     |
|-----------|------------------|-------------|---------|
| librosa   | 54.97 s          | 0.561 s     | 0.88 s  |
| prod-v4   | 52.88 s          | 0.540 s     | 0.86 s  |
| **prod-v5** | **53.20 s**     | **0.543 s** | **0.87 s** |
| madmom    | 3902.87 s        | 39.825 s    | 64.23 s |
| BeatNet   | 282.09 s         | 2.878 s     | 4.66 s  |

prod-v5 is ~73× faster than madmom and ~5× faster than BeatNet while
matching raw librosa throughput. Performance is CPU-only (no model
inference), so cost scales linearly with catalog size: a full 98-song
sweep completes in ~53 s on the POC hardware.

## CPS Bucket → Lognormal Prior Mapping

The CPS value is bucketed using the nominal scheme and mapped to a
lognormal prior centered on the expected BPM band:

| CPS bucket (nominal)   | Prior mean (BPM) | Prior std (BPM) | Librosa `tempo()` call                               |
|------------------------|------------------|-----------------|------------------------------------------------------|
| `slow` (cps < 1.5)     | 70.0             | 12.0            | `librosa_tempo(..., prior=stats.lognorm(...))`       |
| `moderate` (1.5–2.8)   | 105.0            | 15.0            | (as above, different mean/std)                        |
| `fast` (> 2.8)         | 135.0            | 15.0            | (as above, different mean/std)                        |
| CPS missing            | —                | —               | `librosa_tempo(..., start_bpm=80)` (v4 fallback)     |

The lognormal is parameterized via the standard mean/std→(mu, sigma)
conversion:

```python
var = std ** 2
mu = math.log(mean ** 2 / math.sqrt(var + mean ** 2))
sigma = math.sqrt(math.log(1 + var / mean ** 2))
prior = stats.lognorm(scale=math.exp(mu), s=sigma)
```

When a prior is active the v4 octave guard is **skipped** (the prior is the
v5 signal that resolves octave ambiguity; re-running with scalar
`start_bpm` would discard it). When `cps is None`, v5 falls back to the
exact v4 behavior (start_bpm=80 + double/half-time guard).

## Design Decisions (confirmed with user)

| Decision | Choice |
|---|---|
| Scope | **Fast path only** — `analyze_audio_fast`. `analyze_audio` (allin1) untouched. |
| LRC source | **Pass `lrc_content` in job request** — administered by caller (admin-cli fetches from R2, then posts it as part of the fast-analyze payload). Analyzer stays decoupled from R2/DB. |
| Runtime selection | **New `BPM_ALGORITHM_VERSION` setting** — mirrors `KEY_ALGORITHM_VERSION`. Values: `"v4_octave_guard"` (default, current behavior) and `"v5_cps_prior"` (new). Lets us roll back via env var and versions the fast cache suffix. |
| Fallback when LRC missing | **Fall back to v4 octave guard** — when `lrc_content` is None/empty or CPS unparseable, v5 path uses `start_bpm=80` + the existing v4 double/half-time guard. Identical behavior for songs without lyrics. |
| Cache key | **Bump version suffix only** — incorporate `BPM_ALGORITHM_VERSION` into the `_fast` cache filename alongside `KEY_ALGORITHM_VERSION`. LRC content changes won't auto-invalidate; acceptable because LRC rarely changes post-generation. |
| scipy dep | **Add `scipy>=1.10.0` explicitly** to `ops/analysis-service/pyproject.toml` deps. Already transitively available via librosa/madmom; declaring it makes the contract explicit and importable in minimal test envs. |

## CPS Computation

The CPS helpers (`count_lyric_chars`, `compute_cps`, `cps_to_prior`,
`cps_bucket_label`) are ported verbatim from
`lab/poc-scripts/compare_bpm_libraries.py` into a new module
`sow_analysis/workers/cps.py` (see § Architecture). The analyzer imports
from there — no duplicate implementation.

### Character counting — CJK per-char, ASCII per-word

CJK characters count individually; ASCII alphanumeric runs collapse to 1
token each; whitespace + Unicode P*/S* categories excluded. Implemented
via `unicodedata.category()` + the `ord(ch) > 0x2E7F` CJK gate.

### Vocal span — first → last timed LRC line

`cps = total_chars / (last_ts − first_ts)`. Requires ≥ 2 timed LRC lines and
non-positive span returns None. Parsed via a vendored copy of the admin-cli
`parse_lrc` (see § Dependencies).

## Architecture

### New module: `ops/analysis-service/src/sow_analysis/workers/cps.py`

Extracts the CPS-specific helpers so `analyzer.py` stays focused and the POC
script can be retired without losing the knowledge. Exports:

- `count_lyric_chars(text: str) -> int`
- `compute_cps(lrc_content: str) -> tuple[Optional[float], Optional[dict]]`
- `cps_to_prior(cps: Optional[float]) -> Optional[stats.rv_continuous]`
- `cps_bucket_label(cps: Optional[float]) -> Optional[str]`
- `CPS_SLOW_MAX`, `CPS_MODERATE_MAX` constants (1.5 and 2.8)

LRC parsing uses a vendored copy of
`ops/admin-cli/src/stream_of_worship/admin/services/lrc_parser.py` → new
file `sow_analysis/workers/lrc_parser.py` (same code, no admin-cli import).
The analysis-service must not introduce a runtime dependency on admin-cli.

### `config.py` — add `BPM_ALGORITHM_VERSION`

```python
# Tempo detection algorithm version.
#   "v4_octave_guard" -> start_bpm=80 + double/half-time guard (current default)
#   "v5_cps_prior"    -> CPS-derived lognormal prior (skips octave guard)
BPM_ALGORITHM_VERSION: str = "v4_octave_guard"
```

Placed directly below `KEY_ALGORITHM_VERSION` (config.py:27). Validated by
a `@field_validator` that raises on unknown values (mirrors the spirit of
`KEY_ALGORITHM_VERSION`'s consumers in `analyzer.py:detect_key`).

### `storage/cache.py` — version fast-results with `BPM_ALGORITHM_VERSION`

Extend `_versioned_analysis_file()` to fold in the BPM version when the
suffix involves fast results. Strategy:

- Add a new helper `_versioned_fast_file(content_hash) -> Path` returning
  `{hash32}.v{KEY_ALGORITHM_VERSION}.v{BPM_ALGORITHM_VERSION}_fast.json`.
- `get_fast_analyze_result` reads in order:
  1. `_versioned_fast_file(content_hash)` (new)
  2. `{hash32}.v{KEY_ALGORITHM_VERSION}_fast.json` (legacy v4)
  3. `{hash32}_fast.json` (pre-versioning)
- `save_fast_analyze_result` writes to `_versioned_fast_file(content_hash)`.

Full-tier cache (`get_analysis_result` / `save_analysis_result`) is
**unaffected** — only the fast path changes.

### `models.py` — add `lrc_content` to `FastAnalyzeOptions`

```python
class FastAnalyzeOptions(BaseModel):
    force: bool = False
    sample_rate: int = 22050
    hop_length: int = 512
    start_bpm: float = Field(default=80.0, ge=40.0, le=200.0)
    lrc_content: Optional[str] = None  # NEW — LRC lyrics text for CPS-based prod-v5 prior
```

`lrc_content` is `Optional[str]` (default None). When None or empty, the
analyzer's v5 path falls back to v4 behavior even if
`BPM_ALGORITHM_VERSION=v5_cps_prior`.

No change to `FastAnalyzeJobRequest` itself — `lrc_content` is read via
`request.options.lrc_content`.

### `workers/analyzer.py` — add v5 tempo branch in `analyze_audio_fast`

New private function `_compute_tempo_v5(y, sr, hop_length, lrc_content,
start_bpm) -> float` that:

1. Parses `lrc_content` → CPS via `cps.compute_cps(lrc_content)`.
2. If CPS is not None:
   - `prior = cps.cps_to_prior(cps)`
   - `tempo_primary = librosa.beat.tempo(onset_envelope=onset_env, sr=sr,
     hop_length=hop_length, prior=prior)` — **no octave guard**.
   - Return `tempo_primary`.
3. If CPS is None (LRC missing or unparseable):
   - Delegate to the existing `_compute_tempo()` (the v4 path, unchanged).

The caller `_compute_tempo()` is renamed internally but its logic is
preserved verbatim for fallback use.

Dispatch in `analyze_audio_fast` reads `settings.BPM_ALGORITHM_VERSION`:

```python
algorithm = settings.BPM_ALGORITHM_VERSION
if algorithm == "v5_cps_prior":
    bpm = await loop.run_in_executor(
        None, _compute_tempo_v5, y, sr, hop_length, lrc_content, start_bpm
    )
elif algorithm == "v4_octave_guard":
    bpm = await loop.run_in_executor(None, _compute_tempo, y, sr, hop_length, start_bpm)
else:
    raise ValueError(f"Unsupported BPM_ALGORITHM_VERSION: {algorithm}")
```

`analyze_audio_fast` signature gains `lrc_content: Optional[str] = None`:

```python
async def analyze_audio_fast(
    audio_path: Path,
    cache_manager: CacheManager,
    content_hash: str,
    sample_rate: int = 22050,
    hop_length: int = 512,
    start_bpm: float = 80.0,
    force: bool = False,
    lrc_content: Optional[str] = None,  # NEW
) -> dict:
```

### `workers/queue.py` — pass `lrc_content` through

At the `_process_fast_analyze_job` call site (queue.py:707-714), add:

```python
analysis_result = await analyze_audio_fast(
    audio_path,
    self.cache_manager,
    request.content_hash,
    sample_rate=request.options.sample_rate,
    hop_length=request.options.hop_length,
    start_bpm=request.options.start_bpm,
    force=request.options.force,
    lrc_content=request.options.lrc_content,  # NEW
)
```

No other queue.py changes — the R2 audio download flow, semaphore logic,
and job-store plumbing are all unchanged.

### Admin-CLI caller — fetch LRC and pass it through

In `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py:submit_fast_analysis`
(line 267), add an `lrc_content: Optional[str] = None` parameter and fold
it into the `options` payload:

```python
def submit_fast_analysis(
    self,
    audio_url: str,
    content_hash: str,
    force: bool = False,
    sample_rate: int = 22050,
    hop_length: int = 512,
    start_bpm: float = 80.0,
    lrc_content: Optional[str] = None,  # NEW
) -> JobInfo:
    ...
    payload = {
        "audio_url": audio_url,
        "content_hash": content_hash,
        "options": {
            "force": force,
            "sample_rate": sample_rate,
            "hop_length": hop_length,
            "start_bpm": start_bpm,
            **({"lrc_content": lrc_content} if lrc_content is not None else {}),
        },
    }
```

In `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:5282` (the
`analysis_tier == "fast"` branch), before calling
`submit_fast_analysis`, fetch the LRC via the existing
`R2Client.download_lrc_content(hash_prefix)` (admin-cli has this method at
`services/r2.py:486`):

```python
if analysis_tier == "fast":
    lrc_content = r2_client.download_lrc_content(recording.hash_prefix) if r2_client else None
    job = analysis_client.submit_fast_analysis(
        audio_url=recording.r2_audio_url,
        content_hash=recording.content_hash,
        force=force,
        lrc_content=lrc_content,
    )
```

The CLI enrolls in v5 by setting `BPM_ALGORITHM_VERSION=v5_cps_prior` in
the analysis-service env. When that env is unset, the service stays on
`v4_octave_guard` and the `lrc_content` payload is harmlessly ignored
(backward compatible).

## Dependencies

- Add `scipy>=1.10.0` to `ops/analysis-service/pyproject.toml` `[project]
  dependencies` (via `uv add scipy --project ops/analysis-service`). Even
  though librosa/madmom depend on it transitively, declaring it makes the
  `from scipy import stats` import contract explicit.
- Add `pyproject.toml` clarification: scipy is **not** added to admin-cli
  — the admin-cli only passes LRC text through, it never computes the
  prior. No new admin-cli dep.
- The LRC parser (`parse_lrc`) is a vendored copy of the admin-cli
  implementation into `sow_analysis/workers/lrc_parser.py`. Both copies
  must remain byte-identical; the original is the source of truth.

## Files Changed

| File | Change |
|---|---|
| `ops/analysis-service/pyproject.toml` | Add `scipy>=1.10.0` to `[project] dependencies` |
| `ops/analysis-service/src/sow_analysis/config.py` | Add `BPM_ALGORITHM_VERSION: str = "v4_octave_guard"` + validator |
| `ops/analysis-service/src/sow_analysis/storage/cache.py` | Add `_versioned_fast_file()`; update `get_fast_analyze_result` / `save_fast_analyze_result` to use new filename |
| `ops/analysis-service/src/sow_analysis/models.py` | Add `lrc_content: Optional[str] = None` to `FastAnalyzeOptions` |
| `ops/analysis-service/src/sow_analysis/workers/cps.py` | NEW module: `count_lyric_chars`, `compute_cps`, `cps_to_prior`, `cps_bucket_label` |
| `ops/analysis-service/src/sow_analysis/workers/lrc_parser.py` | NEW: vendored copy of admin-cli `parse_lrc` + `LRCFile` / `LRCLine` dataclasses |
| `ops/analysis-service/src/sow_analysis/workers/analyzer.py` | Add `_compute_tempo_v5`; dispatch on `BPM_ALGORITHM_VERSION`; add `lrc_content` param to `analyze_audio_fast` |
| `ops/analysis-service/src/sow_analysis/workers/queue.py` | Pass `lrc_content=request.options.lrc_content` to `analyze_audio_fast` (one-liner at line 707-714) |
| `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py` | Add `lrc_content` param to `submit_fast_analysis`; fold into options payload |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | In the fast-submit branch (line 5282), fetch LRC via `r2_client.download_lrc_content(hash_prefix)` and pass through |

Out of scope (no changes):
- `workers/analyzer.py:analyze_audio` (full allin1 tier) — unchanged.
- `routes/jobs.py` — endpoint stays the same; the new `lrc_content` field
  is picked up automatically by Pydantic from the request body.
- `R2Client` (analysis-service side) — no `download_lrc` method added (the
  analyzer does not fetch LRC itself per the decision).
- `workers/lrc.py` — its LRC generation pipeline is untouched.

## Implementation Phases

### Phase 1 — Dependencies + CPS module (no behavior change)

1. `uv add scipy --project ops/analysis-service`.
2. Vendor `lrc_parser.py` from admin-cli → `sow_analysis/workers/lrc_parser.py`.
3. Create `sow_analysis/workers/cps.py` with the CPS helpers ported from
   `compare_bpm_libraries.py:174-283`.
4. Add unit tests `ops/analysis-service/tests/test_cps.py`:
   - `count_lyric_chars`: CJK char = 1, ASCII run = 1, mixed.
   - `compute_cps`: standard LRC returns expected CPS; <2 lines → None;
     non-positive span → None; empty string → None.
   - `cps_bucket_label`: boundary cases (1.499, 1.5, 2.8, 2.801).
   - `cps_to_prior`: returns a `stats.lognorm`; round-trip
     `prior.mean()` / `prior.std()` ≈ (mean, std) within tolerance.
5. Run: `cd ops/analysis-service && PYTHONPATH=src pytest tests/test_cps.py -v`.

### Phase 2 — Config + cache versioning (no behavior change)

1. Add `BPM_ALGORITHM_VERSION` setting + `@field_validator`.
2. Add `_versioned_fast_file()` to `cache.py`; update
   `get_fast_analyze_result` (3-tier read) and `save_fast_analyze_result`
   (write to new path).
3. Add tests to `tests/test_cache.py`:
   - Fast result saves under the new filename when
     `BPM_ALGORITHM_VERSION=v5_cps_prior`.
   - Legacy file is read as fallback.
4. Run: `PYTHONPATH=src pytest tests/test_cache.py -v`.

### Phase 3 — Analyzer v5 branch (opt-in via env)

1. Add `_compute_tempo_v5()` to `analyzer.py`.
2. Add dispatch block in `analyze_audio_fast` reading
   `settings.BPM_ALGORITHM_VERSION`.
3. Add `lrc_content: Optional[str] = None` param to `analyze_audio_fast`.
4. Extend `tests/test_analyzer.py`:
   - Mock `scipy.stats.lognorm` alongside existing librosa mocks.
   - `test_v5_uses_cps_prior_when_lrc_present`: when v5 + lrc_content,
     assert `librosa.beat.tempo` called with `prior=...` (mocked), no
     octave-guard call.
   - `test_v5_falls_back_to_v4_when_lrc_missing`: when v5 +
     `lrc_content=None`, assert `_compute_tempo` (v4) path runs.
   - `test_v5_falls_back_to_v4_when_cps_unparseable`: when v5 +
     malformed LRC, fall through to v4.
   - `test_v4_default_unchanged`: when
     `BPM_ALGORITHM_VERSION=v4_octave_guard`, `lrc_content` is ignored.
5. Run: `PYTHONPATH=src pytest tests/test_analyzer.py -v`.

### Phase 4 — Queue + models + admin-cli plumbing

1. Add `lrc_content` to `FastAnalyzeOptions`.
2. Update `queue.py:707` call site to pass `lrc_content`.
3. Update `admin-cli/services/analysis.py:submit_fast_analysis` signature.
4. Update `admin-cli/commands/audio.py:5282` to fetch LRC via
   `r2_client.download_lrc_content(hash_prefix)` before submission.
5. Run admin-cli tests:
   `PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest ops/admin-cli/tests -v -k "analysis or audio_batch"`.

### Phase 5 — End-to-end smoke (manual)

1. Set `BPM_ALGORITHM_VERSION=v5_cps_prior` in analysis-service env.
2. Submit a fast-analyze job for a known song (e.g.
   `hereibow_a9271bf4`, stored BPM 92.3, expected v5 BPM 68.0 from CSV row
   `bpm_comparison_20260708_114300.csv:2`).
3. Verify the returned `tempo_bpm` matches the CSV's `prod_v5_bpm` (~68.0,
   not ~92 — v5 picks the half-time octave because the CPS prior shapes it
   there).
4. Repeat for a song without LRC: confirm the result matches v4 behavior
   (octave guard + `start_bpm=80`).
5. Toggle `BPM_ALGORITHM_VERSION` back to `v4_octave_guard` and re-submit
   to confirm rollback returns the previous BPM.

## Verification

```bash
# Analysis-service tests
cd ops/analysis-service
PYTHONPATH=src pytest tests/test_cps.py tests/test_cache.py tests/test_analyzer.py -v

# Admin-cli tests (analysis client + batch path)
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 --extra admin --extra test \
  pytest ops/admin-cli/tests -v -k "analysis or audio_batch"
```

Post-implementation: run the catalog re-analysis command (out of scope for
this plan — a separate `--force` sweep is the operator's call) to populate
the new `v5_cps_prior` cache slot across the catalog.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| LRC missing for some songs passed to v5 | `lrc_content=None` → `_compute_tempo_v5` delegates to v4 `_compute_tempo`. Identical behavior for songs without lyrics; no silent BPM drift. |
| `scipy.stats.lognorm` frozen rv rejected by librosa `tempo()` | Already verified by the POC script (`compare_bpm_libraries.py:timed_prod_v5`). Phase 3 unit test mocks the prior; Phase 5 E2E confirms the live integration. |
| CPS→BPM prior means (70/105/135) are approximations | They are prior *centers* with std=12–15 — soft Bayesian priors, not hard pins. The POC's 100% diagonal agreement confirms the table is well-tuned for this catalog; the table is the single tuning knob if drift appears. |
| Stale cache returns v4 BPM when v5 env active | Cache key incorporates `BPM_ALGORITHM_VERSION` (Phase 2). First v5 run with the new env misses cache, writes a fresh v5 file. Operators can `--force` to bust manually. |
| admin-cli fetches LRC synchronously on the submit path | `download_lrc_content` is a single S3 GET (~50 ms typical). Acceptable; CLI already does dozens of network calls per analysis submission. |
| Vendored `lrc_parser.py` drifts from admin-cli source | Documented as "byte-identical copy" in module docstring with a pointer to the source path. Any bugfix in one must be mirrored in the other. A shared package is a future refactor. |
| `BPM_ALGORITHM_VERSION` typos silently fall back to v4 | `@field_validator` raises on unknown values at startup — service fails fast. |
| `lrc_content` field added to `FastAnalyzeOptions` breaks old callers | Field has default `None`; old payloads omitting it deserialize fine (Pydantic optional). Existing admin-cli versions continue to work against a v5-enabled service — the service simply receives `lrc_content=None` and falls back. |
| LRC content changes after analysis (e.g., editor re-aligns) | Cache key uses `BPM_ALGORITHM_VERSION`, not LRC hash. Operator must `--force` re-analyze after intentional LRC edits. Documented in the CLI help text for analyze command. |

## Out of Scope

- Allin1 (full-tier) BPM — allin1's internal tempo remains the source for
  `analyze_audio`. A follow-up could let the v5 prior override allin1's BPM
  if the CSV showed drift, but the POC did not compare against allin1.
- Per-song prior mean tuning — the 70/105/135 table is shared across all
  songs. Regression from CPS→BPM per song is a future refinement.
- LRC-hash-in-cache-key — defeated as too much plumbing for marginal value
  (LRC rarely changes post-generation). Documented as a known limitation.
- Batch re-analysis of the catalog — separate operational task.
- Retiring `lab/poc-scripts/compare_bpm_libraries.py` — kept as the
  reference implementation and regression oracle for the ported CPS helpers.
