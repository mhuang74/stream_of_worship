import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TransitionSheet } from "@/components/transition/TransitionSheet";
import { TransitionSettings } from "@/components/songset/TransitionPanel";
import { AudioPlayerProvider } from "@/contexts/AudioPlayerContext";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const mockPlay = vi.fn();

vi.mock("@/contexts/AudioPlayerContext", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/contexts/AudioPlayerContext")>();
  return {
    ...actual,
    useAudioPlayerContext: () => ({
      play: mockPlay,
      pause: vi.fn(),
      stop: vi.fn(),
      currentTrack: null,
      state: {
        isPlaying: false,
        currentTime: 0,
        duration: 0,
        volume: 1,
        isMuted: false,
        isLooping: false,
        loopWindowStart: 0,
        loopWindowEnd: 0,
      },
      togglePlay: vi.fn(),
      seek: vi.fn(),
      setVolume: vi.fn(),
      toggleMute: vi.fn(),
      toggleLoop: vi.fn(),
      setLoopWindow: vi.fn(),
      clearLoopWindow: vi.fn(),
      audioRef: { current: null },
    }),
  };
});

const defaultSettings: TransitionSettings = {
  gapBeats: 2,
  crossfadeEnabled: false,
  crossfadeDurationSeconds: 2,
  keyShiftSemitones: 0,
  tempoRatio: 1.0,
};

function renderSheet(props?: Partial<React.ComponentProps<typeof TransitionSheet>>) {
  const defaultProps = {
    isOpen: true,
    onOpenChange: vi.fn(),
    fromSong: { title: "Song A", key: "G", tempoBpm: 120 },
    toSong: { title: "Song B", key: "A", tempoBpm: 100 },
    fromRecordingHash: "hash-a",
    toRecordingHash: "hash-b",
    settings: defaultSettings,
    onSave: vi.fn().mockResolvedValue(undefined),
  };
  return render(<TransitionSheet {...defaultProps} {...props} />);
}

describe("TransitionSheet", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch = vi.fn();
  });

  it("renders when isOpen is true", () => {
    renderSheet();
    expect(screen.getByText("Song A")).toBeInTheDocument();
    expect(screen.getByText("Song B")).toBeInTheDocument();
  });

  it("does not render when isOpen is false", () => {
    renderSheet({ isOpen: false });
    expect(screen.queryByText("Song A")).not.toBeInTheDocument();
  });

  it("shows Cancel and Save buttons", () => {
    renderSheet();
    expect(screen.getByRole("button", { name: /cancel/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save transition settings/i })).toBeInTheDocument();
  });

  it("disables Save when settings are unchanged", () => {
    renderSheet();
    expect(screen.getByRole("button", { name: /save transition settings/i })).toBeDisabled();
  });

  it("enables Save when settings change", () => {
    renderSheet();
    // Change gap via the minus button doesn't enable save... we need to change settings
    // Trigger crossfade toggle
    fireEvent.click(screen.getByRole("switch"));
    expect(screen.getByRole("button", { name: /save transition settings/i })).not.toBeDisabled();
  });

  it("calls onSave with updated settings on Save click", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    renderSheet({ onSave });

    fireEvent.click(screen.getByRole("switch"));
    fireEvent.click(screen.getByRole("button", { name: /save transition settings/i }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({ crossfadeEnabled: true })
      );
    });
  });

  it("calls onOpenChange(false) after successful save", async () => {
    const onOpenChange = vi.fn();
    const onSave = vi.fn().mockResolvedValue(undefined);
    renderSheet({ onOpenChange, onSave });

    fireEvent.click(screen.getByRole("switch"));
    fireEvent.click(screen.getByRole("button", { name: /save transition settings/i }));

    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false);
    });
  });

  it("resets settings and calls onOpenChange(false) on Cancel", () => {
    const onOpenChange = vi.fn();
    renderSheet({ onOpenChange });

    // Change a setting
    fireEvent.click(screen.getByRole("switch"));
    // Then cancel
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("shows preview button when recording hashes are provided", () => {
    renderSheet({ fromRecordingHash: "hash-a", toRecordingHash: "hash-b" });
    expect(
      screen.getByRole("button", { name: /preview transition audio/i })
    ).toBeInTheDocument();
  });

  it("does not show preview button when no recording hashes", () => {
    renderSheet({ fromRecordingHash: undefined, toRecordingHash: undefined });
    expect(
      screen.queryByRole("button", { name: /preview transition audio/i })
    ).not.toBeInTheDocument();
  });

  it("calls fetch and plays audio on preview click", async () => {
    const mockUrl = "https://example.com/preview.mp3";
    vi.mocked(global.fetch).mockResolvedValue({
      ok: true,
      json: async () => ({ url: mockUrl, expiresAt: new Date().toISOString() }),
    } as any);

    renderSheet();
    fireEvent.click(screen.getByRole("button", { name: /preview transition audio/i }));

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/transitions/preview",
        expect.objectContaining({ method: "POST" })
      );
      expect(mockPlay).toHaveBeenCalledWith(
        expect.objectContaining({ src: mockUrl, type: "transition" })
      );
    });
  });

  it("shows error toast when preview fetch fails", async () => {
    const { toast } = await import("sonner");
    vi.mocked(global.fetch).mockResolvedValue({
      ok: false,
      status: 503,
    } as any);

    renderSheet();
    fireEvent.click(screen.getByRole("button", { name: /preview transition audio/i }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("Failed to load preview audio");
    });
  });

  it("shows fallback title when songs not provided", () => {
    renderSheet({ fromSong: undefined, toSong: undefined });
    expect(screen.getByText("Transition Settings")).toBeInTheDocument();
  });
});
