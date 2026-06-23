import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SettingsForm, UserSettingsData } from "@/components/settings/SettingsForm";

const defaultSettings: UserSettingsData = {
  offlineAutoCache: true,
  defaultGapBeats: 2.0,
  defaultVideoTemplate: "dark",
  defaultResolution: "720p",
  lyricsLoopWindowSeconds: 3.0,
  defaultFontSizePreset: "M",
  defaultFontFamily: "noto_serif_tc",
  defaultKeyShiftSemitones: 0,
  timingReviewFont: "sans",
};

function renderForm(
  props?: Partial<React.ComponentProps<typeof SettingsForm>>
) {
  const defaults = {
    initialSettings: defaultSettings,
    onSave: vi.fn().mockResolvedValue(undefined),
    ...props,
  };
  return render(<SettingsForm {...defaults} />);
}

describe("SettingsForm rendering", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders section headings", () => {
    renderForm();
    expect(screen.getByText("Transitions")).toBeInTheDocument();
    expect(screen.getByText("Video")).toBeInTheDocument();
    expect(screen.getByText("Playback")).toBeInTheDocument();
    expect(screen.getByText("Offline")).toBeInTheDocument();
  });

  it("renders default gap beats label", () => {
    renderForm();
    expect(screen.getByLabelText("Default gap beats")).toBeInTheDocument();
  });

  it("renders default template label", () => {
    renderForm();
    expect(screen.getByLabelText("Default template")).toBeInTheDocument();
  });

  it("renders default resolution label", () => {
    renderForm();
    expect(screen.getByLabelText("Default resolution")).toBeInTheDocument();
  });

  it("renders default font size label", () => {
    renderForm();
    expect(screen.getByLabelText("Default font size")).toBeInTheDocument();
  });

  it("renders default font family label", () => {
    renderForm();
    expect(screen.getByLabelText("Default font family")).toBeInTheDocument();
  });

  it("renders font preview text", () => {
    renderForm();
    expect(screen.getByText("耶和華是我的牧者")).toBeInTheDocument();
    expect(screen.getByText("我必不至缺乏")).toBeInTheDocument();
  });

  it("renders lyrics loop window label", () => {
    renderForm();
    expect(screen.getByLabelText("Lyrics loop window")).toBeInTheDocument();
  });

  it("renders offline auto-cache toggle", () => {
    renderForm();
    expect(screen.getByRole("switch")).toBeInTheDocument();
  });

  it("renders Save button disabled initially (no dirty state)", () => {
    renderForm();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("renders Reset button disabled initially (no dirty state)", () => {
    renderForm();
    expect(screen.getByRole("button", { name: "Reset" })).toBeDisabled();
  });
});

describe("SettingsForm interactions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("enables Save button after changing offline toggle", async () => {
    renderForm();
    const toggle = screen.getByRole("switch");
    fireEvent.click(toggle);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Save" })).not.toBeDisabled();
    });
  });

  it("enables Reset button after making a change", async () => {
    renderForm();
    const toggle = screen.getByRole("switch");
    fireEvent.click(toggle);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Reset" })).not.toBeDisabled();
    });
  });

  it("calls onSave with current settings on form submit", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    renderForm({ onSave });

    // Dirty the form
    const toggle = screen.getByRole("switch");
    fireEvent.click(toggle);

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(onSave).toHaveBeenCalledTimes(1);
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({
          offlineAutoCache: false,
        })
      );
    });
  });

  it("resets form to initial settings on Reset click", async () => {
    const onSave = vi.fn();
    renderForm({ onSave });

    // Dirty the form
    const toggle = screen.getByRole("switch");
    fireEvent.click(toggle);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Reset" })).not.toBeDisabled();
    });

    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    await waitFor(() => {
      // After reset, buttons should be disabled again
      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Reset" })).toBeDisabled();
    });
  });

  it("shows Saving... text when isSaving is true", () => {
    renderForm({ isSaving: true });
    expect(screen.getByRole("button", { name: "Saving..." })).toBeInTheDocument();
  });
});

describe("SettingsForm iOS note", () => {
  const originalNavigator = global.navigator;

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    Object.defineProperty(global, "navigator", {
      value: originalNavigator,
      writable: true,
      configurable: true,
    });
  });

  it("does not show iOS note on non-iOS device", () => {
    Object.defineProperty(global, "navigator", {
      value: { userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)" },
      writable: true,
      configurable: true,
    });
    renderForm();
    expect(screen.queryByTestId("ios-offline-note")).not.toBeInTheDocument();
  });

  it("shows iOS note when on iOS < 17.4", () => {
    Object.defineProperty(global, "navigator", {
      value: {
        userAgent:
          "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15",
      },
      writable: true,
      configurable: true,
    });
    renderForm();
    expect(screen.getByTestId("ios-offline-note")).toBeInTheDocument();
    expect(screen.getByTestId("ios-offline-note")).toHaveTextContent(
      "Offline caching requires iOS 17.4 or later"
    );
  });

  it("does not show iOS note on iOS 17.4", () => {
    Object.defineProperty(global, "navigator", {
      value: {
        userAgent:
          "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15",
      },
      writable: true,
      configurable: true,
    });
    renderForm();
    expect(screen.queryByTestId("ios-offline-note")).not.toBeInTheDocument();
  });
});

describe("SettingsForm desktop-only section", () => {
  it("renders Advanced card with hidden class on mobile, visible on lg", () => {
    renderForm();
    const advancedHeading = screen.getByText("Advanced");
    // The parent div should have hidden lg:block classes
    const wrapper = advancedHeading.closest(".hidden");
    expect(wrapper).toBeInTheDocument();
    expect(wrapper?.classList.contains("lg:block")).toBe(true);
  });

  it("renders default key shift label", () => {
    renderForm();
    expect(screen.getByLabelText("Default key shift")).toBeInTheDocument();
  });

  it("renders timing review font label", () => {
    renderForm();
    expect(screen.getByLabelText("Timing review font")).toBeInTheDocument();
  });
});
