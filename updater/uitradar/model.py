"""Normalised event record + the tunable classification helpers."""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# The taxonomy the front-end filters on. Everything gets mapped into this.
CATEGORIES = [
    "concert", "classical", "club", "theatre", "dance", "comedy", "film",
    "festival", "market", "exhibition", "family", "talk", "sports", "quiz", "other",
]

# Checked in order — first hit wins, so put the specific ones before the broad ones.
CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("quiz", ("quiz",)),
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


# Filler that would only spend search_text()'s budget: every Dutch blurb has
# "de", "het" and "een", and no one searches for them.
_SEARCH_STOP = frozenset("""
    aan als bij dan dat de deze die dit door een en het hij hun ik in is je
    kan maar met naar niet nog of om ook op over te tot uit van voor wat we
    wel wie wij zal ze zich zij zijn zo and are but by for from has have his
    its of on or our that the their this to was were will with you your
""".split())
_SEARCH_WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*")


def search_text(*texts: str | None, exclude: tuple = (), limit: int = 400) -> str | None:
    """Distinct lowercase words from `texts`, for the page to match but never show.

    For text that names what an event IS without being in its title or
    summary: Ziggo Dome's Joe Hisaishi concert is titled "... filmmuziek -
    Bijzonder Groots", and "Studio Ghibli" appears only in the long description,
    so a search for ghibli could not find it. Words already in `exclude` (the
    title and summary, which the page searches anyway) and filler are dropped
    and the rest kept in order of first appearance, so `limit` keeps the most
    leading words rather than a random sample.
    """
    seen = {w.lower() for t in exclude for w in _SEARCH_WORD.findall(clean_text(t) or "")}
    words: list[str] = []
    size = 0
    for text in texts:
        for word in _SEARCH_WORD.findall(clean_text(text) or ""):
            key = word.lower()
            if len(key) < 3 or key in seen or key in _SEARCH_STOP or key.isdigit():
                continue
            if size + len(key) + 1 > limit:
                return " ".join(words) or None
            seen.add(key)
            words.append(key)
            size += len(key) + 1
    return " ".join(words) or None


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


# --- date parsing -----------------------------------------------------------

# Every venue on the site is Dutch, so this is the wall clock every start time
# is shown in. Named here rather than taken from the process: astimezone() with
# no argument converts to whatever TZ the container happens to run with, and
# Concertgebouw and TivoliVredenburg (both UTC "...Z") came out right only
# because compose sets TZ=Europe/Amsterdam. A local run on a UTC box, or a
# compose edit that dropped the variable, would have moved them two hours.
LOCAL_TZ = ZoneInfo("Europe/Amsterdam")


