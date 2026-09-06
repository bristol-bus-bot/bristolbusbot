import assert from 'node:assert/strict';
import test from 'node:test';
import { MarkerMotion, observationPath, positionOnPath, projectPosition, positionAge } from '../../static/js/marker_motion.js';

test('sparse route vertices do not pull a bus forward to a stop', () => {
    const shape = [[51.46, -2.53], [51.46, -2.52]];
    const end = [51.46, -2.526];
    assert.deepEqual(projectPosition(end, shape).point, end);
    const path = observationPath([51.46, -2.529], end, [shape]);
    assert.deepEqual(positionOnPath(path, 1), end);
    assert(positionOnPath(path, .5)[1] < end[1]);
});

test('ambiguous, reverse and distant route paths fall back to exact GPS endpoints', () => {
    const start = [51.46, -2.53], end = [51.46, -2.52];
    assert.deepEqual(observationPath(start, end, [[end, start]]), [start, end]);
    assert.deepEqual(observationPath(start, end, [[start, end], [start, end]]), [start, end]);
    assert.equal(projectPosition([51.5, -2.52], [start, end]), null);
});

test('duplicates and older observations never restart; interrupted frames cannot move markers', () => {
    let now = 0, next = 0;
    const frames = new Map(), cancelled = [];
    const motion = new MarkerMotion({ clock: () => now, reducedMotion: () => false,
        requestFrame: f => { frames.set(++next, f); return next; }, cancelFrame: id => cancelled.push(id) });
    let point = [51.46, -2.53];
    const marker = { getLatLng: () => ({ lat: point[0], lng: point[1] }), setLatLng: p => { point = p; } };
    const bus = { vehicleRef: 'bus', latitude: point[0], longitude: point[1], recordedAt: '2026-09-06T14:00:00Z', tripId: 'trip' };
    motion.update(marker, bus);
    const newer = { ...bus, longitude: -2.525, recordedAt: '2026-09-06T14:00:30Z' };
    motion.update(marker, newer);const first = frames.get(next);const count = next;
    motion.update(marker, newer);motion.update(marker, bus);assert.equal(next, count);
    now = 3000;first(now);const intermediate = [...point];
    assert(Math.abs(point[1] - (-2.52875)) < 0.000001); // quarter of a relaxed 12-second glide
    motion.update(marker, { ...newer, longitude: -2.52, recordedAt: '2026-09-06T14:01:00Z' });
    first(3000);assert.deepEqual(point, intermediate);assert(cancelled.length > 0);
    frames.get(next)(15000);assert.deepEqual(point, [51.46, -2.52]);
    motion.remove('bus');assert.equal(motion.states.size, 0);
});

test('age follows observation time, not browser refresh time', () => {
    assert.equal(positionAge({ recordedAt: '2026-09-06T14:00:00Z' }, Date.parse('2026-09-06T14:00:30Z')), 'Location updated 30s ago');
    assert.equal(positionAge({}), 'Location update time unavailable');
});

test('journey changes and reduced motion place the marker directly without a phantom drive', () => {
    let point = [51.46, -2.53], calls = 0;
    const marker = { getLatLng: () => ({lat:point[0], lng:point[1]}), setLatLng: p => { point=p; } };
    const motion = new MarkerMotion({requestFrame: () => ++calls, cancelFrame: () => {}, clock: () => 0, reducedMotion: () => true});
    const bus = {vehicleRef:'bus',latitude:point[0],longitude:point[1],recordedAt:'2026-09-06T14:00:00Z',tripId:'a'};
    motion.update(marker,bus);
    motion.update(marker,{...bus,longitude:-2.52,recordedAt:'2026-09-06T14:00:30Z'});
    assert.equal(calls,0);assert.deepEqual(point,[51.46,-2.52]);
    motion.reducedMotion=()=>false;
    motion.update(marker,{...bus,tripId:'b',recordedAt:'2026-09-06T14:01:00Z'});
    assert.equal(calls,0);assert.deepEqual(point,[51.46,-2.53]);
});
