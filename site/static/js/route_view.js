/** Render journey schedules and route-search results in the sidebar. */
import { el, replaceContents } from "./util.js";

let savedNodes = null;

export function saveBoard(host) {
    if (savedNodes === null) savedNodes = Array.from(host.childNodes);
}

export function restoreBoard(host) {
    if (savedNodes !== null) {
        host.replaceChildren(...savedNodes);
        savedNodes = null;
        return true;
    }
    return false;
}

export function dropSavedBoard() { savedNodes = null; }

const STATUS = {
    waiting:  { text: () => "waiting to depart", cls: "vp-waiting" },
    punctual: { text: () => "on time",           cls: "vp-punctual" },
    early:    { text: d => `${Math.abs(d)}m early`, cls: "vp-early" },
    delayed:  { text: d => `${d}m late`,         cls: "vp-delayed" },
};

function statusPill(eventType, waiting, delayMinutes) {
    const s = waiting ? STATUS.waiting : (STATUS[eventType] || STATUS.punctual);
    return el("span", { class: `rv-pill ${s.cls}` },
              [s.text(parseInt(delayMinutes) || 0)]);
}

function closeButton(onClose) {
    return el("button", { class: "rv-close", onClick: onClose }, ["✕ Close"]);
}

/** Journey view header (line → destination + status). */
export function journeyHeader(host, ctx) {
    const head = el("div", { class: "rv-head" }, [
        el("div", { class: "rv-head-top" }, [
            el("div", {}, [
                el("span", { class: "rv-line" }, [ctx.line]),
                el("span", { class: "rv-dest" }, [`→ ${ctx.destination}`]),
            ]),
            closeButton(ctx.onClose),
        ]),
        statusPill(ctx.eventType, ctx.eventType === "waiting", ctx.delayMinutes),
    ]);
    if (ctx.hasShape) head.style.background =
        // full-strength livery blind (an alpha wash reads pastel on the
    // light sidebar); darkened tail keeps the white text legible
    `linear-gradient(135deg, ${ctx.routeColor}, color-mix(in srgb, ${ctx.routeColor} 65%, black))`;
    const wrap = el("div", { class: "rv-wrap" }, [head]);
    replaceContents(host, [wrap]);
    return wrap;
}

export function journeyNoSchedule(wrap) {
    wrap.appendChild(el("div", { class: "rv-empty" }, [
        "Schedule data not available for this journey",
        el("div", { class: "rv-empty-sub" }, ["Route shape shown on map"]),
    ]));
}

/** Journey view stop list: groups = [{ward, stops:[{idx, common_name,
 *  arrival_time, latitude, longitude}]}] */
export function journeyStops(wrap, ctx) {
    const { groups, currentStopIdx, busDelay, hasDelay, routeColor,
            fmtTime, fmtTimeWithDelay, flyTo } = ctx;
    const out = [];
    for (const g of groups) {
        const rows = [el("div", { class: "rv-ward" }, [g.ward])];
        for (const s of g.stops) {
            const isCurrent = s.idx === currentStopIdx;
            const isPast = s.idx < currentStopIdx;
            const stateCls = isCurrent ? "rv-stop-current"
                          : isPast ? "rv-stop-past" : "rv-stop-future";
            const dotEl = el("span", { class: "rv-dot", "aria-hidden": "true" },
                             [isCurrent ? "◉" : isPast ? "●" : "○"]);
            if (isPast) dotEl.style.color = routeColor;

            let time;
            if (!isPast && hasDelay) {
                time = el("span", { class: "rv-time" }, [
                    el("span", { class: `rv-est ${busDelay > 0 ? "rv-est-late" : "rv-est-early"}${isCurrent ? " rv-est-current" : ""}` },
                       [fmtTimeWithDelay(s.arrival_time, busDelay)]),
                    el("span", { class: "rv-sched-strike" }, [fmtTime(s.arrival_time)]),
                ]);
            } else {
                time = el("span", { class: "rv-time rv-time-plain" },
                          [fmtTime(s.arrival_time)]);
                if (isPast) time.style.color = "#b48800";
            }

            const name = el("span", {
                class: "rv-stop-name",
                onClick: s.latitude ? () => flyTo(s.latitude, s.longitude) : null,
            }, [s.common_name]);
            if (s.latitude) name.classList.add("rv-clickable");

            rows.push(el("div", { class: `rv-stop ${stateCls}` },
                         [dotEl, name, time]));
        }
        out.push(el("div", { class: "rv-group" }, rows));
    }
    out.forEach(n => wrap.appendChild(n));
}

/** Route search view: header + destination-grouped live buses. */
export function routeSearchView(host, ctx) {
    const {
        line,
        operatorName,
        variants,
        approximate,
        pathLoading,
        buses,
        onClose,
        locateBus,
    } = ctx;
    const pathLabel = variants
        ? `${variants} ${approximate ? "timetable path" : "variant"}${variants !== 1 ? "s" : ""}`
        : pathLoading ? "loading route path" : "live buses only";
    const head = el("div", { class: "rv-head rv-head-search" }, [
        el("div", { class: "rv-head-top" }, [
            el("div", {}, [
                el("span", { class: "rv-line rv-line-lg" }, [line]),
                el("span", { class: "rv-op" }, [operatorName]),
            ]),
            closeButton(onClose),
        ]),
        el("div", { class: "rv-tags" }, [
            el("span", { class: "rv-tag rv-tag-blue" },
               [pathLabel]),
            el("span", { class: `rv-tag ${buses.length ? "rv-tag-green" : "rv-tag-grey"}` },
               [`${buses.length} active bus${buses.length !== 1 ? "es" : ""}`]),
        ]),
    ]);
    const out = [el("div", { class: "rv-wrap" }, [head])];

    if (!buses.length) {
        out.push(el("div", { class: "rv-empty" },
                    ["No active buses on this route right now"]));
    } else {
        const byDest = new Map();
        for (const b of buses) {
            const dest = b.destination || "Unknown";
            if (!byDest.has(dest)) byDest.set(dest, []);
            byDest.get(dest).push(b);
        }
        for (const [dest, destBuses] of byDest) {
            const rows = [el("div", { class: "rv-ward rv-ward-blue" }, [`→ ${dest}`])];
            for (const b of destBuses) {
                const fleetText = b.fleetNumber ? `#${b.fleetNumber}` : (b.reg || b.vehicleRef);
                const title = [b.model || fleetText];
                if (b.model) title.push(el("span", { class: "rv-sub-inline" }, [fleetText]));
                const main = [el("div", { class: "rv-bus-title" }, title)];
                if (b.lastStopName)
                    main.push(el("div", { class: "rv-near" }, [`Near ${b.lastStopName}`]));
                rows.push(el("div", {
                    class: "rv-bus rv-clickable",
                    onClick: () => locateBus(b.vehicleRef),
                }, [
                    el("div", { class: "rv-bus-main" }, main),
                    statusPill(b.eventType, b.waitingAtOrigin || b.eventType === "waiting",
                               b.delayMinutes),
                ]));
            }
            out.push(el("div", { class: "rv-group" }, rows));
        }
    }
    replaceContents(host, out);
}

window.BBB = window.BBB || {};
Object.assign(window.BBB, { saveBoard, restoreBoard, dropSavedBoard,
                            journeyHeader, journeyNoSchedule, journeyStops,
                            routeSearchView });
