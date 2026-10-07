"""Tests for probe_transcript_retrievability.py (issue #246).

Covers the pure logic: retrievability classification (≥10 cleaned lines),
transient-error retry (exactly one), systemic IP-block abort, pacing,
per-video_id manifest caching, and the seed self-check evaluation. All
network/DB access is injected; no real fetches in unit tests.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from probe_transcript_retrievability import (
    MIN_RETRIEVABLE_LINES,
    PACE_SECONDS,
    ProbeResult,
    ProbeRunner,
    SongProbeSpec,
    SystemicIpBlockError,
    classify_lines,
    evaluate_self_check,
    load_probe_manifest,
)

# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


class TestClassifyLines:
    def test_threshold_is_ten(self):
        assert MIN_RETRIEVABLE_LINES == 10

    def test_ten_lines_is_retrievable(self):
        retrievable, count = classify_lines([f"line {i}" for i in range(10)])
        assert retrievable is True
        assert count == 10

    def test_nine_lines_is_not_retrievable(self):
        retrievable, count = classify_lines([f"line {i}" for i in range(9)])
        assert retrievable is False
        assert count == 9

    def test_zero_lines_is_not_retrievable(self):
        assert classify_lines([]) == (False, 0)


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


def make_clock():
    """Deterministic clock + sleep recorder for pacing assertions."""

    class _FakeClock:
        def __init__(self):
            self.now = 1000.0
            self.sleeps: list[float] = []

        def time(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)
            self.now += seconds

    return _FakeClock()


def make_fetch(script):
    """script: list of per-call outcomes (list[str] = success lines,
    Exception instance = raise). Asserted empty at end of run."""
    calls: list[str] = []

    def fetch(url):
        outcome = script[len(calls)]
        calls.append(url)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    fetch.calls = calls
    fetch.script = script
    return fetch


def spec(song_id="s1", url="https://www.youtube.com/watch?v=vid1", lrc_source=None):
    return SongProbeSpec(song_id=song_id, youtube_url=url, lrc_source=lrc_source)


LINES_10 = [f"line {i}" for i in range(10)]
LINES_3 = ["a", "b", "c"]


class FakeTranscriptApiErrors:
    """Exception classes with the real library's names (name-matched)."""

    class IpBlocked(Exception):
        pass

    class RequestBlocked(Exception):
        pass

    class TranscriptsDisabled(Exception):
        pass

    class YouTubeRequestFailed(Exception):
        pass


# --------------------------------------------------------------------------
# ProbeRunner: retry / pacing / caching / abort
# --------------------------------------------------------------------------


