#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACCOUNT_ID="762288208920"
REGION="us-west-2"
ROLE_NAME="sow-render-worker-role"
FUNCTION_NAME="sow-render-worker"
QUEUE_NAME="sow-render-jobs"
DLQ_NAME="sow-render-jobs-dlq"
LOG_GROUP="/aws/lambda/${FUNCTION_NAME}"
ECR_IMAGE="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/sow-render-worker:latest"
QUEUE_URL="http://sqs.${REGION}.localhost.localstack.cloud:4566/${ACCOUNT_ID}/${QUEUE_NAME}"
DLQ_URL="http://sqs.${REGION}.localhost.localstack.cloud:4566/${ACCOUNT_ID}/${DLQ_NAME}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── Prerequisites ──────────────────────────────────────────────────────────
check_prerequisites() {
    info "Checking prerequisites..."

    if ! command -v awslocal &>/dev/null; then
        error "awslocal not found. Install with: pip install awscli-local"
        exit 1
    fi

    if ! command -v docker &>/dev/null; then
        error "docker not found. Install Docker and start it."
        exit 1
    fi

    if ! docker info &>/dev/null 2>&1; then
        error "Docker daemon is not running."
        exit 1
    fi

    if ! docker ps --format '{{.Names}}' | grep -q 'localstack'; then
        error "No LocalStack container found. Start LocalStack first, e.g.:"
        error "  docker run -d --name localstack -p 4566:4566 -p 4510-4559:4510-4559 localstack/localstack-pro"
        exit 1
    fi

    if ! awslocal sts get-caller-identity &>/dev/null; then
        error "LocalStack is not responding on port 4566."
        exit 1
    fi

    info "Prerequisites OK"
}

# ── Tear down existing resources ───────────────────────────────────────────
tear_down() {
    info "Tearing down existing resources (ignoring errors)..."

    # Delete event source mapping
    local mapping_uuid
    mapping_uuid=$(awslocal lambda list-event-source-mappings \
        --function-name "$FUNCTION_NAME" \
        --query 'EventSourceMappings[0].UUID' \
        --output text 2>/dev/null || true)
    if [[ -n "$mapping_uuid" && "$mapping_uuid" != "None" ]]; then
        info "  Deleting event source mapping: $mapping_uuid"
        awslocal lambda delete-event-source-mapping --uuid "$mapping_uuid" 2>/dev/null || true
    fi

    # Delete Lambda function
    info "  Deleting Lambda function: $FUNCTION_NAME"
    awslocal lambda delete-function --function-name "$FUNCTION_NAME" 2>/dev/null || true

    # Delete SQS queues
    info "  Deleting SQS queue: $QUEUE_NAME"
    awslocal sqs delete-queue --queue-url "$QUEUE_URL" 2>/dev/null || true
    info "  Deleting SQS DLQ: $DLQ_NAME"
    awslocal sqs delete-queue --queue-url "$DLQ_URL" 2>/dev/null || true

    # Delete CloudWatch log group
    info "  Deleting CloudWatch log group: $LOG_GROUP"
    awslocal logs delete-log-group --log-group-name "$LOG_GROUP" 2>/dev/null || true

    # Detach policies and delete IAM role
    info "  Detaching policies from role: $ROLE_NAME"
    awslocal iam detach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole 2>/dev/null || true
    awslocal iam detach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole 2>/dev/null || true
    info "  Deleting IAM role: $ROLE_NAME"
    awslocal iam delete-role --role-name "$ROLE_NAME" 2>/dev/null || true

    info "Tear down complete"
}

# ── Create IAM Role ────────────────────────────────────────────────────────
create_iam_role() {
    info "Creating IAM role: $ROLE_NAME"

    awslocal iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document "file://${SCRIPT_DIR}/trust-policy.json"

    info "Attaching managed policies..."
    awslocal iam attach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

    awslocal iam attach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole

    info "IAM role created with policies attached"
}

# ── Create SQS Queues ──────────────────────────────────────────────────────
create_sqs_queues() {
    info "Creating SQS dead-letter queue: $DLQ_NAME"
    awslocal sqs create-queue --queue-name "$DLQ_NAME"

    local dlq_arn
    dlq_arn=$(awslocal sqs get-queue-attributes \
        --queue-url "$DLQ_URL" \
        --attribute-names QueueArn \
        --query 'Attributes.QueueArn' \
        --output text)

    info "Creating SQS main queue: $QUEUE_NAME (with redrive to DLQ)"
    awslocal sqs create-queue \
        --queue-name "$QUEUE_NAME" \
        --attributes "{
            \"RedrivePolicy\": \"{\\\"deadLetterTargetArn\\\":\\\"${dlq_arn}\\\",\\\"maxReceiveCount\\\":\\\"3\\\"}\",
            \"VisibilityTimeout\": \"900\"
        }"

    local queue_arn
    queue_arn=$(awslocal sqs get-queue-attributes \
        --queue-url "$QUEUE_URL" \
        --attribute-names QueueArn \
        --query 'Attributes.QueueArn' \
        --output text)

    info "SQS queues created"
    info "  Main queue ARN: $queue_arn"
    info "  DLQ ARN:        $dlq_arn"
}

