"""SQLite store for subscribers, their watches, and what was already mailed.

What is kept is the minimum the service needs: an address, the filters, when
they were confirmed, and which events each watch has already reported. No IP
address, no names, no open/click tracking. Unsubscribing deletes the subscriber
row and everything under it (ON DELETE CASCADE), so nothing lingers.

One connection behind one lock: the HTTP threads and the notifier loop share
it, and the traffic is a handful of writes a day.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta

SCHEMA = """
PRAGMA foreign_keys = ON;
-- AUTOINCREMENT so an id is never reused: tokens are derived from ids, and a
-- reused id would make an old unsubscribe link work on a new subscriber.
CREATE TABLE IF NOT EXISTS subscriber (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  confirmed_at TEXT,
  last_confirm_mail TEXT,
  confirm_mail_day TEXT,
  confirm_mails INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS watch (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subscriber_id INTEGER NOT NULL REFERENCES subscriber(id) ON DELETE CASCADE,
  q TEXT NOT NULL DEFAULT '',
  city TEXT NOT NULL DEFAULT 'all',
  cats TEXT NOT NULL DEFAULT '',
  remind_days INTEGER,
  created_at TEXT NOT NULL,
  confirmed_at TEXT
);
-- kind: seen (already there when the watch was confirmed), new (announced),
-- remind (reminder sent). day is the event's date, for pruning.
CREATE TABLE IF NOT EXISTS sent (
  watch_id INTEGER NOT NULL REFERENCES watch(id) ON DELETE CASCADE,
  uid TEXT NOT NULL,
  kind TEXT NOT NULL,
  day TEXT NOT NULL,
  PRIMARY KEY (watch_id, uid, kind)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
-- Aggregate counters per day (sign-ups, unsubscribes, mails sent...), for the
-- dashboard card. Numbers only: nothing in here points back at a person.
CREATE TABLE IF NOT EXISTS daily (
  day TEXT NOT NULL,
  metric TEXT NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY (day, metric)
);
"""

# Abuse limits on the one thing a stranger can make us do: send a mail to an
# address they typed. Per address, so the form cannot be used to flood someone.
CONFIRM_MAIL_GAP = timedelta(minutes=2)
CONFIRM_MAILS_PER_DAY = 3
MAX_WATCHES = 10
MAX_PENDING_WATCHES = 3
PENDING_TTL = timedelta(hours=48)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, data_dir: str):
        os.makedirs(data_dir, exist_ok=True)
        self.db = sqlite3.connect(os.path.join(data_dir, "alerts.sqlite3"),
                                  check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.execute("PRAGMA journal_mode = WAL")
        self.secret = _load_secret(os.path.join(data_dir, "secret"))

    # -- tokens -------------------------------------------------------------
    # Signed, not stored: <id>.<hmac>. The HMAC covers what the token is for
    # and a value that changes if the row is recreated, so a confirm token
    # cannot unsubscribe and an old link dies with its row. Rotating the
    # secret file invalidates every link ever sent.
    def token(self, purpose: str, row_id: int, salt: str) -> str:
        mac = hmac.new(self.secret, f"{purpose}:{row_id}:{salt}".encode(), hashlib.sha256)
        return f"{row_id}.{base64.urlsafe_b64encode(mac.digest()[:18]).decode()}"

    def _check(self, purpose: str, token: str, table: str, salt_col: str) -> sqlite3.Row | None:
        head, _, _ = (token or "").partition(".")
        if not head.isdigit() or len(token) > 64:
            return None
        row = self.db.execute(f"SELECT * FROM {table} WHERE id = ?", (int(head),)).fetchone()
        if row and hmac.compare_digest(self.token(purpose, row["id"], row[salt_col]), token):
            return row
        return None

    def manage_token(self, sub: sqlite3.Row) -> str:
        return self.token("manage", sub["id"], sub["email"])

    def confirm_token(self, watch: sqlite3.Row) -> str:
        return self.token("confirm", watch["id"], watch["created_at"])

    # -- sign-up ------------------------------------------------------------
    def subscribe(self, email: str, w: dict) -> tuple[sqlite3.Row | None, str]:
        """Add an unconfirmed watch. Returns (watch, "") when a confirmation
        mail should go out, or (None, reason) when it should not."""
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                sub = self.db.execute("SELECT * FROM subscriber WHERE email = ?",
                                      (email,)).fetchone()
                if sub is None:
                    sub_id = self.db.execute(
                        "INSERT INTO subscriber (email, created_at) VALUES (?, ?)",
                        (email, now())).lastrowid
                    sub = self.db.execute("SELECT * FROM subscriber WHERE id = ?",
                                          (sub_id,)).fetchone()
                # The same filter twice is one watch: re-sending its
                # confirmation is what a second sign-up should do, and a
                # confirmed one only takes the new reminder setting.
                watch = self.db.execute(
                    "SELECT * FROM watch WHERE subscriber_id = ? AND q = ? AND city = ? AND cats = ?",
                    (sub["id"], w["q"], w["city"], ",".join(w["cats"]))).fetchone()
                if watch is not None and watch["confirmed_at"]:
                    self.db.execute("UPDATE watch SET remind_days = ? WHERE id = ?",
                                    (w["remind_days"], watch["id"]))
                    self.db.execute("COMMIT")
                    return None, "already confirmed"
                reason = self._mail_budget(sub)
                if reason:
                    self.db.execute("ROLLBACK")
                    return None, reason
                if watch is None:
                    counts = self.db.execute(
                        "SELECT SUM(confirmed_at IS NOT NULL) AS live, SUM(confirmed_at IS NULL) AS pending"
                        " FROM watch WHERE subscriber_id = ?", (sub["id"],)).fetchone()
                    if (counts["live"] or 0) >= MAX_WATCHES:
                        self.db.execute("ROLLBACK")
                        return None, "too many watches"
                    if (counts["pending"] or 0) >= MAX_PENDING_WATCHES:
                        self.db.execute("ROLLBACK")
                        return None, "too many pending"
                    watch_id = self.db.execute(
                        "INSERT INTO watch (subscriber_id, q, city, cats, remind_days, created_at)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (sub["id"], w["q"], w["city"], ",".join(w["cats"]),
                         w["remind_days"], now())).lastrowid
                    watch = self.db.execute("SELECT * FROM watch WHERE id = ?",
                                            (watch_id,)).fetchone()
                self._spend_mail(sub)
                self.db.execute("COMMIT")
                return watch, ""
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def _mail_budget(self, sub: sqlite3.Row) -> str:
        last = sub["last_confirm_mail"]
        if last and datetime.fromisoformat(last) > datetime.now() - CONFIRM_MAIL_GAP:
            return "too soon"
        today = datetime.now().date().isoformat()
        if sub["confirm_mail_day"] == today and sub["confirm_mails"] >= CONFIRM_MAILS_PER_DAY:
            return "daily limit"
        return ""

    def _spend_mail(self, sub: sqlite3.Row) -> None:
        today = datetime.now().date().isoformat()
        count = sub["confirm_mails"] + 1 if sub["confirm_mail_day"] == today else 1
        self.db.execute("UPDATE subscriber SET last_confirm_mail = ?, confirm_mail_day = ?,"
                        " confirm_mails = ? WHERE id = ?", (now(), today, count, sub["id"]))

    def confirm(self, token: str) -> tuple[sqlite3.Row, sqlite3.Row, bool] | None:
        """(watch, subscriber, newly_confirmed) for a valid confirm token."""
        with self.lock:
            watch = self._check("confirm", token, "watch", "created_at")
            if watch is None:
                return None
            fresh = watch["confirmed_at"] is None
            if fresh:
                stamp = now()
                self.db.execute("UPDATE watch SET confirmed_at = ? WHERE id = ?", (stamp, watch["id"]))
                self.db.execute("UPDATE subscriber SET confirmed_at = COALESCE(confirmed_at, ?)"
                                " WHERE id = ?", (stamp, watch["subscriber_id"]))
            watch = self.db.execute("SELECT * FROM watch WHERE id = ?", (watch["id"],)).fetchone()
            sub = self.db.execute("SELECT * FROM subscriber WHERE id = ?",
                                  (watch["subscriber_id"],)).fetchone()
            return watch, sub, fresh

    # -- managing -----------------------------------------------------------
    def subscriber_by_token(self, token: str) -> sqlite3.Row | None:
        with self.lock:
            return self._check("manage", token, "subscriber", "email")

    def watches(self, sub_id: int, confirmed_only: bool = True) -> list[sqlite3.Row]:
        with self.lock:
            sql = "SELECT * FROM watch WHERE subscriber_id = ?"
            if confirmed_only:
                sql += " AND confirmed_at IS NOT NULL"
            return self.db.execute(sql + " ORDER BY id", (sub_id,)).fetchall()

    def delete_watch(self, sub_id: int, watch_id: int) -> bool:
        """Delete one watch; a subscriber left with none is deleted too.
        Returns True when the subscriber is gone."""
        with self.lock:
            self.db.execute("DELETE FROM watch WHERE id = ? AND subscriber_id = ?",
                            (watch_id, sub_id))
            left = self.db.execute("SELECT COUNT(*) FROM watch WHERE subscriber_id = ?"
                                   " AND confirmed_at IS NOT NULL", (sub_id,)).fetchone()[0]
            if not left:
                self.db.execute("DELETE FROM subscriber WHERE id = ?", (sub_id,))
            return not left

    def unsubscribe(self, sub_id: int) -> None:
        with self.lock:
            self.db.execute("DELETE FROM subscriber WHERE id = ?", (sub_id,))

    # -- the notifier's side ------------------------------------------------
    def live_watches(self) -> list[sqlite3.Row]:
        """Confirmed watches with their subscriber's address, by subscriber."""
        with self.lock:
            return self.db.execute(
                "SELECT w.*, s.email FROM watch w JOIN subscriber s ON s.id = w.subscriber_id"
                " WHERE w.confirmed_at IS NOT NULL ORDER BY s.id, w.id").fetchall()

    def subscriber(self, sub_id: int) -> sqlite3.Row | None:
        with self.lock:
            return self.db.execute("SELECT * FROM subscriber WHERE id = ?", (sub_id,)).fetchone()

    def known(self, kinds: tuple[str, ...]) -> set[tuple[int, str]]:
        with self.lock:
            marks = ",".join("?" * len(kinds))
            rows = self.db.execute(f"SELECT watch_id, uid FROM sent WHERE kind IN ({marks})", kinds)
            return {(r[0], r[1]) for r in rows}

    def mark(self, rows: list[tuple[int, str, str, str]]) -> None:
        """rows: (watch_id, uid, kind, day)."""
        with self.lock:
            self.db.executemany("INSERT OR IGNORE INTO sent (watch_id, uid, kind, day)"
                                " VALUES (?, ?, ?, ?)", rows)

    def get_meta(self, key: str) -> str | None:
        with self.lock:
            row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.lock:
            self.db.execute("INSERT INTO meta (key, value) VALUES (?, ?)"
                            " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def count(self, metric: str, n: int = 1) -> None:
        if n:
            with self.lock:
                self.db.execute("INSERT INTO daily (day, metric, n) VALUES (?, ?, ?)"
                                " ON CONFLICT(day, metric) DO UPDATE SET n = n + excluded.n",
                                (date.today().isoformat(), metric, int(n)))

    def totals(self, days: int | None = None) -> dict[str, int]:
        """Counter sums over the last `days` days, today included; all time
        without `days`."""
        sql, args = "SELECT metric, SUM(n) FROM daily", ()
        if days:
            sql, args = sql + " WHERE day > ?", ((date.today() - timedelta(days=days)).isoformat(),)
        with self.lock:
            return {m: n for m, n in self.db.execute(sql + " GROUP BY metric", args)}

    def top_selections(self, limit: int = 5) -> list[sqlite3.Row]:
        """The filters most people follow, by how many follow each."""
        with self.lock:
            return self.db.execute(
                "SELECT q, city, cats, COUNT(*) AS n FROM watch WHERE confirmed_at IS NOT NULL"
                " GROUP BY q, city, cats ORDER BY n DESC, MIN(confirmed_at) LIMIT ?",
                (limit,)).fetchall()

    def with_reminder(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM watch WHERE confirmed_at IS NOT NULL"
                                   " AND remind_days IS NOT NULL").fetchone()[0]

    def prune(self) -> tuple[int, int]:
        """Forget sign-ups never confirmed within PENDING_TTL, and sent marks
        for events a month gone. Returns (watches, subscribers) removed."""
        cutoff = (datetime.now() - PENDING_TTL).isoformat(timespec="seconds")
        month_ago = (datetime.now() - timedelta(days=30)).date().isoformat()
        with self.lock:
            w = self.db.execute("DELETE FROM watch WHERE confirmed_at IS NULL AND created_at < ?",
                                (cutoff,)).rowcount
            s = self.db.execute("DELETE FROM subscriber WHERE id NOT IN (SELECT subscriber_id FROM watch)"
                                " AND created_at < ?", (cutoff,)).rowcount
            self.db.execute("DELETE FROM sent WHERE day < ?", (month_ago,))
            self.db.execute("DELETE FROM daily WHERE day < ?",
                            ((datetime.now() - timedelta(days=400)).date().isoformat(),))
            return w, s

    def stats(self) -> dict:
        with self.lock:
            one = lambda sql: self.db.execute(sql).fetchone()[0]  # noqa: E731
            return {
                "subscribers": one("SELECT COUNT(*) FROM subscriber WHERE confirmed_at IS NOT NULL"),
                "pending_subscribers": one("SELECT COUNT(*) FROM subscriber WHERE confirmed_at IS NULL"),
                "watches": one("SELECT COUNT(*) FROM watch WHERE confirmed_at IS NOT NULL"),
                "pending_watches": one("SELECT COUNT(*) FROM watch WHERE confirmed_at IS NULL"),
            }


def _load_secret(path: str) -> bytes:
    """The token key, made on first start. Lives beside the database rather
    than in .env: it is only meaningful with this database, and is backed up
    with it."""
    if not os.path.exists(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(os.urandom(32).hex())
    with open(path, encoding="ascii") as fh:
        return bytes.fromhex(fh.read().strip())
