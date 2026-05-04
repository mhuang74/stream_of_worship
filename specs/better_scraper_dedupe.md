# Scraper Dedup Enhancement: Prefer 敬拜讚美 Album Series

## Context

The `CatalogScraper` in `src/stream_of_worship/admin/services/scraper.py` scrapes the sop.org/songs table and de-duplicates rows that share the same stable song ID (derived from `title + composer + lyricist`). Currently, dedup is rigidly **first-seen-wins**: the first row encountered for a given song ID is kept and all later duplicates are skipped.

This is suboptimal because sop.org often lists the same song across multiple album series, and we strongly prefer the version that belongs to the **敬拜讚美** (Jingbai Zanmei) album series — these are the canonical worship albums and our primary source for recordings and analysis.

## Problem

### Current dedup logic (lines 124--148)

```
seen_ids = set()
for row_num, row in enumerate(data_rows, 1):
    song = self._parse_row(cells, col_indices, row_num)
    if song.id in seen_ids:
        skip  # first duplicate wins
    seen_ids.add(song.id)
```

Because `_parse_row()` runs inside the loop, the first row for a given song ID is immediately saved; later duplicates are discarded with no consideration for album series quality.

### Why it matters

A song like **將天敞開** may appear twice:
- Row 42: album_series=`其他專輯`, album_name=`讚美之泉精選`
- Row 87: album_series=`敬拜讚美15`, album_name=`讓讚美飛揚`

With first-seen-wins, we keep the `其他專輯` version. We want the `敬拜讚美15` version instead.

## Proposed Solution

Change dedup from **single-pass first-seen-wins** to a **two-pass best-candidate** approach:

1. **First pass:** Parse ALL rows into a lightweight candidate structure (without full `Song` construction). Group candidates by computed song ID.
2. **Second pass:** For each group, select the best candidate using the preference rules below.
3. **Emit:** Convert only the selected candidates to full `Song` objects and return them.

This keeps row-order determinism while allowing "retroactive" replacement when a better album series appears later in the table.

### Preference rules (per song ID group)

