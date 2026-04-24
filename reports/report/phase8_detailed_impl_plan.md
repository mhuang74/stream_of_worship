# Phase 8: User App (TUI) — Detailed Implementation Plan

## Overview

Build an interactive Textual TUI application (`sow-app`) for worship leaders to browse the song catalog, assemble multi-song songsets with gap transitions, preview audio, and export final audio + lyrics video files. The app reads the admin CLI's existing `songs`/`recordings` tables (read-only) and writes its own `songsets`/`songset_items` tables in the same SQLite database.

**User decisions:** Local SQLite (Turso-swappable later), gap transitions only, configurable video templates (solid/gradient/image background, fixed text per template), multi-song ordered chains with reordering, on-demand R2 asset caching, miniaudio playback, Pillow+FFmpeg video engine, MP3 audio output.

---

## Module Structure

New package at `src/stream_of_worship/app/`:

```
src/stream_of_worship/app/
├── __init__.py
├── main.py                    # CLI entry point (sow-app command)
├── config.py                  # App-specific config (cache_dir, output_dir, video settings)
├── db/
│   ├── __init__.py
│   ├── schema.py              # songsets + songset_items DDL
│   ├── models.py              # Songset, SongsetItem dataclasses
│   ├── read_client.py         # Read-only access to songs/recordings
│   └── songset_client.py      # CRUD for songsets/songset_items
├── services/
│   ├── __init__.py
│   ├── catalog.py             # Song browsing, filtering, search
│   ├── asset_cache.py         # R2 download + local cache management
│   ├── audio_engine.py        # Gap transition generation (ported from POC)
│   ├── video_engine.py        # Template rendering, LRC overlay, FFmpeg mux
│   ├── playback.py            # miniaudio playback controller
│   └── export.py              # Export orchestrator (audio + video pipeline)
├── screens/
│   ├── __init__.py
│   ├── songset_list.py        # Home screen: list/create/delete songsets
│   ├── browse.py              # Song catalog browser (modal)
│   ├── songset_editor.py      # Edit songset: reorder, add/remove, transition params
│   ├── transition_detail.py   # Per-transition parameter editing + preview
│   ├── export_progress.py     # Export progress modal
│   ├── settings.py            # App settings screen
│   └── app.tcss               # Textual CSS stylesheet
├── state.py                   # Reactive application state
└── app.py                     # Main Textual App class
```

---

## Files to Modify/Create

| File | Action | Description |
|------|--------|-------------|
| `pyproject.toml` | Modify | Add `app` optional dependency group + `sow-app` entry point |
| `src/stream_of_worship/admin/services/r2.py` | Modify | Add generic `download_file()` and `file_exists()` methods |
| `src/stream_of_worship/app/__init__.py` | **Create** | Package init |
| `src/stream_of_worship/app/main.py` | **Create** | CLI entry point |
| `src/stream_of_worship/app/config.py` | **Create** | App configuration |
| `src/stream_of_worship/app/db/__init__.py` | **Create** | DB package init |
| `src/stream_of_worship/app/db/schema.py` | **Create** | Songset tables DDL |
| `src/stream_of_worship/app/db/models.py` | **Create** | Songset/SongsetItem models |
| `src/stream_of_worship/app/db/read_client.py` | **Create** | Read-only song/recording access |
| `src/stream_of_worship/app/db/songset_client.py` | **Create** | Songset CRUD |
| `src/stream_of_worship/app/services/catalog.py` | **Create** | Catalog browsing service |
| `src/stream_of_worship/app/services/asset_cache.py` | **Create** | R2 asset cache |
| `src/stream_of_worship/app/services/audio_engine.py` | **Create** | Transition audio engine |
| `src/stream_of_worship/app/services/video_engine.py` | **Create** | Video generation engine |
| `src/stream_of_worship/app/services/playback.py` | **Create** | Audio playback |
| `src/stream_of_worship/app/services/export.py` | **Create** | Export orchestrator |
| `src/stream_of_worship/app/screens/songset_list.py` | **Create** | Home screen |
| `src/stream_of_worship/app/screens/browse.py` | **Create** | Browse modal |
| `src/stream_of_worship/app/screens/songset_editor.py` | **Create** | Songset editor |
| `src/stream_of_worship/app/screens/transition_detail.py` | **Create** | Transition detail |
| `src/stream_of_worship/app/screens/export_progress.py` | **Create** | Export progress |
| `src/stream_of_worship/app/screens/settings.py` | **Create** | Settings screen |
| `src/stream_of_worship/app/screens/app.tcss` | **Create** | TUI stylesheet |
| `src/stream_of_worship/app/state.py` | **Create** | App state management |
| `src/stream_of_worship/app/app.py` | **Create** | Main App class |
| `tests/app/` | **Create** | All app tests |

---

## Step 1: `pyproject.toml` — Add app dependency group

### 1a. New `app` extra

```toml
# User App (TUI) dependencies
app = [
    "textual>=0.44.0",
    "miniaudio>=1.59",
    "soundfile>=0.12.0",
    "numpy>=1.24.0",
    "Pillow>=10.0.0",
    "boto3>=1.34.0",
    "tomli>=2.0.0",
    "tomli-w>=1.0.0",
    "rich>=13.0.0",
]
```

### 1b. New entry point

```toml
[project.scripts]
sow-app = "stream_of_worship.app.main:cli_entry"
```

### 1c. Update `all` extra

```toml
all = [
    "stream-of-worship[scraper,lrc_generation,video,tui,song_analysis,migration,test,transcription,admin,app]",
]
```

**Note:** `ffmpeg` is a system dependency (not pip-installable). Document requirement in app startup check.

---

