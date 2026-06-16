# Graph Report - stream_of_worship  (2026-06-16)

## Corpus Check
- 369 files · ~260,001 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3455 nodes · 11724 edges · 105 communities detected
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
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 134|Community 134]]
- [[_COMMUNITY_Community 135|Community 135]]
- [[_COMMUNITY_Community 136|Community 136]]
- [[_COMMUNITY_Community 137|Community 137]]
- [[_COMMUNITY_Community 138|Community 138]]
- [[_COMMUNITY_Community 139|Community 139]]
- [[_COMMUNITY_Community 140|Community 140]]
- [[_COMMUNITY_Community 141|Community 141]]
- [[_COMMUNITY_Community 142|Community 142]]
- [[_COMMUNITY_Community 143|Community 143]]
- [[_COMMUNITY_Community 203|Community 203]]
- [[_COMMUNITY_Community 204|Community 204]]
- [[_COMMUNITY_Community 205|Community 205]]
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
- [[_COMMUNITY_Community 226|Community 226]]
- [[_COMMUNITY_Community 227|Community 227]]
- [[_COMMUNITY_Community 228|Community 228]]
- [[_COMMUNITY_Community 229|Community 229]]
- [[_COMMUNITY_Community 230|Community 230]]
- [[_COMMUNITY_Community 231|Community 231]]
- [[_COMMUNITY_Community 232|Community 232]]
- [[_COMMUNITY_Community 233|Community 233]]
- [[_COMMUNITY_Community 234|Community 234]]
- [[_COMMUNITY_Community 236|Community 236]]
- [[_COMMUNITY_Community 237|Community 237]]
- [[_COMMUNITY_Community 238|Community 238]]
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
- `completeRenderJob()` --calls--> `transaction()`  [INFERRED]
  webapp/src/lib/render/job-manager.ts → src/stream_of_worship/app/db/songset_client.py
- `_check_memory_pressure()` --calls--> `open()`  [INFERRED]
  services/render-worker/src/sow_render_worker/video_engine.py → webapp/src/components/songset/TransitionPanel.tsx
- `_write_lrc()` --calls--> `open()`  [INFERRED]
  services/analysis/src/sow_analysis/workers/lrc.py → webapp/src/components/songset/TransitionPanel.tsx

## Communities

### Community 0 - "Community 0"
Cohesion: 0.02
Nodes (318): Main TUI application for Stream of Worship User App.  Textual-based application, Handle app mount event., Wire up the per-user ``SongsetClient`` and continue to the list.          Called, Force reconnection to the Postgres catalog database (Shift+S).          Useful a, Create a fresh screen instance.          Creates a new screen instance on each c, Check if a Textual screen instance matches a given AppScreen enum value., Navigate to a screen.          Args:             screen: Screen to navigate to, Navigate back to the previous screen. (+310 more)

### Community 1 - "Community 1"
Cohesion: 0.02
Nodes (317): AdminConfig, Configuration for sow-admin CLI.      Attributes:         analysis_url: URL of t, align_lrc_recording(), _cancel_all_jobs(), cancel_jobs(), _cancel_single_job(), _colorize_status(), _get_alignment_lyrics_text() (+309 more)

### Community 2 - "Community 2"
Cohesion: 0.02
Nodes (202): Config, Update configuration values.          Args:             **kwargs: Key-value pair, Get lyrics look-ahead time in seconds based on BPM.          Args:             b, Configuration for Stream of Worship application., Core utilities for Stream of Worship., DataTable, Convert SongsetItem to dictionary.          Args:             include_joined: Wh, Enum (+194 more)

### Community 3 - "Community 3"
Cohesion: 0.03
Nodes (217): BaseModel, BaseSettings, check_cache_access(), check_embedding_connection(), check_llm_connection(), check_r2_connection(), health_check(), Health check endpoint. (+209 more)

