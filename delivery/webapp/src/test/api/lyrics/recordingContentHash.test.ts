import { describe, it, expect, beforeEach, vi } from "vitest";
import { GET } from "@/app/api/lyrics/[recordingContentHash]/route";
import { auth } from "@/lib/auth";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

const mockQueryUserLrcOverrides = vi.fn();
const mockSelect = vi.fn();

vi.mock("@/db", () => ({
  db: {
    query: {
      userLrcOverrides: { findFirst: (...args: unknown[]) => mockQueryUserLrcOverrides(...args) },
    },
    select: (...args: unknown[]) => mockSelect(...args),
  },
}));

const mockCreateR2ClientFromEnv = vi.fn();
vi.mock("@/lib/r2/client", () => ({
  createR2ClientFromEnv: (...args: unknown[]) => mockCreateR2ClientFromEnv(...args),
}));

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

const sessionUser = { user: { id: 42 } };
const sampleLrc = "[00:01.00]Hello world\n[00:05.00]Second line";

function makeRequest(contentHash: string): NextRequest {
  const url = `http://localhost/api/lyrics/${contentHash}`;
  const request = new Request(url, { method: "GET" }) as unknown as NextRequest;
  const urlObj = new URL(url);
  Object.defineProperty(request, "nextUrl", { value: urlObj, writable: false });
  return request;
}

function selectChain(rows: unknown[]) {
  return {
    from: vi.fn().mockReturnValue({
      where: vi.fn().mockReturnValue({
        limit: vi.fn().mockResolvedValue(rows),
      }),
    }),
  };
}

function mockParams(contentHash: string) {
  return { params: Promise.resolve({ recordingContentHash: contentHash }) };
}

