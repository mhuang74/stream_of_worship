/**
 * Artifact range-serving primitives for the service worker (issue #204).
 *
 * Plain JS, no imports: sw.js loads it with importScripts() so both the SW
 * and this unit suite share one implementation. The SW's artifact route maps
 * a proxy request onto the client-managed cache key
 * (/sow-artifact-cache/<renderJobId>/{mp3,mp4,chapters}, see
 * src/lib/offline/artifact-cache.ts) and slices Range requests out of the
 * cached full body.
 */

const ARTIFACT_CACHE_NAME = "sow-artifacts";

/** Proxy filename → artifact cache key type segment. */
const ARTIFACT_FILE_TYPE = {
  "output.mp3": "mp3",
  "output.mp4": "mp4",
  "chapters.json": "chapters",
};

/**
 * Returns the mapped cache key for a proxy artifact request, or null when
 * the URL is not a recognized artifact path.
 *
 * `/api/r2/artifact/<jobId>/<file>` → `/sow-artifact-cache/<jobId>/<type>`
 */
function artifactCacheKeyForUrl(url) {
  const segments = url.pathname.split("/");
  // ["", "api", "r2", "artifact", <jobId>, <file>]
  const jobId = segments[4];
  const file = segments[5];
  if (!jobId || !file) return null;
  const type = ARTIFACT_FILE_TYPE[file];
  if (!type) return null;
  return `/sow-artifact-cache/${jobId}/${type}`;
}

/**
 * Parses a single-range Range header ("bytes=start-end", "bytes=start-",
 * "bytes=-suffix"). Returns {start, end?} / {suffix} or null for anything
 * else (multi-range, malformed, empty) — callers degrade to serving the
 * full body.
 */
function parseRangeHeader(header) {
  const match = /^bytes=(\d*)-(\d*)$/.exec(header.trim());
  if (!match) return null;
  const [, startStr, endStr] = match;
  if (startStr === "" && endStr === "") return null;
  if (startStr === "") {
    const suffix = Number(endStr);
    return suffix > 0 ? { suffix } : null;
  }
  const start = Number(startStr);
  if (endStr === "") return { start };
  const end = Number(endStr);
  return end >= start ? { start, end } : null;
}

/**
 * Builds a 206 response sliced from a cached full 200 body. Without a
 * parsable, satisfiable range this returns the cached response untouched
 * (a full 200 — degrades gracefully rather than erroring).
 */
async function rangeResponseFrom(cachedResponse, rangeHeader) {
  if (!rangeHeader) return cachedResponse;
  const range = parseRangeHeader(rangeHeader);
  if (!range) return cachedResponse;

  // Read through a clone: the degradation paths below hand the cached
  // response back untouched, and a response whose body this function already
  // drained fails the consumer's fetch with a TypeError.
  const blob = await cachedResponse.clone().blob();
  const size = blob.size;

  let start;
  let end;
  if (range.suffix !== undefined) {
    start = Math.max(0, size - range.suffix);
    end = size - 1;
  } else {
    start = range.start;
    end = range.end === undefined ? size - 1 : Math.min(range.end, size - 1);
  }
  if (start >= size) return cachedResponse;

  const slice = blob.slice(start, end + 1);
  return new Response(slice, {
    status: 206,
    headers: {
      "Content-Type": cachedResponse.headers.get("Content-Type") ?? "application/octet-stream",
      "Content-Range": `bytes ${start}-${end}/${size}`,
      "Content-Length": String(end - start + 1),
      "Accept-Ranges": "bytes",
    },
  });
}

/**
 * The SW artifact route handler body (exported for the unit suite; sw.js
 * registers it verbatim). Request URL → mapped cache key, then either serve
 * from cache or fetch-and-store. Cache.put only ever receives full 200
 * responses — Cache.put throws on 206 per the Cache API spec, and partial
 * content would poison the key.
 *
 * Workbox invokes route handlers with exactly {url, request, event, params}
 * (workbox-routing 7 `handler.handle({url, request, event, params})`), so
 * `caches` and `fetchFn` default to the SW globals; tests inject both.
 */
async function artifactHandler({ request, caches: cachesRef = caches, fetchFn = fetch }) {
  const url = new URL(request.url);

  // Explicit file downloads (?download=1) pass through to the network so the
  // proxy's Content-Disposition attachment headers survive (cache-served
  // responses have no attachment header — the download would stream inline).
  if (url.searchParams.get("download") === "1") {
    return fetchFn(request);
  }

  const mappedKey = artifactCacheKeyForUrl(url);
  if (!mappedKey) {
    // Not a recognized artifact path — plain network passthrough.
    return fetchFn(request);
  }

  const cache = await cachesRef.open(ARTIFACT_CACHE_NAME);
  const cached = await cache.match(mappedKey);
  if (cached) {
    return rangeResponseFrom(cached, request.headers.get("range"));
  }

  // Cache miss: always fetch WITHOUT the Range header so the response is a
  // full 200 we can store (a 206 must never be stored — Cache.put throws on
  // 206, and partial content would poison the key), then serve the slice.
  const fullResponse = await fetchFn(new Request(url.origin + url.pathname));
  if (fullResponse.status !== 200) {
    // Non-200 upstream (206 partial, 404, …): pass it through untouched —
    // slicing an error body would fabricate a Content-Range with a wrong
    // total, and a 206 must never be stored.
    return fullResponse;
  }
  await cache.put(mappedKey, fullResponse.clone());
  return rangeResponseFrom(fullResponse, request.headers.get("range"));
}

if (typeof importScripts === "function") {
  // Loaded inside the service worker via importScripts(): publish onto the
  // global scope for the route registration below (see sw.js).
  self.artifactRangeServing = {
    artifactHandler,
    artifactCacheKeyForUrl,
    parseRangeHeader,
    rangeResponseFrom,
  };
}

if (typeof module !== "undefined" && module.exports) {
  // Loaded by a bundler/test runner (CommonJS interop): export the
  // primitives directly.
  module.exports = { artifactHandler, artifactCacheKeyForUrl, parseRangeHeader, rangeResponseFrom };
}