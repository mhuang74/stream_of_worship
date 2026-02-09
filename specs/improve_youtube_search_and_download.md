# YouTube Download Confirmation with Duration Filtering

## Context

The YouTube audio download feature in the admin CLI (`sow-admin audio download`) currently downloads the top search result without any preview or confirmation. This causes problems because:

1. **Multi-song performances get downloaded** - 30-minute worship sets or medleys are selected instead of single songs
2. **No user control** - No preview before download, users can't verify the selection
3. **Search quality** - Simple queries don't prefer official single-song recordings

This plan implements a preview-and-confirm workflow with duration warnings and manual URL fallback.

## User Requirements

1. Add search suffix to prefer official lyrics videos: `"官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美"`
2. Show video preview (title, duration, URL) before downloading
3. Only download with user confirmation
4. Allow manual YouTube URL input if auto-selection is rejected
5. Warn when videos exceed 7 minutes (420 seconds)
6. **Provide way to clear/delete previously downloaded audio** - If wrong audio was downloaded, user needs to remove it and download the correct version

## Implementation Plan

### 1. Enhance YouTubeDownloader Class

**File:** `src/stream_of_worship/admin/services/youtube.py`

#### Add `preview_video()` method
- Use `yt_dlp.extract_info(query, download=False)` to fetch metadata without downloading
- Handle `ytsearch1:` response structure (results in `entries[0]`)
- Return dict with: `id`, `title`, `duration`, `webpage_url`
- Return `None` if no results found
- Raise `RuntimeError` for download errors

#### Add `download_by_url()` method
- Accept direct YouTube URL instead of search query
- Reuse existing download logic but skip `ytsearch1:` prefix
- Use same file-finding and post-processing logic as `download()`

#### Update `build_search_query()` method
- Add optional `suffix: str = ""` parameter
- Append suffix to query if provided
- Maintain backward compatibility (existing calls work unchanged)

**Constants to add:**
```python
OFFICIAL_LYRICS_SUFFIX = "官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美"
DURATION_WARNING_THRESHOLD = 420  # 7 minutes
```

### 2. Add Recording Deletion Functionality

#### Add new `delete` command to audio.py

**Command:** `sow-admin audio delete SONG_ID [--yes]`

**Purpose:** Delete a recording and all associated R2 files (audio, stems, LRC) to allow re-downloading the correct version.

**Implementation steps:**

1. **Lookup recording** by song_id
   - If not found: show error and exit

2. **Display what will be deleted:**
   - Show recording info (hash_prefix, filename, size)
   - List R2 resources:
     - ✓ Audio file (r2_audio_url)
     - ✓ Stems file (r2_stems_url) if exists
     - ✓ LRC file (r2_lrc_url) if exists
   - Use Rich Panel to display deletion plan

3. **Confirmation prompt** (unless `--yes` flag)
   - "Delete this recording and all associated files? [y/n]"
   - Show warning that this cannot be undone

4. **Deletion sequence:**
   - Delete R2 objects (audio, stems, LRC) using R2Client
   - Delete database record using DatabaseClient
   - Show success message

5. **Error handling:**
   - If R2 deletion fails: log warning but continue (files may already be deleted)
   - If DB deletion fails: show error and exit
   - Use try/except for each R2 deletion to be fault-tolerant

**Helper function to add:**
```python
def _delete_r2_object_safe(
    r2_client: R2Client,
    url: Optional[str],
    description: str,
    console: Console
) -> None:
    """Safely delete R2 object, showing status and handling errors."""
    if not url:
        return
    try:
        _, key = R2Client.parse_s3_url(url)
        r2_client.delete_file(key)
        console.print(f"[green]✓ Deleted {description}[/green]")
    except Exception as e:
        console.print(f"[yellow]⚠ Could not delete {description}: {e}[/yellow]")
```

**Database method to add (in DatabaseClient):**
```python
def delete_recording(self, hash_prefix: str) -> None:
    """Delete a recording by hash_prefix."""
    cursor = self.connection.cursor()
    cursor.execute("DELETE FROM recordings WHERE hash_prefix = ?", (hash_prefix,))
    self.connection.commit()
```

