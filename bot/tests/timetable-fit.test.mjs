import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { TimetableFit, bandFor } from '../dist/services/timetable-fit.js';
import { availableSubjects, chooseSubject, SUBJECT_PERSONAS } from '../dist/services/story-subject.js';
import { buildStoryPrompt } from '../dist/services/story-brief.js';
import { SubjectHistory } from '../dist/services/subject-history.js';

function segments(items) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'segments-'));
  fs.writeFileSync(path.join(dir, 'FBRI.json'), JSON.stringify({ operator: 'FBRI', routes: { '1': items } }));
  return dir;
}
const tight = { kind: 'tight', from: 'Temple Meads Stn', to: 'The Centre', from_stop: 'bstpjgw', to_stop: 'bstgapd',
  band: 'interpeak', day_type: 'weekday', timetable_min: 7, typical_min: 13.8, share_pct: 100, journeys: 434 };
// Friday 23 October 2026, 11:30 BST.
const event = { line: '1', direction: 'inbound', operatorRef: 'FBRI', vehicleRef: 'FBRI-37245',
  timestamp: '2026-10-23T10:30:00Z', eventType: 'delay', delayMinutes: 6, lastStopName: 'The Centre', lastStopCode: 'bstgapd' };

test('the audit band and day type follow the UK clock', () => {
  assert.deepEqual(bandFor('2026-10-23T10:30:00Z'), { band: 'interpeak', dayType: 'weekday' });
  assert.deepEqual(bandFor('2026-10-24T06:30:00Z'), { band: 'am_peak', dayType: 'weekend' });
  assert.deepEqual(bandFor('2026-10-25T23:30:00Z'), { band: 'evening', dayType: 'weekend' });
});

test('a bus five or more minutes late at the end of a known tight stretch gets the fact', () => {
  const fit = new TimetableFit(segments([tight]), true);
  const fact = fit.factFor(event);
  assert.equal(fact.from, 'Temple Meads Stn');
  assert.equal(fact.timetableMinutes, 7);
  assert.equal(fact.typicalMinutes, 14);
  assert.equal(fact.when, 'weekdays 10am-4pm');
  assert.match(fact.scope, /does not prove why this bus is late/);
});

test('no fact for small delays, other stops, other times, shaky observations or when switched off', () => {
  const dir = segments([tight, { ...tight, kind: 'slack', to_stop: 'other' }]);
  const fit = new TimetableFit(dir, true);
  assert.equal(fit.factFor({ ...event, delayMinutes: 4 }), null);
  assert.equal(fit.factFor({ ...event, eventType: 'punctual', delayMinutes: 0 }), null);
  assert.equal(fit.factFor({ ...event, lastStopCode: 'other' }), null);
  assert.equal(fit.factFor({ ...event, timestamp: '2026-10-24T10:30:00Z' }), null);
  assert.equal(fit.factFor({ ...event, lowConfidence: true }), null);
  assert.equal(new TimetableFit(dir, false).factFor(event), null);
  assert.equal(new TimetableFit(path.join(dir, 'missing'), true).factFor(event), null);
});

test('it takes a service turn, stays about the bus, and is never repeated back to back', () => {
  const fit = new TimetableFit(segments([tight]), true);
  const choice = chooseSubject(event, null, null, 0, [], [], null, { cycle: ['service', 'vehicle', 'weather', 'depot'], timetableFit: fit });
  assert.equal(choice.kind, 'timetable');
  assert.ok(availableSubjects(event, null, null, null, fit).some(c => c.kind === 'timetable'));
  const again = chooseSubject(event, null, null, 0, [{ kind: 'timetable', key: choice.key, postHash: 'a'.repeat(64) }], [], null,
    { cycle: ['service', 'vehicle', 'weather', 'depot'], timetableFit: fit });
  assert.equal(again.kind, 'service');
  const prompt = buildStoryPrompt(event, event.timestamp, null, [], [], null, choice);
  assert.match(prompt, /never on the driver/);
  assert.match(prompt, /"timetableFit"/);
  assert.match(SUBJECT_PERSONAS.timetable, /this one bus/);
});

test('subject history accepts the new kind', () => {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'history-')), 'h.json');
  fs.writeFileSync(file, JSON.stringify({ version: 1, publishedCount: 1, history: [{ kind: 'timetable', key: 'x', postHash: 'b'.repeat(64) }] }));
  assert.equal(new SubjectHistory(file).history[0].kind, 'timetable');
});
