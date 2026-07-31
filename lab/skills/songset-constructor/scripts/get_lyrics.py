#!/usr/bin/env python3
"""Retrieve LRC lyrics for a specific recording from R2 or DB.

Usage:
    python get_lyrics.py --hash-prefix a1b2c3d4e5f6
    python get_lyrics.py --song-id abc123 --source raw
    python get_lyrics.py --hash-prefix a1b2c3d4e5f6 --source auto

Output: LRC or raw lyrics text (UTF-8) to stdout.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ADMIN_CLI_SRC = PROJECT_ROOT / "ops" / "admin-cli" / "src"
if str(ADMIN_CLI_SRC) not in sys.path:
    sys.path.insert(0, str(ADMIN_CLI_SRC))


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieve LRC lyrics for a recording")
    parser.add_argument("--hash-prefix", type=str, default=None, help="Recording hash prefix (12-char hex)")
    parser.add_argument("--song-id", type=str, default=None, help="Song ID (alternative lookup)")
    parser.add_argument(
        "--source",
        type=str,
        default="auto",
        choices=["lrc", "raw", "auto"],
        help="Source: lrc (R2), raw (DB lyrics_raw), or auto (try LRC first, fall back to raw)",
    )
    args = parser.parse_args()

    if not args.hash_prefix and not args.song_id:
        print("Error: --hash-prefix or --song-id is required", file=sys.stderr)
        sys.exit(1)

    from stream_of_worship.admin.config import AdminConfig
    from stream_of_worship.db.app.read_client import ReadOnlyClient
    from stream_of_worship.db.connection import ConnectionProvider

    config = AdminConfig.load()
    connection_url = config.get_connection_url()
    provider = ConnectionProvider(connection_url)
    read_client = ReadOnlyClient(provider)

    try:
        song_id = args.song_id

        # Resolve song_id from hash_prefix if needed
        if not song_id and args.hash_prefix:
            recording = read_client.get_recording_by_hash(args.hash_prefix)
            if recording is None:
                print(f"Error: No recording found for hash prefix: {args.hash_prefix}", file=sys.stderr)
                sys.exit(1)
            song_id = recording.song_id

        if not song_id:
            print("Error: Could not resolve song ID", file=sys.stderr)
            sys.exit(1)

        # Try LRC from R2 first (if source is lrc or auto)
        if args.source in ("lrc", "auto"):
            lrc_content = _try_r2_lrc(config, args.hash_prefix, song_id, read_client)
            if lrc_content is not None:
                sys.stdout.write(lrc_content)
                if not lrc_content.endswith("\n"):
                    sys.stdout.write("\n")
                provider.close()
                return
            if args.source == "lrc":
                print(f"Error: No LRC content found in R2 for {args.hash_prefix or song_id}", file=sys.stderr)
                sys.exit(1)

        # Fall back to raw lyrics from DB
        if args.source in ("raw", "auto"):
            song = read_client.get_song(song_id)
            if song and song.lyrics_raw:
                sys.stdout.write(song.lyrics_raw)
                if not song.lyrics_raw.endswith("\n"):
                    sys.stdout.write("\n")
                provider.close()
                return
            print(f"Error: No lyrics found for song {song_id}", file=sys.stderr)
            sys.exit(1)
    finally:
        provider.close()


def _try_r2_lrc(
    config: "AdminConfig",
    hash_prefix: str | None,
    song_id: str,
    read_client: ReadOnlyClient,
) -> str | None:
    """Try to download LRC content from R2. Returns None if unavailable."""
    import os

    access_key = os.environ.get("SOW_R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("SOW_R2_SECRET_ACCESS_KEY")
    if not access_key or not secret_key:
        return None

    if not hash_prefix:
        recording = read_client.get_recording_by_song_id(song_id)
        if recording is None:
            return None
        hash_prefix = recording.hash_prefix

    if not hash_prefix:
        return None

    try:
        from stream_of_worship.admin.services.r2 import R2Client

        r2 = R2Client(
            bucket=config.r2_bucket,
            endpoint_url=config.r2_endpoint_url,
            region=config.r2_region,
        )
        return r2.download_lrc_content(hash_prefix)
    except Exception as e:
        print(f"Warning: R2 LRC download failed: {e}", file=sys.stderr)
        return None


if __name__ == "__main__":
    main()
