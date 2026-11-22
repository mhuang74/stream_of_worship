import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import { renderWithLocale as render } from "@/test/render";
import { ArrangementEditorSheet } from "@/components/songset/ArrangementEditorSheet";
import type { SongListItem } from "@/components/songset/SongList";
import type { GlueJobStatus } from "@/lib/glue/client";
import type { ResolvedGlueJunction } from "@/lib/glue/resolve";

vi.mock("@/contexts/SongsetListenContext", () => ({
  useSongsetListen: () => ({ audio: { pause: vi.fn() } }),
}));

vi.mock("@/hooks/useSongsetAudio", () => ({
  useSongsetAudio: () => ({
    status: "idle",
    errorMessage: null,
    timelinePositionSeconds: 0,
    totalSeconds: 120,
    progress: null,
    play: vi.fn().mockResolvedValue(undefined),
    pause: vi.fn(),
    stop: vi.fn(),
    seek: vi.fn(),
    startAtSeconds: vi.fn().mockResolvedValue(undefined),
  }),
}));

vi.mock("@/hooks/useSongLyrics", () => ({
  useSongLyrics: () => ({
    lrcContent: "[00:01.00]line\n",
    lines: null,
    loading: false,
    error: null,
  }),
}));

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly url: string;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  close(): void {
    this.closed = true;
  }

  emit(status: GlueJobStatus): void {
    this.onmessage?.({ data: JSON.stringify(status) });
  }
}

const TAKE_ID = "6b7f9c34-1c2b-4c4d-8a3a-1b2c3d4e5f60";
const JOB_ID = "9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f";

const SECTIONS = [{ label: "Verse", start: 0, end: 30 }];
const BEATS = Array.from({ length: 16 }, (_, index) => index * 2);

function junction(
  overrides: Partial<ResolvedGlueJunction> = {}
): ResolvedGlueJunction {
  return {
    fromItemId: "item-1",
    toItemId: "item-2",
    glueEnabled: true,
    glueBeats: 4,
    descriptorKey: "a".repeat(64),
    state: "needs-generation",
    selectedTake: null,
    activeJob: null,
    quote: null,
    quotaLimit: 5,
    quotaUsed: 2,
    quotaRemaining: 3,
    quotaResetAt: "2026-11-23T09:15:00.000Z",
    expectedFromUpdatedAt: "2026-11-22T10:00:00.000Z",
    expectedToUpdatedAt: "2026-11-22T10:01:00.000Z",
    unavailableReason: null,
    ...overrides,
  };
}

function makeItem(overrides: Partial<SongListItem> = {}): SongListItem {
  return {
    id: "item-1",
    songId: "song-1",
    position: 0,
    song: {
      id: "song-1",
      title: "Amazing Grace",
      composer: null,
      lyricist: null,
      albumName: null,
      musicalKey: "G",
    },
    recording: {
      contentHash: "abc123",
      hashPrefix: "ab",
      durationSeconds: 30,
      tempoBpm: 120,
      musicalKey: "G",
      sections: SECTIONS,
      beats: BEATS,
    },
    gapBeats: 2,
    crossfadeEnabled: 0,
    keyShiftSemitones: 0,
    tempoRatio: 1,
    arrangementSteps: null,
    arrangedDurationSeconds: 30,
    arrangementAnalysisAvailable: true,
    updatedAt: "2026-11-22T10:00:00.000Z",
    glueEnabled: true,
    glueBeats: 4,
    glue: junction(),
    ...overrides,
  };
}

function successor(): SongListItem {
  return makeItem({
    id: "item-2",
    songId: "song-2",
    position: 1,
    song: {
      id: "song-2",
      title: "How Great Thou Art",
      composer: null,
      lyricist: null,
      albumName: null,
      musicalKey: "G",
    },
    glue: undefined,
  });
}

const QUOTE = {
  descriptorKey: "a".repeat(64),
  fromItemId: "item-1",
  toItemId: "item-2",
  expectedFromUpdatedAt: "2026-11-22T10:00:00.000Z",
  expectedToUpdatedAt: "2026-11-22T10:01:00.000Z",
  uncachedSides: [],
  generationSeconds: 3,
  uploadCredits: 405,
  generationCredits: 0,
  estimatedNewCredits: 405,
  blockedSides: [],
  quotaLimit: 5,
  quotaUsed: 2,
  quotaRemaining: 3,
  quotaResetAt: "2026-11-23T09:15:00.000Z",
};

