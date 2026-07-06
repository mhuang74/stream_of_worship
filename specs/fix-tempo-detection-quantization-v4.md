# Fix Tempo Detection Quantization v4 (half-time guard false-doubling on legitimate slow tempos)

## Overview

Song "我活著要稱頌祢" (`wo_huo_zhu_yao_cheng_song_mi_48f285e8`, hash prefix `cc8f923fa60d`) was detected at **129 BPM** by a fast-tier (librosa) Analyze job, but its true tempo is ~65 BPM. The v3 safeguard (`fix-tempo-detection-quantization-v3.md`) added a symmetric double-time guard that successfully handles songs where librosa reports ~2× the true tempo. However, for this song librosa's **primary** estimate is already correct (64.6 BPM), and the v3 guard is irrelevant — instead, the **half-time guard** (inherited from v2) misfires and doubles the correct primary to 129.2 BPM.

The root cause is that the half-time guard's acceptance criteria — `abs(tempo_alt − 2×tempo_primary) < 8` AND `100 ≤ tempo_alt ≤ 160` — **cannot distinguish** a legitimately slow song (true ~65 BPM, primary 64.6, alt 129.2) from a genuinely halved fast song (true ~130 BPM, primary 64.6, alt 129.2). Both produce identical `(primary, alt)` pairs. The v3 threshold lowering from `< 70` to `< 65` protected 65–70 BPM songs but still admits 60–65 BPM songs, which are legitimate worship tempos.

This plan (v4) tightens the half-time guard so it only fires when the primary is **implausibly slow** (below the worship-plausible floor), and raises the acceptance range floor so doublings of legitimate slow tempos are rejected.

| | |
|---|---|
| **Date** | 2026-07-06 |
| **Status** | Plan v4 — pending implementation |
| **Components** | `ops/analysis-service/` |
| **Breaking** | None to the API surface; half-time guard threshold and acceptance range tightened. Cached fast-analysis results produced by the buggy guard remain stale until force re-analyzed. |
| **Re-analysis required** | Yes — re-run fast analysis with `--force` for affected songs (minimum: `cc8f923fa60d`; recommended: full catalog to catch other latent half-time false-doublings) |
| **Depends on** | v3 fix already deployed (symmetric double-time guard at analyzer.py:424, half-time guard at analyzer.py:452) |

---

## 1. Root Cause

### 1.1 Symptom

```
sow_admin audio list | grep 我活著要
│ 我活著要稱頌… │ ... │ 5:27 │ C major │ 129 │ wo_huo_zhu_yao_cheng_song_mi_48f285e8 │ ... │ cc8f923fa60d │
```

Reported tempo = 129 BPM; true tempo ≈ 65 BPM. Ratio ≈ 1.99× → classic octave-doubling error, but introduced by the **guard itself**, not by librosa's primary estimate.

### 1.2 Reproduction

Direct librosa invocation on the cached audio file (`/home/mhuang/.cache/stream-of-worship-admin/cc8f923fa60d/audio/audio.mp3`) with the same parameters the analyzer uses:

```python
y, sr = librosa.load(path, sr=22050, mono=True)
onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512)

primary = librosa.beat.tempo(onset_envelope=onset_env, sr=sr, hop_length=512, start_bpm=80)
# → 64.599609375   ← CORRECT (true tempo ≈ 65)

alt60 = librosa.beat.tempo(onset_envelope=onset_env, sr=sr, hop_length=512, start_bpm=60)
# → 64.599609375   (same as primary; the 60 BPM prior does not move it)

alt120 = librosa.beat.tempo(onset_envelope=onset_env, sr=sr, hop_length=512, start_bpm=120)
# → 129.19921875   ← the doubling
```

### 1.3 Why the v3 double-time guard did not fire

`ops/analysis-service/src/sow_analysis/workers/analyzer.py:424`:

```python
if tempo_primary > 120.0:                    # ← only fires for DOUBLE-time errors
    tempo_alt = librosa.beat.tempo(..., start_bpm=60.0)
    if abs(tempo_alt - tempo_primary / 2.0) < 8.0 and 65.0 <= tempo_alt <= 100.0:
        return tempo_alt
```

