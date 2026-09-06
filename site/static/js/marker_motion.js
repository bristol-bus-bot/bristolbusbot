/** Animate observations, never a prediction of where a vehicle should be. */
export function metres(a, b) {
    const x = (b[1] - a[1]) * Math.cos((a[0] + b[0]) * Math.PI / 360);
    return Math.hypot(b[0] - a[0], x) * 111195;
}

export function projectPosition(point, points) {
    const candidates = [];
    let along = 0;
    const scale = Math.cos(point[0] * Math.PI / 180);
    for (let i = 0; i < points.length - 1; i++) {
        const a = points[i], b = points[i + 1];
        const ax = (a[1] - point[1]) * scale * 111195;
        const ay = (a[0] - point[0]) * 111195;
        const dx = (b[1] - a[1]) * scale * 111195;
        const dy = (b[0] - a[0]) * 111195;
        const length = Math.hypot(dx, dy);
        if (!length) continue;
        const fraction = Math.max(0, Math.min(1, -(ax * dx + ay * dy) / length ** 2));
        candidates.push({ index: i, along: along + fraction * length,
            distance: Math.hypot(ax + fraction * dx, ay + fraction * dy),
            point: [a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction] });
        along += length;
    }
    const best = candidates.reduce((a, b) => !a || b.distance < a.distance ? b : a, null);
    if (!best || best.distance > 35 || candidates.some(p =>
        p.distance <= best.distance + 10 && Math.abs(p.along - best.along) > 75)) return null;
    return best;
}

export function observationPath(start, end, shapes = []) {
    const paths = [];
    for (const points of shapes) {
        if (!Array.isArray(points) || points.length > 5000 || !points.every(p =>
            Array.isArray(p) && p.length === 2 && p.every(Number.isFinite))) continue;
        const a = projectPosition(start, points), b = projectPosition(end, points);
        if (!a || !b || b.along < a.along) continue;
        if (b.along - a.along > metres(start, end) * 1.8 + 100) continue;
        paths.push([start, a.point, ...points.slice(a.index + 1, b.index + 1), b.point, end]);
    }
    // Do not arbitrarily choose between multiple route variants.
    return paths.length === 1 ? paths[0] : [start, end];
}

export function positionOnPath(path, fraction) {
    const lengths = path.slice(1).map((p, i) => metres(path[i], p));
    const total = lengths.reduce((a, b) => a + b, 0);
    let remaining = Math.max(0, Math.min(1, fraction)) * total;
    for (let i = 0; i < lengths.length; i++) {
        if (remaining <= lengths[i] && lengths[i] > 0) {
            const t = remaining / lengths[i];
            return [path[i][0] + (path[i + 1][0] - path[i][0]) * t,
                path[i][1] + (path[i + 1][1] - path[i][1]) * t];
        }
        remaining -= lengths[i];
    }
    return path[path.length - 1];
}

export class MarkerMotion {
    constructor({ requestFrame = callback => requestAnimationFrame(callback),
        cancelFrame = id => cancelAnimationFrame(id),
        clock = () => performance.now(), reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches } = {}) {
        Object.assign(this, { requestFrame, cancelFrame, clock, reducedMotion });
        this.states = new Map();
    }

    remove(ref) {
        const previous = this.states.get(ref);
        if (previous?.frame != null) this.cancelFrame(previous.frame);
        this.states.delete(ref);
    }

    update(marker, bus, shapes = []) {
        const ref = bus.vehicleRef;
        const end = [bus.latitude, bus.longitude];
        const stamp = Date.parse(bus.recordedAt);
        if (!Number.isFinite(stamp) || !end.every(Number.isFinite)) return;
        const previous = this.states.get(ref);
        if (previous && stamp <= previous.stamp) return;
        const journey = [bus.operatorRef, bus.line, bus.directionRef, bus.journeyCode, bus.originAimedDep, bus.tripId].join('|');
        const start = marker.getLatLng();
        const from = [start.lat, start.lng];
        const distance = metres(from, end);
        const changedJourney = previous && previous.journey !== journey;
        this.remove(ref);
        const state = { stamp, journey, frame: null };
        this.states.set(ref, state);
        if (!previous || changedJourney || distance < 1 || distance > 2000 || this.reducedMotion()) {
            marker.setLatLng(end);
            return;
        }
        const path = observationPath(from, end, shapes);
        // Short catch-up, no twelve-second artificial lag or extrapolation.
        const duration = Math.min(3000, Math.max(600, (stamp - previous.stamp) * .15));
        const started = this.clock();
        const step = now => {
            if (this.states.get(ref) !== state) return;
            const t = Math.min(1, Math.max(0, (now - started) / duration));
            marker.setLatLng(positionOnPath(path, t * t * (3 - 2 * t)));
            state.frame = t < 1 ? this.requestFrame(step) : null;
        };
        state.frame = this.requestFrame(step);
    }
}

export function positionAge(bus, now = Date.now()) {
    const stamp = Date.parse(bus?.recordedAt);
    if (!Number.isFinite(stamp)) return 'Location update time unavailable';
    const seconds = Math.max(0, Math.floor((now - stamp) / 1000));
    return seconds < 60 ? `Location updated ${seconds}s ago`
        : `Location updated ${Math.floor(seconds / 60)}m ${seconds % 60}s ago`;
}

export function refreshPositionAges(root = document) {
    for (const node of root.querySelectorAll('[data-position-recorded]')) {
        node.textContent = positionAge({ recordedAt: node.dataset.positionRecorded });
    }
}

if (typeof window !== 'undefined') {
    window.BBB = window.BBB || {};
    Object.assign(window.BBB, { MarkerMotion, refreshPositionAges });
}
