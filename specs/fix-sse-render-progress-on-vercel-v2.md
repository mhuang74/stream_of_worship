# Fix SSE Render Progress on Vercel (v2)

## Problem

The SSE-based render progress system (`/api/render-jobs/[id]/events`) is fundamentally incompatible with Vercel's serverless function execution model:

1. **SSE stream is killed by Vercel's function timeout.** The SSE events route has `maxDuration: 60` (60s), but renders take 10+ minutes. When the serverless function times out, the SSE connection drops, triggering `onerror` on the client.

2. **Console error: `SSE error: [object Event]`** — The `onerror` handler logs the raw Event object which serializes to `[object Event]`, providing no useful debugging info.

3. **Retry loop wastes resources.** After SSE drops, the client retries up to 3 times with exponential backoff (2s, 4s, 6s). Each retry creates a new SSE connection that will also timeout after ~60s, creating a cycle of: connect → receive updates for ~60s → timeout → error → retry → repeat.

4. **No fallback to polling.** When SSE fails after max retries, the component shows a "Lost connection" error and stops updating entirely, even though the REST endpoint `/api/render-jobs/[id]` works perfectly (confirmed by network requests returning 200).

5. **`onopen` resets retry count prematurely.** The SSE connection opens successfully (Vercel responds with 200 and starts streaming), so `retryCount` resets to 0 on each new connection. This means the 3-retry limit is never actually reached — the client retries indefinitely in a loop.

## Solution

Replace the SSE-first approach with a **polling-first approach** that uses SSE as an optional enhancement when available. The REST polling endpoint `/api/render-jobs/[id]` already exists and works reliably on Vercel. SSE should be treated as a best-effort optimization, not the primary transport.

### v2 Changes from v1 Spec

The v1 spec had several operational gaps identified during review:

| Concern | v1 Behavior | v2 Fix |
|---------|-------------|--------|
| Poll failure backoff | Fixed 2s interval even when API is down | Exponential backoff: 2s → 4s → 8s → 16s → 30s cap; reset on success |
| Callback stability | `onComplete`/`onError`/`onCancel` in useEffect deps | Store callbacks in refs; remove from dependency array |
| Interval type | `NodeJS.Timeout` (wrong in browser) | `ReturnType<typeof setInterval>` |
| Stale detection dedup | Copy-pasted in poll() and SSE onmessage | Extract `checkStaleProgress()` helper |
| In-flight fetch on unmount | No abort; `setProgress()` called after unmount | `AbortController` aborted in cleanup |

---

## Phase 1: `RenderProgress.tsx` — Replace SSE with polling as primary transport

### Current behavior

- SSE connection on mount → retry up to 3 times on error → show "Lost connection" error
- Separate `useEffect` fetches initial status via REST

### New behavior

- Start with REST polling via `setInterval` at 2-second intervals using `/api/render-jobs/${jobId}`
- Optionally attempt SSE connection; if it connects, reduce polling interval to 5s as a health check fallback
- If SSE drops, seamlessly continue polling (no error shown to user)
- Remove the "Lost connection" error state entirely — polling will always work as long as the API is up
- Keep stale progress detection (10 min threshold) as a safety net

### Key implementation details

```typescript
const POLL_INTERVAL_MS = 2000
const SSE_FALLBACK_POLL_INTERVAL_MS = 5000
const MAX_POLL_INTERVAL_MS = 30000
const STALE_PROGRESS_THRESHOLD_MINUTES = 10

function checkStaleProgress(
  data: RenderProgressData,
  lastElapsedRef: React.MutableRefObject<number | null>,
  lastChangeTimeRef: React.MutableRefObject<number | null>,
  setStaleWarning: (msg: string | null) => void,
) {
  if (data.elapsedSeconds !== lastElapsedRef.current) {
    lastElapsedRef.current = data.elapsedSeconds
    lastChangeTimeRef.current = Date.now()
    setStaleWarning(null)
  } else if (lastChangeTimeRef.current !== null) {
    const minutesSinceChange = (Date.now() - lastChangeTimeRef.current) / 60000
    if (minutesSinceChange > STALE_PROGRESS_THRESHOLD_MINUTES) {
      setStaleWarning(
        `Progress hasn't updated in ${Math.round(minutesSinceChange)} minutes. ` +
        `The render may be stuck. You can cancel and try again.`
      )
    }
  }
}

// Inside the component, before the useEffect:
const onCompleteRef = useRef(onComplete)
const onErrorRef = useRef(onError)
const onCancelRef = useRef(onCancel)

