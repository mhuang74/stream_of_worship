#!/usr/bin/env python3
"""
POC: Two-Stage Vocal Extraction via MVSEP Cloud API

Mirrors gen_clean_vocal_stem.py but uses the MVSEP API instead of local models.
Pipeline: BS Roformer (sep_type=40) → Reverb Removal (sep_type=22)

API token: --api-token CLI arg or MVSEP_API_KEY env var.
"""

import argparse
import json
import os
import time
from pathlib import Path

import requests

MVSEP_API_BASE = "https://mvsep.com/api/separation"
POLL_INITIAL_INTERVAL = 5.0
POLL_MAX_INTERVAL = 30.0
POLL_BACKOFF_FACTOR = 1.5
DEFAULT_TIMEOUT = 900.0


def submit_job(
    audio_path: Path,
    api_token: str,
    sep_type: int,
    add_opt1: int = 0,
    add_opt2: int | None = None,
    output_format: int = 2,
) -> str:
    data = {
        "api_token": api_token,
        "sep_type": sep_type,
        "add_opt1": add_opt1,
        "output_format": output_format,
    }
    if add_opt2 is not None:
        data["add_opt2"] = add_opt2

    with open(audio_path, "rb") as f:
        files = {"audiofile": (audio_path.name, f)}
        resp = requests.post(
            f"{MVSEP_API_BASE}/create", data=data, files=files, timeout=60
        )

    resp.raise_for_status()
    body = resp.json()
    if not body.get("success"):
        raise RuntimeError(f"MVSEP API error on submit: {body}")

    return body["data"]["hash"]


