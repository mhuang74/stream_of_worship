// The one-shot "listen to this AI transition" window: song context, the
// transition, and the incoming song's context. Pure and client-safe.

export const GLUE_PREVIEW_CONTEXT_SECONDS = 5;
export const GLUE_PREVIEW_BEATS_PER_BAR = 4;

export interface GluePreviewClip {
  startSeconds: number;
  endSeconds: number;
  leadInSeconds: number;
  leadOutSeconds: number;
}

/** 5 s rounded up to the next whole bar of `tempoBpm`; 1 s/beat when unusable. */
export function contextSecondsForTempo(tempoBpm: number | null): number {
  const beat = tempoBpm !== null && tempoBpm > 0 ? 60 / tempoBpm : 1;
  const bars = Math.max(
    1,
    Math.ceil(GLUE_PREVIEW_CONTEXT_SECONDS / beat / GLUE_PREVIEW_BEATS_PER_BAR)
  );
  return bars * GLUE_PREVIEW_BEATS_PER_BAR * beat;
}

/**
 * Clamps the context window to the two songs that actually own it, so the
 * preview never leaks into a third song, and to `limitSeconds` (the transport's
 * total) so a clip that reaches the Songset end still ends. Null when the clamp
 * leaves no audio.
 */
export function buildGluePreviewWindow(args: {
  glue: { startSeconds: number; endSeconds: number };
  outgoing: { startSeconds: number; endSeconds: number; tempoBpm: number | null };
  incoming: { startSeconds: number; endSeconds: number; tempoBpm: number | null };
  limitSeconds?: number;
}): GluePreviewClip | null {
  const leadInSeconds = contextSecondsForTempo(
    args.outgoing.tempoBpm ?? args.incoming.tempoBpm
  );
  const leadOutSeconds = contextSecondsForTempo(
    args.incoming.tempoBpm ?? args.outgoing.tempoBpm
  );
  const startSeconds = Math.max(
    args.outgoing.startSeconds,
    args.glue.startSeconds - leadInSeconds
  );
  const endSeconds = Math.min(
    args.incoming.endSeconds,
    args.glue.endSeconds + leadOutSeconds,
    args.limitSeconds ?? Number.POSITIVE_INFINITY
  );
  if (!(endSeconds > startSeconds)) return null;
  return { startSeconds, endSeconds, leadInSeconds, leadOutSeconds };
}
