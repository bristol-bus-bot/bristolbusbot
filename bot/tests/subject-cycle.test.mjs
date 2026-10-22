import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DateTime } from 'luxon';
import { chooseSubject, parseSubjectCycle, DEFAULT_SUBJECT_CYCLE, WIDER_LIBRARY_MINIMUM } from '../dist/services/story-subject.js';
import { EditorialContextStore } from '../dist/services/editorial-context.js';

test('the cycle setting accepts valid slots and refuses anything that could stop posting', () => {
  assert.deepEqual(parseSubjectCycle(undefined).cycle, DEFAULT_SUBJECT_CYCLE);
  assert.deepEqual(parseSubjectCycle(' service, Vehicle ,wider?,traffic ').cycle, ['service', 'vehicle', 'wider?', 'traffic']);
  for (const bad of ['service,livery,weather,depot', 'vehicle,weather,depot,wider', 'service,vehicle', 'service,nonsense,weather,depot']) {
    const parsed = parseSubjectCycle(bad);
    assert.ok(parsed.error, bad);
    assert.deepEqual(parsed.cycle, DEFAULT_SUBJECT_CYCLE);
  }
});

const bus = { line: '76', direction: 'outbound', operatorRef: 'FBRI', vehicleRef: 'v', timestamp: new Date().toISOString(),
  eventType: 'delay', delayMinutes: 6, lastStopName: 'Nine Tree Hill', lastStopCode: 's' };
const hook = { kind: 'fact', id: 'f', label: 'sourced fact', claim: 'A qualified local fact.', promptHint: '', requirements: [] };
const cycle = ['service', 'wider?', 'service', 'wider'];

test('the second facts turn needs a big enough library; the first does not', () => {
  assert.equal(chooseSubject(bus, null, hook, 1, [], [], null, { cycle, widerLibrarySize: WIDER_LIBRARY_MINIMUM }).kind, 'wider');
  assert.equal(chooseSubject(bus, null, hook, 1, [], [], null, { cycle, widerLibrarySize: WIDER_LIBRARY_MINIMUM - 1 }).kind, 'service');
  assert.equal(chooseSubject(bus, null, hook, 3, [], [], null, { cycle, widerLibrarySize: 0 }).kind, 'wider');
});

test('on a facts turn a fact is offered whenever one is eligible, not one time in five', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bbb-cycle-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const store = new EditorialContextStore(path.join(process.cwd(), 'data', 'editorial-context.json'), path.join(dir, 'usage.json'), () => 0.9);
  const now = DateTime.fromISO('2026-11-02T12:00:00', { zone: 'Europe/London' });
  const event = { operatorRef: 'FBRI', line: '76', placeContext: { locality: 'Bristol', localAuthority: 'Bristol, City of' } };
  assert.ok(store.factLibrarySize(now, event) > 0);
  assert.equal(store.select(now, [], event), null, 'off the facts turn, 0.9 misses the 20% extra');
  assert.equal(store.select(now, [], event, { factsTurn: true })?.kind, 'fact');
});

test('a simulated 200 posts follow the configured cycle where material exists', () => {
  // Offline simulation: synthetic availability, not a replay of real events.
  let history = [], counts = {};
  const vehicleBus = { ...bus, busDetails: { reg: 'YK25OJO', livery: { name: 'WESTbus' },
    vehicle_type: { name: 'Yutong U11DD', fuel: 'electric', double_decker: true }, special_features: ['USB-C'] } };
  const weather = 'OpenWeather area observation near Bristol at 2026-10-21 12:00 BST: 12°C, with light rain';
  for (let i = 0; i < 200; i++) {
    const choice = chooseSubject(vehicleBus, i % 3 ? weather : null, { ...hook, id: `f${i}` }, i, history, [], null,
      { cycle: DEFAULT_SUBJECT_CYCLE, widerLibrarySize: 24 });
    const slot = ['livery', 'model', 'power', 'age', 'features'].includes(choice.kind) ? 'vehicle' : choice.kind;
    counts[slot] = (counts[slot] || 0) + 1;
    history = [...history, { kind: choice.kind, key: `${choice.key}:${i}`, postHash: String(i % 10).repeat(64) }].slice(-20);
  }
  // 2 vehicle, 2 wider in 8: 25% each within ±5 points. Depot/traffic material is absent, so those
  // slots fall to other subjects, never to a repeat of the same subject.
  assert.ok(Math.abs(counts.vehicle / 200 - 0.25) <= 0.05, JSON.stringify(counts));
  assert.ok(Math.abs(counts.wider / 200 - 0.25) <= 0.05, JSON.stringify(counts));
  assert.ok(!counts.depot && !counts.traffic);
});

import { availableSubjects as allSubjects, SUBJECT_PERSONAS } from '../dist/services/story-subject.js';
import { PLACE_CARDS, placeFact } from '../dist/services/place-cards.js';

test('place cards stay unused until approved, and are keyed by authority and locality', () => {
  const event = { ...bus, lastStopCode: 's', placeContext: { sourceStopCode: 's', locality: 'Hanham', localAuthority: 'South Gloucestershire' } };
  assert.ok(!allSubjects(event, null, null).some(c => c.kind === 'place'), 'draft cards are never used');
  assert.equal(placeFact('South Gloucestershire', 'Hanham'), undefined);
  const approved = { ...PLACE_CARDS, 'South Gloucestershire|Hanham': { ...PLACE_CARDS['South Gloucestershire|Hanham'], status: 'approved' } };
  assert.match(placeFact('South Gloucestershire', 'Hanham', '2026-10-22T12:00:00Z', approved).fact.claim, /Hanham/);
  assert.equal(placeFact('Bristol, City of', 'Hanham', undefined, approved), undefined, 'same name elsewhere does not match');
  assert.ok(SUBJECT_PERSONAS.place.includes('never say the bus visited'));
  assert.doesNotThrow(() => allSubjects({ ...bus, lastStopCode: undefined }, null, null), 'no stop code, no place data');
  for (const entry of Object.values(PLACE_CARDS)) {
    assert.ok(entry.facts.length >= 1);
    for (const f of entry.facts) assert.match(f.source, /^https:\/\/en\.wikipedia\.org\//);
  }
});
