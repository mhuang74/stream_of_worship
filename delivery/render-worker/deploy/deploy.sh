#!/usr/bin/env bash
set -euo pipefail

AWS_PROFILE="${AWS_PROFILE:-mhuang74}"
AWS_REGION="us-west-2"
ACCOUNT_ID="762288208920"
ECR_REPO="sow-render-worker"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"
FUNCTION_NAME="sow-render-worker"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Logging in to ECR ==="
aws --profile "$AWS_PROFILE" ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ECR_URI"

echo "=== Building Docker image ==="
docker build -t "$ECR_REPO" "$PROJECT_DIR"

echo "=== Tagging image ==="
docker tag "${ECR_REPO}:latest" "${ECR_URI}:latest"

echo "=== Pushing image to ECR ==="
docker push "${ECR_URI}:latest"

echo "=== Updating Lambda function code ==="
aws --profile "$AWS_PROFILE" lambda update-function-code \
  --function-name "$FUNCTION_NAME" \
  --image-uri "${ECR_URI}:latest"

echo "=== Deploy complete ==="
