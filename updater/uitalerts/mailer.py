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
        # Who hears about new sign-ups: ALERTS_ADMIN, else the contact address.
        self.admin_to = env.get("ALERTS_ADMIN") or self.contact
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

    def admin(self, subject: str, text: str) -> None:
        """A plain note to the site's owner; no-op without an address."""
        if self.admin_to:
            self.send(self.admin_to, subject, text,
                      _page("".join(f"<p>{_e(line)}</p>" for line in text.split("\n\n"))))

    # -- links --------------------------------------------------------------
    # Tokens ride in the fragment: never sent to the server, so they stay out
    # of access logs, analytics and Referer headers. The page reads them and
    # POSTs; a GET never changes anything, because mail scanners follow links.
    # The fragment keys stay Dutch on both pages; only the page differs.
    def link(self, action: str, token: str, lang: str = "nl") -> str:
        return f"{self.base}{PAGE[lang]}#{action}={token}"

    def one_click(self, token: str) -> str:
        return f"{self.base}/api/alerts/one-click?t={token}"

    # -- the three mails ----------------------------------------------------
    def confirm(self, to: str, w: dict, token: str, lang: str = "nl") -> None:
        t = TEXT[lang]
        url = self.link("bevestig", token, lang)
        what = W.describe(w, lang)
        home = W.site_url(self.base, {"q": "", "city": "all", "cats": []}, lang)
        text = (f"{t['asked'].format(site=self.base)}\n\n  {what}\n\n{t['confirm_here']}\n{url}\n\n"
                f"{t['not_you']}\n")
        body = _page(
            f"<p>{t['asked'].format(site=f'<a href=\"{home}\">uitjes</a>')}</p>"
            f"<p style=\"font-size:17px\"><b>{_e(what)}</b></p>"
            f"{_button(url, t['confirm_button'])}"
            f"<p style=\"color:#666\">{_e(t['not_you'])}</p>", lang)
        self.send(to, t["confirm_subject"], text, body)

    def announce(self, to: str, sections: list[tuple[dict, list[dict], int]], token: str,
                 lang: str = "nl") -> None:
        t = TEXT[lang]
        total = sum(len(evs) + more for _, evs, more in sections)
        subject = (t["new_one"].format(title=sections[0][1][0]["title"]) if total == 1
                   else t["new_many"].format(n=total))
        self._digest(to, subject, t["new_heading"], sections, token, lang)

    def remind(self, to: str, sections: list[tuple[dict, list[dict], int]], token: str,
               lang: str = "nl") -> None:
        t = TEXT[lang]
        first = sections[0][1][0]
        total = sum(len(evs) + more for _, evs, more in sections)
        subject = (t["soon_one"].format(title=first["title"], when=W.when(first, lang)) if total == 1
                   else t["soon_many"].format(n=total))
        self._digest(to, subject, t["soon_heading"], sections, token, lang)

    def _digest(self, to: str, subject: str, heading: str,
                sections: list[tuple[dict, list[dict], int]], token: str, lang: str = "nl") -> None:
        t = TEXT[lang]
        manage, leave = self.link("beheer", token, lang), self.link("afmelden", token, lang)
        home = W.site_url(self.base, {"q": "", "city": "all", "cats": []}, lang)
        text_parts, html_parts = [f"{heading}\n"], [f"<h1 style=\"font-size:20px\">{_e(heading)}</h1>"]
        for w, evs, more in sections:
            view = W.site_url(self.base, w, lang)
            what = W.describe(w, lang)
            text_parts.append(f"\n{what}\n")
            html_parts.append(f"<h2 style=\"font-size:15px;margin:22px 0 6px;color:{ACCENT}\">"
                              f"{_e(what)}</h2>")
            rows = []
            for ev in evs:
                place = " \u00b7 ".join(x for x in (ev.get("venue"), ev.get("city")) if x)
                link = ev.get("url") or view
                text_parts.append(f"- {W.when(ev, lang)}  {ev['title']}\n  {place}\n  {link}\n")
                rows.append(
                    "<tr><td style=\"padding:6px 12px 6px 0;vertical-align:top;white-space:nowrap;"
                    f"color:#555\">{_e(W.when(ev, lang))}</td><td style=\"padding:6px 0\">"
                    f"<a href=\"{_e(link)}\" style=\"color:#111;font-weight:bold\">{_e(ev['title'])}</a>"
                    f"<br><span style=\"color:#666\">{_e(place)}</span></td></tr>")
            html_parts.append("<table style=\"border-collapse:collapse\">" + "".join(rows) + "</table>")
            label = t["more"].format(n=more) if more else t["view"]
            if more:
                text_parts.append(f"  {label}: {view}\n")
            html_parts.append(f"<p><a href=\"{_e(view)}\" style=\"color:{ACCENT}\">{_e(label)}</a></p>")
        text_parts.append(f"\n{t['why'].format(site=self.base)}\n"
                          f"{t['manage']}: {manage}\n{t['leave']}: {leave}\n")
        html_parts.append(
            "<p style=\"color:#666;font-size:13px;margin-top:28px;border-top:1px solid #ddd;"
            f"padding-top:12px\">{t['why'].format(site=f'<a href=\"{home}\" style=\"color:#666\">uitjes</a>')} "
            f"<a href=\"{_e(manage)}\" style=\"color:#666\">{_e(t['manage'])}</a> \u00b7 "
            f"<a href=\"{_e(leave)}\" style=\"color:#666\">{_e(t['leave'])}</a></p>")
        self.send(to, subject, "".join(text_parts), _page("".join(html_parts), lang),
                  unsubscribe=self.one_click(token))


