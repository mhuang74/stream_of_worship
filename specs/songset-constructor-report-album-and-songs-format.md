# Songset Constructor: Add Album to Details Table + Change Brief Summary Songs Format

## Goal

Two changes to the Songset Constructor markdown report output:

1. **Add Album name** to the "Details" table so songs with similar titles can be distinguished.
2. **Change the Brief Summary "Songs:" line** to the Admin CLI `songset create` input format (space-separated quoted titles) instead of the numbered arrow format.

## Context

There are **three codebases** involved:

| Codebase | Path | Role |
|----------|------|------|
| **Admin CLI** | `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/` | Production code; the skill imports from here |
| **POC Scripts** | `lab/poc-scripts/poc/songset_constructor/` | Legacy/experimental near-identical copy; has the tests |
| **Skill** | `lab/skills/songset-constructor/scripts/write_report.py` | Has its own `_proposal_section` that imports `brief_summary_block` from Admin CLI |

The Admin CLI and POC copies are near-identical. Both `ProposalItem` models currently lack `album_name`; it lives only on `SongCandidate`.

### Key facts discovered

- `ProposalItem` (models.py:62) is a denormalized view model carrying `title`, `key`, `mode`, `bpm`, etc. — but **not** `album_name`.
- `album_name` exists on `SongCandidate` (models.py:14).
- `ProposalItem` is constructed in two places in `rules/proposals.py`: `item_from_candidate` and `proposal_from_draft`. Both already have access to the `SongCandidate` (which has `album_name`).
- The Brief Summary "Songs:" line is produced by `_song_sequence_line` in `artifacts/writer.py`. The skill's `write_report.py` does **not** have its own copy — it imports `brief_summary_block` from Admin CLI's `writer.py`, which calls `_song_sequence_line`. So changing Admin CLI's `_song_sequence_line` automatically updates the skill's output.
- There are **three** Details tables that render the same columns:
  - Admin CLI `writer.py` → `write_report` (~L376)
  - Admin CLI `writer.py` → `_proposal_section` (fallback review report, ~L551)
  - Skill `write_report.py` → `_proposal_section` (~L198)
  - (POC `writer.py` has the same two as Admin CLI: `write_report` ~L493, `_proposal_section` ~L876)
- Tests live in `lab/poc-scripts/tests/test_songset_constructor_artifacts.py` and import from `poc.songset_constructor` (the POC copy). The Admin CLI has **no** tests for these artifacts.

## Strategy

Add `album_name` as a field on `ProposalItem` (denormalized, same pattern as `title`, `key`, `bpm`). This avoids pool lookups in every renderer and makes `album_name` available in `proposals.json` too. Then update all Details tables and the single `_song_sequence_line` function per codebase.

## Changes

### Change 1: Add `album_name` to `ProposalItem` model (2 files)

| File | Location | Change |
|------|----------|--------|
| `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/models.py` | `ProposalItem` (~L71) | Add `album_name: str \| None = None` |
| `lab/poc-scripts/poc/songset_constructor/models.py` | `ProposalItem` (~L71) | Add `album_name: str \| None = None` |

### Change 2: Populate `album_name` in proposal construction (2 files)

| File | Function | Change |
|------|----------|--------|
| `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/proposals.py` | `item_from_candidate` (~L24) | Add `album_name=candidate.album_name,` |
| `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/proposals.py` | `proposal_from_draft` (~L62) | Add `album_name=candidate.album_name,` |
| `lab/poc-scripts/poc/songset_constructor/rules/proposals.py` | `item_from_candidate` (~L22) | Add `album_name=candidate.album_name,` |
| `lab/poc-scripts/poc/songset_constructor/rules/proposals.py` | `proposal_from_draft` (~L60) | Add `album_name=candidate.album_name,` |

### Change 3: Change `_song_sequence_line` format (2 files)

| File | Location | FROM | TO |
|------|----------|------|----|
| `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/artifacts/writer.py` | `_song_sequence_line` (~L36-40) | `f"{item.position}. {item.title}"` joined by `"  →  "` | `f'"{item.title}"'` joined by `" "` |
| `lab/poc-scripts/poc/songset_constructor/artifacts/writer.py` | `_song_sequence_line` (~L45-49) | Same | Same |

Example output change:

