function limitedEvidence(row) {
  return !row.qualification || row.qualification.status === "unavailable"
    || row.qualification.reasons?.includes("wide_sampling_range") || false;
}

function evidenceText(overall, composition, operator, isPeriod) {
  const q = overall.qualification;
  const parts = [];
  if (!q) parts.push("Historical · sample checks unavailable.");
  else {
    parts.push(q.status === "unavailable" ? "Punctuality unavailable."
      : q.status === "indicative" ? "Indicative · observed readings."
      : "Observed readings.");
    if (q.journeys != null) parts.push(`${q.journeys.toLocaleString()} observed journeys across ${q.service_days} service day(s).`);
    if (q.reasons?.includes("coverage_unverified")) parts.push("Coverage unverified.");
    if (q.reasons?.includes("inconsistent_journey_order")) parts.push("Inconsistent journey order.");
    if (q.status !== "unavailable" && q.range_pct) parts.push(`Sampling / journey-order range: ${q.range_pct[0]}–${q.range_pct[1]}%.`);
  }
  if (operator === "ALL" && composition) {
    const shares = composition.operators.filter(x => x.readings > 0)
      .map(x => `${x.name}: ${x.share_pct}%`).join("; ");
    parts.push(shares ? `Share of ${isPeriod ? "this period's" : "this day's"} readings: ${shares}.` : "No eligible operator readings.");
  }

  return parts.join(" ");
}
const DEFAULT_TARGET_PCT = 95;
const ROUTES_SHOWN = 12;
const MODELS_SHOWN = 6;

const state = {
  data: null,
  view: "period",
  day: null,
  operator: null,
  rows: [],
  target: DEFAULT_TARGET_PCT,
  targetFinancialYear: "",
  targetSource: "",
  targetSourceUrl: "",
  routeSort: "worst",
  routesShown: ROUTES_SHOWN,
  selectedRoute: null,
  hourKind: "weekday",
  mapMode: "area",
  fleetAll: false,
  hourly: {},
  segments: {},
  places: null,
};

const $ = (id) => document.getElementById(id);

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function finiteNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function safeHTTPURL(value) {
  try {
    const url = new URL(String(value || ""));
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch (_error) {
    return "";
  }
}

function targetYearLabel() {
  return state.targetFinancialYear
    ? state.targetFinancialYear.replace("-", "–")
    : "current year";
}

// After WECA's published schedule ends, no target is current: the last one
// is named for reference and still sets the colours (planning #138).
function targetPending() {
  return state.targetStatus === "not_yet_published";
}

function lastTargetYearLabel() {
  return (state.lastTargetFinancialYear || "the previous year").replace("-", "–");
}

function targetReferenceHTML() {
  const source = escapeHTML(state.targetSource || "published WECA target schedule");
  const url = safeHTTPURL(state.targetSourceUrl);
  const label = targetPending()
    ? `${finiteNumber(state.target)}% (${escapeHTML(lastTargetYearLabel())}, last published)`
    : `${finiteNumber(state.target)}% target · ${escapeHTML(targetYearLabel())}`;
  return url ? `<a href="${escapeHTML(url)}" title="${source}" target="_blank" rel="noopener noreferrer">${label}</a>` : label;
}

function renderTargetContext() {
  $("h-tgt-label").innerHTML = targetReferenceHTML();
  const key = targetPending()
    ? `${finiteNumber(state.target)}% · ${lastTargetYearLabel()} last published`
    : `${finiteNumber(state.target)}% target`;
  ["geo-target-note", "route-target-note"].forEach((id) => {
    $(id).textContent = key;
    $(id).title = "Green means at or above " + key;
  });
}

const format = {
  delaySeconds(seconds) {
    if (seconds === null || seconds === undefined) return "–";
    seconds = finiteNumber(seconds);
    const sign = seconds > 0 ? "+" : seconds < 0 ? "−" : "";
    const absolute = Math.abs(seconds);
    const minutes = Math.floor(absolute / 60);
    const remainder = absolute % 60;
    return minutes
      ? `${sign}${minutes}m ${String(remainder).padStart(2, "0")}s`
      : `${sign}${remainder}s`;
  },
  serviceDate(yyyymmdd) {
    if (!yyyymmdd || yyyymmdd.length !== 8) return yyyymmdd || "–";
    return new Date(`${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}T00:00:00`)
      .toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  },
  shortDate(yyyymmdd) {
    if (!yyyymmdd || yyyymmdd.length !== 8) return yyyymmdd || "–";
    return new Date(`${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}T00:00:00`)
      .toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  },
};

// Green at or above the target, amber within 25 points of it, red below that.
function verdict(percent) {
  if (percent === null || percent === undefined) return "var(--fg-3)";
  if (percent >= state.target) return "var(--good)";
  if (percent >= state.target - 25) return "var(--warn)";
  return "var(--bad)";
}

function legendHTML(extra = "") {
  const target = finiteNumber(state.target);
  return `<span><i style="background:var(--good)"></i>${target}%+</span>`
    + `<span><i style="background:var(--warn)"></i>${target - 25}–${target - 1}%</span>`
    + `<span><i style="background:var(--bad)"></i>under ${target - 25}%</span>${extra}`;
}

function hasReadings(row) {
  return row.on_time_pct !== null && row.on_time_pct !== undefined;
}

function inTen(pct) {
  return `${Math.round(finiteNumber(pct) / 10)} in 10`;
}

// The route's page on the live map site, which mirrors route_pages.py:
// /routes/<operator>/<route>, e.g. /routes/first/4-bath. Only one operator's
// own labels have a page (the network view tags labels with the operator).
const ROUTE_PAGE_OPERATORS = { FBRI: "first" };
function pageSlug(value) {
  let text = String(value || "").toLowerCase().replace(/'/g, "");
  if (text.startsWith("the ")) text = text.slice(4);
  return text.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}
function routePageUrl(operator, route) {
  if (!operator || operator === "ALL" || !route) return null;
  const operatorSlug = ROUTE_PAGE_OPERATORS[operator] || pageSlug(operatorName(operator));
  return operatorSlug ? `https://bristolbuses.live/routes/${operatorSlug}/${pageSlug(route)}` : null;
}

function operatorName(code) {
  const meta = (state.data.operators || []).find((o) => o.code === code);
  return meta ? meta.name : code;
}

function dayTypeLabel(dayType) {
  return dayType === "weekday" ? "weekdays" : "weekends";
}

// Network route labels name other operators ("9 Stagecoach"): the number goes
// in the badge, the operator beside it.
function splitRoute(label) {
  const text = String(label);
  const space = text.indexOf(" ");
  return space < 0 ? [text, ""] : [text.slice(0, space), text.slice(space + 1)];
}

// --- Lazy per-operator files ------------------------------------------------
function loadHourly(operator) {
  const file = state.data.hourly?.files?.[operator];
  if (!file || !/^hourly\/[A-Za-z0-9_-]+\.json$/.test(file)) return Promise.resolve(null);
  state.hourly[operator] ||= fetch(file, { cache: "no-store" })
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null);
  return state.hourly[operator];
}

function loadSegments(operator) {
  const file = state.data.segments?.files?.[operator];
  if (!file || !/^segments\/[A-Za-z0-9_-]+\.json$/.test(file)) return Promise.resolve(null);
  state.segments[operator] ||= fetch(file, { cache: "no-store" })
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null);
  return state.segments[operator];
}

function loadPlaces() {
  state.places ||= fetch("places.json")
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null);
  return state.places;
}

// --- Time of day -------------------------------------------------------------
// The service day starts at 04:00, so late-night hours sit after 23:00.
const HOUR_ORDER = [...Array(20).keys()].map((h) => h + 4).concat([0, 1, 2, 3]);

function hourLabel(hour) {
  return String(hour).padStart(2, "0") + ":00";
}