function jobStatus(overrides: Partial<GlueJobStatus> = {}): GlueJobStatus {
  return {
    id: JOB_ID,
    songsetId: "songset-1",
    fromItemId: "item-1",
    toItemId: "item-2",
    phase: "queued",
    active: true,
    takeMode: "initial",
    createdAt: "2026-11-22T10:00:00.000Z",
    startedAt: null,
    finishedAt: null,
    elapsedSeconds: 4,
    attemptId: "attempt-1",
    providerContacted: false,
    selectedTakeId: null,
    resultingTakeId: null,
    errorCode: null,
    errorDetail: null,
    retryKind: null,
    quota: { limit: 5, used: 2, remaining: 3, resetAt: null },
    ...overrides,
  };
}

interface Harness {
  createBodies: Record<string, unknown>[];
  cancelCalls: string[];
  discoveryCalls: number;
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

function installFetch(activeStatuses: GlueJobStatus[] = []): Harness {
  const harness: Harness = {
    createBodies: [],
    cancelCalls: [],
    discoveryCalls: 0,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("glue-quote")) return jsonResponse(QUOTE);
      if (url.includes("?active=1")) {
        harness.discoveryCalls += 1;
        return jsonResponse({
          active: activeStatuses.filter((entry) => entry.active),
          latestByFromItemId: Object.fromEntries(
            activeStatuses.map((entry) => [entry.fromItemId, entry])
          ),
        });
      }
      if (url.endsWith("/glue-jobs") && method === "POST") {
        harness.createBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return jsonResponse({ jobId: JOB_ID, status: "queued" }, 202);
      }
      if (url.endsWith("/cancel") && method === "POST") {
        harness.cancelCalls.push(url);
        return jsonResponse({ jobId: JOB_ID, status: "cancelled" });
      }
      if (url.endsWith("/retry-processing") && method === "POST") {
        return jsonResponse({ jobId: JOB_ID, status: "staging", charged: false }, 202);
      }
      if (url.endsWith("/resume") && method === "POST") {
        return jsonResponse({ jobId: JOB_ID, status: "queued", charged: false }, 202);
      }
      if (url.match(/\/api\/glue-jobs\/[0-9a-f-]+$/)) {
        return jsonResponse(jobStatus());
      }
      return jsonResponse({ code: "GLUE_NOT_FOUND", message: "not found" }, 404);
    })
  );
  return harness;
}

