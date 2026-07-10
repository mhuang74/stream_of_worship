# Plan: BeatNet Octave Guard + Alternate-Model Comparison

## Overview

BeatNet reports a fast BPM of **142.9** for the slow worship song
"Reach One More [再贏得一個靈魂]"
(`reachonemore_zai_ying_de_yi_ge_ling_hun__49a1c779`) whose true tempo is
**78.3 BPM** (confirmed by librosa 78.3, madmom 77.9, prod-v4 78.3, and the
stored DB value). This spec documents the root cause and proposes two
additions to `lab/poc-scripts/compare_bpm_libraries.py`:

1. **Octave guard on neural-tracker outputs** — apply prod-v4's halving
   heuristic to `timed_beatnet` and `timed_madmom`, which currently have no
   octave correction at all.
2. **Alternate BeatNet model comparison** — add a `--beatnet-model` option
   (1=GTZAN, 2=Ballroom, 3=Rock corpus) so the three trained CRNNs can be
   compared on the same song without code changes.

This is a **research POC** — adoption into the production analyzer is out of
scope (same boundary as the existing v1/v5 specs).

## Background & Motivation

### The failure

CSV row from `lab/poc-scripts/output/bpm_comparison_20260708_114300.csv`:

```
song_id:        reachonemore_zai_ying_de_yi_ge_ling_hun__49a1c779
hash_prefix:    6a388fe58b99
title:          Reach One More [再贏得一個靈魂]
stored_bpm:     78.30256
librosa_bpm:    78.3   (0.652s)
madmom_bpm:     77.9   (37.210s)
beatnet_bpm:    142.9  (2.665s)   ← wrong
prod_v4_bpm:    78.3   (0.491s)
lrc_available:  true
cps:            1.1544   (chars=284, lines=30, span=246.020s)
cps_bucket:      slow
prod_v5_bpm:    63.0   (0.492s)   prior=cps_slow
```

- True beat interval = 60 / 78.3 = **0.766 s**
- BeatNet interval = 60 / 142.9 = **0.420 s**
- Ratio = 0.766 / 0.420 = **1.82** (≈ ½, i.e. BeatNet is tracking roughly
  every half-beat — a subdivision, not the quarter-note pulse)

### Root cause

BeatNet is constructed in `_get_beatnet_estimator`
(`compare_bpm_libraries.py:299-318`) as:

```python
BeatNet(1, mode="offline", inference_model="DBN", plot=[], thread=False)
```

Internally (`BeatNet/BeatNet.py:66`) this instantiates the madmom decoder:

```python
self.estimator = DBNDownBeatTrackingProcessor(beats_per_bar=[2, 3, 4], fps=50)
```

The `DBNDownBeatTrackingProcessor` defaults (verified by introspection) are:

| Parameter | Default | Effect |
|---|---|---|
| `min_bpm` | `55.0` | Floor — does not prevent double-time over-detection |
| `max_bpm` | `215.0` | Wide ceiling — no worship-music prior |
| `num_tempi` | `60` | 60 tempo hypotheses spread log-spaced across [55, 215] |
| `transition_lambda` | `100` | Strong preference for constant tempo |
| `observation_lambda` | `16` | Beat state occupies 1/16 of each beat period |
| `correct` | `True` | Snaps beats to nearest activation peak |
| `fps` | `50` | 50 frames per second |

The pipeline is:

1. **CRNN** (model 1, GTZAN-trained on pop/rock ~120–140 BPM) produces
   per-frame beat + downbeat activation probabilities.
2. **DBN** decodes activations into beat times via Viterbi, free to pick any
   tempo in [55, 215] BPM and any meter in {2, 3, 4}.

For sparse worship ballads that are out-of-distribution for the GTZAN
training corpus, the CRNN activations peak on **eighth-note subdivisions**
rather than quarter notes. The DBN, with its wide flat tempo search space,
happily locks onto those subdivisions at ~143 BPM (well within [55, 215]).

The BPM is then derived in `timed_beatnet` (`compare_bpm_libraries.py:358-373`):

```python
output = estimator.process(str(audio_path))
beats = output[:, 0]                                    # beat times only
bpm = float(60.0 / np.median(np.diff(beats)))           # no octave guard
```

The downbeat column (`output[:, 1]`) is discarded, so every false sub-beat
position enters the median. There is **no octave guard** — unlike
`timed_prod_v4` (line 376-415) which halves when `tempo_primary > 120` and a
re-estimated `start_bpm=60` alternative lands in `[65, 100]`.

