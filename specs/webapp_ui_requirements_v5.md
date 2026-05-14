# Stream of Worship — Web App UI Requirements v5

**Source:** Revised from v4 spec, design-decision interview (2026-05-11), and resolved open questions from v4 §14
**Target:** Next.js (App Router) hosted on Vercel (Pro plan, Fluid Compute enabled)
**Date:** 2026-05-11
**Supersedes:** `specs/webapp_ui_requirements_v4.md`

---

## 1. Overview & Goals

The Stream of Worship Web App is a browser-based tool for worship leaders and media volunteers to browse a song catalog, assemble songsets, preview audio, review lyric timing, **render** a finished MP3 + MP4 lyrics video, and play back that video during a worship session projected to a screen or TV.

### Primary personas

**Persona A — Prep persona**
A worship leader or media volunteer, **primarily on their phone** (sometimes at a desktop), preparing materials for an upcoming service:
1. Pick songs from the catalog and assemble a setlist.
2. Tune transitions (gap, crossfade; key shift + tempo tuning available on desktop).
3. Review auto-generated lyrics — mark problem lines. Correct text and timestamps directly when on desktop.
4. Render to produce an MP3 (full mix) + MP4 (lyrics video) ready for playback.

**Persona B — Worship persona**
A church small-group leader, **on their phone**, leading worship in a home or small room, projecting lyrics to a TV or large screen via an HDMI / USB-C cable or Chromecast:
1. Open the app on phone; navigate to a pre-rendered songset.
2. Verify the artifacts are downloaded and ready before worship begins.
3. Tap **▶ Play** → pre-play card loads.
4. Optionally send lyrics to TV via Presentation API (Android + Cast), or mirror the phone screen (iOS / wired HDMI).
5. Tap **Start** → controller player begins on phone; lyrics fill the TV.
6. Control playback during worship: pause, resume, skip to next/prev song, adjust volume, jump to a specific lyric line.
7. If font size is too small after projecting, re-render with a larger preset.

The render pipeline and the playback pipeline are the two primary outputs. The prep persona drives the render; the worship persona drives the playback.

### Why "Render," not "Export"

The artifact produced by this app is not a file being "exported" to another tool — it **is** the playback. Without a render there is no worship session. Calling the action "Render" aligns the mental model with the outcome: you render a songset so you can play it. The underlying pipeline is identical to v4 (same FFmpeg + canvas stack, same MP3 + MP4 + chapters.json outputs); only the language changes.

### What "stage tool" means and does not mean

Unchanged from v4 §1. Worship Playback is in scope as a media player for pre-rendered artifacts. MIDI sync, NDI output, presenter confidence monitor, live mixer remain out of scope.

### Design philosophy

- **Phone-first, desktop-power-mode.** The phone is the primary device for both prep and worship. Every screen is designed for phone first. Tablet inherits the phone layout via responsive breakpoints. Desktop (≥1024 px) unlocks a power-mode for dense QA work — dense lyrics timing review, transition key/tempo tuning, keyboard shortcuts.
- **Production tool aesthetic.** Information-dense for prep on desktop; clean and large-target for playback and phone prep.
- **Reviewability over glanceability (prep).** Users are doing focused QA. Provide context, not just the current moment.
- **Immediacy over discoverability (playback).** Once worship begins, every interaction must be a single tap. Controls auto-hide; critical actions (pause, next song) are always reachable within 1 tap.
- **Offline-first for worship artifacts.** The MP4 and MP3 must survive a venue with no Wi-Fi. Cache before worship, not during.
- **Sheets over routes (prep).** Sub-features (browse, transition editing, lyrics review) are overlay layers within the editor — not separate pages.
- **Touch-first interactions.** Every action has a tap/swipe/gesture trigger. Keyboard shortcuts are progressive enhancement for desktop only.
- **Phone-lite / desktop-full UX split.** On phone, prep surfaces are simplified: lyrics Review mode only (mark problems), gap + crossfade transition controls. On desktop (power-mode), the full v4 prep suite unlocks: Edit text, Edit timing, word-level alignment, transition key shift + tempo nudge + waveform preview.

### Key goals

- **Multi-device access** — responsive web app replaces the desktop-only TUI; phone is first-class
- **Multi-user** — each user has their own songsets; catalog is shared, managed by admins
- **Full feature parity** — all TUI capabilities (playback, transition preview, render); some at desktop-only tier
- **Lyric QA** — mark problem lines on phone; correct text and timing on desktop; corrections save as a per-user override
- **Worship playback** — fullscreen controller player on phone; dedicated lyrics-only projection surface for TV
- **Offline cache** — pre-rendered artifacts downloadable for zero-network playback
- **Semantic discovery** — search songs by message, theme, or lyric meaning using pgvector + bge-m3
- **Sharing** — send artifacts via file share or a public hosted-player link

### Out of scope for v1

