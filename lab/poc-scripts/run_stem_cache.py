#!/usr/bin/env python3
"""Phase 0c runner: serial stem-separation cache for the LRC triage set.

Implements the runner of ``specs/lrc-review-triage-cascade-design-v2.md``
Phase 0c (issue #244). For each song in the target set:

1. Skip if the manifest already records ``cached``/``fallback`` (resume).
2. Reuse an on-disk ``clean_vocals.flac`` if a previous run left one without
   recording (crash recovery).
3. Download the best R2 vocal stem (``vocals_dry.flac`` preferred) and copy
   it to ``stems/clean_vocals.flac`` — no separation needed.
4. Else download the full mix and run two-stage separation, then record the
   dry output as ``clean_vocals.flac``:
   - default: MVSEP cloud API (BS-Roformer + Reverb Removal, matching the
     prod analysis-service configuration; free tier = 50 separations/day,
     replenished 24h after exhaustion — failed songs retry next invocation)
   - ``--local``: local audio-separator with the same MelBand Roformer
     ep_3005 + UVR-De-Echo models (quota-free; needs an env with
     audio-separator installed, e.g. ``uv run --project
     ops/analysis-service --extra service``; models cached at
     ``~/.cache/audio-separator``)

Everything runs strictly serially; a flock guard makes concurrent runner
invocations fail loudly. Failed songs are recorded and retried on the next
invocation. Zero writes to canonical Lyrics, catalog status, provenance, or
visibility.

Scale-out populations (issue #247):

- ``--set phase12`` — the Phase 1/2 ten-song set (default).
- ``--set phase3_positive`` — the Phase 3 calibration population: a seeded
  random sample of ≤60 songs from ``eval/lrc_truth/positive.txt`` (all of it
  when ≤60; 54 songs today → all 54).
- ``--set review_queue`` — the full triage population from the Phase 0a
  review-queue snapshot (``eval/lrc_truth/latest.json``; 391 songs at
  snapshot time vs. the spec's 399 — the live snapshot count is
  authoritative, per issue #243). Snapshot hash_prefixes are verified
  against the live DB before caching; a moved/deleted song fails loudly.

Usage (from repo root):
    uv run --project lab/poc-scripts --extra stem_separation --extra test \
        python lab/poc-scripts/run_stem_cache.py \
        [--set phase12|phase3_positive|review_queue] [--snapshot <path>] \
        [--local] [--cache-dir <path>] [--manifest <path>] [--dry-run]

Cache root defaults to ``sow_legacy_cli_tui.core.paths.get_cache_dir()``
(``~/.cache/stream-of-worship`` on Linux) — the same root the Phase 1/2
consumer ``poc/experiment_lrc_signals.py`` resolves stems from; override
with ``--cache-dir``. Layout per song:
``<cache_root>/<hash_prefix>/stems/clean_vocals.flac`` (+ ``audio/audio.mp3``
input).

Set ``MVSEP_API_KEY`` in the environment for the separation path.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from stem_cache import (
    CacheStatus,
    SongRef,
    StemCacheError,
    StemCacheManifest,
    load_or_init_manifest,
    lookup_r2_clean_vocals,
    next_pending,
    pick_dry_vocals,
    record_result,
    try_lock_serial,
)

# Phase 1/2 ten-song set (spec: 5 seed positives + 5 seed negatives,
# including the qwen3_asr and manual_upload negatives).
PHASE12_SET: tuple[str, ...] = (
    # 5 seed positives (published, hand-verified LRCs)
    "zhu_a__wo_yao_gen_sui_mi_83163301",
    "hereforyou_62e79ae9",
    "xin_kao_mei_yi_ju_ying_xu_df457941",
    "cong_zao_chen_dao_ye_wan_b035044f",
    "mei_hao_de_chuang_zao_3d42d76e",
    # 5 seed negatives (feedback-poor): 3 youtube_transcript + the
    # qwen3_asr + the manual_upload negatives
    "shu_bu_jin_71fba0ce",
    "wo_neng_gei_ni_shen_me_03b2dcb2",
    "jing_bai_ye_su_b08227a2",
    "ai_shi_wo_men_yong_gan_6d1865b8",  # qwen3_asr
    "bu_ting_zan_mei_mi_e937a9d3",  # manual_upload
)

_SCRIPT_DIR = Path(__file__).resolve().parent
# Anchored to the module directory, not cwd: the manifest/lock paths and the
# truth-list paths must be the same files no matter where the runner is
# invoked from.
DEFAULT_MANIFEST = _SCRIPT_DIR / "eval/lrc_truth/stem_cache/manifest.json"
DEFAULT_LOCK = _SCRIPT_DIR / "eval/lrc_truth/stem_cache/serial.lock"
DEFAULT_TRUTH_DIR = _SCRIPT_DIR.parent.parent / "eval" / "lrc_truth"
DEFAULT_SNAPSHOT = DEFAULT_TRUTH_DIR / "latest.json"

# Scale-out populations (issue #247 — Phase 3 positive sample, Phase 4 triage)

# Phase 3 sample size limit (spec: "seeded random sample of 60 (serial
# stem-separation bound; if ≤60, use all of it)"). Default seed: picked so a
# rerun reproduces the exact same sample.
PHASE3_SAMPLE_SIZE = 60
SEED = 42


def load_positive_ids(truth_dir: Path = DEFAULT_TRUTH_DIR) -> list[str]:
    """Phase 3 positive sample population: the published-LRC snapshot list.

    ``eval/lrc_truth/positive.txt`` is written verbatim from the live
    ``sow-admin lyrics feedback list`` stdout by ``snapshot_lrc_truth.py``
    (Phase 0a); rerunning the snapshotter regenerates it, so it stays fresh
    without a code change.
    """
    path = truth_dir / "positive.txt"
    ids = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not ids:
        raise StemCacheError(f"positive population {path} is empty; run snapshot_lrc_truth.py")
    return ids


def load_review_queue_ids(
    snapshot_path: Path = DEFAULT_SNAPSHOT,
) -> list[tuple[str, str]]:
    """Phase 4 triage population: the full review queue from the Phase 0a
    snapshot (eval/lrc_truth/latest.json).
    """
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    rows = data.get("review_queue")
    if not rows:
        raise StemCacheError(
            f"snapshot {snapshot_path} has no review_queue; rerun snapshot_lrc_truth.py"
        )
    refs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        song_id = row.get("song_id")
        hash_prefix = row.get("hash_prefix")
        if not song_id or not hash_prefix:
            raise StemCacheError(f"snapshot row missing song_id/hash_prefix: {row!r}")
        if song_id in seen:
            raise StemCacheError(f"snapshot contains duplicate song_id {song_id!r}")
        seen.add(song_id)
        refs.append((song_id, hash_prefix))
    return refs


def sample_positive(
    ids: list[str], seed: int = SEED, sample_size: int = PHASE3_SAMPLE_SIZE
) -> list[str]:
    """Phase 3 positive sample (spec line 242): seeded random sample of
    *sample_size* songs, or all songs when the population is at or below the
    sample size — no sampling, input order preserved (serial bound respected).
    """
    if len(ids) <= sample_size:
        return list(ids)
    rng = random.Random(seed)
    return sorted(rng.sample(ids, sample_size))


MVSEP_STAGE1_SEP_TYPE = 48  # MelBand Roformer (matches prod analysis service)
MVSEP_STAGE1_ADD_OPT1 = 11  # becruily deux, best vocals
MVSEP_STAGE2_SEP_TYPE = 22  # Reverb Removal
MVSEP_STAGE2_ADD_OPT1 = 0
MVSEP_OUTPUT_FORMAT = 2  # FLAC 16-bit

# Local separation (--local): model filenames mirror the prod analysis-service
# config (ops/analysis-service config.py SOW_VOCAL_SEPARATION_MODEL /
# SOW_DEREVERB_MODEL) so the local product matches the R2
# "analysis_service_mel_band_roformer_ep_3005" stems.
LOCAL_VOCAL_MODEL = "model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt"
LOCAL_DEREVERB_MODEL = "UVR-De-Echo-Normal.pth"
LOCAL_MODEL_PRODUCER = "local_audio_separator_mel_band_ep_3005"
# audio-separator defaults model_file_dir to /tmp/audio-separator-models/ and
# would silently re-download the 1GB MelBand model; point it at the machine's
# model cache instead (Separator also honors the env var, set explicitly).
DEFAULT_LOCAL_MODEL_DIR = Path.home() / ".cache" / "audio-separator"


@dataclass
class RunSummary:
    cached: int = 0
    fallback: int = 0
    failed: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return (
            f"cached={self.cached} fallback={self.fallback} "
            f"failed={self.failed} skipped={self.skipped}"
        )


def build_song_refs(
    conn, song_ids: list[str], known_prefixes: dict[str, str] | None = None
) -> tuple[list[tuple[str, str]], list[str]]:
    """Resolve song IDs → hash prefixes via a read-only DB query.

    With *known_prefixes* (review-queue snapshot, issue #247), each song_id
    must map to exactly the recorded prefix: the snapshot is verified against
    the live DB before caching, so a moved song fails loudly instead of
    caching stems under the wrong recording.

    Returns (refs, missing): *missing* lists songs absent from the DB
    (deleted since the snapshot). In verification mode they are reported to
    the caller for per-song FAILED records and the run continues; without
    known_prefixes (implicit-population mode) a missing song is an error.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id, r.hash_prefix
            FROM recordings r JOIN songs s ON s.id = r.song_id
            WHERE s.id = ANY(%s) AND r.deleted_at IS NULL
            ORDER BY s.id
            """,
            (song_ids,),
        )
        rows: dict[str, list[str]] = {}
        for sid, hp in cur.fetchall():
            rows.setdefault(sid, []).append(hp)
    missing = [sid for sid in song_ids if sid not in rows]
    if missing and known_prefixes is None:
        raise StemCacheError(f"songs not found in DB (deleted or unknown): {missing}")
    ambiguous = {sid: hps for sid, hps in rows.items() if len(hps) > 1}
    if ambiguous:
        raise StemCacheError(
            "songs with multiple live recordings - cannot pick a canonical "
            f"hash_prefix; resolve manually: {ambiguous}"
        )
    mismatched = {
        sid: (rows[sid][0], known_prefixes[sid])
        for sid in song_ids
        if known_prefixes is not None
        and sid in known_prefixes
        and sid in rows
        and rows[sid][0] != known_prefixes[sid]
    }
    if mismatched:
        raise StemCacheError(
            "hash_prefix mismatch between snapshot and live DB: "
            + "; ".join(f"{sid}: snapshot={sp} db={dp}" for sid, (dp, sp) in mismatched.items())
        )
    # preserve the caller's order (the runner's deterministic serial order)
    return [(sid, rows[sid][0]) for sid in song_ids if sid in rows], missing


def _mvsep_separate(
    audio_path: Path,
    output_dir: Path,
    api_token: str,
) -> list[Path]:
    """Run two-stage MVSEP separation; return downloaded output paths."""
    from poc.gen_clean_vocal_stem_mvsep import (  # type: ignore[import-not-found]
        download_files,
        poll_job,
        submit_job,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    stage1_dir = output_dir / "stage1_vocal_separation"
    stage2_dir = output_dir / "stage2_dereverb"

    print(
        f"  [mvsep] Stage 1: sep_type={MVSEP_STAGE1_SEP_TYPE} " f"add_opt1={MVSEP_STAGE1_ADD_OPT1}"
    )
    job_hash = submit_job(
        audio_path,
        api_token,
        sep_type=MVSEP_STAGE1_SEP_TYPE,
        add_opt1=MVSEP_STAGE1_ADD_OPT1,
        output_format=MVSEP_OUTPUT_FORMAT,
    )
    data = poll_job(job_hash, timeout=1800.0)
    stage1_paths = download_files(data.get("files", []), stage1_dir)
    vocals = next((p for p in stage1_paths if "vocal" in p.name.lower()), None)
    if vocals is None:
        raise StemCacheError(f"MVSEP stage 1 produced no vocals file: {stage1_paths}")

    print(
        f"  [mvsep] Stage 2: sep_type={MVSEP_STAGE2_SEP_TYPE} " f"add_opt1={MVSEP_STAGE2_ADD_OPT1}"
    )
    job_hash = submit_job(
        vocals,
        api_token,
        sep_type=MVSEP_STAGE2_SEP_TYPE,
        add_opt1=MVSEP_STAGE2_ADD_OPT1,
        add_opt2=1,
        output_format=MVSEP_OUTPUT_FORMAT,
    )
    data = poll_job(job_hash, timeout=1800.0)
    stage2_paths = download_files(data.get("files", []), stage2_dir)
    return stage2_paths


def _local_separate(audio_path: Path, output_dir: Path, model_dir: Path) -> list[Path]:
    """Run two-stage LOCAL separation via audio-separator (issue #247).

    Wraps ``poc.gen_clean_vocal_stem.extract_vocals_two_stage`` (the script
    version): stage 1 MelBand Roformer vocals, stage 2 UVR-De-Echo — same
    models as the prod analysis service. Returns produced files; the caller
    picks the dry vocals with ``pick_dry_vocals`` (stage-2 outputs carry
    "No Echo"/"No Reverb" names).
    """
    from poc.gen_clean_vocal_stem import extract_vocals_two_stage

    # audio-separator defaults model_file_dir to /tmp/audio-separator-models
    # and would re-download the 1GB MelBand model; point it at the machine's
    # model cache. The env var wins inside Separator even before the
    # (unset) model_file_dir parameter.
    os.environ["AUDIO_SEPARATOR_MODEL_DIR"] = str(model_dir)

    results = extract_vocals_two_stage(
        audio_path,
        output_dir,
        vocal_model=LOCAL_VOCAL_MODEL,
        dereverb_model=LOCAL_DEREVERB_MODEL,
    )
    outputs: list[Path] = []
    for stage in ("stage1", "stage2"):
        outputs.extend(Path(p) for p in results["stages"][stage]["outputs"])
    return outputs


def process_song(
    ref: SongRef,
    *,
    cache_root: Path,
    manifest: StemCacheManifest,
    manifest_path: Path,
    r2_client,
    mvsep_fn: Callable[[Path, Path], list[Path]],
    mvsep_token: str | None = None,
    sep_source: str = "mvsep",
    sep_producer: str | None = None,
) -> tuple[str, CacheStatus, str]:
    """Resolve clean vocals for one song through the priority chain.

    *sep_source*/*sep_producer* label the active separation backend
    ("mvsep" + its model spec, or "local" + LOCAL_MODEL_PRODUCER) for
    manifest provenance.

    Returns (song_id, terminal_status, source). Terminal statuses are
    ``cached`` (de-echoed clean vocals), ``fallback`` (wet vocal stem
    explicitly recorded — never presented as clean), or ``failed``.
    """
    song_id, hash_prefix = ref.song_id, ref.hash_prefix
    song_dir = cache_root / hash_prefix
    stems_dir = song_dir / "stems"
    clean_path = stems_dir / "clean_vocals.flac"

    # 1. On-disk cache hit from an earlier run (possibly unrecorded).
    if clean_path.exists() and clean_path.stat().st_size > 0:
        record_result(
            manifest,
            manifest_path,
            SongRef(song_id, hash_prefix),
            status=CacheStatus.CACHED,
            source="local_clean_vocals",
            audio="stems/clean_vocals.flac",
        )
        return song_id, CacheStatus.CACHED, "local_clean_vocals"

    # 2. R2 vocal stems — download the best available and record it.
    lookup = lookup_r2_clean_vocals(r2_client, hash_prefix)
    if lookup is not None:
        source_name, rel_stem_path = lookup
        stems_dir.mkdir(parents=True, exist_ok=True)
        dest = song_dir / rel_stem_path
        r2_client.download_file(f"{hash_prefix}/{rel_stem_path}", dest)
        if source_name in ("vocals_dry", "vocals_clean"):
            target_status = CacheStatus.CACHED
            target = stems_dir / "clean_vocals.flac"
            target.write_bytes(dest.read_bytes())
            audio_rel = "stems/clean_vocals.flac"
        else:
            # wet vocal stem: explicit fallback record. Materialize it at
            # stems/vocals.wav — the exact path the Phase 1/2 consumer
            # (experiment_lrc_signals.py) resolves when clean_vocals.flac is
            # absent — so the fallback is actually readable downstream.
            target_status = CacheStatus.FALLBACK
            fallback = stems_dir / "vocals.wav"
            fallback.write_bytes(dest.read_bytes())
            audio_rel = "stems/vocals.wav"
        record_result(
            manifest,
            manifest_path,
            SongRef(song_id, hash_prefix),
            status=target_status,
            source=f"r2_{source_name}",
            audio=audio_rel,
            producer="analysis_service_mel_band_roformer_ep_3005",
        )
        return song_id, target_status, f"r2_{source_name}"

    # 3. No R2 stems: download the full mix and separate serially.
    audio_dir = song_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / "audio.mp3"
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        r2_client.download_audio(hash_prefix, audio_path)
    print(f"  separating {song_id} via {sep_source} (input: {audio_path.name})")
    try:
        outputs = mvsep_fn(audio_path, song_dir)
    except Exception as e:  # noqa: BLE001 — record and retry on next invocation
        record_result(
            manifest,
            manifest_path,
            SongRef(song_id, hash_prefix),
            status=CacheStatus.FAILED,
            error=f"{type(e).__name__}: {e}",
        )
        return song_id, CacheStatus.FAILED, sep_source
    dry = pick_dry_vocals(outputs)
    if dry is None:
        record_result(
            manifest,
            manifest_path,
            SongRef(song_id, hash_prefix),
            status=CacheStatus.FAILED,
            error="separation produced no output files",
        )
        return song_id, CacheStatus.FAILED, sep_source
    stems_dir.mkdir(parents=True, exist_ok=True)
    (stems_dir / "clean_vocals.flac").write_bytes(dry.read_bytes())
    record_result(
        manifest,
        manifest_path,
        SongRef(song_id, hash_prefix),
        status=CacheStatus.CACHED,
        source=sep_source,
        audio="stems/clean_vocals.flac",
        producer=sep_producer,
    )
    return song_id, CacheStatus.CACHED, sep_source


def run(
    *,
    song_ids: list[str],
    cache_root: Path,
    manifest_path: Path,
    lock_path: Path,
    config_path: Path | None,
    dry_run: bool,
    override_refs: list[tuple[str, str]] | None = None,
    local: bool = False,
) -> RunSummary:
    """Resolve clean vocals for every song in *song_ids*.

    *override_refs* (issue #247 scale-out, review_queue set) supplies
    song_id → hash_prefix pairs straight from the Phase 0a snapshot; they
    are verified against the live DB instead of re-resolved.

    *local* (issue #247): separate with the local audio-separator two-stage
    pipeline (same models as the prod analysis service) instead of the
    MVSEP cloud API — no daily separation quota.  Still strictly serial.
    """
    summary = RunSummary()

    with try_lock_serial(lock_path):
        from stream_of_worship.admin.config import AdminConfig
        from stream_of_worship.admin.services.r2 import R2Client
        from stream_of_worship.db.connection import ConnectionProvider

        config = AdminConfig.load(config_path)
        provider = ConnectionProvider(config.get_connection_url())
        conn = provider.get_connection()
        manifest = load_or_init_manifest(manifest_path)
        try:
            if override_refs is not None:
                built, missing = build_song_refs(
                    conn,
                    [sid for sid, _ in override_refs],
                    known_prefixes=dict(override_refs),
                )
                # Snapshot songs deleted from the catalog since the Phase 0a
                # snapshot: per-song FAILED record (explicit fallback record,
                # issue #247) so the serial run continues and is resumable —
                # never a wholesale abort.
                for sid in missing:
                    hp = dict(override_refs)[sid]
                    summary.failed += 1
                    print(f"  {sid}: not in DB (deleted since snapshot) - recording failed")
                    record_result(
                        manifest,
                        manifest_path,
                        SongRef(sid, hp),
                        status=CacheStatus.FAILED,
                        error="song not found in DB (deleted since snapshot)",
                    )
            else:
                built, _ = build_song_refs(conn, song_ids)
        finally:
            provider.close()

        r2_client = R2Client(
            bucket=config.r2_bucket,
            endpoint_url=config.r2_endpoint_url,
            region=config.r2_region,
        )

        pending = next_pending(
            manifest, [SongRef(sid, hp) for sid, hp in built], cache_root=cache_root
        )
        summary.skipped = len(built) - len(pending)
        print(
            f"songs: {len(built)} total, {summary.skipped} already resolved, "
            f"{len(pending)} pending"
        )

        if dry_run:
            for ref in pending:
                print(f"  pending: {ref.song_id}")
            return summary

        import os

        if local:
            # Local audio-separator backend: no quota, models from
            # AUDIO_SEPARATOR_MODEL_DIR or DEFAULT_LOCAL_MODEL_DIR. Fail
            # early with a clear message instead of per-song import errors.
            model_dir = Path(
                os.environ.get("AUDIO_SEPARATOR_MODEL_DIR", str(DEFAULT_LOCAL_MODEL_DIR))
            )
            if not model_dir.exists():
                raise StemCacheError(
                    f"local separation requested but model dir {model_dir} does not "
                    "exist; set AUDIO_SEPARATOR_MODEL_DIR to the audio-separator "
                    "model cache"
                )
            sep_source = "local"
            sep_producer = LOCAL_MODEL_PRODUCER

            def mvsep_fn(audio_path: Path, output_dir: Path) -> list[Path]:
                return _local_separate(audio_path, output_dir, model_dir)

        else:
            mvsep_token = os.environ.get("MVSEP_API_KEY")
            sep_source = "mvsep"
            sep_producer = (
                f"mvsep_sep_type{MVSEP_STAGE1_SEP_TYPE}_opt{MVSEP_STAGE1_ADD_OPT1}"
                f"+sep_type{MVSEP_STAGE2_SEP_TYPE}_opt{MVSEP_STAGE2_ADD_OPT1}"
            )

            def mvsep_fn(audio_path: Path, output_dir: Path) -> list[Path]:
                if not mvsep_token:
                    # Lazy check: a tokenless run may still fully resolve via
                    # the R2 chain; only fail when separation is reached.
                    raise StemCacheError(
                        "MVSEP_API_KEY not set; required for songs without cached "
                        "R2 vocal stems (or rerun with --local for local separation)"
                    )
                return _mvsep_separate(audio_path, output_dir, mvsep_token)

        for ref in pending:
            song_id = ref.song_id
            try:
                _, status, source = process_song(
                    ref,
                    cache_root=cache_root,
                    manifest=manifest,
                    manifest_path=manifest_path,
                    r2_client=r2_client,
                    mvsep_fn=mvsep_fn,
                    mvsep_token=None,
                    sep_source=sep_source,
                    sep_producer=sep_producer,
                )
            except Exception as e:  # noqa: BLE001 — record and continue serially
                try:
                    record_result(
                        manifest,
                        manifest_path,
                        ref,
                        status=CacheStatus.FAILED,
                        error=f"{type(e).__name__}: {e}",
                    )
                except StemCacheError as guard_err:
                    # A terminal entry requeued for a missing local file must
                    # not be downgraded to failed: keep the terminal status
                    # and let the per-song error surface in the summary.
                    print(f"  [warn] {song_id}: keeping manifest status ({guard_err})")
                    status, source = CacheStatus.FAILED, "guard"
                else:
                    status, source = CacheStatus.FAILED, type(e).__name__
            if status == CacheStatus.CACHED:
                summary.cached += 1
            elif status == CacheStatus.FALLBACK:
                summary.fallback += 1
            else:
                summary.failed += 1
            print(f"  {song_id}: {status.value} ({source})")

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--set",
        choices=("phase12", "phase3_positive", "review_queue"),
        default="phase12",
        help=(
            "Which song set to process (default: phase12). phase3_positive: "
            "seeded random sample (<=60) of the Phase 0a positive snapshot "
            "(eval/lrc_truth/positive.txt). review_queue: every song in the "
            "Phase 0a review-queue snapshot (eval/lrc_truth/latest.json)."
        ),
    )
    parser.add_argument(
        "--song-id",
        action="append",
        default=None,
        help="Explicit song IDs (overrides --set; repeatable)",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=DEFAULT_SNAPSHOT,
        help=f"Phase 0a snapshot JSON (--set review_queue; default: {DEFAULT_SNAPSHOT})",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Cache root (default: the admin cache dir from config)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"Manifest path (default: {DEFAULT_MANIFEST})",
    )
    parser.add_argument(
        "--lock",
        type=Path,
        default=DEFAULT_LOCK,
        help=f"Serial lock path (default: {DEFAULT_LOCK})",
    )
    parser.add_argument("--config", type=Path, default=None, help="Admin config path")
    parser.add_argument(
        "--local",
        action="store_true",
        help=(
            "Separate locally with audio-separator (MelBand Roformer ep_3005 "
            f"+ UVR-De-Echo, models from {DEFAULT_LOCAL_MODEL_DIR}) instead of "
            "the MVSEP cloud API — no daily quota. Requires an env with "
            "audio-separator installed (e.g. ops/analysis-service --extra "
            "service)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List pending songs without downloading or separating",
    )
    args = parser.parse_args(argv)

    refs: list[tuple[str, str]] | None = None
    if args.song_id is not None:
        song_ids = list(args.song_id)
    elif args.set == "phase12":
        song_ids = list(PHASE12_SET)
    elif args.set == "phase3_positive":
        # Phase 3 calibration sample: seeded random sample of <=60 positives
        # (all of them when the snapshot has <=60); DB lookup still resolves
        # each song's canonical hash_prefix.
        song_ids = sample_positive(load_positive_ids())
        print(
            f"phase3 positive sample: {len(song_ids)} songs "
            f"(seed={SEED}, sample_size={PHASE3_SAMPLE_SIZE})"
        )
    else:  # review_queue
        # Full triage population straight from the Phase 0a snapshot. The
        # snapshot's hash_prefix column is verified against the live DB
        # inside run() before anything is cached.
        refs = load_review_queue_ids(args.snapshot)
        song_ids = [sid for sid, _ in refs]
        print(f"review queue: {len(refs)} songs from {args.snapshot}")

    if args.cache_dir is not None:
        cache_root = args.cache_dir
    else:
        # Default to the same root the Phase 1/2 consumer
        # (poc/experiment_lrc_signals.py, via sow_legacy_cli_tui paths) resolves
        # stems from: ~/.cache/stream-of-worship on Linux — NOT the
        # sow-admin cache dir.
        from sow_legacy_cli_tui.core.paths import get_cache_dir as legacy_get_cache_dir

        cache_root = legacy_get_cache_dir()

    try:
        summary = run(
            song_ids=song_ids,
            cache_root=cache_root,
            manifest_path=args.manifest,
            lock_path=args.lock,
            config_path=args.config,
            dry_run=args.dry_run,
            override_refs=refs,
            local=args.local,
        )
    except StemCacheError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"done: {summary}")
    # nonzero exit only when something failed; cached/fallback/skipped = success
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
