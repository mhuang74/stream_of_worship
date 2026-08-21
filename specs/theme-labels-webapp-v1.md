# Issue #152: Include Theme Labels in Webapp

## Context

Issue #152 asks to extend LLM-based theme/posture classification from the Song Component level to the Recording level, and expose theme as color-coded text labels in the webapp's SongRow and SongSet views. The Analysis Service's `ThemeClassifier` already classifies per-component theme (12 Chinese values) and vocal posture (3 values). The Admin CLI persists these to `song_components` and is the sole Postgres writer. The webapp is a pure reader of the shared Neon Postgres. This plan adds recording-level theme/posture columns, an aggregation step in the Admin CLI, and theme label UI across all song/songset display surfaces in the webapp.

## Tickets

| # | Title | Blocked by | GitHub Issue |
|---|-------|-----------|--------------|
| 1 | Recording-level theme/posture schema + Admin CLI aggregation + backfill | None | #153 |
| 2 | Domain modeling: VocalPosture term + ADR 0005 | None | #154 |
| 3 | SongCard theme badge — constants, i18n, ThemeLabel component, 4-row layout | #153 | #155 |
| 4 | SongsetRow + DashboardSongsetCard arc span theme badge | #155 | #156 |
| 5 | Songset editor — per-song theme badges + all-themes summary | #155 | #157 |
| 6 | Share view theme badges | #155 | #158 |

Frontier (unblocked): #153 and #154 can start immediately.

## Approach

### Phase 1: Schema — add recording-level theme + posture columns

**Step 1.1: Admin CLI schema constants** (`ops/admin-cli/src/stream_of_worship/admin/db/schema.py`)
- Add `ALTER_RECORDINGS_THEME_COLUMNS` idempotent ALTER statement after `ALTER_RECORDINGS_STRUCTURED_LYRICS_COLUMNS` (line ~314):
  ```sql
  ALTER TABLE recordings ADD COLUMN IF NOT EXISTS theme TEXT
      CHECK (theme IN ('讚美','感恩','敬拜','奉獻','認罪','差遣','信心','祈禱','復興','聖靈','十字架','跟隨') OR theme IS NULL);
  ALTER TABLE recordings ADD COLUMN IF NOT EXISTS vocal_posture TEXT
      CHECK (vocal_posture IN ('To God','About God','To Congregation') OR vocal_posture IS NULL);
  ```
- Add `theme TEXT, vocal_posture TEXT,` columns to `CREATE_RECORDINGS_TABLE` (after `deleted_at`, line ~92) for fresh installs.
- Append `theme` and `vocal_posture` to `RECORDING_COLUMNS_SELECT` (line ~367). Update `RECORDING_COLUMN_COUNT` from 36 to 38.
- Register the new ALTER in `ALL_SCHEMA_STATEMENTS` in `ops/admin-cli/src/stream_of_worship/db/postgres_schema.py` (import and append `ALTER_RECORDINGS_THEME_COLUMNS`).

**Step 1.2: Admin CLI Recording model** (`ops/admin-cli/src/stream_of_worship/admin/db/models.py`)
- Add `theme: Optional[str] = None` and `vocal_posture: Optional[str] = None` fields to the `Recording` dataclass (after `deleted_at`, line ~226).
- Update `Recording.from_row` (line ~248): in the `row_len >= 36` branch, read `theme = row[36]`, `vocal_posture = row[37]`. The 38-column path is the new canonical shape.

**Step 1.3: Webapp Drizzle schema + migration**
- Add to `delivery/webapp/src/db/schema.ts` `recordings` pgTable (after `deletedAt`, line ~112):
  ```ts
  theme: text("theme"),
  vocalPosture: text("vocal_posture"),
  ```
- Create `delivery/webapp/drizzle/0022_add_recording_theme_posture.sql`:
  ```sql
  ALTER TABLE "recordings" ADD COLUMN "theme" text;
  ALTER TABLE "recordings" ADD COLUMN "vocal_posture" text;
  --> statement-breakpoint
  ALTER TABLE "recordings" ADD CONSTRAINT "recordings_theme_check"
      CHECK (theme IN ('讚美','感恩','敬拜','奉獻','認罪','差遣','信心','祈禱','復興','聖靈','十字架','跟隨') OR theme IS NULL);
  ALTER TABLE "recordings" ADD CONSTRAINT "recordings_vocal_posture_check"
      CHECK (vocal_posture IN ('To God','About God','To Congregation') OR vocal_posture IS NULL);
  ```
  The webapp migration creates the column (per user decision Q9). Admin CLI's DDL is updated for fresh installs only.

