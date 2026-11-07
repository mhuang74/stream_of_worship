import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "@/app/api/admin/lyrics-review/sets/route";

const mocks = vi.hoisted(() => ({ session: vi.fn(), sets: vi.fn() }));
vi.mock("@/lib/auth", () => ({ auth: { api: { getSession: mocks.session } } }));
vi.mock("@/lib/lyrics/review", () => ({ reviewSets: mocks.sets }));
const owner = { id: "42", email: "mhuang74@yahoo.com", emailVerified: true };
const request = () => new NextRequest("https://localhost/api/admin/lyrics-review/sets");

beforeEach(() => {
  vi.resetAllMocks();
  mocks.session.mockResolvedValue({ user: owner });
  mocks.sets.mockResolvedValue([]);
});

describe("review set access", () => {
  it("denies unauthenticated and non-admin requests before reading sets", async () => {
    mocks.session.mockResolvedValueOnce(null);
    expect((await GET(request())).status).toBe(401);
    mocks.session.mockResolvedValueOnce({ user: { ...owner, email: "other@example.com" } });
    expect((await GET(request())).status).toBe(403);
    mocks.session.mockResolvedValueOnce({ user: { ...owner, emailVerified: false } });
    expect((await GET(request())).status).toBe(403);
    expect(mocks.sets).not.toHaveBeenCalled();
  });
});

it("returns every set, complete ones included, in a private uncached response", async () => {
  const sets = [
    { id: "set-1", name: "march-batch", total: 3, done: 1 },
    { id: "set-2", name: "finished", total: 2, done: 2 },
  ];
  mocks.sets.mockResolvedValue(sets);
  const response = await GET(request());
  expect(response.status).toBe(200);
  expect(response.headers.get("cache-control")).toBe("private, no-store");
  expect(await response.json()).toEqual({ sets });
});

it("reports a load failure instead of an empty list", async () => {
  mocks.sets.mockRejectedValue(new Error("boom"));
  const response = await GET(request());
  expect(response.status).toBe(503);
  expect(await response.json()).toEqual({ error: "Unable to load review sets. Please retry." });
});
