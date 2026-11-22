import { sanitizeGlueError } from "@/lib/glue/errors";

// Structured Glue events (spec: specs/inpaint-transition-elevenlabs-v2.md §4.2).
// One JSON line per event so log search and the operator runbook can key on
// `event` and `glueJobId`. Never pass keys, signed URLs, lyrics, or provider
// bodies here: `fields` is serialized as-is, and error details are sanitized.

export type GlueLogFields = Record<string, unknown>;

function dropUndefined(fields: GlueLogFields): GlueLogFields {
  const output: GlueLogFields = {};
  for (const [key, value] of Object.entries(fields)) {
    if (value !== undefined) output[key] = value;
  }
  return output;
}

/** Stable info event, e.g. `glue.job.created`, `glue.take.selected`. */
export function logGlueEvent(event: string, fields: GlueLogFields = {}): void {
  console.info(JSON.stringify({ event, ...dropUndefined(fields) }));
}

/** Failure event: same shape plus the sanitized message, on stderr. */
export function logGlueError(event: string, error: unknown, fields: GlueLogFields = {}): void {
  const detail = sanitizeGlueError(error);
  const name = error instanceof Error ? error.name : undefined;
  console.error(
    JSON.stringify({
      event,
      ...dropUndefined(fields),
      ...(name === undefined ? {} : { errorName: name }),
      ...(detail === undefined ? {} : { detail }),
    })
  );
}
