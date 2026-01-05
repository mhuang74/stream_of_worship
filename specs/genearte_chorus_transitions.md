# Design Specification: Section-Level Compatibility Analysis for Worship Music Transitions

**Version:** 1.0
**Date:** 2026-01-03
**Author:** Claude Code
**Status:** Draft for Review

---

## 1. Overview

### 1.1 Purpose
Extend the existing song-level compatibility analysis to support **section-level** (chorus-to-chorus) compatibility scoring and transition generation. This enables more precise, musically intelligent transitions by matching similar structural sections between songs.

### 1.2 Scope
- **Phase 1 (This Spec):** First chorus to first chorus transitions with "best chorus" selection
- **Future Phases:** Multi-chorus support, verse/bridge transitions, real-time section detection

### 1.3 Key Requirements (from User)
1. **Compatibility Scoring:** Hybrid approach using both embeddings and traditional metrics (tempo/key/energy)
2. **Section Selection:** Best chorus per song (highest energy); fallback to verse if no chorus detected
3. **Output Formats:**
   - CSV file with section pair scores
   - Heatmap visualization of section compatibility matrix
   - Audio files of chorus-to-chorus transitions

---

## 2. Technical Architecture

### 2.1 Data Flow

```
Audio Files (poc_audio/)
    ↓
[analyze_song_allinone] (existing)
    ↓
Song Analysis Results with Sections
    ↓
[NEW: extract_section_features]
    ↓
Section-Level Feature Database
    ↓
[NEW: select_best_chorus]
    ↓
Best Chorus per Song
    ↓
[NEW: calculate_section_compatibility]
    ↓
Section Compatibility Matrix (CSV + JSON)
    ↓
[NEW: generate_section_transitions]
    ↓
Chorus Transition Audio Files + Metadata
```

### 2.2 New Components

#### 2.2.1 Section Feature Extractor
**Module:** `poc/analyze_sections.py` (new file)
**Function:** `extract_section_features(song_result, section_idx, audio_path)`

**Purpose:** Extract all necessary features for a single section (chorus/verse/bridge)

**Inputs:**
- `song_result`: Full analysis result from `analyze_song_allinone()`
- `section_idx`: Index of section in `song_result['sections']`
- `audio_path`: Path to original audio file

**Outputs:** Dictionary with section features
```python
{
    'song_filename': str,
    'section_index': int,
    'label': str,  # "chorus", "verse", etc.
    'start': float,  # seconds
    'end': float,    # seconds
    'duration': float,  # seconds

    # Traditional Metrics
    'tempo': float,  # BPM estimated from beat density
    'key': str,      # e.g., "C major"
    'mode': str,     # "major" or "minor"
    'loudness_db': float,  # Mean RMS loudness
    'loudness_std': float,  # Loudness variation
    'spectral_centroid': float,  # Brightness
    'energy_score': float,  # Normalized 0-100 for chorus selection

    # Embedding Features (NEW)
    'embeddings_shape': tuple,  # (4, section_timesteps, 24)
    'embeddings_mean': np.ndarray,  # (4, 24) - mean across timesteps per stem
    'embeddings_std': np.ndarray,   # (4, 24) - std across timesteps

    # Raw Data (not serialized to CSV)
    '_section_audio': np.ndarray,  # Stereo audio (2, samples)
    '_embeddings': np.ndarray,     # (4, timesteps, 24)
}
```

**Implementation Details:**

1. **Audio Extraction:**
   ```python
   y, sr = librosa.load(audio_path, sr=44100, mono=False)
   start_samples = int(section['start'] * sr)
   end_samples = int(section['end'] * sr)
   section_audio = y[:, start_samples:end_samples]
   ```

2. **Tempo Estimation (Beat Density):**
   ```python
   # Count beats within section timespan
   beats_in_section = [b for b in song_result['_beats']
                       if section['start'] <= b <= section['end']]
   beat_density = len(beats_in_section) / section['duration']
   section_tempo = beat_density * 60  # Convert to BPM
   ```

3. **Key Detection (Chroma-based):**
   ```python
   # Extract section audio as mono
   section_mono = librosa.to_mono(section_audio)
   chroma = librosa.feature.chroma_cqt(y=section_mono, sr=sr)
   # Apply Krumhansl-Schmuckler (same as song-level, lines 359-383)
   ```

4. **Energy Metrics:**
   ```python
   rms = librosa.feature.rms(y=section_mono)
   loudness_db = librosa.amplitude_to_db(rms, ref=np.max).mean()
   centroid = librosa.feature.spectral_centroid(y=section_mono, sr=sr).mean()

   # Energy score for chorus selection (0-100)
   energy_score = normalize_to_100(loudness_db, loudness_std, centroid)
   ```

