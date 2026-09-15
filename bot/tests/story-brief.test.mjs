import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import Database from 'better-sqlite3';
import { DateTime } from 'luxon';
import { buildStoryPrompt, observationIssue, latestStoryEvents, factualStoryIssues } from '../dist/services/story-brief.js';
import { EventReader } from '../dist/ingest/event-reader.js';
import { SocialMediaManager } from '../dist/services/social-media.js';
import { AICommentary } from '../dist/services/ai-commentary.js';
import { observationPost, reservePost } from '../dist/services/posting-fallback.js';
import { availableSubjects } from '../dist/services/story-subject.js';

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

test('brief distinguishes assigned depot from location and limits vehicle repetition', () => {
  const enriched = { ...event, busDetails: { vehicle_type: { name: 'Yutong U11DD' },
    livery: { name: 'Olympia' }, garage: { name: 'Lawrence Hill' } } };
  const depot = availableSubjects(enriched).find(s => s.kind === 'depot');
  const prompt = buildStoryPrompt(enriched, new Date(now).toISOString(), null, [], [], null, depot);
  assert.doesNotMatch(prompt, /Olympia|Yutong|suggestedDetail/);
  assert.match(prompt, /"assignedDepot":"Lawrence Hill"/);
  assert.match(prompt, /home garage is an assignment/);
  assert.doesNotMatch(prompt, /Network:|Position:|Vehicle notes:/);
  assert.match(prompt, /do not establish arrival, departure/);
  assert.match(prompt, /Include route, known direction/);
  assert.match(factualStoryIssues('The 42 left ten minutes early.').join(' '), /departure/);
  assert.match(factualStoryIssues('The 42 is at stop 1.').join(' '), /position/);
  assert.match(factualStoryIssues('The network average delay is ten minutes.').join(' '), /network/);
});

test('current writing path fetches weather for the bus and preserves it in the writer and verifier brief', async () => {
  const ai = Object.create(AICommentary.prototype);
  ai.aiConfig = { pipeline: 'single' };
  ai.appState = { getNetworkStatus: () => ({}) };
  const weather = 'OpenWeather area observation near Bath: 12°C, light rain';
  let requested;
  ai.weatherService = { getCurrentWeather: async location => { requested = location; return weather; } };
  const bus = { ...event, location: { latitude: 51.38, longitude: -2.36 } };
  const context = await ai.buildAIContext(bus);
  assert.deepEqual(requested, bus.location);
  const prompt = ai.buildSingleWriterPrompt(context, DateTime.utc(), null, [], [], availableSubjects(bus, weather).find(s => s.kind === 'weather'));
  assert.ok(prompt.includes(weather));
  assert.ok(ai.buildVerifierPrompt(prompt, 'A post.').includes('OpenWeather area observation near Bath'));
  ai.weatherService.getCurrentWeather = async () => null;
  const missing = await ai.buildAIContext(bus);
  assert.equal(missing.weatherContext, undefined);
  assert.doesNotMatch(ai.buildSingleWriterPrompt(missing, DateTime.utc(), null, [], []), /areaWeather/);
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
  assert.equal(reader.isObservationPublishable(event, now), true);
  for (const [column, value] of [['journey_ref', 'next-run'], ['operator_ref', 'SCGL'],
    ['stop_code', 'next-stop'], ['delay_seconds', 600], ['low_confidence', 1], ['at_depot', 'LH'],
    ['recorded_at', '2026-09-13T11:55:00Z']]) {
    const original = db.prepare(`SELECT ${column} AS value FROM vehicles`).get().value;
    db.prepare(`UPDATE vehicles SET ${column} = ?`).run(value);
    assert.equal(reader.isObservationCurrent(event, now), false, column);
    assert.equal(reader.isObservationPublishable(event, now),
      ['stop_code', 'delay_seconds'].includes(column), `publication: ${column}`);
    db.prepare(`UPDATE vehicles SET ${column} = ?`).run(original);
  }
  assert.equal(reader.isObservationPublishable({ ...event,
    timestamp: '2026-09-13T11:54:00Z' }, now), false, 'old report with a fresh bus is still stale');
  db.prepare('UPDATE vehicles SET event_type=?, delay_seconds=?').run('punctual', 0);
  assert.equal(reader.isObservationCurrent(event, now), false);
  assert.equal(reader.isObservationPublishable(event, now), true, 'later punctuality does not erase the earlier delay');
  db.exec("ALTER TABLE vehicles ADD COLUMN trip_id TEXT; ALTER TABLE vehicles ADD COLUMN stop_sequence INTEGER; UPDATE vehicles SET trip_id='matched', stop_sequence=30");
  const withMatch = { ...event, eventType: 'punctual', delayMinutes: 0, collectorTripId: 'matched', collectorStopSequence: 30 };
  assert.equal(reader.isObservationCurrent(withMatch, now), true);
  db.exec('UPDATE vehicles SET stop_sequence=40');
  assert.equal(reader.isObservationCurrent(withMatch, now), false);
  assert.equal(reader.isObservationPublishable(withMatch, now), true);
  db.exec("UPDATE vehicles SET trip_id='rematched'");
  assert.equal(reader.isObservationPublishable(withMatch, now), false, 'changed timetable match invalidates old journey context');
  db.exec(`CREATE TABLE events (id INTEGER PRIMARY KEY, stop_code TEXT, stop_name TEXT);
    INSERT INTO events VALUES (1, 'bst-test', 'Two Mile Hill');
    UPDATE vehicles SET event_type='punctual', delay_seconds=0;`);
  reader.delayAnalyzer = { calculateEventSignificance: () => ({ type: 'ignore', score: 0 }),
    extractBusDetails: () => undefined };
  const [normal] = reader.getCurrentStories(now);
  assert.equal(normal.eventType, 'punctual');
  assert.equal(normal.source, 'live_snapshot');
  assert.equal(normal.collectorEventId, undefined, 'snapshot must not claim an old exception event id');
  assert.equal(normal.timestamp, '2026-09-13T11:59:50Z');
});

