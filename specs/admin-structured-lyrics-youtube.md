---
# Admin CLI: Structured Song Lyrics from YouTube Description

## Summary

Enhance the Admin CLI to capture the structured (section-tagged) lyrics
embedded in a YouTube video's description and persist them alongside the
recording. The structured lyrics — blocks like `[Verse]`, `[Pre-Chorus]`,
`[Chorus]` with their lyric lines — are then (a) displayed in `sow-admin
audio show` next to the timed LRC contents, and (b) preferred over the
scraped `songs.lyrics_raw` as the `lyrics_text` payload sent to the
Analysis Service when triggering an LRC job.

Currently the audio-download flow throws away the YouTube description
entirely; the only lyrics source for LRC alignment is the
single-flat-block `lyrics_raw` column scraped from sop.org, which lacks
section structure and is occasionally incomplete or stale.

## Motivation

### Example

YouTube video `https://www.youtube.com/watch?v=nGzADKIDf4A` publishes the
lyrics in its description with explicit section tags:

```
[Verse]
親愛耶穌　祢真愛我
毫無保留　我敬拜祢
因祢捨命　我回到父神面前
在恩典和應許中敬拜

[Pre-Chorus]
喔耶穌　喔耶穌
祢喜悅我向祢歌頌
喔耶穌　喔耶穌
高舉雙手　全心敬拜祢

[Chorus]
我要一生　一生敬拜祢
在祢殿中　瞻仰祢榮美
在祢豐盛恩典中　我歡欣歌頌
在祢救恩盼望中　我靈不住快樂
```

The sop.org scrape stores the same lyrics as a flat text blob in
`songs.lyrics_raw` with **no** section boundaries, and sometimes with
minor formatting drift. The section structure improves Whisper-based
forced alignment quality (the aligner can place hard boundaries between
sections) and gives the LRC editor a canonical segmentation to render.

## Current Behavior

### Key Files

| File | Role |
| --- | --- |
| `ops/admin-cli/src/stream_of_worship/admin/services/youtube.py` | `extract_video_metadata()` (lines 218–254) returns `YouTubeVideoMetadata` including `description`; `derive_song_defaults()` (257–288); `YouTubeDownloader` (368+) does the actual audio download and is what `audio download` uses today. |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | `import_youtube_audio_for_song()` (717–963) drives the download; **does NOT call `extract_video_metadata()`**; LRC submission helpers `_submit_lrc_job` (638–715, used by download), `_submit_lrc_single` (348–487), `_submit_lrc_batch` (490–585), `_submit_lrc_for_song` (5937–6074, batch workflow) — all read `song.lyrics_raw` and pass it as `lyrics_text`. `show_recording` (1385–1506) shows recording metadata + LRC status/URL but **not** LRC contents; `_display_lrc` (4141–4263) renders LRC contents for the separate `audio view-lrc` command. |
| `ops/admin-cli/src/stream_of_worship/admin/commands/catalog.py` | `insert_song` with `--youtube` (254–392) already calls `extract_video_metadata()` (303) + `derive_song_defaults()` (308), then seeds `lyrics_raw` from the YouTube transcript (314–324). Currently discards the description. |
| `ops/admin-cli/src/stream_of_worship/admin/db/schema.py` | Raw SQL DDL for `songs` (8–35) and `recordings` (38–90); column-list constants `RECORDING_COLUMNS_SELECT` (356–366), `RECORDING_COLUMN_COUNT = 34` (375). No ALTER section for `recordings` exists. |
| `ops/admin-cli/src/stream_of_worship/admin/db/models.py` | `Recording` dataclass (160–458) with `from_row` (226–343) handling 25–34 column schemas; `Song.lyrics_list` property (142–156). |
| `ops/admin-cli/src/stream_of_worship/admin/db/client.py` | `_insert_recording_with_cursor` (557–629) with an explicit INSERT column list that does **not** include any structured-lyrics columns; `replace_recording_after_import` (1492–1521) delegates to it; `get_song` (322); `update_recording_status` (880–929). |
| `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py` | `AnalysisClient.submit_lrc()` (392–474) POSTs `lyrics_text` to `/api/v1/jobs/lrc`; the analysis service treats it as a plain-text forced-alignment hint. |
| `ops/admin-cli/src/stream_of_worship/db/postgres_schema.py` | Aggregates `ALL_SCHEMA_STATEMENTS` (63–91); re-exports from admin `schema.py`. |

### Gaps

1. `audio download` never calls `extract_video_metadata()` — the description
   (with structured lyrics) is discarded.
