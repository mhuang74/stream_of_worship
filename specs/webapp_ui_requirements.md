# Stream of Worship — Web App UI Requirements

**Source:** Ported from TUI User App (`src/stream_of_worship/app/`)  
**Target:** Next.js (App Router) hosted on Vercel  
**Date:** 2026-05-10

---

## 1. Overview & Goals

The Stream of Worship Web App replaces the Textual TUI with a browser-based interface that worship leaders can access from any device, including tablets on stage. The core workflow is unchanged: browse a shared song catalog, assemble songsets with smooth inter-song transitions, preview audio and lyrics, and export final audio + lyrics video files.

### Key goals

- **Multi-device access** — replace the desktop-only TUI with a mobile-first, responsive web app
- **Multi-user** — each worship leader has their own songsets; catalog is shared and managed by admins
- **Full feature parity** — all TUI capabilities must be preserved (playback, transition preview, export)
- **Familiar UX** — navigation and interactions mapped to standard web patterns (drawers, modals, bottom nav)

### Out of scope for v1

- Admin catalog management (stays in the `sow-admin` CLI)
- Song analysis / LRC generation (stays in the Analysis Service)
- Songset JSON import/export CLI commands (defer to v2)

---

## 2. Architecture Decisions

| Concern | Decision |
|---|---|
| Frontend | Next.js (App Router) + TypeScript |
| Styling | Tailwind CSS, mobile-first |
| UI components | shadcn/ui (headless, accessible) |
| Auth | Better Auth with Neon Postgres adapter |
| Database | Neon Postgres (replaces dual SQLite) |
| Backend API | Python FastAPI (shared code with Admin CLI) |
| Heavy processing | Existing Analysis Service (Docker, handles export) |
| Audio playback | Web Audio API via browser |
| Real-time progress | Server-Sent Events (SSE) from Python API |
| Deployment | Vercel (frontend) + existing infra (Python API, Analysis Service) |

The Next.js app is a pure frontend + thin API proxy. All data operations and audio/video processing go through the Python API.

---

## 3. Authentication

### 3.1 Provider

Better Auth with Neon Postgres as the session/user store. OAuth providers TBD (Google recommended as default).

### 3.2 Protected routes

All app routes are protected. Unauthenticated users are redirected to `/login`.

```
/login          → Public: OAuth sign-in page
/               → Protected: redirects to /songsets
/songsets       → Protected
/songsets/[id]  → Protected (owner check)
/settings       → Protected
```

### 3.3 User data isolation

Each user has their own songsets. The Python API enforces user ownership on all songset/item operations. The catalog (songs, recordings) is shared read-only across all users.

---

## 4. Navigation & Routing

### 4.1 Route structure (Next.js App Router)

```
/login                                    Login page
/songsets                                 Songset list (home)
/songsets/[id]                            Songset editor
/songsets/[id]/browse                     Browse catalog (add songs)
/songsets/[id]/items/[itemId]/transition  Transition detail
/songsets/[id]/items/[itemId]/lyrics      Lyrics preview
/songsets/[id]/export                     Export progress
/settings                                 App settings
```

### 4.2 Navigation patterns

**Desktop (≥768px):** Top navigation bar with breadcrumbs showing current location. Back navigates via `router.back()` or explicit breadcrumb link.

**Mobile (<768px):** Bottom navigation bar with 4 tabs: Home (songsets), Browse (within editor context), Export, Settings. Breadcrumb replaced by a back arrow in the top app bar. Screens use full viewport height with scroll.

### 4.3 Navigation flow

```
/login
  └── /songsets (home, after auth)
        ├── /settings
        └── /songsets/[id]  (edit / create)
              ├── /songsets/[id]/browse
              ├── /songsets/[id]/items/[itemId]/transition
              ├── /songsets/[id]/items/[itemId]/lyrics
              └── /songsets/[id]/export
```

### 4.4 State passing

Use URL params and search params for navigation state. Avoid client-side global state for navigation (use React Query / SWR for server state).

---

## 5. Screen Requirements

### 5.1 Songset List — `/songsets`

**Purpose:** Landing screen. Shows the user's saved songsets and lets them create, edit, or delete them.

#### Layout

