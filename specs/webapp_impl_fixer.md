You are a code FIXER.

The implementation plan is at .dex/plan.md.

Step 1: Get Branch Context

Run these commands to understand what was done:
- git log c54d18d6a3f15f95e3a1ddf0fd9456af4cca5fad..HEAD --oneline — see commit history
- git diff c54d18d6a3f15f95e3a1ddf0fd9456af4cca5fad...HEAD — see actual code changes

Step 2: Review the Findings from All Reviewers

── quality ──
Reviewer: quality
Scope: bugs, security, correctness, simplicity

Findings:

- [critical] webapp/src/app/api/render-jobs/[id]/events/route.ts:125-130
  Issue: SSE polling catch block never calls clearInterval or controller.close() on error
  Impact: After any DB polling failure the interval keeps firing every second forever, enqueueing error events into a stream that may already be closed, creating a resource leak that persists for the lifetime of the serverless function. A
subsequent controller.enqueue() after controller.close() also throws, masking the original error.
  Fix: Add clearInterval(intervalId) and controller.close() inside the catch block, mirroring the cleanup done in the terminal-state branches above it.

- [critical] webapp/src/lib/render/audio-engine.ts:291-302
  Issue: aevalsrc silence inputs are passed to fluent-ffmpeg with command.input(input) but without .inputOptions('-f lavfi'), so FFmpeg treats the lavfi filter string as a file path
  Impact: Any render job where at least one gap > 0 generates an aevalsrc input that FFmpeg cannot open as a file. concatenateAudioFiles rejects with a "No such file or directory" error, making every non-trivial songset audio render fail.
  Fix: When the input starts with "aevalsrc=", add the lavfi format flag: command.input(input).inputOptions('-f lavfi').

- [critical] webapp/src/lib/render/video-engine.ts:349-353
  Issue: generateBlankVideo builds the FFmpeg lavfi color filter using raw integer RGB values: color=c=${bgR},${bgG},${bgB}:s=... FFmpeg's color filter does not accept comma-separated integers; it expects a hex string (#rrggbb) or a named
color.
  Impact: Any songset that has no lyrics falls back to generateBlankVideo, which always fails because FFmpeg rejects the malformed color argument, leaving the render job permanently in an error state.
  Fix: Format the color as hex: color=c=#${bgR.toString(16).padStart(2,'0')}${bgG.toString(16).padStart(2,'0')}${bgB.toString(16).padStart(2,'0')}:s=...

- [major] webapp/src/lib/render/lrc-parser.ts:116-120
  Issue: Logic error in estimateLastLyricDuration character counting. The condition !char.trim() is true for whitespace characters, so whitespace is counted as 0.5. All non-CJK printable ASCII characters (Latin letters, digits,
punctuation) are counted as 0 because they fall into neither branch.
  Impact: Duration estimates for mixed Chinese/Latin lyrics are wildly wrong (Latin text contributes nothing to the duration), causing the last lyric line to disappear too early in the video.
  Fix: Change the else-if to a plain else so any non-CJK character counts as 0.5: } else { charCount += 0.5; }

- [major] webapp/src/app/api/signed-url/route.ts:164
  Issue: The GET handler casts fileType from the query string with "as ..." but does not validate it against the allowed enum values. An unrecognised value (e.g. fileType=raw) reaches generateSignedUrl(), where FILE_TYPE_CONFIGS[fileType]
returns undefined, causing fileConfig.contentType to throw a TypeError.
  Impact: Any client passing an unexpected fileType receives a 500 instead of 400. The POST handler validates with Zod but the GET path does not, so the two code paths diverge silently.
  Fix: Add an explicit allowlist check (or re-use the Zod enum) for fileType in the GET handler before constructing the R2 client, and return 400 on invalid values.

- [major] webapp/src/app/api/songsets/route.ts:23-25
  Issue: limit and offset are obtained via parseInt() on unvalidated query strings. parseInt("abc") returns NaN; Math.min(NaN, 100) returns NaN; that NaN is forwarded to Drizzle's findMany limit/offset, which may produce an invalid SQL
query or silently return unexpected results.
  Impact: A malformed request like ?limit=abc can cause a DB error or return all rows ignoring the pagination cap.
  Fix: Sanitize after parsing: const rawLimit = parseInt(...); const limit = Math.min(isNaN(rawLimit) ? 50 : rawLimit, 100); and similarly for offset.

