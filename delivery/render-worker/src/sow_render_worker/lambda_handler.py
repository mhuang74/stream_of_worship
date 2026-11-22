import json
import logging
import time
import traceback

from sow_render_worker.config import load_config
from sow_render_worker.db import GLUE_TERMINAL_PHASES, finish_glue_job, get_connection, get_glue_job
from sow_render_worker.glue_jobs import execute_glue_job
from sow_render_worker.pipeline import execute_render_pipeline

logger = logging.getLogger(__name__)
logging.getLogger().setLevel(logging.INFO)

RENDER_MESSAGE_KIND = "render"
GLUE_MESSAGE_KIND = "glue.generate"


def _parse_record(record: dict) -> tuple[dict, str]:
    body = record.get("body", "{}")
    try:
        record_data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in SQS message body: {exc}") from exc

    if not isinstance(record_data, dict):
        raise ValueError(f"SQS message body is not a JSON object: {type(record_data).__name__}")
    return record_data, record_data.get("kind") or RENDER_MESSAGE_KIND


def _verify_glue_message(conn, job_id: str, user_id: int, songset_id: str) -> bool:
    """Verify the message identity against the stored job before any work.

    Returns False for a mismatch. The message is acknowledged (never retried)
    because redelivering the same stale identity cannot succeed; a songset
    mismatch terminates the stored job as a safe failure.
    """
    job = get_glue_job(conn, job_id, user_id)
    if job is None:
        logger.warning(
            "Glue message references no job for this owner; acknowledging",
            extra={"job_id": job_id, "user_id": user_id},
        )
        return False
    if job.songset_id != songset_id:
        logger.error(
            "Glue message songsetId does not match the stored job",
            extra={"job_id": job_id, "user_id": user_id},
        )
        if job.active and job.phase not in GLUE_TERMINAL_PHASES:
            finish_glue_job(
                conn,
                job_id,
                user_id,
                phase="failed",
                error_code="GLUE_WORKER_FAILED",
                error_detail="Message does not match the stored job",
                retry_kind="none",
            )
        return False
    return True


def _process_record(record: dict, config, conn, context) -> None:
    record_data, kind = _parse_record(record)

    job_id = record_data.get("jobId")
    user_id = record_data.get("userId")
    if not job_id:
        raise ValueError("SQS message body missing required field 'jobId'")
    if not user_id:
        raise ValueError("SQS message body missing required field 'userId'")
    user_id = int(user_id)

    if kind == RENDER_MESSAGE_KIND:
        logger.info(
            "Processing render job",
            extra={"job_id": job_id, "user_id": user_id},
        )

        start = time.monotonic()
        execute_render_pipeline(job_id, user_id, conn, lambda_context=context)
        duration = time.monotonic() - start

        logger.info(
            "Render job completed successfully in %.1fs",
            duration,
            extra={"job_id": job_id, "user_id": user_id, "duration_seconds": duration},
        )
        return

    if kind == GLUE_MESSAGE_KIND:
        songset_id = record_data.get("songsetId")
        if not songset_id:
            raise ValueError(
                "SQS message body missing required field 'songsetId' for kind 'glue.generate'"
            )
        logger.info(
            "Processing glue job",
            extra={"job_id": job_id, "user_id": user_id},
        )
        if not _verify_glue_message(conn, job_id, user_id, str(songset_id)):
            return

        start = time.monotonic()
        execute_glue_job(job_id, user_id, conn, lambda_context=context)
        duration = time.monotonic() - start

        logger.info(
            "Glue job delivery completed in %.1fs",
            duration,
            extra={"job_id": job_id, "user_id": user_id, "duration_seconds": duration},
        )
        return

    raise ValueError(f"Unknown SQS message kind: {kind!r}")


def handler(event, context):
    records = event.get("Records", [])

    if not records:
        logger.warning("Received event with no SQS records")
        return {"statusCode": 200, "body": json.dumps({"message": "No records to process"})}

    logger.info(
        "Received SQS event with %d record(s)",
        len(records),
        extra={"record_count": len(records)},
    )

    config = load_config()
    conn = None
    try:
        conn = get_connection(config.SOW_DATABASE_URL)

        batch_item_failures = []

        for i, record in enumerate(records):
            message_id = record.get("messageId", f"record_{i}")
            try:
                try:
                    conn.rollback()
                except Exception:
                    pass
                _process_record(record, config, conn, context)
            except Exception as exc:
                logger.error(
                    "Failed to process SQS record %s: %s",
                    message_id,
                    exc,
                    extra={
                        "message_id": message_id,
                        "record_index": i,
                        "error_type": type(exc).__name__,
                    },
                )
                logger.debug("Traceback: %s", traceback.format_exc())
                batch_item_failures.append({"itemIdentifier": message_id})

        if batch_item_failures:
            return {"batchItemFailures": batch_item_failures}

        return {"statusCode": 200, "body": json.dumps({"message": "All records processed successfully"})}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
