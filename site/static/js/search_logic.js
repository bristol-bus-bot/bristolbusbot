/**
 * Pure search and route-selection rules. Keeping these separate from the DOM
 * makes the user-facing thresholds testable without a browser framework.
 */

function compact(value) {
    return String(value ?? "").toLowerCase().replace(/\s+/g, "");
}

function routeParts(routeKey) {
    const parts = String(routeKey).split("_");
    return { operator: parts[0], line: parts.slice(1).join("_") };
}

export function isBusOnRoute(bus, routeKey) {
    const { operator, line } = routeParts(routeKey);
    return Boolean(bus
        && bus.operatorRef === operator
        && bus.line === line
        && bus.eventType !== "depot");
}

export function getFleetMatches(
    fleetData, latestBusData, query, limit = 8,
) {
    if (!Array.isArray(fleetData) || !fleetData.length)
        return { matches: [], total: 0 };

    const q = String(query ?? "").toLowerCase().trim();
    if (!q) return { matches: [], total: 0 };
    const qCompact = compact(q);

    const activeFleetCodes = new Set();
    const activeRegs = new Set();
    for (const bus of Array.isArray(latestBusData) ? latestBusData : []) {
        if (bus.fleetNumber != null)
            activeFleetCodes.add(compact(bus.fleetNumber));
        if (bus.reg) activeRegs.add(compact(bus.reg));
    }

    const filtered = fleetData.filter(vehicle => {
        const fleetCode = compact(vehicle.fleet_code);
        const fleetNumber = compact(vehicle.fleet_number);
        if ((fleetCode && fleetCode === qCompact)
                || (fleetNumber && fleetNumber === qCompact))
            return true;

        if (qCompact.length >= 3
                && (compact(vehicle.reg).includes(qCompact)
                    || compact(vehicle.previous_reg).includes(qCompact)))
            return true;

        return q.length >= 4
            && (String(vehicle.name ?? "").toLowerCase().includes(q)
                || String(vehicle.model ?? "").toLowerCase().includes(q));
    });

    const tagged = filtered.map(vehicle => {
        const fleetCode = compact(vehicle.fleet_code || vehicle.fleet_number);
        const reg = compact(vehicle.reg);
        return {
            ...vehicle,
            isLive: (fleetCode && activeFleetCodes.has(fleetCode))
                || (reg && activeRegs.has(reg)),
        };
    });

    tagged.sort((a, b) => {
        if (a.isLive !== b.isLive) return a.isLive ? -1 : 1;
        const aNumber = Number(a.fleet_number);
        const bNumber = Number(b.fleet_number);
        if (Number.isFinite(aNumber) && Number.isFinite(bNumber)
                && aNumber !== bNumber)
            return aNumber - bNumber;
        return String(a.fleet_code ?? "").localeCompare(
            String(b.fleet_code ?? ""));
    });

    return { matches: tagged.slice(0, limit), total: tagged.length };
}

export function getRouteMatches(
    routeIndex, latestBusData, query, operatorNames = {},
) {
    const q = String(query ?? "").trim().toUpperCase();
    if (!q) return [];

    const buses = Array.isArray(latestBusData) ? latestBusData : [];
    const candidates = new Map();
    for (const [routeKey, variants] of Object.entries(routeIndex ?? {})) {
        const { operator, line } = routeParts(routeKey);
        if (line.toUpperCase() !== q) continue;
        candidates.set(routeKey, {
            routeKey,
            operator,
            line,
            variants: variants.length,
        });
    }

    // A live route remains a real search result even when its timetable came
    // from TNDS and has no GTFS shape. Geometry can be recovered from the
    // matched live journeys after selection.
    for (const bus of buses) {
        if (!bus?.operatorRef || String(bus.line ?? "").toUpperCase() !== q
                || bus.eventType === "depot")
            continue;
        const routeKey = `${bus.operatorRef}_${bus.line}`;
        if (!candidates.has(routeKey)) {
            candidates.set(routeKey, {
                routeKey,
                operator: bus.operatorRef,
                line: bus.line,
                variants: 0,
            });
        }
    }

    const matches = [];
    for (const candidate of candidates.values()) {
        const active = buses.filter(
            bus => isBusOnRoute(bus, candidate.routeKey));
        matches.push({
            ...candidate,
            activeBuses: active.length,
            destinations: [...new Set(
                active.map(bus => bus.destination).filter(Boolean))],
            operatorName: operatorNames[candidate.operator]
                || candidate.operator,
        });
    }
    return matches.sort((a, b) =>
        b.activeBuses - a.activeBuses
        || a.operatorName.localeCompare(b.operatorName));
}

export function routeFallbackCandidates(latestBusData, routeKey, limit = 4) {
    const active = (Array.isArray(latestBusData) ? latestBusData : [])
        .filter(bus => isBusOnRoute(bus, routeKey))
        .sort((a, b) =>
            Number(Boolean(b.tripId)) - Number(Boolean(a.tripId))
            || Number(Boolean(b.hasSchedule)) - Number(Boolean(a.hasSchedule)));
    const chosen = [];
    const seen = new Set();
    for (const bus of active) {
        const direction = bus.directionId ?? bus.directionRef
            ?? bus.destination ?? "unknown";
        if (seen.has(direction)) continue;
        seen.add(direction);
        chosen.push(bus);
        if (chosen.length >= limit) break;
    }
    return chosen;
}

export function fleetResultLimit(routeMatches) {
    return routeMatches.length ? 3 : 8;
}

export function routeSelectionSheetState(mobile) {
    return mobile ? "peek" : "collapsed";
}

export function routeFitOptions(mobile, viewportHeight = 800) {
    if (!mobile) return { padding: [40, 40] };
    return {
        paddingTopLeft: [32, 32],
        paddingBottomRight: [
            32,
            Math.max(220, Math.round(viewportHeight * 0.36)),
        ],
    };
}

export function searchAnnouncement(routeCount, stopCount, busCount) {
    return `${routeCount} route${routeCount === 1 ? "" : "s"}, `
        + `${stopCount} stop${stopCount === 1 ? "" : "s"} and `
        + `${busCount} bus${busCount === 1 ? "" : "es"} found`;
}

if (typeof window !== "undefined") {
    window.BBB = window.BBB || {};
    Object.assign(window.BBB, {
        fleetResultLimit,
        getFleetMatches,
        getRouteMatches,
        isBusOnRoute,
        routeFallbackCandidates,
        routeFitOptions,
        routeSelectionSheetState,
        searchAnnouncement,
    });
}
