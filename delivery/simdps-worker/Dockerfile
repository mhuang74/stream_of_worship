FROM nvidia/cuda:12.8.1-runtime-ubuntu22.04

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PROJECT_ENVIRONMENT=/opt/env \
    HF_HUB_CACHE=/models \
    PATH=/opt/env/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /worker
COPY delivery/simdps-worker/pyproject.toml delivery/simdps-worker/uv.lock ./
RUN uv sync --locked --no-dev --python 3.11 && rm -rf /root/.cache/uv
COPY delivery/simdps-worker/bake_weights.py ./
RUN python bake_weights.py

ENV HF_HUB_OFFLINE=1
COPY delivery/render-worker/src/sow_render_worker/simdps_audio.py ./
COPY delivery/simdps-worker/handler.py ./
ENTRYPOINT ["python", "handler.py"]
