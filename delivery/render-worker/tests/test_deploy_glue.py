"""Deployment assertions for the dedicated Glue worker (issue #322).

These tests are source/literal checks: they never call AWS or ElevenLabs. They
fail when a required deployment literal is removed or weakened, when the
provider key could be printed, or when the runbook/README stop documenting a
recovery class.
"""

from __future__ import annotations

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


def test_scripts_exist() -> None:
    assert AWS_UP_GLUE.is_file()
    assert OBSERVABILITY.is_file()
    assert RUNBOOK.is_file()


def test_aws_up_glue_pins_frozen_literals() -> None:
    text = read(AWS_UP_GLUE)
    required = [
        "sow-glue-jobs-dlq",
        "sow-glue-jobs",
        "sow-glue-worker",
        "VisibilityTimeout",
        "2100",
        "maxReceiveCount",
        "3",
        "--timeout 1900",
        "--memory-size 3072",
        "--ephemeral-storage Size=2048",
        "--batch-size 1",
        "--scaling-config MaximumConcurrency=2",
        "--function-response-types ReportBatchItemFailures",
        "--capacity-provider-config",
        "LambdaManagedInstancesCapacityProviderConfig={CapacityProviderArn=$GLUE_CAPACITY_PROVIDER_ARN}",
        "put-function-scaling-config",
        "--qualifier '$LATEST.PUBLISHED'",
        "--function-scaling-config 'MinExecutionEnvironments=0,MaxExecutionEnvironments=2'",
        "publish-version",
        "GLUE_CAPACITY_PROVIDER_ARN is required",
    ]
    for literal in required:
        assert literal in text, f"aws-up-glue.sh lost required literal: {literal!r}"


def test_aws_up_glue_never_passes_scaling_to_create_or_update() -> None:
    """`FunctionScalingConfig` is not a CreateFunction/UpdateFunctionConfiguration
    request field; passing it aborts the script under `set -euo pipefail`."""
    text = read(AWS_UP_GLUE)
    for command in ("create-function", "update-function-configuration"):
        start = text.index(f"lambda {command}")
        block = text[start:].split(">/dev/null", 1)[0]
        assert "--function-scaling-config" not in block, (
            f"aws-up-glue.sh passes --function-scaling-config to {command}, which the AWS API rejects"
        )


def test_aws_up_glue_sets_scaling_only_after_publishing_a_version() -> None:
    """`$LATEST.PUBLISHED` does not resolve before the first published version,
    so a fresh-account run would abort under `set -euo pipefail`."""
    text = read(AWS_UP_GLUE)
    main_block = text[text.index("main() {"):]
    order = [
        main_block.index("create_or_update_lambda"),
        main_block.index("create_event_source_mapping"),
        main_block.index("publish_version"),
        main_block.index("apply_scaling_config"),
    ]
    assert order == sorted(order), "aws-up-glue.sh main() no longer publishes before scaling"


def test_aws_up_glue_fails_fast_before_any_aws_call() -> None:
    text = read(AWS_UP_GLUE)
    guard = text.index("GLUE_CAPACITY_PROVIDER_ARN is required")
    assert guard < text.index("aws_cmd sts get-caller-identity")
    assert guard < text.index("sqs get-queue-url")
    assert guard < text.index("lambda create-function")

    guard_block = text[guard : guard + 600]
    assert "exit 1" in guard_block


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


def test_observability_creates_required_filters_and_alarms() -> None:
    text = read(OBSERVABILITY)
    required = [
        "put-metric-filter",
        "put-metric-alarm",
        "--treat-missing-data notBreaching",
        "sow-glue-dlq-arrival",
        "sow-glue-provider-ambiguous",
        "sow-glue-queue-age",
        "sow-glue-heartbeat-missing",
        "sow-glue-staging-recovery-spike",
        "glue.provider.ambiguous",
        "glue.heartbeat.failed",
        "glue.staging.recovered",
        "ApproximateNumberOfMessagesVisible",
        "ApproximateAgeOfOldestMessage",
        "sow-glue-jobs-dlq",
        "sow-glue-jobs",
    ]
    for literal in required:
        assert literal in text, f"glue-observability.sh lost required literal: {literal!r}"


def test_deploy_workflow_reuses_the_render_image_and_publishes_a_version() -> None:
    text = read(DEPLOY_WORKFLOW)
    required = [
        "deploy-glue-worker",
        "needs.deploy-render-worker.outputs.image",
        "aws lambda update-function-code",
        "--function-name sow-glue-worker",
        "aws lambda publish-version",
        "SOW_ELEVENLABS_API_KEY",
        "CapacityProviderConfig.LambdaManagedInstancesCapacityProviderConfig.CapacityProviderArn",
        "Timeout",
        "1900",
        "exit 1",
    ]
    for literal in required:
        assert literal in text, f"deploy.yml lost required literal: {literal!r}"


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
            "GLUE_CAPACITY_PROVIDER_ARN=",
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
        "2100",
        "maxReceiveCount",
        "batch size 1",
        "ReportBatchItemFailures",
        "sow-glue-worker",
        "1900",
        "Managed Instances",
        "concurrency 2",
        "SOW_ELEVENLABS_API_KEY",
        "Glue function only",
        "music_v2_5",
        "Music inpainting",
        "paid",
        "GLUE_NOT_CONFIGURED",
        "before any paid reservation or spend",
        "publish-version",
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
        "sow-glue-provider-ambiguous",
        "sow-glue-queue-age",
        "sow-glue-heartbeat-missing",
        "sow-glue-staging-recovery-spike",
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
