"""Bounded Modal call mapping: a dead GPU call must fall back, never hang."""

import asyncio
import time
from types import SimpleNamespace

import pytest
from modal import exception

from sow_render_worker import glue_modal


def _install(monkeypatch, call):
    monkeypatch.setattr(glue_modal.modal.FunctionCall, "from_id", lambda remote_id: call)


def _call(*, get=None, cancel=None):
    return SimpleNamespace(
        get=SimpleNamespace(aio=get) if get else None,
        cancel=SimpleNamespace(aio=cancel) if cancel else None,
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (exception.FunctionTimeoutError("timed out"), ("TIMED_OUT", {})),
        (exception.OutputExpiredError("expired"), ("FAILED", {})),
        (exception.RemoteError("remote failed"), ("FAILED", {})),
        (exception.ConnectionError("connection reset"), (None, {})),
    ],
)
def test_poll_maps_exceptions_to_terminal_status(monkeypatch, error, expected):
    async def get(timeout):
        raise error

    _install(monkeypatch, _call(get=get))
    assert glue_modal.ModalSampler("main").poll("fc-1", time.monotonic() + 5) == expected


def test_poll_returns_completed_output(monkeypatch):
    async def get(timeout):
        assert timeout == 0
        return {"staging_status": "ok"}

    _install(monkeypatch, _call(get=get))
    status, output = glue_modal.ModalSampler("main").poll("fc-1", time.monotonic() + 5)
    assert (status, output) == ("COMPLETED", {"staging_status": "ok"})


def test_cancel_terminates_containers(monkeypatch):
    calls = []

    async def cancel(**kwargs):
        calls.append(kwargs)

    _install(monkeypatch, _call(cancel=cancel))
    glue_modal.ModalSampler("main").cancel("fc-1", time.monotonic() + 5)
    assert calls == [{"terminate_containers": True}]


def test_poll_returns_pending_when_deadline_passes(monkeypatch):
    async def never(timeout):
        await asyncio.sleep(3600)

    _install(monkeypatch, _call(get=never))
    started = time.monotonic()
    assert glue_modal.ModalSampler("main").poll("fc-1", time.monotonic() + 0.01) == (None, {})
    assert time.monotonic() - started < 1