### Why madmom is unaffected

`timed_madmom` (line 340-355) uses madmom's standalone
`RNNBeatProcessor` + `DBNBeatTrackingProcessor` (NOT the downbeat variant),
which also defaults to `min_bpm=55, max_bpm=215`. Yet it tracks at 77.9 BPM.
The difference is the **activation function**: madmom's RNN was trained on
the GTZAN beat task specifically, while BeatNet's CRNN was trained jointly
for beat + downbeat on a different corpus split, producing activations that
fire more readily on subdivisions for out-of-distribution audio.

### Why prod-v5 returns 63.0 (related but separate)

The CPS for this song is 1.15, which falls in the "slow" bucket
(`cps < 1.5`). The v5 prior centers at 70 BPM with σ=12, so prior mass is
concentrated in [55, 85] BPM. The librosa tempogram peak under that prior
lands at 63.0 — a different octave error (under-detection, not
over-detection). This is a known v5 issue but **out of scope** for this spec;
the user's question is specifically about BeatNet's 142.9.

## Design Decisions (confirmed with user)

| Decision | Choice |
|---|---|
| Octave guard scope | **BeatNet + madmom** (both neural trackers; madmom is currently correct but other slow songs may trigger the same issue) |
| Guard direction | **Halving only** (upper guard: `bpm > 120` → try `bpm/2`). Neural trackers don't underestimate; they over-count subdivisions. No doubling guard needed. |
| Halving criterion | **Range gate only**: if `bpm > 120` AND `bpm/2 ∈ [65, 100]`, use `bpm/2`. No re-estimation (no cheap alternate estimator exists for neural trackers — the median interval is all we have). |
| Worship range gate | `[65, 100]` BPM — matches prod-v4's half-time acceptance window (line 392) |
| Trigger threshold | `> 120` BPM — matches prod-v4's upper trigger (line 385) |
| BeatNet model option | `--beatnet-model` (int, default 1) passed to `_get_beatnet_estimator` |
| Models to compare | 1=GTZAN (default), 2=Ballroom, 3=Rock corpus — all three ship with the BeatNet package (`BeatNet/models/model_{1,2,3}_weights.pt`, confirmed in `BeatNet.py:73-78`) |

## Implementation

### Part A — Octave guard helper + application

#### New helper function

Place near `octave_flag` (around line 480), alongside the other output
formatting helpers:

```python
def halve_fast_bpm(
    bpm: float,
    trigger: float = 120.0,
    lo: float = 65.0,
    hi: float = 100.0,
) -> float:
    """Halve a runaway high BPM if its half-time lands in the worship range.

    Neural trackers (BeatNet, madmom) tend to lock onto eighth-note
    subdivisions for slow worship songs, reporting ~2× the true tempo.
    This mirrors the upper half of prod-v4's octave guard (line 385-395)
    but without re-estimation — the median inter-beat interval is all we
    have for neural trackers, so the half-time candidate is simply bpm/2.

    No doubling guard (bpm < 60) is applied: neural trackers over-count
    subdivisions; they do not underestimate.
    """
    if bpm > trigger:
        half = bpm / 2.0
        if lo <= half <= hi:
            return half
    return bpm
```

#### Apply to `timed_beatnet` (line 358-373)

Current:
```python
def timed_beatnet(audio_path: Path) -> LibraryResult:
    estimator = _get_beatnet_estimator()
    t0 = time.perf_counter()
    try:
        output = estimator.process(str(audio_path))
        beats = output[:, 0]
        if len(beats) > 1:
            bpm = float(60.0 / np.median(np.diff(beats)))
        else:
            bpm = 0.0
        elapsed = time.perf_counter() - t0
        return LibraryResult(bpm=bpm, elapsed=elapsed)
    ...
```

Change: wrap the final `bpm` with `halve_fast_bpm(...)` before returning:

```python
        if len(beats) > 1:
            bpm = float(60.0 / np.median(np.diff(beats)))
        else:
            bpm = 0.0
        bpm = halve_fast_bpm(bpm)
        elapsed = time.perf_counter() - t0
        return LibraryResult(bpm=bpm, elapsed=elapsed)
```

#### Apply to `timed_madmom` (line 340-355)

Same one-line wrap after the median computation:

```python
        if len(beats) > 1:
            bpm = float(60.0 / np.median(np.diff(beats)))
        else:
            bpm = 0.0
        bpm = halve_fast_bpm(bpm)
        elapsed = time.perf_counter() - t0
        return LibraryResult(bpm=bpm, elapsed=elapsed)
```

