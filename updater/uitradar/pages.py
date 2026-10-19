"""The agenda as plain HTML, for whoever reads the page without running app.js.

Until 2026-10-19 the served page held about 65 words: every event was drawn
by app.js out of events.json, so a crawler that does not run scripts (and
Google, which does, but later and not always) saw an empty agenda. This writes
the next few weeks as static markup next to events.json:

  data/pages/<page>.html   the upcoming days for one page, as the same markup
                           app.js draws, so its first render replaces it
                           without a jump. nginx pulls it into #results in
                           index.html with an SSI include; see
                           ../../nginx/default.conf.
  data/pages/<page>.ld.html  schema.org Event JSON-LD for those same events,
                           included OUTSIDE #results: Google reads structured
                           data from the rendered page, and app.js empties
                           #results when it draws.
  data/sitemap.xml         the pages, each with <lastmod> = the day its events
                           last changed. nginx serves it at /sitemap.xml.
  data/.pages-state.json   what that date is based on (nginx never serves
                           dotfiles).

Pages are "all" (/) and one per city in CITY_PAGES. The page copy (titles,
headings) is hand-written in web/index.html; this only writes events.

Why the JSON-LD is lean: Google's Event rich results need one page per event
("Each event MUST have a unique URL (a leaf page)"), and as of the docs dated
2026-09-08 the feature is not offered in the Netherlands or in Dutch at all.
So the markup here is a plain, valid description of what is on the page for
any consumer that reads it, not a bid for rich results, and it leaves out
what would only add weight (images, descriptions) or would have to be guessed
(street addresses: the sources give a venue and a city, nothing more).
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta

from .model import LOCAL_TZ

SITE = "https://uitjes.mhjansen.nl"

# Page slug -> (path, city or None). nginx has a location for each path and
# app.js maps a city filter to the same path, so a new city here needs both.
CITY_PAGES = ("Amsterdam", "Utrecht")
PAGES = {"all": ("/", None), **{c.lower(): (f"/{c.lower()}", c) for c in CITY_PAGES}}

# How much of the agenda goes into the HTML. Whole days are added until the
# next one would take the fragment past BUDGET bytes, so a busy week shows
# fewer days than a quiet one, and the page stays well under a megabyte (it
# goes out gzipped, at about a sixth of that). MIN_DAYS wins over the budget.
BUDGET = 450_000
MIN_DAYS, MAX_DAYS = 7, 28

# Mirrors CATEGORY in app.js: what a visitor sees, singular and plural.
CATEGORY = {
    "concert": ("concert", "concerten"), "classical": ("klassiek", "klassiek"),
    "club": ("club", "club"), "theatre": ("theater", "theater"), "dance": ("dans", "dans"),
    "comedy": ("comedy", "comedy"), "film": ("film", "film"),
    "festival": ("festival", "festivals"), "market": ("markt", "markten"),
    "exhibition": ("expositie", "exposities"), "family": ("familie", "familie"),
    "talk": ("lezing", "lezingen"), "sports": ("sport", "sport"), "other": ("overig", "overig"),
}

# The most specific schema.org type for each category; the rest stay Event.
SCHEMA_TYPE = {
    "concert": "MusicEvent", "classical": "MusicEvent", "club": "MusicEvent",
    "theatre": "TheaterEvent", "dance": "DanceEvent", "comedy": "ComedyEvent",
    "film": "ScreeningEvent", "festival": "Festival", "exhibition": "ExhibitionEvent",
    "family": "ChildrensEvent", "sports": "SportsEvent", "talk": "EducationEvent",
}

WEEKDAYS = ("maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag")
MONTHS = ("januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
          "september", "oktober", "november", "december")
MONTHS_SHORT = ("jan.", "feb.", "mrt.", "apr.", "mei", "jun.", "jul.", "aug.", "sep.",
                "okt.", "nov.", "dec.")

_SOLD_OUT = re.compile(r"\b(sold[ -]?out|uitverkocht)\b", re.I)
_AMOUNT = re.compile(r"(\d+)(?:[.,](\d{1,2}))?")


def _e(text) -> str:
    return html.escape(str(text), quote=True)


def _day(iso: str) -> date:
    return date.fromisoformat(iso[:10])


# --- HTML, mirroring row(), runsBlock() and render() in app.js ---------------

def _row(ev: dict) -> str:
    parts = []
    if ev.get("url"):
        parts.append(f'<a class="event" href="{_e(ev["url"])}" target="_blank" '
                     f'rel="noopener noreferrer">')
    else:
        parts.append('<a class="event" href="#">')
    if ev.get("all_day") or len(ev["start"]) <= 10:
        parts.append('<div class="time allday">hele dag</div>')
    else:
        parts.append(f'<div class="time">{ev["start"][11:16]}</div>')

    parts.append(f'<div><h3>{_e(ev["title"])}</h3>')
    where = [w for w in (ev.get("venue"), ev.get("city")) if w]
    if ev.get("end") and ev["end"][:10] != ev["start"][:10]:
        end = _day(ev["end"])
        where.append(f"t/m {end.day} {MONTHS_SHORT[end.month - 1]}")
    if where:
        parts.append(f'<p class="meta">{_e(" · ".join(where))}</p>')
    if ev.get("summary"):
        parts.append(f'<p class="summary">{_e(ev["summary"])}</p>')
    bits = [CATEGORY.get(c, (c,))[0] for c in ev.get("categories", [])]
    if ev.get("price"):
        bits.append(ev["price"])
    bits.append("via " + (ev.get("source_label") or ev.get("source", "")))
    flag = ' · <span class="flag">onbevestigd</span>' if ev.get("provisional") else ""
    parts.append(f'<p class="sub">{_e(" · ".join(bits))}{flag}</p></div></a>')
    # No image: app.js fades its thumbnails in on load, and a no-script reader
    # gains nothing from a hundred hotlinked pictures but weight.
    return "".join(parts)


def _runs(evs: list[dict]) -> str:
    by_cat = Counter((ev.get("categories") or ["other"])[0] for ev in evs)
    what = ", ".join(f"{n} {CATEGORY.get(c, (c, c))[0 if n == 1 else 1]}"
                     for c, n in by_cat.most_common())
    n = len(evs)
    return ('<details class="runs"><summary><span class="time">doorlopend</span>'
            f'<span class="what"><b>{n} {"uitje" if n == 1 else "uitjes"}</b>'
            f'<span>: {_e(what)}</span></span><span class="more">tonen</span></summary>'
            + "".join(_row(ev) for ev in evs) + "</details>")


def _day_head(day: date, today: date) -> str:
    # No "vandaag"/"morgen" as app.js writes: this file is written at 06:20,
    # 13:20 and 20:20, and is read until the next run, past midnight.
    month = MONTHS[day.month - 1] + ("" if day.year == today.year else f" {day.year}")
    return (f'<h2 class="dayhead"><span class="num">{day.day}</span>'
            f'<span class="wd">{WEEKDAYS[day.weekday()]}</span>'
            f'<span class="mo">{month}</span></h2>')


# --- JSON-LD ------------------------------------------------------------------

def _when(iso: str) -> str:
    """The data's naive Amsterdam wall clock, with its UTC offset spelled out."""
    if len(iso) <= 10:
        return iso
    return datetime.fromisoformat(iso).replace(tzinfo=LOCAL_TZ).isoformat(timespec="minutes")