5. **Embeddings Extraction (NEW - requires metadata addition):**
   ```python
   # PREREQUISITE: Add to analyze_song_allinone() return dict:
   # 'embeddings_hop_length': 512
   # 'embeddings_sr': 22050

   hop_length = song_result.get('embeddings_hop_length', 512)
   embed_sr = song_result.get('embeddings_sr', 22050)

   timestep_start = int(section['start'] * embed_sr / hop_length)
   timestep_end = int(section['end'] * embed_sr / hop_length)

   section_embeddings = song_result['_embeddings'][:, timestep_start:timestep_end, :]
   # Shape: (4 stems, section_timesteps, 24 dims)

   # Compute mean/std across time for each stem
   embeddings_mean = section_embeddings.mean(axis=1)  # (4, 24)
   embeddings_std = section_embeddings.std(axis=1)    # (4, 24)
   ```

---

#### 2.2.2 Best Chorus Selector
**Module:** `poc/analyze_sections.py`
**Function:** `select_best_chorus(song_result, audio_path, fallback_to_verse=True)`

**Purpose:** Identify the "best" chorus in a song using energy-based heuristics

**Algorithm:**
```
1. Filter sections where label == "chorus"
2. If no choruses found and fallback_to_verse == True:
   - Filter sections where label == "verse"
   - If still none, return None (skip song)
3. For each candidate section:
   - Extract features using extract_section_features()
   - Compute energy_score (weighted: 70% loudness, 20% brightness, 10% duration)
4. Return section with highest energy_score
```

**Energy Scoring Formula:**
```python
def compute_energy_score(loudness_db, spectral_centroid, duration):
    """
    Compute 0-100 energy score for chorus selection.

    - Louder is better (but normalize to avoid clipping bias)
    - Brighter is better (higher spectral centroid)
    - Typical chorus duration is better (20-60s range)
    """
    # Normalize loudness (-60 to 0 dB → 0 to 100)
    loudness_norm = (loudness_db + 60) / 60 * 100
    loudness_norm = np.clip(loudness_norm, 0, 100)

    # Normalize brightness (1000-5000 Hz → 0 to 100)
    brightness_norm = (spectral_centroid - 1000) / 4000 * 100
    brightness_norm = np.clip(brightness_norm, 0, 100)

    # Duration penalty (penalize very short <10s or very long >90s sections)
    if 20 <= duration <= 60:
        duration_score = 100
    elif duration < 20:
        duration_score = duration / 20 * 100
    else:  # duration > 60
        duration_score = max(50, 100 - (duration - 60) * 2)

    # Weighted average
    energy_score = (loudness_norm * 0.70 +
                    brightness_norm * 0.20 +
                    duration_score * 0.10)

    return energy_score
```

**Outputs:** Dictionary from `extract_section_features()` for the best chorus, or None

---

#### 2.2.3 Section Compatibility Calculator
**Module:** `poc/analyze_sections.py`
**Function:** `calculate_section_compatibility(section_a, section_b, weights=None, embedding_stems='all')`

**Purpose:** Compute hybrid compatibility score between two sections using embeddings + traditional metrics with configurable weights

**Parameters:**
- `weights`: Dict with keys `{'tempo': float, 'key': float, 'energy': float, 'embeddings': float}`
  - Default: `{'tempo': 0.25, 'key': 0.25, 'energy': 0.15, 'embeddings': 0.35}`
  - Must sum to 1.0 (validation required)
- `embedding_stems`: Which stems to include in embeddings scoring
  - `'all'`: Average all 4 stems (default)
  - `'vocals'`: Only vocals stem (index 3)
  - `'bass'`: Only bass stem (index 0)
  - `'drums'`: Only drums stem (index 1)
  - `'other'`: Only other stem (index 2)
  - `'bass+drums'`: Average bass and drums
  - `'bass+vocals'`: Average bass and vocals
  - `'drums+vocals'`: Average drums and vocals
  - `'other+vocals'`: Average other and vocals
  - `'bass+drums+vocals'`: Average bass, drums, and vocals

**Scoring Components:**

1. **Tempo Score (default: 25% weight)** - Same as song-level
   ```python
   tempo_diff_pct = abs(section_a['tempo'] - section_b['tempo']) /
                    max(section_a['tempo'], section_b['tempo'])
   # Score: 100 if <5%, scales to 0 at >20%
   ```

2. **Key Score (default: 25% weight)** - Same as song-level
   ```python
   # 100 if exact match, 80 if relative, 70 if compatible, 40 otherwise
   ```

3. **Energy Score (default: 15% weight)** - Loudness similarity
   ```python
   energy_diff = abs(section_a['loudness_db'] - section_b['loudness_db'])
   energy_score = max(0, 100 - energy_diff * 5)
   ```

