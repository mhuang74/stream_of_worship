from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
import json
import shutil
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from sow_render_worker.arrangements import ArrangementStep
from sow_render_worker.audio_engine import GlueClip, SongsetItem, generate_songset_audio
from sow_render_worker.chapters import generate_chapters_manifest
from sow_render_worker.db import RenderJob
from sow_render_worker.glue_descriptor import build_boundary_descriptor
from sow_render_worker.glue_jobs import _rebuild_junction, _StaleJunction
from sow_render_worker.glue_provider import build_boundary_compose_request
from sow_render_worker.glue_staging import ReferenceAudio, prepare_staged_take
from sow_render_worker.pipeline import arranged_opening_timing
from sow_render_worker.video_engine import RESOLUTION_MAP, VideoEngine


RATE = 44100
ARRANGEMENT = {"version": 1, "steps": [{
    "sectionIndex": 0, "label": "Verse", "startSeconds": 0, "endSeconds": 12,
    "manualBounds": True,
}]}


@pytest.mark.parametrize("target", ["intro", "outro"])
def test_worker_checks_current_direction_arrangement_and_settings(target):
    stamp = datetime(2026, 11, 28, 12, tzinfo=timezone.utc)
    item = {
        "id": "song", "updated_at": stamp, "recording_hash_prefix": "a",
        "recording_content_hash": "a", "tempo_bpm": 120, "duration_seconds": 12,
        "arrangement_steps": json.dumps(ARRANGEMENT),
        "sections": '[{"label":"Verse","start":0,"end":12}]',
        "beats": "[0,0.5,1,11.5]",
        "boundary_settings": {target: {"enabled": True, "beats": 8, "revision": "saved"}},
    }
    job = SimpleNamespace(
        target=target, from_item_id="song", expected_from_updated_at=stamp,
        boundary_revision="saved",
    )
    rebuilt = _rebuild_junction([item], job)
    assert rebuilt.descriptor["target"] == target
    assert rebuilt.descriptor["recording"] == {"contentHash": "a", "steps": [[0, 12]]}
    if target == "intro":
        assert rebuilt.descriptor_key == "899c1e9d93e7e2442e7c5fadb5007a526fdf74f037e1f3fd1699a5fafaeda814"
    neighbor = {**item, "id": "neighbor"}
    moved = [neighbor, item] if target == "intro" else [item, neighbor]
    with pytest.raises(_StaleJunction):
        _rebuild_junction(moved, job)
    with pytest.raises(_StaleJunction):
        _rebuild_junction([{**item, "arrangement_steps": None}], job)
    item["boundary_settings"][target]["revision"] = "edited"
    with pytest.raises(_StaleJunction):
        _rebuild_junction([item], job)


def pcm(seconds, amplitude, frequency):
    samples = np.sin(2 * np.pi * frequency * np.arange(round(seconds * RATE)) / RATE)
    return np.repeat((samples * amplitude).astype("<i2")[:, None], 2, axis=1)


@pytest.mark.parametrize("target", ["intro", "outro"])
@pytest.mark.parametrize("backend", ["runpod", "modal", "elevenlabs"])
def test_staging_keeps_exactly_the_generated_interval(target, backend):
    descriptor, _, _ = build_boundary_descriptor(
        target=target, beats=2, tempo_bpm=120, recording={"contentHash": "a", "steps": [[0, 12]]}
    )
    fill = pcm(1, 4096, 440)
    ref = pcm(4, 8192, 330)
    empty = np.zeros((2 * RATE, 2), dtype="<i2")
    if backend == "elevenlabs":
        request = build_boundary_compose_request(
            target=target, seed=1, duration_ms=1000, song_id="reference", reference_ms=4000
        )
        chunks = request["composition_plan"]["chunks"]
        gen = chunks[0 if target == "intro" else 1]
        retained = chunks[1 if target == "intro" else 0]
        assert gen["duration_ms"] == 3000
        assert retained["song_id"] == "reference"
        raw = (np.concatenate((empty, fill, ref)) if target == "intro"
               else np.concatenate((ref, fill, empty)))
        route = {"provider": backend}
    else:
        a, b = (empty, ref) if target == "intro" else (ref, empty)
        raw = np.concatenate((a, fill, b))
        route = {"provider": backend, "a_frames": len(a), "b_frames": len(b), "fill_frames": len(fill)}
    take = prepare_staged_take(
        raw.tobytes(), job=SimpleNamespace(provider_context_json=route), descriptor=descriptor,
        items={}, reference=ReferenceAudio(a_seconds=4, b_seconds=4),
    )
    staged, _ = sf.read(BytesIO(take.wav_bytes), dtype="int16")
    assert take.seconds == 1
    assert len(staged) == RATE
    np.testing.assert_allclose(staged[RATE // 4:RATE // 2], fill[RATE // 4:RATE // 2], atol=1)


def decode(path, *args):
    return subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path), *args, "-"])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
