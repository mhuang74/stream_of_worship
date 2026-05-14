# Stream of Worship — Web App UI Requirements v2

**Source:** Redesigned from v1 spec and TUI codebase (`src/stream_of_worship/app/`)  
**Target:** Next.js (App Router) hosted on Vercel  
**Date:** 2026-05-10  
**Supersedes:** `specs/webapp_ui_requirements.md`

---

## 1. Overview & Goals

The Stream of Worship Web App is a browser-based tool for worship leaders to browse a song catalog, assemble songsets, preview audio and lyrics, and export final audio + video files. It replaces the Textual TUI with a mobile-first interface accessible from tablets on stage.

### Design philosophy

- **Mobile-first, tablet-primary.** The primary persona is a worship leader using a 10–11" tablet in portrait orientation, on stage or backstage. Glanceability and large touch targets are non-negotiable. Desktop is a secondary editing environment.
- **Notion/Linear aesthetic.** Clean whitespace, hairline dividers, muted section labels, minimal chrome. Content-first typography. No decorative cards with shadows or gradients.
- **Sheets over routes.** Sub-features (browse, transition editing, lyrics) live as overlay layers within the editor — not separate pages. Fewer page navigations means less context loss and a faster feel.
- **Touch-first interactions.** Every action has a tap/swipe/gesture trigger. Keyboard shortcuts are a narrow progressive enhancement for desktop.

### Key goals

- **Multi-device access** — replace the desktop-only TUI with a responsive web app
- **Multi-user** — each worship leader has their own songsets; catalog is shared and managed by admins
- **Full feature parity** — all TUI capabilities preserved (playback, transition preview, export)
- **Familiar UX** — standard mobile patterns (sheets, FAB, swipe actions, drag reorder)

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
| Audio playback | Web Audio API / native `<audio>` element |
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
/login              → Public: OAuth sign-in page
/                   → Protected: redirects to /songsets
/songsets           → Protected
/songsets/[id]      → Protected (owner check)
/songsets/[id]/export → Protected (owner check)
/settings           → Protected
```

### 3.3 User data isolation

Each user owns their own songsets. The Python API enforces ownership on all songset/item operations. The catalog (songs, recordings) is shared read-only across all users.

---

## 4. Navigation & Routing

### 4.1 Route structure

```
/login                      Login page
/songsets                   Songset list (home)
/songsets/[id]              Songset editor  ← browse, transition, lyrics are sheets/overlays here
/songsets/[id]/export       Export config + progress
/settings                   App settings + catalog sync
```

**What changed from v1:** `/songsets/[id]/browse`, `/songsets/[id]/items/[itemId]/transition`, and `/songsets/[id]/items/[itemId]/lyrics` are no longer separate routes. They are overlay layers within `/songsets/[id]`. This eliminates context loss on navigation and lets the global audio player persist without remounting.

### 4.2 Navigation patterns

**Mobile (<768px):** Top app bar with page title and back arrow. No bottom tab bar — the songset editor has its own bottom action bar. Navigation flows as a stack via browser history.

**Desktop (≥768px):** Same top app bar. Sheets become side panels (slide in from right). Song list becomes a table layout with column headers.

### 4.3 Navigation flow

```
/login
  └── /songsets (home, after auth)
        ├── /settings
        └── /songsets/[id]  (editor)
              ├── Browse Sheet (overlay, no URL change)
              ├── Transition Sheet (overlay, no URL change)
              ├── Lyrics Overlay (overlay, no URL change)
              └── /songsets/[id]/export
