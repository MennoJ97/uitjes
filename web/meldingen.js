/* uitjes — mail alerts page. Talks to /api/alerts/ (updater/uitalerts).

   Which view shows is decided by the fragment: #bevestig=<token>,
   #beheer=<token>, #afmelden=<token>, or nothing for the sign-up form. The
   fragment is cleared once read, so a token does not linger in the address
   bar or the history. ?q=&city=&cat= prefill the form in the same shape the
   agenda's own URL uses, so its "mail me" link can hand over the view. */

/* Dutch at /meldingen, English at /en/alerts (since 2026-10-21): <html lang> is
   set per path with SSI, as on the agenda. Every call tells the API which, so
   its messages and the mails come back in the same language. */
const LANG = /^en\b/.test(document.documentElement.lang) ? "en" : "nl";

const T = {
  nl: {
    /* Mirrors CATEGORY in app.js (plural label only). */
    category: {
      concert: "Concerten", classical: "Klassiek", club: "Club", theatre: "Theater", dance: "Dans",
      comedy: "Comedy", film: "Film", festival: "Festivals", market: "Markten", exhibition: "Exposities",
      family: "Familie", talk: "Lezingen", sports: "Sport", quiz: "Quizzen", openmic: "Open podia",
      other: "Overig",
    },
    remind: { 0: "op de dag zelf", 1: "een dag van tevoren", 3: "drie dagen van tevoren",
              7: "een week van tevoren" },
    offline: "Geen verbinding. Probeer het zo opnieuw.",
    failed: (status) => `Er ging iets mis (${status}). Probeer het later opnieuw.`,
    questions: " Vragen of verzoeken: ",
    badEmail: "Vul een geldig e-mailadres in.",
    needSomething: "Kies minstens een zoekterm of een categorie.",
    busy: "Bezig…", loading: "Laden…",
    confirmText: "Bevestig je aanmelding om meldingen te gaan ontvangen.", confirm: "Bevestig",
    confirmed: (watch) => `Bevestigd. Je krijgt een mail zodra er iets nieuws bijkomt voor: ${watch}.`,
    seeNow: "Bekijk wat er nu al is", manage: "Je meldingen beheren",
    leaveText: "Afmelden voor alle uitjes-meldingen? Je adres en zoekopdrachten worden meteen gewist.",
    leave: "Afmelden", left: "Je bent afgemeld en je gegevens zijn gewist.",
    none: "Geen meldingen.", noReminder: "geen herinnering", reminder: (r) => "herinnering " + r,
    remove: "Verwijderen",
    lastGone: "Dat was je laatste melding: je bent afgemeld en je adres is gewist.",
    unavailable: "Meldingen zijn op dit moment niet beschikbaar. Probeer het later opnieuw.",
    soon: "Meldingen per mail komen binnenkort.",
  },
  en: {
    category: {
      concert: "Concerts", classical: "Classical", club: "Club", theatre: "Theatre", dance: "Dance",
      comedy: "Comedy", film: "Film", festival: "Festivals", market: "Markets", exhibition: "Exhibitions",
      family: "Family", talk: "Talks", sports: "Sport", quiz: "Quizzes", openmic: "Open mics",
      other: "Other",
    },
    remind: { 0: "on the day itself", 1: "the day before", 3: "three days before", 7: "a week before" },
    offline: "No connection. Try again in a moment.",
    failed: (status) => `Something went wrong (${status}). Try again later.`,
    questions: " Questions or requests: ",
    badEmail: "Enter a valid email address.",
    needSomething: "Choose at least a search term or a category.",
    busy: "Working…", loading: "Loading…",
    confirmText: "Confirm your sign-up to start receiving alerts.", confirm: "Confirm",
    confirmed: (watch) => `Confirmed. You will get an email as soon as something new turns up for: ${watch}.`,
    seeNow: "See what is on already", manage: "Manage your alerts",
    leaveText: "Unsubscribe from all uitjes alerts? Your address and searches are erased at once.",
    leave: "Unsubscribe", left: "You have been unsubscribed and your details erased.",
    none: "No alerts.", noReminder: "no reminder", reminder: (r) => "reminder " + r,
    remove: "Remove",
    lastGone: "That was your last alert: you have been unsubscribed and your address erased.",
    unavailable: "Alerts are not available right now. Try again later.",
    soon: "Alerts by email are coming soon.",
  },
}[LANG];
const API = "/api/alerts/";
const $ = (sel) => document.querySelector(sel);

