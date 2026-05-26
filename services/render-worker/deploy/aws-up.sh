#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
AWS_PROFILE="${AWS_PROFILE:-mhuang74}"
ACCOUNT_ID="762288208920"
REGION="us-west-2"
ROLE_NAME="sow-render-worker-role"
FUNCTION_NAME="sow-render-worker"
ECR_REPO="sow-render-worker"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}"
QUEUE_NAME="sow-render-jobs"
DLQ_NAME="sow-render-jobs-dlq"
LOG_GROUP="/aws/lambda/${FUNCTION_NAME}"
QUEUE_URL="https://sqs.${REGION}.amazonaws.com/${ACCOUNT_ID}/${QUEUE_NAME}"
DLQ_URL="https://sqs.${REGION}.amazonaws.com/${ACCOUNT_ID}/${DLQ_NAME}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

aws_cmd() {
    aws --profile "$AWS_PROFILE" --region "$REGION" "$@"
}

# ── Prerequisites ──────────────────────────────────────────────────────────
check_prerequisites() {
    info "Checking prerequisites..."

    if ! command -v aws &>/dev/null; then
        error "aws CLI not found. Install with: pip install awscli"
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

    if ! aws_cmd sts get-caller-identity &>/dev/null; then
        error "AWS CLI profile '${AWS_PROFILE}' is not configured or credentials are invalid."
        error "Run: aws configure --profile ${AWS_PROFILE}"
        exit 1
    fi

    local env_file="${PROJECT_DIR}/.env"
    if [[ ! -f "$env_file" ]]; then
        warn "No .env file found at ${env_file}"
        warn "Lambda will be created with placeholder env vars — update manually"
    fi

    info "Prerequisites OK (profile: ${AWS_PROFILE}, region: ${REGION})"
}

# ── Tear down existing resources ───────────────────────────────────────────
tear_down() {
    info "Tearing down existing AWS resources (ignoring errors)..."

    # Delete event source mapping
    local mapping_uuid
    mapping_uuid=$(aws_cmd lambda list-event-source-mappings \
        --function-name "$FUNCTION_NAME" \
        --query 'EventSourceMappings[0].UUID' \
        --output text 2>/dev/null || true)
    if [[ -n "$mapping_uuid" && "$mapping_uuid" != "None" ]]; then
        info "  Deleting event source mapping: $mapping_uuid"
        aws_cmd lambda delete-event-source-mapping --uuid "$mapping_uuid" 2>/dev/null || true
    fi

    # Delete Lambda function
    info "  Deleting Lambda function: $FUNCTION_NAME"
    aws_cmd lambda delete-function --function-name "$FUNCTION_NAME" 2>/dev/null || true

    # Delete SQS queues
    info "  Deleting SQS queue: $QUEUE_NAME"
    aws_cmd sqs delete-queue --queue-url "$QUEUE_URL" 2>/dev/null || true
    info "  Deleting SQS DLQ: $DLQ_NAME"
    aws_cmd sqs delete-queue --queue-url "$DLQ_URL" 2>/dev/null || true

    # Detach policies and delete IAM role
    info "  Detaching policies from role: $ROLE_NAME"
    aws_cmd iam detach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole 2>/dev/null || true
    aws_cmd iam detach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole 2>/dev/null || true
    info "  Deleting IAM role: $ROLE_NAME"
    aws_cmd iam delete-role --role-name "$ROLE_NAME" 2>/dev/null || true

    # Note: ECR repository is NOT deleted (may contain other image tags)
    info "  ECR repository preserved (not deleted)"

    info "Tear down complete"
}

# ── Create IAM Role ────────────────────────────────────────────────────────
create_iam_role() {
    info "Creating IAM role: $ROLE_NAME"

    aws_cmd iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document "file://${SCRIPT_DIR}/trust-policy.json"

    info "Attaching managed policies..."
    aws_cmd iam attach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

    aws_cmd iam attach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole

    info "Waiting for IAM role propagation (10s)..."
    sleep 10

    info "IAM role created with policies attached"
}

# ── Create ECR Repository ─────────────────────────────────────────────────
create_ecr_repo() {
    if aws_cmd ecr describe-repositories --repository-names "$ECR_REPO" &>/dev/null; then
        info "ECR repository already exists: $ECR_REPO"
    else
        info "Creating ECR repository: $ECR_REPO"
        aws_cmd ecr create-repository --repository-name "$ECR_REPO"
        info "ECR repository created"
    fi
}