```

### 4.4 State passing

Use URL params for page-level state (e.g., export job ID). Sheet/overlay state is local React state within the editor page. Use React Query for server state caching.

---

## 5. Screen Requirements

### 5.1 Songset List — `/songsets`

**Purpose:** Landing screen. Shows the user's songsets as a clean list. Create, open, or delete songsets.

#### Layout (mobile)

```
┌─────────────────────────────────┐
│  Stream of Worship    [avatar]  │  ← top app bar, minimal
├─────────────────────────────────┤
│  Sunday Morning Worship      >  │  ← row with chevron
│  5 songs · 2 days ago           │
│  ─────────────────────────────  │  ← hairline divider
│  Evening Set                 >  │
│  3 songs · Today                │
│  ─────────────────────────────  │
│  (empty space)                  │
│                                 │
│                           [+]   │  ← FAB, bottom-right
└─────────────────────────────────┘
```

#### Layout (desktop)

Max-width 640px container, centered. Rows gain a third column showing description. FAB replaced by an inline "+ New songset" button at top-right of the header row. No sidebar.

#### Songset row

Each row displays:
- Songset name (semibold)
- Song count + relative updated-at timestamp (muted, smaller)
- Tap/click navigates to `/songsets/[id]`
- Long-press (mobile) or right-click (desktop) → context menu: **Rename**, **Duplicate**, **Delete**

#### Actions

| Action | Trigger | Behavior |
|---|---|---|
| Open songset | Tap row | Navigate to `/songsets/[id]` |
| Create songset | FAB `+` (mobile) / `+ New songset` (desktop) | Creates songset with default name "New Songset", navigates to editor |
| Rename | Context menu → Rename | Inline text input replaces name in row; save on blur or Enter |
| Duplicate | Context menu → Duplicate | Creates a copy with "Copy of …" prefix; refreshes list |
| Delete | Context menu → Delete | Confirmation dialog ("Delete 'X'? This cannot be undone."), then delete + remove from list |
| Pull-to-refresh | Pull down on list | Reloads songset list |

#### Empty state

Centered "No songsets yet" message + "Create your first songset" button.

---

### 5.2 Songset Editor — `/songsets/[id]`

**Purpose:** Core editing screen. Manage the ordered list of songs, preview audio, and initiate export. Browse, transition editing, and lyrics are overlay layers within this screen.

#### Layout (mobile)

```
┌─────────────────────────────────┐
│  ←  Sunday Morning Worship  ··· │  ← back, inline-editable title, overflow menu
├─────────────────────────────────┤
│  ≡  1  How Great Is Our God     │  ← drag handle, position, title
│        G · 72 BPM · 4:32        │    metadata line
│        2.0b gap                 │    transition summary
│  ─────────────────────────────  │
│  ≡  2  Cornerstone              │
│        E · 68 BPM · 5:14        │
│        2.0b gap · crossfade 4s  │
│  ─────────────────────────────  │
│  ≡  3  Great Are You Lord       │
│        C · 80 BPM · 3:45        │
│        (last song)              │
│                                 │
├─────────────────────────────────┤
│  [+ Add]   [▶ Play]  [Export →] │  ← bottom action bar
├─────────────────────────────────┤
│  How Great Is Our God           │  ← playback bar (when active)
│  ◀◀  01:23  ████░░░░  04:32  ▶▶ │
└─────────────────────────────────┘
```

#### Layout (desktop)

Song list renders as a table with columns: `#`, `Song`, `Key`, `BPM`, `Duration`, `Gap`, `Transition`. Hover on a row reveals inline icon buttons: lyrics (♪), transition (↔), remove (×). Drag handle appears on hover at left. Bottom action bar moves to a toolbar above the table. Sheets become side panels that slide in from the right.

#### Song row

| Field | Source |
|---|---|
| # | `position` |
| Song title | `songTitle` |
| Key | `displayKey` |
| BPM | `tempoBpm` |
| Duration | `formattedDuration` |
| Gap/transition summary | `gapBeats` + "b gap", + "crossfade Xs" if enabled |

Orphan items (song/recording missing) shown with a warning icon (⚠) and italicized title.

#### Song row interactions (mobile)

- **Tap row** → open **Transition Sheet** (§5.2b) for that item
- **Tap song title text** (tap the text specifically, not the whole row) → open **Lyrics Overlay** (§5.2c)
- **Swipe left** → reveal **Remove** button (destructive, red). Confirmation tap required.
- **Drag `≡` handle** → reorder via drag-and-drop; calls reorder API on drop

