# Stream of Worship — Web App UI Requirements v3

**Source:** Revised from v2 spec, TUI codebase (`src/stream_of_worship/app/`), and DB schema (`src/stream_of_worship/admin/db/schema.py`)
**Target:** Next.js (App Router) hosted on Vercel (Pro plan, Fluid Compute enabled)
**Date:** 2026-05-10
**Supersedes:** `specs/webapp_ui_requirements_v2.md`

---

## 1. Overview & Goals

The Stream of Worship Web App is a browser-based tool for worship leaders and media volunteers to browse a song catalog, assemble songsets, preview audio, review lyric timing, and export a finished MP3 + MP4 lyrics video for a worship service.

### Primary persona

A worship leader or media volunteer sitting at a desk, kitchen table, or couch with a tablet, preparing materials for an upcoming service. Their workflow:
1. Pick songs from the catalog and assemble a setlist.
2. Tune transitions (gap, crossfade, key shift, tempo) between songs.
3. Review auto-generated lyrics for each song — verify text accuracy, word sequence, and time-alignment; correct text or timestamps directly when wrong.
4. Export an MP3 (full mix) + MP4 (lyrics video).

The export is the primary output and the reason everything else exists.

**This is not a stage tool.** The app is for video production preparation, not performance. There is no stage display mode, no "zero-chrome" view, and no design for stage lighting conditions. (A stage projection feature, if ever needed, is a separate product.)

### Design philosophy

- **Tablet-first, desktop-equal.** Primary device is a 10–11" tablet; desktop is a first-class environment, not a secondary one. Mobile phone is an accommodation.
- **Production tool aesthetic.** Information-dense where it matters (lyrics review, transition tuning), clean where it doesn't (songset list, settings). Think lightweight DAW or video editor — not a consumer reading app.
- **Reviewability over glanceability.** Users are doing focused QA: reading lyrics, scrubbing audio, marking issues. Provide context, not just the current moment.
- **Sheets over routes.** Sub-features (browse, transition editing, lyrics review) are overlay layers within the editor — not separate pages. Fewer navigations = less context loss.
- **Touch-first interactions.** Every action has a tap/swipe/gesture trigger. Keyboard shortcuts are progressive enhancement for desktop only.

### Key goals

- **Multi-device access** — responsive web app replaces the desktop-only TUI
- **Multi-user** — each user has their own songsets; catalog is shared, managed by admins
- **Full feature parity** — all TUI capabilities (playback, transition preview, export)
- **Lyric QA** — correct lyric text and timing in-app; corrections save as a per-user override and are used in that user's exports

### Out of scope for v1

- Admin catalog management (stays in `sow-admin` CLI)
- Song analysis / LRC generation (stays in the Analysis Service, triggered by Admin CLI)
- Songset JSON import/export (defer to v2)
- Submit-for-approval workflow (admin merges user overrides offline via `sow-admin`)
- Stage projection / performance display mode

---

## 2. Architecture

### 2.1 Decisions

| Concern | Decision |
|---|---|
| Frontend | Next.js (App Router) + TypeScript |
| Styling | Tailwind CSS, mobile-first |
| UI components | shadcn/ui (headless, accessible) |
| Auth | Better Auth with Neon Postgres adapter |
| Database | Neon Postgres — single source of truth |
| ORM | Drizzle + `@neondatabase/serverless` driver |
| Backend logic | Next.js Route Handlers (no separate Python API) |
| Blob storage | Cloudflare R2 (audio files, LRC, export artifacts) |
| Audio export rendering | Node FFmpeg (`fluent-ffmpeg` + `ffmpeg-static`) within a Vercel Function |
| Video export rendering | Node canvas (`skia-canvas` or `node-canvas`) + Node FFmpeg, same function |
| Real-time progress | Server-Sent Events (SSE) from the long-running export Route Handler |
| Deployment | Vercel Pro plan, Fluid Compute enabled (required for `maxDuration: 800`) |

The Next.js app is a full backend. **There is no Python API between the browser and the database.** The proposed "Python FastAPI backend" from v2 is not introduced.

### 2.2 System boundary

The web app has **zero runtime dependencies on the Analysis Service**. The Analysis Service supports only the offline admin/catalog pipeline.

```
                   ┌──── Web App runtime boundary ────┐
                   │                                  │
┌─────────────┐    │   ┌────────────────────────────┐ │
│   Browser   │ HTTPS  │  Next.js (Vercel, Pro+,    │ │
│             ├───>│   │           Fluid Compute)   │ │
│             │SSE │   │  ─ Better Auth             │ │
│             │<───│   │  ─ App Router routes       │ │
└─────────────┘    │   │  ─ Drizzle ORM → Neon      │ │
                   │   │  ─ R2 SDK (signed URLs)    │ │
                   │   │  ─ Node FFmpeg + canvas    │ │
                   │   │      → audio MP3 render    │ │
                   │   │      → video MP4 render    │ │
                   │   └────┬───────────────────┬───┘ │
                   │        │ pg (TLS)          │ S3  │
                   │        v                   v API │
                   │   ┌────────────┐      ┌─────────┐│
                   │   │   Neon     │      │   R2    ││
                   │   │  Postgres  │      │ (blobs) ││
                   │   └─────^──────┘      └────^────┘│
                   └─────────┼──────────────────┼────┘
                             │ pg               │ S3 API
                             │                  │
                   ┌─────────┴──────────────────┴────────┐
                   │  Admin / Catalog pipeline           │
                   │  (offline, not user-facing)         │
                   │                                     │
                   │  ┌────────────┐   ┌──────────────┐  │
                   │  │ Admin CLI  │──>│   Analysis   │  │
                   │  │ (sow-admin)│   │   Service    │  │
                   │  └────────────┘   │  (Docker, ML)│  │
                   │                   │  ─ allinone  │  │
                   │                   │  ─ Demucs    │  │
                   │                   │  ─ Whisper   │  │
                   │                   └──────────────┘  │
                   └─────────────────────────────────────┘
```

### 2.3 Component responsibilities

