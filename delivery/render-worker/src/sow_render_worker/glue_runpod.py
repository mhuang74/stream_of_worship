"""Queue transport and durable R2 handoff for the SimDPS Glue provider."""

import hashlib
import json
import os
import random
import re
import time
from contextlib import closing
from io import BytesIO
from pathlib import Path
from math import isfinite
from uuid import uuid4

import numpy as np
import soundfile as sf
import urllib3
from botocore.exceptions import ClientError

from sow_render_worker import db
from sow_render_worker import glue_jobs as spine
from sow_render_worker.glue_descriptor import milliseconds_to_frames
from sow_render_worker.glue_excerpts import (
    GlueAudioUnavailable,
    GlueExcerptTooShort,
    build_guide,
)
from sow_render_worker.glue_provider import (
    MAX_RESPONSE_BYTES,
    R2RawResponseStore,
    generation_duration_ms,
    raw_response_key,
)
from sow_render_worker.glue_staging import set_read_timeout, validate_generated_level
from sow_render_worker.r2_client import create_r2_client_from_env
from sow_render_worker.simdps_audio import (
    CHECKPOINT_REVISION,
    INPUT_CAP_BYTES,
    MODEL_ID,
    SAMPLE_RATE,
    STEPS,
    UPSTREAM_REVISION,
    latent_window,
    search_candidates,
)

API_SECONDS = 360
OUTCOME_SECONDS = 60
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}")
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}
JSON_BYTES = 64 * 1024
LOOKUP_SECONDS = 10
CANCEL_SECONDS = 10


class InvalidResult(Exception):
    """The durable object failed the seam contract."""


class OwnershipLost(Exception):
    """An active attempt is no longer owned by this delivery."""


def remaining(lambda_context):
    ms = spine._remaining_ms(lambda_context)
    return float("inf") if ms is None else ms / 1000


def preparation_time(lambda_context):
    available = remaining(lambda_context) - API_SECONDS - OUTCOME_SECONDS
    if available < 1:
        raise spine.GlueJobError(
            spine.GLUE_EXECUTION_DEADLINE, "free", "Insufficient time to prepare SimDPS inputs"
        )
    return min(60, available)


def check_config():
    endpoint = os.environ.get("RUNPOD_ENDPOINT_ID", "")
    if not IDENTIFIER.fullmatch(endpoint) or not os.environ.get("RUNPOD_API_KEY"):
        raise spine.GlueJobError(
            spine.GLUE_NOT_CONFIGURED, "free", "Runpod endpoint or API key is not configured"
        )
    try:
        return endpoint, create_r2_client_from_env(bounded=True)
    except ValueError:
        raise spine.GlueJobError(
            spine.GLUE_NOT_CONFIGURED, "free", "SimDPS R2 is not configured"
        ) from None


def _missing(exc):
    return exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}


def _read_chunk(body, size, deadline):
    available = deadline - time.monotonic()
    if available <= 0:
        raise TimeoutError("SimDPS R2 read timed out")
    set_read_timeout(body, min(5, available))
    return body.read(size)


def _read_object(r2, key, cap, deadline, *, exact=None):
    if deadline <= time.monotonic():
        raise TimeoutError("SimDPS R2 lookup deadline reached")
    try:
        response = r2.client.get_object(Bucket=r2.bucket_name, Key=key)
    except ClientError as exc:
        if _missing(exc):
            return None
        raise
    with closing(response["Body"]) as body:
        size = response["ContentLength"]
        if size <= 0 or size > cap or (exact is not None and size != exact):
            raise InvalidResult
        data = bytearray()
        while len(data) < size:
            chunk = _read_chunk(body, min(65536, size - len(data)), deadline)
            if not chunk:
                break
            data.extend(chunk)
        if len(data) != size:
            raise InvalidResult
    return bytes(data), response.get("Metadata", {})


def download_catalog_audio(r2, key, path, deadline):
    """Fetch a catalog asset directly so signed URLs stay out of exceptions."""
    if deadline <= time.monotonic():
        raise TimeoutError("SimDPS catalog download deadline reached")
    try:
        response = r2.client.get_object(Bucket=r2.bucket_name, Key=key)
    except ClientError as exc:
        if _missing(exc):
            return None
        raise
    partial = path.with_suffix(".part")
    try:
        with closing(response["Body"]) as body, partial.open("wb") as output:
            remaining_bytes = response["ContentLength"]
            if not 0 < remaining_bytes <= 256 * 1024 * 1024:
                raise ValueError("Catalog audio size exceeds the preparation limit")
            while remaining_bytes:
                chunk = _read_chunk(body, min(65536, remaining_bytes), deadline)
                if not chunk:
                    raise OSError("Catalog audio download was incomplete")
                output.write(chunk)
                remaining_bytes -= len(chunk)
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    return path


