# Neon: Promote `development` → `production` — v2 (reset-based cutover, write freeze)

**Date:** 2026-09-25
**Project:** Neon project `muddy-mode-84176076` (org `org-polished-hill-46155238`), Free plan
**Status:** Planned — not yet executed
**Supersedes:** `specs/neon-promote-development-to-production.md` (v1, kept unchanged for history)

## Goal

1. Promote the live `development` branch data (~137 MB) into the root `production` branch
   (currently stale, ~34 MB, idle since July).
2. Take a manual snapshot of `production` after promotion.
3. Rebuild the dev flow (`staging` → `development`) from the promoted `production`
   **in place**, via `branches restore`/`reset` — keeping branch IDs, endpoints, and DSNs.

## What changed from v1 (and why)

Findings from the 2026-09-25 review of v1, verified against live Neon state, neonctl 6.0.0,
and repo code:

| # | Change | Driver |
|---|---|---|
| 1 | **Hard write freeze is Phase 0** — includes the deployed Vercel app and the render-worker Lambda, not just host tooling | Vercel's `SOW_DATABASE_URL` points at the **development** endpoint (`ep-patient-paper-ak9vp129`), verified. Anything written after the Phase 1 dump is lost forever once the branch is reset (6-h history doesn't survive reset/delete) |
| 2 | **Keep `staging`/`development`; re-point them with `branches restore`/`reset --parent`** instead of delete + recreate | (a) Neon refuses to delete the **default** branch — v1 Phase 5 would jam mid-destruction; (b) deleting `development` takes the live Vercel site down; (c) reset preserves endpoints → `/opt/sow/.env` and `/opt/sow/.env_webapp` (both pin `ep-patient-paper-ak9vp129`) keep working with zero env churn |
| 3 | `set-default production` moved **before** the restructure | Metadata-only; unblocks everything downstream |
| 4 | `pg_restore`: **no `--jobs 4`** on custom-format dumps (fallback path) | Parallel restore requires directory format; v1's command fails *after* production was already wiped |
| 5 | Env var name corrected to **`SOW_DATABASE_URL`** everywhere; Vercel repoint is a hard, verified step | Code reads `SOW_DATABASE_URL` (`delivery/webapp/src/db/index.ts`, render-worker `db.py`) — v1 checklist said `DATABASE_URL`, which would add a dead var and leave the deployment on the old branch |
| 6 | Verification uses **exact `count(*)`** diffs, not `n_live_tup` estimates | `n_live_tup` diverges on freshly restored DBs → false gate failures |
| 7 | App **smoke test moved after the snapshot** | Keeps the snapshot a clean copy of the promoted data (no Better Auth session rows) |
| 8 | **Server-side rollback artifacts** via `--preserve-under-name`; optional **server-side promotion path** (`neon branches restore production development`) tested first | Stronger than local-only dumps; may collapse Phase 2 to a one-liner (verify at runtime, dump/restore is the fallback) |
| 9 | DSN files: mode-600 under `output/neon-backups/` (not world-readable `/tmp`), shredded at cleanup | Secret hygiene |
| 10 | Backups copied **off-host (R2)** at the end | Single local disk was the only copy |
| 11 | `dev_0904`: delete **without** a dump | Confirmed disposable (user decision, 2026-09-25) |
| 12 | Render-worker Lambda (SQS trigger) paused during the freeze | It writes render-job status to the DB |

## Current state (verified 2026-09-25)

```
production  (br-falling-waterfall-akxdv9x0)  ROOT, default=false, ep-summer-water-ak5jwwyh, ~34 MB stale
└─ staging  (br-delicate-recipe-akkavkdx)    ep-lucky-cloud-akuzu8e5, ~34 MB idle
   └─ development  (br-jolly-wildflower-akl5skg9)  DEFAULT + current (.neon context), ep-patient-paper-ak9vp129, ~137 MB LIVE
      ├─ dev_0904  (br-dark-mountain-akaz444w)  ARCHIVED — disposable, delete without dump
      ├─ dev_0912  (br-shy-sunset-akf0hpqi)     ready, dormant (forked 09-12) — dump, then delete
      └─ dev_0923  (br-flat-thunder-akueu9a6)   ready, dormant (forked 09-22) — dump, then delete
```

- Snapshots: exactly one — `snap-spring-hill-akdendjj` (production, 2026-05-15, manual).
- Server: Postgres 17. Single role project-wide: `neondb_owner` → `--no-owner --no-privileges`
  is safe (no custom grants exist to lose).
