# Graph Report - stream_of_worship  (2026-06-16)

## Corpus Check
- 369 files · ~259,965 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3456 nodes · 11728 edges · 107 communities detected
- Extraction: 36% EXTRACTED · 64% INFERRED · 0% AMBIGUOUS · INFERRED: 7458 edges (avg confidence: 0.56)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 137|Community 137]]
- [[_COMMUNITY_Community 138|Community 138]]
- [[_COMMUNITY_Community 139|Community 139]]
- [[_COMMUNITY_Community 140|Community 140]]
- [[_COMMUNITY_Community 141|Community 141]]
- [[_COMMUNITY_Community 142|Community 142]]
- [[_COMMUNITY_Community 143|Community 143]]
- [[_COMMUNITY_Community 144|Community 144]]
- [[_COMMUNITY_Community 145|Community 145]]
- [[_COMMUNITY_Community 146|Community 146]]
- [[_COMMUNITY_Community 206|Community 206]]
- [[_COMMUNITY_Community 207|Community 207]]
- [[_COMMUNITY_Community 208|Community 208]]
- [[_COMMUNITY_Community 209|Community 209]]
- [[_COMMUNITY_Community 210|Community 210]]
- [[_COMMUNITY_Community 211|Community 211]]
- [[_COMMUNITY_Community 212|Community 212]]
- [[_COMMUNITY_Community 213|Community 213]]
- [[_COMMUNITY_Community 214|Community 214]]
- [[_COMMUNITY_Community 215|Community 215]]
- [[_COMMUNITY_Community 216|Community 216]]
- [[_COMMUNITY_Community 217|Community 217]]
- [[_COMMUNITY_Community 218|Community 218]]
- [[_COMMUNITY_Community 219|Community 219]]
- [[_COMMUNITY_Community 220|Community 220]]
- [[_COMMUNITY_Community 221|Community 221]]
- [[_COMMUNITY_Community 222|Community 222]]
- [[_COMMUNITY_Community 223|Community 223]]
- [[_COMMUNITY_Community 224|Community 224]]
- [[_COMMUNITY_Community 225|Community 225]]
- [[_COMMUNITY_Community 226|Community 226]]
- [[_COMMUNITY_Community 229|Community 229]]
- [[_COMMUNITY_Community 230|Community 230]]
- [[_COMMUNITY_Community 231|Community 231]]
- [[_COMMUNITY_Community 232|Community 232]]
- [[_COMMUNITY_Community 233|Community 233]]
- [[_COMMUNITY_Community 234|Community 234]]
- [[_COMMUNITY_Community 235|Community 235]]
- [[_COMMUNITY_Community 236|Community 236]]
- [[_COMMUNITY_Community 237|Community 237]]
- [[_COMMUNITY_Community 239|Community 239]]
- [[_COMMUNITY_Community 240|Community 240]]
- [[_COMMUNITY_Community 241|Community 241]]
- [[_COMMUNITY_Community 242|Community 242]]
- [[_COMMUNITY_Community 243|Community 243]]
- [[_COMMUNITY_Community 244|Community 244]]
- [[_COMMUNITY_Community 245|Community 245]]
- [[_COMMUNITY_Community 246|Community 246]]
- [[_COMMUNITY_Community 247|Community 247]]
- [[_COMMUNITY_Community 248|Community 248]]
- [[_COMMUNITY_Community 249|Community 249]]
- [[_COMMUNITY_Community 250|Community 250]]
- [[_COMMUNITY_Community 251|Community 251]]
- [[_COMMUNITY_Community 252|Community 252]]
- [[_COMMUNITY_Community 253|Community 253]]
- [[_COMMUNITY_Community 254|Community 254]]
- [[_COMMUNITY_Community 255|Community 255]]
- [[_COMMUNITY_Community 256|Community 256]]
- [[_COMMUNITY_Community 257|Community 257]]
- [[_COMMUNITY_Community 258|Community 258]]
- [[_COMMUNITY_Community 259|Community 259]]
- [[_COMMUNITY_Community 260|Community 260]]
- [[_COMMUNITY_Community 261|Community 261]]
- [[_COMMUNITY_Community 262|Community 262]]
- [[_COMMUNITY_Community 263|Community 263]]
- [[_COMMUNITY_Community 264|Community 264]]

