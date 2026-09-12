# Spec: Collect User Lyrics Feedback

Status: ready-for-agent
Design session: grilled 2026-09-12 (22 questions, 4 rounds). Glossary term: CONTEXT.md "Lyrics Feedback". Governing ADR: docs/adr/0007-lyrics-feedback-is-advisory.md (feedback is advisory; never mutates pipeline state; regeneration never auto-resolves).

## Problem Statement

A worship participant listening to a song in the webapp sees the Lyrics pull-up in the audio player. Sometimes the Lyrics are missing entirely, and sometimes the synced timestamps drift from the actual audio — the highlighted line shows up early, late, or not at all. Today the participant has no way to tell anyone. The broken Lyrics stay broken until an admin independently notices, and the admin has no idea which recordings users are actually struggling with.

## Solution

The Lyrics pull-up gains a lightweight one-tap feedback affordance: a happy face and a sad face. Happy means "these Lyrics serve me" (a positive verification signal). Sad asks, inline in the panel, why — lyrics missing, timing wrong, text wrong, or other — and records the answer. Feedback is attributed to the signed-in user and the Recording they were listening to, is retractable, and can be switched between happy and sad. The same affordance appears on the Worship Playback Controller's lyrics sheet, targeting the song currently playing. An admin then works a queue in `sow-admin lyrics feedback`: every Recording with unresolved feedback, grouped, with a suggested action (generate Lyrics, re-align, or manual text review), and marks feedback resolved once addressed.

## User Stories

1. As a worship participant, I want to tap a happy face on the Lyrics pull-up when the lyrics serve me well, so that admins know this Recording's Lyrics are verified by a real user.
2. As a worship participant, I want to tap a sad face when the Lyrics are missing, so that admins know to generate synced Lyrics for this Recording.
3. As a worship participant, I want to tell admins the timing is wrong when the highlighted line doesn't match the audio, so that they re-align instead of regenerating.
4. As a worship participant, I want to report wrong text, so that transcription or scrape errors get corrected.
5. As a worship participant, I want an "other" reason, so that I can report problems outside the fixed vocabulary.
6. As a worship participant, I want to see my current feedback state reflected on the icons (filled when active), so that I know my report was recorded.
7. As a worship participant, I want to retract my feedback by tapping the active icon again, so that I can correct a mis-tap.
8. As a worship participant, I want to switch my feedback from happy to sad (or back) in one tap, so that my latest opinion wins without fiddly delete-and-redo.
9. As a worship participant, I want my feedback to persist per Recording, so that revisiting the song later still shows what I reported.
10. As a worship participant, I want the reason choices to match what I'm actually looking at, so that I'm never offered "timing" when there are no synced lyrics on screen.
11. As a worship participant, I want a happy face only when there is something to be happy about, so that I'm never invited to praise a blank panel.
12. As a worship participant, I want to still report "missing" when the panel falls back to unsynced text, so that admins know I want proper synced Lyrics, not just any text.
13. As a worship participant, I want feedback from the sad flow to appear immediately without a page reload, so that the panel never feels like it lost my input.
14. As a worship participant, I want the feedback row to stay out of the way of the lyrics, so that reading lyrics remains the primary activity.
15. As a worship participant using the Traditional Chinese UI, I want all feedback affordances in my Locale, so that the feature doesn't feel foreign.
16. As a worship participant, I want my sad report to be private to me and the admins, so that other users don't see my complaints (and I don't see theirs).
17. As a worship participant, I want admins to fix the canonical Lyrics, so that I don't have to maintain my own copy (personal overrides are a separate, existing feature).
18. As a worship leader operating the Worship Playback Controller, I want the same happy/sad affordance on the lyrics sheet, so that I can report problems mid-service without switching apps.
19. As a worship leader on the controller, I want feedback to target the song currently playing, so that I never have to figure out which Recording I'm reporting on.
20. As a worship leader on the controller, I want the feedback row to appear only once a song is current, so that I never report against an ambiguous target.
21. As a worship leader on the controller, I want my controller feedback to count as the same feedback I'd give from the player panel, so that one Recording carries one opinion from me regardless of surface.
22. As an admin, I want a queue of Recordings with open lyrics feedback, so that I know exactly which songs users are struggling with.
23. As an admin, I want the queue grouped by Recording with an open count and a breakdown of reasons, so that one broken song doesn't flood my list with duplicate rows.
24. As an admin, I want a suggested action per Recording (generate Lyrics / re-align / manual text review), so that I know which pipeline command addresses the complaint.
25. As an admin, I want to resolve all open feedback for a Recording in one command, so that fixing one LRC closes every report against it.
26. As an admin, I want to unresolve, so that I can revert a mistaken resolution.
27. As an admin, I want to filter the queue by rating, reason, and open state, so that I can work, say, only timing complaints first.
28. As an admin, I want each queue row to show the Recording's pipeline Lyrics status, so that I can distinguish "pipeline says complete but user disagrees" from "pipeline never finished".
29. As an admin, I want feedback resolution to be invisible to the reporting user, so that users aren't prompted to re-report or churn on resolution state.
30. As an admin, I want feedback to never change the pipeline's own status fields, so that the analysis pipeline and user opinion stay on separate axes (per ADR 0007).
31. As an admin, I want regeneration to never auto-close feedback, so that a regeneration that didn't actually fix the timing doesn't silently bury complaints.
32. As a developer, I want the feedback API to enforce which ratings/reasons are valid for a Recording's actual lyrics situation, so that contradictory reports never reach the database.
33. As a developer, I want feedback storage and admin-side bootstrap DDL kept in lockstep with the existing user-tables convention, so that both deploy paths create the table.

