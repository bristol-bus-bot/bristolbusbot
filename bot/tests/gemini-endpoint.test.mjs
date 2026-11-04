import assert from 'node:assert/strict';
import test from 'node:test';
import { geminiEndpoint } from '../dist/services/gemini-endpoint.js';
import { AICommentary, buildGeminiStructuredGenerationConfig } from '../dist/services/ai-commentary.js';

const key = 'k'.repeat(39);

test('the Developer API stays the default endpoint, with the key in a header', () => {
  const { url, headers } = geminiEndpoint({ apiKey: key }, 'gemini-3.6-flash');
  assert.equal(url, 'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent');
  assert.equal(headers['x-goog-api-key'], key);
  assert.ok(!url.includes(key));
});

test('Vertex uses the project path, global by default and regional hosts otherwise', () => {
  const vertex = { apiKey: key, provider: 'vertex', vertexProject: 'bus-bot-123' };
  assert.equal(geminiEndpoint(vertex, 'gemini-3.5-flash-lite').url,
    'https://aiplatform.googleapis.com/v1/projects/bus-bot-123/locations/global/publishers/google/models/gemini-3.5-flash-lite:generateContent');
  assert.equal(geminiEndpoint({ ...vertex, vertexLocation: 'europe-west2' }, 'gemini-3.6-flash').url,
    'https://europe-west2-aiplatform.googleapis.com/v1/projects/bus-bot-123/locations/europe-west2/publishers/google/models/gemini-3.6-flash:generateContent');
  assert.throws(() => geminiEndpoint({ ...vertex, vertexProject: '' }, 'gemini-3.6-flash'));
  assert.throws(() => geminiEndpoint({ ...vertex, vertexProject: 'a/../b' }, 'gemini-3.6-flash'));
  assert.throws(() => geminiEndpoint(vertex, 'models/x:y'));
});

test('Vertex takes responseFormat as a list; the Developer API as one object', () => {
  const schema = { type: 'object' };
  const one = buildGeminiStructuredGenerationConfig(schema, 1, 'LOW');
  const list = buildGeminiStructuredGenerationConfig(schema, 1, 'LOW', 'vertex');
  assert.deepEqual(one.responseFormat, { text: { mimeType: 'APPLICATION_JSON', schema } });
  assert.deepEqual(list.responseFormat, [{ text: { mimeType: 'APPLICATION_JSON', schema } }]);
  assert.deepEqual(list.thinkingConfig, { thinkingLevel: 'LOW' });
});

test('the checker can use its own model while the writer keeps the main one', async () => {
  const event = { line: '42', direction: 'outbound', operatorRef: 'FBRI', vehicleRef: 'test',
    timestamp: new Date().toISOString(), eventType: 'delay', delayMinutes: 8, lastStopName: 'Two Mile Hill' };
  const ai = Object.create(AICommentary.prototype);
  ai.appState = { recentPosts: [] };
  ai.subjectHistory = { publishedCount: 0, history: [] };
  ai.pendingPublications = new Map();
  ai.aiConfig = { model: 'writer-model', verifierModel: 'checker-model', timeout: 75000 };
  ai.thinkingLevels = { draft: { normal: 'LOW', editorial: 'MEDIUM' }, verifier: 'LOW' };
  const models = [];
  ai.requestGeminiStructured = async (prompt, schema, temperature, thinking, timeoutMs, model) => {
    models.push(model);
    return schema.properties?.verdict
      ? JSON.stringify({ verdict: 'PASS', reasons: [] })
      : JSON.stringify({ post: 'The outbound 42 was 8 minutes late at Two Mile Hill.', hook_used: false });
  };
  await ai.callSingleWriterGemini({ event }, 0, null);
  assert.deepEqual(models, [undefined, 'checker-model']);
});
