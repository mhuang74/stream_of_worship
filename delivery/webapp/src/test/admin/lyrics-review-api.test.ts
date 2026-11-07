import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET, PUT, DELETE } from "@/app/api/admin/lyrics-review/route";
import { canReviewLyrics } from "@/lib/lyrics/review-access";

const mocks = vi.hoisted(() => ({
  session: vi.fn(), queue: vi.fn(), recording: vi.fn(), lyrics: vi.fn(),
  find: vi.fn(), insert: vi.fn(), values: vi.fn(), upsert: vi.fn(),
  remove: vi.fn(), where: vi.fn(), audio: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({ auth: { api: { getSession: mocks.session } } }));
vi.mock("@/lib/lyrics/review", () => ({ reviewQueue: mocks.queue, reviewRecording: mocks.recording, canonicalReviewLyrics: mocks.lyrics }));
vi.mock("@/lib/r2/client", () => ({ createR2ClientFromEnv: () => ({ getAudioSignedUrl: mocks.audio }) }));
vi.mock("@/db", () => ({ db: { query: { lyricsFeedback: { findFirst: mocks.find } }, insert: mocks.insert, delete: mocks.remove } }));
const owner = { id: "42", email: "mhuang74@yahoo.com", emailVerified: true };
function request(method = "GET", body?: unknown, search = "?recording=hash") {
  return new NextRequest(`https://localhost/api/admin/lyrics-review${search}`, {
    method, headers: { origin: "https://localhost", "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
}
beforeEach(() => {
  vi.resetAllMocks();
  mocks.session.mockResolvedValue({ user: owner });
  mocks.recording.mockResolvedValue({ contentHash: "hash", hashPrefix: "prefix" });
  mocks.lyrics.mockResolvedValue({ synced: true, lines: [{ timeSeconds: 1, text: "詞" }] });
  mocks.audio.mockResolvedValue({ url: "https://audio" });
  mocks.find.mockResolvedValue(null);
  mocks.queue.mockResolvedValue({ recordings: [], hasMore: false });
  mocks.insert.mockReturnValue({ values: mocks.values });
  mocks.values.mockReturnValue({ onConflictDoUpdate: mocks.upsert });
  mocks.remove.mockReturnValue({ where: mocks.where });
});

describe("review access", () => {
  it("requires the verified, allowlisted account", () => {
    expect(canReviewLyrics(owner)).toBe(true);
    expect(canReviewLyrics({ ...owner, email: "Tester@StreamOfWorship.com" })).toBe(true);
    expect(canReviewLyrics({ ...owner, emailVerified: false })).toBe(false);
    expect(canReviewLyrics({ ...owner, email: "someone@example.com" })).toBe(false);
    expect(canReviewLyrics(null)).toBe(false);
  });
  for (const method of [GET, PUT, DELETE]) {
    it(`denies unauthenticated and non-admin ${method.name} requests before data access`, async () => {
      mocks.session.mockResolvedValueOnce(null);
      expect((await method(request())).status).toBe(401);
      mocks.session.mockResolvedValueOnce({ user: { ...owner, email: "other@example.com" } });
      expect((await method(request())).status).toBe(403);
      mocks.session.mockResolvedValueOnce({ user: { ...owner, emailVerified: false } });
      expect((await method(request())).status).toBe(403);
      expect(mocks.recording).not.toHaveBeenCalled();
      expect(mocks.queue).not.toHaveBeenCalled();
    });
  }
  it("rejects cross-origin writes", async () => {
    const req = request("PUT", { rating: "happy" });
    req.headers.set("origin", "https://other.example");
    expect((await PUT(req)).status).toBe(403);
    expect(mocks.insert).not.toHaveBeenCalled();
  });
  it("cannot open or rate a non-Review recording", async () => {
    mocks.recording.mockResolvedValue(undefined);
    expect((await GET(request())).status).toBe(404);
    expect((await PUT(request("PUT", { rating: "happy" }))).status).toBe(404);
    expect(mocks.insert).not.toHaveBeenCalled();
  });
});

it("fetches canonical lyrics and returns private, uncached responses", async () => {
  const response = await GET(request());
  expect(response.status).toBe(200);
  expect(response.headers.get("cache-control")).toBe("private, no-store");
  expect((await response.json()).synced).toBe(true);
  expect(mocks.lyrics).toHaveBeenCalledWith("prefix");
});
it.each([
  { rating: "happy", reason: null },
  { rating: "sad", reason: "timing" },
])("returns the reviewer's unresolved $rating feedback and hides it once resolved", async ({ rating, reason }) => {
  const feedback = { rating, reason, note: null };
  mocks.find.mockResolvedValueOnce({ ...feedback, resolvedAt: null });
  expect(await (await GET(request())).json()).toMatchObject({ feedback });
  mocks.find.mockResolvedValueOnce({ ...feedback, resolvedAt: new Date() });
  expect(await (await GET(request())).json()).toMatchObject({ feedback: null });
});
it("forwards search and reviewed filters; rejects bad pagination", async () => {
  await GET(request("GET", undefined, "?q=grace&reviewed=1&offset=50"));
  expect(mocks.queue).toHaveBeenCalledWith("grace", true, 50);
  expect((await GET(request("GET", undefined, "?offset=-1"))).status).toBe(400);
});
it("includes all queue feedback in the private list response", async () => {
  const queue = { recordings: [{ contentHash: "hash", title: "Song", album: null, reviewed: true, feedback: [
    { id: "poor", rating: "sad", reason: "wrong_text", note: "Second verse" },
    { id: "good", rating: "happy", reason: null, note: null },
  ] }], hasMore: false };
  mocks.queue.mockResolvedValue(queue);
  const response = await GET(request("GET", undefined, "?reviewed=1"));
  expect(response.status).toBe(200);
  expect(response.headers.get("cache-control")).toBe("private, no-store");
  expect(await response.json()).toEqual(queue);
});
it("rejects Good when canonical timed lyrics are absent", async () => {
  mocks.lyrics.mockResolvedValue({ synced: false, lines: [] });
  expect((await PUT(request("PUT", { rating: "happy" }))).status).toBe(400);
  expect(mocks.insert).not.toHaveBeenCalled();
});
it("distinguishes load failure from missing; does not write feedback on failure", async () => {
  mocks.lyrics.mockRejectedValue(new Error("network unavailable"));
  expect((await PUT(request("PUT", { rating: "sad", reason: "missing" }))).status).toBe(503);
  expect(mocks.insert).not.toHaveBeenCalled();
});
it("saves Poor with a trimmed note and reopens resolved feedback", async () => {
  const response = await PUT(request("PUT", { rating: "sad", reason: "timing", note: "  late  " }));
  expect(response.status).toBe(200);
  expect(mocks.values).toHaveBeenCalledWith(expect.objectContaining({ userId: 42, recordingContentHash: "hash", note: "late" }));
  expect(mocks.upsert).toHaveBeenCalledWith(expect.objectContaining({ set: expect.objectContaining({ resolvedAt: null }) }));
});
it("rejects invalid Poor reasons and supports missing feedback without timed lyrics", async () => {
  expect((await PUT(request("PUT", { rating: "sad", reason: "invalid" }))).status).toBe(400);
  mocks.lyrics.mockResolvedValue({ synced: false, lines: [] });
  expect((await PUT(request("PUT", { rating: "sad", reason: "missing" }))).status).toBe(200);
});
it("retracts only personal feedback without fetching lyrics", async () => {
  expect((await DELETE(request("DELETE"))).status).toBe(200);
  expect(mocks.where).toHaveBeenCalled();
  expect(mocks.lyrics).not.toHaveBeenCalled();
});
