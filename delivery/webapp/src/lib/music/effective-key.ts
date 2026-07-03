import { parseMusicalKey, ParsedMusicalKey } from "./key";

export type EffectiveKeyInput = {
  catalogKey?: string | null;
  catalogParsed?: ParsedMusicalKey | null;
  detectedKey?: string | null;
  detectedMode?: string | null;
  detectedConfidence?: number | null;
  detectedMargin?: number | null;
  detectedWindowAgreement?: number | null;
};

export type EffectiveKey = {
  display: string | null;
  source: "catalog" | "audio" | "audio_legacy" | "unknown";
  startRoot: string | null;
  endRoot: string | null;
  mode: "major" | "minor" | "unknown";
  startPitchClass: number | null;
  endPitchClass: number | null;
  confidence: number | null;
  warning: "none" | "audio_low_confidence" | "catalog_audio_disagree" | "unparseable_catalog";
};

const MIN_CONFIDENCE = 0.70;
const MIN_MARGIN = 0.05;
const MIN_WINDOW_AGREEMENT = 0.55;

function unknown(warning: EffectiveKey["warning"] = "none"): EffectiveKey {
  return {
    display: null,
    source: "unknown",
    startRoot: null,
    endRoot: null,
    mode: "unknown",
    startPitchClass: null,
    endPitchClass: null,
    confidence: null,
    warning,
  };
}

function parsedToEffective(
  parsed: ParsedMusicalKey,
  source: EffectiveKey["source"],
  confidence: number | null,
  warning: EffectiveKey["warning"] = "none"
): EffectiveKey {
  return {
    display: parsed.display || parsed.raw || null,
    source,
    startRoot: parsed.startRoot,
    endRoot: parsed.endRoot,
    mode: parsed.mode,
    startPitchClass: parsed.startPitchClass,
    endPitchClass: parsed.endPitchClass,
    confidence,
    warning,
  };
}

function normalizeMode(mode: string | null | undefined): EffectiveKey["mode"] {
  const normalized = (mode ?? "").normalize("NFKC").trim().toLowerCase();
  if (normalized === "minor" || normalized === "m" || normalized === "小調") {
    return "minor";
  }
  if (normalized === "major" || normalized === "大調") {
    return "major";
  }
  return "unknown";
}

function detectedToEffective(
  parsed: ParsedMusicalKey,
  source: EffectiveKey["source"],
  input: EffectiveKeyInput
): EffectiveKey {
  const detectedMode = normalizeMode(input.detectedMode);
  if (detectedMode === "unknown" || detectedMode === parsed.mode) {
    return parsedToEffective(parsed, source, input.detectedConfidence ?? null);
  }
  const suffix = detectedMode === "minor" ? "m" : "";
  return {
    ...parsedToEffective(parsed, source, input.detectedConfidence ?? null),
    display:
      parsed.status === "ok"
        ? `${parsed.startRoot}${suffix}`
        : `${parsed.startRoot}${suffix} → ${parsed.endRoot}${suffix}`,
    mode: detectedMode,
  };
}

function audioPasses(input: EffectiveKeyInput): boolean {
  return (
    (input.detectedConfidence ?? 0) >= MIN_CONFIDENCE &&
    (input.detectedMargin ?? 0) >= MIN_MARGIN &&
    (input.detectedWindowAgreement ?? 0) >= MIN_WINDOW_AGREEMENT
  );
}

export function getEffectiveKey(input: EffectiveKeyInput): EffectiveKey {
  const catalogParsed = input.catalogParsed ?? parseMusicalKey(input.catalogKey);
  const detectedParsed = parseMusicalKey(input.detectedKey);
  const hasDetected = detectedParsed.status === "ok" || detectedParsed.status === "range";
  const hasNewDiagnostics =
    input.detectedMargin != null || input.detectedWindowAgreement != null;
  const detectedPasses = hasDetected && (
    hasNewDiagnostics ? audioPasses(input) : input.detectedConfidence != null
  );

  if (catalogParsed.status === "ok" || catalogParsed.status === "range") {
    const warning =
      detectedPasses &&
      detectedParsed.startPitchClass != null &&
      catalogParsed.startPitchClass != null &&
      detectedParsed.startPitchClass !== catalogParsed.startPitchClass
        ? "catalog_audio_disagree"
        : "none";
    return parsedToEffective(catalogParsed, "catalog", null, warning);
  }

  if (catalogParsed.status === "unparseable") {
    return unknown("unparseable_catalog");
  }

  if (!hasDetected) return unknown();

  if (!hasNewDiagnostics) {
    return detectedToEffective(detectedParsed, "audio_legacy", input);
  }

  if (audioPasses(input)) {
    return detectedToEffective(detectedParsed, "audio", input);
  }

  return unknown("audio_low_confidence");
}

export function formatEffectiveKey(key: Pick<EffectiveKey, "display" | "startRoot" | "endRoot">): string | null {
  if (key.startRoot && key.endRoot && key.startRoot !== key.endRoot) {
    return `${key.startRoot} → ${key.endRoot}`;
  }
  return key.display;
}