2. `catalog insert --youtube` calls it but only uses the transcript for
   `lyrics_raw`; the description is discarded.
3. The recordings table has no column to hold structured lyrics.
4. `audio show` does not render structured lyrics nor the LRC body — only
   status/URL.
5. LRC submission always uses `songs.lyrics_raw`, ignoring the higher-fidelity
   structured description.

## Product Decisions

Confirmed via interview (this session). Each is **decided** —
implementation must follow these choices, not re-litigate them.

| # | Decision | Rationale |
| --- | --- | --- |
| D1 | Store structured lyrics on the **`recordings`** table (two new columns). | The description is per-YouTube-video, i.e. per-recording. Two recordings of the same song may have come from different videos with different descriptions. |
| D2 | Store **both** the raw description and a parsed JSON. `structured_lyrics_raw TEXT` (verbatim description) + `structured_lyrics TEXT` (parsed JSON of section blocks). | Raw preserves fidelity for re-parsing/audit; parsed JSON gives the CLI and analysis service a stable schema to consume. |
| D3 | Keep the parsed `[Verse]`/`[Chorus]` sections **separate** from the existing `songs.sections` / `recordings.sections` columns. | The existing `sections` columns are audio-analysis-derived (detected structural segments with time bounds). Structured lyrics sections are a textual lyric-segmentation concept. Conflating them would corrupt the audio-section semantics. |
| D4 | Use the existing `extract_video_metadata()` to retrieve the description in `audio download` (it currently is NOT called there). | Single source of truth for yt-dlp metadata extraction; the function already returns `description` (youtube.py:251). The extra `extract_info(download=False)` call is cheap relative to the audio download itself. |
| D5 | Also populate structured lyrics during `catalog insert --youtube` (which already calls `extract_video_metadata()`). | Consistency: both YouTube-aware entry points capture the description. `catalog insert` creates the song row; structured lyrics are written to the recording row at audio-import time (it calls `import_youtube_audio_for_song` at catalog.py:370). |
| D6 | For LRC submission, when `structured_lyrics` exists, prefer it over `songs.lyrics_raw`. Send it as `lyrics_text` **with the `[Verse]`/`[Chorus]` tags preserved** as plain-text lines. | The whisper forced-aligner treats bracket-only lines as ignorable boundaries; keeping the tags gives it free section hints at zero protocol cost (no new API field required). Falls back to `lyrics_raw` when structured lyrics are absent so existing songs keep working. |
| D7 | Display structured lyrics **and** LRC contents in `audio show`. | One command shows both the input (structured lyrics) and the output (synchronized LRC) side by side, so an operator can sanity-check alignment quality without switching commands. |
| D8 | **Always overwrite** structured lyrics on re-download (including `--force`). | Latest description wins. Re-downloads are usually done because the previous description was missing or wrong. |

## Proposed Design

### 1. Schema (`recordings` table)

Add two columns to `CREATE_RECORDINGS_TABLE` (schema.py:38–90), placed
**after `youtube_url`** (line 79) and before `visibility_status` (line 82)
so the YouTube-derived data sits together and `deleted_at` stays last
(existing convention):

```sql
    -- YouTube structured lyrics (parsed from video description)
    structured_lyrics_raw TEXT,
    structured_lyrics TEXT,
```

Add an **idempotent ALTER** (new module-level constant, mirroring the
`ALTER_SONG_COMPONENTS_V5_COLUMNS` pattern at schema.py:271–285) so
existing databases pick up the columns without a rebuild:

```python
ALTER_RECORDINGS_STRUCTURED_LYRICS_COLUMNS = """
ALTER TABLE recordings ADD COLUMN IF NOT EXISTS structured_lyrics_raw TEXT;
ALTER TABLE recordings ADD COLUMN IF NOT EXISTS structured_lyrics TEXT;
"""
```

Register the ALTER in `ALL_SCHEMA_STATEMENTS` in **both**
`ops/admin-cli/src/stream_of_worship/admin/db/schema.py` (its
`ALL_SCHEMA_STATEMENTS`, line 288–305) and the unified re-exporting
`ops/admin-cli/src/stream_of_worship/db/postgres_schema.py`
(`ALL_SCHEMA_STATEMENTS`, line 63–91, plus the `__all__` list at 93–134).
Place the ALTER statement **after** `CREATE_RECORDINGS_TABLE` /
`CREATE_RECORDINGS_UPDATE_TRIGGER` so the table exists first.

Update the column-list constants in `schema.py`:

- `RECORDING_COLUMNS_SELECT` (356–366): append `, structured_lyrics_raw, structured_lyrics`
  **before** the trailing `deleted_at` so the soft-delete column stays last.
- `RECORDING_COLUMN_COUNT` (375): change `34` → `36`.

The exact new ordering of the tail of `RECORDING_COLUMNS_SELECT` becomes:

```
..., youtube_url, structured_lyrics_raw, structured_lyrics, visibility_status, download_status, deleted_at
```

> Note for implementer: verify `RECORDING_COLUMNS_FOR_JOIN`
> (schema.py:370–372) is derived from `RECORDING_COLUMNS_SELECT` via the
> `.join(f"r.{c.strip()}")` comprehension, so it picks up the new columns
> automatically. Do **not** hand-edit the JOIN list.

### 2. `Recording` model (`models.py:160–458`)

Add two fields to the `Recording` dataclass (after `youtube_url`, before
`visibility_status`, matching the column order):

```python
structured_lyrics_raw: Optional[str] = None
structured_lyrics: Optional[str] = None
```

Update `from_row` (lines 226–343):

- The current code keys off `row_len >= 34` for the newest schema (line 246).
  Bump the sentinel to `row_len >= 36` and read the two new columns at the
  indices that match the new column order. Per the new ordering,
  `structured_lyrics_raw` sits at index 31 (was `visibility_status`) and
  `structured_lyrics` at index 32, shifting `visibility_status` → 33,
  `download_status` → 34, `deleted_at` → 35.
- Keep the existing 25–34-column fallbacks intact for backward
  compatibility with old cached rows.

Add the two keys to `to_dict()` (345–386) next to `youtube_url`.

### 3. Persistence (`db/client.py`)

`_insert_recording_with_cursor` (lines 557–629): add the two columns to the
`INSERT ... (column list)` and the `VALUES (...)` placeholder list, and to
the `ON CONFLICT (content_hash) DO UPDATE SET` clause. Because the
structured lyrics should always reflect the latest import, the
`ON CONFLICT` arm sets them unconditionally (matching the D8 overwrite
decision):

```sql
structured_lyrics_raw = EXCLUDED.structured_lyrics_raw,
structured_lyrics = EXCLUDED.structured_lyrics,
```

Append `recording.structured_lyrics_raw` and `recording.structured_lyrics`
to the params tuple at lines 599–628, in positional order matching the
column list (so: after `youtube_url`, before `analysis_status`-era
columns if any are inserted — confirm against the actual column order in
the INSERT).

`replace_recording_after_import` (1492–1521) delegates to
`_insert_recording_with_cursor`, so it inherits the change automatically.

No change to `update_recording_status` — structured lyrics are written at
insert time, not as a status update.

### 4. Structured-lyrics parser (new module: `services/structured_lyrics.py`)

Create `ops/admin-cli/src/stream_of_worship/admin/services/structured_lyrics.py`
with a pure function:

```python
def parse_structured_lyrics(description: str | None) -> dict | None:
    """Parse a YouTube description into structured lyric sections.

    Returns {"sections": [{"label": str, "raw_label": str, "lines": [str]}], "preamble_lines": [str]}
    or None if the description has no section tags.
    """
```

Parsing rules:

1. Split `description` on newlines; strip trailing `\r`.
2. Identify section-tag lines via case-insensitive regex
   `^\[(?P<label>[^\]]+)\]\s*$`. Recognised canonical labels (case-folded
   comparison): `verse`, `verse 1`, `verse 2`, ..., `pre-chorus`, `prechorus`,
   `chorus`, `chorus 1`, ..., `bridge`, `intro`, `outro`, `instrumental`,
   `hook`, `refrain`, `tag`. **Unrecognised** bracket-only lines are
   still treated as section headers (parsed generically) so we never drop
   lyric content — the label is whatever was inside the brackets.
3. Lines **before** the first section tag go into `preamble_lines`
   (typically channel promo; kept for audit but not sent to the aligner).
4. Each subsequent non-empty, non-section-tag line appends to the
   **current** section's `lines`. Blank lines between sections are
   dropped (they are visual separators, not lyric content).
5. Return `None` when zero section tags are present — caller falls back
   to `lyrics_raw`.

Also add a flattening helper used by the LRC submission path:

```python
def flatten_structured_lyrics(structured: dict) -> str:
    """Render structured sections to a single lyrics_text blob, tags preserved.

    Each section emits its `[Label]` header line followed by its lyric
    lines, separated by single newlines. Blank lines between sections are
    omitted. Preamble lines are NOT included.
    """
```

