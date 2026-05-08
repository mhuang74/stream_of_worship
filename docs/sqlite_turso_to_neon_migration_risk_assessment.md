# SQLite/Turso to Neon Postgres Migration — Risk Assessment

**Date:** 2026-05-08
**Source Document:** `specs/sqlite_turso_to_neon_migration_runbook_v2.md`
**Status:** Review completed against runbook v2

---

## Executive Summary

The runbook is well-structured for a sole-user offline migration and includes strong verification gates, integrity checks, and rollback logic. However, there are **high-severity gaps** around source capture freshness, target branch immutability, and load atomicity that could lead to data loss or irreversible cutover failure if not addressed before execution.

---

## Scope of Assessment

This risk assessment focuses exclusively on:
- Data integrity and loss scenarios during migration
- Operational failure modes during cutover and rollback
- Gaps in the runbook that could prevent a clean rollback or recovery

It does not cover:
- Performance tuning of the Neon target
- Long-term Neon cost optimization
- Feature refactors unrelated to the migration

---

## Data Loss Risks

| # | Risk | Phase | Severity | Mitigation |
|---|------|-------|----------|------------|
| 1 | **Stale source capture** — The local admin replica is accepted as source-of-truth without a mandatory freshness check. A skipped or incomplete Turso sync would silently omit recent writes. | Phase 1 | **High** | Always prefer Turso primary export; if using local replica, require a sync timestamp check and explicit operator sign-off. |
| 2 | **Partial database load** — `02_load_data.py` is not explicitly required to be atomic or idempotent. A mid-load network drop could leave the target partially populated, creating ambiguity during retry. | Phase 5 / 7 | **High** | Run loader inside a single transaction, or implement a clear "already loaded" sentinel so retries are safe. |
| 3 | **Invalid JSON in text columns** — JSON-like text columns (e.g., `lyrics_lines`, `beats`, `sections`) may contain malformed data. If mapped to `jsonb`, the load aborts; if left as `text`, downstream parsing may fail later. | Phase 2 / 3 | **Medium** | Add a pre-load JSON validity scan in `02_load_data.py` and abort with specific row IDs if invalid. |
| 4 | **Accidental Neon writes before rollback** — The runbook acknowledges this but offers no concrete tool to diff/export post-cutover changes. If abandoned, those writes are lost. | Phase 8 | **Medium** | Provide a concrete script that exports rows changed after a given cutover timestamp to JSON/CSV. |
| 5 | **`sync_metadata` omission** — If any tool relies on this table for sync-state or freshness logic, dropping it could cause silent misbehavior after cutover. | Phase 3 | **Low** | Audit app/admin code for any `sync_metadata` reads before excluding it. |

---

## Operational Risks

| # | Risk | Phase | Severity | Mitigation |
|---|------|-------|----------|------------|
| 1 | **In-place `production` branch recreation** — Phase 7 accepts destroying the `production` branch as a target. This is irreversible and removes the Neon-side baseline for comparison or instant revert. | Phase 7 | **High** | Mandate a fresh `cutover-<timestamp>` branch for every production load. Never destroy the existing `production` branch until acceptance. |
| 2 | **No active-writer verification** — The operator is told to close apps, but no command validates that nothing still holds the admin DB file open. | Phase 1 | **Medium** | Add an explicit `lsof`/`fuser` or SQLite lock-check step before `.backup`. |
| 3 | **Missing config backup step** — Rollback requires restoring legacy config, but the runbook does not back up existing config files before editing. Under pressure, manual reconstruction is error-prone. | Phase 7 / 8 | **Medium** | Add a mandatory step to copy `config.toml` and env files into `specs/migration/snapshots/` before any changes. |
| 4 | **Neon scale-to-zero cold starts** — As a sole-user TUI, the app may experience 100–500 ms cold-start latency after idle suspension, degrading UX unexpectedly. | Phase 7 | **Medium** | Document cold-start behavior and test UX in smoke tests; consider disabling auto-suspend during stabilization. |
| 5 | **DSN mix-ups** — Four DSNs exist (prod/staging x admin/app). Manual env switching creates risk of pointing `sow-admin` at a read-only or staging DSN. | Phase 0 / 7 | **Medium** | Use clearly named env vars and validate write permissions in an early smoke-test step. |
| 6 | **`songsets.db` reference drift** — Songsets remain on SQLite while the catalog moves to Postgres. Future code that assumes co-location may fail. | Phase 4 / 7 | **Low** | Document the dual-database topology as a follow-up item and audit for any cross-db joins. |

---

## Severity Definitions

- **High:** Risk of irreversible data loss, corrupted target state, or inability to rollback. Must be mitigated before migration execution.
- **Medium:** Risk of operational failure, degraded experience, or extra recovery work. Should be mitigated or explicitly accepted with documented workarounds.
- **Low:** Risk of future technical debt or minor inconvenience. Can be addressed during stabilization.

---

## Recommendations (Priority Order)

1. **Mandate a `cutover-<timestamp>` branch** for every production load; remove the "acceptable: recreate `production`" option.
2. **Require atomic/idempotent data load** with a clear transaction boundary or retry-safe sentinel in `02_load_data.py`.
3. **Add a pre-load JSON validity scan** on all JSON-like text columns, failing fast with row identifiers.
4. **Backup legacy config files** before any DSN switch in Phase 7.
5. **Verify quiescence** with `lsof` or an SQLite lock-check before taking the final snapshot.
6. **Provide a concrete accidental-write export script** for Phase 8 that uses a cutover timestamp.
7. **Document and test Neon scale-to-zero cold starts** during app smoke tests.
8. **Audit `sync_metadata` consumers** in app/admin code before deciding to exclude it.

---

## Cross-Reference to Runbook Phases

- **Phase 1** (Capture): Operational Risks #1, #2
- **Phase 2** (Inventory): Data Loss Risk #3
- **Phase 3** (Schema): Data Loss Risk #5, Operational Risk #6
- **Phase 5 / 7** (Staging / Cutover): Data Loss Risk #2, Operational Risk #1
- **Phase 8** (Rollback): Data Loss Risk #4, Operational Risk #3
- **Phase 9** (Hardening): Operational Risk #4 (cold start)

---

*Reviewed against `specs/sqlite_turso_to_neon_migration_runbook_v2.md` (495 lines), dated 2026-05-08.*