```
FROM: Songs: 1. 齊來讚美  →  2. 我敬拜祢  →  3. 聖潔的羔羊  →  4. 主啊，我要跟隨祢
TO:   Songs: "齊來讚美" "我敬拜祢" "聖潔的羔羊" "主啊，我要跟隨祢"
```

This single-function change per codebase automatically affects all callers of `brief_summary_block` (main report, fallback review report, and the skill's report which imports from Admin CLI).

### Change 4: Add Album column to Details tables (5 locations)

Insert an `Album` column between `Title` and `Phase` in each Details table.

| File | Function/Location | Header | Row Data |
|------|-------------------|--------|----------|
| `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/artifacts/writer.py` | `write_report` (~L376) | Insert `Album` after `Title` | Insert `{_md_cell(item.album_name or "")}` |
| `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/artifacts/writer.py` | `_proposal_section` (~L551) | Same | Same (already uses `_md_cell` pattern) |
| `lab/poc-scripts/poc/songset_constructor/artifacts/writer.py` | `write_report` (~L493) | Same | Same |
| `lab/poc-scripts/poc/songset_constructor/artifacts/writer.py` | `_proposal_section` (~L876) | Same | Same (already uses `_md_cell` pattern) |
| `lab/skills/songset-constructor/scripts/write_report.py` | `_proposal_section` (~L198) | Insert `Album` after `Title` | Insert `{item.album_name or ""}` (no `_md_cell` used here) |

New header / separator:

```
| # | Title | Album | Phase | BPM | Key | Themes | Transition |
|---|---|---|---:|---:|---|---|---|
```

### Change 5: Update test assertions (1 file)

| File | Line | Current | New |
|------|------|---------|-----|
| `lab/poc-scripts/tests/test_songset_constructor_artifacts.py` | 193 | `"1. 主你荣耀  →  2. 恩典已降临  →  3. 耶稣我爱祢"` | `'"主你荣耀" "恩典已降临" "耶稣我爱祢"'` |
| `lab/poc-scripts/tests/test_songset_constructor_artifacts.py` | 198 | `"1. 唯一"` | `'"唯一"'` |
| `lab/poc-scripts/tests/test_songset_constructor_artifacts.py` | 203 | `"(no songs)"` | `"(no songs)"` (unchanged) |

### Change 6: Update test helper `_item` (optional, 1 file)

| File | Location | Change |
|------|----------|--------|
| `lab/poc-scripts/tests/test_songset_constructor_artifacts.py` | `_item` (~L150-170) | Optionally add an `album_name` parameter for new tests asserting on the Album column |

This is optional since `album_name` defaults to `None` on `ProposalItem`. New tests asserting on Album column content would need it.

## What is NOT changing

- **SKILL.md** — The workflow doc doesn't specify exact table columns or song format, so no update needed.
- **`_song_sequence_line` in the skill** — The skill has no copy; it imports `brief_summary_block` from Admin CLI's `writer.py`, which calls `_song_sequence_line`. Changing Admin CLI's copy automatically updates the skill's output.
- **`brief_summary_block` in the skill** — The skill's `write_report.py` imports it from Admin CLI. No local override needed.
- **`README.md`** — No format-level detail documented.

## Verification

```bash
# POC tests (includes _song_sequence_line assertions)
cd lab/poc-scripts && uv run --extra test pytest tests/test_songset_constructor_artifacts.py -v

# Admin CLI tests (no direct artifact tests, but verify models still validate)
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest -v
```

## Files modified (summary)

| # | File | Changes |
|---|------|---------|
| 1 | `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/models.py` | Add `album_name` to `ProposalItem` |
| 2 | `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/proposals.py` | Populate `album_name` in 2 constructors |
| 3 | `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/artifacts/writer.py` | `_song_sequence_line` format + Album column in 2 Details tables |
| 4 | `lab/poc-scripts/poc/songset_constructor/models.py` | Add `album_name` to `ProposalItem` |
| 5 | `lab/poc-scripts/poc/songset_constructor/rules/proposals.py` | Populate `album_name` in 2 constructors |
| 6 | `lab/poc-scripts/poc/songset_constructor/artifacts/writer.py` | `_song_sequence_line` format + Album column in 2 Details tables |
| 7 | `lab/skills/songset-constructor/scripts/write_report.py` | Album column in 1 Details table |
| 8 | `lab/poc-scripts/tests/test_songset_constructor_artifacts.py` | Update `_song_sequence_line` assertions |

**Total: 8 files**
