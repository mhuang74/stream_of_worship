import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import type { CastTransportResult, CastMedia } from "@/hooks/useCast";

// Mock next/navigation
const mockPush = vi.fn();
const mockReplace = vi.fn();
// Use a stable object so useRouter() returns the same reference on every render,
// preventing useEffect([songsetId, router]) from re-running on each re-render.
const mockRouterInstance = { push: mockPush, replace: mockReplace };
vi.mock("next/navigation", () => ({
  useRouter: () => mockRouterInstance,
  useParams: () => ({ id: "test-songset", token: "share-tok" }),
}));

// Mock sonner toast (hoisted so the factory can reference the mocks).
const {
  toastError,
  toastSuccess,
  toastInfo,
} = vi.hoisted(() => ({
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
  toastInfo: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: {
    success: toastSuccess,
    error: toastError,
    info: toastInfo,
  },
}));

import ControllerPage from "@/app/songsets/[id]/play/controller/page";
import ShareControllerPage from "@/app/share/[token]/play/controller/page";
import SharePage from "@/app/share/[token]/page";

// --- Transport hook mocks -------------------------------------------------

function makeTransport(overrides: Partial<CastTransportResult> = {}): CastTransportResult {
  return {
    isSupported: true,
    availability: "available" as const,
    isConnecting: false,
    isConnected: false,
    deviceName: "",
    playerState: "",
    currentTime: 0,
    duration: 0,
    volume: 1,
    isMuted: false,
    bufferingSinceMs: null,
    lastError: null,
    resumeProposal: null,
    start: vi.fn(),
    stop: vi.fn(),
    play: vi.fn(),
    pause: vi.fn(),
    seek: vi.fn(),
    setVolume: vi.fn(),
    setMuted: vi.fn(),
    onError: vi.fn(),
    ...overrides,
  };
}

function makeSender(overrides: Partial<{
  isSupported: boolean;
  isConnected: boolean;
  start: ReturnType<typeof vi.fn>;
  stop: ReturnType<typeof vi.fn>;
  send: ReturnType<typeof vi.fn>;
}> = {}) {
  return {
    isSupported: true,
    isConnected: false,
    start: vi.fn(),
    stop: vi.fn(),
    send: vi.fn(),
    ...overrides,
  };
}

const castTransportMock = vi.fn();
const presentationSenderMock = vi.fn();

vi.mock("@/hooks/useCast", () => ({
  useCastTransport: (opts: unknown) => castTransportMock(opts),
}));

vi.mock("@/hooks/usePresentation", () => ({
  usePresentationSender: (opts: unknown) => presentationSenderMock(opts),
}));

// --- ControllerPlayer mock (captures the unified transport props) ---------

interface CapturedControllerProps {
  playerId: string;
  videoSrc: string;
  chapters: unknown[];
  isPresentationActive: boolean;
  transport?: CastTransportResult;
  presentationFallback?: { isSupported: boolean; isConnected?: boolean };
  presentationMediaStatus?: unknown;
  isCastSupported?: boolean;
  isCastConnecting?: boolean;
  onSendToTV?: () => void;
  onStopPresentation?: () => void;
  onSendTransportCommand?: (cmd: unknown) => void;
  exitRoute?: string;
  autoFullscreen?: boolean;
}

let lastControllerProps: CapturedControllerProps | null = null;
vi.mock("@/components/play/ControllerPlayer", () => ({
  ControllerPlayer: (props: CapturedControllerProps) => {
    lastControllerProps = props;
    return (
      <div data-testid="controller-player">
        <div data-testid="video-src">{props.videoSrc}</div>
        <div data-testid="chapters-count">{props.chapters.length}</div>
        <div data-testid="presentation-active">
          {props.isPresentationActive ? "true" : "false"}
        </div>
        <div data-testid="cast-supported">
          {props.isCastSupported ? "true" : "false"}
        </div>
        <div data-testid="cast-connecting">
          {props.isCastConnecting ? "true" : "false"}
        </div>
        <div data-testid="presentation-fallback-supported">
          {props.presentationFallback?.isSupported ? "true" : "false"}
        </div>
        <button
          data-testid="send-to-tv"
          onClick={() => props.onSendToTV?.()}
        >
          send
        </button>
        <button
          data-testid="send-cmd"
          onClick={() => props.onSendTransportCommand?.({ type: "play" })}
        >
          cmd
        </button>
        <button
          data-testid="stop-presentation"
          onClick={() => props.onStopPresentation?.()}
        >
          stop
        </button>
      </div>
    );
  },
}));

