# Neon: Promote `development` → `production` — Plan v3 (synthesis)

**Date:** 2026-09-25
**Project:** Neon project `muddy-mode-84176076` (org `org-polished-hill-46155238`), Free plan
**Status:** Planned — not yet executed
**Supersedes:** v1 (`neon-promote-development-to-production.md`) and the four v2 variants
(`-v2-k3`, `-v2-dspro`, `-v2-glm53`, `-v2-qwen38`), none of which are edited.

## How v3 was built (synthesis of the four v2 reviews)

v3 takes **k3 as the base** (correct env var, GH Actions + pooled/direct DSN detail,
grace-period retention, scratch test-restore, one-time-bootstrap warning) and folds in:

- **qwen38:** freeze scope (Lambda + host tooling, not just "be careful"), freeze
  *verification* via `pg_stat_activity`, smoke test **after** the snapshot so the
  snapshot stays a clean copy.
- **glm53:** R2 off-host copy as a **fail-gate** in Phase 2, sha256 manifest,
  snapshot smoke test (restore to throwaway branch), zero-drift check, catalog
  generated exact-count baseline, instant-restore escape hatch timestamp.
- **dspro:** wipe must drop **all** non-system schemas, not just `public`.

## Rejected alternatives (evaluated against Neon docs, 2026-09-25)

