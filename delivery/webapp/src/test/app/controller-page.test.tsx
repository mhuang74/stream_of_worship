import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor, act } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import type { CastTransportResult, CastMedia } from "@/hooks/useCast";
import type { OfflineSongsetRecord } from "@/lib/offline/offline-index";

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

// The controller's offline boot reads the offline index; the cache lookup
// underneath it runs for real against the Cache Storage stub in the offline
// tests below.
const { mockGetOfflineRecord } = vi.hoisted(() => ({
  mockGetOfflineRecord: vi.fn(),
}));
vi.mock("@/lib/offline/offline-index", () => ({
  getOfflineRecord: mockGetOfflineRecord,
}));

// The share controller (issue #218 PR2) boots from the token-keyed share
// index; mocked separately from the owner index so namespace separation is
// exercised.
const mockGetShareOfflineRecord = vi.hoisted(() => vi.fn());
vi.mock("@/lib/offline/share-offline-index", () => ({
  getShareOfflineRecord: mockGetShareOfflineRecord,
}));

const { mockedDownloadShareArtifacts, MockShareNoArtifactsError } = vi.hoisted(() => ({
  mockedDownloadShareArtifacts:
    vi.fn<(input: unknown, onProgress?: (p: number) => void) => Promise<void>>(),
  MockShareNoArtifactsError: class ShareNoArtifactsError extends Error {},
}));
vi.mock("@/lib/offline/download-share-offline", () => ({
  downloadShareOfflineArtifacts: mockedDownloadShareArtifacts,
  ShareNoArtifactsError: MockShareNoArtifactsError,
}));

import ControllerPage from "@/app/songsets/[id]/play/controller/page";
import { setConnectivityProbe } from "@/hooks/useConnectivity";
import ShareControllerPage from "@/app/share/[token]/play/controller/page";
import SharePage from "@/app/share/[token]/page";

// --- Transport hook mocks -------------------------------------------------

// Cache/SW stubs shared by the songset and share controller suites (issue
// #218: the share controller's recovery boots the same index/cache).
function setServiceWorkerController(controlling: boolean) {
  Object.defineProperty(navigator, "serviceWorker", {
    value: controlling ? { controller: { scriptURL: "/sw.js" } } : undefined,
    configurable: true,
  });
}

/** Cache Storage stub holding the download path's keys (sow-artifacts). */
function installArtifactCache(bodies: {
  mp4?: string;
  mp3?: string;
  chapters?: unknown;
}) {
  const entries = new Map<string, Response>();
  if (bodies.mp4 !== undefined) {
    entries.set("/sow-artifact-cache/job-offline/mp4", new Response(bodies.mp4));
  }
  if (bodies.mp3 !== undefined) {
    entries.set("/sow-artifact-cache/job-offline/mp3", new Response(bodies.mp3));
  }
  if (bodies.chapters !== undefined) {
    entries.set(
      "/sow-artifact-cache/job-offline/chapters",
      Response.json(bodies.chapters)
    );
  }

  Object.defineProperty(window, "caches", {
    value: {
      open: () =>
        Promise.resolve({
          match: (key: string) => Promise.resolve(entries.get(key)),
        }),
    },
    configurable: true,
  });
}

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
    // play/pause are OUR adapter methods (not SDK methods).
    // The SDK uses playOrPause(); our transport exposes separate play/pause
    // for a cleaner dispatch contract.
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
  videoSrc?: string;
  audioSrc?: string;
  chapters: unknown[];
  chapterRecordingHashes?: (string | null)[];
  isOfflineMedia?: boolean;
  onMediaError?: () => Promise<boolean>;
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
        <div data-testid="video-src">{props.videoSrc ?? ""}</div>
        <div data-testid="audio-src">{props.audioSrc ?? ""}</div>
        <div data-testid="offline-media">
          {props.isOfflineMedia ? "true" : "false"}
        </div>
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

// --- Fixtures -------------------------------------------------------------

// Offline-copy fixture shared by the songset and share controller suites
// (issue #218: the share controller's recovery boots the same index/cache).
const OFFLINE_RECORD: OfflineSongsetRecord = {
  songsetId: "test-songset",
  renderJobId: "job-offline",
  songsetName: "Offline Set",
  cachedMp3: true,
  cachedMp4: true,
  cachedChapters: true,
  cachedAt: "2026-09-15T00:00:00.000Z",
  chapterContentHashes: ["hash-a", "hash-b", null],
};
const MP4_PROXY_SRC = "/api/r2/artifact/job-offline/output.mp4";

