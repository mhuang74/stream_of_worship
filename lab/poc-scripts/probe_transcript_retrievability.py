#!/usr/bin/env python3
"""Transcript-retrievability probe for LRC review triage (issue #246).

Implements the probe-first routing of
``specs/lrc-review-triage-cascade-design-v2.md``: for each song, attempt a
read-only YouTube transcript fetch and classify
``retrievable = ≥10 cleaned transcript lines``. RuntimeError / transcript
disabled / fewer than 10 lines = not retrievable. This decides "will regen
help?" BEFORE any regen is queued — a no-transcript regen deterministically
re-runs the known-poor Qwen3 path and burns DashScope/LLM spend.

Properties (per spec):
- read-only network fetch — zero writes to canonical Lyrics, catalog status,
  provenance, or visibility;
- paced ≥2s between fetches;
- one retry on transient network errors (permanent errors do not retry);
- results cached per video_id in the run manifest (resumable);
- systemic IP-blocking (IpBlocked / RequestBlocked) stops the whole run with
  a report for a maintainer decision (the YouTube Data API captions endpoint
  is the named alternate) — never a retry storm.

Usage (from repo root):
    uv run --project ops/admin-cli --extra admin --extra test \
        python lab/poc-scripts/probe_transcript_retrievability.py \
        [--manifest lab/poc-scripts/eval/lrc_truth/probe_manifest.json] \
        [--limit N] [--self-check-only]

The target set is the review queue snapshot (``eval/lrc_truth/latest.json``,
Phase 0a). ``--self-check-only`` probes just the 8 seed negatives and asserts
the 6 youtube_transcript ones retrievable and the qwen3_asr one not; the
manual_upload anomaly's result is recorded, not asserted.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

sys.path.insert(0, str(Path(__file__).parent))

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST = SCRIPT_DIR / "eval/lrc_truth/probe_manifest.json"
# Repo root (lab/poc-scripts/..) — independent of the invocation cwd.
REPO_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_SNAPSHOT = REPO_ROOT / "eval/lrc_truth/latest.json"

MANIFEST_SCHEMA_VERSION = 1
MIN_RETRIEVABLE_LINES = 10
PACE_SECONDS = 2.0

# youtube-transcript-api exception class names treated as systemic IP blocking.
# Name-matched (not isinstance) so a stale library version degrades to the
# conservative "transient" path instead of crashing the probe.
IP_BLOCK_ERROR_NAMES = {"IpBlocked", "RequestBlocked"}
# Exception class names that are permanent video-state errors: no retry.
PERMANENT_ERROR_NAMES = {
    "TranscriptsDisabled",
    "NoTranscriptFound",
    "VideoUnavailable",
    "InvalidVideoId",
    "AgeRestricted",
    "VideoUnplayable",
    "NotTranslatable",
    "PoTokenRequired",
    "CouldNotRetrieveTranscript",  # base class of the above
}


class SystemicIpBlockError(RuntimeError):
    """YouTube is blocking this IP systemically; the run stops for a human."""


Outcome = Literal["retrievable", "no_transcript", "too_few_lines", "error"]


@dataclass
class SongProbeSpec:
    song_id: str
    youtube_url: str
    lrc_source: str | None = None


@dataclass
class ProbeResult:
    song_id: str
    video_id: str
    retrievable: bool
    line_count: int | None
    outcome: Outcome  # retrievable | no_transcript | too_few_lines | error
    source: str | None
    error: str | None
    attempts: int


def extract_video_id(url: str) -> str | None:
    """Extract a YouTube video ID (mirrors the admin service helper without
    importing the heavy yt-dlp module)."""
    import re

    if "youtu.be/" in url:
        match = re.search(r"youtu\.be/([^/?]+)", url)
        if match:
            return match.group(1)
    match = re.search(r"[?&]v=([^&]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"youtube\.com/(?:embed|shorts|live)/([^/?]+)", url)
    if match:
        return match.group(1)
    return None


def classify_lines(lines: list[str]) -> tuple[bool, int]:
    """retrievable = ≥ MIN_RETRIEVABLE_LINES cleaned transcript lines."""
    return len(lines) >= MIN_RETRIEVABLE_LINES, len(lines)


def _exception_cause_names(exc: BaseException) -> set[str]:
    """Class names of the exception and its __cause__/__context__ chain."""
    names: set[str] = set()
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        names.add(type(current).__name__)
        current = current.__cause__ or current.__context__
    return names


def _is_permanent(exc: BaseException) -> bool:
    return bool(_exception_cause_names(exc) & PERMANENT_ERROR_NAMES)


def _default_fetch(url: str) -> tuple[str, list[str]]:
    """Read-only transcript fetch via the admin service's probe primitive."""
    from stream_of_worship.admin.services.youtube import _fetch_transcript_draft

    draft = _fetch_transcript_draft(url)
    return draft.source, draft.lines


