import test from 'node:test';
import assert from 'node:assert/strict';
import { createBasemap, cartoRequest, styleUrl } from '../../static/js/basemap.js';

const tick = () => new Promise(resolve => setImmediate(resolve));
function setup(load = async () => {}, timeoutMs = 10000) {
    const events = {}, layers = new Set(), container = { dataset: {} }, attrs = new Set();
    const gl = { on: (name, fn) => { events[name] = fn; }, setStyle: url => { gl.style = url; } };
    const map = { getContainer: () => container, attributionControl: {
        addAttribution: a => attrs.add(a), removeAttribution: a => attrs.delete(a),
    }, removeLayer: l => layers.delete(l) };
    let vectorOptions;
    const L = {
        maplibreGL: options => { vectorOptions = options; return {
            addTo() { layers.add(this); return this; }, getMaplibreMap: () => gl,
        }; },
        tileLayer: url => ({ url, addTo() { layers.add(this); return this; }, setUrl(u) { this.url = u; } }),
    };
    const controller = createBasemap(map, { key: 'fixture-key', theme: 'day', L, load, timeoutMs });
    return { controller, gl, events, layers, container, attrs, options: () => vectorOptions };
}

test('key is attached only to HTTPS CARTO resources, including sprites, glyphs and tiles', () => {
    for (const url of ['https://basemaps.cartocdn.com/gl/style.json', 'https://tiles.basemaps.cartocdn.com/fonts/range.pbf?x=1']) {
        assert.equal(new URL(cartoRequest(url, 'a&b').url).searchParams.get('key'), 'a&b');
    }
    for (const url of ['https://basemaps.cartocdn.com.evil.test/style', 'https://example.com/tile', 'http://basemaps.cartocdn.com/tile']) {
        assert.equal(cartoRequest(url, 'private').url, url);
    }
    assert.match(styleUrl('night', 'test'), /dark-matter-gl-style/);
});

test('vector path makes no raster layer and changes style on the same map', async () => {
    const s = setup(); await tick(); s.events.idle();
    assert.equal(s.controller.mode, 'vector'); assert.equal(s.layers.size, 1);
    assert.equal(s.options().interactive, false);
    const original = [...s.layers][0];
    s.controller.setTheme('night'); s.events.idle();
    assert.match(s.gl.style, /dark-matter/);
    assert.equal([...s.layers][0], original); assert.equal(s.attrs.size, 1);
    s.controller.destroy(); assert.equal(s.layers.size, 0); assert.equal(s.attrs.size, 0);
});

for (const failure of ['error', 'webglcontextlost']) {
    test(`${failure} removes vector and keeps a single keyed, theme-aware fallback`, async () => {
        const s = setup(); await tick(); s.events[failure](); await tick();
        assert.equal(s.controller.mode, 'raster'); assert.equal(s.layers.size, 1);
        s.events[failure](); await tick(); assert.equal(s.layers.size, 1);
        s.controller.setTheme('night'); assert.match([...s.layers][0].url, /dark_all.*key=fixture-key/);
        s.controller.destroy();
    });
}

test('missing module falls back and a theme change during loading is respected', async () => {
    const s = setup(async () => { throw new Error('unavailable'); });
    s.controller.setTheme('night'); await tick();
    assert.equal(s.controller.mode, 'raster'); assert.match([...s.layers][0].url, /dark_all/);
    s.controller.destroy();
});

test('a stalled load falls back and a late import cannot replace it', async () => {
    let resolve; const s = setup(() => new Promise(r => { resolve = r; }), 5);
    await new Promise(r => setTimeout(r, 20));
    assert.equal(s.controller.mode, 'raster'); resolve(); await tick();
    assert.equal(s.layers.size, 1); assert.equal(s.options(), undefined); s.controller.destroy();
});

test('destroy during loading prevents late layers', async () => {
    const s = setup(); s.controller.destroy(); await tick(); assert.equal(s.layers.size, 0);
});
