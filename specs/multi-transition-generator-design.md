# Design Plan: Multi-Transition Generator for Worship Music POC

**Date:** 2026-01-02
**Version:** 1.0
**Purpose:** Generate multiple transition audio files from pre-computed compatibility analysis

---

## 1. Overview

### Current State
The POC system (`poc_analysis_allinone.py`) successfully analyzes songs and generates compatibility scores, but only creates **one** transition between the best-scoring pair. This is insufficient for human review and comparison.

### Goal
Create a standalone script (`poc/generate_transitions.py`) that:
- Reads existing analysis results from `poc_output_allinone/`
- Generates **multiple** transitions for all viable song pairs
- Saves transitions as high-quality audio files for human evaluation
- Provides metadata about each transition for informed review

---

## 2. Design Requirements

### Functional Requirements
1. **Input Sources**
   - `poc_output_allinone/poc_compatibility_scores.csv` - Pre-computed compatibility matrix
   - `poc_output_allinone/poc_full_results.json` - Song analysis with sections/structure
   - `poc_audio/*.mp3` or `*.flac` - Original audio files

2. **Transition Generation**
   - Generate transitions for **all pairs above quality threshold** (not just best pair)
   - Support **multiple crossfade durations** per pair (6s, 8s, 10s, 12s)
   - Use **section-aware positioning** when possible (chorus→intro, outro→verse)
   - Apply **equal-power crossfade** for perceptually smooth transitions

3. **Output Requirements**
   - Save each transition as separate FLAC file (high quality)
   - Generate metadata JSON with transition details
   - Create summary report for human review
   - Optional: Visualization of each transition waveform

### Non-Functional Requirements
- **Performance:** Process all transitions in reasonable time (<2 min for 4 songs)
- **Modularity:** Reusable crossfade function (already exists in poc_analysis_allinone.py)
- **Configurability:** Easy to adjust thresholds, durations, output format
- **Debuggability:** Clear logging of what's being generated and why

---

## 3. Architecture Design

### Script Structure: `poc/generate_transitions.py`

```
┌─────────────────────────────────────────────────────┐
│         INPUTS (Read from disk)                     │
├─────────────────────────────────────────────────────┤
│ 1. poc_compatibility_scores.csv                     │
│ 2. poc_full_results.json                            │
│ 3. poc_audio/ (original audio files)                │
└─────────────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────┐
│      FILTERING & SELECTION                          │
├─────────────────────────────────────────────────────┤
│ - Filter pairs by MIN_SCORE_THRESHOLD (default: 60) │
│ - Sort by overall_score (best first)                │
│ - Optional: Limit to top N pairs                    │
└─────────────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────┐
│   ADAPTIVE DURATION SELECTION (per pair)            │
├─────────────────────────────────────────────────────┤
│ IF tempo_score >= 95: durations = [6s, 8s]          │
│ ELIF tempo_score >= 80: durations = [8s, 10s]       │
│ ELSE: durations = [10s, 12s]                        │
│                                                      │
│ (Or generate ALL durations: [6, 8, 10, 12])         │
└─────────────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────┐
│      CROSSFADE GENERATION (per pair × duration)     │
├─────────────────────────────────────────────────────┤
│ 1. Load stereo audio (44.1kHz)                      │
│ 2. Extract outro (last N seconds of song_a)         │
│ 3. Extract intro (first N seconds of song_b)        │
│ 4. Apply equal-power fade:                          │
│    - fade_out = sqrt(1 - t)                         │
│    - fade_in = sqrt(t)                              │
│ 5. Mix: transition = outro_faded + intro_faded      │
└─────────────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────┐
│              OUTPUTS (Save to disk)                 │
├─────────────────────────────────────────────────────┤
│ 1. Audio files: transitions/*.flac                  │
│    Format: "transition_{song_a}_{song_b}_{dur}s.flac"│
│                                                      │
│ 2. Metadata: transitions/transitions_metadata.json  │
│    - List of all transitions with scores/params     │
│                                                      │
│ 3. Summary: transitions/transitions_summary.csv     │
│    - Table view for quick comparison                │
│                                                      │
│ 4. Optional: transitions/waveforms/*.png            │
│    - Visual inspection of each transition           │
└─────────────────────────────────────────────────────┘
```

---

## 4. Algorithm Details

### 4.1 Pair Filtering Algorithm