- [minor] webapp/src/app/api/songsets/[id]/items/route.ts:94
  Issue: body.itemId is read from the raw (any-typed) request body after Zod schema validation, but itemId is not part of the validated schema, so it is never type-checked as a string. An attacker could send { "itemId": {"$in": [...]} }
and pass a non-string to updateSongsetItem.
  Impact: Unexpected type passed to the DB query; at minimum an unhandled exception, at worst a query injection depending on Drizzle internals.
  Fix: Add z.string().min(1) for itemId in updateSongsetItemSchema and read it from parsed.data rather than body.

- [minor] webapp/src/db/schema.ts:215
  Issue: completedAt is declared .notNull() in the Drizzle schema, but the generated migration (drizzle/0000_flat_ravenous.sql:84) defines the column as nullable (no NOT NULL constraint). The Drizzle type annotations are wrong.
  Impact: TypeScript infers completedAt as Date rather than Date | null everywhere it is used. In computeRenderState (lib/db/songsets.ts:79), gt(songsetItems.createdAt, job.completedAt) compares against what can actually be null at
runtime, potentially returning "stale" unexpectedly when completedAt is null.
  Fix: Remove .notNull() from the schema declaration so the type matches the real DB column, then handle the null case in computeRenderState.

Overall:
- 8 findings

── implementation ──
Reviewer: implementation
Scope: goal coverage, wiring, completeness, logic flow

Findings:

- [critical] webapp/src/app/api/render-jobs/route.ts:37
  Issue: POST /api/render-jobs creates a DB record and sets status "queued" but never executes the render. AudioEngine and VideoEngine are never called from any route handler or background task. The SSE polling endpoint only reads job
status from DB — nothing writes it forward. Jobs stay "queued" forever.
  Impact: The entire render pipeline is non-functional. The core product feature (rendering audio/video) cannot work at all.
  Fix: After inserting the queued job, trigger the actual render pipeline asynchronously (e.g., call a route that runs the full pipeline in a Vercel Fluid Compute function, or chain to a queue). The render job flow — AudioEngine ->
VideoEngine -> R2Uploader -> completeRenderJob / failRenderJob — must be wired to the create endpoint.

- [critical] webapp/src/lib/render/audio-engine.ts:276
  Issue: Silence gaps are added as inputs via command.input("aevalsrc=0:d=X") without specifying the lavfi format. fluent-ffmpeg translates this to -i "aevalsrc=0:d=X" with no -f lavfi, so FFmpeg treats it as a filename and fails with "No
such file or directory". Both paths in the if/else at lines 294-300 call command.input(input) identically — the silence branch is identical to the audio file branch.
  Impact: Every render involving more than one song (i.e., any real use case) will fail during audio concatenation.
  Fix: Use command.input("aevalsrc=0:d=X").inputFormat("lavfi") for silence inputs, or generate silence inline in the filter_complex string instead of as a separate input.

- [major] webapp/src/middleware.ts (does not exist)
  Issue: Task 1.3 required creating webapp/src/middleware.ts to protect routes. The file does not exist. UI pages — /songsets, /songsets/[id], /songsets/[id]/render, /songsets/[id]/play — have no authentication guard. Unauthenticated users
reach these pages and see loading spinners or error messages rather than being redirected to /login.
  Impact: No client-side route protection. UX is broken for unauthenticated users on all protected pages.
  Fix: Create webapp/src/middleware.ts using Better Auth's betterFetch or the auth.api.getSession helper to check the session and redirect to /login for protected paths.

- [major] webapp/src/app/api/render-jobs/[id]/events/route.ts:17, webapp/src/app/api/render-jobs/[id]/route.ts:6, webapp/src/app/api/songsets/[id]/route.ts:12
  Issue: In Next.js 15+, dynamic route params are async. All dynamic route handlers destructure { params } and then access params.id synchronously (e.g., params.id on line 33 of events/route.ts). This is the synchronous params access
pattern from Next.js 14, which is deprecated and broken in 15+.
  Impact: Route handlers will warn or fail at runtime in Next.js 15. params.id may be undefined or throw depending on the framework version.
  Fix: Change all route handlers to const { id } = await params (where params is typed as Promise<{ id: string }>).

