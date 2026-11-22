# A Songset Item, not a Song, identifies an occurrence

A Songset may hold the same Song more than once: an arranged song is often reused for a different moment in the flow. The schema always allowed it — `songset_items` has no unique constraint on `(songset_id, song_id)` — and the mixer already laid out repeats, but three components assumed a Song appeared once. The webapp refused a second add, the render worker bucketed lyrics by Song title, and chapters/title cards could not name occurrence 2.

The Songset Item is now the unit of identity everywhere. Render-worker lyric lines carry `item_id` (not the Song title), `group_lyrics_by_item` buckets them by Item, and `_resolve_visual_state` reads the current segment's own bucket, so occurrence 1's last-line fade and blank-gap windows are computed from its own lines. Chapters and the title-card song list are ordered by Item; `chapter_titles` suffixes the k-th use of a base title with its ordinal ("Title", "Title (2)") while a single-occurrence Songset keeps its plain titles. The webapp's add affordance always adds and shows an `×N` badge, and each editor row offers "Duplicate item", which copies the Item (Song Arrangement included) directly below itself under the same cap and duration admission as any append.

Two consequences are accepted. Positions are an ordering key, not a dense index: deleting an Item leaves a gap, so duplicates shift `position > source.position` by one and appends use `max(position) + 1` rather than the item count. And the per-Songset cap counts Items, not distinct Songs.

## Considered options

- **[Rejected] Keying worker lyrics by `(song_id, occurrence index)`** — every consumer would have to recompute the ordinal, and a stale or reordered list would silently mismatch; the Item id is already stable and unique.
- **[Rejected] A `songset_item_occurrences` table** — Items already are the occurrences; a second table would duplicate the Transition settings and arrangement per row for no gain.
- **[Rejected] Renumbering positions on delete** — it rewrites every later row on each delete and races with concurrent edits; gaps are harmless to `ORDER BY position`.
- **[Rejected] Blocking a second add but allowing duplicate-from-the-row** — the Browse sheet is where a user re-adds a song mid-flow; hiding it there made the feature undiscoverable.