4. **Embeddings Score (default: 35% weight, configurable)** - NEW
   ```python
   # Parse embedding_stems to get list of stem indices
   STEM_MAP = {'bass': 0, 'drums': 1, 'other': 2, 'vocals': 3}

   if embedding_stems == 'all':
       stem_indices = [0, 1, 2, 3]
   elif embedding_stems in STEM_MAP:
       stem_indices = [STEM_MAP[embedding_stems]]
   else:
       # Parse combinations like 'bass+drums' -> [0, 1]
       stem_names = embedding_stems.split('+')
       stem_indices = [STEM_MAP[name] for name in stem_names]

   # Compute cosine similarity for selected stems only
   stem_similarities = []
   for stem_idx in stem_indices:
       emb_a = section_a['embeddings_mean'][stem_idx]  # (24,)
       emb_b = section_b['embeddings_mean'][stem_idx]  # (24,)

       cosine_sim = np.dot(emb_a, emb_b) / (np.linalg.norm(emb_a) * np.linalg.norm(emb_b))
       # Convert [-1, 1] to [0, 100]
       stem_score = (cosine_sim + 1) / 2 * 100
       stem_similarities.append(stem_score)

   # Average across selected stems
   embeddings_score = np.mean(stem_similarities)
   ```

**Overall Score (with custom weights):**
```python
# Use provided weights or defaults
if weights is None:
    weights = {'tempo': 0.25, 'key': 0.25, 'energy': 0.15, 'embeddings': 0.35}

# Validate weights sum to 1.0
assert abs(sum(weights.values()) - 1.0) < 1e-6, "Weights must sum to 1.0"

overall_score = (tempo_score * weights['tempo'] +
                 key_score * weights['key'] +
                 energy_score * weights['energy'] +
                 embeddings_score * weights['embeddings'])
```

**Special Case - Embeddings Disabled:**
```python
# If embeddings_weight = 0, skip embeddings computation entirely
if weights['embeddings'] == 0:
    embeddings_score = 0  # Don't compute cosine similarity
    # Renormalize other weights to sum to 1.0
    total_weight = weights['tempo'] + weights['key'] + weights['energy']
    overall_score = (tempo_score * weights['tempo'] +
                     key_score * weights['key'] +
                     energy_score * weights['energy']) / total_weight
```

**Outputs:** Dictionary with compatibility data
```python
{
    'song_a': str,
    'song_b': str,
    'section_a_label': str,  # "chorus", "verse", etc.
    'section_b_label': str,
    'section_a_index': int,
    'section_b_index': int,
    'section_a_time': str,  # "45.2s-67.8s"
    'section_b_time': str,

    # Scores
    'overall_score': float,
    'tempo_score': float,
    'key_score': float,
    'energy_score': float,
    'embeddings_score': float,

    # Individual metrics
    'tempo_a': float,
    'tempo_b': float,
    'tempo_diff_pct': float,
    'key_a': str,
    'key_b': str,
    'energy_diff_db': float,

    # Embeddings details
    'embeddings_bass_similarity': float,
    'embeddings_drums_similarity': float,
    'embeddings_other_similarity': float,
    'embeddings_vocals_similarity': float,
}
```

---

#### 2.2.4 Section Transition Generator
**Module:** `poc/generate_section_transitions.py` (new file)
**Function:** `generate_section_transition(song_a_path, song_b_path, section_a, section_b, crossfade_duration=8.0)`

**Purpose:** Create crossfade transition between two specific sections (e.g., chorus A → chorus B)

**Algorithm:**
```
1. Load full stereo audio for both songs
2. Extract section audio segments using start/end times
3. Apply equal-power crossfade (same as song-level)
4. Return transition audio
```

**Implementation:**
```python
def generate_section_transition(song_a_path, song_b_path, section_a, section_b,
                                 crossfade_duration=8.0):
    """
    Create crossfade transition between two song sections.

    Args:
        section_a, section_b: Section feature dicts with 'start', 'end' keys
    """
    # Load audio
    y_a, sr = librosa.load(song_a_path, sr=44100, mono=False)
    y_b, sr_b = librosa.load(song_b_path, sr=44100, mono=False)

    # Ensure stereo
    if y_a.ndim == 1:
        y_a = np.stack([y_a, y_a])
    if y_b.ndim == 1:
        y_b = np.stack([y_b, y_b])

    # Extract sections
    section_a_start = int(section_a['start'] * sr)
    section_a_end = int(section_a['end'] * sr)
    section_b_start = int(section_b['start'] * sr)
    section_b_end = int(section_b['end'] * sr)

    section_a_audio = y_a[:, section_a_start:section_a_end]
    section_b_audio = y_b[:, section_b_start:section_b_end]

    # Determine crossfade region
    crossfade_samples = int(crossfade_duration * sr)

    # Take last N seconds of section A
    outro = section_a_audio[:, -crossfade_samples:]

    # Take first N seconds of section B
    intro = section_b_audio[:, :crossfade_samples]

    # Equal-power crossfade (same as song-level)
    fade_curve = np.linspace(0, 1, crossfade_samples)
    fade_out = np.sqrt(1 - fade_curve)
    fade_in = np.sqrt(fade_curve)

    outro_faded = outro * fade_out
    intro_faded = intro * fade_in

    transition = outro_faded + intro_faded

    return transition, sr
```

