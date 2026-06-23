#!/usr/bin/env python3
"""
POC: Two-Stage Vocal Extraction with Echo Removal

This script tests a two-stage vocal extraction pipeline:
1. Stage 1: Extract vocals using BS-Roformer-Viperx-1297 (high-quality vocal separation)
2. Stage 2: Remove echo/reverb using UVR-De-Echo-Normal

Models used:
- BS-Roformer-Viperx-1297: model_bs_roformer_ep_317_sdr_12.9755.ckpt
- UVR-De-Echo-Normal: UVR-De-Echo-Normal.pth

Reference: https://github.com/nomadkaraoke/python-audio-separator
"""

import argparse
from pathlib import Path
import time


def extract_vocals_two_stage(
    input_path: Path,
    output_dir: Path,
    vocal_model: str = "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
    dereverb_model: str = "UVR-De-Echo-Normal.pth",
    reuse_stage1: bool = False,
) -> dict:
    """
    Two-stage vocal extraction pipeline.

    Stage 1: Extract vocals from the mix using BS-Roformer
    Stage 2: Remove echo/reverb from extracted vocals

    Args:
        input_path: Path to input audio file
        output_dir: Directory for output files
        vocal_model: Model filename for vocal extraction
        dereverb_model: Model filename for echo removal

    Returns:
        Dictionary with paths to all output files and timing info
    """
    from audio_separator.separator import Separator

    output_dir.mkdir(parents=True, exist_ok=True)
    results = {
        "input": str(input_path),
        "stages": {},
    }

    # === STAGE 1: Vocal Extraction ===
    print("\n" + "=" * 60)
    print("STAGE 1: Vocal Extraction (BS-Roformer-Viperx-1297)")
    print("=" * 60)

    stage1_dir = output_dir / "stage1_vocal_separation"
    stage1_dir.mkdir(exist_ok=True)

    def _find_stage1_stems(directory: Path) -> tuple[Path | None, Path | None, list[str]]:
        vocals = None
        instrumental = None
        outputs = []
        for output_path in sorted(directory.glob("*")):
            if not output_path.is_file():
                continue
            name = output_path.name
            outputs.append(str(output_path))
            if "Vocals" in name or "vocals" in name:
                vocals = output_path
            elif "Instrumental" in name or "instrumental" in name:
                instrumental = output_path
        return vocals, instrumental, outputs

    if reuse_stage1:
        vocals_file, instrumental_file, stage1_outputs = _find_stage1_stems(stage1_dir)
        if vocals_file:
            print("Reusing existing Stage 1 outputs in output dir.")
            load_time = 0.0
            process_time = 0.0
        else:
            print("No existing Stage 1 vocals found; running separation.")
            reuse_stage1 = False

    if not reuse_stage1:
        separator = Separator(
            output_dir=str(stage1_dir),
            output_format="FLAC",
        )

        print(f"Loading model: {vocal_model}")
        start_time = time.time()
        separator.load_model(model_filename=vocal_model)
        load_time = time.time() - start_time
        print(f"Model loaded in {load_time:.1f}s")

        print(f"Processing: {input_path.name}")
        start_time = time.time()
        stage1_outputs = separator.separate(str(input_path))
        process_time = time.time() - start_time
        print(f"Separation completed in {process_time:.1f}s")

    # Release Stage 1 model memory before loading Stage 2
    if not reuse_stage1:
        del separator
        import gc
        gc.collect()

    # Find the vocals output file
    if not reuse_stage1:
        vocals_file, instrumental_file, _ = _find_stage1_stems(stage1_dir)

    results["stages"]["stage1"] = {
        "model": vocal_model,
        "load_time_s": round(load_time, 2),
        "process_time_s": round(process_time, 2),
        "outputs": stage1_outputs,
        "vocals_file": str(vocals_file) if vocals_file else None,
        "instrumental_file": str(instrumental_file) if instrumental_file else None,
    }

    print(f"\nStage 1 outputs:")
    for f in stage1_outputs:
        print(f"  - {f}")

    if not vocals_file or not vocals_file.exists():
        print("\nERROR: No vocals file found from Stage 1")
        return results

    # === STAGE 2: Echo/Reverb Removal ===
    print("\n" + "=" * 60)
    print("STAGE 2: Echo/Reverb Removal (UVR-De-Echo-Normal)")
    print("=" * 60)

    stage2_dir = output_dir / "stage2_dereverb"
    stage2_dir.mkdir(exist_ok=True)

    # Create new separator instance for dereverb model
    separator_dereverb = Separator(
        output_dir=str(stage2_dir),
        output_format="FLAC",
    )

    print(f"Loading model: {dereverb_model}")
    start_time = time.time()
    separator_dereverb.load_model(model_filename=dereverb_model)
    load_time = time.time() - start_time
    print(f"Model loaded in {load_time:.1f}s")

    print(f"Processing: {vocals_file.name}")
    start_time = time.time()
    stage2_outputs = separator_dereverb.separate(str(vocals_file))
    process_time = time.time() - start_time
    print(f"Dereverb completed in {process_time:.1f}s")

    # Find the dry (no reverb) output
    dry_vocals_file = None
    reverb_file = None
    for output_file in stage2_outputs:
        output_path = Path(output_file)
        if not output_path.is_absolute():
            output_path = stage2_dir / output_path
        name_lower = output_path.name.lower()
        # De-Echo models typically output "No Echo" and "Echo" stems
        if "no echo" in name_lower or "dry" in name_lower or "no_echo" in name_lower:
            dry_vocals_file = output_path
        elif "echo" in name_lower or "reverb" in name_lower:
            reverb_file = output_path

    # If we couldn't identify by name, take the first output as dry vocals
    if not dry_vocals_file and stage2_outputs:
        dry_vocals_file = Path(stage2_outputs[0])

    results["stages"]["stage2"] = {
        "model": dereverb_model,
        "load_time_s": round(load_time, 2),
        "process_time_s": round(process_time, 2),
        "outputs": stage2_outputs,
        "dry_vocals_file": str(dry_vocals_file) if dry_vocals_file else None,
        "reverb_file": str(reverb_file) if reverb_file else None,
    }

    print(f"\nStage 2 outputs:")
    for f in stage2_outputs:
        print(f"  - {f}")

    # === SUMMARY ===
    print("\n" + "=" * 60)
    print("EXTRACTION COMPLETE")
    print("=" * 60)

    total_time = sum(
        stage["load_time_s"] + stage["process_time_s"]
        for stage in results["stages"].values()
    )
    results["total_time_s"] = round(total_time, 2)

    print(f"\nInput: {input_path}")
    print(f"Total processing time: {total_time:.1f}s")
    print(f"\nOutput files:")
    print(f"  Instrumental: {results['stages']['stage1'].get('instrumental_file')}")
    print(f"  Vocals (with reverb): {results['stages']['stage1'].get('vocals_file')}")
    print(f"  Vocals (dry/no echo): {results['stages']['stage2'].get('dry_vocals_file')}")
    print(f"  Reverb/Echo only: {results['stages']['stage2'].get('reverb_file')}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Two-stage vocal extraction: BS-Roformer + De-Echo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s input.mp3
  %(prog)s input.flac -o ./output
  %(prog)s input.wav --dereverb-model UVR-De-Echo-Aggressive.pth

Available de-reverb models:
  - UVR-De-Echo-Normal.pth (default, balanced)
  - UVR-De-Echo-Aggressive.pth (stronger echo removal)
  - UVR-DeEcho-DeReverb.pth (combined de-echo and de-reverb)
        """,
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Input audio file (mp3, flac, wav, etc.)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: ./vocal_extraction_output/<input_stem>)",
    )
    parser.add_argument(
        "--vocal-model",
        type=str,
        default="model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        help="Model for vocal extraction (default: BS-Roformer-Viperx-1297)",
    )
    parser.add_argument(
        "--dereverb-model",
        type=str,
        default="UVR-De-Echo-Normal.pth",
        help="Model for echo/reverb removal (default: UVR-De-Echo-Normal)",
    )
    parser.add_argument(
        "--reuse-stage1",
        action="store_true",
        help="Reuse existing Stage 1 outputs if found in output dir",
    )

    args = parser.parse_args()

    if not args.input.exists():
        print(f"ERROR: Input file not found: {args.input}")
        return 1

    if args.output_dir is None:
        args.output_dir = Path("vocal_extraction_output") / args.input.stem

    print("=" * 60)
    print("Two-Stage Vocal Extraction POC")
    print("=" * 60)
    print(f"Input: {args.input}")
    print(f"Output dir: {args.output_dir}")
    print(f"Vocal model: {args.vocal_model}")
    print(f"De-reverb model: {args.dereverb_model}")

    results = extract_vocals_two_stage(
        input_path=args.input,
        output_dir=args.output_dir,
        vocal_model=args.vocal_model,
        dereverb_model=args.dereverb_model,
        reuse_stage1=args.reuse_stage1,
    )

    # Save results summary as JSON
    import json

    results_file = args.output_dir / "extraction_results.json"
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {results_file}")

    return 0


if __name__ == "__main__":
    exit(main())
