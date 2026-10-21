"""The two passes over the published agenda.

announce: after every PUBLISHED build - generated_at in events.json changed -
each watch is matched against upcoming events, and anything it has not reported
yet goes out, one mail per subscriber. A refused build leaves generated_at
alone (it only stamps last_attempt), so a broken source never mails anyone.

remind: once a day from REMIND_HOUR, events exactly remind_days ahead.

A watch is baselined when it is confirmed: everything already matching is
marked seen, so confirming "jazz" does not mail the forty jazz nights already
on the site - only what is announced after.

Per subscriber the marks are written only after their mail went out. A send
that fails stops the pass without moving the generated_at marker, so the next
loop retries, and whoever was already mailed is not mailed again.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from datetime import date, timedelta

from . import watch as W
from .mailer import PER_WATCH

REMIND_HOUR = 8


def load(path: str) -> tuple[str | None, list[dict]]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("generated_at"), data.get("events") or []


def baseline(store, watch_row, events: list[dict], today: date) -> int:
    w = W.from_row(watch_row)
    rows = [(watch_row["id"], ev["uid"], "seen", W.day_of(ev)) for ev in events
            if W.upcoming(ev, today) and W.matches(ev, w)]
    store.mark(rows)
    return len(rows)


def _by_subscriber(watches) -> "OrderedDict[int, list]":
    grouped: OrderedDict[int, list] = OrderedDict()
    for row in watches:
        grouped.setdefault(row["subscriber_id"], []).append(row)
    return grouped


def announce(store, mailer, events: list[dict], today: date, log=print) -> int:
    known = store.known(("seen", "new"))
    upcoming = sorted((ev for ev in events if W.upcoming(ev, today)), key=lambda e: e["start"])
    mailed = 0
    for sub_id, rows in _by_subscriber(store.live_watches()).items():
        sections, marks = [], []
        for row in rows:
            w = W.from_row(row)
            hits = [ev for ev in upcoming if (row["id"], ev["uid"]) not in known and W.matches(ev, w)]
            if hits:
                sections.append((w, hits[:PER_WATCH], max(0, len(hits) - PER_WATCH)))
                marks += [(row["id"], ev["uid"], "new", W.day_of(ev)) for ev in hits]
        if not sections:
            continue
        sub = store.subscriber(sub_id)
        mailer.announce(sub["email"], sections, store.manage_token(sub))
        store.mark(marks)
        mailed += 1
        log(f"announce: subscriber {sub_id}, {len(marks)} event(s) in {len(sections)} watch(es)")
    return mailed


def remind(store, mailer, events: list[dict], today: date, log=print) -> int:
    known = store.known(("remind",))
    mailed = 0
    for sub_id, rows in _by_subscriber(store.live_watches()).items():
        sections, marks = [], []
        for row in rows:
            if row["remind_days"] is None:
                continue
            target = (today + timedelta(days=row["remind_days"])).isoformat()
            w = W.from_row(row)
            hits = sorted((ev for ev in events if W.day_of(ev) == target
                           and (row["id"], ev["uid"]) not in known and W.matches(ev, w)),
                          key=lambda e: e["start"])
            if hits:
                sections.append((w, hits[:PER_WATCH], max(0, len(hits) - PER_WATCH)))
                marks += [(row["id"], ev["uid"], "remind", target) for ev in hits]
        if not sections:
            continue
        sub = store.subscriber(sub_id)
        mailer.remind(sub["email"], sections, store.manage_token(sub))
        store.mark(marks)
        mailed += 1
        log(f"remind: subscriber {sub_id}, {len(marks)} event(s)")
    return mailed