## God Nodes (most connected - your core abstractions)
1. `PlaybackService` - 268 edges
2. `AssetCache` - 228 edges
3. `AppState` - 216 edges
4. `SongsetItem` - 208 edges
5. `Song` - 204 edges
6. `R2Client` - 200 edges
7. `SongsetClient` - 189 edges
8. `ConnectionProvider` - 177 edges
9. `DatabaseClient` - 173 edges
10. `Recording` - 169 edges

## Surprising Connections (you probably didn't know these)
- `run_command()` --calls--> `run()`  [INFERRED]
  scripts/populate_songs_batch.py → src/stream_of_worship/app/main.py
- `main()` --calls--> `open()`  [INFERRED]
  scripts/populate_songs_batch.py → webapp/src/components/songset/TransitionPanel.tsx
- `get_existing_catalog()` --calls--> `CatalogIndex`  [INFERRED]
  scripts/migrate_song_library.py → src/stream_of_worship/core/catalog.py
- `Data migration script for Stream of Worship.  This script migrates existing POC` --uses--> `CatalogIndex`  [INFERRED]
  scripts/migrate_song_library.py → src/stream_of_worship/core/catalog.py
- `Convert Chinese name to pinyin for cross-platform compatibility.      Args:` --uses--> `CatalogIndex`  [INFERRED]
  scripts/migrate_song_library.py → src/stream_of_worship/core/catalog.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.01
Nodes (347): Main TUI application for Stream of Worship User App.  Textual-based application, Handle app mount event., Wire up the per-user ``SongsetClient`` and continue to the list.          Called, Force reconnection to the Postgres catalog database (Shift+S).          Useful a, Create a fresh screen instance.          Creates a new screen instance on each c, Check if a Textual screen instance matches a given AppScreen enum value., Navigate to a screen.          Args:             screen: Screen to navigate to, Navigate back to the previous screen. (+339 more)

### Community 1 - "Community 1"
Cohesion: 0.02
Nodes (328): AdminConfig, Configuration for sow-admin CLI.      Attributes:         analysis_url: URL of t, Audio commands for sow-admin.  Provides CLI commands for downloading audio from, Delete multiple recordings from stdin., List audio recordings.      Display recordings from the database with optional s, Prompt for y/n confirmation, return True if accepted.      Args:         message, Show detailed info for a recording.      Displays all metadata for a recording,, Prompt for manual URL, validate format, return URL or None.      Args:         m (+320 more)

### Community 2 - "Community 2"
Cohesion: 0.01
Nodes (229): handle_config(), handle_migration(), handle_playlist(), launch_tui(), main(), Main CLI entry point for Stream of Worship.  Provides a unified interface for: -, Main entry point for the CLI.      When run without arguments, launches the TUI, Launch the TUI application.      Args:         config: Configuration object (+221 more)

### Community 3 - "Community 3"
Cohesion: 0.03
Nodes (238): BaseModel, BaseSettings, check_cache_access(), check_embedding_connection(), check_llm_connection(), check_r2_connection(), health_check(), Health check endpoint. (+230 more)

### Community 4 - "Community 4"
Cohesion: 0.02
Nodes (222): Get a configuration value by key.          Supports dot notation mapping to flat, run(), align_lrc_recording(), analyze_recording(), batch(), _build_fresh_editor_state(), cache_assets(), _cancel_all_jobs() (+214 more)

### Community 5 - "Community 5"
Cohesion: 0.02
Nodes (143): cli_entry(), config(), main(), Main entry point for sow-admin CLI.  Provides a Typer-based CLI for managing Str, Entry point for the CLI application., Callback for --version flag., sow-admin: Administrative tools for Stream of Worship.      Manage song catalogs, Manage configuration.      Show, set, or display the path to the configuration f (+135 more)

### Community 6 - "Community 6"
Cohesion: 0.02
Nodes (95): GET(), Set a configuration value by key.          Supports dot notation mapping to flat, GET(), buildPublishedRecordingExistsClause(), buildSongWhereClause(), findTopMatchingLines(), getAlbums(), getSong() (+87 more)

### Community 7 - "Community 7"
Cohesion: 0.03
Nodes (76): Protocol, Clear cached files.          Args:             hash_prefix: If specified, only c, AssetFetcher, AssetFetcherProtocol, AudioSegmentInfo, build_ffmpeg_filter_complex(), calculate_gap_ms(), calculate_total_duration() (+68 more)

