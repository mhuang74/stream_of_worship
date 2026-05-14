# Stream of Worship — Web App UI Requirements v4

**Source:** Revised from v3 spec, use-case interview (2026-05-10), TUI codebase (`src/stream_of_worship/app/`), and DB schema (`src/stream_of_worship/admin/db/schema.py`)
**Target:** Next.js (App Router) hosted on Vercel (Pro plan, Fluid Compute enabled)
**Date:** 2026-05-10
**Supersedes:** `specs/webapp_ui_requirements_v3.md`

---

## 1. Overview & Goals

The Stream of Worship Web App is a browser-based tool for worship leaders and media volunteers to browse a song catalog, assemble songsets, preview audio, review lyric timing, export a finished MP3 + MP4 lyrics video, and **play back that video during a worship session projected to a screen or TV**.

### Primary personas

**Persona A — Prep persona (unchanged from v3)**
A worship leader or media volunteer sitting at a desk, kitchen table, or couch with a tablet, preparing materials for an upcoming service:
1. Pick songs from the catalog and assemble a setlist.
2. Tune transitions (gap, crossfade, key shift, tempo) between songs.
3. Review auto-generated lyrics — verify text accuracy, word sequence, and time-alignment; correct text or timestamps directly when wrong.
4. Export an MP3 (full mix) + MP4 (lyrics video).

**Persona B — Worship persona (new in v4)**
A church small-group leader leading worship in a home or small room, projecting lyrics to a TV or large screen via an HDMI / USB-C cable from their phone:
1. Open the app on phone; navigate to a pre-prepared songset.
2. Verify the artifacts are downloaded and ready before worship begins.
3. Tap **Start Worship** → fullscreen player begins.
4. Control playback during worship: pause, resume, skip to next/prev song, adjust volume, jump to a specific lyric line.
5. If font size is too small after projecting, re-export with a larger preset.

The export pipeline and the playback pipeline are the two primary outputs. The prep persona drives the export; the worship persona drives the playback.

### What "stage tool" means and does not mean

v3 stated "this is not a stage tool." That framing was too broad. v4 clarifies:

**In scope:** Pre-generated video playback from a phone mirrored to a TV/screen, controlled by the worship leader via the phone. Fullscreen player with chapter navigation, scrub bar, volume, lyric-jump list, and wake-lock. This is a **media player for a pre-rendered artifact**, not a live production tool.

**Still out of scope:** MIDI sync, NDI output, presenter display with confidence monitor, in-the-moment key change, live mixer, performance-lighting design mode. These are a separate product if ever needed.

### Design philosophy (updated)

- **Tablet-first, desktop-equal.** Primary prep device is a 10–11" tablet; desktop is a first-class environment. Phone is elevated in v4: it is the primary playback device during worship.
- **Production tool aesthetic.** Information-dense for prep (lyrics review, transition tuning), clean and large-target for playback (worship mode controls are 64px+ tap targets).
- **Reviewability over glanceability (prep).** Users are doing focused QA. Provide context, not just the current moment.
- **Immediacy over discoverability (playback).** Once worship begins, every interaction must be a single tap. Controls auto-hide; critical actions (pause, next song) are always reachable.
- **Offline-first for worship artifacts.** The MP4 and MP3 must survive a venue with no Wi-Fi. Cache before worship, not during.
- **Sheets over routes (prep).** Sub-features (browse, transition editing, lyrics review) are overlay layers within the editor — not separate pages.
- **Touch-first interactions.** Every action has a tap/swipe/gesture trigger. Keyboard shortcuts are progressive enhancement for desktop only.

### Key goals

- **Multi-device access** — responsive web app replaces the desktop-only TUI
- **Multi-user** — each user has their own songsets; catalog is shared, managed by admins
- **Full feature parity** — all TUI capabilities (playback, transition preview, export)
- **Lyric QA** — correct lyric text and timing in-app; corrections save as a per-user override
- **Worship playback** — fullscreen player for pre-rendered MP4, designed for phone→TV projection *(new)*
- **Offline cache** — pre-rendered artifacts downloadable for zero-network playback *(new)*
- **Semantic discovery** — search songs by message, theme, or lyric meaning using pgvector *(new)*
- **Sharing** — send artifacts via file share or a public hosted-player link *(new)*

### Out of scope for v1

- Admin catalog management (stays in `sow-admin` CLI)
- Song analysis / LRC generation (stays in the Analysis Service, triggered by Admin CLI)
- Songset JSON import/export (defer to v2)
- Submit-for-approval workflow (admin merges user overrides offline via `sow-admin`)
- MIDI sync, NDI, presenter confidence monitor, live mixer

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
| Blob storage | Cloudflare R2 (audio files, LRC, export artifacts, chapter manifests) |
| Audio export rendering | Node FFmpeg (`fluent-ffmpeg` + `ffmpeg-static`) within a Vercel Function |
| Video export rendering | Node canvas (`skia-canvas` or `node-canvas`) + Node FFmpeg, same function |
| Real-time progress | Server-Sent Events (SSE) from the long-running export Route Handler |
| Deployment | Vercel Pro plan, Fluid Compute enabled (required for `maxDuration: 800`) |
| Offline cache | Service Worker (Workbox) + Cache Storage for MP4/MP3/chapter manifest |
| Vector search | `pgvector` extension on Neon; `song_embedding` table |
| Embedding generation | New Analysis Service worker (runs after LRC); multilingual model ≤1024 dims |
| Public share | `/share/[token]` route — unauthenticated; signed R2 URL behind `songset_share` table |

The Next.js app is a full backend. **There is no Python API between the browser and the database.** The Analysis Service remains admin-only.

### 2.2 System boundary

The web app has **zero runtime dependencies on the Analysis Service**. The Analysis Service supports only the offline admin/catalog pipeline.

```
                   ┌──── Web App runtime boundary ────┐
                   │                                  │
┌─────────────┐    │   ┌────────────────────────────┐ │
│   Browser   │ HTTPS  │  Next.js (Vercel, Pro+,    │ │
│  (PWA/SW)   ├───>│   │           Fluid Compute)   │ │
│             │SSE │   │  ─ Better Auth             │ │
│             │<───│   │  ─ App Router routes       │ │
└─────────────┘    │   │  ─ Drizzle ORM → Neon      │ │
      │ offline    │   │  ─ R2 SDK (signed URLs)    │ │
      │ cache      │   │  ─ pgvector search         │ │
      ↓            │   │  ─ Node FFmpeg + canvas    │ │
┌─────────────┐    │   │      → audio MP3 render    │ │
│Service Worker│   │   │      → video MP4 render    │ │
│+ Cache API  │   │   └────┬───────────────────┬───┘ │
└─────────────┘    │        │ pg (TLS)          │ S3  │
                   │        v                   v API │
                   │   ┌────────────┐      ┌─────────┐│
                   │   │   Neon     │      │   R2    ││
                   │   │  Postgres  │      │ (blobs) ││
                   │   │  +pgvector │      └────^────┘│
                   │   └─────^──────┘           │     │
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
                   │  └────────────┘   │  ─ allinone  │  │
                   │                   │  ─ Demucs    │  │
                   │                   │  ─ Whisper   │  │
                   │                   │  ─ Embedding │  │ ← new
                   │                   └──────────────┘  │
                   └─────────────────────────────────────┘
```

