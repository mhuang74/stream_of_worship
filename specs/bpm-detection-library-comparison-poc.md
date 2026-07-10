# Plan: BPM Detection Library Comparison POC

## Overview

Create a standalone POC script `lab/poc-scripts/compare_bpm_libraries.py` that compares BPM detection across **BeatNet**, **madmom**, **librosa**, and the current **production analyzer (v4 octave guard)** on three specific Chinese worship songs, with **per-song and aggregate timing** for each library. The script is structured to scale to the full 99-song catalog in the future, producing a BPM-distribution comparison alongside the timing data. The goal is to evaluate both **accuracy** and **runtime cost** of more-accurate neural beat trackers so the production analyzer can be upgraded without unexpectedly slow analysis.

## Background & Motivation

The current fast-tier analyzer (`ops/analysis-service/src/sow_analysis/workers/analyzer.py:406-473`) uses `librosa.beat.tempo` with a `start_bpm=80` prior plus a v4 octave-guard heuristic. The v4 spec (`specs/fix-tempo-detection-quantization-v4.md`) documents a fundamental ambiguity: the half-time guard **cannot distinguish** a true 65 BPM song from a halved 130 BPM song when both yield the same `(primary, alt)` pair. Neural-network-based beat trackers (BeatNet, madmom) detect individual beats via RNN/TCN models rather than tempogram peak-picking and may resolve this.

Critically, accuracy is not the only question — **runtime matters**. The fast-tier analyzer runs on every newly imported recording and on re-analysis sweeps. If madmom/BeatNet are 5–20× slower than librosa, that affects batch throughput. This POC quantifies both axes on the same set of songs so the tradeoff is visible.

## Test Songs (Phase 1) and Full Catalog (Phase 2)

### Phase 1 (this POC): 3 specific songs

| Song ID | Hash Prefix (resolved at runtime) |
|---|---|
| `yu_mi_man_bu_e46c5fe7` | DB lookup via `get_recording_by_song_id` |
| `mei_hao_de_chuang_zao_3d42d76e` | DB lookup |
| `song_zan_gui_yu_mi_d0e41287` | `441e02a0dbc7` (confirmed) |

The 3 song IDs are the **default**, so the script runs with no args: `compare_bpm_libraries.py`.

### Phase 2 (future, same script): full 99-song catalog

The script accepts options that make the catalog sweep a no-code change:

```bash
# All 3 default songs (this POC)
uv run --project lab/poc-scripts --extra bpm_poc python lab/poc-scripts/compare_bpm_libraries.py

# Explicit list of song IDs
... compare_bpm_libraries.py --song-id A --song-id B --song-id C

# Entire catalog (Phase 2 future run)
... compare_bpm_libraries.py --all-catalog
... compare_bpm_libraries.py --all-catalog --limit 99
```

`--all-catalog` fetches the full catalog pool via the same `fetch_catalog_pool` used by `test_tempo_strategies.py` (which already samples the catalog), but without the sampling step — every song. The 3 hardcoded defaults are kept so the POC reproduces without flags.

## Libraries Compared

| # | Library | Approach | BPM Derivation |
|---|---|---|---|
| A | **librosa** (raw) | `librosa.beat.tempo`, `hop=512`, `start_bpm=80` | Direct tempo output |
| B | **madmom** | `RNNBeatProcessor` → `DBNBeatTrackingProcessor` | `60.0 / median(diff(beats))` |
| C | **BeatNet** | Neural net beat/downbeat tracker (TCN, offline) | `60.0 / median(diff(beats))` (or library output if simpler) |
| D | **Production v4** (baseline) | librosa + double-time guard (`>120`) + half-time guard (`<60`) | Replicated `_compute_tempo()` logic inline |

### Why D is replicated, not imported

`analyze_audio_fast()` is async in `sow_analysis.workers.analyzer` and pulls in the entire analysis-service package (allin1, demucs, PyTorch), which `lab/poc-scripts` does not depend on. Replicating the ~30-line `_compute_tempo()` guard logic inline (pure librosa) keeps the POC self-contained and consistent with `test_tempo_strategies.py`.

