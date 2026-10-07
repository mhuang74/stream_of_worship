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

    def test_main_ip_block_writes_report_and_exits_2(self, tmp_path):
        from probe_transcript_retrievability import main

        err = RuntimeError("blocked")
        err.__cause__ = FakeTranscriptApiErrors.IpBlocked("blocked")
        fetch = make_fetch([err])
        manifest_path = tmp_path / "m.json"
        runner = ProbeRunner(fetch_fn=fetch, clock=make_clock(), manifest_path=manifest_path)
        report_path = tmp_path / "report.json"
        snapshot_path = tmp_path / "snap.json"

        snapshot_path.write_text(
            json.dumps(
                {
                    "review_queue": [
                        {"song_id": "s1", "hash_prefix": "h1", "lrc_source": "youtube_transcript"},
                        {"song_id": "s2", "hash_prefix": "h2", "lrc_source": "youtube_transcript"},
                    ]
                }
            )
        )

        resolver_calls: list[int] = []

        def resolver(specs):
            resolver_calls.append(1)
            for s in specs:
                s.youtube_url = f"https://www.youtube.com/watch?v={s.song_id}-vid"

        # DB and YouTube are unreachable from unit tests anyway; the run must
        # abort inside the first probe_song before any real network access.
        # The resolver seam is the ONLY url source — if it were bypassed the
        # real DB resolve_urls would fail this hermetic test.
        code = main(
            [
                "--manifest",
                str(manifest_path),
                "--snapshot",
                str(snapshot_path),
                "--report",
                str(report_path),
            ],
            runner_factory=lambda: runner,
            url_resolver=resolver,
        )
        assert len(resolver_calls) == 1  # resolver seam was actually used

        assert code == 2
        report = json.loads(report_path.read_text())
        assert report["status"] == "stopped_ip_blocked"
        assert report["stopped_at_song"] == "s1"
        assert "YouTube Data API" in report["note"]

    def test_main_self_check_flags_diverging_snapshot_provenance(self, tmp_path):
        """A snapshot provenance that diverges from the pinned Appendix-A
        map must FAIL loudly (exit 1, divergence recorded) — never be
        adopted wholesale, which would let a partial snapshot silently
        shrink the assertion set."""
        from probe_transcript_retrievability import main

        fetch = make_fetch([[]])
        manifest_path = tmp_path / "m.json"
        runner = ProbeRunner(fetch_fn=fetch, clock=make_clock(), manifest_path=manifest_path)
        report_path = tmp_path / "report.json"
        snapshot_path = tmp_path / "custom-snap.json"

        # Partial provenance (1 entry, reclassified): must fail the run.
        snapshot_path.write_text(
            json.dumps(
                {
                    "seed_subsets": {
                        "negative": {
                            "lrc_source_provenance": {"wo_jing_bai_mi__ye_su_e6dd6146": "qwen3_asr"}
                        }
                    },
                    "review_queue": [],
                }
            )
        )

        resolver_calls: list[int] = []

        def resolver(specs):
            resolver_calls.append(1)
            for s in specs:
                s.youtube_url = f"https://www.youtube.com/watch?v={s.song_id}"

        # Resolver is the only URL source (hermetic: real DB resolve_urls
        # would fail on CI where live Neon access is absent).
        code = main(
            [
                "--self-check-only",
                "--manifest",
                str(manifest_path),
                "--snapshot",
                str(snapshot_path),
                "--report",
                str(report_path),
            ],
            runner_factory=lambda: runner,
            url_resolver=resolver,
        )
        assert len(resolver_calls) == 1  # resolver seam used on the self-check path

        assert code == 1
        report = json.loads(report_path.read_text())
        assert report["source_of"] == "snapshot"
        assert report["passed"] is False
        assert report["asserted"] == 7  # pinned expectation, not the 1-entry snapshot
        assert len(report["divergences"]) == 8  # 7 missing + 1 reclassified
        assert any("missing from snapshot provenance" in d for d in report["divergences"])
        assert any(
            "snapshot='qwen3_asr' vs pinned='youtube_transcript'" in d
            for d in report["divergences"]
        )
        # all 8 pinned seeds were still probed/evaluated
        assert len(report["results"]) == 8


# --------------------------------------------------------------------------
# Seed self-check
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


class TestProvenanceGuard:
    """The pinned Appendix-A map is the assertion target; the snapshot's
    provenance is a cross-check that must fail loudly on divergence."""

    def test_matching_provenance_yields_no_divergences(self):
        from probe_transcript_retrievability import (
            SEED_NEGATIVE_SOURCES,
            provenance_divergences,
        )

        assert provenance_divergences(SEED_NEGATIVE_SOURCES, SEED_NEGATIVE_SOURCES) == []

    def test_none_provenance_diverges(self):
        from probe_transcript_retrievability import provenance_divergences

        divergences = provenance_divergences({"s1": "youtube_transcript"}, None)
        assert divergences == ["snapshot has no seed_subsets.negative.lrc_source_provenance"]

    def test_missing_and_reclassified_and_extra_seeds_reported(self):
        from probe_transcript_retrievability import provenance_divergences

        pinned = {"s1": "youtube_transcript", "s2": "qwen3_asr", "s3": "manual_upload"}
        snapshot = {"s1": "qwen3_asr", "s2": "qwen3_asr", "sX": "youtube_transcript"}
        divergences = provenance_divergences(pinned, snapshot)
        assert any(
            "s1" in d and "qwen3_asr" in d and "youtube_transcript" in d for d in divergences
        )
        assert any("s3" in d and "missing" in d for d in divergences)
        assert any("sX" in d and "unexpected extra" in d for d in divergences)

    def test_run_self_check_passes_on_fallback_when_snapshot_lacks_provenance(self, tmp_path):
        """A snapshot with no provenance entry → pinned map still drives the
        evaluation (recorded manual_upload, source_of='fallback')."""
        from probe_transcript_retrievability import ProbeRunner, run_self_check

        lines = [f"line{i}" for i in range(12)]
        runner = ProbeRunner(
            fetch_fn=lambda url: lines,
            clock=make_clock(),
            manifest_path=tmp_path / "m.json",
        )
        empty_snapshot = tmp_path / "snap.json"
        empty_snapshot.write_text(json.dumps({"review_queue": []}))

        report = run_self_check(
            runner,
            config_path=None,
            snapshot_path=empty_snapshot,
            url_resolver=lambda specs: [
                setattr(s, "youtube_url", f"https://www.youtube.com/watch?v={s.song_id}")
                for s in specs
            ],
        )
        assert report["source_of"] == "fallback"
        assert report["asserted"] == 7
        assert report["passed"] is False  # qwen3_asr seed unexpectedly retrievable
