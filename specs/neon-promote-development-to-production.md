# Neon: Promote `development` → `production`, then snapshot `production`

**Date:** 2026-09-25
**Project:** Neon project `muddy-mode-84176076` (org `org-polished-hill-46155238`), Free plan
**Status:** Planned — not yet executed

## Goal

1. Promote the live `development` branch data (~137 MB) into the root `production` branch
   (currently stale, ~34 MB, idle since July).
2. Take a manual snapshot of `production` after promotion.
3. Restructure the branch tree so the dev flow (`staging` → `development`) hangs off the
   newly promoted `production`.

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
safety net; local `pg_dump` files are the rollback mechanism.

## Decisions (confirmed with Matt)

| Decision | Choice |
|---|---|
| Production's existing data | Wipe & replace with development data (safety dump of prod taken first) |
| Free-plan snapshot slot (1 max, already used) | Delete the old May-15 snapshot, then create the new one |
| dev_0904 | Delete |
| dev_0912 / dev_0923 | Archive as local `pg_dump` files, then delete (required — Neon refuses to delete a branch that has children) |
| Topology after | Restructure: `production` (root) → `staging` → `development` |

## Constraints & facts driving the design

- **No re-parenting in Neon.** The only way to move data "up" the tree is
  dump → restore. `development` can never become the root branch itself.
- **Snapshots are root-branches-only** → they must target `production` after the data
  lands there.
- **Free plan = 1 manual snapshot.** The new snapshot requires deleting the old one.
  Delete old + create new back-to-back to minimize the unprotected window.
- **Branch deletion does not cascade** — children must be deleted first
  (docs: "You cannot delete a branch that has child branches").
- **Local `pg_dump` is 16.15; the Neon server is PG 17.** pg_dump must be ≥ server
  version → `postgresql-client-17` must be installed (or use a `postgres:17` Docker
  container) before any dump/restore.
- **Free plan = 0.5 GB storage/project.** End state: production ~137 MB + two CoW
  children (near-zero marginal) ≈ 150 MB ✓. The ~137 MB snapshot is separate storage.
- **Protected branches are paid-only** — `production` cannot be protected on Free.
- Free-plan root-branch allowance is 3; we keep exactly 1 root.

## Safety artifacts (all local, gitignored)

Dump directory: `output/neon-backups/` (add to `.gitignore` if not already ignored).

| File | Purpose |
|---|---|
| `production_pre_promotion_YYYYMMDD.dump` | Rollback: production's pre-promotion state |
| `development_source_YYYYMMDD.dump` | The promotion payload (source of truth) |
| `dev_0912_archive_YYYYMMDD.dump` | Archival of dormant branch |
| `dev_0923_archive_YYYYMMDD.dump` | Archival of dormant branch |

---

## Phase 0 — Preconditions

1. Install PG 17 client tools:
   ```bash
   sudo apt-get install -y postgresql-client-17
   # verify: pg_dump --version  → must be ≥ 17
   ```
   Fallback if apt is unavailable: run dump/restore inside
   `docker run --rm -i postgres:17 …`.
2. Confirm `NEON_API_KEY` is set in the environment and `neon branch list` works.
3. Record current branch IDs (see table above) in case names change mid-run.

## Phase 1 — Safety dumps

Connection strings are resolved via the CLI (never paste secrets into logs/specs):

```bash
mkdir -p output/neon-backups

# Unpooled DSNs (pg_dump/pg_restore should NOT use the -pooler host)
neon connection-string production  > /tmp/dsn_prod.env      # then source-style export
neon connection-string development > /tmp/dsn_dev.env

pg_dump --format=custom --no-owner --no-privileges \
  --file output/neon-backups/production_pre_promotion_20260925.dump \
  "$PROD_DSN"
pg_dump --format=custom --no-owner --no-privileges \
  --file output/neon-backups/development_source_20260925.dump \
  "$DEV_DSN"
pg_dump --format=custom --no-owner --no-privileges \
  --file output/neon-backups/dev_0912_archive_20260925.dump \
  "$DEV0912_DSN"
pg_dump --format=custom --no-owner --no-privileges \
  --file output/neon-backups/dev_0923_archive_20260925.dump \
  "$DEV0923_DSN"
```

Verify each dump: `pg_restore --list <file>` exits 0 and lists the expected tables.

Also record on **development**: `pg_extension` list, non-system schema list (`\dn`),
and row counts of key tables (users/sessions, songs, songsets, theme_anchors, …) —
these become the Phase 3 verification baseline:

```sql
SELECT extname FROM pg_extension;
SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema';
SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname;
```

## Phase 2 — Wipe & restore production

