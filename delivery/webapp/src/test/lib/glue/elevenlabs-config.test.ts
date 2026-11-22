import { describe, it, expect, afterEach, vi } from "vitest";

import {
  ElevenLabsNotConfiguredError,
  isElevenLabsConfigured,
  requireElevenLabsConfigured,
} from "@/lib/glue/elevenlabs-config";

describe("ElevenLabs configuration presence", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("reports configured only when the key is non-empty", () => {
    vi.stubEnv("SOW_ELEVENLABS_API_KEY", "");
    expect(isElevenLabsConfigured()).toBe(false);

    vi.stubEnv("SOW_ELEVENLABS_API_KEY", "   ");
    expect(isElevenLabsConfigured()).toBe(false);

    vi.stubEnv("SOW_ELEVENLABS_API_KEY", "server-only-test-value");
    expect(isElevenLabsConfigured()).toBe(true);
  });

  it("throws the stable GLUE_NOT_CONFIGURED code when required but absent", () => {
    vi.stubEnv("SOW_ELEVENLABS_API_KEY", "");
    expect(() => requireElevenLabsConfigured()).toThrow(ElevenLabsNotConfiguredError);
    try {
      requireElevenLabsConfigured();
    } catch (error) {
      expect((error as { code?: string }).code).toBe("GLUE_NOT_CONFIGURED");
    }
  });

  it("does not throw when the server key is present", () => {
    vi.stubEnv("SOW_ELEVENLABS_API_KEY", "server-only-test-value");
    expect(() => requireElevenLabsConfigured()).not.toThrow();
  });
});