### Phase 2: Admin CLI — aggregation + persistence + backfill

**Step 2.1: Aggregation function** (`ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`)
- Add `_aggregate_recording_theme(components: list[SongComponent]) -> tuple[Optional[str], Optional[str]]`:
  - For theme: count occurrences per theme (excluding None). If all None → return `(None, None)`. Find the mode (highest count). If tie: (a) check if any component with `component_type='chorus'` and `occurrence_index=1` has one of the tied themes — that theme wins; (b) if no chorus or chorus theme not in tied set, pick the tied theme with highest average `theme_confidence`; (c) final fallback: first occurrence in component list order.
  - For posture: same mode logic, same tiebreak (chorus → avg confidence → first occurrence).
  - Return `(theme, posture)`.
- No equivalent function exists in the codebase. This is new logic.

**Step 2.2: DB client update method** (`ops/admin-cli/src/stream_of_worship/admin/db/client.py`)
- Add `update_recording_theme(self, hash_prefix: str, theme: Optional[str], vocal_posture: Optional[str]) -> bool`:
  ```sql
  UPDATE recordings SET theme = %s, vocal_posture = %s, updated_at = NOW()
  WHERE hash_prefix = %s AND deleted_at IS NULL
  ```
  Follows the existing `update_recording_*` method pattern (e.g. `update_recording_status` at line 903). Coerce theme/posture to None if not in the canonical enum frozensets (reuse `_SONG_COMPONENT_THEME_SET` / `_SONG_COMPONENT_POSTURE_SET` already defined at lines 38-63).

**Step 2.3: Write aggregation after component persistence** (`ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`)
- In `_submit_component_analysis_job` (line ~2580), after `db_client.upsert_song_components(song_id, recording.content_hash, components)` at line 2841:
  ```python
  theme, posture = _aggregate_recording_theme(components)
  if theme is not None or posture is not None:
      db_client.update_recording_theme(recording.hash_prefix, theme, posture)
  ```
- Also add after the cached-components upsert path at line 2742-2745 (when cache is valid and components are upserted from R2 cache).

**Step 2.4: Backfill flag on `components` command** (`ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`)
- Add `--backfill-song-classification` flag to `components_recording` (line ~2901):
  ```python
  backfill_classification: bool = typer.Option(
      False, "--backfill-song-classification",
      help="Skip analysis; aggregate existing song_components theme/posture into recordings table"
  )
  ```
- When this flag is set: skip job submission and R2 cache check. Instead, query `song_components` for the recording (via a new `db_client.get_song_components(song_id, content_hash)` method or existing query), call `_aggregate_recording_theme`, call `db_client.update_recording_theme`. Works with `--stdin` for batch backfill.
- Print a summary line: `[green]Backfilled theme=讚美, posture=To God for {song_id}[/green]` or `[yellow]No theme data to backfill for {song_id}[/yellow]`.

**Step 2.5: Admin CLI display** (`_render_components_table`, line 2523)
- No change needed — the table already shows per-component theme/posture. Optionally add a summary line below the table: `Recording Theme: 讚美 | Posture: To God` when the recording-level values are set.

### Phase 3: Webapp data layer — thread theme through query shapes

**Step 3.1: RecordingInfo interface** (`delivery/webapp/src/lib/db/songs.ts`, line 38)
- Add `theme: string | null;` to `RecordingInfo`.
- In `mapRecordingInfo` (line 82): add `theme: recording.theme,` to the return object.

**Step 3.2: SongCardData interface** (`delivery/webapp/src/components/songset/SongCard.tsx`, line 12)
- Add `theme: string | null;` to the `recordings[0]` type inside `SongCardData`.

**Step 3.3: toSongCardData mapper** (`delivery/webapp/src/lib/song-card-data.ts`, line 17)
- Add `theme: r.theme,` to the `recordings.map` return object.

