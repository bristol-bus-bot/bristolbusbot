/** Pure status classification and filter helpers shared by counts and markers. */
/// <reference path="../../types.d.ts" />

/** @type {readonly (BusStatus | string | null)[]} */
export const BUS_STATUSES = [
    "punctual", "early", "delayed", "waiting", "depot", "unknown",
];

/**
 * @param {LiveBus | null | undefined} bus
 */
export function timingUnavailable(bus) {
    if (bus?.eventType === "waiting") return false;
    return bus?.eventType === "unknown" || bus?.hasSchedule === false
        || bus?.delayMinutes === null;
}

/** Return one and only one display status for a live vehicle. */
/**
 * @param {LiveBus | null | undefined} bus
 * @returns {BusStatus}
 */
export function busStatus(bus) {
    if (bus?.eventType === "depot") return "depot";
    if (timingUnavailable(bus)) return "unknown";
    if (bus?.waitingAtOrigin || bus?.eventType === "waiting") return "waiting";
    // @ts-expect-error Keep the legacy fallback; the Python API emits "delayed".
    if (bus?.eventType === "delayed" || bus?.eventType === "delay")
        return "delayed";
    if (bus?.eventType === "early") return "early";
    return bus?.eventType === "punctual" ? "punctual" : "unknown";
}

/**
 * @param {LiveBus[] | null | undefined} buses
 */
export function countBusStatuses(buses) {
    const counts = Object.fromEntries(BUS_STATUSES.map(status => [status, 0]));
    for (const bus of Array.isArray(buses) ? buses : [])
        counts[busStatus(bus)] += 1;
    return counts;
}

/**
 * @param {BusStatus | null} current
 * @param {string} requested
 */
export function nextStatusFilter(current, requested) {
    if (!BUS_STATUSES.includes(requested)) return null;
    return current === requested ? null : requested;
}

/**
 * @param {LiveBus | null | undefined} bus
 * @param {BusStatus | null} activeStatus
 */
export function statusFilterVisual(bus, activeStatus) {
    if (!BUS_STATUSES.includes(activeStatus)) return null;
    const matches = busStatus(bus) === activeStatus;
    return {
        matches,
        hollow: !matches,
        mode: `status-${activeStatus}-${matches ? "match" : "other"}`,
        zOffset: matches ? 1600 : 400,
    };
}

/**
 * @param {Iterable<Pick<HTMLElement, 'dataset' | 'setAttribute'>> | null} buttons
 * @param {BusStatus | null} activeStatus
 */
export function syncStatusFilterButtons(buttons, activeStatus) {
    for (const button of buttons || []) {
        const pressed = button.dataset?.statusFilter === activeStatus;
        button.setAttribute("aria-pressed", String(pressed));
    }
}

if (typeof window !== "undefined") {
    window.BBB = window.BBB || {};
    Object.assign(window.BBB, {
        BUS_STATUSES,
        busStatus,
        countBusStatuses,
        nextStatusFilter,
        statusFilterVisual,
        syncStatusFilterButtons,
    });
}
