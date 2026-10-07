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
        refs, missing = build_song_refs(
            conn, ["song_a"], known_prefixes={"song_a": "aaaaaaaaaaaa"}
        )
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
    monkeypatch.setattr(
        "stream_of_worship.admin.config.AdminConfig.load", lambda p: _FakeConfig()
    )
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

        _stub_run_infra(
            monkeypatch, r2=_ExplodingR2("R2 (resume must not touch)")
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

    def test_snapshot_deleted_song_recorded_failed_and_run_continues(
        self, tmp_path, monkeypatch
    ):
        """A snapshot song deleted from the DB since Phase 0a gets its own
        FAILED manifest record and does NOT abort the remaining songs
        (issue #247 acceptance: fallback-recorded, resumable)."""
        manifest_path = tmp_path / "manifest.json"
        cache_root = tmp_path / "cache"

        r2 = MagicMock()
        r2.file_exists.side_effect = lambda key: key.endswith(
            "aaaaaaaaaaaa/stems/vocals_dry.flac"
        )
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
