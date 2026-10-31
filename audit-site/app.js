function limitedEvidence(row) {
  return !row.qualification || row.qualification.status === "unavailable"
    || row.qualification.reasons?.includes("wide_sampling_range") || false;
}

function evidenceText(overall, composition, operator, isPeriod) {
  const q = overall.qualification;
  const parts = [];
  if (!q) parts.push("Historical result: journey-level sample checks are unavailable.");
  else {
    parts.push(q.status === "unavailable" ? "Punctuality unavailable: the retained evidence cannot support this result."
      : q.status === "indicative" ? "Indicative punctuality from the buses observed."
      : "Punctuality from the buses observed.");
    if (q.journeys != null) parts.push(`${q.journeys.toLocaleString()} observed journeys across ${q.service_days} service day(s).`);
    if (q.reasons?.includes("coverage_unverified")) parts.push("The share of scheduled journeys captured is unverified.");
    if (q.reasons?.includes("inconsistent_journey_order")) parts.push("Some retained stop readings have an inconsistent journey order.");
    if (q.status !== "unavailable" && q.range_pct) parts.push(`Sampling and known journey-order sensitivity: ${q.range_pct[0]}–${q.range_pct[1]}%. This assumes independent journeys and does not cover missing-feed bias or other assignment errors.`);
  }
  if (operator === "ALL" && composition) {
    const shares = composition.operators.filter(x => x.readings > 0)
      .map(x => `${x.name}: ${x.share_pct}%`).join("; ");
    parts.push(shares ? `Share of ${isPeriod ? "this period's" : "this day's"} readings: ${shares}.` : "No eligible operator readings.");
  }
  parts.push("Places and operators without observations are not represented. This is timetable adherence, not excess waiting time or a passenger survey.");
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

function targetReferenceHTML() {
  const source = escapeHTML(state.targetSource || "published WECA target schedule");
  const url = safeHTTPURL(state.targetSourceUrl);
  const sourceHTML = url
    ? `<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">${source}</a>`
    : source;
  return `<strong>${finiteNumber(state.target)}%</strong> area-wide target for <strong>${escapeHTML(targetYearLabel())}</strong> (${sourceHTML})`;
}

function renderTargetContext() {
  const reference = targetReferenceHTML();
  const longTerm = `${finiteNumber(state.data.target_pct, 95)}% by ${finiteNumber(state.data.target_year, 2030)}`;
  const note = `Compared with WECA's ${reference}. The separate long-term goal is ${escapeHTML(longTerm)}.`;
  $("headline-target-note").innerHTML = note;
  // The sections only need the colour key; the full reference is up top.
  const key = `Green means at or above the ${finiteNumber(state.target)}% target for ${escapeHTML(targetYearLabel())}.`;
  ["geo-target-note", "route-target-note"].forEach((id) => {
    const element = $(id);
    if (element) element.innerHTML = key;
  });
  const point = $("point-callout");
  if (point) {
    point.innerHTML = `<strong>The point.</strong> Bus operators across the West of England publish their live vehicle data. WECA's current published benchmark is a ${reference}, with a longer-term goal of ${escapeHTML(longTerm)}, but there is no accessible route-level public scorecard showing whether those targets are being met. This site builds one from the operators' own open data.`;
  }
  const footer = $("footer-intro");
  if (footer) {
    footer.innerHTML = `An independent, non-commercial measurement of West of England bus punctuality, compared with WECA's ${reference}. Full method, sources and limitations are in the <a href="https://github.com/bristol-bus-bot/weca-bus-audit/blob/main/AUDIT_METHODOLOGY.md">methodology</a>, and all the code and data are on <a href="https://github.com/bristol-bus-bot/weca-bus-audit">GitHub</a>.`;
  }
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
  return `<span><i style="background:var(--good)"></i>${target}%+ (target)</span>`
    + `<span><i style="background:var(--warn)"></i>${target - 25}–${target - 1}%</span>`
    + `<span><i style="background:var(--bad)"></i>under ${target - 25}%</span>${extra}`;
}

function hasReadings(row) {
  return row.on_time_pct !== null && row.on_time_pct !== undefined;
}

function inTen(pct) {
  return `${Math.round(finiteNumber(pct) / 10)} in 10`;
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

function hourSummary(cells, kind, subject) {
  const when = kind === "weekday" ? "On weekdays" : "At weekends";
  const hours = bestAndWorstHour(cells);
  if (!hours) return `${when} there aren't yet enough readings in any hour to compare times of day.`;
  const { worst, best } = hours;
  if (Math.round(best.pct / 10) === Math.round(worst.pct / 10))
    return `${when} ${subject} about as punctual at every well-measured hour: around ${inTen(best.pct)} on time.`;
  return `${when} ${subject} least punctual around ${hourLabel(worst.hour)} (${inTen(worst.pct)} on time) and most punctual around ${hourLabel(best.hour)} (${inTen(best.pct)}).`;
}

function hourKindControls() {
  return `<div class="seg" role="group" aria-label="Day type">${["weekday", "weekend"].map((kind) =>
    `<button type="button" class="hctl${state.hourKind === kind ? " on" : ""}" data-hour-kind="${kind}"
      aria-pressed="${state.hourKind === kind ? "true" : "false"}">${kind === "weekday" ? "Weekdays" : "Weekends"}</button>`).join("")}</div>`;
}

function hourStripHTML(byKind, hourly, title, subject) {
  const kind = state.hourKind;
  const cells = hourCells(byKind, kind, hourly.min_readings);
  if (!cells.length) return '<p class="faint small">No hourly readings for this period yet.</p>';
  const days = hourly.window.days[kind];
  const strip = cells.map((c) => {
    const colour = c.total ? verdict(c.pct) : "var(--fg-3)";
    const text = c.total
      ? `${hourLabel(c.hour)}: ${inTen(c.pct)} on time, ${c.late.toLocaleString()} late, ${c.early.toLocaleString()} early, from ${c.total.toLocaleString()} readings${c.low ? " (too few to rely on)" : ""}`
      : `${hourLabel(c.hour)}: no buses measured`;
    return `<li class="hour-cell${c.low ? " low" : ""}" title="${escapeHTML(text)}" tabindex="0"
      data-detail="${escapeHTML(text)}" style="background:${c.total ? colour : "transparent"}">
      <span class="sr-only">${escapeHTML(text)}</span>
      <span class="tick" aria-hidden="true">${c.hour % 3 === 0 ? String(c.hour).padStart(2, "0") : ""}</span></li>`;
  }).join("");
  // A route's summary already says this for weekdays; repeat it only at weekends.
  const sentence = subject && (subject !== "it is" || kind === "weekend")
    ? `<p class="summary">${escapeHTML(hourSummary(cells, kind, subject))}</p>` : "";
  return `${sentence}
    <ol class="strip" aria-label="${escapeHTML(title)}">${strip}</ol>
    <p class="strip-detail" aria-live="polite">Tap an hour for its figures.</p>
    <div class="legend">${legendHTML('<span><i style="background:var(--fg-3);opacity:.35"></i>too few readings</span>')}</div>
    <p class="faint small">${days} ${kind === "weekday" ? "weekday" : "weekend"} service day${days === 1 ? "" : "s"},
      ${escapeHTML(format.shortDate(hourly.window.from))} to ${escapeHTML(format.shortDate(hourly.window.to))}.
      Hours are the scheduled time at each timing point.</p>`;
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

// --- Did the buses turn up? -----------------------------------------------------
const JOURNEY_BAND_LABELS = {
  am_peak: "7–10am", interpeak: "10am–4pm", pm_peak: "4–7pm",
  evening: "evenings and early mornings",
};

function journeyCounts(entry, dutyChecked) {
  if (!entry) return null;
  const [timetabled, seen, likely] = entry.total.map((v) => finiteNumber(v));
  if (!timetabled) return null;
  const counted = dutyChecked ? likely : 0;
  return { timetabled, seen, likely: counted, unseen: Math.max(0, timetabled - seen - counted) };
}

// Three honestly separate numbers, never added together into "cancelled".
function journeysHTML(entry, dutyChecked) {
  const counts = journeyCounts(entry, dutyChecked);
  if (!counts) return "";
  const { timetabled, seen, likely, unseen } = counts;
  const overallMissing = (timetabled - seen) / timetabled;
  const worst = Object.entries(entry.bands || {})
    .map(([band, v]) => ({ band, timetabled: finiteNumber(v[0]), seen: finiteNumber(v[1]) }))
    .filter((b) => b.timetabled >= 50)
    .map((b) => ({ ...b, missing: (b.timetabled - b.seen) / b.timetabled }))
    .sort((a, b) => b.missing - a.missing)[0];
  const worstLine = worst && worst.missing >= 0.03 && worst.missing >= 1.5 * overallMissing
    ? `<p class="small">Most often missing: <strong>${escapeHTML(JOURNEY_BAND_LABELS[worst.band] || worst.band)}</strong>, when ${Math.round(100 * (worst.timetabled - worst.seen) / worst.timetabled)} in 100 journeys weren't seen.</p>`
    : "";
  return `
    <h3>Did the buses turn up?</h3>
    <div class="turnup">
      <div><b>${Math.round((100 * seen) / timetabled)}%</b><span>of ${timetabled.toLocaleString()} timetabled journeys seen running</span></div>
      ${dutyChecked ? `<div><b>${likely.toLocaleString()}</b><span><strong>Very likely didn't run.</strong> We tracked the same bus on the journey before and the journey after, but not on this one.</span></div>` : ""}
      <div><b>${unseen.toLocaleString()}</b><span><strong>Not seen.</strong> Either it was cancelled or the bus's tracker was off. We can't tell which.</span></div>
    </div>
    ${worstLine}
    <p class="faint small">Weekdays and weekends together, counting only days when our tracking was working properly.</p>`;
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
    <div class="mins"><span>Buses take</span><span class="track"><span class="ty${item.kind === "slack" ? " slack" : ""}" style="width:${(100 * typical) / scale}%"></span></span><span>${Math.round(typical)} min</span></div>`;
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
    ? `${when} · ${finiteNumber(item.share_pct)}% of ${finiteNumber(item.journeys).toLocaleString()} journeys lose 2+ min here`
    : `${when} · buses run early here and may have to wait`;
  const badge = withRoute ? `<span class="badge">${escapeHTML(splitRoute(item.route)[0])}</span> ` : "";
  return `<li>${minutesPill(item)} ${badge}<strong>${stopHTML(item.from, item.from_place)} → ${stopHTML(item.to, item.to_place)}</strong>
    <span class="small"> ${escapeHTML(note)}</span>${minutesBars(item, scale)}</li>`;
}

// --- Likely reasons ------------------------------------------------------------------
// Only from patterns with evidence behind them, and never stated as fact.
function likelyReasons(route, hourly, segments) {
  const out = [];
  const items = segments?.routes?.[route] || [];
  const tight = strongestStretches(items, ["tight"]);
  if (tight.length) {
    const t = tight[0];
    out.push({ title: "The timetable doesn't allow enough time",
      text: `Between ${stopLabel(t.from, t.from_place)} and ${stopLabel(t.to, t.to_place)}, buses get ${Math.round(t.timetable_min)} min but usually take about ${Math.round(t.typical_min)} (${dayTypeLabel(t.day_type)} ${FIT_BANDS[t.band] || t.band}).` });
  }
  const byKind = hourly?.routes?.[route];
  const hours = byKind && bestAndWorstHour(hourCells(byKind, "weekday", hourly.min_readings));
  if (hours && hours.best.pct - hours.worst.pct >= 20) {
    out.push({ title: `Much worse around ${hourLabel(hours.worst.hour)} on weekdays`,
      text: `${inTen(hours.worst.pct)} on time then, against ${inTen(hours.best.pct)} around ${hourLabel(hours.best.hour)}. Whatever goes wrong, it goes wrong most at that time of day.` });
  }
  const counts = journeyCounts(hourly?.journeys?.routes?.[route], hourly?.journeys?.duty_checked);
  if (counts && counts.timetabled >= 100) {
    const missing = (counts.timetabled - counts.seen) / counts.timetabled;
    if (missing >= 0.1 || counts.likely >= 5) {
      out.push({ title: "Some journeys didn't turn up",
        text: `We saw ${Math.round((100 * counts.seen) / counts.timetabled)} in 100 timetabled journeys run.${counts.likely ? ` ${counts.likely} very likely didn't run (the same bus ran the journeys either side).` : ""}` });
    }
  }
  const tightPlaces = new Set(tight.map((t) => `${t.from}|${t.to}`));
  const wait = items.find((i) => i.kind === "waiting" && !tightPlaces.has(`${i.from}|${i.to}`));
  if (wait) {
    out.push({ title: `Buses wait at ${wait.from}`,
      text: "They usually arrive early and sit there, so the next stretch looks slow on paper." });
  }
  return out.slice(0, 3);
}

function routeSummary(row, hourly) {
  const head = `The ${splitRoute(row.route)[0]} is on time ${inTen(row.on_time_pct)} times`;
  const byKind = hourly?.routes?.[row.route];
  const hours = byKind && bestAndWorstHour(hourCells(byKind, "weekday", hourly.min_readings));
  if (!hours) return `${head}. There aren't enough readings yet to say when it goes wrong.`;
  if (Math.round(hours.best.pct / 10) === Math.round(hours.worst.pct / 10))
    return `${head}, and about as punctual at every time of day.`;
  return `${head}. On weekdays it's least punctual around ${hourLabel(hours.worst.hour)} (${inTen(hours.worst.pct)}) and best around ${hourLabel(hours.best.hour)} (${inTen(hours.best.pct)}).`;
}

// --- Headline --------------------------------------------------------------------------
function renderHeadline(opData) {
  const data = state.data;
  const day = state.day;
  const overall = opData.overall || {};
  const pct = overall.on_time_pct;
  const colour = verdict(pct);
  const when = day.period
    ? `the last ${day.period.days} days (${format.shortDate(day.period.from)} to ${format.shortDate(day.period.to)})`
    : format.serviceDate(day.service_date);

  $("period-line").textContent = `${operatorName(state.operator)} · ${day.period ? `${format.shortDate(day.period.from)} to ${format.shortDate(day.period.to)}` : format.serviceDate(day.service_date)}`;
  $("h-inten").textContent = hasReadings(overall) ? inTen(pct) : "–";
  $("h-pct").textContent = hasReadings(overall) ? `on time (${finiteNumber(pct).toFixed(1)}%)` : "no readings";
  const shortBy = state.target - finiteNumber(pct);
  $("h-lead").textContent = hasReadings(overall)
    ? `${day.period ? "Over" : "On"} ${when}, ${finiteNumber(pct).toFixed(1)}% of ${state.operator === "ALL" ? "" : operatorName(state.operator) + " "}bus arrivals at timing points were on time. The target for ${targetYearLabel()} is ${finiteNumber(state.target)}%${shortBy > 0 ? `, so buses are ${shortBy.toFixed(1)} points short` : ", and buses met it"}.`
    : "No readings for this operator in this period.";
  $("h-fill").style.width = Math.min(100, finiteNumber(pct)) + "%";
  $("h-fill").style.background = colour;
  $("h-tgt").style.left = state.target + "%";
  $("h-tgt-label").textContent = `Target ${finiteNumber(state.target)}%`;
  $("s-median").textContent = format.delaySeconds(overall.median_delay_s);
  $("s-seen").textContent = overall.coverage_pct != null ? `${Math.round(overall.coverage_pct)} in 100` : "–";
  $("s-routes").textContent = (opData.routes || []).filter(hasReadings).length.toLocaleString();
  $("s-readings").textContent = finiteNumber(overall.readings_in_gate).toLocaleString();

  const status = overall.qualification?.status || "historical";
  $("evidence").querySelector("summary").textContent = `How sure are we? (${status} sample)`;
  let text = evidenceText(overall, day.operator_composition, state.operator, Boolean(day.period));
  const excluded = data.excluded_service_days || [];
  if (excluded.length) text += ` Excluded historical days: ${excluded.map((x) => format.serviceDate(x.service_date)).join("; ")}.`;
  const withheld = opData.coverage_withheld_routes || [];
  if (withheld.length) text += ` Journey coverage is withheld for route${withheld.length === 1 ? "" : "s"} ${withheld.join(", ")}: the timetable lists some of their journeys twice with identical times, so the number of scheduled journeys is uncertain. Their punctuality still counts.`;
  const groups = opData.frequency_adherence?.groups;
  if (groups) text += " " + [
    ["frequent", "Frequent services"], ["non_frequent", "Non-frequent services"],
    ["unclassified", "Frequency unverified"],
  ].filter(([key]) => groups[key] && finiteNumber(groups[key].readings)).map(([key, label]) => {
    const group = groups[key];
    const routes = group.routes == null ? ""
      : group.routes_counted === group.routes ? `, ${group.routes} routes`
      : `, ${group.routes_counted} of ${group.routes} routes (the rest have too few readings)`;
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
    return !query || label.startsWith(query) || label.includes(" " + query);
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
      aria-pressed="${selected ? "true" : "false"}" aria-label="Route ${route}: ${pct.toFixed(1)}% on time${limitedEvidence(row) ? ", few readings" : ""}">
      <span class="rname"><span class="badge">${escapeHTML(number)}</span><small>${escapeHTML(operator)}</small></span>
      <span class="mini"><i style="width:${Math.min(100, pct)}%;background:${colour}"></i><b style="left:${state.target}%"></b></span>
      <span class="pct" style="color:${colour}">${Math.round(pct)}%</span>
    </button></li>`;
  }).join("") || '<li class="empty">No route matches that.</li>';
  $("more").hidden = rows.length <= state.routesShown;
  $("more").textContent = `Show all ${rows.length} routes`;
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
    box.innerHTML = '<div class="card empty">Pick a route to see when it runs late, likely reasons, and whether its buses turned up.</div>';
    return;
  }
  box.classList.add("open");
  const operator = state.operator;
  const [hourly, segments] = await Promise.all([loadHourly(operator), loadSegments(operator)]);
  if (operator !== state.operator || String(row.route) !== String(state.selectedRoute)) return;

  const route = escapeHTML(row.route);
  const [number, operatorLabel] = splitRoute(row.route);
  const colour = verdict(row.on_time_pct);
  const reasons = likelyReasons(row.route, hourly, segments);
  const byKind = hourly?.routes?.[row.route];
  const fit = strongestStretches(segments?.routes?.[row.route], ["tight", "slack"]).slice(0, 4);
  const scale = Math.max(1, ...fit.map((s) => Math.max(finiteNumber(s.timetable_min), finiteNumber(s.typical_min))));
  box.innerHTML = `<div class="card" role="region" aria-label="Route ${route}">
    <div class="detail-head">
      <span class="badge big">${escapeHTML(number)}</span>${operatorLabel ? `<span class="small">${escapeHTML(operatorLabel)}</span>` : ""}
      <span class="verdict" style="color:${colour}">${inTen(row.on_time_pct)} on time</span>
      ${row.frequent ? '<span class="tag freq" title="High-frequency service: officially judged by waiting time, not timetable punctuality">frequent</span>' : ""}
      ${limitedEvidence(row) ? '<span class="small faint">few readings: treat with care</span>' : ""}
      <button class="close" type="button" id="close">Close</button>
    </div>
    <p class="summary">${escapeHTML(routeSummary(row, hourly))}</p>
    <h3>Likely reasons</h3>
    <div class="reasons">${reasons.length
      ? reasons.map((x) => `<div class="reason"><strong>${escapeHTML(x.title)}</strong><span>${escapeHTML(x.text)}</span></div>`).join("")
      : '<div class="reason none"><strong>No clear pattern</strong><span>Late running is spread across the day and the route, so we don\'t suggest a reason.</span></div>'}</div>
    ${byKind ? `<h3>When it's on time</h3>${hourKindControls()}${hourStripHTML(byKind, hourly, `Route ${row.route} on time by hour`, "it is")}` : ""}
    ${journeysHTML(hourly?.journeys?.routes?.[row.route], hourly?.journeys?.duty_checked)}
    ${fit.length ? `<h3>Where the timetable doesn't fit</h3><ul class="fit">${fit.map((s) => stretchHTML(s, scale, false)).join("")}</ul>
      <p class="faint small">Between timing points, last 28 days, only patterns seen in at least 3 of the 4 weeks.</p>` : ""}
    ${row.coverage_withheld ? '<p class="faint small">Journey coverage withheld: the timetable lists some of this route\'s journeys twice with identical times.</p>' : ""}
    <h3>The numbers</h3>
    ${arrivalSplitHTML(row)}
    <div class="numbers">
      <div><span class="k">On time</span><span class="v">${finiteNumber(row.on_time_pct).toFixed(1)}%</span></div>
      <div><span class="k">Typical</span><span class="v">${format.delaySeconds(row.median_delay_s)}</span></div>
      <div><span class="k">Average</span><span class="v">${format.delaySeconds(row.mean_delay_s)}</span></div>
      <div><span class="k">Readings</span><span class="v">${finiteNumber(row.readings_in_gate).toLocaleString()}</span></div>
    </div>
    <p class="faint small">Typical and average lateness are minutes and seconds late (+) or early (−).</p>
  </div>`;
  wireHourControls(box, () => { renderRouteDetail(); renderNetworkHours(); });
  $("close").addEventListener("click", closeRoute);
}

/** Early, on time, late and very late as one bar, so "6 in 10 on time" says how the rest missed. */
function arrivalSplitHTML(row) {
  const readings = finiteNumber(row.readings_in_gate);
  if (!readings) return "";
  const veryLate = row.very_late == null ? null : Math.min(finiteNumber(row.very_late), finiteNumber(row.late));
  const parts = [
    ["early", "Early", "left more than a minute before the timetable", finiteNumber(row.early)],
    ["ontime", "On time", "up to a minute early or under 6 minutes late", finiteNumber(row.on_time)],
    ["late", "Late", veryLate == null ? "6 minutes or more" : "6 to 20 minutes", finiteNumber(row.late) - (veryLate || 0)],
    ...(veryLate == null ? [] : [["verylate", "Very late", "20 minutes or more", veryLate]]),
  ];
  const pct = (n) => (100 * n) / readings;
  return `<div class="split" role="img" aria-label="${escapeHTML(parts.map(([, label, , n]) => `${label} ${Math.round(pct(n))}%`).join(", "))}">${
    parts.map(([key, label, , n]) => `<i class="${key}" style="width:${pct(n)}%" title="${escapeHTML(label)}: ${Math.round(pct(n))}%"></i>`).join("")}</div>
    <ul class="split-legend">${parts.map(([key, label, what, n]) =>
      `<li><i class="${key}"></i><strong>${Math.round(pct(n))}%</strong> ${escapeHTML(label.toLowerCase())} <span class="faint">(${escapeHTML(what)})</span></li>`).join("")}</ul>`;
}

// --- Network sections -----------------------------------------------------------------
async function renderNetworkHours() {
  const panel = $("net-hours");
  const operator = state.operator;
  const hourly = await loadHourly(operator);
  if (operator !== state.operator) return;
  if (!hourly) {
    panel.innerHTML = '<p class="faint">No time-of-day figures for this operator yet.</p>';
    return;
  }
  panel.innerHTML = hourKindControls()
    + hourStripHTML(hourly.network, hourly, `${operatorName(operator)} on time by hour`, "buses are")
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
    list.innerHTML = '<li class="faint">No stretches stand out for this operator yet.</li>';
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
    ? `Not on the map: ${finiteNumber(unknown.readings_in_gate).toLocaleString()} readings (${((100 * finiteNumber(unknown.readings_in_gate)) / finiteNumber(opData.overall.readings_in_gate)).toFixed(2)}% of all) at stops we can't place in a West of England council area, mostly where routes run just beyond its edge.`
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
      return `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${r.toFixed(1)}" fill="${verdict(ward.on_time_pct)}" fill-opacity="${limitedEvidence(ward) ? 0.35 : 0.9}" tabindex="0" data-detail="${escapeHTML(text)}"><title>${escapeHTML(text)}</title></circle>`;
    }).join("");
  }
  svg.innerHTML = shapes + labels + dots;
  svg.querySelectorAll("[data-detail]").forEach((shape) => {
    const show = () => {
      svg.querySelectorAll(".picked").forEach((s) => s.classList.remove("picked"));
      shape.classList.add("picked");
      $("map-d").textContent = shape.dataset.detail;
    };
    shape.addEventListener("click", show);
    shape.addEventListener("focus", show);
  });
}

function rankRow(row) {
  const key = escapeHTML(row.key);
  const colour = verdict(row.on_time_pct);
  const pct = finiteNumber(row.on_time_pct);
  return `<li class="${limitedEvidence(row) ? "lim" : ""}" title="${key}: ${pct.toFixed(1)}% from ${finiteNumber(row.readings_in_gate).toLocaleString()} readings"><span>${key}</span>
    <span class="mini"><i style="width:${Math.min(100, pct)}%;background:${colour}"></i><b style="left:${state.target}%"></b></span>
    <span class="pct" style="color:${colour}">${Math.round(pct)}%</span></li>`;
}

function renderWards(opData) {
  const query = $("wq").value.trim().toLowerCase();
  const wards = (opData.geography?.ward || [])
    .filter((w) => hasReadings(w) && w.key !== "Unknown" && (!query || String(w.key).toLowerCase().includes(query)));
  const pick = query
    ? wards.slice(0, 12)
    : wards.filter((w) => !limitedEvidence(w)).sort((a, b) => a.on_time_pct - b.on_time_pct).slice(0, 6);
  $("wards").innerHTML = (query ? "" : '<li class="note small faint">Least punctual wards (search for yours)</li>')
    + (pick.map(rankRow).join("") || '<li class="note small faint">No ward matches.</li>');
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
    $("e-text").textContent = "No vehicle readings for this operator yet.";
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
    : Math.round(electricOn) === Math.round(restOn)
      ? ` Electric buses were on time ${Math.round(electricOn)}% of the time, the same as the rest.`
      : ` Electric buses were on time ${Math.round(electricOn)}% of the time, the rest ${Math.round(restOn)}%.`;
  $("e-share").textContent = `${Math.round((100 * buses.electric) / allBuses)}%`;
  $("e-share").classList.toggle("none", !buses.electric);
  $("e-text").textContent = buses.electric
    ? `of the ${allBuses.toLocaleString()} buses we saw are electric (${buses.electric.toLocaleString()} bus${buses.electric === 1 ? "" : "es"}).${compare}`
    : `None of the ${allBuses.toLocaleString()} buses we saw ${allBuses === 1 ? "is" : "are"} electric.`;
  $("fleetbar").innerHTML = used.map(([key, label, colour]) =>
    `<i style="width:${(100 * buses[key]) / allBuses}%;background:${colour}" title="${label}: ${buses[key]} buses"></i>`).join("");
  $("fleetbar").setAttribute("aria-label", used.map(([key, label]) => `${label} ${buses[key]} buses`).join(", "));
  $("fleet-legend").innerHTML = used.map(([key, label, colour]) =>
    `<span><i style="background:${colour}"></i>${label}: ${buses[key]} (${Math.round((100 * buses[key]) / allBuses)}%)</span>`).join("");

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
  $("fleet-more").textContent = state.fleetAll ? "Show fewer" : `Show all ${fleet.length} models`;
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
    finiteNumber(data.current_target_pct,
      finiteNumber(data.target_pct, DEFAULT_TARGET_PCT))));
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
      $("map-d").textContent = "Tap a place for its figures.";
      renderMap(currentOpData());
    });
  });
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
