# POC Scripts Summary

This document summarizes the various Proof of Concept (POC) scripts in this directory, including their purposes, models used, libraries involved, and detailed input/output specifications.

## Table of Contents

- [Audio Analysis Scripts](#audio-analysis-scripts)
- [Lyrics & Transcription Scripts](#lyrics--transcription-scripts)
- [Transition Generation Scripts](#transition-generation-scripts)
- [Review & Analysis Scripts](#review--analysis-scripts)
- [Utility Scripts](#utility-scripts)

---

## Audio Analysis Scripts

### `poc_analysis.py`

**Purpose**: Baseline audio analysis for worship music using traditional signal processing (librosa). Validates audio analysis pipeline with 3-5 Stream of Praise worship songs.

**Features**:
- Tempo detection (BPM)
- Key detection using Krumhansl-Schmuckler profiles
- Energy metrics (RMS, loudness dB, spectral centroid)
- Structure segmentation (intro, verse, chorus, outro)
- Compatibility scoring between songs
- Simple crossfade transition generation

**Models**: None (traditional signal processing)

**Libraries**:
- **Audio Processing**: librosa, soundfile
- **Data/Math**: numpy, pandas, scipy
- **Visualization**: matplotlib, seaborn
- **Utilities**: pathlib, json, hashlib

**Inputs**:
- **Local Files**: Audio files from `poc_audio/` (MP3/FLAC)
  - Scans for `*.mp3` and `*.flac` files
  - Default directory: `poc_audio/`

**Outputs**:
- **Local Files**: Analysis results to `poc_output/`
  - `poc_summary.csv`: Summary table (CSV)
  - `poc_full_results.json`: Full analysis results (JSON)
  - `poc_compatibility_scores.csv`: Compatibility matrix (CSV)
  - `poc_analysis_visualizations.png`: Song visualizations (PNG)
  - `poc_compatibility_heatmap.png`: Compatibility heatmap (PNG)
  - `transition_*.flac`: Sample transition audio (if 2+ songs)
  - `transition_waveform.png`: Transition waveform visualization
- **Console**: Progress messages and analysis statistics

---

### `poc_analysis_allinone.py`

**Purpose**: Enhanced audio analysis using deep learning models (all-in-one) for more accurate beat/downbeat/tempo detection and ML-predicted segment labels. Includes optional stem generation via Demucs.

**Features**:
- All-in-one deep learning model for beat/downbeat/tempo detection
- ML-predicted segment labels (intro/verse/chorus/bridge/outro)
- 24-dim embeddings per stem (bass, drums, other, vocals)
- Key detection via librosa chroma
- Caching system for faster re-analysis
- Optional stem generation (Demucs htdemucs model)

**Models**:
- **all-in-one**: Deep learning model for audio structure analysis (beat tracking, segmentation, embeddings)
- **Demucs**: htdemucs model for stem separation (optional)

**Libraries**:
- **Audio Processing**: allin1, librosa, soundfile
- **ML Framework**: torch (PyTorch)
- **Data/Math**: numpy, pandas, scipy
- **Visualization**: matplotlib, seaborn
- **Utilities**: pathlib, json, hashlib, base64
- **Stem Separation**: subprocess (for Demucs)

**Inputs**:
- **Local Files**: 
  - Audio files from `poc_audio/` (MP3/FLAC)
  - Optional stems from `poc_output_allinone/stems/` (if `--generate-stems`)
- **Local Cache**: Analysis results from `poc_output_allinone/cache/`
  - Content-addressable cache via SHA256 file hashing
  - Cache files: `{hash[:32]}.json`
  - Reads embeddings serialized as base64
- **Optional**: Prior analysis via `poc_output_allinone/poc_full_results.json`

**Outputs**:
- **Local Files**: Analysis results to `poc_output_allinone/`
  - `poc_summary.csv`: Summary table (CSV)
  - `poc_full_results.json`: Full analysis with embeddings (JSON)
  - `poc_compatibility_scores.csv`: Compatibility matrix (CSV)
  - `poc_analysis_visualizations.png`: Song visualizations (PNG)
  - `poc_compatibility_heatmap.png`: Compatibility heatmap (PNG)
  - `transition_*.flac`: Sample transition audio (if 2+ songs)
  - `transition_waveform.png`: Transition waveform visualization
- **Local Cache**: `poc_output_allinone/cache/`
  - Individual cache files per song (SHA256-based filenames)
  - `metadata.json`: Central cache index
- **Optional Stem Files** (if `--generate-stems`: 
  - `poc_output_allinone/stems/{song_stem}/bass.wav`
  - `poc_output_allinone/stems/{song_stem}/drums.wav`
  - `poc_output_allinone/stems/{song_stem}/other.wav`
  - `poc_output_allinone/stems/{song_stem}/vocals.wav`
- **Console**: Progress messages, cache hit/miss info, analysis statistics

---

### `analyze_sections.py`

**Purpose**: Section-level audio analysis extending song-level analysis. Analyzes and compares song sections (chorus, verse, bridge) for more precise transitions.

**Features**:
- Extract section-specific features (tempo, key, energy, embeddings)
- Select "best" chorus per song using energy heuristics
- Calculate section compatibility with configurable weights
- Support for traditional metrics (tempo/key/energy) and ML embeddings
- Configurable stem selection for embeddings

**Models**: Uses data from `poc_analysis_allinone.py`

**Libraries**:
- **Audio Processing**: librosa (imported)
- **Data/Math**: numpy, pandas
- **Visualization**: matplotlib, seaborn
- **Utilities**: argparse, json, uuid

**Inputs**:
- **Local Files**: 
  - Audio files from `poc_audio/` (MP3/FLAC)
  - Stems from `poc_output_allinone/stems/` (for embedding calculations)
- **Local Cache**: Analysis results from `poc_output_allinone/cache/`
  - Read via `poc_analysis_allinone.load_from_cache()`
  - Requires `_embeddings`, `_beats` fields
- **Optional**: Prior analysis via `poc_output_allinone/poc_full_results.json`

**Outputs**:
- **Local Files**: Section analysis results to `poc_output_allinone/`
  - `section_features.json`: Section feature data with embeddings (JSON)
  - `section_compatibility_scores.csv`: Section compatibility matrix (CSV)
  - `section_compatibility_heatmap.png`: Section compatibility heatmap (PNG)
- **Console**: Progress messages, section selection info, compatibility scores

---

## Lyrics & Transcription Scripts

### `lyrics_scraper.py`

**Purpose**: Web scraper for extracting Chinese worship song lyrics from Stream of Praise (sop.org/songs) website.

**Features**:
- Scrapes lyrics table from sop.org/songs
- Extracts metadata (title, composer, lyricist, album, key)
- Preserves line breaks and structure
- Generates filesystem-safe song IDs using Pinyin romanization
- Saves individual song files and master index

**Models**: None

**Libraries**:
- **Web Scraping**: requests, beautifulsoup4
- **Natural Language**: pypinyin (for romanization)
- **Utilities**: argparse, json, logging, re

**Inputs**:
- **Remote**: HTTPS GET request to `https://www.sop.org/songs/`
  - Parses HTML TablePress table with id="tablepress-3"
- **CLI Arguments**: 
  - `--limit`: Maximum number of songs to scrape
  - `--output`: Output directory (default: `data/lyrics`)

**Outputs**:
- **Local Files**: Lyrics data to `data/lyrics/`
  - `data/lyrics/songs/{song_id}.json`: Individual song lyrics files
  - `data/lyrics/lyrics_index.json`: Master index with all song metadata
  - `data/lyrics/test_song.json`: Test song output (if `--test`)
- **Console**: Progress messages, scrape statistics

---

### `gen_lrc_whisper.py`

**Purpose**: Whisper transcription test driver for generating LRC (lyric timestamp) files using OpenAI's Whisper model.

**Features**:
- Transcribes audio using Whisper model
- Supports various Whisper models (base, small, medium, large-v3)
- Voice Activity Detection (VAD) filtering
- Segment extraction for partial transcription
- Initial prompt with song lyrics for better accuracy
- Configurable compute types (int8, float16, int8_float16)

**Models**: Whisper (OpenAI's automatic speech recognition)

**Libraries**:
- **Audio Processing**: faster_whisper
- **Audio Segment**: pydub
- **CLI**: typer
- **Utilities**: Local utils module (format_timestamp, extract_audio_segment, resolve_song_audio_path)

**Inputs**:
- **Local Files**: 
  - Direct audio file path (if input is file path)
  - Optional stems from local cache (if `--use-vocals`)
- **Local Cache** (via utils.resolve_song_audio_path):
  - Vocals stem: `{config.cache_dir}/stems/{hash_prefix}/vocals.flac` (if cached)
  - Main audio: `{config.cache_dir}/audio/{hash_prefix}.{ext}` (if cached)
- **Remote** (via R2):
  - Downloads assets from Cloudflare R2 bucket (if not in cache)
  - Uses `R2Client` and `AssetCache` classes
- **Database**: 
  - Queries SongDB for song metadata and lyrics (for initial prompt)
  - Reads from `{config.db_path}` (SQLite)
- **CLI**: 
  - `song_id`: Song ID or audio file path
  - `--use-vocals/--no-use-vocals`: Prefer vocals stem over main audio
  - `--output`: Output LRC file path (default: stdout)
  - `--start`, `--end`: Time range for partial transcription

**Outputs**:
- **Local Files**: LRC file (if `--output` specified)
  - LRC format: `[mm:ss.xx] 中文歌词`
- **Console**: 
  - LRC content to stdout (if no `--output`)
  - Progress messages: model loading, transcription status, segment counts

---

### `gen_lrc_qwen3.py`

**Purpose**: Qwen3-ForcedAligner for generating LRC files by aligning existing lyrics to audio timestamps (more accurate than transcription).

**Features**:
- Forces alignment of known lyrics to audio timestamps
- Requires pre-existing lyrics (no transcription)
- Character/word-level timestamp alignment
- Maps segments back to original lyric lines
- Maximum audio length 5 minutes
- Model caching support

**Models**: Qwen3-ForcedAligner-0.6B (forced alignment model)

**Libraries**:
- **ASR**: qwen_asr (Qwen3ForcedAligner)
- **ML Framework**: torch (PyTorch)
- **Audio**: pydub
- **CLI**: typer
- **Utilities**: Local utils module, hashlib

**Inputs**:
- **Local Files**: 
  - Direct audio file path (if input is file path)
  - Optional stems from local cache (if `--use-vocals`)
  - Lyrics file (if `--lyrics-file` specified)
  - Model cache: `{model_cache_dir}/hub/models--Qwen--Qwen3-ForcedAligner-0.6B/`
- **Local Cache** (via utils.resolve_song_audio_path):
  - Vocals stem: `{config.cache_dir}/stems/{hash_prefix}/vocals.flac` (if cached)
  - Main audio: `{config.cache_dir}/audio/{hash_prefix}.{ext}` (if cached)
- **Remote** (via R2):
  - Downloads assets from Cloudflare R2 bucket (if not in cache)
  - Downloads model from HuggingFace (if not cached)
- **Database**: 
  - Queries SongDB for song lyrics (required for forced alignment)
  - Reads from `{config.db_path}` (SQLite)
- **CLI**: 
  - `song_id`: Song ID or audio file path
  - `--use-vocals/--no-use-vocals`: Prefer vocals stem
  - `--lyrics-file`: Override database lyrics with file
  - `--output`: Output LRC file path (default: stdout)
  - `--model-cache-dir`: Custom model cache location
  - `--language`: Language hint (default: Chinese)
  - `--offline/--download`: Cache-only mode or allow R2 downloads

**Outputs**:
- **Local Files**: 
  - LRC file (if `--output` specified)
  - Model cache downloads (first time only)
- **Console**: 
  - LRC content to stdout (if no `--output`)
  - Progress messages: model caching, alignment status
  - Warning if audio exceeds 5-minute limit

---

### `gen_lrc_sensevoice.py`

**Purpose**: SenseVoice transcription driver for generating LRC files using Alibaba's SenseVoice ASR model (optimized for Chinese).

**Features**:
- Chinese-optimized speech recognition
- VAD (Voice Activity Detection) model integration
- Sentence-level timestamps with punctuation model
- Split-on-silence capability
- Configurable chunking and batching

**Models**:
- **SenseVoice**: iic/SenseVoiceSmall (ASR)
- **VAD**: fsmn-vad (voice activity detection)
- **Punctuation**: ct-punc-c (for sentence timestamps)

**Libraries**:
- **ASR**: funasr (AutoModel, rich_transcription_postprocess)
- **Audio**: pydub
- **CLI**: typer
- **Utilities**: Local utils module

**Inputs**:
- **Local Files**: 
  - Direct audio file path (if input is file path)
  - Optional stems from local cache (if `--use-vocals`)
- **Local Cache** (via utils.resolve_song_audio_path):
  - Vocals stem: `{config.cache_dir}/stems/{hash_prefix}/vocals.flac` (if cached)
  - Main audio: `{config.cache_dir}/audio/{hash_prefix}.{ext}` (if cached)
- **Remote** (via R2):
  - Downloads assets from Cloudflare R2 bucket (if not in cache)
  - Downloads ASR models from ModelScope (if not cached)
- **Database**: 
  - Queries SongDB for song metadata (not scripts, just for info)
  - Reads from `{config.db_path}` (SQLite)
- **CLI**: 
  - `song_id`: Song ID or audio file path
  - `--use-vocals/--no-use-vocals`: Prefer vocals stem
  - `--output`: Output LRC file path (default: stdout)
  - `--start`, `--end`: Time range for partial transcription
  - `--vad-model`: VAD model name
  - `--sentence-timestamp/--no-sentence-timestamp`: Enable sentence-level timestamps

**Outputs**:
- **Local Files**: 
  - LRC file (if `--output` specified)
  - Model cache downloads via `AutoModel`
  - Temporary segment files during transcription (cleaned up)
- **Console**: 
  - LRC content to stdout (if no `--output`)
  - Progress messages: VAD segmentation, transcription status

---

### `gen_lrc_omnisensevoice.py`

**Purpose**: OmniSenseVoice transcription driver for generating LRC files using the OmniSenseVoice ASR model.

**Features**:
- Multi-language support (zh, en, yue, ja, ko)
- Word-level timestamps
- Text normalization (with/without ITN)
- Chunk-based processing for long files
- Model quantization support

**Models**: iic/SenseVoiceSmall (via OmniSenseVoice wrapper)

**Libraries**:
- **ASR**: omnisense (OmniSenseVoiceSmall)
- **Audio**: pydub
- **CLI**: typer
- **Utilities**: Local utils module

**Inputs**:
- **Local Files**: 
  - Direct audio file path (if input is file path)
  - Optional stems from local cache (if `--use-vocals`)
- **Local Cache** (via utils.resolve_song_audio_path):
  - Vocals stem: `{config.cache_dir}/stems/{hash_prefix}/vocals.flac` (if cached)
  - Main audio: `{config.cache_dir}/audio/{hash_prefix}.{ext}` (if cached)
- **Remote** (via R2):
  - Downloads assets from Cloudflare R2 bucket (if not in cache)
  - Downloads model from ModelScope (if not cached)
- **Database**: 
  - Queries SongDB for song metadata
  - Reads from `{config.db_path}` (SQLite)
- **CLI**: 
  - `song_id`: Song ID or audio file path
  - `--use-vocals/--no-use-vocals`: Prefer vocals stem
  - `--output`: Output LRC file path (default: stdout)
  - `--chun k-seconds`: Chunk window size for long files
  - `--timestam ps/--no-timestamps`: Enable word-level timestamps

**Outputs**:
- **Local Files**: 
  - LRC file (if `--output` specified)
  - Model cache downloads
  - Temporary chunk files during transcription (cleaned up)
- **Console**: 
  - LRC content to stdout (if no `--output`)
  - Progress messages: chunking status, transcription time

---

### `gen_lrc_youtube.py`

**Purpose**: YouTube subtitle LRC generation prototype. Downloads YouTube transcripts and corrects them against official lyrics using an LLM.

**Features**:
- Downloads YouTube transcripts via youtube-transcript-api
- Uses LLM (configurable via environment variables) to correct lyrics
- Aligns auto-generated subtitles to official lyrics
- Preserves timecodes from YouTube transcripts

**Models**:
- **Transcription**: YouTube's auto-generated transcripts
- **Correction**: Configurable LLM (gpt-4, Claude, etc.)

**Libraries**:
- **YouTube**: youtube-transcript-api
- **LLM**: openai (OpenAI client - works with compatible APIs)
- **CLI**: typer
- **Utilities**: re, os

**Inputs**:
- **Remote**: 
  - YouTube video via HLS streaming (extracts transcripts via API)
  - LLM API call (configurable URL/key via environment variables)
- **Database**: 
  - Queries SongDB for lyrics and YouTube URL
  - Queries SongDB for official lyrics for alignment
  - Reads from `{config.db_path}` (SQLite)
- **Environment Variables**: 
  - `SOW_LLM_API_KEY`: LLM API key
  - `SOW_LLM_BASE_URL`: LLM base URL
  - `SOW_LLM_MODEL`: Model name (default: gpt-4)
- **CLI**: 
  - `song_id`: Song ID (looks up YouTube URL from database)
  - `--youtube-url`: Override YouTube URL directly
  - `--output`: Output LRC file path (default: stdout)
  - `--lang`: Subtitle language (default: en-US)
  - `--model`: LLM model name

**Outputs**:
- **Local Files**: 
  - LRC file (if `--output` specified)
- **Console**: 
  - LRC content to stdout (if no `--output`)
  - Full LLM prompt (for transparency/debugging)
  - Progress messages: transcript download, LLM call status

---

### `gen_lrc_whisperx.py`

**Purpose**: WhisperX enhanced transcription with word-level timestamps and diarization support.

**Features**:
- Whisper model with word-level timestamps
- Forced alignment with original audio
- Speaker diarization support
- Batch processing

**Models**: Whisper (via whisperad), pyannote.audio (for diarization)

**Libraries**:
- **ASR**: whisperx
- **Diarization**: pyannote.audio
- **Audio**: pydub
- **CLI**: typer
- **Utilities**: Local utils module

**Inputs**:
- **Local Files**: 
  - Direct audio file path (if input is file path)
  - Optional stems from local cache (if `--use-vocals`)
- **Local Cache** (via utils.resolve_song_audio_path):
  - Vocals stem: `{config.cache_dir}/stems/{hash_prefix}/vocals.flac` (if cached)
  - Main audio: `{config.cache_dir}/audio/{hash_prefix}.{ext}` (if cached)
- **Remote** (via R2):
  - Downloads assets from Cloudflare R2 bucket (if not in cache)
  - Downloads WhisperX models (alignment, diarization)
- **Database**: 
  - Queries SongDB for song metadata
  - Reads from `{config.db_path}` (SQLite)
- **CLI**: 
  - `song_id`: Song ID or audio file path
  - `--use-vocals/--no-use-vocals`: Prefer vocals stem
  - `--output`: Output LRC file path (default: stdout)

**Outputs**:
- **Local Files**: 
  - LRC file (if `--output` specified)
  - Model cache downloads
- **Console**: 
  - LRC content to stdout (if no `--output`)
  - Progress messages: alignment status, diarization status

---

## Transition Generation Scripts

### `generate_transitions.py`

**Purpose**: Multi-transition generator that creates multiple crossfade transitions between compatible song pairs.

**Features**:
- Reads pre-computed compatibility analysis
- Generates transitions for viable song pairs
- Multiple crossfade durations (6s, 8s, 10s, 12s)
- Adaptive duration selection based on tempo scores
- Equal-power crossfades

**Models**: None

**Libraries**:
- **Audio Processing**: librosa, soundfile
- **Data/Math**: numpy, pandas
- **Utilities**: json, datetime

**Inputs**:
- **Local Files**: 
  - Audio files from `poc_audio/` (MP3/FLAC)
  - Compatibility scores from `poc_output_allinone/poc_compatibility_scores.csv`
  - Full analysis from `poc_output_allinone/poc_full_results.json` (optional)
- **CLI Arguments**:
  - `min_score`: Minimum compatibility score (default: 60)
  - `max_pairs`: Limit number of pairs (default: all)

**Outputs**:
- **Local Files**: Transition outputs to `poc_output_allinone/transitions/`
  - `transition_{song_a}_{song_b}_{duration}s.flac`: Crossfade audio files
  - `transitions_metadata.json`: Full metadata (JSON)
  - `transitions_summary.csv`: Summary for spreadsheet viewing (CSV)
- **Console**: Progress messages, generation statistics, next steps

---

### `generate_section_transitions.py`

**Purpose**: Section-level transition generator with comprehensive metadata support (v2.1). Creates multi-variant transitions for song sections.

**Features**:
- Four transition variants per pair:
  - Medium-Crossfade: Full sections with equal-power crossfade (8s)
  - Medium-Silence: Full sections with tempo-based silence gap
  - Vocal-Fade: Vocal-only bridge with silence gap (using stems)
  - Drum-Fade: Drum-only bridge with silence gap (using stems)
- Comprehensive v2.0 metadata schema
- Review support with human ratings

**Models**: Uses stems from Demucs

**Libraries**:
- **Audio Processing**: librosa, soundfile
- **Data/Math**: numpy, pandas
- **Utilities**: json, uuid, argparse

**Inputs**:
- **Local Files**: 
  - Audio files from `poc_audio/` (MP3/FLAC)
  - Stems from `poc_output_allinone/stems/` (required for vocal/drum-fade variants)
  - Section compatibility scores from `poc_output_allinone/section_compatibility_scores.csv`
  - Full analysis results from `poc_output_allinone/poc_full_results.json` or cache
- **CLI Arguments**:
  - `min_score`: Minimum section compatibility score (default: 60)

**Outputs**:
- **Local Files**: Transition outputs to `poc_output_allinone/section_transitions/`
  - `audio/medium-crossfade/transition_*.flac`: Crossfade variants
  - `audio/medium-silence/transition_*.flac`: Silence variants
  - `audio/vocal-fade/transition_*.flac`: Vocal fade variants
  - `audio/drum-fade/transition_*.flac`: Drum fade variants
  - `metadata/transitions_index.json`: Master index (v2.0 schema)
  - `metadata/transitions_summary.csv`: Summary CSV
  - `metadata/review_progress.json`: Review session state
  - `metadata/analysis/feedback_analysis.json`: Feedback correlations
  - `metadata/analysis/correlation_analysis.png`: Score vs rating visualization
  - `metadata/analysis/variant_preferences.png`: Preferred variant distribution
- **Console**: Progress messages, generation statistics, variant count

---

### `gen_clean_vocal_stem.py`

**Purpose**: Two-stage vocal extraction pipeline for generating clean vocal stems with echo/reverb removal.

**Features**:
- Stage 1: Extract vocals using BS-Roformer-Viperx-1297
- Stage 2: Remove echo/reverb using UVR-De-Echo-Normal
- Outputs: instrumental, vocals (with reverb), vocals (dry/no echo), reverb only

**Models**:
- **Stage 1**: model_bs_roformer_ep_317_sdr_12.9755.ckpt (BS-Roformer-Viperx-1297)
- **Stage 2**: UVR-De-Echo-Normal.pth

**Libraries**:
- **Audio Separation**: audio-separator (Separator)
- **Utilities**: argparse, pathlib, time, json

**Inputs**:
- **Local Files**: 
  - Input audio file (any format supported by audio-separator: WAV, MP3, FLAC, etc.)
- **Local Models** (downloaded via audio-separator):
  - BS-Roformer model files (cached locally)
  - UVR-De-Echo-Normal model file (cached locally)
- **CLI**:
  - `input`: Input audio file path
  - `--output-dir`: Output directory (default: `vocal_extraction_output/{input_stem}`)
  - `--vocal-model`: Vocal extraction model name
  - `--dereverb-model`: Echo removal model name
  - `--reuse-stage1`: Reuse existing stage 1 outputs

**Outputs**:
- **Local Files**: To output directory (default: `vocal_extraction_output/{input_stem}/`)
  - `stage1_vocal_separation/*Vocals.flac`: Vocals with reverb
  - `stage1_vocal_separation/*Instrumental.flac`: Instrumental
  - `stage2_dereverb/*No Echo.flac`: Dry vocals (no reverb)
  - `stage2_dereverb/*Echo.flac`: Reverb only
  - `extraction_results.json`: Extraction metadata and timing info
- **Console**: Progress messages, timing statistics, file paths

---

## Review & Analysis Scripts

### `review_transitions.py`

**Purpose**: Interactive CLI for reviewing and rating section transitions with playback controls and progress tracking.

**Features**:
- Load transitions from master index
- Play audio variants with seek controls (arrow keys for ±5s)
- Collect structured feedback and ratings
- Save progress persistently
- Export summary CSV

**Models**: None

**Libraries**:
- **Audio Playback**: sounddevice, soundfile
- **Data/Math**: numpy, pandas
- **Utilities**: json, datetime, threading, select, termios, tty

**Inputs**:
- **Local Files**: 
  - Master index: `poc_output_allinone/section_transitions/metadata/transitions_index.json`
  - Audio files: `poc_output_allinone/section_transitions/audio/*/*.flac`
  - Review progress: `poc_output_allinone/section_transitions/metadata/review_progress.json`
- **CLI**: Interactive command loop
  - Commands: `p <variant>`, `← →`, `s`, `r`, `n`, `b`, `j <num>`, `i`, `h`, `q`

**Outputs**:
- **Local Files**: Updates to `poc_output_allinone/section_transitions/metadata/`
  - `transitions_index.json`: Updated with review data (atomic write)
  - `review_progress.json`: Session progress tracking
  - `transitions_summary_reviewed.csv`: Review summary export
- **Console**: 
  - Interactive review interface
  - Session summary on exit (songs reviewed, duration)
  - Help messages and progress updates

---

### `analyze_feedback.py`

**Purpose**: Analyze correlation between compatibility scores and human ratings from transition reviews.

**Features**:
- Correlation analysis (Pearson, Spearman)
- Weight tuning recommendations
- Variant preference analysis
- Setlist building insights
- Visualization generation

**Models**: None

**Libraries**:
- **Statistics**: scipy.stats
- **Data/Math**: numpy, pandas
- **Visualization**: matplotlib, seaborn
- **Utilities**: json, pathlib

**Inputs**:
- **Local Files**: 
  - Master index: `poc_output_allinone/section_transitions/metadata/transitions_index.json`
  - Configuration weights from index

**Outputs**:
- **Local Files**: Analysis outputs to `poc_output_allinone/section_transitions/metadata/analysis/`
  - `feedback_analysis.json`: Complete analysis results (JSON)
  - `correlation_analysis.png`: Score vs rating correlations (visualization)
  - `variant_preferences.png`: Preferred variant distribution (visualization)
- **Console**: 
  - Correlation statistics
  - Weight tuning recommendations
  - Setlist insights
  - Visualizations saved confirmation

---

## Utility Scripts

### `utils.py`

**Purpose**: Shared utilities for POC lyric generation scripts to reduce code duplication.

**Features**:
- Timestamp formatting for LRC files
- Audio segment extraction to temporary files
- Song ID resolution (path or database lookup)
- Database, R2 client, and asset cache integration
- Offline mode support

**Models**: None

**Libraries**:
- **Data**: typer
- **Audio**: pydub
- **Database**: stream_of_worship.app.db (ReadOnlyClient, CatalogService, AssetCache, R2Client)
- **Utilities**: tempfile

**Inputs**:
- **Database**: SongDB at `{config.db_path}` (SQLite)
- **Remote** (via R2): Cloudflare R2 bucket
  - Configured via environment variables or config file
- **Configuration**: App config loaded from `~/.sow-app/config.yaml`

**Outputs**:
- **Temporary Files**: 
  - Audio segment extracts (cleaned up after processing)
- **Local Cache** (downloads): 
  - Vocals stem: `{config.cache_dir}/stems/{hash_prefix}/vocals.flac`
  - Main audio: `{config.cache_dir}/audio/{hash_prefix}.{ext}`
- **Functions Return**: 
  - `format_timestamp()`: Formatted LRC timestamp string
  - `extract_audio_segment()`: Path to temporary audio file
  - `resolve_song_audio_path()`: Tuple of (audio_path, lyrics_list)

---

### `find_test_song.py`

**Purpose**: Helper script to locate and verify test songs in the catalog.

**Models**: None

**Libraries**:
- **Database**: stream_of_worship.app services
- **Utilities**: Local database client

**Inputs**:
- **Database**: SongDB at `{config.db_path}` (SQLite)

**Outputs**:
- **Console**: Test song information (title, recording hash, paths)

---

### `run_song_process_pipeline.sh`

**Purpose**: Shell script to orchestrate the full song processing pipeline.

**Models**: None

**Libraries**: None

**Inputs**:
- **Local**: Calls other POC scripts in sequence
  - `poc_analysis_allinone.py`
  - `analyze_sections.py`
  - `generate_section_transitions.py`
  - `review_transitions.py`

**Outputs**:
- **Console**: Pipeline execution logs

---

### Test Scripts

### `test_allinone_analyze.py`

**Purpose**: Test script for all-in-one analysis functionality.

**Inputs**: Uses test audio files from `poc_audio/`

**Outputs**: Console test results

**Libraries**: poc_analysis_allinone import

---

### `test_miniaudio.py`

**Purpose**: Test script for miniaudio library usage.

**Inputs**: Test audio file

**Outputs**: Console playback output

**Libraries**: miniaudio

---

### `test_sensevoice.py`

**Purpose**: Test script for SenseVoice ASR functionality.

**Inputs**: Test audio file

**Outputs**: Console transcription results

**Libraries**: funasr

---

### `test_whisper.py`

**Purpose**: Test script for Whisper ASR functionality.

**Inputs**: Test audio file

**Outputs**: Console transcription results

**Libraries**: faster_whisper

---

## Summary Table

| Script | Purpose | Model | Input Sources | Output Destinations | Key Libraries |
|--------|---------|-------|---------------|-------------------|---------------|
| poc_analysis.py | Baseline audio analysis | None | Local: `poc_audio/` MP3/FLAC | Local: `poc_output/` CSV/JSON/PNG/FLAC | librosa, pandas, seaborn |
| poc_analysis_allinone.py | DL-based audio analysis | all-in-one, Demucs | Local: `poc_audio/` + cache | Local: `poc_output_allinone/` + stems `stems/` + cache `cache/` | torch, allin1, librosa |
| analyze_sections.py | Section-level analysis | all-in-one | Local: `poc_audio/` + stems + cache | Local: `poc_output_allinone/` CSV/JSON/PNG | librosa, pandas |
| lyrics_scraper.py | Scrape lyrics from web | None | Remote: sop.org/songs | Local: `data/lyrics/` JSON | requests, beautifulsoup4, pypinyin |
| gen_lrc_whisper.py | Whisper transcription | Whisper | Local/R2: Audio + cache + DB | Console/Local: LRC | faster_whisper, pydub |
| gen_lrc_qwen3.py | Forced alignment | Qwen3-ForcedAligner | Local/Remote/R2: Audio + lyrics + cache | Console/Local: LRC + model cache | qwen_asr, torch |
| gen_lrc_sensevoice.py | SenseVoice ASR | SenseVoice, fsmn-vad | Local/Remote/R2: Audio + cache | Console/Local: LRC + model cache | funasr, pydub |
| gen_lrc_omnisensevoice.py | OmniSenseVoice ASR | SenseVoice | Local/Remote/R2: Audio + cache | Console/Local: LRC + model cache | omnisense, pydub |
| gen_lrc_youtube.py | YouTube transcript + LLM | YouTube + LLM | Remote: YouTube + LLM API + DB lyrics | Console/Local: LRC | youtube-transcript-api, openai |
| gen_lrc_whisperx.py | WhisperX with diarization | Whisper, pyannote.audio | Local/R2: Audio + cache | Console/Local: LRC + model cache | whisperx, pyannote.audio |
| generate_transitions.py | Song-level transitions | None | Local: `poc_audio/` + CSV analysis | Local: `transitions/` FLAC/JSON/CSV | librosa, soundfile |
| generate_section_transitions.py | Section-level transitions | Demucs stems | Local: Audio + stems + CSV/JSON | Local: `section_transitions/` FLAC/JSON/CSV/PNG | librosa, soundfile |
| gen_clean_vocal_stem.py | Vocal extraction pipeline | BS-Roformer, UVR-De-Echo | Local: Input audio file | Local: Vocal stems (FLAC) + JSON | audio-separator |
| review_transitions.py | Review transitions | None | Local: Index JSON + audio files | Local: Updated JSON + CSV | sounddevice, pandas |
| analyze_feedback.py | Analyze feedback | None | Local: Index JSON | Local: JSON + PNG | scipy.stats, pandas, seaborn |
| utils.py | Shared utilities | None | DB + R2 + local | Temporary files + cache | pydub, stream_of_worship |

---

## Common Pattern: LRC Generation

All `gen_lrc_*.py` scripts follow a common pattern:

1. **Resolve Audio**: Use `resolve_song_audio_path()` to get audio file
   - First checks local file path (if provided as path)
   - Then checks local cache (`{config.cache_dir}/stems/` and `/audio/`)
   - Finally downloads from R2 (Cloudflare object storage) if not cached
   - Returns: `(audio_path, lyrics_list)`

2. **Transcribe/Align**: Run ASR or forced alignment with various models
   - Downloads models from HuggingFace/ModelScope if not cached
   - Processes audio locally (no audio uploaded to Cloud)

3. **Format Output**: Convert timestamps/text to LRC format using `format_timestamp()`
   - Format: `[mm:ss.xx] 中文歌词`

4. **Save/Output**: Write to file or print to stdout
   - File path via `--output` argument, or stdout by default

This modular design allows easy experimentation with different ASR models and configurations.

---

## Common Pattern: Transition Generation

Transition scripts follow this pattern:

1. **Load Analysis**: Read compatibility scores and metadata
   - From `poc_compatibility_scores.csv` (song-level) or `section_compatibility_scores.csv` (section-level)
   - Or from `poc_full_results.json` / cache

2. **Filter Candidates**: Select viable pairs above score threshold
   - Uses `min_score` parameter (default: 60)
   - Sorts by compatibility score (highest first)

3. **Generate Audio**: Create transitions with different methods
   - Crossfade: Equal-power curve mixing
   - Silence: Tempo-based gap calculation
   - Stem-based: Separate fades for different stems (vocal/drum)

4. **Save Metadata**: Export comprehensive JSON metadata
   - v2.0 schema with compatibility details
   - Supports human review ratings

5. **Support Review**: Include fields for human ratings and feedback
   - Status: pending/reviewed/approved/rejected
   - Ratings: overall, theme_fit, musical_fit, etc.
   - Preferred variant: medium-crossfade/vocal-fade/etc.

The section-level version supports multiple variants per pair for A/B testing.

---

## Data Flow Diagrams

### LRC Generation Data Flow

```
[SongDB/Cache/R2/Local] → [resolve_song_audio_path()] → [Audio File]
[SongDB] → [Lyrics] → [ASR/Align Model] → [Timestamps + Text]
[format_timestamp()] → [LRC Format] → [File/Stdout]
```

### Transition Generation Data Flow

```
[poc_audio/] → [Audio Files]
[poc_analysis_allinone/] → [Cache + Embeddings]
[CSV/JSON] → [Compatibility Scores]
[generate_*_transitions.py] → [Transitions Audio]
[transitions_index.json] → [Review Metadata]
```

## Next Steps

For production use, consider:

1. **Batch Processing**: Run analyses on large song catalogs
2. **Database Integration**: Save results to SongDB
3. **API Endpoints**: Expose analysis and generation services
4. **Quality Metrics**: Automatically evaluate transition quality
5. **Optimization**: GPU acceleration for heavy models (ASR, all-in-one)

---

*Generated: 2026-01-06*
*POC Version: 2.1.0*