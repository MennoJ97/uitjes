"""Run the enabled sources, then filter / dedupe / rank into one dataset."""
from __future__ import annotations

import time
import tomllib
from collections import Counter
from datetime import date, datetime, timedelta

from .adapters import REGISTRY
from .model import Event, day_of, search_text, slugify, within_horizon

# Words that carry no identity in a title. The canonical city names are added
# to this at run time from filter.cities, so "Guus Meeuwis in Amsterdam" and
# "Guus Meeuwis" come out as the same set without naming Amsterdam here.
FUZZY_STOPWORDS = (
    "in", "at", "the", "a", "de", "het", "een", "van", "bij", "op", "and", "en",
    "live", "presents", "ticket", "tickets", "tour",
)

# Words two unrelated titles at one venue easily share, so they never count as
# the shared word that anchors a venue match (see _venue_match): two different
# acts can both have an "album release" or a "Metropole Orkest". Dates too -
# "Acda en de Munnik 10 oktober 2026" says nothing with its last three words.
# Festival names belong here as well: during ADE half of Melkweg's programme
# says "ADE", and it matched annie's weekender to Dave Clarke's "30 Years of
# ADE" in another room.
WEAK_ANCHORS = frozenset("""
    ade dance event events weekender
    concert concerten show shows night nacht party release album special edition
    world festival anniversary jubileum jaar years orkest orchestra sold out
    uitverkocht met zijn his her feat featuring vs presenteert presenteren
    januari februari maart april mei juni juli augustus september oktober
    november december
""".split())

# Generic halves of a venue name. "Koninklijk Theater Carre - Theaterzaal" and
# "Carre" are one venue; "Theater X" and "Theater Y" are not, so what has to
# agree is the rest.
_VENUE_FILLER = frozenset("""
    het de the koninklijk theater theatre zaal grote kleine foyer
""".split())


def load_config(path: str) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def run_source(spec: dict, http, root: str) -> tuple[list[Event], dict]:
    """Never raises: a broken source degrades the dataset, it doesn't kill the build."""
    sid = spec["id"]
    report = {"id": sid, "label": spec.get("label", sid), "adapter": spec.get("adapter"),
              "enabled": spec.get("enabled", True), "count": 0, "kept": 0,
              "error": None, "seconds": 0.0}
    if not report["enabled"]:
        report["error"] = "disabled"
        return [], report

    adapter = REGISTRY.get(spec.get("adapter", ""))
    if adapter is None:
        report["error"] = f"unknown adapter {spec.get('adapter')!r}"
        return [], report

    cfg = dict(spec)
    cfg["_root"] = root
    started = time.time()
    try:
        events = adapter(cfg, http) or []
    except Exception as exc:  # noqa: BLE001 - every source is allowed to fail alone
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["seconds"] = round(time.time() - started, 2)
        return [], report

    # For a source read for one strand of its programme: a café's agenda where
    # only the pubquiz is wanted, not its bingo brunch or brewery tour. A title
    # must contain one of these words (any case) to be kept.
    keep = [w.lower() for w in spec.get("keep_title_contains") or []]
    if keep:
        events = [ev for ev in events if any(w in (ev.title or "").lower() for w in keep)]

    fallback = list(spec.get("fallback_categories") or [])
    # The stronger sibling of fallback_categories, for a venue that only does
    # one thing and publishes no genre at all: take the category from the
    # config and never infer. Inference reads the marketing copy, and a
    # comedian's blurb saying "live" or "on tour" comes out a concert.
    forced = list(spec.get("force_categories") or [])
    # Some sources publish an address where the site wants a name: Comedyhuis
    # says "Catharijnepoort 6", which is a correct answer to a different
    # question. Maps the raw string (case-insensitively) onto what to show;
    # anything unmapped passes through untouched, so a new location appears as
    # the source spelled it rather than disappearing.
    venue_map = {k.strip().lower(): v for k, v in (spec.get("venue_map") or {}).items()}
    for ev in events:
        ev.source = sid
        ev.source_label = spec.get("label", sid)
        if venue_map and ev.venue:
            ev.venue = venue_map.get(ev.venue.strip().lower(), ev.venue)
        ev.finalise()
        if forced:
            ev.categories = list(forced)
        # A concert hall's unclassifiable events are still concerts. Only fires
        # after keyword inference has already failed.
        elif fallback and ev.categories == ["other"]:
            ev.categories = fallback
        # A quiz is a quiz whoever lists it, and nobody's genre list has the
        # word: uitamsterdam files a pubquiz under "uitgaan" (club), Tivoli its
        # Popquiz under pop, so the source label always beats the keyword pass.
        # Added in front rather than instead - a film quiz is still film.
        if "quiz" in ev.title.lower() and "quiz" not in ev.categories:
            ev.categories = ["quiz"] + [c for c in ev.categories if c != "other"]
    report["count"] = len(events)
    report["seconds"] = round(time.time() - started, 2)
    return events, report


