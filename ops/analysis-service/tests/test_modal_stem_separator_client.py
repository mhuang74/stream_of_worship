"""Tests for the Modal stem-separation client (services/modal_stem_separator_client.py).

No network: modal.Cls.from_name is patched with a duck-typed fake handle, so nothing
here touches Modal. modal must be importable (runtime dep).
"""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import grpclib
import modal
import modal.exception as modal_exception
import pytest

from sow_analysis.services.modal_stem_separator_client import (
    ModalStemSeparatorClient,
    ModalStemSeparatorConfigError,
    ModalStemSeparatorError,
    get_modal_stem_separator_client,
)

STEMS = {
    "vocals_dry": b"DRY",
    "vocals": b"VOC",
    "instrumental": b"INST",
}


class _FakeCls:
    """Duck-typed stand-in for the modal.Cls.from_name(...) result."""

    def __init__(self, aio):
        self._aio = aio
        self.calls = 0
        self.instance_kwargs: list[dict] = []

    def __call__(self, **kwargs):
        self.calls += 1
        self.instance_kwargs.append(kwargs)
        remote = SimpleNamespace(aio=self._aio)
        return SimpleNamespace(
            separate=SimpleNamespace(remote=remote),
            remove_reverb=SimpleNamespace(remote=remote),
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
    client = ModalStemSeparatorClient(**kwargs)
    return client, aio, fake_cls


def _write_input(path: Path, data: bytes = b"INPUT") -> Path:
    path.write_bytes(data)
    return path


async def test_separate_stems_writes_three_stems_in_dry_vocals_inst_order(monkeypatch):
    client, aio, _ = _make_client(monkeypatch, [dict(STEMS)])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        input_path = _write_input(root / "song.mp3")
        out = root / "out"
        result = await client.separate_stems(input_path, out)
        written = [p.read_bytes() if p else None for p in result]
        leftover = list(out.glob("*.part"))

    assert result == (
        out / "vocals_dry.flac",
        out / "vocals.flac",
        out / "instrumental.flac",
    )
    assert written == [b"DRY", b"VOC", b"INST"]
    assert leftover == []
    sent = aio.await_args.kwargs
    assert sent["audio_bytes"] == b"INPUT"
    assert sent["filename"] == "song.mp3"


async def test_missing_stem_payload_becomes_none(monkeypatch):
    client, _, _ = _make_client(monkeypatch, [{"vocals_dry": b"DRY", "vocals": b"", "instrumental": None}])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = await client.separate_stems(
            _write_input(root / "song.mp3"), root / "out"
        )

    assert result == (root / "out" / "vocals_dry.flac", None, None)


async def test_remove_reverb_returns_dry_and_none(monkeypatch):
    client, aio, _ = _make_client(monkeypatch, [{"vocals_dry": b"DRY"}])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        vocals = _write_input(root / "vocals.flac")
        dry, second = await client.remove_reverb(vocals, root / "out")
        assert dry == root / "out" / "vocals_dry.flac"
        assert dry.read_bytes() == b"DRY"
        assert list((root / "out").iterdir()) == [root / "out" / "vocals_dry.flac"]

    assert second is None
    assert aio.await_args.kwargs["vocals_bytes"] == b"INPUT"


async def test_missing_credentials_raise_config_error_before_modal(monkeypatch):
    client, aio, _ = _make_client(monkeypatch, [dict(STEMS)])
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with pytest.raises(ModalStemSeparatorConfigError, match="MODAL_TOKEN_ID"):
            await client.separate_stems(_write_input(root / "song.mp3"), root / "out")
    aio.assert_not_awaited()


async def test_oversize_input_fails_before_modal(monkeypatch):
    client, aio, _ = _make_client(monkeypatch, [dict(STEMS)], max_audio_bytes=10)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with pytest.raises(
            ModalStemSeparatorError, match="SOW_MODAL_STEM_MAX_AUDIO_BYTES"
        ):
            await client.separate_stems(
                _write_input(root / "song.mp3", b"x" * 11), root / "out"
            )
    aio.assert_not_awaited()


async def test_retryable_transport_error_then_success(monkeypatch):
    client, aio, _ = _make_client(
        monkeypatch,
        [grpclib.GRPCError(grpclib.Status.UNAVAILABLE, "try again"), dict(STEMS)],
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = await client.separate_stems(_write_input(root / "song.mp3"), root / "out")

    assert result[0] is not None
    assert aio.await_count == 2


async def test_non_retryable_grpc_status_fails_immediately(monkeypatch):
    client, aio, _ = _make_client(
        monkeypatch,
        [grpclib.GRPCError(grpclib.Status.INVALID_ARGUMENT, "bad input")] * 2,
        max_attempts=3,
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with pytest.raises(ModalStemSeparatorError, match="INVALID_ARGUMENT"):
            await client.separate_stems(_write_input(root / "song.mp3"), root / "out")
    assert aio.await_count == 1


async def test_exhausted_retries_report_attempt_count_and_app_name(monkeypatch):
    client, aio, _ = _make_client(
        monkeypatch, [modal_exception.RemoteError("still broken")] * 2
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with pytest.raises(ModalStemSeparatorError) as exc_info:
            await client.separate_stems(_write_input(root / "song.mp3"), root / "out")

    message = str(exc_info.value)
    assert "after 2 attempt(s)" in message
    assert "RemoteError" in message
    assert "modal app logs sow-stems" in message
    assert aio.await_count == 2


async def test_not_found_error_prints_deploy_command(monkeypatch):
    client, aio, _ = _make_client(
        monkeypatch, [modal_exception.NotFoundError("no such class")], max_attempts=3
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with pytest.raises(
            ModalStemSeparatorConfigError,
            match=r"modal deploy modal_apps/stem_separator_app\.py",
        ):
            await client.separate_stems(_write_input(root / "song.mp3"), root / "out")
    assert aio.await_count == 1


async def test_container_exception_surfaces_original_class(monkeypatch):
    """Container errors re-raise as their original class and must not be retried."""
    client, aio, _ = _make_client(
        monkeypatch, [RuntimeError("CUDA out of memory")], max_attempts=3
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with pytest.raises(ModalStemSeparatorError) as exc_info:
            await client.separate_stems(_write_input(root / "song.mp3"), root / "out")

    message = str(exc_info.value)
    assert "RuntimeError" in message
    assert "sow-stems" in message
    assert aio.await_count == 1


async def test_model_pair_selects_its_own_class_instance(monkeypatch):
    """Both model filenames are class parameters, so a new pair is a new handle."""
    client, aio, fake_cls = _make_client(
        monkeypatch,
        [dict(STEMS), dict(STEMS), dict(STEMS)],
        vocal_model="a.ckpt",
        dereverb_model="b.pth",
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        song = _write_input(root / "song.mp3")
        await client.separate_stems(song, root / "out1")
        await client.separate_stems(song, root / "out2")
        assert fake_cls.calls == 1

        client.dereverb_model = "c.pth"
        await client.separate_stems(song, root / "out3")

    assert fake_cls.calls == 2
    assert fake_cls.instance_kwargs == [
        {"vocal_model": "a.ckpt", "dereverb_model": "b.pth"},
        {"vocal_model": "a.ckpt", "dereverb_model": "c.pth"},
    ]


def test_singleton_getter_returns_same_object():
    # Wiring only: never use the singleton in the other tests.
    assert get_modal_stem_separator_client() is get_modal_stem_separator_client()
