"""Deterministic tests for the seam-inpaint generator. Synthetic audio only.

No database, Modal, or network: every input is generated here. The audio is
placed on the PCM16 grid (k / 32768) so a FLAC PCM_16 roundtrip is bit-exact,
which is what makes "the clip's context is the original recording, sample for
sample" a real assertion instead of a tolerance.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
import soundfile as sf

import gen_audio_inpaint as gap

SR = gap.SR
CONTEXT = gap.seconds_to_samples(gap.CLIP_CONTEXT_SECONDS)
SIDE = gap.seconds_to_samples(gap.SIDE_SECONDS)
FILL = 61_431  # the production 2-beat gap at 86.13281 BPM
BEAT = 30_720  # one beat of the incoming recording
FIRST = 17_920  # run2's measured first onset
SECOND = 41_472  # run2's measured second onset
BURST = 900  # samples of decaying noise per hit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def pcm16_grid(samples: np.ndarray) -> np.ndarray:
    """Snap to values a PCM16 file stores exactly (k / 32768)."""
    codes = np.clip(np.round(np.asarray(samples, dtype=np.float64) * 32768.0), -32767, 32767)
    return (codes / 32768.0).astype(np.float32)


def tone(seconds: float, frequency: float, amplitude: float = 0.2) -> np.ndarray:
    samples = gap.seconds_to_samples(seconds)
    time = np.arange(samples, dtype=np.float64) / SR
    mono = amplitude * np.sin(2.0 * np.pi * frequency * time)
    stereo = np.stack([mono, mono * 0.9], axis=1)
    return pcm16_grid(stereo)


def hit(length: int = BURST, amplitude: float = 0.8, seed: int = 0) -> np.ndarray:
    """A short decaying noise burst — a transient with a well-defined start."""
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(length).astype(np.float32)
    noise /= float(np.max(np.abs(noise)))
    envelope = np.exp(-np.arange(length, dtype=np.float32) / (length / 6.0))
    return (amplitude * envelope * noise).astype(np.float32)


def seam_at(
    a_end: float = 60.0,
    b_start: float = 5.0,
    duration: float = 120.0,
    bpm_left: int = 68,
    bpm_right: int = 86,
    key_left: str = "D Major",
    key_right: str = "C Major",
) -> gap.Seam:
    """A boundary-shaped seam over 120 s of synthetic, grid-exact audio."""
    left = tone(duration, 220.0)
    right = tone(duration, 330.0, amplitude=0.15)
    return gap.Seam(
        scope="boundary",
        label="a -> b",
        detail={
            "kind": "boundary",
            "left_song": "song-a",
            "right_song": "song-b",
            "left_key": key_left,
            "right_key": key_right,
            "bpm_left": bpm_left,
            "bpm_right": bpm_right,
            "recording_tempo_left": float(bpm_left),
            "recording_tempo_right": 86.13281,
            "production_gap_seconds": 1.393,
            "boundary_right_seconds": b_start,
            "gap_beats": 2,
        },
        left_audio=left,
        right_audio=right,
        a_arranged=gap.slice_window(left, 0.0, a_end),
        b_arranged=gap.slice_window(right, b_start, 120.0),
        a_post=gap.post_seam_audio(left, a_end, gap.HEAD_PAD_MAX_SECONDS),
        bpm_left=bpm_left,
        bpm_right=bpm_right,
        key_left=key_left,
        key_right=key_right,
    )


def polygon_hole(num_samples: int) -> np.ndarray:
    """A non-silent, grid-exact stand-in for generated audio."""
    samples = np.arange(num_samples, dtype=np.float64) / SR
    mono = 0.2 * np.sin(2.0 * np.pi * 261.63 * samples)
    return pcm16_grid(np.stack([mono, mono], axis=1))


def fill_with_hits(*starts: int) -> np.ndarray:
    fill = np.zeros((FILL, 2), dtype=np.float32)
    for index, start in enumerate(starts):
        burst = hit(seed=index)
        fill[start : start + burst.shape[0], 0] = burst
        fill[start : start + burst.shape[0], 1] = burst * 0.9
    return fill


def test_montage_carries_the_whole_arranged_audio() -> None:
    """The render plays non-adjacent sections; all of them reach the model."""
    left = tone(200.0, 220.0)
    right = tone(200.0, 330.0, amplitude=0.15)
    # Item 0's real shape: playback order is 99.69-130.55, 187.07-217.50, then
    # 11.85-40.55 — the seam sits at the *last array element's* end, 40.55 s into
    # the recording, not at the chronologically last section. Offsets scaled down
    # to fit these 200 s fixtures; contiguity and order are what matter here.
    a_steps = [(35.00, 66.00), (120.00, 150.00), (11.85, 40.55)]
    b_steps = [(0.70, 22.04), (160.00, 184.00)]
    seam = gap.Seam(
        scope="boundary",
        label="a -> b",
        detail={"kind": "boundary", "left_song": "a", "right_song": "b"},
        left_audio=left,
        right_audio=right,
        a_arranged=gap.arranged_audio(
            left, [{"startSeconds": s, "endSeconds": e} for s, e in a_steps], "a"
        ),
        a_post=gap.post_seam_audio(left, 40.55, gap.HEAD_PAD_MAX_SECONDS),
        b_arranged=gap.arranged_audio(
            right, [{"startSeconds": s, "endSeconds": e} for s, e in b_steps], "b"
        ),
        bpm_left=68,
        bpm_right=86,
        key_left="D Major",
        key_right="C Major",
    )
    arranged_a = sum(gap.seconds_to_samples(e - s) for s, e in a_steps)
    arranged_b = sum(gap.seconds_to_samples(e - s) for s, e in b_steps)
    assert seam.a_part().shape[0] == arranged_a
    assert seam.b_part().shape[0] == arranged_b

    montage = gap.build_montage(seam, 1.393)
    assert montage.hole_start == arranged_a
    assert montage.hole_samples == FILL
    assert montage.samples.shape[0] == arranged_a + FILL + arranged_b

    # Every step is present, in array order, sample for sample.
    offset = 0
    for start, end in a_steps:
        window = gap.slice_window(left, start, end)
        assert np.array_equal(montage.samples[offset : offset + window.shape[0]], window)
        offset += window.shape[0]
    offset = arranged_a + FILL
    for start, end in b_steps:
        window = gap.slice_window(right, start, end)
        assert np.array_equal(montage.samples[offset : offset + window.shape[0]], window)
        offset += window.shape[0]

    # The listening clip is the last/first 15 s of that arranged audio.
    assert np.array_equal(seam.a_clip_context(), seam.a_part()[-CONTEXT:])
    assert np.array_equal(seam.b_clip_context(), seam.b_part()[:CONTEXT])


# ---------------------------------------------------------------------------
# Reference
# ---------------------------------------------------------------------------


def test_reference_is_thirty_seconds_at_48k(tmp_path) -> None:
    seam = seam_at()
    reference = gap.build_reference(seam, "incoming")

    assert reference.shape == (gap.REFERENCE_SAMPLES, 2)
    assert reference.shape == (1_440_000, 2)
    assert reference.dtype == np.float32

    path = tmp_path / "reference.flac"
    gap.write_reference(path, reference)
    info = sf.info(str(path))
    assert info.frames == 1_440_000
    assert info.samplerate == 48_000
    assert info.channels == 2


def test_reference_orders_the_two_sides_by_alignment() -> None:
    """`align` picks which side contributes two of the three 10 s segments."""
    seam = seam_at(a_end=60.0, b_start=5.0)
    block = gap.REFERENCE_SAMPLES // 3
    # A is a 220 Hz tone, B a 330 Hz tone: the dominant frequency of each third
    # says which recording it came from.
    incoming = [dominant_hz(part) for part in _thirds(gap.build_reference(seam, "incoming"), block)]
    # Segment 1 is the outgoing arrangement's tail, 2 the incoming head, 3 spends
    # the last 10 s on the side the bridge is aligned to (its arrangement middle).
    assert incoming == [220, 330, 330]
    outgoing = [dominant_hz(part) for part in _thirds(gap.build_reference(seam, "outgoing"), block)]
    assert outgoing == [220, 330, 220]


def _thirds(samples: np.ndarray, block: int) -> list[np.ndarray]:
    return [samples[index * block : (index + 1) * block, 0] for index in range(3)]


def dominant_hz(mono: np.ndarray) -> int:
    spectrum = np.abs(np.fft.rfft(np.asarray(mono, dtype=np.float64)))
    frequencies = np.fft.rfftfreq(mono.shape[0], d=1.0 / gap.REFERENCE_SR)
    return int(round(float(frequencies[int(np.argmax(spectrum))])))


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


def test_request_is_one_pass_with_aligned_metadata() -> None:
    seam = seam_at()
    caption = "worship band bridge, no vocals"

    request = gap.build_request(seam, caption, "inst", 4242, 8.0, 24.0, "incoming", "base", 0)
    # The hole starts where the outgoing arranged audio ends (60 s in this fixture).
    assert request["repaint_start"] == 60.0
    assert request["repaint_end"] == 68.0
    assert request["bpm"] == 86
    assert request["keyscale"] == "C Major"
    assert request["inference_steps"] == 64
    assert request["guidance_scale"] == 8.0
    assert request["use_adg"] is True
    assert request["shift"] == 1.0
    assert request["repaint_mode"] == "conservative"
    assert request["repaint_strength"] == 1.0
    assert request["duration"] == 24.0
    assert request["instrumental"] is True
    assert request["lyrics"] == gap.INSTRUMENTAL_LYRICS
    assert request["vocal_language"] == "en"
    assert request["seed"] == 4242
    assert request["caption"] == caption

    outgoing = gap.build_request(seam, caption, "texture", 4242, 8.0, 24.0, "outgoing", "base", 0)
    assert outgoing["bpm"] == 68
    assert outgoing["keyscale"] == "D Major"
    assert outgoing["instrumental"] is False
    assert outgoing["lyrics"] == gap.TEXTURE_LYRICS
    # One mask over the whole hole, in both alignments: no half-hole split.
    assert outgoing["repaint_start"] == 60.0
    assert outgoing["repaint_end"] == 68.0

    gap_request = gap.build_request(seam, caption, "inst", 4242, 1.393, 17.393, "incoming", "base", 0)
    assert gap_request["repaint_start"] == 60.0
    assert gap_request["repaint_end"] == 61.393
    assert gap_request["duration"] == 17.393
    assert gap_request["bpm"] == 86
    assert gap_request["keyscale"] == "C Major"

    turbo = gap.build_request(seam, caption, "inst", 4242, 8.0, 24.0, "incoming", "turbo", 0)
    assert turbo["inference_steps"] == 8
    assert turbo["use_adg"] is False
    assert turbo["shift"] == 3.0

    overridden = gap.build_request(seam, caption, "inst", 4242, 8.0, 24.0, "incoming", "base", 32)
    assert overridden["inference_steps"] == 32


def test_sa3_request_has_exactly_its_six_keys() -> None:
    seam = seam_at()
    caption = "TrackType: Instrument, drum kit"

    assert gap.build_request(seam, caption, "inst", 4242, 8.0, 24.0, "incoming", "sa3", 0) == {
        "prompt": caption,
        "seed": 4242,
        "steps": 8,
        "start_seconds": 60.0,
        "end_seconds": 68.0,
        "duration_seconds": 24.0,
    }
    gap_request = gap.build_request(seam, caption, "inst", 4242, 1.393, 17.393, "incoming", "sa3", 0)
    assert gap_request == {
        "prompt": caption,
        "seed": 4242,
        "steps": 8,
        "start_seconds": 60.0,
        "end_seconds": 61.393,
        "duration_seconds": 17.393,
    }
    for leaked in ("bpm", "keyscale", "instrumental", "repaint_start", "repaint_end", "lyrics"):
        assert leaked not in gap_request


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def test_assemble_keeps_real_context_sample_identical(tmp_path) -> None:
    seam = seam_at()
    montage = gap.build_montage(seam, 8)
    hole = polygon_hole(montage.hole_samples)
    record = {"id": "take1", "variant_id": "variant1", "length_seconds": 8, "align": "incoming"}

    returned = gap.assemble(record, seam, montage, hole, tmp_path, gap.HOLE_FADE_SECONDS)

    clip_path = tmp_path / "clips" / "take1.flac"
    bridge_path = tmp_path / "bridges" / "take1.flac"
    clip = gap.read_wav(clip_path)
    bridge = gap.read_wav(bridge_path)

    overlap = gap.seconds_to_samples(gap.HOLE_OVERLAP_SECONDS)
    assert clip.shape[0] == 2 * CONTEXT + montage.hole_samples - overlap
    assert record["metrics"]["clip_duration_seconds"] == pytest.approx(37.5, abs=1e-3)
    assert record["metrics"]["hole_overlap_seconds"] == pytest.approx(0.5, abs=1e-3)

    fade = gap.seconds_to_samples(gap.HOLE_FADE_SECONDS)
    # The real context is the arranged audio, sample for sample, no gain.
    assert np.array_equal(clip[: CONTEXT - fade], seam.a_clip_context()[:-fade])
    hole_start = CONTEXT
    hole_end = hole_start + montage.hole_samples
    # The hole's last 0.5 s was handed to B, so B resumes at the overlap mark.
    assert np.array_equal(clip[hole_end + fade :], seam.b_clip_context()[overlap + fade :])

    # The hole is real generated audio, and the bridge file is exactly that region.
    assert gap.peak_level(returned) > 0.05
    assert np.array_equal(bridge, clip[hole_start:hole_end])
    assert returned.shape[0] == montage.hole_samples
    assert record["metrics"]["hole_samples"] == montage.hole_samples
    assert record["metrics"]["peak_limit"] == "none"


def test_assemble_a_two_beat_gap_is_61431_frames(tmp_path) -> None:
    seam = seam_at()
    montage = gap.build_montage(seam, 1.393)
    assert montage.hole_samples == FILL
    hole = polygon_hole(FILL)
    record = {"id": "tgap", "variant_id": "tgap", "length_seconds": 1.393, "align": "incoming"}

    returned = gap.assemble(
        record, seam, montage, hole, tmp_path, gap.GAP_FADE_SECONDS, gap=True
    )

    clip = gap.read_wav(tmp_path / "clips" / "tgap.flac")
    bridge = gap.read_wav(tmp_path / "bridges" / "tgap.flac")
    assert returned.shape[0] == FILL
    assert bridge.shape[0] == FILL
    # Gap mode never hands over: B enters exactly at the end of the 1.393 s gap.
    assert clip.shape[0] == 2 * CONTEXT + FILL
    assert record["metrics"]["clip_duration_seconds"] == pytest.approx(31.393, abs=1e-3)
    assert record["metrics"]["hole_overlap_seconds"] == 0.0
    # 5 ms fades keep the first attack; the context is still untouched.
    fade = gap.seconds_to_samples(gap.GAP_FADE_SECONDS)
    assert np.array_equal(clip[: CONTEXT - fade], seam.a_clip_context()[:-fade])
    assert np.array_equal(clip[-CONTEXT + fade :], seam.b_clip_context()[fade:])


def test_assemble_measures_the_joins(tmp_path) -> None:
    seam = seam_at()
    montage = gap.build_montage(seam, 8)
    # A hole far quieter than the context: the level staging must lift it onto
    # the seam's target and the join jump must reflect what was written.
    hole = polygon_hole(montage.hole_samples) * 0.2
    record = {"id": "quiet", "variant_id": "quiet", "length_seconds": 8, "align": "incoming"}

    gap.assemble(record, seam, montage, hole, tmp_path, gap.HOLE_FADE_SECONDS)
    metrics = record["metrics"]

    target = 0.5 * (metrics["a_level_db"] + metrics["b_level_db"])
    assert metrics["hole_target_mean_db"] == pytest.approx(target, abs=0.05)
    # Staging anchors each end of the hole to the 200 ms of real audio beside it,
    # so the joins are level-continuous even though the hole arrived ~14 dB low.
    assert 5.0 < metrics["hole_edge_gain_db"]["start"] < gap.EDGE_GAIN_LIMIT_DB
    # The staging objective itself: both ends land on the adjacent real audio.
    assert abs(metrics["staged_edge_error_db"]["start"]) < 0.5
    assert abs(metrics["staged_edge_error_db"]["end"]) < 0.5
    assert abs(metrics["join_rms_jump_db"]["left"]) < 4.0
    assert metrics["requested_bpm"] == 86
    assert metrics["requested_key"] == "C Major"
    assert set(metrics["join_rms_jump_db"]) == {"left", "right"}
    assert set(metrics["join_centroid_jump_hz"]) == {"left", "right"}
    assert set(metrics["context_centroid_hz"]) == {"a", "b"}
    for key in ("stereo_corr_hole", "chroma_corr_a", "chroma_corr_b", "tempo_bpm_hole"):
        assert key in metrics
    json.dumps(record["metrics"])  # every value is JSON-serialisable (non-finite -> null)


def test_a_decaying_hole_does_not_drop_out_at_the_join(tmp_path) -> None:
    """Take 1 of the arranged-audio run: the bridge's own tail sat 22 dB down.

    A 4 s-mean gain staging left a 16 dB step into B. The staging must anchor the
    joins instead, and the handoff must replace the tail with B's real audio.
    """
    seam = seam_at()
    montage = gap.build_montage(seam, 8)
    hole = polygon_hole(montage.hole_samples)
    decay = np.linspace(1.0, 0.03, hole.shape[0], dtype=np.float32)[:, None]
    record = {"id": "decay", "variant_id": "decay", "length_seconds": 8, "align": "incoming"}

    gap.assemble(record, seam, montage, hole * decay, tmp_path, gap.HOLE_FADE_SECONDS)
    metrics = record["metrics"]

    assert metrics["hole_edge_gain_db"]["end"] > 10.0  # the decay is compensated
    # Without the handoff the staging alone still leaves a step into B ...
    assert abs(metrics["raw_join_rms_jump_db"]["right"]) > 3.0
    # ... the 0.5 s overlap removes it (the window spans the crossfade, so a
    # residual dB or two is the equal-power mix, not a dropout).
    assert abs(metrics["join_rms_jump_db"]["right"]) < 3.0
    # The start end lands within half a dB (the dB ramp reaches its end value at
    # the last sample, while the window stops one fade short). The tail needs
    # 24.9 dB and EDGE_GAIN_LIMIT_DB caps it at 18, so a residual step remains —
    # far below the 7.7 dB the raw splice would have had.
    assert abs(metrics["staged_edge_error_db"]["start"]) < 0.5
    assert 0.0 < metrics["staged_edge_error_db"]["end"] <= gap.EDGE_GAIN_LIMIT_DB

    clip = gap.read_wav(tmp_path / "clips" / "decay.flac")
    overlap = gap.seconds_to_samples(gap.HOLE_OVERLAP_SECONDS)
    hole_end = CONTEXT + montage.hole_samples
    # After the hole region B's audio resumes at the overlap mark, sample for
    # sample (the 40 ms de-click sits inside the last 40 ms of the hole region).
    fade = gap.seconds_to_samples(gap.HOLE_FADE_SECONDS)
    assert np.array_equal(clip[hole_end + fade :], seam.b_clip_context()[overlap + fade :])


# ---------------------------------------------------------------------------
# Gap timing correction
# ---------------------------------------------------------------------------


def test_gap_trim_removes_the_lead_in() -> None:
    fill = fill_with_hits(FIRST, SECOND)
    corrected, meta = gap.correct_gap_timing(
        fill, np.array([FIRST, SECOND]), BEAT, FILL, "trim"
    )

    assert corrected.shape == (FILL, 2)
    assert corrected.dtype == np.float32
    values = np.abs(corrected.mean(axis=1))
    assert not np.any(fill[FIRST - 1]) and values[: int(0.02 * SR)].max() > 0.2
    assert meta["trimmed_samples"] == FIRST
    assert meta["trimmed_seconds"] == pytest.approx(0.406349, abs=1e-6)
    assert meta["stretch_ratio"] is None

    # Trim never time-stretches: the second hit is unchanged, sample for sample.
    offset = SECOND - FIRST
    assert np.array_equal(corrected[offset : offset + BURST], fill[SECOND : SECOND + BURST])
    assert not np.any(corrected[BURST:offset])
    # Everything added to reach 61,431 frames is silence at the end.
    assert not np.any(corrected[offset + BURST :])
    assert corrected.shape[0] - fill[FIRST:].shape[0] == FIRST


def test_gap_grid_puts_beat_two_on_the_beat() -> None:
    fill = fill_with_hits(FIRST, SECOND)
    corrected, meta = gap.correct_gap_timing(
        fill, np.array([FIRST, SECOND]), BEAT, FILL, "grid"
    )

    assert corrected.shape == (FILL, 2)
    values = np.abs(corrected.mean(axis=1))
    assert values[:1024].max() > 0.1  # no leading padding
    assert meta["stretch_ratio"] == pytest.approx((SECOND - FIRST) / BEAT, abs=1e-6)

    window = values[BEAT - 2048 : BEAT + 2048]
    position = BEAT - 2048 + int(np.argmax(window))
    assert abs(position - BEAT) <= 1024
    assert float(window.max()) > 0.1


def test_gap_correction_refuses_to_invent_a_fill() -> None:
    fill = fill_with_hits(FIRST, SECOND)
    for mode in ("trim", "grid"):
        with pytest.raises(ValueError, match="no drum onset detected"):
            gap.correct_gap_timing(fill, np.array([], dtype=np.int64), BEAT, FILL, mode)
    with pytest.raises(ValueError, match="no second onset"):
        gap.correct_gap_timing(fill, np.array([FIRST]), BEAT, FILL, "grid")
    with pytest.raises(ValueError, match="time-stretch ratio"):
        gap.correct_gap_timing(fill, np.array([FIRST, FIRST + BEAT // 2]), BEAT, FILL, "grid")
    with pytest.raises(ValueError, match="unknown correction mode"):
        gap.correct_gap_timing(fill, np.array([FIRST, SECOND]), BEAT, FILL, "stretch")


def test_gap_timing_metrics_report_the_run2_defect() -> None:
    raw = gap.timing_metrics(fill_with_hits(FIRST, SECOND), BEAT)
    # run2's `sa3__drum-a__s7003` numbers, reproduced from the detector.
    assert raw["first_onset_ms"] == pytest.approx(406.35, abs=1.0)
    assert raw["second_onset_ms"] == pytest.approx(940.41, abs=1.0)
    assert raw["leading_silence_ms"] == raw["first_onset_ms"]
    assert raw["beat_two_target_ms"] == pytest.approx(BEAT / SR * 1000.0, abs=1e-3)
    assert raw["beat_two_error_ms"] == pytest.approx(940.41 - 696.599, abs=1.0)

    corrected, _ = gap.correct_gap_timing(
        fill_with_hits(FIRST, SECOND), np.array([FIRST, SECOND]), BEAT, FILL, "trim"
    )
    last_material = SECOND - FIRST + BURST - 1
    assert gap.trailing_silence_ms(corrected) == round(
        (FILL - 1 - last_material) / SR * 1000.0, 2
    )
    assert gap.trailing_silence_ms(np.zeros((FILL, 2), dtype=np.float32)) == round(
        FILL / SR * 1000.0, 2
    )

    # A hit already sitting on beat two: the measured energy peak is where the beat is.
    on_beat = gap.timing_metrics(fill_with_hits(BEAT), BEAT)
    assert on_beat["beat_two_peak_ms"] == pytest.approx(BEAT / SR * 1000.0, abs=20.0)
    assert on_beat["beat_two_error_ms"] is None  # one onset only: no second hit invented
    assert on_beat["second_onset_ms"] is None


# ---------------------------------------------------------------------------
# Gap planning and captions
# ---------------------------------------------------------------------------


def test_gap_plan_covers_two_styles_and_every_take() -> None:
    takes = gap.plan_gap_takes(_args(takes=3, engine="base"))
    assert len(takes) == 6
    assert [take.style for take in takes] == ["drum-fill"] * 3 + ["drum-immediate"] * 3
    for index, take in enumerate(takes):
        style = "drum-fill" if index < 3 else "drum-immediate"
        seed = 4242 + index % 3
        assert take.id == f"gap__{style}__incoming__base__s{seed}"
        assert take.seed == seed
        assert take.take == index % 3
        assert take.caption_key == f"gap/{style}"

    sa3 = gap.plan_gap_takes(_args(takes=1, engine="sa3"))
    assert [take.id for take in sa3] == [
        "gap__drum-fill__incoming__sa3__s4242",
        "gap__drum-immediate__incoming__sa3__s4242",
    ]


def _args(**overrides):
    class Args:
        songset = "songset"
        out = "out"
        mode = "gap"
        engine = "base"
        align = "incoming"
        takes = 3
        steps = 0
        seed = 4242

    for key, value in overrides.items():
        setattr(Args, key, value)
    return Args()


def test_gap_captions_are_fixed_per_engine_family(tmp_path) -> None:
    facts = "seam facts"
    base = gap.load_captions(tmp_path, facts, False, mode="gap", engine="base")
    assert base["source"] == "fixed"
    assert base["engine"] == "base"
    assert {key: value["caption"] for key, value in base["captions"].items()} == gap.gap_captions(
        "base"
    )
    assert json.loads((tmp_path / "captions.json").read_text()) == base

    sa3 = gap.load_captions(tmp_path, facts, False, mode="gap", engine="sa3")
    assert sa3["engine"] == "sa3"
    assert {key: value["caption"] for key, value in sa3["captions"].items()} == gap.gap_captions(
        "sa3"
    )
    assert all("TrackType:" in value["caption"] for value in sa3["captions"].values())

    # Switching engines back rewrites the two prompts without an LLM call.
    again = gap.load_captions(tmp_path, facts, False, mode="gap", engine="base")
    assert again["engine"] == "base"
    assert {key: value["caption"] for key, value in again["captions"].items()} == gap.gap_captions(
        "base"
    )


def test_bridge_caption_keys_are_one_per_variant() -> None:
    keys = gap.caption_keys()
    assert len(keys) == 9
    assert set(keys) == set(gap.FALLBACK_CAPTIONS)
    for arc in gap.ARCS:
        for treatment in gap.TREATMENTS:
            assert f"boundary/{arc}/{treatment}" in keys
        assert f"intra/{arc}/inst" in keys
    assert not any(key.endswith(("/left", "/right")) for key in keys)


def test_variant_ids_and_caption_keys_are_side_free() -> None:
    variant = gap.Variant(
        id="boundary__keep-intensity__8s__inst",
        scope="boundary",
        arc="keep-intensity",
        length_seconds=8,
        treatment="inst",
    )
    assert variant.caption_key() == "boundary/keep-intensity/inst"
    assert gap.take_id_for(variant, "incoming", "base", 4242) == (
        "boundary__keep-intensity__8s__inst__incoming__base__s4242"
    )


def test_detect_onsets_does_not_split_one_attack() -> None:
    """librosa returns two hits 23 ms apart for a single sharp attack.

    Measured on the 15 s-context ACE-Step fill: onsets at 23.22 and 46.44 ms while
    the accent sat on the beat at 696.53 ms. The split hit became "the second
    onset" and reported a -650 ms beat-two error on a fill that was in time.
    """
    fill = np.zeros((FILL, 2), dtype=np.float32)
    spacing = gap.seconds_to_samples(0.023)
    for index, start in enumerate((FIRST, FIRST + spacing)):
        burst = hit(seed=index)
        fill[start : start + burst.shape[0], 0] = burst
        fill[start : start + burst.shape[0], 1] = burst * 0.9

    onsets = gap.detect_onsets(fill)

    assert onsets.size == 1
    assert int(onsets[0]) == pytest.approx(FIRST, abs=1024)


def test_a_dead_head_is_covered_with_a_real_continuation(tmp_path: Path) -> None:
    """SA3 opens pad bridges from silence; A's recording continues past the seam.

    Measured on the 15 s-context arranged run: every pad bridge began 19-37 dB
    under the surrounding music, far outside what the 18 dB edge ramp may lift
    without amplifying noise.
    """
    seam = seam_at()
    montage = gap.build_montage(seam, 8.0)
    hole = polygon_hole(montage.hole_samples)
    dead = gap.seconds_to_samples(0.5)
    hole[:dead] = 0.0  # the model's fade-in
    record = {"id": "thead", "variant_id": "thead", "length_seconds": 8, "align": "incoming"}

    gap.assemble(record, seam, montage, hole, tmp_path, gap.HOLE_FADE_SECONDS)

    metrics = record["metrics"]
    assert metrics["hole_head_pad_seconds"] >= 0.5
    clip = gap.read_wav(tmp_path / "clips" / "thead.flac")
    hole_start = gap.seconds_to_samples(gap.CLIP_CONTEXT_SECONDS)
    # The borrowed head is A's own audio, sample for sample half a second in.
    pad = gap.seconds_to_samples(metrics["hole_head_pad_seconds"])
    cross = gap.seconds_to_samples(gap.HEAD_PAD_FADE_SECONDS)
    fade = gap.seconds_to_samples(gap.HOLE_FADE_SECONDS)
    borrowed = clip[hole_start + fade : hole_start + pad - cross]
    expected = seam.a_head_pad(pad)[fade : pad - cross]
    assert np.array_equal(borrowed, expected)
    # ... so the join no longer steps and no dead stretch is left at the head.
    assert abs(metrics["join_rms_jump_db"]["left"]) < 3.0
    assert metrics["hole_dead_edge_seconds"]["start"] == 0.0


def test_a_gap_fill_that_cannot_be_staged_is_refused(tmp_path: Path) -> None:
    """A fill 21 dB under the gap's surroundings is a dropout, not a fill."""
    seam = seam_at()
    montage = gap.build_montage(seam, 1.393)
    record = {"id": "tquiet", "variant_id": "tquiet", "length_seconds": 1.393}

    hole = polygon_hole(montage.hole_samples) * 0.02  # ~34 dB down
    with pytest.raises(RuntimeError, match="cannot be staged onto the gap"):
        gap.assemble(
            record, seam, montage, hole, tmp_path, gap.GAP_FADE_SECONDS, gap=True
        )
    assert not (tmp_path / "clips" / "tquiet.flac").exists()


def test_a_gap_fill_with_dead_air_before_the_downbeat_is_refused(tmp_path: Path) -> None:
    """run3's defect: the fill stops early and B's downbeat arrives after silence.

    Measured on the base gap set, `grid`'s stretch plus zero padding left 68-90 ms
    of digital silence; `raw` left 0-2 ms and `trim` 46 ms.
    """
    seam = seam_at()
    montage = gap.build_montage(seam, 1.393)
    record = {"id": "tdead", "variant_id": "tdead", "length_seconds": 1.393}
    hole = polygon_hole(montage.hole_samples)
    hole[-gap.seconds_to_samples(0.09) :] = 0.0

    with pytest.raises(RuntimeError, match="dead air before the incoming downbeat"):
        gap.assemble(record, seam, montage, hole, tmp_path, gap.GAP_FADE_SECONDS, gap=True)
    assert not (tmp_path / "clips" / "tdead.flac").exists()
