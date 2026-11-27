"""Provider ordering and durable replay against a fake clock and object store."""

import copy
import hashlib
import json
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import psycopg2
import pytest
from botocore.exceptions import ClientError
from modal import exception as modal_error

from sow_render_worker import glue_cascade as cascade
from sow_render_worker import glue_excerpts
from sow_render_worker import glue_jobs as spine


class Interrupted(BaseException):
    pass


@pytest.fixture
def delivery(monkeypatch, tmp_path):
    now = 1000
    submissions = []
    cancelled = []
    objects = {}
    published = {}
    saved = SimpleNamespace(route=None, charged=0, raw=None)
    events = []
    montage_puts = 0
    state = SimpleNamespace(
        eleven="success",
        backend_ready={},
        modal_fails=False,
        modal_reject=False,
        remote_bad=False,
        runpod_status="IN_PROGRESS",
        lookup_fail=False,
        hedge_storage_fail=False,
        invalid=None,
        crash_at=None,
        deny=None,
        skip=False,
        ineligible=False,
        edited=False,
    )
    job = SimpleNamespace(
        id="job",
        user_id=123,
        attempt_id="attempt",
        seed=4321,
        songset_id="songs",
        from_item_id="a",
        to_item_id="b",
        phase="preparing",
        heartbeat_at="lease",
        provider_contacted=False,
        provider_context_json=None,
    )
    inputs = SimpleNamespace(descriptor={}, from_item={}, to_item={})
    geometry = {
        "a_frames": 16,
        "fill_frames": 16,
        "b_frames": 16,
        "guide_frames": 16,
        "seed": job.seed,
        "search_source": "incoming_mix",
    }
    montage = np.zeros((48, 2), dtype="<i2")
    montage[:16] = 1024
    montage[32:] = 2048
    audio = cascade.gpu.encode_flac(montage, "PCM_16")

    def travel(seconds):
        nonlocal now
        now += seconds

    def read_object(Bucket, Key):
        if state.lookup_fail and Key.endswith("seam.pcm"):
            raise ClientError({"Error": {"Code": "InternalError"}}, "GetObject")
        for backend, (request, submitted) in published.items():
            duration = state.backend_ready.get(backend, float("inf"))
            if Key == request["result_url"] and now >= submitted + duration:
                pcm = montage.copy()
                pcm[16:32] = 4096
                if state.invalid == backend:
                    pcm[0] = 0
                metadata = {
                    key.removeprefix("x-amz-meta-"): value
                    for key, value in request["result_headers"].items()
                    if key.startswith("x-amz-meta-")
                }
                objects.setdefault(Key, (pcm.tobytes(), metadata))
        if Key not in objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        data, metadata = objects[Key]
        return {"Body": BytesIO(data), "ContentLength": len(data), "Metadata": metadata}

    def put_object(Bucket, Key, Body, **kwargs):
        nonlocal montage_puts
        data = Body if isinstance(Body, bytes) else Body.read()
        kind = Key.rsplit("/", 1)[-1]
        if kind == "montage.flac":
            if state.hedge_storage_fail and montage_puts:
                raise OSError("input storage unavailable")
            montage_puts += 1
        if Key in objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        objects[Key] = data, {}
        if state.crash_at == kind:
            raise Interrupted

    client = SimpleNamespace(
        put_object=put_object,
        get_object=read_object,
        head_object=lambda Bucket, Key: {"ContentLength": len(objects[Key][0])},
        generate_presigned_url=lambda command, Params, **_: Params["Key"],
    )
    r2 = SimpleNamespace(client=client, bucket_name="fake")

    def persist(route, phase, raw=None):
        if phase and state.deny == set(route["attempts"]):
            return False
        saved.route = copy.deepcopy(route)
        if raw:
            assert saved.raw is None
            saved.raw = raw
        return True

    def first_contact(conn, job_id, user_id, phase, route, **kwargs):
        assert kwargs == {"claim_heartbeat": "lease", "verify_junction": True}
        assert saved.charged == 0
        if persist(route, phase):
            saved.charged += 1
            if state.crash_at == "intent":
                raise Interrupted
            return True
        return False

    def submit(backend, request, deadline):
        assert deadline > now
        assert request["seed"] == job.seed
        assert backend in saved.route["attempts"]
        assert all(call[0] != backend for call in submissions)
        submissions.append((backend, now))
        published[backend] = request, now
        if state.crash_at == "submit":
            raise Interrupted
        return "not-an-id!" if state.remote_bad else backend

    def runpod_call(method, operation, deadline, payload=None):
        if operation == "run":
            request = json.loads(payload)
            assert request["policy"] == {"executionTimeout": 180000, "ttl": 330000}
            return 200, {"id": submit("runpod", request["input"], deadline)}
        if operation.startswith("cancel/"):
            cancelled.append("runpod")
        return 200, {"status": state.runpod_status}

    def modal_submit(request, deadline):
        if state.modal_reject:
            raise modal_error.AuthError("submission rejected")
        return submit("modal", request, deadline)

    modal = SimpleNamespace(
        submit=modal_submit,
        poll=lambda *args: ("FAILED" if state.modal_fails else None, {}),
        cancel=lambda *args: cancelled.append("modal"),
    )

    def stage_factory(**callbacks):
        def stage(*args, work_dir, **kwargs):
            callbacks["authorize"]("upload-a")
            submissions.append(("elevenlabs", now))
            if state.eleven == "copyright":
                raise spine.GlueJobError(cascade.GLUE_COPYRIGHT_REJECTED, "none")
            travel(5)
            callbacks["authorize"]("upload-b")
            travel(5)
            callbacks["authorize"]("compose")
            if state.eleven == "timeout":
                travel(50)
                raise spine.GlueJobError(spine.GLUE_PROVIDER_UNKNOWN, "paid")
            path = work_dir / "raw.pcm"
            path.write_bytes(b"elevenlabs pcm")
            callbacks["accept"](path)

        return stage

    monkeypatch.setattr(cascade, "_time", lambda: now)
    monkeypatch.setattr(cascade, "_monotonic", lambda: now)
    monkeypatch.setattr(cascade, "_sleep", travel)
    monkeypatch.setattr(cascade.gpu.time, "monotonic", lambda: now)
    monkeypatch.setattr(cascade.gpu, "check_config", lambda: ("endpoint", r2))
    monkeypatch.setattr(cascade.gpu, "create_r2_client_from_env", lambda **_: r2)
    monkeypatch.setattr(cascade.gpu, "RunpodAPI", lambda *args: SimpleNamespace(call=runpod_call))
    monkeypatch.setattr(
        cascade.gpu,
        "prepare_audio",
        lambda *args, **kwargs: (
            (None, "unsupported-window") if state.ineligible else ((audio, audio, geometry), None)
        ),
    )
    monkeypatch.setattr(cascade, "ModalSampler", lambda *args: modal)
    monkeypatch.setattr(cascade, "create_provider_stage", stage_factory)
    monkeypatch.setattr(cascade, "prepare_staged_take", lambda *args, **kwargs: None)
    monkeypatch.setattr(cascade.Cascade, "_inputs", lambda self: inputs)
    monkeypatch.setattr(
        cascade.Cascade,
        "_excerpts",
        lambda *args, **kwargs: [
            SimpleNamespace(rejection_code="copyright" if state.skip else None, seconds=6),
            SimpleNamespace(rejection_code=None, seconds=6),
        ],
    )
    monkeypatch.setattr(cascade.db, "record_provider_contact", first_contact)
    monkeypatch.setattr(
        cascade.db,
        "get_glue_job",
        lambda *args: SimpleNamespace(
            active=True, heartbeat_at=job.heartbeat_at, provider_contacted=True
        )
        if state.edited
        else None,
    )
    monkeypatch.setattr(
        cascade.db,
        "update_glue_cascade",
        (lambda conn, job, route, submission_phase, raw: persist(route, submission_phase, raw)),
    )
    monkeypatch.setenv("SOW_ELEVENLABS_API_KEY", "test")

    def log(event, **fields):
        events.append((event, fields))

    def deliver(*, recover=False):
        if recover:
            job.provider_contacted = bool(saved.charged)
            job.provider_context_json = copy.deepcopy(saved.route)
        runner = cascade.Cascade(
            None,
            job,
            tmp_path,
            None,
            None,
            log,
            saved_route=job.provider_context_json if recover else None,
        )
        runner.run()
        assert saved.charged == 1
        key, digest, size = saved.raw
        assert hashlib.sha256(objects[key][0]).hexdigest() == digest
        assert len(objects[key][0]) == size
        return saved.route["winner"]["backend"]

    return SimpleNamespace(
        deliver=deliver,
        state=state,
        saved=saved,
        travel=travel,
        submissions=submissions,
        cancelled=cancelled,
        objects=objects,
        job=job,
        events=events,
        log=log,
    )


