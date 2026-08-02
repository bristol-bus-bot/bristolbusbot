/** Pure status classification and filter helpers shared by counts and markers. */

export const BUS_STATUSES = [
    "punctual", "early", "delayed", "waiting", "depot",
];

/** Return one and only one display status for a live vehicle. */
export function busStatus(bus) {
    if (bus?.eventType === "depot") return "depot";
    if (bus?.waitingAtOrigin || bus?.eventType === "waiting") return "waiting";
    if (bus?.eventType === "delayed" || bus?.eventType === "delay")
        return "delayed";
    if (bus?.eventType === "early") return "early";
    return "punctual";
}

export function countBusStatuses(buses) {
    const counts = Object.fromEntries(BUS_STATUSES.map(status => [status, 0]));
    for (const bus of Array.isArray(buses) ? buses : [])
        counts[busStatus(bus)] += 1;
    return counts;
}

export function nextStatusFilter(current, requested) {
    if (!BUS_STATUSES.includes(requested)) return null;
    return current === requested ? null : requested;
}

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

export function syncStatusFilterButtons(buttons, activeStatus) {
    for (const button of buttons || []) {
        const pressed = button.dataset?.statusFilter === activeStatus;
        button.setAttribute("aria-pressed", String(pressed));
    }
}

window.BBB = window.BBB || {};
Object.assign(window.BBB, {
    BUS_STATUSES,
    busStatus,
    countBusStatuses,
    nextStatusFilter,
    statusFilterVisual,
    syncStatusFilterButtons,
});