function socialHarness(events = [freshEvent()]) {
  const state = { postsTodayCount: 0, resetDailyCounters() {}, getAndClearBusEvents: () => events };
  const manager = new SocialMediaManager({ dailyLimit: 10, postLimit: 300, testMode: false }, state);
  let publications = 0;
  const delivered = [];
  manager.postUpdate = async (text, event) => { publications++; delivered.push({ text, event }); return { bluesky: true }; };
  return { manager, state, delivered, publications: () => publications };
}

test('rejected AI drafts publish a factual observation with its timestamp', async () => {
  const h = socialHarness();
  h.manager.setAICommentary({ generatePost: async () => null });
  await h.manager.processEventCollector();
  assert.equal(h.publications(), 1);
  assert.match(h.delivered[0].text, /^At \d\d:\d\d, the 42 was recorded 8 minutes late at Two Mile Hill\.$/);
});

test('a changed observation during writing uses reserve prose without a fake bus event', async () => {
  const h = socialHarness();
  let current = true;
  h.manager.setObservationValidator(() => current);
  h.manager.setAICommentary({ generatePost: async () => { current = false; return 'A draft.'; } });
  await h.manager.processEventCollector();
  assert.equal(h.publications(), 1);
  assert.equal(h.delivered[0].event, null);
  assert.doesNotMatch(h.delivered[0].text, /Two Mile Hill|A draft/);
});

test('bus movement during writing keeps the verified recent observation and final publication accepts it', async () => {
  const original = freshEvent();
  const h = socialHarness([original]);
  let samePosition = true;
  h.manager.setObservationValidator(() => samePosition);
  h.manager.setPublicationValidator(() => true);
  const draft = 'The 42 was eight minutes late at Two Mile Hill. Plenty of time to reconsider the timetable.';
  h.manager.setAICommentary({ generatePost: async () => { samePosition = false; return draft; } });
  await h.manager.processEventCollector();
  assert.deepEqual(h.delivered, [{ text: draft, event: original }]);
  // Exercise the real final publication gate without any external posting.
  h.manager.socialConfig.testMode = true;
  h.state.incrementPostCount = () => h.state.postsTodayCount++;
  assert.deepEqual(await SocialMediaManager.prototype.postUpdate.call(h.manager, draft, original), { bluesky: true });
  h.manager.setPublicationValidator(() => false);
  assert.deepEqual(await SocialMediaManager.prototype.postUpdate.call(h.manager, draft, original), { bluesky: false, stale: true });
});

