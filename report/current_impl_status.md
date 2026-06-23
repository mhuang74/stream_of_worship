# Current Implementation Status

## 2026-06-23

- Completed the ops/delivery/lab repository reorganization from `specs/ops-delivery-lab-reorganization-v2.md`.
- Active backend/admin code now lives under `ops/admin-cli`; shared DB/auth/schema helpers, including the former `stream_of_worship.app.db`, are owned by `stream_of_worship.db.app`.
- Analysis Service moved to `ops/analysis-service`; Web App moved to `delivery/webapp`; Render Worker moved to `delivery/render-worker`.
- Deprecated/experimental code moved under `lab/`: `lab/sow-app`, `lab/legacy-cli-tui`, and `lab/poc-scripts`.
- Root Python project metadata and root `uv.lock` were removed. Python subprojects now own their project metadata and lockfiles.

Canonical commands:

```bash
uv run --project ops/admin-cli --extra admin sow-admin --help
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest ops/admin-cli/tests -v
cd ops/analysis-service && docker compose up -d
pnpm --filter sow-webapp dev
cd delivery/webapp && pnpm dev
cd delivery/render-worker && docker compose up --build
uv run --project lab/sow-app sow-app --help
uv run --project lab/legacy-cli-tui stream-of-worship --help
uv run --project lab/poc-scripts python -c "from stream_of_worship.db.app.read_client import ReadOnlyClient; print('poc db ok')"
```
