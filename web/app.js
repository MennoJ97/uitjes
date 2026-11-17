/* uitjes front-end — no dependencies, reads data/events.json */

/* Dutch at /, English under /en (since 2026-10-21). index.html sets <html lang>
   per path with SSI and holds its own labels; everything this file draws comes
   from T below. The events are as the venues wrote them, in Dutch, in both. */
const LANG = /^en\b/.test(document.documentElement.lang) ? "en" : "nl";
const OTHER_LANG = LANG === "en" ? "nl" : "en";
const LOCALE = LANG === "en" ? "en-GB" : "nl-NL";

const T = {
  nl: {
    today: "vandaag", tomorrow: "morgen",
    of: (n, total) => `${n} van ${total}`,
    follow: "Volg deze selectie", free: "gratis",
    allDay: "hele dag", timeUnknown: "tijd onbekend", until: "t/m", unconfirmed: "onbevestigd",
    running: "doorlopend", events: (n) => (n === 1 ? "uitje" : "uitjes"), show: "tonen", hide: "verbergen",
    nothing: "Niets gevonden met deze filters.", clearAll: "Alle filters wissen", clear: "Filters wissen",
    both: "Beide", allCats: "Alle categorieën", nCats: (n) => `${n} categorieën`, showAll: "Alles tonen",
    off: "uit", now: (n, err) => `nu ${n}: ${err}`,
    carried: (when, err) => `onbereikbaar, toont de versie van ${when}: ${err}`,
    expired: (when, err) => `onbereikbaar sinds ${when}, weggelaten: ${err}`,
    refused: (at, problems, built) => `Laatste poging (${at}) niet gepubliceerd: ${problems}. ` +
      `Hieronder de laatste build die wel doorkwam, van ${built}.`,
    showFilters: "Filters tonen", hideFilters: "Filters verbergen",
    footnote: (total, live, days, when) => `${total} uitjes uit ${live} ${live === 1 ? "bron" : "bronnen"}, ` +
      `${days} dagen vooruit. Bijgewerkt ${when}.`,
    heldBack: " Nieuwere builds zijn tegengehouden, zie Bronnen.",
    loadFail: (err) => `De volledige agenda kon niet worden geladen, de filters werken daardoor niet. (${err})`,
  },
  en: {
    today: "today", tomorrow: "tomorrow",
    of: (n, total) => `${n} of ${total}`,
    follow: "Follow this selection", free: "free",
    allDay: "all day", timeUnknown: "time unknown", until: "until", unconfirmed: "unconfirmed",
    running: "ongoing", events: (n) => (n === 1 ? "event" : "events"), show: "show", hide: "hide",
    nothing: "Nothing found with these filters.", clearAll: "Clear all filters", clear: "Clear filters",
    both: "Both", allCats: "All categories", nCats: (n) => `${n} categories`, showAll: "Show all",
    off: "off", now: (n, err) => `now ${n}: ${err}`,
    carried: (when, err) => `unreachable, showing the copy from ${when}: ${err}`,
    expired: (when, err) => `unreachable since ${when}, left out: ${err}`,
    refused: (at, problems, built) => `Last attempt (${at}) not published: ${problems}. ` +
      `Below is the last build that did go through, from ${built}.`,
    showFilters: "Show filters", hideFilters: "Hide filters",
    footnote: (total, live, days, when) => `${total} events from ${live} ${live === 1 ? "source" : "sources"}, ` +
      `${days} days ahead. Updated ${when}.`,
    heldBack: " Newer builds have been held back, see Sources.",
    loadFail: (err) => `The full listing could not be loaded, so the filters do not work. (${err})`,
  },
}[LANG];

/* Category keys are the data's (English, from the builder); what people see is
   in the page's language. Plural for the filter menu, singular for the line
   under an event. The Dutch labels mirror CATEGORY in uitradar/pages.py, which
   the mail alerts match against. */
const CATEGORY_NL = {
  concert: ["Concerten", "concert"], classical: ["Klassiek", "klassiek"], club: ["Club", "club"],
  theatre: ["Theater", "theater"], dance: ["Dans", "dans"], comedy: ["Comedy", "comedy"],
  film: ["Film", "film"], festival: ["Festivals", "festival"], market: ["Markten", "markt"],
  exhibition: ["Exposities", "expositie"], family: ["Familie", "familie"], talk: ["Lezingen", "lezing"],
  sports: ["Sport", "sport"], quiz: ["Quizzen", "quiz"],
  openmic: ["Open podia", "open podium"], other: ["Overig", "overig"],
};
const CATEGORY_EN = {
  concert: ["Concerts", "concert"], classical: ["Classical", "classical"], club: ["Club", "club"],
  theatre: ["Theatre", "theatre"], dance: ["Dance", "dance"], comedy: ["Comedy", "comedy"],
  film: ["Film", "film"], festival: ["Festivals", "festival"], market: ["Markets", "market"],
  exhibition: ["Exhibitions", "exhibition"], family: ["Family", "family"], talk: ["Talks", "talk"],
  sports: ["Sport", "sport"], quiz: ["Quizzes", "quiz"],
  openmic: ["Open mics", "open mic"], other: ["Other", "other"],
};
const CATEGORY = LANG === "en" ? CATEGORY_EN : CATEGORY_NL;
const catPlural = (c) => (CATEGORY[c] || [c])[0];
const catSingular = (c) => (CATEGORY[c] || [c, c])[1];

