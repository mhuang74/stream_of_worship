import { describe, it, expect, beforeEach, vi } from "vitest";
import { POST } from "@/app/api/embed/route";
import { auth } from "@/lib/auth";
import { generateEmbedding } from "@/lib/embed/client";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: {
    api: {
      getSession: vi.fn(),
    },
  },
}));

vi.mock("@/lib/embed/client", () => ({
  generateEmbedding: vi.fn(),
  EMBEDDING_MODEL_VERSION: "bge-m3",
  EMBEDDING_DIMENSIONS: 1024,
}));

function makeRequest(body: unknown, url = "http://localhost:3000/api/embed"): NextRequest {
  return new Request(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }) as unknown as NextRequest;
}

const mockEmbedding = Array.from({ length: 1024 }, (_, i) => i / 1024);

describe("POST /api/embed", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await POST(makeRequest({ text: "hello" }));
    expect(res.status).toBe(401);
    const data = await res.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("returns 400 for invalid JSON", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    const req = new Request("http://localhost:3000/api/embed", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "not-json",
    }) as unknown as NextRequest;
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  it("returns 400 when text is missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    const res = await POST(makeRequest({}));
    expect(res.status).toBe(400);
  });

  it("returns 400 when text is empty", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    const res = await POST(makeRequest({ text: "" }));
    expect(res.status).toBe(400);
  });

  it("returns 400 when text is too long", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    const res = await POST(makeRequest({ text: "a".repeat(8193) }));
    expect(res.status).toBe(400);
  });

  it("returns embedding on success", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(generateEmbedding).mockResolvedValue(mockEmbedding);

    const res = await POST(makeRequest({ text: "songs about grace" }));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.embedding).toHaveLength(1024);
    expect(data.dimensions).toBe(1024);
    expect(data.model).toBe("bge-m3");
  });

  it("calls generateEmbedding with the provided text", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(generateEmbedding).mockResolvedValue(mockEmbedding);

    await POST(makeRequest({ text: "worship songs in Chinese" }));
    expect(generateEmbedding).toHaveBeenCalledWith("worship songs in Chinese");
  });

  it("returns 500 when generateEmbedding throws", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(generateEmbedding).mockRejectedValue(new Error("Model not loaded"));

    const res = await POST(makeRequest({ text: "songs" }));
    expect(res.status).toBe(500);
    const data = await res.json();
    expect(data.error).toContain("Model not loaded");
  });
});
