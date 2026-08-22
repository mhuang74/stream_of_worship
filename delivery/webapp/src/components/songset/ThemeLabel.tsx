"use client";

import { Badge } from "@/components/ui/badge";
import {
  SONG_THEMES,
  THEME_PHASE_COLORS,
  THEME_TO_PHASE,
  type SongTheme,
} from "@/lib/constants";
import { useLocale } from "@/hooks/useLocale";
import type { TranslationKey } from "@/lib/i18n/messages";

/** Narrow a DB string to a known theme, or null if it's not in the enum. */
export function toSongTheme(value: string): SongTheme | null {
  return SONG_THEMES.includes(value as SongTheme) ? (value as SongTheme) : null;
}

/** A color-coded badge for a single worship theme. */
export function ThemeLabel({ theme }: { theme: SongTheme }) {
  const { t } = useLocale();
  const safeTheme = toSongTheme(theme);
  if (!safeTheme) return null;
  const phase = THEME_TO_PHASE[safeTheme];
  const colors = THEME_PHASE_COLORS[phase];

  return (
    <Badge
      variant="outline"
      className="text-xs px-1.5 py-0"
      style={{ backgroundColor: colors.bg, color: colors.text, borderColor: "transparent" }}
      data-testid="theme-label"
    >
      {t(`theme.${safeTheme}` as TranslationKey)}
    </Badge>
  );
}

/** Compact "first → last" arc pill showing the worship arc span of a songset.
 *
 * - Empty array → render nothing.
 * - Single theme → render a single ThemeLabel.
 * - Two+ themes → render "first → last" using translated labels.
 */
export function ThemeArcSpan({ themes }: { themes: SongTheme[] }) {
  const { t } = useLocale();

  const safeThemes = themes
    .map(toSongTheme)
    .filter((t): t is SongTheme => t !== null);

  if (safeThemes.length === 0) return null;
  if (safeThemes.length === 1) return <ThemeLabel theme={safeThemes[0]} />;

  const first = safeThemes[0];
  const last = safeThemes[safeThemes.length - 1];
  const firstColors = THEME_PHASE_COLORS[THEME_TO_PHASE[first]];
  const lastColors = THEME_PHASE_COLORS[THEME_TO_PHASE[last]];

  return (
    <span className="inline-flex items-center gap-1" data-testid="theme-arc-span">
      <Badge
        variant="outline"
        className="text-xs px-1.5 py-0"
        style={{ backgroundColor: firstColors.bg, color: firstColors.text, borderColor: "transparent" }}
      >
        {t(`theme.${first}` as TranslationKey)}
      </Badge>
      <span className="text-muted-foreground">→</span>
      <Badge
        variant="outline"
        className="text-xs px-1.5 py-0"
        style={{ backgroundColor: lastColors.bg, color: lastColors.text, borderColor: "transparent" }}
      >
        {t(`theme.${last}` as TranslationKey)}
      </Badge>
    </span>
  );
}
