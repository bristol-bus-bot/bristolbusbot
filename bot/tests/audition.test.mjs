import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
process.env.ENABLE_FILE_LOGS = 'false';
process.env.LOG_LEVEL = 'error';
const { replay, recordedResponder, comparisonPage, digest, validateCases } = await import('../scripts/audition-core.mjs');
const { logger } = await import('../dist/utils/logging.js');
logger.silent = true;
const now = '2026-09-25T12:00:30Z';
const event = { operatorRef: 'FBRI', vehicleRef: 'fixture', line: '42', direction: 'outbound', timestamp: '2026-09-25T12:00:00Z',
    originAimedDepartureTimeStr: '2026-09-25T11:00:00Z', lastStopName: 'Two Mile Hill', lastStopCode: 'fixture-stop', delayMinutes: 8, delaySeconds: 480, eventType: 'delay' };
const cases = (...events) => ({ version: 1, cases: events.map((event, i) => ({ id: `case-${i}`, now, event })) });
const good = 'The outbound 42 was 8 minutes late at Two Mile Hill.';
const reply = post => ({ text: JSON.stringify({ post, hook_used: false }) });
const pass = { text: JSON.stringify({ verdict: 'PASS', reasons: [] }) };
test('actual direction check triggers one targeted repair then verifier; published history advances', async () => {
    const seen = [];
    const run = await replay(cases(event, event), async (r) => {
        seen.push(r);
        if (r.kind === 'verifier')
            return pass;
        return reply(r.id === 'case-0:1' ? 'The 42 was 8 minutes late at Two Mile Hill.' : good);
    });
    assert.equal(run.results[0].repaired, true);
    assert.match(run.results[0].drafts[0].issues.join(' '), /outbound/);
    assert.match(seen[1].prompt, /DRAFT TO CORRECT/);
    assert.equal(run.results[1].historyBefore.publishedCount, 1);
    assert.deepEqual(run.results[1].historyBefore.recentPosts, [good]);
    assert.equal(run.results[0].drafts[1].verifier.verdict, 'PASS');
});
test('verifier rejection repairs once, repeated rejection uses factual fallback', async () => {
    const run = await replay(cases(event), async (r) => r.kind === 'verifier' ? { text: JSON.stringify({ verdict: 'FAIL', reasons: ['unsupported claim'] }) } : reply(good));
    assert.equal(run.calls.length, 4);
    assert.equal(run.results[0].status, 'observation_fallback');
    assert.match(run.results[0].post, /was recorded 8 minutes late/);
});
test('stale evidence uses reserve with no model call; unavailable response stops sequential history', async () => {
    let calls = 0;
    const run = await replay(cases({ ...event, timestamp: '2026-09-25T10:00:00Z' }, event, event), async () => { calls++; return null; });
    assert.equal(run.results[0].status, 'reserve_fallback');
    assert.equal(run.results[1].status, 'awaiting_response');
    assert.equal(run.results[1].historyBefore.publishedCount, 1);
    assert.equal(run.results.length, 2);
    assert.equal(calls, 1);
    assert.equal(run.complete, false);
});
test('transient provider retry is counted and recorded, with no extra repair loop', async () => {
    let first = true;
    const run = await replay(cases(event), async (r) => {
        if (first) {
            first = false;
            return { error: 'Provider HTTP 503', httpStatus: 503 };
        }
        return r.kind === 'verifier' ? pass : reply(good);
    });
    assert.equal(run.calls.length, 3);
    assert.equal(run.results[0].status, 'verified');
});
test('recorded replay binds exact prompts and produces same posts and history', async () => {
    const input = cases(event, event);
    const run = await replay(input, async (r) => r.kind === 'verifier' ? pass : reply(good));
    const replayed = await replay(input, recordedResponder(run));
    assert.deepEqual(replayed.results.map(r => [r.post, r.historyBefore]), run.results.map(r => [r.post, r.historyBefore]));
    await assert.rejects(recordedResponder(run)({ ...run.calls[0], promptSha: 'changed' }), /does not match/);
});
test('comparison rejects different fixtures and escapes evidence/model output', () => {
    const run = { fixtureSha: 'a', results: [{ id: '<script>', status: 'verified', post: '<img onerror=alert(1)>', evidence: { provenance: '<script>evil</script>' } }] };
    const page = comparisonPage(run, run);
    assert.ok(page.includes('&lt;img'));
    assert.ok(!page.includes('<script>'));
    assert.ok(page.includes('Content-Security-Policy'));
    assert.throws(() => comparisonPage(run, { fixtureSha: 'b' }), /different fixture/);
    assert.throws(() => validateCases(cases(event, event).cases), /Expected/);
    const duplicate = cases(event, event);
    duplicate.cases[1].id = duplicate.cases[0].id;
    assert.throws(() => validateCases(duplicate), /unique/);
});
test('CLI enforces a durable call budget, counts before requests and never stores the key', t => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bbb-audition-budget-'));
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const input = path.join(dir, 'cases.json'), ledger = path.join(dir, 'ledger.json'), mock = path.join(dir, 'mock.mjs');
    fs.writeFileSync(input, JSON.stringify(cases(event)));
    fs.writeFileSync(mock, `globalThis.fetch=async(url,options)=>{const body=JSON.parse(options.body);const verifier=body.generationConfig.responseJsonSchema?.properties?.verdict || body.generationConfig.responseSchema?.properties?.verdict;return new Response(JSON.stringify({candidates:[{content:{parts:[{text:JSON.stringify(verifier?{verdict:'PASS',reasons:[]}:{post:${JSON.stringify(good)},hook_used:false})}]}}],usageMetadata:{totalTokenCount:10}}),{status:200});};`);
    const command = ['--import', pathToFileURL(mock).href, 'scripts/audition.mjs', input, path.join(dir, 'report'),
        '--live', '--model', 'mock-only', '--max-calls', '1', '--ledger', ledger];
    const invoke = () => spawnSync(process.execPath, command, { cwd: new URL('..', import.meta.url), encoding: 'utf8', env: { ...process.env, AI_API_KEY: 'secret-mock-key' } });
    let result = invoke();
    assert.equal(result.status, 1, result.stdout + result.stderr);
    assert.match(result.stderr, /budget exhausted/);
    let saved = JSON.parse(fs.readFileSync(ledger, 'utf8'));
    assert.equal(saved.calls.length, 1);
    assert.equal(saved.calls[0].complete, true);
    assert.ok(!fs.readFileSync(ledger, 'utf8').includes('secret-mock-key'));
    result = invoke();
    assert.equal(result.status, 1);
    assert.equal(JSON.parse(fs.readFileSync(ledger)).calls.length, 1);
    saved.calls[0].complete = false;
    fs.writeFileSync(ledger, JSON.stringify(saved));
    result = invoke();
    assert.equal(result.status, 1);
    assert.match(result.stderr, /Uncertain prior request/);
    assert.equal(JSON.parse(fs.readFileSync(ledger)).calls.length, 1);
});
test('transport storage failure propagates rather than being disguised as a publishable fallback', async () => {
    const before = Date.now;
    await assert.rejects(replay(cases(event), async () => { throw new Error('ledger write failed'); }), /ledger write failed/);
    assert.equal(Date.now, before);
});