**Step 3.4: SongsetListItem + listSongsetSummaries** (`delivery/webapp/src/lib/db/songsets.ts`)
- Add `themes: string[]` to `SongsetListItem` (line 209) — ordered list of non-null themes per song in position order, for the arc span badge.
- In `listSongsetSummaries` query (line 463): add `recordingTheme: recordings.theme` to the select. In the mapping (line 502), collect themes from the joined recordings. Since the query groups by songset, and recordings are joined via `songsetItems`, use `array_agg(recordings.theme) filter (where recordings.deletedAt is null and recordings.theme is not null)` ordered by `songsetItems.position` — add as a SQL expression:
  ```ts
  themes: sql<string[]>`array_agg(${recordings.theme}) filter (where ${recordings.deletedAt} is null and ${recordings.theme} is not null order by ${songsetItems.position})`,
  ```
  Map to `themes: row.themes ?? []` in the return.

**Step 3.5: SongsetItemDetail + SongsetItemRecording** (`delivery/webapp/src/lib/db/songsets.ts`)
- Add `theme: string | null;` to `SongsetItemRecording` (line 225).
- In `getSongsetEditorData` query (line 560): add `recordingTheme: recordings.theme` to the select. In the recording mapping (line 658): add `theme: item.recordingTheme,`.

**Step 3.6: SongListItem interface** (`delivery/webapp/src/components/songset/SongList.tsx`, line 55)
- Add `theme: string | null;` to the `recording` shape inside `SongListItem`.
- Update `transformItems` in `SongsetEditorClient.tsx` (line ~112) to pass `theme` through from the API response.

**Step 3.7: DashboardSongset interface** (`delivery/webapp/src/components/dashboard/DashboardSongsetCard.tsx`, line 11)
- Add `themes: string[];` to `DashboardSongset`. The `getRecentSongsets` → `listSongsetSummaries` flow already returns `SongsetListItem` which now has `themes`; the spread in `page.tsx` line 34 (`...songset`) passes it through.

**Step 3.8: PublicSongsetItem + getSongsetPublicView** (`delivery/webapp/src/lib/db/songsets.ts`, line 20)
- Add `theme: string | null;` to `PublicSongsetItem`.
- In `getSongsetPublicView` query (line 60): add `recordingTheme: recordings.theme` to the select. In the mapping (line 84): add `theme: item.recordingTheme,`.
- Update the `PublicSongsetItem` interface in `delivery/webapp/src/app/share/[token]/page.tsx` (line 10) to include `theme: string | null;`.

### Phase 4: Webapp constants + i18n

**Step 4.1: Theme constants** (`delivery/webapp/src/lib/constants.ts`)
- Add:
  ```ts
  // 12-theme vocabulary (mirrors admin CLI SONG_COMPONENT_THEMES)
  export const SONG_THEMES = [
    "讚美", "感恩", "敬拜", "奉獻", "認罪", "差遣",
    "信心", "祈禱", "復興", "聖靈", "十字架", "跟隨",
  ] as const;
  export type SongTheme = (typeof SONG_THEMES)[number];

  // Theme → Worship Arc phase (mirrors admin CLI THEME_TO_PHASE in phases.py)
  export const THEME_TO_PHASE: Record<SongTheme, 1 | 2 | 3 | 4 | 5> = {
    "讚美": 1, "感恩": 2, "敬拜": 3, "祈禱": 3, "信心": 3, "聖靈": 3,
    "奉獻": 4, "認罪": 4, "十字架": 4,
    "差遣": 5, "跟隨": 5, "復興": 5,
  };

  // 5-color palette: one {bg, text} pair per Worship Arc phase
  export const THEME_PHASE_COLORS: Record<number, { bg: string; text: string }> = {
    1: { bg: "#fef3c7", text: "#92400e" }, // Call — amber
    2: { bg: "#dcfce7", text: "#166534" }, // Thanksgiving — green
    3: { bg: "#dbeafe", text: "#1e40af" }, // Worship — blue
    4: { bg: "#fce7f3", text: "#9d174d" }, // Response — pink
    5: { bg: "#e0e7ff", text: "#3730a3" }, // Commission — indigo
  };
  ```
  These are static mappings replicated from the admin CLI (per Q20 — no cross-stack import). Color pairs chosen for WCAG AA contrast on light backgrounds.

