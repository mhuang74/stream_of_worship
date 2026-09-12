import { describe, it, expect, beforeEach, vi } from "vitest";
import { resolveLyricsSituation } from "@/lib/lyrics/situation";
import { validateFeedbackSubmission } from "@/lib/lyrics/situation";

// --------------------------------------------------------------------------
// resolveLyricsSituation — mirrors the /api/lyrics/[recordingContentHash]
// resolution order: R2 canonical LRC first (unless lrcStatus === "missing"),
// then scraped unsynced text (lyricsLines JSON array → lyricsRaw), else none.
const mockSelect = vi.fn();

vi.mock("@/db", () => ({
  db: {
    select: (...args: unknown[]) => mockSelect(...args),
  },
}));

const mockCreateR2ClientFromEnv = vi.fn();
vi.mock("@/lib/r2/client", () => ({
  createR2ClientFromEnv: (...args: unknown[]) => mockCreateR2ClientFromEnv(...args),
}));

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

describe("resolveLyricsSituation", () => {
  const sampleLrc = "[00:01.00]Hello world\n[00:05.00]Second line";
  function selectChain(rows: unknown[]) {
    return {
      from: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          limit: vi.fn().mockResolvedValue(rows),
        }),
      }),
    };
  }

  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch.mockReset();
  });

  it("(a) R2 LRC with 2+ timestamped lines → synced", async () => {
    mockSelect.mockReturnValueOnce(
      selectChain([{ hashPrefix: "ab", lrcStatus: "ready", songId: "song-1" }])
    );
    mockCreateR2ClientFromEnv.mockReturnValue({
      getLrcSignedUrl: vi.fn().mockResolvedValue({ url: "https://r2.example.com/x.lrc" }),
    });
    mockFetch.mockResolvedValue({ ok: true, text: vi.fn().mockResolvedValue(sampleLrc) });

    const situation = await resolveLyricsSituation("hash123");
    expect(situation).toEqual({ kind: "synced" });
  });

  it("(b) lrcStatus missing skips R2; lyricsLines JSON → unsynced", async () => {
    mockSelect
      .mockReturnValueOnce(
        selectChain([{ hashPrefix: "ab", lrcStatus: "missing", songId: "song-1" }])
      )
      .mockReturnValueOnce(
        selectChain([{ lyricsLines: JSON.stringify(["line1", "line2"]), lyricsRaw: null }])
      );

    const situation = await resolveLyricsSituation("hash123");
    expect(situation).toEqual({ kind: "unsynced" });
  });

  it("(c) lyricsRaw plain text (no timestamps) → unsynced", async () => {
    mockSelect
      .mockReturnValueOnce(
        selectChain([{ hashPrefix: "ab", lrcStatus: "missing", songId: "song-1" }])
      )
      .mockReturnValueOnce(
        selectChain([{ lyricsLines: null, lyricsRaw: "plain text lyrics" }])
      );

    const situation = await resolveLyricsSituation("hash123");
    expect(situation).toEqual({ kind: "unsynced" });
  });

  it("(d) no recording row at all → none", async () => {
    mockSelect.mockReturnValueOnce(selectChain([]));

    const situation = await resolveLyricsSituation("unknown-hash");
    expect(situation).toEqual({ kind: "none" });
  });

  it("(e) recording exists, no lyrics anywhere → none", async () => {
    mockSelect
      .mockReturnValueOnce(
        selectChain([{ hashPrefix: "ab", lrcStatus: "missing", songId: null }])
      );

    const situation = await resolveLyricsSituation("hash123");
    expect(situation).toEqual({ kind: "none" });
  });

  it("(f) recording exists, R2 404 + no song lyrics → none", async () => {
    mockSelect
      .mockReturnValueOnce(
        selectChain([{ hashPrefix: "ab", lrcStatus: "ready", songId: null }])
      );
    mockCreateR2ClientFromEnv.mockReturnValue({
      getLrcSignedUrl: vi.fn().mockResolvedValue({ url: "https://r2.example.com/x.lrc" }),
    });
    mockFetch.mockResolvedValue({ ok: false, status: 404 });

    const situation = await resolveLyricsSituation("hash123");
    expect(situation).toEqual({ kind: "none" });
  });

  it("(g) R2 200 with non-LRC plain text → unsynced (fallback text)", async () => {
    mockSelect
      .mockReturnValueOnce(
        selectChain([{ hashPrefix: "ab", lrcStatus: "ready", songId: null }])
      );
    mockCreateR2ClientFromEnv.mockReturnValue({
      getLrcSignedUrl: vi.fn().mockResolvedValue({ url: "https://r2.example.com/x.lrc" }),
    });
    mockFetch.mockResolvedValue({
      ok: true,
      text: vi.fn().mockResolvedValue("just some words, no timestamps"),
    });

    const situation = await resolveLyricsSituation("hash123");
    expect(situation).toEqual({ kind: "unsynced" });
  });

  it("(h) R2 network error falls through silently", async () => {
    mockSelect
      .mockReturnValueOnce(
        selectChain([{ hashPrefix: "ab", lrcStatus: "ready", songId: null }])
      );
    mockCreateR2ClientFromEnv.mockReturnValue({
      getLrcSignedUrl: vi.fn().mockRejectedValue(new Error("DNS failure")),
    });

    const situation = await resolveLyricsSituation("hash123");
    expect(situation).toEqual({ kind: "none" });
  });
});

