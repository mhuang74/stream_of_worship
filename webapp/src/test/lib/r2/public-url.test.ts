import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

describe("getPublicAudioUrl", () => {
  const originalEnv = process.env.NEXT_PUBLIC_R2_PUBLIC_DOMAIN;

  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_R2_PUBLIC_DOMAIN = originalEnv;
  });

  it("returns null when env var is not set", async () => {
    delete process.env.NEXT_PUBLIC_R2_PUBLIC_DOMAIN;
    const { getPublicAudioUrl } = await import("@/lib/r2/public-url");
    expect(getPublicAudioUrl("abc123")).toBeNull();
  });

  it("returns null when env var is empty string", async () => {
    process.env.NEXT_PUBLIC_R2_PUBLIC_DOMAIN = "";
    const { getPublicAudioUrl } = await import("@/lib/r2/public-url");
    expect(getPublicAudioUrl("abc123")).toBeNull();
  });

  it("constructs URL from domain without https://", async () => {
    process.env.NEXT_PUBLIC_R2_PUBLIC_DOMAIN = "pub-test.r2.dev";
    const { getPublicAudioUrl } = await import("@/lib/r2/public-url");
    expect(getPublicAudioUrl("abc123")).toBe("https://pub-test.r2.dev/abc123/audio.mp3");
  });

  it("strips https:// prefix from env var value", async () => {
    process.env.NEXT_PUBLIC_R2_PUBLIC_DOMAIN = "https://pub-test.r2.dev";
    const { getPublicAudioUrl } = await import("@/lib/r2/public-url");
    expect(getPublicAudioUrl("abc123")).toBe("https://pub-test.r2.dev/abc123/audio.mp3");
  });

  it("strips http:// prefix from env var value", async () => {
    process.env.NEXT_PUBLIC_R2_PUBLIC_DOMAIN = "http://pub-test.r2.dev";
    const { getPublicAudioUrl } = await import("@/lib/r2/public-url");
    expect(getPublicAudioUrl("abc123")).toBe("https://pub-test.r2.dev/abc123/audio.mp3");
  });
});