const state = { q: "", city: "all", cats: new Set(), range: "all", date: null, free: false };

/* /amsterdam and /utrecht are this page with one city chosen, each with its own
   title and its own events in the HTML (nginx/default.conf). The city
   filter and the path are one thing: a city page opens with its city chosen,
   and choosing a city moves the address to its page. Keys are the paths, in
   step with that nginx regex and CITY_PAGES in uitradar/pages.py. */
const CITY_PAGES = { amsterdam: "Amsterdam", utrecht: "Utrecht" };
const cityPath = (city) => (city !== "all" && CITY_PAGES[city.toLowerCase()] ? "/" + city.toLowerCase() : null);
/* Each of those again under /en, as lang_path() in uitradar/pages.py spells it:
   "/" is /en, "/amsterdam" is /en/amsterdam. BASE_PATH is this page's path
   with the language taken off. */
const langPath = (lang, path) => (lang === "en" ? "/en" + (path === "/" ? "" : path) : path);
const PAGE_PATHS = new Set(["nl", "en"].flatMap((lang) =>
  ["/", ...Object.keys(CITY_PAGES).map((c) => "/" + c)].map((p) => langPath(lang, p))));
const BASE_PATH = location.pathname.replace(/^\/en(?=\/|$)/, "") || "/";
let DATA = { events: [], sources: [], counts: {}, generated_at: null };

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};
const NUM = new Intl.NumberFormat(LOCALE);

