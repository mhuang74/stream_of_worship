# Mobile Lyrics Review

Status: design interview in progress. The settled decisions below describe the
requested behavior; implementation awaits confirmation of the complete design.

## Settled decisions

- Add a focused Lyrics Review screen to `delivery/webapp`, drawing on the Admin
  CLI review workflow.
- Use the existing webapp password login. Restrict the screen and its APIs to the
  verified account `mhuang74@yahoo.com`; show its navigation entry only to that
  account. A separate editor password is unnecessary.
- Review the Recording's canonical Lyrics rather than a personal override.
  Although canonical corrections were initially selected, the subsequent scope
  is review only: this screen has no Lyrics editing or canonical write operation.
- Support audio playback, visible Lyrics and timecodes, tapping a Lyrics line to
  seek to its timestamp, and dragging the playback progress bar to seek anywhere
  in the Recording.
- Collect Good/Poor Lyrics Feedback, with reasons for Poor feedback.
- Keep review separate from Lyrics editing, regeneration, forced alignment,
  publication changes, and feedback resolution.

## Existing behavior that informs the design

- Lyrics belong to a Recording; a Song may have multiple Recordings.
- Canonical Lyrics are stored in R2. Existing web editors save per-user overrides;
  those overrides do not correct canonical Lyrics or rendered videos.
- Existing Lyrics Feedback is one retractable, switchable rating per user per
  Recording. It remains advisory under [ADR-0007](adr/0007-lyrics-feedback-is-advisory.md).
- Timestamped blank Lyrics rows are intentional Gap Placeholders and must be
  preserved rather than discarded. See
  [ADR-0008](adr/0008-gap-placeholders-inserted-post-llm.md).
- The CLI offers ranked and fresh review queues. Its fresh queue means a
  Recording has no feedback rows of any kind, including resolved feedback;
  it does not mean the current reviewer has never reviewed it.

## Open decisions

- How to select the next Recording: search, review queues, or both; which
  publication states are included.
- Whether to reuse the existing per-user Lyrics Feedback semantics unchanged,
  including its fixed reasons and retraction behavior.
- Queue advancement and return behavior after feedback is saved.

Record additional decisions here as the interview settles them. Add an ADR only
if a decision meets the repository's architectural-decision criteria.