### 2.3 Component responsibilities

| Component | Owns |
|---|---|
| **Next.js (Vercel)** | All user-facing UI and API; auth; all CRUD for users, songsets, items, settings, LRC overrides, lyric marks; catalog read access; signed-URL minting for R2 audio/LRC/artifacts; pgvector semantic search queries; audio MP3 rendering; video MP4 rendering; chapter manifest generation; SSE export progress; public-share token issuance and validation. |
| **Neon Postgres** | Single source of truth: catalog (songs, recordings), user data (songsets, items, settings), auth sessions, LRC overrides, lyric marks, render jobs, song embeddings, share tokens. |
| **Cloudflare R2** | Blob store: original audio, stems, LRC files, rendered MP3/MP4 artifacts, chapter manifests, title-card frames. |
| **Service Worker (browser)** | Offline cache of MP4, MP3, and chapter manifest for songsets marked "available offline." Cache-first strategy for artifact requests. |
| **Analysis Service (offline)** | Song analysis (allinone, Demucs), LRC generation (Whisper), **text embedding generation** (new). Triggered by Admin CLI only. Not in any web-app request path. |
| **Admin CLI (`sow-admin`)** | sop.org scraping, recording import, dispatching analysis/LRC/embed jobs to Analysis Service, catalog admin. Direct Neon client (separate DB role). |

### 2.4 Export rendering constraints

Unchanged from v3 §2.4. Additional render phases for v4 (title card, chapter marker generation) are lightweight and do not materially change the render time estimate.

Font size presets add no render time (they change canvas text size, not the number of frames).

### 2.5 Offline layer

**Service Worker (Workbox, `NetworkFirst` for API, `CacheFirst` for R2 artifacts):**

When the user marks a songset "Available offline," the Service Worker:
1. Fetches the signed R2 URL for the MP4, MP3, and chapter manifest.
2. Downloads them to the Cache Storage (keyed by `render_job.id` + filename to version the cache).
3. On subsequent requests, the SW intercepts and serves from cache without hitting the network.

**Cache invalidation:** Each export produces a new `render_job.id`. The offline cache key embeds the job ID. After re-export, the old job-ID cache entries are evicted during the next SW activation cycle.

**Storage quota:** The SW checks `navigator.storage.estimate()` before caching. If remaining quota < 150 MB or total cached size > 2 GB, the user is prompted to evict the oldest cached songset.

**iOS caveat:** Safari on iOS (before iOS 17.4) may cap persistent storage at ~1 GB without a PWA install prompt. The app should detect when quota is restricted and surface an "Add to Home Screen for full offline support" nudge.

### 2.6 Semantic search layer

**pgvector extension** is enabled on the Neon Postgres database. A new table `song_embedding` stores one embedding per song (keyed by `song_id`). Embeddings are generated offline by the Analysis Service after LRC generation, using a multilingual text model (≤1024 dimensions; must support Chinese + English; open-weights preferred so it can run inside the Analysis Service Docker container — final model selection is a follow-up decision; candidates include `bge-m3` and `nomic-embed-text-v1.5`).

**Embedding content:** `songs.title || ' ' || songs.composer || ' ' || songs.lyrics_raw`. The `lyrics_raw` field from the existing `songs` schema is the primary semantic signal.

**Query path:** `POST /api/songs/search/semantic` accepts `{ query: string }`, embeds the query server-side using the same model (or a Vercel AI SDK embedding call), then runs a `<=>` cosine-similarity search on `song_embedding`. Returns top-K results with similarity score and a matching lyric snippet.

**Model hosting:** If an open-weights model is used, it must be callable from the Next.js Route Handler without GPU. Options: (a) small CPU-only model bundled via ONNX Runtime; (b) external embedding API (OpenAI, Voyage, Jina); (c) Vercel AI SDK embedding support. This decision is deferred but must be made before implementing the search endpoint.

### 2.7 Share token layer

A new `songset_share` table stores public-share tokens. Tokens are URL-safe random strings (24 chars). The `/share/[token]` route is unauthenticated; it validates the token, checks expiry and revocation, mints a short-lived signed R2 URL for the MP4/MP3 files, and renders an HTML5 video player page (server-rendered, no React hydration required for baseline).

Share tokens are scoped to a specific `render_job_id`. If the user re-exports and wants to share the new artifacts, they issue a new share token.

### 2.8 Schema and migrations

Additions to v3 §2.5:

**New tables for v4:**

```sql
-- Text embedding per song (generated by Analysis Service after LRC)
song_embedding (
  song_id          TEXT PRIMARY KEY REFERENCES songs(id),
  embedding        vector(1024) NOT NULL,
  model_version    TEXT NOT NULL,
  created_at       TIMESTAMPTZ DEFAULT NOW()
)

-- Public share token (links a render job to an unauthenticated viewer)
songset_share (
  token            TEXT PRIMARY KEY,           -- 24-char URL-safe random
  songset_id       TEXT NOT NULL REFERENCES songsets(id),
  render_job_id    TEXT NOT NULL REFERENCES render_jobs(id),
  created_by_user_id TEXT NOT NULL,
  allow_download   BOOLEAN DEFAULT FALSE,
  expires_at       TIMESTAMPTZ,               -- NULL = never
  revoked_at       TIMESTAMPTZ,               -- NULL = active
  created_at       TIMESTAMPTZ DEFAULT NOW()
)
```

**Additions to existing tables:**

`render_jobs` adds: `font_size_preset TEXT DEFAULT 'M'`, `include_title_card BOOLEAN DEFAULT FALSE`, `title_card_duration_seconds INTEGER DEFAULT 10`, `chapters_r2_key TEXT` (R2 path for chapters.json sidecar).

`user_settings` (new app settings table or addition to existing): `offline_auto_cache BOOLEAN DEFAULT TRUE` — if true, "Make available offline" is checked by default on the export screen.

`songsets` adds: `latest_render_job_id TEXT REFERENCES render_jobs(id)` — denormalized for efficient freshness checks.

---

## 3. Authentication

### 3.1 Provider

Better Auth with Neon Postgres as the session/user store. OAuth providers TBD (Google recommended).

### 3.2 Protected routes

```
/login              → Public: OAuth sign-in page
/share/[token]      → Public: anonymous hosted-player (token-gated)
/                   → Protected: redirects to /songsets
/songsets           → Protected
/songsets/[id]      → Protected (owner check)
/songsets/[id]/export → Protected (owner check)
/songsets/[id]/worship → Protected (owner check)
/settings           → Protected
```

