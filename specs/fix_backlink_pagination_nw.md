# Webapp Pagination Place Restoration

## Context

When a user is on the Songsets list at page N (e.g. page 3), clicks into a
songset to open the editor, then taps the back arrow to return to the list,
the list resets to page 1 — losing the user's pagination (and search) place.

Root cause: every "back to songsets" navigation uses
`router.push("/songsets")` (no query string), discarding the `?page=N` and
`?search=...` query params that `SongsetsClient` syncs into the URL via
`router.replace` (`SongsetsClient.tsx:134-140`). A plain `push("/songsets")`
lands on page 1.

The list URL already carries the full restorable state (`?page=3&search=foo`),
and the entry navigation to the editor is a `next/link` **push**
(`SongsetRow.tsx:120-121, 207-208`), so the prior `/songsets?page=N` entry is
sitting right behind the editor in the history stack. `router.back()` will pop
to it; Next.js App Router restores scroll position on back navigation by
default, and the server component `songsets/page.tsx` re-reads `searchParams`
so page N renders correctly.

## Approach

Single behavior change: the editor's primary back arrow navigates with
`router.back()` (guarded) instead of `router.push("/songsets")`.

### Step 1 — Change `handleBack` in `SongsetEditor.tsx`

File: `delivery/webapp/src/components/songset/SongsetEditor.tsx`
Symbol: `handleBack` (lines 147-150)

Current:
```ts
const handleBack = () => {
  router.push("/songsets");
};
```

New:
```ts
const handleBack = () => {
  // Prefer browser back so the list restores its pagination/search place
  // (the URL behind this entry is /songsets?page=N&search=...). Fall back
  // to the bare list when there is no prior history (direct entry/refresh).
  if (typeof window !== "undefined" && window.history.length > 1) {
    router.back();
  } else {
    router.push("/songsets");
  }
};
```

`router` is already in scope (`useRouter()` at line 107); the component is a
client component (`"use client"`, line 1), so `window` is available in the
click handler. No new imports.

### Out of scope (explicit non-changes)

- **Error-state "Back to songsets" buttons** (`SongsetEditorClient.tsx:517`,
  `play/page.tsx:155`, `render/RenderPageClient.tsx:198`): these render only
  when the songset/render data failed to load, and their text promises the
  *list* (not "go back in history"). `router.back()` from a render/play error
  would return to the editor, not the list — different semantics. Left as
  `router.push("/songsets")`.
- **Delete handler** (`SongsetEditor.tsx:216-217`): after deleting a songset,
  `router.push("/songsets")` is correct — the deleted item's page may no
  longer be valid; a fresh page-1 list is the right destination.
- **Duplicate handler** (`SongsetEditorClient.tsx:381`): navigates to the new
  songset's editor — unrelated.
- No `next.config.ts` changes: App Router scroll restoration on back/forward
  is on by default; no `experimental.scrollRestoration` needed.

## Critical files & anchors

- `delivery/webapp/src/components/songset/SongsetEditor.tsx` — `handleBack`
  (lines 147-150): the sole edit.
- `delivery/webapp/src/app/songsets/SongsetsClient.tsx:134-140` — the
  `router.replace` URL-sync effect that puts `?page=N` in the URL in the first
  place; the reason `router.back()` restores the right page. No edit, but
  confirms the mechanism.
- `delivery/webapp/src/components/songset/SongsetRow.tsx:120-121, 207-208` —
  the `<Link href={/songsets/${id}}>` push navigation that leaves the
  `/songsets?page=N` entry on the stack. No edit.

## Verification

Prerequisite: webapp dev server + a logged-in user with ≥3 pages (≥41
songsets) of songsets.

1. Start dev server:
   ```bash
   pnpm --filter sow-webapp dev
   ```
   Wait for `Ready` on `http://localhost:8080`.

2. **Primary case (restoration works):**
   - Browser: open `http://localhost:8080/songsets`.
   - Click page **3** in the pagination (data-testid `pagination-page-3`).
     Confirm URL is `/songsets?page=3`.
   - Click any songset row → editor loads at `/songsets/<id>`.
   - Click the back arrow (ArrowLeft, aria-label "Go back") in the editor app
     bar.
   - **Expect:** URL returns to `/songsets?page=3`; list shows page 3 (same
     songsets as before clicking in); scroll position near where it was.

3. **Search-state preservation:**
   - On `/songsets`, type a search term and submit; confirm URL is
     `/songsets?page=1&search=<term>` and results show.
   - Click into a songset from the results, then back arrow.
   - **Expect:** URL returns to `/songsets?page=1&search=<term>`; filtered
     list re-shown.

4. **Edge case — direct entry falls back:**
   - Open a new tab directly to `http://localhost:8080/songsets/<some-id>`
     (no prior history on the list).
   - Click the back arrow.
   - **Expect:** navigates to `/songsets` (page 1), since
     `window.history.length === 1` triggers the `router.push("/songsets")`
     fallback.

5. **Edge case — refresh on detail then back:**
   - From the list on page 3, click into a songset, then **refresh** the
     detail page in the browser.
   - Click the back arrow.
   - **Expect:** `router.back()` returns to `/songsets?page=3` (history entry
     preserved across refresh).

If check 2 fails (lands on page 1): confirm the editor back arrow is wired to
`handleBack` (`SongsetEditor.tsx:273` `onClick={handleBack}`) and that the
guard `window.history.length > 1` is truthy at that point (add a
`console.log(window.history.length)` temporarily to diagnose).

## Assumptions & contingencies

- **Assumption:** App Router's default client navigation cache + scroll
  restoration preserve the list's rendered page-N state on `router.back()`. If
  in practice the list re-mounts and briefly flashes page 1 before the
  `loadSongsets` effect (line 84-132) refetches page N, that is cosmetic — the
  URL still reads `?page=N` and the data settles on page N. No action needed
  unless the flash is jarring.
- **Contingency:** if `router.back()` ever lands somewhere other than the
  list (e.g. user navigated editor→editor via duplicate), the fallback
  `router.push("/songsets")` is the safe floor; acceptable per the chosen
  approach.