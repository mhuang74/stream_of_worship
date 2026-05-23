import json
import logging
import time
import traceback

from sow_render_worker.config import load_config
from sow_render_worker.db import get_connection
from sow_render_worker.pipeline import execute_render_pipeline

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def _process_record(record: dict, config, conn, context) -> None:
    body = record.get("body", "{}")
    try:
        record_data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in SQS message body: {exc}") from exc

    if not isinstance(record_data, dict):
        raise ValueError(f"SQS message body is not a JSON object: {type(record_data).__name__}")

    job_id = record_data.get("jobId")
    user_id = record_data.get("userId")
    if not job_id:
        raise ValueError("SQS message body missing required field 'jobId'")
    if not user_id:
        raise ValueError("SQS message body missing required field 'userId'")
    user_id = int(user_id)

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
