import assert from "node:assert/strict";
import test from "node:test";

import {
    buildFeaturedPostIndex,
    featuredPostForBus,
} from "../../static/js/featured_posts.js";

const post = {
    operatorRef: "FBRI", vehicleRef: "FBRI-100", journeyRef: "J-1",
    originAimedDeparture: "2026-07-28T12:00:00Z",
    postUrl: "https://bsky.app/profile/bristolbusbot.live/post/abc",
    postText: "Exact published text", timestamp: "2026-07-28T12:10:00Z",
};

test("a post matches only its exact operator, vehicle, journey and departure", () => {
    const index = buildFeaturedPostIndex([post]);
    const bus = {
        operatorRef: "FBRI", vehicleRef: "FBRI-100", journeyCode: "J-1",
        originAimedDep: "2026-07-28T12:00:00Z",
    };
    assert.equal(featuredPostForBus(index, bus)?.postText, "Exact published text");
    assert.equal(featuredPostForBus(index, { ...bus, operatorRef: "OTHER" }), null);
    assert.equal(featuredPostForBus(index, { ...bus, journeyCode: "J-2" }), null);
    assert.equal(featuredPostForBus(index, { ...bus, originAimedDep: "13:00:00" }), null);
});

test("incomplete provenance is never indexed", () => {
    assert.deepEqual(buildFeaturedPostIndex([{ ...post, journeyRef: "" }]), {});
    assert.deepEqual(buildFeaturedPostIndex([{ ...post, postUrl: "" }]), {});
});
