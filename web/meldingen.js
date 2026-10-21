/* uitjes — mail alerts page. Talks to /api/alerts/ (updater/uitalerts).

   Which view shows is decided by the fragment: #bevestig=<token>,
   #beheer=<token>, #afmelden=<token>, or nothing for the sign-up form. The
   fragment is cleared once read, so a token does not linger in the address
   bar or the history. ?q=&city=&cat= prefill the form in the same shape the
   agenda's own URL uses, so its "mail me" link can hand over the view. */

/* Mirrors CATEGORY in app.js (plural label only). */
const CATEGORY = {
  concert: "Concerten", classical: "Klassiek", club: "Club", theatre: "Theater", dance: "Dans",
  comedy: "Comedy", film: "Film", festival: "Festivals", market: "Markten", exhibition: "Exposities",
  family: "Familie", talk: "Lezingen", sports: "Sport", quiz: "Quizzen", other: "Overig",
};
const REMIND = { 0: "op de dag zelf", 1: "een dag van tevoren", 3: "drie dagen van tevoren",
                 7: "een week van tevoren" };
const API = "/api/alerts/";
const $ = (sel) => document.querySelector(sel);

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
      body: JSON.stringify(body),
    });
  } catch (e) {
    return { error: "Geen verbinding. Probeer het zo opnieuw." };
  }
  let data = {};
  try { data = await res.json(); } catch (e) { /* a proxy error page */ }
  if (!res.ok && !data.error) data.error = "Er ging iets mis (" + res.status + "). Probeer het later opnieuw.";
  return data;
}

/* ---------- sign-up ---------- */
function buildForm(info) {
  const grid = $("#f-cats");
  for (const [key, label] of Object.entries(CATEGORY)) {
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
    c.append(" Vragen of verzoeken: ");
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
  if (!email || !$("#f-email").checkValidity()) return say("Vul een geldig e-mailadres in.", "bad");
  if (!q && !cats.length) return say("Kies minstens een zoekterm of een categorie.", "bad");
  const button = $("#f-submit");
  button.disabled = true;
  say("Bezig…");
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
  action("Bevestig je aanmelding om meldingen te gaan ontvangen.", "Bevestig", async () => {
    const data = await call("confirm", { token });
    $("#act").hidden = true;
    if (data.error) return say(data.error, "bad");
    say("Bevestigd. Je krijgt een mail zodra er iets nieuws bijkomt voor: " + data.watch + ".", "good");
    const p = document.createElement("p");
    p.innerHTML = '<a id="c-view">Bekijk wat er nu al is</a> · <a id="c-manage">Je meldingen beheren</a>';
    $("main").append(p);
    $("#c-view").href = data.url;
    $("#c-manage").href = "/meldingen#beheer=" + data.manage;
  });
}

function leaveView(token) {
  action("Afmelden voor alle uitjes-meldingen? Je adres en zoekopdrachten worden meteen gewist.",
         "Afmelden", async () => {
    const data = await call("unsubscribe", { token });
    $("#act").hidden = true;
    if (data.error) return say(data.error, "bad");
    say("Je bent afgemeld en je gegevens zijn gewist.", "good");
  });
}

/* ---------- manage ---------- */
async function manageView(token) {
  say("Laden…");
  const data = await call("manage", { token });
  if (data.error) return say(data.error, "bad");
  say("");
  $("#m-email").textContent = data.email;
  const list = $("#m-list");
  list.replaceChildren();
  if (!data.watches.length) list.append(Object.assign(document.createElement("li"), { textContent: "Geen meldingen." }));
  for (const w of data.watches) {
    const li = document.createElement("li");
    const what = document.createElement("a");
    what.href = w.url;
    what.textContent = w.text;
    const when = document.createElement("span");
    when.className = "hint";
    when.textContent = w.remind_days == null ? "geen herinnering" : "herinnering " + REMIND[w.remind_days];
    const del = document.createElement("button");
    del.type = "button";
    del.className = "link";
    del.textContent = "Verwijderen";
    del.onclick = async () => {
      del.disabled = true;
      const r = await call("delete-watch", { token, id: w.id });
      if (r.error) return say(r.error, "bad");
      if (r.gone) {
        $("#manage").hidden = true;
        return say("Dat was je laatste melding: je bent afgemeld en je adres is gewist.", "good");
      }
      li.remove();
    };
    li.append(what, " ", when, " ", del);
    list.append(li);
  }
  $("#m-leave").onclick = () => { $("#manage").hidden = true; leaveView(token); };
  $("#manage").hidden = false;
}

/* ---------- start ---------- */
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
  if (!info) return say("Meldingen zijn op dit moment niet beschikbaar. Probeer het later opnieuw.", "bad");
  // Until the mailbox exists the service writes mail to a folder instead of
  // sending it; a sign-up then would wait for a mail that never comes.
  // ?proef opens the form anyway, for testing.
  if (info.mail !== "smtp" && !new URLSearchParams(location.search).has("proef")) {
    return say("Meldingen per mail komen binnenkort.");
  }
  buildForm(info);
})();
