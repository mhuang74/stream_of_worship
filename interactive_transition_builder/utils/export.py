"""
Export utility for saving transitions to FLAC + JSON.

Exports generated transitions with full parameter metadata.
"""

import soundfile as sf
import json
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Tuple, Optional

from ..models import TransitionConfig


def export_transition(
    audio_data: np.ndarray,
    config: TransitionConfig,
    output_path: Path,
    sample_rate: int = 44100,
    metadata: Optional[dict] = None
) -> Tuple[Path, Path]:
    """
    Export transition audio to FLAC and metadata to JSON.

    Args:
        audio_data: Stereo audio array (2, num_samples)
        config: TransitionConfig with generation parameters
        output_path: Output file path (with or without extension)
        sample_rate: Audio sample rate
        metadata: Optional additional metadata from generation

    Returns:
        Tuple of (audio_path, metadata_path)

    Raises:
        IOError: If export fails
    """
    # Ensure output path has no extension
    output_path = Path(output_path)
    if output_path.suffix:
        output_path = output_path.with_suffix('')

    # Create output directory if needed
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Export paths
    audio_path = output_path.with_suffix('.flac')
    metadata_path = output_path.with_suffix('.json')

    try:
        # Export audio to FLAC
        # Transpose for soundfile (expects num_samples, channels)
        if audio_data.shape[0] == 2:
            audio_data = audio_data.T

        sf.write(
            str(audio_path),
            audio_data,
            sample_rate,
            format='FLAC',
            subtype='PCM_16'
        )

        # Build metadata
        metadata_dict = config.to_dict()

        # Add audio info
        metadata_dict['audio'] = {
            'sample_rate': sample_rate,
            'channels': 2,
            'duration': audio_data.shape[0] / sample_rate,
            'format': 'flac',
            'num_samples': audio_data.shape[0]
        }

        # Add generation metadata if provided
        if metadata:
            metadata_dict['generation'] = metadata

        # Add export info
        metadata_dict['exported_at'] = datetime.now().isoformat()
        metadata_dict['audio_file'] = audio_path.name

        # Export metadata to JSON
        with open(metadata_path, 'w') as f:
            json.dump(metadata_dict, f, indent=2)

        print(f"\n✓ Exported transition:")
        print(f"  Audio: {audio_path}")
        print(f"  Metadata: {metadata_path}")
        print(f"  Duration: {metadata_dict['audio']['duration']:.1f}s")

        return audio_path, metadata_path

    except Exception as e:
        raise IOError(f"Failed to export transition: {e}")


def generate_default_filename(config: TransitionConfig) -> str:
    """
    Generate default filename for transition.

    Format: transition_{songA}_{sectionA}_to_{songB}_{sectionB}

    Args:
        config: TransitionConfig

    Returns:
        Filename (without extension)
    """
    # Get song stems (remove extension)
    song_a_stem = Path(config.song_a.filename).stem if config.song_a else "unknown"
    song_b_stem = Path(config.song_b.filename).stem if config.song_b else "unknown"

    # Get section labels
    section_a_label = config.section_a.label if config.section_a else "unknown"
    section_b_label = config.section_b.label if config.section_b else "unknown"

    # Build filename
    filename = f"transition_{song_a_stem}_{section_a_label}_to_{song_b_stem}_{section_b_label}"

    # Clean filename (replace spaces with underscores)
    filename = filename.replace(' ', '_').lower()

    return filename