# The sign-up page for each language: /meldingen is Dutch, /en/alerts English
# (served from the same meldingen.html; see nginx/default.conf).
PAGE = {"nl": "/meldingen", "en": "/en/alerts"}

# Every string a subscriber reads, per language. {site} is either the bare URL
# (plain text) or a link to the home page (HTML).
TEXT = {
    "nl": {
        "asked": "Iemand, hopelijk jij, vroeg op {site} om een mail bij nieuwe uitjes voor:",
        "confirm_here": "Bevestig dat hier:",
        "confirm_button": "Bevestig je aanmelding",
        "not_you": ("Niet aangevraagd? Dan hoef je niets te doen: zonder bevestiging wordt dit adres "
                    "binnen 48 uur gewist, en je hoort niets meer van ons."),
        "confirm_subject": "Bevestig je uitjes-melding",
        "new_heading": "Nieuw aangekondigd",
        "new_one": "Nieuw op uitjes: {title}",
        "new_many": "{n} nieuwe uitjes voor je meldingen",
        "soon_heading": "Binnenkort",
        "soon_one": "Binnenkort: {title} ({when})",
        "soon_many": "Binnenkort: {n} uitjes uit je meldingen",
        "more": "En nog {n} meer op uitjes",
        "view": "Bekijk deze selectie op uitjes",
        "why": "Je krijgt deze mail omdat je dit op {site} hebt aangevraagd.",
        "manage": "Meldingen aanpassen",
        "leave": "Afmelden",
    },
    "en": {
        "asked": "Someone, hopefully you, asked on {site} for an email when new events appear for:",
        "confirm_here": "Confirm it here:",
        "confirm_button": "Confirm your sign-up",
        "not_you": ("Didn't ask for this? Then there's nothing to do: without confirmation this address "
                    "is deleted within 48 hours, and you won't hear from us again."),
        "confirm_subject": "Confirm your uitjes alert",
        "new_heading": "Just announced",
        "new_one": "New on uitjes: {title}",
        "new_many": "{n} new events for your alerts",
        "soon_heading": "Coming up",
        "soon_one": "Coming up: {title} ({when})",
        "soon_many": "Coming up: {n} events from your alerts",
        "more": "And {n} more on uitjes",
        "view": "See this selection on uitjes",
        "why": "You're getting this email because you asked for it on {site}.",
        "manage": "Manage your alerts",
        "leave": "Unsubscribe",
    },
}


def _e(value) -> str:
    return html.escape(str(value or ""), quote=True)


def _button(url: str, label: str) -> str:
    return (f"<p style=\"margin:22px 0\"><a href=\"{_e(url)}\" style=\"background:{ACCENT};"
            "color:#fff;padding:11px 18px;border-radius:4px;text-decoration:none;"
            f"font-weight:bold;display:inline-block\">{_e(label)}</a></p>")


def _page(inner: str, lang: str = "nl") -> str:
    return (f"<!doctype html><html lang=\"{lang}\"><body style=\"margin:0;padding:24px;"
            "font-family:Helvetica,Arial,sans-serif;font-size:15px;line-height:1.45;color:#111;"
            f"background:#fff\"><div style=\"max-width:600px\">{inner}</div></body></html>")
