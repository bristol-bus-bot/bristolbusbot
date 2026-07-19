const LOW_SAMPLE_THRESHOLD = 30;
const DEFAULT_TARGET_PCT = 95;

const state = {
  data: null,
  day: null,
  operator: null,
  rows: [],
  target: DEFAULT_TARGET_PCT,
  tableSortKey: "on_time_pct",
  tableSortType: "num",
  tableSortAscending: false,
  chartSort: "worst",
  hideLowSample: false,
  selectedRoute: null,
  geoView: "area",
  geoSort: "reads",
};

const TINTS = {
  "var(--good)": "rgba(63,185,80,0.12)",
  "var(--warn)": "rgba(210,153,34,0.12)",
  "var(--bad)": "rgba(248,81,73,0.12)",
  "var(--fg-3)": "rgba(107,119,131,0.12)",
};

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

function motionOK() {
  return !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
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
    const year = yyyymmdd.slice(0, 4);
    const month = yyyymmdd.slice(4, 6);
    const day = yyyymmdd.slice(6, 8);
    return new Date(`${year}-${month}-${day}T00:00:00`).toLocaleDateString("en-GB", {
      weekday: "long",
      day: "numeric",
      month: "long",
      year: "numeric",
    });
  },
  percent(value) {
    return value === null || value === undefined
      ? "–"
      : finiteNumber(value).toFixed(1) + "%";
  },
};

function verdict(percent, target) {
  if (percent === null || percent === undefined) return "var(--fg-3)";
  if (percent >= target) return "var(--good)";
  if (percent >= target - 25) return "var(--warn)";
  return "var(--bad)";
}

function tint(colour) {
  return TINTS[colour] || "rgba(255,255,255,0.06)";
}

function routePill(route, percent, target) {
  const colour = verdict(percent, target);
  const fill = tint(colour);
  return `<span class="route-pill" style="color:${colour};background:${fill};border-color:${fill}">${escapeHTML(route)}</span>`;
}

function hasReadings(row) {
  return row.on_time_pct !== null && row.on_time_pct !== undefined;
}

function operatorName(code) {
  const meta = (state.data.operators || []).find((o) => o.code === code);
  return meta ? meta.name : code;
}

function gridRows() {
  let rows = [...state.rows];
  if (state.hideLowSample) {
    rows = rows.filter((row) => (row.readings_in_gate || 0) >= LOW_SAMPLE_THRESHOLD);
  }
  const key = state.chartSort;
  if (key === "route") {
    rows.sort((a, b) => String(a.route).localeCompare(String(b.route), undefined, { numeric: true }));
    return rows;
  }
  rows.sort((a, b) => {
    const av = hasReadings(a) ? a.on_time_pct : (key === "worst" ? Infinity : -Infinity);
    const bv = hasReadings(b) ? b.on_time_pct : (key === "worst" ? Infinity : -Infinity);
    return key === "worst" ? av - bv : bv - av;
  });
  return rows;
}

function renderRouteGrid() {
  const grid = document.getElementById("route-grid");
  if (!grid) return;
  const rows = gridRows();
  if (!rows.length) {
    grid.innerHTML = '<p class="faint audit-small" style="margin:6px 0">No routes to show yet.</p>';
    renderRouteDetail();
    return;
  }
  const target = state.target;
  grid.innerHTML = rows
    .map((row) => {
      const colour = verdict(row.on_time_pct, target);
      const low = (row.readings_in_gate || 0) < LOW_SAMPLE_THRESHOLD;
      const pct = hasReadings(row) ? finiteNumber(row.on_time_pct).toFixed(0) + "%" : "–";
      const sel = state.selectedRoute !== null && String(row.route) === String(state.selectedRoute) ? " selected" : "";
      const freq = row.frequent ? '<span class="cell-freq">freq</span>' : "";
      const route = escapeHTML(row.route);
      return `<button type="button" class="route-cell${low ? " lown" : ""}${sel}" data-route="${route}"
        aria-pressed="${sel ? "true" : "false"}"
        style="color:${colour};background:${tint(colour)};border-color:${tint(colour)}">
        <span class="cell-route">${route}</span>
        <span class="cell-pct">${pct}</span>${freq}
      </button>`;
    })
    .join("");
  grid.querySelectorAll(".route-cell").forEach((cell) => {
    cell.addEventListener("click", () => selectRoute(cell.dataset.route));
  });
  renderRouteDetail();
}

