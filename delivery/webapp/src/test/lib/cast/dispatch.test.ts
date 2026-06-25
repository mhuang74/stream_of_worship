import { describe, it, expect, vi } from "vitest";
import { dispatchCast, type CastCommandTarget } from "@/lib/cast/dispatch";
import type { PresentationCommand } from "@/types/presentation-api";

function makeTarget(): CastCommandTarget & {
  calls: Record<string, unknown[][]>;
} {
  const calls: Record<string, unknown[][]> = {};
  const record = (name: string) => (...args: unknown[]) => {
    (calls[name] ??= []).push(args);
  };
  return {
    play: vi.fn(record("play")),
    pause: vi.fn(record("pause")),
    seek: vi.fn(record("seek")),
    setVolume: vi.fn(record("setVolume")),
    setMuted: vi.fn(record("setMuted")),
    calls,
  };
}

describe("dispatchCast", () => {
  it("routes play → cast.play()", () => {
    const t = makeTarget();
    dispatchCast(t, { type: "play" });
    expect(t.play).toHaveBeenCalledTimes(1);
    expect(t.pause).not.toHaveBeenCalled();
  });

  it("routes pause → cast.pause()", () => {
    const t = makeTarget();
    dispatchCast(t, { type: "pause" });
    expect(t.pause).toHaveBeenCalledTimes(1);
    expect(t.play).not.toHaveBeenCalled();
  });

  it("routes seek → cast.seek(positionSeconds)", () => {
    const t = makeTarget();
    const cmd: PresentationCommand = { type: "seek", positionSeconds: 42.5 };
    dispatchCast(t, cmd);
    expect(t.seek).toHaveBeenCalledWith(42.5);
    expect(t.calls.seek).toEqual([[42.5]]);
  });

  it("routes volume → cast.setVolume(level)", () => {
    const t = makeTarget();
    const cmd: PresentationCommand = { type: "volume", level: 0.7 };
    dispatchCast(t, cmd);
    expect(t.setVolume).toHaveBeenCalledWith(0.7);
  });

  it("routes mute → cast.setMuted(muted) — NOT setVolume(0)", () => {
    const t = makeTarget();
    dispatchCast(t, { type: "mute", muted: true });
    expect(t.setMuted).toHaveBeenCalledWith(true);
    expect(t.setVolume).not.toHaveBeenCalled();
    // Explicitly assert never receives a 0 volume (the v2 anti-pattern).
    expect(t.calls.setVolume ?? []).toEqual([]);
  });

  it("mute=false routes to setMuted(false) without touching volume", () => {
    const t = makeTarget();
    dispatchCast(t, { type: "mute", muted: false });
    expect(t.setMuted).toHaveBeenCalledWith(false);
    expect(t.setVolume).not.toHaveBeenCalled();
  });

  it("songTitle is a no-op (title set via media metadata at loadMedia)", () => {
    const t = makeTarget();
    dispatchCast(t, { type: "songTitle", title: "Amazing Grace" });
    expect(t.play).not.toHaveBeenCalled();
    expect(t.pause).not.toHaveBeenCalled();
    expect(t.seek).not.toHaveBeenCalled();
    expect(t.setVolume).not.toHaveBeenCalled();
    expect(t.setMuted).not.toHaveBeenCalled();
  });

  it("unknown command type is a no-op", () => {
    const t = makeTarget();
    // @ts-expect-error intentional unknown command for forward-compat test
    dispatchCast(t, { type: "unknownCommand" });
    expect(t.play).not.toHaveBeenCalled();
    expect(t.pause).not.toHaveBeenCalled();
    expect(t.seek).not.toHaveBeenCalled();
    expect(t.setVolume).not.toHaveBeenCalled();
    expect(t.setMuted).not.toHaveBeenCalled();
  });
});
