"""Production Modal entry point for the shared SimDPS image."""

import os
import re

import modal

# Modal re-imports this module in a container that already has the deployed image.
image = None
if modal.is_local():
    image_ref = os.environ.get("SIMDPS_IMAGE", "")
    if not re.fullmatch(r"[^@\s]+@sha256:[a-f0-9]{64}", image_ref):
        raise ValueError(
            "Set SIMDPS_IMAGE to an immutable image reference (<image>@sha256:<digest>)"
        )

    registry_secret_name = os.environ.get("SIMDPS_MODAL_REGISTRY_SECRET")
    image = modal.Image.from_registry(
        image_ref,
        secret=modal.Secret.from_name(registry_secret_name) if registry_secret_name else None,
        setup_dockerfile_commands=[
            "ENTRYPOINT []",
            "RUN uv pip install --python /opt/env/bin/python pip==25.3",
            "ENV PYTHONPATH=/worker",
        ],
    )

app = modal.App("sow-glue-simdps")


@app.cls(
    image=image,
    gpu="L4",
    min_containers=0,
    max_containers=2,
    timeout=180,
    retries=0,
    secrets=[modal.Secret.from_name("sow-glue-simdps-r2")],
)
class GlueSampler:
    @modal.enter()
    def load(self):
        from handler import configured_handler, load_sampler

        self.handle = configured_handler(*load_sampler())

    @modal.method()
    def sample(self, payload):
        return self.handle({"input": payload})
