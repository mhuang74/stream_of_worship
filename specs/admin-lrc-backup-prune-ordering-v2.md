# Admin / Analysis LRC Backup Prune Ordering — v2

Supersedes `specs/admin-lrc-backup-prune-ordering-v1.md` (v1 is left untouched).

v1 is directionally correct: the prune must order by R2 `LastModified`, not by the
integer embedded in the filename, and the analysis-service must stop minting
monotonic event-loop timestamps. v2 keeps that core, pins the parts v1 left
underspecified, and corrects one v1 instruction that would have reintroduced the
same class of ordering bug. Nothing here is implemented yet.

## Review verdict on v1

Sound and verified against the tree:

- Line references are accurate. `admin/.../services/r2.py:709-738` and
  `sow_analysis/storage/r2.py:419-442` are the duplicated prune blocks; the
  constants are at `admin r2.py:21` and `analysis r2.py:23` (both `5`).
- `analysis-service`'s `list_objects()` (`r2.py:317-336`) does drop
  `LastModified`, and its **only** caller is the prune block at `r2.py:422`
  (verified by grep across `src/` and `tests/`), so extending its return shape
  is a two-site change, not an API break.
- The vendoring precedent is real: `sow_analysis/workers/lrc_parser.py:1-13`.
- The dev-only dependency claim is real: `analysis-service/pyproject.toml:36`
  lists `stream-of-worship` in `dev` (source at `[tool.uv.sources]`), the
  Dockerfile installs `-e ".[service]"` (`Dockerfile:44`), and pytest already
  puts `../admin-cli/src` on `pythonpath` — so a parity test can import both
  copies, while production cannot import the admin copy.

Problems in v1 that v2 fixes:

| # | v1 issue | Severity | v2 resolution |
|---|---|---|---|
| 1 | "Normalize (`.astimezone(timezone.utc)`)" for **naive** datetimes is wrong: `astimezone` on a naive value assumes *local* time and shifts it by the host's UTC offset — the exact reordering v1 warns about, applied by its own prescribed code. | High (correctness of ordering) | Naive → `replace(tzinfo=timezone.utc)`; aware → `astimezone(timezone.utc)`. Normalization moves *inside* the pure selector. |
| 2 | Collision disambiguation is a menu ("retry with the next millisecond, **or** append a short suffix"). Unimplementable as written. | High (silent overwrite path stays open) | Pinned: bump `timestamp_ms` by 1, up to 10 attempts, then `BackupFailedError`. No suffix format. |
| 3 | Deletion order is never specified; only the delete *set* is. | High (requirement: oldest first; also affects crash/failure outcome) | Delete calls are issued strictly oldest→newest by `LastModified`. Acceptance criterion. |
| 4 | One failed `delete_object` aborts the loop and is swallowed by the outer `except Exception` — retention silently stalls. | Medium | Per-key `try/except`, log, continue; prune still never fails the upload. |
| 5 | Key format is described prose-only; no single source of truth for the expression. | Medium | One canonical expression stated once; both writers must match byte-for-byte (`{hash_prefix}/lyrics.backup.{epoch_ms}.lrc`), asserted by a test. |
| 6 | Normalization is split between caller ("when building the list") and comparator; the parity test would then compare only the comparator. | Medium | Selector is a total function: it accepts raw `datetime` values (naive or aware) and normalizes itself. |
| 7 | `keep = max_backups - len(protected)` is fragile (depends on the protected key appearing exactly once; awkward at `max_backups = 0`). | Low | Compute `delete_count` from the total, then skip the protected key while walking oldest→newest. |
| 8 | Silent overwrite is listed as "reduce but not eliminate"; residual TOCTOU window is not named. | Low | Explicitly documented as accepted residual risk (see "Accepted residual risks"). |

## Root cause (unchanged from v1, re-verified)

`upload_official_lrc()` prunes `{hash_prefix}/lyrics.backup.*.lrc` down to
`MAX_BACKUPS_PER_PREFIX = 5` using the integer parsed out of the filename:

| Writer | Timestamp source | Magnitude |
|---|---|---|
| `ops/admin-cli/.../services/r2.py:698` | `int(time.time() * 1000)` — epoch ms | ~1.79e12 |
| `ops/analysis-service/.../storage/r2.py:405` | `int(asyncio.get_event_loop().time() * 1000)` — event-loop monotonic uptime ms | ~3.5e8 |

