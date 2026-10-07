#!/usr/bin/env python3
"""Phase 0c runner: serial stem-separation cache for the LRC triage set.

Implements the runner of ``specs/lrc-review-triage-cascade-design-v2.md``
Phase 0c (issue #244). For each song in the target set:

1. Skip if the manifest already records ``cached``/``fallback`` (resume).
2. Reuse an on-disk ``clean_vocals.flac`` if a previous run left one without
   recording (crash recovery).
3. Download the best R2 vocal stem (``vocals_dry.flac`` preferred) and copy
   it to ``stems/clean_vocals.flac`` — no separation needed.
4. Else download the full mix and run two-stage MVSEP separation
   (BS-Roformer + Reverb Removal, matching the prod analysis-service
   configuration), then record the dry output as ``clean_vocals.flac``.

Everything runs strictly serially; a flock guard makes concurrent runner
invocations fail loudly. Failed songs are recorded and retried on the next
invocation. Zero writes to canonical Lyrics, catalog status, provenance, or
visibility.

Usage (from repo root):
    uv run --project lab/poc-scripts --extra stem_separation --extra test \
        python lab/poc-scripts/run_stem_cache.py \
        [--set phase12] [--cache-dir <path>] [--manifest <path>] [--dry-run]

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
# Anchored to the module directory, not cwd: the serial lock must be the same
# file no matter where the runner is invoked from.
DEFAULT_MANIFEST = _SCRIPT_DIR / "eval/lrc_truth/stem_cache/manifest.json"
DEFAULT_LOCK = _SCRIPT_DIR / "eval/lrc_truth/stem_cache/serial.lock"

MVSEP_STAGE1_SEP_TYPE = 48  # MelBand Roformer (matches prod analysis service)
MVSEP_STAGE1_ADD_OPT1 = 11  # becruily deux, best vocals
MVSEP_STAGE2_SEP_TYPE = 22  # Reverb Removal
MVSEP_STAGE2_ADD_OPT1 = 0
MVSEP_OUTPUT_FORMAT = 2  # FLAC 16-bit


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


def build_song_refs(conn, song_ids: list[str]) -> list[tuple[str, str]]:
    """Resolve song IDs → hash prefixes via a read-only DB query."""
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
    if missing:
        raise StemCacheError(f"songs not found in DB (deleted or unknown): {missing}")
    ambiguous = {sid: hps for sid, hps in rows.items() if len(hps) > 1}
    if ambiguous:
        raise StemCacheError(
            "songs with multiple live recordings - cannot pick a canonical "
            f"hash_prefix; resolve manually: {ambiguous}"
        )
    # preserve the caller's order (the runner's deterministic serial order)
    return [(sid, rows[sid][0]) for sid in song_ids]


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


def process_song(
    ref: SongRef,
    *,
    cache_root: Path,
    manifest: StemCacheManifest,
    manifest_path: Path,
    r2_client,
    mvsep_fn: Callable[[Path, Path], list[Path]],
    mvsep_token: str | None = None,
) -> tuple[str, CacheStatus, str]:
    """Resolve clean vocals for one song through the priority chain.

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

    # 3. No R2 stems: download the full mix and separate serially via MVSEP.
    audio_dir = song_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / "audio.mp3"
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        r2_client.download_audio(hash_prefix, audio_path)
    print(f"  separating {song_id} via MVSEP (input: {audio_path.name})")
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
        return song_id, CacheStatus.FAILED, "mvsep"
    dry = pick_dry_vocals(outputs)
    if dry is None:
        record_result(
            manifest,
            manifest_path,
            SongRef(song_id, hash_prefix),
            status=CacheStatus.FAILED,
            error="MVSEP produced no output files",
        )
        return song_id, CacheStatus.FAILED, "mvsep"
    stems_dir.mkdir(parents=True, exist_ok=True)
    (stems_dir / "clean_vocals.flac").write_bytes(dry.read_bytes())
    record_result(
        manifest,
        manifest_path,
        SongRef(song_id, hash_prefix),
        status=CacheStatus.CACHED,
        source="mvsep",
        audio="stems/clean_vocals.flac",
        producer=(
            f"mvsep_sep_type{MVSEP_STAGE1_SEP_TYPE}_opt{MVSEP_STAGE1_ADD_OPT1}"
            f"+sep_type{MVSEP_STAGE2_SEP_TYPE}_opt{MVSEP_STAGE2_ADD_OPT1}"
        ),
    )
    return song_id, CacheStatus.CACHED, "mvsep"


def run(
    *,
    song_ids: list[str],
    cache_root: Path,
    manifest_path: Path,
    lock_path: Path,
    config_path: Path | None,
    dry_run: bool,
) -> RunSummary:
    summary = RunSummary()

    with try_lock_serial(lock_path):
        from stream_of_worship.admin.config import AdminConfig
        from stream_of_worship.admin.services.r2 import R2Client
        from stream_of_worship.db.connection import ConnectionProvider

        config = AdminConfig.load(config_path)
        provider = ConnectionProvider(config.get_connection_url())
        conn = provider.get_connection()
        try:
            refs = build_song_refs(conn, song_ids)
        finally:
            provider.close()

        r2_client = R2Client(
            bucket=config.r2_bucket,
            endpoint_url=config.r2_endpoint_url,
            region=config.r2_region,
        )

        manifest = load_or_init_manifest(manifest_path)
        pending = next_pending(
            manifest, [SongRef(sid, hp) for sid, hp in refs], cache_root=cache_root
        )
        summary.skipped = len(refs) - len(pending)
        print(
            f"songs: {len(refs)} total, {summary.skipped} already resolved, "
            f"{len(pending)} pending"
        )

        if dry_run:
            for ref in pending:
                print(f"  pending: {ref.song_id}")
            return summary

        import os

        mvsep_token = os.environ.get("MVSEP_API_KEY")

        def mvsep_fn(audio_path: Path, output_dir: Path) -> list[Path]:
            if not mvsep_token:
                # Lazy check: a tokenless run may still fully resolve via the
                # R2 chain; only fail when separation is actually reached.
                raise StemCacheError(
                    "MVSEP_API_KEY not set; required for songs without cached " "R2 vocal stems"
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
                    mvsep_token=mvsep_token,
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
        choices=("phase12",),
        default="phase12",
        help="Which song set to process (default: phase12)",
    )
    parser.add_argument(
        "--song-id",
        action="append",
        default=None,
        help="Explicit song IDs (overrides --set; repeatable)",
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
        "--dry-run",
        action="store_true",
        help="List pending songs without downloading or separating",
    )
    args = parser.parse_args(argv)

    song_ids = list(PHASE12_SET) if args.song_id is None else list(args.song_id)

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
        )
    except StemCacheError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"done: {summary}")
    # nonzero exit only when something failed; cached/fallback/skipped = success
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
