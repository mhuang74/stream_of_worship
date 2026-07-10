# Admin CLI Catalog Insert From YouTube v2

## Summary

This document replaces the v1 implementation plan in
`specs/catalog-insert-youtube.md` for future implementation work. Do not edit
the v1 file when implementing this version.

Add a manually curated catalog intake flow for songs that are not already in the
Stream of Praise catalog:

```bash
sow-admin catalog insert
sow-admin catalog insert --youtube <youtube-url>
```

The YouTube mode fetches video metadata, prepares editable defaults, optionally
loads caption text as draft lyrics, inserts the reviewed `songs` row, then
downloads audio from the same reviewed YouTube URL and creates the associated
`recordings` row.

The v2 intent is intentionally conservative: the command helps admins gather
data, but it does not trust YouTube captions or silently upsert catalog records.

## Changes From v1

- Remove `--yes`. This process requires manual curation.
- Remove `--force` from `catalog insert`. Existing songs are handled by
  `catalog edit`, `catalog restore`, or existing audio commands.
- Drop all musical-key discovery from this feature. `musical_key` remains an
  optional manual field and defaults blank.
- Add catalog recovery and correction commands:
  - `catalog edit <song_id>`
  - `catalog delete <song_id>`
  - `catalog restore <song_id>`
  - `catalog list --deleted`
- Treat YouTube captions as draft input only. They are saved as canonical
  catalog lyrics only after admin review.
- Soft-delete bad catalog data instead of hard-deleting it.

## Goals

- Admins can add a non-SOP song manually.
- Admins can add a non-SOP song from a YouTube URL with reviewed metadata,
  reviewed lyrics, and a recording downloaded from the same URL.
- Admins can recover from bad inserts by hiding the catalog entry without
  purging files or rewriting user songsets.
- Admins can correct nominal catalog lyrics and metadata after insert.

## Out Of Scope

- Automatic musical-key detection or web-search helpers.
- Automatic analysis or LRC submission after insert.
- Hard deletion or R2 purge of songs inserted through this flow.
- Re-keying song IDs after title/composer/lyricist edits.
- Rewriting existing user songsets when a song is soft-deleted.

## Command Behavior

### `catalog insert`

Common options:

```text
--config, -c       Path to admin config file
--id              Override generated song ID
--dry-run, -n     Show reviewed data and planned audio action without writing
```

Manual mode prompts or opens an editable review document for:

- `title` - required
- `composer`
- `lyricist`
- `album_name`
- `album_series`
- `musical_key` - optional manual value only
- `source_url` - required
- `lyrics_raw`

Manual mode inserts only a song row. It does not download audio.

YouTube mode:

1. Requires a valid YouTube URL.
2. Fetches video metadata with `yt-dlp`.
3. Fetches captions with `youtube-transcript-api` when available.
4. Prefills an editable review document.
5. Requires final admin confirmation.
6. Inserts the reviewed song.
7. Downloads audio from the same reviewed YouTube URL.
8. Inserts the recording with `recordings.youtube_url` set to the canonical URL.

`--dry-run` still fetches metadata and captions, opens or prints the reviewed
data, then reports:

- Proposed song fields.
- Generated or overridden song ID.
- Caption/transcript source and draft lyric count.
- Planned audio download URL.

`--dry-run` must not insert a song, download audio, upload to R2, or create a
recording.

### Duplicate Handling

Before writing, `catalog insert` must check:

- Whether the generated or overridden song ID already exists, including
  soft-deleted rows.
- Whether the reviewed `source_url` already belongs to any song, including
  soft-deleted rows.

If either exists, the command exits without writing or downloading. It prints
the matching song and suggests the appropriate follow-up:

- `sow-admin catalog edit <song_id>` for active rows.
- `sow-admin catalog restore <song_id>` for soft-deleted rows.
- `sow-admin audio download <song_id> --url <youtube-url> --force` when the
  song is correct but audio needs replacement.

