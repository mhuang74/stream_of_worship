#!/usr/bin/env python3
"""Verify that the unified PostgreSQL schema contains all elements from admin and app schemas."""

import re
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from stream_of_worship.db import postgres_schema as unified
from stream_of_worship.admin.db import schema as admin
from stream_of_worship.app.db import schema as app


def extract_tables_from_ddl(ddl: str) -> set[str]:
    """Extract table names from CREATE TABLE statements."""
    pattern = r"CREATE TABLE IF NOT EXISTS (\w+)"
    return set(re.findall(pattern, ddl))


def extract_indexes_from_list(index_list: list[str]) -> set[str]:
    """Extract index names from CREATE INDEX statements."""
    pattern = r"CREATE INDEX IF NOT EXISTS (\w+)"
    indexes = set()
    for stmt in index_list:
        indexes.update(re.findall(pattern, stmt))
    return indexes


def extract_triggers_from_ddl(ddl: str) -> set[str]:
    """Extract trigger names from CREATE TRIGGER statements."""
    pattern = r"CREATE TRIGGER (\w+)"
    return set(re.findall(pattern, ddl))


def extract_columns_from_ddl(ddl: str) -> set[str]:
    """Extract column names from CREATE TABLE statement."""
    lines = ddl.strip().split("\n")
    columns = set()
    for line in lines:
        line = line.strip()
        if line.startswith(")"):
            break
        match = re.match(r"(\w+)\s+", line)
        if match and match.group(1) not in ("CREATE", "TABLE", "PRIMARY", "FOREIGN", "UNIQUE", "REFERENCES"):
            columns.add(match.group(1))
    return columns


def verify_tables():
    """Verify all tables are defined in unified schema."""
    print("=" * 70)
    print("VERIFYING TABLES")
    print("=" * 70)
    
    unified_tables = set()
    unified_tables.update(extract_tables_from_ddl(unified.CREATE_SONGS_TABLE))
    unified_tables.update(extract_tables_from_ddl(unified.CREATE_RECORDINGS_TABLE))
    unified_tables.update(extract_tables_from_ddl(unified.CREATE_SONGSETS_TABLE))
    unified_tables.update(extract_tables_from_ddl(unified.CREATE_SONGSET_ITEMS_TABLE))
    
    admin_tables = set()
    admin_tables.update(extract_tables_from_ddl(admin.CREATE_SONGS_TABLE))
    admin_tables.update(extract_tables_from_ddl(admin.CREATE_RECORDINGS_TABLE))
    
    app_tables = set()
    app_tables.update(extract_tables_from_ddl(app.CREATE_SONGSETS_TABLE))
    app_tables.update(extract_tables_from_ddl(app.CREATE_SONGSET_ITEMS_TABLE))
    
    expected_tables = admin_tables | app_tables
    
    print(f"Unified schema tables: {sorted(unified_tables)}")
    print(f"Admin schema tables: {sorted(admin_tables)}")
    print(f"App schema tables: {sorted(app_tables)}")
    print(f"Expected tables (admin + app): {sorted(expected_tables)}")
    
    missing_tables = expected_tables - unified_tables
    extra_tables = unified_tables - expected_tables
    
    if missing_tables:
        print(f"\n❌ MISSING TABLES: {sorted(missing_tables)}")
        return False
    
    if extra_tables:
        print(f"\n⚠️  EXTRA TABLES in unified schema: {sorted(extra_tables)}")
    
    print("\n✅ All tables are defined in unified schema")
    return True


def verify_indexes():
    """Verify all indexes are created in unified schema."""
    print("\n" + "=" * 70)
    print("VERIFYING INDEXES")
    print("=" * 70)
    
    unified_indexes = set()
    unified_indexes.update(extract_indexes_from_list(unified.CREATE_ADMIN_INDEXES))
    unified_indexes.update(extract_indexes_from_list(unified.CREATE_APP_INDEXES))
    
    admin_indexes = extract_indexes_from_list(admin.CREATE_INDEXES)
    app_indexes = extract_indexes_from_list(app.CREATE_APP_INDEXES)
    
    expected_indexes = admin_indexes | app_indexes
    
    print(f"Unified schema indexes: {sorted(unified_indexes)}")
    print(f"Admin schema indexes: {sorted(admin_indexes)}")
    print(f"App schema indexes: {sorted(app_indexes)}")
    print(f"Expected indexes (admin + app): {sorted(expected_indexes)}")
    
    missing_indexes = expected_indexes - unified_indexes
    extra_indexes = unified_indexes - expected_indexes
    
    if missing_indexes:
        print(f"\n❌ MISSING INDEXES: {sorted(missing_indexes)}")
        return False
    
    if extra_indexes:
        print(f"\n⚠️  EXTRA INDEXES in unified schema: {sorted(extra_indexes)}")
    
    print("\n✅ All indexes are created in unified schema")
    return True


