import assert from "node:assert/strict";
import test from "node:test";

globalThis.window = { BBB: {} };
const { busIcon, depotIcon, featuredPostBadge } = await import(
    "../../static/js/map_render.js");

test("featured journey marker has an explicit bot-post speech bubble", () => {
    assert.equal(featuredPostBadge(false), "");
    const badge = featuredPostBadge(true);
    assert.match(badge, /busbot-post-badge/);
    assert.match(badge, /fill="#FFDD00"/);
    assert.match(badge, /<path/);
});

test("markers use a 44px tap target and expose the journey state accessibly", () => {
    globalThis.L = { divIcon: options => options };
    const bus = {
        eventType: "punctual", waitingAtOrigin: false, bearing: 90,
        line: "75", destination: "Hengrove", livery: { left: "#006688" },
    };
    const featured = busIcon(bus, true);
    const ordinary = busIcon(bus, false);
    assert.deepEqual(featured.iconSize, [44, 44]);
    assert.deepEqual(ordinary.iconSize, [44, 44]);
    assert.match(featured.className, /featured/);
    assert.match(featured.html, /aria-label="Route 75, to Hengrove, on time, heading 90 degrees, bot posted about this journey"/);
    assert.match(featured.html, /busbot-post-badge/);
    assert.doesNotMatch(ordinary.html, /busbot-post-badge/);
    delete globalThis.L;
});

test("late and early remain different shapes when hollow", () => {
    globalThis.L = { divIcon: options => options };
    const common = { waitingAtOrigin: false, bearing: 180,
                     livery: { left: "#006688" } };
    const late = busIcon({ ...common, eventType: "delayed" }, false,
                         { hollow: true });
    const early = busIcon({ ...common, eventType: "early" }, false,
                          { hollow: true });
    assert.match(late.html, /<rect data-marker-core="delayed"/);
    assert.match(late.html, /stroke="var\(--marker-status-late/);
    assert.match(early.html, /<path data-marker-core="early"/);
    assert.match(early.html, /stroke="var\(--marker-status-early/);
    assert.doesNotMatch(early.html, /rotate\(45/);
    delete globalThis.L;
});

test("waiting marker has no livery ring or direction nose", () => {
    globalThis.L = { divIcon: options => options };
    const waiting = busIcon({
        eventType: "waiting", waitingAtOrigin: true, bearing: 90,
        livery: { left: "#006688" },
    }, false);
    assert.match(waiting.html, /data-marker-ring="waiting"/);
    assert.match(waiting.html, /data-marker-core="waiting"/);
    assert.doesNotMatch(waiting.html, /#006688/);
    assert.doesNotMatch(waiting.html, /data-marker-nose/);
    delete globalThis.L;
});

test("depot icon is a quiet dot with a hollow filtered-out variant", () => {
    globalThis.L = { divIcon: options => options };
    const ordinary = depotIcon({ left: "#006688" });
    const filtered = depotIcon({ left: "#006688" }, { hollow: true });
    assert.deepEqual(ordinary.iconSize, [44, 44]);
    assert.match(ordinary.html, /data-marker-core="depot"/);
    assert.doesNotMatch(ordinary.html, /#006688/);
    assert.doesNotMatch(ordinary.className, /filtered-out/);
    assert.match(filtered.html, /fill="none"/);
    assert.match(filtered.className, /filtered-out/);
    delete globalThis.L;
});
