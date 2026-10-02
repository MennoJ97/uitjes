"""Ziggo Dome.

Next.js front end over a JSON API that the agenda page calls directly:
`/api/agenda/aankomend?limit=&offset=`. `limit` is capped at 50 server-side
regardless of what you ask for, so it is paged by offset.

Bespoke rather than `json_api` because of four site-specific shapes:
  * `showDate` is UTC with no marker on it: "2026-10-03 18:00:00" is the
    20:00 show (the timetable on the event page says so), and in winter a
    "19:00:00" is 20:00. Read as local, every show was listed two hours early
    in summer and one in winter. See _start().
  * `genres` is a JSON-*encoded string* nested inside the JSON,
  * there is no URL field — the detail page is built from the row's `id`,
  * the title is split across `performerName` and `event.title`, and either
    one alone reads badly ("Roxy Dekker" / "Live in de Ziggo Dome").

The long HTML `description` goes into `search`, not `summary`: it is where
"Studio Ghibli" lives for a show titled "Joe Hisaishi filmmuziek - Bijzonder
Groots", and too long to show on a card.

Config keys: url, page_size, max_pages, default_venue, default_city,
category_map, base_url
"""
from __future__ import annotations

import json
import re

from ..model import Event, classify, parse_dt

DEFAULT_URL = "https://www.ziggodome.nl/api/agenda/aankomend"


def fetch(cfg: dict, http) -> list[Event]:
    endpoint = cfg.get("url", DEFAULT_URL)
    size = int(cfg.get("page_size", 50))
    max_pages = int(cfg.get("max_pages", 20))
    base = cfg.get("base_url", "https://ziggodome.nl")
    cmap = {k.lower(): v for k, v in (cfg.get("category_map") or {}).items()}

    out: list[Event] = []
    for page in range(max_pages):
        payload = http.get_json(f"{endpoint}?limit={size}&offset={page * size}",
                                headers={"Accept": "application/json"}, ttl=cfg.get("ttl"))
        rows = payload.get("data") or []
        for row in rows:
            ev = _to_event(row, cfg, cmap, base)
            if ev:
                out.append(ev)
        if not (payload.get("pagination") or {}).get("hasMore") or not rows:
            break
    return out


def _title(row: dict) -> str:
    """`event.title` is often the tour, `performerName` the artist; neither is
    reliably the whole story, so combine unless one already contains the other."""
    title = ((row.get("event") or {}).get("title") or "").strip()
    performer = (row.get("performerName") or "").strip()
    if not performer:
        return title
    if not title:
        return performer
    if performer.lower() in title.lower():
        return title
    return f"{performer} — {title}"


_HAS_ZONE = re.compile(r"(Z|[+-]\d\d:?\d\d)$")


def _start(row: dict) -> tuple[str | None, bool]:
    """`showDate` as Amsterdam time, and whether the clock is unknown.

    The API sends UTC without saying so; the "Z" makes parse_dt convert it.
    Only added when the value carries no zone, so the day they start sending
    one this keeps working. When the stage time is not fixed the card shows
    the day alone, taken after conversion so it is the Dutch calendar day.
    """
    raw = (row.get("showDate") or "").strip()
    if not raw:
        return None, False
    if not _HAS_ZONE.search(raw):
        raw += "Z"
    start, _ = parse_dt(raw)
    if start and row.get("showTimeUnknown"):
        return start[:10], True
    return start, False


def _genres(row: dict) -> list[str]:
    raw = row.get("genres")
    if not raw:
        return []
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    return [g["name"] for g in items
            if isinstance(g, dict) and isinstance(g.get("name"), str)]


def _to_event(row: dict, cfg: dict, cmap: dict, base: str) -> Event | None:
    # `visible` is the CMS publish flag; private hires are not public events.
    if not row.get("visible") or row.get("privateEvent"):
        return None
    start, all_day = _start(row)
    if not start:
        return None

    title = _title(row)
    if not title:
        return None

    # The only image URL on offer is an Azure blob SAS link whose token has
    # already expired (403), and the container is private so stripping the
    # token 404s. Off by default rather than firing a failing request per card;
    # flip `include_images` if they ever start minting fresh tokens.
    image = None
    if cfg.get("include_images"):
        image = (row.get("artistImage") or {}).get("assetFileSas")
    return Event(
        title=title,
        start=start,
        # The site hides the clock when the stage time is not fixed yet.
        all_day=all_day,
        venue=cfg.get("default_venue", "Ziggo Dome"),
        city=cfg.get("default_city", "Amsterdam"),
        categories=classify(title, row.get("preview"), mapping=cmap, given=_genres(row)),
        url=f"{base}/events/{row['id']}" if row.get("id") else None,
        image=image if isinstance(image, str) else None,
        summary=row.get("preview"),
        search=row.get("description"),
    )
