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
    if (!bus) return {
        text: "not currently running",
        longText: "not currently running",
        cls: "vs-status-off",
        shape: "vs-shape-off",
    };
    if (bus.eventType === "depot")
        return {
            text: "at depot", longText: "at depot",
            cls: "vs-status-off", shape: "vs-shape-off",
        };
    if (bus.waitingAtOrigin || bus.eventType === "waiting")
        return {
            text: "waiting to depart", longText: "waiting to depart",
            cls: "vs-status-waiting", shape: "vs-shape-waiting",
        };
    const delay = Number.parseInt(bus.delayMinutes, 10) || 0;
    if (delay >= 4) return {
        text: `${delay}m late`, longText: `${delay} minutes late`,
        cls: "vs-status-late", shape: "vs-shape-late",
    };
    if (delay <= -3) return {
        text: `${Math.abs(delay)}m early`,
        longText: `${Math.abs(delay)} minutes early`,
        cls: "vs-status-early", shape: "vs-shape-early",
    };
    return {
        text: "on time", longText: "on time",
        cls: "vs-status-ontime", shape: "vs-shape-ontime",
    };
}