Unchanged from v4 §1.

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
| Blob storage | Cloudflare R2 (audio files, LRC, render artifacts, chapter manifests) |
| Audio render | Node FFmpeg (`fluent-ffmpeg` + `ffmpeg-static`) within a Vercel Function |
| Video render | Node canvas (`skia-canvas` or `node-canvas`) + Node FFmpeg, same function |
| Real-time progress | Server-Sent Events (SSE) from the long-running render Route Handler |
| Deployment | Vercel Pro plan, Fluid Compute enabled (required for `maxDuration: 800`) |
| Offline cache | Service Worker (Workbox) + Cache Storage for MP4/MP3/chapter manifest |
| Vector search | `pgvector` extension on Neon; `song_embedding` table |
| Embedding model | **`bge-m3`** (multilingual Chinese+English, ≤1024 dims, open-weights) |
| Embedding batch | Analysis Service Docker (offline, bge-m3 via Python fastembed) |
| Embedding query | **`fastembed-js`** + ONNX in a separate Vercel Edge Function (`/api/embed`) — keeps main app bundle small; isolates ONNX cold-start to the embed path |
| Public share | `/share/[token]` route — unauthenticated; signed R2 URL behind `songset_share` table |
| Second screen | Presentation API (Android Chrome + Cast targets); mirror fallback for iOS/wired HDMI |

### 2.2 System boundary

Unchanged from v4 §2.2. The web app has zero runtime dependencies on the Analysis Service.

### 2.3 Component responsibilities

Unchanged from v4 §2.3, with the following update:

- **Next.js (Vercel)** additionally owns: query embedding via `fastembed-js` + ONNX (bge-m3); Presentation API session brokering (projection route).
- **Analysis Service (offline)** additionally owns: song embedding generation (bge-m3 via Python fastembed).

### 2.4 Render rendering constraints

Unchanged from v4 §2.4 ("Export rendering constraints"). Font size presets and title card add no material render time.

### 2.5 Offline layer

Unchanged from v4 §2.5.

**iOS minimum for offline:** iOS 17.4+. Versions below 17.4 show an "Update iOS for offline support" banner; the "Make available offline" checkbox is disabled. Online playback (streaming from R2) still works on iOS 16+.

### 2.6 Semantic search layer

**Embedding model:** `bge-m3`. Multilingual (Chinese + English), 1024 dimensions, open-weights (Apache 2.0). Runs in the Analysis Service Docker container via Python `fastembed`. The same model is used for query embedding via `fastembed-js` (ONNX runtime, CPU-only) in a dedicated Vercel Edge Function (`/api/embed`), ensuring that song vectors and query vectors occupy the same embedding space.

**Implementation note:** The query embedding runs in a dedicated Vercel Edge Function at `/api/embed`. The bge-m3 ONNX model file is bundled into that function only, keeping the main Next.js function bundle well under Vercel's compressed limit. The route handler at `POST /api/songs/search/semantic` calls `/api/embed` internally; the external interface does not change.

All other semantic search behavior is unchanged from v4 §2.6.

### 2.7 Share token layer

Unchanged from v4 §2.7.

### 2.8 Schema and migrations

Unchanged from v4 §2.8, plus:

**`song_embedding` table** — unchanged from v4 (vector size 1024, model_version column stores `"bge-m3-v1.0"`).

**`songsets` table additions (beyond v4):**
```sql
last_failed_render_job_id TEXT REFERENCES render_jobs(id)  -- NULL if last job succeeded/none
```

This powers the "Retry render" + "View error" button state when the last job failed.

**`render_jobs` table** — column names are unchanged from v4 (the `render_jobs` name was already correct at the DB layer; v4 used "export" in the spec text inconsistently). Column `font_size_preset`, `include_title_card`, `title_card_duration_seconds`, `chapters_r2_key` are as defined in v4 §2.8.

---

## 3. Authentication

Unchanged from v4 §3.

---

## 4. Navigation & Routing

### 4.1 Route structure

```
/login                            Login page
/share/[token]                    Public hosted-player (unauthenticated)
/share/[token]/play/projection    Public projection surface (unauthenticated)
/songsets                         Songset list (home)
/songsets/[id]                    Songset editor  ← browse, transition, lyrics are sheets/overlays
/songsets/[id]/render             Render config + progress  (renamed from /export)
/songsets/[id]/play               Play screen: pre-play card + controller player  (renamed from /worship)
/songsets/[id]/play/projection    Projection surface: lyrics-only (new)
/settings                         App settings + catalog sync + shared links
```

### 4.2 Navigation patterns

Unchanged from v4 §4.2, except:

- `/songsets/[id]/render` replaces `/songsets/[id]/export`.
- `/songsets/[id]/play` replaces `/songsets/[id]/worship`.
- `/songsets/[id]/play/projection` is a new fullscreen route, typically opened by the Presentation API on a second screen. On desktop it can be opened manually and dragged to an external monitor.
- `/share/[token]/play/projection` mirrors the authenticated projection route for hosted-player recipients.

### 4.3 Navigation flow

```
/login
  └── /songsets (home, after auth)
        ├── /settings
        └── /songsets/[id]  (editor)
              ├── Browse Sheet (overlay, no URL change)
              │     └── Describe mode (semantic search via bge-m3)
              ├── Transition Panel (inline expand, no URL change)
              ├── Lyrics Review (overlay, no URL change)
              │     └── Desktop only: Edit text / Edit timing modes
              ├── /songsets/[id]/render
              │     └── (post-render: Share dialog, Make available offline)
              └── /songsets/[id]/play
                    ├── (pre-play card)
                    │     └── "Send lyrics to TV" → opens /play/projection on second screen
                    └── (fullscreen controller player)
                          └── /songsets/[id]/play/projection (second screen / opened by Presentation API)

/share/[token]  (no auth required)
  └── /share/[token]/play/projection  (no auth required)
```

### 4.4 State passing

Unchanged from v4 §4.4. URL params for page-level state: render job ID (`?job=<id>`). Sheet/overlay state is local React state. React Query for server state caching.

---