- History retention (Free): **6 hours** — PITR is not a safety net; dumps + preserved branches are.
- DB consumers and their pinned endpoints:
  | Consumer | Env var | Current target |
  |---|---|---|
  | Deployed webapp (Vercel) | `SOW_DATABASE_URL` | **development** (`ep-patient-paper-ak9vp129`) |
  | Render-worker Lambda | `SOW_DATABASE_URL` | development (verify at runtime) |
  | Host tooling — `pnpm dev`, admin CLI, songset constructor, analysis-service compose, render-worker dev compose (`/opt/sow/.env`, `/opt/sow/.env_webapp`) | `SOW_DATABASE_URL` | development (`ep-patient-paper-ak9vp129`) |
  | Android app | — | webapp JSON APIs only, no DB |

## Decisions (confirmed with user, 2026-09-25)

| Decision | Choice |
|---|---|
| Production's existing data | Wipe & replace with development data (safety dump + server-side preserve first) |
| Free-plan snapshot slot (1 max, used) | Delete May-15 snapshot, create new one back-to-back |
| dev_0904 | Delete, **no dump** (confirmed disposable) |
| dev_0912 / dev_0923 | Archive as local `pg_dump`, then delete |
| staging / development | **Reset in place** to promoted production — do NOT delete/recreate |
| Write window | **Hard freeze** for the whole run (Vercel + Lambda + host tooling) |
| Vercel after run | Repoint `SOW_DATABASE_URL` → **production pooled DSN** (hard step, verified — otherwise production goes stale again) |

## Constraints & facts driving the design

- **No re-parenting.** Data moves "up" to the root only via dump→restore, or (verify at
  runtime) server-side `neon branches restore production development`. `development` can
  never become the root branch itself.
- **`restore`/`reset` move data *down/across* to an existing branch** and preserve the
  branch ID and its compute endpoint → DSNs survive. This is what makes the in-place
  rebuild possible.
- **Snapshots are root-branch-only** → target `production` after the data lands there.
- **Free plan = 1 manual snapshot**; delete old + create new back-to-back.
- **Default branch cannot be deleted** — irrelevant in v2 (nothing default is deleted),
  but `set-default production` still runs before the restructure.
- **Local `pg_dump` is 16.15; server is PG 17** → `postgresql-client-17` (or `postgres:17`
  Docker) required before any dump/restore.
- **Free plan = 0.5 GB storage/project.** End state: production ~137 MB + preserved old
  development ~137 MB (its pages are not shared with the restored production) + CoW
  staging/development ≈ **~275 MB** ✓. Delete the preserved branch after a soak period to
  reclaim. The ~137 MB snapshot is separate storage.
- **Protected branches are paid-only** — nothing prevents accidental writes to production.
- **neonctl 6.0.0 verified** to provide: `branches restore <target> <source>[@(ts|lsn)]`,
  `branches reset <branch> --parent`, both with `--preserve-under-name`, plus
  `branches set-default`, `snapshots list/create/delete`, `connection-string`, `checkout`.

## Safety artifacts

Local dumps — `output/neon-backups/` (`output/*` already gitignored, `.gitignore:282`),
mode 600, created under `umask 077`:

| File | Purpose |
|---|---|
| `production_pre_promotion_$RUN_DATE.dump` | Rollback: production's pre-promotion state |
| `development_source_$RUN_DATE.dump` | The promotion payload (source of truth) |
| `dev_0912_archive_$RUN_DATE.dump` | Archival of dormant branch |
| `dev_0923_archive_$RUN_DATE.dump` | Archival of dormant branch |
| `baseline_dev_counts.txt` / `verify_prod_counts.txt` | Exact row-count baseline & verification |

Server-side preserved branches (created by `--preserve-under-name`, survive the run):

| Branch | Contents | Disposition |
|---|---|---|
| `production_pre_promotion_$RUN_DATE` | Old production (only if Path A is used in Phase 2) | Delete after soak (~1–2 weeks) |
| `development_pre_promotion_$RUN_DATE` | Old development at reset time | Delete after soak (~1–2 weeks) |

---

## Phase 0 — Preconditions & write freeze

1. Install/verify PG 17 client tools:
   ```bash
   sudo apt-get install -y postgresql-client-17
   pg_dump --version    # must be ≥ 17
   ```
   Fallback: run dump/restore inside `docker run --rm -i postgres:17 …`.
