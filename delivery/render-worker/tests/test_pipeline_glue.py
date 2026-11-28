import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from sow_render_worker.asset_fetcher import GlueDownloadError
from sow_render_worker.audio_engine import GlueClip, SongsetItem
from sow_render_worker.db import GlueTake, RenderJob
from sow_render_worker.pipeline import (
    GLUE_NOT_READY,
    GLUE_STALE_SNAPSHOT,
    GlueSnapshotError,
    load_render_glue_clips,
)

SHA256 = "b" * 64
DESCRIPTOR_KEY = "a" * 64
UPDATED_AT = datetime(2026, 11, 22, 10, 0, 0, tzinfo=timezone.utc)
UPDATED_AT_ISO = UPDATED_AT.isoformat()


def _item(item_id: str, position: int, **overrides) -> SongsetItem:
    defaults = {
        "id": item_id,
        "songset_id": "ss_001",
        "song_id": f"song-{item_id}",
        "song_title": f"Song {item_id}",
        "recording_hash_prefix": f"prefix-{item_id}",
        "position": position,
        "gap_beats": 2.0,
        "crossfade_enabled": 0,
        "crossfade_duration_seconds": None,
        "key_shift_semitones": 0,
        "tempo_ratio": 1.0,
        "tempo_bpm": 120.0,
        "duration_seconds": 180.0,
        "recording_content_hash": f"content-{item_id}",
        "deleted_at": None,
        "updated_at": UPDATED_AT,
        "arrangement_steps": None,
    }
    defaults.update(overrides)
    return SongsetItem(**defaults)


def _job(**overrides) -> RenderJob:
    defaults = {
        "id": "job-1",
        "songset_id": "ss_001",
        "user_id": 42,
        "status": "running",
        "template": "dark",
        "resolution": "720p",
        "audio_enabled": True,
        "video_enabled": False,
        "font_size_preset": "M",
        "font_family": "noto_serif_tc",
        "include_title_card": False,
        "title_card_duration_seconds": None,
    }
    defaults.update(overrides)
    return RenderJob(**defaults)


def _take(**overrides) -> GlueTake:
    defaults = {
        "id": "take-1",
        "user_id": 42,
        "descriptor_key": DESCRIPTOR_KEY,
        "descriptor_json": "{}",
        "ordinal": 1,
        "seed": 7,
        "status": "ready",
        "r2_key": f"glues/42/{DESCRIPTOR_KEY}/take-take-1.wav",
        "seconds": 2.786,
        "metrics_json": json.dumps(
            {"audioSha256": SHA256, "audioBytes": 1000}, sort_keys=True
        ),
        "reasons_json": "[]",
    }
    defaults.update(overrides)
    return GlueTake(**defaults)


def _entry(take: GlueTake, *, from_item_id: str = "item-1",
           to_item_id: str = "item-2", **overrides) -> dict:
    entry = {
        "from_item_id": from_item_id,
        "to_item_id": to_item_id,
        "take_id": take.id,
        "descriptor_key": take.descriptor_key,
        "r2_key": take.r2_key,
        "seconds": take.seconds,
        "sha256": SHA256,
        "from_updated_at": UPDATED_AT_ISO,
        "to_updated_at": UPDATED_AT_ISO,
    }
    entry.update(overrides)
    return entry


def _fetcher(path: str = "/tmp/cache/glue.wav") -> MagicMock:
    fetcher = MagicMock()
    fetcher.download_glue.return_value = path
    return fetcher


def _load(*, transitions, takes, items=None, fetcher=None, job=None):
    if items is None:
        items = [_item("item-1", 0), _item("item-2", 1), _item("item-3", 2)]
    fetcher = fetcher or _fetcher()
    take_map = {take.id: take for take in takes}
    with patch(
        "sow_render_worker.pipeline.get_render_glue_transitions",
        return_value=transitions,
    ), patch(
        "sow_render_worker.pipeline.get_glue_take_by_id",
        side_effect=lambda conn, take_id, user_id: take_map.get(take_id),
    ):
        clips = load_render_glue_clips(
            MagicMock(), job or _job(), items, fetcher
        )
    return clips, fetcher