`catalog insert` must not upsert existing songs in v2.

### YouTube Caption Handling

Captions are draft data, not trusted catalog lyrics.

Transcript cleanup rules:

- Prefer Chinese captions first, then English captions.
- Drop cue-only bracketed segments such as `[Music]` and `[Laughter]`.
- Trim whitespace and collapse repeated internal whitespace.
- Preserve repeated lyric lines.
- Do not translate transcript text.
- If transcript fetching fails, warn and continue with blank lyrics.

Interactive review should make the source obvious, for example:

```toml
# lyrics_source = "YouTube English (auto-generated), 52 draft lines"
```

The reviewed text becomes both `lyrics_raw` and `lyrics_lines`. If the admin
removes the draft lyrics, both fields should be stored consistently as empty or
null according to the existing scraper/catalog conventions.

## Recovery And Editing Commands

### `catalog list --deleted`

Extend the existing `catalog list` command with:

```text
--deleted          Show soft-deleted songs instead of active songs
```

Default `catalog list` continues to show only active songs. `--deleted` lists
only rows where `songs.deleted_at IS NOT NULL`, reusing the existing table and
filter behavior where practical. It should work with `--format ids`.

### `catalog delete <song_id>`

Delete is the default recovery path for bad catalog inserts.

Behavior:

1. Look up the song, including active rows only by default.
2. Show the song metadata.
3. Show active recordings associated with the song.
4. Show affected songset reference counts from `songset_items.song_id`.
5. Ask for confirmation unless `--yes` is passed.
6. Soft-delete the song via `songs.deleted_at`.
7. Set associated active recordings to `visibility_status = 'hold'`.
8. Preserve R2 audio, stems, and LRC files.
9. Do not mutate `songset_items`.

After deletion, the song is hidden from catalog browse/search and normal app
song discovery. Existing songsets are not rewritten; the command only reports
their references so the admin knows what may need manual follow-up.

### `catalog restore <song_id>`

Behavior:

1. Look up the song including soft-deleted rows.
2. Clear `songs.deleted_at`.
3. Print associated recordings whose visibility is `hold`.
4. Do not automatically publish recordings.
5. Suggest:

```bash
sow-admin audio set-visibility <song_id> --status review
sow-admin audio set-visibility <song_id> --status published
```

as appropriate.

### `catalog edit <song_id>`

Add a curated edit flow for nominal catalog data.

Behavior:

1. Load the existing active song by ID.
2. Open a TOML editor containing editable metadata and lyrics.
3. Preserve the existing song ID even if title/composer/lyricist change.
4. Validate required fields: `title` and `source_url`.
5. Recompute `title_pinyin` from the reviewed title.
6. Normalize lyrics into `lyrics_raw`, `lyrics_lines`, and single
   `unknown` section JSON.
7. Show a before/after summary or diff.
8. Confirm before saving.

If lyrics changed, print follow-up commands:

```bash
sow-admin audio lrc <song_id> --force
sow-admin audio embed <song_id>
```

Do not automatically submit LRC or embedding work in v2.

## Implementation Plan

### 1. YouTube Service

Extend `src/stream_of_worship/admin/services/youtube.py`.

Add an admin-safe metadata API:

```python
@dataclass
class YouTubeVideoMetadata:
    video_id: str
    title: str
    webpage_url: str
    duration: int | None
    channel: str | None
    uploader: str | None
    creator: str | None
    upload_date: str | None
    description: str | None
    thumbnail: str | None
    raw: dict[str, Any]
```

Add:

```python
def extract_video_metadata(url: str) -> YouTubeVideoMetadata
def extract_video_id(url: str) -> str | None
def derive_song_defaults(metadata: YouTubeVideoMetadata) -> dict[str, str | None]
def fetch_transcript_lines(url: str, languages: list[str] | None = None) -> list[str]
```

Implementation notes:

- Reuse the existing `yt-dlp` dependency from the `admin` extra.
- Keep `extractor_args.youtube.remote_components` consistent with the current
  downloader.