# ── Build and Push Docker Image ────────────────────────────────────────────
build_and_push_image() {
    info "Authenticating Docker with ECR..."
    aws_cmd ecr get-login-password \
        | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

    info "Building Docker image..."
    docker build -t "$ECR_REPO" "$PROJECT_DIR"

    info "Tagging image..."
    docker tag "${ECR_REPO}:latest" "${ECR_URI}:latest"

    info "Pushing image to ECR..."
    docker push "${ECR_URI}:latest"

    info "Image pushed: ${ECR_URI}:latest"
}

# ── Create SQS Queues ──────────────────────────────────────────────────────
create_sqs_queues() {
    info "Creating SQS dead-letter queue: $DLQ_NAME"
    aws_cmd sqs create-queue --queue-name "$DLQ_NAME"

    local dlq_arn
    dlq_arn=$(aws_cmd sqs get-queue-attributes \
        --queue-url "$DLQ_URL" \
        --attribute-names QueueArn \
        --query 'Attributes.QueueArn' \
        --output text)

    info "Creating SQS main queue: $QUEUE_NAME (with redrive to DLQ)"
    aws_cmd sqs create-queue \
        --queue-name "$QUEUE_NAME" \
        --attributes "{
            \"RedrivePolicy\": \"{\\\"deadLetterTargetArn\\\":\\\"${dlq_arn}\\\",\\\"maxReceiveCount\\\":\\\"3\\\"}\",
            \"VisibilityTimeout\": \"900\"
        }"

    local queue_arn
    queue_arn=$(aws_cmd sqs get-queue-attributes \
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
    info "  Image:            $ECR_URI:latest"
    info "  Role:             $role_arn"
    info "  Timeout:          900s"
    info "  Memory:           2048MB"
    info "  Ephemeral Storage: 2048MB"

    local env_file="${PROJECT_DIR}/.env"
    if [[ -f "$env_file" ]]; then
        info "  Loading environment from .env file"
        set -a
        # shellcheck disable=SC1090
        source "$env_file"
        set +a
    else
        warn "  No .env file found — using placeholder values"
    fi

    local env_vars
    env_vars=$(build_env_vars)

    aws_cmd lambda create-function \
        --function-name "$FUNCTION_NAME" \
        --package-type Image \
        --code "ImageUri=${ECR_URI}:latest" \
        --role "$role_arn" \
        --timeout 900 \
        --memory-size 3072 \
        --ephemeral-storage Size=2048 \
        --environment "$env_vars"

    info "Lambda function created"
}

build_env_vars() {
    local sqs_url="$QUEUE_URL"

    printf '{"Variables":{"SOW_DATABASE_URL":"%s","SOW_R2_BUCKET":"%s","SOW_R2_ENDPOINT_URL":"%s","SOW_R2_ACCESS_KEY_ID":"%s","SOW_R2_SECRET_ACCESS_KEY":"%s","SOW_AWS_REGION":"%s","SOW_SQS_QUEUE_URL":"%s","SOW_FRAME_CACHE_ENABLED":"%s","SOW_FADE_ALPHA_STEPS":"%s","SOW_MAX_CACHE_ENTRIES":"%s"}}' \
        "${SOW_DATABASE_URL:-postgresql://user:password@localhost/db}" \
        "${SOW_R2_BUCKET:-your-r2-bucket}" \
        "${SOW_R2_ENDPOINT_URL:-https://your-account-id.r2.cloudflarestorage.com}" \
        "${SOW_R2_ACCESS_KEY_ID:-your-r2-access-key-id}" \
        "${SOW_R2_SECRET_ACCESS_KEY:-your-r2-secret-access-key}" \
        "${SOW_AWS_REGION:-us-west-2}" \
        "$sqs_url" \
        "${SOW_FRAME_CACHE_ENABLED:-true}" \
        "${SOW_FADE_ALPHA_STEPS:-16}" \
        "${SOW_MAX_CACHE_ENTRIES:-300}"
}

