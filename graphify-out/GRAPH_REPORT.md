# Graph Report - .  (2026-04-29)

## Corpus Check
- 119 files · ~87,885 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2048 nodes · 6916 edges · 76 communities detected
- Extraction: 36% EXTRACTED · 64% INFERRED · 0% AMBIGUOUS · INFERRED: 4426 edges (avg confidence: 0.55)
- Token cost: 15,845 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_CLI Commands|CLI Commands]]
- [[_COMMUNITY_Configuration & Settings|Configuration & Settings]]
- [[_COMMUNITY_Admin CLI Commands|Admin CLI Commands]]
- [[_COMMUNITY_User App TUI|User App TUI]]
- [[_COMMUNITY_FastAPI Service|FastAPI Service]]
- [[_COMMUNITY_Cache Management|Cache Management]]
- [[_COMMUNITY_Songset Management|Songset Management]]
- [[_COMMUNITY_LRC Generation Service|LRC Generation Service]]
- [[_COMMUNITY_Lyrics Alignment|Lyrics Alignment]]
- [[_COMMUNITY_Logging|Logging]]
- [[_COMMUNITY_Audio Processing|Audio Processing]]
- [[_COMMUNITY_Playback Control|Playback Control]]
- [[_COMMUNITY_CLI Entry Points|CLI Entry Points]]
- [[_COMMUNITY_Audio Playback|Audio Playback]]
- [[_COMMUNITY_File IO|File I/O]]
- [[_COMMUNITY_Hash Computation|Hash Computation]]
- [[_COMMUNITY_ID Generation|ID Generation]]
- [[_COMMUNITY_Database Schema|Database Schema]]
- [[_COMMUNITY_Analysis Routes|Analysis Routes]]
- [[_COMMUNITY_Stream Init|Stream Init]]
- [[_COMMUNITY_Stream Worship|Stream Worship]]
- [[_COMMUNITY_Admin CLI|Admin CLI]]
- [[_COMMUNITY_Sow-Admin Commands|Sow-Admin Commands]]
- [[_COMMUNITY_Stream User|Stream User]]
- [[_COMMUNITY_Model Loading|Model Loading]]
- [[_COMMUNITY_Model State|Model State]]
- [[_COMMUNITY_Model Creation|Model Creation]]
- [[_COMMUNITY_Client Creation|Client Creation]]
- [[_COMMUNITY_Model Configuration|Model Configuration]]
- [[_COMMUNITY_Service Client|Service Client]]
- [[_COMMUNITY_Configuration Loading|Configuration Loading]]
- [[_COMMUNITY_URL Parsing|URL Parsing]]
- [[_COMMUNITY_Response Parsing|Response Parsing]]
- [[_COMMUNITY_Model Creation|Model Creation]]
- [[_COMMUNITY_Lyrics Parsing|Lyrics Parsing]]
- [[_COMMUNITY_Recording Creation|Recording Creation]]
- [[_COMMUNITY_Analysis Status|Analysis Status]]
- [[_COMMUNITY_Generation Status|Generation Status]]
- [[_COMMUNITY_Publication Status|Publication Status]]
- [[_COMMUNITY_Beats Detection|Beats Detection]]
- [[_COMMUNITY_Time Formatting|Time Formatting]]
- [[_COMMUNITY_Song Counting|Song Counting]]
- [[_COMMUNITY_Recording Counting|Recording Counting]]
- [[_COMMUNITY_Audio State|Audio State]]
- [[_COMMUNITY_Playback State|Playback State]]
- [[_COMMUNITY_Audio Control|Audio Control]]
- [[_COMMUNITY_File Operations|File Operations]]
- [[_COMMUNITY_Position Tracking|Position Tracking]]
- [[_COMMUNITY_Transition Logic|Transition Logic]]
- [[_COMMUNITY_Crossfade Logic|Crossfade Logic]]
- [[_COMMUNITY_Dictionary Operations|Dictionary Operations]]
- [[_COMMUNITY_Status Display|Status Display]]
- [[_COMMUNITY_Error Handling|Error Handling]]
- [[_COMMUNITY_Session Logging|Session Logging]]
- [[_COMMUNITY_Config|Config]]
- [[_COMMUNITY_Video Processing|Video Processing]]
- [[_COMMUNITY_Display Formatting|Display Formatting]]
- [[_COMMUNITY_Model Creation|Model Creation]]
- [[_COMMUNITY_Catalog Loading|Catalog Loading]]
- [[_COMMUNITY_Config Loading|Config Loading]]
- [[_COMMUNITY_Sync Status|Sync Status]]
- [[_COMMUNITY_S3 Operations|S3 Operations]]
- [[_COMMUNITY_API Configuration|API Configuration]]
- [[_COMMUNITY_Region Configuration|Region Configuration]]
- [[_COMMUNITY_Playback State|Playback State]]
- [[_COMMUNITY_Audio Control|Audio Control]]
- [[_COMMUNITY_File Loading|File Loading]]
- [[_COMMUNITY_Duration Calculation|Duration Calculation]]
- [[_COMMUNITY_Position Tracking|Position Tracking]]
- [[_COMMUNITY_Songset Creation|Songset Creation]]
- [[_COMMUNITY_ID Generation|ID Generation]]
- [[_COMMUNITY_Item Creation|Item Creation]]
- [[_COMMUNITY_Item Generation|Item Generation]]
- [[_COMMUNITY_Time Formatting|Time Formatting]]
- [[_COMMUNITY_Display Formatting|Display Formatting]]
- [[_COMMUNITY_Python Runtime|Python Runtime]]