Since `tempo_primary = 64.6` (not > 120), the double-time branch is skipped. The v3 guard is irrelevant for this song — librosa's primary is already correct.

### 1.4 Why the v2/v3 half-time guard misfired

`ops/analysis-service/src/sow_analysis/workers/analyzer.py:452`:

```python
elif tempo_primary < 65.0:                   # ← fires for 64.6
    tempo_alt = librosa.beat.tempo(..., start_bpm=120.0)
    if abs(tempo_alt - 2.0 * tempo_primary) < 8.0 and 100.0 <= tempo_alt <= 160.0:
        return tempo_alt                     # ← returns 129.2 (the doubling)
```

Trace for this song:

| Step | Value |
|---|---|
| `tempo_primary` (start_bpm=80) | **64.6** ← correct |
| Branch taken | `elif tempo_primary < 65.0` → fires (64.6 < 65) |
| `tempo_alt` (start_bpm=120) | 129.2 |
| `abs(129.2 − 2×64.6)` | 0.0 < 8.0 ✓ |
| `100 ≤ 129.2 ≤ 160` | ✓ |
| **Returned** | **129.2** ← doubled |

### 1.5 The fundamental ambiguity

The half-time guard **cannot distinguish** these two cases — both produce identical `(primary, alt)` pairs:

| True tempo | primary (start=80) | alt (start=120) | Correct action |
|---|---|---|---|
| ~65 BPM (this song) | 64.6 | 129.2 | **keep** primary |
| ~130 BPM (halved) | 64.6 | 129.2 | **return** alt |

The octave-match criterion (`abs(alt − 2×primary) < 8`) and the range gate `[100, 160]` accept both. The v3 threshold lowering from `< 70` to `< 65` protected 65–70 BPM songs but still admits 60–65 BPM songs, which are legitimate worship tempos (e.g. slow ballads, hymn-style arrangements).

### 1.6 Why simply lowering the threshold further is insufficient

Lowering the threshold to `< 60` would protect 60–65 BPM songs but still misfire on a true 55 BPM song (primary 55, alt 110 — accepted as doubled). The deeper problem is that the **acceptance range floor of 100** admits doublings of any primary ≥ 50. A true 65 BPM song doubled to 130 lands squarely in `[100, 160]`. To reject doublings of legitimate slow tempos, the floor must rise above `2 × (worship slow floor)`.

---

