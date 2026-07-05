# Fix Tempo Detection Quantization (hop_length + start_bpm)

## Overview

The songset constructor fails to build any 5-song songset from a 99-song catalog because **every song has one of only two tempo values** (107.7 or 161.5 BPM), making H3 (closing tempo ≤ 90) unsatisfiable. The root cause is `librosa.beat.tempo` called with `hop_length=4096` in the fast analysis path, which produces a frame rate so low (5.38 Hz) that the autocorrelation can only distinguish between two integer lags. A secondary cause is the default `start_bpm=120` prior, which biases toward double-time interpretations of worship songs whose true tempos are 65-95 BPM.

This plan fixes both parameters in the analysis service, adds a `start_bpm` option through the full fast-analyze pipeline, and re-analyzes all existing recordings.

| | |
|---|---|
| **Date** | 2026-07-05 |
| **Status** | Plan — pending implementation |
| **Components** | `ops/analysis-service/`, `ops/admin-cli/` |
| **Breaking** | Changes default `hop_length` from 4096 → 512 and adds `start_bpm=80` default; existing cached fast-analysis results will be invalidated |
| **Re-analysis required** | Yes — all 99 existing recordings need re-analysis via `sow-admin audio batch --analyze --analysis-tier fast --force` |

---

## 1. Analysis — Root Cause Investigation

### 1.1 Symptom

```
start beam_seed_candidates
stop beam_seed_candidates in 0.24s candidates=0
```

The beam search produced zero candidates. Diagnostics reported `valid_closers_h3 = 0`: no song in the pool satisfies H3 (phase 4/5 with tempo ≤ 90 BPM).

### 1.2 Pool Tempo Distribution

A diagnostic script (`lab/poc-scripts/diagnose_closers.py`) loaded the 99-song catalog and printed the tempo distribution:

| Tempo (BPM) | Count |
|---|---|
| 107.7 | 70 |
| 161.5 | 28 |
| 69.0 | 1 |

Only **3 distinct values** across 99 songs. The two dominant values are suspiciously precise: `107.666016` and `161.49902`.

### 1.3 Root Cause: hop_length=4096

The fast analysis path (`ops/analysis-service/src/sow_analysis/workers/analyzer.py:402-407`) estimates tempo via:

```python
onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
tempo = librosa.beat.tempo(onset_envelope=onset_env, sr=sr, hop_length=hop_length)
```

With `hop_length=4096` and `sr=22050`, the frame rate is `22050 / 4096 ≈ 5.38 Hz`. The autocorrelation-based tempo estimator produces tempos at integer lags:

| Lag (frames) | Tempo (BPM) | Formula |
|---|---|---|
| 2 | 161.5 | `60 × 5.38 / 2` |
| 3 | 107.7 | `60 × 5.38 / 3` |
| 4 | 80.7 | `60 × 5.38 / 4` (never selected — prior biases away) |

There is no lag that produces any tempo between 107.7 and 161.5. The estimator is quantized to two values.

### 1.4 Secondary Cause: start_bpm=120 Default

Even with `hop_length=512` (frame rate 43.07 Hz, 5 unique values), all tempos land in the 117-152 BPM range — still no valid closers ≤ 90 BPM. This is because `librosa.beat.tempo` defaults to `start_bpm=120`, which centers a log-normal prior at 120 BPM. When a song's onset envelope has energy at both 130 BPM (double-time) and 65 BPM (true tempo), the prior pulls the estimate toward 130.

### 1.5 Beam Search Cascade Failure

Even with `relax_h3_bpm=160` (auto-relax escalation), the beam search dies at position 5:

```
position 4: beams_in=8 expanded=168  top beam all at 161.5 BPM
position 5: beams_in=8 expanded=0   -> BEAM DIED
```

The beam sort key (`beam.py:109-115`) prefers zero tempo delta, so all 8 surviving beams at position 4 are locked at 161.5 BPM. At position 5, the remaining phase 4/5 songs at 107.7 BPM can't be reached (delta=53.8 > h4_limit of 25), and the few remaining 161.5 BPM phase 4/5 songs are either already used or fail CFD constraints.

### 1.6 Strategy Comparison (20-song sample)

