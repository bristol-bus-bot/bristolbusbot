import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { TrainingLog } from '../dist/services/training-log.js';
import { AICommentary } from '../dist/services/ai-commentary.js';

const temp = t => { const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bbb-training-')); t.after(() => fs.rmSync(dir, { recursive: true, force: true })); return dir; };
const lines = file => fs.readFileSync(file, 'utf8').trim().split('\n').map(line => JSON.parse(line));

test('the training log keeps at most two capped files', t => {
  const file = path.join(temp(t), 'training.jsonl');
  const log = new TrainingLog(file, 300);
  for (let i = 0; i < 10; i++) log.record({ at: String(i), brief: 'x'.repeat(100) });
  assert.ok(fs.statSync(file).size < 300 + 200);
  assert.ok(fs.existsSync(file + '.1'));
  assert.deepEqual(fs.readdirSync(path.dirname(file)).sort(), ['training.jsonl', 'training.jsonl.1']);
  assert.equal(lines(file).at(-1).at, '9');
  assert.doesNotThrow(() => new TrainingLog(path.join(file, 'not-a-dir', 'x')).record({}));
});

test('each draft is saved with its brief, model and check results', async t => {
  const dir = temp(t);
  const event = { line: '42', direction: 'outbound', operatorRef: 'FBRI', vehicleRef: 'test',
    timestamp: new Date().toISOString(), eventType: 'delay', delayMinutes: 8, lastStopName: 'Two Mile Hill' };
  const ai = Object.create(AICommentary.prototype);
  ai.appState = { recentPosts: [] };
  ai.subjectHistory = { publishedCount: 0, history: [] };
  ai.pendingPublications = new Map();
  ai.aiConfig = { model: 'writer-model', verifierModel: 'checker-model', timeout: 75000,
    editorialUsagePath: path.join(dir, 'editorial-usage.json') };
  ai.thinkingLevels = { draft: { normal: 'LOW', editorial: 'MEDIUM' }, verifier: 'LOW' };
  const good = 'The outbound 42 was 8 minutes late at Two Mile Hill.';
  const replies = [JSON.stringify({ post: good, hook_used: false }),
    JSON.stringify({ verdict: 'FAIL', reasons: ['invents passengers'] }),
    JSON.stringify({ post: good + ' A modest drift.', hook_used: false }),
    JSON.stringify({ verdict: 'PASS', reasons: [] })];
  ai.requestGeminiStructured = async () => replies.shift();
  await ai.callSingleWriterGemini({ event }, 0, null);
  const saved = lines(path.join(dir, 'editorial-usage.json.training.jsonl'));
  assert.deepEqual(saved.map(s => s.outcome), ['verifier_fail', 'passed']);
  assert.deepEqual(saved[0].verifier, { verdict: 'FAIL', reasons: ['invents passengers'] });
  assert.equal(saved[1].draft, good + ' A modest drift.');
  assert.equal(saved[1].attempt, 1);
  assert.equal(saved[0].model, 'writer-model');
  assert.equal(saved[0].verifierModel, 'checker-model');
  assert.match(saved[1].brief, /Two Mile Hill/);
  assert.match(saved[1].brief, /invents passengers/, 'the repair brief, exactly as sent');
});
