import { describe, it, expect } from "vitest";

import { applyDraftValidity } from "@/lib/glue/draft";
import type { ResolvedGlueJunction } from "@/lib/glue/resolve";

function junction(overrides: Partial<ResolvedGlueJunction> = {}): ResolvedGlueJunction {
  return {
    fromItemId: "item-a",
    toItemId: "item-b",
    glueEnabled: true,
    glueBeats: 4,
    descriptorKey: "a".repeat(64),
    state: "ready",
    selectedTake: null,
    activeJob: null,
    quote: null,
    quotaLimit: 5,
    quotaUsed: 0,
    quotaRemaining: 5,
    quotaResetAt: null,
    expectedFromUpdatedAt: "2026-11-22T10:00:00.000Z",
    expectedToUpdatedAt: "2026-11-22T10:01:00.000Z",
    unavailableReason: null,
    ...overrides,
  };
}

describe("applyDraftValidity", () => {
  const junctions = [
    junction({ fromItemId: "item-a", toItemId: "item-b" }),
    junction({ fromItemId: "item-b", toItemId: "item-c" }),
    junction({ fromItemId: "item-c", toItemId: "item-d" }),
  ];

  it("marks the edited item's outgoing junction and its predecessor's incoming junction stale", () => {
    const result = applyDraftValidity(junctions, {
      editedItemId: "item-b",
      hasUnsavedDraft: true,
    });
    expect(result[0]).toMatchObject({
      fromItemId: "item-a",
      state: "stale",
      unavailableReason: "unsaved-draft",
    });
    expect(result[1]).toMatchObject({
      fromItemId: "item-b",
      state: "stale",
      unavailableReason: "unsaved-draft",
    });
    expect(result[2]).toMatchObject({ state: "ready", unavailableReason: null });
  });

  it("changes nothing when there is no unsaved draft", () => {
    const result = applyDraftValidity(junctions, {
      editedItemId: "item-b",
      hasUnsavedDraft: false,
    });
    expect(result).toEqual(junctions);
    expect(result[0]).not.toBe(junctions[0]);
  });

  it("handles the first item (no predecessor)", () => {
    const result = applyDraftValidity(junctions, {
      editedItemId: "item-a",
      hasUnsavedDraft: true,
    });
    expect(result[0].state).toBe("stale");
    expect(result[1].state).toBe("ready");
    expect(result[2].state).toBe("ready");
  });

  it("does not touch disabled junctions or a last-item no-next junction", () => {
    const disabled = junction({ fromItemId: "item-b", state: "disabled" });
    const last = junction({
      fromItemId: "item-c",
      toItemId: null,
      state: "disabled",
      unavailableReason: "no-next",
    });
    const result = applyDraftValidity([disabled, last], {
      editedItemId: "item-b",
      hasUnsavedDraft: true,
    });
    expect(result[0]).toEqual(disabled);
    expect(result[1]).toEqual(last);
  });
});
