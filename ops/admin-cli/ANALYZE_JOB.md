# Analyze Job Guide

Guide for the `sow-admin audio analyze` command — the full-tier analysis job
that runs allin1 + librosa to detect BPM, key, beats, sections, embeddings,
and optionally stems.

For the broader admin CLI reference, see [README.md](./README.md). For general
write-back and status commands, see [USER_GUIDE.md](./USER_GUIDE.md).

## Overview

The analyze job (`JobType.ANALYZE`, value `"analyze"`) is the full-tier audio
analysis. It is distinct from the fast-tier job (`JobType.FAST_ANALYZE`,
value `"fast_analyze"`), which uses librosa only.

| Tier | Job type | Flag | Produces | `analysis_status` on success |
|------|----------|------|----------|-------------------------------|
| Fast | `fast_analyze` | `--analysis-tier fast` (default) | tempo, key, loudness | `partial` |
| Full | `analyze` | `--analysis-tier full` | tempo, key, loudness, beats, downbeats, sections, embeddings, stems | `completed` |

Both tiers populate `tempo_bpm`. The full tier additionally writes `beats`,
`downbeats`, `sections`, `embeddings_shape`, and `r2_stems_url`.

## Submitting

```bash
# Full tier — allin1 + optional stems, ~minutes
sow-admin audio analyze <song-id> --analysis-tier full

# Full tier without stem separation
sow-admin audio analyze <song-id> --analysis-tier full --no-stems

# Block until the job completes (30 min timeout)
sow-admin audio analyze <song-id> --analysis-tier full --wait

# Force re-analysis even if already completed
sow-admin audio analyze <song-id> --analysis-tier full --force
```

If `--wait` is omitted, the CLI exits immediately after submission. The job
keeps running server-side; you must trigger write-back separately (see
[USER_GUIDE.md](./USER_GUIDE.md)).

If `--wait` is used and the job does not finish within 30 minutes, the CLI
prints:

```
Timed out after 30 min. The job is still running server-side.
Poll with: sow-admin audio status <job-id>
```

The job continues running on the analysis service. Use `status --reconcile`
or `status --sync` to write back results once it has finished.

## Two-Phase Lifecycle

1. **Submission** — `sow-admin audio analyze <song-id> --analysis-tier full`
   submits a job to the analysis service and records `analysis_job_id` +
   `analysis_status='processing'` in the `recordings` table.
2. **Write-back** — the analysis result (BPM, key, loudness, beats, sections,
   embeddings, stems URL) is written back to the `recordings` table by the
   admin CLI, **not** by the analysis service. The service only persists the
   result to its own jobs table and uploads `analysis.json` to R2.

The analysis service never writes to the `recordings` table directly.

### Write-Back Triggers

Use these when you did not pass `--wait`, when `--wait` timed out, or when the
CLI was interrupted (Ctrl-C). All of them call
`db_client.update_recording_analysis(tempo_bpm=..., ...)` against the
`recordings` table.

| Situation | Recommended command |
|-----------|---------------------|
| `--wait` timed out, service still running | `status --sync` |
| `--wait` timed out, service restarted | `status --reconcile` |
| Did not use `--wait`, want to backfill now | `status --reconcile` |
| Want to resume blocking on one job | `audio analyze <song-id> --wait` |
| Recording stuck in `processing`, no R2 output yet | `status <job-id>` to inspect, then `--force-status failed` to unstick |
| Bulk backfill after service outage | `status --reconcile` |

See [USER_GUIDE.md](./USER_GUIDE.md) for full details on each trigger.

## How BPM Detection Works

The full-tier analyze job detects BPM through allin1, which combines a PyTorch
neural network with madmom for preprocessing and postprocessing. The chain is:

```
Audio file
  │
  ▼
1. madmom spectrogram preprocessing
   (FilteredSpectrogramProcessor, ShortTimeFourierTransformProcessor,
    FramedSignalProcessor — in allin1/spectrogram.py)
  │
  ▼
2. PyTorch neural network inference
   (produces beat/downbeat probability activations)
  │
  ▼
3. madmom DBNDownBeatTrackingProcessor
   (converts probability activations → beat & downbeat timestamps
    via a Dynamic Bayesian Network — in allin1/postprocessing/metrical.py)
  │
  ▼
4. allin1 estimate_tempo_from_beats()
   (derives BPM from inter-beat intervals — in
    allin1/postprocessing/tempo.py)
  │
  ▼
result.bpm  →  analysis_result["tempo_bpm"]
```