class PreparationAssets:
    """Use the existing excerpt builder with bounded catalog reads."""

    def __init__(self, r2, work_dir, lambda_context, *, time_left=None):
        self.r2 = r2
        self.work_dir = work_dir
        self.lambda_context = lambda_context
        self.time_left = time_left or (lambda: preparation_time(lambda_context))

    def download_audio(self, prefix):
        path = self.work_dir / f"{prefix}.mp3"
        if path.exists():
            return path
        return download_catalog_audio(
            self.r2,
            f"{prefix}/audio.mp3",
            path,
            time.monotonic() + self.time_left(),
        )


def encode_flac(samples, subtype):
    output = BytesIO()
    sf.write(output, samples, SAMPLE_RATE, format="FLAC", subtype=subtype)
    data = output.getvalue()
    if len(data) > INPUT_CAP_BYTES:
        raise ValueError("SimDPS FLAC exceeds input limit")
    return data


def prepare_audio(job, inputs, a, b, r2, work_dir, assets, lambda_context, *, time_left=None):
    time_left = time_left or (lambda: preparation_time(lambda_context))
    a_pcm, b_pcm = (
        sf.read(excerpt.wav_path, dtype="int16", always_2d=True)[0] for excerpt in (a, b)
    )
    fill = milliseconds_to_frames(
        generation_duration_ms(inputs.descriptor["beats"], inputs.descriptor["toTempoBpm"])
    )
    try:
        window = latent_window(len(a_pcm), fill, len(b_pcm))
    except ValueError:
        return None, "unsupported-window"

    prefix = inputs.to_item["recording_hash_prefix"]
    stem = download_catalog_audio(
        r2,
        f"{prefix}/stems/instrumental.flac",
        work_dir / "incoming-stem.flac",
        time.monotonic() + time_left(),
    )
    guide = None
    search_source = "instrumental_stem"
    if stem is not None:
        try:
            guide = build_guide(
                stem, inputs.to_steps, work_dir, timeout=time_left()
            )
        except GlueExcerptTooShort:
            pass
    if guide is None or not search_candidates(len(guide), window):
        search_source = "incoming_mix"
        mix = assets.download_audio(prefix)
        if mix is None:
            raise GlueAudioUnavailable("Incoming recording is unavailable", side="B")
        try:
            guide = build_guide(
                Path(mix), inputs.to_steps, work_dir, timeout=time_left()
            )
        except GlueExcerptTooShort:
            return None, "insufficient-search-source"
    if not search_candidates(len(guide), window):
        return None, "insufficient-search-source"
    montage = np.concatenate((a_pcm, np.zeros((fill, 2), dtype=np.int16), b_pcm))
    return (
        encode_flac(montage, "PCM_16"),
        encode_flac(guide, "PCM_24"),
        {
            "a_frames": len(a_pcm),
            "fill_frames": fill,
            "b_frames": len(b_pcm),
            "guide_frames": len(guide),
            "seed": job.seed,
            "search_source": search_source,
        },
    ), None


def result_metadata(route):
    return {
        "submission-id": route["submission_id"],
        "model": route["model"],
        "upstream-revision": route["upstream_revision"],
        "checkpoint-revision": route["checkpoint_revision"],
    }


def seam_bytes(route):
    return 4 * sum(route[field] for field in ("a_frames", "fill_frames", "b_frames"))


def stage_inputs(r2, endpoint, prefix, audio, *, check_time=lambda: None):
    montage, guide, geometry = audio
    submission = str(uuid4())
    route = {
        "version": 1,
        "provider": "simdps",
        "model": MODEL_ID,
        "steps": STEPS,
        "upstream_revision": UPSTREAM_REVISION,
        "checkpoint_revision": CHECKPOINT_REVISION,
        "endpoint_id": endpoint,
        "submission_id": submission,
        **geometry,
    }
    request = {
        key: route[key]
        for key in (
            "version",
            "submission_id",
            "seed",
            "a_frames",
            "fill_frames",
            "b_frames",
            "guide_frames",
        )
    }
    for name, data in (("montage", montage), ("guide", guide)):
        key = f"{prefix}/{submission}/{name}.flac"
        digest = hashlib.sha256(data).hexdigest()
        route[f"{name}_key"] = key
        route[f"{name}_sha256"] = request[f"{name}_sha256"] = digest
        check_time()
        r2.client.put_object(
            Bucket=r2.bucket_name,
            Key=key,
            Body=data,
            IfNoneMatch="*",
            ContentType="audio/flac",
            CacheControl="private, max-age=0",
        )
    return route, request


