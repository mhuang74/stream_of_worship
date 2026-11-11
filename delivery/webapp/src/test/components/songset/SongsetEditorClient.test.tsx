import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import type { SongListItem } from "@/components/songset/SongList";
import type { TransitionSettings } from "@/components/songset/TransitionPanel";
import { SongsetEditorClient } from "@/app/songsets/[id]/SongsetEditorClient";

const shell = vi.hoisted(() => ({ push: vi.fn(), unlockAudioContext: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: shell.push }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/offline/offline-index", () => ({
  getOfflineRecord: vi.fn().mockResolvedValue(null),
  removeOfflineSongset: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
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

// Sheets other than the arrangement editor are out of scope for this slice.
vi.mock("@/components/songset/BrowseSheet", () => ({
  BrowseSheet: () => null,
}));
vi.mock("@/components/share/ShareDialog", () => ({
  ShareDialog: () => null,
}));

vi.mock("@/lib/audio/audio-context", () => ({
  unlockAudioContext: shell.unlockAudioContext,
  getAudioContext: vi.fn(),
}));

vi.mock("@/components/songset/ListenMixDialog", () => ({
  ListenMixDialog: (props: {
    songsetId: string;
    songsetName: string;
    isOpen: boolean;
    onOpenChange: (open: boolean) => void;
  }) =>
    props.isOpen ? (
      <div
        data-testid="listen-dialog-mock"
        data-songset-id={props.songsetId}
        data-songset-name={props.songsetName}
      >
        <button
          type="button"
          data-testid="listen-dialog-close"
          onClick={() => props.onOpenChange(false)}
        />
      </div>
    ) : null,
}));

/**
 * Minimal editor-shell stub: it renders the item state the client owns and
 * exposes the two callbacks this slice exercises, so the assertions read the
 * real hook state instead of component wiring.
 */
vi.mock("@/components/songset/SongsetEditor", () => ({
  SongsetEditor: (props: {
    items: SongListItem[];
    onListen: () => void;
    onUpdateTransition: (
      itemId: string,
      settings: TransitionSettings
    ) => Promise<void>;
    onEditArrangement: (itemId: string) => void;
  }) => {
    const item = props.items[0];
    return (
      <div>
        <span data-testid="shell-gap">{item?.gapBeats}</span>
        <span data-testid="shell-arrangement">{item?.arrangementSteps}</span>
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

/** Opens the (real) arrangement sheet for the single seeded item. */
async function openArrangementSheet() {
  fireEvent.click(screen.getByTestId("shell-edit-arrangement"));
  await screen.findByTestId("arrangement-sheet");
}

describe("SongsetEditorClient arrangement save", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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

  it("unlocks audio in the click and mounts the Listen dialog for the saved Songset", async () => {
    render(<SongsetEditorClient songsetId="songset-1" initialData={initialData()} />);

    fireEvent.click(screen.getByTestId("shell-listen"));

    expect(shell.unlockAudioContext).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("listen-dialog-mock")).toHaveAttribute(
      "data-songset-id",
      "songset-1"
    );
    expect(screen.getByTestId("listen-dialog-mock")).toHaveAttribute(
      "data-songset-name",
      "Sunday Worship"
    );

    fireEvent.click(screen.getByTestId("listen-dialog-close"));

    await waitFor(() => {
      expect(
        screen.queryByTestId("listen-dialog-mock")
      ).not.toBeInTheDocument();
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
});
