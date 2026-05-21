# SQS LocalStack Endpoint Support

**Service:** Web App (`webapp/`)  
**Status:** Draft  
**Created:** 2026-05-22

## Overview

Add custom endpoint support to the webapp SQS client so it can communicate with LocalStack (or any non-AWS SQS-compatible service) without relying on the AWS SDK v3's QueueUrl host auto-detection hack, which produces a warning on every request:

> QueueUrl=http://sqs.us-west-2.localhost.localstack.cloud:4566/... differs from SQSClient resolved endpoint=https://sqs.us-west-2.amazonaws.com/, using QueueUrl host as endpoint.

## 1. Add `endpoint` to `SQSClientConfig`

**File:** `webapp/src/lib/sqs/client.ts`

Add `endpoint?: string` to the `SQSClientConfig` interface:

```typescript
export interface SQSClientConfig {
  region: string;
  queueUrl: string;
  endpoint?: string;
  accessKeyId?: string;
  secretAccessKey?: string;
}
```

## 2. Pass `endpoint` and `useQueueUrlAsEndpoint` to `AWSSQSClient`

**File:** `webapp/src/lib/sqs/client.ts`

Update the `SQSClient` constructor to pass `endpoint` and `useQueueUrlAsEndpoint: false` to the AWS SDK client:

```typescript
this.client = new AWSSQSClient({
  region: config.region,
  ...(config.endpoint ? { endpoint: config.endpoint } : {}),
  useQueueUrlAsEndpoint: false,
  ...(config.accessKeyId && config.secretAccessKey
    ? {
        credentials: {
          accessKeyId: config.accessKeyId,
          secretAccessKey: config.secretAccessKey,
        },
      }
    : {}),
});
```

**Why `useQueueUrlAsEndpoint: false` always?** When `endpoint` is set, the SDK should use it, not derive the host from the QueueUrl. When `endpoint` is not set (production AWS), the SDK defaults to the regional endpoint which matches the QueueUrl anyway, so `false` is safe in both cases.

## 3. Read `SQS_ENDPOINT_URL` in `createSQSClientFromEnv()`

**File:** `webapp/src/lib/sqs/client.ts`

Add `endpoint` from `SQS_ENDPOINT_URL` env var:

```typescript
export function createSQSClientFromEnv(): SQSClient {
  const region = process.env.AWS_REGION;
  const queueUrl = process.env.SQS_QUEUE_URL;
  const endpoint = process.env.SQS_ENDPOINT_URL;
  const accessKeyId = process.env.AWS_ACCESS_KEY_ID;
  const secretAccessKey = process.env.AWS_SECRET_ACCESS_KEY;

  if (!region) {
    throw new Error(
      "AWS_REGION environment variable is required for SQS client"
    );
  }

  if (!queueUrl) {
    throw new Error(
      "SQS_QUEUE_URL environment variable is required for SQS client"
    );
  }

  return new SQSClient({
    region,
    queueUrl,
    endpoint,
    accessKeyId,
    secretAccessKey,
  });
}
```

`SQS_ENDPOINT_URL` is optional — when unset, the AWS SDK uses the default regional endpoint (production AWS behavior).

## 4. Update `.env.example`

**File:** `webapp/.env.example`

Add `SQS_ENDPOINT_URL` with a comment:

```
# Custom SQS endpoint URL (optional, for LocalStack or other non-AWS SQS services).
# When set, the SDK sends requests to this endpoint instead of the default AWS endpoint.
# Example: http://localhost:4566 (with SSH port forwarding to LocalStack)
SQS_ENDPOINT_URL=
```

## 5. Update `.env.production.example`

**File:** `webapp/.env.production.example`

Add `SQS_ENDPOINT_URL` in the AWS SQS section:

```
# Custom SQS endpoint URL (optional).
# Only needed for non-AWS SQS-compatible services (e.g., LocalStack for local dev).
# Leave unset for production AWS deployments.
SQS_ENDPOINT_URL=
```

## 6. Update SQS Client Tests

**File:** `webapp/src/test/lib/sqs/client.test.ts`

### 6a. Constructor tests

Add test: "creates client with custom endpoint":