For the worked example, `flatten_structured_lyrics` yields exactly the
tagged block shown in the *Motivation* section above (header line, then
lyric lines, repeat per section), with no trailing blank line.

### 5. `audio download` flow (`audio.py:717–963`)

In `import_youtube_audio_for_song`, **after** the video is resolved (the
preview/confirm step at lines 785–854 produces a canonical
`search_or_url`) and **before** constructing the `Recording` (line 890),
call `extract_video_metadata()` on the resolved URL and parse the
description:

```python
from stream_of_worship.admin.services.youtube import extract_video_metadata
from stream_of_worship.admin.services.structured_lyrics import parse_structured_lyrics

try:
    metadata = extract_video_metadata(search_or_url)
    structured_raw = metadata.description
    structured_json = parse_structured_lyrics(metadata.description)
    structured_json_str = json.dumps(structured_json, ensure_ascii=False) if structured_json else None
except RuntimeError as e:
    console.print(f"[yellow]Could not fetch video metadata for structured lyrics: {e}[/yellow]")
    structured_raw = None
    structured_json_str = None
import json as _json  # already imported at audio.py:7
structured_json_str = _json.dumps(structured_json, ensure_ascii=False) if structured_json else None
```

Then set them on the `Recording` at construction (audio.py:890–901):

```python
recording = Recording(
    ...
    youtube_url=video_info.get("webpage_url"),
    structured_lyrics_raw=structured_raw,
    structured_lyrics=structured_json_str,
    duration_seconds=duration,
)
```

Notes:

- Use `search_or_url` (the resolved URL the user just confirmed) as the
  `extract_video_metadata` argument, **not** the original `youtube_url`
  arg — in the search-based flow the user may have picked a result whose
  URL differs from the input.
- `extract_video_metadata` does a second yt-dlp `extract_info(download=False)`
  call. This is acceptable: it is one extra metadata call relative to the
  multi-megabyte audio download, and `YouTubeDownloader` does not expose
  the underlying `info` dict in a stable way (reusing it would require
  changing `download`/`download_by_url` to return the info — more invasive
  than the extra call costs).
- Failures here MUST be non-fatal: structured lyrics are an enhancement,
  not a blocker for the download. The `except RuntimeError` branch above
  hardens that. (D4 does not require the search-based metadata call — only
  that `extract_video_metadata()` is the function used when we do fetch.)
- D8 (always overwrite) is satisfied by the unconditional assignment to
  `structured_lyrics_raw`/`structured_lyrics` on the `Recording` and the
  `ON CONFLICT ... = EXCLUDED.*` clauses from §3.

### 6. `catalog insert --youtube` flow (`catalog.py:254–392`)

`catalog insert --youtube` does not itself persist a recording — it
creates the song row and then calls `import_youtube_audio_for_song`
(catalog.py:370), which now fetches structured lyrics per §5. So **no
catalog.py edits are required for persistence**.

The one optional improvement (out of scope unless explicitly requested):
prefill `initial_fields["lyrics_raw"]` from the parsed structured lyrics
**flattened without tags** when a transcript draft is unavailable. This is
a nice-to-have; the spec's primary path is that the structured lyrics live
on the recording, written by `import_youtube_audio_for_song`. Leave
catalog.py's transcript-seeding logic (314–324) unchanged.

### 7. LRC submission: prefer structured lyrics (D6)

Introduce a single helper in `audio.py` (near the other `_submit_lrc_*`
helpers, e.g. just above `_submit_lrc_job` at line 638):

```python
def _resolve_lyrics_text(song: Song, recording: Recording) -> str | None:
    """Pick the best lyrics payload for an LRC job.

    Prefers structured lyrics (flattened, tags preserved) from the
    recording; falls back to songs.lyrics_raw. Returns None if neither
    is available.
    """
    if recording.structured_lyrics:
        try:
            structured = json.loads(recording.structured_lyrics)
            if structured and structured.get("sections"):
                return flatten_structured_lyrics(structured)
        except json.JSONDecodeError:
            pass
    return song.lyrics_raw
```

Replace `lyrics_text=song.lyrics_raw` with `lyrics_text=_resolve_lyrics_text(song, recording)`
at all four call sites. Also replace the "no lyrics" guard at each site:

- `_submit_lrc_job` (audio.py:675–679): change `if not song or not song.lyrics_raw:`
  to compute `lyrics_text = _resolve_lyrics_text(song, recording)` first and
  skip when `lyrics_text` is falsy. Pass `lyrics_text=lyrics_text` at line 688.
