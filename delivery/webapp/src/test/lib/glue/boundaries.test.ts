import { describe, expect, it, vi } from "vitest";

vi.mock("@/db", () => ({ db: {} }));
vi.mock("@/db/mutation-db", () => ({ mutationDb: {} }));

import { buildArrangementTimeline } from "@/lib/arrangements";
import { buildBoundaryDescriptor } from "@/lib/glue/descriptor";
import { createGlueJob, createGlueJobDeps } from "@/lib/glue/jobs";
import { computeGlueQuote, deriveGlueTarget, getGlueTargetItems, type GlueResolverItem } from "@/lib/glue/quote";
import { resolveSongsetBoundaries, type GlueTakeRow } from "@/lib/glue/resolve";
import type { SongsetBoundarySettings } from "@/lib/glue/boundary-types";
import { arrangedVideoLeadIn } from "@/lib/render/opening-timing";

const saved = JSON.stringify({
  version: 1,
  steps: [{ sectionIndex: 0, label: "Verse", startSeconds: 0, endSeconds: 12, manualBounds: true }],
});
const at = "2026-11-28T12:00:00.000Z";
const config = { intro: { enabled: true, beats: 8, takeId: null } } satisfies SongsetBoundarySettings;

function item(id: string, arrangementSteps: string | null = saved): GlueResolverItem {
  return {
    itemId: id, position: 0, userId: 1, glueEnabled: false, glueBeats: 4,
    glueActiveTakeId: null, updatedAt: at, arrangementSteps,
    recording: {
      contentHash: id, durationSeconds: 12, tempoBpm: 120, beats: [0, 0.5, 1, 11.5],
      sections: [{ label: "Verse", start: 0, end: 12 }],
    },
  };
}

function resolve(items: GlueResolverItem[], settings?: SongsetBoundarySettings, takes: GlueTakeRow[] = []) {
  return resolveSongsetBoundaries({
    items, takes, jobs: [], cachedExcerptKeys: new Set(), rejectedExcerptKeys: new Set(),
    quota: { limit: 5, used: 0, remaining: 5, resetAt: null },
  }, settings);
}

function take(direction: "intro" | "outro", contentHash: string, id: string): GlueTakeRow {
  const descriptor = buildBoundaryDescriptor(direction, 8, 120, { contentHash, steps: [[0, 12]] });
  return {
    id, userId: 1, descriptorKey: descriptor.key, descriptorJson: descriptor.canonicalJson,
    ordinal: 1, status: "ready", r2Key: `glues/${id}.wav`, seconds: 4,
    audioSha256: "a".repeat(64), metricsJson: "{}", reasonsJson: "[]", createdAt: at,
  };
}

