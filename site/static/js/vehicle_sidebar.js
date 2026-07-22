/** Unified journey, vehicle identity and observed-history sidebar. */
import { el, replaceContent } from "./util.js";
import { liveryColor } from "./map_render.js";
import { vehicleCard } from "./vehicle_card.js";
import { formatServiceDate, statusPresentation } from "./vehicle_sidebar_logic.js";

function formatGtfsTime(value, delayMinutes = 0) {
    if (!value) return "";
    const parts = String(value).split(":");
    let minutes = (Number(parts[0]) * 60) + Number(parts[1]) + delayMinutes;
    minutes %= 24 * 60;
    if (minutes < 0) minutes += 24 * 60;
    return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

function badgeData(vehicle) {
    const out = [];
    const fuel = String(vehicle?.fuel || "").toLowerCase();
    if (fuel === "electric") out.push({ text: "ELECTRIC", cls: "fb-electric" });
    else if (fuel === "gas" || fuel === "biogas")
        out.push({ text: "BIOGAS", cls: "fb-gas" });
    else if (fuel === "hybrid") out.push({ text: "HYBRID", cls: "fb-electric" });
    else if (fuel) out.push({ text: fuel.toUpperCase() });
    if (vehicle?.double_decker) out.push({ text: "DOUBLE-DECKER" });
    if (vehicle?.coach) out.push({ text: "COACH" });
    (Array.isArray(vehicle?.special_features) ? vehicle.special_features : [])
        .forEach(feature => out.push({ text: String(feature) }));
    return out;
}

function identityValue(ctx, fleetKey, busKey) {
    return ctx.vehicle?.[fleetKey] || ctx.bus?.[busKey] || "";
}

function identityCard(ctx) {
    const vehicle = ctx.vehicle || {};
    const bus = ctx.bus;
    const actions = [];
    if (bus) {
        actions.push(el("button", {
            class: "vc-action vc-action-primary",
            onClick: ctx.onTrack,
        }, ["Focus bus on map"]));
    } else {
        actions.push(el("div", { class: "vc-offduty" },
            ["Not currently running - live journey information is unavailable"]));
    }
    if (ctx.featuredPost?.postUrl) {
        actions.push(el("a", {
            class: "vc-action vc-action-bsky",
            href: ctx.featuredPost.postUrl,
            target: "_blank",
        }, ["Featured on @bristolbusbot.live"]));
    }

    return vehicleCard({
        fleetCode: identityValue(ctx, "fleet_code", "fleetNumber") || "?",
        reg: identityValue(ctx, "reg", "reg"),
        previousReg: vehicle.previous_reg,
        model: identityValue(ctx, "model", "model"),
        liveryName: vehicle.livery_name || bus?.livery?.name,
        operatorName: vehicle.operator_name || bus?.operatorRef,
        garageName: vehicle.garage_name
            ? vehicle.garage_name + (vehicle.garage_code ? ` (${vehicle.garage_code})` : "")
            : bus?.garage,
        namedBus: vehicle.name,
        notes: vehicle.notes,
        blurb: ctx.description,
        badges: badgeData(vehicle),
        accent: ctx.routeColor,
        withdrawn: Boolean(vehicle.withdrawn),
        isDepot: bus?.eventType === "depot",
        status: (bus && bus.eventType !== "depot") ? {
            eventType: bus.eventType,
            waiting: bus.waitingAtOrigin,
            delayMinutes: bus.delayMinutes,
            lastStopName: bus.lastStopName,
        } : null,
        actions,
    }, "embedded");
}

function emptyState(title, detail) {
    return el("div", { class: "vs-empty" }, [
        el("strong", {}, [title]),
        el("span", {}, [detail]),
    ]);
}

function journeyPanel(ctx) {
    if (!ctx.bus)
        return emptyState("This bus is off duty",
            "Its identity and observed route history are still available in the other tabs.");
    if (ctx.bus.eventType === "depot")
        return emptyState("This bus is at the depot",
            ctx.bus.depotName || "No live passenger journey is currently reported.");
    if (ctx.scheduleLoading)
        return emptyState("Loading this journey", "Checking the matched timetable and stop sequence.");
    if (!ctx.schedule)
        return emptyState("Schedule unavailable",
            "The bus remains visible on the map, but no trustworthy stop sequence was matched.");

    const stops = Array.isArray(ctx.schedule.stops) ? ctx.schedule.stops : [];
    if (!stops.length)
        return emptyState("No stops returned", "The matched journey did not contain a displayable stop list.");

    const current = Math.max(0, Math.min(ctx.currentStopIdx ?? 0, stops.length - 1));
    const next = stops[Math.min(current + 1, stops.length - 1)];
    const delay = Number.parseInt(ctx.bus.delayMinutes, 10) || 0;
    const hasPrediction = delay !== 0 && !ctx.bus.waitingAtOrigin;
    const out = el("div", { class: "vs-journey" }, [
        el("div", { class: "vs-next" }, [
            el("div", {}, [
                el("span", { class: "vs-eyebrow" }, [current >= stops.length - 1 ? "Final stop" : "Next stop"]),
                el("strong", {}, [next.common_name || next.stop_name || "Unknown stop"]),
            ]),
            el("span", { class: "vs-next-time" }, [
                formatGtfsTime(next.arrival_time, hasPrediction ? delay : 0),
            ]),
        ]),
    ]);

    let previousWard = null;
    const list = el("ol", { class: "vs-stop-list" });
    stops.forEach((stop, index) => {
        const ward = stop.ward || "Other";
        if (ward !== previousWard) {
            list.appendChild(el("li", { class: "vs-ward" }, [ward]));
            previousWard = ward;
        }
        const isCurrent = index === current;
        const isPast = index < current;
        const name = stop.common_name || stop.stop_name || "Unknown stop";
        const scheduled = formatGtfsTime(stop.arrival_time);
        const predicted = (!isPast && hasPrediction)
            ? formatGtfsTime(stop.arrival_time, delay) : scheduled;
        const time = el("span", { class: "vs-stop-time" }, [predicted]);
        if (!isPast && hasPrediction)
            time.appendChild(el("small", {}, [scheduled]));
        const row = el("li", {
            class: `vs-stop${isCurrent ? " is-current" : ""}${isPast ? " is-past" : ""}`,
        }, [
            el("span", { class: "vs-stop-dot", "aria-hidden": "true" }),
            el("button", {
                class: "vs-stop-name",
                onClick: stop.latitude && stop.longitude
                    ? () => ctx.onFlyTo(stop.latitude, stop.longitude) : null,
            }, [name, isCurrent ? el("small", {}, ["bus nearby"]) : null]),
            time,
        ]);
        list.appendChild(row);
    });
    out.appendChild(list);
    return out;
}

function metric(value, label) {
    return el("div", { class: "vs-metric" }, [
        el("strong", {}, [value]), el("span", {}, [label]),
    ]);
}

function historyPanel(ctx) {
    if (ctx.profileLoading)
        return emptyState("Loading observed history", "Reading the latest published audit snapshot.");
    if (!ctx.profile) {
        return emptyState("Not enough observations yet",
            "Profiles appear after at least two service days and 30 timing-point readings.");
    }

    const profile = ctx.profile;
    const root = el("div", { class: "vs-history" }, [
        el("div", { class: "vs-metrics" }, [
            metric(`${profile.on_time_pct}%`, "on time"),
            metric(Number(profile.readings || 0).toLocaleString(), "readings"),
            metric(profile.observed_days, "service days"),
        ]),
        el("p", { class: "vs-method-note" }, [
            `Observed ${formatServiceDate(profile.measurement_start)} to ${formatServiceDate(profile.through_date)}. `,
            "On time means 1 minute early to 5 min 59 s late at a timing point.",
        ]),
    ]);

    const routes = Array.isArray(profile.routes) ? profile.routes : [];
    routes.forEach((route, index) => {
        const summaryParts = [`${route.observed_days} day${route.observed_days === 1 ? "" : "s"}`,
                              `${route.readings} readings`];
        if (route.on_time_pct !== undefined)
            summaryParts.unshift(`${route.on_time_pct}% on time`);
        const details = el("details", { class: "vs-route-history" }, [
            el("summary", {}, [
                el("strong", {}, [route.route]),
                el("span", {}, [summaryParts.join(" / ")]),
            ]),
        ]);
        if (index === 0) details.open = true;
        const days = Array.isArray(route.days) ? route.days : [];
        if (!days.length) {
            details.appendChild(el("p", { class: "vs-day-empty" },
                ["Daily detail will appear after the next audit refresh."]));
        } else {
            const dayList = el("div", { class: "vs-day-list" });
            days.forEach(day => {
                dayList.appendChild(el("div", { class: "vs-day" }, [
                    el("time", {}, [formatServiceDate(day.service_date)]),
                    el("span", {}, [`${day.on_time_pct}% on time`]),
                    el("small", {}, [`${day.readings} readings`]),
                ]));
            });
            details.appendChild(dayList);
        }
        root.appendChild(details);
    });

    root.appendChild(el("p", { class: "vs-disclosure" }, [
        "These are aggregate public-data observations, not a continuous movement history. ",
        "Traffic, route allocation and operating conditions all affect the result.",
    ]));
    if (ctx.profileUrl) {
        root.appendChild(el("a", {
            class: "vs-profile-link",
            href: ctx.profileUrl,
        }, ["Open shareable vehicle profile"]));
    }
    return root;
}

function tabButton(id, label, active, onSelect) {
    return el("button", {
        class: "vs-tab",
        role: "tab",
        "aria-selected": active ? "true" : "false",
        "aria-controls": `vs-panel-${id}`,
        onClick: () => onSelect(id),
    }, [label]);
}

export function renderVehicleSidebar(host, ctx) {
    const bus = ctx.bus;
    const vehicle = ctx.vehicle || {};
    const livery = vehicle.livery_left || bus?.livery?.left || "#7E8582";
    const routeColor = ctx.routeColor || liveryColor({ left: livery }) || "#7E8582";
    const reg = vehicle.reg || bus?.reg || "Vehicle details";
    const fleet = vehicle.fleet_code || bus?.fleetNumber;
    const liveryName = vehicle.livery_name || bus?.livery?.name || "Livery unavailable";
    const status = statusPresentation(bus);
    const activeTab = ctx.activeTab || (bus ? "journey" : "vehicle");
    const stops = ctx.schedule?.stops || [];
    const progress = stops.length > 1 && Number.isInteger(ctx.currentStopIdx)
        ? Math.max(0, Math.min(100, (ctx.currentStopIdx / (stops.length - 1)) * 100)) : 0;

    const liveryBand = el("div", {
        class: "vs-livery-band",
        title: liveryName,
        "aria-label": `${liveryName} livery`,
    });
    liveryBand.style.background = livery;
    const headerChildren = [
        liveryBand,
        el("div", { class: "vs-head-top" }, [
            el("div", {}, [
                el("span", { class: "vs-kicker" }, [
                    `${reg}${fleet ? ` / fleet ${fleet}` : ""}`,
                ]),
                el("span", { class: "vs-livery-name" }, [liveryName]),
            ]),
            el("button", {
                class: "vs-close",
                "aria-label": "Close vehicle details",
                onClick: ctx.onClose,
            }, ["Close"]),
        ]),
    ];

    if (bus) {
        headerChildren.push(el("div", { class: "vs-route-identity" }, [
            el("div", { class: "vs-route-number" }, [bus.line || "-"]),
            el("div", { class: "vs-route-copy" }, [
                el("h2", {}, [bus.eventType === "depot"
                    ? (bus.depotName || "At depot")
                    : `To ${bus.destination || "Unknown destination"}`]),
                el("div", { class: "vs-live-line" }, [
                    el("span", { class: `vs-status ${status.cls}` }, [status.text]),
                    bus.lastStopName && bus.lastStopName !== "unknown"
                        ? el("span", {}, [`at ${bus.lastStopName}`]) : null,
                ]),
            ]),
        ]));
        if (stops.length > 1) {
            const fill = el("span");
            fill.style.width = `${progress}%`;
            fill.style.backgroundColor = routeColor;
            headerChildren.push(el("div", { class: "vs-progress" }, [
                el("span", {}, [stops[0].common_name || "Start"]),
                el("span", { class: "vs-progress-track" }, [fill]),
                el("span", {}, [stops[stops.length - 1].common_name || "Destination"]),
            ]));
        }
    } else {
        headerChildren.push(el("div", { class: "vs-offline-title" }, [
            el("h2", {}, [vehicle.model || reg]),
            el("span", { class: `vs-status ${status.cls}` }, [status.text]),
        ]));
    }

    const panelContent = activeTab === "journey" ? journeyPanel({ ...ctx, routeColor })
        : activeTab === "vehicle" ? identityCard({ ...ctx, routeColor })
        : historyPanel(ctx);
    const shell = el("article", { class: "vehicle-sidebar" }, [
        el("header", { class: "vs-head" }, headerChildren),
        el("nav", { class: "vs-tabs", role: "tablist", "aria-label": "Vehicle information" }, [
            tabButton("journey", "Journey", activeTab === "journey", ctx.onTabChange),
            tabButton("vehicle", "Vehicle", activeTab === "vehicle", ctx.onTabChange),
            tabButton("history", "History", activeTab === "history", ctx.onTabChange),
        ]),
        el("section", {
            class: "vs-panel",
            id: `vs-panel-${activeTab}`,
            role: "tabpanel",
        }, [panelContent]),
    ]);
    replaceContent(host, shell);
}

if (typeof window !== "undefined") {
    window.BBB = window.BBB || {};
    window.BBB.renderVehicleSidebar = renderVehicleSidebar;
}
