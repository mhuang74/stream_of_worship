"""Shared constants mirroring the webapp's constants.ts.

NOTE: these duplicate ``delivery/webapp/src/lib/constants.ts:1-2``. When a shared
config source lands, prefer that and remove this module.
"""

SONGSET_MAX_SONGS = 5
SONGSET_MAX_DURATION_SECONDS = 1500  # 25 minutes

# Mirrors the analysis service's SOW_FORCED_ALIGN_MAX_TOTAL_SECONDS. Recordings
# longer than this cannot be aligned; 300-900 s recordings are chunked service-side.
FORCED_ALIGN_MAX_DURATION_SECONDS = 900.0

# ---- CLI job-wait budgets (client-side polling limits) ----
# Default budget for waiting on one analysis-service job (20 minutes).
JOB_WAIT_TIMEOUT_SECONDS = 1200.0
# `audio analyze --analysis-tier full` (allin1 structure + stems) needs more (30 minutes).
ANALYZE_JOB_WAIT_TIMEOUT_SECONDS = 1800.0
# `audio components --compute-all-fields` adds per-component audio metadata + LLM
# classification and routinely exceeds 10 minutes (30 minutes).
COMPONENT_JOB_WAIT_TIMEOUT_SECONDS = 1800.0
