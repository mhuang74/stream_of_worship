# Admin CLI Audio Download YouTube Search Result Matching

## Summary

Enhance the `sow-admin audio download` YouTube search capability so that
instead of always downloading the top search result, the system scans the top
5 YouTube search results and selects the first candidate whose title matches
the expected song title. This applies to both the interactive single-song
download path and the batch download paths.

If no candidate matches, the interactive path prompts the user to provide a
manual URL (existing fallback behavior), while the batch path aborts with an
error.

## Problem

The current implementation uses `ytsearch1:` (returns exactly 1 result) and
unconditionally takes `entries[0]`. This means YouTube's relevance ranking alone
determines which video is downloaded. When the top result is the wrong video
(for example, a different song with a similar name, a live performance, or a
cover), the admin gets the wrong audio file.

The existing title-mismatch detection (`_extract_chinese_title_from_youtube`)
runs **after** the download decision is already locked in. In batch mode it
wastes bandwidth by downloading, then deleting the file on mismatch. In
interactive mode it only warns but still offers the wrong video for confirmation.

## Current Behavior

### Key Files

| File | Role |
| --- | --- |
| `ops/admin-cli/src/stream_of_worship/admin/services/youtube.py` | YouTube downloader service (`preview_video`, `download`, `download_with_info`) |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | CLI command logic (`import_youtube_audio_for_song`, `_download_and_create_recording`, `_download_if_needed`, `_extract_chinese_title_from_youtube`) |
| `ops/admin-cli/tests/admin/test_youtube.py` | Unit tests for the YouTube service |

### Current Flow

1. `build_search_query()` constructs a query from `song.title`, `song.composer`,
   `song.album_name`, and the hardcoded `OFFICIAL_LYRICS_SUFFIX`.
2. `preview_video()` calls `ytsearch1:{query}` and takes `entries[0]`.
3. `_extract_chinese_title_from_youtube()` extracts the Chinese title from the
   `【...】` brackets in the video title and does exact string equality with
   `song.title`.
4. Interactive path: mismatch is a warning + confirmation prompt.
5. Batch path: mismatch is a hard fail (deletes downloaded file, returns error).

## Proposed Behavior

### 1. `preview_video()` Multi-Candidate Scan

Change `preview_video()` to accept an optional `max_results` parameter
(defaulting to 1 for backward compatibility) and a `song_title` parameter.

When `max_results > 1` and `song_title` is provided:

- Use `ytsearch{max_results}:{query}` instead of `ytsearch1:`.
- Iterate through all returned entries.
- For each entry, extract the Chinese title using
  `_extract_chinese_title_from_youtube()`.
- Return the first entry where the extracted title **exactly equals**
  `song_title`.
- If no entry matches, return `None` (signals "no match found" to the caller).

When `max_results` is 1 or `song_title` is not provided, behavior is unchanged
(direct URL handling and single-result search remain as-is).

### 2. `download_with_info()` Multi-Candidate Scan

Add an optional `max_results` parameter (default 1) and a `song_title`
parameter. When both are provided:

