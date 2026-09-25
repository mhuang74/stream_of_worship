# Neon: Promote `development` → `production`, then snapshot `production` (v2)

**Date:** 2026-09-25
**Project:** Neon project `muddy-mode-84176076` (org `org-polished-hill-46155238`), Free plan
**Status:** Planned — not yet executed
**Supersedes:** `specs/neon-promote-development-to-production.md` (v1)

## Why v2 (changes from v1)

Three corrections that were blocking / lossy in v1, plus minor fixes:

1. **The deployed webapp reads the *live* `development` branch today** (not production), and
   `development` is **actively written** (app traffic, Better Auth sessions, admin CLI). v1
   assumed "production idle" and "development is the untouched fallback," and dumped
   `development` mid-write — losing any write that landed after the dump. v2 adds an explicit
   **write-freeze + maintenance window** before dumping.
2. **v1 deleted the default branch.** `neon branches delete development` fails while
   `development` is `default=true` (Neon refuses to delete the default or root branch), and v1
   only ran `set-default production` *after* the deletes. v2 moves `set-default` (and the app
   cutover) **before** deletion.
3. **v1 repointed the app *after* deleting `development`**, so a live app reading `development`
   would be down from delete until repoint. v2 repoints the app to `production` immediately
   after verification, *then* deletes the old tree.

Also corrected: broken DSN sourcing in Phase 1, an incomplete production wipe (non-public
schemas were left dangling), and the outdated "snapshots are root-only" premise.

## Goal

1. Promote the live `development` branch data (~137 MB) into the root `production` branch
   (currently stale, ~34 MB, idle since July) — with **zero lost writes** and **minimal
   app downtime**.
2. Take a manual snapshot of `production` after promotion (the snapshot-of-record).
3. Repoint the deployed app + worker from the old `development` DSN to `production`.
4. Restructure the branch tree so dev flow (`staging` → `development`) hangs off `production`.

## Current state (verified 2026-09-25)

```
production   (br-falling-waterfall-akxdv9x0)  ROOT, default=false, ~34 MB, stale
└─ staging   (br-delicate-recipe-akkavkdx)     ~34 MB, idle
   └─ development (br-jolly-wildflower-akl5skg9)  DEFAULT=true, ~137 MB, LIVE + actively written
      ├─ dev_0904 (br-dark-mountain-akaz444w)  ARCHIVED
      ├─ dev_0912 (br-shy-sunset-akf0hpqi)     ready, dormant (forked 09-12)
      └─ dev_0923 (br-flat-thunder-akueu9a6)   ready, dormant (forked 09-22)
```

Snapshots: exactly one — `snap-spring-hill-akdendjj` (production, 2026-05-15, manual).
Server: Postgres 17. History retention (Free plan): **6 hours** — PITR is NOT a usable safety
net; local `pg_dump` files are the rollback mechanism.

**Load-bearing facts (differ from v1):**
- The **deployed webapp (Vercel) and render worker currently point at `development`'s DSN**.
- `development` receives **live writes** during normal operation.
- `neon branches delete` refuses to delete a **default** or **root** branch. (The API `primary`
  field is deprecated — it is the same thing as `default`.)

## Decisions (confirmed with Matt)

| Decision | Choice |
|---|---|
| Production's existing data | Wipe & replace with development data (safety dump of prod taken first) |
| Free-plan snapshot slot (1 max, already used) | Delete the old May-15 snapshot, then create the new one |
| dev_0904 | Delete (no archive — it is ARCHIVED and has no compute to dump) |
| dev_0912 / dev_0923 | Archive as local `pg_dump` files, then delete |
| Promotion mechanism | `pg_dump`/`pg_restore` (development → production), not snapshot-restore |
| Write handling | **Maintenance window + write-freeze on `development` before dumping** |
| Cutover | Repoint app + worker to production **after** verify, **before** deleting old tree |
| Topology after | `production` (root, default) → `staging` → `development` |

## Constraints & facts driving the design

- **No re-parenting in Neon.** `development` can never become the root; data moves "up" only via
  dump → restore (or snapshot-restore, see Alternative below).
- **You cannot delete the default or root branch.** `set-default production` must precede
  deleting `development`.
- **Branch deletion does not cascade** — children must be deleted first
  ("You cannot delete a branch that has child branches").
- **Free plan = 1 manual snapshot.** Delete old + create new back-to-back.
- **Local `pg_dump` must be ≥ PG 17** (Neon server is PG 17) → `postgresql-client-17` or a
  `postgres:17` Docker container.
- **Free plan = 0.5 GB storage/project.** End state ≈ 150 MB ✓ (snapshot storage is separate).
- **Protected branches are paid-only** — `production` cannot be protected on Free.
- **Root-branch allowance (Free) = 3**; we keep exactly 1 root.
- **(Corrected) Snapshots are NOT root-only** — `neon snapshots create --branch <any>` works on
  any branch. v1's "snapshots are root-branches-only" was outdated. We still choose
  dump/restore because the **1-slot manual-snapshot limit** makes snapshot-restore promotion
  awkward on Free (see Alternative).