2. `NEON_API_KEY` set; `neon branch list` works; branch IDs match the table above.
3. `RUN_DATE=$(date +%Y%m%d)`; `mkdir -p output/neon-backups`; `umask 077`.
4. **FREEZE all writers to `development`:**
   - [ ] **Vercel (hard freeze, fail-closed):** temporarily set `SOW_DATABASE_URL` to an
         invalid value and redeploy, so the deployed app cannot write. (Alternative: run
         in a known low-traffic window and accept soft-freeze — record the choice here:
         ______.) The Phase 8 redeploy with the production DSN brings the site back.
   - [ ] **Render-worker Lambda:** disable its SQS trigger (it writes job status to the DB).
   - [ ] **Host tooling:** stop `pnpm dev`, analysis-service compose, render-worker dev
         compose; no admin-CLI / songset-constructor runs during the whole procedure.
5. **Verify the freeze** — no foreign sessions on development:
   ```sql
   SELECT usename, application_name, client_addr, state
   FROM pg_stat_activity
   WHERE datname = current_database() AND pid <> pg_backend_pid();
   ```
   Expect zero rows (or only your own psql). If anything is connected, find and stop it
   before proceeding.

## Phase 1 — Safety dumps & baseline

```bash
# mode-600 DSN files inside the gitignored backup dir (never /tmp, never in logs)
neon connection-string production  > output/neon-backups/.dsn_prod
neon connection-string development > output/neon-backups/.dsn_dev
neon connection-string dev_0912    > output/neon-backups/.dsn_0912
neon connection-string dev_0923    > output/neon-backups/.dsn_0923
PROD_DSN=$(cat output/neon-backups/.dsn_prod)   # unpooled; pg_dump must NOT use -pooler
DEV_DSN=$(cat output/neon-backups/.dsn_dev)

pg_dump --format=custom --no-owner --no-privileges \
  --file "output/neon-backups/production_pre_promotion_${RUN_DATE}.dump" "$PROD_DSN"
pg_dump --format=custom --no-owner --no-privileges \
  --file "output/neon-backups/development_source_${RUN_DATE}.dump" "$DEV_DSN"
pg_dump --format=custom --no-owner --no-privileges \
  --file "output/neon-backups/dev_0912_archive_${RUN_DATE}.dump" "$(cat output/neon-backups/.dsn_0912)"
pg_dump --format=custom --no-owner --no-privileges \
  --file "output/neon-backups/dev_0923_archive_${RUN_DATE}.dump" "$(cat output/neon-backups/.dsn_0923)"
```

(dev_0904: intentionally **not** dumped — confirmed disposable.)

Verify each dump: `pg_restore --list <file>` exits 0 and lists the expected tables.

**Optional but recommended:** full test-restore `development_source_*.dump` into a scratch
branch (`neon branches create --name restore_test --parent development`, restore, spot
check, delete). `--list` only proves readability; this dump is the only local copy of the
live data.

Baseline (run on **development**, save outputs):

```sql
SELECT extname FROM pg_extension;
SELECT nspname FROM pg_namespace
 WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema';
```

Exact per-table counts (generator → execute → save):

```bash
COUNT_SQL="SELECT format('SELECT %L AS tbl, count(*) FROM %I.%I;',
             schemaname || '.' || relname, schemaname, relname)
           FROM pg_stat_user_tables ORDER BY relname;"
psql "$DEV_DSN" -At -c "$COUNT_SQL" | psql "$DEV_DSN" -At \
  > output/neon-backups/baseline_dev_counts.txt
```

## Phase 2 — Promote production

**Path A (preferred — try first; server-side, no local round-trip):**

```bash
neon branches restore production development --preserve-under-name "production_pre_promotion_${RUN_DATE}"
```

- Restores production to development's head; old production is preserved under a new
  branch name (server-side rollback, no dump needed for rollback).
- **Verify at runtime** that restore accepts a *descendant* branch as source and is
  available on the Free plan. If the command errors or the result fails Phase 3 →
  fall back to Path B (and delete the preserved branch if one was created).

**Path B (fallback — dump/restore, corrected from v1):**

1. On **production**, wipe:
   ```sql
   -- first: SELECT extname FROM pg_extension;  → DROP EXTENSION IF EXISTS <each non-plpgsql> CASCADE;
   DROP SCHEMA public CASCADE;
   CREATE SCHEMA public;
   -- recreate any additional schemas found on development in Phase 1
   ```
