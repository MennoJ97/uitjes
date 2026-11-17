"""Last-resort HTML scraper for sites with no feed and no JSON-LD.

You give it a regex with named groups; everything lives in sources.toml so a
site redesign is a config fix, not a code change. Use `build.py --probe <id>`
to iterate on the pattern.

Config keys:
  urls          pages to scrape (may include {page} — see pages)
  pages         optional list of values substituted into {page}
  item_pattern  regex with named groups: title, date (required),
                url, venue, time, end, image, summary, category, price
                (optional)
  flags         "is" (default) -> re.I | re.S
  base_url      for turning relative hrefs absolute
  headers       extra request headers, e.g. a User-Agent for a site that turns
                away the default one
  default_title the title for a page that only lists dates (a quiz API, a
                sitemap of one recurring night)
  link          what a card without a url group links to, when the page read
                is not one to send people to (an API); default the page itself
  default_time  "HH:MM" for cards that print a date and no clock, when the
                venue states the start once for all of them
  default_price "gratis" for a venue whose nights are all free and say so
                nowhere on the card (SPUI25: free with registration)
  price_map     the price group's text (any case) -> what to show: De
                Zwijger's button says "Reserveer" (free, booking only) or
                "Koop kaarten"; an empty value shows no price at all
  stop_on_page_error
                as in json_api: a page that fails after one has been read
                ends the walk instead of failing the source (Waterhole's
                /gigs/page/3/ is a 404 until the programme grows)
  unescape_json decode JSON's backslash-u escapes in every captured group,
                for a pattern run over JSON that sits in a page as text
                (Q-Factory's Next.js payload writes "&" as backslash-u0026);
                the pattern has to let the escapes into its groups
  day_month_dates
                the date is a bare "09.11" (day.month, no year, Het Pothuys):
                give it this year, or next year's when that day is more than
                60 days gone, the rule parse_dt applies to "5 jan"
  detail_patterns
                regexes run over an event's own page, for a card that has a
                date but no clock (AFAS Live prints the timetable only there).
                Named groups time and end; the first pattern that matches a
                group wins, so list the preferred line first. Only for events
                within detail_days (default 60) - times are announced late, so
                fetching a show months out costs a request and finds nothing -
                and only for a url that one card links to (a page shared by
                two dates cannot say which clock belongs to which).
  dated_replaces_weekly
                a card whose date is a standing night ("Elke dinsdag, vanaf
                20:00") becomes one event a week to the horizon; with this set,
                a dated card at the same venue on the same day takes that
                night's place - a themed edition instead of the regular one
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta
from urllib.parse import urljoin

from ..model import Event, classify, parse_dt, parse_range, parse_weekly
from ..net import FetchError

_FLAGS = {"i": re.I, "s": re.S, "m": re.M, "x": re.X}
_JSON_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_DAY_MONTH = re.compile(r"^\s*(\d{1,2})[./-](\d{1,2})\s*$")


def _with_year(text: str | None) -> str | None:
    """ "09.11" as "09-11-2026" (see day_month_dates); anything else as is."""
    match = _DAY_MONTH.match(text or "")
    if not match:
        return text
    day, month, today = int(match.group(1)), int(match.group(2)), date.today()
    try:
        year = today.year + ((today - date(today.year, month, day)).days > 60)
    except ValueError:
        return text
    return f"{day:02d}-{month:02d}-{year}"


def fetch(cfg: dict, http) -> list[Event]:
    flags = 0
    for ch in cfg.get("flags", "is"):
        flags |= _FLAGS.get(ch, 0)
    pattern = re.compile(cfg["item_pattern"], flags)

    urls: list[str] = []
    for template in cfg.get("urls") or [cfg["url"]]:
        if "{page}" in template:
            urls.extend(template.replace("{page}", str(p)) for p in cfg.get("pages", [1]))
        else:
            urls.append(template)

    cmap = {k.lower(): v for k, v in (cfg.get("category_map") or {}).items()}
    base = cfg.get("base_url") or urls[0]
    out: list[Event] = []
    weekly: list[Event] = []

    read_one = False
    for url in urls:
        try:
            body = http.get(url, headers=cfg.get("headers"), ttl=cfg.get("ttl"))
        except FetchError:
            if cfg.get("stop_on_page_error") and read_one:
                break
            raise
        read_one = True
        for match in pattern.finditer(body):
            row = {k: v for k, v in match.groupdict().items() if v}
            if cfg.get("unescape_json"):
                row = {k: _JSON_ESCAPE.sub(lambda e: chr(int(e.group(1), 16)), v)
                       for k, v in row.items()}
            if cfg.get("day_month_dates"):
                row["date"] = _with_year(row.get("date"))
            rule = parse_weekly(row.get("date"))
            if rule:
                weekly.extend(_card(row, when, not clock, None, cfg, cmap, base, url)
                              for when, clock in _weekly(rule, cfg))
                continue
            start, end, all_day = parse_range(row.get("date"))
            if not start:
                continue
            if row.get("time") and all_day:
                start = parse_dt(f"{row['date']} {row['time']}")[0] or start
                all_day = False
            if row.get("end"):
                end = parse_dt(row["end"])[0] or end
            # Some sites publish midnight as "time not announced yet" rather
            # than as a real start, and rendering that as 00:00 is worse than
            # showing no time at all.
            if cfg.get("midnight_means_all_day") and start.endswith("T00:00"):
                start, all_day = start[:10], True
            if all_day and cfg.get("default_time"):
                start, all_day = f"{start[:10]}T{cfg['default_time']}", False
            out.append(_card(row, start, all_day, end, cfg, cmap, base, url))

    if cfg.get("detail_patterns"):
        _from_detail_pages(out, cfg, http, flags)

    if cfg.get("dated_replaces_weekly"):
        taken = {(ev.venue, ev.start[:10]) for ev in out}
        weekly = [ev for ev in weekly if (ev.venue, ev.start[:10]) not in taken]
    return out + weekly


def _from_detail_pages(events: list[Event], cfg: dict, http, flags: int) -> None:
    """Fill in a missing clock (and end) from each event's own page."""
    patterns = [re.compile(p, flags) for p in cfg["detail_patterns"]]
    last_day = (date.today() + timedelta(days=int(cfg.get("detail_days", 60)))).isoformat()
    cards_per_url = Counter(ev.url for ev in events)
    for ev in events:
        if not ev.all_day or not ev.url or cards_per_url[ev.url] != 1 or ev.start[:10] > last_day:
            continue
        try:
            body = http.get(ev.url, headers=cfg.get("headers"), ttl=cfg.get("ttl"))
        except Exception:  # noqa: BLE001 - a missing page leaves the card as it was
            continue
        found: dict[str, str] = {}
        for pattern in patterns:
            match = pattern.search(body)
            for key, value in (match.groupdict().items() if match else ()):
                if value and key not in found:
                    found[key] = value
        day = ev.start[:10]
        start = parse_dt(f"{day} {found['time']}")[0] if found.get("time") else None
        if start and len(start) > 10:
            ev.start, ev.all_day = start, False
            end = parse_dt(f"{day} {found['end']}")[0] if found.get("end") else None
            if end and end > start:
                ev.end = end