# ── Create Lambda Function ─────────────────────────────────────────────────
create_lambda() {
    local role_arn="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

    info "Creating Lambda function: $FUNCTION_NAME"
    info "  Image:  $ECR_IMAGE"
    info "  Role:   $role_arn"
    info "  Timeout: 900s  Memory: 2048MB"

    local env_file="${SCRIPT_DIR}/../.env"
    if [[ -f "$env_file" ]]; then
        info "  Loading environment from .env file"
        set -a
        # shellcheck disable=SC1090
        source "$env_file"
        set +a
    else
        warn "  No .env file found at ${env_file}"
        warn "  Using placeholder values — update Lambda env vars manually"
    fi

    local env_vars
    env_vars=$(build_env_vars)

    awslocal lambda create-function \
        --function-name "$FUNCTION_NAME" \
        --package-type Image \
        --code "ImageUri=${ECR_IMAGE}" \
        --role "$role_arn" \
        --timeout 900 \
        --memory-size 2048 \
        --environment "$env_vars"

    info "Lambda function created"
}

build_env_vars() {
    local sqs_url="${QUEUE_URL}"

    printf '{"Variables":{"SOW_DATABASE_URL":"%s","SOW_R2_BUCKET":"%s","SOW_R2_ENDPOINT_URL":"%s","SOW_R2_ACCESS_KEY_ID":"%s","SOW_R2_SECRET_ACCESS_KEY":"%s","SOW_AWS_REGION":"%s","SOW_SQS_QUEUE_URL":"%s"}}' \
        "${SOW_DATABASE_URL:-postgresql://user:password@localhost/db}" \
        "${SOW_R2_BUCKET:-your-r2-bucket}" \
        "${SOW_R2_ENDPOINT_URL:-https://your-account-id.r2.cloudflarestorage.com}" \
        "${SOW_R2_ACCESS_KEY_ID:-your-r2-access-key-id}" \
        "${SOW_R2_SECRET_ACCESS_KEY:-your-r2-secret-access-key}" \
        "${SOW_AWS_REGION:-us-west-2}" \
        "$sqs_url"
}

# ── Create Event Source Mapping ────────────────────────────────────────────
create_event_source_mapping() {
    local queue_arn
    queue_arn=$(awslocal sqs get-queue-attributes \
        --queue-url "$QUEUE_URL" \
        --attribute-names QueueArn \
        --query 'Attributes.QueueArn' \
        --output text)

    info "Creating event source mapping: $QUEUE_NAME -> $FUNCTION_NAME"
    info "  Queue ARN: $queue_arn"

    awslocal lambda create-event-source-mapping \
        --function-name "$FUNCTION_NAME" \
        --batch-size 1 \
        --event-source-arn "$queue_arn"

    info "Event source mapping created"
}

# ── Create CloudWatch Log Group ────────────────────────────────────────────
create_log_group() {
    info "Creating CloudWatch log group: $LOG_GROUP"
    awslocal logs create-log-group --log-group-name "$LOG_GROUP" 2>/dev/null || true
    info "CloudWatch log group ready"
}

# ── Verify ─────────────────────────────────────────────────────────────────
verify() {
    info "Verifying deployment..."

    echo ""
    echo "=== Lambda Function ==="
    awslocal lambda get-function-configuration \
        --function-name "$FUNCTION_NAME" \
        --query '{FunctionName:FunctionName,PackageType:PackageType,Timeout:Timeout,MemorySize:MemorySize,Role:Role}' \
        --output table

    echo ""
    echo "=== Event Source Mapping ==="
    awslocal lambda list-event-source-mappings \
        --function-name "$FUNCTION_NAME" \
        --query 'EventSourceMappings[].{UUID:UUID,State:State,EventSourceArn:EventSourceArn}' \
        --output table

    echo ""
    echo "=== SQS Queues ==="
    echo "Main queue:"
    awslocal sqs get-queue-attributes \
        --queue-url "$QUEUE_URL" \
        --attribute-names All \
        --query 'Attributes.{QueueArn:QueueArn,VisibilityTimeout:VisibilityTimeout,RedrivePolicy:RedrivePolicy}' \
        --output table
    echo "DLQ:"
    awslocal sqs get-queue-attributes \
        --queue-url "$DLQ_URL" \
        --attribute-names All \
        --query 'Attributes.QueueArn' \
        --output text

    echo ""
    echo "=== IAM Role ==="
    awslocal iam list-attached-role-policies \
        --role-name "$ROLE_NAME" \
        --output table

    echo ""
    echo "=== CloudWatch Log Group ==="
    awslocal logs describe-log-groups \
        --log-group-name-prefix "$LOG_GROUP" \
        --query 'logGroups[].{LogGroup:logGroupName,Retention:retentionInDays}' \
        --output table

    echo ""
    info "All resources created successfully!"
    echo ""
    echo "Quick test commands:"
    echo "  awslocal sqs send-message --queue-url $QUEUE_URL --message-body '{\"jobId\":\"test-1\",\"songsetId\":\"test\",\"userId\":1}'"
    echo "  awslocal logs filter-log-events --log-group-name $LOG_GROUP --limit 20"
}

# ── Main ───────────────────────────────────────────────────────────────────
main() {
    echo "========================================="
    echo " LocalStack Render Worker Setup"
    echo "========================================="
    echo ""

    check_prerequisites
    tear_down
    echo ""
    create_iam_role
    echo ""
    create_sqs_queues
    echo ""
    create_lambda
    echo ""
    create_event_source_mapping
    echo ""
    create_log_group
    echo ""
    verify
}

main "$@"