function selectRoute(route) {
  state.selectedRoute = String(state.selectedRoute) === String(route) ? null : route;
  document.querySelectorAll("#route-grid .route-cell").forEach((cell) => {
    const selected = state.selectedRoute !== null
      && cell.dataset.route === String(state.selectedRoute);
    cell.classList.toggle("selected", selected);
    cell.setAttribute("aria-pressed", selected ? "true" : "false");
  });
  renderRouteDetail();
}

function renderRouteDetail() {
  const panel = document.getElementById("route-detail");
  if (!panel) return;
  if (state.selectedRoute === null) {
    panel.className = "route-detail";
    panel.innerHTML = '<p class="faint audit-small" style="margin:0">Tap a route above to see its full breakdown.</p>';
    return;
  }
  const row = state.rows.find((r) => String(r.route) === String(state.selectedRoute));
  if (!row) {
    panel.className = "route-detail";
    panel.innerHTML = "";
    return;
  }
  const colour = verdict(row.on_time_pct, state.target);
  const low = (row.readings_in_gate || 0) < LOW_SAMPLE_THRESHOLD;
  const freqNote = row.frequent
    ? '<span class="freq-tag" title="High-frequency service: officially judged by wait time, not timetable punctuality">frequent</span>'
    : "";
  const lowNote = low
    ? `<span class="lown">n=${finiteNumber(row.readings_in_gate)}${finiteNumber(row.readings_in_gate) === 0 ? ", no readings" : ", indicative"}</span>`
    : "";
  panel.className = "route-detail open";
  panel.innerHTML = `
    <div class="rd-head">
      <span class="route-pill" style="color:${colour};background:${tint(colour)};border-color:${tint(colour)}">${escapeHTML(row.route)}</span>
      <span class="badge" style="color:${colour};background:${tint(colour)}">${format.percent(row.on_time_pct)} on-time</span>
      ${freqNote}${lowNote}
    </div>
    <div class="rd-stats">
      <div class="rd-stat"><span class="k">Median</span><span class="v">${format.delaySeconds(row.median_delay_s)}</span></div>
      <div class="rd-stat"><span class="k">Mean</span><span class="v">${format.delaySeconds(row.mean_delay_s)}</span></div>
      <div class="rd-stat"><span class="k">Readings</span><span class="v">${finiteNumber(row.readings_in_gate).toLocaleString()}</span></div>
      <div class="rd-stat"><span class="k">Early</span><span class="v">${finiteNumber(row.early).toLocaleString()}</span></div>
      <div class="rd-stat"><span class="k">On time</span><span class="v">${finiteNumber(row.on_time).toLocaleString()}</span></div>
      <div class="rd-stat"><span class="k">Late</span><span class="v">${finiteNumber(row.late).toLocaleString()}</span></div>
    </div>`;
}

function drawTargetLine(container, target) {
  const firstTrack = container.querySelector(".bar-track");
  if (!firstTrack) return;
  const containerBox = container.getBoundingClientRect();
  const trackBox = firstTrack.getBoundingClientRect();
  const offsetX = trackBox.left - containerBox.left + trackBox.width * (target / 100);
  const line = document.createElement("div");
  line.className = "tgtline";
  line.style.left = offsetX + "px";
  const label = document.createElement("span");
  label.textContent = `${finiteNumber(target)}% target`;
  line.appendChild(label);
  container.appendChild(line);
}

