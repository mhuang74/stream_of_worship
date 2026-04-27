"""Platform-specific path resolution for Stream of Worship.

This module handles cross-platform path conventions for storing user data,
configuration, and cache files.

Supported Platforms:
- macOS: ~/Library/Application Support/sow/
- Linux: ~/.local/share/sow/ (XDG_DATA_HOME)
- Windows: %APPDATA%\\sow\\
"""

import os
import sys
from pathlib import Path
from typing import Optional


def get_user_data_dir() -> Path:
    """Get the platform-specific user data directory.

    Resolution order: SOW_DATA_DIR env > STREAM_OF_WORSHIP_DATA_DIR (legacy) > platform default.

    Examples:
        >>> get_user_data_dir()  # doctest: +SKIP
        Path('/home/user/.local/share/sow')  # Linux
        Path('/Users/user/Library/Application Support/sow')  # macOS
        Path('C:\\Users\\user\\AppData\\Roaming\\sow')  # Windows
    """
    if "SOW_DATA_DIR" in os.environ:
        return Path(os.environ["SOW_DATA_DIR"])

    # Legacy fallback
    if "STREAM_OF_WORSHIP_DATA_DIR" in os.environ:
        return Path(os.environ["STREAM_OF_WORSHIP_DATA_DIR"])

    if sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "sow"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            path = Path.home() / "AppData" / "Roaming" / "sow"
        else:
            path = Path(appdata) / "sow"
    else:
        # Linux and others: XDG_DATA_HOME or ~/.local/share
        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        if xdg_data_home:
            path = Path(xdg_data_home) / "sow"
        else:
            path = Path.home() / ".local" / "share" / "sow"

    return path


def get_cache_dir() -> Path:
    """Get the platform-specific cache directory for the app.

    Resolution order: SOW_CACHE_DIR env > platform default.

    Examples:
        >>> get_cache_dir()  # doctest: +SKIP
        Path('/home/user/.cache/sow')  # Linux
        Path('/Users/user/Library/Caches/sow')  # macOS
        Path('C:\\Users\\user\\AppData\\Local\\sow\\cache')  # Windows
    """
    if "SOW_CACHE_DIR" in os.environ:
        return Path(os.environ["SOW_CACHE_DIR"])

    if sys.platform == "darwin":
        path = Path.home() / "Library" / "Caches" / "sow"
    elif sys.platform == "win32":
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if not localappdata:
            path = Path.home() / "AppData" / "Local" / "sow" / "cache"
        else:
            path = Path(localappdata) / "sow" / "cache"
    else:
        # Linux and others: XDG_CACHE_HOME or ~/.cache
        xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache_home:
            path = Path(xdg_cache_home) / "sow"
        else:
            path = Path.home() / ".cache" / "sow"

    return path


def ensure_directories() -> None:
    """Ensure all required directories exist.

    Creates the following directory structure:
    - User data directory
    - Song library directory
    - Playlists directory
    - Assets directory
    - Output directory
    - Cache directory
    - Whisper cache directory

    This function is safe to call multiple times and will only create
    directories that don't already exist.
    """
    data_dir = get_user_data_dir()
    cache_dir = get_cache_dir()

    # User data directories
    directories = [
        data_dir,
        data_dir / "song_library",
        data_dir / "playlists",
        data_dir / "assets" / "backgrounds",
        data_dir / "output" / "audio",
        data_dir / "output" / "video",
        # Cache directories
        cache_dir,
        cache_dir / "whisper",
        cache_dir / "temp",
    ]

    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


def get_song_library_path() -> Path:
    """Get the path to the song library directory.

    Returns:
        Path to the song library directory.
    """
    return get_user_data_dir() / "song_library"


def get_catalog_index_path() -> Path:
    """Get the path to the catalog index JSON file.

    Returns:
        Path to the catalog_index.json file.
    """
    return get_song_library_path() / "catalog_index.json"


def get_playlists_path() -> Path:
    """Get the path to the playlists directory.

    Returns:
        Path to the playlists directory.
    """
    return get_user_data_dir() / "playlists"


def get_output_path(subdir: str = "") -> Path:
    """Get the path to the output directory.

    Args:
        subdir: Optional subdirectory (e.g., "audio", "video")

    Returns:
        Path to the output directory or subdirectory.
    """
    path = get_user_data_dir() / "output"
    if subdir:
        path = path / subdir
    return path


def get_config_path() -> Path:
    """Get the path to the config.json file.

    Returns:
        Path to the config.json file in user data directory.
    """
    return get_user_data_dir() / "config.json"


def get_whisper_cache_path() -> Path:
    """Get the path to the Whisper model cache directory.

    Returns:
        Path to the whisper cache directory.
    """
    return get_cache_dir() / "whisper"


def get_recording_cache_path(hash_prefix: str, cache_dir: Optional[Path] = None) -> Path:
    """Get the cache path for a specific recording by hash prefix.

    Args:
        hash_prefix: The recording's hash prefix (first 12 chars of content hash)
        cache_dir: Optional override cache directory; defaults to get_cache_dir()

    Returns:
        Path to the recording's cache directory.
    """
    base = cache_dir or get_cache_dir()
    return base / hash_prefix


def get_song_dir(song_id: str) -> Path:
    """Get the path to a specific song's directory.

    Args:
        song_id: The song identifier (e.g., "jiang_tian_chang_kai_209")

    Returns:
        Path to the song's directory.
    """
    return get_song_library_path() / "songs" / song_id


def get_project_root() -> Path:
    """Get the path to the project root directory.

    This is useful for accessing bundled assets like fonts.

    Returns:
        Path to the project root (contains src/, specs/, etc.)
    """
    # Start from this file's location
    path = Path(__file__).resolve()
    # Go up to src/stream_of_worship/core -> src/stream_of_worship -> src -> project
    return path.parent.parent.parent.parent


def get_bundled_font_path() -> Path:
    """Get the path to the bundled Noto Sans TC font.

    Returns:
        Path to the bundled font file.
    """
    return get_project_root() / "src" / "stream_of_worship" / "assets" / "fonts" / "NotoSansTC-Bold.ttf"
