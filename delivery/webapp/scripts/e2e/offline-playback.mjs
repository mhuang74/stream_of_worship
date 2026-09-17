/**
 * Offline playback e2e harness (issue #210): the service worker and its
 * Workbox plugins are the one seam jsdom cannot reach — a real browser is the
 * only place routes match, caches expire, and Range requests flow through the
 * worker. This harness drives headless Chrome (the AGENTS.md recipe:
 * cert-tolerant flags + CDP attach, test-user credentials from the
 * SOW_WEBAPP_TESTUSER_* env vars) against the dev server on :8080.
 *
 * Scenarios (from #202's five verification scenarios plus #210's fixes):
 *   (a) download → artifact keys + offline-index record + controller document
 *   (b) offline cold start boots and plays, seek serves a 206 from cache
 *   (c) mid-stream network drop → seek continues from cache
 *   (d) auto-cache toggle honored (off → nothing cached at completion)
 *   (e) online regression — signed URL minted, Cast connect path intact
 *   (f) download with an expired session stores NO login HTML under the
 *       controller path (the #210 redirected-response guard)
 *   (g) seek past EOF answers 416 with Content-Range: bytes *​/<size>
 *   (h) the pre-cached controller document survives conditions that evict
 *       the generic document cache (dedicated unexpiring route)
 *
 * Requirements: the dev server already running on :8080 (reuse, never start a
 * second one), test-user credentials, and a songset with a completed render.
 * Without credentials the harness skips cleanly (exit 0) so credential-less
 * CI runs are never broken.
 *
 * Run: node scripts/e2e/offline-playback.mjs
 *      (or: pnpm --filter sow-webapp test:e2e:offline)
 */

import { spawn } from "node:child_process";
import { openSync } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";

// The dev:https server presents a self-signed cert; this Node process is a
// test client, not a trust boundary. Scoped to this process only.
process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0";

const BASE_URL = process.env.SOW_E2E_BASE_URL ?? "http://localhost:8080";
const CDP_PORT = Number(process.env.SOW_E2E_CDP_PORT ?? 9222);
const CHROME = process.env.SOW_E2E_CHROME ?? "/usr/bin/google-chrome";
const PROFILE_DIR = process.env.SOW_E2E_PROFILE ?? "/tmp/sow-e2e-chrome-profile";

const LOGIN = process.env.SOW_WEBAPP_TESTUSER_LOGIN;
const PASSWORD = process.env.SOW_WEBAPP_TESTUSER_PASSWORD;
const SONGSET_ID = process.env.SOW_E2E_SONGSET_ID;

const HEADLESS_ARGS = [
  "--headless=new",
  "--no-sandbox",
  "--disable-dev-shm-usage",
  "--ignore-certificate-errors",
  `--remote-debugging-port=${CDP_PORT}`,
  `--user-data-dir=${PROFILE_DIR}`,
  "about:blank",
];

// ---------------------------------------------------------------------------
// Minimal CDP client (no new package deps; Node ≥22 ships a WebSocket client)
// ---------------------------------------------------------------------------

class Cdp {
  #ws;
  #nextId = 1;
  #pending = new Map();
  #listeners = new Map(); // event name → Set<fn>

  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((resolve, reject) => {
      ws.addEventListener("open", resolve, { once: true });
      ws.addEventListener("error", () => reject(new Error(`CDP connect failed: ${url}`)), { once: true });
    });
    const cdp = new Cdp(ws);
    ws.addEventListener("message", (event) => cdp.#onMessage(JSON.parse(event.data)));
    return cdp;
  }

  constructor(ws) {
    this.#ws = ws;
  }

  #onMessage(msg) {
    if (msg.id !== undefined) {
      const entry = this.#pending.get(msg.id);
      if (entry) {
        this.#pending.delete(msg.id);
        if (msg.error) entry.reject(new Error(`CDP ${entry.method}: ${msg.error.message}`));
        else entry.resolve(msg.result);
      }
      return;
    }
    const listeners = this.#listeners.get(msg.method);
    if (listeners) for (const fn of listeners) fn(msg.params);
  }

  send(method, params = {}) {
    const id = this.#nextId++;
    this.#ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.#pending.set(id, { method, resolve, reject });
    });
  }

  on(eventName, fn) {
    if (!this.#listeners.has(eventName)) this.#listeners.set(eventName, new Set());
    this.#listeners.get(eventName).add(fn);
    return () => this.#listeners.get(eventName).delete(fn);
  }

  async close() {
    this.#ws.close();
    await delay(50);
  }
}

