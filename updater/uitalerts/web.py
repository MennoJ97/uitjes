"""The JSON API behind uitjes.<domain>/api/alerts/, and the loop that mails.

Every state change is a POST with a JSON body. A cross-site form cannot send
application/json without a CORS preflight, which this server never answers, so
that alone stops another site from driving these endpoints; the Origin check
is the second lock. The one exception is one-click, which RFC 8058 defines as a
form POST from the mail client - and which can only ever unsubscribe.

Responses to /subscribe are the same whether or not the address is known, a
confirmation went out, or a limit was hit: the form must not tell a stranger
who is subscribed.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import notify
from . import watch as W

PREFIX = "/api/alerts/"
MAX_BODY = 4096
SUBSCRIBES_PER_IP_HOUR = 6
CONFIRM_MAILS_PER_HOUR = 40   # all addresses together: protects the sender's reputation
GENERIC_OK = ("Als het adres klopt, staat er een mail klaar om je aanmelding te bevestigen. "
              "Zie je niets? Kijk ook even bij spam.")


def log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


class App:
    def __init__(self, store, mailer, events_path: str):
        self.store, self.mailer, self.events_path = store, mailer, events_path
        self.per_ip: dict[str, deque] = defaultdict(deque)
        self.mail_times: deque = deque()
        self.rate_lock = threading.Lock()
        origin = urlsplit(mailer.base)
        self.origin = f"{origin.scheme}://{origin.netloc}"
        self._events_mtime, self._events = None, (None, [])

    def events(self) -> tuple[str | None, list[dict]]:
        mtime = os.stat(self.events_path).st_mtime
        if mtime != self._events_mtime:
            self._events = notify.load(self.events_path)
            self._events_mtime = mtime
        return self._events

    def allow(self, ip: str) -> bool:
        now = time.monotonic()
        with self.rate_lock:
            hits = self.per_ip[ip]
            while hits and hits[0] < now - 3600:
                hits.popleft()
            if len(hits) >= SUBSCRIBES_PER_IP_HOUR:
                return False
            hits.append(now)
            if len(self.per_ip) > 5000:  # forget idle addresses
                for key in [k for k, v in self.per_ip.items() if not v]:
                    del self.per_ip[key]
            return True

    def mail_budget(self) -> bool:
        now = time.monotonic()
        with self.rate_lock:
            while self.mail_times and self.mail_times[0] < now - 3600:
                self.mail_times.popleft()
            if len(self.mail_times) >= CONFIRM_MAILS_PER_HOUR:
                return False
            self.mail_times.append(now)
            return True

    # -- endpoints ----------------------------------------------------------
    def subscribe(self, body: dict, ip: str):
        if body.get("website"):            # the honeypot field humans never see
            return 202, {"ok": True, "message": GENERIC_OK}
        if not self.allow(ip):
            return 429, {"error": "Te veel aanmeldingen vanaf dit adres. Probeer het over een uur opnieuw."}
        email = W.valid_email(str(body.get("email") or ""))
        if not email:
            return 400, {"error": "Dat lijkt geen geldig e-mailadres."}
        try:
            w = W.normalise(body)
        except (ValueError, TypeError) as exc:
            return 400, {"error": str(exc) if isinstance(exc, ValueError) else "Ongeldige invoer."}
        if not self.mail_budget():
            log("subscribe: hourly confirmation budget spent, not mailing")
            return 202, {"ok": True, "message": GENERIC_OK}
        row, reason = self.store.subscribe(email, w)
        if row is None:
            log(f"subscribe: no mail ({reason})")
            return 202, {"ok": True, "message": GENERIC_OK}
        try:
            self.mailer.confirm(email, w, self.store.confirm_token(row))
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            log(f"subscribe: confirmation mail failed: {type(exc).__name__}: {exc}")
            return 503, {"error": "De bevestigingsmail kon niet worden verstuurd. Probeer het later opnieuw."}
        log(f"subscribe: confirmation sent for watch {row['id']}")
        return 202, {"ok": True, "message": GENERIC_OK}

    def confirm(self, body: dict):
        found = self.store.confirm(str(body.get("token") or ""))
        if not found:
            return 404, {"error": "Deze link werkt niet (meer). Een aanmelding die niet binnen "
                                  "48 uur is bevestigd, is gewist; meld je dan opnieuw aan."}
        row, sub, fresh = found
        if fresh:
            n = notify.baseline(self.store, row, self.events()[1], date.today())
            log(f"confirm: watch {row['id']} live, {n} existing match(es) marked seen")
            # Off the request thread: the visitor's confirmation must not wait
            # on, or fail with, a mail to the owner.
            threading.Thread(target=self.tell_admin, args=(sub, row), daemon=True).start()
        return 200, {"ok": True, "watch": W.describe(W.from_row(row)),
                     "url": W.site_url(self.mailer.base, W.from_row(row)),
                     "manage": self.store.manage_token(sub)}

    def tell_admin(self, sub, row) -> None:
        """Mail the owner about a confirmed sign-up. Only confirmed ones: a
        submitted form proves nothing (anyone can type any address), and
        alerting on those would let a stranger fill the owner's inbox."""
        w = W.from_row(row)
        first = sub["confirmed_at"] == row["confirmed_at"]
        stats = self.store.stats()
        # Worded as the sign-up page's select offers them.
        remind = {None: "geen", 0: "op de dag zelf", 1: "een dag van tevoren",
                  3: "drie dagen van tevoren", 7: "een week van tevoren"}.get(w["remind_days"], "?")
        subject = ("uitjes: nieuwe aanmelding" if first
                   else "uitjes: bestaande abonnee volgt nog een selectie")
        text = (f"{sub['email']} volgt nu: {W.describe(w)}\n\n"
                f"Herinnering: {remind}. Op de site: {W.site_url(self.mailer.base, w)}\n\n"
                f"In totaal {stats['subscribers']} abonnee(s) met {stats['watches']} melding(en).")
        try:
            self.mailer.admin(subject, text)
            log(f"confirm: owner told about watch {row['id']}")
        except Exception as exc:  # noqa: BLE001 - a lost note, never a lost sign-up
            log(f"confirm: owner mail failed: {type(exc).__name__}: {exc}")

    def manage(self, body: dict):
        sub = self.store.subscriber_by_token(str(body.get("token") or ""))
        if not sub:
            return 404, {"error": "Deze link werkt niet (meer). Misschien ben je al afgemeld."}
        out = []
        for row in self.store.watches(sub["id"]):
            w = W.from_row(row)
            out.append({"id": row["id"], "text": W.describe(w), "remind_days": w["remind_days"],
                        "url": W.site_url(self.mailer.base, w)})
        return 200, {"email": sub["email"], "watches": out}

    def delete_watch(self, body: dict):
        sub = self.store.subscriber_by_token(str(body.get("token") or ""))
        if not sub:
            return 404, {"error": "Deze link werkt niet (meer)."}
        try:
            watch_id = int(body.get("id"))
        except (TypeError, ValueError):
            return 400, {"error": "Ongeldige melding."}
        gone = self.store.delete_watch(sub["id"], watch_id)
        log(f"delete-watch: subscriber {sub['id']}{' (now gone)' if gone else ''}")
        return 200, {"ok": True, "gone": gone}

    def unsubscribe(self, token: str):
        sub = self.store.subscriber_by_token(token)
        if not sub:
            # Already gone is success from where the visitor stands.
            return 200, {"ok": True, "gone": True}
        self.store.unsubscribe(sub["id"])
        log(f"unsubscribe: subscriber {sub['id']} deleted")
        return 200, {"ok": True, "gone": True}

    # -- the loop -----------------------------------------------------------
    def tick(self) -> None:
        generated, events = self.events()
        today = date.today()
        seen = self.store.get_meta("announced_generated_at")
        if generated and generated != seen:
            if seen is None:
                log(f"loop: first run, starting from the build of {generated}")
            else:
                n = notify.announce(self.store, self.mailer, events, today, log=log)
                log(f"loop: build {generated} checked, {n} mail(s)")
            self.store.set_meta("announced_generated_at", generated)
        if datetime.now().hour >= notify.REMIND_HOUR and \
                self.store.get_meta("reminded_day") != today.isoformat():
            n = notify.remind(self.store, self.mailer, events, today, log=log)
            log(f"loop: reminders for {today}, {n} mail(s)")
            self.store.set_meta("reminded_day", today.isoformat())
        if self.store.get_meta("pruned_hour") != datetime.now().strftime("%Y-%m-%dT%H"):
            w, s = self.store.prune()
            if w or s:
                log(f"loop: pruned {w} unconfirmed watch(es), {s} subscriber(s)")
            self.store.set_meta("pruned_hour", datetime.now().strftime("%Y-%m-%dT%H"))

    def run_loop(self, every: int = 60) -> None:
        while True:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - the loop must survive anything
                log(f"loop: {type(exc).__name__}: {exc}")
            time.sleep(every)