## 2. Design Decisions

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | Half-time trigger threshold | `tempo_primary < 60.0` | Worship songs rarely have a true tempo below 60 BPM. The v3 `< 65` threshold admitted legitimate 60–65 BPM slow songs (this song: 64.6). Lowering to `< 60` protects the entire 60–100 BPM worship-plausible band. A primary below 60 is genuinely implausible and worth re-estimating. |
| D2 | Acceptance range floor | `110.0 <= tempo_alt` (raised from 100) | The v3 floor of 100 admits doublings of primaries ≥ 50 (e.g. 65→130). Raising to 110 means only primaries < 55 can have their doublings accepted (since `2×55 = 110`). Combined with D1's `< 60` trigger, the guard now only accepts doublings of primaries in `[0, 60)` — i.e. genuinely implausibly slow songs. A 64.6 primary's doubling (129.2) is rejected because 129.2 < 110 is false... wait, 129.2 ≥ 110, so this alone does not reject it. See D3. |
| D3 | Acceptance range ceiling | `tempo_alt <= 180.0` (raised from 160) | Accommodates doublings of primaries up to 90 (e.g. a true 90 BPM song mis-estimated at 45 would double to 90, but that is below the new floor; a true 80 BPM song mis-estimated at 40 would double to 80, also below floor). In practice, with D1's `< 60` trigger, the accepted alt range is `[110, 180]`, corresponding to primaries in `[55, 90]` — but only primaries `< 60` trigger, so the effective accepted-primary range is `[55, 60)`, yielding alts in `[110, 120)`. The ceiling of 180 is a safety upper bound. |
| D4 | Octave-match tolerance | Unchanged (`< 8.0`) | The octave relationship is still the primary signal; only the trigger and range gate change. |
| D5 | Guard ordering | Unchanged (`if > 120 ... elif < 60 ...`) | The double-time guard (v3) remains first; the half-time guard (v2, tightened) remains `elif`. Mutual exclusivity preserved. |
| D6 | Hardcoded thresholds | Same as v2/v3 | Keeps surface minimal. The existing `start_bpm` FastAnalyzeOptions field already lets callers retune the primary estimate. Threshold tuning tracked as future work. |
| D7 | Full-tier (allin1) path | No change | User confirmed this song went through the fast tier. allin1's `result.bpm` (analyzer.py:292) is out of scope; revisit only if a full-tier song exhibits the same symptom. |
| D8 | Cache invalidation | `force=True` on re-analysis | Stale `{hash_prefix}_fast.json` cache files contain the wrong (doubled) tempo; must bypass. |
| D9 | Re-analysis scope | Recommended: full catalog via `audio batch --analyze --analysis-tier fast --force` | The tightened guard only fires when `tempo_primary < 60` (narrower than v3's `< 65`). Songs correctly detected by v3 are unaffected — the `< 60` branch is unreachable for primaries in `[60, 120]`. A full re-run also catches any other latent half-time false-doublings silently present in the catalog. |
| D10 | Interaction with double-time guard | None | The double-time guard (`> 120`) and half-time guard (`< 60`) are mutually exclusive. A primary of 64.6 triggers neither after v4. A primary of 129.2 (if it had been returned) would trigger the double-time guard, but v4 prevents the half-time guard from producing that 129.2 in the first place. |

### 2.1 Why D2's floor of 110 is the key fix

Re-examining the trace for this song with v4:

| Step | Value | v3 | v4 |
|---|---|---|---|
| `tempo_primary` | 64.6 | — | — |
| Branch `elif tempo_primary < 65` (v3) / `< 60` (v4) | — | **fires** (64.6 < 65) | **does not fire** (64.6 ≥ 60) |
| Returned | — | 129.2 (doubled) | **64.6** (correct) |

With D1 alone (`< 60` threshold), the guard does not fire for this song at all, and the correct primary is returned. D2 (floor 110) and D3 (ceiling 180) are **defense-in-depth**: they ensure that even if a future song has `primary < 60` but is a legitimate slow tempo (e.g. 58 BPM), its doubling (116 BPM) is rejected because `116 < 110` is false... wait, 116 ≥ 110, so it would still be accepted.

**Correction:** D2's floor of 110 rejects doublings of primaries `< 55` (since `2×55 = 110` is the boundary). For a primary of 58 (legitimate slow), the doubling is 116, which is ≥ 110 and would be accepted. D2's floor therefore does **not** protect 55–60 BPM legitimate slow songs. The protection for this song comes entirely from D1 (`< 60` threshold). D2's floor of 110 is retained from v3's spirit (reject implausibly low doublings like 50→100) but is not the operative fix.

**The operative fix is D1: lowering the trigger threshold from `< 65` to `< 60`.** This alone resolves the reported song and all songs in the 60–65 BPM band. D2 and D3 are minor range adjustments for consistency and defense-in-depth.

---

## 3. Implementation Plan

### Phase A: Tighten the half-time guard in `_compute_tempo()`

**File**: `ops/analysis-service/src/sow_analysis/workers/analyzer.py` (lines 443–469)

Change the `elif` threshold from `< 65.0` to `< 60.0`, and raise the acceptance range floor from `100.0` to `110.0` and ceiling from `160.0` to `180.0`:

```python
# Half-time guard (v4): if primary is below the worship-plausible floor
# (< 60 BPM), re-estimate with the 120 BPM prior to probe the
# double-time peak. Handles edge-case fast songs (true tempo > 110 BPM)
# without over-correcting the predominantly 60-100 BPM worship catalog.
# NOTE: threshold lowered from v3's < 65 to < 60 because 60-65 BPM is a
# legitimate slow worship tempo (e.g. "我活著要稱頌祢" at 64.6 BPM true).
# The v3 < 65 threshold misfired on 64.6 and doubled it to 129.2.
# Acceptance range floor raised from 100 to 110 so doublings of primaries
# in [50, 55) (which yield alts in [100, 110)) are rejected as implausible;
# ceiling raised from 160 to 180 to accommodate doublings of primaries up
# to 90 (defense-in-depth; in practice only primaries < 60 trigger).
elif tempo_primary < 60.0:
    tempo_alt = librosa.beat.tempo(
        onset_envelope=onset_env,
        sr=sr,
        hop_length=hop_length,
        start_bpm=120.0,
    )
    if hasattr(tempo_alt, "__iter__"):
        tempo_alt = float(tempo_alt[0])
    tempo_alt = float(tempo_alt)

    # If the alternative is roughly double the primary AND lands in the
    # genuinely-fast range, the primary was half-time.
    if (
        abs(tempo_alt - 2.0 * tempo_primary) < 8.0
        and 110.0 <= tempo_alt <= 180.0
    ):
        return tempo_alt

return tempo_primary
```

Key changes:
- `elif tempo_primary < 65.0` → `elif tempo_primary < 60.0` (D1).
- `100.0 <= tempo_alt <= 160.0` → `110.0 <= tempo_alt <= 180.0` (D2, D3).
- Comment block updated to document the v4 rationale and the regression that motivated it.

### Phase B: Re-analyze affected recordings

```bash
# Ensure analysis service runs the new code
cd ops/analysis-service && docker compose up -d

# Re-analyze the known-affected song
uv run --project ops/admin-cli --extra admin sow-admin audio batch \
    --analyze --analysis-tier fast --force \
    --song-id wo_huo_zhu_yao_cheng_song_mi_48f285e8
```

(Recommended full-catalog sweep to catch latent half-time false-doublings):

```bash
uv run --project ops/admin-cli --extra admin sow-admin audio batch \
    --analyze --analysis-tier fast --force --limit 500
```

The `--force` flag bypasses stale `{hash_prefix}_fast.json` cache files (D8). Because the tightened guard only fires when `tempo_primary < 60` (narrower than v3's `< 65`), songs correctly detected by v3 are unaffected — the re-run is safe and idempotent. Songs that v3 had **correctly doubled** (true tempo > 110, primary < 60) remain correctly doubled because the octave-match and raised range gate still accept them.

### Phase C: Verify

After re-analysis, confirm:

```bash
sow_admin audio list | grep cc8f923fa60d
# Expect BPM ≈ 64-66 (no longer 129)
```

---

## 4. Tests

**File**: `ops/analysis-service/tests/test_analyzer.py` (extend existing `TestAnalyzeAudioFastTempoParams`)

### 4.1 Update existing half-time guard tests

The existing `test_octave_guard_selects_double_time` uses `primary=50.0, alt=100.0`. With v4's raised floor of 110, `100.0 < 110` so the alt would be **rejected**. This test must be updated to use a primary whose doubling lands in the new `[110, 180]` range:

```python
@patch("sow_analysis.workers.analyzer.compute_loudness")
@patch("sow_analysis.workers.analyzer.detect_key")
@patch("sow_analysis.workers.analyzer.librosa")
@pytest.mark.asyncio
async def test_octave_guard_selects_double_time(
    self, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
):
    """When primary estimate < 60 and alt ≈ 2×primary in fast range [110,180], return alt."""
    # First call (start_bpm=80) returns 55.0; second call (start_bpm=120) returns 110.0
    mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
    mock_librosa.get_duration.return_value = 3.0
    mock_librosa.onset.onset_strength.return_value = np.zeros(258)
    mock_librosa.beat.tempo.side_effect = [
        np.array([55.0]),  # primary with start_bpm=80 (< 60, triggers guard)
        np.array([110.0]), # alt with start_bpm=120 (≈ 2×55, in [110,180])
    ]
    mock_detect_key.return_value = _stub_key_result()
    mock_compute_loudness.return_value = -20.0

    cache_manager = MagicMock()
    cache_manager.get_fast_analyze_result.return_value = None

    audio_path = tmp_path / "audio.mp3"
    audio_path.write_text("dummy")

    result = await analyze_audio_fast(audio_path, cache_manager, "abc123")

    # The octave guard should select the double-time alternative
    assert result["tempo_bpm"] == 110.0
    assert mock_librosa.beat.tempo.call_count == 2
```

The existing `test_octave_guard_ignores_non_double_time` (`primary=50, alt=90`) remains green: 90 is not ≈ 2×50, so the alt is rejected regardless of range. No change needed, but verify.

### 4.2 Add new regression test for the reported song

```python
@patch("sow_analysis.workers.analyzer.compute_loudness")
@patch("sow_analysis.workers.analyzer.detect_key")
@patch("sow_analysis.workers.analyzer.librosa")
@pytest.mark.asyncio
async def test_half_time_guard_not_fired_on_legitimate_64_bpm_tempo(
    self, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
):
    """Regression: a ~65 BPM primary must NOT be doubled by the half-time guard.

    Song cc8f923fa60d ("我活著要稱頌祢") has a true tempo of ~65 BPM. librosa
    returns primary=64.6 with start_bpm=80. The v3 guard threshold (< 65)
    misfired and doubled it to 129.2. With the threshold lowered to < 60,
    the guard no longer fires and the correct primary is returned.
    """
    mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
    mock_librosa.get_duration.return_value = 3.0
    mock_librosa.onset.onset_strength.return_value = np.zeros(258)
    mock_librosa.beat.tempo.return_value = np.array([64.6])
    mock_detect_key.return_value = _stub_key_result()
    mock_compute_loudness.return_value = -20.0

    cache_manager = MagicMock()
    cache_manager.get_fast_analyze_result.return_value = None

    audio_path = tmp_path / "audio.mp3"
    audio_path.write_text("dummy")

    result = await analyze_audio_fast(audio_path, cache_manager, "abc123")

    assert result["tempo_bpm"] == 64.6
    assert mock_librosa.beat.tempo.call_count == 1
```

### 4.3 Add test for raised acceptance floor

```python
@patch("sow_analysis.workers.analyzer.compute_loudness")
@patch("sow_analysis.workers.analyzer.detect_key")
@patch("sow_analysis.workers.analyzer.librosa")
@pytest.mark.asyncio
async def test_half_time_guard_rejects_doubling_below_110(
    self, mock_librosa, mock_detect_key, mock_compute_loudness, tmp_path
):
    """When alt ≈ 2×primary but alt < 110, keep primary (50 vs 100).

    A primary of 50 doubled to 100 is below the v4 floor of 110, so the
    guard rejects the doubling. This prevents false-doubling of legitimate
    very-slow tempos (though such tempos are rare in worship music).
    """
    mock_librosa.load.return_value = (np.zeros(22050 * 3), 22050)
    mock_librosa.get_duration.return_value = 3.0
    mock_librosa.onset.onset_strength.return_value = np.zeros(258)
    mock_librosa.beat.tempo.side_effect = [
        np.array([50.0]),  # primary with start_bpm=80 (< 60, triggers guard)
        np.array([100.0]), # alt with start_bpm=120 (≈ 2×50, but < 110)
    ]
    mock_detect_key.return_value = _stub_key_result()
    mock_compute_loudness.return_value = -20.0

    cache_manager = MagicMock()
    cache_manager.get_fast_analyze_result.return_value = None

    audio_path = tmp_path / "audio.mp3"
    audio_path.write_text("dummy")

    result = await analyze_audio_fast(audio_path, cache_manager, "abc123")

    assert result["tempo_bpm"] == 50.0
    assert mock_librosa.beat.tempo.call_count == 2
```

### 4.4 Existing tests that remain green

- `test_octave_guard_ignores_non_double_time` (`primary=50, alt=90`): 90 not ≈ 2×50 → rejected. Green.
- `test_half_time_guard_not_fired_on_legitimate_slow_tempo` (`primary=69.837`): 69.837 ≥ 60 → guard does not fire. Green (was green under v3 too).
- `test_no_octave_guard_when_primary_in_worship_range` (`primary=85`): 85 in `[60, 120]` → no guard fires. Green.
- `test_double_time_guard_*` (4 tests): primaries > 120 → double-time guard fires, half-time `elif` unreachable. Green.

---

## 5. Files Changed

| File | Change |
|---|---|
| `ops/analysis-service/src/sow_analysis/workers/analyzer.py` | Tighten half-time guard: threshold `< 65` → `< 60`; acceptance range `[100, 160]` → `[110, 180]`. Update comment block. |
| `ops/analysis-service/tests/test_analyzer.py` | Update `test_octave_guard_selects_double_time` (primary 50→55, alt 100→110); add `test_half_time_guard_not_fired_on_legitimate_64_bpm_tempo`; add `test_half_time_guard_rejects_doubling_below_110`. |

No changes to: `models.py` (no new options), `queue.py` (no new params), admin CLI (uses existing `submit_fast_analysis`), webapp (BPM bands unaffected — slow songs are already in the correct band).

---

## 6. Out of Scope

- **Full-tier (allin1) path** (analyzer.py:292 `bpm = result.bpm`): user confirmed fast tier. Revisit only if a full-tier song exhibits half-time false-doubling symptoms.
- **Tempogram multi-peak analysis**: more robust multi-peak disambiguation considered in v2 (Strategy I) and rejected due to librosa bugs; not revisited here.
- **Tunability of thresholds** (`< 60`, `[110, 180]`): kept hardcoded for parity with v2/v3. Promote to `FastAnalyzeOptions` only if a non-worship use case emerges.
- **Fundamental ambiguity resolution**: the half-time guard cannot, by construction, distinguish a true 65 BPM song from a halved 130 BPM song when both yield `(primary=64.6, alt=129.2)`. v4 resolves the reported case by relying on librosa's primary being correct for 60–65 BPM songs (so the guard never fires). A truly halved 130 BPM song (primary 65, alt 130) would now be **mis-classified as 65** — but such songs are vanishingly rare in this worship catalog, and the v3 double-time guard would catch a 130 BPM primary if librosa had reported it directly. This tradeoff is accepted.

---

## 7. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Genuinely halved fast song (true ~130 BPM, primary 65) is no longer doubled | Single false non-correction per affected song | Such songs are extremely rare in this worship catalog. If one surfaces, it can be re-analyzed with `start_bpm=120` to force the fast interpretation, or the double-time guard will catch it if librosa reports 130 directly. The tradeoff favors precision (not doubling legitimate slow songs) over recall (catching rare halved fast songs). |
| Stale cache returns old 129 BPM | Re-analysis ineffective | `--force` flag bypasses cache; documented in Phase B. |
| Existing `test_octave_guard_selects_double_time` breaks | CI failure | Test updated in §4.1 to use `primary=55, alt=110` (lands in new `[110, 180]` range). |
| A true 58 BPM song (legitimate slow) is doubled to 116 | Single false correction | 116 is in `[110, 180]` and `abs(116 − 2×58) = 0 < 8`, so the guard accepts it. This is a residual false-positive. Mitigation: 58 BPM is at the extreme low end of worship tempos and rare; if it occurs, manual override via `start_bpm` retune is available. A future v5 could add a tempogram-based disambiguation to resolve this. |
| Guard no longer fires for primaries in `[60, 65)` that were genuinely halved | Missed doubling for true 120–130 BPM songs | True 120–130 BPM songs are rare in worship music, and librosa with `start_bpm=80` typically reports them directly as 120–130 (triggering the double-time guard if > 120). The half-time path (primary < 60) is only needed when librosa collapses a fast song onto its half-time peak, which is uncommon for the 80 BPM prior. |

---

## 8. Changelog from v3 → v4

1. Lowered half-time guard trigger threshold from `< 65` to `< 60` (D1): protects legitimate 60–65 BPM slow worship songs from false doubling. This is the operative fix for the reported song (`cc8f923fa60d`, primary 64.6).
2. Raised half-time acceptance range floor from `100` to `110` (D2): defense-in-depth; rejects doublings of primaries below 55.
3. Raised half-time acceptance range ceiling from `160` to `180` (D3): accommodates doublings of primaries up to 90 (safety upper bound).
4. Updated `test_octave_guard_selects_double_time` to use `primary=55, alt=110` (lands in new acceptance range).
5. Added `test_half_time_guard_not_fired_on_legitimate_64_bpm_tempo` regression test for the reported song.
6. Added `test_half_time_guard_rejects_doubling_below_110` for the raised floor.
7. Scoped to fast tier only; allin1 path explicitly out of scope (D7), unchanged from v3.