#### Song row interactions (desktop)

- **Click row** → open Transition side panel
- **Click ♪ icon** → open Lyrics Overlay
- **Click × icon** → remove song (with confirmation popover)
- **Drag handle** → reorder

#### Songset metadata

- **Name:** Inline text input in the app bar. Saves on blur or Enter. Validates non-empty.
- **Description:** Accessible via `···` overflow menu → "Edit description" bottom sheet (mobile) or inline below name (desktop).

#### Bottom action bar

| Button | Behavior |
|---|---|
| `+ Add` | Open Browse Sheet (§5.2a) |
| `▶ Play` | Play songset preview from position 1 (or resume if paused); audio bar appears |
| `Export →` | Navigate to `/songsets/[id]/export` (validates ≥1 song) |

#### Overflow menu (`···`)

Edit description, Duplicate songset, Delete songset.

#### Keyboard shortcuts (desktop progressive enhancement only)

| Key | Action |
|---|---|
| `Space` | Toggle playback |
| `←` `→` | Seek −10s / +10s |

No other keyboard shortcuts.

---

### 5.2a Browse Sheet (within Editor)

**Trigger:** `+ Add` button in the editor bottom action bar.

**Presentation:** Slides up from the bottom. Covers ~85% of the viewport. Dismissible by swipe-down or pressing `×`. The song list and playback bar remain visible behind the sheet.

#### Layout (mobile)

```
┌─────────────────────────────────┐
│  ────────  (drag indicator)     │
│  Browse Songs               [×] │
├─────────────────────────────────┤
│  [🔍 Search songs…]    [All ▾]  │  ← search input + field filter chip
├─────────────────────────────────┤
│  How Great Is Our God       [+] │  ← title + inline add button
│  G · 72 BPM · 4:32              │
│  ─────────────────────────────  │
│  Cornerstone                [✓] │  ← already-added song shows checkmark
│  E · 68 BPM · 5:14              │
│  ─────────────────────────────  │
│  Great Are You Lord         [+] │
│  C · 80 BPM · 3:45              │
└─────────────────────────────────┘
```

#### Desktop adaptation

Sheet becomes a side panel that slides in from the right, ~360px wide. Song list scrolls independently.

#### Search behavior

- Debounced input (300ms) — results update automatically as the user types
- **Field filter chip** cycles: All → Title → Lyrics → Composer → All …
- Default shows only songs with recordings (`hasAnalysis: true`)
- "Show all" toggle in the filter chip to include unanalyzed songs
- "×" in search input clears and resets to full list

#### Song list

Each row: title, key, BPM, duration. No album or metadata beyond what fits one line.

- **`[+]` button** → adds song to the songset immediately; button changes to `[✓]` (checkmark). No "select then add" two-step.
- Added songs remain visible in the list (user may want to add the same song again).

#### Empty states

- No results: "No songs found" with guidance.
- Empty catalog: "Catalog not yet synced — ask your admin to run `sow-admin sync`."

#### Actions

| Action | Trigger | Behavior |
|---|---|---|
| Search | Type in input | Debounced API call, updates list |
| Change field | Tap field chip | Cycles through field options, re-searches |
| Add song | `[+]` button on row | POSTs item to songset; button → `[✓]`; toast "Added 'X'" |
| Preview audio | Tap song title (long-press) | Plays audio for that song; audio bar appears |
| Dismiss | Swipe down or `[×]` | Closes sheet; editor song list refreshes to show added songs |

---

### 5.2b Transition Sheet (within Editor)

**Trigger:** Tap a song row in the editor (not the title — the row body).

**Presentation:** Bottom sheet, covers ~50% of the viewport. Dismissible by swipe-down or `×`. **Changes auto-save on dismiss** — no explicit Save button. An undo toast ("Transition updated. Undo") appears for 5 seconds after dismiss.

#### Layout

