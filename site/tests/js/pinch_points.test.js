import test from 'node:test';
import assert from 'node:assert/strict';
import { createPinchPointLayer, formatWindow, lineStyle, lostLevel, popupContent } from '../../static/js/pinch_points.js';

function node(tag) {
    return { tag, children: [], dataset: {}, attributes: {}, hidden: false, textContent: '',
        className: '', style: {}, relList: { add() {} },
        setAttribute(k, v) { this.attributes[k] = v; if (k.startsWith('data-')) this.dataset[k.slice(5)] = v; },
        addEventListener(type, fn) { this[`on${type}`] = fn; },
        appendChild(n) { this.children.push(n); },
        append(...n) { this.children.push(...n); },
        replaceChildren(...n) { this.children = n; } };
}
const dom = () => { globalThis.document = { createElement: node, createTextNode: text => ({ text }) }; };
const text = n => (n.text ?? '') + (n.children || []).map(text).join('|');
const find = (n, cls) => (n.className === cls ? [n] : []).concat(...(n.children || []).map(c => find(c, cls)));
const route = { route: '1 Bristol', operator: 'FBRI', timetable_min: 8, typical_min: 14.7, lost_min: 6.7, journeys: 177 };
const window_ = { from: '20261023', to: '20261119' };

test('colours step at 4 and 7 minutes lost', () => {
    assert.equal(lostLevel(2.1).level, 'low');
    assert.equal(lostLevel(4).level, 'mid');
    assert.equal(lostLevel(9.9).level, 'high');
    assert.equal(lineStyle({ lost_min: 8, approximate: true }).dashArray, '6 6');
    assert.equal(lineStyle({ lost_min: 3, approximate: false }).dashArray, null);
});

test('the pop-up is stops, then route and time, two bars, then journeys and dates: no sentence', () => {
    dom();
    assert.equal(formatWindow(window_), '23 Oct to 19 Nov');
    const popup = popupContent({ from: 'Bristol Temple Meads Station', to: 'The Centre',
        to_place: 'Bristol City Centre', routes: [route], approximate: true }, window_, 'Weekdays · 4–7pm');
    const [heading, block] = popup.children;
    assert.equal(text(heading), 'Bristol Temple Meads Station → The Centre, Bristol City Centre');
    assert.deepEqual(block.children.map(c => c.className),
        ['pinch-popup-when', 'pinch-mins', 'pinch-mins', 'pinch-popup-count']);
    assert.equal(text(block.children[0]), 'Route 1 Bristol · Weekdays · 4–7pm');
    assert.match(text(block.children[1]), /^Timetable\|.*\|8 min$/);
    assert.match(text(block.children[2]), /^Usually\|.*\|15 min$/);
    assert.equal(text(block.children[3]), '177 journeys · 23 Oct to 19 Nov');
    const [timetableFill, usualFill] = find(popup, 'pinch-fill pinch-fill-timetable')
        .concat(find(popup, 'pinch-fill pinch-fill-usual'));
    assert.equal(usualFill.style.width, '100%');
    assert.equal(timetableFill.style.width, `${(100 * 8) / 14.7}%`);
    assert.doesNotMatch(text(popup), /\. /);                  // no sentence on screen
    assert.match(popup.attributes['aria-label'], /not the drivers.*drawn straight/);
    assert.match(block.attributes['aria-label'], /the timetable allows 8 min, buses usually take 15 min/);
});

test('the panel is title, choices, labelled key and one small line', async () => {
    dom();
    const host = node('div');
    const added = [];
    const map = { removeLayer: l => added.splice(added.indexOf(l), 1), getSize: () => ({ x: 390 }) };
    const bound = [];
    const L = { geoJSON: (body, opts) => {
        body.features.forEach(f => opts.onEachFeature(f, { bindPopup: (fn, o) => bound.push(o) }));
        return { addTo() { added.push(this); return this; } };
    } };
    const urls = [];
    let reply = { available: true, window: window_, features: [
        { type: 'Feature', properties: { lost_min: 6.7, routes: [route] }, geometry: {} }] };
    const fetchImpl = async url => { urls.push(url); return { json: async () => reply }; };
    const layer = createPinchPointLayer(map, { host, fetchImpl, L });
    const panel = host.children[0];
    assert.equal(text(panel.children[0]), 'Where the timetable is too tight');
    assert.match(panel.attributes['aria-label'], /not the drivers/);
    const legend = panel.children[3];
    assert.equal(text(legend), 'Extra minutes buses usually take|2–4|4–7|7+');
    assert.match(legend.attributes['aria-label'], /2 to 4 minutes, 4 to 7 minutes, 7 or more minutes/);
    layer.set({ band: 'am_peak', day: 'weekend' });
    panel.offsetTop = 10; panel.offsetHeight = 200;
    layer.show();
    await new Promise(r => setTimeout(r, 0));
    assert.equal(urls.at(-1), '/api/pinch-points?band=am_peak&day=weekend');
    const status = panel.children.find(c => c.attributes.role === 'status');
    assert.equal(status.textContent, '23 Oct to 19 Nov · tap a line');
    assert.match(status.attributes['aria-label'], /1 section shown/);
    assert.deepEqual(bound[0].autoPanPaddingTopLeft, [10, 222]);   // pop-ups open below the panel on a phone
    reply = { available: false, reason: 'no_current_data' };
    layer.show();
    await new Promise(r => setTimeout(r, 0));
    assert.equal(added.length, 0);
    assert.equal(status.textContent, 'No current data');
    layer.hide();
    assert.equal(panel.hidden, true);
});