**R2Client method to add:**
```python
def delete_file(self, s3_key: str) -> None:
    """Delete a file from R2 by its S3 key.

    Args:
        s3_key: Full S3 key (path within bucket)

    Raises:
        ClientError: If deletion fails
    """
    self._client.delete_object(Bucket=self.bucket, Key=s3_key)
```

Note: boto3's `delete_object()` is idempotent - it succeeds even if the object doesn't exist, so we don't need existence checks.

#### Add `--force` flag to download command

**Purpose:** Convenience flag to delete existing recording and re-download in one step.

**Changes to download_audio command:**

1. Add option:
   ```python
   force: bool = typer.Option(
       False, "--force", "-f", help="Replace existing recording"
   )
   ```

2. Modify existing recording check (currently lines 113-120):
   ```python
   existing = db_client.get_recording_by_song_id(song_id)
   if existing:
       if not force:
           console.print(
               f"[yellow]Recording already exists for this song "
               f"(hash: {existing.hash_prefix}). Use --force to replace.[/yellow]"
           )
           raise typer.Exit(0)
       else:
           # Delete existing recording
           console.print(f"[cyan]Deleting existing recording {existing.hash_prefix}...[/cyan]")
           _delete_recording_and_files(db_client, r2_client, existing, console)
           console.print("[green]Existing recording deleted. Proceeding with download...[/green]")
   ```

3. Add shared helper function:
   ```python
   def _delete_recording_and_files(
       db_client: DatabaseClient,
       r2_client: R2Client,
       recording: Recording,
       console: Console
   ) -> None:
       """Delete recording from DB and R2. Shared by delete command and --force flag."""
       # Delete R2 files
       _delete_r2_object_safe(r2_client, recording.r2_audio_url, "audio file", console)
       _delete_r2_object_safe(r2_client, recording.r2_stems_url, "stems file", console)
       _delete_r2_object_safe(r2_client, recording.r2_lrc_url, "LRC file", console)

       # Delete DB record
       db_client.delete_recording(recording.hash_prefix)
   ```

### 3. Update CLI Command Flow

**File:** `src/stream_of_worship/admin/commands/audio.py` (lines 73-183)

#### Add new CLI options
```python
url: Optional[str] = typer.Option(
    None, "--url", "-u", help="Direct YouTube URL (skip search)"
)
skip_confirm: bool = typer.Option(
    False, "--yes", "-y", help="Skip confirmation prompt"
)
force: bool = typer.Option(
    False, "--force", "-f", help="Replace existing recording if it exists"
)
```

#### Implement new download flow

**Step 0: Check for existing recording**
- Look up recording by song_id
- If exists and `--force` not set: show error message with suggestion to use `--force` or `delete` command
- If exists and `--force` is set: delete existing recording and all R2 files, then continue

**Step 1: Determine URL or search**
- If `--url` provided: use it directly
- Otherwise: build search query with official lyrics suffix

**Step 2: Preview video**
- Call `downloader.preview_video(query)` or `preview_video(url)`
- Handle no results case (exit with error message)

**Step 3: Display video preview panel**
- Show video title, formatted duration (MM:SS), and URL
- Display warning badge if duration > 420 seconds
- Use Rich Panel with color-coded border:
  - 🟨 Yellow + warning if > 7 minutes
  - 🟩 Green if ≤ 7 minutes

**Step 4: Confirmation prompt**
- Skip if `--yes` flag set
- Prompt: "Download this video? [y/n]"
- If rejected: offer manual URL input

**Step 5: Manual URL fallback (if needed)**
- Prompt: "Enter YouTube URL (or press Enter to cancel):"
- Validate URL format (contains youtube.com or youtu.be)
- Re-preview the manual URL
- Show video info again and confirm
- Up to 3 attempts for valid URL

**Step 6: Download and persist**
- Download using appropriate method (`download()` or `download_by_url()`)
- Continue with existing flow: hash → upload to R2 → persist recording

