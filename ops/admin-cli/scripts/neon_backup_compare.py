#!/usr/bin/env python3
"""
Neon backup & compare helper — non-destructive portions of the promotion plan.

Implements the safe phases of specs/neon-promote-development-to-production-v3.md:
precondition checks (Phase 0), write-freeze gate (Phase 1.5), pg_dump backups with
integrity + sha256 manifest + R2 upload fail-gate + baseline counts file (Phase 2),
scratch-branch test restore (Phase 3), snapshot create with guard (Phase 6),
snapshot smoke test, post-promotion verification (Phase 5) and zero-drift check
(Phase 9.4).

Destructive steps (branch wipes/renames/deletes other than this script's own
scratch branches, snapshot slot deletion, compute repointing, env cutover) are
performed manually by the operator, never by this script.

A `--date` is immutable: `backup` refuses to overwrite any same-date
dump/baseline/manifest; redo with a new `--date`.

Usage (from repo root):
    uv run --project ops/admin-cli --python 3.11 --extra admin python \
        ops/admin-cli/scripts/neon_backup_compare.py <subcommand> ...

Requires: pg_dump/psql >= 17, neonctl (authed via NEON_API_KEY), repo file .neon
for project pinning, SOW_R2_ACCESS_KEY_ID / SOW_R2_SECRET_ACCESS_KEY for uploads.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "ops" / "admin-cli" / "src"))

MAJOR_PG = re.compile(r"pg_dump.*?(\d+)\.")


def die(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def run(argv, input_text: str | None = None, timeout: int | None = None, env: dict | None = None):
    """Run a command with argv list (no shell). Returns (rc, stdout, stderr)."""
    merged = {**os.environ, **env} if env else None
    result = subprocess.run(
        argv,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=merged,
    )
    return result.returncode, result.stdout, result.stderr


def run_ok(
    argv, input_text: str | None = None, timeout: int | None = None, env: dict | None = None
) -> str:  # noqa: E501
    """Run a command, die on non-zero. Returns stdout."""
    rc, stdout, stderr = run(argv, input_text=input_text, timeout=timeout, env=env)
    if rc != 0:
        redacted = REDACT_DSN.sub(r"\1****@", " ".join(argv))
        redacted_stderr = REDACT_DSN.sub(r"\1****@", stderr.strip())
        die(f"command failed ({rc}): {redacted}\n{redacted_stderr}")
    return stdout


def mask(dsn: str) -> str:
    from urllib.parse import urlparse

    p = urlparse(dsn)
    return p.hostname or dsn


REDACT_DSN = re.compile(r"(postgres(?:ql)?://[^:\s]+:)[^@\s]+@")


def dsn_host(dsn: str) -> str:
    from urllib.parse import urlparse

    host = urlparse(dsn).hostname
    if not host:
        die(f"could not parse host from DSN: {mask(dsn)}")
    return host


def _normalize_neon_host(host: str) -> str:
    # Neon pooled DSNs insert "-pooler" into the host; treat both forms as equal.
    return host.replace("-pooler.", ".")


def assert_dsn_matches_branch(dsn: str, branch: str, project_id: str | None = None) -> None:
    expected = dsn_host(neon_dsn(branch, project_id=project_id))
    actual = dsn_host(dsn)
    if _normalize_neon_host(actual) != _normalize_neon_host(expected):
        die(f"--dsn host {actual!r} does not match branch '{branch}' host {expected!r}")


def pg_env(dsn: str) -> tuple[str, dict[str, str]]:
    """Split DSN into (dsn_without_password, env_with_PGPASSWORD)."""
    from urllib.parse import urlsplit, urlunsplit

    p = urlsplit(dsn)
    if not p.password:
        return dsn, {}
    netloc = f"{p.username}@{p.hostname}" if p.username else str(p.hostname)
    if p.port:
        netloc += f":{p.port}"
    clean = urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))
    return clean, {"PGPASSWORD": p.password}


# ---------------------------------------------------------------- Neon helpers


def neon_json(argv: list[str]):
    rc, stdout, stderr = run(["neon", *argv, "--output", "json"])
    if rc != 0:
        die(f"neon {' '.join(argv)} failed\n{stderr.strip()}")
    import json

    return json.loads(stdout)


def neon_dsn(branch: str, pooled: bool = False, project_id: str | None = None) -> str:
    """Fetch a full connection string (incl. password) for a Neon branch."""
    argv = ["neon", "connection-string", branch]
    if pooled:
        argv.append("--pooled")
    if project_id:
        argv += ["--project-id", project_id]
    rc, stdout, stderr = run(argv, timeout=60)
    dsn_lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    dsn_lines = [ln for ln in dsn_lines if ln.startswith(("postgres://", "postgresql://"))]
    dsn = dsn_lines[-1] if dsn_lines else ""
    if rc != 0 or not dsn:
        die(f"neon connection-string {branch} failed\n{stderr.strip()}")
    return dsn


def psql(dsn: str, sql: str, input_text: str | None = None) -> str:
    clean, env = pg_env(dsn)
    return run_ok(
        ["psql", clean, "-At", "-v", "ON_ERROR_STOP=1", "-c", sql], input_text=input_text, env=env
    )


def psql_rc(dsn: str, sql: str) -> int:
    clean, env = pg_env(dsn)
    rc, _, _ = run(["psql", clean, "-At", "-v", "ON_ERROR_STOP=1", "-c", sql], env=env)
    return rc


def wait_ready(dsn: str, attempts: int = 6, delay: int = 10) -> None:
    """Preflight connection with retries (dormant compute cold start)."""
    for attempt in range(1, attempts + 1):
        rc = psql_rc(dsn, "SELECT 1")
        if rc == 0:
            print(f"  database ready (attempt {attempt})")
            return
        if attempt == attempts:
            die(f"database not reachable after {attempts} attempts: {mask(dsn)}")
        print(f"  not ready (attempt {attempt}/{attempts}), retrying in {delay}s...")
        time.sleep(delay)


# ------------------------------------------------------------- Baseline triple


EXT_SQL = "SELECT extname FROM pg_extension ORDER BY 1"
SCHEMA_SQL = (
    "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' "
    "AND nspname <> 'information_schema' ORDER BY 1"
)
COUNT_GEN_SQL = (
    "SELECT format('SELECT %L AS tbl, count(*) AS n FROM %I.%I;', "
    "table_schema || '.' || table_name, table_schema, table_name) "
    "FROM information_schema.tables WHERE table_type = 'BASE TABLE' "
    "AND table_schema NOT IN ('pg_catalog','information_schema') ORDER BY 1;"
)


def collect_baseline(dsn: str) -> str:
    """Extensions + non-system schemas + exact per-table counts, in plan order."""
    exts = psql(dsn, EXT_SQL)
    schemas = psql(dsn, SCHEMA_SQL)
    count_sql = psql(dsn, COUNT_GEN_SQL)
    if not count_sql.strip():
        die("no base tables found — count section would be empty")
    counts = psql(dsn, count_sql)
    if not counts.strip():
        die("count query produced no rows")
    return f"# extensions\n{exts}# schemas\n{schemas}# table_counts\n{counts}"


def diff_baselines(baseline_path: Path, actual: str) -> list[str]:
    expected = baseline_path.read_text()
    if expected == actual:
        return []
    return list(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=str(baseline_path),
            tofile="actual",
        )
    )


# ---------------------------------------------------------------------- R2


def r2_upload_files(files: list[Path], date: str, r2_prefix: str) -> None:
    """Upload files to R2; never overwrites remote objects; any failure is fatal."""
    from stream_of_worship.admin.config import AdminConfig
    from stream_of_worship.admin.services.r2 import R2Client

    config = AdminConfig.load()
    client = R2Client(bucket=config.r2_bucket, endpoint_url=config.r2_endpoint_url)
    for path in files:
        key = f"{r2_prefix}/{date}/{path.name}"
        pre = client.head_object(key)
        if pre is not None:
            die(f"R2 object already exists: {key} — refusing to overwrite; use a new --date")
        with path.open("rb") as fh:
            url = client.upload_fileobj(fh, key)
        post = client.head_object(key)
        if post is None:
            die(f"R2 upload not found after upload: {key}")
        if post["size"] != path.stat().st_size:
            die(
                f"R2 upload size mismatch for {key}: remote {post['size']} != "
                f"local {path.stat().st_size}"
            )
        print(f"  uploaded {url}")


# ------------------------------------------------------------------- Subcommands


def cmd_precheck(args) -> None:
    print("== Phase 0 preconditions ==")

    # pg_dump >= 17
    out = run_ok(["pg_dump", "--version"]).strip()
    m = MAJOR_PG.search(out)
    if not m or int(m.group(1)) < 17:
        die(f"pg_dump too old (need >= 17): {out}")
    print(f"pg_dump OK: {out}")
    out = run_ok(["psql", "--version"]).strip()
    print(f"psql OK: {out}")

    # neonctl + API key
    out = run_ok(["neon", "--version"]).strip()
    print(f"neonctl OK: {out}")
    snapshots_argv = ["snapshots", "list"]
    if args.project_id:
        snapshots_argv += ["--project-id", args.project_id]
    snapshots = neon_json(snapshots_argv)
    print(f"neon API key OK: snapshots list returned {len(snapshots)} snapshot(s)")

    branches_argv = ["branches", "list"]
    if args.project_id:
        branches_argv += ["--project-id", args.project_id]
    branches = neon_json(branches_argv)
    if len(branches) > 8:
        die(f"expected at most 8 branches, found {len(branches)} — reconcile manually")
    print(f"branches ({len(branches)}):")
    for b in branches:
        print(f"  {b['name']:16} {b['id']:36} {b.get('current_state')}")

    # R2 config + creds
    from stream_of_worship.admin.config import AdminConfig

    config = AdminConfig.load()
    if not config.r2_bucket or not config.r2_endpoint_url:
        die("config.toml missing r2.bucket / r2.endpoint_url")
    print(f"R2 config OK: bucket={config.r2_bucket} endpoint={config.r2_endpoint_url}")
    if not os.environ.get("SOW_R2_ACCESS_KEY_ID") or not os.environ.get("SOW_R2_SECRET_ACCESS_KEY"):
        die("SOW_R2_ACCESS_KEY_ID / SOW_R2_SECRET_ACCESS_KEY not set")
    print("R2 credentials OK")

    # DSN resolution for the two key branches
    for branch in ("production", "development"):
        dsn = neon_dsn(branch, project_id=args.project_id)
        print(f"DSN {branch}: host={mask(dsn)}")

    print("PRECHECK PASS")


def cmd_freeze_check(args) -> None:
    dsn = args.dsn or neon_dsn(args.branch, project_id=args.project_id)
    if args.dsn:
        assert_dsn_matches_branch(args.dsn, args.branch, args.project_id)
    wait_ready(dsn)
    sql = (
        # advisory snapshot only — this does not block new writers; the freeze is procedural
        "SELECT usename, application_name, client_addr, state FROM pg_stat_activity "
        "WHERE datname = current_database() AND pid <> pg_backend_pid();"
    )
    rows = psql(dsn, sql)
    if rows.strip():
        print("FOREIGN SESSIONS DETECTED:")
        print(rows, end="")
        die("write-freeze violated — drain sessions before proceeding")
    print(f"OK: no foreign sessions on {args.branch} ({mask(dsn)})")


def dump_prefix_for(branch: str) -> str:
    if branch == "production":
        return "production_pre_promotion"
    if branch == "development":
        return "development_source"
    return f"{branch}_archive"


def assert_date_fresh(out_dir: Path, date: str) -> None:
    """Refuse to overwrite an existing same-date snapshot (dumps/baseline/manifest)."""
    existing = list(out_dir.glob(f"*_{date}.dump"))
    for name in (f"baseline_dev_counts_{date}.txt", f"manifest_{date}.sha256"):
        if (out_dir / name).exists():
            existing.append(out_dir / name)
    if existing:
        die(f"date snapshot for {date} already exists — refusing to overwrite; use a new --date")


def cmd_backup(args) -> None:
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    branches = [b.strip() for b in args.branches.split(",") if b.strip()]
    dumps: list[Path] = []

    assert_date_fresh(out_dir, args.date)

    if args.dsn and len(branches) != 1:
        die("--dsn requires exactly one branch in --branches")
    if args.dsn:
        assert_dsn_matches_branch(args.dsn, branches[0], args.project_id)

    for branch in branches:
        prefix = dump_prefix_for(branch)
        dump_path = out_dir / f"{prefix}_{args.date}.dump"
        dsn = args.dsn or neon_dsn(branch, project_id=args.project_id)
        print(f"== dumping {branch} -> {dump_path.name} (host {mask(dsn)}) ==")
        wait_ready(dsn)
        tmp_path = out_dir / f"{prefix}_{args.date}.dump.tmp"
        tmp_path.unlink(missing_ok=True)
        clean, env = pg_env(dsn)
        run_ok(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--file",
                str(tmp_path),
                clean,
            ],
            env=env,
            timeout=None,
        )
        run_ok(["pg_restore", "--list", str(tmp_path)])  # verify before publish
        tmp_path.rename(dump_path)  # atomic same-dir publish; overwrites only after verify
        size_mb = dump_path.stat().st_size / 1_000_000
        print(f"  dumped {size_mb:.1f} MB")
        dumps.append(dump_path)

    # sha256 manifest (sha256sum-compatible format) over the dumps produced by
    # THIS invocation — freshness is guaranteed by assert_date_fresh, which
    # refuses to run when any same-date artifact already exists.
    manifest = out_dir / f"manifest_{args.date}.sha256"
    lines = []
    for dump in dumps:
        digest = hashlib.sha256(dump.read_bytes()).hexdigest()
        lines.append(f"{digest}  {dump.name}")
    manifest.write_text("\n".join(lines) + "\n")
    print(f"manifest written: {manifest.name} ({len(dumps)} dumps)")

    # Baseline counts always come from development; --dsn never applies here
    # (the baseline is development-sourced by definition).
    dev_dsn = neon_dsn("development", project_id=args.project_id)
    baseline = out_dir / f"baseline_dev_counts_{args.date}.txt"
    baseline.write_text(collect_baseline(dev_dsn))
    print(f"baseline written: {baseline.name} (source: development)")

    if args.no_upload:
        print("skipping R2 upload (--no-upload)")
        return

    artifacts = [*dumps, manifest, baseline]
    r2_upload_files(artifacts, args.date, args.r2_prefix)
    print("BACKUP PASS")


def branch_exists(name: str, project_id: str | None = None) -> bool:
    argv = ["branches", "list"]
    if project_id:
        argv += ["--project-id", project_id]
    branches = neon_json(argv)
    return any(b["name"] == name or b["id"] == name for b in branches)


def delete_branch(name: str, project_id: str | None = None) -> None:
    argv = ["neon", "branches", "delete", name]
    if project_id:
        argv += ["--project-id", project_id]
    run_ok(argv)


def cmd_test_restore(args) -> None:
    if not args.scratch_name:
        die("scratch branch name unresolved — pass --scratch-name explicitly")
    if not args.scratch_name.startswith("scratch_"):
        die(
            f"scratch branch name must start with 'scratch_' (got {args.scratch_name!r}) — "
            "refusing to restore into a non-scratch branch"
        )
    if args.scratch_name in {"production", "development", "staging"}:
        die(f"refusing to restore into protected branch {args.scratch_name!r}")
    dump_path = Path(args.dump)
    if not dump_path.is_file():
        die(f"dump not found: {dump_path}")
    baseline_path = Path(args.baseline)
    if not baseline_path.is_file():
        die(f"baseline not found: {baseline_path}")

    if branch_exists(args.scratch_name, project_id=args.project_id):
        die(
            f"branch '{args.scratch_name}' already exists — delete it manually or "
            "choose another --scratch-name"
        )

    project_argv = ["--project-id", args.project_id] if args.project_id else []
    print(f"== creating scratch branch {args.scratch_name} (parent {args.parent}) ==")
    run_ok(
        [
            "neon",
            "branches",
            "create",
            "--name",
            args.scratch_name,
            "--parent",
            args.parent,
            "--no-secrets",
            *project_argv,
        ]
    )

    restore_ok = False
    diff_ok = False
    try:
        dsn = neon_dsn(args.scratch_name, project_id=args.project_id)
        assert_dsn_matches_branch(dsn, args.scratch_name, args.project_id)
        wait_ready(dsn, attempts=12, delay=10)
        print(f"== restoring {dump_path.name} into {args.scratch_name} ==")
        clean, env = pg_env(dsn)
        run_ok(
            [
                "pg_restore",
                "--no-owner",
                "--no-privileges",
                "--clean",
                "--if-exists",
                "--exit-on-error",
                "--jobs",
                "4",
                "--dbname",
                clean,
                str(dump_path),
            ],
            env=env,
            timeout=None,
        )
        restore_ok = True
        print("  restore OK")

        actual = collect_baseline(dsn)
        diffs = diff_baselines(baseline_path, actual)
        if diffs:
            print("".join(diffs))
            die(f"baseline mismatch after restore into {args.scratch_name}")
        diff_ok = True
        print("  baseline identical")
    finally:
        if restore_ok and diff_ok:
            print(f"== deleting scratch branch {args.scratch_name} ==")
            delete_branch(args.scratch_name, project_id=args.project_id)
            print(f"TEST-RESTORE PASS ({dump_path.name})")
        else:
            print(
                f"KEEPING scratch branch {args.scratch_name} for inspection; "
                "delete manually after diagnosing."
            )


def cmd_snapshot(args) -> None:
    if not args.name:
        die("snapshot name unresolved — pass --name explicitly")
    existing_argv = ["snapshots", "list"]
    if args.project_id:
        existing_argv += ["--project-id", args.project_id]
    existing = neon_json(existing_argv)
    if existing:
        print("existing snapshot(s):")
        for s in existing:
            print(f"  {s.get('id')}  {s.get('name')}  source_branch={s.get('source_branch_id')}")
        die(
            "snapshot slot occupied — delete the existing snapshot MANUALLY first "
            "(free plan = 1 slot); this script never deletes snapshots"
        )
    create_argv = ["neon", "snapshots", "create", "--branch", args.branch, "--name", args.name]
    if args.project_id:
        create_argv += ["--project-id", args.project_id]
    run_ok(create_argv)
    after_argv = ["snapshots", "list"]
    if args.project_id:
        after_argv += ["--project-id", args.project_id]
    after = neon_json(after_argv)
    # Snapshot create is async — an immediate list may return the snapshot with
    # source_branch_id still null. Match by name (unique per project) and treat
    # a missing source_branch_id as a warning, not a failure.
    created = [s for s in after if s.get("name") == args.name]
    if len(created) != 1:
        die(f"expected exactly 1 snapshot named '{args.name}', found {len(created)}")
    s = created[0]
    branches_argv = ["branches", "list"]
    if args.project_id:
        branches_argv += ["--project-id", args.project_id]
    prod_id = None
    for b in neon_json(branches_argv):
        if b["name"] == args.branch:
            prod_id = b["id"]
            break
    if prod_id is None:
        die(f"branch {args.branch!r} not found in branches list")
    if s.get("source_branch_id") != prod_id:
        print(
            f"WARNING: snapshot source_branch_id={s.get('source_branch_id')} "
            f"not yet resolved to {prod_id} ({args.branch}) — async creation still "
            "in progress; verify later with `neon snapshots list`"
        )
    print(f"SNAPSHOT PASS: {s.get('id')} name={s.get('name')} source={s.get('source_branch_id')}")


def cmd_snapshot_smoke(args) -> None:
    baseline_path = Path(args.baseline)
    if not baseline_path.is_file():
        die(f"baseline not found: {baseline_path}")

    if not args.branch_name.startswith("snapshot_smoke_"):
        die(
            f"smoke branch name must start with 'snapshot_smoke_' (got "
            f"{args.branch_name!r}) — refusing to restore into a non-smoke branch"
        )
    if args.branch_name in {"production", "development", "staging"}:
        die(f"refusing to restore into protected branch {args.branch_name!r}")

    if branch_exists(args.branch_name, project_id=args.project_id):
        die(f"branch '{args.branch_name}' already exists — delete it manually first")

    print(f"== restoring snapshot {args.snapshot} into new branch {args.branch_name} ==")
    restore_argv = ["neon", "snapshots", "restore", args.snapshot, "--name", args.branch_name]
    if args.project_id:
        restore_argv += ["--project-id", args.project_id]
    run_ok(restore_argv)
    smoke_ok = False
    try:
        dsn = neon_dsn(args.branch_name, project_id=args.project_id)
        wait_ready(dsn, attempts=12, delay=10)
        actual = collect_baseline(dsn)
        diffs = diff_baselines(baseline_path, actual)
        if diffs:
            print("".join(diffs))
            die(f"snapshot smoke baseline mismatch for branch {args.branch_name}")
        print("  baseline identical")
        smoke_ok = True
        print("SNAPSHOT-SMOKE PASS")
    finally:
        if smoke_ok and branch_exists(args.branch_name, project_id=args.project_id):
            print(f"== deleting smoke branch {args.branch_name} ==")
            delete_branch(args.branch_name, project_id=args.project_id)
        elif not smoke_ok:
            print(
                f"KEEPING smoke branch {args.branch_name} for inspection (if created); "
                "delete manually after diagnosing."
            )


def parse_dump_tables(dump_path: Path) -> set[str]:
    """schema.name set from pg_restore TOC TABLE entries.

    TOC lines look like: `10; 145433 145431 TABLE public songs postgres`
    (semicolon only after the sequence number; leading `;` marks comments).
    The TOC also contains `... TABLE DATA public songs postgres` entries with
    the same prefix — excluded via lookahead so they don't parse as a fake
    "DATA.<schema>" table.
    """
    rc, toc, stderr = run(["pg_restore", "--list", str(dump_path)])
    if rc != 0:
        die(f"pg_restore --list failed for {dump_path}\n{stderr.strip()}")
    tables: set[str] = set()
    for line in toc.splitlines():
        m = re.match(r"^\d+;\s+\d+\s+\d+\s+TABLE\s+(?!DATA\b)(\S+)\s+(\S+)\s", line)
        if m:
            tables.add(f"{m.group(1)}.{m.group(2)}")
    if not tables:
        die(f"no TABLE entries parsed from TOC of {dump_path} — format drift?")
    return tables


def cmd_verify_production(args) -> None:
    dump_path = Path(args.source_dump)
    if not dump_path.is_file():
        die(f"dump not found: {dump_path}")
    baseline_path = Path(args.baseline)
    if not baseline_path.is_file():
        die(f"baseline not found: {baseline_path}")

    dsn = args.dsn or neon_dsn(args.branch, project_id=args.project_id)
    if args.dsn:
        assert_dsn_matches_branch(args.dsn, args.branch, args.project_id)
    wait_ready(dsn)

    print(f"== baseline check on {args.branch} ({mask(dsn)}) ==")
    diffs = diff_baselines(baseline_path, collect_baseline(dsn))
    if diffs:
        print("".join(diffs))
        die(f"baseline mismatch on {args.branch}")
    print("  baseline identical")

    print("== table-list check vs source dump ==")
    dump_tables = parse_dump_tables(dump_path)
    live_tables = {
        line.strip()
        for line in psql(
            dsn,
            "SELECT table_schema || '.' || table_name FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE' AND table_schema NOT IN "
            "('pg_catalog','information_schema') ORDER BY 1;",
        ).splitlines()
        if line.strip()
    }
    missing = sorted(dump_tables - live_tables)
    extra = sorted(live_tables - dump_tables)
    if missing or extra:
        if missing:
            print("in dump but not in branch:")
            for t in missing:
                print(f"  - {t}")
        if extra:
            print("in branch but not in dump:")
            for t in extra:
                print(f"  + {t}")
        die("table-list mismatch between dump and branch")
    print(f"  table lists identical ({len(dump_tables)} tables)")
    print("VERIFY-PRODUCTION PASS")


def cmd_zero_drift(args) -> None:
    baseline_path = Path(args.baseline)
    if not baseline_path.is_file():
        die(f"baseline not found: {baseline_path}")
    dsn = args.dsn or neon_dsn(args.branch, project_id=args.project_id)
    if args.dsn:
        assert_dsn_matches_branch(args.dsn, args.branch, args.project_id)
    wait_ready(dsn)
    diffs = diff_baselines(baseline_path, collect_baseline(dsn))
    if diffs:
        print("".join(diffs))
        die("something wrote during the run — stop and investigate")
    print(f"OK: zero drift on {args.branch} ({mask(dsn)})")


# ------------------------------------------------------------------------ CLI


def add_common(parser: argparse.ArgumentParser, *, dsn: bool = True, date: bool = False) -> None:
    if dsn:
        parser.add_argument(
            "--dsn", default=None, help="override DSN (bypasses neon connection-string)"
        )
    if date:
        parser.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y%m%d"))
    parser.add_argument("--project-id", default=None, help="override Neon project id")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("precheck", help="Phase 0 precondition checks")
    add_common(p, dsn=False)
    p.set_defaults(func=cmd_precheck)

    p = sub.add_parser("freeze-check", help="Phase 1.5 write-freeze gate")
    p.add_argument("--branch", default="development")
    add_common(p)
    p.set_defaults(func=cmd_freeze_check)

    p = sub.add_parser("backup", help="Phase 2 backups + manifest + baseline + R2 upload")
    p.add_argument("--branches", default="production,development,dev_0912,dev_0923")
    p.add_argument("--output-dir", default=str(REPO_ROOT / "output" / "neon-backups"))
    add_common(p, date=True)
    p.add_argument("--no-upload", action="store_true")
    p.add_argument("--r2-prefix", default="neon-backups")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("test-restore", help="Phase 3 scratch-branch restore test")
    p.add_argument("--dump", required=True)
    p.add_argument("--scratch-name", default=None)
    p.add_argument("--parent", default="production")
    add_common(p, dsn=False, date=True)
    p.add_argument("--baseline", default=None)
    p.set_defaults(func=cmd_test_restore)

    p = sub.add_parser("snapshot", help="Phase 6 guarded snapshot create")
    p.add_argument("--name", default=None)
    p.add_argument("--branch", default="production")
    add_common(p, dsn=False, date=True)
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("snapshot-smoke", help="Phase 6 snapshot smoke test")
    p.add_argument("--snapshot", required=True)
    p.add_argument("--branch-name", default="snapshot_smoke_test")
    add_common(p, dsn=False)
    p.add_argument("--baseline", required=True)
    p.set_defaults(func=cmd_snapshot_smoke)

    p = sub.add_parser("verify-production", help="Phase 5 post-promotion verification")
    p.add_argument("--branch", default="production")
    p.add_argument("--source-dump", required=True)
    add_common(p, date=True)
    p.add_argument("--baseline", default=None)
    p.set_defaults(func=cmd_verify_production)

    p = sub.add_parser("zero-drift", help="Phase 9.4 zero-drift check")
    p.add_argument("--branch", default="development")
    add_common(p)
    p.add_argument("--baseline", required=True)
    p.set_defaults(func=cmd_zero_drift)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    if getattr(args, "scratch_name", None) is None and hasattr(args, "date"):
        args.scratch_name = f"scratch_restore_test_{args.date}"
    if getattr(args, "name", None) is None and hasattr(args, "date"):
        args.name = f"production-promoted-{args.date}"
    if getattr(args, "baseline", None) is None and hasattr(args, "date"):
        args.baseline = str(
            REPO_ROOT / "output" / "neon-backups" / f"baseline_dev_counts_{args.date}.txt"
        )
    args.func(args)


if __name__ == "__main__":
    main()
