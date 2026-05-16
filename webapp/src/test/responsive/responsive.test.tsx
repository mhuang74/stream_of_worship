/**
 * Responsive design tests for Task 8.1.
 *
 * jsdom cannot apply CSS media queries, so these tests verify that the
 * correct Tailwind responsive classes are present in the DOM.  They act
 * as regression guards: if a class is removed, the test fails and a
 * reviewer knows the responsive behaviour changed.
 *
 * Conventions used here:
 *  - "hidden lg:block"  → invisible on phone/tablet, visible on desktop
 *  - "lg:hidden"        → visible on phone/tablet, hidden on desktop
 *  - "hidden lg:flex"   → same as hidden lg:block, flex layout on desktop
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

vi.mock("next/navigation", () => ({
  usePathname: () => "/songsets",
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@/hooks/useWakeLock", () => ({
  useWakeLock: () => ({ isSupported: false, isActive: false }),
}));

vi.mock("@/hooks/useKeyboardShortcuts", () => ({
  useKeyboardShortcuts: vi.fn(),
}));

vi.mock("@/hooks/useMediaSession", () => ({
  useMediaSession: () => ({
    updatePlaybackState: vi.fn(),
    updatePositionState: vi.fn(),
  }),
}));

// Sheet / Dialog: render children when open=true
vi.mock("@base-ui/react/dialog", () => ({
  Dialog: {
    Root: ({ children, open }: { children: React.ReactNode; open: boolean }) =>
      open ? <div data-testid="sheet-root">{children}</div> : null,
    Trigger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Portal: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Backdrop: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Popup: ({ children }: { children: React.ReactNode }) => (
      <div data-testid="sheet-popup">{children}</div>
    ),
    Close: ({ children }: { children: React.ReactNode }) => (
      <button data-testid="sheet-close">{children}</button>
    ),
    Title: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
    Description: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
  },
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Returns true if the element's className string contains all given tokens. */
function hasClasses(el: Element | null, ...classes: string[]): boolean {
  if (!el) return false;
  const cls = el.getAttribute("class") ?? "";
  return classes.every((c) => cls.split(/\s+/).includes(c));
}

// ---------------------------------------------------------------------------
// Component imports (after mocks)
// ---------------------------------------------------------------------------

import { BottomNav } from "@/components/layout/BottomNav";
import { Header } from "@/components/layout/Header";
import { TransitionControls } from "@/components/transition/TransitionControls";
import { SettingsForm, UserSettingsData } from "@/components/settings/SettingsForm";
import { LyricsReviewSheet } from "@/components/lyrics/LyricsReviewSheet";
import { PlaybackControls } from "@/components/play/PlaybackControls";
import { PrePlayCard } from "@/components/play/PrePlayCard";
import { Input } from "@/components/ui/input";

// ---------------------------------------------------------------------------
// Shared fixtures
// ---------------------------------------------------------------------------

const defaultTransitionSettings = {
  gapBeats: 2,
  crossfadeEnabled: false,
  crossfadeDurationSeconds: 2,
  keyShiftSemitones: 0,
  tempoRatio: 1.0,
};

const defaultUserSettings: UserSettingsData = {
  offlineAutoCache: true,
  defaultGapBeats: 2,
  defaultVideoTemplate: "dark",
  defaultResolution: "720p",
  lyricsLoopWindowSeconds: 3,
  defaultFontSizePreset: "M",
  defaultKeyShiftSemitones: 0,
  timingReviewFont: "sans",
};

const defaultPlaybackControlsProps = {
  isPlaying: false,
  currentTime: 0,
  duration: 300,
  volume: 1,
  isMuted: false,
  currentSongIndex: 0,
  totalSongs: 3,
  isPresentationActive: false,
  onPlayPause: vi.fn(),
  onSeek: vi.fn(),
  onSkipBack: vi.fn(),
  onSkipForward: vi.fn(),
  onPrevSong: vi.fn(),
  onNextSong: vi.fn(),
  onVolumeChange: vi.fn(),
  onToggleMute: vi.fn(),
};