## 5. Screen Requirements

### 5.1 Songset List — `/songsets`

**Purpose:** Landing screen. Shows the user's songsets with render state, offline status, and a single adaptive primary action button.

#### Layout (phone)

```
┌─────────────────────────────────┐
│  Stream of Worship    [avatar]  │  ← top app bar
├─────────────────────────────────┤
│  Sunday Morning Worship      >  │  ← row; tap body → editor
│  5 songs · 23m 14s · 2 days ago │
│  [✈ Offline]                    │
│  [  ▶ Play          ]           │  ← primary action button (full width on phone)
│  ─────────────────────────────  │
│  Evening Set                 >  │
│  3 songs · 18m 02s · Today      │
│  ⚠ Artifacts out of date        │
│  [  Re-render       ] [▶ Play anyway ▸]
│  ─────────────────────────────  │
│  New Worship Set             >  │
│  0 songs · —                    │
│  [  Render          ]           │  ← primary action: no render yet
│  ─────────────────────────────  │
│  (empty space)                  │
│                           [+]   │  ← FAB
└─────────────────────────────────┘
```

#### Render/Play state-machine button

Each songset row shows one primary action button (and optionally a secondary) derived from the songset's render state:

| Render state | Primary button | Secondary | Primary action |
|---|---|---|---|
| `unrendered` — no render job ever | **Render** | — | → `/songsets/[id]/render` |
| `rendering` — active job in progress | **Rendering… 42%** (disabled, tappable) | Cancel (icon) | → `/songsets/[id]/render` (shows progress) |
| `fresh` — render complete, no changes since | **▶ Play** | ··· (Re-render in menu) | → `/songsets/[id]/play` |
| `stale` — render complete, but edits since | **Re-render** | **▶ Play anyway** | primary → `/songsets/[id]/render`; secondary → `/songsets/[id]/play` |
| `failed` — last job failed | **Retry render** | View error | → `/songsets/[id]/render` |

The `renderState` field is computed server-side from `latestRenderJobId`, `lastFailedRenderJobId`, `artifactsOutOfDate`, and the active job status.

#### Songset row (metadata)

- Songset name (semibold)
- Song count + total duration + relative updated-at timestamp (muted, smaller)
- **`✈ Offline` badge** — shown when artifacts are cached offline and up to date
- **`⚠ Artifacts out of date` indicator** — shown when render is stale
- Tap/click row body → navigate to `/songsets/[id]`
- Long-press (mobile) or right-click (desktop) → context menu

#### Context menu

**Rename**, **Duplicate**, **Render**, **Play**, **Share…**, **Delete**

#### Actions

| Action | Trigger | Behavior |
|---|---|---|
| Open songset | Tap row body | Navigate to `/songsets/[id]` |
| Create songset | FAB `+` | Creates with default name "New Songset", navigates to editor |
| Rename | Context menu → Rename | Inline text input; save on blur or Enter |
| Duplicate | Context menu → Duplicate | Creates copy with "Copy of …" prefix; refreshes list |
| Render | Action button / context menu | Navigate to `/songsets/[id]/render` |
| Play | Action button / context menu | Navigate to `/songsets/[id]/play` |
| Share… | Context menu → Share… | Opens Share dialog (§7.2) |
| Delete | Context menu → Delete | Confirmation dialog, then delete + remove from list |

#### Empty state

Centered "No songsets yet" + "Create your first songset" button.

---

### 5.2 Songset Editor — `/songsets/[id]`

The editor app bar carries the same Render/Play state-machine button as the list row, so the primary action is always reachable without leaving the editor.

**Phone layout:**

```
┌─────────────────────────────────┐
│  ←  Sunday Morning Worship  ··· │  ← ··· = overflow menu
│  ⚠ Render updated · re-render   │  ← stale banner (tap → /render); dismissible
│    or play anyway           [×] │
│  [  Re-render  ] [▶ Play anyway]│  ← action buttons below banner
├─────────────────────────────────┤
│  📝 2 marked lines               │  ← marks badge; tap → lyrics review
│  [Open on desktop for text edit]│  ← nudge when marked lines exist (phone only)
├─────────────────────────────────┤
│  Song list (drag handles)       │
│  1. How Great Is Our God  4:32  │
│     G · 72 BPM  [transition ▾]  │
│  2. Cornerstone           5:14  │
│     E · 68 BPM  [transition ▾]  │
│  …                              │
├─────────────────────────────────┤
│  [+ Browse Songs]               │  ← opens Browse Sheet
└─────────────────────────────────┘
```

**Overflow menu (`···`):** Render, Play, Edit description, Duplicate songset, Delete songset.

**"Open on desktop for text edit" nudge:** Shown on phone breakpoints only when `songset.markedLineCount > 0`. Tapping copies the URL to clipboard and shows "URL copied — open on a desktop to fix marked lyrics." Dismissible for the session.

**Desktop layout (power-mode, ≥1024px):**

Identical to the v4 editor layout (side-by-side song list + transition panel). The mark nudge is hidden. All prep features are accessible: Edit text, Edit timing, transition key shift + tempo.

---

### 5.2a Browse Sheet (within Editor)

Unchanged from v4 §5.2a. The Describe mode still calls `POST /api/songs/search/semantic`; the server-side implementation now uses `fastembed-js` + bge-m3 ONNX for query embedding.

---

### 5.2b Transition Detail Sheet

**Phone (simplified):**