### Community 4 - "Community 4"
Cohesion: 0.01
Nodes (146): Get a configuration value by key.          Supports dot notation mapping to flat, from_row(), Data models for Better Auth identity entities.  Shared between admin (``sow-admi, Shared database helper functions.  Provides common utilities for database operat, Coerce a value to an ISO-8601 string, handling datetime objects.      psycopg3 r, to_str(), List all users, ordered by ID ascending (creation order)., Exception (+138 more)

### Community 5 - "Community 5"
Cohesion: 0.02
Nodes (158): analyze_recording(), batch(), _build_fresh_editor_state(), cache_assets(), check_status(), _colorize_visibility(), _compute_content_hash(), _confirm_r2_lrc() (+150 more)

### Community 6 - "Community 6"
Cohesion: 0.02
Nodes (148): cli_entry(), config(), main(), Main entry point for sow-admin CLI.  Provides a Typer-based CLI for managing Str, Entry point for the CLI application., Callback for --version flag., sow-admin: Administrative tools for Stream of Worship.      Manage song catalogs, Manage configuration.      Show, set, or display the path to the configuration f (+140 more)

### Community 7 - "Community 7"
Cohesion: 0.02
Nodes (95): GET(), Set a configuration value by key.          Supports dot notation mapping to flat, GET(), buildPublishedRecordingExistsClause(), buildSongWhereClause(), findTopMatchingLines(), getAlbums(), getSong() (+87 more)

### Community 8 - "Community 8"
Cohesion: 0.02
Nodes (94): App, ensure_app_config_exists(), get_app_config_dir(), get_app_config_path(), _key_to_attr(), load(), Configuration management for sow-app TUI.  Manages app-specific settings for ass, Return a Postgres DSN with password injected from env var.          The ``databa (+86 more)

### Community 9 - "Community 9"
Cohesion: 0.04
Nodes (109): LRCWorkerError, _bucket_to_phrase(), _canonical_lines(), convert(), _FallbackFuzz, _normalize(), _phrases_from_words(), ratio() (+101 more)

### Community 10 - "Community 10"
Cohesion: 0.05
Nodes (15): Textual application for the admin LRC editor.  Launches the interactive LRC edit, Admin LRC editor Textual application., LRCEditorScreen, UndoEntry, format_centiseconds(), Format seconds to ``[mm:ss.xx]`` centisecond string.      Uses centisecond round, Pause playback.          Returns:             True if successful, False otherwis, Update state and notify listeners. (+7 more)

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

### Community 20 - "Community 20"
Cohesion: 0.33
Nodes (2): formatBytes(), getFileSizeDisplay()

### Community 22 - "Community 22"
Cohesion: 0.53
Nodes (4): mockEmptyShares(), mockSizes(), openSendFileTab(), renderDialog()

### Community 26 - "Community 26"
Cohesion: 0.47
Nodes (4): resolvePublicOrigin(), activeShareConditions(), GET(), POST()

### Community 27 - "Community 27"
Cohesion: 0.4
Nodes (2): _BindingGroup, Grouped footer widget for the LRC editor.  Displays key bindings organized into

### Community 28 - "Community 28"
Cohesion: 0.6
Nodes (3): downloadArtifact(), downloadArtifactViaProxy(), fetchSignedUrlAndDownload()

### Community 29 - "Community 29"
Cohesion: 0.5
Nodes (2): buildLrc(), LyricsTimingEditor()

### Community 38 - "Community 38"
Cohesion: 0.67
Nodes (2): fetchSettings(), loadSettings()

### Community 39 - "Community 39"
Cohesion: 0.67
Nodes (2): DELETE(), GET()

### Community 40 - "Community 40"
Cohesion: 0.67
Nodes (2): DELETE(), GET()

### Community 41 - "Community 41"
Cohesion: 0.5
Nodes (2): fullTextSearchSongs(), GET()

### Community 42 - "Community 42"
Cohesion: 0.5
Nodes (2): Get the total size of cached files in bytes.          Args:             hash_pre, Get the total size of cached files in MB.          Args:             hash_prefix

### Community 43 - "Community 43"
Cohesion: 0.5
Nodes (2): Initialize the asset cache.          Args:             cache_dir: Base directory, Ensure the cache directory exists.

### Community 50 - "Community 50"
Cohesion: 1.0
Nodes (2): gapToSeconds(), TransitionControls()

### Community 57 - "Community 57"
Cohesion: 1.0
Nodes (2): loadSongsets(), transformSongsets()

### Community 58 - "Community 58"
Cohesion: 1.0
Nodes (2): handleSubmit(), validate()

### Community 59 - "Community 59"
Cohesion: 1.0
Nodes (2): handleSubmit(), validate()

### Community 60 - "Community 60"
Cohesion: 1.0
Nodes (2): DELETE(), GET()

### Community 61 - "Community 61"
Cohesion: 0.67
Nodes (1): Connection health checker for sow-app.  Re-exports the shared check_database_con

### Community 62 - "Community 62"
Cohesion: 0.67
Nodes (2): Initialize playback service., Initialize the playback service.          Args:             buffer_ms: Audio buf

### Community 63 - "Community 63"
Cohesion: 0.67
Nodes (1): SQL schema definitions for sow-app database tables (PostgreSQL).  Defines the da

### Community 64 - "Community 64"
Cohesion: 0.67
Nodes (1): handleSeek()

### Community 134 - "Community 134"
Cohesion: 2.0
Nodes (1): API routes for the analysis service.

### Community 135 - "Community 135"
Cohesion: 1.0
Nodes (1): Stream of Worship - A seamless worship music transition system.  This package pr

### Community 136 - "Community 136"
Cohesion: 1.0
Nodes (1): CLI entry points for Stream of Worship.

### Community 137 - "Community 137"
Cohesion: 1.0
Nodes (1): sow-admin CLI: Administrative tools for Stream of Worship.  This package provide

### Community 138 - "Community 138"
Cohesion: 1.0
Nodes (1): CLI commands for sow-admin.

### Community 139 - "Community 139"
Cohesion: 1.0
Nodes (1): Admin interactive LRC editor package.

### Community 140 - "Community 140"
Cohesion: 1.0
Nodes (1): SQL schema for Better Auth core tables.  Defines the canonical Better Auth schem

### Community 141 - "Community 141"
Cohesion: 1.0
Nodes (1): Unified PostgreSQL schema for Stream of Worship.  Combines catalog (songs, recor

### Community 142 - "Community 142"
Cohesion: 1.0
Nodes (1): Stream of Worship User App (TUI).  Interactive Textual TUI application for worsh

### Community 143 - "Community 143"
Cohesion: 1.0
Nodes (1): SQL schema for per-user app tables.  Tables we own that are scoped to a user via

### Community 203 - "Community 203"
Cohesion: 1.0
Nodes (1): Ensure concurrent jobs is at least 1 to prevent deadlock.

### Community 204 - "Community 204"
Cohesion: 1.0
Nodes (1): Convert empty-string env vars to None for Optional[int] fields.          pydanti

### Community 205 - "Community 205"
Cohesion: 1.0
Nodes (1): Check if models are validated and ready.

### Community 206 - "Community 206"
Cohesion: 1.0
Nodes (1): Check if MVSEP is available for use.          Returns:             True when ena

### Community 207 - "Community 207"
Cohesion: 1.0
Nodes (1): Create from dictionary.

### Community 208 - "Community 208"
Cohesion: 1.0
Nodes (1): Get or create LLM client.

### Community 209 - "Community 209"
Cohesion: 1.0
Nodes (1): Get or load Whisper model.

### Community 210 - "Community 210"
Cohesion: 1.0
Nodes (1): Get or create LLM client.

### Community 211 - "Community 211"
Cohesion: 1.0
Nodes (1): Load configuration from TOML file.          Args:             path: Path to conf

### Community 212 - "Community 212"
Cohesion: 1.0
Nodes (1): Convert dot-notation key to attribute name.          Maps TOML section paths to

### Community 213 - "Community 213"
Cohesion: 1.0
Nodes (1): Parse a job response from JSON.          Args:             data: JSON response f

### Community 214 - "Community 214"
Cohesion: 1.0
Nodes (1): Create a Song from a database row tuple.          Args:             row: Databas

### Community 215 - "Community 215"
Cohesion: 1.0
Nodes (1): Get lyrics as a list of lines.          Returns:             List of lyric lines

### Community 216 - "Community 216"
Cohesion: 1.0
Nodes (1): Create a Recording from a database row tuple.          Args:             row: Da

### Community 217 - "Community 217"
Cohesion: 1.0
Nodes (1): Check if analysis is complete.          Returns:             True if analysis_st

### Community 218 - "Community 218"
Cohesion: 1.0
Nodes (1): Check if LRC generation is complete.          Returns:             True if lrc_s

### Community 219 - "Community 219"
Cohesion: 1.0
Nodes (1): Check if the recording is published for user visibility.          Returns:

### Community 220 - "Community 220"
Cohesion: 1.0
Nodes (1): Get beats as a list of floats.          Returns:             List of beat timest

### Community 221 - "Community 221"
Cohesion: 1.0
Nodes (1): Get duration formatted as MM:SS.          Returns:             Formatted duratio

### Community 222 - "Community 222"
Cohesion: 1.0
Nodes (1): Get total number of songs.          Returns:             Number of songs in the

### Community 223 - "Community 223"
Cohesion: 1.0
Nodes (1): Get total number of recordings.          Returns:             Number of recordin

### Community 226 - "Community 226"
Cohesion: 1.0
Nodes (1): Check if audio is currently playing.

### Community 227 - "Community 227"
Cohesion: 1.0
Nodes (1): Check if audio is currently paused.

### Community 228 - "Community 228"
Cohesion: 1.0
Nodes (1): Check if audio is stopped (not playing and not paused).

### Community 229 - "Community 229"
Cohesion: 1.0
Nodes (1): Get the currently loaded file.

### Community 230 - "Community 230"
Cohesion: 1.0
Nodes (1): Get current playback position in seconds.

### Community 231 - "Community 231"
Cohesion: 1.0
Nodes (1): Check if this is a gap transition.

### Community 232 - "Community 232"
Cohesion: 1.0
Nodes (1): Check if this is a crossfade transition.

### Community 233 - "Community 233"
Cohesion: 1.0
Nodes (1): Create from dictionary.

### Community 234 - "Community 234"
Cohesion: 1.0
Nodes (1): Return status indicator.

### Community 236 - "Community 236"
Cohesion: 1.0
Nodes (1): Whether error logging is enabled.

### Community 237 - "Community 237"
Cohesion: 1.0
Nodes (1): Whether session logging is enabled.

### Community 238 - "Community 238"
Cohesion: 1.0
Nodes (1): Create a User from a ``"user"`` table row.          Args:             row: Row t

### Community 239 - "Community 239"
Cohesion: 1.0
Nodes (1): Load configuration from a JSON file.          Args:             path: Path to co

### Community 240 - "Community 240"
Cohesion: 1.0
Nodes (1): Get video resolution as (width, height) tuple.          Returns:             Tup

### Community 241 - "Community 241"
Cohesion: 1.0
Nodes (1): Get formatted display name.

### Community 242 - "Community 242"
Cohesion: 1.0
Nodes (1): Create Song from dictionary.          Args:             data: Dictionary contain

### Community 243 - "Community 243"
Cohesion: 1.0
Nodes (1): Load catalog index from JSON file.          Args:             path: Path to cata

### Community 244 - "Community 244"
Cohesion: 1.0
Nodes (1): Cache directory - always at standard platform location.

### Community 245 - "Community 245"
Cohesion: 1.0
Nodes (1): Log directory - derived from working_dir.

### Community 246 - "Community 246"
Cohesion: 1.0
Nodes (1): Output directory - derived from working_dir.

### Community 247 - "Community 247"
Cohesion: 1.0
Nodes (1): Songset backup directory - derived from working_dir.

### Community 248 - "Community 248"
Cohesion: 1.0
Nodes (1): Deprecated: Use songsets_backup_dir instead.

### Community 249 - "Community 249"
Cohesion: 1.0
Nodes (1): Load configuration from TOML file.          Args:             path: Path to conf

### Community 250 - "Community 250"
Cohesion: 1.0
Nodes (1): Convert dot-notation key to attribute name.          Maps TOML section paths to

### Community 251 - "Community 251"
Cohesion: 1.0
Nodes (1): Get current playback state.

### Community 252 - "Community 252"
Cohesion: 1.0
Nodes (1): Check if currently playing.

### Community 253 - "Community 253"
Cohesion: 1.0
Nodes (1): Get currently loaded file.

### Community 254 - "Community 254"
Cohesion: 1.0
Nodes (1): Get duration of current file in seconds.

### Community 255 - "Community 255"
Cohesion: 1.0
Nodes (1): Get current position in seconds.

### Community 256 - "Community 256"
Cohesion: 1.0
Nodes (1): Create a Songset from a database row tuple.          Args:             row: Data

### Community 257 - "Community 257"
Cohesion: 1.0
Nodes (1): Generate a new unique songset ID.          Returns:             Unique ID string

### Community 258 - "Community 258"
Cohesion: 1.0
Nodes (1): Create a SongsetItem from a database row tuple.          Args:             row:

### Community 259 - "Community 259"
Cohesion: 1.0
Nodes (1): Generate a new unique item ID.          Returns:             Unique ID string.

### Community 260 - "Community 260"
Cohesion: 1.0
Nodes (1): Get duration formatted as MM:SS.          Returns:             Formatted duratio

### Community 261 - "Community 261"
Cohesion: 1.0
Nodes (1): Get the key to display (song key or recording key).          Returns:

## Knowledge Gaps
- **405 isolated node(s):** `Run a command and return (success, output, error)`, `Fetch all song IDs from catalog, optionally filtered by album`, `Fetch all song IDs that have audio recordings`, `Get songs from catalog that don't have audio yet, optionally filtered by album`, `Extract job ID from command output` (+400 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 15`** (13 nodes): `formatDurationSafe()`, `handleConfirmRender()`, `handleSubmit()`, `isDifferent()`, `isIOS174OrLater()`, `updateField()`, `formatDuration()`, `formatTotalDuration()`, `handlePlay()`, `loadShare()`, `renderUnavailableMessage()`, `page.tsx`, `RenderForm.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 18`** (10 nodes): `Badge()`, `cn()`, `Sheet()`, `SheetClose()`, `SheetDescription()`, `SheetPortal()`, `SheetTitle()`, `SheetTrigger()`, `badge.tsx`, `sheet.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 20`** (7 nodes): `formatBytes()`, `formatLimit()`, `formatShareDuration()`, `getFileSizeDisplay()`, `isAboveLimit()`, `loadData()`, `ShareDialog.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 27`** (6 nodes): `_BindingGroup`, `._format_content()`, `.__init__()`, `.compose()`, `Grouped footer widget for the LRC editor.  Displays key bindings organized into`, `footer.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 29`** (5 nodes): `buildLrc()`, `lrcTimestampToSeconds()`, `LyricsTimingEditor()`, `secondsToLrcTimestamp()`, `LyricsTimingEditor.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 38`** (4 nodes): `fetchSettings()`, `handleSave()`, `loadSettings()`, `page.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 39`** (4 nodes): `DELETE()`, `GET()`, `POST()`, `route.ts`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 40`** (4 nodes): `DELETE()`, `GET()`, `POST()`, `route.ts`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 41`** (4 nodes): `fullTextSearchSongs()`, `GET()`, `route.ts`, `search.ts`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 42`** (4 nodes): `.get_cache_size()`, `.get_cache_size_mb()`, `Get the total size of cached files in bytes.          Args:             hash_pre`, `Get the total size of cached files in MB.          Args:             hash_prefix`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 43`** (4 nodes): `._ensure_cache_dir()`, `.__init__()`, `Initialize the asset cache.          Args:             cache_dir: Base directory`, `Ensure the cache directory exists.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 50`** (3 nodes): `gapToSeconds()`, `TransitionControls()`, `TransitionControls.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 57`** (3 nodes): `loadSongsets()`, `transformSongsets()`, `SongsetsClient.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 58`** (3 nodes): `handleSubmit()`, `validate()`, `page.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 59`** (3 nodes): `handleSubmit()`, `validate()`, `page.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 60`** (3 nodes): `DELETE()`, `GET()`, `route.ts`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 61`** (3 nodes): `Connection health checker for sow-app.  Re-exports the shared check_database_con`, `sync.py`, `sync.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 62`** (3 nodes): `.__init__()`, `Initialize playback service.`, `Initialize the playback service.          Args:             buffer_ms: Audio buf`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 63`** (3 nodes): `SQL schema definitions for sow-app database tables (PostgreSQL).  Defines the da`, `schema.py`, `schema.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 64`** (3 nodes): `handleSeek()`, `handleVolumeChange()`, `AudioPlayerBar.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 134`** (2 nodes): `API routes for the analysis service.`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 135`** (2 nodes): `__init__.py`, `Stream of Worship - A seamless worship music transition system.  This package pr`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 136`** (2 nodes): `CLI entry points for Stream of Worship.`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 137`** (2 nodes): `sow-admin CLI: Administrative tools for Stream of Worship.  This package provide`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 138`** (2 nodes): `CLI commands for sow-admin.`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 139`** (2 nodes): `Admin interactive LRC editor package.`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 140`** (2 nodes): `SQL schema for Better Auth core tables.  Defines the canonical Better Auth schem`, `auth_schema.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 141`** (2 nodes): `Unified PostgreSQL schema for Stream of Worship.  Combines catalog (songs, recor`, `postgres_schema.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 142`** (2 nodes): `Stream of Worship User App (TUI).  Interactive Textual TUI application for worsh`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 143`** (2 nodes): `SQL schema for per-user app tables.  Tables we own that are scoped to a user via`, `user_data_schema.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 203`** (1 nodes): `Ensure concurrent jobs is at least 1 to prevent deadlock.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 204`** (1 nodes): `Convert empty-string env vars to None for Optional[int] fields.          pydanti`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 205`** (1 nodes): `Check if models are validated and ready.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 206`** (1 nodes): `Check if MVSEP is available for use.          Returns:             True when ena`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 207`** (1 nodes): `Create from dictionary.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 208`** (1 nodes): `Get or create LLM client.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 209`** (1 nodes): `Get or load Whisper model.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 210`** (1 nodes): `Get or create LLM client.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 211`** (1 nodes): `Load configuration from TOML file.          Args:             path: Path to conf`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 212`** (1 nodes): `Convert dot-notation key to attribute name.          Maps TOML section paths to`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 213`** (1 nodes): `Parse a job response from JSON.          Args:             data: JSON response f`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 214`** (1 nodes): `Create a Song from a database row tuple.          Args:             row: Databas`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 215`** (1 nodes): `Get lyrics as a list of lines.          Returns:             List of lyric lines`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 216`** (1 nodes): `Create a Recording from a database row tuple.          Args:             row: Da`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 217`** (1 nodes): `Check if analysis is complete.          Returns:             True if analysis_st`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 218`** (1 nodes): `Check if LRC generation is complete.          Returns:             True if lrc_s`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 219`** (1 nodes): `Check if the recording is published for user visibility.          Returns:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 220`** (1 nodes): `Get beats as a list of floats.          Returns:             List of beat timest`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 221`** (1 nodes): `Get duration formatted as MM:SS.          Returns:             Formatted duratio`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 222`** (1 nodes): `Get total number of songs.          Returns:             Number of songs in the`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 223`** (1 nodes): `Get total number of recordings.          Returns:             Number of recordin`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 226`** (1 nodes): `Check if audio is currently playing.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 227`** (1 nodes): `Check if audio is currently paused.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 228`** (1 nodes): `Check if audio is stopped (not playing and not paused).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 229`** (1 nodes): `Get the currently loaded file.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 230`** (1 nodes): `Get current playback position in seconds.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 231`** (1 nodes): `Check if this is a gap transition.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 232`** (1 nodes): `Check if this is a crossfade transition.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 233`** (1 nodes): `Create from dictionary.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 234`** (1 nodes): `Return status indicator.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 236`** (1 nodes): `Whether error logging is enabled.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 237`** (1 nodes): `Whether session logging is enabled.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 238`** (1 nodes): `Create a User from a ``"user"`` table row.          Args:             row: Row t`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 239`** (1 nodes): `Load configuration from a JSON file.          Args:             path: Path to co`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 240`** (1 nodes): `Get video resolution as (width, height) tuple.          Returns:             Tup`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 241`** (1 nodes): `Get formatted display name.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 242`** (1 nodes): `Create Song from dictionary.          Args:             data: Dictionary contain`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 243`** (1 nodes): `Load catalog index from JSON file.          Args:             path: Path to cata`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 244`** (1 nodes): `Cache directory - always at standard platform location.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 245`** (1 nodes): `Log directory - derived from working_dir.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 246`** (1 nodes): `Output directory - derived from working_dir.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 247`** (1 nodes): `Songset backup directory - derived from working_dir.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 248`** (1 nodes): `Deprecated: Use songsets_backup_dir instead.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 249`** (1 nodes): `Load configuration from TOML file.          Args:             path: Path to conf`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 250`** (1 nodes): `Convert dot-notation key to attribute name.          Maps TOML section paths to`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 251`** (1 nodes): `Get current playback state.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 252`** (1 nodes): `Check if currently playing.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 253`** (1 nodes): `Get currently loaded file.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 254`** (1 nodes): `Get duration of current file in seconds.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 255`** (1 nodes): `Get current position in seconds.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 256`** (1 nodes): `Create a Songset from a database row tuple.          Args:             row: Data`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 257`** (1 nodes): `Generate a new unique songset ID.          Returns:             Unique ID string`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 258`** (1 nodes): `Create a SongsetItem from a database row tuple.          Args:             row:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 259`** (1 nodes): `Generate a new unique item ID.          Returns:             Unique ID string.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 260`** (1 nodes): `Get duration formatted as MM:SS.          Returns:             Formatted duratio`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 261`** (1 nodes): `Get the key to display (song key or recording key).          Returns:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PlaybackService` connect `Community 0` to `Community 1`, `Community 2`, `Community 5`, `Community 10`, `Community 14`, `Community 62`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Why does `R2Client` connect `Community 1` to `Community 0`, `Community 2`, `Community 3`, `Community 4`, `Community 5`, `Community 10`, `Community 43`, `Community 42`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Why does `AssetCache` connect `Community 0` to `Community 1`, `Community 4`, `Community 5`, `Community 42`, `Community 43`?**
  _High betweenness centrality (0.040) - this node is a cross-community bridge._
- **Are the 244 inferred relationships involving `PlaybackService` (e.g. with `Audio commands for sow-admin.  Provides CLI commands for downloading audio from` and `Format seconds as MM:SS.      Args:         seconds: Duration in seconds      Re`) actually correct?**
  _`PlaybackService` has 244 INFERRED edges - model-reasoned connections that need verification._
- **Are the 210 inferred relationships involving `AssetCache` (e.g. with `Audio commands for sow-admin.  Provides CLI commands for downloading audio from` and `Format seconds as MM:SS.      Args:         seconds: Duration in seconds      Re`) actually correct?**
  _`AssetCache` has 210 INFERRED edges - model-reasoned connections that need verification._
- **Are the 199 inferred relationships involving `AppState` (e.g. with `TransitionBuilderApp` and `Main TUI application for Stream of Worship.  This is the entry point for the Tex`) actually correct?**
  _`AppState` has 199 INFERRED edges - model-reasoned connections that need verification._
- **Are the 205 inferred relationships involving `SongsetItem` (e.g. with `AppScreen` and `AppState`) actually correct?**
  _`SongsetItem` has 205 INFERRED edges - model-reasoned connections that need verification._