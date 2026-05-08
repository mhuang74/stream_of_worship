-- Stream of Worship - Verification SQL (v4 runbook)
-- Run against target PostgreSQL database after loading and post-load steps.

-- =============================================================================
-- 1. Row count parity with expected source counts
-- =============================================================================
SELECT 'songs' AS table_name, COUNT(*) AS row_count FROM songs
UNION ALL
SELECT 'recordings', COUNT(*) FROM recordings
UNION ALL
SELECT 'songsets', COUNT(*) FROM songsets
UNION ALL
SELECT 'songset_items', COUNT(*) FROM songset_items;

-- =============================================================================
-- 2. Integrity: duplicate primary keys (should return zero rows)
-- =============================================================================
SELECT
    'songs duplicate id' AS check_name,
    id,
    COUNT(*) AS cnt
FROM songs
GROUP BY id
HAVING COUNT(*) > 1;

SELECT
    'recordings duplicate content_hash' AS check_name,
    content_hash,
    COUNT(*) AS cnt
FROM recordings
GROUP BY content_hash
HAVING COUNT(*) > 1;

SELECT
    'recordings duplicate hash_prefix' AS check_name,
    hash_prefix,
    COUNT(*) AS cnt
FROM recordings
GROUP BY hash_prefix
HAVING COUNT(*) > 1;

-- =============================================================================
-- 3. Foreign key integrity (should return zero rows)
-- =============================================================================
SELECT
    'orphan recording (song_id missing)' AS check_name,
    r.content_hash,
    r.song_id
FROM recordings r
LEFT JOIN songs s ON s.id = r.song_id
WHERE r.song_id IS NOT NULL AND s.id IS NULL;

SELECT
    'orphan songset_item (songset_id missing)' AS check_name,
    si.id,
    si.songset_id
FROM songset_items si
LEFT JOIN songsets ss ON ss.id = si.songset_id
WHERE ss.id IS NULL;

-- =============================================================================
-- 4. Hash prefix consistency (should return zero rows)
-- =============================================================================
SELECT
    'hash_prefix mismatch' AS check_name,
    content_hash,
    hash_prefix
FROM recordings
WHERE hash_prefix <> SUBSTRING(content_hash FROM 1 FOR 12);

-- =============================================================================
-- 5. Status distributions (compare with source)
-- =============================================================================
SELECT
    'analysis_status distribution' AS metric,
    analysis_status,
    COUNT(*) AS cnt
FROM recordings
GROUP BY analysis_status
ORDER BY analysis_status;

SELECT
    'lrc_status distribution' AS metric,
    lrc_status,
    COUNT(*) AS cnt
FROM recordings
GROUP BY lrc_status
ORDER BY lrc_status;

SELECT
    'visibility_status distribution' AS metric,
    visibility_status,
    COUNT(*) AS cnt
FROM recordings
GROUP BY visibility_status
ORDER BY visibility_status;

SELECT
    'download_status distribution' AS metric,
    download_status,
    COUNT(*) AS cnt
FROM recordings
GROUP BY download_status
ORDER BY download_status;

-- =============================================================================
-- 6. App-critical query: LRC-ready published songs
-- =============================================================================
SELECT
    'lrc_ready_published' AS metric,
    COUNT(*) AS cnt
FROM songs s
JOIN recordings r ON s.id = r.song_id
WHERE r.lrc_status = 'completed'
  AND r.visibility_status = 'published'
  AND r.deleted_at IS NULL
  AND s.deleted_at IS NULL;

-- =============================================================================
-- 7. App-critical query: active counts
-- =============================================================================
SELECT
    'active_songs' AS metric,
    COUNT(*) AS cnt
FROM songs
WHERE deleted_at IS NULL;

SELECT
    'active_recordings' AS metric,
    COUNT(*) AS cnt
FROM recordings
WHERE deleted_at IS NULL;

SELECT
    'analyzed_recordings' AS metric,
    COUNT(*) AS cnt
FROM recordings
WHERE analysis_status = 'completed' AND deleted_at IS NULL;

-- =============================================================================
-- 8. Spot-check: sample rows for manual inspection
-- =============================================================================
SELECT
    id, title, scraped_at, created_at, updated_at, deleted_at
FROM songs
ORDER BY id
LIMIT 5;

SELECT
    content_hash, hash_prefix, song_id, imported_at,
    analysis_status, lrc_status, visibility_status, download_status,
    created_at, updated_at, deleted_at
FROM recordings
ORDER BY content_hash
LIMIT 5;

-- =============================================================================
-- 9. JSON/text payload spot-check (parse test)
-- =============================================================================
-- Run these manually if needed; they report rows where JSON is unparsable.
-- If 02_load_data.py --validate-json passed, these should all be clean.
--
-- SELECT id FROM songs WHERE lyrics_lines IS NOT NULL AND jsonb_typeof(lyrics_lines::jsonb) IS NULL;
-- SELECT id FROM songs WHERE sections IS NOT NULL AND jsonb_typeof(sections::jsonb) IS NULL;
-- SELECT content_hash FROM recordings WHERE beats IS NOT NULL AND jsonb_typeof(beats::jsonb) IS NULL;
-- SELECT content_hash FROM recordings WHERE downbeats IS NOT NULL AND jsonb_typeof(downbeats::jsonb) IS NULL;
-- SELECT content_hash FROM recordings WHERE sections IS NOT NULL AND jsonb_typeof(sections::jsonb) IS NULL;
-- SELECT content_hash FROM recordings WHERE embeddings_shape IS NOT NULL AND jsonb_typeof(embeddings_shape::jsonb) IS NULL;

-- =============================================================================
-- 10. Privilege verification placeholders (run with app role DSN)
-- =============================================================================
-- SELECT current_user, current_database(),
--        has_table_privilege(current_user, 'public.songs', 'INSERT') AS can_insert_songs,
--        has_table_privilege(current_user, 'public.recordings', 'INSERT') AS can_insert_recordings,
--        has_table_privilege(current_user, 'public.songsets', 'INSERT') AS can_insert_songsets,
--        has_table_privilege(current_user, 'public.songset_items', 'INSERT') AS can_insert_songset_items;