// --- Fixtures --------------------------------------------------------------

const SONGSET_RESPONSE = {
  id: "test-songset",
  name: "Test Songset",
  renderState: "fresh",
  latestRenderJobId: "job-1",
  lastFailedRenderJobId: null,
  lastCompletedRenderJobId: "job-1",
};

const RENDER_JOB_RESPONSE = {
  id: "job-1",
  status: "completed",
  mp4R2Key: "videos/test.mp4",
  chaptersR2Key: null,
};

const SIGNED_URL_RESPONSE = {
  url: "https://r2.example.com/videos/test.mp4",
};

// Shared success fetch chain for the songset controller.
function songsetSuccessFetches() {
  global.fetch = vi
    .fn()
    .mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(SONGSET_RESPONSE),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(RENDER_JOB_RESPONSE),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(SIGNED_URL_RESPONSE),
    });
}

const SHARE_RESPONSE = {
  token: "share-tok",
  shareType: "songset",
  songset: { id: "ss-1", name: "Shared Set Name" },
  playback: {
    mp4Url: "https://r2.example.com/share/video.mp4",
    chaptersUrl: null,
  },
};

describe("ControllerPage (songset)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    lastControllerProps = null;
    castTransportMock.mockImplementation(() => makeTransport());
    presentationSenderMock.mockImplementation(() => makeSender());
  });

  describe("loading state", () => {
    it("shows loading spinner while fetching data", async () => {
      global.fetch = vi.fn().mockImplementation(() => new Promise(() => {}));

      render(<ControllerPage />);

      expect(screen.getByText(/loading player/i)).toBeInTheDocument();
    });
  });

  describe("error state", () => {
    it("shows error when songset not found", async () => {
      global.fetch = vi.fn().mockResolvedValueOnce({
        ok: false,
        status: 404,
      });

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByText(/songset not found/i)).toBeInTheDocument();
      });
    });

    it("shows error when render job not found", async () => {
      global.fetch = vi.fn().mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            ...SONGSET_RESPONSE,
            renderState: "unrendered",
            latestRenderJobId: null,
          }),
      });

      render(<ControllerPage />);

      await waitFor(() => {
        expect(
          screen.getByText(/songset has not been rendered yet/i)
        ).toBeInTheDocument();
      });
    });

    it("shows error when video not available", async () => {
      global.fetch = vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve(SONGSET_RESPONSE),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({ ...RENDER_JOB_RESPONSE, mp4R2Key: null }),
        });

      render(<ControllerPage />);

      await waitFor(() => {
        expect(
          screen.getByText(/no video available for this songset/i)
        ).toBeInTheDocument();
      });
    });

    it("shows go back button on error", async () => {
      global.fetch = vi.fn().mockResolvedValueOnce({
        ok: false,
        status: 404,
      });

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByText(/go back/i)).toBeInTheDocument();
      });
    });
  });

  describe("success state", () => {
    it("renders ControllerPlayer when data loaded", async () => {
      songsetSuccessFetches();

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });
    });

    it("passes video URL to ControllerPlayer", async () => {
      songsetSuccessFetches();

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("video-src")).toHaveTextContent(
          "https://r2.example.com/videos/test.mp4"
        );
      });
    });

    it("mints the signed MP4 URL with cast=true (4-hour Cast expiry)", async () => {
      const fetchMock = vi.fn().mockImplementation((url: string) => {
        if (typeof url === "string" && url.includes("/api/songsets/")) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve(SONGSET_RESPONSE) });
        }
        if (typeof url === "string" && url.includes("/api/render-jobs/")) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve(RENDER_JOB_RESPONSE) });
        }
        if (typeof url === "string" && url.startsWith("/api/signed-url")) {
          // Capture the URL so we can assert the cast flag is present.
          (fetchMock as unknown as { lastSignedUrl: string }).lastSignedUrl = url;
          return Promise.resolve({ ok: true, json: () => Promise.resolve(SIGNED_URL_RESPONSE) });
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      });
      global.fetch = fetchMock as unknown as typeof fetch;

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      const captured = (fetchMock as unknown as { lastSignedUrl: string }).lastSignedUrl;
      expect(captured).toContain("fileType=video");
      expect(captured).toContain("cast=true");
    });

    it("loads chapters when chaptersR2Key present", async () => {
      global.fetch = vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve(SONGSET_RESPONSE),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({ ...RENDER_JOB_RESPONSE, chaptersR2Key: "chapters/test.json" }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve(SIGNED_URL_RESPONSE),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              chapters: [
                {
                  position: 0,
                  songTitle: "Amazing Grace",
                  startSeconds: 0,
                  endSeconds: 180,
                  lines: [],
                },
              ],
            }),
        });

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("chapters-count")).toHaveTextContent("1");
      });
    });
  });

  describe("authentication", () => {
    it("redirects to login on 401", async () => {
      global.fetch = vi.fn().mockResolvedValueOnce({
        ok: false,
        status: 401,
      });

      render(<ControllerPage />);

      await waitFor(() => {
        expect(mockPush).toHaveBeenCalledWith("/login");
      });
    });
  });

  describe("transport wiring", () => {
    it("passes correct presentationUrl to usePresentationSender", async () => {
      songsetSuccessFetches();

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      const senderOpts = presentationSenderMock.mock.calls[0][0] as {
        presentationUrl: string;
      };
      expect(senderOpts.presentationUrl).toBe(
        "/songsets/test-songset/play/projection"
      );
    });

    it("passes correct media payload to useCastTransport", async () => {
      songsetSuccessFetches();

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      // The hook is called on every render; the last call carries the loaded
      // videoUrl + songset name.
      const lastCall =
        castTransportMock.mock.calls[castTransportMock.mock.calls.length - 1][0] as {
          media: CastMedia;
        };
      expect(lastCall.media.videoUrl).toBe("https://r2.example.com/videos/test.mp4");
      expect(lastCall.media.title).toBe("Test Songset");
      expect(lastCall.media.source).toEqual({
        kind: "songset",
        idOrToken: "test-songset",
      });
      expect(lastCall.media.startSeconds).toBe(0);
    });

    it("cast.isConnected drives ControllerPlayer.isPresentationActive", async () => {
      songsetSuccessFetches();
      castTransportMock.mockImplementation(() =>
        makeTransport({ isConnected: true, deviceName: "Living Room TV" })
      );

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("presentation-active")).toHaveTextContent("true");
      });
    });

    it("isPresentationActive is false when neither cast nor sender connected", async () => {
      songsetSuccessFetches();
      castTransportMock.mockImplementation(() => makeTransport({ isSupported: true }));
      presentationSenderMock.mockImplementation(() => makeSender({ isConnected: false }));

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      expect(screen.getByTestId("presentation-active")).toHaveTextContent("false");
    });

    it("prefers Cast (cast.start) when cast.isSupported=true", async () => {
      songsetSuccessFetches();
      const transport = makeTransport({ isSupported: true });
      castTransportMock.mockImplementation(() => transport);
      const sender = makeSender({ isSupported: true });
      presentationSenderMock.mockImplementation(() => sender);

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      await act(async () => {
        screen.getByTestId("send-to-tv").click();
      });

      expect(transport.start).toHaveBeenCalled();
      expect(sender.start).not.toHaveBeenCalled();
    });

    it("Presentation fallback (sender.start) only when !cast.isSupported", async () => {
      songsetSuccessFetches();
      const transport = makeTransport({ isSupported: false });
      castTransportMock.mockImplementation(() => transport);
      const sender = makeSender({ isSupported: true });
      presentationSenderMock.mockImplementation(() => sender);

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      await act(async () => {
        screen.getByTestId("send-to-tv").click();
      });

      expect(sender.start).toHaveBeenCalled();
      expect(transport.start).not.toHaveBeenCalled();
    });

    it("forwards transport command via Cast when supported", async () => {
      songsetSuccessFetches();
      const transport = makeTransport({ isSupported: true });
      castTransportMock.mockImplementation(() => transport);
      const sender = makeSender();
      presentationSenderMock.mockImplementation(() => sender);

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      await act(async () => {
        screen.getByTestId("send-cmd").click();
      });

      expect(transport.play).toHaveBeenCalled();
      expect(sender.send).not.toHaveBeenCalled();
    });

    it("forwards transport command via sender fallback when !cast.isSupported", async () => {
      songsetSuccessFetches();
      const transport = makeTransport({ isSupported: false });
      castTransportMock.mockImplementation(() => transport);
      const sender = makeSender({ isSupported: true });
      presentationSenderMock.mockImplementation(() => sender);

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      await act(async () => {
        screen.getByTestId("send-cmd").click();
      });

      expect(sender.send).toHaveBeenCalledWith({ type: "play" });
      expect(transport.play).not.toHaveBeenCalled();
    });

    it("passes onStopPresentation and stops Cast when Cast is active", async () => {
      songsetSuccessFetches();
      const transport = makeTransport({ isSupported: true, isConnected: true });
      castTransportMock.mockImplementation(() => transport);

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      expect(lastControllerProps?.onStopPresentation).toEqual(expect.any(Function));

      await act(async () => {
        screen.getByTestId("stop-presentation").click();
      });

      expect(transport.stop).toHaveBeenCalledTimes(1);
    });

    it("passes onStopPresentation and stops sender fallback when fallback is active", async () => {
      songsetSuccessFetches();
      const transport = makeTransport({ isSupported: false, isConnected: false });
      castTransportMock.mockImplementation(() => transport);
      const sender = makeSender({ isSupported: true, isConnected: true });
      presentationSenderMock.mockImplementation(() => sender);

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      expect(lastControllerProps?.onStopPresentation).toEqual(expect.any(Function));

      await act(async () => {
        screen.getByTestId("stop-presentation").click();
      });

      expect(sender.stop).toHaveBeenCalledTimes(1);
      expect(transport.stop).not.toHaveBeenCalled();
    });

    it("cast.onError triggers a toast", async () => {
      songsetSuccessFetches();

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      const opts = castTransportMock.mock.calls[0][0] as { onError: (m: string) => void };
      opts.onError("receiver rejected media");

      expect(toastError).toHaveBeenCalledWith("receiver rejected media");
    });

    it("toasts on cast connect lifecycle transition", async () => {
      songsetSuccessFetches();
      castTransportMock.mockImplementation(() =>
        makeTransport({ isConnected: true, deviceName: "Living Room TV" })
      );

      render(<ControllerPage />);

      await waitFor(() => {
        expect(toastSuccess).toHaveBeenCalledWith("Connected to Living Room TV");
      });
    });

    it("passes transport + isCastSupported to ControllerPlayer", async () => {
      songsetSuccessFetches();
      const transport = makeTransport({
        isSupported: true,
        isConnecting: true,
        deviceName: "TV",
      });
      castTransportMock.mockImplementation(() => transport);

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      expect(lastControllerProps?.transport).toBe(transport);
      expect(lastControllerProps?.isCastSupported).toBe(true);
      expect(lastControllerProps?.isCastConnecting).toBe(true);
    });

    it("passes presentationFallback (sender.isSupported/isConnected) to ControllerPlayer", async () => {
      songsetSuccessFetches();
      castTransportMock.mockImplementation(() => makeTransport({ isSupported: false }));
      presentationSenderMock.mockImplementation(() =>
        makeSender({ isSupported: true, isConnected: true }),
      );

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      expect(lastControllerProps?.presentationFallback).toEqual({
        isSupported: true,
        isConnected: true,
      });
    });

    it("surfaces a toast on sender onStatus {type:'error'} (TV projection failed)", async () => {
      songsetSuccessFetches();
      castTransportMock.mockImplementation(() => makeTransport({ isSupported: false }));
      presentationSenderMock.mockImplementation(() => makeSender({ isSupported: true }));

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      const senderOpts = presentationSenderMock.mock.calls[0][0] as {
        onStatus: (status: { type: string; message?: string }) => void;
      };
      senderOpts.onStatus({ type: "error", message: "TV projection failed — check connection" });

      expect(toastError).toHaveBeenCalledWith("TV projection failed — check connection");
    });

    it("passes Presentation API media status to ControllerPlayer", async () => {
      songsetSuccessFetches();
      castTransportMock.mockImplementation(() => makeTransport({ isSupported: false }));
      presentationSenderMock.mockImplementation(() =>
        makeSender({ isSupported: true, isConnected: true }),
      );

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      const mediaStatus = {
        type: "media",
        currentTime: 190,
        duration: 420,
        playerState: "playing",
        volume: 0.8,
        isMuted: false,
      };
      const senderOpts = presentationSenderMock.mock.calls[0][0] as {
        onStatus: (status: typeof mediaStatus) => void;
      };

      await act(async () => {
        senderOpts.onStatus(mediaStatus);
      });

      expect(lastControllerProps?.presentationMediaStatus).toEqual(mediaStatus);
    });
  });
});