1. **Server-side promotion (`neon branches restore production development`)** — rejected.
   Point-in-time restore docs state the **source must be a root branch**; head-restore
   examples are parent→child only. Restoring a root branch *from a descendant* is
   undocumented. Additionally, `production` currently **has children**, so
   `--preserve-under-name` is mandatory and per the restore API "all child branches will
   be moved to the newly created branch" — i.e. `staging`/`development` would be moved
   under a backup branch that **cannot be deleted** ("Backup branches created when
   restoring a root branch from another branch cannot be deleted"). Dump/restore is
   deterministic and takes minutes at 137 MB.
2. **In-place dev-tree rebuild via `restore`/`reset` (qwen38)** — rejected. Neon docs:
   **reset from parent is blocked for branches that have children** (`staging` has child
   `development`), and a restore of a branch with children moves those children to a
   backup branch; whether cross-branch restore re-parents the target is undocumented.
   The payoff ("zero env churn") is small: only `/opt/sow/.env`, `/opt/sow/.env_webapp`
   and root `.env.local` pin the development endpoint, and those are edited during this
   run anyway. v3 uses **rename → recreate → grace period** instead, which is fully
   documented behavior and also sidesteps the Branch-Recovery name-reservation risk.
3. **Snapshot-based promotion (dspro's alternative)** — impossible, not just awkward:
   manual snapshots are **root-branches-only** (Neon docs), so you cannot snapshot
   `development` at all. dspro's "snapshots are NOT root-only" correction is wrong.
4. **qwen38's `--jobs` removal** — its rationale ("parallel restore requires directory
   format") is incorrect: PG 17 `pg_restore` docs explicitly support `-j` for the
   **custom** format. v3 keeps `--jobs 4` (with `--exit-on-error`).

## Verified environment facts

- Env var is **`SOW_DATABASE_URL`** everywhere that matters: `delivery/webapp/src/db/index.ts`,
  `delivery/webapp/scripts/migrate.ts`, `delivery/render-worker/src/sow_render_worker/{config,db}.py`,
  `.github/workflows/deploy.yml` (migrations) and `ci.yml`. The root `.env.local` still
  uses legacy `DATABASE_URL` names — included in the inventory/grep sweep.
- `.github/workflows/deploy.yml` runs `npx tsx scripts/migrate.ts` with
  `secrets.SOW_DATABASE_URL` before every webapp deploy → the GH secret **must** be
  repointed and must be the **direct (unpooled)** DSN (migrations must not traverse
  PgBouncer). Vercel runtime and Lambda use the **pooled** DSN.
- Host dev tooling reads `/opt/sow/.env` (webapp `pnpm dev` via `env-cmd`, render-worker
  dev compose) and pins the development endpoint `ep-patient-paper-ak9vp129`;
  `/opt/sow/.env_webapp` likewise. Root `.env.local` pins `ep-patient-paper-ak9vp129(-pooler)`.
- Webapp auth is standard Better Auth with the Drizzle adapter (`src/lib/auth.ts`) —
  data lives in the regular schema; there is no `neon_auth` schema to special-case.

## Goal

1. Promote the live `development` branch data (~137 MB) into the root `production`
   branch (currently stale, ~34 MB, idle since July).
2. Take a manual snapshot of `production` after promotion (snapshot-of-record).
3. Restructure the branch tree so the dev flow (`staging` → `development`) hangs off
   the newly promoted `production`.

## Current state (verified 2026-09-25)

```
production  (br-falling-waterfall-akxdv9x0, ep-summer-water-ak5jwwyh)   ROOT, default=false, ~34 MB, stale
└─ staging  (br-delicate-recipe-akkavkdx, ep-lucky-cloud-akuzu8e5)      ~34 MB, idle
   └─ development  (br-jolly-wildflower-akl5skg9, ep-patient-paper-ak9vp129)  DEFAULT, ~137 MB, LIVE data
      ├─ dev_0904  (br-dark-mountain-akaz444w)  ARCHIVED — disposable, no dump
      ├─ dev_0912  (br-shy-sunset-akf0hpqi)     ready, dormant — dump, then delete
      └─ dev_0923  (br-flat-thunder-akueu9a6)   ready, dormant — dump, then delete
```

Snapshots: exactly one — `snap-spring-hill-akdendjj` (production, 2026-05-15, manual).
Server: Postgres 17. Free-plan history retention: **6 hours** — PITR is NOT a safety
net; local dumps + R2 copies + grace-period legacy branches are the rollback mechanism.

**Consumers to repoint (all confirmed):**

| Consumer | Variable | New value |
|---|---|---|
| Deployed webapp (Vercel) | `SOW_DATABASE_URL` | production **pooled** DSN |
| Render worker (Lambda) | `SOW_DATABASE_URL` | production **pooled** DSN |
| GitHub Actions secret | `SOW_DATABASE_URL` | production **direct (unpooled)** DSN |
| `/opt/sow/.env`, `/opt/sow/.env_webapp` | `SOW_DATABASE_URL` | **new** development pooled DSN |
| Root `.env.local`, admin CLI / lab app env | `DATABASE_URL` / `SOW_DATABASE_URL` | per use case (dev flow) |
| Android app | — | webapp JSON APIs only — no change |

## Decisions (confirmed with Matt)

| Decision | Choice |
|---|---|
| Production's existing data | Wipe & replace with development data (safety dump + R2 first) |
| Free-plan snapshot slot (1 max, used) | Delete May-15 snapshot, create new back-to-back |
| dev_0904 | Delete, **no dump** (archived, confirmed disposable) |
| dev_0912 / dev_0923 | Dump + R2, delete once dumps verify |
| Promotion mechanism | **Dump/restore only** (no server-side restore attempt) |
| Write freeze | **Procedural + verified** (no real traffic; Lambda paused; `pg_stat_activity` gate) |
| Old `staging` / `development` | Rename to `legacy_*`, retain ~7 days, then delete |
| Smoke test | **After** the snapshot (snapshot stays clean of session rows) |
| Topology after | `production` (root, default) → `staging` → `development` |

## Constraints & facts driving the design

- **No re-parenting in Neon.** Data moves "up" the tree only via dump → restore.
- **Snapshots are root-branches-only; 1 manual snapshot on Free.** Restore-from-snapshot
  to a *new* branch is allowed and consumes no slot (used for the smoke test).
- **Instant restore (PITR) is root-branch-only** — production-only escape hatch within
  the 6-hour history window.
- **The default branch cannot be deleted; deletion does not cascade** (leaf-first).
  `set-default production` must precede any deletion of the old default.
- **Local `pg_dump` is 16.15; server is PG 17** → `postgresql-client-17` or a
  `postgres:17` container.
- **`pg_restore -j` supports custom format** (PG 17 docs) — `--jobs 4` is valid.
- **Free plan:** 0.5 GB storage, 10 branches, 3 root branches, no protected branches.
  Peak storage ≈ 345 MB (Phase 3, scratch included); grace period ≈ 310 MB. ✓
- **Branch auto-archiving (Free):** idle >24 h + age >14 days → cold storage; slow
  first connection on unarchive. Expected for dormant branches; not a blocker.
- **Neon–Vercel integration is NOT used** (plain env var). If ever enabled later,
  preview branches would fork from `production` (real user data) — keep it off.

## Downtime & data-loss model

- **Freeze point (Phase 1):** Lambda paused, host tooling stopped, no webapp/admin
  writes. Freeze is *verified* (zero foreign sessions) before any dump is trusted.
- **App impact:** effectively zero (no real traffic). The deployed site is repointed at
  Phase 9 and the freeze ends immediately after.
- **Rollback** stays cheap through Phase 8: old `development` is untouched until the
  Phase 10 rename; production is restorable from dump, R2 copy, and the 6-hour PITR window.

## Safety artifacts

Local: `output/neon-backups/` (gitignored). Off-host: R2 bucket `stream-of-worship`,
prefix `neon-backups/<date>/`. DSNs live in shell variables only — never on disk.

| File | Purpose |
|---|---|
| `production_pre_promotion_$DATE.dump` | Rollback: production's pre-promotion state |
| `development_source_$DATE.dump` | The promotion payload (source of truth) |
| `dev_0912_archive_$DATE.dump` | Archival of dormant branch |
| `dev_0923_archive_$DATE.dump` | Archival of dormant branch |
| `baseline_dev_counts_$DATE.txt` | Exact row counts + extensions + schemas from development |
| `manifest_$DATE.sha256` | Checksums (uploaded to R2 with the dumps) |

Keep `production_pre_promotion` and `development_source` until the next successful
promotion cycle.

---

## Phase 0 — Preconditions

1. Install PG 17 client tools; `pg_dump --version` must print ≥ 17. Fallback:
   `docker run --rm -i postgres:17 …`.
2. `neon --version` must have the `snapshots` subcommands (`npm i -g neon` if not).
   Confirm `NEON_API_KEY` works: `neon branches list`.
3. Record current branch IDs (table above) in run notes.
4. `neon branches list` — confirm 8 branches or fewer; note whether **Branch Recovery**
   preview is enabled (if enabled, deleted branch names may be reserved; the
   rename-before-recreate flow in Phase 10 avoids this either way).
5. Inventory every location that references the old development endpoint
   `ep-patient-paper-ak9vp129`: Vercel env, Lambda env, GH secret, `/opt/sow/.env`,
   `/opt/sow/.env_webapp`, root `.env.local`, any admin CLI / lab app env files.
   Record the hostname for the Phase 9 grep sweep.
6. Rule for the whole run: run each phase in a single shell session; any non-zero
   exit stops the run.

## Phase 1 — Write freeze (procedural, then verified)

1. Declare the maintenance window.
2. Pause the render pipeline (it writes job status to the DB):
   ```bash
   aws lambda list-event-source-mappings --function-name sow-render-worker \
     --query 'EventSourceMappings[*].UUID' --output text
   aws lambda update-event-source-mapping --uuid <UUID> --no-enabled
   ```
3. Stop host tooling that writes to `development`: `pnpm dev`, analysis-service
   compose, render-worker dev compose; no admin CLI / songset-constructor runs.
4. Webapp: no maintenance-mode flag exists; with no real traffic the freeze is
   procedural — do not sign in to the deployed app during the run.
5. **Verify the freeze** on `development`:
   ```sql
   SELECT usename, application_name, client_addr, state FROM pg_stat_activity
   WHERE datname = current_database() AND pid <> pg_backend_pid();
   ```
   Expect zero rows (or only your own psql). Do not proceed until clean.

## Phase 2 — Safety dumps, baseline, R2 fail-gate

```bash
mkdir -p output/neon-backups && DATE=$(date +%Y%m%d)

PROD_DSN="$(neon connection-string production)"    # unpooled by default — correct for dump/restore
DEV_DSN="$(neon connection-string development)"
D0912_DSN="$(neon connection-string dev_0912)"     # wakes dormant compute; slow start = unarchive, wait
D0923_DSN="$(neon connection-string dev_0923)"

pg_dump --format=custom --no-owner --no-privileges \
  --file "output/neon-backups/production_pre_promotion_$DATE.dump" "$PROD_DSN"
pg_dump --format=custom --no-owner --no-privileges \
  --file "output/neon-backups/development_source_$DATE.dump" "$DEV_DSN"
pg_dump --format=custom --no-owner --no-privileges \
  --file "output/neon-backups/dev_0912_archive_$DATE.dump" "$D0912_DSN"
pg_dump --format=custom --no-owner --no-privileges \
  --file "output/neon-backups/dev_0923_archive_$DATE.dump" "$D0923_DSN"

# integrity check (TOC)
for f in output/neon-backups/*_$DATE.dump; do pg_restore --list "$f" > /dev/null || echo "CORRUPT: $f"; done

# checksums + off-host copy — FAIL THE PHASE if the upload fails
(cd output/neon-backups && sha256sum *_$DATE.dump > manifest_$DATE.sha256)
aws s3 cp output/neon-backups/ s3://stream-of-worship/neon-backups/$DATE/ \
  --recursive --endpoint-url "$R2_ENDPOINT_URL"
```

Baseline from **development** (exact counts generated from the catalog — never
`n_live_tup`; extensions and schemas included):

```bash
psql "$DEV_DSN" -At -c "SELECT extname FROM pg_extension ORDER BY 1" \
  > "output/neon-backups/baseline_dev_counts_$DATE.txt"
psql "$DEV_DSN" -At -c "SELECT nspname FROM pg_namespace
  WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema' ORDER BY 1" \
  >> "output/neon-backups/baseline_dev_counts_$DATE.txt"

COUNT_SQL="SELECT format('SELECT %L AS tbl, count(*) AS n FROM %I.%I;',
  table_schema || '.' || table_name, table_schema, table_name)
  FROM information_schema.tables
  WHERE table_type = 'BASE TABLE'
    AND table_schema NOT IN ('pg_catalog','information_schema') ORDER BY 1;"
psql "$DEV_DSN" -At -c "$COUNT_SQL" | psql "$DEV_DSN" -At \
  >> "output/neon-backups/baseline_dev_counts_$DATE.txt"
```

Then free slots/storage for the scratch test-restore (dumps verified + R2'd):

```bash
neon branches delete dev_0904        # no dump — confirmed disposable
neon branches delete dev_0912        # dump verified + R2
neon branches delete dev_0923        # dump verified + R2
```

## Phase 3 — Test-restore into a scratch branch

`pg_restore --list` only proves the TOC is readable; this dump is the only copy of the
live data until promotion completes. Prove restorability end-to-end:

```bash
neon branches create --name scratch_restore_test --parent production
SCRATCH_DSN="$(neon connection-string scratch_restore_test)"
# Scratch branch inherits production's tables/functions — live-verified that
# restore fails without pre-clean (pkey/function/table already-exists):
psql "$SCRATCH_DSN" -At -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
pg_restore --no-owner --no-privileges --exit-on-error --jobs 4 \
  --dbname "$SCRATCH_DSN" "output/neon-backups/development_source_$DATE.dump"

# regenerate the identical baseline triple (extensions, schemas, exact counts)
# on the scratch branch and diff whole files — must be identical:
psql "$SCRATCH_DSN" -At -c "SELECT extname FROM pg_extension ORDER BY 1" > /tmp/scratch_baseline.txt
psql "$SCRATCH_DSN" -At -c "SELECT nspname FROM pg_namespace
  WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema' ORDER BY 1" >> /tmp/scratch_baseline.txt
psql "$SCRATCH_DSN" -At -c "$COUNT_SQL" | psql "$SCRATCH_DSN" -At >> /tmp/scratch_baseline.txt
diff /tmp/scratch_baseline.txt "output/neon-backups/baseline_dev_counts_$DATE.txt"
rm /tmp/scratch_baseline.txt
neon branches delete scratch_restore_test
```

The `test-restore` subcommand of `ops/admin-cli/scripts/neon_backup_compare.py` performs
this pre-clean (generalized to all non-system schemas + non-plpgsql extensions) and the
baseline diff automatically; prefer it over the hand commands.

Do not proceed on any error. Peak storage here ≈ 345 MB (< 500 MB cap).

## Phase 4 — Wipe & restore production

0. **Record the pre-wipe UTC timestamp** (`date -u +%FT%TZ`). Within 6 hours,
   `neon branches restore production ^self@<ts> --preserve-under-name production_pre_wipe`
   is an instant-restore escape hatch (note: it would move `staging` under the backup
   branch — acceptable for an emergency; the dump remains the primary rollback).
1. On **production**, enumerate then drop everything stale — **all** non-system
   schemas (not just `public`) and all non-`plpgsql` extensions (the dump emits
   `CREATE SCHEMA` / `CREATE EXTENSION` itself):
   ```sql
   SELECT extname FROM pg_extension;                      -- DROP EXTENSION each non-plpgsql CASCADE
   SELECT nspname FROM pg_namespace
     WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema';
   -- DROP SCHEMA each CASCADE; then:
   DROP SCHEMA public CASCADE;
   CREATE SCHEMA public;
   ```
2. Restore — `--exit-on-error` (default pg_restore continues past errors) and
   `--jobs 4` (valid for custom format):
   ```bash
   pg_restore --no-owner --no-privileges --exit-on-error --jobs 4 \
     --dbname "$PROD_DSN" "output/neon-backups/development_source_$DATE.dump"
   ```
3. Refresh planner stats (fresh restores have none):
   ```bash
   vacuumdb --analyze-only --dbname "$PROD_DSN"
   ```

## Phase 5 — Verify production (read-only gate)

1. Extensions + schema list == baseline.
2. Exact counts == baseline (`diff` against `baseline_dev_counts_$DATE.txt`).
3. Table list matches `pg_restore --list` of the source dump.
4. Do **not** proceed until verification passes. Rollback here is cheap: re-wipe
   production, restore `production_pre_promotion_$DATE.dump`, end the freeze —
   `development` is untouched.

## Phase 6 — Snapshot production (+ snapshot smoke test)

Back-to-back to minimize the unprotected window; if `create` fails after `delete`,
retry immediately (the deleted May-15 snapshot held the stale 34 MB state, fully
covered by the pre-promotion dump):

```bash
neon snapshots delete snap-spring-hill-akdendjj
neon snapshots create --branch production --name "production-promoted-$DATE"
neon snapshots list   # exactly one snapshot, source = production, ~137 MB

# smoke test the snapshot (no slot consumed, production untouched):
neon snapshots restore "production-promoted-$DATE" --name snapshot_smoke_test
psql "$(neon connection-string snapshot_smoke_test)" -At -c "$COUNT_SQL"   # == baseline
neon branches delete snapshot_smoke_test
```

## Phase 7 — App smoke test (post-snapshot; writes are fine now)

Run the webapp locally with `SOW_DATABASE_URL` = production **pooled** DSN
(`neon connection-string production --pooled`): sign in, load `/songsets`, open a
songset page. Doing this *after* the snapshot keeps the snapshot a clean copy of the
promoted data (no Better Auth session rows).

## Phase 8 — Make production the default branch (must precede deletions)

```bash
neon branches set-default production
neon checkout production   # unpin .neon from the branch about to be renamed
neon branch list           # production shows [default]
```

## Phase 9 — Cutover: repoint consumers, zero-drift check, end freeze

Repoint **before** ending the freeze, **before** any rename of the old tree:

1. Vercel `SOW_DATABASE_URL` → production **pooled** DSN; trigger a redeploy.
2. GH secret `SOW_DATABASE_URL` → production **direct (unpooled)** DSN
   (`deploy.yml` runs `scripts/migrate.ts` with it). Prove CI: run
   `scripts/migrate.ts` manually with the secret value, or trigger the workflow.
3. Lambda `SOW_DATABASE_URL` → production **pooled** DSN; re-enable the SQS event
   source mapping; submit one test render job; confirm its status rows land in
   **production**.
4. Zero-drift check on old `development`: re-run `$COUNT_SQL` there and diff against
   production's verified counts. Any drift = something wrote during the run — stop,
   identify the writer, re-run Phases 2–5 or consciously accept the delta.
5. Grep all inventoried config locations for the old endpoint hostname
   (`ep-patient-paper-ak9vp129`) — zero hits outside archival notes.
6. End the write freeze.

## Phase 10 — Rebuild the dev flow (rename → recreate → update env)

Names must be unique per project; rename also frees the names immediately (and
sidesteps any Branch Recovery name reservation):

```bash
neon branches rename development legacy_development_$DATE
neon branches rename staging    legacy_staging_$DATE
neon branches create --name staging     --parent production
neon branches create --name development --parent staging
```

Re-pin local context and refresh env files (new development → **new endpoint/DSN**):

```bash
neon checkout development
# update: /opt/sow/.env, /opt/sow/.env_webapp (pooled new-development DSN),
#         root .env.local, admin CLI / lab app env files
```

Branch count after this phase: production, staging, development, legacy_staging,
legacy_development = 5 of 10 ✓. Storage ≈ 310 MB ✓.

## Phase 11 — Grace period, then delete legacy branches

Run live for ~7 days against production. The legacy tree is the fastest rollback
(repoint consumers back to `legacy_development_$DATE`; it is untouched). Then delete
leaf-first:

```bash
neon branches delete legacy_development_$DATE
neon branches delete legacy_staging_$DATE
```

## Phase 12 — Final state check

```bash
neon branch list      # production (root, default), staging, development
neon snapshots list   # one snapshot of production, ~137 MB
```

```
production  (root, default, ~137 MB live data, snapshotted, serving the deployed webapp)
└─ staging
   └─ development   (new endpoint/DSN; local dev only)
```

---

## Rollback

| Failure point | Rollback |
|---|---|
| Phase 2 (dump/upload fails) | Abort. Nothing changed. End freeze. |
| Phase 3 test-restore fails | Abort; production untouched. Investigate dump (PG version, extensions), re-dump. End freeze. |
| Phase 4/5 restore bad or verification fails | Re-wipe production; `pg_restore production_pre_promotion_$DATE.dump`. Within 6 h of the wipe: `neon branches restore production ^self@<pre-wipe ts> --preserve-under-name production_pre_wipe`. Development untouched. End freeze. |
| Phase 6 snapshot create fails after delete | Retry; old state covered by the pre-promotion dump. |
| Phase 7 app smoke test fails on production DSN | Rollback per Phase 4/5 row; deployed app still points at old `development`. |
| Phase 9, before freeze ends | Repoint not yet verified → roll back Vercel env to old development DSN; restore production from dump if needed. |
| During grace period (Phases 10–11) | Repoint consumers back to `legacy_development_$DATE` (untouched, endpoint intact); restore `production_pre_promotion` dump into production if prod must be reverted. |
| After legacy deletion | Dumps only: restore `production_pre_promotion_$DATE.dump` into production; `dev_0912/0923` archives into fresh branches off `development`. |
| Render jobs in flight at freeze time | Status rows may be stale; re-submit from the webapp after Phase 9. |

## Future promotion flow (IMPORTANT — read before ever repeating this)

**This dump→wipe→restore procedure is a ONE-TIME bootstrap**, safe only because
production is currently stale and disposable. After this run, production holds live
user data (Better Auth users, songsets, render jobs). Re-running it later would
**destroy that data** (this corrects qwen38's "repeatable runbook" claim).

From now on:

1. Develop on `development`; validate on `staging`.
2. Promote schema via Drizzle migrations (`scripts/migrate.ts` / `drizzle-kit migrate`)
   with the **direct** DSN — `deploy.yml` already does this on merge.
3. Refresh dev data **downward** with `neon branches reset development --parent`
   (and `staging --parent`) — never the reverse. Note: reset is blocked while a branch
   has children, so reset `development` only when childless, or delete/recreate.
4. Keep the single manual snapshot slot for pre-risky-change backups of production;
   anything older than the 6-hour history window lives in the dump + R2 routine
   (worth scripting as `sow-admin maintenance backup-neon` later).

## Risks / notes

- **Freeze is procedural, not enforced** — no webapp maintenance flag exists; verified
  quiescence (Phase 1.5) + zero-drift check (Phase 9.4) are the compensating controls.
- **Snapshot gap** between delete and create is covered by the pre-promotion dump.
- **No branch protection on Free** — nothing prevents accidental writes to production.
  Revisit `protected` + scheduled snapshots (`neon snapshots schedule set`, paid) after
  any plan upgrade.
- **6-hour history window** — older mistakes recoverable only from dumps + R2.
- **Auto-archiving** of dormant `staging`/`development` (Free) — slow first connection
  after a break; not breakage. Deletion of an archived branch still works.
- **Snapshot storage** is separate from the 0.5 GB project cap (~$0.09/GB-month when paid).
- **Preserved branch storage** — legacy branches cost ~171 MB during the grace period;
  calendar the Phase 11 deletion to reclaim it.
- **Ordering invariants to keep in any future revision:** verified freeze before dump →
  dumps+R2 before wipe → test-restore before wipe → verify before snapshot → snapshot
  before smoke → set-default before rename/delete → cutover before freeze ends →
  zero-drift check before freeze ends.
