"""Tests for the Modal downbeats client (services/modal_downbeats_client.py).

No network: modal.Cls.from_name is patched with a duck-typed fake handle, so
nothing here touches Modal. modal must be importable (runtime dep).
"""

import logging
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import modal
import modal.exception as modal_exception
import numpy as np
import pytest
import soundfile as sf

from sow_analysis.services.modal_downbeats_client import (
    ModalDownbeatsClient,
    ModalDownbeatsConfigError,
    ModalDownbeatsError,
)

BEATS_PAYLOAD = {
    "source": "madmom",
    "beats": [[0.464, 1], [0.928, 2]],
    "downbeats": [0.464],
    "detected_at": "2026-08-12T12:34:56.789000+00:00",
    "madmom_params": {"beats_per_bar": [3, 4], "fps": 100},
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

    `outcomes` is wired to the ``detect_beats`` remote method.
    """
    primary = AsyncMock(side_effect=list(outcomes))
    fake_cls = _FakeCls({"detect_beats": primary})
    monkeypatch.setattr(modal.Cls, "from_name", staticmethod(lambda app, name: fake_cls))
    monkeypatch.setenv("MODAL_TOKEN_ID", "test-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "test-secret")
    kwargs.setdefault("max_attempts", 2)
    kwargs.setdefault("retry_delay_seconds", 0.01)
    kwargs.setdefault("max_audio_bytes", 10_000_000)
    client = ModalDownbeatsClient(**kwargs)
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


async def test_detect_beats_success_passthrough(monkeypatch):
    client, aio = _make_client(monkeypatch, [BEATS_PAYLOAD])
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_test_wav(Path(tmp) / "a.wav", duration=0.1)
        result = await client.detect_beats(audio)

    assert result == BEATS_PAYLOAD
    assert aio.await_count == 1
    sent_kwargs = aio.await_args.kwargs
    assert sent_kwargs["filename"] == "a.wav"
    assert isinstance(sent_kwargs["audio_bytes"], bytes)
    request_id = sent_kwargs["request_id"]
    assert re.fullmatch(r"[0-9a-f]{12}", request_id)


async def test_transient_connection_error_then_success(monkeypatch, caplog):
    client, aio = _make_client(
        monkeypatch, [modal_exception.ConnectionError("reset"), BEATS_PAYLOAD]
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_test_wav(Path(tmp) / "a.wav", duration=0.1)
        with caplog.at_level(
            logging.WARNING, logger="sow_analysis.services.modal_downbeats_client"
        ):
            result = await client.detect_beats(audio)
    assert result == BEATS_PAYLOAD
    assert aio.await_count == 2
    request_id = aio.await_args.kwargs["request_id"]
    assert any(
        "attempt 1/2" in r.getMessage() and request_id in r.getMessage()
        for r in caplog.records
    )


async def test_missing_credentials_raises_config_error(monkeypatch):
    client, aio = _make_client(monkeypatch, [BEATS_PAYLOAD])
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_test_wav(Path(tmp) / "a.wav", duration=0.1)
        with pytest.raises(ModalDownbeatsConfigError, match="MODAL_TOKEN_ID"):
            await client.detect_beats(audio)
    aio.assert_not_awaited()


async def test_empty_file_raises(monkeypatch):
    client, aio = _make_client(monkeypatch, [BEATS_PAYLOAD])
    with tempfile.TemporaryDirectory() as tmp:
        audio = _write_audio(Path(tmp) / "a.mp3", b"")
        with pytest.raises(ModalDownbeatsError, match="Audio file is empty"):
            await client.detect_beats(audio)
    aio.assert_not_awaited()


async def test_incompressible_oversize_fails_before_modal(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        wav = _write_test_wav(Path(tmp) / "big.wav", duration=2.0)
        client, aio = _make_client(monkeypatch, [BEATS_PAYLOAD], max_audio_bytes=100)
        with pytest.raises(ModalDownbeatsError, match="SOW_MODAL_DOWNBEATS_MAX_AUDIO_BYTES"):
            await client.detect_beats(wav)
    aio.assert_not_awaited()


async def test_singleton_returns_same_instance(monkeypatch):
    import sow_analysis.services.modal_downbeats_client as m

    monkeypatch.setattr(m, "_client", None)
    c1 = m.get_modal_downbeats_client()
    c2 = m.get_modal_downbeats_client()
    assert c1 is c2
    monkeypatch.setattr(m, "_client", None)
