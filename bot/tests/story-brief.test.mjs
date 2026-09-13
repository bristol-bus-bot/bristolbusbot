import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import Database from 'better-sqlite3';
import { buildStoryPrompt, observationIssue, latestStoryEvents, factualStoryIssues } from '../dist/services/story-brief.js';
import { EventReader } from '../dist/ingest/event-reader.js';
import { SocialMediaManager } from '../dist/services/social-media.js';
import { AICommentary } from '../dist/services/ai-commentary.js';

const now = Date.parse('2026-09-13T12:00:00Z');
const event = {
  collectorEventId: 1, operatorRef: 'FBRI', vehicleRef: 'FBRI-test', line: '42', direction: 'outbound',
  datedJourneyRef: 'j42', originAimedDepartureTimeStr: '2026-09-13T11:30:00Z',
  timestamp: '2026-09-13T11:59:00Z', lastStopCode: 'bst-test', lastStopName: 'Two Mile Hill',
  delayMinutes: 8, delaySeconds: 480, eventType: 'delay', significance: 8, lastStopTime: '',
};
const freshEvent = () => ({ ...event, timestamp: new Date().toISOString(),
  originAimedDepartureTimeStr: new Date(Date.now() - 3600_000).toISOString() });

test('story eligibility rejects stale, uncertain and pre-origin early reports without claiming they are waiting', () => {
  assert.equal(observationIssue(event, now), null);
  assert.match(observationIssue({ ...event, timestamp: '2026-09-13T11:54:00Z' }, now), /stale/);
  assert.match(observationIssue({ ...event, timestamp: 'invalid' }, now), /invalid/);
  assert.match(observationIssue({ ...event, lowConfidence: true }, now), /confidence/);
  for (const origin of ['', 'invalid', '2026-09-13T12:05:00Z']) {
    assert.match(observationIssue({ ...event, eventType: 'early', delayMinutes: -4,
      originAimedDepartureTimeStr: origin }, now), /does not establish/);
  }
  assert.equal(observationIssue({ ...event, eventType: 'early', delayMinutes: -4 }, now), null);
});

test('newer uncertainty suppresses an older good report and operators do not collide', () => {
  assert.deepEqual(latestStoryEvents([event, { ...event, timestamp: '2026-09-13T11:59:30Z', lowConfidence: true }], now), []);
  assert.equal(latestStoryEvents([event, { ...event, operatorRef: 'SCGL' }], now).length, 2);
});

test('brief excludes misleading network/route-position/garage facts and limits vehicle repetition', () => {
  const enriched = { ...event, busDetails: { vehicle_type: { name: 'Yutong U11DD' },
    livery: { name: 'Olympia' }, garage: { name: 'Lawrence Hill' } } };
  const prompt = buildStoryPrompt(enriched, new Date(now).toISOString(), null, ['Olympia Yutong U11DD']);
  assert.match(prompt, /"optionalDetail": null/);
  assert.doesNotMatch(prompt, /Lawrence Hill|Network:|Position:|Vehicle notes:/);
  assert.match(prompt, /"departureObserved": false/);
  assert.match(prompt, /Direction is optional/);
  assert.match(factualStoryIssues('The 42 left ten minutes early.').join(' '), /departure/);
  assert.match(factualStoryIssues('The 42 is at stop 1.').join(' '), /position/);
  assert.match(factualStoryIssues('The network average delay is ten minutes.').join(' '), /network/);
});