- Wrap `yt_dlp.utils.DownloadError` as `RuntimeError`, matching current service
  style.
- Add `youtube-transcript-api` to the `admin` optional dependency group.
- Do not import `services/analysis`; duplicate only the small video ID and
  language-priority logic needed by Admin CLI.

### 2. Catalog Insert/Edit Service

Create a focused helper module, for example:

```text
src/stream_of_worship/admin/services/catalog_edit.py
```

Responsibilities:

- Normalize reviewed field values.
- Convert `lyrics_raw` to `lyrics_lines`.
- Build the existing single `unknown` section JSON.
- Build `Song` objects for insert/edit.
- Generate stable IDs for new songs.
- Render and parse the TOML review document.

Move the current scraper ID formula into a shared helper without changing its
output:

```text
<pinyin_slug>_<8-char-sha256-title-composer-lyricist>
```

`CatalogScraper._compute_song_id()` should call the shared helper so scrape ID
stability tests continue to pass.

### 3. Database Helpers

Add explicit helpers to `DatabaseClient` instead of relying on insert/upsert
side effects:

- `get_song(..., include_deleted=True)` already exists; reuse it.
- `find_song_by_source_url(source_url, include_deleted=False) -> Song | None`.
- `update_song(song: Song) -> bool`.
- `list_recordings_by_song_id(song_id, include_deleted=False) -> list[Recording]`.
- `hold_recordings_for_song(song_id) -> int`.
- `count_songset_references(song_id) -> int`.
- Use existing `soft_delete_song()`, `restore_song()`, and `list_deleted_songs()`.

No migration is required.

### 4. Catalog Commands

Extend `src/stream_of_worship/admin/commands/catalog.py`.

Add:

```python
@app.command("insert")
def insert_song(...):
    ...

@app.command("edit")
def edit_song(...):
    ...

@app.command("delete")
def delete_song(...):
    ...

@app.command("restore")
def restore_song(...):
    ...
```

Extend existing `list_songs()` with `--deleted`.

Insert flow:

1. Load `AdminConfig`.
2. Create `DatabaseClient`.
3. Gather manual fields or YouTube defaults.
4. Open the review document in `$EDITOR` when interactive.
5. Validate reviewed fields.
6. Compute or apply song ID.
7. Check duplicate song ID and duplicate source URL, including deleted rows.
8. Show a Rich review panel/table.
9. Confirm insert.
10. Insert the song with `db_client.insert_song(song)`.
11. In YouTube mode, call the shared audio import helper with the reviewed URL.

Failure handling:

- Metadata extraction failure is fatal.
- Transcript extraction failure is nonfatal.
- Validation or duplicate checks happen before any write.
- Song insert failure is fatal and must not start audio download.
- Audio download/upload failure after song insert keeps the song row and prints
  a retry command.

### 5. Shared Audio Import Flow

Refactor the core body of `audio download` into a callable helper so
`catalog insert --youtube` does not duplicate download logic.

Recommended helper shape:

```python
def import_youtube_audio_for_song(
    *,
    song_id: str,
    youtube_url: str,
    config: AdminConfig,
    db_client: DatabaseClient,
    console: Console,
    force: bool = False,
    skip_video_confirm: bool = False,
    analyze: bool = False,
    lrc: bool = False,
) -> Recording | None:
    ...
```

For `catalog insert --youtube`, call with:

```python
force=False
skip_video_confirm=True
analyze=False
lrc=False
```

The final catalog insert confirmation covers the audio import. The helper can
still show the video preview, duration warning, and planned URL before that
confirmation.

The helper should preserve existing `audio download` behavior:

- Check existing recording.
- Preview video.
- Show duration warning.
- Download by direct URL.
- Compute hash.
- Probe duration.
- Upload to R2.
- Insert `Recording`.
- Clean up temp files.
- Optionally submit analysis/LRC only when explicitly requested by the caller.

## Tests

### Unit Tests

