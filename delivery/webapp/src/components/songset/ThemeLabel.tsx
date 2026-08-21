"use client";

import { Badge } from "@/components/ui/badge";
import { THEME_PHASE_COLORS, THEME_TO_PHASE, type SongTheme } from "@/lib/constants";
import { useLocale } from "@/hooks/useLocale";
import type { TranslationKey } from "@/lib/i18n/messages";

/** A color-coded badge for a single worship theme. */
export function ThemeLabel({ theme }: { theme: string }) {
  const { t } = useLocale();
  const phase = THEME_TO_PHASE[theme as SongTheme];
  const colors = phase ? THEME_PHASE_COLORS[phase] : null;

  return (
    <Badge
      variant="outline"
      className="text-xs px-1.5 py-0"
      style={colors ? { backgroundColor: colors.bg, color: colors.text, borderColor: "transparent" } : undefined}
      data-testid="theme-label"
    >
      {t(`theme.${theme}` as TranslationKey)}
    </Badge>
  );
}

/** Compact "first → last" arc pill showing the worship arc span of a songset.
 *
 * - Empty array → render nothing.
 * - Single theme → render a single ThemeLabel.
 * - Two+ themes → render "first → last" using translated labels.
 */
export function ThemeArcSpan({ themes }: { themes: string[] }) {
  const { t } = useLocale();

  if (themes.length === 0) return null;
  if (themes.length === 1) return <ThemeLabel theme={themes[0]} />;

  const first = themes[0];
  const last = themes[themes.length - 1];
  const firstPhase = THEME_TO_PHASE[first as SongTheme];
  const lastPhase = THEME_TO_PHASE[last as SongTheme];
  const firstColors = firstPhase ? THEME_PHASE_COLORS[firstPhase] : null;
  const lastColors = lastPhase ? THEME_PHASE_COLORS[lastPhase] : null;

  return (
    <span
      className="inline-flex items-center gap-1 rounded-4xl text-xs font-medium"
      data-testid="theme-arc-span"
    >
      <span
        className="inline-flex items-center rounded-4xl px-1.5 py-0.5"
        style={firstColors ? { backgroundColor: firstColors.bg, color: firstColors.text } : undefined}
      >
        {t(`theme.${first}` as TranslationKey)}
      </span>
      <span className="text-muted-foreground">→</span>
      <span
        className="inline-flex items-center rounded-4xl px-1.5 py-0.5"
        style={lastColors ? { backgroundColor: lastColors.bg, color: lastColors.text } : undefined}
      >
        {t(`theme.${last}` as TranslationKey)}
      </span>
    </span>
  );
}