# Align Webapp Env Vars to `SOW_` Prefix

## Summary

Rename all project-specific environment variables in the webapp to use the `SOW_` prefix, aligning with the standardized naming convention in `/opt/sow/.env.server` and the render-worker service. Better Auth and `NEXT_PUBLIC_*` variables are excluded per user decision.

## Variable Mapping

| Current | New | Notes |
|---|---|---|
| `DATABASE_URL` | `SOW_DATABASE_URL` | Already used in `drizzle.config.ts` |
| `R2_ACCOUNT_ID` | `SOW_R2_ENDPOINT_URL` | Switch from account ID to full endpoint URL (like render-worker) |
| `R2_ACCESS_KEY_ID` | `SOW_R2_ACCESS_KEY_ID` | |
| `R2_SECRET_ACCESS_KEY` | `SOW_R2_SECRET_ACCESS_KEY` | |
| `R2_BUCKET_NAME` | `SOW_R2_BUCKET` | Shorter, matches render-worker |
| `AWS_REGION` | `SOW_AWS_REGION` | |
| `SQS_QUEUE_URL` | `SOW_SQS_QUEUE_URL` | |
| `AWS_ACCESS_KEY_ID` | `SOW_AWS_ACCESS_KEY_ID` | Uppercase AWS SDK convention |
| `AWS_SECRET_ACCESS_KEY` | `SOW_AWS_SECRET_ACCESS_KEY` | Uppercase AWS SDK convention |
| `SQS_ENDPOINT_URL` | `SOW_SQS_ENDPOINT_URL` | |
| `R2_PUBLIC_DOMAIN` (in `.env.production.example`) | `NEXT_PUBLIC_R2_PUBLIC_DOMAIN` | Bug fix: production example was missing `NEXT_PUBLIC_` prefix |
| `BETTER_AUTH_SECRET` | (no change) | |
| `BETTER_AUTH_URL` | (no change) | |
| `NEXT_PUBLIC_*` | (no change) | Next.js convention |
| `SOW_RENDER_WORKER_MODE` | (already SOW_) | |
| `SOW_RENDER_WORKER_REST_URL` | (already SOW_) | |

> **Note:** `webapp/drizzle.config.ts` already uses `SOW_DATABASE_URL` (line 8), creating a pre-existing inconsistency with `src/db/index.ts` which uses `DATABASE_URL`. This change resolves that inconsistency. No modification to `drizzle.config.ts` is needed.

## Files to Modify (12 files)

### 1. `webapp/src/db/index.ts` (lines 5-6, 9)

- `process.env.DATABASE_URL` → `process.env.SOW_DATABASE_URL`
- Error message: `"DATABASE_URL environment variable is required"` → `"SOW_DATABASE_URL environment variable is required"`

### 2. `webapp/src/lib/r2/client.ts` — interface + constructor refactor

**`R2Config` interface (line 8-14):**

- Remove `accountId: string`
- Add `endpointUrl: string`

> **Breaking change:** `accountId: string → endpointUrl: string` changes the exported `R2Config` interface. Currently only used within the webapp, but any external consumer would break.

**`R2Client` constructor (line 56-68):**

- Replace `const endpoint = \`https://${config.accountId}.r2.cloudflarestorage.com\`;` with `const endpoint = config.endpointUrl;`

**`createR2ClientFromEnv()` (lines 226-244):**

- `process.env.R2_ACCOUNT_ID` → `process.env.SOW_R2_ENDPOINT_URL`
- `process.env.R2_ACCESS_KEY_ID` → `process.env.SOW_R2_ACCESS_KEY_ID`
- `process.env.R2_SECRET_ACCESS_KEY` → `process.env.SOW_R2_SECRET_ACCESS_KEY`
- `process.env.R2_BUCKET_NAME` → `process.env.SOW_R2_BUCKET`
- Variable names: `accountId` → `endpointUrl`, `bucketName` → `bucketName` (keep)
- Error message update: `"Set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, and R2_BUCKET_NAME environment variables."` → `"Set SOW_R2_ENDPOINT_URL, SOW_R2_ACCESS_KEY_ID, SOW_R2_SECRET_ACCESS_KEY, and SOW_R2_BUCKET environment variables."`
- Constructor call: `accountId` → `endpointUrl`

### 3. `webapp/src/lib/sqs/client.ts` (lines 53-78)

