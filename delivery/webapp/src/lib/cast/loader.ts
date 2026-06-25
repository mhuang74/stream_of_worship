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
 * Hard ceiling on how long `loadCastSdk()` will wait for the gstatic sender
 * script to fire `window.__onGCastApiAvailable` before settling as failed.
 * The SDK loads in well under a second on any realistic network; the timeout
 * only catches the degenerate case where the script is blocked (CSP / ad
 * blocker) and emits neither a `load` nor an `error` event, so the hook's
 * `castAvailability` cannot strand on `"unknown"`.
 */
const LOAD_TIMEOUT_MS = 15_000;

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

/**
 * Handle of the load-timeout guard scheduled when the script is injected.
 * Cleared on any settlement so a late timeout cannot fire after the SDK has
 * already reported success / failure. Held at module scope so the success path
 * can `clearTimeout` it from `dispatchSettlement`.
 */
let loadTimeoutHandle: ReturnType<typeof setTimeout> | null = null;

/**
 * Single settlement point shared by the global SDK callback, the script
 * `error` listener, and the load timeout. Sets `settled`, drains the pending
 * table (resolving cancelled callers silently, resolving non-cancelled on
 * success, rejecting on failure), and clears the load-timeout guard.
 * Idempotent — the first caller wins; subsequent settlements short-circuit.
 */
function dispatchSettlement(loaded: boolean): void {
  if (settled) return;
  settled = loaded ? { loaded: true } : { loaded: false };
  if (loadTimeoutHandle !== null) {
    clearTimeout(loadTimeoutHandle);
    loadTimeoutHandle = null;
  }
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
}

function injectScriptOnce(): void {
  if (injected) return;
  injected = true;
  const script = document.createElement("script");
  script.src = CAST_SENDER_URL;
  script.async = true;
  // If the gstatic script is blocked by CSP / an ad blocker or the network
  // errors, the browser fires the script `error` event and never invokes
  // `__onGCastApiAvailable`. Settle as failed so the transport surfaces
  // `availability: "unavailable"` and the Presentation/iPhone fallbacks
  // render instead of stranding the UI on "unknown" forever.
  script.addEventListener("error", () => dispatchSettlement(false));
  document.head.appendChild(script);
  // Belt-and-braces: a hard timeout the SDK would never approach on any real
  // network. Catches the degenerate hang where the script emits neither
  // `load` nor `error` (e.g. a hung connection behind a captive portal).
  loadTimeoutHandle = setTimeout(() => dispatchSettlement(false), LOAD_TIMEOUT_MS);
}

function bindGlobalCallback(): void {
  // The SDK calls `window.__onGCastApiAvailable(loaded)` exactly once on
  // completion. We install our own dispatcher that fans out to every in-flight
  // caller except cancelled ones, then records the terminal state so any
  // future caller short-circuits.
  window.__onGCastApiAvailable = (loaded: boolean) => {
    dispatchSettlement(loaded);
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
 * Settlement paths:
 *  - `__onGCastApiAvailable(true)` → resolves (success).
 *  - `__onGCastApiAvailable(false)` → rejects with "Google Cast SDK failed to
 *    load" (the SDK reported it could not initialize).
 *  - Script `error` event (CSP / ad blocker / network failure never invokes
 *    the global callback) → rejects with the same failure error.
 *  - `LOAD_TIMEOUT_MS` elapsed with no callback and no `error` (e.g. a hung
 *    connection that emits nothing) → rejects with the same failure error.
 *
 * Rejection only happens for callers that have NOT aborted. An aborted caller
 * never rejects. Any settlement clears the load-timeout guard so it cannot
 * fire late.
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
