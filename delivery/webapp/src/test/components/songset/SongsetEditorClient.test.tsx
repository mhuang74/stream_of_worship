import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import type { SongListItem } from "@/components/songset/SongList";
import type { TransitionSettings } from "@/components/songset/TransitionPanel";
import type { SongCardData } from "@/components/songset/SongCard";
import type { ResolvedGlueJunction } from "@/lib/glue/resolve";
import { SongsetEditorClient } from "@/app/songsets/[id]/SongsetEditorClient";
import { toast } from "sonner";

const shell = vi.hoisted(() => ({
  push: vi.fn(),
  unlockAudioContext: vi.fn(),
  open: vi.fn(),
  pauseAudition: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: shell.push }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/offline/offline-index", () => ({
  getOfflineRecord: vi.fn().mockResolvedValue(null),
  removeOfflineSongset: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

// The preview transport requires the shared player; the save path only stops it.
vi.mock("@/contexts/AudioPlayerContext", () => ({
  useAudioPlayerContext: () => ({
    currentTrack: null,
    state: { isPlaying: false },
    play: vi.fn(),
    stop: vi.fn(),
  }),
}));

vi.mock("@/components/songset/BrowseSheet", () => ({
  BrowseSheet: ({ onAddSong }: { onAddSong: (song: SongCardData) => Promise<void> }) => (
    <button
      type="button"
      data-testid="browse-add"
      onClick={() => void onAddSong({
        id: "song-3",
        title: "Third song",
        composer: null,
        lyricist: null,
        albumName: null,
        musicalKey: null,
        recordings: [],
      })}
    />
  ),
}));
vi.mock("@/components/share/ShareDialog", () => ({
  ShareDialog: () => null,
}));

vi.mock("@/lib/audio/audio-context", () => ({
  unlockAudioContext: shell.unlockAudioContext,
  getAudioContext: vi.fn(),
}));

vi.mock("@/contexts/SongsetListenContext", () => ({
  // The editor starts the audition; the arrangement sheet pauses it.
  useSongsetListen: () => ({ open: shell.open, audio: { pause: shell.pauseAudition } }),
}));

/**
 * Minimal editor-shell stub: it renders the item state the client owns and
 * exposes the two callbacks this slice exercises, so the assertions read the
 * real hook state instead of component wiring.
 */
vi.mock("@/components/songset/SongsetEditor", () => ({
  SongsetEditor: (props: {
    songset: {
      updatedAt: string;
      renderState: string;
      durationSeconds: number | null;
      itemCount: number;
    };
    items: SongListItem[];
    onListen: () => void;
    onCancelRender?: () => void;
    onUpdateTransition: (
      itemId: string,
      settings: TransitionSettings
    ) => Promise<void>;
    onEditArrangement: (itemId: string) => void;
    onDuplicateItem: (itemId: string) => Promise<void>;
    onRemoveItem: (itemId: string) => Promise<void>;
    onUpdateItems: (items: SongListItem[]) => void;
    isItemMutationPending?: boolean;
  }) => {
    const item = props.items[0];
    return (
      <div>
        <span data-testid="shell-updated-at">{props.songset.updatedAt}</span>
        <span data-testid="shell-render-state">{props.songset.renderState}</span>
        <span data-testid="shell-duration">{props.songset.durationSeconds}</span>
        <span data-testid="shell-item-count">{props.songset.itemCount}</span>
        <span data-testid="shell-pending">{String(props.isItemMutationPending ?? false)}</span>
        <span data-testid="shell-gap">{item?.gapBeats}</span>
        <span data-testid="shell-arrangement">{item?.arrangementSteps}</span>
        <span data-testid="shell-positions">
          {props.items.map((entry) => entry.position).join(",")}
        </span>
        <span data-testid="shell-glue">
          {props.items.map((entry) =>
            `${entry.id}:${entry.glue?.state ?? ""}:${entry.glue?.selectedTake?.id ?? ""}`
          ).join(",")}
        </span>
        <button
          type="button"
          data-testid="shell-reverse"
          onClick={() => props.onUpdateItems([...props.items].reverse())}
        />
        <button
          type="button"
          data-testid="shell-duplicate-first"
          onClick={() => void props.onDuplicateItem("item-1")}
        />
        <button
          type="button"
          data-testid="shell-remove-first"
          onClick={() => void props.onRemoveItem("item-1")}
        />
        <button
          type="button"
          data-testid="shell-cancel-render"
          onClick={() => props.onCancelRender?.()}
        />
        <button
          type="button"
          data-testid="shell-listen"
          onClick={props.onListen}
        />
        <button
          type="button"
          data-testid="shell-edit-arrangement"
          onClick={() => props.onEditArrangement(item.id)}
        />
        <button
          type="button"
          data-testid="shell-update-transition"
          onClick={() => {
            void props.onUpdateTransition(item.id, {
              gapBeats: 7,
              crossfadeEnabled: false,
              crossfadeDurationSeconds: 0,
              keyShiftSemitones: 0,
              tempoRatio: 1,
            });
          }}
        />
      </div>
    );
  },
}));

const SECTIONS = [
  { label: "Verse", start: 0, end: 10 },
  { label: "Chorus", start: 10, end: 20 },
];
const BEATS = [0, 5, 10, 15, 20];

function initialData() {
  return {
    id: "songset-1",
    name: "Sunday Worship",
    description: null,
    createdAt: "2026-01-01T00:00:00.000Z",
    updatedAt: "2026-01-01T00:00:00.000Z",
    renderState: "unrendered" as const,
    itemCount: 1,
    durationSeconds: 20,
    latestRenderJobId: null,
    lastFailedRenderJobId: null,
    lastCompletedRenderJobId: null,
    renderErrorMessage: null,
    failedAt: null,
    isArtifactsStale: false,
    items: [
      {
        id: "item-1",
        songId: "song-1",
        recordingHashPrefix: "ab",
        position: 0,
        gapBeats: 2,
        crossfadeEnabled: 0,
        crossfadeDurationSeconds: null,
        keyShiftSemitones: 0,
        tempoRatio: 1,
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
          durationSeconds: 20,
          tempoBpm: 120,
          musicalKey: "G",
          sections: SECTIONS,
          beats: BEATS,
        },
      },
    ],
  };
}

function jsonResponse(body: unknown, ok = true) {
  return {
    ok,
    status: ok ? 200 : 500,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

function readyJunction(fromItemId: string, toItemId: string): ResolvedGlueJunction {
  return {
    fromItemId,
    toItemId,
    glueEnabled: true,
    glueBeats: 8,
    descriptorKey: "descriptor",
    state: "ready",
    selectedTake: {
      id: "take-1", ordinal: 1, seconds: 6, r2Key: "take.wav",
      descriptorKey: "descriptor", status: "ok", reasons: [],
    },
    availableTakes: [],
    activeJob: null,
    quote: null,
    quotaLimit: 5,
    quotaUsed: 1,
    quotaRemaining: 4,
    quotaResetAt: null,
    expectedFromUpdatedAt: null,
    expectedToUpdatedAt: null,
    unavailableReason: null,
  };
}

/** Opens the (real) arrangement sheet for the single seeded item. */
async function openArrangementSheet() {
  fireEvent.click(screen.getByTestId("shell-edit-arrangement"));
  await screen.findByTestId("arrangement-sheet");
}

describe("SongsetEditorClient duplicate item", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shifts later Items and keeps the stored position gap", async () => {
    const data = initialData();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/items/item-1/duplicate") && init?.method === "POST") {
        return Promise.resolve(jsonResponse({ ...data.items[0], id: "item-copy", position: 1 }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <SongsetEditorClient
        songsetId="songset-1"
        initialData={{
          ...data,
          itemCount: 3,
          items: [
            data.items[0],
            { ...data.items[0], id: "item-2", position: 2 },
            { ...data.items[0], id: "item-3", position: 4 },
          ],
        }}
      />
    );
    expect(screen.getByTestId("shell-positions")).toHaveTextContent("0,2,4");

    fireEvent.click(screen.getByTestId("shell-duplicate-first"));

    // The copy takes position 1 and later Items shift by one, exactly like the server
    // write; renumbering densely would make the next append reuse position 3 or 5.
    await waitFor(() =>
      expect(screen.getByTestId("shell-positions")).toHaveTextContent("0,1,3,5")
    );
  });
});

describe("SongsetEditorClient Item mutation queue", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("starts a queued remove only after the in-flight duplicate settles", async () => {
    const data = initialData();
    const duplicate = Promise.withResolvers<Response>();
    const calls: string[] = [];
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/items/item-1/duplicate") && init?.method === "POST") {
        calls.push("duplicate");
        return duplicate.promise;
      }
      if (url.includes("/items?itemId=item-1") && init?.method === "DELETE") {
        calls.push("delete");
        return Promise.resolve(jsonResponse({ success: true }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<SongsetEditorClient songsetId="songset-1" initialData={data} />);

    fireEvent.click(screen.getByTestId("shell-duplicate-first"));
    fireEvent.click(screen.getByTestId("shell-remove-first"));

    await waitFor(() => expect(calls).toEqual(["duplicate"]));
    expect(screen.getByTestId("shell-pending")).toHaveTextContent("true");

    duplicate.resolve(
      jsonResponse({ ...data.items[0], id: "item-copy", position: 1 })
    );

    await waitFor(() => expect(calls).toEqual(["duplicate", "delete"]));
    // The source is gone, so the copy the server persisted is the only Item left.
    await waitFor(() => expect(screen.getByTestId("shell-positions")).toHaveTextContent("1"));
    expect(screen.getByTestId("shell-pending")).toHaveTextContent("false");
  });

  it("keeps duplicate requests in order instead of interleaving them", async () => {
    const data = initialData();
    const first = Promise.withResolvers<Response>();
    const second = Promise.withResolvers<Response>();
    let duplicateCalls = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/items/item-1/duplicate") && init?.method === "POST") {
        duplicateCalls += 1;
        return duplicateCalls === 1 ? first.promise : second.promise;
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<SongsetEditorClient songsetId="songset-1" initialData={data} />);

    fireEvent.click(screen.getByTestId("shell-duplicate-first"));
    fireEvent.click(screen.getByTestId("shell-duplicate-first"));

    await waitFor(() => expect(duplicateCalls).toBe(1));

    first.resolve(jsonResponse({ ...data.items[0], id: "item-copy-1", position: 1 }));
    await waitFor(() => expect(duplicateCalls).toBe(2));

    second.resolve(jsonResponse({ ...data.items[0], id: "item-copy-2", position: 1 }));
    await waitFor(() =>
      expect(screen.getByTestId("shell-positions")).toHaveTextContent("0,1,2")
    );
  });

  it("recomputes the Songset duration from the resulting Items", async () => {
    const data = initialData();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/items/item-1/duplicate") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            ...data.items[0],
            id: "item-copy",
            position: 1,
            recording: { ...data.items[0].recording, durationSeconds: 30 },
          })
        );
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<SongsetEditorClient songsetId="songset-1" initialData={data} />);
    expect(screen.getByTestId("shell-duration")).toHaveTextContent("20");

    fireEvent.click(screen.getByTestId("shell-duplicate-first"));

    // 20s + the copy's 2-beat gap at 120 BPM (1s) + 30s; no crossfade.
    await waitFor(() => expect(screen.getByTestId("shell-duration")).toHaveTextContent("51"));
    expect(screen.getByTestId("shell-item-count")).toHaveTextContent("2");
  });

  it("keeps a saved copy when its refresh fails and retries only the read", async () => {
    const data = initialData();
    const first = {
      ...data.items[0], glueEnabled: true, glueBeats: 8 as const,
      glue: readyJunction("item-1", "item-2"),
    };
    const second = { ...data.items[0], id: "item-2", position: 1 };
    const copy = {
      ...first, id: "item-copy", position: 1,
      glue: readyJunction("item-copy", "item-2"),
    };
    const savedFirst = {
      ...first,
      glue: { ...first.glue, toItemId: copy.id, state: "stale", selectedTake: null },
    };
    const read = vi.fn()
      .mockResolvedValueOnce(jsonResponse({}, false))
      .mockResolvedValueOnce(jsonResponse({
        ...data, itemCount: 3, durationSeconds: 67,
        items: [savedFirst, copy, { ...second, position: 2 }],
      }));
    const duplicate = vi.fn().mockResolvedValue(jsonResponse(copy));
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/duplicate") && init?.method === "POST") return duplicate();
      return read(input, init);
    }));
    render(<SongsetEditorClient songsetId="songset-1" initialData={{
      ...data, itemCount: 2, durationSeconds: 46, items: [first, second],
    }} />);

    fireEvent.click(screen.getByTestId("shell-duplicate-first"));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(screen.getByTestId("shell-positions")).toHaveTextContent("0,1,2");
    // The source's old A-to-B take must not contribute another six seconds.
    expect(screen.getByTestId("shell-duration")).toHaveTextContent("67");
    const refreshToast = vi.mocked(toast.error).mock.calls.at(-1)?.[1];
    const retry = refreshToast?.action;
    expect(retry).toMatchObject({ label: "Retry" });
    if (!retry || !("onClick" in retry)) throw new Error("Expected refresh retry");
    retry.onClick(new MouseEvent("click"));

    await waitFor(() =>
      expect(screen.getByTestId("shell-glue")).toHaveTextContent("item-1:stale:,item-copy:ready:take-1")
    );
    expect(duplicate).toHaveBeenCalledTimes(1);
    expect(read).toHaveBeenCalledTimes(2);
    expect(read).toHaveBeenCalledWith(
      "/api/songsets/songset-1", expect.objectContaining({ cache: "no-store" })
    );
  });

  it("retains a Glue PATCH while an append and reorder wait in the queue", async () => {
    const data = initialData();
    const first = data.items[0];
    const second = { ...first, id: "item-2", position: 1 };
    const third = { ...first, id: "item-3", position: 2 };
    const ready = {
      ...first, glueEnabled: true, glueBeats: 8, glue: readyJunction(first.id, second.id),
    };
    const patch = Promise.withResolvers<Response>();
    const appended = Promise.withResolvers<Response>();
    const calls: string[] = [];
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/items") && init?.method === "PATCH") {
        calls.push("patch");
        return patch.promise;
      }
      if (url.endsWith("/items") && init?.method === "POST") {
        calls.push("append");
        return appended.promise;
      }
      if (url.endsWith("/items/reorder")) {
        calls.push("reorder");
        return Promise.resolve(jsonResponse({ ok: true }));
      }
      if (url.endsWith("/api/songsets/songset-1")) {
        calls.push("read");
      }
      return Promise.resolve(jsonResponse({}, false));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SongsetEditorClient songsetId="songset-1" initialData={{
      ...data, itemCount: 2, durationSeconds: 41, items: [first, second],
    }} />);
    await openArrangementSheet();
    fireEvent.click(screen.getByTestId("arrangement-glue-toggle"));
    await waitFor(() => expect(calls).toEqual(["patch"]));
    fireEvent.click(screen.getByTestId("browse-add"));

    patch.resolve(jsonResponse(ready));
    await waitFor(() => expect(calls).toEqual(["patch", "append"]));
    fireEvent.click(screen.getByTestId("shell-reverse"));
    appended.resolve(jsonResponse(third));

    // An order captured before the append must not overwrite the new row or
    // the just-saved selection. The server refresh is intentionally failed
    // here so it cannot mask an obsolete ref on the optimistic path.
    await waitFor(() => expect(screen.getByTestId("shell-pending")).toHaveTextContent("false"));
    expect(calls).toEqual(["patch", "append", "read"]);
    expect(screen.getByTestId("shell-glue")).toHaveTextContent("item-1:ready:take-1");
    expect(screen.getByTestId("shell-item-count")).toHaveTextContent("3");
  });

  it("adopts the authoritative Songset when the duplicated source vanished", async () => {
    const data = initialData();
    const remove = Promise.withResolvers<Response>();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/items?itemId=item-1") && init?.method === "DELETE") {
        return remove.promise;
      }
      if (url.endsWith("/items/item-1/duplicate") && init?.method === "POST") {
        return Promise.resolve(jsonResponse({ ...data.items[0], id: "item-copy", position: 1 }));
      }
      if (url.endsWith("/api/songsets/songset-1")) {
        return Promise.resolve(
          jsonResponse({
            ...data,
            itemCount: 1,
            durationSeconds: 40,
            items: [{ ...data.items[0], id: "item-copy", position: 0 }],
          })
        );
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<SongsetEditorClient songsetId="songset-1" initialData={data} />);

    // The remove settles first, so the duplicate's own source is gone locally
    // when its 201 lands; the persisted copy must not be dropped.
    fireEvent.click(screen.getByTestId("shell-remove-first"));
    fireEvent.click(screen.getByTestId("shell-duplicate-first"));
    remove.resolve(jsonResponse({ success: true }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) => String(input).endsWith("/api/songsets/songset-1") && !init?.method
        )
      ).toBe(true)
    );
    await waitFor(() => expect(screen.getByTestId("shell-positions")).toHaveTextContent("0"));
    expect(screen.getByTestId("shell-item-count")).toHaveTextContent("1");
    expect(screen.getByTestId("shell-duration")).toHaveTextContent("40");
  });
});

