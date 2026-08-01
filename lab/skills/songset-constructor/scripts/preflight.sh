#!/usr/bin/env bash
# Pre-flight environment checks for songset constructor skill.
# Verifies: database connectivity, theme_anchors table, R2 credentials, pool cache.
#
# Usage:
#   bash scripts/preflight.sh
#
# Exit code: 0 if all critical checks pass, 1 if any FAIL.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
ADMIN_CLI="$PROJECT_ROOT/ops/admin-cli"

FAIL_COUNT=0
WARN_COUNT=0

run_check() {
    local label="$1"
    local status="$2"
    local detail="$3"
    if [ "$status" = "OK" ]; then
        printf "[OK]   %s" "$label"
        [ -n "$detail" ] && printf " (%s)" "$detail"
        printf "\n"
    elif [ "$status" = "FAIL" ]; then
        printf "[FAIL] %s: %s\n" "$label" "$detail"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    elif [ "$status" = "WARN" ]; then
        printf "[WARN] %s: %s\n" "$label" "$detail"
        WARN_COUNT=$((WARN_COUNT + 1))
    else
        printf "[INFO] %s: %s\n" "$label" "$detail"
    fi
}

# ---------------------------------------------------------------------------
# Check 1: Database URL configured
# ---------------------------------------------------------------------------
DB_URL_SET="no"
if [ -n "${SOW_DATABASE_URL:-}" ]; then
    DB_URL_SET="yes"
    run_check "Database URL configured" "OK" "via SOW_DATABASE_URL"
else
    # Check if config file has database.url
    CONFIG_PATH="${XDG_CONFIG_HOME:-$HOME/.config}/stream-of-worship-admin/config.toml"
    if [ -f "$CONFIG_PATH" ]; then
        if grep -q '\[database\]' "$CONFIG_PATH" 2>/dev/null; then
            DB_URL_SET="yes"
            run_check "Database URL configured" "OK" "via config.toml"
        fi
    fi
    if [ "$DB_URL_SET" = "no" ]; then
        run_check "Database URL configured" "FAIL" "Set SOW_DATABASE_URL or configure config.toml"
    fi
fi

