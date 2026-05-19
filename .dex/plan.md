# Stream of Worship Webapp — Deployment Plan v2 Implementation

## Overview

Migrate the render pipeline from in-process Vercel execution to an AWS Lambda-based worker architecture. The Next.js app will enqueue render jobs to SQS instead of running `executeRenderPipeline()` via `after()`. A Python Lambda container will process jobs, achieving feature parity with the current Node.js pipeline while eliminating the need for heavy native dependencies (canvas, ffmpeg-static, fastembed) on Vercel.

## Context

- Files involved:
  - `webapp/src/app/api/render-jobs/route.ts` — current in-process pipeline trigger
  - `webapp/src/lib/render/pipeline.ts` — 219-line render orchestrator to port
  - `webapp/src/lib/render/audio-engine.ts` — 560-line FFmpeg audio mixing to port
  - `webapp/src/lib/render/video-engine.ts` — 611-line FFmpeg video encoding to port
  - `webapp/src/lib/render/frame-renderer.ts` — 570-line node-canvas frame rendering to port
  - `webapp/src/lib/render/chapters.ts` — 256-line chapter manifest to port
  - `webapp/src/lib/render/lrc-parser.ts` — 199-line LRC parser to port
  - `webapp/src/lib/render/uploader.ts` — 280-line R2 upload to port
  - `webapp/src/lib/render/asset-fetcher.ts` — 200-line R2 download to port
  - `webapp/src/lib/render/job-manager.ts` — 385-line DB job CRUD (shared, not ported)
  - `webapp/src/lib/render/render-ratio.ts` — 58-line progress estimation
  - `webapp/src/lib/embed/client.ts` — fastembed runtime (to be deleted)
  - `webapp/src/app/api/songs/search/semantic/route.ts` — currently uses fastembed at runtime; will switch to pre-computed embedding lookup
  - `webapp/src/app/api/songs/search/route.ts` — currently uses ilike; will switch to tsvector full-text search
  - `webapp/src/lib/db/search.ts` — new hybrid search utility module
  - `webapp/src/db/schema.ts` — songs table needs tsvector generated column + GIN index
  - `webapp/next.config.ts` — serverExternalPackages to clean up
  - `webapp/vercel.json` — maxDuration to reduce
  - `webapp/package.json` — heavy deps to remove, SQS SDK to add
  - `webapp/src/test/deployment/deployment.test.ts` — deployment config tests to update
  - `webapp/.env.production.example` — new env vars to document
  - `webapp/src/db/schema.ts` — render_jobs table (read by both Vercel and Lambda)
- Related patterns:
  - `services/analysis/Dockerfile` — existing Python Dockerfile pattern
  - `services/analysis/docker-compose.yml` — existing service compose pattern
  - `services/analysis/scripts/deploy.sh` — existing deployment script pattern
  - `webapp/src/lib/r2/client.ts` — R2 S3-compatible client (boto3 equivalent needed)
  - `webapp/src/db/index.ts` — neon-http driver (psycopg2 equivalent needed)
- Dependencies:
  - Python 3.11, Pillow, boto3, psycopg2-binary, FFmpeg (system), CJK fonts
  - `@aws-sdk/client-sqs` for Next.js SQS integration
  - AWS Lambda container image runtime (`public.ecr.aws/lambda/python:3.11`)

## Development Approach

- **Testing approach**: Regular (code first, then tests)
- Complete each task fully before moving to the next
- The Python worker must achieve feature parity with the Node.js pipeline — port logic faithfully, don't redesign
- Lambda handler is thin; all render logic lives in testable Python modules
- Use boto3 for R2 (S3-compatible) and psycopg2 for Neon DB access in the worker
- The Next.js app remains the source of truth for job creation and status queries; Lambda only reads/writes job status
- **CRITICAL: every task MUST include new/updated tests**
- **CRITICAL: all tests must pass before starting next task**

## Implementation Steps

