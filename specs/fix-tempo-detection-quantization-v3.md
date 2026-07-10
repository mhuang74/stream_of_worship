# Fix Tempo Detection Quantization v3 (symmetric double-time octave guard)

## Overview

Song "我在這裡 — 當祢走進我們當中" (`863331f713b5`) was detected at **136 BPM** by a fast-tier (librosa) Analyze job, but its true tempo is ~70 BPM. The cause is an **asymmetric octave guard**: the v2 fix (`fix-tempo-detection-quantization-v2.md`) only corrects **half-time** errors (`tempo_primary < 70` → re-estimate at `start_bpm=120`, accept if `≈ 2×primary`). It has no correction for the opposite **double-time** error, where a slow song's onset-strength envelope peaks at twice the beat rate (common with eighth-/sixteenth-note patterns, hi-hats, arpeggiated synths) and librosa reports ~2× the true tempo.

This plan adds a **symmetric double-time guard** to `_compute_tempo()` so that suspiciously fast primary estimates (>120 BPM) are re-evaluated with a low prior (`start_bpm=60`) and replaced with the half-time candidate when it lands in the worship-plausible range (65–100 BPM).

| | |
|---|---|
| **Date** | 2026-07-05 |
| **Status** | Plan v3 — pending implementation |
| **Components** | `ops/analysis-service/` |
| **Breaking** | None to the API surface; additive octave guard only. Cached fast-analysis results produced by the buggy guard remain stale until force re-analyzed. |
| **Re-analysis required** | Yes — re-run fast analysis with `--force` for affected songs (minimum: `863331f713b5`; recommended: full catalog to catch other latent double-time errors) |
| **Depends on** | v2 fix already deployed (`hop_length=512`, `start_bpm=80`, half-time guard at analyzer.py:424) |

---

## 1. Root Cause

### 1.1 Symptom

```
sow_admin audio list | grep 863331f713b5
│ 我在這裡 │ 當祢走進我們當中 │ ... │ 7:55 │ F major │ 136 │ dang_mi_zou_jin_wo_men_dang_zhong_3975a746 │ ... │ 863331f713b5 │
```

Reported tempo = 136 BPM; true tempo ≈ 70 BPM. Ratio ≈ 1.94× → classic octave-doubling error.

### 1.2 Why the v2 guard did not fire

`ops/analysis-service/src/sow_analysis/workers/analyzer.py:424`:

```python
if tempo_primary < 70.0:                    # ← only fires for HALF-time errors
    tempo_alt = librosa.beat.tempo(..., start_bpm=120.0)
    if abs(tempo_alt - 2.0 * tempo_primary) < 8.0:
        return tempo_alt
```

Since `136 > 70`, the branch was skipped and 136 was returned verbatim. The guard is symmetric in concept but only implements one direction.

### 1.3 Why librosa doubles the tempo

With `start_bpm=80` (worship prior) and a song whose onset envelope has strong energy at both ~70 BPM (true beat) and ~140 BPM (sub-beat level from eighth-note hi-hat / arpeggio patterns), the log-normal prior centered at 80 can still collapse onto the 140 peak when the sub-beat energy dominates the autocorrelation. The result is `tempo_primary ≈ 136`. Re-centering the prior at 60 BPM (`start_bpm=60`) biases the estimator toward the slower peak and recovers ~70 BPM.

---

## 2. Design Decisions

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | Trigger threshold | `tempo_primary > 120.0` | Worship songs rarely exceed ~110 BPM; 120 cleanly separates plausible-fast from likely-double-time. Matches the spirit of the existing `< 70` half-time threshold. |
| D2 | Re-estimate prior | `start_bpm=60.0` | Centers the log-normal prior on the half-time region so the autocorrelation prefers the ~65–75 BPM peak. |
| D3 | Acceptance criteria | `abs(tempo_alt - tempo_primary / 2.0) < 8.0` **AND** `65 <= tempo_alt <= 100` | Octave relationship + worship-range plausibility. The range gate prevents false-halving of genuinely fast songs (e.g. a true 140 BPM song re-estimated at 70 would be admitted, but such songs are vanishingly rare in this catalog; the range upper bound of 100 excludes implausible half-time values like 50). |
| D4 | Guard ordering | `if >120 ... elif <70 ...` (mutually exclusive) | A single tempo cannot be both >120 and <70; `elif` makes the exclusivity explicit and avoids a redundant second `librosa.beat.tempo` call. |
| D5 | Hardcoded thresholds (not API options) | Same as v2's `< 70` and `start_bpm=120` are hardcoded | Keeps surface minimal; the existing `start_bpm` FastAnalyzeOptions field already lets callers retune the primary estimate. Threshold tuning tracked as future work if needed. |
| D6 | Full-tier (allin1) path | No change | User confirmed this song went through the fast tier. allin1's `result.bpm` (analyzer.py:292) is out of scope; revisit only if a full-tier song exhibits the same symptom. |
| D7 | Cache invalidation | `force=True` on re-analysis | Stale `{hash_prefix}_fast.json` cache files contain the wrong (doubled) tempo; must bypass. |
| D8 | Re-analysis scope | Recommended: full catalog via `audio batch --analyze --analysis-tier fast --force` | The guard is additive — it cannot change songs already correctly detected (its branch only fires >120). A full re-run also catches any other latent double-time errors silently present in the catalog. |

