# Deploying the Render Worker to AWS Lambda

Step-by-step guide to deploy the render worker as a container-based AWS Lambda function, triggered by SQS.

## Prerequisites

- Docker installed and running
- AWS CLI configured with a profile that has ECR, Lambda, IAM, and SQS permissions
- AWS account ID: `762288208920`
- AWS region: `us-west-2`

## 1. Create IAM Role for Lambda

Create the trust policy file (`trust-policy.json`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

Create the role:

```bash
aws --profile mhuang74 iam create-role \
  --role-name sow-render-worker-role \
  --assume-role-policy-document file://trust-policy.json
```

Attach managed policies for Lambda execution and SQS access:

```bash
aws --profile mhuang74 iam attach-role-policy \
  --role-name sow-render-worker-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

aws --profile mhuang74 iam attach-role-policy \
  --role-name sow-render-worker-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole
```

## 2. Create ECR Repository

```bash
aws --profile mhuang74 ecr create-repository \
  --repository-name sow-render-worker \
  --region us-west-2
```

> **Note:** Skip this step if the repository already exists. You can check with:
> ```bash
> aws --profile mhuang74 ecr describe-repositories --repository-names sow-render-worker --region us-west-2
> ```

## 3. Build and Push Docker Image

### Authenticate Docker with ECR

```bash
aws --profile mhuang74 ecr get-login-password --region us-west-2 | \
  docker login --username AWS --password-stdin \
  762288208920.dkr.ecr.us-west-2.amazonaws.com
```

### Build the image

```bash
docker build -t sow-render-worker .
```

### Tag and push

```bash
docker tag sow-render-worker:latest \
  762288208920.dkr.ecr.us-west-2.amazonaws.com/sow-render-worker:latest

docker push 762288208920.dkr.ecr.us-west-2.amazonaws.com/sow-render-worker:latest
```

## 4. Create the Lambda Function

```bash
aws --profile mhuang74 lambda create-function \
  --function-name sow-render-worker \
  --package-type Image \
  --code ImageUri=762288208920.dkr.ecr.us-west-2.amazonaws.com/sow-render-worker:latest \
  --role arn:aws:iam::762288208920:role/sow-render-worker-role \
  --timeout 900 \
  --memory-size 2048 \
  --ephemeral-storage Size=2048
```

> **Important:** The default timeout of 30s is too short for render jobs. Use `--timeout 900` (15 min) to match the SQS visibility timeout. Memory should be at least 2048 MB for video encoding. Ephemeral storage (`/tmp`) must be at least 2048 MB to hold cached audio files and output video.

### Quick fix: update an existing Lambda's timeout, memory, and storage

If your Lambda was created with default settings (30s timeout, 512 MB memory), run:

```bash
aws --profile mhuang74 lambda update-function-configuration \
  --function-name sow-render-worker \
  --timeout 900 \
  --memory-size 2048 \
  --ephemeral-storage Size=2048
```

### Configure environment variables

```bash
aws --profile mhuang74 lambda update-function-configuration \
  --function-name sow-render-worker \
  --environment Variables={
    SOW_DATABASE_URL=<your-neon-connection-string>,
    SOW_R2_BUCKET=<your-r2-bucket>,
    SOW_R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com,
    SOW_R2_ACCESS_KEY_ID=<your-r2-access-key-id>,
    SOW_R2_SECRET_ACCESS_KEY=<your-r2-secret-access-key>,
    SOW_AWS_REGION=us-west-2,
    SOW_SQS_QUEUE_URL=https://sqs.us-west-2.amazonaws.com/762288208920/sow-render-jobs
  }
```

## 5. Create SQS Queue and DLQ

### Create the dead-letter queue

```bash
aws --profile mhuang74 sqs create-queue --queue-name sow-render-jobs-dlq
```

### Create the main queue

```bash
aws --profile mhuang74 sqs create-queue --queue-name sow-render-jobs \
  --attributes '{
    "RedrivePolicy": "{\"deadLetterTargetArn\":\"arn:aws:sqs:us-west-2:762288208920:sow-render-jobs-dlq\",\"maxReceiveCount\":\"3\"}",
    "VisibilityTimeout": "900"
  }'
```

- **Visibility timeout**: 900s (15 min) — must exceed max render duration
- **maxReceiveCount**: 3 — after 3 failures, message moves to DLQ

## 6. Connect Lambda to SQS

```bash
aws --profile mhuang74 lambda create-event-source-mapping \
  --function-name sow-render-worker \
  --batch-size 1 \
  --event-source-arn arn:aws:sqs:us-west-2:762288208920:sow-render-jobs
```

## 7. Redeploy After Code Changes

After making code changes, rebuild and push a new image, then update the Lambda:

```bash
# Build and push
docker build -t sow-render-worker .
docker tag sow-render-worker:latest \
  762288208920.dkr.ecr.us-west-2.amazonaws.com/sow-render-worker:latest
docker push 762288208920.dkr.ecr.us-west-2.amazonaws.com/sow-render-worker:latest

# Update Lambda to use the new image
aws --profile mhuang74 lambda update-function-code \
  --function-name sow-render-worker \
  --image-uri 762288208920.dkr.ecr.us-west-2.amazonaws.com/sow-render-worker:latest
```

## 8. Verify Deployment

### Check Lambda configuration

```bash
aws --profile mhuang74 lambda get-function-configuration \
  --function-name sow-render-worker
```

### Test with a manual invocation

```bash
aws --profile mhuang74 lambda invoke \
  --function-name sow-render-worker \
  --cli-binary-format raw-in-base64-out \
  --payload '{"Records":[{"messageId":"test-1","body":"{\"jobId\":\"test-job\",\"songsetId\":\"test-songset\",\"userId\":1}"}]}' \
  /tmp/lambda-response.json
```

### Check SQS queue attributes

```bash
aws --profile mhuang74 sqs get-queue-attributes \
  --queue-url https://sqs.us-west-2.amazonaws.com/762288208920/sow-render-jobs \
  --attribute-names All
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `name unknown: The repository does not exist` | Run step 2 to create the ECR repository first |
| Lambda timeout | Increase `--timeout` (render jobs need 900s) |
| `CannotPullContainer` error | Verify ECR login and that the image was pushed successfully |
| SQS messages not triggering Lambda | Verify the event-source-mapping exists and is `Enabled` |
| Job stuck in `running` | Orphan recovery marks jobs as `failed` after 30 minutes |
