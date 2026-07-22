/** Render vehicle information in compact or full form. */
import { el } from "./util.js";

const STATUS = {
    waiting:  { label: d => "waiting to depart", cls: "st-waiting" },
    punctual: { label: d => "on time",           cls: "st-punctual" },
    early:    { label: d => `${Math.abs(d)}m early`, cls: "st-early" },
    delayed:  { label: d => `${d}m late`,        cls: "st-delayed" },
    depot:    { label: d => "at depot",          cls: "st-depot" },
};

export function statusChip(eventType, waiting, delayMinutes) {
    const s = waiting ? STATUS.waiting : (STATUS[eventType] || STATUS.punctual);
    return el("span", { class: `vc-status ${s.cls}` },
              [s.label(parseInt(delayMinutes) || 0)]);
}

export function plate(reg, size) {
    return el("div", { class: `uk-plate uk-plate-${size}` }, [
        el("div", { class: "uk-plate-flag" },
           [el("span", { class: "uk-plate-flag-mark" }, ["●"]), "UK"]),
        el("div", { class: "uk-plate-text" }, [reg]),
    ]);
}

/**
 * data: { line, destination, fleetCode, reg, previousReg, model, liveryLeft,
 *   liveryName, branding, operatorName, garageName, namedBus, notes, blurb,
 *   badges: [{text, cls}], accent, withdrawn, isDepot, depotName,
 *   status: {eventType, waiting, delayMinutes, lastStopName} | null,
 *   actions: [HTMLElement] }
 */
export function vehicleCard(data, mode) {
    const full = mode !== "compact";
    const embedded = mode === "embedded";

    // header: livery band = operator identity (kept saturated on purpose —
    // the one non-flat element, same in both sizes)
    const headKids = [];
    if (full) {
        headKids.push(el("span", { class: "vc-fleet" }, [`#${data.fleetCode}`]));
        if (data.withdrawn)
            headKids.push(el("span", { class: "vc-status st-withdrawn" }, ["WITHDRAWN"]));
        else if (data.status)
            headKids.push(el("span", { class: "vc-status st-live" }, ["● LIVE"]));
        else
            headKids.push(el("span", { class: "vc-status st-off" }, ["OFF DUTY"]));
        if (data.line)
            headKids.push(el("span", { class: "vc-head-route" },
                [`${data.line}${data.destination ? " → " + data.destination : ""}`]));
    } else if (data.isDepot) {
        if (data.line) headKids.push(el("span", { class: "vc-line vc-line-sm" }, [data.line]));
        headKids.push(el("span", { class: "vc-depot-name" }, [data.depotName || "Depot"]));
    } else {
        headKids.push(el("span", { class: "vc-line" }, [data.line]));
        headKids.push(el("span", { class: "vc-dest" }, [`→ ${data.destination}`]));
    }
    const header = el("div", { class: "vc-head" }, headKids);
    header.style.background = data.liveryLeft || "#2a2a2a";
    if (data.isDepot || data.withdrawn || (full && !data.status))
        header.style.opacity = "0.6";

    const body = [];
    // 1. plate — SAME element both sizes (md compact / lg full)
    if (data.reg)
        body.push(el("div", { class: "vc-plate-row" },
                     [plate(data.reg, full ? "lg" : "md")]));
    // 2. named bus (full only)
    if (full && data.namedBus)
        body.push(el("div", { class: "vc-named" }, [
            el("div", { class: "vc-named-label" }, ["Named bus"]),
            el("div", { class: "vc-named-name" }, [`"${data.namedBus}"`])]));
    // 3. status row
    if (data.status && !data.isDepot) {
        const kids = [statusChip(data.status.eventType, data.status.waiting,
                                 data.status.delayMinutes)];
        if (data.status.lastStopName)
            kids.push(el("span", { class: "vc-at" },
                         [`at ${data.status.lastStopName}`]));
        body.push(el("div", { class: "vc-status-row" }, kids));
    }
    // 4. identity block
    const idKids = [];
    if (data.model) idKids.push(el("div", { class: "vc-model" }, [data.model]));
    idKids.push(el("div", { class: "vc-code" }, [
        [full ? `#${data.fleetCode}` : (data.reg || data.fleetCode),
         !full && data.fleetCode ? ` · #${data.fleetCode}` : "",
         full && data.previousReg ? ` · prev. ${data.previousReg}` : ""].join("")]));
    if (full && data.operatorName)
        idKids.push(el("div", { class: "vc-meta" }, [data.operatorName]));
    if (data.liveryName)
        idKids.push(el("div", { class: "vc-meta" },
            [data.liveryName + (data.branding ? " · " + data.branding : "")
             + (full ? " livery" : "")]));
    if (full && data.garageName)
        idKids.push(el("div", { class: "vc-meta" }, [`Garage: ${data.garageName}`]));
    const idBlock = el("div", { class: "vc-identity" }, idKids);
    if (data.accent) idBlock.style.borderLeftColor = data.accent;
    body.push(idBlock);
    // 5. blurb
    if (data.blurb) body.push(el("div", { class: "vc-quote" }, [data.blurb]));
    // 6. badges
    if (data.badges && data.badges.length)
        body.push(el("div", { class: "vc-badges" },
            data.badges.map(b => el("span",
                { class: `vc-badge ${b.cls || ""}` }, [b.text]))));
    // 7. notes (full only)
    if (full && data.notes)
        body.push(el("div", { class: "vc-notes" }, [
            el("span", { class: "vc-notes-label" }, ["Notes"]), data.notes]));
    // 8. actions
    (data.actions || []).forEach(a => body.push(a));
    // 9. Flickr reg-plate search — bus spotters photograph most of the
    // fleet; linking out (not embedding) credits photographers properly
    if (data.reg)
        body.push(el("a", {
            class: "vc-action vc-action-flickr",
            href: "https://www.flickr.com/search/?text="
                  + encodeURIComponent(data.reg),
            target: "_blank", rel: "noopener",
        }, ["\u{1F4F7} Photos of this bus on Flickr \u2197"]));

    const children = [];
    if (!embedded) children.push(header);
    children.push(el("div", { class: "vc-body" }, body));
    return el("div", { class: `vc vc-${mode}` }, children);
}

window.BBB = window.BBB || {};
Object.assign(window.BBB, { vehicleCard, statusChip });
