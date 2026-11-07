# Mobile Lyrics Review

Status: confirmed and implemented at `/admin/lyrics-review`. The allowlisted
reviewer can open Lyrics Review from the avatar menu after signing in.

## Settled decisions

- Add a focused Lyrics Review screen to `delivery/webapp`, drawing on the Admin
  CLI review workflow.
- Use the existing webapp password login. Restrict the screen and its APIs to the
  verified accounts `mhuang74@yahoo.com` and the shared test account
  `tester@streamofworship.com` (the latter for manual QA and screenshots); show
  its navigation entry only to those accounts. A separate editor password is
  unnecessary.
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
- Prioritize unseen Songs in the default queue: exclude a Song if any user has
  Good or Poor Lyrics Feedback on any of its non-deleted Recordings, including
  resolved feedback and feedback on Published or Hold Recordings. Ignore feedback
  attached only to soft-deleted Recordings.
- Search and Show reviewed include reviewed Songs, with a song-level reviewed
  badge using that same rule. Keep the existing Review-only Recording selection
  and ranking; filter reviewed Songs before pagination without switching the
  selected Recording. No schema migration is needed.
- Saving feedback refreshes the queue while keeping the selected Recording and
  playback open until Next. Resolving feedback does not restore eligibility;
  retracting the last feedback row across non-deleted Recordings does, provided
  an eligible Review Recording remains. Personal feedback controls are unchanged.
- Highlight the current Lyrics line and always follow playback. Manual scrolling
  (wheel, touch, paging keys) suspends following, which resumes on its own about
  five seconds after the last manual scroll, or immediately when the reviewer
  seeks — by tapping a timestamped line or dragging the progress bar. There is no
  Follow playback button.
- Full Screen hides native `<audio>` chrome and renders one line (title,
  play/pause, `current / total` time, two-tap Exit) with a slim seek bar on the
  row below it, in a fixed overlay, so it works on iOS Safari where the
  Fullscreen Element API does not. The normal view uses only the native
  `<audio controls>` element — no custom slider or timecode. Leaving requires two
  taps: the first arms the Exit button (label Confirm exit) for three seconds,
  which guards against accidental taps. Feedback controls stay in the normal view.

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

## Implementation and validation

The review API rechecks the verified account and Recording visibility on each
request. Canonical Lyrics are fetched without the personal-override cache;
upstream failures return a retryable error. Feedback writes require a same-origin
request. The service worker uses NetworkOnly for admin pages and APIs.

The screen uses native audio controls plus a timestamped progress slider and
Lyrics list. It stops the global player on entry so two recordings cannot play
at once. UI labels support English and Traditional Chinese.

Postgres regression coverage checks song-level feedback exclusion across users,
ratings, resolution states and Recording visibility; soft deletion; inclusive
search and Show reviewed; deduplication, selection, ranking and pagination; and
eligibility after saving or retracting feedback. UI coverage checks that saving
refreshes the queue without interrupting playback or navigating until Next.

Automated coverage also checks account restrictions, Review-only access, canonical
fetch failures, Gap Placeholders, feedback validation and retraction, seeking,
manual Next (including pagination), and follow behavior. The live catalog query
and canonical R2 load were also checked with read-only calls. The configured test
account is not allowlisted; it was correctly denied the API and page. Mobile and
desktop layout checks use fixture data rather than that account's admin access.

Read-only acceptance on 2026-11-07 confirmed that 主我敬拜祢
(`zhu_wo_jing_bai_mi_6c3718ae`) has feedback on a non-deleted Recording, is absent
from all 330 default queue entries, and appears in search and Show reviewed with
`reviewed: true`.