def test_elevenlabs_success_contacts_no_gpu(delivery):
    assert delivery.deliver() == "elevenlabs"
    assert delivery.submissions == [("elevenlabs", 1000)]
    attempt = delivery.saved.route["attempts"]["elevenlabs"]
    assert attempt["deadline_epoch"] == 1060
    assert attempt["operations"] == {"upload-a": 1000, "upload-b": 1005, "compose": 1010}


@pytest.mark.parametrize("failure,modal_submitted", [("timeout", 1060), ("copyright", 1000)])
def test_elevenlabs_failure_falls_back_once(delivery, failure, modal_submitted):
    delivery.state.eleven = failure
    delivery.state.backend_ready = {"modal": 3}
    assert delivery.deliver() == "modal"
    assert delivery.submissions == [("elevenlabs", 1000), ("modal", modal_submitted)]


def test_cached_rejection_with_incompatible_geometry_stays_precontact(delivery):
    delivery.state.skip = delivery.state.ineligible = True
    with pytest.raises(spine.GlueJobError) as caught:
        delivery.deliver()
    assert (caught.value.code, caught.value.retry_kind) == (
        "GLUE_JUNCTION_AUDIO_UNAVAILABLE",
        "none",
    )
    assert delivery.saved.charged == 0
    assert delivery.submissions == []


