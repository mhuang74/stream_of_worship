# Mobile Lyrics Review

Status: design interview complete; awaiting confirmation before implementation.

## Settled decisions

- Add a focused Lyrics Review screen to `delivery/webapp`, drawing on the Admin
  CLI review workflow.
- Use the existing webapp password login. Restrict the screen and its APIs to the
  verified account `mhuang74@yahoo.com`; show its navigation entry only to that
  account. A separate editor password is unnecessary.
- Review the Recording's canonical Lyrics rather than a personal override. This
  screen has no Lyrics editing or canonical write operation.
- Support audio playback, visible Lyrics and timecodes, tapping a Lyrics line to
  seek to its timestamp, and dragging the playback progress bar to seek anywhere
  in the Recording.
- Collect Good/Poor Lyrics Feedback, with reasons for Poor feedback.
- Offer song search and a ranked review queue based on the CLI's prioritization,
  with recent unresolved Poor feedback taking precedence.
- Both search and the queue include only Recordings whose publication state is
  Review. Published and Hold Recordings are excluded from this review workflow.
- Reuse existing Lyrics Feedback: Good/Poor map to positive/negative, Poor takes
  exactly one reason (Lyrics missing, Timing wrong, Text wrong, Other) plus an
  optional note, and feedback can be changed or retracted.
- Allow Good only when canonical timed Lyrics exist. When they are absent, show
  that timed Lyrics are missing and allow Poor feedback. Distinguish loading
  failures from missing Lyrics and offer Retry for failures.
- Keep review separate from Lyrics editing, regeneration, forced alignment,
  publication changes, and feedback resolution.
- After feedback is successfully saved, keep the current Recording open and
  display Saved with an explicit Next action. Saving does not interrupt playback
  or immediately navigate away; the reviewer may finish listening or revise
  feedback before advancing.
- Hide Recordings with this reviewer's active feedback from the default queue.
  Offer Show reviewed to revisit them; search includes them regardless of the
  reviewer's feedback. Resolved or retracted feedback makes a Recording eligible
  for the default queue again, provided its publication state remains Review.
- Highlight the current Lyrics line and follow playback automatically. Manual
  scrolling pauses following; a Follow playback button resumes it. Tapping a
  timestamped line seeks to that time.

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

## Confirmation

All design questions are settled. Confirm the design above before implementing
the screen, access restrictions, canonical read path, queue, and feedback flow.
