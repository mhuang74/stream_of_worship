# Admin CLI Catalog Insert From YouTube

## Summary

Add `sow-admin catalog insert` so admins can manually add songs that are not in the
Stream of Praise catalog. Add `sow-admin catalog insert --youtube <url>` as a
YouTube-first intake flow that:

1. Fetches YouTube video metadata.
2. Prefills song metadata for admin review.
3. Tries to prefill catalog lyrics from YouTube captions.
4. Inserts the reviewed `songs` row.
5. Automatically downloads the audio from the same YouTube URL and creates the
   associated `recordings` row.

This is a planning/spec-only document. It does not implement the feature.

## Prototype Findings

Prototype URL:

```text
https://www.youtube.com/watch?v=hvHi7N8kc8s
```

`yt-dlp` can extract metadata without downloading audio. The sample returned:

```json
{
  "id": "hvHi7N8kc8s",
  "title": "Here I Bow (Official Lyric Video) -  Brian & Jenn Johnson | After All These Years",
  "channel": "Bethel Music",
  "uploader": "Bethel Music",
  "creator": "Bethel Music, Jenn Johnson",
  "duration": 264,
  "duration_string": "4:24",
  "webpage_url": "https://www.youtube.com/watch?v=hvHi7N8kc8s",
  "upload_date": "20170127"
}
```

A simple title parser can prefill:

```json
{
  "title": "Here I Bow",
  "composer": "Brian & Jenn Johnson",
  "album_name": "After All These Years"
}
```

`youtube-transcript-api` found one English auto-generated transcript:

```json
[
  {
    "language_code": "en",
    "language": "English (auto-generated)",
    "is_generated": true,
    "is_translatable": true
  }
]
```

The transcript had 56 segments. After dropping cue-only segments such as `[Music]`
and `[Laughter]`, it produced 52 draft lyric lines. The quality is noisy, so
transcript lyrics must be treated as editable draft text, not as trusted catalog
lyrics.

## Command Behavior

Add a new command:

```bash
sow-admin catalog insert
sow-admin catalog insert --youtube https://www.youtube.com/watch?v=hvHi7N8kc8s
```

Common options:

```text
--config, -c       Path to admin config file
--id              Override generated song ID
--force, -f       Upsert an existing song ID and replace existing recording when needed
--dry-run, -n     Show reviewed data and planned audio action without writing
--yes, -y         Accept defaults and skip confirmation prompts
```

Manual mode prompts for:

- `title` - required
- `composer`
- `lyricist`
- `album_name`
- `album_series`
- `musical_key`
- `source_url` - required
- `lyrics_raw`

YouTube mode:

- Requires a valid YouTube URL.
- Fetches video metadata with `yt-dlp`.
- Fetches captions with `youtube-transcript-api` when available.
- Prefills fields, then requires admin review unless `--yes` is passed.
- Stores the canonical YouTube URL in `songs.source_url`.
- After inserting the song, automatically downloads audio from the same URL and
  stores the canonical YouTube URL in `recordings.youtube_url`.

`--dry-run` still performs metadata and transcript extraction, then prints:

- Proposed song fields.
- Generated or overridden song ID.
- Transcript source and draft lyric count.
- Planned audio download URL.

It must not insert a song, download audio, upload to R2, or create a recording.

## Metadata Mapping

Use these defaults in YouTube mode:

| Song field | YouTube-derived default |
| --- | --- |
| `title` | Parsed leading title from video title |
| `composer` | Parsed artist segment, else `creator`, else `uploader`/`channel` |
| `lyricist` | Blank |
| `album_name` | Parsed title suffix after `|`, else quoted album mention in description |
| `album_series` | Blank |
| `musical_key` | Blank |
| `source_url` | Canonical `webpage_url` |
| `lyrics_raw` | Cleaned transcript draft, if captions are available |
| `lyrics_lines` | JSON array from reviewed `lyrics_raw` |
| `sections` | Existing single `unknown` section format used by scraper |
| `scraped_at` | Current timestamp |
| `title_pinyin` | Existing `pypinyin` title behavior |

Title parsing rules for v1:

- Remove parenthesized video qualifiers containing words such as `official`,
  `lyric`, `lyrics`, `video`, `mv`, or `audio`.
- Split the remaining title on ` - ` and ` | `.
- Treat segment 1 as title, segment 2 as artist/composer, and segment 3 as
  album.
- If parsing produces empty values, fall back to raw YouTube metadata.

Transcript cleanup rules:

- Prefer Chinese captions first, then English captions, matching analysis worker
  priority: Chinese variants, then English variants.