#### Helper functions to add
```python
def _format_duration_mmss(seconds: float) -> str:
    """Format seconds as MM:SS."""

def _display_video_preview(video_info: dict, console: Console, threshold: int = 420) -> None:
    """Display video preview in Rich Panel with duration warning."""

def _prompt_confirmation(message: str) -> bool:
    """Prompt for y/n confirmation, return True if accepted."""

def _prompt_manual_url() -> Optional[str]:
    """Prompt for manual URL, validate format, return URL or None."""
```

### 4. User Interaction Flow

#### Download Flow (with --force support)

```
┌─────────────────────────────────────────────┐
│ START: sow-admin audio download SONG_ID    │
└──────────────────┬──────────────────────────┘
                   │
         ┌─────────▼──────────┐
         │ Recording exists?  │
         └─────┬─────────┬────┘
              YES       NO
               │         │
         ┌─────▼─────┐   │
         │ --force?  │   │
         └─┬───────┬─┘   │
          YES     NO     │
           │       │     │
    Delete existing  Error: "Use --force"
    recording + R2   → EXIT
           │             │
           └────┬────────┘
                │
         ┌─────▼─────────┐
         │ --url provided?   │
         └─────┬─────────┬───┘
              YES       NO
               │         │
               │    Build search query
               │    + add official lyrics suffix
               │         │
               └────┬────┘
                    │
           ┌────────▼────────┐
           │ Preview video   │
           │ (metadata only) │
           └────────┬────────┘
                    │
           ┌────────▼────────┐
           │ Results found?  │
           └─────┬─────┬─────┘
                YES   NO
                 │     │
                 │   Error: No results
                 │   → EXIT
                 │
        ┌────────▼────────┐
        │ Display preview │
        │ • Title         │
        │ • Duration      │
        │ • URL           │
        │ • Warning (>7m) │
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │ --yes flag set? │
        └─────┬─────┬─────┘
             YES   NO
              │     │
              │  Prompt: "Download? [y/n]"
              │     │
              │  ┌──▼──┐
              │  │Accept?
              │  └─┬─┬─┘
              │   YES NO
              └───┬─┘ │
                  │   │
                  │ Prompt: "Enter URL or cancel"
                  │   │
                  │ ┌─▼──────┐
                  │ │URL given?
                  │ └─┬────┬─┘
                  │  YES  NO
                  │   │    │
                  │   │  EXIT
                  │   │
                  │  Re-preview → Confirm
                  │   │
                  └───┴───┐
                          │
                 ┌────────▼────────┐
                 │ Download audio  │
                 └────────┬────────┘
                          │
                 ┌────────▼────────┐
                 │ Hash → Upload   │
                 │ → Persist DB    │
                 └────────┬────────┘
                          │
                        EXIT
```

#### Delete Command Flow

```
┌─────────────────────────────────────────────┐
│ START: sow-admin audio delete SONG_ID      │
└──────────────────┬──────────────────────────┘
                   │
         ┌─────────▼──────────┐
         │ Recording exists?  │
         └─────┬──────────┬───┘
              YES        NO
               │          │
               │      Error: "Not found"
               │      → EXIT
               │
      ┌────────▼────────┐
      │ Display info:   │
      │ • Hash prefix   │
      │ • Filename      │
      │ • Audio URL     │
      │ • Stems URL     │
      │ • LRC URL       │
      └────────┬────────┘
               │
      ┌────────▼────────┐
      │ --yes flag set? │
      └─────┬─────┬─────┘
           YES   NO
            │     │
            │  Prompt: "Delete? [y/n]"
            │     │
            │  ┌──▼──┐
            │  │Confirm?
            │  └─┬─┬─┘
            │   YES NO
            └───┬─┘ │
                │  EXIT
                │
       ┌────────▼────────┐
       │ Delete R2 files │
       │ • Audio         │
       │ • Stems (if any)│
       │ • LRC (if any)  │
       └────────┬────────┘
                │
       ┌────────▼────────┐
       │ Delete DB record│
       └────────┬────────┘
                │
       ┌────────▼────────┐
       │ Show success    │
       └────────┬────────┘
                │
              EXIT
```

### 5. Error Handling

