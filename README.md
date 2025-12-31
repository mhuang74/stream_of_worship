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

1. **Docker Desktop** installed ([Download](https://www.docker.com/products/docker-desktop/))
2. **3-5 worship songs** in MP3 or FLAC format (test files)

### Setup Steps

```bash
# 1. Clone repository (if not already done)
git clone <your-repo-url>
cd stream_of_worship

# 2. Place your test songs in poc_audio/
cp /path/to/your/songs/*.mp3 poc_audio/

# 3. Build Docker container
docker-compose build
```

### Running the Analysis

**Option 1: Command-Line Script (Recommended)**

Best for: Quick analysis, automation, debugging, CI/CD

```bash
# Run the standalone POC analysis script
docker-compose run --rm jupyter python poc/poc_analysis.py

# Or, if container is already running:
docker-compose exec jupyter python poc/poc_analysis.py
```

**Option 2: Interactive Jupyter Notebook**

Best for: Exploration, visualization, experimentation, learning

```bash
# 1. Start Jupyter Lab
docker-compose up

# 2. Open browser to: http://localhost:8888

# 3. Navigate to notebooks/01_POC_Analysis.ipynb

# 4. Run all cells: Menu → Run → Run All Cells

# 5. Wait 2-5 minutes for analysis to complete
```

**Review outputs in `poc_output/` directory**

### Expected Outputs

After running the analysis (either method), you should see:

```
poc_output/
├── poc_summary.csv                        # Summary table
├── poc_full_results.json                  # Full analysis data
├── poc_analysis_visualizations.png        # Song visualizations
├── poc_compatibility_scores.csv           # Compatibility matrix
├── poc_compatibility_heatmap.png          # Heatmap visualization
├── transition_<songA>_to_<songB>.flac    # Sample transition audio
└── transition_waveform.png                # Transition visualization
```

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
