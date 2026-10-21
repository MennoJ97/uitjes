"""Last-resort HTML scraper for sites with no feed and no JSON-LD.

You give it a regex with named groups; everything lives in sources.toml so a
site redesign is a config fix, not a code change. Use `build.py --probe <id>`
to iterate on the pattern.

Config keys:
  urls          pages to scrape (may include {page} — see pages)
  pages         optional list of values substituted into {page}
  item_pattern  regex with named groups: title, date (required),
                url, venue, time, image, summary (optional)
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
  dated_replaces_weekly
                a card whose date is a standing night ("Elke dinsdag, vanaf
                20:00") becomes one event a week to the horizon; with this set,
                a dated card at the same venue on the same day takes that
                night's place - a themed edition instead of the regular one
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import urljoin

from ..model import Event, classify, parse_dt, parse_range, parse_weekly

_FLAGS = {"i": re.I, "s": re.S, "m": re.M, "x": re.X}


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

    for url in urls:
        body = http.get(url, headers=cfg.get("headers"), ttl=cfg.get("ttl"))
        for match in pattern.finditer(body):
            row = {k: v for k, v in match.groupdict().items() if v}
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

    if cfg.get("dated_replaces_weekly"):
        taken = {(ev.venue, ev.start[:10]) for ev in out}
        weekly = [ev for ev in weekly if (ev.venue, ev.start[:10]) not in taken]
    return out + weekly


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
    )
