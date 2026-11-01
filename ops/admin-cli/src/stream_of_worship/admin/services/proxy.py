"""Rotating proxy support for YouTube requests.

Parses ``SOW_YOUTUBE_PROXY``, which may carry a port range
(``http://user:pass@host:10001-10010``) for rotating residential proxies, and
expands it into a pool of one proxy URL per port.

This module is the admin-CLI twin of
``ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py``
(``RotatingProxyConfig`` / ``_parse_proxy_pool`` / ``_redact_proxy_url``).
The two implementations MUST stay behaviourally identical: a change to the
notation in one package requires mirroring it in the other.
"""

import os
import random
import re

_PROXY_PORT_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")
_MAX_PROXY_POOL_SIZE = 64
_DEFAULT_PROXY_RETRIES = 10


class ProxyConfigError(RuntimeError):
    """Raised when ``SOW_YOUTUBE_PROXY`` is well-formed enough to parse but invalid.

    Subclasses :class:`RuntimeError` so existing command-level error handlers
    surface it as a normal failure instead of an unexpected crash.
    """


def parse_proxy_pool(proxy_url: str) -> list[str]:
    """Expand a proxy URL with a port range into a list of proxy URLs.

    Supports ``http://user:pass@host:10001-10010`` — expands to one URL per
    port (10001..10010 inclusive). A URL without a port range is returned as
    a single-element pool unchanged.

    Parsing deliberately avoids :mod:`urllib.parse` URL splitting: proxy
    passwords commonly contain characters (e.g. ``:``) that would break
    generic URL parsing. Instead only the trailing ``host:port`` segment is
    inspected.

    Raises:
        ProxyConfigError: if the range is inverted (low > high) or the
            resulting pool exceeds 64 URLs. The redacted URL is included in
            the message.
    """
    redacted = redact_proxy_url(proxy_url)
    rest, sep, hostport = proxy_url.rpartition("@")
    prefix = rest + sep  # "" when no "@": the whole string is hostport
    host, _, port_segment = hostport.rpartition(":")
    match = _PROXY_PORT_RANGE_RE.match(port_segment)
    if not match:
        return [proxy_url]
    low, high = int(match.group(1)), int(match.group(2))
    if low > high:
        raise ProxyConfigError(f"Invalid proxy port range (low > high): {redacted}")
    urls = [f"{prefix}{host}:{port}" for port in range(low, high + 1)]
    if len(urls) > _MAX_PROXY_POOL_SIZE:
        raise ProxyConfigError(
            f"Proxy port range too large ({len(urls)} ports, max {_MAX_PROXY_POOL_SIZE}): {redacted}"
        )
    return urls


def redact_proxy_url(proxy_url: str) -> str | None:
    """Redact userinfo credentials from a proxy URL for logging.

    Keeps scheme + host:port, replaces the userinfo part with ``***``.
    Returns ``"***"`` when the URL shape is unexpected (no ``@`` separator or
    no ``://``) so callers can render the redacted form unconditionally.
    """
    rest, sep, hostport = proxy_url.rpartition("@")
    if not sep or ":" not in hostport:
        return "***"
    scheme, _, _rest = rest.partition("://")
    if not _rest:  # no "://" — unexpected shape; redact everything
        return "***"
    return f"{scheme}://***@{hostport}"


def get_proxy_pool() -> list[str] | None:
    """Return the expanded proxy pool from ``SOW_YOUTUBE_PROXY``.

    Returns ``None`` when the variable is unset or blank, letting callers fall
    back to no proxy (or the system ``HTTPS_PROXY`` env var).

    Deliberately uncached: parsing at most 64 ports is free, and tests /
    long-running processes must observe fresh values.
    """
    value = os.environ.get("SOW_YOUTUBE_PROXY", "").strip()
    if not value:
        return None
    return parse_proxy_pool(value)


def get_proxy_retries() -> int:
    """Return the per-operation attempt budget from ``SOW_YOUTUBE_PROXY_RETRIES``.

    Unset or non-numeric values yield the default of 10 (matching the Analysis
    Service); ``"0"`` clamps to 1 so a misconfiguration cannot disable attempts
    entirely.
    """
    value = os.environ.get("SOW_YOUTUBE_PROXY_RETRIES", "").strip()
    if value.isdigit():
        return max(1, int(value))
    return _DEFAULT_PROXY_RETRIES


def choose_proxy(pool: list[str]) -> str:
    """Pick one proxy URL from ``pool`` at random."""
    return random.choice(pool)


class RotatingProxyConfig:
    """Proxy config backed by a pool of proxy URLs with rotation.

    Duck-types the ``proxy_config`` protocol of ``youtube_transcript_api`` 1.x.
    Each call to :meth:`to_requests_dict` picks a random URL from the pool, so
    every request may use a different proxy port — useful when individual ports
    of the pool have different success rates.

    Also sets:
    - ``prevent_keeping_connections_alive=True`` (triggers IP rotation per request)
    - ``retries_when_blocked=N`` (retries on HTTP 429, also triggers rotation)
    """

    def __init__(self, urls: list[str], retries_when_blocked: int = 3):
        if not urls:
            raise ValueError("RotatingProxyConfig requires at least one proxy URL")
        self._urls = list(urls)
        self._retries_when_blocked = retries_when_blocked

    def to_requests_dict(self) -> dict:
        url = random.choice(self._urls)
        return {"http": url, "https": url}

    @property
    def prevent_keeping_connections_alive(self) -> bool:
        return True

    @property
    def retries_when_blocked(self) -> int:
        return self._retries_when_blocked
