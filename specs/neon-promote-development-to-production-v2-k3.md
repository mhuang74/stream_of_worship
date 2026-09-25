# Neon: Promote `development` → `production`, then snapshot `production` (v2)

**Date:** 2026-09-25
**Project:** Neon project `muddy-mode-84176076` (org `org-polished-hill-46155238`), Free plan
**Status:** Planned — not yet executed
**Supersedes:** `specs/neon-promote-development-to-production.md` (v1)

## What changed vs v1 (review findings incorporated)

1. **Cutover reordered: repoint consumers BEFORE deleting old branches.** v1 deleted the
   live `development` branch (Phase 5) before repointing Vercel/Lambda (Phase 6) —
   guaranteed outage + silent loss of any writes made after the dump. v2 adds a
   declared **write-freeze window** (confirmed acceptable) and deletes old branches
   only after a grace period.
2. **Correct env var name: `SOW_DATABASE_URL`** (not `DATABASE_URL`) — used by the
   webapp (`src/db/index.ts`), `scripts/migrate.ts`, the render worker, and GitHub
   Actions secrets.
3. **GitHub Actions added to the consumer checklist** — `deploy.yml` runs
   `scripts/migrate.ts` against `secrets.SOW_DATABASE_URL` before every Vercel deploy;
   leaving it pointed at a deleted branch breaks all deploys. Migrations need the
   **direct (unpooled)** DSN; the Vercel runtime uses the **pooled** DSN.
4. **`set-default` moved before any branch deletion** — Neon refuses to delete the
   project's default branch; v1 would have failed mid-Phase-5 with a half-deleted tree.
5. **Verification uses exact `COUNT(*)`** instead of `pg_stat_user_tables.n_live_tup`
   (a planner estimate, typically 0/stale on a freshly restored DB until `ANALYZE`).
6. **Test-restore of the development dump into a scratch branch** before touching
   production (`pg_restore --list` only validates the archive TOC).
7. **Grace period:** old `staging`/`development` branches are renamed and retained
   (~7 days) instead of deleted immediately. Branch count (≤9 of 10) and storage
   (~280–310 MB of 500 MB) both allow it.
8. **"Repeatable runbook" warning corrected:** this dump→wipe→restore is a ONE-TIME
   bootstrap. Repeating it later would wipe live production user data. Future
   promotion = Drizzle migrations forward (see "Future promotion flow").
9. No plaintext DSN files in `/tmp`; dates use `$(date +%Y%m%d)`; `pg_restore` runs
   with `--exit-on-error`; `ANALYZE` after restore; redundant manual schema recreation
   removed (the dump emits `CREATE SCHEMA`).

## Goal

1. Promote the live `development` branch data (~137 MB) into the root `production`
   branch (currently stale, ~34 MB, idle since July).
2. Take a manual snapshot of `production` after promotion.
3. Restructure the branch tree so the dev flow (`staging` → `development`) hangs off
   the newly promoted `production`.

## Current state (verified 2026-09-25)

```
production  (br-falling-waterfall-akxdv9x0)   ROOT, default=false, ~34 MB, stale
└─ staging  (br-delicate-recipe-akkavkdx)     ~34 MB, idle
   └─ development  (br-jolly-wildflower-akl5skg9)  DEFAULT + primary, ~137 MB, LIVE data
      ├─ dev_0904  (br-dark-mountain-akaz444w)  ARCHIVED
      ├─ dev_0912  (br-shy-sunset-akf0hpqi)     ready, dormant (forked 09-12)
      └─ dev_0923  (br-flat-thunder-akueu9a6)   ready, dormant (forked 09-22)
```

Snapshots: exactly one — `snap-spring-hill-akdendjj` (production, 2026-05-15, manual).
Server: Postgres 17. History retention (Free plan): **6 hours** — PITR is NOT a usable
safety net; local `pg_dump` files plus the retained legacy branches are the rollback
mechanism.

**Confirmed consumers of the current `development` DSN (must all be repointed):**
deployed webapp (Vercel env), render worker (Lambda env), GitHub Actions
`secrets.SOW_DATABASE_URL`, and local admin CLI / lab app env files.

## Decisions (confirmed with Matt)

| Decision | Choice |
|---|---|
| Production's existing data | Wipe & replace with development data (safety dump first) |
| Free-plan snapshot slot (1 max, already used) | Delete the old May-15 snapshot, then create the new one, back-to-back |
| dev_0904 | Delete after its dump verifies |
| dev_0912 / dev_0923 | Archive as local `pg_dump` files, delete after dumps verify |
| Old `staging` / `development` | Rename to `legacy_*` and retain ~7 days (grace period), then delete |
| Write freeze | Acceptable — declared maintenance window during Phases 1–8 |
| Topology after | `production` (root, default) → `staging` → `development` |