```
┌─────────────────────────────────────────┐
│  [≡ Menu]    Stream of Worship   [Sync] │  ← Top app bar
├─────────────────────────────────────────┤
│  Your Songsets                  [+ New] │  ← Section header + CTA
├─────────────────────────────────────────┤
│  ┌─────────────────────────────────┐   │
│  │ Sunday Morning Set         [>] │   │  ← Songset card
│  │ 5 songs · Updated 2 days ago   │   │
│  └─────────────────────────────────┘   │
│  ┌─────────────────────────────────┐   │
│  │ Evening Worship            [>] │   │
│  │ 3 songs · Updated today        │   │
│  └─────────────────────────────────┘   │
│  (empty state if no songsets)          │
└─────────────────────────────────────────┘
```

#### Songset card

Each card displays:
- Songset name (bold)
- Song count + relative updated-at timestamp
- Tap/click navigates to `/songsets/[id]`
- Long-press (mobile) or right-click (desktop) opens context menu with: Rename, Delete

#### Actions

| Action | Trigger | Behavior |
|---|---|---|
| Create songset | `+ New` button | Creates a new songset with default name "New Songset", navigates to editor |
| Edit songset | Tap card | Navigates to `/songsets/[id]` |
| Delete songset | Context menu → Delete | Confirmation dialog ("Delete 'X'? This cannot be undone."), then delete + refresh list |
| Sync catalog | `[Sync]` button in app bar | Triggers catalog sync via Python API, shows toast with result |

#### Empty state

When no songsets exist: centered illustration + "No songsets yet" + "Create your first songset" button.

#### Mobile adaptations

- Cards are full-width, tap targets ≥ 48px tall
- `+ New` is a floating action button (FAB) in the bottom-right corner
- Sync button moves to a settings menu or pull-to-refresh gesture

---

### 5.2 Songset Editor — `/songsets/[id]`

**Purpose:** Core editing screen. Manage the ordered list of songs, edit metadata, preview audio, and initiate export.

#### Layout (desktop)

```
┌────────────────────────────────────────────────────────────┐
│ ← Back   [Songset name input]   [Description input]  [···] │  ← App bar
├────────────────────────────────────────────────────────────┤
│ #  Song              Key    BPM    Duration  Gap   Transition│
│ 1  How Great Is Our  G      72     4:32      2.0b  Crossfade │  ← Song row
│ 2  Cornerstone       E      68     5:14      2.0b  Gap       │
│ 3  Great Are You     C      80     3:45      2.0b  —         │
├────────────────────────────────────────────────────────────┤
│ [+ Add Songs]  [Preview ▶]  [Lyrics 🎵]  [Export →]        │  ← Action bar
└────────────────────────────────────────────────────────────┘
```

#### Layout (mobile)

```
┌─────────────────────────────┐
│ ← [Songset name]       [···]│  ← App bar (name is tap-to-edit inline input)
├─────────────────────────────┤
│ ┌───────────────────────┐  │
│ │ 1  How Great Is Our   │  │  ← Song card (swipeable)
│ │    G · 72 BPM · 4:32  │  │
│ │    Gap: 2.0b           │  │
│ └───────────────────────┘  │
│ ┌───────────────────────┐  │
│ │ 2  Cornerstone        │  │
│ │    E · 68 BPM · 5:14  │  │
│ └───────────────────────┘  │
├─────────────────────────────┤
│ [Add] [Preview] [Lyrics][Ex]│  ← Bottom action bar (4 icons + labels)
└─────────────────────────────┘
```

#### Song row / card fields

| Field | Source |
|---|---|
| # | `position` |
| Song title | `song_title` |
| Key | `display_key` (recording key if available, else song musical_key) |
| BPM | `tempo_bpm` |
| Duration | `formatted_duration` |
| Gap | `gap_beats` + "b" suffix |
| Transition | "Crossfade" if `crossfade_enabled`, else "Gap" |

Orphan items (song/recording missing from catalog) shown with warning icon and italicized title.

#### Song row actions

**Desktop:** Row hover reveals inline action buttons: `↑` Move up, `↓` Move down, `✎` Edit transition, `🎵` Lyrics, `✕` Remove.

**Mobile:** Swipe left reveals: "Edit Transition" (primary) and "Remove" (destructive). Tap-and-hold drag handle for reordering. Tap row opens transition detail drawer.

#### Songset metadata

