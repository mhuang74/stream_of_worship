import { NextResponse } from "next/server";

/**
 * Unauthenticated reachability probe (issue #211).
 *
 * The client's Connectivity state (src/hooks/useConnectivity.ts) decides the
 * app's offline behavior by probing this endpoint — never `/api/songsets` or
 * any session-bearing route — so a stale session can never produce a false
 * "Offline". Touches no database and no auth: a 204 is the only contract.
 *
 * `HEAD` is what the reachability probe sends. `GET` answers identically so
 * the endpoint is verifiable from a browser address bar. `Cache-Control:
 * no-store` keeps intermediaries from caching the answer, and the service
 * worker registers this path NetworkOnly (public/sw.js).
 */
export async function HEAD() {
  return new NextResponse(null, {
    status: 204,
    headers: { "Cache-Control": "no-store" },
  });
}

export async function GET() {
  return new NextResponse(null, {
    status: 204,
    headers: { "Cache-Control": "no-store" },
  });
}
