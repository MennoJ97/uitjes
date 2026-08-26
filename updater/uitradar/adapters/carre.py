"""Koninklijk Theater Carré.

The only venue here that needs bespoke code: its API returns a normalised
graph rather than a flat list. One request gives every production, each with
its individual performances, plus separate lookup tables for venues/halls and
for the CMS pages that carry the human-readable URL and title. The adapter's
job is the join.

Config keys:
  url             the render endpoint (default is the production list)
  default_venue / default_city
  category_map
"""
from __future__ import annotations

from ..model import Event, classify, parse_dt

DEFAULT_URL = "https://carre.nl/api/render/production-page-list-nl"


def fetch(cfg: dict, http) -> list[Event]:
    payload = http.get_json(cfg.get("url", DEFAULT_URL), ttl=cfg.get("ttl"))
    cmap = {k.lower(): v for k, v in (cfg.get("category_map") or {}).items()}
    base = cfg.get("base_url", "https://carre.nl")

    halls = _hall_names(payload.get("venues") or {})
    pages = _pages_by_production(payload.get("nodes") or {})

    out: list[Event] = []
    for pid, production in (payload.get("productions") or {}).items():
        page = pages.get(str(pid), {})
        title = page.get("title") or (production.get("data") or {}).get("name")
        if not title:
            continue
        href = page.get("url")

        for perf in production.get("events") or []:
            start, all_day = parse_dt(perf.get("start_date"))
            if not start:
                continue
            hall = halls.get(str(perf.get("hall_id")))
            out.append(Event(
                title=title,
                start=start,
                end=parse_dt(perf.get("end_date"))[0],
                all_day=all_day,
                venue=f"{cfg.get('default_venue', 'Koninklijk Theater Carré')}"
                      + (f" — {hall}" if hall else ""),
                city=cfg.get("default_city", "Amsterdam"),
                categories=classify(title, mapping=cmap),
                url=(base + href) if href else perf.get("sales_url"),
                summary=page.get("subheading") or None,
            ))
    return out


def _hall_names(venues: dict) -> dict[str, str]:
    names: dict[str, str] = {}
    for venue in venues.values():
        for hall in venue.get("halls") or []:
            label = (hall.get("data") or {}).get("name")
            if label:
                names[str(hall.get("id"))] = label
    return names


def _pages_by_production(nodes: dict) -> dict[str, dict]:
    """production_id -> {title, url, subheading} from the CMS page nodes."""
    pages: dict[str, dict] = {}
    for node in nodes.values():
        if node.get("kind") != "production-page":
            continue
        pid = node.get("production_id")
        if not pid:
            continue
        data = node.get("data") or {}
        pages[str(pid)] = {
            "title": data.get("title") or data.get("agendaTitle"),
            "url": data.get("url"),
            "subheading": data.get("subheading"),
        }
    return pages