Monotonic values are always numerically smaller than epoch values, so **every
analysis-service backup sorts below every admin-cli backup**. Prune keeps the 5
numerically largest keys, deletes the rest newest-last, and both `LastModified`
and the meaning of "oldest" are discarded. The analysis-service monotonic clock
also resets on every container restart, so a restarted service mints small keys
that can collide with existing backup names; `copy_object` to an existing key
overwrites silently.

Observed consequence (song `fan_ruo_yi_kao_ye_he_hua_05a20f2f`, prefix
`c77bd1687dcd`): a manually corrected LRC (1329 B, md5 `4c4fbdb2…`) survives
only as `lyrics.backup.357735493.lrc`, written 2026-10-22T23:27:44Z. Four other
backups exist, three of them older admin-cli/editor snapshots. The set is at the
cap, so the next LRC write deletes the manual snapshot and keeps three generated
ones. Ordering by `LastModified` would delete
`lyrics.backup.1792710546483.lrc` (23:09:05Z) instead.

## Design

### 1. One key format, wall-clock, identical in both writers

Both writers MUST generate backup names with exactly this expression:

```python
timestamp_ms = int(time.time() * 1000)          # Unix epoch ms, UTC-based
backup_key = f"{hash_prefix}/lyrics.backup.{timestamp_ms}.lrc"
```

- Canonical source of truth: `ops/admin-cli/src/stream_of_worship/admin/services/r2.py`
  (`upload_official_lrc`, step 3). The analysis-service copy MUST use the same
  expression verbatim; its docstring names the admin path.
- Delete `asyncio.get_event_loop().time()` from
  `sow_analysis/storage/r2.py:405` and add `import time`.
- Cross-host clock skew affects only the filename (forensic display). Ordering
  is decided by R2 `LastModified`, so skew cannot reorder retention.
- Legacy monotonic-scale keys already on R2 remain readable and correctly
  ordered; no rename, no backfill.

### 2. Collision guard: bump milliseconds, then fail

Only when a backup is actually created (`exists and not skip_backup`), before
`copy_object`:

```python
base_ms = int(time.time() * 1000)
for attempt in range(10):
    backup_key = f"{hash_prefix}/lyrics.backup.{base_ms + attempt}.lrc"
    try:
        head(Bucket=..., Key=backup_key)        # admin: self._client.head_object
    except ClientError as e:                    # analysis: await self.head_object(...)
        if _is_not_found(e):                    # codes "404", "NoSuchKey"
            break
        raise BackupFailedError(f"Failed to verify backup name {backup_key}: {e}")
else:
    raise BackupFailedError(
        f"Could not find a free backup key near {base_ms} for {hash_prefix}"
    )
# then copy_object into backup_key
```

Rules:

- A pre-existing key is never overwritten: the loop only proceeds on a confirmed
  not-found.
- **Each attempt re-probes its own candidate key with a fresh HEAD.** The
  not-found result is never cached or reused across attempts; the loop breaks
  only after the *current* candidate returns not-found.
- A HEAD failure that is **not** not-found (403, 5xx, network) is treated as
  failure and raises `BackupFailedError` — never "assume free". Only a
  `ClientError` whose code is `404` or `NoSuchKey` counts as free.
- `BackupFailedError` is already handled by every caller
  (`admin/commands/lyrics.py:2081`, `analysis/workers/queue.py:1928,2222`), so
  no call-site changes are needed there.
- The winning `backup_key` is remembered and passed to the prune as
  `protected_key` (or `None` when no backup was created).

### 3. Prune by `LastModified`, deleting strictly oldest first

Both prune blocks switch to a shared pure selector. Canonical copy lives in
`admin/services/r2.py` at module level; the analysis-service gets a
byte-identical vendored copy with a provenance docstring (same pattern as
`workers/lrc_parser.py`).

```python
def select_backups_to_delete(
    objects: list[tuple[str, datetime]],   # (key, LastModified) as returned by boto3
    protected_key: Optional[str],          # backup created in this call, else None
    max_backups: int,
) -> list[str]:
    """Return keys to delete, oldest LastModified first."""
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:                 # naive: assume UTC, do NOT shift
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    ordered = sorted(((_utc(ts), key) for key, ts in objects), key=lambda o: (o[0], o[1]))
    delete_count = max(0, len(ordered) - max(0, max_backups))
    to_delete: list[str] = []
    for _, key in ordered:                       # oldest → newest
        if len(to_delete) >= delete_count:
            break
        if key == protected_key:
            continue
        to_delete.append(key)
    return to_delete
```