## Step 2: `admin/services/r2.py` — Add Generic Download Methods

Add two methods to existing `R2Client` class:

```python
def download_file(self, s3_key: str, dest_path: Path) -> Path:
    """Download any file from R2 by its S3 key.

    Args:
        s3_key: Full S3 key (e.g., "abc123def456/stems/vocals.wav")
        dest_path: Local path to save the downloaded file

    Returns:
        dest_path after download completes
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    self._client.download_file(self.bucket, s3_key, str(dest_path))
    return dest_path

def file_exists(self, s3_key: str) -> bool:
    """Check whether a file exists in R2 by its S3 key.

    Args:
        s3_key: Full S3 key

    Returns:
        True if the object exists in the bucket
    """
    try:
        self._client.head_object(Bucket=self.bucket, Key=s3_key)
        return True
    except ClientError:
        return False
```

These generic methods allow the app's `AssetCacheService` to download stems and LRC files without duplicating boto3 logic.

---

## Step 3: Database Schema — `app/db/schema.py`

### 3a. `songsets` table

```sql
CREATE TABLE IF NOT EXISTS songsets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    total_duration_seconds REAL DEFAULT 0.0,
    total_songs INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
```

### 3b. `songset_items` table

```sql
CREATE TABLE IF NOT EXISTS songset_items (
    id TEXT PRIMARY KEY,
    songset_id TEXT NOT NULL REFERENCES songsets(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    song_id TEXT NOT NULL REFERENCES songs(id),
    recording_hash_prefix TEXT NOT NULL REFERENCES recordings(hash_prefix),

    -- Section selection (JSON: {"start_section": 0, "end_section": -1})
    section_selection TEXT DEFAULT '{"start_section": 0, "end_section": -1}',

    -- Transition parameters (applied to gap AFTER this item)
    gap_beats REAL DEFAULT 1.0,
    fade_window_beats REAL DEFAULT 8.0,
    fade_bottom REAL DEFAULT 0.33,
    stems_to_fade TEXT DEFAULT '["bass", "drums", "other"]',

    -- Section boundary adjustments (beats)
    section_a_end_adjust INTEGER DEFAULT 0,
    section_b_start_adjust INTEGER DEFAULT 0,

    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),

    UNIQUE(songset_id, position)
);
```

### 3c. Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_songset_items_songset_id
ON songset_items(songset_id);

CREATE INDEX IF NOT EXISTS idx_songset_items_position
ON songset_items(songset_id, position);
```

### 3d. Triggers

```sql
CREATE TRIGGER IF NOT EXISTS trg_songsets_updated_at
AFTER UPDATE ON songsets
BEGIN
    UPDATE songsets SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_songset_items_updated_at
AFTER UPDATE ON songset_items
BEGIN
    UPDATE songset_items SET updated_at = datetime('now') WHERE id = NEW.id;
END;
```

### 3e. Schema module structure

```python
# app/db/schema.py
CREATE_SONGSETS_TABLE = """..."""
CREATE_SONGSET_ITEMS_TABLE = """..."""
CREATE_SONGSET_INDEXES = [...]
CREATE_SONGSET_TRIGGERS = [...]
ALL_APP_SCHEMA_STATEMENTS = [
    CREATE_SONGSETS_TABLE,
    CREATE_SONGSET_ITEMS_TABLE,
    *CREATE_SONGSET_INDEXES,
    *CREATE_SONGSET_TRIGGERS,
]
```

---

## Step 4: Data Models — `app/db/models.py`

### 4a. `Songset`

```python
@dataclass
class Songset:
    id: str
    name: str
    description: str = ""
    total_duration_seconds: float = 0.0
    total_songs: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: tuple) -> "Songset": ...

    def to_dict(self) -> dict[str, Any]: ...
```

### 4b. `SongsetItem`

```python
@dataclass
class SongsetItem:
    id: str
    songset_id: str
    position: int
    song_id: str
    recording_hash_prefix: str
    section_selection: str = '{"start_section": 0, "end_section": -1}'
    gap_beats: float = 1.0
    fade_window_beats: float = 8.0
    fade_bottom: float = 0.33
    stems_to_fade: str = '["bass", "drums", "other"]'
    section_a_end_adjust: int = 0
    section_b_start_adjust: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: tuple) -> "SongsetItem": ...

    def to_dict(self) -> dict[str, Any]: ...

    @property
    def section_selection_dict(self) -> dict: ...

    @property
    def stems_to_fade_list(self) -> list[str]: ...
