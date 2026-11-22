// Canonical numeric and JSON identity for Glue descriptors
// (spec: specs/inpaint-transition-elevenlabs-v2.md §2.2).
//
// The Python mirror lives at
// delivery/render-worker/src/sow_render_worker/glue_descriptor.py and must emit
// byte-identical canonical JSON. Both test suites assert the shared fixture
// src/test/fixtures/glue-canonical.cross_language.json.
//
// Numbers are formatted from integer micro-units (never float String()): an
// integer-valued number serializes as `12` (not `12.0`), `-0` becomes `0`,
// and an exponent is never emitted.

import "server-only";

export type CanonicalObject = { [key: string]: CanonicalValue };

export type CanonicalValue =
  | null
  | boolean
  | number
  | string
  | CanonicalValue[]
  | CanonicalObject;

const MICRO_UNITS = 1_000_000;
const MICRO_UNITS_BIGINT = BigInt(MICRO_UNITS);
const ZERO_BIGINT = BigInt(0);

/**
 * Rounds to the sixth decimal, half up, and returns the canonical float.
 * Throws when the value is not finite or its micro-units exceed the exactly
 * representable integer range (canonical identity must stay exact).
 */
export function canonicalSixDp(value: number): number {
  return exactMicroUnits(value) / MICRO_UNITS;
}

/**
 * Canonical text for a number: sign, integer part, and a fraction appended
 * only when non-zero (zero-stripped). Never uses an exponent.
 */
export function canonicalNumberText(value: number): string {
  const micro = exactMicroUnits(value);
  const negative = micro < 0;
  const units = BigInt(Math.abs(micro));
  const whole = units / MICRO_UNITS_BIGINT;
  const fraction = units % MICRO_UNITS_BIGINT;
  const sign = negative ? "-" : "";
  if (fraction === ZERO_BIGINT) {
    return `${sign}${whole}`;
  }
  const digits = fraction.toString().padStart(6, "0").replace(/0+$/, "");
  return `${sign}${whole}.${digits}`;
}

/**
 * Compact JSON in insertion order, with strings escaped by JSON.stringify so
 * non-ASCII text stays raw (ensure_ascii=False semantics).
 */
export function canonicalJson(value: CanonicalValue): string {
  if (value === null) {
    return "null";
  }
  switch (typeof value) {
    case "boolean":
      return value ? "true" : "false";
    case "number":
      return canonicalNumberText(value);
    case "string":
      return JSON.stringify(value);
    case "object":
      if (Array.isArray(value)) {
        return `[${value.map(canonicalJson).join(",")}]`;
      }
      return `{${Object.entries(value)
        .map(([key, entry]) => `${JSON.stringify(key)}:${canonicalJson(entry as CanonicalValue)}`)
        .join(",")}}`;
    default:
      throw new TypeError(`canonicalJson: unsupported value type ${typeof value}`);
  }
}

function exactMicroUnits(value: number): number {
  if (!Number.isFinite(value)) {
    throw new TypeError(`canonical number must be finite, received ${String(value)}`);
  }
  // Math.round rounds .5 toward +Infinity, matching Python's
  // math.floor(v * 1e6 + 0.5) in glue_descriptor.py.
  const micro = Math.round(value * MICRO_UNITS);
  if (Math.abs(micro) > Number.MAX_SAFE_INTEGER) {
    throw new RangeError(`canonical number exceeds six-decimal precision: ${String(value)}`);
  }
  return micro;
}