# ── Wait for Lambda to become Active ───────────────────────────────────────
wait_for_lambda_active() {
    info "Waiting for Lambda function to reach Active state..."
    local max_attempts=60
    local attempt=0
    local state

    while (( attempt < max_attempts )); do
        state=$(aws_cmd lambda get-function-configuration \
            --function-name "$FUNCTION_NAME" \
            --query 'State' \
            --output text 2>/dev/null || echo "Pending")

        if [[ "$state" == "Active" ]]; then
            info "Lambda function is Active"
            return 0
        fi

        attempt=$((attempt + 1))
        info "  State: $state (attempt $attempt/$max_attempts) — waiting 10s..."
        sleep 10
    done

    error "Lambda did not become Active within $((max_attempts * 10))s"
    error "Check the Lambda console for image pull errors"
    return 1
}

# ── Create Event Source Mapping ────────────────────────────────────────────
create_event_source_mapping() {
    local queue_arn
    queue_arn=$(aws_cmd sqs get-queue-attributes \
        --queue-url "$QUEUE_URL" \
        --attribute-names QueueArn \
        --query 'Attributes.QueueArn' \
        --output text)

    info "Creating event source mapping: $QUEUE_NAME -> $FUNCTION_NAME"
    info "  Queue ARN: $queue_arn"

    aws_cmd lambda create-event-source-mapping \
        --function-name "$FUNCTION_NAME" \
        --batch-size 1 \
        --event-source-arn "$queue_arn"

    info "Event source mapping created"
}

# ── Verify ─────────────────────────────────────────────────────────────────
verify() {
    info "Verifying deployment..."

    echo ""
    echo "=== Lambda Function ==="
    aws_cmd lambda get-function-configuration \
        --function-name "$FUNCTION_NAME" \
        --query '{FunctionName:FunctionName,PackageType:PackageType,State:State,Timeout:Timeout,MemorySize:MemorySize,EphemeralStorage:EphemeralStorage,Role:Role}' \
        --output table

    echo ""
    echo "=== Event Source Mapping ==="
    aws_cmd lambda list-event-source-mappings \
        --function-name "$FUNCTION_NAME" \
        --query 'EventSourceMappings[].{UUID:UUID,State:State,EventSourceArn:EventSourceArn}' \
        --output table

    echo ""
    echo "=== SQS Queues ==="
    echo "Main queue:"
    aws_cmd sqs get-queue-attributes \
        --queue-url "$QUEUE_URL" \
        --attribute-names All \
        --query 'Attributes.{QueueArn:QueueArn,VisibilityTimeout:VisibilityTimeout,RedrivePolicy:RedrivePolicy}' \
        --output table
    echo "DLQ:"
    aws_cmd sqs get-queue-attributes \
        --queue-url "$DLQ_URL" \
        --attribute-names All \
        --query 'Attributes.QueueArn' \
        --output text

    echo ""
    echo "=== IAM Role ==="
    aws_cmd iam list-attached-role-policies \
        --role-name "$ROLE_NAME" \
        --output table

    echo ""
    echo "=== ECR Repository ==="
    aws_cmd ecr describe-repositories \
        --repository-names "$ECR_REPO" \
        --query 'repositories[].{RepositoryName:repositoryName,Uri:repositoryUri}' \
        --output table 2>/dev/null || warn "ECR repository not found"

    echo ""
    info "All resources created successfully!"
    echo ""
    echo "Quick test commands:"
    echo "  aws --profile $AWS_PROFILE sqs send-message --queue-url $QUEUE_URL --message-body '{\"jobId\":\"test-1\",\"songsetId\":\"test\",\"userId\":1}'"
    echo "  aws --profile $AWS_PROFILE logs filter-log-events --log-group-name $LOG_GROUP --limit 20"
}

# ── Main ───────────────────────────────────────────────────────────────────
main() {
    echo "========================================="
    echo " AWS Render Worker Setup"
    echo " Profile: $AWS_PROFILE"
    echo " Region:  $REGION"
    echo "========================================="
    echo ""

    check_prerequisites
    tear_down
    echo ""
    create_iam_role
    echo ""
    create_ecr_repo
    echo ""
    build_and_push_image
    echo ""
    create_sqs_queues
    echo ""
    create_lambda
    echo ""
    wait_for_lambda_active
    echo ""
    create_event_source_mapping
    echo ""
    verify
}

main "$@"
