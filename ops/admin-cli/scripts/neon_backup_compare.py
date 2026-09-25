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


def run(argv, input_text: str | None = None, timeout: int | None = None):
    """Run a command with argv list (no shell). Returns (rc, stdout, stderr)."""
    result = subprocess.run(
        argv,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


def run_ok(argv, input_text: str | None = None, timeout: int | None = None) -> str:
    """Run a command, die on non-zero. Returns stdout."""
    rc, stdout, stderr = run(argv, input_text=input_text, timeout=timeout)
    if rc != 0:
        die(f"command failed ({rc}): {' '.join(argv)}\n{stderr.strip()}")
    return stdout


def mask(dsn: str) -> str:
    from urllib.parse import urlparse

    p = urlparse(dsn)
    return p.hostname or dsn


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
    dsn = stdout.strip().splitlines()[-1].strip() if stdout.strip() else ""
    if rc != 0 or not dsn:
        die(f"neon connection-string {branch} failed\n{stderr.strip()}")
    return dsn


def psql(dsn: str, sql: str, input_text: str | None = None) -> str:
    return run_ok(["psql", dsn, "-At", "-v", "ON_ERROR_STOP=1", "-c", sql], input_text=input_text)


def psql_rc(dsn: str, sql: str) -> int:
    rc, _, _ = run(["psql", dsn, "-At", "-v", "ON_ERROR_STOP=1", "-c", sql])
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
    """Upload files to R2; any failure is fatal (fail-gate)."""
    from stream_of_worship.admin.config import AdminConfig
    from stream_of_worship.admin.services.r2 import R2Client

    config = AdminConfig.load()
    client = R2Client(bucket=config.r2_bucket, endpoint_url=config.r2_endpoint_url)
    for path in files:
        key = f"{r2_prefix}/{date}/{path.name}"
        with path.open("rb") as fh:
            url = client.upload_fileobj(fh, key)
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
    snapshots = neon_json(["snapshots", "list"])
    print(f"neon API key OK: snapshots list returned {len(snapshots)} snapshot(s)")

    branches = neon_json(["branches", "list"])
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
    dsn = args.dsn or neon_dsn(args.branch)
    wait_ready(dsn)
    sql = (
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


def cmd_backup(args) -> None:
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    branches = [b.strip() for b in args.branches.split(",") if b.strip()]
    dumps: list[Path] = []

    for branch in branches:
        prefix = dump_prefix_for(branch)
        dump_path = out_dir / f"{prefix}_{args.date}.dump"
        if dump_path.exists() and not args.force:
            die(f"refusing to overwrite existing dump: {dump_path} (use --force)")
        dsn = args.dsn or neon_dsn(branch)
        print(f"== dumping {branch} -> {dump_path.name} (host {mask(dsn)}) ==")
        wait_ready(dsn)
        run_ok(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--file",
                str(dump_path),
                dsn,
            ],
            timeout=None,
        )
        size_mb = dump_path.stat().st_size / 1_000_000
        print(f"  dumped {size_mb:.1f} MB")
        dumps.append(dump_path)

    # Integrity check: every dump's TOC must be readable
    for dump in dumps:
        run_ok(["pg_restore", "--list", str(dump)])
        print(f"  integrity OK: {dump.name}")

    # sha256 manifest (sha256sum-compatible format) over ALL dumps for this
    # date in the output dir — not just the ones dumped in this invocation —
    # so repeated backup runs accumulate instead of dropping earlier entries.
    all_dumps = sorted(out_dir.glob(f"*_{args.date}.dump"))
    if not all_dumps:
        die("no dump files found for manifest")
    manifest = out_dir / f"manifest_{args.date}.sha256"
    lines = []
    for dump in all_dumps:
        digest = hashlib.sha256(dump.read_bytes()).hexdigest()
        lines.append(f"{digest}  {dump.name}")
    manifest.write_text("\n".join(lines) + "\n")
    print(f"manifest written: {manifest.name} ({len(all_dumps)} dumps)")

    # Baseline counts always come from development
    dev_dsn = args.dsn or neon_dsn("development")
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
    branches = neon_json(argv)
    return any(b["name"] == name or b["id"] == name for b in branches)


def delete_branch(name: str) -> None:
    run_ok(["neon", "branches", "delete", name])


def cmd_test_restore(args) -> None:
    if not args.scratch_name:
        die("scratch branch name unresolved — pass --scratch-name explicitly")
    dump_path = Path(args.dump)
    if not dump_path.is_file():
        die(f"dump not found: {dump_path}")
    baseline_path = Path(args.baseline)
    if not baseline_path.is_file():
        die(f"baseline not found: {baseline_path}")

    if branch_exists(args.scratch_name):
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
        dsn = args.dsn or neon_dsn(args.scratch_name)
        wait_ready(dsn, attempts=12, delay=10)
        print(f"== restoring {dump_path.name} into {args.scratch_name} ==")
        run_ok(
            [
                "pg_restore",
                "--no-owner",
                "--no-privileges",
                "--exit-on-error",
                "--jobs",
                "4",
                "--dbname",
                dsn,
                str(dump_path),
            ],
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
            delete_branch(args.scratch_name)
            print(f"TEST-RESTORE PASS ({dump_path.name})")
        else:
            print(
                f"KEEPING scratch branch {args.scratch_name} for inspection; "
                "delete manually after diagnosing."
            )


def cmd_snapshot(args) -> None:
    if not args.name:
        die("snapshot name unresolved — pass --name explicitly")
    existing = neon_json(["snapshots", "list"])
    if existing:
        print("existing snapshot(s):")
        for s in existing:
            print(f"  {s.get('id')}  {s.get('name')}  source_branch={s.get('source_branch_id')}")
        die(
            "snapshot slot occupied — delete the existing snapshot MANUALLY first "
            "(free plan = 1 slot); this script never deletes snapshots"
        )
    run_ok(["neon", "snapshots", "create", "--branch", args.branch, "--name", args.name])
    after = neon_json(["snapshots", "list"])
    # Snapshot create is async — an immediate list may return the snapshot with
    # source_branch_id still null. Match by name (unique per project) and treat
    # a missing source_branch_id as a warning, not a failure.
    created = [s for s in after if s.get("name") == args.name]
    if len(created) != 1:
        die(f"expected exactly 1 snapshot named '{args.name}', found {len(created)}")
    s = created[0]
    prod_id = next(b["id"] for b in neon_json(["branches", "list"]) if b["name"] == args.branch)
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

    if branch_exists(args.branch_name):
        die(f"branch '{args.branch_name}' already exists — delete it manually first")

    print(f"== restoring snapshot {args.snapshot} into new branch {args.branch_name} ==")
    run_ok(["neon", "snapshots", "restore", args.snapshot, "--name", args.branch_name])
    try:
        dsn = args.dsn or neon_dsn(args.branch_name)
        wait_ready(dsn, attempts=12, delay=10)
        actual = collect_baseline(dsn)
        diffs = diff_baselines(baseline_path, actual)
        if diffs:
            print("".join(diffs))
            die(f"snapshot smoke baseline mismatch for branch {args.branch_name}")
        print("  baseline identical")
    finally:
        print(f"== deleting smoke branch {args.branch_name} ==")
        delete_branch(args.branch_name)
        print("SNAPSHOT-SMOKE PASS")


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

    dsn = args.dsn or neon_dsn(args.branch)
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
    dsn = args.dsn or neon_dsn(args.branch)
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
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("test-restore", help="Phase 3 scratch-branch restore test")
    p.add_argument("--dump", required=True)
    p.add_argument("--scratch-name", default=None)
    p.add_argument("--parent", default="production")
    add_common(p, date=True)
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
    add_common(p)
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