function wireChartControls() {
  document.querySelectorAll(".ctl").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".ctl").forEach((other) => other.classList.remove("active"));
      button.classList.add("active");
      document.querySelectorAll(".ctl").forEach((other) =>
        other.setAttribute("aria-pressed", other === button ? "true" : "false"));
      state.chartSort = button.dataset.sort;
      renderRouteGrid();
    });
  });
  document.getElementById("hideLowN").addEventListener("change", (event) => {
    state.hideLowSample = event.target.checked;
    renderRouteGrid();
  });
}

function wireSections() {
  document.querySelectorAll(".sec").forEach((section) => {
    const head = section.querySelector(".sec-head");
    const body = section.querySelector(".sec-body");
    head.addEventListener("click", () => {
      section.classList.toggle("collapsed");
      const open = !section.classList.contains("collapsed");
      head.setAttribute("aria-expanded", open ? "true" : "false");
      animateSection(body, open);
    });
  });
}

function animateSection(body, open) {
  if (!motionOK()) {
    body.style.height = open ? "auto" : "0px";
    return;
  }
  const from = body.getBoundingClientRect().height;
  body.style.height = open ? "auto" : "0px";
  const to = body.getBoundingClientRect().height;
  if (from === to) return;
  body.animate(
    [{ height: from + "px" }, { height: to + "px" }],
    { duration: 340, easing: "cubic-bezier(.4,0,.2,1)" }
  );
}

