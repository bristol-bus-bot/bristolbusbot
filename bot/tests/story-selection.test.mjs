import assert from 'node:assert/strict';
import test from 'node:test';
import sqlite3 from 'sqlite3';
import { selectStory } from '../dist/services/story-selection.js';
import { matchedJourneyContext } from '../dist/services/journey-context.js';
import { DatabaseManager } from '../dist/services/database-manager.js';
import { buildStoryPrompt, BOT_VOICE, factualStoryIssues } from '../dist/services/story-brief.js';
import { AICommentary } from '../dist/services/ai-commentary.js';

const bus = (vehicleRef, eventType = 'delay', line = '42') => ({ vehicleRef, eventType, line,
  operatorRef: 'FBRI', delayMinutes: eventType === 'punctual' ? 0 : 8 });

test('a large on-time pool cannot dominate available late/early stories', () => {
  const events = [...Array.from({ length: 84 }, (_, i) => bus(`p${i}`, 'punctual')),
    ...Array.from({ length: 27 }, (_, i) => bus(`d${i}`)),
    ...Array.from({ length: 18 }, (_, i) => bus(`e${i}`, 'early'))];
  let history = [];
  const chosen = [];
  for (let i = 0; i < 20; i++) {
    const event = selectStory(events, history, () => 0.7);
    chosen.push(event.eventType);
    history = [...history, event].slice(-6);
  }
  assert.equal(chosen.filter(type => type === 'punctual').length, 4);
  for (let i = 0; i < 20; i++) assert.equal(chosen[i] === 'punctual', i % 5 === 4);
});

test('selection still posts when only on-time, only early or only late buses exist', () => {
  for (const type of ['punctual', 'early', 'delay']) assert.equal(selectStory([bus('one', type)], []).eventType, type);
  assert.equal(selectStory([], []), null);
  assert.equal(selectStory([bus('late')], Array.from({ length: 4 }, () => bus('previous'))).vehicleRef, 'late');
});

test('selection avoids recent routes and vehicles without excluding the only story', () => {
  const previous = bus('same');
  assert.equal(selectStory([previous, bus('other', 'delay', '43')], [previous], () => 0).vehicleRef, 'other');
  assert.equal(selectStory([previous], [previous], () => 0).vehicleRef, 'same');
});

const event = { ...bus('one'), timestamp: '2026-09-14T21:00:00Z', direction: 'inbound',
  collectorTripId: 'trip-42', collectorStopSequence: 30, lastStopCode: 'loop', lastStopName: 'Loop Road' };
const stops = [
  { stop_sequence: 10, stop_code: 'loop', stop_name: 'Loop Road' },
  { stop_sequence: 20, stop_code: 'middle', stop_name: 'Middle Street' },
  { stop_sequence: 30, stop_code: 'loop', stop_name: 'Loop Road' },
];

test('journey position uses the matched trip point, including loop stops and non-contiguous sequences', () => {
  assert.equal(matchedJourneyContext(event, stops).timingPointNumber, 3);
  assert.equal(matchedJourneyContext(event, stops).totalStops, 3);
  for (const change of [{ collectorTripId: undefined }, { collectorStopSequence: 2 },
    { lastStopCode: 'different' }, { lowConfidence: true }]) {
    assert.equal(matchedJourneyContext({ ...event, ...change }, stops), undefined);
  }
  assert.equal(matchedJourneyContext(event, [...stops, stops[2]]), undefined);
});

test('database lookup uses only the collector-selected trip and matching route', async t => {
  const db = new sqlite3.Database(':memory:');
  t.after(() => db.close());
  await new Promise((resolve, reject) => db.exec(`
    CREATE TABLE routes(route_id TEXT, route_short_name TEXT);
    CREATE TABLE trips(trip_id TEXT, route_id TEXT);
    CREATE TABLE stops(stop_id TEXT, stop_code TEXT, stop_name TEXT);
    CREATE TABLE stop_times(trip_id TEXT, stop_id TEXT, stop_sequence INTEGER);
    INSERT INTO routes VALUES ('r42','42'),('r43','43');
    INSERT INTO trips VALUES ('trip-42','r42'),('trip-43','r43');
    INSERT INTO stops VALUES ('a','start','Start'),('b','loop','Loop Road');
    INSERT INTO stop_times VALUES ('trip-42','a',10),('trip-42','b',30),('trip-43','b',30);
  `, error => error ? reject(error) : resolve()));
  const manager = Object.create(DatabaseManager.prototype);
  manager.timetableDb = db;
  manager.appState = { dbIsReloading: false };
  assert.equal((await manager.enrichStoryJourney(event)).journeyContext.timingPointNumber, 2);
  assert.equal((await manager.enrichStoryJourney({ ...event, line: '43' })).journeyContext, undefined);
  assert.equal((await manager.enrichStoryJourney({ ...event, collectorTripId: 'missing' })).journeyContext, undefined);
});

test('service writer preserves the civic voice and timing without leaking unrelated context or previous prose', () => {
  const contextual = { ...event, journeyContext: matchedJourneyContext(event, stops),
    placeContext: { locality: 'Example district', localColour: 'A steep hill.' },
    busDetails: { vehicle_type: { name: 'Model A', electric: true }, livery: { name: 'Livery B' } } };
  const recent = ['On time. Nothing to complain about.'];
  const prompt = buildStoryPrompt(contextual, '2026-09-14T21:01:00Z', null, recent);
  assert.match(BOT_VOICE, /dry, concise and on the passenger's side/);
  assert.doesNotMatch(prompt, /A joke is optional|Direction is optional|That evidence is absent/);
  for (const text of ['Model A', 'Livery B', 'Example district', 'A steep hill.', recent[0], '"timingPointNumber": 3']) {
    assert.ok(!prompt.includes(text), text);
  }
  assert.match(prompt, /Aim criticism at the service/);
  assert.deepEqual(factualStoryIssues('The inbound 42 was late at stop 3.', contextual), []);
  assert.ok(factualStoryIssues('The 42 left early.', contextual).length);
});

test('missing supplied direction is repairable but unknown direction is not invented', () => {
  const ai = Object.create(AICommentary.prototype);
  const writer = { post: 'The 42 was eight minutes late at Loop Road.', hookUsed: false };
  assert.ok(ai.prepareWriterCandidate(writer, { event }, null).issues.some(issue => issue.includes('direction')));
  assert.deepEqual(ai.prepareWriterCandidate({ ...writer, post: 'The inbound 42 was eight minutes late at Loop Road.' }, { event }, null).issues, []);
  assert.deepEqual(ai.prepareWriterCandidate(writer, { event: { ...event, direction: '' } }, null).issues, []);
});