class TestNoSnapshot:
    def test_null_snapshot_yields_no_clips(self):
        clips, fetcher = _load(transitions=None, takes=[])
        assert clips == {}
        fetcher.download_glue.assert_not_called()

    def test_empty_snapshot_yields_no_clips(self):
        clips, fetcher = _load(transitions=[], takes=[])
        assert clips == {}
        fetcher.download_glue.assert_not_called()

    def test_malformed_snapshot_json_fails_visibly(self):
        with patch(
            "sow_render_worker.pipeline.get_render_glue_transitions",
            side_effect=ValueError("render_jobs.glue_transitions is not valid JSON"),
        ):
            with pytest.raises(GlueSnapshotError) as excinfo:
                load_render_glue_clips(MagicMock(), _job(), [], MagicMock())
        assert excinfo.value.code == GLUE_NOT_READY
        assert "not valid JSON" in str(excinfo.value)


class TestValidSnapshot:
    def test_downloads_verified_metadata_and_preserves_snapshot_order(self):
        first = _take(id="take-1")
        second = _take(
            id="take-2",
            ordinal=2,
            r2_key=f"glues/42/{DESCRIPTOR_KEY}/take-take-2.wav",
            seconds=3.5,
        )
        entries = [
            _entry(second, from_item_id="item-2", to_item_id="item-3"),
            _entry(first),
        ]
        fetcher = _fetcher("/tmp/cache/glue.wav")
        fetcher.download_glue.side_effect = ["/tmp/cache/take-2.wav", "/tmp/cache/take-1.wav"]

        clips, fetcher = _load(
            transitions=entries, takes=[first, second], fetcher=fetcher
        )

        assert list(clips) == ["item-3", "item-2"]
        assert clips["item-2"] == GlueClip(
            path="/tmp/cache/take-1.wav",
            take_id="take-1",
            seconds=2.786,
            checksum=SHA256,
        )
        assert clips["item-3"].seconds == 3.5

        calls = fetcher.download_glue.call_args_list
        assert calls[0].args[0] == second.r2_key
        assert calls[1].args[0] == first.r2_key
        for call in calls:
            assert call.kwargs["expected_bytes"] is None
            assert call.kwargs["expected_sha256"] == SHA256
            assert call.kwargs["job_id"] == "job-1"
            assert call.kwargs["take_id"] in {"take-1", "take-2"}
            assert call.kwargs["cache_key"].startswith("glue-take-")

    def test_accepts_audio_sha256_and_audio_bytes_aliases(self):
        take = _take()
        entry = _entry(take)
        entry.pop("sha256")
        entry["audio_sha256"] = SHA256
        entry["audio_bytes"] = 1000

        clips, fetcher = _load(transitions=[entry], takes=[take])

        assert clips["item-2"].checksum == SHA256
        assert fetcher.download_glue.call_args.kwargs["expected_bytes"] == 1000
        assert fetcher.download_glue.call_args.kwargs["expected_sha256"] == SHA256

    def test_seconds_within_one_microsecond_are_accepted(self):
        take = _take(seconds=2.7860000001)
        clips, _ = _load(transitions=[_entry(take)], takes=[take])
        assert clips["item-2"].seconds == pytest.approx(2.786)

    def test_logs_one_loaded_event_per_clip(self, caplog):
        take = _take()
        with caplog.at_level("INFO", logger="sow_render_worker.pipeline"):
            _load(transitions=[_entry(take)], takes=[take])
        events = [
            json.loads(record.getMessage())
            for record in caplog.records
            if record.getMessage().startswith("{")
        ]
        loaded = [e for e in events if e["event"] == "glue.render.glue.loaded"]
        assert loaded == [
            {
                "event": "glue.render.glue.loaded",
                "jobId": "job-1",
                "takeId": "take-1",
                "toItemId": "item-2",
                "seconds": 2.786,
            }
        ]