## Constraints & facts driving the design

- **No re-parenting in Neon.** The only way to move data "up" the tree is
  dump → restore. `development` can never become the root branch itself.
- **Snapshots are root-branches-only** → they must target `production` after the data
  lands there.
- **Free plan = 1 manual snapshot.** Delete old + create new back-to-back to minimize
  the unprotected window.
- **Neon refuses to delete the default branch** and refuses to delete a branch that
  has children. Therefore: `set-default production` BEFORE deleting old `development`,
  and delete leaf branches before their parents.
- **Local `pg_dump` is 16.15; the Neon server is PG 17.** pg_dump must be ≥ server
  version → `postgresql-client-17` (or a `postgres:17` Docker container) before any
  dump/restore.
- **Free plan = 0.5 GB storage/project, 10 branches/project, 3 root branches.**
  Peak usage during the run: old dev 137 + old staging ~34 + scratch test-restore ~137
  ≈ **310 MB** (scratch deleted before production restore). After production restore
  with legacy branches retained: ≈ **280 MB**. Snapshot storage is separate. ✓
- **Pooled vs direct DSNs** (hostname with/without `-pooler`): pooled for serverless
  runtime (Vercel, Lambda); **direct for migrations** (`scripts/migrate.ts`,
  drizzle-kit) and for `pg_dump`/`pg_restore`.
- **Protected branches are paid-only** — `production` cannot be protected on Free.

## Safety artifacts (all local; `output/*` already gitignored)

Dump directory: `output/neon-backups/`

| File | Purpose |
|---|---|
| `production_pre_promotion_$(date +%Y%m%d).dump` | Rollback: production's pre-promotion state |
| `development_source_$(date +%Y%m%d).dump` | The promotion payload (source of truth) |
| `dev_0912_archive_$(date +%Y%m%d).dump` | Archival of dormant branch |
| `dev_0923_archive_$(date +%Y%m%d).dump` | Archival of dormant branch |
| `baseline_counts_$(date +%Y%m%d).txt` | Exact row counts + extension/schema list from development |

Keep `development_source_*.dump` and `production_pre_promotion_*.dump` until the next
successful promotion cycle.

---

## Phase 0 — Preconditions

1. Install PG 17 client tools:
   ```bash
   sudo apt-get install -y postgresql-client-17
   pg_dump --version   # must be ≥ 17
   ```
   Fallback: run dump/restore inside `docker run --rm -i postgres:17 …`.
2. Confirm `NEON_API_KEY` is set and `neon branches list` works.
3. Record current branch IDs (table above) in case names change mid-run.
4. Inventory the CURRENT values (hostnames only, not secrets) of:
   Vercel `SOW_DATABASE_URL`, Lambda `SOW_DATABASE_URL`, GH secret `SOW_DATABASE_URL`,
   and local env files — confirm they all reference the old `development` endpoint
   (`ep-…` host of `br-jolly-wildflower-akl5skg9`). Write the old endpoint hostname
   down; after repointing, grep for it to prove nothing still references it.

## Phase 1 — Start write freeze

1. Announce/declare the maintenance window (~30–60 min).
2. Pause the render pipeline: disable the Lambda SQS event source mapping (or set
   reserved concurrency to 0). Note the current setting so it can be restored.
   ```bash
   aws lambda list-event-source-mappings --function-name <render-worker> \
     --query 'EventSourceMappings[*].UUID' --output text
   aws lambda update-event-source-mapping --uuid <UUID> --no-enabled
   ```
3. Webapp: no maintenance-mode flag exists; the freeze is procedural (low-traffic
   window). Any webapp writes during the window are lost — keep the window short.

## Phase 2 — Safety dumps + baseline

Connection strings are resolved via the CLI into shell variables (never written to
disk, never pasted into logs):

```bash
mkdir -p output/neon-backups
DATE=$(date +%Y%m%d)

PROD_DSN="$(neon connection-string production)"       # direct (unpooled) by default
DEV_DSN="$(neon connection-string development)"
D0912_DSN="$(neon connection-string dev_0912)"
D0923_DSN="$(neon connection-string dev_0923)"

pg_dump --format=custom --no-owner --no-privileges \
  --file "output/neon-backups/production_pre_promotion_$DATE.dump" "$PROD_DSN"
pg_dump --format=custom --no-owner --no-privileges \
  --file "output/neon-backups/development_source_$DATE.dump" "$DEV_DSN"
pg_dump --format=custom --no-owner --no-privileges \
  --file "output/neon-backups/dev_0912_archive_$DATE.dump" "$D0912_DSN"
pg_dump --format=custom --no-owner --no-privileges \
  --file "output/neon-backups/dev_0923_archive_$DATE.dump" "$D0923_DSN"

# verify archive integrity (TOC readable)
for f in output/neon-backups/*_$DATE.dump; do pg_restore --list "$f" > /dev/null || echo "CORRUPT: $f"; done
```

