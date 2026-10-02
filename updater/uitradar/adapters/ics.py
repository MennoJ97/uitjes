"""Minimal iCalendar (.ics) feed reader — several venues and museums publish one.

Config keys: url, default_venue, default_city, category_map
"""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
        key, *params = name.split(";")
        key = key.upper()
        for old, new in _UNESCAPE:
            value = value.replace(old, new)
        current[key] = value
        # DTSTART;TZID=Europe/London:20261002T200000 - the zone is a parameter
        # on the property name, not part of the value.
        for param in params:
            if param.upper().startswith("TZID="):
                current[key + ";TZID"] = param[5:].strip('"')
    return out


def _ical_dt(value: str, tzid: str | None = None) -> str:
    """iCal date-time to ISO, keeping the zone so parse_dt converts it.

    Three forms (RFC 5545 3.3.5): "...T200000Z" is UTC, a TZID parameter names
    the zone, and neither means floating local time. The trailing Z used to be
    matched and then dropped, so a UTC feed read two hours early in summer.
    """
    value = value.strip()
    if re.fullmatch(r"\d{8}T\d{6}Z?", value):
        iso = f"{value[0:4]}-{value[4:6]}-{value[6:8]}T{value[9:11]}:{value[11:13]}"
        if value.endswith("Z"):
            return iso + "Z"
        if tzid:
            try:
                return datetime.fromisoformat(iso).replace(tzinfo=ZoneInfo(tzid)).isoformat()
            except (ZoneInfoNotFoundError, ValueError):
                # Outlook writes Windows names ("W. Europe Standard Time");
                # for a Dutch venue, local time is the right guess.
                pass
        return iso
    if re.fullmatch(r"\d{8}", value):
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return value


def _to_event(row: dict, cfg: dict, cmap: dict) -> Event | None:
    start, all_day = parse_dt(_ical_dt(row.get("DTSTART", ""), row.get("DTSTART;TZID")))
    if not start:
        return None
    return Event(
        title=row.get("SUMMARY") or "",
        start=start,
        end=parse_dt(_ical_dt(row.get("DTEND", ""), row.get("DTEND;TZID")))[0],
        all_day=all_day,
        venue=row.get("LOCATION") or cfg.get("default_venue"),
        city=cfg.get("default_city"),
        categories=classify(row.get("SUMMARY"), row.get("DESCRIPTION"),
                            mapping=cmap,
                            given=[c for c in (row.get("CATEGORIES") or "").split(",") if c]),
        url=row.get("URL"),
        summary=row.get("DESCRIPTION"),
    )