Invariants:

- **Ordering key is `LastModified` only.** Ties break on key string, so the
  result is deterministic and independent of listing order. Because
  `datetime.timestamp()` applies the same naive-means-local assumption,
  comparison is only safe after the `_utc()` normalization above.
- **Oldest first, in the returned order.** Callers MUST iterate the returned
  list as-is. If the process dies mid-prune, the surviving set is biased toward
  recent backups, which is the safe direction.
- **The new backup is never deleted.** `protected_key` is skipped wherever it
  appears, covering same-second ties and a backwards wall clock. It also means
  the arithmetic stays correct when the protected key is the oldest object.
- **Post-prune count ≤ `max_backups`.** `delete_count` is derived from the total
  object count, so the protected key consumes one of the retained slots rather
  than being kept on top of the cap.
- **No filename parsing anywhere.** Mixed-clock and unparsable names
  (`lyrics.backup.manual.lrc`) are ordinary candidates ordered by
  `LastModified`; nothing is pinned first.
- **Pure:** no boto3, no logging, no I/O. Vendoring is a copy, and the parity
  test compares logic rather than transport.

Per-call flow (both clients):

1. Backup (with collision guard) if `lyrics.lrc` exists and backup is enabled;
   record `backup_key`.
2. List `{hash_prefix}/lyrics.backup.` (paginated), keep entries whose key
   starts with the prefix and ends with `.lrc`, capturing `(Key, LastModified)`.
   Admin keeps using the paginator; analysis `list_objects()` returns
   `list[tuple[str, datetime]]` instead of `list[str]` (sole caller, changed
   together; update its docstring).
3. `to_delete = select_backups_to_delete(objects, backup_key, MAX_BACKUPS_PER_PREFIX)`.
4. For each key in order: `delete_object`; on per-key failure log a warning and
   continue with the next key.

### 4. Prune failures are logged, never fatal

Replace both `except Exception: pass` blocks with `except Exception as exc:` +
`logger.warning(...)`, carrying `hash_prefix`, `scanned`, `deleted`, `failed`,
`error`. `deleted`/`failed` count what actually happened (partial deletes are
possible now that a single failure does not abort the loop). Prune remains
non-fatal: an LRC upload must not fail because cleanup failed. Use
`logging.getLogger(__name__)` in both modules; admin CLI already configures
logging via `admin/logging_config.py`, and the analysis service runs under
uvicorn's root logger.

### 5. Constants

`MAX_BACKUPS_PER_PREFIX = 5` stays in both modules (values must match). Each
docstring names the other file path so a future change is grepped, not
forgotten. Removing the duplication is out of scope (see "Vendored duplicate").

### Vendored duplicate, with a parity test

- `analysis-service/pyproject.toml` declares `stream-of-worship` only in `dev`,
  the image installs `.[service]` (`Dockerfile:44`), and pytest already has
  `../admin-cli/src` on `pythonpath` — so runtime import is impossible and
  test-time cross-import is already supported.
- The vendored copy carries a docstring naming
  `ops/admin-cli/src/stream_of_worship/admin/services/r2.py` as source of truth,
  mirroring `workers/lrc_parser.py:1-13`.
- Parity test asserts identical output for every case table row.

## Files to Change

