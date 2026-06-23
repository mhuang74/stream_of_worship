# Ops/Delivery/Lab Repo Reorganization

## Summary

Reorganize the repository into three clear top-level component groups:

- `ops/`: operational/backend tooling, including Admin CLI and Analysis Service.
- `delivery/`: user-facing delivery/runtime systems, including Webapp and Render Worker.
- `lab/`: experimental and deprecated runnable tools, including POC scripts, `sow-app`, and the legacy CLI/TUI.

This is a clean-break reorganization. Root-level Python commands such as
`uv run --extra admin sow-admin ...` and `uv run --extra app sow-app ...` do not need to remain compatible.
Canonical commands, CI, docs, tests, and deployment paths should be updated to the new layout.

Create this as a new implementation plan only. Do not edit `specs/runtime-tree-reorganization.md`.

## Target Layout

```text
ops/
  admin-cli/
    pyproject.toml
    src/stream_of_worship/
      admin/
      db/
    scripts/
    tests/
  analysis-service/
    pyproject.toml
    docker-compose.yml
    src/sow_analysis/
    tests/

delivery/
  webapp/
  render-worker/

lab/
  poc-scripts/
  sow-app/
  legacy-cli-tui/
```

Keep root-level monorepo files only when they coordinate multiple components, such as:

- `README.md`
- `CLAUDE.md`
- `DEVELOPER.md`
- `pnpm-workspace.yaml`
- `pnpm-lock.yaml`
- `.github/workflows/*`
- `uv.lock` only if a root Python project is intentionally retained; otherwise move or regenerate locks per Python subproject.

## Implementation Changes

### Ops: Admin CLI

- Move the active Admin CLI into `ops/admin-cli/`.
- Move the root Python project metadata into `ops/admin-cli/pyproject.toml`.
- Keep active Python import names as `stream_of_worship.admin.*` and `stream_of_worship.db.*`.
- Move `src/stream_of_worship/admin` to `ops/admin-cli/src/stream_of_worship/admin`.
- Move active shared DB/auth helpers from `src/stream_of_worship/db` to `ops/admin-cli/src/stream_of_worship/db`.
- Treat Admin as the owner of active shared Python DB/auth/schema code.
- Remove Admin runtime imports from `stream_of_worship.app.*`.
  - Move schema/model helpers currently imported from `stream_of_worship.app.db.*` into Admin-owned `stream_of_worship.db.*` modules.
  - Replace Admin uses of `stream_of_worship.app.services.asset_cache` with an Admin-owned equivalent, likely `stream_of_worship.admin.services.asset_cache`.
  - Replace Admin uses of `stream_of_worship.app.services.playback` with `stream_of_worship.admin.services.playback` where behavior already exists.
- Move `tests/admin` and active `tests/db` coverage into `ops/admin-cli/tests`.
- Move `scripts/populate_songs_batch.py` into `ops/admin-cli/scripts/` and update embedded commands to use `uv run --project ops/admin-cli`.

Canonical Admin commands:

```bash
uv run --project ops/admin-cli --extra admin sow-admin --help
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest ops/admin-cli/tests -v
```

### Ops: Analysis Service

- Move `services/analysis` to `ops/analysis-service`.
- Keep the Python package name `sow_analysis`.
- Update service-local docs, Docker Compose paths, deployment scripts, and references from `services/analysis` to `ops/analysis-service`.
- Keep Analysis Service dependencies isolated from Admin CLI dependencies.

Canonical Analysis Service command:

```bash
cd ops/analysis-service && docker compose up -d
```

### Delivery: Webapp

- Move `webapp` to `delivery/webapp`.
- Keep package name `sow-webapp`.
- Update `pnpm-workspace.yaml` from `webapp` to `delivery/webapp`.
- Update root and webapp docs for the new path.
- Update workflow working directories and path filters.
- Update deployment tests that assert workflow paths or `webapp/` locations.
- Update Vercel/root-directory docs to point to `delivery/webapp`.

Canonical Webapp commands:

```bash
pnpm --filter sow-webapp dev
cd delivery/webapp && pnpm dev
```

### Delivery: Render Worker

- Move `services/render-worker` to `delivery/render-worker`.
- Keep the Python package name `sow_render_worker`.
- Update render-worker docs, Docker files, deploy scripts, CI/deploy workflow working directories, and path filters.
- Keep render-worker tests service-local.

Canonical Render Worker commands:

```bash
cd delivery/render-worker && docker compose up --build
cd delivery/render-worker && PYTHONPATH=src pytest tests/ -v
```

### Lab: POC Scripts

- Move existing `poc/` contents to `lab/poc-scripts/`.
- Preserve POC scripts and data that are intentionally tracked.
- Update POC documentation and runnable examples to reference `lab/poc-scripts`.
- Keep POC Docker workflows, including all-in-one analysis workflows, under `lab/poc-scripts/docker/`.

