// The worker's `error_detail` token for an unusable junction, turned back into
// a typed cause so the rail can name the input that has to change. Client-safe.

export type GlueFailureCause =
  | { kind: "unsupported-window"; supportedBeats: 2 | 4 | 8 | 16 | null }
  | { kind: "insufficient-search-source" }
  | { kind: "source-audio-unavailable"; side: "A" | "B" | null }
  | { kind: "decode-failed"; side: "A" | "B" | null };

const BEATS_CHOICES = new Set([2, 4, 8, 16]);

function parseSide(token: string | undefined): "A" | "B" | null {
  if (token === undefined) return null;
  return token === "A" || token === "B" ? token : null;
}

/**
 * `"<kind> [supportedBeats=N] [side=A|B]"`. Anything else — including an
 * unrecognised value for a known token — is `null`, so a raw detail never
 * renders as copy.
 */
export function parseGlueFailureCause(
  detail: string | null | undefined
): GlueFailureCause | null {
  if (!detail) return null;
  const tokens = detail.trim().split(/\s+/);
  const [kind, ...rest] = tokens;
  if (kind === undefined) return null;

  const named = new Map<string, string>();
  for (const token of rest) {
    const separator = token.indexOf("=");
    if (separator <= 0) return null;
    named.set(token.slice(0, separator), token.slice(separator + 1));
  }

  switch (kind) {
    case "unsupported-window": {
      const raw = named.get("supportedBeats");
      if (raw === undefined) return { kind, supportedBeats: null };
      const beats = Number(raw);
      if (!BEATS_CHOICES.has(beats)) return null;
      return { kind, supportedBeats: beats as 2 | 4 | 8 | 16 };
    }
    case "insufficient-search-source":
      return rest.length === 0 ? { kind } : null;
    case "source-audio-unavailable":
    case "decode-failed": {
      const raw = named.get("side");
      if (raw !== undefined && raw !== "A" && raw !== "B") return null;
      return { kind, side: parseSide(raw) };
    }
    default:
      return null;
  }
}
