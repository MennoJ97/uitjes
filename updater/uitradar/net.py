"""Cached HTTP fetching. Stdlib only."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import zlib

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "close",
}


class FetchError(RuntimeError):
    pass


class Http:
    def __init__(self, cache_dir: str, ttl: int = 3600, refresh: bool = False,
                 timeout: int = 25, delay: float = 0.4):
        self.cache_dir = cache_dir
        self.ttl = ttl
        self.refresh = refresh
        self.timeout = timeout
        self.delay = delay
        self._last_call = 0.0
        os.makedirs(cache_dir, exist_ok=True)

    def _path(self, url: str, headers: dict) -> str:
        key = hashlib.sha1((url + json.dumps(sorted(headers.items()))).encode()).hexdigest()
        return os.path.join(self.cache_dir, key + ".json")

    def get(self, url: str, headers: dict | None = None, ttl: int | None = None) -> str:
        hdrs = dict(DEFAULT_HEADERS)
        hdrs.update(headers or {})
        ttl = self.ttl if ttl is None else ttl
        path = self._path(url, hdrs)

        if not self.refresh and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    blob = json.load(fh)
                if time.time() - blob["ts"] < ttl:
                    return blob["body"]
            except (OSError, ValueError, KeyError):
                pass

        # be polite: throttle between live requests
        gap = time.time() - self._last_call
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last_call = time.time()

        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
        except urllib.error.HTTPError as exc:
            raise FetchError(f"HTTP {exc.code} for {url}") from exc
        except Exception as exc:  # noqa: BLE001 - network is messy, report and move on
            raise FetchError(f"{type(exc).__name__}: {exc} for {url}") from exc

        if enc == "gzip":
            raw = gzip.decompress(raw)
        elif enc == "deflate":
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        body = raw.decode("utf-8", errors="replace")

        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"url": url, "ts": time.time(), "body": body}, fh)
        return body

    def get_json(self, url: str, headers: dict | None = None, ttl: int | None = None):
        hdrs = {"Accept": "application/json"}
        hdrs.update(headers or {})
        text = self.get(url, hdrs, ttl)
        try:
            return json.loads(text)
        except ValueError as exc:
            raise FetchError(f"non-JSON response from {url}: {text[:120]!r}") from exc
