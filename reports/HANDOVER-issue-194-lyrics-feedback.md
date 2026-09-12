# Handover: Issue #194 — Collect User Lyrics Feedback

Branch: `user_lyrics_feedback` (spec commit `041f703a`, ADR `f9311c01` already pushed)
Date: 2026-09-12
Status: Webapp complete and green (tsc 0 errors, 420 targeted tests passing). **Admin CLI not started.** Final verification/commit/push not done.

Spec: `specs/collect_user_lyrics_feedback.md` · ADR: `docs/adr/0007-lyrics-feedback-is-advisory.md` (advisory-only; nothing but an admin action writes `resolved_at`) · Glossary: `CONTEXT.md` "Lyrics Feedback".

## Progress summary (todo phases)

| Phase | Status |
|---|---|
| Schema (webapp Drizzle + admin mirror DDL) | ✅ done, tests green |
| Webapp API (situation resolver + feedback route) | ✅ done, tests green |
| Webapp UI (hook, row, panel, controller, i18n) | ✅ done, tests green, tsc clean |
| Admin CLI (`lyrics feedback list/resolve/unresolve`) | ❌ **not started** — was about to write failing tests |
| Verification (full suites, lint, integration) | ❌ partial (targeted runs only) |
| code-review → commit → push | ❌ |

## What exists now (files)

### Schema
- `delivery/webapp/src/db/schema.ts` — `lyricsFeedback` table (`lyrics_feedback`: id nanoid PK, user_id FK user cascade, recording_content_hash FK recordings cascade, rating, reason nullable, resolved_at nullable, created_at/updated_at; UNIQUE(user_id, recording_content_hash); index `idx_lyrics_feedback_recording_resolved`) + `lyricsFeedbackRelations`.
- `delivery/webapp/drizzle/0024_lyrics_feedback.sql` — hand-written migration (IF NOT EXISTS + `DO $$ ... duplicate_object` FK guards, same precedent as 0018/0022/0023).
- `delivery/webapp/drizzle/meta/_journal.json` — appended entry `{idx: 24, version: 7, when: 1787526910000, tag: "0024_lyrics_feedback", breakpoints: true}` (no snapshot; matches hand-written convention).
- `ops/admin-cli/src/stream_of_worship/db/app/user_data_schema.py` — `CREATE_LYRICS_FEEDBACK_TABLE`, `CREATE_LYRICS_FEEDBACK_UPDATE_TRIGGER`, feedback indexes merged into `CREATE_USER_DATA_INDEXES`, wired into `ALL_USER_DATA_SCHEMA_STATEMENTS` (16 statements; parses OK; verified `lyrics_feedback` present).