- [major] webapp/src/app/songsets/[id]/render/page.tsx:259-265
  Issue: RenderComplete receives mp3Url={jobData.mp3R2Key}, mp4Url={jobData.mp4R2Key}, chaptersUrl={jobData.chaptersR2Key}. These are R2 storage keys (e.g., renders/abc123/output.mp3), not HTTP URLs. RenderComplete.tsx:55 calls fetch(url)
on them, which fails because they are not valid URLs.
  Impact: The "Download Audio" and "Download Video" buttons on the completion screen always fail. Users cannot download rendered artifacts.
  Fix: Before passing to RenderComplete, call POST /api/signed-url with the R2 key to obtain a time-limited signed URL, then pass that URL as the prop. Or change RenderComplete to accept R2 keys and call the signed-url endpoint internally.

- [major] webapp/src/app/api/render-jobs/route.ts:37, webapp/src/app/api/render-jobs/[id]/events/route.ts:33
  Issue: session.user.id from Better Auth is typed and returned as a string. Both the POST route and SSE route pass it directly to functions typed userId: number (createRenderJob, getRenderJob) without Number() conversion. Other routes
(songsets routes) consistently use Number(session.user.id), revealing that the conversion is required.
  Impact: The Drizzle eq(renderJobs.userId, userId) comparison receives a string where it expects a bigint/number. Depending on DB driver behavior, this either silently matches no rows or throws a type error.
  Fix: Apply Number(session.user.id) consistently in render-jobs/route.ts line 37, render-jobs/[id]/route.ts lines 18 and 51, and events/route.ts line 33.

- [minor] webapp/src/lib/render/video-engine.ts:349
  Issue: Blank video generation uses color=c=${bgR},${bgG},${bgB} where bgR/bgG/bgB are integers (e.g., 20,20,30 for the dark template). FFmpeg's color filter does not accept comma-separated decimal RGB values — the format must be a named
color, hex (0xRRGGBB), or percent notation.
  Impact: generateBlankVideo fails for all songsets where LRC files are missing, which is the error-recovery path. Templates other than pure black would also produce invalid color strings.
  Fix: Convert the RGB array to hex: color=c=0x${bgR.toString(16).padStart(2,'0')}${bgG.toString(16).padStart(2,'0')}${bgB.toString(16).padStart(2,'0')}.

- [minor] webapp/src/lib/render/video-engine.ts:168, 189
  Issue: Chapter songTitle and SegmentInfo.songTitle are set to segment.item.songId?.toString() (a nanoid like "V1StGXR8_Z5jdHi6B-myT"), not the human-readable song title. The AudioSegmentInfo type carries item.songId but not the title
string.
  Impact: Chapter navigation, song title overlay in ProjectionPlayer, and the chapters.json manifest all show opaque IDs instead of song titles. The chapters.json format spec in the plan requires a human-readable songTitle field.
  Fix: Extend SongsetItem (audio-engine.ts) to carry a songTitle field populated from the songs table join. Use that field in VideoEngine and chapters generation.

- [minor] webapp/src/lib/db/songsets.ts:109-121
  Issue: listSongsets calls computeRenderState(row.id) for every songset in the result, and computeRenderState issues 2-3 separate DB queries per songset (findFirst on songsets, findFirst on renderJobs, potentially findFirst on
songsetItems). For a list of 50 songsets this can issue 100-150 queries.
  Impact: The songset list page will be slow and will hit DB connection limits under normal usage.
  Fix: Join render_jobs data in the initial findMany query (using with: { latestRenderJob: true }) to avoid the extra queries, then compute render state in-process from the joined data.

- [minor] webapp/src/db/schema.ts:215 vs webapp/drizzle/0000_flat_ravenous.sql:84
  Issue: Drizzle schema marks completedAt as .notNull() but the generated migration creates the column as nullable ("completed_at" timestamp with time zone with no NOT NULL). Drizzle's TypeScript types for the column are Date
(non-optional), but the live DB schema allows null. Any code reading job.completedAt from the DB on a queued/running job gets null at runtime while TypeScript believes it is Date.
  Impact: If any code path reaches gt(songsetItems.createdAt, job.completedAt) with a null completedAt, the comparison silently evaluates to NULL (false), which causes incorrect render state calculation. The migration and schema are out of