describe("ShareControllerPage (share token)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    lastControllerProps = null;
    castTransportMock.mockImplementation(() => makeTransport());
    presentationSenderMock.mockImplementation(() => makeSender());
  });

  function shareSuccessFetches() {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(SHARE_RESPONSE),
    });
  }

  it("renders ControllerPlayer when share data loaded", async () => {
    shareSuccessFetches();

    render(<ShareControllerPage />);

    await waitFor(() => {
      expect(screen.getByTestId("controller-player")).toBeInTheDocument();
    });
  });

  it("passes token-derived presentationUrl with ?v=&t= to usePresentationSender", async () => {
    shareSuccessFetches();

    render(<ShareControllerPage />);

    await waitFor(() => {
      expect(screen.getByTestId("controller-player")).toBeInTheDocument();
    });

    // Use the last call since the hook re-runs when videoUrl/shareName load
    const lastSenderCall = presentationSenderMock.mock.calls[
      presentationSenderMock.mock.calls.length - 1
    ];
    const senderOpts = lastSenderCall[0] as {
      presentationUrl: string;
    };
    // The controller builds a URL with the presigned R2 URL (v) and the
    // songset name (t) so the receiver can boot without calling any API.
    expect(senderOpts.presentationUrl).toContain("/share/share-tok/play/projection?");
    expect(senderOpts.presentationUrl).toContain(
      "v=https%3A%2F%2Fr2.example.com%2Fshare%2Fvideo.mp4"
    );
    expect(senderOpts.presentationUrl).toContain("t=Shared+Set+Name");
  });

  it("does not pass autoFullscreen (defaults to true, matching songsets)", async () => {
    shareSuccessFetches();

    render(<ShareControllerPage />);

    await waitFor(() => {
      expect(screen.getByTestId("controller-player")).toBeInTheDocument();
    });

    expect(lastControllerProps?.autoFullscreen).toBeUndefined();
  });

  it("passes token-derived media payload to useCastTransport", async () => {
    shareSuccessFetches();

    render(<ShareControllerPage />);

    await waitFor(() => {
      expect(screen.getByTestId("controller-player")).toBeInTheDocument();
    });

    const lastCall =
      castTransportMock.mock.calls[castTransportMock.mock.calls.length - 1][0] as {
        media: CastMedia;
      };
    expect(lastCall.media.videoUrl).toBe("https://r2.example.com/share/video.mp4");
    expect(lastCall.media.title).toBe("Shared Set Name");
    expect(lastCall.media.source).toEqual({
      kind: "share",
      idOrToken: "share-tok",
    });
    expect(lastCall.media.startSeconds).toBe(0);
  });

  it("caster.isConnected drives isPresentationActive", async () => {
    shareSuccessFetches();
    castTransportMock.mockImplementation(() =>
      makeTransport({ isConnected: true, deviceName: "TV" })
    );

    render(<ShareControllerPage />);

    await waitFor(() => {
      expect(screen.getByTestId("presentation-active")).toHaveTextContent("true");
    });
  });

  it("passes onStopPresentation and stops Cast when Cast is active", async () => {
    shareSuccessFetches();
    const transport = makeTransport({ isSupported: true, isConnected: true });
    castTransportMock.mockImplementation(() => transport);

    render(<ShareControllerPage />);

    await waitFor(() => {
      expect(screen.getByTestId("controller-player")).toBeInTheDocument();
    });

    expect(lastControllerProps?.onStopPresentation).toEqual(expect.any(Function));

    await act(async () => {
      screen.getByTestId("stop-presentation").click();
    });

    expect(transport.stop).toHaveBeenCalledTimes(1);
  });

  it("passes onStopPresentation and stops sender fallback when fallback is active", async () => {
    shareSuccessFetches();
    const transport = makeTransport({ isSupported: false, isConnected: false });
    castTransportMock.mockImplementation(() => transport);
    const sender = makeSender({ isSupported: true, isConnected: true });
    presentationSenderMock.mockImplementation(() => sender);

    render(<ShareControllerPage />);

    await waitFor(() => {
      expect(screen.getByTestId("controller-player")).toBeInTheDocument();
    });

    expect(lastControllerProps?.onStopPresentation).toEqual(expect.any(Function));

    await act(async () => {
      screen.getByTestId("stop-presentation").click();
    });

    expect(sender.stop).toHaveBeenCalledTimes(1);
    expect(transport.stop).not.toHaveBeenCalled();
  });
});

