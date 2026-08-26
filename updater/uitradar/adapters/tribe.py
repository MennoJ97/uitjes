"""WordPress "The Events Calendar" REST API (namespace tribe/events/v1).

Config keys:
  base_url     site root, e.g. "https://www.uitamsterdam.com"
  per_page     items per request (default 50, max 50)
  max_pages    hard stop (default 20)
  horizon_days how far ahead to ask for (default from global config)
  category_map {"Muziek" = "concert", ...}
"""
from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlencode

from ..model import Event, classify, parse_dt


def fetch(cfg: dict, http) -> list[Event]:
    base = cfg["base_url"].rstrip("/")
    per_page = min(int(cfg.get("per_page", 50)), 50)
    max_pages = int(cfg.get("max_pages", 20))
    horizon = int(cfg.get("horizon_days", 120))
    cmap = {k.lower(): v for k, v in (cfg.get("category_map") or {}).items()}

    today = date.today()
    events: list[Event] = []
    for page in range(1, max_pages + 1):
        query = urlencode({
            "per_page": per_page,
            "page": page,
            "start_date": today.isoformat(),
            "end_date": (today + timedelta(days=horizon)).isoformat(),
        })
        payload = http.get_json(f"{base}/wp-json/tribe/events/v1/events?{query}",
                                ttl=cfg.get("ttl"))
        batch = payload.get("events") or []
        for raw in batch:
            ev = _to_event(raw, cfg, cmap)
            if ev:
                events.append(ev)
        if page >= int(payload.get("total_pages") or 1) or not batch:
            break
    return events


def _to_event(raw: dict, cfg: dict, cmap: dict) -> Event | None:
    start, all_day = parse_dt(raw.get("start_date"))
    if not start:
        return None
    end, _ = parse_dt(raw.get("end_date"))
    venue = (raw.get("venue") or {})
    cats = [c.get("name", "") for c in (raw.get("categories") or [])]
    tags = [t.get("name", "") for t in (raw.get("tags") or [])]

    return Event(
        title=raw.get("title") or "",
        start=start,
        end=end,
        all_day=bool(raw.get("all_day")) or all_day,
        venue=venue.get("venue") or cfg.get("default_venue"),
        city=venue.get("city") or cfg.get("default_city"),
        categories=classify(raw.get("title"), raw.get("excerpt"),
                            mapping=cmap, given=cats + tags),
        url=raw.get("url") or raw.get("website"),
        image=((raw.get("image") or {}).get("url") if isinstance(raw.get("image"), dict)
               else raw.get("image")) or None,
        summary=raw.get("excerpt") or raw.get("description"),
        price=raw.get("cost") or None,
    )
