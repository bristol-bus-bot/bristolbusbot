import assert from "node:assert/strict";
import test from "node:test";

globalThis.window = { BBB: {} };
const { busIcon, featuredPostBadge } = await import(
    "../../static/js/map_render.js");

test("featured journey marker has an explicit bot-post speech bubble", () => {
    assert.equal(featuredPostBadge(false), "");
    const badge = featuredPostBadge(true);
    assert.match(badge, /busbot-post-badge/);
    assert.match(badge, /fill="#F59E0B"/);
    assert.match(badge, /<circle[^>]+cx="26\.8"/);
});

test("featured bus icon is larger and exposes the post state accessibly", () => {
    globalThis.L = { divIcon: options => options };
    const bus = {
        eventType: "punctual", waitingAtOrigin: false, bearing: 90,
        livery: { left: "#006688" },
    };
    const featured = busIcon(bus, true);
    const ordinary = busIcon(bus, false);
    assert.deepEqual(featured.iconSize, [36, 36]);
    assert.deepEqual(ordinary.iconSize, [28, 28]);
    assert.match(featured.className, /featured/);
    assert.match(featured.html, /aria-label="Bot posted about this journey"/);
    assert.match(featured.html, /busbot-post-badge/);
    assert.doesNotMatch(ordinary.html, /busbot-post-badge/);
    delete globalThis.L;
});