- **Name:** Inline text input in the app bar. Saves on blur or Enter. Validates non-empty.
- **Description:** Desktop: inline input below the name. Mobile: accessible via `···` overflow menu → "Edit description" bottom sheet.

#### Actions

| Action | Trigger | Behavior |
|---|---|---|
| Add songs | `+ Add Songs` button | Navigates to `/songsets/[id]/browse` |
| Remove song | Swipe/button | Removes item, reorders positions, refreshes list |
| Reorder | Drag handle (mobile), ↑/↓ buttons (desktop) | Calls reorder API, refreshes |
| Edit transition | Swipe/button/tap row | Opens transition detail (drawer on mobile, navigates on desktop) |
| Preview audio | `Preview ▶` button | Generates and plays transition preview for selected song (audio bar appears) |
| Lyrics preview | `Lyrics 🎵` button or per-row icon | Navigates to `/songsets/[id]/items/[itemId]/lyrics` |
| Export | `Export →` button | Validates ≥1 song, navigates to `/songsets/[id]/export` |
| Toggle playback | Audio bar play/pause | Play/pause current preview |
| Skip | Audio bar ← → | Skip backward/forward 10 seconds |

#### Audio playback bar

Appears at the bottom when a song is playing/paused. Contains:
- Song title (truncated)
- Play/Pause button
- Seek bar (progress indicator, scrubable)
- Skip backward 10s, skip forward 10s
- Close/stop button

On mobile, this bar sits above the bottom navigation.

#### Keyboard shortcuts (desktop)

| Key | Action |
|---|---|
| `a` | Add songs |
| `,` | Move selected song up |
| `.` | Move selected song down |
| `e` | Edit transition for selected song |
| `l` | Open lyrics preview for selected song |
| `space` | Toggle playback |
| `←` `→` | Skip backward/forward |
| `x` | Export |

---

### 5.3 Browse / Add Songs — `/songsets/[id]/browse`

**Purpose:** Browse the full song catalog and add songs to the current songset.

#### Layout (desktop)

```
┌──────────────────────────────────────────────────────────┐
│ ← Songset Editor                  Browse Songs           │
├──────────────────────────────────────────────────────────┤
│ [🔍 Search by title, lyrics, composer...          ] [X] │  ← Search bar
│ Field: [All ▾]                                           │  ← Field selector
├──────────────────────────────────────────────────────────┤
│ Title               Key   BPM   Duration  Album          │
│ How Great Is Our    G     72    4:32      ——             │
│ Cornerstone         E     68    5:14      ——             │
│ ...                                                       │
├──────────────────────────────────────────────────────────┤
│ [Preview ▶]  [+ Add to Songset]                          │
└──────────────────────────────────────────────────────────┘
```

#### Layout (mobile)

```
┌─────────────────────────────┐
│ ← Browse Songs              │
├─────────────────────────────┤
│ [🔍 Search...          ] [X]│
│ [All ▾]                     │  ← Field filter chip
├─────────────────────────────┤
│ ┌─────────────────────────┐│
│ │ How Great Is Our God    ││  ← Song card
│ │ G · 72 BPM · 4:32       ││
│ └─────────────────────────┘│
│ ┌─────────────────────────┐│
│ │ Cornerstone              ││
│ │ E · 68 BPM · 5:14       ││
│ └─────────────────────────┘│
├─────────────────────────────┤
│      [+ Add to Songset]     │  ← Sticky bottom button
└─────────────────────────────┘
```

#### Search behavior

- Default: title-only search (same as TUI default)
- Field selector: dropdown/chip with options: **All**, **Title**, **Lyrics**, **Composer**
- Debounced input (300ms) — search triggers automatically as user types, no "Search" button needed
- "Clear" (×) button clears the search and resets to full list
- Only shows songs with recordings (has_analysis) by default; show all toggle in filters

#### Song list

Each row/card shows: title, key, BPM, duration, album (if available). No lyrics preview in this view.

#### Empty state

When catalog is empty or no results: "No songs found" with guidance message. If catalog is empty overall, show "Catalog not yet synced — ask your admin to run sow-admin sync".

#### Actions

