"""Shared HTTP fetch-through-cache with rate limiting and redacted errors."""

from __future__ import annotations

import os
import time

import httpx

from .cache import SECRET_PARAMS, CachedResponse, RawCache, cache_key


class FetchError(RuntimeError):
    pass


def _redact(params: dict | None) -> dict:
    return {k: ("***" if k.lower() in SECRET_PARAMS else v) for k, v in (params or {}).items()}


class CachedFetcher:
    def __init__(
        self,
        source: str,
        cache: RawCache,
        *,
        headers: dict | None = None,
        min_interval: float = 0.0,
        client: httpx.Client | None = None,
        offline: bool = False,
    ):
        self.source = source
        self.cache = cache
        self.min_interval = min_interval
        self.offline = offline
        self._client = client or httpx.Client(headers=headers or {}, timeout=60.0)
        self._last = 0.0

    def fetch(self, url: str, params: dict | None = None, endpoint: str | None = None,
              retries: int = 3) -> CachedResponse:
        endpoint = endpoint or url
        key = cache_key(self.source, endpoint, params)
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        if self.offline:
            raise FetchError(f"{self.source} {endpoint} {_redact(params)} not cached (offline)")
        last_err: Exception | None = None
        for attempt in range(retries):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                resp = self._client.get(url, params=params)
            except httpx.HTTPError as e:  # network; keep only the type so URLs/keys never leak
                last_err = FetchError(type(e).__name__)
                time.sleep(2**attempt)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last_err = FetchError(f"HTTP {resp.status_code}")
                time.sleep(2 ** (attempt + 1))
                continue
            if resp.status_code != 200:
                raise FetchError(
                    f"{self.source} {endpoint} {_redact(params)} -> HTTP {resp.status_code}"
                )
            meta = {
                "source": self.source,
                "endpoint": endpoint,
                "params": dict(params or {}),
                "content_type": resp.headers.get("content-type"),
            }
            return self.cache.put(key, resp.content, meta)
        raise FetchError(f"{self.source} {endpoint} {_redact(params)} failed: {last_err}")


QUOTES = "\"'“”‘’«»` "


def env_value(name: str, value: str | None = None) -> str | None:
    """Read a credential or header value from the environment, tolerating values
    pasted with smart quotes around them. Raises with a readable message (never
    echoing the value) if anything non-ASCII remains, since HTTP headers and
    query strings cannot carry it."""
    raw = value if value is not None else os.environ.get(name)
    if raw is None:
        return None
    cleaned = raw.strip().strip(QUOTES).strip()
    bad = sorted({f"U+{ord(c):04X}" for c in cleaned if not c.isascii()})
    if bad:
        raise RuntimeError(
            f"{name} contains non-ASCII characters ({', '.join(bad)}), often smart quotes "
            f"from copy-paste. Re-type it with plain quotes, e.g. export {name}='...'"
        )
    return cleaned or None