| File | Change |
|---|---|
| `ops/admin-cli/src/stream_of_worship/admin/services/r2.py` | Add module-level `select_backups_to_delete()`; rewrite the prune block (`:709-738`) to collect `(Key, LastModified)`, call the selector, and delete oldest-first with per-key logging; add the 10-attempt collision guard before `copy_object` (`:702`); keep `timestamp_ms` epoch ms; add `logging`; update `upload_official_lrc` docstring steps 3 and 5 and the `MAX_BACKUPS_PER_PREFIX` comment |
| `ops/analysis-service/src/sow_analysis/storage/r2.py` | Add byte-identical vendored `select_backups_to_delete()` + provenance docstring; change `:405` to `int(time.time() * 1000)`; add collision guard before `copy_object` (`:409`); rewrite prune (`:419-442`); `list_objects()` returns `list[tuple[str, datetime]]`; `import time`, `import logging`; update docstrings |
| `ops/admin-cli/tests/admin/test_r2.py` | Rewrite `test_prunes_old_backups` (`:896`) fixture with `LastModified`; add mixed-clock regression, tie, unparsable-key, protected-key, below-cap, listing-failure (`caplog`), per-key delete failure, collision-guard and key-format tests; add direct selector unit tests |
| `ops/analysis-service/tests/integration/test_r2.py` | Same rewrite for `test_upload_official_lrc_prunes_old_backups` (`:245`); assert epoch-ms key naming under patched `time.time`; assert `list_objects()` shape |
| `ops/analysis-service/tests/test_r2_prune_parity.py` (new) | Import both `select_backups_to_delete` copies, assert identical output across the full case table (mixed clocks, ties, naive datetimes, unparsable keys, protected key, at-cap, over-cap, `max_backups=0`) |

`ops/admin-cli/build/lib/...` contains stale build copies of the same modules; do not
edit them (they are regenerated artifacts and their grep hits are noise).

Existing prune tests encode the buggy behaviour (six synthetic keys, no
`LastModified`) and must be rewritten, not preserved. Note that the delete
assertion changes shape with the collision guard: the backup key is now minted
after a HEAD probe, so fixtures must stub `head_object` for the backup key with
a not-found `ClientError` and set the paginator's `LastModified` values.

## Tests

The shipped incident, reproduced with realistic magnitudes
(`LastModified` ascending; cap 5):

```
1792710546483  23:09:05Z   (admin epoch — OLDEST)
1792711476891  23:24:35Z
357735493      23:27:44Z   (analysis monotonic — the manual correction)
358288869      23:36:58Z
358431311      23:39:20Z
<new>          23:44:00Z   (just created, protected)
```

Expected: exactly `…/lyrics.backup.1792710546483.lrc` deleted. Pre-fix code
deletes `357735493` (numerically smallest). The assertion that matters is that
the manual snapshot survives.

Additional cases:

1. **Unparsable key** (`lyrics.backup.manual.lrc`): retained when recent,
   deleted when oldest by `LastModified`. Proves `_extract_ts → 0` pinning is
   gone.
2. **Tie**: two keys with identical `LastModified` → exactly one deletion,
   decided by key tiebreak; result identical when the listing order is reversed.
3. **Protected key is oldest**: never deleted even with a backwards clock; the
   next-oldest object is deleted instead, and the post-prune count is still
   ≤ 5.
4. **Naive `LastModified`**: `datetime(...)` without tzinfo and a tz-aware value
   one minute older — naive is treated as UTC (no local-offset shift), so the
   aware (older) object is deleted. Fails if `astimezone` is used naively.
5. **Below cap** (exactly 5 objects, none protected) → zero deletes.
6. **At cap with new backup** (5 existing + protected new) → exactly the oldest
   existing key deleted.
7. **Listing failure** → upload still returns the official URL; warning logged
   (`caplog`); no deletes attempted.
8. **Per-key delete failure** → remaining keys still deleted, each failure
   logged, upload still returns.
9. **Collision guard** → after 10 consecutive occupied keys, `BackupFailedError`
   is raised and `copy_object` is never called; with the third key free,
   `copy_object` is called with `base_ms + 2` and no occupied key is used.
10. **Key format** → with `time.time` patched, both clients write
    `{hash_prefix}/lyrics.backup.{int(fake * 1000)}.lrc` (compatibility of the
    two writers, asserted in both suites).
11. **Parity** (analysis-service suite) → both `select_backups_to_delete`
    copies return identical, oldest-first lists for every case above.

No live-R2 test in CI: mocks cover the decision logic, which is where the bug
is. The live check is the non-destructive smoke below.

## Manual Verification (non-destructive)

Never touch `c77bd1687dcd` for verification, and never overwrite a real song's
`lyrics.lrc`.

