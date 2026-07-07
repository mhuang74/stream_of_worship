# BPM Detection Library Comparison POC - Implementation Summary

**Date:** 2026-07-08
**Spec:** `specs/bpm-detection-library-comparison-poc.md`

## Overview

Implemented a standalone POC script that compares BPM detection accuracy and runtime cost across four libraries — librosa (raw), madmom, BeatNet, and the production v4 analyzer — on Chinese worship songs. The script produces per-song BPM + timing tables, aggregate runtime summaries with projected catalog-sweep estimates, BPM distribution comparisons, and CSV output for post-hoc analysis. Designed to scale from a 3-song POC to the full 99-song catalog with no code changes.

---

## Files Modified/Created

| File | Action | Description |
|------|--------|-------------|
| `lab/poc-scripts/pyproject.toml` | Modified | Added `bpm_poc` optional-dependency section |
| `lab/poc-scripts/compare_bpm_libraries.py` | **New** | 4-library BPM comparison script with timing, distribution, and CSV output |
| `specs/bpm-detection-library-comparison-poc.md` | **New** | Spec document (pre-existing, committed with implementation) |

---

## Implementation Details

### 1. Dependencies (`pyproject.toml`)

Added `bpm_poc` extra with 10 packages:

```toml
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

- **madmom**: Same git source as analysis-service (`git+https://github.com/CPJKU/madmom.git`), Python 3.11-compatible dev version
- **BeatNet**: PyPI `BeatNet>=1.0.0` (verified v1.1.3 latest)
- Base `stream-of-worship[postgres]` dependency provides `AdminConfig`, `R2Client`, `DatabaseClient`, `ConnectionProvider`

### 2. Script Architecture (`compare_bpm_libraries.py`)

```
compare_bpm_libraries.py
├── DEFAULT_SONG_IDS = ["yu_mi_man_bu_e46c5fe7",
│                       "mei_hao_de_chuang_zao_3d42d76e",
│                       "song_zan_gui_yu_mi_d0e41287"]
├── SR = 22050, HOP = 512, CATALOG_SIZE = 99
├── SongInfo dataclass          → song_id, hash_prefix, stored_bpm, title
├── LibraryResult dataclass     → bpm, elapsed
├── build_r2_client()           → AdminConfig + R2Client
├── build_db_client()           → AdminConfig + ConnectionProvider + DatabaseClient
├── resolve_songs(args)         → 3 default IDs, --song-id list, or fetch_catalog_pool()
├── resolve_song(song_id)       → DB lookup via get_recording_by_song_id + get_song
├── download_audio(r2, hash, dest)  → R2 → temp file
│
├── _get_madmom_processors()    → Lazy singleton (RNNBeatProcessor + DBNBeatTrackingProcessor)
├── _get_beatnet_estimator()    → Lazy singleton (BeatNet offline DBN mode)
│
├── timed_librosa_raw(y, sr)        → (LibraryResult)
├── timed_madmom(audio_path)        → (LibraryResult)
├── timed_beatnet(audio_path)       → (LibraryResult)
├── timed_prod_v4(y, sr)           → (LibraryResult)
│
├── print_per_song_table()
├── print_aggregate_runtime()       → includes projected-99 line
├── print_bpm_distribution()
├── write_csv()
└── main()                          → Typer CLI entry point
```

### 3. Library Implementations

#### A. librosa (raw)
- `librosa.onset.onset_strength(y, sr, hop_length=512)` → `librosa.beat.tempo(..., start_bpm=80)`
- Receives pre-loaded `(y, sr)` array; timing captures algorithm only

#### B. madmom
- `RNNBeatProcessor()` → `DBNBeatTrackingProcessor(fps=100)`
- BPM = `60.0 / np.median(np.diff(beats))` when `len(beats) > 1`
- Receives file path; timing includes internal audio loading + processing

#### C. BeatNet
- `BeatNet(1, mode='offline', inference_model='DBN', plot=[], thread=False)`
- Output is `numpy_array(num_beats, 2)` with beat times in column 0
- BPM = `60.0 / np.median(np.diff(beats))` when `len(beats) > 1`
- API verified against GitHub README: `from BeatNet.BeatNet import BeatNet`

#### D. Production v4 (replicated)
- Replicates `_compute_tempo()` from `analyzer.py:406-473` inline (pure librosa)
- Double-time guard: if `> 120`, re-estimate with `start_bpm=60`; accept if `≈ primary/2` and in `[65, 100]`
- Half-time guard: if `< 60`, re-estimate with `start_bpm=120`; accept if `≈ 2×primary` and in `[110, 180]`
- Not imported from `sow_analysis.workers.analyzer` to keep POC self-contained (avoids pulling in allin1, demucs, PyTorch)

### 4. Timing Infrastructure

- Every per-library per-song call wrapped with `time.perf_counter()`
- **Lazy model singletons** (`_get_madmom_processors`, `_get_beatnet_estimator`): model weights loaded once on first call, excluded from per-song timing so the mean-per-song projection is not inflated by startup cost
- librosa/prod-v4 share one `librosa.load` but timing is captured inside each `timed_*` wrapper after `(y, sr)` is ready — measures algorithm time, not load time
- madmom/BeatNet load internally; timing includes load (reflects real end-to-end cost)

