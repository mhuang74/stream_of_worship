# Songset Sharing v2 — Detailed Implementation Plan

**Date:** 2026-06-06
**Status:** Ready for Implementation
**Companion to:** `specs/songset-sharing-readonly-playback-v2.md`

---

## 0. Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Stale detection | `songset.updatedAt > renderJob.completedAt` | Any songset edit after render completed makes playback stale. Simple and conservative. |
| Active-share limit scope | Per-user (current behavior, 20 max) | Consistent with existing behavior. |
| Rate limiting | TODO in code + tests | No rate-limit helper exists in the project. Follow spec fallback guidance. |
| Public song list query | New dedicated `getSongsetPublicView()` function | Clean separation of concerns; returns only whitelisted fields. |
| `renderJobId` nullable migration | `ALTER TABLE songset_share ALTER COLUMN render_job_id DROP NOT NULL` | Existing shares keep their `renderJobId`; new songset-level shares store `null`. |

---

## 1. Current State Summary

### What Exists

- `songset_share` table with `render_job_id NOT NULL` — shares are tightly coupled to completed render jobs
- `POST /api/share` — only accepts `{ renderJobId }`, requires completed render job
- `GET /api/share` — lists user's shares, filters by `renderJobId` only
- `GET /api/share/[token]` — returns flat response with top-level `mp3Url`, `mp4Url`, `songsetName`
- `DELETE /api/share/[token]` — soft-revokes by setting `revokedAt`
- `ShareDialog` component — fully built (429 lines) but **never imported by any page**
- Public share pages at `/share/[token]/` — basic landing + projection + audio playback
- All share actions navigate to `?share=true` which **nobody reads** — the query param is never consumed

### Key Gaps

- No songset-level sharing (requires completed render job)
- No live songset data in public API (only songset name + playback URLs)
- No song list on public share page
- No stale playback detection
- No formatted share message (only bare URL)
- No live-link warning
- `ShareDialog` is orphaned — never wired into any page
- `NEXT_PUBLIC_BASE_URL` fallback is empty string — can produce broken relative URLs
- Active-share count doesn't exclude expired shares
- No `?songsetId` filter on `GET /api/share`

---

## 2. Implementation Phases

### Phase 1: DB Schema & Migration

**File:** `webapp/src/db/schema.ts`

Change `renderJobId` from `.notNull()` to nullable:

```ts
// Before (line ~364):
renderJobId: text("render_job_id").notNull(),

// After:
renderJobId: text("render_job_id"),
```

**Generate migration** from `webapp/`:

```bash
npx drizzle-kit generate
```

Expected SQL:

```sql
ALTER TABLE songset_share ALTER COLUMN render_job_id DROP NOT NULL;
```

**Test update:** `webapp/src/test/db/schema.test.ts` — no change needed; the column still exists, just nullable now.

---

### Phase 2: Public Origin Helper

**New function** in `webapp/src/app/api/share/route.ts` (or extract to `webapp/src/lib/share.ts`):

```ts
function resolvePublicOrigin(request: NextRequest): string | null {
  const envUrl = process.env.NEXT_PUBLIC_BASE_URL;
  if (envUrl) {
    try {
      const u = new URL(envUrl);
      if (u.origin) return u.origin;
    } catch {}
  }
  if (request.nextUrl?.origin) return request.nextUrl.origin;
  try {
    return new URL(request.url).origin;
  } catch {}
  return null;
}
```

Usage in POST and GET handlers:

```ts
const origin = resolvePublicOrigin(request);
if (!origin) {
  return NextResponse.json({ error: "Cannot determine public origin" }, { status: 500 });
}
const shareUrl = `${origin}/share/${token}`;
```

This replaces the current pattern:

```ts
const baseUrl = process.env.NEXT_PUBLIC_BASE_URL ?? "";
const shareUrl = `${baseUrl}/share/${token}`;
```

---

### Phase 3: DB Query — `getSongsetPublicView()`

**File:** `webapp/src/lib/db/songsets.ts` (new function)

**Types:**

```ts
export interface PublicSongsetItem {
  id: string;
  position: number;
  songTitle: string | null;
  composer: string | null;
  lyricist: string | null;
  albumName: string | null;
  songMusicalKey: string | null;
  durationSeconds: number | null;
  tempoBpm: number | null;
  recordingMusicalKey: string | null;
}

export interface SongsetPublicView {
  id: string;
  name: string;
  description: string | null;
  updatedAt: Date;
  totalDurationSeconds: number | null;
  renderState: RenderState;
  latestRenderJobId: string | null;
  lastCompletedRenderJobId: string | null;
  items: PublicSongsetItem[];
}
```

