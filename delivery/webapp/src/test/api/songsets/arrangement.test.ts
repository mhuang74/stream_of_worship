import { describe, it, expect, beforeEach, vi } from "vitest";
import { PUT } from "@/app/api/songsets/[id]/items/[itemId]/arrangement/route";
import { auth } from "@/lib/auth";
import {
  SongsetRenderValidationError,
  updateSongArrangement,
  type SongsetItemDetail,
} from "@/lib/db/songsets";
import { ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE } from "@/lib/arrangements";
import { NextRequest } from "next/server";

vi.mock("@/lib/auth", () => ({
  auth: {
    api: {
      getSession: vi.fn(),
    },
  },
}));

// The route's real dependency module is partially mocked below; its own
// `@/db` import must not require a live database URL in unit tests.
vi.mock("@/db", () => ({ db: {} }));

// The real songsets module imports the Pool-backed mutation client; stub it so
// these route tests never require a database URL.
vi.mock("@/db/mutation-db", () => ({ mutationDb: { transaction: vi.fn() } }));

vi.mock("@/lib/db/songsets", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/db/songsets")>();
  return {
    ...actual,
    updateSongArrangement: vi.fn(),
  };
});

const mockUpdateSongArrangement = vi.mocked(updateSongArrangement);

/** Session mock: the route only reads `user.id`. */
function mockSignedIn() {
  vi.mocked(auth.api.getSession).mockResolvedValue({
    user: { id: 1 },
  } as unknown as Awaited<ReturnType<typeof auth.api.getSession>>);
}

function createMockRequest(url: string, options?: RequestInit): NextRequest {
  const request = new Request(url, options) as unknown as NextRequest;
  const urlObj = new URL(url);
  Object.defineProperty(request, "nextUrl", {
    value: urlObj,
    writable: false,
  });
  return request;
}

const URL_UNDER_TEST =
  "http://localhost:3000/api/songsets/songset-1/items/item-1/arrangement";

const params = { params: { id: "songset-1", itemId: "item-1" } };

const arrangement = {
  version: 1 as const,
  steps: [
    { sectionIndex: 0, label: "intro", startSeconds: 0, endSeconds: 4.5 },
    { sectionIndex: 2, label: "chorus", startSeconds: 30, endSeconds: 45 },
    { sectionIndex: 2, label: "chorus", startSeconds: 30, endSeconds: 45 },
  ],
};

const detailResponse = {
  id: "item-1",
  songId: "song-1",
  recordingHashPrefix: "abc123",
  position: 0,
  gapBeats: 2,
  crossfadeEnabled: 0,
  crossfadeDurationSeconds: null,
  keyShiftSemitones: 0,
  tempoRatio: 1,
  arrangementSteps: JSON.stringify(arrangement),
  arrangement,
  arrangementInvalid: false,
  markedLineCount: 0,
  song: null,
  recording: {
    contentHash: "hash-1",
    durationSeconds: 180,
    tempoBpm: 100,
    musicalKey: "C",
    effectiveKey: null,
    effectiveKeySource: null,
    effectiveKeyStartRoot: null,
    effectiveKeyEndRoot: null,
    effectiveKeyMode: null,
    effectiveKeyStartPitchClass: null,
    effectiveKeyEndPitchClass: null,
    r2AudioUrl: null,
    theme: null,
    sections: [{ label: "intro", start: 0, end: 4.5 }],
    beats: [0, 1, 2],
  },
} as unknown as SongsetItemDetail;

