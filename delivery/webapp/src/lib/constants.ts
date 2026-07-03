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
  slow: { label: "Slow", max: 90 },
  moderate: { label: "Moderate", min: 90, max: 120 },
  fast: { label: "Fast", min: 120 },
} as const;

export const BPM_BAND_KEYS = ["slow", "moderate", "fast"] as const;
export type BpmBandKey = (typeof BPM_BAND_KEYS)[number];
