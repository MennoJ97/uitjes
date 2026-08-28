/* UITradar front-end — no dependencies, reads web/data/events.json */

const CATEGORY_LABEL = {
  concert: "Concerts", classical: "Classical", club: "Club", theatre: "Theatre",
  dance: "Dance", comedy: "Comedy", film: "Film", festival: "Festivals",
  market: "Markets", exhibition: "Exhibitions", family: "Family", talk: "Talks",
  sports: "Sports", other: "Other",
};

const state = { q: "", city: "all", cats: new Set(), range: "all", date: null };
let DATA = { events: [], sources: [], counts: {}, generated_at: null };

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};

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
  if (!to) return true;
  const start = asDate(ev.start);
  const end = ev.end ? asDate(ev.end) : start;
  return start <= to && end >= from;   // any overlap with the window counts
}

const DAY_FMT = new Intl.DateTimeFormat("en-GB",
  { weekday: "long", day: "numeric", month: "long" });

function dayLabel(key) {
  const d = asDate(key);
  const diff = Math.round((d - TODAY) / 86400000);
  const base = d.getFullYear() === TODAY.getFullYear()
    ? DAY_FMT.format(d)
    : DAY_FMT.format(d) + " " + d.getFullYear();
  if (diff === 0) return { text: "Today · " + base, today: true };
  if (diff === 1) return { text: "Tomorrow · " + base, today: false };
  return { text: base, today: false };
}