describe("PUT /api/songsets/[id]/items/[itemId]/arrangement", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest(URL_UNDER_TEST, {
      method: "PUT",
      body: JSON.stringify({ arrangement }),
    });
    const response = await PUT(request, params);

    expect(response.status).toBe(401);
    expect(mockUpdateSongArrangement).not.toHaveBeenCalled();
  });

  it("returns 400 when the body is not valid JSON", async () => {
    mockSignedIn();

    const request = createMockRequest(URL_UNDER_TEST, {
      method: "PUT",
      body: "{not-json",
    });
    const response = await PUT(request, params);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
    expect(mockUpdateSongArrangement).not.toHaveBeenCalled();
  });

  it("returns 400 when arrangement is missing", async () => {
    mockSignedIn();

    const request = createMockRequest(URL_UNDER_TEST, {
      method: "PUT",
      body: JSON.stringify({}),
    });
    const response = await PUT(request, params);

    expect(response.status).toBe(400);
    expect(mockUpdateSongArrangement).not.toHaveBeenCalled();
  });

  it("returns 400 for a strict-schema violation (extra step field)", async () => {
    mockSignedIn();

    const request = createMockRequest(URL_UNDER_TEST, {
      method: "PUT",
      body: JSON.stringify({
        arrangement: {
          version: 1,
          steps: [
            {
              sectionIndex: 0,
              label: "intro",
              startSeconds: 0,
              endSeconds: 4,
              forged: true,
            },
          ],
        },
      }),
    });
    const response = await PUT(request, params);

    expect(response.status).toBe(400);
    expect(mockUpdateSongArrangement).not.toHaveBeenCalled();
  });

  it("accepts a manual-bounds Step and a null-index inferred Intro", async () => {
    mockSignedIn();
    mockUpdateSongArrangement.mockResolvedValue(detailResponse);

    const manualArrangement = {
      version: 1 as const,
      steps: [
        {
          sectionIndex: null,
          label: "intro",
          startSeconds: 0,
          endSeconds: 12.4,
          manualBounds: true as const,
        },
        {
          sectionIndex: 1,
          label: "verse",
          startSeconds: 12.3,
          endSeconds: 28.7,
          manualBounds: true as const,
        },
      ],
    };
    const response = await PUT(
      createMockRequest(URL_UNDER_TEST, {
        method: "PUT",
        body: JSON.stringify({ arrangement: manualArrangement }),
      }),
      params
    );

    expect(response.status).toBe(200);
    expect(mockUpdateSongArrangement).toHaveBeenCalledWith(
      "item-1",
      "songset-1",
      1,
      manualArrangement
    );
  });

  it("returns 400 for a manualBounds value that is not the literal true", async () => {
    mockSignedIn();

    for (const manualBounds of [false, 1, "true"] as const) {
      const response = await PUT(
        createMockRequest(URL_UNDER_TEST, {
          method: "PUT",
          body: JSON.stringify({
            arrangement: {
              version: 1,
              steps: [
                {
                  sectionIndex: 0,
                  label: "intro",
                  startSeconds: 0,
                  endSeconds: 4,
                  manualBounds,
                },
              ],
            },
          }),
        }),
        params
      );
      expect(response.status).toBe(400);
    }
    expect(mockUpdateSongArrangement).not.toHaveBeenCalled();
  });

  it("returns 400 for a negative section index", async () => {
    mockSignedIn();

    const response = await PUT(
      createMockRequest(URL_UNDER_TEST, {
        method: "PUT",
        body: JSON.stringify({
          arrangement: {
            version: 1,
            steps: [
              { sectionIndex: -1, label: "intro", startSeconds: 0, endSeconds: 4 },
            ],
          },
        }),
      }),
      params
    );

    expect(response.status).toBe(400);
    expect(mockUpdateSongArrangement).not.toHaveBeenCalled();
  });

  it("returns 400 when steps exceed the 64-Step maximum", async () => {
    mockSignedIn();

    const request = createMockRequest(URL_UNDER_TEST, {
      method: "PUT",
      body: JSON.stringify({
        arrangement: {
          version: 1,
          steps: Array.from({ length: 65 }, () => ({
            sectionIndex: 0,
            label: "intro",
            startSeconds: 0,
            endSeconds: 1,
          })),
        },
      }),
    });
    const response = await PUT(request, params);

    expect(response.status).toBe(400);
    expect(mockUpdateSongArrangement).not.toHaveBeenCalled();
  });

  it("returns 400 when version is not 1", async () => {
    mockSignedIn();

    const request = createMockRequest(URL_UNDER_TEST, {
      method: "PUT",
      body: JSON.stringify({ arrangement: { version: 2, steps: arrangement.steps } }),
    });
    const response = await PUT(request, params);

    expect(response.status).toBe(400);
  });

  it("counts label length in code points at the wire boundary", async () => {
    mockSignedIn();
    mockUpdateSongArrangement.mockResolvedValue(detailResponse);

    const emojiSteps = [
      {
        sectionIndex: 0,
        label: "😀".repeat(100),
        startSeconds: 0,
        endSeconds: 4,
      },
    ];
    const accepted = await PUT(
      createMockRequest(URL_UNDER_TEST, {
        method: "PUT",
        body: JSON.stringify({
          arrangement: { version: 1, steps: emojiSteps },
        }),
      }),
      params
    );
    expect(accepted.status).toBe(200);

    mockUpdateSongArrangement.mockClear();
    const rejected = await PUT(
      createMockRequest(URL_UNDER_TEST, {
        method: "PUT",
        body: JSON.stringify({
          arrangement: {
            version: 1,
            steps: [{ ...emojiSteps[0], label: "😀".repeat(101) }],
          },
        }),
      }),
      params
    );
    expect(rejected.status).toBe(400);
    expect(mockUpdateSongArrangement).not.toHaveBeenCalled();
  });

  it("returns 404 when the item is missing or unowned", async () => {
    mockSignedIn();
    mockUpdateSongArrangement.mockResolvedValue(null);

    const request = createMockRequest(URL_UNDER_TEST, {
      method: "PUT",
      body: JSON.stringify({ arrangement }),
    });
    const response = await PUT(request, params);

    expect(response.status).toBe(404);
    const data = await response.json();
    expect(data.error).toBe("Songset item not found");
  });

  it("saves the arrangement and returns the editor detail shape", async () => {
    mockSignedIn();
    mockUpdateSongArrangement.mockResolvedValue(detailResponse);

    const request = createMockRequest(URL_UNDER_TEST, {
      method: "PUT",
      body: JSON.stringify({ arrangement }),
    });
    const response = await PUT(request, params);

    expect(response.status).toBe(200);
    expect(mockUpdateSongArrangement).toHaveBeenCalledWith(
      "item-1",
      "songset-1",
      1,
      arrangement
    );
    const data = await response.json();
    expect(data.id).toBe("item-1");
    expect(data.arrangementSteps).toBe(JSON.stringify(arrangement));
    expect(data.arrangement.steps).toHaveLength(3);
    expect(data.arrangementInvalid).toBe(false);
    expect(data.recording.sections).toHaveLength(1);
    expect(data.recording.beats).toEqual([0, 1, 2]);
  });

  it("resets with null and succeeds", async () => {
    mockSignedIn();
    mockUpdateSongArrangement.mockResolvedValue({
      ...detailResponse,
      arrangementSteps: null,
      arrangement: null,
      arrangementInvalid: false,
    });

    const request = createMockRequest(URL_UNDER_TEST, {
      method: "PUT",
      body: JSON.stringify({ arrangement: null }),
    });
    const response = await PUT(request, params);

    expect(response.status).toBe(200);
    expect(mockUpdateSongArrangement).toHaveBeenCalledWith(
      "item-1",
      "songset-1",
      1,
      null
    );
    const data = await response.json();
    expect(data.arrangementSteps).toBeNull();
    expect(data.arrangement).toBeNull();
  });

  it("returns 409 ARRANGEMENT_ANALYSIS_UNAVAILABLE when analysis is missing", async () => {
    mockSignedIn();
    mockUpdateSongArrangement.mockRejectedValue(
      new SongsetRenderValidationError("ARRANGEMENT_ANALYSIS_UNAVAILABLE")
    );

    const request = createMockRequest(URL_UNDER_TEST, {
      method: "PUT",
      body: JSON.stringify({ arrangement }),
    });
    const response = await PUT(request, params);

    expect(response.status).toBe(409);
    const data = await response.json();
    expect(data.code).toBe("ARRANGEMENT_ANALYSIS_UNAVAILABLE");
    expect(data.error).toBe(ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE);
  });

  it("returns 409 ARRANGEMENT_STALE for a second save after analysis changed", async () => {
    mockSignedIn();
    mockUpdateSongArrangement.mockRejectedValue(
      new SongsetRenderValidationError("ARRANGEMENT_STALE")
    );

    const request = createMockRequest(URL_UNDER_TEST, {
      method: "PUT",
      body: JSON.stringify({ arrangement }),
    });
    const response = await PUT(request, params);

    expect(response.status).toBe(409);
    const data = await response.json();
    expect(data.code).toBe("ARRANGEMENT_STALE");
  });

  it("returns 400 SONGSET_TOO_LONG when the selected total exceeds 1500s", async () => {
    mockSignedIn();
    mockUpdateSongArrangement.mockRejectedValue(
      new SongsetRenderValidationError(
        "SONGSET_TOO_LONG",
        "Songset exceeds maximum duration of 25 minutes"
      )
    );

    const request = createMockRequest(URL_UNDER_TEST, {
      method: "PUT",
      body: JSON.stringify({ arrangement }),
    });
    const response = await PUT(request, params);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.code).toBe("SONGSET_TOO_LONG");
    expect(data.error).toContain("maximum duration");
  });

  it("returns 500 on unexpected error", async () => {
    mockSignedIn();
    mockUpdateSongArrangement.mockRejectedValue(new Error("Database error"));

    const request = createMockRequest(URL_UNDER_TEST, {
      method: "PUT",
      body: JSON.stringify({ arrangement }),
    });
    const response = await PUT(request, params);

    expect(response.status).toBe(500);
    const data = await response.json();
    expect(data.error).toBe("Failed to save song arrangement");
  });
});