## Downtime & data-loss model (new in v2)

- **Freeze point** (Phase 1): the app is placed in maintenance (or writes are otherwise
  stopped) so `development` is quiesced. From the freeze until the Phase 4 cutover, no writes
  land on `development`, so the dump is a consistent snapshot of source-of-truth.
- **App downtime** = Phase 1 freeze → Phase 4 cutover. For ~137 MB this should be minutes
  (dump + restore + verify + repoint). Track it; restart anything that holds a pooled
  connection after the cutover.
- **Rollback** at every point up to Phase 6 keeps the app restorable to `development`
  (which is untouched until Phase 6): simply end the freeze if verification fails.

## Safety artifacts (all local, gitignored)

Dump directory: `output/neon-backups/` (add to `.gitignore` if not already ignored).

| File | Purpose |
|---|---|
| `production_pre_promotion_YYYYMMDD.dump` | Rollback: production's pre-promotion state |
| `development_source_YYYYMMDD.dump` | The promotion payload (source of truth, taken post-freeze) |
| `dev_0912_archive_YYYYMMDD.dump` | Archival of dormant branch |
| `dev_0923_archive_YYYYMMDD.dump` | Archival of dormant branch |

---

## Phase 0 — Preconditions

1. Schedule a maintenance window; prepare to stop/redirect writes to `development`
   (deploy a maintenance page on the webapp, or point it at a read-only mode).
2. Install PG 17 client tools:
   ```bash
   sudo apt-get install -y postgresql-client-17
   pg_dump --version   # MUST print 17.x (a 16.x client fails against a PG 17 server)
   ```
   Fallback: `docker run --rm -i postgres:17 …`.
3. Confirm `NEON_API_KEY` is set and `neon branch list` works.
4. Record current branch IDs (table above) in case names change mid-run.
5. Inventory what reads/writes the DB today: Vercel webapp env (`DATABASE_URL`), render
   worker/Lambda env, admin CLI local `.env`, any cron/scheduled jobs. Each must be repointed
   in Phase 4.

## Phase 1 — Freeze writes + safety dumps

1. **Stop writes to `development`** (maintenance page / read-only). Confirm quiesced: re-run the
   baseline `n_live_tup`/row-count queries twice a few seconds apart; counts must be stable.
2. Resolve DSNs via the CLI (never paste secrets into logs/specs). **`neon connection-string`
   prints a bare `postgres://…` URL, not `KEY=value` — export it directly:**
   ```bash
   mkdir -p output/neon-backups
   export PROD_DSN="$(neon connection-string production)"        # unpooled
   export DEV_DSN="$(neon connection-string development)"        # unpooled
   export DEV0912_DSN="$(neon connection-string dev_0912)"      # wakes dormant compute
   export DEV0923_DSN="$(neon connection-string dev_0923)"      # wakes dormant compute
   ```
3. Dump (unpooled DSNs — never the `-pooler` host for pg_dump/pg_restore):
   ```bash
   pg_dump --format=custom --no-owner --no-privileges \
     --file output/neon-backups/production_pre_promotion_$(date +%Y%m%d).dump "$PROD_DSN"
   pg_dump --format=custom --no-owner --no-privileges \
     --file output/neon-backups/development_source_$(date +%Y%m%d).dump "$DEV_DSN"
   pg_dump --format=custom --no-owner --no-privileges \
     --file output/neon-backups/dev_0912_archive_$(date +%Y%m%d).dump "$DEV0912_DSN"
   pg_dump --format=custom --no-owner --no-privileges \
     --file output/neon-backups/dev_0923_archive_$(date +%Y%m%d).dump "$DEV0923_DSN"
   ```
4. Verify each dump: `pg_restore --list <file>` exits 0 and lists expected tables.
5. Record on **development**: extension list, non-system schema list (`\dn`), and row counts of
   key tables — the Phase 3 verification baseline:
   ```sql
   SELECT extname FROM pg_extension;
   SELECT nspname FROM pg_namespace
     WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema';
   SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname;
   ```

## Phase 2 — Wipe & restore production

1. On **production**, drop **all** non-system schemas **and** non-`plpgsql` extensions so
   nothing stale survives (v1 only dropped `public`, which left other schemas dangling and
   colliding on restore):
   ```sql
   -- enumerate first:
   SELECT extname FROM pg_extension;                      -- DROP each non-plpgsql, CASCADE
   SELECT nspname FROM pg_namespace
     WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema';  -- DROP each, CASCADE
   -- then:
   DROP SCHEMA public CASCADE;
   CREATE SCHEMA public;
   ```
2. Restore the development dump into production:
   ```bash
   pg_restore --no-owner --no-privileges --jobs 4 \
     --dbname "$PROD_DSN" \
     output/neon-backups/development_source_$(date +%Y%m%d).dump
   ```
   Ownership maps to the connecting role (`--no-owner`); grants are skipped
   (`--no-privileges`). Confirm the webapp/render worker do **not** use a second DB role whose
   grants this would drop (single-role is assumed on Neon).

## Phase 3 — Verify production