YouTube metadata:

- `extract_video_id()` accepts standard `watch?v=...` and `youtu.be/...` URLs.
- `derive_song_defaults()` parses the sample title into:
  - title: `Here I Bow`
  - composer: `Brian & Jenn Johnson`
  - album: `After All These Years`
- Metadata extraction wraps `yt-dlp` errors as `RuntimeError`.

Transcript cleanup:

- Drops `[Music]` and `[Laughter]`.
- Keeps repeated lyric lines.
- Collapses whitespace.
- Returns empty draft lyrics or raises a handled service error when captions are
  unavailable.

Catalog builder:

- Manual fields create a valid `Song`.
- Reviewed transcript text populates both `lyrics_raw` and `lyrics_lines`.
- Empty lyrics are stored consistently with existing scraper behavior.
- Generated song IDs match the existing scraper helper.
- `catalog edit` preserves the original song ID.

### CLI Tests

Use mocks for YouTube, R2, hashing, ffprobe, editor launch, and DB setup.

- `catalog insert` manual mode inserts one song and no recording.
- `catalog insert --youtube <url>` inserts a reviewed song and calls shared
  audio import with the same reviewed URL.
- `catalog insert --youtube <url> --dry-run` extracts metadata/transcript but
  does not insert or download.
- Passing `--yes` or `--force` to `catalog insert` is rejected by Typer.
- Existing active song ID exits without audio import.
- Existing deleted song ID suggests `catalog restore`.
- Existing active or deleted `source_url` exits without audio import.
- Audio failure after insert prints the retry command.
- `catalog list` excludes soft-deleted songs by default.
- `catalog list --deleted` returns only soft-deleted songs and supports
  `--format ids`.
- `catalog delete` soft-deletes the song, holds recordings, reports songset
  references, and does not delete R2 resources.
- `catalog restore` clears `deleted_at` and does not change recording
  visibility.
- `catalog edit` updates metadata/lyrics, preserves ID, and prints LRC/embed
  follow-ups only when lyrics changed.

### Verification Commands

```bash
PYTHONPATH=src uv run --python 3.11 --extra admin --extra test pytest tests/admin/ -v
```

If implementation touches shared DB model behavior, also run:

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
  --ignore=tests/services/analysis \
  --ignore=services/qwen3/tests \
  --ignore=services/analysis/tests -v
```

After code changes, run:

```bash
graphify update .
```

## Acceptance Criteria

- Admins can manually add a non-SOP song with `sow-admin catalog insert`.
- Admins can add a reviewed YouTube song and recording with
  `sow-admin catalog insert --youtube <url>`.
- `catalog insert` has no `--yes` or `--force` path.
- The sample URL prefills title, artist/composer, album, source URL, duration
  preview, and draft transcript lyrics.
- Transcript failure does not block song insertion.
- YouTube transcript text is not trusted unless reviewed by the admin.
- Duplicate ID/source URL checks prevent accidental upserts.
- Download failure after song insertion is recoverable with the printed
  `audio download` command.
- Admins can soft-delete bad catalog rows and list them through
  `catalog list --deleted`.
- Admins can restore soft-deleted rows without automatically publishing
  recordings.
- Admins can fix catalog lyrics/metadata through `catalog edit`.
- Existing `catalog scrape` ID behavior is unchanged.
- Existing `audio download` behavior remains unchanged from a user perspective.

## Assumptions And Defaults

- Soft-delete means hidden from discovery but preserved for audit and restore.
- Soft-delete does not purge R2 resources.
- Soft-delete does not rewrite user songsets.
- Existing songsets may still reference soft-deleted songs; v2 reports this but
  leaves remediation to admins.
- `catalog edit` preserves the existing song ID.
- `musical_key` is optional manual metadata and blank by default.
- `songs.source_url` stores the canonical YouTube URL for YouTube-inserted
  songs.
- `recordings.youtube_url` stores the same canonical URL after audio import.
- No database migration is required.
