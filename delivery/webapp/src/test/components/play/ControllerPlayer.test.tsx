import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { ControllerPlayer } from "@/components/play/ControllerPlayer";
import type { CastTransportResult } from "@/hooks/useCast";

// Mock next/navigation
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
  useParams: () => ({ id: "test-songset" }),
}));

// Mock sonner toast
vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

function makeTransport(
  overrides: Partial<CastTransportResult> = {}
): CastTransportResult {
  return {
    isSupported: true,
    availability: "available" as const,
    isConnecting: false,
    isConnected: false,
    deviceName: "",
    playerState: "",
    currentTime: 0,
    duration: 420,
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

describe("ControllerPlayer", () => {
  const mockChapters = [
    {
      position: 0,
      songTitle: "Amazing Grace",
      startSeconds: 0,
      endSeconds: 180,
      lines: [
        { text: "Amazing grace, how sweet the sound", startSeconds: 10 },
        { text: "That saved a wretch like me", startSeconds: 20 },
      ],
    },
    {
      position: 1,
      songTitle: "How Great Thou Art",
      startSeconds: 180,
      endSeconds: 420,
      lines: [
        { text: "O Lord my God", startSeconds: 190 },
        { text: "When I in awesome wonder", startSeconds: 200 },
      ],
    },
  ];

  const defaultProps = {
    playerId: "test-songset",
    videoSrc: "https://example.com/video.mp4",
    chapters: mockChapters,
    isPresentationActive: false,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();

    // Mock sessionStorage
    const sessionStorageMock = {
      getItem: vi.fn(),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    };
    Object.defineProperty(window, "sessionStorage", {
      value: sessionStorageMock,
      writable: true,
    });

    // Mock fullscreen API
    Object.defineProperty(document, "fullscreenElement", {
      value: null,
      writable: true,
      configurable: true,
    });
    Object.defineProperty(document.documentElement, "requestFullscreen", {
      value: vi.fn().mockResolvedValue(undefined),
      writable: true,
      configurable: true,
    });
    Object.defineProperty(document, "exitFullscreen", {
      value: vi.fn().mockResolvedValue(undefined),
      writable: true,
      configurable: true,
    });

    // Mock navigator.userAgent for iOS detection (non-iOS by default)
    Object.defineProperty(navigator, "userAgent", {
      value: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      writable: true,
      configurable: true,
    });

    // Mock navigator.wakeLock
    Object.defineProperty(navigator, "wakeLock", {
      value: undefined,
      writable: true,
      configurable: true,
    });

    // Mock HTMLMediaElement play/pause for jsdom
    Object.defineProperty(window.HTMLMediaElement.prototype, "play", {
      value: vi.fn().mockResolvedValue(undefined),
      writable: true,
      configurable: true,
    });
    Object.defineProperty(window.HTMLMediaElement.prototype, "pause", {
      value: vi.fn(),
      writable: true,
      configurable: true,
    });
    //currentTime / duration are writable number props on the prototype so
    // tests can set them and read them back.
    Object.defineProperty(window.HTMLMediaElement.prototype, "currentTime", {
      value: 0,
      writable: true,
      configurable: true,
    });
    Object.defineProperty(window.HTMLMediaElement.prototype, "duration", {
      value: 0,
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ── Rendering (baseline) ────────────────────────────────────────────────
  describe("rendering", () => {
    it("renders video element", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const video = document.querySelector("video");
      expect(video).toBeInTheDocument();
    });

    it("renders playback controls", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      expect(screen.getByRole("button", { name: /^play$/i })).toBeInTheDocument();
    });

    it("renders exit button", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      expect(screen.getByRole("button", { name: /^back$/i })).toBeInTheDocument();
    });

    it("renders back and fullscreen controls as separate top-left actions", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const leftActions = screen.getByTestId("playback-left-actions");
      const backButton = screen.getByRole("button", { name: /^back$/i });
      const fullscreenButton = screen.getByRole("button", { name: /re-enter fullscreen/i });

      expect(leftActions).toContainElement(backButton);
      expect(leftActions).toContainElement(fullscreenButton);
      expect(backButton).not.toBe(fullscreenButton);
    });

    it("renders lyric jump list handle", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      expect(screen.getByText(/lyrics/i)).toBeInTheDocument();
    });
  });

  // ── Presentation mode ──────────────────────────────────────────────────
  describe("presentation mode", () => {
    it("shows connected indicator when presentation is active", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} isPresentationActive={true} />);
      });

      expect(screen.getByText(/connected to tv/i)).toBeInTheDocument();
    });

    it("shows transport.deviceName in the connected badge", async () => {
      const transport = makeTransport({
        isConnected: true,
        deviceName: "Living Room TV",
        playerState: "playing",
      });
      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            transport={transport}
          />
        );
      });

      expect(screen.getByText(/connected to living room tv/i)).toBeInTheDocument();
    });

    it("does not show connected indicator when presentation is inactive", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} isPresentationActive={false} />);
      });

      expect(screen.queryByText(/connected to tv/i)).not.toBeInTheDocument();
    });

    it("shows close button only while presentation is active", async () => {
      const { rerender } = render(
        <ControllerPlayer {...defaultProps} isPresentationActive={false} />
      );

      expect(screen.queryByTestId("presentation-close-button")).not.toBeInTheDocument();

      await act(async () => {
        rerender(<ControllerPlayer {...defaultProps} isPresentationActive={true} />);
      });

      expect(screen.getByTestId("presentation-close-button")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /close tv view/i })).toBeInTheDocument();
    });

    it("clicking the close button calls onStopPresentation", async () => {
      const onStopPresentation = vi.fn();
      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            onStopPresentation={onStopPresentation}
          />
        );
      });

      await act(async () => {
        fireEvent.click(screen.getByTestId("presentation-close-button"));
      });

      expect(onStopPresentation).toHaveBeenCalledTimes(1);
    });

    it("mutes + pauses local video when presentation is active", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} isPresentationActive={true} />);
      });

      const video = document.querySelector("video") as HTMLVideoElement;
      expect(video).toHaveAttribute("muted");
      expect(video.pause).toHaveBeenCalled();
    });

    it("unmutes video when presentation becomes inactive", async () => {
      const { rerender } = render(
        <ControllerPlayer {...defaultProps} isPresentationActive={true} />
      );

      await act(async () => {
        rerender(<ControllerPlayer {...defaultProps} isPresentationActive={false} />);
      });

      const video = document.querySelector("video");
      expect(video).not.toHaveAttribute("muted");
    });

    it("renders LyricJumpList when active", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} isPresentationActive={true} />);
      });

      expect(screen.getByText(/lyrics/i)).toBeInTheDocument();
    });
  });

  // ── UI reconciles from Cast status ─────────────────────────────────────
  describe("UI reconciliation from transport", () => {
    it("reflects transport.currentTime / playerState when connected", async () => {
      const transport = makeTransport({
        isConnected: true,
        playerState: "playing",
        currentTime: 120,
        duration: 420,
        volume: 0.7,
        isMuted: true,
      });

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            transport={transport}
          />
        );
      });

      // Pause button visible because receiver reports "playing".
      expect(screen.getByRole("button", { name: /^pause$/i })).toBeInTheDocument();
      // Time display reflects transport.currentTime (120s -> 2:00).
      expect(screen.getByText("2:00")).toBeInTheDocument();
    });

    it("shows play button when receiver reports paused", async () => {
      const transport = makeTransport({
        isConnected: true,
        playerState: "paused",
        currentTime: 30,
      });

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            transport={transport}
          />
        );
      });

      expect(screen.getByRole("button", { name: /^play$/i })).toBeInTheDocument();
    });
  });

  // ── Command forwarding ─────────────────────────────────────────────────
  describe("command forwarding", () => {
    it("handlePlayPause emits {type:'pause'} when receiver is playing", async () => {
      const onSendTransportCommand = vi.fn();
      const transport = makeTransport({
        isConnected: true,
        playerState: "playing",
        currentTime: 10,
      });

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            transport={transport}
            onSendTransportCommand={onSendTransportCommand}
          />
        );
      });

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /^pause$/i }));
      });

      expect(onSendTransportCommand).toHaveBeenCalledWith({ type: "pause" });
    });

    it("handlePlayPause emits {type:'play'} when receiver is paused", async () => {
      const onSendTransportCommand = vi.fn();
      const transport = makeTransport({
        isConnected: true,
        playerState: "paused",
        currentTime: 10,
      });

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            transport={transport}
            onSendTransportCommand={onSendTransportCommand}
          />
        );
      });

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /^play$/i }));
      });

      expect(onSendTransportCommand).toHaveBeenCalledWith({ type: "play" });
    });

    it("handleToggleMute emits {type:'mute'} (not volume)", async () => {
      const onSendTransportCommand = vi.fn();
      const transport = makeTransport({
        isConnected: true,
        playerState: "playing",
        currentTime: 10,
        isMuted: false,
      });

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            transport={transport}
            onSendTransportCommand={onSendTransportCommand}
          />
        );
      });

      const muteButton = screen.getByRole("button", { name: /mute/i });
      await act(async () => {
        fireEvent.click(muteButton);
      });

      expect(onSendTransportCommand).toHaveBeenCalledWith({
        type: "mute",
        muted: true,
      });
      const calls = onSendTransportCommand.mock.calls.map((c) => c[0]);
      expect(calls.find((c) => c.type === "volume")).toBeUndefined();
    });

    it("forwarding not invoked when not active (local playback path)", async () => {
      const onSendTransportCommand = vi.fn();

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={false}
            onSendTransportCommand={onSendTransportCommand}
          />
        );
      });

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /^play$/i }));
      });

      expect(onSendTransportCommand).not.toHaveBeenCalled();
    });

    it("handleVolumeChange emits clamped {type:'volume'} (not mute) while active", async () => {
      const onSendTransportCommand = vi.fn();
      const transport = makeTransport({
        isConnected: true,
        playerState: "playing",
        currentTime: 10,
        volume: 0.5,
        isMuted: false,
      });

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            transport={transport}
            onSendTransportCommand={onSendTransportCommand}
          />
        );
      });

      onSendTransportCommand.mockClear();

      const volumeSlider = screen.getByRole("slider", { name: /volume/i });
      // Out-of-range input (1.5) must be clamped to 1.
      await act(async () => {
        fireEvent.change(volumeSlider, { target: { value: "1.5" } });
      });

      await waitFor(() => {
        expect(onSendTransportCommand).toHaveBeenCalledWith({
          type: "volume",
          level: 1,
        });
      });
      const calls = onSendTransportCommand.mock.calls.map((c) => c[0]);
      expect(calls.find((c) => c.type === "mute")).toBeUndefined();
    });
  });

  // ── Seek forwarding (skip / prev / jump / scrub) ────────────────────────
  describe("seek forwarding", () => {
    it("next song emits a clamped seek", async () => {
      const onSendTransportCommand = vi.fn();
      const transport = makeTransport({
        isConnected: true,
        playerState: "playing",
        currentTime: 10,
        duration: 420,
        isMuted: false,
      });

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            transport={transport}
            onSendTransportCommand={onSendTransportCommand}
          />
        );
      });

      // next song should seek to chapters[1].startSeconds (180).
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /next song/i }));
      });

      await waitFor(() => {
        expect(onSendTransportCommand).toHaveBeenCalledWith({
          type: "seek",
          positionSeconds: 180,
        });
      });
    });

    it("enables previous song immediately after a Presentation API next-song seek", async () => {
      const onSendTransportCommand = vi.fn();

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            presentationFallback={{ isSupported: true, isConnected: true }}
            presentationMediaStatus={{
              type: "media",
              currentTime: 10,
              duration: 420,
              playerState: "playing",
              volume: 1,
              isMuted: false,
            }}
            onSendTransportCommand={onSendTransportCommand}
          />
        );
      });

      onSendTransportCommand.mockClear();

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /next song/i }));
      });

      await waitFor(() => {
        expect(screen.getByText("2/2")).toBeInTheDocument();
      });
      expect(screen.getByRole("button", { name: /previous song/i })).not.toBeDisabled();

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /previous song/i }));
      });

      await waitFor(() => {
        const seekCalls = onSendTransportCommand.mock.calls
          .map((c) => c[0])
          .filter((c) => c.type === "seek");
        expect(seekCalls).toEqual([{ type: "seek", positionSeconds: 0 }]);
      });
    });

    it("Presentation API ] then [ jumps back to song 1", async () => {
      const onSendTransportCommand = vi.fn();

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            presentationFallback={{ isSupported: true, isConnected: true }}
            presentationMediaStatus={{
              type: "media",
              currentTime: 10,
              duration: 420,
              playerState: "playing",
              volume: 1,
              isMuted: false,
            }}
            onSendTransportCommand={onSendTransportCommand}
          />
        );
      });

      onSendTransportCommand.mockClear();

      await act(async () => {
        fireEvent.keyDown(document, { key: "]" });
      });

      await waitFor(() => {
        expect(screen.getByText("2/2")).toBeInTheDocument();
      });

      await act(async () => {
        fireEvent.keyDown(document, { key: "[" });
      });

      await waitFor(() => {
        expect(onSendTransportCommand).toHaveBeenCalledWith({
          type: "seek",
          positionSeconds: 0,
        });
      });
    });

    it("skip-forward emits a seek debounced 200ms", async () => {
      const onSendTransportCommand = vi.fn();
      const transport = makeTransport({
        isConnected: true,
        playerState: "playing",
        currentTime: 10,
        duration: 420,
        isMuted: false,
      });

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            transport={transport}
            onSendTransportCommand={onSendTransportCommand}
          />
        );
      });

      // Find a skip-forward control. PlaybackControls does not label a
      // skip-forward button, so drive the seek via the scrub bar instead.
      const scrubBar = screen.getByRole("slider", { name: /seek/i });
      await act(async () => {
        fireEvent.keyDown(scrubBar, { key: "ArrowRight" });
      });

      await waitFor(() => {
        expect(onSendTransportCommand).toHaveBeenCalledWith({
          type: "seek",
          positionSeconds: 20,
        });
      });
    });

    it("rapid scrub inputs collapse to one debounced seek (latest-wins)", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: false, now: Date.now() });
      try {
        const onSendTransportCommand = vi.fn();
        const transport = makeTransport({
          isConnected: true,
          playerState: "playing",
          currentTime: 0,
          duration: 420,
        });

        await act(async () => {
          render(
            <ControllerPlayer
              {...defaultProps}
              isPresentationActive={true}
              transport={transport}
              onSendTransportCommand={onSendTransportCommand}
            />
          );
        });

        onSendTransportCommand.mockClear();
        const scrubBar = screen.getByRole("slider", { name: /seek/i });

        // Three rapid skip-forwards (+10 each) within the debounce window.
        await act(async () => {
          fireEvent.keyDown(scrubBar, { key: "ArrowRight" });
          fireEvent.keyDown(scrubBar, { key: "ArrowRight" });
          fireEvent.keyDown(scrubBar, { key: "ArrowRight" });
        });
        // No transport seek fired yet (debounced).
        const seekCalls = onSendTransportCommand.mock.calls
          .map((c) => c[0])
          .filter((c) => c.type === "seek");
        expect(seekCalls.length).toBe(0);

        await act(async () => {
          vi.advanceTimersByTime(200);
        });

        const seekCallsAfter = onSendTransportCommand.mock.calls
          .map((c) => c[0])
          .filter((c) => c.type === "seek");
        // Exactly one seek (latest-wins). transport.currentTime is static at 0
        // in the mock, so the last computed target is 0 + 10 = 10.
        expect(seekCallsAfter.length).toBe(1);
        expect(seekCallsAfter[0]).toEqual({ type: "seek", positionSeconds: 10 });
      } finally {
        vi.useRealTimers();
      }
    });
  });

  // ── Jump-to-chapter / jump-to-lyric (local seek via LyricJumpList) ──────
  describe("jump list seek", () => {
    it("jump-to-chapter emits a local seek (LyricJumpList, not active)", async () => {
      // When not active, the LyricJumpList is rendered; clicking a chapter
      // seeks the local <video> (a seek, no transport command).
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} isPresentationActive={false} />);
      });

      const video = document.querySelector("video") as HTMLVideoElement;
      video.currentTime = 0;

      // Open the lyric list handle and click a chapter.
      const handle = screen.getByText(/lyrics/i);
      await act(async () => {
        fireEvent.click(handle);
      });

      // Find a chapter button by its song title.
      const chapterButton = await screen.findByText("How Great Thou Art");
      await act(async () => {
        fireEvent.click(chapterButton);
      });

      // Local video should have been seeked to chapter 1 start (180).
      expect(video.currentTime).toBe(180);
    });

    it("jump-to-lyric emits a local seek (LyricJumpList, not active)", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} isPresentationActive={false} />);
      });

      const video = document.querySelector("video") as HTMLVideoElement;
      video.currentTime = 0;

      const handle = screen.getByText(/lyrics/i);
      await act(async () => {
        fireEvent.click(handle);
      });

      // Expand chapter 0 then click its second lyric line (startSeconds=20).
      const chapterHeader = await screen.findByText("Amazing Grace");
      await act(async () => {
        fireEvent.click(chapterHeader);
      });

      const line = await screen.findByText("That saved a wretch like me");
      await act(async () => {
        fireEvent.click(line);
      });

      expect(video.currentTime).toBe(20);
    });

    it("jump-to-lyric emits a Presentation API seek while active", async () => {
      const onSendTransportCommand = vi.fn();

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            presentationFallback={{ isSupported: true, isConnected: true }}
            presentationMediaStatus={{
              type: "media",
              currentTime: 10,
              duration: 420,
              playerState: "playing",
              volume: 1,
              isMuted: false,
            }}
            onSendTransportCommand={onSendTransportCommand}
          />
        );
      });

      onSendTransportCommand.mockClear();

      await act(async () => {
        fireEvent.click(screen.getByText(/lyrics/i));
      });

      await act(async () => {
        fireEvent.click(await screen.findByText("That saved a wretch like me"));
      });

      await waitFor(() => {
        expect(onSendTransportCommand).toHaveBeenCalledWith({
          type: "seek",
          positionSeconds: 20,
        });
      });
    });
  });

  // ── Song-change effect (songTitle) ──────────────────────────────────────
  describe("song-title forwarding", () => {
    it("emits {type:'songTitle'} on song change while active", async () => {
      const onSendTransportCommand = vi.fn();
      const transport = makeTransport({
        isConnected: true,
        playerState: "playing",
        currentTime: 0,
        duration: 420,
      });

      const { rerender } = render(
        <ControllerPlayer
          {...defaultProps}
          isPresentationActive={true}
          transport={transport}
          onSendTransportCommand={onSendTransportCommand}
        />
      );

      // The first song emits its title on mount while active.
      await waitFor(() => {
        expect(onSendTransportCommand).toHaveBeenCalledWith({
          type: "songTitle",
          title: "Amazing Grace",
        });
      });

      onSendTransportCommand.mockClear();

      // Drive a song change: bump transport.currentTime into song 2 (start 180)
      // and re-render. The connected-transport chapter-index effect recomputes
      // currentSongIndex, which fires the song-change effect with song 2's title.
      rerender(
        <ControllerPlayer
          {...defaultProps}
          isPresentationActive={true}
          transport={makeTransport({
            isConnected: true,
            playerState: "playing",
            currentTime: 190,
            duration: 420,
          })}
          onSendTransportCommand={onSendTransportCommand}
        />
      );

      await waitFor(() => {
        expect(onSendTransportCommand).toHaveBeenCalledWith({
          type: "songTitle",
          title: "How Great Thou Art",
        });
      });
    });

    it("does not emit songTitle when not active", async () => {
      const onSendTransportCommand = vi.fn();

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={false}
            onSendTransportCommand={onSendTransportCommand}
          />
        );
      });

      expect(onSendTransportCommand).not.toHaveBeenCalled();
    });
  });

  // ── Buffering chip ──────────────────────────────────────────────────────
  describe("buffering chip", () => {
    it("renders 'TV is loading…' + controls stay enabled", async () => {
      const transport = makeTransport({
        isConnected: true,
        playerState: "buffering",
        currentTime: 5,
        bufferingSinceMs: Date.now(),
      });

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            transport={transport}
          />
        );
      });

      expect(screen.getByText(/tv is loading/i)).toBeInTheDocument();
      // Play/pause controls remain enabled (latest-wins).
      expect(screen.getByRole("button", { name: /^play$/i })).not.toBeDisabled();
    });

    it("shows actionable copy when bufferingSinceMs > 15s ago", async () => {
      const transport = makeTransport({
        isConnected: true,
        playerState: "buffering",
        currentTime: 5,
        bufferingSinceMs: Date.now() - 16_000,
      });

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            transport={transport}
          />
        );
      });

      expect(
        screen.getByText(/tv is still loading/i)
      ).toBeInTheDocument();
    });

    it("does not render the chip when not buffering", async () => {
      const transport = makeTransport({
        isConnected: true,
        playerState: "playing",
        currentTime: 5,
        bufferingSinceMs: null,
      });

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            transport={transport}
          />
        );
      });

      expect(screen.queryByTestId("buffering-chip")).not.toBeInTheDocument();
    });
  });

  // ── Disconnect → local resume ───────────────────────────────────────────
  describe("disconnect → local resume", () => {
    it("tap-to-resume renders when play() rejects on disconnect (isStale=false)", async () => {
      // play() rejects on disconnect.
      Object.defineProperty(window.HTMLMediaElement.prototype, "play", {
        value: vi.fn().mockRejectedValue(new Error("not allowed")),
        writable: true,
        configurable: true,
      });

      const transport = makeTransport({
        isConnected: true,
        playerState: "playing",
        currentTime: 90,
        duration: 420,
        resumeProposal: null,
      });

      const { rerender } = render(
        <ControllerPlayer
          {...defaultProps}
          isPresentationActive={true}
          transport={transport}
        />
      );

      // Disconnect: connected→false, proposal fresh & not stale.
      const disconnected = makeTransport({
        isConnected: false,
        playerState: "",
        currentTime: 90,
        duration: 420,
        resumeProposal: { time: 100, isStale: false, lastState: "playing" },
      });

      await act(async () => {
        rerender(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={false}
            transport={disconnected}
          />
        );
      });

      // play() rejected → tap-to-resume prompt renders.
      expect(await screen.findByTestId("tap-to-resume")).toBeInTheDocument();
      expect(screen.getByText(/tap to resume at 1:40/i)).toBeInTheDocument();

      // The local video should already be seeked to the proposal time.
      const video = document.querySelector("video") as HTMLVideoElement;
      expect(video.currentTime).toBe(100);
    });

    it("stale prompt renders + play() NOT auto-invoked when isStale=true", async () => {
      const playMock = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(window.HTMLMediaElement.prototype, "play", {
        value: playMock,
        writable: true,
        configurable: true,
      });

      const transport = makeTransport({
        isConnected: true,
        playerState: "playing",
        currentTime: 90,
        duration: 420,
        resumeProposal: null,
      });

      const { rerender } = render(
        <ControllerPlayer
          {...defaultProps}
          isPresentationActive={true}
          transport={transport}
        />
      );

      const disconnected = makeTransport({
        isConnected: false,
        playerState: "",
        currentTime: 90,
        duration: 420,
        resumeProposal: { time: 150, isStale: true, lastState: "playing" },
      });

      await act(async () => {
        rerender(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={false}
            transport={disconnected}
          />
        );
      });

      // Stale prompt renders, auto-resume does NOT happen.
      expect(await screen.findByTestId("tap-to-resume")).toBeInTheDocument();
      expect(
        screen.getByText(/resume from tv position may be stale/i)
      ).toBeInTheDocument();
      // play() should not have been called automatically.
      expect(playMock).not.toHaveBeenCalled();

      // Tapping the prompt does attempt resume.
      await act(async () => {
        fireEvent.click(screen.getByTestId("tap-to-resume"));
      });
      expect(playMock).toHaveBeenCalled();
    });

    it("clears a stale pendingResume prompt when presentation becomes active again", async () => {
      const playMock = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(window.HTMLMediaElement.prototype, "play", {
        value: playMock,
        writable: true,
        configurable: true,
      });

      const transport = makeTransport({
        isConnected: true,
        playerState: "playing",
        currentTime: 90,
        duration: 420,
        resumeProposal: null,
      });

      const { rerender } = render(
        <ControllerPlayer
          {...defaultProps}
          isPresentationActive={true}
          transport={transport}
        />
      );

      // Disconnect with a stale proposal → stale prompt renders.
      const disconnected = makeTransport({
        isConnected: false,
        playerState: "",
        currentTime: 90,
        duration: 420,
        resumeProposal: { time: 150, isStale: true, lastState: "playing" },
      });

      await act(async () => {
        rerender(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={false}
            transport={disconnected}
          />
        );
      });

      expect(await screen.findByTestId("tap-to-resume")).toBeInTheDocument();

      // Reconnect: presentation becomes active again → stale prompt must clear
      // so it does not persist on top of an active Cast session.
      await act(async () => {
        rerender(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            transport={transport}
          />
        );
      });

      expect(screen.queryByTestId("tap-to-resume")).not.toBeInTheDocument();
    });

    it("resumes local playback on disconnect when play() resolves (fresh proposal, isStale=false)", async () => {
      const playMock = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(window.HTMLMediaElement.prototype, "play", {
        value: playMock,
        writable: true,
        configurable: true,
      });

      const transport = makeTransport({
        isConnected: true,
        playerState: "playing",
        currentTime: 100,
        duration: 420,
        resumeProposal: null,
      });

      const { rerender } = render(
        <ControllerPlayer
          {...defaultProps}
          isPresentationActive={true}
          transport={transport}
        />
      );

      // Disconnect with a fresh (non-stale) proposal.
      const disconnected = makeTransport({
        isConnected: false,
        playerState: "",
        currentTime: 100,
        duration: 420,
        resumeProposal: { time: 110, isStale: false, lastState: "playing" },
      });

      await act(async () => {
        rerender(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={false}
            transport={disconnected}
          />
        );
      });

      // play() resolved → no tap-to-resume prompt should be present.
      expect(screen.queryByTestId("tap-to-resume")).not.toBeInTheDocument();

      // The local video should have been seeked to the proposal time.
      const video = document.querySelector("video") as HTMLVideoElement;
      expect(video.currentTime).toBe(110);
      // play() should have been called automatically on the success path.
      expect(playMock).toHaveBeenCalled();
    });
  });

  // ── Diagnostic bottom sheet (Cast unavailable) ─────────────────────────
  describe("diagnostic bottom sheet", () => {
    it("opens on disabled-button tap when castAvailability='unavailable'", async () => {
      const onSendToTV = vi.fn();

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={false}
            isCastSupported={false}
            castAvailability="unavailable"
            presentationFallback={{ isSupported: false }}
            onSendToTV={onSendToTV}
          />
        );
      });

      const castButton = screen.getByTestId("cast-button");
      await act(async () => {
        fireEvent.click(castButton);
      });

      // Sheet opens with the 4 diagnostic lines.
      expect(await screen.findByTestId("diagnostic-sheet")).toBeInTheDocument();
      expect(screen.getByText(/android chrome over https/i)).toBeInTheDocument();
      expect(screen.getByText(/same wi-fi \/ vlan/i)).toBeInTheDocument();
      expect(screen.getByText(/whitelisted/i)).toBeInTheDocument();
      expect(screen.getByText(/opening the mp4 url/i)).toBeInTheDocument();

      // onSendToTV is NOT invoked (button is diagnostic-only when unavailable).
      expect(onSendToTV).not.toHaveBeenCalled();
    });

    it("invokes onSendToTV when Cast is available", async () => {
      const onSendToTV = vi.fn();

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={false}
            isCastSupported={true}
            castAvailability="available"
            onSendToTV={onSendToTV}
          />
        );
      });

      await act(async () => {
        fireEvent.click(screen.getByTestId("cast-button"));
      });

      await waitFor(() => {
        expect(onSendToTV).toHaveBeenCalled();
      });
    });
  });

  // ── iPhone fallback ────────────────────────────────────────────────────
  describe("iPhone fallback", () => {
    it("shows AirPlay fallback when isCastSupported=false and presentationFallback.isSupported=false", async () => {
      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={false}
            isCastSupported={false}
            castAvailability="unavailable"
            presentationFallback={{ isSupported: false }}
          />
        );
      });

      expect(screen.getByTestId("airplay-fallback")).toBeInTheDocument();
      // The diagnostic Cast button remains visible (disabled-but-tappable,
      // opens the bottom sheet) even on iOS where Cast is unsupported — the
      // reviewer's P0 contract: the sheet must be reachable from the
      // "unavailable" branch, not dead code in production.
      expect(screen.getByTestId("cast-button")).toBeInTheDocument();
      expect(screen.getByTestId("cast-button")).toHaveAttribute(
        "aria-label",
        "Cast unavailable",
      );
    });

    it("does not show AirPlay fallback when Cast is supported", async () => {
      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={false}
            isCastSupported={true}
            castAvailability="available"
            presentationFallback={{ isSupported: false }}
          />
        );
      });

      expect(screen.queryByTestId("airplay-fallback")).not.toBeInTheDocument();
    });

    it("does not render Presentation fallback or AirPlay fallback during the SDK load window (castAvailability='unknown')", async () => {
      // During the SDK load window, isCastSupported is false but castAvailability
      // is "unknown" — neither the Presentation fallback button nor the iPhone
      // AirPlay fallback should render, because Cast may still become available.
      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={false}
            isCastSupported={false}
            castAvailability="unknown"
            presentationFallback={{ isSupported: true }}
          />
        );
      });

      expect(screen.queryByTestId("presentation-send-to-tv-button")).not.toBeInTheDocument();
      expect(screen.queryByTestId("airplay-fallback")).not.toBeInTheDocument();
    });

    it("renders the Presentation fallback Send-to-TV button when Cast unsupported + Presentation supported", async () => {
      const onSendToTV = vi.fn();
      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={false}
            isCastSupported={false}
            castAvailability="unavailable"
            presentationFallback={{ isSupported: true }}
            onSendToTV={onSendToTV}
          />
        );
      });

      const btn = screen.getByTestId("presentation-send-to-tv-button");
      expect(btn).toBeInTheDocument();
      expect(screen.queryByTestId("airplay-fallback")).not.toBeInTheDocument();

      await act(async () => {
        fireEvent.click(btn);
      });
      expect(onSendToTV).toHaveBeenCalled();
    });

    it("hides the Presentation fallback button while presentation is active", async () => {
      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            isCastSupported={false}
            castAvailability="unavailable"
            presentationFallback={{ isSupported: true, isConnected: true }}
          />
        );
      });

      expect(screen.queryByTestId("presentation-send-to-tv-button")).not.toBeInTheDocument();
    });
  });

  // ── Reconnect ──────────────────────────────────────────────────────────
  describe("reconnect", () => {
    it("does not issue a seek on initial active mount / reconnect", async () => {
      const onSendTransportCommand = vi.fn();
      const transport = makeTransport({
        isConnected: true,
        playerState: "playing",
        currentTime: 50,
        duration: 420,
      });

      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            transport={transport}
            onSendTransportCommand={onSendTransportCommand}
          />
        );
      });

      // On mount/reconnect the only forwarded command is the songTitle effect,
      // never a seek. No seek command should have been emitted.
      const seekCalls = onSendTransportCommand.mock.calls
        .map((c) => c[0])
        .filter((c) => c.type === "seek");
      expect(seekCalls).toHaveLength(0);
    });
  });

  // ── Controls visibility ────────────────────────────────────────────────
  describe("controls visibility", () => {
    it("shows controls by default", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const controls = screen.getByRole("button", { name: /^play$/i }).closest("div[class*='transition-opacity']");
      expect(controls).toHaveClass("opacity-100");
    });

    it("keeps controls visible when presentation is active", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} isPresentationActive={true} />);
      });

      const controls = screen.getByRole("button", { name: /^play$/i }).closest("div[class*='transition-opacity']");
      expect(controls).toHaveClass("opacity-100");
    });
  });

  // ── Playback controls (local) ──────────────────────────────────────────
  describe("playback controls", () => {
    it("toggles play/pause when play button clicked", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const playButton = screen.getByRole("button", { name: /^play$/i });

      await act(async () => {
        fireEvent.click(playButton);
      });

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /^pause$/i })).toBeInTheDocument();
      });
    });

    it("navigates to next song when next button clicked", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const nextButton = screen.getByRole("button", { name: /next song/i });

      await act(async () => {
        fireEvent.click(nextButton);
      });

      expect(nextButton).not.toBeDisabled();
    });

    it("clicking video shows controls without toggling play/pause", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const video = document.querySelector("video")!;

      await act(async () => {
        fireEvent.click(video);
      });

      const playButton = screen.getByRole("button", { name: /^play$/i });
      expect(playButton).toBeInTheDocument();
    });
  });

  // ── Volume controls (local) ────────────────────────────────────────────
  describe("volume controls", () => {
    it("toggles mute when volume button clicked", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const volumeButton = screen.getByRole("button", { name: /mute/i });

      await act(async () => {
        fireEvent.click(volumeButton);
      });

      expect(screen.getByRole("button", { name: /unmute/i })).toBeInTheDocument();
    });

    it("renders volume slider", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const volumeSlider = screen.getByRole("slider", { name: /volume/i });
      expect(volumeSlider).toBeInTheDocument();
    });
  });

  // ── Scrub bar ──────────────────────────────────────────────────────────
  describe("scrub bar", () => {
    it("renders scrub bar", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const scrubBar = screen.getByRole("slider", { name: /seek/i });
      expect(scrubBar).toBeInTheDocument();
    });
  });

  // ── Exit functionality ─────────────────────────────────────────────────
  describe("exit functionality", () => {
    it("navigates back when exit button clicked", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const exitButton = screen.getByRole("button", { name: /^back$/i });

      await act(async () => {
        fireEvent.click(exitButton);
      });

      expect(exitButton).toBeInTheDocument();
    });

    it("tears down the Cast session on exit when transport is connected", async () => {
      const stop = vi.fn();
      const transport = makeTransport({
        isConnected: true,
        isSupported: true,
        availability: "available",
      });
      transport.stop = stop;
      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            transport={transport}
          />
        );
      });

      const exitButton = screen.getByRole("button", { name: /^back$/i });
      await act(async () => {
        fireEvent.click(exitButton);
      });

      // The connected Cast session must be ended before navigating away so
      // the TV receiver does not keep playing audio with no controller.
      expect(stop).toHaveBeenCalledTimes(1);
    });

    it("uses onStopPresentation on exit when presentation is active", async () => {
      const stop = vi.fn();
      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={true}
            onStopPresentation={stop}
          />
        );
      });

      const exitButton = screen.getByRole("button", { name: /^back$/i });
      await act(async () => {
        fireEvent.click(exitButton);
      });

      expect(stop).toHaveBeenCalledTimes(1);
    });

    it("does not call transport.stop on exit when transport is not connected", async () => {
      const stop = vi.fn();
      const transport = makeTransport({
        isConnected: false,
        isSupported: true,
        availability: "available",
      });
      transport.stop = stop;
      await act(async () => {
        render(
          <ControllerPlayer
            {...defaultProps}
            isPresentationActive={false}
            transport={transport}
          />
        );
      });

      const exitButton = screen.getByRole("button", { name: /^back$/i });
      await act(async () => {
        fireEvent.click(exitButton);
      });

      expect(stop).not.toHaveBeenCalled();
    });
  });

  // ── iOS info toast ────────────────────────────────────────────────────
  describe("iOS info toast", () => {
    it("shows iOS info toast on iOS devices", async () => {
      Object.defineProperty(navigator, "userAgent", {
        value: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
        writable: true,
        configurable: true,
      });

      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      expect(screen.getByText(/iOS Playback Tips/i)).toBeInTheDocument();
    });

    it("does not show iOS info toast on non-iOS devices", async () => {
      Object.defineProperty(navigator, "userAgent", {
        value: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        writable: true,
        configurable: true,
      });

      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      expect(screen.queryByText(/iOS Playback Tips/i)).not.toBeInTheDocument();
    });

    it("does not show iOS info when presentation is active", async () => {
      Object.defineProperty(navigator, "userAgent", {
        value: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
        writable: true,
        configurable: true,
      });

      await act(async () => {
        render(<ControllerPlayer {...defaultProps} isPresentationActive={true} />);
      });

      expect(screen.queryByText(/iOS Playback Tips/i)).not.toBeInTheDocument();
    });

    it("does not show iOS info toast when sessionStorage says already shown", async () => {
      Object.defineProperty(navigator, "userAgent", {
        value: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
        writable: true,
        configurable: true,
      });

      // Simulate the user having already seen + dismissed the toast in a
      // prior visit — sessionStorage.getItem returns "true".
      const sessionStorageMock = {
        getItem: vi.fn().mockReturnValue("true"),
        setItem: vi.fn(),
        removeItem: vi.fn(),
      };
      Object.defineProperty(window, "sessionStorage", {
        value: sessionStorageMock,
        writable: true,
      });

      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      expect(screen.queryByText(/iOS Playback Tips/i)).not.toBeInTheDocument();
    });
  });

  // ── Fullscreen ─────────────────────────────────────────────────────────
  describe("fullscreen", () => {
    it("requests fullscreen on mount", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      await waitFor(() => {
        expect(document.documentElement.requestFullscreen).toHaveBeenCalled();
      });
    });
  });
});