class FakeClock:
    """Deterministic clock + sleep recorder for tests."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _real_clock() -> Any:  # pragma: no cover - trivial adapter
    class _Clock:
        def time(self) -> float:
            return time.monotonic()

        def sleep(self, seconds: float) -> None:
            time.sleep(seconds)

    return _Clock()


class ProbeManifest:
    """JSON-backed manifest caching probe results per video_id."""

    def __init__(self, data: dict[str, Any], path: Path):
        self.data = data
        self.path = path

    @property
    def probes(self) -> dict[str, dict[str, Any]]:
        return self.data["probes"]

    def save(self) -> None:
        import os
        import tempfile

        self.data["updated_at"] = _utcnow()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


def load_probe_manifest(path: Path) -> ProbeManifest:
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        version = data.get("schema_version")
        if version != MANIFEST_SCHEMA_VERSION:
            raise RuntimeError(
                f"probe manifest {path} has schema_version={version!r}, "
                f"expected {MANIFEST_SCHEMA_VERSION}"
            )
        data.setdefault("probes", {})
        return ProbeManifest(data, path)
    now = _utcnow()
    return ProbeManifest(
        {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "created_at": now,
            "updated_at": now,
            "min_retrievable_lines": MIN_RETRIEVABLE_LINES,
            "pace_seconds": PACE_SECONDS,
            "probes": {},
        },
        path,
    )


def _utcnow() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


class ProbeRunner:
    """Paced, retry-once, cached transcript probe over a list of songs."""

    def __init__(
        self,
        fetch_fn: Callable[[str], Any] | None = None,
        clock: Any | None = None,
        manifest_path: Path | None = None,
        manifest: ProbeManifest | None = None,
        ip_block_error_names: set[str] | None = None,
    ):
        self._fetch_fn = fetch_fn or _default_fetch
        self._clock = clock if clock is not None else _real_clock()
        self._manifest = manifest or load_probe_manifest(manifest_path or DEFAULT_MANIFEST)
        self._ip_block_names = ip_block_error_names or IP_BLOCK_ERROR_NAMES
        self.ip_blocked_at: str | None = None

    # -- single song -------------------------------------------------------

    def probe_song(self, song: SongProbeSpec, force: bool = False) -> ProbeResult:
        video_id = extract_video_id(song.youtube_url)
        if not video_id:
            return ProbeResult(
                song_id=song.song_id,
                video_id="",
                retrievable=False,
                line_count=None,
                outcome="error",
                source=None,
                error=f"no video id in {song.youtube_url}",
                attempts=0,
            )

        cached = self._manifest.probes.get(video_id)
        if cached is not None and not force:
            return ProbeResult(
                song_id=song.song_id,
                video_id=video_id,
                retrievable=cached["retrievable"],
                line_count=cached.get("line_count"),
                outcome=cached["outcome"],
                source=cached.get("source"),
                error=cached.get("error"),
                attempts=0,  # served from cache
            )

        attempts = 0
        last_error: str | None = None
        while True:
            if attempts > 0:
                self._clock.sleep(PACE_SECONDS)
            attempts += 1
            try:
                outcome = self._fetch_fn(song.youtube_url)
                source, lines = outcome if isinstance(outcome, tuple) else (None, outcome)
                retrievable, count = classify_lines(lines)
                result = ProbeResult(
                    song_id=song.song_id,
                    video_id=video_id,
                    retrievable=retrievable,
                    line_count=count,
                    outcome="retrievable" if retrievable else "too_few_lines",
                    source=source,
                    error=None,
                    attempts=attempts,
                )
                break
            except Exception as exc:  # classify and decide
                cause_names = _exception_cause_names(exc)
                if cause_names & self._ip_block_names:
                    self.ip_blocked_at = song.song_id
                    raise SystemicIpBlockError(
                        f"YouTube is blocking this IP (while probing {song.song_id} "
                        f"video {video_id}): {exc}. Run stopped for a maintainer "
                        "decision; the YouTube Data API captions endpoint is the "
                        "named alternate means."
                    ) from exc
                last_error = f"{type(exc).__name__}: {exc}"
                if attempts >= 2 or _is_permanent(exc):
                    result = ProbeResult(
                        song_id=song.song_id,
                        video_id=video_id,
                        retrievable=False,
                        line_count=None,
                        outcome=("no_transcript" if _is_permanent(exc) else "error"),
                        source=None,
                        error=last_error,
                        attempts=attempts,
                    )
                    break

        self._record(song, result)
        return result

    def _record(self, song: SongProbeSpec, result: ProbeResult) -> None:
        self._manifest.probes[result.video_id] = {
            "song_id": song.song_id,
            "retrievable": result.retrievable,
            "line_count": result.line_count,
            "outcome": result.outcome,
            "source": result.source,
            "error": result.error,
            "attempts": result.attempts,
            "probed_at": _utcnow(),
        }
        self._manifest.save()

    # -- batch --------------------------------------------------------------

    def run(self, songs: list[SongProbeSpec]) -> list[ProbeResult]:
        results: list[ProbeResult] = []
        fetched_this_run = False

        for song in songs:
            video_id = extract_video_id(song.youtube_url) if song.youtube_url else None
            is_cached = video_id is not None and video_id in self._manifest.probes
            if not is_cached and fetched_this_run:
                # pace only before an actual network fetch
                self._clock.sleep(PACE_SECONDS)
            results.append(self.probe_song(song))
            if not is_cached:
                fetched_this_run = True
        return results


# --------------------------------------------------------------------------
# Seed self-check
# --------------------------------------------------------------------------

# Authoritative assertion target (specs/...design-v2.md Appendix A, pin
# snapshot 2026-10-07): the self-check asserts THIS map's expectation —
# 6 youtube_transcript retrievable, the qwen3_asr seed not, the
# manual_upload anomaly recorded only. The Phase 0a snapshot's
# seed_subsets.negative.lrc_source_provenance is only a cross-check: a
# drifting/partial provenance must FAIL loudly here (divergences below),
# never silently shrink the assertion set.
SEED_NEGATIVE_SOURCES = {
    "bu_ting_zan_mei_mi_e937a9d3": "manual_upload",  # anomaly: recorded, not asserted
    "shu_bu_jin_71fba0ce": "youtube_transcript",
    "wo_neng_gei_ni_shen_me_03b2dcb2": "youtube_transcript",
    "jing_bai_ye_su_b08227a2": "youtube_transcript",
    "wo_jing_bai_mi__ye_su_e6dd6146": "youtube_transcript",
    "na_me_shen_de_ke_mu_ff92abd9": "youtube_transcript",
    "cang_shen_zhi_chu_39437ec0": "youtube_transcript",
    "ai_shi_wo_men_yong_gan_6d1865b8": "qwen3_asr",
}

EXPECTED_ASSERTED = 7  # 6 youtube_transcript + 1 qwen3_asr seeds


def load_seed_sources(
    snapshot_path: Path,
) -> tuple[dict[str, str], dict[str, str] | None, str]:
    """Load the pinned seed map plus the snapshot's provenance for
    cross-checking.

    Returns (sources, snapshot_provenance, source_of) where ``sources`` is
    always the pinned SEED_NEGATIVE_SOURCES (the assertion target) and
    ``snapshot_provenance`` is the snapshot's map (None when absent), so the
    caller can report divergence instead of adopting it wholesale.
    """
    if snapshot_path.exists():
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
        provenance = data.get("seed_subsets", {}).get("negative", {}).get("lrc_source_provenance")
        if provenance:
            return dict(SEED_NEGATIVE_SOURCES), dict(provenance), "snapshot"
    return dict(SEED_NEGATIVE_SOURCES), None, "fallback"


def provenance_divergences(
    pinned: dict[str, str], snapshot_provenance: dict[str, str] | None
) -> list[str]:
    """Human-readable divergence list between pinned and snapshot maps."""
    if snapshot_provenance is None:
        return ["snapshot has no seed_subsets.negative.lrc_source_provenance"]
    divergences: list[str] = []
    for song_id, source in pinned.items():
        if song_id not in snapshot_provenance:
            divergences.append(f"{song_id}: missing from snapshot provenance")
        elif snapshot_provenance[song_id] != source:
            divergences.append(
                f"{song_id}: snapshot={snapshot_provenance[song_id]!r} vs pinned={source!r}"
            )
    for song_id in snapshot_provenance:
        if song_id not in pinned:
            divergences.append(f"{song_id}: unexpected extra seed in snapshot provenance")
    return divergences


def evaluate_self_check(
    results: dict[str, ProbeResult],
    lrc_sources: dict[str, str | None],
) -> dict[str, Any]:
    """Pure evaluation of the seed self-check expectations.

    Asserted: every youtube_transcript seed must be retrievable; the
    qwen3_asr seed must not be. Recorded only: manual_upload (and any
    unknown source) — outcome recorded, never asserted.
    """
    failures: list[dict[str, str]] = []
    asserted = 0
    recorded_only: list[str] = []

    for song_id, source in lrc_sources.items():
        result = results.get(song_id)
        if result is None:
            failures.append({"song_id": song_id, "problem": "no probe result"})
            continue
        if source == "youtube_transcript":
            asserted += 1
            if not result.retrievable:
                failures.append(
                    {
                        "song_id": song_id,
                        "problem": (
                            f"youtube_transcript seed not retrievable "
                            f"(outcome={result.outcome}, error={result.error})"
                        ),
                    }
                )
        elif source == "qwen3_asr":
            asserted += 1
            if result.retrievable:
                failures.append(
                    {
                        "song_id": song_id,
                        "problem": "qwen3_asr seed unexpectedly retrievable",
                    }
                )
        else:
            recorded_only.append(song_id)

    return {
        "passed": not failures,
        "asserted": asserted,
        "recorded_only": recorded_only,
        "failures": failures,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def load_review_snapshot(path: Path) -> list[SongProbeSpec]:
    """Probe targets from the Phase 0a review-queue snapshot."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        SongProbeSpec(
            song_id=row["song_id"] or row["hash_prefix"],
            youtube_url="",  # resolved from the DB by resolve_urls
            lrc_source=row["lrc_source"],
        )
        for row in data["review_queue"]
    ]


