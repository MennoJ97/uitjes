"""A watch is a saved filter view of the agenda: search words, a city and a set
of categories - exactly what the page puts in its URL, so "mail me about this"
can mean the view in front of you. Matching mirrors matches() in app.js; if one
changes, change the other, or a mail and the site will disagree about what a
filter means.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urlencode

from uitradar.pages import CATEGORY, LANG, lang_path

CITIES = ("Amsterdam", "Utrecht")
LANGS = ("nl", "en")
REMIND_CHOICES = (0, 1, 3, 7)
MAX_Q = 80
_WS = re.compile(r"\s+")
# Search text is typed by strangers and, once confirmed, shown on the owner's
# dashboard and in Umami: letters, digits, spaces and ordinary punctuation only.
_Q_JUNK = re.compile(r"[^\w \-.,'\u2019&/+#!?()]")
EMAIL = re.compile(r"^[^@\s<>\"',;]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


def lang_of(value) -> str:
    """The page's language from untrusted input; Dutch unless it says English."""
    return "en" if str(value or "").lower() == "en" else "nl"


def normalise(raw: dict) -> dict:
    """A watch from untrusted input, or ValueError whose argument is a message
    key (see MESSAGES in web.py), so the API can answer in either language."""
    q = _WS.sub(" ", _Q_JUNK.sub(" ", str(raw.get("q") or ""))).strip()[:MAX_Q]
    city = str(raw.get("city") or "all")
    city = next((c for c in CITIES if c.lower() == city.lower()), "all")
    cats_in = raw.get("cats") or []
    if isinstance(cats_in, str):
        cats_in = cats_in.split(",")
    cats = sorted({str(c).strip().lower() for c in cats_in} & set(CATEGORY))
    remind = raw.get("remind_days")
    remind = None if remind in (None, "", "none") else int(remind)
    if remind is not None and remind not in REMIND_CHOICES:
        raise ValueError("bad-reminder")
    # A city alone is the whole agenda of a city, a few hundred mails' worth
    # of events a month - not an alert, a newsletter nobody asked for.
    if not q and not cats:
        raise ValueError("need-filter")
    return {"q": q, "city": city, "cats": cats, "remind_days": remind}


def from_row(row) -> dict:
    return {"q": row["q"], "city": row["city"],
            "cats": [c for c in row["cats"].split(",") if c],
            "remind_days": row["remind_days"]}


def valid_email(value: str) -> str | None:
    value = (value or "").strip()
    if len(value) > 254 or not EMAIL.match(value):
        return None
    local, _, domain = value.rpartition("@")
    return f"{local}@{domain.lower()}"


def matches(ev: dict, w: dict) -> bool:
    if w["city"] != "all" and ev.get("city") != w["city"]:
        return False
    cats = ev.get("categories") or []
    if w["cats"] and not any(c in w["cats"] for c in cats):
        return False
    if w["q"]:
        # The same haystack as app.js: the labels are in it so "markt" finds
        # what the data files under market. Both languages' labels, since a
        # watch saved from the English page says "markets".
        labels = [x for c in cats for t in LANG.values() for x in t["category"].get(c, (c, c))]
        hay = " ".join(str(x) for x in [ev.get("title"), ev.get("venue"), ev.get("city"),
                                        ev.get("summary"), ev.get("search"), *cats, *labels]
                       if x).lower()
        if not all(t in hay for t in w["q"].lower().split()):
            return False
    return True


def day_of(ev: dict) -> str:
    return (ev.get("start") or "")[:10]


def upcoming(ev: dict, today: date) -> bool:
    return day_of(ev) >= today.isoformat()


BOTH_CITIES = {"nl": "Amsterdam en Utrecht", "en": "Amsterdam and Utrecht"}


def describe(w: dict, lang: str = "nl") -> str:
    """'"jazz" · concerten, club · Utrecht' - what a person signed up for."""
    labels = LANG[lang]["category"]
    bits = []
    if w["q"]:
        bits.append(f"“{w['q']}”")
    if w["cats"]:
        bits.append(", ".join(labels.get(c, (c, c))[1] for c in w["cats"]))
    bits.append(w["city"] if w["city"] != "all" else BOTH_CITIES[lang])
    return " · ".join(bits)


def site_url(base: str, w: dict, lang: str = "nl") -> str:
    """The same view on the site, in the URL shape app.js reads."""
    path = lang_path(lang, "/" + w["city"].lower() if w["city"] in CITIES else "/")
    params = {}
    if w["q"]:
        params["q"] = w["q"]
    if w["cats"]:
        params["cat"] = ",".join(w["cats"])
    qs = urlencode(params).replace("%2C", ",")
    return base + path + ("?" + qs if qs else "")


WEEKDAYS = {"nl": ("ma", "di", "wo", "do", "vr", "za", "zo"),
            "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")}
MONTHS = {"nl": ("jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"),
          "en": LANG["en"]["months_short"]}


def when(ev: dict, lang: str = "nl") -> str:
    """'za 24 okt · 20:30', the way the site prints a date."""
    start = ev.get("start") or ""
    try:
        d = date.fromisoformat(start[:10])
    except ValueError:
        return start
    text = f"{WEEKDAYS[lang][d.weekday()]} {d.day} {MONTHS[lang][d.month - 1]}"
    if d.year != datetime.now().year:
        text += f" {d.year}"
    if len(start) >= 16 and not ev.get("all_day"):
        text += f" · {start[11:16]}"
    return text