### 3.3 User data isolation

Unchanged from v3. Additionally: share tokens are owned by their creator but viewable by anyone with the URL (no auth required for `/share/[token]`).

---

## 4. Navigation & Routing

### 4.1 Route structure

```
/login                      Login page
/share/[token]              Public hosted-player (unauthenticated)
/songsets                   Songset list (home)
/songsets/[id]              Songset editor  ← browse, transition, lyrics are sheets/overlays
/songsets/[id]/export       Export config + progress
/songsets/[id]/worship      Worship Playback (pre-play card + fullscreen player)
/settings                   App settings + catalog sync + shared links
```

### 4.2 Navigation patterns

Unchanged from v3 §4.2. Additionally:

**`/songsets/[id]/worship`** is a full-screen route on all breakpoints. On phones, it is portrait by default (pre-play card) and rotates to landscape on entering the fullscreen player (if device orientation lock is not engaged). No app bar visible during fullscreen playback.

### 4.3 Navigation flow

```
/login
  └── /songsets (home, after auth)
        ├── /settings
        └── /songsets/[id]  (editor)
              ├── Browse Sheet (overlay, no URL change)
              │     └── Describe mode (semantic search)
              ├── Transition Panel (inline expand, no URL change)
              ├── Lyrics Review (overlay, no URL change)
              ├── /songsets/[id]/export
              │     └── (post-export: Share dialog, Make available offline)
              └── /songsets/[id]/worship
                    └── (fullscreen player)

/share/[token]  (no auth required)
```

### 4.4 State passing

URL params for page-level state: export job ID (`?job=<id>`). Sheet/overlay state is local React state. React Query for server state caching.

---

## 5. Screen Requirements

### 5.1 Songset List — `/songsets`

**Purpose:** Landing screen. Shows the user's songsets with offline status and quick worship access.

#### Layout (mobile/tablet)

```
┌─────────────────────────────────┐
│  Stream of Worship    [avatar]  │  ← top app bar
├─────────────────────────────────┤
│  Sunday Morning Worship      >  │  ← row with chevron
│  5 songs · 23m 14s · 2 days ago │
│  [✈ Offline] [▶ Worship]        │  ← badges / quick actions
│  ─────────────────────────────  │
│  Evening Set                 >  │
│  3 songs · 18m 02s · Today      │
│  ⚠ Artifacts out of date        │  ← stale indicator
│  ─────────────────────────────  │
│  Untitled Set                >  │
│  0 songs · —                    │
│  ─────────────────────────────  │
│  (empty space)                  │
│                           [+]   │  ← FAB
└─────────────────────────────────┘
```

#### Songset row

Each row displays:
- Songset name (semibold)
- Song count + total duration + relative updated-at timestamp (muted, smaller)
- **`✈ Offline` badge** — shown when artifacts are cached offline and up to date
- **`⚠ Artifacts out of date` badge** — shown when the export predates recent edits (any song, transition param, or LRC override changed after `render_job.completed_at`)
- **`▶ Worship` quick-action button** — visible on rows with valid cached artifacts; taps directly to `/songsets/[id]/worship`
- Tap/click row body → navigate to `/songsets/[id]`
- Long-press (mobile) or right-click (desktop) → context menu

#### Context menu

**Rename**, **Duplicate**, **Export**, **Worship**, **Share…**, **Delete**

#### Actions

| Action | Trigger | Behavior |
|---|---|---|
| Open songset | Tap row | Navigate to `/songsets/[id]` |
| Create songset | FAB `+` / `+ New songset` | Creates with default name "New Songset", navigates to editor |
| Rename | Context menu → Rename | Inline text input; save on blur or Enter |
| Duplicate | Context menu → Duplicate | Creates copy with "Copy of …" prefix; refreshes list |
| Export | Context menu → Export | Navigate to `/songsets/[id]/export` |
| Worship | Quick button or context menu | Navigate to `/songsets/[id]/worship` |
| Share… | Context menu → Share… | Opens Share dialog (§7.2) |
| Delete | Context menu → Delete | Confirmation dialog, then delete + remove from list |

#### Empty state

Centered "No songsets yet" + "Create your first songset" button.

---

### 5.2 Songset Editor — `/songsets/[id]`

Unchanged from v3 §5.2, with the following additions:

**Overflow menu (`···`)** now includes: **Worship**, Export, Edit description, Duplicate songset, Delete songset.

**Marks badge and `📝` chip** — unchanged from v3.

**`⚠ Artifacts out of date` banner** — appears below the app bar when `songset.artifactsOutOfDate` is true:

```
┌─────────────────────────────────┐
│  ←  Sunday Morning Worship  ··· │
│  ⚠ Export updated · re-export   │  ← stale banner (tap → /export)
│    or start worship anyway  [×] │     dismissible for session
├─────────────────────────────────┤
│  …song list…                    │
```

---

### 5.2a Browse Sheet (within Editor)

**Changes from v3:** Adds a **Describe** mode tab at the top for semantic search.

#### Layout

```
┌─────────────────────────────────┐
│  ────────  (drag indicator)     │
│  Browse Songs               [×] │
├─────────────────────────────────┤
│  [ Search ]  [ Describe ]       │  ← mode tabs
├─────────────────────────────────┤
│  ── Search mode (default) ──    │
│  [🔍 Search songs…          ] [×]│
│  [ Title ✓ ][ Lyrics ][ Composer ]
│  Album: [Any ▾]  Key: [Any ▾]  BPM: [—]  ☐ Show unanalyzed
├─────────────────────────────────┤
│  (when search is empty: Recent + Frequent + album groups — same as v3)
│  (when search active: flat ranked list, no grouping)
│
│  ── Describe mode ──            │
│  [✦ Describe the message…    ] [×]│  ← semantic input; ✦ icon marks it as AI
│    e.g. "God's faithfulness"    │
│         "Easter resurrection"   │
│         "worship in suffering"  │
├─────────────────────────────────┤
│  (Describe results — ranked by similarity)
│  How Great Is Our God       [+] │
│  ▸ "…for great is our God…"     │  ← top matching lyric snippet
│  G major · 72 BPM · 4:32  ✦91% │  ← similarity score
│  ─────────────────────────────  │
│  Cornerstone                [+] │
│  ▸ "…Christ alone, cornerstone" │
│  E major · 68 BPM · 5:14  ✦88% │
│  ─────────────────────────────  │
│  (expand row to see "Why this match?")
│  ▾ Great Are You Lord       [+] │
│  ▸ Lyric 1: "…all my hope…"     │
│    Lyric 2: "…praise to the…"   │
│  C major · 80 BPM · 3:45  ✦85% │
└─────────────────────────────────┘
```

#### Describe mode behavior