/* ---------- dates ---------- */
const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const TODAY = startOfDay(new Date());
const dayKey = (iso) => iso.slice(0, 10);
const asDate = (iso) => new Date(iso.slice(0, 10) + "T00:00:00");
// toISOString() would shift us a day west of CEST, so build the key locally.
const localKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-` +
                        `${String(d.getDate()).padStart(2, "0")}`;
const TODAY_KEY = localKey(TODAY);

function weekendWindow() {
  // Friday 00:00 through Sunday 23:59 of the current or upcoming weekend.
  const dow = TODAY.getDay();                  // Sun = 0
  const toFriday = dow === 0 ? -2 : 5 - dow;   // Sunday counts as this weekend
  const from = new Date(TODAY); from.setDate(from.getDate() + toFriday);
  const to = new Date(from); to.setDate(to.getDate() + 2);
  return [from < TODAY ? TODAY : from, to];
}

/** [from, to] of the active date filter; `to` is null for "everything". */
function rangeWindow() {
  if (state.range === "date") {
    const day = asDate(state.date);
    return [day, day];
  }
  if (state.range === "all") return [TODAY, null];
  if (state.range === "today") return [TODAY, TODAY];
  if (state.range === "weekend") return weekendWindow();
  const limit = new Date(TODAY);
  limit.setDate(limit.getDate() + parseInt(state.range, 10));
  return [TODAY, limit];
}

function inRange(ev) {
  const [from, to] = rangeWindow();
  const start = asDate(ev.start);
  const end = ev.end ? asDate(ev.end) : start;
  // "Alles" has no end but still starts today. Without that lower bound, a
  // stale events.json (the builder refusing to publish, say) turns every
  // concert of the last few days into a "doorlopend" run.
  if (!to) return end >= from;
  return start <= to && end >= from;   // any overlap with the window counts
}

const LONG_FMT = new Intl.DateTimeFormat(LOCALE, { weekday: "long", day: "numeric", month: "long" });
const UNTIL_FMT = new Intl.DateTimeFormat(LOCALE, { day: "numeric", month: "short" });
const WEEKDAY = new Intl.DateTimeFormat(LOCALE, { weekday: "long" });
const MONTH = new Intl.DateTimeFormat(LOCALE, { month: "long" });

/** The pieces of the date column: "vandaag" / 18 / vrijdag / september. */
function dayParts(key) {
  const d = asDate(key);
  const diff = Math.round((d - TODAY) / 86400000);
  const month = MONTH.format(d) + (d.getFullYear() === TODAY.getFullYear() ? "" : " " + d.getFullYear());
  return {
    rel: diff === 0 ? T.today : diff === 1 ? T.tomorrow : null,
    num: String(d.getDate()),
    wd: WEEKDAY.format(d),
    mo: month,
  };
}

/* ---------- filtering ---------- */
function matches(ev) {
  if (state.city !== "all" && ev.city !== state.city) return false;
  if (state.cats.size && !ev.categories.some((c) => state.cats.has(c))) return false;
  // `free` is decided by the builder from the price text (is_free in
  // uitradar/model.py), so "Gratis voor jaarleden" is not a free night here.
  if (state.free && !ev.free) return false;
  if (!inRange(ev)) return false;
  if (state.q) {
    // Both languages' labels in the haystack too, so "theater" or "markt" finds
    // what the data files under theatre and market, on either page. `search` is
    // words the card does not show (a long description, a merged duplicate's
    // title): "ghibli" finds Ziggo Dome's Joe Hisaishi night, whose title never
    // says it.
    const hay = [ev.title, ev.venue, ev.city, ev.summary, ev.search, ...ev.categories,
                 ...ev.categories.flatMap((c) => [...(CATEGORY_NL[c] || []), ...(CATEGORY_EN[c] || [])])]
      .filter(Boolean).join(" ").toLowerCase();
    if (!state.q.split(/\s+/).every((t) => hay.includes(t))) return false;
  }
  return true;
}

/* ---------- filters in the URL ----------
   The whole point of this page is sending someone a link, so a filtered view
   has to survive being copied out of the address bar: ?city=Amsterdam&
   cat=comedy&when=weekend is an answer to "what shall we do Saturday", where
   the bare URL is only ever the unfiltered front page.

   replaceState rather than pushState: every keystroke in the search box is a
   state change, and pushing those would make the back button walk out of the
   query one letter at a time instead of leaving the page. Defaults are left
   out, so an untouched page keeps a clean URL. The parameter names and values
   stay English (they are the data's keys), so links shared before the page
   went Dutch keep working. */
const URL_RANGES = new Set(["today", "weekend", "7", "30", "all"]);
// The search box's text as typed. state.q is the lowercased form matches() uses,
// but a link reading ?q=Jeff+Buckley is friendlier than ?q=jeff+buckley, and the
// search is case-insensitive either way.
let qRaw = "";

function readUrlState() {
  const p = new URLSearchParams(location.search);

  qRaw = (p.get("q") || "").trim();
  state.q = qRaw.toLowerCase();

  const pageCity = CITY_PAGES[BASE_PATH.slice(1)];
  if (pageCity) state.city = pageCity;

  // Everything below is checked against what this build actually contains, so
  // a link that outlived a renamed city or a category that no longer has any
  // events degrades to the unfiltered page rather than to a blank one. A
  // ?city= from before the city pages still works, and moves to its page.
  const city = (p.get("city") || "").toLowerCase();
  const known = (DATA.cities || []).find((c) => c.toLowerCase() === city);
  if (known) state.city = known;

  (p.get("cat") || "").split(",").forEach((c) => {
    const key = c.trim().toLowerCase();
    if (CATEGORY[key]) state.cats.add(key);
  });
  state.free = p.get("free") === "1" && Boolean(DATA.counts.free);

  // A link naming a day that has already passed is stale rather than wrong, and
  // an empty page explains nothing — fall back to the default window.
  const date = p.get("date");
  if (date && /^\d{4}-\d{2}-\d{2}$/.test(date) && date >= TODAY_KEY) {
    state.range = "date";
    state.date = date;
    return;
  }
  const when = p.get("when");
  if (when && URL_RANGES.has(when)) state.range = when;
}

function writeUrlState() {
  // On the site the city is the path. Anywhere else (a local file server, say)
  // there is only the one page, and the city stays a parameter.
  const onSite = PAGE_PATHS.has(location.pathname);
  const path = onSite ? langPath(LANG, cityPath(state.city) || "/") : location.pathname;

  const p = new URLSearchParams();
  if (state.q) p.set("q", qRaw || state.q);
  if (state.city !== "all" && !(onSite && cityPath(state.city))) p.set("city", state.city);
  // Sorted, so the same set of categories always produces the same link
  // whatever order they were ticked in.
  if (state.cats.size) p.set("cat", [...state.cats].sort().join(","));
  if (state.free) p.set("free", "1");
  if (state.range === "date") p.set("date", state.date);
  else if (state.range !== "all") p.set("when", state.range);

  // URLSearchParams percent-encodes the comma between categories. A comma is
  // legal in a query value and ?cat=comedy,theatre is a good deal easier to
  // read in a message than ?cat=comedy%2Ctheatre, so put it back.
  const qs = p.toString().replace(/%2C/g, ",");
  try {
    history.replaceState(null, "", path + (qs ? "?" + qs : "") + location.hash);
  } catch (e) { /* file:// and sandboxed frames refuse this; filters still work */ }

  // The language switch goes to this same view in the other language.
  const sw = $("#lang-switch");
  if (sw && onSite) sw.href = langPath(OTHER_LANG, cityPath(state.city) || "/") + (qs ? "?" + qs : "");
}

/** What the "when" control currently says, in words. */
function whenLabel() {
  if (state.range === "date") return LONG_FMT.format(asDate(state.date));
  const b = $(`#when button[data-range="${state.range}"]`);
  return b && state.range !== "all" ? b.textContent.toLowerCase() : null;
}

