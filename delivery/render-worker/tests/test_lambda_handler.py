import json
from unittest.mock import MagicMock, patch

import pytest

from sow_render_worker.lambda_handler import (
    _process_record,
    handler,
)


def _make_sqs_record(job_id="job_abc123", user_id=42, songset_id="ss_001", message_id="msg-001"):
    return {
        "messageId": message_id,
        "body": json.dumps({"jobId": job_id, "userId": user_id, "songsetId": songset_id}),
        "eventSource": "aws:sqs",
        "eventSourceARN": "arn:aws:sqs:us-east-1:123456789:test-queue",
    }


def _make_sqs_event(records):
    return {"Records": records}


class TestProcessRecord:
    @patch("sow_render_worker.lambda_handler.execute_render_pipeline")
    def test_success_path(self, mock_pipeline):
        mock_pipeline.return_value = None
        mock_conn = MagicMock()
        mock_config = MagicMock()
        mock_context = MagicMock()

        record = _make_sqs_record()
        _process_record(record, mock_config, mock_conn, mock_context)

        mock_pipeline.assert_called_once_with("job_abc123", 42, mock_conn, lambda_context=mock_context)

    @patch("sow_render_worker.lambda_handler.execute_render_pipeline")
    def test_pipeline_failure_raises(self, mock_pipeline):
        mock_pipeline.side_effect = RuntimeError("render failed")
        mock_conn = MagicMock()
        mock_config = MagicMock()
        mock_context = MagicMock()

        record = _make_sqs_record()
        with pytest.raises(RuntimeError, match="render failed"):
            _process_record(record, mock_config, mock_conn, mock_context)

    @patch("sow_render_worker.lambda_handler.execute_render_pipeline")
    def test_uses_config_database_url(self, mock_pipeline):
        mock_pipeline.return_value = None
        mock_config_obj = MagicMock()
        mock_config_obj.SOW_DATABASE_URL = "postgresql://test:test@localhost/db"
        mock_conn = MagicMock()
        mock_context = MagicMock()

        record = _make_sqs_record()
        _process_record(record, mock_config_obj, mock_conn, mock_context)

        mock_pipeline.assert_called_once_with("job_abc123", 42, mock_conn, lambda_context=mock_context)


