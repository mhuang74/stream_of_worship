#!/usr/bin/env bash
# Create or update the dedicated Glue (AI transition) worker:
#
#   * SQS dead-letter queue  sow-glue-jobs-dlq
#   * SQS queue              sow-glue-jobs (visibility 2100 s, redrive 3)
#   * Lambda function        sow-glue-worker on AWS Lambda Managed Instances
#                            running the shared sow-render-worker image
#   * SQS event source map   batch size 1, ReportBatchItemFailures
#   * scaling config         min 0 / max 2 execution environments on $LATEST.PUBLISHED
#   * published Lambda version
#
# Managed Instances are required: standard Lambda caps asynchronous invocations
# at 1800 s, while a Glue job needs 1900 s. The capacity provider ARN is an
# operator-supplied input and must already exist in the account.
#
# This script never prints secrets: provider keys and the merged environment
# JSON are passed directly to the AWS CLI and never echoed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

AWS_PROFILE="${AWS_PROFILE:-mhuang74}"
AWS_REGION="${AWS_REGION:-us-west-2}"
ACCOUNT_ID="${AWS_ACCOUNT_ID:-762288208920}"

# Shared image + execution role with the render worker.
ECR_REPO="${GLUE_ECR_REPO:-sow-render-worker}"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"
ROLE_NAME="${GLUE_ROLE_NAME:-sow-render-worker-role}"

FUNCTION_NAME="sow-glue-worker"
QUEUE_NAME="sow-glue-jobs"
DLQ_NAME="sow-glue-jobs-dlq"
LOG_GROUP="/aws/lambda/${FUNCTION_NAME}"
QUEUE_URL="https://sqs.${AWS_REGION}.amazonaws.com/${ACCOUNT_ID}/${QUEUE_NAME}"
DLQ_URL="https://sqs.${AWS_REGION}.amazonaws.com/${ACCOUNT_ID}/${DLQ_NAME}"

# Glue limits (spec §2.4 / §2.5): visibility > function timeout; provider
# concurrency 2 independent of render render throughput. These values are
# intentionally inline in the CLI calls below so deployment tests pin the
# exact flags the platform requires: 2100 s visibility, maxReceiveCount 3,
# 1900 s timeout, 3072 MB memory, 2048 MB ephemeral storage, 2 environments.

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── Fail fast: Managed Instances capacity provider ─────────────────────────
# Must run before any `aws` call so an unset value never creates a queue or a
# standard-Lambda function that would silently time out at 1800 s.
if [[ -z "${GLUE_CAPACITY_PROVIDER_ARN:-}" ]]; then
    error "GLUE_CAPACITY_PROVIDER_ARN is required: sow-glue-worker must run on AWS Lambda Managed Instances (standard Lambda caps asynchronous invocations at 1800 s, below the 1900 s Glue timeout)."
    error "Set GLUE_CAPACITY_PROVIDER_ARN to the ARN of the Lambda Managed Instances capacity provider for this account/region."
    exit 1
fi

aws_cmd() {
    aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"
}

# ── Prerequisites ──────────────────────────────────────────────────────────
check_prerequisites() {
    info "Checking prerequisites..."

    if ! command -v aws &>/dev/null; then
        error "aws CLI not found. Install with: pip install awscli"
        exit 1
    fi
    if ! command -v python3 &>/dev/null; then
        error "python3 not found; it is required to build the Lambda environment JSON."
        exit 1
    fi

    if ! aws_cmd sts get-caller-identity &>/dev/null; then
        error "AWS CLI profile '${AWS_PROFILE}' is not configured or credentials are invalid."
        error "Run: aws configure --profile ${AWS_PROFILE}"
        exit 1
    fi

    info "Prerequisites OK (profile: ${AWS_PROFILE}, region: ${AWS_REGION})"
}