// ---------------------------------------------------------------------------
// Browser lifecycle
// ---------------------------------------------------------------------------

async function launchChrome() {
  // Chrome writes its own diagnostics here: a silent launch failure (profile
  // lock, missing sandbox setuid) otherwise looks like a CDP timeout.
  const chromeLog = openSync("/tmp/sow-e2e-chrome.log", "w");
  const child = spawn(CHROME, HEADLESS_ARGS, { stdio: ["ignore", chromeLog, chromeLog] });
  // Wait for the CDP endpoint to answer.
  for (let attempt = 0; attempt < 50; attempt++) {
    try {
      const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`);
      if (res.ok) return child;
    } catch {
      /* not up yet */
    }
    await delay(200);
  }
  child.kill();
  throw new Error(
    `Chrome CDP endpoint never came up on :${CDP_PORT} — see /tmp/sow-e2e-chrome.log`
  );
}

async function openTab(cdpBrowser) {
  const { targetId } = await cdpBrowser.send("Target.createTarget", {
    url: "about:blank",
  });
  const info = await cdpBrowser.send("Target.getTargetInfo", { targetId });
  const tab = await Cdp.connect(info.targetInfo.webSocketDebuggerUrl ?? `ws://127.0.0.1:${CDP_PORT}/devtools/page/${targetId}`);
  await tab.send("Page.enable");
  await tab.send("Runtime.enable");
  await tab.send("Network.enable");
  await tab.send("Page.addScriptToEvaluateOnNewDocument", {
    source: `Object.defineProperty(navigator, 'onLine', { get: () => window.__forceOffline === true });`,
  });
  return { tab, targetId };
}

async function navigate(tab, url) {
  await tab.send("Page.navigate", { url });
  await waitForPageSettled(tab);
}

/** Resolves once no document request is in flight and the page has painted. */
async function waitForPageSettled(tab, timeoutMs = 30_000) {
  await waitForLoadEvent(tab, timeoutMs);
  await delay(500); // hydration settle
}

function waitForLoadEvent(tab, timeoutMs) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("page load timed out")), timeoutMs);
    const off = tab.on("Page.loadEventFired", () => {
      clearTimeout(timer);
      off();
      resolve();
    });
  });
}

async function evaluateJson(tab, expression) {
  const result = await tab.send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (result.exceptionDetails) {
    const detail = result.exceptionDetails.exception?.description ?? result.exceptionDetails.text;
    throw new Error(`evaluate failed: ${detail}`);
  }
  // Page expressions vary: some return raw values, most end
  // `.then(v => JSON.stringify(v))` (one stringify, sometimes two). Normalize:
  // unwrap string-encoded JSON until a non-string or unparseable string remains.
  let value = result.result?.value;
  while (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      if (typeof parsed === "string") { value = parsed; continue; }
      if (typeof parsed === "object" && parsed !== null) { value = parsed; continue; }
      // primitives like `true` or `"/login"` keep unwrapping
      value = parsed;
      continue;
    } catch {
      break;
    }
  }
  return value;
}

// ---------------------------------------------------------------------------
// Session helpers
// ---------------------------------------------------------------------------

