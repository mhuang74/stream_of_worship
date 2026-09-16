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
 * Fetches the controller document, stores it in the sow-pages cache, and
 * preloads the same-origin assets it references. Resolves true only when the
 * document itself is cached; asset warming is attempted but never fatal.
 */
export async function cacheControllerDocument(songsetId: string): Promise<boolean> {
  if (typeof window === "undefined" || !("caches" in window) || !window.caches) {
    return false;
  }

  const path = controllerDocumentPath(songsetId);

  try {
    const response = await fetch(path);
    if (!response.ok) return false;

    const cache = await window.caches.open(SOW_PAGES_CACHE_NAME);
    await cache.put(path, response.clone());

    await Promise.allSettled(preloadTargets(await response.text()).map(preload));
    return true;
  } catch (err) {
    console.warn("Failed to pre-cache the controller document:", err);
    return false;
  }
}