## Timing Infrastructure

Every per-library per-song call is wrapped with `time.perf_counter()`:

```python
t0 = time.perf_counter()
bpm = bpm_madmom(audio_path)
elapsed_madmom = time.perf_counter() - t0
```

Two timing views are produced:

1. **Per-song table** — each library's BPM and elapsed seconds side by side (lets you see "madmom took 3.2s for this song vs librosa 0.8s").
2. **Aggregate runtime summary** — total and mean elapsed time per library across all processed songs. This is the key number for projecting batch cost: `(mean_madmom_per_song × 99)` tells you how long a full-catalog sweep would take.

## Architecture

### File: `lab/poc-scripts/compare_bpm_libraries.py`

```
compare_bpm_libraries.py
├── DEFAULT_SONG_IDS = ["yu_mi_man_bu_e46c5fe7",
│                       "mei_hao_de_chuang_zao_3d42d76e",
│                       "song_zan_gui_yu_mi_d0e41287"]
├── SR = 22050, HOP = 512
├── build_r2_client()           → AdminConfig + R2Client (same as test_tempo_strategies.py)
├── build_db_client()           → AdminConfig + ConnectionProvider + DatabaseClient
├── resolve_songs(args)         → either the 3 default IDs, --song-id list, or fetch_catalog_pool() for --all-catalog
├── resolve_song(song_id)       → DB lookup: hash_prefix, stored_bpm, title
├── download_audio(r2, hash_prefix, dest)  → R2 → temp file (same as existing scripts)
│
├── timed_librosa_raw(y, sr)        → (bpm, elapsed)
├── timed_madmom(audio_path)        → (bpm, elapsed)
├── timed_beatnet(audio_path)       → (bpm, elapsed)
├── timed_prod_v4(y, sr)           → (bpm, elapsed)   # replicated _compute_tempo()
│
├── main()
│   ├── parse args (--song-id, --all-catalog, --limit, --csv)
│   ├── for each song: download → run 4 timed libraries → collect row
│   ├── write results CSV (if --csv or always, see Persistence)
│   ├── print per-song table
│   ├── print aggregate timing summary
│   └── print BPM distribution summary (scales to 99-song run)
```

### Audio Loading