/* The tally doubles as the collapsed bar's readout: with the controls hidden
   it is the only thing telling you which filters are still on. */
function writeTally(n) {
  const tally = $("#tally");
  tally.replaceChildren();
  if (!DATA.events.length) return;

  tally.append(document.createTextNode(T.of(NUM.format(n), NUM.format(DATA.events.length))));

  const active = [];
  if (state.city !== "all") active.push(state.city);
  const when = whenLabel();
  if (when) active.push(when);
  if (state.cats.size) active.push([...state.cats].map((c) => catPlural(c).toLowerCase()).join(", "));
  if (state.free) active.push(T.free);
  if (state.q) active.push(`"${qRaw || state.q}"`);
  if (!active.length) return;

  tally.append(document.createTextNode(" · "));
  tally.append(el("span", "active", active.join(" · ")));
}

/* "Volg deze selectie": mail alerts for the view on screen. meldingen.html (/meldingen, or
   /en/alerts in English) takes the same q/city/cat parameters as this page,
   so the link hands the filters over as they are.
   Only for a view with a search or a category - a city alone is the whole
   agenda, which the alerts service refuses as a watch. Shown once the service
   says it really sends mail (/api/alerts/info); until the mailbox exists it
   writes mail to a folder, and the link stays away. */
let alertsLive = false;
const BELL_SVG = '<svg class="bell" aria-hidden="true" focusable="false" viewBox="0 0 16 16"><path d="M8 1.5a4.5 4.5 0 0 0-4.5 4.5v2.6L2.2 11h11.6l-1.3-2.4V6A4.5 4.5 0 0 0 8 1.5Z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M6.3 12.8a1.8 1.8 0 0 0 3.4 0" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>';
fetch("/api/alerts/info").then((r) => r.ok && r.json()).then((info) => {
  alertsLive = Boolean(info && info.mail === "smtp");
  writeAlertLink();
}).catch(() => { /* no alerts service: no link */ });

function writeAlertLink() {
  let a = $("#alert-link");
  if (!alertsLive || !(state.q || state.cats.size)) {
    if (a) a.remove();
    return;
  }
  if (!a) {
    a = el("a", "alert-link", T.follow);
    a.id = "alert-link";
    // The bell is drawn, not the emoji: an emoji renders as a full-colour
    // yellow bell on most devices, and this site is greys plus one accent.
    a.insertAdjacentHTML("afterbegin", BELL_SVG);
    $("#tally").after(a);
  }
  const p = new URLSearchParams();
  if (state.q) p.set("q", qRaw || state.q);
  if (state.city !== "all") p.set("city", state.city);
  if (state.cats.size) p.set("cat", [...state.cats].sort().join(","));
  a.href = (LANG === "en" ? "/en/alerts?" : "/meldingen?") + p.toString().replace(/%2C/g, ",");
}

/* ---------- rendering ---------- */
function row(ev) {
  const a = el("a", "event");
  // The builder keeps only http(s) links (_web_url in uitradar/model.py);
  // checked again here because this is where a link becomes clickable.
  const url = /^https?:\/\//i.test(ev.url || "") ? ev.url : null;
  a.href = url || "#";
  if (url) {
    a.target = "_blank"; a.rel = "noopener noreferrer";
    // Which events people open, as an Umami "event-open" event: the tracker
    // reports clicks on anything carrying data-umami-event, with every
    // data-umami-event-* attribute as a property. A new tab, so nothing waits.
    a.dataset.umamiEvent = "event-open";
    a.dataset.umamiEventTitle = ev.title.slice(0, 100);
    a.dataset.umamiEventSource = ev.source_label || ev.source;
    a.dataset.umamiEventCategory = ev.categories[0] || "other";
    if (ev.city) a.dataset.umamiEventCity = ev.city;
  }

  // "hele dag" only for what runs all day (a market, an exhibition); a night
  // the source gave no clock for says so instead (time_unknown, set by the
  // builder in uitradar/pipeline.py).
  a.append(ev.time_unknown ? el("div", "time allday", T.timeUnknown)
    : ev.all_day || ev.start.length <= 10 ? el("div", "time allday", T.allDay)
    : el("div", "time", ev.start.slice(11, 16)));

  const body = el("div");
  body.append(el("h3", null, ev.title));

  // Where, and until when for a run.
  const where = [ev.venue, ev.city].filter(Boolean);
  if (ev.end && dayKey(ev.end) !== dayKey(ev.start)) {
    where.push(T.until + " " + UNTIL_FMT.format(asDate(ev.end)));
  }
  if (where.length) body.append(el("p", "meta", where.join(" · ")));
  if (ev.summary) body.append(el("p", "summary", ev.summary));

  // What it is and where it came from, in one quiet line: the title is the
  // only strong thing in a row.
  const sub = el("p", "sub");
  const bits = ev.categories.map(catSingular);
  if (ev.price) bits.push(ev.price);
  bits.push("via " + (ev.source_label || ev.source));
  sub.append(document.createTextNode(bits.join(" · ")));
  if (ev.provisional) {
    sub.append(document.createTextNode(" · "));
    sub.append(el("span", "flag", T.unconfirmed));
  }
  body.append(sub);
  a.append(body);

  if (ev.image) {
    const fig = el("figure");
    const img = el("img");
    img.alt = ""; img.loading = "lazy"; img.decoding = "async";
    // Fades in once it has actually arrived; until then, and if it never does,
    // there is nothing there rather than an empty grey box.
    img.addEventListener("load", () => img.classList.add("loaded"));
    img.addEventListener("error", () => fig.remove());
    img.src = ev.image;
    fig.append(img);
    a.append(fig);
  }
  return a;
}