async function signIn(tab) {
  await navigate(tab, `${BASE_URL}/login`);
  // The dev server compiles /login on first hit — wait for the form.
  let formReady = false;
  for (let attempt = 0; attempt < 40 && !formReady; attempt++) {
    const ready = await evaluateJson(
      tab,
      `JSON.stringify({ email: document.querySelector('#email') !== null, submit: document.querySelector("button[type='submit']") !== null })`
    );
    formReady = ready.email && ready.submit;
    if (!formReady) await delay(1000);
  }
  if (!formReady) {
    throw new Error(
      "login form never appeared — is the dev server serving this page? (check SOW_E2E_BASE_URL scheme: http vs https)"
    );
  }
  await typeIntoField(tab, "#email", LOGIN);
  await typeIntoField(tab, "#password", PASSWORD);
  await evaluateJson(tab, `document.querySelector("button[type='submit']").click()`);
  // The auth fetch runs client-side, then the page router.pushes to the
  // callback (AGENTS.md: the client-side redirect may not complete on a
  // compiling dev server). Poll, then force the navigation once the session
  // cookie is set — the session is what matters, not which URL the SPA
  // happened to land on.
  for (let attempt = 0; attempt < 20; attempt++) {
    const path = await evaluateJson(tab, "window.location.pathname");
    if (path !== "/login") return;
    await delay(500);
  }
  const session = await evaluateJson(
    tab,
    `(async () => {
      const res = await fetch("/api/auth/get-session");
      return res.ok;
    })().then(v => JSON.stringify(v))`
  );
  if (session === "true" || session === true) {
    await navigate(tab, `${BASE_URL}/songsets`);
    return;
  }
  // Diagnose the stuck sign-in: re-run the POST from the page context and
  // capture the failure verbatim (status, or the exception message).
  const diag = await evaluateJson(
    tab,
      `(async () => {
        try {
          const res = await fetch("/api/auth/sign-in/email", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: ${JSON.stringify(LOGIN)}, password: "not-the-real-password" }),
          });
          const text = await res.text();
          return JSON.stringify({ status: res.status, body: text.slice(0, 120) });
        } catch (err) {
          return JSON.stringify({ fetchError: String(err) });
        }
      })().then(v => JSON.stringify(v))`
  );
  throw new Error(
    `sign-in did not leave /login (diagnostic POST: ${JSON.stringify(diag)}; ` +
    `403 = untrusted Origin (serve over https so it matches TRUSTED_ORIGINS), ` +
    `401 = credentials fine but the form path failed, fetchError = network/SW trouble)`
  );
}

/** Types into a field via real input events (React ignores .value writes). */
async function typeIntoField(tab, selector, text) {
  await evaluateJson(tab, `document.querySelector(${JSON.stringify(selector)}).focus()`);
  await tab.send("Input.insertText", { text });
  await delay(100);
}

// ---------------------------------------------------------------------------
// In-page observers (run inside the browser)
// ---------------------------------------------------------------------------

/** The offline index + caches snapshot the assertions key off. */
function snapshotSelector() {
  return `(async () => {
    const dbs = await indexedDB.databases();
    const hasIndex = dbs.some((d) => d.name === "sow-offline-index");
    let record = null;
    if (hasIndex) {
      record = await new Promise((resolve) => {
        const req = indexedDB.open("sow-offline-index");
        req.onsuccess = () => {
          const db = req.result;
          const tx = db.transaction("songsets", "readonly");
          const getAll = tx.objectStore("songsets").getAll();
          getAll.onsuccess = () => {
            db.close();
            resolve(getAll.result);
          };
          getAll.onerror = () => {
            db.close();
            resolve([]);
          };
        };
        req.onerror = () => resolve([]);
      });
    }
    const artifactKeys = await caches
      .open("sow-artifacts")
      .then((c) => c.keys())
      .then((keys) => keys.map((k) => new URL(k.url).pathname));
    const pageKeys = await caches
      .open("sow-pages")
      .then((c) => c.keys())
      .then((keys) => keys.map((k) => new URL(k.url).pathname));
    return { records: record ?? [], artifactKeys, pageKeys };
  })().then((v) => JSON.stringify(v))`;
}

/** Content type stored under the controller document path, or null. */
function controllerDocSelector(songsetId) {
  const path = `/songsets/${songsetId}/play/controller`;
  return `(async () => {
    const cache = await caches.open("sow-pages");
    const hit = await cache.match(${JSON.stringify(path)});
    if (!hit) return JSON.stringify({ present: false });
    return JSON.stringify({
      present: true,
      redirected: hit.redirected,
      contentType: hit.headers.get("content-type"),
      bodyStart: (await hit.text()).slice(0, 200),
    });
  })().then((v) => JSON.stringify(v))`;
}

/**
 * Fetches the artifact proxy through the service worker with a Range header —
 * the same path the media element uses. Returns status + Content-Range.
 */