| Action | Trigger | Behavior |
|---|---|---|
| Search | Type in search input | Debounced API call, updates list |
| Change field | Field selector | Re-searches with selected field |
| Select song | Tap/click row | Highlights selected row |
| Preview audio | `Preview ▶` button / tap song row playback icon | Downloads audio from R2, plays in browser; audio bar appears |
| Toggle playback | `space` key or audio bar | Play/stop currently previewing song |
| Add to songset | `+ Add to Songset` button | POSTs item to songset, shows toast "Added 'X' to songset", button stays enabled for continuous adding |
| Back | Back arrow | Returns to songset editor |

#### Mobile adaptations

- Search bar is always visible (sticky top)
- Selecting a song shows a bottom action sheet with: "Preview", "Add to Songset"
- No separate "Preview" button; use the action sheet

---

### 5.4 Transition Detail — `/songsets/[id]/items/[itemId]/transition`

**Purpose:** Fine-tune transition parameters between the previous song and this song.

**Mobile presentation:** Bottom sheet drawer (slides up from bottom), not a separate page. Desktop: separate page or side panel.

#### Layout

```
┌─────────────────────────────┐
│     Transition Settings     │  ← Sheet title
│  Entering: "Cornerstone"    │  ← Context: which song this affects
│  After:    "How Great..."   │
├─────────────────────────────┤
│  Gap (beats)                │
│  [──────○──────────] 2.0   │  ← Slider with numeric input
│                             │
│  Crossfade                  │
│  [●──────────────────] OFF  │  ← Toggle switch
│                             │
│  Crossfade Duration (s)     │
│  [────────────────────] 4.0 │  ← (disabled when crossfade off)
│                             │
│  Key Shift (semitones)      │
│  [—3][—2][—1][0][+1][+2][+3]│  ← Segmented control or stepper
├─────────────────────────────┤
│  [Cancel]       [Save]      │
└─────────────────────────────┘
```

#### Fields

| Field | Type | Default | Constraint |
|---|---|---|---|
| Gap (beats) | Slider + numeric input | `2.0` | Range 0.0–16.0, step 0.5 |
| Crossfade enabled | Toggle switch | `false` | |
| Crossfade duration (s) | Numeric input | `4.0` | Enabled only when crossfade is on; range 1.0–30.0 |
| Key shift (semitones) | Stepper / segmented | `0` | Range -6 to +6 |

#### Actions

| Action | Trigger | Behavior |
|---|---|---|
| Save | "Save" button | PATCHes item via API, closes drawer, updates row in editor |
| Cancel | "Cancel" or swipe-down | Discards changes, closes drawer |
| Preview | (v2) "Preview" button | Not in v1 (TUI stub); placeholder for future |

The first song in a songset has no "previous song" — transition settings should be hidden or show "This is the first song — no transition" message.

---

### 5.5 Lyrics Preview — `/songsets/[id]/items/[itemId]/lyrics`

**Purpose:** Synchronized lyrics display with audio playback. Shows current and next lyric lines with a progress bar. Includes an LRC debug table.

#### Layout (landscape / desktop — primary use case on tablet)

```
┌──────────────────────────────────┬──────────────────────┐
│                                  │  ● Song Title         │
│                                  │  G major · 72 BPM     │
│    How great is our God          │  4:32 · Album Name    │
│    (current lyric — large bold)  │                       │
│                                  │  ─────────────────    │
│    Sing with me                  │  00:00  (intro)       │
│    (next lyric — dimmed)         │  00:45  How great is  │
│                                  │  01:10  Sing with me  │
│    ▶ 01:23 ─────█░░░── 04:32    │  01:35  How great...  │  ← LRC table
│                                  │  ...                  │
└──────────────────────────────────┴──────────────────────┘
       ←10s  ▶/⏸  +10s  ✕
```

#### Layout (portrait mobile)

```
┌─────────────────────────────┐
│ ← Lyrics: "Cornerstone"     │  ← App bar
├─────────────────────────────┤
│                             │
│                             │
│   How great is our God      │  ← Current lyric (centered, large)
│                             │
│   Sing with me              │  ← Next lyric (centered, dimmed)
│                             │
│  ▶ 01:23 ───█░░░─── 04:32  │  ← Progress bar
├─────────────────────────────┤
│  [←10s]  [▶/⏸]  [+10s]    │  ← Controls
└─────────────────────────────┘
```

LRC debug table is hidden on portrait mobile; accessible via "Show all lyrics" toggle button.

#### Behavior

