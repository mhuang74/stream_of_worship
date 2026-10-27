"""Tests for the Modal analyzer client (services/modal_analyzer_client.py).

No network: modal.Cls.from_name is patched with a duck-typed fake handle, so
nothing here touches Modal. modal must be importable (runtime dep).
"""

import asyncio
import logging
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import grpclib
import modal
import modal.exception as modal_exception
import numpy as np
import pytest
import soundfile as sf

from sow_analysis.services.modal_analyzer_client import (
    ModalAnalyzerClient,
    ModalAnalyzerConfigError,
    ModalAnalyzerError,
    get_modal_analyzer_client,
)

ANALYSIS_PAYLOAD = {
    "tempo_bpm": 72.0,
    "beats": [0.46, 1.39],
    "downbeats": [0.46],
    "sections": [{"label": "verse", "start": 0.0, "end": 10.0}],
    "embeddings_shape": [4, 100, 24],
}


class _FakeCls:
    """Duck-typed stand-in for modal.Cls.from_name(...) result."""

    def __init__(self, aio_by_method):
        self._aio_by_method = aio_by_method
        self.init_kwargs: dict = {}

    def __call__(self, **kwargs):
        self.init_kwargs = kwargs
        return SimpleNamespace(
            **{
                name: SimpleNamespace(remote=SimpleNamespace(aio=aio))
                for name, aio in self._aio_by_method.items()
            }
        )


