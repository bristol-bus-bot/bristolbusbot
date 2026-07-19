/** Render map icons and popups for vehicles and depots. */
import { el } from "./util.js";
import { vehicleCard } from "./vehicle_card.js";

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

function badgeData(bus) {
    const out = [];
    if (bus.isElectric) out.push({ text: "ELECTRIC", cls: "fb-electric" });
    else if (bus.fuel === "gas") out.push({ text: "BIOGAS", cls: "fb-gas" });
    if (bus.isDoubleDecker) out.push({ text: "DOUBLE-DECKER" });
    if (bus.isCoach) out.push({ text: "COACH" });
    (bus.specialFeatures || []).forEach(f => out.push({ text: String(f) }));
    return out;
}

export function busPopup(bus, featuredPost) {
    // One vehicle card, compact size — identical structure to the modal.
    const isDepot = bus.eventType === "depot";
    const actions = [];
    if (bus.profileUrl)
        actions.push(el("a", { class: "vc-action", href: bus.profileUrl },
                        ["View vehicle profile"]));
    if (!isDepot && featuredPost && featuredPost.postUrl)
        actions.push(el("a", { class: "vc-action vc-action-bsky",
                              href: featuredPost.postUrl },
                        ["Featured on @bristolbusbot.live"]));
    // hasSchedule = the collector matched this journey THIS poll cycle;
    // without it the journey ref may be hours old — showing a route or an
    // "on time" pill from it presents yesterday's timetable as live data
    if (!isDepot && bus.journeyCode && bus.hasSchedule)
        actions.push(el("button", {
            class: "vc-action vc-action-blue",
            onClick: (ev) => {
                window.showBusRoute(bus.vehicleRef, bus.line, bus.directionId,
                    bus.journeyCode, bus.destination,
                    liveryColor(bus.livery) || "#666",
                    bus.directionRef || "", bus.originAimedDep || "",
                    bus.delayMinutes, bus.eventType, bus.operatorRef || "",
                    bus.tripId || "");
                const close = ev.target.closest(".leaflet-popup")
                    ?.querySelector(".leaflet-popup-close-button");
                if (close) close.click();
            },
        }, ["Show Route"]));

    return vehicleCard({
        line: bus.line,
        destination: bus.destination,
        fleetCode: bus.fleetNumber || "",
        reg: bus.reg,
        model: bus.model,
        liveryLeft: bus.livery && bus.livery.left,
        liveryName: bus.livery && bus.livery.name,
        branding: bus.branding,
        blurb: bus.description,
        badges: badgeData(bus),
        accent: liveryColor(bus.livery) || "#666",
        isDepot,
        depotName: bus.depotName,
        status: (isDepot || !bus.hasSchedule) ? null : {
            eventType: bus.eventType,
            waiting: bus.waitingAtOrigin,
            delayMinutes: bus.delayMinutes,
            lastStopName: bus.lastStopName,
        },
        actions,
    }, "compact");
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
