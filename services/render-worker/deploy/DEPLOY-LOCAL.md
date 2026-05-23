# Deploying the Render Worker Locally via LocalStack

Set up the render worker Lambda function and SQS queue on LocalStack for local development and testing.

## Prerequisites

- Docker running
- LocalStack Pro container running (`localstack-aws`)
- `awslocal` CLI installed (`pip install awscli-local`)
- `aws` CLI installed and configured with AWS credentials (for ECR operations)
- Render worker Docker image built locally (`docker build -t sow-render-worker .`)

## 1. Create IAM Role

```bash
awslocal iam create-role \
  --role-name sow-render-worker-role \
  --assume-role-policy-document file://trust-policy.json
```

Attach managed policies:

```bash
awslocal iam attach-role-policy \
  --role-name sow-render-worker-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

awslocal iam attach-role-policy \
  --role-name sow-render-worker-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole
```

## 2. Create ECR Repository and Push Image

> **Note:** LocalStack free tier does not support ECR. The Docker image is pushed to **real AWS private ECR** instead. The LocalStack Lambda will pull the image from AWS ECR at invocation time.

### Create the repository (real AWS)

```bash
aws ecr create-repository --repository-name sow-render-worker --region us-west-2
```

### Authenticate Docker with AWS ECR

```bash
aws ecr get-login-password --region us-west-2 | \
  docker login --username AWS --password-stdin \
  762288208920.dkr.ecr.us-west-2.amazonaws.com
```

### Tag and push the image

```bash
docker tag sow-render-worker:latest \
  762288208920.dkr.ecr.us-west-2.amazonaws.com/sow-render-worker:latest

docker push 762288208920.dkr.ecr.us-west-2.amazonaws.com/sow-render-worker:latest
```

## 3. Create SQS Queues

### Create the dead-letter queue

```bash
awslocal sqs create-queue --queue-name sow-render-jobs-dlq
```

### Create the main queue with redrive policy

```bash
awslocal sqs create-queue --queue-name sow-render-jobs \
  --attributes '{
    "RedrivePolicy": "{\"deadLetterTargetArn\":\"arn:aws:sqs:us-west-2:762288208920:sow-render-jobs-dlq\",\"maxReceiveCount\":\"3\"}",
    "VisibilityTimeout": "900"
  }'
```

- **Visibility timeout**: 900s (15 min) — must exceed max render duration
- **maxReceiveCount**: 3 — after 3 failures, message moves to DLQ

> **Note:** LocalStack uses account ID `762288208920` in SQS ARNs. Verify with:
> ```bash
> awslocal sqs get-queue-attributes \
>   --queue-url http://sqs.us-west-2.localhost.localstack.cloud:4566/762288208920/sow-render-jobs \
>   --attribute-names QueueArn
> ```

## 4. Create Lambda Function

```bash
awslocal lambda create-function \
  --function-name sow-render-worker \
  --package-type Image \
  --code ImageUri=762288208920.dkr.ecr.us-west-2.amazonaws.com/sow-render-worker:latest \
  --role arn:aws:iam::762288208920:role/sow-render-worker-role \
  --timeout 900 \
  --memory-size 2048 \
  --environment Variables={
    SOW_DATABASE_URL=postgresql://user:password@ep-xxx.region.aws.neon.tech/dbname?sslmode=require,
    SOW_R2_BUCKET=your-r2-bucket-name,
    SOW_R2_ENDPOINT_URL=https://your-account-id.r2.cloudflarestorage.com,
    SOW_R2_ACCESS_KEY_ID=your-r2-access-key-id,
    SOW_R2_SECRET_ACCESS_KEY=your-r2-secret-access-key,
    SOW_AWS_REGION=us-west-2,
    SOW_SQS_QUEUE_URL=http://sqs.us-west-2.localhost.localstack.cloud:4566/762288208920/sow-render-jobs
  }
```

> **Important:**
> - **Timeout**: 900s (15 min) — render jobs are long-running
> - **Memory**: 2048 MB — video encoding is memory-intensive
> - **ImageUri**: Must reference the AWS private ECR image pushed in step 2 (LocalStack free tier does not support ECR)
> - **SQS Queue URL**: Uses the LocalStack endpoint format, not the real AWS format
> - Replace the environment variable values with your actual credentials

## 5. Create Event Source Mapping

Connect the SQS queue to the Lambda function:

```bash
awslocal lambda create-event-source-mapping \
  --function-name sow-render-worker \
  --batch-size 1 \
  --event-source-arn arn:aws:sqs:us-west-2:762288208920:sow-render-jobs
```

> **Note:** If the ARN doesn't match, get the correct one from:
> ```bash
> awslocal sqs get-queue-attributes \
>   --queue-url http://sqs.us-west-2.localhost.localstack.cloud:4566/762288208920/sow-render-jobs \
>   --attribute-names QueueArn
> ```