### Task 1: Create Python Render Worker Package Structure

**Files:**
- Create: `services/render-worker/pyproject.toml`
- Create: `services/render-worker/requirements.txt`
- Create: `services/render-worker/src/sow_render_worker/__init__.py`
- Create: `services/render-worker/src/sow_render_worker/lambda_handler.py`
- Create: `services/render-worker/src/sow_render_worker/config.py`
- Create: `services/render-worker/tests/__init__.py`
- Create: `services/render-worker/tests/conftest.py`

- [ ] Create `services/render-worker/` directory with Python package structure
- [ ] Create `pyproject.toml` with project metadata, Python 3.11 target, ruff config (line-length 100)
- [ ] Create `requirements.txt` with: boto3, psycopg2-binary, Pillow, python-dotenv
- [ ] Create `config.py` module that reads env vars (DATABASE_URL, R2_*, AWS_REGION, SQS_QUEUE_URL) with validation
- [ ] Create stub `lambda_handler.py` with `handler(event, context)` that logs the event and returns 200
- [ ] Create `conftest.py` with test fixtures (env vars mock, temp directory)
- [ ] Write tests for `config.py` — verify env var reading, validation errors on missing vars
- [ ] Run `PYTHONPATH=src pytest tests/ -v` from `services/render-worker/` — must pass

### Task 2: Port LRC Parser to Python

**Files:**
- Create: `services/render-worker/src/sow_render_worker/lrc_parser.py`
- Create: `services/render-worker/tests/test_lrc_parser.py`

- [ ] Port `parseLRC()` from `webapp/src/lib/render/lrc-parser.ts` — parse `[mm:ss.xx]text` format, sort by timestamp
- [ ] Port `convertToGlobalTimeline()` — shift local timestamps by segment offset, attach title
- [ ] Port `estimateLastLyricDuration()` — two-tier: match previous same-text line, then char-count + BPM fallback
- [ ] Port `groupLyricsBySong()` — group GlobalLRCLine by title
- [ ] Port `findCurrentLyricIndex()` — binary-style search for current lyric at time
- [ ] Port `isValidLRC()` — regex check for valid format
- [ ] Write tests matching the TypeScript test patterns — parse various LRC formats, global timeline conversion, duration estimation, grouping, edge cases (empty, single line, no timestamps)
- [ ] Run tests — must pass

### Task 3: Port Chapters Module to Python

**Files:**
- Create: `services/render-worker/src/sow_render_worker/chapters.py`
- Create: `services/render-worker/tests/test_chapters.py`

- [ ] Port `ChaptersManifest` dataclass — chapters list, total_duration_seconds, generated_at
- [ ] Port `Chapter` dataclass — position, song_title, start_seconds, end_seconds, lines
- [ ] Port `ChapterLine` dataclass — text, start_seconds
- [ ] Port `build_chapters_from_segments()` — iterate segments, fetch lyrics via callback, build chapter list
- [ ] Port `generate_chapters_manifest()` — build chapters with LRC download callback
- [ ] Port `chapters_to_ffmpeg_metadata()` — FFMETADATA1 format output
- [ ] Port `find_chapter_at_time()`, `get_song_title_at_time()`, `get_lyric_at_time()` — lookup helpers
- [ ] Port `parse_chapters_manifest()` — JSON parse with validation
- [ ] Write tests — manifest generation, FFmpeg metadata format, time lookups, validation errors
- [ ] Run tests — must pass

### Task 4: Port Audio Engine to Python

**Files:**
- Create: `services/render-worker/src/sow_render_worker/audio_engine.py`
- Create: `services/render-worker/tests/test_audio_engine.py`