def _price(text: str | None) -> float | None:
    """The lowest amount in "vanaf € 10,-", "€13,50" or "gratis"; None if unsure."""
    if not text:
        return None
    low = text.lower()
    if low.startswith("gratis") and "jaarleden" not in low:
        return 0.0
    m = _AMOUNT.search(text)
    if not m or "€" not in text:
        return None
    return float(f"{m.group(1)}.{m.group(2) or 0}")


def _place(ev: dict, labels: dict, aggregators: set) -> str | None:
    venue = ev.get("venue")
    # A list of rooms ("Cloud Nine;Club Nine;...") or no venue at all: the
    # building is the source itself, unless the source is an aggregator, in
    # which case nobody here knows where it is and the event is left out.
    if not venue or ";" in venue:
        return None if ev.get("source") in aggregators else labels.get(ev.get("source"))
    return venue


def _jsonld(ev: dict, labels: dict, aggregators: set) -> dict | None:
    # Unconfirmed recurrences are not marked up as events: their dates are a
    # guess, and the markup would state them as fact.
    if ev.get("provisional"):
        return None
    place = _place(ev, labels, aggregators)
    if not place or place.lower() == "online" or not ev.get("city"):
        return None
    item = {
        "@type": SCHEMA_TYPE.get((ev.get("categories") or ["other"])[0], "Event"),
        "name": ev["title"],
        "startDate": _when(ev["start"][:10] if ev.get("all_day") else ev["start"]),
    }
    if ev.get("end"):
        item["endDate"] = _when(ev["end"][:10] if ev.get("all_day") else ev["end"])
    item["eventStatus"] = "https://schema.org/EventScheduled"
    item["location"] = {"@type": "Place", "name": place,
                        "address": {"@type": "PostalAddress",
                                    "addressLocality": ev["city"], "addressCountry": "NL"}}
    if ev.get("url"):
        item["url"] = ev["url"]
    # An offer only where the link is the venue's own: an aggregator's page
    # describes the event but does not sell the ticket.
    if ev.get("url") and ev.get("source") not in aggregators:
        offer = {"@type": "Offer", "url": ev["url"]}
        price = _price(ev.get("price"))
        if price is not None:
            offer["price"] = f"{price:.2f}"
            offer["priceCurrency"] = "EUR"
        if _SOLD_OUT.search(ev["title"]):
            offer["availability"] = "https://schema.org/SoldOut"
        item["offers"] = offer
    return item


def _script(items: list[dict]) -> str:
    body = json.dumps({"@context": "https://schema.org", "@graph": items},
                      ensure_ascii=False, separators=(",", ":"))
    # A title containing "</script>" must not end the element early.
    return '<script type="application/ld+json">' + body.replace("</", "<\\/") + "</script>"


# --- one page -----------------------------------------------------------------