| Error Condition | Handling Strategy |
|-----------------|-------------------|
| No search results | Show error + suggest `--url` option |
| Invalid manual URL | Validate format, re-prompt (max 3 attempts) |
| yt-dlp network error | Catch `DownloadError`, show friendly message |
| Missing duration metadata | Show "Unknown", add extra warning |
| Video unavailable/private | Show error, offer manual URL input |
| Recording already exists | Show error + suggest `--force` or `delete` command |
| Recording not found (delete) | Show error message and exit |
| R2 deletion fails | Log warning, continue (file may already be deleted) |
| DB deletion fails | Show error and exit (do not continue) |

### 6. Testing Strategy

#### Unit Tests (`tests/admin/test_youtube.py`)
- `test_preview_video_success` - Returns correct video info dict
- `test_preview_video_no_results` - Returns None for no results
- `test_preview_video_handles_ytsearch` - Extracts from `entries[0]`
- `test_download_by_url_success` - Downloads from direct URL
- `test_build_query_with_suffix` - Appends suffix correctly
- `test_build_query_backward_compat` - Works without suffix parameter

#### Unit Tests for DatabaseClient (`tests/admin/test_db_client.py`)
- `test_delete_recording_success` - Deletes recording by hash_prefix
- `test_delete_recording_not_found` - Handles non-existent recording

#### Integration Tests (`tests/admin/test_audio_download_flow.py`)
- Test full flow with confirmation (accept)
- Test full flow with rejection → manual URL
- Test `--url` option
- Test `--yes` flag skips confirmation
- Test duration warning display
- Test `--force` flag with existing recording
- Test download with existing recording (no --force) shows error

#### Integration Tests for Delete (`tests/admin/test_audio_delete.py`)
- Test delete command success
- Test delete with `--yes` flag
- Test delete with user confirmation (accept)
- Test delete with user confirmation (reject)
- Test delete non-existent recording
- Test R2 deletion failure handling

#### Manual Testing Checklist

**Download Flow:**
- [ ] Auto-search with Chinese song title
- [ ] Accept auto-selected video
- [ ] Reject and provide manual URL
- [ ] Reject and cancel
- [ ] Use `--url` flag with direct URL
- [ ] Use `--yes` flag to skip confirmation
- [ ] Verify warning shows for videos > 7 minutes
- [ ] Test with no internet connection
- [ ] Test with invalid manual URL
- [ ] Attempt download with existing recording (should show error)
- [ ] Download with `--force` flag to replace existing recording

**Delete Flow:**
- [ ] Delete a recording with confirmation
- [ ] Delete with `--yes` flag (skip confirmation)
- [ ] Attempt to delete non-existent recording
- [ ] Verify R2 files are deleted (check R2 console)
- [ ] Verify DB record is deleted
- [ ] Test delete then re-download workflow

### 7. Example Usage

```bash
# Standard flow with auto-search and confirmation
sow-admin audio download SONG_001

# Direct URL (skip search)
sow-admin audio download SONG_001 --url https://www.youtube.com/watch?v=abc123

# Skip confirmation (for automation)
sow-admin audio download SONG_001 --yes

# Replace existing recording (delete + re-download)
sow-admin audio download SONG_001 --force

# Combine flags: direct URL + force + skip confirmation
sow-admin audio download SONG_001 --url https://youtube.com/watch?v=xyz --force --yes

# Preview search query only (existing flag)
sow-admin audio download SONG_001 --dry-run

# Delete a recording and associated R2 files
sow-admin audio delete SONG_001

# Delete without confirmation prompt
sow-admin audio delete SONG_001 --yes

# Typical workflow when wrong audio was downloaded:
# Option 1: Use --force flag
sow-admin audio download SONG_001 --force --url https://youtube.com/watch?v=correct_video

# Option 2: Delete then download
sow-admin audio delete SONG_001
sow-admin audio download SONG_001 --url https://youtube.com/watch?v=correct_video
```

## Critical Files