1. Re-run the Phase 1 baseline queries on **production**; row counts, table list, and extensions
   must match development.
2. Smoke test: run the webapp locally with `DATABASE_URL` = production pooled DSN
   (`neon connection-string production --pooled`) — sign in, load `/songsets`, open a songset.
3. **Do not proceed until verification passes.** `development` is untouched at this point; the
   app can be trivially rolled back to it.

## Phase 4 — Cutover: repoint app + set default (before any delete)

Do this **after** verify and **before** touching the old tree — this is the key reorder vs v1.

1. Repoint the app to production's pooled DSN (production's endpoint is unchanged by the wipe —
   only its data changed):
   - [ ] Vercel webapp `DATABASE_URL` → `production` **pooled** DSN
   - [ ] Render worker / Lambda `DATABASE_URL` → `production` pooled DSN (if it reads DB directly)
   - [ ] Admin CLI local `.env` → production pooled (or leave on dev until Phase 7 repoint; see
         note) DSN
   - [ ] Confirm live traffic now hits production (logs/row counts move).
2. Make production the default branch (**must precede** deleting `development`):
   ```bash
   neon branches set-default production
   ```
3. End the maintenance window. From here the app writes to `production`.

> Note: the admin CLI can remain pointed at old `development` until Phase 7, but no **writes**
> should go to `development` after the cutover or they will be lost when the branch is deleted.

## Phase 5 — Snapshot production (snapshot-of-record)

Back-to-back, minimizing the unprotected window. Production is now the live branch:

```bash
neon snapshots delete snap-spring-hill-akdendjj
neon snapshots create --branch production --name production-promoted-20260925
```

Confirm `neon snapshots list` → exactly one snapshot, source = production, ~137 MB.
Take the snapshot **before** deleting the old tree (Phase 6).

## Phase 6 — Delete the old tree (now safe)

The default has moved to production and no app points at these branches, so deletion is safe
and follows the no-children rule (leaf → root):

```bash
neon branches delete dev_0904        # archived; no compute, no dump needed
neon branches delete dev_0912        # archived locally in Phase 1
neon branches delete dev_0923        # archived locally in Phase 1
neon branches delete development     # OK now: no longer default; data lives in production
neon branches delete staging
```

> If the "Branch Recovery" preview is enabled on this account, deleted branches stay recoverable
> for 7 days, which may reserve the `staging`/`development` names and block recreation. Verify
> the feature is off, or plan renamed intermediates before Phase 7.

## Phase 7 — Rebuild the dev flow

```bash
neon branches create --name staging    --parent production
neon branches create --name development --parent staging
```

New branches get read-write computes by default (scale-to-zero on Free). **The new `development`
gets a new compute endpoint → new DSN.**

## Phase 8 — Repoint local/CI dev context + final state check

1. Re-pin local context to the **new** `development` branch:
   ```bash
   neon checkout development   # updates .neon, pulls new branch's env (new DSN)
   ```
2. Refresh anything that cached the old development DSN: admin CLI `.env`, CI env, local
   `webapp/.env.local`.
3. Final check:
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
| Freeze/dump bad (Phase 1) | End maintenance; app resumes on `development`. Nothing changed. |
| Phase 2/3 restore bad or verification fails | Re-wipe production and restore `production_pre_promotion_*.dump`. App still on `development`; end maintenance. |
| After cutover (Phase 4), before snapshot/delete (5–6) | Repoint app back to `development` DSN and `set-default development`; production's promoted data remains but is unused. |
| After Phase 6 (old tree deleted) | Data rollback: restore pre-promotion dump into production. Dormant-branch data: restore `dev_0912/0923` archive dumps into fresh branches off `development`. |

## Risks / notes

- **Write-frozen window is the safety margin.** Any write to `development` after the Phase 1
  freeze is lost from the promoted production; keep the maintenance window enforced until the
  Phase 4 cutover.
- **Snapshot gap:** brief window between `snapshots delete` and `snapshots create` with no
  snapshot. Local dumps cover it; both run in one sitting.
- **No branch protection on Free** — nothing prevents accidental writes to production. Revisit
  `protected` + backup schedules after a plan upgrade.
- **6-hour history** — mistakes >6h old are recoverable only via `output/neon-backups/`. Keep the
  pre-promotion dump until the next successful promotion.
- **Repeatable runbook:** future promotions repeat Phases 1–8 (minus dormant-branch archiving).
  Consider scripting Phases 1–5 as an admin-cli command later.

## Alternative considered: native snapshot-restore (rejected on Free, but worth upgrading to)

Neon's official dev→prod promotion is `snapshots create --branch development` →
`snapshots restore <id> --target-branch production --finalize`. It is instant/atomic, preserves
the production endpoint, and auto-creates an `old production` backup branch — strictly safer than
`pg_dump`/`pg_restore` (no partial restore). It is rejected here **only** because the Free plan's
1-slot manual-snapshot limit forces a delete→create juggle with a no-snapshot gap, and restoring
onto a parent-with-children requires `preserve_under_name`. Re-evaluate it after any plan upgrade.