describe("GET /api/lyrics/[recordingContentHash]", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch.mockReset();
  });

  it("(g) returns 401 without session", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await GET(makeRequest("hash123"), mockParams("hash123"));
    expect(res.status).toBe(401);
  });

  it("(a) override path returns lrcContent", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockQueryUserLrcOverrides.mockResolvedValue({ lrcContent: sampleLrc });

    const res = await GET(makeRequest("hash123"), mockParams("hash123"));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.lrcContent).toBe(sampleLrc);
    expect(data.lines).toBeNull();
    expect(mockSelect).not.toHaveBeenCalled();
  });

  it("(b) lrcStatus='missing' skips R2 and returns lines from lyrics_lines", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockQueryUserLrcOverrides.mockResolvedValue(undefined);

    mockSelect
      .mockReturnValueOnce(selectChain([
        { hashPrefix: "ab", lrcStatus: "missing", songId: "song-1" },
      ]))
      .mockReturnValueOnce(selectChain([
        { lyricsLines: JSON.stringify(["line1", "line2"]), lyricsRaw: null },
      ]));

    const res = await GET(makeRequest("hash123"), mockParams("hash123"));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.lines).toEqual(["line1", "line2"]);
    expect(data.lrcContent).toBeNull();
    expect(mockCreateR2ClientFromEnv).not.toHaveBeenCalled();
  });

  it("(c) R2 200 returns lrcContent", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockQueryUserLrcOverrides.mockResolvedValue(undefined);

    mockSelect.mockReturnValueOnce(selectChain([
      { hashPrefix: "ab", lrcStatus: "ready", songId: "song-1" },
    ]));

    mockCreateR2ClientFromEnv.mockReturnValue({
      getLrcSignedUrl: vi.fn().mockResolvedValue({ url: "https://r2.example.com/lyrics.lrc" }),
    });
    mockFetch.mockResolvedValue({
      ok: true,
      text: vi.fn().mockResolvedValue(sampleLrc),
    });

    const res = await GET(makeRequest("hash123"), mockParams("hash123"));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.lrcContent).toBe(sampleLrc);
    expect(data.lines).toBeNull();
  });

  it("(d) R2 404 falls through to lyrics_lines", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockQueryUserLrcOverrides.mockResolvedValue(undefined);

    mockSelect
      .mockReturnValueOnce(selectChain([
        { hashPrefix: "ab", lrcStatus: "ready", songId: "song-1" },
      ]))
      .mockReturnValueOnce(selectChain([
        { lyricsLines: JSON.stringify(["fallback line"]), lyricsRaw: null },
      ]));

    mockCreateR2ClientFromEnv.mockReturnValue({
      getLrcSignedUrl: vi.fn().mockResolvedValue({ url: "https://r2.example.com/lyrics.lrc" }),
    });
    mockFetch.mockResolvedValue({ ok: false, status: 404 });

    const res = await GET(makeRequest("hash123"), mockParams("hash123"));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.lines).toEqual(["fallback line"]);
    expect(data.lrcContent).toBeNull();
  });

  it("(e) lyrics_lines invalid JSON falls through to lyrics_raw returning lrcContent as plain text", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockQueryUserLrcOverrides.mockResolvedValue(undefined);

    mockSelect
      .mockReturnValueOnce(selectChain([
        { hashPrefix: "ab", lrcStatus: "missing", songId: "song-1" },
      ]))
      .mockReturnValueOnce(selectChain([
        { lyricsLines: "not valid json {{{", lyricsRaw: "plain text lyrics" },
      ]));

    const res = await GET(makeRequest("hash123"), mockParams("hash123"));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.lrcContent).toBe("plain text lyrics");
    expect(data.lines).toBeNull();
  });

  it("(f) all-null returns { lrcContent: null, lines: null }", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockQueryUserLrcOverrides.mockResolvedValue(undefined);

    mockSelect
      .mockReturnValueOnce(selectChain([
        { hashPrefix: "ab", lrcStatus: "missing", songId: "song-1" },
      ]))
      .mockReturnValueOnce(selectChain([
        { lyricsLines: null, lyricsRaw: null },
      ]));

    const res = await GET(makeRequest("hash123"), mockParams("hash123"));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.lrcContent).toBeNull();
    expect(data.lines).toBeNull();
  });

  it("(h) lyrics_lines empty array falls through to lyrics_raw", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockQueryUserLrcOverrides.mockResolvedValue(undefined);

    mockSelect
      .mockReturnValueOnce(selectChain([
        { hashPrefix: "ab", lrcStatus: "missing", songId: "song-1" },
      ]))
      .mockReturnValueOnce(selectChain([
        { lyricsLines: JSON.stringify([]), lyricsRaw: "raw lyrics text" },
      ]));

    const res = await GET(makeRequest("hash123"), mockParams("hash123"));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.lrcContent).toBe("raw lyrics text");
    expect(data.lines).toBeNull();
  });

  it("R2 network error falls through to DB silently", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockQueryUserLrcOverrides.mockResolvedValue(undefined);

    mockSelect
      .mockReturnValueOnce(selectChain([
        { hashPrefix: "ab", lrcStatus: "ready", songId: "song-1" },
      ]))
      .mockReturnValueOnce(selectChain([
        { lyricsLines: null, lyricsRaw: "db fallback lyrics" },
      ]));

    mockCreateR2ClientFromEnv.mockReturnValue({
      getLrcSignedUrl: vi.fn().mockResolvedValue({ url: "https://r2.example.com/lyrics.lrc" }),
    });
    mockFetch.mockRejectedValue(new Error("Network error"));

    const res = await GET(makeRequest("hash123"), mockParams("hash123"));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.lrcContent).toBe("db fallback lyrics");
  });

  it("recording not found returns nulls", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockQueryUserLrcOverrides.mockResolvedValue(undefined);

    mockSelect.mockReturnValueOnce(selectChain([]));

    const res = await GET(makeRequest("hash123"), mockParams("hash123"));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.lrcContent).toBeNull();
    expect(data.lines).toBeNull();
  });
});