- On mount: downloads LRC + audio from Python API (which proxies R2), populates LRC table, auto-starts playback
- LRC timestamps: `[mm:ss.xx]` and `[mm:ss.xxx]` formats supported
- Current lyric: determined by scanning LRC lines for largest timestamp ≤ current position
- Progress bar: visual fill (`position / duration * 100%`), scrubable
- LRC table: auto-scrolls to highlight the current line
- Playback starts automatically on enter

#### Actions

| Action | Trigger | Behavior |
|---|---|---|
| Play/Pause | Button or `space` | Toggles playback state |
| Skip backward | Button or `←` key | Seeks -10 seconds |
| Skip forward | Button or `→` key | Seeks +10 seconds |
| Seek | Drag progress bar | Seeks to position |
| Back | Back button | Stops playback, returns to songset editor |

#### Audio implementation

Use Web Audio API (via a library like Howler.js or native `<audio>` element) for playback. The audio file URL is fetched from the Python API, which returns a signed R2 URL or streams the file directly.

---

### 5.6 Export Progress — `/songsets/[id]/export`

**Purpose:** Configure and monitor the audio + lyrics video export job submitted to the Analysis Service.

#### Layout

```
┌─────────────────────────────────────┐
│ ← Export                            │
├─────────────────────────────────────┤
│  Export Configuration               │
│                                     │
│  ☑ Include audio (MP3)              │
│  ☑ Include lyrics video (MP4)       │
│                                     │
│  Video template:  [Dark ▾]          │
│                                     │
│  [     Start Export     ]           │
├─────────────────────────────────────┤
│  (Progress section — shown after    │
│   export is started)                │
│                                     │
│  Downloading assets...              │
│  [████████░░░░░░░░░░] 40%           │
│  Step 2 of 5                        │
│                                     │
│  [       Cancel       ]             │
├─────────────────────────────────────┤
│  (Completion section)               │
│  ✓ Export complete! (2m 34s)        │
│  [Download Audio] [Download Video]  │
└─────────────────────────────────────┘
```

#### Export configuration (pre-start)

| Field | Options | Default |
|---|---|---|
| Include audio (MP3) | Checkbox | Checked |
| Include lyrics video (MP4) | Checkbox | Checked |
| Video template | Select: dark, gradient_warm, gradient_blue | From user settings |

#### Progress tracking

- On "Start Export": POST to Python API → Analysis Service, receive `job_id`
- Progress updates via SSE stream at `/api/export/[jobId]/stream`
- Progress states (from `ExportState`): Preparing, Downloading, Generating Audio, Generating Video, Finalizing, Completed, Failed, Cancelled
- Display: status description label, step indicator ("Step X of Y"), animated progress bar

#### Completion

- **Success:** Green checkmark + "Export complete! (2m 34s)" + Download links for audio and video files
- **Failure:** Red × + error message + "Try again" button
- **Cancelled:** Grey indicator + "Export cancelled" + "Export again" button

#### Actions

| Action | Trigger | Behavior |
|---|---|---|
| Start export | "Start Export" button | Validates ≥1 song, POSTs export job, begins SSE stream |
| Cancel | "Cancel" button | Sends cancel request to API, closes SSE stream |
| Download | Download buttons (shown on completion) | Downloads MP3/MP4 file directly |
| Back | Back button | Returns to songset editor; export continues in background (if running) |

#### Mobile adaptations

- Same layout, full-screen
- Download buttons are prominent, full-width

---

### 5.7 Settings — `/settings`

**Purpose:** View and edit application preferences.

#### Layout

```
┌─────────────────────────────┐
│ ← Settings                  │
├─────────────────────────────┤
│  Playback                   │
│  ─────────────────────────  │
│  Default gap (beats)   [2.0]│
│                             │
│  Export                     │
│  ─────────────────────────  │
│  Video template    [Dark ▾] │
│                             │
│  Account                    │
│  ─────────────────────────  │
│  mhuang@example.com         │
│  [Sign Out]                 │
├─────────────────────────────┤
│  App Info                   │
│  ─────────────────────────  │
│  Version: 0.2.0             │
│  [View changelog]           │
└─────────────────────────────┘
```

#### Settings fields