## God Nodes (most connected - your core abstractions)
1. `PlaybackService` - 197 edges
2. `SongsetItem` - 190 edges
3. `SongsetClient` - 187 edges
4. `AppState` - 185 edges
5. `AssetCache` - 179 edges
6. `Song` - 138 edges
7. `DatabaseClient` - 134 edges
8. `Recording` - 126 edges
9. `R2Client` - 115 edges
10. `ReadOnlyClient` - 105 edges

## Surprising Connections (you probably didn't know these)
- `main()` --calls--> `run()`  [INFERRED]
  services/qwen3/src/sow_qwen3/main.py → src/stream_of_worship/app/main.py
- `Storage layer for R2 and local cache.` --uses--> `R2Client`  [INFERRED]
  services/analysis/src/sow_analysis/storage/__init__.py → src/stream_of_worship/admin/services/r2.py
- `download_audio()` --calls--> `client()`  [INFERRED]
  services/qwen3/src/sow_qwen3/storage/audio.py → src/stream_of_worship/ingestion/metadata_generator.py
- `main()` --calls--> `run()`  [INFERRED]
  services/analysis/src/sow_analysis/main.py → src/stream_of_worship/app/main.py
- `parse_lrc_response()` --calls--> `LRCLine`  [INFERRED]
  services/analysis/src/sow_analysis/workers/youtube_transcript.py → src/stream_of_worship/app/services/video_engine.py

## Hyperedges (group relationships)
- **Core Four Components** — stream_of_worship, admin_cli, analysis_service, user_app [EXTRACTED 1.00]
- **ML Analysis Pipeline** — analysis_service, allin1, demucs, whisper, bs_roformer, uvr_deecho, audio_separator [EXTRACTED 1.00]
- **Data Flow Architecture** — admin_cli, sop_org, sqlite, turso, cloudflare_r2, analysis_service, user_app [INFERRED 0.85]

## Communities

### Community 0 - "CLI Commands"
Cohesion: 0.02
Nodes (193): _check_catalog_health(), _check_database(), _check_first_run(), cli_entry(), export_all_songsets(), export_songset(), import_songset(), main() (+185 more)

### Community 1 - "Configuration & Settings"
Cohesion: 0.02
Nodes (161): Config, Update configuration values.          Args:             **kwargs: Key-value pair, Get lyrics look-ahead time in seconds based on BPM.          Args:             b, Configuration for Stream of Worship application., Core utilities for Stream of Worship., Convert SongsetItem to dictionary.          Args:             include_joined: Wh, Enum, TUI models for Stream of Worship. (+153 more)

