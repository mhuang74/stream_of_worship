# Switch local code between Production and Dev DB with one command per component (v2)

> v2 of `separate_dev_and_prod_db_for_local_testing.md`. Changes: a fail-closed Neon-branch guard for the three destructive admin commands, render-submission consequence corrected (SQS redelivery → DLQ), `sow_admin_dev` sources only the DSN, `db url`/`db status` show the Neon branch, and a local UI badge (`NEXT_PUBLIC_DB_TARGET`) makes the active DB target visible.

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
- **Destructive admin commands use the connected DB branch as the R2-safety oracle while the bucket is always the production one.** `maintenance purge-r2-waste` deletes every bucket prefix that has no recording row *in the connected branch* (`commands/maintenance.py:598-614,650-700`); `maintenance purge-soft-deletes` hard-deletes the connected-branch row and then `r2_client.delete_prefix()`s the shared bucket (`maintenance.py:375-389`), so a dev-branch purge destroys production audio while the production row stays live; `db init --wipe-songsets` runs `DROP TABLE IF EXISTS songset_items, songsets CASCADE` on the connected branch (`db/client.py:193-194`). `R2Client` reads its credentials from `SOW_R2_ACCESS_KEY_ID` / `SOW_R2_SECRET_ACCESS_KEY` (`services/r2.py:109-148`) — the same values in both webapp env files, one bucket. Nothing in the admin CLI is branch-aware today: `current_setting('neon.branch_id')` appears nowhere in source.
- **Dev render submissions mount up in production infrastructure.** The dev webapp enqueues to the production SQS queue; the deployed Lambda connects to the production branch, `get_render_job` returns `None`, and the pipeline raises `ValueError("Render job … not found")` (`delivery/render-worker/src/sow_render_worker/pipeline.py:217-219`). SQS→Lambda deletes a message only on successful invocation, so every dev submission redelivers until `maxReceiveCount` and lands in the DLQ — repeated production Lambda invocations per dev submit, not a single silent no-op.
- **The branch oracle works through the exact endpoint hosts the app uses** (verified live: `select current_setting('neon.branch_id', true)` → `br-silent-surf-b3qfgd71` on `ep-calm-mode-b33utavj…`, `br-lucky-violet-b36bpnpu` on `ep-crimson-bar-b3se1v53…`, both non-NULL through Neon's pooled endpoint), so the guard can rely on it. The host check in the guard exists for the failure case — a pooler config change, a network error, or a malformed DSN all yield an unreadable branch id — and fails closed rather than allowing the delete.
- Admin CLI test layout: command tests live in `ops/admin-cli/tests/admin/` (e.g. `test_publish_guard.py`, `test_audio_soft_delete_maintenance.py`, `test_r2_backup_commands.py`), with `db` command tests under `ops/admin-cli/tests/admin/commands/test_db_commands.py`; `MagicMock`/`patch` client doubles and `typer.testing.CliRunner` are the established style. `ops/admin-cli/pyproject.toml:105` sets `addopts = "-m 'not integration'"`, so the testcontainers-backed `TestDbInit` class there is skipped by default — new guard tests belong in an unmarked module.
- Webapp test pattern for env-dependent modules: `vi.resetModules()` + `await import(…)` per case — see `delivery/webapp/src/test/lib/render/dispatcher.test.ts:80-122` and `src/test/lib/r2/public-url.test.ts:1-15`; component tests render with `@testing-library/react` under `jsdom` (`vitest.config.ts:8`, `src/test/setup.ts`).
- The webapp `dev` / `dev:https` scripts already wrap `next dev` in `env-cmd -f "${SOW_WEBAPP_ENV_FILE:-/opt/sow/.env}"` (`delivery/webapp/package.json:7-8`), and `~/.zshrc:383` exports `SOW_WEBAPP_ENV_FILE=/opt/sow/.env_webapp`, so dev currently reads the production webapp env by accident of that export. v1's "byte-identical `next dev` invocations" description of those lines is stale; step 2 below changes their default path rather than introducing the wrapper.

## Approach

### 1. Create the dev webapp env file `/opt/sow/.env_webapp_dev` (host)

Full copy of the production webapp env with only the DB endpoint swapped, so the two `pnpm` commands differ solely in database:

```bash
{
  printf '# Webapp env pinned to the Neon `dev` branch (br-lucky-violet-b36bpnpu).\n'
  printf '# Used by `pnpm dev` / `pnpm dev:https`. `pnpm prod` keeps /opt/sow/.env_webapp (production).\n'
  sed -E -e '/^NEXT_PUBLIC_DB_TARGET=/d' -e 's#ep-calm-mode-b33utavj#ep-crimson-bar-b3se1v53#' /opt/sow/.env_webapp
  printf '\n# Local UI target badge (client component; non-empty value renders the chip)\nNEXT_PUBLIC_DB_TARGET=dev\n'
} > /opt/sow/.env_webapp_dev
chmod 600 /opt/sow/.env_webapp_dev
grep -q '^NEXT_PUBLIC_DB_TARGET=' /opt/sow/.env_webapp || printf '\nNEXT_PUBLIC_DB_TARGET=production\n' >> /opt/sow/.env_webapp
```

- Everything else (R2, Better Auth, `SOW_RENDER_WORKER_MODE=sqs`, `SOW_WEBAPP_TESTUSER_*` are zsh exports, not in this file) stays byte-identical to production.
- The `sed '/^NEXT_PUBLIC_DB_TARGET=/d'` branch drops any marker copied from the production file, so re-running the block never yields two conflicting values in the dev file. Run the block in the order shown: the dev copy is made **before** the production marker line is appended.
- `/opt/sow/.env_webapp` receives exactly one appended marker line (`NEXT_PUBLIC_DB_TARGET=production`) and is otherwise untouched; no consumer other than the badge reads it. When the var is unset (Lambda/serverless deployment), the badge renders nothing. `/opt/sow/.env` is not modified.
- Consequence of keeping `SOW_RENDER_WORKER_MODE=sqs`: dev render submissions still go to the production SQS queue, and the deployed Lambda looks the job up in the production branch, where it does not exist. Dev render submissions therefore **never complete** — and since SQS→Lambda deletes a message only on success, each submission redelivers until `maxReceiveCount` and lands in the DLQ, with repeated production Lambda invocations in between. Do **not** submit render jobs from a dev session. Every other dev-stack flow (browse/search/songsets/editor/playback of existing renders) works.

### 2. `delivery/webapp/package.json` — split `dev` (dev DB) and `prod` (production DB)

Replace the two dev scripts (lines 7-8) so their default file is the dev env, and add two production twins:

```json
"dev": "env-cmd -f \"${SOW_WEBAPP_ENV_FILE_DEV:-/opt/sow/.env_webapp_dev}\" next dev -p 8080 -H 0.0.0.0",
"dev:https": "env-cmd -f \"${SOW_WEBAPP_ENV_FILE_DEV:-/opt/sow/.env_webapp_dev}\" next dev -p 8080 -H 0.0.0.0 --experimental-https",
"prod": "env-cmd -f \"${SOW_WEBAPP_ENV_FILE:-/opt/sow/.env_webapp}\" next dev -p 8080 -H 0.0.0.0",
"prod:https": "env-cmd -f \"${SOW_WEBAPP_ENV_FILE:-/opt/sow/.env_webapp}\" next dev -p 8080 -H 0.0.0.0 --experimental-https",
```

- `pnpm prod` runs the local dev server (`next dev`, hot reload) against production data — it is not a deploy and does not run `next build`.
- Each command pins its DB: an exported `SOW_DATABASE_URL` cannot flip `pnpm prod` to dev (env-cmd default, verified). Switching files ad hoc stays possible through `SOW_WEBAPP_ENV_FILE_DEV` / `SOW_WEBAPP_ENV_FILE`.
- `SOW_WEBAPP_ENV_FILE=/opt/sow/.env_webapp` is already exported in `~/.zshrc:383`, so `prod` uses production even there; `SOW_WEBAPP_ENV_FILE_DEV` is optional and defaults to the new file. Note that this export is exactly why today's `dev` script lands on production data — do not remove it.
- Missing dev file → env-cmd exits non-zero with "Failed to load env file"; no silent fallback to production (intended).

### 3. `ops/analysis-service` — rename `start-dev.sh` → `start.sh`

```bash
git mv ops/analysis-service/start-dev.sh ops/analysis-service/start.sh
```

Then inside the file: header comment `# Start the Analysis Service in development mode` → `# Start the Analysis Service locally (docker compose target analysis-dev)`, banner `=== Analysis Service Development Startup ===` → `=== Analysis Service Startup ===`, and `Starting Analysis Service in development mode...` → `Starting Analysis Service...`. Keep every flag (`--build`, `--log-level`), the `/opt/sow/.env` env file, the `analysis-dev` compose service, and the `SOW_BIND_IP` logic unchanged. No dev/prod pair: the service has no Postgres target to switch.

### 4. `~/.zshrc` — add `sow_admin_dev` next to the existing `sow_admin` alias (line 402)

An alias cannot scope an env var, so use a function that extracts **only** the DSN from the dev env file inside a subshell (caller's shell keeps pointing at production):

```zsh
sow_admin_dev() {
    ( DSN="$(grep -m1 '^SOW_DATABASE_URL=' /opt/sow/.env_webapp_dev | sed -E 's/^SOW_DATABASE_URL=//; s/^"//; s/"$//')"
      SOW_DATABASE_URL="$DSN" LOG_LEVEL=DEBUG uv run --project ops/admin-cli --extra admin sow-admin "$@" )
}
```

- Same command/cwd contract as the existing `alias sow_admin="LOG_LEVEL=DEBUG uv run --project ops/admin-cli --extra admin sow-admin"` (run from repo root); leave that alias untouched.
- `SOW_DATABASE_URL` from the file overrides `[database].url`; the DSN carries its own password, so `SOW_DATABASE_PASSWORD` is not needed.
- Only `SOW_DATABASE_URL` (plus `LOG_LEVEL=DEBUG`) is exported, so the Better Auth secret, AWS render-CI credentials, and the Resend/embedding keys living in the webapp env file never enter the admin process. R2 credentials still come from the same `~/.zshrc`/config sources as today, so no target changes.

### 5. `ops/admin-cli/src/stream_of_worship/admin/commands/db.py` — report the DSN actually used, and the Neon branch

In `_show_connection_info` replace `masked = _mask_url(config.database_url)` with:

```python
    try:
        resolved_url = config.get_connection_url()
    except ValueError:
        resolved_url = config.database_url
    masked = _mask_url(resolved_url)
```

Apply the same replacement in `show_url` (identical one-liner there). Without this, `sow_admin_dev db url` reports the production host while `db status` connects to `dev`. Leave the `Password` row and `_mask_url` unchanged.

Additionally, `_show_connection_info` gains an optional client parameter so the table states which Neon branch it is actually talking to:

```python
def _show_connection_info(
    config: AdminConfig, db_client: DatabaseClient | None = None
) -> bool:
```

Directly after the existing `Password` row, add:

```python
    branch = db_client.current_neon_branch() if db_client is not None else None
    info_table.add_row("Neon branch", branch or "n/a")
```

`init_db` passes its live client (`_show_connection_info(config, db_client=client)`, inside the `with client:` block). `show_status` currently calls `_show_connection_info` *before* building a client; reorder so one client serves both the info table and the stats, keeping both existing messages:

```python
    try:
        client = _get_db_client(config)
        with client:
            is_healthy = _show_connection_info(config, db_client=client)
            if not is_healthy:
                console.print(
                    "\n[yellow]Database is unreachable. Check your URL and network.[/yellow]"
                )
                return
            _display_stats(client)
    except Exception as e:
        console.print(f"\n[red]Error reading database: {e}[/red]")
        raise typer.Exit(1)
```

`_get_db_client` only wraps the DSN in a `ConnectionProvider` (`commands/db.py:44-54`), so creating it before the health probe opens no connection; `current_neon_branch()` returns `None` when the probe fails. `show_url` stays parameter-less (it builds no connection).

### 6. Neon-branch guard for the lethal three destructive commands

New component lesson: nothing equivalent exists — the admin CLI has no branch awareness today (no `neon.branch_id` reference anywhere in source), so this introduces one small, testable seam rather than sprinkling string checks through commands.

New method on `DatabaseClient` in `ops/admin-cli/src/stream_of_worship/admin/db/client.py` (raw `SELECT` with no commit, matching the existing simple-read methods such as `get_stats`):

```python
    def current_neon_branch(self) -> Optional[str]:
        """Return the Neon branch id of the connected database, or None off-Neon."""
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT current_setting('neon.branch_id', true)")
            row = cursor.fetchone()
            return str(row[0]) if row and row[0] else None
        except psycopg.Error as e:
            logger.warning("Could not read neon.branch_id: %s", e)
            return None
```

(`logging`-based `logger` already exists in that module; `psycopg` and `Optional` are already imported.)

New helper file `ops/admin-cli/src/stream_of_worship/admin/commands/guard.py`:

```python
"""Branch guard for destructive admin commands."""

import os
from urllib.parse import urlparse

import typer
from rich.console import Console

from stream_of_worship.admin.db.client import DatabaseClient

console = Console()
PRODUCTION_BRANCH_ID = "br-silent-surf-b3qfgd71"
NEON_HOST_SUFFIX = ".neon.tech"
OVERRIDE_ENV_VAR = "SOW_ALLOW_DESTRUCTIVE_ON_BRANCH"


def ensure_production_branch(db_client: DatabaseClient, db_url: str) -> None:
    """Abort unless the connection targets the Neon production branch.

    Fail-closed: once the host is known to be Neon, an unreadable branch id
    (pooler that hides ``neon.branch_id``, network failure, empty host) is
    refused — never allowed — because that is exactly the shape of the
    dev-branch data-loss case. Only a host that is *known* to be non-Neon
    (testcontainers, local postgres) skips the check.
    Set SOW_ALLOW_DESTRUCTIVE_ON_BRANCH=1 to bypass.
    """
    host = (urlparse(db_url).hostname or "").lower()
    branch = db_client.current_neon_branch()
    if branch == PRODUCTION_BRANCH_ID:
        return
    if os.environ.get(OVERRIDE_ENV_VAR):
        console.print(
            f"[yellow]Destructive override active: target branch "
            f"{branch or 'unknown'} on {host or 'unknown host'}[/yellow]"
        )
        return
    if host and not host.endswith(NEON_HOST_SUFFIX):
        console.print(
            f"[yellow]Target host {host} is not Neon ({NEON_HOST_SUFFIX}); "
            "branch check not applicable.[/yellow]"
        )
        return
    console.print(
        f"[red]Refusing destructive operation: connected to Neon branch "
        f"{branch or 'unknown'}, not production ({PRODUCTION_BRANCH_ID}).[/red]\n"
        f"If that is intentional, re-run with {OVERRIDE_ENV_VAR}=1."
    )
    raise typer.Exit(1)
```

Guarded callsites — the guard runs only where the command would actually destroy; dry-run stays allowed because wrong-branch reconnaissance leaves data intact. Callers pass the DSN they are connecting with (`config` is already in scope in all three) so the guard can tell Neon from off-Neon hosts:

- `commands/maintenance.py` `purge_soft_deletes`: immediately after `config, db_client = _load_clients(config_path)` / `r2_client = _load_r2(config)` (currently lines 354-355), add `if confirm: ensure_production_branch(db_client, config.get_connection_url())`, with `from stream_of_worship.admin.commands.guard import ensure_production_branch` at the top.
- `commands/maintenance.py` `purge_r2_waste`: same insertion after its `_load_clients` / `_load_r2` lines (currently 655-656). Neither command body wraps its work in `except Exception`, so the guard's message and exit code pass straight through.
- `commands/db.py` `init_db`: **the guard must sit outside the existing `try:`**. `typer.Exit` subclasses `RuntimeError` (verified: `typer.Exit.__mro__` = `Exit, RuntimeError, Exception, …`) and `init_db` ends in `except Exception as e: console.print(f"[red]Failed to initialize schema: {e}[/red]"); raise typer.Exit(1)`, so a guard raise placed inside that block is caught and re-emitted as the useless `Failed to initialize schema: 1`. Restructure the tail of `init_db` to:

```python
    client = _get_db_client(config)
    if wipe_songsets:
        ensure_production_branch(client, config.get_connection_url())
    try:
        with client:
            client.initialize_schema(wipe_songsets=wipe_songsets)
            console.print("[green]Postgres schema initialized successfully![/green]")
            # Display connection info without re-connecting
            _show_connection_info(config, db_client=client)
            # Reuse same connection so Neon's pooler doesn't route us to a stale backend
            _display_stats(client)
    except Exception as e:
        console.print(f"[red]Failed to initialize schema: {e}[/red]")
        raise typer.Exit(1)
```

  Hoisting `client = _get_db_client(config)` above the `try:` is safe because `init_db` has already validated `config.get_connection_url()` a few lines earlier (its own `except ValueError` → exit 1), and `_get_db_client` only builds a lazy `ConnectionProvider`. (`--force` alone merely re-runs idempotent schema statements; it stays unguarded.)

New tests — all in one new **unmarked** file, `ops/admin-cli/tests/admin/test_maintenance_branch_guard.py` (an unmarked test placed in `tests/admin/commands/test_db_commands.py` would drag in that module's testcontainers fixtures, and its `TestDbInit` class is `@pytest.mark.integration`, excluded by `addopts = "-m 'not integration'"` in `ops/admin-cli/pyproject.toml:105`). Follow the `CliRunner` + `MagicMock` conventions of `tests/admin/test_audio_soft_delete_maintenance.py` / `tests/admin/test_r2_backup_commands.py`: patch `_load_clients`/`_load_r2` (or `_get_db_client` for `db init`) with a mock `DatabaseClient` whose `current_neon_branch.return_value` is set per case, and pass a `--config`-free `AdminConfig` whose `get_connection_url()` returns a Neon or localhost DSN. Cases:

| # | branch id returned | DSN host | env | expected |
|---|---|---|---|---|
| 1 | `br-silent-surf-b3qfgd71` | `*.neon.tech` (prod endpoint) | — | proceeds |
| 2 | `br-lucky-violet-b36bpnpu` | `*.neon.tech` (dev endpoint) | — | `exit_code == 1` + "Refusing destructive operation" |
| 3 | `br-lucky-violet-b36bpnpu` | `*.neon.tech` | `SOW_ALLOW_DESTRUCTIVE_ON_BRANCH=1` | proceeds + override warning |
| 4 | `None` | `127.0.0.1` (testcontainers) | — | proceeds (off-Neon) |
| 5 | `None` | `*.neon.tech` (pooler hides the setting / read failed) | — | `exit_code == 1` (fail-closed) |
| 6 | `None` | empty host | — | `exit_code == 1` (fail-closed) |

Cover 1-6 for `maintenance purge-soft-deletes --song-id X --confirm`; repeat 2 and 5 for `maintenance purge-r2-waste --all --confirm` (both commands share the guard, so the value is in proving each callsite is wired) and for `db init --wipe-songsets`, plus a `db init --wipe-songsets --confirm`-free case asserting the refusal message is the guard's own line, not `Failed to initialize schema: 1`.

### 7. Local UI target badge

New file `delivery/webapp/src/components/db-target-badge.tsx` (nothing equivalent exists):

```tsx
// Deliberately a server component: no hooks, no "use client". A client
// component would inline NEXT_PUBLIC_DB_TARGET into the client bundle at
// compile time, so a warm `.next` cache could keep rendering the previous
// target after switching env files. Server-side rendering reads process.env
// per request, so the label always matches the running server.
export function DbTargetBadge() {
  const target = process.env.NEXT_PUBLIC_DB_TARGET;
  if (!target) return null; // unset in deployed envs → no badge
  const isDev = target !== "production";
  return (
    <div
      data-db-target={target}
      className={
        isDev
          ? "fixed bottom-2 right-2 z-[100] rounded-full bg-destructive px-3 py-1 text-xs font-medium text-white"
          : "fixed bottom-2 right-2 z-[100] rounded-full border border-border px-3 py-1 text-xs opacity-60"
      }
    >
      {isDev ? "DEV database" : "PRODUCTION data"}
    </div>
  );
}
```

Mount it in `delivery/webapp/src/app/layout.tsx` — inside `<body>`, after the `</LocaleProvider>` closing tag and before `<Analytics />`, leaving the existing `LocaleProvider` / `GlobalAudioPlayer` / `Header` / `main` / `BottomNav` / `Toaster` / `ServiceWorkerRegistrar` chain untouched — so the chip renders on every route including `/login`. `RootLayout` is already an async server component, so an async-free server child needs no plumbing. No existing test renders `RootLayout` (`src/test/components/layout/` holds only `Header.test.tsx` and `BottomNav.test.tsx`), so the layout edit needs no matching layout test. English-only copy is intentional: dev tooling, not user-facing zh-Hant content (no `docs/translation-guidelines.md` string table involvement).

New test `delivery/webapp/src/test/components/db-target-badge.test.tsx` (new file; `src/test/components/` exists) copying the env-reset idiom from `src/test/lib/r2/public-url.test.ts` — `vi.resetModules()` in `beforeEach`, env save/restore in `afterEach`, `await import("@/components/db-target-badge")` per case, `render()` from `@testing-library/react`:

```tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

describe("DbTargetBadge", () => {
  const originalEnv = process.env.NEXT_PUBLIC_DB_TARGET;

  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    if (originalEnv === undefined) delete process.env.NEXT_PUBLIC_DB_TARGET;
    else process.env.NEXT_PUBLIC_DB_TARGET = originalEnv;
    cleanup();
  });

  it("renders the dev chip when the target is dev", async () => {
    process.env.NEXT_PUBLIC_DB_TARGET = "dev";
    const { DbTargetBadge } = await import("@/components/db-target-badge");
    render(<DbTargetBadge />);
    expect(screen.getByText("DEV database")).toBeInTheDocument();
    expect(screen.getByText("DEV database")).toHaveAttribute("data-db-target", "dev");
  });

  it("renders the production chip when the target is production", async () => {
    process.env.NEXT_PUBLIC_DB_TARGET = "production";
    const { DbTargetBadge } = await import("@/components/db-target-badge");
    render(<DbTargetBadge />);
    expect(screen.getByText("PRODUCTION data")).toBeInTheDocument();
  });

  it("renders nothing when the target is unset", async () => {
    delete process.env.NEXT_PUBLIC_DB_TARGET;
    const { DbTargetBadge } = await import("@/components/db-target-badge");
    const { container } = render(<DbTargetBadge />);
    expect(container).toBeEmptyDOMElement();
  });
});
```

(`toBeEmptyDOMElement` and `toHaveAttribute` come from `@testing-library/jest-dom`, loaded by `src/test/setup.ts`.)

### 8. Docs that define the commands

- `delivery/webapp/README.md` "Environment Setup" (lines 18-25): state that `pnpm dev` / `pnpm dev:https` load `/opt/sow/.env_webapp_dev` (Neon `dev` branch) and `pnpm prod` / `pnpm prod:https` load `${SOW_WEBAPP_ENV_FILE:-/opt/sow/.env_webapp}` (production); `env-cmd`'s default precedence means the selected file beats an exported `SOW_DATABASE_URL`; other files are selected with `SOW_WEBAPP_ENV_FILE_DEV` / `SOW_WEBAPP_ENV_FILE`. Add `pnpm prod` / `pnpm prod:https` to the "Development" command block.
- `delivery/webapp/README.md` also gets three lines: `sow_admin_dev` (admin CLI against `dev`), `ops/analysis-service/start.sh` (service has no Postgres), and the note that dev-stack render submissions still land in the production queue (`SOW_RENDER_WORKER_MODE=sqs`).
- `delivery/webapp/AGENTS.md:9-16` — the real "Development Commands" table (`delivery/webapp/CLAUDE.md` is an 8-line file whose first line is `@AGENTS.md`, so editing `AGENTS.md` covers both client idiolects): add rows `| pnpm prod | Local dev server on :8080 against the production DB |` and `| pnpm prod:https | Same, HTTPS (Cast testing) |`. The root `/AGENTS.md` "Web App Commands (delivery/webapp/ directory)" block deserves the same two rows, since it is the file every agent loads.
- `delivery/webapp/README.md` "Development" block gains a warning line: do **not** submit render jobs from a dev-branch session (`pnpm dev`) — jobs land in the production SQS queue, redeliver until the DLQ, and never complete.
- The `sow_admin_dev` bullet states the branch guard: `maintenance purge-soft-deletes`, `maintenance purge-r2-waste`, and `db init --wipe-songsets` refuse on non-production branches unless `SOW_ALLOW_DESTRUCTIVE_ON_BRANCH` is set (dry-runs stay allowed).
- One line about the badge: the chip in the bottom-right corner of every page shows `DEV database` (dev file) or `PRODUCTION data` (production file); absent when `NEXT_PUBLIC_DB_TARGET` is unset (deployed webapp).

### 9. Restart any running dev server

`env-cmd` resolves the file once at process start; stop the current `next dev` on :8080 before verifying.

## Critical files & anchors

- `/opt/sow/.env_webapp_dev` — new host env file; `/opt/sow/.env_webapp:5` is the production source it is copied from.
- `/opt/sow/.env_webapp` — receives one appended `NEXT_PUBLIC_DB_TARGET=production` marker line.
- `delivery/webapp/package.json:7-8` — `dev`, `dev:https` (currently `env-cmd -f "${SOW_WEBAPP_ENV_FILE:-/opt/sow/.env}"`), plus new `prod`, `prod:https`.
- `ops/analysis-service/start-dev.sh` — rename to `start.sh` + three banner/comment strings.
- `~/.zshrc:402` — alias block where `sow_admin_dev` is added; `~/.zshrc:383` exports `SOW_WEBAPP_ENV_FILE`.
- `ops/admin-cli/src/stream_of_worship/admin/commands/db.py` — `init_db` (`with client:` / `initialize_schema` ~117-118), `_show_connection_info` (~129), `show_status` (~161), `show_url` (~200).
- `ops/admin-cli/src/stream_of_worship/admin/commands/maintenance.py:336-360,639-660` — `purge_soft_deletes` / `purge_r2_waste` guard insertion points (`_load_clients` at 55, `_load_r2` at 64).
- `ops/admin-cli/src/stream_of_worship/admin/commands/guard.py` — new file (`ensure_production_branch(db_client, db_url)`, `PRODUCTION_BRANCH_ID`, `NEON_HOST_SUFFIX`).
- `ops/admin-cli/pyproject.toml:105` — `addopts = "-m 'not integration'"`; guard tests go in an unmarked module.
- `delivery/webapp/AGENTS.md:9-16` — the real Development Commands table (`CLAUDE.md` only `@AGENTS.md`-imports it); root `/AGENTS.md` "Web App Commands" block is the second place for the same rows.
- `ops/admin-cli/src/stream_of_worship/admin/db/client.py:95-97` — `connection` property precedent for the new `current_neon_branch`.
- `delivery/webapp/src/app/layout.tsx` — badge mount point (inside `<body>`, after `</LocaleProvider>`, before `<Analytics />`).
- `delivery/webapp/src/test/lib/render/dispatcher.test.ts:80-122` and `src/test/lib/r2/public-url.test.ts:1-15` — `vi.resetModules()` + dynamic-import env-reset patterns to copy.

## Verification

**V1 — dev env file is self-consistently dev**

```bash
grep -c 'ep-crimson-bar-b3se1v53' /opt/sow/.env_webapp_dev   # → 1
grep -c 'ep-calm-mode-b33utavj'   /opt/sow/.env_webapp_dev   # → 0
grep -c '^NEXT_PUBLIC_DB_TARGET=dev' /opt/sow/.env_webapp_dev          # → 1
grep -c '^NEXT_PUBLIC_DB_TARGET=production' /opt/sow/.env_webapp       # → 1
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
zsh -ic 'sow_admin_dev db status'   # → Health: Connected, Neon branch: br-lucky-violet-b36bpnpu, Songs 587, Health Check OK
zsh -ic 'sow_admin db url'          # → …@ep-calm-mode-b33utavj… (production, unchanged)
zsh -ic 'sow_admin db status'       # → Neon branch: br-silent-surf-b3qfgd71
```

**V6 — changed suites still pass**

```bash
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest -v
cd delivery/webapp && pnpm test
```

**V7 — branch guard**

Pre-flight: prove the oracle returns a real id through the *exact* endpoint hosts the app uses (verified in plan review: `ep-calm-mode-b33utavj…` → `br-silent-surf-b3qfgd71`, `ep-crimson-bar-b3se1v53…` → `br-lucky-violet-b36bpnpu`, both non-NULL, so Neon's pooler does expose `neon.branch_id` here):

```bash
psql "$PROD_DSN" -Atc "select coalesce(current_setting('neon.branch_id', true), 'NULL')"   # → br-silent-surf-b3qfgd71
psql "$DEV_DSN"  -Atc "select coalesce(current_setting('neon.branch_id', true), 'NULL')"   # → br-lucky-violet-b36bpnpu
```

If either prints `NULL`, stop: the guard fails closed on that branch and will refuse even legitimate production runs. Do not "fix" it with the override — check whether the endpoint hides the setting.

```bash
zsh -ic 'sow_admin_dev maintenance purge-soft-deletes --song-id deadbeef --confirm'
# → "Refusing destructive operation: connected to Neon branch br-lucky-violet-b36bpnpu, not production (br-silent-surf-b3qfgd71)." + "If that is intentional, re-run with SOW_ALLOW_DESTRUCTIVE_ON_BRANCH=1."; exit 1
zsh -ic 'sow_admin_dev maintenance purge-r2-waste --all'
# → dry-run manifest, no refusal (the guard only runs with --confirm)
zsh -ic 'sow_admin_dev maintenance purge-r2-waste --all --confirm'
# → same refusal line, exit 1
zsh -ic 'sow_admin_dev db init --wipe-songsets'
# → same refusal line, exit 1 — and NOT "Failed to initialize schema: 1" (that would mean the guard is inside init_db's except Exception)
SOW_ALLOW_DESTRUCTIVE_ON_BRANCH=1 zsh -ic 'sow_admin_dev maintenance purge-soft-deletes --song-id deadbeef --confirm'
# → "Destructive override active: target branch br-lucky-violet-… on ep-crimson-bar-…", then the dry-run manifest (no matching soft-deleted id)
```

The production-side allow path, the off-Neon (`127.0.0.1`) allow path, and the fail-closed `NULL` cases are covered by the unit tests, not by a live prod purge. `db init --wipe-songsets` is never actually applied on a real branch during verification.

**V8 — target badge**

`pnpm dev` / `pnpm prod` serve plain HTTP on :8080 — only the `:https` twins pass `--experimental-https`, so `curl -k https://…` against them hits no TLS listener. Use the HTTPS pair (it matches the headless-Chrome recipe):

```bash
cd delivery/webapp && pnpm dev:https    # keep running
curl -sS -k https://localhost:8080/login | grep -o 'DEV database'           # → 1 match
curl -sS -k https://localhost:8080/login | grep -o 'data-db-target="dev"'   # → 1 match
# stop that server, then:
pnpm prod:https
curl -sS -k https://localhost:8080/login | grep -o 'PRODUCTION data'        # → 1 match
```

Plain-HTTP variant: `pnpm dev` / `pnpm prod` with `http://localhost:8080/login`. Because the badge is a server component, the label comes from the running server's `process.env` — no client-bundle inlining, so a warm `.next` cache cannot serve the previous label.

(If curl can't reach the page, use the headless-Chrome recipe in the root `AGENTS.md` and inspect the `[data-db-target]` element instead.)

## Assumptions & contingencies

- **`dev` branch recreated later** (endpoint changes): re-resolve with `curl -s -H "Authorization: Bearer $NEON_API_KEY" https://console.neon.tech/api/v2/projects/tiny-feather-38432620/endpoints` → take the `host` whose `branch_id` is `br-lucky-violet-b36bpnpu`, then `sed -i 's#ep-crimson-bar-b3se1v53#<new-endpoint-id>#' /opt/sow/.env_webapp_dev` (leave user/password/query string untouched).
- **Production branch recreated** (not just the dev endpoint): update `PRODUCTION_BRANCH_ID` in `commands/guard.py` together with the `.env_webapp*` endpoint swaps, otherwise the guard silently refuses every destructive production command.
- **`dev` branch schema drift**: if `select count(*) from drizzle.__drizzle_migrations` on the dev DSN is below `ls delivery/webapp/drizzle/*.sql | wc -l`, apply migrations with `SOW_DATABASE_URL="<dev DSN>" npx tsx scripts/migrate.ts` from `delivery/webapp` before using `pnpm dev`.
- **If a future `env-cmd` release stops letting the file win**: prepend `env -u SOW_DATABASE_URL` to all four scripts so the file-selected DSN can never be overridden by a stray export.
- **`pnpm prod` writes to production data.** It is intentionally named `prod`; no confirmation prompt is added. `sow_admin` (production via `config.toml`) is left untouched.
- **Off-Neon connections** (testcontainers, local postgres) return no `neon.branch_id`; the guard allows them *by host* (`127.0.0.1` and friends are not `*.neon.tech`) so the admin CLI integration suites run without the override. A Neon host with an unreadable branch id is refused, not allowed — that asymmetry is deliberate and is what closes the dev-branch deletion hole.
- **The guard depends on Neon exposing `neon.branch_id`.** It does today on both endpoints (verified live, see Context). If a future endpoint or pooler configuration stops returning it, the guard fails closed on that branch: `--confirm` purges get refused until `PRODUCTION_BRANCH_ID` detection works again, or the operator sets `SOW_ALLOW_DESTRUCTIVE_ON_BRANCH=1` deliberately after checking `db status`'s `Neon branch` row.
- **`typer.Exit` is caught by broad handlers.** It subclasses `RuntimeError`, so any `except Exception` between the guard and the CLI top level will swallow the refusal and re-raise it as a generic failure message. That is why the `init_db` guard sits outside its `try:`; the same review applies to any future guarded callsite.
- **Badge env var scope**: the badge is a **server** component, so `NEXT_PUBLIC_DB_TARGET` is read from the server's `process.env` per request (no build-time inlining, nothing shipped to the client bundle, no `.next` staleness after switching files). The deployed webapp sets no such var, so the badge is local-only. If a deployment ever sets it to something other than `production`, the badge would appear there — correctly, per the label.
- **Badge test env mutation** requires `vi.resetModules()` + dynamic import (`dispatcher.test.ts` / `public-url.test.ts` idiom); a server component is just a synchronous function returning JSX, so `@testing-library/react` renders it directly — do not add hooks or `"use client"` to make it "testable".
