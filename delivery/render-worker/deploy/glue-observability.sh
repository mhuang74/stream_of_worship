#!/usr/bin/env bash
# Create or update CloudWatch metric filters and alarms for the Glue worker.
#
# Alarms (idempotent upsert — put-metric-alarm replaces an existing alarm):
#
#   sow-glue-dlq-arrival             any message on sow-glue-jobs-dlq
#   sow-glue-provider-ambiguous      glue.provider.ambiguous log event
#   sow-glue-queue-age               oldest queued message older than 3300 s
#   sow-glue-heartbeat-missing       Lambda Duration reaches the 3300 s budget
#   sow-glue-staging-recovery-spike  free staging recoveries per 15 min
#
# Metric filters (counters required by spec §4.2):
#
#   sow-glue-paid-attempt          glue.provider.contact.started (reservation consumed)
#   sow-glue-job-claimed           glue.job.claimed
#   sow-glue-job-failed            glue.job.failed (all codes/phases)
#   sow-glue-paid-failure          glue.job.failed with retryKind = paid
#   sow-glue-redelivery            glue.job.redelivered
#   sow-glue-cache-hit             any event with cacheHit = true
#   sow-glue-cached-copyright      cached GLUE_COPYRIGHT_REJECTED
#   sow-glue-response-persisted    glue.response.persisted
#   sow-glue-staging-recovery      glue.staging.recovered
#   sow-glue-staging-unavailable   glue.staging.unavailable
#   sow-glue-heartbeat-failure     glue.heartbeat.failed
#   sow-glue-provider-ambiguous    glue.provider.ambiguous
#
# Set GLUE_ALARM_SNS_TOPIC_ARN to route alarm state changes to SNS. Without it
# the alarms are created without actions (visible in CloudWatch, no paging).
#
# This script never prints secrets.
set -euo pipefail

AWS_PROFILE="${AWS_PROFILE:-mhuang74}"
AWS_REGION="${AWS_REGION:-us-west-2}"
FUNCTION_NAME="sow-glue-worker"
LOG_GROUP="/aws/lambda/${FUNCTION_NAME}"
QUEUE_NAME="sow-glue-jobs"
DLQ_NAME="sow-glue-jobs-dlq"

# Operation budget: the Glue function timeout. A queued/in-flight job older
# than this without a heartbeat is stuck.
OPERATION_BUDGET_SECONDS=3300
STAGING_RECOVERY_SPIKE_THRESHOLD=5

METRIC_NAMESPACE="SOW/Glue"

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