def sign_request(r2, prefix, route, request):
    for name in ("montage", "guide"):
        request[f"{name}_url"] = r2.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": r2.bucket_name, "Key": route[f"{name}_key"]},
            ExpiresIn=600,
            HttpMethod="GET",
        )
    route["result_key"] = f"{prefix}/{route['submission_id']}/seam.pcm"
    metadata = result_metadata(route)
    length = seam_bytes(route)
    request["result_headers"] = {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(length),
        "If-None-Match": "*",
        **{f"x-amz-meta-{key}": value for key, value in metadata.items()},
    }
    request["result_url"] = r2.client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": r2.bucket_name,
            "Key": route["result_key"],
            "ContentLength": length,
            "ContentType": "application/octet-stream",
            "IfNoneMatch": "*",
            "Metadata": metadata,
        },
        ExpiresIn=600,
        HttpMethod="PUT",
    )
    return {"input": request, "policy": {"executionTimeout": 180000, "ttl": 330000}}


class RunpodAPI:
    def __init__(self, endpoint, token, pool=None):
        self.url = f"https://api.runpod.ai/v2/{endpoint}"
        self.token = token
        self.pool = pool or urllib3.PoolManager()

    def call(self, method, operation, deadline, payload=None):
        available = deadline - time.monotonic()
        if available <= 0 or not self.token:
            return None, {}
        response = None
        try:
            response = self.pool.request(
                method,
                f"{self.url}/{operation}",
                body=payload,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                timeout=urllib3.Timeout(
                    total=available, connect=min(3, available), read=min(10, available)
                ),
                preload_content=False,
                retries=False,
                redirect=False,
            )
            if not 200 <= response.status < 300:
                return response.status, {}
            data = bytearray()
            while len(data) <= JSON_BYTES and time.monotonic() < deadline:
                sock = getattr(getattr(response, "connection", None), "sock", None)
                if sock is not None:
                    sock.settimeout(max(0.001, min(10, deadline - time.monotonic())))
                chunk = response.read1(min(4096, JSON_BYTES + 1 - len(data)), decode_content=False)
                if not chunk:
                    parsed = json.loads(data)
                    return response.status, parsed if isinstance(parsed, dict) else {}
                data.extend(chunk)
        except (urllib3.exceptions.HTTPError, OSError, ValueError):
            pass
        finally:
            if response is not None:
                response.close()
                response.release_conn()
        return None, {}


def _verify_result(r2, route, deadline, stored):
    data, metadata = stored
    if metadata != result_metadata(route):
        raise InvalidResult
    montage = _read_object(r2, route["montage_key"], INPUT_CAP_BYTES, deadline)
    if montage is None or hashlib.sha256(montage[0]).hexdigest() != route["montage_sha256"]:
        raise InvalidResult
    try:
        with sf.SoundFile(BytesIO(montage[0])) as source:
            if (source.format, source.subtype, source.samplerate, source.channels, len(source)) != (
                "FLAC",
                "PCM_16",
                SAMPLE_RATE,
                2,
                seam_bytes(route) // 4,
            ):
                raise InvalidResult
            pcm = source.read(dtype="int16", always_2d=True).astype("<i2").tobytes()
        a_end = route["a_frames"] * 4
        b_start = a_end + route["fill_frames"] * 4
        if data[:a_end] != pcm[:a_end] or data[b_start:] != pcm[b_start:]:
            raise InvalidResult
        fill = np.frombuffer(data[a_end:b_start], dtype="<i2").reshape(-1, 2)
        validate_generated_level(fill)
    except (spine.GlueJobError, ValueError, RuntimeError):
        raise InvalidResult from None
    return data