### Does allin1 use madmom for BPM?

**No.** allin1 does **not** use madmom for BPM/tempo estimation. The BPM is
computed by allin1's own `estimate_tempo_from_beats()` function, which simply
takes the inter-beat intervals (`np.diff(beats)`), converts to BPM
(`60 / interval`), rounds to integers, and picks the most common value via
`np.bincount`.

What allin1 **does** use madmom for:

1. **Spectrogram preprocessing** (`allin1/spectrogram.py`) — madmom's
   `FilteredSpectrogramProcessor`, `ShortTimeFourierTransformProcessor`,
   `FramedSignalProcessor`, and `SequentialProcessor` build the log-filtered
   spectrogram fed to the neural network.
2. **Downbeat tracking** (`allin1/postprocessing/metrical.py:3`) — madmom's
   `DBNDownBeatTrackingProcessor` converts the neural network's beat/downbeat
   probability activations into actual beat and downbeat timestamps using a
   Dynamic Bayesian Network (DBN).

So madmom is involved in steps 1 and 3 (spectrogram + beat timestamp
extraction), but the final BPM number in step 4 is allin1's own computation.

### The tempo.py source

From `allin1/postprocessing/tempo.py`:

```python
def estimate_tempo_from_beats(beats: List[float]):
    if len(beats) < 2:
        return None

    beats = np.array(beats)
    beat_interval = np.diff(beats)
    bpm = 60. / beat_interval
    bpm = bpm.round().astype(int)
    bincount = np.bincount(bpm)
    bpm_range = np.arange(len(bincount))
    bpm_strength = bincount / bincount.sum()
    bpm_cand = np.stack([bpm_range, bpm_strength], axis=-1)
    bpm_cand = bpm_cand[np.argsort(bpm_strength)[::-1]]
    bpm_cand = bpm_cand[bpm_cand[:, 1] > 0]

    bpm_est = bpm_cand[0, 0]
    bpm_est = int(bpm_est)

    return bpm_est
```

The BPM is the most frequent inter-beat interval, rounded to the nearest
integer. No tempo tracking, no harmonic/tempo octave correction — just a
histogram over beat intervals.

### Fast-tier BPM (for comparison)

The fast-tier job (`analyze_audio_fast()` in
`ops/analysis-service/src/sow_analysis/workers/analyzer.py:402`) uses librosa
directly:

```python
onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
tempo = librosa.beat.tempo(onset_envelope=onset_env, sr=sr, hop_length=hop_length)
```

This is librosa's built-in tempo estimation via onset strength envelope and
tempogram, which is a different algorithm from allin1's beat-interval
histogram approach.

### Octave Error Guards

Tempo estimation algorithms are susceptible to **octave errors** — where the
detected BPM is an integer ratio of the true BPM rather than the true BPM
itself. The two most common types are:

| Error type | Ratio | Example | Cause |
|------------|-------|---------|-------|
| 2:1 octave | ×2 or ×½ | True 120 → detected 60 or 240 | Every other beat detected, or beats subdivided |
| 3:2 octave | ×3/2 or ×2/3 | True 120 → detected 80 or 180 | Dotted-note rhythmic grouping |

#### Full tier (allin1)

allin1's `estimate_tempo_from_beats()` has **no octave error guards at all**.
It picks the most frequent inter-beat interval via `np.bincount` with no
post-hoc correction. The allin1 config constrains the neural network's beat
detection to `bpm_min=55`–`bpm_max=240`, but this range does not prevent
either 2:1 or 3:2 errors.

#### Fast tier (librosa)

librosa's `beat.tempo()` applies a **pseudo-log-normal prior** in log2 space
centered at `start_bpm=120` with `std_bpm=1.0`:

```python
logprior = -0.5 * ((np.log2(bpms) - np.log2(start_bpm)) / std_bpm) ** 2
```