class Handler(BaseHTTPRequestHandler):
    app: App = None  # set by serve()
    server_version = "uitjes-alerts"
    sys_version = ""

    def log_message(self, fmt, *args):  # noqa: D401 - silence the default access log
        pass

    def _send(self, status: int, payload, content_type="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _ip(self) -> str:
        # Behind Cloudflare the edge sets Cf-Connecting-Ip; for a direct client,
        # which nobody rate-limits in earnest, the peer will do.
        return (self.headers.get("Cf-Connecting-Ip") or self.headers.get("X-Real-Ip")
                or self.client_address[0])

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == PREFIX + "info":
            return self._send(200, {"mail": self.app.mailer.mode, "contact": self.app.mailer.contact})
        if path == PREFIX + "health":
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        parts = urlsplit(self.path)
        path = parts.path
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            return self._send(413, {"error": "too large"})
        raw = self.rfile.read(length) if length else b""

        if path == PREFIX + "one-click":
            token = (parse_qs(parts.query).get("t") or [""])[0]
            self.app.unsubscribe(token)
            return self._send(200, b"Afgemeld.\n", "text/plain; charset=utf-8")

        origin = self.headers.get("Origin")
        if origin and origin != self.app.origin:
            return self._send(403, {"error": "forbidden"})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._send(415, {"error": "expected JSON"})
        try:
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._send(400, {"error": "Ongeldige invoer."})

        routes = {
            "subscribe": lambda: self.app.subscribe(body, self._ip()),
            "confirm": lambda: self.app.confirm(body),
            "manage": lambda: self.app.manage(body),
            "delete-watch": lambda: self.app.delete_watch(body),
            "unsubscribe": lambda: self.app.unsubscribe(str(body.get("token") or "")),
        }
        action = routes.get(path[len(PREFIX):]) if path.startswith(PREFIX) else None
        if action is None:
            return self._send(404, {"error": "not found"})
        try:
            status, payload = action()
        except Exception as exc:  # noqa: BLE001
            log(f"{path}: {type(exc).__name__}: {exc}")
            status, payload = 500, {"error": "Er ging iets mis. Probeer het later opnieuw."}
        return self._send(status, payload)


def serve(app: App, port: int) -> None:
    Handler.app = app
    threading.Thread(target=app.run_loop, daemon=True, name="notifier").start()
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    httpd.daemon_threads = True
    log(f"alerts: listening on :{port}, mail via {app.mailer.mode}, base {app.mailer.base}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