- Use `ytsearch{max_results}:{query}` instead of `ytsearch1:`.
- Extract metadata from all entries **without downloading** (use a two-phase
  approach: first `extract_info(download=False)` to scan candidates, then
  `download_by_url()` on the selected video's URL).
- Select the first matching entry (same exact-title match logic as
  `preview_video`).
- Download the selected video.
- Return the same tuple `(Path, webpage_url, video_title)`.

If no candidate matches, raise `RuntimeError` with a descriptive message.

### 3. `download()` Multi-Candidate Scan

Add the same `max_results` and `song_title` optional parameters. Delegate to
the same scan-then-download approach as `download_with_info` but return only
the `Path`.

### 4. Interactive Path (`import_youtube_audio_for_song`)

Update lines ~759-781:

- Call `preview_video(search_or_url, max_results=5, song_title=song.title)`.
- If `video_info` is `None` (no match among top 5):
  - Print a message: "No matching title found in top 5 results."
  - Fall through to the `_prompt_manual_url()` prompt (existing UI, lines
    787-818, reused as-is).
- If `video_info` is returned, the title-check at lines 773-778 becomes a
  redundant safety-net (it should still pass since we pre-filtered, but keep it
  for the direct-URL case where `--url` bypasses search).
- Remove the pre-mismatch warning for search results since the result is now
  pre-filtered to match. Keep the check for the direct-URL case.

### 5. Batch Path (`_download_and_create_recording`)

Update lines ~4745:

- Call `download_with_info(query, max_results=5, song_title=song.title)`.
- If the call raises `RuntimeError` due to no match, catch it, print a clear
  error, and return `(None, "no matching title in top 5 search results")`.
- Remove or simplify the post-download title check at lines 4747-4759 since the
  match is now pre-validated. Keep the duplicate-hash check (lines 4771-4788)
  as an additional safety net.
- Benefit: no bandwidth wasted downloading the wrong video.

### 6. Batch Path (`_download_if_needed`)

Update lines ~4867:

- Same change as `_download_and_create_recording`: call `download_with_info`
  with `max_results=5` and `song_title=song.title`.
- On `RuntimeError` (no match), update DB status to `"failed"` and return
  `{"download": "failed", "error": "no matching title in top 5 search results"}`.

## Matching Algorithm

```
function find_best_candidate(entries, song_title):
    for entry in entries:
        video_title = entry["title"]
        chinese_title = extract_chinese_title_from_youtube(video_title)
        if chinese_title is not None and chinese_title == song_title:
            return entry
    return None
```

- Uses the existing `_extract_chinese_title_from_youtube()` regex:
  `re.match(r"【([^】\s]+)", video_title)`.
- Extracts the Chinese text from the first `【...】` bracket, stopping at
  whitespace.
- Compares for exact string equality with `song.title`.
- Returns the **first** matching candidate (YouTube already ranks by relevance,
  so the first match is the most relevant match).

No fuzzy matching, no similarity scoring, no channel preference. Exact match
only, as confirmed by the user.

## Edge Cases

| Case | Behavior |
| --- | --- |
| Video title has no `【...】` brackets | `extract_chinese_title` returns `None`, candidate is skipped |
| Fewer than 5 results returned | Iterate over whatever entries are returned; if none match, proceed to no-match behavior |
| Search query returns 0 results | `entries` is empty or `None` → `preview_video` returns `None` → "No results found" (existing behavior) |
| Direct URL (`--url`) provided | `max_results` and `song_title` are not used; bypass search entirely (existing behavior) |
| Multiple candidates match | First match wins (highest YouTube relevance rank among matches) |

## API Changes

### `YouTubeDownloader.preview_video`

```python
def preview_video(
    self,
    query: str,
    max_results: int = 1,
    song_title: str | None = None,
) -> Optional[dict[str, Any]]:
```

### `YouTubeDownloader.download`

```python
def download(
    self,
    query: str,
    max_results: int = 1,
    song_title: str | None = None,
) -> Path:
```

### `YouTubeDownloader.download_with_info`

```python
def download_with_info(
    self,
    query: str,
    max_results: int = 1,
    song_title: str | None = None,
) -> tuple[Path, Optional[str], Optional[str]]:
```

All three methods are backward-compatible: when `max_results=1` (default) and
`song_title=None`, behavior is identical to the current implementation.

## Implementation Steps

### Step 1: Refactor `_extract_chinese_title_from_youtube`

Move `_extract_chinese_title_from_youtube` from `audio.py` (line 4687) to
`youtube.py` as a module-level function (or static method on
`YouTubeDownloader`). This makes it available to the service layer for
candidate matching.

Update all call sites in `audio.py` to import from `youtube.py`.

### Step 2: Add Candidate Selection Helper

Add a private helper in `youtube.py`:

```python
def _select_best_candidate(
    entries: list[dict[str, Any]],
    song_title: str,
) -> Optional[dict[str, Any]]:
    for entry in entries:
        if entry is None:
            continue
        video_title = entry.get("title")
        chinese_title = _extract_chinese_title_from_youtube(video_title)
        if chinese_title is not None and chinese_title == song_title:
            return entry
    return None
```

### Step 3: Update `preview_video`

- Accept `max_results` and `song_title` parameters.
- When `song_title` is provided and query is a search (not a URL):
  - Use `ytsearch{max_results}:{query}`.
  - Call `_select_best_candidate(info["entries"], song_title)`.
  - Return the matched entry as the same dict format, or `None`.
- When `song_title` is `None` or query is a URL: existing behavior.

### Step 4: Update `download` and `download_with_info`

Both need a two-phase approach when `song_title` is provided:

1. `extract_info(f"ytsearch{max_results}:{query}", download=False)` to get
   candidate metadata without downloading.
2. `_select_best_candidate()` to pick the right entry.
3. If no match, raise `RuntimeError("No matching title found in top {N} results for query: {query}")`.
4. Download the selected video by its `webpage_url` using `download_by_url()`.

When `song_title` is `None`, keep the existing `ytsearch1:` + download behavior.

### Step 5: Update Interactive Path

In `import_youtube_audio_for_song` (audio.py ~759):

- Pass `max_results=5, song_title=song.title` to `preview_video`.
- When `video_info is None`:
  - Print "No matching title found in top 5 search results."
  - Fall through to `_prompt_manual_url()` (existing code at lines 787-818).
- Remove the pre-mismatch warning (lines 773-781) for the search-based path
  since results are now pre-filtered. Keep the check for the `--url` path.
- No changes to the download call (lines 820-828): it already uses
  `download_by_url` for URLs and `download(query)` for search queries. Update
  `download()` call to pass `max_results=5, song_title=song.title`.

### Step 6: Update Batch Paths

In `_download_and_create_recording` (audio.py ~4745):

- Replace `downloader.download_with_info(query)` with
  `downloader.download_with_info(query, max_results=5, song_title=song.title)`.
- Wrap in try/except for `RuntimeError` with no-match message.
- Remove or simplify the post-download title check (lines 4747-4759) since the
  match is pre-validated. Keep the duplicate-hash check (lines 4771-4788).

In `_download_if_needed` (audio.py ~4867):

- Same change: pass `max_results=5, song_title=song.title`.
- On `RuntimeError`, set download status to `"failed"` and return error dict.

### Step 7: Update Tests

Update `tests/admin/test_youtube.py`:

- `TestPreviewVideo`: Update existing test to pass `max_results=1` (default)
  and verify backward compatibility.
- Add `TestPreviewVideoMultiCandidate`: Mock `yt-dlp` to return 3-5 entries,
  verify the correct candidate is selected by title match, verify `None` is
  returned when no match, verify direct URL path still works.
- `TestDownload`: Verify `download()` delegates to `download_by_url` when
  `song_title` is provided (two-phase: scan then download the matched URL).
- `TestDownloadWithInfo`: Same two-phase verification, verify tuple return with
  correct URL/title from the matched candidate.
- Add test for `_select_best_candidate` directly (unit test the helper).
- Add test for `_extract_chinese_title_from_youtube` in its new location
  (verify it still produces the same results after the move).

### Step 8: Verify

```bash
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest ops/admin-cli/tests/admin/test_youtube.py -v
```

## Impact Assessment

### Backward Compatibility

- All three service methods (`preview_video`, `download`, `download_with_info`)
  have new optional parameters with backward-compatible defaults.
- Direct URL (`--url`) path is unchanged — search matching only applies to
  query-based search.
- `catalog insert --youtube` uses direct URLs, so it is unaffected.

### Performance

- `ytsearch5:` returns 5 entries instead of 1, but yt-dlp fetches metadata for
  all in a single call (no additional round-trips).
- The two-phase approach in `download`/`download_with_info` adds one
  `extract_info(download=False)` call before the actual download. This is a
  metadata-only call (fast, no audio transferred).
- Net effect: negligible latency increase, significant accuracy improvement.

### Bandwidth Savings

- Batch path no longer downloads the wrong video, then deletes it on mismatch.
- The two-phase approach downloads audio only for the matched candidate.

## Acceptance Criteria

- `sow-admin audio download <song_id>` (without `--url`) scans the top 5 YouTube
  search results and selects the first candidate whose `【...】` bracket title
  exactly matches the song title from the database.
- If no candidate matches in interactive mode, the user is prompted for a manual
  URL (existing fallback behavior).
- If no candidate matches in batch mode, the download is aborted with a clear
  error message and no audio is downloaded.
- Direct URL (`--url`) downloads are unaffected by the search matching change.
- All existing unit tests pass with no behavioral changes for the default
  (`max_results=1, song_title=None`) call path.
- New unit tests cover multi-candidate selection, no-match scenarios, and
  backward compatibility.

## Assumptions

- The `OFFICIAL_LYRICS_SUFFIX` query still produces relevant SOP (Stream of
  Praise) videos with the `【...】` bracket format in their titles.
- YouTube's relevance ranking is good enough that the first matching candidate
  among the top 5 is the correct video.
- The `_extract_chinese_title_from_youtube` regex continues to match the title
  format used by SOP videos.
- No new dependencies are needed (yt-dlp's `ytsearchN:` extractor is already
  available).