def test_cached_rejection_and_immediate_modal_failure(delivery):
    delivery.state.skip = delivery.state.modal_fails = True
    delivery.state.backend_ready = {"runpod": 3}
    assert delivery.deliver() == "runpod"
    assert delivery.submissions == [("modal", 1000), ("runpod", 1000)]


@pytest.mark.parametrize(
    ("modal", "runpod", "invalid", "winner"),
    [
        (15, 9, None, "modal"),
        (18, 3, None, "runpod"),
        (15, 3, None, "modal"),
        (15, 3, "modal", "runpod"),
    ],
)
def test_hedge_accepts_first_verified_audio(delivery, monkeypatch, modal, runpod, invalid, winner):
    monkeypatch.setenv("SOW_GLUE_MODAL_HEDGE_SECONDS", "12")
    delivery.state.skip = True
    delivery.state.backend_ready = {"modal": modal, "runpod": runpod}
    delivery.state.invalid = invalid
    assert delivery.deliver() == winner
    assert delivery.submissions == [("modal", 1000), ("runpod", 1012)]
    attempts = delivery.saved.route["attempts"]
    assert attempts["runpod"]["deadline_epoch"] == 1372
    assert attempts["modal"]["result_key"] != attempts["runpod"]["result_key"]
    assert attempts["modal"]["montage_sha256"] == attempts["runpod"]["montage_sha256"]
    assert delivery.cancelled == [next(name for name in ("modal", "runpod") if name != winner)]


@pytest.mark.parametrize(
    ("crash", "backend", "age"),
    [
        ("elevenlabs.pcm", "elevenlabs", 4680),
        ("response.pcm", "elevenlabs", 4680),
        ("submit", "modal", 4680),
        ("intent", "runpod", 30),
    ],
)
def test_interrupted_job_recovers_under_rollback(delivery, monkeypatch, crash, backend, age):
    delivery.state.crash_at = crash
    delivery.state.skip = backend != "elevenlabs"
    delivery.state.backend_ready = {"modal": 3, "runpod": 3}
    with pytest.raises(Interrupted):
        delivery.deliver()
    delivery.state.crash_at = None
    monkeypatch.setenv("SOW_GLUE_PROVIDER", "elevenlabs")
    delivery.travel(age)
    assert delivery.deliver(recover=True) == backend
    assert len(delivery.submissions) == 1


def test_lost_claim_blocks_additional_submission(delivery):
    delivery.state.eleven = "timeout"
    delivery.state.deny = {"elevenlabs", "modal"}
    with pytest.raises(spine._ClaimLost):
        delivery.deliver()
    assert delivery.submissions == [("elevenlabs", 1000)]
    assert delivery.saved.charged == 1