```typescript
it("creates client with custom endpoint", () => {
  const client = new SQSClient({
    region: "us-east-1",
    queueUrl: "http://sqs.us-west-2.localhost.localstack.cloud:4566/000000000000/sow-render-jobs",
    endpoint: "http://localhost:4566",
  });
  expect(client).toBeInstanceOf(SQSClient);
});
```

Add test: "creates client without endpoint (production AWS)":

```typescript
it("creates client without endpoint (production AWS)", () => {
  const client = new SQSClient({
    region: "us-east-1",
    queueUrl: "https://sqs.us-east-1.amazonaws.com/123456789/render-jobs",
  });
  expect(client).toBeInstanceOf(SQSClient);
});
```

### 6b. `createSQSClientFromEnv` tests

Add test: "reads SQS_ENDPOINT_URL from environment":

```typescript
it("reads SQS_ENDPOINT_URL from environment", () => {
  process.env.AWS_REGION = "us-east-1";
  process.env.SQS_QUEUE_URL =
    "http://sqs.us-west-2.localhost.localstack.cloud:4566/000000000000/sow-render-jobs";
  process.env.SQS_ENDPOINT_URL = "http://localhost:4566";

  const client = createSQSClientFromEnv();
  expect(client).toBeInstanceOf(SQSClient);
});
```

Add test: "creates client without endpoint when SQS_ENDPOINT_URL is not set":

```typescript
it("creates client without endpoint when SQS_ENDPOINT_URL is not set", () => {
  process.env.AWS_REGION = "us-east-1";
  process.env.SQS_QUEUE_URL =
    "https://sqs.us-east-1.amazonaws.com/123456789/render-jobs";
  delete process.env.SQS_ENDPOINT_URL;

  const client = createSQSClientFromEnv();
  expect(client).toBeInstanceOf(SQSClient);
});
```

## 7. Update Deployment Tests

**File:** `webapp/src/test/deployment/deployment.test.ts`

Add test in the `.env.example — AWS SQS variables` describe block:

```typescript
it("documents SQS_ENDPOINT_URL", () => {
  const content = readEnvExample();
  expect(content).toContain("SQS_ENDPOINT_URL=");
});
```

## 8. Files to Modify

| File | Changes |
|---|---|
| `webapp/src/lib/sqs/client.ts` | Add `endpoint?` to `SQSClientConfig`; pass `endpoint` + `useQueueUrlAsEndpoint: false` to `AWSSQSClient`; read `SQS_ENDPOINT_URL` in `createSQSClientFromEnv()` |
| `webapp/.env.example` | Add `SQS_ENDPOINT_URL=` with comment |
| `webapp/.env.production.example` | Add `SQS_ENDPOINT_URL=` with comment in SQS section |
| `webapp/src/test/lib/sqs/client.test.ts` | Add constructor + env tests for `endpoint` / `SQS_ENDPOINT_URL` |
| `webapp/src/test/deployment/deployment.test.ts` | Add test: `.env.example` documents `SQS_ENDPOINT_URL` |

## 9. Out of Scope

- **Render-worker SQS client:** The render-worker is a Lambda function; AWS Lambda polls SQS natively. No code changes needed.
- **LocalStack `0.0.0.0` binding on unoccluded:** The SSH port forwarding workaround is sufficient. Changing the Docker port binding is a separate infrastructure task.
- **Local dev polling script for render-worker:** Not addressed. For local end-to-end testing, a separate script that polls LocalStack SQS and invokes the worker would be needed, but that's a different feature.
- **`forcePathStyle` or other S3-compatible settings:** Not relevant to SQS.

## 10. Decision Rationale

| Decision | Rationale |
|---|---|
| `useQueueUrlAsEndpoint: false` always | Prevents the SDK from auto-detecting the QueueUrl host as the endpoint. In production, the regional endpoint matches the QueueUrl host anyway. In LocalStack, the explicit `endpoint` is used instead. |
| `SQS_ENDPOINT_URL` env var name | Follows the same naming convention as `R2_ENDPOINT_URL` already used in the codebase. |
| Optional `endpoint` in config | Production AWS doesn't need it. Only LocalStack/development setups require a custom endpoint. |
| No changes to render-worker | The render-worker doesn't create an SQS client — Lambda handles polling. Adding endpoint support there would require a fundamentally different local dev architecture. |