def wait_for_result(api, r2, route, deadline, *, heartbeat, log, recovering=False):
    remote_id = route.get("runpod_job_id")
    status = None
    lookup_error = False
    size = seam_bytes(route)
    if not 0 < size <= MAX_RESPONSE_BYTES:
        raise InvalidResult

    def lookup(until):
        nonlocal lookup_error
        try:
            found = _read_object(r2, route["result_key"], MAX_RESPONSE_BYTES, until, exact=size)
        except InvalidResult:
            raise
        except Exception:
            lookup_error = True
            return None
        lookup_error = False
        return found

    # Redelivery after the stored deadline gets one independent durability check.
    final_deadline = deadline
    if recovering and deadline <= time.monotonic():
        final_deadline = time.monotonic() + LOOKUP_SECONDS
    poll_end = deadline - LOOKUP_SECONDS - (CANCEL_SECONDS if remote_id else 0)
    if recovering and time.monotonic() < poll_end:
        found = lookup(poll_end)
        if found:
            return found
    while time.monotonic() < poll_end:
        heartbeat()
        if remote_id and status not in TERMINAL and time.monotonic() < poll_end - 1:
            _, response = api.call(
                "GET", f"status/{remote_id}", min(poll_end, time.monotonic() + 10)
            )
            received = response.get("status")
            if isinstance(received, str):
                status = received
            if status in TERMINAL:
                output = response.get("output")
                timings = {}
                if isinstance(output, dict):
                    timings = {
                        key: output[key]
                        for key in ("model_load_seconds", "sampling_seconds", "processing_seconds")
                        if type(output.get(key)) in (int, float)
                        and 0 <= output[key] <= 3600
                        and isfinite(output[key])
                    }
                log("glue.runpod.terminal", status=status, **timings)
        found = lookup(deadline if status in TERMINAL else poll_end)
        if found:
            return found
        if status in TERMINAL and not lookup_error:
            raise spine.GlueJobError(
                spine.GLUE_WORKER_FAILED, "paid", "Runpod finished without valid audio"
            )
        time.sleep(max(0, min(random.uniform(1, 5), poll_end - time.monotonic())))
    if remote_id and status not in TERMINAL and time.monotonic() < deadline - LOOKUP_SECONDS:
        api.call("POST", f"cancel/{remote_id}", deadline - LOOKUP_SECONDS)
    found = lookup(final_deadline)
    if found:
        return found
    if lookup_error:
        raise RuntimeError("SimDPS result lookup must be retried")
    if status in TERMINAL:
        raise spine.GlueJobError(
            spine.GLUE_WORKER_FAILED, "paid", "Runpod finished without valid audio"
        )
    raise spine.GlueJobError(
        spine.GLUE_PROVIDER_UNKNOWN, "paid", "Runpod outcome could not be verified"
    )


def finish_contacted(
    conn, job, route, r2, work_dir, lambda_context, log, *, payload=None, deadline=None
):
    """Only the caller that won the CAS may supply a payload."""
    api = RunpodAPI(route["endpoint_id"], os.environ.get("RUNPOD_API_KEY"))
    now = time.monotonic()
    if deadline is None:
        deadline = now + max(0, route["deadline_epoch"] - time.time())
    deadline = min(deadline, now + max(0, remaining(lambda_context) - OUTCOME_SECONDS))

    def heartbeat():
        if not db.update_runpod_contact(conn, job, route):
            raise OwnershipLost

    heartbeat()
    if payload is not None:
        status, reply = api.call("POST", "run", min(deadline, time.monotonic() + 13), payload)
        if status in {401, 403, 404, 400, 405, 413, 422}:
            code = (
                spine.GLUE_NOT_CONFIGURED if status in {401, 403, 404} else spine.GLUE_WORKER_FAILED
            )
            log("glue.runpod.rejected", level="error", providerStatus=status, errorCode=code)
            raise spine.GlueJobError(code, "none", "Runpod rejected the submission")
        remote_id = reply.get("id")
        if isinstance(remote_id, str) and IDENTIFIER.fullmatch(remote_id):
            route["runpod_job_id"] = remote_id
            heartbeat()

    try:
        stored = wait_for_result(
            api,
            r2,
            route,
            deadline,
            heartbeat=heartbeat,
            log=log,
            recovering=payload is None,
        )
        data = _verify_result(
            r2, route, time.monotonic() + min(20, remaining(lambda_context)), stored
        )
        path = work_dir / "seam.pcm"
        path.write_bytes(data)
        key = raw_response_key(job)
        heartbeat()
        if not spine._free_retry(
            lambda: R2RawResponseStore(r2).put_if_absent(key, path),
            label="promote_runpod_raw_response",
            log=log,
        ):
            raise InvalidResult
        if not spine._free_retry(
            lambda: db.update_runpod_contact(
                conn, job, route, raw=(key, hashlib.sha256(data).hexdigest(), len(data))
            ),
            label="record_runpod_raw_response",
            log=log,
        ):
            current = db.get_glue_job(conn, job.id, job.user_id)
            if current is not None and spine._has_durable_provider_success(current):
                # A lost DB acknowledgement can leave the old heartbeat in memory.
                # Redelivery claims the stored response before entering free staging.
                raise RuntimeError("SimDPS response is durable; retry staging delivery")
            raise OwnershipLost
    except InvalidResult:
        raise spine.GlueJobError(
            spine.GLUE_WORKER_FAILED, "paid", "Runpod audio failed verification"
        ) from None