**Query logic:**

1. Fetch songset by id (return `null` if not found)
2. Fetch `songsetItems` ordered by `position`, joined with `songs` (on `songId`) and `recordings` (on `recordingHashPrefix` → `hashPrefix`)
3. Map each item to `PublicSongsetItem` — only whitelisted fields:
   - `id` (from `songsetItems.id`)
   - `position` (from `songsetItems.position`)
   - `songTitle` (from `songs.title`)
   - `composer` (from `songs.composer`)
   - `lyricist` (from `songs.lyricist`)
   - `albumName` (from `songs.albumName`)
   - `songMusicalKey` (from `songs.musicalKey`)
   - `durationSeconds` (from `recordings.durationSeconds`)
   - `tempoBpm` (from `recordings.tempoBpm`)
   - `recordingMusicalKey` (from `recordings.musicalKey`)
4. Compute `totalDurationSeconds` as sum of `recordings.durationSeconds` (filter out soft-deleted recordings)
5. Compute `renderState` via existing `computeRenderState()`
6. Return `SongsetPublicView`

**Explicitly excluded from the query result:**
- Owner user id or email
- Raw lyrics (`lyricsRaw`) or lyrics lines (`lyricsLines`)
- Source URLs (`sourceUrl`)
- R2 keys or raw R2 URLs
- Recording content hashes or hash prefixes
- User LRC overrides or lyric marks
- Transition parameters (`gapBeats`, `crossfadeEnabled`, `crossfadeDurationSeconds`, `keyShiftSemitones`, `tempoRatio`)
- Internal job error details

---

### Phase 4: API — `POST /api/share` and `GET /api/share`

**File:** `webapp/src/app/api/share/route.ts`

#### POST Handler Rewrite

**Request body schema:**

```ts
// Accept exactly one of:
{ songsetId: string; allowDownload?: boolean }
// OR:
{ renderJobId: string; allowDownload?: boolean }
// Reject both or neither with 400.
```

**`songsetId` path:**

1. Require authentication → 401
2. Verify songset exists and belongs to current user → 404 for missing or non-owned
3. Allow sharing regardless of render state
4. Look for reusable active share:
   ```sql
   WHERE songsetId = ? AND revokedAt IS NULL AND (expiresAt IS NULL OR expiresAt > now())
   ```
5. If found, return it immediately (no new token created)
6. Otherwise, enforce active-share limit (20 per user, excluding expired):
   ```sql
   SELECT COUNT(*) FROM songset_share
   WHERE created_by_user_id = ? AND revokedAt IS NULL
     AND (expiresAt IS NULL OR expiresAt > now())
   ```
7. If limit reached → 422
8. Create new share with `renderJobId: null`, token via `nanoid(24)`
9. Return 201:
   ```json
   { "token": "...", "shareUrl": "https://...", "songsetId": "...", "renderJobId": null, "allowDownload": false }
   ```

**`renderJobId` path (preserve existing behavior):**

1. Verify render job exists and belongs to user → 404
2. Verify job is completed → 409 if not
3. Continue associating share with both render job and its songset
4. Return 201 with `{ token, shareUrl, songsetId, renderJobId, allowDownload }`

**Active-share count update:**

Change from:

```sql
WHERE revokedAt IS NULL
```

To:

```sql
WHERE revokedAt IS NULL AND (expiresAt IS NULL OR expiresAt > now())
```

This ensures expired shares don't count against the limit.

#### GET Handler Update

1. Add `?songsetId=<id>` query param support alongside existing `?renderJobId=<id>`
2. When `songsetId` supplied: verify songset ownership before returning shares
3. When `renderJobId` supplied: verify render-job ownership (existing behavior)
4. Return only non-revoked, non-expired active shares:
   ```sql
   WHERE created_by_user_id = ? AND revokedAt IS NULL
     AND (expiresAt IS NULL OR expiresAt > now())
   ```
5. Include `shareUrl` using `resolvePublicOrigin()`
6. Return `renderJobId: string | null` in each share object

---

### Phase 5: API — `GET /api/share/[token]` (Public Endpoint Rewrite)