test('expired observations cannot use the movement allowance', async () => {
  const original = freshEvent();
  const h = socialHarness([original]);
  h.manager.setObservationValidator(() => true);
  h.manager.setPublicationValidator(() => true);
  h.manager.setAICommentary({ generatePost: async () => {
    original.timestamp = new Date(Date.now() - 6 * 60_000).toISOString();
    return 'An expired draft.';
  } });
  await h.manager.processEventCollector();
  assert.equal(h.delivered.length, 1);
  assert.equal(h.delivered[0].event, null);
  assert.notEqual(h.delivered[0].text, 'An expired draft.');
});

test('last-instant failed publication check sends one reserve and never the rejected observation', async () => {
  const h = socialHarness();
  const attempts = [];
  h.manager.postUpdate = async (text, event) => {
    attempts.push({ text, event });
    return attempts.length === 1 ? { bluesky: false, stale: true } : { bluesky: true };
  };
  await h.manager.processEventCollector();
  assert.equal(attempts.length, 2);
  assert.equal(attempts[1].event, null);
});

test('empty collector publishes and fresh snapshots can supply an on-time story', async () => {
  const empty = socialHarness([]);
  await empty.manager.processEventCollector();
  assert.equal(empty.publications(), 1);
  assert.equal(empty.delivered[0].event, null);
  const normal = socialHarness([]);
  normal.manager.setStoryProvider(() => [{ ...freshEvent(), eventType: 'punctual', delayMinutes: 0 }]);
  await normal.manager.processEventCollector();
  assert.match(normal.delivered[0].text, /recorded on time/);
});

test('reserve posts fit the platform and differ between consecutive cycles', () => {
  for (let i = 0; i < 12; i++) assert.ok(reservePost(i * 1200000).length <= 300);
  assert.notEqual(reservePost(now), reservePost(now + 1200000));
  assert.match(observationPost(event), /At 12:59, the 42 was recorded 8 minutes late/);
});

test('uncertain delivery never triggers a second different post', async () => {
  const h = socialHarness();
  let attempts = 0;
  h.manager.postUpdate = async () => { attempts++; return { bluesky: false }; };
  await h.manager.processEventCollector();
  assert.equal(attempts, 1);
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
    ? JSON.stringify({ post: 'First Bristol’s outbound 42 was eight minutes late at Two Mile Hill. Apparently it stopped for tea.', hook_used: false })
    : JSON.stringify({ verdict: 'FAIL', reasons: ['Invented cause'] }));
  assert.equal(await ai.callSingleWriterGemini({ event }, 0, null), null);
  assert.equal(calls, 2);
  assert.equal(ai.pendingPublications.size, 0);
});

test('verification uses the exact final writer brief, including selected vehicle detail and stop labels', async () => {
  for (const repair of [false, true]) {
    const ai = Object.create(AICommentary.prototype);
    ai.appState = { recentPosts: [] };
    ai.subjectHistory = { publishedCount: 1, history: [] };
    ai.pendingPublications = new Map();
    ai.aiConfig = { model: 'test' };
    ai.thinkingLevels = { draft: { normal: 'LOW', editorial: 'MEDIUM' }, verifier: 'LOW' };
    const bus = { ...event, lastStopName: 'The Haymarket - B10', busDetails: {
      livery: { name: 'WESTbus' }, vehicle_type: { name: 'Yutong U11DD' } } };
    const post = 'The outbound 42 was eight minutes late at The Haymarket - B10. WESTbus livery with time to spare.';
    const prompts = [];
    ai.requestGeminiStructured = async prompt => {
      prompts.push(prompt);
      if (prompt.startsWith('Check facts')) return JSON.stringify({ verdict: 'PASS', reasons: [] });
      return JSON.stringify({ post: repair && prompts.length === 1 ? 'Missing the evidence.' : post, hook_used: false });
    };
    assert.ok(await ai.callSingleWriterGemini({ event: bus }, 0, null));
    const writerBrief = prompts.at(-2);
    const verifier = prompts.at(-1);
    const verifierData = JSON.parse(verifier.split('\n').find(line => line.startsWith('{"brief":')));
    assert.equal(verifierData.brief, writerBrief);
    assert.match(writerBrief, /"livery":"WESTbus"/);
    assert.doesNotMatch(writerBrief, /Yutong|assignedDepot|areaWeather/);
    assert.match(verifier, /stand label such as B10 or C3, is allowed/);
    assert.match(writerBrief, /The Haymarket - B10/);
    assert.equal(prompts.length, repair ? 3 : 2, 'no extra model calls for the fix');
  }
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