A test script (`lab/poc-scripts/test_tempo_strategies.py`) downloaded 20 songs from R2 and compared 9 tempo estimation strategies:

| Strategy | Unique | Range | In 65-140 | Verdict |
|---|---|---|---|---|
| A (current, hop=4096) | 2 | 107.7-161.5 | 11/20 | **Broken** — binary quantization |
| B (hop=512, defaults) | 5 | 117.5-152.0 | 15/20 | Better but still all double-time |
| **C (hop=512, start_bpm=80)** | **10** | **66.3-92.3** | **20/20** | **Best** — realistic worship tempos |
| D (hop=512, max_tempo=160) | 5 | 117.5-152.0 | 15/20 | No improvement over B |
| E (C + max_tempo=160) | 10 | 66.3-92.3 | 20/20 | Identical to C — max_tempo redundant |
| F (uniform prior 60-180) | 7 | 71.8-161.5 | 13/20 | Still some double-time |
| G (lognorm centered 90) | 8 | 51.7-71.8 | 12/20 | Over-corrects to too-slow |
| H (beat_track) | 5 | 117.5-152.0 | 15/20 | Same as B — same double-time bias |
| I (tempogram multi-peak) | 1 | inf | 0/20 | Bug — tempo_frequencies returns inf |

**Strategy C** (`hop_length=512, start_bpm=80`) is the clear winner:
- 10 unique tempo values (vs 2)
- All in the 66.3-92.3 BPM range (realistic for worship music)
- 20/20 songs in the worship-appropriate 65-140 range
- The `start_bpm=80` parameter shifts the log-normal prior center from 120 to 80, causing the autocorrelation to prefer the half-time peak

---

## 2. Design Decisions

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | Default `hop_length` | Change from 4096 to 512 | 43.07 Hz frame rate gives 5+ unique tempos vs 2; matches librosa default |
| D2 | Default `start_bpm` | Add `start_bpm=80` as new default | Shifts log-normal prior to worship-music range; recovers true tempos from double-time octave errors |
| D3 | `start_bpm` as API parameter | New optional field on `FastAnalyzeOptions` | Allows callers to override for non-worship audio; defaults to 80 |
| D4 | `max_tempo` parameter | Not added | Test showed no effect when `start_bpm=80` is set; keep surface minimal |
| D5 | Cache invalidation | `force=True` on re-analysis | Existing `{hash_prefix}_fast.json` cache files contain wrong tempos; must bypass cache |
| D6 | Full-tier (allin1) path | No change | Full analysis uses `allin1.analyze()` for BPM, not librosa; unaffected |
| D7 | DB migration | None needed | `tempo_bpm` column already exists; re-analysis writes new values via `update_recording_analysis()` |
| D8 | Admin CLI `submit_fast_analysis` | Add `start_bpm` parameter | Passes through to API; defaults to 80 |
| D9 | Batch re-analysis command | `sow-admin audio batch --analyze --analysis-tier fast --force` | Existing batch command already supports `--force`; no new command needed |

---

## 3. Implementation Plan

### Phase A: Analysis Service — Fix Tempo Parameters

#### A1. Add `start_bpm` to `FastAnalyzeOptions`

**File**: `ops/analysis-service/src/sow_analysis/models.py`

```python
class FastAnalyzeOptions(BaseModel):
    """Options for fast analysis jobs (librosa-only, no allin1/stems)."""

    force: bool = False
    sample_rate: int = 22050
    hop_length: int = 512  # CHANGED: 4096 → 512
    start_bpm: float = 80.0  # NEW: worship-music tempo prior center
```

#### A2. Update `analyze_audio_fast` signature and tempo call

**File**: `ops/analysis-service/src/sow_analysis/workers/analyzer.py`

Change the function signature (line 347-353):

```python
async def analyze_audio_fast(
    audio_path: Path,
    cache_manager: CacheManager,
    content_hash: str,
    sample_rate: int = 22050,
    hop_length: int = 512,  # CHANGED: 4096 → 512
    start_bpm: float = 80.0,  # NEW
    force: bool = False,
) -> dict:
```

Change the tempo estimation (line 402-407):

```python
def _compute_tempo() -> float:
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    tempo = librosa.beat.tempo(
        onset_envelope=onset_env,
        sr=sr,
        hop_length=hop_length,
        start_bpm=start_bpm,  # NEW
    )
    if hasattr(tempo, "__iter__"):
        tempo = float(tempo[0])
    return float(tempo)
```