def _in_window(ev: Event, horizon: int) -> bool:
    """Inside the horizon, or a run (exhibition, festival week) still going on."""
    if within_horizon(ev.start, horizon):
        return True
    if not ev.end:
        return False
    today = date.today()
    try:
        start = date.fromisoformat(ev.start[:10])
        end = date.fromisoformat(ev.end[:10])
    except (ValueError, TypeError):
        return False
    return start <= today <= end and start <= today + timedelta(days=horizon)


def _city_of(ev: Event, cities: dict[str, list[str]]) -> str | None:
    """Fold aliases and nearby towns onto a canonical city label."""
    hay = " ".join(x for x in (ev.city, ev.venue) if x).lower()
    if not hay:
        return None
    for canonical, aliases in cities.items():
        for alias in [canonical, *aliases]:
            if alias.lower() in hay:
                return canonical
    return ev.city


def assemble(all_events: list[Event], cfg: dict, reports: list[dict]) -> dict:
    filt = cfg.get("filter", {})
    horizon = int(filt.get("horizon_days", 120))
    cities = {k: list(v) for k, v in (filt.get("cities") or {}).items()}
    strict_city = bool(filt.get("require_known_city", True))
    drop_words = [w.lower() for w in filt.get("drop_title_contains", [])]
    junk_summary = [w.lower() for w in filt.get("drop_summary_contains", [])]
    priority = {sid: i for i, sid in enumerate(cfg.get("output", {}).get("source_priority", []))}

    kept: dict[str, Event] = {}
    kept_per_source: Counter = Counter()

    ordered = sorted(all_events, key=lambda e: priority.get(e.source, 999))
    for ev in ordered:
        if not ev.start or not _in_window(ev, horizon):
            continue
        if any(w in ev.title.lower() for w in drop_words):
            continue
        city = _city_of(ev, cities)
        if strict_city and city not in cities:
            continue
        ev.city = city
        # Some aggregators paste their own disclaimer into every description.
        if ev.summary and any(w in ev.summary.lower() for w in junk_summary):
            ev.summary = None
        ev.uid = ev.finalise().uid

        existing = kept.get(ev.uid)
        if existing is None:
            kept[ev.uid] = ev
            kept_per_source[ev.source] += 1
        else:
            _merge(existing, ev)

    merges = _fuzzy_pass(kept, cfg, filt, priority) if filt.get("fuzzy_titles", True) else []
    for row in merges:
        kept_per_source[row["dropped_source"]] -= 1

    events = sorted(kept.values(), key=lambda e: (e.start, slugify(e.title)))
    for rep in reports:
        rep["kept"] = kept_per_source.get(rep["id"], 0)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "horizon_days": horizon,
        "cities": list(cities),
        "counts": {
            "total": len(events),
            "by_city": dict(Counter(e.city for e in events)),
            "by_category": dict(Counter(c for e in events for c in e.categories)),
        },
        "sources": reports,
        # Kept in the output rather than only logged: it is the record of what
        # was taken off the site and why, and it is what you read when a title
        # you expected is missing.
        "merges": merges,
        "events": [e.to_dict() for e in events],
    }


# slugify() strips non-ascii, which turns a curly apostrophe into a word break
# while a straight one becomes a lone "s" that gets dropped — so "90's NOW" and
# "90’s NOW" come out as different words and never match. Fold both away first,
# along with the multiplication sign a listing uses for "3x3".
_TIGHTEN = str.maketrans({"'": "", "’": "", "‘": "", "´": "", "×": "x"})


def _words(title: str, stop: set[str]) -> set[str]:
    slug = slugify((title or "").translate(_TIGHTEN))
    return {w for w in slug.split("-") if w and w not in stop and len(w) > 1}