| Component | Owns |
|---|---|
| **Next.js (Vercel)** | All user-facing UI and API; auth; all CRUD for users, songsets, items, settings, LRC overrides, lyric marks; catalog read access; signed-URL minting for R2 audio/LRC; audio MP3 rendering; video MP4 rendering; SSE export progress. |
| **Neon Postgres** | Single source of truth: catalog (songs, recordings), user data (songsets, items, settings), auth sessions, LRC overrides, lyric marks, render jobs. |
| **Cloudflare R2** | Blob store: original audio, stems, LRC files, rendered MP3/MP4 artifacts. |
| **Analysis Service (offline)** | Song analysis (allinone, Demucs) and LRC generation (Whisper). Triggered by Admin CLI only. Not in any web-app request path. |
| **Admin CLI (`sow-admin`)** | sop.org scraping, recording import, dispatching analysis jobs to Analysis Service, catalog admin. Direct Neon client (separate DB role). |

### 2.4 Export rendering constraints

Vercel Functions on the Pro plan with Fluid Compute support up to `maxDuration: 800` seconds (13 min), 4 GB / 2 vCPU. The `after()` Next.js primitive allows work to continue after the HTTP response is sent, still within that 800s budget.

Bundle size: `ffmpeg-static` (~80 MB) + `skia-canvas` (~50 MB) comfortably fit the 250 MB uncompressed Node bundle limit.

**Render budget:** A typical 4–6 song set (25–30 min output) at 720p H.264 ultrafast renders at ~3–4× realtime, completing in ~7–10 min — within the 800s cap.

Default export is **720p / ultrafast preset**. Users may opt up to **1080p / medium preset** with an estimated render time warning (for long sets this may exceed 800s; the UI surfaces this).

If a function times out mid-render, the job is retryable: the retry endpoint re-runs from scratch with the same `job_id` (output is deterministic).

### 2.5 Schema and migrations

- **Schema source of truth:** Drizzle schema in `apps/web/db/schema.ts`.
- **Migrations:** Drizzle Kit. The Admin CLI Python models must be kept in sync (CI drift check or generated types).
- **Catalog migration:** Existing catalog data lives in SQLite (TUI) and must be migrated to Neon before the web app goes live. This is a precondition, not a v1 deliverable.
- **New tables for v1:** `user_lrc_override(id, user_id, recording_content_hash, lrc_content, created_at, updated_at)` with unique `(user_id, recording_content_hash)`; `lyric_mark(id, user_id, recording_content_hash, timestamp_seconds, created_at)` with unique `(user_id, recording_content_hash, timestamp_seconds)`. Both FK to `recordings.content_hash`.

---

## 3. Authentication

### 3.1 Provider

Better Auth with Neon Postgres as the session/user store. OAuth providers TBD (Google recommended).

### 3.2 Protected routes

All app routes are protected. Unauthenticated users are redirected to `/login`.

```
/login              → Public: OAuth sign-in page
/                   → Protected: redirects to /songsets
/songsets           → Protected
/songsets/[id]      → Protected (owner check)
/songsets/[id]/export → Protected (owner check)
/settings           → Protected
```

### 3.3 User data isolation

Each user owns their own songsets. Ownership is enforced in Route Handlers. The catalog (songs, recordings) is shared read-only across all users. LRC overrides and marks are per-user per-recording (not shared). The catalog's official LRC is shared and read-only to user-side code.

---

## 4. Navigation & Routing

### 4.1 Route structure

```
/login                      Login page
/songsets                   Songset list (home)
/songsets/[id]              Songset editor  ← browse, transition, lyrics are sheets/overlays
/songsets/[id]/export       Export config + progress
/settings                   App settings + catalog sync
```

Browse, Transition editing, and Lyrics Review are overlay layers within `/songsets/[id]`, not separate routes. This eliminates context loss and lets the global audio player persist without remounting.

### 4.2 Navigation patterns

**Tablet/Mobile (<1024px):** Top app bar with page title and back arrow. Navigation flows as a stack via browser history.

**Desktop (≥1024px):** Same top app bar. Sheets become side panels (slide in from right). Song list renders as a table with column headers.

### 4.3 Navigation flow

```
/login
  └── /songsets (home, after auth)
        ├── /settings
        └── /songsets/[id]  (editor)
              ├── Browse Sheet (overlay, no URL change)
              ├── Transition Panel (inline expand, no URL change)
              ├── Lyrics Review (overlay, no URL change)
              └── /songsets/[id]/export
```

### 4.4 State passing

URL params for page-level state (export job ID: `?job=<id>`). Sheet/overlay state is local React state. React Query for server state caching.

---

## 5. Screen Requirements

### 5.1 Songset List — `/songsets`

**Purpose:** Landing screen. Shows the user's songsets. Create, open, or manage songsets.

#### Layout (mobile/tablet)

```
┌─────────────────────────────────┐
│  Stream of Worship    [avatar]  │  ← top app bar
├─────────────────────────────────┤
│  Sunday Morning Worship      >  │  ← row with chevron
│  5 songs · 23m 14s · 2 days ago │    name, count, duration, recency
│  ─────────────────────────────  │  ← hairline divider
│  Evening Set                 >  │
│  3 songs · 18m 02s · Today      │
│  ─────────────────────────────  │
│  (empty space)                  │
│                           [+]   │  ← FAB, bottom-right
└─────────────────────────────────┘
```

#### Layout (desktop)

Max-width 640px container, centered. FAB replaced by inline "+ New songset" button at top-right. No sidebar.

#### Songset row

Each row displays:
- Songset name (semibold)
- Song count + total duration + relative updated-at timestamp (muted, smaller)
- Tap/click → navigate to `/songsets/[id]`
- Long-press (mobile) or right-click (desktop) → context menu: **Rename**, **Duplicate**, **Export**, **Delete**

#### Actions

| Action | Trigger | Behavior |
|---|---|---|
| Open songset | Tap row | Navigate to `/songsets/[id]` |
| Create songset | FAB `+` / `+ New songset` | Creates with default name "New Songset", navigates to editor |
| Rename | Context menu → Rename | Inline text input replaces name; save on blur or Enter |
| Duplicate | Context menu → Duplicate | Creates copy with "Copy of …" prefix; refreshes list |
| Export | Context menu → Export | Navigate to `/songsets/[id]/export` |
| Delete | Context menu → Delete | Confirmation dialog, then delete + remove from list |

#### Empty state

Centered "No songsets yet" + "Create your first songset" button.

---

### 5.2 Songset Editor — `/songsets/[id]`

**Purpose:** Core editing screen. Manage the ordered list of songs, tune transitions, review lyrics, and initiate export.

