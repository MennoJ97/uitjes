"""Weeztix / Eventix ticket shops — a whole programme in one anonymous GET.

For a venue whose agenda *is* its ticket shop: the site embeds
shop.weeztix.com and has no agenda of its own to read. Behind that embed is
openticket.tech, and one unauthenticated request returns the entire shop —
every event with an exact start and end, its room, and every ticket type:

  https://shop.api.openticket.tech/<shop guid>/data

The guid is in the embed URL on the venue's own ticket page. No key, no token,
no pagination, ~600 KB for a year of programming.

Why this needs code rather than json_api pointed at that URL:

  * The event `name` is a box-office string, not a title — "Woensdag 16
    september | 20:00 uur | Comedytrain Try-Out | €15". The show is one
    segment among the date, the door time and the price, and it is not always
    the same segment. See _title.
  * The price lives in a separate `tickets` collection, in cents, keyed by
    event, so the cheapest ticket has to be joined back on.
  * The city is inside a formatted postal address.

What the shop does NOT have is a per-event permalink: `?event=<guid>` is
client-side route state and a cold load of it just renders the list again
(checked). So every card links to `base_url`, which should be the venue's own
ticket page — the shop is embedded there anyway.

`status` is on every event ("normal" / "sold_out") and deliberately unused:
a sold-out show is still an evening out, and the model has nowhere to badge it.
It is the field to reach for if that ever changes.
"""
from __future__ import annotations

import re

from ..model import Event, classify, parse_dt

DATA_URL = "https://shop.api.openticket.tech/{shop}/data"

# The three kinds of segment a box office puts around the title.
_PRICE = re.compile(r"^(?:€|eur)\s*\d", re.I)
_CLOCK = re.compile(r"^\d{1,2}[:.]?\d{0,2}\s*(?:uur|u|am|pm)?$", re.I)
_WHEN = re.compile(
    r"\b(?:ma|di|wo|do|vr|za|zo)?(?:maandag|dinsdag|woensdag|donderdag|vrijdag"
    r"|zaterdag|zondag|monday|tuesday|wednesday|thursday|friday|saturday"
    r"|sunday)\b"
    r"|\b\d{1,2}\s+(?:jan|feb|mrt|maart|apr|mei|jun|jul|aug|sep|okt|oct|nov|dec)",
    re.I,
)
# "1014 DB Amsterdam" — the city is whatever follows a Dutch postcode.
_POSTCODE = re.compile(r"^\d{4}\s*[A-Z]{2}\s+", re.I)


def fetch(cfg: dict, http) -> list[Event]:
    shop = cfg.get("shop")
    if not shop:
        raise ValueError("weeztix needs `shop` — the GUID in the shop embed URL")
    url = (cfg.get("url") or DATA_URL).replace("{shop}", str(shop))
    payload = http.get_json(url, headers=cfg.get("headers"), ttl=cfg.get("ttl"))

    cmap = {k.lower(): v for k, v in (cfg.get("category_map") or {}).items()}
    cheapest = _cheapest_per_event(payload.get("tickets"))

    out: list[Event] = []
    for row in payload.get("events") or []:
        ev = _to_event(row, cfg, cmap, cheapest)
        if ev:
            out.append(ev)
    return out


def _cheapest_per_event(tickets) -> dict[str, int]:
    """Lowest `min_price` (in cents) per event, across its ticket types."""
    low: dict[str, int] = {}
    for ticket in (tickets or {}).values():
        if not isinstance(ticket, dict):
            continue
        event_id, cents = ticket.get("event_id"), ticket.get("min_price")
        if not event_id or not isinstance(cents, (int, float)) or isinstance(cents, bool):
            continue
        cents = int(cents)
        if event_id not in low or cents < low[event_id]:
            low[event_id] = cents
    return low


def _title(name: str | None) -> str:
    """Pull the show out of a pipe-separated box-office string.

    Drops the segments that are a date, a clock time or a price and keeps the
    rest, because which position the title sits in varies — "Zondag 20
    september | Echt Gebeurd | 16:00 uur | € 24" puts it second. If every
    segment looks like scaffolding the whole string is kept rather than
    publishing an empty title.
    """
    parts = [p.strip() for p in str(name or "").split("|")]
    kept = [p for p in parts
            if p and not _PRICE.match(p) and not _CLOCK.match(p) and not _WHEN.search(p)]
    return " | ".join(kept) if kept else str(name or "")


def _city(address: str | None) -> str | None:
    """The town out of a formatted address, via its postcode."""
    for part in reversed([p.strip() for p in str(address or "").split(",")]):
        match = _POSTCODE.match(part)
        if match:
            return part[match.end():].strip() or None
    return None


def _price(cents: int | None) -> str | None:
    """`vanaf € 24,-` / `vanaf € 17,50` — "vanaf" because it is the cheapest
    of the ticket types, and the shape the rest of the sources print."""
    if cents is None:
        return None
    label = f"{cents / 100:.2f}".replace(".", ",").replace(",00", ",-")
    return f"vanaf € {label}"


def _to_event(row: dict, cfg: dict, cmap: dict, cheapest: dict) -> Event | None:
    start, all_day = parse_dt(row.get("start"))
    if not start:
        return None

    location = row.get("location") or {}
    labels = [row.get("category"), *(row.get("subcategories") or [])]
    title = _title(row.get("name"))

    return Event(
        title=title,
        start=start,
        end=parse_dt(row.get("end"))[0],
        all_day=all_day,
        venue=location.get("name") or cfg.get("default_venue"),
        city=_city(location.get("address")) or cfg.get("default_city"),
        categories=classify(title, row.get("description"), mapping=cmap,
                            given=[str(x) for x in labels if x]),
        url=cfg.get("base_url"),
        summary=row.get("description"),
        price=_price(cheapest.get(row.get("guid"))),
    )