def resolve_urls(specs: list[SongProbeSpec], config_path: str | None) -> None:
    """Fill youtube_url from the DB (read-only).

    Review-queue rows can be songless recordings (snapshot LEFT JOIN keeps
    them with a hash_prefix but no song id), so the lookup matches either
    song_id or hash_prefix in one query.
    """
    from stream_of_worship.admin.config import AdminConfig
    from stream_of_worship.db.connection import ConnectionProvider

    config = AdminConfig.load(config_path)
    provider = ConnectionProvider(config.get_connection_url())
    conn = provider.get_connection()
    try:
        with conn.cursor() as cur:
            keys = [s.song_id for s in specs]
            cur.execute(
                """
                SELECT song_id, hash_prefix, youtube_url
                FROM (
                    SELECT r.song_id, r.hash_prefix, r.youtube_url,
                           ROW_NUMBER() OVER (
                               PARTITION BY COALESCE(r.song_id, r.hash_prefix)
                               ORDER BY r.imported_at DESC
                           ) AS rn
                    FROM recordings r
                    WHERE r.deleted_at IS NULL
                      AND r.youtube_url IS NOT NULL
                      AND (r.song_id = ANY(%(keys)s) OR r.hash_prefix = ANY(%(keys)s))
                ) ranked
                WHERE rn = 1
                """,
                {"keys": keys},
            )
            url_by_key = {}
            for song_id, hash_prefix, url in cur.fetchall():
                if song_id:
                    url_by_key[song_id] = url
                if hash_prefix:
                    url_by_key[hash_prefix] = url
            for song in specs:
                song.youtube_url = url_by_key.get(song.song_id, "")
    finally:
        provider.close()


