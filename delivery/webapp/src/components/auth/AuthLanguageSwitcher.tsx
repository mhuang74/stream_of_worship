"use client";

import { useEffect } from "react";
import { useLocale } from "@/hooks/useLocale";
import type { Locale } from "@/lib/i18n/messages";
import { LOCALES } from "@/lib/i18n/messages";

const LABEL_KEY: Record<Locale, "auth.language.en" | "auth.language.zhHant"> = {
  en: "auth.language.en",
  "zh-Hant": "auth.language.zhHant",
};

export function AuthLanguageSwitcher() {
  const { locale, setLocale, t } = useLocale();

  // Persist the choice in the sow_locale cookie, matching the shape the
  // settings PUT route sets (path=/, 365d, samesite=lax, secure in
  // production), so server-side resolveUserLocale() returns the chosen locale
  // on subsequent navigations (login <-> register soft links re-resolve the
  // initial locale from the server, and the cookie fills the no-auth gap).
  useEffect(() => {
    const secure = process.env.NODE_ENV === "production" ? "; secure" : "";
    document.cookie = `sow_locale=${locale}; path=/; max-age=${60 * 60 * 24 * 365}; samesite=lax${secure}`;
  }, [locale]);

  return (
    <nav aria-label={t("auth.language.ariaLabel")} className="flex items-center gap-1 text-sm">
      {LOCALES.map((l, i) => (
        <span key={l} className="flex items-center gap-1">
          {i > 0 && <span aria-hidden className="text-muted-foreground">|</span>}
          <button
            type="button"
            onClick={() => setLocale(l)}
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