### Community 8 - "Community 8"
Cohesion: 0.05
Nodes (97): LRCWorkerError, _bucket_to_phrase(), _canonical_lines(), convert(), _FallbackFuzz, _normalize(), _phrases_from_words(), ratio() (+89 more)

### Community 9 - "Community 9"
Cohesion: 0.06
Nodes (6): Textual application for the admin LRC editor.  Launches the interactive LRC edit, Admin LRC editor Textual application., LRCEditorScreen, UndoEntry, format_centiseconds(), Format seconds to ``[mm:ss.xx]`` centisecond string.      Uses centisecond round

### Community 10 - "Community 10"
Cohesion: 0.07
Nodes (26): add_user(), delete_user(), _get_user_client(), list_users(), _load_config(), User management commands for sow-admin.  Seed and inspect rows in the Better Aut, Delete a user (CASCADE deletes their songsets, settings, etc.)., connection() (+18 more)

### Community 11 - "Community 11"
Cohesion: 0.12
Nodes (19): ensure_config_exists(), get_cache_dir(), get_config_dir(), get_config_path(), get_env_var_name(), get_secret(), _key_to_attr(), load() (+11 more)

### Community 12 - "Community 12"
Cohesion: 0.12
Nodes (8): TestPlayerWithTrack(), ComponentWithoutProvider(), TestPlayer(), TestChildComponent(), useAudioPlayerContext(), TestComponent(), useAudioPlayer(), formatTime()

