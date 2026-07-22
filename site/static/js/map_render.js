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

function corePath(eventType, c, r) {
    // Each state has a distinct marker shape as well as a colour.
    if (eventType === "delayed")   // square
        return `<rect x="${c - r * 0.82}" y="${c - r * 0.82}" width="${r * 1.64}" height="${r * 1.64}" rx="1.5" fill="${EV_COLORS.delayed}"/>`;
    if (eventType === "early")     // diamond
        return `<rect x="${c - r * 0.78}" y="${c - r * 0.78}" width="${r * 1.56}" height="${r * 1.56}" rx="1.5" fill="${EV_COLORS.early}" transform="rotate(45 ${c} ${c})"/>`;
    if (eventType === "waiting")   // hollow circle
        return `<circle cx="${c}" cy="${c}" r="${r}" fill="${EV_COLORS.waiting}"/>`
             + `<circle cx="${c}" cy="${c}" r="${r * 0.45}" fill="none" stroke="#fff" stroke-width="1.5" opacity="0.9"/>`;
    return `<circle cx="${c}" cy="${c}" r="${r}" fill="${EV_COLORS.punctual}"/>`;
}

export function busIcon(bus, isFeatured) {
    const eventType = String(bus.waitingAtOrigin ? "waiting" : bus.eventType);
    const ring = liveryColor(bus.livery) || "#7E8582";
    const size = isFeatured ? 36 : 28;
    const c = size / 2;
    const coreR = isFeatured ? 10 : 8;
    const bearing = Number.isFinite(Number(bus.bearing)) ? Number(bus.bearing) : null;
    const chevH = isFeatured ? 5.5 : 4.5, chevW = isFeatured ? 5 : 4;
    const pointer = bearing !== null
        ? `<g transform="rotate(${bearing} ${c} ${c})">
             <path d="M${c - chevW} ${c + chevH * 0.3} L${c} ${c - chevH} L${c + chevW} ${c + chevH * 0.3}"
                   fill="none" stroke="#fff" stroke-width="2.5" stroke-linecap="square"/></g>`
        : (eventType === "waiting" ? "" : `<circle cx="${c}" cy="${c}" r="2.5" fill="#fff"/>`);
    const svg = `
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}">
            <circle cx="${c}" cy="${c}" r="${coreR + 3}" fill="none" stroke="${ring}" stroke-width="3"/>
            <circle cx="${c}" cy="${c + 0.8}" r="${coreR}" fill="#000" opacity="0.3"/>
            ${corePath(eventType, c, coreR)}
            ${pointer}
        </svg>`;
    return L.divIcon({ html: svg,
                       className: isFeatured ? "bus-marker featured" : "bus-marker",
                       iconSize: [size, size], iconAnchor: [c, c],
                       popupAnchor: [0, -c] });
}

export function depotIcon(livery) {
    const ring = liveryColor(livery) || "#7E8582";
    return L.divIcon({
        html: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 22 22" width="22" height="22">
                 <circle cx="11" cy="11" r="9" fill="none" stroke="${ring}" stroke-width="2" opacity="0.4"/>
                 <circle cx="11" cy="11" r="5.5" fill="#7E8582" opacity="0.6"/>
                 <circle cx="11" cy="11" r="2" fill="#555"/></svg>`,
        className: "bus-marker depot", iconSize: [22, 22],
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

export function busPopup(bus) {
    const status = tooltipStatus(bus);
    const livery = el("div", {
        class: "bt-livery",
        title: bus.livery?.name || "Vehicle livery",
    });
    livery.style.background = bus.livery?.left || "#7E8582";
    const destination = bus.eventType === "depot"
        ? (bus.depotName || "At depot") : (bus.destination || "Unknown destination");
    const identity = [bus.reg, bus.fleetNumber ? `fleet ${bus.fleetNumber}` : null]
        .filter(Boolean).join(" / ");
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
            identity ? el("div", { class: "bt-vehicle" }, [identity]) : null,
            el("button", {
                class: "bt-details",
                onClick: () => window.openVehicleSidebar(
                    bus.vehicleRef, bus.operatorRef),
            }, ["Journey, vehicle and history"]),
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
               ["VIEW DEPARTURES"]),
        ]),
    ]);
}

window.BBB = window.BBB || {};
Object.assign(window.BBB, { busIcon, depotIcon, busPopup, stopPopup });
