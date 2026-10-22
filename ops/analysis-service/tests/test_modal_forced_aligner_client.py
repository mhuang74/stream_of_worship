"""Tests for the Modal forced-alignment client (services/modal_forced_aligner_client.py).

No network: modal.Cls.from_name is patched with a duck-typed fake handle, so
nothing here touches Modal. modal must be importable (runtime dep).
"""

import logging
import re
import struct
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import modal
import modal.exception as modal_exception
import numpy as np
import pytest
import soundfile as sf

from sow_analysis.services.modal_forced_aligner_client import (
    ModalForcedAlignerClient,
    ModalForcedAlignerConfigError,
    ModalForcedAlignerError,
    get_modal_forced_aligner_client,
)

PAYLOAD = [
    {"start": 0.0, "end": 2.0, "text": "主是我力量"},
    {"start": 2.1, "end": 4.0, "text": "是我盾牌"},
]


class _FakeCls:
    """Duck-typed stand-in for modal.Cls.from_name(...) result."""

    def __init__(self, aio):
        self._aio = aio
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return SimpleNamespace(align=SimpleNamespace(remote=SimpleNamespace(aio=self._aio)))


def _make_client(monkeypatch, outcomes, **kwargs):
    """Fresh client + fake handle per case (never the module singleton)."""
    aio = AsyncMock(side_effect=list(outcomes))
    fake_cls = _FakeCls(aio)
    monkeypatch.setattr(modal.Cls, "from_name", staticmethod(lambda app, name: fake_cls))
    monkeypatch.setenv("MODAL_TOKEN_ID", "test-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "test-secret")
    kwargs.setdefault("max_attempts", 2)
    kwargs.setdefault("retry_delay_seconds", 0.01)
    client = ModalForcedAlignerClient(**kwargs)
    return client, aio


def _write_audio(path: Path, seconds: float = 0.25, sr: int = 8000) -> Path:
    rng = np.random.default_rng(7)
    t = np.arange(int(sr * seconds)) / sr
    y = 0.3 * np.sin(2 * np.pi * 220.0 * t) + 0.2 * rng.uniform(-1.0, 1.0, t.shape)
    sf.write(str(path), y, sr, subtype="PCM_16")
    return path


def _wav_format(wav: bytes) -> tuple[int, int, int, int]:
    """Return (audio_format, channels, sample_rate, bits) from a RIFF/WAVE header."""
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[12:16] == b"fmt "
    audio_format, channels, sample_rate, _byte_rate, _align, bits = struct.unpack(
        "<HHIIHH", wav[20:36]
    )
    return audio_format, channels, sample_rate, bits


async def test_success_returns_tuples_and_sends_16k_mono_wav(monkeypatch):
    client, aio = _make_client(monkeypatch, [PAYLOAD])
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.wav")
        result = await client.align(audio, "主是我力量\n是我盾牌", "Chinese")

    assert result == [(0.0, 2.0, "主是我力量"), (2.1, 4.0, "是我盾牌")]
    assert aio.await_count == 1
    sent = aio.await_args.kwargs
    assert _wav_format(sent["audio_wav"]) == (1, 1, 16000, 16)
    assert sent["lyrics_text"] == "主是我力量\n是我盾牌"
    assert sent["language"] == "Chinese"
    request_id = sent["request_id"]
    assert len(request_id) == 12
    assert re.fullmatch(r"[0-9a-f]{12}", request_id)


async def test_success_logs_total_modal_elapsed(monkeypatch, caplog):
    client, aio = _make_client(monkeypatch, [PAYLOAD])
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.wav")
        with caplog.at_level(
            logging.INFO, logger="sow_analysis.services.modal_forced_aligner_client"
        ):
            await client.align(audio, "主是我力量", "Chinese")
    request_id = aio.await_args.kwargs["request_id"]
    assert any(
        re.search(
            r"Forced alignment total Modal elapsed time: .*attempts=1/2.*segments=2.*"
            + request_id,
            r.getMessage(),
        )
        for r in caplog.records
    )


async def test_missing_credentials_raises_config_error(monkeypatch):
    client, aio = _make_client(monkeypatch, [PAYLOAD])
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.wav")
        with pytest.raises(ModalForcedAlignerConfigError, match="MODAL_TOKEN_ID"):
            await client.align(audio, "主是我力量", "Chinese")
    aio.assert_not_awaited()


async def test_empty_file_raises_before_modal(monkeypatch):
    client, aio = _make_client(monkeypatch, [PAYLOAD])
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / "empty.wav"
        audio.write_bytes(b"")
        with pytest.raises(ModalForcedAlignerError, match="Audio file is empty"):
            await client.align(audio, "主是我力量", "Chinese")
    aio.assert_not_awaited()


async def test_oversize_after_transcode_fails_before_modal(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.wav", seconds=2.0)
        # 2 s @ 16 kHz mono PCM-16 = ~64 KB, so a 10 KB cap is unreachable.
        client, aio = _make_client(monkeypatch, [PAYLOAD], max_audio_bytes=10_000)
        with pytest.raises(
            ModalForcedAlignerError,
            match="SOW_MODAL_FORCED_ALIGNER_MAX_AUDIO_BYTES|Audio too large for Modal blob upload",
        ):
            await client.align(audio, "主是我力量", "Chinese")
    aio.assert_not_awaited()


async def test_retryable_failure_then_success_logs_recovery(monkeypatch, caplog):
    client, aio = _make_client(
        monkeypatch, [modal_exception.ConnectionError("reset"), PAYLOAD]
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.wav")
        with caplog.at_level(
            logging.INFO, logger="sow_analysis.services.modal_forced_aligner_client"
        ):
            result = await client.align(audio, "主是我力量", "Chinese")
    assert result == [(0.0, 2.0, "主是我力量"), (2.1, 4.0, "是我盾牌")]
    assert aio.await_count == 2
    request_id = aio.await_args.kwargs["request_id"]
    messages = [r.getMessage() for r in caplog.records]
    assert any("attempt 1/2" in m and request_id in m for m in messages)
    assert any("recovered" in m and request_id in m for m in messages)


async def test_exhausted_retries_raise_with_request_id(monkeypatch, caplog):
    client, aio = _make_client(
        monkeypatch, [modal_exception.RemoteError("still broken")] * 2
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.wav")
        with caplog.at_level(
            logging.INFO, logger="sow_analysis.services.modal_forced_aligner_client"
        ):
            with pytest.raises(ModalForcedAlignerError) as exc_info:
                await client.align(audio, "主是我力量", "Chinese")
    message = str(exc_info.value)
    request_id = aio.await_args.kwargs["request_id"]
    assert "after 2 attempt(s)" in message
    assert request_id in message
    assert "RemoteError" in message
    assert aio.await_count == 2
    assert any(
        "Forced alignment total Modal elapsed time" in r.getMessage()
        and "failed: RemoteError" in r.getMessage()
        for r in caplog.records
    )


async def test_not_found_error_is_config_error_with_deploy_command(monkeypatch):
    client, aio = _make_client(
        monkeypatch, [modal_exception.NotFoundError("no such class")], max_attempts=3
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.wav")
        with pytest.raises(
            ModalForcedAlignerConfigError,
            match=r"modal deploy modal_apps/forced_aligner_app\.py",
        ):
            await client.align(audio, "主是我力量", "Chinese")
    assert aio.await_count == 1  # no retry on lookup failure


async def test_container_user_exception_surfaces_class_name(monkeypatch):
    # Container exceptions re-raise as their original class: a plain RuntimeError must
    # match the catch-all, not the retryable branches.
    client, aio = _make_client(
        monkeypatch, [RuntimeError("CUDA out of memory")], max_attempts=3
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.wav")
        with pytest.raises(ModalForcedAlignerError) as exc_info:
            await client.align(audio, "主是我力量", "Chinese")
    message = str(exc_info.value)
    assert "RuntimeError" in message
    assert "sow-forced-aligner" in message
    assert "modal app logs" in message
    assert aio.await_count == 1


async def test_single_cached_instance_across_calls(monkeypatch):
    """The model is a module constant, so the class handle is instantiated once."""
    aio = AsyncMock(side_effect=[PAYLOAD, PAYLOAD])
    fake_cls = _FakeCls(aio)
    monkeypatch.setattr(modal.Cls, "from_name", staticmethod(lambda app, name: fake_cls))
    monkeypatch.setenv("MODAL_TOKEN_ID", "test-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "test-secret")
    client = ModalForcedAlignerClient(max_attempts=2, retry_delay_seconds=0.01)
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.wav")
        await client.align(audio, "主是我力量", "Chinese")
        await client.align(audio, "主是我力量", "Chinese")
    assert fake_cls.calls == 1


def test_singleton_getter_returns_same_object():
    # Wiring only: never use the singleton in the other tests.
    assert get_modal_forced_aligner_client() is get_modal_forced_aligner_client()
