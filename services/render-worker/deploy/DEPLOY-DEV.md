# Running the Render Worker in DEV Mode

Run the render worker as a local Docker container using the Lambda Runtime Interface Emulator (RIE). No AWS account, SQS queue, or Lambda function required — just Docker and an env file.

This is the fastest way to iterate on the render worker code. Source changes are reflected by restarting the container (no rebuild needed).

## Prerequisites

- Docker installed and running
- Environment file at `/opt/sow/.env` with all `SOW_*` variables set (see `.env.example`)

## Build & Start

```bash
cd services/render-worker
docker compose --env-file /opt/sow/.env -f docker-compose.dev.yml up --build
```

The container starts the Lambda RIE on port **9000**. You should see:

```
Lambda Runtime Interface Emulator listening on port 8080
```

## Buildx 403 Forbidden Error

If the build fails with:

```
failed to resolve source metadata for public.ecr.aws/lambda/python:3.11: 403 Forbidden
```

The `multiarch-builder` (docker-container buildx driver) has cached an expired auth token for `public.ecr.aws`. Public ECR allows anonymous pulls, but the stale token takes precedence and gets rejected.

**Fix** — Recreate the builder to clear stale credentials:

```bash
docker buildx rm multiarch-builder
docker buildx create --name multiarch-builder --use
```

Then retry the `docker compose up --build` command.

**Alternative** — Use the default builder (shares Docker daemon auth, no container isolation):

```bash
docker buildx build --builder default -f Dockerfile -t render-worker --load .
```

## Send a Test Event

Once the container is running, invoke the handler via the RIE endpoint:

```bash
curl -XPOST "http://localhost:9000/2015-03-31/functions/function/invocations" \
  -d '{
    "Records": [{
      "messageId": "test-1",
      "body": "{\"jobId\": \"your-job-id\", \"songsetId\": \"your-songset-id\", \"userId\": 1}"
    }]
  }'
```

Replace `your-job-id` and `your-songset-id` with valid IDs from your database.

## Live Code Reload

The dev compose file volume-mounts the source directory into the container:

```yaml
volumes:
  - ./src/sow_render_worker:/var/task/sow_render_worker
```

After editing source files under `src/sow_render_worker/`, restart the container to pick up changes (no rebuild needed):

```bash
docker compose --env-file /opt/sow/.env -f docker-compose.dev.yml restart
```

If you change `requirements.txt` or the `Dockerfile`, you must rebuild:

```bash
docker compose --env-file /opt/sow/.env -f docker-compose.dev.yml up --build
```

## REST Mode with Webapp

For end-to-end local development, pair the dev container with the Next.js webapp in REST mode:

1. **Terminal 1** — Start the render worker:
   ```bash
   cd services/render-worker
   docker compose --env-file /opt/sow/.env -f docker-compose.dev.yml up --build
   ```

2. **Terminal 2** — Start the webapp with REST mode:
   ```bash
   cd webapp
   SOW_RENDER_WORKER_MODE=rest pnpm dev
   ```

When `SOW_RENDER_WORKER_MODE=rest`, the webapp sends render jobs directly to the RIE endpoint instead of SQS. The browser polls the database for progress via SSE, just like in production.

**Note:** REST mode sends all jobs to a single container with no concurrency limit. For local development with a single user, this is fine.

## Stop

```bash
docker compose --env-file /opt/sow/.env -f docker-compose.dev.yml down
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `403 Forbidden` on `public.ecr.aws/lambda/python:3.11` | Recreate the buildx builder (see [Buildx 403 Forbidden Error](#buildx-403-forbidden-error)) |
| `port 9000 already in use` | Stop the existing container or change the port mapping in `docker-compose.dev.yml` |
| Missing env var errors at startup | Verify `/opt/sow/.env` contains all `SOW_*` variables (see `.env.example`) |
| Container exits with OOM | Increase Docker memory limit, or reduce resolution in the render job |
| `Connection refused` on RIE endpoint | Wait for the `Lambda Runtime Interface Emulator listening` log line before sending requests |
| Source changes not taking effect | Restart the container (`docker compose restart`), or rebuild if `requirements.txt` changed |
