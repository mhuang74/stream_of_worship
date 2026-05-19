/**
 * Deployment configuration tests for Task 8.4.
 *
 * Verifies that vercel.json and .env.production.example are correctly structured
 * so that production deployments meet the app's runtime requirements:
 *
 * - Render functions have maxDuration: 60 (jobs are enqueued to SQS, not run inline).
 * - Fluid Compute is no longer required on render routes (Lambda worker handles long-running tasks).
 * - Preview deployments must be enabled for all branches.
 * - .env.production.example must document every required production variable.
 * - Cast receiver app ID documentation must be present.
 *
 * External steps (Cast SDK console registration, Vercel Pro plan activation,
 * Google review) cannot be verified automatically — they are marked todo below.
 */

import { describe, it, expect } from "vitest";
import * as fs from "fs";
import * as path from "path";

// Resolve paths relative to the webapp/ root (two levels up from src/test/deployment/)
const WEBAPP_ROOT = path.resolve(__dirname, "../../../");
const VERCEL_JSON_PATH = path.join(WEBAPP_ROOT, "vercel.json");
const ENV_EXAMPLE_PATH = path.join(WEBAPP_ROOT, ".env.production.example");
const README_PATH = path.join(WEBAPP_ROOT, "README.md");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function readVercelJson(): Record<string, unknown> {
  const raw = fs.readFileSync(VERCEL_JSON_PATH, "utf-8");
  return JSON.parse(raw) as Record<string, unknown>;
}

function readEnvExample(): string {
  return fs.readFileSync(ENV_EXAMPLE_PATH, "utf-8");
}

function readReadme(): string {
  return fs.readFileSync(README_PATH, "utf-8");
}

// ---------------------------------------------------------------------------
// vercel.json existence and structure
// ---------------------------------------------------------------------------

describe("vercel.json", () => {
  it("exists at webapp/vercel.json", () => {
    expect(fs.existsSync(VERCEL_JSON_PATH)).toBe(true);
  });

  it("specifies nextjs framework", () => {
    const config = readVercelJson();
    expect(config.framework).toBe("nextjs");
  });

  it("specifies pnpm build command", () => {
    const config = readVercelJson();
    expect(config.buildCommand).toContain("pnpm");
    expect(config.buildCommand).toContain("build");
  });

  it("specifies pnpm install command", () => {
    const config = readVercelJson();
    expect(config.installCommand).toContain("pnpm");
    expect(config.installCommand).toContain("install");
  });
});

// ---------------------------------------------------------------------------
// Render function: maxDuration
// ---------------------------------------------------------------------------

describe("vercel.json — render function maxDuration", () => {
  it("has functions configuration", () => {
    const config = readVercelJson();
    expect(config.functions).toBeDefined();
    expect(typeof config.functions).toBe("object");
  });

  it("sets maxDuration: 60 for render-jobs POST route", () => {
    const config = readVercelJson();
    const functions = config.functions as Record<string, { maxDuration?: number; fluid?: boolean }>;
    const renderRoute = functions["src/app/api/render-jobs/route.ts"];
    expect(renderRoute).toBeDefined();
    expect(renderRoute.maxDuration).toBe(60);
  });

  it("sets maxDuration: 60 for render-jobs GET route", () => {
    const config = readVercelJson();
    const functions = config.functions as Record<string, { maxDuration?: number; fluid?: boolean }>;
    const renderRoute = functions["src/app/api/render-jobs/[id]/route.ts"];
    expect(renderRoute).toBeDefined();
    expect(renderRoute.maxDuration).toBe(60);
  });

  it("sets maxDuration: 60 for render-jobs SSE events route", () => {
    const config = readVercelJson();
    const functions = config.functions as Record<string, { maxDuration?: number; fluid?: boolean }>;
    const eventsRoute = functions["src/app/api/render-jobs/[id]/events/route.ts"];
    expect(eventsRoute).toBeDefined();
    expect(eventsRoute.maxDuration).toBe(60);
  });
});

// ---------------------------------------------------------------------------
// Fluid Compute
// ---------------------------------------------------------------------------

describe("vercel.json — Fluid Compute (not required on render routes)", () => {
  it("does not enable fluid compute on render-jobs POST route", () => {
    const config = readVercelJson();
    const functions = config.functions as Record<string, { maxDuration?: number; fluid?: boolean }>;
    const renderRoute = functions["src/app/api/render-jobs/route.ts"];
    expect(renderRoute?.fluid).toBeFalsy();
  });

  it("does not enable fluid compute on render-jobs GET route", () => {
    const config = readVercelJson();
    const functions = config.functions as Record<string, { maxDuration?: number; fluid?: boolean }>;
    const renderRoute = functions["src/app/api/render-jobs/[id]/route.ts"];
    expect(renderRoute?.fluid).toBeFalsy();
  });

  it("does not enable fluid compute on SSE events route", () => {
    const config = readVercelJson();
    const functions = config.functions as Record<string, { maxDuration?: number; fluid?: boolean }>;
    const eventsRoute = functions["src/app/api/render-jobs/[id]/events/route.ts"];
    expect(eventsRoute?.fluid).toBeFalsy();
  });
});

// ---------------------------------------------------------------------------
// Preview deployments
// ---------------------------------------------------------------------------