2. Restore (custom format ⇒ **single-threaded**; no `--jobs`):
   ```bash
   pg_restore --no-owner --no-privileges \
     --dbname "$PROD_DSN" \
     "output/neon-backups/development_source_${RUN_DATE}.dump"
   ```
   Ownership maps to the connecting role (`neondb_owner` project-wide — safe).

## Phase 3 — Verify production (read-only gate)

1. Extensions + schema list on production == Phase 1 baseline.
2. Exact counts match:
   ```bash
   psql "$PROD_DSN" -At -c "$COUNT_SQL" | psql "$PROD_DSN" -At \
     > output/neon-backups/verify_prod_counts.txt
   diff output/neon-backups/baseline_dev_counts.txt output/neon-backups/verify_prod_counts.txt
   ```
   (`count(*)` is exact — no ANALYZE needed; do NOT use `n_live_tup` here.)
3. **No app smoke test yet** — it writes session rows; that happens in Phase 7, after the
   snapshot, so the snapshot stays a clean copy.
4. Do **not** proceed until everything matches. `development` is still untouched and
   frozen — it remains the fallback source.

## Phase 4 — Make production the default

```bash
neon branches set-default production
neon branch list    # production shows [default]
```

## Phase 5 — Snapshot production

Back-to-back, minimizing the unprotected window (Free plan = 1 slot):

```bash
neon snapshots delete snap-spring-hill-akdendjj
neon snapshots create --branch production --name "production-promoted-${RUN_DATE}"
# no --expires-at → kept until manually deleted
neon snapshots list    # exactly one snapshot, source = production, ~137 MB
```

## Phase 6 — Rebuild the dev flow in place (reset/restore)

Children first (deletion does not cascade; dumps for 0912/0923 exist from Phase 1):

```bash
neon branches delete dev_0904    # disposable per decision — no dump
neon branches delete dev_0912    # archived locally in Phase 1
neon branches delete dev_0923    # archived locally in Phase 1
```

Re-point staging and development at the promoted production **in place** (branch IDs and
endpoints are preserved):

```bash
neon branches restore staging production
neon branches restore development production \
  --preserve-under-name "development_pre_promotion_${RUN_DATE}"
```