def poll_job(job_hash: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    interval = POLL_INITIAL_INTERVAL
    start = time.time()

    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            raise TimeoutError(f"Job {job_hash} timed out after {elapsed:.0f}s")

        resp = requests.get(
            f"{MVSEP_API_BASE}/get", params={"hash": job_hash}, timeout=30
        )
        resp.raise_for_status()
        body = resp.json()

        if not body.get("success"):
            raise RuntimeError(f"MVSEP API error on poll: {body}")

        data = body["data"]
        status = body.get("status", "unknown")
        print(f"  Status: {status} (elapsed {elapsed:.0f}s)")

        if status == "done":
            return data
        if status in ("failed", "not_found", "error"):
            raise RuntimeError(f"Job {job_hash} ended with status: {status}")

        time.sleep(min(interval, POLL_MAX_INTERVAL))
        interval = min(interval * POLL_BACKOFF_FACTOR, POLL_MAX_INTERVAL)


def download_files(file_entries: list[dict], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []
    for entry in file_entries:
        url = entry.get("download_link") or entry.get("url") or entry.get("link")
        if not url:
            continue
        filename = entry.get("name") or entry.get("download") or url.split("/")[-1].split("?")[0]
        dest = output_dir / filename
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
        downloaded.append(dest)
    return downloaded


def extract_vocals_two_stage_mvsep(
    input_path: Path,
    output_dir: Path,
    api_token: str,
    vocal_model: int = 81,
    dereverb_model: int = 0,
    output_format: int = 2,
    reuse_stage1: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    results = {
        "input": str(input_path),
        "stages": {},
    }
    total_start = time.time()

    # === STAGE 1: Vocal Separation (BS Roformer) ===
    print("\n" + "=" * 60)
    print("STAGE 1: Vocal Separation (BS Roformer, sep_type=40)")
    print("=" * 60)

    stage1_dir = output_dir / "stage1_vocal_separation"
    stage1_dir.mkdir(exist_ok=True)

    vocals_file = None
    instrumental_file = None
    stage1_outputs = []
    stage1_time = 0.0

    if reuse_stage1:
        for f in sorted(stage1_dir.iterdir()):
            if f.is_file() and "vocal" in f.name.lower():
                vocals_file = f
                break
        if vocals_file:
            print("Reusing existing Stage 1 vocals.")
            for f in sorted(stage1_dir.iterdir()):
                if f.is_file():
                    stage1_outputs.append(str(f))
                    if "instrumental" in f.name.lower() or "accompaniment" in f.name.lower():
                        instrumental_file = f
        else:
            print("No existing Stage 1 vocals found; running separation.")
            reuse_stage1 = False

    if not reuse_stage1:
        stage1_start = time.time()
        print(f"Submitting Stage 1 job (sep_type=40, add_opt1={vocal_model})...")
        job_hash = submit_job(
            input_path,
            api_token,
            sep_type=40,
            add_opt1=vocal_model,
            output_format=output_format,
        )
        print(f"Job submitted: {job_hash}")
        data = poll_job(job_hash, timeout=timeout)
        stage1_outputs_paths = download_files(data.get("files", []), stage1_dir)
        stage1_time = time.time() - stage1_start

        for path in stage1_outputs_paths:
            stage1_outputs.append(str(path))
            name_lower = path.name.lower()
            if "vocal" in name_lower:
                vocals_file = path
            elif "instrumental" in name_lower or "accompaniment" in name_lower:
                instrumental_file = path

    results["stages"]["stage1"] = {
        "model": f"BS Roformer (add_opt1={vocal_model})",
        "process_time_s": round(stage1_time, 2),
        "outputs": stage1_outputs,
        "vocals_file": str(vocals_file) if vocals_file else None,
        "instrumental_file": str(instrumental_file) if instrumental_file else None,
    }

    print(f"\nStage 1 outputs:")
    for f in stage1_outputs:
        print(f"  - {f}")

    if not vocals_file or not vocals_file.exists():
        print("\nERROR: No vocals file found from Stage 1")
        results["total_time_s"] = round(time.time() - total_start, 2)
        return results

    # === STAGE 2: Reverb Removal ===
    print("\n" + "=" * 60)
    print("STAGE 2: Reverb Removal (sep_type=22)")
    print("=" * 60)

    stage2_dir = output_dir / "stage2_dereverb"
    stage2_dir.mkdir(exist_ok=True)

    stage2_start = time.time()
    print(f"Submitting Stage 2 job (sep_type=22, add_opt1={dereverb_model}, add_opt2=1)...")
    job_hash = submit_job(
        vocals_file,
        api_token,
        sep_type=22,
        add_opt1=dereverb_model,
        add_opt2=1,
        output_format=output_format,
    )
    print(f"Job submitted: {job_hash}")
    data = poll_job(job_hash, timeout=timeout)
    stage2_outputs_paths = download_files(data.get("files", []), stage2_dir)
    stage2_time = time.time() - stage2_start

    stage2_outputs = [str(p) for p in stage2_outputs_paths]
    dry_vocals_file = None
    reverb_file = None
    for path in stage2_outputs_paths:
        name_lower = path.name.lower()
        if "no reverb" in name_lower or "noreverb" in name_lower:
            dry_vocals_file = path
        else:
            reverb_file = path

    if not dry_vocals_file and stage2_outputs_paths:
        dry_vocals_file = stage2_outputs_paths[0]

    results["stages"]["stage2"] = {
        "model": f"Reverb Removal (add_opt1={dereverb_model})",
        "process_time_s": round(stage2_time, 2),
        "outputs": stage2_outputs,
        "dry_vocals_file": str(dry_vocals_file) if dry_vocals_file else None,
        "reverb_file": str(reverb_file) if reverb_file else None,
    }

    print(f"\nStage 2 outputs:")
    for f in stage2_outputs:
        print(f"  - {f}")

    # === SUMMARY ===
    total_time = time.time() - total_start
    results["total_time_s"] = round(total_time, 2)

    print("\n" + "=" * 60)
    print("EXTRACTION COMPLETE")
    print("=" * 60)
    print(f"\nInput: {input_path}")
    print(f"Total time: {total_time:.1f}s")
    print(f"\nOutput files:")
    print(f"  Instrumental:       {results['stages']['stage1'].get('instrumental_file')}")
    print(f"  Vocals (wet):       {results['stages']['stage1'].get('vocals_file')}")
    print(f"  Vocals (dry):       {results['stages']['stage2'].get('dry_vocals_file')}")
    print(f"  Reverb only:        {results['stages']['stage2'].get('reverb_file')}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Two-stage vocal extraction via MVSEP cloud API: BS Roformer + Reverb Removal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s input.mp3
  %(prog)s input.mp3 -o /tmp/mvsep_out --vocal-model 29
  %(prog)s input.mp3 --reuse-stage1

--vocal-model variants (sep_type=40, BS Roformer):
  81  BS Roformer ver 2025.07, SDR 11.89 (default)
  29  BS Roformer ver 2024.08, SDR 11.24
  (see MVSEP docs for full list)

--dereverb-model variants (sep_type=22, Reverb Removal):
  0   FoxJoy MDX23C (default)
  (see MVSEP docs for full list)
        """,
    )
    parser.add_argument("input", type=Path, help="Input audio file")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: ./vocal_extraction_output/<stem>)",
    )
    parser.add_argument("--api-token", type=str, default=None, help="MVSEP API token")
    parser.add_argument(
        "--vocal-model",
        type=int,
        default=81,
        help="Stage 1 BS Roformer add_opt1 variant (default: 81)",
    )
    parser.add_argument(
        "--dereverb-model",
        type=int,
        default=0,
        help="Stage 2 reverb removal add_opt1 variant (default: 0)",
    )
    parser.add_argument(
        "--output-format",
        type=int,
        default=2,
        help="MVSEP output format code (default: 2 = FLAC 16-bit)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Max seconds to wait per stage (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--reuse-stage1",
        action="store_true",
        help="Reuse existing Stage 1 vocals if found in output dir",
    )

    args = parser.parse_args()

    api_token = args.api_token or os.environ.get("MVSEP_API_KEY")
    if not api_token:
        print("ERROR: MVSEP API token required. Use --api-token or set MVSEP_API_KEY env var.")
        return 1

    if not args.input.exists():
        print(f"ERROR: Input file not found: {args.input}")
        return 1

    if args.output_dir is None:
        args.output_dir = Path("vocal_extraction_output") / args.input.stem

    print("=" * 60)
    print("Two-Stage Vocal Extraction via MVSEP Cloud API")
    print("=" * 60)
    print(f"Input:          {args.input}")
    print(f"Output dir:     {args.output_dir}")
    print(f"Vocal model:    {args.vocal_model}")
    print(f"Dereverb model: {args.dereverb_model}")
    print(f"Output format:  {args.output_format}")
    print(f"Timeout:        {args.timeout}s per stage")

    try:
        results = extract_vocals_two_stage_mvsep(
            input_path=args.input,
            output_dir=args.output_dir,
            api_token=api_token,
            vocal_model=args.vocal_model,
            dereverb_model=args.dereverb_model,
            output_format=args.output_format,
            reuse_stage1=args.reuse_stage1,
            timeout=args.timeout,
        )
    except (RuntimeError, TimeoutError) as e:
        print(f"\nERROR: {e}")
        return 1

    results_file = args.output_dir / "extraction_results.json"
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {results_file}")

    return 0


if __name__ == "__main__":
    exit(main())
