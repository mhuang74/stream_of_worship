# Songset Sharing: Read-Only Link and Worship Playback

**Date:** 2026-06-06
**Status:** Draft

---

## 1. Problem

The Songset KAB menu currently has a **Share** action, but selecting it routes the user back to the Songset Edit screen (`/songsets/[id]?share=true`). This does not match user expectations for sharing:

- the user is not presented with a shareable URL
- there is no copy-ready text for email or chat
- recipients cannot open the songset without authentication
- recipients cannot start Worship Playback from the shared songset

The app already has render-job sharing through `/share/[token]`, but that flow is artifact-first and tied to a completed render job. Songset sharing needs to work at the songset level and remain useful even before playback artifacts exist.

## 2. Goals

Add songset-level sharing from the Songset KAB menu:

1. Open a share dialog instead of navigating to the editor URL.
2. Generate or reuse a public URL such as `/share/{token}`.
3. Present a copy-ready message that users can paste into email/chat.
4. Let anyone with the URL open the songset in read-only mode without login.
5. Let recipients start Worship Playback when completed playback artifacts are available.
6. Keep the shared songset live: recipients see the current songset after later edits.

## 3. User-Facing Share Message

The share dialog must make the primary copy action copy a full readable message, not only the bare URL.

The message should be easy to paste into email or chat and must include:

- the songset name
- total duration, formatted as `18 min` or `1h 05m`
- a simple explanation of what the recipient can do
- the public URL on its own line

Recommended message format:

```text
I shared a Stream of Worship songset with you:

Sunday Worship
Duration: 18 min

Open this link to view the song list in read-only mode and start Worship Playback:
https://example.com/share/abc123
```

The dialog should show this message in a multi-line read-only text area with a clear copy button. A secondary bare URL field is optional, but the main copy button must copy the full formatted message.

## 4. Data and API Changes

### Step 1: Allow songset-level share records

**File:** `webapp/src/db/schema.ts`

Change `songset_share.render_job_id` so it can be nullable. Existing render-job shares keep using this field; new songset-level shares may leave it null.

Add a Drizzle migration for the nullability change.

### Step 2: Extend share creation/listing API

**File:** `webapp/src/app/api/share/route.ts`

Update `POST /api/share` to accept either:

- `{ songsetId, allowDownload?: boolean }`
- `{ renderJobId, allowDownload?: boolean }`

For `songsetId`:

- require authentication
- verify the songset belongs to the current user
- allow sharing regardless of render state
- reuse the first active non-revoked share for that `songsetId` when available
- otherwise create a new token with `renderJobId: null`
- enforce the existing active-share limit
- return `{ token, shareUrl, songsetId, renderJobId: null, allowDownload }`

For `renderJobId`, preserve existing behavior for completed render-job shares.

Update `GET /api/share` to support:

- `?songsetId=<id>`
- `?renderJobId=<id>`

The endpoint should still list only shares owned by the authenticated user.

### Step 3: Extend public token API

**File:** `webapp/src/app/api/share/[token]/route.ts`

Update `GET /api/share/[token]` so it returns live songset data for both songset-level and render-job shares:

- token
- songset id/name/description
- ordered song items with read-only song and recording metadata
- total duration seconds
- render state
- latest and last-completed render job ids
- signed MP3/MP4/chapters URLs when completed artifacts are available
- artifact sizes when available
- created/expiry/revoked state handling

Playback URL selection:

- prefer the songset's `lastCompletedRenderJobId` for songset-level shares
- use `renderJobId` for existing render-job shares
- return no playback URLs when artifacts are unavailable

Keep response headers no-cache for public share data.

## 5. UI Changes

### Step 1: Refactor the share dialog for songsets

**File:** `webapp/src/components/share/ShareDialog.tsx`

Support songset-level props:

- `songsetId`
- `songsetName`
- `durationSeconds`
- optional `renderJobId`

Behavior:

- load existing share with `/api/share?songsetId=...`
- create share with `POST /api/share { songsetId, allowDownload: false }`
- display the formatted share message once a URL exists
- copy the full formatted message to clipboard
- show loading, error, and revoked states
- keep revoke support through `DELETE /api/share/[token]`
- keep send-file/artifact affordances only when a completed render job is available

### Step 2: Replace KAB navigation with dialog opening

Update the Share handlers in:

- `webapp/src/app/songsets/page.tsx`
- `webapp/src/app/songsets/[id]/page.tsx`
- `webapp/src/app/songsets/[id]/play/page.tsx`
- related `SongsetList`, `SongsetRow`, `SongsetEditor`, and `PrePlayCard` call sites as needed

The Share action should open the dialog in place. It should not navigate to `/songsets/[id]?share=true`.

For backward compatibility, if `/songsets/[id]?share=true` is visited, open the share dialog and then replace the URL with `/songsets/[id]`.

### Step 3: Build the public read-only songset page

**File:** `webapp/src/app/share/[token]/page.tsx`

Enhance the page from a simple shared media page into a read-only songset view:

- show Stream of Worship header
- show songset name and optional description
- show total duration
- show ordered song list with duration and basic metadata
- show render/playback status
- show `Start Worship` when MP4 or MP3 playback is available
- route video playback to `/share/[token]/play/projection`
- route audio-only playback to `/share/[token]/play/audio`
- show a clear read-only unavailable state when the songset is unrendered, rendering, failed, or has no artifacts

Do not add public edit, render, duplicate, delete, reorder, or transition-edit controls.

## 6. Tests

### API tests

Update or add tests under `webapp/src/test/api/share/`:

- unauthenticated songset share creation returns 401
- non-owned songset share creation returns 404
- owned songset share creation succeeds without completed render artifacts
- active songset share is reused for repeated creation
- listing supports `songsetId`
- render-job sharing remains compatible
- public token fetch returns live songset details and total duration
- public token fetch returns playback URLs when artifacts exist
- public token fetch handles revoked, expired, missing, unrendered, and no-artifact states

### Component tests

Update or add tests under `webapp/src/test/components/`:

- Songset KAB Share opens the dialog and does not navigate
- Share dialog displays the formatted message with name, duration, explanation, and URL
- Copy button writes the full formatted message to clipboard
- Revoke clears the active share state
- Public share page renders read-only song list
- Public share page enables `Start Worship` only when playback URLs exist

### Validation commands

Run from `webapp/`:

```bash
pnpm test
pnpm lint
```

After implementation modifies code, run from the repo root:

```bash
graphify update .
```

## 7. Assumptions

- Shared songset links are live, not snapshots.
- Sharing is allowed before render completion.
- Existing render-job share links remain valid.
- The public share URL grants read/play access only.
- The primary clipboard content is the formatted share message, not just the URL.