| Section | Field | Type | Values |
|---|---|---|---|
| Playback | Default gap (beats) | Number input | 0.0–16.0, step 0.5 |
| Export | Default video template | Select | dark, gradient_warm, gradient_blue |
| Account | Email | Read-only display | From Better Auth session |
| Account | Sign out | Button | Clears session, redirects to /login |

Settings removed from web app (managed server-side or not applicable):
- Cache directory (server-side concern)
- Output directory (server-side concern)
- Database path (managed by Neon)

#### Actions

| Action | Trigger | Behavior |
|---|---|---|
| Save settings | Auto-save on change or "Save" button | PATCHes user settings via API |
| Sign out | "Sign Out" button | Invalidates session, redirects to `/login` |

---

## 6. Audio Feature Requirements

### 6.1 Playback architecture

Audio files are stored in Cloudflare R2. The Python API provides signed URLs or proxies audio streams. The browser plays audio using the Web Audio API.

**Recommended implementation:** Native `<audio>` element (simpler, better mobile support) with custom controls UI. Fall back to Howler.js if cross-browser codec support is needed.

### 6.2 Playback capabilities

| Feature | Requirement |
|---|---|
| Play / Pause / Stop | Required |
| Seek (scrub) | Required |
| Skip ±10 seconds | Required |
| Volume control | Optional (v1) |
| Background playback (mobile) | Nice-to-have (Media Session API) |

### 6.3 Transition preview

Transition preview is server-side: the Python API calls `AudioEngine.preview_transition()`, caches the result, and returns a URL. The browser plays the resulting clip.

Request: `POST /api/songsets/[id]/items/[itemId]/preview-transition`  
Response: `{ "preview_url": "...", "duration_seconds": 30 }`

### 6.4 Audio states

The browser maintains a single global audio player instance (React context or Zustand store). Only one audio source plays at a time. Navigating away pauses (does not stop) current audio so the user can resume.

---

## 7. Export Pipeline

### 7.1 Flow

```
Browser                 Next.js/Python API          Analysis Service
   │                          │                           │
   │── POST /export ──────────>│                           │
   │                          │── POST /jobs ────────────>│
   │                          │<─ { job_id } ─────────────│
   │<── { job_id } ──────────│                           │
   │                          │                           │
   │── GET /export/[id]/stream─>│ (SSE)                    │
   │                          │── GET /jobs/[id] ─────────>│
   │<──── SSE events ─────────│<─ progress ───────────────│
   │         ...              │         ...               │
   │<──── complete ───────────│<─ done ───────────────────│
   │                          │                           │
   │── GET /export/[id]/files─>│                           │
   │<── { audio_url, video_url}│                           │
```

### 7.2 SSE event format

```json
{ "state": "downloading", "step": 2, "total_steps": 5, "percent": 40, "description": "Downloading assets..." }
{ "state": "completed", "step": 5, "total_steps": 5, "percent": 100, "elapsed_seconds": 154 }
{ "state": "failed", "error": "FFmpeg not found" }
```

### 7.3 Job persistence

Export jobs survive page refresh (job_id stored in URL or localStorage). On return to `/songsets/[id]/export`, the page reconnects to the SSE stream if the job is still running, or shows the completed/failed state.

---

## 8. Responsive Design Guidelines

### 8.1 Breakpoints

| Breakpoint | Width | Target devices |
|---|---|---|
| Mobile | < 768px | Phones, small tablets |
| Tablet | 768px–1024px | iPads, Android tablets |
| Desktop | > 1024px | Laptop/desktop browsers |

Mobile is the primary design target (worship leaders use tablets/phones on stage).

### 8.2 Typography

- Current lyric (lyrics preview): `text-3xl font-bold` mobile, `text-5xl font-bold` desktop
- Next lyric: `text-xl text-muted` mobile, `text-2xl text-muted` desktop
- Body text: `text-base` (16px minimum for readability under stage lighting)

### 8.3 Touch targets

All interactive elements: minimum 48×48px. Destructive actions require a second confirmation tap.

### 8.4 Gestures

| Gesture | Element | Action |
|---|---|---|
| Swipe left on song card | Song card in editor | Reveal "Edit Transition" + "Remove" actions |
| Swipe down on drawer | Transition detail drawer | Dismiss / cancel |
| Long-press on songset card | Songset list | Context menu (rename, delete) |
| Pull-to-refresh | Songset list | Reload songsets |
| Drag handle | Song card in editor | Reorder |