```

---

## Step 5: Database Clients

### 5a. `app/db/read_client.py` — Read-Only Access

Wraps a `sqlite3.Connection` (from the shared DB) to read songs and recordings without modification.

```python
class ReadClient:
    """Read-only access to admin's songs and recordings tables."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._connection: Optional[sqlite3.Connection] = None

    @property
    def connection(self) -> sqlite3.Connection:
        """Lazy connection with row_factory and foreign keys."""
        ...

    def get_song(self, song_id: str) -> Optional[Song]: ...
    def list_songs(self, album: Optional[str] = None, limit: Optional[int] = None) -> list[Song]: ...
    def search_songs(self, query: str, field: str = "all", limit: int = 20) -> list[Song]: ...
    def get_recording(self, hash_prefix: str) -> Optional[Recording]: ...
    def get_recording_by_song_id(self, song_id: str) -> Optional[Recording]: ...
    def list_recordings(self, status: str = "completed") -> list[Recording]: ...
    def get_analyzed_songs(self) -> list[tuple[Song, Recording]]:
        """Return songs that have completed analysis (joined query)."""
        ...
    def close(self) -> None: ...
```

Reuses `Song` and `Recording` models from `stream_of_worship.admin.db.models`.

### 5b. `app/db/songset_client.py` — Songset CRUD

```python
class SongsetClient:
    """CRUD operations for songsets and songset_items."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._connection: Optional[sqlite3.Connection] = None

    @property
    def connection(self) -> sqlite3.Connection: ...

    def initialize_schema(self) -> None:
        """Create songsets/songset_items tables if they don't exist."""
        ...

    # Songset operations
    def create_songset(self, name: str, description: str = "") -> Songset: ...
    def get_songset(self, songset_id: str) -> Optional[Songset]: ...
    def list_songsets(self) -> list[Songset]: ...
    def update_songset(self, songset_id: str, name: str = None, description: str = None) -> None: ...
    def delete_songset(self, songset_id: str) -> None: ...

    # SongsetItem operations
    def add_item(self, songset_id: str, song_id: str, recording_hash_prefix: str,
                 position: Optional[int] = None) -> SongsetItem:
        """Add item at position (appends if None). Shifts existing items down."""
        ...

    def remove_item(self, item_id: str) -> None:
        """Remove item and reindex positions."""
        ...

    def move_item(self, item_id: str, new_position: int) -> None:
        """Move item to new position, shifting others."""
        ...

    def get_items(self, songset_id: str) -> list[SongsetItem]:
        """Get all items in a songset, ordered by position."""
        ...

    def update_item_transition(
        self, item_id: str,
        gap_beats: Optional[float] = None,
        fade_window_beats: Optional[float] = None,
        fade_bottom: Optional[float] = None,
        stems_to_fade: Optional[list[str]] = None,
        section_a_end_adjust: Optional[int] = None,
        section_b_start_adjust: Optional[int] = None,
    ) -> None: ...

    def update_item_sections(self, item_id: str, section_selection: dict) -> None: ...

    def update_songset_totals(self, songset_id: str) -> None:
        """Recalculate total_songs and total_duration_seconds."""
        ...

    def close(self) -> None: ...
```

---

## Step 6: App Configuration — `app/config.py`

Extends `AdminConfig` to add app-specific settings.

```python
@dataclass
class AppConfig:
    """App-specific configuration, layered on top of AdminConfig."""

    # Inherited from AdminConfig
    db_path: Path
    r2_bucket: str
    r2_endpoint_url: str
    r2_region: str

    # App-specific
    cache_dir: Path          # Default: ~/.config/sow-admin/cache/
    output_dir: Path         # Default: ~/Music/sow-output/
    video_resolution: tuple[int, int] = (1920, 1080)
    video_fps: int = 30
    sample_rate: int = 44100

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "AppConfig":
        """Load admin config, then overlay [app] section."""
        admin_config = AdminConfig.load(config_path)
        # Read [app] section from same TOML if present
        ...
        return cls(
            db_path=admin_config.db_path,
            r2_bucket=admin_config.r2_bucket,
            r2_endpoint_url=admin_config.r2_endpoint_url,
            r2_region=admin_config.r2_region,
            cache_dir=...,
            output_dir=...,
        )
```

TOML `[app]` section:

```toml
[app]
cache_dir = "~/.config/sow-admin/cache"
output_dir = "~/Music/sow-output"
video_resolution = [1920, 1080]
video_fps = 30
```

---

## Step 7: Services

### 7a. `services/catalog.py` — Catalog Browsing

```python
class CatalogService:
    """Browse and filter the song catalog for the TUI."""

    def __init__(self, read_client: ReadClient):
        self.read_client = read_client

    def get_available_songs(self) -> list[tuple[Song, Recording]]:
        """Get songs with completed analysis (ready for use)."""
        return self.read_client.get_analyzed_songs()

    def search(self, query: str, field: str = "all") -> list[tuple[Song, Recording]]:
        """Search songs and return with their recordings."""
        ...

    def filter_by_album(self, album: str) -> list[tuple[Song, Recording]]: ...
    def filter_by_key(self, key: str) -> list[tuple[Song, Recording]]: ...
    def get_albums(self) -> list[str]: ...
    def get_keys(self) -> list[str]: ...
```

### 7b. `services/asset_cache.py` — R2 Asset Cache

```python
class AssetCacheService:
    """Download and cache audio assets from R2."""

    def __init__(self, r2_client: R2Client, cache_dir: Path):
        self.r2_client = r2_client
        self.cache_dir = cache_dir

    def get_audio(self, hash_prefix: str) -> Path:
        """Get cached audio path, downloading from R2 if needed."""
        cached = self.cache_dir / hash_prefix / "audio.mp3"
        if cached.exists():
            return cached
        self.r2_client.download_file(f"{hash_prefix}/audio.mp3", cached)
        return cached

    def get_stems(self, hash_prefix: str) -> dict[str, Path]:
        """Get cached stem paths, downloading from R2 if needed.

        Returns:
            Dict of stem_name -> local path (e.g., {"vocals": Path(...), ...})
        """
        stems = {}
        for stem in ["vocals", "bass", "drums", "other"]:
            s3_key = f"{hash_prefix}/stems/{stem}.wav"
            local_path = self.cache_dir / hash_prefix / "stems" / f"{stem}.wav"
            if not local_path.exists():
                self.r2_client.download_file(s3_key, local_path)
            stems[stem] = local_path
        return stems

    def get_lrc(self, hash_prefix: str) -> Optional[Path]:
        """Get cached LRC file, downloading from R2 if needed."""
        s3_key = f"{hash_prefix}/lyrics.lrc"
        local_path = self.cache_dir / hash_prefix / "lyrics.lrc"
        if local_path.exists():
            return local_path
        if self.r2_client.file_exists(s3_key):
            self.r2_client.download_file(s3_key, local_path)
            return local_path
        return None

    def is_cached(self, hash_prefix: str, asset_type: str) -> bool:
        """Check if an asset is already cached locally."""
        ...

    def get_cache_size(self) -> int:
        """Get total cache size in bytes."""
        ...

    def clear_cache(self) -> None:
        """Remove all cached assets."""
        ...
