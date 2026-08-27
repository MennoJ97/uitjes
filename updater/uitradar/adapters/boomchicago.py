"""Boom Chicago.

WordPress, but with no event post type: /shows/ describes the resident shows in
prose and hands every actual date to a FareHarbor booking widget. There is
nothing dated in the HTML and nothing dated in wp-json either, so FareHarbor's
own read API is the record. Two endpoints of it are open:

    /api/v1/companies/boomchicago/items/                    the product list
    /api/v1/companies/boomchicago/items/<pk>/calendar/<yyyy>/<mm>/   its dates

Bespoke rather than `json_api` for three reasons:
  * the work is a fan-out — one calendar request per item per month — and the
    months have to be computed from today rather than written into config,
  * availabilities are buried in weeks[].days[].availabilities[], which the
    dotted `list_path` of json_api cannot flatten,
  * `items` is a product catalogue, not a programme: gift cards, donations,
    memberships and thirty-odd Academy classes sit next to the shows.

Everything else in that API needs a dashboard session — the company-wide
availability endpoint and every date-*range* variant 404 for us. That is why
this pays a request per item per month instead of asking once for everything,
and why `horizon_days` here is deliberately shorter than the site's 120: the
programme is a handful of resident shows playing most nights, so the far end of
the horizon adds requests without adding much a visitor would not already see.

Config keys: url, base_url, horizon_days, skip_name_prefixes, skip_item_pks,
default_venue, default_city, category_map (keyed on show titles — see
_to_event), fallback_categories
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from ..model import Event, classify, parse_dt

ITEMS_URL = "https://fareharbor.com/api/v1/companies/boomchicago/items/"
# book_url arrives as "/boomchicago/items/<pk>/availability/<pk>/book/" — the
# path under the embed root, not under the API.
BOOK_ROOT = "https://fareharbor.com/embeds/book"

# "[Community Night] Tight Five" is how the box office files it, not how anyone
# would say it. The tag is stripped from the title; it never carried a genre.
_BRACKET_TAG = re.compile(r"^\s*\[[^\]]+\]\s*")


def _months(horizon_days: int, today: date | None = None):
    """Every (year, month) touched by today .. today + horizon."""
    today = today or date.today()
    last = today + timedelta(days=horizon_days)
    year, month = today.year, today.month
    while (year, month) <= (last.year, last.month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def _is_show(item: dict, skip_prefixes: list[str], skip_pks: set[int]) -> bool:
    # is_retail is FareHarbor's own flag for a thing with no date: the gift
    # cards, memberships and donations all carry it, so it does the filtering
    # that would otherwise be a list of names to keep in sync.
    if item.get("is_retail") or item.get("is_archived") or item.get("is_private"):
        return False
    if item.get("is_unlisted") or item.get("is_rental"):
        return False
    if item.get("pk") in skip_pks:
        return False
    name = item.get("name") or ""
    return not any(name.startswith(p) for p in skip_prefixes)


def fetch(cfg: dict, http) -> list[Event]:
    items_url = cfg.get("url", ITEMS_URL)
    horizon = int(cfg.get("horizon_days", 60))
    skip_prefixes = list(cfg.get("skip_name_prefixes")
                         or ["[Academy]", "[Summer Intensive]"])
    skip_pks = {int(pk) for pk in cfg.get("skip_item_pks") or []}
    cmap = {k.lower(): v for k, v in (cfg.get("category_map") or {}).items()}
    base = cfg.get("base_url", BOOK_ROOT)

    catalogue = http.get_json(items_url, ttl=cfg.get("ttl")).get("items") or []
    items = [i for i in catalogue if _is_show(i, skip_prefixes, skip_pks)]

    out: list[Event] = []
    for item in items:
        for year, month in _months(horizon):
            payload = http.get_json(f"{items_url}{item['pk']}/calendar/"
                                    f"{year}/{month:02d}/", ttl=cfg.get("ttl"))
            for week in (payload.get("calendar") or {}).get("weeks") or []:
                for day in week.get("days") or []:
                    # Days spill over from the neighbouring months to fill the
                    # grid; taking them would double-count every boundary week.
                    if day.get("month") != "current":
                        continue
                    for ava in day.get("availabilities") or []:
                        ev = _to_event(ava, item, cfg, cmap, base)
                        if ev:
                            out.append(ev)
    return out


def _to_event(ava: dict, item: dict, cfg: dict, cmap: dict, base: str) -> Event | None:
    # Unlisted availabilities are the private hires and the school shows: they
    # exist so a link can be handed to one group, and are not open to walk-ins.
    if ava.get("is_unlisted"):
        return None
    start, all_day = parse_dt(ava.get("start_at"))
    if not start:
        return None

    title = _BRACKET_TAG.sub("", item.get("name") or "").strip()
    if not title:
        return None

    href = ava.get("book_url")
    return Event(
        title=title,
        start=start,
        end=parse_dt(ava.get("end_at"))[0],
        all_day=all_day,
        venue=cfg.get("default_venue", "Boom Chicago"),
        city=cfg.get("default_city", "Amsterdam"),
        # FareHarbor has no genre field, so the category is inferred from the
        # title. `category_map` therefore keys on whole show titles here, not
        # on genre labels: it is the override for the ones inference reads
        # wrong ("Sunday Night Live" is improv, not a concert).
        categories=classify(title, mapping=cmap, given=[title]),
        url=f"{base}{href}" if href else None,
        # The catalogue carries image_cdn_url, short_description and headline
        # for every item and Boom Chicago has left all three empty, so there is
        # no picture and no blurb to be had from this API.
        image=None,
        summary=None,
    )
