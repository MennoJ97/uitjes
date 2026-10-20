/* uitjes front-end — no dependencies, reads data/events.json */

/* Category keys are the data's (English, from the builder); what people see is
   Dutch. Plural for the filter menu, singular for the line under an event. */
const CATEGORY = {
  concert: ["Concerten", "concert"], classical: ["Klassiek", "klassiek"], club: ["Club", "club"],
  theatre: ["Theater", "theater"], dance: ["Dans", "dans"], comedy: ["Comedy", "comedy"],
  film: ["Film", "film"], festival: ["Festivals", "festival"], market: ["Markten", "markt"],
  exhibition: ["Exposities", "expositie"], family: ["Familie", "familie"], talk: ["Lezingen", "lezing"],
  sports: ["Sport", "sport"], other: ["Overig", "overig"],
};
const catPlural = (c) => (CATEGORY[c] || [c])[0];
const catSingular = (c) => (CATEGORY[c] || [c, c])[1];

const state = { q: "", city: "all", cats: new Set(), range: "all", date: null };

/* /amsterdam and /utrecht are this page with one city chosen, each with its own
   title and its own events in the HTML (nginx/default.conf). The city
   filter and the path are one thing: a city page opens with its city chosen,
   and choosing a city moves the address to its page. Keys are the paths, in
   step with that nginx regex and CITY_PAGES in uitradar/pages.py. */
const CITY_PAGES = { amsterdam: "Amsterdam", utrecht: "Utrecht" };
const PAGE_PATHS = new Set(["/", ...Object.keys(CITY_PAGES).map((c) => "/" + c)]);
const cityPath = (city) => (city !== "all" && CITY_PAGES[city.toLowerCase()] ? "/" + city.toLowerCase() : null);
let DATA = { events: [], sources: [], counts: {}, generated_at: null };

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};
const NUM = new Intl.NumberFormat("nl-NL");

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

const LONG_FMT = new Intl.DateTimeFormat("nl-NL", { weekday: "long", day: "numeric", month: "long" });
const UNTIL_FMT = new Intl.DateTimeFormat("nl-NL", { day: "numeric", month: "short" });
const WEEKDAY = new Intl.DateTimeFormat("nl-NL", { weekday: "long" });
const MONTH = new Intl.DateTimeFormat("nl-NL", { month: "long" });

/** The pieces of the date column: "vandaag" / 18 / vrijdag / september. */
function dayParts(key) {
  const d = asDate(key);
  const diff = Math.round((d - TODAY) / 86400000);
  const month = MONTH.format(d) + (d.getFullYear() === TODAY.getFullYear() ? "" : " " + d.getFullYear());
  return {
    rel: diff === 0 ? "vandaag" : diff === 1 ? "morgen" : null,
    num: String(d.getDate()),
    wd: WEEKDAY.format(d),
    mo: month,
  };
}

