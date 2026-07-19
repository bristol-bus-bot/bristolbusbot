/** Render live and scheduled departures with DOM nodes. */
import { el, replaceContent, replaceContents } from "./util.js";

function mergeDepartures(liveDeps, schedDeps) {
    // Live rows win; scheduled rows that look like
    // the same bus (same line, ETA within 5 min) are suppressed
    const merged = [];
    const used = new Set();
    liveDeps.forEach(live => {
        for (let i = 0; i < schedDeps.length; i++) {
            if (used.has(i)) continue;
            if (schedDeps[i].line === live.line
                && Math.abs(schedDeps[i].eta_mins - live.eta_mins) <= 5) {
                used.add(i);
                break;
            }
        }
        merged.push({ ...live, source: "live" });
    });
    schedDeps.forEach((s, i) => { if (!used.has(i)) merged.push(s); });
    merged.sort((a, b) => a.eta_mins - b.eta_mins);
    return merged.slice(0, 20);
}

function departureRow(dep, locateBus) {
    const isLive = dep.source === "live";
    const isDue = isLive && dep.eta_mins <= 0;
    const etaText = !isLive ? (dep.scheduled_time || `${dep.eta_mins} min`)
                  : isDue ? "DUE"
                  : dep.eta_mins === 1 ? "1 min" : `${dep.eta_mins} min`;
    const clickable = isLive && dep.vehicleRef;
    return el("div", {
        class: ["dep", isLive ? "dep-live" : "dep-sched",
                clickable ? "dep-clickable" : null].filter(Boolean).join(" "),
        role: "listitem",
        onClick: clickable ? () => locateBus(dep.vehicleRef) : null,
    }, [
        el("div", { class: "dep-left" }, [
            el("div", { class: "route" }, [dep.line]),
            el("div", {}, [
                el("div", { class: "dest" }, [dep.destination]),
                el("div", { class: "src-row" }, isLive
                    ? [el("span", { class: "livedot", "aria-hidden": "true" }),
                       el("span", { class: "src-tag src-live" }, ["LIVE"])]
                    : [el("span", { class: "src-tag src-sched" }, ["SCHED"])]),
            ]),
        ]),
        el("div", {
            class: ["eta", isDue ? "eta-due" : null,
                    !isLive ? "eta-sched" : null].filter(Boolean).join(" "),
        }, [etaText]),
    ]);
}

function stopHeader(stopName, stopCode, info) {
    document.getElementById("selected-stop-name").textContent = stopName;
    document.getElementById("stop-code-display").textContent = stopCode;
    const street = document.getElementById("stop-street-display");
    const ward = document.getElementById("stop-ward-display");
    const routesHost = document.getElementById("stop-routes-display");
    if (!info) {
        street.textContent = ward.textContent = "";
        replaceContents(routesHost, []);
        return;
    }
    street.textContent = info.street ? `on ${info.street}` : "";
    const parts = [];
    if (info.enriched_locality) parts.push(info.enriched_locality);
    if (info.local_authority) parts.push(info.local_authority);
    else {
        if (info.ward && info.ward !== "Other") parts.push(info.ward);
        if (info.area && info.area !== "Other") parts.push(info.area);
    }
    ward.textContent = parts.join(", ");
    const routes = info.routes || [];
    const chips = routes.slice(0, 12).map(r => el("span", { class: "chip" }, [r]));
    if (routes.length > 12)
        chips.push(el("span", { class: "chip-more" }, [`+${routes.length - 12}`]));
    replaceContents(routesHost, chips);
}

let _lastAnnounce = 0;

function announce(msg) {
    // Screen-reader announcement, debounced: the board refreshes every 15s
    // and repeating identical announcements is noise, not accessibility.
    const host = document.getElementById("dep-live-region");
    if (!host) return;
    const now = Date.now();
    if (now - _lastAnnounce < 20_000) return;
    host.textContent = msg;
    _lastAnnounce = now;
}

export function renderBoard(liveData, schedData, ctx) {
    const stopName = liveData.stop_name || schedData.stop_name || "";
    const stopCode = liveData.stop_code || schedData.stop_code || "";
    stopHeader(stopName, stopCode,
               (ctx.searchStops || []).find(s => s.stop_code === stopCode));
    document.getElementById("departure-update-time").textContent =
        new Date().toLocaleTimeString();

    const host = document.getElementById("departures-list");
    const merged = mergeDepartures(liveData.departures || [],
                                   schedData.scheduled_departures || []);
    if (!merged.length) {
        replaceContent(host, el("div", { class: "board-empty" },
                                ["no upcoming departures"]));
        return;
    }
    host.setAttribute("role", "list");
    replaceContents(host, merged.map(d => departureRow(d, ctx.locateBus)));
    const next = merged[0];
    announce(`Departures for ${stopName} updated. Next: ${next.line} to ` +
             `${next.destination}, ${next.source === "live" && next.eta_mins <= 0
                ? "due now" : (next.scheduled_time || next.eta_mins + " minutes")}.`);
}

window.BBB = window.BBB || {};
window.BBB.renderBoard = renderBoard;
