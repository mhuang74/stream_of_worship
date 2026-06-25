// Google Cast Web Sender SDK loader.
//
// The SDK is provided as an external script
// (https://www.gstatic.com/cv/js/sender/v1/cast_sender.js) that, once fetched,
// invokes a globally-named callback `window.__onGCastApiAvailable(loaded)`
// with `loaded===true` on success. This module wraps that contract with a
// ref-counted singleton injection and an unmount-safe Promise surface so that
// React hooks can request the SDK without leaking duplicate script tags or
// scheduling state updates on an unmounted tree.

declare global {
  interface Window {
    __onGCastApiAvailable?: ((loaded: boolean) => void) | null;
  }
}

const CAST_SENDER_URL = "https://www.gstatic.com/cv/js/sender/v1/cast_sender.js";

/**
 * Tracks in-flight loader requests by a monotonically-increasing request id.
 * Entries whose AbortSignal fires before the global callback fires are added
 * here; the global callback resolves/rejects every caller EXCEPT cancelled
 * ones, which are resolved silently. This prevents any React state update from
 * being scheduled against an unmounted component.
 */
const cancelled = new Set<number>();
let nextRequestId = 0;

/**
 * In-flight resolver table keyed by request id. Each entry is the tuple
 * `(resolve, reject, signal?, abortHandler?)` to call when the global callback
 * fires. The abort handler reference is retained so the dispatcher can
 * `removeEventListener` it on the success path (otherwise the `{ once: true }`
 * listener would stay attached to the AbortSignal for its lifetime, leaking
 * the closure over `id`/`resolve`/`pending`). Cancelled entries are removed
 * before the callback runs, so the lookup naturally skips them (they resolve
 * silently via the abort handler).
 */
const pending = new Map<
  number,
  {
    resolve: () => void;
    reject: (e: Error) => void;
    signal?: AbortSignal;
    abortHandler?: () => void;
  }
>();

/**
 * True once the script tag has been appended to the DOM. Multiple callers
 * share a single tag — there is never more than one `cast_sender.js` injected
 * per page load.
 */
let injected = false;

/**
 * True once the global `__onGCastApiAvailable` callback has fired (with any
 * value). Subsequent callers either resolve immediately (loaded) or reject
 * immediately (failed) without re-injecting the script.
 */
let settled: { loaded: true } | { loaded: false } | null = null;

function injectScriptOnce(): void {
  if (injected) return;
  injected = true;
  const script = document.createElement("script");
  script.src = CAST_SENDER_URL;
  script.async = true;
  document.head.appendChild(script);
}

function bindGlobalCallback(): void {
  // The SDK calls `window.__onGCastApiAvailable(loaded)` exactly once on
  // completion. We install our own dispatcher that fans out to every in-flight
  // caller except cancelled ones, then records the terminal state so any
  // future caller short-circuits.
  window.__onGCastApiAvailable = (loaded: boolean) => {
    settled = loaded ? { loaded: true } : { loaded: false };
    const snapshot = Array.from(pending.entries());
    pending.clear();
    for (const [id, entry] of snapshot) {
      if (cancelled.has(id)) {
        // Already aborted: resolve silently, never reject, never schedule UI.
        cancelled.delete(id);
        entry.resolve();
        // The abort handler already removed itself via { once: true }.
        continue;
      }
      if (loaded) entry.resolve();
      else entry.reject(new Error("Google Cast SDK failed to load"));
      // Drop the abort listener now that this entry has settled — the
      // `{ once: true }` flag only auto-removes on abort, not on success.
      if (entry.signal && entry.abortHandler) {
        try {
          entry.signal.removeEventListener("abort", entry.abortHandler);
        } catch {
          /* best-effort */
        }
      }
    }
  };
}

/**
 * Loads the Google Cast Web Sender SDK.
 *
 * - SSR-safe: resolves immediately (no-op) when there is no `window`.
 * - Ref-counted: only one `cast_sender.js` script tag is ever appended; the
 *   first caller injects it, subsequent callers await the same completion.
 * - Unmount-safe: if `opts.signal` aborts before the SDK fires its global
 *   callback, this Promise resolves silently and the caller must NOT schedule
 *   any React state update in response (treat resolution as "give up").
 *
 * Rejection only happens for callers that have NOT aborted and the SDK reports
 * `loaded===false`. An aborted caller never rejects.
 */
export function loadCastSdk(opts?: { signal?: AbortSignal }): Promise<void> {
  // SSR guard — never touch `window` on the server.
  if (typeof window === "undefined") return Promise.resolve();

  // Already settled from a previous load attempt — short-circuit.
  if (settled) {
    if (opts?.signal?.aborted) return Promise.resolve();
    return settled.loaded ? Promise.resolve() : Promise.reject(new Error("Google Cast SDK failed to load"));
  }

  const id = nextRequestId++;
  if (opts?.signal?.aborted) {
    // Caller already gave up before we even started.
    return Promise.resolve();
  }

  const promise = new Promise<void>((resolve, reject) => {
    let abortHandler: (() => void) | undefined;
    if (opts?.signal) {
      abortHandler = () => {
        // If the global callback already ran, nothing to do. Otherwise mark
        // this request cancelled so the dispatcher skips it, and resolve
        // silently now (the caller must not schedule state updates).
        if (pending.has(id)) {
          cancelled.add(id);
          pending.delete(id);
          resolve();
          // The `cancelled` entry is only consulted by the global callback
          // dispatcher to skip a late-arriving settle for this request. If the
          // SDK never fires that callback (script blocked / network down), the
          // entry would otherwise linger for the lifetime of the SPA session
          // — bounded but unbounded within a single long-lived session. Sweep
          // it after a window longer than any plausible SDK load so the Set
          // cannot grow monotonically across repeated mount/unmount cycles.
          const cancelledId = id;
          setTimeout(() => {
            cancelled.delete(cancelledId);
          }, 60_000);
        }
      };
      opts.signal.addEventListener("abort", abortHandler, { once: true });
    }
    pending.set(id, { resolve, reject, signal: opts?.signal, abortHandler });
  });

  // Install the global callback and inject the script exactly once. Doing it
  // here (rather than at module load) keeps side effects lazy and testable.
  if (window.__onGCastApiAvailable === undefined) {
    bindGlobalCallback();
  }
  injectScriptOnce();

  return promise;
}

/**
 * Reports whether the Cast Web Sender SDK globals are present on this window.
 * Deliberately does NOT consult `navigator.presentation` — that surface is a
 * separate (dev-only) fallback path, not a Cast compatibility signal.
 */
export function isCastSdkSupported(): boolean {
  if (typeof window === "undefined") return false;
  return !!(window.chrome?.cast && window.cast?.framework);
}
