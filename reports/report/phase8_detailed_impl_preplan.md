# Plan: Write Phase 8 Detailed Implementation Plan

## Task
Write `report/phase8_detailed_impl_plan.md` — a comprehensive implementation plan for Phase 8 (User App TUI).

## User Decisions (from interview)
1. **Data source**: Local SQLite (shared with admin CLI), Turso-swappable later
2. **Transitions**: Gap only (stem-based fading, same as POC)
3. **Video**: Configurable templates — solid/gradient color OR custom background image, fixed text style per template
4. **Multi-song**: Ordered chain of N songs with reordering (move up/down, insert, remove)
5. **Asset caching**: Download stems/audio/LRC from R2 on demand, cache locally
6. **Persistence**: Songsets saved in SQLite (new tables: `songsets`, `songset_items`)
7. **Playback**: miniaudio (cross-platform, no system dependency)
8. **Video engine**: Pillow frames + FFmpeg CLI subprocess
9. **UI framework**: Textual TUI
10. **Audio output**: MP3

## Plan: What to write

The report file will contain the following sections:

### 1. Module Structure
New package at `src/stream_of_worship/app/` with:
- `main.py` — entry point (`sow-app` CLI command)
- `config.py` — reuses `AdminConfig.load()` for DB/R2 config, adds app-specific `[app]` section (cache_dir, output_dir, video_resolution)
- `db/` — `schema.py` (songsets + songset_items DDL), `read_client.py` (read songs/recordings), `songset_client.py` (CRUD songsets), `models.py` (AppSong, AppRecording, Songset, SongsetItem)
- `services/` — `catalog.py`, `asset_cache.py`, `audio_engine.py`, `video_engine.py`, `playback.py`, `export.py`
- `screens/` — `songset_list.py`, `browse.py`, `songset_editor.py`, `transition_detail.py`, `export_progress.py`, `settings.py`, `app.tcss`
- `state.py`, `app.py` (main Textual App class)

### 2. Database Tables
- `songsets` — id (UUID), name, description, total_duration_seconds, total_songs, timestamps
- `songset_items` — id (UUID), songset_id (FK), position, song_id (FK), recording_hash_prefix (FK), section selection, transition params (gap_beats, fade_window_beats, fade_bottom, stems_to_fade JSON, section boundary adjustments), UNIQUE(songset_id, position)
- Triggers for updated_at, CASCADE delete on songset_items when songset deleted

### 3. TUI Screen Flow
```
SongsetListScreen (home) → SongsetEditorScreen → TransitionDetailScreen
                              ↕ BrowseScreen (modal)
                              ↕ ExportProgressScreen (modal)
SongsetListScreen → SettingsScreen
```

### 4. Audio Pipeline (port from POC)
- Reuse logarithmic fade curves from `poc/transition_builder_v2/app/services/generation.py`
- Reference `src/stream_of_worship/tui/services/generation.py` for partial migration patterns
- Multi-song chain: iterate pairs, generate per-pair transitions, concatenate all segments
- WAV intermediate → MP3 via FFmpeg subprocess

### 5. Video Pipeline (new)
- Template system: dataclass defining background_type (solid/gradient/image), colors, font_family, font_size, text_color, text_position
- LRC parsing from R2 cache
- Pillow frame rendering at 30fps: background + current lyrics line highlighted
- FFmpeg piped encoding: raw RGB frames → H.264 MP4, muxed with audio

### 6. R2 Client Extension
- Current `R2Client` only has `upload_audio`, `download_audio`, `audio_exists`
- `AssetCacheService` will use boto3 directly (via R2Client's internal `_client`) to download:
  - `{hash_prefix}/stems/vocals.wav`, `drums.wav`, `bass.wav`, `other.wav`
  - `{hash_prefix}/lyrics.lrc`
- OR: add `download_file(s3_key, dest_path)` and `file_exists(s3_key)` generic methods to R2Client

### 7. Sub-phases (6 incremental phases)
- **8A**: Foundation — config, DB schema, models, read_client, songset_client, catalog service (~60 tests)
- **8B**: Asset cache + playback — R2 download/cache, miniaudio playback (~25 tests)
- **8C**: Audio engine — port POC transitions, multi-song generation (~30 tests)
- **8D**: Video engine — templates, Pillow frames, FFmpeg muxing (~20 tests)
- **8E**: TUI screens — all screens, export orchestration, app.tcss (~30 tests)
- **8F**: Integration + polish — pyproject.toml, e2e tests, report updates (~20 tests)

### 8. Key Architectural Decisions
- App reads admin's `songs` + `recordings` tables (read-only), writes its own `songsets` + `songset_items`
- Same SQLite file shared by admin CLI and user app (no write conflicts, WAL mode)
- Reuses `AdminConfig.load()` to get db_path and R2 settings
- R2Client reused from `stream_of_worship.admin.services.r2` (extended with generic download)
- Progress callbacks for long operations (export runs in `run_worker()` thread)

### 9. Risks and Mitigations Table

### 10. Verification Steps

## Critical Files to Reference
- `poc/transition_builder_v2/app/services/generation.py` — audio engine to port (fade curves, stem mixing, gap transitions)
- `src/stream_of_worship/tui/services/generation.py` — partial migration reference
- `src/stream_of_worship/tui/state.py` — state management pattern
- `src/stream_of_worship/admin/db/client.py` — DB client pattern (connection, transactions, from_row)
- `src/stream_of_worship/admin/db/schema.py` — schema definition pattern (CREATE TABLE IF NOT EXISTS, triggers, indexes)
- `src/stream_of_worship/admin/db/models.py` — model pattern (from_row, to_dict, JSON property accessors)
- `src/stream_of_worship/admin/config.py` — config pattern (TOML loading, get/set, platform paths)
- `src/stream_of_worship/admin/services/r2.py` — R2 client to extend/reuse
- `pyproject.toml` — dependency groups, entry points

## Implementation Action
Write the complete plan as `report/phase8_detailed_impl_plan.md` containing all sections above with:
- Concrete file paths and class/function signatures
- SQL DDL statements
- Sub-phase breakdown with test counts and dependencies
- Risk/mitigation table
- Verification checklist
