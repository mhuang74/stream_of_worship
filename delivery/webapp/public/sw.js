importScripts(
  "https://storage.googleapis.com/workbox-cdn/releases/7.0.0/workbox-sw.js"
);

// Offline artifact serving (issue #204): the single custom route for
// /api/r2/artifact/* maps the proxy URL onto the client-managed cache key
// (/sow-artifact-cache/<renderJobId>/{mp3,mp4,chapters}, see
// src/lib/offline/artifact-cache.ts) and serves Range requests by slicing
// the cached full body. Those keys are NOT request URLs: Workbox
// strategies can never hit them, and URL-matching range plugins would miss
// too — no range plugin is registered anywhere.

// Range-serving primitives + route handler, unit-tested at
// public/sw-artifact-serving.js.
//
// The query string is a cache-buster whose value is the module's own content
// hash — a browser only installs a new worker when the bytes of *this* file
// differ, and importScripts is resolved during install, so editing the module
// without changing this token would leave every existing client on the old
// module (sw-artifact-serving.test.ts asserts the token matches the module
// hash; regenerate it with: sha256sum public/sw-artifact-serving.js). The
// registration passes updateViaCache: "none" (src/lib/offline/precaching.ts)
// so the token is resolved against the network rather than the HTTP cache.
let artifactHandlerRoute = null;
try {
  importScripts("/sw-artifact-serving.js?v=da3676bbb217");
  artifactHandlerRoute = self.artifactRangeServing?.artifactHandler ?? null;
  if (!artifactHandlerRoute) {
    console.error("[sw] sw-artifact-serving.js did not publish artifactHandler");
  }
} catch (err) {
  // A failed import must not take the whole worker down: the static-asset and
  // API routes below keep working, artifacts fall back to the network.
  console.error("[sw] failed to load sw-artifact-serving.js", err);
}

workbox.setConfig({ debug: false });

// Reachability probe (issue #211): the client's Connectivity check must
// always reflect a genuine round trip — no strategy may ever cache or
// time out around the health endpoint.
workbox.routing.registerRoute(
  ({ url }) => url.pathname === "/api/health",
  new workbox.strategies.NetworkOnly()
);

// Cache static assets (JS, CSS, fonts, images) – serve from cache, refresh in background.
workbox.routing.registerRoute(
  ({ request }) =>
    request.destination === "script" ||
    request.destination === "style" ||
    request.destination === "font" ||
    request.destination === "image",
  new workbox.strategies.StaleWhileRevalidate({
    cacheName: "sow-static-assets",
    plugins: [
      new workbox.expiration.ExpirationPlugin({
        maxEntries: 100,
        maxAgeSeconds: 30 * 24 * 60 * 60, // 30 days
      }),
    ],
  })
);

// Runtime caching for song catalog API – stale-while-revalidate (read-heavy, changes infrequently).
workbox.routing.registerRoute(
  ({ url }) => url.pathname.startsWith("/api/songs"),
  new workbox.strategies.StaleWhileRevalidate({
    cacheName: "sow-api-songs",
    plugins: [
      new workbox.expiration.ExpirationPlugin({
        maxEntries: 100,
        maxAgeSeconds: 24 * 60 * 60, // 1 day
      }),
      new workbox.cacheableResponse.CacheableResponsePlugin({
        statuses: [0, 200],
      }),
    ],
  })
);

// Runtime caching for songset API – network first so mutations are always fresh.
workbox.routing.registerRoute(
  ({ url }) => url.pathname.startsWith("/api/songsets"),
  new workbox.strategies.NetworkFirst({
    cacheName: "sow-api-songsets",
    networkTimeoutSeconds: 10,
    plugins: [
      new workbox.expiration.ExpirationPlugin({
        maxEntries: 50,
        maxAgeSeconds: 7 * 24 * 60 * 60, // 7 days
      }),
      new workbox.cacheableResponse.CacheableResponsePlugin({
        statuses: [0, 200],
      }),
    ],
  })
);

// Signed-URL endpoint is ephemeral – never cache it.
workbox.routing.registerRoute(
  ({ url }) => url.pathname.startsWith("/api/signed-url"),
  new workbox.strategies.NetworkOnly()
);