**File:** `webapp/src/app/api/share/[token]/route.ts`

#### New Response Shape

```json
{
  "token": "abc123",
  "shareType": "songset",
  "songset": {
    "id": "songset-1",
    "name": "Sunday Worship",
    "description": "Weekly service songs",
    "totalDurationSeconds": 1080,
    "renderState": "fresh",
    "latestRenderJobId": "job-456",
    "lastCompletedRenderJobId": "job-456"
  },
  "items": [
    {
      "id": "item-1",
      "position": 0,
      "songTitle": "Amazing Grace",
      "composer": "John Newton",
      "lyricist": null,
      "albumName": "Hymns Collection",
      "songMusicalKey": "G",
      "durationSeconds": 240,
      "tempoBpm": 80,
      "recordingMusicalKey": "G"
    }
  ],
  "playback": {
    "selectedRenderJobId": "job-456",
    "isStale": false,
    "staleStatus": null,
    "mp3Url": "https://r2.example.com/signed...",
    "mp4Url": "https://r2.example.com/signed...",
    "chaptersUrl": "https://r2.example.com/signed...",
    "mp3SizeBytes": 52428800,
    "mp4SizeBytes": null
  },
  "allowDownload": false,
  "createdAt": "2026-01-01T00:00:00Z",
  "expiresAt": null
}
```

#### Logic Flow

1. **Token validation:**
   - Look up share by token
   - 404 if not found
   - 410 if revoked (`revokedAt` is set)
   - 410 if expired (`expiresAt` is past)

2. **Determine share type:**
   - `share.renderJobId !== null` → `shareType: "renderJob"`
   - `share.renderJobId === null` → `shareType: "songset"`

3. **Fetch live songset data:**
   - Call `getSongsetPublicView(share.songsetId)`
   - If songset deleted (returns `null`): return 404 with generic message

4. **Playback selection:**
   - **Songset-level share:** prefer `songset.lastCompletedRenderJobId`
   - **Render-job share:** use `share.renderJobId`
   - Verify selected job exists, is completed, and belongs to the shared songset
   - If no valid playback job: `playback` URLs all `null`, `selectedRenderJobId: null`

5. **Stale detection:**
   - If `songset.updatedAt > selectedJob.completedAt`:
     - `playback.isStale = true`
     - `playback.staleStatus = "Playback may reflect an earlier render than the current song list"`
   - Otherwise: `isStale = false`, `staleStatus = null`

6. **Signed URLs:**
   - Generate only when a valid playback job exists
   - Use existing `r2Client.generateSignedUrl()` with 1-hour expiry
   - Degrade gracefully on R2 failure: set URLs to `null`, log server-side, do not expose provider details
   - If R2 is not configured: all URLs `null`

