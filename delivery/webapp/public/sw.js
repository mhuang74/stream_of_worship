importScripts(
  "https://storage.googleapis.com/workbox-cdn/releases/7.0.0/workbox-sw.js"
);

workbox.setConfig({ debug: false });

// Precache static Next.js build assets injected at build time.
// __WB_MANIFEST is replaced by workbox-webpack-plugin; fall back to [] for
// the development/CDN-only setup used here.
workbox.precaching.precacheAndRoute(self.__WB_MANIFEST || []);

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

// R2 proxy endpoint serves large binary files – never cache.
workbox.routing.registerRoute(
  ({ url }) => url.pathname.startsWith("/api/r2/"),
  new workbox.strategies.NetworkOnly()
);

// Offline fallback: return a minimal JSON error for uncached API requests.
workbox.routing.setCatchHandler(async ({ event }) => {
  if (event.request.destination === "document") {
    return new Response(
      "<!DOCTYPE html><html><body><p>You are offline. Please reconnect.</p></body></html>",
      { headers: { "Content-Type": "text/html" } }
    );
  }
  if (event.request.headers.get("Accept")?.includes("application/json")) {
    return new Response(
      JSON.stringify({ error: "offline" }),
      { headers: { "Content-Type": "application/json" }, status: 503 }
    );
  }
  return Response.error();
});