- Single text input; `✦` icon signals AI/semantic mode.
- Debounced 400ms; minimum 6 characters before triggering a query.
- `POST /api/songs/search/semantic` with `{ query, limit: 20 }`.
- Results are a flat ranked list with similarity score and top matching lyric snippet.
- **"Why this match?"** — tapping the row expands to show the 2 lyric lines most responsible for the score.
- Already-added songs show `[✓]` instead of `[+]`; the same song can still be added multiple times.
- Structural filters (Album, Key, BPM) are **not** available in Describe mode — switch to Search mode to combine with filters.
- If the embedding model is unavailable (API error), Describe mode shows: "Semantic search unavailable. Try Search mode."

#### Search mode

Unchanged from v3 §5.2a.

---

### 5.2b Transition Detail Sheet

Unchanged from v3 §5.2b.

---

### 5.2c Lyrics Review

Unchanged from v3 §5.2c.

---

### 5.3 Export — `/songsets/[id]/export`

**Changes from v3:** Adds font size preset, title card option, and "Make available offline" post-export action.

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
│  [preview thumbnail] Dark    ▾  │
│                                 │
│  Lyrics font size               │
│  (○) S   (●) M   (○) L   (○) XL│  ← new; default M
│                                 │
│  Output resolution              │
│  (●) 720p  (faster, default)    │
│  ( ) 1080p (sharper)            │
│    Est. render: ~8 min          │
│                                 │
│  ─────  TITLE CARD  ───────────  │  ← new section
│  [×] Include opening title card │
│      Duration: [10s ▾]          │  ← 5s / 10s / 15s
│  ┌─────────────────────────┐    │
│  │  Sunday Morning Worship │    │  ← live preview (canvas thumbnail)
│  │  "Isaiah 40:31"         │    │
│  └─────────────────────────┘    │
│  (Preview uses description text)│
│                                 │
│  ─────  AFTER EXPORT  ─────────  │  ← new section
│  [×] Make available offline     │  ← default per user setting
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
│  Phase 1 of 5 · ~6 min left     │
│                                 │
│  [          Cancel          ]   │
├─────────────────────────────────┤
│  (completion)                   │
│                                 │
│  ✓ Done in 7m 42s               │
│  [      Download Audio      ]   │
│  [      Download Video      ]   │
│  [      Share…              ]   │  ← new
└─────────────────────────────────┘
```

#### Export configuration

| Field | Options | Default |
|---|---|---|
| Include audio (MP3) | Checkbox | Checked |
| Include lyrics video (MP4) | Checkbox | Checked |
| Video template | Select: Dark, Gradient Warm, Gradient Blue | From user settings |
| Lyrics font size | S / M / L / XL (segmented) | From user settings (M) |
| Output resolution | 720p / 1080p | 720p |
| Include opening title card | Checkbox | Unchecked |
| Title card duration | 5s / 10s / 15s (select; shown when checkbox checked) | 10s |
| Make available offline | Checkbox | From user settings |

#### Font size pixel values

| Preset | 720p | 1080p |
|---|---|---|
| S | 32px | 48px |
| M | 42px | 63px |
| L | 54px | 81px |
| XL | 68px | 102px |

These are the canvas `fontSize` values used by the video renderer. The preview thumbnail on the export screen uses the same values scaled to the thumbnail dimensions.

#### Title card

When enabled, the first `title_card_duration_seconds` of the MP4 are a static title-card frame showing:
- Line 1: songset name (large, semibold)
- Line 2: first 140 characters of description (smaller, italic) — omitted if description is empty
- Same visual template as the selected video template (background, color palette)

The title card is rendered as a static frame (or short fade-in from black) prepended before song 1 audio. Chapter markers are offset accordingly (song 1 `startSeconds` = `title_card_duration_seconds`).

#### Pre-export validation

Unchanged from v3, plus:
- Blocks if "Include lyrics video" is checked but the MP4 renderer would fail: no songs with completed LRC.

#### Render phases (updated)

| Phase | Description |
|---|---|
| Preparing | Fetching audio from R2; fetching LRC (user override or official); validating inputs |
| Mixing audio | FFmpeg transition mix; chapter boundary timestamps computed here |
| Rendering frames | Canvas lyric overlay + (if enabled) title card frame generation |
| Encoding video | FFmpeg H.264 encode; chapter atoms injected into MP4 container |
| Uploading | Writing MP3 + MP4 + chapters.json to R2 |
| Completed | All files available; offline cache warmed if "Make available offline" was checked |

If video-only or audio-only is selected, phases not applicable to the skipped artifact are omitted from the phase counter.

#### SSE event format (updated)

```json
{ "phase": "mixing_audio", "phaseIndex": 1, "totalPhases": 5, "percent": 40, "estimatedSecondsLeft": 360, "description": "Mixing audio…" }
{ "phase": "completed", "phaseIndex": 5, "totalPhases": 5, "percent": 100, "elapsedSeconds": 462 }
{ "phase": "failed", "error": "FFmpeg error: input file not found" }
```

#### Post-export actions

After successful export:
- **Download Audio** / **Download Video** — signed R2 URL download (unchanged from v3)
- **Share…** — opens Share dialog (§7.2)
- If "Make available offline" was checked: SW cache warming begins immediately; a progress indicator shows "Caching for offline… (240 MB)" until complete.

---

### 5.4 Settings — `/settings`

**Changes from v3:** Adds Playback Export section (font size, offline default), Offline Storage section, and Shared Links management.

#### Layout

```
┌─────────────────────────────────┐
│  ←  Settings                    │
├─────────────────────────────────┤
│  PLAYBACK                       │
│  Default gap (beats)      [2.0] │
│  Lyrics loop window (s)   [6.0] │
├─────────────────────────────────┤
│  EXPORT                         │
│  Video template   [Dark      ▾] │
│  Default resolution  [720p   ▾] │
│  Default font size   [M      ▾] │  ← new
│  Make available offline         │  ← new
│    after export     [On      ▾] │
├─────────────────────────────────┤
│  OFFLINE STORAGE                │  ← new section
│  Cached songsets: 3             │
│  Total size: 712 MB / ~2 GB     │
│  [████████░░░░░░░░░░░]          │
│                                 │
│  Sunday Morning Worship         │
│  MP4 + MP3 · 240 MB · Synced ✓ │  [Remove]
│  ─────────────────────────────  │
│  Evening Set                    │
│  MP4 + MP3 · 180 MB · Synced ✓ │  [Remove]
│  ─────────────────────────────  │
│  [    Clear all offline data   ]│
├─────────────────────────────────┤
│  SHARED LINKS                   │  ← new section
│  Sunday Morning Worship         │
│  Created 2 days ago · Never exp.│  [Revoke]
│  ─────────────────────────────  │
│  (empty if no active links)     │
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
│  Version: 0.4.0                 │
└─────────────────────────────────┘
```

#### Settings fields (additions from v3)

| Section | Field | Type | Notes |
|---|---|---|---|
| Export | Default font size | Select | S, M, L, XL; auto-save |
| Export | Make available offline after export | Toggle | On/Off; auto-save |
| Offline Storage | Cached songsets list | Read-only list | Shows each cached songset, size, sync status, Remove button |
| Offline Storage | Clear all offline data | Button | Confirmation dialog; wipes all SW caches |
| Shared Links | Active share links | Read-only list | Token, songset name, creation date, expiry, Revoke button |

---

### 5.5 Worship Playback — `/songsets/[id]/worship`

**Purpose:** Run a pre-generated lyrics video during a small-group worship session, projected to a TV or screen via HDMI/USB-C cable from a phone (or used directly on any device).

This is **not** a live production tool. It plays the pre-rendered MP4 artifact with convenient navigation controls. No real-time rendering, no LRC re-parse, no network calls during playback (when offline).

#### Pre-play card (portrait, before worship begins)

```
┌─────────────────────────────────┐
│  ←  Sunday Morning Worship      │  ← back to editor
├─────────────────────────────────┤
│  5 songs · 23m 14s              │
│                                 │
│  NOTES                          │
│  This week's theme: God's       │
│  faithfulness in uncertain      │
│  times. — Isaiah 40:31          │
│  (full description text, no     │
│   truncation)                   │
│                                 │
│  SONGS                          │
│  1.  How Great Is Our God  4:32 │
│  2.  Cornerstone           5:14 │
│  3.  Great Are You Lord    3:45 │
│  4.  Build My Life         3:50 │
│  5.  Way Maker             6:23 │
│                                 │
│  ⚠ Artifacts out of date        │  ← shown when stale
│    Songs edited after export.   │
│    [Re-export ›]                │
│                                 │
│  ✈ Ready offline (240 MB)       │  ← shown when cached
│  ⬇ Download for offline        │  ← shown when not yet cached
│    (240 MB)  [Download]         │
│                                 │
│  [   ▶  Start Worship   ]       │  ← disabled if no artifacts
│                                 │
│  [        Share…        ]       │
└─────────────────────────────────┘
```

**Pre-play card behavior:**

- Shown at `/songsets/[id]/worship` on load.
- If `latestRenderJobId` is null (never exported): shows "No export yet. Export this songset first." with a link to `/songsets/[id]/export`.
- If artifacts out of date: **`⚠ Artifacts out of date`** banner with a "Re-export ›" link. The **Start Worship** button remains enabled (user can play the old artifacts if they choose).
- If not cached offline: shows **⬇ Download for offline (X MB)** progress indicator. Start Worship is enabled and will stream from R2 (requires network).
- If cached: shows **✈ Ready offline** confirmation. Start Worship plays from cache.
- **Start Worship** button: navigates to the fullscreen player.

#### Fullscreen player (portrait phone)

Tapping **Start Worship** enters the fullscreen player. The native `<video>` element is set to fullscreen; controls are a custom overlay.

```
┌─────────────────────────────────┐
│  ← Sunday Morning Worship   [×] │  ← top bar; auto-hides after 3s; tap to show
│        How Great Is Our God     │  ← current song title
├─────────────────────────────────┤
│                                 │
│                                 │
│     (video: lyrics baked in)    │
│                                 │
│                                 │
├─────────────────────────────────┤
│  [⏮]  [⏪]  [   ▶/⏸  ]  [⏩]  [⏭] │  ← bottom controls
│  0:32 ━━━━●━━━━━━━━━━━━━  23:14 │  ← scrub bar; chapter ticks shown as |
│  🔊━━━━●━━━━      ☰ Lyrics  [⛶] │  ← volume, lyric list, fullscreen
└─────────────────────────────────┘
```

Chapter ticks on the scrub bar are thin vertical marks at each song boundary, color-coded (or white on dark background). Hovering/pressing the scrub bar shows a tooltip with the song title at that position.

**Controls (bottom bar):**

| Control | Action |
|---|---|
| `[⏮]` Prev song | Seek to start of previous song (from chapter manifest) |
| `[⏪]` Skip back | Seek −10 seconds |
| `[▶/⏸]` Play/Pause | Toggle; large (≥64×64px) tap target |
| `[⏩]` Skip forward | Seek +10 seconds |
| `[⏭]` Next song | Seek to start of next song (from chapter manifest) |
| Scrub bar | Drag to any position; chapter tick marks visible |
| `🔊` Volume | Inline slider, 0–100% |
| `☰ Lyrics` | Opens lyric jump list (see below) |
| `[⛶]` Fullscreen | Requests `document.fullscreenElement` or `webkitRequestFullscreen` |

**Top bar:**

Auto-hides 3 seconds after the last tap. Tap anywhere on the video area to reveal. Shows:
- `←` back (exits fullscreen, returns to pre-play card)
- Songset name
- Current song title (updates when chapter changes)
- `[×]` exit (same as `←`)

**Lyric jump list:**

Tapping `☰ Lyrics` slides up a bottom sheet (~70% viewport) showing all lyric lines across the entire songset, grouped by song:

```
┌─────────────────────────────────┐
│  Lyrics                     [×] │
├─────────────────────────────────┤
│  HOW GREAT IS OUR GOD          │
│  ▶ 0:28  than Jesus' blood      │  ← ▶ = current line
│     0:35  and righteousness     │
│     0:41  I dare not trust      │
│  ─────────────────────────────  │
│  CORNERSTONE                   │
│     5:00  My hope is built      │
│     5:22  On nothing less       │
│     …                           │
└─────────────────────────────────┘
```

- Timestamps shown relative to the full MP4 (not per-song).
- Tap any line → seek to that timestamp and resume playback; sheet closes.
- Current line is highlighted with `▶` marker; list auto-scrolls to keep it visible.
- Data source: `chapters.json` sidecar fetched from R2 (or SW cache).

#### Fullscreen player (landscape)

When the phone rotates to landscape (or user taps `⛶`), the video fills the full width. Controls dock to a slim bar at the bottom:

```
┌──────────────────────────────────────────────────────────────────────┐
│  ← Sunday Morning Worship                    How Great Is Our God [×]│  (auto-hides)
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│                    (video fills width)                               │
│                                                                      │
│  [⏮] [⏪]  [           ▶/⏸           ]  [⏩] [⏭]  🔊━━●━  ☰  [⛶] │
│  0:32 ━━━━━━━━━━|━━━━━━━━━━|━━━━━━━━━━|━━━━━━━━━━━━━━━━━━  23:14  │
└──────────────────────────────────────────────────────────────────────┘
```

The `|` marks on the scrub bar are song chapter ticks.

#### Wake lock and screen behavior

- Requests `navigator.wakeLock.request('screen')` on entering the fullscreen player.
- Re-acquires if wake lock is released (e.g., on tab visibility change).
- Wake lock is released on exit (back to pre-play card).
- If the API is unavailable (older browsers), no fallback — screen may sleep; noted as a known limitation.

#### Offline playback

The native `<video>` element's `src` is set to a Service Worker–intercepted URL. When the artifact is cached, the SW returns the cached response; the video plays with zero network access. When not cached, the SW fetches from the signed R2 URL (requires network).

The signed URL is fetched from the server at pre-play card load time and stored in component state. It is not fetched again during playback.

#### Audio

Audio is the MP4's own audio track. When the phone is mirrored via HDMI, audio routes through the TV's speakers automatically — no app-level configuration needed. The in-app volume slider controls the phone's media volume (via `HTMLMediaElement.volume`).

Hardware volume buttons on the phone control the same media volume. The app does not intercept them.

#### Media Session API (nice-to-have)

Populate `navigator.mediaSession.metadata` with the songset name and current song title. Register `play`, `pause`, `previoustrack`, `nexttrack` handlers for lock-screen controls. Register `seekbackward` (10s) and `seekforward` (10s).

#### Keyboard shortcuts (desktop only)

| Key | Action |
|---|---|
| `Space` | Toggle playback |
| `←` `→` | Seek −10s / +10s |
| `[` `]` | Prev / next song |

---

## 6. Offline & Sync

### 6.1 Making a songset available offline

**Trigger:** "Make available offline" checkbox on the export screen (auto-checked if user's default setting is On) or the `[⬇ Download for offline]` button on the worship pre-play card.

**Process:**
1. Next.js API mints time-limited signed R2 URLs for the MP4, MP3, and chapters.json of the latest render job.
2. The Service Worker fetches each URL and stores the response in the Cache Storage, keyed by `sow-artifacts-v1/{job_id}/output.mp4` (etc.).
3. The `songset.offlineAvailable` flag is updated in Neon (via app state). The offline badge appears on the songset list row.

**Progress feedback:** A download progress indicator is shown in the pre-play card and (if the export screen is still open) below the completion state. The indicator shows `Caching for offline… 147 / 240 MB`.

### 6.2 Cache invalidation

Each export produces a new `render_job.id`. The offline cache key embeds the job ID. After re-export:
- The new artifacts are cached under the new job ID key.
- The old job ID key is evicted during the next SW `activate` event.
- The `songset.latestRenderJobId` pointer is updated; the `offlineAvailable` flag reflects the new job.

### 6.3 Freshness detection

The `songset.artifactsOutOfDate` flag is computed server-side and returned in all `GET /api/songsets/[id]` responses. It is `true` if any of the following has a `updated_at` greater than `latest_render_job.completed_at`:
- Any `songset_item` (position change, transition param change, removal, addition)
- Any `user_lrc_override` for a recording in this songset
- The songset `name` or `description` (if `include_title_card` was set on the job)

### 6.4 Storage budget

At SW install time and before each cache-warming operation, the app calls `navigator.storage.estimate()`. If `(quota − usage) < 150 MB`, it warns: "Storage almost full. Remove a cached songset in Settings → Offline Storage before proceeding."

If the user's total cached size exceeds 2 GB, a Settings badge appears: "⚠ Offline storage: 2.1 GB".

### 6.5 iOS Safari caveats

On iOS Safari before 17.4, persistent storage for the Cache API is limited to ~1 GB per origin without explicit user permission. If quota is insufficient:
1. Show an "Add to Home Screen for full offline support" nudge (once per session).
2. If the user dismisses, warn that offline caching may fail for large songsets.
3. Request `navigator.storage.persist()` on the first offline-cache action; browser will prompt the user.

---

## 7. Sharing

### 7.1 Entry points

The **Share…** action is accessible from:
- Songset list row context menu
- Worship Playback pre-play card
- Export screen completion state

**Prerequisite:** A completed render job must exist. If no export has been done, Share… is disabled with a tooltip "Export first to share."

### 7.2 Share dialog

```
┌─────────────────────────────────┐
│  Share "Sunday Morning Worship" │
│                             [×] │
├─────────────────────────────────┤
│  [Send file]  [Share link]      │  ← tabs
│                                 │
│  ── Send file ──                │
│  Share the MP3 and MP4 files    │
│  directly via your apps.        │
│                                 │
│  MP4: 224 MB  ·  MP3: 22 MB    │
│  Total: 246 MB                  │
│                                 │
│  [  Share via WhatsApp    ]     │
│  [  Share via Line        ]     │
│  [  Share via Email       ]     │
│  [  More options…         ]     │
│                                 │
│  (Note: recipients need no      │
│   account to play the files.)   │
│                                 │
│  ── Share link ──               │
│  Anyone with the link can play  │
│  this songset in a browser.     │
│  No account required.           │
│                                 │
│  https://sow.app/share/xk9pQr…  │
│  [     Copy link      ]         │
│  [   Open share sheet ]         │
│                                 │
│  Allow recipients to download   │
│  MP4 + MP3?  (●) No  (○) Yes   │
│                                 │
│  Link expires: [Never ▾]        │
│     options: 7 days / 30 days / Never
└─────────────────────────────────┘
```

### 7.3 Send file behavior

Uses the Web Share API (`navigator.share({ files: [mp4Blob, mp3Blob] })`). The files are fetched from the offline cache (if available) or directly from R2 via a signed URL.

**Progressive enhancement:**
- If `navigator.canShare({ files })` is `false` (desktop Firefox, some desktop Safari versions): hide the per-app buttons; show "Download MP4" and "Download MP3" links instead.
- The "More options…" button calls `navigator.share()` without specifying the app, relying on the OS share sheet.

**File size warning:** If total > 100 MB, show: "Large files — sharing via WhatsApp or Line may be limited by the app. Email or direct download recommended."

### 7.4 Share link behavior

1. `POST /api/songsets/[id]/shares` with `{ renderJobId, allowDownload, expiresInDays }` → returns `{ token, url }`.
2. Token is stored in `songset_share`; URL is `https://{domain}/share/{token}`.
3. Tapping **Copy link** copies the URL to the clipboard; shows a "Copied!" toast.
4. Tapping **Open share sheet** calls `navigator.share({ url })` (text/URL share, widely supported).