1. **Scratch prefix.** Seed `deadbeef0000/lyrics.lrc` plus five
   `deadbeef0000/lyrics.backup.*.lrc` objects with deliberately shuffled
   `LastModified` values and mixed filename styles (two epoch-ms, two
   monotonic-scale, one unparsable). Run one `upload_official_lrc` against it
   (both clients). Expected: exactly the oldest-`LastModified` key is gone, the
   unparsable-but-recent key survives, the new backup is present, total = 5.
   Delete the prefix afterwards. This exercises pagination, real `LastModified`
   values, and the real delete path.
2. Record the deletion request order (boto3 debug log or an S3 access log) and
   confirm it is oldest → newest.
3. **Dry run** is optional and never sufficient on its own: compute the delete
   set, print it, perform no `copy_object` / `upload_file` / `delete_object`.

## Migration / Operational Note

No backfill, no renames. The selector ignores the filename integer, so legacy
monotonic keys (`357735493`) and epoch keys (`1792710546483`) order correctly by
`LastModified`.

After this ships, the next LRC write to `c77bd1687dcd` deletes
`lyrics.backup.1792710546483.lrc` (23:09:05Z) and retains
`lyrics.backup.357735493.lrc` (23:27:44Z, the manual correction).

**Until this ships, any LRC write to that prefix destroys the manual snapshot.**
The byte-identical copy currently sits outside R2 at
`/tmp/c77bd1687dcd__lyrics.backup.357735493.lrc` (md5 `4c4fbdb2…`) and must be
moved to durable storage independently of this change.

## Accepted residual risks

- **HEAD-then-COPY is TOCTOU.** Two writers that both confirm the same key is
  free can still race and the second `copy_object` overwrites the first. The
  window is one round trip and requires both writers to land on the same
  millisecond *and* to bypass the `StaleObjectError` ETag check on
  `lyrics.lrc`; treated as accepted. Closing it fully needs conditional copy
  (`If-None-Match: *`), whose R2 support is unverified — future work.
- **Concurrent prunes** on the same prefix may each delete an overlapping set;
  `delete_object` is idempotent, so the outcome is still "some objects at or
  under the cap", with the newest retained.
- **`LastModified` resolution.** If R2 truncates to seconds, same-second
  backups tie and fall back to the deterministic key tiebreak; the protected
  key is still never deleted.

## Out of Scope

- **Retention of `{hash_prefix}/backups/lyrics.{YYYYmmdd-HHMMSS}.lrc`**
  (`admin/editor/upload.py:109`, `upload_r2_backup`): different prefix, never
  pruned, unbounded growth, and arguably the most valuable snapshots. Unified
  retention is a separate change with its own migration question. User decision:
  keep out of scope for v2.
- Restoring the manual LRC to the official key.
- Changing `MAX_BACKUPS_PER_PREFIX`'s value (duplication documented, not removed).
- Renaming, re-keying, or backfilling existing R2 objects.
- Conditional-copy (`If-None-Match`) collision elimination.
- Any webapp / Android consumer — neither reads `lyrics.backup.*`.

## Acceptance Criteria

- [ ] Both writers mint `{hash_prefix}/lyrics.backup.{epoch_ms}.lrc` using
      `int(time.time() * 1000)`; no monotonic/event-loop clock remains.
- [ ] Prune order is determined solely by `LastModified` in both services; no
      filename integer is parsed anywhere.
- [ ] Delete calls are issued oldest → newest; a mixed-clock key set deletes the
      oldest-`LastModified` object, not the numerically smallest key.
- [ ] The newly created backup is never deleted in the same call, including
      when it is the oldest object or ties on `LastModified`.
- [ ] Unparsable keys are ordinary candidates; nothing is pinned to delete
      first.
- [ ] Post-prune object count ≤ `MAX_BACKUPS_PER_PREFIX`, with the new backup
      counted inside the cap.
- [ ] Backup creation never overwrites an existing key: 10 occupied
      millisecond slots → `BackupFailedError`, no `copy_object`.
- [ ] Per-key delete failures are logged and skipped; prune failure logs a
      warning and never fails the upload.
- [ ] The two `select_backups_to_delete` copies are byte-identical, pass the
      parity test, and the vendored docstring names the admin path (matching
      `workers/lrc_parser.py`).
- [ ] Both test suites pass; the mixed-clock regression test fails on the
      pre-fix code.
- [ ] Non-destructive smoke on a scratch prefix: exactly the oldest
      `LastModified` key deleted, manual-style key retained, new backup present,
      `c77bd1687dcd` untouched.