### Webapp API
- `src/lib/lyrics/situation.ts` — `resolveLyricsSituation(hash)` → `{kind: "synced"|"unsynced"|"none"}` mirroring the `/api/lyrics/[recordingContentHash]` resolution order (R2 canonical LRC unless `lrcStatus==="missing"` → isValidLRC → else unsynced text via songs.lyricsLines/lyricsRaw → none; user override deliberately ignored). `validateFeedbackSubmission(rating, reason, situation)` = spec matrix: happy only when lyrics exist; missing only when NOT synced; timing only when synced; wrong_text/other any; reason null for happy, non-null for sad; closed vocab.
- `src/app/api/lyrics/feedback/[recordingContentHash]/route.ts` — GET (caller's row → `{feedback: {rating, reason}|null}`), PUT (resolve situation → validate → upsert with `onConflictDoUpdate` on (userId, recordingContentHash), returns stored feedback), DELETE (idempotent retract). 401 unauth, 400 machine-readable errors, 500 wrap.

### Webapp UI
- `src/hooks/useLyricsFeedback.ts` — GET on mount (401 → null), `submit(rating, reason?)` optimistic with rollback (reads `feedbackRef` — avoid re-adding `feedback` to useCallback deps), `retract()`.
- `src/components/audio/LyricsFeedbackRow.tsx` — exports `LyricsSituationKind = "synced"|"unsynced"|"none"`. Right-aligned Smile/Frown icons, `aria-pressed` filled states, tap-active-retracts, sad expands inline chips; chips filtered by situation (`chipsForSituation`: synced → timing/wrong_text/other; unsynced/none → missing/wrong_text/other; happy hidden on `none`). Uses `TranslationKey`-typed `t`.
- `src/components/audio/PlayerLyricsPanel.tsx` — computes `situation` (null on loading/error → row hidden; synced if isValidLRC; unsynced for lines/raw; none otherwise), renders row below scroll container (flex-col layout, scroll area + footer).
- `src/components/play/LyricJumpList.tsx` — new optional prop `currentRecordingContentHash?: string | null`; renders `LyricsFeedbackRow` after the scrollable content div (pinned at sheet bottom) only when hash non-null AND `chapters[currentSongIndex]` exists; situation = `lines.length > 0 ? "synced" : "none"` (cast via `as LyricsSituationKind`). NOTE: this edit accidentally deleted the `export type { Chapter, ChapterLine }` re-export — no consumers found, left deleted.
- `src/components/play/ControllerPlayer.tsx` — new prop `chapterRecordingHashes?: (string|null)[]` (per chapter index); passes `chapterRecordingHashes?.[currentSongIndex] ?? null` to LyricJumpList. Destructure verified complete (`presentationMediaStatus`, `isCastSupported`, `chapterRecordingHashes` all present; tsc 0 errors). Anonymous share-controller variant never receives hashes → row hidden.
- `src/app/songsets/[id]/play/controller/page.tsx` — `SongsetData` fetch now also maps `songsetData.items` sorted by `position` → `recording.contentHash` into new state `chapterRecordingHashes`, passed to `<ControllerPlayer chapterRecordingHashes={...}>` (line ~297). Anonymous share-controller variant untouched (excluded by spec).
- `src/lib/i18n/messages/audio.ts` — 6 keys × 2 locales under `audio.feedback.*` (happyAriaLabel, sadAriaLabel, reasonMissing, reasonTiming, reasonWrongText, reasonOther). Verified parity.

### Tests (all passing)
- `src/test/db/schema.test.ts` (+7 assertions for the table)
- `src/test/lib/lyrics/situation.test.ts` (26: resolver 8 + matrix 18)
- `src/test/api/lyrics/feedback.test.ts` (23: auth 3, GET 3, PUT matrix 13, DELETE 2)
- `src/test/hooks/useLyricsFeedback.test.ts` (7)
- `src/test/components/audio/LyricsFeedbackRow.test.tsx` (15)
- `src/test/components/audio/PlayerLyricsPanel.test.tsx` (13 total; +5 footer tests; `mockFeedbackOk()` in both describes' beforeEach)
- `src/test/components/play/LyricJumpListFeedback.test.tsx` (7; new file)
- Original `LyricJumpList.test.tsx` (19) and `controller-page.test.tsx` (35) unmodified, green.

## Environment / tooling gotchas (encountered)

1. **Edit tool path resolution**: session cwd is `ops/analysis-service`. Use **absolute paths** in `edit`/`read` for `delivery/webapp/**` files; relative paths fail or hit stale snapshots. Multi-hunk edits mangled files several times — prefer single-hunk edits or `python3` heredoc string replace via bash for tricky regions.
2. **Webapp vitest needs a dummy DB env** (db/index.ts throws without it): `SOW_DATABASE_URL="postgresql://test:test@localhost:5432/test" npx vitest run <paths>`.
3. `vi.requireActual` does NOT exist in this vitest 4.1.6 — use async factory with `await vi.importActual<...>(...)`. Hoisted `vi.mock` factories cannot reference top-level import bindings (ReferenceError) — reference module-scope `vi.fn()` consts only.
4. bash occasionally auto-backgrounds (job stubs) — `sleep 3` then re-run; read the completion notice.
5. Drizzle `_journal.json` must keep trailing newline (git diff cleanliness).

## Remaining work

### 1. Admin CLI (new top-level `lyrics` group)
Create `ops/admin-cli/src/stream_of_worship/admin/commands/lyrics.py`:
```python
console = Console()
app = typer.Typer(help="Lyrics curation operations")
feedback_app = typer.Typer(help="Lyrics feedback triage")
app.add_typer(feedback_app, name="feedback")
```
Register in `main.py` alongside the other `add_typer` calls: `app.add_typer(lyrics_commands.app, name="lyrics", help="Lyrics curation operations")`.

**`lyrics feedback list`** — group by Recording, join songs for titles. SQL sketch (follow `_load_key_review_rows` prior art in `commands/audio.py:10156` — direct `db_client.connection.cursor()`):
```sql
SELECT r.content_hash, r.hash_prefix, r.lrc_status, s.title, s.id AS song_id,
       COUNT(*) FILTER (WHERE f.resolved_at IS NULL) AS open_count,
       COUNT(*) FILTER (WHERE f.resolved_at IS NULL AND f.rating='sad' AND f.reason='missing') AS n_missing,
       ... timing, wrong_text, other ...,
       MAX(f.created_at) FILTER (WHERE f.resolved_at IS NULL) AS latest_report
FROM lyrics_feedback f
JOIN recordings r ON r.content_hash = f.recording_content_hash
JOIN songs s ON s.id = r.song_id
GROUP BY r.content_hash, r.hash_prefix, r.lrc_status, s.title, s.id
HAVING COUNT(*) FILTER (WHERE f.resolved_at IS NULL) > 0   -- unless --all
```
- Filters: `--rating happy|sad`, `--reason missing|timing|wrong_text|other` (filter inside aggregates or drop groups without that reason among open rows — implementer's call; spec wants "work only timing complaints first"), `--all` (include fully-resolved recordings).
- Columns: song title, content-hash prefix (`hash_prefix`), pipeline Lyrics status (`r.lrc_status`), open count, reason breakdown (e.g. `missing×2 timing×1`), latest report date, **suggested action** — presentation-only mapping: missing→`audio lrc <song-id>`, timing→`audio align-lrc <song-id>`, wrong_text/other→`manual review` (pick dominant open reason; tie → first in missing/timing/wrong_text/other order). Rich `Table` per `key_review_list` style (`audio.py:10245`).

**`lyrics feedback resolve <content-hash-or-song-id>`** and **`unresolve`**:
- Accept a full `content_hash` OR a song id; if arg matches a `songs.id` row, resolve/unresolve open rows for ALL non-deleted recordings of that song (`WHERE content_hash IN (SELECT content_hash FROM recordings WHERE song_id = %s AND deleted_at IS NULL)`); else treat as content hash (exact `recordings.content_hash`).
  The list query reads `recordings.lrc_status` for the pipeline-status column.

### 2. Admin CLI tests (Seam 3, TDD — none written yet)
- New `ops/admin-cli/tests/admin/test_lyrics_feedback_commands.py`: `CliRunner` + `setup_db`-style fixture (copy `_make_provider_and_schema` / `_write_config` / `_drop_all_tables` from `tests/admin/test_audio_commands.py:22-64`). Seed songs/recordings/user rows + `lyrics_feedback` rows via raw SQL. Cover: list grouping/counts/reason breakdown/filters (--rating/--reason/--all), suggested-action column, resolve/unresolve end state (re-query DB), resolve by song id.
- **Must also update existing files**:
  - `tests/db/test_full_schema_init.py`: add `"lyrics_feedback"` to `EXPECTED_TABLES` (line ~29), add FK rows to `required` list (lyrics_feedback.user_id→user.id, .recording_content_hash→recordings.content_hash), and add `lyrics_feedback` to both cleanup DROP lists (lines ~59, ~120).
  - Every `DROP TABLE IF EXISTS ...` cleanup block in `tests/admin/**` + `tests/db/**` (grep: they're in conftest.py, test_audio_commands.py, test_catalog_commands.py, test_client.py, test_scraper.py, commands/test_db_commands.py, test_postgres_clients.py, test_user_client.py, db/test_user_client.py) — add `DROP TABLE IF EXISTS lyrics_feedback CASCADE;` above the `lyric_mark` line.
- Run: `uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest -v` (integration excluded by default; testcontainers needs Docker; run `-m integration` to include).

### 2b. Webapp test gaps (spec-required, small — do before final commit)
1. Route-test matrix cells missing in `src/test/api/lyrics/feedback.test.ts`:
   - happy ACCEPTED when situation is `unsynced` (spec allows happy for synced OR unsynced; only "none" is covered today).
   - switch-overwrite: PUT sad+timing then PUT happy → both 200, second call still upserts (pins "latest opinion wins"). Assert upsert `set` includes `updatedAt: expect.any(Date)` — the webapp deploy path has NO updated_at trigger (trigger exists only in the admin mirror DDL), so the route sets it explicitly; keep that.
2. Rename misleading test (~line 166 of `src/test/lib/lyrics/situation.test.ts`): "sad+missing accepted when synced lyrics exist" → "sad+missing **rejected** when synced lyrics exist" (assertion correct, name wrong).
3. Controller-page test: `src/test/app/controller-page.test.tsx` mocks ControllerPlayer at page level — add an assertion that the mock received `chapterRecordingHashes` (pins the silent-omission class; the optional prop means tsc/tests can't catch a missing page→player prop).
- `delivery/webapp/src/test/db/schema.test.ts` import order is slightly off (`userSettings` after `songLineEmbeddings`) — verify with `pnpm lint` whether ESLint enforces import order here; fix or drop accordingly.
- Docstring/table-list updates: `postgres_schema.py` module docstring (add `lyrics_feedback` to the table list), `db/user_client.py` cascade-delete docstring + preview dict, `commands/users.py` cascade warning string — all enumerate user tables; tests assert exact sets (`tests/test_users_command.py` CASCADE_TABLES, `tests/db/test_user_client.py:123`) so update assertions together.
- `LyricJumpList.tsx` ~line 303: drop redundant `as LyricsSituationKind` cast (type-safe without it).

### 3. Verification
- Webapp full: `cd delivery/webapp && pnpm test && pnpm lint && npx tsc --noEmit` (tsc currently 0 errors; full suite not yet run — only targeted areas).
- Admin CLI: pytest as above.
- Other suites untouched by this change; optionally run render-worker/legacy to prove isolation.
- Optional manual browser validation per repo `AGENTS.md` webapp visual-validation recipe (headless Chrome via hub + CDP, test-user env vars) — spec says no E2E seam this iteration, manual only if needed.

### 4. Finish
- Run `graphify update .` (repo rule after code changes).
- `/code-review` skill on the branch diff.
- All work is UNCOMMITTED on `user_lyrics_feedback` (20 files: 10 modified, 10 new). If handing off mid-stream, at minimum commit the green webapp state first (`git add` the listed files; message suggestion: `feat(webapp): user lyrics feedback — API, panel, controller (issue #194)`) so nothing is lost.
- Repo rule is mandatory: `git pull --rebase && git push && git status` must show up-to-date. Do not stop before push succeeds.

## Test commands cheat-sheet
```bash
# webapp (from delivery/webapp)
SOW_DATABASE_URL="postgresql://test:test@localhost:5432/test" npx vitest run src/test/lib/lyrics src/test/api/lyrics src/test/hooks/useLyricsFeedback.test.ts src/test/components/audio src/test/components/play src/test/db/schema.test.ts
npx tsc --noEmit && pnpm lint && pnpm test

# admin CLI (from repo root)
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest -v
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/admin/test_lyrics_feedback_commands.py -v -m integration
```

## Design invariants (do not break)
- One row per (user, recording); same-rating re-submit = retract; switching overwrites; admin-only `resolved_at`; never mutate `recordings.lrc_status`; no auto-resolve on regeneration; no cross-user aggregates; no anonymous/share-controller feedback; chips must agree with server validation matrix; closed reason vocab; reason null iff happy.