sync.
  Fix: Remove .notNull() from the schema definition to match the actual migration, making the type Date | null. Update computeRenderState to handle the null case (only compare timestamps when completedAt is non-null).

Overall:
- 10 findings (2 critical, 4 major, 4 minor)

── simplification ──
Reviewer: simplification
Scope: unnecessary complexity, over-engineering

Findings:

- major webapp/src/lib/render/job-manager.ts:126-151, 165-191, 230-256, 285-313, 343-369, 396-422, 438-465, 497-523
  Issue: DB row -> RenderJob mapping block is copy-pasted 8 times across createRenderJob, getRenderJob, updateRenderProgress, completeRenderJob, failRenderJob, cancelRenderJob, startRenderJob, and updateRenderJobR2Keys. Each block is ~25
lines of identical field assignments.
  Impact: ~200 lines of pure duplication. Any schema change (e.g. adding a column) requires editing 8 places. Already diverged: some blocks use updated.audioEnabled ?? true while createRenderJob returns job.audioEnabled ?? true, creating a
consistency risk.
  Fix: Extract function mapRowToRenderJob(row): RenderJob at the top of the file and replace every return block with return mapRowToRenderJob(updated).

- major webapp/src/app/api/signed-url/route.ts:15-146, 148-273
  Issue: The GET and POST handlers contain nearly identical code. Both parse the same three identifiers (key, hashPrefix, renderJobId), validate them, build options, and run the same ~50-line if/else decision tree to select the right R2
client method. Error handling at the bottom is also identical.
  Impact: ~120 lines of duplication. A new fileType case or new identifier type has to be added in two places and the divergence will grow.
  Fix: Extract async function generateSignedUrlResponse(params, options) shared by both handlers. Each handler only parses its input format (query params vs JSON body) and calls the shared function.

- major webapp/src/lib/render/uploader.ts:104-139, 149-180
  Issue: uploadFile and uploadBuffer are near-identical. uploadFile reads a file into a buffer, then does exactly the same PutObjectCommand construction and send as uploadBuffer. All the cacheControl/contentType resolution logic is
duplicated.
  Impact: ~70 lines of duplication. Any change to upload behavior (e.g. adding a checksum header) has to be made in both methods.
  Fix: Extract a private putObject(key: string, body: Buffer, size: number, options: UploadOptions): Promise<UploadResult> method. uploadFile reads the file and calls putObject; uploadBuffer calls putObject directly.

- minor webapp/src/lib/render/uploader.ts:401-403
  Issue: createR2UploaderFromEnv() is a one-liner that returns new R2Uploader(). The no-arg R2Uploader constructor already reads from env vars; the factory function is an unnecessary indirection layer.
  Impact: Minor, but it adds a second "factory" alongside the constructor, and callers in other files import it expecting something meaningful.
  Fix: Remove createR2UploaderFromEnv. All call sites are already in this file (uploader.ts:421); replace with new R2Uploader().

- minor webapp/src/lib/render/uploader.ts:416-423
  Issue: The standalone uploadRenderArtifacts function creates an R2Uploader and immediately delegates to its uploadRenderArtifacts method. It is a pass-through wrapper that adds one extra instantiation.
  Impact: Two public APIs for the same operation (class method and standalone function). Tests use both interchangeably, which is confusing.
  Fix: Remove the standalone function. Callers use new R2Uploader().uploadRenderArtifacts(...) or the class directly.

- minor webapp/src/lib/render/lrc-parser.ts:16-22
  Issue: GlobalLRCLine extends LRCLine and adds globalTimeSeconds, but in convertToGlobalTimeline (line 69-70) both timeSeconds and globalTimeSeconds are always set to the same value (segmentStartSeconds + line.timeSeconds). The inherited
timeSeconds from LRCLine now silently means "global time" rather than "local time", which contradicts what LRCLine.timeSeconds means everywhere else.
  Impact: Any code reading globalTimeSeconds.timeSeconds and assuming it is local time will silently produce wrong results. findCurrentLyricIndex uses globalTimeSeconds correctly, but the redundant field creates confusion.
  Fix: Either drop the LRCLine extension and define GlobalLRCLine with explicit localTimeSeconds and globalTimeSeconds fields, or remove globalTimeSeconds and document that timeSeconds is overridden to mean global time.

