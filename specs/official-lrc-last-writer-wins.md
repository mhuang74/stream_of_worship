# Official LRC Last-Writer-Wins

## Summary

- Make `{hash_prefix}/lyrics.lrc` the single official LRC object.
- Both regular LRC jobs and ForcedAlignment jobs overwrite this same official object.
- Before overwriting, copy the current official object to
  `{hash_prefix}/lyrics.backup.{unix_timestamp}.lrc`.
- Stop creating new `lyrics.{lang}.forced.lrc` and `lyrics.{lang}.v2.lrc` artifacts.
- Leave historical `.forced.lrc` and `.v2.lrc` objects in R2 untouched.

## Key Changes

- Add a shared analysis-service upload helper for official LRC writes:
  - Check `s3://{bucket}/{hash_prefix}/lyrics.lrc`.
  - If it exists, copy it to `s3://{bucket}/{hash_prefix}/lyrics.backup.{timestamp}.lrc`.
  - Upload the new LRC file to `lyrics.lrc`.
  - Return `s3://{bucket}/{hash_prefix}/lyrics.lrc`.
- Use this helper from both `_process_lrc_job()` and `_process_forced_alignment_job()`.
- ForcedAlignment result should set:
  - `JobResult.lrc_url = s3://{bucket}/{hash_prefix}/lyrics.lrc`
  - `JobResult.lrc_source = "forced_alignment"`
- Regular LRC result should also return `lyrics.lrc`, preserving its existing `lrc_source`.

## Consumers

- Admin CLI continues storing `final_job.result.lrc_url` in `recordings.r2_lrc_url`; after
  this change it will be the official `lyrics.lrc`.
- Web Lyrics Pullup Screen should read the official `lyrics.lrc` path and must not treat
  ForcedAlignment output as a `user_lrc_override`.
- Lyrics Renderer remains unchanged because it already fetches `{hash_prefix}/lyrics.lrc`.
- Web user override APIs remain separate and are out of scope for this change.

## Cache Behavior

- Because `lyrics.lrc` is mutable and last-writer-wins, cached LRC job metadata must not
  complete a job unless the official object is actually rewritten.
- Store generated LRC text in the LRC cache going forward.
- On cache hit with cached text, write that text to a temp file, backup existing
  `lyrics.lrc`, upload official `lyrics.lrc`, and complete the job.
- Ignore older metadata-only LRC cache entries and regenerate.

## Test Plan

- ForcedAlignment uploads only `lyrics.lrc`, returns `.../lyrics.lrc`, and does not upload
  `.forced.lrc`.
- ForcedAlignment backs up existing `lyrics.lrc` before overwrite.
- Regular LRC uploads only `lyrics.lrc`, returns `.../lyrics.lrc`, and does not upload
  `.v2.lrc`.
- Regular LRC cache hit with cached text rewrites official `lyrics.lrc`.
- Metadata-only legacy LRC cache entry is ignored.
- Existing admin, web signed-url, and render-worker tests continue asserting
  `{hash_prefix}/lyrics.lrc`.

## Assumptions

- Official LRC means the shared R2 object `{hash_prefix}/lyrics.lrc`.
- Last completed LRC-producing job wins, whether regular LRC or ForcedAlignment.
- Backups are always copies of the previous official `lyrics.lrc`.
- Web official-LRC editing authorization is out of scope.
