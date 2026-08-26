"""Minimal iCalendar (.ics) feed reader — several venues and museums publish one.

Config keys: url, default_venue, default_city, category_map
"""
from __future__ import annotations

import re

from ..model import Event, classify, parse_dt

_UNESCAPE = [("\\n", " "), ("\\,", ","), ("\\;", ";"), ("\\\\", "\\")]


def fetch(cfg: dict, http) -> list[Event]:
    body = http.get(cfg["url"], ttl=cfg.get("ttl"))
    cmap = {k.lower(): v for k, v in (cfg.get("category_map") or {}).items()}

    # RFC 5545 line folding: a leading space continues the previous line.
    lines = re.sub(r"\r?\n[ \t]", "", body).splitlines()

    out: list[Event] = []
    current: dict[str, str] | None = None
    for line in lines:
        if line.startswith("BEGIN:VEVENT"):
            current = {}
            continue
        if line.startswith("END:VEVENT"):
            if current:
                ev = _to_event(current, cfg, cmap)
                if ev:
                    out.append(ev)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        name, value = line.split(":", 1)
        key = name.split(";", 1)[0].upper()
        for old, new in _UNESCAPE:
            value = value.replace(old, new)
        current[key] = value
    return out


def _ical_dt(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"\d{8}T\d{6}Z?", value):
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}T{value[9:11]}:{value[11:13]}"
    if re.fullmatch(r"\d{8}", value):
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return value


def _to_event(row: dict, cfg: dict, cmap: dict) -> Event | None:
    start, all_day = parse_dt(_ical_dt(row.get("DTSTART", "")))
    if not start:
        return None
    return Event(
        title=row.get("SUMMARY") or "",
        start=start,
        end=parse_dt(_ical_dt(row.get("DTEND", "")))[0],
        all_day=all_day,
        venue=row.get("LOCATION") or cfg.get("default_venue"),
        city=cfg.get("default_city"),
        categories=classify(row.get("SUMMARY"), row.get("DESCRIPTION"),
                            mapping=cmap,
                            given=[c for c in (row.get("CATEGORIES") or "").split(",") if c]),
        url=row.get("URL"),
        summary=row.get("DESCRIPTION"),
    )