/** Doorlopend: runs that opened before this day (an exhibition, a theatre run),
    folded into one line at the top, so the day opens with what actually
    happens on it. */
function runsBlock(evs) {
  const box = el("details", "runs");
  const sum = el("summary");
  sum.append(el("span", "time", T.running));

  const byCat = new Map();
  evs.forEach((ev) => { const c = ev.categories[0] || "other"; byCat.set(c, (byCat.get(c) || 0) + 1); });
  const parts = [...byCat.entries()].sort((a, b) => b[1] - a[1])
    .map(([c, n]) => `${n} ${(n === 1 ? catSingular(c) : catPlural(c)).toLowerCase()}`);

  const what = el("span", "what");
  what.append(el("b", null, `${NUM.format(evs.length)} ${T.events(evs.length)}`));
  what.append(el("span", null, ": " + parts.join(", ")));
  sum.append(what);
  sum.append(el("span", "more", T.show));
  box.addEventListener("toggle", () => {
    sum.querySelector(".more").textContent = box.open ? T.hide : T.show;
  });
  box.append(sum);
  evs.forEach((ev) => box.append(row(ev)));
  return box;
}

/** Every filter back to its default, in place: the controls, the state and,
    through render(), the URL. On a city page that includes the city, as the
    tally lists it among the filters. Used by the bar's "Filters wissen" and
    by the link under an empty result. */
function clearFilters() {
  qRaw = "";
  Object.assign(state, { q: "", city: "all", range: "all", date: null, free: false });
  state.cats.clear();
  $("#q").value = "";
  [...$("#cities").children].forEach((c, i) => c.classList.toggle("on", i === 0));
  [...$("#when").children].forEach((c) => c.classList.toggle("on", c.dataset.range === "all"));
  const date = $("#date");
  date.value = "";
  date.classList.remove("on");
  $("#date-clear").hidden = true;
  document.querySelectorAll("#cats input").forEach((i) => { i.checked = false; });
  $("#cats-summary").textContent = T.allCats;
  $("#catpick").classList.remove("on");
  const free = $("#free");
  if (free) { free.classList.remove("on"); free.setAttribute("aria-pressed", "false"); }
  render();
}

const anyFilter = () => Boolean(state.q || state.city !== "all" || state.cats.size ||
                                state.range !== "all" || state.free);

/* ---------- analytics ----------
   What the filters were set to, as one Umami "filter" event once the visitor
   has left them alone for 1.5s, so typing "jazz" is one event and not four.
   The tag in index.html excludes the query string from page views for the same
   reason. The first render is skipped: the page view already covers the state
   a page opened with. */
let trackedFilters = null;
let filterTimer = 0;

/* Campaign links. data-exclude-search strips the whole query from page views,
   utm_* included, and writeUrlState() drops anything it does not know on the
   first render anyway -- before the deferred tracker has sent a thing. So the
   utm_* parameters are read here, while app.js runs and the URL is still the
   one that was clicked, and put back on the FIRST page view only, through the
   tag's data-before-send hook (called with every payload; returns what to send).
   A later view or a reshared link carries no campaign. */
const LANDING_UTM = (() => {
  const p = new URLSearchParams();
  new URLSearchParams(location.search).forEach((v, k) => { if (/^utm_[a-z]+$/.test(k)) p.set(k, v.slice(0, 100)); });
  return p.toString();
})();
let utmSent = !LANDING_UTM;
window.uitjesBeforeSend = (type, payload) => {
  if (!utmSent && payload && !payload.name && payload.url) {
    utmSent = true;
    const u = new URL(payload.url, location.href);
    u.search = LANDING_UTM;
    payload.url = /^https?:/.test(payload.url) ? u.toString() : u.pathname + u.search + u.hash;
  }
  return payload;
};
function trackFilters() {
  const props = {
    city: state.city,
    when: state.range,
    cats: [...state.cats].sort().join(",") || "none",
    free: state.free ? "yes" : "no",
  };
  if (state.q) props.q = (qRaw || state.q).slice(0, 60);
  const key = JSON.stringify(props);
  if (trackedFilters === null) { trackedFilters = key; return; }
  clearTimeout(filterTimer);
  filterTimer = setTimeout(() => {
    if (key === trackedFilters) return;
    trackedFilters = key;
    if (window.umami) window.umami.track("filter", props);
  }, 1500);
}