```

### 7c. `services/audio_engine.py` — Gap Transition Engine

Ported from `poc/transition_builder_v2/app/services/generation.py`. Key differences:
- Reads stems from local cache paths (not POC folder structure)
- Uses `Recording` model for tempo/sections (not POC `Song` model)
- Outputs WAV intermediate (not OGG)
- No POC logger dependency

```python
STEM_TYPES = ["vocals", "bass", "drums", "other"]

def create_logarithmic_fade_out(num_samples: int, fade_bottom: float = 0.0) -> np.ndarray:
    """Logarithmic fade-out curve. Ported from POC."""
    ...

def create_logarithmic_fade_in(num_samples: int, fade_bottom: float = 0.0) -> np.ndarray:
    """Logarithmic fade-in curve. Ported from POC."""
    ...

class AudioEngine:
    """Generate gap transitions between songs using stem-based fading."""

    def __init__(self, sample_rate: int = 44100):
        self.sample_rate = sample_rate

    def load_stems(self, stem_paths: dict[str, Path]) -> dict[str, np.ndarray]:
        """Load stem WAV files into numpy arrays.

        Args:
            stem_paths: Dict of stem_name -> local file path

        Returns:
            Dict of stem_name -> stereo float32 numpy array
        """
        ...

    def apply_fade_to_stems(
        self,
        stems: dict[str, np.ndarray],
        stems_to_fade: list[str],
        fade_type: str,       # "out" or "in"
        fade_samples: int,
        at_start: bool,
        fade_bottom: float = 0.0,
    ) -> dict[str, np.ndarray]:
        """Apply logarithmic fade to specified stems. Ported from POC."""
        ...

    def mix_stems(self, stems: dict[str, np.ndarray]) -> np.ndarray:
        """Sum all stems into a single stereo mix."""
        ...

    def extract_section(
        self,
        stems: dict[str, np.ndarray],
        start_seconds: float,
        end_seconds: float,
        sr: int,
    ) -> dict[str, np.ndarray]:
        """Extract a time range from all stems."""
        ...

    def generate_gap_transition(
        self,
        stems_a: dict[str, Path],
        stems_b: dict[str, Path],
        section_a_start: float,
        section_a_end: float,
        section_b_start: float,
        section_b_end: float,
        tempo_a: float,
        tempo_b: float,
        gap_beats: float = 1.0,
        fade_window_beats: float = 8.0,
        fade_bottom: float = 0.33,
        stems_to_fade: list[str] = None,
        section_a_end_adjust: int = 0,
        section_b_start_adjust: int = 0,
    ) -> np.ndarray:
        """Generate gap transition audio as numpy array.

        Flow:
        1. Load stems for both songs
        2. Extract sections with boundary adjustments
        3. Apply fade-out to song A stems (at end)
        4. Apply fade-in to song B stems (at start)
        5. Mix stems for each song
        6. Concatenate: section_a_mix + silence + section_b_mix

        Returns:
            Stereo float32 numpy array of transition audio
        """
        ...

    def generate_full_songset(
        self,
        items: list[dict],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> np.ndarray:
        """Generate complete songset audio from ordered items.

        Args:
            items: List of dicts with keys:
                - stems: dict[str, Path]
                - sections: list[dict] with start/end times
                - section_selection: dict with start_section/end_section
                - tempo: float
                - transition: dict with gap_beats, fade_window_beats, etc.
            progress_callback: Called with (current_step, total_steps, description)

        Flow:
        For each consecutive pair (A, B):
            1. Render song A sections up to transition point
            2. Generate gap transition between A and B
            3. After last song, render remaining sections

        Returns:
            Complete songset audio as stereo float32 numpy array
        """
        ...

    def save_as_wav(self, audio: np.ndarray, output_path: Path) -> Path:
        """Save audio array to WAV file."""
        ...
```

### 7d. `services/video_engine.py` — Video Generation

```python
@dataclass
class VideoTemplate:
    """Video template configuration."""
    name: str
    background_type: str         # "solid", "gradient", "image"
    background_color: str        # Hex color (e.g., "#000000") for solid
    gradient_colors: tuple[str, str] = ("#000000", "#1a1a2e")  # For gradient
    background_image_path: Optional[Path] = None  # For image type
    font_family: str = "Arial"
    font_size: int = 48
    text_color: str = "#FFFFFF"
    text_position: str = "center"   # "center", "bottom", "top"
    highlight_color: str = "#FFD700"  # Color for current lyric line

DEFAULT_TEMPLATES = {
    "dark": VideoTemplate(name="dark", background_type="solid", background_color="#000000"),
    "gradient_blue": VideoTemplate(
        name="gradient_blue", background_type="gradient",
        gradient_colors=("#0a0a2e", "#1a1a4e")
    ),
    "gradient_warm": VideoTemplate(
        name="gradient_warm", background_type="gradient",
        gradient_colors=("#1a0a00", "#2e1a0a")
    ),
}

@dataclass
class LrcLine:
    """Parsed LRC line with timestamp."""
    timestamp_ms: int
    text: str

class VideoEngine:
    """Generate lyrics video from LRC files and audio."""

    def __init__(self, template: VideoTemplate, resolution: tuple[int, int] = (1920, 1080),
                 fps: int = 30):
        self.template = template
        self.resolution = resolution
        self.fps = fps

    def parse_lrc(self, lrc_path: Path) -> list[LrcLine]:
        """Parse LRC file into timestamped lines."""
        ...

    def render_frame(
        self,
        current_time_ms: int,
        lrc_lines: list[LrcLine],
        frame_number: int,
    ) -> Image.Image:
        """Render a single video frame with lyrics overlay.

        Shows current line highlighted, with context lines above/below.
        """
        ...

    def render_background(self) -> Image.Image:
        """Render the template background (solid, gradient, or image)."""
        ...

    def generate_video(
        self,
        audio_path: Path,
        lrc_paths: list[tuple[Path, float]],  # (lrc_path, start_offset_seconds)
        output_path: Path,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Path:
        """Generate lyrics video.

        Flow:
        1. Merge all LRC files with time offsets
        2. Calculate total frames from audio duration
        3. Open FFmpeg subprocess (stdin pipe for raw RGB frames)
        4. For each frame: render_frame() -> pipe to FFmpeg
        5. FFmpeg muxes with audio -> output MP4

        FFmpeg command:
            ffmpeg -y -f rawvideo -pix_fmt rgb24 -s WxH -r FPS -i pipe:
                   -i audio.wav -c:v libx264 -preset medium -crf 23
                   -c:a aac -b:a 192k -shortest output.mp4

        Returns:
            Path to output MP4 file
        """
        ...
```

### 7e. `services/playback.py` — Audio Playback

```python
class PlaybackState(Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"

class PlaybackController:
    """Audio playback using miniaudio."""

    def __init__(self):
        self._device: Optional[miniaudio.PlaybackDevice] = None
        self._decoder: Optional[miniaudio.DecodeCallbackGenerator] = None
        self._state: PlaybackState = PlaybackState.STOPPED
        self._position_seconds: float = 0.0
        self._duration_seconds: float = 0.0

    @property
    def state(self) -> PlaybackState: ...

    @property
    def position(self) -> float: ...

    @property
    def duration(self) -> float: ...

    def play(self, audio_path: Path) -> None:
        """Start playback of an audio file."""
        ...

    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def stop(self) -> None: ...
    def seek(self, position_seconds: float) -> None: ...
    def cleanup(self) -> None:
        """Release audio device resources."""
        ...
```

### 7f. `services/export.py` — Export Orchestrator

```python
@dataclass
class ExportOptions:
    """Options for songset export."""
    output_dir: Path
    songset_name: str
    export_audio: bool = True
    export_video: bool = True
    video_template: str = "dark"
    audio_format: str = "mp3"   # "mp3" or "wav"

@dataclass
class ExportResult:
    """Result of a songset export."""
    success: bool
    audio_path: Optional[Path] = None
    video_path: Optional[Path] = None
    duration_seconds: float = 0.0
    error_message: Optional[str] = None

class ExportService:
    """Orchestrate full songset export (audio + video)."""

    def __init__(
        self,
        audio_engine: AudioEngine,
        video_engine: VideoEngine,
        asset_cache: AssetCacheService,
        read_client: ReadClient,
        songset_client: SongsetClient,
    ):
        ...

    def export_songset(
        self,
        songset_id: str,
        options: ExportOptions,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> ExportResult:
        """Export a complete songset.

        Pipeline:
        1. Load songset + items from DB
        2. For each item: ensure stems/audio/LRC cached from R2
        3. Generate full songset audio via AudioEngine
        4. Save WAV intermediate
        5. Convert to MP3 via FFmpeg subprocess
        6. If video requested: generate video via VideoEngine
        7. Clean up intermediates
        8. Return ExportResult

        progress_callback is called with (stage_name, current, total):
            ("Downloading assets", 1, 5)
            ("Generating audio", 2, 5)
            ("Converting to MP3", 3, 5)
            ("Generating video", 4, 5)
            ("Finalizing", 5, 5)
        """
        ...

    def _convert_wav_to_mp3(self, wav_path: Path, mp3_path: Path) -> Path:
        """Convert WAV to MP3 using FFmpeg subprocess.

        Command: ffmpeg -y -i input.wav -codec:a libmp3lame -b:a 192k output.mp3
        """
        ...

    def _check_ffmpeg(self) -> bool:
        """Check if ffmpeg is available on PATH."""
        ...
```

---

## Step 8: TUI Screen Flow

```
SongsetListScreen (home)
    │
    ├── [New] ────────────────→ SongsetEditorScreen
    ├── [Select] ─────────────→ SongsetEditorScreen
    ├── [Delete] ─────────────→ Confirmation dialog
    └── [Settings] ───────────→ SettingsScreen
                                    │
SongsetEditorScreen                 │
    │                               │
    ├── [Add Song] ───────────→ BrowseScreen (modal, returns song selection)
    ├── [Move Up/Down] ───────→ Reorder in place
    ├── [Remove] ─────────────→ Remove item
    ├── [Edit Transition] ────→ TransitionDetailScreen
    ├── [Preview] ────────────→ Play transition audio (inline)
    ├── [Export] ─────────────→ ExportProgressScreen (modal)
    └── [Back] ───────────────→ SongsetListScreen

TransitionDetailScreen
    │
    ├── Adjust gap_beats, fade_window_beats, fade_bottom
    ├── Toggle stems_to_fade (vocals/bass/drums/other)
    ├── Adjust section boundaries (section_a_end_adjust, section_b_start_adjust)
    ├── [Preview] → Generate + play transition preview
    └── [Save] → Update item in DB, back to SongsetEditorScreen
```

### Screen descriptions

**SongsetListScreen** (home):
- DataTable listing all songsets: Name, Songs, Duration, Last Modified
- Keybindings: `n` new, `Enter` edit, `d` delete, `s` settings, `q` quit

**BrowseScreen** (modal):
- DataTable listing analyzed songs: Title, Album, Key, Tempo, Duration
- Search input at top, album/key filter dropdowns
- `Enter` selects song and returns to editor

**SongsetEditorScreen**:
- Header: songset name (editable)
- Ordered list of songs with position numbers
- Between each pair: transition summary (gap beats, fade, stems)
- Footer: total duration, export button

**TransitionDetailScreen**:
- Shows song A (end) → song B (start) with section info
- Sliders/inputs for gap_beats, fade_window_beats, fade_bottom
- Checkboxes for stems_to_fade
- Section boundary adjustments (+/- beats)
- Preview button generates and plays transition audio

**ExportProgressScreen** (modal):
- Progress bar with stage indicator
- Cancel button
- Shows output paths when complete

**SettingsScreen**:
- Cache directory, output directory
- Video template selection
- Cache management (size, clear button)
- FFmpeg status check

---

## Step 9: Application State — `app/state.py`

```python
@dataclass
class AppState:
    """Reactive application state."""

    # Current data
    current_songset_id: Optional[str] = None
    current_songset: Optional[Songset] = None
    current_items: list[SongsetItem] = field(default_factory=list)
    selected_item_index: int = 0

    # Catalog cache
    available_songs: list[tuple[Song, Recording]] = field(default_factory=list)

    # Playback
    playback_state: str = "stopped"
    playback_position: float = 0.0
    playback_duration: float = 0.0

    # Export
    is_exporting: bool = False
    export_stage: str = ""
    export_progress: int = 0
    export_total: int = 0
```

---

## Step 10: Main App — `app/app.py`

```python
class SowApp(App):
    """Stream of Worship — Songset Builder TUI."""

    CSS_PATH = "screens/app.tcss"
    TITLE = "Stream of Worship"
    SUB_TITLE = "Songset Builder"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.read_client = ReadClient(config.db_path)
        self.songset_client = SongsetClient(config.db_path)
        self.catalog_service = CatalogService(self.read_client)
        # R2 and asset cache initialized lazily (requires credentials)
        self._asset_cache: Optional[AssetCacheService] = None
        self._audio_engine = AudioEngine(sample_rate=config.sample_rate)
        self._playback = PlaybackController()

    def on_mount(self) -> None:
        """Initialize app and push home screen."""
        self.songset_client.initialize_schema()
        self.push_screen(SongsetListScreen())

    def on_unmount(self) -> None:
        """Cleanup resources."""
        self._playback.cleanup()
        self.read_client.close()
        self.songset_client.close()
```

---

## Step 11: Entry Point — `app/main.py`

```python
import typer

app = typer.Typer(name="sow-app", help="Stream of Worship — Songset Builder")

@app.callback(invoke_without_command=True)
def launch(
    config_path: Path = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Launch the Stream of Worship TUI application."""
    from stream_of_worship.app.config import AppConfig
    from stream_of_worship.app.app import SowApp

    config = AppConfig.load(config_path)
    tui = SowApp(config)
    tui.run()

def cli_entry() -> None:
    """Entry point for sow-app command."""
    app()
```

---

## Step 12: Tests

### 12a. `tests/app/db/test_schema.py` (~5 tests)
- Schema creates songsets table
- Schema creates songset_items table
- Foreign key references work
- Unique constraint on (songset_id, position)
- CASCADE delete removes items when songset deleted

### 12b. `tests/app/db/test_models.py` (~8 tests)
- Songset.from_row / to_dict round-trip
- SongsetItem.from_row / to_dict round-trip
- section_selection_dict property
- stems_to_fade_list property
- Default values correct
- Optional fields handle None

### 12c. `tests/app/db/test_read_client.py` (~10 tests)
- get_song returns Song
- get_song returns None for missing
- list_songs returns list
- search_songs finds by title
- get_recording returns Recording
- get_recording_by_song_id returns Recording
- list_recordings filters by status
- get_analyzed_songs returns joined results
- connection lazy initialization
- close releases connection

### 12d. `tests/app/db/test_songset_client.py` (~20 tests)
- create_songset generates UUID
- get_songset returns Songset
- list_songsets returns all
- update_songset modifies fields
- delete_songset removes songset
- delete_songset cascades to items
- add_item appends to end
- add_item inserts at position
- add_item shifts existing items
- remove_item reindexes
- move_item up shifts others
- move_item down shifts others
- move_item to same position no-op
- get_items returns ordered by position
- update_item_transition modifies transition params
- update_item_sections modifies section selection
- update_songset_totals recalculates
- initialize_schema idempotent
- foreign key constraint on song_id
- foreign key constraint on recording_hash_prefix

### 12e. `tests/app/services/test_catalog.py` (~8 tests)
- get_available_songs returns analyzed only
- search returns matching songs
- filter_by_album works
- filter_by_key works
- get_albums returns unique list
- get_keys returns unique list
- empty catalog returns empty
- search no results returns empty

### 12f. `tests/app/services/test_asset_cache.py` (~10 tests)
- get_audio downloads on miss
- get_audio returns cached on hit
- get_stems downloads all four stems
- get_stems returns cached
- get_lrc downloads on miss
- get_lrc returns None when not on R2
- is_cached returns correct state
- get_cache_size calculates total
- clear_cache removes all files
- cache directory structure correct

### 12g. `tests/app/services/test_audio_engine.py` (~20 tests)
- create_logarithmic_fade_out basic curve
- create_logarithmic_fade_out with fade_bottom
- create_logarithmic_fade_out zero samples
- create_logarithmic_fade_in basic curve
- create_logarithmic_fade_in with fade_bottom
- load_stems reads all four WAVs
- load_stems handles mono to stereo
- apply_fade_to_stems fade-out at end
- apply_fade_to_stems fade-in at start
- apply_fade_to_stems selective stems
- apply_fade_to_stems "all" stems
- mix_stems sums correctly
- extract_section correct range
- generate_gap_transition produces correct structure
- generate_gap_transition with section adjustments
- generate_gap_transition with custom stems_to_fade
- generate_gap_transition silence gap correct length
- generate_full_songset two songs
- generate_full_songset three songs
- save_as_wav writes valid file

### 12h. `tests/app/services/test_video_engine.py` (~12 tests)
- parse_lrc basic parsing
- parse_lrc multiple lines
- parse_lrc handles empty file
- render_background solid color
- render_background gradient
- render_background image
- render_frame with current line highlighted
- render_frame between lines
- VideoTemplate defaults
- DEFAULT_TEMPLATES valid
- generate_video calls ffmpeg (mock subprocess)
- LrcLine dataclass

### 12i. `tests/app/services/test_playback.py` (~8 tests)
- play starts playback (mock miniaudio)
- pause pauses
- resume resumes
- stop stops
- state transitions correct
- position updates
- cleanup releases device
- play replaces current playback

### 12j. `tests/app/services/test_export.py` (~12 tests)
- export_songset audio only
- export_songset audio + video
- export_songset downloads missing assets
- export_songset uses cached assets
- export_songset converts WAV to MP3
- export_songset progress callback called
- export_songset missing ffmpeg error
- export_songset empty songset error
- export_songset single song (no transitions)
- _convert_wav_to_mp3 correct command
- _check_ffmpeg returns True/False
- ExportResult fields correct

### 12k. `tests/app/test_config.py` (~6 tests)
- load with default values
- load with [app] section
- load inherits admin config
- cache_dir default path
- output_dir default path
- video_resolution parsing

### 12l. `tests/admin/services/test_r2.py` additions (~4 tests)
- download_file downloads by s3_key
- download_file creates parent directories
- file_exists returns True when exists
- file_exists returns False when missing

### Total new tests: ~123

---

## Sub-phases (6 incremental phases)

### Phase 8A: Foundation (~60 tests)
**Files:** `pyproject.toml`, `app/__init__.py`, `app/config.py`, `app/db/schema.py`, `app/db/models.py`, `app/db/read_client.py`, `app/db/songset_client.py`, `app/services/catalog.py`, `admin/services/r2.py` (add 2 methods)

**Tests:** test_schema (5) + test_models (8) + test_read_client (10) + test_songset_client (20) + test_catalog (8) + test_config (6) + test_r2 additions (4) = ~61

**Dependencies:** None (builds on existing admin DB)

**Verification:**
```bash
PYTHONPATH=src uv run --extra admin --extra app --extra test pytest tests/app/db/ tests/app/services/test_catalog.py tests/app/test_config.py -v
PYTHONPATH=src uv run --extra admin --extra test pytest tests/admin/services/test_r2.py -v
```

### Phase 8B: Asset Cache + Playback (~18 tests)
**Files:** `app/services/asset_cache.py`, `app/services/playback.py`

**Tests:** test_asset_cache (10) + test_playback (8) = ~18

**Dependencies:** Phase 8A (R2Client.download_file, R2Client.file_exists)

**Verification:**
```bash
PYTHONPATH=src uv run --extra admin --extra app --extra test pytest tests/app/services/test_asset_cache.py tests/app/services/test_playback.py -v
```

### Phase 8C: Audio Engine (~20 tests)
**Files:** `app/services/audio_engine.py`

**Tests:** test_audio_engine (20) = ~20

**Dependencies:** Phase 8B (asset_cache provides stem paths)

**Verification:**
```bash
PYTHONPATH=src uv run --extra admin --extra app --extra test pytest tests/app/services/test_audio_engine.py -v
```

### Phase 8D: Video Engine (~12 tests)
**Files:** `app/services/video_engine.py`

**Tests:** test_video_engine (12) = ~12

**Dependencies:** Phase 8C (needs audio output for muxing)

**Verification:**
```bash
PYTHONPATH=src uv run --extra admin --extra app --extra test pytest tests/app/services/test_video_engine.py -v
```

### Phase 8E: Export + TUI Screens (~12 tests + screens)
**Files:** `app/services/export.py`, `app/state.py`, `app/app.py`, `app/main.py`, all `app/screens/*.py`, `app/screens/app.tcss`

**Tests:** test_export (12) = ~12

**Dependencies:** Phases 8C + 8D (audio_engine, video_engine)

**Note:** TUI screens are tested manually via `sow-app` command. Automated screen tests are deferred to Phase 8F.

**Verification:**
```bash
PYTHONPATH=src uv run --extra admin --extra app --extra test pytest tests/app/services/test_export.py -v
# Manual: sow-app --config /tmp/test.toml
```

### Phase 8F: Integration + Polish (~0 automated, manual verification)
**Files:** Report updates, MEMORY.md, edge case fixes

**Tasks:**
- Run all 295 existing tests (no regressions)
- Run all ~123 new app tests
- Manual smoke tests of full TUI flow
- Update `report/current_impl_status.md`
- Update `MEMORY.md` with Phase 8 commit hash

**Verification:**
```bash
# All existing tests still pass
PYTHONPATH=src uv run --extra admin --extra test pytest tests/admin/ -v
# All new app tests pass
PYTHONPATH=src uv run --extra admin --extra app --extra test pytest tests/app/ -v
# Total: 295 + ~123 = ~418 tests
```

---

## Key Architectural Patterns

### Write-domain isolation
- **Admin CLI writes:** `songs`, `recordings`, `sync_metadata`
- **User App writes:** `songsets`, `songset_items`
- No write conflicts possible between the two applications
- SQLite WAL mode enabled for concurrent read access

### Shared database, separate clients
- Both apps use the same `.db` file at `config.db_path`
- Admin uses `DatabaseClient` (full CRUD on songs/recordings)
- App uses `ReadClient` (read-only songs/recordings) + `SongsetClient` (CRUD songsets)
- App calls `songset_client.initialize_schema()` on startup to ensure its tables exist

### R2 asset access
- App reuses `R2Client` from `stream_of_worship.admin.services.r2`
- New generic `download_file()` / `file_exists()` methods avoid duplicating boto3 logic
- `AssetCacheService` manages local cache at `~/.config/sow-admin/cache/{hash_prefix}/`

### Audio pipeline
- Stems loaded from local cache (downloaded from R2 on demand)
- Logarithmic fade curves ported directly from POC
- WAV intermediate → FFmpeg subprocess → MP3 output
- Multi-song: iterate pairs, generate per-pair transitions, concatenate all segments

### Video pipeline
- Template system: solid color, gradient, or custom background image
- LRC files parsed into timestamped lines
- Pillow renders each frame (background + lyrics overlay)
- Raw RGB frames piped to FFmpeg stdin → H.264 MP4 with AAC audio

### Progress reporting
- Long operations (export, download) report progress via callbacks
- TUI runs exports in `run_worker()` thread to keep UI responsive
- `ExportProgressScreen` shows progress bar with stage descriptions

---

## Error Handling Summary

| Scenario | Exception / Behavior | User sees |
|----------|---------------------|-----------|
| No analyzed songs in DB | Empty catalog | "No songs available. Run sow-admin catalog scrape and audio analyze first." |
| R2 credentials not set | `ValueError` from R2Client | "R2 credentials not configured. Set SOW_R2_ACCESS_KEY_ID and SOW_R2_SECRET_ACCESS_KEY." |
| R2 download fails | `ClientError` | "Failed to download {asset}. Check network and R2 credentials." |
| FFmpeg not installed | `FileNotFoundError` | "FFmpeg not found. Install FFmpeg to export audio/video." |
| Empty songset export | `ValueError` | "Cannot export empty songset. Add at least one song." |
| Single song (no transition) | Normal flow | Exports full song audio without transitions |
| Stems not available | Fallback to full mix fade | Warning: "Stems not available for {song}, using full audio fade" |
| LRC not available | Skip video generation | Warning: "LRC not available for {song}, skipping lyrics video" |
| Database locked | `sqlite3.OperationalError` | "Database is locked. Close other applications using it." |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Large WAV intermediates consume disk space | Clean up after MP3 conversion; warn if <1GB free |
| FFmpeg not installed on user's system | Check on startup, show clear install instructions |
| miniaudio compatibility across platforms | Tested on macOS/Linux; fallback to soundfile for preview |
| R2 download speed for large stem files | Progress indicator; cache aggressively; download in parallel |
| Pillow font rendering for Chinese characters | Bundle CJK font or use system fonts; document requirement |
| Video generation slow for long songsets | Show frame progress; allow audio-only export |
| SQLite concurrent access (admin + app) | WAL mode; write-domain isolation; no schema conflicts |
| Memory usage with large numpy arrays | Process one transition at a time; release arrays after concatenation |

---

## Verification Checklist

```bash
# 1. All existing tests pass (no regressions)
PYTHONPATH=src uv run --extra admin --extra test pytest tests/admin/ -v
PYTHONPATH=src uv run --extra admin --extra test pytest tests/analysis/ -v

# 2. New app tests pass
PYTHONPATH=src uv run --extra admin --extra app --extra test pytest tests/app/ -v

# 3. Manual smoke tests
sow-app --config /path/to/config.toml          # Launches TUI
sow-app --help                                   # Shows help

# 4. TUI flow verification
# - Create songset
# - Browse catalog, add 2-3 songs
# - Reorder songs (move up/down)
# - Edit transition parameters
# - Preview transition audio
# - Export audio (MP3)
# - Export video (MP4)
# - Delete songset

# 5. Edge cases
# - Export with single song (no transitions)
# - Export without R2 credentials (shows error)
# - Export without FFmpeg (shows error)
# - Empty catalog (shows message)
# - Cancel export in progress
```

---

## Implementation Order

1. `pyproject.toml` — add `app` extra + entry point
2. `admin/services/r2.py` — add `download_file()` + `file_exists()`
3. `app/db/schema.py` — songsets + songset_items DDL
4. `app/db/models.py` — Songset, SongsetItem dataclasses
5. `app/config.py` — AppConfig (extends AdminConfig)
6. `app/db/read_client.py` — read-only song/recording access
7. `app/db/songset_client.py` — songset CRUD
8. `app/services/catalog.py` — catalog browsing
9. `app/services/asset_cache.py` — R2 download + local cache
10. `app/services/playback.py` — miniaudio playback
11. `app/services/audio_engine.py` — gap transition engine
12. `app/services/video_engine.py` — Pillow + FFmpeg video
13. `app/services/export.py` — export orchestrator
14. `app/state.py` — reactive app state
15. `app/screens/` — all TUI screens + app.tcss
16. `app/app.py` — main Textual App class
17. `app/main.py` — CLI entry point
18. Tests for all modules
19. Run all tests, verify 295 existing + ~123 new = ~418 total
20. Update `report/current_impl_status.md` and `MEMORY.md`