- `_submit_lrc_single` (audio.py:371–374): same pattern; pass to `submit_lrc`
  at line 409.
- `_submit_lrc_batch` (audio.py:520–524, call at 550): same.
- `_submit_lrc_for_song` (audio.py:5981–5984, call at 6029): same.

Import `flatten_structured_lyrics` from
`stream_of_worship.admin.services.structured_lyrics` near the existing
service imports at audio.py:31–47.

The `AnalysisClient.submit_lrc` signature (analysis.py:392–406) and its
payload (429–444) need **no changes** — `lyrics_text` is already a plain
string field, and D6 sends the tagged text as that string.

### 8. `audio show` — display structured lyrics + LRC contents (D7)

Extend `show_recording` (audio.py:1385–1506) to render two additional
blocks **below** the existing `Panel.fit` (after line 1494, before the
components table at 1496):

#### 8a. Structured Lyrics panel

When `recording.structured_lyrics` is present, parse it and render a Rich
`Panel` titled "Structured Lyrics (YouTube)" whose body is the flattened
tagged text (reuse `flatten_structured_lyrics`). If only
`structured_lyrics_raw` is present (parsing failed), render the raw text
truncated to the first ~40 lines with a `[dim](… truncated, run 'sow-admin
audio view-lrc {song_id}' for full)[/dim]` hint. When neither is present,
print nothing (do not show an empty panel).

#### 8b. Synchronized LRC contents

When `recording.lrc_status == "completed"` (i.e. `recording.has_lrc`),
fetch and render the LRC body. **Reuse** `_display_lrc` (audio.py:4141–4263)
in its default (parsed-table) mode:

```python
if recording.has_lrc:
    _display_lrc(
        console=console,
        song=song,
        recording=recording,
        song_id=song_id,
        raw=False,
        no_timestamps=False,
    )
```

`_display_lrc` already loads `AdminConfig`, constructs an `R2Client`, and
gracefully prints `[yellow]No LRC file found in R2 for {song_id}[/yellow]`
on a 404 (lines 4197–4200). When LRC status is not `"completed"`, print a
single dim hint line instead:

```
[dim]LRC not yet generated (run 'sow-admin audio lrc {song_id}')[/dim]
```

This adds an R2 dependency to `audio show` (previously it only read the
DB). That is acceptable because `audio show` already calls
`db_client.get_song_components` (line 1497) and the command is interactive;
R2 is already a configured dependency of the admin CLI. Behaviour on R2
misconfiguration degrades to the existing `_display_lrc` error path.

### 9. Read-side: `get_song` / `Recording.from_row`

`get_song` (db/client.py:322) does not touch the recordings table — no
change. `get_recording_by_song_id` (661) and `get_recording_by_hash` (631)
SELECT `RECORDING_COLUMNS_SELECT`, which now includes the two new columns;
`Recording.from_row` (updated in §2) deserialises them. No query-text
edits needed beyond the schema.py and models.py changes.

## File-by-File Change List

| File | Change |
| --- | --- |
| `ops/admin-cli/src/stream_of_worship/admin/db/schema.py` | Add columns to `CREATE_RECORDINGS_TABLE`; add `ALTER_RECORDINGS_STRUCTURED_LYRICS_COLUMNS`; append to `ALL_SCHEMA_STATEMENTS`; extend `RECORDING_COLUMNS_SELECT`; bump `RECORDING_COLUMN_COUNT` to 36; re-export the ALTER constant. |
| `ops/admin-cli/src/stream_of_worship/db/postgres_schema.py` | Import + re-export `ALTER_RECORDINGS_STRUCTURED_LYRICS_COLUMNS`; append to `ALL_SCHEMA_STATEMENTS` after the recordings trigger; add to `__all__`. |
| `ops/admin-cli/src/stream_of_worship/admin/db/models.py` | Add `structured_lyrics_raw`, `structured_lyrics` fields to `Recording`; update `from_row` for the 36-column schema (and keep older fallbacks); add to `to_dict`. |
| `ops/admin-cli/src/stream_of_worship/admin/db/client.py` | Extend `_insert_recording_with_cursor` INSERT column list + values + `ON CONFLICT` SET clauses; no change to `replace_recording_after_import` (delegates) or `update_recording_status`. Add new `update_recording_structured_lyrics(hash_prefix, structured_lyrics_raw, structured_lyrics)` method. |
| `ops/admin-cli/src/stream_of_worship/admin/services/structured_lyrics.py` | **New file.** `parse_structured_lyrics(description)` and `flatten_structured_lyrics(structured)`. |
| `ops/admin-cli/src/stream_of_worship/admin/services/youtube.py` | No change. `extract_video_metadata()` already returns `description` (line 251). |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | `import_youtube_audio_for_song`: fetch metadata + parse description, set fields on `Recording`. Add `_resolve_lyrics_text` helper; use it at the four LRC submission sites (`_submit_lrc_job`, `_submit_lrc_single`, `_submit_lrc_batch`, `_submit_lrc_for_song`). `show_recording`: render structured-lyrics panel + call `_display_lrc` when LRC is complete. |
| `ops/admin-cli/src/stream_of_worship/admin/commands/catalog.py` | No change required — `catalog insert --youtube` delegates to `import_youtube_audio_for_song`, which now captures structured lyrics. |

