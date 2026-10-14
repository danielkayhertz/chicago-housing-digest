"""HTTP access for adapters. Adapters depend on the Fetcher protocol so tests can use FakeFetcher."""
from __future__ import annotations

import json
import time
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)
BOT_UA = "chicago-housing-digest/0.1 (+https://github.com/danielkayhertz/chicago-housing-digest)"
BROWSER_HOSTS = ("chicago.gov",)


class FetchError(Exception):
    def __init__(self, url: str, status: int | None, message: str = ""):
        super().__init__(f"{status} {url} {message}".strip())
        self.url = url
        self.status = status


class NotFound(FetchError):
    pass


CHICAGO_CONTENT_PREFIX = "https://www.chicago.gov/content/city/en/"
CHICAGO_PUBLIC_PREFIX = "https://www.chicago.gov/city/en/"


def public_url(url: str) -> str:
    """chicago.gov 403s non-browser clients on /content/city/en/ paths but serves the same pages at /city/en/."""
    if url.startswith(CHICAGO_CONTENT_PREFIX):
        return CHICAGO_PUBLIC_PREFIX + url[len(CHICAGO_CONTENT_PREFIX):]
    return url


def url_key(url: str, params: dict | None = None) -> str:
    return str(httpx.URL(url, params=params))


class Fetcher(Protocol):
    def get_bytes(self, url: str, params: dict | None = None) -> bytes: ...
    def get_text(self, url: str, params: dict | None = None) -> str: ...
    def get_json(self, url: str, params: dict | None = None) -> Any: ...


class HttpFetcher:
    """Polite fetcher: per-host delay, retries on transport errors and 5xx, browser UA for chicago.gov."""

    def __init__(self, delay: float = 1.1, timeout: float = 90.0, retries: int = 2):
        self.client = httpx.Client(timeout=timeout, follow_redirects=True)
        self.delay = delay
        self.retries = retries
        self._last: dict[str, float] = {}

    def _headers(self, host: str) -> dict[str, str]:
        ua = BROWSER_UA if host.endswith(BROWSER_HOSTS) else BOT_UA
        return {"User-Agent": ua, "Accept": "*/*"}

    def get_bytes(self, url: str, params: dict | None = None) -> bytes:
        url = public_url(url)
        host = urlsplit(url).hostname or ""
        key = url_key(url, params)
        for attempt in range(self.retries + 1):
            wait = self._last.get(host, 0.0) + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()
            try:
                r = self.client.get(url, params=params, headers=self._headers(host))
            except httpx.TransportError as e:
                if attempt == self.retries:
                    raise FetchError(key, None, str(e)) from e
                continue
            if r.status_code == 404:
                raise NotFound(key, 404)
            if r.status_code >= 500 and attempt < self.retries:
                continue
            if r.status_code >= 400:
                raise FetchError(key, r.status_code)
            return r.content
        raise FetchError(key, None, "retries exhausted")

    def get_text(self, url: str, params: dict | None = None) -> str:
        return self.get_bytes(url, params).decode("utf-8", errors="replace")

    def get_json(self, url: str, params: dict | None = None) -> Any:
        body = self.get_bytes(url, params)
        try:
            return json.loads(body)
        except ValueError as e:  # JSONDecodeError and UnicodeDecodeError both subclass ValueError
            raise FetchError(url_key(public_url(url), params), None, f"invalid JSON: {e}") from e
