import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

class FakeBufferSource {
  buffer: unknown = null;
  connect = vi.fn();
  start = vi.fn();
}

class FakeAudioContext {
  static instances: FakeAudioContext[] = [];
  static resumeError: Error | null = null;

  readonly constructedWith: unknown[];
  readonly sampleRate = 48000;
  readonly destination = { name: "destination" };
  readonly buffer = { name: "silent" };
  readonly sources: FakeBufferSource[] = [];
  state = "suspended";
  resume = vi.fn(async () => {
    if (FakeAudioContext.resumeError) throw FakeAudioContext.resumeError;
    this.state = "running";
  });
  close = vi.fn(async () => {});
  createBuffer = vi.fn(() => this.buffer);
  createBufferSource = vi.fn(() => {
    const source = new FakeBufferSource();
    this.sources.push(source);
    return source;
  });

  constructor(...args: unknown[]) {
    this.constructedWith = args;
    FakeAudioContext.instances.push(this);
  }
}

// Each case re-imports after vi.resetModules() so the module-level singleton
// starts empty; a static import cannot cross that module-loading boundary.
describe("audio-context", () => {
  beforeEach(() => {
    FakeAudioContext.instances = [];
    FakeAudioContext.resumeError = null;
    vi.resetModules();
    vi.stubGlobal("AudioContext", FakeAudioContext);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("creates the context lazily, without options, and returns the same instance", async () => {
    const { getAudioContext } = await import("@/lib/audio/audio-context");

    expect(FakeAudioContext.instances).toHaveLength(0);

    const first = getAudioContext();
    const second = getAudioContext();

    expect(first).toBe(second);
    expect(FakeAudioContext.instances).toHaveLength(1);
    expect(FakeAudioContext.instances[0].constructedWith).toEqual([]);
    expect(FakeAudioContext.instances[0].close).not.toHaveBeenCalled();
  });

  it("resumes and starts one silent one-sample source per unlock", async () => {
    const { unlockAudioContext } = await import("@/lib/audio/audio-context");

    unlockAudioContext();
    unlockAudioContext();

    const context = FakeAudioContext.instances[0];
    expect(context.resume).toHaveBeenCalledTimes(2);
    expect(context.createBuffer).toHaveBeenCalledTimes(2);
    expect(context.createBuffer).toHaveBeenNthCalledWith(1, 1, 1, context.sampleRate);
    expect(context.sources).toHaveLength(2);

    for (const source of context.sources) {
      expect(source.buffer).toBe(context.buffer);
      expect(source.connect).toHaveBeenCalledWith(context.destination);
      expect(source.start).toHaveBeenCalledTimes(1);
    }
  });

  it("does not throw or leak an unhandled rejection when resume rejects", async () => {
    FakeAudioContext.resumeError = new Error("gesture required");
    const { unlockAudioContext } = await import("@/lib/audio/audio-context");

    expect(() => unlockAudioContext()).not.toThrow();
    // Flush the rejection handler that unlockAudioContext() attached.
    await Promise.resolve();
    await Promise.resolve();
  });
});