@pytest.mark.parametrize(("card", "intro_on", "lyrics", "bpm"), [
    (False, False, True, 137),
    (True, False, True, None),
    (True, True, True, 120),
    (False, True, False, 120),
])
def test_mixed_onsets_held_video_frames_and_chapters(tmp_path, monkeypatch, card, intro_on, lyrics, bpm):
    song = tmp_path / "song.wav"
    intro_path = tmp_path / "intro.wav"
    outro_path = tmp_path / "outro.wav"
    for path, seconds, hz in [(song, 4, 220), (intro_path, 1, 440), (outro_path, 1, 660)]:
        sf.write(path, pcm(seconds, 6000, hz), RATE)
    lrc = "[00:00.00]\n[00:01.50]Opening lyric\n[00:02.50]Closing lyric\n" if lyrics else None
    assets = SimpleNamespace(download_audio=lambda _: str(song), download_lrc=lambda _: lrc)
    item = SongsetItem(
        "item", "set", "song", song_title="Song", recording_hash_prefix="a", tempo_bpm=bpm,
        arrangement_steps=(ArrangementStep(0, "Verse", 0, 4),),
    )
    job = RenderJob("job", "set", 1, "running", include_title_card=card, first_lyrics_hold=True)
    intro = GlueClip(str(intro_path), "intro", 1, "") if intro_on else None
    outro = GlueClip(str(outro_path), "outro", 1, "")
    hold, silence = arranged_opening_timing(job, item, intro)
    assert hold == (6 if card else 0)
    assert silence == (0 if intro_on else 240 / (bpm or 70))
    assert arranged_opening_timing(replace(job, video_enabled=False), item, intro) == (None, 0)

    audio_path = tmp_path / "mixed.mp3"
    audio = generate_songset_audio(
        [item], str(audio_path), assets, normalize=False, intro=intro, outro=outro,
        lead_in_resolver=lambda _: hold, opening_silence_seconds=silence, first_lyrics_hold_start=hold,
    )
    recorded_start = hold + silence + (1 if intro_on else 0)
    assert audio.segments[0].start_time_seconds == pytest.approx(recorded_start)
    assert audio.outro_start == pytest.approx(recorded_start + 4)
    assert audio.total_duration_seconds == pytest.approx(recorded_start + 5)
    # MP3 encoding can leak a few samples across the onset. The decoded audio
    # must be effectively silent until the instrumental or recording starts.
    samples = np.frombuffer(decode(audio_path, "-ac", "1", "-ar", str(RATE), "-f", "s16le"), dtype="<i2")
    onset = hold if intro_on else recorded_start
    if onset:
        assert np.max(np.abs(samples[:round((onset - 0.02) * RATE)]), initial=0) < 150
    assert np.max(np.abs(samples[round((onset + 0.02) * RATE):round((onset + 0.1) * RATE)])) > 1000
    manifest = generate_chapters_manifest(list(audio.segments), assets.download_lrc, audio.total_duration_seconds)
    (chapter,) = manifest.chapters
    assert chapter.start_seconds == pytest.approx(recorded_start)
    assert chapter.end_seconds == pytest.approx(recorded_start + 4)
    assert [line.start_seconds for line in chapter.lines if line.text] == (
        pytest.approx([recorded_start + 1.5, recorded_start + 2.5]) if lyrics else []
    )

    monkeypatch.setitem(RESOLUTION_MAP, "test", (320, 180))
    engine = VideoEngine(assets, resolution="test", fps=12, include_title_card=card)
    video = tmp_path / "video.mp4"
    engine.generate_video(
        str(audio_path), list(audio.segments), str(video), first_lyrics_hold_start=hold,
        outro_start=audio.outro_start,
    )
    frames = np.frombuffer(decode(video, "-an", "-f", "rawvideo", "-pix_fmt", "rgb24"), dtype=np.uint8)
    frames = frames.reshape(-1, 180, 320, 3)
    opening = frames[round((hold + 0.25) * 12)].astype(float)
    held = frames[round((recorded_start + 0.75) * 12)].astype(float)
    ending = frames[round((audio.outro_start + 0.25) * 12)].astype(float)
    assert np.abs(opening - held).mean() < 2
    if lyrics:
        assert np.abs(opening - ending).mean() > 0.4
        closing = frames[round((recorded_start + 3.5) * 12)].astype(float)
        assert np.abs(closing - ending).mean() < 2
    else:
        assert np.abs(opening - ending).mean() < 0.1
    if card:
        blank = frames[round((hold - 0.5) * 12)]
        assert np.max(np.ptp(blank, axis=(0, 1))) < 3
        assert np.ptp(frames[0]) > 60
    if not card and not intro_on:
        audio_only = tmp_path / "audio-only.mp3"
        standalone = generate_songset_audio([item], str(audio_only), assets, normalize=False, outro=outro)
        assert standalone.segments[0].start_time_seconds == 0
        standalone_samples = np.frombuffer(
            decode(audio_only, "-ac", "1", "-ar", str(RATE), "-f", "s16le"), dtype="<i2"
        )
        assert np.max(np.abs(standalone_samples[: RATE // 10])) > 1000
