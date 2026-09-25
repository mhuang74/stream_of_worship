# Neon: Promote `development` → `production` — Plan v2 (reviewed)

**Date:** 2026-09-25
**Project:** Neon project `muddy-mode-84176076` (org `org-polished-hill-46155238`), Free plan
**Status:** Planned — not yet executed
**Supersedes:** `specs/neon-promote-development-to-production.md` (v1, 2026-09-25). v1 is not
edited; this document is the corrected plan.

## Goal

Unchanged from v1:

1. Promote the live `development` branch data (~137 MB) into the root `production` branch
   (currently stale, ~34 MB, idle since July).
2. Take a manual snapshot of `production` after promotion.
3. Restructure the branch tree so the dev flow (`staging` → `development`) hangs off the
   newly promoted `production`.

## Current state (verified 2026-09-25, unchanged from v1)

```
production  (br-falling-waterfall-akxdv9x0)   ROOT, default=false, ~34 MB, stale
└─ staging  (br-delicate-recipe-akkavkdx)     ~34 MB, idle
   └─ development  (br-jolly-wildflower-akl5skg9)  DEFAULT + primary, ~137 MB, LIVE data
      ├─ dev_0904  (br-dark-mountain-akaz444w)  ARCHIVED
      ├─ dev_0912  (br-shy-sunset-akf0hpqi)     ready, dormant (forked 09-12)
      └─ dev_0923  (br-flat-thunder-akueu9a6)   ready, dormant (forked 09-22)
```

