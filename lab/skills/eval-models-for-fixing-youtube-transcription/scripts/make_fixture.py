#!/usr/bin/env python3
"""Build an eval fixture JSON from the recording DB (admin env).

Admin env — reuses production lyrics resolution WITHOUT importing sow_analysis
(the admin venv cannot import it: workers/__init__ -> queue -> storage/db ->
aiosqlite):

    uv run --project ops/admin-cli --extra admin python \
        lab/skills/eval-models-for-fixing-youtube-transcription/scripts/make_fixture.py \
        (--song-id song_0001 [--song-id ...] | --auto N [--album ALBUM]) \
        [--language zh] [--out PATH]

Tag lines (``[Label]``) are KEPT in the lyrics list — the correction prompt
must match production input. ``lyrics_source`` records whether structured
lyrics were flattened (``structured``) or ``lyrics_raw`` was used (``raw``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import OUTPUT_ROOT, bootstrap_admin_src, now_run_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate eval fixture from the recording DB")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--song-id", action="append", help="Song ID to include (repeatable)")
    mode.add_argument(
        "--auto",
        type=int,
        metavar="N",
        help="Take the first N published recordings with a YouTube URL",
    )
    parser.add_argument("--album", default=None, help="Optional album filter for --auto")
    parser.add_argument(
        "--language", default="zh", choices=["zh", "en"], help="Language label for DB entries"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Output path (default: {OUTPUT_ROOT}/fixtures-<run_id>.json)",
    )
    args = parser.parse_args()

    bootstrap_admin_src()
    from stream_of_worship.admin.config import AdminConfig
    from stream_of_worship.admin.db.client import DatabaseClient
    from stream_of_worship.admin.services.lrc_jobs import resolve_lyrics_text
    from stream_of_worship.admin.services.structured_lyrics import flatten_structured_lyrics
    from stream_of_worship.db.connection import ConnectionProvider

    config = AdminConfig.load()
    provider = ConnectionProvider(config.get_connection_url())
    db = DatabaseClient(provider)

    entries: list[dict] = []
    kept = skipped = 0
    per_source = {"structured": 0, "raw": 0}

    def _add_entry(song, recording) -> bool:
        nonlocal kept, skipped
        if not recording.youtube_url:
            print(f"SKIP {song.id} ({song.title}): recording has no youtube_url", file=sys.stderr)
            skipped += 1
            return False
        if recording.duration_seconds is None:
            print(
                f"SKIP {song.id} ({song.title}): recording has no duration_seconds", file=sys.stderr
            )
            skipped += 1
            return False
        lyrics_text = resolve_lyrics_text(song, recording)
        if not lyrics_text or not lyrics_text.strip():
            print(f"SKIP {song.id} ({song.title}): no lyrics available", file=sys.stderr)
            skipped += 1
            return False
        # Production split (youtube_transcript.py:893): no tag stripping.
        lyrics = [ln for ln in lyrics_text.split("\n") if ln.strip()]
        lyrics_source = "raw"
        if recording.structured_lyrics:
            try:
                structured = json.loads(recording.structured_lyrics)
                # Cross-check against the production helper's own flattening.
                if (
                    structured
                    and structured.get("sections")
                    and flatten_structured_lyrics(structured).strip()
                ):
                    lyrics_source = "structured"
            except json.JSONDecodeError:
                pass
        per_source[lyrics_source] += 1
        entries.append(
            {
                "song_id": song.id,
                "title": song.title,
                "language": args.language,
                "youtube_url": recording.youtube_url,
                "duration_seconds": float(recording.duration_seconds),
                "lyrics": lyrics,
                "lyrics_source": lyrics_source,
            }
        )
        kept += 1
        return True

    if args.song_id:
        for song_id in args.song_id:
            song = db.get_song(song_id)
            if song is None:
                print(f"SKIP {song_id}: song not found", file=sys.stderr)
                skipped += 1
                continue
            recording = db.get_recording_by_song_id(song_id)
            if recording is None:
                print(f"SKIP {song_id}: no recording", file=sys.stderr)
                skipped += 1
                continue
            _add_entry(song, recording)
    else:
        rows = db.list_recordings_with_songs(
            visibility="published",
            album=args.album or None,
            sort_by="imported",
            limit=200,  # scan window
        )
        eligible = 0
        for recording, song_title, _album_name, _album_series in rows:
            if eligible >= args.auto:
                break
            if not recording.youtube_url or recording.duration_seconds is None:
                continue
            if not recording.song_id:
                continue
            song = db.get_song(recording.song_id)
            if song is None:
                continue
            if _add_entry(song, recording):
                eligible += 1
        if eligible < args.auto:
            print(
                f"WARNING: only {eligible} of requested {args.auto} entries qualify "
                f"(scan window: {len(rows)} published recordings).",
                file=sys.stderr,
            )

    provider.close()

    out = args.out or (OUTPUT_ROOT / f"fixtures-{now_run_id()}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"Kept: {kept}  Skipped: {skipped}  (structured: {per_source['structured']}, raw: {per_source['raw']})"
    )
    print(f"Fixture written: {out}")
    if kept == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
