import test from 'node:test';
import assert from 'node:assert/strict';
import { cellLevel, cellText, createRouteHoursController, hourCells, hourSummary, journeyLines } from '../../static/js/route_hours.js';

function node() {
    return { children: [], dataset: {}, attributes: {}, hidden: false, relList: { add() {} },
        setAttribute(k, v) { this.attributes[k] = v; },
        addEventListener(type, fn) { this[`on${type}`] = fn; },
        appendChild(n) { this.children.push(n); },
        replaceChildren(...n) { this.children = n; } };
}
const dom = () => { globalThis.document = { createElement: node, createTextNode: text => ({ text }) }; };
const hours = { weekday: { 6: [5, 0, 1], 8: [30, 2, 68], 11: [80, 5, 15] }, weekend: { 9: [40, 0, 10] } };

test('cells run from the first to the last hour served, with gaps and low samples marked', () => {
    const cells = hourCells(hours, 'weekday', 30);
    assert.deepEqual(cells.map(c => c.hour), [6, 7, 8, 9, 10, 11]);
    assert.equal(cells[0].low, true);
    assert.equal(cells[1].total, 0);
    assert.equal(cellLevel(cells[1]), 'none');
    assert.equal(cellLevel(cells[2]), 'bad');
    assert.equal(cellLevel(cells[5]), 'good');
    assert.match(cellText(cells[0]), /too few to rely on/);
    assert.match(cellText(cells[2]), /08:00: 3 in 10 on time, 68 late, 2 early, from 100 readings/);
});

test('the summary compares only hours with enough readings', () => {
    assert.equal(hourSummary(hourCells(hours, 'weekday', 30), 'weekday'),
        'On weekdays it\'s least punctual from 08:00 (3 in 10 on time) and most punctual from 11:00 (8 in 10).');
    assert.match(hourSummary(hourCells(hours, 'weekend', 30), 'weekend'), /aren't enough readings/);
});

test('journey lines keep cancelled, very likely not run and not seen apart', () => {
    const lines = journeyLines({ total: [1000, 900, 10],
        bands: { am_peak: [200, 160, 2], interpeak: [800, 740, 8] } }, true);
    assert.match(lines[0], /1,000 journeys were timetabled\. We saw 900 of them running \(90 in 100\)/);
    assert.match(lines[1], /^10 very likely didn't run/);
    assert.match(lines[2], /didn't see the other 90\..*don't count them as cancelled/);
    assert.match(lines[3], /Most often missing: 7–10am, when 20 in 100/);
    // Without a passed duty check, nothing is called "very likely didn't run".
    const unchecked = journeyLines({ total: [1000, 900, 10], bands: {} }, false);
    assert.equal(unchecked.length, 2);
    assert.match(unchecked[1], /other 100/);
    assert.deepEqual(journeyLines(null, true), []);
});

test('controller shows the route, ignores late answers and clears', async () => {
    dom();
    const host = node(), pending = [];
    const controller = createRouteHoursController(host, () => new Promise(r => pending.push(r)));
    const reply = route => ({ ok: true, json: async () => ({ available: true, route, hours,
        min_readings: 30, window: { days: { weekday: 20, weekend: 8 } },
        journeys: { total: [100, 90, 1], bands: {} }, duty_checked: true }) });
    const first = controller.show('FBRI', '1');
    const second = controller.show('FBRI', '75');
    pending[1](reply('75')); await second;
    pending[0](reply('1')); await first;
    assert.match(JSON.stringify(host), /When is the 75 on time\?/);
    assert.doesNotMatch(JSON.stringify(host), /When is the 1 on time/);
    assert.match(JSON.stringify(host), /Did the buses turn up\?/);
    assert.equal(host.hidden, false);
    controller.clear();
    assert.equal(host.hidden, true);
});

test('no data means no panel', async () => {
    dom();
    const host = node();
    host.hidden = true;
    const controller = createRouteHoursController(host, async () => ({ ok: true, json: async () => ({ available: false }) }));
    await controller.show('FBRI', '75');
    assert.equal(host.hidden, true);
});