**Naming Convention:**
```
transition_section_{song_a_base}_{section_a_label}_{song_b_base}_{section_b_label}_{duration}s.flac

Example: transition_section_joy_chorus_heaven_chorus_8s.flac
```

---

## 3. File Modifications

### 3.1 Modify Existing: `poc/poc_analysis_allinone.py`

**Changes Required:**

**Line ~450 (in `analyze_song_allinone()` return dict):**
Add embeddings metadata for timestep alignment:
```python
# Embeddings (NEW - for future compatibility matching)
'embeddings_shape': list(embeddings.shape),
'embeddings_mean': float(np.mean(embeddings)),
'embeddings_std': float(np.std(embeddings)),
'embeddings_hop_length': 512,  # NEW: Required for section alignment
'embeddings_sr': 22050,         # NEW: Sample rate for embeddings
'_embeddings': embeddings,
```

**Rationale:** These two new fields enable converting section start/end times (in seconds) to embedding timestep indices.

---

### 3.2 New File: `poc/analyze_sections.py`

**Purpose:** All section-level analysis logic (CLI script + library functions)

**Exports:**
- `extract_section_features(song_result, section_idx, audio_path) -> dict`
- `select_best_chorus(song_result, audio_path, fallback_to_verse=True) -> dict | None`
- `calculate_section_compatibility(section_a, section_b, weights=None, embedding_stems=None) -> dict`
- `analyze_all_sections(audio_dir, cache_dir, weights=None, embedding_stems=None) -> tuple[list, pd.DataFrame]`

**CLI Arguments:**
```python
parser = argparse.ArgumentParser(description='Analyze song sections for compatibility')

# Scoring weights
parser.add_argument('--tempo-weight', type=float, default=0.25,
                    help='Weight for tempo score (0.0-1.0, default: 0.25)')
parser.add_argument('--key-weight', type=float, default=0.25,
                    help='Weight for key score (0.0-1.0, default: 0.25)')
parser.add_argument('--energy-weight', type=float, default=0.15,
                    help='Weight for energy score (0.0-1.0, default: 0.15)')
parser.add_argument('--embeddings-weight', type=float, default=0.35,
                    help='Weight for embeddings score (0.0-1.0, default: 0.35). Set to 0 to disable.')

# Embeddings stem selection
parser.add_argument('--embedding-stems', type=str, default='all',
                    choices=['all', 'bass', 'drums', 'other', 'vocals',
                             'bass+drums', 'bass+vocals', 'drums+vocals',
                             'other+vocals', 'bass+drums+vocals'],
                    help='Which stems to use for embeddings scoring (default: all)')

# Other options
parser.add_argument('--audio-dir', type=Path, default=Path('poc_audio'),
                    help='Directory containing audio files')
parser.add_argument('--cache-dir', type=Path, default=Path('poc_output_allinone/cache'),
                    help='Directory for cached analysis results')
parser.add_argument('--output-dir', type=Path, default=Path('poc_output_allinone'),
                    help='Output directory for results')
parser.add_argument('--section-type', type=str, default='chorus',
                    choices=['chorus', 'verse', 'bridge'],
                    help='Section type to analyze (default: chorus)')
parser.add_argument('--fallback-to-verse', action='store_true', default=True,
                    help='Fallback to verse if chorus not found')
parser.add_argument('--verbose', action='store_true', help='Enable verbose output')
```

**Example Usage:**
```bash
# Default weights (25% tempo, 25% key, 15% energy, 35% embeddings)
python poc/analyze_sections.py

# Disable embeddings entirely (traditional metrics only)
python poc/analyze_sections.py --embeddings-weight 0 --tempo-weight 0.4 --key-weight 0.4 --energy-weight 0.2

# Use only vocals embeddings
python poc/analyze_sections.py --embedding-stems vocals

# Use only tempo and embeddings (no key/energy)
python poc/analyze_sections.py --tempo-weight 0.5 --embeddings-weight 0.5 --key-weight 0 --energy-weight 0

# Custom weights favoring key harmony
python poc/analyze_sections.py --tempo-weight 0.2 --key-weight 0.5 --energy-weight 0.1 --embeddings-weight 0.2
```

