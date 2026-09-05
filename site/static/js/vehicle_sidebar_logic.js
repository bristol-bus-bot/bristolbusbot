import { timingUnavailable } from "./status_filter.js";

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
    if (timingUnavailable(bus)) return {
        text: "Timing unavailable", longText: "Timing unavailable",
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

/** Build a bounded dot-strip model from the aggregate audit histogram. */
export function delayDotColumns(
    edgesInput, countsInput, maxDots = 600, maxColumnDots = 30,
) {
    const edges = Array.isArray(edgesInput) ? edgesInput.map(Number) : [];
    const counts = Array.isArray(countsInput) ? countsInput.map(Number) : [];
    if (edges.length < 2 || counts.length !== edges.length + 1
            || edges.some((value, index) => !Number.isFinite(value)
                || (index > 0 && value <= edges[index - 1]))
            || counts.some(value => !Number.isInteger(value) || value < 0))
        return null;

    const total = counts.reduce((sum, value) => sum + value, 0);
    if (!total) return null;
    const minS = edges[0];
    const maxS = edges[edges.length - 1];
    const range = maxS - minS;
    const largestBucket = Math.max(...counts);
    const unit = Math.max(
        1,
        Math.ceil(total / Math.max(1, maxDots)),
        Math.ceil(largestBucket / Math.max(1, maxColumnDots)),
    );
    const percentage = value => Math.max(
        0, Math.min(100, ((value - minS) / range) * 100));
    const centres = counts.map((_, index) => {
        if (index === 0) return minS;
        if (index === edges.length) return maxS;
        return (edges[index - 1] + edges[index]) / 2;
    });

    return {
        total,
        unit,
        axis: {
            minimumSeconds: minS,
            maximumSeconds: maxS,
            zeroPct: percentage(0),
            onTimeStartPct: percentage(-60),
            onTimeEndPct: percentage(360),
        },
        columns: counts.map((count, index) => {
            const centre = centres[index];
            return {
                count,
                dots: count ? Math.ceil(count / unit) : 0,
                leftPct: percentage(centre),
                kind: centre < -60 ? "early" : centre >= 360 ? "late" : "on-time",
            };
        }),
    };
}
