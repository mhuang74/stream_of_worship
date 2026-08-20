import type { Locale } from "./messages";

/**
 * Parse the Accept-Language header and return the best matching locale.
 * - Any `zh*` tag → "zh-Hant"
 * - "en" or no match → "en"
 * - Ignores quality values; first match wins.
 *
 * Framework-agnostic and pure so it can be imported by the request-time
 * proxy (src/proxy.ts) and the Server Component resolver
 * (src/lib/i18n/server.ts) without either depending on the other.
 */
export function parseAcceptLanguage(headerValue: string | null): Locale {
  if (!headerValue) return "en";
  const tags = headerValue
    .split(",")
    .map((s) => s.split(";")[0].trim().toLowerCase());
  for (const tag of tags) {
    if (tag.startsWith("zh")) return "zh-Hant";
    if (tag === "en") return "en";
  }
  return "en";
}