describe("Songset boundaries", () => {
  it("offers each direction only at an arranged edge and offers both for one item", () => {
    expect(resolve([item("a", null), item("b")]).map((entry) => entry.eligible)).toEqual([false, true]);
    expect(resolve([item("b"), item("a", null)]).map((entry) => entry.eligible)).toEqual([true, false]);
    expect(resolve([item("b")]).map((entry) => [entry.eligible, entry.glueBeats]))
      .toEqual([[true, 8], [true, 8]]);
    expect(resolve([item("a", "invalid")]).map((entry) => entry.eligible)).toEqual([false, false]);
  });

  it("suspends on reset, marks reordered audio stale, and restores the matching selection", () => {
    const a = take("intro", "a", "a-take");
    const b = take("intro", "b", "b-take");
    const settings: SongsetBoundarySettings = {
      intro: { enabled: true, beats: 8, takeId: a.id, selections: { [a.descriptorKey]: a.id } },
    };
    expect(resolve([item("a", null), item("b")], settings, [a, b])[0])
      .toMatchObject({ suspended: true, selectedTake: null, glueEnabled: true });
    expect(resolve([item("b"), item("a")], settings, [a, b])[0])
      .toMatchObject({ state: "stale", availableTakes: [{ id: "b-take" }] });
    settings.intro!.takeId = b.id;
    expect(resolve([item("a"), item("b")], settings, [a, b])[0])
      .toMatchObject({ state: "ready", selectedTake: { id: "a-take" } });
  });

  it("quotes only the retained side and distinguishes two-beat takes by direction", () => {
    expect(buildBoundaryDescriptor("intro", 8, 120, { contentHash: "a", steps: [[0, 12]] }).key)
      .toBe("899c1e9d93e7e2442e7c5fadb5007a526fdf74f037e1f3fd1699a5fafaeda814");
    const first = item("a");
    first.glueBeats = 2;
    const intro = deriveGlueTarget("intro", first, first);
    const outro = deriveGlueTarget("outro", first, first);
    if (!intro.ok || !outro.ok) throw new Error("Expected eligible edges");
    expect(intro.descriptorKey).not.toBe(outro.descriptorKey);
    expect(computeGlueQuote(intro, new Set(), new Set())).toMatchObject({
      generationSeconds: 1, uncachedSides: [{ side: "B", seconds: 12 }],
    });
    expect(computeGlueQuote(outro, new Set(), new Set()).uncachedSides)
      .toMatchObject([{ side: "A", seconds: 12 }]);
  });

  it("recovers a matching paid take for free and rejects a changed quote before reserving", async () => {
    const first = { ...item("a"), boundarySettings: config };
    const inputs = getGlueTargetItems([first], "intro")!;
    const derivation = deriveGlueTarget("intro", inputs.from, inputs.to);
    if (!derivation.ok) throw new Error("Expected eligible Intro");
    const createJobAtomically = vi.fn();
    const selectBoundaryTake = vi.fn().mockResolvedValue("saved");
    const deps = createGlueJobDeps({
      loadItems: vi.fn().mockResolvedValue(new Map([["set", [first]]])),
      repository: {
        ...createGlueJobDeps().repository,
        findJobByClientRequestId: vi.fn().mockResolvedValue(null),
        selectNewestReadyTakeId: vi.fn().mockResolvedValue("paid"),
        createJobAtomically,
      },
      selectBoundaryTake,
    });
    const request = {
      songsetId: "set", userId: 1, target: "intro", fromItemId: "a", clientRequestId: "request",
      expectedFromUpdatedAt: at, expectedToUpdatedAt: at, takeMode: "initial",
      expectedDescriptorKey: derivation.descriptorKey,
    } as const;
    expect(await createGlueJob(request, deps)).toEqual({ kind: "ready", takeId: "paid", cached: true });
    expect(selectBoundaryTake).toHaveBeenCalledWith("set", 1, "intro", { takeId: "paid" }, derivation.descriptorKey);
    await expect(createGlueJob({ ...request, expectedDescriptorKey: "stale" }, deps))
      .rejects.toMatchObject({ code: "GLUE_STALE_SNAPSHOT" });
    expect(createJobAtomically).not.toHaveBeenCalled();
  });

  it("places both takes outside the single Song's recorded span and counts visuals only in video", () => {
    const timeline = buildArrangementTimeline([{
      itemId: "a", recordingHashPrefix: "abc", arrangementSteps: saved, recordingDurationSeconds: 12,
      intro: { id: "intro", seconds: 3 }, outro: { id: "outro", seconds: 4 },
    }]);
    expect(timeline.totalSeconds).toBe(19);
    expect(timeline.items[0]).toMatchObject({
      startSeconds: 3, durationSeconds: 12,
      glueWindowBefore: { startSeconds: 0, endSeconds: 3, target: "intro" },
      glueWindowAfter: { startSeconds: 15, endSeconds: 19, target: "outro" },
    });
    expect(arrangedVideoLeadIn({ arrangedOpening: true, openingSilenceSeconds: 2 }, {
      videoEnabled: true, includeTitleCard: true, titleCardDurationSeconds: 5,
    })).toBe(8);
    expect(arrangedVideoLeadIn({ arrangedOpening: true, openingSilenceSeconds: 2 }, {
      videoEnabled: false, includeTitleCard: true,
    })).toBe(0);
  });
});
