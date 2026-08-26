"""Normalised event record + the tunable classification helpers."""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

# The taxonomy the front-end filters on. Everything gets mapped into this.
CATEGORIES = [
    "concert", "classical", "club", "theatre", "dance", "comedy", "film",
    "festival", "market", "exhibition", "family", "talk", "sports", "other",
]

# Checked in order — first hit wins, so put the specific ones before the broad ones.
CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("market", ("vlooienmarkt", "rommelmarkt", "kringloop", "vintage", "snuffelmarkt",
                "boekenmarkt", "platenbeurs", "braderie", "warenmarkt", "flea market",
                "markt", "market", "beurs", "fair")),
    ("exhibition", ("expositie", "tentoonstelling", "exhibition", "museum", "expo", "galerie")),
    ("classical", ("klassiek", "classical", "symfon", "orkest", "orchestra", "opera",
                   "kamermuziek", "recital", "barok", "baroque", "koor", "choir",
                   "strijkkwartet", "philharmon", "requiem", "cantate", "lunchconcert")),
    ("comedy", ("cabaret", "comedy", "stand-up", "standup", "kleinkunst", "improv")),
    ("dance", ("ballet", "choreograf", "moderne dans", "dansvoorstelling", "dans ")),
    ("theatre", ("theater", "theatre", "toneel", "voorstelling", "musical", "schouwburg",
                 "mime", "poppentheater")),
    ("film", ("film", "cinema", "bioscoop", "screening", "documentaire", "premiere")),
    ("club", ("clubnacht", "techno", "house night", "rave", "dj ", "nachtleven",
              "nightlife", "afterparty", "club ")),
    ("family", ("kinderen", "kindervoorstelling", "kids", "familie", "family", "jeugd", "4+", "6+")),
    ("talk", ("lezing", "debat", "debate", "college", "talkshow", "literatuur",
              "boekpresentatie", "rondleiding", "workshop", "cursus", "talk")),
    ("sports", ("wedstrijd", "marathon", "voetbal", "hockey", "toernooi", "sport")),
    ("festival", ("festival", "openluchttheater", "open air", "openlucht")),
    ("concert", ("concert", "muziek", "music", "live", "tour", "band", "pop", "rock",
                 "jazz", "hiphop", "hip-hop", "metal", "punk", "soul", "singer")),
]

_WS = re.compile(r"\s+")
_TAGS = re.compile(r"<[^>]+>")


def clean_text(value: str | None, limit: int | None = None) -> str | None:
    if not value:
        return None
    text = html.unescape(_TAGS.sub(" ", str(value)))
    text = html.unescape(text)
    text = _WS.sub(" ", text).strip()
    if not text:
        return None
    if limit and len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


# --- date parsing -----------------------------------------------------------

_MONTHS_NL = {
    "jan": 1, "feb": 2, "mrt": 3, "maa": 3, "apr": 4, "mei": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "okt": 10, "oct": 10, "nov": 11, "dec": 12, "mar": 3, "may": 5,
    "jun.": 6, "july": 7, "june": 6,
}
_NL_DATE = re.compile(r"(\d{1,2})\s*([a-zA-Z]{3,10})\.?\s*(\d{4})?")
# 11-09-2026 / 11.09.2026 / 11/09/2026 — day-first, the Dutch convention
_DMY = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b")
_TIME = re.compile(r"\b(\d{1,2})[:.](\d{2})\b")


def parse_dt(value, *, fallback_year: int | None = None) -> tuple[str | None, bool]:
    """Return (iso_string, all_day). Accepts ISO, 'YYYY-MM-DD HH:MM', epoch, NL text."""
    if value is None:
        return None, False
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value).isoformat(timespec="minutes"), False

    text = str(value).strip()
    if not text:
        return None, False

    iso = text.replace("Z", "+00:00")
    for candidate in (iso, iso.replace(" ", "T")):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            all_day = len(text) <= 10 or (dt.hour == 0 and dt.minute == 0 and "T" not in candidate)
            return dt.isoformat(timespec="minutes"), all_day
        except ValueError:
            pass

    try:
        return date.fromisoformat(text[:10]).isoformat(), True
    except ValueError:
        pass

    match = _DMY.search(text)
    if match:
        day, month, year = (int(g) for g in match.groups())
        try:
            parsed = date(year, month, day)
        except ValueError:
            return None, False
        clock = _TIME.search(text)
        if clock:
            return datetime(year, month, day, int(clock.group(1)),
                            int(clock.group(2))).isoformat(timespec="minutes"), False
        return parsed.isoformat(), True

    match = _NL_DATE.search(text)
    if match:
        day, month_word, year = match.groups()
        month = _MONTHS_NL.get(month_word[:3].lower())
        if month:
            explicit_year = year is not None
            year = int(year) if year else (fallback_year or date.today().year)
            try:
                parsed = date(year, month, int(day))
            except ValueError:
                return None, False
            # "5 jan" seen in December means next January, not last.
            if not explicit_year and (date.today() - parsed).days > 60:
                year += 1
                parsed = date(year, month, int(day))
            time_match = _TIME.search(text)
            if time_match:
                hh, mm = int(time_match.group(1)), int(time_match.group(2))
                return datetime(year, month, int(day), hh, mm).isoformat(timespec="minutes"), False
            return parsed.isoformat(), True
    return None, False