- `process.env.AWS_REGION` → `process.env.SOW_AWS_REGION`
- `process.env.SQS_QUEUE_URL` → `process.env.SOW_SQS_QUEUE_URL`
- `process.env.SQS_ENDPOINT_URL` → `process.env.SOW_SQS_ENDPOINT_URL`
- `process.env.AWS_ACCESS_KEY_ID` → `process.env.SOW_AWS_ACCESS_KEY_ID`
- `process.env.AWS_SECRET_ACCESS_KEY` → `process.env.SOW_AWS_SECRET_ACCESS_KEY`
- Error messages: `"AWS_REGION"` → `"SOW_AWS_REGION"`, `"SQS_QUEUE_URL"` → `"SOW_SQS_QUEUE_URL"`

### 4. `webapp/.env.example` — rename all vars

```
DATABASE_URL → SOW_DATABASE_URL
R2_ACCOUNT_ID → SOW_R2_ENDPOINT_URL (value changes from blank to https://your-account-id.r2.cloudflarestorage.com)
R2_ACCESS_KEY_ID → SOW_R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY → SOW_R2_SECRET_ACCESS_KEY
R2_BUCKET_NAME → SOW_R2_BUCKET
AWS_REGION → SOW_AWS_REGION
SQS_QUEUE_URL → SOW_SQS_QUEUE_URL
AWS_ACCESS_KEY_ID → SOW_AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY → SOW_AWS_SECRET_ACCESS_KEY
SQS_ENDPOINT_URL → SOW_SQS_ENDPOINT_URL
```

### 5. `webapp/.env.production.example` — rename all vars + fix `R2_PUBLIC_DOMAIN` bug

Same renames as above, plus:

- `R2_PUBLIC_DOMAIN` → `NEXT_PUBLIC_R2_PUBLIC_DOMAIN` (bug fix: was missing `NEXT_PUBLIC_` prefix)

### 6. `webapp/README.md` — update env var list (lines 17-22) and psql command (line 39)

- Replace bullet list with `SOW_`-prefixed names
- `psql "$DATABASE_URL"` → `psql "$SOW_DATABASE_URL"`

### 7. `webapp/DEPLOY-VERCEL.md` — update variable reference table and all inline references

- Lines 37-38: `vercel env add DATABASE_URL` → `vercel env add SOW_DATABASE_URL`, `vercel env add R2_ACCOUNT_ID` → `vercel env add SOW_R2_ENDPOINT_URL`
- Lines 46-60: Variable reference table — rename all rows
- Line 77: `vercel env add DATABASE_URL production` → `vercel env add SOW_DATABASE_URL production`
- Lines 224-225: `export DATABASE_URL=` → `export SOW_DATABASE_URL=`
- Line 242: `R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME` → `SOW_R2_ENDPOINT_URL, SOW_R2_ACCESS_KEY_ID, SOW_R2_SECRET_ACCESS_KEY, SOW_R2_BUCKET`
- Line 250: `AWS_REGION, SQS_QUEUE_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY` → `SOW_AWS_REGION, SOW_SQS_QUEUE_URL, SOW_AWS_ACCESS_KEY_ID, SOW_AWS_SECRET_ACCESS_KEY`

### 8. `webapp/src/test/lib/r2/client.test.ts` — update all env var references

**`mockConfig` object (lines 29-35):**

- `accountId: "test-account"` → `endpointUrl: "https://test-account.r2.cloudflarestorage.com"`
- This affects **all 15+ constructor test cases** (lines 43, 53, 66, 80, 90, 100, 117, 133, 149, 165, 181, 196, 206, 216), not just the `createR2ClientFromEnv()` tests at the bottom.

**`createR2ClientFromEnv()` tests (lines 269-309):**

- `process.env.R2_ACCOUNT_ID` → `process.env.SOW_R2_ENDPOINT_URL` (lines 270, 279, 288, 296, 304)
- `process.env.R2_ACCESS_KEY_ID` → `process.env.SOW_R2_ACCESS_KEY_ID`
- `process.env.R2_SECRET_ACCESS_KEY` → `process.env.SOW_R2_SECRET_ACCESS_KEY`
- `process.env.R2_BUCKET_NAME` → `process.env.SOW_R2_BUCKET`
- Test descriptions: `"throws when R2_ACCOUNT_ID is missing"` → `"throws when SOW_R2_ENDPOINT_URL is missing"`, etc.

### 9. `webapp/src/test/lib/sqs/client.test.ts` — update all env var references

- `process.env.AWS_REGION` → `process.env.SOW_AWS_REGION`
- `process.env.SQS_QUEUE_URL` → `process.env.SOW_SQS_QUEUE_URL`
- `process.env.AWS_ACCESS_KEY_ID` → `process.env.SOW_AWS_ACCESS_KEY_ID`
- `process.env.AWS_SECRET_ACCESS_KEY` → `process.env.SOW_AWS_SECRET_ACCESS_KEY`
- `process.env.SQS_ENDPOINT_URL` → `process.env.SOW_SQS_ENDPOINT_URL`
- Error message assertions: update to match new var names

