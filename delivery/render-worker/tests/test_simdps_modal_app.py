"""Exercise the SimDPS entry point in deploy and container import contexts."""

import runpy
from pathlib import Path
from unittest.mock import Mock

import modal
import pytest

APP_FILE = Path(__file__).resolve().parents[2] / "simdps-worker" / "modal_app.py"
IMAGE_REF = "ghcr.io/example/simdps-worker@sha256:" + "a" * 64


def test_container_import_registers_sampler_without_deploy_image(monkeypatch):
    monkeypatch.setattr(modal, "is_local", lambda: False)
    monkeypatch.delenv("SIMDPS_IMAGE", raising=False)
    monkeypatch.delenv("SIMDPS_MODAL_REGISTRY_SECRET", raising=False)
    from_registry = Mock()
    monkeypatch.setattr(modal.Image, "from_registry", from_registry)

    namespace = runpy.run_path(str(APP_FILE))

    assert isinstance(namespace["GlueSampler"], modal.Cls)
    from_registry.assert_not_called()


@pytest.mark.parametrize(
    "image_ref", [None, "", "ghcr.io/example/simdps-worker:latest", "@sha256:" + "a" * 64]
)
def test_deploy_requires_an_immutable_image_reference(monkeypatch, image_ref):
    monkeypatch.setattr(modal, "is_local", lambda: True)
    if image_ref is None:
        monkeypatch.delenv("SIMDPS_IMAGE", raising=False)
    else:
        monkeypatch.setenv("SIMDPS_IMAGE", image_ref)
    from_registry = Mock()
    monkeypatch.setattr(modal.Image, "from_registry", from_registry)

    with pytest.raises(ValueError, match="Set SIMDPS_IMAGE to an immutable image reference"):
        runpy.run_path(str(APP_FILE))

    from_registry.assert_not_called()


def test_startup_timeout_defaults_to_300_and_follows_the_deploy_environment(monkeypatch):
    monkeypatch.setattr(modal, "is_local", lambda: False)
    monkeypatch.delenv("SIMDPS_STARTUP_TIMEOUT_SECONDS", raising=False)
    assert runpy.run_path(str(APP_FILE))["startup_timeout_seconds"] == 300

    monkeypatch.setenv("SIMDPS_STARTUP_TIMEOUT_SECONDS", "120")
    assert runpy.run_path(str(APP_FILE))["startup_timeout_seconds"] == 120


@pytest.mark.parametrize("value", ["0", "3601", "abc"])
def test_deploy_rejects_an_invalid_startup_timeout(monkeypatch, value):
    monkeypatch.setenv("SIMDPS_STARTUP_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="SIMDPS_STARTUP_TIMEOUT_SECONDS"):
        runpy.run_path(str(APP_FILE))


def test_deploy_uses_the_pinned_image_and_registry_secret(monkeypatch):
    monkeypatch.setattr(modal, "is_local", lambda: True)
    monkeypatch.setenv("SIMDPS_IMAGE", IMAGE_REF)
    monkeypatch.setenv("SIMDPS_MODAL_REGISTRY_SECRET", "test-registry")
    from_registry = Mock(return_value=modal.Image.debian_slim())
    monkeypatch.setattr(modal.Image, "from_registry", from_registry)
    secret_from_name = Mock(wraps=modal.Secret.from_name)
    monkeypatch.setattr(modal.Secret, "from_name", secret_from_name)

    namespace = runpy.run_path(str(APP_FILE))

    assert isinstance(namespace["GlueSampler"], modal.Cls)
    from_registry.assert_called_once()
    assert from_registry.call_args.args == (IMAGE_REF,)
    assert isinstance(from_registry.call_args.kwargs["secret"], modal.Secret)
    secret_from_name.assert_any_call("test-registry")