**Regeneration:** If the user re-exports (new `render_job_id`), old share links continue to point to the old artifacts (which remain in R2 until manually cleaned). The user can issue a new share link for the new export. Old links are visible in Settings → Shared Links for revocation.

### 7.5 Public hosted player (`/share/[token]`)

Unauthenticated route. Server-side:
1. Validate token: not revoked, not expired.
2. Fetch `render_job` → check status is `completed`.
3. Mint short-lived (1 hour) signed R2 URLs for the MP4 and chapters.json.
4. Render server-side HTML with an embedded `<video>` player.

Player features:
- Same visual style as the Worship Playback fullscreen player (without the in-app navigation).
- Chapter navigation (prev/next song, scrub with ticks, lyric jump list).
- Volume slider.
- **Download buttons** (MP4 + MP3) — shown only if `allow_download = true` on the token.
- Branded footer: "Powered by Stream of Worship".
- No editing, no LRC review, no export controls.

If token is revoked or expired:
```
┌─────────────────────────────────┐
│  Stream of Worship              │
│                                 │
│  This link has expired or       │
│  been revoked.                  │
│                                 │
│  Ask the sender for a new link. │
└─────────────────────────────────┘
```

### 7.6 Abuse prevention

- Rate limit: max 20 active share tokens per user. Creating a 21st requires revoking an older one.
- Share links do not expose user identity or editing history.
- Signed R2 URLs expire in 1 hour; the `/share/[token]` page refreshes them on each load.

