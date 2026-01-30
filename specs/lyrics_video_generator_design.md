# Lyrics Video Generator & Multi-Song Transition Tool - Design Specification

---

## 1. Overview & Goals

This document specifies the design for a **Lyrics Video Generator** and an expansion of the existing Transition Builder. This tool enables users to create worship song sets (playlists) containing multiple songs with smooth, gapless transitions, and automatically generate high-quality lyrics videos for the entire set.

### Core Goals
1.  **Multi-Song Support**: Expand beyond A-to-B transitions to support A-to-B-to-C... playlists of arbitrary length.
2.  **Gapless Playback**: Ensure zero "awkward silence" between songs using the transition logic established in `transition_builder_v2`.
3.  **Automated Video Generation**: Create YouTube-ready lyrics videos with:
    - Time-coded lyrics (synced via Whisper + Scraper data).
    - "Look-ahead" display (lyrics appear 1 beat before audio).
    - Dynamic Intro sequences.
    - Animated or static backgrounds.
4.  **Serverless Centralized Data**: A shared data architecture using Cloud Storage (S3/GCS) and index files, avoiding running database costs.

### Target Audience & Deployment
- **Primary User**: Personal tool, single-user focus.
- **Sharing Model**: Docker-based distribution for easy sharing with trusted collaborators.
- **Development**: Local Python/uv environment. Docker for production/sharing only.
- **Rendering Strategy**: Hybrid (local by default, optional cloud for 4K or batch exports).
- **Authentication**: None required initially (single-user/trusted sharing).
- **Data Paths**: Configured via `config.json` (same pattern as existing TUI).

---

## 2. System Architecture

The system consists of three distinct components:
1.  **Ingestion Pipeline (Admin)**: Runs offline. Analyzes audio, scrapes lyrics, generates LRC files, separates stems, and updates the central index.
2.  **Central Data Repository (Cloud)**: A static file structure hosting processed assets.
3.  **Client Application (User)**: The interactive TUI tool that syncs data, builds playlists, and renders outputs.

```mermaid
graph TD
    subgraph "Ingestion (Offline Batch)"
        RawAudio[Raw Audio] --> Analysis[Audio Analysis (AllInOne)]
        RawAudio --> Whisper[Whisper ASR]
        Scraper[Lyrics Scraper] --> TextAlignment[Text Alignment]
        Whisper --> TextAlignment
        Analysis --> CentralStore
        TextAlignment --> CentralStore
    end

    subgraph "Central Data (Object Storage)"
        CentralStore[Cloud Bucket]
        Index[Global Index JSON]
    end

    subgraph "Client Application (TUI)"
        Sync[Data Sync] --> LocalCache[Local Cache]
        CentralStore --> Sync
        LocalCache --> PlaylistBuilder[Playlist TUI]
        PlaylistBuilder --> AudioEng[Audio Engine]
        PlaylistBuilder --> VideoEng[Video Engine]
    end
```

---

## 3. The "Serverless" Database Strategy

To avoid database operation costs, we utilize a **Directory-as-Database** approach synchronized via simple file transfers.

**Cloud Strategy**:
- **Phase 1 (MVP)**: Local-only. All assets stored in local `data_cache/` directory.
- **Phase 2 (Future)**: Optional Cloudflare R2 sync. S3-compatible API, zero egress fees.
- **Abstraction**: Design sync layer with provider-agnostic interface to enable future cloud backends.