def run_self_check(
    runner: ProbeRunner,
    config_path: str | None,
    snapshot_path: Path | None = None,
    url_resolver: Callable[[list[SongProbeSpec]], None] | None = None,
) -> dict[str, Any]:
    """Probe the 8 seed negatives and evaluate the pinned seed expectations.

    The pinned map (SEED_NEGATIVE_SOURCES) is the assertion target; the
    snapshot's provenance is cross-checked and any divergence is recorded
    AND fails the self-check, so a drifted/partial snapshot provenance can
    never silently shrink the assertion set.
    """
    sources, snapshot_provenance, source_of = load_seed_sources(snapshot_path or DEFAULT_SNAPSHOT)
    divergences = provenance_divergences(sources, snapshot_provenance)
    specs = [
        SongProbeSpec(song_id=song_id, youtube_url="", lrc_source=source)
        for song_id, source in sources.items()
    ]
    if url_resolver is not None:
        url_resolver(specs)
    else:
        resolve_urls(specs, config_path)
    missing = [s.song_id for s in specs if not s.youtube_url]
    if missing:
        raise SystemExit(f"no youtube_url in DB for seed songs: {missing}")
    results = {s.song_id: r for s, r in zip(specs, runner.run(specs))}
    report = evaluate_self_check(results, sources)
    report["source_of"] = source_of
    report["divergences"] = divergences
    report["failures"] = report["failures"] + [
        {"song_id": "*", "problem": f"seed provenance divergence: {d}"} for d in divergences
    ]
    report["passed"] = not report["failures"]
    if report["asserted"] != EXPECTED_ASSERTED:
        report["failures"].append(
            {
                "song_id": "*",
                "problem": (
                    f"asserted count {report['asserted']} != expected {EXPECTED_ASSERTED} "
                    "(partial provenance silently shrank the assertion set)"
                ),
            }
        )
        report["passed"] = False
    report["results"] = {
        sid: {
            "retrievable": r.retrievable,
            "line_count": r.line_count,
            "outcome": r.outcome,
            "source": r.source,
            "error": r.error,
        }
        for sid, r in results.items()
    }
    return report