Controls exposed on phone:
- Gap: numeric stepper (± 0.5 beats) + display in seconds at current BPM
- Crossfade: toggle (on/off)
- Audio preview of the transition (tap to play)

**Desktop (power-mode):**

All phone controls plus:
- Key shift: semitone selector (−6 to +6)
- Tempo nudge: ± BPM with live preview
- Waveform preview panel

---

### 5.2c Lyrics Review

**Phone (Review mode only):**

```
┌─────────────────────────────────┐
│  ────────  (drag indicator)     │
│  Lyrics — How Great Is Our God  │
│                             [×] │
├─────────────────────────────────┤
│  0:28  My hope is built     [🔖]│  ← tap 🔖 to mark/unmark this line
│  0:35  on nothing less      [🔖]│
│  0:41  Than Jesus' blood    [🔖]│  ← marked (filled icon)
│  0:51  and righteousness    [🔖]│
│  …                              │
├─────────────────────────────────┤
│  📝 1 marked in this song       │
│  [Open on desktop to fix ›]     │
└─────────────────────────────────┘
```

- Phone shows only the **Review** tab: scroll through LRC lines, tap 🔖 to flag a problem.
- No in-line text or timing editing on phone.
- A persistent footer prompts desktop for fixes when marks exist.

**Desktop (full, power-mode):**

Three tabs: **Review** | **Edit text** | **Edit timing** — unchanged from v4 §5.2c.

---

### 5.3 Render — `/songsets/[id]/render`

Renamed from "Export." Options and pipeline are identical to v4 §5.3 ("Export"), with the following copy changes:

| v4 label | v5 label |
|---|---|
| "Start Export" | "Start Render" |
| "EXPORT OPTIONS" | "RENDER OPTIONS" |
| "AFTER EXPORT" | "AFTER RENDER" |
| "Done in 7m 42s" | "Rendered in 7m 42s" |
| Phase labels (SSE) | unchanged (internal: `mixing_audio`, etc.) |

#### Layout (phone)

```
┌─────────────────────────────────┐
│  ←  Render                      │
├─────────────────────────────────┤
│  ─────  RENDER OPTIONS  ─────── │
│  [×] Audio (MP3)                │
│  [×] Lyrics video (MP4)         │
│                                 │
│  Video template                 │
│  [preview thumbnail] Dark    ▾  │
│                                 │
│  Lyrics font size               │
│  (○) S   (●) M   (○) L   (○) XL│
│                                 │
│  Output resolution              │
│  (●) 720p  (faster, default)    │
│  ( ) 1080p (sharper)            │
│    Est. render: ~8 min          │
│                                 │
│  ─────  TITLE CARD  ───────────  │
│  [ ] Include opening title card │
│      Duration: [10s ▾]          │
│  ┌─────────────────────────┐    │
│  │  Sunday Morning Worship │    │
│  │  "Isaiah 40:31"         │    │
│  └─────────────────────────┘    │
│                                 │
│  ─────  AFTER RENDER  ─────────  │
│  [×] Make available offline     │
│                                 │
│  🔖 2 marked lines · 1 song     │
│     [Review ›]                  │
│                                 │
│  [      Start Render      ]     │
├─────────────────────────────────┤
│  (progress — shown after start) │
│  Mixing audio…                  │
│  [████████░░░░░░░░░░]  40%      │
│  Phase 1 of 5 · ~6 min left     │
│  [          Cancel          ]   │
├─────────────────────────────────┤
│  (completion)                   │
│  ✓ Rendered in 7m 42s           │
│  [      Download Audio      ]   │
│  [      Download Video      ]   │
│  [      Share…              ]   │
└─────────────────────────────────┘
```

All render configuration, font size pixel values, title card behavior, pre-render validation, SSE event format, and post-render actions are unchanged from v4 §5.3 ("Export"). "Make available offline" checkbox behavior: iOS 17.4+ required; on older iOS the checkbox is disabled with a tooltip "Update iOS for offline support."

---

### 5.4 Settings — `/settings`

Same as v4 §5.4 with:
- "EXPORT" section heading renamed to **"RENDER"**.
- Desktop-only fields (transition key shift default, timing review font) shown only at `lg` breakpoint.
- iOS offline note: "Offline caching requires iOS 17.4 or later."

---

### 5.5 Play — `/songsets/[id]/play`

Renamed from "Worship Playback." Same purpose: run a pre-generated lyrics video during a worship session.

#### 5.5.1 Pre-play card (portrait phone)

```
┌─────────────────────────────────┐
│  ←  Sunday Morning Worship      │
├─────────────────────────────────┤
│  5 songs · 23m 14s              │
│                                 │
│  NOTES                          │
│  This week's theme: God's       │
│  faithfulness — Isaiah 40:31    │
│  (full description, no truncation)
│                                 │
│  SONGS                          │
│  1.  How Great Is Our God  4:32 │
│  2.  Cornerstone           5:14 │
│  3.  Great Are You Lord    3:45 │
│  4.  Build My Life         3:50 │
│  5.  Way Maker             6:23 │
│                                 │
│  ⚠ Render out of date           │  ← stale
│    Songs edited after render.   │
│    [Re-render ›]                │
│                                 │
│  ✈ Ready offline (240 MB)       │  ← cached
│  ⬇ Download for offline (240 MB)│  ← not cached
│    [Download]                   │
│                                 │
│  [📺 Send lyrics to TV]         │  ← Android Chrome + Cast only; hidden if unavailable
│                                 │
│  [   ▶  Start Worship   ]       │  ← disabled if no render artifacts
│                                 │
│  [        Share…        ]       │
└─────────────────────────────────┘
```

