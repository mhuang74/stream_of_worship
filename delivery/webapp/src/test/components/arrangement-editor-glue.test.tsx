import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { Mock } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { ArrangementEditorSheet } from "@/components/songset/ArrangementEditorSheet";
import type { SongListItem } from "@/components/songset/SongList";
import type { ResolvedGlueJunction } from "@/lib/glue/resolve";

vi.mock("@/contexts/SongsetListenContext", () => ({
  useSongsetListen: () => ({ audio: { pause: vi.fn() } }),
}));

const audioMocks = vi.hoisted(() => ({
  play: vi.fn().mockResolvedValue(undefined),
  pause: vi.fn(),
  stop: vi.fn(),
  seek: vi.fn(),
  startAtSeconds: vi.fn().mockResolvedValue(undefined),
  prepareAt: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/hooks/useSongsetAudio", () => ({
  useSongsetAudio: () => ({
    status: "idle",
    errorMessage: null,
    timelinePositionSeconds: 0,
    totalSeconds: 120,
    progress: null,
    glueFailures: [],
    retryGlueAudio: vi.fn().mockResolvedValue(true),
    ...audioMocks,
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

const SECTIONS = [{ label: "Verse", start: 0, end: 30 }];
const BEATS = Array.from({ length: 16 }, (_, index) => index * 2);
const TAKE_ID = "6b7f9c34-1c2b-4c4d-8a3a-1b2c3d4e5f60";

function junction(overrides: Partial<ResolvedGlueJunction> = {}): ResolvedGlueJunction {
  return {
    fromItemId: "item-1",
    toItemId: "item-2",
    glueEnabled: true,
    glueBeats: 4,
    descriptorKey: "a".repeat(64),
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
    glueEnabled: false,
    glueBeats: 4,
    glue: junction({ state: "needs-generation", selectedTake: null }),
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
  uncachedSides: [
    { side: "A", seconds: 12, credits: 180 },
    { side: "B", seconds: 12, credits: 180 },
  ],
  generationSeconds: 3,
  uploadCredits: 360,
  generationCredits: 45,
  estimatedNewCredits: 405,
  blockedSides: [],
  quotaLimit: 5,
  quotaUsed: 2,
  quotaRemaining: 3,
  quotaResetAt: "2026-11-23T09:15:00.000Z",
};

// A stable identity keeps a re-render comparable to the previous one instead of
// looking like a different parent callback.
const NOOP_UPDATE_GLUE = async () => {};

function sheetElement(
  items: SongListItem[],
  onUpdateGlue?: (itemId: string, patch: Record<string, unknown>) => Promise<void>
) {
  return (
    <ArrangementEditorSheet
      isOpen
      onOpenChange={vi.fn()}
      item={items[0]}
      items={items}
      onSave={vi.fn().mockResolvedValue(undefined)}
      songsetId="songset-1"
      onUpdateGlue={onUpdateGlue ?? NOOP_UPDATE_GLUE}
    />
  );
}

function renderSheet(args: {
  items?: SongListItem[];
  onUpdateGlue?: (itemId: string, patch: Record<string, unknown>) => Promise<void>;
}) {
  const items = args.items ?? [makeItem(), successor()];
  return render(sheetElement(items, args.onUpdateGlue));
}

/** Quote requests only: the rail also polls `glue-jobs`, which is not a quote. */
function quoteCallCount(): number {
  return (fetch as unknown as Mock).mock.calls.filter(([url]) =>
    String(url).includes("glue-quote")
  ).length;
}

describe("ArrangementEditorSheet AI transition rail", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => QUOTE })
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("hides the AI transition controls when there is no successor", () => {
    renderSheet({ items: [makeItem()] });
    expect(screen.queryByTestId("arrangement-glue")).toBeNull();
  });

  it("shows the switch named for the next Song and the minimum-length caption", async () => {
    renderSheet({});
    expect(screen.getByTestId("arrangement-glue")).toBeTruthy();
    expect(
      screen.getByText(/AI transition to How Great Thou Art/)
    ).toBeTruthy();
    fireEvent.click(screen.getByTestId("arrangement-glue-toggle"));
    // 4 beats at 120 BPM is 2 s, raised to the 3 s floor: the caption says so
    // instead of printing a length the junction does not actually stage.
    await waitFor(() =>
      expect(
        screen.getByTestId("arrangement-glue-minimum-length").textContent
      ).toContain("about 3 s")
    );
    expect(screen.getByTestId("arrangement-glue-beats-4").getAttribute("aria-pressed")).toBe(
      "true"
    );
  });

  it("PATCHes the switch and puts the remaining count and reset behind the info icon", async () => {
    const onUpdateGlue = vi.fn().mockResolvedValue(undefined);
    renderSheet({ onUpdateGlue });
    fireEvent.click(screen.getByTestId("arrangement-glue-toggle"));
    await waitFor(() =>
      expect(onUpdateGlue).toHaveBeenCalledWith("item-1", { glueEnabled: true })
    );
    const info = await screen.findByTestId("arrangement-glue-quota-info");
    const label = info.getAttribute("aria-label") ?? "";
    expect(label).toContain("3 of 5 AI transition attempts remaining");
    expect(label).toContain("Resets at");
    expect(label).not.toContain("credits");
    // Nothing on the rail repeats the numbers outside the popover.
    expect(screen.queryByText(/attempts remaining/)).toBeNull();
  });

  it("rolls the switch back when the PATCH fails", async () => {
    const onUpdateGlue = vi.fn().mockRejectedValue(new Error("nope"));
    renderSheet({ onUpdateGlue });
    const toggle = screen.getByTestId("arrangement-glue-toggle");
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(screen.getByTestId("arrangement-glue-error")).toBeTruthy()
    );
    expect(toggle.getAttribute("aria-checked")).toBe("false");
  });

  it("sends the chosen beat count", async () => {
    const onUpdateGlue = vi.fn().mockResolvedValue(undefined);
    renderSheet({ onUpdateGlue });
    fireEvent.click(screen.getByTestId("arrangement-glue-toggle"));
    await waitFor(() => expect(onUpdateGlue).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId("arrangement-glue-beats-8"));
    await waitFor(() =>
      expect(onUpdateGlue).toHaveBeenCalledWith("item-1", { glueBeats: 8 })
    );
  });

  it("requests the quote without a browser cache so a beat change cannot replay the old quote", async () => {
    renderSheet({});
    fireEvent.click(screen.getByTestId("arrangement-glue-toggle"));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    const init = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls
      .map((call) => call[1] as RequestInit | undefined)
      .find((options) => options?.cache !== undefined);
    expect(init?.cache).toBe("no-store");
  });

  it("never shows a quote computed for a different descriptor", async () => {
    const staleQuote = { ...QUOTE, descriptorKey: "b".repeat(64) };
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async () => {
        calls += 1;
        // The first answer describes the pre-PATCH junction; the retry sees the
        // saved one.
        return { ok: true, json: async () => (calls === 1 ? staleQuote : QUOTE) };
      })
    );
    renderSheet({});
    fireEvent.click(screen.getByTestId("arrangement-glue-toggle"));
    const quote = await screen.findByTestId("arrangement-glue-quota-info");
    expect(quote.textContent).not.toContain("credits");
    expect(calls).toBeGreaterThan(1);
  });

  it("surfaces a quote error instead of numbers for a junction it cannot confirm", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async () => ({
        ok: true,
        json: async () => ({ ...QUOTE, descriptorKey: "b".repeat(64) }),
      }))
    );
    // With a resolved junction the rail knows which descriptor the answer must
    // describe; a replica that keeps returning the previous one is never shown.
    renderSheet({
      items: [makeItem({ glueEnabled: true, glue: junction() }), successor()],
    });
    expect(
      await screen.findByText(/Could not load the AI transition details/, {}, { timeout: 5000 })
    ).toBeTruthy();
    expect(screen.queryByTestId("arrangement-glue-quota-info")).toBeNull();
  });

  it("does not request a quote until the enabling PATCH lands", async () => {
    const patch = Promise.withResolvers<void>();
    const onUpdateGlue = vi.fn(() => patch.promise);
    renderSheet({ onUpdateGlue });
    fireEvent.click(screen.getByTestId("arrangement-glue-toggle"));
    await waitFor(() => expect(onUpdateGlue).toHaveBeenCalled());
    expect(quoteCallCount()).toBe(0);
    patch.resolve();
    expect(await screen.findByTestId("arrangement-glue-quota-info")).toBeTruthy();
  });

  it("retries a quote refused because the enable was not visible yet", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (url: unknown) => {
        if (!String(url).includes("glue-quote")) {
          return { ok: true, json: async () => [] };
        }
        calls += 1;
        return calls === 1
          ? { ok: false, json: async () => ({ code: "GLUE_NOT_ENABLED" }) }
          : { ok: true, json: async () => QUOTE };
      })
    );
    renderSheet({});
    fireEvent.click(screen.getByTestId("arrangement-glue-toggle"));
    expect(await screen.findByTestId("arrangement-glue-quota-info")).toBeTruthy();
    expect(calls).toBe(2);
    expect(screen.queryByText(/Could not load the AI transition details/)).toBeNull();
  });

  it("re-quotes after the saved item changes and drops the old failure", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (url: unknown) => {
        if (!String(url).includes("glue-quote")) {
          return { ok: true, json: async () => [] };
        }
        calls += 1;
        return calls === 1
          ? { ok: false, json: async () => ({ code: "GLUE_ARRANGEMENT_INVALID" }) }
          : { ok: true, json: async () => QUOTE };
      })
    );
    const { rerender } = renderSheet({
      items: [makeItem({ glueEnabled: true, glue: junction() }), successor()],
    });
    expect(
      await screen.findByText("Fix the arrangement before creating an AI transition.")
    ).toBeTruthy();
    rerender(
      sheetElement([
        makeItem({
          glueEnabled: true,
          glue: junction(),
          updatedAt: "2026-11-22T11:00:00.000Z",
        }),
        successor(),
      ])
    );
    expect(await screen.findByTestId("arrangement-glue-quota-info")).toBeTruthy();
    expect(
      screen.queryByText("Fix the arrangement before creating an AI transition.")
    ).toBeNull();
    expect(calls).toBe(2);
  });

  it("shows the selected AI transition and its plain-language check warning", async () => {
    const take = {
      id: TAKE_ID,
      ordinal: 1,
      seconds: 2.8,
      beats: 4,
      r2Key: "glues/take.wav",
      descriptorKey: "a".repeat(64),
      status: "check" as const,
      reasons: ["ending-energy"],
    };
    renderSheet({
      items: [
        makeItem({
          glueEnabled: true,
          glue: junction({
            selectedTake: take,
            availableTakes: [{ ...take, createdAt: "2026-11-22T09:00:00.000Z" }],
          }),
        }),
        successor(),
      ],
    });
    const select = screen.getByTestId("arrangement-glue-takes");
    expect(select.textContent).toContain("Version 1");
    expect(select.textContent).toContain("needs a listen");
    expect(screen.getByText(/Give it a listen/)).toBeTruthy();
  });

  it("names the beats change and offers the matching version for free", async () => {
    const staleTake = {
      id: TAKE_ID,
      ordinal: 1,
      seconds: 5,
      beats: 4,
      r2Key: "glues/old.wav",
      descriptorKey: "a".repeat(64),
      status: "ok" as const,
      reasons: [],
      createdAt: "2026-11-22T09:00:00.000Z",
    };
    const matching = {
      id: "take-matching",
      ordinal: 2,
      seconds: 5,
      beats: 8,
      r2Key: "glues/new.wav",
      descriptorKey: "c".repeat(64),
      status: "ok" as const,
      reasons: [],
      createdAt: "2026-11-22T11:00:00.000Z",
    };
    // The heal PATCH never settles, so the button's saving state is observable.
    const onUpdateGlue = vi.fn(() => new Promise<void>(() => {}));
    renderSheet({
      items: [
        makeItem({
          glueEnabled: true,
          glueBeats: 8,
          glue: junction({
            glueBeats: 8,
            state: "stale",
            staleChange: { change: "beats", storedBeats: 4, currentBeats: 8 },
            staleTake,
            availableTakes: [matching],
          }),
        }),
        successor(),
      ],
      onUpdateGlue,
    });

    expect(screen.getByTestId("arrangement-glue-reason").textContent).toContain(
      "Transition length changed from 4 to 8 beats"
    );
    const heal = screen.getByTestId("arrangement-glue-use-matching");
    fireEvent.click(heal);
    expect(onUpdateGlue).toHaveBeenCalledWith("item-1", {
      glueActiveTakeId: "take-matching",
    });
    await waitFor(() => expect(heal.hasAttribute("disabled")).toBe(true));
  });

  it("offers no matching version when the stored take is the only one", () => {
    renderSheet({
      items: [
        makeItem({
          glueEnabled: true,
          glueBeats: 8,
          glue: junction({
            glueBeats: 8,
            state: "stale",
            staleChange: { change: "beats", storedBeats: 4, currentBeats: 8 },
            staleTake: {
              id: TAKE_ID,
              ordinal: 1,
              seconds: 5,
              beats: 4,
              r2Key: "glues/old.wav",
              descriptorKey: "a".repeat(64),
              status: "ok",
              reasons: [],
              createdAt: "2026-11-22T09:00:00.000Z",
            },
            availableTakes: [],
          }),
        }),
        successor(),
      ],
    });
    expect(screen.getByTestId("arrangement-glue-reason").textContent).toContain(
      "Transition length changed from 4 to 8 beats"
    );
    expect(screen.queryByTestId("arrangement-glue-use-matching")).toBeNull();
  });

  it("pauses the preview transport before auditioning a stale take", async () => {
    const play = vi.fn().mockResolvedValue(undefined);
    const pauseElement = vi.fn();
    vi.stubGlobal(
      "Audio",
      class {
        paused = true;
        src = "";
        onended: (() => void) | null = null;
        onerror: (() => void) | null = null;
        play = play;
        pause = pauseElement;
      }
    );
    renderSheet({
      items: [
        makeItem({
          glueEnabled: true,
          glueBeats: 8,
          glue: junction({
            glueBeats: 8,
            state: "stale",
            staleChange: { change: "beats", storedBeats: 4, currentBeats: 8 },
            staleTake: {
              id: TAKE_ID,
              ordinal: 1,
              seconds: 5,
              beats: 4,
              r2Key: "glues/old.wav",
              descriptorKey: "a".repeat(64),
              status: "ok",
              reasons: [],
              createdAt: "2026-11-22T09:00:00.000Z",
            },
            availableTakes: [],
          }),
        }),
        successor(),
      ],
    });

    fireEvent.click(screen.getByTestId("arrangement-glue-stale-play"));
    // The two transports never sound together, in either direction.
    expect(audioMocks.pause).toHaveBeenCalled();
    expect(play).toHaveBeenCalledTimes(1);

    // Tapping again silences the element.
    await waitFor(() =>
      expect(
        screen.getByTestId("arrangement-glue-stale-play").getAttribute("aria-label")
      ).toBeTruthy()
    );
    fireEvent.click(screen.getByTestId("arrangement-glue-stale-play"));
    expect(pauseElement).toHaveBeenCalled();
  });
});
