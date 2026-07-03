# Admin CLI Audio Download/Batch: Bracketed Song Title Matching

## Summary

Enhance the `sow-admin audio download` and `sow-admin audio batch` YouTube
title extraction and title-matching logic to handle catalog song titles that
embed the Chinese name in square brackets (e.g. `Holy, Holy [聖潔榮耀主]`).

Today the catalog title is fed verbatim into the YouTube search query (leaking
`[]` characters) and the title extractor stops at the first whitespace inside
the `【...】` bracket, so it returns `Holy,` instead of the full title. The
result is a failed auto-match ("No matching title found in top 5 results") and
a false `⚠ Title mismatch: ... got 'Holy,'` warning when a manual URL is used.

## Problem

### Concrete example

- Catalog title: `Holy, Holy [聖潔榮耀主]`
- YouTube title: `【Holy, Holy 聖潔榮耀主】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (30)`

### Observed behavior

```
Search query: Holy, Holy [聖潔榮耀主] 何俊傑、林以諾 深愛耶穌 官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美
WARNING: [youtube:search] Incomplete data received. Retrying (1/3)...
...
No matching title found in top 5 results.
Enter YouTube URL (or press Enter to cancel): https://www.youtube.com/watch?v=mqrGn4uG7a0
⚠ Title mismatch: expected 'Holy, Holy [聖潔榮耀主]', got 'Holy,' from video '...'
```

### Root causes

There are two distinct bugs, both stemming from the assumption that a song
title is a single no-space Chinese token followed by an English subtitle inside
`【Chinese English】`.

**Bug 1 — Search query leaks `[]` brackets.**

`YouTubeDownloader.build_search_query`
(`ops/admin-cli/src/stream_of_worship/admin/services/youtube.py:299`) joins
`song.title` verbatim into the query, producing
`Holy, Holy [聖潔榮耀主] 何俊傑、林以諾 ...`. The literal `[]` characters never
appear in the YouTube video title (`【Holy, Holy 聖潔榮耀主】...`), so the query
is noisy and yields poorer search results.

**Bug 2 — Title extraction stops at the first space.**

`_extract_chinese_title_from_youtube`
(`ops/admin-cli/src/stream_of_worship/admin/services/youtube.py:66`) uses
`re.match(r"【([^】\s]+)", video_title)`, which captures up to the first
whitespace OR closing `】`. For `【Holy, Holy 聖潔榮耀主】...` it captures `Holy,`
(stops at the space after `Holy,`). Then:

- `_select_best_candidate` (youtube.py:90) does an exact compare
  `Holy, == Holy, Holy [聖潔榮耀主]` → no match → `preview_video` returns `None`
  → "No matching title found in top 5 results."
- The mismatch warning in
  `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:786` and
  `:820` fires with `got 'Holy,'`.

The existing code handles **one** convention:
`【Chinese English】` where Chinese is a single no-space token. The catalog
`English [Chinese]` format paired with the `【English Chinese】` YouTube format
is a second convention that is not handled.

> Note: The `WARNING: [youtube:search] Incomplete data received. Retrying...`
> messages are yt-dlp / YouTube search throttling, **not** caused by the
> brackets. Stripping the brackets improves query **quality** but does not fix
> network throttling; that remains handled by the existing manual-URL fallback.

## Current Behavior

### Key Files

| File | Role |
| --- | --- |
| `ops/admin-cli/src/stream_of_worship/admin/services/youtube.py` | `build_search_query`, `_extract_chinese_title_from_youtube`, `_select_best_candidate`, `preview_video`, `download`, `download_with_info`, `_scan_for_match`, `_download_with_match` |
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | CLI command logic; imports `_extract_chinese_title_from_youtube`; emits the mismatch warning at lines 786 (`--url` flow) and 820 (manual-URL flow) |
| `ops/admin-cli/tests/admin/test_youtube.py` | Unit tests for the YouTube service (`TestExtractChineseTitle`, `TestSelectBestCandidate`, `TestPreviewVideoMultiCandidate`) |

