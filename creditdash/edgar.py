"""SEC EDGAR client.

Fair-access rules (https://www.sec.gov/os/accessing-edgar-data): every request carries a User-Agent naming
you and a contact email (SEC_USER_AGENT), and stays under 10 requests per second. We default to 5/s
(SEC_MAX_RPS) and back off on 429/503. Responses can be cached on disk; `max_age_s=None` keeps a cached
file forever, 0 always refetches.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import httpx

from .config import Settings
from .errors import SecError

WWW = "https://www.sec.gov"
DATA = "https://data.sec.gov"
TICKERS_URL = f"{WWW}/files/company_tickers.json"
MAX_ATTEMPTS = 4

_lock = threading.Lock()
_last = [0.0]


def submissions_url(cik: int) -> str:
    return f"{DATA}/submissions/CIK{cik:010d}.json"


def companyfacts_url(cik: int) -> str:
    return f"{DATA}/api/xbrl/companyfacts/CIK{cik:010d}.json"


def archive_url(cik: int, accession: str, doc: str) -> str:
    return f"{WWW}/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{doc}"


def instance_doc(primary_doc: str) -> str:
    """The XBRL instance SEC extracts from an inline-XBRL filing: crwv-20260630.htm -> crwv-20260630_htm.xml."""
    stem, _, ext = primary_doc.rpartition(".")
    return f"{stem}_{ext}.xml"


class Edgar:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.requests = 0
        self._client = httpx.Client(transport=transport, timeout=60, follow_redirects=True,
                                    headers={"User-Agent": settings.user_agent(),
                                             "Accept-Encoding": "gzip, deflate"})

    def json(self, url: str, cache: Path | None, max_age_s: float | None) -> dict:
        return json.loads(self._get(url, cache, max_age_s))

    def text(self, url: str, cache: Path | None, max_age_s: float | None) -> str:
        return self._get(url, cache, max_age_s).decode("utf-8", errors="replace")

    def _get(self, url: str, cache: Path | None, max_age_s: float | None) -> bytes:
        if cache is not None and cache.exists():
            age = time.time() - cache.stat().st_mtime
            if max_age_s is None or age < max_age_s:
                return cache.read_bytes()
        body = self._fetch(url)
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(cache.suffix + f".{os.getpid()}.tmp")
            tmp.write_bytes(body)
            tmp.replace(cache)
        return body

    def _fetch(self, url: str) -> bytes:
        for attempt in range(MAX_ATTEMPTS):
            self._throttle()
            self.requests += 1
            try:
                r = self._client.get(url)
            except httpx.TransportError as e:
                if attempt == MAX_ATTEMPTS - 1:
                    raise SecError(f"SEC unreachable: {url}: {e}") from e
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                return r.content
            if r.status_code in (429, 503) and attempt < MAX_ATTEMPTS - 1:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 403:
                raise SecError(f"SEC refused the request (403) for {url}. Check SEC_USER_AGENT: the SEC requires "
                               "'Your Name you@example.com'.")
            raise SecError(f"SEC returned {r.status_code} for {url}")
        raise SecError(f"SEC kept throttling {url}")

    def _throttle(self) -> None:
        gap = 1.0 / self.settings.sec_max_rps
        with _lock:
            wait = _last[0] + gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.monotonic()
