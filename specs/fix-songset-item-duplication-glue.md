# Preserve AI transitions when editing repeated Songset Items

## Summary

Fix four regressions where Duplicate Item and other Songset edits interact with
ElevenLabs Glue. Preserve valid takes, re-resolve changed junctions, and use the
same duration calculation for duplication and rendering.

## Duplicate transaction

- Under the existing owned Songset row lock, copy the source's Glue switch, beat
  count, and same-owner take pointer alongside its arrangement and ordinary
  transition settings. Keep a same-owner pointer even if it currently resolves
  as stale. Check the copied pointer against the new junction.
- Give the copy its new Item ID before projecting the order. Insert it
  immediately after the source, preserve position gaps, and apply the existing
  deleted-recording filter. Load referenced takes in one transaction-scoped query
  and validate each projected outgoing junction using the shared descriptor
  derivation and take validator. Supply effective takes to
  `getSongsetDurationSeconds`. Return `409 SONGSET_TOO_LONG` before writing if the
  result exceeds 1,500 seconds.
- Keep the duplicate endpoint's item response and error codes. Resolve the
  returned item against the committed order.

## Editor state and preview

- Queue Glue PATCHes with item mutations. Apply the returned Glue fields and
  timestamp to the current row through `commitItems`. Build queued reorders from
  current rows by Item ID and preserve field-specific transition and arrangement
  updates.
- Refresh from the existing no-store Songset GET after duplication and after
  append, remove, or reorder when Glue is enabled. Adopt server order, junctions,
  timestamps, count, and duration together before releasing the queue.
- Preview and local duration use a resolved take only when its source and
  destination match the current adjacent Items. If the refresh fails, keep the
  committed edit, use ordinary timing for unconfirmed junctions, and offer a
  retry of the GET. Retrying the read does not repeat the item mutation.
- When an arrangement save changes a cut, invalidate the adjacent junction
  views until they re-resolve. Their Item IDs stay the same across a cut change.

## Verification

- Duplicate A in `A → B` with a ready take and nondefault beats. Verify that
  `A → A copy` uses ordinary timing when its descriptor differs, while
  `A copy → B` retains the ready take. Cover disabled, stale, and foreign-owner
  selections.
- Reject a 1,490-second ordinary projection with 20 seconds of effective Glue
  before writing. Admit a projection within the cap when ready Glue replaces a
  longer ordinary gap.
- Exercise Glue PATCH followed by append, duplication refresh success and
  failure, and queued reorder. Check that saved Glue survives unrelated edits,
  old audio never lands on a changed junction, and a failed read never creates a
  second copy.
- Run the focused editor and duplicate tests, then the webapp suite, typecheck,
  lint, and Postgres smoke tests with the Node 24 CI runtime.

## Scope

Webapp-only change using the existing schema, routes, and saved takes. No
migration, render-worker change, or paid ElevenLabs call is required.
