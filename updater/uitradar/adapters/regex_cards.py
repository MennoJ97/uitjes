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
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from ..model import Event, classify, parse_dt, parse_range

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

    for url in urls:
        body = http.get(url, ttl=cfg.get("ttl"))
        for match in pattern.finditer(body):
            row = {k: v for k, v in match.groupdict().items() if v}
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
            href = row.get("url")
            out.append(Event(
                title=row.get("title", ""),
                start=start,
                end=end,
                all_day=all_day,
                venue=row.get("venue") or cfg.get("default_venue"),
                city=cfg.get("default_city"),
                categories=classify(row.get("title"), row.get("summary"),
                                    row.get("venue"), mapping=cmap,
                                    given=[row["category"]] if row.get("category") else []),
                url=urljoin(base, href) if href else url,
                image=urljoin(base, row["image"]) if row.get("image") else None,
                summary=row.get("summary"),
            ))
    return out