function countUp(element, target, decimals) {
  if (!motionOK()) {
    element.textContent = target.toFixed(decimals);
    return;
  }
  const duration = 650;
  let start;
  function step(now) {
    if (start === undefined) start = now;
    const progress = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    element.textContent = (target * eased).toFixed(decimals);
    if (progress < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function showError(message) {
  const box = document.getElementById("err");
  box.textContent = message;
  box.style.display = "block";
}

function renderOperatorTabs() {
  const container = document.getElementById("operator-tabs");
  const present = (state.data.operators || []).filter((o) => state.day.by_operator[o.code]);
  container.innerHTML = present
    .map((o) => {
      const active = o.code === state.operator;
      return `<button type="button" role="tab" aria-selected="${active ? "true" : "false"}" class="op-tab${active ? " active" : ""}" data-op="${escapeHTML(o.code)}">${escapeHTML(o.name)}</button>`;
    })
    .join("");
  container.querySelectorAll(".op-tab").forEach((btn) => {
    btn.addEventListener("click", () => selectOperator(btn.dataset.op));
  });
}

function selectOperator(code) {
  state.operator = code;
  state.selectedRoute = null;
  const opData = state.day.by_operator[code] || { overall: {}, routes: [] };
  state.rows = opData.routes || [];
  document.querySelectorAll("#operator-tabs .op-tab").forEach((b) => {
    const active = b.dataset.op === code;
    b.classList.toggle("active", active);
    b.setAttribute("aria-selected", active ? "true" : "false");
  });
  renderHeadline(opData);
  renderRouteGrid();
  renderGeography(opData);
  renderFleet(opData);
}

function renderHeadline(opData) {
  const data = state.data;
  const day = state.day;
  const overall = opData.overall || {};
  const pct = overall.on_time_pct;
  const colour = verdict(pct, state.target);
  const dayCount = data.days.length;

  document.getElementById("period-line").textContent =
    operatorName(state.operator) +
    " · service day " +
    format.serviceDate(day.service_date) +
    (dayCount > 1 ? ` · ${dayCount} days collected` : " · 1 day collected");

  const pctEl = document.getElementById("ot-pct");
  if (pct === null || pct === undefined) {
    pctEl.textContent = "–";
  } else {
    countUp(pctEl, pct, 1);
  }

  const fill = document.getElementById("ot-fill");
  const finalWidth = Math.min(100, pct || 0) + "%";
  fill.style.background = colour;
  if (motionOK() && pct) {
    fill.style.width = "0%";
    requestAnimationFrame(() => requestAnimationFrame(() => (fill.style.width = finalWidth)));
  } else {
    fill.style.width = finalWidth;
  }

  document.getElementById("ot-tgt").style.left = state.target + "%";
  document.getElementById("tgt-label").textContent = "target " + state.target + "%";
  document.getElementById("ot-band").textContent = "On-time = " + (data.on_time_band || "");
  document.getElementById("median-delay").textContent = format.delaySeconds(overall.median_delay_s);
  document.getElementById("mean-delay").textContent = format.delaySeconds(overall.mean_delay_s);
  document.getElementById("readings").textContent = (overall.readings_in_gate || 0).toLocaleString();
  document.getElementById("trips").textContent = (overall.observed_trips || 0).toLocaleString();

  document.getElementById("genat").textContent = data.generated_at
    ? new Date(data.generated_at).toLocaleString("en-GB")
    : "–";
  document.getElementById("genday").textContent = format.serviceDate(day.service_date);
}

async function load() {
  let data;
  try {
    const response = await fetch("audit_data.json", { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    data = await response.json();
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
    finiteNumber(data.target_pct, DEFAULT_TARGET_PCT)));

  const day = data.days && data.days[data.days.length - 1];
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

  const present = (data.operators || []).filter((o) => day.by_operator[o.code]);
  if (!present.length) {
    showError("No operator data for this day yet.");
    return;
  }

  renderOperatorTabs();
  selectOperator(present[0].code);
}

function geoRows(opData) {
  const geo = (opData.geography && opData.geography[state.geoView]) || [];
  let rows = geo.filter((r) => r.on_time_pct !== null && r.on_time_pct !== undefined);
  const s = state.geoSort;
  rows = [...rows].sort((a, b) =>
    s === "worst" ? a.on_time_pct - b.on_time_pct
    : s === "best" ? b.on_time_pct - a.on_time_pct
    : b.readings_in_gate - a.readings_in_gate
  );
  if (state.geoView === "ward") rows = rows.slice(0, 40);
  return rows;
}

function renderGeography(opData) {
  const container = document.getElementById("geo-chart");
  if (!container) return;
  const rows = geoRows(opData);
  if (!rows.length) {
    container.innerHTML = '<p class="faint audit-small" style="margin:6px 0">No geographic readings for this operator yet.</p>';
    return;
  }
  const target = state.target;
  const grow = motionOK();
  container.innerHTML = rows
    .map((row) => {
      const colour = verdict(row.on_time_pct, target);
      const final = Math.min(100, Math.max(0, finiteNumber(row.on_time_pct)));
      const width = grow ? 0 : final;
      const key = escapeHTML(row.key);
      return `<div class="bar-row geo-row">
      <div class="rlabel geo-label" title="${key}">${key}</div>
      <div class="bar-track"><div class="bar-fill" data-w="${final}" style="width:${width}%;background:${colour}"></div></div>
      <div class="bar-meta">${finiteNumber(row.on_time_pct).toFixed(1)}% <span class="n">n=${finiteNumber(row.readings_in_gate).toLocaleString()}</span></div>
    </div>`;
    })
    .join("");
  if (grow) {
    requestAnimationFrame(() => {
      container.querySelectorAll(".bar-fill").forEach((fill) => {
        fill.style.width = fill.dataset.w + "%";
      });
    });
  }
  drawTargetLine(container, target);
}

function wireGeoControls() {
  const redraw = () => renderGeography(state.day.by_operator[state.operator] || {});
  document.querySelectorAll(".gctl").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".gctl").forEach((o) => o.classList.remove("active"));
      button.classList.add("active");
      document.querySelectorAll(".gctl").forEach((other) =>
        other.setAttribute("aria-pressed", other === button ? "true" : "false"));
      state.geoView = button.dataset.geo;
      redraw();
    });
  });
  document.querySelectorAll(".gsort").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".gsort").forEach((o) => o.classList.remove("active"));
      button.classList.add("active");
      document.querySelectorAll(".gsort").forEach((other) =>
        other.setAttribute("aria-pressed", other === button ? "true" : "false"));
      state.geoSort = button.dataset.gsort;
      redraw();
    });
  });
}

