# Graph Report - webapp  (2026-09-23)

## Corpus Check
- 432 files · ~297,722 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2234 nodes · 5298 edges · 159 communities (110 shown, 49 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 42 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `8affea1a`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- db/songsets.ts
- SongsetEditor.tsx
- index.ts
- cn
- artifact-cache.ts
- cast-sdk.d.ts
- usePlaybackCore.ts
- useCast.ts
- button.tsx
- AudioPlayerContext.tsx
- constants.ts
- schema.ts
- offline-playback.mjs
- usePresentation.ts
- songs.ts
- renderWithLocale
- useConnectivity.ts
- useLocaleContext
- job-manager.ts
- offline-index.ts
- Deploy Webapp to Vercel
- situation.ts
- messages.ts
- compilerOptions
- share-offline-index.ts
- BrowseSheet.tsx
- semantic/route.ts
- R2Client
- presentation-api.d.ts
- SongsetRow.tsx
- dispatcher.ts
- controller-page.test.tsx
- auth.ts
- HomePageClient.tsx
- lrc-parser.ts
- LyricsReviewSheet.tsx
- RenderForm.tsx
- PlayerLyricsPanel.tsx
- components.json
- ControllerPlayer.tsx
- LocaleContext.tsx
- document-cache.ts
- search-helpers.ts
- effective-key.ts
- completion.ts
- Stream of Worship Web App
- app/layout.tsx
- album-filter.ts
- artifact-cache-sw-parity.test.ts
- settings/route.ts
- share/route.test.ts
- settings/page.tsx
- SettingsForm.tsx
- token.test.ts
- db/favorites.ts
- dashboard.ts
- SongsetsClient.tsx
- chapters.ts
- vercel.json
- log-client-error/route.ts
- signed-url/route.test.ts
- SongsetListSkeleton.tsx
- render-page.test.tsx
- ShareDialog.test.tsx
- songset-list-state.test.ts
- devDependencies
- scripts
- LyricsErrorBoundary.test.tsx
- share-token-cast-expiry.test.ts
- render/page.tsx
- RenderPageClient.tsx
- marks.test.ts
- overrides.test.ts
- ControllerPlayer.test.tsx
- recordingContentHash.test.ts
- WorshipClient.tsx
- rate-limit.ts
- dependencies
- artifact-sizes.test.ts
- ServiceWorkerRegistrar.tsx
- useSongLyrics.ts
- [token]/route.ts
- LyricsTimingEditor.tsx
- useMediaSession.ts
- header-avatar.test.tsx
- SemanticSearch.test.tsx
- deployment.test.ts
- useLyricsFeedback.ts
- Offline Playback e2e Harness (issue #210)
- share-page-invite.test.tsx
- PlayerLyricsPanel.test.tsx
- workflows.test.ts
- server.test.ts
- package.json
- generate-build-info.ts
- forgot-password.test.tsx
- search.integration.test.ts
- health/route.ts
- ResetPasswordForm
- build-info.ts
- @aws-sdk/client-sqs
- @aws-sdk/s3-request-presigner
- @base-ui/react
- better-auth
- class-variance-authority
- clsx
- @dnd-kit/core
- @dnd-kit/sortable
- eslint
- eslint.config.mjs
- eslint-config-next
- js-yaml
- jsdom
- nanoid
- @neondatabase/serverless
- next
- next.config.ts
- next-themes
- openai
- react
- react-dom
- resend
- shadcn
- sonner
- tailwind-merge
- tw-animate-css
- @upstash/ratelimit
- @upstash/redis
- @vercel/analytics
- @vercel/speed-insights
- zod
- @tailwindcss/postcss
- @testing-library/react
- @testing-library/user-event
- tsx
- @types/jsdom
- @types/node
- @types/react
- @types/react-dom
- @vitejs/plugin-react
- vitest
- postcss.config.mjs
- sw.js
- sw-static-route.test.ts
- {
  signIn,
  signOut,
  signUp,
  useSession,
  requestPasswordReset,
  resetPassword,
  changePassword,
  updateUser,
  sendVerificationEmail: requestVerificationEmail,
}

## God Nodes (most connected - your core abstractions)
1. `cn()` - 152 edges
2. `useLocaleContext()` - 70 edges
3. `auth` - 64 edges
4. `renderWithLocale()` - 56 edges
5. `Button()` - 47 edges
6. `db` - 32 edges
7. `useAudioPlayerContext()` - 23 edges
8. `SongTheme` - 23 edges
9. `useConnectivity()` - 22 edges
10. `AlbumFilter` - 22 edges

## Surprising Connections (you probably didn't know these)
- `ApiSongset` --references--> `RenderState`  [EXTRACTED]
  src/app/songsets/SongsetsClient.tsx → src/components/songset/RenderStatusBadge.tsx
- `ApiSongset` --references--> `RenderState`  [EXTRACTED]
  src/app/worship/WorshipClient.tsx → src/components/songset/RenderStatusBadge.tsx
- `FilterToggle()` --calls--> `useLocaleContext()`  [EXTRACTED]
  src/app/worship/WorshipClient.tsx → src/contexts/LocaleContext.tsx
- `LyricsErrorFallback()` --calls--> `useLocaleContext()`  [EXTRACTED]
  src/components/audio/AudioPlayerBar.tsx → src/contexts/LocaleContext.tsx
- `BpmRangeMultiSelectProps` --references--> `BpmBandKey`  [EXTRACTED]
  src/components/songset/BpmRangeMultiSelect.tsx → src/lib/constants.ts

## Import Cycles
- None detected.

## Communities (159 total, 49 thin omitted)

### Community 0 - "db/songsets.ts"
Cohesion: 0.06
Nodes (57): GET(), RouteParams, POST(), createSongsetItemSchema, DELETE(), PATCH(), POST(), updateSongsetItemSchema (+49 more)

### Community 1 - "SongsetEditor.tsx"
Cohesion: 0.06
Nodes (47): ShareDialog, ApiResponse, ApiSongset, ApiSongsetItem, BrowseSheet, RenderJobR2Keys, ShareDialog, SongsetEditorClientProps (+39 more)

### Community 2 - "index.ts"
Cohesion: 0.07
Nodes (33): LyricsResponse, DELETE(), GET(), ALLOWED_FILES, CONTENT_TYPES, FILE_TYPES, GET(), createRenderJobSchema (+25 more)

### Community 3 - "cn"
Cohesion: 0.08
Nodes (35): AudioPlayerBar(), LyricsErrorFallback(), ContainingSongset, LocateSongsetsPopover(), KEY_SHIFT_OPTIONS, TEMPO_OPTIONS, TransitionPanel(), TransitionPanelProps (+27 more)

### Community 4 - "artifact-cache.ts"
Cohesion: 0.07
Nodes (34): formatDuration(), SharePage(), OfflineStatus(), ARTIFACT_CACHE_NAME, artifactCacheKey(), ArtifactCacheStatus, CacheableArtifacts, cacheArtifacts() (+26 more)

### Community 5 - "cast-sdk.d.ts"
Cohesion: 0.04
Nodes (22): AutoJoinPolicy, Capability, cast.framework, CastContext, CastContextEventType, CastOptions, CastState, CastStateEventData (+14 more)

### Community 6 - "usePlaybackCore.ts"
Cohesion: 0.09
Nodes (34): ShareControllerPage(), ADR-0009, ControllerPage(), loadChapters(), SongsetData, LyricJumpListProps, AuthRedirectError, ChapterRecordingHashes (+26 more)

### Community 7 - "useCast.ts"
Cohesion: 0.08
Nodes (33): castAppIdMode(), CastMedia, clamp(), ClientErrorPayload, configuredCastReceiverAppId(), defaultCastReceiverAppId(), detectBrowser(), detectPlatform() (+25 more)

### Community 8 - "button.tsx"
Cohesion: 0.18
Nodes (23): LoginPage(), handleSubmit(), validate(), RegisterPage(), handleSubmit(), validate(), RenderCompleteProps, RenderSubmittedProps (+15 more)

### Community 9 - "AudioPlayerContext.tsx"
Cohesion: 0.09
Nodes (28): GlobalAudioPlayer(), GlobalAudioPlayerProps, PlaybarRouteGuard(), AudioPlayerContext, AudioPlayerContextValue, AudioPlayerProvider(), AudioPlayerState, AudioTrack (+20 more)

### Community 10 - "constants.ts"
Cohesion: 0.11
Nodes (29): BpmRangeMultiSelect(), BpmRangeMultiSelectProps, MusicalKeyMultiSelect(), MusicalKeyMultiSelectProps, ThemeMultiSelect(), ThemeMultiSelectProps, DropdownMenu(), DropdownMenuCheckboxItem() (+21 more)

### Community 11 - "schema.ts"
Cohesion: 0.06
Nodes (31): accounts, accountsRelations, lyricMarksRelations, lyricsFeedback, lyricsFeedbackRelations, recordingsRelations, renderJobsRelations, sessions (+23 more)

### Community 12 - "offline-playback.mjs"
Cohesion: 0.16
Nodes (29): Cdp, CDP_PORT, check(), controllerDocSelector(), evaluateJson(), failFast(), HEADLESS_ARGS, isLoginHtml() (+21 more)

### Community 13 - "usePresentation.ts"
Cohesion: 0.10
Nodes (21): ShareProjectionPage(), ProjectionPage(), ProjectionPlayer(), ProjectionPlayerProps, clamp(), usePresentationReceiver(), UsePresentationReceiverOptions, UsePresentationReceiverResult (+13 more)

### Community 14 - "songs.ts"
Cohesion: 0.14
Nodes (28): sql, favoritesFirstOrder(), favoritesOnlyPredicate(), textArrayLiteral(), fullTextSearchSongs(), buildBpmPredicates(), buildThemePredicate(), buildVisibilityCondition() (+20 more)

### Community 15 - "renderWithLocale"
Cohesion: 0.08
Nodes (20): ResetPasswordPage(), LyricJumpList(), TransitionSheet(), mockFetch, { mockPush, mockRefresh, mockSignIn, mockRequestVerificationEmail }, mockFetch, { mockPush, mockRefresh, mockSignUp, mockRequestVerificationEmail }, { mockPush, mockResetPassword } (+12 more)

### Community 16 - "useConnectivity.ts"
Cohesion: 0.09
Nodes (17): Connectivity, ConnectivityProbe, getConnectivity(), listeners, notify(), probeConnectivity(), setConnectivityProbe(), subscribeConnectivity() (+9 more)

### Community 17 - "useLocaleContext"
Cohesion: 0.12
Nodes (20): BottomNav(), Header(), LABEL_KEY, LanguageSwitcher(), PlaybackControls(), PlaybackControlsProps, SemanticSearch(), AccountSettings() (+12 more)

### Community 18 - "job-manager.ts"
Cohesion: 0.13
Nodes (25): DELETE(), GET(), POST(), songsets, cancelRenderJob(), completeRenderJob(), createRenderJob(), CreateRenderJobInput (+17 more)

### Community 19 - "offline-index.ts"
Cohesion: 0.11
Nodes (22): SongsetEditorClient(), transformItems(), deleteControllerDocument(), getOfflineRecord(), isOfflineIndexAvailable(), listOfflineRecords(), OFFLINE_INDEX_DB_NAME, OFFLINE_INDEX_STORE_NAME (+14 more)

### Community 20 - "Deploy Webapp to Vercel"
Cohesion: 0.06
Nodes (30): Auth cookies not working, Authentication, AWS SQS, Build fails: pnpm not found, Build fails: "Root Directory" not set, Cloudflare R2, Database Migration, Deploy Webapp to Vercel (+22 more)

### Community 21 - "situation.ts"
Cohesion: 0.09
Nodes (22): DELETE(), GET(), LyricsFeedbackResponse, PUT(), FEEDBACK_RATINGS, FEEDBACK_REASONS, FeedbackRating, FeedbackReason (+14 more)

### Community 22 - "messages.ts"
Cohesion: 0.12
Nodes (18): audioBundle, browseBundle, bundle(), BundleKeys, controlBundle, core, favoritesBundle, LOCALES (+10 more)

### Community 23 - "compilerOptions"
Cohesion: 0.07
Nodes (29): dom, dom.iterable, esnext, **/*.mts, .next/dev/types/**/*.ts, next-env.d.ts, .next/types/**/*.ts, node_modules (+21 more)

### Community 24 - "share-offline-index.ts"
Cohesion: 0.12
Nodes (20): invalidateArtifactCache(), deleteShareControllerDocument(), getShareOfflineRecord(), isIndexAvailable(), OfflineShareRecord, openIndexDb(), putShareOfflineRecord(), removeShareOfflineRecord() (+12 more)

### Community 25 - "BrowseSheet.tsx"
Cohesion: 0.20
Nodes (23): FavoritesClientProps, ResultMode, SemanticSearchProps, SemanticSearchResult, UseSemanticSearchOptions, AlbumMultiSelectProps, BrowseSheetProps, SearchMode (+15 more)

### Community 26 - "semantic/route.ts"
Cohesion: 0.15
Nodes (19): GET(), GET(), AlbumFilterSchema, POST(), RequestSchema, FavoriteContext, loadFavoriteContext(), parseAlbumFilterParams() (+11 more)

### Community 27 - "R2Client"
Cohesion: 0.10
Nodes (6): R2Client, AssetFetcher, AssetFetcherOptions, mockFetch, mockGetAudioSignedUrl, mockGetLrcSignedUrl

### Community 28 - "presentation-api.d.ts"
Cohesion: 0.07
Nodes (11): Navigator, Presentation, PresentationAvailability, PresentationCommand, PresentationConnection, PresentationConnectionAvailableEvent, PresentationConnectionList, PresentationMediaStatus (+3 more)

### Community 29 - "SongsetRow.tsx"
Cohesion: 0.15
Nodes (19): PublicSongsetItem, ShareData, ADR-0009, DashboardSongsetCard(), DashboardSongsetCardProps, RenderStatusBadge(), SongCardProps, escapeCssSelectorValue() (+11 more)

### Community 30 - "dispatcher.ts"
Cohesion: 0.14
Nodes (15): DispatchMessage, dispatchToRenderWorker(), getRenderWorkerMode(), RenderWorkerMode, createRestClientFromEnv(), RenderWorkerMessage, RenderWorkerRestClient, RenderWorkerRestConfig (+7 more)

### Community 31 - "controller-page.test.tsx"
Cohesion: 0.08
Nodes (17): castTransportMock, { mockedDownloadShareArtifacts, MockShareNoArtifactsError }, { mockGetOfflineRecord }, mockGetShareOfflineRecord, mockPush, mockReplace, mockRouterInstance, OFFLINE_CHAPTERS (+9 more)

### Community 32 - "auth.ts"
Cohesion: 0.15
Nodes (15): { GET, POST }, GET(), GET(), RouteParams, auth, User, getAlbums(), getSong() (+7 more)

### Community 33 - "HomePageClient.tsx"
Cohesion: 0.17
Nodes (18): FavoritesClient(), HomePageClient(), HomePageClientProps, DashboardSongset, useSemanticSearch(), BrowseSheet(), normalizeAlbumOptions(), SongCard() (+10 more)

### Community 34 - "lrc-parser.ts"
Cohesion: 0.14
Nodes (19): PlayerLyricsPanel(), ChapterLine, CURSOR_LEAD_SECONDS, findLineJumpTarget(), firstLineStartAfter(), lastLineStartBefore(), LINE_RESTART_THRESHOLD_SECONDS, LineJumpDirection (+11 more)

### Community 35 - "LyricsReviewSheet.tsx"
Cohesion: 0.12
Nodes (14): LyricsEditor(), LyricsEditorProps, formatTime(), LyricsReviewSheet(), LyricsReviewSheetProps, TABS, TabType, Sheet() (+6 more)

### Community 36 - "RenderForm.tsx"
Cohesion: 0.14
Nodes (18): formatDurationSafe(), isDifferent(), PreviousRenderJobData, RenderForm(), RenderFormProps, TITLE_CARD_DURATIONS, AlertDialog(), AlertDialogAction() (+10 more)

### Community 37 - "PlayerLyricsPanel.tsx"
Cohesion: 0.15
Nodes (16): chipsForSituation(), LyricsFeedbackRow(), LyricsFeedbackRowProps, LyricsSituationKind, PlayerLyricsPanelProps, StatCard(), StatCardProps, CURSOR_COLORS (+8 more)

### Community 38 - "components.json"
Cohesion: 0.09
Nodes (21): aliases, components, hooks, lib, ui, utils, iconLibrary, menuAccent (+13 more)

### Community 39 - "ControllerPlayer.tsx"
Cohesion: 0.14
Nodes (18): canDocumentFullscreenSnapshot(), canVideoFullscreenSnapshot(), clamp(), ControllerPlayer(), ControllerPlayerBaseProps, ControllerPlayerProps, formatTime(), isPortraitSnapshot() (+10 more)

### Community 40 - "LocaleContext.tsx"
Cohesion: 0.12
Nodes (13): UserSettingsData, LocaleContext, LocaleContextValue, LocaleProvider(), ADR-0004, Locale, mockPathname, mockSession (+5 more)

### Community 41 - "document-cache.ts"
Cohesion: 0.15
Nodes (13): cacheControllerDocument(), cacheDocumentAtPath(), cacheShareControllerDocument(), cacheWorshipListDocument(), controllerDocumentPath(), isLoginPage(), preload(), PreloadTarget (+5 more)

### Community 42 - "search-helpers.ts"
Cohesion: 0.22
Nodes (18): buildBpmPredicate(), buildCatalogKeyTokenRegex(), buildEffectiveKeyPredicate(), buildKeyRegex(), buildKeyTokenRegex(), buildRecordingKeyPredicate(), buildRootTokenRegexForPitchClasses(), displayedKeyPitchClasses() (+10 more)

### Community 43 - "effective-key.ts"
Cohesion: 0.19
Nodes (15): audioPasses(), detectedToEffective(), EffectiveKeyInput, getEffectiveKey(), normalizeMode(), parsedToEffective(), unknown(), missing() (+7 more)

### Community 44 - "completion.ts"
Cohesion: 0.20
Nodes (11): FavoriteButton(), getCompletedSongIds(), isSongCompleted(), Listener, listeners, markSongCompleted(), readCompleted(), resetCompletionForTests() (+3 more)

### Community 45 - "Stream of Worship Web App"
Cohesion: 0.11
Nodes (18): API Summary, Architecture, Cast playback constraints (v3), Database Migrations, Deployment (Vercel Pro + AWS Lambda), Development, Environment Setup, Google Cast SDK Setup (+10 more)

### Community 46 - "app/layout.tsx"
Cohesion: 0.18
Nodes (14): geistMono, geistSans, metadata, RootLayout(), parseAcceptLanguage(), isLocale(), resolveUserLocale(), config (+6 more)

### Community 47 - "album-filter.ts"
Cohesion: 0.22
Nodes (14): AlbumMultiSelect(), albumFilterKey(), compareNullsLast(), extractSeriesPrefix(), extractTrailingNumber(), formatAlbumLabel(), formatAlbumOptionLabel(), normalizeAlbumFilters() (+6 more)

### Community 48 - "artifact-cache-sw-parity.test.ts"
Cohesion: 0.17
Nodes (10): ARTIFACT_FILE_TYPE, artifactCacheKeyForUrl(), artifactHandler(), RFC-9110, parseRangeHeader(), rangeResponseFrom(), SOW_PAGES_CACHE_NAME, SW_MODULE_PATH (+2 more)

### Community 49 - "settings/route.ts"
Cohesion: 0.12
Nodes (14): DEFAULTS, GET(), PUT(), VALID_FONT_PRESETS, VALID_FONTS, VALID_RESOLUTIONS, VALID_TEMPLATES, userSettings (+6 more)

### Community 50 - "share/route.test.ts"
Cohesion: 0.15
Nodes (13): activeShareConditions(), GET(), POST(), songsetShares, resolvePublicOrigin(), completedJob, mockFindFirstJob, mockFindFirstShare (+5 more)

### Community 51 - "settings/page.tsx"
Cohesion: 0.14
Nodes (12): DEFAULT_SETTINGS, fetchSettings(), SettingsPage(), loadSettings(), FontPreviewStylesheets(), mockPush, mockToast, SETTINGS_PAYLOAD (+4 more)

### Community 52 - "SettingsForm.tsx"
Cohesion: 0.12
Nodes (13): FONT_PRESETS, GAP_BEATS_OPTIONS, isIOSLessThan174(), KEY_SHIFT_OPTIONS, LOCALE_OPTIONS, LOOP_WINDOW_OPTIONS, SettingsForm(), SettingsFormProps (+5 more)

### Community 53 - "token.test.ts"
Cohesion: 0.11
Nodes (14): activeShare, completedJob, mockCreateR2Client, mockEnforceRateLimit, mockFindFirstJob, mockFindFirstShare, mockGenerateSignedUrl, mockGetObjectSize (+6 more)

### Community 54 - "db/favorites.ts"
Cohesion: 0.24
Nodes (9): addFavoriteSchema, GET(), POST(), DELETE(), FavoritesPage(), addFavorite(), getFavoriteSongIds(), removeFavorite() (+1 more)

### Community 55 - "dashboard.ts"
Cohesion: 0.23
Nodes (12): loadPage(), HomePage(), songs, getCommunityFavoriteSample(), getDashboardStats(), getRecentFavoriteSongs(), getRecentSongsets(), toSongCardData() (+4 more)

### Community 56 - "SongsetsClient.tsx"
Cohesion: 0.23
Nodes (13): ApiResponse, ApiSongset, ShareDialog, SongsetsClient(), loadSongsets(), SongsetsClientProps, transformSongsets(), transformSongsetsWithOffline() (+5 more)

### Community 57 - "chapters.ts"
Cohesion: 0.23
Nodes (13): AudioSegmentInfo, buildChaptersFromSegments(), ChapterGenerationOptions, ChaptersManifest, chaptersToFFmpegMetadata(), findChapterAtTime(), generateChaptersManifest(), getChapterDurations() (+5 more)

### Community 58 - "vercel.json"
Cohesion: 0.13
Nodes (14): iad1, buildCommand, main, framework, functions, src/app/api/render-jobs/[id]/route.ts, src/app/api/render-jobs/route.ts, git (+6 more)

### Community 59 - "log-client-error/route.ts"
Cohesion: 0.16
Nodes (10): clientErrorSchema, DELETE(), GET(), metaSchema, PUT(), runtime, clientErrorLog, limitMock (+2 more)

### Community 60 - "signed-url/route.test.ts"
Cohesion: 0.17
Nodes (12): GET(), POST(), signedUrlRequestSchema, generateSignedUrlResponse(), mockGenerateSignedUrl, mockGetAudioSignedUrl, mockGetChaptersSignedUrl, mockGetLrcSignedUrl (+4 more)

### Community 61 - "SongsetListSkeleton.tsx"
Cohesion: 0.19
Nodes (4): SettingsSkeleton(), SongsetEditorSkeleton(), SongsetListSkeleton(), Skeleton()

### Community 62 - "render-page.test.tsx"
Cohesion: 0.13
Nodes (8): RENDER_JOB_POLL_INTERVAL_MS, RenderSubmitted(), FetchHandlers, INITIAL_RENDER_DATA, mockPush, renderSubmitted(), RUNNING_JOB, SONGSET

### Community 63 - "ShareDialog.test.tsx"
Cohesion: 0.16
Nodes (10): Toaster(), mockPause, mockPlay, mockReplace, mockEmptyShares(), mockFetch, mockSizes(), mockWindowOpen (+2 more)

### Community 64 - "songset-list-state.test.ts"
Cohesion: 0.20
Nodes (6): useSongsetListBack(), buildSongsetsUrl(), getSongsetListState(), saveSongsetListState(), SongsetListState, songsetsListUrl()

### Community 65 - "devDependencies"
Cohesion: 0.15
Nodes (13): drizzle-kit, env-cmd, devDependencies, drizzle-kit, env-cmd, postgres, tailwindcss, @testing-library/jest-dom (+5 more)

### Community 66 - "scripts"
Cohesion: 0.15
Nodes (13): scripts, build, dev, dev:https, lint, prebuild, start, test (+5 more)

### Community 67 - "LyricsErrorBoundary.test.tsx"
Cohesion: 0.17
Nodes (3): LyricsErrorBoundary, Props, State

### Community 68 - "share-token-cast-expiry.test.ts"
Cohesion: 0.15
Nodes (9): activeShare, completedJob, mockCreateR2Client, mockFindFirstJob, mockFindFirstShare, mockGenerateSignedUrl, mockGetObjectSize, mockGetSongsetPublicView (+1 more)

### Community 69 - "render/page.tsx"
Cohesion: 0.24
Nodes (8): RenderPage(), serializeJob(), RenderPageClient(), normalizeFontFamily(), RenderJobSummary, APP_RENDER_DEFAULTS, buildInitialRenderData(), UserSettingsData

### Community 70 - "RenderPageClient.tsx"
Cohesion: 0.18
Nodes (11): RenderForm, RenderJobData, RenderPageClientProps, RenderScreenState, RenderState, RenderSubmitted, SongsetData, ADR-0002 (+3 more)

### Community 71 - "marks.test.ts"
Cohesion: 0.24
Nodes (8): DELETE(), GET(), POST(), lyricMarks, mockDelete, mockInsert, mockSelect, sessionUser

### Community 72 - "overrides.test.ts"
Cohesion: 0.24
Nodes (8): DELETE(), GET(), POST(), userLrcOverrides, mockDelete, mockFindFirst, mockInsert, sessionUser

### Community 74 - "recordingContentHash.test.ts"
Cohesion: 0.20
Nodes (6): GET(), mockCreateR2ClientFromEnv, mockFetch, mockQueryUserLrcOverrides, mockSelect, sessionUser

### Community 75 - "WorshipClient.tsx"
Cohesion: 0.27
Nodes (7): ApiResponse, ApiSongset, FilterToggle(), hasRenderedVideo(), transformSongsets(), WorshipClient(), WorshipRow

### Community 76 - "rate-limit.ts"
Cohesion: 0.27
Nodes (9): enforceRateLimit(), getLimiter(), InMemoryBucket, inMemoryBuckets, inMemoryLimit(), limiterCache, RateLimiter, __resetRateLimitCacheForTests() (+1 more)

### Community 77 - "dependencies"
Cohesion: 0.22
Nodes (9): @aws-sdk/client-s3, @dnd-kit/utilities, lucide-react, dependencies, @aws-sdk/client-s3, @dnd-kit/utilities, drizzle-orm, lucide-react (+1 more)

### Community 78 - "artifact-sizes.test.ts"
Cohesion: 0.22
Nodes (6): GET(), completedJob, mockCreateR2Client, mockFindFirst, mockGetObjectSize, sessionUser

### Community 79 - "ServiceWorkerRegistrar.tsx"
Cohesion: 0.36
Nodes (5): ServiceWorkerRegistrar(), registerServiceWorker(), ServiceWorkerRegistrationResult, unregisterServiceWorker(), registerMock

### Community 80 - "useSongLyrics.ts"
Cohesion: 0.28
Nodes (7): CachedResult, clearLyricsCache(), lyricsCache, NULL_RESULT, SongLyricsResult, useSongLyrics(), mockFetch

### Community 81 - "[token]/route.ts"
Cohesion: 0.36
Nodes (7): POST(), DELETE(), GET(), NO_CACHE_HEADERS, dailySalt(), getClientIp(), hashIp()

### Community 82 - "LyricsTimingEditor.tsx"
Cohesion: 0.57
Nodes (6): buildLrc(), lrcTimestampToSeconds(), LyricsTimingEditor(), LyricsTimingEditorProps, secondsToLrcTimestamp(), LRCLine

### Community 83 - "useMediaSession.ts"
Cohesion: 0.32
Nodes (4): isMediaSessionAvailable(), MediaSessionActions, MediaSessionMetadata, useMediaSession()

### Community 84 - "header-avatar.test.tsx"
Cohesion: 0.25
Nodes (7): mockPathname, mockPush, mockRefresh, mockSession, mockSignOut, mockToast, renderHeader()

### Community 85 - "SemanticSearch.test.tsx"
Cohesion: 0.25
Nodes (7): defaultProps, hymnsFilter, mockFetch, mockGetPublicAudioUrl, mockPlay, mockSongs, worshipFilter

### Community 86 - "deployment.test.ts"
Cohesion: 0.25
Nodes (4): ENV_EXAMPLE_PATH, README_PATH, VERCEL_JSON_PATH, WEBAPP_ROOT

### Community 87 - "useLyricsFeedback.ts"
Cohesion: 0.29
Nodes (5): FeedbackRating, FeedbackReason, LyricsFeedbackResult, LyricsFeedbackState, mockFetch

### Community 89 - "Offline Playback e2e Harness (issue #210)"
Cohesion: 0.33
Nodes (5): Environment variables, Offline Playback e2e Harness (issue #210), Quirks learned the hard way, Run, Scenarios

### Community 90 - "share-page-invite.test.tsx"
Cohesion: 0.33
Nodes (5): mockPush, mockReplace, mockRouterInstance, { mockUseSession }, shareResponse

### Community 91 - "PlayerLyricsPanel.test.tsx"
Cohesion: 0.33
Nodes (3): mockUseAudioPlayer, mockUseLyricsFeedback, mockUseSongLyrics

### Community 92 - "workflows.test.ts"
Cohesion: 0.33
Nodes (4): CI_WORKFLOW_PATH, DEPLOY_WORKFLOW_PATH, REPO_ROOT, WEBAPP_ROOT

### Community 93 - "server.test.ts"
Cohesion: 0.33
Nodes (3): mockFrom, mockSelect, mockWhere

### Community 94 - "package.json"
Cohesion: 0.40
Nodes (4): name, packageManager, private, version

### Community 95 - "generate-build-info.ts"
Cohesion: 0.40
Nodes (3): commitDate, commitHash, outputPath

### Community 96 - "forgot-password.test.tsx"
Cohesion: 0.50
Nodes (4): ForgotPasswordPage(), handleSubmit(), validate(), { mockRequestPasswordReset }

### Community 99 - "ResetPasswordForm"
Cohesion: 1.00
Nodes (3): ResetPasswordForm(), handleSubmit(), validate()

## Knowledge Gaps
- **680 isolated node(s):** `$schema`, `style`, `rsc`, `tsx`, `config` (+675 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **49 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `auth` connect `auth.ts` to `db/songsets.ts`, `index.ts`, `songs.ts`, `job-manager.ts`, `situation.ts`, `semantic/route.ts`, `app/layout.tsx`, `settings/route.ts`, `share/route.test.ts`, `token.test.ts`, `db/favorites.ts`, `dashboard.ts`, `signed-url/route.test.ts`, `render/page.tsx`, `marks.test.ts`, `overrides.test.ts`, `recordingContentHash.test.ts`, `WorshipClient.tsx`, `artifact-sizes.test.ts`, `[token]/route.ts`, `server.test.ts`?**
  _High betweenness centrality (0.067) - this node is a cross-community bridge._
- **Why does `useLocaleContext()` connect `useLocaleContext` to `SongsetEditor.tsx`, `cn`, `usePlaybackCore.ts`, `button.tsx`, `constants.ts`, `usePresentation.ts`, `BrowseSheet.tsx`, `SongsetRow.tsx`, `HomePageClient.tsx`, `RenderForm.tsx`, `PlayerLyricsPanel.tsx`, `ControllerPlayer.tsx`, `LocaleContext.tsx`, `settings/page.tsx`, `SettingsForm.tsx`, `SongsetsClient.tsx`, `SongsetListSkeleton.tsx`, `RenderPageClient.tsx`, `WorshipClient.tsx`, `ResetPasswordForm`?**
  _High betweenness centrality (0.065) - this node is a cross-community bridge._
- **Why does `cn()` connect `cn` to `HomePageClient.tsx`, `lrc-parser.ts`, `LyricsReviewSheet.tsx`, `SongsetEditor.tsx`, `PlayerLyricsPanel.tsx`, `artifact-cache.ts`, `ControllerPlayer.tsx`, `button.tsx`, `RenderForm.tsx`, `constants.ts`, `completion.ts`, `renderWithLocale`, `album-filter.ts`, `useLocaleContext`, `LyricsTimingEditor.tsx`, `SongsetListSkeleton.tsx`, `BrowseSheet.tsx`, `SongsetRow.tsx`?**
  _High betweenness centrality (0.051) - this node is a cross-community bridge._
- **What connects `$schema`, `style`, `rsc` to the rest of the system?**
  _680 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `db/songsets.ts` be split into smaller, more focused modules?**
  _Cohesion score 0.05894736842105263 - nodes in this community are weakly interconnected._
- **Should `SongsetEditor.tsx` be split into smaller, more focused modules?**
  _Cohesion score 0.0648018648018648 - nodes in this community are weakly interconnected._
- **Should `index.ts` be split into smaller, more focused modules?**
  _Cohesion score 0.06509803921568627 - nodes in this community are weakly interconnected._