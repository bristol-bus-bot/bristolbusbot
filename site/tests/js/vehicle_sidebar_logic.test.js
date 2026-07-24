import assert from "node:assert/strict";
import test from "node:test";

import {
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