function electricDonut(electricPct) {
  electricPct = Math.min(100, Math.max(0, finiteNumber(electricPct)));
  const r = 52;
  const circ = 2 * Math.PI * r;
  const dash = (electricPct / 100) * circ;
  return `<svg viewBox="0 0 130 130" width="130" height="130" class="donut" aria-hidden="true">
    <circle cx="65" cy="65" r="${r}" fill="none" stroke="var(--surface-2)" stroke-width="16"/>
    <circle cx="65" cy="65" r="${r}" fill="none" stroke="var(--good)" stroke-width="16"
      stroke-dasharray="${dash} ${circ}" stroke-linecap="round" transform="rotate(-90 65 65)"/>
    <text x="65" y="61" text-anchor="middle" class="donut-num">${electricPct.toFixed(0)}%</text>
    <text x="65" y="80" text-anchor="middle" class="donut-sub">electric</text>
  </svg>`;
}

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

function renderFleet(opData) {
  const card = document.getElementById("electric-card");
  const list = document.getElementById("fleet-models");
  if (!card || !list) return;
  const fleet = (opData.fleet || []).filter((m) => m.readings_in_gate > 0);
  if (!fleet.length) {
    card.innerHTML = '<p class="faint audit-small">No fleet readings for this operator yet.</p>';
    list.innerHTML = "";
    return;
  }
  const totalReads = fleet.reduce((s, m) => s + finiteNumber(m.readings_in_gate), 0);
  const elecReads = fleet.filter((m) => m.electric)
    .reduce((s, m) => s + finiteNumber(m.readings_in_gate), 0);
  const totalVeh = fleet.reduce((s, m) => s + finiteNumber(m.vehicles), 0);
  const elecVeh = fleet.filter((m) => m.electric)
    .reduce((s, m) => s + finiteNumber(m.vehicles), 0);
  const elecPct = totalReads ? (100 * elecReads / totalReads) : 0;

  card.innerHTML = `
    <div class="cap">Electric vs diesel</div>
    <div class="donut-wrap">
      ${electricDonut(elecPct)}
      <div class="donut-legend">
        <div class="stat"><span class="k"><span class="dot dot-good"></span>Electric</span><span class="v">${elecVeh} buses</span></div>
        <div class="stat"><span class="k"><span class="dot dot-faint"></span>Diesel / other</span><span class="v">${totalVeh - elecVeh} buses</span></div>
        <div class="stat"><span class="k">Share of readings</span><span class="v">${elecPct.toFixed(0)}% electric</span></div>
      </div>
    </div>`;

  list.innerHTML = fleet.map((m) => {
    const colour = verdict(m.on_time_pct, state.target);
    const routes = (m.routes || []).map((r) => `<span class="route-pill mini" style="color:${colour};background:${tint(colour)};border-color:${tint(colour)}">${escapeHTML(r[0])}</span>`).join("");
    const ev = m.electric ? '<span class="ev-tag">⚡ electric</span>' : "";
    return `<div class="model-row">
      <div class="model-head">
        <span class="model-name"><a class="model-link" href="${escapeHTML(wikiLink(m.model))}" target="_blank" rel="noopener noreferrer">${escapeHTML(m.model)}</a>${ev}</span>
        <span class="model-stats"><span class="badge" style="color:${colour};background:${tint(colour)}">${format.percent(m.on_time_pct)}</span> <span class="faint">${finiteNumber(m.vehicles)} buses · n=${finiteNumber(m.readings_in_gate).toLocaleString()}</span></span>
      </div>
      <div class="model-routes">${routes}</div>
    </div>`;
  }).join("");
}

wireSections();
wireChartControls();
wireGeoControls();
load();