1. On **production**, drop pre-existing extensions (beyond `plpgsql`) and the schema,
   so nothing stale survives (the dump may not contain objects that prod has):

   ```sql
   -- first: SELECT extname FROM pg_extension;  → DROP EXTENSION IF EXISTS <each non-plpgsql> CASCADE;
   DROP SCHEMA public CASCADE;
   CREATE SCHEMA public;
   -- recreate any additional schemas found on development in Phase 1
   ```
2. Restore the development dump into production:
   ```bash
   pg_restore --no-owner --no-privileges --jobs 4 \
     --dbname "$PROD_DSN" \
     output/neon-backups/development_source_20260925.dump
   ```
   Ownership maps to the connecting role (`--no-owner`); grants are skipped
   (`--no-privileges`) — same-lineage roles, so this is safe.

## Phase 3 — Verify production

1. Re-run the Phase 1 baseline queries on **production**; row counts and table list
   must match development (they should be equal).
2. Extension list matches development.
3. Smoke test: run the webapp locally with `DATABASE_URL` pointed at production's
   pooled DSN (`neon connection-string production --pooled`) — sign in, load
   `/songsets`, open a songset page. Or minimally exercise the Better Auth + drizzle
   paths via `psql`.
4. Do **not** proceed until verification passes. `development` is still untouched at
   this point and remains the fallback source.

## Phase 4 — Snapshot production

Back-to-back, minimizing the unprotected window:

```bash
neon snapshots delete snap-spring-hill-akdendjj
neon snapshots create --branch production --name production-promoted-20260925
# no --expires-at → kept until manually deleted
```

Confirm with `neon snapshots list` (exactly one snapshot, source = production).

## Phase 5 — Restructure the dev flow

Deletion order is forced by the no-children rule (leaf → root):

```bash
neon branches delete dev_0904        # archived branch
neon branches delete dev_0912        # dump archived locally in Phase 1
neon branches delete dev_0923        # dump archived locally in Phase 1
neon branches delete development     # old live branch; data now lives in production
neon branches delete staging
```

Rebuild the flow from the promoted root:

```bash
neon branches create --name staging    --parent production
neon branches create --name development --parent staging
```

Both get read-write computes by default (scale-to-zero on Free).

## Phase 6 — Repoint defaults and environments

1. Make production the project default:
   ```bash
   neon branches set-default production
   ```
2. Re-pin local context to the NEW development branch and refresh env:
   ```bash
   neon checkout development   # updates .neon, pulls that branch's env
   ```
   Note: the new `development` has a **new compute endpoint → new DSN**. Anything that
   cached the old development DSN must be refreshed (`neon env pull` / re-copy into
   the webapp's `.env.local`).
3. External config checklist (manual — outside this repo where applicable):
   - [ ] Deployed webapp (Vercel) `DATABASE_URL` → **production pooled DSN**
   - [ ] Render worker / Lambda env `DATABASE_URL` → production pooled DSN (if it
         reads the DB directly)
   - [ ] Android app: no change (talks only to webapp JSON APIs)
   - [ ] Admin CLI local usage: continues against development via `.env`

## Phase 7 — Final state check

```bash
neon branch list        # expect: production (root, default), staging, development
neon snapshots list     # expect: one snapshot of production, ~137 MB
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
| Phase 2/3 restore bad or verification fails | Re-wipe production and restore `production_pre_promotion_20260925.dump` (same procedure as Phase 2). Development untouched. |
| After Phase 4, before Phase 5 | Same as above; restore dev flow from the still-existing old branches if desired. |
| After Phase 5 (old tree deleted) | Data rollback: restore the pre-promotion dump into production. Dormant-branch data: restore `dev_0912/0923` archive dumps into fresh branches created from `development`. |

## Risks / notes

- **Snapshot gap:** between `snapshots delete` and `snapshots create` there is a brief
  window with no snapshot. Both run back-to-back in one sitting; production is idle,
  so exposure is minimal.
- **Snapshot cost/quotas:** manual snapshots have no forced expiry; watch storage
  quota on the Free plan (snapshot storage is separate from the 0.5 GB project cap,
  billed $0.09/GB-month on paid usage).
- **No branch protection on Free** — nothing prevents accidental writes to production.
  Revisit `protected` + backup schedules after any plan upgrade.
- **6-hour history window** means mistakes older than 6 h are only recoverable via the
  dumps in `output/neon-backups/`. Keep at least the pre-promotion dump until the next
  successful promotion cycle.
- **Repeatable runbook:** future promotions repeat Phases 1–7 (minus archiving steps
  unless new dormant dev branches accumulate). Consider scripting Phases 1–4 as an
  admin-cli command later.