- minor webapp/src/lib/render/chapters.ts:44-101, 112-151
  Issue: generateChaptersManifest and generateChaptersManifestFromLyrics contain the same chapter-building loop (segment -> startSeconds/endSeconds -> songTitle -> map lines -> push chapter). The only difference is how lines are fetched:
one does an async R2 download, the other looks up a pre-built Map.
  Impact: ~50 lines duplicated. The two functions will drift apart when chapter structure changes.
  Fix: Extract the shared loop into a private helper that accepts a getLyrics: (hashPrefix: string) => Promise<LRCLine[]> | LRCLine[] callback. Both public functions provide their own adapter and call the helper.

- minor webapp/src/lib/render/chapters.ts:259-261
  Issue: serializeChaptersManifest is a one-liner that wraps JSON.stringify(manifest, null, 2). It is called in exactly one place (uploader.ts). The wrapper adds no validation, no error handling, and no logic.
  Impact: Extra import and indirection for callers, while hiding that a simple JSON.stringify is happening.
  Fix: Remove the function. In uploader.ts replace serializeChaptersManifest(artifacts.chapters) with JSON.stringify(artifacts.chapters, null, 2).

Overall:
- 8 findings

── testing ──
Reviewer: testing
Scope: coverage, test quality, edge cases

Findings:
- [critical] webapp/src/lib/render/video-engine.ts:118
  Issue: VideoEngine.generateVideo() has zero test coverage. Only static helpers (getAvailableTemplates, getFontSize, getTemplate, formatChaptersForFFmpeg) are tested; the primary public method that renders frames and encodes the MP4 is
never called in any test.
  Impact: The entire video rendering pipeline — LRC download, frame rendering, FFmpeg encode, chapter injection — can break silently. This is the most functionally important code in the render pipeline.
  Fix: Add tests for generateVideo() mocking AssetFetcher.downloadLrc, FrameRenderer.renderFrame, and the FFmpeg spawn call. Cover single-song, multi-song, and title-card enabled paths.

- [critical] webapp/src/lib/db/songsets.ts:74-82
  Issue: The "stale" render state is never tested. computeRenderState() has two stale branches: (1) a songset_item was created after job.completedAt, (2) songset.updatedAt > job.completedAt. Neither branch is exercised in db.test.ts even
though db.query.songsetItems.findFirst is mocked.
  Impact: A regression in either stale-detection condition would silently compute "fresh" instead of "stale", causing users to play outdated renders without warning.
  Fix: Add two tests to the "computeRenderState" describe block — one where db.query.songsetItems.findFirst returns a newer item, one where songset.updatedAt exceeds job.completedAt.

- [major] webapp/src/lib/db/songs.ts (entire file)
  Issue: No test file exists for the songs database module. The five exported functions — listSongs, getSong, searchSongs, getAlbums, getComposers — have zero unit test coverage.
  Impact: Regressions in song listing, search, or filtering logic cannot be caught automatically. The catalog API is a core feature used on every browse action.
  Fix: Create webapp/src/test/api/songs/db.test.ts mirroring the pattern in db/songsets' test, mocking db and drizzle-orm, and covering success and not-found paths for each function.

- [major] webapp/src/lib/render/lrc-parser.ts:139,161
  Issue: findCurrentLyricIndex and groupLyricsBySong are exported functions with no tests. The other five functions in lrc-parser.ts are tested (in video-engine.test.ts), but these two are absent.
  Impact: findCurrentLyricIndex is used during video playback to determine which lyric to display. groupLyricsBySong is used to organize lyrics by song. Silent regressions here would corrupt lyrics display timing.
  Fix: Add tests in the existing "LRC Parser" describe block in video-engine.test.ts (or a dedicated lrc-parser.test.ts) covering: findCurrentLyricIndex with time before first lyric (-1), time at first lyric (0), and time between two
lyrics; groupLyricsBySong with multiple songs.

