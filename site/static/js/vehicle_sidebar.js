/** Unified journey, vehicle identity and observed-record sidebar. */
import { el, replaceContent } from "./util.js";
import { liveryColor } from "./map_render.js";
import { plate } from "./vehicle_card.js";
import {
    delayDotColumns,
    formatServiceDate,
    statusPresentation,
} from "./vehicle_sidebar_logic.js";

const TAB_ORDER = ["journey", "vehicle", "record"];

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

function emptyState(title, detail) {
    return el("div", { class: "vs-empty" }, [
        el("strong", {}, [title]),
        el("span", {}, [detail]),
    ]);
}

function vehicleSummary(ctx) {
    const vehicle = ctx.vehicle || {};
    const reg = identityValue(ctx, "reg", "reg");
    const fleet = identityValue(ctx, "fleet_code", "fleetNumber");
    const model = identityValue(ctx, "model", "model") || "Model unavailable";
    const operator = vehicle.operator_name || ctx.bus?.operatorRef || "";
    const garage = vehicle.garage_name
        ? vehicle.garage_name + (vehicle.garage_code ? ` (${vehicle.garage_code})` : "")
        : ctx.bus?.garage;
    const meta = [operator, garage, fleet ? `fleet ${fleet}` : null].filter(Boolean);
    return el("section", { class: "vs-identity-summary", "aria-label": "Vehicle identity" }, [
        reg ? el("div", { class: "vs-plate" }, [plate(reg, "lg")]) : null,
        el("h2", { class: "vs-model" }, [model]),
        badgeData(vehicle).length
            ? el("div", { class: "vc-badges vs-summary-badges" },
                badgeData(vehicle).map(badge => el("span", {
                    class: `vc-badge ${badge.cls || ""}`,
                }, [badge.text])))
            : null,
        meta.length ? el("p", { class: "vs-identity-meta" }, [meta.join(" · ")]) : null,
    ]);
}

function detailRow(label, value) {
    if (!value) return null;
    return [
        el("dt", {}, [label]),
        el("dd", {}, [String(value)]),
    ];
}

function vehiclePanel(ctx) {
    const vehicle = ctx.vehicle || {};
    const bus = ctx.bus;
    const reg = identityValue(ctx, "reg", "reg");
    const fleet = identityValue(ctx, "fleet_code", "fleetNumber");
    const liveryName = vehicle.livery_name || bus?.livery?.name;
    const garage = vehicle.garage_name
        ? vehicle.garage_name + (vehicle.garage_code ? ` (${vehicle.garage_code})` : "")
        : bus?.garage;
    const details = [
        ...(detailRow("Registration", reg) || []),
        ...(detailRow("Previous registration", vehicle.previous_reg) || []),
        ...(detailRow("Fleet number", fleet) || []),
        ...(detailRow("Operator", vehicle.operator_name || bus?.operatorRef) || []),
        ...(detailRow("Garage", garage) || []),
        ...(detailRow("Livery", liveryName) || []),
    ];
    const content = [];

    if (vehicle.name) {
        content.push(el("div", { class: "vc-named" }, [
            el("div", { class: "vc-named-label" }, ["Named bus"]),
            el("div", { class: "vc-named-name" }, [`“${vehicle.name}”`]),
        ]));
    }
    if (vehicle.withdrawn) {
        content.push(el("div", { class: "vs-withdrawn" }, [
            el("span", { class: "vs-state-shape vs-shape-late", "aria-hidden": "true" }),
            el("strong", {}, ["Withdrawn from service"]),
        ]));
    }
    if (details.length)
        content.push(el("dl", { class: "vs-details" }, details));
    if (ctx.description)
        content.push(el("blockquote", { class: "vc-quote" }, [ctx.description]));
    if (vehicle.notes) {
        content.push(el("div", { class: "vc-notes" }, [
            el("span", { class: "vc-notes-label" }, ["Notes"]),
            vehicle.notes,
        ]));
    }

    const links = [];
    if (ctx.featuredPost?.postUrl) {
        links.push(el("a", {
            class: "vc-action vc-action-bsky",
            href: ctx.featuredPost.postUrl,
            target: "_blank",
            rel: "noopener",
        }, ["Featured on @bristolbusbot.live"]));
    }
    if (reg) {
        links.push(el("a", {
            class: "vc-action vc-action-flickr",
            href: "https://www.flickr.com/search/?text=" + encodeURIComponent(reg),
            target: "_blank",
            rel: "noopener",
        }, ["Photos of this bus on Flickr ↗"]));
    }
    if (links.length)
        content.push(el("div", { class: "vs-external-links" }, links));
    if (!bus) {
        content.push(el("div", { class: "vc-offduty" },
            ["Not currently running — live journey information is unavailable."]));
    }
    return el("div", { class: "vs-vehicle" }, content);
}