/* ---------- filtering ---------- */
function matches(ev) {
  if (state.city !== "all" && ev.city !== state.city) return false;
  if (state.cats.size && !ev.categories.some((c) => state.cats.has(c))) return false;
  if (!inRange(ev)) return false;
  if (state.q) {
    const hay = [ev.title, ev.venue, ev.city, ev.summary, ...ev.categories]
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
   out, so an untouched page keeps a clean URL. */
const URL_RANGES = new Set(["today", "weekend", "7", "30", "all"]);
// The search box's text as typed. state.q is the lowercased form matches() uses,
// but a link reading ?q=Jeff+Buckley is friendlier than ?q=jeff+buckley, and the
// search is case-insensitive either way.
let qRaw = "";

function readUrlState() {
  const p = new URLSearchParams(location.search);

  qRaw = (p.get("q") || "").trim();
  state.q = qRaw.toLowerCase();

  // Everything below is checked against what this build actually contains, so
  // a link that outlived a renamed city or a category that no longer has any
  // events degrades to the unfiltered page rather than to a blank one.
  const city = (p.get("city") || "").toLowerCase();
  const known = (DATA.cities || []).find((c) => c.toLowerCase() === city);
  if (known) state.city = known;

  (p.get("cat") || "").split(",").forEach((c) => {
    const key = c.trim().toLowerCase();
    if (CATEGORY_LABEL[key]) state.cats.add(key);
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
  const p = new URLSearchParams();
  if (state.q) p.set("q", qRaw || state.q);
  if (state.city !== "all") p.set("city", state.city);
  // Sorted, so the same set of pills always produces the same link whatever
  // order they were clicked in.
  if (state.cats.size) p.set("cat", [...state.cats].sort().join(","));
  if (state.range === "date") p.set("date", state.date);
  else if (state.range !== "all") p.set("when", state.range);

  // URLSearchParams percent-encodes the comma between categories. A comma is
  // legal in a query value and ?cat=comedy,theatre is a good deal easier to
  // read in a message than ?cat=comedy%2Ctheatre, so put it back.
  const qs = p.toString().replace(/%2C/g, ",");
  try {
    history.replaceState(null, "",
                         location.pathname + (qs ? "?" + qs : "") + location.hash);
  } catch (e) { /* file:// and sandboxed frames refuse this; filters still work */ }
}

/** What the "when" control currently says, in words. */
function whenLabel() {
  if (state.range === "date") return DAY_FMT.format(asDate(state.date));
  const b = $(`#when button[data-range="${state.range}"]`);
  return b && state.range !== "all" ? b.textContent : null;
}

/* The tally doubles as the collapsed bar's readout: with the rows hidden it is
   the only thing telling you which filters are still on. */
function writeTally(n) {
  const tally = $("#tally");
  tally.replaceChildren();
  if (!DATA.events.length) return;

  tally.append(document.createTextNode(
    `${n} event${n === 1 ? "" : "s"} of ${DATA.events.length}`));

  const active = [];
  if (state.city !== "all") active.push(state.city);
  const when = whenLabel();
  if (when) active.push(when);
  if (state.cats.size) active.push([...state.cats].map((c) => CATEGORY_LABEL[c]).join(", "));
  if (state.q) active.push(`"${state.q}"`);
  if (!active.length) return;

  tally.append(document.createTextNode(" · "));
  tally.append(el("span", "active", active.join(" · ")));
}

/* ---------- rendering ---------- */
function card(ev) {
  const a = el("a", "card");
  a.href = ev.url || "#";
  if (ev.url) { a.target = "_blank"; a.rel = "noopener noreferrer"; }

  const time = ev.all_day || ev.start.length <= 10
    ? el("div", "time allday", "all day")
    : el("div", "time", ev.start.slice(11, 16));
  a.append(time);

  const body = el("div");
  body.append(el("h3", null, ev.title));

  const meta = el("p", "meta");
  const bits = [ev.venue, ev.city].filter(Boolean);
  if (ev.end && dayKey(ev.end) !== dayKey(ev.start)) {
    bits.push("until " + DAY_FMT.format(asDate(ev.end)));
  }
  bits.forEach((bit, i) => {
    if (i) meta.append(el("span", "sep", "·"));
    meta.append(document.createTextNode(bit));
  });
  if (bits.length) body.append(meta);
  if (ev.summary) body.append(el("p", "summary", ev.summary));

  const chips = el("div", "chips");
  ev.categories.forEach((c) => chips.append(el("span", "badge", CATEGORY_LABEL[c] || c)));
  if (ev.price) chips.append(el("span", "badge price", ev.price));
  if (ev.provisional) chips.append(el("span", "badge prov", "unconfirmed"));
  chips.append(el("span", "badge src", ev.source_label || ev.source));
  body.append(chips);
  a.append(body);

  if (ev.image) {
    const fig = el("figure");
    const img = el("img");
    img.src = ev.image; img.alt = ""; img.loading = "lazy"; img.decoding = "async";
    img.addEventListener("error", () => fig.remove());
    fig.append(img);
    a.append(fig);
  }
  return a;
}

function render() {
  // Every control ends up here, which makes this the one place the URL has to
  // be kept in step with the filters.
  writeUrlState();

  const hits = DATA.events.filter(matches);
  const results = $("#results");
  results.replaceChildren();

  writeTally(hits.length);

  if (!hits.length) {
    results.append(el("p", "empty", "Nothing matches those filters."));
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
    const { text, today } = dayLabel(key);
    const head = el("h2", "dayhead");
    head.append(el("span", today ? "today" : null, text));
    group.append(head);
    byDay.get(key).forEach((ev) => group.append(card(ev)));
    frag.append(group);
  }
  results.append(frag);
  applyBarHeight();
}

/* ---------- controls ---------- */
function buildPills() {
  const cities = el("div");
  const cityBox = $("#cities");
  const all = ["all", ...(DATA.cities || [])];
  all.forEach((name) => {
    const b = el("button", name === state.city ? "on" : null,
                name === "all" ? "Both cities" : name);
    b.addEventListener("click", () => {
      state.city = name;
      [...cityBox.children].forEach((c) => c.classList.toggle("on", c === b));
      render();
    });
    cityBox.append(b);
  });
  cities.remove();

  const catBox = $("#cats");
  const counts = DATA.counts.by_category || {};
  Object.keys(CATEGORY_LABEL)
    .filter((c) => counts[c])
    .sort((a, b) => counts[b] - counts[a])
    .forEach((c) => {
      const b = el("button", state.cats.has(c) ? "on" : null);
      b.append(document.createTextNode(CATEGORY_LABEL[c]));
      b.append(el("span", "n", counts[c]));
      b.addEventListener("click", () => {
        state.cats.has(c) ? state.cats.delete(c) : state.cats.add(c);
        b.classList.toggle("on", state.cats.has(c));
        render();
      });
      catBox.append(b);
    });

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
  // marks "Everything" as the default, so move the highlight to match. Set
  // after min/max above, or the picker rejects the value.
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

function buildSourcePanel() {
  const body = $("#sources-table tbody");
  DATA.sources.forEach((s) => {
    const tr = el("tr");
    tr.append(el("td", null, s.label));
    tr.append(el("td", null, s.adapter || "—"));
    tr.append(el("td", "num", s.count));
    tr.append(el("td", "num", s.kept));
    let cls = "ok", msg = "ok";
    if (s.error === "disabled") { cls = "off"; msg = "disabled"; }
    else if (s.error) { cls = "fail"; msg = s.error; }
    tr.append(el("td", cls, msg));
    body.append(tr);
  });

  const toggle = $("#sources-toggle");
  toggle.addEventListener("click", () => {
    const panel = $("#sources-panel");
    panel.hidden = !panel.hidden;
    toggle.setAttribute("aria-expanded", String(!panel.hidden));
  });
}

/* ---------- collapsing the filter bar ----------
   On a phone the expanded bar is over half the viewport, which leaves almost no
   room for results. Collapsing leaves just the toggle and the tally, so the
   sticky strip costs ~40px instead of ~450px. The choice is remembered. */
const COLLAPSE_KEY = "uitradar.filters.collapsed";

function setCollapsed(on, remember) {
  $("#filters").classList.toggle("collapsed", on);
  $("#filters-toggle").setAttribute("aria-expanded", String(!on));
  $("#filters-toggle").querySelector(".chev").textContent = on ? "\u25B8" : "\u25BE";
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
   The day headings pin directly under the filter bar. The bar's height is not a
   constant — it wraps to more rows as the viewport narrows, and gained a row
   when the date picker was added — so a hardcoded offset parks the headings
   behind it and they vanish while scrolling a long day. Measure it instead. */
function applyBarHeight() {
  const bar = $(".filters");
  if (!bar) return;
  const h = Math.round(bar.getBoundingClientRect().height);
  document.documentElement.style.setProperty("--bar", h + "px");
}

function trackBarHeight() {
  applyBarHeight();
  // The bar reflows for two different reasons: the viewport changing how many
  // rows the pills wrap onto, and the tally line inside it gaining or losing
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
    // Before buildPills, which paints the pills from state.
    readUrlState();
    buildPills();
    initCollapse();
    trackBarHeight();
    buildSourcePanel();
    render();
    const when = new Date(data.generated_at);
    const live = data.sources.filter((s) => s.kept > 0).length;
    $("#footnote").textContent =
      `${data.counts.total} events from ${live} live source${live === 1 ? "" : "s"}, ` +
      `${data.horizon_days}-day horizon. Built ${when.toLocaleString("en-GB")}.`;
  })
  .catch((err) => {
    $("#results").append(el("p", "empty",
      "Could not load data/events.json — run `python3 build.py` first. " + err));
  });