// Unexpiring controller-document route (issue #210): navigations to the
// controller path the download pre-caches (src/lib/offline/
// document-cache.ts). The generic document route below expires the sow-pages
// cache (7 days / 50 entries), which would silently evict the pre-cached
// controller document and kill the offline tap path weeks after soundcheck
// while artifacts survive. Workbox's expiration plugin has no per-entry
// exemption, so expiry is dodged by routing: this NetworkFirst over the SAME
// sow-pages cache, registered BEFORE the generic route, with the redirect-drop
// guard (lockstep with the pre-cache's isLoginPage guard) but NO expiration
// plugin. Online stays fresh (NetworkFirst); only the controller document is
// immortal. Registered before the generic document route — workbox matches in
// registration order.
//
// request.mode === "navigate" keeps RSC payload fetches (same URL, no
// navigate mode) on the generic route's bounded expiration — unbounded RSC
// growth must not land in an unexpiring cache. The /songsets/ shape keeps the
// share controller (/share/<token>/play/controller) on the generic route too
// (the share flow is out of scope).
workbox.routing.registerRoute(
  ({ request, url }) =>
    request.mode === "navigate" && /^\/songsets\/[^/]+\/play\/controller$/.test(url.pathname),
  new workbox.strategies.NetworkFirst({
    cacheName: "sow-pages",
    networkTimeoutSeconds: 10,
    plugins: [
      {
        cacheWillUpdate: ({ response }) => (response.redirected ? null : response),
      },
      new workbox.cacheableResponse.CacheableResponsePlugin({
        statuses: [0, 200],
      }),
    ],
  })
);

// Offline navigation (issue #206): full document loads and Next.js RSC
// payload fetches (RSC: 1 header). Network-first — online users get the
// fresh document, zero behavioral change — with sow-pages as the offline
// fallback. The controller document is pre-cached here at download time
// (src/lib/offline/document-cache.ts); RSC payloads are NOT pre-cached
// (runtime _rsc hashes can't be predicted) and accumulate from warmed
// sessions instead.
//
// cacheWillUpdate drops redirect responses: a navigation answered with a
// 307 (e.g. the auth proxy redirecting to /login) resolves through fetch()
// to the FINAL page, and caching it would store the login HTML under the
// original URL — an offline visit to that URL would then show the login
// page instead of the document the user asked for.
workbox.routing.registerRoute(
  ({ request }) =>
    request.mode === "navigate" || request.headers.get("RSC") === "1",
  new workbox.strategies.NetworkFirst({
    cacheName: "sow-pages",
    networkTimeoutSeconds: 10,
    plugins: [
      {
        cacheWillUpdate: ({ response }) => (response.redirected ? null : response),
      },
      new workbox.expiration.ExpirationPlugin({
        maxEntries: 50,
        maxAgeSeconds: 7 * 24 * 60 * 60, // 7 days
      }),
      new workbox.cacheableResponse.CacheableResponsePlugin({
        statuses: [0, 200],
      }),
    ],
  })
);

// Artifact proxy endpoint: the single offline-artifact route (mapped cache
// keys + Range support). Registration order is irrelevant here — workbox
// matches routes in registration order and nothing above matches an artifact
// URL — but keep it after the API routes so the reading order mirrors the
// specificity order. Skipped when the module failed to load, in which case
// artifact requests fall through to the network (pre-#204 behaviour).
if (artifactHandlerRoute) {
  workbox.routing.registerRoute(
    ({ url }) => url.pathname.startsWith("/api/r2/artifact/"),
    artifactHandlerRoute
  );
}

// Take control of already-open clients (e.g. the document that registered
// this SW mid-session) without waiting for a reload.
workbox.core.skipWaiting();
workbox.core.clientsClaim();

// Offline fallback: return a minimal JSON error for uncached API requests.
workbox.routing.setCatchHandler(async ({ event }) => {
  if (event.request.destination === "document") {
    return new Response(
      "<!DOCTYPE html><html><body><p>You are offline. Please reconnect.</p></body></html>",
      { headers: { "Content-Type": "text/html" } }
    );
  }
  if (event.request.destination === "video") {
    // Let the <video> element surface its own error event; the player UI
    // reacts to it (media-failure overlay).
    return Response.error();
  }
  if (event.request.headers.get("Accept")?.includes("application/json")) {
    return new Response(
      JSON.stringify({ error: "offline" }),
      { headers: { "Content-Type": "application/json" }, status: 503 }
    );
  }
  return Response.error();
});
