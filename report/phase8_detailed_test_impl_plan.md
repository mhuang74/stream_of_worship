# Phase 8 Test Implementation Plan

## Context

Phase 8 (User App TUI) has been implemented in `src/stream_of_worship/app/` but the comprehensive test suite outlined in `report/phase8_detailed_impl_plan.md` Step 12 still needs to be implemented. The target classes are documented in `report/current_impl_status.md`.

This plan covers the implementation of approximately **123 tests** across 12 test files to achieve full test coverage for the User App module.

---

## Current Implementation Status

### Already Implemented (in `src/stream_of_worship/app/`)

| Module | Classes/Functions | Status |
|--------|-------------------|--------|
| `db/schema.py` | SQL DDL strings, triggers, indexes | ✅ Implemented |
| `db/models.py` | `Songset`, `SongsetItem` dataclasses | ✅ Implemented |
| `db/read_client.py` | `ReadOnlyClient` class | ✅ Implemented |
| `db/songset_client.py` | `SongsetClient` class | ✅ Implemented |
| `services/catalog.py` | `CatalogService`, `SongWithRecording` | ✅ Implemented |
| `services/asset_cache.py` | `AssetCache`, `CacheEntry` | ✅ Implemented |
| `services/audio_engine.py` | `AudioEngine`, `AudioSegmentInfo`, `ExportResult` | ✅ Implemented |
| `services/video_engine.py` | `VideoEngine`, `LRCLine`, `VideoTemplate`, `TEMPLATES` | ✅ Implemented |
| `services/playback.py` | `PlaybackService`, `PlaybackState`, `PlaybackPosition` | ✅ Implemented |
| `services/export.py` | `ExportService`, `ExportState`, `ExportProgress`, `ExportJob` | ✅ Implemented |
| `config.py` | `AppConfig`, helper functions | ✅ Implemented |
| `state.py` | `AppState`, `AppScreen` | ✅ Implemented |

### Already Implemented in `admin/services/r2.py`
- `download_file(s3_key, dest_path)` - Generic file download
- `file_exists(s3_key)` - Generic file existence check

---

## Test Implementation Plan

### 12a. `tests/app/db/test_schema.py` (~6 tests)

**Target:** `src/stream_of_worship/app/db/schema.py`

| Test | Description |
|------|-------------|
| `test_songsets_table_created` | Verify `CREATE_SONGSETS_TABLE` executes without error |
| `test_songset_items_table_created` | Verify `CREATE_SONGSET_ITEMS_TABLE` executes without error |
| `test_foreign_key_references_work` | Verify FK constraints on song_id, recording_hash_prefix |
| `test_unique_constraint_position` | Verify UNIQUE(songset_id, position) enforces no duplicates |
| `test_cascade_delete_removes_items` | Verify deleting songset cascades to items |
| `test_updated_at_trigger_fires` | Verify trigger updates updated_at on modification |

**Key fixtures needed:**
- `db_connection` - SQLite in-memory connection with foreign keys enabled

---

### 12b. `tests/app/db/test_models.py` (~10 tests)

**Target:** `src/stream_of_worship/app/db/models.py`

#### Songset Tests
| Test | Description |
|------|-------------|
| `test_songset_from_row_basic` | Verify from_row() with minimal tuple |
| `test_songset_from_row_full` | Verify from_row() with all fields |
| `test_songset_to_dict` | Verify to_dict() returns correct dict |
| `test_songset_to_dict_roundtrip` | Verify from_row(songset.to_dict().values()) works |
| `test_songset_generate_id_format` | Verify ID format matches pattern |

#### SongsetItem Tests
| Test | Description |
|------|-------------|
| `test_songset_item_from_row_basic` | Verify from_row() with minimal tuple |
| `test_songset_item_from_row_detailed` | Verify from_row() with detailed=True |
| `test_songset_item_to_dict` | Verify to_dict() returns correct dict |
| `test_songset_item_formatted_duration` | Verify formatted_duration property |
| `test_songset_item_display_key` | Verify display_key property prioritizes recording_key |

