import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { LocaleProvider, useLocaleContext } from "@/contexts/LocaleContext";

function Harness() {
  const { locale, setLocale, t } = useLocaleContext();
  return (
    <div>
      <span data-testid="locale">{locale}</span>
      <span data-testid="nav">{t("nav.songsets")}</span>
      <button onClick={() => setLocale("zh-Hant")}>toggle</button>
    </div>
  );
}

describe("LocaleContext", () => {
  it("throws when used outside a LocaleProvider", () => {
    expect(() => render(<Harness />)).toThrow("useLocale must be used within a LocaleProvider");
  });

  it("defaults to English", () => {
    render(
      <LocaleProvider>
        <Harness />
      </LocaleProvider>
    );
    expect(screen.getByTestId("locale").textContent).toBe("en");
    expect(screen.getByTestId("nav").textContent).toBe("Songsets");
  });

  it("resolves 繁體中文 strings from the initial locale", () => {
    render(
      <LocaleProvider initialLocale="zh-Hant">
        <Harness />
      </LocaleProvider>
    );
    expect(screen.getByTestId("locale").textContent).toBe("zh-Hant");
    expect(screen.getByTestId("nav").textContent).toBe("詩歌集");
  });

  it("switches language and updates the document lang attribute", () => {
    render(
      <LocaleProvider>
        <Harness />
      </LocaleProvider>
    );
    expect(document.documentElement.lang).toBe("en");

    fireEvent.click(screen.getByText("toggle"));

    expect(screen.getByTestId("locale").textContent).toBe("zh-Hant");
    expect(screen.getByTestId("nav").textContent).toBe("詩歌集");
    expect(document.documentElement.lang).toBe("zh-Hant");
  });

  it("mirrors a changed initialLocale prop into state and document lang", () => {
    const { rerender } = render(
      <LocaleProvider initialLocale="en">
        <Harness />
      </LocaleProvider>
    );
    expect(screen.getByTestId("locale").textContent).toBe("en");
    expect(document.documentElement.lang).toBe("en");

    rerender(
      <LocaleProvider initialLocale="zh-Hant">
        <Harness />
      </LocaleProvider>
    );

    expect(screen.getByTestId("locale").textContent).toBe("zh-Hant");
    expect(screen.getByTestId("nav").textContent).toBe("詩歌集");
    expect(document.documentElement.lang).toBe("zh-Hant");
  });
});