**Step 4.2: ThemeLabel component** (`delivery/webapp/src/components/songset/ThemeLabel.tsx` — new file)
- A small reusable badge that takes `theme: string` and renders:
  ```tsx
  <Badge variant="outline" className="text-xs" style={{ backgroundColor: phaseColor.bg, color: phaseColor.text, borderColor: "transparent" }}>
    {t(themeLabelKey)}
  </Badge>
  ```
  Where `phaseColor = THEME_PHASE_COLORS[THEME_TO_PHASE[theme]]` and `themeLabelKey = `theme.${theme}` as TranslationKey`.
- Export `ThemeLabel` and a `ThemeArcSpan` component (for the SongsetRow compact first→last badge):
  ```tsx
  <Badge variant="outline" className="text-xs" style={...}>
    {t(`theme.${themes[0]}`)} → {t(`theme.${themes[themes.length-1]}`)}
  </Badge>
  ```
  If `themes.length === 0`, render nothing. If `themes.length === 1`, render a single `ThemeLabel`.

**Step 4.3: i18n bundle** (`delivery/webapp/src/lib/i18n/messages/themes.ts` — new file)
- 12 theme keys, both locales:
  ```ts
  export const themesBundle = bundle({
    en: {
      "theme.讚美": "Praise",
      "theme.感恩": "Thanksgiving",
      "theme.敬拜": "Worship",
      "theme.奉獻": "Offering",
      "theme.認罪": "Confession",
      "theme.差遣": "Sending",
      "theme.信心": "Faith",
      "theme.祈禱": "Prayer",
      "theme.復興": "Revival",
      "theme.聖靈": "Holy Spirit",
      "theme.十字架": "Cross",
      "theme.跟隨": "Following",
    },
    "zh-Hant": {
      "theme.讚美": "讚美",
      "theme.感恩": "感恩",
      "theme.敬拜": "敬拜",
      "theme.奉獻": "奉獻",
      "theme.認罪": "認罪",
      "theme.差遣": "差遣",
      "theme.信心": "信心",
      "theme.祈禱": "祈禱",
      "theme.復興": "復興",
      "theme.聖靈": "聖靈",
      "theme.十字架": "十字架",
      "theme.跟隨": "跟隨",
    },
  });
  ```
- Import and add to `mergeMessages` in `delivery/webapp/src/lib/i18n/messages.ts` (line 72).

### Phase 5: Webapp UI — per-song theme badges

**Step 5.1: SongCard restructure** (`delivery/webapp/src/components/songset/SongCard.tsx`)
- Restructure the card body into 4 rows (per Q18):
  - Row 1 (unchanged): title + verified badge + favorited-by pill (lines 135-152).
  - Row 2: artist (Music icon) + album — move album from the current metadata row up here, same `text-xs text-muted-foreground` style.
  - Row 3: duration (Clock) + key (Badge outline) + tempo — the current metadata row minus album.
  - Row 4: theme badge — render `<ThemeLabel theme={primaryRecording.theme} />` if `primaryRecording.theme` is not null.
- `primaryRecording` is already resolved at line 75-78 (published or first recording). Use `primaryRecording.theme`.

**Step 5.2: SongListItem theme badge** (`delivery/webapp/src/components/songset/SongList.tsx`)
- In `SortableSongItem` (line 105), after the metadata row (line 225-247), add a theme row:
  ```tsx
  {item.recording?.theme && (
    <div className="flex items-center gap-1 mt-0.5">
      <ThemeLabel theme={item.recording.theme} />
    </div>
  )}
  ```
- Import `ThemeLabel` from `./ThemeLabel`.

**Step 5.3: Share view theme badge** (`delivery/webapp/src/app/share/[token]/page.tsx`)
- In the song item rendering (line 203-231), add a theme badge after the title/composer block:
  ```tsx
  {item.theme && <ThemeLabel theme={item.theme} />}
  ```
  Place it inside the `flex-1 min-w-0` div (line 211), below the composer line.
