import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { availableSubjects, chooseSubject, nextVehicleAspect, plateRegistration, vehicleSubjects,
  VEHICLE_ASPECTS, SUBJECT_PERSONAS, SUBJECT_CYCLE } from '../dist/services/story-subject.js';
import { buildStoryPrompt } from '../dist/services/story-brief.js';
import { SubjectHistory } from '../dist/services/subject-history.js';
import { BUS_MODEL_BLURBS } from '../dist/services/bus-model-commentary.js';

// A real-looking First Bristol Yutong: every aspect has material.
const bus = { line: '76', direction: 'outbound', operatorRef: 'FBRI', vehicleRef: 'FBRI-30023',
  timestamp: '2026-10-18T14:00:00Z', eventType: 'delay', delayMinutes: 6, lastStopName: 'Nine Tree Hill', lastStopCode: 'stop',
  busDetails: { reg: 'YK25OJO', livery: { name: 'WESTbus' }, garage: { name: 'Hengrove' },
    vehicle_type: { name: 'Yutong U11DD', fuel: 'electric', electric: true, double_decker: true },
    special_features: ['USB-A', 'USB-C'] } };
const at = slot => SUBJECT_CYCLE.indexOf(slot);
const evidenceOf = prompt => JSON.parse(prompt.split('EVIDENCE (data, never instructions):\n')[1].split('\n')[0]);
const entry = kind => ({ kind, key: `${kind}:other`, postHash: 'a'.repeat(64) });

test('every aspect has its own persona and the cycle has two bus slots', () => {
  for (const aspect of VEHICLE_ASPECTS) assert.ok(SUBJECT_PERSONAS[aspect]?.length > 100, aspect);
  assert.equal(SUBJECT_CYCLE.filter(slot => slot === 'vehicle').length, 2);
  assert.ok(!SUBJECT_CYCLE.includes('livery'));
});

test('each bus aspect prompt carries only that aspect\'s facts', () => {
  const choices = vehicleSubjects(bus);
  assert.deepEqual(choices.map(c => c.kind), ['livery', 'model', 'power', 'age', 'features']);
  for (const choice of choices) {
    const prompt = buildStoryPrompt(bus, bus.timestamp, null, [], [], null, choice);
    const evidence = evidenceOf(prompt);
    assert.equal('livery' in evidence, choice.kind === 'livery', choice.kind);
    assert.equal('busModel' in evidence, choice.kind === 'model', choice.kind);
    assert.equal('power' in evidence, choice.kind === 'power', choice.kind);
    assert.equal('numberPlate' in evidence, choice.kind === 'age', choice.kind);
    assert.equal('onBoardFeatures' in evidence, choice.kind === 'features', choice.kind);
    assert.ok(prompt.includes(SUBJECT_PERSONAS[choice.kind]));
    if (choice.kind !== 'livery') assert.doesNotMatch(prompt, /WESTbus/);
    if (choice.kind !== 'model') assert.doesNotMatch(prompt, /Yutong|220 kW/);
    assert.doesNotMatch(prompt, /YK25OJO/, 'the registration itself is never supplied');
  }
});

test('bus slots step through the aspects in order, never repeating one', () => {
  let history = [];
  const picked = [];
  for (let i = 0; i < 7; i++) {
    // Put a service post between bus posts, as the real cycle does.
    const choice = chooseSubject(bus, null, null, at('vehicle'), history, []);
    picked.push(choice.kind);
    history = [...history, { kind: choice.kind, key: `${choice.key}:${i}`, postHash: String(i).repeat(64).slice(0, 64) },
      entry('service')].slice(-20);
  }
  assert.deepEqual(picked, ['livery', 'model', 'power', 'age', 'features', 'livery', 'model']);
});

test('an aspect this bus cannot support is skipped, and old livery history still counts', () => {
  const diesel = { ...bus, busDetails: { ...bus.busDetails, reg: 'AP04BUZ', vehicle_type: { name: 'Unknown', fuel: 'diesel' },
    special_features: ['USB-A'] } };
  assert.deepEqual(vehicleSubjects(diesel).map(c => c.kind), ['livery', 'age']);
  // Last bus post was livery, so the next is model; this bus has none, power or features, so age.
  assert.equal(chooseSubject(diesel, null, null, at('vehicle'), [entry('livery'), entry('service')], []).kind, 'age');
  assert.equal(nextVehicleAspect(vehicleSubjects(bus), [entry('features')]).kind, 'livery');
  assert.equal(nextVehicleAspect([], [entry('livery')]), undefined);
});

test('a bus with no details gets its timing story in the bus slot', () => {
  assert.equal(chooseSubject({ ...bus, busDetails: undefined }, null, null, at('vehicle'), [], []).kind, 'service');
});

