export const SONGSET_MAX_SONGS = 5;
export const SONGSET_MAX_DURATION_SECONDS = 1500;

export const FONT_FAMILIES = [
  {
    value: "lxgw_wenkai_tc",
    label: "Traditional - LXGW WenKai TC",
    cssFamily: "LXGW WenKai TC",
    cssVariable: "--font-lxgw-wenkai-tc",
  },
  {
    value: "chocolate_classical_sans",
    label: "Elegant - Chocolate Classical Sans",
    cssFamily: "Chocolate Classical Sans",
    cssVariable: "--font-chocolate-classical-sans",
  },
  {
    value: "chiron_goround_tc",
    label: "Modern - Chiron GoRound TC",
    cssFamily: "Chiron GoRound TC",
    cssVariable: "--font-chiron-goround-tc",
  },
  {
    value: "noto_serif_tc",
    label: "Classic - Noto Serif TC",
    cssFamily: "Noto Serif TC",
    cssVariable: "--font-noto-serif-tc",
  },
] as const;

export const VALID_FONT_FAMILIES = FONT_FAMILIES.map((font) => font.value);
export type FontFamilyValue = (typeof FONT_FAMILIES)[number]["value"];