- Import `ThemeLabel` from `@/components/songset/ThemeLabel`.

### Phase 6: Webapp UI — songset-level theme aggregate

**Step 6.1: SongsetRow arc span** (`delivery/webapp/src/components/songset/SongsetRow.tsx`)
- Add `themes: string[]` to `SongsetRowProps` (line 37-60).
- In the badge row (line 227-245), after `RenderStatusBadge`, add:
  ```tsx
  {themes.length > 0 && <ThemeArcSpan themes={themes} />}
  ```
- Import `ThemeArcSpan` from `./ThemeLabel`.
- Update all callers of `SongsetRow` to pass `themes` from `SongsetListItem`. Find callers via grep for `SongsetRow` usage in `delivery/webapp/src/app/songsets/`.

**Step 6.2: DashboardSongsetCard arc span** (`delivery/webapp/src/components/dashboard/DashboardSongsetCard.tsx`)
- Add `themes: string[]` to `DashboardSongset` (line 11) and `DashboardSongsetCardProps`.
- In the badge row (line 82-84), after `RenderStatusBadge`, add:
  ```tsx
  {songset.themes.length > 0 && <ThemeArcSpan themes={songset.themes} />}
  ```
- Import `ThemeArcSpan` from `@/components/songset/ThemeLabel`.

**Step 6.3: Songset editor detail — all themes summary** (`delivery/webapp/src/app/songsets/[id]/SongsetEditorClient.tsx`)
- The `SongsetEditorClient` receives `initialData` with `items: ApiSongsetItem[]`. Compute distinct themes from items: `const themes = [...new Set(items.map(i => i.recording?.theme).filter(Boolean))]`.
- Below the songset title/description header (find the editor header in `SongsetEditor` component at `delivery/webapp/src/components/songset/SongsetEditor.tsx`), add a badge row:
  ```tsx
  {themes.length > 0 && (
    <div className="flex items-center gap-2 flex-wrap mt-2">
      {themes.map(theme => <ThemeLabel key={theme} theme={theme} />)}
    </div>
  )}
  ```
  This shows all distinct themes, each color-coded by arc phase.
- The `SongsetEditor` component needs a `themes` prop or the editor client computes it and passes it down. Check `SongsetEditor` props to determine the cleanest insertion point.

### Phase 7: Domain modeling

**Step 7.1: CONTEXT.md** (repo root)
- Add `VocalPosture` term under the Catalog section:
  ```md
  **VocalPosture**:
  A 3-value classification of a song component's lyrical addressee: "To God", "About God", or "To Congregation". Admin-side only; persisted at the component and recording level but not surfaced in the webapp.
  _Avoid_: voice, perspective, address
  ```
- Update the existing `Theme` term to note recording-level aggregation:
  Append: "At the recording level, the theme is aggregated from component-level classifications (most frequent, with chorus-preference tie-breaking) and persisted as `recordings.theme`."

**Step 7.2: ADR 0005** (`docs/adr/0005-posture-persisted-not-surfaced.md` — new file)
- Record the decision that vocal posture is persisted at the recording level but deliberately not surfaced in the webapp:
  ```md
  # Vocal Posture Persisted But Not Surfaced in Webapp

  Vocal posture (To God / About God / To Congregation) is a transition-planning input meaningful to admins and the songset constructor, not to end users building worship sets. We persist it at the recording level (`recordings.vocal_posture`) for admin CLI display and future use, but deliberately do not surface it as a label in the webapp. Surfacing it would add visual noise to every SongCard for a concept users cannot act on. If a future need arises, the column already exists and a UI change is sufficient without a schema migration.
  ```

## Critical files & anchors