7. **Object sizes:**
   - Fetch via `r2Client.getObjectSize()` only when playback job is valid
   - Skip if R2 is not configured
   - Return `null` on error (don't fail the whole response)

8. **Headers:**
   - `Cache-Control: no-store, no-cache` (existing)

9. **Rate limiting:**
   - Add `// TODO: Add rate limiting by token and client IP for public share endpoint` comment

#### HTTP Status Behavior

| Condition | Status | Notes |
|-----------|--------|-------|
| Missing token | 404 | Generic message |
| Revoked token | 410 | "This share link has been revoked" |
| Expired token | 410 | "This share link has expired" |
| Songset deleted | 404 | Generic message, don't reveal private id |
| Valid token, no playback artifacts | 200 | Songset data with unavailable playback state |
| Valid token, playback available | 200 | Full response with signed URLs |
| Unexpected server failure | 500 | Generic message |

#### DELETE Handler

No changes needed. Existing behavior is correct:
- Auth required → 401
- Owner only → 404
- Already revoked → 409
- Success → sets `revokedAt`, returns `{ success: true }`

---

### Phase 6: UI — ShareDialog Rewrite

**File:** `webapp/src/components/share/ShareDialog.tsx`

#### New Props

```ts
export interface ShareDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  songsetId: string;
  songsetName: string;
  durationSeconds: number | null;
  renderJobId?: string;  // optional, for send-file tab
}
```

#### Changes

1. **On open:** Fetch `GET /api/share?songsetId=...` instead of `?renderJobId=...`

2. **Create share:** `POST /api/share { songsetId, allowDownload: false }`

3. **Formatted share message** in read-only textarea:

   ```text
   I shared a Stream of Worship songset with you:

   Sunday Worship
   Duration: 18 min

   Open this link to view the song list in read-only mode and start Worship Playback:
   https://example.com/share/abc123
   ```

4. **Duration formatting helper:**

   ```ts
   function formatShareDuration(seconds: number | null): string {
     if (!seconds) return "Not available";
     const totalMinutes = Math.round(seconds / 60);
     if (totalMinutes < 60) return `${totalMinutes} min`;
     const hours = Math.floor(totalMinutes / 60);
     const mins = totalMinutes % 60;
     return `${hours}h ${String(mins).padStart(2, "0")}m`;
   }
   ```

5. **Main copy button** writes full formatted message to clipboard (not just URL)

6. **Live-link warning text** near copy action:

   > "This link stays live. Future edits to this songset will be visible to anyone with the link until you revoke it."

7. **Send-file tab:** Hidden when no `renderJobId` prop provided (i.e., no completed render job available). When visible, keep existing WhatsApp/Line/Email behavior.

8. **Revoke:** Keep existing `DELETE /api/share/[token]` behavior

9. **States:** Loading, error, revoked, retry — keep existing patterns

---

### Phase 7: UI — Wire ShareDialog into Pages

#### 7a. `webapp/src/app/songsets/page.tsx`

**Current behavior:** `handleShare` navigates to `/songsets/${id}?share=true`

**New behavior:**

1. Add state:

   ```ts
   const [shareDialogOpen, setShareDialogOpen] = useState(false);
   const [shareTarget, setShareTarget] = useState<{
     id: string; name: string; durationSeconds: number | null;
   } | null>(null);
   ```

2. Replace `handleShare`:

   ```ts
   const handleShare = useCallback((id: string) => {
     const songset = songsets.find(s => s.id === id);
     if (songset) {
       setShareTarget({ id, name: songset.name, durationSeconds: songset.durationSeconds ?? null });
       setShareDialogOpen(true);
     }
   }, [songsets]);
   ```

3. Render `<ShareDialog>`:

   ```tsx
   {shareTarget && (
     <ShareDialog
       open={shareDialogOpen}
       onOpenChange={setShareDialogOpen}
       songsetId={shareTarget.id}
       songsetName={shareTarget.name}
       durationSeconds={shareTarget.durationSeconds}
     />
   )}
   ```

4. Update `SongsetList` → `SongsetRow` `onShare` callback to pass `name` and `durationSeconds` through (or use the parent's `songsets` data to look them up)

#### 7b. `webapp/src/app/songsets/[id]/page.tsx`

**Current behavior:** `handleShare` navigates to `/songsets/${songsetId}?share=true`

**New behavior:**

1. Add state: `const [shareDialogOpen, setShareDialogOpen] = useState(false);`

2. Replace `handleShare`:

   ```ts
   const handleShare = useCallback(() => {
     setShareDialogOpen(true);
   }, []);
   ```

3. Add `durationSeconds` to `ApiSongset` interface (backend already returns it):

   ```ts
   interface ApiSongset {
     // ... existing fields ...
     durationSeconds: number | null;  // add this
   }
   ```

4. Render `<ShareDialog>`:

   ```tsx
   <ShareDialog
     open={shareDialogOpen}
     onOpenChange={setShareDialogOpen}
     songsetId={songsetId}
     songsetName={songset.name}
     durationSeconds={songset.durationSeconds ?? null}
     renderJobId={songset.lastCompletedRenderJobId ?? undefined}
   />
   ```

5. **Backward compatibility** for `?share=true`:

   ```ts
   const searchParams = useSearchParams();
   const isNew = searchParams.get("new") === "true";
   const isShare = searchParams.get("share") === "true";

   useEffect(() => {
     if (isShare) {
       setShareDialogOpen(true);
       router.replace(`/songsets/${songsetId}`);
     }
   }, [isShare, songsetId, router]);
   ```

#### 7c. `webapp/src/app/songsets/[id]/play/page.tsx`

**Current behavior:** `handleShare` navigates to `/songsets/${songsetId}?share=true`

**New behavior:**

1. Add state: `const [shareDialogOpen, setShareDialogOpen] = useState(false);`

2. Replace `handleShare`:

   ```ts
   const handleShare = useCallback(() => {
     setShareDialogOpen(true);
   }, []);
   ```

3. Compute `durationSeconds` from items (same pattern as `PrePlayCard`):

   ```ts
   const totalDurationSeconds = items.reduce(
     (sum, item) => sum + (item.recording?.durationSeconds || 0), 0
   );
   ```

4. Render `<ShareDialog>`:

   ```tsx
   <ShareDialog
     open={shareDialogOpen}
     onOpenChange={setShareDialogOpen}
     songsetId={songset.id}
     songsetName={songset.name}
     durationSeconds={totalDurationSeconds || null}
     renderJobId={renderJob?.id}
   />
   ```

#### 7d. `SongsetRow` / `SongsetList` Callback Updates

**`SongsetRow.tsx`:** The `onShare` callback currently takes no args. The parent page looks up the songset by id. No change needed to the callback signature — the parent already has the songset data.

**`SongsetList.tsx`:** Passes `onShare` through. No change needed.

#### 7e. `SongsetEditor.tsx`

No prop changes needed. The component already has `songset.name` and computes `totalDurationSeconds` from items. The parent page's `handleShare` now opens the dialog instead of navigating.

#### 7f. `PrePlayCard.tsx`

**Current behavior:** Tries Web Share API first, then falls back to `onShare` prop which navigates.

**New behavior:** Remove Web Share API fallback. The `onShare` prop now opens the dialog. The parent page handles dialog rendering.

---

### Phase 8: UI — Public Share Page Rewrite

#### 8a. `webapp/src/app/share/[token]/page.tsx`

**Major rewrite** to show live read-only songset view.

**Data flow:**

1. Fetch `GET /api/share/[token]` → new response shape
2. Handle error states (revoked, expired, missing)

**Layout:**

1. **Header:** Stream of Worship branding + songset name
2. **Description:** Optional songset description
3. **Total duration** display
4. **Song list:** Ordered list with display-safe metadata per item:
   - Position number
   - Song title
   - Composer / Lyricist (if available)
   - Musical key
   - Duration
   - Tempo (BPM)
5. **Render/playback status** indicator
6. **Stale warning** (when `playback.isStale` is true):
   > "The song list above is current, but the playback may reflect an earlier render."
7. **Start Worship button:**
   - Enabled only when `playback.mp4Url` or `playback.mp3Url` is available
   - Video (mp4Url available) → navigates to `/share/[token]/play/projection`
   - Audio-only (mp3Url available, no mp4Url) → navigates to `/share/[token]/play/audio`
8. **Unavailable states:**

   | Condition | Display |
   |-----------|---------|
   | Unrendered | "This songset hasn't been rendered yet. Worship Playback is not available." |
   | Rendering | "This songset is currently being rendered. Check back soon." |
   | Failed | "Rendering failed. Worship Playback is not available." |
   | No artifacts | "No playback artifacts available yet." |
   | Revoked | "This share link has been revoked." |
   | Expired | "This share link has expired." |
   | Missing | "Share not found." |

9. **Never expose:** edit, render, duplicate, delete, reorder, or transition controls

#### 8b. `webapp/src/app/share/[token]/play/projection/page.tsx`

**Update for new response shape:**

- Extract `mp4Url` from `data.playback.mp4Url` instead of `data.mp4Url`
- Extract `songsetName` from `data.songset.name` instead of `data.songsetName`
- Keep re-fetching `/api/share/[token]` for fresh signed URLs
- Show stale context if practical (e.g., subtitle), but do not block playback

#### 8c. `webapp/src/app/share/[token]/play/audio/page.tsx`

**Update for new response shape:**

- Extract `mp3Url` from `data.playback.mp3Url` instead of `data.mp3Url`
- Extract `songsetName` from `data.songset.name` instead of `data.songsetName`
- Keep re-fetching for fresh signed URLs
- Reject playback when URL is missing

---

### Phase 9: Tests

#### 9a. `webapp/src/test/api/share/route.test.ts` — Updates

**New POST tests:**

| Test | Expected |
|------|----------|
| Unauthenticated songset share creation | 401 |
| Both `songsetId` and `renderJobId` | 400 |
| Neither target | 400 |
| Non-owned songset | 404 |
| Owned songset share creation succeeds without completed render | 201 |
| Active songset share is reused for repeated creation | Returns existing token |
| Expired songset share is not reused | Creates new token |
| Expired shares don't count against active-share limit | 201 even with 20 expired shares |
| `renderJobId` path still works | 201 (existing test) |

**New GET tests:**

| Test | Expected |
|------|----------|
| Listing supports `?songsetId=` | Returns filtered shares |
| Listing omits expired shares | Not in response |
| Listing omits revoked shares | Not in response |
| `?renderJobId=` still works | Existing test |

**Mock updates needed:**

- Add `mockFindFirstSongset` for songset ownership verification
- Add `mockFindFirstShare` for active-share reuse lookup
- Update `mockSelect` to handle the new active-share count query (with expired exclusion)

#### 9b. `webapp/src/test/api/share/token.test.ts` — Updates

**New GET tests:**

| Test | Expected |
|------|----------|
| Public token fetch returns live songset details and total duration | 200 with `songset` and `items` |
| Public token fetch exposes only whitelisted metadata | No `ownerId`, `lyricsRaw`, `r2Keys`, etc. |
| Public token fetch does not expose sensitive fields | Verify absent: `sourceUrl`, `hashPrefix`, `contentHash`, `lyricsRaw`, `lyricsLines`, transition params |
| Public token fetch returns playback URLs when artifacts exist | `playback.mp3Url` / `playback.mp4Url` present |
| Public token fetch returns songset data with unavailable playback when artifacts don't exist | 200, `playback` URLs null |
| Public token fetch flags stale playback | `playback.isStale === true` |
| Public token fetch handles revoked | 410 |
| Public token fetch handles expired | 410 |
| Public token fetch handles missing songset | 404 |
| Public token fetch handles unrendered songset | 200, no playback |
| Public token fetch handles rendering songset | 200, no playback |
| Public token fetch handles failed songset | 200, no playback |
| Public token fetch uses no-cache headers | `Cache-Control: no-store` |
| Songset-level share with `renderJobId: null` | 200, `shareType: "songset"` |
| Render-job-level share | 200, `shareType: "renderJob"` |

**Mock updates needed:**

- Add `mockGetSongsetPublicView` for the new query function
- Update response shape assertions for nested `songset`, `items`, `playback` objects

**DELETE tests:** No changes needed.

#### 9c. `webapp/src/test/components/share/ShareDialog.test.tsx` — Updates

**Updated `renderDialog` defaults:**

```ts
const defaultProps = {
  open: true,
  onOpenChange: vi.fn(),
  songsetId: "songset-1",
  songsetName: "Sunday Worship",
  durationSeconds: 1080,
  ...props,
};
```

**New/updated tests:**

| Test | Expected |
|------|----------|
| Dialog opens with `songsetId` prop | Fetches `GET /api/share?songsetId=songset-1` |
| Formatted message includes name, duration, explanation, and URL | Textarea contains all parts |
| Copy button writes full formatted message | `navigator.clipboard.writeText` called with full message |
| Live-link warning text is shown | Warning text visible |
| Revoke clears active share state | After revoke, create button reappears |
| Send-file tab hidden when no `renderJobId` prop | Tab not rendered |
| Send-file tab visible when `renderJobId` prop provided | Tab rendered |
| Duration formatting: under 60 min | Shows "18 min" |
| Duration formatting: 60+ min | Shows "1h 05m" |
| Duration formatting: null | Shows "Not available" |

#### 9d. New: Public Share Page Component Tests

**New file:** `webapp/src/test/components/share/PublicSharePage.test.tsx`

| Test | Expected |
|------|----------|
| Renders read-only song list | Song titles visible |
| Shows songset name and description | Visible |
| Shows total duration | Visible |
| Enables Start Worship only when playback URLs exist | Button enabled/disabled |
| Shows stale playback warning when `playback.isStale` | Warning visible |
| Does not render edit/render/duplicate/delete/reorder/transition controls | Absent |
| Shows unavailable state for unrendered songset | Message visible |
| Shows unavailable state for rendering songset | Message visible |
| Shows unavailable state for failed songset | Message visible |
| Shows revoked state | 410 message visible |
| Shows expired state | 410 message visible |

#### 9e. `webapp/src/test/db/schema.test.ts`

No changes needed. The `render_job_id` column still exists; it's just nullable now.

---

### Phase 10: Cleanup & Validation

1. **Remove `?share=true` navigation** from all call sites:
   - `webapp/src/app/songsets/page.tsx` — remove `window.location.href = ...?share=true`
   - `webapp/src/app/songsets/[id]/page.tsx` — remove `router.push(...?share=true)`
   - `webapp/src/app/songsets/[id]/play/page.tsx` — remove `router.push(...?share=true)`

2. **Add backward compat** in `songsets/[id]/page.tsx` for `?share=true`:
   - Read `searchParams.get("share")`, if `"true"`: open dialog + `router.replace()` to clean URL

3. **Remove Web Share API fallback** from `PrePlayCard.tsx` `handleShare`

4. **Run validation commands:**

   ```bash
   cd webapp && pnpm test
   cd webapp && pnpm lint
   ```

5. **Run from repo root:**

   ```bash
   graphify update .
   ```

6. **Push:**

   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```

---

## 3. Files Modified (Estimated)

| File | Change Type | Phase |
|------|-------------|-------|
| `webapp/src/db/schema.ts` | Edit: `renderJobId` nullable | 1 |
| `webapp/drizzle/` | New: migration file | 1 |
| `webapp/src/app/api/share/route.ts` | Major rewrite | 2, 4 |
| `webapp/src/app/api/share/[token]/route.ts` | Major rewrite | 5 |
| `webapp/src/lib/db/songsets.ts` | Add: `getSongsetPublicView()` + types | 3 |
| `webapp/src/components/share/ShareDialog.tsx` | Major rewrite | 6 |
| `webapp/src/app/songsets/page.tsx` | Edit: dialog state | 7 |
| `webapp/src/app/songsets/[id]/page.tsx` | Edit: dialog state + backward compat | 7 |
| `webapp/src/app/songsets/[id]/play/page.tsx` | Edit: dialog state | 7 |
| `webapp/src/components/songset/SongsetRow.tsx` | Minor: onShare callback | 7 |
| `webapp/src/components/songset/SongsetList.tsx` | Minor: pass through share data | 7 |
| `webapp/src/components/play/PrePlayCard.tsx` | Edit: remove Web Share fallback | 7 |
| `webapp/src/app/share/[token]/page.tsx` | Major rewrite | 8 |
| `webapp/src/app/share/[token]/play/projection/page.tsx` | Update: new response shape | 8 |
| `webapp/src/app/share/[token]/play/audio/page.tsx` | Update: new response shape | 8 |
| `webapp/src/test/api/share/route.test.ts` | Major update | 9 |
| `webapp/src/test/api/share/token.test.ts` | Major update | 9 |
| `webapp/src/test/components/share/ShareDialog.test.tsx` | Major update | 9 |
| `webapp/src/test/components/share/PublicSharePage.test.tsx` | New file | 9 |

**Total: ~19 files, ~8 major rewrites, 1 new migration, 1 new test file**

---

## 4. Implementation Order

The phases should be implemented in order because of dependencies:

1. **Phase 1** (schema) — must come first; other phases depend on nullable `renderJobId`
2. **Phase 2** (public origin helper) — small, needed by Phase 4
3. **Phase 3** (DB query) — needed by Phase 5
4. **Phase 4** (POST/GET API) — depends on Phase 1 schema change
5. **Phase 5** (public GET API) — depends on Phase 3 query function
6. **Phase 6** (ShareDialog) — depends on Phase 4 API
7. **Phase 7** (wire into pages) — depends on Phase 6 dialog
8. **Phase 8** (public share page) — depends on Phase 5 API
9. **Phase 9** (tests) — can be written alongside each phase or after
10. **Phase 10** (cleanup) — last

Phases 6-8 can be parallelized since they depend on different API changes.

---

## 5. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Migration breaks existing shares with `render_job_id` | `DROP NOT NULL` is backward-compatible; existing rows keep their values |
| Public origin resolution fails in some deployments | Return 500 rather than producing broken URLs; log the failure |
| R2 signed URL generation fails on public endpoint | Degrade to `playback` unavailable; log server-side without exposing provider details |
| Stale detection false positives (name/description edits) | Acceptable per spec: "live links should be treated as public disclosure of current songset display data until revoked" |
| Public endpoint abuse without rate limiting | Add TODO comment; rate limiting is a follow-up task |
| `ShareDialog` rewrite breaks existing render-job share flow | Keep `renderJobId` path in POST handler; test both paths |
| Backward compat with `?share=true` | Handle in `songsets/[id]/page.tsx` with `useSearchParams` + `router.replace` |
