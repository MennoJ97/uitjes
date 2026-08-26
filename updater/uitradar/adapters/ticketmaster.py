"""Ticketmaster Discovery API v2 — the pragmatic way to cover the big rooms
(Ziggo Dome, AFAS Live, AFAS Theater, Johan Cruijff ArenA, plus a lot of
club and theatre inventory).

Needs a free key: https://developer.ticketmaster.com  ->  env TICKETMASTER_API_KEY
Free tier: 5000 calls/day, 5 req/s.

Config keys:
  city          list of city names, e.g. ["Amsterdam", "Utrecht"]
  country_code  default "NL"
  radius / unit optional geo widening
  size          page size (max 200), default 200
  max_pages     default 5  (Discovery caps deep paging at ~1000 items)
  classification optional segment filter, e.g. "Music"
  category_map  maps TM segment/genre names onto our taxonomy
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from ..model import Event, classify, parse_dt

ENDPOINT = "https://app.ticketmaster.com/discovery/v2/events.json"


class MissingKey(RuntimeError):
    pass


def fetch(cfg: dict, http) -> list[Event]:
    key = os.environ.get(cfg.get("api_key_env", "TICKETMASTER_API_KEY"), "").strip()
    if not key:
        raise MissingKey(
            "no TICKETMASTER_API_KEY in the environment — get a free key at "
            "developer.ticketmaster.com, then re-run"
        )

    cities = cfg.get("city") or [None]
    size = min(int(cfg.get("size", 200)), 200)
    max_pages = int(cfg.get("max_pages", 5))
    horizon = int(cfg.get("horizon_days", 120))
    cmap = {k.lower(): v for k, v in (cfg.get("category_map") or {}).items()}

    start = datetime.now().replace(microsecond=0)
    end = datetime.combine(date.today() + timedelta(days=horizon), datetime.min.time())

    out: list[Event] = []
    for city in cities:
        for page in range(max_pages):
            params = {
                "apikey": key,
                "countryCode": cfg.get("country_code", "NL"),
                "size": size,
                "page": page,
                "sort": "date,asc",
                "startDateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            if city:
                params["city"] = city
            if cfg.get("classification"):
                params["classificationName"] = cfg["classification"]
            if cfg.get("radius"):
                params["radius"] = cfg["radius"]
                params["unit"] = cfg.get("unit", "km")

            payload = http.get_json(f"{ENDPOINT}?{urlencode(params)}", ttl=cfg.get("ttl"))
            batch = ((payload.get("_embedded") or {}).get("events")) or []
            for raw in batch:
                ev = _to_event(raw, cfg, cmap)
                if ev:
                    out.append(ev)
            total_pages = ((payload.get("page") or {}).get("totalPages")) or 1
            if page + 1 >= total_pages or not batch:
                break
    return out


def _to_event(raw: dict, cfg: dict, cmap: dict) -> Event | None:
    dates = raw.get("dates") or {}
    start_block = dates.get("start") or {}
    start, all_day = parse_dt(
        start_block.get("dateTime")
        or (f"{start_block['localDate']}T{start_block['localTime']}"
            if start_block.get("localDate") and start_block.get("localTime")
            else start_block.get("localDate"))
    )
    if not start:
        return None

    venues = ((raw.get("_embedded") or {}).get("venues")) or []
    venue = venues[0] if venues else {}

    labels: list[str] = []
    for cls in raw.get("classifications") or []:
        for part in ("segment", "genre", "subGenre"):
            name = (cls.get(part) or {}).get("name")
            if name and name.lower() not in ("undefined", "other"):
                labels.append(name)

    images = sorted(
        [i for i in raw.get("images") or [] if i.get("url")],
        key=lambda i: i.get("width", 0), reverse=True,
    )
    image = next((i["url"] for i in images if i.get("width", 0) <= 1200), None) or (
        images[0]["url"] if images else None)

    price = None
    for pr in raw.get("priceRanges") or []:
        low, high = pr.get("min"), pr.get("max")
        if low is not None:
            price = f"€ {low:.0f}" if low == high or high is None else f"€ {low:.0f}–{high:.0f}"
        break

    return Event(
        title=raw.get("name") or "",
        start=start,
        all_day=all_day,
        end=parse_dt((dates.get("end") or {}).get("dateTime"))[0],
        venue=venue.get("name") or cfg.get("default_venue"),
        city=((venue.get("city") or {}).get("name")) or cfg.get("default_city"),
        categories=classify(raw.get("name"), mapping=cmap, given=labels),
        url=raw.get("url"),
        image=image,
        summary=raw.get("info") or raw.get("pleaseNote"),
        price=price,
    )