/* The agenda's own paths, as langPath() in app.js spells them. */
const langPath = (lang, path) => (lang === "en" ? "/en" + (path === "/" ? "" : path) : path);

function say(text, kind) {
  const s = $("#status");
  s.textContent = text || "";
  s.className = "alert-status" + (kind ? " " + kind : "");
}

async function call(action, body) {
  let res;
  try {
    res = await fetch(API + action, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, lang: LANG }),
    });
  } catch (e) {
    return { error: T.offline };
  }
  let data = {};
  try { data = await res.json(); } catch (e) { /* a proxy error page */ }
  if (!res.ok && !data.error) data.error = T.failed(res.status);
  return data;
}

/* ---------- sign-up ---------- */
function buildForm(info) {
  const grid = $("#f-cats");
  for (const [key, label] of Object.entries(T.category)) {
    const l = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = key;
    l.append(box, " " + label);
    grid.append(l);
  }
  const p = new URLSearchParams(location.search);
  $("#f-q").value = p.get("q") || "";
  setCity(p.get("city") || "all");
  const cats = new Set((p.get("cat") || "").split(",").map((c) => c.trim()));
  grid.querySelectorAll("input").forEach((b) => { b.checked = cats.has(b.value); });

  $("#f-city").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-city]");
    if (b) setCity(b.dataset.city);
  });
  if (info.contact) {
    const c = $("#contact");
    c.append(T.questions);
    const a = document.createElement("a");
    a.href = "mailto:" + info.contact;
    a.textContent = info.contact;
    c.append(a, ".");
  }
  $("#signup").addEventListener("submit", submit);
  $("#signup").hidden = false;
}

function setCity(city) {
  const known = ["all", "Amsterdam", "Utrecht"].find((c) => c.toLowerCase() === String(city).toLowerCase()) || "all";
  document.querySelectorAll("#f-city button").forEach((b) => {
    const on = b.dataset.city === known;
    b.classList.toggle("on", on);
    b.setAttribute("aria-checked", on ? "true" : "false");
  });
}

async function submit(e) {
  e.preventDefault();
  const email = $("#f-email").value.trim();
  const q = $("#f-q").value.trim();
  const cats = [...document.querySelectorAll("#f-cats input:checked")].map((b) => b.value);
  if (!email || !$("#f-email").checkValidity()) return say(T.badEmail, "bad");
  if (!q && !cats.length) return say(T.needSomething, "bad");
  const button = $("#f-submit");
  button.disabled = true;
  say(T.busy);
  const data = await call("subscribe", {
    email, q, cats,
    city: $("#f-city button.on").dataset.city,
    remind_days: $("#f-remind").value,
    website: $("#f-website").value,
  });
  button.disabled = false;
  if (data.error) return say(data.error, "bad");
  $("#signup").hidden = true;
  say(data.message, "good");
}

/* ---------- confirm / unsubscribe ---------- */
function action(text, label, run) {
  $("#act-text").textContent = text;
  const b = $("#act-button");
  b.textContent = label;
  b.onclick = async () => { b.disabled = true; await run(); };
  $("#act").hidden = false;
}

function confirmView(token) {
  action(T.confirmText, T.confirm, async () => {
    const data = await call("confirm", { token });
    $("#act").hidden = true;
    if (data.error) return say(data.error, "bad");
    say(T.confirmed(data.watch), "good");
    const p = document.createElement("p");
    const view = Object.assign(document.createElement("a"), { href: data.url, textContent: T.seeNow });
    const manage = Object.assign(document.createElement("a"), { textContent: T.manage });
    manage.href = location.pathname + "#beheer=" + data.manage;
    p.append(view, " · ", manage);
    $("main").append(p);
  });
}

