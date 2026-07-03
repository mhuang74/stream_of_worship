export interface StructuredSearchCriteria {
  query?: string;
  keys?: string[];
  bpmRange?: "slow" | "moderate" | "fast";
}

export const BPM_BANDS = {
  slow: { label: "Slow", max: 90 },
  moderate: { label: "Moderate", min: 90, max: 120 },
  fast: { label: "Fast", min: 120 },
} as const;

export const BPM_BAND_KEYS = ["slow", "moderate", "fast"] as const;
export type BpmBandKey = (typeof BPM_BAND_KEYS)[number];