### 3.1 Cloud Directory Structure
```
cloud_bucket/
├── catalog_index.json         # Master index of all songs (lightweight)
├── assets/                    # Shared resources
│   ├── backgrounds/           # .mp4 loops or .jpg images
│   └── fonts/                 # .ttf files
└── songs/
    ├── {song_id}/             # song_id = pinyin_title_uniqueid
    │   ├── audio.mp3          # Original source audio (MP3 from source)
    │   ├── analysis.json      # Output from poc_analysis_allinone.py (BPM, key, sections, beats)
    │   ├── metadata.json      # LLM-generated: summary, themes, bible_verses, vocalist
    │   ├── lyrics.json        # Scraped metadata (composer, album, raw lyrics text)
    │   ├── lyrics.lrc         # Time-coded lyrics file (phrase-level)
    │   └── stems/             # Separated tracks (WAV format)
    │       ├── vocals.wav     # Kept during transitions
    │       ├── drums.wav      # Faded during transitions
    │       ├── bass.wav       # Faded during transitions
    │       └── other.wav      # Faded during transitions
```

### 3.2 Index Schema (`catalog_index.json`)
This file is downloaded by the client on startup to know what is available.
```json
{
  "last_updated": "2026-01-30T10:00:00Z",
  "version": "1.0",
  "songs": [
    {
      "id": "jiang_tian_chang_kai_209",
      "title": "將天敞開",
      "artist": "Stream of Praise",
      "bpm": 128.5,
      "key": "G",
      "duration": 245.0,
      "tempo_category": "fast",
      "vocalist": "mixed",
      "themes": ["Worship", "Presence", "Glory"],
      "bible_verses": ["Isaiah 6:1", "Revelation 4:8"],
      "ai_summary": "An energetic worship song about inviting God's presence and glory to descend, with themes of opening the heavens.",
      "has_stems": true,
      "has_lrc": true
    }
  ]
}
```

### 3.3 Client Sync Logic
1.  Client fetches `catalog_index.json`.
2.  Compares with local cache index.
3.  Identifies new or updated songs.
4.  Lazy-loading:
    - **Metadata**: Always downloaded immediately.
    - **Audio/Stems**: Downloaded only when the user adds the song to a playlist (to save bandwidth/disk).

---

## 4. Pre-processing: LRC Generation Pipeline

A new script `poc/generate_lrc.py` will handle the creation of high-quality time-coded lyrics.

### 4.1 Workflow

**Processing Model**: Batch/offline. All assets (LRC, stems, analysis) are pre-generated by admin and shared with users. Long processing times are acceptable (hours/days for full catalog).

**Lyric Source Policy**: Scraped lyrics are **required**. Songs without scraped lyrics are not added to the catalog. No fallback to Whisper-only transcription (too error-prone for worship context).

**LRC Granularity**: Phrase-level. Each LRC entry represents a phrase or clause (roughly half a lyric line). Provides good timing precision without word-level complexity.

1.  **ASR (Whisper)**: Run OpenAI Whisper (`v2-large` model) **locally** on the song audio to get a raw transcript with word-level timestamps. GPU recommended but CPU acceptable for batch processing.
2.  **Scraping**: Retrieve human-verified lyrics using `poc/lyrics_scraper.py`.
3.  **Validation**: If scraping fails, song is flagged and excluded from catalog until lyrics are manually added.
4.  **Alignment (LLM-based)**:
    - Use **GPT-4o-mini** via OpenRouter API to intelligently align scraped lyrics text with Whisper timestamps.
    - Input: Whisper word-level timestamps + scraped lyrics text.
    - Output: Phrase-level aligned LRC entries.
    - **Phrase Boundaries**: Derived from Whisper's natural pause detection. LLM maps scraped text to Whisper's detected phrase groupings.
    - LLM can handle Chinese text nuances, punctuation variations, and Whisper transcription errors.
    - **Fallback**: If LLM fails, flag song for manual review.
    - **Cost Estimate**: ~$0.01-0.02 per song (GPT-4o-mini pricing).
4.  **Beat Grid Integration**:
    - Load `analysis.json` to get the beat grid.
    - **Always** snap LRC timestamps to nearest beat/downbeat for musical precision.
5.  **Manual Review**: LRC files can be manually edited in any text editor if alignment is incorrect. No in-app editor required.
6.  **Output**: Write standard `.lrc` format.

