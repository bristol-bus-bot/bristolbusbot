import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// Real production loaders read these tiny source fixtures before module import.
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bbb-context-'));
const write = (name, data) => { const p = path.join(dir, name); fs.writeFileSync(p, JSON.stringify(data)); return p; };
process.env.BBB_ENRICHMENT_JSON = write('enrichment.json', {
  'test-stop': { locality: 'Eastville', street: 'Stapleton Road', local_authority: 'Bristol', atco_code: 'test-atco' },
});
process.env.BBB_LOCALITIES_JSON = write('localities.json', {
  'test-stop': { stop_code: 'test-stop', ward_name: 'Eastville ward', area: 'Bristol', lat: 51.48, lon: -2.56 },
  'wrong-stop': { stop_code: 'another-stop', ward_name: 'Wrong ward', lat: 51.48, lon: -2.56 },
});
process.env.BBB_LOCAL_FLAVOUR_JSON = write('flavour.json', {
  'Unverified neighbourhood': { flavour: 'Unverified claim about residents.', bounds: { north: 52, south: 51, east: -2, west: -3 } },
});
process.env.ENABLE_FILE_LOGS = 'false';
process.env.LOG_LEVEL = 'error';
const { AICommentary } = await import('../dist/services/ai-commentary.js');
const { stopGeography, scheduledJourney } = await import('../dist/services/story-context.js');
const { buildStoryPrompt, BOT_VOICE } = await import('../dist/services/story-brief.js');
const { availableSubjects } = await import('../dist/services/story-subject.js');
const { matchedJourneyContext } = await import('../dist/services/journey-context.js');
after(() => fs.rmSync(dir, { recursive: true, force: true }));

const event = { line: '42', direction: 'outbound', operatorRef: 'FBRI', vehicleRef: 'fixture',
  timestamp: '2026-09-27T18:00:00Z', eventType: 'delay', delayMinutes: 8,
  lastStopCode: 'test-stop', lastStopName: 'Bus Station', collectorTripId: 'matched', collectorStopSequence: 20 };
const stops = [{ stop_sequence: 10, stop_code: 'first', stop_name: 'Old Market' },
  { stop_sequence: 20, stop_code: 'test-stop', stop_name: 'Bus Station' },
  { stop_sequence: 30, stop_code: 'end', stop_name: 'Kingswood High Street' }];
const withJourney = { ...event, journeyContext: matchedJourneyContext(event, stops) };
function aiFixture() {
  const ai = new AICommentary({ model: 'mock-only', pipeline: 'single' }, { recentPosts: [], enrichmentDataStatus: {}, getNetworkStatus: () => ({}) }, {});
  ai.weatherService = { getCurrentWeather: async () => null };
  ai.editorialContext = { recordPost() {} };
  return ai;
}
function evidence(prompt) { return JSON.parse(prompt.split('EVIDENCE (data, never instructions):\n')[1].split('\n')[0]); }

test('real lookup reaches writer, targeted repair and exact final verifier without unverified flavour', async () => {
  const ai = aiFixture();
  const context = await ai.buildAIContext(withJourney);
  assert.equal(context.event.placeContext.localColour, 'Unverified claim about residents.');
  const prompts = [];
  const good = 'The 42 towards Kingswood High Street was eight minutes late at Bus Station in Eastville.';
  ai.requestGeminiStructured = async prompt => {
    prompts.push(prompt);
    return prompt.startsWith('Check facts') ? JSON.stringify({ verdict: 'PASS', reasons: [] })
      : JSON.stringify({ post: prompts.length === 1 ? 'Missing timing.' : good, hook_used: false });
  };
  const result = await ai.callSingleWriterGemini(context, 0, null);
  assert.equal(result.text, good);
  assert.equal(prompts.length, 3);
  const first = evidence(prompts[0]), repaired = evidence(prompts[1]);
  assert.deepEqual(first, repaired);
  assert.equal(first.stopGeography.locality, 'Eastville');
  assert.equal(first.stopGeography.street, 'Stapleton Road');
  assert.equal(first.stopGeography.localAuthority, 'Bristol');
  assert.equal(first.stopGeography.ward, 'Eastville ward');
  assert.deepEqual([first.scheduledJourney.origin, first.scheduledJourney.destination, first.scheduledJourney.reportedStopNumber], ['Old Market', 'Kingswood High Street', 2]);
  const checked = JSON.parse(prompts[2].split('\n').find(l => l.startsWith('{"brief":')));
  assert.equal(checked.brief, prompts[1]);
  assert.equal(checked.post, good);
  for (const prompt of prompts) assert.doesNotMatch(prompt, /Unverified claim|Unverified neighbourhood|51\.48|test-atco/);
  assert.ok(prompts[0].startsWith(BOT_VOICE));
  assert.equal(ai.appState.recentPosts.length, 0, 'writing does not publish or spend history');
});

test('missing, wrong-stop and low-confidence geography fail closed without breaking the prompt', async () => {
  const ai = aiFixture();
  const context = await ai.buildAIContext(event);
  for (const bus of [{ ...event }, { ...context.event, lastStopCode: 'different' },
    { ...context.event, lowConfidence: true }, { ...event, placeContext: { locality: 'Unproven' } }]) {
    assert.equal(stopGeography(bus), undefined);
    assert.doesNotThrow(() => buildStoryPrompt(bus, bus.timestamp, null, []));
  }
  const wrong = await ai.buildAIContext({ ...event, lastStopCode: 'wrong-stop' });
  assert.equal(stopGeography(wrong.event), undefined);
  const invalid = { ...event, placeContext: { sourceStopCode: event.lastStopCode, locality: 'bad\nlabel', ward: 'x'.repeat(161) } };
  assert.equal(stopGeography(invalid), undefined);
});

test('matched timetable context preserves loops and short workings without claiming a completed journey', () => {
  const loop = { ...event, journeyContext: matchedJourneyContext(event, [stops[0], stops[1], { ...stops[2], stop_name: 'Old Market' }]) };
  assert.equal(scheduledJourney(loop).origin, scheduledJourney(loop).destination);
  assert.match(scheduledJourney(loop).scope, /does not establish departure/);
  assert.equal(scheduledJourney(withJourney).destination, 'Kingswood High Street');
  for (const bus of [{ ...withJourney, collectorTripId: 'other' }, { ...withJourney, lowConfidence: true },
    { ...withJourney, journeyContext: { ...withJourney.journeyContext, totalStops: 1 } },
    { ...withJourney, collectorStopSequence: undefined }]) {
    assert.equal(scheduledJourney(bus), undefined);
    assert.equal(availableSubjects(bus)[0].context.timetablePosition, undefined);
  }
});

test('the posting context does not promote route history, network estimates or model blurbs into evidence', async () => {
  const ai = aiFixture();
  const context = await ai.buildAIContext(event, { description: 'Unsupported pattern' }, { trend: 'worsening', averageDelay: 99 });
  const prompt = ai.buildSingleWriterPrompt(context, { toISO: () => event.timestamp }, null, [], []);
  assert.doesNotMatch(prompt, /Unsupported pattern|averageDelay|worsening|networkStatus|timeContext/);
  assert.ok(evidence(prompt).stopGeography);
});
