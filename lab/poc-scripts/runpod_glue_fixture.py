"""Run an isolated fixture through the production Runpod transport and staging."""

import argparse
import json
import os
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
    RunpodAPI,
    _verify_result,
    check_config,
    sign_request,
    stage_inputs,
    wait_for_result,
)
from sow_render_worker.glue_staging import ReferenceAudio, prepare_staged_take
from sow_render_worker.simdps_audio import SAMPLE_RATE


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
    endpoint, r2 = check_config()
    scratch_bucket = os.environ.get("SIMDPS_POC_R2_BUCKET")
    if not scratch_bucket or scratch_bucket != r2.bucket_name:
        raise SystemExit("Set SOW_R2_BUCKET to the dedicated SIMDPS_POC_R2_BUCKET")
    prefix = f"glue-simdps-tmp/poc/{uuid4()}"
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
    api = RunpodAPI(endpoint, os.environ["RUNPOD_API_KEY"])
    deadline = time.monotonic() + API_SECONDS
    status, reply = api.call("POST", "run", time.monotonic() + 13, payload)
    if status in {400, 401, 403, 404, 405, 413, 422}:
        raise SystemExit(f"Runpod rejected the request (HTTP {status})")
    job_id = reply.get("id")
    if isinstance(job_id, str) and IDENTIFIER.fullmatch(job_id):
        route["runpod_job_id"] = job_id

    diagnostics = {}

    def log(event, **fields):
        if event == "glue.runpod.terminal":
            diagnostics.update(fields)

    result = wait_for_result(api, r2, route, deadline, heartbeat=lambda: None, log=log)
    raw = _verify_result(r2, route, time.monotonic() + 20, result)
    fill = np.frombuffer(raw[4 * args.a : 4 * args.end], "<i2").reshape(-1, 2)
    sf.write(args.output, fill.astype(np.float32) / 32768, SAMPLE_RATE, subtype="FLOAT")
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
        take = prepare_staged_take(
            raw,
            job=SimpleNamespace(from_item_id="A", to_item_id="B", provider_context_json=route),
            descriptor={"from": {}, "to": {}, "beats": args.beats, "toTempoBpm": args.tempo},
            items={},
            reference=reference,
        )
        args.output.with_suffix(".staged.wav").write_bytes(take.wav_bytes)
        diagnostics["staging_status"] = take.status
    print(json.dumps(diagnostics))


if __name__ == "__main__":
    main()
