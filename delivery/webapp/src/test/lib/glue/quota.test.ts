import { describe, it, expect, vi } from "vitest";

vi.mock("@/db", () => ({ db: {} }));

import { buildGlueQuota } from "@/lib/glue/quota";

const NOW = new Date("2026-11-22T12:00:00.000Z");
const HOUR = 60 * 60 * 1000;

function hoursAgo(hours: number): Date {
  return new Date(NOW.getTime() - hours * HOUR);
}

describe("buildGlueQuota", () => {
  it("reports remaining slots and no reset time below the limit", () => {
    const quota = buildGlueQuota([hoursAgo(1), hoursAgo(2), hoursAgo(3)], NOW);
    expect(quota).toEqual({ limit: 5, used: 3, remaining: 2, resetAt: null });
  });

  it("returns the oldest consumption expiry only when the window is full", () => {
    const quota = buildGlueQuota(
      [hoursAgo(2), hoursAgo(3), hoursAgo(4), hoursAgo(5), hoursAgo(6)],
      NOW
    );
    expect(quota.used).toBe(5);
    expect(quota.remaining).toBe(0);
    expect(quota.resetAt).toBe("2026-11-23T06:00:00.000Z");
  });

  it("ignores attempts older than 24 hours", () => {
    const quota = buildGlueQuota(
      [hoursAgo(25), hoursAgo(26), hoursAgo(1), hoursAgo(2)],
      NOW
    );
    expect(quota).toEqual({ limit: 5, used: 2, remaining: 3, resetAt: null });
  });

  it("reports zero remaining (never negative) when the ledger is over the limit", () => {
    const quota = buildGlueQuota(
      [hoursAgo(1), hoursAgo(2), hoursAgo(3), hoursAgo(4), hoursAgo(5), hoursAgo(6)],
      NOW
    );
    expect(quota.used).toBe(6);
    expect(quota.remaining).toBe(0);
    expect(quota.resetAt).toBe("2026-11-23T06:00:00.000Z");
  });
});
