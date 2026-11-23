import "server-only";

import { createHash } from "node:crypto";

import { canonicalJson, canonicalSixDp, type CanonicalObject } from "@/lib/glue/canonical";

// Canonical Glue excerpt identity (spec:
// specs/inpaint-transition-elevenlabs-v2.md §2.2). The Python mirror at
// delivery/render-worker/src/sow_render_worker/glue_descriptor.py must produce
// the same canonical JSON and sha256 key; the shared fixture
// src/test/fixtures/glue-canonical.cross_language.json is asserted by both.

const EXCERPT_VERSION = 2 as const;

export interface GlueExcerptKeyInput {
  side: "A" | "B";
  seconds: number;
  contentHash: string;
  steps: [number, number][] | null;
}

export interface GlueExcerptKeyBuild {
  canonicalJson: string;
  key: string;
}

interface GlueExcerptPayload extends CanonicalObject {
  v: typeof EXCERPT_VERSION;
  side: "A" | "B";
  seconds: number;
  contentHash: string;
  steps: [number, number][] | null;
}

/**
 * Canonical excerpt key: sha256 over the canonical JSON in the frozen field
 * order `v, side, seconds, contentHash, steps`. `steps: null` stays `null`.
 */
export function buildGlueExcerptKey(input: GlueExcerptKeyInput): GlueExcerptKeyBuild {
  if (input.side !== "A" && input.side !== "B") {
    throw new RangeError(`Glue excerpt side must be "A" or "B"; received ${String(input.side)}`);
  }
  if (typeof input.contentHash !== "string" || input.contentHash.length === 0) {
    throw new TypeError("Glue excerpt requires a non-empty contentHash");
  }
  const payload: GlueExcerptPayload = {
    v: EXCERPT_VERSION,
    side: input.side,
    seconds: canonicalSixDp(input.seconds),
    contentHash: input.contentHash,
    steps:
      input.steps === null
        ? null
        : input.steps.map(
            ([startSeconds, endSeconds]) =>
              [canonicalSixDp(startSeconds), canonicalSixDp(endSeconds)] as [number, number]
          ),
  };
  const canonical = canonicalJson(payload);
  return {
    canonicalJson: canonical,
    key: createHash("sha256").update(canonical, "utf8").digest("hex"),
  };
}