**Main Function:**
```python
def analyze_all_sections(audio_dir=Path('poc_audio'),
                         cache_dir=Path('poc_output_allinone/cache'),
                         weights=None,
                         embedding_stems='all'):
    """
    Analyze all songs and extract best chorus sections.

    Args:
        weights: Dict with keys 'tempo', 'key', 'energy', 'embeddings' (values 0.0-1.0)
                 Default: {'tempo': 0.25, 'key': 0.25, 'energy': 0.15, 'embeddings': 0.35}
        embedding_stems: Which stems to use for embeddings ('all', 'vocals', 'bass+drums', etc.)

    Returns:
        (section_features_list, compatibility_df)
    """
    # 1. Validate weights sum to 1.0
    # 2. Run analyze_song_allinone() for each song
    # 3. Extract best chorus using select_best_chorus()
    # 4. Compute pairwise compatibility with custom weights
    # 5. Return results
```

---

### 3.3 New File: `poc/generate_section_transitions.py`

**Purpose:** Generate chorus-to-chorus transitions (CLI script)

**Configuration:**
```python
CONFIG = {
    'input_dir': Path('poc_output_allinone'),
    'audio_dir': Path('poc_audio'),
    'output_dir': Path('poc_output_allinone/section_transitions'),

    'min_score': 60,  # Minimum section compatibility score
    'max_pairs': None,

    'durations': [6, 8, 10, 12],
    'adaptive_duration': True,
    'sample_rate': 44100,
    'output_format': 'flac',

    'section_type': 'chorus',  # Future: 'verse', 'bridge'
    'fallback_to_verse': True,

    # Compatibility scoring weights (can be overridden by CLI args)
    'compatibility_weights': {
        'tempo': 0.25,
        'key': 0.25,
        'energy': 0.15,
        'embeddings': 0.35
    },
    'embedding_stems': 'all',  # Which stems to use for embeddings
}
```

**CLI Arguments (same as analyze_sections.py):**
```python
parser.add_argument('--tempo-weight', type=float, default=0.25)
parser.add_argument('--key-weight', type=float, default=0.25)
parser.add_argument('--energy-weight', type=float, default=0.15)
parser.add_argument('--embeddings-weight', type=float, default=0.35)
parser.add_argument('--embedding-stems', type=str, default='all',
                    choices=['all', 'bass', 'drums', 'other', 'vocals', ...])
parser.add_argument('--min-score', type=int, default=60)
# ... other args
```

**Main Flow:**
```python
def main():
    # 1. Load section analysis results (or run if not cached)
    section_features, compatibility_df = load_or_analyze_sections()

    # 2. Filter by score threshold and select pairs
    viable_pairs = select_transition_candidates(compatibility_df,
                                                  min_score=CONFIG['min_score'])

    # 3. Generate transitions for each pair
    for pair in viable_pairs:
        for duration in determine_crossfade_durations(pair):
            transition, sr = generate_section_transition(...)
            save_transition_audio(...)

    # 4. Save metadata and summary
    save_section_metadata(transitions)
    save_section_summary_csv(transitions)
    print_section_summary_report(transitions)
```

---

## 4. Output Specifications

### 4.1 CSV: Section Compatibility Scores
**Filename:** `poc_output_allinone/section_compatibility_scores.csv`

**Columns:**
```
song_a, song_b, section_a_label, section_b_label, section_a_index, section_b_index,
section_a_time, section_b_time,
overall_score, tempo_score, key_score, energy_score, embeddings_score,
tempo_a, tempo_b, tempo_diff_pct,
key_a, key_b,
energy_diff_db,
embeddings_bass_similarity, embeddings_drums_similarity,
embeddings_other_similarity, embeddings_vocals_similarity
```

**Example Row:**
```csv
joy.mp3, heaven.mp3, chorus, chorus, 2, 1,
45.2s-67.8s, 38.5s-62.1s,
87.3, 95.2, 100.0, 82.1, 75.8,
128.5, 130.2, 1.32,
C major, C major,
2.3,
78.2, 82.5, 71.3, 70.9
```

---

### 4.2 Visualization: Section Compatibility Heatmap
**Filename:** `poc_output_allinone/section_compatibility_heatmap.png`

**Format:** Matplotlib heatmap (similar to existing song-level heatmap)

**Axes:**
- X-axis: Song filenames with section labels (e.g., "joy.mp3 [chorus]")
- Y-axis: Same
- Cell values: Overall compatibility score (0-100)
- Colormap: RdYlGn (red=poor, yellow=moderate, green=excellent)

**Annotations:** Display score values in cells (e.g., "87.3")

---

### 4.3 Audio: Section Transition Files
**Directory:** `poc_output_allinone/section_transitions/`

**Filename Format:**
```
transition_section_{song_a_base}_{section_a_label}_{song_b_base}_{section_b_label}_{duration}s.flac
```

