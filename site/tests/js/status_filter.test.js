import assert from "node:assert/strict";
import test from "node:test";

globalThis.window = { BBB: {} };
const {
    busStatus,
    countBusStatuses,
    nextStatusFilter,
    statusFilterVisual,
    syncStatusFilterButtons,
} = await import("../../static/js/status_filter.js");

test("status classifier is exclusive and gives depot then waiting precedence", () => {
    assert.equal(busStatus({ eventType: "depot", waitingAtOrigin: true }), "depot");
    assert.equal(busStatus({ eventType: "punctual", waitingAtOrigin: true }), "waiting");
    assert.equal(busStatus({ eventType: "waiting" }), "waiting");
    assert.equal(busStatus({ eventType: "delay" }), "delayed");
    assert.equal(busStatus({ eventType: "delayed" }), "delayed");
    assert.equal(busStatus({ eventType: "early" }), "early");
    assert.equal(busStatus({ eventType: "punctual" }), "punctual");
});

test("header counts cannot double-count waiting vehicles", () => {
    const buses = [
        { eventType: "punctual", waitingAtOrigin: true },
        { eventType: "punctual" },
        { eventType: "early" },
        { eventType: "delayed" },
        { eventType: "depot", waitingAtOrigin: true },
    ];
    const counts = countBusStatuses(buses);
    assert.deepEqual(counts, {
        punctual: 1, early: 1, delayed: 1, waiting: 1, depot: 1,
    });
    assert.equal(Object.values(counts).reduce((sum, count) => sum + count, 0),
                 buses.length);
});

test("single-select toggles and marker modes are deterministic", () => {
    assert.equal(nextStatusFilter(null, "early"), "early");
    assert.equal(nextStatusFilter("early", "early"), null);
    assert.equal(nextStatusFilter("early", "depot"), "depot");
    assert.equal(nextStatusFilter("early", "unknown"), null);

    const matching = statusFilterVisual({ eventType: "early" }, "early");
    const other = statusFilterVisual({ eventType: "punctual" }, "early");
    assert.deepEqual(matching, {
        matches: true, hollow: false,
        mode: "status-early-match", zOffset: 1600,
    });
    assert.deepEqual(other, {
        matches: false, hollow: true,
        mode: "status-early-other", zOffset: 400,
    });
});

test("button aria state follows the active filter", () => {
    const buttons = ["early", "depot"].map(status => ({
        dataset: { statusFilter: status },
        attributes: {},
        setAttribute(name, value) { this.attributes[name] = value; },
    }));
    syncStatusFilterButtons(buttons, "early");
    assert.equal(buttons[0].attributes["aria-pressed"], "true");
    assert.equal(buttons[1].attributes["aria-pressed"], "false");
});
