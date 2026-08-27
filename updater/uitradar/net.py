"""Cached HTTP fetching. Stdlib only."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
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


# Worth a second go: the server said "busy" or "later", or the connection never
# finished. Everything else — 404, 403, a 500 that means the query is wrong — is
# an answer, and asking again just annoys someone else's server.
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class Http:
    def __init__(self, cache_dir: str, ttl: int = 3600, refresh: bool = False,
                 timeout: int = 25, delay: float = 0.4, retries: int = 2,
                 retry_backoff: float = 1.0, host_failure_limit: int = 4):
        self.cache_dir = cache_dir
        self.ttl = ttl
        self.refresh = refresh
        self.timeout = timeout
        self.delay = delay
        self.retries = max(0, int(retries))
        self.retry_backoff = float(retry_backoff)
        # The brake. A source like boomchicago makes ~200 requests, so a host
        # that is simply down would otherwise cost 200 x (1 + retries) x timeout
        # — over an hour of a build sitting on dead sockets. After this many
        # failures in a row against one host, further requests to it fail
        # immediately for the rest of the run. Any success resets the count, so
        # one flaky response does not disable a working host.
        self.host_failure_limit = max(1, int(host_failure_limit))
        self._host_failures: dict[str, int] = {}
        self.retried = 0
        self._last_call = 0.0
        os.makedirs(cache_dir, exist_ok=True)

    def _path(self, url: str, headers: dict, data: bytes | None = None) -> str:
        # `data` is part of the key: two POSTs to one GraphQL endpoint differ
        # only in their body, and without it page 2 would serve page 1 back.
        key = hashlib.sha1((url + json.dumps(sorted(headers.items()))).encode()
                           + (data or b"")).hexdigest()
        return os.path.join(self.cache_dir, key + ".json")

    def get(self, url: str, headers: dict | None = None, ttl: int | None = None,
            data: bytes | None = None) -> str:
        """GET, or POST when `data` is given — urllib picks the verb from it."""
        hdrs = dict(DEFAULT_HEADERS)
        hdrs.update(headers or {})
        ttl = self.ttl if ttl is None else ttl
        path = self._path(url, hdrs, data)

        if not self.refresh and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    blob = json.load(fh)
                if time.time() - blob["ts"] < ttl:
                    return blob["body"]
            except (OSError, ValueError, KeyError):
                pass

        req = urllib.request.Request(url, data=data, headers=hdrs)
        raw, enc = self._fetch(req, url)

        if enc == "gzip":
            raw = gzip.decompress(raw)
        elif enc == "deflate":
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        body = raw.decode("utf-8", errors="replace")

        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"url": url, "ts": time.time(), "body": body}, fh)
        return body

    def _throttle(self) -> None:
        """Be polite: leave a gap between live requests."""
        gap = time.time() - self._last_call
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last_call = time.time()

    def _fetch(self, req, url: str) -> tuple[bytes, str]:
        """One request, retried a bounded number of times on transient trouble.

        Melkweg timing out once took 227 events off the site on 2026-08-27; the
        build is designed to degrade rather than fail, which quietly published a
        thinner agenda. A couple of retries covers that without turning a real
        outage into a build that never ends — see host_failure_limit.
        """
        host = urllib.parse.urlsplit(url).netloc
        failures = self._host_failures.get(host, 0)
        if failures >= self.host_failure_limit:
            raise FetchError(f"{host} failed {failures} times in a row this run "
                             f"— not trying again for {url}")

        error = None
        # Only trouble that says "this host is unwell" counts toward the brake.
        # A 404 is the host answering correctly and promptly — a scraper walking
        # one page past the end of a paginated agenda must not be able to
        # blacklist the site for the rest of the run.
        transient = False
        for attempt in range(self.retries + 1):
            if attempt:
                # Capped so a slow host cannot stretch the build indefinitely.
                time.sleep(min(self.retry_backoff * 2 ** (attempt - 1), 8.0))
                self.retried += 1
            self._throttle()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    self._host_failures[host] = 0
                    return resp.read(), (resp.headers.get("Content-Encoding") or "").lower()
            except urllib.error.HTTPError as exc:
                error = FetchError(f"HTTP {exc.code} for {url}")
                transient = exc.code in RETRY_STATUS
                if not transient:
                    break
            except Exception as exc:  # noqa: BLE001 - network is messy, report and move on
                error = FetchError(f"{type(exc).__name__}: {exc} for {url}")
                transient = True

        if transient:
            self._host_failures[host] = failures + 1
        raise error

    def get_json(self, url: str, headers: dict | None = None, ttl: int | None = None,
                 data: bytes | None = None):
        hdrs = {"Accept": "application/json"}
        hdrs.update(headers or {})
        text = self.get(url, hdrs, ttl, data)
        try:
            return json.loads(text)
        except ValueError as exc:
            raise FetchError(f"non-JSON response from {url}: {text[:120]!r}") from exc
