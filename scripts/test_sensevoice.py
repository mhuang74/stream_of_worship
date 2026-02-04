#!/usr/bin/env python3
"""
Test script for SenseVoice transcription with LRC format output.

Usage:
    python test_sensevoice.py <audio_file>

Output:
    Creates a .sensevoice.lrc file with timestamps in [mm:ss.xx] format.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Optional


def format_lrc_timestamp(seconds: float) -> str:
    """Convert seconds to LRC timestamp format [mm:ss.xx]."""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    centis = int((seconds % 1) * 100)
    return f"[{minutes:02d}:{secs:02d}.{centis:02d}]"


def write_lrc_file(output_path: Path, segments: list[tuple[float, str]]) -> None:
    """Write transcription segments to LRC file."""
    with open(output_path, 'w', encoding='utf-8') as f:
        for timestamp, text in segments:
            lrc_line = f"{format_lrc_timestamp(timestamp)}{text}"
            f.write(lrc_line + '\n')


def transcribe_with_sensevoice(audio_path: Path) -> list[tuple[float, str]]:
    """
    Transcribe audio file using SenseVoice.
    
    Returns list of (timestamp, text) tuples.
    """
    try:
        from funasr import AutoModel
        from funasr.utils.postprocess_utils import rich_transcription_postprocess
    except ImportError:
        print("Error: funasr is required. Install with: pip install funasr")
        sys.exit(1)

    model_dir = "iic/SenseVoiceSmall"
    
    print(f"Loading SenseVoice model from {model_dir}...")
    model = AutoModel(
        model=model_dir,
        trust_remote_code=True,
        remote_code="./model.py",
        vad_model="fsmn-vad",
        vad_kwargs={"max_single_segment_time": 30000},
        device="cpu",
    )

    print(f"Transcribing: {audio_path}")
    result = model.generate(
        input=str(audio_path),
        cache={},
        language="zh",
        use_itn=True,
        batch_size_s=60,
        merge_vad=True,
        merge_length_s=15,
    )

    segments = []
    if result and len(result) > 0:
        for res in result:
            if 'sentence_info' in res:
                for seg in res['sentence_info']:
                    start = seg.get('start', 0) / 1000.0  # Convert ms to seconds
                    text = seg.get('text', '').strip()
                    if text:
                        segments.append((start, text))
            elif 'text' in res:
                # Fallback for simple text output
                text = res['text'].strip()
                if text:
                    segments.append((0.0, text))

    return segments


def main():
    parser = argparse.ArgumentParser(
        description="Transcribe audio using SenseVoice and output LRC format"
    )
    parser.add_argument(
        "audio_file",
        type=str,
        help="Path to audio file to transcribe"
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        help="Output LRC file path (default: input.sensevoice.lrc)"
    )
    
    args = parser.parse_args()
    
    audio_path = Path(args.audio_file)
    if not audio_path.exists():
        print(f"Error: Audio file not found: {audio_path}")
        sys.exit(1)
    
    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = audio_path.parent / f"{audio_path.stem}.sensevoice.lrc"
    
    # Transcribe
    try:
        segments = transcribe_with_sensevoice(audio_path)
    except Exception as e:
        print(f"Error during transcription: {e}")
        sys.exit(1)
    
    if not segments:
        print("Warning: No transcription results generated")
        sys.exit(1)
    
    # Write LRC file
    write_lrc_file(output_path, segments)
    print(f"LRC file saved to: {output_path}")
    print(f"Transcribed {len(segments)} segments")


if __name__ == "__main__":
    main()