function renderSheet(items?: SongListItem[]) {
  const allItems = items ?? [makeItem(), successor()];
  return render(
    <ArrangementEditorSheet
      isOpen
      onOpenChange={vi.fn()}
      item={allItems[0]}
      items={allItems}
      onSave={vi.fn().mockResolvedValue(undefined)}
      songsetId="songset-1"
      onUpdateGlue={vi.fn().mockResolvedValue(undefined)}
    />
  );
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ArrangementEditorSheet AI transition jobs", () => {
  it("confirms the exact credits, quota, and reset before starting a paid job", async () => {
    const harness = installFetch();
    renderSheet();

    await waitFor(() =>
      expect(screen.getByTestId("arrangement-glue-quote")).toBeTruthy()
    );
    fireEvent.click(screen.getByTestId("arrangement-glue-generate"));

    const confirm = await screen.findByTestId("arrangement-glue-confirm");
    expect(confirm.textContent).toContain("405");
    expect(confirm.textContent).toContain("3");
    expect(confirm.textContent).toContain("5");

    fireEvent.click(screen.getByTestId("arrangement-glue-confirm-generate"));
    await waitFor(() => expect(harness.createBodies).toHaveLength(1));
    expect(harness.createBodies[0]).toMatchObject({
      fromItemId: "item-1",
      clientRequestId: expect.any(String),
      expectedFromUpdatedAt: "2026-11-22T10:00:00.000Z",
      expectedToUpdatedAt: "2026-11-22T10:01:00.000Z",
      takeMode: "initial",
    });

    const job = await screen.findByTestId("arrangement-glue-job");
    expect(job.textContent).toContain(JOB_ID.slice(0, 8));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    FakeEventSource.instances[0].emit(jobStatus());
    await waitFor(() =>
      expect(screen.getByTestId("arrangement-glue-phase")).toBeTruthy()
    );
    expect(screen.getByTestId("arrangement-glue-phase").textContent).toContain(
      "Waiting to start"
    );
    expect(screen.getByTestId("arrangement-glue-elapsed")).toBeTruthy();
  });

  it("never offers Cancel after provider contact and shows the refund wording", async () => {
    installFetch();
    renderSheet();

    await waitFor(() =>
      expect(screen.getByTestId("arrangement-glue-quote")).toBeTruthy()
    );
    fireEvent.click(screen.getByTestId("arrangement-glue-generate"));
    await screen.findByTestId("arrangement-glue-confirm");
    fireEvent.click(screen.getByTestId("arrangement-glue-confirm-generate"));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    const stream = FakeEventSource.instances[0];
    stream.emit(jobStatus({ phase: "preparing", providerContacted: false }));
    await waitFor(() =>
      expect(screen.getByTestId("arrangement-glue-cancel")).toBeTruthy()
    );

    stream.emit(
      jobStatus({
        phase: "composing",
        providerContacted: true,
        elapsedSeconds: 9,
      })
    );
    await waitFor(() =>
      expect(screen.queryByTestId("arrangement-glue-cancel")).toBeNull()
    );
    expect(screen.getByTestId("arrangement-glue-contacted").textContent).toContain(
      "can't be cancelled or refunded"
    );
    expect(screen.getByTestId("arrangement-glue-phase").textContent).toContain(
      "Creating the AI transition"
    );
    // The server's real elapsed time, never a percentage.
    expect(screen.getByTestId("arrangement-glue-elapsed").textContent).toContain("9");
    expect(screen.getByTestId("arrangement-glue").textContent).not.toMatch(/\d+\s*%/);
  });

  it("adopts a persisted active job on load and can cancel it before contact", async () => {
    const active = jobStatus({ phase: "preparing" });
    const harness = installFetch([active]);
    renderSheet([
      makeItem({
        glue: junction({
          state: "preparing",
          activeJob: {
            id: JOB_ID,
            phase: "preparing",
            takeMode: "initial",
            createdAt: "2026-11-22T10:00:00.000Z",
            startedAt: "2026-11-22T10:00:01.000Z",
          },
        }),
      }),
      successor(),
    ]);

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(harness.discoveryCalls).toBeGreaterThanOrEqual(1);
    const stream = FakeEventSource.instances[0];
    stream.emit(active);
    await waitFor(() =>
      expect(screen.getByTestId("arrangement-glue-cancel")).toBeTruthy()
    );

    fireEvent.click(screen.getByTestId("arrangement-glue-cancel"));
    await waitFor(() => expect(harness.cancelCalls).toHaveLength(1));
    expect(harness.cancelCalls[0]).toContain(JOB_ID);
    await waitFor(() =>
      expect(screen.getByTestId("arrangement-glue-cancelled")).toBeTruthy()
    );
  });

  it("offers a free retry for a worker failure with a stored response", async () => {
    installFetch();
    renderSheet();
    await waitFor(() =>
      expect(screen.getByTestId("arrangement-glue-quote")).toBeTruthy()
    );
    fireEvent.click(screen.getByTestId("arrangement-glue-generate"));
    await screen.findByTestId("arrangement-glue-confirm");
    fireEvent.click(screen.getByTestId("arrangement-glue-confirm-generate"));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    FakeEventSource.instances[0].emit(
      jobStatus({
        phase: "failed",
        active: false,
        providerContacted: true,
        retryKind: "free",
        errorCode: "GLUE_WORKER_FAILED",
      })
    );

    await waitFor(() =>
      expect(screen.getByTestId("arrangement-glue-retry-processing")).toBeTruthy()
    );
    expect(screen.queryByTestId("arrangement-glue-resume")).toBeNull();
    expect(screen.getByTestId("arrangement-glue-job-error").textContent).toContain(
      "did not finish"
    );
    expect(screen.getByTestId("arrangement-glue").textContent).not.toContain(
      "new paid request"
    );
  });

  it("offers a paid Try another when the last attempt cannot be retried for free", async () => {
    installFetch();
    renderSheet([
      makeItem({
        glue: junction({
          state: "ready",
          selectedTake: {
            id: TAKE_ID,
            ordinal: 1,
            seconds: 3,
            r2Key: "glues/take.wav",
            descriptorKey: "a".repeat(64),
            status: "ok",
            reasons: [],
          },
        }),
      }),
      successor(),
    ]);

    await waitFor(() =>
      expect(screen.getByTestId("arrangement-glue-quote")).toBeTruthy()
    );
    fireEvent.click(screen.getByTestId("arrangement-glue-try-another"));
    const confirm = await screen.findByTestId("arrangement-glue-confirm");
    expect(confirm.textContent).toContain("replaces the current selection");
    fireEvent.click(screen.getByTestId("arrangement-glue-confirm-generate"));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    FakeEventSource.instances[0].emit(
      jobStatus({
        phase: "failed",
        active: false,
        providerContacted: true,
        retryKind: "paid",
        errorCode: "GLUE_PROVIDER_UNKNOWN",
      })
    );

    await waitFor(() =>
      expect(
        screen.getAllByText(/new paid request/i).length
      ).toBeGreaterThanOrEqual(1)
    );
    expect(screen.queryByTestId("arrangement-glue-retry-processing")).toBeNull();
    expect(screen.queryByTestId("arrangement-glue-resume")).toBeNull();
  });
});