**Key fixtures needed:**
- `sample_songset_row` - Tuple matching Songset.from_row() expectation
- `sample_songset_item_row` - Tuple matching SongsetItem.from_row() expectation
- `sample_songset_item_detailed_row` - Tuple with joined fields

---

### 12c. `tests/app/db/test_read_client.py` (~12 tests)

**Target:** `src/stream_of_worship/app/db/read_client.py`

| Test | Description |
|------|-------------|
| `test_get_song_returns_song` | Verify get_song() returns Song dataclass |
| `test_get_song_returns_none_for_missing` | Verify None returned when song not found |
| `test_list_songs_returns_list` | Verify list_songs() returns list of Songs |
| `test_list_songs_with_album_filter` | Verify album filter works |
| `test_search_songs_finds_by_title` | Verify title search matches |
| `test_search_songs_finds_by_artist` | Verify artist search matches |
| `test_get_recording_by_hash_returns_recording` | Verify hash lookup works |
| `test_get_recording_by_song_id_returns_recording` | Verify song_id lookup works |
| `test_list_recordings_filters_by_status` | Verify status filter works |
| `test_get_analyzed_songs_returns_joined_results` | Verify join query returns tuples |
| `test_connection_lazy_initialization` | Verify connection created on first access |
| `test_context_manager_closes_connection` | Verify __exit__ closes connection |

**Key fixtures needed:**
- `populated_db` - SQLite DB with sample songs/recordings inserted
- `read_client` - ReadOnlyClient instance using populated_db

**Setup requirements:**
- Insert sample data using admin db/models for consistency
- Use `stream_of_worship.admin.db.models.Song, Recording` for test data

---

### 12d. `tests/app/db/test_songset_client.py` (~18 tests)

**Target:** `src/stream_of_worship/app/db/songset_client.py`

#### Songset CRUD Tests
| Test | Description |
|------|-------------|
| `test_create_songset_generates_id` | Verify ID auto-generated |
| `test_create_songset_stores_fields` | Verify name/description stored |
| `test_get_songset_returns_songset` | Verify retrieval works |
| `test_get_songset_returns_none_for_missing` | Verify None for unknown ID |
| `test_list_songsets_returns_all` | Verify all songsets returned |
| `test_update_songset_modifies_fields` | Verify update changes stored |
| `test_update_songset_updates_timestamp` | Verify updated_at changes |
| `test_delete_songset_removes_songset` | Verify deletion works |
| `test_delete_songset_cascades_to_items` | Verify CASCADE deletes items |

#### SongsetItem Tests
| Test | Description |
|------|-------------|
| `test_add_item_appends_to_end` | Verify position auto-assigned |
| `test_add_item_inserts_at_position` | Verify explicit position respected |
| `test_add_item_shifts_existing_items` | Verify reindexing on insert |
| `test_remove_item_reindexes_positions` | Verify positions corrected after removal |
| `test_move_item_up_shifts_others` | Verify swap works for upward move |
| `test_move_item_down_shifts_others` | Verify swap works for downward move |
| `test_move_item_same_position_noop` | Verify no change when same position |
| `test_get_items_returns_ordered_by_position` | Verify ORDER BY position |
| `test_update_item_modifies_transition_params` | Verify gap_beats, crossfade stored |

#### Schema Tests
| Test | Description |
|------|-------------|
| `test_initialize_schema_idempotent` | Verify multiple calls don't error |
| `test_foreign_key_constraint_song_id` | Verify FK error on invalid song_id |
| `test_foreign_key_constraint_recording_hash` | Verify FK error on invalid recording_hash_prefix |

**Key fixtures needed:**
- `songset_client` - SongsetClient with in-memory DB
- `sample_songset` - Created songset for item tests
- `mock_song_recording` - Need to insert valid song/recording for FK constraints

---

### 12e. `tests/app/services/test_catalog.py` (~10 tests)

**Target:** `src/stream_of_worship/app/services/catalog.py`