def render_page(events: list[dict], today: date, labels: dict, aggregators: set,
                horizon_days: int, budget: int = BUDGET) -> tuple[str, str, int, int]:
    """(markup, JSON-LD script, days shown, events shown) for one page's events."""
    upcoming = [ev for ev in events if _day(ev.get("end") or ev["start"]) >= today]
    by_day: dict[date, list[dict]] = {}
    for ev in upcoming:
        # A run already under way sorts under today, as in app.js.
        by_day.setdefault(max(_day(ev["start"]), today), []).append(ev)

    chunks, items, size, days, shown = [], [], 0, 0, 0
    for day in sorted(by_day):
        if (day - today).days >= MAX_DAYS:
            break
        evs = by_day[day]
        running = [ev for ev in evs if _day(ev["start"]) < day]
        starting = [ev for ev in evs if _day(ev["start"]) >= day]
        block = (f'<section class="daygroup">{_day_head(day, today)}<div class="day-list">'
                 + (_runs(running) if running else "")
                 + "".join(_row(ev) for ev in starting) + "</div></section>")
        day_items = [i for i in (_jsonld(ev, labels, aggregators) for ev in evs) if i]
        weight = len(block.encode()) + len(_script(day_items).encode())
        if days and (day - today).days >= MIN_DAYS and size + weight > budget:
            break
        chunks.append(block)
        items.extend(day_items)
        size += weight
        days = (day - today).days + 1
        shown += len(evs)

    note = (f'<p class="static-note">Hierboven de komende {days} dagen. Met JavaScript aan '
            f'toont deze pagina de hele agenda, {horizon_days} dagen vooruit, en kun je '
            f'filteren op stad, datum en soort uitje.</p>')
    return "\n".join(chunks) + "\n" + note + "\n", _script(items) + "\n", days, shown


# --- files ----------------------------------------------------------------------

def _write_if_changed(path: str, text: str) -> bool:
    """Atomic, and a no-op when the content is the same, so an unchanged file
    keeps its mtime (nginx's Last-Modified) across runs."""
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read() == text:
                return False
    except OSError:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return True


def _digest(events: list[dict]) -> str:
    blob = json.dumps(events, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def _lastmod(state_path: str, hashes: dict[str, str], today: date) -> dict[str, str | None]:
    """The day each page's events last changed, as far as this file has seen.

    events.json is rewritten on every run (generated_at, last_attempt), so its
    mtime says when the builder last ran, not when the agenda changed. What is
    compared is the page's events. A page seen for the first time gets no date
    rather than a guessed one: <lastmod> appears from its first real change.
    """
    try:
        with open(state_path, encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    out = {}
    for page, digest in hashes.items():
        seen = state.get(page) or {}
        if seen.get("sha256") == digest:
            out[page] = seen.get("changed")
        elif seen:
            out[page] = today.isoformat()
        else:
            out[page] = None
        state[page] = {"sha256": digest, "changed": out[page]}
    _write_if_changed(state_path, json.dumps(state, indent=1, sort_keys=True) + "\n")
    return out


def _sitemap(lastmod: dict[str, str | None]) -> str:
    rows = []
    for page, (path, _city) in PAGES.items():
        stamp = f"<lastmod>{lastmod[page]}</lastmod>" if lastmod.get(page) else ""
        rows.append(f"  <url><loc>{SITE}{path}</loc>{stamp}</url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            "<!-- Written by uitjes-updater (uitradar/pages.py) on every run; do not edit.\n"
            "     lastmod = the day that page's events last changed. -->\n"
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(rows) + "\n</urlset>\n")


def publish(dataset: dict, out_dir: str, cfg: dict, today: date | None = None) -> list[str]:
    """Write every page fragment and the sitemap for a published dataset."""
    today = today or datetime.now(LOCAL_TZ).date()
    sources = cfg.get("source", [])
    labels = {s["id"]: s.get("label", s["id"]) for s in sources}
    aggregators = {s["id"] for s in sources if s.get("aggregator")}
    events = dataset.get("events") or []
    horizon = int(dataset.get("horizon_days") or 0)

    os.makedirs(os.path.join(out_dir, "pages"), exist_ok=True)
    hashes, lines = {}, []
    for page, (path, city) in PAGES.items():
        mine = [ev for ev in events if city is None or ev.get("city") == city]
        hashes[page] = _digest(mine)
        markup, script, days, shown = render_page(mine, today, labels, aggregators, horizon)
        _write_if_changed(os.path.join(out_dir, "pages", f"{page}.html"), markup)
        _write_if_changed(os.path.join(out_dir, "pages", f"{page}.ld.html"), script)
        lines.append(f"{path} {shown} events over {days} days, "
                     f"{(len(markup.encode()) + len(script.encode())) // 1024} KB")

    lastmod = _lastmod(os.path.join(out_dir, ".pages-state.json"), hashes, today)
    _write_if_changed(os.path.join(out_dir, "sitemap.xml"), _sitemap(lastmod))
    lines.append("sitemap lastmod: " + ", ".join(f"{p} {d or '-'}" for p, d in lastmod.items()))
    return lines