```
┌─────────────────────────────────┐
│  ──────── (drag indicator)      │
│  Transition into "Cornerstone"  │
│  After "How Great Is Our God"   │
├─────────────────────────────────┤
│  Gap (beats)                    │
│  [────────○──────────]  2.0    │  ← slider + numeric display
│                                 │
│  Crossfade                      │
│  [●────────────────]  OFF      │  ← toggle switch
│                                 │
│  Crossfade Duration (s)         │
│  [───────────────────]  4.0    │  ← disabled when crossfade off
│                                 │
│  Key Shift (semitones)          │
│  [−3][−2][−1][ 0][+1][+2][+3]  │  ← segmented pill control
├─────────────────────────────────┤
│  [Preview transition ▶]         │  ← generates and plays transition audio
└─────────────────────────────────┘
```

#### Fields

| Field | Type | Default | Constraint |
|---|---|---|---|
| Gap (beats) | Slider + numeric display | `2.0` | 0.0–16.0, step 0.5 |
| Crossfade enabled | Toggle | `false` | |
| Crossfade duration (s) | Slider | `4.0` | 1.0–30.0; disabled when crossfade off |
| Key shift (semitones) | Segmented pill | `0` | −6 to +6 |

#### First song behavior

The first song in a songset has no previous song. The sheet shows: "This is the first song — no transition." Only the Key Shift field is shown (it affects how the audio engine processes the recording).

#### Actions

| Action | Trigger | Behavior |
|---|---|---|
| Preview | "Preview transition ▶" button | POSTs to `/api/songsets/[id]/items/[itemId]/preview-transition`, plays returned audio in the global player |
| Save | Swipe down or `×` | PATCHes item via API; shows undo toast |
| Undo | Tap "Undo" in toast | Reverts to previous values, re-PATCHes |

---

### 5.2c Lyrics Overlay (within Editor)

**Trigger:** Tap the song title text in an editor row (or the ♪ icon on desktop).

**Presentation:** Full-screen overlay, slides up. Has a two-mode toggle pill: **Stage** and **Edit**. Playback auto-starts on open.

#### Stage Mode (default)

Designed for on-stage use. Maximum text size, zero metadata chrome. Tap anywhere to toggle controls visibility.

```
┌─────────────────────────────────┐
│                            [×]  │  ← close (fades after 3s idle)
│        [Stage | Edit]           │  ← mode toggle pill
│                                 │
│                                 │
│                                 │
│   How great is our God          │  ← current lyric
│                                 │    text-4xl bold (mobile)
│   Sing with me                  │  ← next lyric, muted
│                                 │    text-2xl (mobile)
│                                 │
│  01:23 ════════░░░░░  04:32     │  ← seek bar
│     [◀◀]   [▶/⏸]   [▶▶]       │  ← controls (tap to reveal)
└─────────────────────────────────┘
```

Controls fade after 3 seconds of inactivity. Tap anywhere to reveal them again.

#### Edit Mode

Shows song metadata and the full LRC timestamp table. For verification and debugging LRC timing.

```
┌─────────────────────────────────┐
│  Cornerstone               [×]  │
│  E major · 68 BPM · 5:14        │  ← song metadata
│        [Stage | Edit]           │
├─────────────────────────────────┤
│  Time    │ Lyric                │
│  ────────┼──────────────────── │
│  00:00   │ (intro)              │
│  00:45   │ ▶ How great is…      │  ← highlighted current line
│  01:10   │   Sing with me       │
│  01:35   │   How great…         │
│  …                              │
├─────────────────────────────────┤
│  01:23 ════════░░░░░  04:32     │
│     [◀◀]   [▶/⏸]   [▶▶]       │
└─────────────────────────────────┘
```

LRC table auto-scrolls to keep the current line visible.

#### Desktop adaptation

Overlay takes the full browser window. Stage mode: lyrics centered in a large viewport. Edit mode: two-column split (current+next lyric left, LRC table right).

#### Behavior