| Test | Description |
|------|-------------|
| `test_get_song_with_recording_returns_combined` | Verify SongWithRecording creation |
| `test_get_song_with_recording_returns_none_for_missing` | Verify None when no recording |
| `test_list_songs_with_recordings_returns_analyzed_only` | Verify filters for recordings |
| `test_list_songs_with_recordings_returns_empty_when_none` | Verify empty list handling |
| `test_search_songs_finds_by_title` | Verify title search |
| `test_search_songs_finds_by_artist` | Verify artist search |
| `test_search_songs_returns_empty_when_no_match` | Verify no results handling |
| `test_list_available_albums_returns_unique` | Verify distinct albums |
| `test_list_available_keys_returns_unique` | Verify distinct keys |
| `test_get_stats_returns_correct_counts` | Verify stats calculation |

**Key fixtures needed:**
- `mock_read_client` - Mocked ReadOnlyClient with predefined return values
- `catalog_service` - CatalogService using mock_read_client

---

### 12f. `tests/app/services/test_asset_cache.py` (~12 tests)

**Target:** `src/stream_of_worship/app/services/asset_cache.py`

| Test | Description |
|------|-------------|
| `test_get_audio_path_returns_correct_path` | Verify path construction |
| `test_download_audio_creates_file` | Verify file created on download |
| `test_download_audio_uses_cached_when_exists` | Verify no re-download if cached |
| `test_get_stem_path_returns_correct_path` | Verify path construction per stem |
| `test_download_stem_creates_file` | Verify stem download works |
| `test_download_all_stems_downloads_four_stems` | Verify all stems fetched |
| `test_get_lrc_path_returns_correct_path` | Verify LRC path construction |
| `test_download_lrc_creates_file` | Verify LRC download works |
| `test_is_cached_returns_true_when_exists` | Verify cache hit detection |
| `test_is_cached_returns_false_when_missing` | Verify cache miss detection |
| `test_get_cache_size_calculates_total` | Verify size summation |
| `test_clear_cache_removes_all_files` | Verify cleanup works |

**Key fixtures needed:**
- `mock_r2_client` - Mocked R2Client
- `tmp_cache_dir` - Temporary directory for cache
- `asset_cache` - AssetCache with mocks

**Mocking strategy:**
- Mock `R2Client.download_file()` to copy test files instead of actual download
- Mock `R2Client.file_exists()` for LRC tests

---

### 12g. `tests/app/services/test_audio_engine.py` (~15 tests)

**Target:** `src/stream_of_worship/app/services/audio_engine.py`

| Test | Description |
|------|-------------|
| `test_calculate_gap_ms_from_beats` | Verify beat-to-ms conversion using tempo |
| `test_calculate_gap_ms_with_tempo` | Verify different tempos produce different gaps |
| `test_load_audio_returns_audio_segment` | Verify pydub loads correctly |
| `test_load_audio_handles_missing_file` | Verify error handling |
| `test_normalize_loudness_adjusts_gain` | Verify loudness matching |
| `test_generate_songset_audio_single_song` | Verify single song output (no transitions) |
| `test_generate_songset_audio_with_gap` | Verify gap inserted between songs |
| `test_generate_songset_audio_with_crossfade` | Verify crossfade applied |
| `test_generate_songset_audio_progress_callback` | Verify callback invoked |
| `test_preview_transition_generates_clip` | Verify transition preview created |
| `test_get_audio_info_returns_metadata` | Verify duration, channels, etc. |
| `test_export_result_dataclass` | Verify ExportResult creation |
| `test_audio_segment_info_tracks_timing` | Verify segment timing info |
| `test_generate_songset_audio_empty_list` | Verify error on empty songset |
| `test_generate_songset_audio_missing_audio_file` | Verify error when audio not cached |

**Key fixtures needed:**
- `mock_asset_cache` - Mocked AssetCache returning test MP3 paths
- `sample_mp3_file` - Actual small MP3 for pydub testing
- `audio_engine` - AudioEngine with mock cache
- `sample_songset_items` - List of SongsetItem for testing

**Note:** Tests need actual small MP3 files or mocked pydub.AudioSegment

---

### 12h. `tests/app/services/test_video_engine.py` (~10 tests)

**Target:** `src/stream_of_worship/app/services/video_engine.py`

