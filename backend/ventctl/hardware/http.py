"""Minimal async HTTP helper (stdlib only, runs urllib in a worker thread)."""

from __future__ import annotations

import asyncio
import base64
import json
import urllib.error
import urllib.parse
import urllib.request

from .base import HardwareError


def _request(url: str, timeout: float, method: str, body: bytes | None, headers: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HardwareError(f"{urllib.parse.urlsplit(url).netloc}: {getattr(e, 'reason', e)}") from None


async def get_json(url: str, timeout: float = 4.0, user: str = "", password: str = "",
                   headers: dict | None = None, method: str = "GET", body: dict | None = None):
    h = dict(headers or {})
    if user:
        h["Authorization"] = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    status, raw = await asyncio.to_thread(_request, url, timeout, method, data, h)
    if status == 401:
        raise HardwareError(f"{url}: authentication required")
    if status >= 400:
        raise HardwareError(f"{url}: HTTP {status}")
    try:
        return json.loads(raw.decode("utf-8", "replace")) if raw.strip() else None
    except json.JSONDecodeError:
        raise HardwareError(f"{url}: response is not JSON") from None


def base_url(host: str) -> str:
    host = host.strip().rstrip("/")
    return host if host.startswith(("http://", "https://")) else f"http://{host}"
