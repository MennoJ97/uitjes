"""RSS feeds whose <content:encoded> holds a JSON record.

Some venues run a headless CMS in front of a ticketing backend and their
WordPress feed ends up carrying the whole production object as JSON. That is a
far better source than scraping the page — and on at least one site (Tivoli)
the feed answers even when the HTML pages sit behind a bot challenge.

Config keys:
  url            feed URL; may contain {page}
  pages          values substituted into {page}
  root_path      dotted path to the record inside the JSON (e.g. "production")
  prefer_timing  if the record has a timings list, use the entry whose
                 `timing_type_key` equals this, instead of fields.start
  timings_path / timing_type_key / timing_value_key
  fields         our field <- dotted path inside the record
                 (`url` defaults to the RSS <link>)
  price_min      dotted path to a nested structure; the smallest numeric
                 "price" found anywhere under it becomes the price label
"""
from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET

from ..model import Event, classify, parse_dt
from .json_api import _dig

CONTENT_NS = {"content": "http://purl.org/rss/1.0/modules/content/"}
_DECODER = json.JSONDecoder(strict=False)


def fetch(cfg: dict, http) -> list[Event]:
    template = cfg["url"]
    pages = cfg.get("pages") or [None]
    fields = cfg.get("fields") or {}
    if "title" not in fields:
        raise ValueError("rss_json needs at least `fields.title`")
    cmap = {k.lower(): v for k, v in (cfg.get("category_map") or {}).items()}

    out: list[Event] = []
    seen_links: set[str] = set()
    for page in pages:
        url = template.replace("{page}", str(page)) if page is not None else template
        body = http.get(url, ttl=cfg.get("ttl"))
        try:
            root = ET.fromstring(body.encode("utf-8", "replace"))
        except ET.ParseError:
            continue

        items = root.findall(".//item")
        if not items:
            break
        fresh = 0
        for item in items:
            link = (item.findtext("link") or "").strip()
            if link and link in seen_links:
                continue
            if link:
                seen_links.add(link)
            fresh += 1
            record = _record(item, cfg)
            if record is None:
                continue
            ev = _to_event(record, link, item, cfg, fields, cmap)
            if ev:
                out.append(ev)
        if not fresh:      # the feed started repeating itself — stop paging
            break
    return out


def _record(item, cfg: dict):
    raw = item.findtext("content:encoded", namespaces=CONTENT_NS) or ""
    raw = re.sub(r"</?p>", "", raw).strip()
    if not raw.startswith("{"):
        raw = html.unescape(raw).strip()
    if not raw.startswith("{"):
        return None
    try:
        payload, _ = _DECODER.raw_decode(raw)   # trailing HTML after the JSON
    except ValueError:
        return None
    return _dig(payload, cfg.get("root_path", ""))


def _min_price(node) -> str | None:
    """Smallest numeric `price` anywhere under `node`."""
    found: list[float] = []

    def walk(value):
        if isinstance(value, dict):
            for key, sub in value.items():
                if key == "price" and isinstance(sub, (int, float)):
                    found.append(float(sub))
                else:
                    walk(sub)
        elif isinstance(value, list):
            for sub in value:
                walk(sub)

    walk(node)
    if not found:
        return None
    low = min(found)
    return f"vanaf € {low:.2f}".replace(".00", ",-")


def _timing(record, cfg: dict) -> str | None:
    wanted = cfg.get("prefer_timing")
    if not wanted:
        return None
    rows = _dig(record, cfg.get("timings_path", "timings"))
    type_key = cfg.get("timing_type_key", "timingType")
    value_key = cfg.get("timing_value_key", "timing")
    for row in rows or []:
        if isinstance(row, dict) and row.get(type_key) == wanted:
            return row.get(value_key)
    return None


def _to_event(record, link: str, item, cfg: dict, fields: dict, cmap: dict) -> Event | None:
    get = lambda name: _dig(record, fields[name]) if name in fields else None  # noqa: E731

    start, all_day = parse_dt(_timing(record, cfg) or get("start"))
    if not start:
        # last resort: the date is often in the permalink as DD-MM-YYYY
        start, all_day = parse_dt(link)
    if not start:
        return None

    category = get("category")
    labels = [category] if isinstance(category, str) else list(category or [])
    price = _min_price(_dig(record, cfg["price_min"])) if cfg.get("price_min") else get("price")

    return Event(
        title=str(get("title") or item.findtext("title") or ""),
        start=start,
        end=parse_dt(get("end"))[0],
        all_day=all_day,
        venue=get("venue") or cfg.get("default_venue"),
        city=get("city") or cfg.get("default_city"),
        categories=classify(get("title"), get("summary"), mapping=cmap, given=labels),
        url=get("url") or link or None,
        image=get("image"),
        summary=get("summary"),
        price=price if isinstance(price, str) else None,
    )
