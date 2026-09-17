import { describe, it, expect, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";
import { auth } from "@/lib/auth";
import { proxy } from "@/proxy";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

function req(
  url: string,
  opts: { cookie?: string; acceptLanguage?: string; sessionCookie?: boolean } = {}
) {
  const headers = new Headers();
  if (opts.acceptLanguage) headers.set("accept-language", opts.acceptLanguage);
  const request = new NextRequest(new URL(url, "http://localhost:3000"), { headers });
  if (opts.cookie) request.cookies.set("sow_locale", opts.cookie);
  if (opts.sessionCookie) request.cookies.set("better-auth.session_token", "test");
  return request;
}

describe("proxy locale cookie", () => {
  beforeEach(() => vi.clearAllMocks());

  it("sets sow_locale=zh-Hant from Accept-Language on a public first visit (no cookie, no session)", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    const res = await proxy(req("/", { acceptLanguage: "zh-TW,en-US;q=0.9" }));
    expect(res.cookies.get("sow_locale")?.value).toBe("zh-Hant");
  });

  it("does not set sow_locale when a valid sow_locale cookie already exists", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    const res = await proxy(req("/", { cookie: "zh-Hant", acceptLanguage: "en-US" }));
    expect(res.cookies.get("sow_locale")).toBeUndefined();
  });

  it("does not set sow_locale when a session cookie is present (authenticated user)", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    const res = await proxy(req("/songsets", { sessionCookie: true, acceptLanguage: "zh-TW" }));
    expect(res.cookies.get("sow_locale")).toBeUndefined();
  });

  it("sets sow_locale on the redirect response for an unauthenticated non-public path", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    const res = await proxy(req("/songsets", { acceptLanguage: "zh-HK" }));
    expect(res.status).toBe(307);
    expect(res.cookies.get("sow_locale")?.value).toBe("zh-Hant");
  });

  it("defaults to en when Accept-Language is absent or unrecognized", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    const res = await proxy(req("/"));
    expect(res.cookies.get("sow_locale")?.value).toBe("en");
  });
});

describe("public path rules", () => {
  beforeEach(() => vi.clearAllMocks());

  // The SW scripts are fetched by register()/importScripts outside the page
  // navigation: a 307 to /login makes them fail with "script resource is
  // behind a redirect" and the worker never installs. A new importScripts()
  // target belongs in PUBLIC_PATHS too.
  it.each(["/sw.js", "/sw-artifact-serving.js", "/sw-artifact-serving.js?v=abc123"])(
    "does not redirect the unauthenticated service worker script %s",
    async (path) => {
      vi.mocked(auth.api.getSession).mockResolvedValue(null);
      const res = await proxy(req(path));
      expect(res.status).not.toBe(307);
    }
  );

  // The reachability probe (issue #211) must never depend on session state:
  // an unauthenticated /api/health answers 204, not a 307 to /login, so a
  // stale session on the device cannot produce a false "Offline".
  it.each(["/api/health"])(
    "does not redirect the unauthenticated health probe %s",
    async (path) => {
      vi.mocked(auth.api.getSession).mockResolvedValue(null);
      const res = await proxy(req(path));
      expect(res.status).not.toBe(307);
      expect(res.status).not.toBe(401);
    }
  );

  it("still redirects an unauthenticated app path", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await proxy(req("/songsets"));
    expect(res.status).toBe(307);
  });
});