function render() {
  // Every control ends up here, which makes this the one place the URL has to
  // be kept in step with the filters.
  writeUrlState();
  trackFilters();

  const hits = DATA.events.filter(matches);
  const results = $("#results");
  results.replaceChildren();

  writeTally(hits.length);
  writeAlertLink();
  $("#clear-filters").hidden = !anyFilter();

  if (!hits.length) {
    const p = el("p", "empty", T.nothing);
    const b = el("button", "link", T.clearAll);
    b.addEventListener("click", clearFilters);
    p.append(b);
    results.append(p);
    applyBarHeight();
    return;
  }

  const byDay = new Map();
  const windowStart = localKey(rangeWindow()[0]);
  for (const ev of hits) {
    // A run already under way (exhibition, festival week) sorts under the first
    // day of the window you're looking at, not the day it originally opened.
    const key = dayKey(ev.start) < windowStart ? windowStart : dayKey(ev.start);
    if (!byDay.has(key)) byDay.set(key, []);
    byDay.get(key).push(ev);
  }

  const frag = document.createDocumentFragment();
  for (const key of [...byDay.keys()].sort()) {
    const group = el("section", "daygroup");
    const { rel, num, wd, mo } = dayParts(key);
    const head = el("h2", "dayhead");
    if (rel) head.append(el("span", "rel", rel));
    head.append(el("span", "num", num), el("span", "wd", wd), el("span", "mo", mo));
    group.append(head);

    const list = el("div", "day-list");
    const evs = byDay.get(key);
    const running = evs.filter((ev) => dayKey(ev.start) < key);
    if (running.length) list.append(runsBlock(running));
    evs.filter((ev) => dayKey(ev.start) >= key).forEach((ev) => list.append(row(ev)));
    group.append(list);
    frag.append(group);
  }
  results.append(frag);
  applyBarHeight();
}

/* ---------- controls ---------- */
function buildControls() {
  const cityBox = $("#cities");
  ["all", ...(DATA.cities || [])].forEach((name) => {
    const b = el("button", name === state.city ? "on" : null, name === "all" ? T.both : name);
    b.addEventListener("click", () => {
      state.city = name;
      [...cityBox.children].forEach((c) => c.classList.toggle("on", c === b));
      render();
    });
    cityBox.append(b);
  });

  buildCategoryMenu();

  // Gratis: one toggle, only offered when this build has free events at all.
  const clear = $("#clear-filters");
  clear.textContent = T.clear;
  clear.addEventListener("click", clearFilters);

  const free = $("#free");
  if (free && DATA.counts.free) {
    free.hidden = false;
    const show = () => { free.classList.toggle("on", state.free); free.setAttribute("aria-pressed", String(state.free)); };
    show();
    free.addEventListener("click", () => { state.free = !state.free; show(); render(); });
  }

  const dateInput = $("#date");
  const dateClear = $("#date-clear");

  // The presets and the date picker are one control with two faces: choosing
  // either clears the other, so "when" always has exactly one answer.
  function selectPreset(b) {
    state.range = b.dataset.range;
    state.date = null;
    dateInput.value = "";
    dateInput.classList.remove("on");
    dateClear.hidden = true;
    [...$("#when").children].forEach((c) => c.classList.toggle("on", c === b));
    render();
  }

  $("#when").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-range]");
    if (b) selectPreset(b);
  });

  // Bound to the data, so the picker can't offer a day the dataset cannot answer.
  const days = DATA.events.map((e) => dayKey(e.start)).sort();
  dateInput.min = TODAY_KEY;
  dateInput.max = days.length ? days[days.length - 1] : TODAY_KEY;

  // A link can arrive with a preset or a single day already chosen. index.html
  // marks "Alles" as the default, so move the highlight to match. Set after
  // min/max above, or the picker rejects the value.
  if (state.range === "date") {
    dateInput.value = state.date;
    dateInput.classList.add("on");
    dateClear.hidden = false;
    [...$("#when").children].forEach((c) => c.classList.remove("on"));
  } else {
    [...$("#when").children].forEach((c) =>
      c.classList.toggle("on", c.dataset.range === state.range));
  }

  dateInput.addEventListener("change", () => {
    if (!dateInput.value) { selectPreset($('#when button[data-range="all"]')); return; }
    state.range = "date";
    state.date = dateInput.value;
    dateInput.classList.add("on");
    dateClear.hidden = false;
    [...$("#when").children].forEach((c) => c.classList.remove("on"));
    render();
  });

  dateClear.addEventListener("click", () =>
    selectPreset($('#when button[data-range="all"]')));

  $("#q").value = qRaw;

  let timer;
  $("#q").addEventListener("input", (e) => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      qRaw = e.target.value.trim();
      state.q = qRaw.toLowerCase();
      render();
    }, 120);
  });
}