- [major] webapp/src/lib/render/audio-engine.ts:160-248
  Issue: generateSongsetAudio is only tested with crossfadeEnabled: 0 on all items. The crossfade-enabled path through calculateGapMs returns 0, but the actual crossfade blending logic in concatenateAudioFiles is untested.
  Impact: Crossfade transitions are a named feature of the render pipeline. If the FFmpeg filter expression for crossfade is wrong, all crossfade-enabled renders produce incorrect output with no test signal.
  Fix: Add a generateSongsetAudio test with at least one item having crossfadeEnabled: 1 and crossfadeDurationSeconds: 2.0, and assert that the FFmpeg mock receives a complexFilter containing an amix or acrossfade filter.

- [minor] webapp/src/test/lib/render/uploader.test.ts:282-289
  Issue: The deleteFile test contains expect(true).toBe(true) with a comment explaining "Skip this test since DeleteObjectCommand is dynamically imported." This is a fake test that always passes regardless of code behavior.
  Impact: The deleteFile method is part of the artifact management API. A bug in it would not be caught.
  Fix: Either properly mock the dynamic import of DeleteObjectCommand using vi.mock with a factory, or remove the test block entirely rather than leaving a permanently-passing placeholder.

- [minor] webapp/src/test/lib/render/video-engine.test.ts:65-93
  Issue: Three VideoEngine constructor tests all assert only expect(engine).toBeDefined(). Since the constructor cannot return undefined in TypeScript, these assertions can never fail regardless of any code change.
  Impact: These tests give false confidence that constructor option handling works correctly (e.g., the titleCardDurationSeconds clamp to 5-30s is verified structurally but never observed in output).
  Fix: After constructing with titleCardDurationSeconds: 3 or 35, call getFontSize or access a property that reflects the clamped value, or generate a single frame to confirm the engine is functional.

- [minor] webapp/src/test/lib/render/audio-engine.test.ts:507-527
  Issue: The two "AudioEngine options" tests outside the main describe block only assert expect(engine).toBeDefined(), testing nothing about whether options are actually applied.
  Impact: Custom targetLufs, outputBitrate, sampleRate, and channels values could be silently ignored if the constructor assignment is broken.
  Fix: Exercise the engine with the custom options, e.g., call calculateGapMs or observe that getAudioInfo uses the configured sampleRate.

- [minor] webapp/src/test/lib/render/video-engine.test.ts:165
  Issue: formatChaptersForFFmpeg is tested by accessing it via (videoEngine as unknown as { formatChaptersForFFmpeg: ... }).formatChaptersForFFmpeg(). This bypasses TypeScript's private access check to test an implementation detail.
  Impact: If formatChaptersForFFmpeg is renamed, inlined, or moved to a helper, the test breaks without the behavior changing. Testing internal formatting is fragile — the format should be observable through generateVideo output.
  Fix: Either make formatChaptersForFFmpeg package-internal and test through the public generateVideo path, or expose it as a static utility and test directly without the cast.

- [minor] webapp/src/test/lib/render/job-manager.test.ts:441-516
  Issue: cancelRenderJob tests cover status "completed", "failed", and missing job, but omit the "cancelled" case — trying to cancel a job that is already cancelled.
  Impact: If the code throws for cancelled→cancel transitions (which it should), a missing guard would allow double-cancellation silently.
  Fix: Add a test where db.query.renderJobs.findFirst returns status "cancelled" and assert cancelRenderJob rejects with a message matching "Cannot cancel job with status: cancelled".

Overall:
- 10 findings

── documentation ──
Reviewer: documentation
Scope: README, internal docs, plan alignment

Findings:

- [critical] webapp/README.md:1-37
  Issue: File is unmodified create-next-app boilerplate. It documents generic Next.js commands (npm run dev, yarn dev, bun dev) instead of project-specific setup. Plan Task 8.6 explicitly requires "Create webapp/README.md with setup
instructions" and it remains unwritten.
  Impact: Any developer or agent opening the webapp directory gets generic Next.js docs instead of how to actually set up and run this app. The dev server runs on port 8080 (not the default 3000 shown), DATABASE_URL / R2 / Better Auth env
vars are not mentioned, and no Drizzle migration commands are documented.
  Fix: Replace the boilerplate with a webapp-specific README covering: purpose, env var setup (reference .env.example with explanations), dev server (pnpm dev runs on port 8080), test commands (pnpm test / pnpm test:watch), build, and
Drizzle migration (npx drizzle-kit push or generate).

