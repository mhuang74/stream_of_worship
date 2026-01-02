# POC Analysis Script

Standalone Python script for analyzing worship music transitions.

## Quick Start

### Running from Inside Docker Container
```bash
cd /workspace
python poc/poc_analysis.py
```

### Running from Host Machine
```bash
# Option 1: Execute in running container
docker-compose exec librosa python poc/poc_analysis.py

# Option 2: Run one-off command
docker-compose run --rm librosa python poc/poc_analysis.py
```

## Prerequisites

- Audio files in `poc_audio/` directory (MP3 or FLAC format)
- Minimum 3 songs recommended for meaningful analysis

## What It Does

The script performs a complete 7-stage POC analysis workflow:

1. **Setup**: Discovers audio files, creates output directory
2. **Feature Extraction**: Analyzes each song for tempo, key, energy, and structure
3. **Summary**: Generates analysis summary tables and JSON export
4. **Visualizations**: Creates waveform, chromagram, and energy profile charts
5. **Compatibility**: Calculates pairwise song compatibility scores
6. **Transitions**: Generates sample crossfade between best compatible pair
7. **Report**: Prints validation checklist and next steps

## Outputs

All outputs are saved to `poc_output/`:

- `poc_summary.csv` - Summary table of all analyzed songs
- `poc_full_results.json` - Complete analysis results (JSON)
- `poc_analysis_visualizations.png` - Multi-panel visualizations for each song
- `poc_compatibility_scores.csv` - Pairwise compatibility matrix
- `poc_compatibility_heatmap.png` - Visual compatibility heatmap
- `transition_<song_a>_to_<song_b>.flac` - Sample transition audio
- `transition_waveform.png` - Transition waveform visualization

## Notes

- Execution time: ~30-60 seconds per song
- The original Jupyter notebook (`notebooks/01_POC_Analysis.ipynb`) is kept for interactive exploration
- For debugging specific features, see `reproduce_error.py`
