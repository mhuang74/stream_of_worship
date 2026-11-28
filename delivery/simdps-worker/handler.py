"""Run a single pinned SimDPS-Multi sampler and publish its immutable seam."""

import hashlib
import json
import os
import re
from io import BytesIO
from math import isfinite
from time import monotonic, time
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import UUID

import numpy as np
import soundfile as sf
import urllib3

from simdps_audio import (
    CHECKPOINT_REVISION,
    INPUT_CAP_BYTES,
    MODEL_ID,
    PRESIGN_SECONDS,
    SAMPLE_RATE,
    STALE_SECONDS,
    STEPS,
    UPSTREAM_REVISION,
    extract_fill,
    latent_window,
    search_candidates,
)

POOL = urllib3.PoolManager()
TRANSPORT_SECONDS = 30
SAFE_COMPONENT = re.compile(r"[A-Za-z0-9_-]{1,128}")
HEX_SHA = re.compile(r"[a-f0-9]{64}")


def _url_path(url, host, bucket, submission, name, headers):
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError("Invalid object URL")
    parts = urlsplit(url)
    path = unquote(parts.path).split("/")
    query = parse_qs(parts.query)
    lifetime = query.get("X-Amz-Expires", ())
    if (
        parts.scheme != "https"
        or parts.netloc != host
        or parts.fragment
        or len(path) != 7
        or path[:3] != ["", bucket, "glue-simdps-tmp"]
        or not all(SAFE_COMPONENT.fullmatch(value) for value in path[3:5])
        or path[5:] != [submission, name]
        or query.get("X-Amz-Algorithm") != ["AWS4-HMAC-SHA256"]
        or len(lifetime) != 1
        or not (lifetime[0].isascii() and lifetime[0].isdigit())
        or not 1 <= int(lifetime[0]) <= PRESIGN_SECONDS
        or set(query.get("X-Amz-SignedHeaders", [""])[0].split(";"))
        != {"host", *(key.lower() for key in headers)}
    ):
        raise ValueError("Invalid signed object location")
    return path[:-1]


def validate(payload, host, bucket):
    if type(payload.get("version")) is not int or payload["version"] != 1:
        raise ValueError("Unsupported contract version")
    submission = payload.get("submission_id")
    if not isinstance(submission, str) or str(UUID(submission)) != submission:
        raise ValueError("Invalid submission ID")
    submitted = payload.get("submitted_epoch")
    if submitted is not None:
        # Workers built before this contract sign no submission time; only a
        # stamped payload can be checked, and an unstamped one stays acceptable.
        if type(submitted) not in (int, float) or not isfinite(submitted):
            raise ValueError("Invalid submission time")
        if time() - submitted > STALE_SECONDS:
            raise ValueError("Input submission is stale")
    limits = {
        "a_frames": SAMPLE_RATE * 12,
        "fill_frames": SAMPLE_RATE * 15,
        "b_frames": SAMPLE_RATE * 12,
        "guide_frames": SAMPLE_RATE * 18,
    }
    for name, limit in limits.items():
        value = payload.get(name)
        if type(value) is not int or not 1 <= value <= limit:
            raise ValueError("Invalid audio dimensions")
    seed = payload.get("seed")
    if type(seed) is not int or not 0 <= seed <= 2**63 - 1:
        raise ValueError("Invalid seed")
    a, fill, b, guide = (payload[name] for name in limits)
    window = latent_window(a, fill, b)
    if not search_candidates(guide, window):
        raise ValueError("Insufficient search source")
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(4 * (a + fill + b)),
        "If-None-Match": "*",
        "x-amz-meta-submission-id": submission,
        "x-amz-meta-model": MODEL_ID,
        "x-amz-meta-upstream-revision": UPSTREAM_REVISION,
        "x-amz-meta-checkpoint-revision": CHECKPOINT_REVISION,
    }
    if payload.get("result_headers") != headers:
        raise ValueError("Invalid result contract")
    path = _url_path(payload.get("result_url"), host, bucket, submission, "seam.pcm", headers)
    for name in ("montage", "guide"):
        url_path = _url_path(
            payload.get(f"{name}_url"),
            host,
            bucket,
            submission,
            f"{name}.flac",
            {},
        )
        digest = payload.get(f"{name}_sha256")
        if url_path != path or not isinstance(digest, str) or not HEX_SHA.fullmatch(digest):
            raise ValueError("Invalid input contract")
    return a, fill, b, guide