function journeyPanel(ctx) {
    if (!ctx.bus)
        return emptyState("This bus is off duty",
            "Its identity and observed route record are still available in the other tabs.");
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
        const canLocate = Number.isFinite(Number(stop.latitude))
            && Number.isFinite(Number(stop.longitude));
        const nameNode = canLocate
            ? el("button", {
                class: "vs-stop-name",
                onClick: () => ctx.onFlyTo(stop.latitude, stop.longitude),
            }, [name, isCurrent ? el("small", {}, ["bus nearby"]) : null])
            : el("span", { class: "vs-stop-name" },
                [name, isCurrent ? el("small", {}, ["bus nearby"]) : null]);
        list.appendChild(el("li", {
            class: `vs-stop${isCurrent ? " is-current" : ""}${isPast ? " is-past" : ""}`,
        }, [
            el("span", { class: "vs-stop-dot", "aria-hidden": "true" }),
            nameNode,
            time,
        ]));
    });
    out.appendChild(list);
    return out;
}

function metric(value, label) {
    return el("div", { class: "vs-metric" }, [
        el("strong", {}, [value]),
        el("span", {}, [label]),
    ]);
}

function setStyles(node, values) {
    Object.entries(values).forEach(([name, value]) => {
        node.style.setProperty(name, value);
    });
    return node;
}

function delayDots(model, compact = false) {
    return model.columns.flatMap(column => {
        if (!column.dots) return [];
        const dots = Array.from({ length: column.dots }, () => el("span", {
            class: `vs-delay-dot vs-delay-dot-${column.kind}`,
            "aria-hidden": "true",
        }));
        return [setStyles(el("span", {
            class: compact ? "vs-delay-stack is-compact" : "vs-delay-stack",
            "aria-hidden": "true",
        }, dots), { left: `${column.leftPct}%` })];
    });
}

function delayPlot(profile, { compact = false } = {}) {
    const model = delayDotColumns(
        profile?.delay_bins_s,
        profile?.delay_counts,
        compact ? 42 : 600,
        compact ? 3 : 30,
    );
    if (!model || model.total !== Number(profile?.readings)) return null;
    const band = setStyles(el("span", {
        class: "vs-delay-band", "aria-hidden": "true",
    }), {
        left: `${model.axis.onTimeStartPct}%`,
        width: `${model.axis.onTimeEndPct - model.axis.onTimeStartPct}%`,
    });
    const zero = setStyles(el("span", {
        class: "vs-delay-zero", "aria-hidden": "true",
    }), { left: `${model.axis.zeroPct}%` });
    const minimum = Math.round(model.axis.minimumSeconds / 60);
    const maximum = Math.round(model.axis.maximumSeconds / 60);
    const ariaLabel = `${model.total} timing-point readings: `
        + `${profile.early} early, ${profile.on_time} on time, ${profile.late} late`;
    const chart = el("div", {
        class: compact ? "vs-delay-chart is-compact" : "vs-delay-chart",
        role: "img",
        "aria-label": ariaLabel,
    }, [
        band,
        zero,
        ...delayDots(model, compact),
        el("span", { class: "vs-delay-axis", "aria-hidden": "true" }),
        compact ? null : setStyles(el("span", {
            class: "vs-delay-zero-label", "aria-hidden": "true",
        }, ["0"]), { left: `${model.axis.zeroPct}%` }),
        compact ? null : el("span", {
            class: "vs-delay-axis-labels", "aria-hidden": "true",
        }, [
            el("span", {}, [`${minimum} early`]),
            el("span", {}, ["on time"]),
            el("span", {}, [`+${maximum} late`]),
        ]),
    ]);
    return { chart, model };
}

function delayFigure(profile) {
    const plot = delayPlot(profile);
    if (!plot) return null;
    const dotLabel = plot.model.unit === 1
        ? "One dot per timing-point reading"
        : `One dot per ${plot.model.unit} timing-point readings`;
    return el("figure", { class: "vs-delay-figure" }, [
        el("div", { class: "vs-delay-heading" }, [
            el("strong", {}, ["Minutes from timetable"]),
            el("span", {}, [`${Number(profile.readings || 0).toLocaleString()} readings`]),
        ]),
        el("p", { class: "vs-delay-caption" }, [dotLabel]),
        plot.chart,
    ]);
}

