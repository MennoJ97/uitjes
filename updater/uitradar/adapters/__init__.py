"""Adapter registry.

An adapter is a callable ``fetch(cfg, http) -> list[Event]``.
`cfg` is the raw [[source]] table from sources.toml, so every knob an adapter
exposes is tunable from config without touching Python.
"""
from __future__ import annotations

from . import (boomchicago, carre, ics, json_api, jsonld, manual, paradiso,
               regex_cards, rss, rss_json, ticketmaster, tribe, weeztix,
               ziggodome)

REGISTRY = {
    "tribe": tribe.fetch,
    "jsonld": jsonld.fetch,
    "json_api": json_api.fetch,
    "rss_json": rss_json.fetch,
    "carre": carre.fetch,
    "boomchicago": boomchicago.fetch,
    "paradiso": paradiso.fetch,
    "ziggodome": ziggodome.fetch,
    "ticketmaster": ticketmaster.fetch,
    "ics": ics.fetch,
    "rss": rss.fetch,
    "manual": manual.fetch,
    "regex_cards": regex_cards.fetch,
    "weeztix": weeztix.fetch,
}

__all__ = ["REGISTRY"]
