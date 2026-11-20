import test from 'node:test';
import assert from 'node:assert/strict';
import { createPinchPointLayer, formatWindow, lineStyle, lostLevel, popupContent, routeLine } from '../../static/js/pinch_points.js';

function node(tag) {
    return { tag, children: [], dataset: {}, attributes: {}, hidden: false, textContent: '',
        className: '', relList: { add() {} },
        setAttribute(k, v) { this.attributes[k] = v; if (k.startsWith('data-')) this.dataset[k.slice(5)] = v; },
        addEventListener(type, fn) { this[`on${type}`] = fn; },
        appendChild(n) { this.children.push(n); },
        append(...n) { this.children.push(...n); },
        replaceChildren(...n) { this.children = n; } };
}
const dom = () => { globalThis.document = { createElement: node, createTextNode: text => ({ text }) }; };
const text = n => (n.text ?? '') + (n.children || []).map(text).join('');
const route = { route: '1 Bristol', operator: 'FBRI', timetable_min: 8, typical_min: 14.7, lost_min: 6.7, journeys: 177 };

test('colours step at 4 and 7 minutes lost', () => {
    assert.equal(lostLevel(2.1).level, 'low');
    assert.equal(lostLevel(4).level, 'mid');
    assert.equal(lostLevel(6.7).level, 'mid');
    assert.equal(lostLevel(9.9).level, 'high');
    assert.equal(lineStyle({ lost_min: 8, approximate: true }).dashArray, '6 6');
    assert.equal(lineStyle({ lost_min: 3, approximate: false }).dashArray, null);
});

test('the popup gives the stretch, when, and each route in the audit\'s words', () => {
    dom();
    assert.equal(routeLine(route),
        'Route 1 Bristol: the timetable allows 8 min, buses usually take 14.7 min (177 journeys)');
    assert.equal(formatWindow({ from: '20261023', to: '20261119' }), '23 Oct to 19 Nov');
    const popup = popupContent({ from: 'Bristol Temple Meads Station', to: 'The Centre',
        to_place: 'Bristol City Centre', routes: [route], approximate: true },
        { from: '20261023', to: '20261119' }, 'Weekdays, 4–7pm');
    const words = text(popup);
    assert.match(words, /Bristol Temple Meads Station → The Centre, Bristol City Centre/);
    assert.match(words, /Weekdays, 4–7pm\. Measured 23 Oct to 19 Nov\./);
    assert.match(words, /buses usually take 14.7 min/);
    assert.match(words, /drawn straight/);
});

test('the layer fetches the chosen band, says when there is no data, and hides cleanly', async () => {
    dom();
    const host = node('div');
    const added = [];
    const map = { removeLayer: l => added.splice(added.indexOf(l), 1) };
    const L = { geoJSON: (body, opts) => ({ body, opts, addTo(m) { added.push(this); return this; } }) };
    const urls = [];
    let reply = { available: true, window: { from: '20261023', to: '20261119' }, features: [
        { type: 'Feature', properties: { lost_min: 6.7, routes: [route] }, geometry: {} }] };
    const fetchImpl = async url => { urls.push(url); return { json: async () => reply }; };
    const layer = createPinchPointLayer(map, { host, fetchImpl, L });
    const panel = host.children[0];
    assert.equal(panel.hidden, true);
    layer.set({ band: 'am_peak', day: 'weekend' });
    layer.show();
    await new Promise(r => setTimeout(r, 0));
    assert.equal(urls.at(-1), '/api/pinch-points?band=am_peak&day=weekend');
    assert.equal(added.length, 1);
    const status = panel.children.find(c => c.attributes.role === 'status');
    assert.match(status.textContent, /1 section, 23 Oct to 19 Nov/);
    reply = { available: false, reason: 'no_current_data' };
    layer.set({ band: 'pm_peak', day: 'nonsense' });
    layer.show();
    await new Promise(r => setTimeout(r, 0));
    assert.equal(added.length, 0);
    assert.match(status.textContent, /No current data/);
    assert.deepEqual(layer.state, { band: 'pm_peak', day: 'weekend', visible: true });
    layer.hide();
    assert.equal(panel.hidden, true);
});
