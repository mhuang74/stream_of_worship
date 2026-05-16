Webapp Implementation Fix Plan
===============================

Derived from webapp_impl_fixer.md review findings after verification.
13 of 31 original findings are FALSE POSITIVE (already fixed or never existed).
18 findings are CONFIRMED and organized below into 5 implementation phases.

False Positives (no action needed)
----------------------------------
- SSE catch block missing cleanup: clearInterval + controller.close() already present (events/route.ts:128-129)
- aevalsrc missing -f lavfi: .inputOptions("-f lavfi") already applied (audio-engine.ts:296)
- color=c raw RGB integers: already converted to hex with toString(16).padStart(2,"0") (video-engine.ts:350)
- GET handler no fileType validation: allowlist check already present (signed-url/route.ts:164-171)
- limit/offset no NaN check: isNaN checks with defaults already present (songsets/route.ts:23-25)
- itemId not in Zod schema: itemId: z.string().min(1) already in schema (items/route.ts:22)
- middleware.ts does not exist: file exists with 33 lines of auth guard logic
- Next.js 15 async params: params typed as Promise, awaited correctly
- R2 keys passed as URLs: keys passed to /api/signed-url to generate proper signed URLs
- session.user.id string vs number: Number() conversion already applied
- SSE catch block (dup of finding 1)
- aevalsrc missing lavfi (dup of finding 2)
- color=c raw RGB (dup of finding 3)


Phase 1: Critical Bugs
======================

1.1 Fix startRenderJob broken inline mapping
--------------------------------------------
File: webapp/src/lib/render/job-manager.ts:319-340
Problem: startRenderJob has an incomplete inline object literal (lines 319-339) that is never
properly closed, followed by `return mapRowToRenderJob(updated)` on line 340. The inline
mapping is dead code and the object literal syntax is broken (missing closing brace and
semicolons before the return statement). This likely causes a TypeScript compilation error
or runtime crash.
Fix: Delete lines 319-339 (the inline object literal) and keep only line 340:
  `return mapRowToRenderJob(updated);`
This matches the pattern used by all other functions in the file.
Verification: Run `pnpm build` in webapp/ to confirm no TypeScript errors.

1.2 Wire render pipeline to POST /api/render-jobs
---------------------------------------------------
File: webapp/src/app/api/render-jobs/route.ts:37
Problem: POST handler creates a job with status "queued" but never triggers actual render
execution. AudioEngine, VideoEngine, and R2Uploader are never called from any route or
background task. Jobs stay "queued" forever.
Fix: After creating the job, trigger the render pipeline asynchronously. Create a new
module webapp/src/lib/render/pipeline.ts with an exported function:
  executeRenderPipeline(jobId: string, userId: number): Promise<void>
This function should:
  1. Call startRenderJob(jobId, userId) to set status to "running"
  2. Fetch songset items from DB
  3. Call AudioEngine.generateSongsetAudio() to produce the audio file
  4. Call VideoEngine.generateVideo() to produce the video file
  5. Call R2Uploader.uploadRenderArtifacts() to upload to R2
  6. Call completeRenderJob() with R2 keys on success
  7. Call failRenderJob() on error
  8. Update progress via updateRenderProgress() at each phase
In the POST handler, after createRenderJob, call executeRenderPipeline without await
(fire-and-forget for Vercel Fluid Compute, or use waitUntil if available):
  executeRenderPipeline(job.id, Number(session.user.id)).catch(console.error)
Verification: Create a test that mocks AudioEngine/VideoEngine/R2Uploader and verifies
the pipeline function calls them in order and transitions job status correctly.

1.3 Fix completedAt schema/migration mismatch
-----------------------------------------------
Files: webapp/src/db/schema.ts:215, webapp/drizzle/0001_chunky_pete_wisdom.sql:1
Problem: Schema declares completedAt as nullable (no .notNull()), but migration 0001
adds SET NOT NULL to the column. A newly created "queued" job has no completedAt value,
which would violate the NOT NULL constraint at the DB level. The schema and migration
are out of sync.
Fix: Two options:
  Option A (recommended): Remove the NOT NULL constraint from the DB. Create a new
  migration (0002) that runs:
    ALTER TABLE "render_jobs" ALTER COLUMN "completed_at" DROP NOT NULL;
  This matches the Drizzle schema (nullable) and the business logic (completedAt is
  null until the job completes).
  Option B: Add .notNull() to the schema and set a default value. This is wrong because
  completedAt should be null for queued/running jobs.
