# Ops/Delivery/Lab Repo Reorganization (v2)

> Revisions over `specs/ops-delivery-lab-reorganization.md`: resolves
> ambiguous `app.db` ownership, fills test-coverage gaps
> (`tests/services/analysis`, `tests/poc`, root `tests/conftest.py`),
> specifies per-subproject `uv.lock` strategy, fixes poc script imports,
> and adds `AGENTS.md` + `report/` to the docs-update list.
> This is a new spec only — do not edit `specs/runtime-tree-reorganization.md`
> or the original `specs/ops-delivery-lab-reorganization.md`.

## Summary

Reorganize the repository into three clear top-level component groups:

- `ops/`: operational/backend tooling — Admin CLI and Analysis Service.
- `delivery/`: user-facing delivery/runtime systems — Webapp and Render Worker.
- `lab/`: experimental and deprecated runnables — POC scripts, `sow-app`, legacy CLI/TUI.

Clean break. Root-level Python commands such as
`uv run --extra admin sow-admin ...` and `uv run --extra app sow-app ...`
are intentionally dropped in favor of `--project`-scoped commands.

## Target Layout

```text
ops/
  admin-cli/
    pyproject.toml
    uv.lock
    src/stream_of_worship/
      admin/
      db/                 # owns ALL shared DB/auth/schema/app-db code
        app/              # moved from src/stream_of_worship/app/db/*
    scripts/
    tests/
      conftest.py         # moved from root tests/conftest.py
      admin/
      db/
      poc/                # moved from root tests/poc/
  analysis-service/
    pyproject.toml
    uv.lock
    docker-compose.yml
    src/sow_analysis/
    tests/
      *.py                # service-local tests (from services/analysis/tests/)
      integration/
        *.py              # merged from root tests/services/analysis/*

delivery/
  webapp/                 # (unchanged structure, moved)
  render-worker/          # (unchanged structure, moved)

lab/
  poc-scripts/
    pyproject.toml        # path-deps ops/admin-cli
    uv.lock
    docker/               # all-in-one workflows
    tests/
  sow-app/
    pyproject.toml
    uv.lock
    src/sow_lab_app/
    tests/
  legacy-cli-tui/
    pyproject.toml
    uv.lock
    src/sow_legacy_cli_tui/
    tests/
```

Root-level monorepo files retained only when they coordinate multiple components:

- `README.md`, `CLAUDE.md`, `AGENTS.md`, `DEVELOPER.md`
- `pnpm-workspace.yaml`, `pnpm-lock.yaml`
- `.github/workflows/*`
- `uv.lock` is **removed** from root; each Python subproject owns its own lock.

## Key Dependency Decision: `app.db` Ownership

The `stream_of_worship.app.db` package is imported by BOTH the lab-destined app
AND active admin code (`admin/db/postgres_schema.py` imports `app.db.schema` +
`app.db.user_data_schema`; admin tests + poc scripts import
`app.db.read_client` / `songset_client` / `models`).

**Decision: move ALL of `app.db` into Admin-owned `stream_of_worship.db.app.*`:**

| Source (current)                                            | Destination (new)                                          |
|-------------------------------------------------------------|-------------------------------------------------------------|
| `src/stream_of_worship/app/db/models.py`                     | `ops/admin-cli/src/stream_of_worship/db/app/models.py`      |
| `src/stream_of_worship/app/db/read_client.py`               | `ops/admin-cli/src/stream_of_worship/db/app/read_client.py` |
| `src/stream_of_worship/app/db/songset_client.py`            | `ops/admin-cli/src/stream_of_worship/db/app/songset_client.py` |
| `src/stream_of_worship/app/db/schema.py`                    | `ops/admin-cli/src/stream_of_worship/db/app/schema.py`      |
| `src/stream_of_worship/app/db/user_data_schema.py`          | `ops/admin-cli/src/stream_of_worship/db/app/user_data_schema.py` |