// Cells for the hours this route or network runs, from first to last.
function hourCells(byKind, kind, minReadings) {
  const present = HOUR_ORDER.filter((h) =>
    (byKind.weekday || {})[h] || (byKind.weekend || {})[h]);
  if (!present.length) return [];
  const first = HOUR_ORDER.indexOf(present[0]);
  const last = HOUR_ORDER.indexOf(present[present.length - 1]);
  return HOUR_ORDER.slice(first, last + 1).map((hour) => {
    const [on, early, late] = (byKind[kind] || {})[hour] || [0, 0, 0];
    const total = finiteNumber(on) + finiteNumber(early) + finiteNumber(late);
    return { hour, on: finiteNumber(on), early: finiteNumber(early), late: finiteNumber(late),
      total, pct: total ? (100 * on) / total : null, low: total < minReadings };
  });
}

function bestAndWorstHour(cells) {
  const solid = cells.filter((c) => c.total && !c.low);
  if (solid.length < 2) return null;
  return {
    worst: solid.reduce((a, b) => (b.pct < a.pct ? b : a)),
    best: solid.reduce((a, b) => (b.pct > a.pct ? b : a)),
  };
}

function hourSummary(cells) {
  const hours = bestAndWorstHour(cells);
  if (!hours) return "Too few hourly readings";
  const { worst, best } = hours;
  if (Math.round(best.pct / 10) === Math.round(worst.pct / 10))
    return `Similar throughout · ${inTen(best.pct)} on time`;
  return `Low ${hourLabel(worst.hour)} · ${inTen(worst.pct)} / High ${hourLabel(best.hour)} · ${inTen(best.pct)}`;
}

function hourKindControls() {
  return `<div class="seg" role="group" aria-label="Day type">${["weekday", "weekend"].map((kind) =>
    `<button type="button" class="hctl${state.hourKind === kind ? " on" : ""}" data-hour-kind="${kind}"
      aria-pressed="${state.hourKind === kind ? "true" : "false"}">${kind === "weekday" ? "Weekdays" : "Weekends"}</button>`).join("")}</div>`;
}

function hourStripHTML(byKind, hourly, title, isRoute = false) {
  const kind = state.hourKind;
  const cells = hourCells(byKind, kind, hourly.min_readings);
  if (!cells.length) return '<p class="faint small">No hourly readings</p>';
  const days = hourly.window.days[kind];
  const strip = cells.map((c) => {
    const colour = c.total ? verdict(c.pct) : "var(--fg-3)";
    const text = c.total
      ? `${hourLabel(c.hour)}: ${inTen(c.pct)} on time, ${c.late.toLocaleString()} late, ${c.early.toLocaleString()} early, from ${c.total.toLocaleString()} readings${c.low ? " (too few to rely on)" : ""}`
      : `${hourLabel(c.hour)}: no readings`;
    return `<li class="hour-cell${c.low ? " low" : ""}" title="${escapeHTML(text)}" tabindex="0"
      data-detail="${escapeHTML(text)}" style="background:${c.total ? colour : "transparent"}">
      <span class="sr-only">${escapeHTML(text)}</span>
      <span class="tick" aria-hidden="true">${c.hour % 3 === 0 ? String(c.hour).padStart(2, "0") : ""}</span></li>`;
  }).join("");
  // A route's summary already says this for weekdays; repeat it only at weekends.
  const summary = !isRoute || kind === "weekend"
    ? `<p class="summary">${escapeHTML(hourSummary(cells))}</p>` : "";
  return `${summary}
    <ol class="strip" aria-label="${escapeHTML(title)}">${strip}</ol>
    <p class="strip-detail" aria-live="polite">Select an hour</p>
    <div class="legend">${legendHTML('<span><i style="background:var(--fg-3);opacity:.35"></i>too few readings</span>')}</div>
    <p class="faint small">${days} ${kind === "weekday" ? "weekday" : "weekend"} day${days === 1 ? "" : "s"} ·
      ${escapeHTML(format.shortDate(hourly.window.from))} to ${escapeHTML(format.shortDate(hourly.window.to))}</p>`;
}

function wireHourControls(container, redraw) {
  container.querySelectorAll(".hour-cell").forEach((cell) => {
    const show = () => {
      const detail = cell.closest("ol").nextElementSibling;
      container.querySelectorAll(".hour-cell.picked").forEach((c) => c.classList.remove("picked"));
      cell.classList.add("picked");
      detail.textContent = cell.dataset.detail;
    };
    cell.addEventListener("click", show);
    cell.addEventListener("focus", show);
  });
  container.querySelectorAll(".hctl").forEach((button) => {
    button.addEventListener("click", () => {
      state.hourKind = button.dataset.hourKind;
      redraw();
    });
  });
}

// --- Journeys observed -----------------------------------------------------
const JOURNEY_BAND_LABELS = {
  am_peak: "7–10am", interpeak: "10am–4pm", pm_peak: "4–7pm",
  evening: "evenings and early mornings",
};

function journeyCounts(entry, dutyChecked) {
  if (!entry) return null;
  const [timetabled, seen, likely, elsewhere] = entry.total.map((v) => finiteNumber(v));
  if (!timetabled) return null;
  const counted = dutyChecked ? likely : 0;
  // #146: an estimate, kept apart from "very likely didn't run".
  const other = dutyChecked ? (elsewhere || 0) : 0;
  return { timetabled, seen, likely: counted, elsewhere: other,
    unseen: Math.max(0, timetabled - seen - counted - other) };
}

