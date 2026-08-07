# Handover: Webapp Player — Find Containing Songsets (v2)

**Date:** 2026-08-07
**Spec:** `specs/webapp-player-find-containing-songsets-v2.md`
**Status:** Implementation ~90% complete. Lint + typecheck pass. Tests need final fixes.

---

## What's Done

### Phase 1: Player types and call sites — COMPLETE

- **`src/contexts/AudioPlayerContext.tsx`**: Added `songId?` and `originSongsetId?` fields to `AudioTrack` interface.
- **`src/hooks/useAudioPlayer.ts`**: Added `originSongsetId?` to `PlaySongOptions`; `playSong` now sets `songId` and `originSongsetId` on the `AudioTrack`.
- **`src/components/songset/SongList.tsx`**: Both `play()` calls in `handlePlaySong` now include `songId` and `originSongsetId` (from new `songsetId` prop). Added `songsetId?` and `highlightSongId?` to `SongListProps`.
- **`src/components/songset/BrowseSheet.tsx`**: Both `play()` calls now include `songId` (no `originSongsetId`).
- **`src/components/search/SemanticSearch.tsx`**: Both `play()` calls now include `songId` (no `originSongsetId`).
- **`src/components/transition/TransitionSheet.tsx`**: No changes needed (transition tracks, not song tracks).

### Phase 2: DB schema, helper, API route — COMPLETE

- **`src/db/schema.ts`**: Added `index("idx_songset_items_song_id").on(t.songId)` to `songsetItems` table config.
- **`src/lib/db/songsets.ts`**: Added `users` to imports. Added `SongsetContainingSong` interface and `findSongsetsContainingSong()` function using a CTE subquery for item counts, with origin-first sorting and `timePageLoad` instrumentation.
- **`src/app/api/songs/[id]/songsets/route.ts`** (NEW): `GET /api/songs/:songId/songsets?origin=<songsetId>` — auth-gated, validates `songId`, trims `origin` param, returns `{ songsets: [...] }` with `owner` field.

### Phase 3: Popover UI — COMPLETE

- **`src/components/ui/popover.tsx`** (NEW): Base UI Popover wrapper following the same pattern as `tooltip.tsx`. Exports `Popover`, `PopoverTrigger`, `PopoverContent`, `PopoverClose`.
- **`src/components/audio/LocateSongsetsPopover.tsx`** (NEW): Fetches containing songsets on every open (no caching). Hidden for non-song tracks or when `songId` is absent. Shows loading/error/empty states. Marks origin songset. Navigates to `/songsets/[id]?highlightSong=[songId]` on click. Returns focus to trigger on close. Uses `AbortController` for fetch cleanup.
- **`src/components/audio/AudioPlayerBar.tsx`**: Imported and rendered `<LocateSongsetsPopover />` immediately after the title/artist block in the track-info area.

### Phase 4: Highlight and scroll — COMPLETE

- **`src/components/songset/SongList.tsx`**:
  - Added `data-song-id={item.songId}` to `SortableSongItem` root `<div>`.
  - Added `isHighlighted?` prop to `SortableSongItemProps`; applies `ring-2 ring-primary border-primary animate-pulse` to `<Card>`.
  - Added scroll-to-and-highlight effect using `requestAnimationFrame` with retry loop (max 20 attempts × 50ms). Uses `globalThis.CSS.escape()` (not `CSS.escape` — the `@dnd-kit/utilities` `CSS` import shadows the global). Dismisses highlight after 3 seconds.
  - Passes `isHighlighted={highlightedSongId === item.songId}` to each `SortableSongItem`.
- **`src/components/songset/SongsetEditor.tsx`**: Added `highlightSongId?` to `SongsetEditorProps`; passes `songsetId={songset.id}` and `highlightSongId` to `SongList`.
- **`src/app/songsets/[id]/SongsetEditorClient.tsx`**: Reads `highlightSongId` from `searchParams.get("highlightSong")`. Added URL cleanup effect that strips only `highlightSong` while preserving other params (`?new=true`, `?share=true`, etc.). Passes `highlightSongId` to `SongsetEditor`.

### Verification

- **`pnpm lint`**: PASSES (0 errors, 0 warnings)
- **`npx tsc --noEmit`**: PASSES (0 errors)

---

## What's Left

### Test fixes needed — IN PROGRESS

Run `pnpm test` from `delivery/webapp/` to see current failures. As of last run: **23 tests failed | 1902 passed**.

#### 1. `src/test/components/audio/AudioPlayerBar.test.tsx` (22 failures)

**Root cause:** The `next/navigation` mock was missing `useRouter` and `useSearchParams`, which `LocateSongsetsPopover` (now rendered inside `AudioPlayerBar`) requires.

**Fix applied but not yet verified:** Updated the mock at line 14-17 to include `useRouter` (returns `push`, `replace`, etc.) and `useSearchParams` (returns `new URLSearchParams()`).

**To verify:** Run:
```bash
cd delivery/webapp && pnpm test -- --run src/test/components/audio/AudioPlayerBar.test.tsx
```

If still failing, check whether the `LocateSongsetsPopover` renders correctly in the test environment — it may need a `fetch` mock for `/api/songs/.../songsets`. The popover is always rendered but only fetches when opened, so it should not affect initial render tests.

#### 2. `src/test/components/search/SemanticSearch.test.tsx` (1 failure)

**Root cause:** Test at line 541 asserts `mockPlay` was called with an exact object that now includes `songId: "song-1"`.