Update the docstring (line 370) to document `start_bpm`:

```
start_bpm: Initial tempo guess for the log-normal prior (default 80).
    Worship music typically has tempos 65-95 BPM; the librosa default
    of 120 biases toward double-time octave errors.
```

#### A3. Pass `start_bpm` through the queue

**File**: `ops/analysis-service/src/sow_analysis/workers/queue.py`

In `_process_fast_analyze_job` (line 707-714), add `start_bpm`:

```python
analysis_result = await analyze_audio_fast(
    audio_path,
    self.cache_manager,
    request.content_hash,
    sample_rate=request.options.sample_rate,
    hop_length=request.options.hop_length,
    start_bpm=request.options.start_bpm,  # NEW
    force=request.options.force,
)
```

### Phase B: Admin CLI — Pass `start_bpm` Through

#### B1. Add `start_bpm` to `submit_fast_analysis`

**File**: `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`

Update `submit_fast_analysis` (line 265-300):

```python
def submit_fast_analysis(
    self,
    audio_url: str,
    content_hash: str,
    force: bool = False,
    sample_rate: int = 22050,
    hop_length: int = 512,  # CHANGED: 4096 → 512
    start_bpm: float = 80.0,  # NEW
) -> JobInfo:
```

Add `start_bpm` to the payload:

```python
payload = {
    "audio_url": audio_url,
    "content_hash": content_hash,
    "options": {
        "force": force,
        "sample_rate": sample_rate,
        "hop_length": hop_length,
        "start_bpm": start_bpm,  # NEW
    },
}
```

#### B2. Update batch command (no signature change needed)

**File**: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

The batch command (line 5275) calls `submit_fast_analysis` without `hop_length` or `start_bpm`, so it will pick up the new defaults automatically. No change needed.

The single-song `analyze` command (line 1574) calls `submit_analysis` (full tier), not `submit_fast_analysis`, so it is unaffected.

### Phase C: Re-analyze Existing Recordings

#### C1. Re-run fast analysis for all recordings

After deploying the code changes, re-analyze all 99 recordings:

```bash
# Ensure analysis service is running with the new code
cd ops/analysis-service && docker compose up -d

# Re-analyze all recordings with --force to bypass stale cache
uv run --project ops/admin-cli --extra admin sow-admin audio batch \
    --analyze --analysis-tier fast --force --limit 200
```

This will:
- Submit 99 fast-analyze jobs with `hop_length=512` and `start_bpm=80`
- Each job downloads audio from R2, runs librosa with the new params, and writes the corrected `tempo_bpm` to the DB
- The `--force` flag bypasses the `{hash_prefix}_fast.json` cache files that contain the old wrong tempos

#### C2. Verify the fix

After re-analysis, run the songset constructor:

```bash
uv run --project lab/poc-scripts --extra songset_constructor \
    python lab/poc-scripts/construct_songset_agent.py --env-file /opt/sow/.env
```

The beam search should now produce candidates because:
- Phase 4/5 songs will have tempos in the 66-92 BPM range (≤ 90 BPM, satisfying H3)
- The tempo diversity (10+ unique values) allows H4 (adjacent delta ≤ 20) to be satisfied

### Phase D: Tests

#### D1. Unit test for tempo estimation parameters

**File**: `ops/analysis-service/tests/test_analyzer.py` (new file)

Test that `analyze_audio_fast` passes `hop_length` and `start_bpm` to `librosa.beat.tempo` by mocking the librosa calls:

```python
class TestAnalyzeAudioFastTempoParams:
    """Tests that tempo estimation uses correct hop_length and start_bpm."""

    @patch("sow_analysis.workers.analyzer.librosa")
    @pytest.mark.asyncio
    async def test_default_params_are_hop512_start80(self, mock_librosa, tmp_path):
        """Verify default hop_length=512 and start_bpm=80 are passed through."""
        # Setup mocks for librosa.load, onset_strength, beat.tempo, etc.
        mock_librosa.beat.tempo.return_value = np.array([80.0])
        # ... call analyze_audio_fast and assert mock called with start_bpm=80, hop_length=512

    @pytest.mark.asyncio
    async def test_custom_start_bpm_passed_through(self, ...):
        """Verify custom start_bpm overrides the default."""
```

