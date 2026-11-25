#!/usr/bin/env bash
# Configure alarms for the standard Glue worker.
set -euo pipefail

AWS_PROFILE="${AWS_PROFILE:-mhuang74}"
AWS_REGION="${AWS_REGION:-us-west-2}"
FUNCTION_NAME="sow-glue-worker-standard"
LOG_GROUP="/aws/lambda/${FUNCTION_NAME}"
QUEUE_NAME="sow-glue-jobs"
DLQ_NAME="sow-glue-jobs-dlq"

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

check_prerequisites() {
    if ! command -v aws &>/dev/null; then
        error "aws CLI not found. Install with: pip install awscli"
        exit 1
    fi
    if ! aws_cmd sts get-caller-identity &>/dev/null; then
        error "AWS CLI profile '${AWS_PROFILE}' is not configured or credentials are invalid."
        exit 1
    fi
}

ensure_log_group() {
    if aws_cmd logs describe-log-groups \
        --log-group-name-prefix "$LOG_GROUP" \
        --query "logGroups[?logGroupName=='${LOG_GROUP}'].logGroupName" \
        --output text 2>/dev/null | grep -q .; then
        info "Log group exists: $LOG_GROUP"
    else
        info "Creating log group: $LOG_GROUP"
        aws_cmd logs create-log-group --log-group-name "$LOG_GROUP" >/dev/null
    fi
}

if [[ -n "${GLUE_ALARM_SNS_TOPIC_ARN:-}" ]]; then
    ALARM_ACTIONS=(--alarm-actions "$GLUE_ALARM_SNS_TOPIC_ARN")
else
    ALARM_ACTIONS=()
    warn "GLUE_ALARM_SNS_TOPIC_ARN is not set; alarms will have no notification actions."
fi

put_alarm() {
    local alarm_name="$1"
    local metric_name="$2"
    local namespace="$3"
    local statistic="$4"
    local period="$5"
    local evaluation_periods="$6"
    local threshold="$7"
    local comparison="$8"
    local dimensions="$9"
    local description="${10}"

    info "Alarm: $alarm_name"
    # `--dimensions` shorthand takes struct pairs (`Name=QueueName,Value=...`),
    # not MetricDimension-style `QueueName=...`; the latter is rejected with
    # 'Missing required parameter in Dimensions[0]: "Name"'.
    local dimension_args=()
    if [[ -n "$dimensions" ]]; then
        dimension_args=(--dimensions "$dimensions")
    fi
    aws_cmd cloudwatch put-metric-alarm \
        --alarm-name "$alarm_name" \
        --alarm-description "$description" \
        --namespace "$namespace" \
        --metric-name "$metric_name" \
        --statistic "$statistic" \
        --period "$period" \
        --evaluation-periods "$evaluation_periods" \
        --threshold "$threshold" \
        --comparison-operator "$comparison" \
        "${dimension_args[@]}" \
        --treat-missing-data notBreaching \
        "${ALARM_ACTIONS[@]}" >/dev/null
}

check_prerequisites
ensure_log_group
aws_cmd logs put-retention-policy --log-group-name "$LOG_GROUP" --retention-in-days 14

put_alarm sow-glue-dlq-arrival ApproximateNumberOfMessagesVisible AWS/SQS Maximum 60 1 0 \
    GreaterThanThreshold "Name=QueueName,Value=${DLQ_NAME}" "Inspect dead-lettered Glue jobs."
put_alarm sow-glue-queue-age ApproximateAgeOfOldestMessage AWS/SQS Maximum 60 2 180 \
    GreaterThanThreshold "Name=QueueName,Value=${QUEUE_NAME}" "Check Glue queue backlog."
put_alarm sow-glue-lambda-errors Errors AWS/Lambda Sum 60 1 0 \
    GreaterThanThreshold "Name=FunctionName,Value=${FUNCTION_NAME}" "Inspect Glue Lambda errors."


if [[ "${GLUE_RUNPOD_ALERTS:-0}" == 1 ]]; then
    aws_cmd logs put-metric-filter --log-group-name "$LOG_GROUP" \
        --filter-name sow-glue-runpod-rejected \
        --filter-pattern '%glue\.runpod\.rejected%' \
        --metric-transformations 'metricName=RunpodRejected,metricNamespace=SOW/Glue,metricValue=1'
    put_alarm sow-glue-runpod-rejected RunpodRejected SOW/Glue Sum 60 1 0 \
        GreaterThanThreshold "" "Check Runpod credentials and the request contract."
fi
