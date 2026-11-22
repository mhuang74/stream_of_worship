"""No-paid end-to-end Glue render journey over the Traditional Chinese fixture.

This drives the render worker's real seams from an immutable snapshot written
by the webapp:

    fetch_songset_items -> load_render_glue_clips -> calculate_total_duration
    -> generate_songset_audio -> generate_chapters_manifest -> video/frame

Audio is real: three 44.1 kHz stereo PCM16 WAVs (A, Glue, B) are synthesized,
hashed with SHA-256, and served through a fetcher double that verifies every
checksum it is handed. The MP3 is produced by the real ffmpeg filter graph, so
its probed duration and its frequency content are facts about the mix rather
than about the test's arithmetic.

Phase A never contacts the paid provider: this module installs guards that
fail loudly if the production ElevenLabs transport, its urllib3 pool, the
provider factory, or ``SOW_ELEVENLABS_API_KEY`` is touched at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf
from PIL import Image, ImageChops

from sow_render_worker import glue_provider
from sow_render_worker.asset_fetcher import GlueDownloadError
from sow_render_worker.audio_engine import (
    GlueWindow,
    calculate_total_duration,
    generate_songset_audio,
    get_audio_info,
)
from sow_render_worker.chapters import (
    find_chapter_at_time,
    generate_chapters_manifest,
    get_lyric_at_time,
    get_song_title_at_time,
)
from sow_render_worker.db import RenderJob
from sow_render_worker.frame_renderer import (
    VIDEO_TEMPLATES,
    FrameRenderer,
    GlueWindowInfo,
    SegmentInfo,
)
from sow_render_worker.lrc_parser import GlobalLRCLine, parse_lrc, remap_lrc_to_clips
from sow_render_worker.pipeline import (
    GLUE_NOT_READY,
    GLUE_STALE_SNAPSHOT,
    GlueSnapshotError,
    fetch_songset_items,
    load_render_glue_clips,
)
from sow_render_worker.video_engine import VideoEngine

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "webapp"
    / "src"
    / "test"
    / "fixtures"
    / "tc-songset.cross_language.json"
)

USER_ID = 42
SONGSET_ID = "ss-tc-glue-acceptance"
JOB_ID = "render-job-glue-1"
UPDATED_AT = datetime(2026, 11, 22, 10, 0, 0, tzinfo=timezone.utc)
SAMPLE_RATE = 44100

# Distinguishable carriers: A's arranged audio, the staged Glue, and B.
FREQ_A_HZ = 220.0
FREQ_GLUE_HZ = 1000.0
FREQ_B_HZ = 440.0


def _load_fixture() -> dict:
    with FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _write_tone(path: Path, seconds: float, frequency_hz: float) -> str:
    """Write a canonical 44.1 kHz stereo PCM16 WAV and return its path."""
    frames = round(seconds * SAMPLE_RATE)
    times = np.arange(frames, dtype=np.float64) / SAMPLE_RATE
    mono = (0.25 * np.sin(2.0 * np.pi * frequency_hz * times)).astype(np.float32)
    stereo = np.column_stack([mono, mono])
    sf.write(str(path), stereo, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return str(path)


def _dominant_frequency_hz(wav_path: Path, start_seconds: float, seconds: float) -> float:
    with sf.SoundFile(str(wav_path)) as handle:
        handle.seek(round(start_seconds * handle.samplerate))
        block = handle.read(frames=round(seconds * handle.samplerate), dtype="float32")
    mono = block.mean(axis=1) if block.ndim > 1 else block
    windowed = mono * np.hanning(len(mono))
    spectrum = np.abs(np.fft.rfft(windowed))
    frequencies = np.fft.rfftfreq(len(windowed), d=1.0 / SAMPLE_RATE)
    return float(frequencies[int(np.argmax(spectrum))])


class _NoProviderEnv:
    """os.environ proxy that fails if the provider key is ever read."""

    FORBIDDEN = "SOW_ELEVENLABS_API_KEY"

    def __init__(self, base) -> None:
        self._base = base
        self.forbidden_reads = 0

    def _guard(self, key) -> None:
        if key == self.FORBIDDEN:
            self.forbidden_reads += 1
            raise AssertionError("Phase A journey must never read the provider key")

    def __getitem__(self, key):
        self._guard(key)
        return self._base[key]

    def get(self, key, default=None):
        self._guard(key)
        return self._base.get(key, default)

    def __contains__(self, key):
        self._guard(key)
        return key in self._base

    def __setitem__(self, key, value):
        self._guard(key)
        self._base[key] = value

    def __delitem__(self, key):
        self._guard(key)
        del self._base[key]

    def __iter__(self):
        return iter(self._base)

    def __len__(self):
        return len(self._base)

    def setdefault(self, key, default=None):
        self._guard(key)
        return self._base.setdefault(key, default)

    def copy(self):
        return self._base.copy()

    def keys(self):
        return self._base.keys()

    def items(self):
        return self._base.items()

    def values(self):
        return self._base.values()


class _JourneyFetcher:
    """Asset-fetcher double: real bytes, real checksum verification."""

    def __init__(self, audio: dict[str, str], glue: dict[str, bytes], lrc: dict[str, str]) -> None:
        self._audio = audio
        self._glue = glue
        self._lrc = lrc
        self.download_glue_calls: list[dict] = []
        self._cache_dir: Path | None = None

    def use_cache_dir(self, path: Path) -> None:
        self._cache_dir = path

    def download_audio(self, hash_prefix: str) -> str | None:
        return self._audio.get(hash_prefix)

    def download_glue(
        self,
        r2_key: str,
        *,
        expected_bytes: int | None,
        expected_sha256: str | None,
        cache_key: str | None = None,
        job_id: str | None = None,
        take_id: str | None = None,
    ) -> str:
        self.download_glue_calls.append(
            {
                "r2_key": r2_key,
                "expected_bytes": expected_bytes,
                "expected_sha256": expected_sha256,
                "job_id": job_id,
                "take_id": take_id,
            }
        )
        blob = self._glue[r2_key]
        if expected_bytes is not None and len(blob) != expected_bytes:
            raise GlueDownloadError("Glue audio byte length mismatch")
        if expected_sha256 is not None and _sha256(blob) != expected_sha256.lower():
            raise GlueDownloadError("Glue audio checksum mismatch")
        assert self._cache_dir is not None
        path = self._cache_dir / f"{cache_key or take_id}.wav"
        path.write_bytes(blob)
        return str(path)

    def download_lrc(self, hash_prefix: str) -> str | None:
        return self._lrc.get(hash_prefix)

    def get_temp_dir(self) -> Path:
        assert self._cache_dir is not None
        return self._cache_dir

    def initialize(self) -> None:
        return None


class _FakeCursor:
    def __init__(self, connection: "_FakeConnection") -> None:
        self._connection = connection
        self._rows: list[dict] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def execute(self, sql: str, params=None) -> None:
        self._rows = self._connection.answer(sql, params)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeConnection:
    """Answers exactly the SQL the real DB readers issue."""

    def __init__(
        self,
        *,
        songset_name: str,
        items: list[dict],
        take: dict | None,
        transitions: list[dict] | None,
    ) -> None:
        self.songset_name = songset_name
        self.items = items
        self.take = take
        self.transitions = transitions
        self.statements: list[str] = []

    def answer(self, sql: str, params) -> list[dict]:
        self.statements.append(sql)
        if "FROM songsets" in sql:
            return [{"name": self.songset_name}]
        if "FROM songset_items si" in sql:
            return self.items
        if "glue_transitions" in sql:
            raw = None if self.transitions is None else json.dumps(self.transitions)
            return [{"glue_transitions": raw}]
        if "FROM glue_takes WHERE id" in sql:
            return [] if self.take is None else [self.take]
        raise AssertionError(f"unexpected SQL in journey: {sql}")

    def cursor(self, *args, **kwargs) -> _FakeCursor:
        return _FakeCursor(self)


def _fixture_item_rows(fixture: dict) -> list[dict]:
    """songset_items rows plus the Recording analysis columns the reader uses."""
    rows: list[dict] = []
    for song in fixture["songs"]:
        rows.append(
            {
                "id": song["itemId"],
                "songset_id": fixture["songset"]["id"],
                "song_id": song["songId"],
                "recording_hash_prefix": song["recording"]["hashPrefix"],
                "position": song["position"],
                "gap_beats": float(song["gapBeats"]),
                "crossfade_enabled": 0,
                "crossfade_duration_seconds": None,
                "key_shift_semitones": 0,
                "tempo_ratio": 1.0,
                "arrangement_steps": json.dumps(song["arrangement"]),
                "updated_at": UPDATED_AT,
                "tempo_bpm": song["analysis"]["tempoBpm"],
                "duration_seconds": song["recording"]["durationSeconds"],
                "sections": json.dumps(song["analysis"]["sections"]),
                "beats": json.dumps(song["analysis"]["beats"]),
                "recording_content_hash": song["recording"]["contentHash"],
                "deleted_at": None,
                "song_title": song["title"],
            }
        )
    return rows


def _render_job() -> RenderJob:
    return RenderJob(
        id=JOB_ID,
        songset_id=SONGSET_ID,
        user_id=USER_ID,
        status="running",
        template="dark",
        resolution="720p",
        audio_enabled=True,
        video_enabled=True,
        font_size_preset="M",
        font_family="noto_serif_tc",
        include_title_card=False,
    )


def _take_row(fixture: dict, glue_sha: str, glue_bytes: int) -> dict:
    glue = fixture["glue"][0]
    return {
        "id": glue["takeId"],
        "user_id": USER_ID,
        "descriptor_key": glue["descriptorKey"],
        "descriptor_json": glue["descriptorJson"],
        "ordinal": 1,
        "seed": 7,
        "status": "ready",
        "r2_key": glue["r2Key"],
        "seconds": glue["stagedSeconds"],
        "metrics_json": json.dumps({"audioSha256": glue_sha, "audioBytes": glue_bytes}),
        "reasons_json": "[]",
    }


def _snapshot_entry(fixture: dict, glue_sha: str) -> dict:
    glue = fixture["glue"][0]
    return {
        "from_item_id": glue["fromItemId"],
        "to_item_id": glue["toItemId"],
        "take_id": glue["takeId"],
        "descriptor_key": glue["descriptorKey"],
        "r2_key": glue["r2Key"],
        "seconds": glue["stagedSeconds"],
        "sha256": glue_sha,
        "from_updated_at": UPDATED_AT.isoformat(),
        "to_updated_at": UPDATED_AT.isoformat(),
    }


@dataclass
class _Context:
    fixture: dict
    connection: _FakeConnection
    fetcher: _JourneyFetcher
    items: list
    job: RenderJob


def _build_context(tmp_path: Path, *, take_overrides=None, entry_overrides=None) -> _Context:
    """Build real audio plus the fixture-backed DB rows (no Glue download yet)."""
    fixture = _load_fixture()
    assert fixture["expectedTimeline"]["totalSeconds"] == 33.0

    media = tmp_path / "media"
    cache = tmp_path / "cache"
    media.mkdir()
    cache.mkdir()

    # Full Recordings (30 s each); the saved arrangements select 12 s / 18 s.
    audio: dict[str, str] = {}
    lrc: dict[str, str] = {}
    for song, frequency in zip(fixture["songs"], (FREQ_A_HZ, FREQ_B_HZ)):
        prefix = song["recording"]["hashPrefix"]
        audio[prefix] = _write_tone(
            media / f"{prefix}.wav", song["recording"]["durationSeconds"], frequency
        )
        lrc[prefix] = song["lrc"]

    glue_wav = Path(
        _write_tone(media / "glue.wav", fixture["glue"][0]["stagedSeconds"], FREQ_GLUE_HZ)
    )
    glue_blob = glue_wav.read_bytes()
    glue_sha = _sha256(glue_blob)

    take_row = _take_row(fixture, glue_sha, len(glue_blob))
    if take_overrides:
        take_row.update(take_overrides)
    entry = _snapshot_entry(fixture, glue_sha)
    if entry_overrides:
        entry.update(entry_overrides)

    connection = _FakeConnection(
        songset_name=fixture["songset"]["name"],
        items=_fixture_item_rows(fixture),
        take=take_row,
        transitions=[entry],
    )
    fetcher = _JourneyFetcher(
        audio=audio,
        glue={take_row["r2_key"]: glue_blob},
        lrc=lrc,
    )
    fetcher.use_cache_dir(cache)
    job = _render_job()

    _name, items = fetch_songset_items(connection, SONGSET_ID)
    assert [item.id for item in items] == [song["itemId"] for song in fixture["songs"]]
    assert [item.duration_seconds for item in items] == pytest.approx([12.0, 18.0])
    assert all(item.updated_at == UPDATED_AT for item in items)
    return _Context(
        fixture=fixture,
        connection=connection,
        fetcher=fetcher,
        items=items,
        job=job,
    )


def _video_inputs(result, fetcher: _JourneyFetcher):
    """Renderer inputs derived from the real mixed segments, not the fixture."""
    segments = [
        SegmentInfo(
            id=segment.item.id,
            song_id=segment.item.song_id,
            position=segment.item.position,
            song_title=segment.item.song_title,
            start_time_seconds=segment.start_time_seconds,
            duration_seconds=segment.duration_seconds,
            tempo_bpm=segment.item.tempo_bpm,
        )
        for segment in result.segments
    ]
    lyrics: list[GlobalLRCLine] = []
    for segment in result.segments:
        lrc_content = fetcher.download_lrc(segment.item.recording_hash_prefix)
        assert lrc_content, "fixture must ship Lyrics for every Song"
        lyrics.extend(
            remap_lrc_to_clips(
                parse_lrc(lrc_content),
                segment.clips,
                segment.item.id,
                tempo_bpm=segment.item.tempo_bpm,
            )
        )
    windows = [
        GlueWindowInfo(
            to_item_id=window.to_item_id,
            start_seconds=window.start_seconds,
            end_seconds=window.end_seconds,
        )
        for window in result.glue_windows
    ]
    return segments, lyrics, windows


def _ink_bbox(img: Image.Image):
    background = Image.new("RGB", img.size, img.getpixel((0, 0)))
    return ImageChops.difference(img, background).getbbox()


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe required for the real mix",
)
class TestGlueRenderJourney:
    def test_mp3_glue_windows_segments_and_chapters_match_the_fixture(self, tmp_path):
        env_proxy = _NoProviderEnv(os.environ)

        with (
            patch.object(
                glue_provider,
                "ElevenLabsHttpTransport",
                side_effect=AssertionError("provider transport must never be constructed"),
            ) as transport,
            patch.object(
                glue_provider.urllib3,
                "PoolManager",
                side_effect=AssertionError("provider HTTP pool must never be constructed"),
            ) as pool,
            patch.object(
                glue_provider,
                "create_provider_from_env",
                side_effect=AssertionError("provider factory must never be called"),
            ) as factory,
            patch.object(os, "environ", env_proxy),
        ):
            context = _build_context(tmp_path)
            fixture = context.fixture
            timeline = fixture["expectedTimeline"]
            glue = fixture["glue"][0]

            # --- snapshot -> clips -------------------------------------
            clips = load_render_glue_clips(
                context.connection, context.job, context.items, context.fetcher
            )
            assert list(clips) == [glue["toItemId"]]
            clip = clips[glue["toItemId"]]
            assert clip.take_id == glue["takeId"]
            assert clip.seconds == 3.0
            assert clip.checksum == _sha256(Path(clip.path).read_bytes())
            assert len(context.fetcher.download_glue_calls) == 1
            call = context.fetcher.download_glue_calls[0]
            assert call["r2_key"] == glue["r2Key"]
            assert call["expected_sha256"] == clip.checksum

            # --- duration ----------------------------------------------
            total = calculate_total_duration(context.items, context.fetcher, clips)
            assert total == pytest.approx(33.0)

            # --- real mix ----------------------------------------------
            mp3 = tmp_path / "songset.mp3"
            result = generate_songset_audio(
                context.items,
                str(mp3),
                context.fetcher,
                normalize=False,
                job_id=JOB_ID,
                glue_clips=clips,
            )

        # The provider key was never read and no production client was built.
        assert env_proxy.forbidden_reads == 0
        transport.assert_not_called()
        pool.assert_not_called()
        factory.assert_not_called()

        assert result.total_duration_seconds == pytest.approx(33.0)
        assert result.glue_windows == (
            GlueWindow(to_item_id="item-b", start_seconds=12.0, end_seconds=15.0),
        )
        assert [segment.item.id for segment in result.segments] == ["item-a", "item-b"]
        assert [segment.start_time_seconds for segment in result.segments] == pytest.approx(
            [0.0, 15.0]
        )
        assert [segment.duration_seconds for segment in result.segments] == pytest.approx(
            [12.0, 18.0]
        )

        # ffprobe reads the real MP3; allow one MP3 frame (26 ms) of padding.
        info = get_audio_info(str(mp3))
        assert info is not None
        assert info["duration_seconds"] == pytest.approx(timeline["totalSeconds"], abs=0.05)
        assert abs(info["duration_seconds"] - result.total_duration_seconds) <= 0.05

        # Provenance of the audio at timeline points, decoded from the MP3.
        decoded = tmp_path / "decoded.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "quiet",
                "-i",
                str(mp3),
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                str(decoded),
            ],
            check=True,
        )
        assert _dominant_frequency_hz(decoded, 1.0, 0.5) == pytest.approx(FREQ_A_HZ, abs=20.0)
        assert _dominant_frequency_hz(decoded, 11.5, 0.25) == pytest.approx(FREQ_A_HZ, abs=20.0)
        assert _dominant_frequency_hz(decoded, 13.5, 0.5) == pytest.approx(FREQ_GLUE_HZ, abs=20.0)
        assert _dominant_frequency_hz(decoded, 20.0, 0.5) == pytest.approx(FREQ_B_HZ, abs=20.0)

        # --- chapters ------------------------------------------------
        manifest = generate_chapters_manifest(
            list(result.segments), context.fetcher.download_lrc, result.total_duration_seconds
        )
        assert [
            (chapter.song_title, chapter.start_seconds, chapter.end_seconds)
            for chapter in manifest.chapters
        ] == [
            (chapter_data["title"], chapter_data["startSeconds"], chapter_data["endSeconds"])
            for chapter_data in timeline["chapters"]
        ]
        assert manifest.total_duration_seconds == pytest.approx(33.0)
        assert manifest.chapters[1].lines[0].start_seconds == pytest.approx(15.5)
        assert manifest.chapters[1].lines[-1].start_seconds == pytest.approx(30.0)
        assert get_lyric_at_time(manifest, 15.5) == "祢的信實何等廣大"
        midpoint = timeline["midpointFrame"]["timeSeconds"]
        assert find_chapter_at_time(manifest, midpoint) == -1
        assert get_song_title_at_time(manifest, midpoint) is None
        assert get_lyric_at_time(manifest, midpoint) is None

        # --- video engine -------------------------------------------
        segments, lyrics, windows = _video_inputs(result, context.fetcher)
        engine = VideoEngine(context.fetcher, include_title_card=False)
        with (
            patch("sow_render_worker.video_engine.get_audio_info", return_value=info),
            patch.object(engine, "encode_video_with_ffmpeg") as encode,
        ):
            video = engine.generate_video(
                str(mp3),
                result.segments,
                str(tmp_path / "songset.mp4"),
                job_id=JOB_ID,
                glue_windows=result.glue_windows,
            )
        # video.duration_seconds comes from the same ffprobe reading of the MP3
        # above, so it carries the same encoder padding tolerance.
        assert video.duration_seconds == pytest.approx(33.0, abs=0.05)
        assert encode.call_args.kwargs["glue_windows"] == tuple(windows)
        encoded_lyrics = encode.call_args.args[4]
        assert [
            (line.text, line.global_time_seconds)
            for line in encoded_lyrics
            if line.item_id == fixture["songs"][1]["itemId"]
        ] == [(line["text"], line["globalSeconds"]) for line in fixture["songs"][1]["lyrics"]]

        # --- 13.5 s frame: B's title only -----------------------------
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        state = renderer._resolve_visual_state(lyrics, segments, midpoint, glue_windows=windows)
        assert state.current_title == timeline["midpointFrame"]["expectedTitle"]
        assert state.current_title == "祢的信實廣大"
        assert state.segment_id == "item-b"
        assert state.current_song_lyrics == []
        assert state.current_lyric_index == -1

        with patch.object(renderer, "render_title_card") as title_card:
            frame = renderer.render_frame(lyrics, segments, midpoint, glue_windows=windows)
        title_card.assert_not_called()
        bbox = _ink_bbox(frame)
        assert bbox is not None, "the incoming title must be drawn"
        assert bbox[3] < int(frame.height * 0.33), "no lyric ink in the lyric band"

        # B's first lyric appears exactly at its shifted global time.
        before = renderer.render_frame(lyrics, segments, 15.5 - 0.001)
        at = renderer.render_frame(lyrics, segments, 15.5)
        assert _ink_bbox(before)[3] < int(before.height * 0.33)
        assert _ink_bbox(at)[3] >= int(at.height * 0.33)
        frame.close()
        before.close()
        at.close()

    def test_ready_take_with_a_different_r2_key_fails_before_mixing(self, tmp_path):
        context = _build_context(
            tmp_path,
            take_overrides={"r2_key": "glues/42/different/take.wav"},
        )

        with pytest.raises(GlueSnapshotError) as excinfo:
            load_render_glue_clips(
                context.connection, context.job, context.items, context.fetcher
            )

        assert excinfo.value.code == GLUE_NOT_READY
        assert "staged object key mismatch" in excinfo.value.detail
        assert context.fetcher.download_glue_calls == []
        assert not (tmp_path / "songset.mp3").exists()

    def test_moved_to_updated_at_fails_as_stale_snapshot(self, tmp_path):
        moved = (UPDATED_AT + timedelta(seconds=1)).isoformat()
        context = _build_context(tmp_path, entry_overrides={"to_updated_at": moved})

        with pytest.raises(GlueSnapshotError) as excinfo:
            load_render_glue_clips(
                context.connection, context.job, context.items, context.fetcher
            )

        assert excinfo.value.code == GLUE_STALE_SNAPSHOT
        assert "to item changed since the snapshot" in excinfo.value.detail
        assert context.fetcher.download_glue_calls == []