#### D2. Integration test for FastAnalyzeOptions model

**File**: `ops/analysis-service/tests/integration/test_models.py`

```python
class TestFastAnalyzeOptions:
    def test_default_hop_length_is_512(self):
        opts = FastAnalyzeOptions()
        assert opts.hop_length == 512

    def test_default_start_bpm_is_80(self):
        opts = FastAnalyzeOptions()
        assert opts.start_bpm == 80.0

    def test_custom_start_bpm(self):
        opts = FastAnalyzeOptions(start_bpm=120.0)
        assert opts.start_bpm == 120.0
```

#### D3. Test for admin CLI submit_fast_analysis

**File**: `ops/admin-cli/tests/admin/test_analysis_client.py`

```python
class TestSubmitFastAnalysis:
    def test_payload_includes_start_bpm(self):
        """Verify start_bpm is included in the API payload."""
        # Mock requests.post, call submit_fast_analysis, assert payload contains start_bpm

    def test_default_start_bpm_is_80(self):
        """Verify default start_bpm=80 when not specified."""
```

---

## 4. Files Changed

| File | Change |
|---|---|
| `ops/analysis-service/src/sow_analysis/models.py` | `FastAnalyzeOptions.hop_length` default 4096→512; add `start_bpm: float = 80.0` |
| `ops/analysis-service/src/sow_analysis/workers/analyzer.py` | `analyze_audio_fast` signature: `hop_length` default 4096→512, add `start_bpm` param; pass `start_bpm` to `librosa.beat.tempo` |
| `ops/analysis-service/src/sow_analysis/workers/queue.py` | `_process_fast_analyze_job`: pass `start_bpm` from `request.options` to `analyze_audio_fast` |
| `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py` | `submit_fast_analysis`: `hop_length` default 4096→512, add `start_bpm: float = 80.0` param and payload field |
| `ops/analysis-service/tests/test_analyzer.py` | New: unit tests for tempo param passthrough |
| `ops/analysis-service/tests/integration/test_models.py` | New: tests for `FastAnalyzeOptions` defaults |
| `ops/admin-cli/tests/admin/test_analysis_client.py` | New: test for `start_bpm` in payload |

---

## 5. Out of Scope

- **Full-tier (allin1) analysis**: Uses `allin1.analyze()` for BPM, not librosa. Unaffected by this fix.
- **Tempogram multi-peak analysis (Strategy I)**: Failed due to `librosa.tempo_frequencies` returning `inf`. Could be a future enhancement but not needed — Strategy C works well.
- **Beam search sort key improvement**: The beam's preference for zero tempo delta causes "tempo ghetto" effects. This is mitigated by the tempo fix (diverse tempos mean fewer zero-delta beams), but a future improvement could add tempo-diversity-aware beam pruning.
- **H3 rule relaxation**: The existing `--relax-h3-bpm` flag and auto-relax escalation remain as fallbacks, but should rarely be needed after the tempo fix.
- **Diagnostic scripts cleanup**: `lab/poc-scripts/diagnose_closers.py` and `lab/poc-scripts/test_tempo_strategies.py` are investigation artifacts and can be kept or removed.

---

## 6. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Re-analysis changes tempos for songs used in existing songsets | Existing songset artifacts may reference stale tempos | Songset artifacts are read-only proposals; no production data depends on `tempo_bpm` being a specific value |
| `start_bpm=80` over-corrects for fast songs (>100 BPM true tempo) | Some fast songs might be detected at half their real tempo | Test showed 20/20 songs landed in 66-92 range, all realistic. If needed, `start_bpm` is now an API parameter that can be tuned per-call. |
| `hop_length=512` increases onset strength computation time | Slower analysis (~2-3x for onset envelope) | Onset strength at hop=512 on a 3-minute song takes ~0.5s vs ~0.1s at hop=4096. Negligible vs the 10-15s total fast analysis time. |
| Stale cache files bypass new params | Re-analysis returns old tempos | `--force` flag in the batch command bypasses cache; `FastAnalyzeOptions.force` defaults to `False` but the batch command passes `--force` |