# ── Lambda environment ─────────────────────────────────────────────────────
# Writes JSON to stdout for command substitution; the caller must never echo
# the result. `SOW_ELEVENLABS_API_KEY` is included only when configured and
# only on this function.
build_environment_json() {
    local queue_url="$1"
    PYTHONPATH="$PROJECT_DIR" python3 - "$queue_url" <<'PY'
import json
import os
import sys

required = (
    "SOW_DATABASE_URL",
    "SOW_R2_BUCKET",
    "SOW_R2_ENDPOINT_URL",
    "SOW_R2_ACCESS_KEY_ID",
    "SOW_R2_SECRET_ACCESS_KEY",
    "SOW_AWS_REGION",
)

missing = [name for name in required if not os.environ.get(name)]
if missing:
    raise SystemExit(
        "Missing required environment variables for the Glue Lambda: "
        + ", ".join(missing)
    )

variables = {name: os.environ[name] for name in required}
variables["SOW_GLUE_SQS_QUEUE_URL"] = sys.argv[1]
variables["SOW_FRAME_CACHE_ENABLED"] = os.environ.get("SOW_FRAME_CACHE_ENABLED", "true")
variables["SOW_FADE_ALPHA_STEPS"] = os.environ.get("SOW_FADE_ALPHA_STEPS", "16")

# Provider key: worker-only. Never NEXT_PUBLIC_*, never logged, never returned.
key = os.environ.get("SOW_ELEVENLABS_API_KEY")
if key:
    variables["SOW_ELEVENLABS_API_KEY"] = key

print(json.dumps({"Variables": variables}, separators=(",", ":")))
PY
}

# ── SQS queue + DLQ ────────────────────────────────────────────────────────
create_sqs_queues() {
    info "Ensuring SQS dead-letter queue: $DLQ_NAME"
    if aws_cmd sqs get-queue-url --queue-name "$DLQ_NAME" &>/dev/null; then
        info "  DLQ already exists"
    else
        aws_cmd sqs create-queue --queue-name "$DLQ_NAME" >/dev/null
    fi

    local dlq_arn
    dlq_arn=$(aws_cmd sqs get-queue-attributes \
        --queue-url "$DLQ_URL" \
        --attribute-names QueueArn \
        --query 'Attributes.QueueArn' \
        --output text)

    info "Ensuring SQS queue: $QUEUE_NAME"
    if aws_cmd sqs get-queue-url --queue-name "$QUEUE_NAME" &>/dev/null; then
        info "  Queue already exists; updating attributes"
    else
        aws_cmd sqs create-queue --queue-name "$QUEUE_NAME" >/dev/null
    fi

    # VisibilityTimeout must exceed the 1900 s function timeout so a slow job
    # is never redelivered while it is still running.
    local attributes
    attributes="{\"VisibilityTimeout\":\"2100\",\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"${dlq_arn}\\\",\\\"maxReceiveCount\\\":\\\"3\\\"}\"}"
    aws_cmd sqs set-queue-attributes \
        --queue-url "$QUEUE_URL" \
        --attributes "$attributes"

    info "SQS ready: visibility 2100s, maxReceiveCount 3"
}

# ── Lambda function (Managed Instances) ────────────────────────────────────
lambda_exists() {
    aws_cmd lambda get-function-configuration --function-name "$FUNCTION_NAME" &>/dev/null
}

wait_for_lambda_updated() {
    info "Waiting for Lambda to finish updating..."
    aws_cmd lambda wait function-updated --function-name "$FUNCTION_NAME" || {
        error "Lambda function $FUNCTION_NAME did not finish updating."
        exit 1
    }
}

create_or_update_lambda() {
    local role_arn="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
    local env_vars
    env_vars=$(build_environment_json "$QUEUE_URL")

    if lambda_exists; then
        info "Updating existing Lambda function: $FUNCTION_NAME"
        aws_cmd lambda update-function-code \
            --function-name "$FUNCTION_NAME" \
            --image-uri "${ECR_URI}:latest" >/dev/null
        wait_for_lambda_updated

        aws_cmd lambda update-function-configuration \
            --function-name "$FUNCTION_NAME" \
            --timeout 1900 \
            --memory-size 3072 \
            --ephemeral-storage Size=2048 \
            --capacity-provider-config "LambdaManagedInstancesCapacityProviderConfig={CapacityProviderArn=$GLUE_CAPACITY_PROVIDER_ARN}" \
            --environment "$env_vars" >/dev/null
        wait_for_lambda_updated
    else
        info "Creating Lambda function: $FUNCTION_NAME (Managed Instances)"
        aws_cmd lambda create-function \
            --function-name "$FUNCTION_NAME" \
            --package-type Image \
            --code "ImageUri=${ECR_URI}:latest" \
            --role "$role_arn" \
            --timeout 1900 \
            --memory-size 3072 \
            --ephemeral-storage Size=2048 \
            --capacity-provider-config "LambdaManagedInstancesCapacityProviderConfig={CapacityProviderArn=$GLUE_CAPACITY_PROVIDER_ARN}" \
            --environment "$env_vars" >/dev/null

        info "Waiting for the new Lambda function to become active..."
        aws_cmd lambda wait function-active --function-name "$FUNCTION_NAME" || {
            error "Lambda function $FUNCTION_NAME did not become active (check the image pull)."
            exit 1
        }
    fi
}

