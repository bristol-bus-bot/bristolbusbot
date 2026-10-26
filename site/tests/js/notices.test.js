import test from 'node:test';
import assert from 'node:assert/strict';
import { bannerText, createNoticeController, noticeCard, stopMarkerStyle } from '../../static/js/notices.js';

function node() {
    return { children: [], dataset: {}, attributes: {}, hidden: false,
        relList: { add() {} },
        setAttribute(k, v) { this.attributes[k] = v; },
        addEventListener(type, fn) { this[`on${type}`] = fn; },
        appendChild(n) { this.children.push(n); },
        replaceChildren(...n) { this.children = n; },
        querySelectorAll() { return []; },
    };
}
function dom() {
    globalThis.document = { createElement: node, createTextNode: text => ({ text }) };
}
const notice = { id: 'n', status: 'current', summary: '<img onerror=bad>', description: 'Notice text',
    source: 'West of England', url: 'https://example.org', periods: [
        { start: '2026-09-15T08:30:00Z', end: '2026-09-15T14:00:00Z' },
    ] };
const response = notices => ({ ok: true, json: async () => ({ available: true, notices }) });

test('notice content is text and dates use UK time', () => {
    dom();
    const card = noticeCard(notice);
    const text = JSON.stringify(card);
    assert.match(text, /09:30/);
    assert.match(text, /15:00/);
    assert.match(text, /"text":"<img onerror=bad>"/);
    const uncertain = noticeCard({ ...notice, status: 'uncertain' });
    assert.match(JSON.stringify(uncertain), /incomplete or inconsistent/);
    assert.doesNotMatch(JSON.stringify(uncertain), /09:30/);
});

test('late response cannot overwrite a newer selection or a cleared panel', async () => {
    dom();
    const host = node(), pending = [];
    const controller = createNoticeController(host, () => new Promise(resolve => pending.push(resolve)));
    const first = controller.show({ stop: 'A' });
    const second = controller.show({ stop: 'B' });
    pending[1](response([{ ...notice, summary: 'Correct B' }])); await second;
    pending[0](response([{ ...notice, summary: 'Wrong A' }])); await first;
    assert.match(JSON.stringify(host), /Correct B/);
    assert.doesNotMatch(JSON.stringify(host), /Wrong A/);
    const third = controller.show({ stop: 'C' });
    controller.clear(); pending[2](response([notice])); await third;
    assert.equal(host.hidden, true);
    assert.deepEqual(host.children, []);
});

test('withdrawal clears the card without an all-clear; failures replace old content', async () => {
    dom();
    let time = 0, result = response([notice]), calls = 0;
    const host = node();
    const controller = createNoticeController(host, async () => { calls++; return result; }, () => time);
    await controller.show({ stop: 'A' });
    await controller.show({ stop: 'A' });
    assert.equal(calls, 1);
    time += 60001; result = response([]);
    await controller.show({ stop: 'A' });
    assert.equal(host.hidden, true);
    time += 60001; result = { ok: false };
    await controller.show({ stop: 'A' });
    assert.match(JSON.stringify(host), /temporarily unavailable/);
    assert.doesNotMatch(JSON.stringify(host), /Notice text/);
});

test('banner wording follows the most pressing notice and never claims more', () => {
    assert.equal(bannerText([]), null);
    assert.deepEqual(bannerText([{ status: 'upcoming' }]),
        { status: 'upcoming', text: 'Upcoming travel notice for this stop' });
    assert.deepEqual(bannerText([{ status: 'upcoming' }, { status: 'uncertain' }]),
        { status: 'uncertain', text: 'Travel notice for this stop: check dates' });
    assert.deepEqual(bannerText([{ status: 'uncertain' }, { status: 'current' }], 'route'),
        { status: 'current', text: 'Travel notice affects this route' });
});

test('stop markers stay quiet without a notice and stand out with one', () => {
    const plain = stopMarkerStyle(undefined, false);
    assert.equal(plain.size, 6); assert.equal(plain.mark, false); assert.equal(plain.label, '');
    const current = stopMarkerStyle('current', false);
    assert.ok(current.size > plain.size); assert.equal(current.mark, true);
    assert.match(current.label, /affects this stop/);
    assert.match(stopMarkerStyle('uncertain', false).label, /check dates/);
    const upcoming = stopMarkerStyle('upcoming', false);
    assert.equal(upcoming.mark, false); assert.match(upcoming.label, /Upcoming/);
    assert.ok(current.zIndex > upcoming.zIndex && upcoming.zIndex > plain.zIndex);
    assert.equal(stopMarkerStyle('current', true).zIndex, 500);
});

test('board shows a banner that opens the first notice card', async () => {
    dom();
    const host = node();
    const controller = createNoticeController(host, async () => response([
        { ...notice, id: 'later', status: 'upcoming', summary: 'Later' },
    ]), () => 0);
    await controller.show({ stop: 'A' });
    const [banner, heading, card] = host.children;
    assert.equal(banner.className, 'notice-banner notice-banner--upcoming');
    assert.match(JSON.stringify(banner), /Upcoming travel notice for this stop/);
    assert.match(JSON.stringify(heading), /Travel notices · 1/);
    assert.ok(!card.open);
    banner.onclick();
    assert.equal(card.open, true);
    assert.match(JSON.stringify(host.children.at(-1)), /Other disruption may not be listed/);
});

test('route selections and current notices get the urgent banner', async () => {
    dom();
    const host = node();
    const controller = createNoticeController(host, async () => response([notice]), () => 0);
    await controller.show({ operator: 'FBRI', line: '43' });
    assert.equal(host.children[0].className, 'notice-banner notice-banner--current');
    assert.match(JSON.stringify(host.children[0]), /Travel notice affects this route/);
});

test('no notices or an out-of-date feed shows no banner', async () => {
    dom();
    let time = 0, result = response([]);
    const host = node();
    const controller = createNoticeController(host, async () => result, () => time);
    await controller.show({ stop: 'A' });
    assert.equal(host.hidden, true);
    assert.deepEqual(host.children, []);
    time += 60001; result = { ok: true, json: async () => ({ available: false, notices: [] }) };
    await controller.show({ stop: 'A' });
    assert.doesNotMatch(JSON.stringify(host), /notice-banner/);
    assert.match(JSON.stringify(host), /temporarily unavailable/);
});

test('a banner host takes the stop banner and is cleared with the selection', async () => {
    dom();
    let time = 0, result = response([notice]), opened = 0;
    const host = node(), bannerHost = node();
    const controller = createNoticeController(host, async () => result, () => time,
        { bannerHost, onOpen: () => opened++ });
    await controller.show({ stop: 'A' });
    assert.equal(bannerHost.hidden, false);
    assert.match(JSON.stringify(bannerHost), /Travel notice affects this stop/);
    assert.doesNotMatch(JSON.stringify(host), /notice-banner/);
    assert.ok(!host.children.includes(null));
    bannerHost.children[0].onclick();
    assert.equal(opened, 1);
    assert.equal(host.children[1].open, true);
    // Routes keep the banner with their cards; the stop banner goes.
    await controller.show({ operator: 'FBRI', line: '43' });
    assert.equal(bannerHost.hidden, true);
    assert.match(JSON.stringify(host.children[0]), /affects this route/);
    // A failed refresh removes the banner rather than leaving it stale.
    await controller.show({ stop: 'A' });
    assert.equal(bannerHost.hidden, false);
    time += 60001; result = { ok: false };
    await controller.show({ stop: 'A' });
    assert.equal(bannerHost.hidden, true);
    controller.clear();
    assert.deepEqual(bannerHost.children, []);
});
