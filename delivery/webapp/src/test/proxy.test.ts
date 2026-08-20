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
