# Fix Redundant Vercel Deploy Workflow

## Problem

The `.github/workflows/deploy.yml` contains a `deploy-webapp` job that deploys to Vercel using `amondnet/vercel-action`. However, the repo is already linked to Vercel and auto-deploys on push to `main`. This causes:

1. **Redundant deployment** — Vercel deploys twice (auto-deploy + CI job)
2. **Failing CI** — The `amondnet/vercel-action` is failing (likely due to token/permission issues)
3. **Missing DB migrations** — The job's DB migration step is broken because it runs `npx drizzle-kit migrate` without first installing Node dependencies

## Current Workflow Structure

```yaml
jobs:
  detect-changes:     # Detects which paths changed
  deploy-webapp:      # Vercel deploy + DB migrate (REDUNDANT deploy, BROKEN migrate)
  deploy-render-worker:  # ECR + Lambda deploy (KEEP)
```

## Proposed Changes

### 1. Remove `deploy-webapp` Job

The Vercel deployment step is redundant. Vercel's Git integration already handles auto-deployment on push to `main`.

### 2. Add New `migrate-db` Job

Replace `deploy-webapp` with a simpler `migrate-db` job that:

- Triggers on `webapp/**` path changes
- Properly sets up Node.js + pnpm
- Installs dependencies (`pnpm install --frozen-lockfile`)
- Runs `npx drizzle-kit migrate`

### 3. Keep `detect-changes` and `deploy-render-worker`

These jobs are still needed:
- `detect-changes` — Determines which components changed
- `deploy-render-worker` — Deploys Lambda container to ECR (unrelated to Vercel)

## New Workflow

```yaml
name: Deploy

on:
  push:
    branches: [main]
    paths:
      - "webapp/**"
      - "services/render-worker/**"

concurrency:
  group: deploy-${{ github.ref }}
  cancel-in-progress: false

jobs:
  detect-changes:
    name: Detect Changes
    runs-on: ubuntu-latest
    outputs:
      webapp: ${{ steps.filter.outputs.webapp }}
      render-worker: ${{ steps.filter.outputs.render-worker }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Check changed paths
        id: filter
        run: |
          webapp=false
          render_worker=false
          for file in $(git diff --name-only ${{ github.event.before }} ${{ github.sha }}); do
            if [[ "$file" == webapp/* ]]; then
              webapp=true
            fi
            if [[ "$file" == services/render-worker/* ]]; then
              render_worker=true
            fi
          done
          echo "webapp=$webapp" >> $GITHUB_OUTPUT
          echo "render-worker=$render_worker" >> $GITHUB_OUTPUT

  migrate-db:
    name: Run DB Migrations
    needs: detect-changes
    if: needs.detect-changes.outputs.webapp == 'true'
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: webapp

    steps:
      - uses: actions/checkout@v4

      - uses: pnpm/action-setup@v4
        with:
          version: 10

      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: pnpm
          cache-dependency-path: webapp/pnpm-lock.yaml

      - name: Install dependencies
        run: pnpm install --frozen-lockfile

      - name: Run DB migrations
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
        run: |
          if [ -z "$DATABASE_URL" ]; then
            echo "Error: DATABASE_URL secret is not configured"
            exit 1
          fi
          npx drizzle-kit migrate

  deploy-render-worker:
    name: Deploy Render Worker to Lambda
    needs: detect-changes
    if: needs.detect-changes.outputs.render-worker == 'true'
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: services/render-worker

    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: us-east-1

      - name: Login to Amazon ECR
        id: login-ecr
        uses: aws-actions/amazon-ecr-login@v2

      - name: Build, tag, and push image to Amazon ECR
        id: build-image
        env:
          ECR_REGISTRY: ${{ steps.login-ecr.outputs.registry }}
          ECR_REPOSITORY: sow-render-worker
          IMAGE_TAG: ${{ github.sha }}
        run: |
          docker build -t $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG .
          docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
          echo "image=$ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG" >> $GITHUB_OUTPUT

      - name: Update Lambda function
        env:
          IMAGE_URI: ${{ steps.build-image.outputs.image }}
        run: |
          aws lambda update-function-code \
            --function-name sow-render-worker \
            --image-uri $IMAGE_URI \
            --publish
```

## Key Differences from Current Workflow

| Aspect | Current | New |
|--------|---------|-----|
| Vercel deploy | Redundant (auto-deploys) | Removed |
| DB migration | Broken (no deps installed) | Fixed (pnpm install first) |
| Node setup | Missing | Added (pnpm + Node 20) |
| Render worker | Unchanged | Unchanged |

## Implementation Steps

1. Replace `.github/workflows/deploy.yml` with the new workflow above
2. Verify GitHub secrets are configured:
   - `DATABASE_URL` — Neon PostgreSQL connection string
   - `AWS_ACCESS_KEY_ID` — AWS credentials for ECR/Lambda
   - `AWS_SECRET_ACCESS_KEY` — AWS credentials for ECR/Lambda
3. Remove unused secrets (if any):
   - `VERCEL_TOKEN` — No longer needed
   - `VERCEL_ORG_ID` — No longer needed
   - `VERCEL_PROJECT_ID` — No longer needed

## Verification

After merging:
1. Push a change to `webapp/**` — should trigger `migrate-db` job only
2. Push a change to `services/render-worker/**` — should trigger `deploy-render-worker` job only
3. Push a change to both — should trigger both jobs
4. Verify Vercel auto-deploys independently
