import importlib.util
import json
import sys
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import numpy as np
import pytest
import urllib3
import soundfile as sf

from sow_render_worker import glue_runpod as provider
from sow_render_worker import simdps_audio
from sow_render_worker.glue_jobs import GlueJobError
from sow_render_worker.r2_client import R2Client


@pytest.mark.parametrize("status_result", ["matching", "wrong-submission", "unavailable"])
def test_poc_fixture_recovers_audio_when_status_follows_r2(
    tmp_path, monkeypatch, capsys, status_result
):
    path = Path(__file__).resolve().parents[3] / "lab/poc-scripts/runpod_glue_fixture.py"
    spec = importlib.util.spec_from_file_location("runpod_fixture", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)

    rate = simdps_audio.SAMPLE_RATE
    a_frames, fill_frames, b_frames = 6 * rate, 3 * rate, 6 * rate
    end = a_frames + fill_frames
    montage = np.zeros((end + b_frames, 2), dtype="<i2")
    montage[:a_frames] = 1024
    montage[end:] = 2048
    montage_path = tmp_path / "montage.flac"
    guide_path = tmp_path / "guide.flac"
    raw_path = tmp_path / "fill.wav"
    sf.write(montage_path, montage, rate)
    sf.write(guide_path, montage[end:], rate, subtype="PCM_24")
    monkeypatch.setattr(sys, "argv", [
        str(path), str(montage_path), str(guide_path), str(raw_path),
        str(a_frames), str(end), "0", "4", "86.13281", "incoming_mix",
    ])
    monkeypatch.setenv("RUNPOD_API_KEY", "private-key")
    monkeypatch.setenv("SOW_R2_BUCKET", "catalog")
    monkeypatch.setenv("SIMDPS_POC_R2_BUCKET", "scratch")
    r2 = R2Client("https://fake.r2.cloudflarestorage.com", "fake", "fake", "scratch", bounded=True)
    objects = {}

    def check_config():
        assert fixture.os.environ["SOW_R2_BUCKET"] == "scratch"
        return "endpoint", r2

    def put_object(*, Bucket, Key, Body, IfNoneMatch, **_):
        assert Bucket == "scratch" and IfNoneMatch == "*"
        assert Key.startswith("glue-simdps-tmp/poc/")
        objects[Key] = (Body, {})

    def get_object(*, Bucket, Key):
        assert Bucket == "scratch"
        data, metadata = objects[Key]
        return {"ContentLength": len(data), "Body": BytesIO(data), "Metadata": metadata}

    monkeypatch.setattr(fixture, "check_config", check_config)
    monkeypatch.setattr(r2.client, "put_object", put_object)
    monkeypatch.setattr(r2.client, "get_object", get_object)
    calls = []
    submission = None

    def call(self, method, operation, deadline, payload=None):
        nonlocal submission
        assert 0 < deadline - fixture.time.monotonic() <= (13 if method == "POST" else 10)
        calls.append((method, operation))
        if method == "POST":
            request = json.loads(payload)["input"]
            submission = request["submission_id"]
            metadata = {
                key.removeprefix("x-amz-meta-"): value
                for key, value in request["result_headers"].items()
                if key.startswith("x-amz-meta-")
            }
            result_key = unquote(urlsplit(request["result_url"]).path.removeprefix("/scratch/"))
            result = montage.copy()
            result[a_frames:end] = 4096
            objects[result_key] = (result.tobytes(), metadata)
            return 200, {"id": "job"}
        if calls.count(("GET", "status/job")) == 1:
            return 200, {"status": "IN_PROGRESS"}
        if status_result == "unavailable":
            raise OSError("https://signed-r2.invalid?X-Amz-Signature=secret")
        return 200, {
            "id": "job",
            "status": "COMPLETED",
            "output": {
                "submission_id": submission if status_result == "matching" else "someone-else",
                "gpu": "NVIDIA L4",
                "sampling_seconds": 11,
                "model_load_seconds": 3,
                "processing_seconds": 12,
                "untrusted_field": "private-key",
            },
        }

    monkeypatch.setattr(provider.RunpodAPI, "call", call)
    fixture.main()
    output = capsys.readouterr()
    diagnostics = json.loads(output.out)
    assert "private-key" not in output.out and "https://" not in output.err
    assert calls == [("POST", "run"), ("GET", "status/job"), ("GET", "status/job")]
    assert sf.info(raw_path).subtype == "FLOAT"
    np.testing.assert_array_equal(sf.read(raw_path, always_2d=True)[0], np.full((fill_frames, 2), 0.125))
    staged = sf.info(raw_path.with_suffix(".staged.wav"))
    assert (staged.frames, staged.channels, staged.samplerate) == (132300, 2, rate)
    assert "staging_status" in diagnostics
    if status_result == "matching":
        assert diagnostics["gpu"] == "NVIDIA L4"
        assert diagnostics["sampling_seconds"] == 11
        assert diagnostics["model_load_seconds"] == 3
        assert diagnostics["processing_seconds"] == 12
    else:
        assert set(diagnostics) == {"staging_status"}