def _make_client(monkeypatch, outcomes, **kwargs):
    """Fresh client + fake handle per case (never the module singleton).

    `outcomes` is wired to the ``analyze`` remote method.
    """
    primary = AsyncMock(side_effect=list(outcomes))
    fake_cls = _FakeCls({"analyze": primary})
    monkeypatch.setattr(modal.Cls, "from_name", staticmethod(lambda app, name: fake_cls))
    monkeypatch.setenv("MODAL_TOKEN_ID", "test-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "test-secret")
    kwargs.setdefault("max_attempts", 2)
    kwargs.setdefault("retry_delay_seconds", 0.01)
    kwargs.setdefault("max_audio_bytes", 10_000_000)
    client = ModalAnalyzerClient(**kwargs)
    return client, primary


def _write_audio(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def _write_test_wav(path: Path, duration: float, sr: int = 8000) -> Path:
    rng = np.random.default_rng(42)
    t = np.arange(int(sr * duration)) / sr
    y = 0.4 * np.sin(2 * np.pi * 220.0 * t) + 0.6 * rng.uniform(-1.0, 1.0, t.shape)
    sf.write(str(path), y, sr, format="WAV", subtype="PCM_16")
    return path


async def test_analyze_success_passthrough(monkeypatch):
    client, aio = _make_client(monkeypatch, [ANALYSIS_PAYLOAD])
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_test_wav(Path(tmp) / "a.wav", duration=0.1)
        result = await client.analyze(audio)

    assert result == ANALYSIS_PAYLOAD
    assert aio.await_count == 1
    sent_kwargs = aio.await_args.kwargs
    assert sent_kwargs["include_stems"] is False
    assert sent_kwargs["filename"] == "a.wav"
    request_id = sent_kwargs["request_id"]
    assert len(request_id) == 12
    assert re.fullmatch(r"[0-9a-f]{12}", request_id)


async def test_analyze_writes_stems_and_strips_key(monkeypatch):
    payload = {**ANALYSIS_PAYLOAD, "stems": {"vocals": b"v", "bass": b"b"}}
    client, aio = _make_client(monkeypatch, [payload])
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_test_wav(Path(tmp) / "a.wav", duration=0.1)
        stems_dir = Path(tmp) / "stems"
        result = await client.analyze(audio, output_stems_dir=stems_dir)

        assert "stems" not in result
        assert result == ANALYSIS_PAYLOAD
        assert (stems_dir / "vocals.wav").read_bytes() == b"v"
        assert (stems_dir / "bass.wav").read_bytes() == b"b"
        assert aio.await_args.kwargs["include_stems"] is True


async def test_analyze_missing_stems_raises(monkeypatch):
    client, _ = _make_client(monkeypatch, [{**ANALYSIS_PAYLOAD}])
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_test_wav(Path(tmp) / "a.wav", duration=0.1)
        with pytest.raises(ModalAnalyzerError, match="returned no stems"):
            await client.analyze(audio, output_stems_dir=Path(tmp) / "stems")


async def test_missing_credentials_raises_config_error(monkeypatch):
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    client, aio = _make_client(monkeypatch, [ANALYSIS_PAYLOAD])
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_test_wav(Path(tmp) / "a.wav", duration=0.1)
        with pytest.raises(ModalAnalyzerConfigError, match="MODAL_TOKEN_ID"):
            await client.analyze(audio)
    aio.assert_not_awaited()


async def test_empty_file_raises(monkeypatch):
    client, aio = _make_client(monkeypatch, [ANALYSIS_PAYLOAD])
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.mp3", b"")
        with pytest.raises(ModalAnalyzerError, match="Audio file is empty"):
            await client.analyze(audio)
    aio.assert_not_awaited()


async def test_not_found_error_is_config_error_with_deploy_command(monkeypatch):
    client, aio = _make_client(
        monkeypatch, [modal_exception.NotFoundError("no such class")], max_attempts=3
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_test_wav(Path(tmp) / "a.wav", duration=0.1)
        with pytest.raises(ModalAnalyzerConfigError, match=r"modal deploy modal_apps/analyzer_app\.py"):
            await client.analyze(audio)
    assert aio.await_count == 1  # no retry on lookup failure


async def test_auth_error_is_config_error_without_retry(monkeypatch):
    client, aio = _make_client(
        monkeypatch, [modal_exception.AuthError("bad token")], max_attempts=3
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_test_wav(Path(tmp) / "a.wav", duration=0.1)
        with pytest.raises(ModalAnalyzerConfigError, match="MODAL_TOKEN_ID"):
            await client.analyze(audio)
    assert aio.await_count == 1


async def test_transient_connection_error_then_success(monkeypatch, caplog):
    client, aio = _make_client(
        monkeypatch, [modal_exception.ConnectionError("reset"), ANALYSIS_PAYLOAD]
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_test_wav(Path(tmp) / "a.wav", duration=0.1)
        with caplog.at_level(
            logging.WARNING, logger="sow_analysis.services.modal_analyzer_client"
        ):
            result = await client.analyze(audio)
    assert result == ANALYSIS_PAYLOAD
    assert aio.await_count == 2
    request_id = aio.await_args.kwargs["request_id"]
    assert any(
        "attempt 1/2" in r.getMessage() and request_id in r.getMessage()
        for r in caplog.records
    )


async def test_grpc_unavailable_then_success(monkeypatch):
    client, aio = _make_client(
        monkeypatch,
        [grpclib.GRPCError(grpclib.Status.UNAVAILABLE, "unavailable"), ANALYSIS_PAYLOAD],
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_test_wav(Path(tmp) / "a.wav", duration=0.1)
        result = await client.analyze(audio)
    assert result == ANALYSIS_PAYLOAD
    assert aio.await_count == 2


async def test_grpc_invalid_argument_is_non_retryable(monkeypatch):
    client, aio = _make_client(
        monkeypatch,
        [grpclib.GRPCError(grpclib.Status.INVALID_ARGUMENT, "bad argument")],
        max_attempts=3,
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_test_wav(Path(tmp) / "a.wav", duration=0.1)
        with pytest.raises(ModalAnalyzerError, match="transport error"):
            await client.analyze(audio)
    assert aio.await_count == 1


async def test_container_user_exception_is_non_retryable(monkeypatch):
    # Container exceptions re-raise as their original class: a plain RuntimeError
    # must match the catch-all, not the retryable branches.
    client, aio = _make_client(
        monkeypatch, [RuntimeError("CUDA out of memory")], max_attempts=3
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_test_wav(Path(tmp) / "a.wav", duration=0.1)
        with pytest.raises(ModalAnalyzerError) as exc_info:
            await client.analyze(audio)
    message = str(exc_info.value)
    assert "RuntimeError" in message
    assert "modal app logs" in message
    assert aio.await_count == 1


async def test_request_size_error_no_retry(monkeypatch):
    client, aio = _make_client(
        monkeypatch, [modal_exception.RequestSizeError("too big")], max_attempts=3
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_test_wav(Path(tmp) / "a.wav", duration=0.1)
        with pytest.raises(ModalAnalyzerError, match="too large"):
            await client.analyze(audio)
    assert aio.await_count == 1


async def test_incompressible_oversize_fails_before_modal(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        wav = _write_test_wav(Path(tmp) / "big.wav", duration=2.0)
        client, aio = _make_client(monkeypatch, [ANALYSIS_PAYLOAD], max_audio_bytes=100)
        with pytest.raises(ModalAnalyzerError, match="SOW_MODAL_ANALYZER_MAX_AUDIO_BYTES"):
            await client.analyze(wav)
    aio.assert_not_awaited()


async def test_no_client_side_timeout(monkeypatch):
    """A slow-but-succeeding call must return the payload — no wait_for wrapper."""

    async def slow_aio(**kwargs):
        await asyncio.sleep(0.2)
        return ANALYSIS_PAYLOAD

    aio = AsyncMock(side_effect=slow_aio)
    fake_cls = _FakeCls({"analyze": aio})
    monkeypatch.setattr(modal.Cls, "from_name", staticmethod(lambda app, name: fake_cls))
    monkeypatch.setenv("MODAL_TOKEN_ID", "test-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "test-secret")
    client = ModalAnalyzerClient()
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_test_wav(Path(tmp) / "a.wav", duration=0.1)
        result = await client.analyze(audio)
    assert result == ANALYSIS_PAYLOAD


async def test_singleton_returns_same_instance(monkeypatch):
    import sow_analysis.services.modal_analyzer_client as m

    monkeypatch.setattr(m, "_client", None)
    c1 = m.get_modal_analyzer_client()
    c2 = m.get_modal_analyzer_client()
    assert c1 is c2
    monkeypatch.setattr(m, "_client", None)