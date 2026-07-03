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
4. `_extract_chinese_title_from_youtube()` captures `【([^】\s]+)` — the first
   whitespace-free token after `【`.
5. Interactive path: on manual URL, `_extract_chinese_title_from_youtube()` is
   called again for the mismatch warning; mismatch is a warning + confirmation.
6. Batch path (`_download_and_create_recording`): a failed match raises
   `RuntimeError("No matching title found ...")` and aborts the song.

## Product Decisions

After review, the following decisions were made (via user interview):

1. **Matcher tolerance**: Use **substring containment with a length heuristic**.
   Over-matching is a concern (e.g. a medley or compilation that happens to
   contain the English and Chinese phrases). To mitigate, require that the
   normalized bracket content length is not more than **150%** of the
   normalized catalog title length (with brackets stripped). This catches
   obvious mismatches (e.g. `Here I Am [我在這裡]` against a long medley title
   `【Here I Am to Worship 我在這裡 And Dwell Here Forever】`) without being so
   strict that `Holy, Holy [聖潔榮耀主]` fails on `【Holy, Holy 聖潔榮耀主】`.

2. **Mismatch warning display**: Show the **full bracket content** (e.g.
   `got 'Holy, Holy 聖潔榮耀主'`) instead of the old first token. This directly
   fixes the user's reported confusion of seeing `got 'Holy,'`.

3. **Convention-1 English-only titles**: Extend convention-1 (no `[]` in
   catalog) to also use **substring containment** for English-only titles, not
   just exact first-token match. This means `Amazing Grace` will match
   `【Amazing Grace Official MV】`. The length heuristic still applies, so
   `Amazing Grace` won't match a very long unrelated bracket.

## Proposed Behavior

### 1. Strip `[]` from the search query (`build_search_query`)

In `build_search_query` (youtube.py:299), normalize the `title` argument before
joining it into the query: replace any `[...]` segment with its bare contents
(space-separated) and collapse internal whitespace.

- `Holy, Holy [聖潔榮耀主]` → `Holy, Holy 聖潔榮耀主`
- `一生敬拜祢` (no brackets) → `一生敬拜祢` (unchanged)
- `A [B] C [D]` → `A B C D` (multi-bracket)

This benefits `audio download`, `audio batch` (audio.py:4726), and the other
call site (audio.py:4851) automatically, since they all go through this method.

Implementation details:
- Keep normalization localized to `build_search_query` so callers are unaffected.
- Unmatched `[` with no `]` → left unchanged (regex `\[([^\]]+)\]` won't match).
- Empty `[]` → reduces to nothing (strips empty brackets).
- Multiple `[...]` segments → all replaced (defensive).

### 2. Add a bracket-aware matcher (`_titles_match`)

Add three helpers in `youtube.py`:

#### `_extract_bracket_content(video_title) -> Optional[str]`

Captures the **full** content of the first `【…】` bracket using
`re.match(r"【([^】]+)", video_title)`, returning e.g.
`Holy, Holy 聖潔榮耀主`. Returns `None` when there is no `【` bracket.

`_extract_chinese_title_from_youtube` is intentionally **kept as-is** to avoid
breaking its other usages and exports. `_titles_match` is the new source of
truth for matching decisions.

#### `_normalize_for_match(s: str) -> str`

Small helper: lowercase, collapse all whitespace sequences to a single space,
strip leading/trailing space.

#### `_titles_match(song_title, video_title) -> bool`

Unified matching logic supporting **both** conventions and English-only titles.

1. Parse the catalog title:
   - `chinese_segments = re.findall(r"\[([^\]]+)\]", song_title)`
   - `english_part = re.sub(r"\s*\[[^\]]+\]\s*", " ", song_title).strip()`
   - `catalog_without_brackets = re.sub(r"\s*\[[^\]]+\]\s*", " ", song_title).strip()`
2. Extract `bracket = _extract_bracket_content(video_title)`.
   - If no bracket, fall back to normalized raw-title compare:
     `_normalize_for_match(song_title) == _normalize_for_match(video_title)`.
3. Compute normalized values:
   - `n_bracket = _normalize_for_match(bracket)`
   - `n_catalog = _normalize_for_match(catalog_without_brackets)`
4. **Length heuristic**: if `len(n_bracket) > len(n_catalog) * 1.5`, return
   `False`. This prevents over-matching against medleys/compilations.
5. **Convention 2** (catalog has `[Chinese]`):
   - `n_english = _normalize_for_match(english_part)`
   - Every Chinese segment normalized must be a substring of `n_bracket`.
   - `n_english` must also be a substring of `n_bracket`.
   - For the user's case: `holy, holy` ⊆ `holy, holy 聖潔榮耀主` ✓ and
     `聖潔榮耀主` ⊆ `holy, holy 聖潔榮耀主` ✓ → match.
6. **Convention 1 / English-only** (catalog has no `[]`):
   - Match iff `n_catalog` is a substring of `n_bracket`.
   - `一生敬拜祢` ⊆ `一生敬拜祢 all the days of my life` ✓ → match.
   - `amazing grace` ⊆ `amazing grace official mv` ✓ → match.

### 3. Use the matcher in candidate selection (`_select_best_candidate`)

Update `_select_best_candidate` (youtube.py:90) to call `_titles_match` instead
of the `chinese_title == song_title` exact compare. The old
`_extract_chinese_title_from_youtube`-based compare is replaced inside this
function; the helper itself remains for other code paths.

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

- Import `_titles_match` and `_extract_bracket_content` into `audio.py`.
- For the warning message, use `_extract_bracket_content(video_title)` (or
  fall back to `video_title`) as the `got '…'` value, so users see the full
  bracket content (e.g. `Holy, Holy 聖潔榮耀主`) instead of just `Holy,`.
- This eliminates the false `⚠ Title mismatch` for the user's case, since
  `_titles_match` returns `True` when both the English and Chinese parts appear
  in the bracket content.

### 5. Tests (`test_youtube.py`)

**Add `TestTitlesMatch`** covering:

- Convention-2 match using the user's exact strings (catalog
  `Holy, Holy [聖潔榮耀主]`, video
  `【Holy, Holy 聖潔榮耀主】官方歌詞版MV ...`).