def test_crash_before_intent_can_prepare_again_without_charging_twice(delivery):
    delivery.state.skip = True
    delivery.state.crash_at = "montage.flac"
    delivery.state.backend_ready = {"modal": 3}
    with pytest.raises(Interrupted):
        delivery.deliver()
    assert delivery.saved.charged == 0
    assert delivery.submissions == []
    delivery.state.crash_at = None
    assert delivery.deliver(recover=True) == "modal"


def test_edit_at_the_snapshot_guard_blocks_fallback(delivery):
    delivery.state.eleven = "timeout"
    delivery.state.deny = {"elevenlabs", "modal"}
    delivery.state.edited = True
    with pytest.raises(spine._StaleJunction):
        delivery.deliver()
    assert delivery.submissions == [("elevenlabs", 1000)]


def test_duplicate_delivery_does_not_run_an_already_claimed_cascade(delivery, monkeypatch):
    delivery.job.provider_contacted = True
    delivery.job.provider_context_json = {"provider": "cascade", "version": 2}
    monkeypatch.setattr(spine, "claim_glue_job", lambda *args: (delivery.job, False))
    spine.execute_glue_job(delivery.job.id, delivery.job.user_id, None)
    assert delivery.submissions == []


def test_execute_rewrites_a_contacted_deadline_failure(delivery, monkeypatch, tmp_path):
    failures = []
    staged = []

    def runaway(self):
        raise spine.GlueJobError(spine.GLUE_EXECUTION_DEADLINE, "paid")

    def fail(conn, job, exc, *, contacted, log):
        failures.append((exc.code, exc.retry_kind, contacted))

    def after(*args, **kwargs):
        staged.append(kwargs)

    monkeypatch.setattr(cascade.Cascade, "run", runaway)
    monkeypatch.setattr(spine, "_handle_failure", fail)
    monkeypatch.setattr(spine, "_after_provider_stage", after)
    delivery.job.provider_contacted = True
    cascade.execute(None, delivery.job, tmp_path, None, None, delivery.log)
    assert failures == [(spine.GLUE_WORKER_FAILED, "paid", True)]
    assert staged == []


def test_execute_reports_gpu_ineligibility_without_charge(delivery, monkeypatch, tmp_path):
    failures = []

    def unavailable(self):
        raise glue_excerpts.GlueAudioUnavailable("Incoming recording is unavailable", side="B")

    def fail(conn, job, exc, *, contacted, log):
        failures.append((exc.code, exc.retry_kind, contacted, exc.detail))

    monkeypatch.setattr(cascade.Cascade, "run", unavailable)
    monkeypatch.setattr(spine, "_handle_failure", fail)
    cascade.execute(None, delivery.job, tmp_path, None, None, delivery.log)
    # The customer-facing detail names the side, never the object key.
    assert failures == [
        ("GLUE_JUNCTION_AUDIO_UNAVAILABLE", "none", False, "source-audio-unavailable side=B")
    ]
    assert cascade.JUNCTION_AUDIO_UNAVAILABLE == "GLUE_JUNCTION_AUDIO_UNAVAILABLE"


def test_execute_keeps_the_ineligible_window_detail(delivery, monkeypatch):
    def ineligible(self):
        raise spine.GlueJobError(
            cascade.JUNCTION_AUDIO_UNAVAILABLE, "none", "unsupported-window supportedBeats=4"
        )

    failures = []

    def fail(conn, job, exc, *, contacted, log):
        failures.append((exc.code, exc.detail))

    monkeypatch.setattr(cascade.Cascade, "run", ineligible)
    monkeypatch.setattr(spine, "_handle_failure", fail)
    cascade.execute(None, delivery.job, "", None, None, delivery.log)
    assert failures == [("GLUE_JUNCTION_AUDIO_UNAVAILABLE", "unsupported-window supportedBeats=4")]


def test_execute_reraises_after_database_loss(delivery, monkeypatch, tmp_path):
    def lost(self):
        raise psycopg2.Error("connection lost")

    monkeypatch.setattr(cascade.Cascade, "run", lost)
    delivery.job.provider_contacted = True
    with pytest.raises(psycopg2.Error):
        cascade.execute(None, delivery.job, tmp_path, None, None, delivery.log)