/* Categories: a dropdown of checkboxes, biggest first, with counts. A native
   <details>, so it opens and closes without script; the script only closes it
   on a click elsewhere or Escape, and keeps the summary saying what is on. */
/* The category control as wide as the widest thing it can say, bold: every
   label it may show, measured once in place. Not while the controls are
   collapsed (nothing to measure); setCollapsed() calls it again on opening. */
function sizeCatSummary() {
  const pick = $("#catpick");
  const sum = $("#cats-summary");
  if (!pick || !sum.offsetParent || sum.dataset.sized) return;
  const text = sum.textContent;
  const wasOn = pick.classList.contains("on");
  pick.classList.add("on");
  let width = 0;
  [T.allCats, T.nCats(Object.keys(CATEGORY).length), ...Object.keys(CATEGORY).map(catPlural)]
    .forEach((label) => { sum.textContent = label; width = Math.max(width, sum.getBoundingClientRect().width); });
  pick.classList.toggle("on", wasOn);
  sum.textContent = text;
  sum.style.minWidth = Math.ceil(width) + "px";
  sum.dataset.sized = "1";
}

/* Reserve each option's bold width (see .seg button::after in style.css). */
function reserveLabelWidths() {
  document.querySelectorAll(".seg button").forEach((b) => { b.dataset.label = b.textContent.trim(); });
  const toggle = $("#filters-toggle");
  if (toggle) {
    toggle.dataset.label = [T.showFilters, T.hideFilters].sort((a, b) => b.length - a.length)[0];
  }
}

function buildCategoryMenu() {
  const pick = $("#catpick");
  const menu = $("#cats");
  const counts = DATA.counts.by_category || {};

  sizeCatSummary();

  const summarise = () => {
    const n = state.cats.size;
    $("#cats-summary").textContent = n === 0 ? T.allCats
      : n === 1 ? catPlural([...state.cats][0]) : T.nCats(n);
    pick.classList.toggle("on", n > 0);
  };

  Object.keys(CATEGORY)
    .filter((c) => counts[c])
    .sort((a, b) => counts[b] - counts[a])
    .forEach((c) => {
      const label = el("label");
      const box = el("input");
      box.type = "checkbox";
      box.value = c;
      box.checked = state.cats.has(c);
      box.addEventListener("change", () => {
        box.checked ? state.cats.add(c) : state.cats.delete(c);
        summarise();
        render();
      });
      label.append(box, document.createTextNode(catPlural(c)), el("span", "n", NUM.format(counts[c])));
      menu.append(label);
    });

  const foot = el("div", "foot");
  const clear = el("button", "link", T.showAll);
  clear.type = "button";
  clear.addEventListener("click", () => {
    state.cats.clear();
    menu.querySelectorAll("input").forEach((i) => { i.checked = false; });
    summarise();
    render();
  });
  foot.append(clear);
  menu.append(foot);

  // Left-aligned unless that runs off the right edge; measured on each open,
  // because where the control sits depends on how the bar wrapped.
  pick.addEventListener("toggle", () => {
    if (!pick.open) return;
    menu.classList.remove("flip");
    if (menu.getBoundingClientRect().right > document.documentElement.clientWidth - 8) menu.classList.add("flip");
  });
  document.addEventListener("click", (e) => { if (pick.open && !pick.contains(e.target)) pick.open = false; });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && pick.open) { pick.open = false; pick.querySelector("summary").focus(); }
  });
  summarise();
}

/** The builder's last run, when it was refused. The events (and `sources`) are
    still the last build that did publish, so on its own this table would go
    on saying "ok" about a source that has been failing for days. */
function refusedAttempt() {
  const la = DATA.last_attempt;
  return la && la.published === false ? la : null;
}

