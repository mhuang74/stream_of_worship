import type { ResolvedGlueJunction } from "@/lib/glue/resolve";

// Client-safe unsaved-draft overlay (spec:
// specs/inpaint-transition-elevenlabs-v2.md §1.4, §5.3). A draft on one item
// makes its outgoing Junction and its predecessor's incoming Junction stale for
// audition and blocks generation until save. Readiness comes from the server
// resolver and is scoped to the adjacency it resolved.

export function glueForAdjacency(
  junction: ResolvedGlueJunction | undefined,
  fromItemId: string,
  toItemId: string | undefined
): ResolvedGlueJunction | null {
  return junction && junction.fromItemId === fromItemId && junction.toItemId === toItemId
    ? junction
    : null;
}

export interface DraftValidityOptions {
  editedItemId: string;
  hasUnsavedDraft: boolean;
}

/**
 * Returns a copy of the Junctions where the edited item's outgoing Junction and
 * its predecessor's incoming Junction are `stale` / `unsaved-draft`. Nothing
 * else changes.
 */
export function applyDraftValidity(
  junctions: readonly ResolvedGlueJunction[],
  { editedItemId, hasUnsavedDraft }: DraftValidityOptions
): ResolvedGlueJunction[] {
  return junctions.map((junction) => {
    if (!hasUnsavedDraft) return { ...junction };
    const isEditedOutgoing = junction.fromItemId === editedItemId;
    const isEditedIncoming = junction.toItemId === editedItemId;
    if (
      (isEditedOutgoing || isEditedIncoming) &&
      junction.glueEnabled &&
      junction.state !== "disabled" &&
      junction.toItemId !== null
    ) {
      return {
        ...junction,
        state: "stale",
        unavailableReason: "unsaved-draft",
      };
    }
    return { ...junction };
  });
}