# "vr 11 sep t/m zo 13 sep", "5 – 9 okt", "1 sep tot 3 okt"
_RANGE_SEP = re.compile(r"\s*(?:t/m|t\.m\.|tot en met|tot|–|—|>)\s*", re.I)


def parse_range(text: str | None) -> tuple[str | None, str | None, bool]:
    """Split a Dutch date range into (start, end, all_day)."""
    if not text:
        return None, None, False
    parts = _RANGE_SEP.split(str(text), 1)
    start, all_day = parse_dt(parts[0])
    if not start:
        start, all_day = parse_dt(text)
        return start, None, all_day
    end = parse_dt(parts[1])[0] if len(parts) > 1 else None
    return start, end, all_day


def day_of(iso: str | None) -> str | None:
    return iso[:10] if iso else None


# --- the record -------------------------------------------------------------

@dataclass
class Event:
    title: str
    start: str
    end: str | None = None
    all_day: bool = False
    venue: str | None = None
    city: str | None = None
    categories: list[str] = field(default_factory=list)
    url: str | None = None
    image: str | None = None
    summary: str | None = None
    price: str | None = None
    # True for hand-entered recurrences whose exact dates were never confirmed
    # against the organiser — the UI badges these so they aren't read as fact.
    provisional: bool = False
    source: str = ""
    source_label: str = ""
    uid: str = ""

    def finalise(self) -> "Event":
        # URLs scraped straight out of HTML carry entity-escaped separators
        # (`&amp;`), which stay literal when the value is assigned from JS and
        # break the request. Attributes are unescaped by a real HTML parser;
        # regex extraction has to do it here.
        self.url = html.unescape(self.url) if self.url else None
        self.image = html.unescape(self.image) if self.image else None
        self.title = clean_text(self.title) or "(untitled)"
        self.summary = clean_text(self.summary, 240)
        self.venue = clean_text(self.venue, 80)
        self.city = clean_text(self.city, 60)
        self.price = clean_text(self.price, 40)
        if not self.categories:
            self.categories = ["other"]
        self.categories = [c for c in dict.fromkeys(self.categories) if c in CATEGORIES]
        if not self.categories:
            self.categories = ["other"]
        self.uid = dedupe_key(self)
        return self

    def to_dict(self) -> dict:
        return asdict(self)


def dedupe_key(ev: Event) -> str:
    base = "|".join([
        slugify(ev.title)[:60],
        day_of(ev.start) or "",
        slugify(ev.city or "")[:20],
    ])
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def classify(*texts: str | None, mapping: dict[str, str] | None = None,
             given: list[str] | None = None) -> list[str]:
    """Map source-native labels through `mapping`, else infer from free text."""
    out: list[str] = []
    for label in given or []:
        key = (label or "").strip().lower()
        mapped = (mapping or {}).get(key)
        if mapped:
            out.extend(m.strip() for m in mapped.split(","))
    if out:
        return [c for c in dict.fromkeys(out) if c in CATEGORIES] or ["other"]

    haystack = " ".join(t.lower() for t in list(texts) + list(given or []) if t)
    for name, words in CATEGORY_KEYWORDS:
        if any(w in haystack for w in words):
            return [name]
    return ["other"]


def within_horizon(iso: str, days: int, today: date | None = None) -> bool:
    today = today or date.today()
    try:
        when = date.fromisoformat(iso[:10])
    except (ValueError, TypeError):
        return False
    return today <= when <= today + timedelta(days=days)