def _may_merge(keeper: Event, other: Event, aggregators: set[str],
               identical: bool = False) -> bool:
    """The rule that replaces a list of words like "afterparty" and "high tea".

    A venue does not publish the same event twice: if Paradiso lists both
    "London Calling" and "London Calling - High Tea", someone decided those are
    two things and the extra words are load-bearing. An aggregator relisting a
    venue's show as "Douwe Bob in Paradiso" decided nothing, and the extra words
    are just labelling. So a title that contains another title is a duplicate
    when the two came from different catalogues — and within one catalogue only
    when that catalogue is second-hand and marked `aggregator` in sources.toml.

    Measured on a 2156-event build: 43 merges, none of them wrong, and the three
    affiliated-but-distinct events in the data (an afterparty, a high tea and a
    Vriendenrepetitie) are all protected by this without being named anywhere.

    Two DIFFERENT venue-direct sources are the third case, and one title
    merely CONTAINING another across two of them never merges: a venue
    publishes its own programme and nobody else's, so that is a coincidence,
    not a duplicate. Two *identical* titles are the exception, because that is
    one event with two publishers — Comedyhuis and TivoliVredenburg both
    listing the night Comedyhuis programmes in Tivoli's hall. Those used to be
    caught by the exact key and are caught here now that the key knows about
    the clock.
    Comedytrain's "Comedytrain presents: Mike Rice" contains Melkweg's act
    "mike." on the same night, which without this took a real Melkweg concert
    off the site and hung its photo on a comedy show. Checked against a live
    61-merge build: not one of them was venue-direct to venue-direct, so this
    costs nothing and only ever prevents that shape.
    """
    if keeper.source != other.source:
        return identical or keeper.source in aggregators or other.source in aggregators
    return keeper.source in aggregators


def _fuzzy_pass(kept: dict[str, Event], cfg: dict, filt: dict,
                priority: dict[str, int]) -> list[dict]:
    """Second dedupe pass. The exact key catches the same title twice; this
    catches one event wearing two labels — a venue suffix, a ticket type, a
    tour name — which is the shape aggregator titles actually take.

    Bucketed by day and city, which is the discriminating half of the exact key
    already, so this is ~11k comparisons on a full build rather than 2 million.
    """
    stop = {w.lower() for w in filt.get("fuzzy_stopwords") or FUZZY_STOPWORDS}
    stop |= {c.lower() for c in (filt.get("cities") or {})}
    aggregators = {s["id"] for s in cfg.get("source", []) if s.get("aggregator")}
    single_stage = {s["id"] for s in cfg.get("source", []) if s.get("single_stage")}

    buckets: dict[tuple, list[Event]] = {}
    for ev in kept.values():
        buckets.setdefault((day_of(ev.start), ev.city), []).append(ev)

    merges: list[dict] = []
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        survivors: list[tuple[Event, set[str]]] = []
        # Highest-priority source first, so the keeper is the venue-direct
        # record and the aggregator is the one that donates and disappears.
        for ev in sorted(bucket, key=lambda e: priority.get(e.source, 999)):
            words = _words(ev.title, stop)
            hit = None
            # The CLOSEST title, not the first one that contains it. An
            # aggregator's one-word "mike." is inside both Melkweg's "mike."
            # and Comedytrain's "Comedytrain presents: Mike Rice"; taking the
            # first match hung it on whichever source ranked higher, which is
            # how it ended up donating its photo to the wrong show. The
            # smallest symmetric difference is the one that adds the least.
            best = None
            for keeper, keeper_words in survivors:
                if not (words and keeper_words):
                    continue
                if (words <= keeper_words or keeper_words <= words) \
                        and _may_merge(keeper, ev, aggregators,
                                       identical=words == keeper_words):
                    distance = len(words ^ keeper_words)
                    if best is None or distance < best:
                        best, hit = distance, (keeper, keeper_words)
            rule = "title"
            if hit is None and ev.source in aggregators:
                hit, rule = _venue_match(ev, words, survivors, aggregators, single_stage)
            if hit is None:
                survivors.append((ev, words))
                continue
            keeper, keeper_words = hit
            merges.append({
                "day": day_of(ev.start),
                "kept": keeper.title,
                "kept_source": keeper.source,
                "kept_start": keeper.start,
                "dropped": ev.title,
                "dropped_source": ev.source,
                "dropped_start": ev.start,
                # The words one title had and the other did not. This is the
                # line to read when a merge looks wrong: a new "+[nazit]" or
                # "+[voorprogramma]" showing up here is a satellite event that
                # slipped through, and the fix is a rule, not a bug report.
                "extra": sorted(words ^ keeper_words),
                # Which rule matched: "title" (one contains the other),
                # "venue" or "single_stage" - see _venue_match.
                "rule": rule,
            })
            _merge(keeper, ev)
            kept.pop(ev.uid, None)
    return merges


def _venue_words(venue: str | None) -> set[str]:
    return {w for w in slugify(venue or "").split("-") if w and w not in _VENUE_FILLER}


def _same_venue(a: str | None, b: str | None) -> bool:
    """One name's distinctive words contain the other's: "Carre" is
    "Koninklijk Theater Carre - Theaterzaal", "Ziggo Dome" is "Ziggo Dome"."""
    wa, wb = _venue_words(a), _venue_words(b)
    return bool(wa and wb) and (wa <= wb or wb <= wa)


