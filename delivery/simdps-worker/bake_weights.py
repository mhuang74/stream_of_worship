"""Bake the complete pinned DiT weights and VAE used by the loader."""

import os
from pathlib import Path

from huggingface_hub import snapshot_download

cache_dir = os.environ["HF_HUB_CACHE"]
snapshot_download(
    "ACE-Step/acestep-v15-base",
    revision="e432212fec32b8965a14ffa57ae653438d6abd14",
    cache_dir=cache_dir,
)
snapshot_download(
    "ACE-Step/Ace-Step1.5",
    revision="19671f406d603126926c1b7e2adc169acbcade22",
    allow_patterns=["vae/*", "README.md", "LICENSE*"],
    cache_dir=cache_dir,
)
(Path(cache_dir).parent / "models-NOTICE").write_text(
    "ACE-Step model files are distributed under the terms in their pinned Hugging Face "
    "repositories. The SimDPS and vendored package licenses are installed with the package.\n"
)