function routeDelayPlot(route, delayBins) {
    const plot = delayPlot({ ...route, delay_bins_s: delayBins }, { compact: true });
    return plot?.chart || null;
}

function recordPanel(ctx) {
    if (ctx.profileLoading)
        return emptyState("Loading observed record", "Reading the latest published audit snapshot.");
    if (!ctx.profile) {
        return emptyState("Record unavailable",
            "Fewer than 2 service days or 30 timing-point readings.");
    }

    const profile = ctx.profile;
    const root = el("div", { class: "vs-history" }, [
        delayFigure(profile),
        el("div", { class: "vs-metrics" }, [
            metric(`${profile.on_time_pct}%`, "on time"),
            metric(Number(profile.readings || 0).toLocaleString(), "readings"),
            metric(profile.observed_days, "service days"),
        ]),
        el("p", { class: "vs-method-note" }, [
            `${formatServiceDate(profile.measurement_start)} to ${formatServiceDate(profile.through_date)} | `,
            "on time = 1 min early to 5 min 59 s late at a timing point",
        ]),
        el("div", { class: "vs-route-heading" }, [
            el("strong", {}, ["Routes observed"]),
            el("span", {}, ["most measured"]),
        ]),
    ]);

    const routes = Array.isArray(profile.routes) ? profile.routes : [];
    const routeList = el("div", { class: "vs-route-list" });
    routes.forEach((route, index) => {
        const summaryParts = [
            `${route.observed_days} day${route.observed_days === 1 ? "" : "s"}`,
            `${route.readings} readings`,
        ];
        if (route.on_time_pct !== undefined)
            summaryParts.unshift(`${route.on_time_pct}% on time`);
        const routePlot = routeDelayPlot(route, profile.delay_bins_s);
        const details = el("details", {
            class: routePlot ? "vs-route-history has-delay-plot" : "vs-route-history",
        }, [
            el("summary", {}, [
                el("strong", { class: "vs-route-badge" }, [route.route]),
                el("span", { class: "vs-route-result" }, [summaryParts.join(" · ")]),
                routePlot,
            ]),
        ]);
        if (index === 0) details.open = true;
        const days = Array.isArray(route.days) ? route.days : [];
        if (!days.length) {
            details.appendChild(el("p", { class: "vs-day-empty" },
                ["No daily observations published."]));
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
        routeList.appendChild(details);
    });
    if (routes.length) root.appendChild(routeList);

    if (ctx.profileUrl) {
        root.appendChild(el("a", {
            class: "vs-profile-link",
            href: ctx.profileUrl,
        }, ["Open shareable vehicle profile"]));
    }
    return root;
}

function tabButton(id, label, active, onSelect) {
    const selectAndFocus = next => {
        onSelect(next);
        window.requestAnimationFrame(() => {
            document.getElementById(`vs-tab-${next}`)?.focus();
        });
    };
    return el("button", {
        id: `vs-tab-${id}`,
        class: "vs-tab",
        role: "tab",
        "aria-selected": active ? "true" : "false",
        "aria-controls": `vs-panel-${id}`,
        tabindex: active ? 0 : -1,
        onClick: () => onSelect(id),
        onKeyDown: event => {
            const current = TAB_ORDER.indexOf(id);
            if (event.key === "ArrowRight") {
                event.preventDefault();
                selectAndFocus(TAB_ORDER[(current + 1) % TAB_ORDER.length]);
            } else if (event.key === "ArrowLeft") {
                event.preventDefault();
                selectAndFocus(TAB_ORDER[(current - 1 + TAB_ORDER.length) % TAB_ORDER.length]);
            } else if (event.key === "Home") {
                event.preventDefault();
                selectAndFocus(TAB_ORDER[0]);
            } else if (event.key === "End") {
                event.preventDefault();
                selectAndFocus(TAB_ORDER[TAB_ORDER.length - 1]);
            }
        },
    }, [label]);
}

function liveBand(ctx, routeColor) {
    const bus = ctx.bus;
    const status = statusPresentation(bus);
    if (!bus) {
        return el("section", { class: "vs-live-band vs-status-off" }, [
            el("div", { class: "vs-live-summary" }, [
                el("span", {
                    class: `vs-state-shape ${status.shape}`,
                    "aria-hidden": "true",
                }),
                el("strong", {}, [status.longText]),
            ]),
        ]);
    }
    const children = [
        el("div", { class: "vs-live-summary" }, [
            el("span", {
                class: `vs-state-shape ${status.shape}`,
                "aria-hidden": "true",
            }),
            el("strong", {}, [status.longText]),
            el("span", { class: "vs-live-source" }, ["live feed"]),
        ]),
        el("div", { class: "vs-live-route" }, [
            el("span", { class: "vs-live-route-number" }, [bus.line || "–"]),
            el("span", {}, [
                bus.eventType === "depot"
                    ? (bus.depotName || "At depot")
                    : `to ${bus.destination || "Unknown destination"}`,
                bus.lastStopName && bus.lastStopName !== "unknown"
                    ? ` · at ${bus.lastStopName}` : "",
            ]),
        ]),
    ];
    const stops = ctx.schedule?.stops || [];
    if (stops.length > 1 && Number.isInteger(ctx.currentStopIdx)) {
        const progress = Math.max(0, Math.min(
            100, (ctx.currentStopIdx / (stops.length - 1)) * 100));
        const fill = el("span");
        fill.style.width = `${progress}%`;
        fill.style.backgroundColor = routeColor;
        children.push(el("div", { class: "vs-progress" }, [
            el("span", {}, [stops[0].common_name || "Start"]),
            el("span", { class: "vs-progress-track" }, [fill]),
            el("span", {}, [stops[stops.length - 1].common_name || "Destination"]),
        ]));
    }
    return el("section", { class: `vs-live-band ${status.cls}` }, children);
}

export function renderVehicleSidebar(host, ctx) {
    const bus = ctx.bus;
    const vehicle = ctx.vehicle || {};
    const livery = vehicle.livery_left || bus?.livery?.left || "#7E8582";
    const routeColor = ctx.routeColor || liveryColor({ left: livery }) || "#7E8582";
    const reg = vehicle.reg || bus?.reg || "Vehicle details";
    const fleet = vehicle.fleet_code || bus?.fleetNumber;
    const liveryName = vehicle.livery_name || bus?.livery?.name || "Livery unavailable";
    const activeTab = TAB_ORDER.includes(ctx.activeTab)
        ? ctx.activeTab : (bus ? "journey" : "vehicle");

    const liveryBand = el("div", {
        class: "vs-livery-band",
        title: liveryName,
        "aria-label": `${liveryName} livery`,
    });
    liveryBand.style.background = livery;

    const panelContent = activeTab === "journey"
        ? journeyPanel({ ...ctx, routeColor })
        : activeTab === "vehicle"
            ? vehiclePanel({ ...ctx, routeColor })
            : recordPanel(ctx);
    const panelChildren = [
        el("div", { class: "vs-panel-content" }, [panelContent]),
    ];
    if (bus) {
        panelChildren.push(el("button", {
            class: "vc-action vc-action-primary vs-focus-action",
            onClick: ctx.onTrack,
        }, ["Focus this bus on the map"]));
    }

    const shell = el("article", { class: "vehicle-sidebar" }, [
        el("header", { class: "vs-head" }, [
            liveryBand,
            el("div", { class: "vs-head-top" }, [
                el("div", {}, [
                    el("span", { class: "vs-kicker" },
                        [`Vehicle ${fleet || reg}`]),
                    el("span", { class: "vs-livery-name" }, [liveryName]),
                ]),
                el("button", {
                    class: "vs-close",
                    "aria-label": "Close vehicle details",
                    onClick: ctx.onClose,
                }, ["Close"]),
            ]),
            vehicleSummary(ctx),
            liveBand(ctx, routeColor),
        ]),
        el("nav", {
            class: "vs-tabs",
            role: "tablist",
            "aria-label": "Vehicle information",
        }, [
            tabButton("journey", "Journey", activeTab === "journey", ctx.onTabChange),
            tabButton("vehicle", "Vehicle", activeTab === "vehicle", ctx.onTabChange),
            tabButton("record", "Record", activeTab === "record", ctx.onTabChange),
        ]),
        el("section", {
            class: "vs-panel",
            id: `vs-panel-${activeTab}`,
            role: "tabpanel",
            "aria-labelledby": `vs-tab-${activeTab}`,
        }, panelChildren),
    ]);
    replaceContent(host, shell);
}

if (typeof window !== "undefined") {
    window.BBB = window.BBB || {};
    window.BBB.renderVehicleSidebar = renderVehicleSidebar;
}