## Migration Notes

- Run `sow-admin db init` against the existing database. The new
  `ALTER ... ADD COLUMN IF NOT EXISTS` is idempotent and safe on a live
  Postgres instance (no table rewrite for nullable TEXT columns).
- Existing recordings get `structured_lyrics_raw = NULL` and
  `structured_lyrics = NULL`. The LRC submission helper transparently
  falls back to `songs.lyrics_raw` for those, so no backfill is required
  to keep LRC jobs working.
- Optional backfill: a future `sow-admin audio backfill-structured-lyrics`
  command could iterate recordings with a `youtube_url` and missing
  `structured_lyrics`, calling `extract_video_metadata(url)`. Out of scope
  for this spec — file a follow-up if desired.

## Testing Plan

1. **Unit: `structured_lyrics.parse_structured_lyrics`**
   - The worked example from *Motivation* parses to 3 sections (Verse,
     Pre-Chorus, Chorus) with the exact line lists shown. Preamble is
     empty.
   - Description with only preamble (no `[...]` tags) → returns `None`.
   - Description with `[Verse 1]`, `[Chorus 2]`, `[Bridge]`, `[Intro]`
     → `sections` labels preserved verbatim in `raw_label`, normalised
     lowercased in `label`.
   - Description containing a non-lyrics block at the bottom (links,
     `訂閱` promo) after the last `[Chorus]` → those lines are dropped
     from the last section's `lines` because they are separated by blank
     lines from the lyrics block? **Implementer note:** decide explicit
     trailing-discard rule — recommend "lines after the last section's
     first blank-line gap that contain URL-ish characters (`http`, `www.`,
     `@`, `粉絲`, `訂閱`, `Subscribe`, `▶`) are trailing non-lyric lines
     and excluded". Add a unit test asserting the discarding.

2. **Unit: `flatten_structured_lyrics`**
   - Round-trip: flatten(parse(ex)) matches the input tagged block,
     ignoring inter-section blank lines.
   - `preamble_lines` are excluded.

3. **Unit: `Recording.from_row`**
   - 36-column row populates `structured_lyrics_raw`/`structured_lyrics`.
   - 34-column legacy row still deserialises (new fields → `None`).

4. **Unit: `_resolve_lyrics_text`**
   - Recording with `structured_lyrics` JSON → returns flattened tagged
     text.
   - Recording with `structured_lyrics = None` → returns
     `song.lyrics_raw`.
   - Recording with malformed JSON `structured_lyrics` → falls back to
     `song.lyrics_raw` (no exception bubbles up).
   - Both empty → returns `None` (caller skips LRC).

5. **Integration (testcontainers):** `sow-admin db init` creates the
   recordings table with the two new columns; an
   `insert_recording` + `get_recording_by_hash` round-trip preserves the
   structured-lyrics fields; `replace_recording_after_import` overwrites
   them (D8).

6. **Manual end-to-end:** `sow-admin audio download song_xxxx --url
   https://www.youtube.com/watch?v=nGzADKIDf4A`, then `sow-admin audio show
   song_xxxx` shows both the "Structured Lyrics (YouTube)" panel (with
   `[Verse]` / `[Pre-Chorus]` / `[Chorus]` blocks) and the synced-lyrics
   LRC table. Then `sow-admin audio lrc song_xxxx --force` and confirm
   the analysis-service request body's `lyrics_text` field (via service
   logs) contains the tagged text, not the flat `lyrics_raw`.

7. **Regression:** a song whose recording has no `structured_lyrics`
   continues to submit LRC with `songs.lyrics_raw`; `audio show` on such a
   recording renders no Structured Lyrics panel and the existing "LRC not
   yet generated" hint (or the LRC table if LRC exists).

## Backfill: `--backfill-lyrics` Flag

### Motivation