### Community 2 - "Admin CLI Commands"
Cohesion: 0.02
Nodes (174): AdminConfig, Set a configuration value by key.          Supports dot notation for nested valu, Configuration for sow-admin CLI.      Attributes:         analysis_url: URL of t, config(), Select a song from the catalog.          Args:             song: Song to select, analyze_recording(), cache_assets(), check_status() (+166 more)

### Community 3 - "User App TUI"
Cohesion: 0.03
Nodes (129): Main TUI application for Stream of Worship User App.  Textual-based application, Handle app mount event., Run sync in background thread with error handling., Sync catalog on demand (capital S key)., Create a fresh screen instance.          Creates a new screen instance on each c, Navigate to a screen.          Args:             screen: Screen to navigate to, Navigate back to the previous screen., Quit the application with cleanup. (+121 more)

### Community 4 - "FastAPI Service"
Cohesion: 0.04
Nodes (151): BaseModel, FastAPI, check_cache_access(), check_llm_connection(), check_r2_connection(), get_aligner(), health_check(), Health check endpoint. (+143 more)

### Community 5 - "Cache Management"
Cohesion: 0.02
Nodes (174): ensure_config_exists(), get_cache_dir(), get_config_dir(), get_config_path(), get_default_db_path(), get_env_var_name(), get_secret(), load() (+166 more)

### Community 6 - "Songset Management"
Cohesion: 0.03
Nodes (127): Initialize listener dictionary., Remove a property change listener.          Args:             property_name: Nam, User-created songset (playlist) for worship sets.      Attributes:         id: U, Convert Songset to dictionary.          Returns:             Dictionary represen, A song within a songset with transition parameters.      Attributes:         id:, Songset, SongsetItem, Read-write database client for songset tables.  Provides CRUD operations for son (+119 more)

### Community 7 - "LRC Generation Service"
Cohesion: 0.07
Nodes (72): LRCWorkerError, Services layer for sow-app.  Business logic for catalog browsing, asset caching,, AlignRequest, AlignResponse, OutputFormat, Qwen3Client, Qwen3ClientError, Qwen3 Service HTTP client for lyrics alignment. (+64 more)

### Community 8 - "Lyrics Alignment"
Cohesion: 0.05
Nodes (48): BaseSettings, align_lyrics(), format_timestamp(), map_segments_to_lines(), normalize_text(), Alignment endpoint for lyrics to audio timestamps., Align lyrics to audio timestamps.      Args:         request: Alignment request, Normalize text by removing whitespace and common punctuation.      Args: (+40 more)

### Community 9 - "Logging"
Cohesion: 0.07
Nodes (22): ErrorLogger, get_session_logger(), init_error_logger(), init_session_logger(), Error logging utility for Transition Builder app.  Provides centralized error lo, Log an audio playback error.          Args:             audio_path: Path to audi, Log a file I/O error.          Args:             file_path: Path to the file, Centralized error logging service.      Appends error events with timestamps and (+14 more)

### Community 10 - "Audio Processing"
Cohesion: 0.08
Nodes (31): Admin CLI (sow-admin), allin1, Analysis Service, Audio Download, audio-separator, BS-Roformer, Catalog Management, Cloudflare R2 (+23 more)

### Community 11 - "Playback Control"
Cohesion: 0.13
Nodes (7): Seek to a specific position.          Args:             position_seconds: Positi, Seek relative to current position.          Args:             delta_seconds: Sec, Get current playback position information.          Returns:             Playbac, Background thread to track playback position., Seek to a position in the current file.          Args:             position_seco, Skip forward by specified seconds.          Args:             seconds: Number of, Skip backward by specified seconds.          Args:             seconds: Number o

### Community 12 - "CLI Entry Points"
Cohesion: 0.21
Nodes (6): current_file(), is_paused(), is_playing(), is_stopped(), Audio playback service for sow-app.  Provides audio playback using miniaudio. Ma, # TODO: Implement section preview with automatic stop

### Community 13 - "Audio Playback"
Cohesion: 0.22
Nodes (8): cli_entry(), main(), Main entry point for sow-admin CLI.  Provides a Typer-based CLI for managing Str, Entry point for the CLI application., Callback for --version flag., sow-admin: Administrative tools for Stream of Worship.      Manage song catalogs, version_callback(), App

