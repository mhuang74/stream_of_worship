// Stable Glue error vocabulary shared by every job route, the client helpers,
// and the editor (spec: specs/inpaint-transition-elevenlabs-v2.md §4.1, §6.3).
//
// `detail` is the only free-form field any Glue response carries, so it must
// pass through `sanitizeGlueError` before it is persisted or serialized: URLs,
// filesystem paths, API keys/tokens, stack frames, and provider bodies never
// leave this module. Client-safe on purpose (`client.ts` classifies failures).

export const GLUE_ERROR_CODES = [
  "GLUE_NOT_CONFIGURED",
  "GLUE_INVALID_REQUEST",
  "GLUE_NOT_FOUND",
  "GLUE_STALE_SNAPSHOT",
  "GLUE_INPUTS_CHANGED",
  "GLUE_ACTIVE_JOB",
  "GLUE_RETRY_PAID",
  "GLUE_RETRY_PROCESSING_FREE",
  "GLUE_NOT_READY",
  "GLUE_PAID_NOT_CANCELLABLE",
  "GLUE_DAILY_LIMIT",
  "GLUE_DURATION_TOO_LONG",
  "GLUE_EXCERPT_TOO_SHORT",
  "GLUE_DISPATCH_FAILED",
  "GLUE_WORKER_FAILED",
  "GLUE_EXECUTION_DEADLINE",
  "GLUE_PROVIDER_UNKNOWN",
  "GLUE_PROVIDER_ENTITLEMENT",
  "GLUE_PROVIDER_PLAN",
  "GLUE_COPYRIGHT_REJECTED",
  "GLUE_AUDIO_UNAVAILABLE",
  "GLUE_JUNCTION_AUDIO_UNAVAILABLE",
] as const;

export type GlueErrorCode = (typeof GLUE_ERROR_CODES)[number];

const GLUE_ERROR_CODE_SET: ReadonlySet<string> = new Set(GLUE_ERROR_CODES);

export function isGlueErrorCode(value: unknown): value is GlueErrorCode {
  return typeof value === "string" && GLUE_ERROR_CODE_SET.has(value);
}

/**
 * i18n key for a user-facing Glue failure. Wording is "AI transition" only
 * (`src/lib/i18n/messages/glue.ts`); unknown codes fall back to the generic
 * `glue.error.unknown` key rather than leaking a raw code to the editor.
 */
const GLUE_ERROR_MESSAGE_KEYS: Record<GlueErrorCode, string> = {
  GLUE_NOT_CONFIGURED: "glue.error.notConfigured",
  GLUE_INVALID_REQUEST: "glue.error.invalidRequest",
  GLUE_NOT_FOUND: "glue.error.notFound",
  GLUE_STALE_SNAPSHOT: "glue.error.staleSnapshot",
  GLUE_INPUTS_CHANGED: "glue.error.inputsChanged",
  GLUE_ACTIVE_JOB: "glue.error.activeJob",
  GLUE_RETRY_PAID: "glue.error.retryPaid",
  GLUE_RETRY_PROCESSING_FREE: "glue.error.retryProcessingFree",
  GLUE_NOT_READY: "glue.error.notReady",
  GLUE_PAID_NOT_CANCELLABLE: "glue.error.paidNotCancellable",
  GLUE_DAILY_LIMIT: "glue.error.dailyLimit",
  GLUE_DURATION_TOO_LONG: "glue.error.durationTooLong",
  GLUE_EXCERPT_TOO_SHORT: "glue.error.excerptTooShort",
  GLUE_DISPATCH_FAILED: "glue.error.dispatchFailed",
  GLUE_WORKER_FAILED: "glue.error.workerFailed",
  GLUE_EXECUTION_DEADLINE: "glue.error.executionDeadline",
  GLUE_PROVIDER_UNKNOWN: "glue.error.providerUnknown",
  GLUE_PROVIDER_ENTITLEMENT: "glue.error.providerEntitlement",
  GLUE_PROVIDER_PLAN: "glue.error.providerPlan",
  GLUE_COPYRIGHT_REJECTED: "glue.error.copyrightRejected",
  GLUE_AUDIO_UNAVAILABLE: "glue.error.audioUnavailable",
  GLUE_JUNCTION_AUDIO_UNAVAILABLE: "glue.error.junctionAudioUnavailable",
};

export const GLUE_UNKNOWN_ERROR_MESSAGE_KEY = "glue.error.unknown";

export function glueErrorMessageKey(code: string): string {
  return isGlueErrorCode(code) ? GLUE_ERROR_MESSAGE_KEYS[code] : GLUE_UNKNOWN_ERROR_MESSAGE_KEY;
}

