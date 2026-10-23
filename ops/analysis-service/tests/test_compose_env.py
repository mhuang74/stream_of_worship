"""docker-compose.yml must forward every Settings field into the service container."""

from pathlib import Path

import pytest
import yaml

from sow_analysis.config import Settings

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.yml"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("service", ["analysis", "analysis-dev"])
def test_every_setting_is_exported(compose, service):
    """A field added to Settings must also be added to the x-common-env anchor.

    Compose only forwards keys written in the file; --env-file merely interpolates
    them. A missing key silently falls back to the config.py default.
    """
    environment = compose["services"][service]["environment"]
    missing = sorted(set(Settings.model_fields) - set(environment))
    assert missing == [], f"{service} does not forward: {missing}"


def test_trace_dump_url_derives_from_published_bind(compose):
    value = compose["services"]["analysis"]["environment"]["SOW_DEBUG_DUMP_PUBLIC_BASE_URL"]
    assert "SOW_BIND_IP" in value
    assert value.endswith(":8000}")