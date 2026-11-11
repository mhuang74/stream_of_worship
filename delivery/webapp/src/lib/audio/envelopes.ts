// Production crossfade envelope math for the shared browser audio engine
// (ADR-0012: browser audio engine decodes clips, not media elements).
//
// Two envelopes per item on TWO nodes in series, so they compose by
// multiplication exactly like the render worker's two sequential afade filters.
// A single piecewise gain
// cannot express the product: an item shorter than its own fade-in plus the
// successor-owned fade-out would step to full volume mid-overlap.

import type { ArrangementTimelineItem } from "@/lib/arrangements";

export interface EnvelopePoint {
  /** Absolute timeline second. */
  t: number;
  /** Target gain at that second. */
  v: number;
}

/** Incoming envelope: 0 → 1 across the item's OWN crossfade window. */
export function incomingEnvelope(item: ArrangementTimelineItem): EnvelopePoint[] {
  if (item.crossfadeSeconds > 0) {
    return [
      { t: item.startSeconds, v: 0 },
      { t: item.startSeconds + item.crossfadeSeconds, v: 1 },
    ];
  }
  return [{ t: item.startSeconds, v: 1 }];
}

/**
 * Outgoing envelope: 1 → 0 across [itemEnd − successorCrossfade, itemEnd].
 * The successor OWNS this fade, so its crossfadeSeconds drives the ramp.
 */
export function outgoingEnvelope(
  item: ArrangementTimelineItem,
  nextItem: ArrangementTimelineItem | null,
): EnvelopePoint[] {
  const itemEnd = item.startSeconds + item.durationSeconds;
  if (nextItem && nextItem.crossfadeSeconds > 0 && itemEnd > item.startSeconds) {
    return [
      { t: itemEnd - nextItem.crossfadeSeconds, v: 1 },
      { t: itemEnd, v: 0 },
    ];
  }
  return [{ t: item.startSeconds, v: 1 }];
}

/** Piecewise-linear value of an envelope at timeline second t. */
export function envelopeValueAt(points: EnvelopePoint[], t: number): number {
  if (points.length === 0) return 1;
  if (t <= points[0].t) return points[0].v;
  for (let i = 1; i < points.length; i++) {
    const prev = points[i - 1];
    const next = points[i];
    if (t <= next.t) {
      if (next.t === prev.t) return next.v;
      return prev.v + ((next.v - prev.v) * (t - prev.t)) / (next.t - prev.t);
    }
  }
  return points[points.length - 1].v;
}

/** Composed gain the listener hears: the two series envelopes multiplied. */
export function composedGainAt(
  item: ArrangementTimelineItem,
  nextItem: ArrangementTimelineItem | null,
  t: number,
): number {
  return (
    envelopeValueAt(incomingEnvelope(item), t) *
    envelopeValueAt(outgoingEnvelope(item, nextItem), t)
  );
}

// Writes one envelope onto one AudioParam relative to the context origin at
// `position`. Fresh nodes are built per schedule, so no cancel is needed;
// value-at-position re-entry matches the production seek behaviour.
export function scheduleEnvelope(
  param: AudioParam,
  points: EnvelopePoint[],
  position: number,
  originContextTime: number,
): void {
  param.setValueAtTime(envelopeValueAt(points, position), originContextTime);
  let next = 0;
  const first = points[0];
  if (first && position < first.t) {
    // Item has not started yet: hold, then ramp from its own start second.
    param.setValueAtTime(first.v, originContextTime + (first.t - position));
    next = 1;
  }
  for (; next < points.length; next++) {
    const point = points[next];
    if (point.t <= position) continue;
    param.linearRampToValueAtTime(point.v, originContextTime + (point.t - position));
  }
}
