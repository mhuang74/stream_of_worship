# Lyrics Feedback is advisory — it never mutates pipeline state

User-submitted Lyrics Feedback (happy/sad with an optional reason, one row per user per recording in the webapp DB) is an advisory signal for admins curating the canonical Lyrics. It never flips `recordings.lrc_status`, and regenerating LRC does not auto-resolve open feedback — an admin must judge whether the regeneration actually fixed the complaint (timing complaints are often about alignment, which regeneration may not improve). Resolution is an explicit admin action via `sow-admin lyrics feedback resolve`.

## Considered options

- Auto-resolve open feedback when `audio analyze lrc` regenerates — rejected: couples the pipeline to user opinion, and a regeneration can silently fail to fix a timing complaint, hiding unresolved pain.
- Auto-demote `lrc_status` on sad reports — rejected: `lrc_status` is pipeline state (pending/processing/completed/failed/missing); user opinion is a different axis.

## Consequences

- Admin triage is manual and stays in the Admin CLI (`lyrics feedback list/resolve`), not in the analysis pipeline.
- The webapp UI and the render worker need no changes to accommodate feedback beyond the feedback row itself.
- **Amendment (2026-10-24): resolution is visible to the reporting user.** This reverses the earlier decision in issue #194 (story 29) that a resolved row stays visible to the user who submitted it. Now `GET /api/lyrics/feedback/[recordingContentHash]` returns `null` once `resolved_at` is set, so the user's happy/sad icons revert to neutral on the next panel open. A later submission through `PUT` clears `resolved_at`, putting the row back in the admin triage queue (`lyrics feedback list` filters `resolved_at IS NULL`) — otherwise a re-report after resolution would be silently invisible to admins. Nothing else here changes: feedback remains advisory, never auto-resolves, and never touches `lrc_status`.