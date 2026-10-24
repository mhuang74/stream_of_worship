"""FastAPI application entry point."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .logging_config import configure_logging

from . import __version__
from .config import Settings, settings

# Configure logging with job_id support
configure_logging(
    level=getattr(logging, settings.SOW_LOG_LEVEL, logging.INFO),
    suppress_external=True,
)
logger = logging.getLogger(__name__)

from .routes import debug_dumps, health, jobs
from .routes.jobs import set_job_queue
from .services.modal_analyzer_client import get_modal_analyzer_client
from .services.modal_stem_separator_client import get_modal_stem_separator_client
from .workers.llm_trace import DEBUG_DUMPS_URL_PATH
from .workers.queue import JobQueue
from .workers.youtube_transcript import _redact_proxy_url

try:
    from .services.mvsep_client import MvsepClient
except ImportError:
    MvsepClient = None

try:
    from .services.qwen3_asr_client import Qwen3AsrClient
except ImportError:
    Qwen3AsrClient = None

# Global job queue instance
job_queue: JobQueue

# Global MVSEP client instance
mvsep_client: "MvsepClient | None" = None

# Global Qwen3 ASR client instance
qwen3_client: "Qwen3AsrClient | None" = None

# Global QuotaWaiter instances
mvsep_quota_waiter = None
qwen3_quota_waiter = None


# Settings fields whose values are credentials. Their startup-table rows show only
# "(set)" / "(not set)": no secret material is ever logged.
SECRET_FIELDS: frozenset[str] = frozenset(
    {
        "SOW_R2_ACCESS_KEY_ID",
        "SOW_R2_SECRET_ACCESS_KEY",
        "SOW_ANALYSIS_API_KEY",
        "SOW_ADMIN_API_KEY",
        "SOW_LLM_API_KEY",
        "SOW_EMBEDDING_API_KEY",
        "SOW_DASHSCOPE_API_KEY",
        "SOW_MVSEP_API_KEY",
    }
)

# Display category for every Settings field. The startup table is generated from
# Settings.model_fields, so a field missing here fails startup loudly (KeyError) and
# fails tests/test_startup_config_table.py instead of disappearing from the log.
_CATEGORY_FIELDS: dict[str, tuple[str, ...]] = {
    "Logging": ("SOW_LOG_LEVEL",),
    "R2": (
        "SOW_R2_BUCKET",
        "SOW_R2_ENDPOINT_URL",
        "SOW_R2_ACCESS_KEY_ID",
        "SOW_R2_SECRET_ACCESS_KEY",
    ),
    "API Security": ("SOW_ANALYSIS_API_KEY", "SOW_ADMIN_API_KEY"),
    "Cache": (
        "CACHE_DIR",
        "SOW_DEBUG_DUMP_DIR",
        "SOW_DEBUG_DUMP_PUBLIC_BASE_URL",
        "KEY_ALGORITHM_VERSION",
        "BPM_ALGORITHM_VERSION",
        "SOW_WHISPER_CACHE_VERSION",
        "SOW_ANALYSIS_CACHE_VERSION",
        "SOW_DASHSCOPE_ASR_CACHE_VERSION",
    ),
    "Processing": (
        "SOW_FAST_ANALYZE_MAX_CONCURRENT",
        "SOW_COMPONENT_MAX_CONCURRENT",
        "SOW_ANALYZE_MAX_CONCURRENT",
        "SOW_LRC_MAX_CONCURRENT",
        "SOW_QUEUE_START_DELAY_SECONDS",
        "SOW_STEP_HEARTBEAT_INTERVAL_SECONDS",
    ),
    "LRC Gap/Tail": (
        "SOW_LRC_GAP_THRESHOLD_BEATS",
        "SOW_LRC_GAP_PLACEHOLDER_BEATS",
        "SOW_LRC_TAIL_FILL_ENABLED",
        "SOW_LRC_TAIL_GAP_SECONDS",
        "SOW_LRC_TAIL_GAP_FRACTION",
        "SOW_LRC_TAIL_LEAD_IN_SECONDS",
        "SOW_LRC_TAIL_DUP_WINDOW_SECONDS",
        "SOW_LRC_TAIL_WHISPER_MAX_NO_SPEECH_PROB",
        "SOW_LRC_TAIL_MIN_USABLE_PHRASES",
    ),
    "LLM": (
        "SOW_LLM_API_KEY",
        "SOW_LLM_BASE_URL",
        "SOW_LLM_MODEL",
        "SOW_LLM_MAX_CONCURRENT",
        "SOW_LLM_RATE_LIMIT_MAX_RETRIES",
        "SOW_LLM_RATE_LIMIT_BASE_DELAY",
        "SOW_LLM_RATE_LIMIT_MAX_DELAY",
        "SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS",
        "SOW_LLM_MIN_INTERVAL_SECONDS",
        "SOW_LLM_CLASSIFICATION_TIMEOUT_SECONDS",
        "SOW_LLM_MODEL_VALIDATION_TIMEOUT_SECONDS",
    ),
    "LLM Segmentation": (
        "SOW_COMPONENTS_USE_LLM_SEGMENTATION",
        "SOW_LLM_SEGMENTATION_MODEL",
        "SOW_LLM_SEGMENTATION_TIMEOUT_SECONDS",
        "SOW_LLM_STRUCTURED_LYRICS_TIMEOUT_SECONDS",
        "SOW_LLM_STRUCTURED_LYRICS_MAX_ATTEMPTS",
        "SOW_LLM_SEGMENTATION_SANITY_CHECK",
        "SOW_LLM_SEGMENTATION_MAX_TOKENS",
    ),
    "Embedding": ("SOW_EMBEDDING_API_KEY", "SOW_EMBEDDING_BASE_URL", "SOW_EMBEDDING_MODEL"),
    "Whisper (Modal)": (
        "SOW_MODAL_WHISPER_APP_NAME",
        "SOW_MODAL_WHISPER_CLASS_NAME",
        "SOW_MODAL_WHISPER_MAX_ATTEMPTS",
        "SOW_MODAL_WHISPER_RETRY_DELAY_SECONDS",
        "SOW_MODAL_WHISPER_MAX_AUDIO_BYTES",
        "SOW_MODAL_WHISPER_ALLOWED_MODELS",
    ),
    "Forced Aligner (Modal)": (
        "SOW_MODAL_FORCED_ALIGNER_APP_NAME",
        "SOW_MODAL_FORCED_ALIGNER_CLASS_NAME",
        "SOW_MODAL_FORCED_ALIGNER_MAX_ATTEMPTS",
        "SOW_MODAL_FORCED_ALIGNER_RETRY_DELAY_SECONDS",
        "SOW_MODAL_FORCED_ALIGNER_MAX_AUDIO_BYTES",
    ),
    "Analyzer (Modal)": (
        "SOW_MODAL_ANALYZER_APP_NAME",
        "SOW_MODAL_ANALYZER_CLASS_NAME",
        "SOW_MODAL_ANALYZER_MAX_ATTEMPTS",
        "SOW_MODAL_ANALYZER_RETRY_DELAY_SECONDS",
        "SOW_MODAL_ANALYZER_MAX_AUDIO_BYTES",
    ),
    "Stem Separator (Modal)": (
        "SOW_MODAL_STEM_APP_NAME",
        "SOW_MODAL_STEM_CLASS_NAME",
        "SOW_MODAL_STEM_MAX_ATTEMPTS",
        "SOW_MODAL_STEM_RETRY_DELAY_SECONDS",
        "SOW_MODAL_STEM_MAX_AUDIO_BYTES",
        "SOW_VOCAL_SEPARATION_MODEL",
        "SOW_DEREVERB_MODEL",
    ),
    "DashScope Qwen3 ASR": (
        "SOW_DASHSCOPE_API_KEY",
        "SOW_DASHSCOPE_ASR_REGION",
        "SOW_DASHSCOPE_ASR_FLASH_MODEL",
        "SOW_DASHSCOPE_ASR_FILETRANS_MODEL",
        "SOW_DASHSCOPE_ASR_CONTEXT_MAX_CHARS",
        "SOW_DASHSCOPE_ASR_TIMEOUT_SECONDS",
        "SOW_DASHSCOPE_ASR_FILETRANS_TIMEOUT_SECONDS",
        "SOW_DASHSCOPE_ASR_MAX_CONCURRENT",
    ),
    "MVSEP": (
        "SOW_MVSEP_API_KEY",
        "SOW_MVSEP_ENABLED",
        "SOW_MVSEP_STAGE1_SEP_TYPE",
        "SOW_MVSEP_STAGE1_ADD_OPT1",
        "SOW_MVSEP_STAGE1_ADD_OPT2",
        "SOW_MVSEP_STAGE2_SEP_TYPE",
        "SOW_MVSEP_STAGE2_ADD_OPT1",
        "SOW_MVSEP_STAGE2_ADD_OPT2",
        "SOW_MVSEP_HTTP_TIMEOUT",
        "SOW_MVSEP_STAGE_TIMEOUT",
        "SOW_MVSEP_STAGE2_TIMEOUT",
        "SOW_MVSEP_TOTAL_TIMEOUT",
        "SOW_MVSEP_MAX_CONCURRENT",
    ),
    "Quota Waiting": (
        "SOW_QUOTA_POLL_INTERVAL_SECONDS",
        "SOW_QUOTA_WAIT_QUIESCENT_LOG_INTERVAL_SECONDS",
        "SOW_PARENT_STEM_WAIT_TIMEOUT_SECONDS",
    ),
    "YouTube": (
        "SOW_YOUTUBE_PROXY",
        "SOW_YOUTUBE_PROXY_RETRIES",
        "SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT",
        "SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS",
        "SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES",
        "SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY",
        "SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD",
        "SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN",
    ),
}

# Field -> category lookup plus display rank, derived from _CATEGORY_FIELDS so the
# two can never disagree.
_CATEGORY_BY_FIELD: dict[str, str] = {
    field: category for category, fields in _CATEGORY_FIELDS.items() for field in fields
}
_CATEGORY_RANK: dict[str, int] = {name: rank for rank, name in enumerate(_CATEGORY_FIELDS)}

# Category shown for a Settings field missing from _CATEGORY_FIELDS. Never used today
# (tests/test_startup_config_table.py asserts no row lands here); it exists so a config
# table bug cannot take the service down at startup.
_UNCATEGORIZED = "Other"


def _format_setting_value(name: str, value: object) -> str:
    """Render one Settings value for the startup table.

    Secrets collapse to "(set)"/"(not set)"; an empty string or None means "unset";
    the YouTube proxy is logged with its userinfo redacted.
    """
    if name in SECRET_FIELDS:
        return "(set)" if value else "(not set)"
    if name == "SOW_YOUTUBE_PROXY":
        return (_redact_proxy_url(str(value)) or "(unparseable)") if value else "(not set)"
    if value is None:
        return "(not set)"
    if isinstance(value, str) and not value:
        return "(not set)"
    return str(value)


def _startup_config_rows(cfg: Settings | None = None) -> list[tuple[str, str, str]]:
    """Build (category, setting, value) rows for every env-derived setting.

    Covers all of Settings.model_fields plus the env-only Modal token flags. ``cfg``
    defaults to the module-level settings; tests pass their own Settings instance.

    A field with no _CATEGORY_FIELDS entry renders under _UNCATEGORIZED rather than
    raising: the table is diagnostics only, and tests/test_startup_config_table.py fails
    on the drift instead.
    """
    cfg = settings if cfg is None else cfg
    uncategorized_rank = len(_CATEGORY_RANK)

    def _sort_key(field: str) -> tuple[int, str]:
        category = _CATEGORY_BY_FIELD.get(field)
        return (_CATEGORY_RANK.get(category, uncategorized_rank), field)

    rows = [
        (
            _CATEGORY_BY_FIELD.get(name, _UNCATEGORIZED),
            name,
            _format_setting_value(name, getattr(cfg, name)),
        )
        for name in sorted(Settings.model_fields, key=_sort_key)
    ]
    modal_token_id = os.environ.get("MODAL_TOKEN_ID")
    modal_token_secret = os.environ.get("MODAL_TOKEN_SECRET")
    rows.extend(
        [
            ("Modal", "MODAL_TOKEN_ID", "(set)" if modal_token_id else "(not set)"),
            ("Modal", "MODAL_TOKEN_SECRET", "(set)" if modal_token_secret else "(not set)"),
            (
                "Modal",
                "modal_token_configured",
                str(bool(modal_token_id and modal_token_secret)),
            ),
        ]
    )
    return rows


def _render_startup_config_table(rows: list[tuple[str, str, str]]) -> str:
    """Render rows as a 3-column ASCII box for the startup log."""
    headers = ("Category", "Setting", "Value")
    col_widths = [max(len(r[i]) for r in rows + [headers]) for i in range(3)]
    separator = f"+-{'-' * col_widths[0]}-+-{'-' * col_widths[1]}-+-{'-' * col_widths[2]}-+"
    table_lines = [
        separator,
        f"| {headers[0]:<{col_widths[0]}} | {headers[1]:<{col_widths[1]}} | {headers[2]:<{col_widths[2]}} |",
        separator,
    ]
    for row in rows:
        table_lines.append(
            f"| {row[0]:<{col_widths[0]}} | {row[1]:<{col_widths[1]}} | {row[2]:<{col_widths[2]}} |"
        )
    table_lines.append(separator)
    return "\n".join(table_lines)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan.

    Args:
        app: FastAPI application

    Yields:
        None
    """
    global job_queue, mvsep_client
    global qwen3_client, mvsep_quota_waiter, qwen3_quota_waiter

    # Serve and index the trace dump directory even before the first dump exists.
    try:
        settings.SOW_DEBUG_DUMP_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("Cannot create debug dump directory %s: %s", settings.SOW_DEBUG_DUMP_DIR, e)

    if settings.SOW_DEBUG_DUMP_PUBLIC_BASE_URL:
        logger.info(
            "LLM trace dumps served at %s%s/",
            settings.SOW_DEBUG_DUMP_PUBLIC_BASE_URL.rstrip("/"),
            DEBUG_DUMPS_URL_PATH,
        )

    # Startup
    job_queue = JobQueue(
        max_concurrent_analyze=settings.SOW_ANALYZE_MAX_CONCURRENT,
        cache_dir=settings.CACHE_DIR,
    )

    # Initialize persistent store and recover interrupted jobs
    await job_queue.initialize()

    # Initialize R2 if configured
    if settings.SOW_R2_ENDPOINT_URL:
        job_queue.initialize_r2(settings.SOW_R2_BUCKET, settings.SOW_R2_ENDPOINT_URL)

    # Initialize MVSEP client if configured
    if MvsepClient is not None:
        if settings.SOW_MVSEP_API_KEY and settings.SOW_MVSEP_ENABLED:
            mvsep_client = MvsepClient()
            job_queue.set_mvsep_client(mvsep_client)
            logger.info("MVSEP client initialized (cloud stem separation enabled)")
            if not mvsep_client.is_available:
                logger.warning(
                    "MVSEP unavailable at startup (missing API key, disabled, or quota "
                    "already exhausted). Stem-separation jobs will wait for quota or fail "
                    "over to Modal."
                )
        else:
            logger.info("MVSEP not configured (stem separation runs on Modal only)")
    else:
        logger.warning("MvsepClient not available")

    # Create Qwen3 ASR client singleton if configured
    qwen3_client = None
    if Qwen3AsrClient is not None:
        if settings.SOW_DASHSCOPE_API_KEY:
            qwen3_client = Qwen3AsrClient(
                api_key=settings.SOW_DASHSCOPE_API_KEY,
                region=settings.SOW_DASHSCOPE_ASR_REGION,
                flash_model=settings.SOW_DASHSCOPE_ASR_FLASH_MODEL,
                filetrans_model=settings.SOW_DASHSCOPE_ASR_FILETRANS_MODEL,
            )
            job_queue.set_qwen3_client(qwen3_client)
            logger.info("Qwen3 ASR client singleton initialized")
        else:
            logger.info("Qwen3 ASR not configured (SOW_DASHSCOPE_API_KEY not set)")
    else:
        logger.warning("Qwen3AsrClient not available")

    # Create QuotaWaiters (jobs block until the daily quota resets)
    mvsep_quota_waiter = None
    qwen3_quota_waiter = None
    if mvsep_client is not None:
        from .workers.quota_waiter import QuotaWaiter

        mvsep_quota_waiter = QuotaWaiter(
            "mvsep",
            lambda: mvsep_client.is_available,
            settings.SOW_QUOTA_POLL_INTERVAL_SECONDS,
        )
    if qwen3_client is not None:
        from .workers.quota_waiter import QuotaWaiter

        qwen3_quota_waiter = QuotaWaiter(
            "qwen3",
            lambda: qwen3_client.is_available,
            settings.SOW_QUOTA_POLL_INTERVAL_SECONDS,
        )
    job_queue.set_quota_waiters(mvsep=mvsep_quota_waiter, qwen3=qwen3_quota_waiter)

    # Create the Modal stem separator client (sow-stems). MVSEP remains the primary
    # backend; this client only runs when MVSEP is unavailable or a stage exhausts retries.
    stem_separator = get_modal_stem_separator_client()
    job_queue.set_stem_separator(stem_separator)
    logger.info(
        "Modal stem separator client: app=%s class=%s configured=%s",
        stem_separator.app_name,
        stem_separator.class_name,
        stem_separator.is_configured,
    )
    if not stem_separator.is_configured:
        logger.warning(
            "MODAL_TOKEN_ID/MODAL_TOKEN_SECRET are not set: stem separation jobs will fail "
            "once MVSEP is unavailable (there is no in-container fallback)"
        )

    # Create the Modal analyzer client (sow-analyzer): backs full ANALYZE jobs and
    # madmom beat-grid detection misses. There is no in-container fallback.
    analyzer_client = get_modal_analyzer_client()
    job_queue.set_analyzer_client(analyzer_client)
    logger.info(
        "Modal analyzer client: app=%s class=%s configured=%s",
        analyzer_client.app_name,
        analyzer_client.class_name,
        analyzer_client.is_configured,
    )
    if not analyzer_client.is_configured:
        logger.warning(
            "MODAL_TOKEN_ID/MODAL_TOKEN_SECRET are not set: ANALYZE jobs will fail with "
            "ModalAnalyzerConfigError (there is no in-container fallback)"
        )

    # Log startup configuration (non-sensitive values only). Rows are generated from
    # Settings.model_fields, so a new env-derived setting can never be omitted; secret
    # fields render as "(set)"/"(not set)".
    logger.info("Startup configuration:\n%s", _render_startup_config_table(_startup_config_rows()))

    # Set job queue in routes
    set_job_queue(job_queue)

    # Start background job processor
    task = asyncio.create_task(job_queue.process_jobs())

    yield

    # Shutdown
    await job_queue.stop()

    # Cleanup MVSEP client
    if mvsep_client is not None:
        await mvsep_client.aclose()
        logger.info("MVSEP client closed")

    # Stop QuotaWaiters
    if mvsep_quota_waiter is not None:
        await mvsep_quota_waiter.stop()
        logger.info("MVSEP quota waiter stopped")
    if qwen3_quota_waiter is not None:
        await qwen3_quota_waiter.stop()
        logger.info("Qwen3 quota waiter stopped")

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Stream of Worship Analysis Service",
    version=__version__,
    lifespan=lifespan,
)

app.include_router(health.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(debug_dumps.router, prefix=DEBUG_DUMPS_URL_PATH)
# Registered after the index routes: Mount matches the prefix and would shadow them.
# check_dir=False: the dump dir is created lazily on the first trace / in lifespan startup.
app.mount(
    DEBUG_DUMPS_URL_PATH,
    StaticFiles(directory=str(settings.SOW_DEBUG_DUMP_DIR), html=False, check_dir=False),
    name="debug-dumps",
)


@app.get("/")
async def root() -> dict:
    """Root endpoint.

    Returns:
        Service info
    """
    return {
        "message": "Stream of Worship Analysis Service",
        "version": __version__,
    }


def main() -> None:
    """Entry point for running the service directly."""
    import uvicorn

    uvicorn.run(
        "sow_analysis.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