The main spec (§5) populates `structured_lyrics` during a fresh audio
download. Existing recordings — already in the database with correct audio
hashes — would need a full re-download to pick up structured lyrics, risking
content-hash changes and orphaning R2 artifacts. The `--backfill-lyrics`
flag fetches structured lyrics for an **existing recording** without
touching its audio.

### Product Decisions

| # | Decision | Rationale |
| --- | --- | --- |
| B1 | Flag name: `--backfill-lyrics`. | Concise; clearly conveys the backfill intent. |
| B2 | YouTube URL source: prefer `--url` if provided, fall back to `recording.youtube_url`. Error if neither is available. | The user may have a better URL than the one originally stored; but often the stored URL is already correct. |
| B3 | **Never** go through YouTube search. Use the resolved URL directly with `extract_video_metadata()`. | The existing recording's audio is the source of truth; the user must target the exact video that was downloaded, not a search result that might differ. |
| B4 | Works on both `audio download` (single song) and `audio batch` (multiple songs). | Batch backfill is the primary use case — operators need to backfill hundreds of recordings. |
| B5 | Error if no recording exists for the song. | The flag backfills an existing recording; it cannot create one. |
| B6 | No downstream job flags (`--lrc`, `--analyze`, `--components`, `--all`). | The user explicitly requested no downstream flags. The operator runs `sow-admin audio lrc <song_id>` separately afterward. If `--backfill-lyrics` is combined with any downstream flag, print an error and exit. |
| B7 | Always overwrite existing `structured_lyrics` (no `--force` needed). | Re-running backfill should always refresh from the latest description. Consistent with D8. |

### `audio download --backfill-lyrics` (single song)

Add a new `--backfill-lyrics` flag to the `download_audio` command
signature (audio.py:966–985):

```python
backfill_lyrics: bool = typer.Option(
    False, "--backfill-lyrics",
    help="Only fetch structured lyrics from YouTube for an existing recording (no audio download)",
),
```

When `backfill_lyrics` is `True`, short-circuit the normal download flow:

1. **Validate exclusivity:** if any of `analyze`, `lrc`, `components`, `all`
   is also `True`, print:
   ```
   [red]--backfill-lyrics is mutually exclusive with --analyze, --lrc, --components, --all.[/red]
   ```
   and exit(1). Also reject `--dry-run` (it's already a no-op preview; just
   run the backfill).

2. **Look up the existing recording:**
   ```python
   recording = db_client.get_recording_by_song_id(song_id)
   if not recording:
       console.print(
           f"[red]No recording found for {song_id}. "
           f"Run 'sow-admin audio download {song_id}' first.[/red]"
       )
       raise typer.Exit(1)
   ```

3. **Resolve the YouTube URL** (B2):
   ```python
   yt_url = url or recording.youtube_url
   if not yt_url:
       console.print(
           f"[red]No YouTube URL available. Use --url to specify one.[/red]"
       )
       raise typer.Exit(1)
   ```

4. **Fetch metadata + parse**: call `extract_video_metadata(yt_url)`, then
   `parse_structured_lyrics(metadata.description)` (same helpers from §4/§5).
   Wrap in try/except `RuntimeError` — print a yellow warning and exit(1)
   on failure.

5. **Persist structured lyrics** via a new DB method (see §"DB client"
   below):
   ```python
   db_client.update_recording_structured_lyrics(
       hash_prefix=recording.hash_prefix,
       structured_lyrics_raw=structured_raw,
       structured_lyrics=structured_json_str,
   )
   ```

6. **Print a summary**: song title, hash prefix, YouTube URL, number of
   parsed sections (or "no section tags found — stored raw only"),
   and a hint to run `sow-admin audio show {song_id}` to verify or
   `sow-admin audio lrc {song_id}` to re-generate LRC with the new
   structured lyrics.

This path does **not** call `import_youtube_audio_for_song` at all — it
short-circuits before the function call at audio.py:1023.

### `audio batch --backfill-lyrics` (batch)

Add `--backfill-lyrics` as a new step flag to the `batch` command
(audio.py:5210–5253), alongside `--download`, `--lrc`, `--analyze`,
`--embedding`:

```python
backfill_lyrics: bool = typer.Option(
    False, "--backfill-lyrics",
    help="Backfill structured lyrics from YouTube for existing recordings",
),
```

Add `"backfill_lyrics"` to the `step_flags` dict (audio.py:5331–5336).
When it's the **only** selected step:

1. **No ThreadPoolExecutor / R2 / analysis client needed.** The backfill
   step is a lightweight metadata-only fetch — no audio download, no R2
   upload, no analysis job. Process songs **sequentially** (or with a
   simple ThreadPoolExecutor for the yt-dlp metadata calls, bounded by
   `download_concurrency`).

