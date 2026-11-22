import type { ResolvedGlueJunction } from "@/lib/glue/resolve";

// Client-safe unsaved-draft overlay (spec:
// specs/inpaint-transition-elevenlabs-v2.md §1.4, §5.3). A draft on one item
// makes its outgoing Junction and its predecessor's incoming Junction stale for
// audition and blocks generation until save. This is the only client-side state
// adjustment; everything else comes from the server resolver.

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