1. **`src/stream_of_worship/admin/services/youtube.py`** - Add `preview_video()` and `download_by_url()` methods
2. **`src/stream_of_worship/admin/commands/audio.py`** - Implement confirmation flow, delete command, --force flag (lines 73-183 and new delete command)
3. **`src/stream_of_worship/admin/db/client.py`** - Add `delete_recording()` method to DatabaseClient
4. **`src/stream_of_worship/admin/services/r2.py`** - Verify R2Client has `delete_file()` method (may need to add if missing)
5. **`tests/admin/test_youtube.py`** - Unit tests for new YouTubeDownloader methods
6. **`tests/admin/test_db_client.py`** - Unit tests for delete_recording()
7. **New file: `tests/admin/test_audio_download_flow.py`** - Integration tests for download CLI flow
8. **New file: `tests/admin/test_audio_delete.py`** - Integration tests for delete command

## Verification Plan

After implementation:

1. **Functional verification - Download:**
   - Run `sow-admin audio download <song_id>` and verify preview is shown
   - Confirm that video info includes title, duration, and URL
   - Verify duration warning appears for videos > 7 minutes
   - Test rejection → manual URL flow works correctly
   - Verify `--url` and `--yes` flags work as expected
   - Test `--force` flag replaces existing recording

2. **Functional verification - Delete:**
   - Run `sow-admin audio delete <song_id>` and verify confirmation prompt
   - Verify deletion info shows all R2 resources
   - Confirm R2 files are deleted (check R2 console or re-download)
   - Confirm DB record is deleted (check with `sow-admin audio list`)
   - Test `--yes` flag skips confirmation

3. **Search quality verification:**
   - Test that the official lyrics suffix improves search results
   - Verify single songs are preferred over multi-song performances
   - Check that Chinese metadata is handled correctly

4. **Test suite verification:**
   - Run `PYTHONPATH=src uv run pytest tests/admin/test_youtube.py -v`
   - Run `PYTHONPATH=src uv run pytest tests/admin/test_db_client.py -v`
   - Run `PYTHONPATH=src uv run pytest tests/admin/test_audio_download_flow.py -v`
   - Run `PYTHONPATH=src uv run pytest tests/admin/test_audio_delete.py -v`
   - Verify all existing tests still pass

5. **Edge case verification:**
   - Test with song that has no results
   - Test with network disconnected
   - Test with invalid manual URLs
   - Test with videos missing duration metadata
   - Test delete when R2 files already deleted (should handle gracefully)
   - Test --force when no existing recording (should work normally)

6. **Integration workflow verification:**
   - Download wrong audio → delete → download correct audio
   - Download wrong audio → download again with --force and correct URL
   - List recordings before and after deletion to verify cleanup

## Backward Compatibility

✅ **All changes are additive:**
- `download()` method unchanged
- `build_search_query()` has new optional parameter (default preserves old behavior)
- New methods don't affect existing functionality
- CLI command has new optional flags (defaults preserve old behavior)
- No database schema changes needed

## Implementation Sequence

**Phase 1:** YouTubeDownloader enhancements (1-2 hours)
- Add `preview_video()`, `download_by_url()` methods
- Update `build_search_query()` with suffix parameter
- Write unit tests

**Phase 2:** Database and R2 deletion support (0.5-1 hour)
- Add `delete_recording()` method to DatabaseClient
- Add `delete_file()` method to R2Client (currently missing)
- Write unit tests for delete_recording() and delete_file()

**Phase 3:** CLI command updates - Download (2-3 hours)
- Add helper functions for formatting and prompting
- Implement preview and confirmation flow
- Add manual URL input fallback
- Add `--force` flag and existing recording handling
- Write integration tests for download flow

**Phase 4:** CLI command updates - Delete (1-1.5 hours)
- Implement `delete` command with confirmation
- Add shared deletion helper function
- Add R2 safe deletion with error handling
- Write integration tests for delete command

**Phase 5:** Testing and refinement (1-2 hours)
- Manual testing with real YouTube videos
- Test with Chinese song metadata
- Verify duration warnings
- Test delete and --force workflows
- Test edge cases (R2 failures, missing files, etc.)

**Total estimated time: 5.5-9.5 hours**
