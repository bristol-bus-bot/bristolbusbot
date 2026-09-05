/** Render map icons and popups for vehicles and depots. */
import { el } from "./util.js";
import { busStatus } from "./status_filter.js";
import { statusPresentation } from "./vehicle_sidebar_logic.js";

const EV_COLORS = {
    delayed: "var(--marker-status-late, #D4351C)",
    early: "var(--marker-status-early, #F59E0B)",
    waiting: "var(--marker-status-waiting, #1D70B8)",
    punctual: "var(--marker-status-ontime, #00703C)",
    unknown: "var(--marker-muted-ring, #8B93A0)",
};
const MARKER_INK = "var(--marker-ink, #14181D)";
const MARKER_GAP = "var(--marker-gap, #FFFFFF)";
const MARKER_MUTED = "var(--marker-muted-ring, #8B93A0)";
const WAITING_BG = "var(--marker-waiting-bg, #E9E7E1)";

const HEX = /^#[0-9a-fA-F]{3,8}$/;

/** First plausible colour in a livery gradient string, or null. */
export function liveryColor(livery) {
    const left = livery && livery.left;
    if (!left) return null;
    if (HEX.test(left.trim())) return left.trim();
    const m = String(left).match(/#[0-9a-fA-F]{3,8}/);
    return m ? m[0] : null;
}

function corePath(eventType, c, r, hollow = false) {
    const type = eventType in EV_COLORS ? eventType : "unknown";
    const color = EV_COLORS[type];
    const edge = MARKER_INK;
    if (hollow) {
        const common = `fill="${MARKER_GAP}" stroke="${color}" stroke-width="2.5"`;
        if (type === "delayed")
            return `<rect data-marker-core="delayed" x="${c - r}" y="${c - r}" width="${r * 2}" height="${r * 2}" rx="2" ${common}/>`;
        if (type === "early")
            return `<path data-marker-core="early" d="M ${c} ${c - r - 1} L ${c + r + 1} ${c + r} L ${c - r - 1} ${c + r} Z" ${common} stroke-linejoin="round"/>`;
        if (type === "waiting")
            return `<circle data-marker-core="waiting" cx="${c}" cy="${c}" r="${r}" fill="${MARKER_GAP}" stroke="${color}" stroke-width="3.5"/>`;
        return `<circle data-marker-core="${type}" cx="${c}" cy="${c}" r="${r}" ${common}/>`;
    }
    const common = `fill="${color}" stroke="${edge}" stroke-width="1.3"`;
    if (type === "delayed")
        return `<rect data-marker-core="delayed" x="${c - r}" y="${c - r}" width="${r * 2}" height="${r * 2}" rx="2" ${common}/>`;
    if (type === "early")
        return `<path data-marker-core="early" d="M ${c} ${c - r - 1} L ${c + r + 1} ${c + r} L ${c - r - 1} ${c + r} Z" ${common} stroke-linejoin="round"/>`;
    if (type === "waiting")
        return `<circle data-marker-core="waiting" cx="${c}" cy="${c}" r="${r}" fill="${MARKER_GAP}" stroke="${color}" stroke-width="3.5"/>`;
    return `<circle data-marker-core="${type}" cx="${c}" cy="${c}" r="${r}" ${common}/>`;
}

/** Small corner tag showing that the bot posted about this journey. */
export function featuredPostBadge(isFeatured, c = 22, ringR = 14) {
    if (!isFeatured) return "";
    const x = c + ringR - 5;
    const y = c - ringR - 3;
    return `<g class="busbot-post-badge" aria-hidden="true">
        <path d="M ${x} ${y} h 9 a 2 2 0 0 1 2 2 v 7 a 2 2 0 0 1 -2 2 h -3 l -3 3 .7 -3 h -3.7 a 2 2 0 0 1 -2 -2 v -7 a 2 2 0 0 1 2 -2 Z"
              fill="#FFDD00" stroke="${MARKER_INK}" stroke-width="1.5"/>
    </g>`;
}

function markerAriaLabel(bus, isFeatured) {
    const parts = [];
    if (bus.line) parts.push(`Route ${bus.line}`);
    if (bus.destination) parts.push(`to ${bus.destination}`);
    parts.push(tooltipStatus(bus).text);
    if (Number.isFinite(Number(bus.bearing)))
        parts.push(`heading ${Math.round(Number(bus.bearing))} degrees`);
    if (isFeatured) parts.push("bot posted about this journey");
    return parts.join(", ");
}

function escapeAttribute(value) {
    return String(value).replaceAll("&", "&amp;").replaceAll('"', "&quot;")
        .replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

export function busIcon(bus, isFeatured, options = {}) {
    const eventType = busStatus(bus);
    const hollow = Boolean(options.hollow);
    const emphasized = Boolean(options.emphasized);
    const size = emphasized ? 48 : 44;
    const c = size / 2;
    const ringR = emphasized ? 17 : 16;
    const coreR = emphasized ? 9 : 7;
    const ringColor = options.ringColor || liveryColor(bus.livery) || MARKER_MUTED;
    const bearing = Number.isFinite(Number(bus.bearing)) ? Number(bus.bearing) : null;
    const waiting = eventType === "waiting";
    const ring = waiting
        ? `<circle data-marker-ring="waiting" cx="${c}" cy="${c}" r="${ringR - 5}" fill="${WAITING_BG}" stroke="${MARKER_INK}" stroke-width="1.5"/>`
        : hollow
            ? `<circle data-marker-ring="livery" cx="${c}" cy="${c}" r="${ringR}" fill="none" stroke="${MARKER_MUTED}" stroke-width="1.5"/>`
            : `<circle data-marker-ring="livery" cx="${c}" cy="${c}" r="${ringR}" fill="${ringColor}" stroke="${MARKER_INK}" stroke-width="1.5"/>
               <circle cx="${c}" cy="${c}" r="${ringR - 5}" fill="${MARKER_GAP}"/>`;
    const noseW = emphasized ? 5.5 : 4.5;
    const noseTop = c - ringR - (emphasized ? 7 : 6);
    const noseBase = c - ringR + 1;
    const pointer = bearing !== null && !waiting
        ? `<g data-marker-nose="direction" transform="rotate(${bearing} ${c} ${c})">
             <path d="M ${c} ${noseTop} L ${c + noseW} ${noseBase} L ${c - noseW} ${noseBase} Z"
                   fill="${hollow ? "none" : MARKER_INK}" stroke="${hollow ? MARKER_MUTED : MARKER_INK}" stroke-width="1.5" stroke-linejoin="round"/></g>`
        : "";
    const aria = escapeAttribute(markerAriaLabel(bus, isFeatured));
    const svg = `
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}"
             role="img" aria-label="${aria}">
            <title>${aria}</title>
            ${pointer}
            ${ring}
            ${corePath(eventType, c, waiting ? coreR - 1 : coreR, hollow)}
            ${featuredPostBadge(isFeatured, c, ringR)}
        </svg>`;
    return L.divIcon({ html: svg,
                       className: ["bus-marker", isFeatured ? "featured" : "",
                                   options.emphasized ? "focused" : ""]
                           .filter(Boolean).join(" "),
                       iconSize: [size, size], iconAnchor: [c, c],
                       popupAnchor: [0, -c] });
}

export function depotIcon(livery, options = {}) {
    const hollow = Boolean(options.hollow);
    const size = 44, c = size / 2, r = 7;
    const fill = hollow ? "none" : "var(--marker-status-depot, #6B7480)";
    const stroke = hollow ? MARKER_MUTED : MARKER_INK;
    return L.divIcon({
        html: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}"
                    role="img" aria-label="Bus at depot">
                 <title>Bus at depot</title>
                 <circle data-marker-core="depot" cx="${c}" cy="${c}" r="${r}" fill="${fill}" stroke="${stroke}" stroke-width="1.5"/></svg>`,
        className: `bus-marker depot${hollow ? " filtered-out" : ""}`,
        iconSize: [size, size],
        iconAnchor: [c, c], popupAnchor: [0, -c] });
}

function tooltipStatus(bus) {
    return statusPresentation(bus);
}

export function busPopup(bus, featuredPost = null) {
    const status = tooltipStatus(bus);
    const livery = el("div", {
        class: "bt-livery",
        title: bus.livery?.name || "Vehicle livery",
    });
    livery.style.background = bus.livery?.left || "#7E8582";
    const destination = bus.eventType === "depot"
        ? (bus.depotName || "At depot") : (bus.destination || "Unknown destination");
    return el("div", { class: "bus-tooltip" }, [
        livery,
        el("div", { class: "bt-body" }, [
            el("div", { class: "bt-route" }, [
                el("strong", { class: "bt-line" }, [bus.line || "Bus"]),
                el("span", { class: "bt-dest" }, [destination]),
            ]),
            el("div", { class: "bt-live" }, [
                el("span", { class: `vs-status ${status.cls}` }, [status.text]),
                bus.lastStopName && bus.lastStopName !== "unknown"
                    ? el("span", { class: "bt-place" }, [`at ${bus.lastStopName}`]) : null,
            ]),
            featuredPost?.postUrl ? el("a", {
                class: "bt-featured",
                href: featuredPost.postUrl,
                target: "_blank",
                rel: "noopener",
            }, ["Bot post about this journey ↗"]) : null,
            el("button", {
                class: "bt-details",
                onClick: () => window.openVehicleSidebar(
                    bus.vehicleRef, bus.operatorRef),
            }, ["Journey, vehicle and record"]),
        ]),
    ]);
}

export function stopPopup(stop, onSelect) {
    const head = [el("div", { class: "sp-name" }, [stop.common_name])];
    if (stop.street) head.push(el("div", { class: "sp-street" }, [`on ${stop.street}`]));
    const loc = [stop.enriched_locality, stop.local_authority].filter(Boolean);
    if (loc.length) head.push(el("div", { class: "sp-loc" }, [loc.join(", ")]));
    head.push(el("div", { class: "sp-code" }, [stop.stop_code]));
    return el("div", { class: "stop-popup sp" }, [
        el("div", { class: "sp-head" }, head),
        el("div", { class: "sp-body" }, [
            el("button", { class: "sp-btn", onClick: () => onSelect(stop.stop_code) },
               ["View departures"]),
        ]),
    ]);
}

window.BBB = window.BBB || {};
Object.assign(window.BBB, { busIcon, depotIcon, busPopup, stopPopup });