Consequences (rewrite these import sites):
- `src/stream_of_worship/db/postgres_schema.py:28-39` → import from `stream_of_worship.db.app.schema` / `.user_data_schema`.
- Admin test files importing `stream_of_worship.app.db.read_client` / `songset_client` / `models` (`tests/admin/test_client.py:206,335`) → `stream_of_worship.db.app.*`.
- Lab `sow-app` internal imports of `stream_of_worship.app.db.*` → `stream_of_worship.db.app.*` (resolved via path dep on `ops/admin-cli`).
- POC script imports of `stream_of_worship.app.db.read_client` (4 files in `poc/`) → `stream_of_worship.db.app.read_client` (resolved via lab/poc-scripts path dep on `ops/admin-cli`).
- Existing `app/db/__init__.py` is dropped (namespace removed from lab app).

Admin becomes the single source of truth for shared DB/auth/schema helpers.
Lab projects reference them only through `tool.uv.sources` path dependencies on
`../../ops/admin-cli` — never by copying active code.

## Implementation Changes

### Ops: Admin CLI

- Create `ops/admin-cli/pyproject.toml` from the root project metadata.
  - Project name stays `stream-of-worship` (imports stay `stream_of_worship.*`).
  - Define `[project.scripts] sow-admin = "stream_of_worship.admin.main:cli_entry"`.
  - Carry only the extras Admin actually uses: `admin`, `postgres`, `scraper`, `test`, and the admin-relevant slices of `transcription` / `lrc_eval` / `poc_qwen3_asr` / `score_lrc_base` / `fix_lrc` that Admin commands invoke. Drop `app`, `tui`, `video`, `song_analysis`, `stem_separation`, `poc_qwen3_mlx` (these move to lab projects).
  - Set `[tool.uv] package = true` (root is `package = false`).
  - `requires-python = ">=3.11"`, keep Black/Ruff line-length 100, `target-version = "py311"`.
  - Keep `[tool.setuptools.packages.find] where = ["src"]` and `package-data` for any assets Admin still references; otherwise remove the `assets/fonts/*` package-data entry (assets move to lab).
- Move `src/stream_of_worship/admin` → `ops/admin-cli/src/stream_of_worship/admin`.
- Move `src/stream_of_worship/db` → `ops/admin-cli/src/stream_of_worship/db` (auth_models, auth_schema, connection, helpers, postgres_schema, user_client).
- Apply the `app.db` → `db.app` migration above.
- Remove Admin runtime imports of `stream_of_worship.app.*`:
  - `admin/commands/audio.py:2284,3524,4101,5376,5457` (`app.services.asset_cache`) → Admin-owned `stream_of_worship.admin.services.asset_cache` (create it; port the `AssetCache` behavior currently living in `app/services/asset_cache.py`).
  - `admin/commands/audio.py:4121` (`app.services.playback`) → already-existing `stream_of_worship.admin.services.playback` (admin already has a `services/playback.py`).
- Move `tests/admin` and `tests/db` → `ops/admin-cli/tests/admin`, `ops/admin-cli/tests/db`.
- Move root `tests/conftest.py` → `ops/admin-cli/tests/conftest.py` (it provides `postgres_url` + `seed_user` fixtures importing `stream_of_worship.db.connection` / `user_client`; update the `src_dir` path insertion to point at `ops/admin-cli/src`).
- Move `scripts/populate_songs_batch.py` → `ops/admin-cli/scripts/`. Update its embedded shell commands from
  `PYTHONPATH=src uv run --python 3.11 --extra admin python -m stream_of_worship.admin.main ...`
  to
  `uv run --project ops/admin-cli --extra admin python -m stream_of_worship.admin.main ...`
  (drop the `PYTHONPATH=src` prefix — the new package install resolves imports).

Canonical Admin commands:

```bash
uv run --project ops/admin-cli --extra admin sow-admin --help
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest ops/admin-cli/tests -v
```

### Ops: Analysis Service

- Move `services/analysis` → `ops/analysis-service`.
- Keep package name `sow_analysis`; keep its own `uv.lock` (already exists at `services/analysis/uv.lock`).
- Update service-local docs (`DEPLOYMENT.md`, `DEVELOPER.md`, `README.md`, `start-dev.sh`), `docker-compose.yml`, `Dockerfile`, and `scripts/` internal references from `services/analysis` paths.
- Keep Analysis Service dependencies isolated from Admin CLI.

**Test merge (resolved gap):** Analysis Service currently has two test trees:

| Current                                         | Destination                                          |
|-------------------------------------------------|------------------------------------------------------|
| `services/analysis/tests/*.py` (9 service-local) | `ops/analysis-service/tests/*.py`                   |
| `tests/services/analysis/*.py` (10 integration)  | `ops/analysis-service/tests/integration/*.py`        |
| `tests/services/analysis/conftest.py`            | `ops/analysis-service/tests/integration/conftest.py` |

