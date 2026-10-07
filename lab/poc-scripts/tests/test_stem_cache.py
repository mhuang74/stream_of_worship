"""Tests for poc/stem_cache.py — Phase 0c stem-cache pipeline (issue #244).

Covers the manifest record lifecycle (pending → cached/failed), resume
semantics (cached entries skipped, failed entries retried), the serial
execution lock, and the R2 clean-vocals lookup priority.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from stem_cache import (
    CacheStatus,
    SongRef,
    StemCacheError,
    load_or_init_manifest,
    lookup_r2_clean_vocals,
    next_pending,
    record_result,
    try_lock_serial,
)

# --------------------------------------------------------------------------
# Manifest load/init
# --------------------------------------------------------------------------


class TestLoadOrInitManifest:
    def test_creates_fresh_manifest(self, tmp_path: Path):
        path = tmp_path / "manifest.json"
        manifest = load_or_init_manifest(path)
        assert path.exists()
        assert manifest.schema_version == 1
        assert manifest.songs == {}

    def test_reloads_existing_manifest(self, tmp_path: Path):
        path = tmp_path / "manifest.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "created_at": "2026-10-07T00:00:00Z",
                    "updated_at": "2026-10-07T00:00:00Z",
                    "songs": {
                        "song_a": {
                            "status": "cached",
                            "source": "r2_vocals_dry",
                            "audio": "stems/clean_vocals.flac",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        manifest = load_or_init_manifest(path)
        assert manifest.songs["song_a"]["status"] == "cached"

    def test_rejects_unknown_schema_version(self, tmp_path: Path):
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps({"schema_version": 99, "songs": {}}), encoding="utf-8")
        with pytest.raises(StemCacheError, match="schema_version"):
            load_or_init_manifest(path)


# --------------------------------------------------------------------------
# Record lifecycle
# --------------------------------------------------------------------------


def _song(song_id: str, hash_prefix: str) -> SongRef:
    return SongRef(song_id=song_id, hash_prefix=hash_prefix)


class TestRecordResult:
    def test_record_cached_writes_audio_path_and_timestamp(self, tmp_path: Path):
        path = tmp_path / "manifest.json"
        manifest = load_or_init_manifest(path)
        record_result(
            manifest,
            path,
            _song("song_a", "aaaaaaaaaaaa"),
            status=CacheStatus.CACHED,
            source="r2_vocals_dry",
            audio="stems/clean_vocals.flac",
        )
        stored = manifest.songs["song_a"]
        assert stored["status"] == "cached"
        assert stored["source"] == "r2_vocals_dry"
        assert stored["audio"] == "stems/clean_vocals.flac"
        assert "resolved_at" in stored
        # manifest file was rewritten atomically with the same content
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["songs"]["song_a"]["audio"] == "stems/clean_vocals.flac"

    def test_record_failed_keeps_error_and_is_retryable(self, tmp_path: Path):
        path = tmp_path / "manifest.json"
        manifest = load_or_init_manifest(path)
        record_result(
            manifest,
            path,
            _song("song_b", "bbbbbbbbbbbb"),
            status=CacheStatus.FAILED,
            error="MVSEP stage1 timeout",
        )
        stored = manifest.songs["song_b"]
        assert stored["status"] == "failed"
        assert stored["error"] == "MVSEP stage1 timeout"
        # failed is not final: next_pending must return it again
        pending = next_pending(manifest, [_song("song_b", "bbbbbbbbbbbb")])
        assert [s.song_id for s in pending] == ["song_b"]

    def test_record_fallback_explicitly(self, tmp_path: Path):
        path = tmp_path / "manifest.json"
        manifest = load_or_init_manifest(path)
        record_result(
            manifest,
            path,
            _song("song_c", "cccccccccccc"),
            status=CacheStatus.FALLBACK,
            source="r2_vocals",
            audio="stems/vocals.flac",
        )
        assert manifest.songs["song_c"]["status"] == "fallback"
        assert manifest.songs["song_c"]["audio"] == "stems/vocals.flac"

    def test_completed_entry_not_corrupted_by_rerun(self, tmp_path: Path):
        """A completed run re-invoked must not overwrite cache entries."""
        path = tmp_path / "manifest.json"
        manifest = load_or_init_manifest(path)
        record_result(
            manifest,
            path,
            _song("song_a", "aaaaaaaaaaaa"),
            status=CacheStatus.CACHED,
            source="mvsep",
            audio="stems/clean_vocals.flac",
        )
        before = manifest.songs["song_a"]
        # re-recording a terminal status raises instead of clobbering
        with pytest.raises(StemCacheError):
            record_result(
                manifest,
                path,
                _song("song_a", "aaaaaaaaaaaa"),
                status=CacheStatus.FAILED,
                error="should not happen",
            )
        assert manifest.songs["song_a"] == before

    def test_atomic_write_leaves_no_partial_file(self, tmp_path: Path):
        path = tmp_path / "manifest.json"
        manifest = load_or_init_manifest(path)
        record_result(
            manifest,
            path,
            _song("song_a", "aaaaaaaaaaaa"),
            status=CacheStatus.CACHED,
            source="r2_vocals_dry",
            audio="stems/clean_vocals.flac",
        )
        # no temp files left behind
        assert list(tmp_path.glob("*.tmp*")) == []


# --------------------------------------------------------------------------
# Resume semantics
# --------------------------------------------------------------------------


class TestNextPending:
    def test_cached_skipped_failed_and_pending_kept(self, tmp_path: Path):
        path = tmp_path / "manifest.json"
        manifest = load_or_init_manifest(path)
        record_result(
            manifest,
            path,
            _song("cached_song", "111111111111"),
            status=CacheStatus.CACHED,
            source="r2_vocals_dry",
            audio="stems/clean_vocals.flac",
        )
        record_result(
            manifest,
            path,
            _song("fallback_song", "222222222222"),
            status=CacheStatus.FALLBACK,
            source="r2_vocals",
            audio="stems/vocals.flac",
        )
        songs = [
            _song("cached_song", "111111111111"),
            _song("fallback_song", "222222222222"),
            _song("failed_song", "333333333333"),
            _song("fresh_song", "444444444444"),
        ]
        record_result(
            manifest,
            path,
            songs[2],
            status=CacheStatus.FAILED,
            error="boom",
        )
        pending = next_pending(manifest, songs)
        assert [s.song_id for s in pending] == ["failed_song", "fresh_song"]

    def test_missing_local_file_requeues_terminal_entry(self, tmp_path: Path):
        """Manifest is committed to the repo; audio is machine-local. A
        terminal entry whose recorded audio is absent must re-resolve."""
        path = tmp_path / "manifest.json"
        manifest = load_or_init_manifest(path)
        record_result(
            manifest,
            path,
            _song("song_a", "aaaaaaaaaaaa"),
            status=CacheStatus.CACHED,
            source="r2_vocals_dry",
            audio="stems/clean_vocals.flac",
        )
        cache_root = tmp_path / "cache"
        songs = [_song("song_a", "aaaaaaaaaaaa")]
        # no file on disk: pending again
        assert next_pending(manifest, songs, cache_root=cache_root) == songs
        # file present but empty: pending again
        f = cache_root / "aaaaaaaaaaaa" / "stems" / "clean_vocals.flac"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"")
        assert next_pending(manifest, songs, cache_root=cache_root) == songs
        # file present with content: skipped
        f.write_bytes(b"flac")
        assert next_pending(manifest, songs, cache_root=cache_root) == []

    def test_empty_manifest_all_pending_in_order(self, tmp_path: Path):
        path = tmp_path / "manifest.json"
        manifest = load_or_init_manifest(path)
        songs = [_song(f"s{i}", f"{i:012d}") for i in range(3)]
        pending = next_pending(manifest, songs)
        assert [s.song_id for s in pending] == ["s0", "s1", "s2"]

    def test_hash_prefix_mismatch_is_error(self, tmp_path: Path):
        path = tmp_path / "manifest.json"
        manifest = load_or_init_manifest(path)
        # manifest claims a different prefix for the same song id
        manifest.songs["song_a"] = {
            "status": "pending",
            "hash_prefix": "ffffffffffff",
        }
        with pytest.raises(StemCacheError, match="hash_prefix"):
            next_pending(manifest, [_song("song_a", "aaaaaaaaaaaa")])


# --------------------------------------------------------------------------
# Serial lock
# --------------------------------------------------------------------------


class TestSerialLock:
    def test_second_concurrent_invocation_fails_loudly(self, tmp_path: Path):
        lock_path = tmp_path / "serial.lock"
        with (
            try_lock_serial(lock_path),
            pytest.raises(StemCacheError, match="already running"),
        ):
            # a second acquire while held must raise, not block
            try_lock_serial(lock_path).__enter__()

    def test_lock_released_after_context(self, tmp_path: Path):
        lock_path = tmp_path / "serial.lock"
        with try_lock_serial(lock_path):
            pass
        with try_lock_serial(lock_path):
            pass  # re-acquire fine after release


# --------------------------------------------------------------------------
# R2 lookup priority
# --------------------------------------------------------------------------


class TestR2Lookup:
    def _r2_with(self, existing_keys: set[str]):
        r2 = MagicMock()
        r2.file_exists.side_effect = lambda key: key in existing_keys
        return r2

    def test_prefers_vocals_dry_flac(self):
        r2 = self._r2_with({"aaaaaaaaaaaa/stems/vocals_dry.flac"})
        result = lookup_r2_clean_vocals(r2, "aaaaaaaaaaaa")
        assert result == ("vocals_dry", "stems/vocals_dry.flac")

    def test_falls_back_to_legacy_vocals_clean(self):
        r2 = self._r2_with({"aaaaaaaaaaaa/stems/vocals_clean.flac"})
        result = lookup_r2_clean_vocals(r2, "aaaaaaaaaaaa")
        assert result == ("vocals_clean", "stems/vocals_clean.flac")

    def test_falls_back_to_wet_vocals_as_explicit_fallback(self):
        r2 = self._r2_with({"aaaaaaaaaaaa/stems/vocals.flac"})
        result = lookup_r2_clean_vocals(r2, "aaaaaaaaaaaa")
        assert result == ("vocals", "stems/vocals.flac")

    def test_wet_vocals_wav_accepted_last(self):
        r2 = self._r2_with({"aaaaaaaaaaaa/stems/vocals.wav"})
        result = lookup_r2_clean_vocals(r2, "aaaaaaaaaaaa")
        assert result == ("vocals_wav", "stems/vocals.wav")

    def test_none_when_no_stems(self):
        r2 = self._r2_with(set())
        assert lookup_r2_clean_vocals(r2, "aaaaaaaaaaaa") is None


# --------------------------------------------------------------------------
# MVSEP result naming
# --------------------------------------------------------------------------


class TestPickDryVocals:
    def test_prefers_no_reverb_named_file(self, tmp_path: Path):
        from stem_cache import pick_dry_vocals

        dry = tmp_path / "vocals_(No Reverb).flac"
        wet = tmp_path / "vocals.flac"
        assert pick_dry_vocals([wet, dry]) == dry

    def test_falls_back_to_first_file(self, tmp_path: Path):
        from stem_cache import pick_dry_vocals

        only = tmp_path / "mystery_output.flac"
        assert pick_dry_vocals([only]) == only

    def test_none_on_empty(self, tmp_path: Path):
        from stem_cache import pick_dry_vocals

        assert pick_dry_vocals([]) is None
