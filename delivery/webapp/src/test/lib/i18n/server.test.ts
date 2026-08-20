import { describe, it, expect, vi, beforeEach } from "vitest";
import { cookies, headers } from "next/headers";
import { auth } from "@/lib/auth";
import { resolveUserLocale } from "@/lib/i18n/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("next/headers", () => ({
  cookies: vi.fn(),
  headers: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

const mockSelect = vi.fn();
const mockFrom = vi.fn();
const mockWhere = vi.fn();

vi.mock("@/db", () => ({
  db: {
    select: (...args: unknown[]) => mockSelect(...args),
  },
}));

function mockCookie(value: string | undefined) {
  vi.mocked(cookies).mockResolvedValue({
    get: () => (value === undefined ? undefined : { value }),
  } as any);
}

function mockSelectResult(rows: { locale: unknown }[]) {
  mockSelect.mockReturnValue({ from: mockFrom });
  mockFrom.mockReturnValue({ where: mockWhere });
  mockWhere.mockResolvedValue(rows as any);
}

describe("resolveUserLocale", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(headers).mockResolvedValue({
      get: () => null,
    } as any);
  });

  it("defaults to en on public pages when no cookie and no session", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    mockCookie(undefined);
    await expect(resolveUserLocale()).resolves.toBe("en");
  });

  it("uses the sow_locale cookie for unauthenticated public pages", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    mockCookie("zh-Hant");
    await expect(resolveUserLocale()).resolves.toBe("zh-Hant");
  });

  it("ignores an invalid cookie value for public pages", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    mockCookie("fr");
    await expect(resolveUserLocale()).resolves.toBe("en");
  });

  it("keeps the authenticated account setting authoritative over the cookie", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 42 } } as any);
    mockCookie("zh-Hant");
    mockSelectResult([{ locale: "en" }]);
    await expect(resolveUserLocale()).resolves.toBe("en");
  });

  it("falls back to the cookie when the account setting is invalid", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 42 } } as any);
    mockCookie("zh-Hant");
    mockSelectResult([{ locale: "fr" }]);
    await expect(resolveUserLocale()).resolves.toBe("zh-Hant");
  });

  it("falls back to the cookie on error in the DB path", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 42 } } as any);
    mockCookie("zh-Hant");
    mockSelect.mockReturnValue({ from: mockFrom });
    mockFrom.mockReturnValue({ where: mockWhere });
    mockWhere.mockRejectedValue(new Error("DB error"));
    await expect(resolveUserLocale()).resolves.toBe("zh-Hant");
  });
});

describe("resolveUserLocale Accept-Language fallback (read-only)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("detects zh-Hant from Accept-Language 'zh-TW' when no cookie and no session", async () => {
    mockCookie(undefined);
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    vi.mocked(headers).mockResolvedValue({
      get: (name: string) => (name === "accept-language" ? "zh-TW,en-US;q=0.9" : null),
    } as any);
    await expect(resolveUserLocale()).resolves.toBe("zh-Hant");
  });

  it("detects zh-Hant from Accept-Language 'zh' when no cookie and no session", async () => {
    mockCookie(undefined);
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    vi.mocked(headers).mockResolvedValue({
      get: (name: string) => (name === "accept-language" ? "zh,en;q=0.9" : null),
    } as any);
    await expect(resolveUserLocale()).resolves.toBe("zh-Hant");
  });

  it("falls back to en for unknown language tags", async () => {
    mockCookie(undefined);
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    vi.mocked(headers).mockResolvedValue({
      get: (name: string) => (name === "accept-language" ? "fr-FR,de;q=0.8" : null),
    } as any);
    await expect(resolveUserLocale()).resolves.toBe("en");
  });

  it("prefers cookie over Accept-Language", async () => {
    mockCookie("zh-Hant");
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    vi.mocked(headers).mockResolvedValue({
      get: (name: string) => (name === "accept-language" ? "en-US" : null),
    } as any);
    await expect(resolveUserLocale()).resolves.toBe("zh-Hant");
  });

  it("uses Accept-Language as the authenticated user's fallback when no DB locale and no cookie", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 42 } } as any);
    mockCookie(undefined);
    mockSelectResult([{ locale: "fr" }]); // invalid DB value
    vi.mocked(headers).mockResolvedValue({
      get: (name: string) => (name === "accept-language" ? "zh-HK" : null),
    } as any);
    await expect(resolveUserLocale()).resolves.toBe("zh-Hant");
  });

  it("NEVER calls cookies().set() (read-only Server Component resolver)", async () => {
    const cookieStore = { get: () => undefined, set: vi.fn() } as any;
    vi.mocked(cookies).mockResolvedValue(cookieStore);
    vi.mocked(auth.api.getSession).mockResolvedValue(null as any);
    vi.mocked(headers).mockResolvedValue({
      get: (name: string) => (name === "accept-language" ? "zh-TW" : null),
    } as any);
    await resolveUserLocale();
    expect(cookieStore.set).not.toHaveBeenCalled();
  });
});
