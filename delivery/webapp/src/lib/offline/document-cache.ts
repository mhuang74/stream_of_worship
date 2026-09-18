/**
 * Controller document pre-cache (issue #206): the offline Start Worship tap
 * performs a full document navigation (SPA navigation to a never-visited
 * route needs RSC fetches that cannot be pre-cached reliably), so the SW
 * document route (public/sw.js, sow-pages cache) must already hold the
 * controller's HTML when the network is gone. Download time is the only
 * moment guaranteed to have both the network and the intent to go offline.
 *
 * The HTML alone is not enough to boot: it references hashed script/style
 * URLs the browser has never fetched unless the controller page was opened
 * before. Those are warmed as preload links — the browser fetches them with
 * real subresource destinations ("script"/"style"/"font"), so the SW's
 * static-asset route (StaleWhileRevalidate) stores them for the offline
 * load. A page-initiated fetch() cannot do that: its request destination is
 * empty and no SW route would match.
 *
 * Best-effort by design: the artifacts are the load-bearing part of a
 * download (playback works without this module), so every failure here
 * degrades to "cold start shows the offline fallback" rather than failing
 * the download.
 */

export const SOW_PAGES_CACHE_NAME = "sow-pages";

/** Preload warming must not hang the download UI if a subresource stalls. */
export const WARM_TIMEOUT_MS = 15_000;

/** The controller route for a songset — where Start Worship navigates. */
export function controllerDocumentPath(songsetId: string): string {
  return `/songsets/${songsetId}/play/controller`;
}

interface PreloadTarget {
  href: string;
  as: string;
}

/** Same-origin scripts/styles/fonts the cached HTML references. */
function preloadTargets(html: string): PreloadTarget[] {
  const seen = new Set<string>();
  const targets: PreloadTarget[] = [];

  function add(href: string | null, as: string): void {
    if (!href || seen.has(href)) return;
    try {
      const url = new URL(href, window.location.href);
      if (url.origin !== window.location.origin) return;
      if (url.protocol !== "https:" && url.protocol !== "http:") return;
      seen.add(href);
      targets.push({ href: url.href, as });
    } catch {
      /* unparseable href — not warmable */
    }
  }

  const doc = new DOMParser().parseFromString(html, "text/html");
  for (const el of doc.querySelectorAll("script[src]")) {
    add(el.getAttribute("src"), "script");
  }
  for (const el of doc.querySelectorAll('link[rel="stylesheet"][href]')) {
    add(el.getAttribute("href"), "style");
  }
  for (const el of doc.querySelectorAll('link[rel="preload"][href][as]')) {
    add(el.getAttribute("href"), el.getAttribute("as") ?? "");
  }

  return targets;
}

/** Preload one subresource; resolves on load, error, or the safety timeout. */
function preload({ href, as }: PreloadTarget): Promise<void> {
  const { promise, resolve } = Promise.withResolvers<void>();
  const link = document.createElement("link");
  link.rel = "preload";
  link.as = as;
  link.href = href;
  const timer = window.setTimeout(done, WARM_TIMEOUT_MS);
  function done(): void {
    window.clearTimeout(timer);
    link.remove();
    resolve();
  }
  link.addEventListener("load", done);
  link.addEventListener("error", done);
  document.head.appendChild(link);
  return promise;
}

/**
 * False when the response is the auth proxy's login page rather than the
 * controller document: a 307 to /login (expired session) resolves through
 * fetch() to a 200 login HTML, and caching it under the controller path would
 * dead-end the offline Start Worship tap. Behavioral twin of the document
 * route's cacheWillUpdate guard in public/sw.js — keep the two in lockstep.
 *
 * The identity of the trap is the redirect or the /login final URL — the
 * genuine controller document is itself text/html, so content type cannot
 * discriminate (it only confirms the redirected page is the login HTML).
 */
function isLoginPage(response: Response): boolean {
  if (response.redirected) return true;
  return new URL(response.url, window.location.href).pathname === "/login";
}

/**
 * Fetches the controller document, stores it in the sow-pages cache, and
 * preloads the same-origin assets it references. Resolves true only when the
 * document itself is cached; asset warming is attempted but never fatal.
 */
export async function cacheControllerDocument(songsetId: string): Promise<boolean> {
  return cacheDocumentAtPath(controllerDocumentPath(songsetId));
}

/**
 * Pre-caches the /offline list document (issue #211 follow-up). It is the
 * offline redirect target and the controller's exit route, so it must be
 * servable offline even before any songset download created a reason to
 * cache it. Same best-effort contract as cacheControllerDocument.
 */
export async function cacheOfflineListDocument(): Promise<boolean> {
  return cacheDocumentAtPath("/offline");
}

/** Shared body of cacheControllerDocument / cacheOfflineListDocument. */
async function cacheDocumentAtPath(path: string): Promise<boolean> {
  if (typeof window === "undefined" || !("caches" in window) || !window.caches) {
    return false;
  }

  try {
    const response = await fetch(path);
    if (!response.ok) return false;
    if (isLoginPage(response)) return false;

    const cache = await window.caches.open(SOW_PAGES_CACHE_NAME);
    await cache.put(path, response.clone());

    await Promise.allSettled(preloadTargets(await response.text()).map(preload));
    return true;
  } catch (err) {
    console.warn(`Failed to pre-cache the ${path} document:`, err);
    return false;
  }
}

/**
 * Deletes the pre-cached controller document from the sow-pages cache.
 * Called when a download is removed, superseded by a re-download, or the
 * songset is deleted, so stale or orphaned controller pages never linger
 * (issue #210). The document path is derived from the songsetId — the
 * offline index stores no field for it. Best-effort: false on any failure.
 */
export async function deleteControllerDocument(songsetId: string): Promise<boolean> {
  if (typeof window === "undefined" || !("caches" in window) || !window.caches) {
    return false;
  }

  try {
    const cache = await window.caches.open(SOW_PAGES_CACHE_NAME);
    return await cache.delete(controllerDocumentPath(songsetId));
  } catch {
    return false;
  }
}
