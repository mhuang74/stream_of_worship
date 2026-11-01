"""Unit tests for the rotating-proxy notation used by SOW_YOUTUBE_PROXY.

Mirrors ops/analysis-service/tests/test_youtube_transcript.py so the two
implementations of the notation stay behaviourally identical.
"""

import random

import pytest

from stream_of_worship.admin.services.proxy import (
    ProxyConfigError,
    RotatingProxyConfig,
    choose_proxy,
    get_proxy_pool,
    get_proxy_retries,
    parse_proxy_pool,
    redact_proxy_url,
)


class TestParseProxyPool:
    """Tests for parse_proxy_pool."""

    def test_single_url_without_range_is_passthrough(self):
        url = "http://user:pass@host:10003"
        assert parse_proxy_pool(url) == [url]

    def test_port_range_expands_inclusive_ascending(self):
        pool = parse_proxy_pool("http://user:pass@host:10001-10010")

        assert len(pool) == 10
        assert pool[0] == "http://user:pass@host:10001"
        assert pool[-1] == "http://user:pass@host:10010"
        assert pool == [f"http://user:pass@host:{port}" for port in range(10001, 10011)]

    def test_password_containing_colons_is_preserved(self):
        pool = parse_proxy_pool("http://user:p:a:ss@host:10001-10002")

        assert pool == [
            "http://user:p:a:ss@host:10001",
            "http://user:p:a:ss@host:10002",
        ]

    def test_socks5_scheme_is_preserved(self):
        pool = parse_proxy_pool("socks5://user:pass@host:9000-9001")

        assert pool == ["socks5://user:pass@host:9000", "socks5://user:pass@host:9001"]

    def test_inverted_range_raises_redacted_error(self):
        with pytest.raises(ProxyConfigError) as exc:
            parse_proxy_pool("http://user:pass@h:10010-10001")

        assert str(exc.value) == "Invalid proxy port range (low > high): http://***@h:10010-10001"

    def test_range_above_cap_raises_redacted_error(self):
        with pytest.raises(ProxyConfigError) as exc:
            parse_proxy_pool("http://user:pass@h:1-65")

        assert str(exc.value) == "Proxy port range too large (65 ports, max 64): http://***@h:1-65"

    def test_range_at_cap_is_accepted(self):
        assert len(parse_proxy_pool("http://user:pass@h:1-64")) == 64

    def test_proxy_config_error_is_runtime_error(self):
        """Command handlers catch RuntimeError; the error must not escape as-is."""
        assert issubclass(ProxyConfigError, RuntimeError)


class TestRedactProxyUrl:
    """Tests for redact_proxy_url."""

    def test_redacts_userinfo_keeps_scheme_and_hostport(self):
        assert redact_proxy_url("http://user:pass@host:10001") == "http://***@host:10001"

    def test_schemeless_value_redacts_everything(self):
        assert redact_proxy_url("not-a-url") == "***"

    def test_userinfo_without_scheme_redacts_everything(self):
        assert redact_proxy_url("user:pass@host:10001") == "***"


class TestEnvReaders:
    """Tests for get_proxy_pool / get_proxy_retries."""

    def test_pool_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("SOW_YOUTUBE_PROXY", raising=False)
        assert get_proxy_pool() is None

    def test_pool_none_when_blank(self, monkeypatch):
        monkeypatch.setenv("SOW_YOUTUBE_PROXY", "   ")
        assert get_proxy_pool() is None

    def test_pool_expands_env_value(self, monkeypatch):
        monkeypatch.setenv("SOW_YOUTUBE_PROXY", "http://user:pass@h:10001-10003")
        assert get_proxy_pool() == [
            "http://user:pass@h:10001",
            "http://user:pass@h:10002",
            "http://user:pass@h:10003",
        ]

    def test_retries_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("SOW_YOUTUBE_PROXY_RETRIES", raising=False)
        assert get_proxy_retries() == 10

    def test_retries_read_from_env(self, monkeypatch):
        monkeypatch.setenv("SOW_YOUTUBE_PROXY_RETRIES", "3")
        assert get_proxy_retries() == 3

    def test_retries_clamped_to_one(self, monkeypatch):
        monkeypatch.setenv("SOW_YOUTUBE_PROXY_RETRIES", "0")
        assert get_proxy_retries() == 1

    def test_retries_non_numeric_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("SOW_YOUTUBE_PROXY_RETRIES", "abc")
        assert get_proxy_retries() == 10


class TestChooseProxy:
    """Tests for choose_proxy."""

    def test_returns_a_pool_member(self, monkeypatch):
        monkeypatch.setattr(random, "choice", lambda seq: seq[-1])
        assert choose_proxy(["http://a:1", "http://b:2"]) == "http://b:2"


class TestRotatingProxyConfig:
    """Tests for RotatingProxyConfig."""

    def test_empty_urls_raises(self):
        with pytest.raises(ValueError, match="at least one proxy URL"):
            RotatingProxyConfig(urls=[])

    def test_to_requests_dict_covers_http_and_https(self):
        config = RotatingProxyConfig(urls=["http://user:pass@h:10001"])

        assert config.to_requests_dict() == {
            "http": "http://user:pass@h:10001",
            "https": "http://user:pass@h:10001",
        }

    def test_to_requests_dict_picks_one_member_per_call(self, monkeypatch):
        calls = []

        def fake_choice(seq):
            calls.append(list(seq))
            return seq[len(calls) - 1]

        monkeypatch.setattr(random, "choice", fake_choice)
        config = RotatingProxyConfig(urls=["http://a:1", "http://b:2"])

        first = config.to_requests_dict()
        second = config.to_requests_dict()

        assert len(calls) == 2
        assert first["http"] == "http://a:1"
        assert second["http"] == "http://b:2"

    def test_rotates_by_default(self):
        config = RotatingProxyConfig(urls=["http://a:1", "http://b:2"])
        picks = {config.to_requests_dict()["http"] for _ in range(64)}
        assert picks == {"http://a:1", "http://b:2"}

    def test_retries_when_blocked_defaults_and_override(self):
        assert RotatingProxyConfig(urls=["http://a:1"]).retries_when_blocked == 3
        assert (
            RotatingProxyConfig(urls=["http://a:1"], retries_when_blocked=7).retries_when_blocked
            == 7
        )

    def test_prevent_keeping_connections_alive(self):
        assert RotatingProxyConfig(urls=["http://a:1"]).prevent_keeping_connections_alive is True