describe("SongsetEditorClient arrangement save", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uses ordinary timing while the saved cut's Glue is unresolved", async () => {
    const data = initialData();
    const first = {
      ...data.items[0], glueEnabled: true, glueBeats: 8 as const,
      glue: readyJunction("item-1", "item-2"),
    };
    const second = { ...data.items[0], id: "item-2", position: 1 };
    const saved = JSON.stringify({
      version: 1,
      steps: [{ sectionIndex: 0, label: "Verse", startSeconds: 0, endSeconds: 10 }],
    });
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      Promise.resolve(init?.method === "PUT"
        ? jsonResponse({ arrangementSteps: saved })
        : jsonResponse({}, false))
    ));
    render(<SongsetEditorClient songsetId="songset-1" initialData={{
      ...data, itemCount: 2, durationSeconds: 46, items: [first, second],
    }} />);
    await openArrangementSheet();
    fireEvent.click(screen.getByTestId("arrangement-save"));

    await waitFor(() => expect(screen.getByTestId("shell-duration")).toHaveTextContent("31"));
    expect(screen.getByTestId("shell-glue")).not.toHaveTextContent("take-1");
  });

  it("keeps a transition edit that landed while the arrangement save was in flight", async () => {
    const saved = JSON.stringify({
      version: 1,
      steps: [
        { sectionIndex: 0, label: "Verse", startSeconds: 0, endSeconds: 10 },
      ],
    });
    const deferred = Promise.withResolvers<Response>();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "PUT") return deferred.promise;
      if (url.endsWith("/items") && init?.method === "PATCH") {
        return Promise.resolve(jsonResponse({ ok: true }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<SongsetEditorClient songsetId="songset-1" initialData={initialData()} />);
    await openArrangementSheet();

    fireEvent.click(screen.getByTestId("arrangement-save"));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")
      ).toBe(true)
    );

    // A transition PATCH commits while the arrangement PUT is still pending.
    fireEvent.click(screen.getByTestId("shell-update-transition"));
    await waitFor(() =>
      expect(screen.getByTestId("shell-gap")).toHaveTextContent("7")
    );

    deferred.resolve(jsonResponse({ arrangementSteps: saved }));
    await waitFor(() =>
      expect(screen.getByTestId("shell-arrangement")).toHaveTextContent(saved)
    );
    // The save applies its arrangement without reverting the newer transition.
    expect(screen.getByTestId("shell-gap")).toHaveTextContent("7");
  });

  it("bumps the displayed last-modified timestamp after a successful edit", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ ok: true })));

    render(<SongsetEditorClient songsetId="songset-1" initialData={initialData()} />);
    expect(screen.getByTestId("shell-updated-at")).toHaveTextContent(
      "2026-01-01T00:00:00.000Z"
    );

    fireEvent.click(screen.getByTestId("shell-update-transition"));

    await waitFor(() =>
      expect(screen.getByTestId("shell-updated-at")).not.toHaveTextContent(
        "2026-01-01T00:00:00.000Z"
      )
    );
  });

  it("unlocks audio in the click and starts the audition for the saved Songset", async () => {
    render(<SongsetEditorClient songsetId="songset-1" initialData={initialData()} />);

    fireEvent.click(screen.getByTestId("shell-listen"));

    expect(shell.unlockAudioContext).toHaveBeenCalledTimes(1);
    expect(shell.open).toHaveBeenCalledWith({
      songsetId: "songset-1",
      name: "Sunday Worship",
    });
  });

  it("reports the translated save error when the PUT fails on the network", async () => {
    const fetchMock = vi.fn(() => Promise.reject(new TypeError("Failed to fetch")));
    vi.stubGlobal("fetch", fetchMock);

    render(<SongsetEditorClient songsetId="songset-1" initialData={initialData()} />);
    await openArrangementSheet();

    fireEvent.click(screen.getByTestId("arrangement-save"));

    await waitFor(() =>
      expect(screen.getByTestId("arrangement-save-error")).toHaveTextContent(
        "Failed to save the arrangement."
      )
    );
    expect(screen.getByTestId("arrangement-save-error")).not.toHaveTextContent(
      /failed to fetch/i
    );
  });

  it("reports the API's reason when the render job can no longer be cancelled", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "DELETE") {
        return Promise.resolve(
          jsonResponse({ error: "Cannot cancel job with status: completed" }, false)
        );
      }
      // The watch poll that runs while renderState is "rendering".
      return Promise.resolve(
        jsonResponse({
          id: "job-1",
          status: "running",
          errorMessage: null,
          mp3R2Key: null,
          mp4R2Key: null,
          chaptersR2Key: null,
          estimatedTotalSeconds: null,
        })
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <SongsetEditorClient
        songsetId="songset-1"
        initialData={{
          ...initialData(),
          renderState: "rendering" as const,
          latestRenderJobId: "job-1",
        }}
      />
    );

    fireEvent.click(screen.getByTestId("shell-cancel-render"));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Cannot cancel job with status: completed"
      )
    );
    // A refused cancel leaves the in-flight render on screen.
    expect(screen.getByTestId("shell-render-state")).toHaveTextContent("rendering");
  });
});