This prior provides a natural **2:1 guard**: a 2:1 octave error shifts the
candidate by exactly 1.0 in log2 space (since `log2(2) = 1`), which the
Gaussian prior penalizes. The further the candidate is from 120 BPM in log2
space, the more it is suppressed. This makes extreme octave errors (e.g.,
true 120 → detected 60 or 240) less likely.

However, there is **no 3:2 guard**. A 3:2 error shifts the candidate by only
`log2(3/2) ≈ 0.585` in log2 space — less than one standard deviation of the
prior. The prior does not meaningfully suppress 3:2 candidates, so the fast
tier can still produce 3/2 or 2/3 tempo errors (e.g., true 120 → detected 80
or 180). There is no explicit 3:2 ratio check or correction in either
librosa's `beat.tempo()` or in the analysis service's `analyze_audio_fast()`.

#### Summary

| Tier | 2:1 guard | 3:2 guard |
|------|-----------|-----------|
| Full (allin1) | No | No |
| Fast (librosa) | Yes (log-normal prior in log2 space) | No |

If BPM accuracy is critical, verify detected BPM values manually or compare
fast-tier and full-tier results for the same recording — a 3:2 discrepancy
between tiers may indicate an octave error in one of them.

## How Key Detection Works

Both tiers use the same key detection algorithm, implemented in
`ops/analysis-service/src/sow_analysis/workers/analyzer.py`. The algorithm is
Krumhansl-Schmuckler key profile matching via librosa's `chroma_cqt`:

1. Compute chroma features (`librosa.feature.chroma_cqt`).
2. Average chroma across the track (or per-window for the segment-vote
   variant).
3. Correlate the 12-dimensional chroma vector against rotated major and
   minor key profiles (Krumhansl-Schmuckler).
4. Pick the key/mode with the highest correlation.

The algorithm version is controlled by `settings.KEY_ALGORITHM_VERSION`:

| Version | Function | Description |
|---------|----------|-------------|
| `ks_fulltrack_v1` | `detect_key_fulltrack()` | Single chroma average over the whole track |
| `ks_segment_vote_v1` | `detect_key_segment_vote()` | Per-window key detection with RMS-weighted voting across allin1 sections |
| `ks_window_vote_v1` | `detect_key_segment_vote()` | Same as segment_vote but with fixed 20s/10s-step windows instead of allin1 sections |

## How Loudness Detection Works

Both tiers use the same function, `compute_loudness()` in
`analyzer.py:211`:

```python
rms = np.sqrt(np.mean(y**2))
db = 20 * np.log10(rms + 1e-10)
```

This is a simple RMS-based loudness estimate in dB, not a true LUFS/integrated
loudness measurement.

## Internal Flow

1. The analysis service runs `analyze_audio()` in
   `ops/analysis-service/src/sow_analysis/workers/analyzer.py:226`.
2. allin1 runs in a thread pool (`analyzer.py:277-286`), producing `bpm`,
   `beats`, `downbeats`, `sections`, and `embeddings`.
3. librosa runs key detection (`analyzer.py:316`) and loudness
   (`analyzer.py:324`).
4. The result dict is built (`analyzer.py:330-339`) including `tempo_bpm`,
   key fields, `loudness_db`, `beats`, `downbeats`, `sections`,
   `embeddings_shape`.
5. The service persists the result as `result_json` in its jobs table and
   uploads the same dict as `{hash_prefix}.json` to R2
   (`queue.py:550-556`).
6. The admin CLI (via `--wait`, `--sync`, or `--reconcile`) fetches the result
   and calls `DatabaseClient.update_recording_analysis()` in
   `ops/admin-cli/src/stream_of_worship/admin/db/client.py:945`.
7. For the full tier, all columns are written and `analysis_status` is set to
   `completed`.

## Inspecting Results

```bash
# List recordings by analysis status
sow-admin audio list --status pending
sow-admin audio list --status processing
sow-admin audio list --status completed
sow-admin audio list --status failed

# Show details for one recording (by hash prefix)
sow-admin audio show <hash-prefix>
```

The `show` output includes `tempo_bpm`, `musical_key`, `musical_mode`,
`key_confidence`, `analysis_status`, `analysis_job_id`, and all other analysis
columns once write-back has completed.