const STAMP = { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" };

function buildSourcePanel() {
  const refused = refusedAttempt();
  const toggle = $("#sources-toggle");

  if (!refused && DATA.sources.some((s) => s.carried_since)) toggle.classList.add("warn");
  if (refused) {
    toggle.classList.add("fail");
    const note = el("p", "attempt fail", T.refused(
      new Date(refused.at).toLocaleString(LOCALE, STAMP), refused.problems.join("; "),
      new Date(DATA.generated_at).toLocaleString(LOCALE, STAMP)));
    $("#sources-table").before(note);
  }

  const body = $("#sources-table tbody");
  DATA.sources.forEach((s) => {
    const tr = el("tr");
    tr.append(el("td", null, s.label));
    tr.append(el("td", null, s.adapter || "—"));
    tr.append(el("td", "num", NUM.format(s.count)));
    tr.append(el("td", "num", NUM.format(s.kept)));
    let cls = "ok", msg = "ok";
    // An unreachable source keeps its last published events for a few days
    // (carry_over() in updater/build.py): amber while it does, red
    // once they have been dropped.
    const stamp = (iso) => new Date(iso).toLocaleString(LOCALE, STAMP);
    if (s.error === "disabled") { cls = "off"; msg = T.off; }
    else if (s.carried_since) { cls = "warn"; msg = T.carried(stamp(s.carried_since), s.error); }
    else if (s.carry_expired) { cls = "fail"; msg = T.expired(stamp(s.carry_expired), s.error); }
    else if (s.error) { cls = "fail"; msg = s.error; }
    // Where the refused run saw it differently, that is the news.
    const now = refused && refused.sources[s.id];
    if (now && now.error && now.error !== "disabled") {
      cls = "fail"; msg = T.now(NUM.format(now.kept), now.error);
    }
    tr.append(el("td", cls, msg));
    body.append(tr);
  });

  toggle.addEventListener("click", () => {
    const panel = $("#sources-panel");
    panel.hidden = !panel.hidden;
    toggle.setAttribute("aria-expanded", String(!panel.hidden));
  });
}

/* ---------- collapsing the filter bar ----------
   On a phone the expanded bar is a good part of the viewport, which leaves
   little room for results. Collapsing leaves just the toggle and the tally.
   The choice is remembered (under the old name, so nobody's preference resets). */
const COLLAPSE_KEY = "uitradar.filters.collapsed";

function setCollapsed(on, remember) {
  $("#filters").classList.toggle("collapsed", on);
  $("#filters-toggle").setAttribute("aria-expanded", String(!on));
  $("#filters-toggle").textContent = on ? T.showFilters : T.hideFilters;
  if (!on) sizeCatSummary();  // could not be measured while the controls were hidden
  if (remember) {
    try { localStorage.setItem(COLLAPSE_KEY, on ? "1" : "0"); } catch (e) { /* private mode */ }
  }
  applyBarHeight();
}

function initCollapse() {
  let stored = null;
  try { stored = localStorage.getItem(COLLAPSE_KEY); } catch (e) { /* private mode */ }
  // No stored preference: start collapsed only where the bar actually crowds
  // the page out, i.e. narrow screens.
  const start = stored === null ? window.innerWidth < 720 : stored === "1";
  setCollapsed(start, false);
  $("#filters-toggle").addEventListener("click", () =>
    setCollapsed(!$("#filters").classList.contains("collapsed"), true));
}

/* ---------- sticky offset ----------
   The date column pins directly under the filter bar. The bar's height is not a
   constant — it wraps to more rows as the viewport narrows — so a hardcoded
   offset parks the dates behind it and they vanish while scrolling a long day.
   Measure it instead. */
function applyBarHeight() {
  const bar = $(".filters");
  if (!bar) return;
  const h = Math.round(bar.getBoundingClientRect().height);
  document.documentElement.style.setProperty("--bar", h + "px");
}

function trackBarHeight() {
  applyBarHeight();
  // The bar reflows for two different reasons: the viewport changing how many
  // rows the controls wrap onto, and the tally line inside it gaining or losing
  // text. ResizeObserver covers the first; render() calls applyBarHeight for
  // the second, because the tally is written after this runs.
  if (window.ResizeObserver) new ResizeObserver(applyBarHeight).observe($(".filters"));
  window.addEventListener("resize", applyBarHeight);
}

/* ---------- language switch ----------
   A plain link to the other language (index.html writes it, writeUrlState()
   keeps its query in step). Following it also stores the choice, and nginx
   (nginx/default.conf) sends a later visit to a page in the other
   language on to this one's twin: a Dutch link opened by someone who chose
   English lands in English. A cookie rather than localStorage because nginx
   has to read it; this host's only, unlike family.js's theme. */
$("#lang-switch")?.addEventListener("click", (e) => {
  try {
    document.cookie = `uitjes_lang=${e.currentTarget.dataset.lang}; Max-Age=31536000; Path=/; SameSite=Lax` +
      (location.protocol === "https:" ? "; Secure" : "");
  } catch (err) { /* cookies off: the link still works, it just is not remembered */ }
});

/* ---------- boot ----------
   From the root on the site: relative, /en/amsterdam would ask for
   /en/data/events.json. Relative anywhere else, so a local copy still works. */
fetch((PAGE_PATHS.has(location.pathname) ? "/" : "") + "data/events.json", { cache: "no-store" })
  .then((r) => r.json())
  .then((data) => {
    DATA = data;
    // Before buildControls, which paints the controls from state.
    readUrlState();
    buildControls();
    reserveLabelWidths();
    initCollapse();
    trackBarHeight();
    buildSourcePanel();
    render();
    const when = new Date(data.generated_at);
    const live = data.sources.filter((s) => s.kept > 0).length;
    $("#footnote").textContent =
      T.footnote(NUM.format(data.counts.total), live, data.horizon_days, when.toLocaleString(LOCALE, STAMP)) +
      (refusedAttempt() ? T.heldBack : "");
  })
  .catch((err) => {
    // Whatever the server put in the HTML stays: the next few weeks, without
    // the filters. Say so above it rather than replacing it.
    $("#results").prepend(el("p", "empty", T.loadFail(err)));
  });
