# Offline Playback e2e Harness (issue #210)

Real-browser end-to-end harness for the offline playback service worker — the
one seam jsdom cannot reach (routes matching, cache expiration, Range
serving). Plain Node, zero new dependencies (Node ≥22 built-in WebSocket is
the CDP client).

## Run

```bash
# 1. Dev server MUST run over HTTPS (Better Auth TRUSTED_ORIGINS excludes
#    http; sign-in POST 403s from a browser served over plain http):
cd delivery/webapp && pnpm dev:https

# 2. Credentials: export SOW_WEBAPP_TESTUSER_LOGIN / SOW_WEBAPP_TESTUSER_PASSWORD
#    (values are NOT stored in this repo).

# 3. Run (fresh profile each run — stale session cookies from a prior run
#    cause auth failures):
rm -rf /tmp/sow-e2e-chrome-profile
pnpm test:e2e:offline
```

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `SOW_E2E_BASE_URL` | `http://localhost:8080` | Dev server base URL — use `https://localhost:8080` with `dev:https` |
| `SOW_E2E_CDP_PORT` | `9222` | Chrome remote-debugging port |
| `SOW_E2E_CHROME` | `/usr/bin/google-chrome` | Chrome binary |
| `SOW_E2E_PROFILE` | `/tmp/sow-e2e-chrome-profile` | Chrome user-data dir (wiped expectations: run fresh) |
| `SOW_E2E_SONGSET_ID` | auto-resolve | Pin the songset instead of picking the first `renderState: "fresh"` set with a completed job |

Without `SOW_WEBAPP_TESTUSER_LOGIN` / `SOW_WEBAPP_TESTUSER_PASSWORD` the
harness skips cleanly (exit 0), so credential-less CI runs are never broken.

## Scenarios

- **(a)** Download for offline → artifact cache keys + offline-index record + pre-cached controller document.
- **(b)** Offline cold start boots the controller player (no fallback page).
- **(c)** Mid-stream network drop → seek still served from cache as 206.
- **(d)** Auto-cache setting observable via the settings API. Coverage note:
  the e2e asserts the setting is readable — the render-completion skip path
  itself is component-tested (render submission is too slow for this harness).
- **(e)** Online regression — controller boots on the R2-served media. The
  spec's Cast-connect assertion needs a real Cast device; the Cast wiring is
  component-tested, so the harness asserts the signed-URL mint instead.
- **(f)** Expired session: the document fetch redirects to `/login` and the pre-cache guard drops it — no login HTML stored under the controller path.
- **(g)** Seek past EOF answers 416 with `Content-Range: bytes */<size>`; malformed Range degrades to 200.
- **(h)** The pre-cached controller document survives the generic document cache's expiry conditions (dedicated unexpiring route).

## Quirks learned the hard way

- The dev server must be up before running (the harness reuses it; it never
  starts one). It pings `${BASE_URL}/login` first and fails fast with a hint.
- Chrome launch diagnostics go to `/tmp/sow-e2e-chrome.log` — check there
  when the CDP endpoint never comes up (profile lock, port already bound by
  a stale Chrome from an earlier killed run: `pkill -f remote-debugging-port`).
- A reused Chrome profile with a stale session cookie poisons sign-in —
  always wipe the profile directory.
- Stale Chrome processes holding the CDP port silently win the attach: the
  new Chrome logs `bind() failed: Address already in use` and the harness
  runs against the OLD browser. Kill strays before running.
