"""End-to-end proof: the card covers silence, not the first lyric line."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sow_render_worker.audio_engine import (
    AudioClipInfo,
    AudioSegmentInfo,
    SongsetItem,
    generate_songset_audio,
)
from sow_render_worker.video_engine import VideoEngine

FFMPEG_BIN = shutil.which("ffmpeg")
FFPROBE_BIN = shutil.which("ffprobe")


pytestmark = pytest.mark.skipif(
    FFMPEG_BIN is None or FFPROBE_BIN is None,
    reason="ffmpeg/ffprobe are required for the real render fixture",
)


class _ToneFetcher:
    """Audio fixture with an LRC whose first sung line starts at 3 s."""

    LRC = "[00:03.00]First line\n[00:08.00]Second line"

    def __init__(self, audio_path: Path) -> None:
        self.audio_path = str(audio_path)

    def download_audio(self, hash_prefix: str) -> str:
        return self.audio_path

    def download_lrc(self, hash_prefix: str) -> str:
        return self.LRC

    def get_temp_dir(self) -> Path:
        return Path(self.audio_path).parent


def _make_item() -> SongsetItem:
    return SongsetItem(
        id="item-1",
        songset_id="ss-1",
        song_id="song-1",
        song_title="Test Song",
        recording_hash_prefix="abc123",
        position=0,
        gap_beats=2.0,
        tempo_bpm=120.0,
    )


def _mp3_duration_seconds(path: Path) -> float:
    probe = subprocess.run(
        [
            FFPROBE_BIN, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        check=True, capture_output=True,
    )
    return float(json.loads(probe.stdout)["format"]["duration"])


def _first_silence_end_seconds(path: Path) -> float:
    result = subprocess.run(
        [
            FFMPEG_BIN, "-v", "info",
            "-f", "lavfi", "-i",
            f"amovie={path},silencedetect=noise=-50dB:d=0.4",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    match = re.search(r"silence_end: ([\d.]+)", result.stderr)
    assert match, result.stderr[-2000:]
    return float(match.group(1))


def test_card_plays_over_lead_in_silence(tmp_path: Path):
    tone = tmp_path / "tone.mp3"
    subprocess.run(
        [
            FFMPEG_BIN, "-y", "-v", "error",
            "-f", "lavfi", "-i", "sine=frequency=330:duration=10:sample_rate=44100",
            "-c:a", "libmp3lame", "-b:a", "192k", str(tone),
        ],
        check=True,
    )

    fetcher = _ToneFetcher(tone)
    item = _make_item()
    # The card is 5 s; the first sung line is at 3 s, so 3 s of silence goes in
    # and that line lands at 6 s — one second after the card clears.
    lead_in = 3.0
    shifted_start = lead_in
    duration = _mp3_duration_seconds(tone)
    segment = AudioSegmentInfo(
        item=item,
        audio_path=str(tone),
        start_time_seconds=shifted_start,
        duration_seconds=duration,
        gap_before_seconds=0.0,
        clips=(
            AudioClipInfo(
                audio_path=str(tone),
                source_start_seconds=0.0,
                source_end_seconds=duration,
                start_time_seconds=shifted_start,
            ),
        ),
    )

    mix_path = tmp_path / "mix.mp3"
    result = generate_songset_audio(
        [item], str(mix_path), fetcher,
        normalize=False,
        lead_in_resolver=lambda segments: lead_in,
    )

    assert result.lead_in_seconds == pytest.approx(lead_in)
    assert result.total_duration_seconds == pytest.approx(duration + lead_in, abs=0.15)
    assert _mp3_duration_seconds(mix_path) == pytest.approx(duration + lead_in, abs=0.15)
    assert _first_silence_end_seconds(mix_path) == pytest.approx(lead_in, abs=0.15)

    # The card overlays the padded mix, so the MP4 keeps the mix's length and the
    # first post-card frame is 2 s into the song, still ahead of the 6 s lyric.
    engine = VideoEngine(
        fetcher,
        resolution="720p",
        include_title_card=True,
        title_card_duration_seconds=5.0,
        songset_name="Worship Set",
    )
    drawn_at: list[float] = []
    render = engine.frame_renderer.render_frame_bytes

    def recording(lyrics, segments, current_time, glue_windows=()):
        drawn_at.append(current_time)
        return render(lyrics, segments, current_time)

    engine.frame_renderer.render_frame_bytes = recording  # type: ignore[method-assign]

    video_path = tmp_path / "out.mp4"
    engine.generate_video(
        str(mix_path), [segment], str(video_path), job_id="e2e-lead-in"
    )
    engine.frame_renderer.clear_cache()

    assert video_path.exists()
    assert _mp3_duration_seconds(video_path) == pytest.approx(
        result.total_duration_seconds, abs=0.3
    )

    # The first frame drawn after the 5 s card (frame 120) sits at 5.0 s on the
    # global timeline: 2 s into the song, one second before the first lyric.
    assert math.ceil(engine.title_card_duration_seconds * engine.fps) == 120
    assert drawn_at[0] == pytest.approx(5.0)