### Community 15 - "File I/O"
Cohesion: 0.29
Nodes (7): get_logger(), Logging configuration for sow-app.  Provides session logging to file without int, Rotate log file on startup if it exceeds max size.      Args:         log_file:, Set up application logging to file with startup rotation.      Args:         log, Get a logger for a specific module.      Args:         name: Module name (usuall, _rotate_log_if_needed(), setup_logging()

### Community 16 - "Hash Computation"
Cohesion: 0.33
Nodes (5): compute_file_hash(), get_hash_prefix(), SHA-256 hashing for audio file identification.  Computes content hashes for audi, Compute the SHA-256 hash of a file.      Reads in 8 KiB chunks so arbitrarily la, Extract the 12-character R2 directory prefix from a full content hash.      This

### Community 17 - "ID Generation"
Cohesion: 0.4
Nodes (5): compute_new_song_id(), _normalize(), Shared utilities for song ID computation., Normalize string for ID computation: NFKC + strip., Compute the new stable song ID format.      Format: <pinyin_slug>_<8-hex-hash>

### Community 18 - "Database Schema"
Cohesion: 0.67
Nodes (1): SQL schema definitions for sow-app database tables.  Defines the database schema

### Community 19 - "Analysis Routes"
Cohesion: 2.0
Nodes (1): API routes for the analysis service.

### Community 20 - "Stream Init"
Cohesion: 1.0
Nodes (1): Stream of Worship - A seamless worship music transition system.  This package pr

### Community 21 - "Stream Worship"
Cohesion: 1.0
Nodes (1): CLI entry points for Stream of Worship.

### Community 22 - "Admin CLI"
Cohesion: 1.0
Nodes (1): sow-admin CLI: Administrative tools for Stream of Worship.  This package provide

### Community 23 - "Sow-Admin Commands"
Cohesion: 1.0
Nodes (1): CLI commands for sow-admin.

### Community 24 - "Stream User"
Cohesion: 1.0
Nodes (1): Stream of Worship User App (TUI).  Interactive Textual TUI application for worsh

### Community 26 - "Model Loading"
Cohesion: 1.0
Nodes (1): Check if the model is loaded and ready.          Returns:             True if mo

### Community 27 - "Model State"
Cohesion: 1.0
Nodes (1): Check if models are loaded and ready.

### Community 28 - "Model Creation"
Cohesion: 1.0
Nodes (1): Create from dictionary.

### Community 29 - "Client Creation"
Cohesion: 1.0
Nodes (1): Get or create LLM client.

### Community 30 - "Model Configuration"
Cohesion: 1.0
Nodes (1): Get or load Whisper model.

### Community 31 - "Service Client"
Cohesion: 1.0
Nodes (1): Get or create LLM client.

### Community 32 - "Configuration Loading"
Cohesion: 1.0
Nodes (1): Load configuration from TOML file.          Args:             path: Path to conf

### Community 33 - "URL Parsing"
Cohesion: 1.0
Nodes (1): Parse S3 URL into bucket and key.          Args:             s3_url: S3 URL like

### Community 34 - "Response Parsing"
Cohesion: 1.0
Nodes (1): Parse a job response from JSON.          Args:             data: JSON response f

### Community 35 - "Model Creation"
Cohesion: 1.0
Nodes (1): Create a Song from a database row tuple.          Args:             row: Databas

### Community 36 - "Lyrics Parsing"
Cohesion: 1.0
Nodes (1): Get lyrics as a list of lines.          Returns:             List of lyric lines

### Community 37 - "Recording Creation"
Cohesion: 1.0
Nodes (1): Create a Recording from a database row tuple.          Args:             row: Da

### Community 38 - "Analysis Status"
Cohesion: 1.0
Nodes (1): Check if analysis is complete.          Returns:             True if analysis_st

### Community 39 - "Generation Status"
Cohesion: 1.0
Nodes (1): Check if LRC generation is complete.          Returns:             True if lrc_s

### Community 40 - "Publication Status"
Cohesion: 1.0
Nodes (1): Check if the recording is published for user visibility.          Returns:

### Community 41 - "Beats Detection"
Cohesion: 1.0
Nodes (1): Get beats as a list of floats.          Returns:             List of beat timest

### Community 42 - "Time Formatting"
Cohesion: 1.0
Nodes (1): Get duration formatted as MM:SS.          Returns:             Formatted duratio

### Community 43 - "Song Counting"
Cohesion: 1.0
Nodes (1): Get total number of songs.          Returns:             Number of songs in the

### Community 44 - "Recording Counting"
Cohesion: 1.0
Nodes (1): Get total number of recordings.          Returns:             Number of recordin

### Community 47 - "Audio State"
Cohesion: 1.0
Nodes (1): Check if audio is currently playing.

### Community 48 - "Playback State"
Cohesion: 1.0
Nodes (1): Check if audio is currently paused.

### Community 49 - "Audio Control"
Cohesion: 1.0
Nodes (1): Check if audio is stopped (not playing and not paused).

### Community 50 - "File Operations"
Cohesion: 1.0
Nodes (1): Get the currently loaded file.

### Community 51 - "Position Tracking"
Cohesion: 1.0
Nodes (1): Get current playback position in seconds.

### Community 52 - "Transition Logic"
Cohesion: 1.0
Nodes (1): Check if this is a gap transition.

### Community 53 - "Crossfade Logic"
Cohesion: 1.0
Nodes (1): Check if this is a crossfade transition.

### Community 54 - "Dictionary Operations"
Cohesion: 1.0
Nodes (1): Create from dictionary.

### Community 55 - "Status Display"
Cohesion: 1.0
Nodes (1): Return status indicator.

### Community 57 - "Error Handling"
Cohesion: 1.0
Nodes (1): Whether error logging is enabled.

### Community 58 - "Session Logging"
Cohesion: 1.0
Nodes (1): Whether session logging is enabled.

### Community 59 - "Config"
Cohesion: 1.0
Nodes (1): Load configuration from a JSON file.          Args:             path: Path to co

### Community 60 - "Video Processing"
Cohesion: 1.0
Nodes (1): Get video resolution as (width, height) tuple.          Returns:             Tup

### Community 61 - "Display Formatting"
Cohesion: 1.0
Nodes (1): Get formatted display name.

### Community 62 - "Model Creation"
Cohesion: 1.0
Nodes (1): Create Song from dictionary.          Args:             data: Dictionary contain

### Community 63 - "Catalog Loading"
Cohesion: 1.0
Nodes (1): Load catalog index from JSON file.          Args:             path: Path to cata

### Community 64 - "Config Loading"
Cohesion: 1.0
Nodes (1): Load configuration from TOML file.          Args:             path: Path to conf

### Community 65 - "Sync Status"
Cohesion: 1.0
Nodes (1): Check if Turso sync is configured.          Returns:             True if Turso U

### Community 66 - "S3 Operations"
Cohesion: 1.0
Nodes (1): Get R2 bucket (from environment or default).          Returns:             R2 bu

### Community 67 - "API Configuration"
Cohesion: 1.0
Nodes (1): Get R2 endpoint (from environment).          Returns:             R2 endpoint UR

### Community 68 - "Region Configuration"
Cohesion: 1.0
Nodes (1): Get R2 region (from environment or default).          Returns:             R2 re

### Community 69 - "Playback State"
Cohesion: 1.0
Nodes (1): Get current playback state.

### Community 70 - "Audio Control"
Cohesion: 1.0
Nodes (1): Check if currently playing.

### Community 71 - "File Loading"
Cohesion: 1.0
Nodes (1): Get currently loaded file.

### Community 72 - "Duration Calculation"
Cohesion: 1.0
Nodes (1): Get duration of current file in seconds.

### Community 73 - "Position Tracking"
Cohesion: 1.0
Nodes (1): Get current position in seconds.

### Community 74 - "Songset Creation"
Cohesion: 1.0
Nodes (1): Create a Songset from a database row tuple.          Args:             row: Data

### Community 75 - "ID Generation"
Cohesion: 1.0
Nodes (1): Generate a new unique songset ID.          Returns:             Unique ID string

### Community 76 - "Item Creation"
Cohesion: 1.0
Nodes (1): Create a SongsetItem from a database row tuple.          Args:             row:

### Community 77 - "Item Generation"
Cohesion: 1.0
Nodes (1): Generate a new unique item ID.          Returns:             Unique ID string

### Community 78 - "Time Formatting"
Cohesion: 1.0
Nodes (1): Get duration formatted as MM:SS.          Returns:             Formatted duratio

### Community 79 - "Display Formatting"
Cohesion: 1.0
Nodes (1): Get the key to display (song key or recording key).          Returns:

### Community 80 - "Python Runtime"
Cohesion: 1.0
Nodes (1): Python

## Knowledge Gaps
- **352 isolated node(s):** `Stream of Worship Qwen3 Alignment Service.`, `Service configuration using pydantic-settings.`, `Qwen3 alignment service configuration.`, `Pydantic models for API requests and responses.`, `Output format options for alignment results.` (+347 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Database Schema`** (3 nodes): `SQL schema definitions for sow-app database tables.  Defines the database schema`, `schema.py`, `schema.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Analysis Routes`** (2 nodes): `API routes for the analysis service.`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Stream Init`** (2 nodes): `__init__.py`, `Stream of Worship - A seamless worship music transition system.  This package pr`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Stream Worship`** (2 nodes): `CLI entry points for Stream of Worship.`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Admin CLI`** (2 nodes): `sow-admin CLI: Administrative tools for Stream of Worship.  This package provide`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Sow-Admin Commands`** (2 nodes): `CLI commands for sow-admin.`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Stream User`** (2 nodes): `Stream of Worship User App (TUI).  Interactive Textual TUI application for worsh`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Model Loading`** (1 nodes): `Check if the model is loaded and ready.          Returns:             True if mo`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Model State`** (1 nodes): `Check if models are loaded and ready.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Model Creation`** (1 nodes): `Create from dictionary.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Client Creation`** (1 nodes): `Get or create LLM client.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Model Configuration`** (1 nodes): `Get or load Whisper model.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Service Client`** (1 nodes): `Get or create LLM client.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Configuration Loading`** (1 nodes): `Load configuration from TOML file.          Args:             path: Path to conf`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `URL Parsing`** (1 nodes): `Parse S3 URL into bucket and key.          Args:             s3_url: S3 URL like`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Response Parsing`** (1 nodes): `Parse a job response from JSON.          Args:             data: JSON response f`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Model Creation`** (1 nodes): `Create a Song from a database row tuple.          Args:             row: Databas`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Lyrics Parsing`** (1 nodes): `Get lyrics as a list of lines.          Returns:             List of lyric lines`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Recording Creation`** (1 nodes): `Create a Recording from a database row tuple.          Args:             row: Da`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Analysis Status`** (1 nodes): `Check if analysis is complete.          Returns:             True if analysis_st`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Generation Status`** (1 nodes): `Check if LRC generation is complete.          Returns:             True if lrc_s`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Publication Status`** (1 nodes): `Check if the recording is published for user visibility.          Returns:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Beats Detection`** (1 nodes): `Get beats as a list of floats.          Returns:             List of beat timest`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Time Formatting`** (1 nodes): `Get duration formatted as MM:SS.          Returns:             Formatted duratio`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Song Counting`** (1 nodes): `Get total number of songs.          Returns:             Number of songs in the`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Recording Counting`** (1 nodes): `Get total number of recordings.          Returns:             Number of recordin`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Audio State`** (1 nodes): `Check if audio is currently playing.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Playback State`** (1 nodes): `Check if audio is currently paused.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Audio Control`** (1 nodes): `Check if audio is stopped (not playing and not paused).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `File Operations`** (1 nodes): `Get the currently loaded file.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Position Tracking`** (1 nodes): `Get current playback position in seconds.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Transition Logic`** (1 nodes): `Check if this is a gap transition.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Crossfade Logic`** (1 nodes): `Check if this is a crossfade transition.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Dictionary Operations`** (1 nodes): `Create from dictionary.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Status Display`** (1 nodes): `Return status indicator.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Error Handling`** (1 nodes): `Whether error logging is enabled.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Session Logging`** (1 nodes): `Whether session logging is enabled.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Config`** (1 nodes): `Load configuration from a JSON file.          Args:             path: Path to co`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Video Processing`** (1 nodes): `Get video resolution as (width, height) tuple.          Returns:             Tup`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Display Formatting`** (1 nodes): `Get formatted display name.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Model Creation`** (1 nodes): `Create Song from dictionary.          Args:             data: Dictionary contain`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Catalog Loading`** (1 nodes): `Load catalog index from JSON file.          Args:             path: Path to cata`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Config Loading`** (1 nodes): `Load configuration from TOML file.          Args:             path: Path to conf`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Sync Status`** (1 nodes): `Check if Turso sync is configured.          Returns:             True if Turso U`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `S3 Operations`** (1 nodes): `Get R2 bucket (from environment or default).          Returns:             R2 bu`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `API Configuration`** (1 nodes): `Get R2 endpoint (from environment).          Returns:             R2 endpoint UR`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Region Configuration`** (1 nodes): `Get R2 region (from environment or default).          Returns:             R2 re`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Playback State`** (1 nodes): `Get current playback state.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Audio Control`** (1 nodes): `Check if currently playing.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `File Loading`** (1 nodes): `Get currently loaded file.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Duration Calculation`** (1 nodes): `Get duration of current file in seconds.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Position Tracking`** (1 nodes): `Get current position in seconds.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Songset Creation`** (1 nodes): `Create a Songset from a database row tuple.          Args:             row: Data`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `ID Generation`** (1 nodes): `Generate a new unique songset ID.          Returns:             Unique ID string`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Item Creation`** (1 nodes): `Create a SongsetItem from a database row tuple.          Args:             row:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Item Generation`** (1 nodes): `Generate a new unique item ID.          Returns:             Unique ID string`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Time Formatting`** (1 nodes): `Get duration formatted as MM:SS.          Returns:             Formatted duratio`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Display Formatting`** (1 nodes): `Get the key to display (song key or recording key).          Returns:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Python Runtime`** (1 nodes): `Python`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PlaybackService` connect `Configuration & Settings` to `Admin CLI Commands`, `User App TUI`, `Songset Management`, `Playback Control`, `CLI Entry Points`?**
  _High betweenness centrality (0.132) - this node is a cross-community bridge._
- **Why does `AppState` connect `User App TUI` to `Configuration & Settings`, `Admin CLI Commands`, `Songset Management`?**
  _High betweenness centrality (0.088) - this node is a cross-community bridge._
- **Why does `R2Client` connect `Admin CLI Commands` to `User App TUI`, `FastAPI Service`, `Cache Management`, `Songset Management`?**
  _High betweenness centrality (0.087) - this node is a cross-community bridge._
- **Are the 176 inferred relationships involving `PlaybackService` (e.g. with `Audio commands for sow-admin.  Provides CLI commands for downloading audio from` and `Format seconds as MM:SS.      Args:         seconds: Duration in seconds      Re`) actually correct?**
  _`PlaybackService` has 176 INFERRED edges - model-reasoned connections that need verification._
- **Are the 187 inferred relationships involving `SongsetItem` (e.g. with `AppScreen` and `AppState`) actually correct?**
  _`SongsetItem` has 187 INFERRED edges - model-reasoned connections that need verification._
- **Are the 164 inferred relationships involving `SongsetClient` (e.g. with `CLI entry point for sow-app TUI.  Provides the `sow-app` command for launching t` and `Stream of Worship User App - Manage worship songsets.`) actually correct?**
  _`SongsetClient` has 164 INFERRED edges - model-reasoned connections that need verification._
- **Are the 169 inferred relationships involving `AppState` (e.g. with `TransitionBuilderApp` and `Main TUI application for Stream of Worship.  This is the entry point for the Tex`) actually correct?**
  _`AppState` has 169 INFERRED edges - model-reasoned connections that need verification._