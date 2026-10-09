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

import run_stem_cache as rsc
from run_stem_cache import (
    PHASE3_SAMPLE_SIZE,
    PHASE12_SET,
    SEED,
    build_song_refs,
    load_positive_ids,
    load_review_queue_ids,
    process_song,
    run as run_stem_cache,
    sample_positive,
)
from stem_cache import (
    CacheStatus,
    SongRef,
    StemCacheError,
    load_or_init_manifest,
    record_result,
    save_manifest,
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
# Scale-out populations (issue #247)
# --------------------------------------------------------------------------


def _write_positive_list(tmp_path: Path, ids: list[str]) -> Path:
    truth_dir = tmp_path / "truth"
    truth_dir.mkdir(parents=True, exist_ok=True)
    (truth_dir / "positive.txt").write_text("\n".join(ids) + "\n", encoding="utf-8")
    return truth_dir


def _write_snapshot(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    snapshot = tmp_path / "latest.json"
    snapshot.write_text(json.dumps({"review_queue": rows or []}), encoding="utf-8")
    return snapshot


class TestLoadPositiveIds:
    def test_reads_positive_txt_from_truth_dir(self, tmp_path: Path):
        ids = ["a", "b", "c"]
        truth_dir = _write_positive_list(tmp_path, ids)
        assert load_positive_ids(truth_dir) == ids

    def test_skips_blank_lines(self, tmp_path: Path):
        truth_dir = _write_positive_list(tmp_path, ["a", "", "b"])
        assert load_positive_ids(truth_dir) == ["a", "b"]

    def test_empty_list_raises(self, tmp_path: Path):
        truth_dir = _write_positive_list(tmp_path, [])
        with pytest.raises(StemCacheError, match="empty"):
            load_positive_ids(truth_dir)

    def test_live_positive_snapshot_within_sample_bound(self):
        """The real positive.txt population must parse; today it is ≤60 songs
        (all of it becomes the Phase 3 sample, no sampling needed)."""
        ids = load_positive_ids()
        assert len(ids) < 399
        assert all("_" in i for i in ids)


class TestLoadReviewQueueIds:
    def test_reads_song_id_and_hash_prefix_pairs(self, tmp_path: Path):
        rows = [
            {"song_id": "s1", "hash_prefix": "aaaaaaaaaaaa", "lrc_source": None},
            {"song_id": "s2", "hash_prefix": "bbbbbbbbbbbb", "lrc_source": "youtube_transcript"},
        ]
        snapshot = _write_snapshot(tmp_path, rows)
        assert load_review_queue_ids(snapshot) == [("s1", "aaaaaaaaaaaa"), ("s2", "bbbbbbbbbbbb")]

    def test_empty_review_queue_raises(self, tmp_path: Path):
        snapshot = _write_snapshot(tmp_path, [])
        with pytest.raises(StemCacheError, match="no review_queue"):
            load_review_queue_ids(snapshot)

    def test_missing_key_raises(self, tmp_path: Path):
        snapshot = tmp_path / "latest.json"
        snapshot.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
        with pytest.raises(StemCacheError, match="no review_queue"):
            load_review_queue_ids(snapshot)

    def test_missing_hash_prefix_raises(self, tmp_path: Path):
        snapshot = _write_snapshot(tmp_path, [{"song_id": "s1", "hash_prefix": None}])
        with pytest.raises(StemCacheError, match="missing song_id/hash_prefix"):
            load_review_queue_ids(snapshot)

    def test_duplicate_song_id_raises(self, tmp_path: Path):
        rows = [
            {"song_id": "s1", "hash_prefix": "aaaaaaaaaaaa"},
            {"song_id": "s1", "hash_prefix": "bbbbbbbbbbbb"},
        ]
        snapshot = _write_snapshot(tmp_path, rows)
        with pytest.raises(StemCacheError, match="duplicate"):
            load_review_queue_ids(snapshot)

    def test_live_snapshot_parses_and_is_nonempty(self):
        """The real Phase 0a snapshot (eval/lrc_truth/latest.json) must parse;
        spec measured 399 but the live snapshot count is authoritative
        (issue #243), so the count is only sanity-bounded here."""
        refs = load_review_queue_ids()
        assert refs
        assert all(song_id and hash_prefix for song_id, hash_prefix in refs)


class TestSamplePositive:
    def test_at_or_below_limit_returns_all_in_order(self):
        ids = ["s3", "s1", "s2"]
        assert sample_positive(ids, seed=SEED, sample_size=PHASE3_SAMPLE_SIZE) == ids

    def test_exactly_limit_returns_all(self):
        ids = [f"s{i}" for i in range(PHASE3_SAMPLE_SIZE)]
        assert sample_positive(ids) == ids

    def test_above_limit_samples_exact_size(self):
        ids = [f"s{i}" for i in range(100)]
        out = sample_positive(ids)
        assert len(out) == PHASE3_SAMPLE_SIZE
        assert set(out) <= set(ids)
        assert len(set(out)) == len(out)

    def test_deterministic_for_fixed_seed(self):
        ids = [f"s{i}" for i in range(100)]
        assert sample_positive(ids, seed=7) == sample_positive(ids, seed=7)

    def test_different_seed_can_differ(self):
        ids = [f"s{i}" for i in range(100)]
        assert sample_positive(ids, seed=SEED) != sample_positive(ids, seed=SEED + 1)


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
        refs, missing = build_song_refs(conn, ["song_a", "song_b"])
        assert refs == [
            ("song_a", "aaaaaaaaaaaa"),
            ("song_b", "bbbbbbbbbbbb"),
        ]
        assert missing == []

    def test_missing_song_raises_without_known_prefixes(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [("song_a", "aaaaaaaaaaaa")]
        with pytest.raises(StemCacheError, match="song_b"):
            build_song_refs(conn, ["song_a", "song_b"])

    def test_known_prefix_match_passes_through(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [("song_a", "aaaaaaaaaaaa")]
        refs, missing = build_song_refs(conn, ["song_a"], known_prefixes={"song_a": "aaaaaaaaaaaa"})
        assert refs == [("song_a", "aaaaaaaaaaaa")]
        assert missing == []

    def test_known_prefix_mismatch_raises(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [("song_a", "aaaaaaaaaaaa")]
        with pytest.raises(StemCacheError, match="mismatch"):
            build_song_refs(conn, ["song_a"], known_prefixes={"song_a": "zzzzzzzzzzzz"})

    def test_known_prefix_missing_song_returned_not_raised(self):
        """Verification mode (issue #247): a snapshot song deleted from the
        DB comes back in *missing* so the runner records per-song FAILED and
        continues; only implicit-population mode raises."""
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [("song_a", "aaaaaaaaaaaa")]
        refs, missing = build_song_refs(
            conn, ["song_a", "song_b"], known_prefixes={"song_b": "bbbbbbbbbbbb"}
        )
        assert refs == [("song_a", "aaaaaaaaaaaa")]
        assert missing == ["song_b"]


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


class _ExplodingR2:
    """R2 stub whose every method raises (assertion by default)."""

    def __init__(self, message_prefix: str = "R2") -> None:
        self._message_prefix = message_prefix

    def __getattr__(self, name: str):
        def _boom(*a, **k):
            raise AssertionError(f"{self._message_prefix}.{name} must not be touched")

        return _boom


class _FakeConn:
    def cursor(self):
        raise AssertionError("DB must not be touched")


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
        # _FakeConn.cursor (and build_song_refs is stubbed per test).
        return "postgresql://unused"


def _stub_run_infra(monkeypatch, r2=None):
    """Patch AdminConfig/ConnectionProvider/R2Client so run() touches no
    real config file, DB, or R2. Pass r2= to customize the R2 stub."""
    monkeypatch.setattr("stream_of_worship.admin.config.AdminConfig.load", lambda p: _FakeConfig())
    monkeypatch.setattr(
        "stream_of_worship.db.connection.ConnectionProvider",
        lambda url: _FakeProvider(),
    )
    monkeypatch.setattr(
        "stream_of_worship.admin.services.r2.R2Client",
        lambda **k: r2 if r2 is not None else _ExplodingR2(),
    )
    monkeypatch.setattr(
        "run_stem_cache.build_song_refs",
        lambda conn, ids, known_prefixes=None: ([(sid, "aaaaaaaaaaaa") for sid in ids], []),
    )


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
        # the recorded audio must exist locally for resume to skip it
        cached_file = cache_root / "aaaaaaaaaaaa" / "stems" / "clean_vocals.flac"
        cached_file.parent.mkdir(parents=True, exist_ok=True)
        cached_file.write_bytes(b"flac-bytes")
        before = json.loads(manifest_path.read_text(encoding="utf-8"))

        _stub_run_infra(monkeypatch, r2=_ExplodingR2("R2 (resume must not touch)"))

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

    def test_stale_terminal_entry_requeued_and_failed_without_run_abort(
        self, tmp_path, monkeypatch
    ):
        """A terminal entry whose local file vanished is re-resolved; if
        resolution fails, the run continues and the manifest keeps the
        terminal status (no downgrade to failed)."""
        import json

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
        # no file materialized -> requeued, then R2 explodes -> per-song failure

        class _RuntimeExplodingR2:
            def __getattr__(self, name):
                def _boom(*a, **k):
                    raise RuntimeError("R2 unreachable")

                return _boom

        _stub_run_infra(monkeypatch, r2=_RuntimeExplodingR2())

        summary = run_cache(
            song_ids=["song_a"],
            cache_root=cache_root,
            manifest_path=manifest_path,
            lock_path=tmp_path / "serial.lock",
            config_path=None,
            dry_run=False,
        )
        assert summary.failed == 1  # recorded as failed for this run...
        after = json.loads(manifest_path.read_text(encoding="utf-8"))
        # ...but the terminal entry survives (no clobbering) modulo resolved_at
        assert after["songs"]["song_a"]["status"] == "cached"
        assert after["songs"]["song_a"]["source"] == before["songs"]["song_a"]["source"]


# --------------------------------------------------------------------------
# Runner scale-out (issue #247): refs override from the Phase 0a snapshot
# --------------------------------------------------------------------------


class TestRunnerScaleOutRefs:
    def test_snapshot_refs_run_and_manifest_recs_keyed_by_song_id(self, tmp_path, monkeypatch):
        """run(refs=...) processes the snapshot songs: both resolve via the
        R2 dry-stem chain with no MVSEP, and their statuses land in the
        manifest under the given song_ids."""
        cache_root = tmp_path / "cache"
        manifest, manifest_path = _make_ctx(cache_root, tmp_path)

        r2 = MagicMock()
        r2.file_exists.side_effect = lambda key: key.endswith(
            ("aaaaaaaaaaaa/stems/vocals_dry.flac", "bbbbbbbbbbbb/stems/vocals_dry.flac")
        )
        r2.download_file.side_effect = lambda key, dest: dest.write_bytes(b"flac")

        # snapshot provides known prefixes matching what build_song_refs sees
        _stub_run_infra(monkeypatch, r2=r2)

        monkeypatch.setattr(
            rsc,
            "build_song_refs",
            lambda conn, ids, known_prefixes=None: (
                [(sid, known_prefixes[sid]) for sid in ids],
                [],
            ),
        )

        summary = run_stem_cache(
            song_ids=["s1", "s2"],
            cache_root=cache_root,
            manifest_path=manifest_path,
            lock_path=tmp_path / "serial.lock",
            config_path=None,
            dry_run=False,
            override_refs=[("s1", "aaaaaaaaaaaa"), ("s2", "bbbbbbbbbbbb")],
        )
        assert summary.cached == 2
        assert summary.failed == 0
        # run() reloads the manifest internally; assert on the persisted state
        persisted = json.loads(manifest_path.read_text(encoding="utf-8"))["songs"]
        assert set(persisted) == {"s1", "s2"}
        # entries carry the snapshot's own hash_prefixes
        assert persisted["s1"]["hash_prefix"] == "aaaaaaaaaaaa"
        assert persisted["s2"]["hash_prefix"] == "bbbbbbbbbbbb"

    def test_snapshot_ref_mismatch_fails_run_without_manifest_writes(self, tmp_path, monkeypatch):
        """A snapshot prefix disagreeing with the live DB aborts loudly
        before any song is processed (fail-closed, no partial cache)."""

        _stub_run_infra(monkeypatch, r2=_ExplodingR2())
        monkeypatch.setattr(
            rsc,
            "build_song_refs",
            MagicMock(side_effect=StemCacheError("hash_prefix mismatch between snapshot")),
        )

        with pytest.raises(StemCacheError, match="mismatch"):
            run_stem_cache(
                song_ids=["s1"],
                cache_root=tmp_path / "cache",
                manifest_path=tmp_path / "manifest.json",
                lock_path=tmp_path / "serial.lock",
                config_path=None,
                dry_run=False,
                override_refs=[("s1", "aaaaaaaaaaaa")],
            )

    def test_refs_none_keeps_db_resolved_path(self, tmp_path, monkeypatch):
        """refs=None (phase12 default) must still resolve prefixes via the
        plain DB query, unchanged from issue #244 behavior."""

        calls = []

        def fake_build(conn, ids, known_prefixes=None):
            calls.append((list(ids), known_prefixes))
            return [(sid, "aaaaaaaaaaaa") for sid in ids], []

        _stub_run_infra(monkeypatch, r2=_ExplodingR2())  # explodes only if processed
        monkeypatch.setattr(rsc, "build_song_refs", fake_build)

        summary = run_stem_cache(
            song_ids=["song_a"],
            cache_root=tmp_path / "cache",
            manifest_path=tmp_path / "manifest.json",
            lock_path=tmp_path / "serial.lock",
            config_path=None,
            dry_run=True,
        )
        assert summary.skipped == 0
        assert calls == [(["song_a"], None)]

    def test_snapshot_deleted_song_recorded_failed_and_run_continues(self, tmp_path, monkeypatch):
        """A snapshot song deleted from the DB since Phase 0a gets its own
        FAILED manifest record and does NOT abort the remaining songs
        (issue #247 acceptance: fallback-recorded, resumable)."""
        manifest_path = tmp_path / "manifest.json"
        cache_root = tmp_path / "cache"

        r2 = MagicMock()
        r2.file_exists.side_effect = lambda key: key.endswith("aaaaaaaaaaaa/stems/vocals_dry.flac")
        r2.download_file.side_effect = lambda key, dest: dest.write_bytes(b"flac")

        _stub_run_infra(monkeypatch, r2=r2)
        monkeypatch.setattr(
            rsc,
            "build_song_refs",
            lambda conn, ids, known_prefixes=None: (
                [(sid, known_prefixes[sid]) for sid in ids if sid != "s_gone"],
                ["s_gone"],
            ),
        )

        summary = run_stem_cache(
            song_ids=["s_gone", "s1"],
            cache_root=cache_root,
            manifest_path=manifest_path,
            lock_path=tmp_path / "serial.lock",
            config_path=None,
            dry_run=False,
            override_refs=[("s_gone", "deadbeefdead"), ("s1", "aaaaaaaaaaaa")],
        )
        assert summary.failed == 1  # only the deleted song
        assert summary.cached == 1  # s1 still resolved
        persisted = json.loads(manifest_path.read_text(encoding="utf-8"))["songs"]
        assert persisted["s_gone"]["status"] == "failed"
        assert "deleted since snapshot" in persisted["s_gone"]["error"]
        assert persisted["s1"]["status"] == "cached"

    def test_snapshot_dry_run_writes_nothing_for_deleted_songs(self, tmp_path, monkeypatch):
        """--dry-run is write-free even when snapshot songs were deleted from
        the DB: no FAILED record may be persisted for them."""
        manifest_path = tmp_path / "manifest.json"

        _stub_run_infra(monkeypatch, r2=_ExplodingR2())
        monkeypatch.setattr(
            rsc,
            "build_song_refs",
            lambda conn, ids, known_prefixes=None: ([], ["s_gone"]),
        )

        summary = run_stem_cache(
            song_ids=["s_gone"],
            cache_root=tmp_path / "cache",
            manifest_path=manifest_path,
            lock_path=tmp_path / "serial.lock",
            config_path=None,
            dry_run=True,
            override_refs=[("s_gone", "deadbeefdead")],
        )
        assert summary.failed == 0
        assert not manifest_path.exists() or "s_gone" not in json.loads(
            manifest_path.read_text(encoding="utf-8")
        ).get("songs", {})

    def test_snapshot_deleted_song_already_cached_does_not_abort(self, tmp_path, monkeypatch):
        """A snapshot song deleted from the DB after being cached in an earlier
        run must not raise through record_result's terminal guard — the run
        continues and keeps the cached entry (no wholesale abort)."""
        manifest_path = tmp_path / "manifest.json"
        cache_root = tmp_path / "cache"
        stems = cache_root / "aaaaaaaaaaaa" / "stems"
        stems.mkdir(parents=True)
        (stems / "clean_vocals.flac").write_bytes(b"flac")

        r2 = MagicMock()
        r2.file_exists.return_value = False
        _stub_run_infra(monkeypatch, r2=r2)
        monkeypatch.setattr(
            rsc,
            "build_song_refs",
            lambda conn, ids, known_prefixes=None: ([], ["s_cached_gone"]),
        )

        manifest = load_or_init_manifest(manifest_path)
        manifest.songs["s_cached_gone"] = {
            "status": "cached",
            "hash_prefix": "aaaaaaaaaaaa",
            "resolved_at": "2026-01-01T00:00:00+00:00",
            "source": "r2_vocals_dry",
            "audio": "stems/clean_vocals.flac",
        }
        save_manifest(manifest, manifest_path)

        summary = run_stem_cache(
            song_ids=["s_cached_gone"],
            cache_root=cache_root,
            manifest_path=manifest_path,
            lock_path=tmp_path / "serial.lock",
            config_path=None,
            dry_run=False,
            override_refs=[("s_cached_gone", "aaaaaaaaaaaa")],
        )
        persisted = json.loads(manifest_path.read_text(encoding="utf-8"))["songs"]
        # terminal entry preserved, not downgraded to failed
        assert persisted["s_cached_gone"]["status"] == "cached"
        assert summary.failed == 1  # counted, but the run survived

    def test_quota_exhaustion_stops_pass_instead_of_walking_all_songs(self, tmp_path, monkeypatch):
        """When MVSEP reports the daily wall, the pass must stop instead of
        attempting (and recording FAILED for) every remaining song."""
        manifest_path = tmp_path / "manifest.json"

        r2 = MagicMock()
        r2.file_exists.return_value = False
        r2.download_audio.side_effect = lambda prefix, dest: dest.write_bytes(b"mp3")
        _stub_run_infra(monkeypatch, r2=r2)
        monkeypatch.setattr(
            rsc,
            "build_song_refs",
            lambda conn, ids, known_prefixes=None: (
                [(sid, "aaaaaaaaaaaa") for sid in ids],
                [],
            ),
        )

        calls = []

        def quota_wall(audio_path, output_dir, api_token):
            calls.append(audio_path.name)
            raise RuntimeError(
                'MVSEP submit HTTP 400: {"success":false,"errors":'
                '["You have reached the limit of separations for today."]}'
            )

        monkeypatch.setattr(rsc, "_mvsep_separate", quota_wall)
        monkeypatch.setenv("MVSEP_API_KEY", "test-token")

        song_ids = [f"s{i}" for i in range(5)]
        summary = run_stem_cache(
            song_ids=song_ids,
            cache_root=tmp_path / "cache",
            manifest_path=manifest_path,
            lock_path=tmp_path / "serial.lock",
            config_path=None,
            dry_run=False,
        )
        assert len(calls) == 1, "only the first song may be attempted"
        assert summary.failed == 1
        persisted = json.loads(manifest_path.read_text(encoding="utf-8"))["songs"]
        assert set(persisted) == {"s0"}

    def test_quota_marker_matching_is_specific(self):
        """Only genuine quota text triggers the stop; other 400s do not."""
        assert rsc._is_quota_exhausted_error(
            RuntimeError("You have reached the limit of separations for today")
        )
        assert not rsc._is_quota_exhausted_error(
            RuntimeError("MVSEP submit HTTP 400: invalid api_token")
        )


# --------------------------------------------------------------------------
# Local separation backend (issue #247 --local)
# --------------------------------------------------------------------------


class TestLocalBackend:
    def test_process_song_records_local_source_and_producer(self, tmp_path: Path):
        """--local flow: process_song records source='local' and the local
        model producer, and consumes pick_dry_vocals on the wrapped outputs."""
        cache_root = tmp_path / "cache"
        manifest, manifest_path = _make_ctx(cache_root, tmp_path)

        r2 = MagicMock()
        r2.file_exists.return_value = False
        r2.download_audio.side_effect = lambda prefix, dest: dest.write_bytes(b"mp3")

        dry = tmp_path / "vocals_(No Echo).flac"
        dry.write_bytes(b"clean")
        mvsep = MagicMock(return_value=[dry])

        _song_id, status, source = process_song(
            SongRef("song_a", "aaaaaaaaaaaa"),
            cache_root=cache_root,
            manifest=manifest,
            manifest_path=manifest_path,
            r2_client=r2,
            mvsep_fn=mvsep,
            sep_source="local",
            sep_producer="local_audio_separator_mel_band_ep_3005",
        )
        assert status == CacheStatus.CACHED
        assert source == "local"
        stored = manifest.songs["song_a"]
        assert stored["source"] == "local"
        assert stored["producer"] == "local_audio_separator_mel_band_ep_3005"

    def test_run_local_missing_model_dir_fails_loudly(self, tmp_path, monkeypatch):
        """run(local=True) without a model dir errors before any song is
        processed (early check, not per-song import failures)."""
        from run_stem_cache import run as run_cache

        monkeypatch.setenv("AUDIO_SEPARATOR_MODEL_DIR", str(tmp_path / "nonexistent-models"))
        _stub_run_infra(monkeypatch, r2=_ExplodingR2())

        with pytest.raises(StemCacheError, match="model dir"):
            run_cache(
                song_ids=["song_a"],
                cache_root=tmp_path / "cache",
                manifest_path=tmp_path / "manifest.json",
                lock_path=tmp_path / "serial.lock",
                config_path=None,
                dry_run=False,
                local=True,
            )

    def test_module_constants_match_prod_models(self):
        """Local models must mirror the prod analysis-service config so the
        local product is the same stem the R2 fallback records."""
        from run_stem_cache import (
            LOCAL_DEREVERB_MODEL,
            LOCAL_VOCAL_MODEL,
        )

        assert LOCAL_VOCAL_MODEL == "model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt"
        assert LOCAL_DEREVERB_MODEL == "UVR-De-Echo-Normal.pth"


class TestLocalSeparate:
    def test_relative_stage2_outputs_resolved_against_stage_dir(self, tmp_path, monkeypatch):
        """Regression: audio-separator returns bare filenames for outputs;
        unresolved relative paths made fully-separated songs record FAILED
        after 75 min of compute (read_bytes() from the runner CWD)."""
        import run_stem_cache as rsc

        output_dir = tmp_path / "song"
        stage2 = output_dir / "stage2_dereverb"
        stage2.mkdir(parents=True)
        dry = stage2 / "audio_(Vocals)_(No Echo)_UVR-De-Echo-Normal.flac"
        dry.write_bytes(b"clean")

        def fake_extract(audio_path, out_dir, vocal_model, dereverb_model):
            assert out_dir == output_dir
            return {
                "stages": {
                    "stage1": {"outputs": [str(out_dir / "stage1_vocal_separation" / "v.flac")]},
                    "stage2": {
                        # bare filename, as separator.separate() returns
                        "outputs": ["audio_(Vocals)_(No Echo)_UVR-De-Echo-Normal.flac"]
                    },
                }
            }

        monkeypatch.setattr("poc.gen_clean_vocal_stem.extract_vocals_two_stage", fake_extract)
        env_dir = tmp_path / "models"
        env_dir.mkdir()
        monkeypatch.setenv("AUDIO_SEPARATOR_MODEL_DIR", str(env_dir))

        outs = rsc._local_separate(tmp_path / "in.mp3", output_dir, env_dir)
        assert dry in outs

    def test_model_dir_env_set_for_cached_models(self, tmp_path, monkeypatch):
        """_local_separate must point audio-separator at the machine model
        cache via AUDIO_SEPARATOR_MODEL_DIR (default /tmp would re-download
        the 1GB MelBand ckpt)."""
        import os as _os

        import run_stem_cache as rsc

        env_dir = tmp_path / "models"
        env_dir.mkdir()
        monkeypatch.setenv("AUDIO_SEPARATOR_MODEL_DIR", str(env_dir))
        seen = {}

        def fake_extract(audio_path, out_dir, vocal_model, dereverb_model):
            seen["env"] = _os.environ.get("AUDIO_SEPARATOR_MODEL_DIR")
            return {"stages": {"stage1": {"outputs": []}, "stage2": {"outputs": []}}}

        monkeypatch.setattr("poc.gen_clean_vocal_stem.extract_vocals_two_stage", fake_extract)
        rsc._local_separate(tmp_path / "in.mp3", tmp_path / "out", env_dir)
        assert seen["env"] == str(env_dir)


# --------------------------------------------------------------------------
# Dual backend: local producer + MVSEP recorder (issue #247)
# --------------------------------------------------------------------------


class TestDualBackendCoordination:
    """The local worker must never write the manifest (single-writer), and the
    recorder must fold its output in without spending MVSEP quota."""

    def _stub(self, monkeypatch, tmp_path, song_ids):
        # Distinct prefix per song: songs must not share a cache directory,
        # or one song's stem would masquerade as another's.
        prefix = {sid: f"{i:012x}" for i, sid in enumerate(song_ids)}
        monkeypatch.setattr(
            rsc,
            "build_song_refs",
            lambda conn, ids, known_prefixes=None: (
                [(sid, prefix[sid]) for sid in ids],
                [],
            ),
        )
        self._prefix = prefix
        monkeypatch.setattr(
            "stream_of_worship.admin.config.AdminConfig.load", lambda p: _FakeConfig()
        )
        monkeypatch.setattr(
            "stream_of_worship.db.connection.ConnectionProvider",
            lambda url: _FakeProvider(),
        )
        r2 = MagicMock()
        r2.download_audio.side_effect = lambda prefix, dest: dest.write_bytes(b"mp3")
        monkeypatch.setattr("stream_of_worship.admin.services.r2.R2Client", lambda **k: r2)

    def test_produce_only_writes_no_manifest_and_claims_the_song(self, tmp_path, monkeypatch):
        """--produce-only: stem + claim land in the cache; the manifest is
        untouched, and the producer runs without taking the serial lock."""
        cache_root = tmp_path / "cache"
        manifest_path = tmp_path / "manifest.json"
        self._stub(monkeypatch, tmp_path, ["s1"])

        dry = tmp_path / "vocals_(No Echo).flac"
        dry.write_bytes(b"clean-vocals")
        monkeypatch.setattr(rsc, "_local_separate", lambda a, o, m: [dry])
        monkeypatch.setenv("AUDIO_SEPARATOR_MODEL_DIR", str(tmp_path))

        summary = rsc.run_produce_only(
            song_ids=["s1"],
            cache_root=cache_root,
            manifest_path=manifest_path,
            config_path=None,
        )

        assert summary.cached == 1
        hp = self._prefix["s1"]
        assert (cache_root / hp / "stems" / "clean_vocals.flac").read_bytes() == b"clean-vocals"
        claim = rsc.read_claim(cache_root / hp)
        assert claim and claim["producer"] == rsc.LOCAL_MODEL_PRODUCER
        # the single-writer invariant: the producer never writes the manifest,
        # not even a missing-file skeleton
        assert not manifest_path.exists()

    def test_recorder_folds_claims_into_manifest_without_separation(self, tmp_path, monkeypatch):
        """--record-claims records a produced stem as cached/local — no MVSEP
        call, no R2 I/O beyond ref resolution."""
        cache_root = tmp_path / "cache"
        manifest_path = tmp_path / "manifest.json"
        self._stub(monkeypatch, tmp_path, ["s1"])
        song_dir = cache_root / self._prefix["s1"]
        (song_dir / "stems").mkdir(parents=True)
        (song_dir / "stems" / "clean_vocals.flac").write_bytes(b"clean-vocals")
        rsc.write_claim(song_dir, hash_prefix=self._prefix["s1"], producer="local_x")
        monkeypatch.setattr(rsc, "_local_separate", lambda *a: pytest.fail("must not separate"))

        summary = rsc.record_claims(
            song_ids=["s1"],
            cache_root=cache_root,
            manifest_path=manifest_path,
            config_path=None,
        )
        assert summary.cached == 1
        persisted = json.loads(manifest_path.read_text(encoding="utf-8"))["songs"]
        assert persisted["s1"]["status"] == "cached"
        assert persisted["s1"]["source"] == "local"
        assert persisted["s1"]["producer"] == "local_x"

    def test_recorder_skips_songs_without_stems(self, tmp_path, monkeypatch):
        """Nothing produced -> nothing recorded (no FAILED noise)."""
        cache_root = tmp_path / "cache"
        manifest_path = tmp_path / "manifest.json"
        self._stub(monkeypatch, tmp_path, ["s1"])

        summary = rsc.record_claims(
            song_ids=["s1"],
            cache_root=cache_root,
            manifest_path=manifest_path,
            config_path=None,
        )
        assert summary.cached == 0 and summary.failed == 0
        assert not manifest_path.exists(), "recorder must not create the manifest"

    def test_drain_records_claim_provenance_on_disk_hit(self, tmp_path, monkeypatch):
        """The MVSEP drain's step-1 disk check picks up a locally produced,
        unrecorded stem at zero quota — recording the producer from the claim."""
        cache_root = tmp_path / "cache"
        song_dir = cache_root / "aaaaaaaaaaaa"
        (song_dir / "stems").mkdir(parents=True)
        (song_dir / "stems" / "clean_vocals.flac").write_bytes(b"clean-vocals")
        rsc.write_claim(
            song_dir, hash_prefix="aaaaaaaaaaaa", producer="local_audio_separator_mel_band_ep_3005"
        )

        manifest, manifest_path = _make_ctx(cache_root, tmp_path)
        mvsep = MagicMock(side_effect=AssertionError("must not spend quota"))
        r2 = MagicMock()
        r2.file_exists.return_value = False

        _sid, status, source = process_song(
            SongRef("s1", "aaaaaaaaaaaa"),
            cache_root=cache_root,
            manifest=manifest,
            manifest_path=manifest_path,
            r2_client=r2,
            mvsep_fn=mvsep,
            sep_source="mvsep",
            sep_producer="mvsep_x",
        )
        assert status == CacheStatus.CACHED
        assert mvsep.call_count == 0
        stored = manifest.songs["s1"]
        assert stored["source"] == "local"
        assert stored["producer"] == "local_audio_separator_mel_band_ep_3005"

    def test_produce_only_skips_already_recorded_and_claimed_songs(self, tmp_path, monkeypatch):
        """Re-running the producer never redoes work: recorded songs and
        already-claimed stems are skipped, so the two backends can't
        double-separate a song."""
        cache_root = tmp_path / "cache"
        manifest_path = tmp_path / "manifest.json"
        self._stub(monkeypatch, tmp_path, ["s_done", "s_claimed", "s_new"])

        manifest = load_or_init_manifest(manifest_path)
        record_result(
            manifest,
            manifest_path,
            SongRef("s_done", self._prefix["s_done"]),
            status=CacheStatus.CACHED,
            source="r2_vocals_dry",
            audio="stems/clean_vocals.flac",
        )
        claimed = cache_root / self._prefix["s_claimed"]
        (claimed / "stems").mkdir(parents=True)
        (claimed / "stems" / "clean_vocals.flac").write_bytes(b"x")
        rsc.write_claim(claimed, hash_prefix=self._prefix["s_claimed"], producer="local_x")

        separates = []

        def fake_local(audio_path, out_dir, model_dir):
            separates.append(audio_path)
            dry = tmp_path / "dry.flac"
            dry.write_bytes(b"new")
            return [dry]

        monkeypatch.setattr(rsc, "_local_separate", fake_local)
        monkeypatch.setenv("AUDIO_SEPARATOR_MODEL_DIR", str(tmp_path))

        summary = rsc.run_produce_only(
            song_ids=["s_done", "s_claimed", "s_new"],
            cache_root=cache_root,
            manifest_path=manifest_path,
            config_path=None,
        )
        assert len(separates) == 1, "only the un-produced song may separate"
        assert summary.skipped == 2
        assert summary.cached == 1

    def test_mvsep_stems_get_no_claim_but_local_stems_do(self, tmp_path):
        """Claims mark local provenance only. An MVSEP-produced stem must not
        carry one, or a later disk-hit would relabel it source='local'."""
        for sep_source, expect_claim in (("mvsep", False), ("local", True)):
            cache_root = tmp_path / f"cache_{sep_source}"
            manifest, manifest_path = _make_ctx(cache_root, tmp_path / f"mf_{sep_source}")
            r2 = MagicMock()
            r2.file_exists.return_value = False
            r2.download_audio.side_effect = lambda prefix, dest: dest.write_bytes(b"mp3")
            dry = tmp_path / f"dry_{sep_source}.flac"
            dry.write_bytes(b"clean")
            process_song(
                SongRef("s1", "aaaaaaaaaaaa"),
                cache_root=cache_root,
                manifest=manifest,
                manifest_path=manifest_path,
                r2_client=r2,
                mvsep_fn=MagicMock(return_value=[dry]),
                sep_source=sep_source,
                sep_producer="p",
            )
            claim = rsc.read_claim(cache_root / "aaaaaaaaaaaa")
            assert (claim is not None) is expect_claim, sep_source

    def test_disk_hit_of_mvsep_stem_without_claim_is_not_labelled_local(self, tmp_path):
        """An unrecorded MVSEP stem on disk resolves as generic cached, never
        as a local-produced stem."""
        cache_root = tmp_path / "cache"
        song_dir = cache_root / "aaaaaaaaaaaa"
        (song_dir / "stems").mkdir(parents=True)
        (song_dir / "stems" / "clean_vocals.flac").write_bytes(b"clean")
        manifest, manifest_path = _make_ctx(cache_root, tmp_path)
        _sid, status, _src = process_song(
            SongRef("s1", "aaaaaaaaaaaa"),
            cache_root=cache_root,
            manifest=manifest,
            manifest_path=manifest_path,
            r2_client=MagicMock(),
            mvsep_fn=MagicMock(side_effect=AssertionError("no separation")),
        )
        assert status == CacheStatus.CACHED
        assert manifest.songs["s1"]["source"] == "local_clean_vocals"

    def test_producer_lock_is_distinct_from_drain_lock_and_exclusive(self, tmp_path, monkeypatch):
        """The producer must not contend with the drain (distinct lock) yet a
        second producer must fail loudly (no parallel local separation)."""
        assert rsc.DEFAULT_PRODUCE_LOCK != rsc.DEFAULT_LOCK
        cache_root = tmp_path / "cache"
        self._stub(monkeypatch, tmp_path, ["s1"])
        monkeypatch.setenv("AUDIO_SEPARATOR_MODEL_DIR", str(tmp_path))
        lock = tmp_path / "produce.lock"
        (tmp_path / "dry.flac").write_bytes(b"x")
        monkeypatch.setattr(
            rsc,
            "_local_separate",
            lambda a, o, m: [tmp_path / "dry.flac"],
        )

        # Hold the producer lock, then attempt a second producer run.
        with rsc.try_lock_serial(lock):
            with pytest.raises(StemCacheError, match="already running"):
                rsc.run_produce_only(
                    song_ids=["s1"],
                    cache_root=cache_root,
                    manifest_path=tmp_path / "manifest.json",
                    config_path=None,
                    produce_lock_path=lock,
                )
        # Lock released -> the same call now proceeds.
        summary = rsc.run_produce_only(
            song_ids=["s1"],
            cache_root=cache_root,
            manifest_path=tmp_path / "manifest.json",
            config_path=None,
            produce_lock_path=lock,
        )
        assert summary.cached == 1

    def test_recorder_does_not_fabricate_local_provenance_for_claimless_stems(
        self, tmp_path, monkeypatch
    ):
        """A pre-claim stem (older drain run, or a crash before write_claim) on
        disk is recorded as a generic on-disk hit — the manifest is committed
        experiment ground truth, so it must not claim local production."""
        cache_root = tmp_path / "cache"
        manifest_path = tmp_path / "manifest.json"
        self._stub(monkeypatch, tmp_path, ["s1"])
        song_dir = cache_root / self._prefix["s1"]
        (song_dir / "stems").mkdir(parents=True)
        (song_dir / "stems" / "clean_vocals.flac").write_bytes(b"stem")
        assert rsc.read_claim(song_dir) is None  # no claim: provenance unknown

        summary = rsc.record_claims(
            song_ids=["s1"],
            cache_root=cache_root,
            manifest_path=manifest_path,
            config_path=None,
        )
        assert summary.cached == 1
        entry = json.loads(manifest_path.read_text(encoding="utf-8"))["songs"]["s1"]
        assert entry["source"] == "local_clean_vocals"
        assert "producer" not in entry

    def test_producer_never_clobbers_a_stem_that_appeared_during_separation(
        self, tmp_path, monkeypatch
    ):
        """If the drain finishes a song while the local worker is separating it,
        the drain's stem (and its provenance) wins — the worker must not
        os.replace over it nor stamp a local claim."""
        cache_root = tmp_path / "cache"
        manifest_path = tmp_path / "manifest.json"
        self._stub(monkeypatch, tmp_path, ["s1"])
        monkeypatch.setenv("AUDIO_SEPARATOR_MODEL_DIR", str(tmp_path))
        stems_dir = cache_root / self._prefix["s1"] / "stems"

        def fake_local(audio_path, out_dir, model_dir):
            # The drain lands its stem mid-separation.
            stems_dir.mkdir(parents=True, exist_ok=True)
            (stems_dir / "clean_vocals.flac").write_bytes(b"MVSEP-PRODUCED")
            dry = tmp_path / "mine.flac"
            dry.write_bytes(b"MINE")
            return [dry]

        monkeypatch.setattr(rsc, "_local_separate", fake_local)

        summary = rsc.run_produce_only(
            song_ids=["s1"],
            cache_root=cache_root,
            manifest_path=manifest_path,
            config_path=None,
        )
        assert (stems_dir / "clean_vocals.flac").read_bytes() == b"MVSEP-PRODUCED"
        assert rsc.read_claim(stems_dir.parent) is None, "must not claim a stem it did not write"
        assert summary.cached == 0 and summary.skipped == 1