- Convention-2 non-match (e.g. wrong Chinese segment).
- Convention-2 over-match blocked by length heuristic (e.g. catalog
  `Here I Am [我在這裡]` vs video `【Here I Am to Worship 我在這裡 And Dwell Here】`).
- Convention-1 regression: `一生敬拜祢` vs
  `【一生敬拜祢 All the Days of My Life】官方歌詞版MV` → match.
- Convention-1 non-match: `一生敬拜祢` vs `【另一首 All the Days】MV` → no match.
- English-only convention-1: `Amazing Grace` vs `【Amazing Grace Official MV】` → match.
- English-only convention-1 blocked by length: `Amazing Grace` vs a very long
  unrelated bracket → no match.
- Bracket-less video title (no `【`): falls back to normalized raw compare.
- Catalog title with multiple `[...]` segments (defensive).

**Extend `TestBuildSearchQuery`** with:

- `build_search_query(title="Holy, Holy [聖潔榮耀主]", ...)` → contains
  `Holy, Holy 聖潔榮耀主` and no `[` or `]`.
- `build_search_query(title="A [B] C [D]", ...)` → contains `A B C D`.
- `build_search_query(title="A [B", ...)` → unchanged `A [B`.

**Add integration test** in `TestSelectBestCandidate`:

- Entries include `【Holy, Holy 聖潔榮耀主】Official MV`; song title
  `Holy, Holy [聖潔榮耀主]` → matched (verifies `_titles_match` integration
  into candidate selection).

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
  query; suppressing the false mismatch warning for matched videos; extending
  convention-1 to English-only substring matching with length guard.
- **Out of scope:** yt-dlp / YouTube search throttling remediation (the
  "Incomplete data received" retries). That is a network/yt-dlp concern handled
  by the existing manual-URL fallback.
- **Out of scope:** Fully fuzzy matching (e.g. typo tolerance, transliteration).
  Matching remains exact-ish (substring containment + length heuristic) to
  avoid false positives selecting the wrong video.
- `_extract_chinese_title_from_youtube` is intentionally **kept** (not removed)
  to avoid breaking its other usages/exports; `_titles_match` is the new source
  of truth for matching decisions.

## Risks and Trade-offs

- **Substring containment + length heuristic** is a middle ground. It is more
  permissive than exact equality (fixes the reported bug) but less permissive
  than pure substring matching (the 150% length cap catches most medley
  over-matches). If false positives still occur, the cap can be tightened (e.g.
  to 125%) or exact normalized equality can be required for convention-2.
- Keeping `_extract_chinese_title_from_youtube` means two title-extraction
  helpers coexist. This is acceptable: one returns the first-token Chinese
  title (for legacy display/fallback), the other (`_titles_match`) drives
  decisions.
