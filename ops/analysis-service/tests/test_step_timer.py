"""Tests for the step_timer context manager and StepHeartbeat."""

import logging
import time

import pytest

from sow_analysis.step_timer import StepHeartbeat, step_timer


def _capture_logs(caplog):
    caplog.set_level(logging.INFO, logger="test.step_timer")
    return logging.getLogger("test.step_timer")


def test_step_timer_backward_compat_no_capture(caplog):
    """Backward compat: no heartbeat_interval, yield value not captured."""
    log = _capture_logs(caplog)
    with step_timer("My step", log):
        pass
    messages = [r.message for r in caplog.records]
    assert "Step started: My step" in messages
    assert any(m.startswith("Step completed: My step") for m in messages)


def test_step_timer_returns_heartbeat(caplog):
    """step_timer yields a StepHeartbeat when captured."""
    log = _capture_logs(caplog)
    with step_timer("My step", log, heartbeat_interval=30) as hb:
        assert isinstance(hb, StepHeartbeat)
        assert hb.step_name == "My step"


def test_step_timer_exception_logs_failed(caplog):
    """Exception path logs 'Step failed' with elapsed time and re-raises."""
    log = _capture_logs(caplog)
    with pytest.raises(RuntimeError, match="boom"), step_timer("My step", log):
        raise RuntimeError("boom")
    messages = [r.message for r in caplog.records]
    assert any(m.startswith("Step failed: My step") and "boom" in m for m in messages)


def test_heartbeat_throttled(caplog):
    """heartbeat() respects heartbeat_interval throttle."""
    log = _capture_logs(caplog)
    with step_timer("My step", log, heartbeat_interval=30) as hb:
        hb.heartbeat("detail 1")
        hb.heartbeat("detail 2")  # within interval -> suppressed
    beats = [r.message for r in caplog.records if "Step heartbeat" in r.message]
    assert len(beats) == 1
    assert "detail 1" in beats[0]


def test_heartbeat_emits_after_interval(caplog):
    """heartbeat() emits again once the interval has elapsed."""
    log = _capture_logs(caplog)
    with step_timer("My step", log, heartbeat_interval=0.01) as hb:
        hb.heartbeat("first")
        time.sleep(0.02)
        hb.heartbeat("second")
    beats = [r.message for r in caplog.records if "Step heartbeat" in r.message]
    assert len(beats) == 2
    assert "first" in beats[0]
    assert "second" in beats[1]


def test_heartbeat_disabled_when_interval_zero(caplog):
    """heartbeat() is a no-op when heartbeat_interval is 0."""
    log = _capture_logs(caplog)
    with step_timer("My step", log, heartbeat_interval=0) as hb:
        hb.heartbeat("should not log")
    beats = [r.message for r in caplog.records if "Step heartbeat" in r.message]
    assert len(beats) == 0