#### Layout (mobile/tablet)

```
┌─────────────────────────────────┐
│  ←  Sunday Morning Worship  ··· │  ← back, inline-editable title, overflow
├─────────────────────────────────┤
│  ≡  1  How Great Is Our God  ♪  │  ← drag handle, position, title, lyrics chip
│        G major · 72 BPM · 4:32  │    metadata line (key includes mode)
│        2.0b gap                 │    transition summary
│        ──────────────────────── │
│        [Gap: ──○── 2.0]         │    ← inline expanded transition controls
│        [Crossfade: OFF]         │      (tap row to toggle)
│        [Key shift: 0]           │
│        [Tempo: 1.00×]           │
│        [More transition… ▸]     │
│  ─────────────────────────────  │
│  ≡  2  Cornerstone           ♪  │
│        E major · 68 BPM · 5:14  │
│        2.0b gap                 │
│        [Compatibility hints]    │    ← collapsed by default; tap row to expand
│          G→E: −3 semitones  [✓] │
│          72→68 BPM: −5.6%  [✓] │
│  ─────────────────────────────  │
│  ≡  3  Great Are You Lord    ♪  │
│        C major · 80 BPM · 3:45  │
│        (first song)             │    ← first song: special controls
│  ─────────────────────────────  │
│  ────────────────────────────   │
├─────────────────────────────────┤
│  [+ Add]     [▶ Play]           │  ← bottom action bar (Export moved to ···)
├─────────────────────────────────┤
│  How Great Is Our God           │  ← playback bar (when active)
│  ◀◀  01:23  ████░░░░  04:32  ▶▶ │
└─────────────────────────────────┘
```

#### Layout (desktop ≥1024px)

Song list renders as a table with columns: `#`, `Song`, `Key`, `BPM`, `Duration`, `Gap`, `Transition`. Hover on a row reveals inline icon buttons: `♪` (lyrics review), `↔` (transition detail panel), `×` (remove). Inline transition expand becomes a side panel that slides in from the right. Bottom action bar moves to a toolbar above the table.

#### Song row

| Field | Source | Notes |
|---|---|---|
| # | `position` | |
| Song title | `songTitle` | |
| Key | `displayKey` + `musicalMode` | e.g. "G major", "E minor" |
| BPM | `tempoBpm` | |
| Duration | `formattedDuration` | |
| Transition summary | `gapBeats` + crossfade + `keyShiftSemitones` + `tempoRatio` | |
| ⚠ low-confidence key | `keyConfidence < 0.7` | Warning icon beside key label |

Orphan items shown with a warning icon (⚠) and italicized title.

Marks badge: if the user has marked lines on this recording, a small `🔖 N` badge appears on the row. A `📝` chip indicates the user has a local LRC override.

#### Song row interactions (mobile/tablet)

- **Tap row body** → toggle inline transition controls open/closed
- **Tap `♪` chip** → open Lyrics Review overlay (§5.2c)
- **Swipe left** → reveal **Remove** button (destructive, red). Confirmation tap required.
- **Drag `≡` handle** → reorder; calls reorder API on drop

#### Song row interactions (desktop)

- **Click row** → toggle inline transition panel
- **Click `♪`** → open Lyrics Review
- **Click `×`** → remove song (with confirmation popover)
- **Drag handle** → reorder

#### Inline transition controls

Appear inside the row when expanded (replaces the separate Transition Sheet of v2):

| Control | Type | Default | Constraint |
|---|---|---|---|
| Gap (beats) | Slider + numeric | `2.0` | 0.0–16.0, step 0.5 |
| Crossfade enabled | Toggle | `false` | |
| Crossfade duration (s) | Slider | `4.0` | 1.0–30.0; disabled when crossfade off |
| Key shift (semitones) | Segmented pill | `0` | −6 to +6 |
| Tempo ratio | Slider + numeric | `1.00` | 0.85–1.15, step 0.01 |
| Compatibility hints | Non-blocking hint strip | — | Suggests key shift and tempo ratio based on adjacent songs; tap [Apply] to pre-fill |
| [Preview transition ▶] | Button | — | Plays transition audio in global player |
| [More transition… ▸] | Link | — | Opens full-detail sheet for edge cases |

**First-song controls** (position 1, no previous song):

| Control | Type | Default | Constraint |
|---|---|---|---|
| Intro fade-in (s) | Slider + numeric | `0.0` | 0.0–10.0, step 0.5 |
| Key shift (semitones) | Segmented pill | `0` | −6 to +6 |
| Tempo ratio | Slider + numeric | `1.00` | 0.85–1.15, step 0.01 |

**Auto-save:** controls save on change (no explicit Save button). An undo toast ("Transition updated. Undo") appears for 5 seconds.

#### Compatibility hints

Injected automatically when a row is expanded:

```
G → E  · −3 semitones to match    [Apply]
72 → 68 BPM · −5.6%               [Apply tempo]
```

[Apply] pre-fills the corresponding slider. Non-blocking — user can ignore.

#### Songset metadata

- **Name:** Inline text input in the app bar. Saves on blur or Enter. Validates non-empty.
- **Description:** Accessible via `···` → "Edit description".

#### Bottom action bar

| Button | Behavior |
|---|---|
| `+ Add` | Open Browse Sheet (§5.2a) |
| `▶ Play` | Play songset preview from position 1 (or resume if paused) |

Export is accessed via `···` overflow menu (used once per session; not worth a persistent button).

#### Overflow menu (`···`)

Export, Edit description, Issues across this set, Duplicate songset, Delete songset.

#### Keyboard shortcuts (desktop only)

| Key | Action |
|---|---|
| `Space` | Toggle playback |
| `←` `→` | Seek −10s / +10s |

---

### 5.2a Browse Sheet (within Editor)

**Trigger:** `+ Add` button in the editor bottom action bar.

**Presentation:** Slides up from the bottom (~85% viewport on mobile/tablet). Swipe-down or `×` to dismiss. On desktop: side panel, ~360px wide.

#### Layout