Capture the verification baseline from **development** — EXACT counts, not planner
estimates (`pg_stat_user_tables.n_live_tup` is stale/zero on fresh restores):

```bash
psql "$DEV_DSN" -tAc "SELECT extname FROM pg_extension ORDER BY 1" \
  >> "output/neon-backups/baseline_counts_$DATE.txt"
psql "$DEV_DSN" -tAc "SELECT nspname FROM pg_namespace
  WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema' ORDER BY 1" \
  >> "output/neon-backups/baseline_counts_$DATE.txt"

# exact row counts — enumerate every user table explicitly (check drizzle schema
# or `\dt public.*` for the full list):
for t in <users> <sessions> <songs> <songsets> <theme_anchors> <render_jobs> ; do
  printf '%s: ' "$t" >> "output/neon-backups/baseline_counts_$DATE.txt"
  psql "$DEV_DSN" -tAc "SELECT count(*) FROM $t" >> "output/neon-backups/baseline_counts_$DATE.txt"
done
```

## Phase 3 — Test-restore into a scratch branch

`pg_restore --list` only proves the TOC is readable. Prove restorability end-to-end
before touching production:

```bash
neon branches create --name scratch_restore_test --parent production
SCRATCH_DSN="$(neon connection-string scratch_restore_test)"
pg_restore --no-owner --no-privileges --exit-on-error --jobs 4 \
  --dbname "$SCRATCH_DSN" "output/neon-backups/development_source_$DATE.dump"
# spot-check: row counts vs baseline
psql "$SCRATCH_DSN" -tAc "SELECT count(*) FROM <songs>"   # etc.
neon branches delete scratch_restore_test
```

Do not proceed on any error. Peak storage in this phase ≈ 310 MB (under the 500 MB
cap); if Neon reports a storage error, delete `dev_0904`/`dev_0912`/`dev_0923` first
(their dumps are already verified) and retry.

## Phase 4 — Wipe & restore production

1. On **production**, drop pre-existing extensions (beyond `plpgsql`) and the schema:

   ```sql
   -- first: SELECT extname FROM pg_extension;
   -- then:  DROP EXTENSION IF EXISTS <each non-plpgsql> CASCADE;
   DROP SCHEMA public CASCADE;
   CREATE SCHEMA public;
   ```
   (No need to pre-create other schemas — the dump emits `CREATE SCHEMA` and
   `CREATE EXTENSION` itself.)
2. Restore:
   ```bash
   pg_restore --no-owner --no-privileges --exit-on-error --jobs 4 \
     --dbname "$PROD_DSN" "output/neon-backups/development_source_$DATE.dump"
   ```
3. Refresh planner stats (fresh restores have none; verification and smoke tests
   misbehave without them):
   ```bash
   vacuumdb --analyze-only --dbname "$PROD_DSN"
   ```

## Phase 5 — Verify production

1. Re-run the exact `count(*)` queries on production; diff against
   `baseline_counts_$DATE.txt` — must match exactly.
2. Extension list matches the baseline.
3. Smoke test: run the webapp locally with `SOW_DATABASE_URL` set to production's
   **pooled** DSN (`neon connection-string production --pooled`) — sign in, load
   `/songsets`, open a songset page.
4. Do **not** proceed until verification passes. Rollback at this point is cheap
   (see Rollback): re-wipe production, restore the pre-promotion dump, end the
   freeze — `development` is untouched.

## Phase 6 — Snapshot production

Back-to-back, minimizing the unprotected window:

```bash
neon snapshots delete snap-spring-hill-akdendjj
neon snapshots create --branch production --name production-promoted-$DATE
neon snapshots list   # expect: exactly one snapshot, source = production
```

## Phase 7 — Make production the default branch (MUST precede deletions)

```bash
neon branches set-default production
```

Neon refuses to delete the default branch; doing this now unblocks the later
deletion of old `development`.

## Phase 8 — Repoint consumers, verify live, end freeze

| Consumer | Variable | New value |
|---|---|---|
| Vercel (deployed webapp) | `SOW_DATABASE_URL` | production **pooled** DSN — then trigger a redeploy |
| GitHub Actions secret | `SOW_DATABASE_URL` | production **direct (unpooled)** DSN — `deploy.yml` runs `scripts/migrate.ts` with it; migrations must not go through PgBouncer |
| Render worker (Lambda env) | `SOW_DATABASE_URL` | production **pooled** DSN |
| Admin CLI / lab apps (local env files) | `SOW_DATABASE_URL` / equivalent | production or NEW development DSN per use case (see Phase 9) |