test('collector recheck rejects stale positions, changed runs, depots, confidence and changed timing', t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bbb-story-live-'));
  const dbPath = path.join(dir, 'live.db');
  const db = new Database(dbPath);
  db.exec(`CREATE TABLE vehicles (vehicle_ref TEXT PRIMARY KEY, operator_ref TEXT, line TEXT,
    journey_ref TEXT, origin_aimed_departure TEXT, direction TEXT, stop_code TEXT, recorded_at TEXT,
    event_type TEXT, delay_seconds INTEGER, low_confidence INTEGER, at_depot TEXT)`);
  db.prepare('INSERT INTO vehicles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)').run(
    event.vehicleRef, event.operatorRef, event.line, event.datedJourneyRef,
    event.originAimedDepartureTimeStr, event.direction, event.lastStopCode,
    '2026-09-13T11:59:50Z', 'delayed', 480, 0, null);
  const reader = new EventReader(dbPath, {}, {});
  t.after(() => { reader.stop(); db.close(); fs.rmSync(dir, { recursive: true, force: true }); });
  assert.equal(reader.isObservationCurrent(event, now), true);
  for (const [column, value] of [['journey_ref', 'next-run'], ['operator_ref', 'SCGL'],
    ['stop_code', 'next-stop'], ['delay_seconds', 600], ['low_confidence', 1], ['at_depot', 'LH'],
    ['recorded_at', '2026-09-13T11:55:00Z']]) {
    const original = db.prepare(`SELECT ${column} AS value FROM vehicles`).get().value;
    db.prepare(`UPDATE vehicles SET ${column} = ?`).run(value);
    assert.equal(reader.isObservationCurrent(event, now), false, column);
    db.prepare(`UPDATE vehicles SET ${column} = ?`).run(original);
  }
});

function socialHarness(events = [freshEvent()]) {
  const state = { postsTodayCount: 0, resetDailyCounters() {}, getAndClearBusEvents: () => events };
  const manager = new SocialMediaManager({ dailyLimit: 10, postLimit: 300, testMode: false }, state);
  let publications = 0;
  manager.postUpdate = async () => { publications++; return { bluesky: true }; };
  return { manager, state, publications: () => publications };
}

test('rejected AI drafts never publish unchecked fallback templates', async () => {
  const h = socialHarness();
  h.manager.setAICommentary({ generatePost: async () => null });
  await h.manager.processEventCollector();
  assert.equal(h.publications(), 0);
});

test('a changed observation during writing cancels publication', async () => {
  const h = socialHarness();
  let current = true;
  h.manager.setObservationValidator(() => current);
  h.manager.setAICommentary({ generatePost: async () => { current = false; return 'A draft.'; } });
  await h.manager.processEventCollector();
  assert.equal(h.publications(), 0);
});

test('periodic budget and overlapping cycles cannot start another generation', async () => {
  const h = socialHarness();
  let generations = 0;
  let release;
  h.manager.setAICommentary({ generatePost: async () => {
    generations++;
    await new Promise(resolve => { release = resolve; });
    return null;
  } });
  const first = h.manager.processEventCollector();
  await h.manager.processEventCollector();
  release();
  await first;
  assert.equal(generations, 1);
  h.state.postsTodayCount = 10;
  await h.manager.processEventCollector();
  assert.equal(generations, 1);
});

test('ordinary posts receive factual verification and rejected prose is skipped', async () => {
  const ai = Object.create(AICommentary.prototype);
  ai.appState = { recentPosts: [] };
  ai.pendingPublications = new Map();
  ai.aiConfig = { model: 'test' };
  ai.thinkingLevels = { draft: { normal: 'LOW', editorial: 'MEDIUM' }, verifier: 'LOW' };
  let calls = 0;
  ai.requestGeminiStructured = async () => (++calls === 1
    ? JSON.stringify({ post: 'First Bristol’s 42 was eight minutes late at Two Mile Hill. Apparently it stopped for tea.', hook_used: false })
    : JSON.stringify({ verdict: 'FAIL', reasons: ['Invented cause'] }));
  assert.equal(await ai.callSingleWriterGemini({ event }, 0, null), null);
  assert.equal(calls, 2);
  assert.equal(ai.pendingPublications.size, 0);
});

test('draft completion does not spend editorial usage; confirmation does', () => {
  const ai = Object.create(AICommentary.prototype);
  ai.appState = { recentPosts: [] };
  ai.pendingPublications = new Map();
  ai.aiConfig = { model: 'test' };
  const recorded = [];
  ai.editorialContext = { recordPost: hook => recorded.push(hook) };
  const hook = { id: 'approved-fact' };
  ai.completeSingleWriterPost('Draft.', { event }, hook, true, null,
    { getElapsed: () => 1, complete() {} });
  assert.deepEqual(recorded, []);
  assert.deepEqual(ai.appState.recentPosts, []);
  ai.recordPublished('Draft.');
  assert.deepEqual(recorded, [hook]);
  assert.deepEqual(ai.appState.recentPosts, ['Draft.']);
});