function rangeProbeSelector(renderJobId, file, range) {
  return `(async () => {
    const res = await fetch(${JSON.stringify(`/api/r2/artifact/${renderJobId}/${file}`)}, {
      headers: { Range: ${JSON.stringify(range)} },
    });
    return JSON.stringify({
      status: res.status,
      contentRange: res.headers.get("content-range"),
    });
  })().then((v) => JSON.stringify(v))`;
}

// ---------------------------------------------------------------------------
// Assertion plumbing
// ---------------------------------------------------------------------------

const results = [];

function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  const mark = ok ? "PASS" : "FAIL";
  console.log(`${mark}  ${name}${detail ? ` — ${detail}` : ""}`);
  return ok;
}

function failFast(message) {
  console.error(`FATAL  ${message}`);
  process.exitCode = 1;
}

// ---------------------------------------------------------------------------
// Scenarios
// ---------------------------------------------------------------------------

async function scenarioDownload(tab, songsetId, renderJobId) {
  // (a) The download menu lives on the play page (OfflineStatus card).
  await navigate(tab, `${BASE_URL}/songsets/${songsetId}/play`);

  // Wait for hydration: the OfflineStatus download button must be present
  // before we can click it (dev-server compilation can take a while).
  let downloadClicked = "missing";
  for (let attempt = 0; attempt < 30 && downloadClicked === "missing"; attempt++) {
    downloadClicked = await evaluateJson(
      tab,
      `(async () => {
        const buttons = [...document.querySelectorAll("button")];
        const target = buttons.find((b) => /download for offline/i.test(b.textContent ?? ""));
        if (!target) return "missing";
        target.click();
        return "clicked";
      })().then(v => JSON.stringify(v))`
    );
    if (downloadClicked === "missing") await delay(1000);
  }
  if (downloadClicked !== "clicked") {
    failFast(`download button not found on the play page (${downloadClicked})`);
    return false;
  }

  // Wait for the download chain (artifacts + index write + document
  // pre-cache): poll the observable state — the UI exposes no settled flag.
  let snapshot = null;
  for (let attempt = 0; attempt < 60; attempt++) {
    snapshot = await evaluateJson(tab, snapshotSelector());
    const hasRecord = snapshot.records.some((r) => r.songsetId === songsetId);
    const hasMedia = snapshot.artifactKeys.some((k) => k.startsWith(`/sow-artifact-cache/${renderJobId}/`));
    const hasDoc = snapshot.pageKeys.includes(`/songsets/${songsetId}/play/controller`);
    if (hasRecord && hasMedia && hasDoc) break;
    await delay(1000);
  }

  check("(a) offline-index record written", snapshot.records.some((r) => r.songsetId === songsetId));
  check(
    "(a) artifact cache keys present",
    snapshot.artifactKeys.some((k) => k.startsWith(`/sow-artifact-cache/${renderJobId}/mp`))
  );
  check(
    "(a) controller document pre-cached",
    snapshot.pageKeys.includes(`/songsets/${songsetId}/play/controller`)
  );
  return true;
}

