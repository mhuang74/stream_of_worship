# Songset Sharing: Read-Only Link and Worship Playback v2

**Date:** 2026-06-06
**Status:** Draft
**Supersedes:** `specs/songset-sharing-readonly-playback.md`

---

## 1. Summary

Add songset-level sharing from the Songset KAB menu. A signed-in owner can create or reuse a public link for a songset, copy a readable share message, and revoke the link later. Anyone with the URL can view the songset in read-only mode without login and start Worship Playback when completed playback artifacts are available.

This v2 plan keeps the original live-link goal and adds explicit UX, data-loss, data-security, and operational guardrails:

- shared songset links are live until revoked
- public metadata is limited to a display-safe whitelist
- stale playback remains available but is clearly labeled
- expired shares are not reused or counted as active
- public token fetches avoid unnecessary R2 cost and should be rate limited
- generated share URLs must use a reliable public origin

## 2. Product Behavior

### Owner Share Flow

- The Share action in songset menus opens a dialog in place instead of navigating to `/songsets/[id]?share=true`.
- The dialog creates or reuses a songset-level public token for the current owner.
- The primary copy action copies a full readable message, not only the URL.
- The dialog clearly states that the link stays live and future songset edits are visible to anyone with the link until the link is revoked.
- Revoke remains available from the dialog and immediately prevents future public API access for that token.
- If `/songsets/[id]?share=true` is visited for backward compatibility, open the dialog and replace the URL with `/songsets/[id]`.

Recommended share message:

```text
I shared a Stream of Worship songset with you:

Sunday Worship
Duration: 18 min

Open this link to view the song list in read-only mode and start Worship Playback:
https://example.com/share/abc123
```

### Public Recipient Flow

- `/share/[token]` opens without authentication.
- The page shows Stream of Worship branding, songset name, optional description, total duration, ordered song list, and render/playback status.
- The page never exposes edit, render, duplicate, delete, reorder, transition-edit, owner, or admin controls.
- `Start Worship` appears only when MP4 or MP3 playback URLs are available.
- Video playback routes to `/share/[token]/play/projection`.
- Audio-only playback routes to `/share/[token]/play/audio`.
- If the songset is unrendered, rendering, failed, revoked, expired, missing, or has no artifacts, show a clear read-only unavailable state.
- If playback artifacts are from an older completed render than the live songset state, allow playback but show a clear stale warning: the visible song list is current, while playback may reflect an earlier render.

## 3. Data and API Changes

### Schema and Migration

Update `webapp/src/db/schema.ts` and add a Drizzle migration:

- Change `songset_share.render_job_id` from non-null to nullable.
- Existing render-job shares keep their `render_job_id`.
- New songset-level shares may store `render_job_id: null`.
- TypeScript types and API response shapes must treat `renderJobId` as `string | null` wherever songset-level shares are possible.

No snapshot tables are required for this version because shared songset links are intentionally live.

### `POST /api/share`

Accept exactly one share target:

- `{ songsetId, allowDownload?: boolean }`
- `{ renderJobId, allowDownload?: boolean }`

Reject requests that provide both or neither target with `400`.

For `songsetId`:

- require authentication
- verify the songset belongs to the current user
- return `404` for missing or non-owned songsets
- allow sharing regardless of render state
- reuse the first active share for that `songsetId`
- create a new token with `renderJobId: null` when no reusable share exists
- enforce the active-share limit before creating a new token
- return `{ token, shareUrl, songsetId, renderJobId: null, allowDownload }`

For `renderJobId`:

- preserve existing completed-render-job behavior
- verify the render job belongs to the current user
- keep returning `409` for non-completed render jobs
- continue associating the share with both the render job and its songset

Active-share definition:

- `revokedAt IS NULL`
- and `expiresAt IS NULL OR expiresAt > now()`

Expired shares must not be reused and must not count against the active-share limit.

### `GET /api/share`