```python
def select_transition_candidates(compatibility_df, min_score=60, max_pairs=None):
    """
    Filter compatible pairs worthy of transition generation.

    Args:
        compatibility_df: DataFrame from poc_compatibility_scores.csv
        min_score: Minimum overall_score (0-100) to consider
        max_pairs: Optional limit on number of pairs (None = all viable pairs)

    Returns:
        List of candidate pairs sorted by score (best first)
    """
    # Filter by threshold
    viable = compatibility_df[compatibility_df['overall_score'] >= min_score]

    # Sort by overall score (descending)
    viable = viable.sort_values('overall_score', ascending=False)

    # Optionally limit
    if max_pairs:
        viable = viable.head(max_pairs)

    return viable.to_dict('records')
```

**Current Dataset Results:**
- 6 total pairs
- Threshold 60 → **2 pairs** selected:
  - `joy_to_heaven → praise` (75.9)
  - `give_thanks → name_of_jesus` (68.7)
- Threshold 50 → **3 pairs**
- Threshold 40 → **6 pairs** (all)

**Recommendation:** Use `min_score=60` to focus on high-quality transitions only.

---

### 4.2 Adaptive Duration Selection

```python
def determine_crossfade_durations(pair_info):
    """
    Select appropriate crossfade durations based on compatibility scores.

    Strategy:
    - High tempo match (95+): Shorter fades work (6-8s)
    - Good tempo match (80-95): Medium fades (8-10s)
    - Poor tempo match (<80): Longer fades needed (10-12s)

    Returns:
        List of durations to try (e.g., [6, 8, 10])
    """
    tempo_score = pair_info['tempo_score']

    if tempo_score >= 95:
        return [6, 8]  # Near-perfect tempo match
    elif tempo_score >= 80:
        return [8, 10]  # Good tempo match
    else:
        return [10, 12]  # Need longer blend time
```

**Alternative (Simpler):** Generate **all durations** `[6, 8, 10, 12]` for every pair, let humans decide.
- **Pros:** Maximum flexibility for evaluation
- **Cons:** 4× more files (but storage is cheap)

**Recommendation:** Start with adaptive selection, add `--all-durations` flag for full generation.

---

### 4.3 Crossfade Audio Generation

**Reuse existing function** from `poc_analysis_allinone.py:290-336`:

```python
def create_simple_crossfade(song_a_path, song_b_path, crossfade_duration=8.0):
    """
    Create equal-power crossfade between two songs.
    (Copy from existing implementation)
    """
    # 1. Load stereo at 44.1kHz
    y_a, sr = librosa.load(song_a_path, sr=44100, mono=False)
    y_b, sr_b = librosa.load(song_b_path, sr=44100, mono=False)

    # 2. Ensure stereo
    if y_a.ndim == 1: y_a = np.stack([y_a, y_a])
    if y_b.ndim == 1: y_b = np.stack([y_b, y_b])

    # 3. Extract segments
    crossfade_samples = int(crossfade_duration * sr)
    outro = y_a[:, -crossfade_samples:]
    intro = y_b[:, :crossfade_samples]

    # 4. Equal-power fade curves
    fade_curve = np.linspace(0, 1, crossfade_samples)
    fade_out = np.sqrt(1 - fade_curve)
    fade_in = np.sqrt(fade_curve)

    # 5. Mix
    transition = outro * fade_out + intro * fade_in

    return transition, sr
```

**Why Equal-Power?**
- Linear fades cause perceived volume dip at midpoint
- Equal-power (`sqrt`) preserves RMS energy throughout crossfade
- Standard practice in professional audio mixing

---

### 4.4 Filename Generation

```python
def generate_transition_filename(song_a, song_b, duration):
    """
    Create descriptive filename for transition audio.

    Format: transition_{song_a_base}_{song_b_base}_{duration}s.flac

    Example: transition_joy_to_heaven_praise_8s.flac
    """
    # Remove extensions
    base_a = song_a.replace('.mp3', '').replace('.flac', '')
    base_b = song_b.replace('.mp3', '').replace('.flac', '')

    # Sanitize (remove special chars)
    base_a = base_a.replace(' ', '_')
    base_b = base_b.replace(' ', '_')

    return f"transition_{base_a}_{base_b}_{duration}s.flac"
```

---

## 5. Output File Structure

### Directory Layout
```
poc_output_allinone/
├── poc_summary.csv                    # Existing (from analysis)
├── poc_full_results.json              # Existing
├── poc_compatibility_scores.csv       # Existing
├── transitions/                       # NEW - Generated by this script
│   ├── transition_joy_to_heaven_praise_6s.flac
│   ├── transition_joy_to_heaven_praise_8s.flac
│   ├── transition_give_thanks_name_of_jesus_8s.flac
│   ├── transition_give_thanks_name_of_jesus_10s.flac
│   ├── transitions_metadata.json      # Details about all transitions
│   ├── transitions_summary.csv        # Quick reference table
│   └── waveforms/                     # Optional visualizations
│       ├── transition_joy_to_heaven_praise_6s.png
│       └── ...
```