## Implementation Decisions

- **Domain terms**: the feature is **Lyrics Feedback** (glossary: CONTEXT.md; avoid "LRC feedback", "report", "vote"). It attaches to the **Recording** (content hash), never the Song, because Lyrics are per-Recording. Existing `user_lrc_override` naming predates the glossary and is left alone.
- **Storage (webapp side)**: a new `lyrics_feedback` table in the webapp's Drizzle schema, owned by a Drizzle migration (same convention as `user_favorite_songs`, `user_lrc_override`). Columns: `id` (nanoid text PK), `user_id` (FK to user, cascade), `recording_content_hash` (FK to recordings.content_hash, cascade), `rating` (text: `happy` | `sad`), `reason` (nullable text: `missing` | `timing` | `wrong_text` | `other`; always null for happy), `resolved_at` (nullable timestamptz), `created_at`/`updated_at`. UNIQUE(userId, recordingContentHash) — one row per user per Recording.
- **Storage (admin mirror)**: the admin CLI's user-data bootstrap owns parallel `CREATE TABLE IF NOT EXISTS` DDL for the same table plus indexes and an `updated_at` trigger, appended to its statement list — same dual-DDL convention as `user_lrc_override`/`lyric_mark`/`songset_share`. The core catalog schema file needs no mirror.
- **Lifecycle**: upsert on (user, recording). Submitting happy while sad exists (or vice versa) overwrites; submitting the same rating again retracts (row deleted). Admin sets/clears `resolved_at`. Nothing else ever writes `resolved_at` (ADR 0007).
- **API**: one route resource keyed by recording content hash under the webapp API, auth-gated like every webapp API: GET returns the caller's current feedback for that Recording (null when none), PUT upserts `{ rating, reason? }`, DELETE retracts. Server-side **state-aware validation**: the route resolves the Recording's actual lyrics situation (canonical Lyrics with parseable timestamps vs. unsynced text fallback vs. none — the same resolution order the lyrics API uses: user is irrelevant here, R2 canonical source, then scraped unsynced text) and rejects contradictions:
  - `happy` accepted only when Lyrics exist (synced or unsynced);
  - `missing` accepted only when no parseable synced Lyrics (unsynced-only or nothing);
  - `timing` accepted only when parseable synced Lyrics exist;
  - `wrong_text` / `other` accepted in any state.
  Invalid combinations return 400 with a machine-readable error; the reason column must be null for happy and non-null for sad.
- **Panel UX**: the audio player's lyrics panel gains a slim footer action row pinned below the scrolling lyrics area (not overlaying text): happy + sad icons, right-aligned. Present in all four content states (parsed synced Lyrics / unsynced lines fallback / raw text / no lyrics). Happy is hidden when there are no lyrics at all. Sad expands inline reason chips within the footer row (no modal); chips offered are filtered by the panel state to match server validation; tapping a chip submits. Tapping the active icon again retracts. The user's existing feedback loads on panel open (GET) and drives initial icon state.
- **Controller UX**: the operator controller's lyrics sheet (the swipe-up lyric jump list) gains the same footer action row inside the open sheet, targeting the Recording of the **current chapter**. The controller already fetches the songset detail (auth-gated) whose items carry the full recording content hash; chapter position maps to the songset item at the same position. The affordance renders only once a chapter is current (playback started / position known). The anonymous share-controller variant gets nothing (no user to attribute feedback to; the public share API carries no recording hashes by design).
- **Surfaces in scope**: audio player lyrics panel + operator controller lyrics sheet. The Projection screen stays chrome-free (glossary Projection definition). The share-token controller variant is excluded. Controller feedback merges into the same (user, recording) row as panel feedback — no new cardinality.
- **Resolution visibility**: none. The reporting user's icons simply revert to neutral when they interact next; no "resolved" badge, no aggregate counts shown to any user.
- **Admin CLI**: new top-level `lyrics` command group (future home of lyrics-curation commands). Two commands:
  - `lyrics feedback list` — groups open feedback by Recording (join to songs for titles), columns: song title, recording content hash (prefix), pipeline Lyrics status, open count, reason breakdown, latest report date; `--rating`, `--reason`, `--all` (include resolved) filters; a **suggested action** column mapping reason→command: `missing`→generate Lyrics, `timing`→re-align, `wrong_text`/`other`→manual review.
  - `lyrics feedback resolve <content-hash-or-song-id>` (and `unresolve`) — bulk-resolves all open rows for that Recording; `--note` is deliberately not offered (no comment column exists).