### Lab: `sow-app`

- Move `src/stream_of_worship/app` to a runnable lab project at `lab/sow-app/`.
- Move `tests/app` into `lab/sow-app/tests`.
- Keep the command name `sow-app`.
- Give the lab app its own `pyproject.toml`.
- Avoid publishing or installing another package that conflicts with active Admin’s `stream_of_worship` package.
- Prefer renaming internal imports to a lab-local package name, such as `sow_lab_app.*`.
- If the app still needs Admin-owned DB/R2 helpers, reference `ops/admin-cli` through `tool.uv.sources` or a path dependency instead of copying active code.

Canonical `sow-app` command:

```bash
uv run --project lab/sow-app sow-app --help
```

### Lab: Legacy CLI/TUI

- Move `src/stream_of_worship/cli`, `src/stream_of_worship/core`, `src/stream_of_worship/ingestion`, and `src/stream_of_worship/tui` to `lab/legacy-cli-tui/`.
- Move old in-package tests from `src/stream_of_worship/tests` to `lab/legacy-cli-tui/tests`.
- Move legacy scripts that depend on `core`, such as `scripts/generate_lrc.py` and `scripts/migrate_song_library.py`, into this lab area or into `lab/poc-scripts` if they are better treated as standalone experiments.
- Keep the command name `stream-of-worship`.
- Give the legacy CLI/TUI its own `pyproject.toml`.
- Prefer a lab-local package name, such as `sow_legacy_cli_tui.*`, to avoid collisions with active Admin’s `stream_of_worship` package.

Canonical legacy command:

```bash
uv run --project lab/legacy-cli-tui stream-of-worship --help
```

### Assets

- Move `src/stream_of_worship/assets` with the lab app or legacy CLI/TUI unless implementation discovers an active Admin dependency.
- Update font path helpers to avoid hardcoded `src/stream_of_worship/assets/...` assumptions after the move.
- Prefer package-resource based asset lookup for moved lab projects.

### Root Cleanup

- Remove the root Python project only after `ops/admin-cli`, `lab/sow-app`, and `lab/legacy-cli-tui` have complete project metadata and commands.
- Update `.gitignore` for moved caches, build outputs, and runtime artifacts.
- Handle the tracked `docker/.dockerignore` explicitly:
  - Move it to the relevant Docker context if still useful.
  - Delete it only if it is redundant after confirming no Docker workflow needs it.
- Preserve unrelated worktree changes.

## Documentation And CI

Update authoritative docs and tests only:

- `README.md`
- `CLAUDE.md`
- `DEVELOPER.md`
- `webapp/AGENTS.md`, moved to `delivery/webapp/AGENTS.md`
- `webapp/CLAUDE.md`, moved to `delivery/webapp/CLAUDE.md`
- service-local READMEs and deployment docs
- `.github/workflows/ci.yml`
- `.github/workflows/deploy.yml`
- deployment tests under the moved webapp test tree

Do not globally rewrite historical specs or reports. They may continue to mention old paths as dated records unless they are active developer instructions or are asserted by tests.

## Test Plan

Admin import smoke:

```bash
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --extra admin python -c "from stream_of_worship.admin.main import app; from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS"
```

Admin tests:

```bash
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest ops/admin-cli/tests -v
```

Analysis Service smoke:

```bash
cd ops/analysis-service && docker compose config
```

Webapp checks:

```bash
pnpm install --frozen-lockfile
pnpm --filter sow-webapp lint
pnpm --filter sow-webapp typecheck
pnpm --filter sow-webapp test
```

Render Worker tests:

```bash
cd delivery/render-worker && PYTHONPATH=src pytest tests/ -v
```

Lab command smoke:

```bash
uv run --project lab/sow-app sow-app --help
uv run --project lab/legacy-cli-tui stream-of-worship --help
```

Graphify update after implementation:

```bash
graphify update .
```

Final implementation completion:

```bash
git pull --rebase
git push
git status
```

`git status` must show the branch is up to date with origin after the implementation commit is pushed.

## Assumptions

- This plan is written as a new spec and does not modify `specs/runtime-tree-reorganization.md`.
- The implementation is a clean break; old root-level Python command compatibility is intentionally dropped.
- Admin owns active shared Python DB/auth/schema helpers.
- Both `sow-app` and the legacy `stream-of-worship tui` flow become runnable lab projects.
- Historical specs and reports do not require global path rewrites.
- Package names for active Admin and services stay stable where they are part of runtime code:
  - `stream_of_worship.admin.*`
  - `stream_of_worship.db.*`
  - `sow_analysis`
  - `sow_render_worker`
- Lab package names should avoid collisions with active Admin’s `stream_of_worship` package.