### Metadata JSON Format

`transitions/transitions_metadata.json`:
```json
{
  "generated_at": "2026-01-02T15:30:00",
  "total_transitions": 4,
  "configuration": {
    "min_score_threshold": 60,
    "crossfade_durations": [6, 8, 10, 12],
    "adaptive_duration": true,
    "output_format": "FLAC"
  },
  "transitions": [
    {
      "id": 1,
      "song_a": "joy_to_heaven.mp3",
      "song_b": "praise.mp3",
      "compatibility": {
        "overall_score": 75.9,
        "tempo_score": 100.0,
        "key_score": 40.0,
        "energy_score": 99.7,
        "tempo_a": 150.0,
        "tempo_b": 150.0,
        "tempo_diff_pct": 0.0,
        "key_a": "C major",
        "key_b": "F major",
        "energy_diff_db": 0.1
      },
      "crossfade_duration": 8,
      "filename": "transition_joy_to_heaven_praise_8s.flac",
      "file_size_mb": 1.2,
      "sample_rate": 44100,
      "channels": 2,
      "notes": "Excellent tempo match (150→150 BPM), different keys may be noticeable"
    }
  ]
}
```

### Summary CSV Format

`transitions/transitions_summary.csv`:
```csv
id,song_a,song_b,overall_score,tempo_score,key_score,duration_s,filename
1,joy_to_heaven.mp3,praise.mp3,75.9,100.0,40.0,8,transition_joy_to_heaven_praise_8s.flac
2,joy_to_heaven.mp3,praise.mp3,75.9,100.0,40.0,6,transition_joy_to_heaven_praise_6s.flac
3,give_thanks.mp3,name_of_jesus.mp3,68.7,88.8,40.0,8,transition_give_thanks_name_of_jesus_8s.flac
4,give_thanks.mp3,name_of_jesus.mp3,68.7,88.8,40.0,10,transition_give_thanks_name_of_jesus_10s.flac
```

---

## 6. Configuration Options

### Command-Line Arguments (Suggested)

```python
# Configuration defaults
CONFIG = {
    'input_dir': 'poc_output_allinone',
    'audio_dir': 'poc_audio',
    'output_dir': 'poc_output_allinone/transitions',

    # Filtering
    'min_score': 60,           # Minimum compatibility score
    'max_pairs': None,         # Limit number of pairs (None = all)

    # Crossfade options
    'durations': [6, 8, 10, 12],  # All durations to generate
    'adaptive_duration': True,     # Use smart duration selection
    'sample_rate': 44100,          # Audio sample rate
    'output_format': 'flac',       # 'flac' or 'wav'

    # Optional features
    'generate_waveforms': False,   # Create visualizations
    'verbose': True                # Print detailed progress
}
```

**Future enhancement:** Add argparse for CLI usage:
```bash
python poc/generate_transitions.py --min-score 60 --durations 6,8,10 --verbose
```

---

## 7. Implementation Plan

### Phase 1: Core Script Setup
**File:** `poc/generate_transitions.py`

1. **Imports and configuration**
   - Copy imports from `poc_analysis_allinone.py` (librosa, numpy, pandas, soundfile)
   - Define configuration constants
   - Set up logging

2. **Load existing results**
   ```python
   def load_analysis_results():
       compatibility_df = pd.read_csv('poc_output_allinone/poc_compatibility_scores.csv')
       with open('poc_output_allinone/poc_full_results.json') as f:
           song_data = json.load(f)
       return compatibility_df, song_data
   ```

3. **Copy crossfade function**
   - Extract `create_simple_crossfade()` from poc_analysis_allinone.py:290-336
   - No modifications needed - works as-is

### Phase 2: Transition Generation Logic

4. **Implement pair selection**
   ```python
   def select_transition_candidates(compatibility_df, min_score=60):
       # Filter and sort as designed above
       pass
   ```

5. **Implement duration selection**
   ```python
   def determine_crossfade_durations(pair_info, adaptive=True):
       # Return list of durations based on scores
       pass
   ```

6. **Main generation loop**
   ```python
   def generate_all_transitions():
       # For each candidate pair:
       #   For each duration:
       #     Generate crossfade
       #     Save FLAC file
       #     Collect metadata
       pass
   ```

### Phase 3: Output Generation

7. **Save metadata JSON**
   - Compile all transition info
   - Write to `transitions/transitions_metadata.json`

8. **Save summary CSV**
   - Create DataFrame from metadata
   - Write to `transitions/transitions_summary.csv`