Also update computeRenderState (songsets.ts:74-84) to handle the null case explicitly.
The current code already checks `if (job.completedAt)` before comparing, so it handles
null correctly. But add a comment clarifying that completedAt can be null for
non-completed jobs.
Verification: Run `pnpm build` and existing tests. Create a new migration and verify
it applies cleanly with `npx drizzle-kit push`.


Phase 2: Major Bugs
====================

2.1 Fix estimateLastLyricDuration for non-CJK non-ASCII chars
--------------------------------------------------------------
File: webapp/src/lib/render/lrc-parser.ts:112-120
Problem: Characters with code > 0x7f (accented Latin, Cyrillic, etc.) get CJK weight 1.0
instead of 0.5. For a Chinese worship music app this is a minor concern, but the logic
is imprecise.
Fix: Refine the condition to specifically detect CJK ranges:
  if (code >= 0x4e00 && code <= 0x9fff ||  // CJK Unified Ideographs
      code >= 0x3400 && code <= 0x4dbf ||  // CJK Extension A
      code >= 0x3000 && code <= 0x303f) {  // CJK Symbols
    charCount += 1.0;
  } else {
    charCount += 0.5;
  }
Alternatively, keep the simpler approach but add a comment explaining the tradeoff.
Verification: Add a unit test with mixed Chinese/Latin text and verify character counts.

2.2 Fix songTitle using songId instead of actual title
-------------------------------------------------------
Files: webapp/src/lib/render/video-engine.ts:168,189
        webapp/src/lib/render/chapters.ts:61
        webapp/src/lib/render/audio-engine.ts:13-26 (SongsetItem type)
Problem: songTitle is set to segment.item.songId (a nanoid like "V1StGXR8_Z5jdHi6B-myT")
instead of the human-readable song title. SongsetItem only carries songId, not title.
Fix:
  1. Add `songTitle: string` field to the SongsetItem interface in audio-engine.ts
  2. Update the DB query that builds SongsetItem objects (in songsets.ts or wherever
     items are fetched) to join with the songs table and populate songTitle from
     songs.title
  3. Update video-engine.ts:168,189 to use `segment.item.songTitle` instead of
     `segment.item.songId?.toString()`
  4. Update chapters.ts:61 to use `segment.item.songTitle` instead of `segment.item.songId`
Verification: Run existing tests. Add a test verifying songTitle is populated from
the songs table join.

2.3 Fix N+1 query in listSongsets
----------------------------------
File: webapp/src/lib/db/songsets.ts:109-121
Problem: listSongsets calls computeRenderState(row.id) for every songset in the result.
computeRenderState issues 2-3 DB queries per songset. For 50 songsets this is 100-150
queries.
Fix: Refactor listSongsets to join render_jobs data in the initial findMany query:
  1. Add a `with: { latestRenderJob: true }` relation to the songsets Drizzle schema
     (or use a manual join)
  2. In listSongsets, fetch the latest render job as part of the findMany query
  3. Compute render state in-process from the joined data instead of calling
     computeRenderState per row
  4. Keep computeRenderState as a standalone function for single-songset lookups
     (getSongset), but avoid using it in listSongsets
Verification: Add a test verifying listSongsets makes only 1-2 DB queries regardless
of result count.


Phase 3: Code Simplification
=============================

3.1 Extract shared signed-url handler
--------------------------------------
File: webapp/src/app/api/signed-url/route.ts
Problem: GET and POST handlers contain ~130 lines of near-identical logic (auth check,
identifier-type branching, R2 client creation, signed URL generation, error handling).
Fix: Extract a shared function:
  async function generateSignedUrlResponse(params: {
    key?: string; hashPrefix?: string; renderJobId?: string;
    fileType?: string; userId: number;
  }): Promise<NextResponse>
Both handlers parse their input format (query params vs JSON body) and call this shared
function. This eliminates ~120 lines of duplication.
Verification: Run existing signed-url tests. Both GET and POST paths should still pass.

3.2 Deduplicate uploadFile/uploadBuffer
----------------------------------------
File: webapp/src/lib/render/uploader.ts:104-180
Problem: uploadFile and uploadBuffer share ~15 lines of identical PutObjectCommand
construction, contentType/cacheControl defaults, and result formatting.
Fix: Extract a private method:
  private async putObject(
    key: string, body: Buffer, sizeBytes: number, options: UploadOptions
  ): Promise<UploadResult>
uploadFile reads the file and calls putObject; uploadBuffer calls putObject directly.
Verification: Run existing uploader tests.

