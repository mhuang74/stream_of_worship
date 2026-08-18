// i18n message infrastructure (issue #143).
//
// Two locales exactly: `en` and `zh-Hant` (Traditional Chinese). UI chrome
// only — the song catalog, lyrics, and rendered output remain verbatim.
//
// The dictionary is assembled from namespace bundles (`MessageBundle`), each
// of which structurally guarantees that both locales define the SAME set of
// keys (a missing zh-Hant key is a compile error, not a runtime blank). All
// bundles are merged into a single typed record `messages`, and `t()` is the
// pure lookup used by the client-side LocaleProvider.

export const LOCALES = ["en", "zh-Hant"] as const;
export type Locale = (typeof LOCALES)[number];

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

/** A name-spaced message set with identical keys in both locales. */
export interface MessageBundle<K extends string> {
  en: Record<K, string>;
  "zh-Hant": Record<K, string>;
}

/**
 * Declare a bundle. Both locale maps must share the exact same keys — the
 * `K` type parameter is inferred from `en` and `zh-Hant` is required to be
 * `Record<K, string>` so any missing or extra key is a compile error.
 */
export function bundle<const K extends string>(parts: {
  en: Record<K, string>;
  "zh-Hant": Record<K, string>;
}): MessageBundle<K> {
  return parts;
}

type BundleKeys<B> = B extends readonly (infer U)[]
  ? U extends MessageBundle<infer K>
    ? K
    : never
  : never;

/** Merge namespace bundles into a single typed dictionary. */
export function mergeMessages<const B extends readonly MessageBundle<string>[]>(
  ...bundles: B
): MessageBundle<BundleKeys<B>> {
  type K = BundleKeys<B>;
  const seen = new Set<string>();
  for (const bundle of bundles) {
    for (const key of Object.keys(bundle.en)) {
      if (seen.has(key)) {
        throw new Error(`i18n: duplicate message key "${key}" across bundles`);
      }
      seen.add(key);
    }
  }
  const en = Object.assign({}, ...bundles.map((b) => b.en)) as Record<K, string>;
  const zhHant = Object.assign({}, ...bundles.map((b) => b["zh-Hant"])) as Record<K, string>;
  return { en, "zh-Hant": zhHant };
}

// Merged dictionary (bundle imports added as namespaces are introduced).
import { core } from "./messages/core";
import { songsetsBundle } from "./messages/songsets";
import { browseBundle } from "./messages/browse";
import { favoritesBundle } from "./messages/favorites";
import { renderBundle } from "./messages/render";
import { playBundle } from "./messages/play";
import { audioBundle } from "./messages/audio";
import { controlBundle } from "./messages/control";

export const messages = mergeMessages(
  core,
  songsetsBundle,
  browseBundle,
  favoritesBundle,
  renderBundle,
  playBundle,
  audioBundle,
  controlBundle
);

export type TranslationKey = keyof typeof messages.en;

/** Pure lookup — used by the client-side provider (and other non-React code). */
export function t(locale: Locale, key: TranslationKey): string {
  return messages[locale][key];
}

/** Build translation keys for dynamic option values (settings/render forms). */
export const optionKey = {
  template: (v: string) => `settings.option.template.${v}` as TranslationKey,
  resolution: (v: string) => `settings.option.resolution.${v}` as TranslationKey,
  fontFamily: (v: string) => `settings.option.fontFamily.${v}` as TranslationKey,
  fontPreset: (v: string) => `settings.option.fontPreset.${v}` as TranslationKey,
  titleCardDuration: (v: number) => `render.titleCard.duration.${v}` as TranslationKey,
};
