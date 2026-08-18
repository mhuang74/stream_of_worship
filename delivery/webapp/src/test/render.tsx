import { render, RenderOptions } from "@testing-library/react";
import { ReactElement, ReactNode } from "react";
import { LocaleProvider } from "@/contexts/LocaleContext";
import type { Locale } from "@/lib/i18n/messages";

/**
 * Render wrapped in a LocaleProvider so components that call `useLocale()` can
 * be tested in isolation. Defaults to English; pass a locale for zh-Hant cases.
 */
export function renderWithLocale(
  ui: ReactElement,
  initialLocale: Locale = "en",
  options?: Omit<RenderOptions, "wrapper">
) {
  function Wrapper({ children }: { children: ReactNode }) {
    return <LocaleProvider initialLocale={initialLocale}>{children}</LocaleProvider>;
  }
  return render(ui, { ...options, wrapper: Wrapper });
}