**Metadata JSON:** `section_transitions_metadata.json`
```json
{
  "generated_at": "2026-01-03T14:32:15",
  "total_transitions": 12,
  "configuration": {
    "min_score_threshold": 60,
    "crossfade_durations": [6, 8, 10, 12],
    "adaptive_duration": true,
    "section_type": "chorus",
    "fallback_to_verse": true,
    "compatibility_weights": {
      "tempo": 0.25,
      "key": 0.25,
      "energy": 0.15,
      "embeddings": 0.35
    },
    "embedding_stems_used": "all"
  },
  "transitions": [
    {
      "id": 1,
      "song_a": "joy.mp3",
      "song_b": "heaven.mp3",
      "section_a": {
        "label": "chorus",
        "index": 2,
        "start": 45.2,
        "end": 67.8,
        "duration": 22.6
      },
      "section_b": {
        "label": "chorus",
        "index": 1,
        "start": 38.5,
        "end": 62.1,
        "duration": 23.6
      },
      "compatibility": {
        "overall_score": 87.3,
        "tempo_score": 95.2,
        "key_score": 100.0,
        "energy_score": 82.1,
        "embeddings_score": 75.8
      },
      "crossfade_duration": 8,
      "filename": "transition_section_joy_chorus_heaven_chorus_8s.flac",
      "file_size_mb": 1.24,
      "notes": "Excellent tempo match, Same key, High embeddings similarity"
    }
  ]
}
```

**Summary CSV:** `section_transitions_summary.csv`
```csv
id, song_a, song_b, section_a_label, section_b_label, overall_score,
tempo_score, key_score, energy_score, embeddings_score, duration_s,
file_size_mb, filename, notes
```

---

## 5. Edge Cases and Constraints

### 5.1 Missing Chorus Handling
**Issue:** Song has no detected chorus section
**Solution:** Fallback to first verse (configurable via `fallback_to_verse` flag)
**Logging:** Warn user when fallback occurs

### 5.2 Short Sections
**Issue:** Chorus duration < crossfade duration (e.g., 5s chorus with 8s crossfade)
**Solution:**
- Option A: Skip pair with warning
- Option B: Reduce crossfade duration to section duration
- **Recommended:** Option B (adaptive crossfade)

### 5.3 Embeddings Alignment Error
**Issue:** Embedding timestep calculation gives out-of-bounds index
**Solution:** Clamp timestep indices to valid range:
```python
timestep_end = min(timestep_end, embeddings.shape[1])
timestep_start = max(0, timestep_start)
```

### 5.4 Multiple Choruses with Same Energy
**Issue:** Two choruses have identical energy scores
**Solution:** Select earlier chorus (by start time) for determinism

### 5.5 No Valid Section Pairs
**Issue:** All song pairs have compatibility < min_score threshold
**Solution:** Print helpful message suggesting to lower threshold or add more songs

---

## 6. Performance Considerations

### 6.1 Computation Costs

**Per-Song Analysis (First Run):**
- Song-level analysis (allin1): ~30-60s per 4min song
- Section feature extraction: ~2-5s per section (depends on duration)
- **Total:** ~40-80s per song (with 3-5 sections)

**Per-Song Analysis (Cached):**
- Load from cache: ~0.5-1s
- Section feature extraction: ~2-5s per section
- **Total:** ~5-10s per song

**Compatibility Matrix:**
- N songs with 1 section each: N*(N-1)/2 pairs
- 10 songs = 45 pairs (~1s total)
- 50 songs = 1,225 pairs (~30s total)

**Transition Generation:**
- Per transition: ~3-5s (audio loading + crossfade + save)
- 10 transitions with 3 durations each = 30 files = ~2-3 minutes

### 6.2 Caching Strategy

**Current Cache:** Song-level analysis results (in `poc_output_allinone/cache/`)
**New Cache (Optional):** Section-level features

**Recommendation:** Don't cache section features initially - they're fast to recompute and storage overhead is high. Re-use existing song-level cache.

### 6.3 Memory Usage

**Per-Song Embeddings:** (4, ~1000 timesteps, 24 dims) × 4 bytes = ~400 KB
**10 Songs in Memory:** ~4 MB (negligible)

**Section Audio (Stereo):**
- 30-second chorus at 44.1kHz: 2 × 30 × 44100 × 4 bytes = ~10 MB
- 10 sections in memory: ~100 MB (manageable)

**Recommendation:** Load section audio on-demand during transition generation, don't keep all in memory.

---

## 7. Testing and Validation

### 7.1 Unit Tests (Optional but Recommended)

**File:** `tests/test_section_analysis.py`

**Test Cases:**
1. `test_extract_section_features_chorus()` - Verify feature extraction
2. `test_select_best_chorus_no_fallback()` - Chorus selection without verse fallback
3. `test_select_best_chorus_with_fallback()` - Fallback to verse when no chorus
4. `test_section_compatibility_same_section()` - Self-compatibility = 100
5. `test_section_transition_generation()` - Generate transition and verify duration
6. `test_embeddings_timestep_alignment()` - Verify correct section embedding extraction

### 7.2 Integration Test (Manual)

**Setup:**
1. Use existing `poc_audio/` with 3-5 songs
2. Ensure at least one song has multiple choruses
3. Ensure at least one song has NO chorus (to test verse fallback)