1. `ops/admin-cli/src/stream_of_worship/admin/db/schema.py` — `ALTER_RECORDINGS_THEME_COLUMNS`, `CREATE_RECORDINGS_TABLE`, `RECORDING_COLUMNS_SELECT`, `RECORDING_COLUMN_COUNT`. New ALTER + column list update.
2. `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` — `_aggregate_recording_theme` (new), `_submit_component_analysis_job` (add aggregation call after upsert), `components_recording` (add `--backfill-song-classification` flag).
3. `delivery/webapp/src/lib/db/songsets.ts` — `SongsetListItem.themes`, `SongsetItemRecording.theme`, `PublicSongsetItem.theme`, `listSongsetSummaries` query (add `array_agg`), `getSongsetEditorData` query, `getSongsetPublicView` query.
4. `delivery/webapp/src/components/songset/SongCard.tsx` — `SongCardData.recordings[].theme`, 4-row layout restructure, import `ThemeLabel`.
5. `delivery/webapp/src/lib/i18n/messages/themes.ts` — new i18n bundle with 12 theme translations (en + zh-Hant).

## Verification

**Admin CLI — aggregation + backfill:**
```bash
# Set DB env
set -a; . /opt/sow/.env; set +a

# Run schema migration (adds columns)
uv run --project ops/admin-cli --extra admin sow-admin db init

# Backfill existing recordings from song_components
uv run --project ops/admin-cli --extra admin sow-admin audio components <song_id> --backfill-song-classification

# Verify recording has theme
uv run --project ops/admin-cli --extra admin sow-admin audio show <song_id>
# Expected: "Theme: 讚美" in the recording display

# Batch backfill
echo "song_id_1\nsong_id_2" | uv run --project ops/admin-cli --extra admin sow-admin audio components --stdin --backfill-song-classification
```

**Admin CLI tests:**
```bash
NO_COLOR=1 uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/ -v -k "theme or recording or component"
```

**Webapp — schema + build:**
```bash
cd delivery/webapp
pnpm build  # Type-checks all interface changes (SongCardData, SongListItem, etc.)
```

**Webapp — UI verification (browser):**
```bash
cd delivery/webapp && pnpm dev
# Open http://localhost:8080
# 1. Home page: verify SongCards show theme badge in row 4 (e.g. "Praise" or "讚美" with arc-phase color)
# 2. Favorites page: verify same theme badges
# 3. Songsets list: verify SongsetRow shows arc span badge (e.g. "讚美 → 差遣")
# 4. Home recent songsets: verify DashboardSongsetCard shows arc span
# 5. Songset editor: verify all-themes badge row below title + per-song theme badges in list
# 6. Share link: verify theme badges in song list
# 7. Toggle locale to English: verify theme labels switch to English (Praise, Worship, etc.)
# 8. Verify SongCard layout: row 1 title, row 2 artist+album, row 3 duration+key+tempo, row 4 theme
```

**Webapp tests:**
```bash
cd delivery/webapp && pnpm test
```

**DB verification:**
```bash
set -a; . /opt/sow/.env; set +a
psql -c "SELECT hash_prefix, theme, vocal_posture FROM recordings WHERE theme IS NOT NULL LIMIT 5;"
# Expected: rows with non-null theme values from the 12-enum
```

## Assumptions & contingencies

- **Assumption**: at least some existing recordings have `song_components` rows with non-null `theme` values (from prior `--classify-theme` runs). If none exist, the backfill command will report "no theme data" for all songs and the webapp will show no theme badges until new component analyses are run with `--classify-theme`. This is expected, not a failure.
- **Assumption**: the 5-color palette in `THEME_PHASE_COLORS` meets WCAG AA contrast on the webapp's light and dark backgrounds. If dark-mode contrast is insufficient, add a dark-mode variant: `THEME_PHASE_COLORS_DARK` selected via the existing `next-themes` `useTheme` hook (same pattern as `sonner.tsx` line 8). Implementer: verify contrast in both themes during browser verification; adjust hex values if needed.
- **Contingency**: if the `array_agg(... order by ...)` SQL expression in `listSongsetSummaries` causes a Postgres error (ordering inside aggregate filter), fall back to collecting themes in a second query: `SELECT recordings.theme FROM songset_items JOIN recordings ON ... WHERE songset_items.songset_id = $1 ORDER BY songset_items.position` and map the result array. The second query adds one round-trip per songset list page but is guaranteed correct.
- **Contingency**: if `SongsetEditor` (the presentational component) does not accept a `themes` prop and refactoring it is risky, compute themes inside `SongsetEditorClient` and render the all-themes badge row directly in the client component, outside `SongsetEditor`. This avoids touching the editor component's prop interface.