describe("SharePage (share landing — entry navigation)", () => {
  const videoShareResponse = {
    token: "share-tok",
    shareType: "songset" as const,
    songset: {
      id: "ss-1",
      name: "Shared Set Name",
      description: null,
      totalDurationSeconds: 600,
      renderState: "fresh" as const,
      latestRenderJobId: "job-1",
      lastCompletedRenderJobId: "job-1",
    },
    items: [],
    playback: {
      selectedRenderJobId: "job-1",
      isStale: false,
      staleStatus: null,
      mp3Url: null,
      mp4Url: "https://r2.example.com/share/video.mp4",
      chaptersUrl: null,
      mp3SizeBytes: null,
      mp4SizeBytes: null,
    },
    allowDownload: false,
    createdAt: new Date().toISOString(),
    expiresAt: null,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(mockPush).mockClear();
    vi.mocked(mockReplace).mockClear();
  });

  it("uses router.replace (not push) so Back always lands on /share/[token]", async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(videoShareResponse),
    });

    render(<SharePage />);

    await waitFor(() => {
      expect(screen.getByTestId("play-button")).toBeInTheDocument();
    });

    await act(async () => {
      screen.getByTestId("play-button").click();
    });

    expect(mockReplace).toHaveBeenCalledWith("/share/share-tok/play/controller");
    expect(mockPush).not.toHaveBeenCalled();
  });
});
