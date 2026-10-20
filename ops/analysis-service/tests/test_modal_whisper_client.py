"""Tests for the Modal Whisper client (services/modal_whisper_client.py).

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

from sow_analysis.services.modal_whisper_client import (
    ModalWhisperClient,
    ModalWhisperConfigError,
    ModalWhisperError,
    get_modal_whisper_client,
)

PAYLOAD = {
    "phrases": [
        {"text": "主是我力量", "start": 0.0, "end": 2.0, "no_speech_prob": 0.01},
        {"text": "是我盾牌", "start": 2.1, "end": 4.0, "no_speech_prob": 0.02},
    ],
    "language": "zh",
    "language_probability": 1.0,
}


class _FakeCls:
    """Duck-typed stand-in for modal.Cls.from_name(...) result."""

    def __init__(self, aio):
        self._aio = aio
        self.init_kwargs: dict = {}

    def __call__(self, **kwargs):
        self.init_kwargs = kwargs
        return SimpleNamespace(
            transcribe=SimpleNamespace(remote=SimpleNamespace(aio=self._aio))
        )


def _make_client(monkeypatch, outcomes, **kwargs):
    """Fresh client + fake handle per case (never the module singleton)."""
    aio = AsyncMock(side_effect=list(outcomes))
    fake_cls = _FakeCls(aio)
    monkeypatch.setattr(modal.Cls, "from_name", staticmethod(lambda app, name: fake_cls))
    monkeypatch.setenv("MODAL_TOKEN_ID", "test-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "test-secret")
    kwargs.setdefault("max_attempts", 2)
    kwargs.setdefault("retry_delay_seconds", 0.01)
    client = ModalWhisperClient(**kwargs)
    return client, aio


def _write_audio(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


async def _transcribe(client: ModalWhisperClient, audio_path: Path, **kwargs):
    kwargs.setdefault("model_name", "large-v3")
    kwargs.setdefault("language", "zh")
    kwargs.setdefault("initial_prompt", "這是一首中文敬拜歌的歌詞")
    kwargs.setdefault("vad_filter", False)
    kwargs.setdefault("condition_on_previous_text", False)
    return await client.transcribe(audio_path, **kwargs)


async def test_success_passthrough_and_request_id(monkeypatch):
    client, aio = _make_client(monkeypatch, [PAYLOAD])
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.mp3", b"audio-bytes")
        result = await _transcribe(client, audio)

    assert result == PAYLOAD
    assert aio.await_count == 1
    sent_kwargs = aio.await_args.kwargs
    assert sent_kwargs["audio_bytes"] == b"audio-bytes"
    assert sent_kwargs["language"] == "zh"
    request_id = sent_kwargs["request_id"]
    assert len(request_id) == 12
    assert re.fullmatch(r"[0-9a-f]{12}", request_id)


async def test_missing_credentials_raises_config_error(monkeypatch):
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    client, aio = _make_client(monkeypatch, [PAYLOAD])
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.mp3", b"audio-bytes")
        with pytest.raises(ModalWhisperConfigError, match="MODAL_TOKEN_ID"):
            await _transcribe(client, audio)
    aio.assert_not_awaited()


async def test_model_outside_allowlist_rejected_before_modal(monkeypatch):
    client, aio = _make_client(monkeypatch, [PAYLOAD], allowed_models=frozenset({"large-v3"}))
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.mp3", b"audio-bytes")
        with pytest.raises(ModalWhisperError, match="SOW_MODAL_WHISPER_ALLOWED_MODELS"):
            await _transcribe(client, audio, model_name="bogus-model")
    aio.assert_not_awaited()


async def test_empty_file_raises(monkeypatch):
    client, aio = _make_client(monkeypatch, [PAYLOAD])
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.mp3", b"")
        with pytest.raises(ModalWhisperError, match="Audio file is empty"):
            await _transcribe(client, audio)
    aio.assert_not_awaited()


async def test_not_found_error_is_config_error_with_deploy_command(monkeypatch):
    client, aio = _make_client(
        monkeypatch, [modal_exception.NotFoundError("no such class")], max_attempts=3
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.mp3", b"audio-bytes")
        with pytest.raises(ModalWhisperConfigError, match=r"modal deploy modal_apps/whisper_app\.py"):
            await _transcribe(client, audio)
    assert aio.await_count == 1  # no retry on lookup failure


async def test_auth_error_is_config_error_without_retry(monkeypatch):
    client, aio = _make_client(
        monkeypatch, [modal_exception.AuthError("bad token")], max_attempts=3
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.mp3", b"audio-bytes")
        with pytest.raises(ModalWhisperConfigError, match="MODAL_TOKEN_ID"):
            await _transcribe(client, audio)
    assert aio.await_count == 1


async def test_transient_connection_error_then_success(monkeypatch, caplog):
    client, aio = _make_client(
        monkeypatch, [modal_exception.ConnectionError("reset"), PAYLOAD]
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.mp3", b"audio-bytes")
        with caplog.at_level(
            logging.WARNING, logger="sow_analysis.services.modal_whisper_client"
        ):
            result = await _transcribe(client, audio)
    assert result == PAYLOAD
    assert aio.await_count == 2
    request_id = aio.await_args.kwargs["request_id"]
    assert any(
        "attempt 1/2" in r.getMessage() and request_id in r.getMessage()
        for r in caplog.records
    )


async def test_function_timeout_error_is_retryable(monkeypatch):
    client, aio = _make_client(
        monkeypatch, [modal_exception.FunctionTimeoutError("timed out"), PAYLOAD]
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.mp3", b"audio-bytes")
        result = await _transcribe(client, audio)
    assert result == PAYLOAD
    assert aio.await_count == 2


async def test_grpc_unavailable_then_success(monkeypatch):
    client, aio = _make_client(
        monkeypatch,
        [grpclib.GRPCError(grpclib.Status.UNAVAILABLE, "unavailable"), PAYLOAD],
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.mp3", b"audio-bytes")
        result = await _transcribe(client, audio)
    assert result == PAYLOAD
    assert aio.await_count == 2


async def test_grpc_invalid_argument_is_non_retryable(monkeypatch):
    client, aio = _make_client(
        monkeypatch,
        [grpclib.GRPCError(grpclib.Status.INVALID_ARGUMENT, "bad argument")],
        max_attempts=3,
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.mp3", b"audio-bytes")
        with pytest.raises(ModalWhisperError, match="transport error"):
            await _transcribe(client, audio)
    assert aio.await_count == 1


async def test_container_user_exception_is_non_retryable(monkeypatch):
    # Container exceptions re-raise as their original class (finding #14): a plain
    # RuntimeError must match the catch-all, not the retryable branches.
    client, aio = _make_client(
        monkeypatch, [RuntimeError("CUDA out of memory")], max_attempts=3
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.mp3", b"audio-bytes")
        with pytest.raises(ModalWhisperError) as exc_info:
            await _transcribe(client, audio)
    message = str(exc_info.value)
    assert "RuntimeError" in message
    assert "modal app logs" in message
    assert aio.await_count == 1


async def test_request_size_error_no_retry(monkeypatch):
    client, aio = _make_client(
        monkeypatch, [modal_exception.RequestSizeError("too big")], max_attempts=3
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.mp3", b"audio-bytes")
        with pytest.raises(ModalWhisperError, match="too large"):
            await _transcribe(client, audio)
    assert aio.await_count == 1


async def test_remote_error_retryable_then_success(monkeypatch):
    client, aio = _make_client(
        monkeypatch, [modal_exception.RemoteError("blob hiccup"), PAYLOAD]
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.mp3", b"audio-bytes")
        result = await _transcribe(client, audio)
    assert result == PAYLOAD
    assert aio.await_count == 2


async def test_remote_error_exhausts_attempts(monkeypatch):
    client, aio = _make_client(
        monkeypatch, [modal_exception.RemoteError("still broken")] * 2
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.mp3", b"audio-bytes")
        with pytest.raises(ModalWhisperError) as exc_info:
            await _transcribe(client, audio)
    message = str(exc_info.value)
    assert "after 2 attempt(s)" in message
    assert "RemoteError" in message
    assert aio.await_count == 2


async def test_execution_error_no_retry(monkeypatch):
    client, aio = _make_client(
        monkeypatch, [modal_exception.ExecutionError("deserialization")], max_attempts=3
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.mp3", b"audio-bytes")
        with pytest.raises(ModalWhisperError, match="execution failed"):
            await _transcribe(client, audio)
    assert aio.await_count == 1


def _write_test_flac(path: Path, duration: float, sr: int = 22050, noise: bool = True) -> Path:
    rng = np.random.default_rng(42)
    t = np.arange(int(sr * duration)) / sr
    # Noise-dominated sine: FLAC stays close to raw size (deterministic sizing),
    # unlike a pure sine which some encoders crush to a few percent.
    if noise:
        y = 0.4 * np.sin(2 * np.pi * 220.0 * t) + 0.6 * rng.uniform(-1.0, 1.0, t.shape)
    else:
        y = rng.uniform(-1.0, 1.0, t.shape)
    sf.write(str(path), y, sr, format="FLAC", subtype="PCM_16")
    return path


async def test_oversize_file_transcoded_to_16k_mono_wav(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        flac = _write_test_flac(Path(tmp) / "big.flac", duration=2.0)
        file_bytes = flac.read_bytes()
        # 16 kHz mono PCM-16 WAV = 32000 bytes/s + RIFF header.
        expected_wav_size = int(2.0 * 16000) * 2 + 44
        cap = (len(file_bytes) + expected_wav_size) // 2
        assert len(file_bytes) > cap > expected_wav_size

        client, aio = _make_client(monkeypatch, [PAYLOAD], max_audio_bytes=cap)
        result = await _transcribe(client, flac)

    assert result == PAYLOAD
    sent = aio.await_args.kwargs["audio_bytes"]
    assert sent != file_bytes
    assert sent[:4] == b"RIFF"  # transcoded to WAV
    assert len(sent) <= cap


async def test_incompressible_oversize_after_transcode_fails_before_modal(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        flac = _write_test_flac(Path(tmp) / "huge.flac", duration=6.0, noise=True)
        # 6 s @ 16 kHz mono PCM-16 = ~192 KB; 100 KB cap is unreachable by transcoding.
        client, aio = _make_client(monkeypatch, [PAYLOAD], max_audio_bytes=100_000)
        with pytest.raises(ModalWhisperError, match="16kHz mono transcode"):
            await _transcribe(client, flac)
    aio.assert_not_awaited()


async def test_no_client_side_timeout(monkeypatch):
    """A slow-but-succeeding call must return the payload — no wait_for wrapper."""

    async def slow_aio(**kwargs):
        await asyncio.sleep(0.2)
        return PAYLOAD

    aio = AsyncMock(side_effect=slow_aio)
    fake_cls = _FakeCls(aio)
    monkeypatch.setattr(modal.Cls, "from_name", staticmethod(lambda app, name: fake_cls))
    monkeypatch.setenv("MODAL_TOKEN_ID", "test-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "test-secret")
    client = ModalWhisperClient()

    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.mp3", b"audio-bytes")
        result = await _transcribe(client, audio)
    assert result == PAYLOAD


def test_singleton_getter_returns_same_object():
    # Wiring only: never use the singleton in the other tests (finding #20).
    assert get_modal_whisper_client() is get_modal_whisper_client()
