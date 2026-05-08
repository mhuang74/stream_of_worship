-- Stream of Worship - Post-Load SQL (v4 runbook)
-- Run after 02_load_data.py completes successfully.
--
-- This script:
--   1. Runs ANALYZE for query planner
--   2. Verifies constraints
--   3. Enables row-level security if needed (placeholder)
--   4. Checks orphaned FK references

-- =============================================================================
-- 1. Refresh statistics for query planner
-- =============================================================================
ANALYZE songs;
ANALYZE recordings;
ANALYZE songsets;
ANALYZE songset_items;

-- =============================================================================
-- 2. Verify primary key uniqueness (should return zero rows)
-- =============================================================================
SELECT 'songs duplicate PK' AS check_name,
       COUNT(*) AS violation_count
FROM songs
GROUP BY id
HAVING COUNT(*) > 1

UNION ALL

SELECT 'recordings duplicate PK' AS check_name,
       COUNT(*) AS violation_count
FROM recordings
GROUP BY content_hash
HAVING COUNT(*) > 1

UNION ALL

SELECT 'recordings duplicate hash_prefix' AS check_name,
       COUNT(*) AS violation_count
FROM recordings
GROUP BY hash_prefix
HAVING COUNT(*) > 1

UNION ALL

SELECT 'songsets duplicate PK' AS check_name,
       COUNT(*) AS violation_count
FROM songsets
GROUP BY id
HAVING COUNT(*) > 1

UNION ALL

SELECT 'songset_items duplicate PK' AS check_name,
       COUNT(*) AS violation_count
FROM songset_items
GROUP BY id
HAVING COUNT(*) > 1;

-- =============================================================================
-- 3. Verify foreign key integrity (should return zero rows)
-- =============================================================================
SELECT 'orphan recordings (song_id)' AS check_name,
       COUNT(*) AS violation_count
FROM recordings r
LEFT JOIN songs s ON s.id = r.song_id
WHERE r.song_id IS NOT NULL AND s.id IS NULL

UNION ALL

SELECT 'orphan songset_items (songset_id)' AS check_name,
       COUNT(*) AS violation_count
FROM songset_items si
LEFT JOIN songsets ss ON ss.id = si.songset_id
WHERE ss.id IS NULL;

-- =============================================================================
-- 4. Verify hash_prefix consistency (should return zero rows)
-- =============================================================================
SELECT 'hash_prefix mismatch' AS check_name,
       COUNT(*) AS violation_count
FROM recordings
WHERE hash_prefix <> SUBSTRING(content_hash FROM 1 FOR 12);

-- =============================================================================
-- 5. Verify soft-delete columns are consistent (no NULL/empty confusion)
-- =============================================================================
SELECT 'songs deleted_at check' AS check_name,
       COUNT(*) AS total_deleted
FROM songs
WHERE deleted_at IS NOT NULL;

SELECT 'recordings deleted_at check' AS check_name,
       COUNT(*) AS total_deleted
FROM recordings
WHERE deleted_at IS NOT NULL;

-- =============================================================================
-- 6. Report final counts
-- =============================================================================
SELECT 'songs' AS table_name, COUNT(*) AS row_count FROM songs
UNION ALL
SELECT 'recordings', COUNT(*) FROM recordings
UNION ALL
SELECT 'songsets', COUNT(*) FROM songsets
UNION ALL
SELECT 'songset_items', COUNT(*) FROM songset_items;