const OFFLINE_CHAPTERS = {
  chapters: [
    {
      position: 0,
      songTitle: "Amazing Grace",
      startSeconds: 0,
      endSeconds: 180,
      lines: [],
    },
  ],
  totalDurationSeconds: 180,
  generatedAt: "2026-09-15T00:00:00.000Z",
};

const SONGSET_RESPONSE = {
  id: "test-songset",
  name: "Test Songset",
  renderState: "fresh",
  latestRenderJobId: "job-1",
  lastFailedRenderJobId: null,
  lastCompletedRenderJobId: "job-1",
  // Position → Recording contentHash map (issue #194). Deliberately
  // out of order to pin position-sorting; position 2 has no recording.
  items: [
    { position: 1, recording: { contentHash: "hash-b" } },
    { position: 0, recording: { contentHash: "hash-a" } },
    { position: 2, recording: null },
  ],
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
    mediaKind: "video",
    selectedRenderJobId: "job-1",
    mp4Url: "https://r2.example.com/share/video.mp4",
    chaptersUrl: null,
    chaptersData: null,
  },
  viewerAuthenticated: false,
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

    it("shows translated loading text in zh-Hant", async () => {
      global.fetch = vi.fn().mockImplementation(() => new Promise(() => {}));

      render(<ControllerPage />, "zh-Hant");

      expect(screen.getByText(/播放器載入中/i)).toBeInTheDocument();
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

    it("passes chapterRecordingHashes (content hashes by position) to ControllerPlayer", async () => {
      songsetSuccessFetches();

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      // Optional prop: tsc cannot catch a missing page→player wiring, so
      // pin it. Sorted by position; item without a recording maps to null.
      expect(lastControllerProps?.chapterRecordingHashes).toEqual([
        "hash-a",
        "hash-b",
        null,
      ]);
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

  describe("offline boot", () => {
    const MP3_PROXY_SRC = "/api/r2/artifact/job-offline/output.mp3";

    function setOnline(online: boolean) {
      Object.defineProperty(navigator, "onLine", {
        value: online,
        configurable: true,
      });
    }

    beforeEach(() => {
      mockGetOfflineRecord.mockResolvedValue(OFFLINE_RECORD);
      installArtifactCache({ mp4: "video-bytes", chapters: OFFLINE_CHAPTERS });
      setServiceWorkerController(true);
      setOnline(false);
      // Fresh connectivity state per test: without this, a probe success
      // recorded by an earlier test certifies Online for the whole suite
      // (module-level lastProbeSucceeded).
      setConnectivityProbe(null);
      Object.defineProperty(URL, "createObjectURL", {
        value: vi.fn(() => "blob:cached-video"),
        configurable: true,
        writable: true,
      });
      Object.defineProperty(URL, "revokeObjectURL", {
        value: vi.fn(),
        configurable: true,
        writable: true,
      });
      global.fetch = vi.fn();
    });

    afterEach(() => {
      Reflect.deleteProperty(window, "caches");
      Reflect.deleteProperty(navigator, "serviceWorker");
      Reflect.deleteProperty(URL, "createObjectURL");
      Reflect.deleteProperty(URL, "revokeObjectURL");
      setOnline(true);
      // Tests that simulate an unreachable server inject a failing probe via
      // the connectivity seam; the default must not leak into other suites.
      setConnectivityProbe(null);
    });

    it("boots from the index with the proxy media source and zero API fetches", async () => {
      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      expect(screen.getByTestId("video-src")).toHaveTextContent(MP4_PROXY_SRC);
      expect(global.fetch).not.toHaveBeenCalled();
      expect(screen.getByTestId("chapters-count")).toHaveTextContent("1");
      expect(lastControllerProps?.isOfflineMedia).toBe(true);
      expect(lastControllerProps?.chapterRecordingHashes).toEqual([
        "hash-a",
        "hash-b",
        null,
      ]);
    });

    it("boots the media even when the cached chapters manifest cannot be parsed", async () => {
      installArtifactCache({ mp4: "video-bytes", chapters: "{ not json" });

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("video-src")).toHaveTextContent(MP4_PROXY_SRC);
      });
      expect(screen.getByTestId("chapters-count")).toHaveTextContent("0");
    });

    it("plays a blob URL of the cached MP4 when no service worker controls the document", async () => {
      setServiceWorkerController(false);

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("video-src")).toHaveTextContent("blob:cached-video");
      });
    });

    it("boots audio playback when only the MP3 was cached", async () => {
      installArtifactCache({ mp3: "audio-bytes" });

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      expect(screen.getByTestId("audio-src")).toHaveTextContent(MP3_PROXY_SRC);
      expect(lastControllerProps?.videoSrc).toBeUndefined();
    });

    it("shows the offline hint while it boots", () => {
      mockGetOfflineRecord.mockReturnValue(new Promise(() => {}));

      render(<ControllerPage />);

      expect(screen.getByText(/starting offline playback/i)).toBeInTheDocument();
    });

    it("reports nothing downloaded when offline with no index record", async () => {
      mockGetOfflineRecord.mockResolvedValue(null);

      render(<ControllerPage />);

      await waitFor(() => {
        expect(
          screen.getByText(/has not been downloaded for offline playback/i)
        ).toBeInTheDocument();
      });
      expect(global.fetch).not.toHaveBeenCalled();
    });

    it("shows the error screen when the record exists but the cached bytes are unusable and the online chain fails", async () => {
      setOnline(true);
      installArtifactCache({}); // record present, artifact cache empty
      global.fetch = vi
        .fn()
        .mockRejectedValue(new TypeError("Failed to fetch"));

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByText(/go back/i)).toBeInTheDocument();
      });
      expect(screen.queryByTestId("controller-player")).not.toBeInTheDocument();
    });

    // Issue #210: a branch-2 boot (chain failed while nominally online) used
    // to land on the downloaded copy silently — the leader had no way to know
    // playback came from the offline copy (Cast included). The toast carries
    // no behavioral weight (isOfflineMedia stays the sole Cast gate); it only
    // surfaces the state. Branch 3 (offline at boot) and the cache-first boot
    // stay silent: those boots already announced offline playback.
    it("toasts the cached-copy hint when the chain of a NON-cached set fails onto the offline copy", async () => {
      setOnline(true);
      // Cache-first sees no record at boot (online chain runs); the
      // post-failure fallback resolves the record via the same index read.
      mockGetOfflineRecord
        .mockResolvedValueOnce(null)
        .mockResolvedValue(OFFLINE_RECORD);
      global.fetch = vi
        .fn()
        .mockRejectedValue(new TypeError("Failed to fetch"));

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      expect(toastInfo).toHaveBeenCalledWith(
        expect.stringMatching(/playing the downloaded copy/i)
      );
      expect(toastError).not.toHaveBeenCalled();
    });

    it("does not toast the cached-copy hint on an offline boot", async () => {
      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      expect(toastInfo).not.toHaveBeenCalled();
    });

    it("shows the error screen when the chain fails and nothing is downloaded", async () => {
      setOnline(true);
      mockGetOfflineRecord.mockResolvedValue(null);
      global.fetch = vi
        .fn()
        .mockRejectedValue(new TypeError("Failed to fetch"));

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByText(/go back/i)).toBeInTheDocument();
      });
      expect(screen.queryByTestId("controller-player")).not.toBeInTheDocument();
    });

    it("still redirects to login on a 401 instead of booting offline (non-cached set)", async () => {
      setOnline(true);
      mockGetOfflineRecord.mockResolvedValue(null);
      global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401 });

      render(<ControllerPage />);

      await waitFor(() => {
        expect(mockPush).toHaveBeenCalledWith("/login");
      });
      expect(screen.queryByTestId("controller-player")).not.toBeInTheDocument();
    });

    it("boots the offline copy when cached, even when online", async () => {
      setOnline(true);
      songsetSuccessFetches();

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      // Cached ⇒ offline copy boots with zero API fetches, connectivity
      // irrelevant (cache-first boot, issue #211 follow-up).
      expect(screen.getByTestId("video-src")).toHaveTextContent(MP4_PROXY_SRC);
      expect(lastControllerProps?.isOfflineMedia).toBe(true);
      expect(global.fetch).not.toHaveBeenCalled();
    });

    it("runs the online chain when the record exists but the cached bytes are gone and the device is online", async () => {
      setOnline(true);
      installArtifactCache({}); // record present, artifact cache empty
      songsetSuccessFetches();

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      expect(screen.getByTestId("video-src")).toHaveTextContent(
        "https://r2.example.com/videos/test.mp4"
      );
      expect(lastControllerProps?.isOfflineMedia).toBe(false);
    });

    it("reports the offline-unavailable error when the record exists but the cached bytes are gone while OS-offline", async () => {
      setOnline(false);
      installArtifactCache({}); // record present, artifact cache empty

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByText(/not been downloaded for offline/i)).toBeInTheDocument();
      });
      expect(screen.queryByTestId("controller-player")).not.toBeInTheDocument();
    });

    // Issue #211: an in-flight/failed probe is Unknown, NOT Offline — the
    // boot must not treat a cold load as offline before the probe has
    // answered. Unknown boots the online chain (cache-first for cached sets
    // no longer depends on connectivity); a genuinely-unreachable server
    // makes the chain fail onto the downloaded copy (branch 2, with its
    // toast). Only definitive Offline (navigator.onLine false) is silent
    // branch 3.
    it("still runs the online chain at boot while the probe has not confirmed online (non-cached set)", async () => {
      setOnline(true);
      setConnectivityProbe(() => Promise.resolve(false)); // server unreachable
      mockGetOfflineRecord.mockResolvedValue(null);
      songsetSuccessFetches();

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      // The chain ran (zero-fetch cache-first would have hidden Cast).
      expect(screen.getByTestId("video-src")).toHaveTextContent(
        "https://r2.example.com/videos/test.mp4"
      );
      expect(lastControllerProps?.isOfflineMedia).toBe(false);
    });

    it("lands on the downloaded copy with the toast when the probe cannot confirm online and the chain fails (non-cached set)", async () => {
      setOnline(true);
      setConnectivityProbe(() => Promise.resolve(false));
      mockGetOfflineRecord
        .mockResolvedValueOnce(null) // cache-first boot: no record
        .mockResolvedValue(OFFLINE_RECORD); // post-failure fallback
      global.fetch = vi
        .fn()
        .mockRejectedValue(new TypeError("Failed to fetch"));

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      expect(screen.getByTestId("video-src")).toHaveTextContent(MP4_PROXY_SRC);
      expect(lastControllerProps?.isOfflineMedia).toBe(true);
      expect(toastInfo).toHaveBeenCalledWith(
        expect.stringMatching(/playing the downloaded copy/i)
      );
      expect(toastError).not.toHaveBeenCalled();
    });

    it("swaps a failed proxy source for a blob URL of the cached artifact", async () => {
      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("video-src")).toHaveTextContent(MP4_PROXY_SRC);
      });

      let handled: boolean | undefined;
      await act(async () => {
        handled = await lastControllerProps?.onMediaError?.();
      });

      expect(handled).toBe(true);
      expect(screen.getByTestId("video-src")).toHaveTextContent("blob:cached-video");
    });

    // Online boot → Airplane Mode mid-playback: the presigned R2 URL dies and
    // the media element fires `error`. The host must recover by swapping to
    // the downloaded copy (issue: offline playback dead-end on online boot).
    it("recovers a failed online source by swapping to the offline copy", async () => {
      setOnline(true);
      mockGetOfflineRecord
        .mockResolvedValueOnce(null) // cache-first boot: no record
        .mockResolvedValue(OFFLINE_RECORD); // media-failure recovery
      songsetSuccessFetches();

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("video-src")).toHaveTextContent(
          "https://r2.example.com/videos/test.mp4"
        );
      });

      let handled: boolean | undefined;
      await act(async () => {
        handled = await lastControllerProps?.onMediaError?.();
      });

      expect(handled).toBe(true);
      expect(screen.getByTestId("video-src")).toHaveTextContent(MP4_PROXY_SRC);
      expect(screen.getByTestId("offline-media")).toHaveTextContent("true");

      expect(lastControllerProps?.chapterRecordingHashes).toEqual([
        "hash-a",
        "hash-b",
        null,
      ]);
    });

    it("attempts the online-boot offline recovery only once per boot", async () => {
      setOnline(true);
      mockGetOfflineRecord
        .mockResolvedValueOnce(null) // cache-first boot: no record
        .mockResolvedValue(OFFLINE_RECORD); // media-failure recovery
      songsetSuccessFetches();

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("video-src")).toHaveTextContent(
          "https://r2.example.com/videos/test.mp4"
        );
      });

      let first: boolean | undefined;
      let second: boolean | undefined;
      await act(async () => {
        first = await lastControllerProps?.onMediaError?.();
      });
      await act(async () => {
        second = await lastControllerProps?.onMediaError?.();
      });

      expect(first).toBe(true);
      expect(second).toBe(false);
    });

    it("leaves the media error unhandled when already playing a blob URL", async () => {
      setServiceWorkerController(false);

      render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("video-src")).toHaveTextContent("blob:cached-video");
      });

      expect(await lastControllerProps?.onMediaError?.()).toBe(false);
    });

    it("releases the blob URL it played from when the controller unmounts", async () => {
      setServiceWorkerController(false);

      const { unmount } = render(<ControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("video-src")).toHaveTextContent("blob:cached-video");
      });

      unmount();

      // A blob URL pins the whole artifact in memory until it is revoked.
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:cached-video");
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
    // The shared core's media-error recovery resolves the offline index;
    // default to no record so online-boot tests never hit the cache stub.
    mockGetOfflineRecord.mockResolvedValue(null);
    setServiceWorkerController(true);
  });

  function shareSuccessFetches(response: Record<string, unknown> = SHARE_RESPONSE) {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(response),
    });
  }

  // Issue #218: MP3-only share renders boot as audio-only — mediaKind is
  // declared by the API, and the page passes audioSrc (never videoSrc).
  it("boots audio playback for an MP3-only share (mediaKind audio)", async () => {
    shareSuccessFetches({
      ...SHARE_RESPONSE,
      playback: {
        mediaKind: "audio",
        selectedRenderJobId: "job-1",
        mp3Url: "https://r2.example.com/share/audio.mp3",
        mp4Url: null,
      },
    });

    render(<ShareControllerPage />);

    await waitFor(() => {
      expect(screen.getByTestId("controller-player")).toBeInTheDocument();
    });
    expect(screen.getByTestId("audio-src")).toHaveTextContent(
      "https://r2.example.com/share/audio.mp3"
    );
    expect(lastControllerProps?.videoSrc).toBeUndefined();
  });

  // Lyrics Feedback is session-gated (ADR-0007): hashes flow for every
  // viewer, the player's feedback row only renders when the share API saw a
  // session — the page relays whatever the route reports.
  it("passes chapterRecordingHashes from the share response", async () => {
    shareSuccessFetches({
      ...SHARE_RESPONSE,
      playback: {
        ...SHARE_RESPONSE.playback,
        chapterRecordingHashes: ["hash-a", "hash-b", null],
      },
      viewerAuthenticated: true,
    });

    render(<ShareControllerPage />);

    await waitFor(() => {
      expect(screen.getByTestId("controller-player")).toBeInTheDocument();
    });
    expect(lastControllerProps?.chapterRecordingHashes).toEqual([
      "hash-a",
      "hash-b",
      null,
    ]);
  });

  it("renders no hashes (no feedback affordance) when the viewer is anonymous", async () => {
    shareSuccessFetches({
      ...SHARE_RESPONSE,
      viewerAuthenticated: false,
    });

    render(<ShareControllerPage />);

    await waitFor(() => {
      expect(screen.getByTestId("controller-player")).toBeInTheDocument();
    });
    // The route omits hashes for anonymous viewers; the page relays the
    // absence — LyricJumpList then hides the Lyrics Feedback row.
    expect(lastControllerProps?.chapterRecordingHashes).toEqual([]);
  });

  // Media-error recovery (issue #218): the shared core's one-per-boot swap
  // to the downloaded copy must work on the share surface too.
  it("recovers a failed online source by swapping to the offline copy", async () => {
    installArtifactCache({ mp4: "video-bytes" });
    shareSuccessFetches();
    // Cache-first sees no share record at boot; the media-failure recovery
    // resolves the share-index record (its frozen renderJobId keys into the
    // same artifact cache).
    mockGetShareOfflineRecord
      .mockResolvedValueOnce(null) // cache-first boot: no record
      .mockResolvedValue(OFFLINE_RECORD); // media-failure recovery

    render(<ShareControllerPage />);

    await waitFor(() => {
      expect(screen.getByTestId("video-src")).toHaveTextContent(
        "https://r2.example.com/share/video.mp4"
      );
    });

    let handled: boolean | undefined;
    await act(async () => {
      handled = await lastControllerProps?.onMediaError?.();
    });

    expect(handled).toBe(true);
    expect(screen.getByTestId("video-src")).toHaveTextContent(MP4_PROXY_SRC);
    expect(screen.getByTestId("offline-media")).toHaveTextContent("true");
  });

  it("shows the error screen with the go-back route when the share fetch fails", async () => {
    // No cached copy: the failing chain has nothing to fall back to.
    mockGetShareOfflineRecord.mockResolvedValue(null);
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: false,
      status: 410,
      json: () => Promise.resolve({ error: "This share link has been revoked" }),
    });

    render(<ShareControllerPage />);

    await waitFor(() => {
      expect(screen.getByText(/revoked/i)).toBeInTheDocument();
    });
    expect(screen.queryByTestId("controller-player")).not.toBeInTheDocument();
  });

  // Share offline copies (issue #218 PR2, ADR-0009): the share controller
  // boots cache-first from the token-keyed share index with zero API calls,
  // and a revoked link's cached copy keeps playing.
  describe("share offline boot", () => {
    const SHARE_OFFLINE_RECORD = {
      token: "share-tok",
      renderJobId: "job-offline",
      songsetName: "Shared Set Name",
      cachedMp3: true,
      cachedMp4: true,
      cachedChapters: true,
      cachedAt: "2026-09-20T00:00:00.000Z",
      chapterContentHashes: ["hash-a", null],
    };

    function setOnline(online: boolean) {
      Object.defineProperty(navigator, "onLine", {
        value: online,
        configurable: true,
      });
    }

    beforeEach(() => {
      vi.clearAllMocks();
      lastControllerProps = null;
      castTransportMock.mockImplementation(() => makeTransport());
      presentationSenderMock.mockImplementation(() => makeSender());
      mockGetOfflineRecord.mockResolvedValue(null);
      mockGetShareOfflineRecord.mockResolvedValue(SHARE_OFFLINE_RECORD);
      installArtifactCache({ mp4: "video-bytes", chapters: OFFLINE_CHAPTERS });
      setServiceWorkerController(true);
      setOnline(false);
      setConnectivityProbe(null);
      Object.defineProperty(URL, "createObjectURL", {
        value: vi.fn(() => "blob:cached-video"),
        configurable: true,
        writable: true,
      });
      Object.defineProperty(URL, "revokeObjectURL", {
        value: vi.fn(),
        configurable: true,
        writable: true,
      });
      global.fetch = vi.fn();
    });

    afterEach(() => {
      Reflect.deleteProperty(window, "caches");
      Reflect.deleteProperty(navigator, "serviceWorker");
      Reflect.deleteProperty(URL, "createObjectURL");
      Reflect.deleteProperty(URL, "revokeObjectURL");
      setOnline(true);
      setConnectivityProbe(null);
    });

    it("boots from the share index with the proxy source and zero API fetches", async () => {
      render(<ShareControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("controller-player")).toBeInTheDocument();
      });

      expect(screen.getByTestId("video-src")).toHaveTextContent(MP4_PROXY_SRC);
      expect(global.fetch).not.toHaveBeenCalled();
      expect(lastControllerProps?.isOfflineMedia).toBe(true);
      expect(lastControllerProps?.chapterRecordingHashes).toEqual(["hash-a", null]);
    });

    it("shows the offline boot hint while a share copy boots", () => {
      const { promise } = Promise.withResolvers<never>();
      mockGetShareOfflineRecord.mockReturnValue(promise);

      render(<ShareControllerPage />);

      expect(screen.getByText(/starting offline playback/i)).toBeInTheDocument();
    });

    it("reports nothing downloaded when offline with no share record", async () => {
      mockGetShareOfflineRecord.mockResolvedValue(null);

      render(<ShareControllerPage />);

      await waitFor(() => {
        expect(
          screen.getByText(/has not been downloaded for offline playback/i)
        ).toBeInTheDocument();
      });
      expect(global.fetch).not.toHaveBeenCalled();
    });

    it("still runs the anonymous token chain online (no share copy)", async () => {
      setOnline(true);
      mockGetShareOfflineRecord.mockResolvedValue(null);
      shareSuccessFetches();

      render(<ShareControllerPage />);

      await waitFor(() => {
        expect(screen.getByTestId("video-src")).toHaveTextContent(
          "https://r2.example.com/share/video.mp4"
        );
      });
      expect(lastControllerProps?.isOfflineMedia).toBe(false);
    });
  });

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
      chaptersData: null,
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

  it("uses router.push so Back returns to /share/[token]", async () => {
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

    expect(mockPush).toHaveBeenCalledWith("/share/share-tok/play/controller");
    expect(mockReplace).not.toHaveBeenCalled();
  });

  // Issue #218: an MP3-only share boots through the controller (audio-only
  // playback with lyrics and controls) — the separate audio page is gone.
  it("routes an audio-only share to the play controller, not the audio page", async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          ...videoShareResponse,
          playback: {
            ...videoShareResponse.playback,
            mediaKind: "audio",
            mp4Url: null,
            mp3Url: "https://r2.example.com/share/audio.mp3",
          },
        }),
    });

    render(<SharePage />);

    await waitFor(() => {
      expect(screen.getByTestId("play-button")).toBeInTheDocument();
    });

    await act(async () => {
      screen.getByTestId("play-button").click();
    });

    expect(mockPush).toHaveBeenCalledWith("/share/share-tok/play/controller");
  });

  // Share Offline Copies (issue #218 PR2, ADR-0009): Download lives on the
  // landing page as an explicit button; staleness (current render ≠ frozen
  // snapshot) surfaces only here and offers Re-download.
  describe("share download affordance", () => {
    // isOfflineSupportedOnCurrentDevice must be true on the test platform.
    function shareFetch(response: Record<string, unknown>) {
      global.fetch = vi.fn().mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(response),
      });
    }

    it("offers Download when artifacts exist and nothing is downloaded yet", async () => {
      shareFetch(videoShareResponse);

      render(<SharePage />);

      await waitFor(() => {
        expect(screen.getByTestId("download-button")).toBeInTheDocument();
      });
    });

    it("shows the downloaded hint when the cached copy matches the current render", async () => {
      mockGetShareOfflineRecord.mockResolvedValue({
        token: "share-tok",
        renderJobId: "job-1",
        songsetName: "Shared Set Name",
        cachedMp3: false,
        cachedMp4: true,
        cachedChapters: false,
        cachedAt: "2026-09-20T00:00:00.000Z",
        chapterContentHashes: [],
      });
      shareFetch(videoShareResponse);

      render(<SharePage />);

      await waitFor(() => {
        expect(screen.getByTestId("downloaded-hint")).toBeInTheDocument();
      });
      expect(screen.queryByTestId("download-button")).not.toBeInTheDocument();
      expect(screen.queryByTestId("redownload-button")).not.toBeInTheDocument();
    });

    it("offers Re-download when the cached copy pins an older render", async () => {
      mockGetShareOfflineRecord.mockResolvedValue({
        token: "share-tok",
        renderJobId: "job-old",
        songsetName: "Shared Set Name",
        cachedMp3: false,
        cachedMp4: true,
        cachedChapters: false,
        cachedAt: "2026-09-19T00:00:00.000Z",
        chapterContentHashes: [],
      });
      shareFetch(videoShareResponse);

      render(<SharePage />);

      await waitFor(() => {
        expect(screen.getByTestId("redownload-button")).toBeInTheDocument();
      });
    });

    it("downloads through the share path and toasts success", async () => {
      mockGetShareOfflineRecord.mockResolvedValue(null);
      mockedDownloadShareArtifacts.mockResolvedValueOnce(undefined);
      shareFetch(videoShareResponse);

      render(<SharePage />);

      await waitFor(() => {
        expect(screen.getByTestId("download-button")).toBeInTheDocument();
      });

      await act(async () => {
        screen.getByTestId("download-button").click();
      });

      expect(mockedDownloadShareArtifacts).toHaveBeenCalledWith(
        expect.objectContaining({
          token: "share-tok",
          renderJobId: "job-1",
          songsetName: "Shared Set Name",
        })
      );
      expect(toastSuccess).toHaveBeenCalledWith(
        expect.stringMatching(/downloaded for offline/i)
      );
    });

    it("toasts the no-artifacts error when the share has nothing to download", async () => {
      mockGetShareOfflineRecord.mockResolvedValue(null);
      mockedDownloadShareArtifacts.mockRejectedValueOnce(new MockShareNoArtifactsError());
      shareFetch(videoShareResponse);

      render(<SharePage />);

      await waitFor(() => {
        expect(screen.getByTestId("download-button")).toBeInTheDocument();
      });

      await act(async () => {
        screen.getByTestId("download-button").click();
      });

      expect(toastError).toHaveBeenCalledWith(
        expect.stringMatching(/no downloadable files/i)
      );
    });
  });
});
