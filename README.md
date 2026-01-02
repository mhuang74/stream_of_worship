# Worship Music Transition System

Seamless Chinese worship music playback system for Stream of Praise (SOP) and similar worship songs.

## Project Status: POC Phase

**Current Phase:** Proof of Concept (Week 1)
**Goal:** Validate audio analysis pipeline with 3-5 songs

### What's Working (POC)

- ✅ Standalone POC analysis script (command-line)
- ✅ Jupyter notebook environment with Docker (interactive)
- ✅ Audio analysis (tempo, key, structure, energy)
- ✅ Compatibility scoring between song pairs
- ✅ Simple crossfade transition prototype

### What's NOT in POC

- ❌ Database (PostgreSQL) - Coming in Phase 2
- ❌ Web API (FastAPI) - Coming in Phase 5
- ❌ Frontend UI (Next.js) - Coming in Phase 5
- ❌ Advanced transitions (tempo stretching, pitch shift) - Future
- ❌ Full 400-song library processing - Phase 3

---

## Quick Start (POC)

### Prerequisites

1. **Docker Desktop** installed and running ([Download](https://www.docker.com/products/docker-desktop/))
2. **3-5 worship songs** in MP3 or FLAC format (test files)
3. **Terminal/Command Prompt** access

### Step-by-Step Setup and Execution

#### Step 1: Prepare Your Audio Files

```bash
# Create the poc_audio directory if it doesn't exist (already exists in repo)
# Place your test worship songs (MP3 or FLAC format) into poc_audio/
cp /path/to/your/songs/*.mp3 poc_audio/

# Verify files were copied
ls poc_audio/
```

**Expected output:** List of your audio files (e.g., `song1.mp3`, `song2.mp3`, etc.)

#### Step 2: Build the Docker Image

```bash
# Build the Docker image (first time only, or after dependencies change)
docker-compose build

# This will take 3-5 minutes as it:
# - Downloads Python 3.11 base image
# - Installs system dependencies (ffmpeg, audio libraries)
# - Installs Python packages (librosa, madmom, etc.)
```

**Expected output:**
- Build progress messages
- Final line: `Successfully tagged stream_of_worship_librosa:latest` or similar

#### Step 3: Run the POC Analysis

Choose **ONE** of the following two methods:

---

**Method A: Command-Line Script (Recommended)**

Best for: Quick analysis, automation, debugging, CI/CD

```bash
# Run the POC analysis script in a one-off container
docker-compose run --rm librosa python poc/poc_analysis.py
```

**What happens:**
1. Docker starts a new container from the built image
2. Mounts your local `poc_audio/` and `poc_output/` directories
3. Runs the analysis script
4. Saves results to `poc_output/`
5. Container automatically removed after completion (`--rm` flag)

**Expected output:**
```
============================================================
POC ANALYSIS - Worship Music Transition System
============================================================

Stage 1/7: Setup and Discovery
--------------------------------------------------------------
Found 3 audio files in poc_audio/
Output directory: poc_output/

Stage 2/7: Feature Extraction
--------------------------------------------------------------
[1/3] Analyzing: song1.mp3
  ✓ Tempo: 120.0 BPM
  ✓ Key: C major
  ✓ Structure: 5 sections detected
...
```

**Runtime:** ~30-60 seconds per song (e.g., 3 songs = ~2-3 minutes total)

**Alternative (if container is already running):**
```bash
# If you have a running container from Method B, use exec instead:
docker-compose exec librosa python poc/poc_analysis.py
```

---

**Method B: Interactive Jupyter Notebook**

Best for: Exploration, visualization, experimentation, learning

```bash
# Step 1: Start Jupyter Lab server
docker-compose up

# Keep this terminal window open - it shows server logs
```

**Expected output:**
```
[I 2024-01-01 12:00:00.000 ServerApp] Jupyter Server is running at:
[I 2024-01-01 12:00:00.000 ServerApp] http://0.0.0.0:8888/lab
```

```bash
# Step 2: Open your web browser and navigate to:
http://localhost:8888

# Step 3: In the Jupyter Lab file browser (left sidebar):
# - Click on "notebooks" folder
# - Click on "01_POC_Analysis.ipynb"

# Step 4: Run the analysis
# - Menu → Run → Run All Cells
# - Or press Shift+Enter repeatedly to run each cell

# Step 5: Wait for analysis to complete
# - Watch for completion indicators in each cell
# - Final cell will print "POC Analysis Complete!"
```

**Runtime:** ~2-5 minutes for 3-5 songs (same as Method A, but with interactive visualization)

```bash
# Step 6: Stop Jupyter Lab when done
# Press Ctrl+C in the terminal where docker-compose up is running
# Then run:
docker-compose down
```

---

#### Step 4: Review Results

```bash
# Check the generated outputs
ls -lh poc_output/

# Expected files:
# - poc_summary.csv                     (summary table)
# - poc_full_results.json               (detailed data)
# - poc_analysis_visualizations.png     (song charts)
# - poc_compatibility_scores.csv        (compatibility matrix)
# - poc_compatibility_heatmap.png       (heatmap)
# - transition_<songA>_to_<songB>.flac (sample transition)
# - transition_waveform.png             (transition visualization)
```

**View results:**
```bash
# Open summary CSV in spreadsheet app
open poc_output/poc_summary.csv        # macOS
xdg-open poc_output/poc_summary.csv    # Linux
start poc_output/poc_summary.csv       # Windows

# View visualizations
open poc_output/poc_analysis_visualizations.png
```

---

### Alternative: All-In-One Deep Learning Analysis

The project includes an **experimental deep learning approach** using the `allin1` library for more advanced music analysis. This alternative method provides:

- **ML-based beat/downbeat/tempo detection** (instead of librosa's signal processing)
- **Automatic segment labeling** (intro, verse, chorus, bridge, outro)
- **Audio embeddings** (24-dimensional feature vectors per stem)
- **Comparison baseline** for evaluating traditional vs. deep learning approaches

#### Prerequisites for All-In-One

1. Same as above (Docker Desktop, audio files)
2. **More disk space**: ~2-3 GB for PyTorch and deep learning models
3. **Longer build time**: 10-20 minutes for first build (downloads models)

#### Step 1: Build the All-In-One Docker Image

```bash
# Build the allinone Docker image using the separate docker-compose file
docker compose -f docker-compose.allinone.yml build

# This will take 10-20 minutes as it:
# - Installs PyTorch (CPU-only for x86_64, standard for ARM64/M-series)
# - Installs NATTEN library (neighborhood attention)
# - Installs allin1 music analysis library
# - Downloads pre-trained models on first run
```

**Expected output:**
- Build progress messages for allinone image
- Final line: `Successfully tagged allinone:latest` or similar

#### Step 2: Run All-In-One POC Analysis

```bash
# Run the POC analysis using all-in-one deep learning models
docker compose -f docker-compose.allinone.yml run --rm allinone python poc/poc_analysis_allinone.py
```

**What happens:**
1. Docker starts container from allinone image
2. Mounts `poc_audio/` (input) and `poc_output_allinone/` (output)
3. Runs deep learning analysis with all-in-one models
4. Saves results to `poc_output_allinone/`
5. Container automatically removed after completion

**Expected output:**
```
✓ All-in-one library loaded successfully
============================================================
POC ANALYSIS (All-In-One) - Worship Music Transition System
============================================================

Stage 1/7: Setup and Discovery
--------------------------------------------------------------
Found 3 audio files in poc_audio/
Output directory: poc_output_allinone/

Stage 2/7: Feature Extraction (Deep Learning)
--------------------------------------------------------------
[1/3] Analyzing: song1.mp3
  Loading all-in-one models...
  ✓ Beat tracking (ML): 120.5 BPM (confidence: 0.95)
  ✓ Segment labels: intro → verse → chorus → verse → outro
  ✓ Embeddings extracted (24-dim per stem)
...
```

**Runtime:** ~2-3 minutes per song (longer than librosa due to model inference)
- First run: Additional 1-2 minutes to download pre-trained models

**Note:** Model weights are cached in `~/.cache/` and persisted between runs.

#### Step 3: Review All-In-One Results

```bash
# Check the generated outputs
ls -lh poc_output_allinone/

# Expected files (similar to librosa output, plus embeddings):
# - poc_allinone_summary.csv                (summary with ML predictions)
# - poc_allinone_full_results.json          (detailed data + embeddings)
# - poc_allinone_visualizations.png         (visualizations with ML labels)
# - poc_allinone_compatibility_scores.csv   (compatibility matrix)
# - poc_allinone_compatibility_heatmap.png  (heatmap)
# - transition_allinone_<songA>_to_<songB>.flac
# - transition_allinone_waveform.png
```

#### Comparison: Librosa vs. All-In-One

| Feature | Librosa (Traditional) | All-In-One (Deep Learning) |
|---------|----------------------|---------------------------|
| **Tempo Detection** | Signal processing (onset envelopes) | Neural network (trained on labeled data) |
| **Beat Tracking** | Dynamic programming | Transformer-based model |
| **Segment Labels** | Generic (section_0, section_1) | Semantic (intro, verse, chorus) |
| **Embeddings** | Hand-crafted features (MFCCs) | Learned 24-dim embeddings |
| **Speed** | Fast (~30-60s per song) | Slower (~2-3 min per song) |
| **Accuracy** | Good for most songs | Better on complex songs |
| **Setup** | Lightweight | Requires PyTorch + models |

**When to use each:**
- **Librosa**: Quick POC, simpler setup, good enough for most worship music
- **All-In-One**: Production system, complex song structures, need semantic labels

---

## POC Validation Checklist

### 1. Tempo Accuracy

- [ ] Tap along to each song manually
- [ ] Compare to detected BPM (should be ±5 BPM)
- [ ] Verify in `poc_summary.csv`

**Success Criteria:** ≥80% of songs within ±5 BPM

### 2. Key Detection

- [ ] Compare to sheet music (if available)
- [ ] Or use external tool (Mixed In Key, Tunebat)
- [ ] Check `full_key` column in summary

**Success Criteria:** ≥70% match sheet music

### 3. Transition Quality

- [ ] Listen to `transition_*.flac` file
- [ ] Does crossfade sound natural?
- [ ] Any jarring discontinuities?

**Success Criteria:** Smooth, natural-sounding transition

### 4. Section Boundaries

- [ ] Review `poc_analysis_visualizations.png`
- [ ] Do colored sections align with actual structure?
- [ ] Are intro/outro/verse/chorus labels reasonable?

**Success Criteria:** ≥50% of boundaries align with real changes

---

## Troubleshooting

### Docker Issues

**Problem:** "Cannot connect to Docker daemon"

```bash
# Solution: Start Docker Desktop application
# Wait for it to fully start (whale icon in system tray)
```

**Problem:** "Port 8888 already in use"

```bash
# Solution: Stop existing Jupyter instance or change port
docker-compose down
# Edit docker-compose.yml: Change "8888:8888" to "8889:8888"
docker-compose up
```

### Audio Issues

**Problem:** "librosa.load() fails with codec error"

```bash
# Solution: Convert audio to supported format
ffmpeg -i input.m4a output.mp3
# Or use FLAC: ffmpeg -i input.m4a output.flac
```

**Problem:** "No audio files found"

```bash
# Solution: Check file location
ls poc_audio/
# Should show *.mp3 or *.flac files
# If empty, copy files: cp /path/to/songs/*.mp3 poc_audio/
```

### Analysis Issues

**Problem:** Tempo detection seems wrong

```python
# In poc/poc_analysis.py or Notebook Cell 2, adjust start_bpm parameter:
tempo_librosa, beats_frames = librosa.beat.beat_track(
    y=y, sr=sr,
    start_bpm=90,  # Change this (try 70 for slow, 120 for fast)
    units='frames'
)
```

**Problem:** Too many/few section boundaries

```python
# In poc/poc_analysis.py or Notebook Cell 2, adjust peak picking parameters:
peaks = librosa.util.peak_pick(
    onset_env,
    pre_max=5,     # Increase for fewer boundaries
    post_max=5,    # Increase for fewer boundaries
    delta=0.5,     # Increase for fewer boundaries
    wait=15
)
```

---

## Project Structure

```
stream_of_worship/
├── docker-compose.yml          # Docker service definitions
├── Dockerfile                  # Container image
├── pyproject.toml             # Python dependencies (Poetry)
├── README.md                  # This file
├── .gitignore                 # Git exclusions
│
├── specs/                     # Design documents
│   └── worship-music-transition-system-design.md
│
├── poc/                       # POC scripts
│   ├── __init__.py            # Package marker
│   ├── poc_analysis.py        # Standalone analysis script
│   ├── reproduce_error.py     # Debugging script
│   └── README.md              # POC script documentation
│
├── notebooks/                 # Jupyter notebooks
│   └── 01_POC_Analysis.ipynb  # Interactive POC analysis
│
├── poc_audio/                 # Test audio files (add 3-5 songs here)
│   └── .gitkeep
│
└── poc_output/                # Generated outputs
    └── .gitkeep
```

---

## Next Steps After POC

If validation passes:

### Phase 2: Core Infrastructure (2 weeks)

- [ ] PostgreSQL database schema
- [ ] SQLAlchemy models
- [ ] Modular preprocessing pipeline (`src/preprocessing/`)
- [ ] Unit tests

### Phase 3: Batch Processing (1 week)

- [ ] Process full SOP library (~400 songs)
- [ ] Compute compatibility matrix
- [ ] Database population

### Phase 4: Runtime System (2 weeks)

- [ ] Playlist generator
- [ ] Transition renderer (tempo stretch, pitch shift)
- [ ] Playback engine

### Phase 5: API & UI (2 weeks)

- [ ] FastAPI REST endpoints
- [ ] Next.js frontend
- [ ] Playback controls

### Phase 6: Deployment (1 week)

- [ ] Production Docker setup
- [ ] Performance tuning
- [ ] Documentation

**Total Timeline:** 9 weeks for MVP

---

## Resources

- **POC Script Guide:** [poc/README.md](poc/README.md)
- **Design Document:** [specs/worship-music-transition-system-design.md](specs/worship-music-transition-system-design.md)
- **librosa Documentation:** https://librosa.org/doc/latest/
- **madmom Documentation:** https://madmom.readthedocs.io/
- **Stream of Praise:** https://www.sop.org/

---

## Contributing

POC phase is exploratory. Feedback on:

- Analysis accuracy
- Additional test songs
- Edge cases or failure modes

---

## License

MIT License - See [LICENSE](LICENSE) file

---

**Last Updated:** 2025-12-30
**POC Status:** Ready for validation (Standalone script + Jupyter notebook)