# put_metric_filter <filter-name> <filter-pattern> <metric-name>
put_metric_filter() {
    local filter_name="$1"
    local filter_pattern="$2"
    local metric_name="$3"

    info "Metric filter: $filter_name"
    aws_cmd logs put-metric-filter \
        --log-group-name "$LOG_GROUP" \
        --filter-name "$filter_name" \
        --filter-pattern "$filter_pattern" \
        --metric-transformations \
            "metricName=${metric_name},metricNamespace=${METRIC_NAMESPACE},metricValue=1,defaultValue=0"
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

main() {
    echo "========================================="
    echo " AWS Glue Worker Observability"
    echo " Profile: $AWS_PROFILE"
    echo " Region:  $AWS_REGION"
    echo "========================================="
    echo ""

    check_prerequisites
    ensure_log_group

    # --- Metric filters (structured Glue log events) ----------------------
    # Stable event names from spec §4.2. Never include titles, lyrics, URLs,
    # signed URLs, keys, or provider bodies in these payloads.
    #
    # Lambda's JSON log format wraps every record as
    # {"timestamp":…,"level":…,"message":"<payload json string>"}, so the
    # payload fields are NOT top-level: `{ $.event = … }` matched nothing and
    # every Glue metric stayed at zero. Match the escaped payload text through
    # `$.message` instead; the event tokens are unique per payload.
    put_metric_filter \
        "sow-glue-provider-ambiguous" \
        '{ $.message = "*glue.provider.ambiguous*" }' \
        "GlueProviderAmbiguous"

    put_metric_filter \
        "sow-glue-heartbeat-failure" \
        '{ $.message = "*glue.heartbeat.failed*" }' \
        "GlueHeartbeatFailure"

    put_metric_filter \
        "sow-glue-staging-recovery" \
        '{ $.message = "*glue.staging.recovered*" }' \
        "GlueStagingRecovery"

    put_metric_filter \
        "sow-glue-paid-failure" \
        '{ $.message = "*glue.job.failed*" && $.message = "*\"retryKind\": \"paid\"*" }' \
        "GluePaidFailure"

    # --- Counters required by spec §4.2 (no dynamic dimensions in CloudWatch
    # Logs metric filters, so each counter gets its own filter/metric) ------
    put_metric_filter \
        "sow-glue-paid-attempt" \
        '{ $.message = "*glue.provider.contact.started*" }' \
        "GluePaidAttempt"

    put_metric_filter \
        "sow-glue-job-claimed" \
        '{ $.message = "*glue.job.claimed*" }' \
        "GlueJobClaimed"

    put_metric_filter \
        "sow-glue-job-failed" \
        '{ $.message = "*glue.job.failed*" }' \
        "GlueJobFailed"

    put_metric_filter \
        "sow-glue-redelivery" \
        '{ $.message = "*glue.job.redelivered*" }' \
        "GlueRedelivery"

    put_metric_filter \
        "sow-glue-cache-hit" \
        '{ $.message = "*\"cacheHit\": true*" }' \
        "GlueCacheHit"

    put_metric_filter \
        "sow-glue-cached-copyright" \
        '{ $.message = "*glue.provider.failed*" && $.message = "*GLUE_COPYRIGHT_REJECTED*" }' \
        "GlueCachedCopyrightRejection"

    put_metric_filter \
        "sow-glue-response-persisted" \
        '{ $.message = "*glue.response.persisted*" }' \
        "GlueResponsePersisted"

    put_metric_filter \
        "sow-glue-staging-unavailable" \
        '{ $.message = "*glue.staging.unavailable*" }' \
        "GlueStagingUnavailable"

    # --- Alarms -----------------------------------------------------------
    # Any DLQ arrival means a job exhausted maxReceiveCount=3.
    put_alarm \
        "sow-glue-dlq-arrival" \
        "ApproximateNumberOfMessagesVisible" \
        "AWS/SQS" \
        "Maximum" \
        60 \
        1 \
        1 \
        "GreaterThanOrEqualToThreshold" \
        "Name=QueueName,Value=${DLQ_NAME}" \
        "Glue dead-letter queue has a message; a job failed 3 deliveries. Inspect glue_jobs by id before acting (see docs/glue-operator-runbook.md)."

    # A provider-ambiguous outcome means a paid request may have been sent and
    # its success is unknown. Never retry automatically.
    put_alarm \
        "sow-glue-provider-ambiguous" \
        "GlueProviderAmbiguous" \
        "$METRIC_NAMESPACE" \
        "Sum" \
        300 \
        1 \
        1 \
        "GreaterThanOrEqualToThreshold" \
        "" \
        "Glue saw an ambiguous provider outcome; never auto-retry. Inspect the job and follow the runbook."

    # Stale queue age: oldest message has waited longer than the operation
    # budget. Growing age indicates dispatch/consumer starvation.
    put_alarm \
        "sow-glue-queue-age" \
        "ApproximateAgeOfOldestMessage" \
        "AWS/SQS" \
        "Maximum" \
        300 \
        2 \
        "$OPERATION_BUDGET_SECONDS" \
        "GreaterThanThreshold" \
        "Name=QueueName,Value=${QUEUE_NAME}" \
        "Oldest Glue queue message exceeds the 3300 s operation budget; check dispatch and worker health."

    # Missing heartbeat beyond the operation budget. A claimed job that stops
    # heartbeating keeps running until the function timeout, so the Lambda
    # Duration metric at (or above) 1,900,000 ms is the durable signal. The
    # sow-glue-heartbeat-failure metric filter above is the diagnostic
    # companion for heartbeat write failures.
    put_alarm \
        "sow-glue-heartbeat-missing" \
        "Duration" \
        "AWS/Lambda" \
        "Maximum" \
        300 \
        1 \
        3300000 \
        "GreaterThanOrEqualToThreshold" \
        "Name=FunctionName,Value=${FUNCTION_NAME}" \
        "A claimed Glue job reached the 3300 s operation budget without completing; check the heartbeat and use free redispatch or staging recovery."

    # Raw-response staging-recovery spikes: repeated free recoveries usually
    # mean a staging/database fault rather than isolated bad input.
    put_alarm \
        "sow-glue-staging-recovery-spike" \
        "GlueStagingRecovery" \
        "$METRIC_NAMESPACE" \
        "Sum" \
        900 \
        1 \
        "$STAGING_RECOVERY_SPIKE_THRESHOLD" \
        "GreaterThanOrEqualToThreshold" \
        "" \
        "Spike in free staging recoveries; investigate raw-response staging or DB finalization failures."

    echo ""
    info "Glue observability configured."
}

main "$@"
