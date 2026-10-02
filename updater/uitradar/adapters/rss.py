"""RSS/Atom reader. Weakest adapter — feeds rarely carry a real event date, so
it tries the title/description first and only falls back to pubDate.

Config keys: url, date_field ("auto" | "pubdate"), default_venue, default_city
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..model import Event, classify, parse_dt, to_local

NS = {"atom": "http://www.w3.org/2005/Atom"}


def fetch(cfg: dict, http) -> list[Event]:
    body = http.get(cfg["url"], ttl=cfg.get("ttl"))
    root = ET.fromstring(body.encode("utf-8", "replace"))
    cmap = {k.lower(): v for k, v in (cfg.get("category_map") or {}).items()}
    mode = (cfg.get("date_field") or "auto").lower()

    items = root.findall(".//item") or root.findall(".//atom:entry", NS)
    out: list[Event] = []
    for item in items:
        title = _text(item, "title") or _text(item, "atom:title")
        desc = _text(item, "description") or _text(item, "atom:summary")
        link = _text(item, "link") or _attr(item, "atom:link", "href")
        cats = [c.text for c in item.findall("category") if c.text]

        start = None
        if mode == "auto":
            start = parse_dt(title)[0] or parse_dt(desc)[0]
        if not start:
            raw = _text(item, "pubDate") or _text(item, "atom:updated")
            if raw:
                try:
                    # pubDate carries its zone ("+0000", "GMT"); convert, not
                    # just strip it, or a UTC feed reads two hours early.
                    start = to_local(parsedate_to_datetime(raw)).isoformat(
                        timespec="minutes")
                except (TypeError, ValueError):
                    start = parse_dt(raw)[0]
        if not start:
            continue

        out.append(Event(
            title=title or "",
            start=start,
            venue=cfg.get("default_venue"),
            city=cfg.get("default_city"),
            categories=classify(title, desc, mapping=cmap, given=cats),
            url=link,
            summary=desc,
        ))
    return out


def _text(node, path):
    found = node.find(path, NS) if ":" in path else node.find(path)
    return (found.text or "").strip() if found is not None and found.text else None


def _attr(node, path, attr):
    found = node.find(path, NS)
    return found.get(attr) if found is not None else None
