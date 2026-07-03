export type ParsedMusicalKey = {
  raw: string;
  status: "ok" | "range" | "missing" | "unparseable";
  display: string;
  root: string | null;
  mode: "major" | "minor" | "unknown";
  startRoot: string | null;
  endRoot: string | null;
  pitchClass: number | null;
  startPitchClass: number | null;
  endPitchClass: number | null;
};

const PITCH_CLASS: Record<string, number> = {
  C: 0,
  "B#": 0,
  "C#": 1,
  DB: 1,
  D: 2,
  "D#": 3,
  EB: 3,
  E: 4,
  FB: 4,
  "E#": 5,
  F: 5,
  "F#": 6,
  GB: 6,
  G: 7,
  "G#": 8,
  AB: 8,
  A: 9,
  "A#": 10,
  BB: 10,
  B: 11,
  CB: 11,
};

const TOKEN_RE = /^\s*([A-Ga-g])([#♯b♭]?)(?:\s*(m|minor|major|小調|大調))?\s*$/i;
const RANGE_RE = /\s*(?:-|→|~)\s*/;

function missing(raw = ""): ParsedMusicalKey {
  return {
    raw,
    status: "missing",
    display: "",
    root: null,
    mode: "unknown",
    startRoot: null,
    endRoot: null,
    pitchClass: null,
    startPitchClass: null,
    endPitchClass: null,
  };
}

function unparseable(raw: string): ParsedMusicalKey {
  return {
    ...missing(raw),
    status: "unparseable",
    display: raw,
  };
}

function parseToken(token: string): { root: string; mode: "major" | "minor"; pitchClass: number } | null {
  const match = token.match(TOKEN_RE);
  if (!match) return null;
  const accidental = (match[2] ?? "").replace("♯", "#").replace("♭", "b");
  const root = `${match[1].toUpperCase()}${accidental}`;
  const pitchClass = PITCH_CLASS[root.toUpperCase()];
  if (pitchClass == null) return null;
  const modeToken = (match[3] ?? "").toLowerCase();
  const mode = modeToken === "m" || modeToken === "minor" || modeToken === "小調"
    ? "minor"
    : "major";
  return { root, mode, pitchClass };
}

export function parseMusicalKey(value: string | null | undefined): ParsedMusicalKey {
  const raw = (value ?? "").normalize("NFKC").trim();
  if (!raw) return missing("");

  const tokens = raw.split(RANGE_RE).filter((token) => token.trim().length > 0);
  if (tokens.length === 0) return missing(raw);

  const parsed = tokens.map(parseToken);
  if (parsed.some((item) => item == null)) return unparseable(raw);

  const first = parsed[0]!;
  const last = parsed[parsed.length - 1]!;
  const status = parsed.length > 1 ? "range" : "ok";
  const display = status === "ok" ? first.root : `${first.root} → ${last.root}`;
  return {
    raw,
    status,
    display,
    root: first.root,
    mode: first.mode,
    startRoot: first.root,
    endRoot: last.root,
    pitchClass: first.pitchClass,
    startPitchClass: first.pitchClass,
    endPitchClass: last.pitchClass,
  };
}

export function pitchClass(value: string | null | undefined): number | null {
  return parseMusicalKey(value).pitchClass;
}