### Current Flow

1. `build_search_query()` constructs a query from `song.title`,
   `song.composer`, `song.album_name`, and `OFFICIAL_LYRICS_SUFFIX`, joining
   the title verbatim (brackets included).
2. `preview_video()` (or `_scan_for_match()` for downloads) calls
   `ytsearch{max_results}:{query}` and iterates entries.
3. `_select_best_candidate()` calls `_extract_chinese_title_from_youtube()`
   on each entry title and does exact string equality with `song_title`.
4. `_extract_chinese_title_from_youtube()` captures `【([^\】\s]+)` — the first
   whitespace-free token after `【`.
5. Interactive path: on manual URL, `_extract_chinese_title_from_youtube()` is
   called again for the mismatch warning; mismatch is a warning + confirmation.
6. Batch path (`_download_and_create_recording`): a failed match raises
   `RuntimeError("No matching title found ...")` and aborts the song.

## Proposed Behavior

### 1. Strip `[]` from the search query (`build_search_query`)

In `build_search_query` (youtube.py:299), normalize the `title` argument before
joining it into the query: replace any `[...]` segment with its bare contents
(space-separated) and collapse internal whitespace.

- `Holy, Holy [聖潔榮耀主]` → `Holy, Holy 聖潔榮耀主`
- `一生敬拜祢` (no brackets) → `一生敬拜祢` (unchanged)

This benefits `audio download`, `audio batch` (audio.py:4726), and the other
call site (audio.py:4851) automatically, since they all go through this method.

Implementation detail: perform the normalization on the `title` parameter only
(composer/album/suffix are not bracket-bearing). Keep the change localized to
`build_search_query` so callers are unaffected.

### 2. Add a bracket-aware matcher (`_titles_match`)

Add two helpers in `youtube.py`:

#### `_extract_bracket_content(video_title) -> Optional[str]`

Captures the **full** content of the first `【…】` bracket using
`re.match(r"【([^】]+)", video_title)`, returning e.g.
`Holy, Holy 聖潔榮耀主`. Returns `None` when there is no `【` bracket.

`_extract_chinese_title_from_youtube` is kept as-is (still used as a
first-token fallback/signal and exported); `_titles_match` is the new source of
truth for matching decisions.

#### `_titles_match(song_title, video_title) -> bool`

Unified matching logic supporting **both** conventions:

1. Parse the catalog title:
   - `chinese_segments = re.findall(r"\[([^\]]+)\]", song_title)`
   - `english_part = re.sub(r"\s*\[[^\]]+\]\s*", " ", song_title).strip()`
2. Extract `bracket = _extract_bracket_content(video_title)`.
   - If no bracket, fall back to a normalized raw-title compare
     (`_normalize_for_match(song_title) == _normalize_for_match(video_title)`).
3. **Convention 2** (catalog has `[Chinese]`): match iff the normalized
   `english_part` **and** every normalized Chinese segment all appear
   (substring containment) in the normalized bracket content.
   - For the user's case: `holy, holy` ⊆ `holy, holy 聖潔榮耀主` ✓ and
     `聖潔榮耀主` ⊆ `holy, holy 聖潔榮耀主` ✓ → match.
4. **Convention 1** (catalog has no `[]`): preserve existing behavior — match
   iff the catalog title equals the **first whitespace token** of the bracket
   content (so `一生敬拜祢` still matches `【一生敬拜祢 All the Days】`). This
   keeps all existing `TestSelectBestCandidate` / `TestExtractChineseTitle`
   tests green.

`_normalize_for_match(s)` is a small helper that collapses whitespace and
lowercases (no punctuation stripping beyond what the bracket parsing already
removes).

### 3. Use the matcher in candidate selection (`_select_best_candidate`)

