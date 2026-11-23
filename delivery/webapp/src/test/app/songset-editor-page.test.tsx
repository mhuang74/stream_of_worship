import { describe, it, expect, vi, beforeEach } from "vitest";

import { auth } from "@/lib/auth";
import { getSongsetEditorData } from "@/lib/db/songsets";

// The page module is a server component; rendering it would pull the real DB
// client, so its only job here is the prop mapping into the editor client. The
// client is replaced with a probe that returns the props it received.
vi.mock("next/headers", () => ({
  headers: vi.fn().mockResolvedValue(new Headers()),
}));

vi.mock("next/navigation", () => ({
  notFound: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
  redirect: vi.fn((url: string) => {
    throw new Error(`NEXT_REDIRECT:${url}`);
  }),
}));

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

vi.mock("@/lib/db/songsets", () => ({
  getSongsetEditorData: vi.fn(),
}));

vi.mock("@/app/songsets/[id]/SongsetEditorClient", () => ({
  SongsetEditorClient: vi.fn(() => null),
}));

import SongsetEditorPage from "@/app/songsets/[id]/page";

/* eslint-disable @typescript-eslint/no-explicit-any */

const GLUE_JUNCTION = {
  fromItemId: "item-1",
  toItemId: "item-2",
  glueEnabled: true,
  glueBeats: 8 as const,
  descriptorKey: "a".repeat(64),
  state: "ready" as const,
  selectedTake: {
    id: "take-1",
    ordinal: 1,
    seconds: 7.2,
    r2Key: "glues/take.wav",
    descriptorKey: "a".repeat(64),
    status: "ok" as const,
    reasons: [],
  },
  availableTakes: [],
  activeJob: null,
  quote: null,
  quotaLimit: 5,
  quotaUsed: 0,
  quotaRemaining: 5,
  quotaResetAt: null,
  expectedFromUpdatedAt: "2026-11-22T10:00:00.000Z",
  expectedToUpdatedAt: "2026-11-22T10:01:00.000Z",
  unavailableReason: null,
};

function editorData(itemOverrides: Record<string, unknown> = {}) {
  return {
    id: "songset-1",
    name: "Friday Worship",
    description: null,
    createdAt: new Date("2026-11-21T10:00:00.000Z"),
    updatedAt: new Date("2026-11-22T10:00:00.000Z"),
    renderState: "unrendered",
    latestRenderJobEstimatedTotalSeconds: null,
    itemCount: 2,
    durationSeconds: 120,
    latestRenderJobId: null,
    lastFailedRenderJobId: null,
    lastCompletedRenderJobId: null,
    renderErrorMessage: null,
    failedAt: null,
    items: [
      {
        id: "item-1",
        songId: "song-1",
        recordingHashPrefix: "f8cfc446",
        position: 0,
        gapBeats: 2,
        crossfadeEnabled: 0,
        crossfadeDurationSeconds: null,
        keyShiftSemitones: 0,
        tempoRatio: 1,
        markedLineCount: 0,
        arrangementSteps: null,
        song: { id: "song-1", title: "我揚聲敬拜", composer: null, lyricist: null, albumName: null, musicalKey: "G" },
        recording: null,
        updatedAt: "2026-11-22T10:00:00.000Z",
        glueEnabled: true,
        glueBeats: 8,
        glue: GLUE_JUNCTION,
        glueTakeId: "take-1",
        glueSeconds: 7.2,
        ...itemOverrides,
      },
      {
        id: "item-2",
        songId: "song-2",
        recordingHashPrefix: "50f64190",
        position: 1,
        gapBeats: 2,
        crossfadeEnabled: 0,
        crossfadeDurationSeconds: null,
        keyShiftSemitones: 0,
        tempoRatio: 1,
        markedLineCount: 0,
        arrangementSteps: null,
        song: { id: "song-2", title: "祢的救恩", composer: null, lyricist: null, albumName: null, musicalKey: "F#" },
        recording: null,
        updatedAt: "2026-11-21T16:05:46.458Z",
        glueEnabled: false,
        glueBeats: 4,
        glue: null,
        glueTakeId: null,
        glueSeconds: null,
      },
    ],
  };
}

describe("Songset editor page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 15 } } as any);
  });

  it("passes the saved Glue switch, beats, and resolved junction to the client", async () => {
    vi.mocked(getSongsetEditorData).mockResolvedValue(editorData() as any);

    // A server component returns the client element; its props are what the
    // browser receives.
    const element = await SongsetEditorPage({
      params: Promise.resolve({ id: "songset-1" }),
    });
    const props = element.props as {
      initialData: { items: Record<string, unknown>[] };
    };
    const [first, second] = props.initialData.items;
    // Dropping these made every reload read the saved switch as off and left
    // the rail with no junction to resolve.
    expect(first.glueEnabled).toBe(true);
    expect(first.glueBeats).toBe(8);
    expect(first.glue).toEqual(GLUE_JUNCTION);
    expect(second.glueEnabled).toBe(false);
    expect(second.glue).toBeNull();
  });

  it("still redirects when there is no session", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    await expect(
      SongsetEditorPage({ params: Promise.resolve({ id: "songset-1" }) })
    ).rejects.toThrow("NEXT_REDIRECT:/login");
    expect(getSongsetEditorData).not.toHaveBeenCalled();
  });
});
