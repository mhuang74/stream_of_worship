"use client";

import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { Locale, t, TranslationKey } from "@/lib/i18n/messages";

interface LocaleContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: TranslationKey) => string;
}

const LocaleContext = createContext<LocaleContextValue | null>(null);

/**
 * Client-side display-language provider (ADR 0004). Holds the current `en` /
 * `zh-Hant` choice (seeded from the server-rendered initial locale), keeps the
 * document `lang` attribute in sync, and exposes the typed `t()` hook.
 */
export function LocaleProvider({
  initialLocale = "en",
  children,
}: {
  initialLocale?: Locale;
  children: ReactNode;
}) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);
  const [prevInitialLocale, setPrevInitialLocale] = useState<Locale>(initialLocale);

  if (prevInitialLocale !== initialLocale) {
    setPrevInitialLocale(initialLocale);
    setLocaleState(initialLocale);
  }

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
  }, []);

  const translate = useCallback((key: TranslationKey) => t(locale, key), [locale]);

  return (
    <LocaleContext.Provider value={{ locale, setLocale, t: translate }}>
      {children}
    </LocaleContext.Provider>
  );
}

export function useLocaleContext(): LocaleContextValue {
  const ctx = useContext(LocaleContext);
  if (!ctx) {
    throw new Error("useLocale must be used within a LocaleProvider");
  }
  return ctx;
}