(Equivalent alternative: `neon branches reset staging --parent` then
`neon branches reset development --parent --preserve-under-name …` — reset pulls from the
parent's head; restore with an explicit source avoids any parent-hop ordering concerns.)

**Contingency:** if restore/reset of `staging` is refused because it has a child
(`development`), fall back for staging only: delete `development` (no longer default after
Phase 4, so deletion is now allowed), `neon branches restore staging production`, then
`neon branches create --name development --parent staging`. Note this fallback gives
development a **new endpoint** → `/opt/sow/.env` and `/opt/sow/.env_webapp` must then be
updated (Phase 8.3).

Verify:

```bash
neon branch list                       # production (root, default), staging, development, preserved branch(es)
neon connection-string development | grep -o 'ep-[a-z0-9-]*'   # MUST still be ep-patient-paper-ak9vp129
neon connection-string staging     | grep -o 'ep-[a-z0-9-]*'   # MUST still be ep-lucky-cloud-akuzu8e5
```

If an endpoint changed, the "zero env churn" property is void → update
`/opt/sow/.env` + `/opt/sow/.env_webapp` (and anything else caching the old DSN) in Phase 8.

Spot-check data: re-run the count generator against development; counts == production.

## Phase 7 — Smoke test (post-snapshot; writes are fine now)

1. Run the webapp locally with `SOW_DATABASE_URL` = production's **pooled** DSN
   (`neon connection-string production --pooled`): sign in, load `/songsets`, open a
   songset page.
2. Run the webapp via `/opt/sow/.env` (untouched → development endpoint): same checks;
   data should be identical to production.
3. If the smoke test fails badly: roll back (see matrix) — development's old state is
   still available both as the preserved branch and the local dump.

## Phase 8 — Repoint consumers & unfreeze

1. **Vercel (hard step — this is what keeps production from going stale again):**
   ```bash
   # SOW_DATABASE_URL (NOT DATABASE_URL) → production POOLED DSN
   vercel env rm SOW_DATABASE_URL production
   neon connection-string production --pooled | vercel env add SOW_DATABASE_URL production
   # redeploy (this also ends the Phase 0 fail-closed freeze)
   ```
   Verify the live site: sign in, `/songsets`, one songset page — against production.
2. **Render-worker Lambda:** set `SOW_DATABASE_URL` → production pooled DSN; re-enable the
   SQS trigger; confirm one job processes end-to-end (status rows land in production).
3. **Host tooling:** `/opt/sow/.env`, `/opt/sow/.env_webapp` — **no change needed** if
   Phase 6 confirmed `ep-patient-paper-ak9vp129` survived; otherwise update them to the
   new development DSN. `neon checkout development` (context already pins it by name).
4. **Android:** no change (webapp APIs only).
5. Unfreeze: restart any host services that were stopped.

## Phase 9 — Final state check & cleanup

```bash
neon branch list      # production (root, default), staging, development,
                      # development_pre_promotion_$RUN_DATE (+ production_pre_promotion_$RUN_DATE if Path A)
neon snapshots list   # one snapshot of production, ~137 MB
```

Target topology:

```
production  (root, default, ~137 MB live data, snapshotted)
└─ staging
   └─ development   (same endpoint ep-patient-paper-ak9vp129 as before)
development_pre_promotion_$RUN_DATE   (preserved rollback, delete after ~1–2 week soak)
```

- [ ] Copy `output/neon-backups/*.dump` to R2 (or another off-host location) — the local
      disk must not be the only copy. Keep at least `production_pre_promotion` and
      `development_source` until the next successful promotion cycle.
- [ ] Shred DSN files: `shred -u output/neon-backups/.dsn_*`.
- [ ] Calendar reminder: delete the preserved branch(es) after the soak period (reclaims
      ~137 MB of the 0.5 GB project cap).
- [ ] Verify storage headroom: `neon inspect` / console → project storage ≈ 275 MB < 500 MB.

---

## Rollback

| Failure point | Rollback |
|---|---|
| Phase 2 Path A rejected / Path B restore errors | Path A left old production preserved: `neon branches restore production production_pre_promotion_$RUN_DATE`. Path B: re-wipe production, `pg_restore production_pre_promotion_$RUN_DATE.dump`. Development untouched & frozen either way. |
| Phase 3 verification fails | Same as above. Do not snapshot, do not restructure. |
| After Phase 5, before Phase 6 | Same as above; delete the new snapshot first if a re-promotion will need the slot. |
| After Phase 6 (staging/development reset) | `neon branches restore development development_pre_promotion_$RUN_DATE` (server-side), or restore `development_source_$RUN_DATE.dump` into development. Staging: `neon branches restore staging development`. |
| After Phase 8 (Vercel repointed) | Data issues: fix forward from dumps/preserved branches, or temporarily repoint Vercel `SOW_DATABASE_URL` back to development's DSN (endpoint unchanged) while sorting out. |

## Risks / notes

- **Path A unproven:** `branches restore` from a *descendant* source may be rejected (API
  restriction or Free-plan gating). Test it first; Path B is fully specified. If Path A
  half-applies, production's pre-state is preserved under `production_pre_promotion_*`.
- **Endpoint-preservation assumption:** reset/restore are documented to keep the branch's
  compute endpoint, but Phase 6 verifies it explicitly. If endpoints change, the Phase 6
  contingency + Phase 8.3 env updates cover it.
- **Freeze completeness:** the riskiest silent writer is the deployed Vercel app (real
  users). The fail-closed freeze (invalid DSN + redeploy) makes writes impossible; a
  soft "low-traffic window" freeze carries residual risk of losing in-run writes.
- **Snapshot gap:** brief window between `snapshots delete` and `snapshots create`;
  production is idle and frozen, exposure minimal. If `create` fails after the delete,
  the project has zero snapshots until retried — retry immediately.
- **Preserved-branch storage:** ~137 MB extra against the 0.5 GB cap until the soak-period
  deletion (~275 MB total — fits, but don't forget the cleanup reminder).
- **6-hour history window:** mistakes older than 6 h are recoverable only from the local
  dumps / R2 copies / preserved branches.
- **No branch protection on Free** — nothing prevents accidental writes to production.
  Revisit `protected` + backup schedules after any plan upgrade.
- **Repeatable runbook:** future promotions repeat Phases 0–9; if Path A holds, Phase 2 +
  Phase 6 are three `neon branches restore` commands. Consider scripting Phases 1–5 as an
  admin-cli command later.
