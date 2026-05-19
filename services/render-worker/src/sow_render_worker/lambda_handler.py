import asyncio
import json
import logging
import traceback

from sow_render_worker.config import load_config
from sow_render_worker.db import get_connection
from sow_render_worker.pipeline import execute_render_pipeline

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def _parse_record_body(body: str) -> dict:
    return json.loads(body)


def _extract_job_fields(record_data: dict) -> tuple[str, int]:
    job_id = record_data["jobId"]
    user_id = int(record_data["userId"])
    return job_id, user_id


def _process_record(record: dict) -> None:
    body = record.get("body", "{}")
    record_data = _parse_record_body(body)
    job_id, user_id = _extract_job_fields(record_data)

    logger.info(
        "Processing render job",
        extra={"job_id": job_id, "user_id": user_id},
    )

    config = load_config()
    conn = get_connection(config.DATABASE_URL)

    try:
        asyncio.run(execute_render_pipeline(job_id, user_id, conn))
        logger.info(
            "Render job completed successfully",
            extra={"job_id": job_id, "user_id": user_id},
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


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

    failed_records = []

    for i, record in enumerate(records):
        message_id = record.get("messageId", f"record_{i}")
        try:
            _process_record(record)
        except Exception as exc:
            body = record.get("body", "{}")
            logger.error(
                "Failed to process SQS record %s: %s",
                message_id,
                exc,
                extra={
                    "message_id": message_id,
                    "record_index": i,
                    "record_body": body,
                    "error_type": type(exc).__name__,
                },
            )
            logger.debug("Traceback: %s", traceback.format_exc())
            failed_records.append({"messageId": message_id, "error": str(exc)})

    if failed_records:
        error_summary = json.dumps(
            {
                "message": "Batch item failures",
                "failed_records": failed_records,
            }
        )
        raise RuntimeError(error_summary)

    return {"statusCode": 200, "body": json.dumps({"message": "All records processed successfully"})}
