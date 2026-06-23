# Runtime Tree Reorganization

## Summary

Move active runtime code under a single `runtime/` wrapper:

- `runtime/admin/` for the Python Admin CLI and its active dependencies.
- `runtime/webapp/` for the Next.js app.
- `runtime/services/` for `analysis` and `render-worker`.
- `poc/user-app/` and `poc/legacy-tui/` for deprecated Python UI apps, runnable via their own `uv` subprojects.
- Remove the unused top-level `docker/` directory after confirming it contains no tracked files.

Canonical commands become:

```bash
uv run --project runtime/admin --extra admin sow-admin --help
uv run --project runtime/admin --extra admin --extra test pytest runtime/admin/tests -v
pnpm --filter sow-webapp dev
cd runtime/services/analysis && docker compose up -d
cd runtime/services/render-worker && docker compose up --build
uv run --project poc/user-app sow-app run
uv run --project poc/legacy-tui stream-of-worship tui
```

## Key Changes

- Move `src/stream_of_worship/admin` and active shared dependencies into `runtime/admin/src/stream_of_worship/`, keeping the Python import name `stream_of_worship`.
- Move active DB/auth/shared modules from `src/stream_of_worship/db` into `runtime/admin/src/stream_of_worship/db`.
- Extract app-owned but active shared pieces before archiving:
  - `app/db/schema.py` -> `stream_of_worship.db.songset_schema`
  - `app/db/user_data_schema.py` -> `stream_of_worship.db.user_data_schema`
  - `app/db/models.py` -> `stream_of_worship.db.songset_models` if still needed by active tests/schema helpers
  - `app/services/asset_cache.py` -> `stream_of_worship.admin.services.asset_cache`
  - Replace admin imports of `stream_of_worship.app.services.playback` with existing `stream_of_worship.admin.services.playback`
- Update `postgres_schema.py` to import songset/user-data schema from `stream_of_worship.db.*`, not from the deprecated app package.
- Move `webapp/` to `runtime/webapp/`; update `pnpm-workspace.yaml`, CI/deploy workflows, Vercel docs/root-directory references, and deployment-path tests.
- Move `services/analysis` and `services/render-worker` to `runtime/services/analysis` and `runtime/services/render-worker`; update CI/deploy workflow paths, Docker docs, and test path assumptions.
- Move root Python project metadata to `runtime/admin/pyproject.toml`; remove root `sow-app`, `stream-of-worship`, `app`, and `tui` entries. Keep only admin/runtime-relevant extras and the `sow-admin` script.
- Move active Python tests to `runtime/admin/tests`; move deprecated app tests to `poc/user-app/tests`; move old TUI/monolith tests to `poc/legacy-tui/tests` or `poc/legacy-monolith/tests`.
- Archive old monolith code (`cli/`, `core/`, `ingestion/`, old in-package tests) under POC because it supports the deprecated `stream-of-worship` flow, not active runtime.
- Move `scripts/populate_songs_batch.py` to `runtime/admin/scripts/` and update its `PYTHONPATH`/`uv --project` commands. Move legacy `generate_lrc.py` and `migrate_song_library.py` with old monolith/core code under POC.

## POC Subprojects

- Create `poc/user-app/pyproject.toml` with script `sow-app`.
- Rename archived app package imports from `stream_of_worship.app.*` to a POC-local package such as `sow_poc_user_app.*`.
- Depend on `runtime/admin` via `tool.uv.sources` for shared admin/db/R2 code.
- Create `poc/legacy-tui/pyproject.toml` with script `stream-of-worship`.
- Rename archived TUI package imports to a POC-local package such as `sow_poc_legacy_tui.*`, including any old `core` helpers it needs.

## Cleanup

- Delete top-level `docker/` only after verifying `git ls-files docker` is empty and `find docker -type f` shows only unused untracked files.
- Keep `poc/docker/` because it is still referenced by POC all-in-one workflows.
- Update `README.md`, `CLAUDE.md`, `DEVELOPER.md`, relevant docs/specs, and `docs/deprecation_analysis.md` to describe the new runtime layout.
- Update `.gitignore` for moved runtime paths, POC artifacts, and generated caches.
- Preserve unrelated existing worktree changes; do not revert current `.planning`, `reports`, or other dirty-state changes unless explicitly requested.
- After code moves, run `graphify update .`.

## Test Plan

Admin import smoke:

```bash
PYTHONPATH=runtime/admin/src uv run --project runtime/admin --extra admin python -c "from stream_of_worship.admin.main import app; from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS"
```

Admin tests:

```bash
PYTHONPATH=runtime/admin/src uv run --project runtime/admin --python 3.11 --extra admin --extra test pytest runtime/admin/tests -v
```

POC app command smoke:

```bash
uv run --project poc/user-app sow-app --help
uv run --project poc/legacy-tui stream-of-worship --help
```

Web app checks:

```bash
pnpm install --frozen-lockfile
pnpm --filter sow-webapp lint
pnpm --filter sow-webapp typecheck
pnpm --filter sow-webapp test
```

Render worker tests:

```bash
cd runtime/services/render-worker && PYTHONPATH=src pytest tests/ -v
```

Analysis service smoke:

```bash
cd runtime/services/analysis && docker compose config
```

## Assumptions

- New canonical admin usage is `uv run --project runtime/admin ...`; root `uv run --extra admin ...` compatibility is intentionally dropped.
- Python package imports remain `stream_of_worship.*` for active admin code.
- Deprecated UI apps remain runnable only from their POC subprojects.
- The root `pnpm-workspace.yaml` stays at repo root and points to `runtime/webapp`.
- Implementation completion still requires normal repo completion steps: update graphify, run verification, commit, pull/rebase, push, and confirm `git status` is up to date with origin.