---

## 3. Implementation Plan

### Phase A: Add double-time guard to `_compute_tempo()`

**File**: `ops/analysis-service/src/sow_analysis/workers/analyzer.py` (lines 406–440)

Replace the existing guard block with a symmetric pair:

```python
def _compute_tempo() -> float:
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)

    # Primary estimate with worship-music prior
    tempo_primary = librosa.beat.tempo(
        onset_envelope=onset_env,
        sr=sr,
        hop_length=hop_length,
        start_bpm=start_bpm,
    )
    if hasattr(tempo_primary, "__iter__"):
        tempo_primary = float(tempo_primary[0])
    tempo_primary = float(tempo_primary)

    # Double-time guard: if primary is suspiciously fast, re-estimate with a
    # 60 BPM prior to probe the half-time peak. Handles slow worship songs
    # (~65-75 BPM true) whose onset envelope peaks at twice the beat rate
    # (eighth-/sixteenth-note patterns) and is reported at ~2x true tempo.
    if tempo_primary > 120.0:
        tempo_alt = librosa.beat.tempo(
            onset_envelope=onset_env,
            sr=sr,
            hop_length=hop_length,
            start_bpm=60.0,
        )
        if hasattr(tempo_alt, "__iter__"):
            tempo_alt = float(tempo_alt[0])
        tempo_alt = float(tempo_alt)

        # Accept the half-time if it is roughly half the primary AND lands in
        # the worship-plausible range (65-100 BPM).
        if (
            abs(tempo_alt - tempo_primary / 2.0) < 8.0
            and 65.0 <= tempo_alt <= 100.0
        ):
            return tempo_alt

    # Half-time guard (v2): if primary is suspiciously slow, re-estimate with
    # the 120 BPM prior to probe the double-time peak. Handles edge-case fast
    # songs (true tempo > 100 BPM) without over-correcting the catalog.
    elif tempo_primary < 70.0:
        tempo_alt = librosa.beat.tempo(
            onset_envelope=onset_env,
            sr=sr,
            hop_length=hop_length,
            start_bpm=120.0,
        )
        if hasattr(tempo_alt, "__iter__"):
            tempo_alt = float(tempo_alt[0])
        tempo_alt = float(tempo_alt)

        if abs(tempo_alt - 2.0 * tempo_primary) < 8.0:
            return tempo_alt

    return tempo_primary
```

Key changes:
- New `if tempo_primary > 120.0` block at the top of the guard (D1, D2).
- Existing `if` becomes `elif` to enforce mutual exclusivity (D4).
- Double-time acceptance uses octave-match **plus** the 65–100 range gate (D3).

### Phase B: Re-analyze affected recordings

```bash
# Ensure analysis service runs the new code
cd ops/analysis-service && docker compose up -d

# Re-analyze the known-affected song
uv run --project ops/admin-cli --extra admin sow-admin audio batch \
    --analyze --analysis-tier fast --force \
    --song-id dang_mi_zou_jin_wo_men_dang_zhong_3975a746
```

(Recommended full-catalog sweep to catch latent double-time errors):

```bash
uv run --project ops/admin-cli --extra admin sow-admin audio batch \
    --analyze --analysis-tier fast --force --limit 500
```

The `--force` flag bypasses stale `{hash_prefix}_fast.json` cache files (D7). Because the new guard only fires when `tempo_primary > 120`, correctly-detected songs are unaffected — the re-run is safe and idempotent.

### Phase C: Verify

After re-analysis, confirm:

```bash
sow_admin audio list | grep 863331f713b5
# Expect BPM ≈ 68-72 (no longer 136)
```

---

## 4. Tests

**File**: `ops/analysis-service/tests/test_analyzer.py` (extend existing `TestAnalyzeAudioFastTempoParams`)

Mirror the existing half-time guard tests for the new double-time direction:

