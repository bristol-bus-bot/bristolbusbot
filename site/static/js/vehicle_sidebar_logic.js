const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Format the compact YYYYMMDD service-date used by the audit snapshot. */
export function formatServiceDate(value) {
    const text = String(value || "");
    if (!/^\d{8}$/.test(text)) return text;
    const month = Number(text.slice(4, 6));
    if (month < 1 || month > 12) return text;
    return `${Number(text.slice(6, 8))} ${MONTHS[month - 1]} ${text.slice(0, 4)}`;
}

/** Turn a live vehicle into the wording and colour class shared by the sidebar. */
export function statusPresentation(bus) {
    if (!bus) return { text: "not currently running", cls: "vs-status-off" };
    if (bus.eventType === "depot")
        return { text: "at depot", cls: "vs-status-off" };
    if (bus.waitingAtOrigin || bus.eventType === "waiting")
        return { text: "waiting to depart", cls: "vs-status-waiting" };
    const delay = Number.parseInt(bus.delayMinutes, 10) || 0;
    if (delay >= 4) return { text: `${delay}m late`, cls: "vs-status-late" };
    if (delay <= -3) return { text: `${Math.abs(delay)}m early`, cls: "vs-status-early" };
    return { text: "on time", cls: "vs-status-ontime" };
}
