import { describe, it, expect, vi } from "vitest";

vi.mock("@/db", () => ({ db: {} }));

import { glueTakeSelectionError, type GlueTakeRow } from "@/lib/glue/resolve";
import type { GlueJunctionReady } from "@/lib/glue/quote";

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);
const DESCRIPTOR_KEY = "c".repeat(64);
const DESCRIPTOR_JSON = '{"v":2}';

function derivation(): GlueJunctionReady {
  return {
    ok: true,
    descriptorKey: DESCRIPTOR_KEY,
    descriptorJson: DESCRIPTOR_JSON,
    fromSide: {
      contentHash: HASH_A,
      steps: null,
      arrangedDurationSeconds: 200,
      excerptSeconds: 12,
      excerptTooShort: false,
    },
    toSide: {
      contentHash: HASH_B,
      steps: null,
      arrangedDurationSeconds: 180,
      excerptSeconds: 12,
      excerptTooShort: false,
    },
    generationSeconds: 3,
  };
}

function take(overrides: Partial<GlueTakeRow> = {}): GlueTakeRow {
  return {
    id: "take-1",
    userId: 1,
    descriptorKey: DESCRIPTOR_KEY,
    descriptorJson: DESCRIPTOR_JSON,
    ordinal: 1,
    status: "ready",
    r2Key: "glues/take-1.wav",
    seconds: 3,
    audioSha256: "d".repeat(64),
    metricsJson: "{}",
    reasonsJson: "[]",
    createdAt: "2026-11-22T10:00:00.000Z",
    ...overrides,
  };
}

function check(args: {
  glueEnabled?: boolean;
  hasSuccessor?: boolean;
  derivation?: ReturnType<typeof derivation> | { ok: false; reason: "no-tempo" };
  take?: GlueTakeRow | null;
  ownerUserId?: number;
}) {
  return glueTakeSelectionError({
    glueEnabled: args.glueEnabled ?? true,
    hasSuccessor: args.hasSuccessor ?? true,
    derivation: args.derivation ?? derivation(),
    take: args.take === undefined ? take() : args.take,
    ownerUserId: args.ownerUserId ?? 1,
  });
}

describe("glueTakeSelectionError", () => {
  it("accepts an owner's ready take matching the derived descriptor", () => {
    expect(check({})).toBeNull();
  });

  it("rejects disabled junctions", () => {
    expect(check({ glueEnabled: false })).toBe("GLUE_NOT_ENABLED");
  });

  it("rejects the last item", () => {
    expect(check({ hasSuccessor: false })).toBe("GLUE_NO_SUCCESSOR");
  });

  it("rejects saved inputs that no longer derive", () => {
    expect(check({ derivation: { ok: false, reason: "no-tempo" } })).toBe("GLUE_INPUTS_CHANGED");
  });

  it("rejects a missing take", () => {
    expect(check({ take: null })).toBe("GLUE_NOT_READY");
  });

  it("rejects a take owned by another user", () => {
    expect(check({ take: take({ userId: 2 }) })).toBe("GLUE_NOT_READY");
  });

  it("rejects a take that is not ready", () => {
    expect(check({ take: take({ status: "failed" }) })).toBe("GLUE_NOT_READY");
  });

  it("rejects a take from a different Junction (stored descriptor differs)", () => {
    expect(check({ take: take({ descriptorKey: "e".repeat(64) }) })).toBe(
      "GLUE_STALE_SNAPSHOT"
    );
  });

  it("rejects a ready take whose descriptor JSON differs at the same key", () => {
    expect(check({ take: take({ descriptorJson: '{"v":2,"beats":8}' }) })).toBe(
      "GLUE_STALE_SNAPSHOT"
    );
  });

  it("rejects a ready take with no staged audio", () => {
    expect(check({ take: take({ r2Key: null }) })).toBe("GLUE_NOT_READY");
  });
});