### 4.2 LRC Format (Enhanced)
Standard LRC is `[mm:ss.xx] Lyric text`.
We may include metadata headers:
```lrc
[ti:將天敞開]
[ar:Stream of Praise]
[offset:0]
[00:12.50]將天敞開 你的榮耀降下來
[00:18.20]將天敞開 你的同在降下來
```

---

## 5. Application Expansion: Playlist TUI

The existing `transition_builder_v2` will be expanded with new screens for intelligent song discovery and playlist building.

### 5.1 New Screen: `SongDiscoveryScreen` (LLM-Assisted)

A chat-based interface for discovering songs using LLM + RAG.

**Workflow**:
1. User describes the worship set via chat (e.g., "I need 4 songs for a Sunday service about God's faithfulness, starting energetic and ending contemplative. Bible passage: Psalm 23.")
2. LLM analyzes the request and generates **shortlists** for each slot (Song 1, Song 2, Song 3, Song 4).
3. User sees the shortlists in a multi-column UI:
   - Each column represents a slot (Song 1, Song 2, etc.)
   - Each column shows **3 suggested songs**.
   - User can start playback of any song (Esc to stop). Same behavior as existing TUI.
4. User selects one song from each shortlist (or swaps columns, e.g., Song 2 suggestions ↔ Song 3 suggestions).
5. Finalized selection proceeds to `PlaylistScreen` for transition configuration.

**Chat Prompts Supported**:
- Theme/mood: "Praise songs about victory"
- Bible verses: "Songs based on Romans 8"
- Style pattern: "Fast → Medium → Slow → Slow"
- Singer preference: "Female vocalist preferred"
- Keywords: "Grace, mercy, salvation"

**Song Metadata for RAG**:
- **AI-generated summary**: One-sentence song summary (generated during ingestion).
- Full lyrics text (semantic search)
- Theme tags (Praise, Worship, Thanksgiving, Lament, Victory, etc.)
- Bible verse references
- Male/Female/Mixed vocalist
- Tempo (Fast/Medium/Slow derived from BPM)
- Key signature

**RAG Architecture**:
- No vector DB required. JSON index with song summaries.
- LLM performs search by reviewing all song summaries and reranking based on user query.
- For large catalogs (100+ songs), batch summaries into chunks for LLM context window.

**LLM Configuration**:
- Model is user-configurable (via config.json or environment variable).
- Default: GPT-4o-mini via OpenRouter.
- Supported: Any OpenAI-compatible API via OpenRouter.
- **Local Catalog Constraint**: LLM only suggests songs from the local catalog. Suggestions are filtered before display.

### 5.1.1 Screen Navigation

The TUI uses a **wizard-style flow**:
```
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│  Discovery      │  →   │   Playlist      │  →   │    Export       │
│  (Chat + Pick)  │      │  (Transitions)  │      │   (Audio/Video) │
└─────────────────┘      └─────────────────┘      └─────────────────┘
         ↑                        ↑
         └────────────────────────┘ (can go back)
```

- **Discovery → Playlist**: After selecting songs from shortlists, proceed to playlist.
- **Playlist → Export**: After configuring transitions, export audio or video.
- **Back navigation**: Can return to previous screens to modify selections.
- **Existing GenerationScreen**: Remains available for quick 2-song transitions (accessible from menu or hotkey).

### 5.2 New Screen: `PlaylistScreen`
Replaces the strict 2-song GenerationScreen layout for transition configuration.

