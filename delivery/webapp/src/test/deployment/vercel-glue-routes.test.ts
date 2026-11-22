import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

// Deployment assertions for the Glue routes (spec §6.3): every route that can
// hold a request open long enough needs the 60 s function budget, and the
// config must keep the shape Vercel expects.

interface VercelConfig {
  functions?: Record<string, { maxDuration?: number }>;
  headers?: unknown;
}

function readConfig(): VercelConfig {
  const file = path.resolve(__dirname, "../../../vercel.json");
  return JSON.parse(readFileSync(file, "utf8")) as VercelConfig;
}

const GLUE_ROUTES = [
  "src/app/api/songsets/[id]/glue-jobs/route.ts",
  "src/app/api/glue-jobs/[id]/route.ts",
  "src/app/api/glue-jobs/[id]/events/route.ts",
  "src/app/api/songsets/[id]/glue-quote/route.ts",
];

describe("vercel.json Glue functions", () => {
  it("budgets 60 s for every Glue route that waits on a job", () => {
    const config = readConfig();
    for (const route of GLUE_ROUTES) {
      expect(config.functions?.[route]).toBeDefined();
      expect(config.functions?.[route].maxDuration).toBe(60);
    }
  });

  it("keeps the existing render route budgets unchanged", () => {
    const config = readConfig();
    expect(config.functions?.["src/app/api/render-jobs/route.ts"]?.maxDuration).toBe(
      60
    );
    expect(
      config.functions?.["src/app/api/render-jobs/[id]/route.ts"]?.maxDuration
    ).toBe(60);
  });

  it("declares Glue SSE as a function with a matching route file", () => {
    const config = readConfig();
    const events = "src/app/api/glue-jobs/[id]/events/route.ts";
    expect(config.functions?.[events]?.maxDuration).toBe(60);
    expect(() =>
      readFileSync(path.resolve(__dirname, "../../../", events), "utf8")
    ).not.toThrow();
  });
});
