# One browser byte cache for song and Songset playback

Song lists and Songset Audition download the same Recording's master MP3 through the same-origin proxy at `/api/recordings/<hashPrefix>/audio`, and both used to throw those bytes away: the Songset engine fetched and decoded per session and released its clips on Stop, while the song `<audio>` element streamed from a public bucket URL or a freshly minted presigned URL that could never be reused. Replaying an unchanged Recording therefore re-downloaded it every time.

Playback media now goes through `src/lib/audio/audio-bytes-cache.ts`: a memory LRU capped at 64 MB, keyed by Recording `hashPrefix` (not URL) and mirrored one Response per key into Cache Storage (`sow-audio-bytes`), so a page reload still skips the download. Callers get their own copy of the bytes because `decodeAudioData` detaches the ArrayBuffer it is handed. Both players share one cache: the Songset session loads through it before decoding, and the song player resolves a memoized blob object URL through it before falling back to the public bucket URL and then `/api/signed-url`.

Keys are hash prefixes because the same Recording is reachable through the proxy, a public bucket URL, and a freshly signed URL, and all three must share one entry. Cache Storage is best-effort: absent (jsdom, private mode), quota-blocked, or partitioned browsers silently degrade to memory-only. Nothing is prefetched; both layers warm only from real playback.

Two consequences are accepted. A first play of an uncached Recording now waits for the full download instead of starting a progressive stream — the same trade the Songset preview already made, and repeat plays are instant from memory. And a logged-out tab can replay bytes it already fetched in that tab, because keys are per Recording rather than per user.

## Considered options

- **[Rejected] Caching decoded PCM across sessions** — `AudioBuffer`s are ~10× the size of the MP3 and would blow the memory ceiling; bytes plus a per-session decode is what the arrange-preview edits already cost.
- **[Rejected] Keying the cache by URL** — presigned URLs are minted per press and public URLs vary by configuration, so a URL key never hits.
- **[Rejected] Fetching the bucket URL directly into the cache for the song player** — R2 is not CORS-configured for browser `fetch`; the same-origin proxy is the only path that works.
- **[Rejected] A service-worker route for `/api/recordings/*`** — the existing artifact cache is scoped to rendered artifacts by render-job id, and a route would still need the same byte-eviction bookkeeping.