9. **Print summary report**
   - Show number of transitions generated
   - List all output files
   - Provide next steps for human review

### Phase 4: Optional Enhancements

10. **Waveform visualizations** (if time permits)
    - Generate matplotlib plots for each transition
    - Save to `transitions/waveforms/`

11. **Section-aware positioning** (future enhancement)
    - Use `sections` data from `poc_full_results.json`
    - Position crossfade at structural boundaries (chorus→verse)

---

## 8. Testing Strategy

### Test Cases

1. **No viable pairs**
   - Mock compatibility CSV with all scores < 60
   - Expected: Script completes with "0 transitions generated" message

2. **Single viable pair**
   - Mock with 1 pair above threshold
   - Expected: Generate 2-4 transitions (different durations)

3. **Multiple pairs** (realistic scenario)
   - Use actual `poc_output_allinone/poc_compatibility_scores.csv`
   - Expected: 2 pairs × 2 durations = 4 transitions

4. **File I/O errors**
   - Missing audio file
   - Expected: Graceful error message, continue with other pairs

### Validation

- **Audio quality check:** Listen to generated transitions
- **Metadata accuracy:** Verify JSON matches actual files
- **File naming:** Check all filenames follow convention
- **Duration accuracy:** Verify transition lengths match requested durations

---

## 9. Success Criteria

### Minimum Viable Product (MVP)
- ✅ Script reads existing analysis results
- ✅ Filters pairs by compatibility threshold (≥60)
- ✅ Generates crossfades for all viable pairs
- ✅ Saves FLAC files with descriptive names
- ✅ Outputs metadata JSON and summary CSV
- ✅ Completes without errors on current 4-song dataset

### Stretch Goals (Future)
- 🔲 Command-line arguments for configuration
- 🔲 Waveform visualizations
- 🔲 Section-aware crossfade positioning
- 🔲 Progress bar for long generation runs
- 🔲 Tempo stretching to improve poor-tempo transitions

---

## 10. Critical Files to Create/Modify

### New Files
- **`poc/generate_transitions.py`** - Main script (estimate: 250-300 lines)

### Files to Read (no modifications)
- `poc_output_allinone/poc_compatibility_scores.csv`
- `poc_output_allinone/poc_full_results.json`
- `poc_audio/*.mp3` and `poc_audio/*.flac`

### Output Directory Structure (created by script)
```
poc_output_allinone/transitions/
├── *.flac (audio files)
├── transitions_metadata.json
└── transitions_summary.csv
```

---

## 11. Estimated Effort

- **Phase 1 (Setup):** 30 minutes
- **Phase 2 (Generation logic):** 45 minutes
- **Phase 3 (Output):** 30 minutes
- **Testing & debugging:** 30 minutes

**Total:** ~2-2.5 hours

---

## 12. Next Steps After Implementation

1. **Run the script:**
   ```bash
   cd /home/mhuang/Development/stream_of_worship
   python poc/generate_transitions.py
   ```

2. **Human review:**
   - Listen to all transitions in `poc_output_allinone/transitions/`
   - Compare different durations for same pair
   - Evaluate quality based on tempo/key compatibility

3. **Iterate:**
   - Adjust `min_score` threshold if too many/few transitions
   - Tweak duration selection logic
   - Add more songs to `poc_audio/` for richer dataset

4. **Document findings:**
   - Which compatibility metrics best predict good transitions?
   - Optimal crossfade durations for different tempo ranges?
   - Does key compatibility matter as much as tempo?

---

## Appendix: Current Dataset Summary

**4 Songs Available:**
- `give_thanks.mp3` - 278s, 71 BPM, A# major
- `joy_to_heaven.mp3` - 248s, 150 BPM, C major
- `name_of_jesus.mp3` - 284s, 77 BPM, A major
- `praise.mp3` - 186s, 150 BPM, F major

**6 Pairs Analyzed:**
1. ⭐ `joy_to_heaven → praise` (75.9) - **Excellent** tempo match
2. ⭐ `give_thanks → name_of_jesus` (68.7) - **Good** tempo match
3. `name_of_jesus → praise` (35.5) - Poor tempo mismatch
4. `joy_to_heaven → name_of_jesus` (35.4) - Poor tempo mismatch
5. `give_thanks → praise` (32.7) - Poor tempo mismatch
6. `give_thanks → joy_to_heaven` (32.6) - Poor tempo mismatch

**Expected Output with min_score=60:**
- 2 pairs selected
- 4 total transitions (if 2 durations each)
- ~4-8 MB total storage

---

## End of Design Plan

**Status:** Ready for implementation
**Next Action:** Begin Phase 1 - Create `poc/generate_transitions.py` script