Verification before ending the freeze:
1. Exercise the live webapp: sign in, load `/songsets`, open a songset.
2. Grep all config locations for the OLD development endpoint hostname recorded in
   Phase 0 — zero hits outside local archival notes.
3. Re-enable the Lambda SQS event source mapping; submit one test render job; confirm
   it completes and its status lands in the **production** DB.
4. Run the repo's deploy workflow once (or `scripts/migrate.ts` manually with the GH
   secret value) to prove CI still works.
5. End the write freeze.

## Phase 9 — Rebuild the dev flow alongside retained legacy branches

Names must be unique per project, so rename before recreating:

```bash
neon branches rename development legacy_development_$DATE
neon branches rename staging    legacy_staging_$DATE
neon branches create --name staging     --parent production
neon branches create --name development --parent staging
```

Re-pin local context to the NEW development branch and refresh env files:

```bash
neon checkout development   # updates .neon
# the NEW development has a NEW compute endpoint → NEW DSN.
# refresh webapp .env.local, admin CLI / lab app env files (neon env pull or manual)
```

Note: the archived branch `dev_0904` still sits under `legacy_development_*`; it is
deleted in Phase 10. Dormant-branch dumps were taken in Phase 2.

## Phase 10 — Grace period, then delete legacy branches

**Wait ~7 days** with the live system running against production. The legacy tree is
the fastest possible rollback (no dump restore needed). Branch count during grace:
production, staging, development, legacy_staging, legacy_development, dev_0904,
dev_0912, dev_0923 = 8 of 10 ✓.

Then delete, leaf-first (Neon refuses to delete a branch that has children):

```bash
neon branches delete dev_0904
neon branches delete dev_0912
neon branches delete dev_0923
neon branches delete legacy_development_$DATE
neon branches delete legacy_staging_$DATE
```

(Earlier deletion of `dev_0904`/`dev_0912`/`dev_0923` is safe any time after Phase 2 —
their dumps are verified — if branch slots or storage are needed mid-run.)

## Phase 11 — Final state check

```bash
neon branches list    # expect: production (root, default), staging, development
neon snapshots list   # expect: one snapshot of production, ~137 MB
```

Target topology:

```
production  (root, default, ~137 MB live data, snapshotted)
└─ staging
   └─ development
```

---

## Rollback

| Failure point | Rollback |
|---|---|
| Phase 3 test-restore fails | Abort. Production untouched; investigate dump (re-dump, check PG version, extensions). End freeze. |
| Phase 4/5 restore bad or verification fails | Re-wipe production; `pg_restore` `production_pre_promotion_$DATE.dump`. Development untouched. End freeze. |
| Phase 6–8, before repoint verification passes | Same as above; all consumers still point at old `development`. |
| During grace period (Phase 9–10) | Repoint consumers back to `legacy_development_$DATE` DSN (it is untouched); restore `production_pre_promotion` dump into production if prod must be reverted. |
| After legacy deletion | Data rollback via local dumps only: restore `production_pre_promotion_$DATE.dump` into production; restore `dev_0912/0923` archives into fresh branches. |
| Render worker writes lost during freeze | In-flight jobs at freeze time may have stale status; re-submit from the webapp after Phase 8. |

## Future promotion flow (IMPORTANT — read before ever repeating this)

**This dump→wipe→restore procedure is a ONE-TIME bootstrap**, safe only because
production is currently stale and disposable. After this run, production accumulates
live user data (Better Auth users, songsets, render jobs). Re-running this procedure
later would **destroy that data**.

From now on, promotion is schema-migrations-forward:

1. Develop on `development`; validate on `staging`.
2. Apply schema changes to production via Drizzle migrations
   (`scripts/migrate.ts` / `drizzle-kit migrate`) using the **direct** DSN — the
   existing `deploy.yml` job already does this on merge.
3. Refresh `staging`/`development` data from production with **reset from parent**
   (`neon branches reset development`) when a fresh copy of prod data is wanted —
   never the reverse.
4. Keep periodic `neon snapshots` of production as the backup mechanism within the
   Free-plan slot.

## Risks / notes

- **Snapshot gap:** between `snapshots delete` and `snapshots create` there is a brief
  window with no snapshot. Both run back-to-back; production is frozen, so exposure is
  minimal.
- **Snapshot storage** is separate from the 0.5 GB project cap; on paid usage it bills
  ~$0.09/GB-month.
- **No branch protection on Free** — nothing prevents accidental writes to production.
  Revisit `protected` + scheduled snapshots (`neon snapshots schedule set`) after any
  plan upgrade.
- **6-hour history window** — mistakes older than 6 h are recoverable only via the
  dumps in `output/neon-backups/` (and the legacy branches until Phase 10).
- **Freeze is procedural, not enforced.** The webapp has no maintenance-mode flag;
  writes that slip in during the window are lost. Keep the window short and quiet.