Support authenticated listing with:

- `?songsetId=<id>`
- `?renderJobId=<id>`

Rules:

- list only shares created by the authenticated user
- return only non-revoked, non-expired active shares
- when `songsetId` is supplied, verify ownership before returning shares
- when `renderJobId` is supplied, verify render-job ownership before returning shares
- include `shareUrl` using the same public-origin logic as creation

### `GET /api/share/[token]`

Public endpoint. It must validate the token and return a live read-only songset response for both songset-level and render-job shares.

Response fields:

- `token`
- `shareType`: `"songset"` or `"renderJob"`
- `songset`: id, name, description, total duration seconds, render state, latest render job id, last completed render job id
- `items`: ordered display-safe song items
- `playback`: selected render job id, freshness state, stale warning flag, MP3/MP4/chapters URLs when available, artifact sizes when available
- `allowDownload`
- `createdAt`, `expiresAt`

Display-safe item whitelist:

- item id may be omitted unless needed as a React key
- position
- song title
- song composer, lyricist, album name, musical key
- recording duration seconds, tempo BPM, musical key

Do not expose through the public API:

- owner user id or email
- raw lyrics or lyrics lines
- source URLs
- R2 keys or raw R2 URLs
- recording content hashes or hash prefixes
- user LRC overrides or lyric marks
- transition parameters unless a later public UX explicitly needs them
- internal job error details that may contain infrastructure information

Playback URL selection:

- for songset-level shares, prefer `songset.lastCompletedRenderJobId`
- for render-job shares, use `share.renderJobId`
- only return playback URLs when the selected job exists, is completed, belongs to the shared songset, and has artifacts
- if the selected completed job is older than the live songset state, set `playback.isStale = true` and include a public-safe stale status
- if artifacts are unavailable, return the songset data with `playback` URLs as `null` rather than failing the whole page

HTTP status behavior:

- missing token: `404`
- revoked token: `410`
- expired token: `410`
- valid token with no playback artifacts: `200` with unavailable playback state
- unexpected server failure: `500`

Headers:

- keep public share responses no-store/no-cache
- do not put signed media URLs in route params, query params, logs, or analytics events

Operational guardrails:

- generate signed media URLs only when the public response needs playback availability
- avoid calling R2 object-size APIs on every public token fetch when sizes are already known or can be omitted
- add lightweight public endpoint rate limiting by token and client IP if the project already has a rate-limit helper; otherwise add a follow-up TODO in code and tests documenting the risk
- signed URL failures should degrade to `playback` unavailable and log server-side without exposing provider details

### Public Origin for Share URLs

Share URL generation must produce an absolute URL.

Use this precedence:

1. `NEXT_PUBLIC_BASE_URL` when configured and absolute
2. request origin from trusted forwarded headers in the deployed Next.js environment
3. request URL origin as a local/dev fallback

If no absolute origin can be determined, return a server error rather than copying a broken relative link.

## 4. UI Implementation Changes

### Share Dialog

Update `webapp/src/components/share/ShareDialog.tsx`:

- accept `songsetId`, `songsetName`, `durationSeconds`, and optional `renderJobId`
- load existing share with `/api/share?songsetId=...`
- create share with `POST /api/share { songsetId, allowDownload: false }`
- show the formatted share message in a multi-line read-only text area
- make the main copy button write the full formatted message
- optionally show a secondary bare URL field
- show loading, error, revoked, and retry states
- show live-link warning text near the copy action
- keep revoke support through `DELETE /api/share/[token]`
- show send-file/artifact affordances only when a completed render job is available

Duration formatting:

- under 60 minutes: `18 min`
- 60 minutes and above: `1h 05m`
- if duration is unknown or zero, show `Duration: Not available`

### KAB and Call Sites

Update share handlers in:

- `webapp/src/app/songsets/page.tsx`
- `webapp/src/app/songsets/[id]/page.tsx`
- `webapp/src/app/songsets/[id]/play/page.tsx`
- related `SongsetList`, `SongsetRow`, `SongsetEditor`, and `PrePlayCard` call sites as needed

Behavior:

- open the dialog in place
- do not navigate to `/songsets/[id]?share=true`
- keep Share available for unrendered songsets
- keep Play/Download controls gated by render artifact availability

### Public Read-Only Page

Update `webapp/src/app/share/[token]/page.tsx`:

- render the live read-only songset view from the public token API
- show only whitelisted display metadata
- show unavailable states for unrendered, rendering, failed, no-artifact, revoked, expired, and missing shares
- show stale playback warning when `playback.isStale` is true
- enable `Start Worship` only when MP4 or MP3 URL availability is reported
- prefer video playback when MP4 is available, otherwise audio-only playback

Update playback pages:

- keep re-fetching `/api/share/[token]` to mint fresh signed URLs
- reject playback when the relevant URL is missing
- show stale context if practical, but do not block playback solely because it is stale

## 5. Runtime and Data Safety Considerations

- Deleting a songset may cascade-delete shares. Public links should then return `404` or `410`; choose one behavior in implementation and test it consistently.
- Revocation cannot invalidate already minted signed media URLs until those URLs expire. Keep signed URL TTL short, currently 1 hour or less.
- The share dialog must not imply revocation stops already downloaded files or already minted signed URLs immediately.
- Live links should be treated as public disclosure of current songset display data until revoked.
- Public error messages should be generic and must not reveal whether a private songset id exists.
- Public pages should not render raw HTML from songset names, descriptions, or song metadata.

## 6. Tests

### API Tests

Update or add tests under `webapp/src/test/api/share/`:

- unauthenticated songset share creation returns `401`
- request with both `songsetId` and `renderJobId` returns `400`
- request with neither target returns `400`
- non-owned songset share creation returns `404`
- owned songset share creation succeeds without completed render artifacts
- active songset share is reused for repeated creation
- expired songset share is not reused
- expired shares do not count against active-share limit
- listing supports `songsetId`
- listing omits expired and revoked shares
- render-job sharing remains compatible
- public token fetch returns live songset details and total duration
- public token fetch exposes only whitelisted metadata
- public token fetch does not expose owner ids, raw lyrics, R2 keys, hash prefixes, source URLs, or transition parameters
- public token fetch returns playback URLs when artifacts exist
- public token fetch returns songset data with unavailable playback when artifacts do not exist
- public token fetch flags stale playback when live songset state is newer than the selected completed render
- public token fetch handles revoked, expired, missing, unrendered, rendering, failed, and no-artifact states
- public token fetch uses no-cache headers

### Component Tests

Update or add tests under `webapp/src/test/components/`:

- Songset KAB Share opens the dialog and does not navigate
- Share is available for unrendered songsets
- Share dialog displays formatted message with name, duration, explanation, and URL
- Copy button writes the full formatted message to clipboard
- Dialog shows live-link warning text
- Revoke clears the active share state
- Send-file affordances are hidden when no completed render job exists
- Public share page renders read-only song list
- Public share page enables `Start Worship` only when playback URLs exist
- Public share page shows stale playback warning when playback is stale
- Public share page does not render edit, render, duplicate, delete, reorder, or transition controls

### Validation Commands

Run from `webapp/`:

```bash
pnpm test
pnpm lint
```

After implementation modifies code, run from the repo root:

```bash
graphify update .
```

## 7. Assumptions and Defaults

- Shared songset links are live, not snapshots.
- Future songset edits are visible through existing public links until revoked.
- Public metadata uses the basic display whitelist in this plan.
- Stale playback remains available with a warning.
- Existing render-job share links remain valid.
- The public share URL grants read/play access only.
- The primary clipboard content is the formatted share message, not just the URL.
- `allowDownload` remains false for new songset shares unless a future spec defines public download UX.