def main(argv: list[str] | None = None, runner_factory=None, url_resolver=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Probe manifest JSON path (default: lab/poc-scripts/eval/lrc_truth/probe_manifest.json)",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=DEFAULT_SNAPSHOT,
        help="Phase 0a review-queue snapshot (default: eval/lrc_truth/latest.json)",
    )
    parser.add_argument("--config", type=Path, default=None, help="Admin config path")
    parser.add_argument(
        "--limit", type=int, default=None, help="Probe only the first N review songs"
    )
    parser.add_argument(
        "--self-check-only",
        action="store_true",
        help="Probe just the 8 seed negatives and assert the seed expectations",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Where to write the run report JSON (default: alongside the manifest)",
    )
    args = parser.parse_args(argv)

    runner = (
        runner_factory() if runner_factory is not None else ProbeRunner(manifest_path=args.manifest)
    )
    report_path = args.report or args.manifest.parent / "probe_report.json"

    # Partial-report skeleton persisted BEFORE probing: a mid-run abort
    # (systemic IP-block stop) still leaves a report on disk listing probed
    # and unprobed songs — the clean stop the spec requires, never silence.
    report: dict[str, Any] = {"status": "running", "started_at": _utcnow()}
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    try:
        if args.self_check_only:
            report = run_self_check(
                runner, str(args.config) if args.config else None, args.snapshot
            )
        else:
            specs = load_review_snapshot(args.snapshot)
            if url_resolver is not None:
                url_resolver(specs)
            else:
                resolve_urls(specs, str(args.config) if args.config else None)
            if args.limit:
                specs = specs[: args.limit]
            results = runner.run(specs)
            retrievable = sum(1 for r in results if r.retrievable)
            report = {
                "probed": len(results),
                "retrievable": retrievable,
                "not_retrievable": len(results) - retrievable,
                "by_outcome": _count_by(results, lambda r: r.outcome),
                "results": [_result_dict(r) for r in results],
            }
            print(
                f"probed {report['probed']} songs: {report['retrievable']} retrievable, "
                f"{report['not_retrievable']} not retrievable"
            )
            print(f"by outcome: {report['by_outcome']}")
            print(f"report: {report_path}")
            return 0
    except SystemicIpBlockError as exc:
        aborted = {
            "status": "stopped_ip_blocked",
            "started_at": _utcnow(),
            "stopped_at_song": runner.ip_blocked_at,
            "probed_before_stop": len(runner._manifest.probes),
            "note": (
                "Systemic YouTube IP blocking detected; run stopped for a "
                "maintainer decision. The YouTube Data API captions endpoint "
                "is the named alternate means. Resume later against the "
                "cached manifest."
            ),
            "reason": str(exc),
        }
        report_path.write_text(
            json.dumps(aborted, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"STOPPED: {aborted['note']}")
        print(f"stopped at song: {runner.ip_blocked_at}")
        print(f"report: {report_path}")
        return 2

    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if args.self_check_only:
        print(f"self-check passed: {report['passed']}")
        print(f"asserted: {report['asserted']}, recorded_only: {report['recorded_only']}")
        for failure in report["failures"]:
            print(f"  FAIL {failure['song_id']}: {failure['problem']}")
        for song_id, entry in report["results"].items():
            print(
                f"  {song_id}: retrievable={entry['retrievable']} "
                f"lines={entry['line_count']} outcome={entry['outcome']}"
            )
        return 0 if report["passed"] else 1

    return 0


def _count_by(results: list[ProbeResult], key_fn: Callable[[ProbeResult], str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in results:
        counts[key_fn(r)] = counts.get(key_fn(r), 0) + 1
    return counts


def _result_dict(r: ProbeResult) -> dict[str, Any]:
    return {
        "song_id": r.song_id,
        "video_id": r.video_id,
        "retrievable": r.retrievable,
        "line_count": r.line_count,
        "outcome": r.outcome,
        "source": r.source,
        "error": r.error,
        "attempts": r.attempts,
    }


if __name__ == "__main__":
    raise SystemExit(main())
