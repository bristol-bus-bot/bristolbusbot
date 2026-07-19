/** Render the full vehicle card in the detail modal. */
import { el, replaceContent } from "./util.js";
import { liveryColor } from "./map_render.js";
import { vehicleCard } from "./vehicle_card.js";

function badgeData(v) {
    const out = [];
    const fuel = (v.fuel || "").toLowerCase();
    if (fuel === "electric") out.push({ text: "ELECTRIC", cls: "fb-electric" });
    else if (fuel === "gas" || fuel === "biogas") out.push({ text: "BIOGAS", cls: "fb-gas" });
    else if (fuel === "hybrid") out.push({ text: "HYBRID", cls: "fb-electric" });
    else if (fuel) out.push({ text: fuel.toUpperCase() });
    if (v.double_decker) out.push({ text: "DOUBLE-DECKER" });
    if (v.coach) out.push({ text: "COACH" });
    (Array.isArray(v.special_features) ? v.special_features : [])
        .forEach(f => out.push({ text: String(f) }));
    return out;
}

export function renderVehiclePanel(host, v, isLive, activeBus, description) {
    const actions = [isLive
        ? el("button", { class: "vc-action vc-action-primary",
                         onClick: () => window.trackVehicleOnMap(v.id) },
             ["→ Track on map"])
        : el("div", { class: "vc-offduty" },
             ["Not currently running — can't track on map"])];
    if (v.profile_url)
        actions.push(el("a", { class: "vc-action", href: v.profile_url },
                        ["View measured vehicle profile"]));

    const card = vehicleCard({
        line: activeBus && activeBus.line,
        destination: activeBus && activeBus.destination,
        fleetCode: v.fleet_code || (v.fleet_number != null ? String(v.fleet_number) : "?"),
        reg: v.reg,
        previousReg: v.previous_reg,
        model: v.model,
        liveryLeft: v.livery_left,
        liveryName: v.livery_name,
        operatorName: v.operator_name,
        garageName: v.garage_name
            ? v.garage_name + (v.garage_code ? ` (${v.garage_code})` : "") : null,
        namedBus: v.name,
        notes: v.notes,
        blurb: description,
        badges: badgeData(v),
        accent: liveryColor({ left: v.livery_left }) || "#7E8582",
        withdrawn: !!v.withdrawn,
        status: (isLive && activeBus) ? {
            eventType: activeBus.eventType,
            waiting: activeBus.waitingAtOrigin,
            delayMinutes: activeBus.delayMinutes,
            lastStopName: activeBus.lastStopName,
        } : null,
        actions,
    }, "full");
    replaceContent(host, card);
}

window.BBB = window.BBB || {};
window.BBB.renderVehiclePanel = renderVehiclePanel;