// Three honestly separate numbers, never added together into "cancelled".
function journeysHTML(entry, dutyChecked) {
  const counts = journeyCounts(entry, dutyChecked);
  if (!counts) return "";
  const { timetabled, seen, likely, elsewhere, unseen } = counts;
  const overallMissing = (timetabled - seen) / timetabled;
  const worst = Object.entries(entry.bands || {})
    .map(([band, v]) => ({ band, timetabled: finiteNumber(v[0]), seen: finiteNumber(v[1]) }))
    .filter((b) => b.timetabled >= 50)
    .map((b) => ({ ...b, missing: (b.timetabled - b.seen) / b.timetabled }))
    .sort((a, b) => b.missing - a.missing)[0];
  const worstLine = worst && worst.missing >= 0.03 && worst.missing >= 1.5 * overallMissing
    ? `<p class="small">Most unseen: <strong>${escapeHTML(JOURNEY_BAND_LABELS[worst.band] || worst.band)}</strong> · ${Math.round(100 * (worst.timetabled - worst.seen) / worst.timetabled)} in 100</p>`
    : "";
  return `
    <h3>Journeys observed</h3>
    <div class="turnup">
      <div><b>${Math.round((100 * seen) / timetabled)}%</b><span>seen / ${timetabled.toLocaleString()} scheduled</span></div>
      ${dutyChecked ? `<div><b>${likely.toLocaleString()}</b><span><strong>Very likely didn't run</strong></span></div>` : ""}
      ${dutyChecked ? `<div><b>${elsewhere.toLocaleString()}</b><span><strong>Bus was on another journey</strong> (estimate)</span></div>` : ""}
      <div><b>${unseen.toLocaleString()}</b><span><strong>Not seen</strong></span></div>
    </div>
    <details class="small"><summary>What these mean</summary>
      <p>Very likely didn't run: the same bus was tracked on the journeys either side, but not this one.</p>
      ${dutyChecked ? "<p>Bus was on another journey: not seen, and the bus due to run it was tracked on a different journey at the time. An estimate: another, untracked bus may have run it.</p>" : ""}
      <p>Not seen: cancelled or not tracked; we cannot tell which. None of these is proof of cancellation.</p>
    </details>
    ${worstLine}
`;
}

// --- Where the timetable doesn't fit -----------------------------------------------
const FIT_BANDS = {
  early: "before 7am", am_peak: "7–10am", interpeak: "10am–4pm",
  pm_peak: "4–7pm", evening: "after 7pm",
};

function gap(item) {
  return finiteNumber(item.typical_min) - finiteNumber(item.timetable_min);
}

function minutesBars(item, scale) {
  const timetable = finiteNumber(item.timetable_min);
  const typical = finiteNumber(item.typical_min);
  return `
    <div class="mins"><span>Timetable</span><span class="track"><span class="tt" style="width:${(100 * timetable) / scale}%"></span></span><span>${Math.round(timetable)} min</span></div>
    <div class="mins"><span>Observed</span><span class="track"><span class="ty${item.kind === "slack" ? " slack" : ""}" style="width:${(100 * typical) / scale}%"></span></span><span>${Math.round(typical)} min</span></div>`;
}

// One entry per stretch and kind: the time it is clearest.
function strongestStretches(items, kinds) {
  const best = new Map();
  for (const item of items || []) {
    if (!kinds.includes(item.kind)) continue;
    const key = [item.kind, item.from, item.to].join("|");
    if (!best.has(key) || Math.abs(gap(item)) > Math.abs(gap(best.get(key)))) best.set(key, item);
  }
  return [...best.values()].sort((a, b) => Math.abs(gap(b)) - Math.abs(gap(a)));
}

/** A stop name with its town after it, when the town isn't already in the name. */
function stopLabel(name, place) {
  return place ? `${name} (${place})` : String(name ?? "");
}

function stopHTML(name, place) {
  return place
    ? `${escapeHTML(name)} <span class="place">(${escapeHTML(place)})</span>`
    : escapeHTML(name);
}

/** "+6 min" for a stretch that takes longer than timetabled, "3 min spare" for one with slack. */
function minutesPill(item) {
  const minutes = Math.round(Math.abs(gap(item)));
  return item.kind === "slack"
    ? `<span class="pill-gap spare">${minutes} min spare</span>`
    : `<span class="pill-gap">+${minutes} min</span>`;
}

function stretchHTML(item, scale, withRoute) {
  const when = `${dayTypeLabel(item.day_type)} ${FIT_BANDS[item.band] || item.band}`;
  const note = item.kind === "tight"
    ? `${when} · ${finiteNumber(item.share_pct)}% / ${finiteNumber(item.journeys).toLocaleString()} journeys · 2+ min lost`
    : `${when} · early · possible wait`;
  const badge = withRoute ? `<span class="badge">${escapeHTML(splitRoute(item.route)[0])}</span> ` : "";
  return `<li>${minutesPill(item)} ${badge}<strong>${stopHTML(item.from, item.from_place)} → ${stopHTML(item.to, item.to_place)}</strong>
    <span class="small"> ${escapeHTML(note)}</span>${minutesBars(item, scale)}</li>`;
}

// --- Likely reasons ------------------------------------------------------------------
// Only from patterns with evidence behind them, and never stated as fact.
/**
 * Time in the wrong place: spare time on the stretch into a stop and too
 * little on the stretch out of it, same direction and time of day. Buses
 * arrive early, wait, then run late. Returns the clearest pair, or null.
 */
function timeInTheWrongPlace(items) {
  let best = null;
  for (const tight of items.filter((i) => i.kind === "tight")) {
    for (const slack of items.filter((i) => i.kind === "slack")) {
      if (slack.to_stop !== tight.from_stop || slack.direction !== tight.direction
          || slack.band !== tight.band || slack.day_type !== tight.day_type) continue;
      // At least 2 minutes to move on each side, or it isn't worth saying.
      const size = Math.min(-gap(slack), gap(tight));
      if (size >= 2 && (!best || size > best.size)) best = { slack, tight, size };
    }
  }
  return best;
}

/**
 * The timetable gives a stretch less time from 10am than in the morning peak,
 * but buses take about as long: the off-peak timing starts too early.
 */
function tenAmCut(items) {
  let best = null;
  for (const midday of items.filter((i) => i.kind === "tight" && i.band === "interpeak")) {
    const morning = items.find((i) => i.band === "am_peak" && i.from_stop === midday.from_stop
      && i.to_stop === midday.to_stop && i.direction === midday.direction && i.day_type === midday.day_type);
    if (!morning) continue;
    const cut = finiteNumber(morning.timetable_min) - finiteNumber(midday.timetable_min);
    const sameSpeed = Math.abs(finiteNumber(morning.typical_min) - finiteNumber(midday.typical_min)) <= 2;
    if (cut >= 2 && gap(midday) >= 3 && sameSpeed && (!best || cut > best.cut)) best = { morning, midday, cut };
  }
  return best;
}

// Trips start later as the day goes on: no slack at the end of the route to recover.
function startsLaterAndLater(row) {
  const s = row?.start_lateness;
  if (!s || !finiteNumber(s.bus_days)) return null;
  const rise = finiteNumber(s.later_min) - finiteNumber(s.morning_min);
  const share = finiteNumber(s.later_days) / finiteNumber(s.bus_days);
  return rise >= 3 && share >= 0.4 ? { ...s, share } : null;
}

function likelyReasons(route, hourly, segments, row) {
  const out = [];
  const items = segments?.routes?.[route] || [];
  const covered = new Set();  // stops a reason already explains

  const wrongPlace = timeInTheWrongPlace(items);
  if (wrongPlace) {
    const { slack, tight } = wrongPlace;
    const stop = stopLabel(tight.from, tight.from_place);
    out.push({ title: "Time in the wrong place",
      text: `${stop}: ${Math.round(slack.timetable_min)} min scheduled / ${Math.round(slack.typical_min)} observed → ${stopLabel(tight.to, tight.to_place)}: ${Math.round(tight.timetable_min)} / ${Math.round(tight.typical_min)} min · ${dayTypeLabel(tight.day_type)} ${FIT_BANDS[tight.band] || tight.band}` });
    covered.add(tight.from_stop);
  }

  const later = startsLaterAndLater(row);
  if (later) {
    out.push({ title: "Trips start later",
      text: `7–10am: +${Math.round(later.morning_min)} min / 1pm onwards: +${Math.round(later.later_min)} min · worsening on ${Math.round(10 * later.share)} in 10 days` });
  }

  const cut = tenAmCut(items);
  if (cut && !covered.has(cut.midday.from_stop)) {
    const { morning, midday } = cut;
    out.push({ title: "Less time allowed after 10am",
      text: `${stopLabel(midday.from, midday.from_place)} → ${stopLabel(midday.to, midday.to_place)} · scheduled ${Math.round(morning.timetable_min)} min before 10am / ${Math.round(midday.timetable_min)} after · observed ${Math.round(midday.typical_min)} min · ${dayTypeLabel(midday.day_type)}` });
    covered.add(midday.from_stop);
  }

  const tight = strongestStretches(items, ["tight"]).filter((t) => !covered.has(t.from_stop));
  if (tight.length) {
    const t = tight[0];
    out.push({ title: "Too little time",
      text: `${stopLabel(t.from, t.from_place)} → ${stopLabel(t.to, t.to_place)} · ${Math.round(t.timetable_min)} min scheduled / ${Math.round(t.typical_min)} observed · ${dayTypeLabel(t.day_type)} ${FIT_BANDS[t.band] || t.band}` });
  }
  const byKind = hourly?.routes?.[route];
  const hours = byKind && bestAndWorstHour(hourCells(byKind, "weekday", hourly.min_readings));
  if (hours && hours.best.pct - hours.worst.pct >= 20) {
    out.push({ title: `Weekday low: ${hourLabel(hours.worst.hour)}`,
      text: `${inTen(hours.worst.pct)} on time / ${inTen(hours.best.pct)} at ${hourLabel(hours.best.hour)}` });
  }
  const counts = journeyCounts(hourly?.journeys?.routes?.[route], hourly?.journeys?.duty_checked);
  if (counts && counts.timetabled >= 100) {
    const missing = (counts.timetabled - counts.seen) / counts.timetabled;
    if (missing >= 0.1 || counts.likely >= 5) {
      out.push({ title: "Unseen journeys",
        text: `${Math.round((100 * counts.seen) / counts.timetabled)} in 100 seen${counts.likely ? ` · ${counts.likely} very likely didn't run` : ""}${counts.elsewhere ? ` · ${counts.elsewhere} bus on another journey (estimate)` : ""}` });
    }
  }
  const tightPlaces = new Set(tight.map((t) => `${t.from}|${t.to}`));
  const wait = items.find((i) => i.kind === "waiting" && !tightPlaces.has(`${i.from}|${i.to}`)
    && !covered.has(i.from_stop));
  if (wait) {
    out.push({ title: "Possible waiting",
      text: `${stopLabel(wait.from, wait.from_place)} · early readings` });
  }
  return out.slice(0, 3);
}

function routeSummary(row, hourly) {
  const head = `${inTen(row.on_time_pct)} on time`;
  const byKind = hourly?.routes?.[row.route];
  const hours = byKind && bestAndWorstHour(hourCells(byKind, "weekday", hourly.min_readings));
  if (!hours) return `${head} · too few hourly readings`;
  if (Math.round(hours.best.pct / 10) === Math.round(hours.worst.pct / 10))
    return `${head} · similar throughout`;
  return `Weekdays: low ${hourLabel(hours.worst.hour)} (${inTen(hours.worst.pct)}) / high ${hourLabel(hours.best.hour)} (${inTen(hours.best.pct)})`;
}

// --- Headline --------------------------------------------------------------------------
// Early arrival at a trip's last timing point counts as on time, as the
// official standard allows (planning #131). The stricter figure, counting it
// as early, sits beside the headline.
function strictFigure(overall) {
  const finalEarly = finiteNumber(overall.final_stop_early_on_time);
  const readings = finiteNumber(overall.readings_in_gate);
  if (!hasReadings(overall) || finalEarly <= 0 || readings <= 0) return null;
  return {
    pct: (100 * (finiteNumber(overall.on_time) - finalEarly)) / readings,
    finalEarly,
  };
}

function renderStrictFigure(overall) {
  const element = $("h-strict");
  const label = $("h-strict-label");
  const strict = strictFigure(overall);
  element.hidden = label.hidden = !strict;
  if (!strict) return;
  element.style.left = Math.max(0, Math.min(100, strict.pct)) + "%";
  label.textContent = `${strict.pct.toFixed(1)}% if early arrivals at the last stop count as early`;
  element.title = `Counting ${Math.round(strict.finalEarly).toLocaleString("en-GB")} early last-stop readings as early instead of on time`;
}

function positionTargetLabel() {
  const width = $("h-bar").clientWidth;
  const label = $("h-tgt-label");
  const marker = width * state.target / 100;
  const left = Math.max(0, Math.min(marker - label.offsetWidth / 2, width - label.offsetWidth));
  label.style.left = `${left - marker}px`;
}

function renderHeadline(opData) {
  const data = state.data;
  const day = state.day;
  const overall = opData.overall || {};
  const pct = overall.on_time_pct;
  const colour = verdict(pct);

  $("period-line").textContent = `${operatorName(state.operator)} · ${day.period ? `${format.shortDate(day.period.from)} to ${format.shortDate(day.period.to)}` : format.serviceDate(day.service_date)}`;
  $("h-inten").textContent = hasReadings(overall) ? inTen(pct) : "–";
  $("h-pct").textContent = hasReadings(overall) ? `on time (${finiteNumber(pct).toFixed(1)}%)` : "no readings";
  // How the rest missed, for the network or operator (planning #123).
  $("h-split").innerHTML = hasReadings(overall) ? arrivalSplitHTML(overall, true) : "";
  renderSixBands();
  renderStrictFigure(overall);
  $("h-fill").style.width = Math.min(100, finiteNumber(pct)) + "%";
  $("h-fill").style.background = colour;
  $("h-tgt").style.left = state.target + "%";
  positionTargetLabel();
  const strict = strictFigure(overall);
  $("h-bar").setAttribute("aria-label", hasReadings(overall)
    ? `${finiteNumber(pct).toFixed(1)}% of readings at timing points were on time; ${finiteNumber(state.target)}% ${targetPending() ? `last published target (${lastTargetYearLabel()}); no target published for ${targetYearLabel()}` : `target for ${targetYearLabel()}`}${strict ? `; ${strict.pct.toFixed(1)}% strict, counting ${Math.round(strict.finalEarly)} early last-stop readings as early` : ""}`
    : "No readings for this operator in this period");
  $("s-median").textContent = format.delaySeconds(overall.median_delay_s);
  $("s-seen").textContent = overall.coverage_pct != null ? `${Math.round(overall.coverage_pct)} in 100` : "–";
  $("s-routes").textContent = (opData.routes || []).filter(hasReadings).length.toLocaleString();
  $("s-readings").textContent = finiteNumber(overall.readings_in_gate).toLocaleString();

  const status = overall.qualification?.status || "historical";
  $("evidence").querySelector("summary").textContent = `Confidence · ${status}`;
  let text = evidenceText(overall, day.operator_composition, state.operator, Boolean(day.period));
  const strictReadings = strictFigure(overall);
  if (strictReadings) text += ` Early last-stop readings: ${Math.round(strictReadings.finalEarly).toLocaleString("en-GB")}.`;
  const shortBy = state.target - finiteNumber(pct);
  if (hasReadings(overall) && !targetPending() && shortBy > 0) text += ` Target gap: ${shortBy.toFixed(1)} points.`;
  const excluded = data.excluded_service_days || [];
  if (excluded.length) text += ` Excluded historical days: ${excluded.map((x) => format.serviceDate(x.service_date)).join("; ")}.`;
  const withheld = opData.coverage_withheld_routes || [];
  if (withheld.length) text += ` Journey coverage is withheld for route${withheld.length === 1 ? "" : "s"} ${withheld.join(", ")} · duplicate entries.`;
  const groups = opData.frequency_adherence?.groups;
  if (groups) text += " " + [
    ["frequent", "Frequent services"], ["non_frequent", "Non-frequent services"],
    ["unclassified", "Frequency unverified"],
  ].filter(([key]) => groups[key] && finiteNumber(groups[key].readings)).map(([key, label]) => {
    const group = groups[key];
    const routes = group.routes == null ? ""
      : group.routes_counted === group.routes ? `, ${group.routes} routes`
      : `, ${group.routes_counted} of ${group.routes} routes (others: few readings)`;
    return group.on_time_pct == null
      ? `${label}: not enough readings for a figure yet`
      : `${label}: ${finiteNumber(group.on_time_pct).toFixed(1)}% on time${routes}`;
  }).join("; ") + ".";
  $("evidence-text").textContent = text;

  $("genat").textContent = data.generated_at ? new Date(data.generated_at).toLocaleString("en-GB") : "–";
  $("genday").textContent = day.period
    ? `${format.shortDate(day.period.from)} to ${format.shortDate(day.period.to)}`
    : `service day ${format.serviceDate(day.service_date)}`;
}

// --- Routes ------------------------------------------------------------------------------
function sortedRoutes() {
  const query = $("q").value.trim().toLowerCase();
  const rows = state.rows.filter(hasReadings).filter((row) => {
    const label = String(row.route).toLowerCase();
    // A place name finds the routes that go there ("clevedon" finds the X6).
    const where = String(row.description || "").toLowerCase();
    return !query || label.startsWith(query) || label.includes(" " + query)
      || (query.length >= 3 && where.includes(query));
  });
  const limited = (row) => (limitedEvidence(row) ? 1 : 0);
  const sorts = {
    worst: (a, b) => limited(a) - limited(b) || a.on_time_pct - b.on_time_pct,
    best: (a, b) => limited(a) - limited(b) || b.on_time_pct - a.on_time_pct,
    busy: (a, b) => finiteNumber(b.readings_in_gate) - finiteNumber(a.readings_in_gate),
    az: (a, b) => String(a.route).localeCompare(String(b.route), undefined, { numeric: true }),
  };
  return rows.sort(sorts[state.routeSort]);
}

function renderRouteList() {
  const rows = sortedRoutes();
  $("list").innerHTML = rows.slice(0, state.routesShown).map((row) => {
    const route = escapeHTML(row.route);
    const [number, operator] = splitRoute(row.route);
    const colour = verdict(row.on_time_pct);
    const pct = finiteNumber(row.on_time_pct);
    const selected = String(state.selectedRoute) === String(row.route);
    return `<li><button type="button" class="route-row${limitedEvidence(row) ? " lim" : ""}${selected ? " sel" : ""}" data-route="${route}"
      aria-pressed="${selected ? "true" : "false"}" aria-label="Route ${route}: ${pct.toFixed(1)}% on time${limitedEvidence(row) ? ", few readings" : ""}"${row.description ? ` title="${escapeHTML(row.description)}"` : ""}>
      <span class="rname"><span class="badge">${escapeHTML(number)}</span><small>${escapeHTML(operator)}</small></span>
      <span class="mini"><i style="width:${Math.min(100, pct)}%;background:${colour}"></i><b style="left:${state.target}%"></b></span>
      <span class="pct" style="color:${colour}">${Math.round(pct)}%</span>
    </button></li>`;
  }).join("") || '<li class="empty">No route matches that.</li>';
  $("more").hidden = rows.length <= state.routesShown;
  $("more").textContent = `All ${rows.length} routes`;
  $("list").querySelectorAll(".route-row").forEach((button) => {
    button.addEventListener("click", () => selectRoute(button.dataset.route));
  });
}

function selectRoute(route) {
  state.selectedRoute = route;
  renderRouteList();
  renderRouteDetail();
}

function closeRoute() {
  const route = state.selectedRoute;
  state.selectedRoute = null;
  renderRouteList();
  renderRouteDetail();
  if (route !== null) document.querySelector(`.route-row[data-route="${CSS.escape(String(route))}"]`)?.focus();
}

async function renderRouteDetail() {
  const box = $("detail");
  const row = state.rows.find((r) => String(r.route) === String(state.selectedRoute));
  document.body.classList.toggle("sheet-open", Boolean(row));
  if (!row) {
    box.classList.remove("open");
    box.innerHTML = '<div class="card empty">Select a route</div>';
    return;
  }
  box.classList.add("open");
  const operator = state.operator;
  const [hourly, segments] = await Promise.all([loadHourly(operator), loadSegments(operator)]);
  if (operator !== state.operator || String(row.route) !== String(state.selectedRoute)) return;

  const route = escapeHTML(row.route);
  const [number, operatorLabel] = splitRoute(row.route);
  const colour = verdict(row.on_time_pct);
  const reasons = likelyReasons(row.route, hourly, segments, row);
  const byKind = hourly?.routes?.[row.route];
  const fit = strongestStretches(segments?.routes?.[row.route], ["tight", "slack"]).slice(0, 4);
  const scale = Math.max(1, ...fit.map((s) => Math.max(finiteNumber(s.timetable_min), finiteNumber(s.typical_min))));
  box.innerHTML = `<div class="card" role="region" aria-label="Route ${route}">
    <div class="detail-head">
      <span class="badge big">${escapeHTML(number)}</span>${operatorLabel ? `<span class="small">${escapeHTML(operatorLabel)}</span>` : ""}
      <span class="verdict" style="color:${colour}">${inTen(row.on_time_pct)} on time</span>
      ${row.frequent ? '<span class="tag freq" title="High-frequency service: officially judged by waiting time, not timetable punctuality">frequent</span>' : ""}
      ${limitedEvidence(row) ? '<span class="small faint">few readings</span>' : ""}
      <button class="close" type="button" id="close">Close</button>
    </div>
    ${row.description ? `<p class="route-desc">${escapeHTML(row.description)}</p>` : ""}
    ${routePageUrl(state.operator, row.route) ? `<p class="route-desc"><a href="${escapeHTML(routePageUrl(state.operator, row.route))}">Live route ↗</a></p>` : ""}
    <p class="summary">${escapeHTML(routeSummary(row, hourly))}</p>
    <h3>Possible reasons</h3>
    <div class="reasons">${reasons.length
      ? reasons.map((x) => `<div class="reason"><strong>${escapeHTML(x.title)}</strong><span>${escapeHTML(x.text)}</span></div>`).join("")
      : '<div class="reason none"><strong>No clear pattern</strong></div>'}</div>
    ${byKind ? `<h3>Hourly punctuality</h3>${hourKindControls()}${hourStripHTML(byKind, hourly, `Route ${row.route} on time by hour`, true)}` : ""}
    ${journeysHTML(hourly?.journeys?.routes?.[row.route], hourly?.journeys?.duty_checked)}
    ${fit.length ? `<h3>Timetable fit</h3><ul class="fit">${fit.map((s) => stretchHTML(s, scale, false)).join("")}</ul>
      <p class="faint small">28 days · present in 3/4 weeks</p>` : ""}
    ${row.coverage_withheld ? '<p class="faint small">Coverage withheld · duplicate timetable entries</p>' : ""}
    <h3>The numbers</h3>
    ${arrivalSplitHTML(row)}
    <div class="numbers">
      <div><span class="k">On time</span><span class="v">${finiteNumber(row.on_time_pct).toFixed(1)}%</span></div>
      <div><span class="k">Typical</span><span class="v">${format.delaySeconds(row.median_delay_s)}</span></div>
      <div><span class="k">Average</span><span class="v">${format.delaySeconds(row.mean_delay_s)}</span></div>
      <div><span class="k">Readings</span><span class="v">${finiteNumber(row.readings_in_gate).toLocaleString()}</span></div>
    </div>

  </div>`;
  wireHourControls(box, () => { renderRouteDetail(); renderNetworkHours(); });
  $("close").addEventListener("click", closeRoute);
}

/** Early, on time, late and very late as one bar, so "6 in 10 on time" says how the rest missed. */
function arrivalSplitHTML(row, showDefinitions = false) {
  const readings = finiteNumber(row.readings_in_gate);
  if (!readings) return "";
  const veryLate = row.very_late == null ? null : Math.min(finiteNumber(row.very_late), finiteNumber(row.late));
  const parts = [
    ["early", "Early", "more than a minute ahead of the timetable (the bus may have arrived early and waited)", finiteNumber(row.early)],
    ["ontime", "On time", "up to a minute early or under 6 minutes late", finiteNumber(row.on_time)],
    ["late", "Late", veryLate == null ? "6 minutes or more" : "6 to 20 minutes", finiteNumber(row.late) - (veryLate || 0)],
    ...(veryLate == null ? [] : [["verylate", "Very late", "20 minutes or more", veryLate]]),
  ];
  const pct = (n) => (100 * n) / readings;
  return `<div class="split" role="img" aria-label="${escapeHTML(parts.map(([, label, what, n]) => `${label} ${Math.round(pct(n))}%: ${what}`).join(", "))}">${
    parts.map(([key, label, what, n]) => `<i class="${key}" style="width:${pct(n)}%" title="${escapeHTML(label)}: ${Math.round(pct(n))}% · ${escapeHTML(what)}"><span aria-hidden="true">${pct(n) >= 12 ? Math.round(pct(n)) + "%" : ""}</span></i>`).join("")}</div>
    <ul class="split-legend">${parts.map(([key, label, what, n]) =>
      `<li><i class="${key}"></i><strong>${Math.round(pct(n))}%</strong> ${escapeHTML(label.toLowerCase())}</li>`).join("")}</ul>
    ${showDefinitions ? `<details class="bands"><summary>What do these mean?</summary>
      <ul>${parts.map(([, label, what]) => `<li><strong>${escapeHTML(label)}:</strong> ${escapeHTML(what)}.</li>`).join("")}</ul>
    </details>` : ""}`;
}

// All six bands (planning #124): the same colours, lighter and darker.
const SIX_BANDS = [
  ["early5", "5+ min early", "more than 5 minutes ahead of the timetable", "5m+ early"],
  ["early1", "1 to 5 min early", "1 to 5 minutes ahead", "1–5m early"],
  ["ontime", "On time", "up to a minute early or under 6 minutes late", "on time"],
  ["late6", "6 to 10 min late", "6 to 10 minutes late", "6–10m late"],
  ["late10", "10 to 20 min late", "10 to 20 minutes late", "10–20m late"],
  ["late20", "20+ min late", "20 minutes or more late", "20m+ late"],
];

function sixBandHTML(counts) {
  if (!Array.isArray(counts) || counts.length !== SIX_BANDS.length) return "";
  const values = counts.map((n) => finiteNumber(n));
  const total = values.reduce((sum, n) => sum + n, 0);
  if (!total) return "";
  const pct = (n) => (100 * n) / total;
  return `<div class="split six" role="img" aria-label="${escapeHTML(SIX_BANDS.map(([, label, what], i) => `${label} ${Math.round(pct(values[i]))}%: ${what}`).join(", "))}">${
    SIX_BANDS.map(([key, label, what], i) => `<i class="${key}" style="width:${pct(values[i])}%" title="${escapeHTML(label)}: ${Math.round(pct(values[i]))}% · ${escapeHTML(what)}"><span aria-hidden="true">${pct(values[i]) >= 12 ? Math.round(pct(values[i])) + "%" : ""}</span></i>`).join("")}</div>
    <ul class="split-legend">${SIX_BANDS.map(([key, , , shortLabel], i) =>
      `<li><i class="${key}"></i><strong>${Math.round(pct(values[i]))}%</strong> ${escapeHTML(shortLabel)}</li>`).join("")}</ul>`;
}

async function renderSixBands() {
  const panel = $("h-bands");
  if (!panel || !panel.open) return;
  const body = panel.querySelector(".bands-body");
  const operator = state.operator;
  const bands = (await loadHourly(operator))?.bands;
  if (operator !== state.operator) return;
  const counts = state.day?.period ? bands?.window : bands?.days?.[state.day?.service_date];
  body.innerHTML = sixBandHTML(counts) || '<p class="small faint">Six bands unavailable</p>';
}

// --- Network sections -----------------------------------------------------------------
async function renderNetworkHours() {
  const panel = $("net-hours");
  const operator = state.operator;
  const hourly = await loadHourly(operator);
  if (operator !== state.operator) return;
  if (!hourly) {
    panel.innerHTML = '<p class="faint">No hourly readings</p>';
    return;
  }
  panel.innerHTML = hourKindControls()
    + hourStripHTML(hourly.network, hourly, `${operatorName(operator)} on time by hour`)
    + journeysHTML(hourly.journeys?.network, hourly.journeys?.duty_checked);
  wireHourControls(panel, () => { renderNetworkHours(); renderRouteDetail(); });
}

async function renderWorstStretches() {
  const list = $("worst");
  const operator = state.operator;
  const segments = await loadSegments(operator);
  if (operator !== state.operator) return;
  const worst = (segments?.worst || []).slice(0, 8);
  if (!worst.length) {
    list.innerHTML = '<li class="faint">No persistent shortfalls</li>';
    return;
  }
  const scale = Math.max(1, ...worst.map((item) => finiteNumber(item.typical_min)));
  list.innerHTML = worst.map((item) => stretchHTML(item, scale, true)).join("");
}

// --- Places ------------------------------------------------------------------------------
const AREA_LABELS = { "Bath and North East Somerset": "Bath & NE Somerset", "South Gloucestershire": "South Glos" };

// A plain SVG map: council area outlines, coloured, or with a dot for each
// ward where its stops are. No map tiles, so no third-party requests.
async function renderMap(opData) {
  const svg = $("map");
  const operator = state.operator;
  const places = await loadPlaces();
  if (operator !== state.operator) return;
  const geography = opData.geography || {};
  const areas = geography.area || [];
  const unknown = areas.find((a) => a.key === "Unknown");
  $("unknown-note").textContent = unknown && finiteNumber(opData.overall?.readings_in_gate)
    ? `Unmapped: ${finiteNumber(unknown.readings_in_gate).toLocaleString()} readings · ${((100 * finiteNumber(unknown.readings_in_gate)) / finiteNumber(opData.overall.readings_in_gate)).toFixed(2)}%`
    : "";
  $("map-legend").innerHTML = legendHTML(state.mapMode === "ward" ? "<span>Bigger dot = more readings</span>" : "");
  if (!places) {
    svg.innerHTML = "";
    $("map-d").textContent = "The map couldn't load.";
    return;
  }
  const k = Math.cos((51.42 * Math.PI) / 180);
  const project = ([lon, lat]) => [lon * k, -lat];
  const all = places.areas.flatMap((a) => a.rings.flat()).map(project);
  const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
  const x0 = Math.min(...xs), y0 = Math.min(...ys);
  const width = 600, scale = width / (Math.max(...xs) - x0);
  const height = Math.round((Math.max(...ys) - y0) * scale);
  const point = (lonLat) => { const [x, y] = project(lonLat); return [(x - x0) * scale, (y - y0) * scale]; };
  svg.setAttribute("viewBox", `-8 -8 ${width + 16} ${height + 16}`);

  const byArea = Object.fromEntries(areas.filter(hasReadings).map((a) => [a.key, a]));
  const areaText = (name, row) => row
    ? `${name}: ${inTen(row.on_time_pct)} on time (${finiteNumber(row.on_time_pct).toFixed(1)}%), from ${finiteNumber(row.readings_in_gate).toLocaleString()} readings`
    : `${name}: no readings`;
  const shapes = places.areas.map((area) => {
    const row = byArea[area.name];
    const d = area.rings.map((ring) => "M" + ring.map((c) => point(c).map((v) => v.toFixed(1)).join(",")).join("L") + "Z").join("");
    const fill = state.mapMode === "area" && row ? verdict(row.on_time_pct) : "var(--surface-2)";
    const text = areaText(area.name, row);
    return `<path d="${d}" fill="${fill}" fill-opacity="${state.mapMode === "area" ? 0.85 : 1}" tabindex="0" data-detail="${escapeHTML(text)}"><title>${escapeHTML(text)}</title></path>`;
  }).join("");
  const labels = state.mapMode !== "area" ? "" : places.areas.map((area) => {
    const row = byArea[area.name];
    if (!row) return "";
    const ring = area.rings.reduce((a, b) => (b.length > a.length ? b : a));
    const pts = ring.map(point);
    const cx = pts.reduce((t, p) => t + p[0], 0) / pts.length;
    const cy = pts.reduce((t, p) => t + p[1], 0) / pts.length;
    return `<text x="${cx.toFixed(0)}" y="${cy.toFixed(0)}">${escapeHTML(AREA_LABELS[area.name] || area.name)}</text>`
      + `<text class="sub" x="${cx.toFixed(0)}" y="${(cy + 21).toFixed(0)}">${Math.round(row.on_time_pct)}%</text>`;
  }).join("");
  let dots = "";
  if (state.mapMode === "ward") {
    const wards = (geography.ward || []).filter((w) => hasReadings(w) && places.wards[w.key])
      .sort((a, b) => finiteNumber(b.readings_in_gate) - finiteNumber(a.readings_in_gate));
    const most = Math.max(1, ...wards.map((w) => finiteNumber(w.readings_in_gate)));
    dots = wards.map((ward) => {
      const [lat, lon] = places.wards[ward.key];
      const [cx, cy] = point([lon, lat]);
      const r = 3 + 11 * Math.sqrt(finiteNumber(ward.readings_in_gate) / most);
      const text = `${areaText(ward.key, ward)}${limitedEvidence(ward) ? " (few readings)" : ""}`;
      return `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${r.toFixed(1)}" fill="${verdict(ward.on_time_pct)}" fill-opacity="${limitedEvidence(ward) ? 0.35 : 0.9}" tabindex="0" data-ward="${escapeHTML(ward.key)}" data-detail="${escapeHTML(text)}"><title>${escapeHTML(text)}</title></circle>`;
    }).join("");
  }
  svg.innerHTML = shapes + labels + dots;
  svg.querySelectorAll("[data-detail]").forEach((shape) => {
    const show = () => {
      svg.querySelectorAll(".picked").forEach((s) => s.classList.remove("picked"));
      shape.classList.add("picked");
      const ward = shape.dataset.ward && (geography.ward || []).find((w) => w.key === shape.dataset.ward);
      const why = ward ? wardRoutesHTML(ward) : "";
      $("map-d").innerHTML = escapeHTML(shape.dataset.detail) + (why ? `.${why}` : "");
      wireWardRoutes($("map-d"));
    };
    shape.addEventListener("click", show);
    shape.addEventListener("focus", show);
  });
}