```
┌─────────────────────────────────┐
│  ────────  (drag indicator)     │
│  Browse Songs               [×] │
├─────────────────────────────────┤
│  [🔍 Search songs…          ] [×]│  ← search input
│  [ Title ✓ ][ Lyrics ][ Composer ]  ← field pills (multi-select)
│  Album: [Any ▾]  Key: [Any ▾]  BPM: [—]  ☐ Show unanalyzed  ← filters (collapsible)
├─────────────────────────────────┤
│  (when search is empty:)        │
│  RECENT                         │  ← per-user history sections
│  How Great Is Our God       [+] │
│  ─────────────────────────────  │
│  FREQUENT IN YOUR SETS          │
│  Cornerstone                [+] │
│  ─────────────────────────────  │
│  敬拜讚美15              ──────  │  ← album_series group headers
│  Cornerstone                [✓] │  ← already-added shows checkmark
│  E major · 68 BPM · 5:14        │
│  ─────────────────────────────  │
│  How Great Is Our God       [+] │
│  G major · 72 BPM · 4:32        │
│  ─────────────────────────────  │
│  (when search active: flat ranked list, no grouping)
└─────────────────────────────────┘
```

#### Search behavior

- Debounced input (300ms)
- **Field pills:** multi-select; default is Title only. Options: Title, Lyrics, Composer. (Replaces the v2 cycling chip — state always visible.)
- Pinyin matching: active when Title or Composer is selected
- **Structural filters** (collapsible row, collapsed by default):
  - Album: select from `album_series` distinct values
  - Key: select from `recordings.musical_key` + `musical_mode`
  - BPM: dual-handle slider (off by default)
  - "Show unanalyzed": when off (default), shows only songs with `lrc_status = 'completed'`
- "×" in search input clears and resets to full list

#### Song list behavior