3.3 Remove createR2UploaderFromEnv
-----------------------------------
File: webapp/src/lib/render/uploader.ts:402-404
Problem: One-liner factory that just returns new R2Uploader(). The constructor already
defaults to env vars.
Fix: Delete the function. Update the one call site (uploader.ts:421 inside
uploadRenderArtifacts) to use new R2Uploader() directly.
Verification: Run existing tests.

3.4 Remove standalone uploadRenderArtifacts
-------------------------------------------
File: webapp/src/lib/render/uploader.ts:416-423
Problem: Standalone function that creates an R2Uploader and delegates to its instance
method. Two public APIs for the same operation.
Fix: Delete the standalone function. Update any external call sites to use
new R2Uploader().uploadRenderArtifacts(...) directly. If the standalone function is
used in tests or other modules, update those call sites.
Verification: Run existing tests. Grep for import of uploadRenderArtifacts to find
all call sites.

3.5 Fix GlobalLRCLine timeSeconds confusion
--------------------------------------------
File: webapp/src/lib/render/lrc-parser.ts:15-22, 69-70
Problem: GlobalLRCLine extends LRCLine and adds globalTimeSeconds, but in
convertToGlobalTimeline, both timeSeconds (inherited) and globalTimeSeconds are set
to the same value. The original "local time" meaning of timeSeconds is lost.
Fix: Remove the LRCLine extension. Define GlobalLRCLine as a standalone interface:
  export interface GlobalLRCLine {
    text: string;
    localTimeSeconds: number;
    globalTimeSeconds: number;
    title: string;
  }
Update convertToGlobalTimeline to set localTimeSeconds = line.timeSeconds and
globalTimeSeconds = segmentStartSeconds + line.timeSeconds. Update all consumers
of GlobalLRCLine to use the explicit field names.
Verification: Run existing tests. Update any code that reads .timeSeconds on
GlobalLRCLine objects.

3.6 Extract shared chapter-building loop
-----------------------------------------
File: webapp/src/lib/render/chapters.ts:44-101, 112-151
Problem: generateChaptersManifest and generateChaptersManifestFromLyrics contain
~50 lines of duplicated segment-iteration and chapter-building logic.
Fix: Extract a private helper:
  function buildChaptersFromSegments(
    segments: AudioSegmentInfo[],
    getLyrics: (hashPrefix: string) => LRCLine[] | Promise<LRCLine[]>
  ): Promise<Chapter[]>
Both public functions provide their own adapter for getLyrics and call this helper.
Verification: Run existing tests.

3.7 Remove serializeChaptersManifest
-------------------------------------
File: webapp/src/lib/render/chapters.ts:259-261
Problem: One-liner wrapper around JSON.stringify(manifest, null, 2). Called in exactly
one place (uploader.ts).
Fix: Delete the function. In uploader.ts, replace
  serializeChaptersManifest(artifacts.chapters)
with
  JSON.stringify(artifacts.chapters, null, 2)
Verification: Run existing tests.


Phase 4: Test Coverage
=======================

4.1 Add tests for generateVideo()
---------------------------------
File: webapp/src/lib/render/video-engine.ts:118
Problem: The primary public method generateVideo() has zero test coverage. Only static
helpers are tested.
Fix: Create webapp/src/test/lib/render/video-engine.test.ts (or extend existing file
if one exists) with tests for:
  - Single-song video generation (mock AssetFetcher.downloadLrc, FrameRenderer.renderFrame,
    FFmpeg spawn)
  - Multi-song video generation with gap transitions
  - Title-card enabled path
  - Blank video fallback when LRC is missing
  - Error handling (LRC download failure, FFmpeg encode failure)
Verification: Run `pnpm test` and verify new tests pass.

4.2 Add tests for stale render state
-------------------------------------
File: webapp/src/lib/db/songsets.ts:74-82
Problem: The "stale" branches in computeRenderState are never tested.
Fix: Add tests to webapp/src/test/api/songsets/db.test.ts (or create if not exists):
  - Test where songsetItems.findFirst returns a newer item (completedAt < createdAt)
  - Test where songset.updatedAt > job.completedAt
  - Test where completedAt is null (job not actually completed)
Verification: Run `pnpm test`.

4.3 Add tests for songs.ts DB module
-------------------------------------
File: webapp/src/lib/db/songs.ts
Problem: No test file exists. Five exported functions have zero coverage.
Fix: Create webapp/src/test/lib/db/songs.test.ts mirroring the pattern in
songsets' test file. Cover:
  - listSongs: success, empty result
  - getSong: success, not found
  - searchSongs: with query, empty query
  - getAlbums: success
  - getComposers: success
