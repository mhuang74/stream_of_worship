# Developer Documentation

This document contains technical details for developers and contributors. For user-facing instructions, see [README.md](README.md).

---

## Table of Contents

1. [Project Status](#project-status)
2. [Architecture Overview](#architecture-overview)
3. [Backend Services](#backend-services)
4. [POC Analysis Setup](#poc-analysis-setup)
5. [Project Structure](#project-structure)
6. [Development Roadmap](#development-roadmap)
7. [Advanced Configuration](#advanced-configuration)
8. [Troubleshooting](#troubleshooting)

---

## Project Status

**Current Phase:** Web App production, Android App available, Admin CLI operational, Analysis Service running  
**Architecture:** Seven-component system with shared PostgreSQL (Neon) database

### Components Status

| Component | Status | Location | Purpose |
|-----------|--------|----------|---------|
| **POC Scripts** | ✅ Archived | `lab/poc-scripts/` | Experimental analysis validation (legacy) |
| **Admin CLI** | ✅ Operational | `ops/admin-cli/src/stream_of_worship/admin/` | Catalog management, audio download, schema init |
| **Analysis Service** | ✅ Operational | `ops/analysis-service/` | Audio analysis, stem separation, LRC generation |
| **User App** | ⚠️ Deprecated | `lab/sow-app/src/sow_lab_app/` | TUI (deprecated in favor of Web App) |
| **Web App** | ✅ Production | `delivery/webapp/` | Primary end-user interface (Next.js) |
| **Android App** | ✅ Available | `delivery/android/` | Native mobile client (Kotlin/Jetpack Compose) |
| **Render Worker** | ✅ Production | `delivery/render-worker/` | AWS Lambda render processing |

---

## Architecture Overview

The project consists of **seven architecturally separate components**:

### 1. 🧪 POC Scripts (Archived Experimental)
- **Location:** `lab/poc-scripts/` directory
- **Purpose:** Validate analysis algorithms during development
- **Runtime:** One-off script execution in Docker
- **Technologies:** Librosa (signal processing) or All-In-One (deep learning)
- **Status:** Archived. The `lab/poc-scripts/transition_builder_v2/` TUI lives on as the `stream-of-worship tui` command but is also deprecated.

### 2. 🖥️ Admin CLI (Backend Management)
- **Location:** `ops/admin-cli/src/stream_of_worship/admin/` (Python package)
- **Purpose:** Backend tool for catalog management and audio operations
- **Users:** Administrators, DevOps
- **Runtime:** One-shot CLI commands (`sow-admin catalog scrape`, `sow-admin audio download`)
- **Dependencies:** **Lightweight** (~50MB) - typer, rich, psycopg3, boto3, yt-dlp
- **Database:** **PostgreSQL (Neon)** via `psycopg` (psycopg3, synchronous) with `ConnectionProvider` for auto-reconnect and cold-start retry
- **Installation:** `uv run --project ops/admin-cli --extra admin sow-admin`

### 3. 🚀 Analysis Service (Microservice)
- **Location:** `ops/analysis-service/` (separate package: `sow_analysis`)
- **Purpose:** CPU/GPU-intensive audio analysis and stem separation
- **Users:** Called by Admin CLI or Web App
- **Runtime:** Long-lived FastAPI HTTP server (port 8000)
- **Technologies:** FastAPI, PyTorch, allin1, Demucs, audio-separator, Cloudflare R2
- **Dependencies:** **Heavy** (~2GB) - PyTorch, ML models, NATTEN
- **Database:** **SQLite** (via `aiosqlite`) for job queue persistence only — **not** connected to the shared PostgreSQL
- **Deployment:** Docker container with platform-specific builds (x86_64 vs ARM64)
- **API:** REST endpoints at `http://localhost:8000/api/v1/`

### 4. 🎵 User App (Deprecated)
- **Location:** `lab/sow-app/src/sow_lab_app/` (Python package)
- **Purpose:** Interactive TUI for transitions and lyrics video generation (**deprecated**)
- **Users:** Worship leaders, media team members (migrating to Web App)
- **Runtime:** TUI (Textual framework)
- **Technologies:** Textual (TUI), psycopg3, Pydub, Pillow, FFmpeg
- **Database:** **PostgreSQL (Neon)** via shared `ConnectionProvider` — migration status uncertain
- **Note:** The Web App (`sow-webapp`) is now the recommended interface for all end-user operations.

### 5. 🌐 Web App (Primary End-User Interface)
- **Location:** `delivery/webapp/` (Node.js/TypeScript, Next.js 16 App Router)
- **Purpose:** Browser-based worship set editor and playback
- **Users:** Worship leaders, media teams, end users
- **Runtime:** Next.js server (Vercel deployment) + browser client
- **Technologies:** Next.js 16, Drizzle ORM, Better Auth, pgvector, Cloudflare R2
- **Database:** **PostgreSQL (Neon)** via `@neondatabase/serverless` + Drizzle ORM
- **Auth:** Better Auth with `drizzleAdapter`
- **Key Features:**
  - Browse and search song catalog (full-text tsvector + semantic pgvector search)
  - Create and manage multi-song worship sets with transition configuration
  - Submit render jobs (processed asynchronously via AWS Lambda)
  - Real-time progress via SSE (Server-Sent Events)
  - Built-in playback controller with synchronized lyrics
  - Second-screen projection via W3C Presentation API or Google Cast
  - LRC lyrics review and editing
  - Shareable public player links

### 6. 📱 Android App (Native Mobile Client)
- **Location:** `delivery/android/` (Kotlin/Jetpack Compose Gradle project)
- **Purpose:** Native mobile delivery client for worship set editing, render submission/status, playback, sharing, settings, and offline downloads
- **Users:** Worship leaders on Android devices
- **Runtime:** Native Android app (min SDK 26 / Android 8.0+)
- **Technologies:** Jetpack Compose, AndroidX Navigation, Retrofit/OkHttp, Better Auth cookies, Media3 ExoPlayer, Android DownloadManager, kotlinx.serialization, DataStore
- **Dependencies:** Kotlin, AGP, Jetpack Compose, Media3, Retrofit, OkHttp, Robolectric, Kover
- **Database:** **None directly.** Consumes the webapp JSON APIs only — does not connect to PostgreSQL, Cloudflare R2, or AWS SQS.
- **Auth:** Better Auth cookies stored in Android-encrypted storage and forwarded by OkHttp
- **Key Features:**
  - Better Auth email/password login, registration, session restore, and sign-out
  - Songset list/detail editing with song search, item reorder, transition parameter editing
  - Render submission and status polling for audio/video jobs
  - Media3 playback of rendered MP4/MP3 with chapters, lyrics, fullscreen, media controls, and wake-lock
  - Share-token creation and Android share/view intents
  - User settings editing
  - Offline artifact downloads tracked in app-private metadata
- **Build/Test Commands:** `./gradlew testDebugUnitTest`, `./gradlew koverXmlReport`, `./gradlew lintDebug`, `./gradlew assembleDebug` (run from `delivery/android/`)
- **Configuration:** API base URL per build variant via `delivery/android/gradle.properties` (`sow.apiBaseUrl.debug`/`.staging`/`.release`)
- **Boundary:** The Android app uses only the webapp JSON APIs. It does not connect directly to PostgreSQL, Cloudflare R2, or AWS SQS.

#### Build and Load the Android App on a Phone

Use this flow when testing the native Android app on a physical device.

1. Install local tools.
   - Install Android Studio with JDK 17 support.
   - Install Android SDK 35 from Android Studio's SDK Manager.
   - Make sure Android platform tools are available. `adb version` should print
     an installed Android Debug Bridge version.

2. Prepare the phone.
   - On the phone, open **Settings > About phone** and tap **Build number** seven
     times to enable Developer options.
   - Open **Settings > System > Developer options** and enable **USB debugging**.
   - Connect the phone by USB and accept the debugging authorization prompt.
   - Verify the device is visible:

     ```bash
     cd delivery/android
     adb devices
     ```

     The phone should appear as `device`, not `unauthorized`.

3. Start a reachable webapp backend.
   - For local backend testing, start the webapp from the repository root:

     ```bash
     pnpm --filter sow-webapp dev
     ```

   - The dev server listens on `0.0.0.0:8080`.
   - Keep the computer and phone on the same network.
   - Find the computer's LAN IP address, for example `192.168.1.25`.
   - If the OS firewall blocks inbound connections, allow port `8080`.

4. Build a debug APK for the phone.
   - Use the computer's LAN IP as the Android API base URL. Do not use
     `10.0.2.2` for a physical phone; that address is only for the Android
     emulator.

     ```bash
     cd delivery/android
     ./gradlew assembleDebug -Psow.apiBaseUrl.debug=http://192.168.1.25:8080
     ```

   - Replace `192.168.1.25` with the actual LAN IP of the development machine.
   - For staging or production-style testing, point the property at an HTTPS
     backend instead:

     ```bash
     ./gradlew assembleDebug -Psow.apiBaseUrl.debug=https://staging.example.com
     ```

5. Install the APK on the connected phone.

   ```bash
   cd delivery/android
   adb install -r app/build/outputs/apk/debug/app-debug.apk
   ```

   The `-r` flag replaces an existing debug install while preserving app data
   when Android allows it.

6. Launch and test the app.
   - Open **Stream of Worship** from the phone launcher.
   - Sign in or register through the webapp-backed Better Auth flow.
   - Validate the primary user workflows: songset list/detail editing, song
     search, render submission/status, signed URL playback, sharing, settings,
     and offline download preparation.

7. Optional: install and launch from Gradle.

   ```bash
   cd delivery/android
   ./gradlew installDebug -Psow.apiBaseUrl.debug=http://192.168.1.25:8080
   ```

   Android Studio can also run the `app` configuration directly on the connected
   phone. Set the same `sow.apiBaseUrl.debug` Gradle property when testing
   against a local webapp.

8. Troubleshoot common phone issues.
   - If `adb devices` shows `unauthorized`, unplug the phone, revoke USB
     debugging authorizations in Developer options, reconnect, and accept the
     prompt again.
   - If login succeeds but later API calls are unauthenticated, confirm the
     Android app uses one consistent scheme and host for every request. Better
     Auth cookies are host-specific.
   - If the app cannot reach the local backend, open `http://<computer-lan-ip>:8080`
     from the phone browser first. If that fails, fix Wi-Fi, VPN, or firewall
     access before debugging the app.
   - If local auth rejects the phone origin, align the webapp `BETTER_AUTH_URL`
     with the externally reachable origin where possible. The webapp also trusts
     private-network origins matching `192.168.*`, `10.*`, and `172.16.*`.

### 7. ⚡ Render Worker (AWS Lambda)
- **Location:** `delivery/render-worker/` (Python, deployed as Lambda container via private ECR)
- **Purpose:** Serverless render processing (audio mixing + video encoding)
- **Users:** Called by Web App via SQS
- **Runtime:** AWS Lambda container (triggered by SQS events)
- **Technologies:** psycopg2, boto3, Pillow, FFmpeg, Cloudflare R2
- **Dependencies:** **Moderate** — psycopg2-binary, boto3, Pillow, ffmpeg-python
- **Database:** **PostgreSQL (Neon)** via `psycopg2` (synchronous, connection string)
- **Queue:** AWS SQS (render jobs enqueued by Web App, processed by Lambda)
- **Deployment:** Docker container → private AWS ECR → Lambda function

### Why Architecturally Separate?

| Concern | Admin CLI | Analysis Service | User App (Dep.) | Web App | Android App | Render Worker |
|---------|-----------|------------------|-----------------|---------|-------------|---------------|
| **Runtime Model** | One-shot commands | Long-lived daemon | Interactive TUI | Serverless + browser | Native Android app | Event-driven Lambda |
| **Target Users** | Admins / DevOps | Internal service | End users (legacy) | End users | End users (mobile) | Internal service |
| **Dependencies** | Minimal | Very heavy (PyTorch) | Moderate | Node.js stack | Kotlin / Jetpack Compose | Moderate (psycopg2, FFmpeg) |
| **Distribution** | `uv run --project ops/admin-cli --extra admin sow-admin` | Docker image | `uv run --project lab/sow-app sow-app` | Vercel | APK (`./gradlew assembleDebug`) | Lambda container |
| **Data Access** | PostgreSQL (Neon) + R2 | R2 + SQLite (jobs) | PostgreSQL (Neon) + R2 | PostgreSQL (Neon) + R2 | Webapp JSON APIs only | PostgreSQL (Neon) + R2 |
| **Database Driver** | psycopg3 | aiosqlite | psycopg3 | Drizzle ORM + Neon | None (API client) | psycopg2 |

### Shared Database Architecture

All components except the Analysis Service share a **single PostgreSQL database hosted on Neon**:

```
                    ┌─────────────────────────────┐
                    │   PostgreSQL (Neon)         │
                    │                             │
                    │  Catalog Tables:            │
                    │    songs, recordings        │
                    │    song_embedding,          │
                    │    song_line_embedding      │
                    │  Auth Tables (Better Auth): │
                    │    user, account, session,  │
                    │    verification             │
                    │  App Tables:                │
                    │    songsets, songset_items  │
                    │    render_jobs              │
                    │    user_settings,           │
                    │    user_lrc_override,       │
                    │    lyric_mark, songset_share│
                    └────────┬────────────────────┘
                             │
            ┌────────────────┼────────────────┐
            │                │                │
     ┌──────┴──────┐  ┌─────┴──────┐  ┌─────┴──────┐
     │ Admin CLI   │  │  Web App   │  │Render Worker│
     │ (psycopg3)  │  │(Drizzle)   │  │ (psycopg2)  │
     └─────────────┘  └────────────┘  └────────────┘
            │                │                │
            └────────────────┼────────────────┘
                             │
                    ┌────────┴────────┐
                    │  Cloudflare R2  │
                    │  (audio, stems, │
                    │   LRC, videos)  │
                    └─────────────────┘
```

**Key Design Decisions:**
1. **Admin CLI** never imports PyTorch/ML libraries. It manages catalog and submits jobs to Analysis Service via HTTP.
2. **Analysis Service** is the only component with heavy ML dependencies and uses SQLite only for its internal job queue (not connected to shared PostgreSQL).
3. **Web App** is the primary end-user interface, using Drizzle ORM with Neon's serverless driver.
4. **Render Worker** shares the same PostgreSQL database as the Web App for render job status tracking.
5. **User App** is deprecated; all new development should target the Web App.

### Component Interaction

```
Backend Flow (Admin):
┌──────────────────┐
│  Admin CLI       │  ← Lightweight, runs on admin's machine
│  (sow-admin)     │
└────────┬─────────┘
         │
         ├─── catalog scrape ──→ sop.org → PostgreSQL (Neon)
         │
         ├─── audio download ──→ YouTube → R2 upload → PostgreSQL
         │
         └─── audio analyze ──→ HTTP POST /api/v1/jobs/analyze
                                          ↓
                         ┌────────────────────────────┐
                         │  Analysis Service          │  ← Heavy ML, Docker
                         │  (FastAPI + Job Queue)     │
                         └────────────┬───────────────┘
                                      │
                         ┌────────────┴────────────┐
                         ↓                         ↓
                  ┌─────────────┐         ┌─────────────┐
                  │ allin1      │         │ Demucs      │
                  │ worker      │         │ worker      │
                  └──────┬──────┘         └──────┬──────┘
                         │                       │
                         └───────────┬───────────┘
                                     ↓
                            ┌─────────────────┐
                            │ Cloudflare R2   │  → Stems, JSON, LRC
                            └─────────────────┘

Frontend Flow (End-User):
┌──────────────────┐
│  Web App         │  ← Next.js on Vercel
│  (sow-webapp)    │
└────────┬─────────┘
         │
         ├─── read/write catalog metadata ──→ PostgreSQL (Neon)
         │                                      (via Drizzle ORM)
         ├─── read/write songsets ──────────→ PostgreSQL (Neon)
         │
         ├─── submit render job ────────────→ PostgreSQL (Neon)
         │            │
         │            └─── SQS enqueue ──→ AWS Lambda (Render Worker)
         │                                  │
         │                                  ├─── fetch songset from DB
         │                                  ├─── download audio/stems from R2
         │                                  ├─── mix audio + render video
         │                                  └─── upload to R2 + update DB
         │
         ├─── download audio/stems ───→ R2 (read-only)
         │
         └─── poll render progress ───→ PostgreSQL (Neon) via SSE
```

---

## Backend Services

The project includes two backend microservices:

### 1. Analysis Service (`ops/analysis-service/`)

FastAPI-based audio analysis service with job queue management.

- **Port:** 8000
- **Purpose:** Audio analysis (tempo, key, beats, sections, embeddings), stem separation, LRC generation
- **Technologies:** FastAPI, PyTorch, allin1, Demucs, audio-separator, Whisper
- **Database:** SQLite (job persistence only, not connected to shared PostgreSQL)
- **Status:** Operational

**Documentation:** [ops/analysis-service/README.md](ops/analysis-service/README.md)

### 2. Render Worker (`delivery/render-worker/`)

AWS Lambda container that processes render jobs from an SQS queue.

- **Purpose:** Audio mixing (FFmpeg) + lyrics video encoding (Pillow + FFmpeg)
- **Technologies:** psycopg2, boto3, Pillow, FFmpeg
- **Database:** PostgreSQL (Neon) via psycopg2
- **Queue:** AWS SQS
- **Status:** Operational

**Documentation:** [delivery/render-worker/README.md](delivery/render-worker/README.md)

---

## POC Analysis Setup

For developers who need to run the experimental analysis scripts.

### Prerequisites

1. **Docker Desktop** installed and running
2. **3-5 worship songs** in MP3 or FLAC format
3. **Terminal/Command Prompt** access

### Step 1: Prepare Audio Files

```bash
# Place test worship songs into poc_audio/
cp /path/to/your/songs/*.mp3 poc_audio/

# Verify files were copied
ls poc_audio/
```

### Step 2: Build Docker Image

```bash
# Build the Docker image (first time only)
docker-compose build
```

### Step 3: Run POC Analysis

**Method A: Command-Line Script (Recommended)**

```bash
# Run POC analysis in one-off container
docker-compose run --rm librosa python lab/poc-scripts/poc_analysis.py
```

**Method B: Interactive Jupyter Notebook**

```bash
# Start Jupyter Lab
docker-compose up

# Open browser to http://localhost:8888
# Navigate to notebooks/01_POC_Analysis.ipynb
```

### Alternative: All-In-One Deep Learning Analysis

For ML-based analysis with semantic segment labels:

```bash
# Build All-In-One image (10-20 min first time)
docker compose -f docker/docker-compose.allinone.yml build

# Run analysis
docker compose -f docker/docker-compose.allinone.yml run --rm allinone python lab/poc-scripts/poc_analysis_allinone.py
```

**Comparison:**

| Feature | Librosa (Traditional) | All-In-One (Deep Learning) |
|---------|----------------------|---------------------------|
| **Tempo Detection** | Signal processing | Neural network |
| **Segment Labels** | Generic (section_0) | Semantic (verse, chorus) |
| **Embeddings** | MFCCs | Learned 24-dim |
| **Speed** | ~30-60s/song | ~2-3 min/song |
| **Setup** | Lightweight | ~2-3 GB PyTorch |

---

## Project Structure

```
sow_cli_admin/                           # Repository root
│
├── ops/admin-cli/src/stream_of_worship/admin/         # 🖥️ Admin CLI Package (backend)
│   ├── commands/                        #    CLI command groups
│   │   ├── db.py                        #    - db init/status/url
│   │   ├── catalog.py                   #    - catalog scrape/list/search/show
│   │   ├── audio.py                     #    - audio download/list/analyze/lrc/align
│   │   └── config.py                    #    - config show/set/path
│   ├── services/                        #    Business logic
│   │   ├── scraper.py                   #    - HTML scraping (sop.org)
│   │   ├── youtube.py                   #    - yt-dlp wrapper
│   │   ├── hasher.py                    #    - SHA-256 hashing
│   │   └── r2.py                        #    - R2 storage client
│   ├── db/                              #    Database layer
│   │   ├── client.py                    #    - DatabaseClient (psycopg3)
│   │   ├── schema.py                    #    - SQL schema DDL
│   │   └── models.py                    #    - Song, Recording dataclasses
│   ├── config.py                        #    TOML config loader
│   └── main.py                          #    Typer app entry point
│
├── lab/sow-app/src/sow_lab_app/           # 🎵 User App Package (DEPRECATED)
│   ├── screens/                           #    TUI screens (Textual)
│   │   ├── generation.py                #    - Transition generator
│   │   ├── browser.py                   #    - Song catalog browser
│   │   └── songset_manager.py           #    - Songset management
│   ├── services/                        #    Business logic
│   │   ├── audio_engine.py             #    - Audio processing
│   │   ├── video_engine.py             #    - Video generation
│   │   ├── asset_cache.py              #    - R2 asset management
│   │   └── turso_client.py             #    - Legacy (unused, kept for compat)
│   ├── db/                              #    Database layer (psycopg3)
│   │   ├── read_client.py              #    - ReadOnlyClient (catalog)
│   │   ├── songset_client.py           #    - SongsetClient (CRUD)
│   │   ├── schema.py                   #    - App-specific schema
│   │   └── user_data_schema.py         #    - Per-user schema
│   ├── config.py                        #    TOML config loader
│   └── main.py                          #    App entry point
│
├── ops/admin-cli/src/stream_of_worship/db/            # Shared database infrastructure
│   ├── connection.py                    #    - ConnectionProvider (psycopg3)
│   └── postgres_schema.py               #    - Unified schema DDL (all components)
│
├── delivery/webapp/                              # 🌐 Web App (Next.js)
│   ├── src/
│   │   ├── app/                         #    Next.js App Router pages
│   │   │   ├── api/                     #    API routes
│   │   │   │   ├── songs/               #    Catalog APIs
│   │   │   │   ├── songsets/            #    Songset CRUD
│   │   │   │   ├── render-jobs/         #    Render job management + SSE
│   │   │   │   ├── auth/                #    Better Auth endpoints
│   │   │   │   └── ...
│   │   │   └── ...
│   │   ├── db/                          #    Database client + schema
│   │   │   ├── index.ts                 #    Drizzle + Neon client
│   │   │   └── schema.ts                #    Full schema (15 tables)
│   │   ├── lib/
│   │   │   ├── auth.ts                  #    Better Auth config
│   │   │   └── db/                      #    DB query functions
│   │   └── test/                        #    Test utilities
│   ├── drizzle/                         #    Drizzle migration files
│   ├── drizzle.config.ts                #    Drizzle Kit config
│   └── package.json                     #    Node.js dependencies
│
├── ops/analysis-service/                   # 🚀 Analysis Service (heavy ML)
│   ├── src/sow_analysis/                #    Service package
│   │   ├── main.py                      #    FastAPI app
│   │   ├── config.py                    #    Pydantic settings
│   │   ├── models.py                    #    Pydantic models
│   │   ├── routes/                      #    API endpoints
│   │   │   ├── health.py
│   │   │   └── jobs.py
│   │   ├── storage/                     #    R2 and cache clients
│   │   │   ├── cache.py
│   │   │   ├── db.py                    #    SQLite job persistence
│   │   │   └── r2.py
│   │   └── workers/                     #    Background job processors
│   │       ├── analyzer.py
│   │       ├── lrc.py
│   │       ├── queue.py
│   │       ├── separator.py
│   │       ├── stem_separation.py
│   │       └── separator_wrapper.py
│   ├── docker-compose.yml               #    Docker Compose config
│   ├── Dockerfile                       #    Multi-platform Docker build
│   └── README.md                        #    Service documentation
│
├── delivery/render-worker/              # ⚡ Render Worker (AWS Lambda)
│   ├── src/sow_render_worker/           #    Worker package
│   │   ├── lambda_handler.py            #    SQS event handler
│   │   ├── config.py                    #    Env var loading
│   │   ├── pipeline.py                  #    5-phase render orchestrator
│   │   ├── audio_engine.py              #    FFmpeg audio mixing
│   │   ├── video_engine.py              #    FFmpeg video encoding
│   │   ├── frame_renderer.py            #    Pillow frame rendering
│   │   ├── db.py                        #    psycopg2 DB operations
│   │   └── r2_client.py                 #    boto3 R2 client
│   ├── tests/                           #    Test suite
│   ├── Dockerfile                       #    Lambda container image
│   └── README.md                        #    Worker documentation
│
├── delivery/android/                    # 📱 Android App (native mobile client)
│   ├── app/
│   │   ├── src/main/java/org/streamofworship/android/
│   │   │   ├── core/                    #    Config, design, navigation, network, session, download
│   │   │   ├── data/                    #    Repositories: songsets, songs, render, playback, share, settings, offline
│   │   │   └── feature/                 #    Feature screens: auth, songsets, render, player, share, settings
│   │   ├── src/test/java/...            #    JVM/Robolectric unit tests
│   │   ├── build.gradle.kts             #    App module config (Compose, dependencies, Kover)
│   │   └── src/main/AndroidManifest.xml
│   ├── settings.gradle.kts              #    Root Gradle settings
│   ├── build.gradle.kts                 #    Root Gradle config
│   ├── gradle.properties                #    API base URLs per variant + build flags
│   └── README.md                        #    Android app documentation
│
├── lab/poc-scripts/                                 # 🧪 POC Scripts (archived)
│   ├── docker/                          #    POC Docker environments
│   ├── poc_analysis.py                  #    Librosa analysis script
│   ├── poc_analysis_allinone.py         #    Deep learning analysis
│   └── transition_builder_v2/           #    Legacy TUI
│
├── tests/                               # Test suites
│   ├── admin/                           #    Admin CLI tests
│   ├── app/                             #    User App tests
│   └── services/                        #    Service tests
│
├── specs/                               # Design documents
├── reports/                             # Implementation plans
├── pyproject.toml                       # Root project config
├── README.md                            # User-facing documentation
└── DEVELOPER.md                         # This file
```

### Key Separation Points

| Directory | Package Name | Purpose | Target Users | Database | Deployment |
|-----------|-------------|---------|--------------|----------|------------|
| `ops/admin-cli/src/stream_of_worship/admin/` | `stream-of-worship-admin` | Backend management CLI | Admins / DevOps | PostgreSQL (Neon) + R2 | `uv run --project ops/admin-cli --extra admin sow-admin` |
| `lab/sow-app/src/sow_lab_app/` | `stream-of-worship-app` | End-user TUI (DEPRECATED) | End users (legacy) | PostgreSQL (Neon) + R2 | `uv run --project lab/sow-app sow-app` |
| `ops/analysis-service/` | `sow-analysis` | Audio analysis microservice | Internal service | SQLite (jobs only) + R2 | Docker image |
| `delivery/webapp/` | `sow-webapp` | Web application | End users | PostgreSQL (Neon) + R2 | Vercel |
| `delivery/android/` | `stream-of-worship-android` | Native mobile client | End users (mobile) | Webapp JSON APIs only (no direct DB/R2/SQS) | `cd delivery/android && ./gradlew assembleDebug` |
| `delivery/render-worker/` | `sow-render-worker` | Render processing | Internal service | PostgreSQL (Neon) + R2 | Lambda container |
| `lab/poc-scripts/` | N/A (scripts) | Experimental validation | Developers | Local files only | Local scripts |

---

## Development Roadmap

### ✅ Phase 1: Foundation (Complete)
- [x] CLI scaffold (Typer)
- [x] Database schema (PostgreSQL/Neon)
- [x] Configuration (TOML)
- [x] `db` command group (init, status, url)

### ✅ Phase 2: Catalog Management (Complete)
- [x] Web scraper for sop.org
- [x] Song ID normalization (Chinese → pinyin)
- [x] `catalog` command group (scrape, list, search, show)
- [x] Incremental scraping

### ✅ Phase 3: Audio Download (Complete)
- [x] YouTube search and download (yt-dlp)
- [x] Content-hash based deduplication (SHA-256)
- [x] Cloudflare R2 upload
- [x] `audio` command group (download, list, show)
- [x] Recording metadata tracking

### ✅ Phase 4: Analysis Service (Complete)
- [x] FastAPI service architecture
- [x] Job queue (in-memory + SQLite persistence)
- [x] allin1 worker (tempo, key, beats, sections, embeddings)
- [x] Demucs worker (stem separation)
- [x] Clean vocals pipeline (MelBand Roformer + UVR-De-Echo)
- [x] LRC generation (Whisper + LLM alignment + forced aligner)
- [x] R2 stems upload
- [x] Docker deployment (x86_64 + ARM64 support)
- [x] CLI integration (`audio analyze`, `audio status`)

### ✅ Phase 5: CLI ↔ Service Integration (Complete)
- [x] `audio analyze` command (submit jobs via HTTP)
- [x] `audio status` command (poll job status)
- [x] `audio lrc` command (submit LRC generation)
- [x] `audio align-lrc` command (local forced alignment)
- [x] Retry logic and error handling
- [x] Progress indicators

### ✅ Phase 6: LRC Generation (Complete)
- [x] Whisper transcription worker
- [x] LLM line alignment (OpenAI-compatible API)
- [x] Forced aligner refinement (Qwen3 Forced Aligner)
- [x] LRC file generation and R2 upload
- [x] `lyrics generate` command (via `audio lrc`)
- [x] DashScope Qwen3 ASR integration (optional)

### ✅ Phase 7: Database Migration to PostgreSQL/Neon (Complete)
- [x] Migrated from Turso/SQLite to PostgreSQL (Neon)
- [x] Admin CLI uses psycopg3 with ConnectionProvider
- [x] Web App uses Drizzle ORM with Neon serverless driver
- [x] Render Worker uses psycopg2 with connection string
- [x] User App uses psycopg3 (migration status uncertain)
- [x] Unified schema via `postgres_schema.py`
- [x] Better Auth integration with drizzleAdapter
- [x] pgvector for semantic search (song_embedding, song_line_embedding)
- [x] Full-text search via tsvector with GIN index
- [x] Removed old Turso sync infrastructure

### ✅ Phase 8: Web App (Complete)
- [x] Next.js 16 App Router setup
- [x] Drizzle ORM + Neon serverless driver
- [x] Better Auth with drizzleAdapter
- [x] Song catalog browser with full-text + semantic search
- [x] Songset CRUD with transition configuration
- [x] Render job submission and SSE progress tracking
- [x] Playback controller with synchronized lyrics
- [x] Second-screen projection (Presentation API + Google Cast)
- [x] LRC lyrics review and editing
- [x] Shareable public player links
- [x] User settings and preferences
- [x] Offline caching via Service Worker
- [x] Vercel deployment with environment configuration

### ✅ Phase 9: Render Worker (Complete)
- [x] AWS Lambda container (Docker → ECR → Lambda)
- [x] SQS event-driven job processing
- [x] 5-phase render pipeline (preparing, mixing_audio, rendering_frames, encoding_video, uploading)
- [x] PostgreSQL job status tracking
- [x] Orphan job recovery
- [x] REST mode for local development
- [x] CJK font support for lyrics rendering

### ✅ Phase 10: Android App (Complete)
- [x] Kotlin/Jetpack Compose Gradle project with Kover and Robolectric
- [x] Better Auth login/registration/session-restore/sign-out via webapp JSON APIs
- [x] Songset list/detail editing with song search, add/remove/reorder, transition parameters
- [x] Render submission and status polling with artifact availability
- [x] Media3 playback of rendered MP4/MP3 with chapters, lyrics, fullscreen, media controls, and wake-lock
- [x] Share-token creation and Android share/view intents
- [x] Settings editing via `/api/settings`
- [x] Offline artifact downloads via Android DownloadManager with completion tracking
- [x] API base URL configured per build variant via `delivery/android/gradle.properties`

### 📋 Future Enhancements
- [ ] User App deprecation and removal
- [ ] Enhanced semantic search with hybrid RRF ranking
- [ ] Template marketplace for video templates
- [ ] Multi-language support beyond Chinese
- [ ] Real-time collaborative worship set editing

**Current Focus:** Web App feature enhancements and stability

---

## Advanced Configuration

### Admin CLI Configuration

Create `~/.config/stream-of-worship-admin/config.toml`:

```toml
[service]
analysis_url = "http://localhost:8000"

[r2]
bucket = "stream-of-worship"
endpoint_url = "https://<account-id>.r2.cloudflarestorage.com"
region = "auto"

[database]
url = "postgresql://sow_admin_rw@ep-xxx-pooler.us-east-1.aws.neon.tech/sow"
```

**Note:** The old `[turso]` config section is silently ignored for backward compatibility.

**Required Environment Variables** (for sensitive credentials):
```bash
# PostgreSQL password (Admin CLI - never store in config)
export SOW_DATABASE_PASSWORD="your-database-password"

# R2 credentials
export SOW_R2_ACCESS_KEY_ID="your-access-key"
export SOW_R2_SECRET_ACCESS_KEY="your-secret-key"

# Analysis service API key
export SOW_ANALYSIS_API_KEY="your-api-key"
```

**Note:** Non-sensitive settings like `database.url`, `r2.bucket`, and `r2.endpoint_url` should be configured in the config file. Only sensitive credentials use environment variables for security.

### Web App Configuration

See [delivery/webapp/.env.production.example](delivery/webapp/.env.production.example) for full documentation of all environment variables.

**Required:**
```bash
SOW_DATABASE_URL=postgresql://...       # Neon PostgreSQL connection string
SOW_R2_BUCKET=stream-of-worship         # R2 bucket name
SOW_R2_ENDPOINT_URL=https://...         # R2 endpoint
SOW_R2_ACCESS_KEY_ID=...                # R2 access key
SOW_R2_SECRET_ACCESS_KEY=...            # R2 secret key
BETTER_AUTH_SECRET=...                  # Auth session signing secret
BETTER_AUTH_URL=https://...             # Auth base URL
NEXT_PUBLIC_BASE_URL=https://...        # Public app URL
```

### Render Worker Configuration

See [delivery/render-worker/.env.example](delivery/render-worker/.env.example).

**Required:**
```bash
SOW_DATABASE_URL=postgresql://...       # Neon PostgreSQL connection string
SOW_R2_BUCKET=stream-of-worship         # R2 bucket name
SOW_R2_ENDPOINT_URL=https://...         # R2 endpoint
SOW_R2_ACCESS_KEY_ID=...                # R2 access key
SOW_R2_SECRET_ACCESS_KEY=...            # R2 secret key
SOW_SQS_QUEUE_URL=https://...           # SQS queue URL
```

### LLM / Embedding Environment Variables

Chat and embedding clients use separate OpenAI-compatible provider
configuration. `SOW_LLM_*` is chat-only (lyric alignment, transcript
processing, agentic songset construction). `SOW_EMBEDDING_*` is
embedding-only (semantic song search, song/line embedding generation).

| Env Var | Purpose | Default |
|--------|---------|---------|
| `SOW_LLM_API_KEY` | API key for the chat provider | _(none — required for chat features)_ |
| `SOW_LLM_BASE_URL` | Base URL for the chat provider | _(none — required for chat features)_ |
| `SOW_LLM_MODEL` | Chat model id (LRC alignment, YouTube transcript, songset agent) | _(none — required for chat features)_ |
| `SOW_EMBEDDING_API_KEY` | API key for the embedding provider | _(none — required for embedding features)_ |
| `SOW_EMBEDDING_BASE_URL` | Base URL for the embedding provider | _(none — required for embedding features)_ |
| `SOW_EMBEDDING_MODEL` | Embedding model id for API calls (provider-specific) | `text-embedding-3-small` |

> **Note:** The DB `model_version` label stored in `song_embedding` /
> `song_line_embedding` is always hardcoded as `"text-embedding-3-small"`
> (provider-agnostic), while `SOW_EMBEDDING_MODEL` is the
> provider-specific name used for the actual API call (e.g.,
> `openai/text-embedding-3-small` on OpenRouter).

#### Usage by Component

| Component | Env Vars | Purpose | Source File(s) |
|----------|----------|---------|-----------------|
| **Web App** | `SOW_EMBEDDING_*` | Query-time embedding for semantic search (`/api/songs/search/semantic`). No chat feature. | `delivery/webapp/src/lib/embedding.ts` |
| **Analysis Service** | `SOW_LLM_*`, `SOW_EMBEDDING_*` | Chat for LRC/transcript workers; embeddings for song/line embedding jobs and health checks. | `ops/analysis-service/src/sow_analysis/config.py`, `workers/lrc.py`, `workers/youtube_transcript.py`, `workers/embedder.py`, `routes/health.py` |
| **POC Scripts** | `SOW_LLM_*`, `SOW_EMBEDDING_*` | Chat for YouTube LRC/songset construction; embeddings for theme anchor vectors. | `lab/poc-scripts/gen_lrc_youtube.py`, `lab/poc-scripts/poc/songset_constructor/graph/llm.py`, `lab/poc-scripts/poc/songset_constructor/regen_theme_anchors.py` |
| **Admin CLI** | — | Submits embedding jobs to the Analysis Service via HTTP (`audio embed` command). The Admin CLI itself never reads these env vars. | `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` (`_submit_embedding_single`) |
| **Render Worker** | — | No LLM or embedding functionality. Render processing uses FFmpeg + Pillow only. | — |
| **Android App** | — | Consumes webapp JSON APIs only. No direct LLM/embedding access. | — |

#### Provider Considerations

The two provider groups are intentionally independent. A deployment can point
`SOW_LLM_*` at a chat-only provider such as NeuralWatt and
`SOW_EMBEDDING_*` at a provider with an embeddings endpoint such as OpenAI,
OpenRouter, or nano-gpt. There is no fallback from embedding vars to chat vars;
missing embedding credentials should fail clearly instead of silently using the
wrong provider.

### Android App Configuration

The Android app has no server-side secrets; it talks only to the webapp JSON APIs. Configure the API base URL per build variant in `delivery/android/gradle.properties`:

```properties
sow.apiBaseUrl.debug=http://10.0.2.2:8080
sow.apiBaseUrl.staging=https://staging.streamofworship.local
sow.apiBaseUrl.release=https://app.streamofworship.local
```

For local development, start the webapp on `0.0.0.0:8080` and use the Android emulator alias (`10.0.2.2`) or your development machine's LAN IP for a physical device. See [delivery/android/README.md](delivery/android/README.md) for full networking, Better Auth cookie, signed-URL playback, and offline-download troubleshooting notes.

---

## Troubleshooting

### Analysis-Specific Issues

**Problem:** Tempo detection seems wrong

```python
# In lab/poc-scripts/poc_analysis.py, adjust start_bpm parameter:
tempo_librosa, beats_frames = librosa.beat.beat_track(
    y=y, sr=sr,
    start_bpm=90,  # Try 70 for slow, 120 for fast
    units='frames'
)
```

**Problem:** Too many/few section boundaries

```python
# Adjust peak picking parameters:
peaks = librosa.util.peak_pick(
    onset_env,
    pre_max=5,     # Increase for fewer boundaries
    post_max=5,
    delta=0.5,     # Increase for fewer boundaries
    wait=15
)
```

### Service Issues

**Problem:** Analysis Service fails to start
- Check R2 credentials in `.env`
- Verify port 8000 is not in use
- Check Docker logs: `docker compose logs -f`

**Problem:** Forced aligner model not loading
- Verify model is downloaded: `huggingface-cli download Qwen/Qwen3-ForcedAligner-0.6B`
- Check `SOW_FORCED_ALIGNER_MODEL_PATH` environment variable
- Check memory allocation (8GB minimum)

### Database Issues

**Problem:** Admin CLI can't connect to PostgreSQL
- Verify `SOW_DATABASE_PASSWORD` environment variable is set
- Check that the Neon connection URL is correct and not expired
- Ensure Neon project is active and has available connections

**Problem:** Web App can't connect to database
- Verify `SOW_DATABASE_URL` is set correctly in `.env.local`
- Check that the `vector` extension is enabled: `psql "$SOW_DATABASE_URL" -c 'CREATE EXTENSION IF NOT EXISTS vector;'`
- Run `npx drizzle-kit push` to ensure schema is up to date

---

## Resources

- **Design Document:** [specs/worship-music-transition-system-design.md](specs/worship-music-transition-system-design.md)
- **Analysis Service:** [ops/analysis-service/README.md](ops/analysis-service/README.md)
- **Render Worker:** [delivery/render-worker/README.md](delivery/render-worker/README.md)
- **Web App:** [delivery/webapp/README.md](delivery/webapp/README.md)
- **Android App:** [delivery/android/README.md](delivery/android/README.md)
- **Admin CLI:** [ops/admin-cli/src/stream_of_worship/admin/README.md](ops/admin-cli/src/stream_of_worship/admin/README.md)
- **librosa Documentation:** https://librosa.org/doc/latest/

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests: `uv run --project lab/sow-app --extra test pytest lab/sow-app/tests -v`
5. Submit a pull request

---

**Last Updated:** 2026-07-04