- On open: fetch LRC and audio URL from Python API, populate LRC table, auto-start playback
- Current lyric: largest LRC timestamp ≤ current playback position
- Seek bar: visual fill (`position / duration × 100%`), scrubable
- Closing the overlay pauses playback; audio bar remains visible in the editor

#### Typography (Stage Mode)

| | Mobile | Tablet | Desktop |
|---|---|---|---|
| Current lyric | `text-4xl font-bold` | `text-5xl font-bold` | `text-6xl font-bold` |
| Next lyric | `text-2xl text-muted` | `text-3xl text-muted` | `text-3xl text-muted` |

#### Actions

| Action | Trigger | Behavior |
|---|---|---|
| Play/Pause | `▶/⏸` button or `Space` (desktop) | Toggles playback |
| Skip backward | `◀◀` button or `←` (desktop) | Seeks −10 seconds |
| Skip forward | `▶▶` button or `→` (desktop) | Seeks +10 seconds |
| Seek | Drag seek bar | Seeks to position |
| Switch mode | Toggle pill | Switches between Stage and Edit mode; remembers preference per session |
| Close | `×` button | Pauses playback, closes overlay; audio bar persists |

---

### 5.3 Export — `/songsets/[id]/export`

**Purpose:** Configure and monitor the audio + lyrics video export job. Remains a separate route because the job may take several minutes and the user may navigate away and return.

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
│  [Dark                      ▾]  │
│                                 │
│  [      Start Export      ]     │  ← full-width primary button
├─────────────────────────────────┤
│  (progress — shown after start) │
│                                 │
│  Downloading assets…            │
│  [████████░░░░░░░░░░]  40%      │
│  Step 2 of 5                    │
│                                 │
│  [          Cancel          ]   │
├─────────────────────────────────┤
│  (completion)                   │
│                                 │
│  ✓ Done in 2m 34s               │
│  [      Download Audio      ]   │
│  [      Download Video      ]   │
└─────────────────────────────────┘
```

#### Export configuration

| Field | Options | Default |
|---|---|---|
| Include audio (MP3) | Checkbox | Checked |
| Include lyrics video (MP4) | Checkbox | Checked |
| Video template | Select: Dark, Gradient Warm, Gradient Blue | From user settings |

#### Progress tracking

- "Start Export": POST to Python API → Analysis Service; receive `job_id`
- `job_id` stored in URL query param (`?job=<id>`) so the page reconnects on refresh
- Progress via SSE at `/api/export/[jobId]/stream`
- States: Preparing, Downloading, Generating Audio, Generating Video, Finalizing, Completed, Failed, Cancelled
- Display: animated progress bar, state label, "Step X of Y" counter

#### Completion states

- **Success:** Green "✓ Done in Xm Ys" + full-width Download Audio and Download Video buttons
- **Failure:** Red "✕ Export failed" + error message + full-width "Try again" button
- **Cancelled:** Grey "Export cancelled" + full-width "Export again" button

#### Actions

| Action | Trigger | Behavior |
|---|---|---|
| Start export | "Start Export" button | Validates ≥1 song; POSTs job; begins SSE stream |
| Cancel | "Cancel" button | Sends cancel request; closes SSE stream |
| Download | Download buttons | Downloads MP3/MP4 directly |
| Back | `←` | Returns to editor; export continues in background if running |

---

### 5.4 Settings — `/settings`

**Purpose:** App preferences, account management, and catalog sync. Catalog sync is here (not in the songset list app bar) to keep the home screen clean.

#### Layout

```
┌─────────────────────────────────┐
│  ←  Settings                    │
├─────────────────────────────────┤
│  PLAYBACK                       │  ← muted section label
│  Default gap (beats)      [2.0] │  ← number input, auto-save
├─────────────────────────────────┤
│  EXPORT                         │
│  Video template   [Dark      ▾] │  ← select, auto-save
├─────────────────────────────────┤
│  CATALOG                        │
│  [      Sync Catalog       ]    │  ← full-width button
│  Last synced: 2 hours ago       │
├─────────────────────────────────┤
│  ACCOUNT                        │
│  mhuang@gmail.com               │
│  [        Sign Out         ]    │
├─────────────────────────────────┤
│  APP INFO                       │
│  Version: 0.2.0                 │
└─────────────────────────────────┘
```

#### Settings fields

| Section | Field | Type | Notes |
|---|---|---|---|
| Playback | Default gap (beats) | Number input | 0.0–16.0, step 0.5; auto-save on blur |
| Export | Default video template | Select | Dark, Gradient Warm, Gradient Blue; auto-save |
| Catalog | Sync Catalog | Button | Triggers catalog sync via Python API; shows toast with result |
| Account | Email | Read-only | From Better Auth session |
| Account | Sign out | Button | Invalidates session, redirects to `/login` |

No explicit "Save" button — all fields save on change or blur.

---

## 6. Global Audio Player

### 6.1 Architecture

A single `AudioContext` instance is managed via React context and lives for the duration of the editor session. Only one audio source plays at a time.

**Recommended implementation:** Native `<audio>` element with custom controls UI. Fall back to Howler.js if cross-browser codec support is needed.

### 6.2 Playback bar

The playback bar is a fixed-position element at the bottom of the viewport, above the device safe area inset. It appears when audio is loaded and persists across sheet open/close within `/songsets/[id]`. It disappears when navigating to a different route.

On mobile the bar sits above the bottom action bar (when both are visible).

Contents:
- Song title (truncated)
- Progress indicator (thin seek bar, scrubable)
- Elapsed / total time
- Play/Pause button
- Skip −10s and +10s buttons
- Close/stop button

### 6.3 Playback sources

| Where | What plays |
|---|---|
| Editor "▶ Play" | Transition preview clip from server (`POST /api/songsets/[id]/preview`) |
| Browse Sheet long-press | Song's raw audio from R2 (via signed URL) |
| Transition Sheet "Preview ▶" | Transition preview between adjacent songs |
| Lyrics Overlay | Song's raw audio from R2 |

### 6.4 Capabilities

| Feature | Status |
|---|---|
| Play / Pause / Stop | Required |
| Seek (scrub) | Required |
| Skip ±10 seconds | Required |
| Background playback (Media Session API) | Nice-to-have |
| Volume control | Deferred to v2 |

---

## 7. Export Pipeline

### 7.1 Flow

```
Browser              Next.js/Python API        Analysis Service
   │                        │                        │
   │── POST /export ────────>│                        │
   │                        │── POST /jobs ──────────>│
   │                        │<─ { job_id } ───────────│
   │<── { job_id } ─────────│                        │
   │                        │                        │
   │── GET /export/[id]/stream ─>│ (SSE)              │
   │                        │── poll /jobs/[id] ─────>│
   │<──── SSE events ────────│<─ progress ────────────│
   │         …              │         …              │
   │<──── complete ──────────│<─ done ────────────────│
   │                        │                        │
   │── GET /export/[id]/files ─>│                     │
   │<── { audio_url, video_url }│                     │
