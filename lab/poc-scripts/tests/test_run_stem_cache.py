"""Tests for poc/run_stem_cache.py — Phase 0c runner (issue #244).

The runner resolves each song's clean vocals via the cache-priority chain
(R2 dry stem download → R2 fallback stem download → MVSEP separation) and
records the outcome in the manifest. Tests exercise the orchestration with
mocked R2/MVSEP/DB layers; no network and no real separation.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from run_stem_cache import (
    PHASE12_SET,
    build_song_refs,
    process_song,
)
from stem_cache import (
    CacheStatus,
    SongRef,
    StemCacheError,
    load_or_init_manifest,
    record_result,
)

# --------------------------------------------------------------------------
# Phase 1/2 song set
# --------------------------------------------------------------------------


class TestPhase12Set:
    def test_exactly_ten_songs(self):
        assert len(PHASE12_SET) == 10

    def test_seed_positive_and_negative_ids_present(self):
        # 5 seed positives (published) + 5 seed negatives, including the
        # qwen3_asr and manual_upload negatives (spec Phase 1).
        assert "hereforyou_62e79ae9" in PHASE12_SET
        assert "ai_shi_wo_men_yong_gan_6d1865b8" in PHASE12_SET  # qwen3_asr
        assert "bu_ting_zan_mei_mi_e937a9d3" in PHASE12_SET  # manual_upload

    def test_song_ids_unique(self):
        assert len(set(PHASE12_SET)) == len(PHASE12_SET)


# --------------------------------------------------------------------------
# build_song_refs: DB lookup (mocked connection)
# --------------------------------------------------------------------------


class TestBuildSongRefs:
    def test_maps_song_ids_to_hash_prefixes(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [
            ("song_a", "aaaaaaaaaaaa"),
            ("song_b", "bbbbbbbbbbbb"),
        ]
        refs = build_song_refs(conn, ["song_a", "song_b"])
        assert refs == [
            ("song_a", "aaaaaaaaaaaa"),
            ("song_b", "bbbbbbbbbbbb"),
        ]

    def test_missing_song_raises(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [("song_a", "aaaaaaaaaaaa")]
        with pytest.raises(StemCacheError, match="song_b"):
            build_song_refs(conn, ["song_a", "song_b"])


# --------------------------------------------------------------------------
# process_song: the per-song resolution chain
# --------------------------------------------------------------------------


def _make_ctx(cache_root: Path, tmp_path: Path):
    manifest_path = tmp_path / "manifest.json"
    manifest = load_or_init_manifest(manifest_path)
    return manifest, manifest_path


class TestProcessSong:
    def test_r2_dry_stem_downloaded_and_recorded(self, tmp_path: Path):
        cache_root = tmp_path / "cache"
        manifest, manifest_path = _make_ctx(cache_root, tmp_path)

        r2 = MagicMock()
        r2.file_exists.side_effect = lambda key: key == "aaaaaaaaaaaa/stems/vocals_dry.flac"
        r2.download_file.side_effect = lambda key, dest: dest.write_bytes(b"flac-bytes")

        _song_id, status, source = process_song(
            SongRef("song_a", "aaaaaaaaaaaa"),
            cache_root=cache_root,
            manifest=manifest,
            manifest_path=manifest_path,
            r2_client=r2,
            mvsep_fn=MagicMock(side_effect=AssertionError("MVSEP must not run")),
        )
        assert status == CacheStatus.CACHED
        assert source == "r2_vocals_dry"
        stored = manifest.songs["song_a"]
        assert stored["status"] == "cached"
        assert stored["audio"] == "stems/clean_vocals.flac"
        # copied into the canonical clean_vocals.flac slot
        assert (
            cache_root / "aaaaaaaaaaaa" / "stems" / "clean_vocals.flac"
        ).read_bytes() == b"flac-bytes"
        r2.download_file.assert_called_once_with(
            "aaaaaaaaaaaa/stems/vocals_dry.flac",
            cache_root / "aaaaaaaaaaaa" / "stems" / "vocals_dry.flac",
        )

    def test_r2_wet_vocals_recorded_as_fallback(self, tmp_path: Path):
        cache_root = tmp_path / "cache"
        manifest, manifest_path = _make_ctx(cache_root, tmp_path)

        r2 = MagicMock()
        r2.file_exists.side_effect = lambda key: key == "aaaaaaaaaaaa/stems/vocals.flac"
        r2.download_file.side_effect = lambda key, dest: dest.write_bytes(b"wet")

        _song_id, status, source = process_song(
            SongRef("song_a", "aaaaaaaaaaaa"),
            cache_root=cache_root,
            manifest=manifest,
            manifest_path=manifest_path,
            r2_client=r2,
            mvsep_fn=MagicMock(side_effect=AssertionError("MVSEP must not run")),
        )
        assert status == CacheStatus.FALLBACK
        assert source == "r2_vocals"
        assert manifest.songs["song_a"]["audio"] == "stems/vocals.wav"
        # the fallback stem must be materialized at the consumer-resolvable path
        assert (cache_root / "aaaaaaaaaaaa" / "stems" / "vocals.wav").read_bytes() == b"wet"

    def test_mvsep_used_when_no_r2_stems(self, tmp_path: Path):
        cache_root = tmp_path / "cache"
        manifest, manifest_path = _make_ctx(cache_root, tmp_path)

        r2 = MagicMock()
        r2.file_exists.return_value = False
        r2.download_audio.side_effect = lambda prefix, dest: dest.write_bytes(b"mp3")

        dry = tmp_path / "vocals_(No Reverb).flac"
        dry.write_bytes(b"clean")
        mvsep = MagicMock(return_value=[dry])

        _song_id, status, source = process_song(
            SongRef("song_a", "aaaaaaaaaaaa"),
            cache_root=cache_root,
            manifest=manifest,
            manifest_path=manifest_path,
            r2_client=r2,
            mvsep_fn=mvsep,
        )
        assert status == CacheStatus.CACHED
        assert source == "mvsep"
        # audio downloaded first, then separation ran on it
        assert (cache_root / "aaaaaaaaaaaa" / "audio" / "audio.mp3").read_bytes() == b"mp3"
        clean = cache_root / "aaaaaaaaaaaa" / "stems" / "clean_vocals.flac"
        assert clean.read_bytes() == b"clean"
        mvsep.assert_called_once()

    def test_mvsep_failure_recorded_failed(self, tmp_path: Path):
        cache_root = tmp_path / "cache"
        manifest, manifest_path = _make_ctx(cache_root, tmp_path)

        r2 = MagicMock()
        r2.file_exists.return_value = False
        r2.download_audio.side_effect = lambda prefix, dest: dest.write_bytes(b"mp3")

        def boom(*a, **kw):
            raise RuntimeError("MVSEP queue full")

        _song_id, status, _source = process_song(
            SongRef("song_a", "aaaaaaaaaaaa"),
            cache_root=cache_root,
            manifest=manifest,
            manifest_path=manifest_path,
            r2_client=r2,
            mvsep_fn=boom,
        )
        assert status == CacheStatus.FAILED
        stored = manifest.songs["song_a"]
        assert stored["status"] == "failed"
        assert "MVSEP" in stored["error"]

    def test_mvsep_output_without_dry_name_recorded_failed(self, tmp_path: Path):
        """MVSEP returning no dry-vocals file must not record a clean vocal."""
        cache_root = tmp_path / "cache"
        manifest, manifest_path = _make_ctx(cache_root, tmp_path)

        r2 = MagicMock()
        r2.file_exists.return_value = False
        r2.download_audio.side_effect = lambda prefix, dest: dest.write_bytes(b"mp3")

        mvsep = MagicMock(return_value=[])

        _song_id, status, _source = process_song(
            SongRef("song_a", "aaaaaaaaaaaa"),
            cache_root=cache_root,
            manifest=manifest,
            manifest_path=manifest_path,
            r2_client=r2,
            mvsep_fn=mvsep,
        )
        assert status == CacheStatus.FAILED
        assert "clean_vocals" not in manifest.songs["song_a"]

    def test_failed_entry_retried_on_next_run(self, tmp_path: Path):
        cache_root = tmp_path / "cache"
        manifest, manifest_path = _make_ctx(cache_root, tmp_path)
        record_result(
            manifest,
            manifest_path,
            SongRef("song_a", "aaaaaaaaaaaa"),
            status=CacheStatus.FAILED,
            error="transient",
        )

        # simulate a fresh runner invocation: reload manifest from disk
        manifest2 = load_or_init_manifest(manifest_path)
        assert manifest2.songs["song_a"]["status"] == "failed"

        r2 = MagicMock()
        r2.file_exists.side_effect = lambda key: key == "aaaaaaaaaaaa/stems/vocals_dry.flac"
        r2.download_file.side_effect = lambda key, dest: dest.write_bytes(b"flac")

        _song_id, status, _source = process_song(
            SongRef("song_a", "aaaaaaaaaaaa"),
            cache_root=cache_root,
            manifest=manifest2,
            manifest_path=manifest_path,
            r2_client=r2,
            mvsep_fn=MagicMock(side_effect=AssertionError("MVSEP must not run")),
        )
        assert status == CacheStatus.CACHED
        assert "error" not in manifest2.songs["song_a"]

    def test_existing_clean_vocals_file_short_circuits(self, tmp_path: Path):
        """A cache hit on disk (from a prior run) is reused without R2 calls."""
        cache_root = tmp_path / "cache"
        clean = cache_root / "aaaaaaaaaaaa" / "stems" / "clean_vocals.flac"
        clean.parent.mkdir(parents=True)
        clean.write_bytes(b"already-here")

        manifest, manifest_path = _make_ctx(cache_root, tmp_path)

        r2 = MagicMock(side_effect=AssertionError("R2 must not be touched"))
        _song_id, status, source = process_song(
            SongRef("song_a", "aaaaaaaaaaaa"),
            cache_root=cache_root,
            manifest=manifest,
            manifest_path=manifest_path,
            r2_client=r2,
            mvsep_fn=MagicMock(side_effect=AssertionError("MVSEP must not run")),
            # prior run crashed before recording the manifest
        )
        assert status == CacheStatus.CACHED
        assert source == "local_clean_vocals"


# --------------------------------------------------------------------------
# Runner-level: serial enforcement and idempotent re-invocation
# --------------------------------------------------------------------------


class TestRunnerIdempotence:
    def test_rerun_after_complete_run_changes_nothing(self, tmp_path, monkeypatch):
        """run() on a fully-resolved set performs no R2/DB I/O beyond ref
        resolution and leaves the manifest untouched."""

        from run_stem_cache import run as run_cache

        cache_root = tmp_path / "cache"
        manifest, manifest_path = _make_ctx(cache_root, tmp_path)
        record_result(
            manifest,
            manifest_path,
            SongRef("song_a", "aaaaaaaaaaaa"),
            status=CacheStatus.CACHED,
            source="r2_vocals_dry",
            audio="stems/clean_vocals.flac",
        )
        before = json.loads(manifest_path.read_text(encoding="utf-8"))

        class _ExplodingR2:
            def __getattr__(self, name):
                def _boom(*a, **k):
                    raise AssertionError(f"R2.{name} must not be touched on resume")

                return _boom

        class _FakeConn:
            def cursor(self):
                raise AssertionError("DB must not be touched on resume")

        class _FakeProvider:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get_connection(self):
                return _FakeConn()

            def close(self):
                pass

        class _FakeConfig:
            r2_bucket = "bucket"
            r2_endpoint_url = "http://localhost"
            r2_region = "us-east-1"

            def get_connection_url(self):
                # run() evaluates the URL eagerly; the DB-isolation guard is
                # _FakeConn.cursor below (and build_song_refs is stubbed).
                return "postgresql://unused"

        monkeypatch.setattr(
            "stream_of_worship.admin.config.AdminConfig.load", lambda p: _FakeConfig()
        )
        monkeypatch.setattr(
            "stream_of_worship.db.connection.ConnectionProvider",
            lambda url: _FakeProvider(),
        )
        monkeypatch.setattr(
            "stream_of_worship.admin.services.r2.R2Client", lambda **k: _ExplodingR2()
        )

        # build_song_refs must still resolve refs (read-only DB): stub it at
        # module level since run() calls it by global name.
        monkeypatch.setattr(
            "run_stem_cache.build_song_refs",
            lambda conn, ids: [("song_a", "aaaaaaaaaaaa")],
        )

        summary = run_cache(
            song_ids=["song_a"],
            cache_root=cache_root,
            manifest_path=manifest_path,
            lock_path=tmp_path / "serial.lock",
            config_path=None,
            dry_run=False,
        )
        assert summary.cached == 0 and summary.fallback == 0 and summary.failed == 0
        assert summary.skipped == 1
        assert before == json.loads(manifest_path.read_text(encoding="utf-8"))
