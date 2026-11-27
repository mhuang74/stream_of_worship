"""Durable provider attempts for a single Glue Job."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from math import isfinite

from botocore.exceptions import BotoCoreError, ClientError
from modal import exception as modal_error
from psycopg2 import Error as DatabaseError

from sow_render_worker import db, glue_jobs as spine
from sow_render_worker import glue_runpod as gpu
from sow_render_worker.glue_excerpts import GlueAudioUnavailable, audio_unavailable_detail
from sow_render_worker.glue_modal import ModalSampler
from sow_render_worker.glue_provider import (
    GLUE_COPYRIGHT_REJECTED,
    MAX_RESPONSE_BYTES,
    ElevenLabsHttpTransport,
    R2RawResponseStore,
    create_provider_stage,
    raw_response_key,
)
from sow_render_worker.glue_staging import ReferenceAudio, prepare_staged_take

LAMBDA_SECONDS = 780
MIN_PREPARATION_SECONDS = 40
IO_SECONDS = 8
POLL_SECONDS = 3
GPU_ORDER = ("runpod", "modal")
JUNCTION_AUDIO_UNAVAILABLE = "GLUE_JUNCTION_AUDIO_UNAVAILABLE"
_time = time.time
_monotonic = time.monotonic
_sleep = time.sleep


@dataclass(frozen=True)
class Timing:
    simdps_seconds: int
    elevenlabs_seconds: int

    @classmethod
    def from_env(cls):
        try:
            timing = cls(
                int(os.environ.get("SOW_GLUE_SIMDPS_SECONDS", "120")),
                int(os.environ.get("SOW_GLUE_ELEVENLABS_SECONDS", "60")),
            )
            if (
                min(timing.simdps_seconds, timing.elevenlabs_seconds) < 1
                or timing.total_seconds + MIN_PREPARATION_SECONDS + gpu.OUTCOME_SECONDS
                > LAMBDA_SECONDS
            ):
                raise ValueError
        except ValueError:
            raise spine.GlueJobError(
                spine.GLUE_NOT_CONFIGURED, "free", "Invalid generation timing"
            ) from None
        return timing

    @property
    def total_seconds(self):
        return 2 * self.simdps_seconds + self.elevenlabs_seconds


class Cascade:
    def __init__(self, conn, job, work_dir, context, assets, log, saved_route=None):
        self.conn = conn
        self.job = job
        self.work_dir = work_dir
        self.context = context
        self.log = log
        self.recovering = saved_route is not None
        self.invocation_deadline = _monotonic() + min(LAMBDA_SECONDS, gpu.remaining(context))
        self.finalization_deadline = self.invocation_deadline
        if saved_route is None:
            timing = Timing.from_env()
            end = self.invocation_deadline - gpu.OUTCOME_SECONDS
            if end - _monotonic() <= timing.total_seconds + MIN_PREPARATION_SECONDS:
                raise spine.GlueJobError(spine.GLUE_EXECUTION_DEADLINE, "free")
            endpoint, self.r2 = gpu.check_config()
            self.route = {
                "version": 3,
                "provider": "cascade",
                "policy": "cascade-simdps",
                "seed": job.seed,
                "deadline_epoch": _time() + end - _monotonic(),
                "simdps_seconds": timing.simdps_seconds,
                "elevenlabs_seconds": timing.elevenlabs_seconds,
                "runpod_endpoint": endpoint,
                "modal_environment": os.environ.get("SOW_GLUE_MODAL_ENVIRONMENT", "main"),
                "attempts": {},
            }
        else:
            self.route = saved_route
            self.r2 = gpu.create_r2_client_from_env(bounded=True)
        self.attempts = self.route["attempts"]
        self.store = R2RawResponseStore(self.r2)
        self.runpod = gpu.RunpodAPI(self.route["runpod_endpoint"], os.environ.get("RUNPOD_API_KEY"))
        self.modal = ModalSampler(self.route["modal_environment"])
        self.assets = assets or gpu.PreparationAssets(
            self.r2, work_dir, context, time_left=self._preparation_time
        )
        self.preparation_epoch = self.route["deadline_epoch"] - (
            2 * self.route.get("simdps_seconds", 0) + self.route.get("elevenlabs_seconds", 0)
        )
        self.audio = None
        self.lookup_errors = set()

    def _deadline(self, epoch):
        return _monotonic() + epoch - _time()

    def _io_deadline(self, end=None):
        deadline = min(self.finalization_deadline, _monotonic() + IO_SECONDS)
        return deadline if end is None else min(deadline, self._deadline(end))

    def _preparation_time(self):
        left = (
            min(self._deadline(self.preparation_epoch), self.finalization_deadline) - _monotonic()
        )
        if left < 1:
            raise spine.GlueJobError(
                spine.GLUE_EXECUTION_DEADLINE, "paid" if self.job.provider_contacted else "free"
            )
        return min(60, left)

    def _inputs(self):
        rows = db.read_songset_items_for_glue(self.conn, self.job.songset_id)
        inputs = spine._rebuild_junction(rows, self.job)
        if inputs.canonical_json != self.job.descriptor_json:
            raise spine._StaleJunction("Saved Junction changed during generation")
        return inputs

    def _save(self, *, submission_phase=None, raw=None):
        if submission_phase:
            self._inputs()
        if not self.job.provider_contacted:
            saved = db.record_provider_contact(
                self.conn,
                self.job.id,
                self.job.user_id,
                submission_phase,
                self.route,
                claim_heartbeat=self.job.heartbeat_at,
                verify_junction=True,
            )
            if saved:
                self.job.provider_contacted = True
                self.log(
                    "glue.provider.contact.started",
                    provider="cascade",
                    attemptId=self.job.attempt_id,
                )
        else:
            saved = db.update_glue_cascade(
                self.conn, self.job, self.route, submission_phase=submission_phase, raw=raw
            )
        if not saved:
            if submission_phase:
                current = db.get_glue_job(self.conn, self.job.id, self.job.user_id)
                if (
                    current
                    and current.active
                    and current.heartbeat_at == self.job.heartbeat_at
                    and current.provider_contacted == self.job.provider_contacted
                ):
                    raise spine._StaleJunction("Saved Junction changed before provider submission")
            raise spine._ClaimLost
        self.job.provider_context_json = self.route
        if submission_phase:
            self.job.phase = "composing" if submission_phase == "compose" else "uploading"

    def _excerpts(self, inputs, *, gpu_audio=False):
        excerpts = []
        for side in ("A", "B"):
            self._preparation_time()
            excerpt = spine._prepare_side(
                self.conn,
                self.job,
                inputs,
                side=side,
                asset_fetcher=self.assets,
                work_dir=self.work_dir,
                log=self.log,
                local_only=gpu_audio,
                include_rejected_audio=gpu_audio,
                preparation_time=self._preparation_time,
            )
            excerpts.append(excerpt)
            spine._heartbeat(self.conn, self.job, self.log, stage=f"excerpt-{side.lower()}")
        return excerpts

    def _fail_attempt(self, backend, reason):
        attempt = self.attempts[backend]
        attempt.update(failure=reason, finished_epoch=_time())
        self._save()
        self.log(
            "glue.provider.fallback",
            provider=backend,
            reason=reason,
            durationMs=round(1000 * (_time() - attempt["submitted_epoch"])),
        )

    def _choose(self, backend, audio):
        attempt = self.attempts[backend]
        self.route["winner"] = {
            "backend": backend,
            "model": attempt["model"],
            "result_key": attempt["result_key"],
            "sha256": hashlib.sha256(audio).hexdigest(),
            "bytes": len(audio),
            "verified_epoch": _time(),
        }
        self._save()
        self.log(
            "glue.provider.winner",
            provider=backend,
            model=attempt["model"],
            submissionId=attempt.get("submission_id"),
            durationMs=round(1000 * (_time() - attempt["submitted_epoch"])),
        )

    def _elevenlabs(self, inputs):
        backend = "elevenlabs"
        self.preparation_epoch = self.route["deadline_epoch"]
        excerpts = self._excerpts(inputs)
        if any(excerpt.rejection_code for excerpt in excerpts):
            self.route["elevenlabs_skip"] = GLUE_COPYRIGHT_REJECTED
            self.log("glue.provider.fallback", provider=backend, reason="cached-copyright")
            return

        attempt = {
            "model": "music_v2_5",
            "result_key": f"glues/{self.job.user_id}/jobs/{self.job.id}/elevenlabs.pcm",
            "operations": {},
            "a_seconds": excerpts[0].seconds,
            "b_seconds": excerpts[1].seconds,
        }

        def authorize(operation):
            now = _time()
            if backend not in self.attempts:
                self._preparation_time()
                attempt.update(
                    submitted_epoch=now, deadline_epoch=now + self.route["elevenlabs_seconds"]
                )
                self.attempts[backend] = attempt
            if now >= attempt["deadline_epoch"]:
                raise spine.GlueJobError(spine.GLUE_EXECUTION_DEADLINE, "paid")
            attempt["operations"][operation] = now
            self._save(submission_phase=operation)

        def accept(path):
            audio = path.read_bytes()
            prepare_staged_take(
                audio,
                job=self.job,
                descriptor=inputs.descriptor,
                items={
                    self.job.from_item_id: inputs.from_item,
                    self.job.to_item_id: inputs.to_item,
                },
                reference=ReferenceAudio(
                    a_seconds=excerpts[0].seconds, b_seconds=excerpts[1].seconds
                ),
            )
            if not self.store.put_if_absent(attempt["result_key"], path):
                raise RuntimeError("Verified ElevenLabs audio conflicts with stored data")
            self._choose(backend, audio)

        key = os.environ.get("SOW_ELEVENLABS_API_KEY")
        if not key:
            self.route["elevenlabs_skip"] = spine.GLUE_NOT_CONFIGURED
            self.log("glue.provider.fallback", provider=backend, reason=spine.GLUE_NOT_CONFIGURED)
            return
        stage = create_provider_stage(
            transport=ElevenLabsHttpTransport(
                key, deadline=lambda: self._deadline(attempt["deadline_epoch"])
            ),
            raw_store=self.store,
            authorize=authorize,
            accept=accept,
        )
        try:
            stage(
                self.job,
                self.conn,
                a_excerpt=excerpts[0],
                b_excerpt=excerpts[1],
                work_dir=self.work_dir,
                log_event=self.log,
            )
        except spine.GlueJobError as exc:
            if not self.job.provider_contacted:
                raise
            self._fail_attempt(backend, exc.code)

    def _gpu_audio(self):
        if self.audio is not None:
            return self.audio
        source = next((self.attempts[name] for name in GPU_ORDER if name in self.attempts), None)
        if source is not None:
            blobs = []
            for name in ("montage", "guide"):
                result = gpu._read_object(
                    self.r2,
                    source[f"{name}_key"],
                    gpu.INPUT_CAP_BYTES,
                    self._io_deadline(),
                )
                if not result or hashlib.sha256(result[0]).hexdigest() != source[f"{name}_sha256"]:
                    raise RuntimeError("Stored SimDPS input is unavailable")
                blobs.append(result[0])
            geometry = {
                key: source[key]
                for key in (
                    "a_frames",
                    "fill_frames",
                    "b_frames",
                    "guide_frames",
                    "seed",
                    "search_source",
                )
            }
            self.audio = (*blobs, geometry)
        else:
            inputs = self._inputs()
            excerpts = self._excerpts(inputs, gpu_audio=True)
            self.audio, reason = gpu.prepare_audio(
                self.job,
                inputs,
                *excerpts,
                self.r2,
                self.work_dir,
                self.assets,
                self.context,
                time_left=self._preparation_time,
            )
            if self.audio is None:
                self.log("glue.provider.ineligible", provider="simdps", reason=reason)
                raise spine.GlueJobError(JUNCTION_AUDIO_UNAVAILABLE, "none", reason)
        return self.audio

    def _submit_gpu(self, backend, *, deadline_seconds):
        audio = self._gpu_audio()
        prefix = f"glue-simdps-tmp/{self.job.user_id}/{self.job.id}"
        attempt, request = gpu.stage_inputs(
            self.r2, self.route["runpod_endpoint"], prefix, audio, check_time=self._preparation_time
        )
        payload = gpu.sign_request(self.r2, prefix, attempt, request)
        self._preparation_time()
        now = _time()
        payload["input"]["submitted_epoch"] = now
        attempt.update(submitted_epoch=now, deadline_epoch=now + deadline_seconds)
        self.attempts[backend] = attempt
        self._save(submission_phase="compose")
        self.log(
            "glue.provider.submitted",
            provider=backend,
            model=attempt["model"],
            submissionId=attempt["submission_id"],
            searchSource=attempt["search_source"],
        )
        deadline = self._io_deadline(attempt["deadline_epoch"])
        if backend == "modal":
            try:
                remote_id = self.modal.submit(payload["input"], deadline)
            except (
                modal_error.AuthError,
                modal_error.NotFoundError,
                modal_error.InvalidError,
                modal_error.PermissionDeniedError,
            ):
                self._fail_attempt(backend, "submission-rejected")
                return
            except (TimeoutError, OSError, modal_error.Error):
                remote_id = None
        else:
            status, reply = self.runpod.call("POST", "run", deadline, json.dumps(payload).encode())
            if status in {400, 401, 403, 404, 405, 413, 422}:
                self._fail_attempt(backend, "submission-rejected")
                return
            remote_id = reply.get("id")
        if isinstance(remote_id, str) and gpu.IDENTIFIER.fullmatch(remote_id):
            attempt["remote_id"] = remote_id
            self._save()
        else:
            self.log("glue.provider.ambiguous", provider=backend, reason="missing-remote-id")

    def _gpu_result(self, backend, *, final=False):
        attempt = self.attempts.get(backend)
        if attempt is None or "failure" in attempt:
            return False
        end = None if final else min(attempt["deadline_epoch"], self.route["deadline_epoch"])
        deadline = self._io_deadline(end)
        if deadline <= _monotonic():
            return False
        try:
            stored = gpu._read_object(
                self.r2,
                attempt["result_key"],
                MAX_RESPONSE_BYTES,
                deadline,
                exact=gpu.seam_bytes(attempt),
            )
            if stored is not None:
                audio = gpu._verify_result(self.r2, attempt, self._io_deadline(), stored)
        except gpu.InvalidResult:
            self.lookup_errors.discard(backend)
            self._fail_attempt(backend, "invalid-audio")
            return False
        except (BotoCoreError, ClientError, OSError):
            self.lookup_errors.add(backend)
            return False
        self.lookup_errors.discard(backend)
        if stored is not None:
            self._choose(backend, audio)
            return True
        if final or not attempt.get("remote_id"):
            return False

        remote_id = attempt["remote_id"]
        deadline = self._io_deadline(end)
        if backend == "modal":
            status, output = self.modal.poll(remote_id, deadline)
        else:
            _, reply = self.runpod.call("GET", f"status/{remote_id}", deadline)
            status, output = reply.get("status"), reply.get("output")
        if isinstance(status, str) and status in gpu.TERMINAL:
            timings = {
                key: value
                for key, value in (output.items() if isinstance(output, dict) else ())
                if key
                in (
                    "model_load_seconds",
                    "sampling_seconds",
                    "processing_seconds",
                    "dispatch_to_processing_seconds",
                )
                and type(value) in (int, float)
                and isfinite(value)
                and 0 <= value <= 3600
            }
            if backend == "runpod":
                delay = reply.get("delayTime")
                if type(delay) in (int, float) and 0 <= delay <= 3_600_000:
                    timings["queue_seconds"] = delay / 1000
            self.log(
                "glue.provider.terminal",
                provider=backend,
                status=status,
                submissionId=attempt["submission_id"],
                **timings,
            )
            if self._gpu_result(backend, final=True):
                return True
            if "failure" not in attempt and backend not in self.lookup_errors:
                self._fail_attempt(backend, f"remote-{status.lower()}")
        return False

    def _gpu_stage(self, backend):
        now = _time()
        limit = min(now + self.route["simdps_seconds"], self.route["deadline_epoch"])
        if limit <= now:
            raise spine.GlueJobError(spine.GLUE_EXECUTION_DEADLINE, "free")
        # Reserve only the windows still ahead of this stage: the ElevenLabs
        # window, plus Modal's when RunPod is running.
        reserved = self.route.get("elevenlabs_seconds", 0)
        if backend == GPU_ORDER[0]:
            reserved += self.route.get("simdps_seconds", 0)
        self.preparation_epoch = self.route["deadline_epoch"] - reserved
        try:
            self._submit_gpu(backend, deadline_seconds=limit - now)
        except spine.GlueJobError as exc:
            if exc.code == JUNCTION_AUDIO_UNAVAILABLE:
                self.route["simdps_skip"] = exc.detail
                return
            raise
        except (BotoCoreError, ClientError, OSError):
            if backend in self.attempts:
                raise  # contact may have been recorded; the outcome is ambiguous
            self.log(
                "glue.provider.fallback",
                provider=backend,
                reason="input-storage-unavailable",
            )
            return
        attempt = self.attempts[backend]
        if "failure" in attempt:
            return
        while _time() < attempt["deadline_epoch"] and _time() < self.route["deadline_epoch"]:
            self._save()
            if self._gpu_result(backend):
                return
            if "failure" in attempt:
                return
            _sleep(max(0, min(POLL_SECONDS, attempt["deadline_epoch"] - _time())))
        if "winner" in self.route or "failure" in attempt:
            return
        if self._gpu_result(backend, final=True):
            return
        if "failure" in attempt:
            return
        if backend in self.lookup_errors:
            raise RuntimeError("Verified audio lookup must be retried")
        self._fail_attempt(backend, "timeout")
        self._cancel(backend)

    def _simdps_pipeline(self):
        self._gpu_stage("runpod")
        if "winner" not in self.route and "simdps_skip" not in self.route:
            self._gpu_stage("modal")
        if "winner" not in self.route:
            self._elevenlabs(self._inputs())

    def _cancel(self, backend):
        attempt = self.attempts.get(backend, {})
        remote_id = attempt.get("remote_id")
        failure = attempt.get("failure", "")
        if not remote_id or failure.startswith("remote-"):
            return
        try:
            deadline = self._io_deadline()
            if backend == "modal":
                self.modal.cancel(remote_id, deadline)
            else:
                self.runpod.call("POST", f"cancel/{remote_id}", deadline)
        except Exception:
            self.log("glue.provider.cancel.failed", provider=backend, level="warning")

    def _salvage(self):
        for backend in GPU_ORDER:
            if self._gpu_result(backend, final=True):
                return
        elevenlabs = self.attempts.get("elevenlabs")
        if elevenlabs is not None and not elevenlabs.get("failure"):
            result = gpu._read_object(
                self.r2, elevenlabs["result_key"], MAX_RESPONSE_BYTES, self._io_deadline()
            )
            if result:
                self._choose("elevenlabs", result[0])
                return
            self._fail_attempt("elevenlabs", "ambiguous")

    def run(self):
        if "winner" not in self.route:
            if self.recovering:
                self._salvage()
            else:
                self._simdps_pipeline()
        self.finalization_deadline = min(
            self.invocation_deadline, _monotonic() + gpu.OUTCOME_SECONDS
        )
        winner = self.route.get("winner")
        for backend in GPU_ORDER:
            attempt = self.attempts.get(backend)
            if winner is not None and winner["backend"] == backend:
                continue
            if attempt is not None and "failure" not in attempt:
                self._cancel(backend)
        if winner is None:
            if self.lookup_errors:
                raise RuntimeError("Verified audio lookup must be retried")
            if not self.job.provider_contacted:
                if self.route.get("simdps_skip"):
                    raise spine.GlueJobError(
                        JUNCTION_AUDIO_UNAVAILABLE, "none", self.route["simdps_skip"]
                    )
                skip = self.route.get("elevenlabs_skip")
                if skip == GLUE_COPYRIGHT_REJECTED:
                    raise spine.GlueJobError(GLUE_COPYRIGHT_REJECTED, "none")
                if skip == spine.GLUE_NOT_CONFIGURED:
                    raise spine.GlueJobError(spine.GLUE_NOT_CONFIGURED, "free")
            raise spine.GlueJobError(
                spine.GLUE_WORKER_FAILED,
                "paid" if self.job.provider_contacted else "free",
            )

        result = gpu._read_object(
            self.r2,
            winner["result_key"],
            MAX_RESPONSE_BYTES,
            self._io_deadline(),
            exact=winner["bytes"],
        )
        if not result or hashlib.sha256(result[0]).hexdigest() != winner["sha256"]:
            raise RuntimeError("Verified audio is unavailable for promotion")
        path = self.work_dir / "winner.pcm"
        path.write_bytes(result[0])
        key = raw_response_key(self.job)
        self._save()
        if not self.store.put_if_absent(key, path):
            raise RuntimeError("Verified audio conflicts with the canonical raw response")
        self._save(raw=(key, winner["sha256"], winner["bytes"]))
        self.job.phase = "storing-response"


def execute(conn, job, work_dir, context, assets, log, saved_route=None):
    try:
        cascade = Cascade(conn, job, work_dir, context, assets, log, saved_route)
        cascade.run()
        cascade.preparation_epoch = _time() + cascade.finalization_deadline - _monotonic()
        spine._after_provider_stage(
            conn,
            job,
            log,
            work_dir=work_dir,
            asset_fetcher=cascade.assets,
            deadline=cascade.finalization_deadline,
        )
    except spine._ClaimLost:
        log("glue.cascade.ownership_lost", phase=job.phase)
    except spine._StaleJunction as exc:
        spine._finish_stale(conn, job, log, reason=str(exc))
    except GlueAudioUnavailable as exc:
        spine._handle_failure(
            conn,
            job,
            spine.GlueJobError(
                JUNCTION_AUDIO_UNAVAILABLE, "none", audio_unavailable_detail(exc)
            ),
            contacted=job.provider_contacted,
            log=log,
        )
    except spine.GlueJobError as exc:
        if job.provider_contacted and exc.code == spine.GLUE_EXECUTION_DEADLINE:
            exc = spine.GlueJobError(spine.GLUE_WORKER_FAILED, "paid")
        spine._handle_failure(conn, job, exc, contacted=job.provider_contacted, log=log)
    except Exception as exc:
        if job.provider_contacted or isinstance(exc, DatabaseError):
            # A lost acknowledgement can leave persisted intent or a verified winner.
            # Keep the delivery on the existing SQS redrive path.
            raise
        spine._handle_failure(conn, job, exc, contacted=False, log=log)
