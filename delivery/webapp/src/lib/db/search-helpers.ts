import { sql, type SQL } from "drizzle-orm";
import { PITCH_CLASSES, BPM_BAND_KEYS, type BpmBandKey } from "@/lib/constants";
import { parseMusicalKey } from "@/lib/music/key";
import {
  normalizeAlbumFilters,
  type AlbumFilter,
} from "@/lib/search/album-filter";

const ENHARMONIC_ROOTS_BY_PITCH_CLASS: Record<number, string[]> = {
  0: ["C", "B#", "B♯"],
  1: ["C#", "C♯", "Db", "D♭"],
  2: ["D"],
  3: ["D#", "D♯", "Eb", "E♭"],
  4: ["E", "Fb", "F♭"],
  5: ["F", "E#", "E♯"],
  6: ["F#", "F♯", "Gb", "G♭"],
  7: ["G"],
  8: ["G#", "G♯", "Ab", "A♭"],
  9: ["A"],
  10: ["A#", "A♯", "Bb", "B♭"],
  11: ["B", "Cb", "C♭"],
};

export function buildKeyRegex(keys: string[]): string {
  const escaped = keys.map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return `^(${escaped.join("|")})(maj|major|minor|min)?(?!\\w)`;
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function selectedPitchClasses(keys: string[]): number[] {
  const pitchClasses = keys
    .map((key) => parseMusicalKey(key).pitchClass)
    .filter((pitchClass): pitchClass is number => pitchClass != null);
  return Array.from(new Set(pitchClasses));
}

function buildRootTokenRegexForPitchClasses(pitchClasses: number[]): string {
  const roots = pitchClasses.flatMap(
    (pitchClass) => ENHARMONIC_ROOTS_BY_PITCH_CLASS[pitchClass] ?? []
  );
  const escapedRoots = Array.from(new Set(roots)).map(escapeRegex);
  return `(?:${escapedRoots.join("|")})`;
}

export function buildKeyTokenRegex(keys: string[]): string {
  const pitchClasses = selectedPitchClasses(keys);
  if (pitchClasses.length === 0) return "(?!)";
  const rootToken = buildRootTokenRegexForPitchClasses(pitchClasses);
  return String.raw`^\s*${rootToken}(?:\s*(?:maj|major|minor|min|m|小調|大調))?\s*(?:$|-|→|~)`;
}

export function buildCatalogKeyTokenRegex(keys: string[]): string {
  const pitchClasses = selectedPitchClasses(keys);
  if (pitchClasses.length === 0) return "(?!)";
  const rootToken = buildRootTokenRegexForPitchClasses(pitchClasses);
  return String.raw`(?:^|\s*(?:-|→|~)\s*)${rootToken}(?:\s*(?:maj|major|minor|min|m|小調|大調))?\s*(?:$|-|→|~)`;
}

export function displayedKeyPitchClasses(value: string | null | undefined): number[] | null {
  const parsed = parseMusicalKey(value);
  if (parsed.status === "missing") return [];
  if (parsed.status === "unparseable") return null;
  const pitchClasses = [parsed.startPitchClass, parsed.endPitchClass].filter(
    (pitchClass): pitchClass is number => pitchClass != null
  );
  return Array.from(new Set(pitchClasses));
}

export function effectiveKeyMatchesFilter(input: {
  catalogKey?: string | null;
  recordingKey?: string | null;
  keys: string[];
}): boolean {
  const selected = selectedPitchClasses(input.keys);
  if (selected.length === 0) return false;

  const catalogPitchClasses = displayedKeyPitchClasses(input.catalogKey);
  if (catalogPitchClasses === null) return false;
  if (catalogPitchClasses.length > 0) {
    return catalogPitchClasses.some((pitchClass) => selected.includes(pitchClass));
  }

  const recordingPitchClasses = displayedKeyPitchClasses(input.recordingKey);
  if (!recordingPitchClasses) return false;
  return recordingPitchClasses.some((pitchClass) => selected.includes(pitchClass));
}

export function buildRecordingKeyPredicate(keys: string[], recordingAlias: string): SQL {
  const col = sql.raw(`${recordingAlias}.musical_key`);
  return sql`${col} ~* ${buildKeyTokenRegex(keys)}`;
}

export function buildEffectiveKeyPredicate(
  keys: string[],
  songAlias: string,
  recordingAlias: string
): SQL {
  const pitchClasses = selectedPitchClasses(keys);
  if (pitchClasses.length === 0) return sql`false`;

  const catalogKey = sql.raw(`${songAlias}.musical_key`);
  const catalogStartPitchClass = sql.raw(`${songAlias}.musical_key_start_pitch_class`);
  const catalogEndPitchClass = sql.raw(`${songAlias}.musical_key_end_pitch_class`);
  const catalogPresent = sql`${catalogKey} IS NOT NULL AND btrim(${catalogKey}) <> ''`;
  const catalogPitchClassArray = sql`ARRAY[${sql.join(
    pitchClasses.map((pitchClass) => sql`${pitchClass}`),
    sql`, `
  )}]::int[]`;
  const catalogPitchClassMatch = sql`(
    ${catalogStartPitchClass} = ANY(${catalogPitchClassArray})
    OR ${catalogEndPitchClass} = ANY(${catalogPitchClassArray})
    OR ${catalogKey} ~* ${buildCatalogKeyTokenRegex(keys)}
  )`;

  return sql`(
    (${catalogPresent} AND ${catalogPitchClassMatch})
    OR (NOT (${catalogPresent}) AND ${buildRecordingKeyPredicate(keys, recordingAlias)})
  )`;
}

export function buildBpmPredicate(bpmRange: BpmBandKey, alias: string = "r"): SQL {
  const col = sql.raw(`${alias}.tempo_bpm`);
  switch (bpmRange) {
    case "slow":
      return sql`${col} < 70`;
    case "moderate":
      return sql`${col} >= 70 AND ${col} < 80`;
    case "upbeat":
      return sql`${col} >= 80 AND ${col} < 90`;
    case "fast":
      return sql`${col} >= 90`;
  }
}

export function buildBpmPredicates(bpmRanges: BpmBandKey[], alias: string = "r"): SQL | undefined {
  if (!bpmRanges || bpmRanges.length === 0) return undefined;
  const predicates = bpmRanges.map((band) => buildBpmPredicate(band, alias));
  if (predicates.length === 1) return predicates[0];
  return sql`(${sql.join(predicates, sql` OR `)})`;
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

export function parseBpmRangeParams(
  bpmRangeParams: string[]
): BpmBandKey[] | undefined {
  const bands = bpmRangeParams
    .flatMap((value) => value.split(","))
    .map((value) => value.trim())
    .filter((value): value is BpmBandKey => isValidBpmBand(value));
  if (bands.length === 0) return undefined;
  return Array.from(new Set(bands));
}

export function parseAlbumValues(values: string[]): string[] | undefined {
  const albums = values
    .flatMap((value) => value.split(","))
    .map((value) => value.trim())
    .filter(Boolean);

  if (albums.length === 0) return undefined;
  return Array.from(new Set(albums)).slice(0, 25);
}

export function parseAlbumNameParams(searchParams: URLSearchParams): string[] | undefined {
  return parseAlbumValues(searchParams.getAll("albumName"));
}

export function parseAlbumFilterValues(
  values: Array<string | AlbumFilter>
): AlbumFilter[] | undefined {
  const parsed = values.flatMap((value) => {
    if (typeof value === "string") {
      return value
        .split(",")
        .map((albumName) => ({ albumName, albumSeries: null }));
    }

    return [{
      albumName: value.albumName,
      albumSeries: value.albumSeries,
    }];
  });

  return normalizeAlbumFilters(parsed);
}

export function parseAlbumFilterParams(searchParams: URLSearchParams): {
  albumFilters?: AlbumFilter[];
  albumNames?: string[];
} {
  const albumNames = searchParams.getAll("albumName");
  const albumSeries = searchParams.getAll("albumSeries");

  if (albumSeries.length === 0) {
    return { albumNames: parseAlbumValues(albumNames) };
  }

  const albumFilters = albumNames.map((albumName, index) => ({
    albumName,
    albumSeries: albumSeries[index]?.trim() || null,
  }));

  return { albumFilters: normalizeAlbumFilters(albumFilters) };
}
