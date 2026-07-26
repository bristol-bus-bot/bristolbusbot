import assert from "node:assert/strict";
import test from "node:test";

import {
    delayDotColumns,
    formatServiceDate,
    statusPresentation,
} from "../../static/js/vehicle_sidebar_logic.js";

test("audit service dates are formatted for people and malformed values fail closed", () => {
    assert.equal(formatServiceDate("20260714"), "14 Jul 2026");
    assert.equal(formatServiceDate("20261314"), "20261314");
    assert.equal(formatServiceDate("unknown"), "unknown");
});

test("live status wording uses the public punctuality thresholds", () => {
    assert.deepEqual(statusPresentation(null), {
        text: "not currently running",
        longText: "not currently running",
        cls: "vs-status-off",
        shape: "vs-shape-off",
    });
    assert.deepEqual(
        statusPresentation({ eventType: "depot" }),
        {
            text: "at depot", longText: "at depot",
            cls: "vs-status-off", shape: "vs-shape-off",
        },
    );
    assert.equal(
        statusPresentation({ waitingAtOrigin: true }).shape,
        "vs-shape-waiting",
    );
    assert.equal(statusPresentation({ delayMinutes: 3 }).text, "on time");
    assert.equal(statusPresentation({ delayMinutes: 4 }).longText, "4 minutes late");
    assert.equal(statusPresentation({ delayMinutes: -3 }).longText, "3 minutes early");
});

test("delay dots use a signed axis with zero inside the on-time band", () => {
    const model = delayDotColumns(
        [-600, -60, 0, 360, 1200],
        [2, 3, 4, 5, 6, 7],
    );
    assert.equal(model.total, 27);
    assert.equal(model.unit, 1);
    assert.ok(Math.abs(model.axis.zeroPct - (100 / 3)) < 1e-9);
    assert.equal(model.axis.onTimeStartPct, 30);
    assert.ok(Math.abs(model.axis.onTimeEndPct - (160 / 3)) < 1e-9);
    assert.equal(model.columns[1].kind, "early");
    assert.equal(model.columns[2].kind, "on-time");
    assert.equal(model.columns[4].kind, "late");
});

test("delay dots group dense profiles and malformed histograms fail closed", () => {
    const model = delayDotColumns([-600, 0, 1200], [0, 1000, 0, 0]);
    assert.equal(model.unit, 34);
    assert.equal(model.columns[1].dots, 30);
    assert.equal(delayDotColumns([-600, 0], [1, 2]), null);
    assert.equal(delayDotColumns([-600, -600], [1, 2, 3]), null);
});
