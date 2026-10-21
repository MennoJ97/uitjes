"""What the alerts service counts, and where it reports it.

Every notable thing (a sign-up confirmed, an unsubscribe, an alert mail sent)
goes through one call that does two things: it bumps the daily counters in the
store, which feed a dashboard card through ALERTS_STATS_FILE, and - when
ALERTS_UMAMI_WEBSITE is set - it sends the same moment to Umami as a custom
event, so it shows under Events with its properties and can be used in Umami's
own reports (funnels, breakdowns).

The Umami side posts to the same public collect path a visitor's browser uses
(<base>/_u/api/send), so this container needs no network next to Umami: it is
public-facing, and should not share a network with Umami's internals.

Use its own Umami website, not uitjes's: Umami makes a session of whatever
sends it events, so on the uitjes site this would count as one extra visitor a
day. Events carry aggregate properties only - city, categories, counts, and for
a confirmed sign-up the selection it follows - never an address.
"""
from __future__ import annotations

import json
import threading
import urllib.request
from urllib.parse import urlsplit

from . import watch as W

# Umami drops anything whose User-Agent looks like a bot; this one does not.
UA = "Mozilla/5.0 (X11; Linux x86_64) uitjes-alerts/1.0"
REMIND_LABEL = {None: "geen", 0: "op de dag zelf", 1: "een dag van tevoren",
                3: "drie dagen van tevoren", 7: "een week van tevoren"}


class Tracker:
    def __init__(self, store, env: dict, base_url: str, log=print):
        self.store, self.log = store, log
        self.website = (env.get("ALERTS_UMAMI_WEBSITE") or "").strip()
        self.endpoint = env.get("ALERTS_UMAMI_URL") or base_url.rstrip("/") + "/_u/api/send"
        self.hostname = urlsplit(base_url).hostname or "localhost"

    def __call__(self, event: str | None, counts: dict[str, int], data: dict | None = None) -> None:
        for metric, n in counts.items():
            self.store.count(metric, n)
        if event and self.website:
            threading.Thread(target=self._send, args=(event, data or {}), daemon=True).start()

    def _send(self, name: str, data: dict) -> None:
        body = json.dumps({"type": "event", "payload": {
            "website": self.website, "hostname": self.hostname, "language": "nl",
            "url": "/meldingen", "title": "uitjes meldingen", "name": name, "data": data,
        }}).encode()
        req = urllib.request.Request(self.endpoint, data=body, headers={
            "Content-Type": "application/json", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                res.read()
        except Exception as exc:  # noqa: BLE001 - analytics never breaks the service
            self.log(f"umami: {name} not recorded: {type(exc).__name__}: {exc}")


def props(w: dict, selection: bool = False) -> dict:
    """A watch as event properties. The selection text only once confirmed:
    an unconfirmed form can hold anything a stranger typed."""
    out = {"city": w["city"] if w["city"] != "all" else "beide",
           "categories": ",".join(w["cats"]) or "alle",
           "search": "ja" if w["q"] else "nee",
           "reminder": REMIND_LABEL.get(w["remind_days"], "?")}
    if selection:
        out["selection"] = W.describe(w)
    return out


def summary(store, mailer) -> dict:
    """The dashboard card's JSON."""
    from datetime import datetime  # noqa: PLC0415
    stats = store.stats()
    failure = store.get_meta("last_failure")
    return {
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mail": mailer.mode,
        **stats,
        "with_reminder": store.with_reminder(),
        "d7": store.totals(7),
        "d30": store.totals(30),
        "total": store.totals(),
        # events.json stamps local time without an offset; the card's
        # relative-time helper needs one.
        "last_build": _aware(store.get_meta("announced_generated_at")),
        "last_alert_mail": store.get_meta("last_alert_mail"),
        "last_failure": json.loads(failure) if failure else None,
        "top_selections": [{"text": W.describe(W.from_row(r)), "n": r["n"]}
                           for r in store.top_selections()],
    }


def _aware(stamp: str | None) -> str | None:
    from datetime import datetime  # noqa: PLC0415
    try:
        return datetime.fromisoformat(stamp).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None
