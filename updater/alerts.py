"""uitjes mail alerts — the uitjes-alerts container's entry point.

  python alerts.py serve              API on :8080 plus the notifier loop
  python alerts.py stats              how many subscribers and watches
  python alerts.py tick               one pass of the loop, then exit
  python alerts.py test-mail <addr>   send one test mail (checks SMTP settings)

Environment: ALERTS_BASE_URL (https://uitjes.<domain>), ALERTS_DATA (/data),
ALERTS_EVENTS (/site/data/events.json), SMTP_HOST / SMTP_PORT / SMTP_USER /
SMTP_PASSWORD / SMTP_FROM, ALERTS_CONTACT (shown on the page; default the From
address), ALERTS_MAIL=outbox to write mails to <data>/outbox instead of sending.
Without SMTP_HOST it is in outbox mode anyway.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from uitalerts.mailer import Mailer  # noqa: E402
from uitalerts.store import Store  # noqa: E402
from uitalerts.web import App, serve  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["serve", "stats", "tick", "test-mail"])
    ap.add_argument("address", nargs="?")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()

    env = os.environ
    data = env.get("ALERTS_DATA", "/data")
    base = env.get("ALERTS_BASE_URL", "http://localhost:8080")
    store = Store(data)
    mailer = Mailer(env, data, base)
    app = App(store, mailer, env.get("ALERTS_EVENTS", "/site/data/events.json"))

    if args.command == "serve":
        serve(app, args.port)
    elif args.command == "stats":
        print(json.dumps({**store.stats(), "mail": mailer.mode,
                          "announced_build": store.get_meta("announced_generated_at"),
                          "reminded_day": store.get_meta("reminded_day")}, indent=2))
    elif args.command == "tick":
        app.tick()
    elif args.command == "test-mail":
        if not args.address:
            ap.error("test-mail needs an address")
        mailer.send(args.address, "uitjes: testmail",
                    "Als je dit leest, werkt het versturen van uitjes-meldingen.\n",
                    "<p>Als je dit leest, werkt het versturen van uitjes-meldingen.</p>")
        print(f"sent via {mailer.mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