describe("vercel.json — preview deployments", () => {
  it("has git configuration", () => {
    const config = readVercelJson();
    expect(config.git).toBeDefined();
  });

  it("enables deployments on main branch", () => {
    const config = readVercelJson();
    const git = config.git as { deploymentEnabled?: Record<string, boolean> };
    expect(git.deploymentEnabled?.main).toBe(true);
  });

  it("enables deployments on all branches (preview deployments)", () => {
    const config = readVercelJson();
    const git = config.git as { deploymentEnabled?: Record<string, boolean> };
    // wildcard "*" enables preview deployments for feature branches
    expect(git.deploymentEnabled?.["*"]).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Cache headers (projection pages must not be CDN-cached)
// ---------------------------------------------------------------------------

describe("vercel.json — cache headers", () => {
  it("sets no-store header for songset projection route", () => {
    const config = readVercelJson();
    const headers = config.headers as Array<{ source: string; headers: Array<{ key: string; value: string }> }>;
    const projection = headers?.find((h) => h.source.includes("projection"));
    expect(projection).toBeDefined();
    const cacheControl = projection?.headers.find((h) => h.key === "Cache-Control");
    expect(cacheControl?.value).toContain("no-store");
  });

  it("sets immutable cache header for Next.js static assets", () => {
    const config = readVercelJson();
    const headers = config.headers as Array<{ source: string; headers: Array<{ key: string; value: string }> }>;
    const staticAssets = headers?.find((h) => h.source.includes("_next/static"));
    expect(staticAssets).toBeDefined();
    const cacheControl = staticAssets?.headers.find((h) => h.key === "Cache-Control");
    expect(cacheControl?.value).toContain("immutable");
  });
});

// ---------------------------------------------------------------------------
// .env.production.example existence and content
// ---------------------------------------------------------------------------

describe(".env.production.example", () => {
  it("exists at webapp/.env.production.example", () => {
    expect(fs.existsSync(ENV_EXAMPLE_PATH)).toBe(true);
  });

  it("documents DATABASE_URL", () => {
    const content = readEnvExample();
    expect(content).toContain("DATABASE_URL=");
  });

  it("documents R2_ACCOUNT_ID", () => {
    const content = readEnvExample();
    expect(content).toContain("R2_ACCOUNT_ID=");
  });

  it("documents R2_ACCESS_KEY_ID", () => {
    const content = readEnvExample();
    expect(content).toContain("R2_ACCESS_KEY_ID=");
  });

  it("documents R2_SECRET_ACCESS_KEY", () => {
    const content = readEnvExample();
    expect(content).toContain("R2_SECRET_ACCESS_KEY=");
  });

  it("documents R2_BUCKET_NAME", () => {
    const content = readEnvExample();
    expect(content).toContain("R2_BUCKET_NAME=");
  });

  it("documents R2_PUBLIC_DOMAIN", () => {
    const content = readEnvExample();
    expect(content).toContain("R2_PUBLIC_DOMAIN=");
  });

  it("documents BETTER_AUTH_SECRET", () => {
    const content = readEnvExample();
    expect(content).toContain("BETTER_AUTH_SECRET=");
  });

  it("documents BETTER_AUTH_URL", () => {
    const content = readEnvExample();
    expect(content).toContain("BETTER_AUTH_URL=");
  });

  it("documents NEXT_PUBLIC_CAST_RECEIVER_APP_ID", () => {
    const content = readEnvExample();
    expect(content).toContain("NEXT_PUBLIC_CAST_RECEIVER_APP_ID=");
  });

  it("documents NEXT_PUBLIC_BASE_URL", () => {
    const content = readEnvExample();
    expect(content).toContain("NEXT_PUBLIC_BASE_URL=");
  });
});

// ---------------------------------------------------------------------------
// Cast receiver documentation
// ---------------------------------------------------------------------------

describe(".env.production.example — Cast receiver documentation", () => {
  it("explains how to register in Google Cast SDK Developer Console", () => {
    const content = readEnvExample();
    expect(content).toContain("cast.google.com/publish");
  });

  it("mentions dev/staging/prod receiver app IDs", () => {
    const content = readEnvExample();
    expect(content.toLowerCase()).toContain("staging");
    expect(content.toLowerCase()).toContain("prod");
  });

  it("describes Cast receiver approval process", () => {
    const content = readEnvExample();
    // Should mention the approval/review process
    expect(content.toLowerCase()).toMatch(/approv|review/);
  });
});

// ---------------------------------------------------------------------------
// README deployment documentation
// ---------------------------------------------------------------------------

describe("README.md — deployment documentation", () => {
  it("exists", () => {
    expect(fs.existsSync(README_PATH)).toBe(true);
  });

  it("mentions Vercel Pro requirement", () => {
    const content = readReadme();
    expect(content).toContain("Vercel Pro");
  });

  it("mentions maxDuration: 60 for render routes", () => {
    const content = readReadme();
    expect(content).toContain("60");
  });

  it("mentions Lambda worker for rendering", () => {
    const content = readReadme();
    expect(content.toLowerCase()).toContain("lambda");
  });

  it("documents preview deployments", () => {
    const content = readReadme();
    expect(content.toLowerCase()).toContain("preview");
  });

  it("includes Cast SDK setup instructions", () => {
    const content = readReadme();
    expect(content).toContain("cast.google.com/publish");
  });

  it("documents Cast production approval process", () => {
    const content = readReadme();
    expect(content.toLowerCase()).toMatch(/approv|review/);
  });
});

// ---------------------------------------------------------------------------
// External / manual steps (cannot be automated)
// ---------------------------------------------------------------------------

describe("external setup steps (not automatable)", () => {
  it.todo("Register dev Cast receiver app in Google Cast SDK Developer Console");
  it.todo("Register staging/preview Cast receiver app in Google Cast SDK Developer Console");
  it.todo("Register production Cast receiver app in Google Cast SDK Developer Console");
  it.todo("Submit production Cast receiver app for Google review");
  it.todo("Activate Vercel Pro plan on the project");
  it.todo("Add environment variables in Vercel Dashboard → Project Settings → Environment Variables");
});
