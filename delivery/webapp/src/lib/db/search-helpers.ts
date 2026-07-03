import { sql, type SQL } from "drizzle-orm";
import { PITCH_CLASSES, BPM_BAND_KEYS, type BpmBandKey } from "@/lib/constants";

export function buildKeyRegex(keys: string[]): string {
  const escaped = keys.map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return `^(${escaped.join("|")})(maj|major|minor|min)?(?!\\w)`;
}

export function buildBpmPredicate(bpmRange: BpmBandKey, alias: string = "r"): SQL {
  const col = sql.raw(`${alias}.tempo_bpm`);
  switch (bpmRange) {
    case "slow":
      return sql`${col} < 90`;
    case "moderate":
      return sql`${col} >= 90 AND ${col} < 120`;
    case "fast":
      return sql`${col} >= 120`;
  }
}

export function buildVisibilityCondition(
  visibilityStatus: string | string[] | undefined,
  alias: string
): SQL | undefined {
  if (!visibilityStatus || visibilityStatus === "all") return undefined;
  const col = sql.raw(`${alias}.visibility_status`);
  if (Array.isArray(visibilityStatus)) {
    if (visibilityStatus.length === 0) return undefined;
    return sql`${col} = ANY(${sql`ARRAY[${sql.join(visibilityStatus.map(s => sql`${s}`), sql`, `)}]::text[]`})`;
  }
  return sql`${col} = ${visibilityStatus}`;
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