- Audit `tests/services/analysis/conftest.py` + the integration tests for imports of `stream_of_worship.db.*` (they exist because integration tests reuse the shared DB helpers). Rewrite these to import from the Admin-owned `stream_of_worship.db.*` namespace, and add a `tool.uv.sources` path dep on `../../ops/admin-cli` in `ops/analysis-service/pyproject.toml` (analysis test extra only) — or, if the integration tests only need `ConnectionProvider`, vendor a minimal local helper to avoid coupling. Prefer the path-dep approach for fidelity.
- Delete the now-empty `tests/services/` directory.

Canonical Analysis Service command:

```bash
cd ops/analysis-service && docker compose up -d
cd ops/analysis-service && docker compose config   # smoke
```

### Delivery: Webapp

- Move `webapp` → `delivery/webapp`. Keep package name `sow-webapp`.
- Update `pnpm-workspace.yaml` from `webapp` to `delivery/webapp`.
- Update CI/deploy workflow working directories + path filters (see CI section).
- Update `delivery/webapp/vercel.json` `rootDir`/output settings if they hardcode `webapp`. Update `delivery/webapp/DEPLOY-VERCEL.md`.
- Move `webapp/AGENTS.md`, `webapp/CLAUDE.md` → `delivery/webapp/AGENTS.md`, `delivery/webapp/CLAUDE.md`.
- Update deployment tests under the moved webapp test tree that assert workflow paths or `webapp/` locations.

Canonical Webapp commands:

```bash
pnpm --filter sow-webapp dev
cd delivery/webapp && pnpm dev
```

### Delivery: Render Worker

- Move `services/render-worker` → `delivery/render-worker`. Keep package name `sow_render_worker`.
- No `stream_of_worship` imports exist in render-worker (verified) — no cross-component dependency rewrites needed.
- Update README/Docker/deploy scripts and CI/deploy workflow working directories + path filters.

Canonical Render Worker commands:

```bash
cd delivery/render-worker && docker compose up --build
cd delivery/render-worker && PYTHONPATH=src pytest tests/ -v
```

### Lab: POC Scripts

- Move `poc/` contents → `lab/poc-scripts/`.
- Move `tests/poc/` → `lab/poc-scripts/tests/`.
- Keep POC Docker workflows (`docker-compose.allinone.yml`, `Dockerfile`, `Dockerfile.allinone`, `requirements*.txt`) under `lab/poc-scripts/docker/`.
- Give `lab/poc-scripts` its own `pyproject.toml` + `uv.lock`. It path-depends `ops/admin-cli` (`tool.uv.sources` → `../../ops/admin-cli`) and aggregates the poc extras: `transcription`, `lrc_eval`, `poc_qwen3_asr`, `poc_qwen3_mlx`, `poc_qwen3_align`, `score_lrc_base`, `fix_lrc`.
- **Rewrite poc script imports** of `stream_of_worship.app.db.read_client` (files: `poc/eval_lrc.py`, `poc/gen_lrc_qwen3_asr_mvsep.py`, `poc/gen_lrc_qwen3_asr_mvsep_force_align_v2.py`, `poc/gen_lrc_youtube.py`, `poc/utils.py`) → `stream_of_worship.db.app.read_client` (resolved through the admin path dep).
- Update POC README/runnable examples to reference `lab/poc-scripts`.

### Lab: `sow-app`

- Move `src/stream_of_worship/app` (minus the `app/db/` package, which moves to Admin per the key decision) → `lab/sow-app/src/sow_lab_app/`.
- Move `tests/app` → `lab/sow-app/tests`.
- Rename internal imports `stream_of_worship.app.*` → `sow_lab_app.*` across the moved tree.
- Imports of the (now Admin-owned) DB helpers become `stream_of_worship.db.app.*` and `stream_of_worship.db.*`, resolved via path dep on `ops/admin-cli`.
- Give `lab/sow-app` its own `pyproject.toml` + `uv.lock`:
  - Project name `sow-lab-app`; `[project.scripts] sow-app = "sow_lab_app.main:cli_entry"`.
  - Extras: `app`, `tui`, `video`, `song_analysis`, `stem_separation`.
  - `tool.uv.sources` path dep: `stream-of-worship = { path = "../../ops/admin-cli" }`.

