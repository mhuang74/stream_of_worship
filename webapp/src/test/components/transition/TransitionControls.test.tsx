import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TransitionControls } from "@/components/transition/TransitionControls";
import { TransitionSettings } from "@/components/songset/TransitionPanel";

const defaultSettings: TransitionSettings = {
  gapBeats: 2,
  crossfadeEnabled: false,
  crossfadeDurationSeconds: 2,
  keyShiftSemitones: 0,
  tempoRatio: 1.0,
};

const defaultProps = {
  settings: defaultSettings,
  onChange: vi.fn(),
};

function renderControls(props?: Partial<React.ComponentProps<typeof TransitionControls>>) {
  return render(<TransitionControls {...defaultProps} {...props} />);
}

describe("TransitionControls", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("gap control", () => {
    it("displays gap beats and seconds", () => {
      renderControls();
      // 2 beats at 120 BPM = 1.0 seconds
      expect(screen.getByLabelText("gap value")).toHaveTextContent("2 beats (1.0s)");
    });

    it("uses tempo from fromSong for seconds calculation", () => {
      renderControls({ fromSong: { title: "Song A", tempoBpm: 60 } });
      // 2 beats at 60 BPM = 2.0 seconds
      expect(screen.getByLabelText("gap value")).toHaveTextContent("2 beats (2.0s)");
    });

    it("renders decrease button", () => {
      renderControls();
      expect(
        screen.getByRole("button", { name: /decrease gap by 0.5 beats/i })
      ).toBeInTheDocument();
    });

    it("renders increase button", () => {
      renderControls();
      expect(
        screen.getByRole("button", { name: /increase gap by 0.5 beats/i })
      ).toBeInTheDocument();
    });

    it("calls onChange with decreased gap on minus click", () => {
      const onChange = vi.fn();
      renderControls({ onChange });
      fireEvent.click(screen.getByRole("button", { name: /decrease gap by 0.5 beats/i }));
      expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ gapBeats: 1.5 }));
    });

    it("calls onChange with increased gap on plus click", () => {
      const onChange = vi.fn();
      renderControls({ onChange });
      fireEvent.click(screen.getByRole("button", { name: /increase gap by 0.5 beats/i }));
      expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ gapBeats: 2.5 }));
    });

    it("disables decrease button when gap is 0", () => {
      renderControls({ settings: { ...defaultSettings, gapBeats: 0 } });
      expect(
        screen.getByRole("button", { name: /decrease gap by 0.5 beats/i })
      ).toBeDisabled();
    });

    it("disables increase button when gap is 8", () => {
      renderControls({ settings: { ...defaultSettings, gapBeats: 8 } });
      expect(
        screen.getByRole("button", { name: /increase gap by 0.5 beats/i })
      ).toBeDisabled();
    });

    it("clamps decrease at 0 beats", () => {
      const onChange = vi.fn();
      renderControls({ settings: { ...defaultSettings, gapBeats: 0.5 }, onChange });
      fireEvent.click(screen.getByRole("button", { name: /decrease gap by 0.5 beats/i }));
      expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ gapBeats: 0 }));
    });

    it("clamps increase at 8 beats", () => {
      const onChange = vi.fn();
      renderControls({ settings: { ...defaultSettings, gapBeats: 7.5 }, onChange });
      fireEvent.click(screen.getByRole("button", { name: /increase gap by 0.5 beats/i }));
      expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ gapBeats: 8 }));
    });
  });

  describe("crossfade toggle", () => {
    it("renders crossfade switch", () => {
      renderControls();
      expect(screen.getByRole("switch")).toBeInTheDocument();
    });

    it("reflects crossfade enabled state", () => {
      renderControls({ settings: { ...defaultSettings, crossfadeEnabled: true } });
      expect(screen.getByRole("switch")).toBeChecked();
    });

    it("reflects crossfade disabled state", () => {
      renderControls({ settings: { ...defaultSettings, crossfadeEnabled: false } });
      expect(screen.getByRole("switch")).not.toBeChecked();
    });

    it("calls onChange with toggled crossfade", () => {
      const onChange = vi.fn();
      renderControls({ onChange });
      fireEvent.click(screen.getByRole("switch"));
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ crossfadeEnabled: true })
      );
    });
  });

  describe("preview button", () => {
    it("renders preview button when onPreview provided", () => {
      renderControls({ onPreview: vi.fn() });
      expect(
        screen.getByRole("button", { name: /preview transition audio/i })
      ).toBeInTheDocument();
    });

    it("does not render preview button when onPreview is not provided", () => {
      renderControls({ onPreview: undefined });
      expect(
        screen.queryByRole("button", { name: /preview transition audio/i })
      ).not.toBeInTheDocument();
    });

    it("calls onPreview when clicked", () => {
      const onPreview = vi.fn();
      renderControls({ onPreview });
      fireEvent.click(screen.getByRole("button", { name: /preview transition audio/i }));
      expect(onPreview).toHaveBeenCalledTimes(1);
    });

    it("disables button when isPreviewLoading is true", () => {
      renderControls({ onPreview: vi.fn(), isPreviewLoading: true });
      expect(
        screen.getByRole("button", { name: /preview transition audio/i })
      ).toBeDisabled();
    });
  });

  describe("waveform panel", () => {
    it("renders waveform preview element", () => {
      renderControls();
      // Waveform is hidden on mobile via CSS but present in DOM
      expect(screen.getByRole("img", { name: /waveform preview/i })).toBeInTheDocument();
    });
  });

  describe("gap progress bar", () => {
    it("renders progress bar for gap", () => {
      renderControls();
      expect(screen.getByRole("progressbar")).toBeInTheDocument();
    });

    it("sets aria-valuenow to gap beats", () => {
      renderControls({ settings: { ...defaultSettings, gapBeats: 4 } });
      const bar = screen.getByRole("progressbar");
      expect(bar).toHaveAttribute("aria-valuenow", "4");
    });
  });
});