1. **Primary criterion:** Prefer any candidate whose `album_series` starts with `敬拜讚美`.
   - If exactly one candidate matches → keep it.
   - If multiple candidates match → **last matching candidate wins** (per user's chosen tie-breaker).
2. **Fallback criterion:** If no candidate matches `敬拜讚美`, keep the **first** candidate (preserves backward compatibility for non-duplicate rows).

### Algorithm sketch

```python
from dataclasses import dataclass
from typing import List, Dict

@dataclass
class _SongCandidate:
    """Lightweight pre-Song data used during two-pass dedup."""
    song_id: str
    title: str
    composer: str
    lyricist: str
    album_name: str
    album_series: str
    musical_key: str
    lyrics_raw: str
    lyrics_lines: List[str]
    table_row_number: int

# --- First pass: collect candidates ---
groups: Dict[str, List[_SongCandidate]] = {}
for row_num, row in enumerate(data_rows, 1):
    candidate = self._parse_row_to_candidate(cells, col_indices, row_num)
    if not candidate:
        continue
    groups.setdefault(candidate.song_id, []).append(candidate)

# --- Count duplicates for logging ---
duplicate_count = sum(len(cands) - 1 for cands in groups.values() if len(cands) > 1)

# --- Second pass: select best per group ---
PREFERRED_SERIES_PREFIX = "敬拜讚美"

selected: List[_SongCandidate] = []
for song_id, candidates in groups.items():
    if len(candidates) == 1:
        selected.append(candidates[0])
        continue

    jingbai_candidates = [
        c for c in candidates
        if c.album_series and c.album_series.startswith(PREFERRED_SERIES_PREFIX)
    ]
    if jingbai_candidates:
        selected.append(jingbai_candidates[-1])
    else:
        selected.append(candidates[0])  # fallback: first seen

# --- Convert to Song objects ---
songs = [self._candidate_to_song(c) for c in selected]
```

## Files to Modify

### 1. `src/stream_of_worship/admin/services/scraper.py`

#### Changes

1. **New dataclass `_SongCandidate`** (private, module-level or within `CatalogScraper`):
   - Holds all fields needed to construct a `Song`, but is lightweight and cheap to create.
   - Fields: `song_id`, `title`, `composer`, `lyricist`, `album_name`, `album_series`, `musical_key`, `lyrics_raw`, `lyrics_lines`, `table_row_number`.

2. **Refactor `_parse_row()` → `_parse_row_to_candidate()`**:
   - Return `_SongCandidate` instead of `Song`.
   - Remove `Song()` construction, `title_pinyin` computation, `_detect_sections()`, and `scraped_at` from the hot path (move to `_candidate_to_song()`).
   - Keep the same null-checking and cell extraction logic.

3. **New method `_candidate_to_song(candidate: _SongCandidate) -> Song`**:
   - Converts the selected `_SongCandidate` to a full `Song` object.
   - Computes `title_pinyin`, `_detect_sections()`, `scraped_at`, etc. here.

4. **Add `_select_best_candidate(candidates: List[_SongCandidate]) -> _SongCandidate`**:
   - Encapsulates the preference logic for testability.
   - Single-candidate case: return it directly.
   - Multi-candidate: apply preference rules above.
   - Log which candidate was selected and why (debug level).

5. **Rewrite `scrape_all_songs()` dedup loop** (lines 123--148):
   - Replace `seen_ids` / `duplicate_count` single-pass logic with the two-pass algorithm above.
   - Preserve existing logging for duplicate count and per-row progress (`Processed N/M songs...`).
   - Preserve incremental mode (`existing_ids` skip): after candidate selection, if a selected candidate's ID is in `existing_ids`, skip adding it to the return list, but still track it in `seen_ids` for soft-delete purposes.
   - Keep `soft_delete_missing` logic unchanged: `missing_ids = existing_ids - set(selected_song_ids)`.

6. **Add `PREFERRED_SERIES_PREFIX` constant**:
   - Module-level or class-level constant `"敬拜讚美"` for the preference prefix.
   - Makes it easy to change or parameterize in the future.

### 2. `tests/admin/services/test_scraper_id_stability.py`

#### Add new test cases

```python
class TestDedupPreferJingbaiZanmei:
    """Test that dedup prefers album_series starting with 敬拜讚美."""

    def test_prefers_jingbai_over_other_series(self):
        """When duplicates exist, prefer 敬拜讚美 series."""
        scraper = CatalogScraper()
        c1 = _SongCandidate(song_id="s1", album_series="其他專輯", ...)
        c2 = _SongCandidate(song_id="s1", album_series="敬拜讚美15", ...)
        selected = scraper._select_best_candidate([c1, c2])
        assert selected.album_series == "敬拜讚美15"

    def test_last_jingbai_wins_when_multiple(self):
        """When multiple 敬拜讚美 candidates exist, last one wins."""
        c1 = _SongCandidate(album_series="敬拜讚美10")
        c2 = _SongCandidate(album_series="敬拜讚美15")
        c3 = _SongCandidate(album_series="敬拜讚美20")
        selected = CatalogScraper()._select_best_candidate([c1, c2, c3])
        assert selected.album_series == "敬拜讚美20"

    def test_fallback_to_first_when_no_jingbai(self):
        """When no 敬拜讚美 candidate exists, keep first seen."""
        c1 = _SongCandidate(album_series="其他專輯1", table_row_number=10)
        c2 = _SongCandidate(album_series="其他專輯2", table_row_number=20)
        selected = CatalogScraper()._select_best_candidate([c1, c2])
        assert selected.table_row_number == 10

    def test_empty_series_is_not_jingbai(self):
        """Empty or None album_series does not qualify as 敬拜讚美."""
        c1 = _SongCandidate(album_series="")
        c2 = _SongCandidate(album_series="敬拜讚美15")
        selected = CatalogScraper()._select_best_candidate([c1, c2])
        assert selected.album_series == "敬拜讚美15"

    def test_single_candidate_unchanged(self):
        """Non-duplicate rows are unaffected."""
        c1 = _SongCandidate(album_series="其他專輯")
        selected = CatalogScraper()._select_best_candidate([c1])
        assert selected.album_series == "其他專輯"

    def test_jingbai_without_number_matches(self):
        """album_series = '敬拜讚美' (no trailing number) still matches."""
        c1 = _SongCandidate(album_series="其他專輯")
        c2 = _SongCandidate(album_series="敬拜讚美")
        selected = CatalogScraper()._select_best_candidate([c1, c2])
        assert selected.album_series == "敬拜讚美"
```

### 3. `tests/admin/services/test_scraper_dedup_integration.py` (new file)

Integration test using a small HTML fixture with duplicate rows:

```python
import pytest
from stream_of_worship.admin.services.scraper import CatalogScraper

@pytest.fixture
def html_with_duplicates():
    """Minimal HTML table where a song appears twice with different series."""
    return """
    <table id="tablepress-3">
        <tr>
            <th>曲名</th><th>作曲</th><th>作詞</th>
            <th>專輯系列</th><th>專輯名稱</th><th>調性</th><th>歌詞</th>
        </tr>
        <tr>
            <td>將天敞開</td><td>游智婷</td><td>鄭懋柔</td>
            <td>其他專輯</td><td>讚美之泉精選</td><td>G</td><td>歌詞A</td>
        </tr>
        <tr>
            <td>將天敞開</td><td>游智婷</td><td>鄭懋柔</td>
            <td>敬拜讚美15</td><td>讓讚美飛揚</td><td>G</td><td>歌詞B</td>
        </tr>
    </table>
    """

def test_scrape_prefers_jingbai_in_full_mode(monkeypatch, html_with_duplicates):
    """Full scrape should return the 敬拜讚美 version."""
    import requests
    class FakeResponse:
        text = html_with_duplicates
        def raise_for_status(self): pass
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: FakeResponse())

    scraper = CatalogScraper()
    songs = scraper.scrape_all_songs(force=True, incremental=False)
    jiang_tian = [s for s in songs if "將天敞開" in s.title]
    assert len(jiang_tian) == 1
    assert jiang_tian[0].album_series == "敬拜讚美15"
```

## Edge Cases

| Scenario | Expected Behavior |
|----------|-------------------|
| First row = 敬拜讚美, later = other | Keep first row (already 敬拜讚美) |
| First row = other, later = 敬拜讚美 | Replace with later row |
| Multiple 敬拜讚美 rows (敬拜讚美10, 敬拜讚美20) | Keep **last** 敬拜讚美 row |
| No 敬拜讚美 in any duplicate | Keep first row (backward compat) |
| album_series is `None` or empty string | Does NOT match 敬拜讚美; fallback applies |
| album_series = `敬拜讚美` (no number) | Matches (startswith); treated as valid |
| album_series = `敬拜讚美15精選` | Matches (startswith); treated as valid |
| album_series = `全心敬拜讚美` | Does NOT match (not prefix-equal to `敬拜讚美`) |

## Open Question: Should incremental mode also upgrade existing songs?

In `incremental=True` mode, the current code skips any song whose ID already exists in the DB. With the two-pass dedup, a potential issue arises:

**Scenario:**
1. First scrape: `將天敞開` appears only as `其他專輯` → saved to DB.
2. Second scrape: sop.org has been updated; `將天敞開` now also appears as `敬拜讚美15`.
3. Incremental mode sees existing ID → skips it → DB still has `其他專輯` version.

**Options:**
- **Option A (Recommended):** Leave incremental mode as-is (skip existing IDs). The admin can run `sow-admin catalog scrape --force` to upgrade. Simplest, safest, no surprise updates.
- **Option B:** In incremental mode, parse all rows and compare album_series. If the selected candidate is "better" (敬拜讚美 vs existing non-敬拜讚美), UPDATE the DB row. More ergonomic but adds complexity.

**Recommendation:** Option A. Document that `--force` is needed to upgrade existing rows when better album series appear. The two-pass dedup still benefits full scrapes and new songs.

## Backward Compatibility

- **No DB schema changes** — `_compute_song_id()` is unchanged; `table_row_number` stays as a debug field.
- **First-seen-wins preserved for non-duplicate rows** — songs that appear exactly once behave identically.
- **Incremental mode unchanged** — existing fast-path semantics are preserved.
- **Duplicate count logging** — `last_run_duplicate_count` still reports total duplicates (sum of group sizes minus 1).

## Verification Checklist

1. Unit test: `_select_best_candidate` prefers 敬拜讚美 over other series.
2. Unit test: last 敬拜讚美 wins among multiple 敬拜讚美 candidates.
3. Unit test: fallback to first when no 敬拜讚美 exists.
4. Unit test: empty/None album_series does not qualify.
5. Integration test: scrape fixture with duplicates → only one `Song` returned, with correct `album_series`.
6. Integration test: non-duplicate fixture → behaves identically to old code.
7. Run existing `test_scraper_id_stability.py` → all pass.
8. Run full scraper against real sop.org → verify `last_run_duplicate_count` and that 敬拜讚美 rows are preferred.

## Rejected Alternatives

1. **Sort rows before processing** (e.g., sort by album_series to put 敬拜讚美 first):
   - Rejected: Loses source table ordering; `table_row_number` semantics become confusing.

2. **Add a post-scrape DB UPDATE step** (scrape everything, then run a SQL query to prefer 敬拜讚美):
   - Rejected: More complex; couples scraper to DB schema; harder to test.

3. **Change `_compute_song_id` to include album_series** (so duplicates have different IDs):
   - Rejected: Would violate the stable-ID contract in V2 spec. A song is the same song regardless of which album it appears on.

4. **Prefer highest series number** (e.g., 敬拜讚美20 > 敬拜讚美15):
   - Rejected per user interview: user explicitly chose "last matching row wins" as the tie-breaker, not numerical comparison.