**Layout Concept:**
```
┌──────────────────────────────────────────────────────────────┐
│ PLAYLIST BUILDER                                             │
│ Set Name: Sunday Service 2026-02-01 [Total: 18:45]           │
│                                                              │
│ ┌──────────────────────┐ ┌─────────────────────────────────┐ │
│ │ SONG LIBRARY         │ │ CURRENT PLAYLIST                │ │
│ │ [Search Filter...]   │ │ 1. Song A (Chorus)    4:30      │ │
│ │ > Song X             │ │    ↓ [Gap: -4 beats]            │ │
│ │   Song Y             │ │ 2. Song B (Verse)     3:45      │ │
│ │   Song Z             │ │    ↓ [Xfade: 8 beats]           │ │
│ │                      │ │ 3. Song C (Intro)     5:10      │ │
│ │                      │ │                                 │ │
│ └──────────────────────┘ └─────────────────────────────────┘ │
│                                                              │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ TRANSITION EDITOR (Selected Link: 1 -> 2)                │ │
│ │ Type: [Gap ▼]   Overlap: [4.0]   Section: [Chorus->Verse]│ │
│ └──────────────────────────────────────────────────────────┘ │
│                                                              │
│ FOOTER: A=Add  D=Delete  ↑/↓=Move  Space=Preview  e=Export Audio  E=Export Video │
└──────────────────────────────────────────────────────────────┘
```

### 5.3 Stem-Based Transition Logic

The playlist builder reuses the existing `TransitionGenerationService` from `transition_builder_v2`.

**Default Behavior**:
- **Vocals**: Always kept at 100% volume during transitions.
- **Other stems** (bass, drums, other): Faded using logarithmic (dB) curve.
- **Fade parameters**: Configurable per-link (same parameters as current TUI):
  - `gap_beats`: Silence duration in beats.
  - `fade_window_beats`: Total fade window (split: half fade-out, half fade-in).
  - `fade_bottom`: Minimum volume during fade (e.g., 0.33 = 33%).
  - `stems_to_fade`: Configurable list (default: `["bass", "drums", "other"]`).
  - Section boundary adjustments in beats.

### 5.4 CLI Interface

In addition to the TUI, a CLI interface will be provided for automation/scripting:

```bash
# Build playlist from JSON
python -m app.playlist build --from-json playlist.json --output set.ogg

# Export video from playlist
python -m app.playlist export-video --from-json playlist.json --background bg.mp4 --output set.mp4

# Validate playlist (check all assets exist)
python -m app.playlist validate --from-json playlist.json
```

### 5.5 Key Features
*   **Drag & Drop**: Reorder songs in the playlist (or move up/down keys).
*   **Section Selection**: User can select start/end sections for each song (e.g., "start from Chorus 2, end after Bridge"). Sections are derived from `analysis.json`.
*   **Multi-Link Editing**: Select the "link" between Song 1 and Song 2 to edit that specific transition. **Simple form-based editor** with beat-based parameters (not waveform editor).
*   **Global Preview**: **Audio-only** playback of the entire set. No real-time video preview in TUI (video is generated on export).
*   **Playlist Persistence**: Local JSON files in a `playlists/` directory. Git-friendly, no cloud sync.
*   **Playlist Size**: Designed for 5-10 songs (typical worship set). UI does not require pagination.
*   **Tempo/Key Adjustment**: Not supported. Songs play at their original tempo and key. Users should select songs with compatible tempos/keys.

---

## 6. Video Generation Engine

A new module `app/services/video_engine.py` will handle video creation.

### 6.1 Tech Stack
*   **ffmpeg-python**: Python wrapper for FFmpeg. More efficient than MoviePy for long videos.
*   **FFmpeg**: Underlying engine for rendering. Must be installed on system.
*   **PIL/Pillow**: For generating text overlay images (higher quality font rendering).
*   **Bundled Font**: Noto Sans TC bundled in `assets/fonts/` for consistent Chinese text rendering across systems.

### 6.2 Lyrics Timing Logic (The "Look-ahead")
The requirement is to show lyrics **1 beat before** the audio.
1.  **Load Data**: Get current audio time `t`, next lyric line time `t_lyric`, and BPM from `analysis.json`.
2.  **Calculate Offset**: `seconds_per_beat = 60 / BPM`.
3.  **Display Rule**: Display lyric line `L` at time `t_lyric - seconds_per_beat`.
4.  **Duration**: Keep displayed until `next_line_time - seconds_per_beat`.