madmom currently reports 77.9 for this song, so the guard is a no-op here
(77.9 < 120). It is added defensively for other slow worship songs where
madmom's RNN may also lock onto subdivisions.

#### Docstring update

Update the module docstring (line 2-20) to note that BeatNet and madmom now
apply a post-hoc octave guard mirroring prod-v4's halving heuristic.

### Part B — `--beatnet-model` option

#### Rework `_get_beatnet_estimator` (line 299-318)

Current:
```python
_beatnet_estimator = None

def _get_beatnet_estimator():
    global _beatnet_estimator
    if _beatnet_estimator is None:
        ...
        _beatnet_estimator = BeatNet(
            1, mode="offline", inference_model="DBN", plot=[], thread=False
        )
    return _beatnet_estimator
```

Change to accept a `model` argument and cache per-model:

```python
_beatnet_estimators: dict[int, "BeatNet"] = {}

def _get_beatnet_estimator(model: int = 1):
    if model not in _beatnet_estimators:
        import sys
        import types

        if "pyaudio" not in sys.modules:
            pyaudio_stub = types.ModuleType("pyaudio")
            pyaudio_stub.PyAudio = type("PyAudio", (), {})
            pyaudio_stub.paFloat32 = 0
            sys.modules["pyaudio"] = pyaudio_stub

        from BeatNet.BeatNet import BeatNet

        _beatnet_estimators[model] = BeatNet(
            model, mode="offline", inference_model="DBN", plot=[], thread=False
        )
    return _beatnet_estimators[model]
```

#### Update `timed_beatnet` signature

```python
def timed_beatnet(audio_path: Path, model: int = 1) -> LibraryResult:
    estimator = _get_beatnet_estimator(model)
    ...
```

#### Add CLI option (in `main`, line 842-854)

```python
@app.command()
def main(
    song_ids: list[str] = typer.Option(
        None, "--song-id", help="Song ID (repeatable); defaults to 3 POC songs"
    ),
    all_catalog: bool = typer.Option(
        False, "--all-catalog", help="Process entire catalog (Phase 2)"
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", help="Cap number of songs (with --all-catalog)"
    ),
    csv_output: bool = typer.Option(True, "--csv/--no-csv", help="Write results CSV (default on)"),
    beatnet_model: int = typer.Option(
        1, "--beatnet-model", help="BeatNet model: 1=GTZAN (default), 2=Ballroom, 3=Rock corpus"
    ),
) -> None:
```

#### Pass model through to `timed_beatnet` (line 909)

```python
    results: dict[str, LibraryResult] = {
        "librosa_raw": timed_librosa_raw(y, sr),
        "madmom": timed_madmom(audio_path),
        "beatnet": timed_beatnet(audio_path, beatnet_model),
        "prod_v4": timed_prod_v4(y, sr),
        "prod_v5": timed_prod_v5(y, sr, cps),
    }
```

#### Print model in per-song table header

In `print_per_song_table` (line 511-552), the BeatNet row label could note
the model. Simplest: add the model number to the stderr progress echo in
`main` (line 885-890):

```python
typer.echo(
    f"\nSong {i}/{len(songs)}: {song.title} ({song.song_id})\n"
    f"  Hash: {song.hash_prefix} | Stored BPM: {song.stored_bpm or '—'}\n"
    f"  BeatNet model: {beatnet_model}\n"
    f"  Downloaded audio.mp3 ({size_mb:.2f} MB)",
    err=True,
)
```

No CSV column change needed — the CSV already has `beatnet_bpm` and
`beatnet_sec`; the model is captured in the run's stderr log and the output
filename context.

## Verification

### Test 1: Octave guard on the failing song

```bash
uv run --project lab/poc-scripts --extra bpm_poc python \
  lab/poc-scripts/compare_bpm_libraries.py \
  --song-id reachonemore_zai_ying_de_yi_ge_ling_hun__49a1c779
```

**Expected:** BeatNet BPM drops from 142.9 to **71.45** (142.9 / 2, which
lands in [65, 100]). Ratio to stored 78.3 = 0.91 — within the `octave_flag`
`≈1` tolerance (±10%). The `Octave*` column should show `≈1` instead of the
previous `1.8×`.

Note: 71.45 is still ~9% below the true 78.3. This is expected — the
octave guard resolves the octave-level ambiguity but not the CRNN's
fine-grained tempo bias. The guard's job is to land in the right octave,
not to be a perfect estimator.