2. **Filter song IDs to those with recordings + `youtube_url`:** the
   existing `_resolve_song_ids` (5494–5563) returns song IDs based on
   album/status filters. After resolution, for each song_id, look up the
   recording; skip songs with no recording or no `youtube_url` (print a
   yellow "→ {song_id} (skipped: no recording)" or "(skipped: no
   YouTube URL)" line).

3. **Per-song processing:** call the same logic as the single-song path
   (steps 4–5 above). Use a quiet `Console` per worker (same pattern as
   `_download_worker` at audio.py:7012). Record the result in the
   `results` dict as `results[song_id]["backfill_lyrics"]` = `"completed"`
   / `"failed"` / `"skipped"`.

4. **No cascade:** `"backfill_lyrics"` does not feed into the unified poll
   loop. It's a terminal step. Print a batch summary (submitted/skipped/
   failed/total) at the end, reusing the `_print_stats` pattern.

5. **`--dry-run`:** list the songs that would be backfilled, showing their
   hash prefix and YouTube URL, with a "would fetch structured lyrics"
   label.

6. **`--force`:** allowed but has no effect (backfill always overwrites
   per B7). If `--force` is passed alongside `--backfill-lyrics`, accept
   it silently (do not trigger the "exactly one step" guard error at
   audio.py:5349–5362; add `"backfill_lyrics"` to the allowed-step set
   for the force scoping check).

7. **Mutual exclusivity with other steps:** `--backfill-lyrics` can be
   combined with `--lrc` in a single batch run — the backfill runs first
   (fetching structured lyrics), then the LRC step uses
   `_resolve_lyrics_text` (§7) which now prefers the freshly-backfilled
   structured lyrics. This is the **only** valid combination; combining
   with `--download`, `--analyze`, or `--embedding` prints an error.

   Implementation: in the step validation block (5330–5346), add logic:
   if `"backfill_lyrics" in selected_steps` and `selected_steps` contains
   any of `{"download", "analyze", "embedding"}`, print an error and
   exit(1). Allow `{"backfill_lyrics", "lrc"}` as the only multi-step
   combo. When both are selected, the backfill step runs first for each
   song, then `_submit_lrc_for_song` is called.

### New DB client method

Add to `DatabaseClient` (`db/client.py`, near the existing
`update_recording_status` at line 880):

```python
def update_recording_structured_lyrics(
    self,
    hash_prefix: str,
    structured_lyrics_raw: Optional[str],
    structured_lyrics: Optional[str],
) -> None:
    """Update the structured-lyrics columns on an existing recording.

    Used by the --backfill-lyrics flow to persist YouTube-description-
    derived structured lyrics without re-inserting the recording row.

    Args:
        hash_prefix: The hash prefix of the recording.
        structured_lyrics_raw: Raw YouTube description text (or None).
        structured_lyrics: Parsed structured-lyrics JSON string (or None).
    """
    with self.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE recordings
            SET structured_lyrics_raw = %s,
                structured_lyrics = %s,
                updated_at = NOW()
            WHERE hash_prefix = %s
            """,
            (structured_lyrics_raw, structured_lyrics, hash_prefix),
        )
```

This mirrors the pattern of `update_recording_lrc` (lines 1093–1146) — a
targeted column update with `updated_at = NOW()`, no full row re-insert.

### File-by-File additions

| File | Additional Change (beyond main spec) |
| --- | --- |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | Add `--backfill-lyrics` flag to `download_audio` (966) and `batch` (5210). Add `_backfill_lyrics_for_song()` helper (single-song logic: resolve URL, fetch metadata, parse, persist). Add `"backfill_lyrics"` to `step_flags` in batch. Add step-combo validation. |
| `ops/admin-cli/src/stream_of_worship/admin/db/client.py` | Add `update_recording_structured_lyrics(hash_prefix, structured_lyrics_raw, structured_lyrics)` method. |

## Out of Scope

- Webapp / Android display of structured lyrics (the structured lyrics
  live on `recordings`, which the webapp already reads via the admin
  read-client; surfacing them in the webapp UI is a separate spec).
- Editing structured lyrics through the interactive LRC editor.
- Changing the analysis-service `/api/v1/jobs/lrc` contract (D6 keeps the
  existing `lyrics_text` string field — no new optional fields).
- Populating `songs.sections` or `recordings.sections` from the parsed
  lyric labels (D3 explicitly keeps them separate).
---