## 6. Verify

### Check Lambda configuration

```bash
awslocal lambda get-function-configuration --function-name sow-render-worker
```

### Check event source mapping

```bash
awslocal lambda list-event-source-mappings --function-name sow-render-worker
```

### Send a test SQS message

```bash
awslocal sqs send-message \
  --queue-url http://sqs.us-west-2.localhost.localstack.cloud:4566/762288208920/sow-render-jobs \
  --message-body '{"jobId":"test-job-1","songsetId":"test-songset","userId":1}'
```

### Invoke Lambda directly

```bash
awslocal lambda invoke \
  --function-name sow-render-worker \
  --cli-binary-format raw-in-base64-out \
  --payload '{"Records":[{"messageId":"test-1","body":"{\"jobId\":\"test-job-1\",\"songsetId\":\"test-songset\",\"userId\":1}"}]}' \
  /tmp/lambda-response.json
```

### Check CloudWatch logs

```bash
# Verify the log group exists
awslocal logs describe-log-groups --log-group-name-prefix /aws/lambda/sow-render-worker

# List log streams (each Lambda invocation creates one)
awslocal logs describe-log-streams \
  --log-group-name /aws/lambda/sow-render-worker \
  --order-by LastEventTime --descending --limit 5

# View recent log events from a specific stream
awslocal logs get-log-events \
  --log-group-name /aws/lambda/sow-render-worker \
  --log-stream-name "<stream-name-from-above>"

# Tail/follow logs in real time (poll for new events)
awslocal logs tail /aws/lambda/sow-render-worker

# Filter logs for errors across all streams
awslocal logs filter-log-events \
  --log-group-name /aws/lambda/sow-render-worker \
  --filter-pattern "ERROR"
```

#### What to look for

- **Healthy execution**: Log shows job received, processing steps (download, render, upload), and a "completed" or "success" message
- **Common errors**: Connection failures (DB/R2 credentials), FFmpeg encoding errors, timeout messages, missing files
- **No log streams**: Lambda is not being invoked — check that the event source mapping state is `Enabled`

## 7. Redeploy After Code Changes

Rebuild the image, push to AWS ECR, and update the Lambda:

```bash
# Rebuild
docker build -t sow-render-worker .

# Push to AWS ECR
aws ecr get-login-password --region us-west-2 | \
  docker login --username AWS --password-stdin \
  762288208920.dkr.ecr.us-west-2.amazonaws.com

docker tag sow-render-worker:latest \
  762288208920.dkr.ecr.us-west-2.amazonaws.com/sow-render-worker:latest
docker push 762288208920.dkr.ecr.us-west-2.amazonaws.com/sow-render-worker:latest

# Update Lambda
awslocal lambda update-function-code \
  --function-name sow-render-worker \
  --image-uri 762288208920.dkr.ecr.us-west-2.amazonaws.com/sow-render-worker:latest
```

## 8. Tear Down

Remove all LocalStack resources to start fresh:

```bash
awslocal lambda delete-function --function-name sow-render-worker
awslocal lambda delete-event-source-mapping --uuid <mapping-uuid>
awslocal sqs delete-queue --queue-url http://sqs.us-west-2.localhost.localstack.cloud:4566/762288208920/sow-render-jobs
awslocal sqs delete-queue --queue-url http://sqs.us-west-2.localhost.localstack.cloud:4566/762288208920/sow-render-jobs-dlq
aws ecr delete-repository --repository-name sow-render-worker --force --region us-west-2
awslocal iam detach-role-policy --role-name sow-render-worker-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
awslocal iam detach-role-policy --role-name sow-render-worker-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole
awslocal iam delete-role --role-name sow-render-worker-role
```

Get the mapping UUID with:
```bash
awslocal lambda list-event-source-mappings --function-name sow-render-worker --query 'EventSourceMappings[0].UUID' --output text
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `RepositoryNotFoundException` | Run step 2 to create the ECR repository first |
| Lambda not triggered by SQS | Verify event source mapping exists and state is `Enabled` |
| `AccessDeniedException` on role | Verify the IAM role exists and policies are attached |
| Wrong SQS ARN in redrive policy | Check the actual ARN with `awslocal sqs get-queue-attributes` and recreate the queue |
| Lambda timeout | Increase `--timeout` (render jobs need 900s) |
| Image pull failure | Ensure image was pushed to AWS private ECR, not LocalStack ECR |
| Queue URL format mismatch | LocalStack uses `http://sqs.<region>.localhost.localstack.cloud:4566/<account>/<name>` |
| No log streams appear | Lambda not invoked — check event source mapping state is `Enabled` |
| Logs show `ERROR` | Use `awslocal logs filter-log-events --log-group-name /aws/lambda/sow-render-worker --filter-pattern "ERROR"` to find details |