```

### 7.2 SSE event format

```json
{ "state": "downloading", "step": 2, "total_steps": 5, "percent": 40, "description": "Downloading assets…" }
{ "state": "completed", "step": 5, "total_steps": 5, "percent": 100, "elapsed_seconds": 154 }
{ "state": "failed", "error": "FFmpeg not found" }
```

### 7.3 Job persistence

`job_id` is stored in the URL query param (`?job=<id>`). On return to `/songsets/[id]/export`, the page reconnects to the SSE stream if the job is still running, or shows the completed/failed state.

---

## 8. Responsive Design

### 8.1 Breakpoints

| Breakpoint | Width | Notes |
|---|---|---|
| Mobile | < 768px | Phones, small tablets; primary target |
| Tablet | 768px–1024px | iPads, Android tablets |
| Desktop | > 1024px | Laptop/desktop browsers; secondary |

### 8.2 Typography

| Context | Mobile | Tablet | Desktop |
|---|---|---|---|
| Stage lyric (current) | `text-4xl font-bold` | `text-5xl font-bold` | `text-6xl font-bold` |
| Stage lyric (next) | `text-2xl text-muted` | `text-3xl text-muted` | `text-3xl text-muted` |
| Song title in list | `text-base font-semibold` | `text-base font-semibold` | `text-sm` (table) |
| Metadata / secondary | `text-sm text-muted` | `text-sm text-muted` | `text-sm text-muted` |
| Minimum body text | 16px | 16px | 14px |

16px minimum for body text on mobile — stage lighting readability.

### 8.3 Touch targets

All interactive elements: minimum 48×48px on mobile/tablet. Destructive actions (Delete, Remove) require a second confirmation tap. On desktop: minimum 32×32px.

### 8.4 Gestures

| Gesture | Element | Action |
|---|---|---|
| Tap | Song row (body) | Open Transition Sheet |
| Tap | Song title text | Open Lyrics Overlay |
| Swipe left | Song row in editor | Reveal Remove button |
| Swipe down | Browse or Transition sheet | Dismiss sheet |
| Drag `≡` handle | Song row in editor | Reorder songs |
| Long-press | Songset list row | Context menu (Rename, Duplicate, Delete) |
| Long-press | Song in Browse Sheet | Preview audio |
| Right-click | Any row (desktop) | Context menu |
| Pull-to-refresh | Songset list | Reload list |
| Tap anywhere | Lyrics Stage mode | Toggle controls visibility |

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
  songKey: string | null;
  songComposer: string | null;
  songLyricist: string | null;
  songAlbumName: string | null;
  recordingHashPrefix: string | null;
  displayKey: string | null;       // recording key preferred, else song key
  tempoBpm: number | null;
  durationSeconds: number | null;
  formattedDuration: string | null; // "4:32"
  isOrphan: boolean;
  // Transition parameters
  gapBeats: number;                // default 2.0
  crossfadeEnabled: boolean;
  crossfadeDurationSeconds: number | null;
  keyShiftSemitones: number;       // default 0
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
  audioUrl: string | null;
  videoUrl: string | null;
  elapsedSeconds: number | null;
}
```

