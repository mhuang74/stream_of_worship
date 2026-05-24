# Fix Redundant Vercel Deploy Workflow — v2

## Problem

Same as v1 (see `fix-redundant-vercel-deploy-workflow.md`), plus six operational
concerns identified during v1 review.

## v1 Review Findings

| # | Concern | Severity |
|---|---------|----------|
| 1 | Migration and Vercel auto-deploy race condition | Critical |
| 2 | `git diff` fails on first push / force-push (all-zeros SHA) | High |
| 3 | `drizzle-kit migrate` fails if no migration files exist | Medium |
| 4 | Hardcoded pnpm version may drift from `packageManager` field | Medium |
| 5 | Workflow-wide concurrency group serializes unrelated jobs | Low |
| 6 | Lambda `update-function-code` is async — no wait/verify | Low |

## Proposed Changes

### 1. Remove `deploy-webapp` Job (same as v1)

Vercel's Git integration already handles deployment. The `amondnet/vercel-action`
step is redundant and failing.

### 2. Disable Vercel Auto-Deploy, Use Deploy Hook (addresses Concern 1)

The core issue: `migrate-db` and Vercel auto-deploy run in parallel. If Vercel
deploys new code before the migration completes, the app hits a broken schema.

**Solution:**

1. In Vercel Dashboard → Project Settings → Git → disable auto-deploy for `main`
2. Create a Vercel Deploy Hook and store the URL as `VERCEL_DEPLOY_HOOK_URL` GitHub secret
3. The `migrate-db` job calls the deploy hook as its final step after migration succeeds

This guarantees: migration → then deploy. No race.

```yaml
- name: Trigger Vercel deploy
  if: success()
  run: curl -sf -X POST "${{ secrets.VERCEL_DEPLOY_HOOK_URL }}"
```

**Trade-off:** Adds ~30s latency (hook → Vercel build start). Vercel deploys are
now fully controlled by CI — if CI is down, no deploy happens.

### 3. Add `migrate-db` Job with Proper Node Setup (same as v1, with fixes)

Replaces `deploy-webapp` with a simpler job that:
- Triggers on `webapp/**` path changes
- Sets up Node.js + pnpm
- Installs dependencies
- Runs DB schema push
- Triggers Vercel deploy via hook

### 4. Guard Against All-Zeros SHA (addresses Concern 2)

On first push to a new branch or after force-push, `github.event.before` is
all zeros, causing `git diff` to fail or produce no output.

**Solution:** Add a guard that assumes both components changed when the SHA is
all zeros (conservative — false positive runs an unnecessary job, false negative
skips a needed deploy).

```yaml
- name: Check changed paths
  id: filter
  run: |
    before="${{ github.event.before }}"
    if [ "$before" = "0000000000000000000000000000000000000000" ]; then
      echo "webapp=true" >> $GITHUB_OUTPUT
      echo "render-worker=true" >> $GITHUB_OUTPUT
    else
      webapp=false
      render_worker=false
      for file in $(git diff --name-only "$before" "${{ github.sha }}"); do
        if [[ "$file" == webapp/* ]]; then webapp=true; fi
        if [[ "$file" == services/render-worker/* ]]; then render_worker=true; fi
      done
      echo "webapp=$webapp" >> $GITHUB_OUTPUT
      echo "render-worker=$render_worker" >> $GITHUB_OUTPUT
    fi
```

### 5. Use `drizzle-kit push` Instead of `migrate` (addresses Concern 3)

`drizzle-kit migrate` requires pre-generated migration files in `webapp/drizzle/`.
If none exist, it errors. `drizzle-kit push` diffs the schema against the live DB
and applies changes directly — idempotent, no files needed.

```yaml
- name: Push DB schema
  env:
    DATABASE_URL: ${{ secrets.DATABASE_URL }}
  run: |
    if [ -z "$DATABASE_URL" ]; then
      echo "Error: DATABASE_URL secret is not configured"
      exit 1
    fi
    npx drizzle-kit push --force
```

**Trade-off:** `push` bypasses the migration journal. If you later need to replay
migrations (e.g., for a staging DB), you'd need to generate migration files
separately. For a single-production-DB setup this is fine. Switch to `migrate`
with generated files if multi-environment replay is needed later.

### 6. Dynamic pnpm Version from `packageManager` (addresses Concern 4)

Hardcoding `version: 10` can drift from the `packageManager` field in
`webapp/package.json`, causing `pnpm install --frozen-lockfile` to fail.

**Solution:** Extract the version dynamically.

```yaml
- name: Get pnpm version
  id: pnpm-version
  working-directory: webapp
  run: echo "version=$(node -p "require('./package.json').packageManager.replace('pnpm@','')")" >> $GITHUB_OUTPUT

- uses: pnpm/action-setup@v4
  with:
    version: ${{ steps.pnpm-version.outputs.version }}
```

### 7. Per-Job Concurrency Groups (addresses Concern 5)

The workflow-wide `concurrency` group serializes `migrate-db` and
`deploy-render-worker` against each other. They are independent and should run
in parallel.

**Solution:** Remove the top-level `concurrency` block. Add per-job concurrency
instead.

```yaml
jobs:
  migrate-db:
    concurrency:
      group: migrate-db-${{ github.ref }}
      cancel-in-progress: false
    ...

  deploy-render-worker:
    concurrency:
      group: deploy-render-worker-${{ github.ref }}
      cancel-in-progress: false
    ...
```

### 8. Wait for Lambda Update to Complete (addresses Concern 6)

