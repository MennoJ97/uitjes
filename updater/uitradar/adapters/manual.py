"""Hand-curated events from a JSON file.

For the things no machine-readable feed covers well: IJ-Hallen weekends,
vintage kilo sales, the Sunday antiques market, one-off open-air concerts.
Supports simple recurrence so a monthly market is one entry, not twelve.

Config keys: file (path relative to the project root)

JSON shape — a list of objects:
  {"title": "...", "dates": ["2026-09-06"], "venue": "...", "city": "...",
   "categories": ["market"], "url": "...", "summary": "...", "price": "...",
   "time": "09:00"}
  {"title": "...", "repeat": {"rule": "monthly-nth-weekday", "weekday": 6,
   "nth": 1, "until": "2027-06-30"}, ...}       # weekday: Mon=0 … Sun=6
  {"title": "...", "repeat": {"rule": "weekly", "weekday": 0,
   "until": "2026-12-31"}, ...}
"""
from __future__ import annotations

import calendar
import json
import os
from datetime import date, timedelta

from ..model import Event, classify


def fetch(cfg: dict, http) -> list[Event]:  # noqa: ARG001 - no network needed
    path = cfg["file"]
    if not os.path.isabs(path):
        path = os.path.join(cfg["_root"], path)
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)

    out: list[Event] = []
    for row in rows:
        for day in _dates_for(row):
            when = f"{day}T{row['time']}" if row.get("time") else day
            out.append(Event(
                title=row.get("title", ""),
                start=when,
                end=row.get("end"),
                all_day=not row.get("time"),
                venue=row.get("venue") or cfg.get("default_venue"),
                city=row.get("city") or cfg.get("default_city"),
                categories=row.get("categories") or classify(row.get("title"),
                                                             row.get("summary")),
                url=row.get("url"),
                image=row.get("image"),
                summary=row.get("summary"),
                price=row.get("price"),
                provisional=bool(row.get("provisional")),
            ))
    return out


def _dates_for(row: dict) -> list[str]:
    if row.get("dates"):
        return list(row["dates"])
    rule = row.get("repeat")
    if not rule:
        return []

    start = date.fromisoformat(rule.get("from") or date.today().isoformat())
    until = date.fromisoformat(rule.get("until") or (start + timedelta(days=365)).isoformat())
    kind = rule.get("rule", "weekly")
    weekday = int(rule.get("weekday", 5))
    days: list[str] = []

    if kind == "weekly":
        step = int(rule.get("every_weeks", 1))
        cursor = start + timedelta(days=(weekday - start.weekday()) % 7)
        while cursor <= until:
            days.append(cursor.isoformat())
            cursor += timedelta(weeks=step)
    elif kind == "monthly-nth-weekday":
        nth = int(rule.get("nth", 1))
        year, month = start.year, start.month
        while date(year, month, 1) <= until:
            matches = [d for d in _month_days(year, month) if d.weekday() == weekday]
            index = nth - 1 if nth > 0 else nth  # nth = -1 -> last of the month
            if -len(matches) <= index < len(matches):
                pick = matches[index]
                if start <= pick <= until:
                    days.append(pick.isoformat())
            month += 1
            if month > 12:
                month, year = 1, year + 1
    return days


def _month_days(year: int, month: int) -> list[date]:
    last = calendar.monthrange(year, month)[1]
    return [date(year, month, d) for d in range(1, last + 1)]
