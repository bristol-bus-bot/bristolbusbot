import test from 'node:test';
import assert from 'node:assert/strict';
import { createNoticeController, noticeCard } from '../../static/js/notices.js';

function node() {
    return { children: [], dataset: {}, attributes: {}, hidden: false,
        relList: { add() {} },
        setAttribute(k, v) { this.attributes[k] = v; },
        addEventListener() {},
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
