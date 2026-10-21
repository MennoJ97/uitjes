"""Composing and sending the three mails: confirm, new events, reminder.

Sends through SMTP when SMTP_HOST is set (STARTTLS on 587, implicit TLS on
465), and otherwise writes each mail as an .eml file into <data>/outbox - the
dry-run mode, and what runs until the mailbox exists. ALERTS_MAIL=outbox forces
the outbox even with SMTP configured.

No tracking of any kind: no pixels, no redirecting links. Every mail after the
confirmation carries List-Unsubscribe with the one-click POST (RFC 8058), which
Gmail and Outlook show as their own "unsubscribe" button.
"""
from __future__ import annotations

import html
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from . import watch as W

ACCENT = "#5E6A00"  # --uitjes in mhjansen.nl/family.css
PER_WATCH = 25      # events listed per watch in one mail; the rest is a link


class Mailer:
    def __init__(self, env: dict, data_dir: str, base_url: str):
        self.base = base_url.rstrip("/")
        self.host = env.get("SMTP_HOST", "").strip()
        self.port = int(env.get("SMTP_PORT") or 587)
        self.user = env.get("SMTP_USER", "")
        self.password = env.get("SMTP_PASSWORD", "")
        self.sender = env.get("SMTP_FROM") or f"uitjes <{self.user or 'noreply@localhost'}>"
        # Shown on the page and used as Reply-To; empty until there is a real
        # address, rather than advertising a placeholder.
        self.contact = env.get("ALERTS_CONTACT") or (parseaddr(env["SMTP_FROM"])[1]
                                                     if env.get("SMTP_FROM") else "")
        self.mode = "smtp" if self.host and env.get("ALERTS_MAIL") != "outbox" else "outbox"
        self.outbox = os.path.join(data_dir, "outbox")

    # -- transport ----------------------------------------------------------
    def send(self, to: str, subject: str, text: str, body_html: str,
             unsubscribe: str | None = None) -> None:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        domain = parseaddr(self.sender)[1].rpartition("@")[2] or "localhost"
        msg["Message-ID"] = make_msgid(domain=domain)
        msg["Auto-Submitted"] = "auto-generated"
        if self.contact:
            msg["Reply-To"] = self.contact
        if unsubscribe:
            msg["List-Unsubscribe"] = f"<{unsubscribe}>"
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        msg.set_content(text)
        msg.add_alternative(body_html, subtype="html")

        if self.mode == "outbox":
            os.makedirs(self.outbox, exist_ok=True)
            name = datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".eml"
            with open(os.path.join(self.outbox, name), "wb") as fh:
                fh.write(bytes(msg))
            return
        context = ssl.create_default_context()
        if self.port == 465:
            server = smtplib.SMTP_SSL(self.host, self.port, timeout=30, context=context)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=30)
            server.starttls(context=context)
        with server:
            if self.user:
                server.login(self.user, self.password)
            server.send_message(msg)

    # -- links --------------------------------------------------------------
    # Tokens ride in the fragment: never sent to the server, so they stay out
    # of access logs, analytics and Referer headers. The page reads them and
    # POSTs; a GET never changes anything, because mail scanners follow links.
    def link(self, action: str, token: str) -> str:
        return f"{self.base}/meldingen#{action}={token}"

    def one_click(self, token: str) -> str:
        return f"{self.base}/api/alerts/one-click?t={token}"

    # -- the three mails ----------------------------------------------------
    def confirm(self, to: str, w: dict, token: str) -> None:
        url = self.link("bevestig", token)
        what = W.describe(w)
        text = (f"Iemand, hopelijk jij, vroeg op {self.base} om een mail bij nieuwe uitjes voor:\n\n"
                f"  {what}\n\nBevestig dat hier:\n{url}\n\n"
                "Niet aangevraagd? Dan hoef je niets te doen: zonder bevestiging wordt dit adres "
                "binnen 48 uur gewist, en je hoort niets meer van ons.\n")
        body = _page(
            f"<p>Iemand, hopelijk jij, vroeg op <a href=\"{self.base}\">uitjes</a> om een mail bij "
            f"nieuwe uitjes voor:</p><p style=\"font-size:17px\"><b>{_e(what)}</b></p>"
            f"{_button(url, 'Bevestig je aanmelding')}"
            "<p style=\"color:#666\">Niet aangevraagd? Dan hoef je niets te doen: zonder bevestiging "
            "wordt dit adres binnen 48 uur gewist, en je hoort niets meer van ons.</p>")
        self.send(to, "Bevestig je uitjes-melding", text, body)

    def announce(self, to: str, sections: list[tuple[dict, list[dict], int]], token: str) -> None:
        total = sum(len(evs) + more for _, evs, more in sections)
        subject = (f"Nieuw op uitjes: {sections[0][1][0]['title']}" if total == 1
                   else f"{total} nieuwe uitjes voor je meldingen")
        self._digest(to, subject, "Nieuw aangekondigd", sections, token)

    def remind(self, to: str, sections: list[tuple[dict, list[dict], int]], token: str) -> None:
        first = sections[0][1][0]
        total = sum(len(evs) + more for _, evs, more in sections)
        subject = (f"Binnenkort: {first['title']} ({W.when(first)})" if total == 1
                   else f"Binnenkort: {total} uitjes uit je meldingen")
        self._digest(to, subject, "Binnenkort", sections, token)

    def _digest(self, to: str, subject: str, heading: str,
                sections: list[tuple[dict, list[dict], int]], token: str) -> None:
        manage, leave = self.link("beheer", token), self.link("afmelden", token)
        text_parts, html_parts = [f"{heading}\n"], [f"<h1 style=\"font-size:20px\">{_e(heading)}</h1>"]
        for w, evs, more in sections:
            view = W.site_url(self.base, w)
            text_parts.append(f"\n{W.describe(w)}\n")
            html_parts.append(f"<h2 style=\"font-size:15px;margin:22px 0 6px;color:{ACCENT}\">"
                              f"{_e(W.describe(w))}</h2>")
            rows = []
            for ev in evs:
                place = " · ".join(x for x in (ev.get("venue"), ev.get("city")) if x)
                link = ev.get("url") or view
                text_parts.append(f"- {W.when(ev)}  {ev['title']}\n  {place}\n  {link}\n")
                rows.append(
                    "<tr><td style=\"padding:6px 12px 6px 0;vertical-align:top;white-space:nowrap;"
                    f"color:#555\">{_e(W.when(ev))}</td><td style=\"padding:6px 0\">"
                    f"<a href=\"{_e(link)}\" style=\"color:#111;font-weight:bold\">{_e(ev['title'])}</a>"
                    f"<br><span style=\"color:#666\">{_e(place)}</span></td></tr>")
            html_parts.append("<table style=\"border-collapse:collapse\">" + "".join(rows) + "</table>")
            if more:
                text_parts.append(f"  ...en nog {more} meer: {view}\n")
                html_parts.append(f"<p><a href=\"{_e(view)}\" style=\"color:{ACCENT}\">"
                                  f"En nog {more} meer op uitjes</a></p>")
            else:
                html_parts.append(f"<p><a href=\"{_e(view)}\" style=\"color:{ACCENT}\">"
                                  "Bekijk deze selectie op uitjes</a></p>")
        text_parts.append(f"\nJe krijgt deze mail omdat je dit op {self.base} hebt aangevraagd.\n"
                          f"Meldingen aanpassen: {manage}\nAfmelden: {leave}\n")
        html_parts.append(
            "<p style=\"color:#666;font-size:13px;margin-top:28px;border-top:1px solid #ddd;"
            f"padding-top:12px\">Je krijgt deze mail omdat je dit op <a href=\"{self.base}\" "
            f"style=\"color:#666\">uitjes</a> hebt aangevraagd. <a href=\"{_e(manage)}\" "
            f"style=\"color:#666\">Meldingen aanpassen</a> · <a href=\"{_e(leave)}\" "
            "style=\"color:#666\">Afmelden</a></p>")
        self.send(to, subject, "".join(text_parts), _page("".join(html_parts)),
                  unsubscribe=self.one_click(token))


def _e(value) -> str:
    return html.escape(str(value or ""), quote=True)


def _button(url: str, label: str) -> str:
    return (f"<p style=\"margin:22px 0\"><a href=\"{_e(url)}\" style=\"background:{ACCENT};"
            "color:#fff;padding:11px 18px;border-radius:4px;text-decoration:none;"
            f"font-weight:bold;display:inline-block\">{_e(label)}</a></p>")


def _page(inner: str) -> str:
    return ("<!doctype html><html lang=\"nl\"><body style=\"margin:0;padding:24px;"
            "font-family:Helvetica,Arial,sans-serif;font-size:15px;line-height:1.45;color:#111;"
            f"background:#fff\"><div style=\"max-width:600px\">{inner}</div></body></html>")
