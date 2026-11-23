import "server-only";

/**
 * Presence-only check for the paid provider key (spec §2.3). The key is read
 * exactly once here and never returned, logged, or forwarded: callers only ever
 * learn whether the server environment is configured.
 */
export class ElevenLabsNotConfiguredError extends Error {
  readonly code = "GLUE_NOT_CONFIGURED";

  constructor() {
    super("AI transitions are not configured on this server.");
    this.name = "ElevenLabsNotConfiguredError";
  }
}

export function isElevenLabsConfigured(): boolean {
  const value = process.env.SOW_ELEVENLABS_API_KEY;
  return typeof value === "string" && value.trim().length > 0;
}

export function requireElevenLabsConfigured(): void {
  if (!isElevenLabsConfigured()) {
    throw new ElevenLabsNotConfiguredError();
  }
}