### 5. CLI Interface (Typer)

```python
song_ids: list[str] = typer.Option(None, "--song-id", help="Song ID (repeatable)")
all_catalog: bool = typer.Option(False, "--all-catalog", help="Process entire catalog")
limit: Optional[int] = typer.Option(None, "--limit", help="Cap number of songs")
csv_output: bool = typer.Option(True, "--csv/--no-csv", help="Write results CSV")
```

- Defaults to 3 POC songs when no flags given
- `--all-catalog` uses `fetch_catalog_pool()` (same as `test_tempo_strategies.py`) without sampling
- `--limit` caps catalog size (for Phase 2 `--all-catalog --limit 99`)

### 6. Output Format

#### Per-song table
```
Song 1/3: 頌讚歸於祢 (song_zan_gui_yu_mi_d0e41287)
  Hash: 441e02a0dbc7 | Stored BPM: 130.0

  Library         BPM     Octave*   Time
  ──────────────  ──────  ────────  ─────
  librosa (raw)   129.2   ×2        0.8s
  madmom          65.1    ≈1        3.2s
  BeatNet         65.0    ≈1        2.1s
  prod-v4         129.2   ×2        0.8s
```

#### Aggregate runtime summary
```
=== Aggregate Runtime ===
                                      (3 songs)
  Library         Total      Mean/song   Rel×
  ──────────────  ─────────  ──────────  ────
  librosa (raw)   2.4s       0.8s        1.0×
  madmom          9.6s       3.2s        4.0×
  ...

  Projected 99-song catalog sweep:
    librosa (raw):   ~80s
    madmom:          ~320s (5m 20s)
```

- `Rel×` = ratio to fastest library's mean
- Projected-99 line shown only when `n < CATALOG_SIZE` and not `--all-catalog`

#### BPM distribution
```
=== BPM Distribution ===
  librosa (raw)   unique=2  range=[65.1, 129.2]  spread=64.1
    65.1   # (1)
    129.2  ## (2)
```

#### CSV output
- Path: `lab/poc-scripts/output/bpm_comparison_<timestamp>.csv`
- Columns: `song_id, hash_prefix, title, stored_bpm, librosa_bpm, librosa_sec, madmom_bpm, madmom_sec, beatnet_bpm, beatnet_sec, prod_v4_bpm, prod_v4_sec`

### 7. Error Handling

- Download failures: skip song, print `DOWNLOAD FAILED`, continue
- Library errors: caught per-library, `bpm=None` in result, `ERROR` in output, elapsed time still captured
- DB lookup failures: warning printed, song skipped
- Empty results after all songs: exit with code 1

---

## Design Decisions

1. **Replicate v4 logic inline** rather than import from `sow_analysis.workers.analyzer` — keeps POC self-contained, avoids pulling in allin1/demucs/PyTorch dependencies
2. **Lazy model singletons** for madmom/BeatNet — one-time weight loading excluded from per-song timing so mean-per-song projection is accurate
3. **Sequential library calls** — clean timing measurement, no parallelization confound
4. **Shared `librosa.load` for librosa/prod-v4** — load cost identical, not a confound; timing captured inside wrappers after `(y, sr)` ready
5. **madmom/BeatNet include load time** — reflects real end-to-end cost vs librosa's algorithm-only time; documented in script header
6. **`fetch_catalog_pool` for `--all-catalog`** — same function used by `test_tempo_strategies.py`, no sampling step
7. **CSV always on by default** — Phase 2 full-catalog run (10+ min) can be post-analyzed without re-running

---

## Verification

```bash
# Install verification
uv run --project lab/poc-scripts --extra bpm_poc python -c "import madmom; import BeatNet; import librosa; import typer"
# → All imports OK

# Help
uv run --project lab/poc-scripts --extra bpm_poc python lab/poc-scripts/compare_bpm_libraries.py --help
# → Typer help rendered

# Lint
uvx ruff check lab/poc-scripts/compare_bpm_libraries.py --config lab/poc-scripts/pyproject.toml
# → All checks passed

uvx black --check --line-length 100 lab/poc-scripts/compare_bpm_libraries.py
# → All done, 1 file would be left unchanged
```

---

## Run Commands

```bash
# Phase 1: 3 POC songs (default)
uv run --project lab/poc-scripts --extra bpm_poc python lab/poc-scripts/compare_bpm_libraries.py

# Explicit song IDs
uv run --project lab/poc-scripts --extra bpm_poc python lab/poc-scripts/compare_bpm_libraries.py --song-id A --song-id B

# Phase 2: full 99-song catalog
uv run --project lab/poc-scripts --extra bpm_poc python lab/poc-scripts/compare_bpm_libraries.py --all-catalog --limit 99
```

---

## Out of Scope (per spec)

- Adoption into production analyzer (separate spec, informed by this POC's data)
- Beat/downbeat array comparison (BPM-only)
- allin1 full-tier comparison (requires Docker + GPU)
- Caching library results (CSV is the persistence layer)
- Parallelizing library calls (sequential for clean timing)