@pytest.fixture
def worker_handler(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "simdps_audio", simdps_audio)
    path = Path(__file__).resolve().parents[2] / "simdps-worker" / "handler.py"
    spec = importlib.util.spec_from_file_location("runpod_handler", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lambda_presigning_matches_the_gpu_worker_put_contract(worker_handler):
    host = "a" * 32 + ".r2.cloudflarestorage.com"
    r2 = R2Client(f"https://{host}", "fake", "fake", "private", bounded=True)
    r2.client.put_object = lambda **_: None
    prefix = "glue-simdps-tmp/7/job"
    route, inputs = provider.stage_inputs(
        r2,
        "endpoint",
        prefix,
        (
            b"montage",
            b"guide",
            {
                "a_frames": 264600,
                "fill_frames": 132300,
                "b_frames": 264180,
                "guide_frames": 264180,
                "seed": 123,
            },
        ),
    )
    request = provider.sign_request(r2, prefix, route, inputs, window_seconds=300)
    assert request["policy"] == {"executionTimeout": 180000, "ttl": 540000}
    assert worker_handler.validate(request["input"], host, "private") == (
        264600,
        132300,
        264180,
        264180,
    )
    assert request["input"]["result_headers"]["Content-Length"] == "2644320"
    assert "https://" not in json.dumps(route)
    request["input"]["result_headers"]["If-None-Match"] = "other"
    with pytest.raises(ValueError, match="result contract"):
        worker_handler.validate(request["input"], host, "private")


def _signed_payload(host, prefix="glue-simdps-tmp/7/job"):
    r2 = R2Client(f"https://{host}", "fake", "fake", "private", bounded=True)
    r2.client.put_object = lambda **_: None
    route, inputs = provider.stage_inputs(
        r2,
        "endpoint",
        prefix,
        (
            b"montage",
            b"guide",
            {
                "a_frames": 264600,
                "fill_frames": 132300,
                "b_frames": 264180,
                "guide_frames": 264180,
                "seed": 123,
            },
        ),
    )
    return r2, route, provider.sign_request(r2, prefix, route, inputs, window_seconds=300)["input"]


def _resign(r2, route, payload, *, expires, field="montage_url", key="montage_key"):
    payload[field] = r2.client.generate_presigned_url(
        "get_object",
        Params={"Bucket": "private", "Key": route[key]},
        ExpiresIn=expires,
        HttpMethod="GET",
    )


def test_gpu_worker_accepts_the_signer_lifetime(worker_handler):
    host = "a" * 32 + ".r2.cloudflarestorage.com"
    _, _, payload = _signed_payload(host)
    assert worker_handler.validate(payload, host, "private") == (264600, 132300, 264180, 264180)


def test_gpu_worker_accepts_a_lifetime_inside_the_contract(worker_handler):
    host = "a" * 32 + ".r2.cloudflarestorage.com"
    r2, route, payload = _signed_payload(host)
    _resign(r2, route, payload, expires=120)
    assert worker_handler.validate(payload, host, "private") == (264600, 132300, 264180, 264180)


def test_gpu_worker_rejects_a_lifetime_beyond_the_contract(worker_handler):
    host = "a" * 32 + ".r2.cloudflarestorage.com"
    r2, route, payload = _signed_payload(host)
    _resign(r2, route, payload, expires=simdps_audio.PRESIGN_SECONDS + 1)
    with pytest.raises(ValueError, match="Invalid signed object location"):
        worker_handler.validate(payload, host, "private")


def test_gpu_worker_rejects_a_stale_submission(worker_handler):
    host = "a" * 32 + ".r2.cloudflarestorage.com"
    _, _, payload = _signed_payload(host)
    payload["submitted_epoch"] = time.time() - simdps_audio.STALE_SECONDS - 1
    with pytest.raises(ValueError, match="stale"):
        worker_handler.validate(payload, host, "private")
    payload.pop("submitted_epoch")
    assert worker_handler.validate(payload, host, "private") == (264600, 132300, 264180, 264180)


def test_handler_quantizes_only_the_fill_and_accepts_create_only_conflict(
    worker_handler, monkeypatch
):
    uploads = []

    def request(method, url, **kwargs):
        uploads.append((method, kwargs))
        return SimpleNamespace(status=412, close=lambda: None, release_conn=lambda: None)

    monkeypatch.setattr(worker_handler, "POOL", SimpleNamespace(request=request))
    montage = np.array([[-32768, 17], [0, 0], [99, 32767]], dtype="<i2")
    worker_handler.write_seam(
        {"result_url": "unused", "result_headers": {"If-None-Match": "*"}},
        montage,
        np.array([[1.0, -1.0]]),
        1,
        2,
    )
    [(method, options)] = uploads
    assert method == "PUT"
    assert options["headers"]["If-None-Match"] == "*"
    assert not options["retries"] and not options["redirect"]
    expected = np.array([[-32768, 17], [32767, -32768], [99, 32767]], dtype="<i2")
    assert options["body"] == expected.tobytes()


@pytest.mark.parametrize("terminal", sorted(provider.TERMINAL))
def test_terminal_status_recovers_existing_r2_bytes(terminal, monkeypatch):
    lookup = []
    route = dict(a_frames=2, fill_frames=2, b_frames=2, runpod_job_id="job", result_key="key")
    data = bytes(24)

    def get_object(**kwargs):
        lookup.append(kwargs)
        return {"ContentLength": len(data), "Body": BytesIO(data)}

    r2 = SimpleNamespace(client=SimpleNamespace(get_object=get_object), bucket_name="private")
    api = SimpleNamespace(call=lambda *args: (200, {"status": terminal}))
    result = provider.wait_for_result(
        api,
        r2,
        route,
        provider.time.monotonic() + 30,
        heartbeat=lambda: None,
        log=lambda *args, **kwargs: None,
    )
    assert result == (data, {})
    assert lookup == [{"Bucket": "private", "Key": "key"}]


def test_ambiguous_post_checks_r2_without_posting_again(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(provider.db, "update_runpod_contact", lambda *args, **kwargs: True)

    def call(self, method, operation, *args):
        calls.append((method, operation))
        return None, {}

    monkeypatch.setattr(provider.RunpodAPI, "call", call)
    route = dict(
        a_frames=2,
        fill_frames=2,
        b_frames=2,
        endpoint_id="endpoint",
        result_key="key",
        deadline_epoch=provider.time.time() - 1,
    )
    monkeypatch.setattr(provider, "_read_object", lambda *args, **kwargs: None)
    with pytest.raises(GlueJobError) as first:
        provider.finish_contacted(
            None,
            None,
            route,
            None,
            tmp_path,
            None,
            lambda *a: None,
            payload=b"request",
            deadline=provider.time.monotonic() + 1,
        )
    assert first.value.code == "GLUE_PROVIDER_UNKNOWN"
    with pytest.raises(GlueJobError) as recovery:
        provider.finish_contacted(None, None, route, None, tmp_path, None, lambda *a: None)
    assert recovery.value.retry_kind == "paid"
    assert calls == [("POST", "run")]


@pytest.mark.parametrize("failure", ["promotion", "metadata"])
def test_transient_storage_failure_recovers_same_submission(monkeypatch, tmp_path, failure):
    from hashlib import sha256

    calls = []
    persisted = []
    raw = b"\x11\x01" * 12
    route = dict(
        a_frames=2,
        fill_frames=2,
        b_frames=2,
        endpoint_id="endpoint",
        result_key="key",
        deadline_epoch=provider.time.time() - 1,
    )
    job = SimpleNamespace(id="job", user_id=7)
    crash_at = {failure}

    def api_call(self, method, operation, *_):
        calls.append((method, operation))
        return 200, {"id": "remote"}

    def promote(_, key, path):
        if "promotion" in crash_at:
            raise OSError("temporary R2 failure")
        persisted.append(path.read_bytes())
        return True

    def update(*args, raw=None):
        if raw:
            if "metadata" in crash_at:
                raise OSError("temporary DB failure")
            assert raw == ("glues/7/jobs/job/response.pcm", sha256(persisted[-1]).hexdigest(), 24)
        return True

    monkeypatch.setattr(provider.RunpodAPI, "call", api_call)
    monkeypatch.setattr(provider, "wait_for_result", lambda *args, **kwargs: (raw, {}))
    monkeypatch.setattr(provider, "_verify_result", lambda *args: raw)
    monkeypatch.setattr(provider.db, "update_runpod_contact", update)
    monkeypatch.setattr(provider.R2RawResponseStore, "__init__", lambda *args: None)
    monkeypatch.setattr(provider.R2RawResponseStore, "put_if_absent", promote)
    monkeypatch.setattr(provider.spine, "_sleep", lambda *_: None)

    with pytest.raises(OSError):
        provider.finish_contacted(
            None,
            job,
            route,
            None,
            tmp_path,
            None,
            lambda *a, **kwargs: None,
            payload=b"request",
        )
    crash_at.clear()
    provider.finish_contacted(None, job, route, None, tmp_path, None, lambda *a, **kwargs: None)
    assert calls == [("POST", "run")]
    assert persisted[-1] == raw


def test_lost_promotion_acknowledgement_leaves_sqs_recovery_available(monkeypatch, tmp_path):
    raw = b"\x11\x01" * 12
    route = dict(
        a_frames=2,
        fill_frames=2,
        b_frames=2,
        endpoint_id="endpoint",
        result_key="key",
        deadline_epoch=provider.time.time() - 1,
    )
    job = SimpleNamespace(id="job", user_id=7)
    durable = SimpleNamespace(
        raw_response_r2_key="glues/7/jobs/job/response.pcm",
        raw_response_sha256="a" * 64,
        raw_response_bytes=len(raw),
    )

    monkeypatch.setattr(provider, "wait_for_result", lambda *a, **k: (raw, {}))
    monkeypatch.setattr(provider, "_verify_result", lambda *args: raw)
    monkeypatch.setattr(provider.R2RawResponseStore, "__init__", lambda *args: None)
    monkeypatch.setattr(provider.R2RawResponseStore, "put_if_absent", lambda *args: True)
    monkeypatch.setattr(provider.db, "update_runpod_contact", lambda *a, raw=None: raw is None)
    monkeypatch.setattr(provider.db, "get_glue_job", lambda *args: durable)

    with pytest.raises(RuntimeError, match="response is durable; retry staging delivery"):
        provider.finish_contacted(None, job, route, None, tmp_path, None, lambda *a, **k: None)


class _FakeResponse:
    def __init__(self, status, chunks, *, sock=None):
        self.status = status
        self._chunks = list(chunks)
        self.connection = SimpleNamespace(sock=sock)
        self.closed = False
        self.released = False

    def read1(self, size, decode_content=False):
        return self._chunks.pop(0) if self._chunks else b""

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


class _FakePool:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    def request(self, method, url, **kwargs):
        if self.error is not None:
            raise self.error
        return self.response


def _call(response=None, error=None):
    pool = _FakePool(response, error)
    api = provider.RunpodAPI("endpoint", "token", pool=pool)
    return api.call("POST", "run", provider.time.monotonic() + 5, b"{}")


def test_runpod_api_parses_json_and_rejects_bad_bodies():
    socket = SimpleNamespace(settimeout=lambda value: None)
    ok = _FakeResponse(200, [b'{"id": "remote"}', b""], sock=socket)
    assert _call(ok) == (200, {"id": "remote"})
    assert (ok.closed, ok.released) == (True, True)

    failing = _FakeResponse(500, [b"internal error"])
    assert _call(failing) == (500, {})
    assert (failing.closed, failing.released) == (True, True)

    oversized = _FakeResponse(200, [b"x" * 4096] * 20)
    assert _call(oversized) == (None, {})
    assert oversized.closed

    assert _call(error=urllib3.exceptions.HTTPError("boom")) == (None, {})


def _frozen_r2():
    objects = {}

    def put_object(**kwargs):
        objects[kwargs["Key"]] = kwargs["Body"]

    def get_object(*, Bucket, Key):
        data = objects[Key]
        return {"ContentLength": len(data), "Body": BytesIO(data)}

    client = SimpleNamespace(
        put_object=put_object,
        get_object=get_object,
        generate_presigned_url=lambda command, Params, **_: Params["Key"],
    )
    return SimpleNamespace(client=client, bucket_name="private"), objects


def _signed_route():
    rate = provider.SAMPLE_RATE
    a_frames, fill_frames, b_frames = 4410, 4410, 4410
    montage = np.zeros((a_frames + fill_frames + b_frames, 2), dtype="<i2")
    montage[:a_frames] = 1024
    montage[a_frames + fill_frames :] = 2048
    guide = np.zeros((1920, 2), dtype="<i2")
    audio = (
        provider.encode_flac(montage, "PCM_16"),
        provider.encode_flac(guide, "PCM_24"),
        {
            "a_frames": a_frames,
            "fill_frames": fill_frames,
            "b_frames": b_frames,
            "guide_frames": len(guide),
            "seed": 4321,
            "search_source": "incoming_mix",
        },
    )
    r2, objects = _frozen_r2()
    prefix = "glue-simdps-tmp/7/job"
    route, request = provider.stage_inputs(r2, "endpoint", prefix, audio)
    provider.sign_request(r2, prefix, route, request, window_seconds=300)
    seam = montage.copy()
    seam[a_frames : a_frames + fill_frames] = 4096
    assert rate == 44100
    return r2, objects, route, seam.tobytes()


def test_verify_result_rejects_metadata_or_preserved_audio_mismatch():
    r2, objects, route, seam = _signed_route()
    deadline = provider.time.monotonic() + 5
    metadata = provider.result_metadata(route)

    with pytest.raises(provider.InvalidResult):
        provider._verify_result(
            r2, route, deadline, (seam, dict(metadata, **{"submission-id": "someone-else"}))
        )

    montage = objects[route["montage_key"]]
    objects[route["montage_key"]] = b"other montage bytes"
    with pytest.raises(provider.InvalidResult):
        provider._verify_result(r2, route, deadline, (seam, metadata))
    objects[route["montage_key"]] = montage

    corrupted = bytearray(seam)
    corrupted[-4:] = b"\x00\x01\x00\x01"
    with pytest.raises(provider.InvalidResult):
        provider._verify_result(r2, route, deadline, (bytes(corrupted), metadata))

    assert provider._verify_result(r2, route, deadline, (seam, metadata)) == seam


def test_prepare_audio_reports_unsupported_window(tmp_path):
    excerpts = []
    for name in ("a", "b"):
        path = tmp_path / f"{name}.wav"
        sf.write(path, np.zeros((64, 2), dtype="<i2"), provider.SAMPLE_RATE, subtype="PCM_16")
        excerpts.append(SimpleNamespace(wav_path=path))
    inputs = SimpleNamespace(
        descriptor={"beats": 4, "toTempoBpm": 120.0},
        to_item={"recording_hash_prefix": "a" * 16},
        to_steps=[[0, 2.0]],
    )
    assert provider.prepare_audio(
        SimpleNamespace(seed=4321),
        inputs,
        *excerpts,
        None,
        tmp_path,
        None,
        None,
        time_left=lambda: 60,
    ) == (None, "unsupported-window")


def test_prepare_audio_names_the_largest_supported_window(tmp_path, monkeypatch):
    frames = round(10 * provider.SAMPLE_RATE)
    excerpts = []
    for name in ("a", "b"):
        path = tmp_path / f"{name}.wav"
        sf.write(path, np.zeros((frames, 2), dtype="<i2"), provider.SAMPLE_RATE, subtype="PCM_16")
        excerpts.append(SimpleNamespace(wav_path=path))
    monkeypatch.setattr(provider, "download_catalog_audio", lambda *a, **k: tmp_path / "stem.flac")
    monkeypatch.setattr(
        provider, "build_guide", lambda *a, **k: np.zeros((frames, 2), dtype="<i2")
    )
    inputs = SimpleNamespace(
        descriptor={"beats": 16, "toTempoBpm": 70.0},
        to_item={"recording_hash_prefix": "a" * 16},
        to_steps=[[0, 2.0]],
    )
    # 16 beats at 70 BPM is a 13.7 s fill, which no 15 s latent window holds;
    # 8 beats (6.9 s) does, so the failure names the length that fits.
    assert provider.prepare_audio(
        SimpleNamespace(seed=4321),
        inputs,
        *excerpts,
        None,
        tmp_path,
        None,
        None,
        time_left=lambda: 60,
    ) == (None, "unsupported-window supportedBeats=8")


@pytest.mark.parametrize(
    ("window", "ttl"),
    [(0, 330000), (120, 360000), (180, 420000), (300, 540000)],
)
def test_ttl_covers_queue_time_without_raising_execution(window, ttl):
    assert provider.policy_for_window(window) == {
        "executionTimeout": 180000,
        "ttl": ttl,
    }