- [major] webapp/AGENTS.md:1-5
  Issue: File contains only a single note about Next.js version differences. Plan Task 8.6 requires "Update AGENTS.md with web app commands" but no commands are listed.
  Impact: Agents working inside the webapp/ directory have no instructions for how to run tests, lint, start the dev server, or apply migrations. This causes agents to guess or invent commands.
  Fix: Add the webapp-specific commands: dev (pnpm dev, runs on :8080), test (pnpm test), lint (pnpm lint), build (pnpm build), and migration (npx drizzle-kit push / generate / migrate).

- [major] CLAUDE.md:54-68
  Issue: The architecture section lists four components (POC, Admin CLI, Analysis Service, User App) but does not include the Web App as a fifth component even though it is now a fully implemented core component with its own test suite,
API routes, and pnpm workspace.
  Impact: Agents reading CLAUDE.md will not know to run pnpm commands for the webapp, will not know it exists as an architecturally separate component, and will not know its location (webapp/) or package manager (pnpm).
  Fix: Add a "5. Web App (Browser-Based Editor)" entry under Architecture & Structure with location (webapp/), tech stack (Next.js 16, Drizzle ORM, Better Auth), and the pnpm dev/test commands. Also add webapp test commands to the
Development Commands section.

- [major] webapp/README.md:17
  Issue: The boilerplate instructs users to open http://localhost:3000 but the dev server is configured in package.json as "next dev -p 8080 -H 0.0.0.0", so the correct URL is http://localhost:8080.
  Impact: Developers following the README will get a connection-refused error and incorrectly believe setup failed.
  Fix: Update the URL to http://localhost:8080 when webapp/README.md is rewritten per the critical finding above.

- [minor] webapp/README.md:1-37
  Issue: The register page (/register) is a new user-facing feature added in this branch (commit 3fc1a7e) but is not mentioned in any documentation — not in webapp/README.md, the root README.md Web App section, or the plan's feature list.
  Impact: Users and admins won't know how to create accounts. The root README.md only mentions a "login page" (task 1.3) with no mention of self-registration.
  Fix: Document the /register route in webapp/README.md and in the root README.md Web App features section.

- [minor] .dex/plan.md:569-577
  Issue: Task 8.6 "Update Documentation" has all items unchecked. The root README.md has already had a Web App section added (14 lines per git diff stat showing README.md +14), so the item "Update README.md with web app section" is
effectively done but not marked.
  Impact: Plan status is slightly inaccurate, making it harder to track what remains.
  Fix: Mark "Update README.md with web app section" as done ([x]) since the root README.md already has a Web App section with Quick Start commands and a setup guide.

- [minor] README.md:433
  Issue: "Last Updated: 2025-12-30" footer is stale. The Web App section, pnpm workspace setup, and multiple feature additions occurred well after that date.
  Impact: Minor confusion about documentation freshness.
  Fix: Update the date to reflect the current documentation revision.

Overall:
- 7 findings

Step 3: Collect, Verify, and Fix Findings

3.1 Collect and Deduplicate
- Merge findings from all reviewers.
- Same file:line + same issue → merge.
- Cross-reviewer duplicates → merge, note both sources.

3.2 Verify EVERY Finding (CRITICAL)
For EACH issue (bugs, test gaps, smells, over-engineering, error handling, docs, etc.):
1. Read actual code at file:line
2. Check full context (20-30 lines around)
3. Verify issue is real, not a false positive
4. Check for existing mitigations

Classify as:
- CONFIRMED: Real issue, fix it
- FALSE POSITIVE: Does not exist or already mitigated — discard

IMPORTANT: Pre-existing issues (linter errors, failed tests) should also be fixed.
Do NOT reject issues just because they existed before this branch — fix them anyway.

3.3 Fix All Confirmed Issues
1. Fix all CONFIRMED issues (all types: bugs, tests, smells, simplifications, docs, etc.)
2. Run tests and linter to verify fixes — ALL tests must pass, ALL linter issues resolved
3. ALWAYS commit fixes: git commit -m "fix: address code review findings"

Do NOT introduce new features or refactoring beyond what the confirmed issues require.

OUTPUT FORMAT: No markdown formatting (no bold, code, # headers). Plain text and - lists are fine.
