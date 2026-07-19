/**
 * Accessible search dropdown rendering. Matching and threshold rules live in
 * search_logic.js; this module only builds safe DOM in priority order.
 */
import { el, replaceContents } from "./util.js";

function section(label, cls) {
    return el("div", {
        class: `sr-section ${cls}`,
        role: "presentation",
    }, [label]);
}

function subsection(label) {
    return el("div", {
        class: "sr-subsection",
        role: "presentation",
    }, [label]);
}

function result(idx, highlightIndex, children, onClick, {
    extraClass = "",
    ariaLabel = "",
} = {}) {
    const activate = event => {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onClick();
        }
    };
    return el("div", {
        id: `search-result-${idx}`,
        class: ["search-result", idx === highlightIndex ? "sr-active" : null,
                extraClass || null].filter(Boolean).join(" "),
        "data-idx": String(idx),
        role: "option",
        tabindex: "0",
        "aria-selected": idx === highlightIndex ? "true" : "false",
        "aria-label": ariaLabel || null,
        onClick,
        onKeyDown: activate,
    }, children);
}

function renderRoutes(out, routeMatches, highlightIndex, handlers, startIndex) {
    let idx = startIndex;
    if (!routeMatches.length) return idx;

    const showOperator = routeMatches.length > 1;
    out.push(section("ROUTES", "sr-sec-routes"));
    for (const match of routeMatches) {
        const count = `${match.activeBuses} bus${match.activeBuses === 1 ? "" : "es"} live`;
        const subtitle = showOperator
            ? `${match.operatorName} · tap to view on map`
            : "tap to view on map";
        out.push(result(idx++, highlightIndex, [
            el("div", { class: "sr-row" }, [
                el("span", { class: "sr-badge sr-badge-route" }, [match.line]),
                el("div", { class: "sr-main" }, [
                    el("div", { class: "sr-title sr-route-title" },
                       [`Route ${match.line} — ${count}`]),
                    el("div", { class: "sr-sub" }, [subtitle]),
                ]),
            ]),
        ], () => handlers.selectSearchRoute(match.routeKey), {
            extraClass: "sr-route-promoted",
            ariaLabel: `Route ${match.line}, ${match.operatorName}, ${count}. Tap to view on map`,
        }));
    }
    return idx;
}

function renderStops(
    out, stopGroups, highlightIndex, handlers, getLocalityEmoji, startIndex,
) {
    let idx = startIndex;
    const groups = stopGroups.filter(group => group.stops.length);
    if (!groups.length) return idx;

    out.push(section("STOPS", "sr-sec-stops"));
    for (const group of groups) {
        const emoji = getLocalityEmoji(group.ward) || getLocalityEmoji(group.area);
        out.push(subsection(
            `${emoji ? `${emoji} ` : ""}${group.ward}, ${group.area}`));
        for (const stop of group.stops) {
            const routes = (stop.routes || []).slice(0, 6)
                .map(route => el("span", { class: "sr-chip" }, [route]));
            if ((stop.routes || []).length > 6)
                routes.push(el("span", { class: "sr-chip-more" },
                               [`+${stop.routes.length - 6}`]));
            out.push(result(idx++, highlightIndex, [
                el("div", { class: "sr-title" }, [
                    stop.stop_name,
                    el("span", { class: "sr-code" }, [stop.stop_code]),
                ]),
                el("div", { class: "sr-chips" }, routes),
            ], () => handlers.selectSearchStop(stop.stop_code, stop.lat, stop.lon), {
                ariaLabel: `${stop.stop_name}, stop ${stop.stop_code}`,
            }));
        }
    }
    return idx;
}

function renderBuses(out, fleetMatches, highlightIndex, handlers, startIndex) {
    let idx = startIndex;
    if (!fleetMatches.matches.length) return idx;

    const more = fleetMatches.total > fleetMatches.matches.length
        ? ` (showing ${fleetMatches.matches.length} of ${fleetMatches.total})`
        : "";
    out.push(section(`BUSES${more}`, "sr-sec-buses"));
    for (const vehicle of fleetMatches.matches) {
        const code = vehicle.fleet_code
            || (vehicle.fleet_number != null ? String(vehicle.fleet_number) : "?");
        const sub = [
            vehicle.model,
            vehicle.livery_name,
            vehicle.name ? `"${vehicle.name}"` : null,
        ].filter(Boolean).join(" · ") || (vehicle.operator_name || "");
        const nameRow = [vehicle.reg || "—"];
        if (vehicle.isLive)
            nameRow.push(el("span", { class: "sr-live" }, ["● LIVE"]));
        if (vehicle.withdrawn)
            nameRow.push(el("span", { class: "sr-withdrawn" }, ["WITHDRAWN"]));
        out.push(result(idx++, highlightIndex, [
            el("div", { class: "sr-row" }, [
                el("span", { class: "sr-badge sr-badge-bus" }, [code]),
                el("div", { class: "sr-main" }, [
                    el("div", { class: "sr-title sr-mono" }, nameRow),
                    el("div", { class: "sr-sub" }, [sub]),
                ]),
            ]),
        ], () => handlers.selectFleetVehicle(vehicle.id), {
            extraClass: vehicle.withdrawn && !vehicle.isLive ? "sr-dim" : "",
            ariaLabel: `Bus ${code}, registration ${vehicle.reg || "unknown"}${vehicle.isLive ? ", live" : ""}`,
        }));
    }
    return idx;
}

export function renderSearchResults(host, ctx) {
    const {
        fleetMatches,
        routeMatches,
        stopGroups,
        highlightIndex,
        handlers,
        getLocalityEmoji,
    } = ctx;
    const out = [];
    let idx = 0;

    idx = renderRoutes(out, routeMatches, highlightIndex, handlers, idx);
    idx = renderStops(
        out, stopGroups, highlightIndex, handlers, getLocalityEmoji, idx);
    renderBuses(out, fleetMatches, highlightIndex, handlers, idx);

    if (!out.length)
        out.push(el("div", { class: "sr-empty", role: "status" },
                    ["No stops, routes or buses found"]));
    replaceContents(host, out);
    host.style.display = "block";
}

window.BBB = window.BBB || {};
window.BBB.renderSearchResults = renderSearchResults;