/* ---------- filtering ---------- */
function matches(ev) {
  if (state.city !== "all" && ev.city !== state.city) return false;
  if (state.cats.size && !ev.categories.some((c) => state.cats.has(c))) return false;
  if (!inRange(ev)) return false;
  if (state.q) {
    // Dutch labels in the haystack too, so "theater" or "markt" finds what the
    // data files under theatre and market. `search` is words the card does not
    // show (a long description, a merged duplicate's title): "ghibli" finds
    // Ziggo Dome's Joe Hisaishi night, whose title never says it.
    const hay = [ev.title, ev.venue, ev.city, ev.summary, ev.search, ...ev.categories,
                 ...ev.categories.map(catPlural), ...ev.categories.map(catSingular)]
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

  const pageCity = CITY_PAGES[location.pathname.slice(1)];
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
  const path = onSite ? cityPath(state.city) || "/" : location.pathname;

  const p = new URLSearchParams();
  if (state.q) p.set("q", qRaw || state.q);
  if (state.city !== "all" && !(onSite && cityPath(state.city))) p.set("city", state.city);
  // Sorted, so the same set of categories always produces the same link
  // whatever order they were ticked in.
  if (state.cats.size) p.set("cat", [...state.cats].sort().join(","));
  if (state.range === "date") p.set("date", state.date);
  else if (state.range !== "all") p.set("when", state.range);

  // URLSearchParams percent-encodes the comma between categories. A comma is
  // legal in a query value and ?cat=comedy,theatre is a good deal easier to
  // read in a message than ?cat=comedy%2Ctheatre, so put it back.
  const qs = p.toString().replace(/%2C/g, ",");
  try {
    history.replaceState(null, "", path + (qs ? "?" + qs : "") + location.hash);
  } catch (e) { /* file:// and sandboxed frames refuse this; filters still work */ }
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

  tally.append(document.createTextNode(`${NUM.format(n)} van ${NUM.format(DATA.events.length)}`));

  const active = [];
  if (state.city !== "all") active.push(state.city);
  const when = whenLabel();
  if (when) active.push(when);
  if (state.cats.size) active.push([...state.cats].map((c) => catPlural(c).toLowerCase()).join(", "));
  if (state.q) active.push(`"${qRaw || state.q}"`);
  if (!active.length) return;

  tally.append(document.createTextNode(" · "));
  tally.append(el("span", "active", active.join(" · ")));
}

/* ---------- rendering ---------- */
function row(ev) {
  const a = el("a", "event");
  a.href = ev.url || "#";
  if (ev.url) {
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

  a.append(ev.all_day || ev.start.length <= 10
    ? el("div", "time allday", "hele dag")
    : el("div", "time", ev.start.slice(11, 16)));

  const body = el("div");
  body.append(el("h3", null, ev.title));

  // Where, and until when for a run.
  const where = [ev.venue, ev.city].filter(Boolean);
  if (ev.end && dayKey(ev.end) !== dayKey(ev.start)) {
    where.push("t/m " + UNTIL_FMT.format(asDate(ev.end)));
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
    sub.append(el("span", "flag", "onbevestigd"));
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
  sum.append(el("span", "time", "doorlopend"));

  const byCat = new Map();
  evs.forEach((ev) => { const c = ev.categories[0] || "other"; byCat.set(c, (byCat.get(c) || 0) + 1); });
  const parts = [...byCat.entries()].sort((a, b) => b[1] - a[1])
    .map(([c, n]) => `${n} ${(n === 1 ? catSingular(c) : catPlural(c)).toLowerCase()}`);

  const what = el("span", "what");
  what.append(el("b", null, `${NUM.format(evs.length)} ${evs.length === 1 ? "uitje" : "uitjes"}`));
  what.append(el("span", null, ": " + parts.join(", ")));
  sum.append(what);
  sum.append(el("span", "more", "tonen"));
  box.addEventListener("toggle", () => {
    sum.querySelector(".more").textContent = box.open ? "verbergen" : "tonen";
  });
  box.append(sum);
  evs.forEach((ev) => box.append(row(ev)));
  return box;
}

function resetFilters() {
  // The defaults are the bare front page, so this is all of it. Not just
  // clearing the query: on /amsterdam that would keep the city.
  if (PAGE_PATHS.has(location.pathname)) location.href = "/";
  else location.search = "";
}

/* ---------- analytics ----------
   What the filters were set to, as one Umami "filter" event once the visitor
   has left them alone for 1.5s, so typing "jazz" is one event and not four.
   The tag in index.html excludes the query string from page views for the same
   reason. The first render is skipped: the page view already covers the state
   a page opened with. */
let trackedFilters = null;
let filterTimer = 0;
function trackFilters() {
  const props = {
    city: state.city,
    when: state.range,
    cats: [...state.cats].sort().join(",") || "none",
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

  if (!hits.length) {
    const p = el("p", "empty", "Niets gevonden met deze filters.");
    const b = el("button", "link", "Alle filters wissen");
    b.addEventListener("click", resetFilters);
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
    const b = el("button", name === state.city ? "on" : null, name === "all" ? "Beide" : name);
    b.addEventListener("click", () => {
      state.city = name;
      [...cityBox.children].forEach((c) => c.classList.toggle("on", c === b));
      render();
    });
    cityBox.append(b);
  });

  buildCategoryMenu();

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
function buildCategoryMenu() {
  const pick = $("#catpick");
  const menu = $("#cats");
  const counts = DATA.counts.by_category || {};

  const summarise = () => {
    const n = state.cats.size;
    $("#cats-summary").textContent = n === 0 ? "Alle categorieën"
      : n === 1 ? catPlural([...state.cats][0]) : `${n} categorieën`;
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
  const clear = el("button", "link", "Alles tonen");
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

  if (refused) {
    toggle.classList.add("fail");
    const note = el("p", "attempt fail",
      `Laatste poging (${new Date(refused.at).toLocaleString("nl-NL", STAMP)}) niet gepubliceerd: ` +
      refused.problems.join("; ") +
      `. Hieronder de laatste build die wel doorkwam, van ` +
      `${new Date(DATA.generated_at).toLocaleString("nl-NL", STAMP)}.`);
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
    if (s.error === "disabled") { cls = "off"; msg = "uit"; }
    else if (s.error) { cls = "fail"; msg = s.error; }
    // Where the refused run saw it differently, that is the news.
    const now = refused && refused.sources[s.id];
    if (now && now.error && now.error !== "disabled") {
      cls = "fail"; msg = `nu ${NUM.format(now.kept)}: ${now.error}`;
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
  $("#filters-toggle").textContent = on ? "Filters tonen" : "Filters verbergen";
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

/* ---------- boot ---------- */
fetch("data/events.json", { cache: "no-store" })
  .then((r) => r.json())
  .then((data) => {
    DATA = data;
    // Before buildControls, which paints the controls from state.
    readUrlState();
    buildControls();
    initCollapse();
    trackBarHeight();
    buildSourcePanel();
    render();
    const when = new Date(data.generated_at);
    const live = data.sources.filter((s) => s.kept > 0).length;
    $("#footnote").textContent =
      `${NUM.format(data.counts.total)} uitjes uit ${live} ${live === 1 ? "bron" : "bronnen"}, ` +
      `${data.horizon_days} dagen vooruit. Bijgewerkt ${when.toLocaleString("nl-NL", STAMP)}.` +
      (refusedAttempt() ? " Nieuwere builds zijn tegengehouden, zie Bronnen." : "");
  })
  .catch((err) => {
    // Whatever the server put in the HTML stays: the next few weeks, without
    // the filters. Say so above it rather than replacing it.
    $("#results").prepend(el("p", "empty",
      "De volledige agenda kon niet worden geladen, de filters werken daardoor niet. (" + err + ")"));
  });