**"Send lyrics to TV" button:**
- Shown only when `navigator.presentation` API is available and a Cast/receiver target is detected via `PresentationRequest.getAvailability()`.
- Hidden on iOS and on browsers without Presentation API support; no fallback UI is shown (mirror mode applies automatically).
- Tapping calls `navigator.presentation.requestSession()` with the URL `/songsets/[id]/play/projection?session=<sessionId>`. The projection page opens on the receiver (TV/Chromecast browser).

**Pre-play card behavior:** Unchanged from v4 §5.5 (worship pre-play card), substituting "render" for "export" in all messaging.

#### 5.5.2 Controller player (in-hand on phone)

Tapping **Start Worship** enters the controller player. The native `<video>` element plays; controls are a custom overlay that is **always visible** on the controller screen (unlike v4 where they auto-hid on the single shared surface). The controller auto-hides controls after 2 s of no interaction **only** in mirror mode (when a Presentation session is not active), to minimize chrome shown on the TV.

```
┌─────────────────────────────────┐
│  ← Sunday Morning Worship   [×] │  ← top bar
│        How Great Is Our God     │  ← current song title
├─────────────────────────────────┤
│                                 │
│     (video: lyrics baked in)    │  ← playback (muted on controller
│    [muted on controller when     │    when projection is active)
│     projection session active]  │
│                                 │
├─────────────────────────────────┤
│  [⏮]  [⏪]  [   ▶/⏸  ]  [⏩]  [⏭] │
│  0:32 ━━━━●━━━━━━━━━━━━━  23:14 │
│  🔊━━━━●━━━━      ☰ Lyrics  [⛶] │
└─────────────────────────────────┘
```

**When Presentation API session is active:**
- Controller `<video>` plays in sync (sends seek/play/pause commands to projection via `PresentationConnection`).
- Controller `<video>` is **muted** (audio comes from the TV's speakers via the projection's `<video>`).
- Controller volume slider controls the projection `<video>` volume via `PresentationConnection` message.
- A small `📺 Connected` indicator is shown in the top bar.

**When in mirror mode (no Presentation session):**
- Controller and projection are the same `<video>` element; the phone screen mirrors to the TV.
- Controls auto-hide after 2 s of no interaction.
- A non-blocking "iOS: controls may briefly appear on the TV" info toast is shown once per session on iOS.

Controls are identical to v4 §5.5 (worship fullscreen player): prev song, skip back 10s, play/pause, skip forward 10s, next song, scrub bar with chapter ticks, volume, lyric jump list, fullscreen toggle.

**Auto-hide behavior (mirror mode):** 2 s after last tap, top bar + bottom controls fade out. Tap anywhere on video area to reveal.

#### 5.5.3 Projection player — `/songsets/[id]/play/projection`

The projection surface is designed to fill a TV/external display with nothing but lyrics and a minimal song title.

```
┌──────────────────────────────────────────────────────────────────────┐
│  How Great Is Our God                                                 │  ← top edge: small, low-opacity, fades after 2s unchanged
│                                                                      │
│                                                                      │
│                  (MP4 fills viewport 100%)                           │
│              (lyrics baked into the video frame)                     │
│                                                                      │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

**Projection surface rules:**
- The rendered MP4 fills 100% of the viewport (object-fit: cover for landscape; letterbox vertically if portrait MP4 on landscape TV).
- Song title overlaid at the top edge: font ≤14 px, opacity 0.5 white-on-dark, fades to invisible 2 s after the title last changed. Reappears when the chapter changes.
- No play/pause, no scrub bar, no chapter ticks, no lyric list, no app bar, no branding chrome.
- Wake-lock active (`navigator.wakeLock.request('screen')`).
- Orientation: `screen.orientation.lock('landscape')` attempted on load; fails gracefully.
- The `<video>` element is controlled by messages received via `PresentationConnection` from the controller:
  - `{ type: 'play' }` — play
  - `{ type: 'pause' }` — pause
  - `{ type: 'seek', positionSeconds: number }` — seek
  - `{ type: 'volume', level: number }` — set volume (0.0–1.0)
  - `{ type: 'songTitle', title: string }` — update the overlay title

**Data source:** The projection page receives the signed R2 URL for the MP4 and the `chapters.json` URL as query params set by the controller when opening the Presentation session. No server round-trip after initial load.

**Caching headers:** `Cache-Control: no-store, no-cache` is required on this route. The page embeds signed R2 URLs that expire; CDN or browser caching would serve stale signatures to new Presentation sessions.

**Offline:** If the artifact is SW-cached, the `<video>` src resolves from cache; no network access during playback.

#### 5.5.4 Controller ↔ projection sync

**Path A — Presentation API (Android Chrome + Cast):**

```
Phone (controller)                         TV (Cast receiver / Chromebook)
    |                                            |
    |-- navigator.presentation.requestSession() →|
    |<-- PresentationConnection established ------|
    |                                            |
    |-- { type: 'play' } ----------------------→ |  video.play()
    |-- { type: 'seek', positionSeconds: 42 } →  |  video.currentTime = 42
    |-- { type: 'volume', level: 0.7 } --------→ |  video.volume = 0.7
    |                                            |
    |<-- { type: 'timeupdate', t: 42.3 } -------|  scrub bar sync (optional)
