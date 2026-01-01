# Implementation Plan: All-In-One Library Integration

## Overview
Create a new standalone POC analysis script using the all-in-one music structure analyzer library that can be run separately from the existing librosa-based script for side-by-side comparison.

## Goals
- Create `poc/poc_analysis_allinone.py` that mirrors the structure of `poc/poc_analysis.py`
- Use all-in-one library for beat detection, tempo, segment boundaries, and labels
- Output results to `poc_output_allinone/` directory
- Enable independent execution and comparison with librosa-based results
- Store embeddings for future compatibility analysis enhancements

## Critical Files

### Files to Create
- `poc/poc_analysis_allinone.py` - New analysis script using all-in-one
- `poc/compare_results.py` - Comparison utility to analyze differences

### Files to Modify
- `pyproject.toml` - Add PyTorch, NATTEN, and allin1 dependencies
- `Dockerfile` - Update to support PyTorch and NATTEN installation
- `docker-compose.yml` - Ensure volume mappings include new output directory

### Files to Reference
- `poc/poc_analysis.py` - Structure template for new script
- `specs/worship-music-transition-system-design.md` - System design reference

---

## Step-by-Step Implementation Plan

### Phase 1: Dependency Setup

#### Step 1.1: Update pyproject.toml
**File**: `pyproject.toml`

Add the following dependencies:
```toml
[tool.poetry.dependencies]
# Existing dependencies remain...

# All-In-One and PyTorch dependencies
torch = "^2.1.0"
torchaudio = "^2.1.0"
allin1 = {git = "https://github.com/mir-aidj/all-in-one.git"}
# Note: NATTEN auto-installs on macOS, manual install needed for Linux/Windows
```

**Rationale**:
- PyTorch 2.1+ is stable and well-supported
- allin1 must be installed from GitHub (not on PyPI yet)
- NATTEN is a dependency of allin1 (auto-installed on macOS)

#### Step 1.2: Update Dockerfile
**File**: `Dockerfile`

Add PyTorch installation before pip install:
```dockerfile
# Add before poetry install step
RUN pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# For NATTEN (Linux/AMD64)
RUN pip install natten -f https://shi-labs.com/natten/wheels/
```

**Rationale**: Use CPU-only PyTorch to reduce image size since we don't need GPU for POC analysis

---

### Phase 2: Create New Analysis Script

#### Step 2.1: Script Structure
**File**: `poc/poc_analysis_allinone.py`

Overall structure (mirrors original):
```python
#!/usr/bin/env python3
"""
POC Analysis using All-In-One: Worship Music Transition System

Version: 0.2.0-poc-allinone
Date: 2024-12-31
Goal: Validate all-in-one deep learning approach vs librosa baseline

Key Differences from Original:
- Uses all-in-one for beat/downbeat/tempo detection
- Uses ML-predicted segment labels (intro/verse/chorus/bridge/outro)
- Extracts and stores 24-dim embeddings per stem
- Outputs to poc_output_allinone/
"""

import allin1
import torch
# ... other imports similar to original

# Configuration
AUDIO_DIR = Path("poc_audio")
OUTPUT_DIR = Path("poc_output_allinone")  # DIFFERENT OUTPUT DIR
```

#### Step 2.2: Core Analysis Function
**Function**: `analyze_song_allinone(filepath)`

Key components:

