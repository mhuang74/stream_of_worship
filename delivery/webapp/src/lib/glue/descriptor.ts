import "server-only";

import { createHash } from "node:crypto";

import { canonicalJson, canonicalSixDp, type CanonicalObject } from "@/lib/glue/canonical";
import type { SongsetBoundary } from "./boundary-types";

// Canonical Glue descriptor identity (spec:
// specs/inpaint-transition-elevenlabs-v2.md §2.2). The Python mirror at
// delivery/render-worker/src/sow_render_worker/glue_descriptor.py must produce
// the same canonical JSON and sha256 key; the shared fixture
// src/test/fixtures/glue-canonical.cross_language.json is asserted by both.

const DESCRIPTOR_VERSION = 2 as const;
const DESCRIPTOR_MODEL = "music_v2_5" as const;
const DESCRIPTOR_PROMPT_VERSION = 2 as const;
const DESCRIPTOR_EXCERPT_SECONDS = 12 as const;
const BEATS_CHOICES = [2, 4, 8, 16] as const;

export type GlueBeats = (typeof BEATS_CHOICES)[number];

export interface GlueDescriptorSideInput {
  contentHash: string;
  steps: [number, number][] | null;
}

export interface GlueDescriptorInput {
  beats: GlueBeats;
  toTempoBpm: number;
  from: GlueDescriptorSideInput;
  to: GlueDescriptorSideInput;
}

export interface GlueDescriptorSide extends CanonicalObject {
  contentHash: string;
  steps: [number, number][] | null;
}

export interface GlueDescriptor extends CanonicalObject {
  v: typeof DESCRIPTOR_VERSION;
  model: typeof DESCRIPTOR_MODEL;
  promptVersion: typeof DESCRIPTOR_PROMPT_VERSION;
  excerptSeconds: typeof DESCRIPTOR_EXCERPT_SECONDS;
  beats: GlueBeats;
  toTempoBpm: number;
  from: GlueDescriptorSide;
  to: GlueDescriptorSide;
}

export interface GlueDescriptorBuild {
  descriptor: GlueDescriptor;
  canonicalJson: string;
  key: string;
}

/**
 * Builds the canonical descriptor, its canonical JSON, and the sha256 key.
 * `steps: null` stays `null` — never substituted with a full-range step.
 */
export function buildGlueDescriptor(input: GlueDescriptorInput): GlueDescriptorBuild {
  const descriptor: GlueDescriptor = {
    v: DESCRIPTOR_VERSION,
    model: DESCRIPTOR_MODEL,
    promptVersion: DESCRIPTOR_PROMPT_VERSION,
    excerptSeconds: DESCRIPTOR_EXCERPT_SECONDS,
    beats: resolveBeats(input.beats),
    toTempoBpm: canonicalSixDp(input.toTempoBpm),
    from: resolveSide(input.from),
    to: resolveSide(input.to),
  };
  const canonical = canonicalJson(descriptor);
  return {
    descriptor,
    canonicalJson: canonical,
    key: createHash("sha256").update(canonical, "utf8").digest("hex"),
  };
}

export function glueDescriptorKey(input: GlueDescriptorInput): string {
  return buildGlueDescriptor(input).key;
}

export function buildBoundaryDescriptor(
  target: SongsetBoundary,
  beats: GlueBeats,
  tempoBpm: number,
  recording: GlueDescriptorSideInput
) {
  const descriptor = {
    v: 1,
    target,
    model: DESCRIPTOR_MODEL,
    promptVersion: 1,
    excerptSeconds: DESCRIPTOR_EXCERPT_SECONDS,
    simdps: { model: "acestep-v15-base", steps: 100, outerContextSeconds: 2 },
    beats: resolveBeats(beats),
    toTempoBpm: canonicalSixDp(tempoBpm),
    recording: resolveSide(recording),
  };
  const canonical = canonicalJson(descriptor);
  return {
    descriptor,
    canonicalJson: canonical,
    key: createHash("sha256").update(canonical, "utf8").digest("hex"),
  };
}

function resolveBeats(beats: GlueBeats): GlueBeats {
  if (!BEATS_CHOICES.includes(beats)) {
    throw new RangeError(
      `Glue descriptor beats must be one of 2, 4, 8, 16; received ${String(beats)}`
    );
  }
  return beats;
}

function resolveSide(side: GlueDescriptorSideInput): GlueDescriptorSide {
  if (typeof side.contentHash !== "string" || side.contentHash.length === 0) {
    throw new TypeError("Glue descriptor side requires a non-empty contentHash");
  }
  const steps =
    side.steps === null
      ? null
      : side.steps.map(
          ([startSeconds, endSeconds]) =>
            [canonicalSixDp(startSeconds), canonicalSixDp(endSeconds)] as [number, number]
        );
  return { contentHash: side.contentHash, steps };
}