// "Mostly the X4 (4 in 10 here, 62% of readings) and the X5 (5 in 10, 30%)."
// The routes behind a ward's figure, each linking to its route panel. Where
// they carry under half its readings, they're only "the busiest".
function wardRoutesHTML(ward) {
  const total = finiteNumber(ward.readings_in_gate);
  const routes = (ward.routes || []).filter((r) => total && finiteNumber(r.readings_in_gate) && r.on_time_pct != null);
  if (!routes.length) return "";
  const known = new Set(state.rows.map((r) => String(r.route)));
  let covered = 0;
  const parts = routes.map((r, i) => {
    const name = known.has(String(r.route))
      ? `<button type="button" class="ward-route" data-route="${escapeHTML(r.route)}">${escapeHTML(r.route)}</button>`
      : escapeHTML(r.route);
    const share = Math.round((100 * finiteNumber(r.readings_in_gate)) / total);
    covered += share;
    return `the ${name} (${inTen(r.on_time_pct)}${i ? "" : " here"}, ${share}%${i ? "" : " of readings"})`;
  });
  const list = parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`;
  if (covered >= 50) return ` Mostly ${list}.`;
  return parts.length === 1 ? ` The busiest route here is ${list}.` : ` The busiest routes here are ${list}.`;
}

function wireWardRoutes(box) {
  box.querySelectorAll(".ward-route").forEach((button) => {
    button.addEventListener("click", () => {
      selectRoute(button.dataset.route);
      $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
}

function rankRow(row) {
  const key = escapeHTML(row.key);
  const colour = verdict(row.on_time_pct);
  const pct = finiteNumber(row.on_time_pct);
  const why = wardRoutesHTML(row);
  const name = why ? `<button type="button" class="ward-name" aria-expanded="false">${key}</button>` : `<span>${key}</span>`;
  return `<li class="${limitedEvidence(row) ? "lim" : ""}" title="${key}: ${pct.toFixed(1)}% from ${finiteNumber(row.readings_in_gate).toLocaleString()} readings">${name}
    <span class="mini"><i style="width:${Math.min(100, pct)}%;background:${colour}"></i><b style="left:${state.target}%"></b></span>
    <span class="pct" style="color:${colour}">${Math.round(pct)}%</span></li>`
    + (why ? `<li class="ward-why" hidden>${key}: ${inTen(pct)} on time.${why}</li>` : "");
}

function renderWards(opData) {
  const query = $("wq").value.trim().toLowerCase();
  const wards = (opData.geography?.ward || [])
    .filter((w) => hasReadings(w) && w.key !== "Unknown" && (!query || String(w.key).toLowerCase().includes(query)));
  const pick = query
    ? wards.slice(0, 12)
    : wards.filter((w) => !limitedEvidence(w)).sort((a, b) => a.on_time_pct - b.on_time_pct).slice(0, 6);
  $("wards").innerHTML = (query ? "" : '<li class="note small faint">Least punctual wards</li>')
    + (pick.map(rankRow).join("") || '<li class="note small faint">No ward matches.</li>');
  $("wards").querySelectorAll(".ward-name").forEach((button) => {
    button.addEventListener("click", () => {
      const why = button.closest("li").nextElementSibling;
      why.hidden = !why.hidden;
      button.setAttribute("aria-expanded", String(!why.hidden));
    });
  });
  wireWardRoutes($("wards"));
}

function renderPlaces(opData) {
  renderMap(opData);
  renderWards(opData);
}

// --- Vehicles ----------------------------------------------------------------------------
function wikiLink(model) {
  const m = (model || "").toLowerCase();
  const map = [
    ["enviro400 city", "Alexander_Dennis_Enviro400#Enviro400_City"],
    ["enviro400", "Alexander_Dennis_Enviro400"],
    ["enviro200", "Alexander_Dennis_Enviro200"],
    ["enviro", "Alexander_Dennis_Enviro400"],
    ["streetlite", "Wright_StreetLite"],
    ["streetdeck", "Wright_StreetDeck"],
    ["b9tl", "Volvo_B9TL"],
    ["eclipse", "Wright_Eclipse_Gemini"],
    ["gemini", "Wright_Eclipse_Gemini"],
    ["solo", "Optare_Solo"],
    ["e12", "Yutong_E12"],
    ["yutong", "Yutong"],
  ];
  for (const [kw, slug] of map) {
    if (m.includes(kw)) return "https://en.wikipedia.org/wiki/" + slug;
  }
  return "https://en.wikipedia.org/w/index.php?search=" + encodeURIComponent(model + " bus");
}

const POWER = [
  ["electric", "Electric", "var(--good)"],
  ["gas", "Gas", "var(--link)"],
  ["hybrid", "Hybrid", "#a371f7"],
  ["diesel", "Diesel", "var(--fg-3)"],
  ["other", "Other", "#4b5563"],
];

function powerOf(model) {
  if (model.electric) return "electric";
  return POWER.some(([key]) => key === model.fuel) ? model.fuel : "other";
}

function renderFleet(opData) {
  const fleet = (opData.fleet || []).filter((m) => finiteNumber(m.readings_in_gate) > 0)
    .sort((a, b) => finiteNumber(b.vehicles) - finiteNumber(a.vehicles));
  if (!fleet.length) {
    $("e-share").textContent = "";
    $("e-text").textContent = "No vehicle readings";
    $("fleetbar").innerHTML = "";
    $("fleet-legend").innerHTML = "";
    $("fleet").innerHTML = "";
    $("fleet-more").hidden = true;
    return;
  }
  // At a glance: the share of the buses we saw, by how they're powered.
  const buses = Object.fromEntries(POWER.map(([key]) => [key,
    fleet.filter((m) => powerOf(m) === key).reduce((t, m) => t + finiteNumber(m.vehicles), 0)]));
  const allBuses = Object.values(buses).reduce((a, b) => a + b, 0) || 1;
  const used = POWER.filter(([key]) => buses[key]);
  const onTime = (models) => {
    const readings = models.reduce((t, m) => t + finiteNumber(m.readings_in_gate), 0);
    return readings ? models.reduce((t, m) => t + finiteNumber(m.readings_in_gate) * finiteNumber(m.on_time_pct), 0) / readings : null;
  };
  const electricOn = onTime(fleet.filter((m) => m.electric));
  const restOn = onTime(fleet.filter((m) => !m.electric));
  const compare = electricOn == null || restOn == null ? ""
    : ` On time: electric ${Math.round(electricOn)}% / rest ${Math.round(restOn)}%`;
  $("e-share").textContent = `${Math.round((100 * buses.electric) / allBuses)}%`;
  $("e-share").classList.toggle("none", !buses.electric);
  $("e-text").textContent = buses.electric
    ? `electric · ${buses.electric.toLocaleString()} / ${allBuses.toLocaleString()} vehicles.${compare}`
    : `electric · 0 / ${allBuses.toLocaleString()} vehicles`;
  $("fleetbar").innerHTML = used.map(([key, label, colour]) =>
    `<i style="width:${(100 * buses[key]) / allBuses}%;background:${colour}" title="${label}: ${buses[key]} buses"></i>`).join("");
  $("fleetbar").setAttribute("aria-label", used.map(([key, label]) => `${label} ${buses[key]} buses`).join(", "));
  $("fleet-legend").innerHTML = used.map(([key, label, colour]) =>
    `<span><i style="background:${colour}"></i>${label} ${buses[key]} · ${Math.round((100 * buses[key]) / allBuses)}%</span>`).join("");

  const rows = state.fleetAll ? fleet : fleet.slice(0, MODELS_SHOWN);
  $("fleet").innerHTML = rows.map((m) => {
    const power = powerOf(m);
    const tag = power === "electric" ? '<span class="tag">electric</span>'
      : power === "gas" ? '<span class="tag gas">gas</span>' : "";
    return `<tr><td><a href="${escapeHTML(wikiLink(m.model))}" target="_blank" rel="noopener noreferrer">${escapeHTML(m.model)}</a>${tag}</td>
      <td class="num">${finiteNumber(m.vehicles)}</td>
      <td class="num" style="color:${verdict(m.on_time_pct)}" title="from ${finiteNumber(m.readings_in_gate).toLocaleString()} readings">${hasReadings(m) ? Math.round(m.on_time_pct) + "%" : "–"}</td></tr>`;
  }).join("");
  $("fleet-more").hidden = fleet.length <= MODELS_SHOWN;
  $("fleet-more").textContent = state.fleetAll ? "Show fewer" : `All ${fleet.length} models`;
}

// --- Operator and period ------------------------------------------------------------------
function currentOpData() {
  return state.day.by_operator[state.operator] || { overall: {}, routes: [] };
}

function renderOperatorPicker() {
  const select = $("operator");
  const present = (state.data.operators || []).filter((o) => state.day.by_operator[o.code]);
  select.innerHTML = present
    .map((o) => `<option value="${escapeHTML(o.code)}"${o.code === state.operator ? " selected" : ""}>${escapeHTML(o.name)}</option>`)
    .join("");
}

function selectOperator(code) {
  state.operator = code;
  state.selectedRoute = null;
  state.routesShown = ROUTES_SHOWN;
  state.fleetAll = false;
  const opData = currentOpData();
  state.rows = opData.routes || [];
  $("operator").value = code;
  try { history.replaceState(null, "", code === "ALL" ? location.pathname + location.hash : `?operator=${encodeURIComponent(code)}${location.hash}`); } catch (_error) { /* file:// */ }
  renderHeadline(opData);
  // On a wide screen, open the least punctual well-measured route straight away.
  if (window.matchMedia("(min-width: 861px)").matches) {
    state.selectedRoute = sortedRoutes().find((row) => !limitedEvidence(row))?.route ?? null;
  }
  renderRouteList();
  renderRouteDetail();
  renderNetworkHours();
  renderWorstStretches();
  renderPlaces(opData);
  renderFleet(opData);
}

// The default view pools the last 28 days, so every route that ran in that
// time shows with a solid sample; the latest single day is one tap away.
function viewDay(view) {
  const data = state.data;
  if (view === "period" && data.period) {
    return { service_date: data.period.to, by_operator: data.period.by_operator,
      operator_composition: data.period.operator_composition, period: data.period };
  }
  return data.latest_day || (data.days && data.days[data.days.length - 1]);
}

function renderViewControls() {
  const box = $("view-controls");
  if (!state.data.period) return;
  const latest = state.data.latest_day;
  const options = [
    ["period", `Last ${state.data.period.days} days`],
    ["day", `Latest day (${latest ? format.shortDate(latest.service_date) : "–"})`],
  ];
  box.innerHTML = options.map(([value, label]) =>
    `<button type="button" class="${state.view === value ? "on" : ""}" data-view="${value}"
      aria-pressed="${state.view === value ? "true" : "false"}">${escapeHTML(label)}</button>`).join("");
  box.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => selectView(button.dataset.view));
  });
  box.hidden = false;
}

function selectView(view) {
  state.view = view;
  state.day = viewDay(view);
  renderViewControls();
  const present = (state.data.operators || []).filter((o) => state.day.by_operator[o.code]);
  const keep = present.find((o) => o.code === state.operator) || present[0];
  renderOperatorPicker();
  if (keep) selectOperator(keep.code);
}

function showError(message) {
  const box = $("err");
  box.textContent = message;
  box.style.display = "block";
}

async function fetchJSON(name) {
  const response = await fetch(name, { cache: "no-store" });
  if (!response.ok) throw new Error("HTTP " + response.status);
  return response.json();
}

async function load() {
  let data;
  try {
    // The small summary first; the full daily history only as a fallback.
    data = await fetchJSON("audit_summary.json").catch(() => fetchJSON("audit_data.json"));
  } catch (error) {
    showError(
      "Couldn't load audit_data.json (" +
        error.message +
        "). If you opened this file directly, run a local server instead: cd audit-site && python -m http.server 8000, then visit http://localhost:8000"
    );
    return;
  }

  state.data = data;
  state.target = Math.min(100, Math.max(0,
    finiteNumber(data.current_target_pct ?? data.last_published_target_pct,
      finiteNumber(data.target_pct, DEFAULT_TARGET_PCT))));
  state.targetStatus = String(data.current_target_status || "published");
  state.lastTargetFinancialYear = String(data.last_published_target_financial_year || "");
  state.targetFinancialYear = String(data.current_target_financial_year || "");
  state.targetSource = String(data.current_target_source || "");
  state.targetSourceUrl = String(data.current_target_source_url || "");
  renderTargetContext();

  state.view = data.period ? "period" : "day";
  const day = viewDay(state.view);
  if (!day) {
    showError("No rollup days in the data yet.");
    return;
  }

  // Support snapshots that predate per-operator summaries.
  if (!day.by_operator) {
    day.by_operator = { FBRI: { overall: day.overall, routes: day.routes || [] } };
    data.operators = [{ code: "FBRI", name: data.operator_name || "First Bristol" }];
  }

  state.day = day;
  renderViewControls();

  const present = (data.operators || []).filter((o) => day.by_operator[o.code]);
  if (!present.length) {
    showError("No operator data for this day yet.");
    return;
  }
  const asked = new URLSearchParams(location.search).get("operator");
  state.operator = (present.find((o) => o.code === asked) || present[0]).code;
  renderOperatorPicker();
  selectOperator(state.operator);
}

function wireControls() {
  const targetPosition = new ResizeObserver(positionTargetLabel);
  targetPosition.observe($("h-bar"));
  targetPosition.observe($("h-tgt-label"));
  $("operator").addEventListener("change", (event) => selectOperator(event.target.value));
  $("q").addEventListener("input", () => {
    state.routesShown = ROUTES_SHOWN;
    renderRouteList();
  });
  $("sort").querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      state.routeSort = button.dataset.s;
      $("sort").querySelectorAll("button").forEach((other) => {
        other.classList.toggle("on", other === button);
        other.setAttribute("aria-pressed", other === button ? "true" : "false");
      });
      renderRouteList();
    });
  });
  $("more").addEventListener("click", () => {
    state.routesShown = Infinity;
    renderRouteList();
  });
  $("wq").addEventListener("input", () => renderWards(currentOpData()));
  $("map-mode").querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      state.mapMode = button.dataset.m;
      $("map-mode").querySelectorAll("button").forEach((other) => {
        other.classList.toggle("on", other === button);
        other.setAttribute("aria-pressed", other === button ? "true" : "false");
      });
      $("map-d").textContent = "Select a place";
      renderMap(currentOpData());
    });
  });
  $("h-bands").addEventListener("toggle", renderSixBands);
  $("fleet-more").addEventListener("click", () => {
    state.fleetAll = !state.fleetAll;
    renderFleet(currentOpData());
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && $("detail").classList.contains("open")
        && window.matchMedia("(max-width: 860px)").matches) closeRoute();
  });
}

wireControls();
load();