**2.2.1: All-In-One Analysis**
```python
def analyze_song_allinone(filepath):
    """
    Run all-in-one analysis on a single song.

    Returns dictionary with:
    - Basic metadata (filename, duration)
    - All-in-one results (BPM, beats, downbeats, segments with ML labels)
    - Embeddings (24-dim per stem: bass, drums, other, vocals)
    - Key detection (still use librosa chroma - all-in-one doesn't provide key)
    - Energy metrics (RMS, loudness - computed from audio)
    """

    print(f"\n{'='*70}")
    print(f"Analyzing with All-In-One: {filepath.name}")
    print(f"{'='*70}")

    # Run all-in-one analysis
    result = allin1.analyze(
        str(filepath),
        out_dir=None,  # Don't save intermediate files
        visualize=False,  # We'll create our own visualizations
        include_embeddings=True,  # Extract embeddings for future use
        sonify=False
    )

    # Extract results
    bpm = result.bpm
    beats = result.beats  # List of beat times in seconds
    downbeats = result.downbeats  # List of downbeat times
    segments = result.segments  # List of segment objects with start, end, label
    embeddings = result.embeddings  # Shape: (4, timesteps, 24)

    print(f"✓ All-In-One Analysis Complete")
    print(f"  BPM: {bpm}")
    print(f"  Beats: {len(beats)} detected")
    print(f"  Downbeats: {len(downbeats)} detected")
    print(f"  Segments: {len(segments)} detected")
    print(f"  Embeddings shape: {embeddings.shape}")
```

**2.2.2: Key Detection (Still Use Librosa)**
```python
    # All-in-one doesn't provide key detection, use librosa
    y, sr = librosa.load(filepath, sr=22050, mono=True)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    # ... same key detection logic as original script
```

**2.2.3: Energy Analysis (Compute from Audio)**
```python
    # Compute energy metrics from raw audio
    rms = librosa.feature.rms(y=y)[0]
    rms_db = librosa.amplitude_to_db(rms, ref=np.max)
    # ... same as original
```

**2.2.4: Structure Results**
```python
    # Process segments from all-in-one
    sections = []
    for seg in segments:
        sections.append({
            'label': seg.label,  # ML-predicted label (intro/verse/chorus/bridge/outro)
            'start': seg.start,
            'end': seg.end,
            'duration': seg.end - seg.start
        })

    print(f"✓ Structure: {len(sections)} sections with ML labels")
    for sec in sections:
        print(f"  {sec['start']:.1f}s - {sec['end']:.1f}s: {sec['label']} ({sec['duration']:.1f}s)")
```

**2.2.5: Return Results**
```python
    return {
        # Metadata
        'filename': filepath.name,
        'filepath': str(filepath),
        'duration': librosa.get_duration(y=y, sr=sr),

        # Rhythm (from all-in-one)
        'tempo': float(bpm),
        'tempo_source': 'allinone',
        'num_beats': len(beats),
        'beats': beats[:100],  # First 100 beats
        'num_downbeats': len(downbeats),
        'downbeats': downbeats[:50],  # First 50 downbeats

        # Harmony (from librosa - all-in-one doesn't provide)
        'key': key,
        'mode': mode,
        'key_confidence': float(confidence),
        'full_key': f"{key} {mode}",
        'key_source': 'librosa',

        # Energy
        'loudness_db': loudness_mean,
        'loudness_std': loudness_std,
        'spectral_centroid': centroid_mean,

        # Structure (from all-in-one with ML labels)
        'num_sections': len(sections),
        'sections': sections,
        'section_label_source': 'allinone_ml',

        # Embeddings (NEW - for future compatibility matching)
        'embeddings_shape': list(embeddings.shape),
        'embeddings_mean': float(np.mean(embeddings)),
        'embeddings_std': float(np.std(embeddings)),
        # Store full embeddings as numpy array for future use
        '_embeddings': embeddings,  # Shape: (4 stems, timesteps, 24 dims)

        # Raw data for visualization
        '_y': y,
        '_sr': sr,
        '_chroma': chroma,
        '_rms': rms,
        '_beats': beats,
        '_downbeats': downbeats
    }
```

#### Step 2.3: Compatibility Calculation
**Function**: `calculate_compatibility(song_a, song_b)`

Keep the same logic as original script - compatibility scores don't change based on analysis method.

#### Step 2.4: Crossfade Function
**Function**: `create_simple_crossfade(song_a_path, song_b_path, crossfade_duration=8.0)`

