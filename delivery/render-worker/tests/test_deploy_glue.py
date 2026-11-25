"""Deployment assertions for the dedicated Glue worker (issue #322).

These tests are source/literal checks: they never call AWS or ElevenLabs. They
fail when a required deployment literal is removed or weakened, when the
provider key could be printed, or when the runbook/README stop documenting a
recovery class.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

AWS_UP_GLUE = WORKER_ROOT / "deploy" / "aws-up-glue.sh"
OBSERVABILITY = WORKER_ROOT / "deploy" / "glue-observability.sh"
WORKER_ENV = WORKER_ROOT / ".env.example"
WORKER_README = WORKER_ROOT / "README.md"
WEBAPP_ENV = REPO_ROOT / "delivery" / "webapp" / ".env.example"
WEBAPP_PROD_ENV = REPO_ROOT / "delivery" / "webapp" / ".env.production.example"
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
RUNBOOK = REPO_ROOT / "docs" / "glue-operator-runbook.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_code(path: Path) -> str:
    """File text without comment lines, for "must not appear" assertions.

    Explanatory comments legitimately name the flag they explain, so absence
    checks have to look at executed lines only.
    """
    return "".join(
        line for line in path.read_text(encoding="utf-8").splitlines(keepends=True)
        if not line.lstrip().startswith("#")
    )


def test_scripts_exist() -> None:
    assert AWS_UP_GLUE.is_file()
    assert OBSERVABILITY.is_file()
    assert RUNBOOK.is_file()


def test_standard_glue_limits_and_disabled_new_mapping() -> None:
    from sow_render_worker import glue_jobs

    script = read_code(AWS_UP_GLUE)
    timeouts = {int(value) for value in re.findall(r"--timeout (\d+)", script)}
    assert timeouts == {180}
    assert 180_000 > 2 * glue_jobs.UPLOAD_STAGE_MS + glue_jobs.COMPOSE_DEADLINE_RESERVE_MS
    assert "visibility=1080" in script
    for flag in (
        "--memory-size 3008", "--ephemeral-storage Size=2048", "--batch-size 1",
        "--function-response-types ReportBatchItemFailures", "--no-enabled",
        "--scaling-config MaximumConcurrency=2",
    ):
        assert flag in script
    for flag in ("--capacity-provider-config", "publish-version", "ProvisionedPollerConfig"):
        assert flag not in script


def test_paid_failure_log_payload_carries_retry_kind() -> None:
    """The GluePaidFailure filter matches `"retryKind": "paid"` in the payload,
    so the worker must emit that field on terminal failures."""
    import json
    import logging

    from sow_render_worker import glue_jobs

    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = Capture()
    glue_jobs.logger.addHandler(handler)
    try:
        glue_jobs._log_event(
            "glue.job.failed",
            level="warning",
            jobId="j",
            phase="failed",
            status="finished",
            retryKind="paid",
        )
    finally:
        glue_jobs.logger.removeHandler(handler)

    payload = records[-1]
    assert '"retryKind": "paid"' in payload
    json.loads(payload)


def test_aws_up_glue_uses_strict_shell_mode() -> None:
    for path in (AWS_UP_GLUE, OBSERVABILITY):
        assert "set -euo pipefail" in read(path)


def test_deploy_scripts_never_print_the_provider_secret() -> None:
    for path in (AWS_UP_GLUE, OBSERVABILITY):
        text = read(path)
        assert "set -x" not in text
        for line in text.splitlines():
            if "SOW_ELEVENLABS_API_KEY" not in line:
                continue
            lowered = line.lower()
            assert "echo" not in lowered, f"{path.name} echoes the provider key: {line!r}"
            assert "printf" not in lowered, f"{path.name} prints the provider key: {line!r}"


def test_deploy_scripts_never_select_the_fake_provider_mode() -> None:
    for path in (AWS_UP_GLUE, OBSERVABILITY, DEPLOY_WORKFLOW):
        assert "SOW_GLUE_PROVIDER_MODE" not in read(path)


def test_observability_uses_native_alarms_and_bounded_retention() -> None:
    text = read_code(OBSERVABILITY)
    assert "put-metric-filter" not in text
    for literal in (
        "put-metric-alarm", "--treat-missing-data notBreaching", "--retention-in-days 14",
        "sow-glue-dlq-arrival", "sow-glue-queue-age", "sow-glue-lambda-errors",
        "ApproximateNumberOfMessagesVisible", "ApproximateAgeOfOldestMessage",
        "Errors AWS/Lambda",
    ):
        assert literal in text


def test_observability_alarm_dimensions_use_name_value_pairs() -> None:
    """`--dimensions` shorthand needs Name=/Value= members; `QueueName=...` is
    rejected with 'Missing required parameter in Dimensions[0]: "Name"'."""
    text = read(OBSERVABILITY)
    for literal in (
        "Name=QueueName,Value=${DLQ_NAME}",
        "Name=QueueName,Value=${QUEUE_NAME}",
        "Name=FunctionName,Value=${FUNCTION_NAME}",
    ):
        assert literal in text, f"glue-observability.sh lost dimension pair: {literal!r}"


def test_deploy_workflow_reuses_render_image_on_standard_lambda() -> None:
    text = read(DEPLOY_WORKFLOW).split("  deploy-glue-worker:", 1)[1]
    for literal in (
        "needs.deploy-render-worker.outputs.image", "aws lambda update-function-code",
        "--function-name sow-glue-worker-standard", ".Timeout == 180 and .Capacity == null",
    ):
        assert literal in text
    assert "publish-version" not in text


def test_deploy_workflow_sets_the_key_only_on_the_glue_job_block() -> None:
    text = read(DEPLOY_WORKFLOW)
    glue_start = text.index("deploy-glue-worker:")
    render_start = text.index("deploy-render-worker:")
    render_end = glue_start
    render_block = text[render_start:render_end]
    glue_block = text[glue_start:]
    assert "SOW_ELEVENLABS_API_KEY" in glue_block
    assert "SOW_ELEVENLABS_API_KEY" not in render_block


def test_env_examples_document_glue_variables_as_server_worker_only() -> None:
    for path in (WORKER_ENV, WEBAPP_ENV, WEBAPP_PROD_ENV):
        text = read(path)
        for variable in (
            "SOW_GLUE_SQS_QUEUE_URL=",
            "SOW_ELEVENLABS_API_KEY=",
        ):
            assert variable in text, f"{path} is missing {variable}"
        assert "NEXT_PUBLIC_SOW_ELEVENLABS_API_KEY" not in text
        assert "NEXT_PUBLIC_SOW_GLUE_SQS_QUEUE_URL" not in text
        assert "NEXT_PUBLIC_GLUE_CAPACITY_PROVIDER_ARN" not in text


def test_worker_readme_documents_all_glue_deployment_facts() -> None:
    text = " ".join(read(WORKER_README).split())
    required = [
        "sow-glue-jobs",
        "sow-glue-jobs-dlq",
        "1080",
        "maxReceiveCount",
        "batch size 1",
        "ReportBatchItemFailures",
        "sow-glue-worker",
        "180",
        "standard Lambda",
        "concurrency 2",
        "SOW_ELEVENLABS_API_KEY",
        "Glue function only",
        "music_v2_5",
        "Music inpainting",
        "paid",
        "GLUE_NOT_CONFIGURED",
        "before any paid reservation or spend",
    ]
    for literal in required:
        assert literal in text, f"render-worker README lost required fact: {literal!r}"


def test_runbook_covers_inspection_and_all_seven_recovery_classes() -> None:
    text = read(RUNBOOK)
    required = [
        "glue_jobs.id",
        "descriptor key",
        "attempt_id",
        "heartbeat_at",
        "provider_contacted",
        "paid reservation",
        "elevenlabs_song_id",
        "raw_response_r2_key",
        "raw_response_sha256",
        "resulting",
        "retry_kind",
        "structured logs",
        "signed URLs",
        # Seven §6.4 recovery classes.
        "Free fresh dispatch",
        "Staging-recovery only",
        "Conditional resume",
        "Never blindly requeue",
        "Reconcile a staging success",
        "Terminal jobs and stale queue records",
        "Keep terminal jobs",
        # Alert names from the observability script.
        "sow-glue-dlq-arrival",
        "sow-glue-queue-age",
    ]
    for literal in required:
        assert literal in text, f"runbook lost required fact: {literal!r}"

    # Each recovery class must say what the user sees.
    assert text.count("What the user sees") >= 7


def test_runbook_forbids_provider_retry_after_ambiguity() -> None:
    text = read(RUNBOOK)
    assert "DLQ arrival does **not** authorize a provider retry" in text
    assert "Never repeat a paid upload or compose" in text
    assert "Only the\n  user's explicit" in text or "Only the user's explicit" in text
