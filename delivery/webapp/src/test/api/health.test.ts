import { describe, it, expect } from "vitest";
import { HEAD, GET } from "@/app/api/health/route";

// The reachability probe contract (issue #211): unauthenticated, no body,
// 204, never cached. Any other status or header combination would corrupt
// the client's Connectivity state machine (src/hooks/useConnectivity.ts),
// which treats "not 204" as unreachable.
describe("GET/HEAD /api/health", () => {
  it("answers HEAD with 204 and no-store", async () => {
    const res = await HEAD();
    expect(res.status).toBe(204);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
  });

  it("answers GET with the same 204/no-store contract", async () => {
    const res = await GET();
    expect(res.status).toBe(204);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
  });
});