### 8.5 Offline / network considerations

- Show loading skeletons during data fetches
- Toast errors for network failures with "Retry" action
- Audio playback requires network (no offline caching in v1)

---

## 9. Data Requirements (API Shape)

This section describes the data each screen needs. Not a DB schema — that is a separate concern.

### 9.1 Songset

```typescript
interface Songset {
  id: string;
  name: string;
  description: string | null;
  songCount: number;
  updatedAt: string; // ISO 8601
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
  songKey: string | null;         // musical_key from Song
  songComposer: string | null;
  songLyricist: string | null;
  songAlbumName: string | null;
  recordingHashPrefix: string | null;
  displayKey: string | null;      // recording key preferred, else song key
  tempoBpm: number | null;
  durationSeconds: number | null;
  formattedDuration: string | null; // "4:32"
  isOrphan: boolean;              // recording/song missing from catalog
  // Transition parameters
  gapBeats: number;               // default 2.0
  crossfadeEnabled: boolean;
  crossfadeDurationSeconds: number | null;
  keyShiftSemitones: number;      // default 0
}
```

### 9.3 Song (catalog)

```typescript
interface Song {
  id: string;
  title: string;
  titlePinyin: string | null;
  composer: string | null;
  lyricist: string | null;
  albumName: string | null;
  musicalKey: string | null;
  hasRecording: boolean;
  hasAnalysis: boolean;
  hasLrc: boolean;
  // From recording (if available):
  recordingHashPrefix: string | null;
  tempoBpm: number | null;
  durationSeconds: number | null;
  formattedDuration: string | null;
  displayKey: string | null;
}
```

### 9.4 Export job

```typescript
interface ExportJob {
  id: string;
  songsetId: string;
  status: 'queued' | 'preparing' | 'downloading' | 'generating_audio' | 'generating_video' | 'finalizing' | 'completed' | 'failed' | 'cancelled';
  step: number;
  totalSteps: number;
  percentComplete: number;
  description: string;
  errorMessage: string | null;
  audioUrl: string | null;   // populated on completion
  videoUrl: string | null;   // populated on completion
  elapsedSeconds: number | null;
}
```

### 9.5 User settings

```typescript
interface UserSettings {
  defaultGapBeats: number;         // 2.0
  defaultVideoTemplate: string;    // "dark"
}
```

---

## 10. Non-Functional Requirements

| Requirement | Target |
|---|---|
| Initial page load (LCP) | < 2.5s on 4G mobile |
| Time to interactive | < 4s on mobile |
| Audio playback start latency | < 1s after tap |
| Accessibility | WCAG 2.1 AA (keyboard nav, ARIA labels, sufficient contrast) |
| Browser support | Chrome 110+, Safari 16+, Firefox 120+, Chrome Android, Safari iOS |
| Minimum font size | 16px (stage lighting readability) |
| Touch target size | ≥ 48×48px |

---

## 11. Feature-to-Screen Mapping (TUI → Web)

| TUI Screen | Web Route | Notes |
|---|---|---|
| Songset List | `/songsets` | Cards replace DataTable rows |
| Songset Editor | `/songsets/[id]` | Drag reorder replaces `,`/`.` keys |
| Browse | `/songsets/[id]/browse` | Debounced search replaces Enter-triggered search |
| Transition Detail | Drawer on mobile, `/…/transition` on desktop | Sliders replace plain inputs |
| Lyrics Preview | `/songsets/[id]/items/[itemId]/lyrics` | `<audio>` + LRC parser replaces miniaudio |
| Export Progress | `/songsets/[id]/export` | SSE replaces Textual callbacks; adds config step |
| Settings | `/settings` | Fewer fields (server manages cache/output dirs) |

| TUI Feature | Web equivalent |
|---|---|
| Keyboard shortcuts | Desktop keyboard shortcuts preserved; mobile uses buttons/gestures |
| `self.notify()` toasts | Toast component (sonner or react-hot-toast) |
| DataTable row selection | Highlighted card / row |
| Textual async workers | Server-side SSE + Python API background threads |
| miniaudio playback | Web Audio API / `<audio>` element |
| Turso replica sync | Replaced by Neon Postgres; sync button triggers catalog refresh |
| Local SQLite songsets DB | Neon Postgres (server-side, per user) |
