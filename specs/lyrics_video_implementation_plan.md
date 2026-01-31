# Implementation Plan - Lyrics Video Generator & Multi-Song Transition Tool

This plan outlines the steps to implement the Lyrics Video Generator and expand the Transition Builder to support multi-song playlists.

## MVP Strategy

**Approach**: Manual Selection MVP first, then add LLM Discovery.

- **MVP Scope**: Migration → LRC Generation → Playlist Screen (manual song selection) → Audio/Video Export (local-only).
- **Phase 2**: Add cloud sync (R2/S3) for centralized song catalog, multi-device access, and collaborator sharing.
- **Post-MVP**: Add LLM-powered SongDiscoveryScreen.
- **Priority**: LRC generation is the critical foundation for lyrics video.

---

## MVP Phase 1: Project Setup & Data Migration

1.  **Reorganize Project Structure**
    - Move `transition_builder_v2/app/` to `src/stream_of_worship/tui/`.
    - Create `src/stream_of_worship/ingestion/` for production ingestion tools.
    - Create `src/stream_of_worship/core/` for shared utilities.
    - Keep `poc/` for experimental scripts (not production).
    - Update `pyproject.toml` to use `src/` layout.

2.  **Update Dependencies** (`pyproject.toml`)
    - Add `video` extra: `ffmpeg-python`, `Pillow`, `numpy`.
    - Add `lrc_generation` extra: `openai-whisper`, `openai` (for OpenRouter API).
    - Bundle Noto Sans TC font in `src/stream_of_worship/assets/fonts/`.
    - Run `uv sync` to install.