@pytest.mark.parametrize(("elevenlabs", "hedge"), [("0", "180"), ("400", "400")])
def test_invalid_generation_timing_is_not_configured(monkeypatch, elevenlabs, hedge):
    monkeypatch.setenv("SOW_GLUE_ELEVENLABS_SECONDS", elevenlabs)
    monkeypatch.setenv("SOW_GLUE_MODAL_HEDGE_SECONDS", hedge)
    with pytest.raises(spine.GlueJobError) as caught:
        cascade.Timing.from_env()
    assert (caught.value.code, caught.value.retry_kind) == (spine.GLUE_NOT_CONFIGURED, "free")


def test_recovery_rejects_corrupted_gpu_input(delivery):
    delivery.state.skip = True
    delivery.state.crash_at = "submit"
    with pytest.raises(Interrupted):
        delivery.deliver()
    delivery.objects[delivery.saved.route["attempts"]["modal"]["montage_key"]] = (
        b"corrupt montage",
        {},
    )
    delivery.state.crash_at = None
    with pytest.raises(RuntimeError, match="Stored SimDPS input is unavailable"):
        delivery.deliver(recover=True)


def test_recovery_without_elevenlabs_artifact_falls_forward(delivery):
    delivery.state.crash_at = "intent"
    delivery.state.backend_ready = {"modal": 3}
    with pytest.raises(Interrupted):
        delivery.deliver()
    delivery.state.crash_at = None
    assert delivery.deliver(recover=True) == "modal"
    attempt = delivery.saved.route["attempts"]["elevenlabs"]
    assert attempt["failure"] == "ambiguous"
    # The interrupted upload is the only ElevenLabs contact; recovery never repeats it.
    assert attempt["operations"] == {"upload-a": 1000}
    assert delivery.submissions == [("modal", 1000)]


def test_terminal_runpod_status_falls_back_to_modal(delivery, monkeypatch):
    monkeypatch.setenv("SOW_GLUE_MODAL_HEDGE_SECONDS", "12")
    delivery.state.skip = True
    delivery.state.runpod_status = "FAILED"
    delivery.state.backend_ready = {"modal": 30}
    assert delivery.deliver() == "modal"
    assert delivery.saved.route["attempts"]["runpod"]["failure"] == "remote-failed"
    assert delivery.submissions == [("modal", 1000), ("runpod", 1012)]
    # A terminal attempt is finished; cancelling it would be a wasted paid call.
    assert delivery.cancelled == []


def test_rejected_gpu_submission_advances_to_the_other_backend(delivery):
    delivery.state.skip = True
    delivery.state.modal_reject = True
    delivery.state.backend_ready = {"runpod": 3}
    assert delivery.deliver() == "runpod"
    assert delivery.saved.route["attempts"]["modal"]["failure"] == "submission-rejected"
    assert delivery.submissions == [("runpod", 1000)]


def test_missing_remote_id_still_promotes_stored_result(delivery):
    delivery.state.skip = True
    delivery.state.remote_bad = True
    delivery.state.backend_ready = {"modal": 3}
    assert delivery.deliver() == "modal"
    assert "remote_id" not in delivery.saved.route["attempts"]["modal"]
    ambiguous = [fields for event, fields in delivery.events if event == "glue.provider.ambiguous"]
    assert ambiguous == [{"provider": "modal", "reason": "missing-remote-id"}]


def test_result_lookup_failure_defers_to_redelivery(delivery):
    delivery.state.skip = True
    delivery.state.lookup_fail = True
    with pytest.raises(RuntimeError, match="Verified audio lookup must be retried"):
        delivery.deliver()
    attempts = delivery.saved.route["attempts"]
    assert attempts and all("failure" not in attempt for attempt in attempts.values())


def test_winner_promotion_conflict_defers_to_redelivery(delivery):
    delivery.state.skip = True
    delivery.state.backend_ready = {"modal": 3}
    delivery.objects[cascade.raw_response_key(delivery.job)] = (b"someone else's audio", {})
    with pytest.raises(RuntimeError, match="conflicts with the canonical raw response"):
        delivery.deliver()


def test_hedge_input_storage_failure_keeps_modal(delivery, monkeypatch):
    monkeypatch.setenv("SOW_GLUE_MODAL_HEDGE_SECONDS", "12")
    delivery.state.skip = True
    delivery.state.hedge_storage_fail = True
    delivery.state.backend_ready = {"modal": 30}
    assert delivery.deliver() == "modal"
    assert delivery.saved.route["runpod_skip"] == "input-storage-unavailable"
    assert delivery.submissions == [("modal", 1000)]