---

## 8. Global Audio Player

Unchanged from v3 §6. The Worship Playback screen has its own dedicated `<video>` element and does not use the global audio player (the two use cases don't overlap: the global player is for preview during editing; the fullscreen player is for worship).

---

## 9. Export Pipeline

### 9.1 Flow

Unchanged from v3 §7.1, with additions:

- During the `Mixing audio` phase, chapter boundary timestamps are computed (cumulative per-song duration including gap/crossfade/title-card offset) and written to `render_job.chapters` JSON column.
- If `include_title_card` is true, a title-card frame sequence is rendered during `Rendering frames` and prepended in `Encoding video`. Song chapter `startSeconds` values are all offset by `title_card_duration_seconds`.
- During `Encoding video`, MP4 chapter atoms are injected into the container (using FFmpeg's `-metadata_block_size` chapter metadata). The chapters.json sidecar is also written.
- During `Uploading`, the chapters.json sidecar is uploaded to R2 at `exports/{job_id}/chapters.json`.

### 9.2 Job persistence

Unchanged from v3 §7.2.

### 9.3 Render phases

| Phase | Description |
|---|---|
| Preparing | Fetching audio from R2; fetching LRC (user override if present, otherwise official); validating inputs. |
| Mixing audio | FFmpeg transition mix; chapter boundary timestamps computed. |
| Rendering frames | Canvas lyric overlay frame generation. Title card frames prepended if enabled. |
| Encoding video | FFmpeg H.264 encode; MP4 chapter atoms injected; chapters.json sidecar written. |
| Uploading | Writing MP3 + MP4 + chapters.json to R2; updating `render_job` to `completed`. |
| Completed | All files available. Offline cache warming begins if requested. |

### 9.4 Chapter manifest format (`chapters.json`)

```json
{
  "version": 1,
  "totalDurationSeconds": 1394.2,
  "titleCardDurationSeconds": 10,
  "songs": [
    {
      "position": 1,
      "songTitle": "How Great Is Our God",
      "startSeconds": 10.0,
      "endSeconds": 290.4,
      "lines": [
        { "text": "My hope is built on nothing less", "startSeconds": 25.2 },
        { "text": "Than Jesus' blood and righteousness", "startSeconds": 32.0 }
      ]
    },
    {
      "position": 2,
      "songTitle": "Cornerstone",
      "startSeconds": 292.4,
      "endSeconds": 610.1,
      "lines": [
        { "text": "Christ alone, Cornerstone", "startSeconds": 307.0 }
      ]
    }
  ]
}
```

`startSeconds` values include the title card offset. `lines` are sourced from the LRC used during render (user override or official), already merged into a flat list per song.

---

## 10. Responsive Design

### 10.1 Breakpoints

Unchanged from v3 §8.1.

### 10.2 Typography

Unchanged from v3 §8.2.

### 10.3 Touch targets

All interactive elements: minimum 48×48px on mobile/tablet. **Worship Playback primary controls (play/pause): minimum 64×64px.**

### 10.4 Gestures

All v3 gestures plus:

| Gesture | Element | Action |
|---|---|---|
| Tap (during worship) | Fullscreen video area | Reveal/hide top + bottom control bars |
| Swipe up | Bottom of worship player | Open lyric jump list |
| Swipe left/right | Lyric jump list row? | No — navigate is via scrub or tap; avoid accidental swipes |
| Hardware vol up/down | Worship player active | Adjusts media volume (OS handles) |

### 10.5 Worship Playback orientation

- **Portrait:** Pre-play card always portrait. Fullscreen player works in portrait; video letterboxed.
- **Landscape:** Fullscreen player fills width; controls dock to bottom. Preferred for HDMI mirror since most TVs are landscape.
- On entering fullscreen (`⛶`), attempt `screen.orientation.lock('landscape')` if supported; fall back gracefully if not.

### 10.6 Loading & error states

Unchanged from v3 §8.5. Additionally:
- Worship player: if the artifact fails to load (expired signed URL, network error), show: "Playback error. Tap to retry." with a reload button. If offline-cached, retry from cache.

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
  formattedTotalDuration: string | null;  // "23m 14s"
  updatedAt: string;  // ISO 8601
  createdAt: string;
  // v4 additions
  latestRenderJobId: string | null;
  artifactsOutOfDate: boolean;            // computed: any item/override updated after last render
  offlineAvailable: boolean;              // true if SW cache is current for latestRenderJobId
  cachedSizeBytes: number | null;         // size of cached artifacts (from SW estimate)
}
```

### 11.2 SongsetItem

Unchanged from v3 §9.2.

### 11.3 Song (catalog)

Unchanged from v3 §9.3.

### 11.4 UserLrcOverride and LyricMark

Unchanged from v3 §9.4.

### 11.5 ExportJob

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
  chaptersUrl: string | null;       // v4: R2 signed URL for chapters.json
  resolution: '720p' | '1080p';
  // v4 additions
  fontSizePreset: 'S' | 'M' | 'L' | 'XL';
  includeTitleCard: boolean;
  titleCardDurationSeconds: number;
  chapters: Chapter[] | null;       // populated on completion
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
  defaultGapBeats: number;              // 2.0
  defaultVideoTemplate: string;         // "dark"
  defaultResolution: '720p' | '1080p'; // "720p"
  lyricsLoopWindowSeconds: number;      // 6.0
  // v4 additions
  defaultFontSizePreset: 'S' | 'M' | 'L' | 'XL';   // "M"
  offlineAutoCacheAfterExport: boolean;              // true
}
```

### 11.7 SongsetShare (new)

```typescript
interface SongsetShare {
  token: string;             // 24-char URL-safe random
  songsetId: string;
  renderJobId: string;
  createdByUserId: string;
  allowDownload: boolean;
  expiresAt: string | null;  // ISO 8601; null = never
  revokedAt: string | null;  // ISO 8601; null = active
  createdAt: string;
  // computed
  isActive: boolean;         // !revokedAt && (!expiresAt || expiresAt > now)
  shareUrl: string;          // https://{domain}/share/{token}
}
```

### 11.8 SongEmbedding (new)

```typescript
interface SongEmbedding {
  songId: string;
  modelVersion: string;   // e.g. "bge-m3-v1.0"
  createdAt: string;
  // embedding vector stored in Neon pgvector; not returned to client directly
}
```

### 11.9 SemanticSearchResult (new)

```typescript
interface SemanticSearchResult extends Song {
  similarityScore: number;      // 0.0–1.0 cosine similarity
  matchingSnippet: string;      // top matching lyric line or phrase
  whyThisMatch: string[];       // top 2 lyric lines (for "Why this match?" expand)
}
```

---

## 12. Non-Functional Requirements

| Requirement | Target |
|---|---|
| Initial page load (LCP) | < 2.5s on 4G tablet |
| Time to interactive | < 4s on tablet |
| Audio playback start latency | < 1s after tap |
| **Worship playback start latency (offline)** | **< 500ms from tap** |
| **Worship playback start latency (streaming)** | **< 2s on 4G** |
| Export function timeout budget | 800s (Pro + Fluid Compute); default 720p to stay under budget |
| Semantic search latency | < 1s p95 for `POST /api/songs/search/semantic` |
| Accessibility | WCAG 2.1 AA (keyboard nav, ARIA labels, sufficient contrast) |
| Browser support | Chrome 110+, Safari 16+, Firefox 120+, Chrome Android, Safari iOS |
| Minimum font size | 16px on mobile/tablet |
| Touch target size | ≥ 48×48px on touch devices; ≥ 64×64px for worship playback primary controls |
| Vercel plan required | Pro (for `maxDuration: 800` and Fluid Compute) |
| **Service Worker** | **Required; Workbox recommended** |
| **Offline playback** | **Zero-network once cached; SW cache keyed by render_job_id** |
| **Wake lock** | **Active during worship playback; released on exit** |
| **Public share page LCP** | **< 2s on 4G** |
| **Share token max per user** | **20 active tokens** |

---

## 13. Key Changes from v3

| Area | v3 | v4 | Reason |
|---|---|---|---|
| Primary persona | Single: worship leader prepping a video | **Two:** prep persona (v3) + **worship persona** (small-group leader running worship from phone→TV) | New use case surfaced in user interview |
| "Stage tool" stance | Explicitly out of scope | **Retired.** Worship Playback is in scope as a media player for pre-rendered artifacts | v3 framing was too broad |
| Worship Playback | Not in spec | **New route `/songsets/[id]/worship`**: pre-play card + fullscreen player with chapter nav, scrub bar, ±10s, lyric jump list, wake lock | New use case |
| Offline | Network required | **Full offline** via Service Worker + Cache Storage; artifacts keyed by render_job_id; storage quota management | Venue Wi-Fi unreliable; worship must not stutter |
| Artifact freshness | Not tracked | **`artifactsOutOfDate`** computed flag; stale banner in editor and worship pre-play card | User needs to know when to re-export |
| Search mode | Title / lyrics / composer pills + structural filters | Adds **Describe mode** (semantic search via pgvector); mode tabs in Browse Sheet | User wants to search by message/theme, not just keywords |
| Semantic backend | None | `pgvector` on Neon; `song_embedding` table; new Analysis Service embedding worker; multilingual model | Enables "songs about God's faithfulness" queries |
| Sharing | Download buttons only | **Share dialog** with two tabs: Send file (Web Share API) + Share link (public hosted player at `/share/[token]`) | User wants to share via WhatsApp/Line/Email |
| Public player | None | `/share/[token]` — unauthenticated server-rendered video player; optional download if `allow_download` is set | Recipients need no account |
| Share management | None | Settings → Shared Links; revoke, expiry (7d / 30d / never) | Token hygiene |
| Font size | Fixed | **Per-export preset S/M/L/XL** (32/42/54/68px at 720p; 48/63/81/102px at 1080p) | Small-group leader found default too small on TV |
| Title card | None | Optional opening title card (5/10/15s) with songset name + description; baked into MP4 | Displays theme/verse before worship begins |
| Chapter manifest | None | `chapters.json` sidecar (R2); chapter atoms in MP4 container; drives chapter nav and lyric jump list | Required for next/prev song and lyric seek during playback |
| Export → offline | Separate action | **"Make available offline" checkbox** on export screen (default On); warms SW cache immediately after export | Removes a step before worship |
| Settings | Playback + Export + Catalog + Account | Adds **Offline Storage section** (cached songsets, evict) and **Shared Links section** (revoke) | New management surfaces |
| Export options | Resolution + template | Adds font size, title card, post-export offline cache | See above |
| Render phases | 4 phases | **5 phases** (adds title card rendering; chapter marker generation is part of mixing_audio) | New render outputs |
| Architecture | Next.js + Neon + R2 | Same, plus **Service Worker (Workbox)**, **pgvector on Neon**, **`song_embedding` table**, **`songset_share` table**, **Analysis Service embedding worker** | New capabilities |
| Keyboard shortcuts | Space + arrows | Same + `[` / `]` for prev/next song in worship playback | Desktop worship mode |
| Pull-to-refresh | Not implemented | Not implemented | Unchanged |
| v3 prep features | All present | **All unchanged:** editor, transitions, lyrics review (Review/Edit text/Edit timing), lyric marks, LRC overrides, global audio player | Additive release |

---

## 14. Open Questions

The following decisions are deferred but must be resolved before implementation of the relevant features:

| Question | Area | Notes |
|---|---|---|
| Embedding model | Semantic search | Must be ≤1024 dims, multilingual (Chinese + English), runnable in Analysis Service Docker. Candidates: `bge-m3`, `nomic-embed-text-v1.5`. Or use an external API (OpenAI text-embedding-3-small). Decision gates the `song_embedding` worker implementation. |
| Embedding hosting | Semantic search | CPU-only ONNX in Vercel handler vs. external API vs. batch-only (offline). If offline-only, queries would require a server-side embedding call to an external API at query time. |
| MP4 chapter atom support | Worship Playback | Most browsers ignore MP4 chapter atoms in `<video>` elements. The chapters.json sidecar is the authoritative source for the app. Chapter atoms are a nice-to-have for native media players (e.g. VLC on Apple TV). Confirm FFmpeg chapter injection works in the `skia-canvas`/`ffmpeg-static` build. |
| iOS Safari PWA storage cap | Offline | Safari 17.4+ relaxed the 1 GB cap; earlier versions are limited. Confirm the minimum iOS version to support and whether to gate offline support behind "Add to Home Screen." |
| Share file size limits | Sharing | WhatsApp's file-size limit for sharing is ~2 GB per file; Line is ~1 GB. A 30-min 1080p MP4 could exceed these. The spec currently surfaces a file-size warning; confirm the exact cutoff values and whether to suggest chunking or the hosted-player link instead for large sets. |
| Revocation of cached artifacts | Sharing | If a user revokes a share link but the recipient has the MP4 downloaded locally, revocation only stops future streaming via the `/share/[token]` route; it does not delete local files. This is expected behavior for file-based sharing; should be noted in the share dialog. |
