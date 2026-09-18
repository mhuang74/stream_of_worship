import type { Locale } from "@/lib/i18n/messages";

/**
 * Base URL of the marketing site, locale-aware: zh-Hant users land on the
 * /zh-Hant tree. Overridable via NEXT_PUBLIC_MARKETING_URL (issue #213).
 */
export function getMarketingUrl(locale: Locale): string {
  const base = process.env.NEXT_PUBLIC_MARKETING_URL ?? "https://streamofworship.com";
  return locale === "zh-Hant" ? `${base}/zh-Hant` : base;
}