class TestHandler:
    @patch("sow_render_worker.lambda_handler.get_connection")
    @patch("sow_render_worker.lambda_handler.load_config")
    @patch("sow_render_worker.lambda_handler._process_record")
    def test_single_record_success(self, mock_process, mock_config, mock_conn_func):
        mock_conn = MagicMock()
        mock_conn_func.return_value = mock_conn
        event = _make_sqs_event([_make_sqs_record()])
        result = handler(event, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "successfully" in body["message"]
        mock_process.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch("sow_render_worker.lambda_handler.get_connection")
    @patch("sow_render_worker.lambda_handler.load_config")
    @patch("sow_render_worker.lambda_handler._process_record")
    def test_multiple_records_all_success(self, mock_process, mock_config, mock_conn_func):
        mock_conn = MagicMock()
        mock_conn_func.return_value = mock_conn
        records = [
            _make_sqs_record(job_id="job_1", user_id=1, message_id="msg-1"),
            _make_sqs_record(job_id="job_2", user_id=2, message_id="msg-2"),
            _make_sqs_record(job_id="job_3", user_id=3, message_id="msg-3"),
        ]
        event = _make_sqs_event(records)
        result = handler(event, None)

        assert result["statusCode"] == 200
        assert mock_process.call_count == 3
        mock_conn.close.assert_called_once()

    @patch("sow_render_worker.lambda_handler.get_connection")
    @patch("sow_render_worker.lambda_handler.load_config")
    @patch("sow_render_worker.lambda_handler._process_record")
    def test_single_record_failure_returns_batch_item_failures(self, mock_process, mock_config, mock_conn_func):
        mock_conn = MagicMock()
        mock_conn_func.return_value = mock_conn
        mock_process.side_effect = RuntimeError("render error")
        event = _make_sqs_event([_make_sqs_record(message_id="msg-fail")])

        result = handler(event, None)

        assert "batchItemFailures" in result
        assert result["batchItemFailures"][0]["itemIdentifier"] == "msg-fail"
        mock_conn.close.assert_called_once()

    @patch("sow_render_worker.lambda_handler.get_connection")
    @patch("sow_render_worker.lambda_handler.load_config")
    @patch("sow_render_worker.lambda_handler._process_record")
    def test_multiple_records_partial_failure(self, mock_process, mock_config, mock_conn_func):
        mock_conn = MagicMock()
        mock_conn_func.return_value = mock_conn
        call_count = [0]

        def side_effect(record, config, conn, context):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("middle record failed")

        mock_process.side_effect = side_effect

        records = [
            _make_sqs_record(job_id="job_1", message_id="msg-1"),
            _make_sqs_record(job_id="job_2", message_id="msg-2"),
            _make_sqs_record(job_id="job_3", message_id="msg-3"),
        ]
        event = _make_sqs_event(records)

        result = handler(event, None)

        assert "batchItemFailures" in result
        assert len(result["batchItemFailures"]) == 1
        assert result["batchItemFailures"][0]["itemIdentifier"] == "msg-2"

    @patch("sow_render_worker.lambda_handler.get_connection")
    @patch("sow_render_worker.lambda_handler.load_config")
    @patch("sow_render_worker.lambda_handler._process_record")
    def test_empty_records_returns_200(self, mock_process, mock_config, mock_conn_func):
        mock_conn = MagicMock()
        mock_conn_func.return_value = mock_conn
        event = _make_sqs_event([])
        result = handler(event, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "No records" in body["message"]
        mock_process.assert_not_called()

    @patch("sow_render_worker.lambda_handler.get_connection")
    @patch("sow_render_worker.lambda_handler.load_config")
    @patch("sow_render_worker.lambda_handler._process_record")
    def test_no_records_key_returns_200(self, mock_process, mock_config, mock_conn_func):
        mock_conn = MagicMock()
        mock_conn_func.return_value = mock_conn
        event = {}
        result = handler(event, None)

        assert result["statusCode"] == 200
        mock_process.assert_not_called()

    @patch("sow_render_worker.lambda_handler.get_connection")
    @patch("sow_render_worker.lambda_handler.load_config")
    @patch("sow_render_worker.lambda_handler._process_record")
    def test_all_records_processed_even_on_failure(self, mock_process, mock_config, mock_conn_func):
        mock_conn = MagicMock()
        mock_conn_func.return_value = mock_conn
        mock_process.side_effect = RuntimeError("always fails")
        records = [
            _make_sqs_record(job_id="job_1", message_id="msg-1"),
            _make_sqs_record(job_id="job_2", message_id="msg-2"),
        ]
        event = _make_sqs_event(records)

        result = handler(event, None)

        assert mock_process.call_count == 2
        assert len(result["batchItemFailures"]) == 2

    @patch("sow_render_worker.lambda_handler.get_connection")
    @patch("sow_render_worker.lambda_handler.load_config")
    @patch("sow_render_worker.lambda_handler._process_record")
    def test_conn_closed_on_failure(self, mock_process, mock_config, mock_conn_func):
        mock_conn = MagicMock()
        mock_conn_func.return_value = mock_conn
        mock_process.side_effect = RuntimeError("render error")
        event = _make_sqs_event([_make_sqs_record()])

        handler(event, None)

        mock_conn.close.assert_called_once()

    @patch("sow_render_worker.lambda_handler.get_connection")
    @patch("sow_render_worker.lambda_handler.load_config")
    def test_invalid_json_body_in_record(self, mock_config, mock_conn_func):
        mock_conn = MagicMock()
        mock_conn_func.return_value = mock_conn
        record = {
            "messageId": "msg-bad",
            "body": "not valid json{{{",
        }
        event = _make_sqs_event([record])

        result = handler(event, None)

        assert "batchItemFailures" in result
        assert result["batchItemFailures"][0]["itemIdentifier"] == "msg-bad"

    @patch("sow_render_worker.lambda_handler.get_connection")
    @patch("sow_render_worker.lambda_handler.load_config")
    def test_missing_job_id_in_body(self, mock_config, mock_conn_func):
        mock_conn = MagicMock()
        mock_conn_func.return_value = mock_conn
        record = {
            "messageId": "msg-no-job",
            "body": json.dumps({"userId": 42}),
        }
        event = _make_sqs_event([record])

        result = handler(event, None)

        assert "batchItemFailures" in result
        assert result["batchItemFailures"][0]["itemIdentifier"] == "msg-no-job"


def _make_kind_record(kind, job_id="job_glue_1", user_id=42, songset_id="ss_001",
                      message_id="msg-glue"):
    body = {"jobId": job_id, "userId": user_id}
    if songset_id is not None:
        body["songsetId"] = songset_id
    if kind is not None:
        body["kind"] = kind
    return {
        "messageId": message_id,
        "body": json.dumps(body),
        "eventSource": "aws:sqs",
    }


class TestGlueRouting:
    @patch("sow_render_worker.lambda_handler.execute_render_pipeline")
    @patch("sow_render_worker.lambda_handler.execute_glue_job")
    def test_omitted_kind_still_renders(self, mock_glue, mock_pipeline):
        mock_conn = MagicMock()
        mock_context = MagicMock()

        _process_record(_make_kind_record(None), MagicMock(), mock_conn, mock_context)

        mock_pipeline.assert_called_once_with(
            "job_glue_1", 42, mock_conn, lambda_context=mock_context
        )
        mock_glue.assert_not_called()

    @patch("sow_render_worker.lambda_handler.get_glue_job")
    @patch("sow_render_worker.lambda_handler.execute_glue_job")
    def test_glue_generate_routes_to_execute_glue_job(self, mock_glue, mock_get_job):
        stored = MagicMock()
        stored.songset_id = "ss_001"
        stored.active = True
        stored.phase = "queued"
        mock_get_job.return_value = stored
        mock_conn = MagicMock()
        mock_context = MagicMock()

        _process_record(
            _make_kind_record("glue.generate"), MagicMock(), mock_conn, mock_context
        )

        mock_get_job.assert_called_once_with(mock_conn, "job_glue_1", 42)
        mock_glue.assert_called_once_with("job_glue_1", 42, mock_conn, lambda_context=mock_context)

    @patch("sow_render_worker.lambda_handler.get_glue_job")
    @patch("sow_render_worker.lambda_handler.execute_glue_job")
    def test_glue_staging_message_passes_explicit_mode(self, mock_glue, mock_get_job):
        mock_get_job.return_value.songset_id = "ss_001"
        record = _make_kind_record("glue.generate")
        body = json.loads(record["body"])
        record["body"] = json.dumps({**body, "mode": "staging"})
        conn = MagicMock()
        context = MagicMock()

        _process_record(record, MagicMock(), conn, context)

        mock_glue.assert_called_once_with(
            "job_glue_1", 42, conn, lambda_context=context, mode="staging"
        )

    @patch("sow_render_worker.lambda_handler.get_glue_job")
    @patch("sow_render_worker.lambda_handler.execute_glue_job")
    @patch("sow_render_worker.lambda_handler.execute_render_pipeline")
    def test_unknown_kind_errors_visibly(self, mock_pipeline, mock_glue, mock_get_job):
        with pytest.raises(ValueError, match="Unknown SQS message kind"):
            _process_record(
                _make_kind_record("glue.unknown"), MagicMock(), MagicMock(), MagicMock()
            )
        mock_pipeline.assert_not_called()
        mock_glue.assert_not_called()

    @patch("sow_render_worker.lambda_handler.execute_glue_job")
    def test_glue_generate_requires_songset_id(self, mock_glue):
        with pytest.raises(ValueError, match="songsetId"):
            _process_record(
                _make_kind_record("glue.generate", songset_id=None),
                MagicMock(),
                MagicMock(),
                MagicMock(),
            )
        mock_glue.assert_not_called()

    @patch("sow_render_worker.lambda_handler.finish_glue_job")
    @patch("sow_render_worker.lambda_handler.get_glue_job")
    @patch("sow_render_worker.lambda_handler.execute_glue_job")
    def test_songset_mismatch_fails_safely_without_retry(
        self, mock_glue, mock_get_job, mock_finish
    ):
        stored = MagicMock()
        stored.songset_id = "ss_other"
        stored.active = True
        stored.phase = "queued"
        mock_get_job.return_value = stored
        mock_conn = MagicMock()

        _process_record(_make_kind_record("glue.generate"), MagicMock(), mock_conn, MagicMock())

        mock_glue.assert_not_called()
        mock_finish.assert_called_once_with(
            mock_conn,
            "job_glue_1",
            42,
            phase="failed",
            error_code="GLUE_WORKER_FAILED",
            error_detail="Message does not match the stored job",
            retry_kind="none",
        )

    @patch("sow_render_worker.lambda_handler.finish_glue_job")
    @patch("sow_render_worker.lambda_handler.get_glue_job")
    @patch("sow_render_worker.lambda_handler.execute_glue_job")
    def test_user_mismatch_acknowledges_without_work(self, mock_glue, mock_get_job, mock_finish):
        mock_get_job.return_value = None

        _process_record(_make_kind_record("glue.generate"), MagicMock(), MagicMock(), MagicMock())

        mock_glue.assert_not_called()
        mock_finish.assert_not_called()

    @patch("sow_render_worker.lambda_handler.get_connection")
    @patch("sow_render_worker.lambda_handler.load_config")
    def test_unknown_kind_returns_batch_item_failure(self, mock_config, mock_conn_func):
        mock_conn_func.return_value = MagicMock()
        event = _make_sqs_event([_make_kind_record("glue.unknown", message_id="msg-unknown")])

        result = handler(event, None)

        assert result["batchItemFailures"][0]["itemIdentifier"] == "msg-unknown"