# ── Execution-environment scaling ──────────────────────────────────────────
# Scaling limits are not a CreateFunction/UpdateFunctionConfiguration request
# field: AWS applies them per qualified ARN through PutFunctionScalingConfig.
# `$LATEST.PUBLISHED` makes every future published version inherit the cap; the
# minimum/maximum pair must be sent together (an omitted value resets to the
# default minimum of 3), so the minimum is pinned to 0 (scale to zero between
# jobs) with the maximum at the spec's 2 provider concurrency.
apply_scaling_config() {
    info "Setting execution environments: min 0, max 2"
    aws_cmd lambda put-function-scaling-config \
        --function-name "$FUNCTION_NAME" \
        --qualifier '$LATEST.PUBLISHED' \
        --function-scaling-config 'MinExecutionEnvironments=0,MaxExecutionEnvironments=2' >/dev/null
}

# ── Event source mapping ───────────────────────────────────────────────────
create_event_source_mapping() {
    local queue_arn
    queue_arn=$(aws_cmd sqs get-queue-attributes \
        --queue-url "$QUEUE_URL" \
        --attribute-names QueueArn \
        --query 'Attributes.QueueArn' \
        --output text)

    local mapping_uuid
    mapping_uuid=$(aws_cmd lambda list-event-source-mappings \
        --function-name "$FUNCTION_NAME" \
        --query "EventSourceMappings[?EventSourceArn=='${queue_arn}'].UUID | [0]" \
        --output text 2>/dev/null || true)

    if [[ -n "$mapping_uuid" && "$mapping_uuid" != "None" ]]; then
        info "Updating event source mapping: $mapping_uuid"
        aws_cmd lambda update-event-source-mapping \
            --uuid "$mapping_uuid" \
            --batch-size 1 \
            --function-response-types ReportBatchItemFailures >/dev/null
    else
        info "Creating event source mapping: $QUEUE_NAME -> $FUNCTION_NAME"
        aws_cmd lambda create-event-source-mapping \
            --function-name "$FUNCTION_NAME" \
            --event-source-arn "$queue_arn" \
            --batch-size 1 \
            --function-response-types ReportBatchItemFailures >/dev/null
    fi

    info "Event source mapping ready: batch size 1, ReportBatchItemFailures"
}

# ── Publish version ─────────────────────────────────────────────────────────
publish_version() {
    info "Publishing Lambda version (required by Managed Instances)"
    local version
    version=$(aws_cmd lambda publish-version \
        --function-name "$FUNCTION_NAME" \
        --query 'Version' \
        --output text)
    info "Published version: $version"
}

# ── Main ───────────────────────────────────────────────────────────────────
main() {
    echo "========================================="
    echo " AWS Glue Worker Setup (Managed Instances)"
    echo " Profile: $AWS_PROFILE"
    echo " Region:  $AWS_REGION"
    echo " Function: $FUNCTION_NAME"
    echo "========================================="
    echo ""

    check_prerequisites
    echo ""
    create_sqs_queues
    echo ""
    create_or_update_lambda
    echo ""
    apply_scaling_config
    echo ""
    create_event_source_mapping
    echo ""
    publish_version
    echo ""
    info "Glue worker ready."
    echo "Queue URL: $QUEUE_URL"
    echo "Function:  $FUNCTION_NAME"
    echo "Log group: $LOG_GROUP"
}

main "$@"