Keep identical to original - crossfade algorithm is independent of analysis method.

#### Step 2.5: Main Function
**Function**: `main()`

Mirror the original's structure:
1. Setup and file discovery (output to `poc_output_allinone/`)
2. Analyze all songs using `analyze_song_allinone()`
3. Create summary CSV and JSON
4. Generate visualizations (similar panels but highlight all-in-one features)
5. Compatibility analysis (same logic)
6. Create transition prototype
7. Summary report

**Key differences in visualizations**:
- Panel 1: Show both beats (red) and downbeats (blue) from all-in-one
- Panel 3: Color-code sections with ML-predicted labels

---

### Phase 3: Comparison Utility

#### Step 3.1: Create Comparison Script
**File**: `poc/compare_results.py`

```python
#!/usr/bin/env python3
"""
Compare results between librosa and all-in-one analysis approaches.

Usage:
    python poc/compare_results.py
"""

import json
import pandas as pd
from pathlib import Path
import matplotlib.pyplot as plt

def load_results(method='librosa'):
    """Load analysis results from JSON file."""
    if method == 'librosa':
        path = Path('poc_output/poc_full_results.json')
    else:
        path = Path('poc_output_allinone/poc_full_results.json')

    with open(path) as f:
        return json.load(f)

def compare_tempo():
    """Compare tempo detection accuracy."""
    librosa_results = load_results('librosa')
    allinone_results = load_results('allinone')

    comparison = []
    for lr, ar in zip(librosa_results, allinone_results):
        comparison.append({
            'filename': lr['filename'],
            'librosa_tempo': lr['tempo'],
            'allinone_tempo': ar['tempo'],
            'diff_bpm': abs(lr['tempo'] - ar['tempo']),
            'diff_pct': abs(lr['tempo'] - ar['tempo']) / lr['tempo'] * 100
        })

    df = pd.DataFrame(comparison)
    print("\nTempo Comparison:")
    print(df.to_string(index=False))

    return df

def compare_structure():
    """Compare structure segmentation."""
    librosa_results = load_results('librosa')
    allinone_results = load_results('allinone')

    comparison = []
    for lr, ar in zip(librosa_results, allinone_results):
        comparison.append({
            'filename': lr['filename'],
            'librosa_sections': lr['num_sections'],
            'allinone_sections': ar['num_sections'],
            'librosa_labels': ', '.join([s['label'] for s in lr['sections']]),
            'allinone_labels': ', '.join([s['label'] for s in ar['sections']])
        })

    df = pd.DataFrame(comparison)
    print("\nStructure Comparison:")
    print(df.to_string(index=False))

    return df

def compare_beat_alignment():
    """Compare beat detection alignment."""
    # Implementation to compare beat timing differences
    pass

def main():
    print("="*70)
    print("COMPARISON: Librosa vs All-In-One")
    print("="*70)

    tempo_df = compare_tempo()
    structure_df = compare_structure()

    # Save comparison results
    output_dir = Path('poc_output_comparison')
    output_dir.mkdir(exist_ok=True)

    tempo_df.to_csv(output_dir / 'tempo_comparison.csv', index=False)
    structure_df.to_csv(output_dir / 'structure_comparison.csv', index=False)

    print(f"\n✓ Comparison results saved to: {output_dir}")

if __name__ == '__main__':
    main()
```

---

### Phase 4: Testing and Validation

#### Step 4.1: Test Installation
```bash
# Install dependencies
poetry install

# Or for Docker
docker-compose build
```

#### Step 4.2: Run Both Scripts
```bash
# Run original librosa-based analysis
python poc/poc_analysis.py

# Run new all-in-one analysis
python poc/poc_analysis_allinone.py

# Compare results
python poc/compare_results.py
```