// --------------------------------------------------------------------------
// validateFeedbackSubmission — the state-aware validation matrix.
// --------------------------------------------------------------------------

describe("validateFeedbackSubmission", () => {
  const synced = { kind: "synced" } as const;
  const unsynced = { kind: "unsynced" } as const;
  const none = { kind: "none" } as const;

  it("happy accepted when synced lyrics exist", () => {
    const result = validateFeedbackSubmission("happy", undefined, synced);
    expect(result.valid).toBe(true);
  });

  it("happy accepted when unsynced lyrics exist", () => {
    const result = validateFeedbackSubmission("happy", undefined, unsynced);
    expect(result.valid).toBe(true);
  });

  it("happy rejected when no lyrics", () => {
    const result = validateFeedbackSubmission("happy", undefined, none);
    expect(result.valid).toBe(false);
  });

  it("sad+missing rejected when synced lyrics exist", () => {
    const result = validateFeedbackSubmission("sad", "missing", synced);
    expect(result.valid).toBe(false);
  });

  it("sad+missing accepted when unsynced-only", () => {
    const result = validateFeedbackSubmission("sad", "missing", unsynced);
    expect(result.valid).toBe(true);
  });

  it("sad+missing accepted when no lyrics", () => {
    const result = validateFeedbackSubmission("sad", "missing", none);
    expect(result.valid).toBe(true);
  });

  it("sad+timing accepted when synced lyrics exist", () => {
    const result = validateFeedbackSubmission("sad", "timing", synced);
    expect(result.valid).toBe(true);
  });

  it("sad+timing rejected when unsynced-only", () => {
    const result = validateFeedbackSubmission("sad", "timing", unsynced);
    expect(result.valid).toBe(false);
  });

  it("sad+timing rejected when no lyrics", () => {
    const result = validateFeedbackSubmission("sad", "timing", none);
    expect(result.valid).toBe(false);
  });

  it("sad+wrong_text accepted in synced state", () => {
    expect(validateFeedbackSubmission("sad", "wrong_text", synced).valid).toBe(true);
  });

  it("sad+wrong_text accepted in unsynced state", () => {
    expect(validateFeedbackSubmission("sad", "wrong_text", unsynced).valid).toBe(true);
  });

  it("sad+wrong_text accepted in none state", () => {
    expect(validateFeedbackSubmission("sad", "wrong_text", none).valid).toBe(true);
  });

  it("sad+other accepted in synced state", () => {
    expect(validateFeedbackSubmission("sad", "other", synced).valid).toBe(true);
  });

  it("sad+other accepted in none state", () => {
    expect(validateFeedbackSubmission("sad", "other", none).valid).toBe(true);
  });

  it("happy with a reason is invalid (reason must be null for happy)", () => {
    expect(validateFeedbackSubmission("happy", "timing", synced).valid).toBe(false);
  });

  it("sad without a reason is invalid (reason must be non-null for sad)", () => {
    expect(validateFeedbackSubmission("sad", undefined, synced).valid).toBe(false);
  });

  it("unknown rating is invalid", () => {
    expect(validateFeedbackSubmission("meh", undefined, synced).valid).toBe(false);
  });

  it("unknown reason is invalid", () => {
    expect(validateFeedbackSubmission("sad", "shouting", synced).valid).toBe(false);
  });
});