- **librosa / prod-v4**: `librosa.load(path, sr=22050, mono=True)` → `(y, sr)`. One load shared by both (timing counted under each library's wrapper, but load cost is identical so not a confound).
- **madmom**: accepts file path; loads internally (`madmom.audio.signal`).
- **BeatNet**: accepts file path; loads internally.

### madmom BPM Derivation (per `specs/worship-music-transition-system-design.md:216-227`)

```python
proc = madmom.features.RNNBeatProcessor()
act = proc(str(audio_path))
beat_proc = madmom.features.beats.DBNBeatTrackingProcessor(fps=100)
beats = beat_proc(act)
tempo = 60.0 / np.median(np.diff(beats)) if len(beats) > 1 else 0.0
```

### BeatNet BPM Derivation

BeatNet's exact API will be verified against its README/PyPI during implementation. Typical usage:

```python
from BeatNet.BeatNet import BeatNet
estimator = BeatNet(num_classes=1, mode='offline', inference_model='PGT', plot=[], thread=False)
output = estimator.process(str(audio_path))   # beat/downbeat arrays
# BPM = 60.0 / median(diff(beat_times))
```

If the import path or call signature differs, adapt. The required output is a beat-time array; BPM is derived the same way as madmom for consistency.

### Production v4 Guard (replicated from `analyzer.py:406-473`)

1. `onset_env = librosa.onset.onset_strength(y, sr, hop_length=512)`
2. `tempo_primary = librosa.beat.tempo(onset_env, sr, hop_length=512, start_bpm=80)`
3. Double-time guard: if `> 120`, re-estimate with `start_bpm=60`; accept if `≈ primary/2` and in `[65, 100]`
4. Half-time guard: if `< 60`, re-estimate with `start_bpm=120`; accept if `≈ 2×primary` and in `[110, 180]`
5. Otherwise return `tempo_primary`

## Output Format

### 1. Per-song table

```
=== BPM Detection Library Comparison ===

Song 1/3: 頌讚歸於祢 (song_zan_gui_yu_mi_d0e41287)
  Hash: 441e02a0dbc7 | Stored BPM: 130.0
  Downloaded audio.mp3 (5.97 MB)

  Library         BPM     Octave*   Time
  ──────────────  ──────  ────────  ─────
  librosa (raw)   129.2   ×2        0.8s
  madmom          65.1    ≈1        3.2s
  BeatNet         65.0    ≈1        2.1s
  prod-v4         129.2   ×2        0.8s

  * Octave = ratio of library BPM to stored DB BPM (≈1, ×2, ×0.5 flags doubling)

Song 2/3: ...
Song 3/3: ...
```

### 2. Aggregate runtime summary

```
=== Aggregate Runtime ===
                                      (3 songs)
  Library         Total      Mean/song   Rel×
  ──────────────  ─────────  ──────────  ────
  librosa (raw)   2.4s       0.8s        1.0×
  madmom          9.6s       3.2s        4.0×
  BeatNet         6.3s       2.1s        2.6×
  prod-v4         2.4s       0.8s        1.0×

  Projected 99-song catalog sweep:
    librosa (raw):   ~80s
    madmom:          ~320s (5m 20s)
    BeatNet:         ~210s (3m 30s)
    prod-v4:         ~80s
```

`Rel×` = ratio to librosa's mean (the fastest). `Projected` = `mean × N_songs`, shown only when fewer than the full catalog were processed; for the 3-song run, projected-to-99 is the actionable preview. When `--all-catalog` is used, the projection line is omitted and the totals are actuals.

### 3. BPM distribution summary (scales to 99-song Phase 2)

For the 3-song POC this is minimal, but it exercises the same reporting path used for the catalog sweep:

```
=== BPM Distribution ===

  librosa (raw)   unique=2  range=[65.1, 129.2]  spread=64.1
    65.1   # (1)
    129.2  ## (2)

  madmom           unique=3  range=[65.0, 88.0]   spread=23.0
    65.0   # (1)
    72.0   # (1)
    88.0   # (1)

  BeatNet          ...
  prod-v4          ...
  stored (DB)      ...
```

In Phase 2 (99 songs) this distribution comparison is the primary output; in Phase 1 it just confirms the 3 songs ran and the values are sensible.

### 4. CSV output (Persistence)

Results are written to `lab/poc-scripts/output/bpm_comparison_<timestamp>.csv` with columns:

```
song_id,hash_prefix,title,stored_bpm,librosa_bpm,librosa_sec,madmom_bpm,madmom_sec,
beatnet_bpm,beatnet_sec,prod_v4_bpm,prod_v4_sec
```

This lets the Phase 2 full-catalog run be post-analyzed without re-running (a 99-song BeatNet+madmom sweep could take 10+ minutes). The CSV path mirrors existing script output conventions under `lab/poc-scripts/output/`.

## CLI Interface (Typer)

```python
# Args
song_ids: list[str] = typer.Option(None, "--song-id", help="Song ID (repeatable); defaults to 3 POC songs")
all_catalog: bool = typer.Option(False, "--all-catalog", help="Process entire catalog (Phase 2)")
limit: Optional[int] = typer.Option(None, "--limit", help="Cap number of songs (with --all-catalog)")
csv: bool = typer.Option(True, "--csv/--no-csv", help="Write results CSV (default on)")
```

Uses Typer consistent with other poc-scripts (e.g. `gen_lrc_qwen3_asr.py` uses Typer). `python compare_bpm_libraries.py --help` works.

## Dependencies

### New extra in `lab/poc-scripts/pyproject.toml`

```toml
[project.optional-dependencies]
bpm_poc = [
    "librosa>=0.10.0",
    "numpy>=1.24.0",
    "scipy>=1.10.0",
    "soundfile>=0.12.0",
    "torch>=2.8.0,<2.9.0",
    "torchaudio>=2.8.0,<2.9.0",
    "madmom @ git+https://github.com/CPJKU/madmom.git",
    "BeatNet>=1.0.0",
    "boto3>=1.34.0",
    "typer>=0.12.0",
]
```

madmom matches the analysis-service's git source (Python 3.11-compatible dev version `0.17.dev0`). The base `stream-of-worship[postgres]` dependency already provides `AdminConfig`, `R2Client`, `DatabaseClient`, `ConnectionProvider`. BeatNet's exact PyPI name/version will be verified during implementation.

## Run Command

```bash
# Phase 1: 3 POC songs
uv run --project lab/poc-scripts --extra bpm_poc python lab/poc-scripts/compare_bpm_libraries.py

# Phase 2 future: full 99-song catalog
uv run --project lab/poc-scripts --extra bpm_poc python lab/poc-scripts/compare_bpm_libraries.py --all-catalog --limit 99
```

## Implementation Phases

### Phase 1: Add `bpm_poc` extra to `lab/poc-scripts/pyproject.toml`
- Add optional-dependency section as above
- Verify install: `uv run --project lab/poc-scripts --extra bpm_poc python -c "import madmom; import BeatNet; import librosa; import typer"`

### Phase 2: Write `compare_bpm_libraries.py`
- Structure per Architecture section
- Follow `test_tempo_strategies.py` / `test_tempo_hop_length.py` conventions (same R2 download pattern, same `AdminConfig.load()` + `R2Client` usage)
- `pathlib.Path` throughout; Black 100; Ruff py311; no comments per project convention (docstrings ok)
- Typer CLI with the three flags
- `time.perf_counter()` around every library call; collect `elapsed` alongside `bpm`
- Aggregate summary + projected-99 line when n < catalog
- CSV output to `lab/poc-scripts/output/`

### Phase 3: Run and verify
- Run on the 3 default songs
- Confirm per-song table, aggregate timing, distribution summary, and CSV all render
- Confirm BeatNet/madmom produce plausible BPMs and elapsed times
- Sanity-check the projected-99 estimate against actual per-song means
- Note any library that errors (especially BeatNet API differences)

## Files Changed

| File | Change |
|---|---|
| `lab/poc-scripts/pyproject.toml` | Add `bpm_poc` optional-dependency section |
| `lab/poc-scripts/compare_bpm_libraries.py` | New script — 4-library BPM comparison with per-song + aggregate timing, catalog-scale CLI, CSV output |

No changes to: analysis-service, admin-cli, webapp, or any existing analyzer code.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| BeatNet API differs from expected | Verify during Phase 1; adapt import/call in Phase 2 |
| madmom build fails locally (Cython) | The `git+https://github.com/CPJKU/madmom.git` dev version is Python 3.11-compatible (proven in analysis-service venv). If it fails, fall back to running in that venv. |
| BeatNet model download (large) | First run downloads weights; documented in script output |
| R2 download fails for a song | Skip and continue (existing pattern); CSV row marked `DOWNLOAD_FAILED` |
| BeatNet/madmom slow on full catalog | Phase 2 run is one-shot; CSV lets re-analysis without re-running. Per-song mean from Phase 1 projects the total. |
| Audio loading time confounds library timing | librosa/prod-v4 share one `librosa.load` but timing is captured inside each `timed_*` wrapper after `y, sr` is ready, so the *algorithm* time is measured, not the load. madmom/BeatNet load internally, included in their time (acceptable — reflects real end-to-end cost vs librosa's algorithm-only time). Documented in script header. |

## Out of Scope

- **Adoption into production analyzer** — separate spec, informed by this POC's timing/accuracy data.
- **Beat/downbeat array comparison** — BPM-only per user decision.
- **allin1 (full-tier) comparison** — requires Docker + GPU; out of scope.
- **Caching library results** — Phase 2's CSV is the persistence layer; no in-process cache.
- **Parallelizing library calls** — kept sequential for clean timing measurement.