### Community 13 - "Community 13"
Cohesion: 0.13
Nodes (8): formatted_duration(), Data models for sow-app database entities.  Provides dataclasses for Songset and, Embedding vector for a song.      Attributes:         song_id: Song ID (primary, Embedding vector for a single lyric line.      Attributes:         id: Row ID (a, SongEmbedding, SongLineEmbedding, total_recordings(), total_songs()

### Community 14 - "Community 14"
Cohesion: 0.24
Nodes (9): current_file(), duration_seconds(), is_paused(), is_playing(), is_stopped(), position_seconds(), Audio playback service for sow-app.  Provides audio playback using miniaudio. Ma, # TODO: Implement section preview with automatic stop (+1 more)

### Community 15 - "Community 15"
Cohesion: 0.15
Nodes (2): formatDurationSafe(), formatDuration()

### Community 16 - "Community 16"
Cohesion: 0.25
Nodes (6): buildChaptersFromSegments(), findChapterAtTime(), generateChaptersManifest(), getChapterProgress(), getLyricAtTime(), getSongTitleAtTime()

### Community 18 - "Community 18"
Cohesion: 0.2
Nodes (2): Badge(), cn()

### Community 21 - "Community 21"
Cohesion: 0.29
Nodes (7): get_logger(), Logging configuration for sow-app.  Provides session logging to file without int, Rotate log file on startup if it exceeds max size.      Args:         log_file:, Set up application logging to file with startup rotation.      Args:         log, Get a logger for a specific module.      Args:         name: Module name (usuall, _rotate_log_if_needed(), setup_logging()

### Community 22 - "Community 22"
Cohesion: 0.33
Nodes (2): formatBytes(), getFileSizeDisplay()

### Community 24 - "Community 24"
Cohesion: 0.29
Nodes (5): from_row(), Data models for Better Auth identity entities.  Shared between admin (``sow-admi, Shared database helper functions.  Provides common utilities for database operat, Coerce a value to an ISO-8601 string, handling datetime objects.      psycopg3 r, to_str()

### Community 25 - "Community 25"
Cohesion: 0.53
Nodes (4): mockEmptyShares(), mockSizes(), openSendFileTab(), renderDialog()

### Community 29 - "Community 29"
Cohesion: 0.47
Nodes (4): resolvePublicOrigin(), activeShareConditions(), GET(), POST()

### Community 30 - "Community 30"
Cohesion: 0.4
Nodes (2): _BindingGroup, Grouped footer widget for the LRC editor.  Displays key bindings organized into

### Community 31 - "Community 31"
Cohesion: 0.6
Nodes (3): downloadArtifact(), downloadArtifactViaProxy(), fetchSignedUrlAndDownload()

### Community 32 - "Community 32"
Cohesion: 0.5
Nodes (2): buildLrc(), LyricsTimingEditor()

### Community 41 - "Community 41"
Cohesion: 0.67
Nodes (2): fetchSettings(), loadSettings()

### Community 42 - "Community 42"
Cohesion: 0.67
Nodes (2): DELETE(), GET()

### Community 43 - "Community 43"
Cohesion: 0.67
Nodes (2): DELETE(), GET()

### Community 44 - "Community 44"
Cohesion: 0.5
Nodes (2): fullTextSearchSongs(), GET()

### Community 45 - "Community 45"
Cohesion: 0.5
Nodes (2): Initialize the asset cache.          Args:             cache_dir: Base directory, Ensure the cache directory exists.

### Community 46 - "Community 46"
Cohesion: 0.5
Nodes (2): Get the total size of cached files in bytes.          Args:             hash_pre, Get the total size of cached files in MB.          Args:             hash_prefix

### Community 53 - "Community 53"
Cohesion: 0.67
Nodes (1): handleSeek()

### Community 54 - "Community 54"
Cohesion: 1.0
Nodes (2): gapToSeconds(), TransitionControls()

### Community 61 - "Community 61"
Cohesion: 1.0
Nodes (2): loadSongsets(), transformSongsets()

### Community 62 - "Community 62"
Cohesion: 1.0
Nodes (2): handleSubmit(), validate()

### Community 63 - "Community 63"
Cohesion: 1.0
Nodes (2): handleSubmit(), validate()

### Community 64 - "Community 64"
Cohesion: 1.0
Nodes (2): DELETE(), GET()

### Community 65 - "Community 65"
Cohesion: 0.67
Nodes (1): Connection health checker for sow-app.  Re-exports the shared check_database_con

### Community 66 - "Community 66"
Cohesion: 0.67
Nodes (2): Initialize playback service., Initialize the playback service.          Args:             buffer_ms: Audio buf

### Community 67 - "Community 67"
Cohesion: 0.67
Nodes (1): SQL schema definitions for sow-app database tables (PostgreSQL).  Defines the da

### Community 137 - "Community 137"
Cohesion: 2.0
Nodes (1): API routes for the analysis service.

### Community 138 - "Community 138"
Cohesion: 1.0
Nodes (1): Stream of Worship - A seamless worship music transition system.  This package pr

### Community 139 - "Community 139"
Cohesion: 1.0
Nodes (1): CLI entry points for Stream of Worship.

### Community 140 - "Community 140"
Cohesion: 1.0
Nodes (1): sow-admin CLI: Administrative tools for Stream of Worship.  This package provide

### Community 141 - "Community 141"
Cohesion: 1.0
Nodes (1): CLI commands for sow-admin.

### Community 142 - "Community 142"
Cohesion: 1.0
Nodes (1): Admin interactive LRC editor package.

### Community 143 - "Community 143"
Cohesion: 1.0
Nodes (1): SQL schema for Better Auth core tables.  Defines the canonical Better Auth schem

### Community 144 - "Community 144"
Cohesion: 1.0
Nodes (1): Unified PostgreSQL schema for Stream of Worship.  Combines catalog (songs, recor

### Community 145 - "Community 145"
Cohesion: 1.0
Nodes (1): Stream of Worship User App (TUI).  Interactive Textual TUI application for worsh

### Community 146 - "Community 146"
Cohesion: 1.0
Nodes (1): SQL schema for per-user app tables.  Tables we own that are scoped to a user via

### Community 206 - "Community 206"
Cohesion: 1.0
Nodes (1): Ensure concurrent jobs is at least 1 to prevent deadlock.

### Community 207 - "Community 207"
Cohesion: 1.0
Nodes (1): Convert empty-string env vars to None for Optional[int] fields.          pydanti

### Community 208 - "Community 208"
Cohesion: 1.0
Nodes (1): Check if models are validated and ready.

### Community 209 - "Community 209"
Cohesion: 1.0
Nodes (1): Check if MVSEP is available for use.          Returns:             True when ena

### Community 210 - "Community 210"
Cohesion: 1.0
Nodes (1): Create from dictionary.

### Community 211 - "Community 211"
Cohesion: 1.0
Nodes (1): Get or create LLM client.

### Community 212 - "Community 212"
Cohesion: 1.0
Nodes (1): Get or load Whisper model.

### Community 213 - "Community 213"
Cohesion: 1.0
Nodes (1): Get or create LLM client.

### Community 214 - "Community 214"
Cohesion: 1.0
Nodes (1): Load configuration from TOML file.          Args:             path: Path to conf

### Community 215 - "Community 215"
Cohesion: 1.0
Nodes (1): Convert dot-notation key to attribute name.          Maps TOML section paths to

### Community 216 - "Community 216"
Cohesion: 1.0
Nodes (1): Parse a job response from JSON.          Args:             data: JSON response f

### Community 217 - "Community 217"
Cohesion: 1.0
Nodes (1): Create a Song from a database row tuple.          Args:             row: Databas

### Community 218 - "Community 218"
Cohesion: 1.0
Nodes (1): Get lyrics as a list of lines.          Returns:             List of lyric lines

### Community 219 - "Community 219"
Cohesion: 1.0
Nodes (1): Create a Recording from a database row tuple.          Args:             row: Da

### Community 220 - "Community 220"
Cohesion: 1.0
Nodes (1): Check if analysis is complete.          Returns:             True if analysis_st

### Community 221 - "Community 221"
Cohesion: 1.0
Nodes (1): Check if LRC generation is complete.          Returns:             True if lrc_s

### Community 222 - "Community 222"
Cohesion: 1.0
Nodes (1): Check if the recording is published for user visibility.          Returns:

### Community 223 - "Community 223"
Cohesion: 1.0
Nodes (1): Get beats as a list of floats.          Returns:             List of beat timest

### Community 224 - "Community 224"
Cohesion: 1.0
Nodes (1): Get duration formatted as MM:SS.          Returns:             Formatted duratio

### Community 225 - "Community 225"
Cohesion: 1.0
Nodes (1): Get total number of songs.          Returns:             Number of songs in the

### Community 226 - "Community 226"
Cohesion: 1.0
Nodes (1): Get total number of recordings.          Returns:             Number of recordin

### Community 229 - "Community 229"
Cohesion: 1.0
Nodes (1): Check if audio is currently playing.

### Community 230 - "Community 230"
Cohesion: 1.0
Nodes (1): Check if audio is currently paused.

### Community 231 - "Community 231"
Cohesion: 1.0
Nodes (1): Check if audio is stopped (not playing and not paused).

### Community 232 - "Community 232"
Cohesion: 1.0
Nodes (1): Get the currently loaded file.

### Community 233 - "Community 233"
Cohesion: 1.0
Nodes (1): Get current playback position in seconds.

### Community 234 - "Community 234"
Cohesion: 1.0
Nodes (1): Check if this is a gap transition.

### Community 235 - "Community 235"
Cohesion: 1.0
Nodes (1): Check if this is a crossfade transition.

### Community 236 - "Community 236"
Cohesion: 1.0
Nodes (1): Create from dictionary.

### Community 237 - "Community 237"
Cohesion: 1.0
Nodes (1): Return status indicator.

### Community 239 - "Community 239"
Cohesion: 1.0
Nodes (1): Whether error logging is enabled.

### Community 240 - "Community 240"
Cohesion: 1.0
Nodes (1): Whether session logging is enabled.

### Community 241 - "Community 241"
Cohesion: 1.0
Nodes (1): Create a User from a ``"user"`` table row.          Args:             row: Row t

### Community 242 - "Community 242"
Cohesion: 1.0
Nodes (1): Load configuration from a JSON file.          Args:             path: Path to co

### Community 243 - "Community 243"
Cohesion: 1.0
Nodes (1): Get video resolution as (width, height) tuple.          Returns:             Tup

### Community 244 - "Community 244"
Cohesion: 1.0
Nodes (1): Get formatted display name.

### Community 245 - "Community 245"
Cohesion: 1.0
Nodes (1): Create Song from dictionary.          Args:             data: Dictionary contain

### Community 246 - "Community 246"
Cohesion: 1.0
Nodes (1): Load catalog index from JSON file.          Args:             path: Path to cata

### Community 247 - "Community 247"
Cohesion: 1.0
Nodes (1): Cache directory - always at standard platform location.

### Community 248 - "Community 248"
Cohesion: 1.0
Nodes (1): Log directory - derived from working_dir.

### Community 249 - "Community 249"
Cohesion: 1.0
Nodes (1): Output directory - derived from working_dir.

### Community 250 - "Community 250"
Cohesion: 1.0
Nodes (1): Songset backup directory - derived from working_dir.

### Community 251 - "Community 251"
Cohesion: 1.0
Nodes (1): Deprecated: Use songsets_backup_dir instead.

### Community 252 - "Community 252"
Cohesion: 1.0
Nodes (1): Load configuration from TOML file.          Args:             path: Path to conf

### Community 253 - "Community 253"
Cohesion: 1.0
Nodes (1): Convert dot-notation key to attribute name.          Maps TOML section paths to

### Community 254 - "Community 254"
Cohesion: 1.0
Nodes (1): Get current playback state.

### Community 255 - "Community 255"
Cohesion: 1.0
Nodes (1): Check if currently playing.

### Community 256 - "Community 256"
Cohesion: 1.0
Nodes (1): Get currently loaded file.

### Community 257 - "Community 257"
Cohesion: 1.0
Nodes (1): Get duration of current file in seconds.

### Community 258 - "Community 258"
Cohesion: 1.0
Nodes (1): Get current position in seconds.

### Community 259 - "Community 259"
Cohesion: 1.0
Nodes (1): Create a Songset from a database row tuple.          Args:             row: Data

### Community 260 - "Community 260"
Cohesion: 1.0
Nodes (1): Generate a new unique songset ID.          Returns:             Unique ID string

### Community 261 - "Community 261"
Cohesion: 1.0
Nodes (1): Create a SongsetItem from a database row tuple.          Args:             row:

### Community 262 - "Community 262"
Cohesion: 1.0
Nodes (1): Generate a new unique item ID.          Returns:             Unique ID string.

### Community 263 - "Community 263"
Cohesion: 1.0
Nodes (1): Get duration formatted as MM:SS.          Returns:             Formatted duratio

### Community 264 - "Community 264"
Cohesion: 1.0
Nodes (1): Get the key to display (song key or recording key).          Returns:

## Knowledge Gaps
- **405 isolated node(s):** `Run a command and return (success, output, error)`, `Fetch all song IDs from catalog, optionally filtered by album`, `Fetch all song IDs that have audio recordings`, `Get songs from catalog that don't have audio yet, optionally filtered by album`, `Extract job ID from command output` (+400 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 15`** (13 nodes): `formatDurationSafe()`, `handleConfirmRender()`, `handleSubmit()`, `isDifferent()`, `isIOS174OrLater()`, `updateField()`, `formatDuration()`, `formatTotalDuration()`, `handlePlay()`, `loadShare()`, `renderUnavailableMessage()`, `page.tsx`, `RenderForm.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 18`** (10 nodes): `Badge()`, `cn()`, `Sheet()`, `SheetClose()`, `SheetDescription()`, `SheetPortal()`, `SheetTitle()`, `SheetTrigger()`, `badge.tsx`, `sheet.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 22`** (7 nodes): `formatBytes()`, `formatLimit()`, `formatShareDuration()`, `getFileSizeDisplay()`, `isAboveLimit()`, `loadData()`, `ShareDialog.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 30`** (6 nodes): `_BindingGroup`, `._format_content()`, `.__init__()`, `.compose()`, `Grouped footer widget for the LRC editor.  Displays key bindings organized into`, `footer.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 32`** (5 nodes): `buildLrc()`, `lrcTimestampToSeconds()`, `LyricsTimingEditor()`, `secondsToLrcTimestamp()`, `LyricsTimingEditor.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 41`** (4 nodes): `fetchSettings()`, `handleSave()`, `loadSettings()`, `page.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 42`** (4 nodes): `DELETE()`, `GET()`, `POST()`, `route.ts`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 43`** (4 nodes): `DELETE()`, `GET()`, `POST()`, `route.ts`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 44`** (4 nodes): `fullTextSearchSongs()`, `GET()`, `route.ts`, `search.ts`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 45`** (4 nodes): `._ensure_cache_dir()`, `.__init__()`, `Initialize the asset cache.          Args:             cache_dir: Base directory`, `Ensure the cache directory exists.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 46`** (4 nodes): `.get_cache_size()`, `.get_cache_size_mb()`, `Get the total size of cached files in bytes.          Args:             hash_pre`, `Get the total size of cached files in MB.          Args:             hash_prefix`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 53`** (3 nodes): `handleSeek()`, `handleVolumeChange()`, `AudioPlayerBar.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 54`** (3 nodes): `gapToSeconds()`, `TransitionControls()`, `TransitionControls.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 61`** (3 nodes): `loadSongsets()`, `transformSongsets()`, `SongsetsClient.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 62`** (3 nodes): `handleSubmit()`, `validate()`, `page.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 63`** (3 nodes): `handleSubmit()`, `validate()`, `page.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 64`** (3 nodes): `DELETE()`, `GET()`, `route.ts`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 65`** (3 nodes): `Connection health checker for sow-app.  Re-exports the shared check_database_con`, `sync.py`, `sync.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 66`** (3 nodes): `.__init__()`, `Initialize playback service.`, `Initialize the playback service.          Args:             buffer_ms: Audio buf`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 67`** (3 nodes): `SQL schema definitions for sow-app database tables (PostgreSQL).  Defines the da`, `schema.py`, `schema.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 137`** (2 nodes): `API routes for the analysis service.`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 138`** (2 nodes): `__init__.py`, `Stream of Worship - A seamless worship music transition system.  This package pr`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 139`** (2 nodes): `CLI entry points for Stream of Worship.`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 140`** (2 nodes): `sow-admin CLI: Administrative tools for Stream of Worship.  This package provide`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 141`** (2 nodes): `CLI commands for sow-admin.`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 142`** (2 nodes): `Admin interactive LRC editor package.`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 143`** (2 nodes): `SQL schema for Better Auth core tables.  Defines the canonical Better Auth schem`, `auth_schema.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 144`** (2 nodes): `Unified PostgreSQL schema for Stream of Worship.  Combines catalog (songs, recor`, `postgres_schema.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 145`** (2 nodes): `Stream of Worship User App (TUI).  Interactive Textual TUI application for worsh`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 146`** (2 nodes): `SQL schema for per-user app tables.  Tables we own that are scoped to a user via`, `user_data_schema.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 206`** (1 nodes): `Ensure concurrent jobs is at least 1 to prevent deadlock.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 207`** (1 nodes): `Convert empty-string env vars to None for Optional[int] fields.          pydanti`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 208`** (1 nodes): `Check if models are validated and ready.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 209`** (1 nodes): `Check if MVSEP is available for use.          Returns:             True when ena`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 210`** (1 nodes): `Create from dictionary.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 211`** (1 nodes): `Get or create LLM client.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 212`** (1 nodes): `Get or load Whisper model.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 213`** (1 nodes): `Get or create LLM client.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 214`** (1 nodes): `Load configuration from TOML file.          Args:             path: Path to conf`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 215`** (1 nodes): `Convert dot-notation key to attribute name.          Maps TOML section paths to`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 216`** (1 nodes): `Parse a job response from JSON.          Args:             data: JSON response f`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 217`** (1 nodes): `Create a Song from a database row tuple.          Args:             row: Databas`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 218`** (1 nodes): `Get lyrics as a list of lines.          Returns:             List of lyric lines`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 219`** (1 nodes): `Create a Recording from a database row tuple.          Args:             row: Da`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 220`** (1 nodes): `Check if analysis is complete.          Returns:             True if analysis_st`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 221`** (1 nodes): `Check if LRC generation is complete.          Returns:             True if lrc_s`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 222`** (1 nodes): `Check if the recording is published for user visibility.          Returns:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 223`** (1 nodes): `Get beats as a list of floats.          Returns:             List of beat timest`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 224`** (1 nodes): `Get duration formatted as MM:SS.          Returns:             Formatted duratio`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 225`** (1 nodes): `Get total number of songs.          Returns:             Number of songs in the`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 226`** (1 nodes): `Get total number of recordings.          Returns:             Number of recordin`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 229`** (1 nodes): `Check if audio is currently playing.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 230`** (1 nodes): `Check if audio is currently paused.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 231`** (1 nodes): `Check if audio is stopped (not playing and not paused).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 232`** (1 nodes): `Get the currently loaded file.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 233`** (1 nodes): `Get current playback position in seconds.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 234`** (1 nodes): `Check if this is a gap transition.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 235`** (1 nodes): `Check if this is a crossfade transition.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 236`** (1 nodes): `Create from dictionary.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 237`** (1 nodes): `Return status indicator.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 239`** (1 nodes): `Whether error logging is enabled.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 240`** (1 nodes): `Whether session logging is enabled.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 241`** (1 nodes): `Create a User from a ``"user"`` table row.          Args:             row: Row t`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 242`** (1 nodes): `Load configuration from a JSON file.          Args:             path: Path to co`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 243`** (1 nodes): `Get video resolution as (width, height) tuple.          Returns:             Tup`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 244`** (1 nodes): `Get formatted display name.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 245`** (1 nodes): `Create Song from dictionary.          Args:             data: Dictionary contain`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 246`** (1 nodes): `Load catalog index from JSON file.          Args:             path: Path to cata`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 247`** (1 nodes): `Cache directory - always at standard platform location.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 248`** (1 nodes): `Log directory - derived from working_dir.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 249`** (1 nodes): `Output directory - derived from working_dir.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 250`** (1 nodes): `Songset backup directory - derived from working_dir.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 251`** (1 nodes): `Deprecated: Use songsets_backup_dir instead.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 252`** (1 nodes): `Load configuration from TOML file.          Args:             path: Path to conf`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 253`** (1 nodes): `Convert dot-notation key to attribute name.          Maps TOML section paths to`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 254`** (1 nodes): `Get current playback state.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 255`** (1 nodes): `Check if currently playing.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 256`** (1 nodes): `Get currently loaded file.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 257`** (1 nodes): `Get duration of current file in seconds.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 258`** (1 nodes): `Get current position in seconds.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 259`** (1 nodes): `Create a Songset from a database row tuple.          Args:             row: Data`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 260`** (1 nodes): `Generate a new unique songset ID.          Returns:             Unique ID string`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 261`** (1 nodes): `Create a SongsetItem from a database row tuple.          Args:             row:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 262`** (1 nodes): `Generate a new unique item ID.          Returns:             Unique ID string.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 263`** (1 nodes): `Get duration formatted as MM:SS.          Returns:             Formatted duratio`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 264`** (1 nodes): `Get the key to display (song key or recording key).          Returns:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `R2Client` connect `Community 1` to `Community 0`, `Community 2`, `Community 3`, `Community 4`, `Community 5`, `Community 7`, `Community 9`, `Community 45`, `Community 46`?**
  _High betweenness centrality (0.082) - this node is a cross-community bridge._
- **Why does `PlaybackService` connect `Community 0` to `Community 1`, `Community 66`, `Community 2`, `Community 4`, `Community 9`, `Community 14`?**
  _High betweenness centrality (0.064) - this node is a cross-community bridge._
- **Why does `AssetCache` connect `Community 0` to `Community 1`, `Community 3`, `Community 4`, `Community 7`, `Community 45`, `Community 46`?**
  _High betweenness centrality (0.040) - this node is a cross-community bridge._
- **Are the 244 inferred relationships involving `PlaybackService` (e.g. with `Audio commands for sow-admin.  Provides CLI commands for downloading audio from` and `Format seconds as MM:SS.      Args:         seconds: Duration in seconds      Re`) actually correct?**
  _`PlaybackService` has 244 INFERRED edges - model-reasoned connections that need verification._
- **Are the 210 inferred relationships involving `AssetCache` (e.g. with `Audio commands for sow-admin.  Provides CLI commands for downloading audio from` and `Format seconds as MM:SS.      Args:         seconds: Duration in seconds      Re`) actually correct?**
  _`AssetCache` has 210 INFERRED edges - model-reasoned connections that need verification._
- **Are the 199 inferred relationships involving `AppState` (e.g. with `TransitionBuilderApp` and `Main TUI application for Stream of Worship.  This is the entry point for the Tex`) actually correct?**
  _`AppState` has 199 INFERRED edges - model-reasoned connections that need verification._
- **Are the 205 inferred relationships involving `SongsetItem` (e.g. with `AppScreen` and `AppState`) actually correct?**
  _`SongsetItem` has 205 INFERRED edges - model-reasoned connections that need verification._