- When search is empty: show Recent (per-user history), Frequent (frequent in this user's sets), then results grouped by `album_series`
- When search is active: flat ranked list, no grouping
- Each row: title, key + mode, BPM, duration
- **`[+]` button** → adds immediately; changes to `[✓]`. Same song can be added multiple times.
- **Long-press song title** → preview audio in global player

#### Empty states

- No results: "No songs found."
- Empty catalog: "No songs available yet. Your administrator needs to add songs to the catalog."

#### Actions

| Action | Trigger | Behavior |
|---|---|---|
| Search | Type in input | Debounced API call |
| Toggle field | Tap field pill | Toggles that field on/off; re-searches |
| Add song | `[+]` button | POSTs item to songset; button → `[✓]`; toast "Added 'X'" |
| Preview audio | Long-press title | Plays audio; audio bar appears |
| Dismiss | Swipe down or `[×]` | Closes sheet; editor list refreshes |

---

### 5.2b Transition Detail Sheet (within Editor)

**Trigger:** `[More transition… ▸]` link inside the inline expanded row (or for advanced editing needs).

**Presentation:** Bottom sheet, ~50% viewport. Dismissible by swipe-down or `×`. Auto-saves on dismiss. Undo toast for 5 seconds.

This sheet exists for edge cases where the inline controls are insufficient (e.g. fine-grained slider control on a small phone screen). Content mirrors the inline controls but with more vertical space.

First-song behavior: shows "This is the first song" header; only shows intro fade-in, key shift, and tempo ratio.

---

### 5.2c Lyrics Review (within Editor)

**Trigger:** Tap the `♪` chip on any song row (or `♪` icon on desktop hover).

**Purpose:** Verify the auto-generated LRC is correct — every lyric word is right, lines are in the correct sequence, and timestamps align accurately with the audio. Fix text or timing directly when something is wrong; corrections are saved as a per-user LRC override and applied to that user's exports.

**Presentation:** Full-screen overlay on tablet/phone. Side panel (≥1024px desktop, ~480px wide). Playback begins automatically on open.

#### Modes

A segmented control in the header switches between three modes. Default is **Review**; mode is local UI state (not persisted between sessions).

```
[Review]  [Edit text]  [Edit timing]
```

Edit modes are disabled when `lrc_status !== 'completed'` (see "No lyrics yet" below).

#### Layout (Review mode)

```
┌────────────────────────────────────────────────┐
│  ←  Cornerstone · 5:14 · E major          [×]  │
│  📝 Using your edited copy · [Reset to official]│  ← shown only when override exists
│  [Review] [Edit text] [Edit timing]   3 marks   │
├────────────────────────────────────────────────┤
│                               📌 [Freeze scroll]│
│  [INTRO]                                       │
│   00:00.0   (intro)                       🔖   │
│                                                │
│  [VERSE 1]                                     │
│   00:15.2   My hope is built on nothing   🔖   │
│   00:22.0   less                          🔖   │
│ ▶ 00:28.4   than Jesus' blood and         🔖   │  ← current line
│   00:35.0   righteousness                 🔖   │
│   00:41.6   I dare not trust the sweetest 🔖●  │  ← marked (filled)
│                                                │
│  [CHORUS]                                      │
│   00:55.0   Christ alone, Cornerstone     🔖   │
│   01:05.0   Weak made strong              🔖   │
│   …                                            │
├────────────────────────────────────────────────┤
│  ←3s  [◀◀]  [▶/⏸]  [▶▶]  +3s   ☐ Loop ±3s    │
│  00:28 ━━━━━━━━━━━━━━━━━━━━  05:14             │
│  [⏮ Prev song]                  [Next song ⏭] │
└────────────────────────────────────────────────┘
```

#### Layout (Edit timing mode)

```
┌────────────────────────────────────────────────┐
│  ←  Cornerstone · 5:14 · E major          [×]  │
│  📝 Using your edited copy · [Reset to official]│
│  [Review] [Edit text] [⬤ Edit timing]   3 marks │
├────────────────────────────────────────────────┤
│  [VERSE 1]                                     │
│   00:15.2  −.5 −.1 +.1 +.5  My hope is built… │
│   00:22.0  −.5 −.1 +.1 +.5  less              │
│ ▶ 00:28.4  [Anchor here] −.5 −.1 +.1 +.5  than│  ← current line
│   00:35.0  −.5 −.1 +.1 +.5  righteousness     │
│   00:41.6  −.5 −.1 +.1 +.5  I dare not…   🔖● │
├────────────────────────────────────────────────┤
│  ←3s  [◀◀]  [▶/⏸]  [▶▶]  +3s   ☐ Loop ±3s    │
│  00:28 ━━━━━━━━━━━━━━━━━━━━  05:14             │
│  [⏮ Prev song]                  [Next song ⏭] │
└────────────────────────────────────────────────┘
```

#### Layout — desktop side panel (≥1024px)

Two-column split. Lyrics list on the left; when in Edit text mode, an inline text input replaces the line text in the same column. No separate issues panel (issues panel is removed; marks are inline only).

#### Override banner

When the user has an LRC override for the current recording, a persistent banner appears below the header:

`📝 Using your edited copy · [Reset to official]`

Tapping **Reset to official** shows a confirmation dialog ("Discard your edits and revert to the catalog LRC?"). On confirm, the `user_lrc_override` row is deleted and the view refreshes from the official R2 LRC.

When no override exists, the banner is hidden; the official LRC is shown.

#### Review mode — behavior

**Lyrics list:**
- Entire song visible, scrollable.
- Auto-scroll keeps the current playing line vertically centered. `📌 Freeze` toggle stops auto-scroll while the user reads ahead — resumes on next play action.
- **Section headers** (`[INTRO]`, `[VERSE 1]`, `[CHORUS]`, etc.) injected from `recordings.sections` (analysis output). LRC itself has no markers.
- **Timestamps** always visible to the left of each line. They are part of the review, not optional.
- Current line marker (`▶`) in the left gutter.

**Interaction per line:**
- **Tap any line** → seek to its LRC timestamp and resume playback from there.
- **Tap `🔖` (mark icon)** → toggles a "needs review" mark on that line. Filled icon (`🔖●`) indicates marked; subtle warning tint on the row.

**Mark counter in header:**
- Shows `N marks` when any lines are marked. Tapping the counter cycles through marked lines (seek + scroll to each in turn). No metadata (no category, note, or panel) — marks are navigation aids only.
- Marks persist server-side (`lyric_mark` table) so they survive across sessions and devices. Editing a line's text or timestamp auto-clears its mark (the user has addressed it).

**Playback controls:**
- `←3s` / `+3s` — step backward/forward 3 seconds
- `[▶/⏸]` — play/pause
- `[◀◀]` / `[▶▶]` — skip ±10s
- Seek bar — scrubable
- `☐ Loop ±3s` — when enabled, playback loops a configurable window centered on the current line's timestamp. Default window: 6s. Adjustable in Settings (see §5.4).

**Song navigation:**
- `[⏮ Prev song]` / `[Next song ⏭]` — swap to the previous/next item in the songset without leaving the view. Audio and lyrics reload for the new song.
- On touch: swipe left/right on the lyrics area to advance songs.

#### Edit text mode — behavior

**Purpose:** Phase A — correct lyric text. Fix wrong words, delete extra lines, insert missing lines. Audio remains available for spot-checking but scrubbing is secondary.

- Lines render with an inline text input; tap a line to focus its input.
- Timestamps shown read-only to the left.
- Per-line affordances:
  - `✎` — focus input for this line
  - `＋` (between lines) — insert a new blank line; placeholder timestamp = midpoint of neighbors; user anchors it in Edit timing mode
  - `🗑` — delete this line; undo toast for 5 seconds
- **Auto-save on blur or Enter.** First save for this recording lazy-initializes the `user_lrc_override` row (a full copy of the current LRC — official or existing override).
- Editing a line's text auto-clears its mark.

#### Edit timing mode — behavior

**Purpose:** Phase B — correct timestamps. Play audio, scrub to the exact moment each line begins, anchor each line to the playhead.

- Lines render compact. Timestamps in a tap target on the left; text read-only on the right.
- Playback is the primary input.
- Per-line affordances on the current (or any focused) line:
  - **`[Anchor here]`** — sets this line's timestamp to the current playhead position. Auto-saves.
  - **`−0.5  −0.1  +0.1  +0.5`** — nudge buttons for fine adjustment without touching playback. Auto-saves on each tap.
- Anchoring or nudging a line auto-clears its mark.

#### API surface

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/recordings/[hashPrefix]/lrc` | Returns `{ source: 'override' \| 'official', content: string, updatedAt: string \| null }`. |
| `PUT` | `/api/recordings/[hashPrefix]/lrc/override` | Body `{ content: string }`. Upserts the user's full LRC override. Server validates the content parses cleanly (standard `[mm:ss.xx]text` format). |
| `DELETE` | `/api/recordings/[hashPrefix]/lrc/override` | Deletes the override; subsequent reads fall back to official. |
| `GET` | `/api/recordings/[hashPrefix]/marks` | Returns array of `{ timestampSeconds, createdAt }` for this user. |
| `PUT` | `/api/recordings/[hashPrefix]/marks/[ts]` | Adds or updates a mark at the given timestamp. |
| `DELETE` | `/api/recordings/[hashPrefix]/marks/[ts]` | Removes a mark. |

#### LRC format

LRC content uses the standard format: one line per row, `[mm:ss.xx]text`. The canonical parser is `src/stream_of_worship/admin/services/lrc_parser.py` (`parse_lrc()`). The Next.js implementation should port the same regex (`\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)`); do not invent a new format.

#### No lyrics yet

If `lrc_status !== 'completed'`:
- Show centered card: "Lyrics not yet generated for this recording."
- Status label: `queued | processing | failed` (live via SSE where available).
- If failed: "Notify administrator" link — opens the user's default email client with a pre-filled subject.
- Edit modes are disabled.

#### Mobile (<768px) adjustments

Full-screen sheet. Auto-scroll anchors current line in the upper third of the viewport (not centered), so the reviewer's tapping thumb area is below the active line. Loop toggle and ±3s buttons in a compact bottom bar.

In Edit timing mode, the four nudge buttons (`−0.5 −0.1 +0.1 +0.5`) collapse into a `−` / `+` pair; long-press gives the coarse (±0.5s) step, tap gives fine (±0.1s).

---

### 5.3 Export — `/songsets/[id]/export`

**Purpose:** Configure and monitor the export job that produces an MP3 + MP4. Remains a separate route because the job may take several minutes and the user may navigate away and return.

#### Layout

```
┌─────────────────────────────────┐
│  ←  Export                      │
├─────────────────────────────────┤
│  ─────  EXPORT OPTIONS  ─────── │
│  [×] Audio (MP3)                │
│  [×] Lyrics video (MP4)         │
│                                 │
│  Video template                 │
│  [preview thumbnail] Dark    ▾  │  ← thumbnail preview of template
│                                 │
│  Output resolution              │
│  (●) 720p  (faster, default)    │
│  ( ) 1080p (sharper)            │
│    Est. render: ~8 min          │  ← shown for 1080p on long sets
│                                 │
│  (warning if marked lines)      │
│  🔖 5 marked lines across 2     │
│    songs. [Review ›]            │
│                                 │
│  [      Start Export      ]     │
├─────────────────────────────────┤
│  (progress — shown after start) │
│                                 │
│  Mixing audio…                  │
│  [████████░░░░░░░░░░]  40%      │
│  Phase 1 of 4 · ~6 min left     │
│                                 │
│  [          Cancel          ]   │
├─────────────────────────────────┤
│  (completion)                   │
│                                 │
│  ✓ Done in 7m 42s               │
│  [      Download Audio      ]   │
│  [      Download Video      ]   │
└─────────────────────────────────┘
```

#### Export configuration

| Field | Options | Default |
|---|---|---|
| Include audio (MP3) | Checkbox | Checked |
| Include lyrics video (MP4) | Checkbox | Checked |
| Video template | Select: Dark, Gradient Warm, Gradient Blue — with thumbnail preview | From user settings |
| Output resolution | 720p (faster) / 1080p (sharper) | 720p |

#### Pre-export validation

- Blocks if no songs in the set.
- Blocks if any item `isOrphan === true` — shows inline warning: "Remove orphan songs before exporting" with a "Remove orphans" action.
- Non-blocking warning if the user has marked lines across any song in the set: "🔖 N marked lines across X songs — timing review may be incomplete. Continue anyway? [Review first ›]"

#### Progress tracking

- "Start Export" → POST to `/api/export` → server begins render, returns `job_id`
- `job_id` stored in URL param (`?job=<id>`) — page reconnects on refresh
- Progress via SSE at `/api/export/[jobId]/stream`
- Render phases: `Preparing` → `Mixing audio` → `Rendering frames` → `Encoding video` → `Uploading` → `Completed`
- Display: animated progress bar, phase label, estimated time remaining

#### SSE event format

```json
{ "phase": "mixing_audio", "phaseIndex": 1, "totalPhases": 4, "percent": 40, "estimatedSecondsLeft": 360, "description": "Mixing audio…" }
{ "phase": "completed", "phaseIndex": 4, "totalPhases": 4, "percent": 100, "elapsedSeconds": 462 }
{ "phase": "failed", "error": "FFmpeg error: input file not found" }
```

#### Completion states

- **Success:** Green "✓ Done in Xm Ys" + Download Audio + Download Video buttons
- **Failure:** Red "✕ Export failed" + error message + "Try again" button
- **Timeout:** Orange "⚠ Render timed out" + "Retry export" button (re-runs from scratch, same output)
- **Cancelled:** Grey "Export cancelled" + "Export again" button

#### Actions

| Action | Trigger | Behavior |
|---|---|---|
| Start export | "Start Export" button | Validates; POSTs job; begins SSE stream |
| Cancel | "Cancel" button | Signals cancel; closes SSE stream |
| Download | Download buttons | Downloads MP3/MP4 via signed R2 URL |
| Back | `←` | Returns to editor; render continues in background |

---

### 5.4 Settings — `/settings`

**Purpose:** App preferences, account management, and catalog sync.

#### Layout

```
┌─────────────────────────────────┐
│  ←  Settings                    │
├─────────────────────────────────┤
│  PLAYBACK                       │
│  Default gap (beats)      [2.0] │  ← number input, auto-save
│  Lyrics loop window (s)   [6.0] │  ← for Loop ±Ns in Lyrics Review
├─────────────────────────────────┤
│  EXPORT                         │
│  Video template   [Dark      ▾] │  ← select, auto-save
│  Default resolution  [720p   ▾] │
├─────────────────────────────────┤
│  CATALOG                        │
│  [      Sync Catalog       ]    │
│  Last synced: 2 hours ago       │
├─────────────────────────────────┤
│  ACCOUNT                        │
│  mhuang@gmail.com               │
│  [        Sign Out         ]    │
├─────────────────────────────────┤
│  APP INFO                       │
│  Version: 0.3.0                 │
└─────────────────────────────────┘
```

#### Settings fields

| Section | Field | Type | Notes |
|---|---|---|---|
| Playback | Default gap (beats) | Number input | 0.0–16.0, step 0.5; auto-save on blur |
| Playback | Lyrics loop window (s) | Number input | 2.0–10.0, step 0.5; default 6.0; auto-save |
| Export | Default video template | Select | Dark, Gradient Warm, Gradient Blue; auto-save |
| Export | Default resolution | Select | 720p, 1080p; auto-save |
| Catalog | Sync Catalog | Button | Triggers sync via Admin CLI (admin-only operation; shows toast) |
| Account | Email | Read-only | From Better Auth session |
| Account | Sign out | Button | Invalidates session, redirects to `/login` |

No explicit "Save" button — all fields auto-save.

---

## 6. Global Audio Player

### 6.1 Architecture

A single `AudioContext` instance managed via React context. Only one audio source plays at a time. Recommended: native `<audio>` element with custom controls UI. Fall back to Howler.js if cross-browser codec issues arise.

### 6.2 Playback bar

Fixed-position at the bottom of the viewport, above the device safe area. Appears when audio is loaded; persists across sheet open/close within `/songsets/[id]`. Disappears when navigating to a different route.

On mobile, sits above the bottom action bar (when both are visible).

Contents:
- Song title (truncated)
- Progress indicator (thin seek bar, scrubable)
- Elapsed / total time
- Play/Pause, Skip −10s and +10s, Close/stop

### 6.3 Playback sources

| Where | What plays |
|---|---|
| Editor "▶ Play" | Transition preview clip from server (`POST /api/songsets/[id]/preview`) |
| Inline transition "Preview ▶" | Transition preview between adjacent songs |
| Browse Sheet long-press | Song's raw audio from R2 (via signed URL) |
| Lyrics Review | Song's raw audio from R2 |

When entering Lyrics Review for the **currently-playing song**, audio resumes from the current playback position (not restarted). When entering Lyrics Review for a **different song**, audio restarts from 0.

If the user has an LRC override for the playing recording, the override LRC drives the lyrics list and line-timing in Lyrics Review; the audio source is unchanged (always the official R2 audio file).

### 6.4 Capabilities

| Feature | Status |
|---|---|
| Play / Pause / Stop | Required |
| Seek (scrub) | Required |
| Skip ±10 seconds | Required |
| Skip ±3 seconds (Lyrics Review) | Required |
| Loop window (Lyrics Review) | Required |
| Background playback (Media Session API) | Nice-to-have |
| Volume control | Deferred to v2 |

---

## 7. Export Pipeline

### 7.1 Flow

```
Browser            Next.js (long-running route, maxDuration: 800s)          R2      Neon
  │                   │                                                       │        │
  │ POST /api/export ─>                                                       │        │
  │                   │ INSERT render_job (queued) ──────────────────────────────────> │
  │<── { jobId } ─────│ (response sent via after(); render continues)        │        │
  │                   │                                                       │        │
  │ GET /api/export/[id]/stream (SSE) ─────────────────────────────────────> │        │
  │                   │── Mix audio (Node FFmpeg) ─>                         │        │
  │<── progress ──────│                                                       │        │
  │                   │── Render frames (Node canvas) ─>                     │        │
  │<── progress ──────│                                                       │        │
  │                   │── Encode video (Node FFmpeg) ─>                      │        │
  │<── progress ──────│                                                       │        │
  │                   │── Upload MP3 ─────────────────────────────────────── ─>       │
  │                   │── Upload MP4 ─────────────────────────────────────── ─>       │
  │                   │── UPDATE render_job (completed) ─────────────────────────────>│
  │<── complete ──────│                                                       │        │
  │                   │                                                       │        │
  │ GET /api/export/[id]/files ─>                                             │        │
  │                   │── sign R2 URLs ─────────────────────────────────────>│        │
  │<── { audioUrl, videoUrl } ─                                               │        │