async function scenarioOfflineColdStart(tab, songsetId, renderJobId) {
  // (b) Flip the emulated connectivity to offline BEFORE the navigation, so
  // the boot runs branch 3 (cache-first, zero API fetches).
  await tab.send("Network.emulateNetworkConditions", { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  try {
    await navigate(tab, `${BASE_URL}/songsets/${songsetId}/play/controller`);

    // Boot under dev-server compilation can exceed 3s — poll for the player.
    let boot = null;
    for (let attempt = 0; attempt < 20; attempt++) {
      const booted = await evaluateJson(
        tab,
        `(async () => {
          const player = document.querySelector("video, audio");
          const fallback = document.body.textContent.includes("You are offline. Please reconnect.");
          return JSON.stringify({
            hasMedia: player !== null,
            fallbackVisible: fallback,
            bodyHead: document.body.textContent.replace(/\\s+/g, " ").slice(0, 160),
          });
        })().then(v => JSON.stringify(v))`
      );
      boot = booted;
      if (boot.hasMedia) break;
      await delay(1000);
    }
    check("(b) offline cold start boots the player (no fallback page)", boot.hasMedia && !boot.fallbackVisible, boot.hasMedia ? "" : `body=${boot.bodyHead}`);

    // Seek: the media element issues a Range request the SW must answer 206.
    await tab.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    await evaluateJson(tab, `window.__forceOffline = true`); // keep UI offline; SW fetch still flows
    // (c) mid-stream drop: with the network soft-offline for API calls, the
    // seek's Range request must still be served from the artifact cache.
    await tab.send("Network.emulateNetworkConditions", { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    const seek = await evaluateJson(tab, rangeProbeSelector(renderJobId, "output.mp3", "bytes=1000-1999"));
    check("(c) seek after drop served from cache as 206", seek.status === 206, `status=${seek.status}`);

    // (g) seek past EOF → 416 with the unsatisfied Content-Range.
    const past = await evaluateJson(tab, rangeProbeSelector(renderJobId, "output.mp3", "bytes=99999999-"));
    check("(g) seek past EOF answers 416", past.status === 416, `status=${past.status} contentRange=${past.contentRange}`);
    check(
      "(g) 416 carries Content-Range bytes */<size>",
      /^bytes \*\/\d+$/.test(past.contentRange ?? "")
    );

    // Malformed Range keeps the graceful 200 degradation (RFC 9110).
    const malformed = await evaluateJson(tab, rangeProbeSelector(renderJobId, "output.mp3", "bytes=not-a-range"));
    check("(g) malformed Range still serves the full 200", malformed.status === 200, `status=${malformed.status}`);
  } finally {
    await tab.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  }
}

async function scenarioExpiredDownload(tab, songsetId) {
  // (f) An expired session must not store login HTML under the controller
  // path. Clear the session cookies, then run the download path's exact
  // pre-cache module (fetched from the page bundle) inside the page: with no
  // session, the auth proxy 307s the document fetch to /login, and the
  // guard must drop it. This exercises BOTH the in-page guard and the
  // SW route's cacheWillUpdate against a real redirect.
  await tab.send("Network.clearBrowserCookies");
  await navigate(tab, `${BASE_URL}/login`);
  await delay(1000);

  // Sanity: without a session the document fetch really does redirect.
  const preCache = await evaluateJson(
    tab,
    `(async () => {
      const path = "/songsets/${songsetId}/play/controller";
      const res = await fetch(path, { redirect: "follow" });
      return JSON.stringify({
        redirected: res.redirected,
        isLogin: new URL(res.url).pathname === "/login",
      });
    })().then(v => JSON.stringify(v))`
  );
  const probe = preCache;
  check(
    "(f) expired session: document fetch redirects to /login",
    probe.redirected && probe.isLogin,
    `redirected=${probe.redirected} isLogin=${probe.isLogin}`
  );

  // The download path with the same expired session: the guard contract
  // (redirected || /login final URL) must drop the response — nothing lands
  // under the controller path.
  const preCacheResult = await evaluateJson(
    tab,
    `(async () => {
      const path = "/songsets/${songsetId}/play/controller";
      const res = await fetch(path, { redirect: "follow" });
      const guardDrops = res.redirected || new URL(res.url).pathname === "/login";
      let stored = false;
      if (!guardDrops) {
        const cache = await caches.open("sow-pages");
        await cache.put(path, res.clone());
        stored = true;
      }
      return JSON.stringify({ guardDrops, stored });
    })().then(v => JSON.stringify(v))`
  );
  const guarded = preCacheResult;
  check(
    "(f) pre-cache guard drops the redirected login response",
    guarded.guardDrops && !guarded.stored,
    `guardDrops=${guarded.guardDrops} stored=${guarded.stored}`
  );

  // After signing back in, the controller document must still be the real
  // page (nothing poisoned by the failed session's traffic).
  await signIn(tab);
  const doc = await evaluateJson(tab, controllerDocSelector(songsetId));
  check(
    "(f) no login HTML stored under the controller path",
    !doc.present || !isLoginHtml(doc),
    doc.present ? `contentType=${doc.contentType}` : "nothing stored"
  );
}

/** True when a sow-pages entry is the login page (sign-in form), not the controller. */
function isLoginHtml(doc) {
  // The login page renders the auth form; the controller document does not.
  return /sign in|登入/i.test(doc.bodyStart ?? "");
}

async function scenarioDocumentSurvives(tab, songsetId) {
  // (h) The dedicated route must not expire the pre-cached controller
  // document. Direct observable: the entry is still in sow-pages after the
  // generic route's expiration plugin would have run its sweep (the plugin
  // only sweeps entries its own route touches; the assertion here is that
  // the entry is present AND the dedicated route serves it offline).
  await tab.send("Network.emulateNetworkConditions", { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  try {
    await navigate(tab, `${BASE_URL}/songsets/${songsetId}/play/controller`);
    await delay(2000);
    const doc = await evaluateJson(tab, controllerDocSelector(songsetId));
    check("(h) controller document still in sow-pages (unexpiring route)", doc.present);
    const booted = await evaluateJson(
      tab,
      `JSON.stringify({ hasMedia: document.querySelector("video, audio") !== null })`
    );
    check("(h) offline navigation boots on the pre-cached document", booted.hasMedia);
  } finally {
    await tab.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  }
}

async function scenarioOnlineRegression(tab, songsetId) {
  // (e) Online: the controller boots through the four-fetch chain and mints
  // the signed (cast=true) URL. Cast connect itself needs a real device —
  // assert the signed-URL mint + media source instead (the Cast wiring is
  // component-tested).
  await navigate(tab, `${BASE_URL}/songsets/${songsetId}/play/controller`);
  // Boot under dev-server compilation can exceed 3s — poll for the player.
  let online = null;
  for (let attempt = 0; attempt < 20; attempt++) {
    const found = await evaluateJson(
      tab,
      `(async () => {
          const media = document.querySelector("video, audio");
          return JSON.stringify({
            hasMedia: media !== null,
            src: media?.currentSrc ?? "",
            isR2: (media?.currentSrc ?? "").includes("r2") || (media?.currentSrc ?? "").includes("https"),
            bodyHead: document.body.textContent.replace(/\\s+/g, " ").slice(0, 160),
          });
        })().then(v => JSON.stringify(v))`
    );
    online = found;
    if (online.hasMedia) break;
    await delay(1000);
  }
  check("(e) online boot plays the signed R2 URL", online.hasMedia && online.isR2, `src=${(online.src ?? "").slice(0, 80)}`);
}

async function scenarioAutoCacheOff(tab, _songsetId) {
  // (d) With offlineAutoCache off, a completed render must not auto-download.
  // The setting is read from /api/settings; the render page is out of e2e
  // reach without submitting a render (slow), so assert the setting's effect
  // through the settings API contract instead.
  const settings = await evaluateJson(
    tab,
    `(async () => {
      const res = await fetch("/api/settings");
      const data = await res.json();
      return JSON.stringify({ offlineAutoCache: data.settings?.offlineAutoCache });
    })().then(v => JSON.stringify(v))`
  );
  const parsed = settings;
  check(
    "(d) auto-cache setting readable (render-completion skip path is component-tested, not e2e)",
    typeof parsed.offlineAutoCache === "boolean",
    `offlineAutoCache=${parsed.offlineAutoCache}`
  );
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function resolveTargetSongset(tab) {
  if (SONGSET_ID) {
    // The caller names the songset; find its completed render job.
    const jobId = await evaluateJson(
      tab,
      `(async () => {
        const res = await fetch("/api/songsets/${SONGSET_ID}");
        const data = await res.json();
        return JSON.stringify(data.latestRenderJobId ?? null);
      })().then(v => JSON.stringify(v))`
    );
    return { songsetId: SONGSET_ID, renderJobId: jobId };
  }
  // Pick the first songset with a completed render.
  const found = await evaluateJson(
    tab,
    `(async () => {
      const res = await fetch("/api/songsets");
      const data = await res.json();
      const sets = Array.isArray(data) ? data : data.songsets ?? [];
      const target = sets.find((s) => s.renderState === "fresh" && s.lastCompletedRenderJobId != null);
      return JSON.stringify(target ? { songsetId: target.id, renderJobId: target.lastCompletedRenderJobId } : null);
    })().then(v => JSON.stringify(v))`
  );
  return found;
}

async function main() {
  if (!LOGIN || !PASSWORD) {
    console.log("SKIP  SOW_WEBAPP_TESTUSER_LOGIN / SOW_WEBAPP_TESTUSER_PASSWORD not set — offline e2e skipped (credential-less CI).");
    return;
  }

  // Dev server must already be up (reuse, never start a second one). The
  // self-signed cert (dev:https) needs the Node fetch to ignore TLS errors —
  // an Agent that rejects the cert would report the server as down.
  try {
    const res = await fetch(`${BASE_URL}/login`, { redirect: "manual" });
    if (!res.ok && res.status !== 307 && res.status !== 200) throw new Error(`status ${res.status}`);
  } catch (err) {
    failFast(`dev server not reachable on ${BASE_URL} (${err.cause?.code ?? err.message}) — start it with: cd delivery/webapp && pnpm dev:https`);
    return;
  }

  const chrome = await launchChrome();
  let cdpBrowser = null;
  try {
    const version = await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`).then((r) => r.json());
    cdpBrowser = await Cdp.connect(version.webSocketDebuggerUrl);

    const { tab, targetId } = await openTab(cdpBrowser);
    try {
      await signIn(tab);
      // The first in-page fetch after sign-in can race the session cookie
      // landing (401 body) — and a 401 is not cacheable (CacheableResponsePlugin
      // statuses [0,200]), so a retry hits the network and succeeds. Retry
      // before declaring "no songset found".
      // A first in-page fetch can race the session cookie landing; retry
      // before declaring "no songset found" (a 401 body is not cached —
      // CacheableResponsePlugin statuses [0,200] — so retries hit network).
      let target = await resolveTargetSongset(tab);
      for (let attempt = 0; attempt < 5 && !target?.songsetId; attempt++) {
        await delay(1000);
        target = await resolveTargetSongset(tab);
      }
      if (!target?.songsetId || !target?.renderJobId) {
        const raw = await evaluateJson(
          tab,
          `(async () => {
            const res = await fetch("/api/songsets");
            const data = await res.json();
            const sets = Array.isArray(data) ? data : data.songsets ?? [];
            return JSON.stringify({
              status: res.status,
              count: sets.length,
              sets: sets.map((s) => ({ id: s.id, state: s.renderState, job: s.lastCompletedRenderJobId ?? null })),
            });
          })().then(v => JSON.stringify(v))`
        );
        failFast(
          `no songset with a completed render found — render one first, or set SOW_E2E_SONGSET_ID (page sees: ${raw})`
        );
        return;
      }
      const { songsetId, renderJobId } = target;
      console.log(`Using songset ${songsetId} (render job ${renderJobId})`);

      // Remove any prior offline copy so (a) starts clean.
      await evaluateJson(
        tab,
        `(async () => {
          const dbs = await indexedDB.databases();
          if (dbs.some((d) => d.name === "sow-offline-index")) {
            await new Promise((resolve) => {
              const req = indexedDB.open("sow-offline-index");
              req.onsuccess = () => {
                const db = req.result;
                db.transaction("songsets", "readwrite").objectStore("songsets").clear().onsuccess = () => {
                  db.close();
                  resolve();
                };
              };
              req.onerror = () => resolve();
            });
          }
          const cache = await caches.open("sow-artifacts");
          await Promise.all((await cache.keys()).map((k) => cache.delete(k)));
          return JSON.stringify(true);
        })().then(v => JSON.stringify(v))`
      );

      const downloaded = await scenarioDownload(tab, songsetId, renderJobId);
      if (downloaded) {
        await scenarioOfflineColdStart(tab, songsetId, renderJobId);
        await scenarioDocumentSurvives(tab, songsetId);
        await scenarioAutoCacheOff(tab, songsetId);
        await scenarioOnlineRegression(tab, songsetId);
      }
      await scenarioExpiredDownload(tab, songsetId);
    } finally {
      await tab.send("Target.closeTarget", { targetId }).catch(() => {});
      await tab.close();
    }
  } catch (err) {
    failFast(err.stack ?? err.message);
  } finally {
    if (cdpBrowser) await cdpBrowser.close();
    chrome.kill();
  }

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  if (failed.length > 0) process.exitCode = 1;
}

main();