### 10. `webapp/src/test/deployment/deployment.test.ts` — update all `.toContain()` assertions

- `"DATABASE_URL="` → `"SOW_DATABASE_URL="`
- `"R2_ACCOUNT_ID="` → `"SOW_R2_ENDPOINT_URL="`
- `"R2_ACCESS_KEY_ID="` → `"SOW_R2_ACCESS_KEY_ID="`
- `"R2_SECRET_ACCESS_KEY="` → `"SOW_R2_SECRET_ACCESS_KEY="`
- `"R2_BUCKET_NAME="` → `"SOW_R2_BUCKET="`
- `"R2_PUBLIC_DOMAIN="` → `"NEXT_PUBLIC_R2_PUBLIC_DOMAIN="` (bug fix)
- `"AWS_REGION="` → `"SOW_AWS_REGION="`
- `"SQS_QUEUE_URL="` → `"SOW_SQS_QUEUE_URL="`
- `"AWS_ACCESS_KEY_ID="` → `"SOW_AWS_ACCESS_KEY_ID="`
- `"AWS_SECRET_ACCESS_KEY="` → `"SOW_AWS_SECRET_ACCESS_KEY="`
- `"SQS_ENDPOINT_URL="` → `"SOW_SQS_ENDPOINT_URL="`
- Test descriptions: update to match new var names

### 11. `webapp/src/test/deployment/workflows.test.ts` — update secret references

- `"secrets.DATABASE_URL"` → `"secrets.SOW_DATABASE_URL"` (line 163)
- `"secrets.AWS_ACCESS_KEY_ID"` → `"secrets.SOW_AWS_ACCESS_KEY_ID"` (line 179)
- `"secrets.AWS_SECRET_ACCESS_KEY"` → `"secrets.SOW_AWS_SECRET_ACCESS_KEY"` (line 187)
- Test descriptions: update to match

### 12. `.github/workflows/deploy.yml` — update GitHub Actions secret references

- Line 74: `secrets.DATABASE_URL` → `secrets.SOW_DATABASE_URL`
- Lines 76-77: `$DATABASE_URL` → `$SOW_DATABASE_URL` (shell variable references)
- Line 104: `secrets.AWS_ACCESS_KEY_ID` → `secrets.SOW_AWS_ACCESS_KEY_ID`
- Line 105: `secrets.AWS_SECRET_ACCESS_KEY` → `secrets.SOW_AWS_SECRET_ACCESS_KEY`

## Risks & Deployment Notes

### Value Format Migration for `R2_ACCOUNT_ID` → `SOW_R2_ENDPOINT_URL`

This is a **value format change**, not just a rename. Developers must convert:

```
# Old
R2_ACCOUNT_ID=abc123

# New
SOW_R2_ENDPOINT_URL=https://abc123.r2.cloudflarestorage.com
```

This is easy to miss during manual `.env.local` updates. The `.env.example` change documents the new format.

### AWS SDK Default Credential Chain

After renaming `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` to `SOW_`-prefixed versions, the AWS SDK default credential chain won't auto-detect them from the environment. Current code is safe — `sqs/client.ts` explicitly passes credentials to the SQS client constructor. Future code must not rely on the default credential chain for these credentials.

### Deployment Ordering (Hard Cutover)

This is a hard cutover with no backward compatibility. Env vars must be updated **before** code deployment:

1. Update Vercel env vars (all environments: production, preview, development)
2. Update GitHub repository secrets
3. Merge & deploy code

Deploying code before env vars are updated will break the app immediately.

### Tight Coupling: `deployment.test.ts` ↔ `.env.production.example`

The deployment test reads `.env.production.example` and asserts exact variable name strings. Both files must be updated in the same commit — missing either causes test failures.

## Post-Implementation Verification

```bash
cd webapp && pnpm test
cd webapp && pnpm lint
```

## Out of Scope / Follow-up

- **GitHub Secrets**: The actual GitHub repository secrets (`DATABASE_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) must be renamed in the repo settings to match the new names (`SOW_DATABASE_URL`, `SOW_AWS_ACCESS_KEY_ID`, `SOW_AWS_SECRET_ACCESS_KEY`). This is a manual step.
- **Vercel Environment Variables**: All env vars in the Vercel project must be updated to the new names. This is a manual step in the Vercel dashboard.
- **Local `.env.local` files**: Developers must update their local env files. The `.env.example` change will guide them.
- **Local `.env.local` value migration**: Developers must convert `R2_ACCOUNT_ID=abc123` to `SOW_R2_ENDPOINT_URL=https://abc123.r2.cloudflarestorage.com` — not just rename the key, but also change the value format from account ID to full endpoint URL.