Canonical `sow-app` command:

```bash
uv run --project lab/sow-app sow-app --help
```

### Lab: Legacy CLI/TUI

- Move `src/stream_of_worship/cli`, `core`, `ingestion`, `tui` → `lab/legacy-cli-tui/src/sow_legacy_cli_tui/` (subpackages `cli/`, `core/`, `ingestion/`, `tui/`).
- Move in-package tests `src/stream_of_worship/tests` → `lab/legacy-cli-tui/tests`.
- Move legacy scripts that depend on `core`/`ingestion`:
  - `scripts/generate_lrc.py` (imports `stream_of_worship.ingestion.lrc_generator` + `core.paths` + `core.catalog`) → `lab/legacy-cli-tui/scripts/` (preferred) or `lab/poc-scripts/` if treated as standalone experiments.
  - `scripts/migrate_song_library.py` (imports `core.paths` + `core.catalog`) → same destination.
  - Rewrite their imports to the `sow_legacy_cli_tui.*` namespace.
- Keep command name `stream-of-worship`.
- Give `lab/legacy-cli-tui` its own `pyproject.toml` + `uv.lock`:
  - Project name `sow-legacy-cli-tui`; `[project.scripts] stream-of-worship = "sow_legacy_cli_tui.cli.main:main"`.
  - Extras: `tui`, `migration`, plus whatever the legacy code needs.
  - If the legacy TUI/CLI also needs DB helpers, add the same path dep on `ops/admin-cli`.

Canonical legacy command:

```bash
uv run --project lab/legacy-cli-tui stream-of-worship --help
```

### Assets

- Move `src/stream_of_worship/assets/fonts/` → `lab/sow-app/src/sow_lab_app/assets/fonts/` (video_engine in the lab app consumes fonts; primary consumer).
  - If the legacy TUI also references fonts, audit before the move and either duplicate into `lab/legacy-cli-tui/` or share via path dep — do not silently break it.
- Update font path helpers to use `importlib.resources` (package-resource lookup) instead of hardcoded `src/stream_of_worship/assets/...` paths.
- Add `[tool.setuptools.package-data] sow_lab_app = ["assets/fonts/*"]` to `lab/sow-app/pyproject.toml`.
- Remove the `stream_of_worship = ["assets/fonts/*"]` package-data entry from `ops/admin-cli/pyproject.toml` if no Admin code references fonts (audit `admin/services/` first).

### Root Cleanup

- Remove root `pyproject.toml` + root `uv.lock` **only after** `ops/admin-cli`, `lab/sow-app`, `lab/legacy-cli-tui`, and `lab/poc-scripts` each have complete project metadata, working commands, and passing smoke tests.
- Remove the now-empty `src/stream_of_worship/` tree (after `admin/`, `db/`, `app/`, `cli/`, `core/`, `ingestion/`, `tui/`, `assets/`, `tests/` are all relocated).
- Remove empty `services/`, `tests/` directories once contents are migrated.
- Update `.gitignore` for moved caches/build outputs/runtime artifacts under new paths (`ops/*/`, `delivery/*/`, `lab/*/`).
- `docker/.dockerignore`: currently the only file under `docker/`. Confirm no Docker workflow references it as a build context; if redundant, delete; otherwise move to the relevant Docker context (`ops/analysis-service/` or `lab/poc-scripts/docker/`).
- Preserve unrelated worktree changes.

## Lock File Strategy

- **Per-subproject `uv.lock`.** Each Python subproject owns its own lock:
  - `ops/admin-cli/uv.lock` (new, regenerated)
  - `ops/analysis-service/uv.lock` (moved from `services/analysis/uv.lock`)
  - `delivery/render-worker/` — uses `requirements.txt` (no uv.lock currently; leave as-is)
  - `lab/sow-app/uv.lock` (new)
  - `lab/legacy-cli-tui/uv.lock` (new)
  - `lab/poc-scripts/uv.lock` (new)
- Remove root `uv.lock`.
- No root uv workspace — full isolation.

## Documentation And CI

Update authoritative docs and CI only:

- `README.md`
- `CLAUDE.md`
- `AGENTS.md` (root — references all old paths/commands in Development Commands and Architecture & Structure sections; must be rewritten to the new `ops/`/`delivery/`/`lab/` layout and `--project` commands)
- `DEVELOPER.md`
- `delivery/webapp/AGENTS.md` + `delivery/webapp/CLAUDE.md` (moved)
- service-local READMEs/deployment docs under `ops/analysis-service/` and `delivery/render-worker/`
- `.github/workflows/ci.yml`
- `.github/workflows/deploy.yml`
- deployment tests under the moved webapp test tree

### CI Updates (`.github/workflows/ci.yml`)

- `paths:` filter → `delivery/webapp/**`, `delivery/render-worker/**`.
- `webapp-lint-and-test` job `working-directory: webapp` → `delivery/webapp`.
- `render-worker-test` job `working-directory: services/render-worker` → `delivery/render-worker`.

### Deploy Updates (`.github/workflows/deploy.yml`)

- `paths:` trigger → `delivery/webapp/**`, `delivery/render-worker/**`.
- `detect-changes` step: rewrite the two `[[ "$file" == ... ]]` comparisons to `delivery/webapp/*` and `delivery/render-worker/*`; rename internal var `render_worker` leakage check accordingly.
- `migrate-db` job `working-directory: webapp` → `delivery/webapp`. Confirm `scripts/migrate.ts` still resolves under the new webapp path.
- `deploy-render-worker` job `working-directory: services/render-worker` → `delivery/render-worker`. The Docker build context (`.`, run from the working dir) is unchanged in effect.

Do not globally rewrite historical specs or reports (`specs/runtime-tree-reorganization.md`, original `specs/ops-delivery-lab-reorganization.md`, `report/*` dated records). They may keep old paths.

## Report / Memory Update (per AGENTS.md mandate)

After implementation, before `git push`, update:

- `report/current_impl_status.md` — note the reorganization phase completion and new canonical commands.
- `MEMORY` — append the new component layout + command cheat-sheet.

## Test Plan

Admin import smoke (verifies `app.db → db.app` refactor worked):

```bash
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --extra admin python -c \
  "from stream_of_worship.admin.main import app; from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS; from stream_of_worship.db.app.read_client import ReadOnlyClient; print('ok')"
```

Admin tests:

```bash
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest ops/admin-cli/tests -v
```

Analysis Service smoke + tests:

```bash
cd ops/analysis-service && docker compose config
cd ops/analysis-service && PYTHONPATH=src pytest tests/ -v          # service-local
cd ops/analysis-service && PYTHONPATH=src pytest tests/integration -v # merged root tests
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
uv run --project lab/poc-scripts python -c "from stream_of_worship.db.app.read_client import ReadOnlyClient; print('poc db ok')"
uv run --project lab/sow-app sow-app --help
uv run --project lab/legacy-cli-tui stream-of-worship --help
```

Graphify update after implementation:

```bash
graphify update .
```

Final implementation completion (mandatory per AGENTS.md):

```bash
git pull --rebase
git push
git status   # MUST show "up to date with origin"
```

## Assumptions

- New spec; does not modify `specs/runtime-tree-reorganization.md` or the original `specs/ops-delivery-lab-reorganization.md`.
- Clean break; old root-level Python command compatibility intentionally dropped.
- Admin owns ALL shared DB/auth/schema helpers — including the entirety of the former `app.db` package (renamed `stream_of_worship.db.app.*`).
- Both `sow-app` and the legacy `stream-of-worship tui` flow become runnable lab projects that reference Admin via `tool.uv.sources` path dependencies — never by copying active code.
- Historical specs/reports do not require global path rewrites.
- Stable runtime package/import names: `stream_of_worship.admin.*`, `stream_of_worship.db.*` (+ `stream_of_worship.db.app.*`), `sow_analysis`, `sow_render_worker`.
- Lab package names (`sow_lab_app`, `sow_legacy_cli_tui`, `sow_lab_poc` if needed) avoid collisions with active Admin's `stream_of_worship` package.
- Per-subproject `uv.lock`; root `uv.lock` removed.
- `tests/services/analysis/` merges into `ops/analysis-service/tests/integration/`.
- `tests/conftest.py` moves to `ops/admin-cli/tests/conftest.py`; `tests/poc/` moves to `lab/poc-scripts/tests/`.