### 9.5 User settings

```typescript
interface UserSettings {
  defaultGapBeats: number;        // 2.0
  defaultVideoTemplate: string;   // "dark"
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
| Minimum font size | 16px on mobile |
| Touch target size | ≥ 48×48px on touch devices |

---

## 11. Key Changes from v1

| Area | v1 | v2 | Reason |
|---|---|---|---|
| Route count | 7 routes | 4 routes | Sheets/overlays replace sub-routes; fewer page transitions |
| Browse | Separate route `/songsets/[id]/browse` | Bottom sheet within editor | Preserves editor context; no navigation needed |
| Transition editing | Separate route `/…/transition` | Bottom sheet (50% viewport) | Same page, auto-save on dismiss |
| Lyrics preview | Separate route `/…/lyrics` | Full-screen overlay with Stage/Edit modes | Optimized for on-stage use; debug table in Edit mode |
| Songset list | Cards with shadow | Clean rows with hairline dividers | Notion/Linear aesthetic; less visual noise |
| Browse add action | Select row, then "Add to Songset" button | Per-row `[+]` inline button | One tap instead of two; continuous adding |
| Transition save | Explicit Save/Cancel buttons | Auto-save on sheet dismiss | Removes friction; undo toast allows recovery |
| Keyboard shortcuts | Full TUI shortcut set (a, e, l, x, etc.) | Space + Arrow keys only | Touch-first; TUI shortcuts are muscle memory that don't apply on web |
| Catalog sync | App bar button on songset list | Button in Settings | Declutters the home screen |
| Lyrics debug table | Always visible (split layout) | Edit mode only | Stage mode is zero-chrome; debug table accessible when needed |
| Song list layout | DataTable (TUI-style) | Drag-reorderable rows (mobile) / table (desktop) | Native mobile interaction vs TUI cursor navigation |
