import "server-only";

// Provider attribution for one terminal Glue job, read from the stored
// `glue_jobs.provider_context_json` route. Three shapes exist (the cascade's
// winner, the direct SimDPS route, and the ElevenLabs stage), and the log
// field must name the same provider the worker's own events use.

export interface GlueProviderAttribution {
  provider: string;
  model: string | null;
}

function objectOf(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

/**
 * `simdps-<winner>` after a cascade race, `simdps-runpod` for a direct RunPod
 * route, `elevenlabs` for the primary stage. Anything unrecognised is `null`:
 * a job that failed before contact has no route at all.
 */
export function attributeGlueProvider(
  providerContextJson: unknown
): GlueProviderAttribution | null {
  const route = objectOf(providerContextJson);
  if (route === null) return null;

  if (route.provider === "cascade") {
    const winner = objectOf(route.winner);
    const backend = nonEmptyString(winner?.backend);
    if (winner === null || backend === null) return null;
    return { provider: `simdps-${backend}`, model: nonEmptyString(winner.model) };
  }
  if (route.provider === "simdps") {
    return { provider: "simdps-runpod", model: nonEmptyString(route.model) };
  }
  if (route.provider === "elevenlabs") {
    return { provider: "elevenlabs", model: nonEmptyString(route.model) ?? "music_v2_5" };
  }
  return null;
}