```

### 7.2 Job persistence

`job_id` is stored in the URL query param (`?job=<id>`). On return to `/songsets/[id]/export`, the page reconnects to the SSE stream if the job is still running, or shows the completed/failed/timeout state from Neon.

### 7.3 Render phases

| Phase | Description |
|---|---|
| Preparing | Fetching audio from R2; fetching LRC (user's override if present, otherwise official R2 LRC); validating inputs. Override lookup is scoped to the `userId` on the export job. |
| Mixing audio | FFmpeg transition mix (concat + crossfade + key shift + tempo) |
| Rendering frames | Canvas lyric overlay frame generation |
| Encoding video | FFmpeg H.264 encode with lyric frame overlay |
| Uploading | Writing MP3 + MP4 to R2 |
| Completed | Both files available |

If only one output is requested (audio-only or video-only), the skipped phases are omitted from the phase counter.

---

## 8. Responsive Design

### 8.1 Breakpoints

| Breakpoint | Width | Notes |
|---|---|---|
| Mobile | < 768px | Phones; accommodation, not primary |
| Tablet | 768px–1024px | iPads, Android tablets; primary target |
| Desktop | > 1024px | Laptops/desktops; first-class environment |

### 8.2 Typography

| Context | Mobile | Tablet | Desktop |
|---|---|---|---|
| Song title in list | `text-base font-semibold` | `text-base font-semibold` | `text-sm` (table) |
| Lyrics Review line (current) | `text-base` | `text-base` | `text-sm` |
| Lyrics Review line (metadata) | `text-sm font-mono text-muted` | `text-sm font-mono text-muted` | `text-xs font-mono text-muted` |
| Secondary / metadata | `text-sm text-muted` | `text-sm text-muted` | `text-sm text-muted` |
| Minimum body text | 16px | 16px | 14px |

Lyrics Review uses body-size text (not display-size) because the user is reading closely, not performing. Timestamps use a monospace font for alignment.

### 8.3 Touch targets

All interactive elements: minimum 48×48px on mobile/tablet. Destructive actions (Delete, Remove, Delete flag) require confirmation. Desktop: minimum 32×32px.

### 8.4 Gestures

| Gesture | Element | Action |
|---|---|---|
| Tap | Song row | Toggle inline transition controls |
| Tap | `♪` chip on row | Open Lyrics Review |
| Swipe left | Song row in editor | Reveal Remove button |
| Drag `≡` handle | Song row | Reorder songs |
| Long-press | Songset list row | Context menu |
| Long-press | Song in Browse Sheet | Preview audio |
| Right-click | Any row (desktop) | Context menu |
| Swipe down | Browse sheet | Dismiss |
| Tap | LRC line in Lyrics Review | Seek to that timestamp |
| Tap `🔖` | LRC line (Review mode) | Toggle "needs review" mark |
| Tap `[Anchor here]` | LRC line (Edit timing mode) | Set line timestamp to current playhead |
| Swipe left/right | Lyrics Review lyric area | Navigate to prev/next song |
| 📌 Tap | Lyrics Review | Toggle auto-scroll freeze |

Pull-to-refresh is not implemented — React Query handles cache invalidation.

### 8.5 Loading & error states

- Show skeleton rows during data fetches
- Toast errors for network failures with "Retry" action
- Audio playback requires network (no offline caching in v1)

---

## 9. Data Requirements

### 9.1 Songset

```typescript
interface Songset {
  id: string;
  name: string;
  description: string | null;
  songCount: number;
  totalDurationSeconds: number | null;
  formattedTotalDuration: string | null;  // "23m 14s"
  updatedAt: string;  // ISO 8601
  createdAt: string;
}
```

### 9.2 SongsetItem

```typescript
interface SongsetItem {
  id: string;
  songsetId: string;
  position: number;
  songId: string;
  songTitle: string;
  songKey: string | null;
  songComposer: string | null;
  songLyricist: string | null;
  songAlbumName: string | null;
  recordingHashPrefix: string | null;
  displayKey: string | null;
  musicalMode: 'major' | 'minor' | null;
  keyConfidence: number | null;
  tempoBpm: number | null;
  durationSeconds: number | null;
  formattedDuration: string | null;
  isOrphan: boolean;
  markCount: number;                    // count of active LyricMarks for this user + recording
  hasLrcOverride: boolean;              // true if user has a local LRC override for this recording
  // Transition parameters
  gapBeats: number;                     // default 2.0
  crossfadeEnabled: boolean;
  crossfadeDurationSeconds: number | null;
  keyShiftSemitones: number;            // default 0
  tempoRatio: number;                   // default 1.0
  introFadeInSeconds: number;           // default 0.0; first-song only
}
```

### 9.3 Song (catalog)

```typescript
interface Song {
  id: string;
  title: string;
  titlePinyin: string | null;
  composer: string | null;
  composerPinyin: string | null;
  lyricist: string | null;
  albumName: string | null;
  albumSeries: string | null;           // e.g. "敬拜讚美15"
  musicalKey: string | null;
  hasRecording: boolean;
  hasAnalysis: boolean;
  hasLrc: boolean;
  recordingHashPrefix: string | null;
  tempoBpm: number | null;
  durationSeconds: number | null;
  formattedDuration: string | null;
  displayKey: string | null;
  musicalMode: 'major' | 'minor' | null;
  keyConfidence: number | null;
  lyricsLines: string[] | null;         // lightweight fallback when LRC missing
  sections: SongSection[] | null;       // from analysis output
}