def download(payload, name, *, frames, subtype, dtype):
    deadline = monotonic() + TRANSPORT_SECONDS
    response = POOL.request(
        "GET",
        payload[f"{name}_url"],
        retries=False,
        redirect=False,
        timeout=urllib3.Timeout(connect=3, read=5, total=TRANSPORT_SECONDS),
        preload_content=False,
    )
    try:
        size = int(response.headers.get("Content-Length", "0"))
        if response.status != 200 or not 0 < size <= INPUT_CAP_BYTES:
            raise ValueError("Input download rejected")
        data = bytearray()
        while len(data) <= size:
            if monotonic() >= deadline:
                raise ValueError("Input download timed out")
            chunk = response.read1(min(65536, size + 1 - len(data)), decode_content=False)
            if not chunk:
                break
            data.extend(chunk)
        if len(data) != size or hashlib.sha256(data).hexdigest() != payload[f"{name}_sha256"]:
            raise ValueError("Invalid input hash or size")
    finally:
        response.close()
        response.release_conn()
    with sf.SoundFile(BytesIO(data)) as audio:
        if (audio.format, audio.samplerate, audio.channels, audio.subtype, len(audio)) != (
            "FLAC",
            SAMPLE_RATE,
            2,
            subtype,
            frames,
        ):
            raise ValueError("Unexpected FLAC format or dimensions")
        return audio.read(dtype=dtype, always_2d=True)


def write_seam(payload, montage, fill, a_end, b_start):
    # Clip after rounding to handle positive full scale without PCM16 wrap.
    pcm = np.clip(np.rint(fill.astype(np.float64) * 32768), -32768, 32767).astype("<i2")
    seam = np.concatenate((montage[:a_end], pcm, montage[b_start:])).astype("<i2").tobytes()
    response = POOL.request(
        "PUT",
        payload["result_url"],
        body=seam,
        headers=payload["result_headers"],
        retries=False,
        redirect=False,
        timeout=urllib3.Timeout(connect=3, read=10, total=13),
        preload_content=False,
    )
    try:
        if response.status not in (200, 201, 204, 412):
            raise ValueError("Result storage rejected")
    finally:
        response.close()
        response.release_conn()


def make_handler(inpainter, torch, load_seconds, host, bucket):
    def handler(job):
        started = monotonic()
        try:
            payload = job["input"]
            a, fill, b, guide_frames = validate(payload, host, bucket)
            montage = download(
                payload, "montage", frames=a + fill + b, subtype="PCM_16", dtype="int16"
            )
            guide = download(
                payload, "guide", frames=guide_frames, subtype="PCM_24", dtype="float32"
            )
            gap = (a, a + fill)
            if np.any(montage[a : a + fill]):
                raise ValueError("Expected a silent montage gap")
            sampling_started = monotonic()
            result = inpainter.inpaint(
                torch.from_numpy(montage.T.astype(np.float32) / 32768),
                SAMPLE_RATE,
                tuple(frame / SAMPLE_RATE for frame in gap),
                source=torch.from_numpy(guide.T.copy()),
                method="multi",
                raw=True,
                seed=payload["seed"],
                steps=STEPS,
            )
            sampling = monotonic() - sampling_started
            generated = extract_fill(
                result.audio.detach().cpu().numpy(),
                result.sample_rate,
                result.window,
                result.gap,
                gap,
            )
            write_seam(payload, montage, generated, *gap)
        except Exception as exc:
            # SDK reports exceptions to Runpod. Transport exceptions can include signed URLs.
            raise RuntimeError(f"SimDPS job failed ({type(exc).__name__})") from None
        output = {
            "version": 1,
            "submission_id": payload["submission_id"],
            "model_id": MODEL_ID,
            "upstream_revision": UPSTREAM_REVISION,
            "checkpoint_revision": CHECKPOINT_REVISION,
            "seed": payload["seed"],
            "steps": STEPS,
            "gpu": torch.cuda.get_device_name(),
            "model_load_seconds": load_seconds,
            "sampling_seconds": sampling,
            "processing_seconds": monotonic() - started,
            "a_frames": a,
            "fill_frames": fill,
            "b_frames": b,
        }
        dispatched = payload.get("submitted_epoch")
        if type(dispatched) in (int, float) and 0 <= time() - dispatched <= 3600:
            output["dispatch_to_processing_seconds"] = max(
                0, time() - dispatched - output["processing_seconds"]
            )
        print(json.dumps({"event": "glue.sampler.completed", **output}), flush=True)
        return output

    return handler


def load_sampler():
    import torch
    from simdps import Inpainter
    from simdps.priors import ACEStep

    started = monotonic()
    prior = ACEStep(
        model="base", device="cuda:0", dtype=torch.bfloat16, cache_dir=os.environ["HF_HUB_CACHE"]
    )
    inpainter = Inpainter(prior, cache_dir=None)
    return inpainter, torch, monotonic() - started


def configured_handler(inpainter, torch, elapsed):
    host = os.environ["SIMDPS_R2_HOST"]
    bucket = os.environ["SIMDPS_R2_BUCKET"]
    if not re.fullmatch(r"[a-f0-9]{32}\.r2\.cloudflarestorage\.com", host):
        raise RuntimeError("Invalid SIMDPS_R2_HOST")
    return make_handler(inpainter, torch, elapsed, host, bucket)


def main():
    import sys

    inpainter, torch, elapsed = load_sampler()
    if sys.argv[1:] == ["--check-model"]:
        print(f"Loaded {MODEL_ID} offline on {torch.cuda.get_device_name()} in {elapsed:.3f}s")
        return

    import runpod

    runpod.serverless.start({"handler": configured_handler(inpainter, torch, elapsed)})


if __name__ == "__main__":
    main()
