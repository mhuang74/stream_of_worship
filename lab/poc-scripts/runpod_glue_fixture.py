"""Run an isolated fixture through the production Runpod transport and staging."""

import argparse
import json
import os
import re
import sys
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import soundfile as sf

from sow_render_worker.glue_runpod import (
    API_SECONDS,
    IDENTIFIER,
    InvalidResult,
    RunpodAPI,
    _verify_result,
    check_config,
    sign_request,
    stage_inputs,
    wait_for_result,
)
from sow_render_worker.glue_staging import ReferenceAudio, prepare_staged_take
from sow_render_worker.simdps_audio import SAMPLE_RATE


class FixtureAPI(RunpodAPI):
    """Collect bounded worker metadata from status replies for this submission."""

    def __init__(self, endpoint, token, route):
        super().__init__(endpoint, token)
        self.route = route
        self.diagnostics = {}

    def call(self, method, operation, deadline, payload=None):
        status, reply = super().call(method, operation, deadline, payload)
        job_id = self.route.get("runpod_job_id")
        output = reply.get("output")
        if (
            job_id
            and method == "GET"
            and operation == f"status/{job_id}"
            and reply.get("id", job_id) == job_id
            and reply.get("status") == "COMPLETED"
            and isinstance(output, dict)
            and output.get("submission_id") == self.route["submission_id"]
        ):
            gpu = output.get("gpu")
            if (
                isinstance(gpu, str)
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._()+/-]{0,95}", gpu)
                and self.token not in gpu
            ):
                self.diagnostics["gpu"] = gpu
            for key in ("model_load_seconds", "sampling_seconds", "processing_seconds"):
                value = output.get(key)
                if type(value) in (int, float) and 0 <= value <= 3600:
                    self.diagnostics[key] = value
        return status, reply

    def look_up_gpu(self):
        job_id = self.route.get("runpod_job_id")
        if job_id and "gpu" not in self.diagnostics:
            try:
                self.call("GET", f"status/{job_id}", time.monotonic() + 10)
            except Exception:
                # The audio is already verified and status is only diagnostic.
                pass


def fixture_error(code, message):
    # The caller consumes only the exit code. Direct invocations receive a safe explanation.
    print(message, file=sys.stderr)
    raise SystemExit(code)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("montage", type=Path)
    parser.add_argument("guide", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("a", type=int)
    parser.add_argument("end", type=int)
    parser.add_argument("seed", type=int)
    parser.add_argument("beats", type=int)
    parser.add_argument("tempo", type=float)
    parser.add_argument("search_source")
    args = parser.parse_args()
    catalog_bucket = os.environ.get("SOW_R2_BUCKET", "").strip().lower()
    scratch_bucket = os.environ.get("SIMDPS_POC_R2_BUCKET")
    if (
        not catalog_bucket
        or not scratch_bucket
        or scratch_bucket.strip().lower() == catalog_bucket
    ):
        fixture_error(20, "Set SIMDPS_POC_R2_BUCKET to a bucket distinct from SOW_R2_BUCKET.")
    os.environ["SOW_R2_BUCKET"] = scratch_bucket
    try:
        endpoint, r2 = check_config()
    except Exception:
        fixture_error(20, "Check RunPod endpoint and scratch R2 configuration.")

    prefix = f"glue-simdps-tmp/poc/{uuid4()}"
    try:
        montage = args.montage.read_bytes()
        guide = args.guide.read_bytes()
        frames = sf.info(BytesIO(montage)).frames
        route, request = stage_inputs(
            r2,
            endpoint,
            prefix,
            (
                montage,
                guide,
                {
                    "a_frames": args.a,
                    "fill_frames": args.end - args.a,
                    "b_frames": frames - args.end,
                    "guide_frames": sf.info(BytesIO(guide)).frames,
                    "seed": args.seed,
                    "search_source": args.search_source,
                },
            ),
        )
        payload = json.dumps(sign_request(r2, prefix, route, request)).encode()
    except Exception:
        fixture_error(21, "Check the input files and scratch R2 write access.")

    api = FixtureAPI(endpoint, os.environ["RUNPOD_API_KEY"], route)
    deadline = time.monotonic() + API_SECONDS
    status, reply = api.call("POST", "run", time.monotonic() + 13, payload)
    if status in {400, 401, 403, 404, 405, 413, 422}:
        fixture_error(22, f"RunPod rejected the request (HTTP {status}).")
    job_id = reply.get("id")
    if isinstance(job_id, str) and IDENTIFIER.fullmatch(job_id):
        route["runpod_job_id"] = job_id

    try:
        result = wait_for_result(
            api, r2, route, deadline, heartbeat=lambda: None, log=lambda *_, **__: None
        )
    except InvalidResult:
        fixture_error(24, "The RunPod R2 result failed verification.")
    except Exception:
        fixture_error(23, "RunPod finished without verified audio. Check the endpoint and R2.")
    try:
        raw = _verify_result(r2, route, time.monotonic() + 20, result)
    except Exception:
        fixture_error(24, "The RunPod R2 result failed verification.")
    fill = np.frombuffer(raw[4 * args.a : 4 * args.end], "<i2").reshape(-1, 2)
    sf.write(args.output, fill.astype(np.float32) / 32768, SAMPLE_RATE, subtype="FLOAT")
    api.look_up_gpu()
    diagnostics = api.diagnostics
    samples, _ = sf.read(BytesIO(montage), dtype="float32", always_2d=True)
    reference = ReferenceAudio(
        a=samples[: args.a].mean(axis=1),
        b=samples[args.end :].mean(axis=1),
        a_seconds=args.a / SAMPLE_RATE,
        b_seconds=(frames - args.end) / SAMPLE_RATE,
    )
    from sow_render_worker.glue_descriptor import milliseconds_to_frames
    from sow_render_worker.glue_provider import generation_duration_ms

    if route["fill_frames"] == milliseconds_to_frames(
        generation_duration_ms(args.beats, args.tempo)
    ):
        try:
            take = prepare_staged_take(
                raw,
                job=SimpleNamespace(from_item_id="A", to_item_id="B", provider_context_json=route),
                descriptor={"from": {}, "to": {}, "beats": args.beats, "toTempoBpm": args.tempo},
                items={},
                reference=reference,
            )
            args.output.with_suffix(".staged.wav").write_bytes(take.wav_bytes)
        except Exception:
            fixture_error(25, "RunPod production staging failed. Check the fill and reference audio.")
        diagnostics["staging_status"] = take.status
    print(json.dumps(diagnostics))


if __name__ == "__main__":
    main()