### 6.2.1 Section Slicing & Instrumental Handling
*   **Section Slicing**: When a user selects only a portion of a song, slice the full LRC file using section boundaries from `analysis.json`. Map original timestamps to the new timeline offset.
*   **Instrumental Sections**: Display blank screen (background only, no lyrics) during intros, outros, and musical breaks. The current/next lyric lines simply fade out until the next vocal section.

### 6.3 Visual Composition
*   **Layer 1 (Background)**:
    *   **Scope**: One background per playlist (applies to entire set).
    *   **Supported Types**: Static images (.jpg/.png) or video loops (.mp4).
    *   Load background image/video from `assets/`.
    *   Video loops should seamlessly repeat; crossfade at loop point if needed.
    *   Apply slight dimming (opacity 0.7 black overlay) to ensure text readability.
*   **Layer 2 (Intro Sequence - 8 beats)**:
    *   Duration: Fixed at 8 beats (duration in seconds depends on first song's BPM).
    *   Fade in Title: "Worship Set" (or user defined).
    *   List Songs:
        1. Song A - Artist
        2. Song B - Artist
    *   Fade out as intro ends.
*   **Layer 2.5 (Song Metadata - Persistent)**:
    *   **Always Visible**: Small label in corner (e.g., top-left or bottom-right) showing current song title/artist.
    *   Style: Semi-transparent background pill, small font size (~24px at 1080p).
    *   Transition: Crossfade to new song info during song transitions.
*   **Layer 3 (Lyrics)**:
    *   **Display Mode**: Two lines - current line highlighted, next line shown dimmed below for context.
    *   **Highlighting**: Line-level only (no word-by-word karaoke effect). Entire line transitions at once.
    *   **Transition Behavior**: During song crossfades, use visual overlap - Song B lyrics appear below Song A lyrics during transition period.
    *   Position: Bottom center or centered.
    *   Font: Noto Sans TC (or similar Chinese-compatible font).
    *   Style: Current line: White text, black outline/shadow. Next line: Semi-transparent (50-70% opacity).
    *   Transition: Simple fade in/out (0.2s) between lines.

### 6.4 Output Specifications
*   **Export Keybindings**: `e` = export audio only (OGG), `E` = export lyrics video (MP4).
*   **Render UX**: Blocking with progress bar (percentage). TUI is unresponsive during render.
*   **Error Handling**: Fail fast. If any error occurs (missing file, corrupted data), abort immediately and show error message. No partial output saved.

**Audio Output (e)**:
*   **Format**: OGG Vorbis
*   **Quality**: High bitrate (~192kbps)

**Video Output (E)**:
*   **Container**: MP4 (YouTube-compatible)
*   **Video Codec**: H.264 (libx264)
*   **Audio Codec**: AAC (192kbps+)
*   **Resolution**: 1920x1080 (1080p) - fixed, not configurable.
*   **Frame Rate**: 30fps (sufficient for lyrics).

---

## 7. Implementation Plan

See `specs/lyrics_video_implementation_plan.md` for detailed implementation steps.

---

## 8. Directory Structure Updates

### 8.1 Data Storage Strategy

**User Data Location** (outside project directory):
- **macOS**: `~/Library/Application Support/StreamOfWorship/`
- **Linux**: `~/.local/share/stream_of_worship/` (XDG_DATA_HOME)
- **Cache**: `~/.cache/stream_of_worship/` (XDG_CACHE_HOME on Linux, ~/Library/Caches on macOS)

This follows platform conventions and keeps user data separate from application code.

```
# macOS Example
~/Library/Application Support/StreamOfWorship/
├── song_library/                       # Central song library
│   ├── catalog_index.json
│   └── songs/{song_id}/...
├── playlists/                          # User-created playlists
├── assets/                             # User assets (backgrounds, custom fonts)
│   └── backgrounds/
└── output/                             # Generated outputs
    ├── audio/
    └── video/

~/Library/Caches/StreamOfWorship/
└── whisper_cache/                      # Whisper model cache, temp files
```

### 8.2 Project Structure (Code Organization)

Reorganize the codebase to separate concerns:

```
stream_of_worship/
├── src/                                # Production source code
│   ├── stream_of_worship/              # Main package
│   │   ├── __init__.py
│   │   ├── cli/                        # CLI entry points
│   │   │   ├── __init__.py
│   │   │   └── main.py                 # Unified CLI (replaces transition_builder_v2)
│   │   ├── tui/                        # TUI application
│   │   │   ├── __init__.py
│   │   │   ├── app.py
│   │   │   ├── screens/
│   │   │   │   ├── discovery.py
│   │   │   │   ├── playlist.py
│   │   │   │   └── generation.py
│   │   │   ├── services/
│   │   │   │   ├── generation.py       # Transition generation
│   │   │   │   ├── video_engine.py     # Video rendering
│   │   │   │   ├── playback.py
│   │   │   │   └── llm_client.py
│   │   │   └── models/
│   │   │       ├── song.py
│   │   │       ├── playlist.py
│   │   │       └── transition.py
│   │   ├── ingestion/                  # Admin/ingestion tools (production)
│   │   │   ├── __init__.py
│   │   │   ├── audio_analysis.py       # Wrapper for allin1
│   │   │   ├── lyrics_scraper.py       # Lyrics scraping
│   │   │   ├── lrc_generator.py        # LRC generation (Whisper + LLM)
│   │   │   ├── metadata_generator.py   # AI metadata generation
│   │   │   └── stem_separator.py       # Audio stem separation
│   │   ├── core/                       # Shared core utilities
│   │   │   ├── __init__.py
│   │   │   ├── config.py               # Configuration management
│   │   │   ├── paths.py                # Platform-specific path resolution
│   │   │   └── catalog.py              # Song catalog management
│   │   └── assets/                     # Bundled assets (fonts, defaults)
│   │       └── fonts/
│   │           └── NotoSansTC-Bold.ttf
│   └── tests/                          # Production tests
│       ├── unit/
│       ├── integration/
│       └── conftest.py
├── poc/                                # POC/experimental scripts (NOT production)
│   ├── poc_analysis.py
│   ├── poc_analysis_allinone.py
│   └── poc_experiments/                # One-off experiments
├── scripts/                            # Admin/migration scripts
│   ├── migrate_song_library.py
│   └── build_index.py
├── docker/                             # Docker configurations
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── docker-compose.allinone.yml
├── docs/                               # Documentation
├── specs/                              # Design specifications
├── pyproject.toml
├── CLAUDE.md
└── README.md
```

### 8.3 Key Changes from Current Structure

1. **`transition_builder_v2/`** → **`src/stream_of_worship/tui/`**: TUI code moves into main package.
2. **`poc/`** remains for experimental code, but production scripts move to `src/stream_of_worship/ingestion/`.
3. **Data outside repo**: Song library, playlists, and outputs stored in OS-appropriate user directories.
4. **Bundled assets**: Only fonts and default configs bundled in package; user backgrounds stored externally.
5. **Unified CLI**: Single entry point `python -m stream_of_worship` with subcommands.

### 8.4 CLI Structure

```bash
# TUI (default)
python -m stream_of_worship

# Subcommands for ingestion (admin)
python -m stream_of_worship ingest analyze --song "song.mp3"
python -m stream_of_worship ingest scrape-lyrics --limit 10
python -m stream_of_worship ingest generate-lrc --song-id "song_123"
python -m stream_of_worship ingest generate-metadata --all

# Playlist operations (automation)
python -m stream_of_worship playlist build --from-json playlist.json --output set.ogg
python -m stream_of_worship playlist export-video --from-json playlist.json --output set.mp4

# Utility
python -m stream_of_worship config --show
python -m stream_of_worship migrate --from-legacy
```