interface SongSection {
  name: string;               // e.g. "verse", "chorus", "bridge"
  startSeconds: number;
}
```

### 9.4 UserLrcOverride and LyricMark

```typescript
interface UserLrcOverride {
  id: string;
  userId: string;
  recordingContentHash: string;
  lrcContent: string;       // full LRC text in standard [mm:ss.xx]text format
  updatedAt: string;        // ISO 8601
  createdAt: string;
}

interface LyricMark {
  // Lightweight "needs review" bookmark. No category or note metadata.
  // Keyed by the line's timestamp on the current LRC (override or official).
  // Advisory only — does not affect playback or export.
  id: string;
  userId: string;
  recordingContentHash: string;
  timestampSeconds: number;
  createdAt: string;
}
```

### 9.5 ExportJob

```typescript
interface ExportJob {
  id: string;
  songsetId: string;
  status: 'queued' | 'preparing' | 'mixing_audio' | 'rendering_frames' | 'encoding_video' | 'uploading' | 'completed' | 'failed' | 'timeout' | 'cancelled';
  phase: string;
  phaseIndex: number;
  totalPhases: number;
  percentComplete: number;
  estimatedSecondsLeft: number | null;
  elapsedSeconds: number | null;
  description: string;
  errorMessage: string | null;
  audioUrl: string | null;
  videoUrl: string | null;
  resolution: '720p' | '1080p';
}
```

### 9.6 User settings

```typescript
interface UserSettings {
  defaultGapBeats: number;              // 2.0
  defaultVideoTemplate: string;         // "dark"
  defaultResolution: '720p' | '1080p'; // "720p"
  lyricsLoopWindowSeconds: number;      // 6.0
}
```

---

## 10. Non-Functional Requirements

| Requirement | Target |
|---|---|
| Initial page load (LCP) | < 2.5s on 4G tablet |
| Time to interactive | < 4s on tablet |
| Audio playback start latency | < 1s after tap |
| Export function timeout budget | 800s (Pro + Fluid Compute); default 720p to stay under budget |
| Accessibility | WCAG 2.1 AA (keyboard nav, ARIA labels, sufficient contrast) |
| Browser support | Chrome 110+, Safari 16+, Firefox 120+, Chrome Android, Safari iOS |
| Minimum font size | 16px on mobile/tablet |
| Touch target size | ≥ 48×48px on touch devices |
| Vercel plan required | Pro (for `maxDuration: 800` and Fluid Compute) |

---

## 11. Key Changes from v2

| Area | v2 | v3 | Reason |
|---|---|---|---|
| Primary persona | "Worship leader on stage" | "Worship leader preparing a video" | Corrects the foundational framing |
| Architecture | Next.js + Python FastAPI + Analysis Service | Next.js + Neon only (Analysis Service is admin-only) | Next.js independence; no Python in user-facing path |
| Video render | Analysis Service (Python) | Next.js (Node FFmpeg + canvas) | Closes the last web-app runtime dependency on Python infra |
| Audio render | Analysis Service | Next.js (Node FFmpeg) | Same as above |
| Lyrics overlay purpose | Stage performance display | LRC QA review tool | Corrects the use case |
| Lyrics layout | 2 lines (current + next) | Full song scrollable with timestamps, section headers | Reviewability over glanceability |
| Lyrics modes | Stage mode / Edit mode | Three sub-modes: Review / Edit text / Edit timing; timestamps always visible | Enforces a clean two-phase correction workflow |
| Controls fade | After 3s idle | Never fade | Not a performance display |
| Lyric marking | Not in spec | Per-line `🔖` mark (no category/note) as a review bookmark; cycles through marked lines from header counter | Lightweight triage while listening — no metadata overhead |
| Lyric editing | Not in spec | In-app LRC text and timing edits saved as a per-user override; export uses override when present; admin merges offline | Removes dependency on offline admin CLI for common fixes |
| Transition controls | Separate sheet | Inline expand on row + "More…" link | Fewer taps for the common case |
| Transition tempoRatio | Missing from spec (in DB/TUI) | Added to inline controls | Restores a real feature |
| First-song controls | Key shift only | Fade-in + key shift + tempo | First song was under-controlled |
| Compatibility hints | None | Key + BPM suggestion strip on expand | Removes math from the user's plate |
| Browse field filter | Cycling chip (anti-pattern) | Multi-select pills | State visible at rest |
| Browse filters | None | Album, Key, BPM, Show unanalyzed | Uses existing catalog service helpers |
| Browse empty state | Shows recent results, full list | Recent + Frequent sections when no query; album_series grouping | Faster common-case song access |
| Songset row metadata | `G · 72 BPM · 4:32` | `G major · 72 BPM · 4:32 · 5 sections` + ⚠ low-confidence | Mode disambiguation; analysis quality signal |
| Songset list row | Song count + age | Song count + total duration + age | Duration matters for service planning |
| Export | Two-service pipeline (v2 §7) | Single Next.js function (Node FFmpeg) | No cross-service coordination |
| Export resolution | Fixed | 720p / 1080p choice | Lets user manage render-time budget |
| Export progress | Generic states | Phased progress with estimated time | Transparent about a ~8 min operation |
| Export issue warning | Not in spec | Non-blocking pre-flight if user has marked lines in any song | Prompts QA before committing to a render |
| Keyboard shortcuts | Space + arrows | Same (no change) | |
| Pull-to-refresh | Present | Removed | React Query handles invalidation |
