"""Deterministic DSP tests for run3's timing correction. Synthetic audio only.

The defect these lock down is measured on run2's `sa3__drum-a__s7003` fill: the
first drum hit sits at 17,920 samples (406 ms) and the next at 41,472 samples
(940 ms), with the incoming song's beat at 30,720 samples (696.6 ms). Onset
positions are supplied directly so librosa frame quantization can never make
these assertions depend on the detector.
"""

from __future__ import annotations

import numpy as np
import pytest

import gen_seam_fill_run3 as run3

SR = run3.SR
FILL = run3.FILL_SAMPLES
BEAT = 30_720  # one beat of the incoming song at 86.13281 BPM
FIRST = 17_920  # run2's measured first onset
SECOND = 41_472  # run2's measured second onset
BURST = 900  # samples of decaying noise per hit


def hit(length: int = BURST, amplitude: float = 0.8, seed: int = 0) -> np.ndarray:
    """A short decaying noise burst — a transient with a well-defined start."""
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(length).astype(np.float32)
    noise /= float(np.max(np.abs(noise)))
    envelope = np.exp(-np.arange(length, dtype=np.float32) / (length / 6.0))
    return (amplitude * envelope * noise).astype(np.float32)


def fill_with_hits(*starts: int) -> np.ndarray:
    fill = np.zeros((FILL, 2), dtype=np.float32)
    for index, start in enumerate(starts):
        mono = hit(seed=index)
        fill[start : start + mono.shape[0], 0] = mono
        fill[start : start + mono.shape[0], 1] = mono * 0.9
    return fill


def mono(samples: np.ndarray) -> np.ndarray:
    return np.abs(samples.mean(axis=1))


def peak_near(samples: np.ndarray, center: int, radius: int) -> tuple[int, float]:
    """Absolute peak position and value inside [center - radius, center + radius]."""
    window = mono(samples)[max(0, center - radius) : center + radius]
    offset = int(np.argmax(window))
    return max(0, center - radius) + offset, float(window[offset])


def test_trim_removes_leading_silence_and_keeps_inter_onset_distance() -> None:
    fill = fill_with_hits(FIRST, SECOND)
    corrected, meta = run3.correct_timing(fill, np.array([FIRST, SECOND]), BEAT, "trim")

    assert corrected.shape == (FILL, 2)
    assert corrected.dtype == np.float32
    # The first hit now starts at the first sample: the defect (406 ms of
    # near-silence) is gone, not merely faded.
    assert not np.any(fill[FIRST - 1]) and mono(corrected)[: int(0.02 * SR)].max() > 0.2
    assert meta == {
        "trimmed_samples": FIRST,
        "trimmed_seconds": pytest.approx(0.406349, abs=1e-6),
        "stretch_ratio": None,
    }

    # Trim never time-stretches: the second hit starts exactly where it did,
    # SECOND - FIRST samples after the first, sample for sample.
    offset = SECOND - FIRST
    assert np.array_equal(corrected[offset : offset + BURST], fill[SECOND : SECOND + BURST])
    assert not np.any(corrected[BURST:offset])
    assert mono(corrected)[offset : offset + int(0.02 * SR)].max() > 0.2

    # Everything added to reach 61,431 frames is silence at the end, and nothing
    # was inserted ahead of the first hit.
    assert not np.any(corrected[offset + BURST :])


def test_trim_pads_the_tail_only() -> None:
    fill = fill_with_hits(FIRST, SECOND)
    corrected, _ = run3.correct_timing(fill, np.array([FIRST, SECOND]), BEAT, "trim")
    # Source material after the first hit is 61,431 - 17,920 frames long; the
    # pad is therefore exactly the trimmed length difference.
    source = fill[FIRST:]
    assert corrected.shape[0] - source.shape[0] == FIRST
    assert np.array_equal(corrected[: source.shape[0]], source)


def test_grid_puts_beat_two_on_the_beat() -> None:
    fill = fill_with_hits(FIRST, SECOND)
    corrected, meta = run3.correct_timing(fill, np.array([FIRST, SECOND]), BEAT, "grid")

    assert corrected.shape == (FILL, 2)
    # No leading padding: the attack is still the first thing heard.
    assert mono(corrected)[:1024].max() > 0.1

    ratio = (SECOND - FIRST) / BEAT
    assert meta["trimmed_samples"] == FIRST
    assert meta["stretch_ratio"] == pytest.approx(ratio, abs=1e-6)
    assert meta["source_beat_two_samples"] == SECOND
    assert meta["target_beat_two_ms"] == pytest.approx(BEAT / SR * 1000.0, abs=1e-3)

    # The second hit lands on beat two. One analysis hop of tolerance: the phase
    # vocoder smears a transient by up to a window, it cannot move the beat.
    position, value = peak_near(corrected, BEAT, 2048)
    assert abs(position - BEAT) <= 1024
    assert value > 0.1
    # And it is a real transient: louder than the preceding 20 ms of fill.
    assert value > 5.0 * mono(corrected)[BEAT - 2048 : BEAT - 1024].max()


def test_trim_accepts_a_single_onset() -> None:
    fill = fill_with_hits(FIRST)
    corrected, meta = run3.correct_timing(fill, np.array([FIRST]), BEAT, "trim")
    assert corrected.shape == (FILL, 2)
    assert meta["trimmed_samples"] == FIRST
    assert mono(corrected)[: int(0.02 * SR)].max() > 0.2


def test_grid_refuses_to_invent_a_second_hit() -> None:
    fill = fill_with_hits(FIRST)
    with pytest.raises(ValueError, match="no second onset"):
        run3.correct_timing(fill, np.array([FIRST]), BEAT, "grid")


def test_grid_refuses_a_destructive_stretch() -> None:
    fill = fill_with_hits(FIRST, SECOND)
    # Half a beat: inside the search window, but a 0.5 stretch is a different
    # performance, so it must raise rather than silently ship a mangled fill.
    with pytest.raises(ValueError, match="time-stretch ratio"):
        run3.correct_timing(fill, np.array([FIRST, FIRST + BEAT // 2]), BEAT, "grid")


def test_no_onset_is_an_error_for_both_modes() -> None:
    fill = fill_with_hits(FIRST, SECOND)
    for mode in ("trim", "grid"):
        with pytest.raises(ValueError, match="no drum onset detected"):
            run3.correct_timing(fill, np.array([], dtype=np.int64), BEAT, mode)


def test_unknown_mode_is_rejected() -> None:
    fill = fill_with_hits(FIRST, SECOND)
    with pytest.raises(ValueError, match="unknown correction mode"):
        run3.correct_timing(fill, np.array([FIRST, SECOND]), BEAT, "stretch")