**Expected Outputs:**
1. Section compatibility CSV with N*(N-1)/2 rows
2. Heatmap visualization showing color gradient
3. At least 3 transition audio files for top-scoring pair
4. Metadata JSON with all transition details

**Validation Checks:**
- [ ] All chorus sections correctly identified
- [ ] Verse fallback triggered for songs without chorus
- [ ] Embeddings score in range [0, 100]
- [ ] Generated audio files playable and correct duration
- [ ] CSV scores match JSON metadata
- [ ] Heatmap displays correctly with annotations

---

## 8. Future Enhancements (Out of Scope)

### 8.1 Multi-Chorus Support
- Analyze all choruses per song
- Generate all chorus-to-chorus combinations (e.g., song A chorus 2 → song B chorus 1)
- Select "best pair" across all combinations

### 8.2 Verse/Bridge Transitions
- Extend to verse-to-verse, bridge-to-bridge
- Mixed transitions (e.g., chorus → verse)

### 8.3 Tempo Warping
- Time-stretch sections to perfect tempo match before crossfade
- Requires `pyrubberband` or `librosa.effects.time_stretch`

### 8.4 Beat-Aligned Transitions
- Align transitions to downbeats for musical precision
- Use detected downbeats from allin1

### 8.5 Real-Time Section Detection
- Integrate with live worship streaming
- Detect current section in real-time and suggest next song

### 8.6 Playlist Generation
- Automatically create worship setlists based on section compatibility graph
- Optimize for flow (e.g., high-energy choruses early, mellow verses later)

---

## 9. Open Questions for Review

1. ✅ **RESOLVED: Embeddings Weighting (35%)** - Now configurable via CLI `--embeddings-weight`. User can experiment with different values or disable entirely.

2. **Energy Score Formula:** The current formula weights loudness at 70%. Should we increase brightness/duration weight for more musical selection? (This affects chorus selection, not compatibility scoring)

3. ✅ **RESOLVED: Fallback Strategy** - User selected "fallback to verse" in questionnaire. Future: could add `--fallback-section` CLI arg for other sections.

4. **Crossfade Duration Selection:** Should we use different default durations for section-level vs song-level transitions? (e.g., shorter 4-8s for chorus-chorus since sections are more musically similar)

5. **Compatibility Threshold:** Default `min_score=60` is based on song-level. Should section-level have different threshold (e.g., 70) since we expect higher compatibility? Or make this configurable via CLI?

6. **Output Directory Structure:** Should section transitions be in a separate directory (`section_transitions/`) or subdirectory by section type (`transitions/chorus/`, `transitions/verse/`)? Current spec uses `section_transitions/`.

---

## 10. Success Criteria

### 10.1 Functional Requirements
- ✅ Generate section compatibility CSV for all song pairs (chorus-to-chorus)
- ✅ Select best chorus per song using energy heuristics
- ✅ Fallback to verse when no chorus detected
- ✅ Compute hybrid compatibility score (embeddings + traditional metrics)
- ✅ Generate transition audio files for high-scoring section pairs
- ✅ Create heatmap visualization of section compatibility

### 10.2 Quality Requirements
- ✅ Section-level compatibility scores correlate with musical quality (manual listening test)
- ✅ Best chorus selection matches human perception (>80% agreement)
- ✅ Embeddings contribute meaningfully to compatibility (not just noise)
- ✅ Generated transitions sound musically coherent

### 10.3 Performance Requirements
- ✅ Section feature extraction: <5s per section
- ✅ Compatibility matrix computation: <1 minute for 10 songs
- ✅ Transition generation: <5s per transition

---

## 11. Implementation Phases (Suggested)

### Phase 1: Foundation (Week 1)
1. Modify `poc_analysis_allinone.py` to add embeddings metadata
2. Create `analyze_sections.py` with `extract_section_features()`
3. Implement `select_best_chorus()` with energy scoring
4. Manual testing on 3-5 songs

### Phase 2: Compatibility Scoring (Week 1-2)
1. Implement `calculate_section_compatibility()` with hybrid scoring
2. Generate section compatibility CSV
3. Create heatmap visualization
4. Validate scores against manual listening

### Phase 3: Transition Generation (Week 2)
1. Create `generate_section_transitions.py`
2. Implement section-aware crossfade logic
3. Generate audio files + metadata
4. End-to-end testing

### Phase 4: Refinement (Week 2-3)
1. Tune energy score formula based on results
2. Adjust embeddings weight if needed
3. Handle edge cases (short sections, missing sections)
4. Documentation and examples

---

## 12. Dependencies and Prerequisites

### 12.1 Required Libraries (Already Available)
- `allin1` - Section detection and embeddings
- `librosa` - Audio processing and key detection
- `numpy` - Numerical operations
- `pandas` - Data manipulation
- `soundfile` - Audio I/O
- `matplotlib`, `seaborn` - Visualization