- [ ] Port `SongsetItem` dataclass — all fields from TypeScript interface
- [ ] Port `AudioSegmentInfo` dataclass — item, audio_path, start_time_seconds, duration_seconds, gap_before_seconds
- [ ] Port `calculate_gap_ms()` — beat-based gap calculation with crossfade override
- [ ] Port `get_crossfade_ms()` — crossfade duration from item config
- [ ] Port `get_audio_info()` — use subprocess `ffprobe` to get duration, sample rate, channels
- [ ] Port `generate_songset_audio()` — download audio files, calculate gaps/crossfades, build FFmpeg filter complex, run ffmpeg subprocess for concatenation with loudnorm
- [ ] Port `concatenate_audio_files()` — build FFmpeg complex filter with adelay, afade, amix, loudnorm; spawn ffmpeg process
- [ ] Port `calculate_total_duration()` — sum durations with gaps
- [ ] Use `subprocess.run` for FFmpeg commands (not fluent-ffmpeg — that's Node.js only)
- [ ] Write tests — gap calculation, crossfade calculation, FFmpeg filter complex construction (mock subprocess), audio info parsing
- [ ] Run tests — must pass

### Task 5: Port Frame Renderer to Python

**Files:**
- Create: `services/render-worker/src/sow_render_worker/frame_renderer.py`
- Create: `services/render-worker/tests/test_frame_renderer.py`

- [ ] Port `VideoTemplate` dataclass — name, background_color, text_color, highlight_color, font_size, resolution
- [ ] Port `FONT_SIZE_PRESETS` dict — S:32, M:48, L:64, XL:80
- [ ] Port `VIDEO_TEMPLATES` dict — dark, gradient_warm, gradient_blue with exact RGB values
- [ ] Port `FrameRenderer` class using Pillow (ImageDraw, ImageFont) instead of node-canvas
- [ ] Port `render_frame()` — create Pillow image, fill background, find current segment, render intro info, render lyrics with highlighting
- [ ] Port `render_intro_info()` — Traditional Chinese labels (歌曲, 專輯, 作曲, 作詞, 讚美之泉音樂事工), fade-out with sqrt-based alpha
- [ ] Port `render_lyrics()` — current line 2x font size centered at 33% height, next line 50% transparent below, last-lyric fade-out
- [ ] Port `render_title_card()` — songset name + song count + duration
- [ ] Port `fit_text()` — auto-scale font size to fit width using Pillow text measurement
- [ ] Port `get_margin()` — single-character margin using "中" as reference
- [ ] Use `ImageFont.truetype()` with system sans-serif font; fall back to `ImageFont.load_default()` if unavailable
- [ ] Write tests — template definitions, font size presets, frame rendering at known times (mock font), title card rendering, text fitting
- [ ] Run tests — must pass

### Task 6: Port Video Engine to Python

**Files:**
- Create: `services/render-worker/src/sow_render_worker/video_engine.py`
- Create: `services/render-worker/tests/test_video_engine.py`

- [ ] Port `VideoEngine` class — template, font_size_preset, resolution, fps, title_card config, ffmpeg_path
- [ ] Port `generate_video()` — get audio duration, collect lyrics with global timing, build chapters, encode with FFmpeg
- [ ] Port `encode_video_with_ffmpeg()` — spawn ffmpeg process reading raw RGBA frames from stdin, pipe frames from FrameRenderer, handle EPIPE/backpressure
- [ ] Port `generate_blank_video()` — FFmpeg color source for no-lyrics fallback
- [ ] Port `inject_chapters()` — FFmpeg metadata injection for chapter atoms
- [ ] Port `get_video_codec_args()` — libx264 ultrafast, CRF 23, configurable bitrate
- [ ] Port `get_audio_info()` — ffprobe subprocess for duration/sample rate/channels
- [ ] Key difference from Node.js: use `subprocess.Popen` with stdin pipe for frame streaming; write RGBA bytes from Pillow `tobytes()` instead of node-canvas `getImageData()`
- [ ] Write tests — video codec args, blank video generation args, chapter injection, FFmpeg command construction (mock subprocess)
- [ ] Run tests — must pass

### Task 7: Port R2 Client and Asset Fetcher to Python

**Files:**
- Create: `services/render-worker/src/sow_render_worker/r2_client.py`
- Create: `services/render-worker/src/sow_render_worker/asset_fetcher.py`
- Create: `services/render-worker/tests/test_r2_client.py`
- Create: `services/render-worker/tests/test_asset_fetcher.py`

- [ ] Port R2 client using boto3 S3 client with Cloudflare R2 endpoint (`https://{account_id}.r2.cloudflarestorage.com`)
- [ ] Port `generate_signed_url()` — generate_presigned_url for GetObject
- [ ] Port `get_audio_signed_url()`, `get_lrc_signed_url()` — key construction helpers
- [ ] Port `file_exists()` — head_object with NotFound handling
- [ ] Port `get_object_size()` — head_object ContentLength
- [ ] Port `AssetFetcher` class — download audio/LRC from R2, local filesystem cache at `/tmp/sow-assets/cache/`
- [ ] Port `download_audio()` — check cache, download via signed URL, write to cache
- [ ] Port `download_lrc()` — download LRC content via signed URL
- [ ] Port `cleanup_temp()` — clean temp directory
- [ ] Write tests — R2 client initialization, signed URL generation, asset fetcher caching logic (mock boto3)
- [ ] Run tests — must pass

### Task 8: Port R2 Uploader to Python

**Files:**
- Create: `services/render-worker/src/sow_render_worker/uploader.py`
- Create: `services/render-worker/tests/test_uploader.py`

- [ ] Port `R2Uploader` class using boto3 S3 client
- [ ] Port `upload_file()` — read file, put_object with content type, cache control, metadata
- [ ] Port `upload_buffer()` — put_object with bytes body
- [ ] Port `upload_render_artifacts()` — upload MP3, MP4, chapters.json to `renders/{jobId}/` prefix
- [ ] Port `file_exists()` — head_object
- [ ] Port `delete_file()` — delete_object
- [ ] Port `delete_render_artifacts()` — delete all artifacts for a job
- [ ] Port content type inference from file extension
- [ ] Write tests — upload artifacts logic, content type mapping, key construction (mock boto3)
- [ ] Run tests — must pass

### Task 9: Port DB Job Manager to Python

**Files:**
- Create: `services/render-worker/src/sow_render_worker/db.py`
- Create: `services/render-worker/tests/test_db.py`

- [ ] Create DB module using psycopg2 with connection from `DATABASE_URL`
- [ ] Port `get_render_job()` — SELECT from render_jobs by id and user_id
- [ ] Port `start_render_job()` — UPDATE status to 'running'
- [ ] Port `update_render_progress()` — UPDATE phase, phase_index, estimated_total_seconds, total_duration_seconds, elapsed_seconds
- [ ] Port `complete_render_job()` — UPDATE status to 'completed', set mp3/mp4/chapters R2 keys, completed_at
- [ ] Port `fail_render_job()` — UPDATE status to 'failed', set error_message
- [ ] Port `recover_orphaned_jobs()` — UPDATE running jobs older than 30 min to failed
- [ ] Port phase index constants and ordering
- [ ] Use parameterized queries (no string interpolation) for SQL injection safety
- [ ] Write tests — job status transitions, progress updates, orphan recovery (mock psycopg2 or use test fixtures)
- [ ] Run tests — must pass

### Task 10: Port Render Pipeline Orchestrator to Python

**Files:**
- Create: `services/render-worker/src/sow_render_worker/pipeline.py`
- Create: `services/render-worker/tests/test_pipeline.py`

- [ ] Port `execute_render_pipeline()` — the main orchestrator function
- [ ] Port phase sequence: preparing -> mixing_audio -> rendering_frames -> encoding_video -> uploading
- [ ] Port `fetch_songset_items()` — query songset_items joined with recordings and songs
- [ ] Port cancellation check — read job status from DB between phases
- [ ] Port progress estimation using `get_render_ratio()` — query historical jobs for ratio
- [ ] Port error handling — mark job as failed on exception, skip if cancelled
- [ ] Port temp directory cleanup in finally block
- [ ] Wire up all modules: AudioEngine, VideoEngine, AssetFetcher, R2Uploader, DB
- [ ] Write integration-style tests — pipeline flow with mocked sub-components, cancellation mid-pipeline, error propagation
- [ ] Run tests — must pass

### Task 11: Implement Lambda Handler

**Files:**
- Modify: `services/render-worker/src/sow_render_worker/lambda_handler.py`
- Create: `services/render-worker/tests/test_lambda_handler.py`

- [ ] Implement `handler(event, context)` — iterate SQS records, parse JSON body, extract jobId/songsetId/userId
- [ ] Call `execute_render_pipeline(job_id, user_id)` for each record
- [ ] On success: return 200 (SQS auto-deletes message)
- [ ] On failure: log error, raise exception (SQS retries after visibility timeout, then DLQ after 3 failures)
- [ ] Add structured logging with job_id context
- [ ] Handle batch size 1 (one render per invocation) — but code should handle multiple records gracefully
- [ ] Write tests — SQS event parsing, success path, failure path, multiple records
- [ ] Run tests — must pass

### Task 12: Create Dockerfile and Docker Compose for Local Testing

**Files:**
- Create: `services/render-worker/Dockerfile`
- Create: `services/render-worker/docker-compose.yml`
- Create: `services/render-worker/.env.example`

- [ ] Create Dockerfile based on `public.ecr.aws/lambda/python:3.11`
- [ ] Install system packages: ffmpeg, fonts-noto-cjk (or google-noto-sans-cjk-fonts)
- [ ] Copy `src/sow_render_worker/` to `/app/sow_render_worker/`
- [ ] Install Python dependencies from requirements.txt
- [ ] Set CMD to `sow_render_worker.lambda_handler.handler`
- [ ] Create docker-compose.yml for local testing — build the image, expose Lambda runtime API port
- [ ] Create `.env.example` with all required env vars documented
- [ ] Test: `docker build` succeeds, `docker run` starts Lambda runtime
- [ ] Write a test that verifies the Docker image builds and the handler is importable (smoke test)
- [ ] Run tests — must pass

### Task 13: Add SQS Integration to Next.js

**Files:**
- Modify: `webapp/package.json` — add `@aws-sdk/client-sqs`
- Create: `webapp/src/lib/sqs/client.ts`
- Modify: `webapp/src/app/api/render-jobs/route.ts` — replace `after()` with SQS enqueue
- Create: `webapp/src/test/lib/sqs/client.test.ts`

- [ ] Add `@aws-sdk/client-sqs` dependency via `pnpm add @aws-sdk/client-sqs` in webapp/
- [ ] Create `webapp/src/lib/sqs/client.ts` — SQSClient wrapper with `sendMessage()` that sends `{ jobId, songsetId, userId }` as JSON body
- [ ] Read `AWS_REGION`, `SQS_QUEUE_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` from env
- [ ] Modify `POST /api/render-jobs/route.ts` — replace `after(() => executeRenderPipeline(...))` with `sqsClient.sendMessage({ jobId, songsetId, userId })`
- [ ] Keep `createRenderJob()` call — job is still created in DB with "queued" status
- [ ] The Lambda worker will transition it to "running" when it picks up the message
- [ ] Write tests for SQS client — message construction, env var validation, error handling (mock SQS)
- [ ] Update existing API route tests if they exist
- [ ] Run `pnpm test` from webapp/ — must pass

### Task 14: Remove Heavy Dependencies from Next.js

**Files:**
- Modify: `webapp/package.json` — remove canvas, ffmpeg-static, fluent-ffmpeg, fastembed
- Modify: `webapp/next.config.ts` — remove fastembed, @anush008/tokenizers, ffmpeg-static from serverExternalPackages
- Modify: `webapp/vercel.json` — reduce maxDuration on render routes from 800 to 60
- Modify: `webapp/pnpm-workspace.yaml` — remove canvas, ffmpeg-static, onnxruntime-node from onlyBuiltDependencies
- Modify: `webapp/src/test/deployment/deployment.test.ts` — update assertions for new config

- [ ] Remove `canvas`, `ffmpeg-static`, `fluent-ffmpeg`, `fastembed` from package.json dependencies
- [ ] Remove `@anush008/tokenizers` from package.json if it was only a fastembed transitive dep
- [ ] Remove `fastembed`, `@anush008/tokenizers`, `ffmpeg-static` from `serverExternalPackages` in next.config.ts
- [ ] Reduce `maxDuration` from 800 to 60 for all render-jobs routes in vercel.json (they now just enqueue SQS)
- [ ] Remove `fluid: true` from render-jobs routes in vercel.json (no longer needed for long-running functions)
- [ ] Update `pnpm-workspace.yaml` to remove native dep build entries for canvas, ffmpeg-static, onnxruntime-node
- [ ] Update deployment tests — maxDuration should be 60, not 800; fluid compute no longer required on render routes
- [ ] Run `pnpm test` and `pnpm lint` from webapp/ — must pass
- [ ] Run `pnpm build` from webapp/ — must succeed (verifies no broken imports)

### Task 15: Implement Hybrid Search (Full-Text + Pre-computed Tag Embeddings)

Replace the current runtime fastembed semantic search with a two-tier hybrid approach:
- Tier 1 (user queries): Postgres full-text search using tsvector across Chinese text (title, composer, lyricist, album) and pinyin (title_pinyin). Covers the vast majority of user-typed searches.
- Tier 2 (similar songs discovery): Pre-computed tag/category embeddings via pgvector for "find similar" use cases. No runtime embedding generation needed.

**Files:**
- Modify: `webapp/src/db/schema.ts` — add tsvector generated column and GIN index to songs table
- Modify: `webapp/src/lib/db/songs.ts` — add `fullTextSearchSongs()`, update `semanticSearchSongs()` to accept pre-computed embedding only
- Modify: `webapp/src/app/api/songs/search/route.ts` — switch from ilike to tsvector full-text search
- Modify: `webapp/src/app/api/songs/search/semantic/route.ts` — accept pre-computed embedding only, remove runtime fastembed
- Modify: `webapp/src/lib/embed/client.ts` — remove (no longer needed at runtime)
- Create: `webapp/src/lib/db/search.ts` — hybrid search utility module
- Create: `webapp/src/test/lib/db/search.test.ts` — hybrid search tests
- Modify: `webapp/src/test/api/songs/search.test.ts` — update for tsvector-based search

- [ ] Add tsvector generated column to songs table in schema.ts: `searchVector` computed from `title`, `title_pinyin`, `composer`, `lyricist`, `album_name` using `setweight(to_tsvector('simple', ...), 'A')` for title/pinyin and `'B'` for others; add GIN index on the generated column
- [ ] Create `webapp/src/lib/db/search.ts` with `fullTextSearchSongs(query, limit, offset, visibilityStatus)` using `plainto_tsquery('simple', query)` against the tsvector column; fall back to `websearch_to_tsquery` for phrase matching; combine with `ts_rank_cd` for relevance ordering
- [ ] Use `'simple'` text search config (not 'zhparser' or 'pg_jieba') — it tokenizes on whitespace/punctuation which works for Chinese characters (each character is a token) and pinyin (space-separated words); no external Postgres extension needed
- [ ] Modify `GET /api/songs/search` route to call `fullTextSearchSongs()` instead of `searchSongs()` with ilike; keep same response shape
- [ ] Modify `POST /api/songs/search/semantic` to require a `recordingId` parameter instead of `query`; look up the pre-computed embedding from `song_embedding` table for that recording, then call `semanticSearchSongs()` with the retrieved embedding
- [ ] Remove `generateEmbedding()` call and `runtime = "nodejs"` export from semantic route
- [ ] Delete `webapp/src/lib/embed/client.ts` entirely (no longer needed at runtime)
- [ ] Write a Drizzle migration that adds the tsvector generated column and GIN index to the existing songs table
- [ ] Write tests for `fullTextSearchSongs()` — Chinese character search, pinyin search, mixed queries, relevance ranking, empty results
- [ ] Write tests for updated semantic search route — verify it requires recordingId, verify it looks up embedding from DB, verify 400 when recording has no embedding
- [ ] Update existing `search.test.ts` to reflect tsvector-based search behavior
- [ ] Run `pnpm test` from webapp/ — must pass

### Task 16: Create GitHub Actions CI/CD Workflows

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/deploy.yml`

- [ ] Create `.github/workflows/ci.yml` — trigger on PR, run `pnpm lint` and `pnpm test` in webapp/, run Python tests in services/render-worker/
- [ ] Create `.github/workflows/deploy.yml` — trigger on push to main with paths filter for `webapp/**` and `services/render-worker/**`
- [ ] Deploy workflow: (1) Vercel deployment via vercel-action, (2) Docker build + ECR push + Lambda update for render-worker changes
- [ ] Add DB migration step: `npx drizzle-kit migrate` against Neon with `DATABASE_URL` from secrets
- [ ] Use GitHub repository secrets for: VERCEL_TOKEN, VERCEL_ORG_ID, VERCEL_PROJECT_ID, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, DATABASE_URL
- [ ] Write tests verifying workflow YAML is valid and has expected job structure (optional but recommended)
- [ ] Run `pnpm test` from webapp/ — must pass

### Task 17: Update Environment Configuration and Documentation

**Files:**
- Modify: `webapp/.env.example` — add SQS/AWS env vars
- Modify: `webapp/.env.production.example` — add SQS/AWS env vars with documentation
- Modify: `webapp/README.md` — update deployment docs for Lambda architecture
- Modify: `webapp/src/test/deployment/deployment.test.ts` — add assertions for new env vars

- [ ] Add to `.env.example`: `AWS_REGION`, `SQS_QUEUE_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
- [ ] Add to `.env.production.example`: same vars with documentation explaining IAM permissions needed
- [ ] Update README.md: replace Vercel Pro + Fluid Compute + maxDuration:800 docs with Lambda worker architecture
- [ ] Document the Lambda worker deployment flow (ECR push -> Lambda update)
- [ ] Document the SQS queue setup (queue name, DLQ, visibility timeout)
- [ ] Add deployment test assertions for new env vars in `.env.production.example`
- [ ] Update deployment test assertions for vercel.json changes (maxDuration:60, no fluid on render routes)
- [ ] Run `pnpm test` from webapp/ — must pass

### Task 18: Verify Acceptance Criteria

- [ ] Run full Python test suite: `cd services/render-worker && PYTHONPATH=src pytest tests/ -v`
- [ ] Run full webapp test suite: `cd webapp && pnpm test`
- [ ] Run webapp linter: `cd webapp && pnpm lint`
- [ ] Run webapp build: `cd webapp && pnpm build` — must succeed with no broken imports
- [ ] Verify Docker image builds: `cd services/render-worker && docker build -t sow-render-worker .`
- [ ] Verify no references to removed packages remain: grep for `canvas`, `ffmpeg-static`, `fluent-ffmpeg`, `fastembed` in webapp/src/ (should only be in deprecated embed/client.ts comments)
- [ ] Verify SQS client is used in render-jobs route instead of `after(() => executeRenderPipeline(...))`

### Task 19: Update Documentation

- [ ] Update `reports/current_impl_status.md` with Lambda worker architecture
- [ ] Update `webapp/README.md` with deployment instructions for Lambda worker
- [ ] Add `services/render-worker/README.md` with worker setup, local testing, and deployment instructions