def _venue_match(ev: Event, words: set[str], survivors: list[tuple[Event, set[str]]],
                 aggregators: set[str], single_stage: set[str]):
    """An aggregator record matched to a venue's own record by WHERE, not title.

    The title rule needs one title inside the other, and an aggregator often
    labels a show differently from the venue rather than just longer: Ziggo
    Dome's "Noah Kahan - The Great Divide Tour" is Uit Amsterdam's "Noah Kahan
    in Ziggo Dome", and neither contains the other. Pinned to one venue on one
    day, sharing the act's name is enough - that is what stops a one-word
    overlap ("Mike") from crossing to another stage, which _may_merge warns
    about. Venue words, years and WEAK_ANCHORS do not count as the shared word.

    `single_stage` venues (sources.toml) put on one production a night, so
    there no shared word is needed at all: if the venue's own feed has exactly
    one title that day, an aggregator record there that day is that show -
    "Jan Smit in Amsterdam" is Ziggo Dome's "Muziekfeest van het Jaar", and
    "Royal Concertgebouw Orchestra, Ivan Fischer" its "Classics Live".

    That leans on the venue's feed being complete, so it backs off when the
    clocks disagree: a timed aggregator listing more than SINGLE_STAGE_GAP
    from every performance is a second show the feed did not list (an
    afternoon event before the evening concert), and folding it in would take
    it off the site. Most aggregator listings at these venues are all-day,
    which has no clock to disagree with.

    With several performances of one title (Disney in Concert at 15:00 and
    19:00, Freek Vonk at 10:00, 13:30 and 17:00) the listing goes onto the
    nearest in time, so its photo and words land on the performance it named.

    Only ever an aggregator onto a venue-direct record: two venue feeds never
    describe each other's programme. Returns ((keeper, keeper_words), rule).
    """
    here = [(k, kw) for k, kw in survivors
            if k.source not in aggregators and _same_venue(k.venue, ev.venue)]
    if not here:
        return None, None
    anchors = {w for w in words - _venue_words(ev.venue) - WEAK_ANCHORS if not w.isdigit()}
    # Most shared words wins, then the closest title, as in the title rule,
    # then the closest performance; _gap is 0 when either side has no clock.
    best = max(here, key=lambda h: (len(anchors & h[1]), -len(words ^ h[1]),
                                    -_gap(ev, h[0])))
    if anchors & best[1]:
        return best, "venue"
    titles = {slugify(k.title) for k, _ in here}
    if len(titles) == 1 and all(k.source in single_stage for k, _ in here):
        nearest = min(here, key=lambda h: (_gap(ev, h[0]), h[0].start))
        if _gap(ev, nearest[0]) <= SINGLE_STAGE_GAP:
            return nearest, "single_stage"
    return None, None


# How far apart two timed listings at a single_stage venue may be and still be
# one show. Wide enough for doors vs. curtain and an aggregator rounding to the
# hour, narrow enough that an afternoon slot and an evening one stay apart.
SINGLE_STAGE_GAP = 180


def _clock(ev: Event) -> int | None:
    """Minutes past midnight, or None for an all-day record."""
    if ev.all_day or not ev.start or len(ev.start) < 16:
        return None
    return int(ev.start[11:13]) * 60 + int(ev.start[14:16])


def _gap(a: Event, b: Event) -> int:
    """Minutes between two records' start times; 0 when either has no clock."""
    ca, cb = _clock(a), _clock(b)
    return 0 if ca is None or cb is None else abs(ca - cb)


def _merge(keeper: Event, other: Event) -> None:
    """Lower-priority duplicate donates whatever the keeper is missing."""
    for field_name in ("venue", "image", "summary", "price", "url"):
        if not getattr(keeper, field_name) and getattr(other, field_name):
            setattr(keeper, field_name, getattr(other, field_name))
    # An end on another day turns a performance into a run. UITagenda lists
    # De Utrechter's monthly pubquiz once, spanning every date of the series,
    # and donated that span to the one Sunday it merged with: "t/m 29 nov" on
    # a three-hour quiz. A timed keeper is one evening, so it only takes an end
    # on its own day; a date-only keeper (a festival) still takes a range.
    if not keeper.end and other.end and (
            keeper.all_day or other.end[:10] == (keeper.start or "")[:10]):
        keeper.end = other.end
    if keeper.categories == ["other"] and other.categories != ["other"]:
        keeper.categories = other.categories
    # The dropped title is often the more searchable one - Uit Amsterdam calls
    # Ziggo Dome's "Bijzonder Groots" night "... met beelden van Studio Ghibli" -
    # so its words stay findable on the keeper instead of leaving with it.
    # Its title goes first: search_text keeps leading words, and a long venue
    # description ahead of it would spend the budget and cut exactly the
    # alternative name the merge was meant to keep.
    keeper.search = search_text(other.title, keeper.search, other.summary, other.search,
                                exclude=(keeper.title, keeper.summary, keeper.venue))