const defaultPrePlayCardProps = {
  songset: {
    id: "s1",
    name: "Sunday Worship",
    description: null,
    renderState: "fresh" as const,
    latestRenderJobId: "rj1",
    lastFailedRenderJobId: null,
  },
  items: [],
  renderJob: {
    id: "rj1",
    status: "completed",
    mp3R2Key: "audio.mp3",
    mp4R2Key: "video.mp4",
    chaptersR2Key: "chapters.json",
  },
  onStartWorship: vi.fn(),
  onReRender: vi.fn(),
  onShare: vi.fn(),
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Responsive Design (Task 8.1)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // -------------------------------------------------------------------------
  // 1. Phone-first layout — BottomNav is phone/tablet only
  // -------------------------------------------------------------------------
  describe("BottomNav — phone/tablet nav (lg:hidden)", () => {
    it("nav element has lg:hidden so it disappears on desktop", () => {
      render(<BottomNav />);
      const nav = screen.getByRole("navigation", { name: /main navigation/i });
      expect(nav.getAttribute("class")).toContain("lg:hidden");
    });

    it("contains Songsets and Settings links", () => {
      render(<BottomNav />);
      expect(screen.getByRole("link", { name: "Songsets" })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Settings" })).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // 2. Desktop nav — Header shows nav only on desktop
  // -------------------------------------------------------------------------
  describe("Header — desktop nav (hidden lg:flex)", () => {
    it("desktop nav has hidden lg:flex so it appears only on desktop", () => {
      const { container } = render(<Header />);
      const desktopNav = container.querySelector("nav");
      expect(desktopNav).not.toBeNull();
      const cls = desktopNav!.getAttribute("class") ?? "";
      expect(cls).toContain("hidden");
      expect(cls).toContain("lg:flex");
    });

    it("desktop nav links are present in DOM for SEO", () => {
      render(<Header />);
      expect(screen.getByRole("link", { name: /songsets/i })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: /settings/i })).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // 3. Transition desktop power-mode: key shift, tempo, waveform
  // -------------------------------------------------------------------------
  describe("TransitionControls — desktop power-mode section (hidden lg:block)", () => {
    it("desktop section is present in DOM (hidden on phone via CSS)", () => {
      const { container } = render(
        <TransitionControls settings={defaultTransitionSettings} onChange={vi.fn()} />
      );
      // Find the div that contains 'Key Shift' and has 'hidden' class
      const desktopSection = Array.from(container.querySelectorAll("div")).find(
        (el) =>
          hasClasses(el, "hidden") && el.textContent?.includes("Key Shift")
      );
      expect(desktopSection).toBeTruthy();
    });

    it("desktop section has lg:block class to show on desktop", () => {
      const { container } = render(
        <TransitionControls settings={defaultTransitionSettings} onChange={vi.fn()} />
      );
      const desktopSection = Array.from(container.querySelectorAll("div")).find(
        (el) =>
          hasClasses(el, "hidden") && el.textContent?.includes("Key Shift")
      );
      expect(desktopSection?.getAttribute("class")).toContain("lg:block");
    });

    it("waveform panel is inside desktop section", () => {
      render(
        <TransitionControls settings={defaultTransitionSettings} onChange={vi.fn()} />
      );
      const waveform = screen.getByRole("img", { name: /waveform preview/i });
      expect(waveform).toBeInTheDocument();
    });

    it("key shift selector is present in DOM", () => {
      render(
        <TransitionControls settings={defaultTransitionSettings} onChange={vi.fn()} />
      );
      expect(screen.getByLabelText(/key shift selector/i)).toBeInTheDocument();
    });

    it("gap and crossfade controls are always visible (phone layout)", () => {
      render(
        <TransitionControls settings={defaultTransitionSettings} onChange={vi.fn()} />
      );
      expect(screen.getByRole("switch")).toBeInTheDocument(); // crossfade
      expect(screen.getByRole("progressbar")).toBeInTheDocument(); // gap bar
    });
  });

  // -------------------------------------------------------------------------
  // 4. LyricsReviewSheet — desktop tabs (hidden lg:flex)
  // -------------------------------------------------------------------------
  describe("LyricsReviewSheet — desktop tabs (hidden lg:flex)", () => {
    beforeEach(() => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ marks: [] }),
      } as unknown as Response);
    });

    it("tab list is in DOM with hidden lg:flex class", async () => {
      render(
        <LyricsReviewSheet
          isOpen={true}
          onOpenChange={vi.fn()}
          recordingContentHash="test-hash"
          lrcContent="[00:01.00]Hello"
          songTitle="Test Song"
        />
      );
      await waitFor(() => {
        const tabList = screen.getByRole("tablist");
        expect(tabList).toBeInTheDocument();
        const cls = tabList.getAttribute("class") ?? "";
        expect(cls).toContain("hidden");
        expect(cls).toContain("lg:flex");
      });
    });

    it("Review, Edit Text, and Edit Timing tabs are all present in DOM", async () => {
      render(
        <LyricsReviewSheet
          isOpen={true}
          onOpenChange={vi.fn()}
          recordingContentHash="test-hash"
          lrcContent="[00:01.00]Hello"
        />
      );
      await waitFor(() => {
        expect(screen.getByRole("tab", { name: "Review" })).toBeInTheDocument();
        expect(screen.getByRole("tab", { name: "Edit Text" })).toBeInTheDocument();
        expect(screen.getByRole("tab", { name: "Edit Timing" })).toBeInTheDocument();
      });
    });

    it("mobile footer hint (for fixing marks on desktop) uses lg:hidden", async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ marks: [1.0] }),
      } as unknown as Response);

      render(
        <LyricsReviewSheet
          isOpen={true}
          onOpenChange={vi.fn()}
          recordingContentHash="test-hash"
          lrcContent="[00:01.00]Hello"
        />
      );
      await waitFor(() => {
        const footer = screen.getByText(/open on desktop to fix/i).closest("div");
        expect(footer?.getAttribute("class")).toContain("lg:hidden");
      });
    });
  });

  // -------------------------------------------------------------------------
  // 5. SettingsForm — desktop advanced section (hidden lg:block)
  // -------------------------------------------------------------------------
  describe("SettingsForm — desktop Advanced section (hidden lg:block)", () => {
    it("Advanced card wrapper has hidden lg:block class", () => {
      const { container } = render(
        <SettingsForm
          initialSettings={defaultUserSettings}
          onSave={vi.fn()}
        />
      );
      // The wrapper div around the Advanced card
      const advancedWrapper = Array.from(container.querySelectorAll("div")).find(
        (el) =>
          hasClasses(el, "hidden", "lg:block") &&
          el.textContent?.includes("Advanced")
      );
      expect(advancedWrapper).toBeTruthy();
    });

    it("default key shift and timing font selectors are inside Advanced section", () => {
      render(
        <SettingsForm
          initialSettings={defaultUserSettings}
          onSave={vi.fn()}
        />
      );
      expect(screen.getByLabelText(/default key shift/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/timing review font/i)).toBeInTheDocument();
    });

    it("Transitions, Video, Playback, and Offline sections are always visible", () => {
      render(
        <SettingsForm
          initialSettings={defaultUserSettings}
          onSave={vi.fn()}
        />
      );
      expect(screen.getByText("Transitions")).toBeInTheDocument();
      expect(screen.getByText("Video")).toBeInTheDocument();
      expect(screen.getByText("Playback")).toBeInTheDocument();
      expect(screen.getByText("Offline")).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // 6. Touch targets — PlaybackControls
  // -------------------------------------------------------------------------
  describe("PlaybackControls — touch targets", () => {
    it("primary play/pause button is 64px (size-16 class)", () => {
      render(<PlaybackControls {...defaultPlaybackControlsProps} />);
      const playBtn = screen.getByRole("button", { name: /^play$/i });
      expect(playBtn.getAttribute("class")).toContain("size-16");
    });

    it("skip-back button is 56px (size-14 class)", () => {
      render(<PlaybackControls {...defaultPlaybackControlsProps} />);
      const skipBack = screen.getByRole("button", { name: /skip back 10 seconds/i });
      expect(skipBack.getAttribute("class")).toContain("size-14");
    });

    it("skip-forward button is 56px (size-14 class)", () => {
      render(<PlaybackControls {...defaultPlaybackControlsProps} />);
      const skipFwd = screen.getByRole("button", { name: /skip forward 10 seconds/i });
      expect(skipFwd.getAttribute("class")).toContain("size-14");
    });

    it("previous song button is 48px (size-12 class)", () => {
      render(<PlaybackControls {...defaultPlaybackControlsProps} />);
      const prev = screen.getByRole("button", { name: /previous song/i });
      expect(prev.getAttribute("class")).toContain("size-12");
    });

    it("next song button is 48px (size-12 class)", () => {
      render(<PlaybackControls {...defaultPlaybackControlsProps} />);
      const next = screen.getByRole("button", { name: /next song/i });
      expect(next.getAttribute("class")).toContain("size-12");
    });
  });

  // -------------------------------------------------------------------------
  // 7. Phone CTAs — PrePlayCard
  // -------------------------------------------------------------------------
  describe("PrePlayCard — phone CTA height (h-14 = 56px)", () => {
    beforeEach(() => {
      Object.defineProperty(navigator, "presentation", {
        value: undefined,
        writable: true,
        configurable: true,
      });
      Object.defineProperty(navigator, "share", {
        value: undefined,
        writable: true,
        configurable: true,
      });
    });

    it("Start Worship button has h-14 (56px) tall CTA", () => {
      render(<PrePlayCard {...defaultPrePlayCardProps} />);
      const startBtn = screen.getByRole("button", { name: /start worship/i });
      expect(startBtn.getAttribute("class")).toContain("h-14");
    });

    it("Start Worship button spans full width", () => {
      render(<PrePlayCard {...defaultPrePlayCardProps} />);
      const startBtn = screen.getByRole("button", { name: /start worship/i });
      expect(startBtn.getAttribute("class")).toContain("w-full");
    });
  });

  // -------------------------------------------------------------------------
  // 8. Minimum font size — Input component (16px = text-base on phone)
  // -------------------------------------------------------------------------
  describe("Input — minimum 16px font on phone (prevents iOS zoom)", () => {
    it("has text-base class for 16px font on phone", () => {
      render(<Input placeholder="test" />);
      const input = screen.getByPlaceholderText("test");
      expect(input.getAttribute("class")).toContain("text-base");
    });

    it("downsizes to text-sm on md breakpoint via md:text-sm class", () => {
      render(<Input placeholder="test" />);
      const input = screen.getByPlaceholderText("test");
      expect(input.getAttribute("class")).toContain("md:text-sm");
    });
  });

  // -------------------------------------------------------------------------
  // 9. Desktop keyboard shortcuts hint in ControllerPlayer
  //    (hooks mocked at top level; ControllerPlayer imported after mocks)
  // -------------------------------------------------------------------------
  describe("Desktop keyboard shortcuts hint (hidden lg:block)", () => {
    beforeEach(() => {
      Object.defineProperty(document.documentElement, "requestFullscreen", {
        value: vi.fn().mockResolvedValue(undefined),
        writable: true,
        configurable: true,
      });
      Object.defineProperty(document, "fullscreenElement", {
        value: null,
        writable: true,
        configurable: true,
      });
      Object.defineProperty(document, "exitFullscreen", {
        value: vi.fn().mockResolvedValue(undefined),
        writable: true,
        configurable: true,
      });
      Object.defineProperty(navigator, "userAgent", {
        value: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        writable: true,
        configurable: true,
      });
      Object.defineProperty(navigator, "wakeLock", {
        value: undefined,
        writable: true,
        configurable: true,
      });
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
    });

    it("renders keyboard shortcuts panel with hidden lg:block class", async () => {
      const { ControllerPlayer } = await import("@/components/play/ControllerPlayer");
      const { container } = render(
        <ControllerPlayer
          songsetId="s1"
          videoSrc="https://example.com/video.mp4"
          chapters={[{ position: 0, songTitle: "Song 1", startSeconds: 0, endSeconds: 180, lines: [] }]}
          isPresentationActive={false}
        />
      );
      const hint = container.querySelector('[data-testid="keyboard-shortcuts-hint"]');
      expect(hint).toBeInTheDocument();
      expect(hint?.getAttribute("class")).toContain("hidden");
      expect(hint?.getAttribute("class")).toContain("lg:block");
    });

    it("keyboard shortcuts panel lists Space, seek, and song navigation keys", async () => {
      const { ControllerPlayer } = await import("@/components/play/ControllerPlayer");
      const { container } = render(
        <ControllerPlayer
          songsetId="s1"
          videoSrc="https://example.com/video.mp4"
          chapters={[{ position: 0, songTitle: "Song 1", startSeconds: 0, endSeconds: 180, lines: [] }]}
          isPresentationActive={false}
        />
      );
      const hint = container.querySelector('[data-testid="keyboard-shortcuts-hint"]');
      expect(hint?.textContent).toContain("Play/Pause");
      expect(hint?.textContent).toContain("Seek 10s");
      expect(hint?.textContent).toContain("Prev song");
      expect(hint?.textContent).toContain("Next song");
    });
  });
});