useEffect(() => {
  onCompleteRef.current = onComplete
  onErrorRef.current = onError
  onCancelRef.current = onCancel
})

useEffect(() => {
  let pollInterval: ReturnType<typeof setInterval>
  let sseConnected = false
  let consecutiveFailures = 0
  const abortController = new AbortController()

  const getPollInterval = () =>
    Math.min(POLL_INTERVAL_MS * Math.pow(2, consecutiveFailures), MAX_POLL_INTERVAL_MS)

  const poll = async () => {
    try {
      const response = await fetch(`/api/render-jobs/${jobId}`, {
        signal: abortController.signal,
      })
      if (!response.ok) throw new Error("Failed to fetch job status")
      const data = await response.json()

      consecutiveFailures = 0

      const progressData: RenderProgressData = {
        phase: data.phase ?? "preparing",
        phaseIndex: data.phaseIndex ?? 0,
        totalPhases: data.totalPhases ?? 5,
        estimatedTotalSeconds: data.estimatedTotalSeconds ?? 0,
        elapsedSeconds: data.elapsedSeconds ?? 0,
        status: data.status,
        errorMessage: data.errorMessage,
      }
      setProgress(progressData)

      checkStaleProgress(progressData, lastElapsedRef, lastChangeTimeRef, setStaleWarning)

      // Terminal states — stop polling
      if (data.status === "completed") {
        onCompleteRef.current()
        clearInterval(pollInterval)
      } else if (data.status === "failed") {
        const errMsg = data.errorMessage || "Render failed"
        setError(errMsg)
        onErrorRef.current(errMsg)
        clearInterval(pollInterval)
      } else if (data.status === "cancelled") {
        onCancelRef.current()
        clearInterval(pollInterval)
      }
    } catch (err) {
      if (abortController.signal.aborted) return
      consecutiveFailures++
      console.warn("Poll failed:", err instanceof Error ? err.message : err)

      // Reschedule with backoff
      clearInterval(pollInterval)
      pollInterval = setInterval(poll, getPollInterval())
    }
  }

  // Start polling immediately
  poll()
  pollInterval = setInterval(poll, POLL_INTERVAL_MS)

  // Optionally attempt SSE — if it connects, reduce polling to fallback frequency
  const trySSE = () => {
    const eventSource = new EventSource(`/api/render-jobs/${jobId}/events`)
    eventSourceRef.current = eventSource

    eventSource.onopen = () => {
      sseConnected = true
      clearInterval(pollInterval)
      // SSE is working — poll less frequently as fallback
      pollInterval = setInterval(poll, SSE_FALLBACK_POLL_INTERVAL_MS)
    }

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as RenderProgressData
        setProgress(data)

        checkStaleProgress(data, lastElapsedRef, lastChangeTimeRef, setStaleWarning)

        // Terminal states
        if (data.status === "completed") {
          eventSource.close()
          eventSourceRef.current = null
          onCompleteRef.current()
          clearInterval(pollInterval)
        } else if (data.status === "failed") {
          eventSource.close()
          eventSourceRef.current = null
          const errMsg = data.errorMessage || "Render failed"
          setError(errMsg)
          onErrorRef.current(errMsg)
          clearInterval(pollInterval)
        } else if (data.status === "cancelled") {
          eventSource.close()
          eventSourceRef.current = null
          onCancelRef.current()
          clearInterval(pollInterval)
        }
      } catch (err) {
        console.warn("Failed to parse SSE data:", err)
      }
    }

    eventSource.onerror = () => {
      console.warn(
        `SSE connection dropped (readyState=${eventSource.readyState}). Falling back to polling.`
      )
      eventSource.close()
      eventSourceRef.current = null
      sseConnected = false
      // Restore fast polling
      clearInterval(pollInterval)
      pollInterval = setInterval(poll, POLL_INTERVAL_MS)
    }
  }

  trySSE()

  return () => {
    abortController.abort()
    clearInterval(pollInterval)
    if (eventSourceRef.current) {
      eventSourceRef.current.close()
      eventSourceRef.current = null
    }
  }
}, [jobId])
```

### Remove

- The separate "Fetch initial status" `useEffect` (lines 184-206) — polling handles this
- The `maxRetries` / `retryCount` / `retryTimeout` retry logic — polling replaces it
- The "Lost connection to render server" error state — polling makes it unnecessary
- `console.error("SSE error:", err)` — replaced with `console.warn` with useful info

### Keep

- Stale progress detection (10 min threshold) — now in `checkStaleProgress()` helper
- Cancel button and cancel logic (close SSE + DELETE request)
- All rendering/UI logic unchanged

---

## Phase 2: Improve SSE error logging

Replace:

```typescript
eventSource.onerror = (err) => {
  console.error("SSE error:", err)
```

With:

```typescript
eventSource.onerror = () => {
  console.warn(
    `SSE connection dropped (readyState=${eventSource.readyState}). Falling back to polling.`
  )
```

This provides actionable info (`readyState` tells us if it was a clean close vs network error) and uses `warn` instead of `error` since it's a recoverable condition.

> Note: This is included in the Phase 1 implementation above. Listed separately for clarity.

---

## Phase 3: SSE events route — Add documentation comment

The SSE events route (`/api/render-jobs/[id]/events/route.ts`) should be kept as-is. It works well for local development and for short-running renders. The polling fallback handles the Vercel timeout gracefully.

**Add a code comment** at the top of the route:

```typescript
// NOTE: This SSE route has maxDuration: 60 on Vercel (serverless function timeout).
// For renders that take longer than ~60s, the SSE connection will drop.
// The client (RenderProgress.tsx) uses REST polling as the primary transport
// and treats SSE as an optional enhancement. Do NOT rely on SSE for critical
// progress updates on Vercel deployments.
```

---

## Phase 4: `vercel.json` — Decide on `maxDuration` for SSE route

Since the SSE route will always timeout for real renders, setting `maxDuration: 60` wastes Vercel function execution time. Options:

| Option | `maxDuration` | Pros | Cons |
|--------|--------------|------|------|
| A: Keep 60s | 60 | SSE works for first ~60s (sub-second updates before polling fallback) | Wastes ~60s of serverless function time per render |
| B: Default (10s) | (remove override) | Less wasted function time | SSE drops almost immediately; no benefit |
| C: Remove SSE route entirely | N/A | Simplest; no wasted function time | Lose SSE benefit for local dev and short renders |

**Recommendation:** Keep `maxDuration: 60` (Option A). The first 60s of SSE provides a better UX (sub-second updates vs 2s polling), and the function cost is minimal. The polling fallback ensures reliability.

---

## Phase 5: Update tests

### `src/test/components/render/RenderProgress.test.tsx`

- Replace `MockEventSource` tests with polling tests
- Test that polling starts immediately on mount
- Test that SSE connection is attempted but polling continues if SSE fails
- Test that SSE reduces polling frequency when connected
- Test that SSE drop restores fast polling
- Test terminal states from polling responses
- Test stale progress detection still works (via `checkStaleProgress` helper)
- Test cancel still closes SSE + sends DELETE
- Test poll failure backoff: consecutive failures increase interval, success resets
- Test AbortController is aborted on unmount (no state updates after unmount)
- Remove "Lost connection" error test (no longer applicable)

### `src/test/api/render-jobs/events.test.ts`

- Keep as-is — the SSE route still exists and should still be tested
- Add a comment noting the Vercel timeout limitation

### `src/test/deployment/deployment.test.ts`

- Keep `maxDuration: 60` assertion for SSE events route (if keeping Phase 4 Option A)

---

## Files Changed Summary

| File | Action |
|------|--------|
| `src/components/render/RenderProgress.tsx` | Replace SSE-first with polling-first; add SSE as optional enhancement; improve error logging; remove "Lost connection" error state; remove separate initial fetch useEffect; add poll failure backoff; use refs for callbacks; use `ReturnType<typeof setInterval>`; extract `checkStaleProgress()` helper; add `AbortController` for cleanup |
| `src/app/api/render-jobs/[id]/events/route.ts` | Add code comment about Vercel timeout limitation |
| `vercel.json` | No change (keep `maxDuration: 60` for SSE route) |
| `src/test/components/render/RenderProgress.test.tsx` | Rewrite tests for polling-first approach; add backoff and abort tests |
| `src/test/api/render-jobs/events.test.ts` | Add comment about Vercel limitation |
| `src/test/deployment/deployment.test.ts` | No change |

---

## Implementation Order

1. Phase 1: Rewrite `RenderProgress.tsx` — polling-first with SSE enhancement (includes backoff, refs, AbortController, helper extraction)
2. Phase 2: Improve SSE error logging (included in Phase 1)
3. Phase 3: Add code comment to SSE events route
4. Phase 4: Decide on `maxDuration` for SSE route (recommendation: keep 60)
5. Phase 5: Update tests
6. Run full test suite (`pnpm test`), lint (`pnpm lint`), typecheck
7. Push
