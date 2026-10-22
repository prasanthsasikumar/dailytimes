"""Outbound HTTP for the auto edition, hardened for a multi-user server.

User-supplied feed URLs are fetched server-side, so they must not reach internal
services (SSRF): only http(s), only public IP addresses, redirects re-checked,
bounded size and time. Requests carry no cookies and nothing about the user.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = "DailyTimes/0.2 (+https://github.com/dailytimes/dailytimes; personal news brief)"
MAX_BYTES = 2_000_000
TIMEOUT = httpx.Timeout(8.0, connect=4.0)


class FetchError(RuntimeError):
    pass


def _public_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise FetchError(f"can't resolve {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise FetchError(f"{host} resolves to a non-public address")


def get(url: str, *, client: httpx.Client | None = None, untrusted: bool = False, max_redirects: int = 3,
        params: dict | None = None) -> httpx.Response:
    """GET with a size cap. `untrusted=True` for user-supplied URLs (SSRF checks on every hop)."""
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=False)
    try:
        for _ in range(max_redirects + 1):
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise FetchError(f"refusing to fetch {url!r}")
            if untrusted:
                _public_host(parts.hostname)
            with client.stream("GET", url, params=params) as r:
                if r.is_redirect and "location" in r.headers:
                    url, params = urljoin(url, r.headers["location"]), None
                    continue
                body = b""
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise FetchError(f"{parts.hostname}: response too large")
                r._content = body  # make .text/.json() work after streaming
                if r.status_code >= 400:
                    raise FetchError(f"{parts.hostname}: HTTP {r.status_code}")
                return r
        raise FetchError("too many redirects")
    except httpx.HTTPError as e:
        raise FetchError(str(e)) from e
    finally:
        if own:
            client.close()