#### Step 4.3: Validation Checklist
1. **Dependency Installation**: Verify PyTorch, NATTEN, and allin1 install correctly
2. **Script Execution**: Both scripts run without errors
3. **Output Files**: Both output directories contain expected files
4. **Tempo Accuracy**: Compare tempo differences between methods
5. **Beat Alignment**: Visual comparison of beat detection
6. **Segment Labels**: Compare ML labels vs heuristic labels
7. **Embeddings**: Verify embeddings are extracted and stored

---

## Output Structure Comparison

### Librosa Output (poc_output/)
```
poc_output/
├── poc_summary.csv
├── poc_full_results.json
├── poc_analysis_visualizations.png
├── poc_compatibility_scores.csv
├── poc_compatibility_heatmap.png
├── transition_<song_a>_to_<song_b>.flac
└── transition_waveform.png
```

### All-In-One Output (poc_output_allinone/)
```
poc_output_allinone/
├── poc_summary.csv                      # Same structure, different values
├── poc_full_results.json                # Includes embeddings data
├── poc_analysis_visualizations.png      # Shows beats + downbeats
├── poc_compatibility_scores.csv         # Same compatibility logic
├── poc_compatibility_heatmap.png
├── transition_<song_a>_to_<song_b>.flac
└── transition_waveform.png
```

### Comparison Output (poc_output_comparison/)
```
poc_output_comparison/
├── tempo_comparison.csv                 # Side-by-side tempo differences
├── structure_comparison.csv             # Section count and label differences
└── beat_alignment_comparison.png        # Visual beat alignment comparison
```

---

## Key Design Decisions

### 1. Separate Scripts vs. Unified Script
**Decision**: Create separate scripts
**Rationale**:
- Simpler to understand and maintain
- Easier to run independently
- Cleaner comparison workflow
- No conditional logic needed

### 2. Key Detection Method
**Decision**: Continue using librosa for key detection in all-in-one script
**Rationale**:
- All-in-one doesn't provide key/harmony detection
- Need consistent key detection for compatibility scoring
- Can revisit in future with dedicated key detection library

### 3. Embeddings Storage
**Decision**: Store embeddings in JSON metadata + optionally as numpy files
**Rationale**:
- Embeddings (4×timesteps×24) can be large
- Store summary stats in JSON
- Store full arrays as numpy files for future use
- Can be used for advanced similarity matching later

### 4. Visualization Differences
**Decision**: Highlight unique features of each method
**Rationale**:
- Librosa: Show beat tracking
- All-in-one: Show beats AND downbeats
- All-in-one: Display ML-predicted labels vs heuristic labels
- Makes comparison visually clear

---

## Success Criteria

1. **Installation Success**: All dependencies install without errors
2. **Script Execution**: Both scripts run to completion on 3 POC songs
3. **Output Generation**: All expected output files created
4. **Tempo Validation**: All-in-one tempo within ±10 BPM of librosa (or manual count)
5. **Beat Detection**: Visual alignment shows reasonable beat detection
6. **Segment Labels**: ML labels are semantically reasonable (intro/verse/chorus/outro)
7. **Embeddings**: Embeddings extracted with expected shape (4, timesteps, 24)
8. **Comparison**: Comparison script successfully analyzes differences

---

## Future Enhancements

### Short-term
1. **Embedding-based Similarity**: Use embeddings for enhanced compatibility scoring beyond tempo/key
2. **Manual Validation UI**: Simple tool to validate segment labels against ground truth
3. **Batch Comparison**: Automated accuracy metrics across full library

### Long-term
1. **Hybrid Approach**: Combine best features of both methods
2. **Custom Training**: Fine-tune all-in-one on Chinese worship music
3. **Real-time Processing**: Optimize for live performance use cases

---

## Timeline Estimate

- **Phase 1** (Dependency Setup): 30 minutes
- **Phase 2** (Script Creation): 2-3 hours
- **Phase 3** (Comparison Utility): 1 hour
- **Phase 4** (Testing): 1 hour

**Total**: 4-6 hours for complete implementation and validation