class TestSnapshotItemIdentity:
    def _assert_stale(self, entry, *, detail, items=None):
        fetcher = _fetcher()
        with pytest.raises(GlueSnapshotError) as excinfo:
            _load(
                transitions=[entry],
                takes=[_take()],
                items=items,
                fetcher=fetcher,
            )
        error = excinfo.value
        assert error.code == GLUE_STALE_SNAPSHOT
        assert detail in error.detail
        assert "item-1 -> item-2" in str(error)
        fetcher.download_glue.assert_not_called()
        return error

    def test_matching_timestamps_render(self):
        take = _take()
        fetcher = _fetcher()
        clips, _ = _load(transitions=[_entry(take)], takes=[take], fetcher=fetcher)
        assert clips["item-2"].take_id == "take-1"
        fetcher.download_glue.assert_called_once()

    def test_timestamps_within_one_microsecond_are_accepted(self):
        take = _take()
        items = [
            _item("item-1", 0, updated_at=UPDATED_AT + timedelta(microseconds=1)),
            _item("item-2", 1, updated_at=UPDATED_AT + timedelta(microseconds=1)),
            _item("item-3", 2),
        ]
        clips, _ = _load(transitions=[_entry(take)], takes=[take], items=items)
        assert clips["item-2"].take_id == "take-1"

    def test_zulu_timestamp_is_accepted(self):
        take = _take()
        zulu = UPDATED_AT.strftime("%Y-%m-%dT%H:%M:%SZ")
        clips, _ = _load(
            transitions=[_entry(take, from_updated_at=zulu, to_updated_at=zulu)],
            takes=[take],
        )
        assert clips["item-2"].take_id == "take-1"

    def test_legacy_millisecond_snapshot_matches_a_postgres_timestamp(self):
        take = _take()
        actual = UPDATED_AT + timedelta(microseconds=99)
        clips, _ = _load(
            transitions=[_entry(take, from_updated_at=UPDATED_AT.isoformat(timespec="milliseconds"))],
            takes=[take],
            items=[_item("item-1", 0, updated_at=actual), _item("item-2", 1)],
        )
        assert clips["item-2"].take_id == take.id

    def test_from_item_updated_after_snapshot_is_stale(self):
        take = _take()
        moved = (UPDATED_AT + timedelta(seconds=1)).isoformat()
        self._assert_stale(
            _entry(take, from_updated_at=moved),
            detail="from item changed since the snapshot (from_updated_at mismatch)",
        )

    def test_to_item_updated_after_snapshot_is_stale(self):
        take = _take()
        moved = (UPDATED_AT + timedelta(seconds=1)).isoformat()
        self._assert_stale(
            _entry(take, to_updated_at=moved),
            detail="to item changed since the snapshot (to_updated_at mismatch)",
        )

    def test_microsecond_beyond_tolerance_is_stale(self):
        take = _take()
        items = [
            _item("item-1", 0, updated_at=UPDATED_AT + timedelta(microseconds=2)),
            _item("item-2", 1),
            _item("item-3", 2),
        ]
        self._assert_stale(
            _entry(take),
            detail="from item changed since the snapshot",
            items=items,
        )

    def test_malformed_from_timestamp_is_stale(self):
        take = _take()
        self._assert_stale(
            _entry(take, from_updated_at="not-a-timestamp"),
            detail="from item identity is unverifiable (from_updated_at missing or invalid)",
        )

    def test_missing_to_timestamp_is_stale(self):
        take = _take()
        entry = _entry(take)
        del entry["to_updated_at"]
        self._assert_stale(
            entry,
            detail="to item identity is unverifiable (to_updated_at missing or invalid)",
        )

    def test_legacy_item_without_updated_at_is_stale(self):
        take = _take()
        items = [
            _item("item-1", 0, updated_at=None),
            _item("item-2", 1),
            _item("item-3", 2),
        ]
        self._assert_stale(
            _entry(take),
            detail="from item identity is unverifiable",
            items=items,
        )

    def test_stale_failure_logs_stable_code(self, caplog):
        take = _take()
        moved = (UPDATED_AT + timedelta(seconds=1)).isoformat()
        with caplog.at_level("INFO", logger="sow_render_worker.pipeline"):
            self._assert_stale(
                _entry(take, from_updated_at=moved),
                detail="from item changed since the snapshot",
            )
        events = [
            json.loads(record.getMessage())
            for record in caplog.records
            if record.getMessage().startswith("{")
        ]
        failed = [e for e in events if e["event"] == "glue.render.glue.failed"]
        assert failed[0]["errorCode"] == GLUE_STALE_SNAPSHOT