Update `_select_best_candidate` (youtube.py:90) to call `_titles_match` instead
of the `chinese_title == song_title` exact compare. The old
`_extract_chinese_title_from_youtube`-based compare is replaced; the helper
remains for the fallback/display path.

This change flows through automatically to:
- `preview_video` multi-candidate scan (youtube.py:380)
- `_scan_for_match` (youtube.py:670), used by `_download_with_match` /
  `_download_with_match_info`, used by `download` / `download_with_info`,
  used by both `audio download` (interactive) and `audio batch`
  (`_download_and_create_recording`).

### 4. Gate the mismatch warning on the matcher (`audio.py`)

At audio.py:786 (`--url` flow) and audio.py:820 (manual-URL flow), change from
"always warn if `chinese_title != song.title`" to "warn only if
`not _titles_match(song.title, video_title)`".

- Import `_titles_match` (and optionally `_extract_bracket_content` for richer
  `got '…'` display, showing the full bracket content instead of just `Holy,`).
- This eliminates the false `⚠ Title mismatch` for the user's case, since
  `_titles_match` returns `True` when both the English and Chinese parts appear
  in the bracket content.

### 5. Tests (`test_youtube.py`)

Add a `TestTitlesMatch` class covering:

- Convention-2 match using the user's exact strings (catalog
  `Holy, Holy [聖潔榮耀主]`, video
  `【Holy, Holy 聖潔榮耀主】官方歌詞版MV ...`).
- Convention-2 non-match (e.g. wrong Chinese segment).
- Convention-1 regression: `一生敬拜祢` vs
  `【一生敬拜祢 All the Days of My Life】官方歌詞版MV` → match.
- Convention-1 non-match: `一生敬拜祢` vs `【另一首 All the Days】MV` → no match.
- Bracket-less video title (no `【`): falls back to normalized raw compare.
- Catalog title with multiple `[...]` segments (defensive).

Add a `TestBuildSearchQuery` (or extend an existing one) test asserting that
`build_search_query(title="Holy, Holy [聖潔榮耀主]", ...)` produces a query
containing `Holy, Holy 聖潔榮耀主` and containing **no** `[` or `]` characters.

## Verification

```bash
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest ops/admin-cli/tests/admin/test_youtube.py -v
```

Lint:

```bash
ruff check ops/admin-cli/src/stream_of_worship/admin/services/youtube.py ops/admin-cli/src/stream_of_worship/admin/commands/audio.py
```

## Scope and Non-Goals

- **In scope:** `audio download` and `audio batch` title extraction/matching
  for the `English [Chinese]` catalog format; cleaning `[]` from the search
  query; suppressing the false mismatch warning for matched videos.
- **Out of scope:** yt-dlp / YouTube search throttling remediation (the
  "Incomplete data received" retries). That is a network/yt-dlp concern handled
  by the existing manual-URL fallback.
- **Out of scope:** Fully fuzzy matching (e.g. typo tolerance, transliteration).
  Matching remains exact-ish (substring containment of both English + Chinese
  parts) to avoid false positives selecting the wrong video.
- `_extract_chinese_title_from_youtube` is intentionally **kept** (not removed)
  to avoid breaking its other usages/exports; `_titles_match` is the new source
  of truth for matching decisions.

## Risks and Trade-offs

- **Substring containment** for convention 2 could in theory over-match if a
  video title's bracket happens to contain both the English phrase and the
  Chinese phrase of an unrelated song. This is unlikely given the specificity
  of bilingual title pairs and is preferable to the current false-negative
  behavior. If it becomes a problem, the matcher can be tightened to require
  the normalized bracket content to equal the normalized catalog title
  (brackets stripped) exactly.
- Keeping `_extract_chinese_title_from_youtube` means two title-extraction
  helpers coexist. This is acceptable: one returns the first-token Chinese
  title (for display/fallback), the other (`_titles_match`) drives decisions.
