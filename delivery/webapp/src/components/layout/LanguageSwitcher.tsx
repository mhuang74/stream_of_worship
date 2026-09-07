"use client";

import { useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useLocale } from "@/hooks/useLocale";
import type { Locale } from "@/lib/i18n/messages";
import { LOCALES } from "@/lib/i18n/messages";

const LABEL_KEY: Record<Locale, "auth.language.en" | "auth.language.zhHant"> = {
  en: "auth.language.en",
  "zh-Hant": "auth.language.zhHant",
};

export function LanguageSwitcher() {
  const { locale, setLocale, t } = useLocale();
  const router = useRouter();

  const persist = useCallback((next: Locale) => {
    const secure = process.env.NODE_ENV === "production" ? "; secure" : "";
    document.cookie = `sow_locale=${next}; path=/; max-age=${60 * 60 * 24 * 365}; samesite=lax${secure}`;
  }, []);

  // Persist the choice in the sow_locale cookie, matching the shape the
  // settings PUT route sets (path=/, 365d, samesite=lax, secure in
  // production), so server-side resolveUserLocale() returns the chosen locale
  // on subsequent navigations (login <-> register soft links re-resolve the
  // initial locale from the server, and the cookie fills the no-auth gap).
  useEffect(() => {
    persist(locale);
  }, [locale, persist]);

  function switchTo(next: Locale) {
    setLocale(next);
    // Cookie synchronously before refresh: server components (public pages,
    // auth pages) resolve their locale from sow_locale via
    // resolveUserLocale(), so router.refresh() must see the new value. The
    // useEffect above only covers locale changes not initiated here.
    persist(next);
    router.refresh();
  }

  return (
    <nav aria-label={t("auth.language.ariaLabel")} className="flex items-center gap-1 text-xs">
      {LOCALES.map((l, i) => (
        <span key={l} className="flex items-center gap-1">
          {i > 0 && <span aria-hidden className="text-muted-foreground">|</span>}
          <button
            type="button"
            onClick={() => switchTo(l)}
            aria-current={locale === l}
            className={
              locale === l
                ? "font-medium underline underline-offset-4"
                : "text-muted-foreground hover:text-foreground underline-offset-4 hover:underline"
            }
          >
            {t(LABEL_KEY[l])}
          </button>
        </span>
      ))}
    </nav>
  );
}