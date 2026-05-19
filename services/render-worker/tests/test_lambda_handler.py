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
    @patch("sow_render_worker.lambda_handler.get_connection")
    @patch("sow_render_worker.lambda_handler.load_config")
    @patch("sow_render_worker.lambda_handler.execute_render_pipeline")
    def test_success_path(self, mock_pipeline, mock_config, mock_conn_func):
        mock_pipeline.return_value = None
        mock_conn = MagicMock()
        mock_conn_func.return_value = mock_conn

        record = _make_sqs_record()
        _process_record(record)

        mock_pipeline.assert_called_once_with("job_abc123", 42, mock_conn)
        mock_conn.close.assert_called_once()

    @patch("sow_render_worker.lambda_handler.get_connection")
    @patch("sow_render_worker.lambda_handler.load_config")
    @patch("sow_render_worker.lambda_handler.execute_render_pipeline")
    def test_pipeline_failure_raises(self, mock_pipeline, mock_config, mock_conn_func):
        mock_pipeline.side_effect = RuntimeError("render failed")
        mock_conn = MagicMock()
        mock_conn_func.return_value = mock_conn

        record = _make_sqs_record()
        with pytest.raises(RuntimeError, match="render failed"):
            _process_record(record)

        mock_conn.close.assert_called_once()

    @patch("sow_render_worker.lambda_handler.get_connection")
    @patch("sow_render_worker.lambda_handler.load_config")
    @patch("sow_render_worker.lambda_handler.execute_render_pipeline")
    def test_conn_closed_on_pipeline_error(self, mock_pipeline, mock_config, mock_conn_func):
        mock_pipeline.side_effect = RuntimeError("boom")
        mock_conn = MagicMock()
        mock_conn_func.return_value = mock_conn

        record = _make_sqs_record()
        with pytest.raises(RuntimeError):
            _process_record(record)

        mock_conn.close.assert_called_once()

    @patch("sow_render_worker.lambda_handler.get_connection")
    @patch("sow_render_worker.lambda_handler.load_config")
    @patch("sow_render_worker.lambda_handler.execute_render_pipeline")
    def test_conn_close_error_does_not_suppress_pipeline_error(
        self, mock_pipeline, mock_config, mock_conn_func
    ):
        mock_pipeline.side_effect = RuntimeError("pipeline error")
        mock_conn = MagicMock()
        mock_conn.close.side_effect = RuntimeError("close error")
        mock_conn_func.return_value = mock_conn

        record = _make_sqs_record()
        with pytest.raises(RuntimeError, match="pipeline error"):
            _process_record(record)

    @patch("sow_render_worker.lambda_handler.get_connection")
    @patch("sow_render_worker.lambda_handler.load_config")
    @patch("sow_render_worker.lambda_handler.execute_render_pipeline")
    def test_uses_config_database_url(self, mock_pipeline, mock_config, mock_conn_func):
        mock_pipeline.return_value = None
        mock_config_obj = MagicMock()
        mock_config_obj.DATABASE_URL = "postgresql://test:test@localhost/db"
        mock_config.return_value = mock_config_obj
        mock_conn = MagicMock()
        mock_conn_func.return_value = mock_conn

        record = _make_sqs_record()
        _process_record(record)

        mock_conn_func.assert_called_once_with("postgresql://test:test@localhost/db")


class TestHandler:
    @patch("sow_render_worker.lambda_handler._process_record")
    def test_single_record_success(self, mock_process):
        event = _make_sqs_event([_make_sqs_record()])
        result = handler(event, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "successfully" in body["message"]
        mock_process.assert_called_once()

    @patch("sow_render_worker.lambda_handler._process_record")
    def test_multiple_records_all_success(self, mock_process):
        records = [
            _make_sqs_record(job_id="job_1", user_id=1, message_id="msg-1"),
            _make_sqs_record(job_id="job_2", user_id=2, message_id="msg-2"),
            _make_sqs_record(job_id="job_3", user_id=3, message_id="msg-3"),
        ]
        event = _make_sqs_event(records)
        result = handler(event, None)

        assert result["statusCode"] == 200
        assert mock_process.call_count == 3

    @patch("sow_render_worker.lambda_handler._process_record")
    def test_single_record_failure_returns_batch_item_failures(self, mock_process):
        mock_process.side_effect = RuntimeError("render error")
        event = _make_sqs_event([_make_sqs_record(message_id="msg-fail")])

        result = handler(event, None)

        assert "batchItemFailures" in result
        assert result["batchItemFailures"][0]["itemIdentifier"] == "msg-fail"

    @patch("sow_render_worker.lambda_handler._process_record")
    def test_multiple_records_partial_failure(self, mock_process):
        call_count = [0]

        def side_effect(record):
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

    @patch("sow_render_worker.lambda_handler._process_record")
    def test_empty_records_returns_200(self, mock_process):
        event = _make_sqs_event([])
        result = handler(event, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "No records" in body["message"]
        mock_process.assert_not_called()

    @patch("sow_render_worker.lambda_handler._process_record")
    def test_no_records_key_returns_200(self, mock_process):
        event = {}
        result = handler(event, None)

        assert result["statusCode"] == 200
        mock_process.assert_not_called()

    @patch("sow_render_worker.lambda_handler._process_record")
    def test_all_records_processed_even_on_failure(self, mock_process):
        mock_process.side_effect = RuntimeError("always fails")
        records = [
            _make_sqs_record(job_id="job_1", message_id="msg-1"),
            _make_sqs_record(job_id="job_2", message_id="msg-2"),
        ]
        event = _make_sqs_event(records)

        result = handler(event, None)

        assert mock_process.call_count == 2
        assert len(result["batchItemFailures"]) == 2

    @patch("sow_render_worker.lambda_handler.load_config")
    @patch("sow_render_worker.lambda_handler.get_connection")
    def test_invalid_json_body_in_record(self, mock_conn, mock_config):
        record = {
            "messageId": "msg-bad",
            "body": "not valid json{{{",
        }
        event = _make_sqs_event([record])

        result = handler(event, None)

        assert "batchItemFailures" in result
        assert result["batchItemFailures"][0]["itemIdentifier"] == "msg-bad"

    @patch("sow_render_worker.lambda_handler.load_config")
    @patch("sow_render_worker.lambda_handler.get_connection")
    def test_missing_job_id_in_body(self, mock_conn, mock_config):
        record = {
            "messageId": "msg-no-job",
            "body": json.dumps({"userId": 42}),
        }
        event = _make_sqs_event([record])

        result = handler(event, None)

        assert "batchItemFailures" in result
        assert result["batchItemFailures"][0]["itemIdentifier"] == "msg-no-job"