# ---------------------------------------------------------------------------
# Check 2: Database reachable + theme_anchors count
# ---------------------------------------------------------------------------
# Pre-scan pool cache so DB-unreachable can be downgraded to WARN when cache exists.
CACHE_DIR="$HOME/.cache/sow/songset_constructor"
CACHE_NOT_NEEDED=false
POOL_CACHE_FILES=""
POOL_CACHE_AGE_HOURS=""
POOL_CACHE_SONG_COUNT=""
if [ -d "$CACHE_DIR" ]; then
    POOL_CACHE_FILES=$(find "$CACHE_DIR" -name 'pool_*.json' -type f 2>/dev/null)
    if [ -n "$POOL_CACHE_FILES" ]; then
        NEWEST_CACHE=$(find "$CACHE_DIR" -name 'pool_*.json' -type f -exec stat -f '%m %N' {} \; 2>/dev/null | sort -rn | head -1)
        if [ -n "$NEWEST_CACHE" ]; then
            CACHE_MTIME=$(echo "$NEWEST_CACHE" | cut -d' ' -f1)
            NOW=$(date +%s)
            AGE_SECS=$((NOW - CACHE_MTIME))
            POOL_CACHE_AGE_HOURS=$((AGE_SECS / 3600))
            CACHE_FILE=$(echo "$NEWEST_CACHE" | cut -d' ' -f2-)
            POOL_CACHE_SONG_COUNT=$(uv run --project "$ADMIN_CLI" --extra admin python -c "
import json, sys
try:
    data = json.load(open('$CACHE_FILE'))
    print(len(data))
except:
    print(0)
" 2>/dev/null)
        fi
    fi
fi

if [ "$DB_URL_SET" = "yes" ]; then
    DB_CHECK=$(uv run --project "$ADMIN_CLI" --extra admin python -c '
import sys
sys.path.insert(0, "'"$ADMIN_CLI/src"'")
try:
    from stream_of_worship.admin.config import AdminConfig
    from stream_of_worship.db.connection import ConnectionProvider
    from stream_of_worship.db.app.read_client import ReadOnlyClient
    from stream_of_worship.admin.songset_constructor.db import check_theme_anchors, ThemeAnchorsTableMissing

    config = AdminConfig.load()
    url = config.get_connection_url()
    provider = ConnectionProvider(url)
    client = ReadOnlyClient(provider)

    # Check DB reachable
    client.connection.execute("SELECT 1")

    # Check theme_anchors
    try:
        count = check_theme_anchors(client)
    except ThemeAnchorsTableMissing:
        print("DB_OK|ANCHORS_MISSING|0")
        sys.exit(0)
    except Exception as e:
        print(f"DB_OK|ANCHORS_ERROR|{e}")
        sys.exit(0)

    print(f"DB_OK|ANCHORS_OK|{count}")
except Exception as e:
    print(f"DB_FAIL|DB_ERROR|{e}")
' 2>&1)

    IFS='|' read -r db_status anchor_status anchor_count <<< "$DB_CHECK"

    if [ "$db_status" = "DB_OK" ]; then
        run_check "Database reachable" "OK" ""
    else
        if [ -n "$POOL_CACHE_FILES" ]; then
            run_check "Database reachable" "WARN" "DB unreachable — proceeding from cached pool (age: ${POOL_CACHE_AGE_HOURS}h, ${POOL_CACHE_SONG_COUNT} songs)"
            CACHE_NOT_NEEDED=true
        else
            run_check "Database reachable" "FAIL" "$anchor_count"
        fi
    fi

    if [ "$anchor_status" = "ANCHORS_OK" ]; then
        if [ "$anchor_count" = "12" ]; then
            run_check "theme_anchors table" "OK" "${anchor_count}/12 rows"
        else
            run_check "theme_anchors table" "FAIL" "${anchor_count} rows (expected 12). Run: sow-admin theme-anchors sync"
        fi
    elif [ "$anchor_status" = "ANCHORS_MISSING" ]; then
        run_check "theme_anchors table" "FAIL" "table does not exist. Run: sow-admin db init && sow-admin theme-anchors sync"
    else
        run_check "theme_anchors table" "FAIL" "$anchor_count"
    fi
fi

# ---------------------------------------------------------------------------
# Check 3: R2 credentials
# ---------------------------------------------------------------------------
if [ -n "${SOW_R2_ACCESS_KEY_ID:-}" ] && [ -n "${SOW_R2_SECRET_ACCESS_KEY:-}" ]; then
    run_check "R2 credentials configured" "OK" ""
else
    run_check "R2 credentials configured" "FAIL" "Set SOW_R2_ACCESS_KEY_ID and SOW_R2_SECRET_ACCESS_KEY (required for lyrics access)"
fi

# ---------------------------------------------------------------------------
# Check 4: Pool cache status
# ---------------------------------------------------------------------------
if [ -n "$POOL_CACHE_FILES" ]; then
    CACHE_COUNT=$(find "$CACHE_DIR" -name 'pool_*.json' -type f 2>/dev/null | wc -l | tr -d ' ')
    run_check "Pool cache" "INFO" "${POOL_CACHE_SONG_COUNT} songs cached (${CACHE_COUNT} file(s), age: ${POOL_CACHE_AGE_HOURS}h)"
elif [ -d "$CACHE_DIR" ]; then
    run_check "Pool cache" "INFO" "cache directory exists but no valid cache files"
else
    run_check "Pool cache" "INFO" "no cache directory (will be created on first fetch)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
if [ "$FAIL_COUNT" -gt 0 ]; then
    printf "\n%d check(s) failed, %d warning(s). Resolve failures before constructing songsets.\n" "$FAIL_COUNT" "$WARN_COUNT"
    exit 1
fi

if [ "$WARN_COUNT" -gt 0 ]; then
    printf "\nAll critical checks passed with %d warning(s).\n" "$WARN_COUNT"
else
    printf "\nAll critical checks passed.\n"
fi
exit 0