**Fix applied but not yet verified:** Updated the expected object at line 541-549 to include `songId: "song-1"`.

**To verify:** Run:
```bash
cd delivery/webapp && pnpm test -- --run src/test/components/search/SemanticSearch.test.tsx
```

#### 3. Check for other test breakages

Search for other tests that assert exact `play()` call objects:
```bash
cd delivery/webapp && rg "toHaveBeenCalledWith" src/test/ -g "*.test.*" -A 10 | rg -B 5 "type.*song"
```

The `BrowseSheet.test.tsx` mocks `play: vi.fn()` but does not appear to assert exact call objects — verify. The `useAudioPlayer.test.tsx` calls `playSong()` (which constructs the track internally) and checks `currentTrack.title`, not the exact object — should be fine.

### After tests pass

1. **Generate Drizzle migration** for the new index:
   ```bash
   cd delivery/webapp && npx drizzle-kit generate
   ```
   This creates a migration file for `idx_songset_items_song_id`. In dev, `npx drizzle-kit push` can be used instead.

2. **Run `graphify update .`** from project root to keep the knowledge graph current.

3. **Git commit and push** — the AGENTS.md mandates `git push` succeeds before declaring completion.

---

## Key Implementation Notes

### `CSS.escape` shadowing

The `SongList.tsx` file imports `CSS` from `@dnd-kit/utilities` (for drag transform). This shadows the global `CSS` object, so `CSS.escape()` must be called as `globalThis.CSS.escape()` to work at runtime and pass typecheck.

### Base UI Popover API

The project uses `@base-ui/react` (not Radix UI). The Popover wrapper in `src/components/ui/popover.tsx` follows the same `asChild` → `render` prop pattern as `tooltip.tsx`. Key differences from Radix:
- `PopoverTrigger` uses `render` prop instead of `asChild` for composition.
- `PopoverContent` wraps `Portal` → `Positioner` → `Popup`.
- `onOpenChange` signature is `(open: boolean, eventDetails) => void`.

### `set-state-in-effect` lint rule

The project enforces `react-hooks/set-state-in-effect`. The `LocateSongsetsPopover` initially called `setLoading(true)` / `setError(null)` synchronously inside the fetch `useEffect`. To comply, these `setState` calls were moved to the `handleOpenChange` callback (called from `onOpenChange`), so they run during the event handler, not the effect.

### URL cleanup preserves other params

The `SongsetEditorClient` highlight cleanup effect uses `URLSearchParams` to delete only `highlightSong` and reconstruct the query string, preserving `?new=true`, `?share=true`, etc. This is separate from the existing `?share=true` cleanup effect (which does a full `router.replace`).

### `originSongsetId` is optional and never fabricated

`SongList` passes `originSongsetId: songsetId` (from its prop). `BrowseSheet` and `SemanticSearch` pass only `songId` without `originSongsetId`. The `LocateSongsetsPopover` reads `currentTrack.originSongsetId` and only includes the `origin` query param when present.

---

## Files Created

| File | Description |
|------|-------------|
| `delivery/webapp/src/components/ui/popover.tsx` | Base UI Popover wrapper component |
| `delivery/webapp/src/components/audio/LocateSongsetsPopover.tsx` | Player bar popover for reverse songset lookup |
| `delivery/webapp/src/app/api/songs/[id]/songsets/route.ts` | API endpoint returning containing songsets |

## Files Modified

| File | Changes |
|------|---------|
| `delivery/webapp/src/contexts/AudioPlayerContext.tsx` | Added `songId`, `originSongsetId` to `AudioTrack` |
| `delivery/webapp/src/hooks/useAudioPlayer.ts` | Added `originSongsetId` to `PlaySongOptions`; pass fields into `playSong` |
| `delivery/webapp/src/components/songset/SongList.tsx` | Pass origin/songId in `play()`; add `data-song-id`; scroll+highlight effect; `isHighlighted` prop; `songsetId`/`highlightSongId` props |
| `delivery/webapp/src/components/songset/SongsetEditor.tsx` | Thread `highlightSongId` to `SongList`; pass `songsetId` |
| `delivery/webapp/src/app/songsets/[id]/SongsetEditorClient.tsx` | Read `?highlightSong=`; pass to editor; clean URL safely |
| `delivery/webapp/src/components/audio/AudioPlayerBar.tsx` | Render `LocateSongsetsPopover` next to track metadata |
| `delivery/webapp/src/components/songset/BrowseSheet.tsx` | Add `songId` to both `play()` calls |
| `delivery/webapp/src/components/search/SemanticSearch.tsx` | Add `songId` to both `play()` calls |
| `delivery/webapp/src/lib/db/songsets.ts` | Add `findSongsetsContainingSong`, `SongsetContainingSong` interface; import `users` |
| `delivery/webapp/src/db/schema.ts` | Add `idx_songset_items_song_id` index |
| `delivery/webapp/src/test/components/audio/AudioPlayerBar.test.tsx` | Mock `useRouter`/`useSearchParams` in `next/navigation` mock |
| `delivery/webapp/src/test/components/search/SemanticSearch.test.tsx` | Add `songId` to expected `play()` call object |

---

## Recommended Next Steps

1. Run `pnpm test` from `delivery/webapp/` and fix any remaining test failures.
2. Run `npx drizzle-kit generate` to create the migration for the new DB index.
3. Run `graphify update .` from project root.
4. `git add -A && git commit && git pull --rebase && git push`.
