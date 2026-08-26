"""Run the enabled sources, then filter / dedupe / rank into one dataset."""
from __future__ import annotations

import time
import tomllib
from collections import Counter
from datetime import date, datetime, timedelta

from .adapters import REGISTRY
from .model import Event, slugify, within_horizon


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
    for ev in events:
        ev.source = sid
        ev.source_label = spec.get("label", sid)
        ev.finalise()
        # A concert hall's unclassifiable events are still concerts. Only fires
        # after keyword inference has already failed.
        if fallback and ev.categories == ["other"]:
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
        "events": [e.to_dict() for e in events],
    }


def _merge(keeper: Event, other: Event) -> None:
    """Lower-priority duplicate donates whatever the keeper is missing."""
    for field_name in ("venue", "image", "summary", "price", "url", "end"):
        if not getattr(keeper, field_name) and getattr(other, field_name):
            setattr(keeper, field_name, getattr(other, field_name))
    if keeper.categories == ["other"] and other.categories != ["other"]:
        keeper.categories = other.categories
