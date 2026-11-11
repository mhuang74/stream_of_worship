# Switch local code between Production and Dev DB with one command per component

## Context

Local dev must offer two explicit targets instead of editing env files by hand: run the same local code against the Neon **production** branch or the Neon **`dev`** branch.

| Command | Target database |
|---|---|
| `pnpm dev` / `pnpm dev:https` (webapp) | Neon `dev` branch |
| `pnpm prod` / `pnpm prod:https` (webapp) | Neon `production` branch |
| `sow_admin …` (existing alias) | production (`~/.config/stream-of-worship-admin/config.toml`) |
| `sow_admin_dev …` (new function) | Neon `dev` branch |
| `ops/analysis-service/start.sh` (renamed from `start-dev.sh`) | none — service has no Postgres |

Verified facts driving the design:

- Neon project `tiny-feather-38432620`; production branch `br-silent-surf-b3qfgd71` @ `ep-calm-mode-b33utavj.c-4.ap-southeast-1.aws.neon.tech`; `dev` branch `br-lucky-violet-b36bpnpu` @ `ep-crimson-bar-b3se1v53.c-4.ap-southeast-1.aws.neon.tech`. Same role/password works on both. Branch self-check: `SELECT current_setting('neon.branch_id')`.
- `/opt/sow/.env_webapp` (pinned by `SOW_WEBAPP_ENV_FILE` in `~/.zshrc:383`) is the complete webapp env, currently pointing at production. `/opt/sow/.env` is the analysis/admin/render-worker env (production).
- `env-cmd` 11 default precedence is **file wins over already-exported variables** (verified: `SOW_DATABASE_URL=sentinel env-cmd -f /opt/sow/.env_webapp node -p 'process.env.SOW_DATABASE_URL'` prints the file's production DSN; `--no-override` prints `sentinel`). So selecting the file per command pins the DB; do **not** pass `--no-override`.
- `/opt/sow/.env_webapp` contains no `$` or backtick characters, so it can be sourced by zsh literally.
- Admin CLI already lets `SOW_DATABASE_URL` beat `[database].url` (`ops/admin-cli/src/stream_of_worship/admin/config.py:65`), and a fully-qualified DSN (with inline password) skips `SOW_DATABASE_PASSWORD` injection.
- `db url` / `db status` print `config.database_url`, not the resolved override (`commands/db.py:129-160`, `199-228`) — they would report production while connected to `dev`.
- Analysis Service never touches Postgres: `ops/analysis-service/src/sow_analysis/storage/db.py` is `aiosqlite`; no `SOW_DATABASE_URL`/`psycopg` anywhere under `ops/analysis-service`. `start-dev.sh` only runs `docker compose --env-file /opt/sow/.env up analysis-dev`. No source/doc references the filename (only generated `graphify-out/` artifacts, refreshed by `graphify update`).

## Approach

### 1. Create the dev webapp env file `/opt/sow/.env_webapp_dev` (host)

Full copy of the production webapp env with only the DB endpoint swapped, so the two `pnpm` commands differ solely in database:

```bash
{
  printf '# Webapp env pinned to the Neon `dev` branch (br-lucky-violet-b36bpnpu).\n'
  printf '# Used by `pnpm dev` / `pnpm dev:https`. `pnpm prod` keeps /opt/sow/.env_webapp (production).\n'
  sed -E 's#ep-calm-mode-b33utavj#ep-crimson-bar-b3se1v53#' /opt/sow/.env_webapp
} > /opt/sow/.env_webapp_dev
chmod 600 /opt/sow/.env_webapp_dev
```

- Everything else (R2, Better Auth, `SOW_RENDER_WORKER_MODE=sqs`, `SOW_WEBAPP_TESTUSER_*` are zsh exports, not in this file) stays byte-identical to production.
- Consequence of keeping `SOW_RENDER_WORKER_MODE=sqs`: render jobs submitted from the dev webapp still go to the production SQS queue, and the deployed Lambda looks the job up in the production branch, where it does not exist. Dev-stack render submissions therefore do not complete; every other dev-stack flow (browse/search/songsets/editor/playback of existing renders) works.
- Do **not** modify `/opt/sow/.env` or `/opt/sow/.env_webapp` — production stays their target.

### 2. `delivery/webapp/package.json` — split `dev` (dev DB) and `prod` (production DB)

Replace the two dev scripts and add two production twins (lines 7-8):

```json
"dev": "env-cmd -f \"${SOW_WEBAPP_ENV_FILE_DEV:-/opt/sow/.env_webapp_dev}\" next dev -p 8080 -H 0.0.0.0",
"dev:https": "env-cmd -f \"${SOW_WEBAPP_ENV_FILE_DEV:-/opt/sow/.env_webapp_dev}\" next dev -p 8080 -H 0.0.0.0 --experimental-https",
"prod": "env-cmd -f \"${SOW_WEBAPP_ENV_FILE:-/opt/sow/.env_webapp}\" next dev -p 8080 -H 0.0.0.0",
"prod:https": "env-cmd -f \"${SOW_WEBAPP_ENV_FILE:-/opt/sow/.env_webapp}\" next dev -p 8080 -H 0.0.0.0 --experimental-https",
```

- `pnpm prod` runs the local dev server (`next dev`, hot reload) against production data — it is not a deploy and does not run `next build`.
- Each command pins its DB: an exported `SOW_DATABASE_URL` cannot flip `pnpm prod` to dev (env-cmd default, verified). Switching files ad hoc stays possible through `SOW_WEBAPP_ENV_FILE_DEV` / `SOW_WEBAPP_ENV_FILE`.
- `SOW_WEBAPP_ENV_FILE=/opt/sow/.env_webapp` is already exported in `~/.zshrc:383`, so `prod` uses production even there; `SOW_WEBAPP_ENV_FILE_DEV` is optional and defaults to the new file.
- Missing dev file → env-cmd exits non-zero with "Failed to load env file"; no silent fallback to production (intended).

### 3. `ops/analysis-service` — rename `start-dev.sh` → `start.sh`

```bash
git mv ops/analysis-service/start-dev.sh ops/analysis-service/start.sh
```

Then inside the file: header comment `# Start the Analysis Service in development mode` → `# Start the Analysis Service locally (docker compose target analysis-dev)`, banner `=== Analysis Service Development Startup ===` → `=== Analysis Service Startup ===`, and `Starting Analysis Service in development mode...` → `Starting Analysis Service...`. Keep every flag (`--build`, `--log-level`), the `/opt/sow/.env` env file, the `analysis-dev` compose service, and the `SOW_BIND_IP` logic unchanged. No dev/prod pair: the service has no Postgres target to switch.

### 4. `~/.zshrc` — add `sow_admin_dev` next to the existing `sow_admin` alias (line 402)

An alias cannot scope an env var, so use a function that sources the dev env file inside a subshell (caller's shell keeps pointing at production):

```zsh
sow_admin_dev() {
    ( set -a; . /opt/sow/.env_webapp_dev; set +a
      LOG_LEVEL=DEBUG uv run --project ops/admin-cli --extra admin sow-admin "$@" )
}
```

- Same command/cwd contract as the existing `alias sow_admin="LOG_LEVEL=DEBUG uv run --project ops/admin-cli --extra admin sow-admin"` (run from repo root); leave that alias untouched.
- `SOW_DATABASE_URL` from the file overrides `[database].url`; the DSN carries its own password, so `SOW_DATABASE_PASSWORD` is not needed.
- Sourcing also exports R2/analysis keys from that file with the same values already exported in `~/.zshrc`, so no target changes.

### 5. `ops/admin-cli/src/stream_of_worship/admin/commands/db.py` — report the DSN actually used

In `_show_connection_info` replace `masked = _mask_url(config.database_url)` with:

```python
    try:
        resolved_url = config.get_connection_url()
    except ValueError:
        resolved_url = config.database_url
    masked = _mask_url(resolved_url)
```

Apply the same replacement in `show_url` (identical one-liner there). Without this, `sow_admin_dev db url` reports the production host while `db status` connects to `dev`. Leave the `Password` row and `_mask_url` unchanged.

### 6. Docs that define the commands

- `delivery/webapp/README.md` "Environment Setup" (lines 18-25): state that `pnpm dev` / `pnpm dev:https` load `/opt/sow/.env_webapp_dev` (Neon `dev` branch) and `pnpm prod` / `pnpm prod:https` load `${SOW_WEBAPP_ENV_FILE:-/opt/sow/.env_webapp}` (production); `env-cmd`'s default precedence means the selected file beats an exported `SOW_DATABASE_URL`; other files are selected with `SOW_WEBAPP_ENV_FILE_DEV` / `SOW_WEBAPP_ENV_FILE`. Add `pnpm prod` / `pnpm prod:https` to the "Development" command block.
- `delivery/webapp/README.md` also gets three lines: `sow_admin_dev` (admin CLI against `dev`), `ops/analysis-service/start.sh` (service has no Postgres), and the note that dev-stack render submissions still land in the production queue (`SOW_RENDER_WORKER_MODE=sqs`).
- `delivery/webapp/CLAUDE.md` "Development Commands" table (the file `delivery/webapp/AGENTS.md` symlinks to): add rows `| pnpm prod | Local dev server on :8080 against the production DB |` and `| pnpm prod:https | Same, HTTPS (Cast testing) |`.

### 7. Restart any running dev server

`env-cmd` resolves the file once at process start; stop the current `next dev` on :8080 before verifying.

## Critical files & anchors

- `/opt/sow/.env_webapp_dev` — new host env file; `/opt/sow/.env_webapp:5` is the production source it is copied from.
- `delivery/webapp/package.json:7-8` — `dev`, `dev:https`, plus new `prod`, `prod:https`.
- `ops/analysis-service/start-dev.sh` — rename to `start.sh` + three banner/comment strings.
- `~/.zshrc:402` — alias block where `sow_admin_dev` is added.
- `ops/admin-cli/src/stream_of_worship/admin/commands/db.py` — `_show_connection_info` (~129) and `show_url` (~199).

## Verification

**V1 — dev env file is self-consistently dev**

```bash
grep -c 'ep-crimson-bar-b3se1v53' /opt/sow/.env_webapp_dev   # → 1
grep -c 'ep-calm-mode-b33utavj'   /opt/sow/.env_webapp_dev   # → 0
psql "$(grep -m1 '^SOW_DATABASE_URL=' /opt/sow/.env_webapp_dev | sed -E 's/^SOW_DATABASE_URL=//; s/^"//; s/"$//')" \
  -Atc "select current_setting('neon.branch_id')"            # → br-lucky-violet-b36bpnpu
```

**V2 — the scripts pin the right file (no server needed)**

```bash
cd delivery/webapp
pnpm exec env-cmd -f "${SOW_WEBAPP_ENV_FILE_DEV:-/opt/sow/.env_webapp_dev}" node -p 'process.env.SOW_DATABASE_URL' | grep -c ep-crimson-bar-b3se1v53   # → 1
pnpm exec env-cmd -f "${SOW_WEBAPP_ENV_FILE:-/opt/sow/.env_webapp}"       node -p 'process.env.SOW_DATABASE_URL' | grep -c ep-calm-mode-b33utavj     # → 1
SOW_DATABASE_URL=sentinel pnpm exec env-cmd -f /opt/sow/.env_webapp node -p 'process.env.SOW_DATABASE_URL' | grep -c sentinel                      # → 0 (export cannot flip prod)
```

**V3 — webapp writes hit the dev branch, then the production branch**

```bash
PROD_DSN="$(grep -m1 '^SOW_DATABASE_URL=' /opt/sow/.env_webapp | sed -E 's/^SOW_DATABASE_URL=//; s/^"//; s/"$//')"
DEV_DSN="$(grep -m1 '^SOW_DATABASE_URL=' /opt/sow/.env_webapp_dev | sed -E 's/^SOW_DATABASE_URL=//; s/^"//; s/"$//')"
psql "$PROD_DSN" -Atc 'select count(*) from "session"'   # baseline P
psql "$DEV_DSN"  -Atc 'select count(*) from "session"'   # baseline D

cd delivery/webapp && pnpm dev    # keep running
curl -sS -c /tmp/sow-dev.txt -H 'Content-Type: application/json' -H 'Origin: http://localhost:8080' \
  -d "{\"email\":\"$SOW_WEBAPP_TESTUSER_LOGIN\",\"password\":\"$SOW_WEBAPP_TESTUSER_PASSWORD\"}" \
  -o /dev/null -w '%{http_code}\n' http://localhost:8080/api/auth/sign-in/email   # → 200
curl -s -b /tmp/sow-dev.txt -o /dev/null -w '%{http_code}\n' http://localhost:8080/songsets  # → 200
psql "$DEV_DSN"  -Atc 'select count(*) from "session"'   # D+1
psql "$PROD_DSN" -Atc 'select count(*) from "session"'   # unchanged (P)

# stop that server, then repeat against production
pnpm prod    # keep running
curl … (same sign-in with a fresh cookie jar)
psql "$PROD_DSN" -Atc 'select count(*) from "session"'   # P+1
psql "$DEV_DSN"  -Atc 'select count(*) from "session"'   # unchanged (D+1)
```

If Better Auth rejects the curl request, use the browser login recipe in the root `AGENTS.md` ("Webapp visual validation", headless Chrome on CDP :9222 with `--ignore-certificate-errors`; use `pnpm dev:https` + `https://localhost:8080` when testing over https) and compare the same `session` counts.

**V4 — analysis script rename is complete and still valid**

```bash
ls ops/analysis-service/start.sh                                   # exists
ls ops/analysis-service/start-dev.sh                               # → No such file
grep -rn "start-dev\.sh" --exclude-dir=graphify-out --exclude-dir=node_modules .   # → no matches
bash -n ops/analysis-service/start.sh                              # syntax OK
cd ops/analysis-service && docker compose --env-file /opt/sow/.env config >/dev/null   # compose file still resolves
```

**V5 — admin CLI aliases hit different branches (after `exec zsh` or `source ~/.zshrc`, from repo root)**

```bash
zsh -ic 'sow_admin_dev db url'      # → Database URL (masked): …@ep-crimson-bar-b3se1v53…
zsh -ic 'sow_admin_dev db status'   # → Health: Connected, Songs 587, Health Check OK
zsh -ic 'sow_admin db url'          # → …@ep-calm-mode-b33utavj… (production, unchanged)
```

**V6 — changed suites still pass**

```bash
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest -v
cd delivery/webapp && pnpm test
```

## Assumptions & contingencies

- **`dev` branch recreated later** (endpoint changes): re-resolve with `curl -s -H "Authorization: Bearer $NEON_API_KEY" https://console.neon.tech/api/v2/projects/tiny-feather-38432620/endpoints` → take the `host` whose `branch_id` is `br-lucky-violet-b36bpnpu`, then `sed -i 's#ep-crimson-bar-b3se1v53#<new-endpoint-id>#' /opt/sow/.env_webapp_dev` (leave user/password/query string untouched).
- **`dev` branch schema drift**: if `select count(*) from drizzle.__drizzle_migrations` on the dev DSN is below `ls delivery/webapp/drizzle/*.sql | wc -l`, apply migrations with `SOW_DATABASE_URL="<dev DSN>" npx tsx scripts/migrate.ts` from `delivery/webapp` before using `pnpm dev`.
- **If a future `env-cmd` release stops letting the file win**: prepend `env -u SOW_DATABASE_URL` to all four scripts so the file-selected DSN can never be overridden by a stray export.
- **`pnpm prod` writes to production data.** It is intentionally named `prod`; no confirmation prompt is added. `sow_admin` (production via `config.toml`) is left untouched.