/** HTTP status a job route uses for each stable code. */
const GLUE_ERROR_HTTP_STATUS: Record<GlueErrorCode, number> = {
  GLUE_NOT_CONFIGURED: 503,
  GLUE_INVALID_REQUEST: 400,
  GLUE_NOT_FOUND: 404,
  GLUE_STALE_SNAPSHOT: 409,
  GLUE_INPUTS_CHANGED: 409,
  GLUE_ACTIVE_JOB: 409,
  GLUE_RETRY_PAID: 409,
  GLUE_RETRY_PROCESSING_FREE: 409,
  GLUE_NOT_READY: 409,
  GLUE_PAID_NOT_CANCELLABLE: 409,
  GLUE_DAILY_LIMIT: 429,
  GLUE_DURATION_TOO_LONG: 422,
  GLUE_EXCERPT_TOO_SHORT: 422,
  GLUE_DISPATCH_FAILED: 503,
  GLUE_WORKER_FAILED: 409,
  GLUE_EXECUTION_DEADLINE: 409,
  GLUE_PROVIDER_UNKNOWN: 409,
  GLUE_PROVIDER_ENTITLEMENT: 409,
  GLUE_PROVIDER_PLAN: 409,
  GLUE_COPYRIGHT_REJECTED: 422,
  GLUE_AUDIO_UNAVAILABLE: 409,
  GLUE_JUNCTION_AUDIO_UNAVAILABLE: 422,
};

export function glueErrorHttpStatus(code: string): number {
  return isGlueErrorCode(code) ? GLUE_ERROR_HTTP_STATUS[code] : 500;
}

/** The typed failure every Glue job operation raises; routes map it verbatim. */
export class GlueJobRequestError extends Error {
  readonly code: GlueErrorCode;
  readonly status: number;
  readonly jobId?: string;
  readonly providerStatus?: number;
  readonly detail?: string;

  constructor(
    code: GlueErrorCode,
    message?: string,
    extra: { jobId?: string; providerStatus?: number; detail?: string } = {}
  ) {
    super(message ?? code);
    this.name = "GlueJobRequestError";
    this.code = code;
    this.status = glueErrorHttpStatus(code);
    this.jobId = extra.jobId;
    this.providerStatus = extra.providerStatus;
    this.detail = extra.detail === undefined ? undefined : sanitizeGlueError(extra.detail);
  }
}

const MAX_DETAIL_LENGTH = 400;

// Ordered redactions. Each pattern is deliberately narrow so a legitimate
// sentence survives; anything that looks like a credential, URL, path, or
// stack frame is removed instead of escaped.
const REDACTIONS: ReadonlyArray<[RegExp, string]> = [
  // Signed and plain provider/storage URLs.
  [/\b(?:https?|s3|gs|wss?):\/\/[^\s"'<>]+/gi, "[url]"],
  // Bearer tokens and explicit credential headers.
  [/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi, "[token]"],
  [/\b(?:x-api-key|xi-api-key|api[_-]?key|access[_-]?key(?:[_-]?id)?|secret[_-]?access[_-]?key|authorization)\b\s*[:=]\s*["']?[^\s"',;}]+/gi, "[redacted]"],
  // Provider-style secret keys.
  [/\bsk_[A-Za-z0-9_-]{8,}\b/g, "[redacted]"],
  // Filesystem paths (posix absolute, home-relative, and Windows drive paths).
  [/(?:^|(?<=[\s"'(=]))\/(?:[^\s"'/]+\/)+[^\s"'/)]*/g, "[path]"],
  [/(?:^|(?<=[\s"'(=]))~(?:[^\s"'/]+\/)+[^\s"'/)]*/g, "[path]"],
  [/\b[A-Za-z]:\\[^\s"'<>]+/g, "[path]"],
  // Stack frames and Node trace headers (line-anchored: a sentence may contain
  // the word "at").
  [/^\s*at\s+(?:async\s+)?[^\s()]+(?:\s*\([^)]*\))?/gm, "[stack]"],
  [/^\s*(?:Error|TypeError|RangeError|SyntaxError|FetchError|AxiosError)\b.*$/gm, "error"],
  // Control characters and zero-width joiners from provider bodies.
  [/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u200b-\u200d\ufeff]/g, " "],
];

/**
 * Reduces any provider/worker text to a short sentence that is safe to persist
 * and return. Allowlisted Glue codes pass through unchanged; everything else
 * is scrubbed, whitespace-collapsed, and truncated. Empty input is `undefined`.
 */
export function sanitizeGlueError(value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined;
  const raw =
    value instanceof Error
      ? value.message
      : typeof value === "string"
        ? value
        : typeof value === "number" || typeof value === "boolean"
          ? String(value)
          : "";
  if (raw.trim().length === 0) return undefined;

  const trimmed = raw.trim();
  if (isGlueErrorCode(trimmed)) return trimmed;

  let text = trimmed;
  for (const [pattern, replacement] of REDACTIONS) {
    text = text.replace(pattern, replacement);
  }
  text = text.replace(/\s+/g, " ").trim();
  if (text.length === 0) return undefined;
  if (text.length > MAX_DETAIL_LENGTH) {
    text = `${text.slice(0, MAX_DETAIL_LENGTH - 1).trimEnd()}…`;
  }
  return text;
}