| Test | Description |
|------|-------------|
| `test_parse_lrc_extracts_timestamps` | Verify [mm:ss.xx] format parsing |
| `test_parse_lrc_handles_empty_lines` | Verify empty line handling |
| `test_parse_lrc_handles_invalid_lines` | Verify graceful skip of invalid |
| `test_load_lrc_reads_file` | Verify file reading |
| `test_video_template_dark_exists` | Verify TEMPLATES dict has 'dark' |
| `test_video_template_gradient_warm_exists` | Verify 'gradient_warm' template |
| `test_video_template_gradient_blue_exists` | Verify 'gradient_blue' template |
| `test_get_available_templates_returns_list` | Verify template enumeration |
| `test_get_template_returns_correct_template` | Verify template lookup |
| `test_lrcline_dataclass_creation` | Verify LRCLine works |

**Key fixtures needed:**
- `sample_lrc_file` - Temporary LRC file with test content
- `sample_lrc_content` - String with LRC format content

---

### 12i. `tests/app/services/test_playback.py` (~8 tests)

**Target:** `src/stream_of_worship/app/services/playback.py`

| Test | Description |
|------|-------------|
| `test_initial_state_is_stopped` | Verify initial PlaybackState |
| `test_load_changes_state_to_ready` | Verify load transitions state |
| `test_play_transitions_to_playing` | Verify play changes state |
| `test_pause_transitions_to_paused` | Verify pause changes state |
| `test_stop_transitions_to_stopped` | Verify stop changes state |
| `test_position_callback_invoked` | Verify position updates trigger callback |
| `test_state_callback_invoked` | Verify state changes trigger callback |
| `test_seek_updates_position` | Verify seek works |

**Key fixtures needed:**
- `playback_service` - PlaybackService instance
- `sample_mp3_file` - Small MP3 for loading

**Note:** miniaudio requires actual audio files; may need to mock miniaudio for CI

---

### 12j. `tests/app/services/test_export.py` (~10 tests)

**Target:** `src/stream_of_worship/app/services/export.py`

| Test | Description |
|------|-------------|
| `test_export_state_enum_values` | Verify ExportState members |
| `test_export_progress_dataclass` | Verify ExportProgress creation |
| `test_export_job_dataclass` | Verify ExportJob creation |
| `test_export_transitions_through_states` | Verify IDLE → EXPORTING → COMPLETED |
| `test_export_async_runs_in_thread` | Verify threading used |
| `test_progress_callback_invoked` | Verify progress updates sent |
| `test_completion_callback_invoked` | Verify completion notification |
| `test_cancel_sets_cancelled_state` | Verify cancellation works |
| `test_cancel_stops_export_midway` | Verify early termination |
| `test_export_result_contains_paths` | Verify audio/video paths in result |

**Key fixtures needed:**
- `mock_asset_cache`, `mock_audio_engine`, `mock_video_engine` - All mocked
- `export_service` - ExportService with mocks
- `export_job` - Sample ExportJob configuration

---

### 12k. `tests/app/test_config.py` (~8 tests)

**Target:** `src/stream_of_worship/app/config.py`

| Test | Description |
|------|-------------|
| `test_load_creates_default_config` | Verify load() creates if missing |
| `test_load_reads_existing_config` | Verify load() reads TOML |
| `test_app_config_has_admin_config` | Verify embedded AdminConfig |
| `test_cache_dir_property` | Verify cache_dir access |
| `test_output_dir_property` | Verify output_dir access |
| `test_default_gap_beats` | Verify default value |
| `test_default_video_template` | Verify default template |
| `test_ensure_directories_creates_paths` | Verify directory creation |

**Key fixtures needed:**
- `tmp_config_dir` - Temporary config directory
- `mock_admin_config` - Patched AdminConfig.load()

---

### 12l. `tests/admin/services/test_r2.py` additions (~4 tests)

**Target:** `src/stream_of_worship/admin/services/r2.py` (new methods)

| Test | Description |
|------|-------------|
| `test_download_file_downloads_by_s3_key` | Verify download_file uses correct key |
| `test_download_file_creates_parent_directories` | Verify mkdir parents |
| `test_file_exists_returns_true_when_exists` | Verify head_object success |
| `test_file_exists_returns_false_when_missing` | Verify ClientError handling |

**Integration:** Add to existing `tests/admin/test_r2.py`

---

## File Structure to Create