### Test 2: No regression on default 3-song set

```bash
uv run --project lab/poc-scripts --extra bpm_poc python \
  lab/poc-scripts/compare_bpm_libraries.py
```

**Expected:** All three default songs (`yu_mi_man_bu_e46c5fe7`,
`mei_hao_de_chuang_zao_3d42d76e`, `song_zan_gui_yu_mi_d0e41287`) should
show unchanged BeatNet BPMs — none exceed 120 BPM, so the guard is a no-op.
madmom BPMs likewise unchanged.

### Test 3: Alternate BeatNet models on the failing song

```bash
# Model 1 (GTZAN, baseline with octave guard)
uv run --project lab/poc-scripts --extra bpm_poc python \
  lab/poc-scripts/compare_bpm_libraries.py \
  --song-id reachonemore_zai_ying_de_yi_ge_ling_hun__49a1c779 \
  --beatnet-model 1

# Model 2 (Ballroom)
uv run --project lab/poc-scripts --extra bpm_poc python \
  lab/poc-scripts/compare_bpm_libraries.py \
  --song-id reachonemore_zai_ying_de_yi_ge_ling_hun__49a1c779 \
  --beatnet-model 2

# Model 3 (Rock corpus)
uv run --project lab/poc-scripts --extra bpm_poc python \
  lab/poc-scripts/compare_bpm_libraries.py \
  --song-id reachonemore_zai_ying_de_yi_ge_ling_hun__49a1c779 \
  --beatnet-model 3
```

**Expected:** Three CSVs in `lab/poc-scripts/output/`. Compare the
`beatnet_bpm` column across the three runs:

- Does model 2 (Ballroom — slower repertoire) avoid the subdivision trap
  and report ~78 BPM natively (before octave guard)?
- Does model 3 (Rock corpus — more accurate beats) do better?
- Does the octave guard fire for all three, or only model 1?

This determines whether a different trained model is a viable alternative
to the post-hoc octave guard.

### Test 4: Full catalog sweep (optional, Phase 2)

```bash
uv run --project lab/poc-scripts --extra bpm_poc python \
  lab/poc-scripts/compare_bpm_libraries.py --all-catalog
```

**Expected:** No BeatNet BPM above 120 in the output (all halved into
[65, 100]). If any legitimate fast worship song exists in the catalog
(true tempo > 120), the guard would wrongly halve it — check the
`Octave*` column for any `×0.5` flags on stored BPMs > 120.

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Legitimate fast song (>120 BPM) wrongly halved | Low (worship catalog is predominantly 60–110 BPM) | The `Octave*` column in the per-song table flags any `×0.5` against stored BPM; the full-catalog run (Test 4) surfaces any such cases. The v4 guard has the same risk and has shipped without incident. |
| BeatNet model 2/3 weights missing or incompatible | Low (all three ship with the package per `BeatNet.py:73-78`) | If `torch.load` fails, the error surfaces immediately in Test 3; fall back to model 1. |
| Octave guard masks a deeper BeatNet accuracy problem | Medium (71.45 vs 78.3 is still 9% off) | The guard is an octave-level correction, not a tempo estimator. The alternate-model comparison (Test 3) investigates whether a different CRNN avoids the subdivision trap entirely. Fine-grained bias correction is out of scope for this POC. |
| madmom guard fires incorrectly on a song where madmom was right | Very low (madmom's RNN tracks the correct beat level for this song; the guard only fires when `bpm > 120`) | The `Octave*` column flags any halving; Test 2 confirms no regression on the default set. |

## Files Touched

| File | Change |
|---|---|
| `lab/poc-scripts/compare_bpm_libraries.py` | Add `halve_fast_bpm()` helper; apply to `timed_beatnet` + `timed_madmom`; rework `_get_beatnet_estimator` to cache per-model; add `--beatnet-model` CLI option; update module docstring |

No other files. No production analyzer changes. No dependency changes.

## Out of Scope

- Fixing prod-v5's 63.0 BPM under-detection on this song (separate issue:
  the CPS "slow" prior centers at 70, pulling the tempogram peak below the
  true 78.3).
- Fine-grained tempo bias correction for BeatNet (the octave guard lands
  at 71.45, still 9% below 78.3).
- Constraining the DBN's `min_bpm`/`max_bpm` directly (would require
  monkey-patching `DBNDownBeatTrackingProcessor` inside BeatNet's
  constructor — more principled but couples the script to BeatNet internals).
- Adopting BeatNet or madmom into the production analyzer (this is a POC).
