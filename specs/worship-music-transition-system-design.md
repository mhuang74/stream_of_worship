# Worship Music Transition System - Design Document

**Project**: Seamless Chinese Worship Music Playback System  
**Target**: Stream of Praise (SOP) and similar Chinese worship songs  
**Version**: 1.0  
**Date**: December 2024

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [System Objectives](#system-objectives)
3. [Technical Architecture](#technical-architecture)
4. [Tech Stack](#tech-stack)
5. [Pre-Processing Pipeline](#pre-processing-pipeline)
6. [Database Design](#database-design)
7. [Runtime System](#runtime-system)
8. [System Integration](#system-integration)
9. [POC Phase Plan](#poc-phase-plan)
10. [Implementation Timeline](#implementation-timeline)
11. [Future Enhancements](#future-enhancements)

---

## Executive Summary

This design document outlines a Python-based system for creating seamless transitions between Chinese worship songs during extended playback sessions. The system analyzes audio files to extract musical features (tempo, key, structure), stores compatibility relationships in a database, and provides intelligent runtime playback with beat-synchronized transitions.

**Core Innovation**: Pre-computed compatibility matrix combined with on-demand transition rendering enables hours-long continuous worship sessions with musically and thematically coherent song sequencing.

---

## System Objectives

### Primary Goals

1. **Seamless Transitions**: Enable smooth audio transitions between worship songs without noticeable breaks
2. **Extended Sessions**: Support hours-long continuous playback with intelligent song selection
3. **Musical Intelligence**: Match songs by tempo, key, energy, and thematic compatibility
4. **Structure Awareness**: Transition at natural musical boundaries (chorus endings, outros)

### Success Criteria

- Transitions feel natural to worship leaders and congregation
- 95%+ of automatic transitions require no manual intervention
- System handles 200-500 song libraries efficiently
- Latency < 2 seconds for transition rendering
- Support for both pre-rendered and real-time transition modes

---

## Technical Architecture

### High-Level System Design

```
┌─────────────────────────────────────────────────────────────┐
│                    WORSHIP MUSIC SYSTEM                      │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌────────────────┐      ┌──────────────────┐               │
│  │  Audio Library │─────▶│  Pre-Processing  │               │
│  │  (MP3/FLAC)    │      │     Pipeline     │               │
│  └────────────────┘      └─────────┬────────┘               │
│                                     │                         │
│                                     ▼                         │
│                          ┌──────────────────┐                │
│                          │   Feature Store  │                │
│                          │   (PostgreSQL)   │                │
│                          └─────────┬────────┘                │
│                                     │                         │
│                    ┌────────────────┼────────────────┐       │
│                    │                │                │       │
│                    ▼                ▼                ▼       │
│          ┌─────────────┐  ┌─────────────┐  ┌──────────────┐ │
│          │  Playlist   │  │ Transition  │  │   Playback   │ │
│          │  Generator  │  │  Renderer   │  │    Engine    │ │
│          └─────────────┘  └─────────────┘  └──────────────┘ │
│                    │                │                │       │
│                    └────────────────┼────────────────┘       │
│                                     ▼                         │
│                          ┌──────────────────┐                │
│                          │  Audio Output    │                │
│                          │  (sounddevice)   │                │
│                          └──────────────────┘                │
└─────────────────────────────────────────────────────────────┘
```

### Design Principles

1. **Offline Analysis, Online Playback**: Heavy computation during pre-processing; lightweight runtime
2. **Caching Strategy**: Pre-render high-value transitions; generate others on-demand
3. **Modular Components**: Loosely coupled modules for easy testing and extension
4. **Data-Driven Decisions**: Musical compatibility based on quantitative analysis
5. **Graceful Degradation**: Fall back to simpler transitions if advanced processing fails

---

## Tech Stack

### Core Libraries

| Component | Library | Version | Justification |
|-----------|---------|---------|---------------|
| Audio I/O | librosa | 0.10.1+ | Industry standard for music analysis |
| Beat Detection | madmom | 0.17+ | Superior accuracy for slow worship tempos |
| Feature Extraction | essentia | 2.1+ | Comprehensive feature set, batch processing |
| Audio Playback | sounddevice | 0.4.6+ | Low-latency real-time audio |
| Time Stretching | pyrubberband | 0.3.0+ | High-quality tempo adjustment |
| Database | PostgreSQL | 15+ | Robust storage with JSON support |
| ORM | SQLAlchemy | 2.0+ | Python-native DB interaction |
| Task Queue | Celery | 5.3+ | Background job processing |
| Caching | Redis | 7.0+ | Fast transition cache lookup |
| Web UI | FastAPI | 0.104+ | Modern async REST API |
| Frontend | React | 18+ | Interactive playlist management |

### Python Environment

```toml
# pyproject.toml
[tool.poetry]
name = "worship-music-system"
version = "1.0.0"
description = "Seamless worship music transition system"
python = "^3.11"

[tool.poetry.dependencies]
librosa = "^0.10.1"
madmom = "^0.17"
essentia-tensorflow = "^2.1b6.dev1110"
sounddevice = "^0.4.6"
pyrubberband = "^0.3.0"
psycopg2-binary = "^2.9.9"
sqlalchemy = "^2.0.23"
alembic = "^1.13.0"
celery = "^5.3.4"
redis = "^5.0.1"
fastapi = "^0.104.1"
uvicorn = "^0.24.0"
pydantic = "^2.5.0"
numpy = "^1.26.2"
scipy = "^1.11.4"
pandas = "^2.1.4"
```

### Development Tools

- **Jupyter Lab**: POC experimentation and analysis
- **pytest**: Unit and integration testing
- **black/ruff**: Code formatting and linting
- **pre-commit**: Git hooks for code quality
- **Docker**: Containerized deployment

---

## Pre-Processing Pipeline

### Pipeline Overview

The pre-processing pipeline analyzes each song once, extracting features needed for runtime decision-making.

```
Audio File → Feature Extraction → Analysis → Storage → Compatibility Matrix
```

### Stage 1: Audio Loading and Normalization

**Module**: `preprocessing.audio_loader`

```python
class AudioLoader:
    """Load and normalize audio files"""
    
    def load_audio(self, filepath: Path) -> tuple[np.ndarray, int]:
        """
        Load audio file and normalize to mono, 22050 Hz
        
        Returns:
            (audio_data, sample_rate)
        """
        y, sr = librosa.load(filepath, sr=22050, mono=True)
        # Normalize to -1.0 to 1.0 range
        y = librosa.util.normalize(y)
        return y, sr
    
    def extract_stereo(self, filepath: Path) -> tuple[np.ndarray, int]:
        """
        Load stereo audio for transition rendering
        """
        y, sr = librosa.load(filepath, sr=44100, mono=False)
        return y, sr
```

**Outputs**:
- Mono 22050 Hz audio for analysis
- Stereo 44100 Hz audio cached for playback

### Stage 2: Tempo and Beat Analysis

**Module**: `preprocessing.rhythm_analysis`

```python
class RhythmAnalyzer:
    """Extract tempo, beats, and downbeats"""
    
    def analyze_tempo(self, y: np.ndarray, sr: int) -> dict:
        """
        Multi-method tempo detection with consensus
        """
        # Method 1: librosa onset-based
        tempo_librosa, beats_librosa = librosa.beat.beat_track(
            y=y, sr=sr, start_bpm=80, units='time'
        )
        
        # Method 2: madmom RNN-based (more accurate for slow tempos)
        proc = madmom.features.RNNBeatProcessor()
        act = proc(filepath)
        beat_proc = madmom.features.beats.DBNBeatTrackingProcessor(
            min_bpm=40, max_bpm=200, fps=100
        )
        beats_madmom = beat_proc(act)
        
        # Compute BPM from madmom beats
        if len(beats_madmom) > 1:
            intervals = np.diff(beats_madmom)
            tempo_madmom = 60.0 / np.median(intervals)
        else:
            tempo_madmom = tempo_librosa
        
        # Consensus: prefer madmom if difference < 10%, else flag for review
        if abs(tempo_librosa - tempo_madmom) / tempo_madmom < 0.1:
            final_tempo = tempo_madmom
            final_beats = beats_madmom
            confidence = "high"
        else:
            final_tempo = tempo_madmom
            final_beats = beats_madmom
            confidence = "review"
        
        return {
            'tempo': final_tempo,
            'tempo_librosa': tempo_librosa,
            'tempo_madmom': tempo_madmom,
            'beats': final_beats.tolist(),
            'confidence': confidence,
            'num_beats': len(final_beats)
        }
    
    def detect_downbeats(self, y: np.ndarray, sr: int, beats: np.ndarray) -> np.ndarray:
        """
        Detect downbeats (first beat of measure)
        Assumes 4/4 time signature for worship music
        """
        proc = madmom.features.DBNDownBeatTrackingProcessor(
            beats_per_bar=4, fps=100
        )
        downbeats = proc(madmom.features.RNNDownBeatProcessor()(filepath))
        return downbeats
```

**Outputs**:
- `tempo`: BPM (float)
- `beats`: Beat timestamps in seconds
- `downbeats`: Measure boundaries
- `confidence`: Analysis quality flag

### Stage 3: Harmonic Analysis

**Module**: `preprocessing.harmonic_analysis`

```python
class HarmonicAnalyzer:
    """Extract key, chroma features, harmonic structure"""
    
    def detect_key(self, y: np.ndarray, sr: int) -> dict:
        """
        Key detection using chroma correlation
        """
        # Compute chromagram
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        
        # Average over time
        chroma_avg = np.mean(chroma, axis=1)
        
        # Krumhansl-Schmuckler profiles
        major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 
                                  2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
        minor_profile = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                                  2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
        
        # Correlate with all 24 keys
        keys = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
        correlations = []
        
        for shift in range(12):
            # Major correlation
            major_corr = np.corrcoef(
                chroma_avg, 
                np.roll(major_profile, shift)
            )[0, 1]
            correlations.append(('major', keys[shift], major_corr))
            
            # Minor correlation
            minor_corr = np.corrcoef(
                chroma_avg,
                np.roll(minor_profile, shift)
            )[0, 1]
            correlations.append(('minor', keys[shift], minor_corr))
        
        # Find best match
        best = max(correlations, key=lambda x: x[2])
        
        # Camelot wheel mapping for DJ-style key compatibility
        camelot = self._get_camelot_code(best[1], best[0])
        
        return {
            'key': best[1],
            'mode': best[0],
            'confidence': best[2],
            'camelot': camelot,
            'full_key': f"{best[1]} {best[0]}"
        }
    
    def _get_camelot_code(self, key: str, mode: str) -> str:
        """Map key to Camelot wheel code"""
        camelot_major = {
            'C': '8B', 'G': '9B', 'D': '10B', 'A': '11B', 'E': '12B',
            'B': '1B', 'F#': '2B', 'C#': '3B', 'G#': '4B', 'D#': '5B',
            'A#': '6B', 'F': '7B'
        }
        camelot_minor = {
            'A': '8A', 'E': '9A', 'B': '10A', 'F#': '11A', 'C#': '12A',
            'G#': '1A', 'D#': '2A', 'A#': '3A', 'F': '4A', 'C': '5A',
            'G': '6A', 'D': '7A'
        }
        
        if mode == 'major':
            return camelot_major.get(key, '?')
        else:
            return camelot_minor.get(key, '?')
    
    def compute_harmonic_complexity(self, y: np.ndarray, sr: int) -> float:
        """
        Estimate harmonic complexity (for energy matching)
        """
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        # Higher entropy = more complex harmony
        entropy = -np.sum(chroma * np.log(chroma + 1e-10), axis=0)
        return float(np.mean(entropy))
```

**Outputs**:
- `key`: Root note (C, D, E, etc.)
- `mode`: Major or minor
- `camelot`: Camelot wheel code (e.g., "8A")
- `harmonic_complexity`: Entropy measure

### Stage 4: Structure Segmentation

**Module**: `preprocessing.structure_analysis`

```python
class StructureAnalyzer:
    """Detect song sections (intro, verse, chorus, outro)"""
    
    def segment_song(self, y: np.ndarray, sr: int) -> dict:
        """
        Segment song into structural sections
        """
        # Compute self-similarity matrix
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        rec_matrix = librosa.segment.recurrence_matrix(
            chroma, 
            mode='affinity',
            metric='cosine'
        )
        
        # Detect boundaries using Foote novelty
        kernel_size = 32  # ~3 seconds at hop_length=512
        novelty = librosa.segment.timelag_filter(
            rec_matrix, 
            size=kernel_size, 
            axis=0
        )
        
        # Peak picking for boundaries
        peaks = librosa.util.peak_pick(
            novelty,
            pre_max=5,
            post_max=5,
            pre_avg=5,
            post_avg=5,
            delta=0.1,
            wait=10
        )
        
        # Convert frames to time
        times_frames = librosa.frames_to_time(peaks, sr=sr)
        
        # Add start and end
        boundaries = [0.0] + times_frames.tolist() + [librosa.get_duration(y=y, sr=sr)]
        
        # Label sections (heuristic-based)
        sections = self._label_sections(boundaries, y, sr)
        
        return {
            'boundaries': boundaries,
            'sections': sections,
            'num_sections': len(sections)
        }
    
    def _label_sections(self, boundaries: list[float], y: np.ndarray, sr: int) -> list[dict]:
        """
        Heuristic labeling of sections
        """
        sections = []
        total_duration = librosa.get_duration(y=y, sr=sr)
        
        for i in range(len(boundaries) - 1):
            start = boundaries[i]
            end = boundaries[i + 1]
            duration = end - start
            position = start / total_duration
            
            # Heuristics for worship songs:
            # - First section < 15s usually intro
            # - Last section < 20s usually outro
            # - Repeated sections likely chorus
            
            if i == 0 and duration < 15:
                label = 'intro'
            elif i == len(boundaries) - 2 and duration < 20:
                label = 'outro'
            else:
                # Default labeling (would be improved with ML model)
                if duration > 30:
                    label = 'verse'
                else:
                    label = 'chorus'
            
            sections.append({
                'label': label,
                'start': start,
                'end': end,
                'duration': duration
            })
        
        return sections
    
    def find_transition_points(self, sections: list[dict], beats: np.ndarray) -> dict:
        """
        Identify optimal transition in/out points
        """
        # Best transition IN: after intro, at first downbeat
        intro_end = next((s['end'] for s in sections if s['label'] == 'intro'), 0)
        transition_in = min(beats[beats >= intro_end], default=beats[0])
        
        # Best transition OUT: before outro, at last chorus end
        outro_start = next((s['start'] for s in sections[::-1] if s['label'] == 'outro'), 
                          sections[-1]['start'])
        last_chorus_end = next((s['end'] for s in sections[::-1] 
                               if s['label'] == 'chorus' and s['end'] < outro_start),
                              outro_start)
        transition_out = max(beats[beats <= last_chorus_end], default=beats[-1])
        
        return {
            'transition_in_time': float(transition_in),
            'transition_out_time': float(transition_out),
            'intro_duration': intro_end,
            'outro_duration': sections[-1]['end'] - outro_start
        }
```

**Outputs**:
- `boundaries`: Section boundary times
- `sections`: Labeled sections with start/end times
- `transition_in_time`: Optimal transition-in point
- `transition_out_time`: Optimal transition-out point

### Stage 5: Energy and Dynamics Analysis

**Module**: `preprocessing.energy_analysis`

```python
class EnergyAnalyzer:
    """Analyze loudness, dynamics, and energy profile"""
    
    def analyze_energy(self, y: np.ndarray, sr: int) -> dict:
        """
        Compute energy and dynamics features
        """
        # RMS energy over time
        rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
        
        # Overall loudness (LUFS approximation via RMS)
        rms_db = librosa.amplitude_to_db(rms, ref=np.max)
        
        # Spectral centroid (brightness)
        centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
        
        # Zero crossing rate (noisiness/percussion content)
        zcr = librosa.feature.zero_crossing_rate(y)[0]
        
        return {
            'rms_mean': float(np.mean(rms)),
            'rms_std': float(np.std(rms)),
            'loudness_db': float(np.mean(rms_db)),
            'dynamic_range_db': float(np.max(rms_db) - np.min(rms_db)),
            'spectral_centroid_mean': float(np.mean(centroid)),
            'spectral_centroid_std': float(np.std(centroid)),
            'zcr_mean': float(np.mean(zcr)),
            'energy_profile': rms.tolist()  # For visualization
        }
```

**Outputs**:
- `loudness_db`: Average loudness
- `dynamic_range_db`: Difference between loud and quiet parts
- `spectral_centroid_mean`: Brightness measure
- `energy_profile`: Time-series energy curve

### Stage 6: Lyrics Extraction (Optional)

**Module**: `preprocessing.lyrics_analysis`

For future implementation using Whisper for Chinese speech recognition:

```python
class LyricsAnalyzer:
    """Extract and analyze lyrics (future enhancement)"""
    
    def extract_lyrics(self, y: np.ndarray, sr: int) -> dict:
        """
        Use Whisper to transcribe Chinese lyrics
        """
        # Future: Whisper large-v3 with Chinese
        # For now: manual lyrics input or metadata extraction
        return {
            'lyrics': None,
            'lyrics_available': False
        }
```

### Pipeline Orchestration

**Module**: `preprocessing.pipeline`

```python
class PreprocessingPipeline:
    """Orchestrate full preprocessing pipeline"""
    
    def __init__(self):
        self.audio_loader = AudioLoader()
        self.rhythm_analyzer = RhythmAnalyzer()
        self.harmonic_analyzer = HarmonicAnalyzer()
        self.structure_analyzer = StructureAnalyzer()
        self.energy_analyzer = EnergyAnalyzer()
    
    def process_song(self, filepath: Path) -> dict:
        """
        Run full analysis pipeline on a single song
        """
        logger.info(f"Processing: {filepath}")
        
        # Load audio
        y, sr = self.audio_loader.load_audio(filepath)
        
        # Extract all features
        features = {
            'filepath': str(filepath),
            'filename': filepath.name,
            'duration': librosa.get_duration(y=y, sr=sr)
        }
        
        # Rhythm analysis
        rhythm = self.rhythm_analyzer.analyze_tempo(y, sr)
        features.update(rhythm)
        
        # Harmonic analysis
        harmonic = self.harmonic_analyzer.detect_key(y, sr)
        features['harmonic_complexity'] = self.harmonic_analyzer.compute_harmonic_complexity(y, sr)
        features.update(harmonic)
        
        # Structure analysis
        structure = self.structure_analyzer.segment_song(y, sr)
        transition_points = self.structure_analyzer.find_transition_points(
            structure['sections'], 
            np.array(rhythm['beats'])
        )
        features.update(structure)
        features.update(transition_points)
        
        # Energy analysis
        energy = self.energy_analyzer.analyze_energy(y, sr)
        features.update(energy)
        
        logger.info(f"Completed: {filepath.name} - {features['tempo']:.1f} BPM, {features['full_key']}")
        
        return features
    
    def batch_process(self, audio_dir: Path, db_session) -> int:
        """
        Process all audio files in directory
        """
        audio_files = list(audio_dir.glob("*.mp3")) + list(audio_dir.glob("*.flac"))
        
        logger.info(f"Found {len(audio_files)} audio files")
        
        processed = 0
        for filepath in audio_files:
            try:
                features = self.process_song(filepath)
                # Save to database
                self._save_to_db(features, db_session)
                processed += 1
            except Exception as e:
                logger.error(f"Failed to process {filepath}: {e}")
        
        logger.info(f"Successfully processed {processed}/{len(audio_files)} songs")
        return processed
```

---

## Database Design

### Database Schema

**Technology**: PostgreSQL 15+ with JSONB support for flexible feature storage

```sql
-- Songs table: core metadata and analysis results
CREATE TABLE songs (
    id SERIAL PRIMARY KEY,
    filepath VARCHAR(512) UNIQUE NOT NULL,
    filename VARCHAR(256) NOT NULL,
    title VARCHAR(256),  -- Extracted from metadata or manual
    artist VARCHAR(256) DEFAULT 'Stream of Praise',
    album VARCHAR(256),
    duration FLOAT NOT NULL,
    
    -- Rhythm features
    tempo FLOAT NOT NULL,
    tempo_confidence VARCHAR(20),
    beats JSONB,  -- Array of beat timestamps
    downbeats JSONB,  -- Array of downbeat timestamps
    num_beats INTEGER,
    
    -- Harmonic features
    key VARCHAR(10) NOT NULL,  -- e.g., 'C', 'D#'
    mode VARCHAR(10) NOT NULL,  -- 'major' or 'minor'
    camelot VARCHAR(5),  -- e.g., '8A', '12B'
    harmonic_complexity FLOAT,
    
    -- Structure features
    sections JSONB NOT NULL,  -- Array of {label, start, end, duration}
    num_sections INTEGER,
    transition_in_time FLOAT,
    transition_out_time FLOAT,
    intro_duration FLOAT,
    outro_duration FLOAT,
    
    -- Energy features
    loudness_db FLOAT,
    dynamic_range_db FLOAT,
    spectral_centroid_mean FLOAT,
    rms_mean FLOAT,
    
    -- Additional metadata
    raw_features JSONB,  -- Full feature dump for debugging
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Indexing for fast queries
    INDEX idx_tempo (tempo),
    INDEX idx_key (key, mode),
    INDEX idx_camelot (camelot),
    INDEX idx_duration (duration)
);

-- Song compatibility matrix
CREATE TABLE song_compatibility (
    id SERIAL PRIMARY KEY,
    song_a_id INTEGER REFERENCES songs(id) ON DELETE CASCADE,
    song_b_id INTEGER REFERENCES songs(id) ON DELETE CASCADE,
    
    -- Compatibility scores (0-100)
    tempo_score FLOAT NOT NULL,  -- How close are tempos
    key_score FLOAT NOT NULL,    -- Harmonic compatibility
    energy_score FLOAT NOT NULL, -- Energy level matching
    structure_score FLOAT NOT NULL,  -- Transition point quality
    overall_score FLOAT NOT NULL,    -- Weighted average
    
    -- Transition metadata
    recommended_crossfade_duration FLOAT,  -- In seconds
    tempo_adjustment_needed BOOLEAN,
    pitch_shift_semitones INTEGER,  -- 0 if no shift needed
    
    -- Caching
    transition_cached BOOLEAN DEFAULT FALSE,
    transition_filepath VARCHAR(512),
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Constraints
    UNIQUE(song_a_id, song_b_id),
    CHECK(song_a_id != song_b_id),
    
    -- Indexing
    INDEX idx_song_a (song_a_id),
    INDEX idx_song_b (song_b_id),
    INDEX idx_overall_score (overall_score DESC)
);

-- Playlists
CREATE TABLE playlists (
    id SERIAL PRIMARY KEY,
    name VARCHAR(256) NOT NULL,
    description TEXT,
    target_duration INTEGER,  -- Target duration in minutes
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Playlist items (ordered)
CREATE TABLE playlist_items (
    id SERIAL PRIMARY KEY,
    playlist_id INTEGER REFERENCES playlists(id) ON DELETE CASCADE,
    song_id INTEGER REFERENCES songs(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,  -- Order in playlist
    transition_type VARCHAR(50) DEFAULT 'auto',  -- 'auto', 'manual', 'silence'
    
    UNIQUE(playlist_id, position),
    INDEX idx_playlist (playlist_id, position)
);

-- Playback sessions (for analytics)
CREATE TABLE playback_sessions (
    id SERIAL PRIMARY KEY,
    playlist_id INTEGER REFERENCES playlists(id),
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    total_duration INTEGER,  -- Actual playback duration in seconds
    songs_played INTEGER,
    transitions_count INTEGER,
    user_interventions INTEGER DEFAULT 0  -- Manual skips/adjustments
);

-- Playback history (song-level)
CREATE TABLE playback_history (
    id SERIAL PRIMARY KEY,
    session_id INTEGER REFERENCES playback_sessions(id) ON DELETE CASCADE,
    song_id INTEGER REFERENCES songs(id),
    started_at TIMESTAMP NOT NULL,
    ended_at TIMESTAMP,
    transition_quality_rating INTEGER,  -- Optional user rating 1-5
    
    INDEX idx_session (session_id)
);
```

### SQLAlchemy Models

**Module**: `database.models`

```python
from sqlalchemy import Column, Integer, String, Float, Boolean, TIMESTAMP, ForeignKey, JSON, Index
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class Song(Base):
    __tablename__ = 'songs'
    
    id = Column(Integer, primary_key=True)
    filepath = Column(String(512), unique=True, nullable=False)
    filename = Column(String(256), nullable=False)
    title = Column(String(256))
    artist = Column(String(256), default='Stream of Praise')
    album = Column(String(256))
    duration = Column(Float, nullable=False)
    
    # Rhythm
    tempo = Column(Float, nullable=False)
    tempo_confidence = Column(String(20))
    beats = Column(JSON)
    downbeats = Column(JSON)
    num_beats = Column(Integer)
    
    # Harmonic
    key = Column(String(10), nullable=False)
    mode = Column(String(10), nullable=False)
    camelot = Column(String(5))
    harmonic_complexity = Column(Float)
    
    # Structure
    sections = Column(JSON, nullable=False)
    num_sections = Column(Integer)
    transition_in_time = Column(Float)
    transition_out_time = Column(Float)
    intro_duration = Column(Float)
    outro_duration = Column(Float)
    
    # Energy
    loudness_db = Column(Float)
    dynamic_range_db = Column(Float)
    spectral_centroid_mean = Column(Float)
    rms_mean = Column(Float)
    
    # Metadata
    raw_features = Column(JSON)
    processed_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    compatibilities_a = relationship("SongCompatibility", 
                                    foreign_keys="SongCompatibility.song_a_id",
                                    back_populates="song_a")
    compatibilities_b = relationship("SongCompatibility",
                                    foreign_keys="SongCompatibility.song_b_id",
                                    back_populates="song_b")
    
    __table_args__ = (
        Index('idx_tempo', 'tempo'),
        Index('idx_key', 'key', 'mode'),
        Index('idx_camelot', 'camelot'),
        Index('idx_duration', 'duration'),
    )

class SongCompatibility(Base):
    __tablename__ = 'song_compatibility'
    
    id = Column(Integer, primary_key=True)
    song_a_id = Column(Integer, ForeignKey('songs.id', ondelete='CASCADE'))
    song_b_id = Column(Integer, ForeignKey('songs.id', ondelete='CASCADE'))
    
    tempo_score = Column(Float, nullable=False)
    key_score = Column(Float, nullable=False)
    energy_score = Column(Float, nullable=False)
    structure_score = Column(Float, nullable=False)
    overall_score = Column(Float, nullable=False)
    
    recommended_crossfade_duration = Column(Float)
    tempo_adjustment_needed = Column(Boolean)
    pitch_shift_semitones = Column(Integer)
    
    transition_cached = Column(Boolean, default=False)
    transition_filepath = Column(String(512))
    
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    
    # Relationships
    song_a = relationship("Song", foreign_keys=[song_a_id])
    song_b = relationship("Song", foreign_keys=[song_b_id])
    
    __table_args__ = (
        Index('idx_song_a', 'song_a_id'),
        Index('idx_song_b', 'song_b_id'),
        Index('idx_overall_score', 'overall_score'),
    )
```

### Compatibility Scoring Algorithm

**Module**: `analysis.compatibility`

```python
class CompatibilityAnalyzer:
    """Calculate compatibility scores between songs"""
    
    def calculate_tempo_score(self, tempo_a: float, tempo_b: float) -> float:
        """
        Score tempo compatibility (0-100)
        - Within 5% = 100
        - Within 10% = 80
        - Within 15% = 60
        - Beyond 20% = 0
        """
        diff_pct = abs(tempo_a - tempo_b) / max(tempo_a, tempo_b)
        
        if diff_pct < 0.05:
            return 100.0
        elif diff_pct < 0.10:
            return 100 - (diff_pct - 0.05) * 400  # Linear 100->80
        elif diff_pct < 0.15:
            return 80 - (diff_pct - 0.10) * 400   # Linear 80->60
        elif diff_pct < 0.20:
            return 60 - (diff_pct - 0.15) * 1200  # Linear 60->0
        else:
            return 0.0
    
    def calculate_key_score(self, camelot_a: str, camelot_b: str) -> float:
        """
        Score key compatibility using Camelot wheel
        - Same key = 100
        - Adjacent (±1 or major/minor swap) = 80
        - ±2 on wheel = 50
        - Further = 0
        """
        if camelot_a == camelot_b:
            return 100.0
        
        # Extract number and letter
        num_a, letter_a = int(camelot_a[:-1]), camelot_a[-1]
        num_b, letter_b = int(camelot_b[:-1]), camelot_b[-1]
        
        # Same number, different letter (relative major/minor)
        if num_a == num_b and letter_a != letter_b:
            return 80.0
        
        # Adjacent numbers, same letter
        if letter_a == letter_b:
            diff = min(abs(num_a - num_b), 12 - abs(num_a - num_b))
            if diff == 1:
                return 80.0
            elif diff == 2:
                return 50.0
        
        return 0.0
    
    def calculate_energy_score(self, song_a: Song, song_b: Song) -> float:
        """
        Score energy level compatibility
        Based on loudness and spectral centroid
        """
        loudness_diff = abs(song_a.loudness_db - song_b.loudness_db)
        centroid_diff = abs(song_a.spectral_centroid_mean - song_b.spectral_centroid_mean)
        
        # Normalize differences
        loudness_score = max(0, 100 - loudness_diff * 5)  # 5 dB diff = 75
        centroid_score = max(0, 100 - centroid_diff / 50)  # Arbitrary scaling
        
        return (loudness_score + centroid_score) / 2
    
    def calculate_structure_score(self, song_a: Song, song_b: Song) -> float:
        """
        Score based on transition point quality
        - Both have clean outros/intros = 100
        - One has clean transition point = 75
        - Neither has clean point = 50
        """
        a_has_outro = song_a.outro_duration > 2.0
        b_has_intro = song_b.intro_duration > 2.0
        
        if a_has_outro and b_has_intro:
            return 100.0
        elif a_has_outro or b_has_intro:
            return 75.0
        else:
            return 50.0
    
    def calculate_overall_score(self, tempo: float, key: float, 
                                energy: float, structure: float) -> float:
        """
        Weighted combination of individual scores
        Weights: tempo=35%, key=35%, energy=20%, structure=10%
        """
        return (tempo * 0.35 + key * 0.35 + energy * 0.20 + structure * 0.10)
    
    def analyze_pair(self, song_a: Song, song_b: Song) -> SongCompatibility:
        """
        Compute all compatibility metrics for a song pair
        """
        tempo_score = self.calculate_tempo_score(song_a.tempo, song_b.tempo)
        key_score = self.calculate_key_score(song_a.camelot, song_b.camelot)
        energy_score = self.calculate_energy_score(song_a, song_b)
        structure_score = self.calculate_structure_score(song_a, song_b)
        overall_score = self.calculate_overall_score(
            tempo_score, key_score, energy_score, structure_score
        )
        
        # Determine recommended crossfade duration
        tempo_diff_pct = abs(song_a.tempo - song_b.tempo) / song_a.tempo
        if tempo_diff_pct < 0.05:
            crossfade_duration = 8.0  # 8 seconds for similar tempos
        elif tempo_diff_pct < 0.15:
            crossfade_duration = 12.0  # 12 seconds for moderate difference
        else:
            crossfade_duration = 16.0  # 16 seconds for large difference
        
        # Determine if pitch shift needed
        # Extract semitone distance from Camelot codes
        pitch_shift = self._calculate_pitch_shift(song_a.camelot, song_b.camelot)
        
        return SongCompatibility(
            song_a_id=song_a.id,
            song_b_id=song_b.id,
            tempo_score=tempo_score,
            key_score=key_score,
            energy_score=energy_score,
            structure_score=structure_score,
            overall_score=overall_score,
            recommended_crossfade_duration=crossfade_duration,
            tempo_adjustment_needed=(tempo_diff_pct > 0.05),
            pitch_shift_semitones=pitch_shift
        )
```

---

## Runtime System

### Module 1: Playlist Generator

**Module**: `runtime.playlist_generator`

```python
class PlaylistGenerator:
    """Generate intelligent playlists based on compatibility"""
    
    def __init__(self, db_session):
        self.db = db_session
    
    def generate_playlist(self, target_duration_minutes: int, 
                         start_song_id: Optional[int] = None,
                         tempo_progression: str = 'wave') -> list[int]:
        """
        Generate playlist with specified duration and progression pattern
        
        Args:
            target_duration_minutes: Target playlist length
            start_song_id: Optional starting song
            tempo_progression: 'wave' (slow->fast->slow), 'ascending', 'descending', 'stable'
        
        Returns:
            List of song IDs in playback order
        """
        target_seconds = target_duration_minutes * 60
        
        # Select starting song
        if start_song_id:
            current_song = self.db.query(Song).get(start_song_id)
        else:
            # Random slow song to start
            current_song = self.db.query(Song).filter(
                Song.tempo.between(70, 90)
            ).order_by(func.random()).first()
        
        playlist = [current_song.id]
        total_duration = current_song.duration
        
        # Generate progression pattern
        tempo_targets = self._generate_tempo_progression(
            tempo_progression, 
            target_duration_minutes
        )
        
        while total_duration < target_seconds:
            # Find next compatible song
            next_song = self._find_next_song(
                current_song,
                playlist,  # Avoid repeats
                tempo_targets[len(playlist) % len(tempo_targets)]
            )
            
            if not next_song:
                break
            
            playlist.append(next_song.id)
            total_duration += next_song.duration
            current_song = next_song
        
        return playlist
    
    def _generate_tempo_progression(self, pattern: str, 
                                    duration_minutes: int) -> list[float]:
        """
        Generate target tempo sequence
        """
        if pattern == 'wave':
            # Slow -> medium -> fast -> medium -> slow cycle
            return [75, 90, 105, 120, 135, 120, 105, 90, 75]
        elif pattern == 'ascending':
            return list(range(70, 140, 10))
        elif pattern == 'descending':
            return list(range(135, 65, -10))
        else:  # stable
            return [95] * 20  # Medium tempo throughout
    
    def _find_next_song(self, current_song: Song, 
                       used_songs: list[int],
                       target_tempo: float) -> Optional[Song]:
        """
        Find best next song based on compatibility and tempo target
        """
        # Query compatible songs
        compatible = self.db.query(Song, SongCompatibility).join(
            SongCompatibility,
            SongCompatibility.song_b_id == Song.id
        ).filter(
            SongCompatibility.song_a_id == current_song.id,
            SongCompatibility.overall_score > 60,  # Minimum quality threshold
            ~Song.id.in_(used_songs)
        ).all()
        
        if not compatible:
            return None
        
        # Score candidates by tempo proximity to target
        scored = []
        for song, compat in compatible:
            tempo_proximity = 100 - abs(song.tempo - target_tempo)
            combined_score = compat.overall_score * 0.7 + tempo_proximity * 0.3
            scored.append((combined_score, song))
        
        # Return highest scoring song
        scored.sort(reverse=True, key=lambda x: x[0])
        return scored[0][1]
```

### Module 2: Transition Renderer

**Module**: `runtime.transition_renderer`

```python
class TransitionRenderer:
    """Render smooth transitions between songs"""
    
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(exist_ok=True)
    
    def render_transition(self, song_a: Song, song_b: Song,
                         compatibility: SongCompatibility) -> Path:
        """
        Render transition segment between two songs
        
        Returns path to rendered transition audio file
        """
        # Check cache first
        if compatibility.transition_cached:
            cached_path = Path(compatibility.transition_filepath)
            if cached_path.exists():
                return cached_path
        
        # Load audio files
        y_a, sr_a = librosa.load(song_a.filepath, sr=44100, mono=False)
        y_b, sr_b = librosa.load(song_b.filepath, sr=44100, mono=False)
        
        # Extract transition segments
        transition_out_samples = int(song_a.transition_out_time * sr_a)
        transition_in_samples = int(song_b.transition_in_time * sr_b)
        
        crossfade_samples = int(compatibility.recommended_crossfade_duration * sr_a)
        
        # Get outro of song A (last N seconds before transition point)
        outro_start = max(0, transition_out_samples - crossfade_samples)
        outro = y_a[:, outro_start:transition_out_samples]
        
        # Get intro of song B (from transition point onward)
        intro = y_b[:, transition_in_samples:transition_in_samples + crossfade_samples]
        
        # Apply tempo stretching if needed
        if compatibility.tempo_adjustment_needed:
            # Stretch song B's intro to match song A's tempo
            stretch_rate = song_a.tempo / song_b.tempo
            intro = pyrubberband.time_stretch(intro, sr_b, stretch_rate)
        
        # Apply pitch shift if needed
        if compatibility.pitch_shift_semitones != 0:
            intro = librosa.effects.pitch_shift(
                intro, 
                sr=sr_b, 
                n_steps=compatibility.pitch_shift_semitones
            )
        
        # Equal-power crossfade
        fade_curve = np.linspace(0, 1, crossfade_samples)
        fade_out = np.cos(fade_curve * np.pi / 2) ** 2
        fade_in = np.sin(fade_curve * np.pi / 2) ** 2
        
        # Apply fades
        outro_faded = outro * fade_out
        intro_faded = intro * fade_in
        
        # Mix
        min_len = min(outro_faded.shape[1], intro_faded.shape[1])
        transition_mix = outro_faded[:, :min_len] + intro_faded[:, :min_len]
        
        # Save to cache
        output_filename = f"transition_{song_a.id}_to_{song_b.id}.flac"
        output_path = self.cache_dir / output_filename
        
        sf.write(output_path, transition_mix.T, sr_a)
        
        # Update database
        compatibility.transition_cached = True
        compatibility.transition_filepath = str(output_path)
        
        return output_path
    
    def prerender_high_priority(self, db_session, limit: int = 100):
        """
        Pre-render transitions for most common song pairs
        """
        # Find highest scoring compatibilities without cached transitions
        to_render = db_session.query(SongCompatibility).filter(
            SongCompatibility.transition_cached == False,
            SongCompatibility.overall_score > 70
        ).order_by(
            SongCompatibility.overall_score.desc()
        ).limit(limit).all()
        
        for compat in to_render:
            song_a = db_session.query(Song).get(compat.song_a_id)
            song_b = db_session.query(Song).get(compat.song_b_id)
            
            self.render_transition(song_a, song_b, compat)
            db_session.commit()
```

### Module 3: Playback Engine

**Module**: `runtime.playback_engine`

```python
class PlaybackEngine:
    """Real-time audio playback with transitions"""
    
    def __init__(self, db_session, transition_renderer: TransitionRenderer):
        self.db = db_session
        self.renderer = transition_renderer
        self.current_song_idx = 0
        self.playlist = []
        self.is_playing = False
        self.stream = None
    
    def load_playlist(self, song_ids: list[int]):
        """Load playlist for playback"""
        self.playlist = song_ids
        self.current_song_idx = 0
    
    def play(self):
        """Start playback"""
        self.is_playing = True
        
        # Initialize audio stream
        self.stream = sd.OutputStream(
            samplerate=44100,
            channels=2,
            callback=self._audio_callback
        )
        self.stream.start()
        
        # Start playback thread
        threading.Thread(target=self._playback_loop, daemon=True).start()
    
    def _playback_loop(self):
        """Main playback loop"""
        while self.is_playing and self.current_song_idx < len(self.playlist):
            current_song_id = self.playlist[self.current_song_idx]
            
            # Check if there's a next song
            if self.current_song_idx + 1 < len(self.playlist):
                next_song_id = self.playlist[self.current_song_idx + 1]
                self._play_with_transition(current_song_id, next_song_id)
            else:
                # Last song, play to end
                self._play_song(current_song_id)
            
            self.current_song_idx += 1
    
    def _play_with_transition(self, current_id: int, next_id: int):
        """Play current song and transition to next"""
        current = self.db.query(Song).get(current_id)
        next_song = self.db.query(Song).get(next_id)
        
        # Get compatibility
        compat = self.db.query(SongCompatibility).filter(
            SongCompatibility.song_a_id == current_id,
            SongCompatibility.song_b_id == next_id
        ).first()
        
        # Load current song audio
        y_current, sr = librosa.load(current.filepath, sr=44100, mono=False)
        
        # Play until transition point
        transition_sample = int(current.transition_out_time * sr)
        self._stream_audio(y_current[:, :transition_sample])
        
        # Render and play transition
        transition_path = self.renderer.render_transition(current, next_song, compat)
        y_transition, sr = librosa.load(transition_path, sr=44100, mono=False)
        self._stream_audio(y_transition)
    
    def _stream_audio(self, audio_data: np.ndarray):
        """Stream audio data to output"""
        # Simplified - actual implementation would use queue-based streaming
        # to prevent blocking and enable smooth playback
        for i in range(0, audio_data.shape[1], 1024):
            if not self.is_playing:
                break
            chunk = audio_data[:, i:i+1024]
            # Write to audio buffer
            # (Actual implementation would use sounddevice callback system)
    
    def pause(self):
        """Pause playback"""
        self.is_playing = False
    
    def skip(self):
        """Skip to next song"""
        self.current_song_idx += 1
```

---

## System Integration

### Application Entry Point

**Module**: `main.py`

```python
from fastapi import FastAPI, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

app = FastAPI(title="Worship Music System")

# Database setup
engine = create_engine("postgresql://user:pass@localhost/worship_music")
SessionLocal = sessionmaker(bind=engine)

# Initialize components
pipeline = PreprocessingPipeline()
compatibility_analyzer = CompatibilityAnalyzer()
playlist_generator = PlaylistGenerator(SessionLocal())
transition_renderer = TransitionRenderer(Path("/tmp/transitions"))
playback_engine = PlaybackEngine(SessionLocal(), transition_renderer)

@app.post("/api/songs/upload")
async def upload_song(file: UploadFile):
    """Upload and process new song"""
    # Save file
    filepath = Path(f"/data/audio/{file.filename}")
    with open(filepath, "wb") as f:
        f.write(await file.read())
    
    # Process
    features = pipeline.process_song(filepath)
    
    # Save to DB
    db = SessionLocal()
    song = Song(**features)
    db.add(song)
    db.commit()
    
    return {"song_id": song.id}

@app.post("/api/playlists/generate")
async def generate_playlist(duration_minutes: int, pattern: str = "wave"):
    """Generate new playlist"""
    db = SessionLocal()
    song_ids = playlist_generator.generate_playlist(duration_minutes, tempo_progression=pattern)
    
    playlist = Playlist(
        name=f"Auto-generated {duration_minutes}min",
        target_duration=duration_minutes
    )
    db.add(playlist)
    db.commit()
    
    for idx, song_id in enumerate(song_ids):
        item = PlaylistItem(
            playlist_id=playlist.id,
            song_id=song_id,
            position=idx
        )
        db.add(item)
    db.commit()
    
    return {"playlist_id": playlist.id, "songs": song_ids}

@app.post("/api/playback/start/{playlist_id}")
async def start_playback(playlist_id: int):
    """Start playlist playback"""
    db = SessionLocal()
    items = db.query(PlaylistItem).filter(
        PlaylistItem.playlist_id == playlist_id
    ).order_by(PlaylistItem.position).all()
    
    song_ids = [item.song_id for item in items]
    playback_engine.load_playlist(song_ids)
    playback_engine.play()
    
    return {"status": "playing", "playlist_id": playlist_id}
```

### Data Flow Diagram

```
User Upload → Pre-Processing Pipeline → PostgreSQL
                                            ↓
User Request → Playlist Generator → Compatibility Lookup
                                            ↓
                              Transition Renderer → Cache
                                            ↓
                              Playback Engine → Audio Output
```

---

## POC Phase Plan

### POC Objectives

1. Validate audio analysis pipeline with 3-5 SOP songs
2. Test tempo/key detection accuracy
3. Verify structure segmentation quality
4. Prototype single transition rendering
5. Establish baseline for full system development

### POC Song Selection

Select 3-5 SOP songs with varying characteristics:

1. **Slow worship** (e.g., "展開清晨的翅膀" - ~69 BPM, ballad)
2. **Medium worship** (e.g., "全然向祢" - ~94 BPM, standard worship)
3. **Fast praise** (e.g., "讓讚美飛揚" - ~135 BPM, upbeat)
4. **Different key** (ensure not all same key for compatibility testing)
5. **(Optional) 5th song** for extended transition testing

### POC Implementation (Jupyter Notebook)

**Notebook**: `notebooks/01_POC_Analysis.ipynb`

#### Cell 1: Setup and Imports

```python
# Install required packages (run once)
# !pip install librosa madmom soundfile matplotlib pandas

import librosa
import librosa.display
import madmom
import soundfile as sf
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
import json

# Configuration
AUDIO_DIR = Path("./poc_audio")  # Place 3-5 SOP songs here
OUTPUT_DIR = Path("./poc_output")
OUTPUT_DIR.mkdir(exist_ok=True)

# List available songs
audio_files = list(AUDIO_DIR.glob("*.mp3")) + list(AUDIO_DIR.glob("*.flac"))
print(f"Found {len(audio_files)} audio files:")
for f in audio_files:
    print(f"  - {f.name}")
```

#### Cell 2: Feature Extraction Function

```python
def analyze_song(filepath):
    """Run complete analysis on a single song"""
    print(f"\n{'='*60}")
    print(f"Analyzing: {filepath.name}")
    print(f"{'='*60}")
    
    # Load audio
    y, sr = librosa.load(filepath, sr=22050, mono=True)
    duration = librosa.get_duration(y=y, sr=sr)
    print(f"Duration: {duration:.1f}s")
    
    # Tempo detection
    tempo_librosa, beats = librosa.beat.beat_track(y=y, sr=sr, start_bpm=80)
    print(f"Tempo (librosa): {tempo_librosa:.1f} BPM")
    
    # Key detection
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    chroma_avg = np.mean(chroma, axis=1)
    
    keys = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 
                              2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    
    correlations = []
    for shift in range(12):
        corr = np.corrcoef(chroma_avg, np.roll(major_profile, shift))[0, 1]
        correlations.append((keys[shift], corr))
    
    best_key = max(correlations, key=lambda x: x[1])
    print(f"Key: {best_key[0]} major (confidence: {best_key[1]:.3f})")
    
    # Energy analysis
    rms = librosa.feature.rms(y=y)[0]
    rms_db = librosa.amplitude_to_db(rms)
    print(f"Average loudness: {np.mean(rms_db):.1f} dB")
    
    # Structure segmentation
    chroma_seg = librosa.feature.chroma_cqt(y=y, sr=sr)
    rec_matrix = librosa.segment.recurrence_matrix(chroma_seg, mode='affinity')
    
    # Detect boundaries
    novelty = librosa.segment.timelag_filter(rec_matrix)
    peaks = librosa.util.peak_pick(novelty, pre_max=5, post_max=5, 
                                    pre_avg=5, post_avg=5, delta=0.1, wait=10)
    boundary_times = librosa.frames_to_time(peaks, sr=sr)
    
    print(f"Detected {len(boundary_times)} section boundaries")
    
    # Return comprehensive results
    return {
        'filename': filepath.name,
        'duration': duration,
        'tempo': tempo_librosa,
        'key': best_key[0],
        'key_confidence': best_key[1],
        'loudness_db': float(np.mean(rms_db)),
        'num_sections': len(boundary_times),
        'boundary_times': boundary_times.tolist(),
        'beats': beats.tolist()[:50],  # First 50 beats for inspection
        # Store for visualization
        '_y': y,
        '_sr': sr,
        '_chroma': chroma,
        '_rms': rms
    }
```

#### Cell 3: Analyze All Songs

```python
# Analyze each song
results = []

for audio_file in audio_files:
    try:
        result = analyze_song(audio_file)
        results.append(result)
    except Exception as e:
        print(f"ERROR processing {audio_file.name}: {e}")

# Create summary dataframe
df = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith('_')} 
                   for r in results])
print("\n" + "="*60)
print("SUMMARY TABLE")
print("="*60)
print(df[['filename', 'duration', 'tempo', 'key', 'loudness_db', 'num_sections']])
```

#### Cell 4: Visualizations

```python
# Visualize each song's structure
fig, axes = plt.subplots(len(results), 3, figsize=(15, 5*len(results)))

for idx, result in enumerate(results):
    y = result['_y']
    sr = result['_sr']
    chroma = result['_chroma']
    rms = result['_rms']
    
    # Waveform
    ax = axes[idx, 0] if len(results) > 1 else axes[0]
    librosa.display.waveshow(y, sr=sr, ax=ax)
    ax.set_title(f"{result['filename']} - Waveform")
    ax.set_xlabel("Time (s)")
    
    # Chromagram
    ax = axes[idx, 1] if len(results) > 1 else axes[1]
    img = librosa.display.specshow(chroma, sr=sr, x_axis='time', y_axis='chroma', ax=ax)
    ax.set_title(f"Chromagram - Key: {result['key']}")
    plt.colorbar(img, ax=ax)
    
    # RMS Energy
    ax = axes[idx, 2] if len(results) > 1 else axes[2]
    times = librosa.times_like(rms, sr=sr)
    ax.plot(times, rms)
    ax.set_title(f"Energy - {result['tempo']:.1f} BPM")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("RMS")

plt.tight_layout()
plt.savefig(OUTPUT_DIR / "poc_analysis_visualizations.png", dpi=150)
plt.show()
```

#### Cell 5: Compatibility Analysis

```python
def calculate_compatibility(song_a, song_b):
    """Calculate compatibility scores between two songs"""
    # Tempo compatibility
    tempo_diff_pct = abs(song_a['tempo'] - song_b['tempo']) / song_a['tempo']
    if tempo_diff_pct < 0.05:
        tempo_score = 100
    elif tempo_diff_pct < 0.10:
        tempo_score = 80
    elif tempo_diff_pct < 0.15:
        tempo_score = 60
    else:
        tempo_score = 30
    
    # Key compatibility (simplified - same key or relative)
    if song_a['key'] == song_b['key']:
        key_score = 100
    else:
        # Check for common compatible keys
        compatible_keys = {
            'C': ['G', 'F', 'A'],
            'G': ['C', 'D', 'E'],
            'D': ['G', 'A', 'B'],
            # ... simplified for POC
        }
        if song_b['key'] in compatible_keys.get(song_a['key'], []):
            key_score = 80
        else:
            key_score = 40
    
    # Energy compatibility
    energy_diff = abs(song_a['loudness_db'] - song_b['loudness_db'])
    energy_score = max(0, 100 - energy_diff * 5)
    
    # Overall score (weighted)
    overall = tempo_score * 0.4 + key_score * 0.4 + energy_score * 0.2
    
    return {
        'song_a': song_a['filename'],
        'song_b': song_b['filename'],
        'tempo_score': tempo_score,
        'key_score': key_score,
        'energy_score': energy_score,
        'overall_score': overall
    }

# Calculate all pairwise compatibilities
compatibilities = []
for i, song_a in enumerate(results):
    for j, song_b in enumerate(results):
        if i < j:  # Avoid duplicates
            compat = calculate_compatibility(song_a, song_b)
            compatibilities.append(compat)

compat_df = pd.DataFrame(compatibilities)
print("\n" + "="*60)
print("COMPATIBILITY MATRIX")
print("="*60)
print(compat_df.sort_values('overall_score', ascending=False))

# Save results
compat_df.to_csv(OUTPUT_DIR / "poc_compatibility_scores.csv", index=False)
```

#### Cell 6: Simple Transition Prototype

```python
def create_simple_crossfade(song_a_path, song_b_path, crossfade_duration=8.0):
    """Create a simple equal-power crossfade between two songs"""
    # Load stereo audio
    y_a, sr = librosa.load(song_a_path, sr=44100, mono=False)
    y_b, sr = librosa.load(song_b_path, sr=44100, mono=False)
    
    crossfade_samples = int(crossfade_duration * sr)
    
    # Take last N seconds of song A
    outro = y_a[:, -crossfade_samples:]
    
    # Take first N seconds of song B  
    intro = y_b[:, :crossfade_samples]
    
    # Create equal-power fade curves
    fade_out = np.linspace(1, 0, crossfade_samples) ** 0.5
    fade_in = np.linspace(0, 1, crossfade_samples) ** 0.5
    
    # Apply fades
    outro_faded = outro * fade_out
    intro_faded = intro * fade_in
    
    # Mix
    transition = outro_faded + intro_faded
    
    return transition, sr

# Test transition between most compatible pair
best_pair = compat_df.iloc[0]
print(f"\nCreating transition between:")
print(f"  {best_pair['song_a']} -> {best_pair['song_b']}")
print(f"  Overall compatibility: {best_pair['overall_score']:.1f}")

song_a_path = AUDIO_DIR / best_pair['song_a']
song_b_path = AUDIO_DIR / best_pair['song_b']

transition, sr = create_simple_crossfade(song_a_path, song_b_path, crossfade_duration=10.0)

# Save transition
output_path = OUTPUT_DIR / f"transition_{best_pair['song_a']}_to_{best_pair['song_b']}.flac"
sf.write(output_path, transition.T, sr)
print(f"\nSaved transition to: {output_path}")

# Visualize transition
plt.figure(figsize=(12, 4))
plt.plot(transition[0, :])  # Left channel
plt.title("Transition Waveform (10 second crossfade)")
plt.xlabel("Samples")
plt.ylabel("Amplitude")
plt.savefig(OUTPUT_DIR / "transition_waveform.png", dpi=150)
plt.show()
```

#### Cell 7: POC Summary and Next Steps

```python
print("\n" + "="*60)
print("POC SUMMARY")
print("="*60)

print(f"\nTotal songs analyzed: {len(results)}")
print(f"Tempo range: {df['tempo'].min():.1f} - {df['tempo'].max():.1f} BPM")
print(f"Keys detected: {df['key'].unique()}")
print(f"Average sections per song: {df['num_sections'].mean():.1f}")

print("\n" + "="*60)
print("OUTPUTS GENERATED")
print("="*60)
print(f"1. Analysis visualizations: {OUTPUT_DIR / 'poc_analysis_visualizations.png'}")
print(f"2. Compatibility matrix: {OUTPUT_DIR / 'poc_compatibility_scores.csv'}")
print(f"3. Sample transition: {OUTPUT_DIR / 'transition_*.flac'}")
print(f"4. Transition visualization: {OUTPUT_DIR / 'transition_waveform.png'}")

print("\n" + "="*60)
print("VALIDATION QUESTIONS")
print("="*60)
print("1. Do detected tempos match manual count?")
print("2. Are detected keys accurate to sheet music?")
print("3. Does the transition sound smooth and natural?")
print("4. Are section boundaries musically meaningful?")

print("\n" + "="*60)
print("NEXT STEPS")
print("="*60)
print("1. Validate POC results with worship leaders")
print("2. Implement madmom beat tracking for improved accuracy")
print("3. Add Camelot wheel for proper key compatibility")
print("4. Build PostgreSQL database schema")
print("5. Develop full pre-processing pipeline")
print("6. Create transition renderer with tempo adjustment")
```

### POC Success Criteria

The POC is successful if:

1. **Tempo detection**: Within ±5 BPM of manual count for ≥80% of songs
2. **Key detection**: Matches sheet music or manual identification for ≥70% of songs
3. **Transition quality**: Crossfade sounds natural without jarring discontinuities
4. **Section boundaries**: At least 50% of detected boundaries align with verse/chorus changes

### POC Timeline

- **Day 1-2**: Collect 3-5 SOP songs, set up Jupyter environment
- **Day 3-4**: Run notebook analysis, validate results
- **Day 5**: Review with stakeholders, document findings
- **Day 6-7**: Iterate on analysis parameters based on feedback

---

## Implementation Timeline

### Phase 1: POC Validation (1 week)
- Set up Jupyter notebook environment
- Analyze 3-5 songs
- Validate tempo, key, structure detection
- Generate sample transitions
- Document accuracy and limitations

### Phase 2: Core Infrastructure (2 weeks)
- PostgreSQL database setup with schema
- SQLAlchemy models and migrations
- Pre-processing pipeline modules
- Unit tests for feature extraction

### Phase 3: Batch Processing (1 week)
- Batch analysis of full SOP library (~400 songs)
- Compatibility matrix computation
- Database population
- Performance optimization

### Phase 4: Runtime System (2 weeks)
- Playlist generator with tempo progression
- Transition renderer with caching
- Basic playback engine
- Integration testing

### Phase 5: API and UI (2 weeks)
- FastAPI REST endpoints
- React frontend for playlist management
- Playback controls and visualization
- User testing

### Phase 6: Polish and Deploy (1 week)
- Performance tuning
- Documentation
- Docker containerization
- Production deployment

**Total estimated time**: 9 weeks for MVP

---

## Future Enhancements

### Short-term (3-6 months)

1. **Lyrical Analysis**
   - Integrate Whisper for Chinese lyric transcription
   - Semantic similarity for thematic matching
   - Auto-categorization into worship types (praise, thanksgiving, intercession)

2. **Advanced Transitions**
   - Stem separation with Demucs
   - Worship-specific mixing (pads, vocals, drums separately)
   - EQ adjustment for frequency balance

3. **User Feedback Loop**
   - Rating system for transitions
   - Manual override and corrections
   - Machine learning to improve compatibility scoring

### Long-term (6-12 months)

1. **Live Worship Integration**
   - MIDI controller support for live transitions
   - Real-time tempo following (human drummer)
   - Ableton Link synchronization

2. **Multi-language Support**
   - English worship songs (Hillsong, Bethel)
   - Mixed language playlists
   - Cross-cultural thematic matching

3. **Cloud Service**
   - Multi-tenant SaaS platform
   - Shared song library
   - Collaborative playlist editing

---

## Appendix A: Technical References

### Libraries and Documentation

- **librosa**: https://librosa.org/doc/latest/
- **madmom**: https://madmom.readthedocs.io/
- **Essentia**: https://essentia.upf.edu/
- **PostgreSQL**: https://www.postgresql.org/docs/
- **FastAPI**: https://fastapi.tiangolo.com/

### Research Papers

1. Müller, M., et al. (2015). "Music Structure Analysis" - FMP Notebooks
2. Böck, S., et al. (2016). "madmom: A New Python Audio Signal Processing Library"
3. McFee, B., et al. (2015). "librosa: Audio and Music Signal Analysis in Python"

### Worship Music Resources

- Stream of Praise official: https://www.sop.org/
- SongBPM database: https://songbpm.com/@zan-mei-zhi-quan-stream-of-praise

---

## Appendix B: Environment Setup

### Python Environment

```bash
# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Jupyter for POC
pip install jupyterlab ipykernel matplotlib

# Launch Jupyter
jupyter lab
```

### PostgreSQL Setup

```bash
# Install PostgreSQL (Ubuntu)
sudo apt install postgresql postgresql-contrib

# Create database
sudo -u postgres createdb worship_music

# Create user
sudo -u postgres psql
CREATE USER worship_user WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE worship_music TO worship_user;
```

### Directory Structure

```
worship-music-system/
├── data/
│   ├── audio/           # Source audio files
│   ├── cache/           # Rendered transitions
│   └── exports/         # Playlist exports
├── notebooks/
│   └── 01_POC_Analysis.ipynb
├── src/
│   ├── preprocessing/
│   │   ├── audio_loader.py
│   │   ├── rhythm_analysis.py
│   │   ├── harmonic_analysis.py
│   │   ├── structure_analysis.py
│   │   └── pipeline.py
│   ├── analysis/
│   │   └── compatibility.py
│   ├── runtime/
│   │   ├── playlist_generator.py
│   │   ├── transition_renderer.py
│   │   └── playback_engine.py
│   ├── database/
│   │   ├── models.py
│   │   └── migrations/
│   └── api/
│       └── main.py
├── tests/
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

---

**End of Design Document**