```
tests/app/
├── __init__.py
├── test_config.py           # ~8 tests
├── test_integration.py      # ~5 tests (optional, for end-to-end)
├── db/
│   ├── __init__.py
│   ├── test_schema.py       # ~6 tests
│   ├── test_models.py       # ~10 tests
│   ├── test_read_client.py  # ~12 tests
│   └── test_songset_client.py # ~18 tests
└── services/
    ├── __init__.py
    ├── test_catalog.py      # ~10 tests
    ├── test_asset_cache.py  # ~12 tests
    ├── test_audio_engine.py # ~15 tests
    ├── test_video_engine.py # ~10 tests
    ├── test_playback.py     # ~8 tests
    └── test_export.py       # ~10 tests

tests/admin/services/
└── test_r2.py               # +4 tests (append to existing)
```

---

## Implementation Order

### Phase 1: Database Layer Tests (Priority: High)
1. `test_schema.py` - Foundation, other tests depend on schema
2. `test_models.py` - Pure dataclass tests, no dependencies
3. `test_read_client.py` - Needs admin models for test data
4. `test_songset_client.py` - Depends on schema and models

### Phase 2: Service Layer Tests (Priority: High)
5. `test_config.py` - Simple, needed by services
6. `test_catalog.py` - Thin wrapper, easy to test
7. `test_asset_cache.py` - Core dependency for other services

### Phase 3: Engine Tests (Priority: Medium)
8. `test_audio_engine.py` - Complex, needs audio fixtures
9. `test_video_engine.py` - LRC parsing focus
10. `test_playback.py` - May need mocking for CI

### Phase 4: Integration Tests (Priority: Medium)
11. `test_export.py` - Orchestrates other services
12. `test_r2.py` additions - Simple method additions

---

## Test Dependencies and Fixtures

### Shared Fixtures (conftest.py)

```python
# tests/app/conftest.py

import pytest
from pathlib import Path

@pytest.fixture
def tmp_db_path(tmp_path):
    """Temporary SQLite database path."""
    return tmp_path / "test.db"

@pytest.fixture
def tmp_cache_dir(tmp_path):
    """Temporary cache directory."""
    cache = tmp_path / "cache"
    cache.mkdir()
    return cache

@pytest.fixture
def tmp_output_dir(tmp_path):
    """Temporary output directory."""
    output = tmp_path / "output"
    output.mkdir()
    return output

@pytest.fixture
def sample_mp3_file(tmp_path):
    """Create a minimal valid MP3 file for testing."""
    # Use pydub to create silent MP3
    from pydub import AudioSegment
    audio = AudioSegment.silent(duration=1000)  # 1 second
    mp3_path = tmp_path / "test.mp3"
    audio.export(mp3_path, format="mp3")
    return mp3_path
```

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| miniaudio requires actual audio hardware | Mock miniaudio in CI; test real playback locally |
| FFmpeg not available in test environment | Mock subprocess calls; mark tests as optional |
| Pydub requires ffmpeg | Create MP3 fixtures manually; mock AudioSegment |
| R2 tests need credentials | Always mock R2Client; never hit real R2 |
| Test execution time with audio processing | Keep audio clips short (< 1 second) |

---

## Verification Commands

```bash
# Run all app tests
PYTHONPATH=src uv run --extra admin --extra app --extra test pytest tests/app/ -v

# Run by component
PYTHONPATH=src uv run --extra admin --extra app --extra test pytest tests/app/db/ -v
PYTHONPATH=src uv run --extra admin --extra app --extra test pytest tests/app/services/ -v

# Run with coverage
PYTHONPATH=src uv run --extra admin --extra app --extra test pytest tests/app/ --cov=src/stream_of_worship/app --cov-report=html

# Run R2 additions
PYTHONPATH=src uv run --extra admin --extra test pytest tests/admin/services/test_r2.py -v
```

---

## Success Criteria

- [ ] All 123 tests implemented and passing
- [ ] No regressions in existing 295 admin tests
- [ ] No regressions in existing 85 analysis service tests
- [ ] Test coverage for app module > 80%
- [ ] All fixtures properly isolated (no test pollution)
- [ ] CI-compatible (no hard dependencies on external services)
