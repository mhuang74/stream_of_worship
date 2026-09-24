# Spec: Regenerate Lyrics from User Feedback

Status: ready-for-agent
Design session: grilled 2026-09-24 (8 questions, 3 rounds). Glossary: CONTEXT.md "Lyrics Feedback", "Lyrics", "Recording". Governing ADRs: docs/adr/0007-lyrics-feedback-is-advisory.md (feedback is advisory; regeneration never auto-resolves), docs/adr/0008-gap-placeholders-inserted-post-llm.md (one of the pipeline fixes motivating regeneration).

## Problem Statement

Users report broken Lyrics through Lyrics Feedback (CONTEXT.md): per-user happy/sad with a fixed reason vocabulary (`missing` | `timing` | `wrong_text` | `other`), stored unresolved in `lyrics_feedback` until an admin acts. Meanwhile the LRC generation pipeline has had several fixes that apply to *previously generated* Lyrics — a strict requirements block in the LLM correction prompt (`e9f14ef1`), a grounded zh worked example in the correction prompt (`5d08762a`), and gap placeholders for long instrumental passages (`0c7534b1`, ADR-0008). Recordings generated before these fixes still carry the old defects.

Today, re-fixing those recordings means hand-assembling the documented pipe

```
sow-admin lyrics feedback list --rating poor --format ids | sow-admin lyrics generate --force --stdin
```

and that pipe is broken in three ways:

1. **Wrong selection**: `--rating poor` matches recordings with *any* sad row — even fully resolved ones — as long as one open row of any rating exists (`lyrics.py` rating clause has no `resolved_at` filter; the `HAVING` only requires an open row). Resolved complaints get needlessly regenerated.
2. **Fire-and-forget**: `--wait` is rejected with `--stdin` (`lyrics.py:355-357`), so after submission nothing updates `recordings.r2_lrc_url` / `lrc_status='completed'` when jobs finish. The batch silently leaves pipeline state in `processing`.
3. **Ambiguous targeting**: feedback points at a Recording (`recording_content_hash`); `lyrics generate` submits by song_id via `get_recording_by_song_id`, a `SELECT ... WHERE song_id = %s` with no `ORDER BY` (`db/client.py:684-701`). For a multi-recording song the regeneration target is nondeterministic and may not be the recording users complained about. Additionally, `--format ids` emits a bare `hash_prefix` for songless recordings (`lyrics.py:247-253`), which `generate` cannot consume at all.

## Solution

Harden the existing pipe — no new command. The flow remains:

```
sow-admin lyrics feedback list --rating poor --format ids | sow-admin lyrics generate --force --stdin --wait
```

with four changes:

1. **Selection tightening** in `lyrics feedback list`: `--rating poor` yields exactly "recordings with ≥1 open sad row" (any reason — all sad reasons qualify; sad wins over happy, so mixed recordings are included). The `--format ids` hash_prefix fallback is removed; songless recordings are reported to stderr as skipped instead of emitted.
2. **Pre-flight target guard** in `lyrics generate --stdin`: for each piped song id, the command queries `lyrics_feedback` itself (it already holds a `ConnectionProvider`) for the open-sad `recording_content_hash` values attached to that song's recordings, performs the exact `get_recording_by_song_id` lookup the submission will use, and compares the target's `content_hash`. No open-sad feedback hash matches the target → the song is *skipped* and listed in the run report (never silently regenerating a recording users didn't complain about). `--format ids` stays single-column; no pipe-format change.
3. **Batch wait mode**: `--wait` becomes valid with `--stdin`. After all jobs are submitted, the command polls every job to completion reusing the `audio batch` machinery (the unified interleaved poll loop `_process_batch` / `_poll_one_cycle`, service-first + R2-fallback reconciliation — see `specs/batch-unified-poll-loop-v2.md`), then reconciles `recordings` (`r2_lrc_url`, `lrc_status='completed'`, `lrc_source`) per recording. A run manifest (`{batch_id}_manifest.json` convention) records song ids, job ids, and outcomes; `--resume <manifest>` re-polls an interrupted run (via `_resume_from_manifest`, `audio.py:8018`); Ctrl+C triggers the same `_reconcile_on_interrupt` path as `audio batch`.
4. **End-of-run report**: per-recording outcome (completed / failed / skipped-guard / skipped-songless), failures with job ids, and the exact `sow-admin lyrics feedback resolve <song-id>` commands for the recordings the admin judges fixed. Nothing is auto-resolved (ADR-0007).

Regeneration uses plain `--force` only. The fixed bugs are all downstream of transcription (LLM correction prompt + post-LLM gap placeholders), and the cache structure guarantees they take effect: the YouTube transcript path re-fetches and re-runs LLM correction on every job (`youtube_transcript.py:1065`; no transcription cache), the LRC result cache is bypassed by `options.force` (`queue.py:1416-1420`), and the Qwen3 ASR cache keys include a cache-version constant so prompt/context changes invalidate automatically (`workers/lrc.py:718-740`). `--force-qwen3-asr` (paid re-transcription) is **not** part of this flow.

## User Stories

1. As an admin, I want one pipe that regenerates every recording users are currently complaining about, so that fixed pipeline bugs reach old recordings without hand-built song lists.
2. As an admin, I want `--rating poor` to mean "has unresolved negative feedback", so that I never spend regeneration cost on recordings whose complaints were already resolved.
3. As an admin, I want all sad reasons to qualify (missing, timing, wrong_text, other), so that prompt and gap-placeholder fixes reach every complaint class they might repair.
4. As an admin, I want recordings with both happy and sad open feedback to be regenerated (sad wins), so that one dissatisfied user's report isn't vetoed by another's praise.
5. As an admin, I want songless recordings and multi-recording-song mismatches skipped and reported — never silently targeted — so that the run report is trustworthy.
6. As an admin, I want `--wait` to work in batch mode, so that `recordings.lrc_status` / `r2_lrc_url` reflect reality when the run ends instead of being stuck at `processing`.
7. As an admin, I want a run manifest, so that I can audit what was attempted and resume after interruption.
8. As an admin, I want Ctrl+C to leave pipeline state consistent, so that a cancelled run doesn't strand recordings.
9. As an admin, I want `--dry-run` to print the selection and guard results without submitting, so that I can review scope before spending API quota.
10. As an admin, I want completed regenerations demoted to `visibility_status='review'`, so that the human-verified checkmark is withdrawn until I spot-check (review recordings remain visible to users; only the badge is affected).
11. As an admin, I want the run report to print the exact `lyrics feedback resolve` commands per fixed recording, so that resolution stays my explicit judgment (ADR-0007) with zero lookup friction.
12. As an admin, I want unresolved feedback to be regenerated again on the next run, so that future pipeline fixes automatically retry still-broken recordings (runbook discipline: resolve after review).
13. As a worship participant, I want the canonical Lyrics of a song I reported to improve without the song disappearing, so that my next session benefits.

## Implementation Decisions

- **No new command, no schema change, no new glossary terms, no new ADR.** The feature is a hardening of `lyrics feedback list` + `lyrics generate`; ADR-0007/0008 already govern the surprising decisions.
- **Selection tightening (`lyrics feedback list`)**: the `--rating` clause gains the open-only filter (`f.resolved_at IS NULL AND f.rating = %(rating)s`), and when `--rating poor` is given the `HAVING` requires `COUNT(*) FILTER (WHERE f.resolved_at IS NULL AND f.rating = 'sad') > 0`. Table output is unchanged otherwise. Behavior change: recordings whose sad rows are all resolved but which have an open happy row drop out of `--rating poor` — that is the intent.
- **`--format ids` fallback removal**: rows whose recording has no `song_id` are no longer emitted as bare `hash_prefix`; they are collected and printed to stderr as a skipped list (Rich stderr console), keeping stdout a clean, fully-consumable id stream.
- **Feedback-aware pre-flight guard (DB-lookup, not pipe-format)**: in `lyrics generate --stdin`, each piped song id is checked before submission: query `lyrics_feedback` (joined via `recordings.song_id`) for the open-sad `recording_content_hash` values on that song, run the identical `get_recording_by_song_id` lookup `submit_lrc_single` will use, and compare the returned recording's `content_hash` against the feedback hashes. No match → skip the song and record it as `skipped-guard` (with both the target hash and the feedback hashes) in the report. A song id with no open-sad feedback at all (e.g. piped from somewhere other than `feedback list`) passes the guard unconditionally — the guard only vetoes *mismatched* targeting, it does not police where ids came from. Rationale for skip-over-warn: the lookup has no `ORDER BY`, so a warned-but-submitted run could regenerate a different recording's Lyrics while the report reads clean. Pinning `ORDER BY imported_at DESC` is rejected — the feedback's recording is not necessarily the newest. An earlier draft of this spec carried the content hash through a two-column `--format ids` output; that was dropped because `read_song_ids_from_stdin` (`services/prompts.py:14-25`) and its duplicate in `catalog.py:451` append whole stripped lines without whitespace tokenization, so a second column would corrupt every existing pipe consumer (`audio.py` download/delete/set-visibility/sync-components/batch, `lyrics.py` generate/preview paths).
- **Batch `--wait`**: lift the `--stdin` + `--wait` rejection (`lyrics.py:355-357`). After `submit_lrc_batch` returns, poll all submitted job ids via the `audio batch` unified poll machinery — `_process_batch` (`audio.py:7627`) driving `_poll_one_cycle` (`audio.py:7410`), service-first + R2-fallback reconciliation per `specs/batch-unified-poll-loop-v2.md`. (The earlier phase-barrier `_poll_all_jobs` no longer exists; it was replaced by this interleaved loop.) Do not use the single-song `wait_for_completion` (its 600s timeout is per-job and wrong for a batch). On per-job completion: `update_recording_lrc(hash_prefix, r2_lrc_url=..., visibility_status='review', lrc_source=...)` — identical to single-song waited completion (`lrc_jobs.py:182-189`). On failure: `lrc_status='failed'`, recorded with the job id in the manifest and report. The Analysis Service serializes YouTube transcript calls (max 1 concurrent, ≥3s interval) and caps Qwen3 ASR at 2 concurrent, so large batches are slow; the poll loop must have no fixed overall timeout (poll until all jobs settle, like `audio batch`).
- **Manifest and resume**: same `{batch_id}_manifest.json` convention as `audio batch` (spec `scale-key-bpm-analysis-batch-v4.md`): song ids, content hashes, job ids, per-job outcome, started/finished timestamps. `--resume <manifest>` re-attaches to the recorded job ids and re-enters the poll loop without resubmitting. Ctrl+C runs `_reconcile_on_interrupt` semantics: jobs already submitted stay valid server-side (WAITING-status recovery per `waiting-status-for-fire-and-forget-jobs-v1.md`); the manifest is flushed before exit so `--resume` can finish the run.
- **`--dry-run`**: performs selection and the pre-flight guard, prints the would-be submission set plus skips, submits nothing, writes no manifest.
- **Visibility demotion**: batch completion demotes `visibility_status` to `'review'`, matching single-song waited mode. User-confirmed harmless: review recordings remain visible to end users; `'published'` only adds a human-verified checkmark badge, which is correct to withdraw pending spot-check.
- **Cache depth**: submit with `force=True` only. Do not pass `--force-qwen3-asr` / `--no-whisper-cache` in this flow; the motivating fixes (`e9f14ef1`, `5d08762a`, `0c7534b1`) are downstream of transcription and plain `--force` picks them up (see Solution). `--force-qwen3-asr` remains a manual per-song escalation for admins who suspect a bad cached ASR transcription.
- **Idempotency is runbook discipline**: no persisted dedup state (no `regenerated_at` column, no runs table). Unresolved feedback is deliberately re-regenerated on the next run — a free retry after future pipeline fixes. Admins resolve via `lyrics feedback resolve` after reviewing a run (ADR-0007).
- **No auto-resolve, no pipeline-state coupling from feedback**: selection reads `lyrics_feedback`; nothing ever writes `resolved_at` except `lyrics feedback resolve/unresolve`, and feedback never mutates `recordings.lrc_status` (ADR-0007).
- **Overwrite safety is inherited**: regeneration overwrites `{hash_prefix}/lyrics.lrc` via `upload_official_lrc`, which backs up the previous object (5 retained) with ETag stale protection (`r2.py:349-445`). No additional rollback machinery in this spec.

## Testing Decisions

- **Good tests assert external behavior only**: CLI stdout/stderr and database end-state given a real (testcontainers) Postgres and a mocked AnalysisClient. No assertions on SQL text or internal call wiring.
- **Seam 1 — `lyrics feedback list` selection (admin CLI integration, `CliRunner` + testcontainers; prior art `tests/admin/test_audio_commands.py`)**: seed feedback rows covering the matrix — open sad only; resolved sad + open happy (must drop out of `--rating poor`); open happy only; open sad + open happy (included, sad wins); per-reason coverage including `timing` and `other`. Assert `--format ids` stdout contains exactly the qualifying song ids, and songless recordings appear only in the stderr skip list.
- **Seam 2 — pre-flight guard**: seed a song whose feedback's `recording_content_hash` belongs to a different recording than the one generation would target; assert the song is skipped, appears in the report as `skipped-guard`, and no job is submitted for it. A song whose target recording matches an open-sad feedback hash → submitted normally. A piped song id with no feedback rows at all → passes the guard.
- **Seam 3 — batch `--wait` end-state**: mocked AnalysisClient driving jobs through pending → completed/failed; assert `recordings` rows end at `lrc_status='completed'` + `r2_lrc_url` + `lrc_source` + `visibility_status='review'` for successes, `lrc_status='failed'` for failures, manifest contents record job ids and outcomes, and `--resume` re-polls without resubmitting. Assert `lyrics_feedback.resolved_at` is untouched throughout.
- **Seam 4 — flag validation**: `--wait` accepted with `--stdin`; `--dry-run` submits nothing and writes no manifest.
- No webapp or analysis-service changes exist in this spec, so no vitest or service-side tests.

## Out of Scope

- Scheduling (cron / CI) or event-driven triggering — the run is admin-invoked (ADR-0007: triage stays in the Admin CLI).
- Sweeping `lrc_status='failed'` or missing-Lyrics recordings without feedback — already served by `audio batch --lrc-status failed --lrc --force`.
- Automating the `timing`→re-align or `wrong_text`/`other`→manual-review actions from the triage queue's suggested-action mapping; this spec automates regeneration only, and all sad reasons are eligible because the shipped fixes plausibly repair more than `missing`.
- Persisted regeneration bookkeeping (dedicated runs table or `lyrics_feedback` columns) — runbook discipline chosen in grilling; revisit only if triage discipline lapses in practice.
- `--force-qwen3-asr` / `--no-whisper-cache` in the default flow (paid re-transcription; manual escalation only).
- Content-hash-targeted submission (regenerating a specific recording of a multi-recording song). Mismatches are skipped and reported instead; a content-hash submit path is future work if the catalog grows real multi-recording songs.
- Webapp, Android, render-worker, and analysis-service changes.
- CONTEXT.md glossary additions and new ADRs (nothing here is a new domain term or a hard-to-reverse trade-off beyond what ADR-0007/0008 already record).

## Further Notes

- The sibling spec `specs/collect_user_lyrics_feedback.md` owns the collection half of this feature and the house format this spec follows. Its suggested-action mapping is presentation-only; this spec is the first consumer to *act* on a mapping, and deliberately broadens it (all sad reasons regenerate) because the motivating fixes span the correction prompt and gap placeholders, not just missing-Lyrics generation.
- Motivating fixes and why plain `--force` suffices: `e9f14ef1` (strict requirements block in the LLM correction prompt), `5d08762a` (grounded zh worked example in the correction prompt), `0c7534b1` (gap placeholders, YouTube path, ADR-0008). The YouTube path re-fetches the transcript and re-runs correction on every job (`youtube_transcript.py:1065`); the LRC result cache is keyed on content_hash + lyrics + language with no prompt version and is bypassed by `options.force` (`queue.py:1416-1420`); the Qwen3 ASR cache self-invalidates via `SOW_DASHSCOPE_ASR_CACHE_VERSION` (`workers/lrc.py:718-740`).
- Throughput expectation: YouTube transcript fetching is serialized (1 concurrent, ≥3s apart) and Qwen3 ASR is capped at 2 concurrent, so a large feedback queue produces a long-running batch. The manifest + `--resume` + Ctrl+C reconciliation exist precisely so a multi-hour run is survivable; do not add concurrency controls in this spec.
- Pipe-format stability: `--format ids` stays one song id per line. The guard gets its content hashes from a direct `lyrics_feedback` query inside `lyrics generate`, not from the pipe, because the shared stdin readers (`services/prompts.py:14-25`, `catalog.py:451`) treat each whole line as an id — any extra column would be handed to the analysis service as a bogus song id across six existing call sites.
