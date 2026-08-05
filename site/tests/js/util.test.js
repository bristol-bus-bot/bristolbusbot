import assert from "node:assert/strict";
import test from "node:test";

import { el } from "../../static/js/util.js";


function fakeDocument() {
    return {
        createElement(tag) {
            return {
                tag,
                attributes: {},
                children: [],
                relList: { add() {} },
                setAttribute(name, value) { this.attributes[name] = value; },
                addEventListener() {},
                appendChild(child) { this.children.push(child); },
            };
        },
        createTextNode(text) { return { text }; },
    };
}


test("time elements accept a machine-readable datetime", () => {
    globalThis.document = fakeDocument();
    try {
        const node = el("time", { datetime: "2026-08-05T23:30:00Z" }, ["now"]);
        assert.equal(node.attributes.datetime, "2026-08-05T23:30:00Z");
        assert.equal(node.children[0].text, "now");
    } finally {
        delete globalThis.document;
    }
});


test("datetime remains rejected on unrelated elements", () => {
    globalThis.document = fakeDocument();
    try {
        assert.throws(
            () => el("div", { datetime: "2026-08-05T23:30:00Z" }),
            /Unsafe or unsupported attribute: datetime on <div>/,
        );
    } finally {
        delete globalThis.document;
    }
});