`aws lambda update-function-code` is asynchronous — the function may still be
`InProgress` when the workflow finishes.

**Solution:** Add `aws lambda wait function-updated`.

```yaml
- name: Wait for Lambda update to complete
  run: |
    aws lambda wait function-updated \
      --function-name sow-render-worker
```

**Trade-off:** Polls every 5s, default timeout 10min. Container image updates
typically take 1-3 minutes. Acceptable.

## New Workflow

```yaml
name: Deploy

on:
  push:
    branches: [main]
    paths:
      - "webapp/**"
      - "services/render-worker/**"

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
          before="${{ github.event.before }}"
          if [ "$before" = "0000000000000000000000000000000000000000" ]; then
            echo "webapp=true" >> $GITHUB_OUTPUT
            echo "render-worker=true" >> $GITHUB_OUTPUT
          else
            webapp=false
            render_worker=false
            for file in $(git diff --name-only "$before" "${{ github.sha }}"); do
              if [[ "$file" == webapp/* ]]; then webapp=true; fi
              if [[ "$file" == services/render-worker/* ]]; then render_worker=true; fi
            done
            echo "webapp=$webapp" >> $GITHUB_OUTPUT
            echo "render-worker=$render_worker" >> $GITHUB_OUTPUT
          fi

  migrate-db:
    name: Run DB Migrations + Deploy
    needs: detect-changes
    if: needs.detect-changes.outputs.webapp == 'true'
    runs-on: ubuntu-latest
    concurrency:
      group: migrate-db-${{ github.ref }}
      cancel-in-progress: false
    defaults:
      run:
        working-directory: webapp

    steps:
      - uses: actions/checkout@v4

      - name: Get pnpm version
        id: pnpm-version
        run: echo "version=$(node -p "require('./package.json').packageManager.replace('pnpm@','')")" >> $GITHUB_OUTPUT

      - uses: pnpm/action-setup@v4
        with:
          version: ${{ steps.pnpm-version.outputs.version }}

      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: pnpm
          cache-dependency-path: webapp/pnpm-lock.yaml

      - name: Install dependencies
        run: pnpm install --frozen-lockfile

      - name: Push DB schema
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
        run: |
          if [ -z "$DATABASE_URL" ]; then
            echo "Error: DATABASE_URL secret is not configured"
            exit 1
          fi
          npx drizzle-kit push --force

      - name: Trigger Vercel deploy
        if: success()
        run: curl -sf -X POST "${{ secrets.VERCEL_DEPLOY_HOOK_URL }}"

  deploy-render-worker:
    name: Deploy Render Worker to Lambda
    needs: detect-changes
    if: needs.detect-changes.outputs.render-worker == 'true'
    runs-on: ubuntu-latest
    concurrency:
      group: deploy-render-worker-${{ github.ref }}
      cancel-in-progress: false
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

      - name: Wait for Lambda update to complete
        run: |
          aws lambda wait function-updated \
            --function-name sow-render-worker
```

## Key Differences from v1 Spec

| Aspect | v1 | v2 |
|--------|----|----|
| Vercel deploy | Removed (auto-deploys) | Removed auto-deploy; triggered via Deploy Hook after migration |
| Migration vs deploy race | Not addressed | Solved: deploy hook only fires after migration succeeds |
| First push / force-push | No guard | All-zeros SHA guard → assume both changed |
| DB migration command | `drizzle-kit migrate` | `drizzle-kit push --force` (idempotent, no files needed) |
| pnpm version | Hardcoded `version: 10` | Dynamic extraction from `packageManager` field |
| Concurrency | Workflow-wide group | Per-job concurrency groups (parallel execution) |
| Lambda update | No wait | `aws lambda wait function-updated` added |

## Secrets Changes

| Secret | Action | Purpose |
|--------|--------|---------|
| `VERCEL_DEPLOY_HOOK_URL` | **Add** | Trigger deploy after migration |
| `VERCEL_TOKEN` | **Remove** | No longer needed |
| `VERCEL_ORG_ID` | **Remove** | No longer needed |
| `VERCEL_PROJECT_ID` | **Remove** | No longer needed |
| `DATABASE_URL` | Keep | Neon PostgreSQL connection string |
| `AWS_ACCESS_KEY_ID` | Keep | AWS credentials for ECR/Lambda |
| `AWS_SECRET_ACCESS_KEY` | Keep | AWS credentials for ECR/Lambda |

## Vercel Configuration Change (Required Before Merge)

1. Go to Vercel Dashboard → Project Settings → Git
2. Disable auto-deploy for the `main` branch
3. Create a Deploy Hook (Settings → Git → Deploy Hooks)
4. Add the hook URL to GitHub repo secrets as `VERCEL_DEPLOY_HOOK_URL`

## Implementation Steps

1. Configure Vercel Deploy Hook and disable auto-deploy (manual, before merge)
2. Add `VERCEL_DEPLOY_HOOK_URL` to GitHub secrets
3. Replace `.github/workflows/deploy.yml` with the new workflow above
4. Remove unused Vercel secrets from GitHub (`VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`)

## Verification

After merging:
1. Push a change to `webapp/**` — should trigger `migrate-db` job, then Vercel deploy via hook
2. Push a change to `services/render-worker/**` — should trigger `deploy-render-worker` job only
3. Push a change to both — should trigger both jobs in parallel
4. Verify Vercel does NOT auto-deploy on push (only via hook)
5. Verify Lambda update completes (check `function-updated` wait step in logs)
