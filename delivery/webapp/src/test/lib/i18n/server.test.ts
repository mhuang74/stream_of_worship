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
    vi.mocked(headers).mockResolvedValue({} as any);
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