```

The projection page listens on `session.addEventListener('message')` and applies commands to its `<video>` element. The controller does not stream audio (muted); audio comes from the projection's `<video>`.

**Path B — iOS / wired HDMI (mirror mode):**

- Controller and TV show the same `<video>` element.
- Audio plays from the phone speaker (and TV speaker via HDMI when mirrored).
- Controls auto-hide after 2 s to minimize visible chrome on TV.
- iOS info toast shown once per session.

**No QR / WebSocket sync in v1.** Clock drift between two independent `<video>` instances makes this unreliable without a tight synchronization protocol. Deferred to v2.

**Cast receiver registration:** The `/play/projection` page is registered as a **Custom HTML5 receiver** via the Google Cast SDK Developer Console. A receiver app ID is provisioned per environment (dev / staging / prod) and set as an environment variable. Production Cast targets (Chromecast devices owned by end users) require Google approval of the receiver before they will load it; until approval is granted, only developer-allowlisted Cast devices can use the receiver. The registration and approval process is an implementation-time task.

#### 5.5.5 Lyric jump list

Unchanged from v4 §5.5 (worship lyric jump list).

#### 5.5.6 Wake lock and screen behavior

Unchanged from v4 §5.5 (worship wake lock), applied to both the controller player and the projection player.

#### 5.5.7 Offline playback

Unchanged from v4 §5.5 (worship offline playback). The signed URL is fetched at pre-play card load time. For projection sessions, the controller passes the already-signed URL to the projection page via query param.

#### 5.5.8 Keyboard shortcuts (desktop controller)

| Key | Action |
|---|---|
| `Space` | Toggle playback |
| `←` `→` | Seek −10s / +10s |
| `[` `]` | Prev / next song |

#### 5.5.9 Media Session API (nice-to-have)

Unchanged from v4 §5.5.

---

### 5.6 Public hosted player — `/share/[token]`

Unchanged from v4 §7.5.

**Projection route:** `/share/[token]/play/projection` mirrors `/songsets/[id]/play/projection` for hosted-player recipients. The recipient can open `/share/[token]` on their phone as a controller and project `/share/[token]/play/projection` via Presentation API to a TV — same capability as the authenticated flow.

**URL handoff and token security:** When the controller opens a Presentation session for the share route, it passes only `?token=<token>&session=<id>` in the projection URL — no signed R2 URLs in query params. The projection page re-validates the share token server-side (via the existing share-token middleware) and mints its own short-lived signed R2 URLs server-side. This adds one extra round-trip on projection start (acceptable) and ensures signed URLs never appear in query strings, referrer headers, or Cast receiver logs.

**Caching headers:** `Cache-Control: no-store, no-cache` on this route — same rationale as the authenticated projection route.

---

## 6. Offline & Sync

### 6.1 Making a songset available offline

Unchanged from v4 §6.1.

**iOS gate:** "Make available offline" is disabled (greyed out) on iOS below 17.4. The disabled state shows a tooltip: "Requires iOS 17.4 or later."

### 6.2 Cache invalidation

Unchanged from v4 §6.2.

### 6.3 Freshness detection

Unchanged from v4 §6.3. The `artifactsOutOfDate` flag feeds the `stale` state in the Render/Play state machine.

### 6.4 Storage budget

Unchanged from v4 §6.4.

### 6.5 iOS Safari caveats

Updated from v4 §6.5:

- **Minimum for offline:** iOS 17.4+. Versions below show an "Update iOS for offline support" banner in any surface that surfaces the offline checkbox or download button.
- On iOS 17.4+, `navigator.storage.persist()` is called on the first cache action; iOS prompts the user.
- No "Add to Home Screen" nudge in v1 (that flow was for pre-17.4 workarounds; v5 drops support below 17.4 for offline).

---

## 7. Sharing

### 7.1 Entry points

Unchanged from v4 §7.1.

### 7.2 Share dialog

Unchanged from v4 §7.2, with the following changes to **Send file** behavior:

#### File size — disable per-app buttons above threshold

When the total artifact size exceeds a per-app limit, that app's share button is **disabled** (greyed out, not hidden) with a tooltip:

| App | Limit | Tooltip when disabled |
|---|---|---|
| WhatsApp | 2 GB | "File too large for WhatsApp. Use Share link instead." |
| Line | 1 GB | "File too large for Line. Use Share link instead." |
| Email | 25 MB | "File too large for email. Use Share link instead." |

"More options…" (OS share sheet) is always enabled; the OS handles per-app limits.

When all per-app buttons are disabled (e.g. a 1.2 GB MP4 on Line and Email), a banner is shown: "Files are too large for most apps. Share the link instead." with a **Switch to Share link** button that changes the active tab.

### 7.3 Send file behavior

Unchanged from v4 §7.3, except the per-app disable logic replaces the non-blocking warning.

### 7.4 Share link behavior

Unchanged from v4 §7.4.

**Revocation notice:** The Share dialog (Share link tab) displays a persistent note below the link: "Revoking this link stops new streams. Recipients who already downloaded the file can still play it."

### 7.5 Public hosted player

Unchanged from v4 §7.5, with the addition of the projection route (§5.6).

### 7.6 Abuse prevention

Unchanged from v4 §7.6.

---

## 8. Global Audio Player

Unchanged from v4 §8.

---

## 9. Render Pipeline

Renamed from "Export Pipeline." Contents are identical to v4 §9.

### 9.1 Flow

Unchanged from v4 §9.1.

### 9.2 Job persistence

Unchanged from v4 §9.2.

### 9.3 Render phases

Identical to v4 §9.3:

| Phase | Description |
|---|---|
| Preparing | Fetching audio from R2; fetching LRC (user override if present, otherwise official); validating inputs. |
| Mixing audio | FFmpeg transition mix; chapter boundary timestamps computed. |
| Rendering frames | Canvas lyric overlay frame generation. Title card frames prepended if enabled. |
| Encoding video | FFmpeg H.264 encode; MP4 chapter atoms injected (best-effort; render proceeds if injection fails); chapters.json sidecar written. |
| Uploading | Writing MP3 + MP4 + chapters.json to R2; updating `render_job` to `completed`. |
| Completed | All files available. Offline cache warming begins if requested. |

**MP4 chapter atoms:** Best-effort. If FFmpeg chapter atom injection fails in the Vercel bundle, render proceeds and a warning is logged. The `chapters.json` sidecar is authoritative for the app; chapter atoms are a nice-to-have for native media players (e.g. VLC).

### 9.4 Chapter manifest format

Unchanged from v4 §9.4.

---

## 10. Responsive Design

### 10.1 Breakpoints

| Name | Min width | Layout tier |
|---|---|---|
| `sm` (default) | 0 px | Phone — phone-lite UX |
| `md` | 768 px | Tablet — same as phone (responsive layouts) |
| `lg` | 1024 px | Desktop — power-mode unlocked |

**Power-mode features unlocked at `lg`:**
- Lyrics Review: Edit text + Edit timing tabs
- Transition Detail: key shift, tempo nudge, waveform preview
- Song list: keyboard reorder (arrow keys) + drag
- Dense keyboard shortcuts (all v4 shortcuts)

### 10.2 Typography

Unchanged from v4 §8.2.

### 10.3 Touch targets

All interactive elements: minimum 48×48 px on mobile/tablet. **Play screen primary controls (play/pause): minimum 64×64 px.** Primary CTA buttons on phone: minimum 56 px tall (full-width on phone).

### 10.4 Gestures

All v4 gestures (§8.4) plus:

| Gesture | Element | Action |
|---|---|---|
| Tap | Controller video area | Reveal/hide controls (mirror mode); no-op in Presentation mode |
| Swipe up | Bottom of controller player | Open lyric jump list |

### 10.5 Playback orientation

- **Portrait:** Pre-play card always portrait. Controller player works in portrait; video letterboxed.
- **Landscape:** Controller player fills width; controls dock to bottom. Projection player: always landscape (lock attempted).
- On entering fullscreen (`⛶`) or projection session start, `screen.orientation.lock('landscape')` attempted; fails gracefully.

### 10.6 Loading & error states

Unchanged from v4 §8.5. Additionally:
- Projection player: if the artifact fails to load (expired signed URL, network error), show a centered "Playback error" message on the projection surface; the controller shows a "Projection error — tap to retry" overlay.

---

## 11. Data Requirements

### 11.1 Songset

```typescript
interface Songset {
  id: string;
  name: string;
  description: string | null;
  songCount: number;
  totalDurationSeconds: number | null;
  formattedTotalDuration: string | null;   // "23m 14s"
  updatedAt: string;    // ISO 8601
  createdAt: string;
  latestRenderJobId: string | null;
  lastFailedRenderJobId: string | null;    // v5: new
  artifactsOutOfDate: boolean;
  offlineAvailable: boolean;
  cachedSizeBytes: number | null;
  // derived
  renderState: 'unrendered' | 'rendering' | 'fresh' | 'stale' | 'failed';
  markedLineCount: number;                 // total marked lines across all songs
}
```

`renderState` is computed server-side:
- `unrendered` — `latestRenderJobId IS NULL` and `lastFailedRenderJobId IS NULL`
- `failed` — `lastFailedRenderJobId IS NOT NULL` and `latestRenderJobId IS NULL` (or last job failed)
- `rendering` — active job with status not in `{completed, failed, timeout, cancelled}`
- `fresh` — `latestRenderJobId IS NOT NULL` and `!artifactsOutOfDate`
- `stale` — `latestRenderJobId IS NOT NULL` and `artifactsOutOfDate`

### 11.2 SongsetItem

Unchanged from v4 §11.2.

### 11.3 Song (catalog)

Unchanged from v4 §11.3.

### 11.4 UserLrcOverride and LyricMark

Unchanged from v4 §11.4.

### 11.5 RenderJob (renamed from ExportJob)

```typescript
interface RenderJob {
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
  chaptersUrl: string | null;
  resolution: '720p' | '1080p';
  fontSizePreset: 'S' | 'M' | 'L' | 'XL';
  includeTitleCard: boolean;
  titleCardDurationSeconds: number;
  chapters: Chapter[] | null;
}

