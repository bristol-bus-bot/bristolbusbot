import { test } from "node:test";
import assert from "node:assert/strict";

// board.js registers itself on window when loaded in the browser.
globalThis.window = globalThis.window || {};
const { sameService } = await import("../../static/js/board.js");

test("a live bus only replaces a scheduled row of its own operator", () => {
    const firstLive = { line: "13", operator: "FBRI" };
    assert.equal(sameService({ line: "13", operator: "FBRI" }, firstLive), true);
    assert.equal(sameService({ line: "13", operator: "SSWL" }, firstLive), false);
    assert.equal(sameService({ line: "14", operator: "FBRI" }, firstLive), false);
    // Rows without an operator code still match on the line alone.
    assert.equal(sameService({ line: "13" }, firstLive), true);
    assert.equal(sameService({ line: "13", operator: "NATX" }, { line: "13" }), true);
});
