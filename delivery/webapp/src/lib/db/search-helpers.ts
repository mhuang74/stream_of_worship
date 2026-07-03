import { sql, type SQL } from "drizzle-orm";
import { PITCH_CLASSES, BPM_BAND_KEYS, type BpmBandKey } from "@/lib/constants";

export function buildKeyRegex(keys: string[]): string {
  const escaped = keys.map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return `^(${escaped.join("|")})(maj|major|minor|min)?\\b`;
}

export function buildBpmPredicate(bpmRange: BpmBandKey): SQL {
  switch (bpmRange) {
    case "slow":
      return sql`r.tempo_bpm < 90`;
    case "moderate":
      return sql`r.tempo_bpm >= 90 AND r.tempo_bpm < 120`;
    case "fast":
      return sql`r.tempo_bpm >= 120`;
  }
}

export function isValidPitchClass(value: string): boolean {
  return (PITCH_CLASSES as readonly string[]).includes(value);
}

export function isValidBpmBand(value: string): value is BpmBandKey {
  return (BPM_BAND_KEYS as readonly string[]).includes(value);
}

export function parseKeysParam(keysParam: string | null): string[] | undefined {
  if (!keysParam) return undefined;
  const parts = keysParam
    .split(",")
    .map((k) => k.trim())
    .filter(Boolean)
    .filter((k) => isValidPitchClass(k));
  if (parts.length === 0) return undefined;
  return Array.from(new Set(parts)).slice(0, PITCH_CLASSES.length);
}

export function parseBpmRangeParam(
  bpmRangeParam: string | null
): BpmBandKey | undefined {
  if (!bpmRangeParam) return undefined;
  if (!isValidBpmBand(bpmRangeParam)) return undefined;
  return bpmRangeParam;
}
