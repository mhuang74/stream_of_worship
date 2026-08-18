// i18n-aware duration formatting (issue #143 review fixes).
//
// Distinct from `lib/format.ts` `formatDuration(seconds)` (non-i18n, `m:ss`
// style). This helper composes a "total duration" label from translation keys
// so the unit words follow the active locale.

import type { TranslationKey } from "./messages";

export function formatTotalDuration(
  t: (key: TranslationKey) => string,
  totalSeconds: number | null
): string {
  if (!totalSeconds) return t("control.notApplicable");
  const totalMinutes = Math.round(totalSeconds / 60);
  if (totalMinutes < 60) return `${totalMinutes} ${t("control.min")}`;
  const hours = Math.floor(totalMinutes / 60);
  const mins = totalMinutes % 60;
  return `${hours}${t("control.hours")} ${String(mins).padStart(2, "0")}${t("control.mins")}`;
}