3.  **Implement Platform-Specific Paths** (`src/stream_of_worship/core/paths.py`)
    - **macOS**: `~/Library/Application Support/StreamOfWorship/` for data, `~/Library/Caches/StreamOfWorship/` for cache.
    - **Linux**: `~/.local/share/stream_of_worship/` (XDG_DATA_HOME), `~/.cache/stream_of_worship/` (XDG_CACHE_HOME).
    - **Windows**: `%APPDATA%\StreamOfWorship\` for data, `%LOCALAPPDATA%\StreamOfWorship\cache\` for cache.
    - Create directories on first run.
    - Config: Allow override via environment variable `STREAM_OF_WORSHIP_DATA_DIR`.

4.  **Migrate Existing Data** (`scripts/migrate_song_library.py`)
    - Create per-song directories in user data location (`song_library/songs/`).
    - Extract individual song analyses from `poc_full_results.json` into per-song `analysis.json`.
    - Move scraped lyrics from `data/lyrics/songs/{song_id}.json` to per-song `lyrics.json`.
    - Move stems from `stems_output/{song_name}/` to per-song `stems/` directories.
    - Link or copy source audio files.
    - Generate `song_library/catalog_index.json`.

## MVP Phase 2: LRC Generation Pipeline (Priority)

3.  **Implement `poc/generate_lrc.py`**
    - **Step A**: Load audio and run `openai-whisper` (large-v3 model, local) to get word-level timestamps with natural phrase groupings. Prioritize accuracy over speed; expected ~1-2 min/song on CPU, ~10-15 sec on GPU.
    - **Step B**: Load "gold standard" text from `data/lyrics/` (scraped data).
    - **Step C**: Implement LLM-based alignment using GPT-4o-mini via OpenRouter:
      - Send Whisper output + scraped lyrics to LLM.
      - **Cost**: ~$0.01-0.02 per song. For 100 songs: $1-2 total. Cost is acceptable for batch ingestion.
      - Prompt LLM to align scraped text to Whisper timestamps at phrase level.
      - Handle edge cases (repeated phrases, ad-libs, etc.).
    - **Step D**: Apply beat-snap logic (using `analysis.json` beats) to always align timestamps to musical grid.
    - **Step E**: Export to `.lrc` format with phrase-level granularity.

3b. **Implement `poc/generate_song_metadata.py`**
    - **Input**: Scraped lyrics text, song title, artist.
    - **Output**: `metadata.json` containing:
      - `ai_summary`: One-sentence description of the song.
      - `themes`: List of theme tags (Praise, Worship, Thanksgiving, etc.).
      - `bible_verses`: List of related Bible references.
      - `vocalist`: "male", "female", or "mixed".
    - **LLM**: Use same configurable model as LRC generation.
    - **Batch Processing**: Run for all songs in catalog during ingestion.

3c. **Stem Separation Configuration**
    - **Tool**: Use Demucs (`htdemucs_6s` model) for high-quality stems.
    - **Priority**: Quality over speed; Demucs provides superior separation vs alternatives.
    - **Expected Time**: ~10-20 min/song on CPU, ~1-2 min on GPU.

## MVP Phase 3: TUI - Playlist Screen (Manual Selection)

4.  **Update Models & State**
    - Create `PlaylistItem` model in `app/models/playlist.py`:
      - `song_id`: str
      - `start_section`: int (index of first section to include)
      - `end_section`: int (index of last section to include)
      - `transition_to_next`: TransitionParams (gap, overlap, etc.)
    - Update `AppState` in `app/state.py`:
      - Add `playlist: List[PlaylistItem]` (in addition to existing left/right song for backwards compat).
      - Add methods: `add_song`, `remove_song`, `move_song`, `update_transition`.

5.  **Implement `PlaylistScreen`** (`app/screens/playlist.py`)
    - **Left Panel**: Song Library (reused from GenerationScreen logic).
    - **Right Panel**: Playlist View (List of songs with transition indicators).
    - **Bottom Panel**: Transition Editor (context-aware for selected playlist link).
    - **Features**: Move up/down keys, Preview button, Section selection per song.
    - **Note**: Manual song selection from library. LLM discovery added in post-MVP.

## MVP Phase 4: Video Engine Implementation

6.  **Implement `VideoEngine`** (`app/services/video_engine.py`)
    - **Class**: `VideoRenderer`
    - **Input**: List of `(AudioSegment, PlaylistItem)` and global `config`.
    - **Features**:
      - `render_intro()`: Create 8-beat intro clip with song list.
      - `render_lyrics()`:
        - Load `.lrc` file.
        - Calculate "Look-ahead" timing: `t_display = t_lyric - (60/BPM)`.
        - Draw text using `Pillow` for high-quality font rendering (Noto Sans TC bundled). Quality prioritized over render speed.
        - Generate overlay images, composite with ffmpeg-python. Expected render: ~10-15 minutes for 20-minute playlist (acceptable as background task).
      - `render_background()`: Loop background video/image using FFmpeg.
      - `composite()`: Use ffmpeg-python to combine layers efficiently.

## MVP Phase 5: Integration

7.  **Connect TUI to Video Engine**
    - Add "Export Video" action to `PlaylistScreen`.
    - Implement a progress callback in TUI to show rendering percentage.

8.  **Configuration**
    - Update `config.json` with:
      - `video_resolution`: "1080p"
      - `default_background`: "assets/backgrounds/default.jpg"
      - `font_path`: "assets/fonts/NotoSansTC-Bold.ttf"
      - `song_library_path`: "song_library/"
      - `output_path`: "output/"

---

## Post-MVP: LLM-Powered Song Discovery

9.  **Implement `SongDiscoveryScreen`** (`app/screens/discovery.py`)
    - **Chat Panel**: Text input for LLM-based song discovery.
    - **Shortlist Columns**: Multi-column view showing 3 suggested songs per slot.
    - **Preview**: Start playback, Esc to stop (same as existing TUI).
    - **Features**:
      - LLM integration via OpenRouter (configurable model).
      - JSON-based song catalog with AI summaries for reranking.
      - Column swapping (move Song 2 suggestions to Song 3 slot).
      - "Use this selection" button to proceed to PlaylistScreen.

10. **Add AI Metadata Generation** (during ingestion)
    - Extend `poc/generate_song_metadata.py` to also generate:
      - `ai_summary`: One-sentence song description.
      - `themes`: Auto-generated theme tags.
      - `bible_verses`: Related Scripture references.
      - `vocalist`: male/female/mixed.

11. **Update Configuration**
    - Add to `config.json`:
      - `llm_model`: "openai/gpt-4o-mini" (configurable)
      - `openrouter_api_key`: (environment variable or config)

---

## Verification Plan

### MVP Manual Verification
1.  **Data Migration**: Run `scripts/migrate_song_library.py` and verify:
    - Each song has its own directory with `analysis.json`, `lyrics.json`.
    - `catalog_index.json` lists all songs with correct metadata.
2.  **LRC Generation**: Run `python poc/generate_lrc.py --song "Song Name"` and verify the `.lrc` file opens in a media player and syncs correctly.
3.  **Playlist TUI**:
    - Start app: `python -m app.main`.
    - Manually add/remove songs, reorder.
    - Edit transitions between songs.
    - Play audio preview.
4.  **Video Export**:
    - Press `E` to export video.
    - Watch resulting `.mp4`.
    - Check: Intro shows correct songs, lyrics appear 1 beat before audio, song title visible in corner, no audio gaps.

### Post-MVP Manual Verification
5.  **Song Discovery TUI**:
    - Enter chat query: "4 songs about God's love, starting fast".
    - Verify shortlists appear for each slot (3 songs each).
    - Select songs and proceed to playlist.

### Automated Tests
- `tests/test_video_engine.py`: Test timing calculations (look-ahead logic), LRC parsing.
- `tests/test_playlist_state.py`: Test adding/removing/moving songs in `AppState`.
- `tests/test_lrc_generation.py`: Test LLM alignment prompt, beat-snapping logic.
- `tests/test_song_discovery.py`: Test LLM client, catalog search, shortlist generation. (Post-MVP)
- `tests/integration/test_full_workflow.py`: End-to-end test from playlist → export (audio only, faster).
