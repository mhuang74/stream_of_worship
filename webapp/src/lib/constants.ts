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

export function normalizeFontFamily(value: unknown): FontFamilyValue {
  return VALID_FONT_FAMILIES.includes(value as FontFamilyValue)
    ? (value as FontFamilyValue)
    : "noto_serif_tc";
}