def to_local(dt: datetime) -> datetime:
    """An aware datetime as naive Amsterdam wall-clock time; naive passes through."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(LOCAL_TZ).replace(tzinfo=None)


_MONTHS_NL = {
    "jan": 1, "feb": 2, "mrt": 3, "maa": 3, "apr": 4, "mei": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "okt": 10, "oct": 10, "nov": 11, "dec": 12, "mar": 3, "may": 5,
    "jun.": 6, "july": 7, "june": 6,
}
# The ordinal is for English cards: "19th of November 2026" (Brouwerij Troost).
_NL_DATE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s*([a-zA-Z]{3,10})\.?\s*(\d{4})?")
# 11-09-2026 / 11.09.2026 / 11/09/2026 — day-first, the Dutch convention
_DMY = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b")
_TIME = re.compile(r"\b(\d{1,2})[:.](\d{2})\b")


def parse_dt(value, *, fallback_year: int | None = None) -> tuple[str | None, bool]:
    """Return (iso_string, all_day). Accepts ISO, 'YYYY-MM-DD HH:MM', epoch, NL text."""
    if value is None:
        return None, False
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, LOCAL_TZ).replace(tzinfo=None).isoformat(
            timespec="minutes"), False

    text = str(value).strip()
    if not text:
        return None, False

    iso = text.replace("Z", "+00:00")
    for candidate in (iso, iso.replace(" ", "T")):
        try:
            dt = to_local(datetime.fromisoformat(candidate))
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


# "vr 11 sep t/m zo 13 sep", "5 – 9 okt", "1 sep tot 3 okt", "vr 2 okt - za 3 okt".
# A plain hyphen only counts with space on both sides: bare, it is the inside
# of "2026-10-02" and "4-10-2026".
_RANGE_SEP = re.compile(r"\s*(?:t/m|t\.m\.|tot en met|tot|–|—|>)\s*|\s+-\s+", re.I)


_BARE_DAY = re.compile(r"^\s*(?:[a-z]{2,9}\.?\s+)?(\d{1,2})\s*$", re.I)


def _borrow_month(head: str, tail: str) -> tuple[str | None, bool]:
    """ "5" (or "vr 5") before "9 okt [2026]": the start's month and year are
    the end's. Without this the whole range parsed as its end date. "30 – 2 okt"
    means 30 September, so a start that lands after the end steps back a month.
    """
    day = _BARE_DAY.match(head)
    end = _NL_DATE.search(tail)
    if not day or not end:
        return None, False
    start, all_day = parse_dt(f"{day.group(1)} {end.group(2)} {end.group(3) or ''}")
    stop = parse_dt(tail)[0]
    if start and stop and start[:10] > stop[:10]:
        d = date.fromisoformat(start[:10])
        prev = date(d.year - (d.month == 1), (d.month - 2) % 12 + 1, 1)
        try:
            start = prev.replace(day=d.day).isoformat()
        except ValueError:
            return None, False
    return start, all_day


def parse_range(text: str | None) -> tuple[str | None, str | None, bool]:
    """Split a Dutch date range into (start, end, all_day)."""
    if not text:
        return None, None, False
    parts = _RANGE_SEP.split(str(text), 1)
    start, all_day = parse_dt(parts[0])
    if not start and len(parts) > 1:
        start, all_day = _borrow_month(parts[0], parts[1])
    if not start:
        start, all_day = parse_dt(text)
        return start, None, all_day
    end = parse_dt(parts[1])[0] if len(parts) > 1 else None
    return start, end, all_day


# "Elke dinsdag, vanaf 20:00" / "Weekly on Mondays, from 20:00": a standing
# night printed where a date would be. Monday is 0, as in date.weekday().
_WEEKDAYS = {
    "maandag": 0, "dinsdag": 1, "woensdag": 2, "donderdag": 3, "vrijdag": 4,
    "zaterdag": 5, "zondag": 6, "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
}
_WEEKLY = re.compile(r"\b(?:elke|iedere|every|weekly on)\s+([a-z]+?)s?\b", re.I)


def parse_weekly(text: str | None) -> tuple[int, str | None] | None:
    """(weekday, "HH:MM" or None) for a weekly rule, None for anything else."""
    match = _WEEKLY.search(text or "")
    if not match or match.group(1).lower() not in _WEEKDAYS:
        return None
    clock = _TIME.search(text)
    return (_WEEKDAYS[match.group(1).lower()],
            f"{int(clock.group(1)):02d}:{clock.group(2)}" if clock else None)


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
    # Extra words the page's search matches against but never displays; see
    # search_text(). Left out of the JSON when empty, which is most events.
    search: str | None = None
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
        self.search = search_text(self.search, exclude=(self.title, self.summary, self.venue))
        if not self.categories:
            self.categories = ["other"]
        self.categories = [c for c in dict.fromkeys(self.categories) if c in CATEGORIES]
        if not self.categories:
            self.categories = ["other"]
        self.uid = dedupe_key(self)
        return self

    def to_dict(self) -> dict:
        out = asdict(self)
        if not out["search"]:
            del out["search"]
        return out


def dedupe_key(ev: Event) -> str:
    """Title + day + clock + city.

    The clock is in the key because two performances of one show in one night
    are two evenings out, not a duplicate — Comedytrain plays the same Line-up
    at 19:00 and 22:00, Carré runs the Wereldkerstcircus at 16:00 and 20:00,
    and a key of title+day+city published one and threw the other away. It was
    costing 177 performances: Stadsschouwburg Utrecht 87, Carré 34,
    Concertgebouw 28, Comedytrain 19, AFAS Live 5, Ziggo Dome 3. An all-day
    record has no clock to compare and keeps the day on its own, so a venue
    that publishes dates without times is unaffected.

    What this key no longer catches is one event that two sources time
    differently (doors vs. curtain). That is the fuzzy pass's job — see
    _may_merge in pipeline.py, which merges identical titles across two
    catalogues whatever the clock says.
    """
    base = "|".join([
        slugify(ev.title)[:60],
        day_of(ev.start) or "",
        "" if ev.all_day else (ev.start or "")[11:16],
        slugify(ev.city or "")[:20],
    ])
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def classify(*texts: str | None, mapping: dict[str, str] | None = None,
             given: list[str] | None = None) -> list[str]:
    """Map source-native labels through `mapping`, else infer from free text."""
    out: list[str] = []
    for label in given or []:
        # Regex-scraped labels arrive raw: "Musea &amp; exposities" would miss
        # the "musea & exposities" key and fall through to keyword guessing.
        key = html.unescape(label or "").strip().lower()
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
