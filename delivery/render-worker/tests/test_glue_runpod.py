import importlib.util
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from sow_render_worker import glue_runpod as provider
from sow_render_worker import simdps_audio
from sow_render_worker.glue_jobs import GlueJobError
from sow_render_worker.r2_client import R2Client


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
    request = provider.sign_request(r2, prefix, route, inputs)
    assert request["policy"] == {"executionTimeout": 180000, "ttl": 330000}
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