Snapshots: exactly one — `snap-spring-hill-akdendjj` (production, 2026-05-15, manual).
Server: Postgres 17. History retention (Free plan): **6 hours, capped at 1 GB** —
[docs](https://neon.com/docs/postgres/backup-restore/history-window). Local `pg_dump`
files (plus an R2 copy, new in v2) are the rollback mechanism.

**Environment facts confirmed with Matt (2026-09-25):**

- Deployed webapp (Vercel) `DATABASE_URL` currently points at the **old `development`
  pooled DSN**.
- **No real user traffic** — the deployed app is effectively idle; a maintenance-style
  write freeze is trivial to honor.
- **No Neon-managed Vercel integration** — plain env var in Vercel. Changing the project
  default branch therefore has no preview-branch side effects.
- Safety dumps get a second copy uploaded to **Cloudflare R2** (bucket `stream-of-worship`).

---

## Review findings — issues in v1 that v2 fixes

### CRITICAL

**C1 — Phase 5 as written cannot run: you cannot delete the default branch.**
v1 deletes `development` in Phase 5 but only runs `neon branches set-default production` in
Phase 6, *after* the deletions. Neon only permits deleting non-default branches
("Any branch not designated as the default branch … You can rename or delete non-default
branches", [Manage branches](https://neon.com/docs/manage/branches#delete-a-branch)).
`neon branches delete development` would fail and stall the run mid-tree.
**Fix:** run `set-default production` immediately after Phase 3 verification (Phase 5 below),
before any deletion.

**C2 — any write to `development` after the Phase 1 dump is silently lost.**
The deployed webapp points at `development`. v1 takes the source dump in Phase 1, then
spends Phases 2–4 restoring/verifying/snapshotting, then *deletes* `development` in
Phase 5. Any write that landed on `development` in between (an accidental page load that
creates a session row, a render job update, admin CLI usage) exists nowhere in the
promoted data and is destroyed with the branch. There is no traffic today, which makes
this cheap to prevent but easy to forget.
**Fix:** (a) declare a write-freeze rule for the whole run; (b) repoint Vercel to
production right after verification (Phase 4) — *before* anything is deleted; (c) run an
exact row-count zero-drift check on `development` immediately before deleting it
(Phase 7).

### HIGH

**H1 — verification baseline uses `pg_stat_user_tables.n_live_tup`, which is an estimate
and can be 0 on a freshly restored database.** `n_live_tup` comes from statistics that are
not populated until `ANALYZE`/autovacuum runs. Phase 3 could "pass" against a meaningless
baseline or fail against a good restore.
**Fix:** run `ANALYZE;` after restore, then compare **exact** `SELECT count(*)` per table,
generated from the catalog.

**H2 — `pg_restore` without `--exit-on-error` continues past errors.** In database mode
pg_restore reports errors but keeps going by default, leaving a partially restored schema
that a loose check might not catch.
**Fix:** add `--exit-on-error` and fail the phase on any non-zero exit.

**H3 — rollback artifacts are single-copy, laptop-local, gitignored.** A disk failure
between Phase 1 and Phase 7 leaves no recoverable copy of pre-promotion production, the
promotion payload, or the dormant-branch archives.
**Fix:** sha256 the dumps and upload them to R2 before touching anything (Phase 1).

**H4 — the new snapshot is never restore-tested.** After Phase 4 in v1, the only on-Neon
copy of the promoted state is the new manual snapshot, and nothing proves it restores.
**Fix:** multi-step snapshot restore to a throwaway branch, verify counts, delete the
throwaway (Phase 6). Restoring a snapshot to a *new* branch consumes no manual-snapshot
slot and does not touch `production`.

**H5 — DSN secrets written to a file in `/tmp` and a "source-style export" hand-wave.**
The v1 snippet `neon connection-string production > /tmp/dsn_prod.env` drops a live DSN
into world-readable `/tmp` and produces a bare string that is not sourceable.
**Fix:** capture connection strings into shell variables in the same shell session; never
write them to disk.

### MEDIUM / notes (carried into Risks)

- **M1 — `.neon` context stays pinned to `development` while it is being deleted.** Unpin
  with `neon checkout production` before the deletion phase.
- **M2 — branch auto-archiving on Free.** Branches idle for 24 h and older than 14 days are
  auto-archived to cold storage; access auto-unarchives but with slow first connections.
  Affects the dormant `dev_0912`/`dev_0923` dumps (slow but fine) and the *new*
  `staging`/`development` branches whenever they go idle for a day (future dev friction:
  first connection after a break is slow; a cold-start double penalty on top of
  scale-to-zero).
- **M3 — Neon–Vercel integration guard.** Not used today. If it is ever enabled later,
  preview branches fork from the project **default** branch — which after this promotion is
  `production` with real user data (emails, sessions). Keep the manual env var setup, or
  think hard before enabling the integration.
- **M4 — instant restore is an extra safety net for `production` only.** `production` is a
  root branch, so `neon branches restore production ^self@<ts>` can roll it back within the
  6-hour history window — including the wipe. v1 didn't use this. v2 records the pre-wipe
  timestamp. (Note: instant-restore sources must be root branches, so it cannot move
  `development`'s data up — dump/restore remains genuinely required for the promotion
  itself. Confirmed: manual snapshots are also root-only.)

---

## Decisions (unchanged from v1, plus new)

| Decision | Choice |
|---|---|
| Production's existing data | Wipe & replace with development data (safety dump of prod taken first) |
| Free-plan snapshot slot (1 max, already used) | Delete the old May-15 snapshot, then create the new one |
| dev_0904 | Delete |
| dev_0912 / dev_0923 | Archive as local `pg_dump` files + R2 copy, then delete (Neon refuses to delete a branch that has children) |
| Topology after | `production` (root) → `staging` → `development` |
| **new:** safety dump redundancy | sha256 + upload to R2 `stream-of-worship` bucket, `neon-backups/` prefix |
| **new:** default-branch switch | `set-default production` happens *before* deleting old `development` |
| **new:** Vercel repoint | Happens *before* any branch deletion, right after production verification |

## Constraints & facts driving the design (verified against Neon docs, 2026-09-25)

- **No re-parenting in Neon**; child→root data movement requires dump → restore. Instant
  restore and manual snapshots can only *source from root branches*, so neither can promote
  `development`'s data into `production`. ([backup-restore](https://neon.com/docs/guides/backup-restore),
  [instant restore](https://neon.com/docs/postgres/backup-restore/branch-restore))
- **Default branch cannot be deleted**; only non-default branches can.
- **Branch deletion does not cascade** — children must be deleted first.
- **Manual snapshots: root branches only; 1 on Free;** restore-to-new-branch is available
  for smoke-testing and does not consume a slot. ([snapshots CLI](https://neon.com/docs/cli/snapshots))
- **Local `pg_dump` is 16.15; the Neon server is PG 17** → install `postgresql-client-17`
  (or use `postgres:17` Docker) before any dump/restore.
- **Free plan = 0.5 GB storage/project, 10 branches, 6 h / 1 GB history window.**
- **Protected branches are paid-only.** Free-plan root-branch allowance is 3; end state
  keeps 1 root (plus any transient backup branches).
- **`pg_restore` does not change endpoints or DSNs** — production keeps its existing
  connection string through the wipe/restore, so repointing Vercel is a pure env-var change
  with no endpoint surprises.

## Safety artifacts

Local dump directory: `output/neon-backups/` (already gitignored via `output/*`).
Second copy: R2 bucket `stream-of-worship`, prefix `neon-backups/<date>/`, uploaded with
the project's existing R2 credentials (S3-compatible endpoint; `aws s3 cp`/`rclone`/admin
tooling all work).

| File | Purpose |
|---|---|
| `production_pre_promotion_YYYYMMDD.dump` | Rollback: production's pre-promotion state |
| `development_source_YYYYMMDD.dump` | The promotion payload (source of truth) |
| `dev_0912_archive_YYYYMMDD.dump` | Archival of dormant branch |
| `dev_0923_archive_YYYYMMDD.dump` | Archival of dormant branch |
| `manifest_YYYYMMDD.sha256` | Checksums of all dumps (also uploaded) |

---

## Phase 0 — Preconditions

1. Install PG 17 client tools:
   ```bash
   sudo apt-get install -y postgresql-client-17
   # verify: pg_dump --version  → must be ≥ 17
   ```
   Fallback if apt is unavailable: run dump/restore inside
   `docker run --rm -i postgres:17 …`.
2. `neon --version` — must be recent enough to have the `snapshots` subcommands
   (`snapshots create/list/restore`). Upgrade with `npm i -g neon` if not.
3. Confirm `NEON_API_KEY` is set and `neon branch list` works.
4. Record current branch IDs (table above) in the run notes, in case names change mid-run.
5. **Write freeze:** for the entire run, do not sign in to the deployed webapp, run the
   admin CLI against `development`, or trigger render jobs. The deployed webapp still
   points at `development` until Phase 4 repoints it.
6. Run every phase in a single shell session per phase; treat any non-zero exit as a stop.

## Phase 1 — Safety dumps, exact-count baseline, R2 upload

Connection strings live in shell variables only — never in files, never in terminal
scrollback where avoidable:

```bash
mkdir -p output/neon-backups
D="20260925"

# Unpooled DSNs (pg_dump/pg_restore must NOT use the -pooler host)
PROD_DSN="$(neon connection-string production)"
DEV_DSN="$(neon connection-string development)"
DEV0912_DSN="$(neon connection-string dev_0912)"
DEV0923_DSN="$(neon connection-string dev_0923)"

pg_dump --format=custom --no-owner --no-privileges \
  --file output/neon-backups/production_pre_promotion_$D.dump "$PROD_DSN"
pg_dump --format=custom --no-owner --no-privileges \
  --file output/neon-backups/development_source_$D.dump "$DEV_DSN"
pg_dump --format=custom --no-owner --no-privileges \
  --file output/neon-backups/dev_0912_archive_$D.dump "$DEV0912_DSN"
pg_dump --format=custom --no-owner --no-privileges \
  --file output/neon-backups/dev_0923_archive_$D.dump "$DEV0923_DSN"
```

Notes:
- `dev_0912`/`dev_0923` are dormant and may auto-archive at any moment; if a dump is slow
  to start, that is the unarchive — wait, don't cancel.
- Verify each dump: `pg_restore --list <file>` exits 0 and lists the expected tables.
- Checksums + R2 second copy (fail the phase if the upload fails — do not proceed on a
  single local copy):
  ```bash
  (cd output/neon-backups && sha256sum *$D.dump > manifest_$D.sha256)
  # upload with whichever R2 tooling is configured for the project, e.g.:
  aws s3 cp output/neon-backups/ s3://stream-of-worship/neon-backups/$D/ \
    --recursive --endpoint-url "$R2_ENDPOINT"
  ```

Exact-count baseline on **development** (replaces v1's `n_live_tup` estimate — see H1):

```sql
-- generate and run exact counts; save the output for Phase 3
SELECT format('SELECT %L AS tbl, count(*) AS n FROM %I.%I;',
       table_schema || '.' || table_name, table_schema, table_name)
FROM information_schema.tables
WHERE table_type = 'BASE TABLE'
  AND table_schema NOT IN ('pg_catalog', 'information_schema');
```

Also record on **development**: `SELECT extname FROM pg_extension;`, non-system schema list
(`\dn`), and the output of `pg_restore --list development_source_$D.dump` (the table list
becomes the Phase 3 target).

## Phase 2 — Wipe & restore production

0. **Record the pre-wipe UTC timestamp** (`date -u +%FT%TZ`). Within the next 6 hours,
   `neon branches restore production ^self@<timestamp> --preserve-under-name
   production_pre_wipe` is an instant-restore escape hatch if everything else fails
   (production is a root branch, so PITR applies). The pg_dump rollback remains primary.
1. On **production**, drop pre-existing extensions (beyond `plpgsql`) and the schema:
   ```sql
   -- first: SELECT extname FROM pg_extension;  → DROP EXTENSION IF EXISTS <each non-plpgsql> CASCADE;
   DROP SCHEMA public CASCADE;
   CREATE SCHEMA public;
   -- recreate any additional non-system schemas found on development in Phase 1
   ```
2. Restore the development dump into production — **with `--exit-on-error`** (see H2):
   ```bash
   pg_restore --no-owner --no-privileges --exit-on-error \
     --dbname "$PROD_DSN" \
     output/neon-backups/development_source_20260925.dump
   ```
   Ownership maps to the connecting role (`--no-owner`); grants are skipped
   (`--no-privileges`) — same-lineage roles, so this is safe. (`--jobs` omitted: single
   connection, deterministic failure point; add it back if restore time matters.)

## Phase 3 — Verify production

1. `ANALYZE;` on production (so any catalog-based checks behave), then re-run the Phase 1
   exact-count queries. **Every table must match development exactly.**
2. Extension list and non-system schema list match development.
3. Table list matches `pg_restore --list` of the source dump.
4. Smoke test: run the webapp locally with `DATABASE_URL` pointed at production's pooled
   DSN (`neon connection-string production --pooled`) — sign in, load `/songsets`, open a
   songset page. Or minimally exercise the Better Auth + drizzle paths via `psql`.
5. Do **not** proceed until verification passes. `development` is still untouched and
   remains the fallback source.

## Phase 4 — Repoint the deployed webapp (BEFORE any deletion)

Update the Vercel `DATABASE_URL` env var to production's **pooled** DSN and redeploy, then
confirm the deployed app loads and authenticates against production. Rationale:

- The production endpoint/DSN is unchanged by the dump/restore, so this is a pure
  env-var flip with no endpoint surprises.
- From this point on, nothing that matters writes to old `development` — which makes the
  Phase 7 zero-drift check meaningful and the deletion safe.
- Render worker / Lambda env: repoint now too if it reads the DB directly.

Checklist:
- [ ] Deployed webapp (Vercel) `DATABASE_URL` → production pooled DSN, redeployed, verified
- [ ] Render worker / Lambda env `DATABASE_URL` → production pooled DSN (if applicable)
- [ ] Android app: no change (talks only to webapp JSON APIs)

## Phase 5 — Make production the default (moved up from v1 Phase 6)

```bash
neon branches set-default production
neon checkout production   # unpin .neon from the soon-to-be-deleted development
```

This must happen **before** Phase 7's deletions — the default branch cannot be deleted
(C1), and `neon checkout` keeps the CLI context from referencing a branch mid-deletion
(M1). Also verify: the Neon-managed Vercel integration is *not* enabled (confirmed with
Matt) — if it ever gets enabled later, preview branches will fork from `production` and
copy real user data (M3).

## Phase 6 — Snapshot production (+ restore smoke test)

```bash
neon snapshots delete snap-spring-hill-akdendjj
neon snapshots create --branch production --name production-promoted-20260925
# no --expires-at → kept until manually deleted
neon snapshots list   # exactly one snapshot, source = production
```

If `snapshots create` fails after the delete: retry before continuing. The deleted May-15
snapshot held the stale 34 MB production state, which is fully preserved in
`production_pre_promotion_20260925.dump` (+ R2 copy), so nothing unique was lost.

Then **smoke-test the new snapshot** (new in v2, see H4) — restore it to a throwaway
branch, verify, clean up:

```bash
neon snapshots restore production-promoted-20260925 --name snap-smoke-test
# compare exact row counts on snap-smoke-test against the Phase 3 numbers
neon branches delete snap-smoke-test
```

Notes: restoring to a new branch does not consume a manual-snapshot slot and does not
touch `production`. The throwaway branch is created *from* a snapshot restore, so PITR is
unavailable on it (irrelevant — it is deleted minutes later). Do not run the smoke test
while Phase 2 is within its 6-hour instant-restore window concerns — order is fine as
written.

## Phase 7 — Zero-drift check, then delete the old tree

1. **Zero-drift check** (new in v2, see C2): re-run the Phase 1 exact-count queries on
   **old `development`** and compare against production's verified counts. They must match
   exactly. Any drift means something wrote to `development` during the run — stop,
   identify the writer, and either re-run Phases 1–3 with a fresh dump or consciously
   accept the delta before deleting.
2. Deletion order is forced by the no-children rule (leaf → root):
   ```bash
   neon branches delete dev_0904        # archived branch
   neon branches delete dev_0912        # dump archived locally + R2 in Phase 1
   neon branches delete dev_0923        # dump archived locally + R2 in Phase 1
   neon branches delete development     # old live branch; data now in production; default already moved (Phase 5)
   neon branches delete staging
   ```
   If a delete fails because the branch auto-archived mid-run, the operation still works —
   archiving does not block deletion; just re-check child order.

## Phase 8 — Rebuild the dev flow

```bash
neon branches create --name staging    --parent production
neon branches create --name development --parent staging
```

Both get read-write computes by default (scale-to-zero on Free, 5-minute suspend
timeout). Expect **auto-archiving** of `staging`/`development` whenever they sit idle
>24 h and are >14 days old — first connection after a break will be slow while they
unarchive (M2). This is normal Free-plan behavior, not breakage.

## Phase 9 — Repoint local environments

1. Re-pin local context to the NEW development branch and refresh env:
   ```bash
   neon checkout development   # updates .neon, pulls that branch's env
   ```
   The new `development` has a **new compute endpoint → new DSN**. Refresh anything that
   cached the old development DSN: `neon env pull` / re-copy into the webapp's
   `.env.local` / the admin CLI's `.env`.
2. Local webapp dev uses the new `development` pooled DSN; production DSN stays only in
   Vercel + render worker.

## Phase 10 — Final state check

```bash
neon branch list        # expect: production (root, default), staging, development
neon snapshots list     # expect: one snapshot of production, ~137 MB
```

Target topology:

```
production  (root, default, ~137 MB live data, snapshotted, serving the deployed webapp)
└─ staging
   └─ development   (new endpoint/DSN; local dev only)
```

---

## Rollback

| Failure point | Rollback |
|---|---|
| Phase 2/3 restore bad or verification fails | Re-wipe production and restore `production_pre_promotion_20260925.dump` (same procedure as Phase 2). Within 6 h of the wipe, `neon branches restore production ^self@<pre-wipe ts> --preserve-under-name production_pre_wipe` is a faster escape hatch. Development untouched. |
| Phase 4 app broken on production DSN | Revert the Vercel env var to the old development DSN (still alive until Phase 7). |
| After Phase 6, before Phase 7 | Same as Phase 2/3 rows; old branches all still exist. |
| After Phase 7 (old tree deleted) | Data rollback: restore the pre-promotion dump into production, or snapshot-restore `production-promoted-20260925` onto production (`neon snapshots restore <id> --target-branch production --finalize`). Dormant-branch data: restore `dev_0912/0923` archive dumps into fresh branches created from `development`. |

All rollback artifacts exist in two places: `output/neon-backups/` and
`s3://stream-of-worship/neon-backups/20260925/` (verify with the manifest sha256s).

## Risks / notes

- **Snapshot gap:** between `snapshots delete` and `snapshots create` there is a brief
  window with no snapshot. Both run back-to-back; production is idle; the deleted snapshot
  is fully covered by the pre-promotion dump. Exposure ≈ zero.
- **One manual snapshot slot on Free, forever:** every future "snapshot before a risky
  change" requires deleting the previous one. Scheduled (automated) snapshots are paid-only.
  Practical consequence: on-Neon safety comes from the 6-hour history window; anything
  older needs the dump/R2 routine. Worth scripting Phases 1–3 as an admin-cli command
  (`sow-admin maintenance backup-neon`-style) so a fresh dump is cheap to take before any
  risky operation — see the R2 backup tooling in `specs/admin-r2-backup-restore-v3.md`
  for the pattern.
- **6-hour history window** means mistakes noticed later than that are only recoverable
  via the dumps in `output/neon-backups/` + R2. Keep at least the pre-promotion dump until
  the next successful promotion cycle.
- **No branch protection on Free** — nothing prevents accidental writes to production.
  Revisit `protected` + backup schedules + a longer history window after any plan upgrade.
- **Branch auto-archiving (Free)** — dormant `staging`/`development` archive after
  14 days + 24 h idle and unarchive on next access (slow first connection). Not a blocker;
  expect it during dev breaks. Archived branches are not deleted, and deletion still works.
- **Neon–Vercel integration** — currently not used (manual env var). If enabled after this
  promotion, PR preview branches fork from `production` and contain real user data.
- **Repeatable runbook:** future promotions repeat Phases 0–10 (minus archiving steps
  unless new dormant dev branches accumulate). The write-freeze (Phase 0.5), Vercel repoint
  (Phase 4), set-default-before-delete (Phase 5), and zero-drift check (Phase 7) are the
  ordering invariants — keep them in that order.
