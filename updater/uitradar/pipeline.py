"""Run the enabled sources, then filter / dedupe / rank into one dataset."""
from __future__ import annotations

import time
import tomllib
from collections import Counter
from datetime import date, datetime, timedelta

from .adapters import REGISTRY
from .model import Event, day_of, slugify, within_horizon

# Words that carry no identity in a title. The canonical city names are added
# to this at run time from filter.cities, so "Guus Meeuwis in Amsterdam" and
# "Guus Meeuwis" come out as the same set without naming Amsterdam here.
FUZZY_STOPWORDS = (
    "in", "at", "the", "a", "de", "het", "een", "van", "bij", "op", "and", "en",
    "live", "presents", "ticket", "tickets", "tour",
)


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


def _may_merge(keeper: Event, other: Event, aggregators: set[str]) -> bool:
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
    """
    if keeper.source != other.source:
        return True
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
            for keeper, keeper_words in survivors:
                if not (words and keeper_words):
                    continue
                if (words <= keeper_words or keeper_words <= words) \
                        and _may_merge(keeper, ev, aggregators):
                    hit = (keeper, keeper_words)
                    break
            if hit is None:
                survivors.append((ev, words))
                continue
            keeper, keeper_words = hit
            merges.append({
                "day": day_of(ev.start),
                "kept": keeper.title,
                "kept_source": keeper.source,
                "dropped": ev.title,
                "dropped_source": ev.source,
                # The words one title had and the other did not. This is the
                # line to read when a merge looks wrong: a new "+[nazit]" or
                # "+[voorprogramma]" showing up here is a satellite event that
                # slipped through, and the fix is a rule, not a bug report.
                "extra": sorted(words ^ keeper_words),
            })
            _merge(keeper, ev)
            kept.pop(ev.uid, None)
    return merges


def _merge(keeper: Event, other: Event) -> None:
    """Lower-priority duplicate donates whatever the keeper is missing."""
    for field_name in ("venue", "image", "summary", "price", "url", "end"):
        if not getattr(keeper, field_name) and getattr(other, field_name):
            setattr(keeper, field_name, getattr(other, field_name))
    if keeper.categories == ["other"] and other.categories != ["other"]:
        keeper.categories = other.categories