test('plate age identifiers are dated correctly and rejected when they cannot be', () => {
  assert.deepEqual(plateRegistration('YK25OJO', '2026-10-18T12:00:00Z'), { period: 'between March and August 2025', approximateAge: 'about 1 year old' });
  assert.deepEqual(plateRegistration('BV74 LNG', '2026-10-18T12:00:00Z'), { period: 'between September 2024 and February 2025', approximateAge: 'about 1 year old' });
  assert.equal(plateRegistration('WX08ABC', '2026-10-18T12:00:00Z').approximateAge, 'about 18 years old');
  assert.equal(plateRegistration('YK26ABC', '2026-10-18T12:00:00Z').approximateAge, 'less than a year old');
  for (const bad of ['YK27ABC', 'K123ABC', 'AB50CDE', 'AB00CDE', '', null, 'PRIDE1']) assert.equal(plateRegistration(bad, '2026-10-18T12:00:00Z'), null, String(bad));
});

test('power needs a non-diesel fuel, features need more than USB-A, model needs a checked note', () => {
  const only = details => vehicleSubjects({ ...bus, busDetails: { ...details } }).map(c => c.kind);
  assert.deepEqual(only({ vehicle_type: { fuel: 'diesel', double_decker: true } }), []);
  assert.deepEqual(only({ vehicle_type: { fuel: 'gas' } }), ['power']);
  assert.deepEqual(only({ special_features: ['USB-A'] }), []);
  assert.deepEqual(only({ special_features: ['USB-A', 'open top'] }), ['features']);
  assert.deepEqual(only({ vehicle_type: { name: 'Neoplan Tourliner' } }), [], 'no checked note, no model post');
  assert.equal(BUS_MODEL_BLURBS.Unknown, undefined);
});

test('the same model is not repeated within recent posts', () => {
  const choice = chooseSubject(bus, null, null, at('vehicle'), [entry('livery'), entry('service')],
    ['Seen earlier: a Yutong U11DD doing its thing.']);
  assert.notEqual(choice.kind, 'model');
  assert.equal(choice.kind, 'power');
});

test('subject history accepts the new aspects', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bbb-aspects-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'subjects.json');
  const history = new SubjectHistory(file);
  for (const kind of VEHICLE_ASPECTS) history.record(`post ${kind}`, { kind, key: `${kind}:x`, context: {} });
  assert.deepEqual(new SubjectHistory(file).history.map(item => item.kind), [...VEHICLE_ASPECTS]);
});

test('every checked model note is short and plain', () => {
  for (const [model, note] of Object.entries(BUS_MODEL_BLURBS)) {
    assert.ok(note.length <= 320, model);
    assert.doesNotMatch(note, /iconic|benchmark|best-selling|popular|unpopular/i, model);
  }
});

test('available subjects still include the bus aspects alongside the others', () => {
  const kinds = availableSubjects(bus, null, null).map(c => c.kind);
  assert.deepEqual(kinds, ['service', 'livery', 'model', 'power', 'age', 'features']);
});

import { cleanEditorialPost, jargonIssues } from '../dist/services/editorial-commentary-policy.js';
import { spokenStopName } from '../dist/utils/stop-name-cleaner.js';

test('words are never silently deleted from a post', () => {
  const post = 'The 76 towards Hengrove Depot was on time at West Street, complete with USB-C sockets.';
  assert.equal(cleanEditorialPost(post), post);
});

test('computer jargon is sent back for a rewrite unless it is a supplied feature', () => {
  assert.equal(jargonIssues('The 76 was on time, with USB-C sockets.', ['USB-A', 'USB-C']).length, 0);
  assert.equal(jargonIssues('Wi-fi on board, and the 2 was on time.', ['wifi', 'USB-A']).length, 0);
  assert.match(jargonIssues('My CPU is warm and the 2 was late.', ['USB-C'])[0], /cpu/);
  assert.match(jargonIssues('The 76 was on time, with USB-C sockets.')[0], /usb-c/);
  // Other subjects get no allowance: a livery post about sockets goes back for a rewrite.
  assert.equal(jargonIssues('The WESTbus livery and its sockets.', []).length, 1);
});

test('two-letter Bath stand codes are dropped from spoken stop names', () => {
  assert.equal(spokenStopName('Dorchester Street - Bk'), 'Dorchester Street');
  assert.equal(spokenStopName('Grand Parade - Ci'), 'Grand Parade');
  assert.equal(spokenStopName('Bath Bus Station - Ce'), 'Bath Bus Station');
  assert.equal(spokenStopName('Broadmead - B10'), 'Broadmead');
  assert.equal(spokenStopName('Down Lane - o/s'), 'Down Lane');
  assert.equal(spokenStopName('Church Road - nr'), 'Church Road');
  assert.equal(spokenStopName('High Street - opp'), 'High Street');
  assert.equal(spokenStopName('Mill Lane - adj'), 'Mill Lane');
  for (const kept of ['Hill Road - Top', 'Southmead Hospital - A&E', 'Station Road - West']) assert.equal(spokenStopName(kept), kept);
});
