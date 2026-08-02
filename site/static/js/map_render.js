/** Render map icons and popups for vehicles and depots. */
import { el } from "./util.js";

const EV_COLORS = { delayed: "#D4351C", early: "#eab308",
                    waiting: "#1D70B8", punctual: "#00703C" };

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
    const outline = "var(--sign-edge, #7E8582)";
    if (hollow) {
        const common = `fill="none" stroke="${outline}" stroke-width="2.4"`;
        if (eventType === "delayed")
            return `<rect x="${c - r * 0.82}" y="${c - r * 0.82}" width="${r * 1.64}" height="${r * 1.64}" rx="1.5" ${common}/>`;
        if (eventType === "early")
            return `<rect x="${c - r * 0.78}" y="${c - r * 0.78}" width="${r * 1.56}" height="${r * 1.56}" rx="1.5" ${common} transform="rotate(45 ${c} ${c})"/>`;
        return `<circle cx="${c}" cy="${c}" r="${r}" ${common}/>`;
    }
    const edge = `stroke="${outline}" stroke-width="1.2"`;
    // Each state has a distinct marker shape as well as a colour.
    if (eventType === "delayed")   // square
        return `<rect x="${c - r * 0.82}" y="${c - r * 0.82}" width="${r * 1.64}" height="${r * 1.64}" rx="1.5" fill="${EV_COLORS.delayed}" ${edge}/>`;
    if (eventType === "early")     // diamond
        return `<rect x="${c - r * 0.78}" y="${c - r * 0.78}" width="${r * 1.56}" height="${r * 1.56}" rx="1.5" fill="${EV_COLORS.early}" ${edge} transform="rotate(45 ${c} ${c})"/>`;
    if (eventType === "waiting")   // hollow circle
        return `<circle cx="${c}" cy="${c}" r="${r}" fill="${EV_COLORS.waiting}" ${edge}/>`
             + `<circle cx="${c}" cy="${c}" r="${r * 0.45}" fill="none" stroke="#fff" stroke-width="1.5" opacity="0.9"/>`;
    return `<circle cx="${c}" cy="${c}" r="${r}" fill="${EV_COLORS.punctual}" ${edge}/>`;
}

/** Small, shape-based marker showing that the bot posted about this journey. */
export function featuredPostBadge(isFeatured) {
    if (!isFeatured) return "";
    return `<g class="busbot-post-badge" aria-hidden="true">
        <path d="M24.5 2.5h7.2a2.3 2.3 0 0 1 2.3 2.3v4.5a2.3 2.3 0 0 1-2.3 2.3h-2.8l-3.7 2.7.8-2.7h-1.5a2.3 2.3 0 0 1-2.3-2.3V4.8a2.3 2.3 0 0 1 2.3-2.3Z"
              fill="#F59E0B" stroke="#0D0F11" stroke-width="1.4"/>
        <circle cx="26.8" cy="7" r="1" fill="#0D0F11"/>
        <circle cx="30.4" cy="7" r="1" fill="#0D0F11"/>
    </g>`;
}

export function busIcon(bus, isFeatured, options = {}) {
    const eventType = String(bus.waitingAtOrigin ? "waiting" : bus.eventType);
    const hollow = Boolean(options.hollow);
    const ring = hollow
        ? "var(--sign-edge, #7E8582)"
        : (options.ringColor || liveryColor(bus.livery) || "#7E8582");
    const emphasized = Boolean(isFeatured || options.emphasized);
    const size = emphasized ? 36 : 28;
    const c = size / 2;
    const coreR = emphasized ? 10 : 8;
    const bearing = Number.isFinite(Number(bus.bearing)) ? Number(bus.bearing) : null;
    const chevH = emphasized ? 5.5 : 4.5, chevW = emphasized ? 5 : 4;
    const pointer = bearing !== null
        ? `<g transform="rotate(${bearing} ${c} ${c})">
             <path d="M${c - chevW} ${c + chevH * 0.3} L${c} ${c - chevH} L${c + chevW} ${c + chevH * 0.3}"
                   fill="none" stroke="${hollow ? "var(--sign-edge, #7E8582)" : "#fff"}" stroke-width="2.5" stroke-linecap="square"/></g>`
        : (eventType === "waiting" ? ""
            : `<circle cx="${c}" cy="${c}" r="2.5" fill="${hollow ? "var(--sign-edge, #7E8582)" : "#fff"}"/>`);
    const svg = `
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}"
             ${isFeatured ? 'role="img" aria-label="Bot posted about this journey"' : 'aria-hidden="true"'}>
            ${isFeatured ? "<title>Bot posted about this journey</title>" : ""}
            <circle cx="${c}" cy="${c}" r="${coreR + 3}" fill="none" stroke="${ring}" stroke-width="3"/>
            ${corePath(eventType, c, coreR, hollow)}
            ${pointer}
            ${featuredPostBadge(isFeatured)}
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
    const ring = hollow
        ? "var(--sign-edge, #7E8582)"
        : (liveryColor(livery) || "#7E8582");
    const innerFill = hollow ? "none" : "#7E8582";
    const centreFill = hollow ? "var(--sign-edge, #7E8582)" : "#495049";
    return L.divIcon({
        html: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 22 22" width="22" height="22">
                 <circle cx="11" cy="11" r="9" fill="none" stroke="${ring}" stroke-width="2"/>
                 <circle cx="11" cy="11" r="5.5" fill="${innerFill}" stroke="var(--sign-edge, #7E8582)" stroke-width="1"/>
                 <circle cx="11" cy="11" r="2" fill="${centreFill}"/></svg>`,
        className: `bus-marker depot${hollow ? " filtered-out" : ""}`,
        iconSize: [22, 22],
        iconAnchor: [11, 11], popupAnchor: [0, -11] });
}

function tooltipStatus(bus) {
    if (bus.eventType === "depot")
        return { text: "at depot", cls: "vs-status-off" };
    if (bus.waitingAtOrigin || bus.eventType === "waiting")
        return { text: "waiting to depart", cls: "vs-status-waiting" };
    const delay = Number.parseInt(bus.delayMinutes, 10) || 0;
    if (delay >= 4) return { text: `${delay}m late`, cls: "vs-status-late" };
    if (delay <= -3) return { text: `${Math.abs(delay)}m early`, cls: "vs-status-early" };
    return { text: "on time", cls: "vs-status-ontime" };
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
