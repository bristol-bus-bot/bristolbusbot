/** Exact-journey matching for recent bot posts. */
export function featuredPostKey(item) {
    return `${String(item?.operatorRef || "").toUpperCase()}\u0000${String(item?.vehicleRef || "")}`;
}

export function buildFeaturedPostIndex(posts) {
    const index = {};
    (Array.isArray(posts) ? posts : []).forEach(post => {
        if (!post?.postUrl || !post?.vehicleRef || !post?.operatorRef
                || !post?.journeyRef || !post?.originAimedDeparture) return;
        const key = featuredPostKey(post);
        if (!index[key]) index[key] = {
            postUrl: post.postUrl,
            postText: post.postText,
            timestamp: post.timestamp,
            journeyRef: post.journeyRef,
            originAimedDeparture: post.originAimedDeparture,
        };
    });
    return index;
}

export function featuredPostForBus(index, bus) {
    const post = index?.[featuredPostKey(bus)];
    if (!post) return null;
    if (String(post.journeyRef || "") !== String(bus?.journeyCode || "")) return null;
    if (String(post.originAimedDeparture || "") !== String(bus?.originAimedDep || "")) return null;
    return post;
}

if (typeof window !== "undefined") {
    window.BBB = window.BBB || {};
    Object.assign(window.BBB, {
        featuredPostKey,
        buildFeaturedPostIndex,
        featuredPostForBus,
    });
}