- **Non-goals (confirmed in grilling)**: no mutation of the pipeline's Lyrics status field; no auto-resolve on regeneration; no cross-user aggregates; no anonymous feedback; no Android UI (API stays client-agnostic); the user self-fix editor stack stays unmounted; no free-text comments; no playback-position capture on reports (Q3 decision: no timestamp column).
- **i18n**: all new UI strings in both Locales (English and Traditional Chinese) via the existing message catalogs; strings follow the existing `audio.lyrics.*` / controller lyrics namespaces.

## Testing Decisions

- **Good tests assert external behavior only**: what a consumer observes — HTTP status/response bodies from the route handlers given a mocked data layer; rendered affordance state (icons, chips, hiding rules, i18n) given mocked hooks; CLI output and database end-state given a real (testcontainers) database. No assertions on component internals, hook wiring, or SQL text.
- **Seam 1 (the one new seam) — the lyrics-feedback API route handlers**: business logic lives here (state-aware validation matrix, upsert/retract, attribution). Tested handler-style with mocked auth + db, exactly like the existing lyrics override/marks route tests in the webapp test suite (prior art: `src/test/api/lyrics/overrides.test.ts`, `recordingContentHash.test.ts`). The validation matrix (happy vs missing vs timing per lyrics situation) is the highest-value test surface; each matrix cell gets one test.
- **Seam 2 (existing) — component level**: the lyrics panel footer and the controller sheet footer, in the existing component test style with mocked hooks (prior art: `PlayerLyricsPanel.test.tsx`, `LyricJumpList.test.tsx`, `AudioPlayerBar.test.tsx`). Cover: icon states per content state, happy hidden on no-lyrics, chip filtering per state, tap-to-retract, sad→chip→submit flow, both Locales via the existing render-with-locale helper.
- **Seam 3 (existing) — admin CLI integration**: `CliRunner` against testcontainers Postgres with full schema init (prior art: `tests/admin/test_audio_commands.py`, `tests/db/test_full_schema_init.py` — note the schema-init test must also prove the mirrored DDL creates the new table, and its table-drop cleanup list must include it). Cover: schema init includes the table; list grouping/counts/filters; resolve/unresolve end state.
- No E2E seam this iteration (user-confirmed seams decision); manual browser validation per the repo's visual-validation recipe if needed.

## Out of Scope

- Mounting the user self-fix editors (Lyrics review/edit sheets writing personal overrides) or funneling sad reports into them — separate future work; the unmounted stack already exists.
- Anonymous feedback or any feedback path from the share-token controller (viewers are unauthenticated by design).
- Android app feedback UI (the JSON API shape is client-agnostic so Android can adopt later).
- Aggregate displays ("N users reported this") anywhere in the UI.
- Any coupling between feedback and the pipeline: status flips, auto-resolve on regeneration (ADR 0007).
- Free-text comments or notes on feedback.
- Capturing playback position with a report.
- Renaming the legacy `user_lrc_override` table to match the glossary.

## Further Notes

- The glossary term **Lyrics Feedback** and ADR 0007 (advisory-only) are already committed and pushed on the design branch (`f9311c01`); implementation builds on them.
- The state-aware validation matrix is the spec's heart: the client's chip filtering and the server's rejection logic must agree on the same lyrics-situation resolution the lyrics API already performs (canonical synced source → unsynced fallback → none). Keeping that resolution in one server-side place avoids drift between what the user sees and what the server accepts.
- Reason vocabulary is deliberately closed (four values) so admin triage stays mechanical; "other" is the escape hatch rather than a comment box.
- The suggested-action mapping in the admin queue is presentation only — it names the existing pipeline commands an admin would run; it does not invoke them.