function leaveView(token) {
  action(T.leaveText, T.leave, async () => {
    const data = await call("unsubscribe", { token });
    $("#act").hidden = true;
    if (data.error) return say(data.error, "bad");
    say(T.left, "good");
  });
}

/* ---------- manage ---------- */
async function manageView(token) {
  say(T.loading);
  const data = await call("manage", { token });
  if (data.error) return say(data.error, "bad");
  say("");
  $("#m-email").textContent = data.email;
  const list = $("#m-list");
  list.replaceChildren();
  if (!data.watches.length) list.append(Object.assign(document.createElement("li"), { textContent: T.none }));
  for (const w of data.watches) {
    const li = document.createElement("li");
    const what = document.createElement("a");
    what.href = w.url;
    what.textContent = w.text;
    const when = document.createElement("span");
    when.className = "hint";
    when.textContent = w.remind_days == null ? T.noReminder : T.reminder(T.remind[w.remind_days]);
    const del = document.createElement("button");
    del.type = "button";
    del.className = "link";
    del.textContent = T.remove;
    del.onclick = async () => {
      del.disabled = true;
      const r = await call("delete-watch", { token, id: w.id });
      if (r.error) return say(r.error, "bad");
      if (r.gone) {
        $("#manage").hidden = true;
        return say(T.lastGone, "good");
      }
      li.remove();
    };
    li.append(what, " ", when, " ", del);
    list.append(li);
  }
  $("#m-leave").onclick = () => { $("#manage").hidden = true; leaveView(token); };
  $("#manage").hidden = false;
}

/* ---------- the way back ----------
   The back link and "Agenda" in the bar go to the agenda showing the
   selection this page was opened with (the agenda's "Volg deze selectie"
   hands it over as ?q=&city=&cat=), on the city's own page, as app.js puts
   it. With nothing handed over they stay the plain front page SSI wrote. */
function backToAgenda() {
  const p = new URLSearchParams(location.search);
  const city = (p.get("city") || "").toLowerCase();
  const path = langPath(LANG, ["amsterdam", "utrecht"].includes(city) ? "/" + city : "/");
  const qs = new URLSearchParams();
  if (p.get("q")) qs.set("q", p.get("q"));
  if (p.get("cat")) qs.set("cat", p.get("cat"));
  const href = path + (qs.toString() ? "?" + qs.toString().replace(/%2C/g, ",") : "");
  document.querySelectorAll("a.agenda-link").forEach((a) => { a.href = href; });
}

/* The language switch stores the choice, as on the agenda (app.js); nginx
   then sends /meldingen and /en/alerts to the chosen one, fragment and all. */
$("#lang-switch")?.addEventListener("click", (e) => {
  try {
    document.cookie = `uitjes_lang=${e.currentTarget.dataset.lang}; Max-Age=31536000; Path=/; SameSite=Lax` +
      (location.protocol === "https:" ? "; Secure" : "");
  } catch (err) { /* cookies off: the link still works, it just is not remembered */ }
});

/* ---------- start ---------- */
backToAgenda();
(async function start() {
  const m = /^#(bevestig|beheer|afmelden)=([\w.-]+)$/.exec(location.hash);
  if (m) {
    try { history.replaceState(null, "", location.pathname + location.search); } catch (e) { /* ignore */ }
    if (m[1] === "bevestig") return confirmView(m[2]);
    if (m[1] === "afmelden") return leaveView(m[2]);
    return manageView(m[2]);
  }
  let info = null;
  try { info = await (await fetch(API + "info")).json(); } catch (e) { /* down */ }
  if (!info) return say(T.unavailable, "bad");
  // Until the mailbox exists the service writes mail to a folder instead of
  // sending it; a sign-up then would wait for a mail that never comes.
  // ?proef opens the form anyway, for testing.
  if (info.mail !== "smtp" && !new URLSearchParams(location.search).has("proef")) {
    return say(T.soon);
  }
  buildForm(info);
})();
