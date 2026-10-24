"""Shared constants mirroring the webapp's constants.ts.

NOTE: these duplicate ``delivery/webapp/src/lib/constants.ts:1-2``. When a shared
config source lands, prefer that and remove this module.
"""

SONGSET_MAX_SONGS = 5
SONGSET_MAX_DURATION_SECONDS = 1500  # 25 minutes

# Mirrors the analysis service's SOW_FORCED_ALIGN_MAX_TOTAL_SECONDS. Recordings
# longer than this cannot be aligned; 300-900 s recordings are chunked service-side.
FORCED_ALIGN_MAX_DURATION_SECONDS = 900.0
