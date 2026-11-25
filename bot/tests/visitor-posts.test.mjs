import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { DateTime } from 'luxon';

import { busName, chooseVisitorPosts, visitorPost, visitorPostsEnabled }
  from '../dist/services/visitor-posts.js';
import { RareWorkingShadowReader } from '../dist/ingest/rare-working-shadow-reader.js';

// Real 27 Oct 2026 case from the 30-day check: FBRI-65172 usually works the 8
// (38 of 56 days) and worked the 37, which shares none of its regular buses.
const event = (over = {}) => ({
  event_id: 'e1', service_date: '20261027', operator: 'FBRI', vehicle_ref: 'FBRI-65172',
  route: '37',
  evidence: { visitor: true, pair_days: 0, usual_route: '8', usual_route_days: 38,
              shared_regulars_pct: 0 },
  ...over,
});
const at = (iso) => DateTime.fromISO(iso, { zone: 'Europe/London' });

test('switched off unless BBB_RARE_WORKING_POSTS=true', () => {
  assert.equal(visitorPostsEnabled({}), false);
  assert.equal(visitorPostsEnabled({ BBB_RARE_WORKING_POSTS: 'false' }), false);
  assert.equal(visitorPostsEnabled({ BBB_RARE_WORKING_POSTS: 'true' }), true);
});

test('a visitor post is about a past day and states only dated facts', () => {
  const text = visitorPost(event(), at('2026-10-28T09:00'));
  assert.equal(text, 'Yesterday, bus 65172 turned up on the 37, a route it hadn\'t worked once in the '
    + 'previous eight weeks. Its usual haunt is the 8, which it worked on 38 of those 56 days.');
  assert.match(visitorPost(event(), at('2026-10-29T09:00')), /^On Tuesday, bus 65172/);
  assert.ok(!text.includes('—'), 'no em dash');
  assert.ok(!/First|FBRI|livery|depot|now|currently/i.test(text), 'no company, livery, depot or current claims');
});

test('never today, never stale, never without the pipeline\'s visitor verdict', () => {
  assert.equal(visitorPost(event(), at('2026-10-27T20:00')), null, 'same day');
  assert.equal(visitorPost(event(), at('2026-11-01T09:00')), null, 'over three days old');
  assert.equal(visitorPost(event({ evidence: { ...event().evidence, visitor: false } }), at('2026-10-28T09:00')), null);
  assert.equal(visitorPost(event({ evidence: { ...event().evidence, pair_days: 1 } }), at('2026-10-28T09:00')), null);
  assert.equal(visitorPost(event({ evidence: undefined }), at('2026-10-28T09:00')), null);
});

test('bus names drop the operator prefix', () => {
  assert.equal(busName('FBRI-65172'), '65172');
  assert.equal(busName('EUTX-VF05_ETE'), 'VF05 ETE');
});

test('at most two a day, one per bus, furthest from home first', () => {
  const events = [
    event({ event_id: 'a', vehicle_ref: 'FBRI-1', evidence: { ...event().evidence, shared_regulars_pct: 4 } }),
    event({ event_id: 'b', vehicle_ref: 'FBRI-2', evidence: { ...event().evidence, shared_regulars_pct: 0 } }),
    event({ event_id: 'c', vehicle_ref: 'FBRI-2', route: '74' }),
    event({ event_id: 'd', vehicle_ref: 'FBRI-3', evidence: { ...event().evidence, shared_regulars_pct: 2 } }),
  ];
  const now = at('2026-10-28T09:00');
  assert.deepEqual(chooseVisitorPosts(events, 0, new Set(), now).map(c => c.event.event_id), ['b', 'd']);
  assert.deepEqual(chooseVisitorPosts(events, 1, new Set(), now).map(c => c.event.event_id), ['b']);
  assert.deepEqual(chooseVisitorPosts(events, 2, new Set(), now), []);
  assert.deepEqual(chooseVisitorPosts(events, 0, new Set(['b', 'c']), now).map(c => c.event.event_id), ['d', 'a']);
});

function snapshotWith(events) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bbb-visitor-'));
  const snapshot = path.join(dir, 'integration.json');
  fs.writeFileSync(snapshot, JSON.stringify({ schema: 1, published_at: '2026-10-28T07:00:00+00:00',
    rare_workings: { mode: 'shadow', events } }));
  return { dir, snapshot, state: path.join(dir, 'state.db') };
}

test('the reader posts nothing without a publisher (the default)', async () => {
  const { dir, snapshot, state } = snapshotWith([event()]);
  const reader = new RareWorkingShadowReader(snapshot, state);
  assert.equal(reader.pollOnce(), 1);
  assert.equal(await reader.publishVisitors(at('2026-10-28T09:00')), 0);
  reader.stop();
  fs.rmSync(dir, { recursive: true, force: true });
});

test('with a publisher the reader posts each visitor once, two a day', async () => {
  const { dir, snapshot, state } = snapshotWith([
    event({ event_id: 'a', vehicle_ref: 'FBRI-1' }), event({ event_id: 'b', vehicle_ref: 'FBRI-2' }),
    event({ event_id: 'c', vehicle_ref: 'FBRI-3' })]);
  const sent = [];
  const reader = new RareWorkingShadowReader(snapshot, state, 60_000, async (text) => { sent.push(text); return true; });
  reader.pollOnce();
  assert.equal(await reader.publishVisitors(at('2026-10-28T09:00')), 2);
  assert.equal(await reader.publishVisitors(at('2026-10-28T15:00')), 0, 'cap reached today');
  assert.equal(await reader.publishVisitors(at('2026-10-29T09:00')), 1, 'next day: the one left');
  assert.equal(sent.length, 3);
  reader.stop();
  fs.rmSync(dir, { recursive: true, force: true });
});

test('a failed post is not recorded and is tried again', async () => {
  const { dir, snapshot, state } = snapshotWith([event()]);
  let ok = false;
  const reader = new RareWorkingShadowReader(snapshot, state, 60_000, async () => ok);
  reader.pollOnce();
  assert.equal(await reader.publishVisitors(at('2026-10-28T09:00')), 0);
  ok = true;
  assert.equal(await reader.publishVisitors(at('2026-10-28T09:10')), 1);
  reader.stop();
  fs.rmSync(dir, { recursive: true, force: true });
});
