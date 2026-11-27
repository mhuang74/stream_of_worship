// Why a stored Glue take no longer matches the Junction's current state.
// Client-safe: shared by the server resolver (`resolve.ts`) and by the two
// client components that explain a stale take to the user.

export type GlueStaleChange =
  | "from-recording"
  | "to-recording"
  | "to-arrangement"
  | "from-arrangement"
  | "incoming-tempo"
  | "beats";

export interface GlueStaleChangeDetail {
  change: GlueStaleChange;
  /** Stored take's beats; null unless `change === "beats"`. */
  storedBeats: number | null;
  /** Currently derived beats; null unless `change === "beats"`. */
  currentBeats: number | null;
}

interface ParsedSide {
  contentHash: string;
  steps: [number, number][] | null;
}

interface ParsedDescriptor {
  beats: number;
  toTempoBpm: number;
  from: ParsedSide;
  to: ParsedSide;
}

function parseSide(value: unknown): ParsedSide | null {
  if (typeof value !== "object" || value === null) return null;
  const record = value as Record<string, unknown>;
  if (typeof record.contentHash !== "string") return null;
  const rawSteps = record.steps;
  if (rawSteps === null) return { contentHash: record.contentHash, steps: null };
  if (!Array.isArray(rawSteps)) return null;
  const steps: [number, number][] = [];
  for (const step of rawSteps) {
    if (!Array.isArray(step) || step.length !== 2) return null;
    const [start, end] = step as unknown[];
    if (typeof start !== "number" || typeof end !== "number") return null;
    steps.push([start, end]);
  }
  return { contentHash: record.contentHash, steps };
}

function parseDescriptor(serialized: string): ParsedDescriptor | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(serialized);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const record = parsed as Record<string, unknown>;
  const from = parseSide(record.from);
  const to = parseSide(record.to);
  if (from === null || to === null) return null;
  if (typeof record.beats !== "number" || typeof record.toTempoBpm !== "number") return null;
  return { beats: record.beats, toTempoBpm: record.toTempoBpm, from, to };
}

/** Elementwise so key order and float formatting cannot look like a diff. */
function stepsDiffer(left: [number, number][] | null, right: [number, number][] | null): boolean {
  if (left === null || right === null) return left !== right;
  if (left.length !== right.length) return true;
  for (let index = 0; index < left.length; index += 1) {
    if (left[index][0] !== right[index][0] || left[index][1] !== right[index][1]) return true;
  }
  return false;
}

function beatsChange(storedBeats: number, currentBeats: number): GlueStaleChangeDetail {
  return { change: "beats", storedBeats, currentBeats };
}

function withoutBeats(change: GlueStaleChange): GlueStaleChangeDetail {
  return { change, storedBeats: null, currentBeats: null };
}

/**
 * Names the single most consequential difference between a stored take's
 * descriptor and the freshly derived one. Order: recordings, then
 * arrangements, then tempo, then beats.
 */
export function describeGlueDescriptorDrift(
  storedDescriptorJson: string,
  currentDescriptorJson: string
): GlueStaleChangeDetail | null {
  const stored = parseDescriptor(storedDescriptorJson);
  const current = parseDescriptor(currentDescriptorJson);
  if (stored === null || current === null) return null;

  if (stored.from.contentHash !== current.from.contentHash) {
    return withoutBeats("from-recording");
  }
  if (stored.to.contentHash !== current.to.contentHash) {
    return withoutBeats("to-recording");
  }
  if (stepsDiffer(stored.to.steps, current.to.steps)) return withoutBeats("to-arrangement");
  if (stepsDiffer(stored.from.steps, current.from.steps)) {
    return withoutBeats("from-arrangement");
  }
  if (stored.toTempoBpm !== current.toTempoBpm) return withoutBeats("incoming-tempo");
  if (stored.beats !== current.beats) return beatsChange(stored.beats, current.beats);
  return null;
}