```python
@patch("sow_analysis.workers.analyzer.compute_loudness")
@patch("sow_analysis.workers.analyzer.detect_key")
@patch("sow_analysis.workers.analyzer.librosa")
@pytest.mark.asyncio
async def test_double_time_guard_selects_half_time(
    self, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
):
    """When primary > 120 and alt ≈ primary/2 in worship range, return alt."""
    mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
    mock_librosa.get_duration.return_value = 3.0
    mock_librosa.onset.onset_strength.return_value = np.zeros(258)
    mock_librosa.beat.tempo.side_effect = [
        np.array([136.0]),  # primary with start_bpm=80
        np.array([70.0]),   # alt with start_bpm=60
    ]
    mock_detect_key.return_value = _stub_key_result()
    mock_compute_loudness.return_value = -20.0

    cache_manager = MagicMock(); cache_manager.get_fast_analyze_result.return_value = None
    audio_path = tmp_path / "audio.mp3"; audio_path.write_text("dummy")

    result = await analyze_audio_fast(audio_path, cache_manager, "abc123")

    assert result["tempo_bpm"] == 70.0
    assert mock_librosa.beat.tempo.call_count == 2


@patch("sow_analysis.workers.analyzer.librosa")
@pytest.mark.asyncio
async def test_double_time_guard_ignores_non_half_time(self, mock_librosa, tmp_path):
    """When alt is not ≈ primary/2, keep primary (137 vs 90)."""
    mock_librosa.beat.tempo.side_effect = [
        np.array([137.0]), np.array([90.0]),
    ]
    # ... assert result["tempo_bpm"] == 137.0, call_count == 2


@patch("sow_analysis.workers.analyzer.librosa")
@pytest.mark.asyncio
async def test_double_time_guard_rejects_half_time_outside_worship_range(
    self, mock_librosa, tmp_path
):
    """When alt ≈ primary/2 but outside 65-100 range, keep primary (140 vs 50)."""
    mock_librosa.beat.tempo.side_effect = [
        np.array([140.0]), np.array([50.0]),
    ]
    # ... assert result["tempo_bpm"] == 140.0, call_count == 2


@patch("sow_analysis.workers.analyzer.librosa")
@pytest.mark.asyncio
async def test_double_time_guard_not_triggered_at_or_below_120(self, mock_librosa, tmp_path):
    """When primary <= 120, no second tempo call is made."""
    mock_librosa.beat.tempo.return_value = np.array([110.0])
    # ... assert result["tempo_bpm"] == 110.0, call_count == 1
```

Existing half-time guard tests (`test_octave_guard_selects_double_time`, `test_octave_guard_ignores_non_double_time`) remain green because the `> 120` branch does not fire for their 65-BPM primaries (they take the `elif < 70` path with identical behavior).

---

## 5. Files Changed

| File | Change |
|---|---|
| `ops/analysis-service/src/sow_analysis/workers/analyzer.py` | Add double-time guard (`tempo_primary > 120` → re-estimate at `start_bpm=60`); convert existing half-time guard from `if` to `elif`. |
| `ops/analysis-service/tests/test_analyzer.py` | Add 4 double-time guard tests mirroring the existing half-time tests. |

No changes to: `models.py` (no new options), `queue.py` (no new params), admin CLI (uses existing `submit_fast_analysis`), webapp (BPM bands unaffected — slow songs are already in the correct band).

---

## 6. Out of Scope

- **Full-tier (allin1) path** (analyzer.py:292 `bpm = result.bpm`): user confirmed fast tier. Revisit only if a full-tier song exhibits double-time symptoms.
- **Tempogram multi-peak analysis**: more robust multi-peak disambiguation considered in v2 (Strategy I) and rejected due to librosa bugs; not revisited here.
- **Tunability of thresholds** (`> 120`, `start_bpm=60`, range `65–100`): kept hardcoded for parity with the v2 half-time guard. Promote to `FastAnalyzeOptions` only if a non-worship use case emerges.

---

## 7. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Genuinely fast worship song (>120 BPM true) gets halved to ~60–70 | Single false correction per affected song | Acceptance requires alt ∈ [65,100]; the only implausible halving would be true ~140 → 70 (within range). Such songs are extremely rare in this worship catalog. If one surfaces, it can be re-analyzed with `start_bpm=120` to force the fast interpretation. |
| Stale cache returns old 136 BPM | Re-analysis ineffective | `--force` flag bypasses cache; documented in Phase B. |
| Guard adds a second `librosa.beat.tempo` call for fast-detected songs | ~0.1–0.5s extra per affected song | Negligible vs total fast-analysis time (~10–15s). Only fires when primary > 120 (a minority of songs). |
| Existing half-time tests regress | CI failure | The `if`→`elif` change is behavior-preserving for `< 70` inputs since the `> 120` branch is unreachable at 65 BPM primary. Existing tests assert `call_count == 2` and specific return values — both unchanged. |

---

## 8. Changelog from v2 → v3

1. Added symmetric double-time octave guard (D1–D3): `tempo_primary > 120` → re-estimate at `start_bpm=60`, accept half-time if `≈ primary/2` AND in worship range 65–100.
2. Converted half-time guard from `if` to `elif` (D4) for explicit mutual exclusivity.
3. Added 4 mirroring unit tests in `test_analyzer.py`.
4. Scoped to fast tier only; allin1 path explicitly out of scope (D6), reversing v2's "unaffected" hand-wave only insofar as a future full-tier double-time case would need its own post-hoc guard.