def _weekly(rule: tuple[int, str | None], cfg: dict):
    """(start, clock) for every night of a weekly rule, today to the horizon."""
    weekday, clock = rule
    today = date.today()
    day = today + timedelta(days=(weekday - today.weekday()) % 7)
    while day <= today + timedelta(days=int(cfg.get("horizon_days", 120))):
        yield (f"{day}T{clock}" if clock else day.isoformat()), clock
        day += timedelta(weeks=1)


def _card(row: dict, start: str, all_day: bool, end: str | None, cfg: dict,
          cmap: dict, base: str, page: str) -> Event:
    href = row.get("url")
    return Event(
        title=row.get("title") or cfg.get("default_title", ""),
        start=start,
        end=end,
        all_day=all_day,
        venue=row.get("venue") or cfg.get("default_venue"),
        city=cfg.get("default_city"),
        categories=classify(row.get("title"), row.get("summary"),
                            row.get("venue"), mapping=cmap,
                            given=[row["category"]] if row.get("category") else []),
        url=urljoin(base, href) if href else cfg.get("link") or page,
        image=urljoin(base, row["image"]) if row.get("image") else None,
        summary=row.get("summary"),
        # "€ 14", "Gratis", "Donation based": the page's own wording, shown
        # as is. Also what the agenda's free filter reads (see app.js).
        price=_price(row.get("price"), cfg),
    )


def _price(text: str | None, cfg: dict) -> str | None:
    if text and cfg.get("price_map"):
        mapped = {k.lower(): v for k, v in cfg["price_map"].items()}.get(text.strip().lower())
        if mapped is not None:
            return mapped or None
    return text or cfg.get("default_price")