### 12.2 Code Changes Required
- **Modify:** `poc/poc_analysis_allinone.py` (2 lines added)
- **Create:** `poc/analyze_sections.py` (~400 lines)
- **Create:** `poc/generate_section_transitions.py` (~300 lines, based on existing `generate_transitions.py`)

### 12.3 Data Prerequisites
- Existing song analysis results in `poc_output_allinone/`
- Audio files in `poc_audio/`
- At least 3 songs with detected chorus sections for meaningful POC

---

## 13. References

### 13.1 Related Code Files
- `poc/poc_analysis_allinone.py:267-467` - Song analysis with sections
- `poc/generate_transitions.py:196-237` - Crossfade implementation
- `poc/poc_analysis_allinone.py:470-540` - Song-level compatibility scoring

### 13.2 Key Data Structures
- Section dict: Lines 403-408 in `poc_analysis_allinone.py`
- Embeddings storage: Lines 447-451 in `poc_analysis_allinone.py`
- Compatibility dict: Lines 527-540 in `poc_analysis_allinone.py`

---

**END OF DESIGN SPECIFICATION**

---

## Appendix A: Example Workflows

### Workflow 1: Default Configuration (Hybrid Scoring)
```bash
# 1. Re-run analysis with embeddings metadata (if needed)
python poc/poc_analysis_allinone.py

# 2. Run section-level analysis with default weights
python poc/analyze_sections.py
# Output:
#   - poc_output_allinone/section_features.json
#   - poc_output_allinone/section_compatibility_scores.csv
#   - poc_output_allinone/section_compatibility_heatmap.png

# 3. Generate chorus-to-chorus transitions
python poc/generate_section_transitions.py
# Output:
#   - poc_output_allinone/section_transitions/*.flac
#   - poc_output_allinone/section_transitions/section_transitions_metadata.json
#   - poc_output_allinone/section_transitions/section_transitions_summary.csv

# 4. Listen to transitions and evaluate quality
vlc poc_output_allinone/section_transitions/*.flac
```

### Workflow 2: Traditional Metrics Only (No Embeddings)
```bash
# 1. Analyze sections without embeddings
python poc/analyze_sections.py \
  --embeddings-weight 0 \
  --tempo-weight 0.4 \
  --key-weight 0.4 \
  --energy-weight 0.2

# 2. Generate transitions with same weights
python poc/generate_section_transitions.py \
  --embeddings-weight 0 \
  --tempo-weight 0.4 \
  --key-weight 0.4 \
  --energy-weight 0.2

# 3. Compare results to default hybrid approach
diff poc_output_allinone/section_compatibility_scores.csv \
     poc_output_allinone/section_compatibility_scores_traditional.csv
```

### Workflow 3: Vocals Embeddings Only
```bash
# 1. Analyze using only vocals stem for embeddings
python poc/analyze_sections.py \
  --embedding-stems vocals \
  --embeddings-weight 0.5 \
  --tempo-weight 0.25 \
  --key-weight 0.25

# 2. Generate transitions
python poc/generate_section_transitions.py \
  --embedding-stems vocals \
  --embeddings-weight 0.5 \
  --tempo-weight 0.25 \
  --key-weight 0.25

# 3. Listen to vocals-focused transitions
vlc poc_output_allinone/section_transitions/*.flac
```

### Workflow 4: Tempo + Embeddings Only (No Key/Energy)
```bash
# Experiment with only tempo and embeddings matching
python poc/analyze_sections.py \
  --tempo-weight 0.5 \
  --embeddings-weight 0.5 \
  --key-weight 0 \
  --energy-weight 0

python poc/generate_section_transitions.py \
  --tempo-weight 0.5 \
  --embeddings-weight 0.5 \
  --key-weight 0 \
  --energy-weight 0
```

### Workflow 5: Compare Different Weight Configurations
```bash
# Generate compatibility matrices with different weights
# Store results in separate output directories

# Traditional (no embeddings)
python poc/analyze_sections.py \
  --output-dir poc_output_allinone/comparison_traditional \
  --embeddings-weight 0 --tempo-weight 0.4 --key-weight 0.4 --energy-weight 0.2

# Embeddings-heavy
python poc/analyze_sections.py \
  --output-dir poc_output_allinone/comparison_embeddings \
  --embeddings-weight 0.6 --tempo-weight 0.2 --key-weight 0.1 --energy-weight 0.1

# Vocals-only embeddings
python poc/analyze_sections.py \
  --output-dir poc_output_allinone/comparison_vocals \
  --embedding-stems vocals --embeddings-weight 0.5 --tempo-weight 0.3 --key-weight 0.2

# Compare heatmaps side by side
open poc_output_allinone/comparison_*/section_compatibility_heatmap.png
```
