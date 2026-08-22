export const SONGSET_MAX_SONGS = 5;
export const SONGSET_MAX_DURATION_SECONDS = 1500;

export const FONT_FAMILIES = [
  {
    value: "lxgw_wenkai_tc",
    label: "Traditional",
    cssFamily: "LXGW WenKai TC",
    cssVariable: "--font-lxgw-wenkai-tc",
  },
  {
    value: "chiron_goround_tc",
    label: "Elegant",
    cssFamily: "Chiron GoRound TC",
    cssVariable: "--font-chiron-goround-tc",
  },
  {
    value: "chocolate_classical_sans",
    label: "Modern",
    cssFamily: "Chocolate Classical Sans",
    cssVariable: "--font-chocolate-classical-sans",
  },
  {
    value: "noto_serif_tc",
    label: "Classic",
    cssFamily: "Noto Serif TC",
    cssVariable: "--font-noto-serif-tc",
  },
] as const;

export const VALID_FONT_FAMILIES = FONT_FAMILIES.map((font) => font.value);
export type FontFamilyValue = (typeof FONT_FAMILIES)[number]["value"];

export const TEMPLATES = [
  { value: "dark", label: "Dark" },
  { value: "gradient_warm", label: "Gradient Warm" },
  { value: "gradient_blue", label: "Gradient Blue" },
] as const;

export const RESOLUTIONS = [
  { value: "720p", label: "720p (HD)" },
  { value: "1080p", label: "1080p (Full HD)" },
] as const;

export const FONT_SIZES = [
  { value: "S", label: "Small (32px)", px: 32 },
  { value: "M", label: "Medium (48px)", px: 48 },
  { value: "L", label: "Large (64px)", px: 64 },
  { value: "XL", label: "Extra Large (80px)", px: 80 },
] as const;

export function normalizeFontFamily(value: unknown): FontFamilyValue {
  return VALID_FONT_FAMILIES.includes(value as FontFamilyValue)
    ? (value as FontFamilyValue)
    : "noto_serif_tc";
}

/** Completion gate (ADR-0002): fraction of a full-song play that marks a
 * song Completed client-side. Heard ≥90% unlocks favoriting. */
export const COMPLETION_THRESHOLD = 0.9;

export const PITCH_CLASSES = [
  "C",
  "C#",
  "D",
  "D#",
  "E",
  "F",
  "F#",
  "G",
  "G#",
  "A",
  "A#",
  "B",
] as const;
export type PitchClass = (typeof PITCH_CLASSES)[number];

export const BPM_BANDS = {
  slow: { label: "Slow", max: 70 },
  moderate: { label: "Moderate", min: 70, max: 80 },
  upbeat: { label: "Upbeat", min: 80, max: 90 },
  fast: { label: "Fast", min: 90 },
} as const;

export const BPM_BAND_KEYS = ["slow", "moderate", "upbeat", "fast"] as const;
export type BpmBandKey = (typeof BPM_BAND_KEYS)[number];

export function formatBpmBandRangeText(band: BpmBandKey): string {
  const config = BPM_BANDS[band];
  if ("max" in config && !("min" in config)) {
    return `< ${config.max}`;
  }
  if ("min" in config && "max" in config) {
    return `${config.min}–${config.max}`;
  }
  return `≥ ${config.min}`;
}

// ---------------------------------------------------------------------------
// Song themes — 12-value vocabulary mirroring admin CLI SONG_COMPONENT_THEMES.
// Each theme maps to a Worship Arc phase (1–5); THEME_PHASE_COLORS provides
// one {bg, text} hex pair per phase for WCAG AA contrast on badges.
// ---------------------------------------------------------------------------

export const SONG_THEMES = [
  "讚美",
  "感恩",
  "敬拜",
  "奉獻",
  "認罪",
  "差遣",
  "信心",
  "祈禱",
  "復興",
  "聖靈",
  "十字架",
  "跟隨",
] as const;

export type SongTheme = (typeof SONG_THEMES)[number];

export const THEME_TO_PHASE: Record<SongTheme, 1 | 2 | 3 | 4 | 5> = {
  讚美: 1,
  感恩: 2,
  敬拜: 3,
  祈禱: 3,
  信心: 3,
  聖靈: 3,
  奉獻: 4,
  認罪: 4,
  十字架: 4,
  差遣: 5,
  跟隨: 5,
  復興: 5,
};

export const THEME_PHASE_COLORS: Record<1 | 2 | 3 | 4 | 5, { bg: string; text: string }> = {
  1: { bg: "#fef3c7", text: "#92400e" }, // amber — Call/Praise
  2: { bg: "#dcfce7", text: "#166534" }, // green — Thanksgiving
  3: { bg: "#dbeafe", text: "#1e40af" }, // blue — Worship
  4: { bg: "#fce7f3", text: "#9d174d" }, // rose — Response
  5: { bg: "#e0e7ff", text: "#3730a3" }, // violet — Commission
};