interface Chapter {
  position: number;
  songTitle: string;
  startSeconds: number;
  endSeconds: number;
  lines: { text: string; startSeconds: number }[];
}
```

### 11.6 User settings

```typescript
interface UserSettings {
  defaultGapBeats: number;
  defaultVideoTemplate: string;
  defaultResolution: '720p' | '1080p';
  lyricsLoopWindowSeconds: number;
  defaultFontSizePreset: 'S' | 'M' | 'L' | 'XL';
  offlineAutoCacheAfterRender: boolean;   // renamed from offlineAutoCacheAfterExport
}
```

### 11.7 SongsetShare

Unchanged from v4 §11.7.

### 11.8 SongEmbedding

```typescript
interface SongEmbedding {
  songId: string;
  modelVersion: string;   // "bge-m3-v1.0"
  createdAt: string;
}
```

### 11.9 SemanticSearchResult

Unchanged from v4 §11.9.

### 11.10 PresentationSession (client-only, not persisted)

```typescript
interface PresentationSession {
  sessionId: string;
  projectionUrl: string;     // /songsets/[id]/play/projection?session=<id>
  connection: PresentationConnection;
  isActive: boolean;
}
```

---

## 12. Non-Functional Requirements

| Requirement | Target |
|---|---|
| Initial page load (LCP) | < 2.5s on 4G phone |
| Time to interactive | < 4s on phone |
| Audio playback start latency | < 1s after tap |
| **Play start latency (offline)** | **< 500ms from tap** |
| **Play start latency (streaming)** | **< 2s on 4G** |
| **Projection screen LCP from Start tap** | **< 1s** (Presentation API session + video buffering) |
| **Controller → projection round-trip latency** | **< 200ms** (play/pause/seek commands via PresentationConnection) |
| Render function timeout budget | 800s (Pro + Fluid Compute); default 720p to stay under budget |
| Semantic search latency | < 1s p95 for `POST /api/songs/search/semantic` (includes fastembed-js ONNX inference, ~200–500ms cold start) |
| Accessibility | WCAG 2.1 AA (keyboard nav, ARIA labels, sufficient contrast) |
| Browser support | Chrome 110+, Safari 16+, Firefox 120+, Chrome Android, Safari iOS |
| Minimum font size | 16px on phone/tablet |
| Touch target size | ≥ 48×48px on touch devices; ≥ 64×64px for primary playback controls; ≥ 56px tall for phone CTAs |
| Vercel plan required | Pro (for `maxDuration: 800` and Fluid Compute) |
| Service Worker | Required; Workbox recommended |
| Offline playback | Zero-network once cached; SW cache keyed by render_job_id |
| iOS offline minimum | iOS 17.4+ |
| Wake lock | Active during playback (controller + projection); released on exit |
| Public share page LCP | < 2s on 4G |
| Share token max per user | 20 active tokens |
| Presentation API | Android Chrome + Cast; iOS falls back to mirror mode (no Presentation API support on iOS Safari) |
| fastembed-js bundle | Lives in a dedicated `/api/embed` Edge Function; bge-m3 ONNX model bundled there only, keeping main function bundle small |

---

## 13. Key Changes from v4

| Area | v4 | v5 | Reason |
|---|---|---|---|
| Primary device | Tablet-first, phone for worship only | **Phone-first for both prep and worship** | Interview confirmed leaders prep on the same phone they use for worship |
| Desktop role | Desktop-equal (same features) | **Desktop power-mode** — unlocks dense lyrics edit + transition tuning | Dense QA UX doesn't fit phone; desktop becomes opt-in, not the baseline |
| Prep on phone | Full feature set | **Phone-lite:** lyrics Review only (mark problems); gap + crossfade transitions only | Phone-friendly simplification; full editing deferred to desktop |
| "Open on desktop" nudge | Not present | New nudge on phone when marked lines exist | Guides users to the right device for text/timing fixes |
| Export → Render | "Export" everywhere | **"Render"** everywhere — route, button, settings section | Render aligns the mental model: you render to enable playback |
| Primary action button | No unified state-machine button | **Render / Rendering… / ▶ Play / Re-render / Retry** state-machine button on list + editor | Single discoverable action adapts to render state |
| lastFailedRenderJobId | Not present | New `songsets.lastFailedRenderJobId` column | Powers Retry render + View error button state |
| Worship → Play | `/songsets/[id]/worship` | `/songsets/[id]/play` | Consistency with "▶ Play" button label |
| Projection screen | Not present | **`/songsets/[id]/play/projection`** — lyrics + minimal song title only; controlled by Presentation API | True clean lyrics-only surface for TV projection |
| Second screen | Not designed | Presentation API (Android Chrome + Cast); mirror fallback (iOS) | User wants TV to show only lyrics |
| Controller player | Single `<video>` with overlay | **Separate controller** (always shows controls); muted when projection session active | Controls stay on phone; TV shows only lyrics |
| Embedding model | TBD (`bge-m3` or `nomic-embed-text-v1.5` or OpenAI) | **bge-m3** locked | Multilingual Chinese+English, open-weights, runs in Docker |
| Query embedding host | TBD | **fastembed-js + ONNX** in a dedicated Vercel Edge Function (`/api/embed`) | Keeps main app bundle small; same model as batch → comparable vectors |
| iOS offline minimum | Not specified | **iOS 17.4+** — older iOS offline checkbox disabled | 17.4 relaxed the 1 GB SW cache cap |
| Large-file share | Warning shown | **Per-app buttons disabled above threshold** with tooltip | More opinionated; fewer failed share attempts |
| Revocation notice | Not present | Share dialog note: "Revoking stops streams; downloaded files are unaffected" | Sets expectations about revocation scope |
| v4 prep features | All present | **All retained** (lyrics review, transitions, marks, LRC overrides, global audio player) | Phone-lite / desktop-full split; nothing removed |

---

## 14. Open Questions

All v4 and v5 §14 questions are resolved. Implementation-time validations — real-world Cast receiver approval latency, Edge Function cold-start measurements for `/api/embed`, Presentation API availability across Android Chrome versions in the field — are tracked in the implementation plan, not in this spec.