class TestProbeRunner:
    def test_success_retrievable(self, tmp_path):
        fetch = make_fetch([LINES_10])
        runner = ProbeRunner(fetch_fn=fetch, clock=make_clock(), manifest_path=tmp_path / "m.json")
        result = runner.probe_song(spec())
        assert result.retrievable is True
        assert result.outcome == "retrievable"
        assert result.line_count == 10
        assert result.attempts == 1

    def test_runtime_error_no_transcript(self, tmp_path):
        err = RuntimeError("Failed to fetch YouTube transcript: disabled")
        err.__cause__ = FakeTranscriptApiErrors.TranscriptsDisabled("disabled")
        fetch = make_fetch([err])
        runner = ProbeRunner(fetch_fn=fetch, clock=make_clock(), manifest_path=tmp_path / "m.json")
        result = runner.probe_song(spec())
        assert result.retrievable is False
        assert result.outcome == "no_transcript"
        assert result.attempts == 1  # permanent error: no retry

    def test_transient_error_retried_once_then_success(self, tmp_path):
        err = RuntimeError("request failed")
        err.__cause__ = FakeTranscriptApiErrors.YouTubeRequestFailed("boom")
        fetch = make_fetch([err, LINES_10])
        clock = make_clock()
        runner = ProbeRunner(fetch_fn=fetch, clock=clock, manifest_path=tmp_path / "m.json")
        result = runner.probe_song(spec())
        assert result.retrievable is True
        assert result.attempts == 2
        # retry is paced: one sleep before the second attempt
        assert len(clock.sleeps) == 1
        assert clock.sleeps[0] >= PACE_SECONDS

    def test_transient_error_twice_gives_up(self, tmp_path):
        err = RuntimeError("request failed")
        err.__cause__ = FakeTranscriptApiErrors.YouTubeRequestFailed("boom")
        fetch = make_fetch([err, err])
        runner = ProbeRunner(fetch_fn=fetch, clock=make_clock(), manifest_path=tmp_path / "m.json")
        result = runner.probe_song(spec())
        assert result.retrievable is False
        assert result.outcome == "error"
        assert result.attempts == 2
        assert fetch.calls == [spec().youtube_url] * 2  # exactly one retry

    def test_too_few_lines_not_retried(self, tmp_path):
        fetch = make_fetch([LINES_3])
        runner = ProbeRunner(fetch_fn=fetch, clock=make_clock(), manifest_path=tmp_path / "m.json")
        result = runner.probe_song(spec())
        assert result.retrievable is False
        assert result.outcome == "too_few_lines"
        assert result.attempts == 1

    def test_ip_blocked_aborts_run(self, tmp_path):
        err = RuntimeError("blocked")
        err.__cause__ = FakeTranscriptApiErrors.IpBlocked("blocked")
        fetch = make_fetch([err, err, err])
        runner = ProbeRunner(fetch_fn=fetch, clock=make_clock(), manifest_path=tmp_path / "m.json")
        with pytest.raises(SystemicIpBlockError):
            runner.run(
                [
                    spec("s1", "https://www.youtube.com/watch?v=v1"),
                    spec("s2", "https://www.youtube.com/watch?v=v2"),
                ]
            )
        assert len(fetch.calls) == 1  # clean stop, no retry storm

    def test_request_blocked_also_aborts(self, tmp_path):
        err = RuntimeError("blocked")
        err.__cause__ = FakeTranscriptApiErrors.RequestBlocked("blocked")
        fetch = make_fetch([err])
        runner = ProbeRunner(fetch_fn=fetch, clock=make_clock(), manifest_path=tmp_path / "m.json")
        with pytest.raises(SystemicIpBlockError):
            runner.run([spec()])

    def test_pacing_between_songs(self, tmp_path):
        clock = make_clock()
        fetch = make_fetch([LINES_10, LINES_10])
        runner = ProbeRunner(fetch_fn=fetch, clock=clock, manifest_path=tmp_path / "m.json")
        runner.run(
            [
                spec("s1", "https://www.youtube.com/watch?v=v1"),
                spec("s2", "https://www.youtube.com/watch?v=v2"),
            ]
        )
        # second fetch must be ≥ PACE_SECONDS after the first
        assert len(clock.sleeps) >= 1
        assert all(s >= PACE_SECONDS for s in clock.sleeps)

    def test_same_video_id_cached_not_refetched(self, tmp_path):
        fetch = make_fetch([LINES_10])
        runner = ProbeRunner(fetch_fn=fetch, clock=make_clock(), manifest_path=tmp_path / "m.json")
        results = runner.run(
            [
                spec("s1", "https://www.youtube.com/watch?v=vidX"),
                spec("s2", "https://www.youtube.com/watch?v=vidX"),
            ]
        )
        assert len(fetch.calls) == 1
        assert results[1].retrievable is True
        assert results[1].line_count == 10

    def test_manifest_persisted_and_resumed(self, tmp_path):
        manifest_path = tmp_path / "m.json"
        fetch = make_fetch([LINES_10])
        runner = ProbeRunner(fetch_fn=fetch, clock=make_clock(), manifest_path=manifest_path)
        runner.run([spec("s1", "https://www.youtube.com/watch?v=vidX")])

        data = json.loads(manifest_path.read_text())
        entry = data["probes"]["vidX"]
        assert entry["retrievable"] is True
        assert entry["line_count"] == 10

        # fresh runner over the same manifest: no fetch happens
        fetch2 = make_fetch([])
        runner2 = ProbeRunner(fetch_fn=fetch2, clock=make_clock(), manifest_path=manifest_path)
        result = runner2.probe_song(spec("s1", "https://www.youtube.com/watch?v=vidX"))
        assert fetch2.calls == []
        assert result.retrievable is True

    def test_load_probe_manifest_schema_guard(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text(json.dumps({"schema_version": 999, "probes": {}}))
        with pytest.raises(Exception, match="schema_version"):
            load_probe_manifest(path)


# --------------------------------------------------------------------------
# Self-check evaluation (pure)
# --------------------------------------------------------------------------


class TestEvaluateSelfCheck:
    def test_seed_expectations(self):
        yt = "youtube_transcript"
        results = {
            "shu_bu_jin": ProbeResult("shu_bu_jin", "v1", True, 12, "retrievable", None, None, 1),
            "wo_neng_gei": ProbeResult("wo_neng_gei", "v2", True, 30, "retrievable", None, None, 1),
            "jing_bai": ProbeResult("jing_bai", "v3", True, 50, "retrievable", None, None, 1),
            "wo_jing_bai": ProbeResult("wo_jing_bai", "v4", True, 11, "retrievable", None, None, 1),
            "na_me_shen": ProbeResult("na_me_shen", "v5", True, 20, "retrievable", None, None, 1),
            "cang_shen": ProbeResult("cang_shen", "v6", True, 15, "retrievable", None, None, 1),
            "ai_shi": ProbeResult(
                "ai_shi", "v7", False, None, "no_transcript", None, "disabled", 1
            ),
            "bu_ting": ProbeResult("bu_ting", "v8", True, 40, "retrievable", None, None, 1),
        }
        sources = {
            "shu_bu_jin": yt,
            "wo_neng_gei": yt,
            "jing_bai": yt,
            "wo_jing_bai": yt,
            "na_me_shen": yt,
            "cang_shen": yt,
            "ai_shi": "qwen3_asr",
            "bu_ting": "manual_upload",
        }
        report = evaluate_self_check(results, sources)
        assert report["passed"] is True
        assert report["asserted"] == 7
        assert report["recorded_only"] == ["bu_ting"]

    def test_fails_when_youtube_transcript_song_not_retrievable(self):
        results = {
            "a": ProbeResult("a", "v1", False, 3, "too_few_lines", None, None, 1),
            "q": ProbeResult("q", "v2", False, None, "no_transcript", None, "disabled", 1),
        }
        sources = {"a": "youtube_transcript", "q": "qwen3_asr"}
        report = evaluate_self_check(results, sources)
        assert report["passed"] is False
        assert any(a["song_id"] == "a" for a in report["failures"])

    def test_fails_when_qwen3_song_retrievable(self):
        results = {
            "q": ProbeResult("q", "v2", True, 50, "retrievable", None, None, 1),
        }
        report = evaluate_self_check(results, {"q": "qwen3_asr"})
        assert report["passed"] is False

    def test_unknown_source_recorded_only(self):
        results = {
            "x": ProbeResult("x", "v1", True, 12, "retrievable", None, None, 1),
        }
        report = evaluate_self_check(results, {"x": "manual_upload"})
        assert report["passed"] is True
        assert report["asserted"] == 0
        assert report["recorded_only"] == ["x"]