- Drop cue-only bracketed segments like `[Music]`.
- Trim whitespace and collapse repeated internal whitespace.
- Preserve repeated lyric lines.
- Do not translate transcript text in the Admin CLI.
- If transcript fetching fails, warn and continue with blank lyrics.

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
  language-priority logic needed for the Admin CLI.

### 2. Catalog Insert Service

Create a small helper module under admin catalog services, for example:

```text
src/stream_of_worship/admin/services/catalog_insert.py
```

Responsibilities:

- Normalize reviewed field values.
- Convert `lyrics_raw` to `lyrics_lines`.
- Build the existing single `unknown` section JSON.
- Build a `Song`.
- Generate stable IDs.

Move the current scraper ID formula into a shared helper without changing its
output:

```text
<pinyin_slug>_<8-char-sha256-title-composer-lyricist>
```

The existing `CatalogScraper._compute_song_id()` should call the shared helper
so scrape ID stability tests continue to pass.

### 3. Catalog Command

Extend `src/stream_of_worship/admin/commands/catalog.py`.

Add:

```python
@app.command("insert")
def insert_song(...):
    ...
```

Flow:

1. Load `AdminConfig`.
2. Create `DatabaseClient`.
3. Gather manual fields or YouTube defaults.
4. Open lyrics draft in editor for review when interactive.
5. Show a Rich review panel/table.
6. Confirm insert unless `--yes`.
7. Check existing song ID.
8. Insert song with `db_client.insert_song(song)`.
9. In YouTube mode, call the shared audio import flow with the reviewed URL.

Existing song handling:

- If song ID exists and `--force` is false, show the existing song and exit
  without audio download.
- If `--force` is true, upsert the song.

Failure handling:

- Metadata extraction failure is fatal.
- Transcript extraction failure is nonfatal.
- Song insert failure is fatal and should not start audio download.
- Audio download/upload failure after song insert should keep the song row and
  print:

```bash
sow-admin audio download <song_id> --url <youtube_url> --force
```

### 4. Shared Audio Import Flow

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
    skip_confirm: bool = False,
    analyze: bool = False,
    lrc: bool = False,
) -> Recording | None:
    ...
```

The helper should preserve existing behavior:

- Check existing recording.
- Preview video.
- Show duration warning.
- Download by direct URL.
- Compute hash.
- Probe duration.
- Upload to R2.
- Insert `Recording`.
- Clean up temp files.
- Optionally submit analysis/LRC when explicitly requested.

For `catalog insert --youtube`, call it with:

```python
analyze=False
lrc=False
skip_confirm=True if --yes else False
```

Do not automatically submit analysis or LRC jobs in v1. The inserted recording
contains `youtube_url`, so the existing LRC pipeline can use YouTube transcript
as its primary path later.

## Tests

Add tests that are not covered by the currently skipped SQLite-era command
tests.

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
- Returns an empty list or raises a handled service error when captions are
  unavailable, depending on helper API shape.

Catalog insert builder:

- Manual fields create a valid `Song`.
- Reviewed transcript text populates both `lyrics_raw` and `lyrics_lines`.
- Empty lyrics leave lyrics fields blank or empty consistently with existing
  scraper behavior.
- Generated song IDs match the existing scraper helper for the same
  title/composer/lyricist inputs.

### CLI Tests

Use mocks for YouTube, R2, hashing, ffprobe, and DB setup.

- `catalog insert` manual mode inserts one song.
- `catalog insert --youtube <url> --yes` inserts song and calls shared audio
  import with the same URL.
- `catalog insert --youtube <url> --dry-run` extracts metadata/transcript but
  does not insert or download.
- Existing song without `--force` exits without audio import.
- Existing song with `--force` upserts and proceeds.
- Audio failure after insert prints the retry command.

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

## Acceptance Criteria

- Admins can add a non-SOP song with `sow-admin catalog insert`.
- Admins can run `sow-admin catalog insert --youtube <url>` and get a reviewed
  catalog row plus downloaded recording.
- The sample URL prefills title, artist/composer, album, source URL, duration
  preview, and draft transcript lyrics.
- Transcript failure does not block song insertion.
- Download failure after song insertion is recoverable with the printed
  `audio download` command.
- Existing `catalog scrape` ID behavior is unchanged.
- Existing `audio download` behavior remains unchanged from a user perspective.

## Assumptions And Defaults

- `catalog insert --youtube` creates both the catalog song and recording.
- The command does not automatically submit analysis or LRC jobs in v1.
- YouTube transcript text is draft catalog lyrics only.
- No database migration is required.
- `songs.source_url` stores the canonical YouTube URL for YouTube-inserted songs.
- `recordings.youtube_url` stores the same canonical URL after audio import.
