"""schema.org JSON-LD scraper.

Most Dutch venue sites embed <script type="application/ld+json"> Event blocks,
either on the agenda index or (more often) on each detail page.

Config keys:
  urls          list of pages to scrape
  follow        regex; when set, matching hrefs on the index are fetched too
  follow_limit  max detail pages per index (default 60)
  default_venue / default_city  filled in when the markup omits them
  category_map  maps schema.org @type or the site's own labels
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from ..model import Event, classify, parse_dt

_LD_BLOCK = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_HREF = re.compile(r'href=["\']([^"\'#]+)["\']', re.I)


def fetch(cfg: dict, http) -> list[Event]:
    urls = list(cfg.get("urls") or ([cfg["url"]] if cfg.get("url") else []))
    if not urls:
        raise ValueError("jsonld source needs `url` or `urls`")

    follow = cfg.get("follow")
    limit = int(cfg.get("follow_limit", 60))
    pages: list[str] = []
    errors: list[Exception] = []

    for url in urls:
        try:
            body = http.get(url, ttl=cfg.get("ttl"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
            continue
        pages.append(body)
        if follow:
            pattern = re.compile(follow)
            seen: list[str] = []
            for href in _HREF.findall(body):
                full = urljoin(url, href)
                if pattern.search(full) and full not in seen:
                    seen.append(full)
            for detail in seen[:limit]:
                try:
                    pages.append(http.get(detail, ttl=cfg.get("ttl")))
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

    # The exception itself, not its text in a RuntimeError: a FetchError (site
    # down, queue page) is what lets carry_over keep the last published events.
    if not pages and errors:
        raise errors[0]

    cmap = {k.lower(): v for k, v in (cfg.get("category_map") or {}).items()}
    out: list[Event] = []
    for body in pages:
        for node in _iter_events(body):
            ev = _to_event(node, cfg, cmap)
            if ev:
                out.append(ev)
    return out


def _iter_events(body: str):
    for block in _LD_BLOCK.findall(body):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        yield from _walk(data)


def _walk(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        types = node.get("@type") or ""
        types = types if isinstance(types, list) else [types]
        if any(isinstance(t, str) and t.endswith("Event") for t in types):
            yield node
        for key in ("@graph", "subEvent", "event", "itemListElement", "item"):
            if key in node:
                yield from _walk(node[key])


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _to_event(node: dict, cfg: dict, cmap: dict) -> Event | None:
    start, all_day = parse_dt(node.get("startDate"))
    if not start:
        return None
    end, _ = parse_dt(node.get("endDate"))

    location = _first(node.get("location")) or {}
    venue = city = None
    if isinstance(location, dict):
        venue = location.get("name")
        address = location.get("address")
        if isinstance(address, dict):
            city = address.get("addressLocality")
        elif isinstance(address, str):
            city = address
    elif isinstance(location, str):
        venue = location

    image = _first(node.get("image"))
    if isinstance(image, dict):
        image = image.get("url") or image.get("contentUrl")

    offers = _first(node.get("offers")) or {}
    price = None
    if isinstance(offers, dict) and offers.get("price") not in (None, ""):
        price = f"{offers.get('priceCurrency', '€')} {offers['price']}".replace("EUR", "€")

    types = node.get("@type") or []
    types = types if isinstance(types, list) else [types]

    return Event(
        title=node.get("name") or "",
        start=start,
        end=end,
        all_day=all_day,
        venue=venue or cfg.get("default_venue"),
        city=city or cfg.get("default_city"),
        categories=classify(node.get("name"), node.get("description"),
                            mapping=cmap, given=[t for t in types if isinstance(t, str)]),
        url=node.get("url") or _first(node.get("sameAs")),
        image=image if isinstance(image, str) else None,
        summary=node.get("description"),
        price=price,
    )