class TestSnapshotValidationFailures:
    def _assert_failure(self, transitions, takes, *, detail, junction="item-1 -> item-2",
                        takes_call=True):
        fetcher = _fetcher()
        with pytest.raises(GlueSnapshotError) as excinfo:
            _load(transitions=transitions, takes=takes, fetcher=fetcher)
        error = excinfo.value
        assert error.code == GLUE_NOT_READY
        assert detail in error.detail
        assert junction in str(error)
        if takes_call:
            fetcher.download_glue.assert_not_called()
        return error

    def test_missing_take(self):
        take = _take()
        self._assert_failure([_entry(take)], [], detail="take is missing")

    def test_foreign_owner_take(self):
        take = _take()
        foreign = _take(user_id=99)
        # get_glue_take_by_id is owner-scoped in SQL; a defensive fake returns a
        # foreign row so the worker guard is exercised too.
        self._assert_failure([_entry(take)], [foreign], detail="belongs to another user")

    def test_non_ready_take(self):
        take = _take(status="failed")
        self._assert_failure([_entry(take)], [take], detail="not ready")

    def test_descriptor_mismatch(self):
        take = _take()
        self._assert_failure(
            [_entry(take, descriptor_key="f" * 64)],
            [take],
            detail="descriptor key mismatch",
        )

    def test_r2_key_mismatch(self):
        take = _take()
        self._assert_failure(
            [_entry(take, r2_key="glues/42/other.wav")],
            [take],
            detail="staged object key mismatch",
        )

    def test_seconds_mismatch(self):
        take = _take()
        self._assert_failure(
            [_entry(take, seconds=2.8)], [take], detail="staged duration mismatch"
        )

    def test_take_without_duration_fails(self):
        take = _take(seconds=None)
        self._assert_failure(
            [_entry(take, seconds=2.786)],
            [take],
            detail="staged duration mismatch",
        )

    def test_non_adjacent_junction(self):
        take = _take()
        self._assert_failure(
            [_entry(take, to_item_id="item-3")],
            [take],
            detail="not adjacent",
            junction="item-1 -> item-3",
        )

    def test_unknown_item_junction(self):
        take = _take()
        self._assert_failure(
            [_entry(take, to_item_id="missing")],
            [take],
            detail="not adjacent",
            junction="item-1 -> missing",
        )

    def test_duplicate_junction(self):
        take = _take()
        entry = _entry(take)
        self._assert_failure(
            [entry, dict(entry)], [take], detail="duplicated", takes_call=False
        )

    def test_checksum_mismatch_against_take_row(self):
        take = _take()
        self._assert_failure(
            [_entry(take, sha256="c" * 64)],
            [take],
            detail="staged checksum mismatch",
        )

    def test_malformed_snapshot_checksum(self):
        take = _take()
        self._assert_failure(
            [_entry(take, sha256=123)], [take], detail="checksum is malformed"
        )

    def test_byte_length_mismatch_against_take_row(self):
        take = _take()
        self._assert_failure(
            [_entry(take, audio_bytes=999)],
            [take],
            detail="byte length mismatch",
        )

    def test_malformed_entry(self):
        take = _take()
        entry = _entry(take)
        del entry["r2_key"]
        self._assert_failure([entry], [take], detail="snapshot entry 0 is malformed")

    def test_missing_seconds_is_malformed(self):
        take = _take()
        entry = _entry(take)
        del entry["seconds"]
        self._assert_failure([entry], [take], detail="snapshot entry 0 is malformed")

    def test_logs_failed_event_with_stable_code(self, caplog):
        take = _take(status="failed")
        with caplog.at_level("INFO", logger="sow_render_worker.pipeline"):
            with pytest.raises(GlueSnapshotError):
                _load(transitions=[_entry(take)], takes=[take])
        events = [
            json.loads(record.getMessage())
            for record in caplog.records
            if record.getMessage().startswith("{")
        ]
        failed = [e for e in events if e["event"] == "glue.render.glue.failed"]
        assert failed == [
            {
                "event": "glue.render.glue.failed",
                "jobId": "job-1",
                "takeId": "take-1",
                "toItemId": "item-2",
                "errorCode": GLUE_NOT_READY,
            }
        ]


class TestDownloadFailure:
    def test_download_failure_is_visible_and_names_the_junction(self):
        take = _take()
        fetcher = _fetcher()
        fetcher.download_glue.side_effect = GlueDownloadError(
            "Glue audio checksum mismatch for glue-take-1.wav"
        )
        with pytest.raises(GlueSnapshotError) as excinfo:
            _load(transitions=[_entry(take)], takes=[take], fetcher=fetcher)
        error = excinfo.value
        assert error.code == "GLUE_AUDIO_UNAVAILABLE"
        assert "AI transition item-1 -> item-2" in str(error)
        assert "audio download failed" in error.detail

    def test_download_failure_does_not_return_partial_clips(self):
        first = _take()
        second = _take(id="take-2", ordinal=2)
        fetcher = _fetcher()
        fetcher.download_glue.side_effect = [
            "/tmp/cache/take-1.wav",
            GlueDownloadError("Glue audio unavailable for glue-take-2.wav: HTTP 404"),
        ]
        with pytest.raises(GlueSnapshotError):
            _load(
                transitions=[_entry(first), _entry(second, to_item_id="item-3")],
                takes=[first, second],
                fetcher=fetcher,
            )
