"""Authenticated, bounded calls to the production Glue sampler."""

import asyncio
import time

import modal
from modal import exception

APP_NAME = "sow-glue-simdps"


class ModalSampler:
    def __init__(self, environment):
        self.environment = environment

    @staticmethod
    def _run(operation, deadline):
        async def invoke():
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                return await operation()

        return asyncio.run(invoke())

    def submit(self, payload, deadline):
        async def spawn():
            sampler = modal.Cls.from_name(
                APP_NAME, "GlueSampler", environment_name=self.environment
            )()
            return (await sampler.sample.spawn.aio(payload)).object_id

        return self._run(spawn, deadline)

    def poll(self, remote_id, deadline):
        async def poll():
            call = modal.FunctionCall.from_id(remote_id)
            try:
                return "COMPLETED", await call.get.aio(timeout=0)
            except exception.FunctionTimeoutError:
                return "TIMED_OUT", {}
            except exception.OutputExpiredError:
                return "FAILED", {}
            except (exception.TimeoutError, exception.ConnectionError, exception.ServiceError):
                return None, {}
            except (
                exception.RemoteError,
                exception.NotFoundError,
                exception.AuthError,
                exception.PermissionDeniedError,
                RuntimeError,
            ):
                return "FAILED", {}

        try:
            return self._run(poll, deadline)
        except TimeoutError:
            return None, {}

    def cancel(self, remote_id, deadline):
        async def cancel():
            call = modal.FunctionCall.from_id(remote_id)
            await call.cancel.aio(terminate_containers=True)

        self._run(cancel, deadline)
