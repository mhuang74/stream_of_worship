#!/usr/bin/env bash
# Bootstrap standard Lambda. New event source mappings start disabled.
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

FUNCTION_NAME="sow-glue-worker-standard"
QUEUE_NAME="${GLUE_QUEUE_NAME:-sow-glue-jobs}"
DLQ_NAME="${QUEUE_NAME}-dlq"
LOG_GROUP="/aws/lambda/${FUNCTION_NAME}"
QUEUE_URL="https://sqs.${AWS_REGION}.amazonaws.com/${ACCOUNT_ID}/${QUEUE_NAME}"
DLQ_URL="https://sqs.${AWS_REGION}.amazonaws.com/${ACCOUNT_ID}/${DLQ_NAME}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

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

    # Keep legacy queue visibility during preflight. Reduce it at cutover.
    local visibility=1080
    if [[ "$QUEUE_NAME" == sow-glue-jobs ]]; then
        visibility=$(aws_cmd sqs get-queue-attributes --queue-url "$QUEUE_URL" \
            --attribute-names VisibilityTimeout --query Attributes.VisibilityTimeout --output text)
        visibility=$((visibility > 1080 ? visibility : 1080))
    fi
    local attributes
    attributes="{\"VisibilityTimeout\":\"${visibility}\",\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"${dlq_arn}\\\",\\\"maxReceiveCount\\\":\\\"3\\\"}\"}"
    aws_cmd sqs set-queue-attributes \
        --queue-url "$QUEUE_URL" \
        --attributes "$attributes"

    for url in "$QUEUE_URL" "$DLQ_URL"; do
        aws_cmd sqs tag-queue --queue-url "$url" --tags project=Glue
    done

    info "SQS ready: visibility ${visibility}s, maxReceiveCount 3"
}

# ── Standard Lambda function ────────────────────────────────────
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
            --image-uri "${GLUE_IMAGE_URI:-${ECR_URI}:latest}" >/dev/null
        wait_for_lambda_updated

        aws_cmd lambda update-function-configuration \
            --function-name "$FUNCTION_NAME" \
            --timeout 180 \
            --logging-config LogFormat=JSON,ApplicationLogLevel=INFO,SystemLogLevel=INFO \
            --memory-size 3008 \
            --ephemeral-storage Size=2048 \
            --environment "$env_vars" >/dev/null
        wait_for_lambda_updated
    else
        info "Creating Lambda function: $FUNCTION_NAME"
        aws_cmd lambda create-function \
            --function-name "$FUNCTION_NAME" \
            --package-type Image \
            --code "ImageUri=${GLUE_IMAGE_URI:-${ECR_URI}:latest}" \
            --role "$role_arn" \
            --timeout 180 \
            --logging-config LogFormat=JSON,ApplicationLogLevel=INFO,SystemLogLevel=INFO \
            --memory-size 3008 \
            --ephemeral-storage Size=2048 \
            --environment "$env_vars" >/dev/null

        aws_cmd lambda wait function-active --function-name "$FUNCTION_NAME"
    fi
    aws_cmd lambda tag-resource --resource "arn:aws:lambda:${AWS_REGION}:${ACCOUNT_ID}:function:${FUNCTION_NAME}" --tags project=Glue
}

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
        --output text)

    if [[ -n "$mapping_uuid" && "$mapping_uuid" != "None" ]]; then
        info "Updating event source mapping: $mapping_uuid"
        aws_cmd lambda update-event-source-mapping \
            --uuid "$mapping_uuid" \
            --batch-size 1 \
            --scaling-config MaximumConcurrency=2 \
            --function-response-types ReportBatchItemFailures >/dev/null
    else
        info "Creating event source mapping: $QUEUE_NAME -> $FUNCTION_NAME"
        aws_cmd lambda create-event-source-mapping \
            --function-name "$FUNCTION_NAME" \
            --event-source-arn "$queue_arn" \
            --no-enabled \
            --batch-size 1 \
            --scaling-config MaximumConcurrency=2 \
            --function-response-types ReportBatchItemFailures >/dev/null
    fi

}

check_prerequisites
create_sqs_queues
create_or_update_lambda
create_event_source_mapping
info "$FUNCTION_NAME ready. New queue mappings are disabled."