Mock db and drizzle-orm as in existing test patterns.
Verification: Run `pnpm test`.

4.4 Add tests for findCurrentLyricIndex and groupLyricsBySong
--------------------------------------------------------------
File: webapp/src/lib/render/lrc-parser.ts:137, 158
Problem: Two exported functions have no tests.
Fix: Create webapp/src/test/lib/render/lrc-parser.test.ts with tests:
  - findCurrentLyricIndex: time before first lyric (-1), time at first lyric (0),
    time between two lyrics, time after last lyric
  - groupLyricsBySong: single song, multiple songs, empty input
Verification: Run `pnpm test`.

4.5 Add test for crossfade-enabled audio path
----------------------------------------------
File: webapp/src/lib/render/audio-engine.ts:160-248
Problem: generateSongsetAudio is only tested with crossfadeEnabled: 0. The crossfade
blending logic in concatenateAudioFiles is untested.
Fix: Add a test in webapp/src/test/lib/render/audio-engine.test.ts (create if needed):
  - At least one item with crossfadeEnabled: 1 and crossfadeDurationSeconds: 2.0
  - Assert that the FFmpeg mock receives a complexFilter containing amix or acrossfade
Verification: Run `pnpm test`.


Phase 5: Documentation
=======================

5.1 Rewrite webapp/README.md
-----------------------------
File: webapp/README.md
Problem: File is unmodified create-next-app boilerplate. No project-specific setup info.
Fix: Replace with webapp-specific README covering:
  - Purpose: Stream of Worship web app for rendering worship music transitions
  - Prerequisites: Node.js 18+, pnpm, PostgreSQL, Cloudflare R2 account
  - Environment setup: Reference .env.example with explanations for DATABASE_URL,
    R2 credentials, Better Auth config
  - Dev server: `pnpm dev` runs on http://localhost:8080
  - Test commands: `pnpm test`, `pnpm test:watch`
  - Build: `pnpm build`
  - Lint: `pnpm lint`
  - Drizzle migration: `npx drizzle-kit push` or `npx drizzle-kit generate`
  - Routes: /login, /register, /songsets, /songsets/[id], /songsets/[id]/render,
    /songsets/[id]/play
Verification: Manual review.

5.2 Update webapp/AGENTS.md with commands
------------------------------------------
File: webapp/AGENTS.md
Problem: Only contains Next.js agent rules notice. No development commands.
Fix: Add webapp-specific commands section:
  - Dev: pnpm dev (runs on :8080)
  - Test: pnpm test / pnpm test:watch
  - Lint: pnpm lint
  - Build: pnpm build
  - Migration: npx drizzle-kit push / generate / migrate
  - Architecture overview: Next.js 16, Drizzle ORM, Better Auth, Cloudflare R2
Verification: Manual review.


Execution Order
===============

Phase 1 (Critical Bugs) must be done first and in order:
  1.1 -> 1.2 -> 1.3

Phase 2 (Major Bugs) can be done in any order after Phase 1.

Phase 3 (Simplification) can be done in any order after Phase 1.
  3.3 and 3.4 should be done together (both in uploader.ts).
  3.6 and 3.7 should be done together (both in chapters.ts).

Phase 4 (Tests) should be done after Phases 1-3 so tests validate the fixed code.
  4.1 and 4.5 depend on Phase 1.2 (render pipeline wiring).

Phase 5 (Documentation) can be done at any time, independent of code changes.


Risk Assessment
===============

High risk:
  - 1.2 (render pipeline wiring): Most complex change. Requires understanding the full
    AudioEngine -> VideoEngine -> R2Uploader pipeline. Must handle errors gracefully
    and update job status correctly. Fire-and-forget pattern needs careful error handling.

Medium risk:
  - 2.3 (N+1 query fix): Requires Drizzle relation/join setup. Schema changes need
    migration.
  - 3.5 (GlobalLRCLine refactor): Interface change affects multiple consumers.

Low risk:
  - 1.1 (startRenderJob fix): Simple deletion of dead code.
  - 1.3 (completedAt migration): Straightforward ALTER TABLE.
  - 2.1 (lrc-parser char counting): Minor logic refinement.
  - 2.2 (songTitle): Requires DB join but straightforward.
  - 3.1-3.4, 3.6-3.7 (simplifications): Pure refactoring, no behavior change.
  - 4.1-4.5 (tests): Additive only, no production code changes.
  - 5.1-5.2 (docs): No code impact.