def verify_triggers():
    """Verify all triggers are defined in unified schema."""
    print("\n" + "=" * 70)
    print("VERIFYING TRIGGERS")
    print("=" * 70)
    
    unified_triggers = set()
    unified_triggers.update(extract_triggers_from_ddl(unified.CREATE_SONGS_UPDATE_TRIGGER))
    unified_triggers.update(extract_triggers_from_ddl(unified.CREATE_RECORDINGS_UPDATE_TRIGGER))
    unified_triggers.update(extract_triggers_from_ddl(unified.CREATE_SONGSETS_UPDATE_TRIGGER))
    
    admin_triggers = set()
    admin_triggers.update(extract_triggers_from_ddl(admin.CREATE_SONGS_UPDATE_TRIGGER))
    admin_triggers.update(extract_triggers_from_ddl(admin.CREATE_RECORDINGS_UPDATE_TRIGGER))
    
    app_triggers = extract_triggers_from_ddl(app.CREATE_SONGSETS_UPDATE_TRIGGER)
    
    expected_triggers = admin_triggers | app_triggers
    
    print(f"Unified schema triggers: {sorted(unified_triggers)}")
    print(f"Admin schema triggers: {sorted(admin_triggers)}")
    print(f"App schema triggers: {sorted(app_triggers)}")
    print(f"Expected triggers (admin + app): {sorted(expected_triggers)}")
    
    missing_triggers = expected_triggers - unified_triggers
    extra_triggers = unified_triggers - expected_triggers
    
    if missing_triggers:
        print(f"\n❌ MISSING TRIGGERS: {sorted(missing_triggers)}")
        return False
    
    if extra_triggers:
        print(f"\n⚠️  EXTRA TRIGGERS in unified schema: {sorted(extra_triggers)}")
    
    print("\n✅ All triggers are defined in unified schema")
    return True


def verify_columns():
    """Verify all columns are present, especially download_status in recordings."""
    print("\n" + "=" * 70)
    print("VERIFYING COLUMNS")
    print("=" * 70)
    
    all_passed = True
    
    # Check songs table
    print("\n--- Songs Table ---")
    unified_songs_cols = extract_columns_from_ddl(unified.CREATE_SONGS_TABLE)
    admin_songs_cols = extract_columns_from_ddl(admin.CREATE_SONGS_TABLE)
    
    missing_songs = admin_songs_cols - unified_songs_cols
    if missing_songs:
        print(f"❌ MISSING columns in songs: {sorted(missing_songs)}")
        all_passed = False
    else:
        print(f"✅ Songs table has all {len(unified_songs_cols)} columns")
    
    # Check recordings table
    print("\n--- Recordings Table ---")
    unified_recordings_cols = extract_columns_from_ddl(unified.CREATE_RECORDINGS_TABLE)
    admin_recordings_cols = extract_columns_from_ddl(admin.CREATE_RECORDINGS_TABLE)
    
    missing_recordings = admin_recordings_cols - unified_recordings_cols
    if missing_recordings:
        print(f"❌ MISSING columns in recordings: {sorted(missing_recordings)}")
        all_passed = False
    else:
        print(f"✅ Recordings table has all {len(unified_recordings_cols)} columns")
    
    # Specifically check for download_status
    if "download_status" in unified_recordings_cols:
        print("✅ download_status column is present in recordings table")
    else:
        print("❌ download_status column is MISSING in recordings table")
        all_passed = False
    
    # Check songsets table
    print("\n--- Songsets Table ---")
    unified_songsets_cols = extract_columns_from_ddl(unified.CREATE_SONGSETS_TABLE)
    app_songsets_cols = extract_columns_from_ddl(app.CREATE_SONGSETS_TABLE)
    
    missing_songsets = app_songsets_cols - unified_songsets_cols
    if missing_songsets:
        print(f"❌ MISSING columns in songsets: {sorted(missing_songsets)}")
        all_passed = False
    else:
        print(f"✅ Songsets table has all {len(unified_songsets_cols)} columns")
    
    # Check songset_items table
    print("\n--- Songset Items Table ---")
    unified_items_cols = extract_columns_from_ddl(unified.CREATE_SONGSET_ITEMS_TABLE)
    app_items_cols = extract_columns_from_ddl(app.CREATE_SONGSET_ITEMS_TABLE)
    
    missing_items = app_items_cols - unified_items_cols
    if missing_items:
        print(f"❌ MISSING columns in songset_items: {sorted(missing_items)}")
        all_passed = False
    else:
        print(f"✅ Songset items table has all {len(unified_items_cols)} columns")
    
    return all_passed


def verify_all_statements():
    """Verify ALL_SCHEMA_STATEMENTS contains all necessary statements."""
    print("\n" + "=" * 70)
    print("VERIFYING ALL_SCHEMA_STATEMENTS")
    print("=" * 70)
    
    expected_count = (
        4 +  # 4 tables
        len(unified.CREATE_ADMIN_INDEXES) +
        len(unified.CREATE_APP_INDEXES) +
        1 +  # trigger function
        3    # triggers
    )
    
    actual_count = len(unified.ALL_SCHEMA_STATEMENTS)
    
    print(f"Expected statement count: {expected_count}")
    print(f"Actual statement count: {actual_count}")
    
    if actual_count == expected_count:
        print("✅ ALL_SCHEMA_STATEMENTS contains all necessary statements")
        return True
    else:
        print(f"❌ Statement count mismatch: expected {expected_count}, got {actual_count}")
        return False


def main():
    """Run all verification checks."""
    print("\n" + "=" * 70)
    print("SCHEMA COMPLETENESS VERIFICATION")
    print("=" * 70)
    
    results = {
        "Tables": verify_tables(),
        "Indexes": verify_indexes(),
        "Triggers": verify_triggers(),
        "Columns": verify_columns(),
        "ALL_SCHEMA_STATEMENTS": verify_all_statements(),
    }
    
    print("\n" + "=" * 70)
    print("VERIFICATION SUMMARY")
    print("=" * 70)
    
    for name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{name}: {status}")
    
    all_passed = all(results.values())
    
    if all_passed:
        print("\n🎉 ALL VERIFICATIONS PASSED!")
        return 0
    else:
        print("\n❌ SOME VERIFICATIONS FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
